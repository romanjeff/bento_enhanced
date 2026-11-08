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
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
D2 = S.D2_SLICE                                                # our shared slice (per base)

OUT = Path(sys.argv[2]); del sys.argv[1:3]
IMAGE = str(OUT / "bento1.bin")
B = 0xC6E784C0                       # the device's own allocation (crash record, BE3.64) (addr-ok: harness)
V_DRUM, V_PIANO = B + S.UI_PAGE_DRUM, B + S.UI_PAGE_PIANO   # the views inside the page set (per base)
EVT = 0x24070000  # addr-ok: harness
DRUM_EVENT, PIANO_EVENT, PIANO_HEADER, DRUM_HEADER = S.DRUM_EVENT, S.PIANO_EVENT, S.PIANO_HEADER, S.DRUM_HEADER
WALK = S.UI_TICK_WALK
DRUM_VT, PIANO_VT = S.DRUM_VIEW_VT, S.PIANO_VIEW_VT
CFG, HEAD, MAGIC, CFGB = D2 + 0x1E0, D2 + 0x1FC0, 0x37444F4D, 0xC6200000  # addr-ok: harness
NM = 0x10F3
TAB_STEP, TAB_SEQ = 0x49, 0x4A
TYPE_FN = 0x081FF101
CTX = 0x1234                         # a bound sequence, as far as the views can tell


class Real:
    def __init__(self):
        e = self.e = emu.Emu(IMAGE)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness
        e.w(S.UI_BUMP_CUR, B); e.w(S.UI_BUMP_END, 0xC8000000)
        assert e.call(S.UI_BUMP_ALLOC, S.UI_PAGES_SIZE) == B
        e.call(S.UI_PAGES_CTOR, B, count=400_000_000)
        e.w(S.UI_PAGES_PTR, B)
        e.call(S.UI_PAGES_INIT, B, count=400_000_000)
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
        if a == S.GET_PROPERTY:                       # the selected track (KEY_SEL_TRACK)
            self.ret(self.selected if u.reg_read(UC_ARM_REG_R1) == S.KEY_SEL_TRACK else 0)
        elif a == S.FIND_TRACK:                     # track number -> handle
            self.e.w(u.reg_read(UC_ARM_REG_R2), 0x77); self.ret(1)
        elif a == S.RESOLVE_OBJECT:                     # handle -> object, whose type is one-shot
            obj = 0x24079800  # addr-ok: harness
            self.e.w(obj, obj + 0x40); self.e.w(obj + 0x40, TYPE_FN)
            self.e.w(u.reg_read(UC_ARM_REG_R2), obj); self.ret(1)
        elif a == TYPE_FN & ~1:
            self.ret(getattr(self, "kind", 0x0E))     # the selected track's instrument type
        elif a in (S.CONTEXT_GET, S.PIANO_SELECTED, S.SNAPSHOT, S.PROJECT_TOUCH, S.UI_POST, S.GRID):
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
        self.e.call(WALK, view, S.UI_WALK_CTX, count=40_000_000)

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



class HeaderThroughStock(unittest.TestCase):
    """The header path on this base's own code (S4+S5; rig from the step UI investigator): stock's template
    loader for the drum page, stock's per-tick update HEADER_TICK -> our HEADER_GEN wrapper -> stock's generator
    body -> strike_toolbar -> stock's compare with the applied header and HEADER_BUILD -> the root's buttons.
    test_step's header classes fake the generator and write records at the table's offsets; this is the
    check that 1.6.8's description layout (actions at +0x8C, armed flag +0x1194, buttons +0x564) is what
    stock really uses. Stubbed: the tempo getter and the RTOS lock pair (no RTOS here)."""
    def setUp(self):
        from unicorn.arm_const import UC_ARM_REG_S0
        self.r = Real()
        u = self.r.u
        def stub(uc, a, sz, _):
            if a == S.TEMPO_GET:
                uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", 120.0))[0])
            else:                                         # M4_LOCK_PAIR: taken
                uc.reg_write(UC_ARM_REG_R0, 1)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in (S.TEMPO_GET, S.M4_LOCK_PAIR):
            u.hook_add(UC_HOOK_CODE, stub, begin=a, end=a)
        self.r.e.call(S.HEADER_TPL_LOAD, S.MODEL, S.PAGE_SEQ_DRUM, count=40_000_000)
        u.mem_write(S.MODEL + S.MODEL_PAGE, bytes([S.PAGE_SEQ_DRUM]))
        u.mem_write(S.MODEL + S.MODEL_HEADER_ON, b"\x01")
        u.mem_write(EVT + 0x100, bytes(24))              # the tick's event (unused by what we check)

    def tick(self, n=2):
        for _ in range(n):
            self.r.e.call(S.HEADER_TICK, S.HEADER_ROOT, EVT + 0x100, count=40_000_000)

    def applied(self):
        """(label, empty, lit, group, id) of each action record in the header stock applied."""
        d = bytes(self.r.u.mem_read(S.HEADER_ROOT + S.HEADER_APPLIED, S.HEADER_DESC_SIZE))
        out = []
        for i in range(S.HEADER_ACTIONS):
            x = d[S.HEADER_DESC_ACTIONS + i * S.HEADER_ACTION_SIZE:][:S.HEADER_ACTION_SIZE]
            out.append((x[:S.HEADER_ACTION_EMPTY].split(b"\0")[0].decode(), x[S.HEADER_ACTION_EMPTY], x[S.HEADER_ACTION_LIT],
                        x[S.HEADER_ACTION_GROUP], struct.unpack_from("<H", x, S.HEADER_ACTION_ID)[0]))
        return out

    def buttons(self):
        """(label, hidden) of each header button widget."""
        b = lambda k: S.HEADER_ROOT + S.HEADER_BUTTONS + k * S.HEADER_BUTTON_STRIDE
        return [(self.r.e.text(b(k) + S.HEADER_BUTTON_LABEL), self.r.u.mem_read(b(k) + S.HEADER_BUTTON_HIDDEN, 1)[0])
                for k in range(S.HEADER_BUTTON_COUNT)]

    def test_drum_header_gets_strike_and_note_mod_left_of_undo(self):
        """One-shot track on page 0x27: Strike Vel (lit: on) in slot 4 left of Undo, Note Mod in slot 3, stock's
        Step / Sequence tabs and Undo kept, as v3.72 shows it on the device."""
        self.r.selected = 0
        self.tick()
        recs = self.applied()
        labels = [x[0] for x in recs]
        self.assertEqual(labels[1:3], ["Step", "Sequence"])
        self.assertEqual([x[4] for x in recs[1:3]], [S.EV_TAB_EVENT, S.EV_TAB_SEQUENCE], "stock's tab events")
        self.assertEqual(recs[4][0::4], ("Strike Vel", S.ACT_STRIKE))
        self.assertEqual(recs[4][2], 1, "lit: strike velocity on")
        self.assertEqual(recs[3][0::4], ("Note Mod", S.ACT_NM))
        self.assertEqual(labels[5], "Undo")

    def test_the_widgets_show_them(self):
        """HEADER_BUILD made the button widgets from that description: ours visible with their labels."""
        self.tick()
        shown = [l for l, hidden in self.buttons() if not hidden]
        for want in ("Note Mod", "Strike Vel", "Undo", "Step", "Sequence"):
            self.assertIn(want, shown)
        self.assertEqual(self.buttons()[4], ("Strike Vel", 0))

    def test_toggling_strike_re_applies_the_header(self):
        """Strike Vel pressed (its action through the drum view's event hook): on the next ticks header_sync sees our
        button differ from the applied header and arms stock's layout flag (HEADER_ARMED), so stock applies it unlit."""
        self.tick()
        self.assertEqual(self.applied()[4][0:3:2], ("Strike Vel", 1))
        self.r.event(DRUM_EVENT, V_DRUM, S.ACT_STRIKE)
        self.tick(3)
        self.assertEqual(self.applied()[4][0:3:2], ("Strike Vel", 0), "stock re-applied the header: unlit")

    def test_strike_button_appears_when_a_drum_track_is_selected(self):
        """A melodic track on page 0x27 first (no Strike Vel), then a one-shot one: our button differs from the applied
        header, header_sync arms stock's layout flag (HEADER_ARMED) and stock shows the widget. Without the arm stock
        keeps the widget hidden (the layout is applied only at a page change)."""
        self.r.kind = 0x01
        self.tick()
        self.assertNotIn("Strike Vel", [l for l, hidden in self.buttons() if not hidden])
        self.r.kind = S.TYPE_ONESHOT
        self.tick(3)
        self.assertEqual(self.buttons()[4], ("Strike Vel", 0))

    def test_our_arm_is_the_flag_stock_reads_and_clears(self):
        """HEADER_ARMED (S4+S5 review): the byte our header_sync sets to 1 is the one stock's per-tick update reads
        and its layout step clears. In the other header tests stock re-applies a changed header without our arm, so
        a wrong offset (1.6.1's 0x117C on 1.6.8) passed them. Here every access to root+HEADER_ARMED is recorded
        with the instruction that made it: ours (appended M4 code) must write 1, then stock must read it as 1 and
        write 0 back; finally the byte is 0 again."""
        from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
        u, a = self.r.u, S.HEADER_ROOT + S.HEADER_ARMED
        log, on = [], [False]                             # (who, 'r'/'w', value); recording after the first ticks
        def acc(uc, kind, addr, size, value, _):
            if not on[0] or not addr <= a < addr + size:
                return
            pc = uc.reg_read(UC_ARM_REG_PC)
            who = "ours" if S.APPEND_FROM <= pc < 0x08200000 else "stock"  # addr-ok: end of the M4 flash bank
            v = (value >> 8 * (a - addr)) & 0xFF if kind == UC_MEM_WRITE else uc.mem_read(a, 1)[0]
            log.append((who, "w" if kind == UC_MEM_WRITE else "r", v))
        # hooked before any code runs (a hook added later may miss blocks already translated)
        hooks = [u.hook_add(k, acc) for k in (UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE)]
        try:
            self.r.kind = 0x01
            self.tick()                                   # melodic: the applied header has no Strike Vel
            on[0] = True
            self.r.kind = S.TYPE_ONESHOT                  # now our button differs from the applied header
            self.tick(3)
        finally:
            for h in hooks:
                u.hook_del(h)
        self.assertIn(("ours", "w", 1), log, "header_sync armed the flag")
        i = log.index(("ours", "w", 1))
        self.assertIn(("stock", "r", 1), log[i:], f"stock never read our arm at root+{S.HEADER_ARMED:#x}: {log}")
        self.assertIn(("stock", "w", 0), log[i:], f"stock never cleared root+{S.HEADER_ARMED:#x}: {log}")
        self.assertEqual(u.mem_read(a, 1)[0], 0, "stock's layout step cleared the flag")
        self.assertEqual(self.buttons()[4], ("Strike Vel", 0))


if __name__ == "__main__":
    unittest.main()
