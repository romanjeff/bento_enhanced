# SPDX-License-Identifier: GPL-3.0-or-later
"""Build M1 (poly aftertouch out of External tracks + diagnostics) on an exact Lab CH10 / 1.6HA image.

    python build.py --base <CH10 bento1.bin> --out <new dir>

Refuses any base except CH10 (SHA-256 below), checks original bytes at every
patch site, writes bento1.bin + manifest.json, and proves that undoing every
write reproduces the base exactly. Run with Lab's venv (ziglang + keystone).
"""
import argparse, hashlib, json, struct, subprocess, sys
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB

HERE = Path(__file__).resolve().parent
BASE = 0x08040000
CH10_SHA = "0e85070685cb451ade4e3cf5adfc62fb1ab95483f7fcdcbf506ae67bc4ca2c76"

M7_TRAMP = 0x080DA000   # inside CH10's untouched zero gap 0x080D8C20..0x080DC000
M7_CODE = 0x080DA100
M7_LIMIT = 0x080DB800
# In-place, inside CH10's zero run 0x081BD8A8..0x081C0000. Not appended: the
# bootloader rejected an image grown to 0x1A0800 bytes ("unable to program /
# verify main firmware"), so the image must not exceed CH10's size.
# Lab's k16_overlay uses up to 0x081BD8C0; CH10 code references 0x081BF6C0.
M4_CODE = 0x081BE000
M4_LIMIT = 0x081BF600   # CH10 reads a (zero) word at 0x081BF6C0: keep a 0xC0 margin
MARKER = b"BENTO_MOD:m1-polyat:v2.2" + bytes(1)

# (name, site, original bytes, displaced instruction(s), resume address, call setup)
M7_HOOKS = [
    ("h_input",   0x08059980, "2de9f043", "push.w {r4,r5,r6,r7,r8,r9,lr}", 0x08059984, ""),
    ("h_send",    0x08065608, "8c4630b5", "mov ip, r1; push {r4,r5,lr}",   0x0806560C, "mov r2, lr; "),
    ("h_ring",    0x08082640, "00f5005c", "add.w ip, r0, #0x2000",         0x08082644, ""),
    ("h_consume", 0x080655A0, "d0f80036", "ldr.w r3, [r0, #0x600]",        0x080655A4, "mov r2, lr; "),
]
M4_BL_SITE, M4_BL_ORIG, M4_BL_ORIG_TARGET = 0x08114B10, "edf7b2f8", 0x08101C78

# Poly AT, M7: External track handler FUN_08062020, branch taken for event
# types that are neither 2 (note on) nor 3 (note off). r6 = track, r8 = the
# context stock passes to its note-on producer, event at sp+0x20.
POLY_SITE, POLY_ORIG, POLY_RESUME = 0x08062466, "7ff40bae", 0x08062080
# Poly AT, M4: MIDI task jump table (types 0x5B..0x63); slot 0x5F is unused
# (falls through to the loop at 0x081050DE). Our handler copies stock's Note On
# case (0x0810509A..0x081050DE) with status 0x90 -> 0xA0.
JT, JT_5F, JT_DEFAULT, M4_LOOP = 0x08105294, 0x081052A4, 0x081050DF, 0x081050DE
M4_POLY = 0x081BF580   # end of our M4 slice (handler is 72 B); diag code grows below it

# Poly cap: the five `bl msg_send` sites that post External MIDI notes, and the
# register holding the track object at each (verified unmodified on every path).
MSG_SEND = 0x08065600
NOTE_SITES = [  # (site, track register, role)
    (0x0805F92C, "r4", "sequencer Note On (note_route)"),
    (0x0805FF50, "r4", "pad Note On (FUN_0805FF00)"),
    (0x0805FE44, "r4", "sequencer Note Off (FUN_0805FE00)"),
    (0x08060088, "r4", "pad Note Off (FUN_08060040)"),
    (0x08061966, "r6", "Note Off, stop/all-off (FUN_080618E0)"),
]

ks = Ks(KS_ARCH_ARM, KS_MODE_THUMB)
def asm(src, addr):
    return bytes(ks.asm(src, addr)[0])

COND = {"eq": 0, "ne": 1}
def bcond_w(cond, src, dst):
    """Thumb-2 conditional branch, encoding T3 (+-1 MB), encoded by hand.
    Keystone mis-encodes conditional branches to absolute targets (asked for
    bne.w 0x08062080 at 0x08062466 it emitted a branch to 0x080C44EA)."""
    off = dst - (src + 4)
    if off & 1 or not -(1 << 20) <= off < (1 << 20):
        raise SystemExit(f"conditional branch {src:#x}->{dst:#x} out of range")
    imm = off & 0x1FFFFF
    S, J2, J1 = (imm >> 20) & 1, (imm >> 19) & 1, (imm >> 18) & 1
    imm6, imm11 = (imm >> 12) & 0x3F, (imm >> 1) & 0x7FF
    hw1 = 0xF000 | (S << 10) | (COND[cond] << 6) | imm6
    hw2 = 0x8000 | (J1 << 13) | (J2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)

def compile_elf(srcs, cpu, ld, out):
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "thumb-freestanding-eabi", f"-mcpu={cpu}",
           "-mfloat-abi=soft", "-Os", "-g", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-nostdlib",
           "-ffunction-sections", "-fdata-sections", "-Wall", "-Werror",
           "-Wl,--gc-sections", f"-Wl,--script={ld}", "-Wl,--entry=0", *map(str, srcs), "-o", str(out)]
    subprocess.run(cmd, check=True)
    return out.read_bytes()

def elf_load(e, lo, hi):
    """Return (load address, bytes) of the single loadable segment inside [lo, hi), plus symbols.
    The linker also emits a PT_LOAD for its own ELF headers below ORIGIN; those bytes are never
    written to the image (they would land on Lab's code), and any other stray segment is an error."""
    phoff, shoff = struct.unpack_from("<II", e, 28)
    phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", e, 42)
    segs = []
    for i in range(phnum):
        t, off, va, _, fsz, msz, flags, _ = struct.unpack_from("<8I", e, phoff + i * phentsize)
        if t == 1 and msz:
            hdr_only = off == 0 and va + fsz <= lo  # ELF/program headers, not part of our code
            if hdr_only:
                continue
            if not (lo <= va and va + msz <= hi):
                raise ValueError(f"segment {va:#x}+{msz:#x} outside our slice")
            if msz != fsz:
                raise ValueError("unexpected .bss/zero-fill in segment: this code must have no RAM data")
            segs.append((va, e[off:off + fsz], flags))
    if len(segs) != 1:
        raise ValueError(f"expected one loadable segment, got {[(hex(v), len(b)) for v, b, _ in segs]}")
    syms = {}
    secs = [struct.unpack_from("<10I", e, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s[6]]
        names = e[strtab[4]:strtab[4] + strtab[5]]
        for j in range(0, s[5], s[9]):
            nm, val, _, info, _, _ = struct.unpack_from("<IIIBBH", e, s[4] + j)
            if info & 15 == 2:  # STT_FUNC
                syms[names[nm:].split(b"\0")[0].decode()] = val & ~1
    va, data, _ = segs[0]
    return va, data, syms

def build(base_img, out):
    if hashlib.sha256(base_img).hexdigest() != CH10_SHA:
        raise SystemExit("base is not Lab CH10 1.6HA; refusing")
    out.mkdir(parents=True, exist_ok=False)
    img = bytearray(base_img)
    writes = []

    def patch(addr, new, expect, why):
        o = addr - BASE
        old = bytes(img[o:o + len(new)])
        if expect is not None and old != expect:
            raise SystemExit(f"{why}: original bytes at {addr:#x} are {old.hex()}, expected {expect.hex()}")
        img[o:o + len(new)] = new
        writes.append(dict(address=addr, size=len(new), original_hex=old.hex(), replacement_hex=new.hex(), purpose=why))

    # --- M7: compile, place, trampolines, entry patches
    m7_elf = compile_elf([HERE / "m7_diag.c", HERE / "m7_polyat.c"], "cortex_m7", HERE / "m7.ld", out / "m7.elf")
    va, code7, s7 = elf_load(m7_elf, M7_CODE, M7_LIMIT)
    if va != M7_CODE or M7_CODE + len(code7) > M7_LIMIT:
        raise SystemExit(f"M7 code placement {va:#x}+{len(code7)} outside our slice")
    if any(base_img[M7_TRAMP - BASE:M7_LIMIT - BASE]):
        raise SystemExit("our M7 slice is not all zero in the base image")
    tramps = b""
    for name, site, orig, displaced, resume, setup in M7_HOOKS:
        t = M7_TRAMP + len(tramps)
        src = ("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
               f"{setup}bl {s7[name]:#x}; "
               "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
               f"{displaced}; b.w {resume:#x}")
        code = asm(src, t)
        tramps += code + bytes(-len(code) % 4)
        patch(site, asm(f"b.w {t:#x}", site), bytes.fromhex(orig), f"M1 diag: {name} entry trampoline")
        if len(asm(f"b.w {t:#x}", site)) != 4:
            raise SystemExit(f"{name}: branch is not 4 bytes")
    t = M7_TRAMP + len(tramps)
    head = asm("cmp r3, #0x0e", t)
    head += bcond_w("ne", t + len(head), POLY_RESUME)          # not ours -> stock path
    poly_src = ("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
                "mov r0, r6; add r1, sp, #0x40; mov r2, r8; "   # event was at sp+0x20; we pushed 0x20
                f"bl {s7['h_poly']:#x}; "
                "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
                f"b.w {POLY_RESUME:#x}")
    code = head + asm(poly_src, t + len(head))
    tramps += code + bytes(-len(code) % 4)
    new_branch = bcond_w("ne", POLY_SITE, t)
    if bcond_w("ne", POLY_SITE, POLY_RESUME).hex() != POLY_ORIG:
        raise SystemExit("hand encoder does not reproduce the original bne.w")
    patch(POLY_SITE, new_branch, bytes.fromhex(POLY_ORIG), "M1 poly AT: External handler 0x0E -> h_poly")
    for site, reg, role in NOTE_SITES:
        stub = M7_TRAMP + len(tramps)
        code = asm(f"mov r2, {reg}; b.w {s7['h_note']:#x}", stub)   # h_note(queue, ev, track); returns to site+4
        tramps += code + bytes(-len(code) % 4)
        orig = asm(f"bl {MSG_SEND:#x}", site)
        if bytes(base_img[site - BASE:site - BASE + 4]) != orig:
            raise SystemExit(f"{site:#x}: not the expected bl msg_send")
        patch(site, asm(f"bl {stub:#x}", site), orig, f"M1b poly cap: {role} -> h_note")
    if M7_TRAMP + len(tramps) > M7_CODE:
        raise SystemExit("trampolines overrun into M7 code")
    patch(M7_TRAMP, tramps, bytes(len(tramps)), "M1 diag: M7 trampolines")
    patch(M7_CODE, code7, bytes(len(code7)), "M1 diag: M7 capture code")

    # --- M4: compile, append, redirect the defaultTask-loop BL
    m4_elf = compile_elf([HERE / "m4_diag.c"], "cortex_m4", HERE / "m4.ld", out / "m4.elf")
    va4, code4, s4 = elf_load(m4_elf, M4_CODE, M4_LIMIT)
    if va4 != M4_CODE or M4_CODE + len(code4) > M4_LIMIT:
        raise SystemExit(f"M4 code placement {va4:#x}+{len(code4)} outside our slice")
    if any(base_img[M4_CODE - BASE:M4_LIMIT - BASE]):
        raise SystemExit("our M4 slice is not all zero in the base image")
    patch(M4_CODE, code4 + MARKER, None, "M1 diag: M4 flush code (in CH10 zero gap)")
    if M4_CODE + len(code4) + 20 > M4_LIMIT:
        raise SystemExit("M4 code + marker overrun")
    if M4_CODE + len(code4) + len(MARKER) > M4_POLY:
        raise SystemExit("M4 diag code reaches the poly handler")
    h = asm("mov r0, r5; bl 0x08105d56; "
            "movs r1, #0xa0; mov r0, r5; bl 0x08105c44; "           # Polyphonic Key Pressure
            "uxtb.w r1, r8; mov r0, r5; bl 0x08105c5e; "             # channel (event word 3)
            "uxtb r1, r7; mov r0, r5; bl 0x08105cfe; "               # note (word 4)
            "uxtb.w r1, r11; mov r0, r5; bl 0x08105cfe; "            # pressure (word 5)
            "uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl 0x08105904; " # send on port (word 2)
            "mov r0, r5; bl 0x08105c4c; mov r1, r0; mov r0, r6; bl 0x08105b74; "
            f"b.w {M4_LOOP:#x}", M4_POLY)
    if M4_POLY + len(h) > M4_LIMIT:
        raise SystemExit("M4 poly handler overruns our slice")
    # Cross-check: the native Note On case assembles to the same shape with 0x90.
    native = base_img[0x0810509A - BASE:0x081050DE - BASE]
    ours_as_native = asm(h_src_native := ("mov r0, r5; bl 0x08105d56; movs r1, #0x90; mov r0, r5; bl 0x08105c44; "
            "uxtb.w r1, r8; mov r0, r5; bl 0x08105c5e; uxtb r1, r7; mov r0, r5; bl 0x08105cfe; "
            "uxtb.w r1, r11; mov r0, r5; bl 0x08105cfe; uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl 0x08105904; "
            "mov r0, r5; bl 0x08105c4c; mov r1, r0; mov r0, r6; bl 0x08105b74"), 0x0810509A)
    if ours_as_native != native:
        raise SystemExit("our copy of the Note On case does not match the native bytes")
    patch(M4_POLY, h, bytes(len(h)), "M1 poly AT: M4 0x5F handler (Note On case copy, status 0xA0)")
    old = struct.pack("<I", JT_DEFAULT)
    patch(JT_5F, struct.pack("<I", M4_POLY | 1), old, "M1 poly AT: MIDI task jump table slot 0x5F -> handler")
    bl_check = asm(f"bl {M4_BL_ORIG_TARGET:#x}", M4_BL_SITE)
    if bl_check.hex() != M4_BL_ORIG:
        raise SystemExit("assembler disagrees with the original BL encoding")
    patch(M4_BL_SITE, asm(f"bl {s4['m4_tick']:#x}", M4_BL_SITE), bytes.fromhex(M4_BL_ORIG),
          "M1 diag: defaultTask loop BL -> m4_tick (calls the original first)")

    # --- invariants
    if len(img) != len(base_img):
        raise SystemExit("image size changed; the bootloader only accepted CH10-sized images")
    if len(img) % 4 or BASE + len(img) > 0x08200000:
        raise SystemExit("image alignment/size")
    undo = bytearray(img)
    for w in reversed(writes):
        o = w["address"] - BASE
        if w["original_hex"]:
            undo[o:o + w["size"]] = bytes.fromhex(w["original_hex"])
        else:
            del undo[o:o + w["size"]]
    if bytes(undo) != base_img:
        raise SystemExit("reversal does not reproduce the CH10 base")

    (out / "bento1.bin").write_bytes(img)
    man = dict(label="1.6HA+m1-polyat-v2.2", base_sha256=CH10_SHA, sha256=hashlib.sha256(img).hexdigest(),
               m7_symbols={k: hex(v) for k, v in s7.items()}, m4_symbols={k: hex(v) for k, v in s4.items()},
               m7_code_bytes=len(code7), m4_code_bytes=len(code4), trampoline_bytes=len(tramps), writes=writes)
    (out / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"built {man['label']} sha256={man['sha256']}")
    print(f"  M7 code {len(code7)} B at {M7_CODE:#x}, trampolines {len(tramps)} B; M4 code {len(code4)} B at {M4_CODE:#x}")
    print(f"  {len(writes)} writes, reversal to CH10 verified")
    return img, man

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    build(a.base.read_bytes(), a.out)
