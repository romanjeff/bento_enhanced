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
LOG_LO, LOG_HI = 0x24048500, 0x24048A00
M7H, M4H, RECS = 0x24048500, 0x24048520, 0x240486C0
MAGIC = 0x31474144
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
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x200000), (0xE0000000, 0x100000)):
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
    def test_same_size_as_ch10(self):
        # The bootloader rejected an image grown to 0x1A0800 bytes; CH10's size is known to install.
        self.assertEqual(len(DIAG), len(CH10))

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
                    self.assertEqual(n, 1 if ready and t is not None or (ready and site == 0x080655A0) else 0)

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

    def test_consume_dedupe(self):
        a, b = self.pair()
        q, out = 0x24060000, 0x24070100
        w32(b, q + 0x600, 0)
        call(b, 0x080655A0, q, 0, out)
        call(b, 0x080655A0, q, 0, out)    # same queue, same caller -> no new record
        call(b, 0x080655A0, q, 1, out)    # idx != 0 -> ignored
        self.assertEqual(len(records(b)), 1)
        self.assertEqual(records(b)[0][1] & 0xFFFF, 4)

class M4EndToEnd(unittest.TestCase):
    def setUp(self):
        u = self.u = emu(DIAG)
        w32(u, 0x3000004C, 0xC6000000); w32(u, 0x30000048, 0xC6200000)
        self.files = []
        def hook(uc, addr, size, _):
            ret = None
            if addr == 0x08101C78: ret = 0                       # original loop function: no-op
            elif addr == 0x08120148: self.cur = []; ret = 0       # f_open
            elif addr == 0x08120604:                              # f_write(fil, buf, n, &done)
                buf, n, done = (uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3))
                self.cur.append(bytes(uc.mem_read(buf, n))); w32(uc, done, n); ret = 0
            elif addr == 0x08120974: self.files.append(b"".join(self.cur)); ret = 0  # f_close
            if ret is not None:
                uc.reg_write(UC_ARM_REG_R0, ret)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in (0x08101C78, 0x08120148, 0x08120604, 0x08120974):
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)

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
        self.assertIn("BENTO M1b poly-AT + poly cap v2", text)
        self.assertIn("| poly sent=0 budget_drops=0 stolen=0 retrig=0 swallowed=0", text)
        self.assertEqual(text.count(" SEND "), 2)
        self.assertIn("lr=0805d5a1", text)
        self.assertIn("# m7 dropped=0", text)
        self.tick(5000)                                  # nothing new -> no rewrite
        self.assertEqual(len(self.files), 1)

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
