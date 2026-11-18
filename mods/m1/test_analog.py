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
P3 Q1 (docs/plan-p3-fm-bits.md 2.4, 3.3, 6.1, 6.2): Props stores Sub FM and FM 1>2 and runs stock's M7 engine input
(H_INPUT_SITE) with all our keys (OD7); P3Identity replays the identity set on this build and on the BE4.40 reference build
(BE_REF, default ~/bento/out/m1-be440: its image and its manifest's DSP symbols) and compares them byte for byte.
BE4.42 (docs/plan-p3-fm-bits.md, BE4.42): Bit Depth (P3 Q2 / Q2b, key 0x1A4, an_crush / an_dc) is removed. BitDepthGone
checks a BE4.41 Bit Depth value (pushed as 0x1A4, as a BE4.41 project's push sends it) plays nothing and is not stored, the
FM stats line, the DSP's libm refusal (now: an_index's VSQRT inline) and the pulse width the FM golden takes from the
kernel (kernel_dc32, pinned to an_render's plateaus). FD3's index law is I = 16 (v/1000)^1.5 (an_index below).
P3 Q3 (plan 4.6, OD6): ShapeLow checks the five shapes' LOW regime (sine, triangle and their morphs below the saw) against
p3_golden.py sample by sample and by spectrum, an_shape's map in the voice path, the sync roles and the Shape ramp across the
new plateaus; P3Identity covers 45..127 against BE4.40.
BE4.43 (docs/plan-p3-fm-bits.md, BE4.43; Jeff's design 2026-11-16): one FM amount per oscillator (OSC2's Sub FM plays only
while OSC1 is not Analog: the tests that had it next to an Analog OSC1 are reviewed expected changes, each marked BE4.43) and
the oscillator's own FM envelope (FM Atk 0x1A4, FM Dec 0x1A0; 0x1A4 was BE4.41's Bit Depth): FmEnv checks its index lines
against p3_golden.fm_env_lines bit for bit, P3Identity masks the envelope's own state bytes (ENV_BYTES), BitDepthGone is
restated for the envelope's defaults and 0x1A4 as FM Atk.
"""
import json, math, os, random, struct, sys, types, unittest
from fractions import Fraction
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
import p3_golden as G                                            # P3's independent golden model (plan 6.3)
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
# our oscillator keys (docs/plan-native-props.md 3.1), typed: Type, Shape, Sync, Glide; P3 Q1: Sub FM, FM 1>2. K_BITS:
# BE4.41's Bit Depth, no key of ours in BE4.42. BE4.43 (docs/plan-p3-fm-bits.md BE4.43): the FM envelope, FM Atk on that
# same key (K_FMATK = K_BITS, a new XML token: a BE4.41 file's bento_bits is not read, test_native_props) and FM Dec
K_TYPE, K_SHAPE, K_SYNC, K_GLIDE = 0x1C0, 0x1A7, 0x1AB, 0x1B7
K_SUBFM, K_BITS, K_FM12 = 0x18B, 0x1A4, 0x132
K_FMATK, K_FMDEC = 0x1A4, 0x1A0
FM_DEC_INF = 128            # FM Dec's Inf (the default: env 1); p3_golden.FM_DEC_INF
FMK_AT, FME_AT = 142, 176   # BE4.43: AnOsc.fmk (u16: stage << 13 | blocks) and AnOsc.fme (f32), in the kernel area
MSUB_AT = 172               # AnOsc.msub, m12 (u16 each; P3 3.3)
SA_FMSTATS = 0x100          # SaFmStats {fm, r1, fm_skipped, fm_missed, ..} (P3 3.3; r1: BE4.41's bits=, 0 since BE4.42)
RUN_FMTAG = 36              # Run.fmtag (u32, P3 3.3: the run state's word 9)
PROPS_AT = 0xB4             # AnProps in the kernel area: {u8 type, shape, sync, spare3 (BE4.41's bits); u16 glide, subfm, fm12, spare}
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
        """AnProps: (type, shape, sync, fmatk, glide, subfm, fm12, fmdec, envf) (BE4.43: FM Atk at +3, FM Dec +10, the
        envelope's set flag +11; BE4.42: +3 spare, +10 a spare halfword)."""
        return struct.unpack("<BBBBHHHBB", self.u.mem_read(self.osc(v, i) + S.OSC_KERNEL + PROPS_AT, 12))
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


class _Moved:
    """The base table with some entries replaced (a candidate layout for build.py's checks to refuse)."""
    def __init__(self, base, **kw):
        self._b, self._kw = base, kw
    def __getattr__(self, name):
        return self._kw[name] if name in self._kw else getattr(self._b, name)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class WindowGuards(unittest.TestCase):
    """BE4.44 W0 (docs/plan-wt-fm.md 5, buy-back 1): the hold-shortcut stubs moved to the page above our M7 slice, so the
    DSP window ends at M7_TRAMP (40 KB). check_dsp_assumptions bounds the window by M7_TRAMP (S6a's old bound, CTRL_INPUT,
    now sits above the slice and would let a window run over the trampolines and the C slice: plan mutation 25), and
    check_ctrl_placement keeps the stubs clear of the slice, of the window and of the disk-mode image."""
    def refused(self, check, want):
        with self.assertRaises(SystemExit) as cm:
            check()
        self.assertIn(want, str(cm.exception))

    def test_this_layout_passes(self):
        self.assertEqual((S.M7_DSP_LIMIT, S.CTRL_INPUT, S.CTRL_STARTUP),
                         (S.M7_TRAMP, S.M7_LIMIT, S.M7_LIMIT + 0x800), "window up to M7_TRAMP; stubs one page up, same layout")
        build.check_dsp_assumptions(STOCK_IMG, S)
        build.check_ctrl_placement(S, (S.M7_DSP, S.M7_DSP_LIMIT))
        w = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        self.assertNotEqual(w(S.CTRL_INPUT), 0, "the input stub is at CTRL_INPUT")
        self.assertNotEqual(w(S.CTRL_STARTUP), 0, "the startup stub is at CTRL_STARTUP")

    def test_a_window_over_our_m7_slice_is_refused(self):
        """A window one page past M7_TRAMP (over the trampolines), and the old bound's largest (up to CTRL_INPUT, over the
        whole C slice): refused by the bound itself, not by a later check that happens to trip (the page above M7_TRAMP holds
        a stock word valued there, 0x081AF024's, which DSP_WINDOW_WORDS would also catch)."""
        for hi in (S.M7_TRAMP + 0x1000, S.CTRL_INPUT):
            with self.subTest(hi=hex(hi)):
                self.refused(lambda: build.check_dsp_assumptions(STOCK_IMG, _Moved(S, M7_DSP_LIMIT=hi)),
                             "below our M7 slice")

    def test_stubs_inside_the_c_slice_are_refused(self):
        moved = _Moved(S, CTRL_INPUT=S.M7_LIMIT - 0x200, CTRL_STARTUP=S.M7_LIMIT - 0x100)
        self.refused(lambda: build.check_ctrl_placement(moved, (S.M7_DSP, S.M7_DSP_LIMIT)), "overlaps our M7 slice")
        # control: buy-back 1b (the same stubs with the C slice's limit lowered by 0x200) is accepted
        build.check_ctrl_placement(_Moved(moved, M7_LIMIT=S.M7_LIMIT - 0x200), (S.M7_DSP, S.M7_DSP_LIMIT))

    def test_stubs_inside_the_window_or_past_the_m7_image_are_refused(self):
        old = _Moved(S, CTRL_INPUT=S.M7_TRAMP - 0x1000, CTRL_STARTUP=S.M7_TRAMP - 0x800)   # BE4.43's place
        self.refused(lambda: build.check_ctrl_placement(old, (S.M7_DSP, S.M7_DSP_LIMIT)), "overlaps the DSP window")
        build.check_ctrl_placement(old, None)                 # control: a build without the window (1.6.1's layout)
        late = _Moved(S, CTRL_STARTUP=build.M7_IMAGE_END - 0x80)
        self.refused(lambda: build.check_ctrl_placement(late, (S.M7_DSP, S.M7_DSP_LIMIT)), "past the M7 image's end")

    def test_the_bounds_are_exact(self):
        """W0 review: each bound at its edge. The startup slot may end exactly at 0x080E0000, not 4 B past it; the stubs may
        touch our M7 slice or a window on either side, not reach 4 B into it."""
        win = (S.M7_DSP, S.M7_DSP_LIMIT)
        end = build.M7_IMAGE_END
        build.check_ctrl_placement(_Moved(S, CTRL_STARTUP=end - 0x100), win)
        self.refused(lambda: build.check_ctrl_placement(_Moved(S, CTRL_STARTUP=end - 0xFC), win), "past the M7 image's end")
        at = lambda lo: _Moved(S, CTRL_INPUT=lo, CTRL_STARTUP=lo + 0x800)        # [lo, lo + 0x900)
        build.check_ctrl_placement(at(S.M7_LIMIT), win)                          # right above the slice (this layout)
        self.refused(lambda: build.check_ctrl_placement(at(S.M7_LIMIT - 4), win), "overlaps our M7 slice")
        self.refused(lambda: build.check_ctrl_placement(at(S.M7_TRAMP - 0x900 + 4), None), "overlaps our M7 slice")
        lo, hi = S.M7_DSP + 0x1000, S.M7_DSP + 0x3000                           # a window with room on both sides
        build.check_ctrl_placement(at(hi), (lo, hi))
        build.check_ctrl_placement(at(lo - 0x900), (lo, hi))
        self.refused(lambda: build.check_ctrl_placement(at(hi - 4), (lo, hi)), "overlaps the DSP window")
        self.refused(lambda: build.check_ctrl_placement(at(lo - 0x900 + 4), (lo, hi)), "overlaps the DSP window")

    def test_build_runs_both_checks_with_its_window(self):
        """W0 review: build() itself calls check_dsp_assumptions and check_ctrl_placement, the latter with the window the
        former checked (deleting the call, or passing None, would disarm the guard while the direct tests stay green). The
        checks are wrapped: the real ones run, then the build stops before it writes anything (out already exists)."""
        import tempfile
        calls, real = [], (build.check_dsp_assumptions, build.check_ctrl_placement)
        class Stop(Exception): pass
        def dsp(img, s):
            calls.append(("dsp", (s.M7_DSP, s.M7_DSP_LIMIT))); real[0](img, s)
        def ctrl(s, window):
            calls.append(("ctrl", window)); real[1](s, window); raise Stop
        saved = {k: getattr(build, k) for k in ("S", "MARKER", "VERSION", "DISPLAY", "SETTINGS", "DISABLED", "OMITTED")
                 if hasattr(build, k)}
        build.check_dsp_assumptions, build.check_ctrl_placement = dsp, ctrl
        try:
            with tempfile.TemporaryDirectory() as out, self.assertRaises(Stop):
                build.build(STOCK_IMG, Path(out))
        finally:
            build.check_dsp_assumptions, build.check_ctrl_placement = real
            for k, v in saved.items():
                setattr(build, k, v)
        win = (S.M7_DSP, S.M7_DSP_LIMIT)
        self.assertEqual(calls, [("dsp", win), ("ctrl", win)])


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
        widget): Sync any value but 0 is On (BE4.40: only 1 was); the type-10 amounts clamp to 0..1000. BE4.42: BE4.41's Bit
        Depth key 0x1A4 was not ours. BE4.43: it is FM Atk's (byte +3), clamped to 0..127, and FM Dec (byte +10) to 0..128;
        a fresh Analog oscillator has FM Dec 128 (Inf, the template's default) and byte +11 stays 0."""
        r = Rig(); root, v = r.voice(0x61)
        o = r.osc(v, 0)
        r.prop(o, K_TYPE, 1)                                     # (slot 8 returns nothing: the effects are checked)
        self.assertEqual(r.settings(v, 0), (1, 48, 0, 0, 0, 0, 0, FM_DEC_INF, 0),
                         "Analog: Shape at its default (saw), FM Dec Inf (BE4.43), the rest 0, the envelope not set")
        # settings(): (type, shape, sync, fmatk, glide, subfm, fm12, fmdec, envf)
        for key, val, want, at in ((K_SHAPE, 1000, 127, 1), (K_SHAPE, -5, 0, 1), (K_SHAPE, 77, 77, 1), (K_GLIDE, 5000, 1000, 4),
                                   (K_GLIDE, -1, 0, 4), (K_GLIDE, 640, 640, 4), (K_SYNC, 7, 1, 2), (K_SYNC, 0, 0, 2),
                                   (K_SYNC, 1, 1, 2), (K_SYNC, -1, 1, 2), (K_SYNC, 0x10000, 1, 2),
                                   (K_SUBFM, 1000, 1000, 5), (K_SUBFM, 5000, 1000, 5), (K_SUBFM, -3, 0, 5), (K_SUBFM, 437, 437, 5),
                                   (K_FM12, 1000, 1000, 6), (K_FM12, 99999, 1000, 6), (K_FM12, -1, 0, 6), (K_FM12, 1, 1, 6),
                                   (K_FMATK, 1000, 127, 3), (K_FMATK, 128, 127, 3), (K_FMATK, -5, 0, 3), (K_FMATK, 77, 77, 3),
                                   (K_FMATK, 0x7FFFFFFF, 127, 3), (K_FMDEC, 129, 128, 7), (K_FMDEC, 500, 128, 7),
                                   (K_FMDEC, -1, 0, 7), (K_FMDEC, 128, 128, 7), (K_FMDEC, 64, 64, 7), (K_FMDEC, 0, 0, 7)):
            with self.subTest(key=hex(key), value=val):
                n0 = r.r32(SA + SA_STATS + 16)
                r.prop(o, key, val)
                self.assertEqual((r.settings(v, 0)[at], r.r32(SA + SA_STATS + 16) - n0), (want, 1))
        self.assertEqual(r.settings(v, 0)[3:9:4], (127, 0), "control: Atk 127, Dec 0 last")
        self.assertEqual(r.settings(v, 0)[8], 1, "envf: the envelope set")
        for k, val, envf in ((K_FMATK, 0, 1), (K_FMDEC, FM_DEC_INF, 0), (K_FMDEC, 127, 1), (K_FMDEC, 900, 0), (K_FMATK, 1, 1)):
            r.prop(o, k, val)
            self.assertEqual(r.settings(v, 0)[8], envf, f"envf after {k:#x} = {val}: Atk != 0 or Dec != Inf")

    def test_the_fm_keys_are_ours_and_kept_only_while_analog(self):
        """P3 Q1: an_prop takes the FM keys (slot 8 returns 1: stock's own slot 8 never sees them), stores nothing for a
        Wavetable oscillator (its kernel area is stock's history), and a fresh Analog state starts them at 0, with the AnOsc
        ramp memory msub / m12 (P3 3.3) at 0; leaving Analog zeroes them with the area. BE4.42: 0x1A4 (BE4.41's Bit Depth) was
        stock's. BE4.43: FM Atk (0x1A4) and FM Dec (0x1A0) are ours the same way: a fresh state at Atk 0 / Dec Inf, its
        envelope at AN_FE_START (fmk 0, fme 0). BE4.44 W1: a Wavetable oscillator keeps them too, once one is not a default
        (WtFmW1); at the defaults its area stays stock's history."""
        r = Rig(); root, v = r.voice(0x6A)
        o = r.osc(v, 1)
        hist = bytes(range(0xC0))
        r.u.mem_write(o + S.OSC_KERNEL, hist)
        props = lambda: r.r32(SA + SA_STATS + 16)              # (slot 8 returns nothing: the props count says whose it was)
        # BE4.44 W1 (reviewed change, docs/plan-wt-fm.md 3.1): a Wavetable oscillator keeps a value that is not a default
        # (WtFmW1); its defaults, and OSC2's Sub FM (D2), write nothing
        for k, x in ((K_SUBFM, 3), (K_FM12, 0), (K_FMATK, 0), (K_FMDEC, FM_DEC_INF)):
            n0 = props(); r.prop(o, k, x)
            self.assertEqual(props() - n0, 1, f"{k:#x}: ours")
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL, 0xC0)), hist, "Wavetable at its defaults: untouched")
        n0 = props(); r.prop(o, 0xC9, 3)
        self.assertEqual(props(), n0, "control: a stock key is stock's")
        r.prop(o, K_TYPE, 1)
        self.assertEqual(r.settings(v, 1)[3:8], (0, 0, 0, 0, FM_DEC_INF), "fresh: Atk 0, Glide 0, no FM, Dec Inf")
        self.assertEqual(struct.unpack("<HH", r.u.mem_read(o + S.OSC_KERNEL + MSUB_AT, 4)), (0, 0), "msub, m12")
        self.assertEqual(struct.unpack("<H", r.u.mem_read(o + S.OSC_KERNEL + FMK_AT, 2))[0], 0, "fmk: AN_FE_START")
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL + FME_AT, 4)), bytes(4), "fme 0")
        for k, val in ((K_SUBFM, 900), (K_FMATK, 2), (K_FM12, 250), (K_FMDEC, 40)):
            r.prop(o, k, val)
        self.assertEqual(r.settings(v, 1)[3:8], (2, 0, 900, 250, 40), "each key its own byte (BE4.43)")
        r.prop(o, K_TYPE, 0)
        self.assertEqual(bytes(r.u.mem_read(o + S.OSC_KERNEL, 0xC0)), bytes(0xC0))

    def test_the_silent_paths_zero_the_fm_ramp_memory(self):
        """P3 6.4 (Q1 review): msub / m12 read 0 after each path that renders nothing, so an oscillator heard again starts
        from its amounts, no ramp: the silent skip (Level 0, not a master), a block past 1024 frames, and (E3) a silent sync
        master that only tracks its phase. Nothing writes them in Q1, so they are set by hand before every block. reset()'s
        own clear has no case: it runs only right after forget has zeroed the whole area (Type 1 on a non-Analog
        oscillator), so no test can see it (M: the mutation removing it is equivalent)."""
        msub = lambda r, o: struct.unpack("<HH", r.u.mem_read(o + S.OSC_KERNEL + MSUB_AT, 4))
        # (Q6 review: OSC2's m12 makes OSC1 feed B, E3 (a), so the E3 cases write OSC2's m12 0: the silent master stays on
        # its own path, an_track (no Sub FM ramp) or E3 (b)'s FM render (msub 123: Q5), each pinned by its calls)
        for name, which, sync, lv, n, mine, oth in (("skip", 1, 0, (1.0, 0.0), None, (123, 456), (123, 456)),
                                                    ("n > 1024", 1, 0, (1.0, 1.0), 2048, (123, 456), (123, 456)),
                                                    ("E3 silent master (an_track)", 0, 1, (0.0, 1.0), None, (0, 456), (123, 0)),
                                                    ("E3 (b) silent master", 0, 1, (0.0, 1.0), None, (123, 456), (123, 0))):
            with self.subTest(case=name):
                r = Rig(); root, v = r.voice(0x6C)
                calls = fm_capture(r)
                for i in (0, 1):
                    r.prop(r.osc(v, i), K_TYPE, 1)
                r.prop(r.osc(v, 1), K_SYNC, sync)
                r.note(v, 60)
                for i in (0, 1):
                    r.level(v, i, lv[i])
                o, other = r.osc(v, which), r.osc(v, 1 - which)
                for b in range(4):                               # (Level's smoother settles in the first block)
                    for x, w in ((o, mine), (other, oth)):
                        r.u.mem_write(x + S.OSC_KERNEL + MSUB_AT, struct.pack("<HH", *w))
                    self.assertEqual(msub(r, o), mine, "control: written where AnOsc keeps them")
                    k0, t0 = len(calls), len(calls.track)
                    r.render(root, v, n, (0, 1)); r.block()
                self.assertEqual(msub(r, o), (0, 0), f"{name}: msub, m12 after the block")
                if which == 0:                                   # (the last block: silent)
                    self.assertEqual(calls.feeds, [], "control: OSC1 feeds nothing")
                    tr = [x for x, _ in calls.track[t0:] if x == o]; fc = [x for x in calls[k0:] if x["osc"] == o]
                    ch = -(-r.frames // AN_EVENTS_N)              # (its sync chunks: 1 at Rig()'s n)
                    self.assertEqual((len(tr), len(fc)), (ch, 0) if mine[0] == 0 else (0, ch), f"{name}: the path")
                if n is None:                                    # P3 Q5 (reviewed edit): the playing path ramps msub (Sub FM 0
                    # there: 123 -> 0 over the block, so 0 after it); P3 Q6 (reviewed edit): and m12 the same way (FM 1>2 0:
                    # 456 -> 0 on OSC2; OSC1's m12 is always 0), so (0, 0) where Q5 kept 456
                    self.assertEqual(msub(r, other), (0, 0), f"{name}: control, the oscillator that played")

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
        the memset: 0x05, 0x27 and 0x31 only, M). Each of our eight keys (BE4.43: the FM envelope's 0x1A4 and 0x1A0 too), at
        its extremes, an oscillator's track handle and either index: posted once, unchanged, never broadcast; and the posted
        message, through stock's root (WT_ROOT_PROPERTY), reaches an_prop for that oscillator."""
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
        keys = (K_TYPE, K_SHAPE, K_SYNC, K_GLIDE, K_SUBFM, K_FM12, K_FMATK, K_FMDEC)
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
        self.assertEqual(delivered, 14, "the seven non-Type keys, both indexes, through stock's root")
        at = {K_SHAPE: 1, K_SYNC: 2, K_FMATK: 3, K_GLIDE: 4, K_SUBFM: 5, K_FM12: 6, K_FMDEC: 7}
        self.assertEqual([r.settings(v, i)[at[k]] for i in (0, 1) for k in at], [1] * 14,
                         "the value 1 of each key, in each oscillator of the voice")

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

    def test_slot_10_called_from_outside_the_route_handler_keeps_nothing(self):
        """BE4.45 X1 (docs/plan-fm-targets.md): the M4 sends routings on Sub FM (0x18B) and FM 1>2 (0x132). X2: the class's
        connect (slot 10) keeps them only when called from stock's route handler (WT_ROUTE: it hands the root; FmTargetsX2).
        Called from anywhere else (here: directly), on an Analog and a Wavetable oscillator, any slot 0..3, it writes nothing
        and the voice renders bit for bit as without them (X1's inert test, kept); a Position routing still lands (control)."""
        outs = []
        for routed in (False, True):
            r = Rig(dev=True); root, v = r.voice(0x73); r.table(0, 0x73, 1); r.note(v, 60)
            o1, o2 = r.osc(v, 0), r.osc(v, 1)
            r.prop(o1, K_SUBFM, 300); r.prop(o2, K_FM12, 200)
            if routed:
                calls = []
                for o in (o1, o2):
                    for key in (K_SUBFM, K_FM12):
                        for slot in range(4):
                            src = r.alloc(0x10); r.wf(src + 4, 1.0)
                            calls.append((o, src, key, slot))
                wrote = []
                h = r.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, sz, val, _: wrote.append(a)
                                 if not STACK - 0x1000 <= a < STACK else None)
                for o, src, key, slot in calls:                     # route()'s call: the class's slot 10
                    r.call(r.r32(r.r32(o) + 0x28) & ~1, o, src, key, slot, fargs=(0.7,))
                r.u.hook_del(h)
                self.assertEqual(len(calls), 16)
                self.assertEqual(wrote, [], "slot 10 wrote nothing")
            xs = []
            for b in range(8):
                out, _ = r.render(root, v); xs += out; r.block()
            outs.append(xs)
        self.assertGreater(max(abs(x) for x in outs[0]), 0.01, "the voice plays (control)")
        same(self, outs[1], outs[0], "routed Sub FM / FM 1>2: today's sound")
        r = Rig(dev=True); _, v = r.voice(0x75); r.table(0, 0x75, 0)  # Wavetable oscillators: the same class slot
        wrote = []
        h = r.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, acc, a, sz, val, _: wrote.append(a) if not STACK - 0x1000 <= a < STACK else None)
        for o in (r.osc(v, 0), r.osc(v, 1)):
            for key in (K_SUBFM, K_FM12):
                r.call(r.r32(r.r32(o) + 0x28) & ~1, o, 0x24001000, key, 0, fargs=(0.7,))   # addr-ok: harness (any source)
        r.u.hook_del(h)
        self.assertEqual(wrote, [], "slot 10 wrote nothing on a Wavetable oscillator")
        r = Rig(dev=True); _, v = r.voice(0x74); o = r.osc(v, 0)
        src = route(r, o, 1, 0.5)
        self.assertEqual(r.r32(o + 0x30 + 16), src, "Position still routes (control)")

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

P3_STOP = 1.0195           # docs/plan-wt-fm.md 6: P3Identity's stop line (M1's own bound is 1.02)


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
        cls.costs, cls.copies = [], []

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "costs", None):
            worst = max(cls.costs, key=lambda c: c[1] / c[2])
            print(f"\n  P3Identity: {len(cls.costs)} cases, instructions this build / BE4.40: worst {worst[1] / worst[2]:.4f} "
                  f"({worst[0]}: {worst[1]} / {worst[2]}), total {sum(c[1] for c in cls.costs) / sum(c[2] for c in cls.costs):.4f}",
                  file=sys.stderr)
        if getattr(cls, "copies", None):                      # P3 Q4: buffer A's fills, left out of the costs above
            print(f"  P3Identity: sub_fill per call (the copy, Rig's zero sub where not stock's root): " + ", ".join(
                f"{n} {t / c:.0f}" for n, c, t in cls.copies), file=sys.stderr)

    # Sub FM 70 %, Bit Depth Full, FM 1>2 33.3 %: test_q1_plays_no_amount. P3 Q2: Bit Depth 2 (8 bits) was in it through Q1;
    # it played from Q2 to BE4.41, so it left this tuple then. P3 Q5: Sub FM plays, so its 700 left too, for
    # SubFm.test_the_amounts_cases_are_the_expected_change (same cases). P3 Q6: FM 1>2 plays where both oscillators are
    # Analog; test_q1_plays_no_amount keeps it on the two voices where it cannot. BE4.42: Bit Depth is gone, so a BE4.41
    # value (2, 8 bits, as a BE4.41 project pushes it) is back here: it must play and store nothing, BE4.40 byte for byte.
    # BE4.43: key 0x1A4 is FM Atk's, so the 2 is FM Atk 2 (3 blocks): the envelope is stored and runs (fm_env_step) on
    # every Analog oscillator here, and plays nothing where no FM can play (the two cases left): BE4.40 byte for byte
    AMOUNTS = (0, 2, 333)
    # the kernel-area bytes of P3's settings (AnProps fmatk (BE4.41's bits), subfm, fm12): stored on this build, never on BE4.40
    P3_BYTES = (PROPS_AT + 3, PROPS_AT + 6, PROPS_AT + 7, PROPS_AT + 8, PROPS_AT + 9)
    # BE4.43: the envelope's own bytes on every Analog oscillator: AnOsc.fmk (where BE4.40's 32-bit note had its high half:
    # 0 for a note, 0xFFFF for none), AnOsc.fme (spare on BE4.40), FM Dec (AnProps +10, Inf = 128 by default, BE4.40's
    # spare 0) and envf (+11, 0 by default). The note's low half (+140) is still compared
    ENV_BYTES = (FMK_AT, FMK_AT + 1, FME_AT, FME_AT + 1, FME_AT + 2, FME_AT + 3, PROPS_AT + 10, PROPS_AT + 11)

    @staticmethod
    def push(r, osc, handle, index, vals, p3=(0, 0, 0)):
        """A push as stock sends it: Type first, then the rest in template order; P3's three at p3 (Sub FM, 0x1A4, FM 1>2),
        by default 0. p3[1] is BE4.41's Bit Depth key, which BE4.41's template pushed; BE4.42 had no such key; BE4.43: it is
        FM Atk's (the template's order: Sub FM, FM 1>2, FM Atk, FM Dec), and FM Dec goes last, p3[3] or Inf (128, the
        template's default; BE4.40's slot 8 ignores both). A pair p3 = (Sub FM, FM 1>2) pushes no envelope key at all
        (BE4.42's push: the fresh state's defaults stand)."""
        if len(p3) == 2:
            keys, p = (K_TYPE, K_SHAPE, K_SYNC, K_GLIDE, K_SUBFM, K_FM12), tuple(vals) + tuple(p3)
        else:
            q = tuple(p3) + (FM_DEC_INF,) * (4 - len(p3))
            keys, p = (K_TYPE, K_SHAPE, K_SYNC, K_GLIDE, K_SUBFM, K_FM12, K_FMATK, K_FMDEC), tuple(vals) + (q[0], q[2], q[1], q[3])
        for k, v in zip(keys, p):
            r.prop(osc, k, v, handle, index)

    @classmethod
    def masked(cls, voice, oscs, wt=()):
        """The voice's bytes with P3's settings bytes of the Analog oscillators oscs zeroed (test_q1_plays_no_amount), and
        (BE4.43) the envelope's bytes of every Analog oscillator (ENV_BYTES; its magic in the area says which). BE4.44 W1: and
        the saved runs (WT_RUNS) of the Wavetable oscillators wt, tagged on this build (WtFmW1 checks what they hold)."""
        b = bytearray(voice)
        for i in wt:
            k = (S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL
            for off, n in WT_RUNS:
                b[k + off:k + off + n] = bytes(n)
        for i in oscs:
            for at in cls.P3_BYTES:
                b[(S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL + at] = 0
        for i in (0, 1):
            k = (S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL
            if struct.unpack_from("<I", b, k)[0] == AN_MAGIC:
                for at in cls.ENV_BYTES:
                    b[k + at] = 0
        return bytes(b)

    def voice_case(self, r, shape=(48, 48), sync=0, glide=0, notes=None, blocks=8, levels=None, mod=None, n=None, level0=1.0,
                   types=(1, 1), p3=(0, 0, 0)):
        """p3: P3's three for both oscillators, or a pair of them (OSC1's, OSC2's)."""
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
        for k, x in zip((K_SUBFM, K_FMATK, K_FM12), p3):         # (BE4.43: 0x1A4 is FM Atk's)
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
        r.v = m.v                                                # (check: the voice, for its tagged Wavetable oscillators)
        for k, x in zip((K_GLIDE, K_SUBFM, K_FMATK, K_FM12), (0,) + tuple(p3)):   # (BE4.43: 0x1A4 is FM Atk's)
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

    def check(self, name, run, dev, mask=(), fills=None, fill_max=None, env=False, wtier=False):
        """mask: the Analog oscillators whose P3 settings bytes (stored on this build, not on BE4.40) the voice leaves out.
        fills: P3 Q4, this build fills buffer A (Sub FM set) that many times: sub_fill's own instructions (Span, its stock
        helpers included) are the copy's, recorded and left out of the +2 % bound, which holds for the rest (a carve-out,
        6.7), and bounded on their own: fill_max instructions a call. env: BE4.43, an FM envelope is set (FM Atk 2 in
        AMOUNTS): feeds()'s own instructions, where it steps the envelope (fm_env_step) every oscillator-block, are its
        stated cost (FmEnv.test_cost bounds it), recorded and left out of the +2 % bound the same way. wtier (BE4.44 W1): a
        Wavetable oscillator is tagged (tier 2): wt_fm_osc's own instructions, bounded at wtier a call (measured + about
        15 %), carved out the same way. Every case is also held to plan 6's stop line, P3_STOP (1.95 %), not only M1's 2 %."""
        ra = Rig(dev=dev)
        span = Span(ra, dsp_sym("sub_fill")) if fills is not None else None
        espan = Span(ra, dsp_sym("feeds")) if env else None
        # BE4.44 W1 (wtier): tier 2 (wt_fm_osc) and stock's render inside it, for the cases that tag a Wavetable oscillator
        wspan = (Span(ra, dsp_sym("wt_fm_osc")), Span(ra, S.WT_OSC_RENDER)) if wtier else None
        a = run(ra)
        b = run(Rig(img=REF_IMG, dev=dev, dsp=REF_DSP))
        self.assertGreater(max(abs(x) for x in struct.unpack(f"<{len(b[0]) // 4}f", b[0])), 0.05, f"{name}: control, it plays")
        same(self, a[0], b[0], f"{name}: the output")
        wt = tuple(i for i in (0, 1) if wt_tagged(a[1], (S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL,
                                                    ra.v + (S.VOICE_WT1, S.VOICE_WT2)[i])) if wtier else ()
        same(self, self.masked(a[1], mask, wt), self.masked(b[1], mask, wt),
             f"{name}: the voice (both oscillators, their kernel areas, the sub)")
        self.assertEqual(a[2], b[2], f"{name}: the stats words")
        copy = 0
        if span:
            self.assertEqual(span.calls, fills, f"{name}: buffer A filled once per voice-block (P3 Q4)")
            copy = span.total
            self.copies.append((name, span.calls, copy))
            self.assertLessEqual(copy, span.calls * fill_max, f"{name}: sub_fill {copy / span.calls:.0f} a call (bound {fill_max})")
        if espan:
            self.assertGreater(espan.calls, 0, f"{name}: control, the envelope stepped")
            copy += espan.total
            self.copies.append((name + " (envelope steps)", espan.calls, espan.total))
        if wspan:
            # BE4.44 W1: a tagged Wavetable oscillator (tier 2) is the Wavetable path's cost, not the Analog FM-off path's
            # this bound is for: wt_fm_osc's own instructions (its runs, the envelope step; stock's render inside it stays in
            # the count, as on BE4.40) are recorded and bounded on their own (WtFmW1 holds tier 2 to 1.02 x stock's render)
            own = wspan[0].total - wspan[1].total
            self.assertEqual(len(wt), 1, f"{name}: control, one oscillator tagged")
            self.assertLessEqual(own, wspan[0].calls * wtier, f"{name}: tier 2's own {own / wspan[0].calls:.0f} a call (bound {wtier})")
            copy += own
            self.copies.append((name + " (WT tier 2)", wspan[0].calls, own))
        self.costs.append((name, a[3] - copy, b[3]))
        self.assertLessEqual(a[3] - copy, b[3] * 1.02, f"{name}: instructions {a[3]} - {copy} (the copy) vs BE4.40's {b[3]} (M1: +2 %)")
        # BE4.44 W1 review: plan 6's stop line as a test (past it the layout is reworked before anything else), so a change
        # adding 0.1 % or more to the Analog FM-off path (worst 1.0184 at W0 and W1) turns this red, not only one past 2 %
        self.assertLessEqual(a[3] - copy, b[3] * P3_STOP, f"{name}: instructions {a[3]} - {copy} vs BE4.40's {b[3]}: "
                             f"{(a[3] - copy) / b[3]:.4f}, past plan 6's stop line {P3_STOP}")

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
        played from Q2 to BE4.41 (BE4.42 removed it).
        Q5: Sub FM plays too, so AMOUNTS has FM 1>2 alone (Sub FM's dev glide case: SubFm's amounts test). Q6: FM 1>2 plays
        here too: the control left (Fm12's amounts test has the case)."""
        # P3 Q6 (reviewed edit): the amounts control (FM 1>2 333 on this case, played as without it) is gone: FM 1>2 plays
        # in this both-Analog case since Q6; the case with it, dev rig included, is Fm12's amounts test
        runs = [self.voice_case(Rig(dev=True), **self.GLIDE),
                self.voice_case(Rig(img=REF_IMG, dev=True, dsp=REF_DSP), **self.GLIDE)]
        (a, va, sa, ca), (b, vb, sb, cb) = runs
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
        test_glide_dev_is_the_expected_change). Q2 made Bit Depth's part its expected change (Bit Depth 2 left AMOUNTS);
        BE4.42 removed Bit Depth, so its 2 is back in AMOUNTS as a BE4.41 project's stale value: played as BE4.40. Q4: Sub
        FM 700 fills buffer A once per voice-block (still played by nothing): the output and the voice stay BE4.40's, and the
        instruction bound is on everything but the copy (check's fills; the copy's own figures are SubCopy's). Q5 (Sub FM)
        made Sub FM's part its expected change: 700 left AMOUNTS, and the same seven cases with it are
        SubFm.test_the_amounts_cases_are_the_expected_change. Q6 (FM 1>2) made the five both-Analog cases its own
        (Fm12.test_the_amounts_cases_are_the_expected_change); FM 1>2 333 stays here on the two voices where it cannot play.
        BE4.43: the 2 on 0x1A4 is FM Atk 2, stored (control) and stepped on every Analog oscillator, playing nothing here.
        BE4.44 W1 (reviewed change, docs/plan-wt-fm.md 3.2, 3.5): a Wavetable oscillator keeps FM values too, so in "osc2
        wavetable" OSC2 (FM Atk 2, FM 1>2 333) and in "mixed" the Wavetable OSC1 (feeding B for OSC2's FM 1>2 333) are tagged:
        tier 2, stock's render with the saved runs put back. Output still BE4.40's byte for byte; the voice too outside those
        runs (masked; WtFmW1 checks their contents); tier 2's own instructions carved out of the +2 % and bounded here."""
        p3 = self.AMOUNTS
        # sub_fill a call, upper bounds (M, Q4: Rig's zero sub (core wave 0) 606 / 1,118 at n = 128 / 256, stock's root's
        # default sub 1,908 / 3,701; +5 %), keyed (stock's root, dev)
        FILL_MAX = {(False, False): 640, (False, True): 1175, (True, False): 2005, (True, True): 3890}
        # P3 Q6 (reviewed edit, plan 6.7): FM 1>2 plays wherever both oscillators are Analog, so free, sync, the silent sync
        # master, glide and stock's root voice left this list for Fm12.test_the_amounts_cases_are_the_expected_change (the
        # same cases, Rig() and Rig(dev=True)); what stays is FM 1>2 stored where it cannot play (OSC2 Wavetable: nobody to
        # modulate; OSC1 Wavetable: 4.5, the control is hidden), against BE4.40 byte for byte
        # the last: tier 2's own instructions a call, bounded (M at W1: 69 with OSC2's envelope (FM Atk 2), 37 for the
        # Wavetable OSC1 tagged only to feed B; + about 15 %, W1 review; was 120 for both)
        # BE4.44 W2 (reviewed change): OSC2's FM 1>2 333 on "osc2 wavetable" opens the reader (tier 3); with no WAV in this
        # rig the body stops at the table, as stock's render does (silent either way): its own path, M 148 a call; "mixed"
        # (OSC1 feeding B, gate 0) adds the gate's loads to tier 2, M 48
        # BE4.44 W3 (reviewed change, docs/plan-wt-fm.md 8 W3): "osc2 wavetable" left this list: its Wavetable OSC2's FM 1>2 333
        # is a carrier of B now, so the Analog OSC1 feeds it (+13 % on that case: the FM parts loop and an_feed, M), as Q6 moved
        # the both-Analog cases; Fm12.test_the_amounts_cases_are_the_expected_change checks it (B written, nothing reads it: no
        # WAV in that rig) and WtFmW3 the B path into a Wavetable carrier
        # BE4.44 W4 (reviewed change, plan 3.5): a Wavetable OSC1 feeds B, so FM 1>2 333 plays in "mixed" too (Fm12's amounts
        # cases check it); "mixed" stays here with FM Atk 2 alone on its Analog OSC2 (an envelope stored and stepped, no
        # amount: BE4.40's output byte for byte), OSC1 untagged (nothing feeds B, no tier 2 to carve out)
        p3 = (0, p3[1], 0)
        cases = (("mixed", lambda r: self.mixed_case(r, 48, p3=p3), (1,), 24, None),)
        for dev in (False, True):
            for name, run, oscs, fills, wt in cases:
                if dev and name == "glide":
                    continue                                    # Q0: the dev rig's glide is an expected change (above)
                with self.subTest(dev=dev, case=name):
                    def stored(r, run=run, oscs=oscs, name=name):
                        x = run(r)
                        if r.dsp is DSP:
                            for i in oscs:
                                s = r.stored[i]
                                self.assertEqual((s[3], s[5], s[6], s[7]), (p3[1], p3[0], p3[2], FM_DEC_INF),
                                                 f"{name}: control, OSC{i + 1}'s Sub FM, FM 1>2, FM Atk (0x1A4), FM Dec stored")
                        return x
                    # P3 Q5 (reviewed edit): with Sub FM 0 nothing fills A (Q4's 8 / 11 / 24 fills went with Sub FM 700),
                    # so no carve-out: the whole count is under M1's +2 %, the output and voice BE4.40's byte for byte
                    self.check(f"amounts {name} dev={dev}", stored, dev, mask=oscs, fills=fills if p3[0] else None,
                               fill_max=FILL_MAX[(name == "root voice", dev)], env=bool(p3[1]), wtier=wt)

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
    y is written over s before Level runs; i is the oscillator (0 OSC1, 1 OSC2, by r0 = osc + OSC_SMOOTH against r.v, which
    voice_case sets; None where r has no r.v); the list's .osc holds each block's i. (BE4.42: BE4.41's an_crush capture,
    .calls / .dc / .stray, is gone with Bit Depth.)"""
    got, lo, hi = Taps(), S.M7_DSP, S.M7_DSP_LIMIT
    def h(uc, a, size, _):
        if not lo <= (uc.reg_read(UC_ARM_REG_LR) & ~1) < hi:
            return
        p, n = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R3)
        x = np.frombuffer(bytes(uc.mem_read(p, 4 * n)), dtype="<f4").copy()
        osc, v = uc.reg_read(UC_ARM_REG_R0) - OSC_SMOOTH, getattr(r, "v", None)
        i = None if v is None else next((k for k in (0, 1) if osc == r.osc(v, k)), "?")
        got.append(x); got.osc.append(i)
        if inject:
            uc.mem_write(p, np.asarray(inject(x, i), dtype="<f4").tobytes())
    r.u.hook_add(UC_HOOK_CODE, h, begin=S.SMOOTH_APPLY, end=S.SMOOTH_APPLY)
    return got


class Taps(list):
    """level_taps' blocks, with each block's oscillator (.osc)."""
    def __init__(self):
        super().__init__(); self.osc = []


def dc_of(k):
    """The pulse's DC term at kernel coordinate k, float64, from the kernel's stated law (analog_kernel.cpp header, plan
    4.7): width = 0.5 - 0.84 (k - 0.5) above 0.5 (k clamped to 1), dc = 2 width - 1; 0 at and below 0.5 (the saw, the
    square, LOW). The independent check of kernel_dc32 (within float32 rounding)."""
    k = min(float(k), 1.0)
    return 0.0 if not k > 0.5 else 2 * (0.5 - 0.84 * (k - 0.5)) - 1


def kernel_dc32(k):
    """The kernel's own float32 dc at k (set_shape: width = .5f - (k - .5f) .84f, dc = 2 width - 1, k clamped to 1; 0 at
    and below 0.5 and for NaN), in the arithmetic clang emits for it: k - 0.5 exact (Sterbenz), the width one VFMA
    (.5 + x (-.84f), one rounding: S, BE4.41's an_dc disassembly, the same expression), dc = 2 w - 1 (2 w exact). BE4.41
    called the kernel's an_dc for this; BE4.42 removed it with Bit Depth, so this statement is pinned to an_render's own
    plateaus instead (BitDepthGone.test_the_width_model_is_the_kernels)."""
    k = float(np.float32(k))
    if not k > 0.5:
        return 0.0
    x = float(np.float32(min(k, 1.0) - 0.5))
    w = float(G._fma32(0.5, x, float(np.float32(-0.84))))
    return float(np.float32(2.0 * w - 1.0))


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class BitDepthGone(unittest.TestCase):
    """BE4.42 (docs/plan-p3-fm-bits.md, BE4.42; Jeff's device feedback 2026-11-16: "not enough difference in practice"): Bit
    Depth is removed. BE4.41's P3 Q2 / Q2b checks (an_crush against p3_golden's grid, the crush at Level's input, the
    pulse's dc, the ramp's parts) are retired with it. What is still true and checked here: a BE4.41 project's Bit Depth
    (key 0x1A4, pushed as BE4.41's template did) plays nothing and is stored nowhere; the FM stats line is cleaned with the
    stats and its word +4 (BE4.41's bits=) stays 0; the DSP links no libm (now: an_index's VSQRT is inline); the pulse
    width the FM golden takes from the kernel. BE4.43: key 0x1A4 is FM Atk's (a new token: a BE4.41 project's bento_bits
    does not reach it, test_native_props, so such a project pushes FM Atk 0 / Dec Inf): the first two tests are restated
    for the envelope's defaults and for 0x1A4 as FM Atk alone."""
    push = staticmethod(P3Identity.push)
    voice_case, root_case = P3Identity.voice_case, P3Identity.root_case

    def test_the_envelope_at_its_defaults_plays_nothing(self):
        """BE4.43 (BE4.42's test_a_be441_bit_depth_plays_nothing restated: 0x1A4 is FM Atk's, and what a BE4.41 or BE4.42
        project now pushes is the envelope's defaults, test_native_props): FM Atk 0 / FM Dec 128 pushed, and out-of-range
        values the M7 clamps to them (Atk -1 / -7, Dec 129 / 5000), with Sub FM 700 on OSC1 and FM 1>2 333 on OSC2 (both FM
        paths run), against the same case with neither key pushed (the fresh state's Atk 0 / Dec Inf: BE4.42's push): output,
        the whole voice, the stats words and the instruction count byte for byte, free and synced in Rig(dev=True);
        fm_env_step never runs (env 1 costs one test on the FM-off path: the envelope bytes stay 0) and FM played (control);
        SaFmStats word +4 (BE4.41's bits=) 0."""
        f = dsp_sym("fm_env_step")
        def run(r, env, sync, shape):
            n = [0]
            r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: n.__setitem__(0, n[0] + 1), begin=f, end=f)
            p3 = ((700, 333) if env is None else (700, env[0], 333, env[1]))
            x = self.voice_case(r, shape=shape, sync=sync, p3=p3, blocks=4)
            return x + (n[0],)
        for name, sync, shape in (("free", 0, (48, 110)), ("sync", 1, (70, 100))):
            base = run(Rig(dev=True), None, sync, shape)
            self.assertEqual(base[4], 0, "control: no envelope pushed, none stepped")
            for env in ((0, FM_DEC_INF), (-1, FM_DEC_INF), (0, 129), (-7, 5000)):
                with self.subTest(case=name, env=env):
                    r = Rig(dev=True); got = run(r, env, sync, shape)
                    same(self, got[0], base[0], f"{name}: output")
                    self.assertEqual(got[1], base[1], f"{name}: the voice")
                    self.assertEqual((got[2], got[3]), (base[2], base[3]), f"{name}: stats words, instructions")
                    self.assertEqual(got[4], 0, f"{name}: fm_env_step not entered")
                    self.assertEqual([(s[3], s[7]) for s in r.stored], [(0, FM_DEC_INF)] * 2, f"{name}: clamped to the defaults")
                    self.assertEqual(r.r32(SA + SA_FMSTATS + 4), 0, f"{name}: word +4 (BE4.41's bits=) 0")
                    self.assertGreater(r.r32(SA + SA_FMSTATS), 0, f"{name}: control, FM played")

    def test_a_late_0x1a4_is_fm_atk_alone(self):
        """Review (BE4.42's mutant: 0x1A4 falling to an_prop's last branch, fm12), restated for BE4.43: 0x1A4 is FM Atk's,
        so 0x1A4 pushed after the P3 settings (Sub FM 700, FM 1>2 333, FM Dec 40), at 1, 2, 3, 7, 0 and 1000 on both
        oscillators, changes FM Atk alone (byte +3, clamped to 127), every other stored setting as it was (fm12 333 and
        FM Dec 40 included), and counts one property of ours each."""
        r = Rig(dev=True)
        root, v = r.voice(0x5A)
        for i in (0, 1):
            self.push(r, r.osc(v, i), 0x5A, i, (1, 48, 0, 0), (700, 0, 333, 40))
        before = [r.settings(v, i) for i in (0, 1)]
        props = r.r32(SA + SA_STATS + 16)
        for val in (1, 2, 3, 7, 0, 1000):
            for i in (0, 1):
                r.prop(r.osc(v, i), K_FMATK, val, 0x5A, i)
            want = [st[:3] + (min(val, 127),) + st[4:] for st in before]
            self.assertEqual([r.settings(v, i) for i in (0, 1)], want, f"0x1A4 = {val}: FM Atk only")
        self.assertTrue(all(333 in st and 700 in st and st[7] == 40 for st in before), f"control: stored {before}")
        self.assertEqual(r.r32(SA + SA_STATS + 16) - props, 12, "props: each 0x1A4 counted as ours (FM Atk)")

    def test_the_fm_stats_line_is_cleaned_with_the_stats(self):
        """Q2 review, kept: finish() cleans SA_FMSTATS's line (DCCMVAC) wherever it cleans SA_STATS, after the block's counts,
        so the M4 reads fresh fm= / fmx= on the device (the emulator has no cache: the clean itself is what is checked). Both
        oscillators with Sub FM 700 (and BE4.41's Bit Depth 2 pushed, ignored), 8 blocks: each clean sees fm= = 2 per
        finished block and word +4 0. BE4.43: 0x1A4 is FM Atk's, so it is pushed at 0 here; OSC2's Sub FM plays nothing next
        to the Analog OSC1 (one amount per oscillator), so fm= is 1 per finished block."""
        r = Rig()
        cleans = []
        def h(uc, access, a, size, value, _):
            line = value & 0xFFFFFFFF
            if line in (SA + SA_STATS, SA + SA_FMSTATS):
                cleans.append((line, struct.unpack("<II", uc.mem_read(SA + SA_FMSTATS, 8))))
        r.u.hook_add(UC_HOOK_MEM_WRITE, h, begin=0xE000EF68, end=0xE000EF6B)   # SA_DCCMVAC (synth_shared.h)
        self.voice_case(r, shape=(48, 88), p3=(700, 0, 0), blocks=8)
        st = [k for line, k in cleans if line == SA + SA_STATS]
        fm = [k for line, k in cleans if line == SA + SA_FMSTATS]
        self.assertEqual(len(st), 7, "the stats line: once per finished block (8 blocks, the last one finishes later)")
        self.assertEqual(fm, [(j + 1, 0) for j in range(7)], "the FM stats line, after each block's fm= counts (BE4.43: OSC1's)")

    def test_the_dsp_links_no_libm(self):
        """Q2 review (plan 4.7), kept: zig's compiler_rt supplies libm-named routines (fmaxf, floorf, sqrtf, sinf, ..) as
        software calls that link without an undefined symbol, so build.py's DSP_REFUSED_LIBM is load-bearing. It refuses the
        family, check_dsp_elf refuses this build's own ELF with one symbol renamed to fmaxf, the shipped ELF has none, and no
        Bit Depth code is left (an_crush, an_dc, part_post). BE4.42: an_index's square root (FD3's v^1.5) is VSQRT inline in
        fm_setup, no call; an_feed is a plain multiply loop, no call, no VDIV."""
        for n in ("rintf", "fminf", "fmaxf", "floorf", "sqrtf", "roundf", "sinf", "truncf", "expf", "floor", "ceilf",
                  "fabsf", "cosf", "tanhf", "powf", "logf", "log2f", "exp2f", "fmodf", "lroundf", "nearbyintf", "sinhf"):
            self.assertTrue(build.DSP_REFUSED_LIBM.match(n), f"{n}: refused")
        e = (BUILD / "dsp.elf").read_bytes()
        _, syms = build.elf_sections(e)
        names = {n for n, *_ in syms if n}
        self.assertIn("an_feed", names)
        self.assertEqual([n for n in names if n in ("an_crush", "an_dc") or "part_post" in n], [], "no Bit Depth code")
        self.assertEqual(sorted(n for n in names if build.DSP_REFUSED_LIBM.match(n)), [], "the shipped ELF: no libm name")
        origin, limit = S.M7_DSP + build.DSP_TRAMP_BYTES, S.M7_DSP_LIMIT
        build.check_dsp_elf(e, origin, limit)                    # control: the shipped ELF passes
        shoff, (shentsize, shnum) = struct.unpack_from("<I", e, 32)[0], struct.unpack_from("<HH", e, 46)
        secs = [struct.unpack_from("<10I", e, shoff + i * shentsize) for i in range(shnum)]
        st = secs[next(s for s in secs if s[1] == 2)[6]]           # the symbol table's string table (not .debug_str)
        at = e.find(b"\0an_feed\0", st[4], st[4] + st[5])
        self.assertGreaterEqual(at, 0, "an_feed in .strtab")
        bad = e[:at] + b"\0fmaxf\0\0\0" + e[at + 9:]
        self.assertIn("fmaxf", {n for n, *_ in build.elf_sections(bad)[1]})
        with self.assertRaises(SystemExit) as cm:
            build.check_dsp_elf(bad, origin, limit)
        self.assertIn("fmaxf", str(cm.exception))
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        def ins(name):
            lo, hi = sym_range(name)
            return [i.mnemonic for i in md.disasm(IMG[lo - BASE:hi - BASE], lo)]
        setup = ins(next(k for k in DSP if "fm_setup" in k))
        self.assertIn("vsqrt.f32", setup, "fm_setup: an_index's VSQRT inline")
        feed = ins("an_feed")
        self.assertIn("vmul.f32", feed, "an_feed: b = s inc")
        self.assertEqual([m for m in feed if m in ("bl", "blx") or m.startswith(("vdiv", "vsqrt"))], [], "an_feed: no call, no VDIV")

    def test_the_width_model_is_the_kernels(self):
        """(BE4.41's test_an_dc_is_the_kernels_dc, restated without an_dc.) kernel_dc32(k) is the DC term the kernel takes
        off its waveform, bit for bit: a free an_render at k (inc 0.001, so whole plateaus) plays exactly f32(1 - dc) on its
        high and f32(-1 - dc) on its low (wave<PULSE> / wave<MIXED> compute (+-1) - dc), for every whole Shape 89..127 and
        1/512-held values next to 88; dc within 1e-6 of dc_of's stated law; 0 at and below k 0.5. Past k 1 (set_shape
        clamps): kernel_dc32(1)'s plateaus. Whether the unfused form (no VFMA) would be seen on this sweep is printed, not
        asserted (it may round alike at every Shape here)."""
        r = Rig()
        st, out = r.alloc(256), r.alloc(4 * 2048)

        def plateaus(k):
            r.u.mem_write(st, struct.pack("<I", 0x4f4c4e41) + bytes(252))
            r.call(DSP["an_render"], st, out, 2048, 0, sargs=(0,), fargs=(0.001, k))
            y = np.frombuffer(bytes(r.u.mem_read(out, 4 * 2048)), dtype="<f4")[1:]
            vals, cnt = np.unique(y, return_counts=True)
            return [float(v) for v in sorted(vals[np.argsort(cnt)[-2:]])]
        want = lambda dc: [float(np.float32(-1.0) - np.float32(dc)), float(np.float32(1.0) - np.float32(dc))]
        unfused = lambda k: float(np.float32(2.0 * float(np.float32(0.5 - float(np.float32((min(k, 1.0) - 0.5) * float(np.float32(0.84)))))) - 1.0))
        seen_diff = False
        for sh in [88 + 1 / 512, 88 + 2 / 512] + list(range(89, 128)):
            k = f32(k_of(sh)); dc = kernel_dc32(k)
            with self.subTest(shape=sh):
                self.assertAlmostEqual(dc, dc_of(k), delta=1e-6)
                self.assertLess(dc, 0.0)
                got = plateaus(k)
                self.assertEqual(got, want(dc))
                if unfused(k) != dc and want(unfused(k)) != got:
                    seen_diff = True
        print(f"\n  width model: the unfused form's plateaus differ from the kernel's at some Shape: {seen_diff}", file=sys.stderr)
        for k in (f32(1.0 + 2 ** -23), 1.25, 2.0, float("inf")):          # (k past 1: the clamp)
            with self.subTest(k=k):
                self.assertEqual(kernel_dc32(k), kernel_dc32(1.0))
                self.assertEqual(plateaus(k), want(kernel_dc32(1.0)))
        for k in (0.0, 0.25, f32(k_of(87.6)), f32(k_of(88 - 1 / 512)), 0.5, -0.5, -1.0, float("nan")):
            self.assertEqual(kernel_dc32(k), 0.0, k)


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


SA_FMBUF_A = 0x800          # buffer A, AN_FM_FRAMES floats (P3 3.3; Q4 fills it)
SA_FMBUF_B = 0xC00          # buffer B, AN_FM_FRAMES floats (P3 3.3; Q6: OSC1 feeds it, an_feed)
AN_FM_FRAMES = 256
RUN_INC_SUB = 40            # Run.inc_sub (float bits; P3 3.3: the run state's word 10)
FM_SKIPPED = 8              # SaFmStats.fm_skipped (byte offset)
SUB_SMOOTH = 0xCC           # the sub's level smoother: SMOOTH_APPLY's r0 in its render (stock's sub render, read by hand)
# the sub oscillator's keys (its class slot 8, read by hand): 3 fine (1/1000 semitone, +0x0C), 0xC3 wave (stores value + 1,
# the core's wave byte: 1 Rev Saw, 2 Triangle, 3 Square, 4 Sine, 5 Noise), 0xC4 level, 0xC5 pulse width (1/1000), 0xC6 retrigger
SUB_FINE, SUB_WAVE, SUB_LEVEL, SUB_PW, SUB_RETRIG = 3, 0xC3, 0xC4, 0xC5, 0xC6
SUB_WAVES = {1: "rev saw", 2: "triangle", 3: "square", 4: "sine", 5: "noise"}
# sub_fill's instructions per voice-block, upper bounds (M, Q4 at 2N with its stock helpers: rev saw 3,724, triangle 2,952,
# square 3,994, sine 5,256, noise 2,953; +5 %)
SUB_COST_MAX = {1: 3910, 2: 3100, 3: 4190, 4: 5520, 5: 3100}


class Span:
    """P3 Q4: the instructions spent inside one function, its callees included (from its entry to its return address), and
    its calls; a second hook on every instruction, so only on the rigs that measure."""
    def __init__(self, r, fn):
        self.r, self.entry, self.stack, self.total, self.calls = r, fn & ~1, [], 0, 0
        r.u.hook_add(UC_HOOK_CODE, self.step)

    def step(self, u, a, size, _):
        if a == self.entry:
            self.stack.append((self.r.n, u.reg_read(UC_ARM_REG_LR) & ~1))
        elif self.stack and a == self.stack[-1][1]:
            n0, _ = self.stack.pop()
            self.total += self.r.n - n0; self.calls += 1


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class SubCopy(unittest.TestCase):
    """P3 Q4 (docs/plan-p3-fm-bits.md 4.2, 6.4, OD10): buffer A, the sub oscillator's block in increment units, filled once
    per voice-block by the first Analog oscillator with Sub FM, from the sub as it is before its own render. On the device's
    path (stock's root ctor and engine init: the sub's +0xDC = 2, 2N = 256 frames; stock's voice render through the root's
    vtable word: OSC1, OSC2, then stock's sub render), A at the sub render's entry is checked against the sub's raw core
    output of that same block (taken at its Level smoother's input, SMOOTH_APPLY (sub + 0xCC, ..)): A = (raw - dc) inc 2^-32
    in float32, bit for bit (worst 0), dc the Square's 1 - 2 pw (OD10), 0 for the others, inc the increment stock's render
    set. And the copy changes nothing else: the same case with Sub FM 0 plays the same output and reaches the sub's render
    with the same voice bytes (P3's settings bytes masked), PARAM_VALUE's two cache words included. Q4 plays nothing from A."""
    BLOCKS = 12
    NOTES = {0: 48, 4: 61, 8: 36, 10: 36}                       # block 10: the same note again (a retrigger)

    def voice(self, r, wave, level, fm=(700, 0), types=(1, 1), glide=300, pw=300, retrig=1, fine=250, handle=0x4545,
              index=0, root=None, sync=0):
        """Stock's root (ctor, engine init) and its voice `index`; Type / Glide / Sub FM / OSC2's Sync through stock's root
        property, the sub's own keys through its slot 8; a source routed to the sub's pitch (+0x84: its slot 10, key 3)."""
        if root is None:
            pool_alloc(r)
            root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
            r.w32(root + S.ROOT_HANDLE, handle)
            for i in range(10):
                vo = r.r32(root + 0x14 + 4 * i)
                for o in (S.VOICE_WT1, S.VOICE_WT2):
                    r.w32(vo + o + 0x18, r.nowav)
            for i in (0, 1):
                r.root_prop(root, K_TYPE, types[i], i)
            r.root_prop(root, K_GLIDE, glide, 0)
            if sync:
                r.root_prop(root, K_SYNC, sync, 1)
            for i in (0, 1):
                r.root_prop(root, K_SUBFM, fm[i], i)
        v = r.r32(root + 0x14 + 4 * index)
        sub = v + S.VOICE_SUB
        for k, x in ((SUB_WAVE, wave - 1), (SUB_LEVEL, level), (SUB_PW, pw), (SUB_RETRIG, retrig), (SUB_FINE, fine)):
            r.prop(sub, k, x)
        src = route(r, sub, 0, 1.0, key=SUB_FINE)
        return root, v, sub, src

    def note(self, r, v, sub, n):
        """Stock's voice note start calls slot 2 of WT1, WT2 and the sub."""
        r.note(v, n)
        nw = r.alloc(4); r.w32(nw, n)
        r.call(r.r32(r.r32(sub) + 8) & ~1, sub, 0, nw)

    def taps(self, r, subs):
        """Per sub (by address): at its render's entry the voice's bytes, buffer A and the run state; at its smoother the raw
        core block. And sub_fill's calls: (the oscillator in the last an_osc entered, the sub it was given)."""
        got = {s: dict(at=[], raw=[]) for s in subs}
        last, fills = [None], []
        def entry(uc, a, size, _):
            s = uc.reg_read(UC_ARM_REG_R0)
            if s in got:
                got[s]["at"].append((bytes(uc.mem_read(s - S.VOICE_SUB, 0xB88)),
                                     np.frombuffer(bytes(uc.mem_read(SA + SA_FMBUF_A, 4 * AN_FM_FRAMES)), dtype="<f4").copy(),
                                     bytes(uc.mem_read(SA + SA_RUN, 64))))
        def smooth(uc, a, size, _):
            s = uc.reg_read(UC_ARM_REG_R0) - SUB_SMOOTH
            if s in got:
                p, n = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R3)
                got[s]["raw"].append(np.frombuffer(bytes(uc.mem_read(p, 4 * n)), dtype="<f4").copy())
        def osc(uc, a, size, _): last[0] = uc.reg_read(UC_ARM_REG_R0)
        def fill(uc, a, size, _): fills.append((last[0], uc.reg_read(UC_ARM_REG_R0)))
        render = r.r32(r.r32(subs[0])) & ~1                     # the sub class's slot 0 (stock's sub render)
        r.u.hook_add(UC_HOOK_CODE, entry, begin=render, end=render)
        r.u.hook_add(UC_HOOK_CODE, smooth, begin=S.SMOOTH_APPLY, end=S.SMOOTH_APPLY)
        r.u.hook_add(UC_HOOK_CODE, osc, begin=DSP["an_osc"] & ~1, end=DSP["an_osc"] & ~1)
        f = dsp_sym("sub_fill")
        r.u.hook_add(UC_HOOK_CODE, fill, begin=f, end=f)
        return got, fills

    def play(self, wave, level, blocks=BLOCKS, levels=None, msub=None, notes=None, each=None, capture=False, **kw):
        """One case on this build: (per-block records, the voice output, sub_fill's calls, instructions per block, Span,
        (rig, voice, sub)). A holds a non-zero pattern before each block (a copy that writes nothing is seen); rec["arg"]
        is the increment sub_inc gave an_sub_copy (its r3) this block, or None. notes: the blocks' note starts (NOTES);
        each(r, sub, b): before block b's render; capture: the an_render_fm calls in rig.fm (fm_capture, P3 Q5)."""
        r = Rig()
        notes = self.NOTES if notes is None else notes
        root, v, sub, src = self.voice(r, wave, level, **kw)
        self.assertEqual(r.r32(sub + 0xDC), 2, "the device's sub: +0xDC = 2 after stock's engine init")
        got, fills = self.taps(r, [sub])
        r.fm = fm_capture(r) if capture else None
        span = Span(r, dsp_sym("sub_fill"))
        lo = dsp_sym("an_sub_copy")
        args = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: args.append(uc.reg_read(UC_ARM_REG_R3)), begin=lo, end=lo)
        hi = min([a & ~1 for a in DSP.values() if (a & ~1) > lo] + [S.M7_DSP_LIMIT])
        divs = [0]
        def vdiv(uc, a, sz, _):
            if sz == 4:
                w0, w1 = struct.unpack("<HH", uc.mem_read(a, 4))
                if w0 & 0xFFB0 == 0xEE80 and w1 & 0x0F50 == 0x0A00:   # VDIV.F32
                    divs[0] += 1
        r.u.hook_add(UC_HOOK_CODE, vdiv, begin=lo, end=hi - 1)
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
        slot18 = r.r32(r.r32(root) + 0x48)
        recs, outs, cyc = [], b"", []
        for b in range(blocks):
            if b in notes:
                self.note(r, v, sub, notes[b])
            if b == 6:
                r.prop(sub, SUB_PW, 650)                         # the Square's width moves: stock latches it at the next wrap
            for i, l in enumerate(levels(b) if levels else (1.0, 1.0)):
                r.level(v, i, l)
            if msub:
                r.u.mem_write(r.osc(v, 0) + S.OSC_KERNEL + MSUB_AT, struct.pack("<H", msub))
            r.wf(src + 4, 0.03 * math.sin(1.3 * b))              # pitch modulation on the sub's +0x84 (octaves)
            if each:
                each(r, sub, b)
            r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
            r.u.mem_write(SA + SA_FMBUF_A, b"\x5a" * (4 * AN_FM_FRAMES))
            t0, d0, k0 = span.total, divs[0], len(args)
            _, c = r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
            outs += bytes(r.u.mem_read(od, 4 * DEV_N)); cyc.append(c)
            recs.append(dict(fill=span.total - t0, divs=divs[0] - d0, arg=args[-1] if len(args) > k0 else None,
                             inc=r.r32(sub + 0x20 + 0xC), pw=r.rf(sub + 0xE4 + 0x3C), pitch=r.rf(sub + 0x84 + 0x3C),
                             wave=r.u.mem_read(sub + 0x20, 1)[0], detune=r.rf(sub + 4)))
            r.block()
        self.assertEqual(len(got[sub]["at"]), blocks); self.assertEqual(len(got[sub]["raw"]), blocks)
        for k, rec in enumerate(recs):
            rec["voice"], rec["a"], rec["run"] = got[sub]["at"][k]
            rec["raw"] = got[sub]["raw"][k]
            rec["phase"] = struct.unpack_from("<I", rec["voice"], S.VOICE_SUB + 0x28)[0]    # the core's, at the block's start
        return recs, outs, fills, cyc, span, (r, v, sub)

    @staticmethod
    def expect(rec):
        """A from the raw block in float32, as the DSP is to compute it, with the dc and inc x 2^-32 it uses."""
        incf = np.float32(rec["inc"] * 2.0 ** -32)
        dc = np.float32(0.0)
        if rec["wave"] == 3:
            x = float(np.float32(rec["pw"])) * 2.0 ** 32
            pend = 0 if not x > 0 else 0xFFFFFFFF if x >= 2.0 ** 32 else int(x)   # vcvt.u32: truncation, saturating
            pend = min(max(pend, 1), 0xFFFFFFFE)
            dc = np.float32(1.0 - float(np.float32(pend)) * 2.0 ** -31)
        return (rec["raw"] - dc) * incf, dc, incf

    def check_block(self, name, rec):
        """A = (raw - dc) inc 2^-32 bit for bit, Run.inc_sub and the tag; returns max |A / inc + dc - raw| and dc."""
        want, dc, incf = self.expect(rec)
        self.assertEqual(len(rec["raw"]), DEV_FRAMES, f"{name}: 2N frames")
        got = rec["a"][:DEV_FRAMES]
        bad = np.nonzero(got.view("<u4") != want.view("<u4"))[0]
        self.assertEqual(len(bad), 0, f"{name}: A = (raw - dc) inc 2^-32 bit for bit; {len(bad)} differ, first at "
                         f"{bad[:1]}: {got[bad[:1]]} / {want[bad[:1]]}")
        run = struct.unpack("<16I", rec["run"])
        self.assertEqual(run[RUN_INC_SUB // 4], int(incf.view("<u4")), f"{name}: Run.inc_sub")
        self.assertEqual(run[RUN_FMTAG // 4] & 1, 1, f"{name}: Run.fmtag says A")
        return float(np.max(np.abs(got.astype(np.float64) / float(incf) + float(dc) - rec["raw"]))), float(dc)

    def test_the_copy_is_stocks_sub_up_to_its_dc(self):
        """6.4: all 5 waves x the sub's Level 0 and 1000 (stock never skips the sub's core at Level 0), over 12 device blocks:
        note changes (48, 61, 36) and a retrigger (the same note again, retrigger on), OSC1's glide (Glide 300: an_enter
        writes the sub's +0x04 before the voice renders), fine 250, pitch modulation on the sub's +0x84, the Square at PW
        30 % and from block 6 65 % (the width latches at the next wrap, mid-block: the case two 128-frame copies got wrong).
        Each block's A as check_block; Sub FM on OSC1. The copy's cost (sub_fill with its stock helpers) is recorded."""
        costs = {}
        for wave in SUB_WAVES:
            for level in (0, 1000):
                with self.subTest(wave=SUB_WAVES[wave], level=level):
                    recs, _, fills, _, span, (r, v, sub) = self.play(wave, level)
                    name = f"{SUB_WAVES[wave]} level {level}"
                    worst, dcs = 0.0, set()
                    for b, rec in enumerate(recs):
                        self.assertEqual(rec["wave"], wave, "control: the sub's wave byte")
                        w, dc = self.check_block(f"{name} block {b}", rec)
                        worst = max(worst, w); dcs.add(dc)
                    self.assertEqual(fills, [(r.osc(v, 0), sub)] * self.BLOCKS, "one fill per voice-block, by OSC1")
                    # controls: the case moves what it claims to
                    self.assertEqual([rec["phase"] for b, rec in enumerate(recs) if b in self.NOTES], [0] * len(self.NOTES),
                                     "control: each note start (retrigger on) restarts the sub's phase")
                    self.assertGreater(len({rec["inc"] for rec in recs}), 6, "control: the increment moves (notes, glide, mod)")
                    self.assertTrue(any(rec["detune"] != 0.0 for rec in recs), "control: the glide reaches the sub's +0x04")
                    self.assertGreater(len({rec["pitch"] for rec in recs}), 6, "control: the pitch modulation reaches +0x84")
                    if wave == 3:
                        self.assertEqual(len(dcs), 2, "the Square's dc follows its width (30 %, then 65 %)")
                        self.assertAlmostEqual(min(dcs), 1 - 2 * 0.65, delta=1e-6)
                        self.assertAlmostEqual(max(dcs), 1 - 2 * 0.30, delta=1e-6)
                    else:
                        self.assertEqual(dcs, {0.0}, "dc 0 but for the Square")
                    if level == 1000:
                        self.assertGreater(float(np.max(np.abs(np.concatenate([rec["raw"] for rec in recs])))), 0.5,
                                           "control: it plays")
                    self.assertLess(worst, 2 ** -22, f"{name}: A / inc + dc against raw (two float32 roundings)")
                    costs[(wave, level)] = span.total / span.calls
        print("\n  sub_fill per voice-block (instructions at 2N = 256, its stock helpers included): " +
              ", ".join(f"{SUB_WAVES[w]} {costs[(w, 1000)]:.0f}" for w in SUB_WAVES if (w, 1000) in costs), file=sys.stderr)
        for (w, l), c in costs.items():
            self.assertLess(c, SUB_COST_MAX[w], f"{SUB_WAVES[w]} level {l}: the copy's cost")

    def test_the_squares_width_at_its_ends(self):
        """4.2: the Square's width as stock's 0x08056440 holds it, 1..0xFFFFFFFE: PW 0 (0 x 2^32 -> 1) and PW 100 % (2^32,
        vcvt.u32 saturates -> 0xFFFFFFFE), then 65 % from block 6 (play); A as check_block, its dc +-1 at the end."""
        for pw in (0, 1000):
            with self.subTest(pw=pw):
                recs = self.play(3, 1000, blocks=8, pw=pw)[0]
                dcs = {self.check_block(f"PW {pw} block {b}", rec)[1] for b, rec in enumerate(recs)}
                self.assertIn(1.0 if pw == 0 else -1.0, {round(d, 6) for d in dcs}, "dc at the end")

    def test_the_copy_divides_at_the_subs_edges_only(self):
        """6.5: VDIV.F32 inside an_sub_copy, counted by PC, per block = the sub's edges from its phase law (p0 + i inc mod
        2^32, p0 the core's phase at the block's start): the Rev Saw one per wrap sample (p < inc), the Square one per wrap
        sample and one per rising-ramp sample (w <= p < w + inc, the width latched at the first wrap), the others none."""
        for wave in SUB_WAVES:
            with self.subTest(wave=SUB_WAVES[wave]):
                recs = self.play(wave, 1000, blocks=8)[0]
                for b, rec in enumerate(recs):
                    p0, inc = rec["phase"], rec["inc"]
                    w = struct.unpack_from("<I", rec["voice"], S.VOICE_SUB + 0x20 + 0x18)[0]
                    pend = None
                    if wave == 3:
                        x = float(np.float32(rec["pw"])) * 2.0 ** 32
                        pend = min(max(0 if not x > 0 else 0xFFFFFFFF if x >= 2.0 ** 32 else int(x), 1), 0xFFFFFFFE)
                    want = 0
                    for i in range(DEV_FRAMES):
                        p = (p0 + i * inc) & 0xFFFFFFFF
                        if wave in (1, 3) and p < inc:
                            want += 1
                            if pend:
                                w, pend = pend, 0
                        elif wave == 3 and w <= p < ((w + inc) & 0xFFFFFFFF):
                            want += 1
                    self.assertEqual(rec["divs"], want, f"block {b}: VDIVs")
                if wave in (1, 3):
                    self.assertGreater(sum(rec["divs"] for rec in recs), 1, "control: it divides at all (notes 48 / 61: a wrap every few blocks)")

    def test_the_copy_changes_nothing_else(self):
        """6.4: with Sub FM 700 on OSC1 (the copy runs) and with Sub FM 0 (it does not), the sub object reaches its render
        with the same bytes, PARAM_VALUE's cache words (+0xC0, +0x120) included, which move every block here (pitch
        modulation, a width change). P3 Q5 (reviewed edit): Sub FM 700 now plays, so the voice's output and the rest of the
        voice (OSC1's state, the filters' histories) differ, and the comparison is the sub's own bytes (voice +0x4C up to the
        voice's oscillator pointers at +0x178); Q4 compared the whole voice and the output byte for byte, and the
        instructions (the gate and the call), which now include the FM render (SubFm.test_cost)."""
        for wave in SUB_WAVES:
            with self.subTest(wave=SUB_WAVES[wave]):
                a = self.play(wave, 1000)
                b = self.play(wave, 1000, fm=(0, 0))
                self.assertNotEqual(a[1], b[1], "control: Sub FM 700 plays (Q5)")
                self.assertEqual(len(a[2]), self.BLOCKS); self.assertEqual(len(b[2]), 0, "control: Sub FM 0 does not fill")
                for k, (x, y) in enumerate(zip(a[0], b[0])):
                    same(self, x["voice"][S.VOICE_SUB:0x178], y["voice"][S.VOICE_SUB:0x178], f"block {k}: the sub at its render")
                cache = lambda x, at: x["voice"][S.VOICE_SUB + at:S.VOICE_SUB + at + 4]
                self.assertGreater(len({cache(x, 0xC0) for x in a[0]}), 6, "control: the pitch cache moves")
                if wave == 3:
                    self.assertEqual(len({cache(x, 0x120) for x in a[0][1:]}), 2, "control: the width cache moves (30 %, 65 %)")

    def test_which_oscillator_fills(self):
        """4.1: A is filled once per voice-block, by OSC1 when it has Sub FM, else by OSC2: OSC2 alone, both (OSC2 uses OSC1's
        A: one fill), OSC1 Wavetable (a mixed voice), OSC1 silent (its skip path: OSC2 fills), and msub (a ramp still to run,
        Q5) with Sub FM 0. A silent sync master (OSC1 Level 0, OSC2 Sync on: E3's branch, not the skip path) fills itself,
        Sub FM on OSC1 alone or on both (E3 (b), Q5). A is the sub's (check_block) in each.
        BE4.43 (expected change, Jeff's design: one FM amount per oscillator): OSC2's Sub FM plays only while OSC1 is not
        Analog (then OSC2's is FM 1>2), so next to an Analog OSC1 it never fills A: "OSC2 only" and "OSC1 silent" (OSC1's skip
        path) fill nothing (A keeps the pattern, fm= 0); the mixed voice (OSC1 Wavetable) is where OSC2 fills.
        BE4.44 W4 (reviewed change, D2's last case): OSC2's amount is FM 1>2 next to either OSC1, so in the mixed voice (OSC1
        Wavetable with no WAV: its own Sub FM plays nothing) nothing fills A either."""
        quiet = lambda b: (0.0, 1.0)
        cases = (("OSC2 only", dict(fm=(0, 700)), None), ("both", dict(fm=(700, 300)), 0),
                 ("OSC1 Wavetable", dict(fm=(700, 700), types=(0, 1)), None),
                 ("OSC1 silent", dict(fm=(700, 700), levels=quiet), None),
                 ("msub", dict(fm=(0, 0), msub=40), 0),
                 ("silent sync master", dict(fm=(700, 0), sync=1, levels=quiet), 0),
                 ("silent sync master, both", dict(fm=(700, 700), sync=1, levels=quiet), 0))
        for name, kw, who in cases:
            with self.subTest(case=name):
                recs, _, fills, _, _, (r, v, sub) = self.play(3, 1000, blocks=6, **kw)
                if who is None:                                  # BE4.43: OSC2's Sub FM next to an Analog OSC1: none
                    self.assertEqual(fills, [], f"{name}: no fill")
                    self.assertTrue(all(rec["a"][:DEV_FRAMES].tobytes() == b"\x5a" * (4 * DEV_FRAMES) for rec in recs),
                                    f"{name}: A untouched")
                    self.assertEqual(r.r32(SA + SA_FMSTATS + FM_FM), 0, f"{name}: fm= 0")
                    self.assertEqual(r.settings(v, 1)[5], 700, f"{name}: control, OSC2's Sub FM stored")
                    continue
                for b, rec in enumerate(recs):
                    self.check_block(f"{name} block {b}", rec)
                self.assertEqual(fills, [(r.osc(v, who), sub)] * 6, f"{name}: one fill a block, by OSC{who + 1}")
                if kw.get("sync"):
                    self.assertEqual(r.settings(v, 1)[2], 1, "control: OSC2's Sync is on (OSC1 the master)")

    def test_each_voice_fills_its_own(self):
        """3.3: two voices of one root rendered in the same audio block (an_enter clears the tag for each): each voice's A, at
        its own sub's render, is its own sub's block (different waves and notes), filled once per voice-block."""
        r = Rig()
        root, v0, s0, _ = self.voice(r, 4, 1000)
        _, v1, s1, _ = self.voice(r, 1, 1000, root=root, index=1)
        got, fills = self.taps(r, [s0, s1])
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        slot18 = r.r32(r.r32(root) + 0x48)
        self.note(r, v0, s0, 60); self.note(r, v1, s1, 43)
        for b in range(4):
            for v in (v0, v1):
                acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
                r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
            r.block()
        self.assertEqual([s for _, s in fills], [s0, s1] * 4, "one fill per voice-block, each voice its own sub")
        for s, wave in ((s0, 4), (s1, 1)):
            self.assertEqual((len(got[s]["at"]), len(got[s]["raw"])), (4, 4))
            for k, ((voice, a, run), raw) in enumerate(zip(got[s]["at"], got[s]["raw"])):
                incf = np.frombuffer(struct.pack("<I", struct.unpack("<16I", run)[RUN_INC_SUB // 4]), dtype="<f4")[0]
                self.assertEqual(struct.unpack("<16I", run)[2], s - S.VOICE_SUB, "Run.voice")
                want = raw * incf
                self.assertEqual(int(np.sum(a[:DEV_FRAMES].view("<u4") != want.view("<u4"))), 0,
                                 f"{SUB_WAVES[wave]} block {k}: the voice's own sub in A")

    def test_the_increment_is_stocks(self):
        """4.2: sub_inc's u32 (an_sub_copy's r3) = the increment stock's render then writes to the core (+0xC), every block of
        a sweep: a note start each block over 0..127, the sub's fine over both signs and odd values (-999..999: the int
        conversion and the /1000 /12 order), OSC1's glide (+0x04) and the pitch modulation (+0x84) under it; A exact
        (check_block, Rev Saw: it divides by inc)."""
        notes = (0, 127, 13, 108, 24, 95, 37, 84, 48, 71, 59, 60, 1, 126, 30, 90, 45, 66, 52, 77, 7, 115, 61, 36)
        fines = (-333, 777, -999, 999, -1, 1, -500, 123, -777, 333, -250, 641, -641, 250, -12, 12, -901, 459, -459, 901,
                 -88, 88, -666, 666)
        recs = self.play(1, 1000, blocks=len(notes), notes=dict(enumerate(notes)),
                         each=lambda r, sub, b: r.prop(sub, SUB_FINE, fines[b]))[0]
        for b, rec in enumerate(recs):
            self.assertEqual(struct.unpack_from("<i", rec["voice"], S.VOICE_SUB + 0x0C)[0], fines[b], "control: the fine")
            self.assertEqual(rec["arg"], rec["inc"], f"block {b} (note {notes[b]}, fine {fines[b]}): sub_inc vs stock's")
            self.check_block(f"block {b}", rec)
        self.assertGreater(len({rec["inc"] for rec in recs}), 20, "control: the increment moves")

    def test_only_the_named_voice_fills(self):
        """3.3: the gate fills A only for the voice an_enter named (the tag is that voice's): voice 1's OSC1 rendered while
        Run.voice names voice 0 fills nothing (A, fmtag as they were), and voice 0's OSC1 then fills its own sub's A."""
        r = Rig()
        root, v0, s0, _ = self.voice(r, 1, 1000)
        _, v1, s1, _ = self.voice(r, 1, 1000, root=root, index=1)
        _, fills = self.taps(r, [s0, s1])
        self.note(r, v0, s0, 60); self.note(r, v1, s1, 43)
        for v in (v0, v1):
            r.level(v, 0, 1.0)
        r.call(r.dsp["an_enter"], root, v0)
        self.assertEqual(r.r32(SA + SA_RUN + 8), v0, "control: Run.voice names voice 0")
        r.u.mem_write(SA + SA_FMBUF_A, b"\x5a" * (4 * AN_FM_FRAMES))
        entered = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: entered.append(uc.reg_read(UC_ARM_REG_R0)),
                     begin=DSP["an_osc"] & ~1, end=DSP["an_osc"] & ~1)
        def osc1(v):
            out, _ = r.buf(DEV_FRAMES); scr, _ = r.buf(DEV_FRAMES)
            o = r.osc(v, 0)
            r.call(r.r32(r.r32(o)) & ~1, o, scr, out)
            self.assertEqual(entered[-1:], [o], "control: through an_osc")
        osc1(v1)
        self.assertEqual(fills, [], "voice 1 while voice 0 is named: no fill")
        self.assertEqual(bytes(r.u.mem_read(SA + SA_FMBUF_A, 4 * AN_FM_FRAMES)), b"\x5a" * (4 * AN_FM_FRAMES), "A untouched")
        self.assertEqual(r.r32(SA + SA_RUN + RUN_FMTAG) & 1, 0, "Run.fmtag clear")
        calls = fm_capture(r)
        osc1(v0)
        self.assertEqual(fills, [(r.osc(v0, 0), s0)], "voice 0's OSC1 fills its own")
        self.assertEqual(r.r32(SA + SA_RUN + RUN_FMTAG) & 1, 1, "Run.fmtag says A")
        self.assertNotEqual(bytes(r.u.mem_read(SA + SA_FMBUF_A, 4 * DEV_FRAMES)), b"\x5a" * (4 * DEV_FRAMES), "A written")
        # P3 Q5 (review): the FM gate's voice term: with A tagged (voice 0's), voice 1's OSC1, Sub FM 700 too, plays plain
        # (an_render, no an_render_fm, msub 0): A is another voice's sub
        self.assertEqual({x["osc"] for x in calls}, {r.osc(v0, 0)}, "control: voice 0's OSC1 plays Sub FM")
        o = r.osc(v1, 0)
        self.assertEqual(struct.unpack("<H", r.u.mem_read(o + S.OSC_KERNEL + PROPS_AT + 6, 2))[0], 700, "control: Sub FM")
        n0 = len(calls)
        osc1(v1)
        self.assertEqual(len(calls), n0, "voice 1 while A is voice 0's: no FM render")
        self.assertIn(o, [x for x, _ in calls.plain], "voice 1's OSC1: an_render")
        self.assertEqual(struct.unpack("<H", r.u.mem_read(o + S.OSC_KERNEL + MSUB_AT, 2))[0], 0, "msub: no FM played")

    def test_other_waves_copy_silence(self):
        """4.2: stock's core has 10 waves (its LFOs'); the sub's wave key stores value + 1 unclamped, so a hand-edited file
        can give it 6..10, or 0 (key value 255: no render). Their copy is 0 (no Sub FM; A held a non-zero pattern before
        each block: play), and the copy still changes nothing. P3 Q5 (reviewed edit; Q4: the voice's output byte for byte
        against Sub FM 0's): Sub FM 700 now renders OSC1 through the FM loop with A = +0.0, so di = fma(I, +0, inc) = inc
        every sample. Stated exactly: every an_render_fm call (one a block) reads A = +0.0 bit for bit and is p3_golden's
        render at rest (its end phase exactly, its samples within fm_tol, its VDIVs the golden's crossings: SubFm.replay);
        and the voice's output is still Sub FM 0's byte for byte (M, review: the first Q5 edit had loosened this to 1e-5;
        the measured difference is 0: here the FM loop at rest plays an_render's samples exactly)."""
        for key in (5, 9, 255):
            with self.subTest(wave_key=key):
                a = self.play(key + 1, 1000, blocks=3, capture=True)
                b = self.play(key + 1, 1000, blocks=3, fm=(0, 0))
                rr, vv = a[5][0], a[5][1]
                calls = rr.fm
                self.assertTrue(calls and all("y" in x for x in calls), "every call returned through an_osc")
                self.assertEqual([(x["osc"], x["block"] - calls[0]["block"], x["n"]) for x in calls],
                                 [(rr.osc(vv, 0), j, DEV_FRAMES) for j in range(3)], "OSC1's FM render, one a block")
                for x in calls:
                    self.assertEqual(np.asarray(x["a"], dtype="<f4").view("<u4").tolist(), [0] * x["n"], "the call's A: +0.0")
                SubFm.replay(self, f"wave key {key}", calls)
                same(self, a[1], b[1], "the voice's output")
                for rec in a[0]:
                    self.assertEqual(rec["wave"], (key + 1) & 0xFF)
                    self.assertEqual(rec["a"][:DEV_FRAMES].view("<u4").tolist(), [0] * DEV_FRAMES, "A: +0.0")

    def test_longer_blocks_than_the_buffer_fill_nothing(self):
        """4.2: a block longer than AN_FM_FRAMES gets no FM: A is not written, the tag stays clear and each render that wanted
        it counts in fm_skipped (fmx=); at 2N the same voice fills A. (Rig's own voice: no stock sub, its core wave 0.)
        P3 Q5 (review): and plays no FM (the gate's tag term: a render would read A past its 256 floats): no an_render_fm
        call, fm= 0, msub 0 after it, so the next 2N block ramps the index in from I(0) (its line (0, I(700) / 2N)); at 2N
        both oscillators play FM (A = 0: at rest) and msub ends on 700. BE4.43 (expected change): OSC2's Sub FM 700 plays
        nothing next to the Analog OSC1, so only OSC1 wants A: fm_skipped 3, fm= 3, msub (700, 0)."""
        for n, fill in ((2 * DEV_FRAMES, False), (DEV_FRAMES, True)):
            with self.subTest(n=n):
                r = Rig(dev=True); root, v = r.voice(0x6D)
                for i in (0, 1):
                    P3Identity.push(r, r.osc(v, i), 0x6D, i, (1, 48, 0, 0), (700, 0, 0))
                r.note(v, 60)
                r.u.mem_write(SA + SA_FMBUF_A, b"\x5a" * (4 * AN_FM_FRAMES))
                calls = fm_capture(r)
                for _ in range(3):
                    r.render(root, v, n, (0, 1)); r.block()
                a = bytes(r.u.mem_read(SA + SA_FMBUF_A, 4 * AN_FM_FRAMES))
                self.assertEqual(r.r32(SA + SA_FMSTATS + FM_SKIPPED), 0 if fill else 3, "fm_skipped: OSC1, 3 blocks (BE4.43)")
                self.assertEqual(r.r32(SA + SA_RUN + RUN_FMTAG) & 1, int(fill), "Run.fmtag")
                self.assertEqual(a, bytes(4 * AN_FM_FRAMES) if fill else b"\x5a" * (4 * AN_FM_FRAMES),
                                 "A: the zero sub's copy (wave 0)" if fill else "A untouched")
                self.assertEqual(r.r32(SA + SA_FMSTATS + FM_FM), 3 if fill else 0, "fm= (BE4.43: OSC1's)")
                self.assertEqual(SubFm.renders(calls), 3 if fill else 0, "the renders with FM")
                msub = [struct.unpack("<H", r.u.mem_read(r.osc(v, i) + S.OSC_KERNEL + MSUB_AT, 2))[0] for i in (0, 1)]
                self.assertEqual(msub, [700, 0] if fill else [0, 0], "msub (BE4.43: OSC2's Sub FM plays nothing here)")
                if not fill:
                    blk = r.idx
                    r.render(root, v, DEV_FRAMES, (0, 1)); r.block()
                    SubFm.lines(self, "after the long blocks", calls, r.osc(v, 0), {blk: (0, 700)})
                    self.assertEqual([x for x in calls if x["osc"] == r.osc(v, 1)], [], "OSC2: no FM (BE4.43)")


# ---- P3 Q5: Sub FM (docs/plan-p3-fm-bits.md 4.1, 4.3, 4.4, 4.8, 6.3 - 6.6) --------------------------------------------------
FM_ROLES = ("free", "master", "slave")
FM_FM = 0                   # SaFmStats.fm (byte offset): renders with FM (fm=)
AN_EVENTS_N = 128           # analog.h AN_EVENTS: a sync chunk


def an_index(v):
    """The kernel's index for an amount (dsp/analog.cpp an_index, BE4.42's stated arithmetic): f32(f32(v sqrt32(v)) K),
    K = f32(16 / 1000^1.5), sqrt32 VSQRT (correctly rounded, as numpy's float32 sqrt); p3_golden.fm_index (16 (v / 1000)^1.5,
    float64) within two float32 roundings (FmKernel.test_the_index_is_the_pow15_law)."""
    x = np.float32(v)
    return float(np.float32(np.float32(x * np.sqrt(x)) * np.float32(16.0 / 1000.0 ** 1.5)))


def sym_range(name):
    """[start, end) of one DSP function (its exact name, or the one mangled name containing it): its symbol to the next DSP
    symbol (or the window's end)."""
    a = DSP[name] & ~1 if name in DSP else dsp_sym(name)
    return a, min([b & ~1 for b in DSP.values() if (b & ~1) > a] + [S.M7_DSP_LIMIT])


def fm_ranges():
    """an_render_fm's code: the function (its role loops inlined) and the long-way sample it calls, fm_slow (out of line)."""
    return [sym_range("an_render_fm"), sym_range("fm_slow")]


def is_vdiv(uc, a, sz):
    if sz != 4:
        return False
    w0, w1 = struct.unpack("<HH", uc.mem_read(a, 4))
    return w0 & 0xFFB0 == 0xEE80 and w1 & 0x0F50 == 0x0A00     # VDIV.F32


def fm_golden(inc, k, a, ia, dia, off, n, phase, delayed, role, ev=None, width=None, mutant=None, b=None, ib=0.0, dib=0.0):
    """p3_golden's render of one an_render_fm call (law "f32", fma True: the kernel's stated arithmetic, analog_kernel.cpp
    run_fm / fm_part): I_j = f32(ia + dia j), j the block frame off + i; di = f32(inc + I_j a_j) clamped. P3 Q6: b (the
    call's second modulator, FmIn.b's samples) with its line J_j = f32(ib + dib j), folded after a: di = f32(f32(inc + I a) +
    J b) (p3_golden's order "ab", 4.3); a zero line adds +0 exactly, so passing one changes nothing. Returns (FmRender, di,
    I)."""
    j = np.arange(off, off + n, dtype=np.float64)
    I = G._fma32(np.full(n, f32(ia)), np.full(n, f32(dia)), j)
    terms = [(I, np.asarray(a, dtype=np.float64))]
    if b is not None:
        terms.append((G._fma32(np.full(n, f32(ib)), np.full(n, f32(dib)), j), np.asarray(b, dtype=np.float64)))
    di = G.increments(inc, terms, law="f32", fma=True)
    g = G.fm_render(di, k=k, phase=phase, delayed=delayed, role=FM_ROLES[role], sync=ev if role == 2 else None, law="f32",
                    fma=True, width=width, mutant=mutant, inc0=inc)
    return g, di, I


_WIDTH = {}
def fm_width(k):
    """The kernel's float32 pulse width at k (dc = 2 w - 1 is exact, so w = (dc + 1) / 2 exactly), None at and below the
    square (FmWave's 0.5 is the kernel's). p3_golden: an edge's position error moves its fraction by err / di, large near
    di = 0, so the golden takes the kernel's own width (its width= argument). BE4.42: dc from kernel_dc32 (set_shape's
    arithmetic, pinned to an_render's plateaus by BitDepthGone.test_the_width_model_is_the_kernels; BE4.41 called the
    kernel's an_dc, removed with Bit Depth)."""
    if not k > 0.5:
        return None
    if k not in _WIDTH:
        _WIDTH[k] = (kernel_dc32(k) + 1.0) / 2.0
    return _WIDTH[k]


def fm_tol(k, role):
    """TOL_F32 + the sine's weight x sinq's stated error: once for a free / master sample (the value), twice for a slave
    (Q3's slave bound: the reset's BLEP and BLAMP read sinq too, 0.5 + 0.45 x 2 pi / 6 < 1 more)."""
    ws = G.FmWave(k).ws if k < 0 else 0.0
    return G.TOL_F32 + (2 if role == 2 else 1) * ws * SINQ_ERR


def fm_check(tc, name, g, y, phase, ev, k, role):
    """One call against its golden: the end phase first (the same float32 law: a difference means di's arithmetic statement
    is wrong, not the tolerance), the samples within fm_tol, a master's bytes the golden's (ev_near +-1). Returns err / tol."""
    tc.assertEqual(f32(phase), f32(g.phase), f"{name}: the end phase (di's arithmetic, the float32 law)")
    tol = fm_tol(k, role)
    d = np.abs(np.asarray(y, dtype=np.float64) - g.y)
    err = float(np.max(d)) if len(d) else 0.0
    tc.assertLessEqual(err, tol, f"{name}: worst {err:.3g} > {tol:.3g} at {int(np.argmax(d)) if len(d) else 0}")
    if role == 1:
        got, want = np.frombuffer(bytes(ev), np.uint8).astype(int), g.ev.astype(int)
        tc.assertEqual(list(got == 255), list(want == 255), f"{name}: the master's wraps")
        bad = (np.abs(got - want) > 0) & ~(g.ev_near & (np.abs(got - want) <= 1))
        tc.assertFalse(bad.any(), f"{name}: event bytes {list(got[bad][:4])} vs {list(want[bad][:4])}")
    return err / tol


class FmBench:
    """an_render_fm called directly on one Rig, its buffers allocated once: (y, phase, delayed, events, instructions, VDIVs
    inside an_render_fm's code by PC: fm_ranges)."""
    def __init__(self, r=None):
        self.r = r = r or Rig()
        self.st, self.a, self.fi, self.b = r.alloc(176), r.alloc(4 * 1024), r.alloc(28), r.alloc(4 * 1024)
        self.out, self.ev = r.alloc(4 * 1024), r.alloc(1024)
        self.divs = 0
        def h(uc, a, sz, _):
            if is_vdiv(uc, a, sz):
                self.divs += 1
        for lo, hi in fm_ranges():
            r.u.hook_add(UC_HOOK_CODE, h, begin=lo, end=hi - 1)

    def state(self, phase, delayed):
        self.r.u.mem_write(self.st, struct.pack("<Iff", AN_MAGIC, phase, delayed))

    def call(self, inc, k, a, ia, dia, off, n, role=0, ev=None, b=None, ib=0.0, dib=0.0):
        """a: the block's A from frame 0 (the call reads a[off:off + n]). P3 Q6: b the second modulator (from frame 0) and
        its line; None: FmIn.b = a at a zero line (an_osc's one-route form)."""
        r = self.r
        r.u.mem_write(self.a, np.asarray(a, dtype="<f4").tobytes())
        if b is not None:
            r.u.mem_write(self.b, np.asarray(b, dtype="<f4").tobytes())
        r.u.mem_write(self.fi, struct.pack("<IffIIff", self.a, ia, dia, off, self.b if b is not None else self.a, ib, dib))
        r.u.mem_write(self.ev, bytes(ev) + bytes([255]) * (1024 - len(ev)) if ev is not None else bytes([255]) * 1024)
        d0 = self.divs
        _, cnt = r.call(DSP["an_render_fm"], self.st, self.out, n, self.ev, sargs=(role, self.fi), fargs=(inc, k))
        y = np.frombuffer(bytes(r.u.mem_read(self.out, 4 * n)), dtype="<f4").astype(np.float64)
        phase, delayed = struct.unpack("<ff", r.u.mem_read(self.st + 4, 8))
        return y, phase, delayed, bytes(r.u.mem_read(self.ev, n)), cnt, self.divs - d0


def edge_count(g):
    """p3_golden's crossings that cost the kernel a VDIV (its statement, analog_kernel.cpp fm_part): every wrap, the width
    where the blend is above 0, the top where the triangle's weight is; resets none (at is a byte)."""
    e = g.edges
    return sum(e[f"{k}_{d}"] for k in ("wrap", "width", "half") for d in "fb")


def f32a(x): return np.float64(np.float32(x))


def sub_a(wave, inc_sub, n, phase=0.1, pw=0.5):
    """A buffer as Q4's copy writes it, from p3_golden's sub waves: f32(inc_sub x (y - dc)) (polarity the golden's; the voice
    tests feed stock's own A)."""
    m, _ = G.sub_mod(wave, inc_sub, n, phase, pw)
    return np.asarray(f32a(inc_sub) * m, dtype=np.float32).astype(np.float64)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class FmKernel(unittest.TestCase):
    """P3 Q5 (plan 4.3, OD5, 6.3, 6.5): an_render_fm on the M7 against p3_golden.py's fm_render (numpy, written from the plan
    alone, before and without the kernel), sample by sample on the kernel's float32 phase law with di fed bit for bit: the
    kernel's own index line (fma(dia, j, ia)) and A, folded as the golden's increments(law "f32", fma True). Every Shape
    region (the sine, blends, the triangle, the saw, saw > square, the square, the pulse, the 8 % pulse), three carriers
    (note 60 at 96 kHz, 0.0123, 0.2), three modulations (a sine sub an octave down at index 8: through zero; a DC-free
    Square at PW 30 % at index 1; a saw at 1.5 x ramping 0 -> 8 across the block), free, master and slave (resets from an
    FM'd golden master that wraps both ways). 2N = 256 frames in two parts of 128, the index line continuing."""
    SHAPES = (0, 12, 24, 36, 48, 68, 88, 100, 127)
    INCS = (f32(261.6256 / DEV_RATE), f32(0.0123), f32(0.2))
    MODS = (("sine I8", "sine", 0.5, 0.5, 1000, 1000), ("square I1", "square", 1.0, 0.3, 354, 354),
            ("saw ramp", "saw", 1.5, 0.5, 0, 1000))
    N = 256

    @classmethod
    def master_events(cls):
        """A golden master under deep FM (inc 0.031, a sine at 0.7 x, index 8: through zero): its bytes, forward and
        backward wraps, for the slave cases (in parts of 128)."""
        inc = f32(0.031)
        a = sub_a("sine", f32(0.7 * inc), cls.N, 0.3)
        g, di, _ = fm_golden(inc, 0.0, a, 8.0, 0.0, 0, cls.N, 0.2, 0.0, 1)
        return g.ev, g.edges, float(np.min(di))

    def run_case(self, b, shape, inc, mod, role, mev):
        name, wave, ratio, pw, v0, v1 = mod
        k = f32(k_of(shape)); N = self.N
        a = sub_a(wave, f32(ratio * inc), N, 0.1, pw)
        ia = an_index(v0); dia = f32(f32(an_index(v1) - an_index(v0)) / N)
        p0 = f32(0.37); d0 = f32(G.FmWave(k, "f32", fm_width(k)).value(p0))
        b.state(p0, d0)
        gp, gd, worst, divs, edges, info = p0, d0, 0.0, 0, 0, dict(resets_b=0, wrap_b=0, dmin=1.0)
        for off in (0, N // 2):
            n = N // 2
            ev = list(mev[off:off + n]) if role == 2 else None
            y, ph, dl, evo, _, dv = b.call(inc, k, a, ia, dia, off, n, role, ev)
            g, di, _ = fm_golden(inc, k, a[off:off + n], ia, dia, off, n, gp, gd, role, ev, fm_width(k))
            worst = max(worst, fm_check(self, f"Shape {shape} inc {inc:.4g} {name} {FM_ROLES[role]} off {off}", g, y, ph, evo,
                                        k, role))
            divs += dv; edges += edge_count(g)
            info["resets_b"] += g.edges["resets_b"]; info["wrap_b"] += g.edges["wrap_b"]
            info["dmin"] = min(info["dmin"], float(di.min()))
            gp, gd = g.phase, g.delayed
        return worst, divs, edges, info

    def test_the_kernel_is_the_golden_model(self):
        """Every case: the end phase of each part the golden's exactly (so di's arithmetic is as stated), every sample
        within fm_tol (TOL_F32 + the sine's weight x SINQ_ERR; twice that for a slave), a master's event bytes the golden's
        (+-1 where its fraction is within float32 rounding of a byte boundary), and (6.5) the VDIVs executed inside
        an_render_fm = the golden's crossings that divide (every wrap, the pulse edge where the blend > 0, the triangle's
        top where its weight > 0), +- 0, at most 2n. Controls: the modulations run the phase backward (di < 0), the slave
        sees backward resets (OD5: to p = 1) and the master backward wraps."""
        mev, medges, mdmin = self.master_events()
        self.assertGreater(medges["wrap_b"], 0, "control: the golden master wraps backward too (OD5: those reset the slave)")
        worst, seen = (0.0, None), dict(resets_b=0, wrap_b=0, dmin=1.0, cases=0)
        for shape in self.SHAPES:
            b = FmBench()
            for inc in self.INCS:
                for mod in self.MODS:
                    for role in (0, 1, 2):
                        with self.subTest(shape=shape, inc=inc, mod=mod[0], role=FM_ROLES[role]):
                            w, divs, edges, info = self.run_case(b, shape, inc, mod, role, mev)
                            self.assertEqual(divs, edges, "VDIVs inside an_render_fm = the golden's dividing crossings")
                            self.assertLessEqual(divs, 2 * self.N, "6.5: at most 2n a render")
                            seen["cases"] += 1
                            for key in ("resets_b", "wrap_b"):
                                seen[key] += info[key]
                            seen["dmin"] = min(seen["dmin"], info["dmin"])
                            if w > worst[0]:
                                worst = (w, (shape, inc, mod[0], FM_ROLES[role]))
        print(f"\n  FM kernel vs golden: {seen['cases']} cases, worst {worst[0]:.2f} of its tolerance at {worst[1]}; "
              f"backward resets {seen['resets_b']}, backward wraps {seen['wrap_b']}, lowest di {seen['dmin']:.4f}",
              file=sys.stderr)
        self.assertLess(seen["dmin"], 0.0, "control: through zero")
        self.assertGreater(seen["resets_b"], 0, "control: backward slave resets (OD5, p = 1)")
        self.assertGreater(seen["wrap_b"], 0, "control: backward wraps")

    def test_the_corner_rules(self):
        """P3 Q5 (review): OD5 / 4.3's corner rules, on crafted states the real cases do not reach, each against the golden
        (fm_check; VDIVs = its crossings): (1) a master wrap at fraction exactly 0 writes its byte (0), from p = 0 going
        backward and from p = 1.0 (the left limit a backward wrap can leave) going forward; (2) a slave event at a sample
        with di == 0 exactly (a = -inc at index 1: fma(1, -inc, inc) = 0) resets nothing, one with di > 0 does (control);
        (3) a phase exactly on the pulse's edge (p == w: the high side, [lo, hi)) moving backward crosses it."""
        inc, n = f32(0.0123), 64
        b = FmBench()
        def run(name, shape, p0, a, role, ev=None):
            k = f32(k_of(shape))
            d0 = f32(G.FmWave(k, "f32", fm_width(k)).value(p0))
            b.state(p0, d0)
            a = np.asarray(a, dtype=np.float64)
            y, ph, _, evo, _, dv = b.call(inc, k, a, 1.0, 0.0, 0, n, role, ev)
            g, di, _ = fm_golden(inc, k, a, 1.0, 0.0, 0, n, p0, d0, role, ev, fm_width(k))
            fm_check(self, name, g, y, ph, evo, k, role)
            self.assertEqual(dv, edge_count(g), f"{name}: VDIVs = the golden's crossings")
            return g, di, evo, ph
        for p0, sign in ((0.0, -1.0), (1.0, 0.0)):
            with self.subTest(case=f"master byte at fraction 0 from p = {p0}"):
                g, di, evo, _ = run("byte", 48, f32(p0), np.full(n, f32(-2.0 * inc) if sign else 0.0), 1)
                self.assertEqual(float(di[0]), -inc if sign else inc, "control: the step")
                self.assertEqual(evo[0], 0, "the wrap at fraction 0: byte 0")
                self.assertEqual(int(g.ev[0]), 0, "the golden's too")
        with self.subTest(case="slave event at di == 0"):
            a = np.zeros(n); ev = [255] * n
            for i in (10, 50):
                a[i] = -inc; ev[i] = 127
            ev[30] = 127                                         # di = inc there: a reset (control)
            g, di, _, ph = run("di 0", 48, f32(0.37), a, 2, ev)
            self.assertEqual([float(di[i]) for i in (10, 30, 50)], [0.0, inc, 0.0], "control: di 0 exactly")
            self.assertEqual(g.edges["resets"], 1, "one reset: none at di == 0")
        with self.subTest(case="on the edge, backward"):
            k = f32(k_of(100)); w = f32(fm_width(k))
            g, di, _, _ = run("edge", 100, w, np.full(n, f32(-2.0 * inc)), 0)
            self.assertEqual(g.edges["width_b"], 1, "control: the golden crosses the edge backward at sample 0")

    def test_zero_modulation_is_the_plain_render(self):
        """A = 0: di = inc every sample, so the golden is Q3's render at rest; the FM loop plays an_render's samples within
        TOL_F32 + the sine's weight x SINQ_ERR, its phase bit for bit (the same float32 law), a master's bytes within one
        (an_render's fraction multiplies by its 1 / inc). Not bit for bit: an_render divides once a block (E2), the FM loop
        at every edge, and (Q3b) its plateaus run_flat. Zero cost and identity when off are the hook's (an_osc calls
        an_render then: P3Identity)."""
        for shape in self.SHAPES:
            b = FmBench(); k = f32(k_of(shape))
            for inc in self.INCS:
                for role in (0, 1):
                    with self.subTest(shape=shape, inc=inc, role=role):
                        b.state(f32(0.37), 0.0)
                        y, ph, _, ev, _, _ = b.call(inc, k, np.zeros(128), 0.0, 0.0, 0, 128, role)
                        sk = ShapeLow.state(None, b.r, f32(0.37), 0.0)
                        yr, phr, _, evr, _ = ShapeLow.kernel(None, b.r, sk, inc, k, 128, role=role)
                        self.assertEqual(ph, phr, "the phase")
                        self.assertLessEqual(float(np.max(np.abs(y - yr))), fm_tol(k, 0) + G.TOL_F32)
                        if role:
                            self.assertLessEqual(max(abs(x - z) for x, z in zip(ev, evr)), 1)

    def test_the_mutants_the_golden_names_are_seen(self):
        """6.6 on this kernel's own deep through-zero cases (index 8, a sine an octave down, inc 0.0123, 256 samples; free
        and slave):
        each of p3_golden's output mutants (backward crossings not mirrored, no correction on them, E2's per-block rinc
        under FM) leaves the kernel by more than its tolerance; the double edge (OD5's p = 0 for a backward reset) has the
        one-edge rule's output up to rounding (the golden's notes), so its red test is its extra backward wraps in the
        VDIV / edge count."""
        mev, _, _ = self.master_events()
        far = {m: 0.0 for m in G.FM_MUTANTS}; extra = 0
        for shape in (12, 24, 48, 100, 127):
            b = FmBench(); k = f32(k_of(shape)); inc = self.INCS[1]; n = self.N
            a = sub_a("sine", f32(0.5 * inc), n, 0.1)
            for role in (0, 2):
                p0 = f32(0.37); d0 = f32(G.FmWave(k, "f32", fm_width(k)).value(p0)); b.state(p0, d0)
                ev = list(mev[:n]) if role == 2 else None
                y, _, _, _, _, dv = b.call(inc, k, a, 8.0, 0.0, 0, n, role, ev)
                for m in G.FM_MUTANTS:
                    g, _, _ = fm_golden(inc, k, a, 8.0, 0.0, 0, n, p0, d0, role, ev, fm_width(k), mutant=m)
                    if m == "reset_p0":
                        extra += edge_count(g) - dv
                    far[m] = max(far[m], float(np.max(np.abs(y - g.y))) / fm_tol(k, role))
        for m in ("no_mirror", "no_backward", "rinc"):
            self.assertGreater(far[m], 1.0, f"{m}: the kernel is not it")
        self.assertGreater(extra, 0, "reset_p0: the double edge's extra backward wraps (its VDIVs) are not the kernel's")
        print(f"\n  FM mutants against the kernel (worst / tolerance): " + ", ".join(f"{m} {v:.3g}" for m, v in far.items()) +
              f"; reset_p0's extra edges {extra}", file=sys.stderr)

    def test_the_index_is_the_pow15_law(self):
        """FD3 (BE4.42): an_index(v) (the kernel's stated arithmetic) = p3_golden.fm_index(v) = 16 (v / 1000)^1.5 within two
        float32 roundings for every v 0..1000 (I(250) = 2, I(1000) = 16 exactly; 50 % 5.66), never the linear law (6.6) nor
        BE4.41's square law 8 (v / 1000)^2 past v 0 (the lines the voice tests check are built from an_index, so the
        kernel's index is pinned there)."""
        for v in range(1001):
            want = G.fm_index(v)
            self.assertLessEqual(abs(an_index(v) - want), 2 ** -22 * want, v)
        self.assertEqual((an_index(250), an_index(1000), an_index(0)), (2.0, 16.0, 0.0))
        self.assertTrue(all(abs(an_index(v) - G.fm_index(v, "linear")) > 1e-3 for v in range(1, 1000)), "linear")
        # BE4.41's square law is at most half the new one (8 x^2 / 16 x^1.5 = sqrt(x) / 2): both near 0 at small v, so relative
        self.assertTrue(all(abs(an_index(v) - G.fm_index(v, "square")) >= 0.49 * an_index(v) for v in range(1, 1001)), "square")

    def test_the_worst_case_divides_at_most_2n(self):
        """6.5's worst case: the 8 % pulse (Shape 127: the width edge and the wrap a cycle), deep through zero (a sine at 3 x
        the carrier, index 8), a high carrier (inc 0.2), free and master: VDIVs = the golden's crossings, at most 2n; the
        instructions per sample recorded."""
        b = FmBench(); k = f32(k_of(127)); inc = f32(0.2)
        a = sub_a("sine", f32(3 * 0.2), 128, 0.1)
        rows = []
        for role in (0, 1):
            b.state(f32(0.37), 0.0)
            y, ph, _, ev, cnt, dv = b.call(inc, k, a, 8.0, 0.0, 0, 128, role)
            g, _, _ = fm_golden(inc, k, a, 8.0, 0.0, 0, 128, f32(0.37), 0.0, role, None, fm_width(k))
            fm_check(self, f"worst case {FM_ROLES[role]}", g, y, ph, ev, k, role)
            self.assertEqual(dv, edge_count(g)); self.assertLessEqual(dv, 2 * 128)
            rows.append((FM_ROLES[role], cnt / 128, dv))
        print("\n  FM worst case (8 % pulse, sine 3 x at index 8, inc 0.2): " +
              ", ".join(f"{n} {c:.1f} a sample, {d} VDIVs / 128" for n, c, d in rows), file=sys.stderr)


class FmCalls(list):
    """fm_capture's records, with .plain (an_render calls: oscillator, block), .track (an_track's), .fills (sub_fill: the
    oscillator in the last an_osc, block) and .rets (an_osc's return sites). P3 Q6: .feeds (an_feed calls: dict(osc, block,
    sp / s (the part's address and samples at entry), out, n, inc, b (its output))) and .plain_calls (an_render
    calls: dict(osc, block, out, n, inc, k))."""


def fm_capture(r):
    """Every an_render_fm call in the voice path: at its entry (the state's phase and delayed, inc, the Shape, n, the role,
    FmIn's a / ia / dia / off, A's samples for the call, a slave's event bytes, the oscillator, the LOAD ring index) and
    after it returns (the samples, the end state, a master's bytes): the return addresses are an_osc's calls to it
    (capstone, its bl sites; play asserts every call came back through one). Each record's divs: the VDIVs executed inside
    an_render_fm's code (fm_ranges) during it; .osc_divs: VDIVs inside the hook's own code by (oscillator, block): an_osc
    and (P3 Q6) its out-of-line FM setup fm_setup, where the index lines are divided. P3 Q6: the call's second modulator
    (FmIn.b, ib, dib: B, b its samples) and an_feed's calls (.feeds), from every call site in the DSP."""
    recs = FmCalls(); recs.plain, recs.track, recs.fills, recs.osc_divs = [], [], [], {}
    recs.feeds, recs.plain_calls = [], []
    last = [None]
    fn = DSP["an_render_fm"] & ~1
    lo, hi = sym_range("an_osc")
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    def target(i):
        try:
            return int(i.op_str.lstrip("#"), 16)
        except ValueError:
            return None
    recs.rets = [i.address + i.size for i in md.disasm(bytes(r.u.mem_read(lo, hi - lo)), lo)
                 if i.mnemonic in ("bl", "blx") and target(i) == fn]
    def entry(uc, a, sz, _):
        st, out, n, ev = (uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        role, fi = struct.unpack("<II", uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 8))
        sf = lambda reg: struct.unpack("<f", struct.pack("<I", uc.reg_read(reg)))[0]
        A, ia, dia, off, B, ib, dib = struct.unpack("<IffIIff", uc.mem_read(fi, 28))
        phase, delayed = struct.unpack("<ff", uc.mem_read(st + 4, 8))
        recs.append(dict(st=st, out=out, n=n, evp=ev, role=role, inc=sf(UC_ARM_REG_S0), k=sf(UC_ARM_REG_S1), A=A, ia=ia,
                         dia=dia, off=off, B=B, ib=ib, dib=dib,
                         a=np.frombuffer(bytes(uc.mem_read(A + 4 * off, 4 * n)), dtype="<f4").astype(np.float64),
                         b=np.frombuffer(bytes(uc.mem_read(B + 4 * off, 4 * n)), dtype="<f4").astype(np.float64),
                         phase0=phase, delayed0=delayed, ev0=bytes(uc.mem_read(ev, n)) if ev else None,
                         osc=st - S.OSC_KERNEL, block=r.idx, lr=uc.reg_read(UC_ARM_REG_LR) & ~1, divs=0))
    def back(uc, a, sz, _):
        if recs and "y" not in recs[-1] and recs[-1]["lr"] == a:
            x = recs[-1]
            x["y"] = np.frombuffer(bytes(uc.mem_read(x["out"], 4 * x["n"])), dtype="<f4").astype(np.float64)
            x["phase1"], x["delayed1"] = struct.unpack("<ff", uc.mem_read(x["st"] + 4, 8))
            x["ev1"] = bytes(uc.mem_read(x["evp"], x["n"])) if x["evp"] else None
    def div(uc, a, sz, _):
        if recs and "y" not in recs[-1] and is_vdiv(uc, a, sz):
            recs[-1]["divs"] += 1
    def odiv(uc, a, sz, _):
        if is_vdiv(uc, a, sz):
            key = (last[0], r.idx); recs.osc_divs[key] = recs.osc_divs.get(key, 0) + 1
    hook_code = [sym_range("an_osc")] + ([sym_range("fm_setup")] if any("fm_setup" in k for k in DSP) else [])
    r.u.hook_add(UC_HOOK_CODE, entry, begin=fn, end=fn)
    for lo, hi in fm_ranges():
        r.u.hook_add(UC_HOOK_CODE, div, begin=lo, end=hi - 1)
    for lo, hi in hook_code:                                 # (P3 Q6: an_osc's FM setup is out of line, fm_setup)
        r.u.hook_add(UC_HOOK_CODE, odiv, begin=lo, end=hi - 1)
    for a in recs.rets:
        r.u.hook_add(UC_HOOK_CODE, back, begin=a, end=a)
    for sym, lst in (("an_render", recs.plain), ("an_track", recs.track)):
        f = DSP[sym] & ~1
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _, lst=lst: lst.append((uc.reg_read(UC_ARM_REG_R0) - S.OSC_KERNEL, r.idx)),
                     begin=f, end=f)
    sf32 = lambda uc, reg: struct.unpack("<f", struct.pack("<I", uc.reg_read(reg)))[0]
    f = DSP["an_render"] & ~1
    r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: recs.plain_calls.append(dict(
        osc=uc.reg_read(UC_ARM_REG_R0) - S.OSC_KERNEL, block=r.idx, out=uc.reg_read(UC_ARM_REG_R1), n=uc.reg_read(UC_ARM_REG_R2),
        inc=sf32(uc, UC_ARM_REG_S0), k=sf32(uc, UC_ARM_REG_S1))), begin=f, end=f)
    if "an_feed" in DSP:                                     # P3 Q6: buffer B's writes, at entry and at an_osc's return sites
        def feed(uc, a, sz, _):
            s_, out, n = (uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            recs.feeds.append(dict(osc=last[0], block=r.idx, sp=s_, out=out, n=n,      # BE4.42: an_feed(s, b, n, inc)
                                   inc=sf32(uc, UC_ARM_REG_S0), lr=uc.reg_read(UC_ARM_REG_LR) & ~1,
                                   s=np.frombuffer(bytes(uc.mem_read(s_, 4 * n)), dtype="<f4").copy()))
        def feed_back(uc, a, sz, _):
            if recs.feeds and "b" not in recs.feeds[-1] and recs.feeds[-1]["lr"] == a:
                x = recs.feeds[-1]
                x["b"] = np.frombuffer(bytes(uc.mem_read(x["out"], 4 * x["n"])), dtype="<f4").copy()
        def wfeed(uc, a, sz, _):                             # BE4.44 W4: wt_feed_b(s, b, n, inc, m), a Wavetable OSC1's B
            feed(uc, a, sz, _)
            recs.feeds[-1]["m"] = sf32(uc, UC_ARM_REG_S1)
        fs = [DSP["an_feed"] & ~1] + ([DSP["wt_feed_b"] & ~1] if "wt_feed_b" in DSP else [])
        for f, h in zip(fs, (feed, wfeed)):
            r.u.hook_add(UC_HOOK_CODE, h, begin=f, end=f)
        recs.feed_rets = []
        for name in DSP:
            lo, hi = sym_range(name)
            for i in md.disasm(bytes(r.u.mem_read(lo, hi - lo)), lo):
                if i.mnemonic in ("bl", "blx") and target(i) in fs:
                    recs.feed_rets.append(i.address + i.size)
                    r.u.hook_add(UC_HOOK_CODE, feed_back, begin=i.address + i.size, end=i.address + i.size)
    o = DSP["an_osc"] & ~1
    r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: last.__setitem__(0, uc.reg_read(UC_ARM_REG_R0)), begin=o, end=o)
    f = dsp_sym("sub_fill")
    r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: recs.fills.append((last[0], r.idx)), begin=f, end=f)
    return recs


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class SubFm(unittest.TestCase):
    """P3 Q5 (plan 4.1, 4.4, 4.5, 4.8, 6.3, 6.4): Sub FM in the voice, on the device's path (stock's root ctor and engine
    init, 2N = 256 frames, stock's sub rendering after OSC1 / OSC2: SubCopy's harness): every an_render_fm call an_osc makes
    is replayed through p3_golden (fm_golden: the call's own A, index line, state, role and events) and must match it
    (fm_check); and the plumbing around it: the index ramp from msub to the amount over the block (FD3's law), the
    parts continuing one line, the block after a ramp to 0 back on an_render, E3 (b) (a silent master with Sub FM renders
    its wraps under FM, an_track without it, a ramp down staying on FM to its end), E3 (c), the mixed voice (OSC2 fills A),
    the slave's events = its master's bytes, fm= = the renders with FM, the Square sub's DC-free pitch."""
    voice, note = SubCopy.voice, SubCopy.note

    def play(self, wave=4, fm=(1000, 1000), shapes=(48, 48), sync=0, types=(1, 1), levels=None, blocks=6, amounts=None,
             pw=300, note=48, taps=False):
        """One device-path case: (FmCalls, voice output bytes, SA_FMSTATS words, level taps or None, (r, v, sub)).
        amounts(b) -> {osc: Sub FM} pushed through stock's root before block b; levels(b) -> (OSC1, OSC2) Level."""
        r = Rig()
        root, v, sub, src = self.voice(r, wave, 1000, fm=fm, types=types, glide=0, pw=pw, fine=0, sync=sync)
        for i in (0, 1):
            if types[i]:
                r.root_prop(root, K_SHAPE, shapes[i], i)
        calls = fm_capture(r)
        r.v = v
        tp = level_taps(r) if taps else None
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
        slot18 = r.r32(r.r32(root) + 0x48)
        self.note(r, v, sub, note)
        outs = b""
        r.first = r.idx
        for b in range(blocks):
            for i, x in (amounts(b) if amounts else {}).items():
                r.root_prop(root, K_SUBFM, x, i)
            for i, l in enumerate(levels(b) if levels else (1.0, 1.0)):
                r.level(v, i, l)
            r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
            r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
            outs += bytes(r.u.mem_read(od, 4 * DEV_N))
            r.block()
        self.assertTrue(all("y" in x for x in calls), f"every an_render_fm call returned through an_osc's sites {calls.rets}")
        stats = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
        return calls, outs, stats, tp, (r, v, sub)

    def replay(self, name, calls):
        """Every call against p3_golden (fm_check), its VDIVs (by PC, inside an_render_fm and fm_slow) the golden's dividing
        crossings (6.5); A is buffer A. Returns (worst err / tol, the lowest di)."""
        worst, dmin = 0.0, 1.0
        for j, x in enumerate(calls):
            # P3 Q6: Sub FM reads A (FmIn.a), FM 1>2 alone B; both: a = A, b = B on FM 1>2's line; one route: b = a, a zero line
            two = (x["ib"], x["dib"]) != (0.0, 0.0)
            self.assertIn(x["A"], (SA + SA_FMBUF_A, SA + SA_FMBUF_B), f"{name}: call {j} reads buffer A or B")
            self.assertEqual((x["A"], x["B"]) if two else x["B"], (SA + SA_FMBUF_A, SA + SA_FMBUF_B) if two else x["A"],
                             f"{name}: call {j}: the second term is B, or none (b = a at a zero line)")
            g, di, _ = fm_golden(x["inc"], x["k"], x["a"], x["ia"], x["dia"], x["off"], x["n"], x["phase0"], x["delayed0"],
                                 x["role"], list(x["ev0"]) if x["role"] == 2 else None, fm_width(x["k"]), b=x["b"],
                                 ib=x["ib"], dib=x["dib"])
            worst = max(worst, fm_check(self, f"{name}: call {j} (block {x['block']}, {FM_ROLES[x['role']]}, off {x['off']})",
                                        g, x["y"], x["phase1"], x["ev1"], x["k"], x["role"]))
            self.assertEqual(x["divs"], edge_count(g), f"{name}: call {j}: VDIVs inside an_render_fm = the golden's crossings (6.5)")
            dmin = min(dmin, float(di.min()))
        return worst, dmin

    def lines(self, name, calls, osc, want, n=DEV_FRAMES):
        """The index plumbing: osc's calls in each block are parts that tile it in order (off continuing), each with the
        block's line (ia, dia) = (I(last block's amount), f32(I(amount) - I(last)) / 2N); want: block -> (v0, v1)."""
        by = {}
        for x in calls:
            if x["osc"] == osc:
                by.setdefault(x["block"], []).append(x)
        self.assertEqual(sorted(by), sorted(want), f"{name}: the blocks with FM")
        for blk, (v0, v1) in want.items():
            xs = by[blk]
            self.assertEqual([x["off"] for x in xs], [sum(y["n"] for y in xs[:m]) for m in range(len(xs))], f"{name}: parts")
            self.assertEqual(sum(x["n"] for x in xs), n, f"{name}: block {blk} rendered end to end")
            i0, i1 = an_index(v0), an_index(v1)
            dia = 0.0 if i1 == i0 else f32(f32(i1 - i0) / n)
            self.assertTrue(all((x["ia"], x["dia"]) == (i0, dia) for x in xs),
                            f"{name}: block {blk}: line {(xs[0]['ia'], xs[0]['dia'])} vs {(i0, dia)}")
            self.assertLessEqual(abs(i0 + n * dia - i1), 1e-6 * max(1.0, i1), f"{name}: block {blk} ends on I(amount)")

    @staticmethod
    def renders(calls):
        return len({(x["osc"], x["block"]) for x in calls})

    def test_both_oscillators_free(self):
        """Sub FM 100 % on both (index 16 since BE4.42, a sine sub: through zero), OSC1 at the saw, OSC2 at the 8 % pulse,
        and at the triangle / sine, 6 blocks: every call the golden's; one part a block (free, held Shape); the line from 0 in
        the first block (msub starts at 0: the amount ramps in), then held at I(1000) = 16; fm= = 2 a block; the output not Sub FM 0's,
        whose stats line stays 0. BE4.43 (expected change, Jeff's design): OSC2's FM next to an Analog OSC1 is FM 1>2, so its
        Sub FM 100 % plays nothing: only OSC1 renders under FM (fm= 1 a block), OSC2 plays an_render every block, and the
        output is the same case's with OSC2's Sub FM 0, byte for byte (its stored value ignored)."""
        for shapes in ((48, 127), (24, 0)):
            with self.subTest(shapes=shapes):
                calls, outs, stats, _, (r, v, sub) = self.play(shapes=shapes)
                w, dmin = self.replay(f"free {shapes}", calls)
                self.assertLess(dmin, 0.0, "control: through zero")
                self.lines("free", calls, r.osc(v, 0), {r.first + b: (0 if b == 0 else 1000, 1000) for b in range(6)})
                self.assertEqual([x for x in calls if x["osc"] == r.osc(v, 1)], [], "OSC2: no FM render (BE4.43)")
                self.assertEqual(sorted(blk - r.first for o, blk in calls.plain if o == r.osc(v, 1)), list(range(6)))
                self.assertEqual(stats[FM_FM // 4], 6, "fm=: OSC1, 6 blocks (BE4.43)")
                _, outs1, _, _, _ = self.play(shapes=shapes, fm=(1000, 0))
                self.assertEqual(outs, outs1, "OSC2's Sub FM 100 % plays as 0 next to an Analog OSC1 (BE4.43)")
                _, outs0, stats0, _, _ = self.play(shapes=shapes, fm=(0, 0))
                self.assertNotEqual(outs, outs0, "Sub FM changes the sound")
                self.assertEqual(stats0, (0,) * 8, "Sub FM 0: nothing counted")
                print(f"\n  Sub FM free {shapes}: worst {w:.2f} of the tolerance, lowest di {dmin:.4f}", file=sys.stderr)

    def test_every_sub_wave_modulates(self):
        """6.3's modulators: the sub's Rev Saw, Triangle, Square (PW 30 %, DC-free), Sine and Noise (its stride-stepped
        table: p3_golden does not model it, so the call's own A is the modulator, as for every wave here), Sub FM 100 % on
        OSC1 (the saw) and 50 % on OSC2 (Shape 100), 4 blocks: every call the golden's, and each wave's A differs. BE4.43:
        OSC2's 50 % plays nothing next to the Analog OSC1 (one FM amount per oscillator), so fm= is OSC1's 4."""
        seen = set()
        for wave in SUB_WAVES:
            with self.subTest(wave=SUB_WAVES[wave]):
                calls, _, stats, _, _ = self.play(wave=wave, fm=(1000, 500), shapes=(48, 100), blocks=4)
                self.replay(SUB_WAVES[wave], calls)
                self.assertEqual(stats[FM_FM // 4], 4, "BE4.43: OSC1's renders")
                a = np.concatenate([x["a"] for x in calls])
                self.assertGreater(float(np.max(np.abs(a))), 0.0, "control: A moves")
                seen.add(a.tobytes())
        self.assertEqual(len(seen), len(SUB_WAVES), "control: five different modulators")

    def test_the_index_ramps_over_the_block(self):
        """4.3 / 4.4 / 6.4: OSC1's Sub FM 0, 1000, 1000, 500, 0, 0, 250, 1000 by block (OSC2 none): each block's line runs
        from I(last block's) to I(this block's) (FD3: I(250) = 2, I(500) = 5.66), the parts continue it; the ramp down (block 4:
        amount 0, msub 500) is still FM; the block after it (5) is an_render's (no FM call: bit for bit the non-FM path).
        fm= counts the FM renders; msub ends on the last amount; an_osc's own VDIVs are the off count plus one in each FM
        block (the line's step, 6.5)."""
        amts = (0, 1000, 1000, 500, 0, 0, 250, 1000)
        calls, outs, stats, _, (r, v, sub) = self.play(fm=(0, 0), blocks=len(amts), amounts=lambda b: {0: amts[b]})
        self.replay("ramp", calls)
        want = {r.first + b: (amts[b - 1] if b else 0, amts[b]) for b in range(len(amts)) if amts[b] or (b and amts[b - 1])}
        o1 = r.osc(v, 0)
        self.lines("ramp", calls, o1, want)
        plain = sorted({blk for o, blk in calls.plain if o == o1})
        self.assertEqual(plain, [r.first + b for b in range(len(amts)) if r.first + b not in want], "an_render where no FM is due")
        self.assertEqual(stats[FM_FM // 4], len(want))
        self.assertEqual(struct.unpack("<H", r.u.mem_read(o1 + S.OSC_KERNEL + MSUB_AT, 2))[0], 1000, "msub: the last amount")
        # 6.5 "everywhere else": an_osc's own VDIVs (rate_of's two, ...) are the FM-off count, plus the line's one division
        # in every FM block (M: the compiler computes the step and selects 0 for a held amount: one VDIV, not per sample)
        dv = {b: calls.osc_divs.get((o1, r.first + b), 0) for b in range(len(amts))}
        base = dv[0]
        self.assertGreater(base, 0, "control: an_osc divides (rate_of)")
        self.assertEqual(dv, {b: base + (r.first + b in want) for b in dv}, "an_osc's VDIVs per block: + 1 with FM")

    def test_the_device_index_is_an_index32_for_a_sweep(self):
        """Review (FD3 on the device, BE4.42): OSC1's Sub FM stepped through 1, 250, 1000 and every 11th amount 3..999 (one a
        block): each FM block's first call starts its line at p3_golden.an_index32(last block's amount), bit for bit, and the
        line ends at an_index32(this block's) within float32 rounding: fm_setup's VSQRT / VMUL / VMUL is the golden law over
        the whole range, not only at the voice tests' amounts."""
        amts = (1, 250, 1000) + tuple(range(3, 1000, 11)) + (1000,)
        calls, _, _, _, (r, v, sub) = self.play(fm=(0, 0), blocks=len(amts), amounts=lambda b: {0: amts[b]})
        o1 = r.osc(v, 0)
        firsts = {x["block"]: x for x in calls if x["osc"] == o1 and x["off"] == 0}
        for b in range(1, len(amts)):
            x = firsts[r.first + b]
            self.assertEqual(np.float32(x["ia"]).tobytes(), np.float32(G.an_index32(amts[b - 1])).tobytes(),
                             f"block {b}: the line starts at an_index32({amts[b - 1]})")
            end = float(np.float32(x["ia"]) + np.float32(x["dia"]) * np.float32(DEV_FRAMES))
            self.assertAlmostEqual(end, G.an_index32(amts[b]), delta=2e-5, msg=f"block {b}: ends at an_index32({amts[b]})")

    def test_sync_master_and_slave(self):
        """M6, both Analog (OSC2 Sync): OSC1 the MASTER with its own Sub FM, OSC2 the SLAVE with Sub FM (through zero: OSC1's
        backward wraps reset OSC2, OD5), Shapes (24, 100) and (48, 12): every call the golden's (the master's bytes its
        golden wraps), and each slave block's bytes are the master's of that block (chunk 0 in WT2's events, chunk 1 in
        WT1's). BE4.43 (expected change): OSC2's Sub FM plays nothing next to the Analog OSC1, so the SLAVE renders plain
        (an_render, two chunks a block), the master alone under FM (fm= 6); a slave under FM is Fm12's sync test (FM 1>2)
        and the mixed voice's (Sub FM, test_a_mixed_voice)."""
        for shapes in ((24, 100), (48, 12)):
            with self.subTest(shapes=shapes):
                calls, _, stats, _, (r, v, sub) = self.play(shapes=shapes, sync=1)
                self.replay(f"sync {shapes}", calls)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                for blk in sorted({x["block"] for x in calls}):
                    m = b"".join(x["ev1"] for x in calls if x["osc"] == o1 and x["block"] == blk)
                    self.assertEqual(len(m), DEV_FRAMES)
                    self.assertTrue(all(x["role"] == 1 and x["osc"] == o1 for x in calls if x["block"] == blk))
                self.assertGreater(sum(e != 255 for x in calls if x["osc"] == o1 for e in x["ev1"]), 0, "control: wraps")
                self.assertEqual(stats[FM_FM // 4], 6, "fm=: the master's (BE4.43)")
                self.assertEqual(sorted(blk - r.first for o, blk in calls.plain if o == o2), sorted(list(range(6)) * 2),
                                 "the slave: an_render, two chunks a block (BE4.43: its Sub FM plays nothing)")
                self.lines(f"sync {shapes}", calls, o1, {r.first + b: (0 if b == 0 else 1000, 1000) for b in range(6)})
                self.assertEqual({x["off"] for x in calls if x["osc"] == o1}, {0, AN_EVENTS_N}, "control: chunked")

    def test_a_silent_master_renders_its_wraps_under_fm(self):
        """E3 (b): OSC1 at Level 0 is OSC2's master, with Sub FM 700, 700, then 0, 0, 0: while it has Sub FM (and in the block
        that ramps it to 0: msub) it renders under FM (MASTER, unheard), not an_track, its bytes the golden's and OSC2's
        events; once the ramp has ended an_track again. OSC2 (no Sub FM) renders plain. P3 Q5 (review): its two chunks a
        block tile it at off 0 and AN_EVENTS on the block's index line (lines: the second chunk reads A from frame 128 and
        continues the line, so the master's wraps, OSC2's resets, are those of the block's modulation). Their Shape:
        test_a_silent_master_heard_again_continues."""
        amts = (700, 700, 0, 0, 0)
        calls, _, stats, _, (r, v, sub) = self.play(fm=(700, 0), sync=1, levels=lambda b: (0.0, 1.0), blocks=5,
                                                    amounts=lambda b: {0: amts[b]})
        self.replay("silent master", calls)
        o1, o2 = r.osc(v, 0), r.osc(v, 1)
        self.assertEqual(sorted({x["block"] - r.first for x in calls if x["osc"] == o1}), [0, 1, 2],
                         "FM through the ramp down (block 2: amount 0, msub 700)")
        self.assertTrue(calls and all(x["role"] == 1 and x["osc"] == o1 for x in calls), "only the master, in its role")
        self.assertEqual(sorted({blk - r.first for o, blk in calls.track if o == o1}), [3, 4], "an_track once the ramp has ended")
        for blk in range(r.first, r.first + 3):
            m = b"".join(x["ev1"] for x in calls if x["block"] == blk)
            self.assertEqual(len(m), DEV_FRAMES)
        self.assertEqual(stats[FM_FM // 4], 3)
        self.lines("silent master", calls, o1, {r.first + b: (amts[b - 1] if b else 0, amts[b]) for b in range(3)})
        self.assertEqual({x["off"] for x in calls}, {0, AN_EVENTS_N}, "control: chunked")

    def test_a_silent_master_heard_again_continues(self):
        """E3 (b), then heard: OSC1 (OSC2's master, Sub FM 700, Shape 100: k = 0.65, not the saw's 0) silent in blocks 0
        and 1, heard from block 2: its silent renders and its heard ones are one Shape (E3 (b) renders at the stored Shape,
        so `delayed` is that Shape's; review: k = 0 there was not seen), the first heard call starts from
        the last silent call's end state (phase and delayed), and the heard calls are the golden's from there (replay), so the
        first heard sample continues the unheard wave; every block's line continues (0 -> 700, then held)."""
        calls, _, stats, _, (r, v, sub) = self.play(fm=(700, 0), sync=1, levels=lambda b: (0.0 if b < 2 else 1.0, 1.0),
                                                    blocks=4, shapes=(100, 48))
        self.replay("heard again", calls)
        o1 = r.osc(v, 0)
        xs = [x for x in calls if x["osc"] == o1]
        self.assertEqual(sorted({x["block"] - r.first for x in xs}), [0, 1, 2, 3])
        self.assertEqual(calls.track, [], "control: E3 (b), not an_track")
        ks = {x["k"] for x in xs}
        self.assertEqual(len(ks), 1, f"silent and heard: one Shape, {ks}")
        self.assertLess(abs(ks.pop() - k_of(100)), 1e-6, "the stored Shape's k")
        last = max((x for x in xs if x["block"] == r.first + 1), key=lambda x: x["off"])
        first = min((x for x in xs if x["block"] == r.first + 2), key=lambda x: x["off"])
        self.assertEqual((first["phase0"], first["delayed0"]), (last["phase1"], last["delayed1"]), "heard: from the unheard state")
        self.lines("heard again", calls, o1, {r.first + b: (0 if b == 0 else 700, 700) for b in range(4)})
        self.assertEqual(stats[FM_FM // 4], 4, "fm=: OSC1's renders (OSC2 has none)")

    def test_a_silent_osc1_leaves_the_sub_to_osc2(self):
        """E3 (c): OSC1 at Level 0, no sync, Sub FM on OSC2 only: OSC1 takes its skip path (no fill, no render), OSC2 fills A
        itself and renders under FM; and OSC1 with Sub FM too (it still skips: nobody hears it, nothing syncs to it).
        BE4.43 (expected change): OSC2's Sub FM plays only while OSC1 is not Analog, and here OSC1 is Analog (silent): OSC1
        skips, OSC2 renders plain (an_render), nothing fills A, fm= 0. OSC2 filling A itself is the mixed voice's
        (test_a_mixed_voice: OSC1 Wavetable)."""
        for fm in ((0, 1000), (1000, 1000)):
            with self.subTest(fm=fm):
                calls, _, stats, _, (r, v, sub) = self.play(fm=fm, levels=lambda b: (0.0, 1.0), blocks=4)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                self.assertEqual(list(calls), [], "no FM render (BE4.43)")
                self.assertEqual(calls.fills, [], "nothing fills A")
                self.assertEqual(sorted(blk - r.first for o, blk in calls.plain if o == o2), list(range(4)), "OSC2: an_render")
                self.assertEqual([o for o, _ in calls.plain if o == o1], [], "OSC1: its skip path")
                self.assertEqual(stats[FM_FM // 4], 0)

    def test_a_mixed_voice(self):
        """M2 / M6, 4.5: OSC1 Wavetable (stock's, no WAV), OSC2 Analog with Sub FM: free, and synced to the Wavetable OSC1
        (mixed sync: an_master's events): OSC2 fills A and renders under FM (SLAVE with Sync), every call the golden's.
        BE4.44 W4 (reviewed change, D2's last case): OSC2's Sub FM 1000 plays nothing next to a Wavetable OSC1 (no fill, no
        FM render: the case above, now silent of FM); its FM 1>2 does, from the Wavetable OSC1's B (here given a WAV), free and
        as the slave of mixed sync: every call the golden's with the master's events, B the Wavetable OSC1's."""
        for sync in (0, 1):
            with self.subTest(sync=sync):
                calls, _, stats, _, (r, v, sub) = self.play(fm=(0, 1000), types=(0, 1), sync=sync, shapes=(48, 88))
                self.assertEqual((list(calls), calls.fills), ([], []), "W4: OSC2's Sub FM plays nothing next to a Wavetable OSC1")
                calls, _, stats, _, (r, v, sub) = Fm12.play(self, fm=(0, 1000), x12=1000, types=(0, 1), sync=sync,
                                                            shapes=(48, 88), each=wt_osc1_wav)
                self.replay(f"mixed sync={sync}", calls)
                o2 = r.osc(v, 1)
                self.assertTrue(calls and all(x["osc"] == o2 and x["role"] == (2 if sync else 0) for x in calls))
                self.assertEqual(calls.fills, [], "nothing fills A")
                self.assertEqual(check_wt_feeds(self, f"mixed sync={sync}", calls, r.osc(v, 0), o2), 6)
                if sync:
                    self.assertGreater(sum(e != 255 for x in calls for e in x["ev0"]), 0, "control: the master's wraps")
                self.assertEqual(stats[FM_FM // 4], 6)

    def test_the_square_sub_keeps_the_pitch(self):
        """6.3 / OD10: Sub FM 100 % (index 16 since BE4.42) by a Square sub at PW 30 %, 16 blocks of one note: over the window's whole sub
        cycles the carrier's travel sum(di) is the unmodulated inc x L within one bin of the L-sample window (one cycle),
        where the raw Square's DC (1 - 2 pw = 0.4) would add I inc_sub 0.4 L."""
        calls, _, _, _, (r, v, sub) = self.play(wave=3, fm=(1000, 0), blocks=16, pw=300)
        o1 = r.osc(v, 0)
        xs = [x for x in calls if x["osc"] == o1 and x["block"] > r.first]  # (block 0 ramps the index in)
        a = np.concatenate([x["a"] for x in xs]); inc = xs[0]["inc"]
        di = np.concatenate([fm_golden(x["inc"], x["k"], x["a"], x["ia"], x["dia"], x["off"], x["n"], x["phase0"],
                                       x["delayed0"], 0)[1] for x in xs])
        inc_sub = float(np.frombuffer(struct.pack("<I", r.r32(SA + SA_RUN + RUN_INC_SUB)), dtype="<f4")[0])
        y = a / inc_sub                                              # y - dc: the Square about its own mean
        starts = [i for i in range(1, len(y)) if y[i] < y[i - 1] - 1.0]   # the wrap's fall from the high
        self.assertGreater(len(starts), 4, "control: whole sub cycles in the window")
        t0, t1 = starts[0], starts[-1]
        L = t1 - t0
        travel = float(np.sum(di[t0:t1]))
        raw = an_index(1000) * inc_sub * 0.4 * L
        print(f"\n  Square sub PW 30 %, index {an_index(1000):g}: travel {travel:.4f} vs inc L {inc * L:.4f} over {L} samples "
              f"({len(starts) - 1} sub cycles); the raw Square would add {raw:.2f} cycles", file=sys.stderr)
        self.assertLess(abs(travel - inc * L), 1.0, "within one bin")
        self.assertGreater(raw, 10.0, "control: the raw Square's DC would be seen")

    def test_the_amounts_cases_are_the_expected_change(self):
        """P3 Q5's expected change (6.7: Q1's test_q1_plays_no_amount, plan 7): its seven cases (free, sync, silent sync
        master, glide, OSC2 Wavetable, stock's root voice, mixed sync), Rig() and Rig(dev=True), with Sub FM 700 and FM 1>2
        333 now play Sub FM: every an_render_fm call is the golden's (Rig's own voices have a zero sub, so di = inc there:
        the FM loop at rest; stock's root has its sub); fm= = the renders with FM. P3 Q6 (reviewed edit): FM 1>2 333 plays
        too where both are Analog (OSC2 on two terms: replay feeds B), and B is OSC1's (check_feeds). BE4.43 (expected
        change): there OSC2's Sub FM 700 plays nothing (one amount per oscillator: FM 1>2), so OSC2 is on one term, B.
        BE4.44 W4 (reviewed change): in "mixed" too (a Wavetable OSC1 feeds B: check_wt_feeds)."""
        p3 = (700, 0, 333)
        me = P3Identity()
        cases = (("free", lambda r: me.voice_case(r, shape=(48, 88), p3=p3)),
                 ("sync", lambda r: me.voice_case(r, shape=(70, 100), sync=1, p3=p3)),
                 ("sync silent master", lambda r: me.voice_case(r, shape=(48, 88), sync=1, level0=0.0, p3=p3)),
                 ("glide", lambda r: me.voice_case(r, **P3Identity.GLIDE, p3=p3)),
                 ("osc2 wavetable", lambda r: me.voice_case(r, shape=(64, 48), types=(1, 0), p3=p3)),
                 ("root voice", lambda r: me.root_case(r, p3=p3)),
                 ("mixed", lambda r: me.mixed_case(r, 48, p3=p3)))
        for dev in (False, True):
            for name, run in cases:
                with self.subTest(dev=dev, case=name):
                    r = Rig(dev=dev); calls = fm_capture(r)
                    run(r)
                    self.assertTrue(all("y" in x for x in calls))
                    self.assertGreater(len(calls), 0, f"{name}: Sub FM plays")
                    self.replay(name, calls)
                    st = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
                    self.assertEqual(st[FM_FM // 4], self.renders(calls), f"{name}: fm= = the renders with FM")
                    if name not in ("osc2 wavetable", "mixed"):  # (P3 Q6: both Analog, FM 1>2 333 from OSC1's B)
                        o2 = next(x["osc"] for x in calls if x["A"] == SA + SA_FMBUF_B)
                        self.assertTrue(all((x["ib"], x["dib"]) == (0.0, 0.0) for x in calls), "one term (BE4.43)")
                        self.assertTrue(all(x["A"] == SA + SA_FMBUF_B for x in calls if x["osc"] == o2), "OSC2: B alone")
                        self.assertGreater(check_feeds(self, name, calls, o2 - S.VOICE_WT2 + S.VOICE_WT1, o2, n=r.frames), 0)
                    elif name == "mixed":                       # BE4.44 W4 (reviewed change): the Wavetable OSC1's B
                        o2 = r.osc(r.v, 1)
                        self.assertTrue(all(x["osc"] == o2 and x["A"] == x["B"] == SA + SA_FMBUF_B for x in calls),
                                        f"{name}: OSC2 on FM 1>2 alone (its Sub FM 700 plays nothing: D2)")
                        self.assertGreater(check_wt_feeds(self, name, calls, r.osc(r.v, 0), o2, n=r.frames), 0)
                    else:                                       # BE4.44 W3 (reviewed change): B for the Wavetable OSC2's FM 1>2
                        self.assertGreater(check_feeds(self, name, calls, r.osc(r.v, 0), r.osc(r.v, 1), n=r.frames), 0)

    def test_cost(self):
        """5 / 6.5: hooks per voice-block (an_enter + an_osc with their stock helpers: Span; median of 6) on the device's
        path, note 60, both oscillators at the saw: Sub FM off; Sub FM 100 % on both by a sine sub (the copy and two FM
        renders); the same synced. Gate (FD6 restated, plan 5): the Sub FM voice at most 1.3 x (the off voice + the
        specialised delta at 2N), the delta measured on the rc26 prototype (scratch p3/q5/proto_cost.py, M): 2 x 2,600 (one
        Sub FM saw oscillator through zero, 23.43 against 13.27 instructions a sample) + 4,398 (the sine copy at -O2) +
        sub_inc's stock part (measured here: sub_fill minus an_sub_copy). The gate is the saw voice's (plan 5); the
        triangle carrier (Shape 24 on both: LOW's FM loop) is measured and printed against it, not gated (review: plan 9)."""
        SPEC_OSC, SPEC_COPY = 2600, 4398
        rows = {}
        for name, fm, sync, shape in (("off", (0, 0), 0, None), ("sub fm", (1000, 1000), 0, None),
                                      ("sub fm+sync", (1000, 1000), 1, None), ("sub fm tri", (1000, 1000), 0, 24)):
            r = Rig()
            root, v, sub, src = self.voice(r, 4, 1000, fm=fm, glide=0, fine=0, sync=sync)
            for i in ((0, 1) if shape is not None else ()):
                r.root_prop(root, K_SHAPE, shape, i)
            sp = [Span(r, DSP["an_enter"]), Span(r, DSP["an_osc"])]
            fill, copy = Span(r, dsp_sym("sub_fill")), Span(r, DSP["an_sub_copy"])
            m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
            slot18 = r.r32(r.r32(root) + 0x48)
            self.note(r, v, sub, 60)
            per = []
            for b in range(6):
                acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
                t0 = sum(s.total for s in sp)
                r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out)); r.block()
                per.append(sum(s.total for s in sp) - t0)
            rows[name] = (sorted(per)[3], (fill.total - copy.total) / max(1, fill.calls), fill.total / max(1, fill.calls))
        stock = rows["sub fm"][1]
        gate = 1.3 * (rows["off"][0] + 2 * SPEC_OSC + SPEC_COPY + stock)
        print(f"\n  Sub FM cost (hooks per voice-block, 2N, note 60, saw): off {rows['off'][0]}, Sub FM on both {rows['sub fm'][0]} "
              f"({rows['sub fm'][0] / rows['off'][0]:.2f} x; sub_fill {rows['sub fm'][2]:.0f}), synced off -, on "
              f"{rows['sub fm+sync'][0]}; sub_inc's stock part {stock:.0f}; gate 1.3 x ({rows['off'][0]} + "
              f"{2 * SPEC_OSC + SPEC_COPY + stock:.0f}) = {gate:.0f}; the triangle carrier {rows['sub fm tri'][0]} "
              f"({rows['sub fm tri'][0] / gate:.3f} of the saw's gate, not gated)", file=sys.stderr)
        self.assertLessEqual(rows["sub fm"][0], gate, "the cost gate")

# ---- P3 Q6: FM 1>2 (docs/plan-p3-fm-bits.md 4.1, 4.4 E3 (a), 4.5, 4.7 BD1, 4.8, 6.3 - 6.6) ----------------------------------
FM_MISSED = 12              # SaFmStats.fm_missed (byte offset): OSC2 wanted B, not written this voice-block (fmx= .../missed)
FM_R1 = 4                   # SaFmStats word +4: BE4.41's bits= count; BE4.42 leaves it 0


def u32s(x): return np.asarray(x, dtype=np.float32).view(np.uint32)


def check_feeds(tc, name, calls, osc1, osc2, n=DEV_FRAMES):
    """P3 Q6 (4.1, BD1): buffer B as OSC1 wrote it, every block it fed (fm_capture's .feeds, an_feed's calls), against
    p3_golden.b_buffer (written from the plan, before this code) bit for bit: only OSC1 feeds; its parts tile B in order,
    each part at the address of its own render's output (s at an_feed's entry is that render's: for an FM render its
    samples bit for bit, captured on its return before anything else touches them); inc = that render's base increment
    (kin = I inc1: not OSC1's FM'd di); B = f32(f32(inc) s) (BE4.42: no Bit Depth grid). And OSC2's FM calls in that block
    read exactly those B samples (FmIn.a for FM 1>2 alone, .b next to Sub FM). Returns the number of blocks fed."""
    B0 = SA + SA_FMBUF_B
    by = {}
    for x in list(calls) + calls.plain_calls:
        by.setdefault((x["osc"], x["block"]), []).append(x)
    blocks = sorted({f["block"] for f in calls.feeds})
    for blk in blocks:
        fs = [f for f in calls.feeds if f["block"] == blk]
        tc.assertTrue(all(f["osc"] == osc1 for f in fs), f"{name}: block {blk}: only OSC1 feeds B")
        tc.assertTrue(all("b" in f for f in fs), f"{name}: every an_feed call returned")
        offs = [(f["out"] - B0) // 4 for f in fs]
        tc.assertEqual(offs, [sum(f["n"] for f in fs[:m]) for m in range(len(fs))], f"{name}: block {blk}: B's parts in order")
        tc.assertEqual(sum(f["n"] for f in fs), n, f"{name}: block {blk}: B written end to end")
        for f, off in zip(fs, offs):
            rr = [x for x in by.get((osc1, blk), []) if x["out"] == f["sp"]]
            tc.assertEqual(len(rr), 1, f"{name}: block {blk} off {off}: the part's own render")
            x = rr[0]
            tc.assertEqual(x["n"], f["n"])
            tc.assertEqual(f32(f["inc"]), f32(x["inc"]), f"{name}: inc1, OSC1's base increment")
            if "y" in x:
                tc.assertTrue(np.array_equal(u32s(f["s"]), u32s(x["y"])), f"{name}: block {blk} off {off}: s is the FM render")
            want = u32s(G.b_buffer(f["s"].astype(np.float64), f["inc"]))
            got = u32s(f["b"])
            if not np.array_equal(got, want):
                i = int(np.flatnonzero(got != want)[0])
                tc.fail(f"{name}: block {blk} off {off}: B[{i}] {f['b'][i]!r} vs golden {want.view(np.float32)[i]!r} "
                        f"(s {f['s'][i]!r}, inc {f['inc']!r})")
        Bk = np.concatenate([f["b"] for f in fs]).astype(np.float64)
        for x in by.get((osc2, blk), []):
            if "A" not in x:
                continue                                         # (an_render: no FM)
            two = (x["ib"], x["dib"]) != (0.0, 0.0)
            got = x["b"] if two else x["a"] if x["A"] == B0 else None
            if got is not None:
                tc.assertTrue(np.array_equal(got, Bk[x["off"]:x["off"] + x["n"]]), f"{name}: block {blk}: OSC2 reads OSC1's B")
    return len(blocks)


def wt_osc1_wav(r, root, b):
    """(Fm12.play's each) BE4.44 W4: OSC1 (Wavetable in the case) given Rig.tabled's table before block 0, so it feeds B."""
    if b == 0:
        r.tabled(r.osc(r.v, 0))


def check_wt_feeds(tc, name, calls, osc1, osc2, n=DEV_FRAMES):
    """BE4.44 W4 (plan 3.5): buffer B from a Wavetable OSC1, every block it fed: only OSC1 feeds, one wt_feed_b call a block
    from wt_body, B end to end; B = f32(f32(s - m) inc) bit for bit (an_feed's form, p3_golden.b_buffer, with D4's frame mean m
    taken out: WtFmW4 pins m to the frame's), s the block it read (OSC1's samples before Level: WtFmW4 pins s to stock's
    kernel and to the reader), inc OSC1's base increment; and OSC2's FM calls in that block read exactly those B samples.
    Returns the number of blocks fed."""
    B0 = SA + SA_FMBUF_B
    blocks = sorted({f["block"] for f in calls.feeds})
    lo, hi = sym_range("wt_body")
    for blk in blocks:
        fs = [f for f in calls.feeds if f["block"] == blk]
        tc.assertEqual([(f["osc"], f["out"], f["n"]) for f in fs], [(osc1, B0, n)], f"{name}: block {blk}: OSC1 feeds B once")
        f = fs[0]
        tc.assertTrue(lo <= f["lr"] < hi, f"{name}: block {blk}: from wt_body")
        tc.assertIn("m", f, f"{name}: block {blk}: B from wt_feed_b")
        want = u32s((f["s"].astype(np.float32) - np.float32(f["m"])) * np.float32(f["inc"]))
        tc.assertTrue(np.array_equal(u32s(f["b"]), want), f"{name}: block {blk}: B = f32(f32(s - m) inc)")
        for x in calls:
            if x["osc"] == osc2 and x["block"] == blk and "A" in x:
                got = x["b"] if (x["ib"], x["dib"]) != (0.0, 0.0) else x["a"] if x["A"] == B0 else None
                if got is not None:
                    tc.assertTrue(np.array_equal(got, f["b"][x["off"]:x["off"] + x["n"]].astype(np.float64)),
                                  f"{name}: block {blk}: OSC2 reads OSC1's B")
    return len(blocks)


class KBench:
    """P3 Q6: OSC1 -> B -> OSC2 on the kernel alone (an_render, an_feed, an_render_fm called directly on one Rig; no hooks
    but the Rig's own counter), buffers of `size` floats: the long and coherent renders 6.3's spectral and drift checks need."""
    def __init__(self, size):
        self.r = r = Rig(); self.size = size
        self.s1, self.s2 = r.alloc(176), r.alloc(176)
        self.y1, self.b, self.a, self.y2 = (r.alloc(4 * size) for _ in range(4))
        self.fi, self.ev = r.alloc(28), r.alloc(max(size, 1024))
        r.u.mem_write(self.ev, bytes([255]) * max(size, 1024))

    def state(self, st, phase, delayed):
        self.r.u.mem_write(st, struct.pack("<Iff", AN_MAGIC, phase, delayed))

    def get(self, p, n):
        return np.frombuffer(bytes(self.r.u.mem_read(p, 4 * n)), dtype="<f4").astype(np.float64)

    def osc1(self, inc, k, n, fm=None):
        """OSC1's render into y1 (an_render, or an_render_fm on A = fm (a, I))."""
        if fm is None:
            self.r.call(DSP["an_render"], self.s1, self.y1, n, self.ev, sargs=(0,), fargs=(inc, k))
        else:
            self.r.u.mem_write(self.a, np.asarray(fm[0], dtype="<f4").tobytes())
            self.r.u.mem_write(self.fi, struct.pack("<IffIIff", self.a, fm[1], 0.0, 0, self.a, 0.0, 0.0))
            self.r.call(DSP["an_render_fm"], self.s1, self.y1, n, self.ev, sargs=(0, self.fi), fargs=(inc, k))
        return self.get(self.y1, n)

    def feed(self, n, inc):
        self.r.call(DSP["an_feed"], self.y1, self.b, n, fargs=(inc,))
        return self.get(self.b, n)

    def osc2(self, inc, k, n, I, sub=None, I_s=0.0):
        """OSC2 under FM 1>2 (B at the constant index I) and, with sub (A), Sub FM at I_s first (an_osc's two-route form)."""
        if sub is None:
            self.r.u.mem_write(self.fi, struct.pack("<IffIIff", self.b, I, 0.0, 0, self.b, 0.0, 0.0))
        else:
            self.r.u.mem_write(self.a, np.asarray(sub, dtype="<f4").tobytes())
            self.r.u.mem_write(self.fi, struct.pack("<IffIIff", self.a, I_s, 0.0, 0, self.b, I, 0.0))
        self.r.call(DSP["an_render_fm"], self.s2, self.y2, n, self.ev, sargs=(0, self.fi), fargs=(inc, k))
        return self.get(self.y2, n), struct.unpack("<ff", self.r.u.mem_read(self.s2 + 4, 8))


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Fm12(unittest.TestCase):
    """P3 Q6 (plan 4.1, 4.4 E3 (a), 4.5, 4.7 BD1, 4.8, OD5, 6.3 - 6.6): FM 1>2, OSC1 -> OSC2. OSC1 (Analog) writes its block
    before Level into buffer B (an_feed: times inc1, its base increment; BE4.42: no Bit Depth grid), also when silent
    (E3 (a)); OSC2 adds I12 B to its increment (FmIn's second term after Sub FM's A, 4.3's order), its index ramped
    from m12. Checked against p3_golden.py (written from the plan alone, before and without this code: b_buffer, fm_render
    through fm_golden, the Bessel, drift and aliasing checks) on the device's path (SubFm's harness: stock's root, 2N, stock's
    sub) and on the kernel alone (KBench)."""
    voice, note = SubCopy.voice, SubCopy.note
    lines, replay, renders = SubFm.lines, SubFm.replay, staticmethod(SubFm.renders)

    def play(self, wave=4, fm=(0, 0), x12=1000, shapes=(48, 48), sync=0, types=(1, 1), levels=None, blocks=6, amounts=None,
             amounts12=None, pw=300, note=48, taps=False, each=None, notes=(), env=None):
        """SubFm.play with FM 1>2: x12 on OSC2 (index 1) through stock's root, amounts12(b) -> FM 1>2 before block b.
        BE4.43: each(r, root, b) before block b's pushes; notes: the blocks after 0 where the note starts again (stock's
        voice note start: WT1, WT2 and the sub's slot 2); env: {osc: (FM Atk, FM Dec)} pushed through the root."""
        r = Rig()
        root, v, sub, src = self.voice(r, wave, 1000, fm=fm, types=types, glide=0, pw=pw, fine=0, sync=sync)
        for i in (0, 1):
            if types[i]:
                r.root_prop(root, K_SHAPE, shapes[i], i)
        r.root_prop(root, K_FM12, x12, 1)
        for i, (a, d) in (env or {}).items():
            r.root_prop(root, K_FMATK, a, i); r.root_prop(root, K_FMDEC, d, i)
        calls = fm_capture(r)
        r.v = v
        tp = level_taps(r) if taps else None
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
        slot18 = r.r32(r.r32(root) + 0x48)
        self.note(r, v, sub, note)
        outs = b""
        r.first = r.idx
        for b in range(blocks):
            if b in notes:
                self.note(r, v, sub, note)
            if each:
                each(r, root, b)
            for i, x in (amounts(b) if amounts else {}).items():
                r.root_prop(root, K_SUBFM, x, i)
            if amounts12:
                r.root_prop(root, K_FM12, amounts12(b), 1)
            for i, l in enumerate(levels(b) if levels else (1.0, 1.0)):
                r.level(v, i, l)
            r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
            r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
            outs += bytes(r.u.mem_read(od, 4 * DEV_N))
            r.block()
        self.assertTrue(all("y" in x for x in calls), f"every an_render_fm call returned through an_osc's sites {calls.rets}")
        stats = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
        return calls, outs, stats, tp, (r, v, sub)

    def blines(self, name, calls, osc, want, n=DEV_FRAMES):
        """FM 1>2's line next to Sub FM: FmIn's (ib, dib) by block, as SubFm.lines checks (ia, dia)."""
        alt = FmCalls(dict(x, ia=x["ib"], dia=x["dib"]) for x in calls if (x["ib"], x["dib"]) != (0.0, 0.0))
        self.lines(name, alt, osc, want, n)

    def test_an_feed_is_the_golden_buffer(self):
        """an_feed on the M7 (three incs) on a sweep over -1.3..1.3, 16,384 random samples and the specials (-0.0, +-1, past
        +-1, the smallest normal and a denormal): B = p3_golden.b_buffer bit for bit (f32(f32(inc) s): BD1, BE4.42 without a
        Bit Depth grid). Each of the golden's B mutants (after a Level of 0.5, one sample late) differs on the random block."""
        rng = np.random.default_rng(6612)
        r = Rig(); p, q = r.alloc(4 * 20001), r.alloc(4 * 20001)
        incs = (f32(261.6256 / DEV_RATE), f32(0.0123), f32(0.2))
        def run(x, inc):
            x = np.asarray(x, dtype=np.float32)
            r.u.mem_write(p, x.tobytes())
            r.call(DSP["an_feed"], p, q, len(x), fargs=(inc,))
            return np.frombuffer(bytes(r.u.mem_read(q, 4 * len(x))), dtype="<f4").copy()
        special = np.array([-0.0, 0.0, 1.0, -1.0, 1.06, -1.06, 2.0 ** -126, 2.0 ** -140, 0.999999], dtype=np.float32)
        for inc in incs:
            for name, x in (("sweep", np.linspace(-1.3, 1.3, 20001)), ("random", rng.standard_normal(16384) * 0.7),
                            ("special", special)):
                with self.subTest(inc=inc, case=name):
                    x = np.asarray(x, dtype=np.float32).astype(np.float64)
                    got = run(x, inc)
                    want = u32s(G.b_buffer(x, inc))
                    self.assertTrue(np.array_equal(got.view(np.uint32), want),
                                    f"{int(np.sum(got.view(np.uint32) != want))} samples differ")
                    if name == "random":
                        base = G.b_buffer(x, inc)
                        for mu in ("after_level", "b_late"):
                            self.assertTrue(np.any(G.b_buffer(x, inc, level=0.5, mutant=mu) != base), mu)

    def test_fm12_alone(self):
        """4.1: FM 1>2 100 % (index 16 since BE4.42) on OSC2, no Sub FM anywhere, OSC1 free at the saw / the sine (Shapes (48, 127), (0, 24)),
        the same pitch on both (ratio 1: through zero): only OSC2 renders under FM (free, a = B, one term), every call the
        golden's; its line from I(0) in the first block (m12 starts at 0), then held at 16; B is OSC1's every block
        (check_feeds); fm= 6, fmx= 0 / 0; the output not FM 1>2 0's, which feeds nothing and counts nothing."""
        for shapes in ((48, 127), (0, 24)):
            with self.subTest(shapes=shapes):
                calls, outs, stats, _, (r, v, sub) = self.play(shapes=shapes)
                w, dmin = self.replay(f"fm12 {shapes}", calls)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                self.assertLess(dmin, 0.0, "control: through zero")
                self.assertTrue(calls and all(x["osc"] == o2 and x["role"] == 0 and x["A"] == SA + SA_FMBUF_B for x in calls))
                self.lines("fm12", calls, o2, {r.first + b: (0 if b == 0 else 1000, 1000) for b in range(6)})
                self.assertEqual(check_feeds(self, f"fm12 {shapes}", calls, o1, o2), 6)
                self.assertEqual((stats[FM_FM // 4], stats[FM_SKIPPED // 4], stats[FM_MISSED // 4]), (6, 0, 0))
                self.assertEqual(calls.fills, [], "no Sub FM: A is not filled")
                c0, outs0, stats0, _, _ = self.play(shapes=shapes, x12=0)
                self.assertNotEqual(outs, outs0, "FM 1>2 changes the sound")
                self.assertEqual((stats0, c0.feeds, list(c0)), ((0,) * 8, [], []), "FM 1>2 0: no feed, no FM, nothing counted")
                print(f"\n  FM 1>2 alone {shapes}: worst {w:.2f} of the tolerance, lowest di {dmin:.4f}", file=sys.stderr)

    def test_both_routes(self):
        """4.1 / 4.3: Sub FM 100 % / 70 % (a sine sub) on OSC1 / OSC2 and FM 1>2 50 % on OSC2, OSC1 at the pulse (Shape 100)
        or the square (Shape 88), OSC2 at the sine: OSC2's calls have two terms (a = A on Sub FM's line, b = B on FM 1>2's),
        every call the golden's; B is OSC1's FM render (s its samples bit for bit) times inc1 (BE4.42: no Bit Depth grid:
        on the square the +1 high is inc1 in B); fm= both oscillators; word +4 (BE4.41's bits=) 0.
        BE4.43 (expected change, Jeff's design: one FM amount per oscillator): OSC2's Sub FM plays only while OSC1 is not
        Analog and its FM 1>2 only while it is, so both routes meet in one block, the switch: OSC1 Wavetable in blocks 0..2
        (OSC2 on Sub FM alone: a = A, filled by OSC2), then OSC1 turned Analog before block 3 (Type 1 through stock's root,
        then its Shape and Sub FM as the M4's resend sends them): in block 3 OSC2's Sub FM ramps out on a (msub 700 -> 0)
        while FM 1>2 ramps in on b (m12 0 -> 500): the two-term loop; from block 4 FM 1>2 alone (a = B). OSC1 fills A from
        block 3 and feeds B (check_feeds: 3 blocks). Every call the golden's.
        BE4.44 W4 (reviewed change, D2's last case): OSC2's Sub FM plays nothing next to a Wavetable OSC1 now (FM 1>2 is its
        amount next to either; this OSC1 has no WAV, so blocks 0..2 have no B: fmx= missed 3, no line, nothing fills A), and
        no real sequence reaches two terms any more. The two-term loop stays checked on a crafted state: OSC2's msub forced
        to 700 before block 3 (BE4.43's ramp memory), so block 3 is the switch block it was."""
        for shapes in ((100, 0), (88, 0)):
            with self.subTest(shapes=shapes):
                def each(r, root, b, shapes=shapes):
                    if b == 3:
                        r.root_prop(root, K_TYPE, 1, 0); r.root_prop(root, K_SHAPE, shapes[0], 0)
                        r.root_prop(root, K_SUBFM, 1000, 0)
                        r.u.mem_write(r.osc(r.v, 1) + S.OSC_KERNEL + MSUB_AT, struct.pack("<H", 700))   # (crafted)
                calls, outs, stats, _, (r, v, sub) = self.play(fm=(1000, 700), x12=500, shapes=shapes, types=(0, 1),
                                                               each=each)
                self.replay(f"both {shapes}", calls)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                A, B = SA + SA_FMBUF_A, SA + SA_FMBUF_B
                c2 = {b: [x for x in calls if x["osc"] == o2 and x["block"] == r.first + b] for b in range(6)}
                self.assertTrue(all(not c2[b] for b in (0, 1, 2)), "W4: no line in 0..2 (no B; Sub FM plays nothing)")
                self.assertTrue(c2[3] and all((x["A"], x["B"]) == (A, B) for x in c2[3]), "block 3: two terms")
                self.assertTrue(all(c2[b] and all((x["A"], x["B"]) == (B, B) for x in c2[b]) for b in (4, 5)), "FM 1>2 alone")
                self.lines("both: a", calls, o2, {r.first + b: ab for b, ab in ((3, (700, 0)), (4, (500, 500)), (5, (500, 500)))})
                self.blines("both: b", calls, o2, {r.first + 3: (0, 500)})
                self.assertEqual(calls.fills, [(o1, r.first + b) for b in (3, 4, 5)])
                self.assertEqual(check_feeds(self, f"both {shapes}", calls, o1, o2), 3)
                self.assertEqual((stats[FM_FM // 4], stats[FM_R1 // 4], stats[FM_MISSED // 4]), (6, 0, 3))

    def test_the_fm12_index_ramps_over_the_block(self):
        """4.4 / 6.4: OSC2's FM 1>2 0, 1000, 1000, 500, 0, 0, 250, 1000 by block, with Sub FM 0 on OSC2 (B alone: FmIn.a's
        line) and with Sub FM 100 % held on OSC2 (FmIn.b's line): each block's line runs from I(m12, last block's) to
        I(this block's) (FD3's law), the ramp down (block 4) still FM; the block after it (5) has no FM 1>2: OSC1 feeds
        nothing, and OSC2 alone renders plainly (an_render) or on Sub FM's single term; OSC1 feeds exactly the blocks with FM
        1>2 or its ramp; m12 ends on the last amount. 6.5: the hook's own VDIVs (an_osc and fm_setup) per OSC2 block are the
        off count plus one per index line (one with FM 1>2 alone, two next to Sub FM). BE4.43 (expected change): OSC2's Sub
        FM 100 % plays nothing next to the Analog OSC1, so that case is the Sub FM 0 case, call for call and sample for sample
        (the second term and its VDIV are gone)."""
        amts = (0, 1000, 1000, 500, 0, 0, 250, 1000)
        seen = []
        for sub in (0, 1000):
            with self.subTest(osc2_sub_fm=sub):
                calls, outs, stats, _, (r, v, _) = self.play(fm=(0, sub), x12=0, blocks=len(amts), amounts12=lambda b: amts[b])
                self.replay("ramp12", calls)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                want = {r.first + b: (amts[b - 1] if b else 0, amts[b]) for b in range(len(amts)) if amts[b] or (b and amts[b - 1])}
                self.lines("ramp12", calls, o2, want)
                self.assertTrue(all((x["ib"], x["dib"]) == (0.0, 0.0) for x in calls), "one term (BE4.43)")
                plain = sorted({x["block"] for x in calls.plain_calls if x["osc"] == o2})
                self.assertEqual(plain, [r.first + b for b in range(len(amts)) if r.first + b not in want])
                seen.append((outs, [(x["block"] - r.first, x["off"], x["y"].tobytes()) for x in calls]))
                self.assertEqual(sorted({f["block"] for f in calls.feeds}), sorted(want), "OSC1 feeds the FM 1>2 blocks only")
                check_feeds(self, "ramp12", calls, o1, o2)
                self.assertEqual(struct.unpack("<H", r.u.mem_read(o2 + S.OSC_KERNEL + MSUB_AT + 2, 2))[0], 1000, "m12")
                dv = {b: calls.osc_divs.get((o2, r.first + b), 0) for b in range(len(amts))}
                base = dv[0]                                     # (block 0: FM 1>2 0 -> 0, no FM: the off count)
                self.assertGreater(base, 0, "control: the hook divides (rate_of)")
                self.assertEqual(dv, {b: base + (r.first + b in want) for b in dv},
                                 f"the hook's VDIVs (an_osc, fm_setup) per block: the off count + 1 a line ({dv})")
        self.assertEqual(seen[0], seen[1], "BE4.43: OSC2's Sub FM 100 % next to the Analog OSC1 plays as 0")

    def test_sync_master_and_slave(self):
        """M6 / OD5: OSC1 the MASTER with its own Sub FM, OSC2 the SLAVE with Sub FM and FM 1>2 (through zero), Shapes (24,
        100) and (48, 12): every call the golden's, the slave's bytes the master's; B the master's FM render; both
        oscillators in two chunks (off 0, 128) on their blocks' lines. BE4.43 (expected change): the slave's Sub FM plays
        nothing next to the Analog OSC1, so it is on FM 1>2 alone (a = B, one term)."""
        for shapes in ((24, 100), (48, 12)):
            with self.subTest(shapes=shapes):
                calls, _, stats, _, (r, v, sub) = self.play(fm=(1000, 1000), x12=1000, shapes=shapes, sync=1)
                self.replay(f"sync {shapes}", calls)
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                for blk in sorted({x["block"] for x in calls}):
                    m = b"".join(x["ev1"] for x in calls if x["osc"] == o1 and x["block"] == blk)
                    s = b"".join(x["ev0"] for x in calls if x["osc"] == o2 and x["block"] == blk)
                    self.assertEqual((len(m), s), (DEV_FRAMES, m), f"block {blk}: the slave's events are the master's bytes")
                self.assertEqual(check_feeds(self, f"sync {shapes}", calls, o1, o2), 6)
                for o in (o1, o2):
                    self.lines(f"sync {shapes}", calls, o, {r.first + b: (0 if b == 0 else 1000, 1000) for b in range(6)})
                    self.assertEqual({x["off"] for x in calls if x["osc"] == o}, {0, AN_EVENTS_N}, "control: chunked")
                self.assertTrue(all((x["A"], x["ib"], x["dib"]) == (SA + SA_FMBUF_B, 0.0, 0.0) for x in calls if x["osc"] == o2),
                                "the slave: FM 1>2 alone (BE4.43)")

    def test_level_input_is_the_fm_render(self):
        """BE4.42 (review: BE4.41's test_bit_depth_crushes_the_fm_render without the crush): with nothing between the FM render
        and Level any more, each block at Level's input (level_taps) is the concatenation, in off order, of that oscillator's
        an_render_fm calls' own y in that block, bit for bit, for both oscillators: Sub FM on both and FM 1>2, saw and a
        pulse (Shape 110), free and synced; every call the golden's; fm= counts every render."""
        for sync in (0, 1):
            with self.subTest(sync=sync):
                calls, _, stats, tp, (r, v, sub) = self.play(fm=(1000, 1000), x12=1000, shapes=(48, 110), sync=sync,
                                                             taps=True)
                self.replay(f"level input sync={sync}", calls)
                self.assertEqual(len(tp), 12)
                self.assertEqual(sorted(tp.osc), [0] * 6 + [1] * 6, "both oscillators at Level each block")
                seen = {0: 0, 1: 0}
                for x, i in zip(tp, tp.osc):
                    blk = r.first + seen[i]; seen[i] += 1
                    parts = sorted((c for c in calls if c["osc"] == r.osc(v, i) and c["block"] == blk),
                                   key=lambda c: c["off"])
                    self.assertTrue(parts, f"OSC{i + 1} block {blk}: FM renders")
                    y = np.concatenate([c["y"] for c in parts])
                    self.assertEqual(x.tobytes(), np.asarray(y, dtype="<f4").tobytes(),
                                     f"OSC{i + 1} block {blk}: Level's input is the FM render")
                self.assertEqual(stats[FM_FM // 4], len(tp), "fm= every FM render (a block's parts are one render)")

    def test_a_silent_osc1_still_modulates(self):
        """E3 (a) (4.4, BD1): OSC1 at Level 0 with FM 1>2 on OSC2 (free, and synced: a silent master): OSC2's FM calls and
        its Level-input blocks are those of the same case with OSC1 heard, bit for bit, and so is B; OSC1 renders (it feeds
        every block; synced, as the MASTER, its wraps the slave's) but reaches no Level. Then FM 1>2 1000, 1000, 0, 0, 0 with OSC1 silent: OSC1 feeds through the ramp down (block
        2), then takes its silent path again (free: no render; synced: an_track). Q6 review: also with OSC1's own Sub FM 100 %
        (the feeding OSC1 renders under FM, FREE or MASTER, through the normal parts, not E3 (b)): its FM calls the golden's
        and those of the heard case, bit for bit (the ramp part with FM 1>2 alone)."""
        for sync, fm in ((0, (0, 0)), (1, (0, 0)), (0, (1000, 0)), (1, (1000, 0))):
            with self.subTest(sync=sync, fm=fm):
                runs = {}
                for lv in (0.0, 1.0):
                    runs[lv] = self.play(sync=sync, fm=fm, shapes=(100, 48), levels=lambda b, lv=lv: (lv, 1.0),
                                         taps=True)
                (cs, _, ss, ts, (r, v, _)), (ch, _, sh, th, _) = runs[0.0], runs[1.0]
                o1, o2 = r.osc(v, 0), r.osc(v, 1)
                self.replay(f"silent osc1 sync={sync}", cs)
                f2 = lambda c: [(x["block"], x["off"], x["y"].tobytes(), x["phase1"]) for x in c if x["osc"] == o2]
                self.assertTrue(f2(cs) and f2(cs) == f2(ch), "OSC2's FM renders: the same with OSC1 silent")
                self.assertEqual([f["b"].tobytes() for f in cs.feeds], [f["b"].tobytes() for f in ch.feeds], "B: the same")
                self.assertEqual(check_feeds(self, f"silent sync={sync}", cs, o1, o2), 6)
                lv2 = lambda t: [x.tobytes() for x, i in zip(t, t.osc) if i == 1]
                self.assertEqual(lv2(ts), lv2(th), "OSC2 at Level: the same")
                self.assertEqual([i for i in ts.osc], [1] * 6, "silent OSC1 reaches no Level")
                self.assertEqual((ss[FM_R1 // 4], sh[FM_R1 // 4]), (0, 0), "word +4 (BE4.41's bits=): 0")
                self.assertEqual((ss[FM_FM // 4], ss[FM_MISSED // 4]), (12 if fm[0] else 6, 0))
                self.assertEqual(ss[FM_FM // 4], sh[FM_FM // 4], "fm=: the same renders with OSC1 silent")
                f1 = lambda c: [(x["block"], x["off"], x["role"], x["y"].tobytes(), x["phase1"], x.get("ev1")) for x in c
                                if x["osc"] == o1]
                self.assertEqual(f1(cs), f1(ch), "OSC1's FM renders: the same unheard")
                self.assertEqual(len(f1(cs)), 12 if fm[0] and sync else 6 if fm[0] else 0, "OSC1 under FM: its parts")
                self.assertTrue(all(x["role"] == sync for x in cs if x["osc"] == o1), "FREE, or the MASTER synced")
                if sync:
                    for blk in sorted({x["block"] for x in cs}):
                        m = [p for p in (cs if fm[0] else cs.plain_calls) if p["osc"] == o1 and p["block"] == blk]
                        self.assertEqual(len(m), 2, "the silent master renders its two chunks (MASTER)")
                    self.assertEqual(cs.track, [], "not an_track while it feeds B")
                if fm[0]:
                    continue
                amts = (1000, 1000, 0, 0, 0)
                c, _, st, _, (r, v, _) = self.play(sync=sync, x12=1000, blocks=5, levels=lambda b: (0.0, 1.0),
                                                   amounts12=lambda b: amts[b])
                self.assertEqual(sorted({f["block"] - r.first for f in c.feeds}), [0, 1, 2], "feeds through the ramp down")
                o1 = r.osc(v, 0)
                rendered = sorted({p["block"] - r.first for p in c.plain_calls if p["osc"] == o1})
                self.assertEqual(rendered, [0, 1, 2], "OSC1 renders only while it feeds")
                self.assertEqual(sorted({blk - r.first for o, blk in c.track if o == o1}), [3, 4] if sync else [],
                                 "then its silent path (synced: an_track)")

    def test_the_two_term_loop_clamps(self):
        """4.3 / OD5 (Q6 review, a surviving mutant: the clamp moved above the second term): the two-term loop (Sub FM's A and
        FM 1>2's B: OSC2, FREE or SLAVE) clamps di after both terms. Crafted A and B (+-0.24 at I = 1, J's line 1 -> 1.5,
        signs independent) at incs 0.0123 (the sum past -0.45 and +0.45) and 0.2 (past +0.45), Shapes 48 / 100 / 24 (the
        loop's MIXED and LOW forms), FmBench's direct an_render_fm: every call the golden's (fm_check, the end phase exactly;
        VDIVs = its crossings). Controls: the golden's di reaches the clamp (both ways at 0.0123), and inc + I a alone never
        does (a clamp before + J b would leave the sum unclamped)."""
        n = 128; rng = np.random.default_rng(6614); fb = FmBench(); lim = float(f32(0.45))
        a = f32a(0.24) * rng.choice([-1.0, 1.0], n); bb = f32a(0.24) * rng.choice([-1.0, 1.0], n)
        ev = [255] * n
        for i in (17, 60, 101):
            ev[i] = 127
        ib, dib = 1.0, f32(0.5 / n)
        for inc in (f32(0.0123), f32(0.2)):
            for shape in (48, 100, 24):
                for role in (0, 2):
                    with self.subTest(inc=inc, shape=shape, role=role):
                        k = f32(k_of(shape)); p0 = f32(0.37); d0 = f32(G.FmWave(k, "f32", fm_width(k)).value(p0))
                        e = ev if role == 2 else None
                        fb.state(p0, d0)
                        y, ph, _, evo, _, dv = fb.call(inc, k, a, 1.0, 0.0, 0, n, role, e, b=bb, ib=ib, dib=dib)
                        g, di, _ = fm_golden(inc, k, a, 1.0, 0.0, 0, n, p0, d0, role, e, fm_width(k), b=bb, ib=ib, dib=dib)
                        self.assertTrue(np.all(np.abs(inc + a) < lim), "control: the first term alone stays inside")
                        self.assertEqual(float(np.max(di)), lim, "control: the sum reaches +0.45")
                        if inc < 0.1:
                            self.assertEqual(float(np.min(di)), -lim, "control: and -0.45")
                        fm_check(self, f"clamp {inc} {shape} {role}", g, y, ph, evo, k, role)
                        self.assertEqual(dv, edge_count(g), "VDIVs = the golden's crossings")

    def test_the_tag(self):
        """3.3 / 6.4: OSC2 never plays a stale or missing B. (1) Blocks longer than the buffers (n = 512): no feed, no FM call,
        fm_skipped 3 (OSC2 a block: OSC1 wants nothing itself), fm_missed 0, m12 0; the next 2N block ramps FM 1>2 in from
        I(0). (2) After a full voice-block (OSC1 fed B, tag B set), a new an_enter and OSC2 alone: the tag is clear, so OSC2
        plays without FM 1>2 (an_render), fm_missed 1, m12 0. (3) Voice 1's OSC2 rendered while Run.voice names voice 0
        (B is voice 0's): no FM, fm_missed 1. (4) Voice 1's OSC1 rendered between voice 0's OSC1 and OSC2: it feeds nothing
        (B is the named voice's), and voice 0's OSC2 reads its own OSC1's B (Q6 mutation: feeds without the voice check).
        (5) Q6 review: only a feed sets the tag, not a render: after an_enter(voice 0), voice 1's OSC1 renders (feeding
        nothing; BE4.42: with BE4.41's Bit Depth 4 pushed to it, ignored; BE4.43: that 0x1A4 is FM Atk 3, an envelope with no
        amount, which feeds nothing either), then voice 0's OSC2 alone: no FM 1>2 call, fmx=
        missed 1."""
        r = Rig(dev=True); root, v = r.voice(0x6E)
        for i in (0, 1):
            P3Identity.push(r, r.osc(v, i), 0x6E, i, (1, 48, 0, 0), (0, 0, 1000))
        r.note(v, 60)
        calls = fm_capture(r)
        o2 = r.osc(v, 1)
        for _ in range(3):
            r.render(root, v, 2 * DEV_FRAMES, (0, 1)); r.block()
        st = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
        self.assertEqual((len(calls), calls.feeds, st[FM_SKIPPED // 4], st[FM_MISSED // 4]), (0, [], 3, 0), "long blocks")
        self.assertEqual(struct.unpack("<HH", r.u.mem_read(o2 + S.OSC_KERNEL + MSUB_AT, 4)), (0, 0))
        blk = r.idx
        r.render(root, v, DEV_FRAMES, (0, 1)); r.block()
        self.lines("after the long blocks", calls, o2, {blk: (0, 1000)})
        self.assertEqual(len(calls.feeds), 1)
        # (2) the tag is cleared by an_enter
        n0 = len(calls); m0 = r.r32(SA + SA_FMSTATS + FM_MISSED)
        r.render(root, v, DEV_FRAMES, (1,)); r.block()
        self.assertEqual(len(calls), n0, "OSC2 alone after an_enter: no FM 1>2 from last block's B")
        self.assertEqual(r.r32(SA + SA_FMSTATS + FM_MISSED) - m0, 1, "fmx= missed")
        self.assertEqual(struct.unpack("<H", r.u.mem_read(o2 + S.OSC_KERNEL + MSUB_AT + 2, 2))[0], 0, "m12 0: ramps in again")
        # (3) another voice's OSC2 while Run.voice names this one
        root1, v1 = r.voice(0x6F)
        for i in (0, 1):
            P3Identity.push(r, r.osc(v1, i), 0x6F, i, (1, 48, 0, 0), (0, 0, 1000))
        r.note(v1, 64)
        r.render(root, v, DEV_FRAMES, (0, 1))                    # voice 0: B and the tag are its
        n0 = len(calls); m0 = r.r32(SA + SA_FMSTATS + FM_MISSED)
        out, od = r.buf(DEV_FRAMES); scr, _ = r.buf(DEV_FRAMES)
        o = r.osc(v1, 1)
        r.call(r.r32(r.r32(o)) & ~1, o, scr, out)
        self.assertEqual(len(calls), n0, "voice 1's OSC2: no FM from voice 0's B")
        self.assertEqual(r.r32(SA + SA_FMSTATS + FM_MISSED) - m0, 1)
        r.block()
        # (4) another voice's OSC1 between this voice's two: it writes no B, so this OSC2 plays its own OSC1's
        f0 = len(calls.feeds)
        r.call(DSP["an_enter"], root, v)
        for vo, i in ((v, 0), (v1, 0), (v, 1)):
            o = r.osc(vo, i)
            r.call(r.r32(r.r32(o)) & ~1, o, scr, out)
        fs = calls.feeds[f0:]
        self.assertEqual([f["osc"] for f in fs], [r.osc(v, 0)], "only the named voice's OSC1 feeds B")
        x = calls[-1]
        self.assertEqual((x["osc"], x["A"]), (o2, SA + SA_FMBUF_B))
        self.assertTrue(np.array_equal(x["a"], fs[0]["b"].astype(np.float64)), "OSC2 reads its own OSC1's B")
        r.block()
        # (5) a render that feeds nothing sets no tag
        r.prop(r.osc(v1, 0), K_BITS, 3, 0x6F, 0)
        r.call(DSP["an_enter"], root, v)
        n0, f0 = len(calls), len(calls.feeds)
        st0 = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
        for vo, i in ((v1, 0), (v, 1)):
            o = r.osc(vo, i)
            r.call(r.r32(r.r32(o)) & ~1, o, scr, out)
        st = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
        self.assertEqual(st[FM_R1 // 4], 0, "word +4 (BE4.41's bits=): 0, the stale 0x1A4 ignored")
        self.assertEqual((len(calls), len(calls.feeds)), (n0, f0), "no feed, so voice 0's OSC2 plays no FM 1>2")
        self.assertEqual(st[FM_MISSED // 4] - st0[FM_MISSED // 4], 1, "fmx= missed")
        r.block()

    def test_mixed_voices(self):
        """4.5: OSC1 Wavetable (stock's), OSC2 Analog with FM 1>2 100 %: no B, no FM 1>2, nothing counted (the control is
        hidden, FM 1>2 needs an Analog OSC1), the output FM 1>2 0's byte for byte; with Sub FM on OSC2 too, Sub FM alone (one
        term, A); free and synced to the Wavetable OSC1. OSC1 Analog next to a Wavetable OSC2 holding FM 1>2: OSC1 feeds
        nothing.
        BE4.44 W4 (reviewed change, plan 3.5 and D2's last case): a Wavetable OSC1 feeds B, so OSC2's FM 1>2 plays next to it
        and is its one amount there. This rig's OSC1 has no WAV, so it writes no B: FM 1>2 is missed (fmx= counts each
        block), nothing renders under FM, the output FM 1>2 0's byte for byte; OSC2's Sub FM 1000 plays nothing either (D2).
        Given a WAV, OSC1 feeds B every block and OSC2 plays FM 1>2 from it alone (a = B), every call the golden's."""
        for sync in (0, 1):
            with self.subTest(sync=sync):
                calls, outs, stats, _, (r, v, _) = self.play(types=(0, 1), sync=sync, shapes=(48, 88))
                _, outs0, _, _, _ = self.play(types=(0, 1), sync=sync, shapes=(48, 88), x12=0)
                self.assertEqual((list(calls), calls.feeds, stats), ([], [], (0, 0, 0, 6, 0, 0, 0, 0)), "no WAV: fmx= missed")
                self.assertEqual(outs, outs0, "FM 1>2 plays nothing with a Wavetable OSC1 that has no WAV")
                calls, _, stats, _, (r, v, _) = self.play(types=(0, 1), sync=sync, shapes=(48, 88), fm=(0, 1000), x12=0)
                self.assertEqual((list(calls), calls.fills), ([], []), "W4 (D2): OSC2's Sub FM plays nothing next to either")
                calls, _, stats, _, (r, v, _) = self.play(types=(0, 1), sync=sync, shapes=(48, 88), fm=(0, 1000),
                                                          each=wt_osc1_wav)
                self.replay("mixed + WAV", calls)
                self.assertTrue(calls and all(x["osc"] == r.osc(v, 1) and x["A"] == x["B"] == SA + SA_FMBUF_B and
                                              (x["ib"], x["dib"]) == (0.0, 0.0) for x in calls), "FM 1>2 alone, from B")
                self.assertEqual(check_wt_feeds(self, "mixed + WAV", calls, r.osc(v, 0), r.osc(v, 1)), 6)
                self.assertEqual(stats[FM_MISSED // 4], 0)
        # BE4.44 W3 (reviewed change, docs/plan-wt-fm.md 8 W3): a tagged Wavetable OSC2 holding FM 1>2 is a carrier of B now, so
        # the Analog OSC1 feeds it (check_feeds); this rig's Wavetable OSC2 has no WAV, so it reads nothing (WtFmW3: one that does)
        calls, _, stats, _, (r, v, _) = self.play(types=(1, 0), fm=(0, 0))
        self.assertEqual((list(calls), stats), ([], (0,) * 8), "a Wavetable OSC2 with no WAV: no FM render, nothing counted")
        self.assertGreater(check_feeds(self, "OSC1 -> a Wavetable OSC2", calls, r.osc(v, 0), r.osc(v, 1)), 0,
                           "W3: OSC1 feeds a Wavetable OSC2's FM 1>2")

    def test_the_amounts_cases_are_the_expected_change(self):
        """P3 Q6's expected change (6.7: test_q1_plays_no_amount's FM 1>2 part, plan 7): its seven cases, Rig() and Rig(dev=True),
        with FM 1>2 333 alone: where both oscillators are Analog (free, sync, the silent sync master, glide, stock's root
        voice) OSC2 plays FM 1>2 from OSC1's B, every call the golden's and B OSC1's (check_feeds; E3 (a): the silent master
        feeds too); where it cannot (OSC2 Wavetable, OSC1 Wavetable: mixed) nothing renders under FM and nothing feeds (the
        part test_q1_plays_no_amount keeps against BE4.40); fm= = the renders with FM, fmx= 0 / 0. BE4.44 W3 (reviewed change):
        an Analog OSC1 feeds a tagged Wavetable OSC2's FM 1>2 now ("osc2 wavetable": B written, check_feeds; that rig's OSC2
        has no WAV, so nothing reads it). BE4.44 W4 (reviewed change, plan 3.5): a Wavetable OSC1 feeds B too, so "mixed" plays
        FM 1>2 333 now (Mixed's OSC1 has a WAV): B the Wavetable OSC1's (check_wt_feeds)."""
        p3 = (0, 0, 333)
        me = P3Identity()
        cases = (("free", lambda r: me.voice_case(r, shape=(48, 88), p3=p3), True),
                 ("sync", lambda r: me.voice_case(r, shape=(70, 100), sync=1, p3=p3), True),
                 ("sync silent master", lambda r: me.voice_case(r, shape=(48, 88), sync=1, level0=0.0, p3=p3), True),
                 ("glide", lambda r: me.voice_case(r, **P3Identity.GLIDE, p3=p3), True),
                 ("osc2 wavetable", lambda r: me.voice_case(r, shape=(64, 48), types=(1, 0), p3=p3), False),
                 ("root voice", lambda r: me.root_case(r, p3=p3), True),
                 ("mixed", lambda r: me.mixed_case(r, 48, p3=p3), True))
        for dev in (False, True):
            for name, run, plays in cases:
                with self.subTest(dev=dev, case=name):
                    r = Rig(dev=dev); calls = fm_capture(r)
                    run(r)
                    st = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
                    self.assertEqual(len(calls) > 0, plays, f"{name}: FM 1>2 plays where both are Analog")
                    self.assertEqual(len(calls.feeds) > 0, plays or name == "osc2 wavetable", "B (W3: for a Wavetable OSC2 too)")
                    if name == "osc2 wavetable":
                        self.assertGreater(check_feeds(self, name, calls, r.osc(r.v, 0), r.osc(r.v, 1), n=r.frames), 0)
                    self.assertEqual(st[FM_FM // 4], self.renders(calls))
                    self.assertEqual((st[FM_SKIPPED // 4], st[FM_MISSED // 4]), (0, 0))
                    if plays:
                        self.replay(name, calls)
                        oscs = {x["osc"] for x in calls}
                        self.assertEqual(len(oscs), 1, "only OSC2 renders under FM")
                        o2 = oscs.pop(); o1 = o2 - S.VOICE_WT2 + S.VOICE_WT1
                        chk = check_wt_feeds if name == "mixed" else check_feeds
                        self.assertGreater(chk(self, name, calls, o1, o2, n=r.frames), 0)

    def test_bessel_sine_to_sine(self):
        """6.3 (R10): OSC1 and OSC2 at Shape 0 (sinq's sine), OSC1 -> B (an_feed, Full) -> OSC2, on the kernel alone (KBench),
        a coherent block of 1024 samples: ratio 1 at index 1 and at I(157) (BE4.42's law near 1), 3:1 at 2, 1:3 at 1: OSC2's spectrum is
        p3_golden.fm12_bessel's (J_k per bin within bessel_tol with OSC1's stated error as eps_m and the block's own float32
        phase walk: 0.34 dB on J3 at I = 1). B is golden bit for bit; the golden's named failures (B one sample late, the index
        2 % off) fail the same check on the kernel's own samples."""
        n = 1024; kb = KBench(n)
        sine = G.FmWave(-1.0, "f32")
        for cc, cm, I in ((11, 11, 1.0), (11, 11, an_index(157)), (33, 11, 2.0), (7, 21, 1.0)):
            with self.subTest(cc=cc, cm=cm, I=I):
                inc1, inc2, qm0, pc0 = f32(cm / n), f32(cc / n), f32(0.13), f32(0.29)
                kb.state(kb.s1, qm0, f32(sine.value(qm0)))
                y1 = kb.osc1(inc1, -1.0, n)
                B = kb.feed(n, inc1)
                self.assertTrue(np.array_equal(u32s(B), u32s(G.b_buffer(y1, inc1))), "B: golden")
                kb.state(kb.s2, pc0, f32(sine.value(pc0)))
                y2, (ph, _) = kb.osc2(inc2, -1.0, n, I)
                di = G.increments(inc2, [(np.full(n, I), B)], law="f32", fma=True)
                g = G.fm_render(di, k=-1.0, phase=pc0, delayed=f32(sine.value(pc0)), law="f32", fma=True)
                fm_check(self, f"bessel {cc}:{cm}", g, y2, ph, None, -1.0, 0)
                walk = G.fm_phase_walk(di, pc0)
                chk = G.fm12_bessel(y2, cc, cm, I, pc0, qm0, walk=walk)
                self.assertTrue(chk["ok"], (chk["worst"], chk["tol"]))
                j3 = [b for b in chk["bins"] if b[0] == (cc + 3 * cm) % n]
                for wrong, kw in (("b late", dict(B=np.r_[B[:1], B[:-1]], I=I)), ("I 2 %", dict(B=B, I=I * 1.02))):
                    kb.r.u.mem_write(kb.b, np.asarray(kw["B"], dtype="<f4").tobytes())
                    kb.state(kb.s2, pc0, f32(sine.value(pc0)))
                    yw, _ = kb.osc2(inc2, -1.0, n, f32(kw["I"]))
                    self.assertFalse(G.fm12_bessel(yw, cc, cm, I, pc0, qm0, walk=walk)["ok"], wrong)
                print(f"\n  Bessel FM 1>2 {cc}:{cm} I {I:.4f}: worst {chk['worst']:.3g} of {chk['tol']:.3g}"
                      + (f"; J3 bin {j3[0][3]:+.3f} dB (bound {j3[0][4]:.3f})" if j3 else ""), file=sys.stderr)

    N100 = 100 * DEV_FRAMES

    def test_no_dc_drift_over_100_blocks(self):
        """6.3 (carried from Q5): index 8 and 16 (BE4.42's 100 %) through zero, 100 device blocks (25,600 samples) on the
        kernel alone, OSC1 = OSC2 at note 60 (96 kHz). FM 1>2 from a free OSC1 at Shapes 48 / 88 / 127 (B by an_feed): OSC2's
        mean frequency over whole OSC1 cycles is the unmodulated one within one bin (p3_golden.drift, DRIFT_BIN). Sub FM by
        the DC-free Square (PW 30 %), the sine and the triangle subs at index 8: the same, and the raw Square's DC (1 - 2 pw)
        fails it (control). OSC2's end phase is the golden's exactly (di's arithmetic; one FM 1>2 and one Sub FM case: a
        python loop). (BE4.41 also ran each Shape at 12 / 8 / 4 bits and the crush's own share: retired with Bit Depth.)"""
        inc = f32(261.6256 / DEV_RATE); N = self.N100; kb = KBench(N)
        whole = G.whole_cycles(np.full(N, inc), 0.1)
        rows = []
        for shape in (48, 88, 127):
            k = f32(k_of(shape)); wv = G.FmWave(k, "f32", fm_width(k))
            for I in (8.0, 16.0):
                with self.subTest(shape=shape, I=I):
                    kb.state(kb.s1, f32(0.1), f32(wv.value(0.1)))
                    y1 = kb.osc1(inc, k, N)
                    B = kb.feed(N, inc)
                    self.assertTrue(np.array_equal(u32s(B), u32s(G.b_buffer(y1, inc))), "B: golden")
                    kb.state(kb.s2, f32(0.3), f32(G.FmWave(0.0, "f32").value(0.3)))
                    _, (ph, _) = kb.osc2(inc, 0.0, N, I)
                    di = G.increments(inc, [(np.full(N, I), B)], law="f32", fma=True)
                    self.assertLess(float(di.min()), 0.0, "through zero")
                    if shape == 88 and I == 16.0:                 # (a python loop over 25,600 samples: one case)
                        self.assertEqual(f32(ph), f32(G.fm_render(di, k=0.0, phase=f32(0.3), delayed=0.0, law="f32").phase),
                                         "OSC2's end phase: the golden's")
                    d = G.drift(di, inc, whole)
                    self.assertTrue(d["ok"], d)
                    rows.append(f"{shape}/I{I:g} {d['cycles']:+.3f} cyc")
        isub = f32(inc / 2)
        for wave, pw, raw in (("square", 0.3, False), ("square", 0.3, True), ("sine", 0.5, False), ("triangle", 0.5, False)):
            with self.subTest(sub=wave, raw=raw):
                m, _ = G.sub_mod(wave, isub, N, 0.1, pw)
                if raw:
                    m = m + (2 * pw - 1)
                a = np.asarray(f32a(isub) * m, dtype=np.float32).astype(np.float64)
                kb.state(kb.s2, f32(0.3), 0.0)
                kb.r.u.mem_write(kb.a, np.asarray(a, dtype="<f4").tobytes())
                kb.r.u.mem_write(kb.fi, struct.pack("<IffIIff", kb.a, 8.0, 0.0, 0, kb.a, 0.0, 0.0))
                kb.r.call(DSP["an_render_fm"], kb.s2, kb.y2, N, kb.ev, sargs=(0, kb.fi), fargs=(inc, 0.0))
                ph = struct.unpack("<f", kb.r.u.mem_read(kb.s2 + 4, 4))[0]
                di = G.increments(inc, [(np.full(N, 8.0), a)], law="f32", fma=True)
                if wave == "square" and not raw:
                    self.assertEqual(f32(ph), f32(G.fm_render(di, k=0.0, phase=f32(0.3), delayed=0.0, law="f32").phase))
                d = G.drift(di, inc, G.whole_cycles(np.full(N, isub), 0.1))
                self.assertEqual(d["ok"], not raw, (wave, raw, d))
                rows.append(f"sub {wave}{' raw' if raw else ''} {d['cycles']:+.3f} cyc")
        print("\n  DC drift over 100 blocks (through zero): " + "; ".join(rows), file=sys.stderr)

    def test_aliasing_after_the_fir_is_below_48k(self):
        """6.3 (carried from Q5): the kernel's FM voice rendered at the device's 96 kHz, then stock's FIR and 2:1 decimation
        (p3_golden.decimate on STOCK_FIR: its six taps found here in stock 1.6.8's image, once, in order: S), against the
        same voice rendered by the kernel at 48 kHz (the old design), coherent blocks of 8192 output samples after one block
        of warm-up: off-harmonic / harmonic energy (p3_golden.alias_db) is more than 3 dB lower: FM 1>2 (a saw carrier, OSC1
        a sine at 149 and at 591 cycles, index 4) and Sub FM (a saw carrier at 298 cycles, a sine sub an octave down, index 8:
        the golden self-test's 1182 / 591 at index 8 hits the clamp at 48 kHz, a figure of the clamp, not of aliasing); BE4.42:
        and FM 1>2 at 149 cycles at index 16 (the new law's 100 %). The golden's own figures for the same cases are printed
        next to the kernel's."""
        six = struct.pack("<6f", *G._FIR6)                     # (the golden's taps, as float32 words in its chain's order)
        self.assertEqual(STOCK_IMG.count(six), 1, "STOCK_FIR: stock's six literals, once, in that order (S: its FIR's pool)")
        self.assertEqual(struct.unpack("<6f", six), tuple(G._FIR6), "the golden's taps are float32 values")
        n48 = 8192; kb = KBench(2 * n48)
        rows = []
        for route, cc, cm, I, kc, km in (("fm12", 149, 149, 4.0, 0.0, -1.0), ("fm12", 591, 591, 4.0, 0.0, 0.0),
                                        ("sub", 298, 149, 8.0, 0.0, -1.0), ("fm12", 149, 149, 16.0, 0.0, -1.0)):
            got = {}
            for fs in (48000, 96000):
                n = n48 * (fs // 48000); inc_c, inc_m = f32(cc / n), f32(cm / n)
                if route == "fm12":
                    wv = G.FmWave(km, "f32")
                    kb.state(kb.s1, 0.0, f32(wv.value(0.0)))
                    kb.osc1(inc_m, km, n); kb.osc1(inc_m, km, n)        # warm-up, then the coherent block
                    kb.feed(n, inc_m)
                    kb.state(kb.s2, 0.0, f32(G.FmWave(kc, "f32").value(0.0)))
                    kb.osc2(inc_c, kc, n, I); y, _ = kb.osc2(inc_c, kc, n, I)
                else:
                    m, _ = G.sub_mod("sine", inc_m, n, 0.0)
                    a = np.asarray(f32a(inc_m) * m, dtype=np.float32)
                    kb.r.u.mem_write(kb.a, a.tobytes())
                    kb.r.u.mem_write(kb.fi, struct.pack("<IffIIff", kb.a, I, 0.0, 0, kb.a, 0.0, 0.0))
                    kb.state(kb.s2, 0.0, f32(G.FmWave(kc, "f32").value(0.0)))
                    for _ in range(2):
                        kb.r.call(DSP["an_render_fm"], kb.s2, kb.y2, n, kb.ev, sargs=(0, kb.fi), fargs=(inc_c, kc))
                    y = kb.get(kb.y2, n)
                got[fs] = G.alias_db(G.decimate(y) if fs == 96000 else y, math.gcd(cc, cm))
            gold = {fs: G.alias_case(route, cc, cm, I, kc, km, fs=fs, n48=n48) for fs in (48000, 96000)}
            rows.append(f"{route} {cc}/{cm} I {I:g}: kernel {got[48000]:.1f} -> {got[96000]:.1f} dB (golden {gold[48000]:.1f} -> "
                        f"{gold[96000]:.1f})")
            self.assertLess(got[96000], got[48000] - 3.0, rows[-1])
        print("\n  aliasing, 48 kHz -> 96 kHz + stock's FIR: " + "; ".join(rows), file=sys.stderr)

    def test_cost(self):
        """5 / 6.5: hooks per voice-block (Span: an_enter + an_osc with their stock helpers; median of 6) on the device's path,
        note 60, both oscillators at the saw, a sine sub: off; the full-FM voice (Sub FM on both, FM 1>2: the copy, B, two FM
        renders, OSC2 on two terms). Gate (plan 5, fixed in Q5): 1.3 x (the off voice + the specialised delta: 2,600 a Sub FM
        oscillator, + 764 for writing B (3,364), the sine copy 4,398, sub_inc's stock part (measured here)). BE4.42: the gate
        was derived at index 8 (BE4.41's 100 %), and an FM render's cost grows with the index (more edge crossings through
        zero), so it gates the same workload: every amount 630 (I = 7.998, the new law's index 8). The new 100 % (index 16)
        is a workload BE4.41 had no knob for: it is gated by an expected-change budget, FM_DEPTH_ALLOW x the same gate (plan
        section BE4.42, "Cost": the deeper index's extra edge corrections measured 1.6 % over plan 5's gate; a restated
        budget for a new workload, put to Jeff, not a loosened bound: the index-8 workload keeps plan 5's gate). Recorded,
        not gated: FM 1>2 alone; the full voice synced; the triangle carriers. 6.5: no VDIV inside an_feed (by PC)."""
        SPEC_OSC, SPEC_B, SPEC_COPY = 2600, 3364, 4398
        I8 = 630                                                 # an_index(630) = 7.998: the gate's index (8)
        FM_DEPTH_ALLOW = 1.03                                    # BE4.42 expected change: index 16's budget (plan BE4.42)
        rows = {}
        for name, fm, x12, sync, shape in (("off", (0, 0), 0, 0, None), ("full", (I8, I8), I8, 0, None),
                                           ("full 100%", (1000, 1000), 1000, 0, None), ("fm12", (0, 0), 1000, 0, None),
                                           ("full+sync", (1000, 1000), 1000, 1, None), ("full tri", (1000, 1000), 1000, 0, 24)):
            r = Rig()
            root, v, sub, src = self.voice(r, 4, 1000, fm=fm, glide=0, fine=0, sync=sync)
            r.root_prop(root, K_FM12, x12, 1)
            for i in ((0, 1) if shape is not None else ()):
                r.root_prop(root, K_SHAPE, shape, i)
            sp = [Span(r, DSP["an_enter"]), Span(r, DSP["an_osc"])]
            fill, copy = Span(r, dsp_sym("sub_fill")), Span(r, DSP["an_sub_copy"])
            vd = [0]
            lo, hi = sym_range("an_feed")
            r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: vd.__setitem__(0, vd[0] + is_vdiv(uc, a, sz)), begin=lo, end=hi - 1)
            m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
            slot18 = r.r32(r.r32(root) + 0x48)
            self.note(r, v, sub, 60)
            per = []
            for b in range(6):
                acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
                t0 = sum(s.total for s in sp)
                r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out)); r.block()
                per.append(sum(s.total for s in sp) - t0)
            rows[name] = (sorted(per)[3], (fill.total - copy.total) / max(1, fill.calls))
            self.assertEqual(vd[0], 0, f"{name}: no VDIV inside an_feed")
            st = struct.unpack("<8I", r.u.mem_read(SA + SA_FMSTATS, 32))
            if x12:
                self.assertGreater(st[FM_FM // 4], 0, f"{name}: control, FM plays")
        self.assertAlmostEqual(an_index(I8), 8.0, delta=0.01)
        stock = rows["full"][1]
        gate = 1.3 * (rows["off"][0] + SPEC_B + SPEC_OSC + SPEC_COPY + stock)
        print(f"\n  FM 1>2 cost (hooks per voice-block, 2N, note 60, saw): off {rows['off'][0]}, full at index 8 {rows['full'][0]} "
              f"({rows['full'][0] / rows['off'][0]:.2f} x; {rows['full'][0] / gate:.3f} of the gate {gate:.0f} = 1.3 x "
              f"({rows['off'][0]} + {SPEC_B + SPEC_OSC + SPEC_COPY + stock:.0f})); full at 100 % (index 16) {rows['full 100%'][0]} "
              f"({rows['full 100%'][0] / gate:.3f} of the gate, {rows['full 100%'][0] / rows['full'][0]:.3f} x index 8's); "
              f"FM 1>2 alone {rows['fm12'][0]}; full synced {rows['full+sync'][0]}; full, triangle carriers {rows['full tri'][0]} "
              f"({rows['full tri'][0] / gate:.3f} of the gate, not gated)", file=sys.stderr)
        self.assertLessEqual(rows["full"][0], gate, "the cost gate (plan 5: Q6's full voice at index 8)")
        self.assertLessEqual(rows["full"][0], 30906, "and Q5's fixed figure for it (plan 5: 1.3 x (13,294 + 10,480), M)")
        self.assertLessEqual(rows["full 100%"][0], FM_DEPTH_ALLOW * gate,
                             "BE4.42: the full voice at 100 % (index 16) within its restated budget (plan BE4.42, Cost)")
        self.assertGreater(rows["full 100%"][0], rows["full"][0], "control: index 16 costs more edges than index 8")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class FmEnv(unittest.TestCase):
    """BE4.43 (docs/plan-p3-fm-bits.md BE4.43; Jeff's design 2026-11-16): one FM amount per oscillator (OSC1 Sub FM; OSC2 FM
    1>2 while OSC1 is Analog, else Sub FM) times the oscillator's own FM envelope (FM Atk 0..127, FM Dec 0..128 = Inf), at
    block rate on the existing index lines. Checked against p3_golden.py's fm_env (written from the design text before
    the M7 code, the spec: its time table FM_ENV_BLOCKS, its float32 law and its lines fm_env_lines) on the device's path
    (Fm12's harness: stock's root, 2N, stock's sub, every an_render_fm call captured): each FM block's index line (ia,
    dia) is the golden's f32 line bit for bit, with retriggers (stock's voice note start, slot 2) back to back and mid-note,
    the amount's own ramp (msub / m12) under the envelope, each oscillator with its own envelope, the mixed voice; the
    envelope keeps time while the oscillator has no amount or is silent; every call stays the kernel's golden (replay);
    each golden mutant is told apart from the M7's lines; the tables in both cores are the golden's; the cost. Review
    follow-ups: an envelope set mid-note starts from 1 (held while unset), Inf holds at its stage's start, and the one
    two-term block (the Wavetable -> Analog switch) under OSC2's envelope."""
    voice, note = SubCopy.voice, SubCopy.note
    play = Fm12.play
    lines, replay = SubFm.lines, SubFm.replay

    def got(self, calls, osc, first):
        """{block: (ia, dia)} of osc's FM calls (one line across a block's parts, the block rendered end to end)."""
        by = {}
        for x in calls:
            if x["osc"] == osc:
                by.setdefault(x["block"] - first, []).append(x)
        out = {}
        for b, xs in by.items():
            ls = {(x["ia"], x["dia"]) for x in xs}
            self.assertEqual(len(ls), 1, f"block {b}: one line across its parts {ls}")
            self.assertEqual(sum(x["n"] for x in xs), DEV_FRAMES, f"block {b}: end to end")
            out[b] = ls.pop()
        return out

    def want(self, atk, dec, v0, v1, n, notes, mutant=None):
        return {b: l for b, l in enumerate(G.fm_env_lines(atk, dec, v0, v1, n, notes=notes, N=DEV_FRAMES, law="f32",
                                                          mutant=mutant))}

    def test_the_tables_are_the_goldens(self):
        """The time curve is a table in both cores (synth_shared.h AN_FM_ENV_BLOCKS: the M7's envelope, the M4's value
        texts), and the decay factor r = f32(0.001^(1 / n)) a table in the DSP: each is p3_golden's (FM_ENV_BLOCKS,
        fm_env_consts), found once in its core's code, in order (a wrong entry would not be found)."""
        blocks = struct.pack("<128H", *G.FM_ENV_BLOCKS)
        self.assertEqual((G.FM_ENV_BLOCKS[0], G.FM_ENV_BLOCKS[127], len(set(G.FM_ENV_BLOCKS))), (1, 3750, 128))
        r = struct.pack("<128f", *[G.fm_env_consts(0, d)[3] for d in range(128)])
        lo, hi = S.M7_DSP - BASE, S.M7_DSP_LIMIT - BASE
        at = [k for k in range(len(IMG)) if IMG.startswith(blocks, k)]
        self.assertEqual(len([k for k in at if lo <= k < hi]), 1, "the DSP's block table: once, the golden's")
        self.assertEqual(len([k for k in at if k + BASE >= 0x08100000]), 1, "the M4's (the value texts): once, the golden's")
        self.assertEqual(IMG[lo:hi].count(r), 1, "the DSP's decay factors: the golden's f32 r, once")
        self.assertEqual(G.fm_env_consts(0, 0)[3], f32(0.001), "control: Dec 0 is one block to -60 dB")

    def test_osc1_lines_are_the_goldens(self):
        """OSC1's Sub FM under its envelope, OSC2 without FM: Atk 5 / Dec 8 (7 and 10 blocks) at 100 %, the note again at
        block 19 and 20 (back to back: a hard reset each, mid-attack) after a full decay to 0; Atk 0 / Dec 0 (instant, a
        one-block fall) with a retrigger; Atk 1 / Inf (two blocks up, then held); Atk 3 / Dec 6 under a moving amount (msub's
        ramp and the envelope's multiply at the line's ends). Every FM block's line is fm_env_lines' f32 line bit for bit,
        and every call the kernel's golden (replay); the envelope at 0 still renders under FM (index 0: the cost of a
        decayed note, no skip)."""
        amt = lambda a: (lambda b: {0: a(b)})
        cases = (("5/8", 5, 8, lambda b: 1000, (0, 19, 20), 24),
                 ("0/0", 0, 0, lambda b: 700, (0, 4), 8),
                 ("1/Inf", 1, G.FM_DEC_INF, lambda b: 1000, (0, 5), 8),
                 ("3/6 moving", 3, 6, lambda b: (120, 250, 1000, 1000, 640, 640, 37, 900, 900, 900, 1000, 1000)[b], (0, 7), 12))
        for name, atk, dec, a, notes, n in cases:
            with self.subTest(case=name):
                calls, _, stats, _, (r, v, sub) = self.play(fm=(0, 0), x12=0, blocks=n, amounts=amt(a), notes=notes[1:],
                                                            env={0: (atk, dec)})
                self.replay(name, calls)
                v1 = [a(b) for b in range(n)]; v0 = [0] + v1[:-1]
                got = self.got(calls, r.osc(v, 0), r.first)
                self.assertEqual(sorted(got), list(range(n)), f"{name}: FM every block")
                want = self.want(atk, dec, v0, v1, n, notes)
                for b in range(n):
                    self.assertEqual(got[b], want[b], f"{name}: block {b}: the M7's line vs the golden's f32")
                self.assertEqual([x for x in calls if x["osc"] == r.osc(v, 1)], [], "OSC2: no FM")
                self.assertEqual(stats[FM_FM // 4], n)
                if name == "5/8":
                    self.assertEqual(got[17], (0.0, 0.0), "control: decayed to 0 (still an FM render)")
                    self.assertGreater(got[16][0], 0.0, "control: the last decaying block starts above 0")
                    self.assertEqual(got[19][0], 0.0, "the retrigger: the line starts at 0")
                    for mu in G.FM_ENV_MUTANTS:              # each named failure is told apart from the M7's lines
                        bad = self.want(atk, dec, v0, v1, n, notes, mutant=mu)
                        self.assertTrue(any(got[b] != bad[b] for b in range(n)), f"mutant {mu} not seen")

    def test_each_oscillator_has_its_own_envelope(self):
        """OSC1: Sub FM 600 with Atk 2 / Inf; OSC2: FM 1>2 1000 with Atk 3 / Dec 4, and a Sub FM 900 stored that plays
        nothing (OSC1 is Analog: one amount per oscillator, so one term, a = B); the note again at block 9: both lines the
        golden's for their own settings, every call golden, B OSC1's (check_feeds). Then the mixed voice: OSC1 Wavetable,
        OSC2's Sub FM 1000 under Atk 2 / Dec 3 (its one amount there), free and synced to the Wavetable OSC1.
        BE4.44 W4 (reviewed change, D2's last case): next to a Wavetable OSC1 OSC2's one amount is FM 1>2 too (the Wavetable
        OSC1, given a WAV here, feeds B: check_wt_feeds), its Sub FM 1000 plays nothing: the mixed voice's line is FM 1>2
        1000's under OSC2's Atk 2 / Dec 3, a = B, free and synced (the slave's events the golden's: replay)."""
        n, notes = 14, (0, 9)
        calls, _, stats, _, (r, v, sub) = self.play(fm=(600, 900), x12=1000, blocks=n, notes=notes[1:],
                                                    env={0: (2, G.FM_DEC_INF), 1: (3, 4)})
        self.replay("own envelopes", calls)
        o1, o2 = r.osc(v, 0), r.osc(v, 1)
        g1, g2 = self.got(calls, o1, r.first), self.got(calls, o2, r.first)
        w1 = self.want(2, G.FM_DEC_INF, [0] + [600] * (n - 1), [600] * n, n, notes)
        w2 = self.want(3, 4, [0] + [1000] * (n - 1), [1000] * n, n, notes)
        self.assertEqual([g1[b] for b in range(n)], [w1[b] for b in range(n)], "OSC1: its own envelope (Sub FM)")
        self.assertEqual([g2[b] for b in range(n)], [w2[b] for b in range(n)], "OSC2: its own envelope (FM 1>2)")
        self.assertTrue(all((x["A"], x["ib"], x["dib"]) == (SA + SA_FMBUF_B, 0.0, 0.0) for x in calls if x["osc"] == o2),
                        "OSC2: FM 1>2 alone, its Sub FM 900 plays nothing")
        self.assertEqual(check_feeds(self, "own envelopes", calls, o1, o2), n)
        for sync in (0, 1):
            with self.subTest(mixed_sync=sync):
                calls, _, _, _, (r, v, sub) = self.play(fm=(0, 1000), x12=1000, types=(0, 1), sync=sync, blocks=n,
                                                        notes=notes[1:], env={1: (2, 3)}, each=wt_osc1_wav)
                self.replay(f"mixed sync={sync}", calls)
                g = self.got(calls, r.osc(v, 1), r.first)
                w = self.want(2, 3, [0] + [1000] * (n - 1), [1000] * n, n, notes)
                self.assertEqual([g[b] for b in range(n)], [w[b] for b in range(n)], "OSC2's FM 1>2 under its envelope")
                self.assertTrue(all(x["A"] == x["B"] == SA + SA_FMBUF_B for x in calls), "B alone: its Sub FM plays nothing")
                self.assertEqual(check_wt_feeds(self, f"mixed sync={sync}", calls, r.osc(v, 0), r.osc(v, 1)), n)

    def test_the_envelope_keeps_time(self):
        """Real time: OSC1's envelope (Atk 4 / Dec 6: 5 and 8 blocks) runs from the note whether or not it is heard: no amount
        in blocks 0..2 (no FM render, the envelope stepped all the same), Sub FM 1000 from block 3, Level 0 in blocks 6..8
        (the skip path: no render), heard again from 9. Each FM block's line is the golden's at the note's own time (env at
        boundary b, not at the first heard block), with the amount's ramp from 0 wherever the block before played no FM
        (msub 0, OD16). Control: some blocks were skipped."""
        n = 16
        a = lambda b: 0 if b < 3 else 1000
        calls, _, _, _, (r, v, sub) = self.play(fm=(0, 0), x12=0, blocks=n, amounts=lambda b: {0: a(b)},
                                                levels=lambda b: (0.0 if 6 <= b <= 8 else 1.0, 1.0), env={0: (4, 6)})
        self.replay("keeps time", calls)
        got = self.got(calls, r.osc(v, 0), r.first)
        heard = sorted(got)
        self.assertEqual(heard[0], 3, "FM from the first block with an amount")
        self.assertTrue(set(range(6, 9)) - set(heard), f"control: blocks skipped while silent ({heard})")
        v1 = [a(b) for b in range(n)]; v0 = [v1[b - 1] if b and (b - 1) in got else 0 for b in range(n)]
        want = self.want(4, 6, v0, v1, n, (0,))
        for b in heard:
            self.assertEqual(got[b], want[b], f"block {b}: the envelope at the note's time")
        e0, _ = G.fm_env(4, 6, n)
        self.assertEqual(got[3][0], 0.0, "block 3: the amount ramps in from 0 (msub)")
        self.assertGreater(got[3][1], 0.0, "and up to I(1000) x env at boundary 4")
        self.assertAlmostEqual(e0[4], 4 / 5, delta=1e-12, msg="control: env 0.8 at boundary 4 (Atk 4 = 5 blocks)")

    def test_a_dec_turned_mid_note(self):
        """A choice of this build (the golden holds its values over a note): FM Dec turned from Inf to 4 (5 blocks) at block 6
        of a held note decays from there, as a note at block 6 with Atk 0 / Dec 4 would (Inf holds at its stage's start, so
        the decay is not already over); FM Atk turned mid-attack rescales the ramp at the next block (e = k / n_a with the
        new n_a, k the blocks since the note). Lines bit for bit against fm_env_lines on those terms."""
        n = 14
        def each(r, root, b):
            if b == 6:
                r.root_prop(root, K_FMDEC, 4, 0)
        calls, _, _, _, (r, v, sub) = self.play(fm=(1000, 0), x12=0, blocks=n, each=each, env={0: (0, G.FM_DEC_INF)})
        got = self.got(calls, r.osc(v, 0), r.first)
        before = self.want(0, G.FM_DEC_INF, [0] + [1000] * 5, [1000] * 6, 6, (0,))
        after = self.want(0, 4, [1000] * (n - 6), [1000] * (n - 6), n - 6, (0,))
        self.assertEqual([got[b] for b in range(6)], [before[b] for b in range(6)], "held at 1")
        self.assertEqual([got[b] for b in range(6, n)], [after[b - 6] for b in range(6, n)], "a decay from block 6")
        def each2(r, root, b):
            if b == 3:
                r.root_prop(root, K_FMATK, 8, 0)                 # 10 blocks, from 20 (31)
        calls, _, _, _, (r, v, sub) = self.play(fm=(1000, 0), x12=0, blocks=n, each=each2, env={0: (20, G.FM_DEC_INF)})
        got = self.got(calls, r.osc(v, 0), r.first)
        e_old = lambda k: f32(f32(float(k)) * f32(1 / 31)); e_new = lambda k: 1.0 if k >= 10 else f32(f32(float(k)) * f32(1 / 10))
        e0 = [e_old(k) for k in range(3)] + [e_old(3)] + [e_new(k) for k in range(4, n)]
        e1 = [e_old(k + 1) for k in range(3)] + [e_new(k + 1) for k in range(3, n)]
        I = G.an_index32(1000)
        for b in range(n):
            ia = f32(I * e0[b]) if b else 0.0
            end = f32(I * e1[b])
            self.assertEqual(got[b], (ia, 0.0 if end == ia else f32(f32(end - ia) / DEV_FRAMES)), f"Atk turned: block {b}")

    def test_an_envelope_set_mid_note_starts_from_1(self):
        """BE4.43 review (an Atk turned up from 0 on a held note restarted the attack: FM dropped out and swelled back): while
        the envelope is unset (Atk 0 / Dec Inf) it is held at 1 every block, so whatever is set mid-note starts from 1 at that
        block. Sub FM 1000 on OSC1, the note at 0 and again at 14: unset 0..5; Atk 8 at 6 (no change: held at 1, the Atk is
        the next note's); Dec 4 at 8 (a decay from 1 there, as a note at 8 with Atk 0 / Dec 4); the note at 14 attacks
        (Atk 8 / Dec 4); unset again at 20 mid-attack (1 from there); Dec 4 at 24 (a fresh decay from 1, not resumed from the
        stale attack state). Each stretch's lines are fm_env_lines' (the golden's) for those terms, bit for bit."""
        n, INF = 30, G.FM_DEC_INF
        def each(r, root, b):
            if b == 6: r.root_prop(root, K_FMATK, 8, 0)
            if b == 8: r.root_prop(root, K_FMDEC, 4, 0)
            if b == 20: r.root_prop(root, K_FMATK, 0, 0); r.root_prop(root, K_FMDEC, INF, 0)
            if b == 24: r.root_prop(root, K_FMDEC, 4, 0)
        calls, _, _, _, (r, v, sub) = self.play(fm=(1000, 0), x12=0, blocks=n, each=each, notes=(14,),
                                                env={0: (0, INF)})
        self.replay("set mid-note", calls)
        got = self.got(calls, r.osc(v, 0), r.first)
        self.assertEqual(sorted(got), list(range(n)), "FM every block")
        L = lambda *a: [w for _, w in sorted(self.want(*a).items())]
        held = lambda m, first=False: L(0, INF, ([0] if first else [1000]) + [1000] * (m - 1), [1000] * m, m, (0,))
        seg = lambda a, d, m: L(a, d, [1000] * m, [1000] * m, m, (0,))
        want = held(8, True) + seg(0, 4, 6) + seg(8, 4, 6) + held(4) + seg(0, 4, 6)
        for b in range(n):
            self.assertEqual(got[b], want[b], f"block {b}")
        I = G.an_index32(1000)
        self.assertEqual(got[6][0], I, "Atk turned up from 0 mid-note: no restart (the line stays at I(1000))")
        self.assertEqual(got[14][0], 0.0, "control: the next note attacks from 0")
        self.assertEqual(got[24][0], I, "set again after unset: from 1, not the stale attack")

    def test_inf_holds_at_its_stages_start(self):
        """BE4.43 review (surviving mutant inf_keeps_counting): Atk 1 / Dec Inf (two blocks up, then held), held 10 blocks past
        the attack, then Dec 4 at block 12: the decay starts from 1 at block 12 (Inf keeps j at 0; a counting hold would find
        the decay over and snap to 0, and a 13-bit count would run into the stage bits on a long hold)."""
        n, INF = 20, G.FM_DEC_INF
        def each(r, root, b):
            if b == 12: r.root_prop(root, K_FMDEC, 4, 0)
        calls, _, _, _, (r, v, sub) = self.play(fm=(1000, 0), x12=0, blocks=n, each=each, env={0: (1, INF)})
        got = self.got(calls, r.osc(v, 0), r.first)
        L = lambda *a: [w for _, w in sorted(self.want(*a).items())]
        want = L(1, INF, [0] + [1000] * 11, [1000] * 12, 12, (0,)) + L(0, 4, [1000] * 8, [1000] * 8, 8, (0,))
        for b in range(n):
            self.assertEqual(got[b], want[b], f"block {b}")
        self.assertGreater(got[13][0], 0.0, "control: decaying, not over")

    def test_the_switch_block_under_osc2s_envelope(self):
        """BE4.43 review (surviving mutant env_not_on_j): the one block with two terms (OSC1 turned Analog at block 3: OSC2's
        Sub FM 700 ramps out on a while its FM 1>2 500 ramps in on b, test_both_routes) with OSC2's envelope set (Atk 8 / Dec
        4: mid-attack at 3, e 0.3 -> 0.4): both lines are the golden's under OSC2's envelope, and so is OSC2's one line in
        every other block (Sub FM 700 in 0..2, FM 1>2 500 in 4, 5).
        BE4.44 W4 (reviewed change, D2's last case): OSC2's Sub FM no longer plays next to a Wavetable OSC1 (FM 1>2 is its one
        amount next to either), so no real sequence reaches two terms any more: OSC2 in blocks 0..2 has FM 1>2 500 with no B
        (this OSC1 has no WAV: fmx= missed, no line). The two-term block is kept as a crafted state (as Q5's corner rules):
        OSC2's msub forced to 700 before block 3 (a Sub FM ramp still to run, BE4.43's switch), so block 3 ramps it out on a
        while FM 1>2 ramps in on b."""
        def each(r, root, b):
            if b == 3:
                r.root_prop(root, K_TYPE, 1, 0); r.root_prop(root, K_SHAPE, 48, 0); r.root_prop(root, K_SUBFM, 1000, 0)
                r.u.mem_write(r.osc(r.v, 1) + S.OSC_KERNEL + MSUB_AT, struct.pack("<H", 700))   # (crafted: BE4.43's msub)
        calls, _, st, _, (r, v, sub) = self.play(fm=(1000, 700), x12=500, types=(0, 1), each=each, env={1: (8, 4)})
        self.replay("switch under env", calls)
        o2 = r.osc(v, 1)
        got = self.got(calls, o2, r.first)
        wa = self.want(8, 4, [0, 0, 0, 700, 500, 500], [0, 0, 0, 0, 500, 500], 6, (0,))
        wb = self.want(8, 4, [0] * 6, [0, 0, 0, 500, 0, 0], 6, (0,))
        self.assertEqual(sorted(got), [3, 4, 5], "OSC2: no line before OSC1 is Analog (no B, its Sub FM plays nothing)")
        self.assertEqual(st[FM_MISSED // 4], 3, "fmx= missed in blocks 0..2")
        self.assertEqual([got[b] for b in range(3, 6)], [wa[b] for b in range(3, 6)], "OSC2's a line under its envelope")
        b3 = {(x["ib"], x["dib"]) for x in calls if x["osc"] == o2 and x["block"] == r.first + 3}
        self.assertEqual(b3, {wb[3]}, "block 3: FM 1>2's b line under OSC2's envelope")
        self.assertNotEqual(wb[3], self.want(0, G.FM_DEC_INF, [0] * 6, [0, 0, 0, 500, 0, 0], 6, (0,))[3],
                            "control: the envelope changes that line")

    def test_each_voice_retriggers_its_own(self):
        """Per voice: two voices of one root (the envelope lives in each oscillator's state), Atk 3 / Dec 5 on OSC1 (Sub FM
        1000), voice 0's note at block 0, voice 1's at block 3 and again at 7: each voice's lines are the golden's for its own
        note starts (voice 1 heard from block 3)."""
        r = Rig()
        root, v0, s0, _ = self.voice(r, 4, 1000, fm=(1000, 0), glide=0, fine=0)
        _, v1, s1, _ = self.voice(r, 4, 1000, root=root, index=1)
        r.root_prop(root, K_FMATK, 3, 0); r.root_prop(root, K_FMDEC, 5, 0)
        calls = fm_capture(r)
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        slot18 = r.r32(r.r32(root) + 0x48)
        acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
        first = r.idx
        n = 12
        for b in range(n):
            if b == 0:
                self.note(r, v0, s0, 60)
            if b in (3, 7):
                self.note(r, v1, s1, 64)
            for vo in ((v0,) if b < 3 else (v0, v1)):
                r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
                r.call(slot18 & ~1, root, vo, 0, scr, sargs=(acc, 0, out))
            r.block()
        g0, g1 = self.got(calls, r.osc(v0, 0), first), self.got(calls, r.osc(v1, 0), first)
        w0 = self.want(3, 5, [0] + [1000] * (n - 1), [1000] * n, n, (0,))
        w1 = self.want(3, 5, [0] + [1000] * (n - 4), [1000] * (n - 3), n - 3, (0, 4))
        self.assertEqual([g0[b] for b in range(n)], [w0[b] for b in range(n)], "voice 0")
        self.assertEqual([g1[b] for b in range(3, n)], [w1[b - 3] for b in range(3, n)], "voice 1: its own notes")

    def test_cost(self):
        """Hooks per voice-block (Span: an_enter + an_osc with their stock helpers; median of 8) on the device's path, note 60,
        saws, a sine sub: the FM-off voice and the full FM voice (Sub FM 1000 on OSC1, FM 1>2 1000 on OSC2), each with the
        envelope at its defaults (Atk 0 / Inf: not stepped) and set on both oscillators (Atk 20 / Dec 60, mid-attack and
        decaying in the measured blocks). Bounds (BE4.43's statement: a counter, at most a VDIV and a VMUL a block, the
        line's two VMULs; nothing a sample): fm_env_step at most 45 instructions a call (M: 38), and the envelope on both
        oscillators adds at most 180 a voice-block to either voice (M: +149 on the FM-off voice, 1.1 % of it, about 75 an
        oscillator-block with feeds' call; on the full FM voice the decaying index saves more edge corrections than the
        envelope costs). Unset (the defaults) it is not stepped at all (BitDepthGone, P3Identity: one byte OR, and since the
        review an_osc's two stores holding it at 1). The 180 is this build's own budget: 149 measured plus margin."""
        rows = {}
        step = None
        for name, fm, x12, env in (("off", (0, 0), 0, None), ("off+env", (0, 0), 0, (20, 60)),
                                   ("full", (1000, 0), 1000, None), ("full+env", (1000, 0), 1000, (20, 60))):
            r = Rig()
            root, v, sub, src = self.voice(r, 4, 1000, fm=fm, glide=0, fine=0)
            r.root_prop(root, K_FM12, x12, 1)
            for i in (0, 1):
                if env:
                    r.root_prop(root, K_FMATK, env[0], i); r.root_prop(root, K_FMDEC, env[1], i)
            sp = [Span(r, DSP["an_enter"]), Span(r, DSP["an_osc"])]
            st = Span(r, dsp_sym("fm_env_step"))
            m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
            slot18 = r.r32(r.r32(root) + 0x48)
            self.note(r, v, sub, 60)
            per = []
            acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
            for b in range(40):
                r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
                t0 = sum(s.total for s in sp)
                r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out)); r.block()
                if b >= 32:
                    per.append(sum(s.total for s in sp) - t0)
            rows[name] = sorted(per)[4]
            self.assertEqual(st.calls, 80 if env else 0, f"{name}: fm_env_step once an oscillator-block, only when set")
            if env:
                step = max(step or 0, st.total / st.calls)
        print(f"\n  FM envelope cost (hooks per voice-block, 2N, note 60, saw): off {rows['off']} -> {rows['off+env']} "
              f"({rows['off+env'] - rows['off']:+d}); full FM {rows['full']} -> {rows['full+env']} "
              f"({rows['full+env'] - rows['full']:+d}); fm_env_step {step:.1f} a call", file=sys.stderr)
        self.assertLessEqual(step, 45, "fm_env_step: a counter, a VDIV or a VMUL")
        self.assertLessEqual(rows["off+env"] - rows["off"], 180, "the envelope on the FM-off voice")
        self.assertLessEqual(rows["full+env"] - rows["full"], 180, "the envelope on the full FM voice")
        self.assertGreater(rows["off+env"], rows["off"], "control: it is stepped (costs something)")


# ---- BE4.44 W1 (docs/plan-wt-fm.md 3.1 .. 3.3, 3.5's bit, 7.3): FM state on a Wavetable oscillator, tiers 1 and 2
WT_TAG_HI, WT_HOME, WT_FEED_B = 0x7FA50000, 0xFF, 1        # analog.h, typed here
WT_RUNS = ((0, 8), (140, 8), (172, 20))                    # the saved runs in the area: tag pair; mipcur, wflags, fmk, wxf;
                                                           # msub, m12, fme, AnProps
WT_MIP_AT, WT_FLAGS_AT, WXF_AT = 140, 141, 144
OSC_POS_SRC = 0x2C                                         # Position's own source value (the knob, 0..1: the osc ctor's)
WT_MIP_OFFS = ((1, 0), (2, 0x80000), (4, 0xC0000), (8, 0xE0000), (16, 0xF0000), (32, 0xF8000), (64, 0xFC000))   # Rig.tabled's


def wt_tag(osc): return WT_TAG_HI | (osc >> 4 & 0xFFFF)


def wt_tagged(mem, k, osc):
    m, c = struct.unpack_from("<II", mem, k)
    return m == wt_tag(osc) and c == ~wt_tag(osc) & 0xFFFFFFFF


def rich_table(r):
    """Rig.tabled's table with frames that differ (a sine, plus a 3rd harmonic growing and a 5th falling with the frame), so
    a Position change crossfades different data; every mip level band-limited the same way."""
    if getattr(r, "rich", False):
        return
    for d, off in WT_MIP_OFFS:
        L = 2048 // d
        k = np.arange(L) / L
        f = np.arange(256)[:, None] / 255.0
        w = np.sin(2 * np.pi * k) + 0.6 * f * np.sin(6 * np.pi * k) + 0.3 * (1 - f) * np.cos(10 * np.pi * k)
        r.u.mem_write(r.tab + 4 * off, w.astype(np.float32).tobytes())
    r.rich = True


class WtRun:
    """Both oscillators Wavetable with the rich table (Mixed's voice, type2=0: WT1 with a pitch source, WT2 a fifth up), on
    stock 1.6.8 or this build, our keys pushed the same way to both (stock's slot 8 has no case for them)."""
    def __init__(self, stock, reset=(1, 1)):
        self.m = m = Mixed(stock=stock, type2=0)
        self.r = r = m.r
        r.tabled(m.o2); rich_table(r)
        self.oscs = (m.o1, m.o2)
        for o, x in zip(self.oscs, reset):
            r.u.mem_write(o + 0x1ED, bytes([x]))               # WTOscReset (OSC_RESET_FLAG)
        self.mems = []

    def push(self, i, key, val): self.r.prop(self.oscs[i], key, val, self.m.handle, i)

    def area(self, i): return bytes(self.r.u.mem_read(self.oscs[i] + S.OSC_KERNEL, 0xC0))


def wt_play(stock, blocks, pushes=(), notes=None, pos=None, levels=None, reset=(1, 1), each=None):
    """One scenario, block by block: each(w, b) first, then a note start, the Positions pos(b, i), the Levels levels(b);
    the voice's bytes after every block."""
    w = WtRun(stock, reset)
    m, r = w.m, w.r
    for i, k, v in pushes:
        w.push(i, k, v)
    notes = {0: 60} if notes is None else notes
    for b in range(blocks):
        if each:
            each(w, b)
        if b in notes:
            m.note(notes[b])
        if pos:
            for i, o in enumerate(w.oscs):
                r.wf(o + OSC_POS_SRC, pos(b, i))
        if levels:
            for i, l in enumerate(levels(b)):
                r.level(m.v, i, l)
        m.block()
        w.mems.append(bytes(r.u.mem_read(m.v, 0xB88)))
    return w


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class WtFmW1(unittest.TestCase):
    """BE4.44 W1 (docs/plan-wt-fm.md 3.1 .. 3.3, 7.3): storage and the switch, no reader yet (a gate that is not 0 plays as
    gate 0). Tier 1: a Wavetable oscillator never given WT FM work is stock's render, bit for bit, samples and memory. Tier 2:
    a tagged one (an envelope, an amount, feeding B) is stock's render body with the saved runs put back around it: samples
    and every byte outside the runs stock's, the runs holding the settings through stock's rewrites (wraps, frame changes),
    the note-start block included. The props side: default-state init before the first value, the tag pair, the untag rule,
    the bounded mipcur, the feed-B bit in any message order, the note start's START + HOME, Type flips with the resend."""
    PUSH_ENV = ((0, K_FMATK, 3), (0, K_FMDEC, 6), (1, K_FMDEC, 10))
    PUSH_AMT = ((0, K_SUBFM, 700), (1, K_FM12, 400), (1, K_FMATK, 2))
    # BE4.44 W2 (reviewed change): an amount opens the reader (tier 3, WtFmW2), so W1's "amounts" tier-2 rows keep only what
    # stays at gate 0: OSC1 tagged to feed B (OSC2's FM 1>2). OSC2 has FM 1>2 and plays the reader (W2: no B reaches a Wavetable
    # carrier until W3, so its index is 0): its samples and kernel are left out (reader=), its runs checked by WtFmW2.
    PUSH_FEED = ((1, K_FM12, 400), (1, K_FMATK, 2))

    def compare(self, name, a, b, tagged, masked=None, reader=()):
        """a this build, b stock: samples, and the voice after every block with the runs of the tagged oscillators masked (on
        both); tagged oscillators tagged after every block, the others never. W1 review: tagged may be a function of the
        block (a tag or an untag mid-note), and masked(block) the oscillators whose runs are left out there (default:
        the tagged ones). W2: reader, the oscillators playing the reader (tier 3): their samples and their whole kernel area and
        state (+0x10C .. +0x1E4) are not compared here."""
        if 0 not in reader:
            same(self, f32le(a.m.x1), f32le(b.m.x1), f"{name}: OSC1's samples")
        if 1 not in reader:
            same(self, f32le(a.m.x2), f32le(b.m.x2), f"{name}: OSC2's samples")
        self.assertGreater(max(abs(x) for x in a.m.x1 + a.m.x2), 0.1, f"{name}: control, it plays")
        ks = [o - a.m.v + S.OSC_KERNEL for o in a.oscs]
        at_blk = tagged if callable(tagged) else (lambda blk: tagged)
        masked = masked or at_blk
        for blk, (ma, mb) in enumerate(zip(a.mems, b.mems)):
            ma, mb = bytearray(ma), bytearray(mb)
            for i, k in enumerate(ks):
                self.assertEqual(wt_tagged(ma, k, a.oscs[i]), i in at_blk(blk), f"{name}: block {blk}: OSC{i + 1} tagged")
            for i, k in enumerate(ks):
                if i in masked(blk):
                    for off, n in WT_RUNS:
                        ma[k + off:k + off + n] = mb[k + off:k + off + n] = bytes(n)
                if i in reader:
                    ma[k:k + 0xD9] = mb[k:k + 0xD9] = bytes(0xD9)
            if ma != mb:
                at = next(j for j in range(len(ma)) if ma[j] != mb[j])
                self.fail(f"{name}: block {blk}: the voice differs from stock's at +{at:#x} (OSC kernel areas at "
                          f"{[hex(k) for k in ks]})")

    def test_untagged_is_stocks_bit_for_bit(self):
        """Tier 1 (7.3): no WT FM work, the FM keys pushed at their defaults (and the two D2 leaves no Wavetable oscillator
        keeps: OSC1's FM 1>2, OSC2's Sub FM): samples and the whole voice (both oscillators, every byte) stock 1.6.8's after
        every block, over notes 24 .. 120 with note starts, WTOscReset on and off, Position fixed and moving, Level 0 on OSC1
        for three blocks; nothing tagged. Cost (S2): at most +40 instructions an oscillator-block over stock (plan: +28)."""
        pushes = ((0, K_SUBFM, 0), (0, K_FM12, 500), (0, K_FMATK, 0), (0, K_FMDEC, FM_DEC_INF),
                  (1, K_SUBFM, 700), (1, K_FM12, 0), (1, K_FMATK, 0), (1, K_FMDEC, FM_DEC_INF))
        kw = dict(notes={0: 24, 6: 60, 7: 60, 12: 96, 18: 120}, pos=lambda b, i: 0.0 if b < 9 else ((b * 37 + 91 * i) % 256) / 255,
                  levels=lambda b: (0.0 if 3 <= b < 6 else 1.0, 1.0))
        for reset in ((1, 1), (0, 0)):
            with self.subTest(reset=reset):
                a = wt_play(False, 24, pushes, reset=reset, **kw)
                b = wt_play(True, 24, pushes, reset=reset, **kw)
                self.compare(f"untagged reset={reset}", a, b, ())
                d = [x - y for ca, cb in zip(a.m.counts, b.m.counts) for x, y in zip(ca, cb)]
                self.assertLessEqual(max(d), 40, f"an untagged oscillator-block over stock: {sorted(d)[-3:]}")
                self.assertGreater(min(d), 0, "control: the trampoline and an_osc's return cost something")
        print(f"\n  WT FM W1: untagged oscillator-block over stock: +{min(d)} .. +{max(d)} instructions", file=sys.stderr)

    def tier2(self, name, pushes, tagged, envs, blocks=40, reset=(1, 1), notes=None, pos=None, reader=()):
        """Tier 2 against stock (7.3): note starts at 0 and three back to back (6, 7, 8), Position fixed to 11 then moving
        (frame crossfades rewrite k + 0x60 .. 0xBF), note 120 from 24 (many wraps: k + 0x00 .. 0x5F at each). envs: per
        oscillator (atk, dec) or None (no envelope: fmk held at DEC j 0, fme 0). The runs after every block: the tag pair,
        the settings, msub = m12 = 0, mipcur HOME, wxf 0, fme the golden envelope's (p3_golden fm_env, f32). notes / pos
        (W1 review): another scenario (test_tagged_gate0_low_notes). BE4.44 W4: a feeding OSC1 keeps its frame's mean and
        peak for B (wt_frame_dc, D4): wxf's low 11 bits the key (frame | level << 8) of the frame and level OSC1's kernel
        ended the block on, wflags' WT_PK_OK set, AnProps.glide the peak (not a setting: left out here; WtFmW4 checks the
        values)."""
        notes = notes or {0: 60, 6: 60, 7: 60, 8: 60, 24: 120}
        pos = pos or (lambda b, i: 0.25 + 0.5 * i if b < 12 else ((b * 53 + 77 * i) % 256) / 255)
        kw = dict(notes=notes, pos=pos, reset=reset)
        a = wt_play(False, blocks, pushes, **kw)
        b = wt_play(True, blocks, pushes, **kw)
        self.compare(name, a, b, tagged, reader=reader)
        nb = sorted(notes)
        for i, osc in enumerate(a.oscs):
            if i not in tagged or i in reader:
                continue
            p = [0, 0, 0, 0, 0, 0, 0, FM_DEC_INF, 0]           # settings(): type, shape, sync, fmatk, glide, subfm, fm12, fmdec, envf
            for j, k, v in pushes:
                if j == i:
                    at = {K_FMATK: 3, K_SUBFM: 5, K_FM12: 6, K_FMDEC: 7}[k]
                    if (k, i) not in ((K_FM12, 0), (K_SUBFM, 1)):
                        p[at] = v
            p[8] = int(p[3] != 0 or p[7] != FM_DEC_INF)
            e1 = G.fm_env(*envs[i], blocks, notes=nb, law="f32")[1] if envs[i] else None
            k = osc - a.m.v + S.OSC_KERNEL
            feeding = i == 0 and any(j == 1 and kk == K_FM12 and v for j, kk, v in pushes)
            for blk, mem in enumerate(a.mems):
                got = list(struct.unpack_from("<BBBBHHHBB", mem, k + PROPS_AT))
                if feeding:
                    self.assertGreater(got[4], 0, f"{name}: block {blk}: B's frame peak (AnProps.glide)")
                    got[4] = 0
                self.assertEqual(got, p, f"{name}: block {blk}: OSC{i + 1}'s settings")
                mip, flags, fmk, wxf = struct.unpack_from("<BBHI", mem, k + 140)
                msub, m12, fme = struct.unpack_from("<HHf", mem, k + MSUB_AT)
                if feeding:                                     # (W4: B's frame mean and its key, D4)
                    ks = KM.St(mem[k + 0xC0:k + 0xC0 + 25])
                    self.assertEqual(wxf & 0x7FF, ks.frame | (12 - ks.len.bit_length()) << 8,
                                     f"{name}: block {blk}: wxf's key, OSC1's frame and level")
                    wxf = 0
                self.assertEqual((mip, msub, m12, wxf), (WT_HOME, 0, 0, 0), f"{name}: block {blk}: mipcur, msub, m12, wxf")
                self.assertEqual(flags, WT_FEED_B | WT_PK_OK if feeding else 0, f"{name}: block {blk}: feed-B")
                if e1 is None:
                    self.assertEqual((fmk, fme), (AN_FE_DEC_HOLD, 0.0), f"{name}: block {blk}: no envelope: held at DEC j 0")
                else:
                    self.assertEqual(fme, f32(e1[blk]), f"{name}: block {blk}: OSC{i + 1}'s envelope (fme) the golden's")
        return a, b

    def test_tagged_gate0_is_stocks(self):
        """Tier 2 (7.3), D8: an envelope with no amount on both (OSC1 Atk 3 / Dec 6, OSC2 Dec 10), and W1's amounts (OSC1 Sub
        FM 700, OSC2 FM 1>2 400: OSC1 feeds B; OSC2 Atk 2), which play as gate 0 here (no reader yet); WTOscReset on and off.
        Both oscillators stock's bit for bit outside the runs, the note-start blocks (0, three back to back, the jump to 120)
        included.
        BE4.44 W4 (plan 3.5, 7.3's B path): the feeding OSC1 writes B at gate 0 from stock's own kernel (wt_kern), so its
        samples and every byte outside the runs stay stock 1.6.8's here (the B-path identity); it is no longer tier 2's cost
        (the body and an_feed: plan 6's "stock WT1 feeding an FM'd WT2" row), so the 1.02 gate (D8) holds the envelope case,
        and the feeding OSC1-block is bounded on its own (FEED_MAX x stock's render, measured here at note 60 only: the body's
        +0.4k, wt_feed_b's about 5 a sample, and on a frame or level change B's frame mean and peak, wt_frame_dc's read of up
        to 128 samples, 256 at frame 0: W4 review). Low notes and a Position moving every block are gated as whole voices in
        WtFmW4.test_cost and WtFmW4.test_d4_the_frame_read_is_level_4_at_low_notes."""
        FEED_MAX = 1.55
        cases = (("envelope", self.PUSH_ENV, ((3, 6), (0, 10)), ()), ("feed-B", self.PUSH_FEED, (None, (2, FM_DEC_INF)), (1,)))
        d, df = [], []
        for name, pushes, envs, rd in cases:
            for reset in ((1, 1), (0, 0)):
                with self.subTest(case=name, reset=reset):
                    a, b = self.tier2(f"{name} reset={reset}", pushes, (0, 1), envs, reset=reset, reader=rd)
                    (df if rd else d).extend((x - y, y) for ca, cb in zip(a.m.counts, b.m.counts)
                                             for i, (x, y) in enumerate(zip(ca, cb)) if i not in rd)
        # the cost (plan 6 and D8: gated at 1.02 x stock's render; the trampoline and an_osc's return (+27, tier 1's), the
        # tag check, the runs, the envelope's step (fm_env_step, as on Analog))
        worst = max(d, key=lambda t: t[0] / t[1])
        wf = max(df, key=lambda t: t[0] / t[1])
        print(f"\n  WT FM W1: tagged (gate 0) oscillator-block over stock: +{min(x for x, _ in d)} .. +{max(x for x, _ in d)} "
              f"instructions, worst {1 + worst[0] / worst[1]:.4f} x stock's ({worst[0]} on {worst[1]}); W4 feeding B at gate 0: "
              f"+{min(x for x, _ in df)} .. +{max(x for x, _ in df)}, worst {1 + wf[0] / wf[1]:.4f} x", file=sys.stderr)
        self.assertLessEqual(1 + worst[0] / worst[1], 1.02, "tier 2 within 1.02 x stock's render (plan 6, D8)")
        self.assertLessEqual(1 + wf[0] / wf[1], FEED_MAX, "W4: a Wavetable OSC1 feeding B at gate 0")

    def test_stock_rewrites_every_run_so_each_restore_matters(self):
        """The control for mutation 2 (a missed restore of one run): in tier 2 stock's render itself writes into each saved
        run (code outside the DSP window, seen by a write hook): the tag pair at the wraps, the WT state word and the
        settings run at the frame changes. So a run not put back would hold stock's bytes, which test_tagged_gate0's runs
        check sees."""
        w = WtRun(False)
        for i, k, v in self.PUSH_ENV:                            # (W2: an amount would open the reader, tier 3)
            w.push(i, k, v)
        k1 = w.m.o1 + S.OSC_KERNEL
        seen = set()
        def hook(u, acc, a, size, val, _):
            pc = u.reg_read(UC_ARM_REG_PC)
            if not S.M7_DSP <= pc < S.M7_DSP_LIMIT:
                seen.update(range(a - k1, a - k1 + size))
        w.r.u.hook_add(UC_HOOK_MEM_WRITE, hook, begin=k1, end=k1 + 0xBF)
        w.m.note(120)
        for b in range(8):
            w.r.wf(w.m.o1 + OSC_POS_SRC, b / 7)
            w.m.block()
        for off, n in WT_RUNS:
            self.assertTrue(set(range(off, off + n)) <= seen, f"stock writes the whole run at +{off} ({n} B)")
        self.assertTrue(wt_tagged(w.area(0), 0, w.m.o1), "and the tag is back after it")

    def fresh(self):
        """This build, both Wavetable, the rich table, stock's renders filling the areas with table bytes (Position moving)."""
        w = WtRun(False)
        w.m.note(60)
        for b in range(4):
            for o in w.oscs:
                w.r.wf(o + OSC_POS_SRC, b / 3)
            w.m.block()
        return w

    def test_the_first_value_writes_the_default_state_first(self):
        """3.2 Tagging (mutation 21): an untagged area is stock's table history; the first value that is not a default writes
        the default state, the tag pair, then the value. A default value on an untagged oscillator writes nothing; D2: a
        Wavetable OSC1's FM 1>2 and a Wavetable OSC2's Sub FM are not kept (no write). Values clamp as on Analog."""
        w = self.fresh()
        for i in (0, 1):
            self.assertTrue(any(w.area(i)[0x60:0xC0]), f"control: OSC{i + 1}'s area holds stock's bytes")
        before = [w.area(i) for i in (0, 1)]
        for i, k, v in ((0, K_SUBFM, 0), (0, K_FMATK, -4), (0, K_FMDEC, 500), (1, K_FM12, 0), (0, K_FM12, 800), (1, K_SUBFM, 800)):
            w.push(i, k, v)
        self.assertEqual([w.area(i) for i in (0, 1)], before, "defaults and D2's leaves: nothing written")
        w.push(0, K_SUBFM, 300)
        a = w.area(0)
        t = wt_tag(w.m.o1)
        self.assertEqual(struct.unpack_from("<II", a, 0), (t, ~t & 0xFFFFFFFF), "the tag pair")
        self.assertEqual(t >> 16, 0x7FA5, "a NaN payload")
        self.assertEqual(struct.unpack_from("<BBBBHHHBB", a, PROPS_AT), (0, 0, 0, 0, 0, 300, 0, FM_DEC_INF, 0),
                         "AnProps: the defaults, then Sub FM 300")
        self.assertEqual(struct.unpack_from("<BBHI", a, 140), (WT_HOME, 0, AN_FE_DEC_HOLD, 0), "mipcur HOME, wflags 0, fmk DEC j 0, wxf 0")
        self.assertEqual(struct.unpack_from("<HHI", a, MSUB_AT), (0, 0, 0), "msub, m12, fme 0")
        self.assertEqual(a[8:140], before[0][8:140], "the rest of the area: stock's (not part of the state)")
        for k, v, at, want in ((K_SUBFM, 5000, 5, 1000), (K_SUBFM, -3, 5, 0), (K_FMATK, 999, 3, 127), (K_FMDEC, -1, 7, 0),
                               (K_FMDEC, 129, 7, FM_DEC_INF), (K_SUBFM, 437, 5, 437)):
            w.push(0, k, v)
            self.assertEqual(struct.unpack_from("<BBBBHHHBB", w.area(0), PROPS_AT)[at], want, f"{k:#x} = {v}")

    def test_the_untag_rule(self):
        """3.3 (mutations 15, 22): an oscillator untags only with no amount, no ramp memory, the mip home, no envelope (envf)
        and not feeding B; mipcur is read bounded (7.., a corrupt byte, is HOME: no out-of-table mip); a valid one (3: the
        reader holds a mip) keeps the tag."""
        w = self.fresh()
        o = w.m.o1
        tagged = lambda: wt_tagged(w.area(0), 0, o)
        w.push(0, K_SUBFM, 300); self.assertTrue(tagged())
        w.push(0, K_SUBFM, 0); self.assertFalse(tagged(), "nothing left: untagged")
        w.push(0, K_FMATK, 2); w.push(0, K_SUBFM, 300); w.push(0, K_SUBFM, 0)
        self.assertTrue(tagged(), "the envelope is set (envf): still tagged")
        w.push(0, K_FMATK, 0); self.assertFalse(tagged(), "envelope unset: untagged")
        for mip, keeps in ((9, False), (WT_MIPS_N, False), (0x80, False), (3, True), (0, True), (WT_MIPS_N - 1, True)):
            with self.subTest(mipcur=mip):
                w.push(0, K_SUBFM, 300)
                w.r.u.mem_write(o + S.OSC_KERNEL + WT_MIP_AT, bytes([mip]))
                w.push(0, K_SUBFM, 0)
                self.assertEqual(tagged(), keeps, f"mipcur {mip}: {'a mip held' if keeps else 'HOME (bounded)'}")
                w.r.u.mem_write(o + S.OSC_KERNEL + WT_MIP_AT, bytes([WT_HOME])); w.push(0, K_SUBFM, 0)
                self.assertFalse(tagged())
        w.push(0, K_SUBFM, 300)
        w.r.u.mem_write(o + S.OSC_KERNEL + MSUB_AT, struct.pack("<H", 5)); w.push(0, K_SUBFM, 0)
        self.assertTrue(tagged(), "ramp memory left (msub): still tagged")

    def test_the_tag_pair(self):
        """3.3 (mutation 1): a table word equal to the tag, with any other word after it, is not ours: stock's render runs
        (tier 1) and leaves the area its own, byte for byte, as on stock; so does a word with the tag's high half alone."""
        for name, check in (("check word wrong", lambda t: t ^ 1), ("check word zero", lambda t: 0),
                            ("high half alone", None)):
            with self.subTest(case=name):
                def each(w, b):
                    if b == 2:
                        t = wt_tag(w.m.o1)
                        x = t if check else t ^ 1                # (the high half alone: another oscillator's tag, its pair)
                        pair = struct.pack("<II", x, check(t) if check else ~x & 0xFFFFFFFF)
                        w.r.u.mem_write(w.m.o1 + S.OSC_KERNEL, pair)
                kw = dict(notes={0: 72}, pos=lambda b, i: 0.6, each=each)
                a = wt_play(False, 6, **kw)
                b = wt_play(True, 6, **kw)
                self.compare(name, a, b, ())

    def test_the_props_side_reads_the_pair_too(self):
        """3.3 (mutation 1, the props side): with the tag word and a wrong check word in the area (table bytes), a note start
        writes nothing and the first value still writes the whole default state and a good pair (the oscillator was not
        tagged); a wrong low half (another oscillator's tag) the same."""
        for name, bad in (("check word wrong", lambda t: (t, t ^ 1)), ("another oscillator's pair", lambda t: (t ^ 1, ~(t ^ 1)))):
            with self.subTest(case=name):
                w = self.fresh()
                o, t = w.m.o1, wt_tag(w.m.o1)
                w.r.u.mem_write(o + S.OSC_KERNEL, struct.pack("<II", *(x & 0xFFFFFFFF for x in bad(t))))
                before = w.area(0)
                w.m.note(60)
                self.assertEqual(w.area(0), before, "the note start: not ours, nothing written")
                w.push(0, K_FMDEC, 20)
                a = w.area(0)
                self.assertEqual(struct.unpack_from("<II", a, 0), (t, ~t & 0xFFFFFFFF), "a good pair")
                self.assertEqual(struct.unpack_from("<BBBBHHHBB", a, PROPS_AT), (0, 0, 0, 0, 0, 0, 0, 20, 1), "the defaults first")
                self.assertEqual(struct.unpack_from("<BBHI", a, 140), (WT_HOME, 0, AN_FE_DEC_HOLD, 0))

    def test_feed_b(self):
        """3.5's bit (mutation 14): a Wavetable OSC1 feeds B while OSC2 (Analog, or Wavetable and tagged) has FM 1>2; set and
        cleared by OSC2's FM 1>2 and by either Type, in any order (a project's push, a resend); clearing it leaves OSC1
        tagged for its own work; an Analog OSC1 has no WT state."""
        def state(w):
            a1, a2 = w.area(0), w.area(1)
            return (wt_tagged(a1, 0, w.m.o1), a1[WT_FLAGS_AT] if wt_tagged(a1, 0, w.m.o1) else None,
                    wt_tagged(a2, 0, w.m.o2), struct.unpack_from("<I", a2, 0)[0] == AN_MAGIC)
        w = self.fresh()
        w.push(1, K_FM12, 500)
        self.assertEqual(state(w), (True, WT_FEED_B, True, False), "both Wavetable: OSC2 keeps FM 1>2, OSC1 feeds B")
        w.push(1, K_FM12, 0)
        self.assertEqual(state(w), (False, None, False, False), "FM 1>2 0: both untagged")
        w.push(0, K_SUBFM, 200); w.push(1, K_FM12, 300); w.push(1, K_FM12, 0)
        self.assertEqual(state(w), (True, 0, False, False), "OSC1 keeps its own Sub FM, the bit cleared")
        w.push(0, K_SUBFM, 0)
        w.push(1, K_TYPE, 1); w.push(1, K_FM12, 300)
        self.assertEqual(state(w), (True, WT_FEED_B, False, True), "an Analog OSC2's FM 1>2")
        w.push(1, K_TYPE, 0)
        self.assertEqual(state(w), (False, None, False, False), "OSC2 leaves Analog (its area forgotten): cleared")
        w.push(1, K_FM12, 300)
        self.assertEqual(state(w), (True, WT_FEED_B, True, False), "the resend's FM 1>2 on the Wavetable OSC2: set again")
        w.push(0, K_TYPE, 1)
        self.assertEqual(struct.unpack_from("<I", w.area(0), 0)[0], AN_MAGIC, "OSC1 Analog: its own state, no WT bit")
        w.push(1, K_FM12, 0); w.push(1, K_FM12, 700)                # (OSC2's FM 1>2 arrives while OSC1 is Analog)
        w.push(0, K_TYPE, 0)
        self.assertEqual(state(w), (True, WT_FEED_B, True, False), "OSC1 back to Wavetable after OSC2's value: set")

    def test_the_note_start_sends_the_mip_home(self):
        """3.1 / 3.6 (mutation 20): a tagged oscillator's note start sets fmk to START (the envelope's retrigger, as on
        Analog) and mipcur to HOME (the reader holds no mip into the note's first block); an untagged one's area is not
        written (test_untagged's note starts)."""
        w = self.fresh()
        o = w.m.o1 + S.OSC_KERNEL
        w.push(0, K_FMATK, 2)
        w.r.u.mem_write(o + WT_MIP_AT, bytes([2])); w.r.u.mem_write(o + FMK_AT, struct.pack("<H", 0x4005))
        w.m.note(64)
        self.assertEqual(struct.unpack_from("<BBH", w.area(0), 140), (WT_HOME, 0, 0), "mipcur HOME, fmk START")

    def test_type_flips_keep_the_values(self):
        """7.4 (M7 side): what the M4 sends (Type, then its resend's seven: test_synth_ui) moves an oscillator's FM between
        its Analog and its Wavetable state: WT -> Analog -> WT keeps Sub FM, FM Atk, FM Dec (FM 1>2 on OSC1 is not kept on
        Wavetable: D2)."""
        w = self.fresh()
        vals = ((K_SUBFM, 600), (K_FMATK, 3), (K_FMDEC, 40))
        for k, v in vals:
            w.push(0, k, v)
        resend = lambda: [w.push(0, k, v) for k, v in ((K_SHAPE, 70), (K_SYNC, 0), (K_GLIDE, 0), (K_SUBFM, 600), (K_FM12, 0))
                          + vals[1:]]
        settings = lambda: struct.unpack_from("<BBBBHHHBB", w.area(0), PROPS_AT)
        self.assertEqual(settings(), (0, 0, 0, 3, 0, 600, 0, 40, 1))
        w.push(0, K_TYPE, 1); resend()
        self.assertEqual(struct.unpack_from("<I", w.area(0), 0)[0], AN_MAGIC)
        self.assertEqual(settings(), (1, 70, 0, 3, 0, 600, 0, 40, 1), "Analog: the resend's values")
        w.push(0, K_TYPE, 0)
        self.assertEqual(w.area(0), bytes(0xC0), "leaving Analog forgets the area")
        resend()
        self.assertTrue(wt_tagged(w.area(0), 0, w.m.o1))
        self.assertEqual(settings(), (0, 0, 0, 3, 0, 600, 0, 40, 1), "Wavetable again: the FM values, nothing of Analog's")

    def test_a_projects_push_tags_its_wavetable_oscillators(self):
        """W1 review (7.4's project load, M7 side): the push after a load (P3Identity.push: Type first, then the template's
        order, as test_native_props' test_the_push_after_a_load_reaches_the_m7 sees the M4 send it) of two Wavetable
        oscillators with FM values, over oscillators that were Analog with other values and over fresh Wavetable ones: both
        tagged, the settings stored (D2's leaves dropped: OSC1's FM 1>2, OSC2's Sub FM), OSC1 feeding B."""
        for before in ("analog", "wavetable"):
            with self.subTest(before=before):
                w = self.fresh()
                if before == "analog":
                    for i in (0, 1):
                        P3Identity.push(w.r, w.oscs[i], w.m.handle, i, (1, 70, i, 0), (900, 5, 800, 30))
                        self.assertEqual(struct.unpack_from("<I", w.area(i), 0)[0], AN_MAGIC, "control: Analog first")
                P3Identity.push(w.r, w.oscs[0], w.m.handle, 0, (0, 48, 0, 0), (400, 3, 250, 20))   # (Sub FM, FM Atk, FM 1>2, FM Dec)
                P3Identity.push(w.r, w.oscs[1], w.m.handle, 1, (0, 48, 0, 0), (600, 0, 300))
                for i, want in ((0, (0, 0, 0, 3, 0, 400, 0, 20, 1)), (1, (0, 0, 0, 0, 0, 0, 300, FM_DEC_INF, 0))):
                    a = w.area(i)
                    self.assertTrue(wt_tagged(a, 0, w.oscs[i]), f"OSC{i + 1} tagged")
                    self.assertEqual(struct.unpack_from("<BBBBHHHBB", a, PROPS_AT), want, f"OSC{i + 1}'s settings")
                self.assertEqual(w.area(0)[WT_FLAGS_AT], WT_FEED_B, "OSC1 feeds B (OSC2's FM 1>2)")

    def test_tagged_gate0_low_notes(self):
        """W1 review (3.2's "stock writes the guard before it reads it", across cycles that span many blocks): tier 2 at
        notes 24 and 36 for 48 blocks, Position fixed (few wraps, no frame change: the runs sit over live history for many
        blocks), and note 24 with Position moving slowly (frame changes between wraps). Same checks as tier2's."""
        fixed = lambda b, i: 0.25 + 0.5 * i
        for note in (24, 36):
            for name, pushes, envs, rd in (("envelope", self.PUSH_ENV, ((3, 6), (0, 10)), ()),
                                           ("feed-B", self.PUSH_FEED, (None, (2, FM_DEC_INF)), (1,))):
                with self.subTest(note=note, case=name):
                    self.tier2(f"{name} note {note}", pushes, (0, 1), envs, blocks=48, notes={0: note}, pos=fixed, reader=rd)
        with self.subTest(note=24, case="Position moving slowly"):
            self.tier2("feed-B note 24 slow Position", self.PUSH_FEED, (0, 1), (None, (2, FM_DEC_INF)), blocks=48,
                       notes={0: 24, 20: 24}, pos=lambda b, i: 0.1 + 0.01 * b + 0.3 * i, reader=(1,))

    def test_tag_and_untag_mid_note(self):
        """W1 review: a tag (wt_init over live history, between wraps) and an untag (wt_settle, magic 0) in the middle of a
        note, at notes 24 and 60. OSC1: an envelope set before the note and removed at block 9 (untagged from then); OSC2:
        Sub FM 500 at block 7 is a D2 leave (no tag), FM Atk 5 at block 13 tags it. Samples stock's bit for bit throughout;
        the voice stock's outside the runs of every oscillator tagged so far; and once a frame change and a wrap have
        passed after OSC1's untag (Position moves at block 16), OSC1's whole area is stock's again (its leftover bytes were
        all rewritten before stock read them)."""
        def each(w, b):
            if b == 7:
                w.push(1, K_SUBFM, 500)
            if b == 9:
                w.push(0, K_FMATK, 0); w.push(0, K_FMDEC, FM_DEC_INF)
            if b == 13:
                w.push(1, K_FMATK, 5)
        pushes = ((0, K_FMATK, 3), (0, K_FMDEC, 6))
        tagged = lambda blk: ({0} if blk < 9 else set()) | ({1} if blk >= 13 else set())
        masked = lambda blk: {0} | ({1} if blk >= 13 else set())
        pos = lambda b, i: (0.3 if b < 16 else 0.8) + 0.1 * i
        for note in (24, 60):
            with self.subTest(note=note):
                kw = dict(notes={0: note}, pos=pos)
                a = wt_play(False, 40, pushes, each=each, **kw)
                b = wt_play(True, 40, pushes, each=lambda w, b: None, **kw)
                self.compare(f"tag/untag note {note}", a, b, tagged, masked)
                k = a.oscs[0] - a.m.v + S.OSC_KERNEL
                self.assertEqual(a.mems[-1][k:k + 0xC0], b.mems[-1][k:k + 0xC0], "OSC1's area: stock's again")

    def test_only_render_and_note_start_write_a_tagged_area(self):
        """W1 review (the saved-run design's premise): of stock's code, only the Wavetable oscillator's render (its runs put
        back by wt_fm_osc) and its note start (WtFmW1's note-start blocks) write a tagged area. S: the kernel's functions on
        osc + 0x10C (its read, the one the render calls on it, and its reset, the one the note start calls) are called
        from three places in all of stock's M7 code: the render, the note start and the ctor (a linear sweep at both
        halfword alignments). M, a write hook on a tagged OSC1 (runs and samples settled): stock's slot 8 (every key it
        handles, 0x03 and 0xC4 .. 0xCC, values 0 .. 1000), the class's other slots (3 .. 15, render and note start aside;
        slot 3, the base class's note-on, reaches the note start through slot 2) and the ctor write nothing there: the tag
        pair and the settings stay as they were, the state but for the note start's START / HOME. A table change is no write: the
        table is the object at osc + 0x18, read by the render."""
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        md.skipdata = True
        def kernel_call(fn):
            """The bl right after fn's add.w r0, rN, #0x10c: (its address, its target)."""
            body = list(md.disasm(STOCK_IMG[fn - BASE:fn - BASE + 0x200], fn))
            j = next(j for j, i in enumerate(body) if i.mnemonic == "add.w" and i.op_str.endswith(", #0x10c"))
            bl = next(i for i in body[j:] if i.mnemonic == "bl")
            return bl.address, int(bl.op_str[1:], 16)
        (r_at, read), (n_at, reset), (c_at, c_tgt) = (kernel_call(f) for f in (S.WT_OSC_RENDER, S.WT_NOTE_START, S.WT_OSC_CTOR))
        self.assertEqual(c_tgt, reset, "the ctor calls the reset too")
        self.assertNotEqual(read, reset)
        calls = {read: set(), reset: set()}
        for off in (0, 2):
            for i in md.disasm(STOCK_IMG[off:S.M7_DSP - BASE], BASE + off):
                if i.mnemonic in ("bl", "b.w", "b") and i.op_str.startswith("#") and int(i.op_str[1:], 16) in calls:
                    calls[int(i.op_str[1:], 16)].add(i.address)
        self.assertEqual(calls, {read: {r_at}, reset: {n_at, c_at}},
                         "the kernel's read (the render) and reset (note start, ctor): no other caller")
        w = WtRun(False)
        for i, k, v in self.PUSH_AMT:
            w.push(i, k, v)
        w.m.note(60)
        for _ in range(3):
            w.m.block()
        o = w.m.o1
        k1 = o + S.OSC_KERNEL
        seen = []
        def hook(u, acc, a, size, val, _):
            if not S.M7_DSP <= u.reg_read(UC_ARM_REG_PC) < S.M7_DSP_LIMIT:
                seen.append((cur, a - k1))
        w.r.u.hook_add(UC_HOOK_MEM_WRITE, hook, begin=k1, end=k1 + 0xBF)
        before = w.area(0)
        scratch = w.r.alloc(0x400)
        w.r.u.mem_write(scratch, struct.pack("<I", 60) + bytes(0x3FC))
        vt = w.r.r32(o)
        for key in (0x03,) + tuple(range(0xC4, 0xCD)):
            for val in (0, 1, 64, 127, 255, 1000):
                cur = f"slot 8 key {key:#x} = {val}"
                w.r.prop(o, key, val, w.m.handle, 0)
        ran = 0
        for slot in range(3, 16):
            cur = f"slot {slot}"
            w.r.call(w.r.r32(vt + 4 * slot) & ~1, o, scratch, scratch, sargs=(scratch, scratch, scratch))
            ran += 1
        cur = "ctor"
        w.r.call(S.WT_OSC_CTOR, o)
        self.assertEqual(seen, [], "no stock write into the tagged area")
        # slot 3 is the base class's note-on: it reaches the note start (slot 2: an_note, then stock's), so fmk is START (0)
        # and mipcur HOME, as at any note start; every other byte as it was
        want = bytearray(before)
        want[WT_MIP_AT] = WT_HOME
        want[FMK_AT:FMK_AT + 2] = bytes(2)
        want[WXF_AT:WXF_AT + 4] = bytes(4)                  # (W4: an_note drops B's frame mean and peak, wt_frame_dc)
        want[WT_FLAGS_AT] &= ~WT_PK_OK
        self.assertNotEqual(bytes(want), before, "control: the note start had something to reset")
        self.assertEqual(w.area(0), bytes(want), "the area as it was (tag pair, state, settings), but for the note start's")
        self.assertEqual(ran, 13, "control: every slot ran")

    def test_wt_fm_osc_stays_out_of_line(self):
        """Plan 6 (mutation 16's inlining form, W1 review): all WT FM work stays in noinline wt_fm_osc; an_osc only branches
        to it. Inlining it costs the Analog path nothing measurable (M at W1), so P3Identity's cost cannot see it; this does."""
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        def targets(name):
            """Direct branch targets in the function, and the addresses it builds with movw / movt (FN's calls: blx rN)."""
            lo, hi = sym_range(name)
            got, half = set(), {}
            for i in md.disasm(IMG[lo - BASE:hi - BASE], lo):
                ops = i.op_str.split(", ")
                if i.mnemonic in ("bl", "b.w", "b") and i.op_str.startswith("#"):
                    got.add(int(i.op_str[1:], 16))
                elif i.mnemonic == "movw" and len(ops) == 2:
                    half[ops[0]] = int(ops[1].lstrip("#"), 16)
                elif i.mnemonic == "movt" and len(ops) == 2 and ops[0] in half:
                    got.add((half.pop(ops[0]) | int(ops[1].lstrip("#"), 16) << 16) & ~1)
            return got
        self.assertIn(S.WT_OSC_RENDER, targets("wt_fm_osc"), "control: wt_fm_osc calls stock's render body")
        osc = targets("an_osc")
        self.assertIn(dsp_sym("wt_fm_osc"), osc, "an_osc branches to wt_fm_osc")
        self.assertNotIn(S.WT_OSC_RENDER, osc, "an_osc itself never calls stock's render body (no WT FM body inside)")


AN_FE_DEC_HOLD = 2 << 13    # AnOsc.fmk at DEC j 0: the envelope held at 1 (analog.h AN_FE_DEC << 13)
WT_PK_OK = 4                # analog.h (W4): wflags, a feeding OSC1's frame mean (wxf) and peak (AnProps.glide) set
WT_MIP_WAIT = 2             # analog.h: wflags, a mip change deferred for a frame change (W2 review)
WT_OSC_RATE = 0x1E8          # analog.cpp OSC_RATE: the oscillator's playback rate, as stock writes it
WT_MIPS_N = 7               # analog.h WT_MIPS


# ---- BE4.44 W2 (docs/plan-wt-fm.md 3.4, 7.1, 7.2, 7.4, D5, D9 (b)): the reader, tier 3 ------------------------------------
import wt_kmodel as KM                                     # 7.2: stock's kernel, a bit-exact model (re-checked below)
WT_KERN = 0x080A2000          # stock 1.6.8's kernel read (S_OSC_KERNEL's note: the render's bl at 0x08081D5E)  # addr-ok: stock kernel (W2)
WT_MIPB_RESET, WT_MIPB_RUN, WT_MIPB_TAPS = 0x08113F10, 0x08113DF0, 0x08113EF8   # addr-ok: stock M4 mip builder (W2 trace)
WT_THR = MAN["wt_mip"] if MAN["wt_mip"] == "off" else float(MAN["wt_mip"])   # D5: this build's threshold, the manifest's
                                           # only (build.py BE_WT_MIP; no copy here); W3: "off", never coarser than stock's level
wt_rule = unittest.skipIf(WT_THR == "off", "BE_WT_MIP=off: no mip rule to test (WtFmW3.test_mip_off tests that build)")
TOL_RD = {32: 2e-6, 256: 2e-7}   # 7.1: plain blocks, device vs golden at len 32 / 256 (prototype 1.8e-6 / 1.9e-7)
# 7.1's named bounds, set at W2 from the measurements (M, printed by test_the_reader_is_the_golden; about 2x, never loosened)
TOL_RD_2048, TOL_RD_TZ, TOL_RD_XM, TOL_RD_XF = 2e-7, 4e-7, 4e-7, 4e-7
TOL_RD_CLAMP = 6e-6              # W2 review: the clamp row, len 32 through zero at 0.45 a sample both ways (M 2.7e-6)
WT_STEP_MAX = 0.01               # 7.4: the step at a switch (WtFmW2.step_at_switches); the plan's 0.02 tightened at the W2 review
                                 # (M worst 0.0040; a skipped mip-home block steps 0.016 .. 0.042: red on all but one table)


def wt_smooth_frames():
    """256 frames, 8 harmonics whose mix moves with the frame (7.1's tolerance table: smooth, as the prototype's)."""
    x = np.arange(2048) / 2048
    f = np.arange(256)[:, None] / 255
    w = sum((0.6 / h) * (1 - f if h % 2 else f + 0.2) * np.sin(2 * np.pi * h * x + h) for h in range(1, 9))
    return w.astype(np.float32)


class WtRd:
    """The reader (wt_play) called directly on WT1 of a device-shaped Rig: its kernel state and mipcur set by hand, the table
    built by stock's builder (p3_golden.wt_build_levels) from 256 level-0 frames, in Rig.tabled's region; the modulators
    written as float32."""
    def __init__(self, frames=None, img=IMG):
        self.r = r = Rig(img=img, dev=True)
        self.root, self.v = r.voice(0x77)
        self.osc = r.osc(self.v, 0)
        r.tabled(self.osc)
        self.tab = G.wt_build_levels(wt_smooth_frames() if frames is None else frames)
        r.u.mem_write(r.tab, self.tab.tobytes())
        self.k = self.osc + S.OSC_KERNEL
        self.out, self.a, self.b = r.alloc(4 * 1024), r.alloc(4 * 256), r.alloc(4 * 256)
        self.fi, self.blk = r.alloc(32), r.alloc(32)

    def set(self, st):
        self.r.u.mem_write(self.k + 0xC0, KM.St(struct.pack(KM.St.FMT, st["frac"], st["idx"], st["frame"], st["off"], st["D"],
                                                          st["len"], st["flag"])).pack())
        fl = self.r.u.mem_read(self.k + WT_FLAGS_AT, 1)[0] & ~WT_MIP_WAIT
        self.r.u.mem_write(self.k + WT_MIP_AT, bytes([st["mip"], fl | (WT_MIP_WAIT if st.get("wait") else 0)]))

    def get(self):
        s = KM.St(self.r.u.mem_read(self.k + 0xC0, 25))
        m, fl = self.r.u.mem_read(self.k + WT_MIP_AT, 2)
        return dict(frac=s.frac, idx=s.idx, frame=s.frame, off=s.off, D=s.D, len=s.len, flag=s.flag, mip=m,
                    wait=int(bool(fl & WT_MIP_WAIT)))

    def block(self, req, rate, n=256, line=None, pa=0.0):
        """(samples, state after, instructions) of one wt_play call; pa: A's known peak (WtBlk.pa; 0: scanned)."""
        fi = 0
        if line is not None:
            a, ia, dia, b, ib, dib = line
            self.r.u.mem_write(self.a, np.asarray(a, "<f4").tobytes()); self.r.u.mem_write(self.b, np.asarray(b, "<f4").tobytes())
            # (W4: one route as fm_setup forms it, b = a at a zero line: the reader's one-route loops take it)
            one = b is a and (ib, dib) == (0.0, 0.0)
            self.r.u.mem_write(self.fi, struct.pack("<IffIIff", self.a, ia, dia, 0, self.a if one else self.b, ib, dib))
            fi = self.fi
        self.r.u.mem_write(self.blk, struct.pack("<IIIfIIf", self.r.tab, fi, self.out, rate, n, req, pa))
        _, c = self.r.call(dsp_sym("wt_play"), self.osc, self.blk)
        return np.frombuffer(bytes(self.r.u.mem_read(self.out, 4 * n)), "<f4").astype(np.float64), self.get(), c

    def stock(self, req, rate, n=256):
        """Stock's kernel read on the same state (the handback's next block)."""
        self.r.u.mem_write(self.out, b"\xAA" * (4 * n))
        self.r.call(WT_KERN, self.k, self.r.tab, req, self.out, sargs=(n,), fargs=(rate,))
        return np.frombuffer(bytes(self.r.u.mem_read(self.out, 4 * n)), "<f4").astype(np.float64), self.get()


def wt_line(inc, n, I0, I1, ratio=0.5, ph=0.0, J=0.0, bratio=1.5, J1=None):
    """A modulator (a sine at ratio x the carrier, in increment units: inc_m sin) and its index line I0 -> I1 over the block
    (fm_setup's form: dia = f32(f32(I1 - I0) / n)); J: a second route (FM 1>2's B) at index J (-> J1), on its own buffer
    (W2 review: a sine at bratio x the carrier, another phase and peak, so a b read from a, or B's peak taken from A, shows);
    J 0: one route, b = a at a zero line (fm_setup's form)."""
    inc_m = inc * ratio
    a = (np.sin(2 * np.pi * (ph + inc_m * np.arange(n))) * inc_m).astype(np.float32)
    f = np.float32
    dia = 0.0 if I1 == I0 else float(f(f(f(I1) - f(I0)) / f(n)))
    if not J and not J1:
        return (a, float(f(I0)), dia, a, 0.0, 0.0)
    J1 = J if J1 is None else J1
    inc_b = inc * bratio
    b = (np.sin(2 * np.pi * (ph * 3 + 0.3 + inc_b * np.arange(n))) * inc_b).astype(np.float32)
    dib = 0.0 if J1 == J else float(f(f(f(J1) - f(J)) / f(n)))
    return (a, float(f(I0)), dia, b, float(f(J)), dib)


def wt_phase(st): return ((st["idx"] + st["frac"]) / st["len"]) % 1.0


def wt_rate(note): return f32(440 * 2 ** ((note - 69) / 12) * 2048 / 96000)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class WtFmW2(unittest.TestCase):
    """BE4.44 W2 (docs/plan-wt-fm.md 3.4, 7.1, 7.2, 7.4, D5, D9 (b)): the reader. The trace of stock's mip builder (its levels
    are not phase-aligned: p3_golden's wt_skew, read by the reader), the reader against p3_golden.wt_read_block (7.1), the
    handback against stock's kernel (wt_kmodel, 7.2), the continuity rows on synthetic and builder-made tables (7.4), the mip
    rule with its hysteresis and the one-change rule, the pending flag, frac 1.0, level 0, D5's threshold, sizes and costs."""

    def test_the_mip_builder_is_stocks(self):
        """W2 trace (S, then M here): stock's M4 decimator (reset 0x08113F10: 12 zeros; run 0x08113DF0: 11 taps, one output
        per two inputs, the level as one stream across frames), emulated on 4096 random samples in 1024-sample chunks as the
        builder feeds it, equals p3_golden.wt_build_levels's level step bit for bit; its taps are STOCK_FIR."""
        taps = struct.unpack("<6f", STOCK_IMG[WT_MIPB_TAPS - BASE:WT_MIPB_TAPS - BASE + 24])
        self.assertEqual(tuple(G.STOCK_FIR), taps[:5] + taps[5:6] + taps[4::-1], "the taps, read off the image")
        r = Rig(img=STOCK_IMG)
        def bufobj(cap):
            o, d = r.alloc(0x20), r.alloc(4 * cap)
            r.u.mem_write(o, struct.pack("<4I", cap, 0, d, r.alloc(4 * cap)))
            return o, d
        hist, _ = bufobj(4096); inp, ind = bufobj(1024); out, outd = bufobj(1024)
        r.call(WT_MIPB_RESET, hist)
        x = np.random.default_rng(1).uniform(-1, 1, 4096).astype(np.float32)
        ys = []
        for o in range(0, 4096, 1024):
            r.u.mem_write(ind, x[o:o + 1024].tobytes()); r.w32(inp + 4, 1024); r.w32(out + 4, 0)
            r.call(WT_MIPB_RUN, hist, inp, out)
            ys.append(np.frombuffer(bytes(r.u.mem_read(outd, 4 * r.r32(out + 4))), np.float32))
        frames = np.zeros((256, 2048), np.float32); frames.reshape(-1)[:4096] = x
        want = G.wt_build_levels(frames)[0x80000:0x80000 + 2048]
        self.assertTrue(np.array_equal(np.concatenate(ys), want), "stock's decimator = the golden's level step")

    def test_kmodel_is_stocks_kernel(self):
        """7.2's oracle re-checked: wt_kmodel.kernel equals stock's kernel (emulated) bit for bit, samples and state, over a
        note start, a fixed and a moving frame, three rates (D 1, 4, 32)."""
        w = WtRd()
        for rate in (0.9, 5.58131, 44.65):
            w.set(G.wt_state())
            m = KM.St(w.r.u.mem_read(w.k + 0xC0, 25))
            for b in range(10):
                req = 7 if b < 5 else 7 + 31 * b
                y, st = w.stock(req, rate)
                want = KM.kernel(m, w.tab, req, rate, 256)
                self.assertEqual(list(y.astype(np.float32)), [np.float32(v) for v in want], f"rate {rate} block {b}")
                self.assertEqual(m.pack(), KM.St(w.r.u.mem_read(w.k + 0xC0, 25)).pack(), f"rate {rate} block {b}: state")

    def check(self, w, st, req, rate, line, tol, what, n=256, pa=0.0, ph_tol=1e-7):
        """One wt_play block against the golden: samples within tol, the phase within 1e-7 of a cycle, frame, mipcur, the
        mip-wait bit, and off / D / len / the flag as the golden leaves them (mutation 3: the reader never writes the levels
        it reads)."""
        w.set(st)
        y, got, _ = w.block(req, rate, n, line, pa)
        want, gst, ks = G.wt_read_block(w.tab, st, req, rate, n, line, WT_THR, pa=pa)
        err = float(np.max(np.abs(y - want)))
        self.assertLessEqual(err, tol, f"{what}: max |device - golden| {err:.2e} (levels {ks})")
        d = abs(wt_phase(got) - wt_phase(gst)); d = min(d, 1 - d)
        self.assertLess(d, ph_tol, f"{what}: phase")
        for f in ("frame", "mip", "wait", "off", "D", "len", "flag"):
            self.assertEqual(got[f], gst[f], f"{what}: {f}")
        return err, ks

    @wt_rule
    def test_the_reader_is_the_golden(self):
        """7.1: plain blocks (index ramps, both routes) at len 32 / 256 / 2048 within TOL_RD / TOL_RD_2048; a through-zero
        block (TOL_RD_TZ), a mip-change block (rd_qm, TOL_RD_XM), a frame-change block (rd_qx, TOL_RD_XF); a frame change
        that would also change the mip changes the frame only (one change a block)."""
        w = WtRd()
        rows = []
        for k, note in ((6, 115), (3, 60), (0, 24)):
            rate = wt_rate(note)
            self.assertEqual(G.wt_ladder(rate), k, "control: the note's level")
            inc = float(f32(rate) * f32(1 / 2048))
            for name, line in (("ramp", wt_line(inc, 256, 0.1, 0.3)), ("two routes", wt_line(inc, 256, 0.2, 0.15, 0.25, 0.3, 0.1))):
                st = G.wt_state(idx=5, frac=0.375, frame=40, flag=0, k=k)
                err, ks = self.check(w, st, 40, rate, line, TOL_RD.get(2048 >> k, TOL_RD_2048), f"len {2048 >> k} {name}")
                self.assertEqual(ks, (k, k), "control: a plain block")
                rows.append((f"len {2048 >> k} {name}", err))
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048))
        st = G.wt_state(idx=100, frac=0.5, frame=3, flag=0, k=3)
        line = wt_line(inc, 256, 3.0, 3.0, 2.0)
        self.assertLess(float(np.min(inc + 3.0 * line[0].astype(np.float64))), -2 * inc, "control: through zero")
        err, ks = self.check(w, dict(st, mip=6), 3, rate, line, TOL_RD_TZ, "through zero (mip held at 6)")
        rows.append(("through zero", err))
        err, ks = self.check(w, st, 3, rate, wt_line(inc, 256, 0.0, 6.0), TOL_RD_XM, "mip change (rd_qm)")
        self.assertGreater(ks[1], ks[0], "control: the mip changed")
        rows.append(("mip change", err))
        err, ks = self.check(w, st, 200, rate, wt_line(inc, 256, 0.5, 0.5), TOL_RD_XF, "frame change (rd_qx)")
        rows.append(("frame change", err))
        err, ks = self.check(w, st, 200, rate, wt_line(inc, 256, 0.0, 6.0), TOL_RD_XF, "frame and mip wanted (one change)")
        self.assertEqual(ks, (3, 3), "the frame changes, the mip waits (mutation 24)")
        self.assertEqual(w.get()["mip"], 3)
        err, ks = self.check(w, dict(st, frame=200, mip=3, wait=1), 200, rate, wt_line(inc, 256, 6.0, 6.0), TOL_RD_XM,
                             "the next block")
        self.assertGreater(ks[1], 3, "the deferred mip change comes next block")
        # W2 review: B on its own buffer (wt_line's b: another ratio, phase and peak). FM 1>2 alone moves the level (I 0, the
        # J term only: mutation "J dropped from the peak"; "B's peak read from A": b's peak is 3x a's), and a two-route
        # block whose B term carries the motion (mutation "B read from A")
        line = wt_line(inc, 256, 0.0, 0.0, J=6.0)
        self.assertGreater(float(np.max(np.abs(line[3]))), 2.5 * float(np.max(np.abs(line[0]))), "control: B's peak is not A's")
        err, ks = self.check(w, st, 3, rate, line, TOL_RD_XM, "FM 1>2 alone moves the level")
        self.assertGreater(ks[1], ks[0], "control: B alone changed the level")
        _, _, ka = G.wt_read_block(w.tab, st, 3, rate, 256, line[:3] + (line[0],) + line[4:], WT_THR)
        self.assertNotEqual(ka, ks, "control: B's peak taken from A picks another level")
        rows.append(("B alone (mip change)", err))
        line = wt_line(inc, 256, 0.3, 0.3, J=1.2)
        err, ks = self.check(w, st, 3, rate, line, TOL_RD[256], "two routes, B's term the larger")
        ya, _, _ = G.wt_read_block(w.tab, st, 3, rate, 256, line[:3] + (line[0],) + line[4:], WT_THR)
        yb, _, _ = G.wt_read_block(w.tab, st, 3, rate, 256, line, WT_THR)
        self.assertGreater(float(np.max(np.abs(ya - yb))), 100 * TOL_RD[256], "control: B read from A is another block")
        rows.append(("two routes, B", err))
        # the clamp at 0.45 (plan 7.4): a step beyond it in both directions (note 115, a modulator at the carrier)
        rate = wt_rate(115); inc = float(f32(rate) * f32(1 / 2048))
        line = wt_line(inc, 256, 16.0, 16.0, 1.0)
        dmax = float(np.max(np.abs(inc + 16.0 * line[0].astype(np.float64))))
        self.assertGreater(dmax, 0.6, "control: the step goes past the clamp")
        err, ks = self.check(w, G.wt_state(idx=3, frac=0.5, frame=9, flag=0, k=6), 9, rate, line, TOL_RD_CLAMP, "the clamp",
                             ph_tol=5e-7)         # (f32 steps near 0.45 a sample: M 2.2e-7 of a cycle in 256)
        rows.append(("clamp", err))
        print("\n  WT FM W2 reader vs golden: " + ", ".join(f"{n} {e:.1e}" for n, e in rows), file=sys.stderr)

    def test_pending_flag_and_frac_one(self):
        """Mutation 5: a note's first block through the reader runs stock's ladder (D, len, off, idx %= len, the flag
        cleared) and takes the frame (-1) with no crossfade. Mutation 18: frac 1.0 (f32 rounding of stock's Q28) is read as
        the next sample."""
        w = WtRd()
        for note in (24, 60, 100):
            rate = wt_rate(note); inc = float(f32(rate) * f32(1 / 2048))
            self.check(w, G.wt_state(idx=3001), 77, rate, wt_line(inc, 256, 1.0, 2.0), TOL_RD_XM, f"note start {note}")
            self.assertEqual(w.get()["len"], 2048 >> G.wt_ladder(rate))
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048))
        self.check(w, G.wt_state(idx=17, frac=1.0, frame=5, flag=0, k=3), 5, rate, wt_line(inc, 256, 0.2, 0.2), TOL_RD[256], "frac 1.0")

    @wt_rule
    def test_hysteresis_band(self):
        """W2 review: the band's value (WT_MIP_HYST 0.84) pinned from both sides. Held at kn + 2 with the peak at 0.90 x
        WT_THR x 2^(kn + 1) (inside the band one level down): stays (0.95 would step down); at 0.78 (below the band one
        level down, above it two down): steps to kn + 1 (0.70 would stay). A's peak known (pa), so the peak is exact."""
        w = WtRd()
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
        line = wt_line(inc, 256, 1.0, 1.0)
        pa = float(f32(inc * 0.5))
        for frac, want in ((0.90, kn + 2), (0.78, kn + 1)):
            x = frac * WT_THR * 2 ** (kn + 1)                       # the peak, in base-table samples a sample
            I = (x / 2048 - inc) / pa
            ln = (line[0], I, 0.0, line[0], 0.0, 0.0)
            px = float(G.wt_peak(inc, ln, 256, pa)) / (WT_THR * 2 ** (kn + 1))
            self.assertAlmostEqual(px, frac, delta=1e-4, msg="control: the peak where it is meant to be")
            _, ks = self.check(w, G.wt_state(idx=9, frac=0.1, frame=0, flag=0, k=kn, mip=kn + 2), 0, rate, ln,
                               TOL_RD[32], f"band at {frac}", pa=pa)          # (levels 4..5: len 128 / 64)
            self.assertEqual(ks[1], want, f"peak {frac} x thr x 2^(kn + 1) from kn + 2")

    def test_low_notes_hold_their_level(self):
        """W2 review: at notes 24 and 36 the sub's period spans blocks; with A's known peak (pa) a steady Sub FM keeps one
        level (the scan of A followed the sub's phase: a third of the blocks were mip changes, M). 64 blocks, the sub's
        phase running on: at most one level change (the first block's)."""
        w = WtRd(G.wt_saw_frames(200))
        for note in (24, 36):
            rate = wt_rate(note); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
            for v in (400, 800):
                I = G.an_index32(v); pa = float(f32(inc * 0.5))
                w.set(G.wt_state(idx=9, frac=0.1, frame=0, flag=0, k=kn))
                mips = []
                for b in range(64):
                    _, got, _ = w.block(0, rate, 256, wt_line(inc, 256, I, I, ph=0.5 * inc * 256 * b), pa)
                    mips.append(got["mip"])
                ch = sum(1 for p, q in zip(mips, mips[1:]) if p != q)
                self.assertLessEqual(ch, 1, f"note {note} Sub FM {v}: {mips}")

    @wt_rule
    def test_hysteresis(self):
        """Mutation 23 (D5's derived band): a peak parked between WT_THR x 0.84 and WT_THR (x 2^k) keeps the coarser level
        held, block after block; a peak moving +-1 % across the threshold changes the level at most twice in 16 blocks."""
        w = WtRd()
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
        x0 = rate / 2 ** kn                                   # the peak in level-kn steps at index 0
        # I such that the peak sits at 0.92 x WT_THR x 2^(kn + 1) with the sub (ratio 0.5): x = rate (1 + I / 2)
        I = 2 * (0.92 * WT_THR * 2 / x0 - 1)
        st = G.wt_state(idx=9, frac=0.1, frame=0, flag=0, k=kn, mip=kn + 1)
        w.set(st)
        for b in range(8):
            _, got, _ = w.block(0, rate, 256, wt_line(inc, 256, I, I, ph=0.37 * b))
            self.assertEqual(got["mip"], kn + 1, f"block {b}: held at the coarser level")
        I = 2 * (WT_THR * 2 / x0 - 1)
        w.set(dict(st, mip=kn))
        mips = []
        for b in range(16):
            j = I * (1 + 0.01 * math.sin(b))
            _, got, _ = w.block(0, rate, 256, wt_line(inc, 256, j, j))
            mips.append(got["mip"])
        self.assertLessEqual(sum(1 for p, q in zip(mips, mips[1:]) if p != q), 2, f"no toggling: {mips}")

    @wt_rule
    def test_position_every_block(self):
        """W2 review (plan 7.4 "Position LFO with a decaying FM Dec"): Position one frame on every block, Sub FM attacking to
        a heavy index and then (a) cut to 0 (the ramp-out block, then the mip-home block) or (b) decaying over six blocks.
        Every block is the golden's (samples, phase, frame, mipcur, the wait bit); the frame playing is never more than one
        block behind Position; under the heavy index the mip leaves the note's level; after the line ends mipcur is HOME
        within two blocks, and never HOME while the reader is still on another level. The first W2 rule (a frame change
        always wins; the golden's "no_wait") keeps the mip at the note's level throughout, so the control below must fail
        on it."""
        w = WtRd(G.wt_saw_frames(200))
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
        cut = [(0.0, 12.0), (12.0, 12.0), (12.0, 12.0), (12.0, 12.0), (12.0, 0.0)] + [None] * 4
        dec = [(0.0, 12.0)] + [(12.0 - 2 * b, 10.0 - 2 * b) for b in range(6)] + [None] * 4
        for name, plan in (("cut", cut), ("decay", dec)):
            for mut in (None, "no_wait"):
                st = G.wt_state(idx=11, frac=0.3, frame=5, flag=0, k=kn)
                mips, frames = [], []
                for b, p in enumerate(plan):
                    line = None if p is None else wt_line(inc, 256, p[0], p[1], ph=0.5 * inc * 256 * b)
                    if mut is None:
                        self.check(w, st, 6 + b, rate, line, max(TOL_RD_XM, TOL_RD_XF), f"{name} block {b}")
                        st = w.get()
                    else:
                        _, st, _ = G.wt_read_block(w.tab, st, 6 + b, rate, 256, line, WT_THR, mutant=mut)
                    mips.append(st["mip"]); frames.append(st["frame"])
                end = plan.index(None)
                ok = max(m for m in mips[:end] if m != G.WT_HOME) > kn and mips[end + 1] == G.WT_HOME
                if mut:
                    self.assertFalse(ok, f"control: {mut} starves the mip ({name}: {mips})")
                    continue
                self.assertTrue(ok, f"{name}: the mip left the note's level under FM and came home in 2 blocks: {mips}")
                self.assertTrue(all(6 + b - f <= 1 for b, f in enumerate(frames)), f"{name}: frames {frames}")
                self.assertNotIn(G.WT_HOME, mips[:end], name)

    # ---- W2 review: through the render, on stock's root with a sounding sub (SubCopy's voice, both oscillators Wavetable) --
    def root_voice(self, wave=4, note=60, pw=300):
        """Stock's root and voice (SubCopy.voice: the sub sounds, so buffer A carries it), both oscillators Wavetable with
        Rig.tabled's table; render(c) one block through the root's voice render (slot 0x48), returning the block's bytes."""
        r = Rig()
        root, v, sub, _ = SubCopy.voice(self, r, wave, 1000, fm=(0, 0), types=(0, 0), glide=0, pw=pw, fine=0)
        o1, o2 = v + S.VOICE_WT1, v + S.VOICE_WT2
        r.tabled(o1); r.tabled(o2)
        r.v = v
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        c = dict(r=r, root=root, v=v, sub=sub, o=(o1, o2), m=m, acc=r.buf(DEV_N * m, 1024), scr=r.buf(DEV_N * m, 1024),
                 out=r.buf(DEV_N, 1024), slot18=r.r32(r.r32(root) + 0x48))
        SubCopy.note(self, r, v, sub, note)
        return c

    @staticmethod
    def root_block(c):
        r, (acc, ad), (scr, _), (out, od) = c["r"], c["acc"], c["scr"], c["out"]
        r.w32(acc + 4, DEV_N * c["m"]); r.w32(scr + 4, DEV_N * c["m"]); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
        r.call(c["slot18"] & ~1, c["root"], c["v"], 0, scr, sargs=(acc, 0, out))
        y = bytes(r.u.mem_read(od, 4 * DEV_N))
        r.block()
        return y

    @wt_rule
    def test_mip_home_through_the_render(self):
        """W2 review (mutation 7 in its natural form: the mip term dropped from wt_fm_osc's gate): a Wavetable OSC1 with Sub
        FM 1000 from a sounding Sine sub goes to a coarse level, holds it through the ramp-out block (Sub FM 0), then plays
        the mip-home block through the reader (the gate held open by mipcur alone) and is HOME after it, and only then."""
        c = self.root_voice()
        r, o1 = c["r"], c["o"][0]
        kn = G.wt_ladder(struct.unpack("<f", r.u.mem_read(o1 + WT_OSC_RATE, 4))[0])
        mips = []
        for b in range(10):
            if b in (2, 6):
                r.root_prop(c["root"], K_SUBFM, 1000 if b == 2 else 0, 0)
            self.root_block(c)
            mips.append(r.u.mem_read(o1 + S.OSC_KERNEL + WT_MIP_AT, 1)[0])
        self.assertTrue(all(m > kn and m != G.WT_HOME for m in mips[2:7]), f"coarse under FM and its ramp-out: {mips}")
        self.assertEqual(mips[7:], [G.WT_HOME] * 3, f"HOME after the mip-home block: {mips}")

    @wt_rule
    def test_a_peak_known_through_the_body(self):
        """W2 review: the body hands wt_play A's known peak, inc_sub x the sub wave's (1 for the Sine; the Square's DC-free
        1 + |1 - 2 pw|, from the width its core plays): at a steady Sub FM the level is the golden's choice for that
        peak (exact: no scan). The Square's factor moves the level at some amount (control)."""
        moved = False
        for wave, pw in ((4, 300), (3, 100)):
            for v in (250, 400, 600):
                c = self.root_voice(wave, pw=pw)
                r, o1 = c["r"], c["o"][0]
                r.root_prop(c["root"], K_SUBFM, v, 0)
                for b in range(4):
                    self.root_block(c)
                rate = struct.unpack("<f", r.u.mem_read(o1 + WT_OSC_RATE, 4))[0]
                inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
                inc_sub = struct.unpack("<f", r.u.mem_read(SA + SA_RUN + RUN_INC_SUB, 4))[0]
                pk = 1.0
                if wave == 3:
                    w = r.r32(c["sub"] + 0x20 + 0x18)
                    pk = float(f32(1) + abs(f32(1) - f32(w) * f32(4.6566128730773926e-10)))
                pa = float(f32(f32(inc_sub) * f32(pk)))
                z = np.zeros(256)
                x = G.wt_peak(inc, (z, G.an_index32(v), 0.0, z, 0.0, 0.0), 256, pa)
                want = G.wt_mip_for(x, WT_THR, kn)
                got = r.u.mem_read(o1 + S.OSC_KERNEL + WT_MIP_AT, 1)[0]
                self.assertEqual(got if got != G.WT_HOME else kn, want, f"wave {wave} Sub FM {v}: peak {x}")
                if wave == 3 and G.wt_mip_for(G.wt_peak(inc, (z, G.an_index32(v), 0.0, z, 0.0, 0.0), 256, inc_sub),
                                              WT_THR, kn) != want:
                    moved = True
        self.assertTrue(moved, "control: the Square's factor changes a level")

    def test_no_line_plays_stocks_render(self):
        """W2 review: an amount that plays nothing yet (OSC2's FM 1>2: no B from a Wavetable OSC1 until W4) opens the gate,
        but with no line and no mip held the body hands the block to stock's render (tier 2), not to the reader at index 0
        (which is close to stock's but not bit for bit: at notes 24 / 36 no block of it is, M; at 47 / 60 every one is): the
        voice's output and OSC2's kernel state are the same bytes as with no amount at all, block after block.
        BE4.44 W4 (reviewed change): a Wavetable OSC1 feeds B now, so the case that still has no B is an OSC1 with no WAV
        (it writes nothing: fmx= missed); OSC1 here has none. Control: with its WAV, FM 1>2 plays (the output differs)."""
        for note in (24, 60):
            runs = []
            for amt in (0, 400, -400):
                c = self.root_voice(note=note)
                r, o2 = c["r"], c["o"][1]
                if amt >= 0:
                    r.w32(c["o"][0] + 0x18, r.nowav)            # (W4: OSC1 with no WAV writes no B)
                amt = abs(amt)
                if amt:
                    r.root_prop(c["root"], K_FM12, amt, 1)
                outs, ks = [], []
                for b in range(8):
                    outs.append(self.root_block(c))
                    ks.append(bytes(r.u.mem_read(o2 + S.OSC_KERNEL + 0xC0, 25)))
                if amt:
                    self.assertTrue(wt_tagged(bytes(r.u.mem_read(o2 + S.OSC_KERNEL, 0xC0)), 0, o2), "control: OSC2 tagged")
                runs.append((outs, ks))
            self.assertEqual(runs[0][0], runs[1][0], f"note {note}: the voice's output")
            self.assertEqual(runs[0][1], runs[1][1], f"note {note}: OSC2's kernel state")
            self.assertNotEqual(runs[1][0], runs[2][0], f"note {note}: control, with OSC1's WAV FM 1>2 plays (W4)")

    def run_seq(self, w, rate, plan, req=40, n=256, ratio=0.5, check=True):
        """A note on the kernel level, block by block: plan a list of None (stock's kernel; while the reader still holds a
        mip, its mip-home block instead, handback B) or (I0, I1) (the reader with that line). Every stock block after a
        reader block is checked against wt_kmodel's continuation from the handed-back state, bit for bit, samples and
        state (7.2). Returns (samples, who played each block, the mip after each)."""
        inc = float(f32(rate) * f32(1 / 2048))
        w.set(G.wt_state())
        y, who, mips, ph = [], [], [], 0.0
        for p in plan:
            st = w.get()
            if p is None and st["mip"] == G.WT_HOME:
                m = KM.St(w.r.u.mem_read(w.k + 0xC0, 25))
                s, _ = w.stock(req, rate, n)
                if check:
                    want = KM.kernel(m, w.tab, req, rate, n)
                    self.assertEqual(list(s.astype(np.float32)), [np.float32(v) for v in want], "7.2: stock's continuation")
                    self.assertEqual(m.pack(), KM.St(w.r.u.mem_read(w.k + 0xC0, 25)).pack(), "7.2: its state")
                who.append("S")
            else:
                line = None if p is None else wt_line(inc, n, p[0], p[1], ratio, ph)
                s, _, _ = w.block(req, rate, n, line)
                who.append("H" if p is None else "R")
            ph += inc * ratio * n
            y.append(s); mips.append(w.get()["mip"])
        return np.concatenate(y), who, mips

    def continuity(self, w, rate, plan, n=256):
        y, who, mips = self.run_seq(w, rate, plan, n=n)
        ref, _, _ = self.run_seq(w, rate, [None] * len(plan), n=n, check=False)
        return self.step_at_switches(y, who, n) + (who, mips), ref

    @staticmethod
    def step_at_switches(y, who, n):
        """(the largest step at a switch, stock's own largest second difference in y). W2 review: the step at a switch at
        sample b is the prediction error of the line through b - 2, b - 1, e(i) = |y[i] - 2 y[i - 1] + y[i - 2]|, at b and
        b + 1 (a jump of s puts s into both; a kink into e(b + 1)), less the signal's own: the largest e over b - 8 .. b - 1
        and b + 3 .. b + 9, samples the jump does not reach. (W2 as first built took e less its two neighbours', and a jump
        is in the neighbour too: blind to steps.) A plain |y[b] - y[b - 1]| would be the slope itself (note 72: 0.034)."""
        e = np.r_[0.0, 0.0, np.abs(np.diff(y, 2))]                 # e[i] ends at sample i
        steps = []
        for b in [b * n for b in range(1, len(who)) if who[b] != who[b - 1]]:
            ref = max(float(np.max(e[b - 8:b])), float(np.max(e[b + 3:b + 10])))
            steps.append(max(float(e[b]), float(e[b + 1])) - ref)
        return max(steps), float(np.max(e))

    @wt_rule
    def test_handback_and_continuity(self):
        """7.2 and 7.4: FM ramped in, held, ramped out, then stock (through the mip-home block when the reader holds a mip):
        every stock block after it is wt_kmodel's continuation (bit for bit); the |step| at every switch (entry, the
        mip-home block, handback) at most WT_STEP_MAX (0.01), against stock's own largest step on the table. On a synthetic table (one
        sine in every level, aligned: Rig.tabled's) and on builder-made ones (a band-limited saw and the smooth 8-harmonic
        set, through stock's decimator: the skewed levels a factory WAV gets), at a small index (same mip) and a large one
        (a mip excursion of two or more levels: mutation 7 removes the mip-home block)."""
        x = np.arange(2048) / 2048
        tables = (("synthetic sine", None), ("builder saw", G.wt_saw_frames(64)), ("builder smooth", wt_smooth_frames()))
        rows = []
        for tname, frames in tables:
            w = WtRd(frames)
            if frames is None:                                # Rig.tabled's aligned sine, not the builder's levels
                t = np.zeros(0x100000, np.float32)
                for k in range(7):
                    L = 2048 >> k
                    t[G.wt_level_off(k):G.wt_level_off(k) + 256 * L] = np.tile(np.sin(2 * np.pi * np.arange(L) / L), 256)
                w.tab = t; w.r.u.mem_write(w.r.tab, t.tobytes())
            for note in (48, 72):
                rate = wt_rate(note)
                for I, name in ((0.5, "same mip"), (12.0, "mip excursion")):
                    plan = [None, None, (0.0, I), (I, I), (I, 0.0), None, None, None, None]
                    (step, _, who, mips), ref = self.continuity(w, rate, plan)
                    own = float(np.max(np.abs(np.diff(ref, 2))))
                    if name == "mip excursion":
                        self.assertGreaterEqual(max(mips[2:5]), G.wt_ladder(rate) + 2, f"control: excursion {mips}")
                        self.assertIn("H", who, "the mip-home block ran")
                    rows.append((tname, note, name, step, own))
                    self.assertLessEqual(step, WT_STEP_MAX, f"{tname} note {note} {name}: |step| at a switch {step:.4f} "
                                                      f"(stock's own largest {own:.3f}; {who} {mips})")
        print("\n  WT FM W2 continuity (|step| at the switches, stock's own largest): " +
              "; ".join(f"{t} n{nt} {nm} {s:.4f} ({o:.3f})" for t, nt, nm, s, o in rows), file=sys.stderr)

    def test_level0_freezes_unless_feeding(self):
        """D9 (b), mutation 8: through the render (slot 0), a Wavetable OSC1 with Sub FM (the reader) at Level 0 with its
        smoother at rest freezes its phase as stock's render does (kernel state unchanged, nothing mixed); the same while it
        feeds B (OSC2's FM 1>2) keeps running (the phase moves, still nothing mixed)."""
        for feed in (False, True):
            with self.subTest(feed=feed):
                w = WtRun(False)
                w.push(0, K_SUBFM, 300)
                if feed:
                    w.push(1, K_FM12, 400)
                w.m.note(60)
                for _ in range(2):
                    w.m.block()
                w.r.level(w.m.v, 0, 0.0)
                for _ in range(6):                             # the smoother comes to rest
                    w.m.block()
                k = w.m.o1 + S.OSC_KERNEL
                before = bytes(w.r.u.mem_read(k + 0xC0, 12))
                x0 = len(w.m.x1)
                for _ in range(3):
                    w.m.block()
                self.assertEqual(max(abs(v) for v in w.m.x1[x0:]), 0.0, "silent")
                after = bytes(w.r.u.mem_read(k + 0xC0, 12))
                self.assertEqual(after != before, feed, "frozen unless it feeds B")
                self.assertTrue(wt_tagged(w.area(0), 0, w.m.o1))

    @wt_rule
    def test_d5_threshold(self):
        """D5: the build's threshold is the manifest's alone (W2 review: not the test-time environment, so an alternate image
        passes as built), in range, and labelled: a non-default value suffixes the version (build.wt_mip_suffix). The
        device picks the golden's level for it over Sub FM 0..1000 at notes 48 / 60 / 72, A scanned and A's peak known. And
        the rows Jeff's decision asked for, from the golden at 1.25 (clean), 1.5, 1.75, 2.0 (A's peak known, the device's
        Sub FM path): the level each amount plays, the on-grid centroid, the on-grid energy above harmonic 16 (the brightness
        row) and the energy off the FM grid (alias), over a window after the last level change (printed)."""
        self.assertTrue(1.0 <= WT_THR <= 4.0, WT_THR)
        suf = build.wt_mip_suffix(WT_THR)
        self.assertEqual(suf == "", WT_THR == build.WT_MIP_DEFAULT)
        if suf:
            self.assertTrue(MAN["version"].endswith(suf), (MAN["version"], suf))
        else:
            self.assertNotIn("-mip", MAN["version"])
        w = WtRd(G.wt_saw_frames(200))
        for note in (48, 60, 72):
            rate = wt_rate(note); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
            for v in range(0, 1001, 50):
                I = G.an_index32(v)
                st = G.wt_state(idx=3, frac=0.2, frame=0, flag=0, k=kn)
                line = wt_line(inc, 256, I, I)
                for pa in (0.0, float(f32(inc * 0.5))):
                    _, gst, ks = G.wt_read_block(w.tab, st, 0, rate, 256, line, WT_THR, pa=pa)
                    w.set(st)
                    _, got, _ = w.block(0, rate, 256, line, pa)
                    self.assertEqual(got["mip"], gst["mip"], f"note {note} Sub FM {v} pa {pa}")
        t = G.wt_build_levels(G.wt_saw_frames())
        lines = []
        for note, c in ((60, 12), (72, 22)):
            rate = float(f32(c / 4096 * 2048))
            for v in (250, 500, 1000):
                row = [f"note~{note} SubFM {v}:"]
                for thr in (None, 1.25, 1.5, 1.75, 2.0):
                    y, _, ks = G.wt_fm_run(t, rate, 24, v, thr or 1.25, mutant="fixed_mip" if thr is None else None)
                    self.assertEqual(len({k for k in ks[8:]}), 1, f"steady over the window: {ks}")
                    cen, al, hf = G.wt_spectrum_rows(y[-4096:], c, c // 2)
                    row.append(f"{thr or 'note level'}: L{ks[-1][1]} {cen:.2f}h hf {hf:.1f} al {al:.1f}")
                lines.append(" | ".join(row))
        print("\n  WT FM W2 D5 rows (level, on-grid centroid in carrier harmonics, on-grid energy above harmonic 16 dB, "
              "alias dB):\n    " + "\n    ".join(lines), file=sys.stderr)

    @wt_rule
    def test_sizes_and_cost(self):
        """Plan 8 W2: the reader's parts (the loops wt_rd, wt_rdf, wt_rdx; wt_play, wt_mip_for), held to their W2 size (the
        plan's 1.5 KB is missed, below); the loops' cost a sample (M at W2: rd_q 34.1, rd_qx and rd_qm printed; plan 29.1 / 39.1 for the one-route prototypes, the B
        term adds two FMAs and a load; rd_qm, the two-level loop, spills registers but runs only on a mip change)."""
        e = (BUILD / "dsp.elf").read_bytes()
        shoff = struct.unpack_from("<I", e, 32)[0]; es, nsec = struct.unpack_from("<HH", e, 46)
        secs = [struct.unpack_from("<10I", e, shoff + i * es) for i in range(nsec)]
        st = next(x for x in secs if x[1] == 2); stro = secs[st[6]][4]
        sizes = {}
        for i in range(st[5] // 16):
            nm, _, sz, info = struct.unpack_from("<IIIB", e, st[4] + 16 * i)
            if info & 0xF == 2:
                sizes[e[stro + nm:e.index(bytes(1), stro + nm)].decode()] = sz
        size = lambda part: sizes[part] if part in sizes else next((v for k, v in sizes.items() if part in k),
                                                                   0 if WT_THR == "off" else None)   # (W3: off has no wt_mip_for)
        parts = {p: size(p) for p in ("wt_rd", "wt_rdc", "wt_rdr", "wt_rdf", "wt_rdfc", "wt_rdx", "wt_play", "wt_mip_for")}
        total = sum(parts.values())
        # plan 8 W2's exit is 1.5 KB; M at W2 as built: 1,796 (rd_q 256, rd_qx 316, rd_qm 380, wt_play 800, wt_mip_for 44; the
        # review's wait bit and A's known peak +72): missed by 260 B (the B route in all three loops, rd_qx kept apart from rd_qm because the shared loop spills to 60 a sample,
        # wt_play's mip choice and handoff larger than the plan's 250-400). Bound: the measured size, not the target
        # BE4.44 W4 (Jeff, "Decisions before W4": the unrolled reader): + wt_rdc / wt_rdr (rd_q on a one-route line, constant
        # and a ramp, two samples a turn), wt_rdfc (rd_qx on a one-route line) and wt_play's routing and lever (a): the bound
        # the measure (printed)
        self.assertLessEqual(total, 3400, f"the reader's parts {parts}")
        w = WtRd()
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048))
        st = G.wt_state(idx=9, frac=0.1, frame=0, flag=0, k=3)
        costs = {}
        # (W4: small indices, so every case but rd_qm stays on the note's level at any threshold the build has, 1.25 up)
        two = dict(J=0.25, J1=0.25)
        for name, req, line in (("rd_q", 0, wt_line(inc, 256, 0.5, 0.6, **two)), ("rd_qc", 0, wt_line(inc, 256, 0.5, 0.5)),
                                ("rd_qr", 0, wt_line(inc, 256, 0.5, 0.6)), ("rd_qx", 9, wt_line(inc, 256, 0.5, 0.5, **two)),
                                ("rd_qxc", 9, wt_line(inc, 256, 0.5, 0.6)), ("rd_qm", 0, wt_line(inc, 256, 0.0, 8.0))):
            w.set(st)
            sp = Span(w.r, DSP[{"rd_q": "wt_rd", "rd_qc": "wt_rdc", "rd_qr": "wt_rdr", "rd_qx": "wt_rdf", "rd_qxc": "wt_rdfc",
                                "rd_qm": "wt_rdx"}[name]])
            _, _, c = w.block(req, rate, 256, line)
            self.assertEqual(sp.calls, 1, name)
            costs[name] = (sp.total / 256, c)
        self.assertLessEqual(costs["rd_q"][0], 35)
        self.assertLessEqual(costs["rd_qc"][0], 24.5, "W4: the unrolled steady loop (M 23.6)")
        self.assertLessEqual(costs["rd_qr"][0], costs["rd_q"][0] - 4, "W4: the one-route ramp loop")
        self.assertLessEqual(costs["rd_qxc"][0], costs["rd_qx"][0] - 4, "W4: the one-route frame loop")
        self.assertLessEqual(costs["rd_qx"][0], 47)
        self.assertLessEqual(costs["rd_qm"][0], 64)
        print(f"\n  WT FM W2/W4 sizes {parts} = {total} B; cost a sample / whole wt_play block: " +
              ", ".join(f"{k} {a:.1f} / {b}" for k, (a, b) in costs.items()), file=sys.stderr)


# ---- BE4.44 W3 (docs/plan-wt-fm.md 8 W3): A and B into Wavetable carriers, the envelope, ramps, E3 / skip, D3, BE_WT_MIP=off,
# the black box, costs, aliasing and brightness rows
FM_SKIPPED_AT = 8           # SaFmStats.fm_skipped (byte offset)
FM_WT_AT, FM_WT_MIP_AT = 16, 20   # SaFmStats.wt, wt_mip (W3: reader blocks, and of those the mip changes: the black box's wt=)
STATS_BLOCKS, STATS_PEAK, STATS_RENDERS = 0, 8, 48   # SaStats.blocks, peak, renders (byte offsets)
WT_RD_KINDS = ("rd_q", "rd_qx", "rd_qm")   # wt_play's return: the block's loop
S3_ANALOG_FULL = 28929      # plan 6 / S3: Analog's full FM voice at BE4.43 (M), hooks per voice-block
HEAVIEST_VOICE = 35309      # plan 6: the heaviest existing voice (the Position-every-block ceiling)


def wt_capture(r):
    """W3: every wt_play call (one a tier-3 block) from wt_body's one call site: at entry the oscillator (osc, as fm_capture's
    records), the LOAD ring index (block), WtBlk's rate, n, Position's frame (pos) and A's known peak (pa), the kernel state
    with mipcur and the wait bit (st, as p3_golden.wt_state), FmIn's line (a, ia, dia, b, ib, dib: a / b the samples read,
    float32) or None, A / B (FmIn's buffer addresses), off 0; on its return the samples (y), the state after (st1) and the
    block's loop (kind: rd_q, rd_qx, rd_qm)."""
    recs = []
    fn = dsp_sym("wt_play")
    lo, hi = sym_range("wt_body")
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    rets = []
    for i in md.disasm(bytes(r.u.mem_read(lo, hi - lo)), lo):
        if i.mnemonic in ("bl", "blx") and i.op_str.startswith("#") and int(i.op_str.lstrip("#"), 16) == fn:
            rets.append(i.address + i.size)
    assert len(rets) == 1, rets
    def kstate(uc, osc):
        k = osc + S.OSC_KERNEL
        s = KM.St(uc.mem_read(k + 0xC0, 25)); m, fl = uc.mem_read(k + WT_MIP_AT, 2)
        return dict(frac=s.frac, idx=s.idx, frame=s.frame, off=s.off, D=s.D, len=s.len, flag=s.flag, mip=m,
                    wait=int(bool(fl & WT_MIP_WAIT)))
    def entry(uc, a, sz, _):
        osc, bk = uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1)
        tab, fi, s, rate, n, pos, pa = struct.unpack("<IIIfIIf", uc.mem_read(bk, 28))
        x = dict(osc=osc, block=r.idx, tab=tab, s=s, rate=rate, n=n, pos=pos, pa=pa, off=0, st=kstate(uc, osc), line=None,
                 A=None, B=None, ia=0.0, dia=0.0, ib=0.0, dib=0.0)
        if fi:
            A, ia, dia, _, B, ib, dib = struct.unpack("<IffIIff", uc.mem_read(fi, 28))
            av = np.frombuffer(bytes(uc.mem_read(A, 4 * n)), "<f4").astype(np.float64)
            bv = np.frombuffer(bytes(uc.mem_read(B, 4 * n)), "<f4").astype(np.float64)
            x.update(A=A, B=B, ia=ia, dia=dia, ib=ib, dib=dib, line=(av, ia, dia, bv, ib, dib))
        recs.append(x)
    def back(uc, a, sz, _):
        x = recs[-1]
        x["y"] = np.frombuffer(bytes(uc.mem_read(x["s"], 4 * x["n"])), "<f4").astype(np.float64)
        x["st1"] = kstate(uc, x["osc"]); x["kind"] = WT_RD_KINDS[uc.reg_read(UC_ARM_REG_R0)]
    r.u.hook_add(UC_HOOK_CODE, entry, begin=fn, end=fn)
    r.u.hook_add(UC_HOOK_CODE, back, begin=rets[0], end=rets[0])
    return recs


class WtVoice:
    """W3: stock's root and voice on the device's path (SubCopy.voice: stock's root ctor and engine init, 2N, a sounding sub,
    so buffer A carries it), Types through stock's root (an Analog oscillator at Shape 48, the saw), each Wavetable
    oscillator on Rig.tabled's table (a sine in every frame and level), or on stock's builder levels from `frames` (written
    over it); our keys through stock's root. Taps: wt (wt_capture), fm (fm_capture: Analog's an_render_fm calls and B's
    writes). block(): one voice-block, its output bytes kept; run(blocks, each, notes): each(w, b) before block b, the note
    again (stock's voice note start) at the blocks in notes."""
    def __init__(self, tc, types=(0, 0), sub=0, x12=0, env=None, sync=0, note=60, frames=None, wave=4, pw=300, capture=True,
                 rig=None):
        self.tc, self.r = tc, rig or Rig()                       # (rig: BE4.45 X2Identity's BE4.44 image)
        r = self.r
        root, v, subo, _ = SubCopy.voice(tc, r, wave, 1000, fm=(0, 0), types=types, glide=0, pw=pw, fine=0, sync=sync)
        self.root, self.v, self.sub, self.types, self.note_n = root, v, subo, types, note
        self.o = (v + S.VOICE_WT1, v + S.VOICE_WT2)
        for i in (0, 1):
            if types[i]:
                r.root_prop(root, K_SHAPE, 48, i)
            else:
                r.tabled(self.o[i])
        if frames is not None and getattr(r, "tab", None):
            r.u.mem_write(r.tab, G.wt_build_levels(frames).tobytes())
        self.tab = np.frombuffer(bytes(r.u.mem_read(r.tab, 4 * 0x100000)), "<f4") if getattr(r, "tab", None) else None
        if sub:
            r.root_prop(root, K_SUBFM, sub, 0)
        if x12:
            r.root_prop(root, K_FM12, x12, 1)
        for i, (a, d) in (env or {}).items():
            r.root_prop(root, K_FMATK, a, i); r.root_prop(root, K_FMDEC, d, i)
        self.wt = wt_capture(r) if capture else []
        self.fm = fm_capture(r) if capture else None
        self.m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        self.acc, self.scr, self.out = r.buf(DEV_N * self.m, 1024), r.buf(DEV_N * self.m, 1024), r.buf(DEV_N, 1024)
        self.slot18 = r.r32(r.r32(root) + 0x48)
        self.note(note)
        r.first = self.first = r.idx
        self.outs = []

    def note(self, n): SubCopy.note(self.tc, self.r, self.v, self.sub, n)
    def prop(self, key, value, i): self.r.root_prop(self.root, key, value, i)

    def block(self):
        r, (acc, ad), (scr, _), (out, od) = self.r, self.acc, self.scr, self.out
        r.w32(acc + 4, DEV_N * self.m); r.w32(scr + 4, DEV_N * self.m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
        _, c = r.call(self.slot18 & ~1, self.root, self.v, 0, scr, sargs=(acc, 0, out))
        self.outs.append(bytes(r.u.mem_read(od, 4 * DEV_N)))
        r.block()
        return c

    def run(self, blocks, each=None, notes=()):
        for b in range(blocks):
            if b in notes:
                self.note(self.note_n)
            if each:
                each(self, b)
            self.block()
        return self

    def fmstats(self): return struct.unpack("<8I", self.r.u.mem_read(SA + SA_FMSTATS, 32))
    def kernel(self, i): return bytes(self.r.u.mem_read(self.o[i] + S.OSC_KERNEL + 0xC0, 25))
    def by_block(self, i): return {x["block"] - self.first: x for x in self.wt if x["osc"] == self.o[i]}
    def lined(self): return FmCalls(x for x in self.wt if x["line"] is not None)


def wt_tol(x):
    """7.1's bound for one captured block, by the shortest level it reads: len 32 (or shorter) TOL_RD[32]; longer, the named
    crossfade / through-zero bound TOL_RD_TZ (4e-7), which also holds the plain rows (W2: plain len 256 2e-7)."""
    ks = [11 - int(math.log2(x["st"]["len"]))] + [m for m in (x["st"]["mip"], x["st1"]["mip"]) if m < WT_MIPS_N]
    return TOL_RD[32] if 2048 >> max(ks) <= 32 else TOL_RD_TZ


def wt_replay(tc, name, w, recs):
    """Every captured reader block against p3_golden.wt_read_block from its own captured state, line, frame, rate and pa
    (7.1 at the voice: A from stock's sub, B from an Analog OSC1's render). The phase at the block's end within
    1e-7 + 2^-23 sum |di| of a cycle (W3, M: 7.1's 1e-7 was set on W2's small synthetic lines; at the voice's index 16 the
    f32 steps' own rounding, two roundings a step of at most 2^-24 relative, adds up to about 1.4e-7 over a block); the
    samples within wt_tol of the band the golden spans when its start phase is moved by that block's measured phase
    difference either way (on stock's builder saw a phase difference of 1e-7 of a cycle moves a sample at an edge by
    several 1e-6: the band is the golden's own answer to the phase the device kept, and nothing wider); the frame,
    mipcur, the wait bit and off / D / len / flag. Returns the worst error / its bound and the kinds of blocks seen."""
    worst, kinds = 0.0, set()
    for j, x in enumerate(recs):
        want, gst, ks = G.wt_read_block(w.tab, x["st"], x["pos"], x["rate"], x["n"], x["line"], WT_THR, pa=x["pa"])
        b = x["block"] - w.first
        d = abs(wt_phase(x["st1"]) - wt_phase(gst)); d = min(d, 1 - d)
        jj = np.arange(x["n"])
        di = np.full(x["n"], float(np.float32(x["rate"]) * np.float32(1 / 2048)))
        if x["line"] is not None:
            di = di + (x["ia"] + x["dia"] * jj) * x["line"][0] + (x["ib"] + x["dib"] * jj) * x["line"][3]
        dmax = 1e-7 + float(np.sum(np.minimum(np.abs(di), 0.45))) * 2.0 ** -23
        tc.assertLess(d, dmax, f"{name}: reader block {j} (block {b}): phase")
        band = [want]
        for s in (-1, 1):
            st = dict(x["st"])
            if st["flag"]:
                L = 2048 >> G.wt_ladder(x["rate"]); st["idx"] %= L
            else:
                L = st["len"]
            p = (st["idx"] + st["frac"]) / L + s * (d + 1e-9)
            st.update(idx=int(math.floor(p * L)) % L, frac=p * L - math.floor(p * L))
            if st["flag"]:
                st.update(D=1 << G.wt_ladder(x["rate"]), len=L, off=G.wt_level_off(G.wt_ladder(x["rate"])), flag=0)
            band.append(G.wt_read_block(w.tab, st, x["pos"], x["rate"], x["n"], x["line"], WT_THR, pa=x["pa"])[0])
        lo, hi = np.min(band, axis=0), np.max(band, axis=0)
        err = float(np.max(np.maximum(lo - x["y"], x["y"] - hi).clip(0)))
        tol = wt_tol(x)
        tc.assertLessEqual(err, tol, f"{name}: reader block {j} (block {b}, {x['kind']}, levels {ks}): device outside the "
                                     f"golden's band by {err:.2e} (plain |device - golden| {float(np.max(np.abs(x['y'] - want))):.2e})")
        for f in ("frame", "mip", "wait", "off", "D", "len", "flag"):
            tc.assertEqual(x["st1"][f], gst[f], f"{name}: reader block {j} (block {b}): {f}")
        worst = max(worst, err / tol); kinds.add(x["kind"])
    return worst, kinds


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class WtFmW3(unittest.TestCase):
    """BE4.44 W3 (docs/plan-wt-fm.md 8 W3): the FM sources into Wavetable carriers on the device's path (stock's root, 2N, a
    sounding sub): Sub FM's A into a Wavetable OSC1, FM 1>2's B from an Analog OSC1 into a Wavetable OSC2 (D2: OSC2's one
    amount); every reader block the golden's (wt_replay), every index line the law's (SubFm.lines) and, under FM Atk / FM
    Dec, FmEnv's golden lines bit for bit and the Analog carrier's own lines in the same case; the amount's ramps and the
    handback; E3 (a silent Wavetable oscillator freezes, its envelope keeping time, FM ramping in from 0 when heard) and the
    skip (blocks longer than the buffers: stock's render, counted); D3 (Sub FM plays as 0 while an Analog OSC2 is synced
    to the Wavetable OSC1, value kept; mutation 17); BE_WT_MIP=off (never coarser than stock's level); the black box; the
    cost gates re-estimated at W3's start; aliasing and brightness rows (device = golden)."""
    voice, note = SubCopy.voice, SubCopy.note
    lines = SubFm.lines
    got = FmEnv.got
    want = FmEnv.want

    @staticmethod
    def amounts(seq, key=K_SUBFM, i=0):
        def each(w, b):
            if b < len(seq) and (b == 0 or seq[b] != seq[b - 1]):
                w.prop(key, seq[b], i)
        return each

    @staticmethod
    def fm_blocks(seq):
        """{block: (v0, v1)}: the blocks an amount sequence plays under FM (an amount, or a ramp down from one)."""
        return {b: (seq[b - 1] if b else 0, seq[b]) for b in range(len(seq)) if seq[b] or (b and seq[b - 1])}

    # ---- A and B into Wavetable carriers --------------------------------------------------------------------------------
    def test_sub_fm_into_a_wavetable_osc1(self):
        """A into a Wavetable OSC1 (OSC2 a plain Wavetable): Sub FM 0 -> 250 -> 1000 held -> 500 -> 0, Position moving in
        blocks 9..11, on the sine table and on stock's builder levels of a band-limited saw; Sub FM 700 again from block 14.
        Every reader block is the golden's (wt_replay); every FM block's line the index law's from msub to the amount
        (SubFm.lines: 0 -> 250 starts at 0), A alone (b = a at a zero line), A's known peak inc_sub (the Sine's 1); after
        the ramp out the reader runs at most the mip-home block, then stock's render plays (no reader call); OSC2 never
        reads; fm= counts the FM blocks. The level leaves the note's under Sub FM 1000 (a control, unless BE_WT_MIP=off)."""
        seq = [0, 250, 1000, 1000, 1000, 1000, 500, 500, 0, 0, 0, 0, 0, 0, 700, 700]
        for tname, frames in (("sine", None), ("builder saw", G.wt_saw_frames(200))):
            with self.subTest(table=tname):
                w = WtVoice(self, frames=frames)
                def each(w, b):
                    self.amounts(seq)(w, b)
                    if 9 <= b <= 11:
                        w.r.wf(w.o[0] + OSC_POS_SRC, (40 * (b - 8)) / 255)
                w.run(len(seq), each)
                worst, kinds = wt_replay(self, tname, w, w.wt)
                self.assertTrue(all(x["osc"] == w.o[0] for x in w.wt), "only OSC1 reads")
                fmb = self.fm_blocks(seq)
                self.lines(tname, w.lined(), w.o[0], {w.first + b: v for b, v in fmb.items()})
                self.assertTrue(all(x["A"] == SA + SA_FMBUF_A and x["B"] == x["A"] and (x["ib"], x["dib"]) == (0.0, 0.0)
                                    for x in w.lined()), "Sub FM: A alone (b = a at a zero line)")
                by = w.by_block(0)
                inc_sub = struct.unpack("<f", w.r.u.mem_read(SA + SA_RUN + RUN_INC_SUB, 4))[0]
                self.assertEqual(f32(by[15]["pa"]), f32(inc_sub) if WT_THR != "off" else 0.0, "A's known peak: inc_sub "
                                 "(the Sine's 1; off: no peak taken)")
                kn = G.wt_ladder(by[2]["rate"])
                if WT_THR != "off":
                    self.assertGreater(max(by[b]["st1"]["mip"] for b in range(2, 6)), kn, "control: a coarser level at 1000")
                # after the ramp out (block 8): the mip-home block, or with Position moving (blocks 9..11) a frame block
                # first (the one-change rule: the home block waits one block), then stock's render
                rest = sorted(b for b in by if 9 <= b < 14)
                self.assertTrue(rest == list(range(9, 9 + len(rest))) and len(rest) <= 2 and
                                all(by[b]["line"] is None for b in rest), f"after the ramp out ({sorted(by)})")
                self.assertEqual(by[rest[-1]]["st1"]["mip"] if rest else G.WT_HOME, G.WT_HOME, "home, then stock's")
                self.assertTrue(all(b not in by for b in range(12, 14)), "stock's render plays")
                self.assertEqual(w.fmstats()[FM_FM // 4], len(fmb), "fm= counts the Wavetable FM blocks")
                print(f"\n  WT FM W3 Sub FM into WT OSC1 ({tname}): {len(w.wt)} reader blocks {sorted(kinds)}, worst "
                      f"{worst:.2f} of the bound", file=sys.stderr)

    def test_fm12_from_an_analog_osc1_into_a_wavetable_osc2(self):
        """B into a Wavetable OSC2 (D2: FM 1>2 is its one amount): an Analog OSC1 (saw) feeds B while the tagged Wavetable
        OSC2 has FM 1>2 or its ramp memory (feeds: W3), with and without Sub FM of its own, and silent (E3 (a): it still
        renders B). B is OSC1's render times its increment (check_feeds, bit for bit), OSC1's FM calls the kernel's golden
        (SubFm.replay), OSC2's reader reads exactly that B (FmIn.a = B, one route) on the FM 1>2 line from m12 (400 -> 1000
        held -> 0), every reader block the golden's; nothing missed (fmx= 0); after the ramp out OSC1 stops feeding."""
        seq = [400, 1000, 1000, 1000, 0, 0, 0, 0]
        for sub, lv in ((0, 1.0), (600, 1.0), (600, 0.0)):
            with self.subTest(sub=sub, level=lv):
                w = WtVoice(self, types=(1, 0), sub=sub)
                def each(w, b):
                    self.amounts(seq, K_FM12, 1)(w, b)
                    w.r.level(w.v, 0, lv)
                w.run(len(seq), each)
                name = f"sub {sub} level {lv}"
                if w.fm:
                    SubFm.replay(self, name, w.fm)
                fed = check_feeds(self, name, w.fm, w.o[0], w.o[1])
                rd = [x for x in w.wt if x["osc"] == w.o[1]]
                self.assertTrue(rd and len(rd) == len(w.wt), "OSC2 reads (OSC1 is Analog)")
                fmb = self.fm_blocks(seq)
                self.lines(name, FmCalls(x for x in rd if x["line"] is not None), w.o[1], {w.first + b: v for b, v in fmb.items()})
                self.assertEqual(fed, len(fmb), "OSC1 fed B in every block OSC2 wanted it (the ramp out too), then stopped")
                feeds = {}
                for f in w.fm.feeds:
                    feeds.setdefault(f["block"] - w.first, []).append(f["b"])
                for x in rd:
                    if x["line"] is not None:
                        b = x["block"] - w.first
                        self.assertEqual((x["A"], x["B"], x["ib"], x["dib"]), (SA + SA_FMBUF_B, SA + SA_FMBUF_B, 0.0, 0.0),
                                         f"block {b}: FM 1>2 alone reads B")
                        self.assertTrue(np.array_equal(x["line"][0], np.concatenate(feeds[b]).astype(np.float64)),
                                        f"block {b}: OSC1's B")
                worst, kinds = wt_replay(self, name, w, rd)
                self.assertEqual(w.fmstats()[FM_MISSED // 4], 0, "fmx= missed 0")
                print(f"\n  WT FM W3 FM 1>2 Analog -> WT OSC2 ({name}): {len(rd)} reader blocks {sorted(kinds)}, worst "
                      f"{worst:.2f} of the bound", file=sys.stderr)

    def test_b_known_peak_and_low_notes(self):
        """W3 review (high): B's known peak. FM 1>2 from an Analog OSC1 into a Wavetable OSC2: wt_body passes pa = inc1 x the
        peak of OSC1's Shape (p3_golden.an_peak: 1 for the sine..square, 1.84 for the narrowest pulse), not a scan of one block
        of B. Every FM block's pa is f32(inc1) an_peak(k) (inc1 and k from OSC1's own render calls) within float32 rounding,
        and it bounds B (max |B| over the run <= pa (1 + 1e-3), and above 0.9 pa: a bound, and a tight one). At notes 24 /
        36 / 48 / 60 and FM 1>2 300 / 500 / 700 / 1000 (a saw OSC1, 40 blocks) the level changes at most once after the
        entry block: a scan followed OSC1's phase when its period spans blocks, and flipped the level on up to every block
        (M, W3 review: note 48 / 700, 30 of 44 blocks rd_qm)."""
        for shape in (0, 48, 127):
            with self.subTest(shape=shape):
                w = WtVoice(self, types=(1, 0), x12=700, note=48, frames=G.wt_saw_frames(200))
                w.prop(K_SHAPE, shape, 0)
                w.run(12)
                incs, ks, bmax = {}, {}, 0.0
                for f in w.fm.feeds:
                    incs.setdefault(f["block"], set()).add(float(f32(f["inc"])))
                    if "b" in f:
                        bmax = max(bmax, float(np.max(np.abs(f["b"]))))
                for x in list(w.fm) + w.fm.plain_calls:
                    if x["osc"] == w.o[0]:
                        ks.setdefault(x["block"], set()).add(float(f32(x["k"])))
                rd = [x for x in w.wt if x["osc"] == w.o[1] and x["line"] is not None]
                self.assertTrue(rd, "OSC2 reads under FM 1>2")
                for x in rd:
                    (inc1,), k = incs[x["block"]], max(ks[x["block"]])
                    want = inc1 * G.an_peak(k)
                    if WT_THR == "off":
                        self.assertEqual(x["pa"], 0.0, "off: no peak passed")
                    else:
                        self.assertAlmostEqual(x["pa"] / want, 1.0, delta=4e-7, msg=f"block {x['block'] - w.first}: pa, k {k}")
                if WT_THR != "off":
                    pa = max(x["pa"] for x in rd)
                    self.assertLessEqual(bmax, pa * (1 + 1e-3), f"shape {shape}: max |B| {bmax:.6g} within pa {pa:.6g}")
                    self.assertGreater(bmax, pa * 0.9, f"shape {shape}: the bound is tight (max |B| {bmax:.6g}, pa {pa:.6g})")
        if WT_THR == "off":
            return
        rows = []
        for note in (24, 36, 48, 60):
            for amt in (300, 500, 700, 1000):
                w = WtVoice(self, types=(1, 0), x12=amt, note=note, frames=G.wt_saw_frames(200))
                w.run(40)
                by = w.by_block(1)
                mips = [by[b]["st1"]["mip"] for b in sorted(by) if b >= 1]
                ch = sum(1 for p, q in zip(mips, mips[1:]) if p != q)
                rows.append(f"n{note}/{amt}: L{mips[-1]} {ch}")
                self.assertLessEqual(ch, 1, f"note {note} FM 1>2 {amt}: {mips}")
        print("\n  WT FM W3 B's known peak, levels held (note / FM 1>2: level, changes): " + ", ".join(rows), file=sys.stderr)

    # ---- the envelope: FmEnv's lines on Wavetable carriers -------------------------------------------------------------
    def analog_lines(self, env, amounts, notes, osc, n, x12=None, levels=None):
        """The same case on Analog carriers (Fm12.play: both Analog; osc's lines, FmEnv.got)."""
        calls, _, _, _, (r, v, sub) = Fm12.play(self, fm=(0, 0), x12=x12 or 0, blocks=n, notes=notes, env=env,
                                                amounts=(lambda b: {0: amounts(b)}) if x12 is None else None,
                                                amounts12=amounts if x12 is not None else None, levels=levels)
        return self.got(calls, r.osc(v, osc), r.first)

    def test_envelope_lines_on_wavetable_carriers(self):
        """FM Atk / FM Dec on a Wavetable carrier (plan 8 W3's exit: FmEnv's lines bit for bit): FmEnv's OSC1 cases (5/8 with
        two back-to-back retriggers after a full decay, 0/0 with a retrigger, 1/Inf, 3/6 under a moving amount) on a
        Wavetable OSC1's Sub FM, and FM 1>2 under Atk 3 / Dec 4 with the note again at 9 on a Wavetable OSC2 fed by an Analog
        OSC1. Every FM block's line (FmIn's ia, dia) is fm_env_lines' f32 line bit for bit and the Analog carrier's own line
        in the same case; every reader block the golden's. Decayed to 0 it still reads under FM (index 0)."""
        cases = (("5/8", 5, 8, lambda b: 1000, (19, 20), 24),
                 ("0/0", 0, 0, lambda b: 700, (4,), 8),
                 ("1/Inf", 1, G.FM_DEC_INF, lambda b: 1000, (5,), 8),
                 ("3/6 moving", 3, 6, lambda b: (120, 250, 1000, 1000, 640, 640, 37, 900, 900, 900, 1000, 1000)[b], (7,), 12))
        for name, atk, dec, a, notes, n in cases:
            with self.subTest(case=name):
                w = WtVoice(self, env={0: (atk, dec)})
                w.run(n, lambda w, b: w.prop(K_SUBFM, a(b), 0), notes=notes)
                got = self.got(w.lined(), w.o[0], w.first)
                self.assertEqual(sorted(got), list(range(n)), f"{name}: FM every block")
                v1 = [a(b) for b in range(n)]; v0 = [0] + v1[:-1]
                want = self.want(atk, dec, v0, v1, n, (0,) + notes)
                ana = self.analog_lines({0: (atk, dec)}, a, notes, 0, n)
                for b in range(n):
                    self.assertEqual(got[b], want[b], f"{name}: block {b}: the Wavetable carrier's line vs the golden's f32")
                    self.assertEqual(got[b], ana[b], f"{name}: block {b}: the same as the Analog carrier's")
                wt_replay(self, name, w, w.wt)
                if name == "5/8":
                    self.assertEqual(got[17], (0.0, 0.0), "control: decayed to 0 (still a reader block)")
        n = 14
        w = WtVoice(self, types=(1, 0), env={1: (3, 4)})
        w.run(n, lambda w, b: w.prop(K_FM12, 1000, 1) if b == 0 else None, notes=(9,))
        got = self.got(w.lined(), w.o[1], w.first)
        want = self.want(3, 4, [0] + [1000] * (n - 1), [1000] * n, n, (0, 9))
        ana = self.analog_lines({1: (3, 4)}, lambda b: 1000, (9,), 1, n, x12=1000)
        self.assertEqual([got[b] for b in range(n)], [want[b] for b in range(n)], "WT OSC2's FM 1>2 under its envelope")
        self.assertEqual([got[b] for b in range(n)], [ana[b] for b in range(n)], "the same as the Analog OSC2's")
        wt_replay(self, "FM 1>2 envelope", w, w.wt)

    def test_the_envelope_keeps_time_and_e3(self):
        """E3 on a Wavetable OSC1 (FmEnv.test_the_envelope_keeps_time's case): Atk 4 / Dec 6 from the note, no amount in blocks
        0..2 (tier 2: no reader, the envelope stepped all the same), Sub FM 1000 from block 3, Level 0 in blocks 6..8 (once the
        smoother rests: frozen as stock's, no reader call, the kernel state unchanged, msub dropped), heard again from 9. Each
        reader line is the golden's at the note's own time, with the amount's ramp from 0 wherever the block before played
        none (block 3, and the first block heard after the silence); the Analog carrier's lines in the same case are the
        same."""
        n = 16
        a = lambda b: 0 if b < 3 else 1000
        lv = lambda b: (0.0 if 6 <= b <= 8 else 1.0, 1.0)
        w = WtVoice(self, env={0: (4, 6)})
        ks = {}
        def each(w, b):
            w.prop(K_SUBFM, a(b), 0)
            w.r.level(w.v, 0, lv(b)[0])
            ks[b] = w.kernel(0)
        w.run(n, each)
        ks[n] = w.kernel(0)
        got = self.got(w.lined(), w.o[0], w.first)
        heard = sorted(got)
        self.assertEqual(heard[0], 3, "FM from the first block with an amount")
        frozen = [b for b in range(6, 9) if ks[b + 1] == ks[b]]
        self.assertTrue(frozen, f"control: a silent block froze the kernel ({heard})")
        self.assertTrue(all(b not in got for b in frozen), "a frozen block reads nothing")
        v1 = [a(b) for b in range(n)]; v0 = [v1[b - 1] if b and (b - 1) in got else 0 for b in range(n)]
        want = self.want(4, 6, v0, v1, n, (0,))
        for b in heard:
            self.assertEqual(got[b], want[b], f"block {b}: the envelope at the note's time")
        self.assertEqual(got[3][0], 0.0, "block 3: the amount ramps in from 0 (msub)")
        back = min(b for b in heard if b > max(frozen))
        self.assertEqual(got[back][0], 0.0, "heard again: from 0 (msub dropped while frozen)")
        ana = self.analog_lines({0: (4, 6)}, a, (), 0, n, levels=lv)
        self.assertEqual({b: got[b] for b in heard}, {b: ana.get(b) for b in heard}, "the Analog carrier's lines")
        wt_replay(self, "keeps time", w, w.wt)

    def test_ramps_and_the_handback(self):
        """Ramps (plan 7.4: 0 -> 4 -> 0 over 3 blocks): Sub FM 400 (index 4.05) for one block between 0s, at notes 48 and 72 on
        stock's builder saw: the reader ramps in (0 -> I) in that block, ramps out (I -> 0, ending at index 0) in the next,
        then at most the mip-home block, then stock's render (no reader call), and FM on again later ramps in from 0. The
        stock blocks after the handback are stock's continuation bit for bit (wt_kmodel run on from the handed-back kernel
        state; 7.2 at the voice)."""
        seq = [0, 0, 400, 0, 0, 0, 0, 0, 1000, 1000]
        for note in (48, 72):
            w = WtVoice(self, note=note, frames=G.wt_saw_frames(200))
            sts = []
            def each(w, b):
                self.amounts(seq)(w, b)
                sts.append(KM.St(w.kernel(0)))
            w.run(len(seq), each)
            sts.append(KM.St(w.kernel(0)))
            by = w.by_block(0)
            self.assertEqual((by[2]["ia"], by[3]["ia"]), (0.0, an_index(400)), f"note {note}: ramp in, ramp out")
            self.assertEqual(f32(by[3]["ia"] + by[3]["dia"] * 256), 0.0, "the ramp out ends at index 0")
            self.assertEqual(by[8]["ia"], 0.0, "on again: from 0")
            self.assertTrue(all(by[b]["line"] is None for b in by if b in (4,)), "block 4: the mip-home block, if any")
            for b in (5, 6, 7):
                self.assertNotIn(b, by, f"note {note}: block {b} is stock's render")
            wt_replay(self, f"ramps n{note}", w, w.wt)
            rate = by[2]["rate"]
            m = KM.St(sts[5].pack())
            for b in (5, 6, 7):
                KM.kernel(m, w.tab, 0, rate, 256)
                self.assertEqual(m.pack(), sts[b + 1].pack(), f"note {note}: block {b}: stock's kernel from the handback")

    # ---- E3 / skip --------------------------------------------------------------------------------------------------------
    def test_a_longer_block_than_the_buffers_is_stocks_and_counted(self):
        """The skip (fm_setup: n > AN_FM_FRAMES, the modulator buffers): a Wavetable OSC1 with Sub FM 700 at 512 frames plays
        stock's render (tier 2), bit for bit with stock 1.6.8's on the same voice (samples), and fmx= counts each block
        skipped; no reader call, wt= 0. W3 review (low, pre-existing): with a mip held when such a block comes (forced here at
        block 3, buffer B filled with NaN), the mip goes home at once and the block is stock's render, bit for bit: no
        mip-home block whose zero line would read n > 256 samples from buffer A on into B (0 x NaN)."""
        def run(stock, held=False):
            m = Mixed(stock=stock, type2=0, frames=512)
            tap = None
            if not stock:
                m.r.prop(m.o1, K_SUBFM, 700, m.handle, 0)
                tap = wt_capture(m.r)
            m.note(60)
            for b in range(6):
                if held and b == 3:
                    m.r.u.mem_write(m.o1 + S.OSC_KERNEL + WT_MIP_AT, bytes([5]))
                    m.r.u.mem_write(SA + SA_FMBUF_B, struct.pack("<I", 0x7FC00000) * AN_FM_FRAMES)
                m.block(0.01 * b)
                if held and b == 3:
                    self.assertEqual(m.r.u.mem_read(m.o1 + S.OSC_KERNEL + WT_MIP_AT, 1)[0], WT_HOME, "the mip home at once")
            return m, tap
        a, tap = run(False)
        b, _ = run(True)
        same(self, f32le(a.x1), f32le(b.x1), "OSC1: stock's render at 512 frames")
        self.assertGreater(max(abs(x) for x in a.x1), 0.1, "control: it plays")
        self.assertEqual(tap, [], "no reader call")
        st = struct.unpack("<8I", a.r.u.mem_read(SA + SA_FMSTATS, 32))
        self.assertEqual((st[FM_SKIPPED_AT // 4], st[FM_WT_AT // 4]), (6, 0), "fmx= skipped each block; wt= 0")
        h, tap = run(False, held=True)
        same(self, f32le(h.x1), f32le(b.x1), "a mip held: stock's render at 512 frames")
        self.assertEqual(tap, [], "a mip held: no reader call")

    # ---- D3 (Jeff, 2026-11-17): Sub FM plays as 0 while an Analog OSC2 is synced to the Wavetable OSC1 ---------------------
    def test_d3_sub_fm_mutes_while_synced(self):
        """D3 (mutation 17): a Wavetable OSC1 with Sub FM 1000 and an Analog OSC2 synced to it (mixed sync) plays as with Sub
        FM 0, byte for byte: the voice's output, OSC1's kernel state and the slave's copy of WT1's phase after every block, so
        mixed sync plays as BE4.43 (no reader call). Value kept: OSC1 still holds 1000. Sync turned off at block 6: Sub FM
        plays from there (ramping in from 0); on again at block 10: no FM line from then (at most the mip-home block). Control:
        the same voice unsynced reads under FM from block 0."""
        def run(sub, sync=1, toggles=False):
            w = WtVoice(self, types=(0, 1), sub=sub, sync=sync)
            ks = []
            def each(w, b):
                if toggles and b in (6, 10):
                    w.prop(K_SYNC, 0 if b == 6 else 1, 1)
                ks.append((w.kernel(0), bytes(w.r.u.mem_read(w.o[1] + S.OSC_KERNEL + 156, 8))))
            w.run(14, each)
            return w, ks
        a, ka = run(1000)
        b, kb = run(0)
        self.assertEqual(a.outs, b.outs, "the voice: Sub FM plays as 0 while synced")
        self.assertEqual(ka, kb, "OSC1's kernel and the slave's copy of its phase")
        self.assertEqual(a.wt, [], "no reader call while synced")
        self.assertEqual(r_settings(a.r, a.o[0])[5], 1000, "value kept")
        c, _ = run(1000, sync=0)
        self.assertIn(0, c.by_block(0), "control: unsynced, it reads under FM from block 0")
        t, _ = run(1000, toggles=True)
        by = t.by_block(0)
        self.assertEqual(sorted(x for x in by if by[x]["line"] is not None), list(range(6, 10)), f"FM while unsynced ({sorted(by)})")
        self.assertEqual(by[6]["ia"], 0.0, "unsynced: ramps in from 0")
        self.assertTrue(all(x <= 10 for x in by), f"synced again: at most the mip-home block ({sorted(by)})")

    def test_d3_with_a_hidden_fm12_and_its_analog_guard(self):
        """W3 review (medium): D3 holds when the Wavetable OSC1 has the feed-B bit (a hidden FM 1>2 on the synced Analog
        OSC2, kept from BE4.43 or a Type flip): Sub FM 1000 plays as Sub FM 0 byte for byte (the voice, OSC1's kernel, the
        slave's copy) with no reader call, at notes 24 and 50 (at note 60 the index-0 reader happens to match stock's: W2);
        the feed-B bit is set (precondition). W3 review (low): D3's guard that OSC2 is Analog: a Wavetable OSC2 holding a
        sync byte of 1 (an Analog OSC2 with Sync on, its Type flipped to Wavetable, the byte forced to 1: stale bytes) does
        not mute OSC1's Sub FM."""
        for note in (24, 50):
            outs = []
            for sub in (1000, 0):
                w = WtVoice(self, types=(0, 1), sub=sub, x12=500, sync=1, note=note)
                self.assertTrue(w.r.u.mem_read(w.o[0] + S.OSC_KERNEL + WT_FLAGS_AT, 1)[0] & 1, "feed-B set (precondition)")
                ks = []
                w.run(24, lambda w, b: ks.append((w.kernel(0), bytes(w.r.u.mem_read(w.o[1] + S.OSC_KERNEL + 156, 8)))))
                outs.append((w.outs, ks, len(w.wt)))
            self.assertEqual(outs[0][2], 0, f"note {note}: no reader call while synced")
            self.assertEqual(outs[0][0], outs[1][0], f"note {note}: the voice as Sub FM 0")
            self.assertEqual(outs[0][1], outs[1][1], f"note {note}: OSC1's kernel and the slave's copy")
        w = WtVoice(self, types=(0, 1), sub=1000, sync=1)
        w.prop(K_TYPE, 0, 1)                                  # (the flip forgets OSC2's Analog state: its sync byte is then
        w.r.tabled(w.o[1])                                    # stale bytes, here forced to 1, as the review's mutation reads)
        w.r.u.mem_write(w.o[1] + S.OSC_KERNEL + PROPS_AT + 2, bytes([1]))
        self.assertFalse(w.r.u.mem_read(w.o[1] + S.OSC_KERNEL, 4) == struct.pack("<I", AN_MAGIC), "OSC2 not Analog (precondition)")
        w.run(4)
        self.assertIn(0, w.by_block(0), "a Wavetable OSC2's sync byte does not mute OSC1's Sub FM")
        self.assertIsNotNone(w.by_block(0)[0]["line"], "OSC1 reads under FM")

    # ---- BE_WT_MIP=off (Jeff, 2026-11-17: D5's bright alternate, "never go darker") ----------------------------------------
    def test_mip_off(self):
        """BE_WT_MIP=off: build.py's parse, suffix (-mipoff) and defines (WT_MIP_OFF); the golden's "off" plays the D5 rows'
        note-level column (control: at Sub FM 1000 the default's level is coarser). On an off image (its manifest's wt_mip
        is off), the reader stays at stock's level for the note under any FM at notes 36, 60 and 84 (no rd_qm block, mipcur
        HOME and no wait bit after every block), every block the golden's ("off"), the version suffixed, and the last FM
        block (its line ending at 0) hands back straight to stock's render, whose blocks continue bit for bit (wt_kmodel)."""
        self.assertEqual(build.wt_mip_threshold("off"), "off")
        self.assertEqual(build.wt_mip_suffix("off"), "-mipoff")
        self.assertIn("-DWT_MIP_OFF=1", build.wt_mip_defines("off"))
        self.assertNotIn("-DWT_MIP_OFF=1", build.wt_mip_defines(1.75))
        t = G.wt_build_levels(G.wt_saw_frames())
        rate = float(f32(12 / 4096 * 2048))
        _, _, ko = G.wt_fm_run(t, rate, 6, 1000, "off")
        _, _, kd = G.wt_fm_run(t, rate, 6, 1000, build.WT_MIP_DEFAULT)
        kn = G.wt_ladder(rate)
        self.assertTrue(all(k == (kn, kn) for k in ko), f"golden off: the note's level ({ko})")
        self.assertGreater(kd[-1][1], kn, "control: the default goes coarser at 1000")
        if WT_THR != "off":
            self.assertNotIn("-mipoff", MAN["version"])
            return
        self.assertTrue(MAN["version"].endswith("-mipoff"), MAN["version"])
        w = WtRd()                                            # (7.1's smooth table: its tolerances are set on it)
        for note in (36, 60, 84):
            rate = wt_rate(note); inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
            pa = float(f32(inc * 0.5))
            st = G.wt_state()
            w.set(st)
            plan = [(0.0, 16.0), (16.0, 16.0), (16.0, 16.0), (16.0, 0.0), None, None]
            for b, p in enumerate(plan):
                if p is None:
                    m = KM.St(w.r.u.mem_read(w.k + 0xC0, 25))
                    y, _ = w.stock(0, rate)
                    want = KM.kernel(m, w.tab, 0, rate, 256)
                    self.assertEqual(list(y.astype(np.float32)), [np.float32(v) for v in want], f"note {note}: stock's block {b}")
                    continue
                line = wt_line(inc, 256, p[0], p[1], ph=0.5 * inc * 256 * b)
                y, got, _ = w.block(0, rate, 256, line, pa)
                _, gst, ks = G.wt_read_block(w.tab, st, 0, rate, 256, line, "off", pa=pa)
                self.assertEqual(ks, (kn, kn), f"note {note} block {b}: the note's level")
                self.assertEqual((got["mip"], got["wait"]), (G.WT_HOME, 0), f"note {note} block {b}: nothing held")
                # device = golden as wt_replay checks the voice's blocks (index 16 through zero: the phase-aware band)
                wt_replay(self, f"off n{note} b{b}", types.SimpleNamespace(tab=w.tab, first=0),
                          [dict(st=st, st1=got, y=y, line=line, ia=line[1], dia=line[2], ib=line[4], dib=line[5], pos=0,
                                rate=rate, n=256, pa=pa, block=b, kind="rd_q")])
                st = got

    # ---- the black box ---------------------------------------------------------------------------------------------------
    def test_the_black_box_counts_wavetable_fm(self):
        """S4: a voice of two Wavetable oscillators (no Analog one: an_enter counts no voice) with Sub FM 1000 on OSC1 for 8
        blocks, then 0: fm= counts its FM blocks, wt= every reader block (the mip-home block too), wt=.../mip the blocks that
        changed the level (rd_qm), each as wt_capture saw them; and the stats line is flushed for these blocks (blocks=,
        renders=: one render a block, peak_cyc=: our hooks' cycles), as for an Analog voice (before W3 an all-Wavetable voice
        left SaStats untouched)."""
        w = WtVoice(self)
        def each(w, b):
            if b in (0, 8):
                w.prop(K_SUBFM, 1000 if b < 8 else 0, 0)
            if b in (4, 5):
                w.r.wf(w.o[0] + OSC_POS_SRC, 0.1 * b)            # (frame blocks too: wt_mip counts mip changes only)
        w.run(12, each)
        self.assertIn("rd_qx", {x["kind"] for x in w.wt}, "control: a frame block")
        st = w.fmstats()
        self.assertEqual(st[FM_FM // 4], len(w.lined()), "fm=")
        self.assertEqual(st[FM_WT_AT // 4], len(w.wt), "wt=")
        self.assertEqual(st[FM_WT_MIP_AT // 4], sum(1 for x in w.wt if x["kind"] == "rd_qm"), "wt=.../mip")
        if WT_THR != "off":
            self.assertGreater(st[FM_WT_MIP_AT // 4], 0, "control: the level changed")
        w.block()                                             # (a block's stats are flushed at the next block's start)
        self.assertEqual(stat(w.r, STATS_RENDERS // 4), len(w.wt), "renders=: the reader blocks")
        self.assertEqual(stat(w.r, STATS_BLOCKS // 4), len({x["block"] for x in w.wt}), "blocks=: the blocks with one")
        # W3 review: the reader's own cycles reach peak_cyc (not an_enter's alone): at least most of wt_play's heaviest block
        sp = max(self.reader_spans())
        self.assertGreater(sp, 5000, "control: a reader block's cost")
        self.assertGreaterEqual(stat(w.r, STATS_PEAK // 4), 0.9 * sp, "peak_cyc= has the reader's cycles")
        # W3 review: wt= counts every reader block, silent ones too (D9 (b): a silent Wavetable OSC1 that feeds B plays the
        # reader, unheard: here OSC2 is Analog with FM 1>2, so feed-B is set)
        s = WtVoice(self, types=(0, 1), sub=1000, x12=400)
        s.run(6, lambda w, b: w.r.level(w.v, 0, 0.0))
        self.assertTrue(s.wt, "a silent OSC1 feeding B reads")
        self.assertEqual(s.fmstats()[FM_WT_AT // 4], len(s.wt), "wt= counts the silent reader blocks")

    def reader_spans(self):
        """The instructions in wt_play a block, for the voice of test_the_black_box_counts_wavetable_fm (Span)."""
        w = WtVoice(self, capture=False)
        sp = Span(w.r, dsp_sym("wt_play"))
        per = []
        for b in range(12):
            if b in (0, 8):
                w.prop(K_SUBFM, 1000 if b < 8 else 0, 0)
            t0 = sp.total
            w.block()
            per.append(sp.total - t0)
        return per

    # ---- costs (plan 6 / S3, re-estimated at W3's start from W2's measured reader: plan "W3: CPU re-estimate") --------------
    # medians (W3 review: A1 -> WT2 set from the measurement, +5 %, not from the re-estimate's 24.2k; a non-pinned note too)
    W3_GATES = {"WT1 Sub FM": 26000, "A1 -> WT2 FM 1>2": 22000, "A1 -> WT2 FM 1>2, n48 700": 22000,
                "A1 Sub FM -> WT2 FM 1>2": 32000}
    # every block, the note start included (W3 review): the Wavetable-only cases within the heaviest existing voice; the
    # mixed full FM voice (an Analog OSC1 with Sub FM feeding a Wavetable OSC2) is over it on its rd_qm blocks (the note
    # start, an FM Atk's ramp): M 36.4k against 35,309, a known miss reported to Jeff, gated here at its measured level
    W3_MIXED_MAX = 37000
    MIXED = ("A1 Sub FM -> WT2 FM 1>2", "A1 Sub FM -> WT2, Position every block", "A1 Sub FM -> WT2, FM Atk 3")

    def voice_cost(self, types=(0, 0), sub=0, x12=0, pos=None, env=None, sub_at=None, blocks=16, note=60):
        """Instructions per voice-block on the device's path, note 60, a builder saw (200 harmonics), a sine sub: an_enter and
        each oscillator's whole render (its slot 0, our trampoline: stock's render too where it plays), Span. pos(b): the
        Position knob before block b (both oscillators); sub_at(b): Sub FM before block b. (per-block list; block 0 the note's
        first block)"""
        w = WtVoice(self, types=types, sub=sub, x12=x12, env=env, frames=G.wt_saw_frames(200), capture=False, note=note)
        sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]   # (one class: one slot 0)
        per = []
        for b in range(blocks):
            if pos:
                for o in w.o:
                    w.r.wf(o + OSC_POS_SRC, pos(b))
            if sub_at:
                w.prop(K_SUBFM, sub_at(b), 0)
            t0 = sum(s.total for s in sp)
            w.block()
            per.append(sum(s.total for s in sp) - t0)
        return per

    def test_cost(self):
        """Plan 6 / S3 as re-estimated at W3's start (W3_GATES; the plan's "W3: CPU re-estimate", I on W2's M): per voice-block,
        note 60, I = 16, the median of the steady blocks (4 on). Gated: a Wavetable OSC1 with Sub FM 1000 (the full Wavetable
        FM voice buildable at W3: FM 1>2 from a Wavetable OSC1 is W4's) within its gate and S3's 28,929; FM 1>2 from an
        Analog OSC1 into a Wavetable OSC2, alone and next to the Analog OSC1's own Sub FM (the mixed full FM voice); with
        Position moving every block, every block within the heaviest existing voice (35,309); the two new cases: a Position
        LFO with a decaying FM Dec at once (mip and frame both wanting to change: the one-change rule alternates), and an
        index parked on a level threshold (+-3 a block: hysteresis, at most two level changes, the median within the Sub FM
        gate). W3 review: the max is over every block, the note's first included (an rd_qm block when the note starts under
        FM); FM 1>2 into a Wavetable OSC2 at a note whose level is not pinned (48, 700); the mixed full FM voice with Position
        moving and with an FM Atk 3 on OSC2 (rd_qm on each attack block), gated at W3_MIXED_MAX (over HEAVIEST_VOICE: a
        known miss). Printed: stock's Wavetable voice and Analog's full FM voice on this build, for reference."""
        rows = {}
        med = lambda per: sorted(per[4:])[len(per[4:]) // 2]
        lfo = lambda b: (b * 7 % 200) / 255
        for name, kw in (("stock WT voice", {}), ("WT1 Sub FM", dict(sub=1000)),
                         ("A1 -> WT2 FM 1>2", dict(types=(1, 0), x12=1000)),
                         ("A1 -> WT2 FM 1>2, n48 700", dict(types=(1, 0), x12=700, note=48)),
                         ("A1 Sub FM -> WT2 FM 1>2", dict(types=(1, 0), sub=1000, x12=1000)),
                         ("A1 Sub FM -> WT2, Position every block", dict(types=(1, 0), sub=1000, x12=1000, pos=lfo)),
                         ("A1 Sub FM -> WT2, FM Atk 3", dict(types=(1, 0), sub=1000, x12=1000, env={1: (3, FM_DEC_INF)})),
                         ("Analog full FM", dict(types=(1, 1), sub=1000, x12=1000))):
            per = self.voice_cost(**kw)
            rows[name] = (med(per), max(per))
        per = self.voice_cost(sub=1000, pos=lfo)
        rows["WT1 Sub FM, Position every block"] = (med(per), max(per))
        per = self.voice_cost(sub=1000, pos=lfo, env={0: (0, 12)}, blocks=24)
        rows["Position LFO + FM Dec"] = (med(per), max(per))
        # an index on a level threshold: the Sub FM whose peak sits at the threshold one level up (A's known peak inc_sub:
        # x = rate (1 + I / 2)), moved +-3 every block
        thr = build.WT_MIP_DEFAULT if WT_THR == "off" else WT_THR
        pw = WtVoice(self, sub=300, frames=G.wt_saw_frames(200)).run(2)
        x = pw.by_block(0)[1]
        rate, pa = x["rate"], x["pa"]; inc = float(f32(rate) * f32(1 / 2048)); kn = G.wt_ladder(rate)
        pa = pa or float(struct.unpack("<f", pw.r.u.mem_read(SA + SA_RUN + RUN_INC_SUB, 4))[0])   # (off: no pa passed)
        I = (thr * 2 ** kn / 2048 - inc) / pa                # x = (inc + I pa) 2048 reaches thr 2^kn: one level up
        v = next(v for v in range(1, 1001) if G.an_index32(v) >= I)
        wob = lambda b: v + (3 if b % 2 else -3)
        per = self.voice_cost(sub=v, sub_at=wob, blocks=20)
        rows["index on a threshold"] = (med(per), max(per))
        tw = WtVoice(self, sub=v, frames=G.wt_saw_frames(200))
        tw.run(20, lambda w, b: w.prop(K_SUBFM, wob(b), 0))
        mips = [x["st1"]["mip"] for x in tw.wt]
        print("\n  WT FM W3 cost (instructions per voice-block, note 60, I 16; median / max): " +
              "; ".join(f"{k} {a} / {b}" for k, (a, b) in rows.items()) + f"; threshold case Sub FM {v}: levels {mips}",
              file=sys.stderr)
        for k, g in self.W3_GATES.items():
            self.assertLessEqual(rows[k][0], g, f"{k}: the re-estimated gate")
        self.assertLessEqual(rows["WT1 Sub FM"][0], S3_ANALOG_FULL, "S3: within Analog's full FM voice")
        for k in ("WT1 Sub FM", "WT1 Sub FM, Position every block", "Position LFO + FM Dec", "index on a threshold"):
            self.assertLessEqual(rows[k][1], HEAVIEST_VOICE, f"{k}: every block within the heaviest existing voice")
        for k in self.MIXED:
            self.assertLessEqual(rows[k][1], self.W3_MIXED_MAX, f"{k}: every block within W3's measured ceiling")
        self.assertLessEqual(sum(1 for p, q in zip(mips[1:], mips[2:]) if p != q), 2, f"no toggling on a threshold: {mips}")
        self.assertLessEqual(rows["index on a threshold"][0], self.W3_GATES["WT1 Sub FM"], "the threshold case's median")

    # ---- aliasing and brightness rows (plan 7.4; D5) ---------------------------------------------------------------------
    def test_alias_and_brightness_rows(self):
        """Plan 7.4's rows on the device's reader (WtRd: wt_play block by block, as wt_fm_run plays the golden): a stock-built saw
        table, a sine sub an octave down (A's known peak), Sub FM 500 and 1000 at notes ~60, ~72, ~96 (coherent: the carrier
        12 / 22 / 90 cycles in 4096 samples) for aliasing, Sub FM 250 and 500 at note ~60 for brightness. Each device row is
        the golden's (samples within 1e-5 of wt_fm_run at this build's threshold); the printed table sets this build beside
        the note level ("off") and stock's clean 1.25: the level, alias dB at 96 kHz and after stock's decimator (48 kHz, what
        the voice puts out), and on-grid energy above harmonic 16 dB (brightness)."""
        t = G.wt_build_levels(G.wt_saw_frames())
        w = WtRd(G.wt_saw_frames())
        lines = []
        for note, c, vs in ((60, 12, (250, 500, 1000)), (72, 22, (500, 1000)), (96, 90, (500, 1000))):
            rate = float(f32(c / 4096 * 2048)); inc = float(f32(rate) * f32(1 / 2048)); inc_sub = inc / 2
            for v in vs:
                w.set(G.wt_state())
                q, ys = 0.0, []
                pa = float(f32(inc_sub))
                for _ in range(24):
                    m, q = G.sub_mod("sine", inc_sub, 256, q)
                    a = (m * inc_sub).astype(np.float32).astype(np.float64)
                    y, _, _ = w.block(0, rate, 256, (a, G.an_index32(v), 0.0, a, 0.0, 0.0), pa)
                    ys.append(y)
                y = np.concatenate(ys)
                gy, _, ks = G.wt_fm_run(t, rate, 24, v, WT_THR)
                self.assertLessEqual(float(np.max(np.abs(y - gy))), 1e-5, f"note {note} Sub FM {v}: device = golden")
                row = [f"n~{note} SubFM {v}:"]
                for label, thr in (("this", WT_THR), ("note", "off"), ("1.25", 1.25)):
                    yy, kk = (y, ks) if label == "this" else G.wt_fm_run(t, rate, 24, v, thr)[::2]
                    _, al, hf = G.wt_spectrum_rows(yy[-4096:], c, c // 2)
                    _, al48, _ = G.wt_spectrum_rows(G.decimate(yy[-4096:]), c, c // 2)
                    row.append(f"{label} L{kk[-1][1]} al {al:.1f} / {al48:.1f} hf {hf:.1f}")
                lines.append(" | ".join(row))
        print("\n  WT FM W3 alias / brightness rows (device = golden; level, alias dB 96k / 48k, on-grid energy above "
              "harmonic 16 dB):\n    " + "\n    ".join(lines), file=sys.stderr)


# ---- BE4.44 W4 (docs/plan-wt-fm.md 3.5, 8 W4; Jeff's "Decisions before W4"): a Wavetable OSC1 as B, D9, the sync re-seed,
# the unrolled reader, lever (a), D2's last case, the costs
def wt_frame_stats(tab, frame, k):
    """D4 (wt_frame_dc's stated rule, written here from it; W4 review: at most 256 samples read). The level read: k's own at
    levels 4..6; level 4 for a level-0..3 frame (but frame 0), its mean and peak scaled back by sc = f32(G^-(4 - k)), G stock's
    builder's DC gain a level (the sum of STOCK_FIR's taps); frame 0 at levels 0..2 every st-th sample of k's own
    (st = 2^(3 - k)). Their mean mr from four float32 running sums (lanes j mod 4, then (0 + 1) + (2 + 3)) times 1 / their
    count; m = mr (k's own) or f32(mr sc), its float32 bits' low 11 cleared (the DC B loses). The peak max(max - a, a - min)
    about a = m (k's own) or mr (level 4), times sc (level 4), times 1.125 (level 4 or strided: not every sample of k), as
    q = floor(4096 peak) + 1 (B's bound in 1/4096, rounded up). Returns (m, q)."""
    c = 4 if k < 4 and frame else k
    st = 1 << (3 - k) if k < 3 and not frame else 1
    L = 2048 >> c
    f = np.asarray(tab[G.wt_level_off(c) + frame * L:G.wt_level_off(c) + (frame + 1) * L], np.float32)[::st]
    lanes = [np.float32(0.0)] * 4
    for i in range(0, len(f), 4):
        lanes = [np.float32(lanes[j] + f[i + j]) for j in range(4)]
    mr = np.float32(np.float32(np.float32(lanes[0] + lanes[1]) + np.float32(lanes[2] + lanes[3])) * np.float32(1.0 / len(f)))
    sc = np.float32(sum(float(x) for x in G.STOCK_FIR) ** -(4 - k)) if k < 4 else np.float32(1.0)
    m = mr if c == k else np.float32(mr * sc)
    m = np.frombuffer((np.frombuffer(m.tobytes(), "<u4") & 0xFFFFF800).tobytes(), "<f4")[0]
    ab = m if c == k else mr
    pk = max(np.float32(np.max(f) - ab), np.float32(ab - np.min(f)))
    if c != k:
        pk = np.float32(pk * sc)
    if c != k or st > 1:
        pk = np.float32(pk * np.float32(1.125))
    return float(m), min(int(np.float32(pk * np.float32(4096.0))) + 1, 0xFFFF)


def wt_seed64(st):
    """an_osc's seed() golden: WT1's kernel phase (idx + frac) / len as a 64-bit fraction (2^64 = one cycle), frac taken as
    (u32)(frac 2^32) (seed's own conversion)."""
    k = st.len.bit_length() - 1
    return ((st.idx << (64 - k)) + (int(np.float32(st.frac) * np.float32(4294967296.0)) << (32 - k))) % (1 << 64)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class WtFmW4(unittest.TestCase):
    """BE4.44 W4 (docs/plan-wt-fm.md 3.5, 7.3, 8 W4): FM 1>2 from a Wavetable OSC1 (B = its block before Level x its
    increment: from stock's own kernel at gate 0, wt_kern, bit for bit, or from the reader when OSC1 has Sub FM; its known
    peak for a Wavetable OSC2, wt_frame_dc), into an Analog and a Wavetable OSC2; D9 (b) (a silent feeding OSC1 runs on for
    B: the divergence test); the mixed-sync re-seed (Jeff: the slave's copy follows WT1 block by block); the unrolled steady
    loop (wt_rdc: rd_q's bit for bit); lever (a) (a note's first block plays the FM level, no crossfade); D2's last case (a
    BE4.43 project's hidden FM 1>2 on an Analog OSC2 next to a Wavetable OSC1 now plays: the reviewed behaviour change);
    the W4 voice costs."""
    replay = SubFm.replay
    lines = SubFm.lines
    voice, note, fm12_play = SubCopy.voice, SubCopy.note, Fm12.play   # (Fm12.play's rig: W4 review's no-WAV sync case)

    # ---- the unrolled reader (wt_rdc) ----------------------------------------------------------------------------------
    def rd_call(self, w, fn, k, frame, ph, inc, a, ia, n, o0=0, dia=0.0, b=None, ib=0.0, frame1=None):
        r = w.r
        g = getattr(self, "_g", None) or r.alloc(48)
        self._g = g
        r.u.mem_write(w.a, np.asarray(a, "<f4").tobytes())
        bb = w.a
        if b is not None:
            r.u.mem_write(w.b, np.asarray(b, "<f4").tobytes()); bb = w.b
        r.u.mem_write(w.fi, struct.pack("<IffIIff", w.a, ia, dia, 0, bb, ib, 0.0))
        l = 11 - k
        t0 = r.tab + 4 * (G.wt_level_off(k) + (frame << l))
        t1 = r.tab + 4 * (G.wt_level_off(k) + ((frame if frame1 is None else frame1) << l))
        r.u.mem_write(g, struct.pack("<IIIIIIIIfII", w.out, t0, t1, o0, 0, l, l, ph, inc, w.fi, n))
        r.u.mem_write(w.out, b"\xAA" * (4 * 1024))
        _, c = r.call(DSP[fn] & ~1, g)
        return bytes(r.u.mem_read(w.out, 4 * n)), struct.unpack("<I", r.u.mem_read(g + 28, 4))[0], c

    def test_the_unrolled_loop_is_rd_qs_bit_for_bit(self):
        """Jeff, "Decisions before W4": the unrolled reader. On a one-route line (b = a, B's term zero: every Wavetable
        carrier's) wt_rdc (constant line) and wt_rdr (a ramp), two samples a turn, against wt_rd, and wt_rdfc (the frame
        crossfade) against wt_rdf, on the same arguments: the samples and the phase out bit for bit, on stock's builder saw at
        levels 0, 3 and 6 (len 2048 / 256 / 32), lines from index 0 to through zero and the clamp (I 0, 1, 16, 60), ramps up and
        down, odd and even n (37, 255, 256), phases near a wrap and a level skew offset. And wt_play routes by the line:
        constant one route wt_rdc, a ramp wt_rdr, a second route wt_rd; a frame change wt_rdfc, with a second route wt_rdf; no
        line (the mip-home block) wt_rdc (Span calls)."""
        w = WtRd(G.wt_saw_frames(200))
        rng = np.random.default_rng(44)
        cases = 0
        for k in (0, 3, 6):
            rate = float(f32(0.7 * 2 ** k)); inc = float(f32(rate) * f32(1 / 2048))
            for I in (0.0, 1.0, 16.0, 60.0):
                for n in (37, 255, 256):
                    a = (np.sin(2 * np.pi * np.arange(n) * 0.013 + rng.uniform(0, 6)) * inc).astype(np.float32)
                    ph = int(rng.integers(0, 1 << 32)) if n != 255 else 0xFFFFFF00
                    o0 = (7 * ((1 << 2) - 1)) << 21 if n == 37 else 0
                    y0, p0, _ = self.rd_call(w, "wt_rd", k, 9, ph, inc, a, I, n, o0)
                    y1, p1, _ = self.rd_call(w, "wt_rdc", k, 9, ph, inc, a, I, n, o0)
                    self.assertEqual((y1, p1), (y0, p0), f"level {k} I {I} n {n}: wt_rdc = wt_rd bit for bit")
                    dia = float(f32(f32((I + 3.0) * (1 if n % 2 else -1) - I) / n)) if I else float(f32(2.0 / n))
                    y0, p0, _ = self.rd_call(w, "wt_rd", k, 9, ph, inc, a, I, n, o0, dia=dia)
                    y1, p1, _ = self.rd_call(w, "wt_rdr", k, 9, ph, inc, a, I, n, o0, dia=dia)
                    self.assertEqual((y1, p1), (y0, p0), f"level {k} I {I} n {n} dia {dia}: wt_rdr = wt_rd bit for bit")
                    y0, p0, _ = self.rd_call(w, "wt_rdf", k, 9, ph, inc, a, I, n, o0, dia=dia, frame1=200)
                    y1, p1, _ = self.rd_call(w, "wt_rdfc", k, 9, ph, inc, a, I, n, o0, dia=dia, frame1=200)
                    self.assertEqual((y1, p1), (y0, p0), f"level {k} I {I} n {n} dia {dia}: wt_rdfc = wt_rdf bit for bit")
                    cases += 3
        self.assertGreater(len(set(np.frombuffer(y0, "<f4"))), 100, "control: a block that moves")
        rate = wt_rate(60); inc = float(f32(rate) * f32(1 / 2048))
        st = G.wt_state(idx=9, frac=0.1, frame=0, flag=0, k=3)
        loops = ("wt_rd", "wt_rdc", "wt_rdr", "wt_rdf", "wt_rdfc")
        # (small indices: the note's level at any threshold the build has, so no case is a mip change)
        for name, line, req, want in (("constant", wt_line(inc, 256, 0.5, 0.5), 0, "wt_rdc"),
                                      ("ramp", wt_line(inc, 256, 0.5, 0.6), 0, "wt_rdr"),
                                      ("two routes", wt_line(inc, 256, 0.5, 0.5, J=0.25, J1=0.25), 0, "wt_rd"),
                                      ("no line", None, 0, "wt_rdc"), ("frame", wt_line(inc, 256, 0.5, 0.6), 5, "wt_rdfc"),
                                      ("frame, two routes", wt_line(inc, 256, 0.5, 0.5, J=0.25, J1=0.25), 5, "wt_rdf")):
            w.set(st)
            sp = {f: Span(w.r, DSP[f]) for f in loops if f in DSP}
            y, _, _ = w.block(req, rate, 256, line)
            want_y, _, _ = G.wt_read_block(w.tab, st, req, rate, 256, line, WT_THR)
            self.assertLessEqual(float(np.max(np.abs(y - want_y))), TOL_RD_XF, f"{name}: the golden's")
            self.assertEqual({f: s.calls for f, s in sp.items()}, {f: int(f == want) for f in loops}, f"{name}: {want}")
        print(f"\n  WT FM W4 wt_rdc = wt_rd bit for bit on {cases} blocks", file=sys.stderr)

    # ---- lever (a) ------------------------------------------------------------------------------------------------------
    @wt_rule
    def test_a_notes_first_block_plays_the_fm_level(self):
        """Jeff, "Decisions before W4", lever (a): a note that starts under an amount (Sub FM 1000 on a Wavetable OSC1: the FM
        level is coarser than the note's) reads that level from its first block's first sample: the block is a plain one
        (rd_q, not rd_qm), its levels (kt, kt), every block the golden's (wt_replay, the golden's lever too), and the same at a
        retrigger (block 6: an_note sends the mip home, stock's pending flag starts the note again). Cost: the note's first
        block (FM ramping in from 0: the ramp loop, wt_rdr) within 2k of the steady median (W3: +5.4k over it, the rd_qm
        crossfade on top of the ramp)."""
        w = WtVoice(self, sub=1000, frames=G.wt_saw_frames(200))
        w.run(10, notes=(6,))
        by = w.by_block(0)
        kn = G.wt_ladder(by[1]["rate"])
        for b in (0, 6):
            x = by[b]
            self.assertEqual(x["st"]["flag"], 1, f"block {b}: a note's first (the pending flag)")
            self.assertEqual(x["kind"], "rd_q", f"block {b}: a plain read, no crossfade")
            self.assertGreater(x["st1"]["mip"], kn, f"block {b}: at the FM level")
        worst, kinds = wt_replay(self, "lever (a)", w, w.wt)
        self.assertNotIn("rd_qm", kinds, "no mip-change block in the run")
        per = WtFmW3.voice_cost(self, sub=1000, blocks=12)
        med = sorted(per[4:])[len(per[4:]) // 2]
        self.assertLessEqual(per[0], med + 2000, f"the note's first block {per[0]} vs the median {med}")

    # ---- B from a Wavetable OSC1 ------------------------------------------------------------------------------------------
    def b_run(self, types2, sub=0, x12=700, note=60, blocks=10, pos=None, frames=None):
        """A voice with a Wavetable OSC1 feeding B to OSC2 (Analog: types2 1; Wavetable: 0), Position pos(b) on OSC1; per block
        b: OSC1's kernel state before it (rec[b][0]), and the frame and rate OSC1's body played in it (rec[b + 1][1:])."""
        w = WtVoice(self, types=(0, types2), sub=sub, x12=x12, note=note, frames=G.wt_saw_frames(200) if frames is None else frames)
        rec = []
        def snap(w):
            rec.append((w.kernel(0), w.r.r32(w.o[0] + 0x20), w.r.rf(w.o[0] + WT_OSC_RATE)))
        def each(w, b):
            if pos:
                w.r.wf(w.o[0] + OSC_POS_SRC, pos(b))
            snap(w)
        w.run(blocks, each)
        snap(w)
        return w, rec

    def test_b_from_a_wavetable_osc1(self):
        """Plan 3.5 / 7.3 / D4: FM 1>2 700 from a Wavetable OSC1 (stock's builder saw, Position moving in blocks 4 and 5) into
        an Analog and into a Wavetable OSC2, with OSC1 at gate 0 and with OSC1's own Sub FM 600 (the reader). Every block OSC1
        feeds B once (check_wt_feeds: B = f32(f32(s - m) inc), OSC2 reads exactly it); s is OSC1's block before Level: at gate 0
        stock's kernel's samples bit for bit (wt_kmodel replayed from OSC1's kernel state before the block, at the frame and
        rate it played: 7.3's B path), under Sub FM the reader's (the wt_capture record's samples); m is the mean of the frame
        OSC1 ended the block on, at its level (wt_frame_stats); every reader block of either oscillator the golden's; an
        Analog OSC2's FM calls the kernel's golden. The Wavetable OSC2's pa is B's known peak: f32(inc1) x q / 4096 (q the
        frame's peak about m, rounded up), exactly in a block whose frame and level did not change, and at least that in one
        that did; a tight bound on B (max |B| within pa and above 0.9 pa). OSC1's saved runs hold them: wxf's key, WT_PK_OK,
        AnProps.glide = q."""
        for t2, sub in ((1, 0), (0, 0), (1, 600), (0, 600)):
            with self.subTest(osc2="Analog" if t2 else "Wavetable", osc1_sub_fm=sub):
                name = f"OSC2 {'Analog' if t2 else 'Wavetable'}, OSC1 Sub FM {sub}"
                w, rec = self.b_run(t2, sub=sub, pos=lambda b: 0.0 if b < 4 else 0.3 if b == 4 else 0.6)
                o1, o2 = w.o
                self.assertEqual(check_wt_feeds(self, name, w.fm, o1, o2), 10, f"{name}: OSC1 fed B every block")
                rd1 = w.by_block(0)
                feeds = {f["block"] - w.first: f for f in w.fm.feeds}
                keys = {}
                for b, f in sorted(feeds.items()):
                    s = f["s"].astype(np.float32)
                    k1 = KM.St(rec[b + 1][0])
                    lv = rd1[b]["st1"]["mip"] if b in rd1 and rd1[b]["st1"]["mip"] < WT_MIPS_N else 12 - k1.len.bit_length()
                    keys[b] = (k1.frame, lv)
                    self.assertEqual(f32(f["m"]), f32(wt_frame_stats(w.tab, *keys[b])[0]), f"{name}: block {b}: m, the frame's DC")
                    if sub:                                     # OSC1 under Sub FM: the reader, B from its samples
                        self.assertTrue(np.array_equal(s, rd1[b]["y"].astype(np.float32)), f"{name}: block {b}: s the reader's")
                        continue
                    self.assertNotIn(b, rd1, f"{name}: block {b}: gate 0, no reader")
                    m = KM.St(rec[b][0])
                    got = KM.kernel(m, w.tab, rec[b + 1][1], rec[b + 1][2], 256)
                    self.assertEqual([np.float32(v) for v in got], list(s), f"{name}: block {b}: s is stock's kernel bit for bit")
                    self.assertEqual(m.pack(), rec[b + 1][0], f"{name}: block {b}: and OSC1's kernel state is stock's")
                mk, q = wt_frame_stats(w.tab, *keys[9])
                wxf, = struct.unpack("<I", w.r.u.mem_read(o1 + S.OSC_KERNEL + WXF_AT, 4))
                fl = w.r.u.mem_read(o1 + S.OSC_KERNEL + WT_FLAGS_AT, 1)[0]
                self.assertEqual((wxf & 0x7FF, wxf & ~0x7FF, fl & WT_PK_OK, r_settings(w.r, o1)[4]),
                                 (keys[9][0] | keys[9][1] << 8, struct.unpack("<I", struct.pack("<f", mk))[0], WT_PK_OK, q),
                                 f"{name}: the saved runs hold the key, m, WT_PK_OK and q")
                if sub:
                    wt_replay(self, name + " (OSC1)", w, [x for x in w.wt if x["osc"] == o1])
                if t2:
                    self.replay(name, w.fm)
                    self.assertTrue(w.fm and all(x["osc"] == o2 and x["A"] == SA + SA_FMBUF_B for x in w.fm))
                    continue
                rd2 = [x for x in w.wt if x["osc"] == o2]
                self.assertTrue(rd2 and all(x["line"] is not None and x["A"] == SA + SA_FMBUF_B for x in rd2))
                wt_replay(self, name, w, rd2)
                if WT_THR == "off":
                    self.assertTrue(all(x["pa"] == 0.0 for x in rd2), "off: no peak passed")
                    continue
                bmax, pas = 0.0, []
                for x in rd2:
                    b = x["block"] - w.first
                    f = feeds[b]
                    bmax = max(bmax, float(np.max(np.abs(f["b"]))))
                    want = f32(f32(f["inc"]) * f32(wt_frame_stats(w.tab, *keys[b])[1] / 4096))
                    if b and keys.get(b - 1) == keys[b]:
                        self.assertEqual(f32(x["pa"]), want, f"{name}: block {b}: pa = inc1 x the frame's peak")
                    else:
                        self.assertGreaterEqual(f32(x["pa"]), want, f"{name}: block {b}: a change block, at least the new one")
                    pas.append(x["pa"])
                pa = max(pas)
                self.assertLessEqual(bmax, pa, f"{name}: max |B| {bmax:.6g} within pa {pa:.6g}")
                self.assertGreater(bmax, pa * 0.9, f"{name}: a tight bound")


    @wt_rule
    def test_b_known_peak_holds_the_level_at_low_notes(self):
        """W3 review's "W4 must set peak_b": FM 1>2 from a Wavetable OSC1 (stock's builder saw) into a Wavetable OSC2 at notes
        24 / 36 / 48 / 60 and FM 1>2 300 / 500 / 700 / 1000, 40 blocks: OSC2's level changes at most once after the entry
        block (a scan of B followed OSC1's phase where its cycle spans blocks: W3 measured 30 of 44 blocks flipping)."""
        rows = []
        for note in (24, 36, 48, 60):
            for amt in (300, 500, 700, 1000):
                w = WtVoice(self, types=(0, 0), x12=amt, note=note, frames=G.wt_saw_frames(200), capture=True)
                w.run(40)
                by = w.by_block(1)
                mips = [by[b]["st1"]["mip"] for b in sorted(by) if b >= 1]
                ch = sum(1 for p, q in zip(mips, mips[1:]) if p != q)
                rows.append(f"n{note}/{amt}: L{mips[-1]} {ch}")
                self.assertLessEqual(ch, 1, f"note {note} FM 1>2 {amt}: {mips}")
                self.assertTrue(all(by[b]["pa"] > 0 for b in by), f"note {note} FM 1>2 {amt}: B's peak known every block")
        print("\n  WT FM W4 B's known peak from a Wavetable OSC1, levels held (note / FM 1>2: level, changes): " +
              ", ".join(rows), file=sys.stderr)

    def test_feeding_follows_fm12_and_its_ramp(self):
        """The feed-B bit's life (3.5, W4): FM 1>2 600 on OSC2 (Analog, and Wavetable) for blocks 0..3, then 0: the Wavetable
        OSC1 feeds B in blocks 0..4 (block 4 is OSC2's ramp out, 600 -> 0, which needs B: wt_feed keeps the bit while OSC2's
        m12 is not 0) and never after; nothing is missed (fmx= 0); once OSC2's FM 1>2 and its ramp are over, wt_body clears
        the bit and OSC1, with nothing else to do, untags (wt_settle): from then on it is stock's render untouched (no tag)."""
        for t2 in (1, 0):
            with self.subTest(osc2="Analog" if t2 else "Wavetable"):
                w = WtVoice(self, types=(0, t2), x12=600, frames=G.wt_saw_frames(200))
                tags = []
                def each(w, b):
                    if b == 4:
                        w.prop(K_FM12, 0, 1)
                    tags.append(wt_tagged(bytes(w.r.u.mem_read(w.o[0] + S.OSC_KERNEL, 8)), 0, w.o[0]))
                w.run(10, each)
                fed = sorted({f["block"] - w.first for f in w.fm.feeds})
                self.assertEqual(fed, [0, 1, 2, 3, 4], "B in every block OSC2's FM 1>2 or its ramp plays")
                self.assertEqual(w.fmstats()[FM_MISSED // 4], 0, "nothing missed (the ramp out heard B)")
                if t2:
                    self.lines("feed life", w.fm, w.o[1], {w.first + b: (0 if b == 0 else 600, 0 if b == 4 else 600) for b in range(5)})
                self.assertTrue(all(tags[:5]), "OSC1 tagged while it feeds")
                self.assertFalse(any(tags[6:]), f"OSC1 untagged once the ramp is over ({tags})")

    def test_d4_a_frames_dc_stays_out_of_b(self):
        """D4 (decided on W4's data: a pulse frame's mean is up to 0.9, a half-wave's 0.32, and through B it detunes OSC2 at
        any amount, by J inc1 m: OSC2 ran backwards at FM 1>2 250 on a narrow pulse): a Wavetable OSC1 at gate 0 on a PWM table
        (band-limited pulses, width 0.5 down to 0.05 over the frames; Position at a narrow one, mean about -0.8) feeding FM 1>2
        500 to an Analog OSC2. B over whole OSC1 cycles averages 0 (within 2e-3 of inc1), where OSC1's own samples average
        the frame's DC (the control); OSC2's FM calls are the golden's. And on stock's builder saw (mean 0 by construction; a
        frame away from frame 0, whose coarser levels start with the builder's zero padding) |m| is below 1e-4 (W4 review: m
        read on level 4 and scaled back, 3.2e-5 here: under 1 cent at FM 1>2 500): the subtraction is next to a no-op where a
        table has no DC."""
        x = np.arange(2048) / 2048
        def pulse(w, h=48):
            return 2 * w - 1 + sum(4 / (np.pi * k) * np.sin(np.pi * k * w) * np.cos(2 * np.pi * k * (x - w / 2)) for k in range(1, h))
        frames = np.stack([pulse(0.5 - 0.45 * f / 255) for f in range(256)]).astype(np.float32)
        w = WtVoice(self, types=(0, 1), x12=500, frames=frames, note=48)
        w.r.wf(w.o[0] + OSC_POS_SRC, 0.9)
        w.run(16)
        fs = [f for f in w.fm.feeds if f["block"] - w.first >= 2]
        s = np.concatenate([f["s"] for f in fs]).astype(np.float64)
        b = np.concatenate([f["b"] for f in fs]).astype(np.float64)
        inc = fs[0]["inc"]
        n = int(round(int(len(b) * inc) / inc))                # (whole OSC1 cycles)
        s, b = s[:n], b[:n]
        self.assertLess(float(np.mean(s)), -0.6, f"control: the frame's DC ({np.mean(s):.3f})")
        self.assertLess(abs(float(np.mean(b))) / inc, 2e-3, f"B's DC {np.mean(b) / inc:.2e} x inc1 (OSC1's {np.mean(s):.3f})")
        self.replay("D4", w.fm)
        w2 = WtVoice(self, types=(0, 1), x12=500, frames=G.wt_saw_frames(200))
        w2.r.wf(w2.o[0] + OSC_POS_SRC, 0.5)
        w2.run(3)
        ms = [abs(f["m"]) for f in w2.fm.feeds]
        self.assertLess(max(ms), 1e-4, f"a saw: m about 0 ({ms})")
        print(f"\n  WT FM W4 D4: PWM frame DC {np.mean(s):.3f}: B's mean {np.mean(b) / inc:+.2e} x inc1", file=sys.stderr)

    # ---- D9 (b) --------------------------------------------------------------------------------------------------------
    def test_d9_a_silent_osc1_keeps_feeding(self):
        """D9 (b) (7.3's named divergence test): a Wavetable OSC1 feeding FM 1>2 600 to an Analog OSC2, OSC1's Level 0 in
        blocks 4..11: OSC1 keeps running for B (its kernel phase moves in every silent block, unlike stock's, which freezes:
        the control, the same voice with FM 1>2 0), feeds B every block, and OSC2's FM 1>2 continues (its line I(600) held,
        every call the golden's). After the silence OSC1's phase is not stock's frozen one (the divergence), and its return is
        click-free: Level ramps in from 0 through stock's smoother (OSC2 at Level 0 here, so the voice's output is OSC1 and
        the sub: its largest step at the block where OSC1 is heard again is within 1.5 x the run's own largest)."""
        lv = lambda b: 0.0 if 4 <= b <= 11 else 1.0
        runs = {}
        for x12, l2 in ((600, 1.0), (0, 1.0), (600, 0.0)):
            w = WtVoice(self, types=(0, 1), x12=x12, frames=G.wt_saw_frames(200))
            ks = []
            def each(w, b, l2=l2):
                w.r.level(w.v, 0, lv(b)); w.r.level(w.v, 1, l2)
                ks.append(w.kernel(0))
            w.run(16, each)
            ks.append(w.kernel(0))
            runs[(x12, l2)] = (w, ks)
        w, ks = runs[(600, 1.0)]
        _, ks0 = runs[(0, 1.0)]
        frozen0 = [b for b in range(4, 12) if ks0[b + 1] == ks0[b]]
        self.assertTrue(frozen0, "control: without feeding, stock's kernel freezes at Level 0")
        self.assertTrue(all(ks[b + 1] != ks[b] for b in range(16)), "feeding: OSC1's kernel runs in every block (D9 (b))")
        self.assertNotEqual(ks[12], ks0[12], "the divergence: OSC1's phase after the silence is not stock's frozen one")
        self.assertEqual(sorted({f["block"] - w.first for f in w.fm.feeds}), list(range(16)), "B every block")
        o2 = w.o[1]
        by = {}
        for x in w.fm:
            by.setdefault(x["block"] - w.first, set()).add((x["ia"], x["dia"]))
        self.assertTrue(all(by.get(b) == {(an_index(600), 0.0)} for b in range(1, 16)), "OSC2's FM 1>2 held through the silence")
        self.replay("D9", w.fm)
        y = np.frombuffer(b"".join(runs[(600, 0.0)][0].outs), "<f4").astype(np.float64)   # (OSC2 silent: OSC1 and the sub)
        d = np.abs(np.diff(y))
        at = 12 * DEV_N                                      # (the voice's output: N frames a block)
        step = float(np.max(d[at - 1:at + 8]))
        rest = float(np.max(np.r_[d[:at - 1], d[at + 8:]]))
        self.assertLessEqual(step, 1.5 * rest, f"OSC1 heard again: step {step:.4f} vs the run's own {rest:.4f}")

    # ---- the mixed-sync re-seed ---------------------------------------------------------------------------------------------
    def test_mixed_sync_reseeds_the_slaves_copy_every_block(self):
        """Jeff ("Decisions before W4"): an Analog OSC2 synced to a Wavetable OSC1 that feeds it B (FM 1>2 600): sync and FM 1>2
        both play. Golden (wt_seed64, seed()'s arithmetic): after every block but the note's first (mrestart: the copy
        restarts at 0 with WT1's reset), the slave's copy of WT1's phase is WT1's kernel phase at the block's end, exactly; so
        the slave follows the phase WT1 really has, block by block. WT1's phase moved by hand before block 6 (idx + 37: any
        departure from the constant-rate copy, as a mip-home block or D9's running would be): the copy follows at once. Control:
        the same voice without FM 1>2 (no feeding: no re-seed) does not follow the hand-moved phase (BE4.43's copy runs on).
        OSC2's FM calls are the golden's with the master's events (replay), B the Wavetable OSC1's."""
        res = {}
        for x12 in (600, 0):
            w = WtVoice(self, types=(0, 1), sync=1, x12=x12, frames=G.wt_saw_frames(200))
            cps = []
            def each(w, b):
                if b == 6:
                    k = w.o[0] + S.OSC_KERNEL + 0xC0
                    st = KM.St(w.r.u.mem_read(k, 25))
                    st.idx = (st.idx + 37) % st.len
                    w.r.u.mem_write(k, st.pack())
            for b in range(12):
                each(w, b)
                w.block()
                hi, lo = struct.unpack("<2I", w.r.u.mem_read(w.o[1] + S.OSC_KERNEL + 156, 8))
                cps.append(((hi << 32) | lo, wt_seed64(KM.St(w.kernel(0)))))
            res[x12] = (w, cps)
        w, cps = res[600]
        for b, (cp, want) in enumerate(cps):
            if b == 0:
                self.assertLess(min((cp - want) % (1 << 64), (want - cp) % (1 << 64)), 1 << 44, "block 0: close (the restart)")
                continue
            self.assertEqual(cp, want, f"block {b}: the slave's copy is WT1's phase (re-seeded)")
        _, c0 = res[0]
        self.assertNotEqual(c0[6][0], c0[6][1], "control: without feeding the copy runs on (no re-seed)")
        self.replay("re-seed", w.fm)
        self.assertTrue(w.fm and all(x["role"] == 2 and x["A"] == SA + SA_FMBUF_B for x in w.fm), "the slave, FM 1>2 from B")
        self.assertGreater(sum(e != 255 for x in w.fm for e in x["ev0"]), 0, "control: the master's wraps")
        self.assertEqual(check_wt_feeds(self, "re-seed", w.fm, w.o[0], w.o[1]), 12)

    def test_d4_the_frame_read_is_level_4_at_low_notes(self):
        """W4 review (CPU): wt_frame_dc reads at most 256 samples, whatever level OSC1 plays: on a PWM table (band-limited
        pulses 0.5 -> 0.05) with Position moving every block, FM 1>2 500 into an Analog OSC2, at notes 24 / 36 / 48 / 60 (levels
        0 .. 3) and 72 (level 4): the last block's m and OSC1's saved q are wt_frame_stats' (level 4 scaled back by G^-(4 - k);
        k's own from level 4 on), and close to the played level's own frame (its exact mean within 2e-4; the peak, rounded up,
        within -2 % .. +16 % of the frame's: the 1.125 margin on a pulse's 0.98 .. 1.07): the cheap read keeps D4. Frame 0
        (Position 0) at notes 24 / 48: the played level's every st-th sample (st = 2^(3 - k)), its mean within 2e-3 of the
        frame's. The read's cost: at most 800 instructions a block with Position moving (the whole level-0 frame was 10.3k at
        note 24: W4 review)."""
        x = np.arange(2048) / 2048
        def pulse(wd, h=48):
            return 2 * wd - 1 + sum(4 / (np.pi * k) * np.sin(np.pi * k * wd) * np.cos(2 * np.pi * k * (x - wd / 2))
                                    for k in range(1, h))
        frames = np.stack([pulse(0.5 - 0.45 * f / 255) for f in range(256)]).astype(np.float32)
        rows = []
        for note, pos in ((24, None), (36, None), (48, None), (60, None), (72, None), (24, 0.0), (48, 0.0)):
            with self.subTest(note=note, frame0=pos is not None):
                w = WtVoice(self, types=(0, 1), x12=500, frames=frames, note=note)
                sp = Span(w.r, dsp_sym("wt_frame_dc"))
                costs = []
                def each(w, b):
                    w.r.wf(w.o[0] + OSC_POS_SRC, pos if pos is not None else (b * 37 % 255) / 255)
                    costs.append(sp.total)
                w.run(8, each)
                costs.append(sp.total)
                k1 = KM.St(w.kernel(0))
                k, fr = 12 - k1.len.bit_length(), k1.frame
                m, q = wt_frame_stats(w.tab, fr, k)
                self.assertEqual(f32(w.fm.feeds[-1]["m"]), f32(m), f"note {note}: m is wt_frame_stats'")
                self.assertEqual(r_settings(w.r, w.o[0])[4], q, f"note {note}: q is wt_frame_stats'")
                L = 2048 >> k
                own = np.asarray(w.tab[G.wt_level_off(k) + fr * L:G.wt_level_off(k) + (fr + 1) * L], np.float64)
                mt = float(np.mean(own))
                pt = max(own.max() - mt, mt - own.min())
                self.assertLess(abs(m - mt), 2e-3 if pos is not None else 2e-4, f"note {note}: m {m:.6f} vs the frame's {mt:.6f}")
                self.assertTrue(0.98 * pt <= q / 4096 <= 1.16 * pt + 1 / 4096, f"note {note}: q / 4096 {q / 4096:.5f} vs {pt:.5f}")
                per = [b - a for a, b in zip(costs, costs[1:])][1:]
                rows.append(f"n{note}{' frame 0' if pos is not None else ''} L{k}: {max(per)}")
                if pos is None:
                    self.assertLessEqual(max(per), 800, f"note {note}: the frame read's cost a block")
        print("\n  WT FM W4 D4 frame read (note, level: wt_frame_dc instructions a block, max): " + ", ".join(rows), file=sys.stderr)

    @wt_rule
    def test_b_known_peak_covers_every_block_of_a_sweep(self):
        """W4 review: the change-block cover (wt_frame_dc: old peak + |old mean - new mean|). A PWM table, Position swept from
        the narrow pulses (a large peak about a large mean) toward the wide ones (smaller), so each change block played a frame
        with a larger peak than the new one; WT1 -> WT2 FM 1>2 500, OSC1 at gate 0 and with Sub FM 300, notes 72 and 84 (levels
        4 and 5: wt_frame_dc reads OSC1's own level, the peak exact): in every block OSC2 plays, max |B| of that block is
        within its pa (not only over the run)."""
        x = np.arange(2048) / 2048
        def pulse(wd, h=48):
            return 2 * wd - 1 + sum(4 / (np.pi * k) * np.sin(np.pi * k * wd) * np.cos(2 * np.pi * k * (x - wd / 2))
                                    for k in range(1, h))
        frames = np.stack([pulse(0.5 - 0.45 * f / 255) for f in range(256)]).astype(np.float32)
        for sub, note in ((0, 72), (300, 72), (0, 84), (300, 84)):
            with self.subTest(sub=sub, note=note):
                w = WtVoice(self, types=(0, 0), sub=sub, x12=500, frames=frames, note=note)
                w.run(30, lambda w, b: w.r.wf(w.o[0] + OSC_POS_SRC, max(0.0, 0.95 - 0.06 * b)))
                feeds = {f["block"] - w.first: f for f in w.fm.feeds}
                rd2 = [x for x in w.wt if x["osc"] == w.o[1]]
                self.assertGreater(len(rd2), 20, "OSC2 under FM every block")
                for x in rd2:
                    b = x["block"] - w.first
                    bm = float(np.max(np.abs(feeds[b]["b"])))
                    self.assertLessEqual(bm, x["pa"], f"sub {sub} note {note}: block {b}: max |B| {bm:.6g} within pa {x['pa']:.6g}")

    def test_mixed_sync_reseed_waits_for_wt1s_phase_to_move(self):
        """W4 review: the re-seed's gate (an_osc re-seeds the slave's copy only when WT1's kernel phase moved). A Wavetable OSC1
        with no WAV (stock's render returns at once: its phase never moves, WT1 is silent) next to an Analog OSC2 synced to it
        with FM 1>2 1000 (the feed-B bit set; B never written): the copy advances by the same non-zero step every block, as
        BE4.43's (the FM 1>2 0 run, no feeding), and the voice's output is that run's byte for byte. Without the gate the copy
        would be put back on WT1's frozen phase every block (the slave synced at the block rate)."""
        res = {}
        for x12 in (1000, 0):
            cps = []
            def each(r, root, b, cps=cps):
                hi, lo = struct.unpack("<2I", r.u.mem_read(r.osc(r.v, 1) + S.OSC_KERNEL + 156, 8))
                cps.append((hi << 32) | lo)
            calls, outs, stats, _, _ = self.fm12_play(x12=x12, types=(0, 1), sync=1, blocks=12, each=each, note=48)
            res[x12] = (cps, outs, calls)
        cps, outs, calls = res[1000]
        d = [(q - p) % (1 << 64) for p, q in zip(cps, cps[1:])]
        self.assertTrue(d[1] != 0 and len(set(d[1:])) == 1, f"the copy advances by one step every block ({d})")
        self.assertEqual(cps, res[0][0], "the copy is BE4.43's (FM 1>2 0, no feeding)")
        self.assertEqual(outs, res[0][1], "the voice's output is FM 1>2 0's byte for byte")
        self.assertEqual(calls.feeds, [], "no WAV: nothing written to B")

    def test_mixed_sync_keeps_the_slaves_restart_at_a_note_start(self):
        """W4 review: the re-seed skips the block whose copy restarted (mrestart): at a note start with WTOscReset on, an_note
        restarts the copy at 0 and marks mrestart, and the slave's first FM call of that block restarts at sample 0
        (events[0] = 0: analog_kernel's an_master). A Wavetable OSC1 feeding an Analog OSC2 synced to it (FM 1>2 600), the
        note again at block 2 (a retrigger), notes 36 / 48 / 60 / 72: blocks 0 and 2 start with that restart. A re-seed
        there would clear mrestart first and drop it."""
        for note in (36, 48, 60, 72):
            with self.subTest(note=note):
                w = WtVoice(self, types=(0, 1), sync=1, x12=600, note=note, frames=G.wt_saw_frames(200))
                w.run(4, notes=(2,))
                for b in (0, 2):
                    first = [x for x in w.fm if x["osc"] == w.o[1] and x["block"] - w.first == b]
                    self.assertTrue(first and first[0]["role"] == 2, f"note {note} block {b}: the slave under FM 1>2")
                    self.assertEqual(int(first[0]["ev0"][0]), 0, f"note {note} block {b}: the restart at sample 0")
                self.assertEqual(check_wt_feeds(self, f"restart n{note}", w.fm, w.o[0], w.o[1]), 4)

    def test_a_long_block_or_another_voice_feeds_nothing(self):
        """W4 review: wt_body's other two feed guards. (1) A block longer than the FM buffers (512 frames; AN_FM_FRAMES 256) with
        a Wavetable OSC1 holding the feed-B bit (an Analog OSC2 with FM 1>2 600): nothing is written to B or past its end (a
        pattern there stays), no feed, FM 1>2 counted skipped (fmx=) each block. (2) The voice an_enter named: after an_enter,
        Run.voice set to another voice's value, OSC1's render writes nothing to B and sets no B tag; the control (Run.voice
        this voice's) feeds B and tags it."""
        m = Mixed(sync=0, frames=512)
        m.prop(K_FM12, 600)
        calls = fm_capture(m.r)
        m.note(60)
        pat = struct.pack("<I", 0x5A5A5A5A) * 512
        st0 = struct.unpack("<8I", m.r.u.mem_read(SA + SA_FMSTATS, 32))
        for b in range(4):
            m.r.u.mem_write(SA + SA_FMBUF_B, pat)
            m.block(0.0)
            self.assertEqual(bytes(m.r.u.mem_read(SA + SA_FMBUF_B, len(pat))), pat, f"block {b}: B and past it untouched")
        st = struct.unpack("<8I", m.r.u.mem_read(SA + SA_FMSTATS, 32))
        self.assertEqual(calls.feeds, [], "a long block: no feed")
        self.assertGreaterEqual(st[FM_SKIPPED // 4] - st0[FM_SKIPPED // 4], 4, "fmx= skipped each block")
        self.assertGreater(max(abs(v) for v in m.x1), 0.1, "control: OSC1 plays")
        for other in (False, True):
            with self.subTest(another_voice=other):
                w = WtVoice(self, types=(0, 1), x12=600, frames=G.wt_saw_frames(200))
                w.run(2)
                r = w.r
                out, _ = r.buf(DEV_FRAMES)
                scr, _ = r.buf(DEV_FRAMES)
                r.call(DSP["an_enter"], w.root, w.v)
                if other:
                    r.w32(SA + SA_RUN + 8, w.v + 0x40)
                r.u.mem_write(SA + SA_FMBUF_B, pat[:4 * AN_FM_FRAMES])
                f0 = len(w.fm.feeds)
                r.call(r.r32(r.r32(w.o[0])) & ~1, w.o[0], scr, out)
                fed = len(w.fm.feeds) - f0
                same_b = bytes(r.u.mem_read(SA + SA_FMBUF_B, 4 * AN_FM_FRAMES)) == pat[:4 * AN_FM_FRAMES]
                tag = r.r32(SA + SA_RUN + RUN_FMTAG) & 2
                if other:
                    self.assertEqual((fed, same_b, tag), (0, True, 0), "another voice: no B, no tag")
                else:
                    self.assertEqual((fed, same_b, tag), (1, False, 2), "control: this voice feeds B")

    # ---- D2's last case: a reviewed behaviour change -------------------------------------------------------------------------
    def test_d2_a_be443_projects_hidden_fm12_now_plays(self):
        """D2's last case (plan 8 W4, Jeff: let it play): a BE4.43 project with a Wavetable OSC1 (with a WAV) and an Analog OSC2
        holding Sub FM 600 (BE4.43's slot 5 there, which played) and FM 1>2 400 (hidden by BE4.43's layout, stored), pushed
        in a project load's order (Types first, then the template's keys). BE4.44 plays FM 1>2 400 from the Wavetable OSC1's B
        (its line ramping in from 0, then held; check_wt_feeds) and OSC2's Sub FM 600 plays nothing (no A filled, no Sub FM
        line): the reverse of BE4.43. The release notes and checklist-v4.44 say so."""
        r = Rig()
        root, v, sub, _ = SubCopy.voice(self, r, 4, 1000, fm=(0, 0), types=(0, 1), glide=0, fine=0)
        r.root_prop(root, K_SHAPE, 48, 1)
        r.tabled(v + S.VOICE_WT1)
        for k, x in ((K_SUBFM, 600), (K_FM12, 400), (K_FMATK, 0), (K_FMDEC, FM_DEC_INF)):
            r.root_prop(root, k, x, 1)
        calls = fm_capture(r)
        r.v = v
        m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
        acc, ad = r.buf(DEV_N * m, 1024); scr, _ = r.buf(DEV_N * m, 1024); out, od = r.buf(DEV_N, 1024)
        slot18 = r.r32(r.r32(root) + 0x48)
        SubCopy.note(self, r, v, sub, 48)
        r.first = r.idx
        for b in range(6):
            r.w32(acc + 4, DEV_N * m); r.w32(scr + 4, DEV_N * m); r.u.mem_write(ad, bytes(4096)); r.w32(out + 4, DEV_N)
            r.call(slot18 & ~1, root, v, 0, scr, sargs=(acc, 0, out))
            r.block()
        o1, o2 = r.osc(v, 0), r.osc(v, 1)
        self.assertEqual(r.settings(v, 1)[5:7], (600, 400), "control: both values stored")
        self.lines("D2", calls, o2, {r.first + b: (0 if b == 0 else 400, 400) for b in range(6)})
        self.assertTrue(all(x["A"] == x["B"] == SA + SA_FMBUF_B for x in calls), "FM 1>2 alone: Sub FM 600 plays nothing")
        self.assertEqual(calls.fills, [], "nothing fills A")
        self.assertEqual(check_wt_feeds(self, "D2", calls, o1, o2), 6)
        self.replay("D2", calls)

    # ---- costs ------------------------------------------------------------------------------------------------------------
    # W4's voices, median / every block (W3's rig: note 60, I 16, a builder saw, a sine sub). Jeff, "Decisions before W4":
    # the unrolled reader (steady) and lever (a) (the note's first block); FM Atk's level-crossing blocks accepted (c)
    W4_GATES = {"WT1 -> A2 FM 1>2": 20500, "WT1 -> WT2 FM 1>2": 20000, "full WT voice": S3_ANALOG_FULL}
    W4_ATK_MAX = 46000

    def test_cost(self):
        """Plan 6's W4 rows, measured (WtFmW3.voice_cost: an_enter and both oscillators' whole renders a voice-block): a Wavetable
        OSC1 feeding an Analog OSC2 and a Wavetable OSC2 (OSC1 at gate 0: stock's kernel plus the body and an_feed), the full
        Wavetable FM voice (WT1 Sub FM 1000 + WT1 -> WT2 FM 1>2 1000), the same with Position every block and with FM Atk 3 on
        both (the accepted level-crossing excess, lever (c)); WT1 Sub FM (W3's row, now on the unrolled loop) for reference.
        Gated: the medians within W4_GATES; every block of the voices without FM Atk within the heaviest existing voice
        (35,309: lever (a) took the note's first block off rd_qm), FM Atk's within W4_ATK_MAX. W4 review: the low-note rows
        (notes 24 / 36, light FM, Position every block: OSC1 on the reader at levels 0 / 1, a frame read every block) every
        block but one within the heaviest voice, that one within W4_ATK_MAX."""
        rows, per_rows = {}, {}
        med = lambda per: sorted(per[4:])[len(per[4:]) // 2]
        lfo = lambda b: (b * 7 % 200) / 255
        vc = lambda **kw: WtFmW3.voice_cost(self, **kw)
        for name, kw in (("stock WT voice", {}), ("WT1 Sub FM", dict(sub=1000)),
                         ("WT1 -> A2 FM 1>2", dict(types=(0, 1), x12=1000)),
                         ("WT1 -> WT2 FM 1>2", dict(x12=1000)),
                         ("full WT voice", dict(sub=1000, x12=1000)),
                         ("full WT voice, Position every block", dict(sub=1000, x12=1000, pos=lfo)),
                         ("full WT voice, FM Atk 3", dict(sub=1000, x12=1000, env={0: (3, FM_DEC_INF), 1: (3, FM_DEC_INF)})),
                         ("full WT voice, note 36", dict(sub=1000, x12=1000, note=36)),
                         ("full WT voice, note 24, light FM, Position every block", dict(sub=300, x12=300, note=24, pos=lfo)),
                         ("full WT voice, note 36, light FM, Position every block", dict(sub=300, x12=300, note=36, pos=lfo)),
                         ("full WT voice, note 24, Sub FM 50, Position every block", dict(sub=50, x12=300, note=24, pos=lfo)),
                         ("Analog full FM", dict(types=(1, 1), sub=1000, x12=1000))):
            per = vc(**kw)
            rows[name] = (med(per), max(per), per[0])
            per_rows[name] = per
        print("\n  WT FM W4 cost (instructions per voice-block, note 60, I 16 unless named; median / max / note's first block): " +
              "; ".join(f"{k} {a} / {b} / {c}" for k, (a, b, c) in rows.items()), file=sys.stderr)
        for k, g in self.W4_GATES.items():
            self.assertLessEqual(rows[k][0], g, f"{k}: the median")
        for k in ("WT1 Sub FM", "WT1 -> A2 FM 1>2", "WT1 -> WT2 FM 1>2", "full WT voice", "full WT voice, Position every block",
                  "full WT voice, note 36"):
            self.assertLessEqual(rows[k][1], HEAVIEST_VOICE, f"{k}: every block within the heaviest existing voice")
        # W4 review: low notes, light FM (OSC1 on the reader at a fine level), Position every block: the frame read on level
        # 4 (wt_frame_dc). Every block but one within the heaviest existing voice; that one (where OSC2's level settles after
        # the entry block, a mip-change block) within W4_ATK_MAX, as FM Atk's crossings
        for k in [k for k in rows if "light FM" in k or "Sub FM 50" in k]:
            per = per_rows[k]
            self.assertLessEqual(sorted(per)[-2], HEAVIEST_VOICE, f"{k}: every block but one within the heaviest existing voice")
            self.assertLessEqual(max(per), self.W4_ATK_MAX, f"{k}: a level change block within W4_ATK_MAX")
        self.assertLessEqual(rows["full WT voice, FM Atk 3"][1], self.W4_ATK_MAX, "FM Atk's level crossings (accepted, c)")


# ---- BE4.45 X2 (docs/plan-fm-targets.md): Sub FM / FM 1>2 as Mod Matrix destinations, the M7 side
SA_X2, SA_X2_N, X2_ENTRY = 0x440, 8, 56      # synth_shared.h SA_X2 / SA_X2_N, dsp/analog.cpp X2Root
RUN_X2N, RUN_XM = 4, 48                      # Run.x2n (the run state's word 1), Run.xm (word 12)
FM_X2_LOST_AT = 24                           # SaFmStats.x2_lost
X2_ROOT_REL = 0x8000
SRC_ENV1, SRC_VEL, SRC_SEQ1 = 0x1, 0xD, 0x9  # source ids: env 1 (voice + 0x1B0), velocity (voice + 0x34), seq1 (root + 0x140)
X2_SLOTS = 4                                 # stock's connect slots 0..3 (3: PARAM slot 4, where WT_ROUTE puts seq1..seq4)
# stock's Wavetable source list (0x081A1304, 16 ids; 0 is None)
WT_SOURCES = (0xD, 0x20, 0x1, 0x2, 0x4, 0x5, 0x8, 0xE, 0x1A, 0x1E, 0x1F, 0x9, 0xA, 0xB, 0xC)
X2_DESTS = ((0, K_SUBFM), (1, K_FM12), (1, K_SUBFM))   # (instance, key): OSC1 Sub FM, OSC2 FM 1>2, OSC2 Sub FM
X2_K = {(0, K_SUBFM): 0, (1, K_FM12): 1, (1, K_SUBFM): 2}


def wt_route(r, root, inst, key, slot, src, amount):
    """A 0x3F routing message into stock's route handler WT_ROUTE (root, msg {+2 instance, +0xC key, +0x10 slot, +0x14 source
    id, +0x16 amount}), as the root's message loop calls it (bl at 0x0807B280): per voice it resolves the source and calls
    slot 10 of oscillator[instance]. (instructions)"""
    m = r.alloc(0x20)
    r.u.mem_write(m, struct.pack("<HHIIHHHHHh", 0x3F, inst, 0, 0, key, 0, slot, 0, src, amount))
    return r.call(S.WT_ROUTE, root, m)[1]


def x2_store(r):
    """The X2 store: [(root, handle, src[12], amt[12])] for the entries in use (Run.x2n): [4 k + slot], k X2_K."""
    n = r.r32(SA + SA_RUN + RUN_X2N)
    out = []
    for i in range(n):
        b = bytes(r.u.mem_read(SA + SA_X2 + X2_ENTRY * i, X2_ENTRY))
        root, handle = struct.unpack_from("<II", b, 0)
        out.append((root, handle, list(struct.unpack_from("<12H", b, 8)), list(struct.unpack_from("<12h", b, 32))))
    return out


def x2_fma32(a, b, c):
    """float32 fused multiply-add, one rounding (to nearest, ties to even), as VFMA.F32."""
    x = Fraction(float(a)) * Fraction(float(b)) + Fraction(float(c))
    y = np.float32(float(x))
    best = None
    for z in (y, np.nextafter(y, np.float32(np.inf)), np.nextafter(y, np.float32(-np.inf))):
        k = (abs(Fraction(float(z)) - x), int(np.frombuffer(np.float32(z).tobytes(), "<u4")[0]) & 1)
        if best is None or k < best[0]:
            best = (k, z)
    return np.float32(best[1])


def x2_law(knob, terms):
    """The plan's law (Q4) in float32, as x2_enter: m = fma(f32(amt) x f32(0.001), src, m) over the slots in order, then
    f = fma(m, 1000, knob), v = 0 unless f > 0, 1000 from f >= 1000, else (u32)(f + 0.5)."""
    f32 = np.float32
    m = f32(0)
    for amt, src in terms:
        m = x2_fma32(f32(amt) * f32(0.001), f32(src), m)
    f = x2_fma32(m, f32(1000), f32(knob))
    return 0 if not f > 0 else 1000 if f >= 1000 else int(f + f32(0.5))


def x2_root(r, handle=0x4646, types=(1, 1), alloc=True):
    """Stock's Wavetable root (ctor, engine init) with its 10 voices, Types through stock's root property."""
    if alloc:
        pool_alloc(r)
    root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
    r.w32(root + S.ROOT_HANDLE, handle)
    vs = [r.r32(root + 0x14 + 4 * i) for i in range(10)]
    for vo in vs:
        for o in (S.VOICE_WT1, S.VOICE_WT2):
            r.w32(vo + o + 0x18, r.nowav)
    for i in (0, 1):
        r.root_prop(root, K_TYPE, types[i], i)
    return root, vs


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class FmTargetsX2(unittest.TestCase):
    """BE4.45 X2 (docs/plan-fm-targets.md): routings on OSC1 Sub FM (0x18B, instance 0), OSC2 FM 1>2 (0x132, instance 1) and
    OSC2 Sub FM (0x18B, instance 1) reach the M7 through stock's route handler (WT_ROUTE) and the oscillator class's slot 10
    (our trampoline first), are kept per root, and play per voice-block as v = clamp(knob + 1000 sum amount_i src_i, 0, 1000)
    into the existing msub / m12 ramps (Q4), on Analog and Wavetable oscillators: shown bit for bit against the same voice
    with the knob turned to that v. Slot 9 (the root's clear) clears them. Nothing routed: today's path (P3Identity, FmEnv,
    WtFmW1..W4, unchanged, cover it)."""

    def test_the_trampolines_are_installed(self):
        w = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        self.assertEqual((S.WT_OSC_CLEAR_SLOT, S.WT_OSC_CONNECT_SLOT), (S.WT_OSC_VT_SLOT + 0x24, S.WT_OSC_VT_SLOT + 0x28))
        self.assertEqual(w(S.WT_OSC_CLEAR_SLOT), DSP["tramp_clear"] | 1)
        self.assertEqual(w(S.WT_OSC_CONNECT_SLOT), DSP["tramp_conn"] | 1)
        self.assertEqual(struct.unpack_from("<I", STOCK_IMG, S.WT_OSC_CONNECT_SLOT - BASE)[0], S.WT_OSC_CONNECT | 1)
        self.assertEqual(struct.unpack_from("<I", STOCK_IMG, S.WT_OSC_CLEAR_SLOT - BASE)[0], S.WT_OSC_CLEAR | 1)

    def test_sources_sit_at_fixed_voice_offsets_and_the_store_finds_them(self):
        """The plan's check (part b, "per-voice sources at fixed voice offsets"): for every source of stock's Wavetable list,
        the pointer stock's own connect gives OSC1 Pitch (routed on matrix slot 2: PARAM slot 3, or slot 4 where WT_ROUTE puts
        the mod seq lanes, ids 9..12, whatever the matrix slot) is the same offset in all 10 voices, or one root address;
        routed to each of our three destinations, the store's entry, in the same slot, decodes to that same pointer for every
        voice (M)."""
        r = Rig()
        root, vs = x2_root(r)
        rows = {}
        for sid in WT_SOURCES:
            wt_route(r, root, 0, 3, 2, sid, 500)                          # stock's OSC1 Pitch (osc + 0x70), matrix slot 2
            at = [(k, [r.r32(vo + S.VOICE_WT1 + 0x70 + 8 * (k + 1)) for vo in vs]) for k in range(4)]
            got = [(k, ps) for k, ps in at if any(ps)]
            self.assertEqual(len(got), 1, f"source {sid:#x}: one PARAM slot")
            slot, ptrs = got[0]
            rel = {p - vo for p, vo in zip(ptrs, vs)}
            if len(rel) == 1 and 0 < next(iter(rel)) < 0xBA0:
                kind, off = "voice", next(iter(rel))
            else:
                self.assertEqual(len(set(ptrs)), 1, f"source {sid:#x}: one pointer for every voice {[hex(p) for p in ptrs]}")
                self.assertTrue(root <= ptrs[0] < root + 0x2F0, f"source {sid:#x}: in the root: {ptrs[0]:#x}")
                kind, off = "root", ptrs[0] - root
            rows[sid] = (kind, off, slot)
            for inst, key in X2_DESTS:
                wt_route(r, root, inst, key, 2, sid, 500)
                j = X2_K[(inst, key)] * X2_SLOTS + slot
                (e,) = x2_store(r)
                self.assertEqual([i for i, c in enumerate(e[2]) if c], [j], f"source {sid:#x}: stock's slot ({slot})")
                c = e[2][j]
                for vo, p in zip(vs, ptrs):
                    self.assertEqual(root + (c & ~X2_ROOT_REL) if c & X2_ROOT_REL else vo + c, p,
                                     f"source {sid:#x} -> {inst} {key:#x}: the voice's pointer")
                self.assertEqual(e[3][j], 500)
                wt_route(r, root, inst, key, 2, 0, 0)                     # source 0 on the matrix slot: removed
                if slot == 3:
                    self.assertEqual(len(x2_store(r)), 1, "(a seq lane sits in slot 3: matrix slot 2's removal leaves it)")
                    wt_route(r, root, inst, key, 3, 0, 0)                 # as stock: the per-type post's slot 3 clears it
                self.assertEqual(x2_store(r), [], "nothing left")
            wt_route(r, root, 0, 3, 2, 0, 0); wt_route(r, root, 0, 3, 3, 0, 0)
        print("\n  X2 sources (id: voice / root offset of the pointer, stock's connect slot): " +
              ", ".join(f"{k:#x}: {kind} +{off:#x} s{sl}" for k, (kind, off, sl) in rows.items()), file=sys.stderr)
        self.assertEqual(rows[SRC_ENV1], ("voice", 0x1B0, 2)); self.assertEqual(rows[SRC_VEL], ("voice", 0x34, 2))
        self.assertEqual(rows[SRC_SEQ1], ("root", 0x140, 3))
        self.assertEqual(r.r32(SA + SA_FMSTATS + FM_X2_LOST_AT), 0, "nothing lost")

    def test_only_the_route_handler_and_only_our_three_destinations_store(self):
        """The store takes slots 0..3 of our three destinations from WT_ROUTE (the trampoline hands its r4, the root, only to a
        call that returns into it); a direct call of slot 10 (no root), OSC1's FM 1>2 (0x08134700 posts every type-6 key to
        both oscillators, X1) and stock's keys store nothing; stock's keys still connect; 0x08134700's per-type post (slot 3,
        source 0) clears slot 3, as it clears stock's PARAM slot 4."""
        r = Rig()
        root, vs = x2_root(r)
        o1 = vs[0] + S.VOICE_WT1
        src = r.alloc(0x10)
        for key in (K_SUBFM, K_FM12):
            r.call(r.r32(r.r32(o1) + 0x28) & ~1, o1, src, key, 0, fargs=(0.5,))
        self.assertEqual(x2_store(r), [], "a direct slot-10 call: no root, nothing kept")
        for slot in range(4):
            wt_route(r, root, 0, K_FM12, slot, SRC_ENV1, 500)
        self.assertEqual(x2_store(r), [], "OSC1's FM 1>2 is not a destination")
        wt_route(r, root, 0, 0xC9, 0, SRC_ENV1, 500)
        self.assertEqual(x2_store(r), [], "Position is stock's")
        self.assertEqual(r.r32(o1 + 0x30 + 8), vs[0] + 0x1B0, "and still connects")
        for slot, (inst, key) in enumerate(X2_DESTS):
            for amt in (-1000, 1000, 7):
                wt_route(r, root, inst, key, slot, SRC_VEL, amt)
                (e,) = x2_store(r)
                j = X2_K[(inst, key)] * X2_SLOTS + slot
                self.assertEqual((e[0], e[1], e[2][j], e[3][j]), (root, 0x4646, 0x34, amt))
        (e,) = x2_store(r)
        self.assertEqual([i for i, c in enumerate(e[2]) if c], [0, 5, 10])
        for slot, (inst, key) in enumerate(X2_DESTS):
            wt_route(r, root, inst, key, slot, 0, 0)
        self.assertEqual(x2_store(r), [])
        wt_route(r, root, 1, K_SUBFM, 0, SRC_SEQ1, 250)                  # seq1 on any matrix slot: stock's slot 3
        (e,) = x2_store(r)
        self.assertEqual((e[2][11], e[3][11]), (X2_ROOT_REL | 0x140, 250), "seq1 on OSC2 Sub FM: slot 3, root + 0x140")
        wt_route(r, root, 1, K_SUBFM, 3, 0, 0)                           # 0x08134700's post: {key, slot 3, source 0}
        self.assertEqual(x2_store(r), [], "the per-type post's slot 3, source 0, clears it")

    # ---- the sound: routed against the knob turned to v, bit for bit
    VALS = (0.0, 0.5, 1.0, -1.0, 2.0, 0.25, -0.25, 0.125, 0.75, 0.375)

    def voice(self, types, sync=0):
        return WtVoice(self, types=types, capture=False, frames=G.wt_saw_frames(200), sync=sync)

    def pair(self, types, inst, key, base=200, amount=600, blocks=10, sync=0, extra=()):
        """The same WtVoice twice: (a) the knob of (inst, key) at clamp(base + amount x VALS[b] / 1000 x 1000) before each
        block b; (b) the knob at base and velocity routed with that amount, velocity VALS[b] before block b. sync: OSC2's
        Sync; extra: (key, value, instance) knobs set on both."""
        want = [max(0, min(1000, round(base + amount * x))) for x in self.VALS]
        runs = []
        for routed in (False, True):
            w = self.voice(types, sync)
            for k, x, i in extra:
                w.prop(k, x, i)
            w.prop(key, base, inst)
            if routed:
                wt_route(w.r, w.root, inst, key, 0, SRC_VEL, amount)
            def each(w, b, routed=routed):
                if routed:
                    w.r.wf(w.v + 0x38, self.VALS[b])
                else:
                    w.prop(key, want[b], inst)
            w.run(blocks, each)
            if routed:
                self.assertEqual(w.r.rf(w.v + 0x38), self.VALS[blocks - 1], "the voice render leaves velocity's value")
            runs.append(w)
        return runs[0].outs, runs[1].outs, runs

    def test_analog_osc1_sub_fm_is_the_knob_travel(self):
        a, b, (wa, wb) = self.pair((1, 1), 0, K_SUBFM)
        self.assertGreater(len(set(a)), 1)
        same(self, b, a, "routed OSC1 Sub FM = the knob at clamp(200 + 600 x velocity)")
        self.assertEqual(wb.fmstats()[0], wa.fmstats()[0], "the same FM renders")

    def test_analog_osc2_fm12_is_the_knob_travel(self):
        a, b, _ = self.pair((1, 1), 1, K_FM12)
        same(self, b, a, "routed OSC2 FM 1>2 = the knob")

    def test_wavetable_osc1_sub_fm_is_the_knob_travel(self):
        a, b, (wa, wb) = self.pair((0, 0), 0, K_SUBFM)
        same(self, b, a, "routed Wavetable OSC1 Sub FM = the knob")
        self.assertGreater(wb.fmstats()[4], 0, "through our reader")

    def test_wavetable_osc2_fm12_is_the_knob_travel(self):
        for types in ((0, 0), (1, 0)):
            with self.subTest(types=types):
                a, b, _ = self.pair(types, 1, K_FM12)
                same(self, b, a, f"routed Wavetable OSC2 FM 1>2 = the knob (OSC1 {'Analog' if types[0] else 'Wavetable'})")

    def test_knob_zero_routed_renders_fm(self):
        """The gate sees the routing: knob 0, a routing, FM plays (an Analog and a Wavetable carrier, each destination)."""
        for types in ((1, 1), (0, 0)):
            for inst, key in X2_DESTS:
                with self.subTest(types=types, inst=inst, key=key):
                    plain = self.voice(types).run(4)
                    w = self.voice(types)
                    wt_route(w.r, w.root, inst, key, 0, SRC_VEL, 1000)
                    w.r.wf(w.v + 0x38, 0.5)
                    w.run(4)
                    self.assertNotEqual(w.outs, plain.outs, "FM plays")
                    msub, m12 = struct.unpack("<HH", w.r.u.mem_read(w.o[inst] + S.OSC_KERNEL + MSUB_AT, 4))
                    self.assertEqual(m12 if key == K_FM12 else msub, 500, "the ramp ends at v = 500")
                    self.assertGreater(w.fmstats()[0], plain.fmstats()[0])

    def test_osc2_sub_fm_plays_only_routed(self):
        """OSC2 Sub FM (no knob since BE4.43; D2: its stored value plays nothing unrouted): routed, its v is the stored value
        plus the routed travel, on A next to FM 1>2's B; the stored value alone still plays nothing."""
        for types in ((1, 1), (0, 0)):
            with self.subTest(types=types):
                plain = self.voice(types).run(4)
                kept = self.voice(types)
                kept.prop(K_SUBFM, 400, 1); kept.run(4)
                same(self, kept.outs, plain.outs, "OSC2's stored Sub FM alone plays nothing (D2, as BE4.44)")
                w = self.voice(types)
                w.prop(K_SUBFM, 100, 1); w.prop(K_FM12, 300, 1)
                wt_route(w.r, w.root, 1, K_SUBFM, 0, SRC_VEL, 500)
                w.r.wf(w.v + 0x38, 0.5)
                w.run(4)
                msub, m12 = struct.unpack("<HH", w.r.u.mem_read(w.o[1] + S.OSC_KERNEL + MSUB_AT, 4))
                # (an Analog OSC2 keeps its stored Sub FM, the base; a Wavetable OSC2's is not kept on the M7, W4 D2: base 0)
                self.assertEqual((msub, m12), (350 if types[1] else 250, 300), "OSC2: Sub FM (base) + 250 routed, FM 1>2 300")
                self.assertEqual(w.fmstats()[3], 0, "no FM missed")

    def test_removal_and_clear_ramp_out_as_the_knob_to_zero(self):
        """Removing the routing (source 0 through WT_ROUTE) or the root's clear (slot 9 of each voice's WT1 / WT2, as
        0x0807B242..5C calls it) at block 4: the same blocks as the knob turned to 0 there (its ramp out included); the store
        is empty after it, and the oscillators' kernel areas end as the knob's run left them."""
        for types in ((1, 1), (0, 0)):
            for inst, key in X2_DESTS[:2]:
                for how in ("remove", "clear"):
                    with self.subTest(types=types, inst=inst, key=key, how=how):
                        runs = []
                        for routed in (False, True):
                            w = self.voice(types)
                            if routed:
                                wt_route(w.r, w.root, inst, key, 0, SRC_VEL, 1000)
                                w.r.wf(w.v + 0x38, 0.6)
                            else:
                                w.prop(key, 600, inst)
                            def each(w, b, routed=routed):
                                if b != 4:
                                    return
                                if not routed:
                                    w.prop(key, 0, inst)
                                elif how == "remove":
                                    wt_route(w.r, w.root, inst, key, 0, 0, 0)
                                else:
                                    for i in range(10):
                                        vo = w.r.r32(w.root + 0x14 + 4 * i)
                                        for o in (vo + S.VOICE_WT1, vo + S.VOICE_WT2):
                                            w.r.call(w.r.r32(w.r.r32(o) + 0x24) & ~1, o)
                            w.run(8, each)
                            runs.append(w)
                        same(self, runs[1].outs, runs[0].outs, "the routed voice = the knob's")
                        self.assertEqual(x2_store(runs[1].r), [], "the store is empty")
                        for i in (0, 1):
                            same(self, runs[1].r.u.mem_read(runs[1].o[i] + S.OSC_KERNEL, 0xC0 + 25),
                                 runs[0].r.u.mem_read(runs[0].o[i] + S.OSC_KERNEL, 0xC0 + 25), f"WT{i + 1}'s kernel area")

    def test_routes_follow_the_root_and_its_handle(self):
        """Per root: two roots keep their own routings; a root stock reuses for another track (a new handle) drops the old
        entry when next seen; the store full (SA_X2_N roots) loses the next and counts it (x2_lost)."""
        r = Rig()
        r.u.mem_map(0xC0000000, 0x5F00000)                               # room for 11 roots  # addr-ok: harness
        pool_alloc(r, at=0xC0000000)                                     # addr-ok: harness
        ra, va = x2_root(r, 0x4701, alloc=False); rb, vb = x2_root(r, 0x4702, alloc=False)
        wt_route(r, ra, 0, K_SUBFM, 0, SRC_VEL, 100); wt_route(r, rb, 1, K_FM12, 1, SRC_ENV1, -200)
        st = {e[0]: e for e in x2_store(r)}
        self.assertEqual((st[ra][2][0], st[ra][3][0], st[rb][2][5], st[rb][3][5]), (0x34, 100, 0x1B0, -200))
        r.w32(ra + S.ROOT_HANDLE, 0x4799)                                 # reused: another track
        r.call(DSP["an_enter"], ra, va[0])
        self.assertEqual([e[0] for e in x2_store(r)], [rb], "the reused root's entry is gone")
        self.assertEqual(r.r32(SA + SA_RUN + RUN_XM), 0, "Run.xm: nothing routed for that voice")
        roots = []
        for i in range(SA_X2_N + 1):                                      # (copies of ra: more than 5 real roots exhaust a
            ro = r.alloc(0x2F0)                                           # pool of stock's the rig does not stub; a copy
            r.u.mem_write(ro, bytes(r.u.mem_read(ra, 0x2F0)))             # routes through ra's voices)
            r.w32(ro + S.ROOT_HANDLE, 0x4800 + i)
            roots.append(ro)
        rc, roots = roots[-1], roots[:-1]
        for ro in roots:
            wt_route(r, ro, 0, K_SUBFM, 0, SRC_VEL, 100)
        self.assertEqual(len(x2_store(r)), SA_X2_N)
        self.assertEqual(r.r32(SA + SA_FMSTATS + FM_X2_LOST_AT), 1, "one root's routing lost, counted")
        r.w32(rb + S.ROOT_HANDLE, 0xFFFFFF00)                             # a track freed (stock's key on a free root)
        wt_route(r, rc, 0, K_SUBFM, 0, SRC_VEL, 100)
        self.assertEqual(r.r32(SA + SA_FMSTATS + FM_X2_LOST_AT), 1, "full: the freed root's entry made room")
        self.assertIn(rc, [e[0] for e in x2_store(r)]); self.assertNotIn(rb, [e[0] for e in x2_store(r)])

    # ---- X2 review: the per-voice, root, slot-3, summed and mixed paths, played (not only decoded)
    def voices(self, types, n=2, vels=None):
        """A WtVoice (voice 0) and voices 1..n-1 of the same root sounding with it (their oscillators on the same tables and
        sub settings, note 60), velocity vels[i] each; render(w) plays one voice-block of each, then the audio block."""
        w = self.voice(types)
        r, vs = w.r, [w.v]
        for i in range(1, n):
            vo = r.r32(w.root + 0x14 + 4 * i)
            for j in (0, 1):
                if not types[j]:
                    r.tabled(vo + (S.VOICE_WT1, S.VOICE_WT2)[j])
            for k, x in ((SUB_WAVE, 3), (SUB_LEVEL, 1000), (SUB_PW, 300), (SUB_RETRIG, 1), (SUB_FINE, 0)):
                r.prop(vo + S.VOICE_SUB, k, x)
            SubCopy.note(self, r, vo, vo + S.VOICE_SUB, 60)
            vs.append(vo)
        if getattr(r, "tab", None):
            r.u.mem_write(r.tab, G.wt_build_levels(G.wt_saw_frames(200)).tobytes())
        for vo, x in zip(vs, vels or ()):
            r.wf(vo + 0x38, x)
        w.vs, w.outs_v = vs, [[] for _ in vs]
        return w

    @staticmethod
    def render(w):
        r, (acc, ad), (scr, _), (o, od) = w.r, w.acc, w.scr, w.out
        for i, vo in enumerate(w.vs):
            r.w32(acc + 4, DEV_N * w.m); r.w32(scr + 4, DEV_N * w.m); r.u.mem_write(ad, bytes(4096)); r.w32(o + 4, DEV_N)
            r.call(w.slot18 & ~1, w.root, vo, 0, scr, sargs=(acc, 0, o))
            w.outs_v[i].append(bytes(r.u.mem_read(od, 4 * DEV_N)))
        r.block()

    @staticmethod
    def ramps(w, vo, i):
        return struct.unpack("<HH", w.r.u.mem_read(vo + (S.VOICE_WT1, S.VOICE_WT2)[i] + S.OSC_KERNEL + MSUB_AT, 4))

    def test_each_voice_reads_its_own_sources(self):
        """Review (R1): two voices of one root, velocity 0.2 and 0.7, routed at 1000 to OSC1 Sub FM and at 500 to OSC2 FM 1>2
        (knobs 0): each voice's msub / m12 end at its own v (200 / 700, 100 / 350), not voice 0's, and the voices differ."""
        for types in ((1, 1), (0, 0)):
            with self.subTest(types=types):
                w = self.voices(types, vels=(0.2, 0.7))
                wt_route(w.r, w.root, 0, K_SUBFM, 0, SRC_VEL, 1000); wt_route(w.r, w.root, 1, K_FM12, 0, SRC_VEL, 500)
                for _ in range(4):
                    self.render(w)
                self.assertEqual([self.ramps(w, vo, 0)[0] for vo in w.vs], [200, 700], "OSC1 Sub FM: each voice its own")
                self.assertEqual([self.ramps(w, vo, 1)[1] for vo in w.vs], [100, 350], "OSC2 FM 1>2: each voice its own")
                self.assertNotEqual(w.outs_v[0][-1], w.outs_v[1][-1])
                self.assertEqual(w.fmstats()[3], 0, "no FM missed")

    # (matrix slot, source id, amount, source value) against the law in float32 (x2_law): a root source (modwheel, root +
    # 0x28C), velocity whose 0.7 of a unit pins the rounding to nearest, seq1 (root + 0x140: stock's slot 3, whatever the
    # matrix slot); and one routing whose final fma(m, 1000, knob) rounds to 764 where m x 1000 + knob gives 765 (M: searched)
    SUM_CASES = (("modwheel + velocity + seq1 (slot 3), summed", 100, ((0, 0x1A, 800, 0.5), (1, SRC_VEL, 7, 0.1),
                                                                       (2, SRC_SEQ1, 600, 0.5)), 801),
                 ("the final FMA's rounding", 630, ((0, SRC_VEL, 406, 0.3312807083129883),), 764))
    WRITE_AT = {0x1A: ("root", 0x290), SRC_SEQ1: ("root", 0x144), SRC_VEL: ("voice", 0x38)}

    def test_root_sources_seq_slot_and_the_sum_play_as_the_law(self):
        """Review (R1, R2): root-relative sources and stock's slot 3 played, several slots summed in stock's order, and the
        rounding: v = x2_law(knob, terms) (the plan's float32 law), the voice bit for bit the same voice with the knob at v.
        The other key's knob is 300 on both (a routing on one key leaves the other key's knob alone)."""
        for types in ((1, 1), (0, 0)):
            for inst, key in X2_DESTS[:2]:
                other = (1, K_FM12) if key == K_SUBFM else (0, K_SUBFM)
                for name, knob, terms, want in self.SUM_CASES:
                    with self.subTest(types=types, inst=inst, key=key, case=name):
                        self.assertEqual(x2_law(knob, [(a, x) for _, _, a, x in terms]), want, "(the law in Python)")
                        runs = []
                        for routed in (False, True):
                            w = self.voice(types)
                            w.prop(other[1], 300, other[0])
                            w.prop(key, knob if routed else want, inst)
                            if routed:
                                for slot, sid, amt, _ in terms:
                                    wt_route(w.r, w.root, inst, key, slot, sid, amt)
                            def each(w, b, routed=routed):
                                if routed:
                                    for _, sid, _, x in terms:
                                        kind, off = self.WRITE_AT[sid]
                                        w.r.wf((w.root if kind == "root" else w.v) + off, x)
                            w.run(6, each)
                            runs.append(w)
                        same(self, runs[1].outs, runs[0].outs, f"{name}: routed = the knob at {want}")
                        got = self.ramps(runs[1], runs[1].v, inst)
                        self.assertEqual(got[1] if key == K_FM12 else got[0], want, "the ramp ends at v")
                        if any(sid == SRC_SEQ1 for _, sid, _, _ in terms):
                            (e,) = x2_store(runs[1].r)
                            self.assertEqual(e[2][X2_K[(inst, key)] * X2_SLOTS + 3], X2_ROOT_REL | 0x140, "seq1 in slot 3")

    def test_a_key_routed_only_in_its_upper_slots_plays(self):
        """Review (mutation sweep): a key whose only routing sits in matrix slot 2, or is a seq lane (stock's slot 3), is
        routed (knob 0): its ramp ends at v, on each destination and type."""
        for types in ((1, 1), (0, 0)):
            for inst, key in X2_DESTS:
                for slot, sid, amt in ((2, SRC_VEL, 1000), (0, SRC_SEQ1, 800)):
                    with self.subTest(types=types, inst=inst, key=key, source=sid):
                        w = self.voice(types)
                        wt_route(w.r, w.root, inst, key, slot, sid, amt)
                        def each(w, b):
                            w.r.wf(w.v + 0x38, 0.5); w.r.wf(w.root + 0x144, 0.5)
                        w.run(4, each)
                        got = self.ramps(w, w.v, inst)
                        self.assertEqual(got[1] if key == K_FM12 else got[0], amt // 2, "the ramp ends at v")

    def test_routed_osc1_sub_fm_follows_mixed_sync_as_the_knob(self):
        """Review (D3): a Wavetable OSC1 feeding B to an Analog OSC2 synced to it (OSC2 FM 1>2 300, Sync on): OSC1's Sub FM
        plays nothing under D3, routed as on the knob."""
        a, b, _ = self.pair((0, 1), 0, K_SUBFM, sync=1, extra=((K_FM12, 300, 1),))
        same(self, b, a, "routed OSC1 Sub FM under mixed sync = the knob's")

    def test_amounts_keep_their_value_through_the_route_handler(self):
        """Review (R2): every amount -1000..1000 through WT_ROUTE's f32(amount) x f32(0.001) is stored back as itself; past
        the matrix's range it is held at +-1000 (the law's v is clamped anyway)."""
        r = Rig()
        root, vs = x2_root(r)
        for amt in list(range(-1000, 1001)) + [1200, -1500, 32767, -32768]:
            wt_route(r, root, 1, K_SUBFM, 1, SRC_VEL, amt)
            (e,) = x2_store(r)
            self.assertEqual(e[3][X2_K[(1, K_SUBFM)] * X2_SLOTS + 1], max(-1000, min(1000, amt)), f"amount {amt}")

    def test_osc2_sub_fm_removal_ramps_out(self):
        """Review (R2): OSC2 Sub FM (no knob twin) routed, then removed at block 4: its msub ramps to 0, the store empties,
        nothing missed; FM 1>2 300 next to it plays on."""
        for types in ((1, 1), (0, 0)):
            with self.subTest(types=types):
                w = self.voice(types)
                w.prop(K_FM12, 300, 1)
                wt_route(w.r, w.root, 1, K_SUBFM, 0, SRC_VEL, 1000)
                w.r.wf(w.v + 0x38, 0.6)
                def each(w, b):
                    if b == 4:
                        self.assertEqual(self.ramps(w, w.v, 1), (600, 300))
                        wt_route(w.r, w.root, 1, K_SUBFM, 0, 0, 0)
                w.run(10, each)
                self.assertEqual(self.ramps(w, w.v, 1), (0, 300), "OSC2 Sub FM ramped out, FM 1>2 kept")
                self.assertEqual(x2_store(w.r), [])
                self.assertEqual(w.fmstats()[3], 0, "no FM missed")

    def test_removal_tidies_every_voice_of_the_root(self):
        """Review (R1): two voices of one root, OSC2 FM 1>2 routed (velocity 0.6 at 1000) and removed (or cleared) at block 4,
        against the same two voices with the knob at 600 turned to 0 there: both voices' blocks, kernel areas and fm_missed
        the same (voice 1's Wavetable OSC1 keeps feeding B through the ramp out)."""
        for types in ((0, 0), (1, 0), (0, 1)):
            for how in ("remove", "clear"):
                with self.subTest(types=types, how=how):
                    runs = []
                    for routed in (False, True):
                        w = self.voices(types, vels=(0.6, 0.6))
                        if routed:
                            wt_route(w.r, w.root, 1, K_FM12, 0, SRC_VEL, 1000)
                        else:
                            w.prop(K_FM12, 600, 1)
                        for b in range(9):
                            if b == 4:
                                if not routed:
                                    w.prop(K_FM12, 0, 1)
                                elif how == "remove":
                                    wt_route(w.r, w.root, 1, K_FM12, 0, 0, 0)
                                else:
                                    for vo in w.vs:
                                        for o in (vo + S.VOICE_WT1, vo + S.VOICE_WT2):
                                            w.r.call(w.r.r32(w.r.r32(o) + 0x24) & ~1, o)
                            self.render(w)
                        runs.append(w)
                    a, b = runs
                    for i in range(2):
                        same(self, b.outs_v[i], a.outs_v[i], f"voice {i}: routed = the knob's")
                        for j in (0, 1):
                            o = (S.VOICE_WT1, S.VOICE_WT2)[j] + S.OSC_KERNEL
                            same(self, b.r.u.mem_read(b.vs[i] + o, 0xC0 + 25), a.r.u.mem_read(a.vs[i] + o, 0xC0 + 25),
                                 f"voice {i} WT{j + 1}'s kernel area")
                    self.assertEqual(b.fmstats()[3], a.fmstats()[3], "fm_missed")
                    self.assertEqual(x2_store(b.r), [])

    def test_connect_guards(self):
        """Review (R2): the connect trampoline keeps s0 / s1 for stock's slot 10 (vpush / vpop present) and passes a root only
        to calls returning into WT_ROUTE (a direct call with the real root in r4 keeps nothing); x2_connect takes a root
        only for its own voices; stock's Position keeps its amount next to our keys' connects."""
        t = bytes(IMG[DSP["tramp_conn"] - BASE:DSP["tramp_conn"] - BASE + 0x40])
        self.assertIn(bytes.fromhex("2ded020a"), t, "vpush {s0, s1}")
        self.assertIn(bytes.fromhex("bdec020a"), t, "vpop {s0, s1}")
        r = Rig()
        root, vs = x2_root(r)
        o1 = vs[0] + S.VOICE_WT1
        for key in (K_SUBFM, K_FM12):
            r.u.reg_write(UC_ARM_REG_R4, root)                            # the real root in r4, lr outside WT_ROUTE
            r.call(r.r32(r.r32(o1) + 0x28) & ~1, o1, vs[0] + 0x34, key, 0, fargs=(0.5,))
        self.assertEqual(x2_store(r), [], "a direct slot-10 call with r4 = the root: nothing kept (the lr check)")
        r2 = Rig()
        other, _ = x2_root(r2, handle=0x4747)
        root2, vs2 = x2_root(r2, alloc=False)
        r2.call(DSP["x2_connect"], vs2[0] + S.VOICE_WT1, vs2[0] + 0x34, K_SUBFM, other, fargs=(0.5,))
        self.assertEqual(x2_store(r2), [], "a root that does not hold the voice: nothing kept")
        r2.call(DSP["x2_connect"], vs2[0] + S.VOICE_WT1, vs2[0] + 0x34, K_SUBFM, root2, fargs=(0.5,))
        self.assertEqual(len(x2_store(r2)), 1, "(its own root: kept)")
        pos = float(np.float32(500) * np.float32(0.001))
        wt_route(r, root, 0, K_SUBFM, 0, SRC_ENV1, 300)
        wt_route(r, root, 0, 0xC9, 0, SRC_ENV1, 500)
        self.assertEqual(r.rf(o1 + 0x30 + 8 + 4), pos, "Position's amount (s0 kept)")
        wt_route(r, root, 0, K_SUBFM, 0, SRC_ENV1, 700)
        self.assertEqual(r.rf(o1 + 0x30 + 8 + 4), pos, "and after our key again")

    ROUTED_MAX = 600            # the routing's own instructions per voice-block (two destinations; M below)

    def test_cost(self):
        """Instructions per voice-block (an_enter and both oscillators' renders, Span), median of the steady blocks / max:
        the three destinations routed against the same voice on the knobs (today's path, x2n 0)."""
        def per(types, mode):
            w = self.voice(types)
            w.prop(K_FM12, 300, 1)
            if mode:                                                  # the same sound routed: OSC1 Sub FM 600 x 0.5, FM 1>2 300 + 0
                wt_route(w.r, w.root, 0, K_SUBFM, 0, SRC_VEL, 600); wt_route(w.r, w.root, 1, K_FM12, 0, SRC_VEL, 0)
                if mode == 2:                                         # and OSC2 Sub FM 400 x 0.5 (an FM term today has not)
                    wt_route(w.r, w.root, 1, K_SUBFM, 0, SRC_VEL, 400)
                w.r.wf(w.v + 0x38, 0.5)
            else:
                w.prop(K_SUBFM, 300, 0)
            sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
            xs = []
            for b in range(12):
                t0 = sum(s.total for s in sp); w.block(); xs.append(sum(s.total for s in sp) - t0)
            return sorted(xs[4:])[len(xs[4:]) // 2], max(xs), w.outs
        rows = {}
        for name, types in (("Analog", (1, 1)), ("Wavetable", (0, 0))):
            rows[name] = [per(types, m) for m in (0, 1, 2)]
            same(self, rows[name][1][2], rows[name][0][2], f"{name}: the same sound routed")
        print("\n  X2 cost (instructions per voice-block, median / max: knobs; the same routed; + OSC2 Sub FM routed): " +
              "; ".join(f"{k} {a[0]} / {a[1]}, {b[0]} / {b[1]}, {c[0]} / {c[1]}" for k, (a, b, c) in rows.items()),
              file=sys.stderr)
        for k, (a, b, c) in rows.items():
            self.assertLessEqual(b[0] - a[0], self.ROUTED_MAX, f"{k}: the routing's own cost per voice-block")
            self.assertLessEqual(max(b[1], c[1]), HEAVIEST_VOICE, f"{k}: routed, every block within the heaviest existing voice")

    SUB2_HEAVY_MAX = 37700      # X2 review: the heaviest voice with OSC2 Sub FM routed too (a second FM term on OSC2 that
                                # BE4.44 never plays; M 37.1k..37.5k): over HEAVIEST_VOICE, reported to Jeff, gated here

    def test_cost_heaviest_voice_routed(self):
        """X2 review (R2): the heaviest existing voice (Wavetable both, Sub FM and FM 1>2 at 1000, Position moving every
        block, notes 24 / 36) with the same sound routed: two keys from velocity; the same two keys with all four slots
        each (velocity, env 1, env 2, seq1 in slot 3); and that with 7 other routed roots ahead in the store. Every block
        within HEAVIEST_VOICE. An unrouted voice with 8 routed roots in the store: within it too. OSC2 Sub FM routed as
        well (a whole FM term today has not): within SUB2_HEAVY_MAX."""
        lfo = lambda b: (b * 7 % 200) / 255
        SUB1, FM12, SUB2 = (0, K_SUBFM), (1, K_FM12), (1, K_SUBFM)
        two = [(*SUB1, 0, SRC_VEL, 1000), (*FM12, 0, SRC_VEL, 1000)]
        full = [(*d, s, src, 1000) for d in (SUB1, FM12) for s, src in ((0, SRC_VEL), (1, 0x1), (2, 0x2))] + \
               [(*d, 0, SRC_SEQ1, 300) for d in (SUB1, FM12)]
        def cost(note, knobs, routes=(), fill=0):
            w = WtVoice(self, types=(0, 0), frames=G.wt_saw_frames(200), capture=False, note=note)
            if knobs:
                w.prop(K_SUBFM, 1000, 0); w.prop(K_FM12, 1000, 1)
            for rt in routes:
                wt_route(w.r, w.root, *rt)
            if fill:                                                  # routed roots ahead of ours (the scan passes them)
                n = w.r.r32(SA + SA_RUN + RUN_X2N)
                ents = [bytes(w.r.u.mem_read(SA + SA_X2 + X2_ENTRY * i, X2_ENTRY)) for i in range(n)]
                fake = []
                for i in range(fill):
                    ro = w.r.alloc(0x2F0); w.r.w32(ro + S.ROOT_HANDLE, 0x5000 + i)
                    fake.append(struct.pack("<II12H12h", ro, 0x5000 + i, 0x34, *[0] * 11, 100, *[0] * 11))
                for i, e in enumerate((fake + ents)[:SA_X2_N]):
                    w.r.u.mem_write(SA + SA_X2 + X2_ENTRY * i, e)
                w.r.w32(SA + SA_RUN + RUN_X2N, min(SA_X2_N, fill + n))
            w.r.wf(w.v + 0x38, 1.0)
            sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
            xs = []
            for b in range(16):
                for o in w.o:
                    w.r.wf(o + OSC_POS_SRC, lfo(b))
                t0 = sum(s.total for s in sp); w.block(); xs.append(sum(s.total for s in sp) - t0)
            return sorted(xs[4:])[len(xs[4:]) // 2], max(xs), w.outs
        rows = {}
        for note in (24, 36):
            k = cost(note, True)
            rows[f"note {note}: knobs"] = k
            rows[f"note {note}: 2 routes"] = r2 = cost(note, False, two)
            same(self, r2[2], k[2], f"note {note}: the same sound routed")
            rows[f"note {note}: 8 slots"] = cost(note, False, full)
            rows[f"note {note}: 8 slots, 7 roots ahead"] = cost(note, False, full, fill=7)
            rows[f"note {note}: unrouted, 8 roots in the store"] = cost(note, True, (), fill=8)
            rows[f"note {note}: + OSC2 Sub FM routed"] = cost(note, False, two + [(*SUB2, 0, SRC_VEL, 1000)])
        print("\n  X2 heaviest voice (instructions per voice-block, Position every block; steady median / max): " +
              "; ".join(f"{k} {a} / {b}" for k, (a, b, _) in rows.items()), file=sys.stderr)
        for k, (a, b, _) in rows.items():
            self.assertLessEqual(b, self.SUB2_HEAVY_MAX if "OSC2 Sub FM" in k else HEAVIEST_VOICE, k)


# ---- BE4.45 X3 (docs/plan-fm-targets.md "X3"): piano-roll lanes on the three FM depths, the M7 side
SRC_SEQ = (0x9, 0xA, 0xB, 0xC)               # the piano roll's lanes 0..3 are the sources seq1..seq4 (lane + 9)
ROOT_LANE_SRC, ROOT_LANE_ENGINE, ROOT_LANE_TRACK = 0x140, 0x13C, 0x284   # root + 0x140 + 8 lane: seq1..seq4 (value at + 4)


class Lanes:
    """The engine's side of a track's four lanes, as stock's root reads them: WT_ROOT_BLOCK (the root's slot 2) runs up to
    WT_ROOT_LANES_END, where it calls the root's block. Its lane read asks the object at root + 0x13C (the engine, stored by
    stock's engine init) vt[0](object, [root + 0x284], lane) for each lane and keeps s0 at root + 0x144 + 8 lane. That object
    is ours here: it answers vals[lane] (the device's comes from the sequencer's 0xA8 lane events: not run here)."""
    def __init__(self, r, root):
        self.r, self.root, self.vals, self.asked = r, root, [0.0] * 4, []
        obj = r.alloc(0x40); stub = obj + 0x20
        r.u.mem_write(stub, b"\x70\x47")                                          # bx lr; the hook sets s0
        r.u.mem_write(obj, struct.pack("<II", obj + 4, stub | 1))                 # object -> its vtable at +4: vt[0]
        def answer(uc, a, sz, _):
            lane = uc.reg_read(UC_ARM_REG_R2)
            self.asked.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), lane))
            uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", self.vals[lane]))[0])
        r.u.hook_add(UC_HOOK_CODE, answer, begin=stub, end=stub)
        r.w32(root + ROOT_LANE_ENGINE, obj)
        self.obj = obj

    def read(self, vals):
        """Stock's lane read with these four values."""
        self.vals = [f32(v) for v in vals]
        u = self.r.u
        u.reg_write(UC_ARM_REG_R0, self.root); u.reg_write(UC_ARM_REG_R1, 0)
        u.reg_write(UC_ARM_REG_SP, STACK - 0x40); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(S.WT_ROOT_BLOCK | 1, S.WT_ROOT_LANES_END, count=100_000)

    def value(self, lane): return self.r.rf(self.root + ROOT_LANE_SRC + 8 * lane + 4)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class FmLanesX3(unittest.TestCase):
    """BE4.45 X3: a piano-roll lane on OSC1 Sub FM, OSC2 FM 1>2 or OSC2 Sub FM is a routing from seq1..seq4 (the lane + 9) in
    matrix slot 0 (test_native_props FmTargets: the toggle, its 0xD1 and the model's 0x3F); the M7 takes it through stock's
    WT_ROUTE into connect slot 3, X2's store and sum. Played here per block through stock's own lane read (Lanes): the FM
    depth follows the lane, bit for bit as the same routing from velocity (X2) and as the knob turned to v; the other
    lanes, a stock destination's lane (Position) and the other FM knob are untouched. No M7 code of X3's: the DSP is X2's,
    but for the review's x2_clear keeping slot 3 (R1-1: test_the_roots_clear_keeps_a_lane_as_it_keeps_positions)."""
    VALS = FmTargetsX2.VALS

    def test_stock_reads_the_four_lanes_into_seq1_to_seq4(self):
        """The trace (S) measured: WT_ROOT_BLOCK asks root + 0x13C's vt[0] for lanes 0..3 with [root + 0x284] and keeps each
        at root + 0x144 + 8 lane, where WT_ROUTE points seq1..seq4 (X2: root + 0x140 + 8 lane, value at + 4); with no engine
        at root + 0x13C it reads nothing."""
        r = Rig(); root, vs = x2_root(r)
        self.assertEqual(r.r32(r.r32(root) + 8), S.WT_ROOT_BLOCK | 1, "the root's slot 2")
        for i in range(4):
            r.wf(root + ROOT_LANE_SRC + 8 * i + 4, 9.0)
        u = r.u
        u.reg_write(UC_ARM_REG_R0, root); u.reg_write(UC_ARM_REG_SP, STACK - 0x40)
        u.emu_start(S.WT_ROOT_BLOCK | 1, S.WT_ROOT_LANES_END, count=100_000)
        self.assertEqual([r.rf(root + ROOT_LANE_SRC + 8 * i + 4) for i in range(4)], [9.0] * 4, "no engine: nothing read")
        ln = Lanes(r, root)
        ln.read([0.25, -0.5, 1.0, 0.125])
        self.assertEqual([ln.value(i) for i in range(4)], [0.25, -0.5, 1.0, 0.125])
        self.assertEqual(ln.asked, [(ln.obj, r.r32(root + ROOT_LANE_TRACK), i) for i in range(4)])
        for lane, sid in enumerate(SRC_SEQ):                              # WT_ROUTE's pointer for seq1..seq4
            wt_route(r, root, 0, K_SUBFM, 0, sid, 500)
            (e,) = x2_store(r)
            self.assertEqual(e[2][3], X2_ROOT_REL | (ROOT_LANE_SRC + 8 * lane), f"seq{lane + 1}: slot 3, root + 0x140 + 8 lane")
            wt_route(r, root, 0, K_SUBFM, 3, 0, 0)
        self.assertEqual(x2_store(r), [])

    def lane_run(self, types, inst, key, lane, how, base=200, amount=600, blocks=10):
        """One WtVoice with the lane engine, other lanes busy (0.9, 0.3, 0.6), a Position lane on OSC1 (the lane after ours,
        amount 400: stock's destination), the other FM knob at 300; how: 'lane' (our routing from the lane, slot 0 as the
        toggle puts it), 'vel' (the same routing from velocity, X2), 'knob' (the knob turned to v, nothing routed).
        Returns (outputs, msub / m12 of (inst, key) after each block, the store)."""
        w = WtVoice(self, types=types, capture=False, frames=G.wt_saw_frames(200))
        other = (1, K_FM12) if (inst, key) == (0, K_SUBFM) else (0, K_SUBFM)
        w.prop(other[1], 300, other[0])
        w.prop(key, base, inst)
        pos_lane = (lane + 1) % 4
        wt_route(w.r, w.root, 0, 0xC9, 1, SRC_SEQ[pos_lane], 400)
        if how == "lane":
            wt_route(w.r, w.root, inst, key, 0, SRC_SEQ[lane], amount)
        elif how == "vel":
            wt_route(w.r, w.root, inst, key, 0, SRC_VEL, amount)
        ln = Lanes(w.r, w.root)
        depth = []
        for b in range(blocks):
            vals = [0.9, 0.3, 0.6, 0.15]
            vals[lane] = self.VALS[b]
            vals[pos_lane] = (b % 4) / 4
            ln.read(vals)
            if how == "vel":
                w.r.wf(w.v + 0x38, self.VALS[b])
            elif how == "knob":
                w.prop(key, x2_law(base, [(amount, self.VALS[b])]), inst)
            w.block()
            msub, m12 = struct.unpack("<HH", w.r.u.mem_read(w.o[inst] + S.OSC_KERNEL + MSUB_AT, 4))
            depth.append(m12 if key == K_FM12 else msub)
        return w.outs, depth, x2_store(w.r)

    def test_a_lane_plays_its_destination_per_block(self):
        """Each destination, Analog and Wavetable oscillators: per block the lane's value moves the depth to
        v = clamp(knob + amount x lane, 0, 1000) (msub / m12 after the block, x2_law); the voice is bit for bit the same
        routing from velocity (X2's path), and for OSC1 Sub FM / OSC2 FM 1>2 the knob turned to v. The store holds the lane
        in connect slot 3 at root + 0x140 + 8 lane; the Position lane and the other knob play alike in every run."""
        for types in ((1, 1), (0, 0)):
            for lane, inst, key in ((0, 0, K_SUBFM), (1, 1, K_FM12), (3, 1, K_SUBFM)):
                with self.subTest(types=types, lane=lane, inst=inst, key=hex(key)):
                    a, da, st = self.lane_run(types, inst, key, lane, "lane")
                    b, db, _ = self.lane_run(types, inst, key, lane, "vel")
                    same(self, a, b, "the lane = the same routing from velocity")
                    self.assertEqual(da, db)
                    (e,) = st
                    j = X2_K[(inst, key)] * X2_SLOTS + 3
                    self.assertEqual((e[2][j], e[3][j]), (X2_ROOT_REL | (ROOT_LANE_SRC + 8 * lane), 600), "slot 3, the lane")
                    self.assertEqual([c for i, c in enumerate(e[2]) if c and i != j], [], "nothing else of ours stored")
                    base = 200 if (inst, key) != (1, K_SUBFM) or types[1] else 0     # a Wavetable OSC2's Sub FM base: 0 (X2)
                    self.assertEqual(da, [x2_law(base, [(600, x)]) for x in self.VALS], "the depth per block")
                    self.assertGreater(len(set(da)), 5, "it moves")
                    if (inst, key) != (1, K_SUBFM):
                        c, dc, stc = self.lane_run(types, inst, key, lane, "knob")
                        same(self, a, c, "the lane = the knob turned to v")
                        self.assertEqual(da, dc)
                        self.assertEqual(stc, [], "the Position lane alone stores nothing of ours")

    def test_two_lanes_on_one_destination_keep_the_last(self):
        """Stock's rule for every destination (X2 open 2): seq lanes share connect slot 3, so a second lane on the same FM
        depth replaces the first, and its removal (the toggle's slot 3, source 0) leaves none."""
        r = Rig(); root, vs = x2_root(r)
        wt_route(r, root, 0, K_SUBFM, 0, SRC_SEQ[0], 500)
        wt_route(r, root, 0, K_SUBFM, 1, SRC_SEQ[2], 300)
        (e,) = x2_store(r)
        self.assertEqual((e[2][3], e[3][3]), (X2_ROOT_REL | (ROOT_LANE_SRC + 16), 300))
        wt_route(r, root, 0, K_SUBFM, 3, 0, 0)
        self.assertEqual(x2_store(r), [])

    @staticmethod
    def clear_all(r, root):
        """Stock's message 0x48 (the root's clear-all) through the root's message loop: its case (WT_ROOT_CLEAR_ALL, jump
        table entry 0x48) up to the loop's next message (WT_ROOT_MSG_NEXT), with the loop's frame ([sp + 0x20] = root +
        0x10, the voice list before its first) and r4 = the root: per voice, slot 9 of oscillators 0..2 (WT_OSC_CLEAR, X2's
        x2_clear first) among stock's other resets."""
        u, sp = r.u, STACK - 0x200
        r.w32(sp + 0x20, root + 0x10); r.w32(sp + 0x50, 0x48)
        u.reg_write(UC_ARM_REG_R4, root); u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(S.WT_ROOT_CLEAR_ALL | 1, S.WT_ROOT_MSG_NEXT, count=2_000_000)

    def test_the_roots_clear_keeps_a_lane_as_it_keeps_positions(self):
        """X3 review (R1-1): stock's clear-all (0x48) zeroes the matrix slots 0..2 of Pitch, Level and Position and keeps
        connect slot 3, the seq lanes' (PARAM slot 4: 0x080A0880 keeps + 0x20). X2's clear now does the same for ours: a lane
        on each FM depth, a Position lane on OSC1 and a velocity routing in matrix slot 1 of the same depth; after the clear
        at block 4 the velocity term is gone, the lane still drives the depth per block (x2_law with the lane alone) and
        Position's lane is still connected, on Analog and Wavetable oscillators."""
        VALS = FmTargetsX2.VALS
        for types in ((1, 1), (0, 0)):
            for lane, inst, key in ((0, 0, K_SUBFM), (1, 1, K_FM12), (3, 1, K_SUBFM)):
                with self.subTest(types=types, lane=lane, inst=inst, key=hex(key)):
                    w = WtVoice(self, types=types, capture=False, frames=G.wt_saw_frames(200))
                    base = 200 if (inst, key) != (1, K_SUBFM) or types[1] else 0     # a Wavetable OSC2's Sub FM base: 0 (X2)
                    w.prop(key, base, inst)
                    pos_lane = (lane + 1) % 4
                    wt_route(w.r, w.root, 0, 0xC9, 1, SRC_SEQ[pos_lane], 400)
                    wt_route(w.r, w.root, inst, key, 0, SRC_SEQ[lane], 600)
                    wt_route(w.r, w.root, inst, key, 1, SRC_VEL, 300)
                    pos_at = w.root + ROOT_LANE_SRC + 8 * pos_lane
                    params = lambda: [w.r.r32(w.o[0] + p + 0x20) for p in (0x30, 0x70, 0xB0)]
                    self.assertIn(pos_at, params(), "Position's lane in its PARAM slot 4 (control)")
                    j = X2_K[(inst, key)] * X2_SLOTS
                    ln = Lanes(w.r, w.root)
                    depth, want = [], []
                    for b in range(10):
                        if b == 4:
                            self.clear_all(w.r, w.root)
                            (e,) = x2_store(w.r)
                            self.assertEqual(e[2][j:j + 4], [0, 0, 0, X2_ROOT_REL | (ROOT_LANE_SRC + 8 * lane)],
                                             "slots 0..2 cleared, the lane's slot 3 kept")
                            self.assertEqual([c for i, c in enumerate(e[2]) if c and i != j + 3], [], "nothing else")
                            self.assertIn(pos_at, params(), "Position's lane kept (stock's)")
                        vals = [0.9, 0.3, 0.6, 0.15]
                        vals[lane] = VALS[b]; vals[pos_lane] = (b % 4) / 4
                        ln.read(vals)
                        w.r.wf(w.v + 0x38, 0.5)
                        w.block()
                        msub, m12 = struct.unpack("<HH", w.r.u.mem_read(w.o[inst] + S.OSC_KERNEL + MSUB_AT, 4))
                        depth.append(m12 if key == K_FM12 else msub)
                        want.append(x2_law(base, ([(300, 0.5)] if b < 4 else []) + [(600, VALS[b])]))
                    self.assertEqual(depth, want, "velocity's term until the clear, the lane before and after it")
                    self.assertGreater(len(set(depth[5:])), 3, "the lane still moves the depth")

    def test_a_lane_beside_a_matrix_routing(self):
        """X3 review (R1-2): velocity in matrix slot 0 and a lane the toggle put in matrix slot 1 (test_native_props
        FmTargets.test_a_lane_beside_a_matrix_routing_takes_the_next_slot): stock's WT_ROUTE stores the lane in connect slot
        3; the toggle's removal (slot 3, source 0) clears it and leaves velocity's slot 0."""
        r = Rig(); root, vs = x2_root(r)
        for inst, key in X2_DESTS:
            wt_route(r, root, inst, key, 0, SRC_VEL, 500)
            wt_route(r, root, inst, key, 1, SRC_SEQ[0], 1000)
            (e,) = x2_store(r)
            j = X2_K[(inst, key)] * X2_SLOTS
            self.assertEqual((e[2][j:j + 4], e[3][j:j + 4]), ([0x34, 0, 0, X2_ROOT_REL | ROOT_LANE_SRC], [500, 0, 0, 1000]))
            wt_route(r, root, inst, key, 3, 0, 0)
            (e,) = x2_store(r)
            self.assertEqual((e[2][j:j + 4], e[3][j:j + 4]), ([0x34, 0, 0, 0], [500, 0, 0, 0]), "velocity's slot 0 kept")
            wt_route(r, root, inst, key, 0, 0, 0)
        self.assertEqual(x2_store(r), [])

    def test_a_lane_edited_to_another_source_stays_until_reload(self):
        """X3 review (R2-2), a known limit pinned (stock's, now on our keys as on Position): the Mod Matrix's source edit of a
        lane's routing (seq1 -> velocity in matrix slot 0) posts only {slot 0, velocity} (test_native_props, M), so the lane
        stays in connect slot 3 and the depth sums both; the routing's removal posts {slot 0, source 0}, so the lane still
        plays. Stock's Position keeps its PARAM slot 4 the same way. Only the per-type post's slot 3 clear (a project's
        reload) ends it."""
        r = Rig(); root, vs = x2_root(r)
        o1 = vs[0] + S.VOICE_WT1
        pos = lambda: [r.r32(o1 + p + 0x20) for p in (0x30, 0x70, 0xB0)]
        for inst, key in X2_DESTS + ((0, 0xC9),):
            wt_route(r, root, inst, key, 0, SRC_SEQ[0], 1000)          # the toggle's lane: matrix slot 0
            wt_route(r, root, inst, key, 0, SRC_VEL, 700)              # the Mod Matrix's source edit
            wt_route(r, root, inst, key, 0, 0, 0)                      # and its removal
            if key == 0xC9:
                self.assertIn(root + ROOT_LANE_SRC, pos(), "stock's Position: the lane stays in PARAM slot 4")
            else:
                (e,) = x2_store(r)
                j = X2_K[(inst, key)] * X2_SLOTS
                self.assertEqual(e[2][j:j + 4], [0, 0, 0, X2_ROOT_REL | ROOT_LANE_SRC], "the lane stays in slot 3")
            wt_route(r, root, inst, key, 3, 0, 0)                      # the reload's per-type slot 3 clear
        self.assertEqual(x2_store(r), [])
        self.assertNotIn(root + ROOT_LANE_SRC, pos())

    def test_cost_heaviest_voice_lanes(self):
        """X2's heaviest voice (Wavetable both, Position every block, notes 24 / 36) with the depths from lanes at 1.0, amount
        1000 (the same sound as the knobs at 1000): seq1 on OSC1 Sub FM and seq2 on OSC2 FM 1>2 within HEAVIEST_VOICE; with
        seq4 on OSC2 Sub FM as well (a second FM term on OSC2) within X2's SUB2_HEAVY_MAX. Instructions per voice-block
        (an_enter and both renders); the lane read is stock's, once per root per block, with or without lanes."""
        lfo = lambda b: (b * 7 % 200) / 255
        two = [(0, K_SUBFM, 0, SRC_SEQ[0], 1000), (1, K_FM12, 0, SRC_SEQ[1], 1000)]
        def cost(note, knobs, routes=(), fill=0):
            w = WtVoice(self, types=(0, 0), frames=G.wt_saw_frames(200), capture=False, note=note)
            if knobs:
                w.prop(K_SUBFM, 1000, 0); w.prop(K_FM12, 1000, 1)
            for rt in routes:
                wt_route(w.r, w.root, *rt)
            if fill:                                  # X3 review (R2-5f): lanes on 8 tracks, ours the last entry scanned
                n = w.r.r32(SA + SA_RUN + RUN_X2N)
                ents = [bytes(w.r.u.mem_read(SA + SA_X2 + X2_ENTRY * i, X2_ENTRY)) for i in range(n)]
                fake = []
                for i in range(fill):
                    ro = w.r.alloc(0x2F0); w.r.w32(ro + S.ROOT_HANDLE, 0x5000 + i)
                    fake.append(struct.pack("<II12H12h", ro, 0x5000 + i, *[0] * 3, X2_ROOT_REL | ROOT_LANE_SRC, *[0] * 8,
                                            *[0] * 3, 1000, *[0] * 8))
                for i, e in enumerate((fake + ents)[:SA_X2_N]):
                    w.r.u.mem_write(SA + SA_X2 + X2_ENTRY * i, e)
                w.r.w32(SA + SA_RUN + RUN_X2N, min(SA_X2_N, fill + n))
            ln = Lanes(w.r, w.root)
            sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
            xs = []
            for b in range(16):
                ln.read([1.0, 1.0, 0.0, 1.0])
                for o in w.o:
                    w.r.wf(o + OSC_POS_SRC, lfo(b))
                t0 = sum(s.total for s in sp); w.block(); xs.append(sum(s.total for s in sp) - t0)
            return sorted(xs[4:])[len(xs[4:]) // 2], max(xs), w.outs
        rows = {}
        for note in (24, 36):
            k = cost(note, True)
            rows[f"note {note}: knobs"] = k
            rows[f"note {note}: 2 lanes"] = l2 = cost(note, False, two)
            same(self, l2[2], k[2], f"note {note}: the same sound from lanes")
            rows[f"note {note}: 2 lanes, 7 laned roots ahead"] = l7 = cost(note, False, two, fill=7)
            same(self, l7[2], k[2], f"note {note}: the same sound, ours the last entry")
            rows[f"note {note}: + OSC2 Sub FM lane"] = cost(note, False, two + [(1, K_SUBFM, 0, SRC_SEQ[3], 1000)])
        print("\n  X3 heaviest voice with lanes (instructions per voice-block, Position every block; steady median / max): " +
              "; ".join(f"{k} {a} / {b}" for k, (a, b, _) in rows.items()), file=sys.stderr)
        for k, (a, b, _) in rows.items():
            self.assertLessEqual(b, FmTargetsX2.SUB2_HEAVY_MAX if "OSC2 Sub FM" in k else HEAVIEST_VOICE, k)


REF444 = Path(os.environ.get("BE_REF444", str(Path.home() / "bento" / "out" / "be444w4f")))
REF444_OK = (REF444 / "bento1.bin").exists() and (REF444 / "manifest.json").exists()


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class X2Identity(unittest.TestCase):
    """BE4.45 X2: nothing routed on our three destinations, this build plays as BE4.44 (BE_REF444, default
    ~/bento/out/be444w4f: its image with its own DSP symbols, replayed) byte for byte: every output sample, the whole voice
    afterwards (both oscillators, their kernel areas, the sub) and the FM stats words, on the device's path (WtVoice: stock's
    root, voice render and sub) for the four type pairs with FM knobs, envelopes, Position moving and a stock routing (a
    Position LFO). Instructions per case: this build / BE4.44 (the no-route cost of the hooks: an_enter's and the gates'
    load and branch)."""
    CASES = (("Analog, Sub FM + FM 1>2, env", dict(types=(1, 1), sub=600, x12=400, env={0: (0, 20)})),
             ("Wavetable, Sub FM + FM 1>2", dict(types=(0, 0), sub=600, x12=400)),
             ("Wavetable, FM Atk on OSC2", dict(types=(0, 0), sub=300, x12=700, env={1: (8, 60)})),
             ("A1 -> WT2", dict(types=(1, 0), sub=0, x12=500)),
             ("WT1 -> A2", dict(types=(0, 1), sub=200, x12=500)),
             ("Analog, no FM", dict(types=(1, 1))),
             ("Wavetable, no FM", dict(types=(0, 0))),
             ("Wavetable, OSC2 Sub FM stored", dict(types=(0, 1), x12=300, osc2_sub=400)))

    @classmethod
    def setUpClass(cls):
        if not REF444_OK:
            raise unittest.SkipTest(f"no BE4.44 reference build at {REF444} (BE_REF444)")
        cls.ref_img = (REF444 / "bento1.bin").read_bytes()
        cls.ref_dsp = {k: int(v, 16) for k, v in json.loads((REF444 / "manifest.json").read_text())["dsp_symbols"].items()}

    def run_case(self, rig, kw, blocks=14):
        kw = dict(kw)
        osc2_sub = kw.pop("osc2_sub", None)
        w = WtVoice(self, capture=False, frames=G.wt_saw_frames(200), rig=rig, **kw)
        if osc2_sub:
            w.prop(K_SUBFM, osc2_sub, 1)
        src = route(w.r, w.o[0], 0, 0.5)                                 # a stock routing: an LFO on OSC1 Position
        def each(w, b):
            w.r.wf(src + 4, math.sin(b * 0.7))
        sp = [Span(w.r, rig.dsp["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
        w.run(blocks, each, notes=(9,))
        voice = bytes(w.r.u.mem_read(w.v, 0xBA0))
        return w.outs, voice, w.fmstats()[:6], sum(s.total for s in sp)

    def test_nothing_routed_is_be444_byte_for_byte(self):
        ratios = []
        for name, kw in self.CASES:
            with self.subTest(case=name):
                a = self.run_case(Rig(), kw)
                b = self.run_case(Rig(img=self.ref_img, dsp=self.ref_dsp), kw)
                self.assertGreater(len(set(a[0])), 1, "the voice plays")
                same(self, a[0], b[0], f"{name}: output")
                same(self, a[1], b[1], f"{name}: the voice's bytes")
                self.assertEqual(a[2], b[2], f"{name}: the FM stats")
                ratios.append((name, a[3] / b[3]))
        worst = max(ratios, key=lambda x: x[1])
        print(f"\n  X2Identity: {len(ratios)} cases, instructions this build / BE4.44 (an_enter and the oscillators' renders): "
              f"worst {worst[1]:.5f} ({worst[0]}), " + ", ".join(f"{n} {x:.5f}" for n, x in ratios), file=sys.stderr)


# BE4.46 S1 (docs/feasibility-fm-env-sources.md): FM Env 1 / 2 as routing sources 3 / 6, in the voice's allocation slack
# (typed here independently of synth_shared.h FMENV_*): source 3's pointer voice + FE_SRC, 6's + FE_SRC + 4, so FM Env 1 is
# the float at + FE_VAL, FM Env 2 at + FE_VAL + 4; FE_ON the stub's "a route was connected" word; the slack ends at FE_END
FE_SRC, FE_VAL, FE_ON, FE_END = 0xB84, 0xB88, 0xB90, 0xBA0
FE_VOICE = 0xB88                                            # stock's voice allocation (the slack starts here)
FE_ONE = 0x3F800000


def fe_route(r, root, inst, key, src, amount=500, slot=0):
    """Stock's routing connect (WT_ROUTE, message 0x3F): +2 instance, +0xC key, +0x10 slot, +0x14 source id, +0x16 amount
    (s16, x 0.001), on every voice of the root."""
    m = r.alloc(0x40)
    r.u.mem_write(m, struct.pack("<HH", 0, inst)); r.u.mem_write(m + 0xC, struct.pack("<H", key))
    r.u.mem_write(m + 0x10, struct.pack("<H", slot)); r.u.mem_write(m + 0x14, struct.pack("<Hh", src, amount))
    return r.call(S.WT_ROUTE, root, m)[1]


@unittest.skipUnless(HAVE and S.has("WT_ROUTE"), "no analog group in this image (1.6.1, or left out)")
class FmEnvSrc(unittest.TestCase):
    """BE4.46 S1 (docs/feasibility-fm-env-sources.md, S1's tests): FM Env 1 / FM Env 2 (each oscillator's FM envelope) as
    mod-matrix sources 3 / 6 of a Wavetable voice (the M4 side, list / names / validator, is S2). M7: stock's routing connect
    (WT_ROUTE) sends ids 3 / 6 through two stubs (build.py fmenv_connect) to the voice's slack, voice + FE_SRC / + FE_SRC + 4,
    which PARAM_VALUE reads at + 4; the stub's body (a DSP trampoline) gives that only to a Wavetable voice ([voice + 0x178]
    == voice + 0x7A4; S1 fix: the class-0x5A root shares the connect) and, on its first connect, sets FE_ON and 1.0 in both
    values. DSP: while FE_ON is set, an_enter publishes each oscillator's e0 (fm_env_step's first line, read only; 1 unset
    or untagged Wavetable) before the renders, and an_note the note's start value. Checked: the stubs, bodies and tables;
    the connect on both paths against stock's (every other id identical); the class-0x5A root's voices stock's for every
    id; a reconnect (an amount step) leaves the published values alone; the published values against p3_golden.fm_env's
    f32 e0, block by block, on Analog, tagged and untagged Wavetable oscillators, and the routed Filter 1 / Position (Shape)
    / Pitch parameters reading them; an envelope unset and set again mid-note; the publish changes nothing else; a write
    trace of the slack over a voice's life (stock: no write at all); the cost (nothing unless routed, an_enter within
    BE4.44's + a load and a branch)."""
    # (instance, key): OSC1 Position (an Analog OSC1's Shape), Filter 1 (keys 0xCD..0xCF, one parameter: frequency, I),
    # OSC2 Pitch, the sub's Pitch (the sub path), each a parameter of the voice at this offset (probe, M)
    DESTS = {"OSC1 Position": (0, 0xC9, 0x7D4), "Filter 1": (0, 0xCD, 0x5C4), "OSC2 Pitch": (1, 0x03, 0xA04),
             "sub Pitch": (2, 0x03, 0xD0)}

    def test_stubs_and_tables(self):
        """The image: ids 3 / 6 of both tables point at a stub in the table's padding; every other entry is stock's; each
        stub is exactly: addw r1, voice, #0xB88 (id 6); b.n join; addw r1, voice, #0xB84 (id 3); join: b.w its body; the
        rest of the padding nop.n. Each body (a DSP trampoline, S1 fix) is exactly: the Wavetable-voice gate ([voice +
        0x178] - 0x7A4 == voice, else stock's own movs r1, #0); FE_ON 0 -> FE_ON = r1 and 1.0 in both values; b.w the join
        after stock's movs. WT_VOICE_ALLOC is stock's movw r2, #0xb88 (the slack is the pool's rounding to 0xBA0) and
        WT_VOICE_OSC1 stock's addw r7, r4, #0x7a4 (the gate's offset)."""
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        h = lambda img, a, n: struct.unpack_from(f"<{n}H", img, a - BASE)
        for tbh, n, pad, padn, nxt, voice, tmp, first, body in (
                ("FMENV_IDX_TBH", 33, "FMENV_IDX_PAD", 28, "FMENV_IDX_NEXT", "r5", "r3", 1, DSP["tramp_fmenv_idx"]),
                ("FMENV_SUB_TBH", 32, "FMENV_SUB_PAD", 26, "FMENV_SUB_NEXT", "r3", "r2", 2, DSP["tramp_fmenv_sub"])):
            t, p, x = getattr(S, tbh), getattr(S, pad), getattr(S, nxt)
            ours, stock = h(IMG, t, n), h(STOCK_IMG, t, n)
            for i in range(n):
                tgt = t + 2 * ours[i]
                if i + first == 3:
                    self.assertEqual(tgt, p + 6, f"{tbh}: id 3 -> the stub's second addw")
                elif i + first == 6:
                    self.assertEqual(tgt, p, f"{tbh}: id 6 -> the stub's first addw")
                else:
                    self.assertEqual(ours[i], stock[i], f"{tbh}: id {i + first} is stock's")
            self.assertEqual(t + 2 * stock[3 - first], x - 2, "control: stock sends id 3 to no source")
            got = [(i.mnemonic, i.op_str) for i in md.disasm(IMG[p - BASE:p - BASE + 14], p)]
            want = [("addw", f"r1, {voice}, #0xb88"), ("b", f"#{p + 10:#x}"), ("addw", f"r1, {voice}, #0xb84"),
                    ("b.w", f"#{body:#x}")]
            self.assertEqual(got, want, f"{pad}: the stub")
            self.assertEqual(IMG[p + 14 - BASE:p + padn - BASE], b"\x00\xbf" * ((padn - 14) // 2), "the padding's rest: nop.n")
            got = [(i.mnemonic, i.op_str) for i in md.disasm(IMG[body - BASE:body - BASE + 42], body)]
            want = [("ldr.w", f"{tmp}, [{voice}, #0x178]"), ("subw", f"{tmp}, {tmp}, #0x7a4"), ("cmp", f"{tmp}, {voice}"),
                    ("bne", f"#{body + 38:#x}"), ("ldr.w", f"{tmp}, [{voice}, #0xb90]"), ("cbnz", f"{tmp}, #{body + 34:#x}"),
                    ("str.w", f"r1, [{voice}, #0xb90]"), ("mov.w", f"{tmp}, #0x3f800000"),
                    ("str.w", f"{tmp}, [{voice}, #0xb88]"), ("str.w", f"{tmp}, [{voice}, #0xb8c]"),
                    ("b.w", f"#{x:#x}"), ("b.w", f"#{x - 2:#x}")]
            self.assertEqual(got, want, f"{pad}: its body")
            self.assertEqual(STOCK_IMG[x - 2 - BASE:x - BASE], b"\x00\x21", "control: x - 2 is stock's movs r1, #0")
            self.assertTrue(S.M7_DSP <= body < S.M7_DSP + build.DSP_TRAMP_BYTES, "the body among the DSP trampolines")
        self.assertEqual(IMG[S.WT_VOICE_ALLOC - BASE:S.WT_VOICE_ALLOC - BASE + 4], bytes.fromhex("40f68832"), "movw r2, #0xb88")
        self.assertEqual(IMG[S.WT_VOICE_ALLOC - BASE:S.WT_VOICE_ALLOC - BASE + 4],
                         STOCK_IMG[S.WT_VOICE_ALLOC - BASE:S.WT_VOICE_ALLOC - BASE + 4], "the allocation is not patched")
        o1 = S.WT_VOICE_OSC1
        self.assertEqual([(i.mnemonic, i.op_str) for i in md.disasm(STOCK_IMG[o1 - BASE:o1 - BASE + 4], o1)],
                         [("addw", "r7, r4, #0x7a4")], "WT_VOICE_OSC1: OSC1 at voice + 0x7A4")

    def root(self, r):
        """Stock's root (ctor, engine init) and its 10 voices on Rig's bump pool (0xB88 rounded to 0xBA0, as stock's)."""
        pool_alloc(r)
        root = r.alloc(0x2F0); r.call(S.WT_ROOT_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
        r.w32(root + S.ROOT_HANDLE, 0x4646)
        vs = [r.r32(root + 0x14 + 4 * i) for i in range(10)]
        for v in vs:
            for o in (S.VOICE_WT1, S.VOICE_WT2):
                r.w32(v + o + 0x18, r.nowav)
        return root, vs

    def test_connect_gives_the_tail(self):
        """Every source id 0..35 to each destination (both tbh paths: instances 0 / 1 and the sub's 2), on this build and on
        stock from the same start: for every id but 3 / 6 the ten voices' bytes (0 .. 0xBA0) are stock's after the route;
        for 3 the routed slot of every voice holds voice + 0xB84, for 6 voice + 0xB88 (stock: 0, no source), its amount is
        stock's, FE_ON holds the pointer and both values are 1.0 (a first connect), and nothing else of the voice differs."""
        ra, rb = Rig(dev=True), Rig(img=STOCK_IMG, dev=True)
        (root, vs), (root_b, vs_b) = self.root(ra), self.root(rb)
        self.assertEqual((root, vs), (root_b, vs_b), "control: the same addresses on both rigs")
        self.assertEqual(vs[1] - vs[0], FE_END, "control: voices 0xBA0 apart (the slack)")
        for name, (inst, key, par) in self.DESTS.items():
            for src in range(36):
                fe_route(ra, root, inst, key, src); fe_route(rb, root, inst, key, src)
                for v in vs:
                    a, b = bytes(ra.u.mem_read(v, FE_END)), bytes(rb.u.mem_read(v, FE_END))
                    if src not in (3, 6):
                        if a != b:
                            same(self, a, b, f"{name}, id {src}: the voice is stock's")
                        continue
                    ptr = v + FE_SRC + (4 if src == 6 else 0)
                    self.assertEqual(struct.unpack_from("<I", a, par + 8)[0], ptr, f"{name}, id {src}: the slot")
                    self.assertEqual(struct.unpack_from("<I", b, par + 8)[0], 0, f"{name}, id {src}: stock: no source")
                    self.assertEqual(a[par + 12:par + 16], b[par + 12:par + 16], f"{name}, id {src}: the amount")
                    self.assertEqual(struct.unpack_from("<I", a, FE_ON)[0], ptr, f"{name}, id {src}: FE_ON")
                    self.assertEqual(struct.unpack_from("<II", a, FE_VAL), (FE_ONE, FE_ONE), f"{name}, id {src}: 1.0 in both")
                    self.assertEqual(b[FE_VOICE:FE_END], bytes(FE_END - FE_VOICE), "stock: the slack untouched")
                    m = bytearray(a)
                    m[par + 8:par + 12] = b[par + 8:par + 12]
                    m[par + 0x28:par + 0x34] = b[par + 0x28:par + 0x34]   # (PARAM_CONNECT's order and count: one slot more)
                    self.assertEqual(struct.unpack_from("<I", a, par + 0x30)[0], struct.unpack_from("<I", b, par + 0x30)[0] + 1,
                                     f"{name}, id {src}: one routed slot more than stock's")
                    m[FE_VOICE:FE_END] = b[FE_VOICE:FE_END]
                    same(self, bytes(m), b, f"{name}, id {src}: nothing else differs")
                    # (the next id must start from stock's state again: the voice's slot and slack back as stock's)
                    ra.u.mem_write(v, b)

    def test_other_root_is_stock(self):
        """S1 fix (review, high): WT_ROUTE is also the class-0x5A root's connect (ROOT_5A_CTOR: 6 voices of 0xFE8 B, its
        OSC1 at + 0x7A8, so voice + 0xB84.. lies inside a live oscillator). Every source id 0..35 to each destination on
        that root, this build and stock from the same start: every voice's bytes (0 .. 0x1000, the pool's rounding) and
        the root's are stock's after every route, ids 3 / 6 included (the body's gate takes stock's no-source path); the
        body ran (control) and never reached its FE_ON store."""
        def root5a(r):
            pool_alloc(r)
            root = r.alloc(0x2F0); r.call(S.ROOT_5A_CTOR, root); r.call(S.WT_ENGINE_INIT, root)
            r.w32(root + S.ROOT_HANDLE, 0x4646)
            return root, [r.r32(root + 0x14 + 4 * i) for i in range(6)]
        ra, rb = Rig(dev=True), Rig(img=STOCK_IMG, dev=True)
        (root, vs), (root_b, vs_b) = root5a(ra), root5a(rb)
        self.assertEqual((root, vs), (root_b, vs_b), "control: the same addresses on both rigs")
        self.assertEqual(vs[1] - vs[0], 0x1000, "control: voices of 0xFE8 B (0x1000 apart)")
        self.assertEqual([ra.r32(v + 0x178) - v for v in vs], [0x7A8] * 6, "control: OSC1 at + 0x7A8, not a Wavetable voice")
        hits = {"body": 0, "store": 0}
        for k, a, off in (("body", DSP["tramp_fmenv_idx"], 0), ("body", DSP["tramp_fmenv_sub"], 0),
                          ("store", DSP["tramp_fmenv_idx"], 0x12), ("store", DSP["tramp_fmenv_sub"], 0x12)):
            ra.u.hook_add(UC_HOOK_CODE, lambda uc, _a, _s, _u, k=k: hits.__setitem__(k, hits[k] + 1), begin=a + off, end=a + off)
        for name, (inst, key, par) in self.DESTS.items():
            for src in range(36):
                fe_route(ra, root, inst, key, src); fe_route(rb, root, inst, key, src)
                same(self, bytes(ra.u.mem_read(root, 0x2F0)), bytes(rb.u.mem_read(root, 0x2F0)), f"{name}, id {src}: the root")
                for v in vs:
                    a, b = bytes(ra.u.mem_read(v, 0x1000)), bytes(rb.u.mem_read(v, 0x1000))
                    if a != b:
                        same(self, a, b, f"{name}, id {src}: the class-0x5A voice is stock's")
        self.assertGreater(hits["body"], 0, "control: ids 3 / 6 ran the body on this root")
        self.assertEqual(hits["store"], 0, "the gate: never FE_ON on a class-0x5A voice")

    def test_a_reconnect_keeps_the_values(self):
        """Review (medium): only a voice's first connect writes 1.0. A later connect on id 3 / 6 (the M4 sends one per amount
        step) changes the slot's amount as stock's would and leaves FE_ON and both published values as they were."""
        w = WtVoice(self, types=(1, 1), env={0: (5, 8), 1: (3, 6)}, capture=False)
        r, v = w.r, w.v
        fe_route(r, w.root, 0, 0xCD, 3, amount=500)
        self.assertEqual((r.r32(v + FE_ON), r.r32(v + FE_VAL), r.r32(v + FE_VAL + 4)), (v + FE_SRC, FE_ONE, FE_ONE),
                         "the first connect: FE_ON and 1.0 in both")
        w.note(w.note_n)
        for _ in range(3):
            w.block()
        before = (r.r32(v + FE_ON), r.rf(v + FE_VAL), r.rf(v + FE_VAL + 4))
        self.assertTrue(all(0.0 < e < 1.0 for e in before[1:]), f"control: both mid-attack {before[1:]}")
        par = v + self.DESTS["Filter 1"][2]
        for amt, src, inst, key in ((-300, 3, 0, 0xCD), (700, 6, 1, 0x03), (100, 3, 2, 0x03)):
            fe_route(r, w.root, inst, key, src, amount=amt)
            self.assertEqual((r.r32(v + FE_ON), r.rf(v + FE_VAL), r.rf(v + FE_VAL + 4)), before,
                             f"id {src}, amount {amt}: FE_ON and the values untouched")
        self.assertAlmostEqual(r.rf(par + 12), -0.3, places=6, msg="control: the reconnect set Filter 1's amount")

    def test_unset_and_set_again_mid_note(self):
        """Review (low, mutants B / D / P6): OSC1's envelope (Atk 1 / Dec 2) runs to its end (e 0) and is then unset mid-note
        (Atk 0 / Dec Inf): FM Env 1 is 1 from the next block on (what FM depth sees; fmk / fme keep their stale end values
        until the render's hold); held unset (fmk DEC j 0, fme still 0) it stays 1; set again (Dec 2, Atk 0) mid-note it
        is 1 in that block (fm_env_step reads DEC j 0 as 1, not the stale fme) and then decays as fm_env_step's own."""
        w = WtVoice(self, types=(1, 1), env={0: (1, 2)}, capture=False)
        r, v, o = w.r, w.v, w.o[0]
        fe_route(r, w.root, 0, 0xCD, 3)
        fmk = lambda: struct.unpack("<H", r.u.mem_read(o + S.OSC_KERNEL + FMK_AT, 2))[0]
        fme = lambda: r.rf(o + S.OSC_KERNEL + FME_AT)
        w.note(w.note_n)
        for b in range(64):
            w.block()
            if fmk() >> 13 == 3:                         # (analog.h AN_FE_END)
                break
        w.block()                                        # (one block in END: its e0 is the end's 0)
        self.assertEqual((fmk() >> 13, r.rf(v + FE_VAL), fme()), (3, 0.0, 0.0), "control: at its end, published 0")
        end_k = fmk()
        w.prop(K_FMATK, 0, 0); w.prop(K_FMDEC, FM_DEC_INF, 0)
        self.assertEqual((fmk(), fme()), (end_k, 0.0), "control: the prop message leaves fmk / fme stale")
        for b in range(3):
            w.block()
            self.assertEqual(r.rf(v + FE_VAL), 1.0, f"unset, block {b}: FM Env 1 = 1")
        self.assertEqual((fmk(), fme()), (AN_FE_DEC_HOLD, 0.0), "control: held at DEC j 0 with fme a stale 0")
        w.prop(K_FMDEC, 2, 0)
        w.block()
        self.assertEqual(r.rf(v + FE_VAL), 1.0, "set again mid-note (Dec 2): FM Env 1 = 1 in that block")
        e1 = fme()
        self.assertTrue(0.0 < e1 < 1.0, f"control: it decays from 1 ({e1})")
        w.block()
        self.assertEqual(r.rf(v + FE_VAL), e1, "the next block: fm_env_step's own value")

    # cases: (name, types, env {osc: (Atk, Dec)}, notes again at, blocks)
    CASES = (("Analog 5/8 + 3/Inf", (1, 1), {0: (5, 8), 1: (3, FM_DEC_INF)}, (9, 10), 24),
             ("Analog unset", (1, 1), {}, (5,), 8),
             ("Analog 0/0 + 1/2", (1, 1), {0: (0, 0), 1: (1, 2)}, (4,), 10),
             ("Wavetable tagged 2/4 + 0/3", (0, 0), {0: (2, 4), 1: (0, 3)}, (7,), 12),
             ("Wavetable untagged", (0, 0), {}, (3,), 6),
             ("mixed A1 4/6, WT2 untagged", (1, 0), {0: (4, 6)}, (8,), 12))

    def test_values_follow_the_golden(self):
        """Per block, the published FM Env 1 / 2 (after the block: an_enter wrote them before the renders, which do not)
        are p3_golden.fm_env's f32 e0 of each oscillator's own envelope bit for bit (1 unset, 1 on an untagged Wavetable
        oscillator), the note's start value right after each note start (an_note, before the block); the routed parameters
        read them: Filter 1 and OSC2 Pitch (PARAM_VALUE) = clamp(their value at 0 + amount x e0), and an Analog OSC1's Shape
        (Position routed: J4) ends the block at 48 + amount x e0 x 127 (within its 1/512 step)."""
        for name, types, env, notes, n in self.CASES:
            with self.subTest(case=name):
                w = WtVoice(self, types=types, env=env, capture=False)
                r, v = w.r, w.v
                fe_route(r, w.root, 0, 0xC9, 3); fe_route(r, w.root, 0, 0xCD, 3); fe_route(r, w.root, 1, 0x03, 6)
                want = [G.fm_env(*env.get(i, (0, FM_DEC_INF)), n, notes=(0,) + notes, law="f32")[0] for i in (0, 1)]
                tagged = [types[i] == 1 or i in env for i in (0, 1)]
                amt = r.rf(v + 0x7D4 + 12)
                r.wf(v + 0x5C0, 0.25)                        # (Filter 1's knob, its source at voice + 0x5BC: inside 0..1)
                for b in range(n):
                    if b in notes:
                        w.note(w.note_n)
                        for i in (0, 1):
                            self.assertEqual(r.rf(v + FE_VAL + 4 * i), f32(want[i][b] if tagged[i] else 1.0),
                                             f"block {b}: FM Env {i + 1} at the note start (an_note)")
                    w.block()
                    for i in (0, 1):
                        self.assertEqual(r.rf(v + FE_VAL + 4 * i), f32(want[i][b] if tagged[i] else 1.0),
                                         f"block {b}: FM Env {i + 1} = the golden's e0")
                    for dn, i in (("Filter 1", 0), ("OSC2 Pitch", 1)):
                        par = v + self.DESTS[dn][2]
                        e = r.rf(v + FE_VAL + 4 * i)
                        pv = r.call(S.PARAM_VALUE, par)[0]
                        pv = r.rf(par + 0x3C)                    # (PARAM_VALUE's result, cached at +0x3C)
                        r.wf(v + FE_VAL + 4 * i, 0.0); r.call(S.PARAM_VALUE, par); p0 = r.rf(par + 0x3C)
                        r.wf(v + FE_VAL + 4 * i, e)
                        lo, hi = r.rf(par + 0x34), r.rf(par + 0x38)
                        self.assertTrue(lo < p0 < hi, f"{dn}: control, its value at 0 is inside its range")
                        self.assertAlmostEqual(pv, min(max(p0 + r.rf(par + 12) * e, lo), hi), delta=2e-6,
                                               msg=f"block {b}: {dn} reads FM Env {i + 1}")
                    if types[0] == 1:
                        sh = struct.unpack("<H", r.u.mem_read(w.o[0] + S.OSC_KERNEL + 166, 2))[0] / 512
                        self.assertLessEqual(abs(sh - (48 + amt * want[0][b] * 127)), 1 / 512 + 1e-6,
                                             f"block {b}: OSC1's Shape follows FM Env 1")
                if any(i in env for i in (0, 1)):
                    self.assertTrue(any(0.0 < x < 1.0 for i in (0, 1) for x in want[i]), "control: the envelope moves")

    def test_the_publish_changes_nothing_else(self):
        """The same voices played twice from the same start, once with FE_ON set by hand on every voice (no route): the
        output and every voice's bytes below the slack are the same, and only the slack's two values differ (published).
        With FE_ON 0, an_enter never calls fm_env_publish and the slack stays as it was."""
        for name, types, env, notes, n in self.CASES[::2]:
            with self.subTest(case=name):
                runs = []
                for on in (0, 1):
                    w = WtVoice(self, types=types, env=env, sub=300, x12=500, capture=False)
                    sp = Span(w.r, dsp_sym("fm_env_publish"))
                    if on:
                        w.r.w32(w.v + FE_ON, 1)
                    w.run(n, notes=notes)
                    runs.append((b"".join(w.outs), bytes(w.r.u.mem_read(w.v, FE_VOICE)),
                                 bytes(w.r.u.mem_read(w.v + FE_VOICE, FE_END - FE_VOICE)), sp.calls))
                same(self, runs[0][0], runs[1][0], f"{name}: the output")
                same(self, runs[0][1], runs[1][1], f"{name}: the voice below the slack")
                self.assertEqual(runs[0][2], bytes(FE_END - FE_VOICE), f"{name}: FE_ON 0: the slack untouched")
                self.assertEqual((runs[0][3], runs[1][3]), (0, n), f"{name}: fm_env_publish only while FE_ON")

    def test_write_trace_of_the_slack(self):
        """A root's life (ctor, engine init, our keys and FM envelopes, every other source id routed to four destinations,
        notes, retriggers, renders, Type changes) writes nothing into any voice's slack, on this build and on stock; then
        ids 3 / 6 routed: on this build only the stubs, fm_env_publish and an_note write there (FE_VAL .. FE_ON + 4), on
        stock still nothing."""
        for img in (IMG, STOCK_IMG):
            r = Rig(img=img, dev=True)
            writes = []
            def wr(uc, acc, a, size, val, _):
                if 0xC5F10000 <= a < 0xC5F10000 + 10 * FE_END and (a - 0xC5F10000) % FE_END >= FE_VOICE:  # addr-ok: harness
                    writes.append((uc.reg_read(UC_ARM_REG_PC), (a - 0xC5F10000) % FE_END, phase[0]))  # addr-ok: harness
            phase = ["life"]
            r.u.hook_add(UC_HOOK_MEM_WRITE, wr)
            root, v0, sub, _ = SubCopy.voice(self, r, 4, 1000, fm=(300, 0), types=(1, 0), glide=100, pw=300, fine=0)
            for i, (a, d) in ((0, (3, 5)), (1, (2, FM_DEC_INF))):
                r.root_prop(root, K_FMATK, a, i); r.root_prop(root, K_FMDEC, d, i)
            r.root_prop(root, K_FM12, 400, 1)
            m = r.call(r.r32(r.r32(root) + 0x4C) & ~1, root)[0]
            acc, scr, out = r.buf(DEV_N * m, 1024), r.buf(DEV_N * m, 1024), r.buf(DEV_N, 1024)
            slot18 = r.r32(r.r32(root) + 0x48)
            def play(blocks, note_at=()):
                for b in range(blocks):
                    if b in note_at:
                        SubCopy.note(self, r, v0, sub, 48 + b)
                    r.w32(acc[0] + 4, DEV_N * m); r.w32(scr[0] + 4, DEV_N * m); r.w32(out[0] + 4, DEV_N)
                    r.call(slot18 & ~1, root, v0, 0, scr[0], sargs=(acc[0], 0, out[0]))
                    r.block()
            for name, (inst, key, par) in self.DESTS.items():
                for src in range(36):
                    if src not in (3, 6):
                        fe_route(r, root, inst, key, src, slot=src % 4)
            play(6, (0, 3, 4))
            r.root_prop(root, K_TYPE, 0, 0); r.root_prop(root, K_TYPE, 1, 1); play(3, (1,))
            r.root_prop(root, K_TYPE, 1, 0); play(3, (0,))
            self.assertEqual(writes, [], f"{'this build' if img is IMG else 'stock'}: no write into the slack over a life")
            phase[0] = "routed"
            fe_route(r, root, 0, 0xCD, 3, slot=1); fe_route(r, root, 1, 0x03, 6, slot=2); fe_route(r, root, 2, 0x03, 3, slot=3)
            play(5, (0, 2))
            if img is STOCK_IMG:
                self.assertEqual(writes, [], "stock: no write into the slack, ids 3 / 6 routed")
                continue
            allowed = [(DSP["tramp_fmenv_idx"], DSP["tramp_fmenv_idx"] + 42), (DSP["tramp_fmenv_sub"], DSP["tramp_fmenv_sub"] + 42),
                       sym_range("fm_env_publish"), sym_range("an_note")]
            self.assertTrue(writes, "control: the routes and the publish write there")
            for pc, off, ph in writes:
                self.assertTrue(any(lo <= pc < hi for lo, hi in allowed), f"write at slack +{off:#x} from {pc:#x}")
                self.assertIn(off, (FE_VAL, FE_VAL + 4, FE_ON), f"write at slack +{off:#x}")
            for v in [r.r32(root + 0x14 + 4 * i) for i in range(10)]:
                self.assertNotEqual(r.r32(v + FE_ON), 0, "every voice: FE_ON set by the connect")

    def voice_cost(self, on, blocks=16, **kw):
        """WtFmW3.voice_cost's per-voice-block instructions (an_enter and both renders), FE_ON set (a route) or not, and
        fm_env_publish's own Span."""
        w = WtVoice(self, frames=G.wt_saw_frames(200), capture=False, **{k: x for k, x in kw.items() if k != "pos"})
        pos = kw.get("pos")
        if on:
            fe_route(w.r, w.root, 0, 0xCD, 3); fe_route(w.r, w.root, 0, 0xCD, 6, slot=1)   # (Filter 1: no change of pitch)
        sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
        pub = Span(w.r, dsp_sym("fm_env_publish"))
        per = []
        for b in range(blocks):
            if pos:
                for o in w.o:
                    w.r.wf(o + OSC_POS_SRC, pos(b))
            t0 = sum(s.total for s in sp)
            w.block()
            per.append(sum(s.total for s in sp) - t0)
        return per, pub, sp[0]

    def test_cost(self):
        """Per voice-block (an_enter and both renders, note 60 unless named): without a route to 3 / 6 fm_env_publish never
        runs; with one it is at most PUBLISH_MAX a voice-block, and the heaviest steady voice (W4: note 24, light FM,
        Position every block) stays within the heaviest existing voice (35,309) in every block but its one level-change
        block (W4_ATK_MAX). Printed: median / max with and without, the publish's own and an_enter's."""
        lfo = lambda b: (b * 7 % 200) / 255
        rows = []
        med = lambda per: sorted(per[4:])[len(per[4:]) // 2]
        for name, kw in (("stock WT voice", {}),
                         ("Analog full FM, FM Atk 3 / Dec 8", dict(types=(1, 1), sub=1000, x12=1000, env={0: (3, 8), 1: (3, 8)})),
                         ("full WT voice, FM Atk 3", dict(sub=1000, x12=1000, env={0: (3, FM_DEC_INF), 1: (3, FM_DEC_INF)})),
                         ("full WT voice, note 24, light FM, Position every block", dict(sub=300, x12=300, note=24, pos=lfo))):
            (p0, pub0, en0), (p1, pub1, en1) = self.voice_cost(0, **kw), self.voice_cost(1, **kw)
            self.assertEqual(pub0.calls, 0, f"{name}: no route, no publish")
            # (review: "~0 without a route" at its own granularity: an_enter a call and the voice-block median within
            # the base's + NO_ROUTE_TOL, a load and a branch. BE4.46 merge: the base is BE4.45, the destinations lane this
            # feature was merged onto (its an_enter is BE4.44's + 8: X2's xm store and x2n test), re-measured, not loosened)
            en_base, med_base = self.NO_ROUTE_BASE[name]
            self.assertLessEqual(en0.total / en0.calls, en_base + self.NO_ROUTE_TOL, f"{name}: no route, an_enter a call")
            self.assertLessEqual(med(p0), med_base + self.NO_ROUTE_TOL, f"{name}: no route, the voice-block median")
            self.assertEqual(pub1.calls, len(p1), f"{name}: routed, one publish a voice-block")
            self.assertLessEqual(pub1.total, pub1.calls * self.PUBLISH_MAX, f"{name}: the publish's cost")
            rows.append((name, med(p0), max(p0), med(p1), max(p1), pub1.total / pub1.calls, en0.total / en0.calls,
                         en1.total / en1.calls))
            if "Position" in name:
                self.assertLessEqual(sorted(p1)[-2], HEAVIEST_VOICE, f"{name}: routed, every block but one")
                self.assertLessEqual(max(p1), WtFmW4.W4_ATK_MAX, f"{name}: routed, its level-change block")
        print("\n  FM Env sources cost (instructions per voice-block; median / max without a route, with one; the publish a "
              "call; an_enter a call without / with): " + "; ".join(
                  f"{n} {a} / {b}, {c} / {d}, publish {e:.0f}, an_enter {f:.0f} / {g:.0f}" for n, a, b, c, d, e, f, g in rows),
              file=sys.stderr)
    PUBLISH_MAX = 80
    # BE4.44 (c541f3b, measured with this harness: an_enter's Span a call, the voice-block median; S1 fix scratch en.py, M)
    NO_ROUTE_BE444 = {"stock WT voice": (59.0, 14155), "Analog full FM, FM Atk 3 / Dec 8": (156.62, 28109),
                      "full WT voice, FM Atk 3": (99.56, 27261),
                      "full WT voice, note 24, light FM, Position every block": (99.56, 34760)}
    # BE4.46 merge: the base this feature now sits on is BE4.45 (be445x 7d72495, ~/bento/out/be445x3r), measured with the
    # same harness (scratch b446/en.py, M; it reproduces NO_ROUTE_BE444 exactly on be444w4f). The merged build measured
    # 70.00 / 14,166, 166.62 / 28,135, 110.56 / 27,310, 110.56 / 34,809: BE4.45's + 2..3
    NO_ROUTE_BASE = {"stock WT voice": (67.0, 14163), "Analog full FM, FM Atk 3 / Dec 8": (164.62, 28133),
                     "full WT voice, FM Atk 3": (107.56, 27307),
                     "full WT voice, note 24, light FM, Position every block": (107.56, 34806)}
    NO_ROUTE_TOL = 8


# ---- BE4.46 (the merge of BE4.45 X1..X3 and BE4.46 S1 / S2): FM Env 1 / 2 routed to the three FM depths
@unittest.skipUnless(HAVE and S.has("WT_ROUTE") and "x2_connect" in DSP, "no analog group with X2 in this image")
class FmEnvToFmDepth(unittest.TestCase):
    """FM Env 1 / 2 (sources 3 / 6: S1's pseudo-sources voice + FE_SRC / + FE_SRC + 4, their floats at + FE_VAL / + 4) routed
    to OSC1 Sub FM, OSC2 FM 1>2 and OSC2 Sub FM (X2): WT_ROUTE gives X2's slot-10 hook the pseudo-source like any per-voice
    source, the store keeps it (voice-relative, inside X2's bound: dsp/analog.cpp static_assert), and per voice-block the
    depth is X2's law of **this block's** published e0 (an_enter publishes before x2_enter: plan-fm-targets X2 open 3), on
    Analog and Wavetable oscillators. For OSC1 Sub FM / OSC2 FM 1>2 the voice is bit for bit the knob turned to that v every
    block. The cost on the heaviest voice."""
    ENVS = {(1, 1): {0: (2, 5), 1: (1, 4)}, (0, 0): {0: (2, 4), 1: (1, 6)}}   # (FM Atk, FM Dec) per oscillator
    N = 14

    def run_route(self, types, inst, key, src, how, base=200, amount=600, remove=None):
        """One voice, FM envelopes on both oscillators, the other FM knob at 300; how: 'fmenv' (the depth routed from source
        src, matrix slot 0), 'knob' (nothing routed; the knob turned to x2_law(base, amount x e0) before each block) or 'vel'
        (routed from velocity, slot 0, its float written to f32 e0 before each block: a per-voice source carrying FM Env's
        values; the reference for OSC2 Sub FM, which has no knob). remove: the block at which the routing is removed (source 0)
        and, for 'knob', the knob turned back to base. (outputs, the depth after each block, FM Env 1 / 2 after each block,
        the store, e0 of both oscillators)."""
        env = self.ENVS[types]
        e0 = [G.fm_env(*env[i], self.N, notes=(0,), law="f32")[0] for i in (0, 1)]
        w = WtVoice(self, types=types, env=env, capture=False, frames=G.wt_saw_frames(200))
        other = (1, K_FM12) if (inst, key) == (0, K_SUBFM) else (0, K_SUBFM)
        w.prop(other[1], 300, other[0])
        w.prop(key, base, inst)
        if how in ("fmenv", "vel"):
            wt_route(w.r, w.root, inst, key, 0, src if how == "fmenv" else SRC_VEL, amount)
        depth, pub = [], []
        for b in range(self.N):
            gone = remove is not None and b >= remove
            if b == remove and how != "knob":
                wt_route(w.r, w.root, inst, key, 0, 0, 0)
                self.assertEqual(x2_store(w.r), [], "removed")
            if how == "knob":
                w.prop(key, base if gone else x2_law(base, [(amount, e0[0 if src == 3 else 1][b])]), inst)
            elif how == "vel":
                w.r.wf(w.v + 0x38, float(f32(e0[0 if src == 3 else 1][b])))
            w.block()
            msub, m12 = struct.unpack("<HH", w.r.u.mem_read(w.o[inst] + S.OSC_KERNEL + MSUB_AT, 4))
            depth.append(m12 if key == K_FM12 else msub)
            pub.append((w.r.rf(w.v + FE_VAL), w.r.rf(w.v + FE_VAL + 4)))
        return w.outs, depth, pub, x2_store(w.r), e0, w

    def test_fm_env_moves_each_fm_depth_per_block(self):
        """FM Env 1 on each of the three depths, and FM Env 2 on OSC2 FM 1>2 and OSC2 Sub FM, Analog and Wavetable: the
        store holds voice + FE_SRC (+ 4) in slot 0 with the amount; the published value after block b is the golden's f32 e0
        of block b; the depth after block b is x2_law(base, 600 x e0[b]) (this block's value, not the last block's) and it
        moves; nothing is lost. OSC1 Sub FM / OSC2 FM 1>2: the output is the knob turned to that v, bit for bit."""
        cases = [(0, K_SUBFM, 3), (1, K_FM12, 3), (1, K_SUBFM, 3), (1, K_FM12, 6), (1, K_SUBFM, 6)]
        for types in ((1, 1), (0, 0)):
            for inst, key, src in cases:
                with self.subTest(types=types, inst=inst, key=hex(key), src=src):
                    outs, depth, pub, st, e0, w = self.run_route(types, inst, key, src, "fmenv")
                    want = e0[0 if src == 3 else 1]
                    self.assertTrue(any(0.0 < x < 1.0 for x in want), "control: the envelope moves")
                    (e,) = st
                    j = X2_K[(inst, key)] * X2_SLOTS
                    self.assertEqual((e[2][j], e[3][j]), (FE_SRC + (4 if src == 6 else 0), 600),
                                     "the store: the FM Env pseudo-source, voice-relative, slot 0")
                    self.assertEqual([c for i, c in enumerate(e[2]) if c and i != j], [], "nothing else stored")
                    self.assertEqual(w.r.r32(SA + SA_FMSTATS + FM_X2_LOST_AT), 0, "not lost (inside X2's bound)")
                    self.assertNotEqual(w.r.r32(w.v + FE_ON), 0, "the connect set FE_ON")
                    self.assertEqual([p[0 if src == 3 else 1] for p in pub], [f32(x) for x in want],
                                     "the published value is the golden's e0, block by block")
                    base = 200 if (inst, key) != (1, K_SUBFM) or types[1] else 0     # a Wavetable OSC2's Sub FM base: 0 (X2)
                    self.assertEqual(depth, [x2_law(base, [(600, x)]) for x in want], "the depth per block: this block's e0")
                    self.assertGreater(len(set(depth)), 3, "it moves")
                    if (inst, key) != (1, K_SUBFM):
                        k_outs, k_depth, _, k_st, _, _ = self.run_route(types, inst, key, src, "knob")
                        self.assertEqual(k_st, [], "control: nothing routed")
                        self.assertEqual(depth, k_depth)
                        same(self, outs, k_outs, "FM Env routed = the knob turned to v every block")
                    else:                                    # (review: no knob plays OSC2's Sub FM: a routed reference)
                        v_outs, v_depth, _, v_st, _, _ = self.run_route(types, inst, key, src, "vel")
                        self.assertEqual(v_st[0][2][j], 0x34, "control: routed from velocity")
                        self.assertEqual(depth, v_depth)
                        same(self, outs, v_outs, "FM Env routed = velocity carrying the same values, bit for bit")

    def test_removal_ramps_out_and_keeps_fm_env_published(self):
        """Source 0 on the routed slot (the Mod Matrix's removal) at block 6: the store empties and every block is the knob's
        run turned to x2_law(base, 600 x e0) before block 6 and back to base from it (X2's ramp out included), bit for bit;
        OSC2 Sub FM (no knob plays it; unrouted it ramps to 0, X2): the same against velocity carrying e0, removed at the
        same block. FE_ON stays set
        (S1: sticky per voice), so FM Env keeps publishing."""
        cases = [(0, K_SUBFM, 3), (1, K_FM12, 6), (1, K_SUBFM, 6)]
        for types in ((1, 1), (0, 0)):
            for inst, key, src in cases:
                with self.subTest(types=types, inst=inst, key=hex(key), src=src):
                    outs, depth, pub, st, e0, w = self.run_route(types, inst, key, src, "fmenv", remove=6)
                    ref = "vel" if (inst, key) == (1, K_SUBFM) else "knob"
                    k_outs, k_depth, _, k_st, _, _ = self.run_route(types, inst, key, src, ref, remove=6)
                    self.assertEqual(st, [], "the store is empty")
                    base = 200 if (inst, key) != (1, K_SUBFM) else 0        # (unrouted, OSC2's Sub FM plays 0: X2)
                    self.assertNotEqual(depth[5], base, "control: routed before the removal")
                    self.assertEqual(depth[-1], base, "the depth back at the knob (OSC2 Sub FM: at 0)")
                    self.assertEqual(depth, k_depth)
                    same(self, outs, k_outs, f"routed then removed = the {ref} run, every block")
                    self.assertNotEqual(w.r.r32(w.v + FE_ON), 0, "FE_ON sticky")
                    want = e0[0 if src == 3 else 1]
                    self.assertEqual([p[0 if src == 3 else 1] for p in pub], [f32(x) for x in want], "still published")

    FE_WORST_MAX = 35440        # BE4.46 merge review (R1): X2's worst two-key case (8 slots, 7 routed roots ahead) with an
                                # FM Env source in slot 0 and / or both FM envelopes running: M 35,312..35,392 steady, the
                                # second-largest block 35,425 (one FM Env, both running: 35,393). Over HEAVIEST_VOICE (by
                                # up to 116: velocity with the envelopes running is already 35,350 there, as on BE4.45):
                                # a known miss, Jeff's (plan-fm-targets "BE4.46 merge review", Open 1), gated at its level

    def test_cost_heaviest_voice_fm_env_routes(self):
        """The heaviest existing voice (Wavetable both, Position every block, notes 24 / 36: X2's) with FM Env 1 on OSC1 Sub
        FM and FM Env 2 on OSC2 FM 1>2 at 1000 (envelopes unset: e0 is 1, so the same sound as both knobs at 1000,
        asserted); the same with OSC2 Sub FM from FM Env 2 too; and with FM envelopes on both (Atk 3, Dec Inf: the FM Env
        itself running). Two keys: every block within HEAVIEST_VOICE; with the envelopes running, every block but one (the
        level change) within it and that one within W4_ATK_MAX; + OSC2 Sub FM within X2's SUB2_HEAVY_MAX (X2 open 5,
        Jeff's), with the envelopes running every block but two (the level change and a second peak, both within
        W4_ATK_MAX), and so is the worst of all three keys (12 slots, FM Env in slot 0, 7 routed roots ahead). Merge review:
        X2's worst two-key case (all 8 slots, 7 routed roots ahead in the store) with FM Env in slot 0 (one source, and
        both), unset and running, and velocity there with the envelopes running: within FE_WORST_MAX (a known miss, over
        HEAVIEST_VOICE). Instructions per
        voice-block (an_enter and both renders), steady median / max."""
        lfo = lambda b: (b * 7 % 200) / 255
        SUB1, FM12 = (0, K_SUBFM), (1, K_FM12)
        two = [(*SUB1, 0, 3, 1000), (*FM12, 0, 6, 1000)]
        seq = [(*d, 0, SRC_SEQ1, 300) for d in (SUB1, FM12)]          # (slot 3: SRC_SEQ1 is root-relative)
        full = {"velocity": [(*d, sl, src, 1000) for d in (SUB1, FM12) for sl, src in ((0, SRC_VEL), (1, 0x1), (2, 0x2))],
                "FM Env": [(*SUB1, 0, 3, 1000), (*SUB1, 1, 0x1, 1000), (*SUB1, 2, 0x2, 1000),
                           (*FM12, 0, 6, 1000), (*FM12, 1, 0x1, 1000), (*FM12, 2, 0x2, 1000)],
                "FM Env 1 + 2": [(*SUB1, 0, 3, 1000), (*SUB1, 1, 6, 1000), (*SUB1, 2, SRC_VEL, 1000),
                                 (*FM12, 0, 6, 1000), (*FM12, 1, 3, 1000), (*FM12, 2, SRC_VEL, 1000)]}
        def cost(note, knobs, routes=(), env=None, fill=0):
            w = WtVoice(self, types=(0, 0), frames=G.wt_saw_frames(200), capture=False, note=note, env=env)
            if knobs:
                w.prop(K_SUBFM, 1000, 0); w.prop(K_FM12, 1000, 1)
            for rt in routes:
                wt_route(w.r, w.root, *rt)
            if fill:                                                  # routed roots ahead of ours (X2's test's layout)
                n = w.r.r32(SA + SA_RUN + RUN_X2N)
                ents = [bytes(w.r.u.mem_read(SA + SA_X2 + X2_ENTRY * i, X2_ENTRY)) for i in range(n)]
                fake = []
                for i in range(fill):
                    ro = w.r.alloc(0x2F0); w.r.w32(ro + S.ROOT_HANDLE, 0x5000 + i)
                    fake.append(struct.pack("<II12H12h", ro, 0x5000 + i, 0x34, *[0] * 11, 100, *[0] * 11))
                for i, e in enumerate((fake + ents)[:SA_X2_N]):
                    w.r.u.mem_write(SA + SA_X2 + X2_ENTRY * i, e)
                w.r.w32(SA + SA_RUN + RUN_X2N, min(SA_X2_N, fill + n))
                w.r.wf(w.v + 0x38, 1.0)
            sp = [Span(w.r, DSP["an_enter"])] + [Span(w.r, a) for a in {w.r.r32(w.r.r32(o)) & ~1 for o in w.o}]
            xs = []
            for b in range(16):
                for o in w.o:
                    w.r.wf(o + OSC_POS_SRC, lfo(b))
                t0 = sum(s.total for s in sp); w.block(); xs.append(sum(s.total for s in sp) - t0)
            return sorted(xs[4:])[len(xs[4:]) // 2], max(xs), w.outs, xs
        env = {0: (3, FM_DEC_INF), 1: (3, FM_DEC_INF)}
        sub2 = [(1, K_SUBFM, 0, 6, 1000)]
        rows = {}
        for note in (24, 36):
            k = cost(note, True)
            rows[f"note {note}: knobs"] = k
            rows[f"note {note}: FM Env 1 / 2 on 2 depths"] = r2 = cost(note, False, two)
            same(self, r2[2], k[2], f"note {note}: FM Env (unset: 1) at 1000 = the knobs at 1000")
            rows[f"note {note}: + OSC2 Sub FM from FM Env 2"] = cost(note, False, two + sub2)
            rows[f"note {note}: knobs, envelopes on"] = cost(note, True, (), env)
            rows[f"note {note}: FM Env on 2 depths, envelopes on"] = cost(note, False, two, env)
            rows[f"note {note}: + OSC2 Sub FM from FM Env 2, envelopes on"] = cost(note, False, two + sub2, env)
            sub2w = [(1, K_SUBFM, sl, src, 1000) for sl, src in ((0, 6), (1, 0x1), (2, 0x2))] + [(1, K_SUBFM, 0, SRC_SEQ1, 300)]
            for e in (None, env):
                rows[f"note {note}: + OSC2 Sub FM worst, 12 slots (FM Env), 7 roots ahead" +
                     (", envelopes on" if e else "")] = cost(note, False, full["FM Env"] + seq + sub2w, e, fill=7)
            for name, rts in full.items():
                for e in ((None, env) if name != "velocity" else (env,)):
                    rows[f"note {note}: worst, 8 slots ({name}), 7 roots ahead" + (", envelopes on" if e else "")] = \
                        cost(note, False, rts + seq, e, fill=7)
        print("\n  FM Env -> FM depth, heaviest voice (instructions per voice-block, Position every block; steady median / "
              "2nd-largest / max): " + "; ".join(f"{k} {a} / {sorted(xs)[-2]} / {b}" for k, (a, b, _, xs) in rows.items()),
              file=sys.stderr)
        for k, (a, b, _, xs) in rows.items():
            top = FmTargetsX2.SUB2_HEAVY_MAX if "OSC2 Sub FM" in k else self.FE_WORST_MAX if "worst" in k else HEAVIEST_VOICE
            if "envelopes on" in k:
                # the level change block; with OSC2 Sub FM routed also a second one, 2 blocks later (M ~40.0k: velocity
                # as the source too), both within W4_ATK_MAX
                peaks = 2 if "OSC2 Sub FM" in k else 1
                self.assertLessEqual(sorted(xs)[-1 - peaks], top, f"{k}: every block but {peaks}")
                self.assertLessEqual(b, WtFmW4.W4_ATK_MAX, f"{k}: the peak blocks")
            else:
                self.assertLessEqual(b, top, k)
            if "worst" in k and "OSC2 Sub FM" not in k:
                self.assertGreater(b, HEAVIEST_VOICE - 100, f"{k}: control (the worst case measures as one)")


def r_settings(r, osc):
    """AnProps of an oscillator: (type, shape, sync, fmatk, glide, subfm, fm12, fmdec, envf)."""
    return struct.unpack("<BBBBHHHBB", r.u.mem_read(osc + S.OSC_KERNEL + PROPS_AT, 12))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v", *sys.argv[1:]])
