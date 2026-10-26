# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for the trigger-condition port (bento-community-lab T2) on stock 1.6.1.

    python test_conditions.py <stock 1.6.1 bento1.bin> <m1 build dir>

M7: decision table for all 35 A:B values, pass counting (increment, restart,
owner/row change), native values untouched; the patched gate and loop hook on
the real image behave like stock except for the condition decision.
M4: label table (100 native + 35 A:B), list-count patch, and the editor value
conversions round-trip probability <-> list index <-> condition.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
STACK = 0x2407F000
CFG = 0x240485E0
MAGIC = 0x31474144
COUNTERS = 0x24048A60
SEQ = 0x24030000
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]

STOCK = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
SYM7 = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}
LABELS = [f"{a}:{b}" for b in range(2, 9) for a in range(1, b + 1)]

def emu(img, cfg_gen=1):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    if cfg_gen:
        u.mem_write(CFG, struct.pack("<8I", MAGIC, 0, 0, cfg_gen, 0, 0, 0, 0))
    seq(u)
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

def seq(u, owner=7, row=2, restart=False):
    w32(u, SEQ + 12, owner); w32(u, SEQ + 0x86C0, row); w32(u, SEQ + 0x14, 0x80000000 if restart else 5)

def call(u, fn, *args):
    for i, v in enumerate(args):
        u.reg_write(UC_ARM_REG_R0 + i, v)
    u.reg_write(UC_ARM_REG_SP, STACK)
    u.reg_write(UC_ARM_REG_LR, SENT | 1)
    u.emu_start(fn | 1, SENT, count=200000)
    return u.reg_read(UC_ARM_REG_R0)

class Decide(unittest.TestCase):
    def test_all_35_conditions_over_8_passes(self):
        for idx, label in enumerate(LABELS):
            a, b = map(int, label.split(":"))
            u = emu(IMG)
            plays = []
            for p in range(2 * b):
                call(u, SYM7["cond_loop"], SEQ)              # first boundary: loop 0, then loop++
                plays.append(call(u, SYM7["cond_decide"], SEQ, 100 + idx))
            with self.subTest(label=label):
                self.assertEqual(plays, [1 if p % b == a - 1 else 0 for p in range(2 * b)])

    def test_native_values_are_left_to_stock(self):
        u = emu(IMG)
        for v in (0, 1, 50, 99, 135, 200, 255):
            self.assertEqual(call(u, SYM7["cond_decide"], SEQ, v), 2, v)

    def test_restart_and_clip_change_reset_the_count(self):
        u = emu(IMG)
        for _ in range(3):
            call(u, SYM7["cond_loop"], SEQ)                  # loop 0, 1, 2
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("3:3")), 1)
        seq(u, restart=True); call(u, SYM7["cond_loop"], SEQ); seq(u)   # launch/restart: loop 0
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("1:2")), 1)
        call(u, SYM7["cond_loop"], SEQ)                      # loop 1
        seq(u, row=3)                                        # another clip on the same sequence
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("1:2")), 1, "count restarts")

    def test_counters_reinitialise_per_boot_generation(self):
        u = emu(IMG, cfg_gen=1)
        u.mem_write(COUNTERS, b"\xab" * 0x200)              # RAM garbage from a previous boot
        call(u, SYM7["cond_loop"], SEQ)
        self.assertEqual(r32(u, COUNTERS), SEQ, "slot 0 claimed after the table was cleared")
        w32(u, CFG + 12, 2)                                  # M4 rebooted
        call(u, SYM7["cond_loop"], SEQ + 0x1000)
        self.assertEqual(r32(u, COUNTERS), SEQ + 0x1000, "table cleared again for the new generation")

class Hooks(unittest.TestCase):
    def gate(self, img, value, loops):
        u = emu(img)
        for _ in range(loops):
            if img is IMG:
                call(u, SYM7["cond_loop"], SEQ)
        sp = STACK - 0x200
        frame = bytearray(0x40); frame[0x29] = value
        u.mem_write(sp, bytes(frame))
        for i, r in enumerate(REGS[:13]):
            u.reg_write(r, 0x01010101 * (i + 1))
        u.reg_write(UC_ARM_REG_R8, SEQ); u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, 0x08060001)
        u.emu_start(0x08060B64 | 1, 0x08060B68, count=200000)
        return u

    def test_native_probability_path_is_stock(self):
        a, b = self.gate(STOCK, 50, 0), self.gate(IMG, 50, 0)
        self.assertEqual([a.reg_read(r) for r in REGS], [b.reg_read(r) for r in REGS])
        self.assertEqual(b.reg_read(UC_ARM_REG_R3), 50)

    def test_condition_play_and_skip(self):
        val = 100 + LABELS.index("1:2")
        u = self.gate(IMG, val, 1)                           # loop 0 -> play
        self.assertEqual(u.reg_read(UC_ARM_REG_PC), 0x08060B68)
        self.assertEqual(u.reg_read(UC_ARM_REG_R3), 0, "play = native 'always' (0)")
        self.assertEqual(u.reg_read(UC_ARM_REG_R8), SEQ)
        # loop 1 -> skip: execution goes to stock's skip path instead of resuming.
        u = emu(IMG)
        call(u, SYM7["cond_loop"], SEQ); call(u, SYM7["cond_loop"], SEQ)
        sp = STACK - 0x200
        frame = bytearray(0x40); frame[0x29] = val
        u.mem_write(sp, bytes(frame))
        u.reg_write(UC_ARM_REG_R8, SEQ); u.reg_write(UC_ARM_REG_SP, sp)
        u.emu_start(0x08060B64 | 1, 0x080609E2, count=200000)
        self.assertEqual(u.reg_read(UC_ARM_REG_PC), 0x080609E2)
        self.assertEqual(u.reg_read(UC_ARM_REG_SP), sp, "stack balanced on the skip path")

    def test_loop_hook_is_stock_plus_count(self):
        res = []
        for img in (STOCK, IMG):
            u = emu(img)
            for i, r in enumerate(REGS[:13]):
                u.reg_write(r, 0x02020202 * (i + 1))
            u.reg_write(UC_ARM_REG_R0, SEQ); u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, 0x08061001)
            u.emu_start(0x08061740 | 1, 0x08061744, count=200000)
            sp = u.reg_read(UC_ARM_REG_SP)
            res.append(([u.reg_read(r) for r in REGS], bytes(u.mem_read(sp, STACK - sp)), u))
        self.assertEqual(res[0][0], res[1][0])
        self.assertEqual(res[0][1], res[1][1], "pushed frame identical to stock")
        self.assertEqual(r32(res[1][2], COUNTERS), SEQ, "pass counted")

class M4UI(unittest.TestCase):
    def test_label_table(self):
        ptr = struct.unpack_from("<I", IMG, 0x08165F8C - BASE)[0]
        ptrs = [struct.unpack_from("<I", IMG, ptr - BASE + 4 * i)[0] for i in range(135)]
        native = [struct.unpack_from("<I", STOCK, 0x0819F30C - BASE + 4 * i)[0] for i in range(100)]
        self.assertEqual(ptrs[:100], native)
        def cstr(a):
            o = a - BASE; return IMG[o:IMG.index(b"\0", o)].decode()
        self.assertEqual([cstr(p) for p in ptrs[100:]], LABELS)
        self.assertEqual(cstr(ptrs[99]), "Always")

    def test_count_patch(self):
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
        md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
        ins = [f"{i.mnemonic} {i.op_str}" for i in md.disasm(IMG[0x08165D40 - BASE:0x08165D44 - BASE], 0x08165D40)]
        self.assertEqual(ins, ["movs r3, #0x87", "str r3, [sp]"])

    def helper(self, site, regs, stop):
        """Run the patched editor site until it returns to stock code at `stop`."""
        u = emu(IMG)
        for r, v in regs.items():
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.emu_start(site | 1, stop, count=1000)
        return u

    def test_drum_view_round_trip(self):
        store = 0x24070000
        def show(v):    # stored probability byte -> list index (written to [r1])
            u = self.helper(0x0815C3D2, {UC_ARM_REG_R3: v, UC_ARM_REG_R1: store}, 0x0815C3D6)
            return r32(u, store)
        def set_(i):    # list index (r1) -> stored byte (r6)
            u = self.helper(0x0815C8B4, {UC_ARM_REG_R1: i}, 0x0815C8BE)
            return u.reg_read(UC_ARM_REG_R6)
        for v in list(range(1, 100)) + list(range(100, 135)):
            with self.subTest(value=v):
                self.assertEqual(set_(show(v)), v)
        self.assertEqual(set_(99), 0, "'Always' entry stores native 0")
        self.assertEqual(set_(200), 0, "out-of-range index stores native 0")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
