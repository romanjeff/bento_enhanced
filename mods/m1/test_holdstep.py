# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for inspect-a-step (port of bento-community-lab T2 holdstep tests).

    python test_holdstep.py <stock 1.6.1 bento1.bin> <m1 build dir>

Runs stock's own press/release, selected-note reads and encoder edits on our
image. Only display redraw, publish and touch hit-testing are stubbed. Lab's
"cleared by UI startup" test becomes a boot-generation test (stock has no such
startup hook; this RAM survives a reset).
"""
import struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
STATE = 0x24048D80
CFG = 0x240485E0
MAGIC = 0x31474144
REGS = [UC_ARM_REG_R0 + i for i in range(4)]
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()

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
    def test_hold_reads_real_condition_and_velocity_without_note_changes(self):
        p = Probe(); before = bytes(p.u.mem_read(p.container, 0x3004)); p.press(0)
        ok, event = p.snapshot(); self.assertEqual(ok, 1); self.assertEqual(event[1], 101)
        self.assertEqual(struct.unpack_from("<H", event, 18)[0], 64)
        self.assertEqual(bytes(p.u.mem_read(p.container, 0x3004)), before)

    def test_short_tap_still_toggles_native_step(self):
        p = Probe(); p.press(0); p.time(5100); p.release(0)
        self.assertIn(("toggle", 0), p.log)

    def test_long_hold_release_does_not_toggle(self):
        p = Probe(); p.press(0); p.time(6500); p.release(0)
        self.assertNotIn(("toggle", 0), p.log); self.assertEqual(p.snapshot()[1][1], 101)

    def test_condition_edit_changes_only_held_hit_and_short_release_does_not_toggle(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c8ac, p.view, 104)
        self.assertEqual(p.value(0, 1), 104); self.assertEqual(p.value(1, 1), 100); self.assertEqual(p.value(2, 1), 102)
        self.assertIn(("publish",), p.log); p.release(0); self.assertNotIn(("toggle", 0), p.log)
        self.assertEqual(p.snapshot()[1][1], 104)

    def test_velocity_edit_protects_short_release(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c7c4, p.view, 85)
        self.assertEqual(p.value(0, 18), 85); self.assertEqual(p.value(1, 18), 90)
        p.release(0); self.assertNotIn(("toggle", 0), p.log)

    def test_timing_edit_protects_short_release(self):
        p = Probe(); p.press(0); p.time(5100); p.call(0x0815c990, p.view, 10)
        self.assertEqual(p.word(p.container + 4), 100)
        p.release(0); self.assertNotIn(("toggle", 0), p.log)

    def test_multiple_held_steps_keep_native_multi_edit(self):
        p = Probe(); p.press(0); p.press(8); p.call(0x0815c8ac, p.view, 103)
        self.assertEqual(p.value(0, 1), 103); self.assertEqual(p.value(1, 1), 103); self.assertEqual(p.value(2, 1), 102)
        p.time(5100); p.release(0); p.release(8)
        self.assertFalse(any(x[0] == "toggle" for x in p.log)); self.assertEqual(p.snapshot()[1][1], 103)

    def test_last_inspected_hit_stays_selected_after_release(self):
        p = Probe(); p.press(8); p.time(6500); p.release(8)
        self.assertEqual(p.selected(), 1); self.assertEqual(p.snapshot()[1][1], 100)
        p.call(0x0815c8ac, p.view, 106); self.assertEqual(p.value(1, 1), 106); self.assertEqual(p.value(0, 1), 101)

    def test_view_return_default_reset_does_not_hide_inspected_condition(self):
        p = Probe(); p.press(0); p.time(6500); p.release(0)
        p.u.mem_write(p.view + 0x11420, bytes(128)); p.w(p.view + 0x114c0, 100); p.w(p.view + 0x114c4, 0)
        self.assertEqual(p.snapshot()[1][1], 101)

    def test_pressing_an_empty_step_clears_last_inspection_without_creating_notes(self):
        p = Probe(); p.press(0); p.time(6500); p.release(0); before = bytes(p.u.mem_read(p.container, 0x3004))
        p.press(4); self.assertEqual(p.snapshot()[0], 0); p.call(0x0815c8ac, p.view, 108)
        p.time(6600); p.release(4)
        self.assertFalse(any(x[0] == "toggle" for x in p.log))
        self.assertEqual(bytes(p.u.mem_read(p.container, 0x3004)), before)

    def test_different_track_clip_or_drum_never_uses_stale_selection(self):
        for offset, value in [(0x113e4, 88), (0x11400, 3), (0x113f4, 32), (0x113f8, 0)]:
            p = Probe(); p.press(0); p.time(6500); p.release(0); p.w(p.view + offset, value)
            self.assertEqual(p.selected(), 0, (offset, value)); self.assertEqual(p.snapshot()[0], 0)

    def test_physical_pad_bank_and_scroll_mapping_use_actual_step(self):
        p = Probe(); p.notes([(20, 2, 105, 80)]); p.w(p.view + 0x11404, 16); p.w(p.view + 0x113f4, 8)
        p.press(4); self.assertEqual(p.word(STATE + 16), 20)
        self.assertEqual(p.snapshot()[1][1], 105)

    def test_touch_hold_uses_the_same_selection_and_edit_protection(self):
        p = Probe(); xy = 0x2002e000; p.w(xy, 100); p.w(xy + 4, 100)
        p.call(0x0815d29c, p.view, xy); self.assertEqual(p.snapshot()[1][1], 100)
        p.time(5100); p.call(0x0815c8ac, p.view, 105); p.call(0x0815d444, p.view)
        self.assertNotIn(("toggle", 8), p.log); self.assertEqual(p.value(1, 1), 105)

    def test_edit_release_protection_handles_clock_wrap_and_zero(self):
        for tick in [0, 1, 1001, 0xffffffff]:
            p = Probe(); p.time(tick); p.press(0); p.call(0x0815c8ac, p.view, 102); p.release(0)
            self.assertFalse(any(x[0] == "toggle" for x in p.log), tick)

    def test_deleted_inspected_note_falls_back_without_creating_notes(self):
        p = Probe(); p.press(0); p.time(6500); p.release(0); p.notes([(8, 2, 100, 90)])
        self.assertEqual(p.selected(), 0); self.assertEqual(p.snapshot()[0], 0)

    def test_inspection_from_a_previous_boot_is_ignored(self):
        p = Probe(gen=1); p.press(8); p.time(6500); p.release(8)
        self.assertEqual(p.selected(), 1)
        p.w(CFG + 12, 2)                                  # M4 rebooted: same RAM, new generation
        self.assertEqual(p.selected(), 0)
        self.assertEqual(p.snapshot()[0], 0)

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
