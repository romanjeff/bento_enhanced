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
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100           # return sentinel (never executed: emu stops on reaching it)
STACK = 0x2407F000
LOG_LO, LOG_HI = 0x24048400, 0x24048DA0   # our reserved block (heap end lowered): logger, monitors, strike line
M7H, M4H, RECS = 0x24048500, 0x24048520, 0x240486C0
MAGIC = 0x31474144
RINGSTAT = 0x24048600
RTC_BKP = 0x58004050
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]

CH10 = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
DIAG = (OUT / "bento1.bin").read_bytes()
MAN = json.loads((OUT / "manifest.json").read_text())
SYM7 = {k: int(v, 16) for k, v in MAN["m7_symbols"].items()}
SYM4 = {k: int(v, 16) for k, v in MAN["m4_symbols"].items()}

def emu(img):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")           # b .
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x200000), (0xE0000000, 0x100000),
                 (0x58000000, 0x10000),   # RTC (backup registers hold stock's crash record)
                 (0x40015000, 0x2000), (0x40020000, 0x1000)):  # SAI1-3, DMA2 (audio DMA monitor)
        u.mem_map(a, n)
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

def call(u, fn, *args, limit=200000):
    for i, v in enumerate(args):
        u.reg_write(UC_ARM_REG_R0 + i, v)
    u.reg_write(UC_ARM_REG_SP, STACK)
    u.reg_write(UC_ARM_REG_LR, SENT | 1)
    u.emu_start(fn | 1, SENT, count=limit)
    return u.reg_read(UC_ARM_REG_R0)

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
        self.assertEqual(DIAG[0x0818BAD4 - BASE:0x0818BAD4 - BASE + 8], label.ljust(8, bytes(1)),
                         "boot screen copies the first 6 bytes of this 8-byte slot")
        self.assertEqual(CH10[0x0818BAD4 - BASE:0x0818BAD4 - BASE + 8], b"1.6.1" + bytes(3), "stock string")

    def test_heap_end_lowered_only_at_the_three_literals(self):
        for a in (0x08040588, 0x080A3BDC, 0x080A3E94):
            self.assertEqual(struct.unpack_from("<I", DIAG, a - BASE)[0], 0x24048400)
        for a in (0x08040514, 0x0808E19C, 0x080A547C):   # stack bottom / sentinels: unchanged
            self.assertEqual(struct.unpack_from("<I", DIAG, a - BASE)[0], 0x24048DA0)

class Trampolines(unittest.TestCase):
    HOOKS = [  # site, resume, r1 points at an event of this type
        (0x08059980, 0x08059984, 0xAC),
        (0x08065608, 0x0806560C, 0x5B),
        (0x08082640, 0x08082644, 0x5B),
        (0x080655A0, 0x080655A4, None),
    ]

    def run_site(self, img, site, resume, ev_type, ready):
        u = emu(img)
        if ready:
            m4_ready(u)
        ev = 0x24070000
        u.mem_write(ev, bytes([ev_type or 0, 0, 0, 0]) + bytes(range(1, 21)))
        vals = [0x11111111 * (i + 1) & 0xFFFFFFFF for i in range(13)]
        vals[0], vals[1], vals[2] = 0x24071000, ev if ev_type is not None else 0, 0x24072000
        for r, v in zip(REGS[:13], vals):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.reg_write(UC_ARM_REG_LR, 0x0805ABC1)
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
                    # Ignore our log slice and dead stack below the (identical) final SP:
                    # pushed-then-popped trampoline scratch is not observable state.
                    sp = a[0][13] - 0x24000000
                    dead = STACK - 0x24000000 - 0x1000
                    o1, o2 = LOG_LO - 0x24000000, LOG_HI - 0x24000000
                    self.assertEqual(a[2][:o1], b[2][:o1], "RAM below our slice differs")
                    self.assertEqual(a[2][o2:dead], b[2][o2:dead], "RAM above our slice differs")
                    self.assertEqual(a[2][sp:], b[2][sp:], "live stack differs")
                    n = len(records(b[3])) if ready else 0
                    self.assertEqual(n, 1 if ready and t is not None else 0)  # queue reads: heartbeat only

class StrikePeak(unittest.TestCase):
    """h_input keeps a per-pad strike: press counter + peak of the first pressure samples."""
    def ev(self, u, t, pad, raw):
        at = 0x24070200
        u.mem_write(at, struct.pack("<6I", t, 0, 0xFFFFFFFE, pad, raw, 0))
        call(u, SYM7["h_input"], 0x24071000, at)

    def strikes(self, u):
        raw = bytes(u.mem_read(0x24048D60, 32))
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

class NativeFunctions(unittest.TestCase):
    def pair(self):
        a, b = emu(CH10), emu(DIAG)
        m4_ready(b)
        return a, b

    def test_msg_send_with_lab_hook(self):
        a, b = self.pair()
        q, ev = 0x24060000, 0x24070000
        for u in (a, b):
            u.mem_write(ev, bytes([0x5B, 0, 0, 0]) + struct.pack("<5I", 0, 1, 2, 60, 100))
            call(u, 0x08065600, q, ev)
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
        q, ev = 0x24060000, 0x24070000
        b.mem_write(ev, bytes([0x3E]) + bytes(23))
        call(b, 0x08065600, q, ev)
        self.assertEqual(len(records(b)), 0)

    def test_ring_post(self):
        a, b = self.pair()
        obj, ev = 0x24050000, 0x24070000
        for u in (a, b):
            u.mem_write(ev, bytes([0x5B, 0, 0, 0]) + bytes(range(20)))
            call(u, 0x08082640, obj, ev)
        self.assertEqual(bytes(a.mem_read(obj + 0x21C0, 0x1C0)), bytes(b.mem_read(obj + 0x21C0, 0x1C0)))
        recs = records(b)
        self.assertEqual([r[1] & 0xFFFF for r in recs], [3])
        # clock ticks are filtered
        b.mem_write(ev, bytes([0x61]) + bytes(23))
        call(b, 0x08082640, obj, ev)
        self.assertEqual(len(records(b)), 1)

    def test_ring_fill_and_overflow(self):
        a, b = self.pair()
        obj, ev = 0x24050000, 0x24070000
        b.mem_write(ev, bytes([0x61]) + bytes(23))       # a clock tick: normally not logged
        w32(b, obj + 0x2360, 20); w32(b, obj + 0x2340, 9)  # 11 unread
        call(b, 0x08082640, obj, ev)
        self.assertEqual(len(records(b)), 0)
        self.assertEqual(r32(b, RINGSTAT), 11)            # max_fill
        w32(b, obj + 0x2340, 5)                           # 16 unread: this post overwrites
        call(b, 0x08082640, obj, ev)
        recs = records(b)
        self.assertEqual(len(recs), 1, "an overwrite is always logged")
        self.assertEqual(recs[0][10], 0x110)              # extra = 0x100 | fill
        self.assertEqual((r32(b, RINGSTAT), r32(b, RINGSTAT + 4), r32(b, RINGSTAT + 8)), (16, 1, 2))

    def test_queue_reads_are_a_heartbeat(self):
        a, b = self.pair()
        q, out, ev = 0x24060000, 0x24070100, 0x24070000
        b.mem_write(ev, bytes([0x5B]) + bytes(23))
        call(b, 0x08065600, q, ev)        # any logged event initialises the M7 side
        w32(b, q + 0x600, 0)
        for _ in range(3):
            call(b, 0x080655A0, q, 0, out)
        call(b, 0x080655A0, q, 1, out)    # idx != 0: not a new pass
        self.assertEqual(r32(b, RINGSTAT + 20), 3)
        self.assertEqual(len(records(b)), 1, "queue reads are not logged")

    def test_stack_minimums(self):
        a, b = self.pair()
        ev = 0x24070000
        b.mem_write(ev, bytes([0x5B]) + bytes(23))
        call(b, 0x08065600, 0x24060000, ev)                 # first call initialises the counters...
        call(b, 0x08065600, 0x24060000, ev)                 # ...later calls sample SP (outside the MSP range)
        self.assertEqual(r32(b, RINGSTAT + 12), 0xFFFFFFFF)  # min_msp untouched
        self.assertLess(r32(b, RINGSTAT + 16), STACK)        # min_sp recorded
        for i, v in enumerate((0x24060000, ev)):
            b.reg_write(UC_ARM_REG_R0 + i, v)
        b.reg_write(UC_ARM_REG_SP, 0x2404A000)               # on the M7 main stack
        b.reg_write(UC_ARM_REG_LR, SENT | 1)
        b.emu_start(0x08065600 | 1, SENT, count=200000)
        msp = r32(b, RINGSTAT + 12)
        self.assertTrue(0x24048DA0 <= msp < 0x2404A000, hex(msp))

LOG, PREV = "\\BENTO_DIAG.log", "\\BENTO_DIAG_prev.log"

class M4EndToEnd(unittest.TestCase):
    """M4 flush path against a tiny emulated FatFS (path -> bytes, modes honoured)."""
    def setUp(self, previous=None):
        u = self.u = emu(DIAG)
        w32(u, 0x3000004C, 0xC6000000); w32(u, 0x30000048, 0xC6200000)
        self.fs = {} if previous is None else {LOG: previous}
        self.open = {}      # FIL address -> [path, position]
        self.writes = []    # (path, mode, bytes written in that open)
        def cstr(uc, a):
            s = b""
            while True:
                c = bytes(uc.mem_read(a, 1))
                if c == b"\0": return s.decode()
                s += c; a += 1
        def hook(uc, addr, size, _):
            r = lambda i: uc.reg_read(UC_ARM_REG_R0 + i)
            ret = None
            if addr == 0x08101C78: ret = 0                       # original loop function: no-op
            elif addr == 0x08120148:                              # f_open(fil, path, mode)
                path, mode = cstr(uc, r(1)), r(2)
                if mode == 0x01:
                    ret = 0 if path in self.fs else 4             # FR_NO_FILE
                elif mode == 0x0A: self.fs[path] = b""; ret = 0   # create always
                elif mode == 0x32: self.fs.setdefault(path, b""); ret = 0   # append
                else: ret = 19                                    # anything else: invalid parameter
                if ret == 0:
                    self.open[r(0)] = [path, 0]
                    self.writes.append([path, mode, b""])
            elif addr == 0x08120464:                              # f_read(fil, buf, n, &got)
                f = self.open[r(0)]; data = self.fs[f[0]][f[1]:f[1] + r(2)]
                uc.mem_write(r(1), data); w32(uc, r(3), len(data)); f[1] += len(data); ret = 0
            elif addr == 0x08120604:                              # f_write(fil, buf, n, &done)
                f = self.open[r(0)]; data = bytes(uc.mem_read(r(1), r(2)))
                self.fs[f[0]] += data; self.writes[-1][2] += data; w32(uc, r(3), len(data)); ret = 0
            elif addr == 0x08120974: self.open.pop(r(0), None); ret = 0   # f_close
            if ret is not None:
                uc.reg_write(UC_ARM_REG_R0, ret)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in (0x08101C78, 0x08120148, 0x08120464, 0x08120604, 0x08120974):
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)

    @property
    def files(self):
        """Contents written to the log in each open (what each flush wrote)."""
        return [w[2] for w in self.writes if w[0] == LOG and w[1] != 0x01]

    def tick(self, ms):
        w32(self.u, 0x3001FE2C, ms)
        call(self.u, SYM4["m4_tick"], 0, 0, limit=2000000)

    def send(self, t, lr=0x0805D5A1):
        ev = 0x24070000
        self.u.mem_write(ev, bytes([t, 0, 0, 0]) + struct.pack("<5I", 0, 1, 2, 60, 100))
        self.u.reg_write(UC_ARM_REG_R2, lr)
        call(self.u, SYM7["h_send"], 0x24060000, ev, lr)

    def test_flow(self):
        self.tick(1000)                                  # first call: init + allocate
        self.assertEqual(r32(self.u, M4H), MAGIC)
        self.assertEqual(r32(self.u, M4H + 12), 0xC6000000, "buffer from the native allocator")
        self.send(0x5B); self.send(0x5C)
        self.tick(1100)                                  # drains, but pads not idle yet
        self.assertEqual(self.files, [])
        self.tick(2700)                                  # idle >= 1500 ms -> flush
        self.assertEqual(len(self.files), 1)
        text = self.files[0].decode()
        self.assertIn("BENTO black box, firmware BE", text)
        self.assertIn("# m7 dropped=0 | poly sent=0 budget_drops=0 stolen=0 retrig=0 swallowed=0"
                      " | midi ring max_fill=0 overflows=0 | m7 stack min_msp=", text)
        self.assertEqual(text.count(" SEND "), 2)
        self.assertIn("lr=0805d5a1", text)
        self.assertNotIn("!!!", text, "no alarms in a normal session")
        self.tick(5000)                                  # nothing new -> no write at all
        self.assertEqual(len(self.files), 1)

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
        for i, v in {24: 3, 25: 5, 31: 0x080DA2B1, 27: 2, 8: 0x2404CC00}.items():
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
        w32(self.u, RTC_BKP + 4 * 24, 1); w32(self.u, RTC_BKP + 4 * 31, 0x0808CC5A)
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
        w32(self.u, RTC_BKP + 4 * 31, 0x08122BC4); w32(self.u, RTC_BKP + 4 * 27, 1)
        self.tick(1000)
        self.assertEqual(self.fs[hist], b"older history\r\n", "not before the card is in use")
        self.tick(2600)                                  # first log write of this boot
        lines = self.fs[hist].decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[1], r"^boot fw=BE[0-9.]+ rtc=\d{6}-\d{6} gen=1 ms=2600 count=86 cause=00000005 pc=08122bc4 core=00000001")
        self.tick(5000)                                  # later flushes: no new history line
        self.assertEqual(len(self.fs[hist].decode().splitlines()), 2)
        w32(self.u, RTC_BKP + 4 * 24, 87)
        self.tick(50); self.tick(1700)                   # reboot, first write of boot 2
        lines = self.fs[hist].decode().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertRegex(lines[2], r"^boot fw=BE[0-9.]+ rtc=\S+ gen=2 ms=1700 count=87")
        self.assertTrue(all(w[1] == 0x32 for w in self.writes if w[0] == hist), "append only, never truncated")

    def test_crash_history_live_crash_line(self):
        hist = "\\BENTO_CRASHES.log"
        self.tick(1000)
        w32(self.u, RTC_BKP + 4 * 24, 1); w32(self.u, RTC_BKP + 4 * 31, 0x0808CC5A)
        self.tick(1050)
        lines = self.fs[hist].decode().splitlines()
        self.assertRegex(lines[-1], r"^live fw=BE[0-9.]+ rtc=\S+ gen=1 ms=1050 count=1")
        self.assertIn("pc=0808cc5a", lines[-1])

    # --- audio DMA monitor (DMA2 streams 5 and 7, as stock's own main-loop check)
    S5, S7 = 0x40020488, 0x400204B8

    def stream(self, base, cr=1, ndtr=100, par=0x40015820):
        for i, v in enumerate((cr, ndtr, par, 0x30000000, 0x30001000, 0x21)):
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
        self.assertRegex(self.fs["\\BENTO_CRASHES.log"].decode().splitlines()[-1], r"^stall fw=BE[0-9.]+ rtc=\S+ gen=1 ms=1040")
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
        w32(self.u, 0xE0001004, cyc)
        call(self.u, SYM7[fn], 0)

    def test_m7_stall_record_written_once_and_printed_next_boot(self):
        self.tick(1000)                                  # M4 publishes boot gen 1
        w32(self.u, 0xE000E108, 0x50); w32(self.u, 0xE000E208, 0x40)   # IRQ 68/70 enabled, 70 pending
        w32(self.u, 0x40020404, 0x00200000)              # HISR: S7 half-transfer flag set
        self.m7("stall_progress", 1000)
        self.m7("stall_check", 1000 + 99 * 480000)       # 99 ms: not yet
        self.assertNotEqual(r32(self.u, 0x240484A0), 0x38544C53)
        self.m7("stall_check", 1000 + 101 * 480000)      # 101 ms: snapshot
        self.assertEqual(r32(self.u, 0x240484A0), 0x38544C53)
        w32(self.u, 0x40020404, 0)
        self.m7("stall_check", 1000 + 200 * 480000)      # once per boot
        self.assertEqual(r32(self.u, 0x240484A0 + 4 * 15), 0x00200000, "first snapshot kept (hisr)")
        self.tick(50); self.tick(1700)                   # watchdog reset, next boot
        boot = self.files[-1].decode()
        self.assertIn("M7 audio stall record (from RAM): gen 1, no audio progress for 101 ms", boot)
        self.assertIn("irq64-95 en/pend/act=00000050/00000040/00000000", boot)
        self.assertIn("HISR=00200000", boot)
        self.tick(50); self.tick(1700)
        self.assertNotIn("M7 audio stall record", self.files[-1].decode(), "printed once")

    def test_m7_progress_rearms_after_a_reboot(self):
        self.tick(1000)
        self.m7("stall_progress", 0); self.m7("stall_check", 101 * 480000)
        self.tick(50); self.tick(1700)                   # gen 2
        self.m7("stall_progress", 0)
        self.assertEqual(r32(self.u, 0x2404848C), 0, "snapped flag reset for the new boot")

    def test_heartbeat_stall_is_reported(self):
        self.tick(1000)
        self.send(0x5B)                                  # M7 side live
        for _ in range(5):
            call(self.u, 0x080655A0, 0x24060000, 0, 0x24070100)
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

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
