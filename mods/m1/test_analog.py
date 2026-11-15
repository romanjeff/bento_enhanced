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
"""
import json, math, random, struct, sys, unittest
from fractions import Fraction
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
BUILD = Path(sys.argv[2])
S = from_build(BUILD, Path(sys.argv[1]).read_bytes())
MAN = json.loads((BUILD / "manifest.json").read_text())
IMG = (BUILD / "bento1.bin").read_bytes()
STOCK_IMG = Path(sys.argv[1]).read_bytes()
del sys.argv[1:3]
HAVE = "analog" not in MAN.get("omitted", []) and "analog" not in MAN.get("disabled", []) and "dsp_symbols" in MAN
DSP = {k: int(v, 16) for k, v in MAN.get("dsp_symbols", {}).items()}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack (DTCM top)  # addr-ok: harness
HEAP = 0x24010000            # harness: voices, buffers, roots  # addr-ok: harness
# synth area (synth_shared.h, typed here independently): 32 KB below the Region
SYNTH_BYTES = 0x8000
SA = S.REGION - SYNTH_BYTES if HAVE else 0
M7_MAGIC, M4_MAGIC = 0x37534542, 0x34534542
SA_M7, SA_M4, SA_STATS, SA_RUN = 0x0, 0x20, 0x40, 0x400
# our oscillator keys (docs/plan-native-props.md 3.1), typed: Type, Shape, Sync, Glide
K_TYPE, K_SHAPE, K_SYNC, K_GLIDE = 0x1C0, 0x1A7, 0x1AB, 0x1B7
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
    def __init__(self, img=IMG, dev=False):
        self.dev = dev
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
        _, c0 = self.call(DSP["an_enter"], root, v)
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
                r.u.hook_add(UC_HOOK_CODE, self._slave, begin=DSP["an_render"], end=DSP["an_render"], user_data=r)
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
            r.call(DSP["an_enter"], self.root, self.v)
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
        """Stock never range-checks (a file is read raw, an edit forwarded raw: test_native_props), so an_prop clamps."""
        r = Rig(); root, v = r.voice(0x61)
        o = r.osc(v, 0)
        r.prop(o, K_TYPE, 1)                                     # (slot 8 returns nothing: the effects are checked)
        self.assertEqual(r.settings(v, 0)[:5], (1, 48, 0, 0, 0), "Analog: Shape at its default (saw), the rest 0")
        for key, val, want, at in ((K_SHAPE, 1000, 127, 1), (K_SHAPE, -5, 0, 1), (K_SHAPE, 77, 77, 1), (K_GLIDE, 5000, 1000, 4),
                                   (K_GLIDE, -1, 0, 4), (K_GLIDE, 640, 640, 4), (K_SYNC, 7, 0, 2), (K_SYNC, 1, 1, 2), (K_SYNC, -1, 0, 2)):
            with self.subTest(key=hex(key), value=val):
                r.prop(o, key, val)
                self.assertEqual(r.settings(v, 0)[at], want)

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
        saw, _ = self.run_(0)
        same(self, low, saw, "clamped at 0")

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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
