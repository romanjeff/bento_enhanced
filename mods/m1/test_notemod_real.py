# SPDX-License-Identifier: GPL-3.0-or-later
"""Note Mod on the firmware's real view objects (v3.65).

    python test_notemod_real.py <stock bento1.bin> <m1 build dir>

test_step.py's rig uses flat RAM as the view, a faked header generator and a page of its
choosing. That hid BE3.64's crash and missing melodic button. Here the firmware's own
constructor builds the UI at the device's base address, with the real widgets, templates and
page gate. Slow (seconds per construction), so each test builds once. No project is loaded,
so model getters run with no real sequence behind them.
"""
import struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
import emu
from unicorn import UcError, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_LR

OUT = Path(sys.argv[2]); del sys.argv[1:3]
IMAGE = str(OUT / "bento1.bin")
B = 0xC6E784C0                       # the device's own allocation (crash record, BE3.64)
V_DRUM, V_PIANO = B + 0x675AF8, B + 0x2ED980
EVT = 0x24070000
DRUM_EVENT, PIANO_EVENT, PIANO_HEADER, DRUM_HEADER = 0x0815D600, 0x0815A598, 0x081599BC, 0x0815C27C
WALK = 0x0816FABC
DRUM_VT, PIANO_VT = 0x0819E854, 0x0819E7B0
CFG, HEAD, MAGIC, CFGB = 0x3003C1E0, 0x3003DFC0, 0x37444F4D, 0xC6200000
NM = 0x10F3
TAB_STEP, TAB_SEQ = 0x49, 0x4A
TYPE_FN = 0x081FF101
CTX = 0x1234                         # a bound sequence, as far as the views can tell


class Real:
    def __init__(self):
        e = self.e = emu.Emu(IMAGE)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)
        e.w(0x3000004C, B); e.w(0x30000048, 0xC8000000)
        assert e.call(0x08104C08, 0x689A98) == B
        e.call(0x081630F4, B, count=400_000_000)
        e.w(0x3001FD24, B)
        e.call(0x08162ADC, B, count=400_000_000)
        self.u.mem_write(CFGB, struct.pack("<3I", MAGIC, 1, 2) + bytes(8 * 232 + 8 * 12 + 40))
        self.u.mem_write(HEAD, struct.pack("<8I", MAGIC, 1, CFGB, ~CFGB & 0xFFFFFFFF, 2, 0, 0, 0))
        e.w(CFG, 0x31474144); e.w(CFG + 20, 1)
        self.selected = 0
        self.u.mem_write(TYPE_FN & ~1, b"pG")
        self.u.hook_add(UC_HOOK_CODE, self.model_stub)

    def ret(self, v=0):
        self.u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def model_stub(self, u, a, size, _):
        """No project is loaded: answer the model getters the bars ask."""
        if a == 0x0812DC74:                       # key 0x131 = the selected track
            self.ret(self.selected if u.reg_read(UC_ARM_REG_R1) == 0x131 else 0)
        elif a == 0x08133960:                     # track number -> handle
            self.e.w(u.reg_read(UC_ARM_REG_R2), 0x77); self.ret(1)
        elif a == 0x0812D80E:                     # handle -> object, whose type is one-shot
            obj = 0x24079800
            self.e.w(obj, obj + 0x40); self.e.w(obj + 0x40, TYPE_FN)
            self.e.w(u.reg_read(UC_ARM_REG_R2), obj); self.ret(1)
        elif a == TYPE_FN & ~1:
            self.ret(0x0E)
        elif a in (0x08133614, 0x0815884C, 0x0815BA04, 0x0813130C, 0x0816FC34, 0x0815C4B8):
            self.ret(0)

    def bind(self, tab, ctx=CTX):
        """The drum view on a tab; ctx != 0 = a bound sequence."""
        self.u.mem_write(V_DRUM + 0x113E0, bytes([tab]))
        self.e.w(V_DRUM + 0x113E4, ctx); self.e.w(V_DRUM + 0x113DC, ctx)

    def event(self, handler, view, id_):
        self.u.mem_write(EVT, struct.pack("<HH4I", id_, 0, 0, 0, 0, 0))
        self.e.call(handler, view, EVT, count=40_000_000)

    def children_ok(self, parent, depth=0, seen=None):
        """Every child reachable from parent has a vtable and names parent as its parent."""
        seen = set() if seen is None else seen
        c = self.e.r(parent + 0x1C)
        n = 0
        while c and n < 4096:
            if c in seen: return True
            seen.add(c)
            if self.e.r(c) == 0 or self.e.r(c + 0x2C) != parent: return False
            if depth < 6 and not self.children_ok(c, depth + 1, seen): return False
            c = self.e.r(c + 0x24); n += 1
        return True

    def walk(self, view):
        """Stock's own tick: BE3.64 faulted here with a zero vtable."""
        self.e.call(WALK, view, 0x30018DBC, count=40_000_000)

    def nm_mask(self):
        return self.e.r(CFGB + 12 + 8 * 232 + 8 * 12 + 8)

    def piano_keys(self):
        return list(struct.unpack("<6H", self.u.mem_read(V_PIANO + 0x11BCC0, 12)))


class NoteModReal(unittest.TestCase):
    def setUp(self):
        self.r = Real()          # a fresh tree per test: damage must not carry over

    def healthy(self, why):
        self.assertTrue(self.r.children_ok(V_DRUM), f"drum widget tree: {why}")
        self.r.walk(V_DRUM)

    def test_objects_are_what_the_code_assumes(self):
        self.assertEqual(self.r.e.r(V_DRUM), DRUM_VT)
        self.assertEqual(self.r.e.r(V_PIANO), PIANO_VT)
        self.healthy("as constructed")

    def test_press_on_the_sequence_tab_keeps_the_tree(self):
        """The BE3.64 crash: boot default tab, press."""
        self.r.bind(1)
        self.r.event(DRUM_EVENT, V_DRUM, NM)
        self.healthy("press from Sequence")
        self.assertEqual(self.r.u.mem_read(V_DRUM + 0x113E0, 1)[0], 0, "moved to Step")
        self.assertEqual(self.r.nm_mask() & 1, 1, "on")

    def test_press_on_step_then_off(self):
        self.r.bind(0)
        self.r.event(DRUM_EVENT, V_DRUM, NM); self.healthy("on")
        self.assertEqual(self.r.nm_mask() & 1, 1)
        self.r.event(DRUM_EVENT, V_DRUM, NM); self.healthy("off")
        self.assertEqual(self.r.nm_mask() & 1, 0)

    def test_press_with_no_sequence_bound(self):
        for tab in (0, 1):
            with self.subTest(tab=tab):
                self.setUp()
                self.r.bind(tab, ctx=0)
                self.r.event(DRUM_EVENT, V_DRUM, NM)
                self.healthy("no sequence")
                self.assertEqual(self.r.nm_mask() & 1, 0, "nothing to show: stays off")

    def test_exit_after_moving_to_sequence(self):
        self.r.bind(0)
        self.r.event(DRUM_EVENT, V_DRUM, NM)
        self.r.event(DRUM_EVENT, V_DRUM, TAB_SEQ); self.healthy("Sequence tab")
        self.assertEqual(self.r.nm_mask() & 1, 0, "leaving Step leaves Note Mod")
        self.r.event(DRUM_EVENT, V_DRUM, NM); self.healthy("press again")

    def test_piano_press_touches_only_the_piano_object(self):
        before = bytes(self.r.u.mem_read(V_DRUM, 0x124E0))
        self.r.event(PIANO_EVENT, V_PIANO, NM)
        self.assertEqual(self.r.nm_mask() & 1, 1)
        self.assertEqual(bytes(self.r.u.mem_read(V_DRUM, 0x124E0)), before)
        self.healthy("piano press")

    def test_one_track_does_not_change_another(self):
        self.r.selected = 2
        self.r.event(PIANO_EVENT, V_PIANO, NM)
        on = self.r.piano_keys()
        self.assertIn(0x4A, on, "Shape on track 2's bar")
        self.r.selected = 3
        self.r.e.call(PIANO_HEADER, V_PIANO, count=40_000_000)
        self.assertNotIn(0x4A, self.r.piano_keys(), "track 3: stock's bar")
        self.assertEqual(self.r.nm_mask(), 1 << 2)
        self.r.selected = 2
        self.r.e.call(PIANO_HEADER, V_PIANO, count=40_000_000)
        self.assertIn(0x4A, self.r.piano_keys(), "track 2: still on")

    def test_a_tab_turns_it_off(self):
        for tab in (0x49, 0x4A, 0x4B):
            with self.subTest(tab=hex(tab)):
                self.setUp()
                self.r.event(PIANO_EVENT, V_PIANO, NM)
                self.assertEqual(self.r.nm_mask() & 1, 1)
                try:
                    self.r.event(PIANO_EVENT, V_PIANO, tab)
                except UcError:
                    self.assertEqual(tab, 0x4B, "stock's Automation tab needs a project; ours ran first")
                    self.assertEqual(self.r.nm_mask() & 1, 0)
                    continue
                self.assertEqual(self.r.nm_mask() & 1, 0)
                self.assertNotIn(0x4A, self.r.piano_keys())

    def test_off_returns_the_same_bar(self):
        self.r.selected = 1
        self.r.e.call(PIANO_HEADER, V_PIANO, count=40_000_000)
        stock = self.r.piano_keys()
        self.r.event(PIANO_EVENT, V_PIANO, NM)
        self.assertNotEqual(self.r.piano_keys(), stock)
        self.r.event(PIANO_EVENT, V_PIANO, NM)
        self.assertEqual(self.r.piano_keys(), stock)


if __name__ == "__main__":
    unittest.main()
