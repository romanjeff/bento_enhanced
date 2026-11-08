# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for the M1 diagnostic (Unicorn 2.1.3, Lab's pinned version).

    python test_diag.py <CH10 bento1.bin> <m1diag build dir>

1. Trampoline equivalence: from each patch site to its resume address, every
   register, flag and memory byte matches CH10, except our log RAM.
2. Native-function equivalence: msg_send (with Lab's hook ahead of ours), the
   M4 MIDI ring post and the queue read leave identical queue memory on both
   images; our log gains exactly the expected records.
3. M4 end-to-end with FatFS stubbed: header, record lines, idle-gated flush,
   overflow accounting and reboot resynchronisation.
"""
import json, re, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
D2 = S.D2_SLICE                                                # our shared slice (per base)

BASE = 0x08040000
UWTICK = S.UWTICK       # stock M7 HAL tick (ms since boot)
SENT = 0x08000100           # return sentinel (never executed: emu stops on reaching it)
STACK = 0x2407F000  # addr-ok: harness
LOG_LO, LOG_HI = D2, D2 + 0xA00  # our reserved block (unlinked top of D2 SRAM2, v3.16): logger, monitors, strike line
M7H, M4H, RECS = D2 + 0x100, D2 + 0x120, D2 + 0x2C0
MAGIC = 0x31474144
RINGSTAT = D2 + 0x200
RTC_BKP = 0x58004050
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]

CH10 = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
DIAG = (OUT / "bento1.bin").read_bytes()
MAN = json.loads((OUT / "manifest.json").read_text())
FW = re.escape(MAN["display"])   # the label this build writes into its logs: BE3.72, BE4.00, BE4stl ...
OFF = set(MAN.get("disabled", []))   # feature groups whose stock sites this build leaves unpatched
OMITTED = set(MAN.get("omitted", []))   # S2: groups not even built into this image (not ported to its base yet)
SYM7 = {k: int(v, 16) for k, v in MAN["m7_symbols"].items()}
SYM4 = {k: int(v, 16) for k, v in MAN["m4_symbols"].items()}
M7_ONLY = "curve_emit" not in SYM7   # m7_polyat & co. not in this image (S2: every M7 group left out; S3 builds them)
# Our RAM on this base (build.py our_ram): the slice and the RAMCHK blocks, (name, lo, hi).
RAMCHK = ([("axi", S.RAMCHK_AXI, S.RAMCHK_AXI_END), ("d2a", S.RAMCHK_D2A, S.D2_SLICE), ("d2b", S.RAMCHK_D2B, S.D2_SLICE_END)]
          if S.has("RAMCHK_D2A") else
          [("axi", S.RAMCHK_AXI, S.RAMCHK_AXI_END), ("top", S.RAMCHK_D2B, S.D2_SLICE_END)])
OUR_RAM = [(D2, S.D2_SLICE_END)] + [(lo, hi) for _, lo, hi in RAMCHK]
M4_FROM = 0x08100000            # the M4 half of the main image: code there runs on the M4
SLICE_IN_AXI = 0x24000000 <= D2 < 0x24080000   # 1.6.8: the M4's MPU maps AXI as Device

# S2, alignment: the M4 reaches AXI (its MPU R0, 0x24040000 + 256 KB) through a Device mapping, where
# an unaligned access faults: on 1.6.8 our slice, on both bases the RAMCHK block above the M7 stack
# (test_slice M4Mpu runs stock's M4 MPU setup). Every emulator records any M4 access there that is
# not naturally aligned, and stops (as the fault would); call() turns it into a failure,
# tearDownModule catches the rest.
ALIGN_RANGES = [(lo, hi) for lo, hi in OUR_RAM if 0x24040000 <= lo < 0x24080000]  # addr-ok: stock's M4 MPU R0
ALIGN_FAULTS = []
def _align(uc, acc, a, size, v, _):
    pc = uc.reg_read(UC_ARM_REG_PC)
    if pc >= M4_FROM and a % size:
        ALIGN_FAULTS.append((pc, a, size))
        uc.emu_stop()

def emu(img):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")           # b .
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (S.PROBE_SCRATCH, 0x10000), (S.EXT_POOL_END, 0x200000), (0xE0000000, 0x100000),
                 (S.LOAD_RING & ~0x7FFF, 0x8000),    # SRAM3 through the alias stock's M7 uses (load ring)
                 (0x58000000, 0x10000),   # RTC (backup registers hold stock's crash record)
                 (0x40015000, 0x2000), (0x40020000, 0x1000)):  # SAI1-3, DMA2 (audio DMA monitor)
        u.mem_map(a, n)
    u.mem_write(S.POOL_LEFT_RAM, struct.pack("<2I", 3405824, S.EXT_POOL_END - 3405824))   # stock's ext pool: left, cursor
    for lo, hi in ALIGN_RANGES:
        u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, _align, begin=lo, end=hi - 1)
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

def call(u, fn, *args, limit=200000):
    for i, v in enumerate(args):
        u.reg_write(UC_ARM_REG_R0 + i, v)
    u.reg_write(UC_ARM_REG_SP, STACK)
    u.reg_write(UC_ARM_REG_LR, SENT | 1)
    n = len(ALIGN_FAULTS)
    u.emu_start(fn | 1, SENT, count=limit)
    if len(ALIGN_FAULTS) > n:
        pc, a, size = ALIGN_FAULTS[n]
        raise AssertionError(f"unaligned M4 access to our (Device-mapped) RAM: {size} B at {a:#x} from pc {pc:#x}")
    return u.reg_read(UC_ARM_REG_R0)

def tearDownModule():
    assert not ALIGN_FAULTS, f"unaligned M4 accesses to our RAM: {[(hex(p), hex(a), n) for p, a, n in ALIGN_FAULTS]}"

def m4_ready(u, gen=1):
    w32(u, M4H, MAGIC); w32(u, M4H + 4, 0); w32(u, M4H + 8, gen)

def records(u):
    wr, rd = r32(u, M7H + 4), r32(u, M4H + 4)
    out = []
    for i in range(rd, wr):
        b = u.mem_read(RECS + (i % 17) * 48, 48)
        out.append(struct.unpack("<12I", b))
    return out

class Image(unittest.TestCase):
    def test_size_within_what_the_bootloader_accepted(self):
        # The bootloader rejected a 0x1A0800-byte image reaching 0x081E0000 (an M7
        # read-only data region); Lab's CH10 at 0x18D334 bytes installs fine.
        self.assertLessEqual(len(DIAG), 0x18D334)
        self.assertLess(BASE + len(DIAG), 0x081E0000)
        self.assertEqual(len(DIAG) % 4, 0)
        self.assertEqual(DIAG[:len(CH10)][:0x100], CH10[:0x100], "stock vector table untouched")

    def test_visible_version_label(self):
        man = json.loads((OUT / "manifest.json").read_text())
        label = man["display"].encode()
        self.assertTrue(label.startswith(b"BE") and len(label) <= 6, label)
        self.assertEqual(DIAG[S.VERSION_STR - BASE:S.VERSION_STR - BASE + 8], label.ljust(8, bytes(1)),
                         "boot screen copies the first 6 bytes of this 8-byte slot")
        self.assertEqual(CH10[S.VERSION_STR - BASE:S.VERSION_STR - BASE + 8], S.version.encode().ljust(8, bytes(1)), "stock string")
        # S2: the version line comes from the base (build.py BASE_SETTINGS): 3.x on 1.6.1, 4.x on 1.6.8
        line = {"stock_161": "3.", "stock_168": "4."}[S.module]
        self.assertTrue(man["version"].startswith(line), man["version"])
        if label[2:3].isdigit():                          # not a BE_LABEL bisect label such as BEoff
            self.assertEqual(man["display"], "BE" + man["version"].split("-")[0])

    def test_m7_heap_is_stock(self):
        """v3.16: the M7 heap end is back to stock (v2.3-v3.15 lowered it by 2.4 KB;
        large projects like SPACED then failed to load)."""
        for a in S.M7_HEAP_END_LITS + S.M7_STACK_BOTTOM_LITS:   # heap literals, stack bottom / sentinels
            self.assertEqual(struct.unpack_from("<I", DIAG, a - BASE)[0], S.M7_HEAP_END)

class Trampolines(unittest.TestCase):
    HOOKS = [  # site, resume, r1 points at an event of this type
        (S.H_INPUT_SITE, S.H_INPUT_SITE + 4, 0xAC),
        (S.H_SEND_SITE, S.H_SEND_SITE + 4, 0x5B),
        (S.H_RING_SITE, S.H_RING_SITE + 4, 0x5B),
        (S.H_CONSUME_SITE, S.H_CONSUME_SITE + 4, None),
    ]

    def run_site(self, img, site, resume, ev_type, ready):
        u = emu(img)
        if ready:
            m4_ready(u)
        ev = 0x24070000  # addr-ok: harness
        u.mem_write(ev, bytes([ev_type or 0, 0, 0, 0]) + bytes(range(1, 21)))
        vals = [0x11111111 * (i + 1) & 0xFFFFFFFF for i in range(13)]
        vals[0], vals[1], vals[2] = 0x24071000, ev if ev_type is not None else 0, 0x24072000  # addr-ok: harness
        for r, v in zip(REGS[:13], vals):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.reg_write(UC_ARM_REG_LR, 0x0805ABC1)  # addr-ok: harness
        u.emu_start(site | 1, resume, count=5000)
        regs = [u.reg_read(r) for r in REGS]
        flags = u.reg_read(UC_ARM_REG_XPSR) & 0xF8000000
        ram = bytes(u.mem_read(0x24000000, 0x80000))
        return regs, flags, ram, u

    def test_equivalence(self):
        for site, resume, t in self.HOOKS:
            for ready in (False, True):
                with self.subTest(site=hex(site), m4_ready=ready):
                    a = self.run_site(CH10, site, resume, t, ready)
                    b = self.run_site(DIAG, site, resume, t, ready)
                    self.assertEqual(a[0], b[0], "registers differ")
                    self.assertEqual(a[1], b[1], "flags differ")
                    # All M7 AXI RAM must match stock, except dead stack below the (identical) final
                    # SP (trampoline scratch) and, on 1.6.8, our own slice (in AXI since S2).
                    def ours_masked(ram):
                        ram = bytearray(ram)
                        for lo, hi in OUR_RAM:
                            if 0x24000000 <= lo < 0x24080000:
                                ram[lo - 0x24000000:hi - 0x24000000] = bytes(hi - lo)
                        return bytes(ram)
                    sp = a[0][13] - 0x24000000
                    dead = STACK - 0x24000000 - 0x1000
                    self.assertEqual(ours_masked(a[2])[:dead], ours_masked(b[2])[:dead], "M7 AXI RAM differs from stock")
                    self.assertEqual(a[2][sp:], b[2][sp:], "live stack differs")
                    n = len(records(b[3])) if ready else 0
                    self.assertEqual(n, 1 if ready and t is not None else 0)  # queue reads: heartbeat only

class StrikePeak(unittest.TestCase):
    """h_input keeps a per-pad strike: press counter + peak of the first pressure samples."""
    def ev(self, u, t, pad, raw):
        at = 0x24070200  # addr-ok: harness
        u.mem_write(at, struct.pack("<6I", t, 0, 0xFFFFFFFE, pad, raw, 0))
        call(u, SYM7["h_input"], 0x24071000, at)  # addr-ok: harness

    def strikes(self, u):
        raw = bytes(u.mem_read(D2 + 0x960, 32))
        return list(raw[:16]), list(raw[16:])

    def test_peak_over_the_first_samples_per_pad(self):
        u = emu(DIAG)
        self.ev(u, 0xAC, 4, 0x0800)                    # contact: low (~4)
        self.ev(u, 0xAE, 4, 0x6800)                    # pressure rises (~63)
        self.ev(u, 0xAC, 9, 0xD000)                    # another pad, full
        self.ev(u, 0xAE, 4, 0x3000)                    # lower again: peak kept
        seq, vel = self.strikes(u)
        self.assertEqual((seq[4], vel[4]), (1, 0x6800 * 127 // 0xD000))
        self.assertEqual((seq[9], vel[9]), (1, 127))
        for _ in range(6):
            self.ev(u, 0xAE, 4, 0x0100)                # the rest of the 8-sample window (low)
        for _ in range(4):
            self.ev(u, 0xAE, 4, 0xD000)                # past the window: ignored
        self.assertLess(self.strikes(u)[1][4], 127)
        self.ev(u, 0xAC, 4, 0x1000)                    # next press of pad 4: new count, new peak
        seq, vel = self.strikes(u)
        self.assertEqual((seq[4], vel[4]), (2, 0x1000 * 127 // 0xD000))

@unittest.skipIf("stall" in OFF, "stall group off in this build (SysTick not hooked)")
class StallProfile(unittest.TestCase):
    """v3.17: the M7 SysTick vector samples where the M7 is while audio is stalled."""
    ST, PROF, TICK = D2 + 0x80, D2 + 0x1000, S.SYSTICK_HANDLER

    def tick(self, u, pc):
        frame = struct.pack("<8I", 0, 1, 2, 3, 12, 0x08012345, pc, 0x01000000) + bytes(range(160))
        u.mem_write(STACK, frame)
        call(u, self.TICK)

    def test_samples_the_stalled_code_and_keeps_counting_ticks(self):
        u = emu(DIAG)
        w32(u, UWTICK, 10000)                                  # 10 s after the M7 booted
        w32(u, self.ST, 1000); w32(u, self.ST + 4, 1); w32(u, self.ST + 8, 7)   # last, armed, gen 7
        for ms in range(1, 401):
            self.tick(u, 0x0809F000 + ms * 2)  # addr-ok: harness
        self.assertEqual(r32(u, self.PROF), 0x31464F50, "magic")
        self.assertEqual(r32(u, self.PROF + 4), 7, "boot gen")
        self.assertEqual(r32(u, self.PROF + 8), 1, "one stall")
        n = r32(u, self.PROF + 12)
        self.assertEqual(n, 18, "samples at 50, 70, ... 390 ms")
        self.assertEqual(r32(u, self.PROF + 16), 399, "idle ms (first tick only arms)")
        pcs = [r32(u, self.PROF + 32 + 4 * i) for i in range(n)]
        self.assertEqual(pcs[0], 0x0809F000 + 51 * 2, "first sample = interrupted pc")  # addr-ok: harness
        self.assertEqual(r32(u, self.PROF + 32 + 160), 0x08012345, "lr")
        self.assertEqual(r32(u, self.PROF + 24), 300, "stack copied at 300 ms")
        self.assertEqual(r32(u, self.PROF + 28), STACK, "frame address")
        stack = self.PROF + 32 + 160 + 160 + 80
        self.assertEqual(r32(u, stack + 24), 0x0809F000 + 301 * 2, "stack copy starts with the frame")  # addr-ok: harness
        w32(u, self.ST, 2000)                                  # audio progressed
        self.tick(u, 0x0809F000)  # addr-ok: harness
        self.assertEqual(r32(u, self.PROF + 16), 0, "idle reset by progress")
        self.assertEqual(r32(u, self.PROF + 12), 18, "previous capture kept until a new stall")

    def test_boot_is_not_a_stall(self):
        """The tracking line survives resets: right after one, the M7's ~1.4 s display
        bring-up has no audio progress yet. v3.1-v3.17 recorded that as a stall."""
        u = emu(DIAG)
        w32(u, self.ST, 1000); w32(u, self.ST + 4, 1); w32(u, self.ST + 8, 7)   # stale: previous boot
        for ms in range(1, 1500):                              # uwTick counts up from 0 (HAL_IncTick)
            self.tick(u, 0x080439C0)  # addr-ok: harness
        self.assertEqual(r32(u, self.PROF + 12), 0, "no samples during boot")

    def test_hal_inctick_still_runs(self):
        a = emu(CH10); b = emu(DIAG)
        for u in (a, b):
            call(u, self.TICK)
        self.assertEqual(bytes(a.mem_read(0x24000000, 0x20000)), bytes(b.mem_read(0x24000000, 0x20000)),
                         "SysTick has the same effect on M7 RAM as stock (HAL_IncTick)")

class MainTaskProfile(unittest.TestCase):
    """v3.18: main_prof (M4 IRQ30, before stock's heartbeat supervisor)."""
    WD, MP = S.M4_WD_COUNTERS, D2 + 0x1400

    def setUp(self):
        self.u = emu(DIAG)
        w32(self.u, self.WD + 12, 1)                           # supervisor enabled
        w32(self.u, D2 + 0x1E0 + 12, 5)                        # CFG->r[0]: boot gen 5
        # handler stack: pushed {r3, EXC_RETURN=thread/PSP}; task frame on PSP
        self.msp, self.psp = 0x2407E000, 0x2407D000  # addr-ok: harness
        w32(self.u, self.msp + 4, 0xFFFFFFFD)
        self.u.mem_write(self.psp, struct.pack("<8I", 0, 1, 2, 3, 12, 0x08120001, 0x0811CDA0, 0x01000000)  # addr-ok: harness
                         + bytes(range(224)))

    def run_ticks(self, main_kick, upto, start=0):
        for t in range(start, upto + 1):
            w32(self.u, self.WD + 8, t); w32(self.u, self.WD + 4, main_kick); w32(self.u, self.WD, t)
            call(self.u, SYM4["main_prof"], self.msp, self.psp)

    def test_worst_gaps_and_a_stale_main_profile(self):
        self.run_ticks(main_kick=0, upto=2600)
        u, mp = self.u, self.MP
        self.assertEqual(r32(u, mp + 32), 2600, "worst Main gap")
        self.assertEqual(r32(u, mp + 36), 0, "PcmThread kicked every tick")
        self.assertEqual(r32(u, mp), 0x3150524D, "magic")
        self.assertEqual(r32(u, mp + 4), 5, "boot gen")
        self.assertEqual(r32(u, mp + 12), 17, "samples at 1000, 1100, ... 2600")
        self.assertEqual(r32(u, mp + 64), 0x0811CDA0, "interrupted task pc")  # addr-ok: harness
        self.assertEqual(r32(u, mp + 64 + 160), 0x08120001, "lr")  # addr-ok: harness
        self.assertEqual(r32(u, mp + 20), 2500, "stack copied at 2500")
        self.assertEqual(r32(u, mp + 28), 1, "from the task (PSP)")

    def test_irq_frame_when_an_interrupt_was_interrupted(self):
        w32(self.u, self.msp + 4, 0xFFFFFFF1)                  # EXC_RETURN: handler mode, MSP
        self.u.mem_write(self.msp + 8, struct.pack("<8I", 0, 0, 0, 0, 0, 0x08100003, 0x08107000, 0))  # addr-ok: harness
        self.run_ticks(main_kick=0, upto=1000)
        self.assertEqual(r32(self.u, self.MP + 64), 0x08107000, "frame above the handler's push")  # addr-ok: harness

    def test_fresh_main_records_nothing(self):
        for t in range(0, 3000):
            w32(self.u, self.WD + 8, t); w32(self.u, self.WD + 4, t - (t % 500)); w32(self.u, self.WD, t)
            call(self.u, SYM4["main_prof"], self.msp, self.psp)
        self.assertEqual(r32(self.u, self.MP + 32), 499, "worst gap")
        self.assertNotEqual(r32(self.u, self.MP), 0x3150524D, "no capture below 1000")

    @unittest.skipIf("irq30" in OFF, "irq30 group off in this build")
    def test_irq30_bl_goes_through_the_stub(self):
        ins = struct.unpack_from("<HH", DIAG, S.M4_WD_SITE - BASE)
        self.assertNotEqual(DIAG[S.M4_WD_SITE - BASE:S.M4_WD_SITE + 4 - BASE], CH10[S.M4_WD_SITE - BASE:S.M4_WD_SITE + 4 - BASE])

class PressureCapture(unittest.TestCase):
    """M2-1: h_input records each press's pressure on an 8 ms clock (m7_diag.c cap)."""
    BLOCK, SLOT = 0xC6100000, 1056  # addr-ok: harness

    def setUp(self):
        u = self.u = emu(DIAG)
        w32(u, D2 + 0x1820 + 4, self.BLOCK); w32(u, D2 + 0x1820 + 8, ~self.BLOCK & 0xFFFFFFFF)
        w32(u, D2 + 0x1820, 0x31504143)                         # CAPCFG published by the M4

    def ev(self, t, pad, raw, ms):
        w32(self.u, UWTICK, ms)
        at = 0x24070200  # addr-ok: harness
        self.u.mem_write(at, struct.pack("<6I", t, 0, 0xFFFFFFFE, pad, raw, 0))
        call(self.u, SYM7["h_input"], 0x24071000, at)  # addr-ok: harness

    def slot(self, pad, k):
        a = self.BLOCK + (pad * 2 + k) * self.SLOT
        hdr = struct.unpack("<8I", self.u.mem_read(a, 32))
        return dict(seq=hdr[0], t0=hdr[1], t_end=hdr[2], n=hdr[3], shift=hdr[4], done=hdr[5],
                    s=list(self.u.mem_read(a + 32, hdr[3])))

    def test_press_pressure_release(self):
        v = lambda raw: raw * 127 // 0xD000
        self.ev(0xAC, 3, 0x6800, 1000)                        # press: seq 1 -> slot 1
        self.ev(0xAE, 3, 0xD000, 1004)                        # same 8 ms bucket: latest wins
        self.ev(0xAE, 3, 0x3400, 1030)                        # bucket 3: 1, 2 hold 127
        self.ev(0xAD, 3, 0, 1050)                             # release in bucket 6: hold 3's value
        s = self.slot(3, 1)
        self.assertEqual((s["seq"], s["t0"], s["t_end"], s["done"], s["shift"]), (1, 1000, 1050, 1, 0))
        self.assertEqual(s["s"], [127, 127, 127, v(0x3400), v(0x3400), v(0x3400), v(0x3400)])

    def test_next_press_uses_the_other_slot(self):
        self.ev(0xAC, 3, 0x6800, 1000); self.ev(0xAD, 3, 0, 1020)
        self.ev(0xAC, 3, 0x1000, 2000)                        # seq 2 -> slot 0; slot 1 kept
        self.assertEqual(self.slot(3, 1)["done"], 1)
        self.assertEqual((self.slot(3, 0)["seq"], self.slot(3, 0)["done"]), (2, 0))

    def test_long_hold_halves_resolution(self):
        self.ev(0xAC, 7, 0x6800, 1000)
        self.ev(0xAE, 7, 0xD000, 1000 + 9000)                  # 9 s: bucket 1125 > 1024
        self.ev(0xAD, 7, 0, 1000 + 9100)
        s = self.slot(7, 1)
        self.assertEqual(s["shift"], 1)
        self.assertEqual(s["n"], (9100 // 8 >> 1) + 1)
        self.assertEqual(s["s"][-1], 127)

    def test_unpublished_block_records_nothing(self):
        w32(self.u, D2 + 0x1820, 0)
        self.ev(0xAC, 3, 0x6800, 1000)
        self.assertEqual(self.slot(3, 1)["n"], 0)

class NativeFunctions(unittest.TestCase):
    def pair(self):
        a, b = emu(CH10), emu(DIAG)
        m4_ready(b)
        return a, b

    def test_msg_send_with_lab_hook(self):
        a, b = self.pair()
        q, ev = 0x24060000, 0x24070000  # addr-ok: harness
        for u in (a, b):
            u.mem_write(ev, bytes([0x5B, 0, 0, 0]) + struct.pack("<5I", 0, 1, 2, 60, 100))
            call(u, S.MSG_SEND, q, ev)
        self.assertEqual(bytes(a.mem_read(q, 0x610)), bytes(b.mem_read(q, 0x610)))
        self.assertEqual(r32(b, q + 0x600), 1, "native msg_send still enqueues")
        recs = records(b)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0][1] & 0xFFFF, 2)       # K_SEND
        self.assertEqual(recs[0][2], SENT | 1)          # caller's LR
        self.assertEqual(recs[0][3], q)
        self.assertEqual(recs[0][4] & 0xFF, 0x5B)

    def test_msg_send_ignores_uninteresting(self):
        a, b = self.pair()
        q, ev = 0x24060000, 0x24070000  # addr-ok: harness
        b.mem_write(ev, bytes([0x3E]) + bytes(23))
        call(b, S.MSG_SEND, q, ev)
        self.assertEqual(len(records(b)), 0)

    def test_ring_post(self):
        a, b = self.pair()
        obj, ev = 0x24050000, 0x24070000  # addr-ok: harness
        for u in (a, b):
            u.mem_write(ev, bytes([0x5B, 0, 0, 0]) + bytes(range(20)))
            call(u, S.H_RING_SITE, obj, ev)
        self.assertEqual(bytes(a.mem_read(obj + 0x21C0, 0x1C0)), bytes(b.mem_read(obj + 0x21C0, 0x1C0)))
        recs = records(b)
        self.assertEqual([r[1] & 0xFFFF for r in recs], [3])
        # clock ticks are filtered
        b.mem_write(ev, bytes([0x61]) + bytes(23))
        call(b, S.H_RING_SITE, obj, ev)
        self.assertEqual(len(records(b)), 1)

    def test_modulation_streams_are_not_traced(self):
        """v3.44: bend 0x5D, CC 0x5E and channel pressure 0x60 arrive as streams
        (External modulation, up to 400/s per port). Tracing them filled the
        256 KB session log within seconds (BE-MT3: first save wrote 0 bytes)."""
        a, b = self.pair()
        obj, ev = 0x24050000, 0x24070000  # addr-ok: harness
        for t in (0x5D, 0x5E, 0x60) * 5:
            b.mem_write(ev, bytes([t]) + bytes(23))
            call(b, S.H_RING_SITE, obj, ev)
            w32(b, obj + 0x2340, r32(b, obj + 0x2360))           # the M4 keeps up: no overwrite
        self.assertEqual(records(b), [], "no trace records for the streams")
        b.mem_write(ev, bytes([0x5B]) + bytes(23))
        call(b, S.H_RING_SITE, obj, ev)
        self.assertEqual(len(records(b)), 1, "notes are still traced")

    def test_ring_fill_and_overflow(self):
        a, b = self.pair()
        obj, ev = 0x24050000, 0x24070000  # addr-ok: harness
        b.mem_write(ev, bytes([0x61]) + bytes(23))       # a clock tick: normally not logged
        w32(b, obj + 0x2360, 20); w32(b, obj + 0x2340, 9)  # 11 unread
        call(b, S.H_RING_SITE, obj, ev)
        self.assertEqual(len(records(b)), 0)
        self.assertEqual(r32(b, RINGSTAT), 11)            # max_fill
        w32(b, obj + 0x2340, 5)                           # 16 unread: this post overwrites
        call(b, S.H_RING_SITE, obj, ev)
        recs = records(b)
        self.assertEqual(len(recs), 1, "an overwrite is always logged")
        self.assertEqual(recs[0][10], 0x110)              # extra = 0x100 | fill
        self.assertEqual((r32(b, RINGSTAT), r32(b, RINGSTAT + 4), r32(b, RINGSTAT + 8)), (16, 1, 2))

    def test_queue_reads_are_a_heartbeat(self):
        a, b = self.pair()
        q, out, ev = 0x24060000, 0x24070100, 0x24070000  # addr-ok: harness
        b.mem_write(ev, bytes([0x5B]) + bytes(23))
        call(b, S.MSG_SEND, q, ev)        # any logged event initialises the M7 side
        w32(b, q + 0x600, 0)
        for _ in range(3):
            call(b, S.H_CONSUME_SITE, q, 0, out)
        call(b, S.H_CONSUME_SITE, q, 1, out)    # idx != 0: not a new pass
        self.assertEqual(r32(b, RINGSTAT + 20), 3)
        self.assertEqual(len(records(b)), 1, "queue reads are not logged")

    def test_stack_minimums(self):
        a, b = self.pair()
        ev = 0x24070000  # addr-ok: harness
        b.mem_write(ev, bytes([0x5B]) + bytes(23))
        call(b, S.MSG_SEND, 0x24060000, ev)                 # first call initialises the counters... (addr-ok: harness)
        call(b, S.MSG_SEND, 0x24060000, ev)                 # ...later calls sample SP (outside the MSP range) (addr-ok: harness)
        self.assertEqual(r32(b, RINGSTAT + 12), 0xFFFFFFFF)  # min_msp untouched
        self.assertLess(r32(b, RINGSTAT + 16), STACK)        # min_sp recorded
        for i, v in enumerate((0x24060000, ev)):  # addr-ok: harness
            b.reg_write(UC_ARM_REG_R0 + i, v)
        b.reg_write(UC_ARM_REG_SP, S.M7_HEAP_END + 0x1260)   # on the M7 main stack (1.6.1: 0x2404A000)
        b.reg_write(UC_ARM_REG_LR, SENT | 1)
        b.emu_start(S.MSG_SEND | 1, SENT, count=200000)
        msp = r32(b, RINGSTAT + 12)
        self.assertTrue(S.M7_HEAP_END <= msp < S.M7_HEAP_END + 0x1260, hex(msp))

LOG, PREV = "\\BENTO_DIAG.log", "\\BENTO_DIAG_prev.log"

class FatFsRig(unittest.TestCase):
    """M4 flush path against a tiny emulated FatFS (path -> bytes, modes honoured)."""
    live = True

    def setUp(self, previous=None):
        u = self.u = emu(DIAG)
        w32(u, S.UI_BUMP_CUR, 0xC6000000); w32(u, S.UI_BUMP_END, 0xC6200000)   # addr-ok: harness heap
        self.fs = {} if previous is None else {LOG: previous}
        self.open = {}      # FIL address -> [path, position]
        self.writes = []    # (path, mode, bytes written in that open)
        self.dma_unaligned = []
        self.write_bufs = []  # buffer address of every f_write (the SD DMA cannot read D2 SRAM)
        def cstr(uc, a):
            s = b""
            while True:
                c = bytes(uc.mem_read(a, 1))
                if c == b"\0": return s.decode()
                s += c; a += 1
        def hook(uc, addr, size, _):
            r = lambda i: uc.reg_read(UC_ARM_REG_R0 + i)
            ret = None
            if addr == S.M4_BL_TARGET: ret = 0                       # original loop function: no-op
            elif addr == S.FS_OPEN:                              # f_open(fil, path, mode)
                path, mode = cstr(uc, r(1)), r(2)
                if mode == 0x01:
                    ret = 0 if path in self.fs else 4             # FR_NO_FILE
                elif mode == 0x0A: self.fs[path] = b""; ret = 0   # create always
                elif mode == 0x32: self.fs.setdefault(path, b""); ret = 0   # append
                else: ret = 19                                    # anything else: invalid parameter
                if ret == 0:
                    self.open[r(0)] = [path, 0]
                    self.writes.append([path, mode, b""])
            elif addr == S.FS_READ:                              # f_read(fil, buf, n, &got)
                f = self.open[r(0)]; data = self.fs[f[0]][f[1]:f[1] + r(2)]
                uc.mem_write(r(1), data); w32(uc, r(3), len(data)); f[1] += len(data); ret = 0
            elif addr == S.FS_WRITE:                              # f_write(fil, buf, n, &done)
                self.write_bufs.append(r(1))
                f = self.open[r(0)]; data = bytes(uc.mem_read(r(1), r(2)))
                # The real f_write: at a sector boundary with a sector or more left, the caller's
                # pointer goes to the SD DMA, which rounds it down to 4 bytes (BE3.67-3.71 logs).
                pos = len(self.fs[f[0]]); head = -pos & 511; run = (r(2) - head) & ~511 if r(2) >= head else 0
                if run:
                    src = r(1) + head
                    if src & 3:
                        self.dma_unaligned.append((f[0], pos + head, src & 3))
                    data = data[:head] + bytes(uc.mem_read(src & ~3, run)) + data[head + run:]
                self.fs[f[0]] += data; self.writes[-1][2] += data; w32(uc, r(3), len(data)); ret = 0
            elif addr == S.FS_CLOSE: self.open.pop(r(0), None); ret = 0   # f_close
            if ret is not None:
                uc.reg_write(UC_ARM_REG_R0, ret)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in (S.M4_BL_TARGET, S.FS_OPEN, S.FS_READ, S.FS_WRITE, S.FS_CLOSE):
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)
        if self.live:
            w32(u, D2 + 0x1800, 0x54495257)               # opt into pre-v3.19 live card writes

    @property
    def files(self):
        """Contents written to the log in each open (what each flush wrote)."""
        return [w[2] for w in self.writes if w[0] == LOG and w[1] != 0x01]

    def tick(self, ms):
        w32(self.u, S.M4_TICK_MS, ms)
        call(self.u, SYM4["m4_tick"], 0, 0, limit=2000000)

    def send(self, t, lr=0x0805D5A1):  # addr-ok: harness
        ev = 0x24070000  # addr-ok: harness
        self.u.mem_write(ev, bytes([t, 0, 0, 0]) + struct.pack("<5I", 0, 1, 2, 60, 100))
        self.u.reg_write(UC_ARM_REG_R2, lr)
        call(self.u, SYM7["h_send"], 0x24060000, ev, lr)  # addr-ok: harness

class M4EndToEnd(FatFsRig):
    """Live card writing opted in (the pre-v3.19 path). BootOnlyWrites covers what ships."""
    live = True

    def test_flow(self):
        self.tick(1000)                                  # first call: init + allocate
        self.assertEqual(r32(self.u, M4H), MAGIC)
        self.assertEqual(r32(self.u, M4H + 12), S.EXT_POOL_END, "buffer from the native allocator")
        self.send(0x5B); self.send(0x5C)
        self.tick(1100)                                  # drains, but pads not idle yet
        self.assertEqual(self.files, [])
        self.tick(2700)                                  # idle >= 1500 ms -> flush
        self.assertEqual(len(self.files), 1)
        text = self.files[0].decode()
        self.assertIn("BENTO black box, firmware BE", text)
        self.assertRegex(text, r"at boot \(after the black box's own buffers\): \| ram bump cur=[0-9a-f]{8} end=[0-9a-f]{8} free=\d+")
        self.assertRegex(text, r"\n# .* \| ram bump cur=[0-9a-f]{8} end=[0-9a-f]{8} free=\d+")
        self.assertRegex(text, r"\n# .* (d2|slice) canary changed=0\b", "canary armed at boot, untouched since")
        self.assertEqual(r32(self.u, D2 + 0xA00), 0x59524E43, "canary magic")
        self.assertIn("# m7 dropped=0 | poly sent=0 budget_drops=0 stolen=0 retrig=0 swallowed=0"
                      " | midi ring max_fill=0 overflows=0 | m7 stack min_msp=", text)
        self.assertEqual(text.count(" SEND "), 2)
        self.assertIn("lr=0805d5a1", text)
        self.assertNotIn("!!!", text, "no alarms in a normal session")
        self.tick(5000)                                  # nothing new -> no write at all
        self.assertEqual(len(self.files), 1)

    def test_trace_leaves_room_for_summaries(self):
        """BE3.57: the trace filled the 256 KB log by ~10 min, so an 18.6-min save's
        summaries were lost. The trace stops 32 KB early; other lines still fit."""
        limit = 0x40000 - 512 - 0x8000
        self.tick(1000)
        buf = r32(self.u, M4H + 12)
        w32(self.u, M4H + 16, limit - 60)                # the trace has nearly used its share
        for t in (0x5B, 0x5C, 0x5B): self.send(t)
        self.tick(1100); self.tick(2700)
        n = r32(self.u, M4H + 16)
        text = bytes(self.u.mem_read(buf + limit - 60, n - (limit - 60))).decode()
        self.assertEqual(text.count("# trace stopped at ms "), 1)
        self.assertLessEqual(text.count(" SEND "), 1, "the trace stops at its limit")
        self.assertIn("# m7 dropped=", text, "summaries still fit after it")
        for t in (0x5B, 0x5C): self.send(t)
        self.tick(2800); self.tick(4400)
        more = bytes(self.u.mem_read(buf + n, r32(self.u, M4H + 16) - n)).decode()
        self.assertNotIn(" SEND ", more, "later records are not traced either")

    def test_later_flushes_append_only_new_text(self):
        self.tick(1000); self.send(0x5B); self.tick(1100); self.tick(2700)
        first = self.files[0]
        self.send(0x5C); self.tick(2800); self.tick(4400)
        self.assertEqual(len(self.files), 2)
        self.assertEqual([w[1] for w in self.writes if w[0] == LOG], [0x0A, 0x32], "create, then append")
        second = self.files[1].decode()
        self.assertNotIn("BENTO M1b", second, "header not rewritten")
        self.assertEqual(second.count(" SEND "), 1)
        self.assertEqual(self.fs[LOG], first + self.files[1], "file = first write + appended text")

    def test_crash_record_in_header(self):
        for i, v in {24: 3, 25: 5, 31: 0x080DA2B1, 27: 2, 8: 0x2404CC00}.items():  # addr-ok: harness
            w32(self.u, RTC_BKP + 4 * i, v)
        self.tick(1000); self.send(0x5B); self.tick(1100); self.tick(2700)   # drain, then idle-gated write
        text = self.files[0].decode()
        self.assertIn("stock crash record: count=3 cause=00000005 pc=080da2b1 core=00000002", text)
        self.assertIn(" 2404cc00", text)                 # bkp8, part of the stacked frame
        self.assertEqual(text.count("rtc_bkp0..31:"), 1)

    def test_previous_log_preserved_at_first_write(self):
        old = b"BENTO session before the crash\r\n" + bytes(range(32, 127)) * 30   # > 512 B: several chunks
        self.setUp(previous=old)
        self.tick(1000)
        self.assertNotIn(PREV, self.fs, "not at the first UI-loop pass (card may not be mounted yet)")
        self.send(0x5B); self.tick(1100); self.tick(2700)
        self.assertEqual(self.fs.get(PREV), old, "copied right before the first write")
        self.assertNotIn(b"before the crash", self.fs[LOG])

    def test_new_crash_record_is_written_immediately(self):
        self.tick(1000)                                  # baseline: count 0
        w32(self.u, RTC_BKP + 4 * 24, 1); w32(self.u, RTC_BKP + 4 * 31, 0x0808CC5A)  # addr-ok: harness
        self.tick(1050)                                  # no idle wait
        text = self.files[-1].decode()
        self.assertIn("!!! NEW CRASH RECORD at ms 1050", text)
        self.assertIn("count=1", text)
        self.assertIn("pc=0808cc5a", text)
        self.tick(1100)
        self.assertEqual(text.count("!!!"), 1, "reported once")

    def test_crash_history_one_line_per_boot_appended_forever(self):
        hist = "\\BENTO_CRASHES.log"
        self.fs[hist] = b"older history\r\n"
        w32(self.u, RTC_BKP + 4 * 24, 86); w32(self.u, RTC_BKP + 4 * 25, 5)
        w32(self.u, RTC_BKP + 4 * 31, 0x08122BC4); w32(self.u, RTC_BKP + 4 * 27, 1)  # addr-ok: harness
        self.tick(1000)
        self.assertEqual(self.fs[hist], b"older history\r\n", "not before the card is in use")
        self.tick(2600)                                  # first log write of this boot
        lines = self.fs[hist].decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], "^boot fw=" + FW + r" rtc=\d{6}-\d{6} gen=1 ms=2600 count=86 cause=00000005 pc=08122bc4 core=00000001")
        self.tick(5000)                                  # later flushes: no new history line
        self.assertEqual(len(self.fs[hist].decode().splitlines()), 2)
        w32(self.u, RTC_BKP + 4 * 24, 87)
        self.tick(50); self.tick(1700)                   # reboot, first write of boot 2
        lines = self.fs[hist].decode().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[2], "^boot fw=" + FW + r" rtc=\S+ gen=2 ms=1700 count=87")
        self.assertTrue(all(w[1] == 0x32 for w in self.writes if w[0] == hist), "append only, never truncated")

    def test_crash_history_live_crash_line(self):
        hist = "\\BENTO_CRASHES.log"
        self.tick(1000)
        w32(self.u, RTC_BKP + 4 * 24, 1); w32(self.u, RTC_BKP + 4 * 31, 0x0808CC5A)  # addr-ok: harness
        self.tick(1050)
        lines = self.fs[hist].decode().splitlines()
        self.assertRegex(lines[-1], "^live fw=" + FW + r" rtc=\S+ gen=1 ms=1050 count=1")
        self.assertIn("pc=0808cc5a", lines[-1])

    # --- audio DMA monitor (DMA2 streams 5 and 7, as stock's own main-loop check)
    S5, S7 = 0x40020488, 0x400204B8

    def stream(self, base, cr=1, ndtr=100, par=0x40015820):
        for i, v in enumerate((cr, ndtr, par, 0x30000000, 0x30001000, 0x21)):  # addr-ok: harness
            w32(self.u, base + 4 * i, v)

    def test_running_audio_dma_raises_nothing(self):
        self.stream(self.S5); self.stream(self.S7)
        for i, ms in enumerate(range(1000, 1400, 10)):
            w32(self.u, self.S5 + 4, 100 - i % 50); w32(self.u, self.S7 + 4, 200 - i % 70)
            self.tick(ms)
        w32(self.u, self.S5 + 4, 7); w32(self.u, self.S7 + 4, 9)   # still running at the idle flush
        self.tick(3000)
        self.assertTrue(self.files, "the idle flush happened")
        self.assertFalse(any(b"AUDIO DMA" in f for f in self.files))

    def test_stream_that_stops_advancing_is_snapshotted(self):
        self.stream(self.S5); self.stream(self.S7)
        w32(self.u, 0x40015818, 0x00000009)                    # SAI1 A SR (e.g. overrun + flag)
        w32(self.u, 0x40020400 + 4, 0x00000800)                # HISR: S5 transfer-error-ish bits
        self.tick(1000); w32(self.u, self.S7 + 4, 199)
        self.tick(1020); w32(self.u, self.S7 + 4, 198)
        self.tick(1040)                                        # S5 unchanged for 40 ms
        text = self.files[-1].decode()
        self.assertIn("!!! AUDIO DMA STALL at ms 1040", text)
        self.assertIn("stream 5 stopped advancing", text)
        self.assertIn("HISR=00000800", text)
        self.assertIn("S5 CR,NDTR,PAR,M0AR,M1AR,FCR = 00000001 00000064 40015820", text)
        self.assertIn("periph SR=00000009", text)
        self.assertRegex(self.fs["\\BENTO_CRASHES.log"].decode().splitlines()[-1], "^stall fw=" + FW + r" rtc=\S+ gen=1 ms=1040")
        n = len(self.files); self.tick(1100); self.tick(3000)
        self.assertFalse(any(b"AUDIO DMA" in f for f in self.files[n:]), "reported once per boot")
        self.stream(self.S5, cr=0); self.stream(self.S7, cr=0)  # reset: audio not restarted yet
        self.tick(50); self.tick(1700)                         # the watchdog reset: next boot
        boot = self.files[-1].decode()
        self.assertIn("audio stall snapshot (from RAM) at ms 1040 gen 1: stream 5 stopped advancing", boot)
        self.tick(50); self.tick(1700)
        self.assertNotIn("audio stall snapshot", self.files[-1].decode(), "printed once")

    def test_stream_disabled_after_running_is_reported(self):
        self.stream(self.S7)
        self.tick(1000); self.stream(self.S7, cr=0); self.tick(1010)
        self.assertIn("stream 7 disabled", self.files[-1].decode())

    def test_non_sai_peripheral_is_never_read(self):
        self.stream(self.S5, par=0x40011028)                   # e.g. a USART data register
        self.tick(1000); self.tick(1040)
        self.assertIn("periph SR=00000000 CR1=00000000", self.files[-1].decode())


    # --- v3.1: RTC, previous-boot line, M7 stall record
    def test_rtc_and_previous_boot_in_header(self):
        w32(self.u, 0x58004000, 0x00142305); w32(self.u, 0x58004004, 0x0026B026)   # 14:23:05, 26-10-26 (wd bits set)
        self.tick(1000); self.tick(2600)
        first = self.files[-1].decode()
        self.assertIn("firmware BE", first); self.assertIn("rtc 261026-142305", first)
        w32(self.u, 0x58004000, 0x00142410)
        self.tick(4000)                                  # last pass of this boot
        w32(self.u, 0x58004000, 0x00153000)
        self.tick(50); self.tick(1700)                   # reset, next boot
        boot = self.files[-1].decode()
        self.assertIn("previous boot: last UI-loop pass at ms 4000, rtc 261026-142410", boot)
        self.assertIn("boot gen 2, rtc 261026-153000", boot)

    def m7(self, fn, cyc):
        if r32(self.u, UWTICK) < 10000:
            w32(self.u, UWTICK, 10000)                   # well after the M7 booted (boot guard)
        w32(self.u, 0xE0001004, cyc)
        call(self.u, SYM7[fn], 0)

    def test_m7_stall_record_written_once_and_printed_next_boot(self):
        self.tick(1000)                                  # M4 publishes boot gen 1
        w32(self.u, 0xE000E108, 0x50); w32(self.u, 0xE000E208, 0x40)   # IRQ 68/70 enabled, 70 pending
        w32(self.u, 0x40020404, 0x00200000)              # HISR: S7 half-transfer flag set
        self.m7("stall_progress", 1000)
        self.m7("stall_check", 1000 + 99 * 480000)       # 99 ms: not yet
        self.assertNotEqual(r32(self.u, D2 + 0xA0), 0x38544C53)
        self.m7("stall_check", 1000 + 101 * 480000)      # 101 ms: snapshot
        self.assertEqual(r32(self.u, D2 + 0xA0), 0x38544C53)
        w32(self.u, 0x40020404, 0)
        self.m7("stall_check", 1000 + 200 * 480000)      # once per boot
        self.assertEqual(r32(self.u, D2 + 0xA0 + 4 * 15), 0x00200000, "first snapshot kept (hisr)")
        self.tick(50); self.tick(1700)                   # watchdog reset, next boot
        boot = self.files[-1].decode()
        self.assertIn("M7 audio stall record (from RAM): gen 1, no audio progress for 101 ms", boot)
        self.assertIn("irq64-95 en/pend/act=00000050/00000040/00000000", boot)
        self.assertIn("HISR=00200000", boot)
        self.tick(50); self.tick(1700)
        self.assertNotIn("M7 audio stall record", self.files[-1].decode(), "printed once")

    @unittest.skipIf("stall" in OFF, "stall group off in this build (SysTick not hooked)")
    def test_stall_profile_printed_next_boot_once(self):
        self.tick(1000)                                  # M4 publishes boot gen 1
        self.m7("stall_progress", 1000)                  # armed
        frame = struct.pack("<8I", 0, 1, 2, 3, 12, 0x08055551, 0x0809ABCC, 0x01000000) + bytes(160)  # addr-ok: harness
        for _ in range(320):                             # 320 ms without audio progress
            self.u.mem_write(STACK, frame)
            call(self.u, S.SYSTICK_HANDLER)
        self.tick(50); self.tick(1700)                   # watchdog reset, next boot
        boot = self.files[-1].decode()
        self.assertIn("M7 stall profile (from RAM): gen 1, stall #1 of that boot, idle 319 ms (SysTick)", boot)
        self.assertIn("50:0809abcc/08055551", boot)
        self.assertIn("stack at 300 ms, frame 2407f000", boot)
        self.tick(50); self.tick(1700)
        self.assertNotIn("M7 stall profile", self.files[-1].decode(), "printed once")

    def test_m7_progress_rearms_after_a_reboot(self):
        self.tick(1000)
        self.m7("stall_progress", 0); self.m7("stall_check", 101 * 480000)
        self.tick(50); self.tick(1700)                   # gen 2
        self.m7("stall_progress", 0)
        self.assertEqual(r32(self.u, D2 + 0x8C), 0, "snapped flag reset for the new boot")

    def test_heartbeat_stall_is_reported(self):
        self.tick(1000)
        self.send(0x5B)                                  # M7 side live
        for _ in range(5):
            call(self.u, S.H_CONSUME_SITE, 0x24060000, 0, 0x24070100)  # addr-ok: harness
        self.tick(1100)                                  # heartbeat seen moving
        self.tick(3000)                                  # 1.9 s quiet: not yet
        self.assertFalse(any(b"HEARTBEAT" in f for f in self.files))
        self.tick(4200)                                  # 3.1 s quiet: audio core presumed stopped
        self.assertTrue(any(b"!!! M7 HEARTBEAT STOPPED" in f for f in self.files))
        n = len(self.files); self.tick(9000)
        self.assertFalse(any(b"HEARTBEAT" in f for f in self.files[n:]), "reported once")

    def test_post_mortem_tail_in_next_boot_header(self):
        self.tick(1000)
        self.send(0x5B); self.send(0x5C)
        self.tick(1100)                                  # drained (no file yet: never idle)
        self.tick(50)                                    # reset: new boot
        self.send(0x5D); self.tick(100); self.tick(1700)
        text = self.files[-1].decode()
        self.assertIn("previous boot (from RAM):", text)
        tail = [l for l in text.splitlines() if l.startswith("  T ")]
        self.assertEqual([l.split("ev=")[1][6:8] for l in tail], ["5b", "5c"], "previous boot's final records")

    def test_overflow_and_reboot(self):
        self.tick(1000)
        for _ in range(25):
            self.send(0x5B)
        self.assertEqual(r32(self.u, M7H + 8), 8, "M7 refuses, never overwrites unread records")
        self.tick(1100); self.tick(3000)
        text = self.files[-1].decode()
        self.assertEqual(text.count(" SEND "), 17)
        self.assertIn("# m7 dropped=8", text)
        self.tick(50)                                    # tick went backwards: reboot
        self.assertEqual(r32(self.u, M4H + 8), 2, "boot generation advanced")
        self.send(0x5B)                                  # M7 resyncs to the new generation
        self.assertEqual(r32(self.u, M7H + 8), 0)
        self.assertEqual(len(records(self.u)), 1)

class ReadinessDiagnostics(FatFsRig):
    """D0 (v3.64): render load on the M7, stock's counters, canaries over the ports' RAM."""
    live = False
    D0A, D0C, D0L = D2 + 0x9A0, D2 + 0xFC0, D2 + 0xFE0
    OBJ = S.LOAD_RING
    BLOCKS = tuple((lo, hi) for _, lo, hi in RAMCHK)

    def hb(self, n=256):
        """h_consume reaching the n-th heartbeat, as the audio loop would."""
        w32(self.u, RINGSTAT + 20, n - 1)
        call(self.u, SYM7["h_consume"], 0, 0, 0)

    def m7_ready(self, gen=1):
        w32(self.u, M4H, MAGIC); w32(self.u, M4H + 8, gen)
        call(self.u, SYM7["diag_put"], 1, 0, 0, 0, 0)     # first call this boot: resets the M7 side

    def heap(self, *chunks, next_last=0):
        w32(self.u, S.M7_HEAP_FREELIST, chunks[0][0] if chunks else 0)
        for i, (a, size) in enumerate(chunks):
            w32(self.u, a, chunks[i + 1][0] if i + 1 < len(chunks) else next_last)
            w32(self.u, a + 4, size)

    def block(self, ratio, period=1280000):
        """Stock's 0x08056F00: the block that just finished, at the next one's start."""
        idx, start = r32(self.u, self.OBJ + 0x408), r32(self.u, self.OBJ + 0x414)
        self.u.mem_write(self.OBJ + 8 + (idx & 0xFF) * 4, struct.pack("<f", ratio))
        w32(self.u, self.OBJ + 0x414, start + period)
        w32(self.u, self.OBJ + 0x408, idx + 1)
        self.hb(1)

    def load(self):
        return struct.unpack("<8I", self.u.mem_read(self.D0L, 32))

    def save_text(self):
        call(self.u, SYM4["dump_after_save"], 0)
        return self.fs["\\BENTO_SAVES.log"].decode()

    def ready(self):
        """Booted on both sides, with the M7's first publish done."""
        self.tick(1000); self.m7_ready(); self.heap(); self.hb()

    def test_m7_publishes_on_the_256th_heartbeat_only(self):
        self.m7_ready()
        for a, v in ((S.AUDIO_BLOCK_STATS, 900), (S.AUDIO_BLOCK_STATS + 0xC, 55), (S.AUDIO_GAP_STATS + 0xC, 1), (S.AUDIO_GAP_STATS + 0x10, 3), (S.AUDIO_GAP_STATS + 8, 0x700),
                     (S.AUDIO_GAP_STATS + 0x14, 4), (S.AUDIO_GAP_STATS + 0x18, 20000), (S.AUDIO_GAP_STATS + 0x1C, 8), (S.AUDIO_DMA_EVENTS, 1802),
                     (S.DTCM_USED, 131040), (S.AXI_USED, 100288), (S.POOL_LEFT_RAM, 5), (S.SHARED_POOL_LEFT, 6)):
            w32(self.u, a, v)
        self.heap((0x24030000, 37000), (0x24040000, 776))  # addr-ok: harness
        self.hb(255)
        self.assertEqual(r32(self.u, self.D0A), 0, "not on other heartbeats")
        self.hb(256)
        a = struct.unpack("<15I", self.u.mem_read(self.D0A, 60))
        self.assertEqual(a[0], 0x3137304D)
        self.assertEqual(a[2:8], (37776, 37776, 2, 37000, 0, 131040))
        self.assertEqual(a[8:15], (100288, 5, 6, 0, 55, 3, 0x700), "no wrong-half yet: head_min = least headroom")
        self.assertEqual(struct.unpack("<6I", self.u.mem_read(self.D0C, 24)), (900, 901, 8, 4, 20000, 1))

    def seq_fields(self, text):
        import re
        m = re.findall(r" seq (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)", text)
        self.assertTrue(m, "seq fields on the AUDIO line")
        return tuple(int(x) for x in m[-1])

    def test_seq_timer_is_summed_peaked_and_cleared_per_stretch(self):
        """v3.69 Stage 0: stock's per-track pass timer, once per block, read only."""
        self.ready()
        before = bytes(self.u.mem_read(S.PASS_TIMER, 0x14))
        w32(self.u, S.PASS_TIMER + 4, 25600); self.block(0.5)
        w32(self.u, S.PASS_TIMER + 4, 76800); self.block(0.5)
        w32(self.u, S.PASS_TIMER + 4, 25600); self.block(0.5)
        after = bytes(self.u.mem_read(S.PASS_TIMER, 0x14))
        self.assertEqual(after[:4] + after[8:], before[:4] + before[8:], "stock's timer block is never written")
        self.assertEqual(struct.unpack("<3I", self.u.mem_read(D2 + 0xF20, 12))[:3], (100 + 300 + 100, 3, 76800))
        self.hb()
        f = self.seq_fields(self.save_text())
        self.assertEqual(f[:3], (500, 3, 76800), "sum >> 8, blocks, peak reach the save line")
        w32(self.u, S.PASS_TIMER + 4, 2560); self.hb(); self.save_text(); self.hb(); self.block(0.5)
        q = struct.unpack("<3I", self.u.mem_read(D2 + 0xF20, 12))
        self.assertEqual(q, (10, 1, 2560), "a save starts a new stretch")

    def test_probe_runs_rarely_touches_only_its_own_lines_and_restores_interrupts(self):
        self.ready()
        BLOCK, FREE = 0xC6100000, S.PROBE_SCRATCH  # addr-ok: harness
        for a in (BLOCK, FREE):
            try: self.u.mem_map(a, 0x10000)
            except Exception: pass
        w32(self.u, D2 + 0x1820 + 4, BLOCK)                 # CAPCFG->block
        self.u.mem_write(FREE, bytes([0xA5]) * 256); self.u.mem_write(BLOCK, b"Z" * 256)
        self.hb(256)
        self.assertEqual(r32(self.u, D2 + 0xF20 + 20), 0, "no price check on an ordinary publish")
        reads, writes, cci = [], [], []
        def acc(uc, kind, a, n, v, _):
            if 0xC0000000 <= a < 0xD0000000: (writes if kind == UC_MEM_WRITE else reads).append(a)
            if a == 0xE000EF70: cci.append(v)
        h = self.u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, acc)
        self.hb(0x4000)
        self.u.hook_del(h)
        self.assertEqual(r32(self.u, D2 + 0xF20 + 20), 1, "one price check at the 0x4000th heartbeat")
        self.assertEqual(self.u.reg_read(UC_ARM_REG_PRIMASK), 0, "interrupts back on")
        self.assertEqual(writes, [], "read only in SDRAM")
        self.assertEqual(sorted(cci), [FREE + i * 32 for i in range(8)], "maintenance only on the free lines")
        self.assertEqual(sorted(reads), sorted([BLOCK + i * 32 for i in range(8)] + [FREE + i * 32 for i in range(8)]))
        self.assertEqual(bytes(self.u.mem_read(FREE, 256)), bytes([0xA5]) * 256)
        self.assertLessEqual(FREE + 8 * 32, self.REGION, "its lines end below the region, which is reserved from here up")
        w32(self.u, D2 + 0x1820 + 4, 0x08000000)            # a nonsense address is not followed
        self.hb(0x8000)
        self.assertEqual(r32(self.u, D2 + 0xF20 + 20), 1)

    REGION = S.REGION

    def test_publish_cleans_exactly_the_region_lines_the_m4_reads(self):
        """v3.70: the region is write-back on the M7, and the M4 has no way to see a dirty line."""
        self.ready()
        lines = []
        def w(uc, k, a, n, v, _):
            if a == 0xE000EF68: lines.append(v & 0xFFFFFFFF)
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, w)
        self.u.ctl_flush_tb()                             # code that already ran doesn't see a later hook
        self.hb(255)
        self.assertEqual([a for a in lines if a >= 0xC0000000], [], "not between publishes")
        self.hb(256)
        self.u.hook_del(h)
        self.assertEqual([a for a in lines if a >= 0xC0000000], [self.REGION, self.REGION + 0x20, self.REGION + 0x40])

    @unittest.skipUnless({"poly", "retrig", "nmod"} <= OFF, "features-off twin only")
    def test_features_off_twin_never_reserves_or_writes_the_pool_tail(self):
        """The bisect twin has only the trace hooks in stock's path. If one of them reached a region
        consumer the baseline would reserve and zero, and no longer be stock's behaviour."""
        writes = []
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: writes.append(a), begin=S.PROBE_SCRATCH, end=S.EXT_POOL_END - 1)
        self.u.ctl_flush_tb()
        self.ready()                                       # its publish runs h_consume too
        self.assertEqual((r32(self.u, D2 + 0x1E0), r32(self.u, D2 + 0x1E0 + 4)), (MAGIC, self.REGION), "the state could come up")
        ev, obj = 0x24070000, 0x24071000  # addr-ok: harness
        for t in (0x5B, 0x5C, 0x0E, 0x5F, 0xAC, 0xAD, 0xAE):
            self.u.mem_write(ev, struct.pack("<6I", t, 0, 1, 2, 60, 100))
            call(self.u, SYM7["h_input"], obj, ev)
            call(self.u, SYM7["h_send"], 0x24060000, ev, SENT | 1)  # addr-ok: harness
            call(self.u, SYM7["h_ring"], obj, ev)
        self.hb(256); self.hb(0x4000)
        self.u.hook_del(h)
        self.assertEqual(r32(self.u, D2 + 0xF20 + 20), 1, "the probe ran")
        self.assertEqual(struct.unpack("<2I", self.u.mem_read(S.POOL_LEFT_RAM, 8)), (3405824, S.EXT_POOL_END - 3405824), "pool counters as stock's startup left them")
        self.assertEqual(writes, [], "nothing written at or above the probe's lines")

    def test_the_first_block_after_the_m4_is_up_takes_the_reservation(self):
        """S6a review (B1, build): h_consume takes the Region and the synth area once the M4's config line is up, so the
        M4 can attach right after its first tick instead of waiting for the first note (late, as v4.10, in a BE_DISABLE
        bisect of analog: that build has no call)."""
        if "analog" in OMITTED or "analog" in OFF:
            self.skipTest("no analog group running in this image")
        left = lambda: r32(self.u, S.POOL_LEFT_RAM)
        self.tick(1000); self.m7_ready(); self.heap()
        self.assertEqual(left(), 3405824, "nothing before the first block")
        self.hb(1)
        self.assertEqual(left(), 3405824 - 0xC000, "the Region and the synth area, one drop")
        self.assertEqual(struct.unpack("<5I", self.u.mem_read(self.REGION - 0x8000, 20)), (0x37534542, 1, 2, 0xC000, 1),
                         "the synth area's header, stamped for this M4 boot")
        self.hb(2)
        self.assertEqual(left(), 3405824 - 0xC000, "once")

    def test_m4_prints_the_stats_at_the_fixed_address(self):
        self.ready()
        self.assertEqual(r32(self.u, D2 + 0x1E0 + 4), self.REGION, "published by the M4")
        stats = (11, 22, 33, 44, 0xDDDD, 55, 66, 0x77, 0x88, 0x99, 170)    # +32..+75; +48 is midi_q, not printed
        self.u.mem_write(self.REGION + 32, struct.pack("<11I", *stats))
        text = self.save_text()
        self.assertIn(" | curves started=11 sent=22 late=33 skipped=44 peak players=55 thinned=66 last dest=00000077", text)
        self.assertIn(" | internal dest=00000088 live=00000099 posts=170", text)

    def test_m4_follows_no_other_region_pointer(self):
        self.ready()
        for other in (0xC6100000, self.REGION + 32, self.REGION - 32, 0):  # addr-ok: harness
            self.fs.clear()
            w32(self.u, D2 + 0x1E0 + 4, other)
            self.assertNotIn("curves started=", self.save_text(), hex(other))
        w32(self.u, D2 + 0x1E0 + 4, self.REGION)
        self.fs.clear()
        self.assertIn("curves started=", self.save_text())

    def test_m4_never_writes_the_region_and_stays_inside_its_stats(self):
        reads, writes = [], []
        def m(uc, k, a, n, v, _): (writes if k == UC_MEM_WRITE else reads).append(a)
        h = self.u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, m, begin=self.REGION, end=self.REGION + 0x3FFF)
        self.tick(1000); self.save_text(); self.tick(2000)      # boot, publish, a save, a poll
        self.tick(50); self.save_text()                         # a reboot: its boot print follows CFG too
        self.u.hook_del(h)
        self.assertEqual(writes, [])
        self.assertTrue(reads, "the save line reads them")
        self.assertGreaterEqual(min(reads), self.REGION + 32)
        self.assertLessEqual(max(reads), self.REGION + 75)

    def test_m4_pool_holds_only_its_own_allocations(self):
        self.tick(1000)
        buf = r32(self.u, M4H + 12)
        self.assertEqual(buf, S.EXT_POOL_END)
        self.assertEqual(r32(self.u, D2 + 0x1820 + 4), buf + 0x40000, "the capture block follows the log buffer: no 12 KB between")

    def test_m4_preflight_no_longer_counts_the_region(self):
        for room, ok in ((0x40000 + 33792 + 64, True), (0x40000 + 33792 + 63, False)):
            self.setUp()
            w32(self.u, S.UI_BUMP_END, S.EXT_POOL_END + room)
            self.tick(1000)
            self.assertEqual(r32(self.u, M4H + 12) == S.EXT_POOL_END, ok, room)
            self.assertEqual(r32(self.u, D2 + 0x1E0 + 4), self.REGION, "the region doesn't depend on the M4's pool")

    def test_head_min_goes_negative_once_late(self):
        self.ready()
        w32(self.u, S.AUDIO_BLOCK_STATS + 4, 2); w32(self.u, S.AUDIO_BLOCK_STATS + 0x18, 0xFFFFFF00); w32(self.u, S.AUDIO_BLOCK_STATS + 0xC, 55)
        self.hb()
        self.assertEqual(r32(self.u, self.D0A + 48), 0xFFFFFF00)
        self.assertIn("wrong_half=2 head_min=-256 ", self.save_text())

    def test_heap_minimum_is_kept(self):
        self.m7_ready()
        self.heap((0x24030000, 1000)); self.hb()  # addr-ok: harness
        self.heap((0x24030000, 400)); self.hb()  # addr-ok: harness
        self.heap((0x24030000, 900)); self.hb()  # addr-ok: harness
        self.assertEqual(r32(self.u, self.D0A + 8), 900)
        self.assertEqual(r32(self.u, self.D0A + 12), 400)

    def test_corrupt_free_list_cannot_fault(self):
        self.m7_ready()
        for bad_next in (0xDEADBEEF, S.M7_HEAP_END, 0x24030001, 0x08000000):  # addr-ok: harness
            self.heap((0x24030000, 1000), next_last=bad_next); self.hb()  # addr-ok: harness
            self.assertEqual(r32(self.u, self.D0A + 24), 1, hex(bad_next))
            self.assertEqual(r32(self.u, self.D0A + 12), 0xFFFFFFFF, "a bad walk never moves the minimum")
        w32(self.u, 0x24030000, 0x24030000)               # a loop ends at the chunk limit (addr-ok: harness)
        self.hb()
        self.assertEqual(r32(self.u, self.D0A + 24), 1)

    def test_render_time_is_a_share_of_the_fixed_budget(self):
        self.ready()
        self.block(0.25); self.block(0.5); self.block(0.75); self.block(0.875)
        l = self.load()
        self.assertEqual(l[0], 4)
        self.assertEqual(l[1], 250 + 500 + 750 + 875)
        self.assertEqual(l[2], 875)
        self.assertEqual(l[3:7], (1, 2, 1, 0), "<50, 50-80, 80-100, 100+")

    def test_overload_reads_as_overload(self):
        """Stock's ratio stays under 1.0 and falls when renders run late; ours doesn't."""
        self.ready()
        self.block(0.75, period=2 * 1280000)              # 75 % of a period two budgets long
        l = self.load()
        self.assertEqual(l[2], 1500, "150 % of the budget")
        self.assertEqual(l[6], 1, "the 100+ bucket")

    def test_skipped_blocks_are_counted_not_guessed(self):
        self.ready()
        w32(self.u, self.OBJ + 0x408, r32(self.u, self.OBJ + 0x408) + 3)
        self.hb(1)
        self.assertEqual(self.load()[0], 0)
        self.assertEqual(r32(self.u, self.D0C + 24), 1)
        self.block(0.5)
        self.assertEqual(self.load()[0], 1, "back in step")

    def test_odd_floats_cannot_overflow(self):
        self.ready()
        for bad in (float("nan"), float("inf"), -1.0, 1e-30):
            self.block(bad, period=0xFFFFFF00)
        self.assertEqual(self.load()[0], 4)
        self.assertLessEqual(self.load()[2], 800000)

    def test_cold_ram_garbage_is_cleared_at_first_publish(self):
        self.u.mem_write(self.D0L, b"\xA5" * 32)
        self.m7_ready(); self.heap(); self.hb()
        self.assertEqual(self.load()[:7], (0, 0, 0, 0, 0, 0, 0))

    def test_each_save_starts_a_new_stretch(self):
        self.ready()
        self.block(0.5); self.block(0.25)
        text = self.save_text()
        self.assertIn("LOAD render blocks=2 avg=37.5% peak=50.0% <50=1 50-80=1 80-100=0 100+=0 missed=0", text)
        self.assertRegex(text, r"AUDIO blocks=\d+ events=\d+ waited=\d+ timeouts=\d+ max_polls=\d+ wrong_half=\d+ head_min=-?\d+ gap_under=\d+ gap_over=\d+ gap_max=\d+")
        self.assertRegex(text, r"M7MEM heap free=\d+ min=\d+ chunks=\d+ largest=\d+ bad=0 \| dtcm used=\d+")
        self.hb()                                        # the M7 acts on the request at its next publish
        self.block(0.125)
        self.assertIn("LOAD render blocks=1 avg=12.5% peak=12.5%", self.save_text())

    def test_the_run_before_a_reset_is_reported_at_boot(self):
        self.ready()
        self.block(0.5)
        w32(self.u, M4H, 0)                              # a reset: nothing was saved
        self.tick(2000)
        self.assertRegex(self.save_text(), r"as found at boot[^\n]*\r\nLOAD render blocks=1 avg=50\.0%")

    def test_a_warm_reset_leaves_the_previous_run_for_the_boot_print(self):
        self.ready()
        w32(self.u, S.AUDIO_BLOCK_STATS, 900); w32(self.u, S.AUDIO_BLOCK_STATS + 4, 7); w32(self.u, S.AUDIO_BLOCK_STATS + 0x18, 0xFFFFFF00)
        self.block(0.5); self.hb()
        for a in (S.AUDIO_BLOCK_STATS, S.AUDIO_BLOCK_STATS + 4, S.AUDIO_BLOCK_STATS + 0x18):   # stock's BSS is zero after the reset
            w32(self.u, a, 0)
        w32(self.u, S.AUDIO_BLOCK_STATS, 4)
        self.block(0.25); self.hb()                      # the engine runs before the M4's first pass
        w32(self.u, M4H, 0)
        self.tick(2000)
        text = self.save_text()
        self.assertRegex(text, r"as found at boot[^\n]*\r\nLOAD render blocks=1 avg=50\.0%")
        self.assertRegex(text, r"as found at boot[^\n]*\r\n[^\n]*\r\nAUDIO blocks=900 [^\n]*wrong_half=7 head_min=-256 ")

    def test_new_boot_clears_it_once_the_generation_moves(self):
        self.ready()
        w32(self.u, S.AUDIO_BLOCK_STATS, 900); self.block(0.5); self.hb()
        w32(self.u, S.AUDIO_BLOCK_STATS, 4)
        self.tick(500)                                   # a reset (the tick went back): the M4 prints it, then a new generation
        call(self.u, SYM7["diag_put"], 1, 0, 0, 0, 0)    # first post moves GEN
        self.hb()
        self.assertEqual(struct.unpack("<I", self.u.mem_read(self.D0C, 4))[0], 4)
        self.assertEqual(self.load()[0], 0)

    def test_avg_saturates_rather_than_wraps(self):
        self.ready()
        self.block(0.5)
        w32(self.u, self.D0L + 4, 0xFFFFFFF0)
        self.block(0.5)
        self.assertEqual(self.load()[1], 0xFFFFFFFF)
        self.block(0.5)
        self.assertEqual(self.load()[1], 0xFFFFFFFF)
        self.assertIn("LOAD render blocks=3 avg=- peak=50.0%", self.save_text())

    def test_missed_belongs_to_the_stretch(self):
        self.ready()
        w32(self.u, self.OBJ + 0x408, r32(self.u, self.OBJ + 0x408) + 3); self.hb(1)
        self.assertIn(" missed=1\r\n", self.save_text())
        self.hb()
        self.assertEqual(r32(self.u, self.D0C + 24), 0)

    def test_link_figures_reach_the_log(self):
        self.ready()
        L, SPI = S.CTRL_LINK, S.SPI1_HANDLE
        for a, v in ((L + 0x194, 5000), (L + 0x19C, 3), (L + 0x190, 5100), (S.LINK_REBOOTS, 3)):
            w32(self.u, a, v)
        self.u.mem_write(L + 0x1A2, struct.pack("<H", 1))
        self.u.mem_write(SPI + 0x81, bytes([5])); self.u.mem_write(S.PANEL_TYPE, bytes([3]))
        self.hb()
        w32(self.u, L + 0x194, 10); self.hb()             # a link restart zeroes the counters
        text = self.save_text()
        self.assertIn("LINK rx=10 bad=3 fw_boots=0 crc_changes=0 tx=5100 restarts=1 version=0001 reboots=3 spi=5 frame_type=3 ", text)

    def test_controller_boots_and_frame_changes_are_counted(self):
        self.ready()
        for booted, crc in ((1, 7), (1, 7), (0, 8), (0, 8), (1, 9)):
            self.u.mem_write(S.PANEL_BOOTED, struct.pack("<H", booted)); w32(self.u, S.PANEL_CRC, crc); self.block(0.1)
        self.assertIn(" fw_boots=2 crc_changes=3 ", self.save_text_published())

    def test_button_changes_are_counted_every_block(self):
        self.ready()
        self.u.mem_write(S.PANEL_BUTTONS, struct.pack("<H", 0x0002)); self.block(0.1)
        self.u.mem_write(S.PANEL_BUTTONS, struct.pack("<H", 0x0000)); self.block(0.1)
        self.u.mem_write(S.PANEL_BUTTONS, struct.pack("<H", 0x0001)); self.block(0.1)
        self.block(0.1)
        self.assertIn("btn_changes=3 btn_or=0003 btn_now=0001", self.save_text_published())

    def save_text_published(self):
        self.hb()
        return self.save_text()

    def test_each_save_also_lands_in_a_small_file(self):
        self.ready()
        self.block(0.5)
        self.save_text()
        saves = self.fs["\\BENTO_SAVES.log"].decode()
        self.assertIn("BENTO black box, firmware BE", saves, "the boot header, once")
        self.assertIn("as found at boot", saves)
        self.assertIn("LOAD render blocks=1 avg=50.0%", saves)
        self.assertIn("# saved:", saves)
        size = len(saves)
        self.hb(); self.block(0.25)
        self.save_text()
        saves = self.fs["\\BENTO_SAVES.log"].decode()
        self.assertEqual(saves.count("BENTO black box"), 1)
        self.assertEqual(saves.count("# saved:"), 2)
        self.assertIn("avg=25.0%", saves[size:])

    def test_save_text_is_never_handed_unaligned_to_the_sd_dma(self):
        self.fs["\\BENTO_SAVES.log"] = b"x"                  # file position 1: any later run is unaligned
        self.ready()
        self.block(0.5)
        self.save_text()
        self.hb(); self.block(0.25)
        self.save_text()
        saves = self.fs["\\BENTO_SAVES.log"]
        self.assertGreater(len(saves), 1024, "long enough to cross whole sectors")
        self.assertEqual(self.dma_unaligned, [])
        self.assertEqual(saves.count(b"# saved:"), 2)

    def test_a_save_never_touches_the_long_log(self):
        self.ready()
        self.block(0.5)
        self.save_text(); self.save_text()
        self.assertNotIn(LOG, self.fs, "no long write")
        self.assertNotIn(PREV, self.fs, "no copy of the old log")
        self.assertEqual(self.fs["\\BENTO_CRASHES.log"].count(b"boot fw="), 1, "one line per boot")

    def test_canaries_armed_once_and_report_changes(self):
        self.tick(1000)
        for lo, hi in self.BLOCKS:
            self.assertEqual(r32(self.u, lo), 0x32524E43, hex(lo))
            self.assertNotEqual(r32(self.u, hi - 4), 0, "filled to the top")
        self.assertIn("RAMCHK" + "".join(f" | {n} changed=0" for n, _, _ in RAMCHK) + "\r\n", self.save_text())
        name, lo, hi = RAMCHK[1]                          # 1.6.1: d2a; 1.6.8: the block above the slice
        w32(self.u, lo + 0x100, 1); w32(self.u, hi - 4, 1)
        self.assertIn(f"{name} changed=2 low={lo + 0x100:08x} high={hi - 4:08x}", self.save_text())
        w32(self.u, M4H, 0)                               # a reset: the fill isn't redone
        self.tick(2000)
        self.assertEqual(r32(self.u, lo + 0x100), 1, "no re-fill hides what happened")

    def test_ranges_stay_clear_of_their_neighbours(self):
        if S.has("RAMCHK_D2A"):                           # 1.6.1: slice in D2
            near = (S.RAMCHK_AXI - 4, S.RAMCHK_AXI_END, S.RAMCHK_D2A - 4, D2 + 0x1FFC, S.D2_SLICE_END, D2 + 0xFC0, D2 + 0xFFC)
            planted = near[:3] + (D2,) + near[3:]
        else:                                             # 1.6.8: axi block | slice | top block, all AXI
            near = (S.RAMCHK_AXI - 4, D2 + 0x40, D2 + 0x1FFC, S.D2_SLICE_END, D2 + 0xFC0, D2 + 0xFFC)
            planted = near
        for a in planted:
            w32(self.u, a, 0x11223344)
        self.tick(1000)
        for a in near:
            self.assertEqual(r32(self.u, a), 0x11223344, hex(a))


class BaseFirmwareChecks(unittest.TestCase):
    """build.py's list of what the Region's place depends on: stock passes, each change is refused."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build
        cls.b = build

    PLANT = BASE + S.size - 0xBC   # a word near the end of stock's image that no check looks at (1.6.1: 0x081B3E80)

    def refused(self, addr, data, want):
        img = bytearray(CH10)
        img[addr - BASE:addr - BASE + len(data)] = data
        with self.assertRaises(SystemExit) as ctx:
            self.b.check_region_assumptions(img, S)
        self.assertIn(want, str(ctx.exception))

    def test_stock_passes(self):
        self.b.check_region_assumptions(CH10, S)

    def test_changed_pool_start_is_refused(self):
        self.refused(S.POOL_INIT_FLASH, struct.pack("<I", 0x04000000), "starting values")
        self.refused(S.POOL_INIT_FLASH + 4, struct.pack("<I", 0xC2000000), "starting values")   # addr-ok: a changed value

    def test_changed_allocator_is_refused(self):
        self.refused(S.ALLOC_BUMP + 8, bytes(2), "allocator")

    def test_new_user_of_the_counters_is_refused(self):
        self.refused(self.PLANT, struct.pack("<I", S.POOL_LEFT_RAM), "counters")
        self.refused(self.PLANT, struct.pack("<I", S.POOL_CUR_RAM), "counters")

    def test_changed_mpu_setup_is_refused(self):
        self.refused(S.MPU_SETUP_END - 4, bytes(4), "MPU")

    def test_a_pointer_into_the_reserved_range_is_refused(self):
        self.refused(self.PLANT, struct.pack("<I", S.REGION), "literal")
        self.refused(self.PLANT, struct.pack("<I", S.EXT_POOL_END - 4), "literal")

    def test_movt_into_the_reserved_range_is_refused(self):
        self.refused(self.PLANT + 2, movt(0, S.REGION >> 16), "movt")       # movt r0, #(top half of the Region)

    def test_the_pool_end_word_itself_is_fine(self):
        self.assertGreaterEqual(CH10.count(struct.pack("<I", S.EXT_POOL_END)), 1)   # stock's own, not in the range
        self.b.check_region_assumptions(CH10, S)

def movt(rd, imm16):
    """Thumb-2 MOVT rd, #imm16 (encoding T1), as bytes."""
    hw1 = 0xF2C0 | ((imm16 >> 11) & 1) << 10 | (imm16 >> 12) & 0xF
    hw2 = ((imm16 >> 8) & 7) << 12 | rd << 8 | imm16 & 0xFF
    return struct.pack("<HH", hw1, hw2)

assert movt(0, 0xC5FF) == bytes.fromhex("ccf2ff50")   # the encoding check_region_assumptions decodes

@unittest.skipIf(M7_ONLY, "M7 feature code (m7_polyat.c) not in this image")
class ScanCost(unittest.TestCase):
    """v3.66: the region is SDRAM, which the M7 then read uncached. Scanning every slot on every
    playing track's block turned a clean SPACED into a crackling one (BE3.64). Cached since v3.70,
    but a read of a cold line is still an SDRAM access, so the count keeps its meaning."""
    REGION = S.REGION

    def test_nothing_playing_costs_few_sdram_reads(self):
        u = emu(DIAG)
        w32(u, D2 + 0x1E0, MAGIC); w32(u, D2 + 0x1E4, self.REGION); w32(u, D2 + 0x1E8, 0x3000); w32(u, D2 + 0x1EC, 7)
        call(u, SYM7["curve_emit"], 1, 0, 0, 128)        # first call zeroes the region
        w32(u, self.REGION + 48, 0x24060000)             # midi_q: an External note has been seen (addr-ok: harness)
        reads = []
        u.hook_add(UC_HOOK_MEM_READ, lambda uc, acc, a, sz, v, _: reads.append(a), begin=self.REGION, end=self.REGION + 0x3000)
        u.ctl_flush_tb()                                 # code that already ran doesn't see a later hook
        call(u, SYM7["curve_emit"], 1, 0, 0, 128)
        self.assertLess(len(reads), 30, "curve_emit, no players")

    def test_slots_are_found_only_up_to_the_high_water_mark(self):
        u = emu(DIAG)
        w32(u, D2 + 0x1E0, MAGIC); w32(u, D2 + 0x1E4, self.REGION); w32(u, D2 + 0x1E8, 0x3000); w32(u, D2 + 0x1EC, 7)
        call(u, SYM7["curve_emit"], 1, 0, 0, 128)
        self.assertEqual(r32(u, D2 + 0xF60), 0)
        self.assertEqual(r32(u, D2 + 0xF64), 0)


class NoCardWrites(FatFsRig):
    """v3.21 default: the black box never touches the card (stock's FatFS and SD
    driver take no locks; any access from our code risked re-mounting the volume
    under stock's open files). Diagnostics stay in reset-surviving RAM."""
    live = False

    def test_no_fatfs_calls_ever(self):
        self.tick(1000); self.send(0x5B)
        for ms in (3000, 21000, 40000, 60000):
            self.tick(ms)
        w32(self.u, 0x58004050 + 24 * 4, r32(self.u, 0x58004050 + 24 * 4) + 1)   # new stock crash record
        self.tick(61000); self.tick(90000)
        self.tick(50); self.tick(25000)                    # reset and a new boot
        self.assertEqual(self.writes, [], "no f_open of any kind")

    def test_dump_after_save_writes_once_per_save(self):
        SAVES = "\\BENTO_SAVES.log"
        self.tick(1000)
        call(self.u, SYM4["dump_after_save"])
        self.assertIn("# saved: m7 dropped=", self.fs[SAVES].decode())
        if "analog" not in MAN.get("omitted", []):    # S6a (X6): the synth UI's line, through its entry table
            self.assertRegex(self.fs[SAVES].decode(), r"\nANALOG area not taken keys=\S+ tracks=0 \|[^\n]*\r\n",
                             "the M7 has not taken the synth area here")
        n = len(self.writes)
        self.tick(2000)
        self.assertEqual(len(self.writes), n, "no writes between saves")
        call(self.u, SYM4["dump_after_save"])
        self.assertEqual(self.fs[SAVES].count(b"# saved:"), 2, "the next save appends")

    def sidecar_rig(self, samples):
        u = self.u
        pool, table, lst = 0xC6180000, 0xC61C0000, 0xC61D0000  # addr-ok: harness
        u.mem_write(pool, struct.pack("<H", len(samples)) + bytes(samples))
        w32(u, table + 4, 0)
        u.mem_write(lst, struct.pack("<H", 1))
        u.mem_write(D2 + 0x1840, struct.pack("<14I", 0x31565243, 1, pool, 8, table, 2, 0, 0, lst, 1,
                                            0, 0, 0, 0xC61E0000))       # wbuf: SDRAM write buffer (addr-ok: harness)

    @staticmethod
    def decode(blob):
        assert blob[:4] == b"BNTC"
        ver, count = struct.unpack_from("<HH", blob, 4); at = 8; out = []
        for _ in range(count):
            n, ln = struct.unpack_from("<HH", blob, at); at += 4
            data, prev = [], None
            for b in blob[at:at + ln]:
                if b < 0x80: data.append(b); prev = b
                elif b < 0xC0: data += [prev] * ((b & 0x3F) + 1)
                else:
                    prev += ((b >> 3) & 7) - 4; data.append(prev)
                    prev += (b & 7) - 4; data.append(prev)
            at += ln; out.append((n, data))
        return ver, out

    def path(self, text):
        self.u.mem_write(0x24070400, text.encode() + b"\0")  # addr-ok: harness
        return 0x24070400  # addr-ok: harness

    def test_sidecar_round_trip_next_to_project_xml(self):
        samples = [10, 10, 10, 12, 14, 15, 15, 50, 49, 48, 48, 48, 48, 0] * 80   # > one 4 KB buffer
        self.tick(1000); self.sidecar_rig(samples)
        # Run on a D2 stack like the real UI task (0x30037CF0..0x300390F0), so a buffer
        # on the stack would show up as a D2 address below.
        self.u.reg_write(UC_ARM_REG_R0, self.path(r"\Projects\X\project.xml"))
        self.u.reg_write(UC_ARM_REG_SP, S.M4_STACK_BOTTOM + 0x1310); self.u.reg_write(UC_ARM_REG_LR, SENT | 1)  # addr-ok: harness
        self.u.emu_start(SYM4["dump_after_save"] | 1, SENT, count=2000000)
        blob = self.fs[r"\Projects\X\curves.bnt"]
        ver, curves = self.decode(blob)
        self.assertEqual((ver, curves), (1, [(len(samples), samples)]))
        nmod = 8 + 8 * 12                                           # v3.59: "NMOD" u16 8, u16 12, 8 records
        self.assertEqual(blob[-nmod:-nmod + 8], b"NMOD" + struct.pack("<HH", 8, 12), "Note Mod chunk")
        blob = blob[:-nmod]
        mods = 8 + 8 * 232                                          # v3.46: "MODS" u16 8, u16 232, 8 records
        self.assertEqual(blob[-mods - 38:-mods - 32], b"TRKS" + struct.pack("<H", 16), "per-track AT settings trailer")
        self.assertEqual(blob[-mods:-mods + 8], b"MODS" + struct.pack("<HH", 8, 232), "External modulation chunk")
        self.assertLess(len(blob) - mods - 38, 8 + 4 + len(samples), "curves smaller than raw")
        self.assertEqual(r32(self.u, D2 + 0x1840 + 36), 0, "save list cleared")
        self.assertIn("sidecar status=4 curves=1", self.fs["\BENTO_SAVES.log"].decode())
        d2 = [hex(b) for b in self.write_bufs if 0x30000000 <= b < 0x30080000 or b < 0x24000000]
        self.assertEqual(d2, [], "every f_write buffer must be DMA-reachable (not D2 SRAM / the M4 stack)")

    def test_no_sidecar_for_other_files(self):
        self.tick(1000); self.sidecar_rig([1, 2, 3])
        call(self.u, SYM4["dump_after_save"], self.path(r"\Patches\P\patch.xml"))
        self.assertFalse(any(k.endswith("curves.bnt") for k in self.fs))
        self.assertEqual(r32(self.u, D2 + 0x1840 + 36), 0, "save list cleared anyway")

    def test_cold_boot_with_garbage_in_reserved_ram(self):
        """v3.25-v3.31 boot loop: init() printed the previous boot's counters, which
        followed CFG->region (reset-surviving D2 RAM) checking only for non-zero. After
        a cold start D2 holds its power-up pattern: the M4 faulted before re-initialising
        CFG, and every warm reset kept the same garbage."""
        for junk in (0xDEADBEE0, 0x00000004, 0xFFFFFFF0):
            self.setUp()
            self.u.mem_write(D2 + 0x1E0, struct.pack("<8I", 0x5A5A5A5A, junk, 0x1234, 7, 9, junk, junk, junk))
            self.tick(1000)                                   # first poll: init must not fault
            self.assertEqual(r32(self.u, M4H), MAGIC, "black box up")

    def test_header_still_built_in_ram(self):
        self.tick(1000)
        buf, n = r32(self.u, M4H + 12), r32(self.u, M4H + 16)
        self.assertIn(b"BENTO black box, firmware", bytes(self.u.mem_read(buf, n)))

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
