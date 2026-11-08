# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog oscillators on Wavetable tracks, M7 side (S6a), on stock 1.6.8's own oscillator code.

    python test_analog.py <stock bento1.bin> <m1 build dir>

The built image runs in Unicorn (Cortex-M7 with its FPU): stock's Wavetable oscillator constructor and note
start, stock's helpers (pitch, level, smoothing, mixing) called by our hook, our two trampolines behind stock's
own vtable words, and the DSP ELF at its real placement. The synth area holds what the M4 would have written
(synth_shared.h: M7 header, M4 header, one table line), the pool counters say the reservation is held, and the
LOAD ring index moves once per block, as stock's render does.

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
"""
import json, math, struct, sys, unittest
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
del sys.argv[1:3]
HAVE = "analog" not in MAN.get("omitted", []) and "dsp_symbols" in MAN
DSP = {k: int(v, 16) for k, v in MAN.get("dsp_symbols", {}).items()}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack (DTCM top)  # addr-ok: harness
HEAP = 0x24010000            # harness: voices, buffers, roots  # addr-ok: harness
# synth area (synth_shared.h, typed here independently): 32 KB below the Region
SYNTH_BYTES = 0x8000
SA = S.REGION - SYNTH_BYTES if HAVE else 0
M7_MAGIC, M4_MAGIC = 0x37534542, 0x34534542
SA_M7, SA_M4, SA_STATS, SA_TAB, SA_RUN = 0x0, 0x20, 0x40, 0x200, 0x400
AN_MAGIC = 0x4F4C4E41
CYCCNT = 0xE0001004
FRAMES = 128


def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]


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
    """One M7 with the built image; Analog state as the M4 would have published it."""
    def __init__(self, img=IMG):
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

    # ---- what the M4 publishes (m4_synth.c put)
    def table(self, line, handle, flags, glide=0):
        a = SA + SA_TAB + 32 * line
        s = (self.r32(a) + 2) & ~1
        self.u.mem_write(a, struct.pack("<8I", s, handle, flags, glide, 0, 0, 0, s))
        self.serial += 2
        self.w32(SA + SA_M4 + 12, self.serial)

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
        return root, v

    def note(self, v, note):
        nw = self.alloc(4); self.w32(nw, note)
        for o in (S.VOICE_WT1, S.VOICE_WT2):
            self.call(S.WT_NOTE_START, v + o, 0, nw)

    def buf(self, n, cap=None):
        b = self.alloc(0x20); d = self.alloc(4 * (cap or n))
        self.u.mem_write(b, struct.pack("<4I", cap or n, n, d, 0))
        return b, d

    def render(self, root, v, n=FRAMES, oscs=(0, 1)):
        """an_enter, then each oscillator's slot 0 through stock's vtable word (our trampoline). (samples, counts)"""
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
    def shape(self, v, i, s): self.wf(self.osc(v, i) + 0x2C, s)      # Position's base source (osc ctor: osc+0x28, amount 1)
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


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Hooks(unittest.TestCase):
    def test_stock_vtable_words_point_at_our_trampolines(self):
        w = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        self.assertEqual(w(S.WT_VOICE_VT_SLOT), DSP["tramp_voice"] | 1)
        self.assertEqual(w(S.WT_OSC_VT_SLOT), DSP["tramp_osc"] | 1)
        self.assertTrue(S.M7_DSP <= DSP["an_render"] < S.M7_DSP_LIMIT)
        r = Rig(); root, v = r.voice()
        self.assertEqual(r.r32(v + S.VOICE_WT1) & ~1, S.WT_OSC_VT_SLOT, "stock's ctor installs the class whose slot 0 we took")


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

    def test_a_root_stock_reuses_for_another_track_follows_its_new_handle(self):
        """Review B1: stock frees a root by setting its key to 0xFFFFFF00 (0x08059C28) and gives a new track the first
        free root with its handle (0x08059C66), with no table change on the M4. The new track has no Analog line."""
        r = Rig(); root, v = r.voice(0x10); r.table(0, 0x10, 1); r.note(v, 60)
        for _ in range(3):
            out, _ = r.render(root, v, oscs=(0,)); r.block()
        self.assertGreater(max(map(abs, out)), 0.1, "Analog while the root is handle 0x10's")
        r.w32(root + S.ROOT_HANDLE, 0xFFFFFF00); r.block()
        r.w32(root + S.ROOT_HANDLE, 0x20)
        r.track_pcs = True
        for _ in range(3):
            out, _ = r.render(root, v, oscs=(0,)); r.block()
        self.assertIn(S.WT_OSC_RENDER, r.pcs, "stock's render for the plain Wavetable track")
        self.assertEqual(out, [0.0] * FRAMES, "its (empty) table, not our saw")
        r.table(1, 0x20, 1)                                     # the M4 marks the new track Analog: it is
        r.pcs.clear()
        out, _ = r.render(root, v, oscs=(0,)); r.block(); out, _ = r.render(root, v, oscs=(0,)); r.block()
        self.assertNotIn(S.WT_OSC_RENDER, r.pcs)

    def test_a_resets_leftover_run_state_is_not_used(self):
        """Review N2: the header and the run state survive a reset in SDRAM. With the pool stock's again, an
        oscillator render must not take the last boot's voice pointer for its own."""
        r = Rig(); root, v = r.voice(0x21); r.table(0, 0x21, 1); r.note(v, 60)
        r.render(root, v, oscs=(0,)); r.block()
        r.w32(S.POOL_LEFT_RAM, 0x100000 + 0xC000)               # stock's startup restored the counters: not taken
        r.track_pcs = True
        osc = r.osc(v, 0); out, od = r.buf(FRAMES); scr, _ = r.buf(FRAMES)
        ret, _ = r.call(r.r32(r.r32(osc)) & ~1, osc, scr, out)  # slot 0 without an_enter first, as after a reset
        self.assertIn(S.WT_OSC_RENDER, r.pcs, "stock's render, not ours")

    def test_hard_sync_resets_the_slave_at_the_masters_wraps(self):
        """Sync on: WT2 (a fifth up) restarts at each of WT1's wraps, so the voice repeats at WT1's period."""
        r = Rig(); root, v = r.voice(0x66); r.table(0, 0x66, 3); r.note(v, 48)
        r.level(v, 0, 0.0)                                      # hear WT2 only; WT1 still tracks its phase (E3)
        w2 = r.osc(v, 1)
        r.w32(w2 + 0x0C, 7000)                                  # WT2 detune +7 semitones (1/1000 semitone units)
        xs, wraps = [], 0
        for _ in range(16):
            out, _ = r.render(root, v, oscs=(0, 1)); xs += out; r.block()
            wraps += sum(1 for e in r.state(v, 1)["events"] if e != 255)
        self.assertGreaterEqual(wraps, 4, "WT1 wrote its wraps into WT2's events (130.8 Hz: one per 367 frames)")
        p = 48000 / 130.81                                      # WT1: note 48
        lag = round(p)
        body = xs[512:]
        err = sum(abs(body[i] - body[i + lag]) for i in range(len(body) - lag)) / (len(body) - lag)
        self.assertLess(err, 0.05, "periodic at the master's period (a free fifth would not be)")
        r2 = Rig(); root2, v2 = r2.voice(0x67); r2.table(0, 0x67, 1); r2.note(v2, 48); r2.level(v2, 0, 0.0)
        r2.w32(r2.osc(v2, 1) + 0x0C, 7000)
        ys = []
        for _ in range(16):
            out, _ = r2.render(root2, v2, oscs=(0, 1)); ys += out; r2.block()
        body2 = ys[512:]
        err2 = sum(abs(body2[i] - body2[i + lag]) for i in range(len(body2) - lag)) / (len(body2) - lag)
        self.assertGreater(err2, 0.2, "control: without sync the fifth is not periodic at WT1's period")

    def test_long_blocks_render_without_sync_and_are_counted(self):
        r = Rig(); root, v = r.voice(0x44); r.table(0, 0x44, 3); r.note(v, 60)
        out, _ = r.render(root, v, n=256, oscs=(0, 1)); r.block()
        self.assertGreater(max(map(abs, out)), 0.1)
        self.assertEqual(r.r32(SA + SA_STATS + 24), 2, "sync_skipped: both oscillators of the 256-frame block")
        self.assertEqual(r.r32(SA + SA_STATS + 20), 256, "n_max")
        out, _ = r.render(root, v, n=64, oscs=(0, 1)); r.block()
        self.assertEqual(r.r32(SA + SA_STATS + 24), 2, "a 64-frame block syncs")

    def test_a_non_analog_oscillator_goes_to_stocks_render(self):
        r = Rig(); root, v = r.voice(0x33); r.note(v, 60)       # no table line: not Analog
        r.track_pcs = True
        out, _ = r.render(root, v, oscs=(0,))
        self.assertIn(S.WT_OSC_RENDER, r.pcs, "stock's render ran")
        self.assertEqual(out, [0.0] * FRAMES, "no table loaded: stock renders nothing")
        self.assertNotEqual(r.state(v, 0)["magic"], AN_MAGIC, "the kernel state is not ours")

    def test_area_not_ours_means_native(self):
        r = Rig(); root, v = r.voice(0x22); r.table(0, 0x22, 1); r.note(v, 60)
        r.w32(S.POOL_LEFT_RAM, 0x100000 + 0x4000)               # the pool does not end at our area: not taken
        r.track_pcs = True
        out, _ = r.render(root, v, oscs=(0,))
        self.assertIn(S.WT_OSC_RENDER, r.pcs)
        self.assertEqual(r.r32(SA + SA_RUN), 0, "nothing written into an area that is not ours")

    def test_root_cache_refills_only_when_the_m4_serial_moves(self):
        r = Rig(); a, va = r.voice(0x10); b, vb = r.voice(0x20)
        r.table(0, 0x10, 1); r.table(1, 0x20, 1); r.note(va, 60); r.note(vb, 64)
        for _ in range(4):
            r.render(a, va); r.render(b, vb); r.block()
        misses = r.r32(SA + SA_STATS + 52)
        self.assertEqual(misses, 2, "one refill per root (upstream's one-entry cache missed on every lookup)")
        r.table(1, 0x20, 0)
        out, _ = r.render(b, vb); r.block()
        self.assertEqual(r.r32(SA + SA_STATS + 52), 3)
        self.assertEqual(r.r32(SA + 0x1C), 1, "handle check: root+0x0C matched a table line")


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

    def test_nan_and_range_guards(self):
        for inc, shape in ((float("nan"), 0.5), (0.01, float("nan")), (-1.0, 0.3), (5.0, 2.0)):
            with self.subTest(inc=inc, shape=shape):
                xs, p, _, _ = self.kernel(inc, shape, 64)
                self.assertTrue(all(math.isfinite(x) for x in xs) and math.isfinite(p))
                want, _, _, _ = model(inc, shape, 64)
                self.close(xs, want)


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
        r.table(0, 0x4242, 0)
        _, _, native = self.render_voice(v); r.block()
        r.table(0, 0x4242, 1)
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

    def cost(self, shape, flags=1, n=FRAMES):
        r = Rig(); root, v = r.voice(0x31); r.table(0, 0x31, flags); r.note(v, 60)
        r.shape(v, 0, shape); r.shape(v, 1, shape)
        totals = []
        for _ in range(6):
            _, c = r.render(root, v, n, (0, 1)); r.block(); totals.append(sum(c))
        return sorted(totals)[len(totals) // 2], r

    def test_cost_per_regime(self):
        rows = []
        for name, shape, flags in (("saw", 0.0, 1), ("mixed", 0.25, 1), ("square", 0.5, 1), ("pulse", 1.0, 1),
                                   ("saw+sync", 0.0, 3), ("mixed+sync", 0.25, 3)):
            c, r = self.cost(shape, flags)
            rows.append((name, c))
            self.assertLess(c, self.BOUND, name)
        print("\n  hooks per voice-block (instructions, n=128): " + ", ".join(f"{n} {c}" for n, c in rows), file=sys.stderr)
        st = struct.unpack("<16I", r.u.mem_read(SA + SA_STATS, 64))
        self.assertGreater(st[0], 0, "blocks counted")
        self.assertEqual(st[3], 2, "osc_peak: two oscillators per block")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
