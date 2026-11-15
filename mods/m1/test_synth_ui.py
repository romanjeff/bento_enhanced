# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wavetable page with Analog oscillators (BE4.40, docs/plan-native-props.md 3.4: A6, A7, A10), m4_synth.c in the built
image, on stock 1.6.8's own synth page.

    python test_synth_ui.py <stock bento1.bin> <m1 build dir>

What runs (emu.py): the built image from the M4 reset's .data copy and static constructors on, model init's registry build
(N2: stock's REG_BUILD, then np_keys), stock's object store with a Wavetable track and its children (CHILD_CREATE, the N1
template), stock's synth page view: its constructor (SYNTH_VIEW_CTOR), its notification 0xA8 (the page build for the
selected track, with A6 in its memcpy and A7 in its pane texts), its class's tick word (A10, then stock's tick), its row
refresh and bind (SYNTH_ROW_SHOW), its encoder widgets (a pending turn, handed to the widget by stock's tick, becomes 0xCF;
the view's event handler re-posts it as 0xD0), the model's 0xD0 edit (MODEL_CHILD_EDIT) and stock's SLOT_TITLE. Only SET_PROPERTY (the post
to the M7) and ENGINE_PROPERTY are logged and return, as in test_native_props; UI_POST runs and is logged.
Typed here independently of the code under test: our keys (plan 3.1), stock's oscillator row {Level 0xC4, Pitch 0x03,
Position 0xC9} (PAGE_KEYS_COPY's table, plan 3.4), the Analog layouts (m4_synth.c want_row's comment / plan 3.3 rule 4),
the stock page facts below (measured in this emulator on stock's code; the tests check them on a stock row first).
On a BE_DISABLE=analog build the page must stay stock's (Disabled); skipped on a build without the analog group (1.6.1).
"""
import json, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
sys.path.insert(0, str(HERE))
import emu
from unicorn import UC_HOOK_CODE, UcError
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
from bases import from_build
STOCK = Path(sys.argv[1]).read_bytes()
S = from_build(sys.argv[2], STOCK)
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
del sys.argv[1:3]
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}
BUILT = "analog" not in MAN.get("omitted", []) and "an_view_tick" in SYM
DISABLED = BUILT and "analog" in MAN.get("disabled", [])
HAVE = BUILT and not DISABLED
DRUMS = "drums" not in MAN.get("omitted", []) and "drums" not in MAN.get("disabled", [])

BASE = 0x08040000
HEAP = 0xC4100000                  # harness heap  # addr-ok: harness
STRS = 0xC0F00000                  # harness out cells, events, buffers  # addr-ok: harness
EV = STRS + 0x1000
TYPE, SHAPE, SYNC, GLIDE = 0x1C0, 0x1A7, 0x1AB, 0x1B7        # our keys (plan 3.1)
LEVEL, PITCH, POSITION = 0xC4, 0x03, 0xC9                    # stock's oscillator row (plan 3.4)
WT_ROW = [LEVEL, PITCH, POSITION, TYPE, 0, 0, 0, 0]           # a Wavetable oscillator: stock's row, Type in the free 4th slot
AN_ROW1 = [LEVEL, PITCH, SHAPE, TYPE, GLIDE, 0, 0, 0]         # OSC1 Analog: Shape for Position, Glide
AN_ROW2 = [LEVEL, PITCH, SHAPE, TYPE, SYNC, 0, 0, 0]          # OSC2 Analog: Sync 1>2, whatever OSC1's Type (BE4.40 J1)
WT_CHILDREN = bytes.fromhex("0606080a0a09090b0b0c0d")           # a Wavetable track's children (stock's list)
# stock 1.6.8's synth page, measured here on stock's code (rc33 implA p0/t5.py, probe2.py and this suite's runs):
SLOT0, SLOT_STRIDE = 0x70, 0x468   # the row's 8 encoder widgets: view + SLOT0 + SLOT_STRIDE * slot
W_KEY, W_HANDLE = 0x9C, 0x34       # a widget's key (u16) and the object handle it edits (the 0xCF event's +4)
W_VALUE, W_TITLE = 0xA8, 0x3C8     # its value and title labels (SLOT_TITLE targets: their text at +0x31)
TEXT = 0x31                        # a label's text (SLOT_TITLE copies it there)
SHOWN = 0x11D88                    # the shown row: byte view + [view + 0x2434] + SHOWN (SYNTH_ROW_BIND)
PANE_TITLE = (0x29C0, 0x2A30)      # "WT1: <wav>" / "WT2: <wav>" (stock's '%s%.20s', not a PANE_TEXT site)
PANE_LABEL = (0x3114, 0x3474)      # the "No WAV" labels (VIEW_NOWAV_LABEL, + 0x360)
PANE_HINT = (0x37BC, 0x3A9C)       # the "Double-tap to load WAV" hints
ROW_BOX, PENDING = 0x38, 0x2398  # the widgets' container (view + ROW_BOX, its widgets from +0x38) and its pending encoder
                                   # deltas (+PENDING + 4 slot): its class's slot 0, from stock's view tick, hands each to the
                                   # widget's input (accumulate, map to the key's value, post 0xCF)
# the synth area and the diag slice (synth_shared.h / diag_shared.h, typed independently)
SA = S.EXT_POOL_END - 0xC000 if BUILT else 0                   # SYNTH_AREA: EXT_POOL_END - (0x4000 + 0x8000)
CFG, D0L, TAKE = S.D2_SLICE + 0x1E0, S.D2_SLICE + 0xFE0, S.D2_SLICE + 0x1D8
M7_MAGIC, DIAG_MAGIC = 0x37534542, 0x31474144
MD = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)


class Rig:
    """One M4 with a live project of one Wavetable track (WT1, WT2) and stock's synth page built for it."""
    def __init__(self, types=(0, 0), area=True, page=True, d0l=None):
        e = self.e = emu.Emu(IMG, init_ctors=False, base=S)
        u = self.u = e.u
        u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness (the heap, the synth area)
        e.ctors()
        self.heap = [HEAP]
        def malloc(uc, a, sz, _):
            n = uc.reg_read(UC_ARM_REG_R0); p = self.heap[0]; self.heap[0] += (n + 31) & ~31
            uc.mem_write(p, bytes(n)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
        self.w32(CFG, DIAG_MAGIC); self.w32(CFG + 12, 1)        # the config line: M4 boot generation 1
        if area:
            self.take()
        self.ms(1000)
        self.call(SYM["wrap_reg_build"] if HAVE else S.REG_BUILD, self.malloc(0x400))
        st = self.malloc(0x6C)
        self.call(S.STORE_INIT, st); self.call(S.STORE_POOL_INIT, st, 0x40000)
        e.w(S.MODEL, st)
        self.store = st
        self.track = self.create(S.TYPE_WAVETABLE)
        for k in WT_CHILDREN:
            self.child(k, self.track)
        self.w1, self.w2 = self.children(self.track)[:2]
        self.h1, self.h2 = self.r32(self.w1 + 0x10), self.r32(self.w2 + 0x10)
        for o, t in zip((self.w1, self.w2), types):
            self.set(o, TYPE, t)
        self.titles, self.posts, self.props, self.shows = [], [], [], []
        def title(uc, a, sz, _): self.titles.append((uc.reg_read(UC_ARM_REG_R0), self.e.text(uc.reg_read(UC_ARM_REG_R1), 40)))
        u.hook_add(UC_HOOK_CODE, title, begin=S.SLOT_TITLE, end=S.SLOT_TITLE)
        def post(uc, a, sz, _): self.posts.append(struct.unpack("<HHIIi", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 16)))
        u.hook_add(UC_HOOK_CODE, post, begin=S.UI_POST, end=S.UI_POST)
        def setprop(uc, a, sz, _):
            sp = uc.reg_read(UC_ARM_REG_SP)
            self.props.append((uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3),
                               struct.unpack("<h", uc.mem_read(sp, 2))[0]))
            uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, setprop, begin=S.SET_PROPERTY, end=S.SET_PROPERTY)
        def ret0(uc, a, sz, _): uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, ret0, begin=S.ENGINE_PROPERTY, end=S.ENGINE_PROPERTY)
        u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.shows.append(1), begin=S.SYNTH_ROW_SHOW, end=S.SYNTH_ROW_SHOW)
        u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness (hooks on code translated by the constructors)
        if d0l:
            self.u.mem_write(D0L, struct.pack("<7I", *d0l))
        if HAVE:
            self.call(SYM["an_tick"])                          # the UI loop's tick runs from boot, before any page
        self.view = None
        if page:
            self.view = self.malloc(0x13000)
            self.call(S.SYNTH_VIEW_CTOR, self.view)
            self.notify(0xA8)

    # ---- memory, calls
    def r32(self, a): return self.e.r(a)
    def w32(self, a, v): self.e.w(a, v)
    def h16(self, a): return self.e.h(a)
    def ms(self, t): self.w32(S.M4_TICK_MS, t)
    def now(self): return self.r32(S.M4_TICK_MS)
    def take(self, gen=1, boot=1):
        """The M7 took the synth area: its header, stamped for M4 boot `boot`."""
        self.u.mem_write(SA, struct.pack("<5I", M7_MAGIC, gen, 2, 0xC000, boot))
    def call(self, fn, *args, count=80_000_000):
        try:
            return self.e.call(fn, *args, count=count)
        except UcError as x:
            raise AssertionError(f"{x} at pc {self.u.reg_read(UC_ARM_REG_PC):#x} lr {self.u.reg_read(UC_ARM_REG_LR):#x} in {fn:#x}")
    def malloc(self, n): return self.call(S.M4_MALLOC, n)

    # ---- stock's model
    def create(self, kind):
        self.w32(STRS, 0); assert self.call(S.STORE_CREATE, self.store, kind, STRS); return self.r32(STRS)
    def child(self, kind, parent):
        self.w32(STRS, 0); assert self.call(S.CHILD_CREATE, self.store, kind, parent, STRS); return self.r32(STRS)
    def children(self, o): return [self.r32(o + S.NODE_CHILDREN + 4 * i) for i in range(self.r32(o + S.NODE_CHILD_N))]
    def set(self, o, key, v): self.call(S.PROP_SET, o, key, v & 0xFFFFFFFF)
    def prop(self, o, key):
        n, a = self.r32(o + 0xC), self.r32(o + 8)
        for i in range(n):
            v, k = struct.unpack("<IH", bytes(self.u.mem_read(a + 8 * i, 6)))
            if k == key:
                return v - (1 << 32) if v >= 1 << 31 else v
        return None
    def edit(self, h, key, value):
        """The model's 0xD0 edit (what stock's view posts for an encoder slot's 0xCF)."""
        self.u.mem_write(EV, struct.pack("<HHIIi", 0xD0, 0, h, key, value))
        self.call(S.MODEL_CHILD_EDIT, S.MODEL, EV)

    # ---- stock's page
    def notify(self, code, a=0):
        self.u.mem_write(EV, struct.pack("<HHIII", code, 0, a, 0, 0))
        return self.call(S.SYNTH_VIEW_NOTIFY, self.view, EV)
    def tick(self, dt=300):
        """One pass of the view's tick (its class's slot 0: A10, then stock's tick), dt ms after the last."""
        self.ms(self.now() + dt)
        self.u.mem_write(STRS + 0x100, bytes(64))
        self.call(self.r32(S.SYNTH_VIEW_TICK_SLOT), self.view, STRS + 0x100)
    def ui_tick(self, dt=0):
        self.ms(self.now() + dt); self.call(SYM["an_tick"])
    def rows(self): return [[self.h16(self.view + S.SYNTH_VIEW_KEYS + 16 * r + 2 * i) for i in range(8)] for r in range(2)]
    def show_row(self, row):
        """Select the shown row (as the page's row buttons do) and let stock refresh and bind it."""
        self.u.mem_write(self.view + self.r32(self.view + 0x2434) + SHOWN, bytes([row]))
        self.call(S.SYNTH_ROW_SHOW, self.view)
    def widget(self, slot): return self.view + SLOT0 + SLOT_STRIDE * slot
    def widgets(self):
        """The shown row's encoder widgets as stock bound them: (key, handle) per slot."""
        return [(self.h16(self.widget(i) + W_KEY), self.r32(self.widget(i) + W_HANDLE)) for i in range(8)]
    def text(self, a): return self.e.text(a + TEXT, 40)
    def wtitle(self, slot): return self.text(self.widget(slot) + W_TITLE)
    def wvalue(self, slot): return self.text(self.widget(slot) + W_VALUE)
    def turn(self, slot, delta):
        """An encoder turn on the shown row's slot, as the input task leaves it (a pending delta), then one tick of the view:
        returns the 0xD0 edits stock's view posted (its event handler re-posts the widget's 0xCF)."""
        self.posts.clear()
        self.w32(self.view + ROW_BOX + PENDING + 4 * slot, delta)
        self.tick()
        return [(h, k, v) for c, _, h, k, v in self.posts if c == 0xD0]
    def black_box(self):
        buf, ln = STRS + 0x2000, STRS + 0x1F00
        self.w32(ln, 0)
        self.call(SYM["an_print"], buf, ln, 0x800)
        return bytes(self.u.mem_read(buf, self.r32(ln))).decode()


def bl_target(a):
    i = next(MD.disasm(IMG[a - BASE:a - BASE + 4], a))
    return int(i.op_str[1:], 16)


def word(img, a): return struct.unpack_from("<I", img, a - BASE)[0]


def text_at(site):
    """The text stock passes at a PANE_TEXT site: its ldr r1, [pc, #imm] just before the bl."""
    for back in (2, 4, 6, 8):
        a = site - back
        i = next(MD.disasm(STOCK[a - BASE:a - BASE + 4], a), None)
        if i and i.mnemonic == "ldr" and i.op_str.startswith("r1, [pc, #"):
            return word(STOCK, ((a + 4) & ~3) + int(i.op_str.split("#")[1].rstrip("]"), 16))
    raise AssertionError(f"no literal before {site:#x}")


STR_DOUBLE_TAP = text_at(S.PANE_TEXT_1) if BUILT else 0


def same(a, n=4): return IMG[a - BASE:a - BASE + n] == STOCK[a - BASE:a - BASE + n]


@unittest.skipUnless(BUILT, "no analog group in this image (1.6.1, or left out)")
class Sites(unittest.TestCase):
    def test_each_site_goes_to_our_code(self):
        if DISABLED:
            self.skipTest("BE_DISABLE=analog: Disabled checks the sites")
        self.assertEqual(word(IMG, S.SYNTH_VIEW_TICK_SLOT), SYM["an_view_tick"] | 1, "A10")
        self.assertEqual(bl_target(S.PAGE_KEYS_COPY), SYM["an_page_keys"], "A6")
        for i in range(1, 7):
            self.assertEqual(bl_target(getattr(S, f"PANE_TEXT_{i}")), SYM["an_pane_text"], f"A7 {i}")

    def test_the_removed_sites_are_stock(self):
        """BE4.40: no menu entry (A3-A5: the menu has its 8 cells), no edit hook (A9: the view's event slot is stock's, edits
        of our keys go 0xCF -> 0xD0 -> set_property), and without the drums no XML, value or handle hook (X1-X3, H1-H3)."""
        for site in ("MENU_CELL", "MENU_COUNT", "MENU_VT_SLOT", "SYNTH_VIEW_EV_SLOT"):
            a = getattr(S, site)
            self.assertTrue(same(a, 20 if site == "MENU_CELL" else 2 if site == "MENU_COUNT" else 4), site)
        self.assertEqual(word(IMG, S.SYNTH_VIEW_EV_SLOT), S.SYNTH_VIEW_EVENT | 1)
        self.assertEqual(IMG[S.MENU_COUNT - BASE:S.MENU_COUNT - BASE + 2], bytes.fromhex("0823"), "movs r3, #8")
        if not DRUMS:
            for site in ("INST_SAVE_SITE", "INST_LOAD", "CONTEXT_GET", "OBJ_CTOR", "NODE_VT_CLONE", "NODE_VT_DESTROY"):
                self.assertTrue(same(getattr(S, site)), site)
        for gone in ("an_menu_event", "an_view_event", "an_context", "synth_keys"):
            self.assertNotIn(gone, SYM)

    def test_the_texts_stock_passes_at_the_pane_sites(self):
        """Two texts reach A7: "No WAV" (the labels) and "Double-tap to load WAV" (the hints); stock's "Loading..." is set
        at a site of its own (not patched)."""
        got = {STOCK[text_at(getattr(S, f"PANE_TEXT_{i}")) - BASE:][:24].split(b"\0")[0] for i in range(1, 7)}
        self.assertEqual(got, {b"No WAV", b"Double-tap to load WAV"})
        self.assertEqual(text_at(S.PANE_TEXT_2), S.STR_NO_WAV)


@unittest.skipUnless(DISABLED, "only on a BE_DISABLE=analog build")
class Disabled(unittest.TestCase):
    def test_a_bisect_leaves_the_page_stock(self):
        for site in ["SYNTH_VIEW_TICK_SLOT", "PAGE_KEYS_COPY", "SYNTH_VIEW_EV_SLOT"] + [f"PANE_TEXT_{i}" for i in range(1, 7)]:
            self.assertTrue(same(getattr(S, site)), site)
        r = Rig(types=(1, 1)); r.shows.clear(); r.tick(); r.tick()
        self.assertEqual(r.rows(), [[LEVEL, PITCH, POSITION, 0, 0, 0, 0, 0]] * 2, "stock's rows: no Type, no Analog layout")
        self.assertEqual(r.shows, [], "no refresh of ours")
        self.assertEqual(r.wtitle(2), "Position")
        self.assertEqual([r.text(r.view + o) for o in PANE_HINT], ["Double-tap to load WAV"] * 2)


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Rows(unittest.TestCase):
    """A6 + A10: each oscillator row's keys follow its own Type; stock binds the shown row's widgets from them."""
    def test_the_harness_reads_stocks_widgets(self):
        """A stock row (both Wavetable) bound by stock's page build: the widget offsets used here are right."""
        r = Rig(); r.tick()
        self.assertEqual(r.widgets()[:3], [(LEVEL, r.h1), (PITCH, r.h1), (POSITION, r.h1)])
        self.assertEqual([r.wtitle(i) for i in range(3)], ["Level", "Pitch", "Position"])

    def test_rows_per_type_for_each_oscillator(self):
        cases = {(0, 0): (WT_ROW, WT_ROW), (1, 0): (AN_ROW1, WT_ROW), (0, 1): (WT_ROW, AN_ROW2), (1, 1): (AN_ROW1, AN_ROW2)}
        for types, want in cases.items():
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertEqual(r.rows(), [list(w) for w in want])
                for row, h in ((0, r.h1), (1, r.h2)):
                    if row:
                        r.show_row(1)
                    self.assertEqual(r.widgets(), [(k, h if k else 0) for k in want[row]], f"row {row} as stock bound it")

    def test_the_titles_stock_draws_for_them(self):
        r = Rig(types=(1, 1)); r.tick()
        self.assertEqual([r.wtitle(i) for i in range(5)], ["Level", "Pitch", "Shape", "Type", "Glide"])
        self.assertEqual([r.wvalue(i) for i in (2, 3, 4)], ["48", "Analog", "0.0%"], "Shape 48 (saw), Glide 0")
        r.show_row(1)
        self.assertEqual([r.wtitle(i) for i in range(5)], ["Level", "Pitch", "Shape", "Type", "Sync 1>2"], "J5")
        self.assertEqual(r.wvalue(4), "Off")
        r = Rig(); r.tick()
        self.assertEqual((r.wtitle(3), r.wvalue(3), r.wtitle(4)), ("Type", "Wavetable", ""))

    def test_sync_whenever_osc2_is_analog(self):
        """BE4.40 J1 (docs/plan-be440-sync-identity.md Track S): OSC2's Sync 1>2 shows whenever OSC2 is Analog, under a
        Wavetable OSC1 (mixed sync) as under an Analog one; with OSC2 Wavetable it stays hidden (the reverse case is not
        built), and never on OSC1's row."""
        for types in ((1, 0), (0, 0)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertNotIn(SYNC, r.rows()[0] + r.rows()[1])
                r.show_row(1)
                self.assertNotIn(SYNC, [k for k, _ in r.widgets()])
        for types in ((0, 1), (1, 1)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertEqual(r.rows()[1][4], SYNC)
                self.assertNotIn(SYNC, r.rows()[0])
                r.show_row(1)
                self.assertEqual(r.widgets()[4], (SYNC, r.h2), "bound to OSC2")
                self.assertEqual(r.wtitle(4), "Sync 1>2")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Heal(unittest.TestCase):
    """A Type edit (stock's 0xD0 path, no page rebuild) is shown within one tick pass."""
    def test_a_type_edit_is_shown_within_one_tick(self):
        r = Rig(); r.tick(); r.tick()
        self.assertEqual(r.rows(), [WT_ROW, WT_ROW])
        r.shows.clear()
        r.edit(r.h1, TYPE, 1)
        r.tick()
        self.assertEqual(r.rows(), [AN_ROW1, WT_ROW])
        self.assertEqual(r.shows, [1], "one refresh of the shown row")
        self.assertEqual(r.widgets()[:5], [(k, r.h1) for k in AN_ROW1[:5]])
        r.edit(r.h2, TYPE, 1); r.tick()
        self.assertEqual(r.rows(), [AN_ROW1, AN_ROW2])
        r.edit(r.h1, TYPE, 0); r.tick()
        self.assertEqual(r.rows(), [WT_ROW, AN_ROW2], "OSC1 back to Wavetable: OSC2's row keeps Sync 1>2 (J1)")
        self.assertEqual(r.widgets()[:5], [(LEVEL, r.h1), (PITCH, r.h1), (POSITION, r.h1), (TYPE, r.h1), (0, 0)],
                         "stock's Position back on WT1's slot 2, slot 4 empty")
        self.assertIn(" heals=3 ", r.black_box(), "three repairs (the A6 copy's own fix is not counted)")

    def test_no_refresh_when_nothing_changed(self):
        r = Rig(types=(1, 0)); r.tick()
        r.shows.clear()
        for _ in range(4):
            r.tick()
        self.assertEqual(r.shows, [])

    def test_stocks_widgets_take_the_rewritten_keys(self):
        """P0 T5: the keys our tick writes into view + SYNTH_VIEW_KEYS are what stock's widgets edit after its refresh, with
        no page rebuild: a turn of each slot is stock's 0xCF -> its view's 0xD0 for the oscillator and our key, clamped
        to the descriptor's range by the widget, and the model stores it."""
        r = Rig(); r.tick()
        r.edit(r.h1, TYPE, 1); r.tick()
        self.assertEqual(r.turn(2, 0x3200), [(r.h1, SHAPE, 49)], "Shape 48 -> 49")
        self.assertEqual(r.turn(2, 0x3200 * 1000), [(r.h1, SHAPE, 127)], "the widget stops at the top of 0..127")
        sent = r.turn(4, 0x3200 * 1000)
        self.assertEqual(sent, [(r.h1, GLIDE, 1000)], "Glide 0..1000")
        for h, k, v in sent:
            r.edit(h, k, v)
        self.assertEqual(r.prop(r.w1, GLIDE), 1000)
        self.assertEqual(r.prop(r.w2, GLIDE), 0, "the other oscillator keeps its own")
        sent = r.turn(3, -0x3200 * 10)
        self.assertEqual(sent, [(r.h1, TYPE, 0)], "Type -> Wavetable")
        for h, k, v in sent:
            r.edit(h, k, v)
        r.tick()
        self.assertEqual(r.rows()[0], WT_ROW, "turning Type on the page switches the layout on the next pass")
        self.assertEqual(r.turn(2, 0x3200)[0][1], POSITION, "slot 2 edits stock's Position again")

    def test_an_oscillator_that_turns_analog_gets_its_settings_again(self):
        """The M7 keeps Shape / Sync / Glide only while the oscillator is Analog; an edit of Type alone does not send them,
        so the tick does: SET_PROPERTY(track, key, value, index) as stock's own edit does, once."""
        r = Rig(); r.tick()
        r.set(r.w2, SHAPE, 100); r.set(r.w2, SYNC, 1)
        r.edit(r.h2, TYPE, 1)
        self.assertEqual([(k, v, i) for _, k, v, i in r.props], [(TYPE, 1, 1)], "stock's edit: Type alone")
        r.props.clear(); r.tick()
        th = r.r32(r.track + 0x10)
        self.assertEqual(r.props, [(th, SHAPE, 100, 1), (th, SYNC, 1, 1), (th, GLIDE, 0, 1)])
        r.props.clear(); r.tick(); r.tick()
        self.assertEqual(r.props, [], "once")
        self.assertIn(" resends=1", r.black_box())
        r = Rig(types=(1, 1)); r.props.clear(); r.tick(); r.tick()
        self.assertEqual(r.props, [], "an oscillator Analog before the page saw it: stock's push sent everything")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Defensive(unittest.TestCase):
    """Stock stores what a file says (test_native_props): Type is Analog only when it is exactly 1, and no value of ours
    indexes anything on the page."""
    def test_only_type_1_is_analog(self):
        for t in (2, 7, 0x101, -1):
            with self.subTest(type=t):
                r = Rig(types=(t, t)); r.tick(); r.tick()
                self.assertEqual(r.rows(), [WT_ROW, WT_ROW])
                self.assertEqual([r.text(r.view + o) for o in PANE_HINT], ["Double-tap to load WAV"] * 2)
                self.assertNotIn("Analog", r.text(r.view + PANE_LABEL[0]) + r.text(r.view + PANE_LABEL[1]))
                self.assertIn(" oscs=0 ", r.black_box())

    def test_out_of_range_values_on_the_page(self):
        """Shape, Sync and Glide far outside their ranges (and negative): the page renders, the widgets show them without a
        table index (Shape is an integer, Sync a two-entry list stock clamps, Glide a percentage), and a turn brings the
        value back into range."""
        for shape, sync, glide in ((1000, 5, 5000), (-5, -1, -1), (0x7FFFFFFF, 0x10000, -0x80000000)):
            with self.subTest(values=(shape, sync, glide)):
                r = Rig(types=(1, 1), page=False)
                for o in (r.w1, r.w2):
                    r.set(o, SHAPE, shape); r.set(o, SYNC, sync); r.set(o, GLIDE, glide)
                r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view); r.notify(0xA8)
                r.tick(); r.tick()
                self.assertEqual(r.rows(), [AN_ROW1, AN_ROW2])
                self.assertEqual(r.wvalue(2), str(shape))
                r.show_row(1)
                self.assertIn(r.wvalue(4), ("Off", "On"))
                self.assertEqual(r.turn(2, -0x3200), [], "stock's widget holds a value it cannot place at its bottom")
                [(h, k, v)] = r.turn(2, 0x3200 * 3)
                self.assertEqual((h, k), (r.h2, SHAPE))
                self.assertTrue(0 <= v <= 127, v)

    def test_the_type_widget_with_a_bad_value(self):
        """A Type stock's list widget cannot name: it shows one of its two labels (measured: 'Analog' for 7 and -1) while the
        page and the M7 treat the oscillator as Wavetable. Cosmetic; recorded so a change is seen."""
        r = Rig(types=(7, 0)); r.tick()
        self.assertEqual(r.rows()[0], WT_ROW)
        self.assertIn(r.wvalue(3), ("Wavetable", "Analog"))


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Panes(unittest.TestCase):
    """A7: an Analog oscillator's pane texts, per pane (the slot's offset says which)."""
    def texts(self, r, offs): return [r.text(r.view + o) for o in offs]

    def test_pane_texts_per_oscillator(self):
        for types in ((1, 0), (0, 1), (1, 1), (0, 0)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                want = ["Analog oscillator (Shape)" if t else "Double-tap to load WAV" for t in types]
                self.assertEqual(self.texts(r, PANE_HINT), want, "stock's row refresh, through A7")
                self.assertEqual(self.texts(r, PANE_TITLE), ["WT1: ", "WT2: "], "the title is stock's (no PANE_TEXT site)")

    def test_the_text_sites_per_pane(self):
        """an_pane_text on the built page with each text stock passes at the six sites."""
        cases = ((S.STR_NO_WAV, "No WAV", "Analog"), (STR_DOUBLE_TAP, "Double-tap to load WAV", "Analog oscillator (Shape)"))
        r = Rig(types=(1, 0)); r.tick()
        for pane, analog in ((0, True), (1, False)):
            for at, stock, ours in cases:
                for slot in (PANE_LABEL[pane], PANE_HINT[pane]):
                    with self.subTest(pane=pane, text=stock, slot=hex(slot)):
                        r.call(SYM["an_pane_text"], r.view + slot, at)
                        self.assertEqual(r.text(r.view + slot), ours if analog else stock)
        r.call(SYM["an_pane_text"], STRS + 0x400, S.STR_NO_WAV)
        self.assertEqual(r.e.text(STRS + 0x400 + TEXT), "No WAV", "not a pane of a synth view: stock's text")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Indicator(unittest.TestCase):
    """D3 (Jeff's 'visible CPU limit'): the first Analog oscillator's 'No WAV' label shows the M7's LOAD, from the black
    box's counters (D0L: seen, permille sum, peak, bins <50 / 50-80 / 80-100 / 100+ %)."""
    def load(self, r, seen, pm_sum, over):
        r.u.mem_write(D0L, struct.pack("<7I", seen, pm_sum, 0, 0, 0, 0, over))

    def run_tick(self, r, t, pane=0):
        r.ms(t); r.ui_tick(); r.ms(t + 1)
        r.tick(dt=0)
        return r.text(r.view + PANE_LABEL[pane])

    def test_the_label_shows_the_load_and_marks_overruns(self):
        for types, pane in (((1, 0), 0), ((0, 1), 1), ((1, 1), 0)):
            with self.subTest(types=types):
                r = Rig(types=types)
                self.load(r, 1000, 600000, 0); self.run_tick(r, 2000, pane)
                self.load(r, 1100, 600000 + 63000, 0)
                self.assertEqual(self.run_tick(r, 2600, pane), "Analog CPU 63%")
                self.load(r, 1200, 663000 + 90000, 2)
                self.assertEqual(self.run_tick(r, 3200, pane), "Analog CPU 90% !", "a block over budget in the last 2 s")
                self.load(r, 1300, 753000 + 50000, 2)
                self.assertEqual(self.run_tick(r, 5300, pane), "Analog CPU 50%", "the ! goes after 2 s")
                self.assertEqual(r.u.mem_read(r.view + PANE_LABEL[pane] + 0x5C, 1)[0], 1, "marked for a redraw")
                if types == (1, 1):
                    self.assertNotIn("CPU", r.text(r.view + PANE_LABEL[1]), "one label: the first Analog oscillator's")

    def test_a_save_restarting_the_counters_is_not_a_spike(self):
        r = Rig(types=(1, 0), d0l=(1000, 600000, 0, 0, 0, 0, 5))   # the boot's first look only takes the counters
        self.run_tick(r, 2000)
        self.load(r, 10, 7000, 0)                               # LOADREQ: a save starts a new stretch
        self.run_tick(r, 2600)
        self.load(r, 110, 7000 + 40000, 0)
        self.assertEqual(self.run_tick(r, 3200), "Analog CPU 40%")

    def test_no_analog_oscillator_and_no_dsp(self):
        r = Rig(); r.titles.clear()
        self.run_tick(r, 2000)
        self.assertEqual([t for a, t in r.titles if a in (r.view + PANE_LABEL[0], r.view + PANE_LABEL[1])], [],
                         "both Wavetable: the labels are stock's")
        r = Rig(types=(0, 1), area=False)
        self.assertEqual(self.run_tick(r, 2000, 1), "Analog (DSP off)", "the M7 has not taken the area")

    def test_the_label_goes_back_when_the_oscillator_does(self):
        """Type back to Wavetable on the page (no rebuild): the label our tick wrote gets stock's 'No WAV' again, and the
        indicator moves to the oscillator that is still Analog."""
        r = Rig(types=(1, 1))
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        self.load(r, 1100, 663000, 0)
        self.assertEqual(self.run_tick(r, 2600), "Analog CPU 63%")
        r.edit(r.h1, TYPE, 0)
        self.run_tick(r, 3200)
        self.assertEqual(self.texts(r), ["No WAV", "Analog CPU 63%"])
        r.edit(r.h2, TYPE, 0)
        self.run_tick(r, 3800)
        self.assertEqual(self.texts(r), ["No WAV", "No WAV"])

    def texts(self, r): return [r.text(r.view + o) for o in PANE_LABEL]

    def test_over_budget_blocks_with_analog_are_counted(self):
        r = Rig(types=(1, 0))
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        r.w32(SA + 0x40, 50)                                    # the M7: blocks with an Analog voice
        self.load(r, 1100, 700000, 3); self.run_tick(r, 2600)
        self.load(r, 1200, 800000, 4); self.run_tick(r, 3200)   # no Analog block in between
        self.assertIn(" over_with_analog=3 ", r.black_box())


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class BlackBox(unittest.TestCase):
    def test_black_box_line(self):
        r = Rig(types=(1, 1)); r.tick()
        r.u.mem_write(SA + 0x40, struct.pack("<16I", 10, 1000, 30000, 2, 7, 128, 0, 9, 1, 0, 0, 3, 20, 4, 0, 0))
        lines = r.black_box().splitlines(keepends=True)          # with drums: the ANALOG and DRUMS lines
        line = next(l for l in lines if l.startswith("ANALOG"))
        for part in ("ANALOG gen=1", "keys=ok", "oscs=2", "| blocks=10", "avg_cyc=1600", "peak_cyc=30000", "osc_peak=2",
                     "share<5%=9 5-15=1 15-30=0 30+=0", "voices_peak=3", "renders=20", "n_max=128", "props=7", "roots=4",
                     "over_with_analog=0", "| ui page_rows=1 heals=1 resends=0"):
            self.assertIn(part, line)
        self.assertTrue(line.endswith("\r\n"))

    def test_drums_line(self):
        """S6c: the DRUMS line (before ANALOG's), from the M7's drum stats lines at +0x80 (synth_shared.h SaDrumStats)."""
        if not DRUMS:
            self.skipTest("no drums in this image")
        r = Rig()
        r.u.mem_write(SA + 0x80, struct.pack("<19I", 8, 4000, 70000, 3, 40, 5, 1, 2, 2, 0, 4, 0, 0, 128, 0, 0, 1, 0, 0))
        line = r.black_box()
        self.assertTrue(line.startswith("DRUMS blocks=8 avg_cyc=8000 (pm=6) peak_cyc=70000 (pm=54)"), line[:120])
        for part in ("share>=5%=1 >=15=0 >=30=0", "voices_peak=3", "renders=40", "starts=5 retrigs=1", "quiet_ends=4",
                     "detects=2 carriers=2 unbuilt=0", "n_max=128", "unbound=0 short_bufs=0"):
            self.assertIn(part, line.split("\r\n")[0])
        self.assertIn("\r\nANALOG gen=1", line)

    def test_why_the_area_is_not_taken(self):
        r = Rig(types=(1, 0), area=False)
        r.w32(TAKE, 0xA5 << 24 | 2 << 8 | 2)                     # the M7: the Region alone, twice
        line = r.black_box()
        self.assertIn("ANALOG area not taken (the pool kept less than 1 MB: Region only, times=2)", line)
        self.assertIn("oscs=1", line, "the oscillators are counted from stock's model all the same")
        r = Rig(area=False); r.take(boot=7)
        self.assertIn("(header for boot 7, this boot 1)", r.black_box())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
