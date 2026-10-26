# SPDX-License-Identifier: GPL-3.0-or-later
"""Build M1 (poly aftertouch + per-track poly cap for External tracks, with
black-box diagnostics) on stock Bento 1.6.1.

    python build.py --base <stock 1.6.1 bento1.bin> --out <new dir>

Refuses any base except stock 1.6.1 (SHA-256 below), checks original bytes at
every patch site, writes bento1.bin + manifest.json, and proves that undoing
every write reproduces the base exactly. Run with Lab's venv (ziglang +
keystone). Earlier versions (<= v2.2) were built on Lab's CH10; CH10 itself
crashed on the device without any of this code, so M1 moved to stock.
"""
import argparse, hashlib, json, struct, subprocess, sys
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB

HERE = Path(__file__).resolve().parent
BASE = 0x08040000
STOCK_SHA = "14475e9d5068f5659d0fe0b00fc1278c1479c43ed9fda3d40654d5f3a7a6bbb3"
# Largest main image the bootloader has accepted on the device (Lab CH10). A
# 0x1A0800-byte image reaching 0x081E0000 was refused; the M7 MPU maps
# 0x081E0000.. as a read-only data region (persistent storage).
PROVEN_MAX_SIZE = 0x18D334
FORBIDDEN_FROM = 0x081E0000

# M7 heap: stock ends it at 0x24048DA0 (three literals, identified by Lab).
# Lowering it frees 0x24048400..0x24048DA0 (below the M7 stack bottom, inside
# the M4's MPU window) for our RAM slice and the trigger-condition counters.
HEAP_LITERALS = (0x08040588, 0x080A3BDC, 0x080A3E94)
HEAP_END_STOCK, HEAP_END_NEW = 0x24048DA0, 0x24048400

M7_TRAMP = 0x080DA000   # inside stock's zero gap after the M7 image's data (0x080CF49C..0x080E0000)
M7_CODE = 0x080DA200
M7_LIMIT = 0x080DB800
# M4 code is appended after stock's image end (0x081B3F3C). 0x081B4000..0x081B5000
# is reserved for the trigger-condition UI (S2).
APPEND_FROM = 0x081B4000
M4_CODE = 0x081B5000
M4_LIMIT = 0x081B6600
MARKER = b"BENTO_MOD:m1-polyat:v2.4-stock+conditions" + bytes(1)

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
M4_POLY = 0x081B6580   # end of our M4 slice (handler is 72 B); diag code grows below it

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

# Trigger conditions (port of bento-community-lab T2, GPL-3.0-or-later).
# M7: probability gate and loop counter. Trampolines are Lab's, unchanged
# except for our function names.
COND_GATE_SITE, COND_GATE_ORIG, COND_GATE_RESUME, COND_SKIP = 0x08060B64, "9df82930", 0x08060B68, 0x080609E2
COND_LOOP_SITE, COND_LOOP_ORIG, COND_LOOP_RESUME = 0x08061740, "2de9f043", 0x08061744
# M4: extend the native probability list (100 labels "1%%".."99%%","Always" at
# 0x0819F30C) with 35 A:B labels, and convert values in the piano-roll and drum
# step editors. Helpers and the new label table live in our reserved block.
COND_UI = 0x081B4000
COND_TABLE = 0x081B4400
COND_UI_LIMIT = 0x081B5000
NATIVE_LABELS = 0x0819F30C
COND_LABELS = [f"{a}:{b}" for b in range(2, 9) for a in range(1, b + 1)]
COND_COUNT_SITE, COND_COUNT_ORIG = 0x08165D40, "cdf80080"
COND_TABLE_PTR_SITE = 0x08165F8C
COND_UI_SITES = [  # (site, helper, original bytes)
    (0x08159B80, "show_piano", "013b3360"),
    (0x0815C3D2, "show_drum", "013b0b60"),
    (0x0815C8B4, "set_drum", "63291abf"),
    (0x08158F1C, "piano_reference", "9df82530"),
    (0x08158A7C, "piano_edit", "9df81930"),
]

def cond_ui_helpers():
    """Lab T2's editor value conversions (native probability byte <-> list index)."""
    def show(store, back):
        return f"cmp r3,#100; blo dec; cmp r3,#134; bls store; movs r3,#99; b store; dec: subs r3,#1; store: str r3,[{store}]; b.w {back}"
    return [
        ("show_piano", show("r6", "0x08159b84")),
        ("show_drum", show("r1", "0x0815c3d6")),
        ("set_drum", "cmp r1,#134; bhi unknown; cmp r1,#100; bhs direct; cmp r1,#99; beq unknown; adds r6,r1,#1; b done; "
                     "direct: mov r6,r1; b done; unknown: movs r6,#0; done: b.w 0x0815c8be"),
        ("piano_reference", "ldrb.w r3,[sp,#0x25]; cbz r3,always; cmp r3,#135; bhs always; cmp r3,#100; blo done; adds r3,#1; "
                            "b done; always: movs r3,#100; done: b.w 0x08158f26"),
        ("piano_edit", "ldrb.w r3,[sp,#0x19]; cbz r3,always; cmp r3,#135; bhs always; cmp r3,#100; blo add; adds r3,#1; b add; "
                       "always: movs r3,#100; add: ldr r2,[sp,#0x84]; adds r3,r3,r2; cmp r3,#1; bge minok; movs r3,#1; "
                       "minok: cmp r3,#135; ble maxok; movs r3,#135; maxok: cmp r3,#100; beq zero; blo store; subs r3,#1; "
                       "b store; zero: movs r3,#0; store: strb.w r3,[sp,#0x31]; b.w 0x08158aac"),
    ]

def check_branches(code, addr, allowed, what):
    """Keystone can mis-encode conditional branches: every branch in an assembled
    block must land inside the block or on an expected exit."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
    md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
    for i in md.disasm(code, addr):
        if i.mnemonic.startswith(("b", "cb")) and i.op_str.startswith("#") and i.mnemonic not in ("bic", "bics", "bfi", "bfc"):
            t = int(i.op_str[1:], 16)
            if not (addr <= t < addr + len(code) or t in allowed):
                raise SystemExit(f"{what}: branch at {i.address:#x} to {t:#x} is outside the block and not an expected exit")

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
    if hashlib.sha256(base_img).hexdigest() != STOCK_SHA:
        raise SystemExit("base is not stock Bento 1.6.1; refusing")
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

    # --- M7 heap: reserve 0x24048400..0x24048DA0
    for a in HEAP_LITERALS:
        patch(a, struct.pack("<I", HEAP_END_NEW), struct.pack("<I", HEAP_END_STOCK),
              "M1: lower M7 heap end to reserve our RAM slice + condition counters (stack unchanged)")

    # --- M7: compile, place, trampolines, entry patches
    m7_elf = compile_elf([HERE / "m7_diag.c", HERE / "m7_polyat.c", HERE / "m7_conditions.c"],
                         "cortex_m7", HERE / "m7.ld", out / "m7.elf")
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
    # Trigger conditions (Lab T2 trampolines): save r0-r12, lr, flags (64 B).
    save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
    restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
    t = M7_TRAMP + len(tramps)
    gate = asm(save + f"mov r0, r8; ldrb.w r1, [sp, #105]; bl {s7['cond_decide']:#x}; "   # value was at sp+0x29
               "cmp r0, #0; beq skip; cmp r0, #1; beq play; "
               + restore + f"ldrb.w r3, [sp, #0x29]; b.w {COND_GATE_RESUME:#x}; "         # native probability
               "play: " + restore + f"movs r3, #0; b.w {COND_GATE_RESUME:#x}; "          # always
               "skip: " + restore + f"b.w {COND_SKIP:#x}", t)
    check_branches(gate, t, {COND_GATE_RESUME, COND_SKIP, s7["cond_decide"]}, "condition gate")
    tramps += gate + bytes(-len(gate) % 4)
    patch(COND_GATE_SITE, asm(f"b.w {t:#x}", COND_GATE_SITE), bytes.fromhex(COND_GATE_ORIG),
          "Conditions (Lab T2): probability gate -> cond_decide")
    t = M7_TRAMP + len(tramps)
    loop = asm(save + f"ldr r0, [sp, #8]; bl {s7['cond_loop']:#x}; " + restore     # r0 = the sequence
               + f"push.w {{r4, r5, r6, r7, r8, r9, lr}}; b.w {COND_LOOP_RESUME:#x}", t)
    check_branches(loop, t, {COND_LOOP_RESUME, s7["cond_loop"]}, "condition loop hook")
    tramps += loop + bytes(-len(loop) % 4)
    patch(COND_LOOP_SITE, asm(f"b.w {t:#x}", COND_LOOP_SITE), bytes.fromhex(COND_LOOP_ORIG),
          "Conditions (Lab T2): sequence loop boundary -> cond_loop")
    if M7_TRAMP + len(tramps) > M7_CODE:
        raise SystemExit("trampolines overrun into M7 code")
    patch(M7_TRAMP, tramps, bytes(len(tramps)), "M1 diag: M7 trampolines")
    patch(M7_CODE, code7, bytes(len(code7)), "M1 diag: M7 capture code")

    # --- M4: compile, append, redirect the defaultTask-loop BL
    m4_elf = compile_elf([HERE / "m4_diag.c"], "cortex_m4", HERE / "m4.ld", out / "m4.elf")
    va4, code4, s4 = elf_load(m4_elf, M4_CODE, M4_LIMIT)
    if va4 != M4_CODE or M4_CODE + len(code4) > M4_LIMIT:
        raise SystemExit(f"M4 code placement {va4:#x}+{len(code4)} outside our slice")
    # Append: pad from stock's end to the end of our slice with 0xFF (erased
    # flash); the reversal check deletes this tail again.
    stock_end = BASE + len(img)
    if stock_end > APPEND_FROM:
        raise SystemExit("base image already reaches our append area")
    pad = b"\xff" * (M4_LIMIT - stock_end)
    img += pad
    writes.append(dict(address=stock_end, size=len(pad), original_hex="", replacement_hex=pad.hex(),
                       purpose="M1: extend image with erased (0xFF) flash for appended M4 code"))
    patch(M4_CODE, code4 + MARKER, None, "M1 diag: M4 black-box code (appended)")

    # --- Trigger conditions, M4 UI (Lab T2): helpers, extended label table, patches
    ui = bytearray(); ui_syms = {}
    for name, src in cond_ui_helpers():
        while len(ui) % 4:
            ui.append(0)
        a = COND_UI + len(ui)
        code = asm(src, a)
        back = int(src.rsplit("b.w ", 1)[1], 16)
        check_branches(code, a, {back}, f"condition UI helper {name}")
        ui_syms[name] = a
        ui += code
    if COND_UI + len(ui) > COND_TABLE:
        raise SystemExit("condition UI helpers overrun the label table")
    native_ptrs = bytes(base_img[NATIVE_LABELS - BASE:NATIVE_LABELS - BASE + 100 * 4])
    strings = b""; new_ptrs = b""
    for label in COND_LABELS:
        new_ptrs += struct.pack("<I", COND_TABLE + 135 * 4 + len(strings))
        strings += label.encode() + b"\0"
    table = native_ptrs + new_ptrs + strings + b"BENTO_MOD:trigger-conditions(Lab T2 port)\0"
    if len(COND_LABELS) != 35 or COND_TABLE + len(table) > COND_UI_LIMIT:
        raise SystemExit("condition label table layout")
    patch(COND_UI, bytes(ui), b"\xff" * len(ui), "Conditions (Lab T2): editor value conversion helpers")
    patch(COND_TABLE, table, b"\xff" * len(table), "Conditions (Lab T2): 100 native + 35 A:B labels")
    for site, name, orig in COND_UI_SITES:
        patch(site, asm(f"b.w {ui_syms[name]:#x}", site), bytes.fromhex(orig), f"Conditions (Lab T2): {name}")
    count = asm("movs r3, #135; str r3, [sp]", COND_COUNT_SITE)
    if len(count) != 4:
        raise SystemExit("condition count patch is not 4 bytes")
    patch(COND_COUNT_SITE, count, bytes.fromhex(COND_COUNT_ORIG), "Conditions (Lab T2): probability list 100 -> 135 entries")
    patch(COND_TABLE_PTR_SITE, struct.pack("<I", COND_TABLE), struct.pack("<I", NATIVE_LABELS),
          "Conditions (Lab T2): probability list labels -> extended table")
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
    patch(M4_POLY, h, b"\xff" * len(h), "M1 poly AT: M4 0x5F handler (Note On case copy, status 0xA0)")
    old = struct.pack("<I", JT_DEFAULT)
    patch(JT_5F, struct.pack("<I", M4_POLY | 1), old, "M1 poly AT: MIDI task jump table slot 0x5F -> handler")
    bl_check = asm(f"bl {M4_BL_ORIG_TARGET:#x}", M4_BL_SITE)
    if bl_check.hex() != M4_BL_ORIG:
        raise SystemExit("assembler disagrees with the original BL encoding")
    patch(M4_BL_SITE, asm(f"bl {s4['m4_tick']:#x}", M4_BL_SITE), bytes.fromhex(M4_BL_ORIG),
          "M1 diag: defaultTask loop BL -> m4_tick (calls the original first)")

    # --- invariants
    if len(img) > PROVEN_MAX_SIZE or BASE + len(img) > FORBIDDEN_FROM:
        raise SystemExit(f"image of {len(img):#x} bytes exceeds the size proven on the device ({PROVEN_MAX_SIZE:#x})")
    if len(img) % 4:
        raise SystemExit("image not word aligned (a misaligned image fails to program)")
    undo = bytearray(img)
    for w in reversed(writes):
        o = w["address"] - BASE
        if w["original_hex"]:
            undo[o:o + w["size"]] = bytes.fromhex(w["original_hex"])
        else:
            del undo[o:o + w["size"]]
    if bytes(undo) != base_img:
        raise SystemExit("reversal does not reproduce the stock base")

    (out / "bento1.bin").write_bytes(img)
    man = dict(label="1.6.1+m1-polyat+conditions-v2.4", base_sha256=STOCK_SHA, sha256=hashlib.sha256(img).hexdigest(),
               m7_symbols={k: hex(v) for k, v in s7.items()}, m4_symbols={k: hex(v) for k, v in s4.items()},
               m7_code_bytes=len(code7), m4_code_bytes=len(code4), trampoline_bytes=len(tramps), writes=writes)
    (out / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"built {man['label']} sha256={man['sha256']}")
    print(f"  M7 code {len(code7)} B at {M7_CODE:#x}, trampolines {len(tramps)} B; M4 code {len(code4)} B at {M4_CODE:#x}")
    print(f"  image {len(img):#x} bytes (proven max {PROVEN_MAX_SIZE:#x}); {len(writes)} writes, reversal to stock verified")
    return img, man

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    build(a.base.read_bytes(), a.out)
