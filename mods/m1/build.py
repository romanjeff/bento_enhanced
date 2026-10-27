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
import argparse, hashlib, json, os, re, struct, subprocess, sys
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
# v2.3-v3.15 lowered it to 0x24048400 for our RAM slice; that broke loading
# large projects (Jeff: SPACED). Since v3.16 the heap is stock and the slice
# lives in unlinked D2 SRAM2 at 0x3003C000 (both cores' MPUs map it).
# Historical note:
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
# Appended M4 blocks, in order (see LAYOUT below for the full table).
M4_CODE = 0x081B5000                        # black box (m4_diag.c)
M4_LIMIT = M4_CODE + 0x3800   # 14 KB since v3.22 (curve log + save dump)
VERSION = "3.24"                       # bump for every build that goes on the device
DISPLAY = f"BE{VERSION}"              # boot screen / info page (Bento Enhanced); max 6 characters
# Bisect builds (diagnosis only): BE_DISABLE="step,hold" leaves those groups' stock
# call sites unpatched (their code stays in the image, unreachable); BE_LABEL sets
# the boot label (<= 6 chars) and BE_TAG suffixes the kit version.
# Off by default since v3.20 (Jeff's bisect, 2026-10-26): with the M7 stall hooks
# (main-loop progress/wait + SysTick profile) patched in, PROJ with a large project
# (SPACED) crash-restarted and the SD card dropped out ("insert microSD card");
# BS-C (stall off) was fine, BS-D (stall on) failed. irq30 (v3.18) is unproven.
# Code stays in the image, unreachable. BE_ENABLE="stall,irq30" turns them back on.
DEFAULT_OFF = {"stall", "irq30"}
DISABLED = (DEFAULT_OFF - {g for g in os.environ.get("BE_ENABLE", "").split(",") if g})     | {g for g in os.environ.get("BE_DISABLE", "").split(",") if g}
DISPLAY = os.environ.get("BE_LABEL", DISPLAY)
VERSION = VERSION + (f"-{os.environ['BE_TAG']}" if os.environ.get("BE_TAG") else "")
PATCH_GROUPS = [("step", ("Per-step", "Step edit", "Strike", "Step inspection", "Curves")),
                ("hold", ("Hold shortcuts",)),
                ("cond", ("Conditions", "Probability:")),
                ("retrig", ("Retrigger",)),
                ("poly", ("M1 poly AT: External", "M1b poly cap", "M1 poly AT: MIDI task")),
                ("trace", ("M1 diag: h_",)),                        # M7 event tracing hooks
                ("stall", ("Stall",)),                              # M7 stall record + SysTick profile
                ("irq30", ("M4 watchdog profile: IRQ30",)),         # M4 heartbeat-supervisor profile
                ("bbox", ("M1 diag: defaultTask", "Diag dump"))]                # M4 black box (also hands poly AT its region)
if "diag7" in DISABLED:
    DISABLED |= {"trace", "stall"}
if "diag" in DISABLED:
    DISABLED |= {"trace", "stall", "irq30", "bbox"}
def patch_group(why):
    return next((g for g, prefixes in PATCH_GROUPS if why.startswith(prefixes)), None)
assert len(DISPLAY) <= 6, "boot label slot copies 6 bytes"
MARKER = f"BENTO_MOD:bento_enhanced:v{VERSION}:stock-1.6.1".encode() + bytes(1)
# Stock's version string "1.6.1" (8-byte slot). Its two users: the boot-screen label
# (FUN_08162950 copies only the first 6 bytes) and "%s-cont-%d" (FUN_08131024).
VERSION_STR, VERSION_ORIG = 0x0818BAD4, b"1.6.1\0\0\0"

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

# Retrigger (m7_retrig.c).
NOTE_ROUTE, RETRIG_ACCEPT_SITE, RETRIG_EMIT_SITE = 0x0805F8E0, 0x08060B9A, 0x08060820

# Audio stall snapshot (idle-restart investigation): the M7 main audio loop
# FUN_0808DF00 refreshes the watchdog only on audio progress.
SYSTICK_HANDLER, HAL_INCTICK = 0x0804BD40, 0x08044280   # M7 SysTick vector target: `b.w HAL_IncTick`
SAVE_WRITE_SITE, FILE_WRITER = 0x0813044C, 0x08130958   # project save (FUN_0813032C): bl to stock's file writer
M4_WD_SITE, M4_WD_SUPERVISOR = 0x08107ADC, 0x08108354   # M4 IRQ30: bl to the Main/PcmThread heartbeat supervisor
STALL_SITES = [  # (bl site, original target, our function, purpose)
    (0x0808DF54, 0x080A0460, "stall_progress", "Stall snapshot: main audio loop progress (watchdog refresh)"),
    (0x0808DFB4, 0x080816C0, "stall_check", "Stall snapshot: main audio loop waiting (message pump)"),
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
COND_UI_LIMIT = 0x081B5000   # probability clamp helpers (block reserved for the original condition UI)
AB_LABELS = [f"{a}:{b}" for b in range(2, 9) for a in range(1, b + 1)]      # stored 100..134 (Lab T2)
# Stored values stay stable (saved projects keep their meaning): A:B 100..134,
# !A:B 135..169, 1ST 170, !1ST 171, PRE 172, !PRE 173. The list shows them as
# 1ST, !1ST, PRE, !PRE, then each A:B followed by its NOT.
COND_STORED = [170, 171, 172, 173] + [v for k in range(35) for v in (100 + k, 135 + k)]
COND_LABELS = ["1ST", "!1ST", "PRE", "!PRE"] + [l for x in AB_LABELS for l in (x, "!" + x)]
COND_N = len(COND_STORED)                                                    # 74 -> list of 174
COND_UI_SITES = [  # (site, helper, original bytes)
    (0x08159B80, "show_piano", "013b3360"),
    (0x0815C3D2, "show_drum", "013b0b60"),
    (0x08158F1C, "piano_reference", "9df82530"),
    (0x08158A7C, "piano_edit", "9df81930"),
]

# Inspect a step (from bento-community-lab T2 holdstep, GPL-3.0-or-later): only the
# edit-protects-release part; no selection kept after release.
HOLD_WRAP = M4_LIMIT; HOLD_CODE = HOLD_WRAP + 0x100; HOLD_LIMIT = HOLD_WRAP + 0x400
HOLD_EDIT_SITES = [0x0815C7C4, 0x0815C8AC, 0x0815C990]   # velocity, condition, timing edit entries

# Hold shortcuts (port of bento-community-lab K16 hold controls, GPL-3.0-or-later).
CTRL_INPUT, CTRL_STARTUP = 0x080D9000, 0x080D9800   # M7, in stock's zero gap (below our 0x080DA000)
CTRL_INPUT_SITE, CTRL_STARTUP_SITE = 0x0808D1FE, 0x080404B6
CTRL_STATE = 0x3003C8A0                             # M7-only line: magic, suppress mask, drops, enable, pad map
CTRL_WRAP = HOLD_LIMIT; CTRL_CODE = CTRL_WRAP + 0x200; CTRL_LIMIT = CTRL_WRAP + 0x1000
STEP_WRAP = CTRL_LIMIT; STEP_CODE = STEP_WRAP + 0x300; STEP_LIMIT = STEP_WRAP + 0x2800   # m4_step.c (10 KB since v3.24)
M4_POLY = STEP_LIMIT; M4_POLY_LIMIT = M4_POLY + 0x100   # 0x5F handler (72 B)
# Per-step Condition / Retrigger (m4_step.c)
STEP_SAVE_SITE, STEP_LOAD_SITE = 0x0812F4F0, 0x0812FBB4
DRUM_HEADER, DRUM_ENCODER = 0x0815C27C, 0x0815CAA0
PIANO_HEADER, PIANO_EVENT = 0x081599BC, 0x0815A598
# Strike velocity: drum step pad press / step toggle (always on since v3.8)
STEP_PRESS, STEP_TOGGLE = 0x0815D1EC, 0x0815D114
HEADER_GEN, DRUM_EVENT = 0x08134800, 0x0815D600   # header description generator; drum view events
NOTE_INSERT = 0x0812E070                            # stock: assign ID + insert (model, seq, record)
NOTE_UPDATE = 0x0812E0CE                            # stock: replace a note (model, seq, record)
HOLD_TAP_SITES = (0x0815D284, 0x0815D484)          # pad release / touch release: "< 1000 ms = tap"
HOLD_TAP_MS = 150   # Jeff: 400 -> 150 ms, confirmed on device v3.13
STRIKE_ACTION = 0x10F0
LAYOUT = dict(M7_TRAMP=M7_TRAMP, M7_CODE=M7_CODE, M4_CODE=M4_CODE, HOLD_WRAP=HOLD_WRAP, HOLD_CODE=HOLD_CODE,
              CTRL_WRAP=CTRL_WRAP, CTRL_CODE=CTRL_CODE, STEP_WRAP=STEP_WRAP, STEP_CODE=STEP_CODE,
              M4_POLY=M4_POLY, IMAGE_END=M4_POLY_LIMIT)
CTRL_CONSUMER_SITE, CTRL_DISPATCH_STOCK, CTRL_DISPATCH_NEXT = 0x0813F98C, 0x0813F990, 0x0813F2FC
CTRL_SELECTION_SITE, CTRL_MIXER_VT = 0x0813BD10, 0x0819DD20
CTRL_SYMBOLS = dict(STATE=hex(CTRL_STATE), STATE_MAGIC="0x31534d42", EVENT_MAGIC="0x314d5442", EVENT_TAIL="0x31525442",
                    PRODUCER_GLOBAL="0x2401bd3c", ENQUEUE="0x08082221", PAD_NEXT="0x0808d1f1", PAD_CHANGED="0x0808d203",
                    TRACKS_MASK="0x2000", LAUNCH_MASK="0x1000", MIXER_MASK="0x400",
                    MODIFIERS_MASK="0x3400")             # Lab: 0x3500 (incl. PROJ 0x100)

def cond_ui_helpers():
    """Editor value conversions for the probability byte (Lab T2 sites). Since
    v3.3 conditions have their own encoder, so these only clamp: a legacy
    condition value (100..173) still in the byte shows as "Always" instead of
    indexing past stock's 100-entry list."""
    def show(store, back):   # stored byte r3 -> list index (0-based) in [store]
        return f"cmp r3, #100; blo dec; movs r3, #99; b store; dec: subs r3, #1; store: str r3, [{store}]; b.w {back}"
    return [
        ("show_piano", show("r6", "0x08159b84")),
        ("show_drum", show("r1", "0x0815c3d6")),
        # piano roll works in 1-based list values: 1..99 = %, 100 = Always
        ("piano_reference", "ldrb.w r3, [sp, #0x25]; cbz r3, always; cmp r3, #100; blo done; "
                            "always: movs r3, #100; done: b.w 0x08158f26"),
        ("piano_edit", "ldrb.w r3, [sp, #0x19]; cbz r3, always; cmp r3, #100; blo add; always: movs r3, #100; "
                       "add: ldr r2, [sp, #0x84]; adds r3, r3, r2; cmp r3, #1; bge minok; movs r3, #1; "
                       "minok: cmp r3, #100; blt store; movs r3, #0; "
                       "store: strb.w r3, [sp, #0x31]; b.w 0x08158aac"),
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

DIV_LABELS = ["1/8", "1/8T", "1/8.", "1/16", "1/16T", "1/16.", "1/32", "1/32T", "1/32.", "1/64", "1/64T"]
RAMP_STEP = 5   # percent per encoder click: -100..+100 = 41 values

def write_step_tables(path):
    """Generated C tables for m4_step.c (single source: the lists above)."""
    def strs(name, items):
        return f"static const char *const {name}[{len(items)}] = {{" + ", ".join(f'"{x}"' for x in items) + "};\n"
    ramp = [f"{v:+d}%" if v else "0%" for v in range(-100, 101, RAMP_STEP)]
    text = ("/* Generated by build.py (write_step_tables); do not edit. */\n"
            f"#define COND_N {COND_N}\n#define RAMP_N {len(ramp)}\n#define RAMP_STEP {RAMP_STEP}\n"
            + strs("COND_LABELS", ["Off"] + COND_LABELS)
            + strs("HITS_LABELS", ["Off"] + [str(i) for i in range(1, 16)])
            + strs("DIV_LABELS", DIV_LABELS)
            + strs("RAMP_LABELS", ramp)
            + f"static const unsigned char COND_STORED[{COND_N}] = {{" + ", ".join(map(str, COND_STORED)) + "};\n")
    path.write_text(text)

def compile_elf(srcs, cpu, ld, out, origin, limit):
    """ld's MEMORY ORIGIN/LENGTH are replaced by this build's layout (written next to the ELF)."""
    text, n = re.subn(r"ORIGIN = 0x[0-9A-Fa-f]+, LENGTH = 0x[0-9A-Fa-f]+",
                      f"ORIGIN = {origin:#x}, LENGTH = {limit - origin:#x}", Path(ld).read_text())
    if n != 1:
        raise SystemExit(f"{ld}: expected one MEMORY line")
    ld = out.with_suffix(".ld")
    ld.write_text(text)
    write_step_tables(out.parent / "step_tables.h")
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "thumb-freestanding-eabi", f"-mcpu={cpu}",
           "-mfloat-abi=soft", "-Os", "-g", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-nostdlib",
           "-ffunction-sections", "-fdata-sections", "-Wall", "-Werror",
           "-Wl,--gc-sections", f"-I{out.parent}", f"-Wl,--script={ld}", "-Wl,--entry=0", *map(str, srcs), "-o", str(out)]
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
    skipped = []

    def patch(addr, new, expect, why):
        if len(new) <= 8 and patch_group(why) in DISABLED:   # bisect build: leave stock's site alone
            skipped.append((addr, why)); return
        o = addr - BASE
        old = bytes(img[o:o + len(new)])
        if expect is not None and old != expect:
            raise SystemExit(f"{why}: original bytes at {addr:#x} are {old.hex()}, expected {expect.hex()}")
        img[o:o + len(new)] = new
        writes.append(dict(address=addr, size=len(new), original_hex=old.hex(), replacement_hex=new.hex(), purpose=why))

    # --- M7 heap: left at stock since v3.16 (our slice is in D2 SRAM2)
    for a in HEAP_LITERALS:
        if img[a - BASE:a - BASE + 4] != struct.pack("<I", HEAP_END_STOCK):
            raise SystemExit(f"heap literal at {a:#x} is not stock")

    # --- M7: compile, place, trampolines, entry patches
    m7_elf = compile_elf([HERE / "m7_diag.c", HERE / "m7_polyat.c", HERE / "m7_conditions.c", HERE / "m7_stall.c", HERE / "m7_retrig.c"],
                         "cortex_m7", HERE / "m7.ld", out / "m7.elf", M7_CODE, M7_LIMIT)
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
    # Retrigger (m7_retrig.c): accept = stub around stock's bl note_route;
    # emit = entry trampoline of the timed-note processor.
    stub = M7_TRAMP + len(tramps)
    code = asm("push {r3, r4, r5, r6, r7, lr}; mov r4, r0; mov r5, r1; mov r6, r2; mov r7, r3; "
               "ldr r0, [sp, #24]; ldr r1, [sp, #28]; sub sp, #8; str r0, [sp]; str r1, [sp, #4]; "   # pad, router
               f"mov r0, r4; mov r1, r5; mov r2, r6; mov r3, r7; bl {NOTE_ROUTE:#x}; "
               "str r6, [sp]; mov r0, r4; mov r1, r5; add r2, sp, #32; ldr r3, [sp, #20]; "          # vel; caller sp; caller r6 (clock)
               f"bl {s7['retrig_accept']:#x}; add sp, #8; pop {{r3, r4, r5, r6, r7, pc}}", stub)
    check_branches(code, stub, {NOTE_ROUTE, s7["retrig_accept"]}, "retrigger accept stub")
    tramps += code + bytes(-len(code) % 4)
    orig = asm(f"bl {NOTE_ROUTE:#x}", RETRIG_ACCEPT_SITE)
    patch(RETRIG_ACCEPT_SITE, asm(f"bl {stub:#x}", RETRIG_ACCEPT_SITE), orig, "Retrigger: accepted note -> note_route, then queue the burst")
    t = M7_TRAMP + len(tramps)
    code = asm("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
               "ldr r12, [sp, #32]; sub sp, #8; str r12, [sp]; "                                       # 5th argument (router)
               f"bl {s7['retrig_emit']:#x}; add sp, #8; "
               "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
               f"push.w {{r4, r5, r6, r7, r8, r9, r10, r11, lr}}; b.w {RETRIG_EMIT_SITE + 4:#x}", t)
    check_branches(code, t, {s7["retrig_emit"], RETRIG_EMIT_SITE + 4}, "retrigger emit trampoline")
    tramps += code + bytes(-len(code) % 4)
    patch(RETRIG_EMIT_SITE, asm(f"b.w {t:#x}", RETRIG_EMIT_SITE), bytes.fromhex("2de9f04f"),
          "Retrigger: timed-note processor entry -> post due hits")
    # Audio stall snapshot: the main audio loop's two calls (see m7_stall.c).
    for site, target, fn, why in STALL_SITES:
        stub = M7_TRAMP + len(tramps)
        code = asm(f"push {{r0, lr}}; bl {s7[fn]:#x}; pop {{r0, lr}}; b.w {target:#x}", stub)
        check_branches(code, stub, {s7[fn], target}, f"stall stub {fn}")
        tramps += code + bytes(-len(code) % 4)
        orig = asm(f"bl {target:#x}", site)
        if bytes(base_img[site - BASE:site - BASE + 4]) != orig:
            raise SystemExit(f"{site:#x}: not the expected bl {target:#x}")
        patch(site, asm(f"bl {stub:#x}", site), orig, why)
    # Trigger conditions (Lab T2 trampolines): save r0-r12, lr, flags (64 B).
    save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
    restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
    t = M7_TRAMP + len(tramps)
    gate = asm(save + f"mov r0, r8; ldrb.w r1, [sp, #105]; ldr r2, [sp, #124]; bl {s7['cond_decide']:#x}; "   # value sp+0x29, word sp+0x3C
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
    # Stall profile (v3.17): the M7 SysTick handler is a lone `b.w HAL_IncTick`;
    # sample the interrupted frame first (m7_stall.c prof_sample).
    t = M7_TRAMP + len(tramps)
    # The M7 is bare-metal and always on MSP (SVC/PendSV are default stubs), so the
    # frame is at MSP. Keystone cannot assemble `mrs r0, msp`: emit F3EF 8008.
    tick = bytes.fromhex("eff30880") + asm(
        f"push {{r0, lr}}; bl {s7['prof_sample']:#x}; pop {{r0, lr}}; b.w {HAL_INCTICK:#x}", t + 4)
    check_branches(tick, t, {s7["prof_sample"], HAL_INCTICK}, "SysTick profile hook")
    tramps += tick + bytes(-len(tick) % 4)
    orig = asm(f"b.w {HAL_INCTICK:#x}", SYSTICK_HANDLER)
    if bytes(base_img[SYSTICK_HANDLER - BASE:SYSTICK_HANDLER - BASE + 4]) != orig:
        raise SystemExit("SysTick handler is not the expected b.w HAL_IncTick")
    patch(SYSTICK_HANDLER, asm(f"b.w {t:#x}", SYSTICK_HANDLER), orig,
          "Stall profile: M7 SysTick samples the interrupted PC/LR while audio is stalled")
    if M7_TRAMP + len(tramps) > M7_CODE:
        raise SystemExit("trampolines overrun into M7 code")
    patch(M7_TRAMP, tramps, bytes(len(tramps)), "M1 diag: M7 trampolines")
    patch(M7_CODE, code7, bytes(len(code7)), "M1 diag: M7 capture code")

    # --- M4: compile, append, redirect the defaultTask-loop BL
    m4_elf = compile_elf([HERE / "m4_diag.c"], "cortex_m4", HERE / "m4.ld", out / "m4.elf", M4_CODE, M4_LIMIT)
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
    # M4 main-task watchdog profile (v3.18): the IRQ30 handler's `bl` to stock's
    # heartbeat supervisor goes through a stub that first calls main_prof(msp, psp).
    # Keystone cannot assemble `mrs r1, psp`: emit F3EF 8109.
    stub = (M4_CODE + len(code4) + len(MARKER) + 3) & ~3
    sup = asm("mov r0, sp", stub) + bytes.fromhex("eff30981")
    sup += asm(f"push {{r3, lr}}; bl {s4['main_prof']:#x}; pop {{r3, lr}}; b.w {M4_WD_SUPERVISOR:#x}", stub + len(sup))
    check_branches(sup, stub, {s4["main_prof"], M4_WD_SUPERVISOR}, "M4 watchdog profile stub")
    if stub + len(sup) > M4_LIMIT:
        raise SystemExit("M4 watchdog profile stub overruns the black-box block")
    patch(stub, sup, None, "M4 watchdog profile: stub before the heartbeat supervisor")
    orig = asm(f"bl {M4_WD_SUPERVISOR:#x}", M4_WD_SITE)
    if bytes(base_img[M4_WD_SITE - BASE:M4_WD_SITE - BASE + 4]) != orig:
        raise SystemExit(f"{M4_WD_SITE:#x}: not the expected bl to the heartbeat supervisor")
    patch(M4_WD_SITE, asm(f"bl {stub:#x}", M4_WD_SITE), orig,
          "M4 watchdog profile: IRQ30 -> main_prof, then stock's heartbeat supervisor")
    # Diagnostics dump (v3.22): our only card access, right after stock's own
    # project-file write, from the same task (FUN_0813032C -> FUN_08130958).
    dstub = (stub + len(sup) + 3) & ~3
    dump = asm(f"push {{r4, lr}}; bl {FILE_WRITER:#x}; mov r4, r0; bl {s4['dump_after_save']:#x}; "
               "mov r0, r4; pop {r4, pc}", dstub)
    check_branches(dump, dstub, {FILE_WRITER, s4["dump_after_save"]}, "diagnostics dump stub")
    if dstub + len(dump) > M4_LIMIT:
        raise SystemExit("diagnostics dump stub overruns the black-box block")
    patch(dstub, dump, None, "Diag dump: stub around stock's project-file write")
    orig = asm(f"bl {FILE_WRITER:#x}", SAVE_WRITE_SITE)
    if bytes(base_img[SAVE_WRITE_SITE - BASE:SAVE_WRITE_SITE - BASE + 4]) != orig:
        raise SystemExit(f"{SAVE_WRITE_SITE:#x}: not the expected bl to stock's file writer")
    patch(SAVE_WRITE_SITE, asm(f"bl {dstub:#x}", SAVE_WRITE_SITE), orig,
          "Diag dump: project save writes the file, then the black box appends its log")

    # --- Probability editor conversions (Lab T2 sites, clamp only since v3.3)
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
    if COND_UI + len(ui) > COND_UI_LIMIT:
        raise SystemExit("probability conversion helpers overrun their block")
    patch(COND_UI, bytes(ui), b"\xff" * len(ui), "Probability editor conversions (clamp legacy condition values)")
    for site, name, orig in COND_UI_SITES:
        patch(site, asm(f"b.w {ui_syms[name]:#x}", site), bytes.fromhex(orig), f"Probability: {name} clamp")

    # --- Inspect a step: encoder-edit wrappers at HOLD_WRAP, C at HOLD_CODE
    hold_elf = compile_elf([HERE / "m4_holdstep.c"], "cortex_m4", HERE / "m4_holdstep.ld", out / "m4_holdstep.elf", HOLD_CODE, HOLD_LIMIT)
    vah, codeh, sh = elf_load(hold_elf, HOLD_CODE, HOLD_LIMIT)
    if vah != HOLD_CODE:
        raise SystemExit(f"hold-step code placement {vah:#x}")
    pad = b"\xff" * (HOLD_LIMIT - (BASE + len(img)))
    writes.append(dict(address=BASE + len(img), size=len(pad), original_hex="", replacement_hex=pad.hex(),
                       purpose="Inspect a step: extend image with erased (0xFF) flash"))
    img += pad
    wrap = bytearray()
    def wrap_block(src, exits):
        while len(wrap) % 4:
            wrap.append(0)
        a = HOLD_WRAP + len(wrap)
        code = asm(src, a)
        check_branches(code, a, exits, "hold-step wrapper")
        wrap.extend(code)
        return a
    # (the three edit sites are wrapped in the per-step block: protection + defaults/locks)
    hold_marker = b"BENTO_MOD:step-edit-keeps-step(from Lab T2)\0"
    if HOLD_CODE + len(codeh) + len(hold_marker) > HOLD_LIMIT:
        raise SystemExit("hold-step code overruns its slice")
    patch(HOLD_CODE, codeh + hold_marker, b"\xff" * (len(codeh) + len(hold_marker)), "Inspect a step (Lab T2): code")
    s4.update({f"hold.{k}": v for k, v in sh.items()})

    # --- Hold shortcuts (Lab K16 port): M7 pad input + startup, M4 consumer/observer/mixer render
    def template(path, addr):
        src = "\n".join(x.split("//")[0] for x in path.read_text().splitlines())
        for k, v in CTRL_SYMBOLS.items():
            src = src.replace(f"@{k}@", v)
        if "@" in src:
            raise SystemExit(f"{path.name}: unresolved template symbol")
        return asm(src, addr)
    code = template(HERE / "m7_controls_input.s", CTRL_INPUT)
    if CTRL_INPUT + len(code) > CTRL_STARTUP:
        raise SystemExit("controls input code overruns its slice")
    patch(CTRL_INPUT, code, bytes(len(code)), "Hold shortcuts (Lab K16): M7 pad input hook")
    patch(CTRL_INPUT_SITE, asm(f"b.w {CTRL_INPUT:#x}", CTRL_INPUT_SITE), bytes.fromhex("9c42f6d0"),
          "Hold shortcuts (Lab K16): pad receiver -> input hook")
    lit = [struct.unpack_from("<I", base_img, ((a + 4) & ~3) + (struct.unpack_from("<H", base_img, a - BASE)[0] & 0xFF) * 4 - BASE)[0]
           for a in (CTRL_STARTUP_SITE, CTRL_STARTUP_SITE + 2)]
    startup = asm(
        f"push.w {{r0-r12, lr}}; ldr r0, ={CTRL_STATE:#x}; movs r1, #0; movs r2, #8; "
        "zero: str r1, [r0], #4; subs r2, #1; bne zero; "
        f"ldr r0, ={CTRL_STATE:#x}; movs r1, #1; strb r1, [r0, #12]; movs r1, #0; "
        "map: add.w r2, r0, #16; strb r1, [r2, r1]; adds r1, #1; cmp r1, #16; bne map; "
        f"ldr r1, ={CTRL_SYMBOLS['STATE_MAGIC']}; dsb; str r1, [r0]; pop.w {{r0-r12, lr}}; "
        f"ldr r0, ={lit[0]:#x}; ldr r1, ={lit[1]:#x}; "                       # the two displaced literal loads
        f"push {{r0, r1}}; ldr r0, ={(CTRL_STARTUP_SITE + 4) | 1:#x}; str r0, [sp, #4]; pop {{r0, pc}}", CTRL_STARTUP)
    if lit != [0x080A5F9C, 0x080A5FE8] or CTRL_STARTUP + len(startup) > CTRL_STARTUP + 0x100:
        raise SystemExit("controls startup: unexpected literals or size")
    patch(CTRL_STARTUP, startup, bytes(len(startup)), "Hold shortcuts (Lab K16): M7 startup inits only its own state line")
    patch(CTRL_STARTUP_SITE, asm(f"b.w {CTRL_STARTUP:#x}", CTRL_STARTUP_SITE), bytes.fromhex("24482449"),
          "Hold shortcuts (Lab K16): M7 startup -> state init")

    ctl_elf = compile_elf([HERE / "m4_controls.c"], "cortex_m4", HERE / "m4_controls.ld", out / "m4_controls.elf", CTRL_CODE, CTRL_LIMIT)
    vac, codec, sc = elf_load(ctl_elf, CTRL_CODE, CTRL_LIMIT)
    if vac != CTRL_CODE:
        raise SystemExit(f"controls code placement {vac:#x}")
    pad = b"\xff" * (CTRL_LIMIT - (BASE + len(img)))
    writes.append(dict(address=BASE + len(img), size=len(pad), original_hex="", replacement_hex=pad.hex(),
                       purpose="Hold shortcuts: extend image with erased (0xFF) flash"))
    img += pad
    consumer = asm(
        "push.w {r0-r12, lr}; sub sp, #8; add.w r0, sp, #608; str r0, [sp]; "   # record (orig sp+0x220)
        f"mov r0, r4; mov r1, r5; mov r2, r6; mov r3, r7; bl {sc['controls_dispatch']:#x}; "
        "cmp r0, #0; add sp, #8; pop.w {r0-r12, lr}; bne consumed; "
        f"sub.w r3, r5, #17; b.w {CTRL_DISPATCH_STOCK:#x}; "                    # displaced instruction
        f"consumed: b.w {CTRL_DISPATCH_NEXT:#x}", CTRL_WRAP)
    check_branches(consumer, CTRL_WRAP, {sc["controls_dispatch"], CTRL_DISPATCH_STOCK, CTRL_DISPATCH_NEXT}, "controls consumer")
    sel_at = CTRL_WRAP + 0x80
    selection = asm(
        "push.w {r0-r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
        f"ldr r3, =0x3003c8f0; ldr r0, [r3]; cmp r0, #0; beq replay; cmp r2, #15; bhi replay; "
        "movs r0, #1; str r0, [r3, #20]; "                                        # INST->picked
        "replay: pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0-r3, r12, lr}; "
        f"push {{r4-r7, lr}}; sub sp, #36; b.w {CTRL_SELECTION_SITE + 4:#x}", sel_at)
    check_branches(selection, sel_at, {CTRL_SELECTION_SITE + 4}, "INST selection observer")
    if len(consumer) > 0x80 or sel_at + len(selection) > CTRL_CODE:
        raise SystemExit("controls wrappers overrun")
    patch(CTRL_WRAP, consumer, b"\xff" * len(consumer), "Hold shortcuts (Lab K16): UI consumer wrapper")
    patch(sel_at, selection, b"\xff" * len(selection), "Hold shortcuts (Lab K16): INST selection observer")
    ctl_marker = b"BENTO_MOD:hold-shortcuts(Lab K16 port)\0"
    if CTRL_CODE + len(codec) + len(ctl_marker) > CTRL_LIMIT:
        raise SystemExit("controls code overruns its slice")
    patch(CTRL_CODE, codec + ctl_marker, b"\xff" * (len(codec) + len(ctl_marker)), "Hold shortcuts (Lab K16): UI code")
    patch(CTRL_CONSUMER_SITE, asm(f"b.w {CTRL_WRAP:#x}", CTRL_CONSUMER_SITE), bytes.fromhex("a5f11103"),
          "Hold shortcuts (Lab K16): UI event consumer -> controls_dispatch")
    patch(CTRL_SELECTION_SITE, asm(f"b.w {sel_at:#x}", CTRL_SELECTION_SITE), bytes.fromhex("f0b589b0"),
          "Hold shortcuts (Lab K16): drum cell selection observer")
    patch(CTRL_MIXER_VT, struct.pack("<I", sc["mixer_render"] | 1), struct.pack("<I", 0x0814B631),
          "Hold shortcuts (Lab K16): mixer render shows Solo/Mute while MIXER is held")
    s4.update({f"controls.{k}": v for k, v in sc.items()})

    # --- Per-step Condition / Retrigger UI (m4_step.c)
    st_elf = compile_elf([HERE / "m4_step.c"], "cortex_m4", HERE / "m4_step.ld", out / "m4_step.elf", STEP_CODE, STEP_LIMIT)
    vas, codes, ss = elf_load(st_elf, STEP_CODE, STEP_LIMIT)
    if vas != STEP_CODE:
        raise SystemExit(f"step code placement {vas:#x}")
    pad = b"\xff" * (STEP_LIMIT - (BASE + len(img)))
    writes.append(dict(address=BASE + len(img), size=len(pad), original_hex="", replacement_hex=pad.hex(),
                       purpose="Per-step UI: extend image with erased (0xFF) flash"))
    img += pad
    wrap = bytearray()
    def sblock(src, exits, what):
        while len(wrap) % 4:
            wrap.append(0)
        a = STEP_WRAP + len(wrap)
        code = asm(src, a)
        check_branches(code, a, exits, what)
        wrap.extend(code)
        return a
    save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
    restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
    # project save / load: node in r4, record on the caller's stack (+64 after our save)
    a = sblock(save + f"mov r0, r4; add r1, sp, #72; bl {ss['step_save']:#x}; " + restore
               + f"ldrb.w r3, [sp, #8]; b.w {STEP_SAVE_SITE + 4:#x}", {ss["step_save"], STEP_SAVE_SITE + 4}, "step save")
    patch(STEP_SAVE_SITE, asm(f"b.w {a:#x}", STEP_SAVE_SITE), bytes.fromhex("9df80830"), "Per-step: note XML save -> be_step_v1")
    a = sblock(save + f"mov r0, r4; add r1, sp, #100; bl {ss['step_load']:#x}; " + restore
               + f"add r1, sp, #0x24; ldr r0, [sp, #0x3c]; b.w {STEP_LOAD_SITE + 4:#x}", {ss["step_load"], STEP_LOAD_SITE + 4}, "step load")
    patch(STEP_LOAD_SITE, asm(f"b.w {a:#x}", STEP_LOAD_SITE), bytes.fromhex("09a90f98"),
          "Per-step: note XML load -> be_step_v1 + migrate conditions out of the probability byte")
    # drum step sequencer header: before/after stock's builder
    orig_drum = sblock(f"push.w {{r4, r5, r6, r7, r8, r9, lr}}; b.w {DRUM_HEADER + 4:#x}", {DRUM_HEADER + 4}, "drum header replay")
    a = sblock(f"push {{r4, lr}}; mov r4, r0; bl {ss['step_header_before']:#x}; mov r0, r4; bl {orig_drum:#x}; "
               f"mov r0, r4; bl {ss['step_header_after']:#x}; pop {{r4, pc}}",
               {ss["step_header_before"], orig_drum, ss["step_header_after"]}, "drum header")
    patch(DRUM_HEADER, asm(f"b.w {a:#x}", DRUM_HEADER), bytes.fromhex("2de9f043"), "Per-step: drum step header -> encoders 5-8")
    # drum encoder events (view, key, value)
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; ldr r2, [sp, #16]; bl {ss['step_encoder']:#x}; "
               "cmp r0, #0; beq stock; " + restore + "movs r0, #0; bx lr; "
               "stock: " + restore + f"add.w r3, r0, #0x11000; b.w {DRUM_ENCODER + 4:#x}",
               {ss["step_encoder"], DRUM_ENCODER + 4}, "drum encoder")
    patch(DRUM_ENCODER, asm(f"b.w {a:#x}", DRUM_ENCODER), bytes.fromhex("00f58833"), "Per-step: drum encoder events -> ours first")
    # piano roll header (after stock's builder) and events
    orig_piano = sblock(f"push.w {{r4, r5, r6, r7, r8, r9, r10, r11, lr}}; b.w {PIANO_HEADER + 4:#x}", {PIANO_HEADER + 4}, "piano header replay")
    a = sblock(f"push {{r4, lr}}; mov r4, r0; bl {orig_piano:#x}; mov r0, r4; bl {ss['piano_header_after']:#x}; pop {{r4, pc}}",
               {orig_piano, ss["piano_header_after"]}, "piano header")
    patch(PIANO_HEADER, asm(f"b.w {a:#x}", PIANO_HEADER), bytes.fromhex("2de9f04f"), "Per-step: piano header -> Condition on encoder 7")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['piano_event']:#x}; "
               "cmp r0, #0; beq stock; " + restore + "movs r0, #0; bx lr; "
               "stock: " + restore + f"push {{r4, r5, r6, r7, lr}}; sub sp, #0x174; b.w {PIANO_EVENT + 4:#x}",
               {ss["piano_event"], PIANO_EVENT + 4}, "piano event")
    patch(PIANO_EVENT, asm(f"b.w {a:#x}", PIANO_EVENT), bytes.fromhex("f0b5ddb0"), "Per-step: piano events -> ours first")
    # stock's velocity / probability / offset edits (view, value): keep the step on
    # release (hold-step), then defaults + locks around stock's own edit
    for f, site in enumerate(HOLD_EDIT_SITES):
        orig_edit = sblock(f"push.w {{r4, r5, r6, r7, r8, r9, r10, r11, lr}}; b.w {site + 4:#x}", {site + 4}, "edit replay")
        a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {sh['step_edit']:#x}; "
                   f"mov r0, r4; movs r1, #{f}; bl {ss['param_pre']:#x}; mov r6, r0; "
                   f"mov r0, r4; mov r1, r5; bl {orig_edit:#x}; "
                   f"mov r0, r4; movs r1, #{f}; mov r2, r6; bl {ss['param_post']:#x}; pop {{r4, r5, r6, pc}}",
                   {sh["step_edit"], ss["param_pre"], orig_edit, ss["param_post"]}, "edit wrapper")
        patch(site, asm(f"b.w {a:#x}", site), bytes.fromhex("2de9f04f"),
              f"Step edit {('velocity', 'probability', 'offset')[f]}: keep step on release + defaults/locks")
    # strike velocity: pad press (view, pad) and step toggle (view, idx)
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['strike_press']:#x}; " + restore
               + f"cmp r1, #15; bls stock; b.w {STEP_PRESS + 0x48:#x}; stock: b.w {STEP_PRESS + 4:#x}",
               {ss["strike_press"], STEP_PRESS + 0x48, STEP_PRESS + 4}, "strike press")
    if bytes(base_img[STEP_PRESS - BASE:STEP_PRESS - BASE + 4]) != asm(f"cmp r1, #15; bhi {STEP_PRESS + 0x48:#x}", STEP_PRESS):
        raise SystemExit("step press entry is not the expected cmp/bhi")
    patch(STEP_PRESS, asm(f"b.w {a:#x}", STEP_PRESS), bytes.fromhex("0f2921d8"), "Strike velocity: drum step pad press")
    orig_toggle = sblock(f"push {{r3, r4, r5, lr}}; cmp r1, #0x1f; b.w {STEP_TOGGLE + 4:#x}", {STEP_TOGGLE + 4}, "toggle replay")
    a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {ss['toggle_pre']:#x}; mov r6, r0; "
               f"mov r0, r4; mov r1, r5; bl {orig_toggle:#x}; "
               f"mov r0, r4; mov r1, r5; mov r2, r6; bl {ss['toggle_post']:#x}; pop {{r4, r5, r6, pc}}",
               {ss["toggle_pre"], orig_toggle, ss["toggle_post"]}, "toggle wrapper")
    patch(STEP_TOGGLE, asm(f"b.w {a:#x}", STEP_TOGGLE), bytes.fromhex("38b51f29"), "Strike velocity: step toggle")
    # "Strike Vel" header button: inject after stock's header generator; catch its action
    orig_gen = sblock(f"push.w {{r4, r5, r6, r7, r8, r9, r10, lr}}; b.w {HEADER_GEN + 4:#x}", {HEADER_GEN + 4}, "header replay")
    a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {orig_gen:#x}; mov r2, r0; mov r0, r4; mov r1, r5; "
               f"bl {ss['strike_toolbar']:#x}; pop {{r4, r5, r6, pc}}", {orig_gen, ss["strike_toolbar"]}, "header wrapper")
    patch(HEADER_GEN, asm(f"b.w {a:#x}", HEADER_GEN), bytes.fromhex("2de9f047"), "Strike Vel: header button (slot left of Undo)")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['strike_event']:#x}; cmp r0, #0; beq stock; "
               + restore + "movs r0, #0; bx lr; stock: " + restore + f"push {{lr}}; sub sp, #0x6c; b.w {DRUM_EVENT + 4:#x}",
               {ss["strike_event"], DRUM_EVENT + 4}, "drum event")
    patch(DRUM_EVENT, asm(f"b.w {a:#x}", DRUM_EVENT), bytes.fromhex("00b59bb0"), "Strike Vel: button action in the drum view")
    # strike velocity for notes created by any view (drum Sequence page)
    a = sblock(save + f"ldr r0, [sp, #16]; ldr r1, [sp, #60]; bl {ss['strike_insert']:#x}; " + restore   # record, caller lr
               + f"push {{r4, r5, r6, lr}}; sub sp, #0x20; b.w {NOTE_INSERT + 4:#x}", {ss["strike_insert"], NOTE_INSERT + 4}, "note insert")
    patch(NOTE_INSERT, asm(f"b.w {a:#x}", NOTE_INSERT), bytes.fromhex("70b588b0"), "Strike velocity: notes created by any view")
    # M2-1: melodic recording sets a note's length at note-off through this update
    a = sblock(save + f"ldr r0, [sp, #16]; ldr r1, [sp, #60]; bl {ss['curve_update']:#x}; " + restore   # record, caller lr
               + f"push {{r4, lr}}; sub sp, #8; b.w {NOTE_UPDATE + 4:#x}", {ss["curve_update"], NOTE_UPDATE + 4}, "note update")
    patch(NOTE_UPDATE, asm(f"b.w {a:#x}", NOTE_UPDATE), bytes.fromhex("10b582b0"), "Curves: note update (recording note-off) -> curve_update")
    # shorter tap/hold boundary for step inspection (Jeff: 400 ms)
    for site in HOLD_TAP_SITES:
        patch(site, asm(f"cmp.w r3, #{HOLD_TAP_MS}", site), bytes.fromhex("b3f57a7f"),
              f"Step inspection: hold > {HOLD_TAP_MS} ms (stock 1000) keeps the step on release")
    if STEP_WRAP + len(wrap) > STEP_CODE:
        raise SystemExit("step wrappers overlap the step code")
    patch(STEP_WRAP, bytes(wrap), b"\xff" * len(wrap), "Per-step: wrappers")
    step_marker = b"BENTO_MOD:per-step-condition-retrig\0"
    if STEP_CODE + len(codes) + len(step_marker) > STEP_LIMIT:
        raise SystemExit("step code overruns its slice")
    patch(STEP_CODE, codes + step_marker, b"\xff" * (len(codes) + len(step_marker)), "Per-step: UI code")
    s4.update({f"step.{k}": v for k, v in ss.items()})
    if M4_CODE + len(code4) + 20 > M4_LIMIT:
        raise SystemExit("M4 code + marker overrun")
    if M4_CODE + len(code4) + len(MARKER) > M4_LIMIT:
        raise SystemExit("M4 diag code + marker overruns its slice")
    h = asm("mov r0, r5; bl 0x08105d56; "
            "movs r1, #0xa0; mov r0, r5; bl 0x08105c44; "           # Polyphonic Key Pressure
            "uxtb.w r1, r8; mov r0, r5; bl 0x08105c5e; "             # channel (event word 3)
            "uxtb r1, r7; mov r0, r5; bl 0x08105cfe; "               # note (word 4)
            "uxtb.w r1, r11; mov r0, r5; bl 0x08105cfe; "            # pressure (word 5)
            "uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl 0x08105904; " # send on port (word 2)
            "mov r0, r5; bl 0x08105c4c; mov r1, r0; mov r0, r6; bl 0x08105b74; "
            f"b.w {M4_LOOP:#x}", M4_POLY)
    if M4_POLY + len(h) > M4_POLY_LIMIT or BASE + len(img) != M4_POLY:
        raise SystemExit("M4 poly handler block layout")
    pad = b"\xff" * (M4_POLY_LIMIT - M4_POLY)
    writes.append(dict(address=M4_POLY, size=len(pad), original_hex="", replacement_hex=pad.hex(),
                       purpose="M1 poly AT: extend image with erased (0xFF) flash for the M4 handler"))
    img += pad
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

    # --- visible version label
    if not 1 <= len(DISPLAY) <= 6 or not DISPLAY.isascii():
        raise SystemExit(f"display label {DISPLAY!r}: the boot screen shows at most 6 ASCII characters")
    patch(VERSION_STR, DISPLAY.encode().ljust(8, b"\0"), VERSION_ORIG, f"Visible version label {DISPLAY}")

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
    man = dict(label=f"{DISPLAY} (bento_enhanced v{VERSION} on stock 1.6.1)", version=VERSION, display=DISPLAY, disabled=sorted(DISABLED), layout={k: hex(v) for k, v in LAYOUT.items()}, base_sha256=STOCK_SHA, sha256=hashlib.sha256(img).hexdigest(),
               m7_symbols={k: hex(v) for k, v in s7.items()}, m4_symbols={k: hex(v) for k, v in s4.items()},
               m7_code_bytes=len(code7), m4_code_bytes=len(code4), trampoline_bytes=len(tramps), writes=writes)
    (out / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"built {man['label']} sha256={man['sha256']}")
    if skipped:
        print(f"disabled groups {sorted(DISABLED)}; {len(skipped)} stock sites left unpatched:")
        for a, why in skipped:
            print(f"  {a:#x} {why}")
    print(f"  M7 code {len(code7)} B at {M7_CODE:#x}, trampolines {len(tramps)} B; M4 code {len(code4)} B at {M4_CODE:#x}")
    print(f"  image {len(img):#x} bytes (proven max {PROVEN_MAX_SIZE:#x}); {len(writes)} writes, reversal to stock verified")
    return img, man

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    build(a.base.read_bytes(), a.out)
