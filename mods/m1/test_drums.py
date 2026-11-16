# SPDX-License-Identifier: GPL-3.0-or-later
"""Drum synths on one-shot pads, M7 side (S6c: the Kick), on stock 1.6.8's own one-shot engine.

    python test_drums.py <stock bento1.bin> <m1 build dir>

The built image runs in Unicorn (Cortex-M7 with its FPU). REAL (stock code from the image, at the table's addresses):
the one-shot engine's voice pool constructor (DRUM_POOL_CTOR, its 32 voices and their constructors), the pad root
constructor (DRUM_ROOT_CTOR), VOICE_START (voice choice, the voice reset, the sample lookup through the root's bank,
DESC_OPEN, the start serial, the start position), the per-voice render entry DRUM_RENDER (our D1 trampoline at its
first word, then stock's render body whenever we hand back), stock's post chain DRUM_POST (filter, amp envelope,
level, mix) and every helper they call. OURS: the D1 trampoline and the DSP ELF at their placement. HARNESS: the sample
bank's lookup (SAMPLE_FIND answers from a dict), a sample provider whose vtable slots are answered here (frames,
rate, channels, the sample read), a bump heap, and Unicorn's missing double-precision unit (F64, as test_analog).

What the review of the first S6c tree (rc25) found, and what each class shows:
  B1  the carrier test ran inside the voice starts before stock had looked the sample up: Starts runs VOICE_START and
      the first render, a voice reused for another pad, a sample swapped under a pad, and a sample voice reused by a
      carrier pad; every non-synth case is bit-identical to the stock image.
  B2  no suite ran the drum code: every class here runs stock's whole path; Golden compares what we hand stock's post
      chain with a host build of the vendored DaisySP (dsp/host/kick_ref.cpp: host compiler, host libm, no code of
      ours), Parity covers the section 9 rows that have an offline column, Cost measures instructions per voice-block.
  S1  knob values only once the M4 has published (its drum serial): Knobs. S3 the scratch capacity: Buffers.
  S4  the render's ownership check (root and start serial): Starts.
  D8  only one-shot pad roots: Gate.
Skipped on a build without the drums group (1.6.1).
"""
import hashlib, json, math, os, re, shutil, struct, subprocess, sys, tempfile, unittest
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
HERE = Path(__file__).resolve().parent
BUILD = Path(sys.argv[2])
STOCK = Path(sys.argv[1]).read_bytes()
S = from_build(BUILD, STOCK)
MAN = json.loads((BUILD / "manifest.json").read_text())
IMG = (BUILD / "bento1.bin").read_bytes()
del sys.argv[1:3]
HAVE = "drums" not in MAN.get("omitted", []) and "dr_render" in MAN.get("dsp_symbols", {})
ON = HAVE and "drums" not in MAN.get("disabled", [])       # D1 patched (BE_DISABLE=drums leaves it stock)
DSP = {k: int(v, 16) for k, v in MAN.get("dsp_symbols", {}).items()}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack (DTCM top)  # addr-ok: harness
STUBS = 0x24070000           # harness: the provider's method stubs (executable AXI SRAM)  # addr-ok: harness
HEAP = 0xC1000000            # harness heap: pool, roots, buffers, bank, provider  # addr-ok: harness
CYCCNT = 0xE0001004
FRAMES = 128
# synth area (synth_shared.h, typed here independently): 32 KB below the Region
SA = S.REGION - 0x8000 if HAVE else 0
M7_MAGIC, M4_MAGIC = 0x37534542, 0x34534542
SA_M4, SA_DSTATS, SA_DKTAB, SA_DCELLS, SA_DRUN = 0x20, 0x80, 0x1000, 0x2400, 0x4400
DSTAT = ("blocks", "sum16", "peak", "cells_peak", "renders", "starts", "retrigs", "detects", "carriers", "unbuilt",
         "quiet_ends", "cap_ends", "knob_reads", "n_max", "unbound", "short_bufs", "hist5", "hist15", "hist30")
# offsets in stock's one-shot objects, typed from the disassembly (facts.py / expected_values.py hold the reviewed ones)
ROOT_BANK, ROOT_POOL, ROOT_ON, ROOT_SAMPLE, ROOT_POLY, ROOT_LOOP, ROOT_REVERSE = 0x14, 0x18, 0x1D, 0x674, 0xF4, 0xEC, 0xF0
VOICE_SERIAL, VOICE_CURSOR, VOICE_FORWARD, VOICE_ENV = 0x08, 0x40, 0x54, 0x5C
VOICE_START_MOD, VOICE_LENGTH_MOD = 0x2B0, 0x2F0           # the render's other start / length paths (review S6)
VOICE_LENGTH_PARAM = 0x240      # the pad's Length parameter (0x08092F40: add.w r0,r5,#0x240; bl PARAM_VALUE; x frames)
# the carrier format (docs/plan-synths-168.md section 8, tools/carriers/gen_carriers.py), typed here
SIGNATURE, CARRIER_FRAMES, PG_FRAMES = 0xB1E70A5C, 4800, 144018
LSB = 1 / 32768
KICK, SNARE = 1, 3
KICK_DEFAULTS = (55, 65, 45, 65, 55, 25, 25)               # Tune Hz, Decay, Tone, Accent, Sweep, Sweep Decay, Dirt
TOL = 1e-4                   # ours against the host build: measured 1.2e-7 over 24 blocks (FMA and libm differences)


def f32(x): return struct.unpack("<f", struct.pack("<f", x))[0]


def carrier(eid=KICK, word=None, frames=None, pg=False):
    """A carrier's samples as stock's reader returns them (16-bit: +-1 LSB)."""
    s = [LSB if (SIGNATURE >> i) & 1 else -LSB for i in range(32)]
    if not pg:
        w = (0x42450100 | eid) if word is None else word
        s += [LSB if (w >> i) & 1 else -LSB for i in range(32)]
    n = frames or ((PG_FRAMES if pg else CARRIER_FRAMES) + eid)
    return s + [0.0] * (n - len(s))


def tone(n=9000, a=0.25):
    return [a * math.sin(2 * math.pi * 220 * i / 48000) for i in range(n)]


class F64:
    """The double-precision instructions stock's code runs (Unicorn's M7 has no f64): decoded once, hooked one by one."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    md.skipdata = True

    def __init__(self, u, img):
        self.ops = {}
        code = img[:S.M7_DSP - BASE]                  # stock's M7 code (its double-precision instructions)
        for i in self.md.disasm(code, BASE):
            if ".f64" in i.mnemonic or (i.mnemonic == "vmov" and re.match(r"r\w+, r\w+, d\d+$", i.op_str)):
                self.ops[i.address] = (i.mnemonic, [x.strip() for x in i.op_str.split(",")], i.size)
                u.hook_add(UC_HOOK_CODE, self.step, begin=i.address, end=i.address)

    @staticmethod
    def reg(name): return getattr(sys.modules["unicorn.arm_const"], f"UC_ARM_REG_{name.upper()}")
    def getd(self, u, n): return struct.unpack("<d", struct.pack("<Q", u.reg_read(self.reg(f"d{n}")) & (2 ** 64 - 1)))[0]
    def setd(self, u, n, v): u.reg_write(self.reg(f"d{n}"), struct.unpack("<Q", struct.pack("<d", v))[0])
    def gets(self, u, r): return struct.unpack("<f", struct.pack("<I", u.reg_read(self.reg(r)) & 0xFFFFFFFF))[0]

    def step(self, u, a, size, _):
        mn, args, n = self.ops[a]
        d = lambda x: int(x[1:])
        if mn == "vmov.f64" and args[1].startswith("#"): self.setd(u, d(args[0]), float(args[1][1:]))
        elif mn == "vmov.f64": self.setd(u, d(args[0]), self.getd(u, d(args[1])))
        elif mn == "vcvt.f64.f32": self.setd(u, d(args[0]), self.gets(u, args[1]))
        elif mn == "vcvt.f64.s32":
            v = u.reg_read(self.reg(args[1])) & 0xFFFFFFFF
            self.setd(u, d(args[0]), float(v - (1 << 32) if v >> 31 else v))
        elif mn == "vcvt.f32.f64": u.reg_write(self.reg(args[0]), struct.unpack("<I", struct.pack("<f", self.getd(u, d(args[1]))))[0])
        elif mn == "vcvt.s32.f64":
            x = self.getd(u, d(args[1]))
            v = 0 if x != x else max(-2 ** 31, min(2 ** 31 - 1, int(x)))
            u.reg_write(self.reg(args[0]), v & 0xFFFFFFFF)
        elif mn in ("vadd.f64", "vsub.f64", "vmul.f64", "vdiv.f64"):
            x, y = self.getd(u, d(args[1])), self.getd(u, d(args[2]))
            r = {"vadd.f64": lambda: x + y, "vsub.f64": lambda: x - y, "vmul.f64": lambda: x * y,
                 "vdiv.f64": lambda: x / y if y else math.copysign(math.inf, x) if x else math.nan}[mn]()
            self.setd(u, d(args[0]), r)
        elif mn in ("vfma.f64", "vfms.f64"):
            acc, x, y = self.getd(u, d(args[0])), self.getd(u, d(args[1])), self.getd(u, d(args[2]))
            self.setd(u, d(args[0]), acc + x * y if mn == "vfma.f64" else acc - x * y)
        elif mn in ("vcmp.f64", "vcmpe.f64"):
            x = self.getd(u, d(args[0])); y = 0.0 if args[1].startswith("#") else self.getd(u, d(args[1]))
            nzcv = 0b0011 if (x != x or y != y) else 0b1000 if x < y else 0b0110 if x == y else 0b0010
            u.reg_write(UC_ARM_REG_FPSCR, (u.reg_read(UC_ARM_REG_FPSCR) & 0x0FFFFFFF) | nzcv << 28)
        elif mn == "vselgt.f64":
            f = u.reg_read(UC_ARM_REG_APSR) >> 28
            gt = not (f & 4) and bool(f >> 3) == bool(f & 1)
            self.setd(u, d(args[0]), self.getd(u, d(args[1] if gt else args[2])))
        elif mn == "vmov":
            q = struct.unpack("<Q", struct.pack("<d", self.getd(u, d(args[2]))))[0]
            u.reg_write(self.reg(args[0]), q & 0xFFFFFFFF); u.reg_write(self.reg(args[1]), q >> 32)
        else:
            raise RuntimeError(f"F64: {mn} {args} at {a:#x} is not emulated")
        u.reg_write(UC_ARM_REG_PC, (a + n) | 1)


class Rig:
    """One M7 with an image (the build, or stock's for comparisons): stock's one-shot engine around one sample bank."""
    def __init__(self, img=IMG, count=False):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, img)
        # ITCM reads as zeros (stock's LFO constructor reads [0 + 8] before the LFO is bound: 0x08064FF8)
        for a, n in ((0x0, 0x10000), (0x20000000, 0x20000), (0x24000000, 0x80000), (0x10000000, 0x50000),  # addr-ok: harness
                     (0x30000000, 0x48000), (0xC1000000, 0x400000), (0xC5F00000, 0x100000), (0xE0000000, 0x100000)):  # addr-ok: harness
            u.mem_map(a, n)
        u.mem_write(SENT, b"\xfe\xe7")
        self.f64 = F64(u, img)
        self.n = 0
        self.min_sp = STACK
        self.heap = HEAP
        self.samples = {}                       # sample id -> (samples, rate, channels)
        self.posted = []                        # what stock's post chain was handed: (voice, the temp buffer's samples)
        self.reads = 0
        self.stub_at = STUBS
        u.hook_add(UC_HOOK_MEM_READ, self.cyc, begin=CYCCNT, end=CYCCNT + 3)
        u.hook_add(UC_HOOK_CODE, self.post, begin=S.DRUM_POST, end=S.DRUM_POST)
        self.stub(S.SAMPLE_FIND, self.find)
        # the synth area is ours: the pool ends at it, the M7 header is stamped (gen 1)
        self.w32(S.POOL_CUR_RAM, SA - 0x100000); self.w32(S.POOL_LEFT_RAM, 0x100000)
        u.mem_write(SA, struct.pack("<4I", M7_MAGIC, 1, 2, 0xC000))
        self.idx = 1
        self.w32(S.LOAD_RING + S.LOAD_RING_IDX, self.idx)
        # a sample provider (stock's sample memory, as the bank's +4 names it) whose methods are answered here
        self.prov = self.alloc(0x40); vt = self.alloc(0x80)
        self.w32(self.prov, vt)
        for slot in range(32):
            self.w32(vt + 4 * slot, self.stub(None, {2: self.read, 6: self.info, 7: self.rate, 9: self.channels}.get(slot, self.zero)) | 1)
        self.bank = self.alloc(8); self.w32(self.bank, self.alloc(0x40)); self.w32(self.bank + 4, self.prov)
        self.pool = self.alloc(0xC720)
        self.call(S.DRUM_POOL_CTOR, self.pool)
        self.clock = self.alloc(0x40)
        if count:
            u.hook_add(UC_HOOK_CODE, self.tick)

    # ---- memory
    def w32(self, a, v): self.u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
    def r32(self, a): return struct.unpack("<I", self.u.mem_read(a, 4))[0]
    def r8(self, a): return self.u.mem_read(a, 1)[0]
    def w8(self, a, v): self.u.mem_write(a, bytes([v]))
    def floats(self, a, n): return list(struct.unpack(f"<{n}f", self.u.mem_read(a, 4 * n)))
    def alloc(self, n):
        p = self.heap; self.heap += (n + 31) & ~31
        self.u.mem_write(p, bytes(n))
        return p

    # ---- harness hooks
    def tick(self, u, a, size, _):
        self.n += 1
        sp = u.reg_read(UC_ARM_REG_SP)
        if sp < self.min_sp: self.min_sp = sp
    def cyc(self, u, access, a, size, value, _): u.mem_write(CYCCNT, struct.pack("<I", self.n & 0xFFFFFFFF))

    def stub(self, at, fn):
        """A function answered in Python: at its address (stock's), or at a new stub (at None)."""
        if at is None:
            at = self.stub_at; self.stub_at += 4
            self.u.mem_write(at, b"\x70\x47\x00\xbf")
        def run(u, a, size, _):
            fn(u)
            u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, run, begin=at, end=at)
        return at

    def zero(self, u): u.reg_write(UC_ARM_REG_R0, 0)
    def find(self, u):
        """SAMPLE_FIND(bank object, id, info out): the 0x24-byte info stock copies into the descriptor's +4."""
        sid, out = u.reg_read(UC_ARM_REG_R1), u.reg_read(UC_ARM_REG_R2)
        if sid not in self.samples:
            u.reg_write(UC_ARM_REG_R0, 0); return
        n = len(self.samples[sid][0])
        u.mem_write(out, struct.pack("<9I", sid, 0, 0, 0, 0, n, 0, 0, 0))   # file, ..., start +0x10, end +0x14
        u.reg_write(UC_ARM_REG_R0, 1)
    def info(self, u):                         # vt+0x18 (out, provider, file): 64-bit frame count
        out, file = u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R2)
        u.mem_write(out, struct.pack("<II", len(self.samples.get(file, ((),))[0]), 0))
    def rate(self, u): u.reg_write(UC_ARM_REG_R0, self.samples.get(u.reg_read(UC_ARM_REG_R1), (0, 48000))[1])
    def channels(self, u): u.reg_write(UC_ARM_REG_R0, self.samples.get(u.reg_read(UC_ARM_REG_R1), (0, 0, 1))[2])
    def read(self, u):
        """vt+8 (provider, -, position 64-bit, [sp] file, [sp+4] left, [sp+8] right, [sp+0xC] count): floats."""
        sp = u.reg_read(UC_ARM_REG_SP)
        pos = u.reg_read(UC_ARM_REG_R2) | (u.reg_read(UC_ARM_REG_R3) << 32)
        pos -= (1 << 64) if pos >> 63 else 0
        file, left, right, cnt = struct.unpack("<4I", u.mem_read(sp, 16))
        xs = self.samples.get(file, ((),))[0]
        self.reads += 1
        for b in (left, right):
            if b:
                u.mem_write(b, struct.pack(f"<{cnt}f", *[xs[pos + i] if 0 <= pos + i < len(xs) else 0.0 for i in range(cnt)]))
    def post(self, u, a, size, _):
        temp = u.reg_read(UC_ARM_REG_R2)
        n, data = self.r32(temp + 4), self.r32(temp + 8)
        self.posted.append((u.reg_read(UC_ARM_REG_R1), self.floats(data, n)))

    def call(self, fn, *args, sargs=()):
        u = self.u
        for i, v in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, v & 0xFFFFFFFF)
        sp = STACK - 0x40
        u.mem_write(sp, b"".join(struct.pack("<I", v & 0xFFFFFFFF) for v in sargs) + bytes(0x40 - 4 * len(sargs)))
        u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        n0 = self.n
        try:
            u.emu_start(fn | 1, SENT, count=50_000_000)
        except UcError as e:
            pc = u.reg_read(UC_ARM_REG_PC)
            raise AssertionError(f"{e} at pc {pc:#x} lr {u.reg_read(UC_ARM_REG_LR):#x}")
        if u.reg_read(UC_ARM_REG_SP) != sp:
            raise AssertionError(f"stack pointer moved over a call of {fn:#x}: {sp:#x} -> {u.reg_read(UC_ARM_REG_SP):#x}")
        return u.reg_read(UC_ARM_REG_R0), self.n - n0

    # ---- stock's one-shot engine
    def sample(self, sid, xs, rate=48000, channels=1):
        self.samples[sid] = (xs, rate, channels)
        return sid

    def pad(self, sid, handle=0x1234, poly=4, **fields):
        """A pad's root, as stock builds it at boot (ctor; bank, pool), with its sample (+0x674) and fields set."""
        root = self.alloc(0x678)
        self.call(S.DRUM_ROOT_CTOR, root)
        self.w32(root + ROOT_BANK, self.bank); self.w32(root + ROOT_POOL, self.pool); self.w32(root + S.ROOT_HANDLE, handle)
        self.w8(root + ROOT_ON, 1); self.w32(root + ROOT_SAMPLE, sid); self.w32(root + ROOT_POLY, poly)
        for k, v in fields.items():
            off = {"loop": ROOT_LOOP, "reverse": ROOT_REVERSE, "fine": S.ROOT_FINE}[k]
            (self.w8 if k == "reverse" else self.w32)(root + off, v)
        return root

    def voices(self, root): return [self.r32(root + S.ROOT_VOICES + 4 * i) for i in range(self.r32(root + S.ROOT_VOICE_N))]

    def start(self, root, note=60, vel=100, length=1.0):
        """VOICE_START (root, clock, -, note, [sp] velocity, 0, 0): the voice it started (the newest serial). Then the
        pad's Length, as the M4's properties would have set it (a fresh root's Length parameter has no source, so it
        evaluates to 0 and stock's render releases a sample at once): one source of that value, amount 1."""
        self.call(S.VOICE_START, root, self.clock, 0, note, sargs=(vel, 0, 0))
        v = max(self.voices(root), key=lambda v: self.r32(v + VOICE_SERIAL))
        if length is not None:
            p, src = v + VOICE_LENGTH_PARAM, self.alloc(8)
            self.u.mem_write(src + 4, struct.pack("<f", length))
            self.w32(p, src); self.u.mem_write(p + 4, struct.pack("<f", 1.0)); self.w8(p + 0x28, 0); self.w32(p + 0x30, 1)
        return v

    def buf(self, n, cap=None, guard=0):
        b = self.alloc(0x20); d = self.alloc(4 * ((cap or n) + guard))
        self.u.mem_write(b, struct.pack("<4I", cap or n, n, d, 0))
        if guard:
            self.u.mem_write(d + 4 * (cap or n), struct.pack(f"<{guard}I", *[0xA5A5A5A5] * guard))
        return b, d

    def render(self, root, v, n=FRAMES, cap=None):
        """The render case's call of DRUM_RENDER(root, voice, r2, r3, [sp] temp, [sp+4] out) for an active voice:
        (out samples, what the post chain got or None, the call's instructions)."""
        a, _ = self.buf(n); b, _ = self.buf(n); temp, _ = self.buf(n, cap); out, od = self.buf(n)
        self.posted.clear()
        _, c = self.call(S.DRUM_RENDER, root, v, a, b, sargs=(temp, out))
        got = [p for vv, p in self.posted if vv == v]
        return self.floats(od, n), (got[-1] if got else None), c

    def block(self):
        self.idx += 1
        self.w32(S.LOAD_RING + S.LOAD_RING_IDX, self.idx)

    def play(self, root, blocks, voices=None):
        """blocks of the render case: every active voice of the root (or the given ones), then the next block.
        {voice: (outs, posted)} concatenated."""
        res = {}
        for _ in range(blocks):
            for v in (voices or self.voices(root)):
                if not self.r8(v):
                    continue
                out, post, _ = self.render(root, v)
                o, p = res.setdefault(v, ([], []))
                o += out; p += post or [None] * FRAMES
            self.block()
        return res

    def stats(self):
        return dict(zip(DSTAT, struct.unpack("<19I", self.u.mem_read(SA + SA_DSTATS, 76))))

    def knobs(self, line, handle, values, engine=0, serial=2, head=None):
        """What the M4 will publish (S6c-2): a knob line and its header's drum serial."""
        v = list(values) + [0] * (8 - len(values))
        h = serial if head is None else head
        self.u.mem_write(SA + SA_DKTAB + 32 * line, struct.pack("<8I", h, handle, 0x100 | engine,
                                                                 int.from_bytes(bytes(v[:4]), "little"),
                                                                 int.from_bytes(bytes(v[4:]), "little"), 0, 0, h))
        self.u.mem_write(SA + SA_M4, struct.pack("<5I", M4_MAGIC, 1, 1, 2, serial))


# ---- the host reference (dsp/host/kick_ref.cpp), built once per run
class Ref:
    path = None

    @classmethod
    def build(cls):
        if cls.path:
            return cls.path
        d = Path(tempfile.mkdtemp(prefix="kick_ref_"))
        src = HERE / "dsp"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-O1", "-ffp-contract=off", "-fno-fast-math", "-std=c++17",
               f"-I{src / 'daisysp/Source'}", f"-I{src / 'daisysp/Source/Utility'}", str(src / "host/kick_ref.cpp"),
               str(src / "daisysp/Source/Drums/synthbassdrum.cpp"), str(src / "daisysp/Source/Filters/svf.cpp"), "-o", str(d / "kick_ref")]
        subprocess.run(cmd, check=True, capture_output=True)
        cls.path = d / "kick_ref"
        return cls.path

    @classmethod
    def kick(cls, seed, blocks, knobs=KICK_DEFAULTS, ratios=(1.0,), n=FRAMES):
        out = subprocess.run([str(cls.build()), str(seed), str(blocks), str(n), *map(str, knobs),
                              *[repr(f32(r)) for r in ratios]], check=True, capture_output=True).stdout
        return list(struct.unpack(f"<{blocks * n}f", out))


def seed(start_no):
    """The noise seed of the start_no-th drum start since the area was taken (drums.cpp start(): the plan's
    per-voice xorshift32, seeded from the start count)."""
    s = (0x51B37A29 ^ (start_no * 0x9E3779B9)) & 0xFFFFFFFF
    return s or 1


def close(tc, got, want, tol, what=""):
    tc.assertEqual(len(got), len(want), what)
    worst = max(abs(a - b) for a, b in zip(got, want))
    tc.assertLess(worst, tol, f"{what}: worst difference {worst:g}")
    return worst


@unittest.skipUnless(HAVE, "no drums group in this image (1.6.1, or left out)")
class Sites(unittest.TestCase):
    def test_render_entry_is_our_trampoline_and_the_voice_starts_are_stock(self):
        w = lambda img, a: bytes(img[a - BASE:a - BASE + 4])
        if ON:
            self.assertNotEqual(w(IMG, S.DRUM_RENDER), w(STOCK, S.DRUM_RENDER))
            self.assertEqual(w(STOCK, S.DRUM_RENDER), bytes.fromhex("2de9f04f"))
        else:
            self.assertEqual(w(IMG, S.DRUM_RENDER), w(STOCK, S.DRUM_RENDER), "BE_DISABLE=drums: D1 stock")
        for site in ("DRUM_VSTART_A_SITE", "DRUM_VSTART_B_SITE"):   # review B1: the voice starts are not patched
            self.assertEqual(w(IMG, getattr(S, site)), w(STOCK, getattr(S, site)), site)
        self.assertTrue(S.M7_DSP <= DSP["dr_render"] < S.M7_DSP_LIMIT)

    def test_disabled_build_plays_the_carrier_as_stock(self):
        if ON:
            self.skipTest("drums on in this image")
        r, s = Rig(), Rig(STOCK)
        for g in (r, s):
            g.root = g.pad(g.sample(7, carrier()))
            g.res = g.play(g.root, 4, [g.start(g.root)])
        self.assertEqual(list(r.res.values())[0][0], list(s.res.values())[0][0])


def run_both(scenario):
    """The same scenario on the built image and on stock's: their outputs, voice by voice in start order."""
    outs = []
    for img in (IMG, STOCK):
        g = Rig(img)
        outs.append(scenario(g))
    return outs


@unittest.skipUnless(ON, "drums not on in this image")
class Starts(unittest.TestCase):
    """B1: what a start is, seen from the render (the descriptor is set and open by then)."""

    def test_first_use_of_a_voice_plays_the_kick(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()))
        v = g.start(root)
        out, post, _ = g.render(root, v)
        self.assertIsNotNone(post, "we rendered and called the post chain")
        self.assertGreater(max(map(abs, post)), 0.05, "the Kick, not the near-silent carrier")
        self.assertGreater(max(map(abs, out)), 0.01)
        st = g.stats()
        self.assertEqual((st["detects"], st["carriers"], st["starts"]), (1, 1, 1))

    def test_a_voice_reused_by_a_sample_pad_plays_the_sample_as_stock(self):
        """The rc25 failure: a voice that last played a carrier on pad A, handed by the pool to pad B (a sample)."""
        def scenario(g):
            a = g.pad(g.sample(7, carrier()), handle=0x10)
            b = g.pad(g.sample(9, tone()), handle=0x20)
            v = g.start(a); g.play(a, 3, [v])
            g.w8(v, 0)                                       # pad A's voice ended (inactive: back to the pool's free use)
            w = g.start(b)
            res = g.play(b, 4, [w])
            return w, res[w][0]
        (w1, o1), (w2, o2) = run_both(scenario)
        self.assertEqual(o1, o2, "pad B's sample, bit for bit as stock plays it")
        self.assertGreater(max(map(abs, o1)), 0.05)

    def test_a_sample_voice_reused_by_a_carrier_pad_plays_the_kick(self):
        g = Rig()
        b = g.pad(g.sample(9, tone()), handle=0x20)
        a = g.pad(g.sample(7, carrier()), handle=0x10)
        v = g.start(b)
        res = g.play(b, 3, [v])
        self.assertEqual(g.stats()["starts"], 0)
        g.w8(v, 0)
        w = g.start(a)
        _, post, _ = g.render(a, w)
        self.assertIsNotNone(post)
        self.assertGreater(max(map(abs, post)), 0.05)

    def test_same_voice_restarted_after_its_end_retriggers_from_the_start(self):
        """A voice that ended (inactive) is the next start's voice on that pad (VOICE_START reuses the root's inactive
        voice): a new serial, so the synth starts over, and the output matches a fresh start's."""
        g = Rig()
        root = g.pad(g.sample(7, carrier()))
        v = g.start(root)
        first = g.play(root, 3, [v])[v][1]
        g.w8(v, 0)
        w = g.start(root)
        self.assertEqual(w, v, "stock reused the root's inactive voice")
        second = g.play(root, 3, [w])[w][1]
        want = Ref.kick(seed(2), 3)
        close(self, second, want, TOL, "the second start, from its own start")
        self.assertNotEqual(first, second, "its own noise seed")

    def test_two_voices_on_a_poly_pad_each_ring(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()), poly=4)
        v1 = g.start(root)
        g.play(root, 2, [v1])
        v2 = g.start(root)
        self.assertNotEqual(v1, v2)
        res = g.play(root, 3, [v1, v2])
        close(self, res[v2][1], Ref.kick(seed(2), 3), TOL, "the second voice from its start")
        close(self, res[v1][1], Ref.kick(seed(1), 5)[2 * FRAMES:], TOL, "the first keeps ringing, untouched")
        self.assertEqual(g.stats()["starts"], 2)

    def test_sample_swapped_under_a_pad_between_hits(self):
        def scenario(g):
            root = g.pad(g.sample(7, carrier()))
            v = g.start(root); g.play(root, 2, [v]); g.w8(v, 0)
            g.sample(9, tone()); g.w32(root + ROOT_SAMPLE, 9)        # the M4 loaded another sample onto the pad
            w = g.start(root)
            return g.play(root, 3, [w])[w][0]
        built, stock = run_both(scenario)
        self.assertEqual(built, stock)

    def test_the_pools_voice_moves_between_pads_without_carrying_the_synth(self):
        """Review S4: voices come from one pool of 32; a voice that played the Kick on pad A, ended, and is started on
        pad B with a sample, then on pad A again: sample (as stock), then the Kick from its start."""
        g = Rig()
        a = g.pad(g.sample(7, carrier()), handle=0x10)
        b = g.pad(g.sample(9, tone()), handle=0x20)
        v = g.start(a); g.play(a, 2, [v]); g.w8(v, 0)
        w = g.start(b)
        _, post, _ = g.render(b, w)
        self.assertEqual(g.stats()["starts"], 1, "no synth on pad B")
        g.block(); g.w8(w, 0)
        x = g.start(a)
        got = g.play(a, 2, [x])[x][1]
        close(self, got, Ref.kick(seed(2), 2), TOL, "pad A's Kick, started over")


@unittest.skipUnless(ON, "drums not on in this image")
class Golden(unittest.TestCase):
    """What we hand stock's post chain, against the host build of the vendored DaisySP (host libm)."""

    def test_default_kick_matches_the_host_build(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()))
        v = g.start(root)
        got = g.play(root, 24, [v])[v][1]
        want = Ref.kick(seed(1), 24)
        worst = close(self, got, want, TOL, "24 blocks")
        self.assertGreater(max(map(abs, want)), 0.3)
        print(f"\n  Golden: default Kick, 24 blocks, worst |ours - host| = {worst:.2e}", file=sys.stderr)

    def test_pitch_follows_stocks_ratio(self):
        """Note 72 on a pad whose root note is 60: stock's start sets the voice ratio 2.0, our block ratio is stock's
        formula, and the reference gets 2.0 per block; fine tune +500 (0.5 semitone) multiplies by 2^(0.5/12)."""
        g = Rig()
        root = g.pad(g.sample(7, carrier()), fine=500)
        v = g.start(root, note=72)
        self.assertAlmostEqual(struct.unpack("<f", g.u.mem_read(v + S.VOICE_RATIO, 4))[0], 2.0, places=5)
        got = g.play(root, 6, [v])[v][1]
        close(self, got, Ref.kick(seed(1), 6, ratios=(2.0 * 2 ** (0.5 / 12),)), TOL)

    def test_velocity_never_reaches_the_dsp(self):
        """Row 3: what we hand the post chain is the same at velocity 30 and 127 (velocity reaches the sound only
        through the pad's routes in stock's post chain, D7), and the post chain scales the synth pad's output between the
        two velocities exactly as it scales a sample pad's (here, with no route: not at all)."""
        syn, smp = [], []
        for vel in (127, 30):
            g = Rig(); root = g.pad(g.sample(7, carrier())); v = g.start(root, vel=vel)
            syn.append(g.play(root, 4, [v])[v])
            g = Rig(); root = g.pad(g.sample(9, tone())); v = g.start(root, vel=vel)
            smp.append(g.play(root, 4, [v])[v][0])
        self.assertEqual(syn[0][1], syn[1][1], "the DSP's samples")
        gain = lambda a, b: max(map(abs, b)) / max(map(abs, a))
        self.assertAlmostEqual(gain(syn[0][0], syn[1][0]), gain(smp[0], smp[1]), delta=1e-6,
                               msg="the post chain treats both pads alike")


@unittest.skipUnless(ON, "drums not on in this image")
class Parity(unittest.TestCase):
    """Section 9 rows with an offline column: the voice end, Loop on, reverse and the other start / length paths."""

    def short_kick(self, g, **fields):
        root = g.pad(g.sample(7, carrier()), handle=0x55, **fields)
        g.knobs(0, 0x55, (55, 0, 45, 0, 55, 25, 25))         # Decay 0, Accent 0: a short Kick
        return root, g.start(root)

    def run_to_end(self, g, root, v, limit=600):
        """Blocks until the voice is inactive (stock's end): (blocks, the synth's done block, outs)."""
        outs, done = [], None
        for b in range(limit):
            if not g.r8(v):
                return b, done, outs
            out, post, _ = g.render(root, v)
            outs += out
            if done is None and g.stats()["quiet_ends"]:
                done = b
            g.block()
        return limit, done, outs

    def test_the_cursor_ends_the_voice(self):
        """Row 17: when the synth is done the cursor goes to the carrier's end; stock's render starts the release there
        and its end check ends the voice (voice[0] = 0). No click: the output after done is the release of silence."""
        g = Rig()
        root, v = self.short_kick(g)
        end, done, outs = self.run_to_end(g, root, v)
        self.assertIsNotNone(done, "the synth ended at -96 dB")
        self.assertLess(end, 600, "stock ended the voice")
        self.assertGreater(end, done)
        tail = outs[(done + 1) * FRAMES:]
        self.assertLess(max(map(abs, tail), default=0.0), 1e-3, "after the synth: the carrier's silence, released")
        self.assertEqual(g.stats()["knob_reads"], 1)

    def forward_release(self):
        g = Rig()
        root, v = self.short_kick(g)
        end, done, _ = self.run_to_end(g, root, v)
        return end - done

    def test_reverse_pad_ends_as_soon_as_a_forward_one(self):
        """Review S6: on a reverse pad stock's render counts down, so the done cursor is 0 (not frames, which would
        play the carrier backwards for 37 blocks before the release)."""
        g = Rig()
        root, v = self.short_kick(g, reverse=1)
        self.assertEqual(g.r8(v + VOICE_FORWARD), 0, "stock's start: a reverse pad plays backwards")
        end, done, outs = self.run_to_end(g, root, v)
        self.assertIsNotNone(done)
        self.assertLessEqual(abs((end - done) - self.forward_release()), 1, "the release starts at once")
        self.assertLess(max(map(abs, outs[(done + 1) * FRAMES:]), default=0.0), 1e-3)

    def test_start_and_length_paths_end_too(self):
        fwd = self.forward_release()
        for off in (VOICE_START_MOD, VOICE_LENGTH_MOD):
            with self.subTest(field=hex(off)):
                g = Rig()
                root, v = self.short_kick(g)
                g.w32(v + off, 1)                            # stock's render takes its other start / length path
                end, done, outs = self.run_to_end(g, root, v)
                self.assertIsNotNone(done)
                self.assertLessEqual(abs((end - done) - fwd), 1)
                self.assertLess(max(map(abs, outs[(done + 1) * FRAMES:]), default=0.0), 1e-3)

    def test_loop_on_keeps_the_voice_and_renders_zeros(self):
        """Row 16: with Loop on the voice stays (as a looping sample would) and the synth is silent once done."""
        g = Rig()
        root, v = self.short_kick(g, loop=1)
        end, done, outs = self.run_to_end(g, root, v, limit=300)
        self.assertEqual(end, 300, "the voice is still active")
        self.assertIsNotNone(done)
        _, post, _ = g.render(root, v)
        self.assertEqual(post, [0.0] * FRAMES, "zeros, through the post chain")
        self.assertEqual(g.r32(v + VOICE_CURSOR), CARRIER_FRAMES + KICK - 1, "inside the carrier")

    def test_lfo1_advances_as_in_stocks_render(self):
        """Row 14: per block we tick LFO1 once, as stock's render does first: after the same blocks the voice's LFO1
        equals the one of the same voice in the stock image, which plays the carrier natively."""
        def scenario(g):
            root = g.pad(g.sample(7, carrier())); v = g.start(root)
            g.play(root, 5, [v])
            return bytes(g.u.mem_read(v + S.VOICE_LFO1, 0x40))
        built, stock = run_both(scenario)
        self.assertEqual(built, stock)


@unittest.skipUnless(ON, "drums not on in this image")
class Detector(unittest.TestCase):
    """Row 20 and the carrier format: anything that is not exactly a carrier plays as stock plays it."""

    def same_as_stock(self, xs, rate=48000, channels=1):
        def scenario(g):
            root = g.pad(g.sample(7, xs, rate, channels)); v = g.start(root)
            return g.play(root, 3, [v])[v][0], g.stats() if g.u else None
        (b, st), (s, _) = run_both(scenario)
        self.assertEqual(b, s)
        return st

    def test_negatives(self):
        bad_sig = carrier(); bad_sig[5] = -bad_sig[5]
        for name, xs, kw in (("one sample off the signature", bad_sig, {}),
                             ("wrong word", carrier(word=0x42450103), {}),
                             ("a carrier-length plain sample", tone(CARRIER_FRAMES + KICK), {}),
                             ("one frame longer", carrier(frames=CARRIER_FRAMES + KICK + 1), {}),
                             ("44.1 kHz", carrier(), dict(rate=44100)),
                             ("stereo", carrier(), dict(channels=2))):
            with self.subTest(name):
                st = self.same_as_stock(xs, **kw)
                self.assertEqual(st["starts"], 0)

    def test_playgrounds_carrier_plays_the_kick(self):
        g = Rig(); root = g.pad(g.sample(7, carrier(pg=True))); v = g.start(root)
        _, post, _ = g.render(root, v)
        self.assertIsNotNone(post)
        self.assertGreater(max(map(abs, post)), 0.05)

    def test_an_engine_not_in_this_build_plays_natively_and_is_counted(self):
        st = self.same_as_stock(carrier(SNARE))
        self.assertEqual((st["carriers"], st["unbuilt"], st["starts"]), (1, 1, 0))

    def test_a_pads_second_voice_needs_no_second_test(self):
        g = Rig(); root = g.pad(g.sample(7, carrier()))
        g.render(root, g.start(root)); g.block()
        g.render(root, g.start(root))
        st = g.stats()
        self.assertEqual((st["detects"], st["starts"]), (1, 2), "the pad's answer is cached")


@unittest.skipUnless(ON, "drums not on in this image")
class Gate(unittest.TestCase):
    """D8: a root that is not of stock's one-shot pad class is never ours (D1 is only reached from that class's
    engine, so this is the belt; the class itself is facts.py DRUM_ROOT_VT's evidence)."""

    def test_other_root_class_plays_natively(self):
        def scenario(g):
            root = g.pad(g.sample(7, carrier())); v = g.start(root)
            g.w32(root, g.r32(root) + 0x100)                  # not S.DRUM_ROOT_VT
            return g.play(root, 3, [v])[v][0]
        built, stock = run_both(scenario)
        self.assertEqual(built, stock)

    def test_area_not_ours_plays_natively(self):
        def scenario(g):
            g.w32(S.POOL_LEFT_RAM, 0x100000 + 0x4000)           # the pool does not end at the synth area
            root = g.pad(g.sample(7, carrier())); v = g.start(root)
            return g.play(root, 3, [v])[v][0]
        built, stock = run_both(scenario)
        self.assertEqual(built, stock)


@unittest.skipUnless(ON, "drums not on in this image")
class Knobs(unittest.TestCase):
    """Review S1: the knob table is read only once the M4 has published (its drum serial); before that every pad
    plays the defaults, whatever SDRAM holds there."""

    def test_garbage_in_the_table_before_the_m4_published(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()), handle=0x77)
        g.knobs(0, 0x77, (180, 0, 100, 0, 0, 0, 100), serial=0)   # a well-formed line, but drum serial 0
        g.u.mem_write(SA + SA_M4, struct.pack("<5I", M4_MAGIC, 1, 1, 2, 0))
        v = g.start(root)
        got = g.play(root, 4, [v])[v][1]
        close(self, got, Ref.kick(seed(1), 4), TOL, "the defaults")
        self.assertEqual(g.stats()["knob_reads"], 0)

    def test_published_values_are_played(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()), handle=0x77)
        knobs = (40, 30, 80, 90, 20, 60, 0)
        g.knobs(3, 0x77, knobs, serial=4)
        v = g.start(root)
        got = g.play(root, 4, [v])[v][1]
        close(self, got, Ref.kick(seed(1), 4, knobs=knobs), TOL, "the pad's values")

    def test_a_torn_line_is_skipped(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier()), handle=0x77)
        g.knobs(0, 0x77, (180, 0, 100, 0, 0, 0, 100), serial=4, head=5)   # head odd: the M4 is writing it
        v = g.start(root)
        close(self, g.play(root, 2, [v])[v][1], Ref.kick(seed(1), 2), TOL, "the defaults")


@unittest.skipUnless(ON, "drums not on in this image")
class Buffers(unittest.TestCase):
    """Review S3: a block larger than the scratch buffer goes to stock's render (dr_render returns 0) and we write
    nothing past it. (Stock's own render has no such check: it writes the block into its scratch whatever its
    capacity, so its buffers always hold a block.)"""

    def test_scratch_smaller_than_the_block(self):
        g = Rig()
        root = g.pad(g.sample(7, carrier())); v = g.start(root)
        temp, td = g.buf(FRAMES, cap=64, guard=8); out, _ = g.buf(FRAMES)
        r, _ = g.call(DSP["dr_render"], root, v, temp, out)
        self.assertEqual(r, 0, "stock's render")
        self.assertEqual(struct.unpack("<8I", g.u.mem_read(td + 4 * 64, 32)), (0xA5A5A5A5,) * 8, "nothing past the buffer")
        self.assertEqual(g.stats()["short_bufs"], 1)
        self.assertEqual(g.posted, [], "no post chain from us")

    def test_64_and_256_frame_blocks(self):
        for n in (64, 256):
            with self.subTest(n=n):
                g = Rig(); root = g.pad(g.sample(7, carrier())); v = g.start(root)
                got = []
                for _ in range(3):
                    _, post, _ = g.render(root, v, n=n); got += post; g.block()
                close(self, got, Ref.kick(seed(1), 3, n=n), TOL)


@unittest.skipUnless(ON, "drums not on in this image")
class Cost(unittest.TestCase):
    """Instructions per voice-block (CYCCNT reads the instruction count here): our hook (what the drum stats line
    measures on the device), the whole voice with stock's post chain, and a sample voice for comparison."""
    HOOK_BOUND = 56000           # measured 52.6 k after review S5's powf memo (rc25 review: 59 k for the engine alone)

    def test_cost_per_voice_block(self):
        rows, depths = {}, {}
        for name, xs in (("kick", carrier()), ("sample", tone(20000))):
            g = Rig(count=True)
            root = g.pad(g.sample(7, xs)); v = g.start(root)
            whole = []
            g.min_sp = STACK
            for _ in range(12):
                self.assertTrue(g.r8(v), f"{name}: the voice still plays")
                _, _, c = g.render(root, v); whole.append(c); g.block()
            rows[name] = sorted(whole[1:])[len(whole[1:]) // 2]
            depths[name] = (STACK - 0x40) - g.min_sp           # below the render entry's caller (its two stack words)
            if name == "kick":
                st = g.stats()
                hook = (st["sum16"] << 4) // st["blocks"]

        frac = hook / 1280000
        print(f"\n  per voice-block (instructions, n={FRAMES}): our hook {hook} ({100 * frac:.2f} % of the 1.28 M-cycle "
              f"block at 1 CPI), whole Kick voice {rows['kick']}, whole sample voice {rows['sample']}, "
              f"extra {rows['kick'] - rows['sample']}; stack below the render entry: Kick {depths['kick']} B, sample {depths['sample']} B", file=sys.stderr)
        self.assertLess(hook, self.HOOK_BOUND, "our hook's cost per voice-block, upper bound")
        self.assertLess(rows["kick"] - rows["sample"], self.HOOK_BOUND)
        self.assertLess(depths["kick"], 640, "stack: trampoline + dr_render + the engine + stock's post chain")


@unittest.skipUnless(HAVE, "no drums group in this image")
class Math(unittest.TestCase):
    """Our libm stand-ins (dsp_lib.cpp) against Python's double-precision math, in the emulator."""

    def test_bounds(self):
        g = Rig()
        def fcall(fn, *xs):
            u = g.u
            for i, x in enumerate(xs):
                u.reg_write(getattr(sys.modules["unicorn.arm_const"], f"UC_ARM_REG_S{i}"), struct.unpack("<I", struct.pack("<f", x))[0])
            g.call(fn)
            return struct.unpack("<f", struct.pack("<I", u.reg_read(UC_ARM_REG_S0) & 0xFFFFFFFF))[0]
        worst = {"sin": 0.0, "pow2": 0.0, "pow": 0.0}
        for i in range(400):
            x = f32(-50 + 100 * i / 399)
            worst["sin"] = max(worst["sin"], abs(fcall(DSP["be_sinf"], x) - math.sin(x)))
            e = f32(-20 + 40 * i / 399)
            worst["pow2"] = max(worst["pow2"], abs(fcall(DSP["be_powf"], 2.0, e) / 2 ** e - 1))
            a = f32(0.01 + 0.98 * i / 399)
            worst["pow"] = max(worst["pow"], abs(fcall(DSP["be_powf"], a, f32(3.7)) / a ** f32(3.7) - 1))
        print(f"\n  libm stand-ins: {worst}", file=sys.stderr)
        self.assertLess(worst["sin"], 4e-6)
        self.assertLess(worst["pow2"], 1e-6)
        self.assertLess(worst["pow"], 2e-6)
        self.assertEqual(fcall(DSP["be_powf"], 2.0, 0.0), 1.0)


@unittest.skipUnless(ON, "drums not on in this image")
class Carriers(unittest.TestCase):
    """The kit's carrier (tools/carriers/gen_carriers.py, as package_m1.sh runs it) is what the detector accepts."""

    @classmethod
    def setUpClass(cls):
        d = Path(tempfile.mkdtemp(prefix="carriers_"))
        subprocess.run([sys.executable, "-I", str(HERE.parent.parent / "tools/carriers/gen_carriers.py"), str(d)],
                       check=True, capture_output=True)
        cls.files = {p.name: p.read_bytes() for p in d.iterdir()}
        shutil.rmtree(d)

    def test_the_kit_has_the_kick_only(self):
        self.assertEqual(sorted(self.files), ["BE Kick.wav"], "S6c builds the Kick: no carrier of an engine not built")

    def test_kick_wav_plays_the_kick(self):
        w = self.files["BE Kick.wav"]
        self.assertEqual((w[:4], w[8:16]), (b"RIFF", b"WAVEfmt "))
        fmt = struct.unpack_from("<HHIIHH", w, 20)
        self.assertEqual(fmt, (1, 1, 48000, 96000, 2, 16), "PCM, mono, 48 kHz, 16-bit")
        self.assertEqual(w[36:40], b"data")
        n = struct.unpack_from("<I", w, 40)[0] // 2
        self.assertEqual(n, CARRIER_FRAMES + KICK)
        self.assertLess(len(w), 10000)
        pcm = struct.unpack_from(f"<{n}h", w, 44)
        xs = [s / 32768 for s in pcm]                     # stock's 16-bit read (the detector needs only +-equal steps)
        self.assertEqual(xs, carrier(KICK), "the format test_drums builds its carriers in")
        g = Rig(); root = g.pad(g.sample(7, xs)); v = g.start(root)
        close(self, g.play(root, 2, [v])[v][1], Ref.kick(seed(1), 2), TOL, "the Kick")


@unittest.skipUnless(HAVE, "no drums group in this image")
class Vendored(unittest.TestCase):
    """The DaisySP subset is unmodified: DaisySP 2c72eaf9's files (hashes typed here; dsp/daisysp/README.md)."""
    WANT = {"LICENSE": "ae36576d7394109775a8cc86c31c94f016de256526e86d11ee554ef25d429570",
            "Source/Drums/synthbassdrum.cpp": "151345fed1088f8a6e5379522a62a6dfc9a43e29bb014c590bc8dea8f6a34a7d",
            "Source/Drums/synthbassdrum.h": "d0dcf35534bc0e7e132c6ac5c89a4c23a5cb4017952e1f1c4f3a528adc98d953",
            "Source/Filters/svf.cpp": "27ff7d834b9c6d705570bec43079c752028c15d4bfb00c97140a42508ec87ce0",
            "Source/Filters/svf.h": "97356a57d038421c5df461d22de2c2ad02b27e92067c65275791da4831620bca",
            "Source/Utility/dsp.h": "534f0f76b7794939a5fff9b128b8fddb23ae0e63ad3354f663445d417924099a"}

    def test_hashes(self):
        d = HERE / "dsp" / "daisysp"
        for f, h in self.WANT.items():
            with self.subTest(f):
                self.assertEqual(hashlib.sha256((d / f).read_bytes()).hexdigest(), h)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v", *sys.argv[1:]])
