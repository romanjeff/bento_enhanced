# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for M1 poly aftertouch (Unicorn 2.1.3).

    python test_polyat.py <CH10 bento1.bin> <m1 build dir>

1. The External handler's patched branch leaves every non-0x0E event type
   exactly as stock (registers + RAM at stock's loop head 0x08062080).
2. A 0x0E event on a MIDI-out track posts {0x5F, 0, port, ch, note, value} to
   the queue stock's note-on producer uses; MIDI out off posts nothing.
3. Rate control: changed values only, <= 1 send per 10 ms per note with a
   later flush of held-back values, global budget of 4-burst / 400 per s.
4. M4: our 0x5F handler makes exactly the native Note On case's calls with
   status 0xA0 instead of 0x90; only jump-table slot 0x5F changed.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
STACK = 0x2407F000
CYC = 0xE0001004
ENGINE = 0x24060000              # stock posts note-ons with msg_send(ENGINE + 0x7C8, ev)
QUEUE = ENGINE + 0x7C8
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]

CH10 = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
SYM7 = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}

def emu(img):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

def track(u, port=1, ch=2, midi=1, base=0x24064000):
    u.mem_write(base, bytes(0x8800))
    u.mem_write(base + 0x31, bytes([port, ch]))
    u.mem_write(base + 0x86D1, bytes([midi]))
    return base

def queued(u):
    """Events in the native msg_send queue: 24-byte entries, count at +0x600."""
    n = r32(u, QUEUE + 0x600)
    return [struct.unpack("<6I", u.mem_read(QUEUE + 24 * i, 24)) for i in range(n)]

class PolyHook(unittest.TestCase):
    def run_branch(self, img, r3, note=60, val=50, midi=1):
        """Enter at the handler's `cmp r3,#3` (0x08062464); stop at stock's loop head."""
        u = emu(img)
        t = track(u, midi=midi)
        sp = STACK - 0x100
        u.mem_write(sp + 0x20, struct.pack("<6I", r3, 0, 0x71, note, val, 0))
        for i, r in enumerate(REGS[:13]):
            u.reg_write(r, 0x01010101 * (i + 1))
        u.reg_write(UC_ARM_REG_R3, r3)
        u.reg_write(UC_ARM_REG_R6, t)
        u.reg_write(UC_ARM_REG_R8, ENGINE)
        u.reg_write(UC_ARM_REG_SP, sp)
        u.reg_write(UC_ARM_REG_LR, 0x08062001)
        w32(u, CYC, 100_000_000)
        u.emu_start(0x08062464 | 1, 0x08062080, count=20000)
        return u, [u.reg_read(r) for r in REGS], bytes(u.mem_read(0x24000000, 0x80000))

    def test_other_types_untouched(self):
        dead = STACK - 0x24000000 - 0x1000
        for r3 in (0x05, 0x0D, 0x0F, 0x30):
            with self.subTest(type=hex(r3)):
                a, b = self.run_branch(CH10, r3), self.run_branch(IMG, r3)
                self.assertEqual(a[1], b[1])
                self.assertEqual(a[2][:dead], b[2][:dead])
                self.assertEqual(queued(b[0]), [])

    def test_0x0e_posts_poly_pressure(self):
        u, regs, _ = self.run_branch(IMG, 0x0E, note=64, val=77)
        self.assertEqual(queued(u), [(0x5F, 0, 1, 2, 64, 77)])
        self.assertEqual(regs, self.run_branch(CH10, 0x0E)[1], "registers at the loop head identical to stock")

    def test_midi_out_off_posts_nothing(self):
        self.assertEqual(queued(self.run_branch(IMG, 0x0E, midi=0)[0]), [])

class PolyRate(unittest.TestCase):
    def setUp(self):
        self.u = emu(IMG)
        self.t = track(self.u)

    def at(self, ms):
        w32(self.u, CYC, 50_000_000 + ms * 480_000)

    def press(self, note, value):
        ev = 0x24070000
        self.u.mem_write(ev, struct.pack("<6I", 0x0E, 0, 0x71, note, value, 0))
        for i, v in enumerate((self.t, ev, ENGINE)):
            self.u.reg_write(UC_ARM_REG_R0 + i, v)
        self.u.reg_write(UC_ARM_REG_SP, STACK)
        self.u.reg_write(UC_ARM_REG_LR, SENT | 1)
        self.u.emu_start(SYM7["h_poly"] | 1, SENT, count=200000)

    def sent(self):
        return [(e[4], e[5]) for e in queued(self.u)]

    def test_changed_values_only_and_100hz(self):
        self.at(0); self.press(60, 10)
        self.at(3); self.press(60, 20)       # < 10 ms since the last send: held back
        self.at(5); self.press(60, 20)
        self.assertEqual(self.sent(), [(60, 10)])
        self.at(12); self.press(61, 5)       # any later event flushes the held-back 60:20
        self.assertEqual(self.sent(), [(60, 10), (61, 5), (60, 20)])
        self.at(30); self.press(60, 20)      # unchanged value: never re-sent
        self.assertEqual(len(self.sent()), 3)

    def test_global_budget(self):
        self.at(0)
        for n in range(10):
            self.press(40 + n, 50)
        self.assertEqual(len(self.sent()), 4, "burst capped at 4")
        self.at(25)                          # tokens refill, capped at 4
        self.press(80, 1)
        self.assertEqual(len(self.sent()), 8)

class M4Handler(unittest.TestCase):
    CALLS = (0x08105D56, 0x08105C44, 0x08105C5E, 0x08105CFE, 0x08105904, 0x08105C4C, 0x08105B74)

    def trace(self, start):
        u = emu(IMG)
        log = []
        def hook(uc, addr, size, _):
            log.append((addr, uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
            uc.reg_write(UC_ARM_REG_R0, 0x30000123)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in self.CALLS:
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)
        for r, v in ((UC_ARM_REG_R5, 0x30005550), (UC_ARM_REG_R6, 0x30006660), (UC_ARM_REG_R7, 0x40),
                     (UC_ARM_REG_R8, 0x02), (UC_ARM_REG_R9, 0x01), (UC_ARM_REG_R11, 0x55)):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.emu_start(start | 1, 0x081050DE, count=2000)
        return log

    def test_same_calls_as_native_note_on_except_status(self):
        native, ours = self.trace(0x0810509A), self.trace(0x081BEC00)
        self.assertEqual(len(native), 8)  # init, status, channel, note, value, send, 2 finishing calls
        self.assertEqual(len(native), len(ours))
        i = [k for k, c in enumerate(native) if c[0] == 0x08105C44][0]
        self.assertEqual((native[i][2], ours[i][2]), (0x90, 0xA0))
        self.assertEqual(native[:i] + native[i + 1:], ours[:i] + ours[i + 1:])

    def test_only_slot_5f_changed(self):
        jt = lambda img: [struct.unpack_from("<I", img, 0x08105294 - BASE + 4 * i)[0] for i in range(9)]
        a, b = jt(CH10), jt(IMG)
        self.assertEqual(b[4], 0x081BEC01)
        self.assertEqual(a[:4] + a[5:], b[:4] + b[5:])

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
