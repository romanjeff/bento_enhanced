# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for inspect-a-step (from bento-community-lab T2 holdstep tests).

    python test_holdstep.py <stock 1.6.1 bento1.bin> <m1 build dir>

Runs stock's own press/release, selected-note reads and encoder edits on our
image. Only display redraw, publish and touch hit-testing are stubbed. Lab's
"stays selected after release" tests are inverted: after release nothing is
selected and the encoders edit no note, as on stock.
"""
import struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
STATE = 0x3003C980
CFG = 0x3003C1E0
MAGIC = 0x31474144
REGS = [UC_ARM_REG_R0 + i for i in range(4)]
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
# Probability encoder (stock since v3.3): list index -> stored byte = index + 1
def cond(index): return index + 1

class Probe:
    def __init__(self, gen=1):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000)
        u.mem_write(BASE, IMG)
        u.mem_write(SENT, b"\xfe\xe7")
        for a, n in ((0x20000000, 0x40000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        self.w(CFG, MAGIC); self.w(CFG + 12, gen)
        self.view = 0x20000000; self.container = 0x20020000
        self.w(self.view + 0x113dc, self.container); self.w(self.view + 0x113e4, 77)
        self.w(self.view + 0x11400, 2); self.w(self.view + 0x113f8, 64)
        self.w(0x30035f40, 5000); self.log = []; self.touch_index = 8
        self.notes([(0, 2, 101, 64), (8, 2, 100, 90), (0, 3, 102, 70)])
        u.hook_add(UC_HOOK_CODE, self.hook)

    def w(self, a, v): self.u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
    def word(self, a): return struct.unpack("<I", self.u.mem_read(a, 4))[0]

    def notes(self, notes):
        self.u.mem_write(self.container, bytes(0x3004))
        for idx, (step, drum, cond, vel) in enumerate(notes):
            raw = bytearray(24); raw[0] = 1; raw[1] = cond
            struct.pack_into("<HIII", raw, 2, drum, step * 960, 960, idx + 1)
            struct.pack_into("<H", raw, 18, vel)
            self.u.mem_write(self.container + idx * 24, bytes(raw))
        self.w(self.container + 0x3000, len(notes))

    def ret(self, v=0):
        self.u.reg_write(UC_ARM_REG_R0, v)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def hook(self, u, a, s, d):
        if a in (0x0815c27c, 0x0815c4b8, 0x08136544):
            self.log.append(("header" if a == 0x0815c27c else "grid" if a == 0x0815c4b8 else "publish",)); self.ret()
        elif a == 0x0815d114:
            self.log.append(("toggle", u.reg_read(UC_ARM_REG_R1))); self.ret()
        elif a == 0x0815bd50:
            self.ret(self.touch_index)

    def call(self, a, *args):
        for r, v in zip(REGS, args):
            self.u.reg_write(r, v)
        self.u.reg_write(UC_ARM_REG_SP, 0x2003f000); self.u.reg_write(UC_ARM_REG_LR, SENT | 1)
        self.u.emu_start(a | 1, SENT, count=100000)
        assert self.u.reg_read(UC_ARM_REG_PC) == SENT

    def time(self, t): self.w(0x30035f40, t)
    def press(self, pad): self.call(0x0815d1ec, self.view, pad)
    def release(self, pad): self.call(0x0815d23c, self.view, pad)
    def snapshot(self):
        out = 0x20028000; self.u.mem_write(out, bytes(24)); self.call(0x0815ba04, self.view, out)
        return self.u.reg_read(UC_ARM_REG_R0), bytes(self.u.mem_read(out, 24))
    def selected(self): self.call(0x0815bab0, self.view); return self.u.reg_read(UC_ARM_REG_R0)
    def value(self, index, offset): return self.u.mem_read(self.container + index * 24 + offset, 1)[0]

class Tests(unittest.TestCase):
    # --- stock behaviour we rely on (held steps are selected while held)
    def test_hold_reads_real_condition_and_velocity_without_note_changes(self):
        p = Probe(); before = bytes(p.u.mem_read(p.container, 0x3004)); p.press(0)
        ok, event = p.snapshot(); self.assertEqual(ok, 1); self.assertEqual(event[1], 101)
        self.assertEqual(struct.unpack_from("<H", event, 18)[0], 64)
        self.assertEqual(bytes(p.u.mem_read(p.container, 0x3004)), before)

    def test_short_tap_still_toggles_native_step(self):
        p = Probe(); p.press(0); p.time(5100); p.release(0)
        self.assertIn(("toggle", 0), p.log)

    def test_hold_boundary_is_150_ms(self):
        p = Probe(); p.press(0); p.time(5000 + 200); p.release(0)
        self.assertNotIn(("toggle", 0), p.log, "200 ms is a hold (stock needed 1000)")
        p = Probe(); p.press(0); p.time(5000 + 100); p.release(0)
        self.assertIn(("toggle", 0), p.log, "100 ms is still a tap")

    def test_long_hold_release_does_not_toggle(self):
        p = Probe(); p.press(0); p.time(6500); p.release(0)
        self.assertNotIn(("toggle", 0), p.log)

    def test_physical_pad_bank_and_scroll_mapping_use_actual_step(self):
        p = Probe(); p.notes([(20, 2, 105, 80)]); p.w(p.view + 0x11404, 16); p.w(p.view + 0x113f4, 8)
        p.press(4); self.assertEqual(p.snapshot()[1][1], 105)

    # --- ours: an edit while held keeps the step on release
    def test_condition_edit_changes_only_held_hit_and_short_release_does_not_toggle(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c8ac, p.view, 44)
        self.assertEqual(p.value(0, 1), cond(44)); self.assertEqual(p.value(1, 1), 100); self.assertEqual(p.value(2, 1), 102)
        self.assertIn(("publish",), p.log); p.release(0); self.assertNotIn(("toggle", 0), p.log)
        self.assertEqual(p.value(0, 1), cond(44))

    def test_velocity_edit_protects_short_release(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c7c4, p.view, 85)
        self.assertEqual(p.value(0, 18), 85); self.assertEqual(p.value(1, 18), 90)
        p.release(0); self.assertNotIn(("toggle", 0), p.log)

    def test_timing_edit_protects_short_release(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c990, p.view, 10)
        self.assertEqual(p.word(p.container + 4), 100)
        p.release(0); self.assertNotIn(("toggle", 0), p.log)

    def test_multiple_held_steps_keep_native_multi_edit(self):
        p = Probe(); p.press(0); p.press(8); p.call(0x0815c8ac, p.view, 43)
        self.assertEqual(p.value(0, 1), cond(43)); self.assertEqual(p.value(1, 1), cond(43)); self.assertEqual(p.value(2, 1), 102)
        p.time(5100); p.release(0); p.release(8)
        self.assertFalse(any(x[0] == "toggle" for x in p.log))

    def test_touch_hold_uses_the_same_selection_and_edit_protection(self):
        p = Probe(); xy = 0x2002e000; p.w(xy, 100); p.w(xy + 4, 100)
        p.call(0x0815d29c, p.view, xy); self.assertEqual(p.snapshot()[1][1], 100)
        p.time(5100); p.call(0x0815c8ac, p.view, 45); p.call(0x0815d444, p.view)
        self.assertNotIn(("toggle", 8), p.log); self.assertEqual(p.value(1, 1), cond(45))

    def test_edit_release_protection_handles_clock_wrap_and_zero(self):
        for tick in [0, 1, 1001, 0xffffffff]:
            p = Probe(); p.time(tick); p.press(0); p.call(0x0815c8ac, p.view, 42); p.release(0)
            self.assertFalse(any(x[0] == "toggle" for x in p.log), tick)

    def test_editing_on_an_empty_step_creates_no_note(self):
        p = Probe(); before = bytes(p.u.mem_read(p.container, 0x3004))
        p.press(4); self.assertEqual(p.snapshot()[0], 0); p.call(0x0815c8ac, p.view, 48)
        p.time(5100); p.release(4)
        self.assertFalse(any(x[0] == "toggle" for x in p.log))
        self.assertEqual(bytes(p.u.mem_read(p.container, 0x3004)), before)

    # --- ours: nothing stays selected after release (Jeff: show the defaults again)
    def test_after_pad_release_no_step_is_selected(self):
        for hold in (100, 1500):
            p = Probe(); p.press(8); p.call(0x0815c8ac, p.view, 46); p.time(5000 + hold); p.release(8)
            self.assertEqual(p.selected(), 0, hold); self.assertEqual(p.snapshot()[0], 0, hold)
            p.call(0x0815c8ac, p.view, 47)                        # encoder turn with nothing held
            self.assertEqual(p.word(p.view + 0x114c4), 48, "it set the pad default (v3.4), not one step")
            self.assertEqual(p.value(1, 1), 47, "the step edited while held (index 46 = 47) keeps its own value")

    def test_after_touch_release_no_step_is_selected(self):
        p = Probe(); xy = 0x2002e000; p.w(xy, 100); p.w(xy + 4, 100)
        p.call(0x0815d29c, p.view, xy); p.call(0x0815c8ac, p.view, 45); p.call(0x0815d444, p.view)
        self.assertEqual(p.selected(), 0); self.assertEqual(p.snapshot()[0], 0)

    def test_no_ram_state(self):
        p = Probe(); p.u.mem_write(STATE, bytes([0x5a]) * 32)
        p.press(0); p.call(0x0815c8ac, p.view, 44); p.release(0)
        self.assertEqual(bytes(p.u.mem_read(STATE, 32)), bytes([0x5a]) * 32)

    def test_selection_hooks_are_stock(self):
        stock = Path(sys.argv[1]).read_bytes()
        for a, n in ((0x0815b9c0, 6), (0x0815bab0, 4), (0x0815d22e, 4), (0x0815d2ea, 4)):
            self.assertEqual(IMG[a - BASE:a - BASE + n], stock[a - BASE:a - BASE + n], hex(a))

class MultiHold(unittest.TestCase):
    """Several held steps + our Condition/Retrig encoders (m4_step.c) through the real
    drum encoder hook, with stock's own press handling."""
    KEYS = (0x1F2, 0x1F1, 0x1F0, 0x1EF)

    def probe(self):
        p = Probe()
        # step UI state as claim() leaves it: magic, boot gen 1, ok, the first four keys (STP6 layout)
        p.u.mem_write(0x3003C880, struct.pack("<2IH11H", 0x36505453, 1, 1, *self.KEYS, *[0] * 7))
        return p

    def encoder(self, p, which, value):
        p.u.reg_write(UC_ARM_REG_SP, 0x2003f000)
        for i, v in enumerate((p.view, self.KEYS[which], value)):
            p.u.reg_write(UC_ARM_REG_R0 + i, v)
        p.u.reg_write(UC_ARM_REG_LR, SENT | 1)
        p.u.emu_start(0x0815CAA0 | 1, SENT, count=200000)

    def word(self, p, i): return p.word(p.container + i * 24 + 20)

    def test_condition_and_retrig_reach_every_held_step(self):
        p = self.probe()
        p.press(0); p.press(8)                                       # two steps held on pad 2
        self.encoder(p, 0, 6)                                        # Condition -> list index 6
        self.encoder(p, 1, 3)                                        # Retrig 3
        w0, w1, w2 = self.word(p, 0), self.word(p, 1), self.word(p, 2)
        self.assertEqual(w0, w1, "both held steps got the same settings")
        self.assertEqual(w0 >> 24, 0xB6)
        self.assertEqual((w0 >> 13) & 0xF, 3)
        self.assertNotEqual((w0 >> 17) & 0x7F, 0)
        self.assertEqual(w2, 0, "the note on another pad is untouched")
        p.time(5100); p.release(0); p.release(8)
        self.assertFalse(any(x[0] == "toggle" for x in p.log), "edited held steps stay on")

    def test_any_number_of_held_pads(self):
        p = self.probe()
        steps = (0, 2, 5, 9, 12, 15)
        p.notes([(st, 2, 0, 100) for st in steps])
        held = (0, 2, 9, 12, 15)                                    # five pads held, step 5 not
        for pad in held:
            p.press(pad)
        self.encoder(p, 1, 4)                                        # Retrig 4
        p.call(0x0815c7c4, p.view, 70)                               # stock velocity edit too
        for i, st in enumerate(steps):
            w = self.word(p, i)
            with self.subTest(step=st):
                if st in held:
                    self.assertEqual(((w >> 13) & 0xF, p.value(i, 18)), (4, 70))
                else:
                    self.assertEqual((w, p.value(i, 18)), (0, 100), "not held: untouched")
        p.time(5100)
        for pad in held:
            p.release(pad)
        self.assertFalse(any(x[0] == "toggle" for x in p.log))

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
