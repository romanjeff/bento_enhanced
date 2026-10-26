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
LABELS = [f"{a}:{b}" for b in range(2, 9) for a in range(1, b + 1)]          # stored 100..134
STORED = [170, 171, 172, 173] + [v for k in range(35) for v in (100 + k, 135 + k)]  # list order
SHOWN = ["1ST", "!1ST", "PRE", "!PRE"] + [l for x in LABELS for l in (x, "!" + x)]

def step_make(cond=0, hits=0, div=3, ramp=0, locks=0):
    """v2 per-step word (diag_shared.h)."""
    f = 0 if div == 3 else div + 1
    r = 0 if ramp == 0 else int(ramp / 5) + 21
    if not cond and not hits and not f and not r and not locks:
        return 0
    return (0xB6 << 24) | ((cond & 0x7F) << 17) | ((hits & 0xF) << 13) | ((f & 0xF) << 9) | (r << 3) | (locks & 7)

def fields(w):
    """(condition, hits, division, ramp) from a v1 or v2 word."""
    tag = w >> 24
    if tag not in (0xB5, 0xB6):
        return (0, 0, 3, 0)
    f = (w >> 9) & 0xF
    if tag == 0xB5:
        r = w & 0xFF; ramp = r - 256 if r > 127 else r
    else:
        r = (w >> 3) & 0x3F; ramp = (r - 21) * 5 if 1 <= r <= 41 else 0
    return ((w >> 17) & 0x7F, (w >> 13) & 0xF, f - 1 if 1 <= f <= 11 else 3, ramp)

def locks(w):
    return w & 7 if w >> 24 == 0xB6 else 0

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
                plays.append(call(u, SYM7["cond_decide"], SEQ, 100 + idx, 0))
            with self.subTest(label=label):
                self.assertEqual(plays, [1 if p % b == a - 1 else 0 for p in range(2 * b)])

    def test_native_values_are_left_to_stock(self):
        u = emu(IMG)
        for v in (0, 1, 50, 99, 174, 200, 255):
            self.assertEqual(call(u, SYM7["cond_decide"], SEQ, v, 0), 2, v)

    def test_restart_and_clip_change_reset_the_count(self):
        u = emu(IMG)
        for _ in range(3):
            call(u, SYM7["cond_loop"], SEQ)                  # loop 0, 1, 2
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("3:3", 0)), 1)
        seq(u, restart=True); call(u, SYM7["cond_loop"], SEQ); seq(u)   # launch/restart: loop 0
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("1:2", 0)), 1)
        call(u, SYM7["cond_loop"], SEQ)                      # loop 1
        seq(u, row=3)                                        # another clip on the same sequence
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100 + LABELS.index("1:2", 0)), 1, "count restarts")

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
    """Since v3.3 conditions have their own encoder: the probability list is stock's."""
    def test_probability_list_is_stock(self):
        self.assertEqual(IMG[0x08165F8C - BASE:0x08165F90 - BASE], STOCK[0x08165F8C - BASE:0x08165F90 - BASE])
        self.assertEqual(IMG[0x08165D40 - BASE:0x08165D44 - BASE], STOCK[0x08165D40 - BASE:0x08165D44 - BASE])
        self.assertEqual(IMG[0x0815C8B4 - BASE:0x0815C8B8 - BASE], STOCK[0x0815C8B4 - BASE:0x0815C8B8 - BASE], "set_drum stock")

    def helper(self, site, regs, stop):
        u = emu(IMG)
        for r, v in regs.items():
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.emu_start(site | 1, stop, count=1000)
        return u

    def test_drum_view_shows_percentages_and_clamps_legacy_values(self):
        store = 0x24070000
        def show(v):
            u = self.helper(0x0815C3D2, {UC_ARM_REG_R3: v, UC_ARM_REG_R1: store}, 0x0815C3D6)
            return r32(u, store)
        for v in range(1, 100):
            self.assertEqual(show(v), v - 1)
        for v in (100, 134, 173, 200):
            self.assertEqual(show(v), 99, "legacy condition byte shows as Always")

class Extended(unittest.TestCase):
    def run_passes(self, value, passes, b=4):
        u = emu(IMG); out = []
        for _ in range(passes):
            call(u, SYM7["cond_loop"], SEQ)
            out.append(call(u, SYM7["cond_decide"], SEQ, value, 0))
        return u, out

    def test_not_conditions_are_the_inverse(self):
        for k, label in enumerate(LABELS):
            a, b = map(int, label.split(":"))
            _, plain = self.run_passes(100 + k, 2 * b)
            _, inv = self.run_passes(135 + k, 2 * b)
            with self.subTest(label=label):
                self.assertEqual(inv, [1 - x for x in plain])

    def test_first_and_not_first(self):
        self.assertEqual(self.run_passes(170, 4)[1], [1, 0, 0, 0])
        self.assertEqual(self.run_passes(171, 4)[1], [0, 1, 1, 1])

    def test_first_again_after_restart(self):
        u, _ = self.run_passes(170, 3)
        seq(u, restart=True); call(u, SYM7["cond_loop"], SEQ); seq(u)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 170, 0), 1)

    def test_pre_follows_the_last_condition_and_is_not_updated_by_itself(self):
        u = emu(IMG); call(u, SYM7["cond_loop"], SEQ)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 172, 0), 0, "PRE false before any condition")
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 173, 0), 1)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 100, 0), 1)          # 1:2 on pass 1 -> played
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 172, 0), 1)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 173, 0), 0)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 172, 0), 1, "PRE/!PRE did not change the state")
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 135, 0), 0)          # !1:2 on pass 1 -> skipped
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 172, 0), 0)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 2, 0), 2, "native % untouched")
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 174, 0), 2, "unknown values left to stock")

    def test_pre_resets_with_the_clip(self):
        u = emu(IMG); call(u, SYM7["cond_loop"], SEQ)
        call(u, SYM7["cond_decide"], SEQ, 100, 0)
        seq(u, row=5)
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 172, 0), 0)

    # piano roll: 1-based list values (1..99 %, 100 Always); legacy values clamp to Always
    def piano_reference(self, stored):
        u = emu(IMG); sp = STACK - 0x300
        u.mem_write(sp, bytes(0x100)); u.mem_write(sp + 0x25, bytes([stored]))
        u.reg_write(UC_ARM_REG_SP, sp); u.emu_start(0x08158F1C | 1, 0x08158F26, count=1000)
        return u.reg_read(UC_ARM_REG_R3)

    def piano_edit(self, stored, delta):
        u = emu(IMG); sp = STACK - 0x300
        u.mem_write(sp, bytes(0x100)); u.mem_write(sp + 0x19, bytes([stored]))
        u.mem_write(sp + 0x84, struct.pack("<i", delta))
        u.reg_write(UC_ARM_REG_SP, sp); u.emu_start(0x08158A7C | 1, 0x08158AAC, count=1000)
        return bytes(u.mem_read(sp + 0x31, 1))[0]

    def test_piano_probability_only(self):
        self.assertEqual(self.piano_reference(0), 100)
        self.assertEqual(self.piano_reference(50), 50)
        self.assertEqual(self.piano_reference(150), 100, "legacy condition -> Always")
        self.assertEqual(self.piano_edit(50, +1), 51)
        self.assertEqual(self.piano_edit(99, +1), 0, "99% -> Always")
        self.assertEqual(self.piano_edit(0, +1), 0, "Always is the top")
        self.assertEqual(self.piano_edit(0, -1), 99)
        self.assertEqual(self.piano_edit(1, -3), 1)
        self.assertEqual(self.piano_edit(150, -1), 99, "legacy value edits from Always")

class Word(unittest.TestCase):
    """v3.3: the condition lives in the note's +20 word; probability then rolls natively."""
    def word(self, cond_value):
        return step_make(cond=cond_value - 99)

    def test_word_condition_gates_then_native_probability(self):
        k = LABELS.index("1:2")
        u = emu(IMG)
        call(u, SYM7["cond_loop"], SEQ)                                      # pass 1
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 50, self.word(100 + k)), 2, "pass -> stock rolls 50%")
        call(u, SYM7["cond_loop"], SEQ)                                      # pass 2
        self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 50, self.word(100 + k)), 0, "fail -> skip")

    def test_every_condition_through_the_word_matches_the_byte(self):
        for v in range(100, 174):
            for passes in range(1, 9):
                a, b = emu(IMG), emu(IMG)
                ra = rb = None
                for _ in range(passes):
                    call(a, SYM7["cond_loop"], SEQ); call(b, SYM7["cond_loop"], SEQ)
                    ra = call(a, SYM7["cond_decide"], SEQ, v, 0)
                    rb = call(b, SYM7["cond_decide"], SEQ, 0, self.word(v))
                with self.subTest(value=v, passes=passes):
                    self.assertEqual(rb, 2 if ra else 0)

    def test_plain_or_foreign_words_are_ignored(self):
        u = emu(IMG)
        for w in (0, 0x51000005, 0x80001234, step_make(hits=3)):
            self.assertEqual(call(u, SYM7["cond_decide"], SEQ, 50, w), 2, hex(w))

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
