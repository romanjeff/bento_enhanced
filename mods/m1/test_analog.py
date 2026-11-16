# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog oscillators on Wavetable tracks, M7 side (S6a), on stock 1.6.8's own oscillator code.

    python test_analog.py <stock bento1.bin> <m1 build dir>

The built image runs in Unicorn (Cortex-M7 with its FPU): stock's Wavetable oscillator constructor and note
start, stock's helpers (pitch, level, smoothing, mixing) called by our hook, our three trampolines behind stock's
own vtable words, and the DSP ELF at its real placement. BE4.40: an oscillator's Type, Shape, Sync and Glide reach it as
set_property messages ({0x3E, .., handle +8, key +0xC, value +0x10, index +0x14}, SET_PROPERTY's layout) through its
class's slot 8 (N3), as stock's root sends them (WT_ROOT_PROPERTY, run in Props and Voice); no M4 table. The synth area
holds the M7 header, the pool counters say the reservation is held, and the LOAD ring index moves once per block, as
stock's render does.

T1 (the oscillator): pitch (note 60 -> 261.63 Hz), saw / square / 8 % pulse by Position, level 0 is silent,
hard sync, glide and its decay, NaN guards, 64 / 256-frame blocks, a non-Analog oscillator goes to stock's
render, the sub oscillator's glide offset follows stock's note-start rewrite of that field and is taken back, a root
stock reuses for another track follows its new handle, a reset's leftover run state is not used. Golden checks: our kernel (an_render) against a
float32 model of upstream's algorithm written here (division edges, no fused multiply-add), so the tolerance
covers E2 (one reciprocal per block) and the compiler's FMA. T2 (the voice): stock's root constructor with a
bump allocator for its 10 voices, then stock's per-voice render through the root's vtable word (our trampoline,
then stock's voice code with our oscillator hook), against the same voice with the track not Analog.
Cost: instructions per voice-block (CYCCNT reads the instruction count here), with upper bounds, and the
stats lines the M4 prints (an_print). Skipped on a build without the analog group (1.6.1).
BE4.40 (docs/plan-be440-sync-identity.md): Device pins the device-shaped rig (Rig(dev=True): divider 2, 2N frames) to
stock's engine init and voice path (B1); Sync covers chunked sync at 2N and mixed sync (a Wavetable OSC1 as the master,
Track S) against WT1's real cycle starts; Golden checks an_master against the exact phase; ShapeMod is Track M (J4).
Note starts go through the oscillator class's slot 2, as stock's voice note start calls them.
P3 Q1 (docs/plan-p3-fm-bits.md 2.4, 3.3, 6.1, 6.2): Props stores Sub FM, Bit Depth and FM 1>2 and runs stock's M7 engine input
(H_INPUT_SITE) with all seven keys (OD7); P3Identity replays the identity set on this build and on the BE4.40 reference build
(BE_REF, default ~/bento/out/m1-be440: its image and its manifest's DSP symbols) and compares them byte for byte.
P3 Q2 (plan 4.7, OD11): BitDepth checks an_crush against p3_golden.py (numpy) bit for bit, and in the voice path takes the
block at Level's input (stock's SMOOTH_APPLY, called from the DSP) to prove the crush is the golden grid on the
oscillator's own signal and the only change. P3 Q2b (OD12 = (b)): the grid acts on s + dc, dc the part's own (an_dc, captured
at each an_crush call), and dc comes off after: BitDepth checks the pulse above Shape 88 stays near-transparent at 12 / 8
bits and DC-free, and a Shape ramp's parts each get their dc.
P3 Q3 (plan 4.6, OD6): ShapeLow checks the five shapes' LOW regime (sine, triangle and their morphs below the saw) against
p3_golden.py sample by sample and by spectrum, an_shape's map in the voice path, the sync roles and the Shape ramp across the
new plateaus; P3Identity covers 45..127 against BE4.40.
"""
import json, math, os, random, struct, sys, unittest
from fractions import Fraction
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
import p3_golden as G                                            # P3's independent golden model (plan 6.3; Q2: Bit Depth)
import build                                                     # Q2: check_dsp_elf, DSP_REFUSED_LIBM
BUILD = Path(sys.argv[2])
S = from_build(BUILD, Path(sys.argv[1]).read_bytes())
MAN = json.loads((BUILD / "manifest.json").read_text())
IMG = (BUILD / "bento1.bin").read_bytes()
STOCK_IMG = Path(sys.argv[1]).read_bytes()
del sys.argv[1:3]
HAVE = "analog" not in MAN.get("omitted", []) and "analog" not in MAN.get("disabled", []) and "dsp_symbols" in MAN
DSP = {k: int(v, 16) for k, v in MAN.get("dsp_symbols", {}).items()}
# P3 Q1 (6.2): the BE4.40 reference build the identity set is replayed on (its own image and DSP symbols)
REF = Path(os.environ.get("BE_REF", str(Path.home() / "bento" / "out" / "m1-be440")))
REF_OK = (REF / "bento1.bin").exists() and (REF / "manifest.json").exists()
REF_MAN = json.loads((REF / "manifest.json").read_text()) if REF_OK else {}
REF_IMG = (REF / "bento1.bin").read_bytes() if REF_OK else b""
REF_DSP = {k: int(v, 16) for k, v in REF_MAN.get("dsp_symbols", {}).items()}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack (DTCM top)  # addr-ok: harness
HEAP = 0x24010000            # harness: voices, buffers, roots  # addr-ok: harness
# synth area (synth_shared.h, typed here independently): 32 KB below the Region
SYNTH_BYTES = 0x8000
SA = S.REGION - SYNTH_BYTES if HAVE else 0
M7_MAGIC, M4_MAGIC = 0x37534542, 0x34534542
SA_M7, SA_M4, SA_STATS, SA_RUN = 0x0, 0x20, 0x40, 0x400
# our oscillator keys (docs/plan-native-props.md 3.1), typed: Type, Shape, Sync, Glide; P3 Q1: Sub FM, Bit Depth, FM 1>2
K_TYPE, K_SHAPE, K_SYNC, K_GLIDE = 0x1C0, 0x1A7, 0x1AB, 0x1B7
K_SUBFM, K_BITS, K_FM12 = 0x18B, 0x1A4, 0x132
MSUB_AT = 172               # AnOsc.msub, m12 (u16 each; P3 3.3)
SA_FMSTATS = 0x100          # SaFmStats {fm, bits, fm_skipped, fm_missed, ..} (P3 3.3; Q2 counts bits)
RUN_FMTAG = 36              # Run.fmtag (u32, P3 3.3: the run state's word 9)
PROPS_AT = 0xB4             # AnProps in the kernel area: {u8 type, shape, sync, bits; u16 glide, subfm, fm12, spare}
AN_MAGIC = 0x4F4C4E41
CYCCNT = 0xE0001004
FRAMES = 128
# The device's shape (plan-analog-fm F0 verdict; docs/plan-be440-sync-identity.md B1): stock renders Wavetable oscillators at
# 2N (the root's vt[19] 0x080A3420 returns 2), with the rate divider osc+0x108 = 2 from the engine init 0x0807F180, and
# decimates to N in the voice post chain. N = 128 (device check: the black box n_max= 256).
DEV_N, DEV_M = 128, 2
DEV_FRAMES = DEV_N * DEV_M
DEV_RATE = 48000 * DEV_M     # the oscillators' sample rate on the device


def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]


def same(tc, a, b, msg="equal"):
    """Two long sample lists (or bytes) equal; else fail naming the first difference (assertEqual's difflib on thousands of
    floats effectively never returns: review M7)."""
    a, b = list(a), list(b)
    if a == b:
        return
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    at = lambda xs: xs[i] if i < len(xs) else "(end)"
    tc.fail(f"{msg}: lengths {len(a)} / {len(b)}, first difference at {i}: {at(a)!r} / {at(b)!r}")


class F64:
    """The double-precision instructions stock's code may run (Unicorn's M7 has no f64), decoded on first use."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    def __init__(self): self.ops = {}
    @staticmethod
    def reg(name): return getattr(sys.modules["unicorn.arm_const"], f"UC_ARM_REG_{name.upper()}")
    def getd(self, u, n): return struct.unpack("<d", struct.pack("<Q", u.reg_read(self.reg(f"d{n}")) & (2 ** 64 - 1)))[0]
    def setd(self, u, n, v): u.reg_write(self.reg(f"d{n}"), struct.unpack("<Q", struct.pack("<d", v))[0])
    def step(self, u, a, size):
        op = self.ops.get(a, 0)
        if op == 0:
            i = next(self.md.disasm(bytes(u.mem_read(a, size)), a, count=1), None)
            op = self.ops[a] = (i.mnemonic, i.op_str) if i and ".f64" in i.mnemonic else None
        if not op:
            return False
        mn, args = op[0], [x.strip() for x in op[1].split(",")]
        num = lambda x: int(x[1:])
        if mn == "vmov.f64" and args[1].startswith("#"): self.setd(u, num(args[0]), float(args[1][1:]))
        elif mn == "vmov.f64": self.setd(u, num(args[0]), self.getd(u, num(args[1])))
        elif mn == "vcvt.f64.f32":
            self.setd(u, num(args[0]), struct.unpack("<f", struct.pack("<I", u.reg_read(self.reg(args[1])) & 0xFFFFFFFF))[0])
        elif mn == "vcvt.f32.f64":
            u.reg_write(self.reg(args[0]), struct.unpack("<I", struct.pack("<f", self.getd(u, num(args[1]))))[0])
        elif mn in ("vadd.f64", "vsub.f64", "vmul.f64", "vdiv.f64"):
            x, y = self.getd(u, num(args[1])), self.getd(u, num(args[2]))
            self.setd(u, num(args[0]), {"vadd.f64": x + y, "vsub.f64": x - y, "vmul.f64": x * y, "vdiv.f64": x / y}[mn])
        elif mn == "vcmpe.f64":
            raise RuntimeError(f"F64: {mn} at {a:#x} is not emulated")
        else:
            raise RuntimeError(f"F64: {mn} {op[1]} at {a:#x} is not emulated")
        u.reg_write(UC_ARM_REG_PC, (a + size) | 1)
        return True


class Rig:
    """One M7 with the built image; Analog state as the M4 would have published it. dev: the device's shape (DEV_*): every
    oscillator's rate divider 2 (as stock's engine init leaves it: Device checks it against the real init) and 2N frames."""
    def __init__(self, img=IMG, dev=False, dsp=None):
        self.dev = dev
        self.dsp = dsp or DSP                                        # the build's DSP symbols (P3Identity: BE4.40's for its image)
        self.frames = DEV_FRAMES if dev else FRAMES
        self.rate = DEV_RATE if dev else 48000
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, img)
        for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x10000000, 0x50000), (0xC5F00000, 0x100000),  # addr-ok: harness
                     (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.mem_write(SENT, b"\xfe\xe7")
        self.f64, self.n, self.pcs = F64(), 0, set()
        self.track_pcs = False
        u.hook_add(UC_HOOK_CODE, self.hook)
        u.hook_add(UC_HOOK_MEM_READ, self.cyc, begin=CYCCNT, end=CYCCNT + 3)
        self.heap = HEAP
        self.voices = {}                                             # handle -> its voices (the M4's track)
        # the reservation is held: stock's pool counters say the pool ends at the synth area
        self.w32(S.POOL_CUR_RAM, SA - 0x100000); self.w32(S.POOL_LEFT_RAM, 0x100000)
        u.mem_write(SA, struct.pack("<4I", M7_MAGIC, 1, 1, 0xC000))
        u.mem_write(SA + SA_M4, struct.pack("<4I", M4_MAGIC, 1, 1, 2))
        self.serial = 2
        self.idx = 1
        self.w32(S.LOAD_RING + S.LOAD_RING_IDX, self.idx)
        # "no wavetable loaded": the object stock's render asks (osc +0x18, its vtable slot 0x20) answers 0, as for a
        # Wavetable track with no WAV (an Analog track's state); stock's ctor leaves +0x18 for the root to fill
        self.nowav = self.alloc(0x40)
        stub = self.nowav + 0x20
        u.mem_write(stub, b"\x00\x20\x70\x47")                       # movs r0, #0; bx lr
        u.mem_write(self.nowav, struct.pack("<I", self.nowav + 8))     # object -> vtable at +8
        u.mem_write(self.nowav + 8 + 0x20, struct.pack("<I", stub | 1))

    def hook(self, u, a, size, _):
        self.n += 1
        if self.track_pcs:
            self.pcs.add(a)
        self.f64.step(u, a, size)

    def cyc(self, u, access, a, size, value, _):
        u.mem_write(CYCCNT, struct.pack("<I", self.n & 0xFFFFFFFF))

    def w32(self, a, v): self.u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
    def r32(self, a): return struct.unpack("<I", self.u.mem_read(a, 4))[0]
    def wf(self, a, v): self.u.mem_write(a, struct.pack("<f", v))
    def rf(self, a): return struct.unpack("<f", self.u.mem_read(a, 4))[0]
    def alloc(self, n):
        p = self.heap; self.heap += (n + 31) & ~31
        self.u.mem_write(p, bytes(n))
        return p

    def call(self, fn, *args, sargs=(), fargs=()):
        u = self.u
        for i, v in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, v & 0xFFFFFFFF)
        for i, v in enumerate(fargs):
            u.reg_write(getattr(sys.modules["unicorn.arm_const"], f"UC_ARM_REG_S{i}"), struct.unpack("<I", struct.pack("<f", v))[0])
        sp = STACK - 0x40
        u.mem_write(sp, b"".join(struct.pack("<I", v & 0xFFFFFFFF) for v in sargs) + bytes(0x40 - 4 * len(sargs)))
        u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        n0 = self.n
        try:
            u.emu_start(fn | 1, SENT, count=20_000_000)
        except UcError as e:
            pc = u.reg_read(UC_ARM_REG_PC)
            raise AssertionError(f"{e} at pc {pc:#x} ({bytes(u.mem_read(pc, 4)).hex()})")
        return u.reg_read(UC_ARM_REG_R0), self.n - n0

    # ---- what stock forwards (BE4.40: the oscillators' own properties, through each one's slot 8)
    def prop(self, osc, key, value, handle=0, index=0):
        """One set_property message into an oscillator's slot 8 (our N3 trampoline, then stock's for other keys)."""
        m = self.alloc(0x20)
        self.u.mem_write(m, struct.pack("<BBhIIIIhh", 0x3E, 0, 0, 0, handle, key, value & 0xFFFFFFFF, index, 0))
        slot8 = self.r32(self.r32(osc) + 0x20)
        return self.call(slot8 & ~1, osc, m)[0]
    def root_prop(self, root, key, value, index):
        """The same message through stock's Wavetable root (WT_ROOT_PROPERTY): every voice's oscillator[index]."""
        m = self.alloc(0x20)
        self.u.mem_write(m, struct.pack("<BBhIIIIhh", 0x3E, 0, 0, 0, self.r32(root + S.ROOT_HANDLE), key, value & 0xFFFFFFFF, index, 0))
        self.call(S.WT_ROOT_PROPERTY, root, m)
    def table(self, line, handle, flags, glide=0, root=None):
        """BE4.31's table line, as the properties stock now sends: both oscillators' Type (flags & 1), WT2's Sync (flags &
        2), WT1's Glide; Type first, as stock's push sends them (template order)."""
        for k, osc_i, v in ((K_TYPE, 0, flags & 1), (K_TYPE, 1, flags & 1), (K_SYNC, 1, 1 if flags & 2 else 0), (K_GLIDE, 0, glide)):
            if root:
                self.root_prop(root, k, v, osc_i)
            else:
                for vo in self.voices.get(handle, ()):
                    self.prop(self.osc(vo, osc_i), k, v, handle, osc_i)

    def block(self):
        self.idx += 1
        self.w32(S.LOAD_RING + S.LOAD_RING_IDX, self.idx)

    # ---- a Wavetable voice's two oscillators, as stock builds them (osc ctor), and their note start
    def voice(self, handle=0x1234):
        root = self.alloc(0x2F0); self.w32(root + S.ROOT_HANDLE, handle)
        v = self.alloc(0xB88)
        for o in (S.VOICE_WT1, S.VOICE_WT2):
            self.call(S.WT_OSC_CTOR, v + o)
            self.w32(v + o + 0x18, self.nowav)
            if self.dev:
                self.wf(v + o + 0x108, float(DEV_M))
        self.w32(v + 0x178, v + S.VOICE_WT1); self.w32(v + 0x17C, v + S.VOICE_WT2)   # as stock's root ctor (Voice checks it)
        self.voices.setdefault(handle, []).append(v)
        return root, v

    def note(self, v, note, oscs=(0, 1)):
        """Stock's note start through each oscillator's class slot 2 (stock's voice note start 0x0807C7F8 calls it so; our
        slot-2 hook, then stock's WT_NOTE_START)."""
        nw = self.alloc(4); self.w32(nw, note)
        for i in oscs:
            o = self.osc(v, i)
            self.call(self.r32(self.r32(o) + 8) & ~1, o, 0, nw)

    def buf(self, n, cap=None):
        b = self.alloc(0x20); d = self.alloc(4 * (cap or n))
        self.u.mem_write(b, struct.pack("<4I", cap or n, n, d, 0))
        return b, d

    def render(self, root, v, n=None, oscs=(0, 1)):
        """an_enter, then each oscillator's slot 0 through stock's vtable word (our trampoline). (samples, counts)"""
        n = n or self.frames
        out, od = self.buf(n); scr, _ = self.buf(n)
        _, c0 = self.call(self.dsp["an_enter"], root, v)
        counts, rets = [c0], []
        for i in oscs:
            osc = v + (S.VOICE_WT1, S.VOICE_WT2)[i]
            slot0 = self.r32(self.r32(osc))                      # the class vtable's slot 0
            r, c = self.call(slot0 & ~1, osc, scr, out)
            counts.append(c); rets.append(r)
        return list(struct.unpack(f"<{n}f", self.u.mem_read(od, 4 * n))), counts

    def osc(self, v, i): return v + (S.VOICE_WT1, S.VOICE_WT2)[i]
    def tabled(self, osc):
        """A real wavetable on the oscillator (a sine in every frame and mip level, rc27 f0verify's): stock's render asks
        the object at osc+0x18 (its vtable slot 0x20) for the table."""
        if not getattr(self, "tab", None):
            self.tab = 0x60000000                                        # addr-ok: harness
            self.u.mem_map(self.tab, 0x400000)
            import array
            a = array.array("f", bytes(4 * 0x100000))
            for dd, off in ((1, 0), (2, 0x80000), (4, 0xC0000), (8, 0xE0000), (16, 0xF0000), (32, 0xF8000), (64, 0xFC000)):
                L = 2048 // dd
                wave = array.array("f", [math.sin(2 * math.pi * k / L) for k in range(L)])
                for f in range(256):
                    a[off + f * L: off + (f + 1) * L] = wave
            self.u.mem_write(self.tab, a.tobytes())
            obj = self.alloc(0x60); stub = obj + 0x40
            self.u.mem_write(stub, bytes.fromhex("0148186001207047") + struct.pack("<I", self.tab))   # ldr r0,=tab; str r0,[r3]; movs r0,#1; bx lr
            self.u.mem_write(obj, struct.pack("<I", obj + 8)); self.u.mem_write(obj + 8 + 0x20, struct.pack("<I", stub | 1))
            self.tabobj = obj
        self.w32(osc + 0x18, self.tabobj)
    def shape(self, v, i, s):
        """BE4.40: Shape (0..127) for the kernel's shape s (0 saw, 0.5 square, 1 an 8 % pulse): 48 + 80 s, then 88 + 78 (s - 1/2)."""
        self.prop(self.osc(v, i), K_SHAPE, round(48 + 80 * s) if s <= 0.5 else round(88 + 78 * (s - 0.5)))
    def settings(self, v, i):
        return struct.unpack("<BBBBHHHH", self.u.mem_read(self.osc(v, i) + S.OSC_KERNEL + PROPS_AT, 12))
    def level(self, v, i, l): self.wf(self.osc(v, i) + 0xF4, l)      # Level's base source (osc+0xF0, amount 0.91)
    def state(self, v, i):
        a = self.osc(v, i) + S.OSC_KERNEL
        magic, phase, delayed = struct.unpack("<Iff", self.u.mem_read(a, 12))
        note, glide, sub, written = struct.unpack("<Ifff", self.u.mem_read(a + 140, 16))
        return dict(magic=magic, phase=phase, delayed=delayed, events=bytes(self.u.mem_read(a + 12, 128)),
                    note=note, glide=glide, sub=sub, written=written)


# ---- an independent model of upstream's kernel (bento-playground analog.h), float32 by rounding every step
def model(inc, shape, n, phase=0.0, delayed=0.0, sync=None, master=False):
    """(samples, phase, delayed, events): AnalogKernel::Render, one operation at a time in float32."""
    inc = 0.0 if not (inc > 0) else min(inc, f32(0.45)); inc = f32(inc)
    shape = 0.0 if not (shape >= 0) else min(shape, 1.0)
    blend = f32(min(1.0, f32(2 * shape)))
    width = f32(0.5 - f32(f32(shape - 0.5) * f32(0.84))) if shape > 0.5 else 0.5
    dc = f32(f32(2 * width) - 1)
    def wave(p):
        return f32(f32(f32(f32(2 * p) - 1) * f32(1 - blend)) + f32(f32((1.0 if p < width else -1.0) - dc) * blend))
    def event(fr, jump, pc):
        t = f32(1 - fr)
        pc[0] = f32(pc[0] + f32(f32(f32(jump * 0.5) * t) * t)); pc[1] = f32(pc[1] - f32(f32(f32(jump * 0.5) * fr) * fr))
    p, out, ev = f32(phase), [], [255] * min(n, 128)
    delayed = f32(delayed)
    for i in range(n):
        pc = [0.0, 0.0]
        def advance(begin, length, rec):
            nonlocal p
            if length <= 0: return
            end = f32(p + f32(inc * length))
            if p < width and end >= width: event(f32(begin + f32(f32(width - p) / inc)), f32(-2 * blend), pc)
            if end >= 1:
                wrap = f32(begin + f32(f32(1 - p) / inc))
                event(wrap, f32(-2 + f32(4 * blend)), pc)
                if rec and i < 128: ev[i] = max(0, min(254, int(f32(f32(wrap * 254) + 0.5))))
                end = f32(end - 1)
                if end >= width: event(f32(wrap + f32(width / inc)), f32(-2 * blend), pc)
            p = end
        e = sync[i] if sync is not None and i < 128 else 255
        if e != 255 and inc > 0:
            edge = f32(e * f32(1 / 254))
            advance(0.0, edge, False)
            jump = f32(wave(0.0) - wave(p)); event(edge, jump, pc); p = 0.0
            advance(edge, f32(1 - edge), False)
        else:
            advance(0.0, 1.0, master)
        out.append(f32(delayed + pc[0]))
        delayed = f32(wave(p) + pc[1])
    return out, p, delayed, ev


def period(xs):
    ups = [i for i in range(1, len(xs)) if xs[i - 1] > 0 >= xs[i]]   # falling edges (the saw's wrap)
    return (ups[-1] - ups[0]) / (len(ups) - 1) if len(ups) > 1 else None


def ups(xs, t0=0):
    """Upward zero crossings (interpolated): a sine table's cycle starts."""
    return [t0 + i - 1 + (-xs[i - 1]) / (xs[i] - xs[i - 1]) for i in range(1, len(xs)) if xs[i - 1] < 0 <= xs[i] and xs[i] != xs[i - 1]]


AN_RENDER_SLAVE = 2


class Mixed:
    """BE4.40 mixed sync in the device's shape (after the sync designer's wf/sync e2e.py): WT1 Wavetable (stock's render, a
    sine table), WT2 Analog with Sync, a fifth up. WT1's pitch parameter gets a source of its own (vibrato and slides on
    WT1 only). Note starts through slot 2, renders through slot 0 after an_enter, as stock's voice. Every slave event is
    taken at an_render's entry (its frame in the block + e / 254), so all of a block's master wraps are seen."""
    def __init__(self, sync=1, table=True, shape=48, stock=False, type2=1, frames=DEV_FRAMES, r=None, handle=0x51):
        self.r = r = r or Rig(img=STOCK_IMG if stock else IMG, dev=True)
        self.root, self.v = r.voice(handle)
        self.o1, self.o2 = r.osc(self.v, 0), r.osc(self.v, 1)
        if table:
            r.tabled(self.o1)
        r.level(self.v, 0, 1.0); r.level(self.v, 1, 1.0)
        self.src = r.alloc(0x10); p = self.o1 + 0x70
        r.w32(p + 0x30, 1); r.u.mem_write(p + 0x28, bytes(5)); r.w32(p, self.src); r.wf(p + 4, 1.0)
        self.stock, self.handle = stock, handle
        if not stock and type2:
            self.prop(K_TYPE, 1); self.prop(K_SHAPE, shape); self.prop(K_SYNC, sync)
        r.w32(self.o2 + 0x0C, 7000)                              # WT2 a fifth up
        self.fr = frames
        self.b1, self.b2, self.sc = r.buf(frames), r.buf(frames), r.buf(frames)
        self.t, self.x1, self.x2, self.wr, self.counts, self.starts = 0, [], [], [], [], []
        if not stock:
            if not hasattr(r, "slaves"):
                r.slaves = {}
                r.u.hook_add(UC_HOOK_CODE, self._slave, begin=r.dsp["an_render"], end=r.dsp["an_render"], user_data=r)
            r.slaves[self.o2 + S.OSC_KERNEL] = self
    @staticmethod
    def _slave(u, a, size, r):
        me = r.slaves.get(u.reg_read(UC_ARM_REG_R0))
        if not me or struct.unpack("<i", u.mem_read(u.reg_read(UC_ARM_REG_SP), 4))[0] != AN_RENDER_SLAVE:
            return
        off = (u.reg_read(UC_ARM_REG_R1) - me.sc[1]) // 4
        ev = u.mem_read(u.reg_read(UC_ARM_REG_R3), u.reg_read(UC_ARM_REG_R2))
        me.wr += [me.t + off + i + e / 254 for i, e in enumerate(ev) if e != 255]
        me.starts += [me.t + off for i, e in enumerate(ev) if i == 0 and e == 0]
    def prop(self, key, value): self.r.prop(self.o2, key, value, self.handle, 1)
    def pitch(self, pm): self.r.wf(self.src + 4, pm)
    def note(self, n): self.r.note(self.v, n)
    def mphase(self):
        hi, lo = struct.unpack("<2I", self.r.u.mem_read(self.o2 + S.OSC_KERNEL + 156, 8))   # AnOsc mphase, mphase_lo
        return (hi << 32) | lo
    def block(self, pm=None):
        r = self.r
        if pm is not None:
            self.pitch(pm)
        if not self.stock:
            r.call(r.dsp["an_enter"], self.root, self.v)
        cs = []
        for o, (b, d) in ((self.o1, self.b1), (self.o2, self.b2)):
            r.u.mem_write(d, bytes(4 * self.fr)); r.u.mem_write(b, struct.pack("<2I", self.fr, self.fr))
            _, c = r.call(r.r32(r.r32(o)) & ~1, o, self.sc[0], b); cs.append(c)
        self.counts.append(cs)
        self.x1 += struct.unpack(f"<{self.fr}f", r.u.mem_read(self.b1[1], 4 * self.fr))
        self.x2 += struct.unpack(f"<{self.fr}f", r.u.mem_read(self.b2[1], 4 * self.fr))
        self.t += self.fr; r.block()
    def errors(self, t_from=0, t_to=1e18):
        """(WT1 cycle starts in the window, master wraps in it, worst |wrap - cycle start| in oscillator samples)"""
        cyc = [c for c in ups(self.x1) if t_from < c < t_to]
        wr = sorted(w for w in self.wr if t_from < w < t_to)
        worst = max((min((abs(c - w) for w in wr), default=1e9) for c in cyc), default=1e9)
        return cyc, wr, worst
    def aligned(self, tc, t_from=0, t_to=1e18, tol=0.01, msg=""):
        cyc, wr, worst = self.errors(t_from, t_to)
        tc.assertGreater(len(cyc), 2, f"{msg}: WT1 cycles in the window")
        tc.assertEqual(len(wr), len(cyc), f"{msg}: one master wrap per WT1 cycle")
        tc.assertLess(worst, tol, f"{msg}: worst |wrap - WT1 cycle start| {worst:.4f} smp")
        return worst


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Hooks(unittest.TestCase):
    def test_stock_vtable_words_point_at_our_trampolines(self):
        w = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        self.assertEqual(w(S.WT_VOICE_VT_SLOT), DSP["tramp_voice"] | 1)
        self.assertEqual(w(S.WT_OSC_VT_SLOT), DSP["tramp_osc"] | 1)
        self.assertTrue(S.M7_DSP <= DSP["an_render"] < S.M7_DSP_LIMIT)
        r = Rig(); root, v = r.voice()
        self.assertEqual(r.r32(v + S.VOICE_WT1) & ~1, S.WT_OSC_VT_SLOT, "stock's ctor installs the class whose slot 0 we took")

    def test_slot_2_runs_an_note_then_stocks_note_start(self):
        """S1: the class's slot 2 word is our trampoline; stock's note start still runs after an_note (the kernel tail reset:
        frac 0, idx 0 with WTOscReset on, C8 -1, D 1, len 2048, fresh 1; the note at +0x10, -1 at +0x24), the same bytes
        as stock's for a Wavetable oscillator next to a synced Analog one."""
        w = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        self.assertEqual(w(S.WT_OSC_NOTE_SLOT), DSP["tramp_note"] | 1)
        self.assertEqual(S.WT_OSC_NOTE_SLOT, S.WT_OSC_VT_SLOT + 8)
        def run(stock):
            m = Mixed(stock=stock); m.r.u.mem_write(m.o1 + 0x1CC, bytes(range(0x24)))
            m.note(67)
            return bytes(m.r.u.mem_read(m.o1, 0x1F0)), m
        a, m = run(False)
        b, _ = run(True)
        self.assertEqual(a, b, "WT1 after the note start")
        self.assertEqual(struct.unpack_from("<fIiIII", a, 0x1CC), (0.0, 0, -1, 0, 1, 2048))
        self.assertEqual((m.r.u.mem_read(m.o2 + S.OSC_KERNEL + 164, 2)[0], m.mphase()), (1, 0), "mrestart, phase 0")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Oscillator(unittest.TestCase):
    def setUp(self):
        self.r = Rig()
        self.root, self.v = self.r.voice(0x1234)
        self.r.table(0, 0x1234, 1)
        self.r.note(self.v, 60)

    def play(self, blocks, n=FRAMES, oscs=(0,)):
        xs, last = [], None
        for _ in range(blocks):
            out, last = self.r.render(self.root, self.v, n, oscs)
            xs += out; self.r.block()
        return xs, last

    def test_note_60_plays_261_6_hz(self):
        self.r.level(self.v, 1, 0.0)
        xs, _ = self.play(24, oscs=(0, 1))
        hz = 48000 / period(xs)
        self.assertAlmostEqual(hz, 440 * 2 ** (-9 / 12), delta=0.5, msg=f"{hz:.2f} Hz")
        self.assertGreater(max(map(abs, xs[256:])), 0.1, "audible")
        self.assertEqual(self.r.state(self.v, 0)["magic"], AN_MAGIC)

    def test_an_octave_up_doubles_the_frequency(self):
        self.r.note(self.v, 72)
        xs, _ = self.play(16)
        self.assertAlmostEqual(48000 / period(xs), 440 * 2 ** (3 / 12), delta=1.0)

    def test_shapes_saw_square_pulse(self):
        """Position 0 = saw (a ramp: about half the samples above 0), 0.5 = square (50 % high), 1 = 8 % pulse."""
        for shape, high in ((0.5, 0.50), (1.0, 0.08)):
            with self.subTest(shape=shape):
                r = Rig(); root, v = r.voice(0x99); r.table(0, 0x99, 1); r.note(v, 48); r.shape(v, 0, shape)
                xs = []
                for _ in range(24):
                    out, _ = r.render(root, v, oscs=(0,)); xs += out; r.block()
                body = xs[512:]
                mid = (max(body) + min(body)) / 2
                frac = sum(1 for x in body if x > mid) / len(body)
                self.assertAlmostEqual(frac, high, delta=0.02)
        xs, _ = self.play(24)
        body = xs[512:]
        steps = [body[i] - body[i - 1] for i in range(1, len(body))]
        rising = sum(1 for d in steps if d > 0) / len(steps)
        self.assertGreater(rising, 0.9, "a saw rises almost everywhere (only the wraps fall)")

    def test_level_0_is_silent_and_skips_the_kernel(self):
        self.r.level(self.v, 0, 0.0); self.r.level(self.v, 1, 0.0)
        for _ in range(4):
            out, counts = self.r.render(self.root, self.v, oscs=(0, 1)); self.r.block()
        self.assertEqual(out, [0.0] * FRAMES)
        self.assertLess(max(counts[1:]), 1500, f"no kernel run for a silent oscillator: {counts}")

    def test_glide_starts_from_the_tracks_last_note_and_decays(self):
        r = Rig(); root, v = r.voice(0x77); r.table(0, 0x77, 1, glide=500); r.note(v, 48)
        r.render(root, v); r.block()
        r.note(v, 60)
        r.render(root, v); r.block()
        g0 = r.state(v, 0)["glide"]
        # 12 semitones below: -1 octave to slide, minus one block of decay: 0.001 ^ (128 / (1 s * 48000))
        coef = 0.001 ** (FRAMES / (500 * 0.002 * 48000))      # glide 0..1000 = 0..2 s
        self.assertAlmostEqual(g0, -1.0 * coef, delta=0.002)
        for _ in range(9):
            r.render(root, v); r.block()
        self.assertAlmostEqual(r.state(v, 0)["glide"], -1.0 * coef ** 10, delta=0.003)
        sub = r.rf(v + S.VOICE_SUB + 4)
        self.assertEqual(sub, r.state(v, 0)["sub"], "the sub oscillator got what the voice entered with")
        self.assertLess(sub, 0.0)

    def test_glide_takes_the_set_time_on_the_device(self):
        """P3 Q0 (OD1, plan 6.4; F0 bug 2): in the device's shape (2N = 256 frames per block in a 96 kHz domain, divider 2
        at osc+0x108) Glide 500 (1 s) is down to 0.001 of the interval after 1 s of output, +- 1 block: block k = 375 of
        128 output frames (k * 2N / 96 kHz). BE4.40 counted the 2N frames at 48 kHz and got there at ~188 (half the time)."""
        r = Rig(dev=True); root, v = r.voice(0x77); r.table(0, 0x77, 1, glide=500); r.note(v, 48)
        self.assertEqual(r.rf(r.osc(v, 0) + 0x108), float(DEV_M), "the device's divider")
        r.render(root, v, oscs=(0,)); r.block()
        r.note(v, 60)
        o1 = r.osc(v, 0); slot0 = r.r32(r.r32(o1)) & ~1
        (out, _), (scr, _) = r.buf(DEV_FRAMES), r.buf(DEV_FRAMES)   # render()'s calls, its buffers reused (~400 blocks)
        k, g = 0, -1.0
        while k < 400 and abs(g) > 0.001:
            r.call(r.dsp["an_enter"], root, v); r.call(slot0, o1, scr, out); r.block(); k += 1
            g = r.state(v, 0)["glide"]
        exact = 0.001 ** (1 / (500 * 0.002 * DEV_RATE))       # per 96 kHz sample: 0.001 after 1 s
        self.assertAlmostEqual(k * DEV_FRAMES / DEV_RATE, 1.0, delta=DEV_FRAMES / DEV_RATE,
                               msg=f"reached 0.001 of the interval after {k} blocks (1 s = 375)")
        self.assertLess(abs(math.log(abs(g)) / k - DEV_FRAMES * math.log(exact)), 5e-5,
                        "one block's decay is 0.001 ^ (2N / (s * 48000 * 2)): ln of it to 5e-5, stock's exp2 table near 2^0 "
                        "(measured 1.1e-5..2.6e-5 at Glide 50..1000)")

    def test_glide_coef_follows_the_block_length(self):
        """P3 Q0 (review): the root's coef cache (AN_E5) is keyed on (glide, n). One root, Glide 500 held, the block
        length changed mid-slide (128, 64, 32, back to 128): each block decays by 0.001 ^ (n / (1 s * 48000)) for its own
        n (ln to 5e-5, stock's exp2 table). A key without n reuses the 128-frame coef. The divider is not in the key: a
        root's vt[19] sets both its 2N and its divider (plan Q0 as built, I)."""
        r = Rig(); root, v = r.voice(0x77); r.table(0, 0x77, 1, glide=500); r.note(v, 48)
        r.render(root, v, oscs=(0,)); r.block()
        r.note(v, 60)
        g = [-1.0]
        for n in (128, 128, 64, 64, 32, 128):
            r.render(root, v, n, oscs=(0,)); r.block()
            g.append(r.state(v, 0)["glide"])
            with self.subTest(block=len(g) - 1, n=n):
                self.assertLess(abs(math.log(g[-1] / g[-2]) - n * math.log(0.001) / 48000), 5e-5,
                                f"block of {n}: decay {g[-1] / g[-2]}")

    def test_no_glide_without_glide_time(self):
        self.r.render(self.root, self.v); self.r.block()
        self.r.note(self.v, 67)
        self.r.render(self.root, self.v); self.r.block()
        self.assertEqual(self.r.state(self.v, 0)["glide"], 0.0)

    def test_leaving_analog_gives_the_sub_its_offset_back(self):
        r = Rig(); root, v = r.voice(0x55); r.table(0, 0x55, 1, glide=1000); r.note(v, 48)
        r.render(root, v); r.block(); r.note(v, 60); r.render(root, v); r.block()
        self.assertNotEqual(r.rf(v + S.VOICE_SUB + 4), 0.0)
        r.table(0, 0x55, 0)                                     # the M4: not Analog any more
        r.render(root, v, oscs=()); r.block()
        self.assertEqual(r.rf(v + S.VOICE_SUB + 4), 0.0, "plan fix 3: no stuck detune on the sub")
        for i in (0, 1):                                        # review N1: the native kernel's history is zeros, not ours
            st = r.state(v, i)
            self.assertEqual((st["magic"], st["phase"], st["events"]), (0, 0.0, bytes(128)), f"WT{i + 1} state cleared")

    def test_stock_note_start_mid_glide_leaves_no_detune(self):
        """Review S1: stock's voice note start rewrites every oscillator's +0x04, the sub's included (0x0807C7F8..C80C,
        vstr s16, [osc, #4]). A note mid-glide then must not leave our old offset in the sub when the glide ends."""
        r = Rig(); root, v = r.voice(0x77); r.table(0, 0x77, 1, glide=200); r.note(v, 48)
        r.render(root, v); r.block(); r.note(v, 60)
        for _ in range(5):
            r.render(root, v); r.block()
        self.assertLess(r.rf(v + S.VOICE_SUB + 4), -0.3, "mid-glide: the sub carries the slide")
        base = 0.25                                             # stock's note start: the sub's octave setting, say +3 semitones
        r.wf(v + S.VOICE_SUB + 4, base); r.note(v, 67)
        r.render(root, v); r.block()
        self.assertAlmostEqual(r.rf(v + S.VOICE_SUB + 4) - base, (60 - 67) / 12, delta=1e-6,
                               msg="the new base plus the new slide (from 60, entered before this block's decay), nothing of the old one")
        for _ in range(400):
            r.render(root, v); r.block()
            if r.state(v, 0)["glide"] == 0.0: break
        self.assertEqual(r.state(v, 0)["glide"], 0.0, "the glide ended")
        r.render(root, v); r.block()                            # the sub follows at the next block's entry
        self.assertAlmostEqual(r.rf(v + S.VOICE_SUB + 4), base, delta=1e-6, msg="the sub is back on stock's base")

    def test_a_root_stock_reuses_starts_its_glide_memory_again(self):
        """Review B1: stock frees a root by setting its key to 0xFFFFFF00 (0x08059C28) and gives a new track the first
        free root with its handle (0x08059C66). BE4.40: Analog belongs to the oscillators (their properties), so only the
        root's glide line (the track's last note) is the root's, and it starts again for the new handle."""
        r = Rig(); root, v = r.voice(0x10); r.table(0, 0x10, 1, glide=500); r.note(v, 48)
        r.render(root, v); r.block(); r.render(root, v); r.block()
        misses = r.r32(SA + SA_STATS + 52)
        r.w32(root + S.ROOT_HANDLE, 0x20)
        r.note(v, 60)
        r.render(root, v); r.block()
        self.assertEqual(r.r32(SA + SA_STATS + 52), misses + 1, "the line refilled for the new handle")
        self.assertEqual(r.state(v, 0)["glide"], 0.0, "no slide from the old track's note")

    def test_area_not_ours_is_silent_and_keeps_the_state(self):
        """Review N2 / BE4.40: the header and run state survive a reset in SDRAM; with the pool stock's again an Analog
        oscillator renders nothing for that block (not stock's render: that would take our state for its history) and its
        state and settings stay; a Wavetable oscillator is stock's as always."""
        r = Rig(); root, v = r.voice(0x21); r.table(0, 0x21, 1); r.shape(v, 0, 0.5); r.note(v, 60)
        r.render(root, v, oscs=(0,)); r.block()
        before = bytes(r.u.mem_read(r.osc(v, 0) + S.OSC_KERNEL, 0xC0))
        r.w32(S.POOL_LEFT_RAM, 0x100000 + 0xC000)               # stock's startup restored the counters: not taken
        r.track_pcs = True
        out, _ = r.render(root, v, oscs=(0,))
        self.assertNotIn(S.WT_OSC_RENDER, r.pcs, "not stock's render")
        self.assertEqual(out, [0.0] * FRAMES)
        self.assertEqual(bytes(r.u.mem_read(r.osc(v, 0) + S.OSC_KERNEL, 0xC0)), before, "state and settings kept")
        self.assertEqual(r.settings(v, 0)[:2], (1, 88))

    def test_one_glide_line_per_root(self):
        r = Rig(); a, va = r.voice(0x10); b, vb = r.voice(0x20)
        r.table(0, 0x10, 1); r.table(1, 0x20, 1); r.note(va, 60); r.note(vb, 64)
        for _ in range(4):
            r.render(a, va); r.render(b, vb); r.block()
        self.assertEqual(r.r32(SA + SA_STATS + 52), 2, "one line per root (upstream's one-entry cache missed on every lookup)")

    def test_hard_sync_resets_the_slave_at_the_masters_wraps(self):
        """Sync on (device rig, 2N): WT2 (a fifth up) restarts at each of WT1's wraps, so the voice repeats at WT1's period;
        both event arrays (WT2's for frames 0..127, WT1's for 128..255) carry wraps."""
        r = Rig(dev=True); root, v = r.voice(0x66); r.table(0, 0x66, 3); r.note(v, 48)
        r.level(v, 0, 0.0)                                      # hear WT2 only; WT1 still tracks its phase (E3)
        w2 = r.osc(v, 1)
        r.w32(w2 + 0x0C, 7000)                                  # WT2 detune +7 semitones (1/1000 semitone units)
        xs, wraps = [], [0, 0]
        for _ in range(16):
            out, _ = r.render(root, v, oscs=(0, 1)); xs += out; r.block()
            for c, i in ((0, 1), (1, 0)):
                wraps[c] += sum(1 for e in r.state(v, i)["events"] if e != 255)
        self.assertGreaterEqual(min(wraps), 2, f"wraps in both halves of the block (130.8 Hz: one per 734 frames): {wraps}")
        p = DEV_RATE / 130.81                                   # WT1: note 48
        self.assertLess(lag_err(xs[1024:], p), 0.05, "periodic at the master's period (a free fifth would not be)")
        r2 = Rig(dev=True); root2, v2 = r2.voice(0x67); r2.table(0, 0x67, 1); r2.note(v2, 48); r2.level(v2, 0, 0.0)
        r2.w32(r2.osc(v2, 1) + 0x0C, 7000)
        ys = []
        for _ in range(16):
            out, _ = r2.render(root2, v2, oscs=(0, 1)); ys += out; r2.block()
        self.assertGreater(lag_err(ys[1024:], p), 0.2, "control: without sync the fifth is not periodic at WT1's period")

    def test_long_blocks_render_without_sync_and_are_counted(self):
        """Both Analog: up to AN_SYNC_FRAMES (2N) syncs; a longer block renders free and is counted. Mixed sync has no limit."""
        r = Rig(dev=True); root, v = r.voice(0x44); r.table(0, 0x44, 3); r.note(v, 60)
        out, _ = r.render(root, v, n=512, oscs=(0, 1)); r.block()
        self.assertGreater(max(map(abs, out)), 0.1)
        self.assertEqual(stat(r, SYNC_SKIPPED), 2, "sync_skipped: both oscillators of the 512-frame block")
        self.assertEqual(stat(r, N_MAX), 512, "n_max")
        for n in (64, 256):
            out, _ = r.render(root, v, n=n, oscs=(0, 1)); r.block()
            self.assertEqual(stat(r, SYNC_SKIPPED), 2, f"a {n}-frame block syncs")
        m = Mixed(frames=512); m.note(60)
        for _ in range(4): m.block()
        self.assertEqual(stat(m.r, SYNC_SKIPPED), 0)
        m.aligned(self, 1)

    def test_a_non_analog_oscillator_goes_to_stocks_render(self):
        r = Rig(); root, v = r.voice(0x33); r.note(v, 60)       # no Type: not Analog
        r.track_pcs = True
        out, _ = r.render(root, v, oscs=(0,))
        self.assertIn(S.WT_OSC_RENDER, r.pcs, "stock's render ran")
        self.assertEqual(out, [0.0] * FRAMES, "no table loaded: stock renders nothing")
        self.assertNotEqual(r.state(v, 0)["magic"], AN_MAGIC, "the kernel state is not ours")

def pool_alloc(r, at=0xC5F10000):                           # addr-ok: harness
    """A bump allocator behind stock's named pool allocator (the root ctor's 10 voices)."""
    pool = [at]
    def alloc(uc, a, size, _):
        n = uc.reg_read(UC_ARM_REG_R2); p = pool[0]; pool[0] += (n + 31) & ~31
        uc.mem_write(p, bytes(n)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    r.u.hook_add(UC_HOOK_CODE, alloc, begin=S.NAMED_ALLOC, end=S.NAMED_ALLOC)


def stat(r, i): return r.r32(SA + SA_STATS + 4 * i)
N_MAX, SYNC_SKIPPED = 5, 6


def lag_err(xs, period):
    """Mean |x[i] - x[i + period]|: small when xs repeats at that period (rounded to whole samples)."""
    lag = round(period)
    return sum(abs(xs[i] - xs[i + lag]) for i in range(len(xs) - lag)) / (len(xs) - lag)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Device(unittest.TestCase):
    """B1 (docs/plan-be440-sync-identity.md): the device's shape, 2N frames per oscillator render with the rate divider 2.
    Every sync test runs in it (Rig(dev=True)); this class pins the rig to stock's own engine init and voice path."""
    def test_the_rig_is_the_devices(self):
        """Stock's root ctor then engine init: every oscillator's divider is 2.0 (what Rig(dev=True) writes), and the root's
        vt[19] (the oversampling factor the render sizes its buffers by) returns DEV_M."""
        r = Rig(); pool_alloc(r)
        root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
        for i in range(10):
            v = r.r32(root + 0x14 + 4 * i)
            self.assertEqual((r.rf(v + S.VOICE_WT1 + 0x108), r.rf(v + S.VOICE_WT2 + 0x108)), (float(DEV_M), float(DEV_M)))
        self.assertEqual(r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0], DEV_M)
        d = Rig(dev=True); _, v = d.voice(0x10)
        self.assertEqual(d.rf(v + S.VOICE_WT2 + 0x108), float(DEV_M))

    def test_both_analog_sync_engages_at_2n(self):
        """B1: at n = 2N the slave restarts at the master's wraps (BE4.40 6adc04f: sync_skipped on every block, free-running)."""
        r = Rig(dev=True); root, v = r.voice(0x66); r.table(0, 0x66, 3); r.note(v, 48)
        r.level(v, 0, 0.0)                                      # hear WT2 only; WT1 still tracks its phase (E3)
        r.w32(r.osc(v, 1) + 0x0C, 7000)                         # WT2 a fifth up
        xs = []
        for _ in range(24):
            out, _ = r.render(root, v, oscs=(0, 1)); xs += out; r.block()
        self.assertEqual(stat(r, N_MAX), DEV_FRAMES)
        self.assertEqual(stat(r, SYNC_SKIPPED), 0, "sync engaged on every block")
        p = DEV_RATE / 130.8128
        self.assertLess(lag_err(xs[1024:], p), 0.05, "periodic at the master's period")

    def test_the_voice_on_the_devices_path(self):
        """Stock's whole voice as the device runs it: root ctor, engine init, buffers sized N x vt[19], the voice render (our
        slot-18 trampoline, stock's voice code with our oscillator hook, the post chain's 2x decimation to N): an Analog saw
        at note 60 is 261.63 Hz at 48 kHz, and both-Analog sync engages."""
        r = Rig(); pool_alloc(r)
        root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
        r.w32(root + S.ROOT_HANDLE, 0x4343)
        v = r.r32(root + 0x14)
        for o in (S.VOICE_WT1, S.VOICE_WT2):
            r.w32(v + o + 0x18, r.nowav)
        r.voices[0x4343] = [v]
        for flags, want in ((1, 261.63), (3, 261.63)):
            with self.subTest(flags=flags):
                r.table(0, 0x4343, flags, root=root); r.note(v, 60)
                if flags & 2:
                    r.level(v, 0, 0.0); r.w32(v + S.VOICE_WT2 + 0x0C, 7000)
                m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
                acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
                slot18 = r.r32(r.r32(root) + 0x48)
                skipped0, outs = stat(r, SYNC_SKIPPED), []
                for _ in range(40):
                    r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096))
                    r.w32(out + 4, DEV_N)
                    r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
                    outs += struct.unpack(f"<{DEV_N}f", r.u.mem_read(od, 4 * DEV_N)); r.block()
                self.assertEqual(r.r32(out + 4), DEV_N, "decimated to N")
                body = outs[1280:]
                ups = [i + body[i] / (body[i] - body[i + 1]) for i in range(len(body) - 1) if body[i] < 0 <= body[i + 1]]
                if flags == 1:
                    hz = 48000 * (len(ups) - 1) / (ups[-1] - ups[0])
                    self.assertAlmostEqual(hz, want, delta=0.5, msg=f"{hz:.2f} Hz")
                else:
                    self.assertEqual(stat(r, SYNC_SKIPPED), skipped0, "sync engaged")
                    self.assertLess(lag_err(body, 48000 / want), 0.05 * max(map(abs, body)), "periodic at the master's period")
                self.assertEqual(stat(r, N_MAX), DEV_N * m)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Sync(unittest.TestCase):
    """Track S (docs/plan-be440-sync-identity.md): sync in chunks at 2N, and a Wavetable OSC1 as the master of an Analog OSC2
    (J1). Every case in the device's shape. Wraps and WT1 cycle starts are in 96 kHz oscillator samples."""
    def test_chunked_2n_equals_two_n_blocks(self):
        """B1 fix: a 256-frame block renders as two 128-frame ones (the same calls in chunks): both Analog and mixed, WT1 and
        WT2 bit for bit, and the master's phase copy the same."""
        def both(fr):
            r = Rig(dev=True); root, v = r.voice(0x66); r.table(0, 0x66, 3); r.note(v, 48); r.shape(v, 1, 0.3)
            r.w32(r.osc(v, 1) + 0x0C, 7000)
            xs = []
            for _ in range(DEV_FRAMES * 12 // fr):
                out, _ = r.render(root, v, n=fr, oscs=(0, 1)); xs += out; r.block()
            return xs, stat(r, SYNC_SKIPPED)
        a, sa = both(DEV_FRAMES); b, sb = both(DEV_FRAMES // 2)
        self.assertEqual((sa, sb), (0, 0))
        self.assertLess(max(abs(x - y) for x, y in zip(a, b)), 1e-6, "both Analog (the level smoother is set per call)")
        ma, mb = Mixed(), Mixed(frames=DEV_FRAMES // 2)
        for m in (ma, mb):
            m.note(60)
            for _ in range(DEV_FRAMES * 12 // m.fr): m.block((1 / 12) * math.sin((m.t // DEV_FRAMES) / 3))   # per 2N
        self.assertGreater(len(ma.wr), 5)
        same(self, ma.wr, mb.wr, "mixed: the same wraps")
        self.assertEqual(ma.mphase(), mb.mphase(), "mixed: the same phase")
        for a_, b_, what in ((ma.x1, mb.x1, "stock's WT1 (its own per-call smoother)"), (ma.x2, mb.x2, "WT2")):
            self.assertLess(max(abs(x - y) for x, y in zip(a_, b_)), 1e-6, what)

    def test_a_wavetable_osc1_is_the_master_across_the_range(self):
        """Every WT1 cycle start has its master wrap within 0.01 smp, notes 24..108, WT1's octave -2..+2 and a fine tune."""
        for note, octv, fine in ((24, 0, 0), (36, 0, 0), (48, 0, 0), (60, 0, 0), (72, 0, 0), (84, 0, 0), (96, 0, 0), (108, 0, 0),
                                 (60, -2, 0), (60, -1, 0), (60, 1, 0), (60, 2, 0), (60, 0, 3333), (61, 0, -12000)):
            with self.subTest(note=note, octave=octv, fine=fine):
                m = Mixed(); m.r.wf(m.o1 + 4, float(octv)); m.r.w32(m.o1 + 0x0C, fine & 0xFFFFFFFF); m.note(note)
                for _ in range(40): m.block()
                m.aligned(self, 1, msg=f"note {note}")
                self.assertEqual(stat(m.r, SYNC_SKIPPED), 0)

    def test_the_master_follows_wt1s_pitch_modulation(self):
        """Vibrato (+-1 semitone) and a slide (WT1's pitch from -1 octave to 0, as a glide would) on WT1's own pitch."""
        m = Mixed(); m.note(60)
        for b in range(150): m.block((1 / 12) * math.sin(2 * math.pi * b / 50))
        m.aligned(self, 1, msg="vibrato")
        m = Mixed(); m.note(60)
        for b in range(120): m.block(-1.0 * max(0.0, 1 - b / 80))
        m.aligned(self, 1, msg="slide")

    def test_the_master_rate_is_stocks(self):
        """The copy advances by exactly n x (stock's WT1 rate +0x1E8) / 2048 per block (64-bit), across note x fine x octave x
        pitch modulation: stock's own formula on WT1's fields, bit for bit (a firmware change to it would show here)."""
        m = Mixed(); m.note(60)
        m.block()
        bad, seeded, checked = [], 0, 0
        for note in (0, 24, 48, 60, 69, 96, 120, 127):
            for fine in (0, 1, -12000, 49999):
                for octv in (-2.0, 0.0, 1.5):
                    for pm in (0.0, 1 / 12, -0.7, 2.0):
                        m.r.w32(m.o1 + 0x0C, fine & 0xFFFFFFFF); m.r.wf(m.o1 + 4, octv)
                        m.r.w32(m.o1 + 0x10, note)               # (stock's note start writes +0x10; no restart here)
                        kern = lambda: (m.r.r32(m.o1 + 0x1D0), m.r.rf(m.o1 + 0x1CC), m.r.r32(m.o1 + 0x1E0))
                        p0 = m.mphase(); live = m.r.u.mem_read(m.o2 + S.OSC_KERNEL + 165, 1)[0]; k0 = kern(); m.block(pm)
                        rate = m.r.rf(m.o1 + 0x1E8)
                        q = int(Fraction(f32(min(rate / 2048, f32(0.45)))) * 2 ** 64)
                        if live == 3 and kern() != k0 and kern()[0] < kern()[2]:
                            # stock's kernel was still the last block (far above its note start's mip, rate / D over
                            # about 32 here, it steps whole table cycles per block: M) and moves now: the copy is seeded
                            # from it, so this block ends at stock's phase rather than the formula's (unless stock's index
                            # is out of its table, as it gets there: M, idx -69; seed() then keeps the copy)
                            idx, frac, ln = kern()
                            seeded += 1
                            if abs((m.mphase() / 2 ** 64 - (idx + frac) / ln + 0.5) % 1 - 0.5) > 1e-6:
                                bad.append(("seed", note, fine, octv, pm))
                        elif (m.mphase() - p0) % 2 ** 64 != (m.fr * q) % 2 ** 64:
                            bad.append((note, fine, octv, pm))
                        else:
                            checked += 1
        self.assertEqual(bad, [], "the copy's rate is stock's")
        self.assertGreater(checked, 300, f"the formula checked in most blocks ({seeded} after a frozen kernel)")

    def test_retrigger_and_a_new_note_restart_master_and_slave(self):
        """Slot 2 (stock's note start, our hook first): a same-note retrigger and a new note restart the copy with stock's WT1,
        and the slave with it (an event at frame 0 of the next block)."""
        m = Mixed(); m.note(60)
        for _ in range(19): m.block()
        t1 = m.t; m.note(60)
        for _ in range(19): m.block()
        t2 = m.t; m.note(67)
        for _ in range(19): m.block()
        m.aligned(self, t1 + 1, t2, msg="after the retrigger")
        m.aligned(self, t2 + 1, msg="after the new note")
        self.assertEqual(m.starts, [0, t1, t2], "the slave restarts at each note start, and only there")

    def test_wtoscreset_off_seeds_the_copy_from_stocks_phase(self):
        """WTOscReset off (osc+0x1ED = 0): stock's phase runs on at a note start (only its fraction is zeroed); the copy takes
        it at the next block, and there is no restart event."""
        m = Mixed(); m.r.u.mem_write(m.o1 + 0x1ED, b"\0"); m.note(60)
        for _ in range(19): m.block()
        t1 = m.t; m.note(72)
        for _ in range(19): m.block()
        m.aligned(self, t1 + 1, msg="Reset off, after the new note")
        self.assertEqual(m.starts, [], "no restarts with Reset off")

    def test_sync_turned_on_mid_note_is_seeded(self):
        """Plan fix 2: Sync turned on mid-note takes stock's phase (no misalignment until the next note), and gives no
        spurious slave restart (a note start with Sync off leaves nothing pending)."""
        m = Mixed(sync=0); m.note(60)
        for _ in range(15): m.block()
        self.assertEqual(m.wr, [], "Sync off: no events")
        t1 = m.t; m.prop(K_SYNC, 1)
        for _ in range(20): m.block()
        m.aligned(self, t1, msg="Sync on mid-note")
        self.assertEqual(m.starts, [])

    def test_sync_off_and_on_again_mid_note_is_seeded(self):
        """Review M7: Sync on, off, on again within one note: the second engage seeds again (the copy did not move while
        Sync was off), no restart."""
        m = Mixed(); m.note(60)
        for _ in range(10): m.block()
        m.prop(K_SYNC, 0)
        for _ in range(7): m.block()
        t1 = m.t; m.prop(K_SYNC, 1)
        for _ in range(15): m.block()
        m.aligned(self, t1, msg="Sync on again")
        self.assertEqual(m.starts, [0], "only the note start's restart")

    def test_stocks_kernel_running_again_reseeds_the_copy(self):
        """Review M7: stock's WT1 kernel stops at level 0 (smoother at rest) and with no WAV; the copy runs on, and when the
        kernel runs again (level back; a WAV loaded mid-note) the copy takes its phase. A level 0 that stays (the classic
        patch) keeps the copy free-running at OSC1's pitch."""
        m = Mixed(); m.note(60)
        for _ in range(10): m.block()
        m.r.level(m.v, 0, 0.0)
        for _ in range(8): m.block()
        self.assertEqual(m.r.u.mem_read(m.o2 + S.OSC_KERNEL + 165, 1)[0], 3, "mlive: stock's kernel frozen")
        t1 = m.t; m.r.level(m.v, 0, 1.0)
        for _ in range(25): m.block()
        m.aligned(self, t1 + 800, msg="WT1 level 0 for 8 blocks, back")   # (WT1's smoother: its cycles show from ~800)
        self.assertEqual(m.starts, [0])
        m = Mixed(table=False); m.note(60)
        for _ in range(10): m.block()
        t1 = m.t; m.r.tabled(m.o1)
        for _ in range(25): m.block()
        m.aligned(self, t1, msg="a WAV loaded mid-note")
        m = Mixed(); m.r.level(m.v, 0, 0.0); m.note(48)
        for _ in range(40): m.block()
        self.assertEqual(max(map(abs, m.x1)), 0.0, "WT1 silent")
        ws = m.wr[1:]
        self.assertAlmostEqual((ws[-1] - ws[0]) / (len(ws) - 1), DEV_RATE / (440 * 2 ** ((48 - 69) / 12)), delta=0.01)

    def test_wt2_turned_analog_mid_note_is_seeded(self):
        """WT2 Wavetable -> Analog (Sync on) mid-note: the copy starts from stock's WT1 phase, not 0."""
        m = Mixed(type2=0); m.note(60)
        for _ in range(15): m.block()
        t1 = m.t; m.prop(K_TYPE, 1); m.prop(K_SHAPE, 48); m.prop(K_SYNC, 1)
        for _ in range(20): m.block()
        m.aligned(self, t1, msg="Type -> Analog mid-note")
        self.assertEqual(m.starts, [])

    def test_the_master_runs_while_the_slave_is_silent(self):
        """WT2 at level 0 for 20 blocks (no render, no events): the copy moves on, so the wraps are WT1's when it is back."""
        m = Mixed(); m.note(60)
        for _ in range(10): m.block()
        m.r.level(m.v, 1, 0.0)
        for _ in range(20): m.block()
        n_wr = len(m.wr)
        t1 = m.t; m.r.level(m.v, 1, 1.0)
        for _ in range(20): m.block()
        m.aligned(self, t1 + 600, msg="after WT2's silence")            # (its level smoother: a few hundred frames)
        self.assertGreater(len(m.wr), n_wr)

    def test_a_no_wav_osc1_is_still_a_master(self):
        """The classic sync patch: OSC1 with no WAV is silent (stock's render returns before its pitch code), and the slave
        still syncs to OSC1's pitch."""
        m = Mixed(table=False); m.note(48)
        for _ in range(40): m.block()
        self.assertEqual(max(map(abs, m.x1)), 0.0, "WT1 silent")
        ws = m.wr[1:]
        per = (ws[-1] - ws[0]) / (len(ws) - 1)
        self.assertAlmostEqual(per, DEV_RATE / (440 * 2 ** ((48 - 69) / 12)), delta=0.01)
        self.assertLess(lag_err(m.x2[2048:], per), 0.05, "WT2 periodic at the master's period")

    def test_wt1_is_stocks_bit_for_bit_with_wt2_synced(self):
        """Plan 3.3: WT1's output and its whole 0x1F0-byte object are stock 1.6.8's, with WT2 Analog synced to it, through
        vibrato and a new note (2N, divider 2)."""
        def run(stock, table=True):
            m = Mixed(stock=stock, table=table); m.note(60)
            for b in range(25): m.block((1 / 12) * math.sin(b / 5))
            m.note(64)
            for b in range(25): m.block(0.1)
            return m.x1, bytes(m.r.u.mem_read(m.o1, 0x1F0)), m
        a, ka, ma = run(False)
        b, kb, _ = run(True)
        self.assertGreater(max(map(abs, b)), 0.1, "control: stock's WT1 plays its table")
        same(self, a, b, "WT1's output")
        same(self, ka, kb, "WT1's object")
        self.assertGreater(len(ma.wr), 20, "WT2 was synced")
        # review M7: with no WAV stock's render never reaches PARAM_VALUE on WT1's pitch, so the master's call to it must not
        # leave its cached value in WT1 (+0xAC)
        a, ka, ma = run(False, table=False)
        b, kb, _ = run(True, table=False)
        same(self, ka, kb, "WT1's object, no WAV")
        self.assertGreater(len(ma.wr), 20, "WT2 was synced (no WAV)")

    def test_two_voices_keep_their_own_masters(self):
        """Two mixed voices of one track (one rig, its stats shared): each slave follows its own WT1."""
        a = Mixed(handle=0x51); b = Mixed(r=a.r, handle=0x51)
        a.note(60); b.note(67)
        for _ in range(30):
            a.block(); b.block()
        a.aligned(self, 1, msg="voice A"); b.aligned(self, 1, msg="voice B")
        self.assertNotEqual(len(a.wr), len(b.wr))

    def test_the_stats_count_synced_renders(self):
        m = Mixed(); m.note(60)
        for _ in range(5): m.block()
        self.assertEqual(stat(m.r, 14), 5, "synced: WT2's 5 renders (WT1 is stock's)")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Props(unittest.TestCase):
    """BE4.40 N3: the oscillator's own settings, as stock's set_property delivers them, per oscillator."""
    def test_settings_are_clamped_where_they_are_stored(self):
        """Stock never range-checks (a file is read raw, an edit forwarded raw: test_native_props), so an_prop clamps. P3 Q1
        (OD4, plan 6.7): the lists play what stock's widget shows for a value it has no label for (test_synth_ui pins the
        widget): Sync any value but 0 is On (BE4.40: only 1 was), Bit Depth past 3 (and negative) is 4 bits (3); the type-10
        amounts clamp to 0..1000."""
        r = Rig(); root, v = r.voice(0x61)
        o = r.osc(v, 0)
        r.prop(o, K_TYPE, 1)                                     # (slot 8 returns nothing: the effects are checked)
        self.assertEqual(r.settings(v, 0)[:7], (1, 48, 0, 0, 0, 0, 0), "Analog: Shape at its default (saw), the rest 0")
        # settings(): (type, shape, sync, bits, glide, subfm, fm12, spare)
        for key, val, want, at in ((K_SHAPE, 1000, 127, 1), (K_SHAPE, -5, 0, 1), (K_SHAPE, 77, 77, 1), (K_GLIDE, 5000, 1000, 4),
                                   (K_GLIDE, -1, 0, 4), (K_GLIDE, 640, 640, 4), (K_SYNC, 7, 1, 2), (K_SYNC, 0, 0, 2),
                                   (K_SYNC, 1, 1, 2), (K_SYNC, -1, 1, 2), (K_SYNC, 0x10000, 1, 2),
                                   (K_BITS, 0, 0, 3), (K_BITS, 1, 1, 3), (K_BITS, 2, 2, 3), (K_BITS, 3, 3, 3), (K_BITS, 7, 3, 3),
                                   (K_BITS, -1, 3, 3), (K_BITS, 0x7FFFFFFF, 3, 3),
                                   (K_SUBFM, 1000, 1000, 5), (K_SUBFM, 5000, 1000, 5), (K_SUBFM, -3, 0, 5), (K_SUBFM, 437, 437, 5),
                                   (K_FM12, 1000, 1000, 6), (K_FM12, 99999, 1000, 6), (K_FM12, -1, 0, 6), (K_FM12, 1, 1, 6)):
            with self.subTest(key=hex(key), value=val):
                r.prop(o, key, val)
                self.assertEqual(r.settings(v, 0)[at], want)
        self.assertEqual(r.settings(v, 0)[7], 0, "the spare halfword stays 0")

    def test_the_fm_keys_are_ours_and_kept_only_while_analog(self):
        """P3 Q1: an_prop takes the three new keys (slot 8 returns 1: stock's own slot 8 never sees them), stores nothing for
        a Wavetable oscillator (its kernel area is stock's history), and a fresh Analog state starts them at 0, with the
        AnOsc ramp memory msub / m12 (P3 3.3) at 0; leaving Analog zeroes them with the area."""
        r = Rig(); root, v = r.voice(0x6A)
        o = r.osc(v, 1)
        hist = bytes(range(0xC0))
        r.u.mem_write(o + S.OSC_KERNEL, hist)
        props = lambda: r.r32(SA + SA_STATS + 16)              # (slot 8 returns nothing: the props count says whose it was)
        for k in (K_SUBFM, K_BITS, K_FM12):
            n0 = props(); r.prop(o, k, 3)
            self.assertEqual(props() - n0, 1, f"{k:#x}: ours")
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL, 0xC0)), hist, "Wavetable: untouched")
        n0 = props(); r.prop(o, 0xC9, 3)
        self.assertEqual(props(), n0, "control: a stock key is stock's")
        r.prop(o, K_TYPE, 1)
        self.assertEqual(r.settings(v, 1)[3:7], (0, 0, 0, 0), "fresh: Bit Depth Full, Glide 0, no FM")
        self.assertEqual(struct.unpack("<HH", r.u.mem_read(o + S.OSC_KERNEL + MSUB_AT, 4)), (0, 0), "msub, m12")
        for k, val in ((K_SUBFM, 900), (K_BITS, 2), (K_FM12, 250)):
            r.prop(o, k, val)
        self.assertEqual(r.settings(v, 1)[3:7], (2, 0, 900, 250))
        r.prop(o, K_TYPE, 0)
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL, 0xC0)), bytes(0xC0))

    def test_the_silent_paths_zero_the_fm_ramp_memory(self):
        """P3 6.4 (Q1 review): msub / m12 read 0 after each path that renders nothing, so an oscillator heard again starts
        from its amounts, no ramp: the silent skip (Level 0, not a master), a block past 1024 frames, and (E3) a silent sync
        master that only tracks its phase. Nothing writes them in Q1, so they are set by hand before every block. reset()'s
        own clear has no case: it runs only right after forget has zeroed the whole area (Type 1 on a non-Analog
        oscillator), so no test can see it (M: the mutation removing it is equivalent)."""
        msub = lambda r, o: struct.unpack("<HH", r.u.mem_read(o + S.OSC_KERNEL + MSUB_AT, 4))
        for name, which, sync, lv, n in (("skip", 1, 0, (1.0, 0.0), None), ("n > 1024", 1, 0, (1.0, 1.0), 2048),
                                         ("E3 silent master", 0, 1, (0.0, 1.0), None)):
            with self.subTest(case=name):
                r = Rig(); root, v = r.voice(0x6C)
                for i in (0, 1):
                    r.prop(r.osc(v, i), K_TYPE, 1)
                r.prop(r.osc(v, 1), K_SYNC, sync)
                r.note(v, 60)
                for i in (0, 1):
                    r.level(v, i, lv[i])
                o, other = r.osc(v, which), r.osc(v, 1 - which)
                for b in range(4):                               # (Level's smoother settles in the first block)
                    for x in (o, other):
                        r.u.mem_write(x + S.OSC_KERNEL + MSUB_AT, struct.pack("<HH", 123, 456))
                    self.assertEqual(msub(r, o), (123, 456), "control: written where AnOsc keeps them")
                    r.render(root, v, n, (0, 1)); r.block()
                self.assertEqual(msub(r, o), (0, 0), f"{name}: msub, m12 after the block")
                if n is None:                                    # P3 Q1 / Q2: the playing path keeps them (Q5 / Q6 ramp them)
                    self.assertEqual(msub(r, other), (123, 456), f"{name}: control, the oscillator that played")

    def test_an_enter_clears_the_fm_tag_for_every_voice(self):
        """P3 3.3 (review R11): Run.fmtag is 0 after an_enter names a voice, before its `no Analog oscillator` return as after
        the full path, so a voice never sees another voice's FM buffers. (P3 Q1: nothing sets it yet; this pins the clear.)"""
        r = Rig(); root, v = r.voice(0x6B)
        for analog in (0, 1):
            with self.subTest(analog=analog):
                r.prop(r.osc(v, 0), K_TYPE, analog)
                r.w32(SA + SA_RUN + RUN_FMTAG, 3)
                r.call(DSP["an_enter"], root, v)
                self.assertEqual((r.r32(SA + SA_RUN + 8), r.r32(SA + SA_RUN + RUN_FMTAG)), (v, 0), "Run.voice, Run.fmtag")

    def test_stocks_m7_input_delivers_every_key_of_ours_intact(self):
        """P3 OD7 (plan 6.1, risk 'the M7 key-flag path never run'): the M7's engine input (H_INPUT_SITE, run here on the built
        image) takes a set_property (0x3E) of a key below 0x1F2 through its broadcast-flag test, the byte engine + 0x8CB2 +
        key (0x08059A40: set -> a copy to the object 0xFFFFFFFE), then posts it to the track's mailbox (BUS_POST, engine +
        0x7190). The flag table is what the engine's constructor leaves (ENGINE_CTOR's body run here, its calls stubbed but
        the memset: 0x05, 0x27 and 0x31 only, M). Each of our seven keys, at its extremes, an oscillator's track handle and
        either index: posted once, unchanged, never broadcast; and the posted message, through stock's root
        (WT_ROOT_PROPERTY), reaches an_prop for that oscillator."""
        r = Rig()
        eng = r.alloc(0x15C20)
        r.u.mem_write(eng + 0x8CB2, b"\xAA" * 0x1F2)          # not zero before the ctor: its memset is seen
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        body = list(md.disasm(IMG[S.ENGINE_CTOR - BASE:S.ENGINE_CTOR - BASE + 0x400], S.ENGINE_CTOR))
        end = next(i.address for i in body if i.mnemonic == "pop.w" and "pc" in i.op_str)
        calls = [(i.address, int(i.op_str[1:], 16)) for i in body if i.address < end and i.mnemonic == "bl"]
        clear = [a for a, t in calls if t == calls[-1][1]]       # the memset the flag table's clear calls (the body's last bl)
        def skip(u, a, size, _):
            u.reg_write(UC_ARM_REG_R0, 0); u.reg_write(UC_ARM_REG_PC, (a + 4) | 1)
        hooks = [r.u.hook_add(UC_HOOK_CODE, skip, begin=a, end=a) for a, t in calls if a not in clear]
        r.call(S.ENGINE_CTOR, eng)
        for h in hooks:
            r.u.hook_del(h)
        flags = bytes(r.u.mem_read(eng + 0x8CB2, 0x1F2))
        self.assertEqual([k for k, b in enumerate(flags) if b], [0x05, 0x27, 0x31], "the constructor's flagged keys")
        keys = (K_TYPE, K_SHAPE, K_SYNC, K_GLIDE, K_SUBFM, K_BITS, K_FM12)
        self.assertTrue(all(k < 0x1F2 and not flags[k] for k in keys), "none of ours is flagged")
        posts, casts = [], []
        def post(u, a, size, _):
            posts.append((u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1), bytes(u.mem_read(u.reg_read(UC_ARM_REG_R1), 0x18))))
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
        def cast(u, a, size, _):
            casts.append(u.reg_read(UC_ARM_REG_R2)); u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
        r.u.hook_add(UC_HOOK_CODE, post, begin=S.BUS_POST, end=S.BUS_POST)
        r.u.hook_add(UC_HOOK_CODE, cast, begin=S.BUS_POST_KEY, end=S.BUS_POST_KEY)
        def send(key, value, index, handle=0x4D):
            m = r.alloc(0x20)
            r.u.mem_write(m, struct.pack("<BBhIIIIhh", 0x3E, 0, 0, 0, handle, key, value & 0xFFFFFFFF, index, 0))
            posts.clear(); casts.clear()
            r.call(S.H_INPUT_SITE, eng, m)
            return m
        send(0x05, 1, 0)
        self.assertEqual((casts, len(posts)), ([0xFFFFFFFE], 1), "control: a flagged key is also broadcast (the hook sees it)")
        pool_alloc(r)
        root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.w32(root + S.ROOT_HANDLE, 0x4D)
        v = r.r32(root + 0x14)
        delivered = 0
        for key in keys:
            for value in (0, 1, 1000, 5000, -3, 0x7FFFFFFF):
                for index in (0, 1):
                    with self.subTest(key=hex(key), value=value, index=index):
                        m = send(key, value, index)
                        self.assertEqual(casts, [], "not broadcast")
                        self.assertEqual(len(posts), 1, "posted once")
                        bus, msg, raw = posts[0]
                        self.assertEqual((bus, msg), (eng + 0x7190, m), "the track mailbox, this message")
                        self.assertEqual(raw, struct.pack("<BBhIIIIhh", 0x3E, 0, 0, 0, 0x4D, key, value & 0xFFFFFFFF, index, 0)[:0x18],
                                         "unchanged")
                        if value == 1 and key != K_TYPE:
                            r.root_prop(root, K_TYPE, 1, index)
                            n0 = r.r32(SA + SA_STATS + 16)
                            r.call(S.WT_ROOT_PROPERTY, root, msg)
                            self.assertEqual(r.r32(SA + SA_STATS + 16) - n0, 10, "an_prop took it on all ten voices")
                            delivered += 1
        self.assertEqual(delivered, 12, "the six non-Type keys, both indexes, through stock's root")
        at = {K_SHAPE: 1, K_SYNC: 2, K_BITS: 3, K_GLIDE: 4, K_SUBFM: 5, K_FM12: 6}
        self.assertEqual([r.settings(v, i)[at[k]] for i in (0, 1) for k in (K_SHAPE, K_SYNC, K_BITS, K_GLIDE, K_SUBFM, K_FM12)],
                         [1] * 12, "the value 1 of each key, in each oscillator of the voice")

    def test_type_is_analog_only_when_exactly_1(self):
        r = Rig(); root, v = r.voice(0x62)
        o = r.osc(v, 1)
        hist = bytes(range(0xC0))                                  # stock's wavetable history in the kernel area
        r.u.mem_write(o + S.OSC_KERNEL, hist)
        for t in (0, 2, -7, 0x101):
            r.prop(o, K_TYPE, t)
            self.assertNotEqual(r.state(v, 1)["magic"], AN_MAGIC, f"Type {t}: Wavetable")
        r.prop(o, K_SHAPE, 100); r.prop(o, K_GLIDE, 9)
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL, 0xC0)), hist, "a Wavetable oscillator's area is stock's: untouched")
        r.prop(o, 0xC9, 640)                                     # a stock key goes on to stock's slot 8
        r.prop(o, K_TYPE, 1)
        self.assertEqual((r.state(v, 1)["magic"], r.settings(v, 1)[:2]), (AN_MAGIC, (1, 48)))

    def test_leaving_analog_zeroes_the_whole_area(self):
        """Stock's render then finds a zero history (review N1), our settings included."""
        r = Rig(); root, v = r.voice(0x63); r.table(0, 0x63, 3, glide=300); r.shape(v, 1, 1.0); r.note(v, 60)
        for _ in range(3):
            r.render(root, v, oscs=(0, 1)); r.block()
        r.prop(r.osc(v, 1), K_TYPE, 0)
        self.assertEqual(bytes(r.u.mem_read(r.osc(v, 1) + S.OSC_KERNEL, 0xC0)), bytes(0xC0))
        self.assertEqual(r.state(v, 0)["magic"], AN_MAGIC, "WT1 is still Analog")
        r.track_pcs = True
        r.render(root, v, oscs=(1,))
        self.assertIn(S.WT_OSC_RENDER, r.pcs, "WT2 is stock's again")

    def test_a_repeated_type_keeps_the_running_state(self):
        """A push repeats Type 1 (a preset, a load): the phase and settings stay."""
        r = Rig(); root, v = r.voice(0x64); r.table(0, 0x64, 1); r.shape(v, 0, 0.5); r.note(v, 60)
        for _ in range(3):
            r.render(root, v, oscs=(0,)); r.block()
        ph = r.state(v, 0)["phase"]
        r.prop(r.osc(v, 0), K_TYPE, 1)
        self.assertEqual((r.state(v, 0)["phase"], r.settings(v, 0)[1]), (ph, 88))

    def test_stocks_root_sends_each_key_to_the_right_oscillator(self):
        """WT_ROOT_PROPERTY with index 0 / 1 reaches every voice's WT1 / WT2 only (index 2, the sub, neither)."""
        r = Rig()
        pool = [0xC5F10000]                                   # harness bump allocator for the 10 voices  # addr-ok: harness
        def alloc(uc, a, size, _):
            n = uc.reg_read(UC_ARM_REG_R2); p = pool[0]; pool[0] += (n + 31) & ~31
            uc.mem_write(p, bytes(n)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        r.u.hook_add(UC_HOOK_CODE, alloc, begin=S.NAMED_ALLOC, end=S.NAMED_ALLOC)
        root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.w32(root + S.ROOT_HANDLE, 0x77)
        voices = [r.r32(root + 0x14 + 4 * i) for i in range(10)]
        r.root_prop(root, K_TYPE, 1, 1); r.root_prop(root, K_SHAPE, 100, 1); r.root_prop(root, K_SHAPE, 20, 0)
        r.root_prop(root, K_TYPE, 1, 2)
        for vo in voices:
            self.assertNotEqual(r.state(vo, 0)["magic"], AN_MAGIC, "WT1 not Analog")
            self.assertEqual((r.state(vo, 1)["magic"], r.settings(vo, 1)[:2]), (AN_MAGIC, (1, 100)), "WT2: Type and Shape")
        r.root_prop(root, K_TYPE, 1, 0); r.root_prop(root, K_GLIDE, 250, 0)
        self.assertEqual([r.settings(vo, 0)[4] for vo in voices], [250] * 10)
        self.assertEqual(r.r32(SA + SA_STATS + 16), 5 * 10, "props: every message of ours a WT oscillator took (the sub is another class)")

    def test_no_sync_master_for_a_wavetable_osc2(self):
        """The reverse case is not built (plan Track S): Analog WT1 + Wavetable WT2 with Sync 1. an_prop drops WT2's Sync,
        WT1 runs free (writes no wraps into WT2's area), and WT2 is stock's render bit for bit, its area untouched."""
        def run(stock):
            r = Rig(img=STOCK_IMG if stock else IMG, dev=True); root, v = r.voice(0x65)
            r.tabled(r.osc(v, 1)); r.level(v, 0, 1.0); r.level(v, 1, 1.0)
            if not stock:
                r.prop(r.osc(v, 0), K_TYPE, 1); r.prop(r.osc(v, 1), K_SYNC, 1)
            r.note(v, 48)
            outs = []
            for _ in range(8):
                if not stock:
                    r.call(DSP["an_enter"], root, v)
                osc = r.osc(v, 1); out, od = r.buf(DEV_FRAMES); scr, _ = r.buf(DEV_FRAMES)
                if not stock:
                    r.call(r.r32(r.r32(r.osc(v, 0))) & ~1, r.osc(v, 0), scr, out)
                    r.u.mem_write(od, bytes(4 * DEV_FRAMES))
                r.call(r.r32(r.r32(osc)) & ~1, osc, scr, out)
                outs += struct.unpack(f"<{DEV_FRAMES}f", r.u.mem_read(od, 4 * DEV_FRAMES)); r.block()
            return outs, bytes(r.u.mem_read(r.osc(v, 1), 0x1F0)), r
        ours, ka, r = run(False)
        stock, kb, _ = run(True)
        self.assertGreater(max(map(abs, stock)), 0.1)
        same(self, ours, stock, "WT2's output")
        same(self, ka, kb, "WT2's object, its kernel area included")
        self.assertEqual(stat(r, 14), 0, "nothing synced")

    def test_a_wavetable_osc1_next_to_an_analog_osc2_is_stocks_bit_for_bit(self):
        """Plan 3.3, the test that matters most: in a mixed voice the Wavetable oscillator (a real table loaded) renders
        exactly as on stock 1.6.8, and its kernel area ends identical."""
        def run(stock):
            r = Rig(img=STOCK_IMG) if stock else Rig()
            root, v = r.voice(0x51)
            r.tabled(r.osc(v, 0)); r.level(v, 0, 1.0); r.level(v, 1, 1.0)
            if not stock:
                r.prop(r.osc(v, 1), K_TYPE, 1, 0x51, 1); r.prop(r.osc(v, 1), K_SHAPE, 88, 0x51, 1)
            r.note(v, 60)
            outs = []
            for _ in range(10):
                if not stock:
                    r.call(DSP["an_enter"], root, v)
                for i in (0, 1):
                    osc = r.osc(v, i)
                    out, od = r.buf(FRAMES); scr, _ = r.buf(FRAMES)
                    r.call(r.r32(r.r32(osc)) & ~1, osc, scr, out)
                    if i == 0:
                        outs += list(struct.unpack(f"<{FRAMES}f", r.u.mem_read(od, 4 * FRAMES)))
                r.block()
            return outs, bytes(r.u.mem_read(r.osc(v, 0) + S.OSC_KERNEL, 0xC0 + 0x24)), r
        ours, ka, ra = run(False)
        stock, kb, _ = run(True)
        self.assertGreater(max(map(abs, stock)), 0.1, "control: stock's WT1 plays its table")
        same(self, ours, stock, "WT1's output")
        same(self, ka, kb, "WT1's kernel area and stock's fields after it")
        self.assertEqual(ra.state(ra.voices[0x51][0], 1)["magic"], AN_MAGIC, "WT2 was Analog")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Golden(unittest.TestCase):
    """an_render (the DSP at its placement) against model() above."""
    def kernel(self, inc, shape, n, role=0, events=None, phase=0.0):
        r = Rig()
        st = r.alloc(152); r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, phase, 0.0))
        ev = r.alloc(128); r.u.mem_write(ev, bytes(events) if events else bytes([255]) * 128)
        out = r.alloc(4 * n)
        _, cnt = r.call(DSP["an_render"], st, out, n, ev, sargs=(role,), fargs=(inc, shape))
        xs = list(struct.unpack(f"<{n}f", r.u.mem_read(out, 4 * n)))
        return xs, struct.unpack("<f", r.u.mem_read(st + 4, 4))[0], bytes(r.u.mem_read(ev, 128)), cnt

    def close(self, a, b, tol=2e-5):
        self.assertEqual(len(a), len(b))
        worst = max(abs(x - y) for x, y in zip(a, b))
        self.assertLess(worst, tol, f"worst {worst:g}")

    def test_regimes(self):
        for inc in (261.63 / 48000, 0.0123, 0.2):
            for shape in (0.0, 0.25, 0.5, 0.75, 1.0):
                with self.subTest(inc=inc, shape=shape):
                    xs, p, _, _ = self.kernel(f32(inc), shape, 128)
                    want, wp, _, _ = model(f32(inc), shape, 128)
                    self.close(xs, want)
                    self.assertAlmostEqual(p, wp, delta=1e-5)

    def test_master_records_its_wraps(self):
        _, _, ev, _ = self.kernel(f32(0.03), 0.3, 128, role=1)
        _, _, _, want = model(f32(0.03), 0.3, 128, master=True)
        got = list(ev)
        self.assertEqual(sum(1 for e in got if e != 255), sum(1 for e in want if e != 255))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(got, want)), 1, "wrap fractions within one 1/254 step")

    def test_slave_resets_at_the_events(self):
        _, _, _, events = model(f32(0.021), 0.0, 128, master=True)
        xs, _, _, _ = self.kernel(f32(0.034), 0.6, 128, role=2, events=events)
        want, _, _, _ = model(f32(0.034), 0.6, 128, sync=events)
        self.close(xs, want)

    # ---- an_master (Track S): against the exact model of stock's phase (wf/sync mtest.py's): integer arithmetic on the
    # float increment's exact value, wraps where the phase crosses a whole cycle
    @staticmethod
    def master_model(p, q, n, restart=False):
        M, ev = 2 ** 64, [255] * n
        if restart:
            ev[0] = 0
        for i in range(n):
            a = p + i * q
            if (a + q) // M > a // M:
                fr = Fraction((a // M + 1) * M - a, q)
                ev[i] = max(0, min(254, math.floor(fr * 254 + Fraction(1, 2))))
        return ev, (p + n * q) % M

    def master(self, r, st, ev, inc, n, p=None, restart=False):
        if p is not None:
            r.u.mem_write(st + 156, struct.pack("<II", p >> 32, p & 0xFFFFFFFF))
        r.u.mem_write(st + 164, bytes([1 if restart else 0]))
        _, c = r.call(DSP["an_master"], st, ev, n, fargs=(inc,))
        hi, lo = struct.unpack("<II", r.u.mem_read(st + 156, 8))
        return list(r.u.mem_read(ev, n)) if ev else None, (hi << 32) | lo, c

    def same_wraps(self, got, want, msg):
        tg = [i + e / 254 for i, e in enumerate(got) if e != 255]
        tw = [i + e / 254 for i, e in enumerate(want) if e != 255]
        self.assertEqual(len(tg), len(tw), f"{msg}: no missing or extra wraps")
        if tg:
            self.assertLessEqual(max(abs(a - b) for a, b in zip(tg, tw)), 1.01 / 254, msg)

    def test_master_against_the_exact_phase(self):
        """20 Hz .. 21 kHz at 96 kHz (2N), steady, a random walk and vibrato, 400 chunks of 128: the wraps within one 1/254
        step of the exact ones, none missing or extra, and the phase exact after every chunk (no drift, plan fix 3)."""
        r = Rig(); st = r.alloc(168); ev = r.alloc(128)
        random.seed(5)
        worst_cost = 0
        for hz in (20.0, 55.0, 130.81, 261.63, 1000.0, 4186.0, 12543.9, 21000.0):
            for mode in ("steady", "walk", "vibrato"):
                p = random.getrandbits(64)
                self.master(r, st, ev, 0.0, 0, p=p)
                for b in range(400):
                    f = hz if mode == "steady" else hz * 2 ** (random.uniform(-1, 1) if mode == "walk" else math.sin(b / 9) / 12)
                    inc = f32(min(f / DEV_RATE, 0.45))
                    q = int(Fraction(inc) * 2 ** 64)
                    want, wp = self.master_model(p, q, 128)
                    got, gp, c = self.master(r, st, ev, inc, 128)
                    worst_cost = max(worst_cost, c)
                    self.assertEqual(gp, wp, f"{hz} Hz {mode}: the exact phase")
                    self.same_wraps(got, want, f"{hz} Hz {mode} chunk {b}")
                    p = gp
        print(f"\n  an_master(128), worst instructions 20 Hz..21 kHz: {worst_cost}", file=sys.stderr)

    def test_master_edges(self):
        """A wrap exactly on the chunk's end (in its last sample, fraction 1: the next chunk has none at its start), exactly at
        the end of the sample before, one LSB either side; phase 0; mrestart (event 0, then cleared); events 0 (phase
        only, mrestart cleared)."""
        r = Rig(); st = r.alloc(168); ev = r.alloc(128)
        n, M = 128, 2 ** 64
        for inc in (f32(261.63 / DEV_RATE), f32(0.3), f32(0.0123456)):
            q = int(Fraction(inc) * M)
            # (the last four, review M7: the wrap 1 .. 2^40 units after the start of the last sample, where the float time
            # puts it in the sample before: it must not be there too)
            for p in ((-n * q) % M, (-n * q + 1) % M, (-n * q - 1) % M, (-(n - 1) * q) % M, (-(n - 1) * q + 1) % M, 0,
                      (-(n - 1) * q - 1) % M, (-(n - 1) * q - 2 ** 20) % M, (-(n - 1) * q - 2 ** 30) % M,
                      (-(n - 1) * q - 2 ** 40) % M):
                with self.subTest(inc=inc, p=hex(p)):
                    got, gp, _ = self.master(r, st, ev, inc, n, p=p)
                    want, wp = self.master_model(p, q, n)
                    self.same_wraps(got, want, "edge")
                    self.assertEqual(gp, wp)
                    got2, _, _ = self.master(r, st, ev, inc, n)
                    want2, _ = self.master_model(wp, q, n)
                    self.same_wraps(got2, want2, "the next chunk")
        inc = f32(0.01); q = int(Fraction(inc) * M)
        got, gp, _ = self.master(r, st, ev, inc, n, p=0, restart=True)
        self.same_wraps(got, self.master_model(0, q, n, restart=True)[0], "restart")
        self.assertEqual(got[0], 0)
        self.assertEqual(r.u.mem_read(st + 164, 1)[0], 0, "mrestart consumed")
        _, gp2, _ = self.master(r, st, 0, inc, 1000, restart=True)
        self.assertEqual((gp2, r.u.mem_read(st + 164, 1)[0]), ((gp + 1000 * q) % M, 0), "events 0: phase only")

    def test_nan_and_range_guards(self):
        for inc, shape in ((float("nan"), 0.5), (0.01, float("nan")), (-1.0, 0.3), (5.0, 2.0)):
            with self.subTest(inc=inc, shape=shape):
                xs, p, _, _ = self.kernel(inc, shape, 64)
                self.assertTrue(all(math.isfinite(x) for x in xs) and math.isfinite(p))
                want, _, _, _ = model(inc, shape, 64)
                self.close(xs, want)


K_POSITION = 0xC9            # stock's Position key: the modulation destination "OSC1: Position" / "OSC2: Position"


def route(r, osc, slot, amount, key=K_POSITION):
    """Stock's modulation routing: the oscillator class's slot 10 (osc, source, key, slot 0..3, amount s0) -> PARAM_CONNECT
    on the key's parameter (0xC9: Position, osc+0x30). The source is any object with its value at +4 (an LFO's output).
    Returns the source; its value is set with r.wf(source + 4, x)."""
    src = r.alloc(0x10)
    r.call(r.r32(r.r32(osc) + 0x28) & ~1, osc, src, key, slot, fargs=(amount,))
    return src


def duty(xs, period=None):
    """The share of samples above the middle, over whole periods (a part period would bias it)."""
    if period:
        xs = xs[:int(int(len(xs) / period) * period)]
    mid = (max(xs) + min(xs)) / 2
    return sum(1 for x in xs if x > mid) / len(xs)


P60 = DEV_RATE / 261.6256


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class ShapeMod(unittest.TestCase):
    """Track M (J4): on an Analog oscillator the modulation stock routes to its Position destination drives Shape:
    Shape + 127 x (the routed sum), clamped to 0..127; Position's own value (the knob) does not count."""
    def run_(self, shape, mod=None, blocks=32, amount=1.0, base=None, r=None):
        r = r or Rig(dev=True); root, v = r.voice(0x71); r.table(0, 0x71, 1); r.note(v, 60)
        r.prop(r.osc(v, 0), K_SHAPE, shape)
        if base is not None:
            r.prop(r.osc(v, 0), K_POSITION, base)                 # the hidden Position knob (stock's slot 8: +0x2C)
        src = route(r, r.osc(v, 0), 0, amount) if mod is not None else None
        xs = []
        for b in range(blocks):
            if src:
                r.wf(src + 4, mod(b) if callable(mod) else mod)
            out, _ = r.render(root, v, oscs=(0,)); xs += out; r.block()
        return xs, r

    def test_routing_reaches_the_position_parameter(self):
        r = Rig(dev=True); _, v = r.voice(0x72); o = r.osc(v, 0)
        src = route(r, o, 1, 0.5)
        self.assertEqual((r.r32(o + 0x30 + 0x30), list(r.u.mem_read(o + 0x30 + 0x28, 2))), (2, [0, 2]), "slot 0 the knob, slot 2")
        self.assertEqual((r.r32(o + 0x30 + 16), r.rf(o + 0x30 + 20)), (src, 0.5))

    def test_lfo_to_position_moves_the_pulse_width(self):
        """Square (88) + 0.2 -> Shape 113.4: width 0.5 - 0.84 x (0.5 x 25.4 / 39) = 0.226; - 0.2 -> 62.6, a saw > square blend."""
        base, _ = self.run_(88)
        up, r = self.run_(88, 0.2)
        self.assertAlmostEqual(duty(base[2048:], P60), 0.5, delta=0.01)
        w = 0.5 - 0.84 * (0.5 * (88 + 0.2 * 127 - 88) / 39)
        self.assertAlmostEqual(duty(up[2048:], P60), w, delta=0.01)
        self.assertEqual(stat(r, 15), 32, "shape_mods: every render")
        half, _ = self.run_(88, 0.1, amount=2.0)
        same(self, half, up, "amount x source")
        top, _ = self.run_(88, 5.0)
        pulse, _ = self.run_(127)
        same(self, top, pulse, "clamped at 127")
        low, _ = self.run_(20, -1.0)
        sine, _ = self.run_(0)
        same(self, low, sine, "clamped at 0: the sine (P3 Q3; BE4.40's 0 was on its saw plateau)")
        saw, _ = self.run_(48)
        self.assertNotEqual(sine, saw, "P3 Q3: Shape 0 is not the saw")

    def test_an_lfo_sweeps_the_width_over_time(self):
        xs, _ = self.run_(100, lambda b: 0.2 * math.sin(2 * math.pi * b / 32), blocks=64)
        d = [duty(xs[i:i + 4 * DEV_FRAMES], P60) for i in range(0, len(xs) - 4 * DEV_FRAMES, 2 * DEV_FRAMES)]
        self.assertGreater(max(d) - min(d), 0.2, f"the width follows the LFO: {min(d):.2f}..{max(d):.2f}")

    def test_a_shape_change_ramps_over_the_block(self):
        """Review M7: a Shape that moved since the last block ramps over this one, AN_RAMP (32) frames a step, each step's jump
        corrected (an_reshape), the last part at the new Shape: the oscillator's state after every block is that of the
        kernel driven that way directly, bit for bit (the modulated Shape held to 1/512 of a step), and its output that
        kernel's times the level (the smoother settled)."""
        r = Rig(dev=True); root, v = r.voice(0x74); r.table(0, 0x74, 1); r.note(v, 48); o = r.osc(v, 0)
        r.prop(o, K_SHAPE, 100); src = route(r, o, 0, 1.0)
        st = o + S.OSC_KERNEL
        ref = r.alloc(172); out = r.alloc(4 * DEV_FRAMES); ev = r.alloc(128)
        r.u.mem_write(ev, bytes([255]) * 128)
        fma = lambda a, b, c: f32(float(Fraction(a) * Fraction(b) + Fraction(c)))   # (clang contracts a * b + c)
        def k_of(sh):
            if not sh > 48: return 0.0
            if sh <= 88: return f32(f32(sh - 48) * f32(0.5 / 40))
            return fma(f32(min(sh, 127.0) - 88), f32(0.5 / 39), 0.5)
        ramps = 0
        for b in range(16):
            x = f32(0.2 * math.sin(b / 2)) if b else 0.0
            r.wf(src + 4, x)
            ph, dl = struct.unpack("<ff", r.u.mem_read(st + 4, 8)); sq0 = struct.unpack("<H", r.u.mem_read(st + 166, 2))[0]
            xs, _ = r.render(root, v, oscs=(0,)); r.block()
            inc = f32(r.rf(o + 0x1E8) * f32(1 / 2048))
            sh = 100.0
            if x:
                sh = fma(x, 127.0, 100.0); sh = min(max(sh, 0.0), 127.0)
                sh = f32(float(int(fma(sh, 512.0, 0.5))) * f32(1 / 512))
            k1 = k_of(sh); sq = int(f32(sh * 512.0))
            self.assertEqual(struct.unpack("<H", r.u.mem_read(st + 166, 2))[0], sq, f"block {b}: mshape")
            r.u.mem_write(ref, struct.pack("<Iff", AN_MAGIC, ph, dl))
            parts = [(0, DEV_FRAMES, k1)]
            if sq0 != 0xFFFF and sq0 != sq:
                ramps += 1
                k0 = k_of(f32(sq0 * f32(1 / 512))); P = DEV_FRAMES // 32
                parts = [(32 * j, 32, k1 if j + 1 == P else fma(f32(k1 - k0), f32((j + 1) / P), k0)) for j in range(P)]
                kp = k0
            else:
                kp = k1
            for off, n, k in parts:
                if k != kp:
                    r.call(DSP["an_reshape"], ref, fargs=(kp, k))
                kp = k
                r.call(DSP["an_render"], ref, out + 4 * off, n, ev, sargs=(0,), fargs=(inc, k))
            self.assertEqual(r.u.mem_read(ref + 4, 8), r.u.mem_read(st + 4, 8), f"block {b}: phase and delayed")
            want = struct.unpack(f"<{DEV_FRAMES}f", r.u.mem_read(out, 4 * DEV_FRAMES))
            if b >= 4:
                g = sum(x * y for x, y in zip(xs, want)) / sum(y * y for y in want)
                err = max(abs(x - g * y) for x, y in zip(xs, want))
                self.assertLess(err, 1e-5, f"block {b}: the output is the ramped kernel's (gain {g:.6f})")
        self.assertGreater(ramps, 10)

    def test_the_ramp_is_close_to_a_per_sample_one(self):
        """Review M7 (fix_m7 zip.py): Shape under an 8 Hz LFO (saw to square, Shape 68 +- 19), the kernel stepped once per
        block (before the fix) and per AN_RAMP frames, each against a step every sample: the ramp's error is a fraction."""
        r = Rig(); N = 256
        def an_shape(sh):
            return 0.0 if not sh > 48 else (sh - 48) * (0.5 / 40) if sh <= 88 else 0.5 + (min(sh, 127) - 88) * (0.5 / 39)
        def run(step):
            st = r.alloc(172); out = r.alloc(4 * N); ev = r.alloc(128)
            r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, 0.0, 0.0)); r.u.mem_write(ev, bytes([255]) * 128)
            xs, kp = [], None
            for b in range(24):
                k1 = f32(an_shape(68 + 127 * 0.15 * math.sin(2 * math.pi * 8 * b * N / DEV_RATE)))
                k0 = k1 if kp is None else kp
                P = N // step if step else 1
                cur = k0 if step else k1
                for j in range(P):
                    k = k1 if j + 1 == P else f32(k0 + (k1 - k0) * ((j + 1) / P))
                    if step and k != cur:
                        r.call(DSP["an_reshape"], st, fargs=(cur, k))
                    cur = k
                    r.call(DSP["an_render"], st, out + 4 * j * (N // P), N // P, ev, sargs=(0,), fargs=(f32(130.81 / DEV_RATE), k))
                kp = k1
                xs += struct.unpack(f"<{N}f", r.u.mem_read(out, 4 * N))
            return xs
        ref, ramp, step = run(1), run(32), run(0)
        rms = lambda x: math.sqrt(sum((a - b) ** 2 for a, b in zip(x[N:], ref[N:])) / (len(x) - N))
        self.assertLess(rms(ramp), rms(step) / 5, f"ramp {rms(ramp):.2e}, a step per block {rms(step):.2e}")

    def test_no_modulation_is_bit_identical(self):
        """No source routed, a routed source at 0, and the Position knob at any value: Shape as stored, bit for bit, and
        nothing counted."""
        plain, r0 = self.run_(70)
        zero, r1 = self.run_(70, 0.0)
        knob, _ = self.run_(70, 0.0, base=200)
        same(self, plain, zero, "a routed source at 0")
        same(self, plain, knob, "the knob alone does not move Shape")
        self.assertEqual((stat(r0, 15), stat(r1, 15)), (0, 0))

    def test_a_wavetable_oscillator_is_untouched(self):
        """Position modulation on a Wavetable oscillator is stock's (its render reads the parameter as stock)."""
        def run(stock):
            r = Rig(img=STOCK_IMG if stock else IMG, dev=True); root, v = r.voice(0x73); o = r.osc(v, 0)
            r.tabled(o); r.level(v, 0, 1.0); r.note(v, 60)
            src = route(r, o, 0, 1.0)
            outs = []
            for b in range(8):
                r.wf(src + 4, 0.1 * b)
                if not stock:
                    r.call(DSP["an_enter"], root, v)
                out, od = r.buf(DEV_FRAMES); scr, _ = r.buf(DEV_FRAMES)
                r.call(r.r32(r.r32(o)) & ~1, o, scr, out)
                outs += struct.unpack(f"<{DEV_FRAMES}f", r.u.mem_read(od, 4 * DEV_FRAMES)); r.block()
            return outs, bytes(r.u.mem_read(o, 0x1F0))
        (a, ka), (b, kb) = run(False), run(True)
        same(self, a, b, "the output")
        same(self, ka, kb, "the object")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Voice(unittest.TestCase):
    """T2: stock's whole Wavetable voice (root ctor, voices from the named allocator, the per-voice render through the
    root's vtable word), Analog against the same voice with the track not Analog."""
    @classmethod
    def setUpClass(cls):
        r = cls.r = Rig()
        pool = [0xC5F10000]                                   # harness bump allocator for the 10 voices  # addr-ok: harness
        def alloc(uc, a, size, _):
            n = uc.reg_read(UC_ARM_REG_R2); p = pool[0]; pool[0] += (n + 31) & ~31
            uc.mem_write(p, bytes(n)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        r.u.hook_add(UC_HOOK_CODE, alloc, begin=S.NAMED_ALLOC, end=S.NAMED_ALLOC)
        cls.root = r.alloc(0x2F0)
        r.call(S.WT_ROOT_CTOR, cls.root)
        r.w32(cls.root + S.ROOT_HANDLE, 0x4242)
        cls.voices = [r.r32(cls.root + 0x14 + 4 * i) for i in range(10)]
        for v in cls.voices:
            for o in (S.VOICE_WT1, S.VOICE_WT2):
                r.w32(v + o + 0x18, r.nowav)

    def render_voice(self, v, n=FRAMES):
        r = self.r
        out, od = r.buf(n); scr, _ = r.buf(n); mix, md = r.buf(n)
        slot18 = r.r32(r.r32(self.root) + 0x48)
        _, c = r.call(slot18 & ~1, self.root, v, 0, scr, sargs=(out, 0, mix))
        return list(struct.unpack(f"<{n}f", r.u.mem_read(md, 4 * n))), list(struct.unpack(f"<{n}f", r.u.mem_read(od, 4 * n))), c

    def test_root_ctor_builds_ten_voices_with_our_oscillator_class(self):
        self.assertEqual(len(set(self.voices)), 10)
        r = self.r
        for v in self.voices:
            self.assertEqual(r.r32(v + 0x178), v + S.VOICE_WT1)
            self.assertEqual(r.r32(v + 0x17C), v + S.VOICE_WT2)
            self.assertEqual(r.r32(v + 0x180), v + S.VOICE_SUB)
        self.assertEqual(r.r32(self.root + 0x48 + 0) if False else r.r32(r.r32(self.root) + 0x48), DSP["tramp_voice"] | 1)

    def test_voice_cost_and_output(self):
        r = self.r
        v = self.voices[0]
        r.note(v, 60)
        r.table(0, 0x4242, 0, root=self.root)
        _, _, native = self.render_voice(v); r.block()
        r.table(0, 0x4242, 1, root=self.root)                  # stock's root sends Type to every voice's WT1 / WT2
        counts, outs = [], []
        for _ in range(8):
            mix, out, c = self.render_voice(v); r.block(); counts.append(c); outs += out
        self.assertGreater(max(map(abs, outs)), 0.05, "our oscillators reached stock's voice buffer")
        per = sorted(counts)[len(counts) // 2]
        print(f"\n  T2 voice-block (instructions, n={FRAMES}): native (no table) {native}, Analog saw {per}, "
              f"Analog extra {per - native}", file=sys.stderr)
        self.assertLess(per - native, 20000, "Analog's cost per voice-block (two oscillators), upper bound")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Cost(unittest.TestCase):
    """Instructions per voice-block of our hooks (an_enter + WT1 + WT2) for each regime; upper bounds."""
    BOUND = 12000
    DEV_BOUND = 2 * BOUND            # the device's 2N block

    def cost(self, shape, flags=1, n=None, dev=False, note=60, mod=None):
        r = Rig(dev=dev); root, v = r.voice(0x31); r.table(0, 0x31, flags); r.note(v, note)
        r.shape(v, 0, shape); r.shape(v, 1, shape)
        if mod is not None:
            for i in (0, 1):
                r.wf(route(r, r.osc(v, i), 0, 1.0) + 4, mod)
        totals = []
        for _ in range(6):
            _, c = r.render(root, v, n, (0, 1)); r.block(); totals.append(sum(c))
        return sorted(totals)[len(totals) // 2], r

    def test_cost_per_regime(self):
        for dev, bound in ((False, self.BOUND), (True, self.DEV_BOUND)):
            rows = []
            for name, shape, flags, note, mod in (("saw", 0.0, 1, 60, None), ("mixed", 0.25, 1, 60, None),
                                                  ("square", 0.5, 1, 60, None), ("pulse", 1.0, 1, 60, None),
                                                  ("saw+sync", 0.0, 3, 60, None), ("mixed+sync", 0.25, 3, 60, None),
                                                  ("saw+sync note 120", 0.0, 3, 120, None),
                                                  ("square+shape mod", 0.5, 1, 60, 0.1)):
                c, r = self.cost(shape, flags, dev=dev, note=note, mod=mod)
                rows.append((name, c))
                self.assertLess(c, bound, name)
            print(f"\n  hooks per voice-block (instructions, n={r.frames}): " + ", ".join(f"{n} {c}" for n, c in rows),
                  file=sys.stderr)
            st = struct.unpack("<16I", r.u.mem_read(SA + SA_STATS, 64))
            self.assertGreater(st[0], 0, "blocks counted")
            self.assertEqual(st[3], 2, "osc_peak: two oscillators per block")

    def test_cost_of_mixed_sync(self):
        """WT2's render (an_osc for an Analog WT2 next to stock's WT1), device block: Sync Off -> On at master notes 24..120."""
        rows = []
        for note in (24, 60, 96, 120):
            per = {}
            for sync in (0, 1):
                m = Mixed(sync=sync); m.note(note)
                for _ in range(8): m.block()
                cs = sorted(c[1] for c in m.counts[2:]); per[sync] = cs[len(cs) // 2]
                w1 = sorted(c[0] for c in m.counts[2:])[3]
            rows.append(f"note {note}: off {per[0]} on {per[1]} (+{per[1] - per[0]}; stock WT1 {w1})")
            self.assertLess(per[1], self.DEV_BOUND, f"note {note}")
        print("\n  mixed sync, WT2 render per voice-block (instructions, n=256): " + "; ".join(rows), file=sys.stderr)


def f32le(xs): return struct.pack(f"<{len(xs)}f", *xs)


# the identity set's stats words (SaStats): osc_peak, n_max, sync_skipped, voices_peak, renders, misses, synced, shape_mods
# (blocks / sum16 / peak are cycle figures; props counts our keys, three more of them on P3)
ID_STATS = (3, 5, 6, 11, 12, 13, 14, 15)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class P3Identity(unittest.TestCase):
    """P3 M1 / 6.2 (docs/plan-p3-fm-bits.md, review R17): with every FM amount 0 and Bit Depth Full, this build plays the
    identity set exactly as BE4.40. Each case runs twice from the same start, on this build's image and on the BE4.40
    reference build's (BE_REF: its image with its own DSP symbols, replayed: no stored capture), with the same calls in the
    same order, our three new keys pushed at 0 on both (BE4.40's an_prop does not know them: stock's slot 8 ignores them).
    Compared byte for byte: every output sample, the whole voice afterwards (both oscillators with their kernel areas, the
    sub), the stats words that are not cycles, and for mixed sync the master's wraps. The set is representative of the Cost, Voice,
    Sync, Shape-mod and Glide cases with Shape >= 45 (whole and 1/512-modulated), each in Rig() and Rig(dev=True) (plan, Q1
    as built, lists what it leaves out); glide in Rig() only: Q0 (OD1) changed the dev rig's glide time on purpose, and
    test_glide_dev_is_the_expected_change pins that difference instead. test_q1_plays_no_amount repeats part of it with
    the three amounts set: stored, not played. Instructions: this build within +2 % of BE4.40 per case (M1).
    test_the_harness_goes_red is the control: a one-ulp change of one sample, and of one input, are both seen."""
    @classmethod
    def setUpClass(cls):
        if not REF_OK:
            raise unittest.SkipTest(f"no BE4.40 reference build at {REF} (BE_REF)")
        cls.costs = []

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "costs", None):
            worst = max(cls.costs, key=lambda c: c[1] / c[2])
            print(f"\n  P3Identity: {len(cls.costs)} cases, instructions this build / BE4.40: worst {worst[1] / worst[2]:.4f} "
                  f"({worst[0]}: {worst[1]} / {worst[2]}), total {sum(c[1] for c in cls.costs) / sum(c[2] for c in cls.costs):.4f}",
                  file=sys.stderr)

    # Sub FM 70 %, Bit Depth Full, FM 1>2 33.3 %: test_q1_plays_no_amount. P3 Q2: Bit Depth 2 (8 bits) was in it through Q1;
    # it plays since Q2, so it left this tuple for BitDepth.test_the_amounts_cases_crush_and_nothing_else (same cases)
    AMOUNTS = (700, 0, 333)
    # the kernel-area bytes of P3's three settings (AnProps bits, subfm, fm12): stored on this build, never on BE4.40
    P3_BYTES = (PROPS_AT + 3, PROPS_AT + 6, PROPS_AT + 7, PROPS_AT + 8, PROPS_AT + 9)

    @staticmethod
    def push(r, osc, handle, index, vals, p3=(0, 0, 0)):
        """A push as stock sends it: Type first, then the rest in template order; P3's three (Sub FM, Bit Depth, FM 1>2) at
        p3, by default 0 (no FM, Full)."""
        for k, v in zip((K_TYPE, K_SHAPE, K_SYNC, K_GLIDE, K_SUBFM, K_BITS, K_FM12), tuple(vals) + tuple(p3)):
            r.prop(osc, k, v, handle, index)

    @classmethod
    def masked(cls, voice, oscs):
        """The voice's bytes with P3's settings bytes of the Analog oscillators oscs zeroed (test_q1_plays_no_amount)."""
        b = bytearray(voice)
        for i in oscs:
            for at in cls.P3_BYTES:
                b[(S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL + at] = 0
        return bytes(b)

    def voice_case(self, r, shape=(48, 48), sync=0, glide=0, notes=None, blocks=8, levels=None, mod=None, n=None, level0=1.0,
                   types=(1, 1), p3=(0, 0, 0)):
        """p3: P3's three for both oscillators, or a pair of them (OSC1's, OSC2's: BitDepth's per-oscillator cases)."""
        root, v = r.voice(0x5A)
        r.v = v                                                  # level_taps tells the oscillators apart by it
        pp = p3 if isinstance(p3[0], tuple) else (p3, p3)
        for i in (0, 1):
            self.push(r, r.osc(v, i), 0x5A, i, (types[i], shape[i], sync if i else 0, glide if i == 0 else 0), pp[i])
        r.stored = [r.settings(v, i) for i in (0, 1)]
        r.w32(r.osc(v, 1) + 0x0C, 7000)                          # WT2 a fifth up
        r.level(v, 0, level0); r.level(v, 1, 1.0)
        srcs = [route(r, r.osc(v, i), 0, 1.0) for i in (0, 1)] if mod else []
        notes = notes or {0: 60}
        out, cyc = [], 0
        for b in range(blocks):
            if b in notes:
                r.note(v, notes[b])
            for i, l in enumerate(levels(b) if levels else ()):
                r.level(v, i, l)
            for i, src in enumerate(srcs):
                r.wf(src + 4, mod(b, i))
            xs, c = r.render(root, v, n, (0, 1)); out += xs; cyc += sum(c); r.block()
        st = struct.unpack("<16I", r.u.mem_read(SA + SA_STATS, 64))
        return f32le(out), bytes(r.u.mem_read(v, 0xB88)), [st[i] for i in ID_STATS], cyc

    def root_case(self, r, p3=(0, 0, 0), blocks=8):
        """Voice's path (T2): stock's root ctor builds the ten voices; the table line and P3's three reach them through
        stock's root (WT_ROOT_PROPERTY); voice 0 renders through the root's vtable word 0x48 (stock's per-voice render into
        our tramp_voice). Both the voice's mix and its output are compared."""
        pool = [0xC5F10000]                                   # harness bump allocator for the 10 voices  # addr-ok: harness
        def alloc(uc, a, size, _):
            k = uc.reg_read(UC_ARM_REG_R2); p = pool[0]; pool[0] += (k + 31) & ~31
            uc.mem_write(p, bytes(k)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        r.u.hook_add(UC_HOOK_CODE, alloc, begin=S.NAMED_ALLOC, end=S.NAMED_ALLOC)
        root = r.alloc(0x2F0)
        r.call(S.WT_ROOT_CTOR, root)
        r.w32(root + S.ROOT_HANDLE, 0x4242)
        voices = [r.r32(root + 0x14 + 4 * i) for i in range(10)]
        for vo in voices:
            for o in (S.VOICE_WT1, S.VOICE_WT2):
                r.w32(vo + o + 0x18, r.nowav)
        v = voices[0]
        r.note(v, 60)
        r.table(0, 0x4242, 1, root=root)
        for k, x in zip((K_SUBFM, K_BITS, K_FM12), p3):
            for i in (0, 1):
                r.root_prop(root, k, x, i)
        r.stored = [r.settings(v, i) for i in (0, 1)]
        n = r.frames
        out, cyc = [], 0
        for b in range(blocks):
            o, od = r.buf(n); scr, _ = r.buf(n); mix, md = r.buf(n)
            _, c = r.call(r.r32(r.r32(root) + 0x48) & ~1, root, v, 0, scr, sargs=(o, 0, mix))
            out += struct.unpack(f"<{n}f", r.u.mem_read(md, 4 * n)) + struct.unpack(f"<{n}f", r.u.mem_read(od, 4 * n))
            cyc += c; r.block()
        st = struct.unpack("<16I", r.u.mem_read(SA + SA_STATS, 64))
        return f32le(out), bytes(r.u.mem_read(v, 0xB88)), [st[i] for i in ID_STATS], cyc

    def mixed_case(self, r, shape=48, blocks=24, p3=(0, 0, 0)):
        m = Mixed(r=r, shape=shape, frames=r.frames)
        for k, x in zip((K_GLIDE, K_SUBFM, K_BITS, K_FM12), (0,) + tuple(p3)):
            m.prop(k, x)
        r.stored = [r.settings(m.v, i) for i in (0, 1)]
        m.note(60)
        cyc = 0
        for b in range(blocks):
            if b == 12:
                m.note(64)
            m.block((1 / 12) * math.sin(b / 4)); cyc += m.counts[-1][1]
        st = struct.unpack("<16I", r.u.mem_read(SA + SA_STATS, 64))
        return f32le(m.x1 + m.x2), bytes(r.u.mem_read(m.v, 0xB88)) + f32le(m.wr), [st[i] for i in ID_STATS], cyc

    def check(self, name, run, dev, mask=()):
        """mask: the Analog oscillators whose P3 settings bytes (stored on this build, not on BE4.40) the voice leaves out."""
        a = run(Rig(dev=dev))
        b = run(Rig(img=REF_IMG, dev=dev, dsp=REF_DSP))
        self.assertGreater(max(abs(x) for x in struct.unpack(f"<{len(b[0]) // 4}f", b[0])), 0.05, f"{name}: control, it plays")
        same(self, a[0], b[0], f"{name}: the output")
        same(self, self.masked(a[1], mask), self.masked(b[1], mask),
             f"{name}: the voice (both oscillators, their kernel areas, the sub)")
        self.assertEqual(a[2], b[2], f"{name}: the stats words")
        self.costs.append((name, a[3], b[3]))
        self.assertLessEqual(a[3], b[3] * 1.02, f"{name}: instructions {a[3]} vs BE4.40's {b[3]} (M1: +2 %)")

    def test_free_running_shapes(self):
        """Cost / Voice: both oscillators Analog, Shape pairs across 45..127 (the saw plateau's new edge 45, the anchors; P3
        Q3: every whole Shape 45..51, so a saw plateau centred on 48 (6.6) is seen); and Cost's square with Shape modulation
        (a constant 0.1 on Position)."""
        for dev in (False, True):
            for shape in ((45, 127), (48, 88), (64, 100), (88, 48), (100, 64), (127, 45), (46, 49), (47, 50), (51, 45)):
                with self.subTest(dev=dev, shape=shape):
                    self.check(f"free {shape} dev={dev}", lambda r: self.voice_case(r, shape=shape), dev)
            with self.subTest(dev=dev, case="square + shape mod"):
                self.check(f"square+shape mod dev={dev}",
                           lambda r: self.voice_case(r, shape=(88, 88), mod=lambda b, i: 0.1, blocks=6), dev)

    def test_osc2_wavetable(self):
        """OSC1 Analog next to a Wavetable OSC2 (stock's render with no WAV: silent; its kernel area, stock's history, compared)."""
        for dev in (False, True):
            with self.subTest(dev=dev):
                self.check(f"osc2 wavetable dev={dev}", lambda r: self.voice_case(r, shape=(64, 48), types=(1, 0)), dev)

    def test_through_stocks_root(self):
        """Voice (T2): stock's root ctor, root property and per-voice render (vtable 0x48), both oscillators Analog."""
        for dev in (False, True):
            with self.subTest(dev=dev):
                self.check(f"root voice dev={dev}", lambda r: self.root_case(r), dev)

    def test_both_analog_sync(self):
        """Sync: WT1 the master; WT1 silent (E3: it only tracks its phase); WT2 silent for some blocks (the skip path, which
        P3 makes reset msub / m12 too); a 2 x 2N block (sync skipped and counted)."""
        cases = (("sync", dict(shape=(48, 48), sync=1)), ("sync shapes", dict(shape=(70, 100), sync=1)),
                 ("sync silent master", dict(shape=(48, 88), sync=1, level0=0.0)),
                 ("sync note 120", dict(shape=(48, 48), sync=1, notes={0: 120})),
                 ("sync slave gap", dict(shape=(100, 48), sync=1, levels=lambda b: (1.0, 0.0 if 3 <= b < 6 else 1.0))))
        for dev in (False, True):
            for name, kw in cases:
                with self.subTest(dev=dev, case=name):
                    self.check(f"{name} dev={dev}", lambda r: self.voice_case(r, **kw), dev)
        for dev in (False, True):
            with self.subTest(dev=dev, case="sync n=512"):
                self.check(f"sync n=512 dev={dev}",
                           lambda r: self.voice_case(r, shape=(48, 48), sync=1, n=2 * DEV_FRAMES, blocks=4), dev)

    def test_mixed_sync(self):
        """Sync (Track S): WT1 Wavetable with a table and vibrato, WT2 Analog synced to it, a new note mid-way."""
        for dev in (False, True):
            for shape in (48, 100):
                with self.subTest(dev=dev, shape=shape):
                    self.check(f"mixed {shape} dev={dev}", lambda r: self.mixed_case(r, shape), dev)

    def test_shape_modulation(self):
        """Shape-mod (J4): an LFO on each oscillator's Position, the Shape held to 1/512 of a step, ramped over each block;
        it stays at or above 48 here (P3 Q3 changes what plays below 45)."""
        mod = lambda b, i: 0.15 * math.sin(b / 2 + i) + 0.05
        for dev in (False, True):
            for name, kw in (("mod", dict(shape=(100, 70))), ("mod sync", dict(shape=(90, 110), sync=1))):
                with self.subTest(dev=dev, case=name):
                    self.check(f"{name} dev={dev}", lambda r: self.voice_case(r, mod=mod, blocks=10, **kw), dev)

    GLIDE = dict(shape=(48, 64), glide=300, notes={0: 60, 3: 67, 7: 55}, blocks=11)

    def test_glide(self):
        """Glide: Glide 300 (0.6 s) from the track's last note, a slide up and one down; Rig() only (plan 6.2): Q0 (OD1)
        changed the dev rig's glide time on purpose (test_glide_dev_is_the_expected_change)."""
        self.check("glide dev=False", lambda r: self.voice_case(r, **self.GLIDE), False)

    def test_glide_dev_is_the_expected_change(self):
        """P3 Q0 (OD1, plan 1 M1 / 6.2): the dropped identity case, as the change it now is. The same glide case in
        Rig(dev=True) on both builds: the blocks before the first slide (0..2) are byte-identical, the first difference is
        in block 3 (WT1 decays its glide after its own render, so WT2 is the first to hear it), and after the last slide
        (block 7, 67 -> 55, +1 octave, then 4 decays) WT1's glide is (0.001 ^ (2N / (0.6 s * 96 kHz))) ^ 4 on this build
        and the square of that, twice as far gone, on BE4.40 (ln to 4 x 5e-5: stock's exp2 table, as in
        Oscillator.test_glide_takes_the_set_time_on_the_device). Q1's amounts control, on this build alone: the same case
        with Sub FM and FM 1>2 set plays it byte for byte (stored, not played; the settings bytes masked). Q2: Bit Depth
        plays now, so it is Full here (AMOUNTS); its dev glide case is in BitDepth.test_the_amounts_cases_crush_and_nothing_else."""
        rp = Rig(dev=True)
        runs = [self.voice_case(Rig(dev=True), **self.GLIDE),
                self.voice_case(Rig(img=REF_IMG, dev=True, dsp=REF_DSP), **self.GLIDE),
                self.voice_case(rp, **self.GLIDE, p3=self.AMOUNTS)]
        (a, va, sa, ca), (b, vb, sb, cb), (p, vp, sp, _) = runs
        for i in (0, 1):
            s = rp.stored[i]
            self.assertEqual((s[3], s[5], s[6]), (0, 700, 333), f"control, OSC{i + 1}'s Bit Depth, Sub FM, FM 1>2 as stored")
        same(self, p, a, "amounts set: the output")
        same(self, self.masked(vp, (0, 1)), self.masked(va, (0, 1)), "amounts set: the voice")
        self.assertEqual(sp, sa, "amounts set: the stats words")
        block = 4 * DEV_FRAMES
        same(self, a[:3 * block], b[:3 * block], "blocks 0..2 (no slide yet)")
        first = next((i for i in range(0, len(a), 4) if a[i:i + 4] != b[i:i + 4]), None)
        self.assertIsNotNone(first, "the dev rig's glide differs from BE4.40's (OD1)")
        self.assertEqual(first // block, 3, "the first difference is in the first slide's block")
        at = S.VOICE_WT1 + S.OSC_KERNEL + 144                     # AnOsc glide (Rig.state)
        g = [struct.unpack_from("<f", x, at)[0] for x in (va, vb)]
        ln_c = math.log(0.001) * DEV_FRAMES / (300 * 0.002 * DEV_RATE)   # one block's decay, exact
        self.assertAlmostEqual(math.log(g[0]), 4 * ln_c, delta=4 * 5e-5, msg=f"this build: {g[0]} vs {math.exp(4 * ln_c)}")
        self.assertAlmostEqual(math.log(g[1]), 8 * ln_c, delta=4 * 5e-5, msg=f"BE4.40: {g[1]} vs {math.exp(8 * ln_c)}")
        self.assertEqual(sa, sb, "the stats words")
        self.costs.append(("glide dev=True (changed)", ca, cb))
        self.assertLessEqual(ca, cb * 1.02, f"instructions {ca} vs BE4.40's {cb} (M1: +2 %)")

    def test_q1_plays_no_amount(self):
        """P3 Q1 (review): the new settings are stored and play nothing yet. Sub FM 700 and FM 1>2 333 on every Analog
        oscillator, pushed alike to both builds (BE4.40's slot 8 ignores the keys), and this build still plays BE4.40 byte
        for byte; only the stored settings bytes differ (masked). Control: this build stored them. Glide in Rig() only
        since Q0 (OD1: the dev rig's glide time changed on purpose; its amounts control is in
        test_glide_dev_is_the_expected_change). Q2 made Bit Depth's part its expected change: Bit Depth 2 left AMOUNTS,
        and the same seven cases with it, in both rigs, are BitDepth.test_the_amounts_cases_crush_and_nothing_else. Q5
        (Sub FM) and Q6 (FM 1>2) turn the rest into theirs (plan 7)."""
        p3 = self.AMOUNTS
        cases = (("free", lambda r: self.voice_case(r, shape=(48, 88), p3=p3), (0, 1)),
                 ("sync", lambda r: self.voice_case(r, shape=(70, 100), sync=1, p3=p3), (0, 1)),
                 ("sync silent master", lambda r: self.voice_case(r, shape=(48, 88), sync=1, level0=0.0, p3=p3), (0, 1)),
                 ("glide", lambda r: self.voice_case(r, **self.GLIDE, p3=p3), (0, 1)),
                 ("osc2 wavetable", lambda r: self.voice_case(r, shape=(64, 48), types=(1, 0), p3=p3), (0,)),
                 ("root voice", lambda r: self.root_case(r, p3=p3), (0, 1)),
                 ("mixed", lambda r: self.mixed_case(r, 48, p3=p3), (1,)))
        for dev in (False, True):
            for name, run, oscs in cases:
                if dev and name == "glide":
                    continue                                    # Q0: the dev rig's glide is an expected change (above)
                with self.subTest(dev=dev, case=name):
                    def stored(r, run=run, oscs=oscs, name=name):
                        x = run(r)
                        if r.dsp is DSP:
                            for i in oscs:
                                s = r.stored[i]
                                self.assertEqual((s[3], s[5], s[6]), (0, 700, 333),
                                                 f"{name}: control, OSC{i + 1}'s Bit Depth, Sub FM, FM 1>2 as stored")
                        return x
                    self.check(f"amounts {name} dev={dev}", stored, dev, mask=oscs)

    def test_the_harness_goes_red(self):
        """The control (plan 6.2): the comparison fails on a one-ulp change of one output sample, and the replay itself
        sees a one-ulp change of one input (WT1's level source 1.0 -> the next float32 up) in its output."""
        a = self.voice_case(Rig(img=REF_IMG, dsp=REF_DSP))
        bad = bytearray(a[0]); w = struct.unpack_from("<I", bad, 400)[0]; struct.pack_into("<I", bad, 400, w + 1)
        with self.assertRaises(AssertionError):
            same(self, bytes(bad), a[0], "one ulp")
        up = 1.0 + 2.0 ** -23                                   # the next float32 above 1.0
        self.assertEqual(f32(up), up)
        b = self.voice_case(Rig(img=REF_IMG, dsp=REF_DSP), level0=up)
        self.assertNotEqual(a[0], b[0], "an input one ulp off changes the output")
        c = self.voice_case(Rig(img=REF_IMG, dsp=REF_DSP))
        self.assertEqual(a, c, "and the replay is deterministic")


OSC_SMOOTH = 0xF8           # the oscillator's level smoother (analog.cpp OSC_SMOOTH): SMOOTH_APPLY's r0


def level_taps(r, inject=None):
    """P3 Q2: every block our DSP hands to Level (stock's SMOOTH_APPLY (smoother, s, 0, n) called from the DSP window; stock's
    own Wavetable render calls it too, from its own code, and is left out), as float32 arrays in call order. inject(x, i) -> y:
    y is written over s before Level runs (the golden crush, put where the build's crush is); i is the oscillator (0 OSC1,
    1 OSC2, by r0 = osc + OSC_SMOOTH against r.v, which voice_case sets; None where r has no r.v).
    P3 Q2b: the list also carries, per block, .osc (that i), .calls (the an_crush calls that wrote into it since the last
    block: (offset, n, bits, dc), dc from s0 at an_crush's entry, as float32) and .dc (one dc per sample, 0 where no call
    wrote: a Full oscillator). an_crush called from outside a block's range is in .stray (none is expected)."""
    got, lo, hi = Taps(), S.M7_DSP, S.M7_DSP_LIMIT
    pending = []
    def c(uc, a, size, _):
        pending.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2),
                        np.frombuffer(struct.pack("<I", uc.reg_read(UC_ARM_REG_S0)), dtype="<f4")[0]))
    def h(uc, a, size, _):
        if not lo <= (uc.reg_read(UC_ARM_REG_LR) & ~1) < hi:
            return
        p, n = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R3)
        x = np.frombuffer(bytes(uc.mem_read(p, 4 * n)), dtype="<f4").copy()
        osc, v = uc.reg_read(UC_ARM_REG_R0) - OSC_SMOOTH, getattr(r, "v", None)
        i = None if v is None else next((k for k in (0, 1) if osc == r.osc(v, k)), "?")
        calls, d = [], np.zeros(n, dtype=np.float32)
        for cp, cn, cb, cd in pending:
            if p <= cp < p + 4 * n:
                calls.append(((cp - p) // 4, cn, cb, cd)); d[(cp - p) // 4:(cp - p) // 4 + cn] = cd
            else:
                got.stray.append((cp, cn, cb, cd))
        pending.clear()
        got.append(x); got.osc.append(i); got.calls.append(calls); got.dc.append(d)
        if inject:
            uc.mem_write(p, np.asarray(inject(x, i), dtype="<f4").tobytes())
    if "an_crush" in r.dsp:
        r.u.hook_add(UC_HOOK_CODE, c, begin=r.dsp["an_crush"] & ~1, end=r.dsp["an_crush"] & ~1)
    r.u.hook_add(UC_HOOK_CODE, h, begin=S.SMOOTH_APPLY, end=S.SMOOTH_APPLY)
    return got


class Taps(list):
    """level_taps' blocks, with each block's oscillator, an_crush calls and per-sample dc (P3 Q2b)."""
    def __init__(self):
        super().__init__(); self.osc, self.calls, self.dc, self.stray = [], [], [], []


def dc_feed(taps):
    """inject's dc source for a Full run that replays a crushed one: the crushed run's per-sample dc of each block, in the
    same Level-call order (the cases assert both runs hand Level as many blocks)."""
    it = iter(taps.dc)
    return lambda: next(it)


def dc_of(k):
    """The pulse's DC term at kernel coordinate k, float64, from the kernel's stated law (analog_kernel.cpp header, plan
    4.7): width = 0.5 - 0.84 (k - 0.5) above 0.5 (k clamped to 1), dc = 2 width - 1; 0 at and below 0.5 (the saw, the
    square, LOW). The independent check of an_dc (within float32 rounding)."""
    k = min(float(k), 1.0)
    return 0.0 if not k > 0.5 else 2 * (0.5 - 0.84 * (k - 0.5)) - 1


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class BitDepth(unittest.TestCase):
    """P3 Q2 (docs/plan-p3-fm-bits.md 4.7, OD11 = (b), 6.3, 6.6): Bit Depth 12 / 8 / 4 is a two's-complement grid,
    q = clamp(rint(x S), -S, S - 1) / S, S = 2^(b-1), on the oscillator's 96 kHz block after the kernel and before Level.
    Checked against p3_golden.py (numpy, written from the plan alone) with tolerance 0: the same float32 input gives the
    same float32 q in every bit (S a power of two: the model's own argument), so the comparison is crush_bits ==, sign of
    zero included. Two levels: an_crush called on crafted blocks (every tie, -0.0, past +1 and below -1: the golden probe,
    which separates every wrong grid the model names), and the voice path, where the block at Level's input is taken with
    Bit Depth set and with it Full (level_taps). Placement and 'nothing else': the build with Full and the golden crush
    injected at Level's input plays the crushed build byte for byte (output, voice, stats).
    P3 Q2b (OD12 = (b)): the grid acts on x + dc and dc comes off after, dc the part's own (an_dc); the golden crush takes it
    as an argument (p3_golden: a spec change, the model follows OD12's text). In the voice path the dc of every crushed part
    is captured at an_crush's entry (level_taps .calls / .dc) and checked against dc_of (the stated law), and the Full
    replay injects the golden crush with that same dc."""

    def crush_on_m7(self, x, bits, dc=0.0):
        r = Rig()
        p = r.alloc(4 * len(x))
        r.u.mem_write(p, np.asarray(x, dtype="<f4").tobytes())
        _, c = r.call(DSP["an_crush"], p, len(x), bits, fargs=(dc,))
        return np.frombuffer(bytes(r.u.mem_read(p, 4 * len(x))), dtype="<f4").copy(), c

    @staticmethod
    def an_dc(k, r=None):
        """The M7's an_dc(k) (the part's dc an_osc hands an_crush), float32."""
        r = r or Rig()
        r.call(DSP["an_dc"], fargs=(k,))
        return float(np.frombuffer(struct.pack("<I", r.u.reg_read(UC_ARM_REG_S0)), dtype="<f4")[0])

    def test_an_crush_is_the_golden_grid(self):
        """Settings 1 / 2 / 3 (12 / 8 / 4 bits; an_prop clamps anything past 3 to 3, OD4): the golden probe, a sweep over
        -1.3..1.3 (every level and both clips), random blocks of 4 x 4096 (normal, sd 0.7); bit for bit. Each of the
        model's wrong grids (mid-tread without the clamp, the top clamped at S, roundf, truncation, a symmetric clip) differs
        from it on the probe (so on this build's output), and the levels are exactly 2^b. Setting 0 leaves the block
        alone.
        Q2b: each of those about dc 0 (Q2's grid, bit for bit: the same probe, sweep and random blocks), -0.75 (a dc of few
        bits, so the shifted probe still lands on every tie: every Q2 and OD12 mutant is separated) and the kernel's own dc
        at Shapes 100 and 127 (an_dc: the shifted probe's points move by an ulp, so OD12's mutants are what it separates);
        the sweep of x + dc has exactly 2^b values of q for one dc. Full leaves the block alone at any dc."""
        rng = np.random.default_rng(1404)
        dcs = (0.0, -0.75, self.an_dc(k_of(100)), self.an_dc(k_of(127)))
        self.assertTrue(dcs[2] < 0 and dcs[3] < dcs[2], dcs)
        for dc in dcs:
            for bits in (1, 2, 3):
                b = G.bits_of(bits)
                for name, x in (("probe", G.probe(b, dc)), ("sweep", np.linspace(-1.3, 1.3, 20001) - dc),
                                ("random", rng.standard_normal(4 * 4096) * 0.7)):
                    with self.subTest(dc=dc, bits=bits, case=name):
                        x = np.asarray(x, dtype=np.float32).astype(np.float64)
                        q, _ = self.crush_on_m7(x, bits, dc)
                        want = G.crush_bits(x, b, dc=dc)
                        self.assertTrue(np.array_equal(q.view(np.uint32), want),
                                        f"{b}-bit dc {dc}: {int(np.sum(q.view(np.uint32) != want))} samples differ")
                        if name == "probe":
                            exact_ties = dc in (0.0, -0.75)
                            for m, k in G.discriminates(x, b, dc).items():
                                if exact_ties or m.startswith("dc_"):
                                    self.assertGreater(k, 0, f"{b}-bit probe (dc {dc}) separates {m}")
                            top, bot = G.check_clip(x, q, b, dc=dc)
                            self.assertGreater(top, 0); self.assertGreater(bot, 0)
                        if name == "sweep":
                            self.assertEqual(G.check_levels(q, b, dc), 1 << b)
        for dc in dcs:
            x = G.probe(8)
            q, _ = self.crush_on_m7(x, 0, dc)
            self.assertTrue(np.array_equal(q.view(np.uint32), x.astype(np.float32).view(np.uint32)), f"Full: untouched (dc {dc})")

    def test_an_dc_is_the_kernels_dc(self):
        """Q2b: an_dc(k) is the DC term the kernel takes off its waveform, bit for bit: a free an_render at k (inc 0.001, so
        whole plateaus) plays exactly f32(1 - dc) on its high and f32(-1 - dc) on its low (wave<PULSE> / wave<MIXED>
        compute (+-1) - dc), for every whole Shape 89..127 and 1/512-held values next to 88; dc within 1e-6 of dc_of's
        stated law; exactly +0.0 at and below k 0.5 (the saw 48, Shape 70, 87.6 and 88 - 1/512 just under the knee, the
        square 88, LOW, NaN). Past k 1 (an_shape caps Shape at AN_SHAPE_MAX, so an_osc never passes it; set_shape clamps
        it, and so does an_dc): an_dc(k) is an_dc(1) bit for bit and still an_render's own plateaus at that k."""
        r = Rig()
        st, out = r.alloc(256), r.alloc(4 * 2048)

        def plateaus(k, dc):
            r.u.mem_write(st, struct.pack("<I", 0x4f4c4e41) + bytes(252))
            r.call(DSP["an_render"], st, out, 2048, 0, sargs=(0,), fargs=(0.001, k))
            y = np.frombuffer(bytes(r.u.mem_read(out, 4 * 2048)), dtype="<f4")[1:]
            vals, cnt = np.unique(y, return_counts=True)
            top2 = sorted(vals[np.argsort(cnt)[-2:]])
            self.assertEqual([float(v) for v in top2], [float(np.float32(-1.0) - np.float32(dc)), float(np.float32(1.0) - np.float32(dc))])
        for sh in [88 + 1 / 512, 88 + 2 / 512] + list(range(89, 128)):
            k = f32(k_of(sh)); dc = self.an_dc(k, r)
            with self.subTest(shape=sh):
                self.assertAlmostEqual(dc, dc_of(k), delta=1e-6)
                self.assertLess(dc, 0.0)
                plateaus(k, dc)
        top = struct.pack("<f", self.an_dc(1.0, r))
        for k in (f32(1.0 + 2 ** -23), 1.25, 2.0, float("inf")):          # (k past 1: the clamp)
            with self.subTest(k=k):
                dc = self.an_dc(k, r)
                self.assertEqual(struct.pack("<f", dc), top)
                plateaus(k, dc)
        for k in (0.0, 0.25, f32(k_of(87.6)), f32(k_of(88 - 1 / 512)), 0.5, -0.5, -1.0, float("nan")):   # (just under the knee too)
            d = self.an_dc(k)
            self.assertEqual(struct.pack("<f", d), struct.pack("<f", 0.0), k)

    # the amounts cases (P3Identity.test_q1_plays_no_amount's seven), here with Bit Depth set
    push = staticmethod(P3Identity.push)
    masked = P3Identity.masked
    voice_case, root_case, mixed_case, GLIDE = P3Identity.voice_case, P3Identity.root_case, P3Identity.mixed_case, P3Identity.GLIDE

    def cases(self, bits):
        p3 = (700, bits, 333)
        return (("free", lambda r: self.voice_case(r, shape=(48, 88), p3=p3), (0, 1)),
                ("sync", lambda r: self.voice_case(r, shape=(70, 100), sync=1, p3=p3), (0, 1)),
                ("sync silent master", lambda r: self.voice_case(r, shape=(48, 88), sync=1, level0=0.0, p3=p3), (0, 1)),
                ("glide", lambda r: self.voice_case(r, **self.GLIDE, p3=p3), (0, 1)),
                ("osc2 wavetable", lambda r: self.voice_case(r, shape=(64, 48), types=(1, 0), p3=p3), (0,)),
                ("root voice", lambda r: self.root_case(r, p3=p3), (0, 1)),
                ("mixed", lambda r: self.mixed_case(r, 48, p3=p3), (1,)))

    # Q2b: each case's Shapes (OSC1, OSC2), for the dc its crushed parts must carry (root voice: Type 1's reset Shape 48;
    # mixed: OSC2 48, OSC1 is stock's Wavetable)
    SHAPES = {"free": (48, 88), "sync": (70, 100), "sync silent master": (48, 88), "glide": P3Identity.GLIDE["shape"],
              "osc2 wavetable": (64, 48), "root voice": (48, 48), "mixed": (48, 48)}

    def check_dcs(self, name, taps, shapes, b, oscs=(0, 1)):
        """Q2b: every crushed block (of the oscillators oscs) is covered by its parts' an_crush calls end to end, at this
        b's setting, with no call outside a block; a held Shape gives one dc per block, within 1e-6 of dc_of(its Shape),
        the stated law (an_dc is pinned to the kernel's own dc by test_an_dc_is_the_kernels_dc). Where level_taps cannot
        tell the oscillator (no r.v), every Shape of the case must give the same dc. Returns the dcs seen."""
        self.assertEqual(taps.stray, [], f"{name}: an_crush only inside the blocks Level gets")
        setting, seen = {12: 1, 8: 2, 4: 3}[b], set()
        for j, (x, i, calls) in enumerate(zip(taps, taps.osc, taps.calls)):
            if i in (0, 1) and i not in oscs:
                continue                                         # (another oscillator's blocks: its own check)
            self.assertEqual(sum(c[1] for c in calls), len(x), f"{name}: block {j} crushed end to end")
            self.assertEqual([c[0] for c in calls], [sum(c[1] for c in calls[:m]) for m in range(len(calls))],
                             f"{name}: block {j}: the parts in order")
            self.assertTrue(all(c[2] == setting for c in calls), f"{name}: block {j} at setting {setting}")
            want = {dc_of(k_of(shapes[i]))} if i in (0, 1) else {dc_of(k_of(s)) for s in shapes}
            self.assertEqual(len(want), 1, f"{name}: the case's Shapes say one dc")
            self.assertEqual(len({float(c[3]) for c in calls}), 1, f"{name}: block {j}: a held Shape, one dc")
            self.assertAlmostEqual(float(calls[0][3]), want.pop(), delta=1e-6, msg=f"{name}: block {j} dc")
            seen.add(float(calls[0][3]))
        return seen

    def expected_change(self, name, at, dev, bits, b):
        """Case `at` three times on this build: crushed (Bit Depth `bits`), Full (the same case, Bit Depth 0), and Full with
        the golden crush injected at Level's input. Returns (Level-input blocks Full, crushed, instructions crushed, Full)."""
        _, run, oscs = self.cases(bits)[at]
        _, full, _ = self.cases(0)[at]
        rc, rf, ri = Rig(dev=dev), Rig(dev=dev), Rig(dev=dev)
        tc, tf = level_taps(rc), level_taps(rf)
        feed = dc_feed(tc)                                       # Q2b: the crushed run's dc per sample, block by block
        level_taps(ri, inject=lambda x, i: G.crush(x, b, dc=feed()))
        c = run(rc)
        self.check_dcs(name, tc, self.SHAPES[self.cases(bits)[at][0]], b)
        for i in oscs:
            self.assertEqual(rc.stored[i][3], min(bits & 0xFFFFFFFF, 3), f"{name}: control, OSC{i + 1}'s Bit Depth as stored")
        f, inj = full(rf), full(ri)
        for i in oscs:
            self.assertEqual(rf.stored[i][3], 0, f"{name}: control, OSC{i + 1} Full in the reference")
        self.assertEqual(len(tc), len(tf), f"{name}: as many renders reach Level")
        self.assertGreater(len(tc), 0, f"{name}: control, the case renders")
        for j, (y, x, d) in enumerate(zip(tc, tf, tc.dc)):
            got, want = y.view(np.uint32), G.crush_bits(x.astype(np.float64), b, dc=d)
            if not np.array_equal(got, want):
                k = int(np.flatnonzero(got != want)[0])
                self.fail(f"{name}: render {j}, sample {k}: {y[k]!r} vs golden {G.crush(x[k:k + 1], b, dc=d[k])[0]!r} "
                          f"(in {x[k]!r}, dc {d[k]!r})")
        self.assertNotEqual(c[0], f[0], f"{name}: Bit Depth {bits} changes the output")
        same(self, c[0], inj[0], f"{name}: the output = Full with the golden crush at Level's input")
        same(self, P3Identity.masked(c[1], oscs), P3Identity.masked(inj[1], oscs), f"{name}: the voice")
        self.assertEqual(c[2], inj[2], f"{name}: the stats words")
        n_bits = struct.unpack("<8I", rc.u.mem_read(SA + SA_FMSTATS, 32))
        self.assertEqual(n_bits, (0, len(tc), 0, 0, 0, 0, 0, 0), f"{name}: bits= counts every crushed render, fm= / fmx= nothing")
        self.assertEqual(struct.unpack("<8I", rf.u.mem_read(SA + SA_FMSTATS, 32)), (0,) * 8, f"{name}: Full counts nothing")
        return tf, tc, c[3], f[3]

    def test_the_amounts_cases_crush_and_nothing_else(self):
        """P3 Q2's expected change (Q1's amounts cases, plan 6.7 / 7): free, sync, a silent sync master (E3: only WT2
        crushes; Q6 crushes it for B, E3 (a)), glide (both rigs: the reference is this build), OSC2 Wavetable (stock's,
        untouched), stock's root voice, mixed sync, each in Rig() and Rig(dev=True), with Sub FM 700, Bit Depth 2, FM 1>2
        333; the free case also at Bit Depth 1, 3 and the out-of-range 7 and -1 (OD4: 4 bits). Each Level-input block is
        the golden crush of the Full block, bit for bit; the output, the voice and the stats are Full's with the golden crush
        injected there, byte for byte (so the crush is before Level, on every render, and changes nothing else); bits=
        counts the crushed renders. Instructions: recorded per case (the crush's cost; not M1's off budget)."""
        clip, cost = [0, 0], []
        for dev in (False, True):
            runs = [(2, at) for at in range(len(self.cases(2)))] + [(bits, 0) for bits in (1, 3, 7, -1)]
            for bits, at in runs:
                b, name = G.bits_of(bits), self.cases(bits)[at][0]
                with self.subTest(dev=dev, case=name, bits=bits):
                    tf, tc, cc, cf = self.expected_change(f"{name} bits={bits} dev={dev}", at, dev, bits, b)
                    for x, y, dc in zip(tf, tc, tc.dc):
                        t, d = G.check_clip(x.astype(np.float64), y.astype(np.float64), b, dc=dc)
                        clip[0] += t; clip[1] += d
                    cost.append((f"{name} bits={bits} dev={dev}", cc, cf, sum(len(x) for x in tc)))
        w = max(cost, key=lambda c: (c[1] - c[2]) / c[3])
        print(f"\n  BitDepth: {len(cost)} cases, crush cost per crushed sample: worst {(w[1] - w[2]) / w[3]:.2f} instructions "
              f"({w[0]}: {w[1]} vs Full {w[2]}, {w[3]} samples); clipped samples top {clip[0]}, bottom {clip[1]}", file=sys.stderr)
        # Q2b (OD12 (b), reviewed edit): the top is now reached by the +1 high itself (the square's, and the pulse's about
        # its dc), no longer by the DC-free pulse's 2 - 2w high
        self.assertGreater(clip[0], 0, "a case reaches the top (s + dc at the +1 high): top at (S-1)/S")

    def pair(self, shape, sync=0, note=60, bits=1, blocks=6, mod=None, dev=True):
        """Q2b: one voice case twice on this build, Bit Depth `bits` on both oscillators and Full: (Full taps, crushed taps).
        Both runs hand Level the same blocks in the same order (asserted), and Full calls an_crush nowhere (identity at
        Full: the Full path is Q1's, P3Identity compares it with BE4.40)."""
        rc, rf = Rig(dev=dev), Rig(dev=dev)
        tc, tf = level_taps(rc), level_taps(rf)
        kw = dict(shape=(shape, shape) if not isinstance(shape, tuple) else shape, sync=sync, notes={0: note}, blocks=blocks,
                  mod=mod)
        self.voice_case(rc, p3=(0, bits, 0), **kw); self.voice_case(rf, p3=(0, 0, 0), **kw)
        self.assertEqual(len(tc), len(tf)); self.assertGreater(len(tc), 0)
        self.assertEqual((tf.stray, sum(len(c) for c in tf.calls)), ([], 0), "Full: no an_crush call")
        return tf, tc

    PULSES = ((88, 0, 60), (89, 0, 60), (100, 0, 60), (110, 0, 72), (120, 0, 60), (127, 0, 60), (100, 1, 96), (127, 1, 115),
              (88, 1, 100))

    def test_real_blocks_sit_on_the_grids_range(self):
        """Q2b, retires Q2's test_real_blocks_reach_the_top_clip (a reviewed expected change, OD12 = (b)): Q2 pinned that the
        DC-free pulse's high (2 - 2w: 1.84 at Shape 127, 1.26 at 100) drove s far past +1, so every Bit Depth clipped it
        hard. Now the grid sees s + dc, the pulse on its own range: on real blocks (Shapes 88..127 free and synced, plus
        Q2's 48 / 70 synced, notes 60..115, Rig(dev=True)) s + dc stays within [-1, 1] (M: the measured ends are printed;
        the kernel's 2-point BLEP interpolates between the levels, so it never overshoots them), while s itself still goes
        past +1 (the reason for dc). The top is still reached at every b, by the +1 high itself (the square's and the pulse's
        about dc), so the voice path still sees a top clamped at S; the bottom tie is not passed (as in Q2: the -1 low lands
        on -S exactly), its cover stays the probe. s + dc is the float32 sum an_crush forms (dc captured at its entry)."""
        top, ends, raw = {b: 0 for b in G.BITS}, [], []
        for shape, sync, note in self.PULSES + ((48, 1, 108), (70, 1, 110)):
            tf, tc = self.pair(shape, sync, note)
            self.check_dcs(f"{shape}/{sync}/{note}", tc, (shape, shape), 12)
            x = np.concatenate(tf).astype(np.float32); d = np.concatenate(tc.dc)
            y = (x + d).astype(np.float64)                       # float32 add, as an_crush's
            ends.append((y.min(), y.max())); raw.append(float(x.max()))
            for b in G.BITS:
                top[b] += int(np.sum(y * G.scale(b) >= G.scale(b) - 0.5))
        lo, hi = min(e[0] for e in ends), max(e[1] for e in ends)
        print(f"\n  real blocks: s + dc in [{lo:.9f}, {hi:.9f}] (s alone up to {max(raw):.6f}); at the top per b {top}",
              file=sys.stderr)
        self.assertGreaterEqual(lo, -1.0, "s + dc: never below -1 (the bottom clamp is not reached)")
        self.assertLessEqual(hi, 1.0, "s + dc: never above +1 (only the +1 high itself meets the top)")
        self.assertGreater(max(raw), 1.8, "control: s alone (the DC-free pulse at 127) is far past +1")
        for b in G.BITS:
            self.assertGreater(top[b], 0, f"{b}-bit: a real block reaches the top")

    def test_12_and_8_bits_are_near_transparent_on_the_pulse(self):
        """Q2b (OD12 = (b)): at 12 and 8 bits the crushed pulse (Shapes 88..127, free and synced, Rig(dev=True)) is within
        one step of the Full one at every sample: |q - s| <= 1/S + 2^-23. Derived: y = s + dc rounds once (half an ulp of
        a value under 2, 2^-24); y stays within [-1, 1] (test_real_blocks_sit_on_the_grids_range), so rint moves it by at
        most 1/(2S), and the clip only moves the +1 high down to (S-1)/S, by 1/S; the - dc rounds once more (2^-24). So the
        largest error is the +1 high's one step, and the pulse keeps its peak to peak but for that step (1/2048 at 12 bits,
        1/128 at 8). Control: Q2's grid without dc (OD12 (a), p3_golden.crush with dc 0) is off by more than 0.2 on the same
        blocks for every Shape from 100 (the clipped high; measured worst printed)."""
        worst, old = {}, {}
        for shape, sync, note in self.PULSES:
            for bits in (1, 2):
                b = G.bits_of(bits); S = G.scale(b)
                tf, tc = self.pair(shape, sync, note, bits)
                x = np.concatenate(tf).astype(np.float64); q = np.concatenate(tc).astype(np.float64)
                e = float(np.max(np.abs(q - x)))
                with self.subTest(shape=shape, sync=sync, bits=bits):
                    self.assertLessEqual(e, 1 / S + 2.0 ** -23, f"{b}-bit Shape {shape}: worst |q - s| {e!r}")
                    pp = (x.max() - x.min()) - (q.max() - q.min())
                    self.assertLessEqual(abs(pp), 1 / S + 2.0 ** -22, f"{b}-bit Shape {shape}: peak to peak lost {pp!r}")
                    a = float(np.max(np.abs(G.crush(x, b) - x)))
                    if shape >= 100:
                        self.assertGreater(a, 0.2, f"control: OD12 (a) at Shape {shape} clips the high ({a!r})")
                worst[(shape, sync, b)] = e * S; old[(shape, sync, b)] = a
        print(f"\n  pulse, worst |q - s| in steps: {max(worst.values()):.6f}; OD12 (a) on the same blocks up to "
              f"{max(old.values()):.4f} (Shape 127 / 12 bits {old[(127, 0, 12)]:.4f})", file=sys.stderr)

    def test_the_crushed_pulse_keeps_its_mean(self):
        """Q2b (OD12 = (b): B stays DC-free): the crush moves the pulse's mean by about -f_hi / S only. Derived, per sample,
        with e = q - s: on the low plateau s = f32(-1 - dc), s + dc rounds to -1 within 2^-24, the level is -S, and q =
        f32(-1 - dc) = s: e = 0 exactly; on the high plateau s = f32(1 - dc), the level is S - 1 (the clip), and e = -1/S
        within 2^-23; on the edge samples (the BLEP's, a sync reset's) |e| <= 1/S + 2^-23 (the transparency bound). So with
        f_hi and f_edge the fractions of high and edge samples,
            |mean(e) + f_hi / S| <= f_edge (1/S + 2^-23) + f_hi 2^-23,
        and since f_hi <= w (the width: 8 % at Shape 127) the shift is at most about (w + f_edge) / S: -3.9e-5 at Shape 127
        / 12 bits (M), against Q2's (OD12 (a)) -0.070 on the same blocks (the clipped high). Under FM 1>2 (Q6, B = I inc1 q)
        the shift is a detune of I inc1 mean(q): at I = 8 about 3e-4 inc1 instead of -0.56. Not removed: the same -f_hi / S
        on the square (Shape 88, no dc), OD11 (b)'s asymmetric top, which at 4 bits is -0.065 (M, printed). Each case also
        checks the low plateau's e = 0 and the
        high's -1/S directly, and, from Shape 100, that Q2's form moves the mean further: over 100 / 10 / 1 times as far at
        12 / 8 / 4 bits (the figures are printed)."""
        rows = []
        for shape, sync, note in self.PULSES:
            for bits in (1, 2, 3):
                b = G.bits_of(bits); S = G.scale(b)
                tf, tc = self.pair(shape, sync, note, bits, blocks=8)
                x = np.concatenate(tf).astype(np.float64); q = np.concatenate(tc).astype(np.float64)
                d = np.concatenate(tc.dc); dc = float(d[0])
                with self.subTest(shape=shape, sync=sync, bits=bits):
                    self.assertTrue(np.all(d == dc), "a held Shape: one dc")
                    hi_v, lo_v = float(np.float32(1.0) - np.float32(dc)), float(np.float32(-1.0) - np.float32(dc))
                    hi, lo = x == hi_v, x == lo_v
                    edge = ~(hi | lo)
                    e = q - x
                    self.assertGreater(lo.sum(), 0, "control: a low plateau")
                    self.assertTrue(np.all(e[lo] == 0.0), "the low plateau: e = 0")
                    if hi.any():                                 # (Shape 127 at note 115: the high is under one sample)
                        self.assertLessEqual(float(np.max(np.abs(e[hi] + 1 / S))), 2.0 ** -23, "the high plateau: e = -1/S")
                    f_hi, f_edge = hi.mean(), edge.mean()
                    m = float(e.mean())
                    bound = f_edge * (1 / S + 2.0 ** -23) + f_hi * 2.0 ** -23
                    self.assertLessEqual(abs(m + f_hi / S), bound, f"{b}-bit Shape {shape}: mean shift {m!r}")
                    ma = float(np.mean(G.crush(x, b) - x))
                    if shape >= 100 and f_hi > 0.05:             # (below 100 dc is small, and so is (a)'s clip; with no
                                                                 # high plateau (127 at note 115) there is little to clip)
                        self.assertGreater(abs(ma), {12: 100, 8: 10, 4: 1}[b] * abs(m), f"control: OD12 (a) {ma!r} vs {m!r}")
                rows.append((shape, sync, b, float(x.mean()), float(q.mean()), m, ma, f_hi, f_edge))
        w = max((r for r in rows if r[0] > 88), key=lambda r: abs(r[5]) * G.scale(r[2]))
        print("\n  crushed pulse mean shift (mean(q) - mean(s)); OD12 (a) on the same blocks:\n" + "\n".join(
              f"    Shape {r[0]:3} sync {r[1]} {r[2]:2}-bit: mean s {r[3]:+.6f} q {r[4]:+.6f} shift {r[5]:+.3e} "
              f"(f_hi {r[7]:.3f}, edges {r[8]:.3f}); (a) {r[6]:+.4f}" for r in rows if r[2] in (12, 4) and r[1] == 0),
              file=sys.stderr)

    def test_a_ramp_crushes_each_part_about_its_own_dc(self):
        """Q2b (OD12: dc follows a Shape ramp part by part): an LFO on Position moves Shape between 100 and about 125 every
        block (free and synced, Rig(dev=True), 12 / 8 / 4 bits), so each changed block ramps in 8 AN_RAMP parts. Each part's
        an_crush call follows its an_render call (same offset and length) with dc = an_dc of that part's k (the M7's own,
        bit for bit) and within 1e-6 of dc_of(k); the dcs of a ramp block are 8 distinct values in the ramp's order. The
        Level-input block is the golden crush of the Full block with that per-sample dc, bit for bit. Each constant-dc part
        has at most 2^b distinct values, on the grid about its dc (check_levels); a held block (one part of 256) at 4 bits has
        at most 16. A whole ramp block is off the grid of any one of its dcs (the 2^b is per part; the count per block is
        printed)."""
        mod = lambda blk, i: 0.2 * (blk % 2)
        r0 = Rig()
        for sync in (0, 1):
            for bits in (1, 2, 3):
                b = G.bits_of(bits)
                with self.subTest(sync=sync, bits=bits):
                    rc, rf = Rig(dev=True), Rig(dev=True)
                    tc, tf = level_taps(rc), level_taps(rf)
                    seq = []
                    rc.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: seq.append(("r", uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2),
                                  np.frombuffer(struct.pack("<I", uc.reg_read(UC_ARM_REG_S1)), dtype="<f4")[0])),
                                  begin=DSP["an_render"] & ~1, end=DSP["an_render"] & ~1)
                    rc.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: seq.append(("c", uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1),
                                  np.frombuffer(struct.pack("<I", uc.reg_read(UC_ARM_REG_S0)), dtype="<f4")[0])),
                                  begin=DSP["an_crush"] & ~1, end=DSP["an_crush"] & ~1)
                    kw = dict(shape=(100, 100), sync=sync, mod=mod, blocks=6)
                    self.voice_case(rc, p3=(0, bits, 0), **kw); self.voice_case(rf, p3=(0, 0, 0), **kw)
                    self.assertEqual((tf.stray, sum(len(c) for c in tf.calls)), ([], 0), "Full: no an_crush call")
                    self.assertEqual(tc.stray, [])
                    self.assertEqual([s[0] for s in seq], ["r", "c"] * (len(seq) // 2), "each render, then its crush")
                    for (_, rout, rn, k), (_, cp, cn, dc) in zip(seq[0::2], seq[1::2]):
                        self.assertEqual((cp, cn), (rout, rn), "the crush covers its render's part")
                        self.assertEqual(float(dc), self.an_dc(float(k), r0), "dc = an_dc(the part's k), bit for bit")
                        self.assertAlmostEqual(float(dc), dc_of(k), delta=1e-6)
                    ramps, per_block = 0, []
                    for j, (x, y, calls, d) in enumerate(zip(tf, tc, tc.calls, tc.dc)):
                        want = G.crush_bits(x.astype(np.float64), b, dc=d)
                        self.assertTrue(np.array_equal(y.view(np.uint32), want), f"block {j}: the golden crush with its parts' dc")
                        dcs = [float(c[3]) for c in calls]
                        if len(set(dcs)) > 1:
                            ramps += 1
                            self.assertEqual(len(calls), 8, f"block {j}: 8 AN_RAMP parts")
                            self.assertEqual(len(set(dcs)), 8, f"block {j}: 8 dcs")
                            self.assertTrue(dcs == sorted(dcs) or dcs == sorted(dcs, reverse=True), f"block {j}: in the ramp's order")
                            per_block.append(int(np.unique(y).size))
                            with self.assertRaises(AssertionError, msg=f"block {j}: one dc for the whole ramp block is off its grid"):
                                G.check_levels(y.astype(np.float64), b, dcs[0])
                        for off, n, _, dc in calls:
                            G.check_levels(y[off:off + n].astype(np.float64), b, float(dc))
                        if len(calls) == 1 and b == 4:
                            self.assertLessEqual(int(np.unique(y).size), 16)
                    self.assertGreaterEqual(ramps, 4, "control: the LFO ramps most blocks")
                    if b == 4:
                        print(f"\n  ramp blocks at 4 bits (sync {sync}): distinct values per block {per_block}", file=sys.stderr)

    def test_each_oscillator_crushes_at_its_own_depth(self):
        """Q2 review: OSC1 and OSC2 at different depths (12 / 4 bits, Full / 8, 8 / Full), free and synced, both rigs: the
        crushed build plays Full with each oscillator's own golden crush injected at its Level input (level_taps tells
        them apart), byte for byte; bits= counts only the crushed oscillators' renders. So a crush reading the other
        oscillator's setting (or one setting for both) is seen in an all-Analog voice, not only in the mixed case."""
        for dev in (False, True):
            for name, kw in (("free", dict(shape=(48, 88))), ("sync", dict(shape=(70, 100), sync=1))):
                for bb in ((1, 3), (0, 2), (2, 0)):
                    with self.subTest(dev=dev, case=name, bits=bb):
                        gb = [G.bits_of(x) if x else 0 for x in bb]
                        rc, rf, ri = Rig(dev=dev), Rig(dev=dev), Rig(dev=dev)
                        tc = level_taps(rc)
                        hits, feed = [], dc_feed(tc)             # Q2b: each block's dc, as the crushed run had it
                        def inj(x, i):
                            self.assertIn(i, (0, 1), "level_taps: the oscillator")
                            hits.append(i)
                            d = feed()
                            return G.crush(x, gb[i], dc=d) if gb[i] else x
                        level_taps(ri, inject=inj)
                        c = self.voice_case(rc, p3=tuple((700, x, 333) for x in bb), **kw)
                        self.assertEqual([rc.stored[i][3] for i in (0, 1)], list(bb), f"{name}: control, stored per oscillator")
                        for i in (0, 1):
                            if gb[i]:
                                self.check_dcs(f"{name} {bb}", tc, kw["shape"], gb[i], oscs=(i,))
                            else:
                                self.assertEqual([c for o, c in zip(tc.osc, tc.calls) if o == i and c], [], f"OSC{i + 1} Full: no crush")
                        f, d = self.voice_case(rf, p3=(700, 0, 333), **kw), self.voice_case(ri, p3=(700, 0, 333), **kw)
                        self.assertEqual(len(hits), len(tc)); self.assertEqual(set(hits), {0, 1}, "both oscillators reach Level")
                        self.assertNotEqual(c[0], f[0], f"{name} {bb}: changes the output")
                        same(self, c[0], d[0], f"{name} {bb}: the output = Full with each oscillator's golden crush")
                        same(self, self.masked(c[1], (0, 1)), self.masked(d[1], (0, 1)), f"{name} {bb}: the voice")
                        self.assertEqual(c[2], d[2], f"{name} {bb}: the stats words")
                        self.assertEqual(struct.unpack("<8I", rc.u.mem_read(SA + SA_FMSTATS, 32)),
                                         (0, sum(1 for i in hits if gb[i]), 0, 0, 0, 0, 0, 0), f"{name} {bb}: bits=")

    def test_the_fm_stats_line_is_cleaned_with_the_stats(self):
        """Q2 review: finish() cleans SA_FMSTATS's line (DCCMVAC) wherever it cleans SA_STATS, after the block's counts, so
        the M4 reads a fresh bits= on the device (the emulator has no cache: the clean itself is what is checked). Both
        oscillators at 8 bits, 8 blocks: each clean sees bits= = 2 per finished block."""
        r = Rig()
        cleans = []
        def h(uc, access, a, size, value, _):
            line = value & 0xFFFFFFFF
            if line in (SA + SA_STATS, SA + SA_FMSTATS):
                cleans.append((line, struct.unpack("<I", uc.mem_read(SA + SA_FMSTATS + 4, 4))[0]))
        r.u.hook_add(UC_HOOK_MEM_WRITE, h, begin=0xE000EF68, end=0xE000EF6B)   # SA_DCCMVAC (synth_shared.h)
        self.voice_case(r, shape=(48, 88), p3=(0, 2, 0), blocks=8)
        st = [k for line, k in cleans if line == SA + SA_STATS]
        fm = [k for line, k in cleans if line == SA + SA_FMSTATS]
        self.assertEqual(len(st), 7, "the stats line: once per finished block (8 blocks, the last one finishes later)")
        self.assertEqual(fm, [2 * (j + 1) for j in range(7)], "the FM stats line, after each block's bits= counts")

    def test_the_dsp_links_no_libm(self):
        """Q2 review (plan 4.7): zig's compiler_rt supplies libm-named routines (fmaxf, floorf, sinf, ..) as software calls
        that link without an undefined symbol, so build.py's DSP_REFUSED_LIBM is load-bearing. It refuses the family (the
        names compiler_rt was measured to link, and their double forms), check_dsp_elf refuses this build's own ELF with one
        symbol renamed to fmaxf, the shipped ELF has none, and an_crush is the inline grid: VRINTX, VMAXNM, VMINNM, no call,
        no VDIV."""
        for n in ("rintf", "fminf", "fmaxf", "floorf", "sqrtf", "roundf", "sinf", "truncf", "expf", "floor", "ceilf",
                  "fabsf", "cosf", "tanhf", "powf", "logf", "log2f", "exp2f", "fmodf", "lroundf", "nearbyintf", "sinhf"):
            self.assertTrue(build.DSP_REFUSED_LIBM.match(n), f"{n}: refused")
        e = (BUILD / "dsp.elf").read_bytes()
        _, syms = build.elf_sections(e)
        names = {n for n, *_ in syms if n}
        self.assertIn("an_crush", names)
        self.assertEqual(sorted(n for n in names if build.DSP_REFUSED_LIBM.match(n)), [], "the shipped ELF: no libm name")
        origin, limit = S.M7_DSP + build.DSP_TRAMP_BYTES, S.M7_DSP_LIMIT
        build.check_dsp_elf(e, origin, limit)                    # control: the shipped ELF passes
        shoff, (shentsize, shnum) = struct.unpack_from("<I", e, 32)[0], struct.unpack_from("<HH", e, 46)
        secs = [struct.unpack_from("<10I", e, shoff + i * shentsize) for i in range(shnum)]
        st = secs[next(s for s in secs if s[1] == 2)[6]]           # the symbol table's string table (not .debug_str)
        at = e.find(b"\0an_crush\0", st[4], st[4] + st[5])
        self.assertGreaterEqual(at, 0, "an_crush in .strtab")
        bad = e[:at] + b"\0fmaxf\0\0\0\0" + e[at + 10:]
        self.assertIn("fmaxf", {n for n, *_ in build.elf_sections(bad)[1]})
        with self.assertRaises(SystemExit) as cm:
            build.check_dsp_elf(bad, origin, limit)
        self.assertIn("fmaxf", str(cm.exception))
        lo = DSP["an_crush"] & ~1
        hi = min([a & ~1 for a in DSP.values() if (a & ~1) > lo] + [S.M7_DSP_LIMIT])
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        ins = [i.mnemonic for i in md.disasm(IMG[lo - BASE:hi - BASE], lo)]
        for m in ("vrintx.f32", "vmaxnm.f32", "vminnm.f32"):
            self.assertIn(m, ins, f"an_crush: {m}")
        self.assertEqual([m for m in ins if m in ("bl", "blx") or m.startswith("vdiv")], [], "an_crush: no call, no VDIV")


SINQ_ERR = 8.1e-5           # dsp/analog_kernel.cpp sinq: its stated design maximum (the fit's), not a residual of the kernel
SINQ_THD_DB = -81.87        # sinq of the triangle, THD (plan 6.3: measured first, in numpy, before the kernel: scratch p3/q3/impl)
SINQ = (1.570243, -0.641711175, 0.0714682192)   # its stated coefficients (sin(pi/2 x) ~ x (c0 + x^2 (c1 + x^2 c2)), rc26)
FLAT_INC = 0.09             # analog_kernel.cpp (P3 Q3b): the plateaus' own loops below this increment, run<LOW> at and above


def k_of(sh):
    """Shape (whole or 1/512-held) -> the kernel's shape, float64: p3_golden's map below 48 (P3 Q3), BE4.40's above."""
    return G.kcoord(sh) if sh <= 48 else (sh - 48) * (0.5 / 40) if sh <= 88 else 0.5 + (min(sh, 127) - 88) * (0.5 / 39)


def dsp_sym(part):
    """The address (Thumb bit cleared) of the one DSP symbol whose (mangled) name contains part, e.g. 'low_step'."""
    got = [a & ~1 for k, a in DSP.items() if part in k]
    assert len(got) == 1, (part, got)
    return got[0]


def render_ranges():
    """[start, end) of each function of an_render's code: an_render itself and (P3 Q3b) the plateaus' out-of-line run_flat
    instances and low_step; each ends at the next DSP symbol."""
    starts = sorted({a & ~1 for a in DSP.values()})
    out = []
    for k, a in DSP.items():
        if k == "an_render" or "run_flat" in k or "low_step" in k:
            a &= ~1
            out.append((a, min([b for b in starts if b > a] + [S.M7_DSP_LIMIT])))
    return sorted(out)


def low_slave(inc, shape, n, ev, phase, delayed, reset_blamp=True):
    """Test-local model of the LOW regime's slave role (p3_golden does not model sync: its module notes), built from the
    golden model's own pieces (weights, naive, _blep, _blamp) and the kernel's phase law (float32 steps): at an event the
    phase runs to the event's fraction at = e / 254, the wave steps to wave(0) (a BLEP) and its slope from wave'(p) to
    wave'(0) (a BLAMP, P3 Q3's own addition: the plan's 4.6 names none, I), then runs on from 0. wave' per cycle: sine
    2 pi sin 2 pi p, triangle +4 below 1/2 and -4 above, saw 2. reset_blamp False: the step's BLEP only (the control).
    Returns y (float64)."""
    ws, wt, wsaw = G.weights(shape)
    r32 = lambda v: float(np.float32(v))
    inc = r32(G.clamp_inc(inc)); p = r32(phase); d = float(delayed)
    K = 8.0 * wt * inc
    slope = lambda q: ws * 2 * math.pi * math.sin(2 * math.pi * q) + wt * (4.0 if q < 0.5 else -4.0) + 2.0 * wsaw
    y = np.empty(n)
    def part(p, begin, length, c):
        end = p + inc * length
        if p < 0.5 <= end:
            a, b = G._blamp(begin + (0.5 - p) / inc, -K); c[0] += a; c[1] += b
        if end >= 1.0:
            fr = begin + (1.0 - p) / inc
            for a, b in (G._blep(fr, -2.0 * wsaw), G._blamp(fr, K)):
                c[0] += a; c[1] += b
            end -= 1.0
        return r32(end)
    for i in range(n):
        c = [0.0, 0.0]
        if ev[i] != 255 and inc > 0:
            at = r32(ev[i] * r32(1 / 254))
            p = part(p, 0.0, at, c)
            for a, b in (G._blep(at, float(G.naive(0.0, shape)) - float(G.naive(p, shape))),
                         G._blamp(at, inc * (slope(0.0) - slope(p)) if reset_blamp else 0.0)):
                c[0] += a; c[1] += b
            p = part(0.0, at, 1.0 - at, c)
        else:
            p = part(p, 0.0, 1.0, c)
        y[i] = d + c[0]
        d = float(G.naive(p, shape)) + c[1]
    return y


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class ShapeLow(unittest.TestCase):
    """P3 Q3 (docs/plan-p3-fm-bits.md 4.6, 6.3, OD6): the five shapes. Below Shape 48 the kernel's LOW regime (sine 0,
    triangle 24, saw 48, plateaus 0..2 / 22..26 / 45..48) against p3_golden.py (numpy, written from the plan alone: its
    Q3 section), sample by sample on the kernel's float32 phase law (law "f32") with the golden's tolerance (6.3's 1e-5
    plus the sine's weight times sinq's stated error), and by spectrum; an_shape's map through the voice; sync roles; the
    Shape ramp across the new plateaus against BE4.40's saw > square ramp. Identity at 45..127 is P3Identity's."""
    INCS = (f32(261.6256 / DEV_RATE), f32(0.0123), f32(0.2), f32(0.45))
    SHAPES = (0, 1, 12, 23, 24, 36, 44, 47)

    def kernel(self, r, st, inc, k, n, role=0, events=None):
        out = r.alloc(4 * n); ev = r.alloc(max(n, 128))
        r.u.mem_write(ev, bytes(events) if events is not None else bytes([255]) * max(n, 128))
        _, cnt = r.call(DSP["an_render"], st, out, n, ev, sargs=(role,), fargs=(inc, k))
        y = np.frombuffer(bytes(r.u.mem_read(out, 4 * n)), dtype="<f4").astype(np.float64)
        phase, delayed = struct.unpack("<ff", r.u.mem_read(st + 4, 8))
        return y, phase, delayed, bytes(r.u.mem_read(ev, n)), cnt

    def state(self, r, phase, delayed):
        st = r.alloc(176); r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, phase, delayed))
        return st

    def test_the_kernel_is_the_golden_model(self):
        """Shapes 0, 1 (the sine plateau), 12 (sine > triangle), 23, 24 (the triangle), 36 (triangle > saw), 44 and 47 (the
        saw plateau: BE4.40's SAW regime, k = 0) at note 60 on the device (inc 0.0027), 0.0123, 0.2 and the clamp 0.45,
        from phase 0.37 (and 0 at note 60), 1024 samples in two chained calls: every sample within tol_time(law "f32",
        sinq_err = SINQ_ERR), the phase after each call the golden's exactly (the same float32 law), and the lowest sample
        printed (Q2's note: the bottom clip; the BLAMP rounds the triangle's trough inward, sinq(-1) is -1 in float32)."""
        lowest, worst = 0.0, (0.0, None)
        for shape in self.SHAPES:
            for inc in self.INCS:
                for ph in ((0.0, 0.37) if inc == self.INCS[0] else (0.37,)):
                    with self.subTest(shape=shape, inc=inc, phase=ph):
                        r = Rig(); k = f32(k_of(shape)); p0 = f32(ph); d0 = f32(float(G.naive(p0, shape)))
                        st = self.state(r, p0, d0)
                        g = G.render(inc, shape, 1024, phase=p0, delayed=d0, law="f32")
                        y1, q1, _, _, _ = self.kernel(r, st, inc, k, 512)
                        y2, q2, _, _, _ = self.kernel(r, st, inc, k, 512)
                        gp = G.render(inc, shape, 512, phase=p0, delayed=d0, law="f32").phase
                        self.assertEqual((q1, q2), (f32(gp), f32(g.phase)), "the phase: the golden's float32 law")
                        y = np.r_[y1, y2]
                        tol = G.tol_time(shape, inc, 1024, "f32", SINQ_ERR)
                        err = float(np.max(np.abs(y - g.y)))
                        self.assertLessEqual(err, tol, f"worst {err:.3g} > {tol:.3g} at {int(np.argmax(np.abs(y - g.y)))}")
                        lowest = min(lowest, float(y.min()))
                        if err / tol > worst[0]:
                            worst = (err / tol, (shape, inc, ph, err))
        print(f"\n  LOW vs golden: worst {worst[1][3]:.3g} = {worst[0]:.2f} of its tolerance at Shape {worst[1][0]}, inc "
              f"{worst[1][1]:.5f}; lowest sample {lowest:.7f}", file=sys.stderr)

    def test_master_events_are_the_golden_wraps(self):
        """MASTER (n <= 128, the harness's event buffer): the LOW regime writes an event byte at each wrap; the bytes are
        the golden's round(fr 254) within one step (E2: the fraction multiplies by 1 / inc), none missing or extra; the
        output is the free render's, bit for bit (the role only records)."""
        for shape in (0, 12, 24, 36):
            for inc in (f32(0.0123), f32(0.073), f32(0.3)):
                with self.subTest(shape=shape, inc=inc):
                    r = Rig(); k = f32(k_of(shape))
                    y, _, _, ev, _ = self.kernel(r, self.state(r, 0.37, 0.0), inc, k, 128, role=1)
                    yf, _, _, _, _ = self.kernel(r, self.state(r, 0.37, 0.0), inc, k, 128)
                    g = G.render(inc, shape, 128, phase=f32(0.37), law="f32")
                    got, want = list(ev), list(g.ev)
                    self.assertEqual([e == 255 for e in got], [e == 255 for e in want], "the wraps")
                    self.assertLessEqual(max(abs(a - b) for a, b in zip(got, want)), 1)
                    self.assertTrue(np.array_equal(y, yf), "the master's output is the free render's")

    def test_slave_resets_band_limit_the_value_and_the_slope(self):
        """SLAVE at the master's events (a golden master at inc 0.031: four or five resets in 128 samples): the kernel
        against low_slave (above), within 1e-5 + 2 x the sine's weight x sinq's error (the step's two waves are sinq's);
        no events: the free render, bit for bit. Control: the kernel against low_slave without the reset's BLAMP is off by
        more than 10 x 1e-5 for the triangle (so the slope correction is what is measured; at inc 0.0123 every reset lands
        before the top, where the triangle's slope does not step, so 0.02 is in the set)."""
        _, _, _, mev, _, _ = G.render(f32(0.031), 48, 128, phase=0.2, law="f32")
        self.assertGreaterEqual(sum(e != 255 for e in mev), 4)
        for shape in (0, 12, 24, 36):
            for inc, ph in ((f32(0.0123), 0.37), (f32(0.02), 0.81), (f32(0.07), 0.6)):
                with self.subTest(shape=shape, inc=inc, phase=ph):
                    r = Rig(); k = f32(k_of(shape)); d0 = f32(float(G.naive(ph, shape)))
                    y, _, _, _, _ = self.kernel(r, self.state(r, ph, d0), inc, k, 128, role=2, events=list(mev))
                    want = low_slave(inc, shape, 128, mev, ph, d0)
                    ws = G.weights(shape)[0]
                    tol = G.TOL_F32 + 2 * ws * SINQ_ERR
                    self.assertLessEqual(float(np.max(np.abs(y - want))), tol)
                    yf, _, _, _, _ = self.kernel(r, self.state(r, ph, d0), inc, k, 128, role=2)
                    yg, _, _, _, _ = self.kernel(r, self.state(r, ph, d0), inc, k, 128)
                    self.assertTrue(np.array_equal(yf, yg), "no events: the free render")
        # control: without the reset's BLAMP the model leaves the kernel by far more than the tolerance (triangle at inc 0.02:
        # its resets land past the top, p 0.645 a master period after the first, where the slope steps by 8 x 0.02)
        r = Rig(); y, _, _, _, _ = self.kernel(r, self.state(r, 0.81, 0.0), f32(0.02), f32(k_of(24)), 128, role=2, events=list(mev))
        off = float(np.max(np.abs(y - low_slave(f32(0.02), 24, 128, mev, 0.81, 0.0, reset_blamp=False))))
        self.assertGreater(off, 10 * G.TOL_F32, f"control: the reset's slope correction is seen ({off:.3g})")

    def test_an_shape_through_the_voice(self):
        """an_shape (dsp/analog.cpp) in the voice path, Rig(dev=True): the shape an_render is called with, for every whole
        Shape 0..48 and for 1/512-held modulated ones next to each plateau edge (1.998, 2.002, 21.998, 22.002, 25.998, 26.002,
        44.998, 45.002, 47.998), is the golden map's (kcoord) in float32 within 2 ulp of 1 (one rounding of a product, one of
        a sum); exactly -1, -0.5 and 0 on the plateaus. The 6.6 mutation "plateau centred on 48" moves 45..47 off 0 (and
        P3Identity's 49..51). The block of each change ramps in AN_RAMP_LOW parts where an end is below the saw."""
        got = {}
        r = Rig(dev=True); root, v = r.voice(0x76); r.table(0, 0x76, 1); r.note(v, 60); o = r.osc(v, 0)
        cap = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: cap.append(struct.unpack("<f", struct.pack("<I", uc.reg_read(UC_ARM_REG_S1)))[0]),
                     begin=DSP["an_render"] & ~1, end=DSP["an_render"] & ~1)
        parts = {}
        for s in range(49):
            r.prop(o, K_SHAPE, s); cap.clear(); r.render(root, v, oscs=(0,)); r.block()
            parts[s] = len(cap)                                          # the change's block: its ramp's parts
            cap.clear(); r.render(root, v, oscs=(0,)); r.block()        # the block after it: no ramp, one part
            self.assertEqual(len(cap), 1)
            got[float(s)] = cap[-1]
        self.assertEqual(parts, {s: 1 if s == 0 else 16 if s <= 45 else 8 for s in range(49)},
                         "a ramp's parts: AN_RAMP_LOW (16 a 2N block) where an end is below the saw (k < 0: Shape < 45), "
                         "else AN_RAMP (8); none on the first block")
        r2 = Rig(dev=True); root2, v2 = r2.voice(0x77); r2.table(0, 0x77, 1); r2.note(v2, 60); o2 = r2.osc(v2, 0)
        r2.prop(o2, K_SHAPE, 0)
        src = route(r2, o2, 0, 1.0)
        r2.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: cap.append(struct.unpack("<f", struct.pack("<I", uc.reg_read(UC_ARM_REG_S1)))[0]),
                      begin=DSP["an_render"] & ~1, end=DSP["an_render"] & ~1)
        fma = lambda a, b, c: f32(float(Fraction(a) * Fraction(b) + Fraction(c)))
        for want in (1.998, 2.002, 21.998, 22.002, 25.998, 26.002, 44.998, 45.002, 47.998):
            x = f32(want / 127)
            sh = fma(x, 127.0, 0.0); sh = f32(float(int(fma(sh, 512.0, 0.5))) * f32(1 / 512))
            r2.wf(src + 4, x)
            for _ in range(2):
                cap.clear(); r2.render(root2, v2, oscs=(0,)); r2.block()
            got[sh] = cap[-1]
        for sh, k in got.items():
            with self.subTest(shape=sh):
                want = f32(k_of(sh))
                if want in (-1.0, -0.5, 0.0):
                    self.assertEqual(k, want)
                self.assertLessEqual(abs(k - want), 2 * 2.0 ** -24 * max(abs(want), 0.5), f"{k!r} vs {want!r}")

    def test_spectra(self):
        """Coherent 4096-sample blocks, no window, after a 4096-sample warm-up (p3_golden.spectrum / steady, law "f32").
        Shape 0 (the sine): its THD is sinq's (SINQ_THD_DB, measured in numpy before the kernel) within 1 dB, and below the
        -55 dB the Bessel check of 6.3 needs; its off-harmonic energy is sinq's (the stated polynomial on the float32 phase,
        numpy, no BLEP: at 301 cycles its 7th harmonic and up fold past Nyquist, so not the golden's < -120 dB) within 1 dB.
        Shapes 12, 24, 36, 47 at 11 and 301
        cycles: the off-harmonic energy (aliasing) the golden's within 0.1 dB (the golden notes: I there, measured here),
        and the band-limited golden below its naive render's (the BLEP/BLAMP buy what they should)."""
        n = 4096
        for cycles in (11, 301):
            inc = G.coherent_inc(cycles, n)
            for shape in (0, 12, 24, 36, 47):
                with self.subTest(shape=shape, cycles=cycles):
                    g, ph0 = G.steady(inc, shape, n, law="f32")
                    r = Rig(); st = self.state(r, f32(0.0), f32(float(G.naive(0.0, shape))))
                    self.kernel(r, st, f32(inc), f32(k_of(shape)), n)
                    y, _, _, _, _ = self.kernel(r, st, f32(inc), f32(k_of(shape)), n)
                    ks, gs = G.spectrum(y, cycles, ph0), G.spectrum(g.y, cycles, ph0)
                    if shape == 0:
                        print(f"\n  Shape 0, {cycles} cycles: THD {ks.thd_db:.2f} dB (sinq {SINQ_THD_DB}), off {ks.off_db:.1f} dB",
                              file=sys.stderr)
                        self.assertLess(abs(ks.thd_db - SINQ_THD_DB), 1.0)
                        self.assertLess(ks.thd_db, -55.0)
                        t = 1 - 4 * np.abs((np.float32(ph0) + np.arange(n) * np.float64(f32(inc))) % 1.0 - 0.5)
                        sq = G.spectrum(t * (SINQ[0] + t * t * (SINQ[1] + t * t * SINQ[2])), cycles, ph0)
                        print(f"  (sinq alone: off {sq.off_db:.1f} dB)", file=sys.stderr)
                        self.assertLess(abs(ks.off_db - sq.off_db), 1.0)
                        continue
                    naive = G.spectrum([float(G.naive((ph0 + j * inc) % 1.0, shape)) for j in range(n)], cycles, ph0)
                    print(f"\n  Shape {shape}, {cycles} cycles: off {ks.off_db:.2f} dB (golden {gs.off_db:.2f}, naive "
                          f"{naive.off_db:.2f})", file=sys.stderr)
                    self.assertLess(abs(ks.off_db - gs.off_db), 0.1)
                    self.assertLess(gs.off_db, naive.off_db - 5)

    def test_one_division_per_render(self):
        """6.5 (FM off): the LOW regime divides once per an_render call (the block's 1 / inc, E2), never per sample or per
        edge: VDIV.F32 executed inside the render's code counted by PC (an_render and, Q3b, the plateaus' run_flat and
        low_step, out of line), Shapes 0 / 24 / 36 at inc 0.2 (an edge every few samples), 256 samples, free, master and
        slave."""
        rs = render_ranges()
        self.assertEqual(len(rs), 4, "an_render, run_flat x 2, low_step")
        lo, hi = min(a for a, _ in rs), max(b for _, b in rs)
        for shape in (0, 24, 36):
            for role, n in ((0, 256), (1, 128), (2, 128)):
                with self.subTest(shape=shape, role=role):
                    r = Rig(); divs = []
                    def h(uc, a, sz, _):
                        if sz == 4 and any(x <= a < y for x, y in rs):
                            w0, w1 = struct.unpack("<HH", uc.mem_read(a, 4))
                            if w0 & 0xFFB0 == 0xEE80 and w1 & 0x0F50 == 0x0A00:
                                divs.append(a)
                    r.u.hook_add(UC_HOOK_CODE, h, begin=lo, end=hi - 1)
                    ev = list(G.render(f32(0.031), 48, 128, phase=0.2, law="f32").ev) if role == 2 else None
                    self.kernel(r, self.state(r, 0.1, 0.0), f32(0.2), f32(k_of(shape)), n, role=role, events=ev)
                    self.assertEqual(len(divs), 1, f"{len(divs)} VDIVs")

    def test_the_plateaus_go_the_long_way_at_every_corner(self):
        """P3 Q3b: Shapes 0 and 24 (the plateaus, k = -1 / -0.5) run_flat: a sample goes the long way (low_step, LOW's own
        sample) wherever a corner can fall, and a correction-free stretch runs between; at and above FLAT_INC run<LOW> takes
        them (no low_step call). 132 calls at incs from 2^-20 (the bound's rinc^2 term at its largest) through note 60 and
        either side of FLAT_INC to the clamp 0.45, each starting within 0 to 3 increments below a corner (1/2 or the wrap),
        n 5 to 512; free, master (its event bytes pre-filled 0x55) and slave (random events). Free / master: every sample
        whose phase crosses 1/2 or 1 on the float32 law went the long way (low_step's out_i, by PC hook), and at most the
        corners + 2 did (the stretch does the work; Q3b review: was 2 x, which a lost odd tail of the sine's pairs passed); output the golden render's within tol_time
        (sinq's error for the sine), the phase the float32 law's exactly, the master's bytes the golden's wraps within one
        step and 255 everywhere else (the stretch writes them). Slave: every event's sample went the long way; output
        low_slave's within its tolerance. (Q3b, M: the plateaus against Q2b's run<LOW> on the same calls: the triangle bit
        for bit, output and state; the sine within 1.8e-7, its phase bit for bit: scratch.)"""
        ls = dsp_sym("low_step")
        rng = random.Random(41)
        incs = (2.0 ** -20, 1.3e-4, float(self.INCS[0]), 0.0123, 0.073, FLAT_INC * (1 - 2 ** -20), FLAT_INC, 0.2, 0.45,
                0.05)
        r = Rig(); st, out, evb = r.alloc(176), r.alloc(4 * 512), r.alloc(512)
        hits = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: hits.append((uc.reg_read(UC_ARM_REG_R2) - out) // 4), begin=ls, end=ls)
        tally = {}
        for t in range(132):
            shape = (0, 24)[t % 2]; role = (t // 2) % 3; k = f32(k_of(shape))
            base = incs[(t // 6) % len(incs)]
            inc = f32(base if abs(base / FLAT_INC - 1) < 1e-3 else min(0.45, base * (1 + 0.1 * rng.random())))
            lim = (0.5, 1.0)[(t // 3) % 2]
            ph = f32((lim - float(inc) * 3 * rng.random()) % 1.0)
            n = 128 if role else (5, 128, 512)[t % 3]
            d0 = f32(float(G.naive(ph, shape)))
            ev = ([255 if rng.random() < 0.9 else rng.randrange(255) for _ in range(n)] if role == 2 else
                  [0x55] * n if role == 1 else [255] * n)
            with self.subTest(shape=shape, role=role, inc=inc, phase=ph, n=n):
                r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, ph, d0)); r.u.mem_write(evb, bytes(ev))
                hits.clear()
                r.call(DSP["an_render"], st, out, n, evb, sargs=(role,), fargs=(inc, k))
                y = np.frombuffer(bytes(r.u.mem_read(out, 4 * n)), dtype="<f4").astype(np.float64)
                phase = struct.unpack("<f", r.u.mem_read(st + 4, 4))[0]
                got = set(hits)
                if not inc < f32(FLAT_INC):
                    self.assertEqual(got, set(), "at and above FLAT_INC: run<LOW>, no long-way call")
                if role == 2:
                    want = {i for i, e in enumerate(ev) if e != 255}
                    if inc < f32(FLAT_INC): self.assertLessEqual(want, got, "every event's sample the long way")
                    tol = G.TOL_F32 + 2 * G.weights(shape)[0] * SINQ_ERR
                    self.assertLessEqual(float(np.max(np.abs(y - low_slave(inc, shape, n, ev, ph, d0)))), tol)
                    continue
                corners, p = set(), np.float32(ph)
                for i in range(n):
                    e = np.float32(p + np.float32(inc))
                    if (p < 0.5 <= e) or e >= 1: corners.add(i)
                    p = np.float32(e - np.float32(1)) if e >= 1 else e
                if inc < f32(FLAT_INC): self.assertLessEqual(corners, got, f"a corner in a stretch: {sorted(corners - got)}")
                self.assertLessEqual(len(got), len(corners) + 2, f"{len(got)} long-way samples, {len(corners)} corners")
                if inc < f32(FLAT_INC): a, c = tally.get(shape, (0, 0)); tally[shape] = (a + len(got), c + len(corners))
                g = G.render(inc, shape, n, phase=ph, delayed=d0, law="f32")
                self.assertEqual(phase, f32(g.phase), "the phase: the float32 law")
                tol = G.tol_time(shape, inc, n, "f32", SINQ_ERR)
                self.assertLessEqual(float(np.max(np.abs(y - g.y))), tol)
                if role == 1:
                    evo = bytes(r.u.mem_read(evb, n))
                    self.assertEqual([e == 255 for e in evo], [e == 255 for e in g.ev], "the wraps (and 255 elsewhere)")
                    self.assertLessEqual(max(abs(a - b) for a, b in zip(evo, g.ev)), 1)
        print("\n  plateaus, long-way samples / corners (free, master): "
              + ", ".join(f"Shape {s} {a} / {c}" for s, (a, c) in tally.items()), file=sys.stderr)

    def test_the_stretch_bound_holds_on_long_stretches(self):
        """P3 Q3b review: run_flat's stretch bound m = floor((lim - p) g), g = rinc (1 - 2^-16) - rinc^2 2^-22, on stretches
        of 5 to 250 samples that end at a corner inside the call (the corner test's calls start 0 to 3 increments below
        one, so there the steps' accumulated rounding, which the rinc^2 term covers, never matters). Each call is chosen on
        the host so that the bound without that term (g = rinc (1 - 2^-16), in float32 as the kernel's) overruns the
        corner: 16 per plateau, incs 1e-5 .. 1e-3 (random mantissa), n 512, free / master / slave with no events. Every
        corner went the long way, at most the corners + 2 did, the output is the golden render's within tol_time, the phase
        the float32 law's (the corner test's asserts)."""
        ls = dsp_sym("low_step")
        rng = random.Random(47)
        f = np.float32
        def overruns(inc, ph):                 # the bound without its rinc^2 term, on the kernel's float32 stretch
            lim = f(0.5) if ph < 0.5 else f(1.0)
            m = int(np.floor(f(f(lim - ph) * f(f(f(1) / inc) * f(1 - 2 ** -16)))))
            p = ph
            for _ in range(m): p = f(p + inc)
            return p >= lim
        cases = []
        while len(cases) < 32:
            inc = f(10 ** rng.uniform(-5, -3)); lim = rng.choice((0.5, 1.0))
            ph = f(lim - float(inc) * rng.uniform(5, 250))
            if overruns(inc, ph): cases.append((len(cases) % 2 * 24, float(inc), float(ph)))
        n = 512
        r = Rig(); st, out, evb = r.alloc(176), r.alloc(4 * n), r.alloc(n)
        hits = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: hits.append((uc.reg_read(UC_ARM_REG_R2) - out) // 4), begin=ls, end=ls)
        for t, (shape, inc, ph) in enumerate(cases):
            role = t // 2 % 3; d0 = f32(float(G.naive(ph, shape)))
            with self.subTest(shape=shape, role=role, inc=inc, phase=ph):
                r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, ph, d0)); r.u.mem_write(evb, bytes([255]) * n)
                hits.clear()
                r.call(DSP["an_render"], st, out, n, evb, sargs=(role,), fargs=(inc, f32(k_of(shape))))
                y = np.frombuffer(bytes(r.u.mem_read(out, 4 * n)), dtype="<f4").astype(np.float64)
                corners, p = set(), f(ph)
                for i in range(n):
                    e = f(p + f(inc))
                    if (p < 0.5 <= e) or e >= 1: corners.add(i)
                    p = f(e - f(1)) if e >= 1 else e
                got = set(hits)
                self.assertTrue(corners, "the corner falls inside the call")
                self.assertLessEqual(corners, got, f"a corner in a stretch: {sorted(corners - got)}")
                self.assertLessEqual(len(got), len(corners) + 2, f"{len(got)} long-way samples, {len(corners)} corners")
                g = G.render(inc, shape, n, phase=ph, delayed=d0, law="f32")
                self.assertEqual(struct.unpack("<f", r.u.mem_read(st + 4, 4))[0], f32(g.phase))
                self.assertLessEqual(float(np.max(np.abs(y - g.y))), G.tol_time(shape, inc, n, "f32", SINQ_ERR))

    def test_the_plateaus_are_lows_own_samples(self):
        """P3 Q3b review: run_flat against LOW's own sample, in the suite (Q3b had it in scratch only): the same call
        rendered one low_step per sample (the build's LOW sample, by symbol: what run<LOW> runs for each sample at a plateau,
        tw / ws / wsaw as set_shape sets them there). The triangle bit for bit: output bytes, phase, delayed and the
        master's event bytes. The sine within 2e-7 (its stretch's p - c rounds once where tri() may round twice: M 1.79e-7),
        its phase and the master's bytes bit for bit. 36 calls, incs 2^-20 to just below FLAT_INC (random mantissa), random
        phases, n 128 / 256, free, master (bytes pre-filled 0x55) and slave (random events, about 1 in 10). This sees a
        stretch error far inside the golden's tolerance (a tri() written otherwise, a lost exact fold of sinq's 4 and
        sign)."""
        ls = dsp_sym("low_step")
        rng = random.Random(43)
        r = Rig(); st, out, evb, ref, pd = r.alloc(176), r.alloc(4 * 256), r.alloc(256), r.alloc(4 * 256), r.alloc(8)
        worst = 0.0
        for t in range(36):
            shape = (0, 24)[t % 2]; role = t // 2 % 3; n = (128, 256)[t // 6 % 2]
            inc = f32(min(FLAT_INC * 0.999, 2.0 ** rng.uniform(-20, math.log2(FLAT_INC))))
            rinc = float(np.float32(1) / np.float32(inc))
            ph = f32(rng.random()); d0 = f32(float(G.naive(ph, shape)))
            ev = ([rng.randrange(255) if rng.random() < 0.1 else 255 for _ in range(n)] if role == 2 else
                  [0x55] * n if role == 1 else [255] * n)
            with self.subTest(shape=shape, role=role, inc=inc, phase=ph, n=n):
                r.u.mem_write(pd, struct.pack("<ff", ph, d0))
                evr = bytes(r.call(ls, pd, pd + 4, ref + 4 * i, ev[i] if role == 2 else 255,
                                   fargs=(inc, rinc, 1.0 if shape else 0.0))[0] & 0xFF for i in range(n))
                r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, ph, d0)); r.u.mem_write(evb, bytes(ev))
                r.call(DSP["an_render"], st, out, n, evb, sargs=(role,), fargs=(inc, f32(k_of(shape))))
                yb, rb = bytes(r.u.mem_read(out, 4 * n)), bytes(r.u.mem_read(ref, 4 * n))
                sa, sb = bytes(r.u.mem_read(st + 4, 8)), bytes(r.u.mem_read(pd, 8))
                if role == 1: self.assertEqual(bytes(r.u.mem_read(evb, n)), evr, "the master's event bytes")
                self.assertEqual(sa[:4], sb[:4], "the phase, bit for bit")
                if shape == 24:
                    self.assertEqual(yb, rb, "the triangle: LOW's output bit for bit")
                    self.assertEqual(sa, sb, "the triangle: LOW's state bit for bit")
                else:
                    d = max(float(np.max(np.abs(np.frombuffer(yb, "<f4").astype(np.float64) - np.frombuffer(rb, "<f4")))),
                            abs(struct.unpack("<f", sa[4:])[0] - struct.unpack("<f", sb[4:])[0]))
                    worst = max(worst, d)
                    self.assertLessEqual(d, 2e-7, "the sine: within 2e-7 of LOW's")
        print(f"\n  plateaus against low_step per sample: the sine within {worst:.3g}", file=sys.stderr)

    def test_only_the_exact_plateaus_run_flat(self):
        """P3 Q3b review: by_regime sends a Shape to run_flat only at ws == 1 or tw == 1 exactly; a blend next to a
        plateau keeps run<LOW> (no low_step call) and its own weights: k one float32 step off -1 and either side of -0.5,
        and Shapes 3, 21 and 27 (the first blends off the plateaus 0..2 and 22..26), incs at note 60 and 0.0123, free and
        slave (random events). The Shapes also against the golden render (free) / low_slave (slave) within their
        tolerances."""
        ls = dsp_sym("low_step")
        rng = random.Random(53)
        r = Rig(); st, out, evb = r.alloc(176), r.alloc(4 * 256), r.alloc(256)
        hits = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: hits.append(a), begin=ls, end=ls)
        n = 256
        near = [(None, float(np.nextafter(np.float32(-1), np.float32(0)))),
                (None, float(np.nextafter(np.float32(-0.5), np.float32(-1)))),
                (None, float(np.nextafter(np.float32(-0.5), np.float32(0))))] + [(s, f32(k_of(s))) for s in (3, 21, 27)]
        for shape, k in near:
            for inc in (self.INCS[0], f32(0.0123)):
                for role in (0, 2):
                    ev = [rng.randrange(255) if rng.random() < 0.05 else 255 for _ in range(n)] if role else [255] * n
                    ph = f32(rng.random()); d0 = f32(float(G.naive(ph, shape))) if shape is not None else 0.0
                    with self.subTest(shape=shape, k=k, inc=inc, role=role):
                        r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, ph, d0)); r.u.mem_write(evb, bytes(ev))
                        hits.clear()
                        r.call(DSP["an_render"], st, out, n, evb, sargs=(role,), fargs=(inc, k))
                        self.assertEqual(hits, [], "a blend: run<LOW>, not a plateau's run_flat")
                        if shape is None: continue
                        y = np.frombuffer(bytes(r.u.mem_read(out, 4 * n)), dtype="<f4").astype(np.float64)
                        if role:
                            tol = G.TOL_F32 + 2 * G.weights(shape)[0] * SINQ_ERR
                            want = low_slave(inc, shape, n, ev, ph, d0)
                        else:
                            tol = G.tol_time(shape, inc, n, "f32", SINQ_ERR)
                            want = G.render(inc, shape, n, phase=ph, delayed=d0, law="f32").y
                        self.assertLessEqual(float(np.max(np.abs(y - want))), tol)

    def test_cost(self):
        """Instructions per voice-block (both oscillators Analog at one Shape, an_enter + WT1 + WT2, median of 6), n = 256 in
        Rig(dev=True) and 128 in Rig(): the LOW regime at Shapes 0, 12, 24, 36, free and synced; and Shape 24 ramping every
        block (an LFO on Position: AN_RAMP_LOW parts) next to Shape 68 under the same LFO (BE4.40's saw > square ramp, AN_RAMP
        parts). The LOW loop is generic (plan 4.6: one regime, not four), so the blends pay the triangle, sinq and the saw
        term every sample: bound 1.3 x Cost's mixed (saw > square) voice in the same rig (a guard against a per-sample
        division or a lost specialisation); the LOW ramp 1.5 x the saw > square ramp (twice the parts, each with
        reshape_low). Q3b: the plateaus run_flat, so on the device's block (n = 256) the sine and the triangle cost at most
        Cost's saw voice, synced at most its saw+sync voice (M: Q3's sine 23,472 = 1.77 x the saw; Q3b's sine 13,100 and
        triangle 11,760 against 13,236). In Rig() (n = 128, twice the increment, so twice the corners a sample) the sine is
        about 1.03 x the saw: held to the blends' bound there."""
        mixed = {dev: Cost.cost(Cost(), 0.25, dev=dev)[0] for dev in (False, True)}
        saw = {(dev, fl): Cost.cost(Cost(), 0.0, fl, dev=dev)[0] for dev in (False, True) for fl in (1, 3)}
        rows = {}
        for dev in (False, True):
            for name, shape, flags, mod in (("sine", 0, 1, None), ("sine>tri", 12, 1, None), ("tri", 24, 1, None),
                                            ("tri>saw", 36, 1, None), ("sine+sync", 0, 3, None), ("tri+sync", 24, 3, None),
                                            ("tri+ramp", 24, 1, "lfo"), ("saw>sq+ramp", 68, 1, "lfo")):
                r = Rig(dev=dev); root, v = r.voice(0x32); r.table(0, 0x32, flags); r.note(v, 60)
                srcs = []
                for i in (0, 1):
                    r.prop(r.osc(v, i), K_SHAPE, shape, 0x32, i)
                    if mod:
                        srcs.append(route(r, r.osc(v, i), 0, 1.0))
                totals = []
                for b in range(6):
                    for src in srcs:
                        r.wf(src + 4, 0.02 * math.sin(b))
                    _, c = r.render(root, v, None, (0, 1)); r.block(); totals.append(sum(c))
                rows[(name, r.frames)] = sorted(totals)[len(totals) // 2]
            f = DEV_FRAMES if dev else FRAMES
            for name in ("sine", "sine>tri", "tri", "tri>saw", "sine+sync", "tri+sync"):
                self.assertLess(rows[(name, f)], 1.3 * mixed[dev], f"{name} n={f}: {rows[(name, f)]} vs mixed {mixed[dev]}")
            if dev:
                for name, fl in (("sine", 1), ("tri", 1), ("sine+sync", 3), ("tri+sync", 3)):
                    self.assertLessEqual(rows[(name, f)], saw[(dev, fl)],
                                         f"{name} n={f}: {rows[(name, f)]} vs the saw voice {saw[(dev, fl)]} (Q3b)")
            self.assertLess(rows[("tri+ramp", f)], 1.5 * rows[("saw>sq+ramp", f)], f"n={f}: the LOW ramp")
        print("\n  LOW hooks per voice-block (instructions): " + ", ".join(f"{n} n={f} {c}" for (n, f), c in rows.items())
              + f"; mixed n=128 {mixed[False]}, n=256 {mixed[True]}; saw n=128 {saw[(False, 1)]} (+sync "
              f"{saw[(False, 3)]}), n=256 {saw[(True, 1)]} (+sync {saw[(True, 3)]})", file=sys.stderr)

    @staticmethod
    def sinq(x):
        """The kernel's stated polynomial (SINQ), float64."""
        x = np.asarray(x, dtype=np.float64)
        return x * (SINQ[0] + x * x * (SINQ[1] + x * x * SINQ[2]))

    @classmethod
    def wave_k(cls, p, k):
        """The naive wave at phase p for kernel shape k (float64): below 0 p3_golden's blend with the sine as the stated
        sinq of the triangle (so the kernel's polynomial, not -cos); at and above 0 BE4.40's MIXED form (test_analog
        model(): blend min(1, 2k), width 0.5 - 0.84 (k - 0.5) above 0.5, dc 2 width - 1)."""
        if k < 0:
            tw = G.tri_weight(k); ws, wsaw = (1 - tw, 0.0) if k <= -0.5 else (0.0, 1 - tw)
            return ws * float(cls.sinq(G.tri(p))) + tw * float(G.tri(p)) + wsaw * float(G.saw(p))
        blend = min(1.0, 2 * k); width = 0.5 - (k - 0.5) * 0.84 if k > 0.5 else 0.5
        return (2 * p - 1) * (1 - blend) + ((1.0 if p < width else -1.0) - (2 * width - 1)) * blend

    def test_an_reshape_takes_each_side_in_its_own_form(self):
        """Q3 review (4.6's must: wave<MIXED> unchanged for k >= 0, the LOW form only for k < 0): an_reshape(from, to) adds
        half the waveform's step at the phase to `delayed`, 0.5 (wave_to(p) - wave_from(p)), each side in its own form:
        LOW > LOW, LOW > MIXED and MIXED > LOW across k = 0 both ways (k = 0 itself is MIXED: the saw), at phases 0.12 /
        0.37 / 0.81, against wave_k within 1e-6 (the sine as the stated sinq); k >= 0 pairs also bit for bit as BE4.40's
        own an_reshape (BE_REF), same state in, same state out. Before this test the mutation "LOW only when the target is
        below 0" (a LOW Shape read through wave<MIXED>: blend / width / dc unset) and a wrong correction on the one step
        that crosses 0 were seen by the aggregate click test alone, or not at all."""
        low = ((-1.0, -0.5), (-0.75, -0.25), (-0.25, -0.75), (-0.5, -0.02))
        across = ((-0.75, 0.3), (-0.02, 0.0), (0.3, -0.75), (0.0, -0.02), (-0.5, 0.8), (0.9, -1.0), (-0.25, 0.5))
        mixed = ((0.2, 0.7), (0.0, 0.5), (0.6, 0.1), (1.0, 0.25))
        def reshape(r, fn, ph, a, b):
            st = r.alloc(176); r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, ph, f32(0.1)))
            r.call(fn, st, fargs=(a, b))
            return bytes(r.u.mem_read(st, 176))
        r = Rig(); ref = Rig(img=REF_IMG, dsp=REF_DSP) if REF_OK else None
        for a, b in low + across + mixed:
            for ph in (0.12, 0.37, 0.81):
                a32, b32, p32 = f32(a), f32(b), f32(ph)
                with self.subTest(frm=a, to=b, phase=ph):
                    got = reshape(r, DSP["an_reshape"], p32, a32, b32)
                    phase, delayed = struct.unpack_from("<ff", got, 4)
                    self.assertEqual(phase, p32, "the phase is not moved")
                    want = 0.5 * (self.wave_k(p32, b32) - self.wave_k(p32, a32))
                    self.assertLessEqual(abs((delayed - f32(0.1)) - want), 1e-6, f"step {delayed - f32(0.1):.7g} vs {want:.7g}")
                    if (a, b) in mixed and ref:
                        self.assertEqual(got, reshape(ref, REF_DSP["an_reshape"], p32, a32, b32), "BE4.40's an_reshape")
        if not ref:
            print(f"\n  (no BE4.40 reference build at {REF}: the k >= 0 identity part not run)", file=sys.stderr)

    def test_the_sine_is_the_stated_sinq(self):
        """Q3 review: sinq's coefficients pinned, not only its error budget (SINQ_ERR lets a c1 off by 1e-5 through). Shape
        0 is the pure sine (tw = wsaw = 0: no BLEP or BLAMP, so each output sample is the wave at the previous sample's
        phase): from phase 0.37 at incs 0.0123, 0.0731 and 0.2, 512 samples, sample i + 1 = the stated polynomial at the
        triangle of the kernel's float32 phase after i + 1 steps (numpy float64; the phase law: p + inc, minus 1 at >= 1,
        each rounded to float32), within 1e-6. Shape 12 (half sine) likewise against the golden render (law f32) with its
        sine replaced by sinq at the delayed sample's phase."""
        for shape in (0, 12):
            for inc in (f32(0.0123), f32(0.0731), f32(0.2)):
                with self.subTest(shape=shape, inc=inc):
                    r = Rig(); p0 = f32(0.37); d0 = f32(float(G.naive(p0, shape)))
                    y, _, _, _, _ = self.kernel(r, self.state(r, p0, d0), inc, f32(k_of(shape)), 512)
                    ps, p = [], np.float32(p0)
                    for _ in range(512):
                        p = np.float32(p + np.float32(inc))
                        if p >= 1: p = np.float32(p - np.float32(1))
                        ps.append(float(p))
                    ps = np.array(ps[:-1])
                    ws = G.weights(shape)[0]
                    if shape == 0:
                        want = self.sinq(G.tri(ps))
                    else:
                        g = G.render(inc, shape, 512, phase=p0, delayed=d0, law="f32")
                        want = g.y[1:] + ws * (self.sinq(G.tri(ps)) - G.sine(ps))
                    err = float(np.max(np.abs(y[1:] - want)))
                    self.assertLessEqual(err, 1e-6, f"worst {err:.3g}")

    def test_the_ramp_across_the_plateaus_clicks_no_more_than_saw_to_square(self):
        """6.4: Shape under an 8 Hz LFO of +-19 (as ShapeMod.test_the_ramp_is_close_to_a_per_sample_one, here from its trough,
        so the 24 blocks sweep the whole range up: centre - 19 to centre + 19), ramped as an_osc
        does (an_reshape between parts, the shape linear in the kernel's coordinate; AN_RAMP = 32 frames a step, AN_RAMP_LOW
        = 16 where either end of the block's ramp is below the saw), against a step every sample. The click energy: the
        first difference of (ramp - per-sample) at the two samples around each part edge, summed per block, RMS over the
        blocks (so 16 edges a block count as 16). Across 22..26 (centre 24), the triangle > saw morph (28, the worst at
        AN_RAMP) and 45 / 48 (46) it is at most the saw > square ramp's (centre 68, BE4.40's: that path is bit-identical to
        BE4.40's, P3Identity). Q3, M: with AN_RAMP below the saw too the 6.4 criterion failed (per edge 1.02 / 1.08 / 0.95 x
        saw > square's at 24 / 28 / 46: the LOW map moves k twice as fast a Shape step), hence AN_RAMP_LOW."""
        r = Rig(); N, B = 256, 24
        def run(centre, step):
            """step 0: as an_osc (16 / 32); 1: a step every sample. (samples, the diff indices around each part edge)"""
            st = r.alloc(176); out = r.alloc(4 * N); ev = r.alloc(128)
            r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, 0.0, 0.0)); r.u.mem_write(ev, bytes([255]) * 128)
            xs, kp, at = [], None, []
            for b in range(B):
                k1 = f32(k_of(centre - 127 * 0.15 * math.cos(2 * math.pi * 8 * b * N / DEV_RATE)))
                k0 = k1 if kp is None else kp
                P = N // (step or (16 if k0 < 0 or k1 < 0 else 32))
                cur = k0
                for j in range(P):
                    k = k1 if j + 1 == P else f32(k0 + (k1 - k0) * ((j + 1) / P))
                    if k != cur:
                        r.call(DSP["an_reshape"], st, fargs=(cur, k))
                        at += [b * N + j * (N // P) - 1, b * N + j * (N // P)]
                    cur = k
                    r.call(DSP["an_render"], st, out + 4 * j * (N // P), N // P, ev, sargs=(0,), fargs=(f32(130.81 / DEV_RATE), k))
                kp = k1
                xs += struct.unpack(f"<{N}f", r.u.mem_read(out, 4 * N))
            return np.array(xs), [i for i in at if N <= i < B * N - 1]
        def click(centre):
            (x, at), (ref, _) = run(centre, 0), run(centre, 1)
            d = np.diff(x - ref)
            return math.sqrt(float(np.sum(d[at] ** 2)) / (B - 1)), len(at) / (B - 1) / 2
        ref, re = click(68)
        got = {c: click(c) for c in (24, 28, 46)}
        print(f"\n  ramp click per block: saw > square (68) {ref:.3g} ({re:.0f} edges a block); "
              + "; ".join(f"centre {c} {v:.3g} ({e:.0f})" for c, (v, e) in got.items()), file=sys.stderr)
        self.assertLessEqual(re, 8, "saw > square: AN_RAMP")
        for c, (v, e) in got.items():
            self.assertGreater(e, 8, f"centre {c}: AN_RAMP_LOW where an end is below the saw")
            self.assertLessEqual(v, ref, f"centre {c}")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
