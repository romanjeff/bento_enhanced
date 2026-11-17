# SPDX-License-Identifier: GPL-3.0-or-later
"""The Wavetable page with Analog oscillators (BE4.40, docs/plan-native-props.md 3.4: A6, A7, A10; identity,
docs/plan-be440-sync-identity.md track I: T0, T1, T2, B3, D1, D2, B1', Y1, V1, H1, W1), m4_synth.c in the built image, on
stock 1.6.8's own synth page.

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
Identity (track I): the pane titles through stock's row refresh (PANE_TITLE_SITE, the shim) and the tick; visibility is
the hidden byte up the parent chain (stock's paint walk skips a hidden child, WIDGET_PAINT_KIDS); the dial drawn by stock's
GFX_FILL into the display emulator's frame buffer (tools/display_emulator render.py), read back as pixels; a kept WAV is
GET_STRING answering Filename (key 0x81) for that oscillator (a harness answer: there is no sample cache here).
On a BE_DISABLE=analog build the page must stay stock's (Disabled); skipped on a build without the analog group (1.6.1).
"""
import json, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
sys.path.insert(0, str(HERE))
import emu
import render
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
SUBFM, FM12 = 0x18B, 0x132                                  # P3 Q1 (docs/plan-p3-fm-bits.md 2.1): Sub FM, FM 1>2
BITS = 0x1A4                                                 # BE4.41's Bit Depth: BE4.42 had no such key (slot 6 empty)
FMATK, FMDEC = 0x1A4, 0x1A0                                  # BE4.43 (plan BE4.43): the FM envelope (0x1A4 Bit Depth's once)
LEVEL, PITCH, POSITION = 0xC4, 0x03, 0xC9                    # stock's oscillator row (plan 3.4)
# a Wavetable oscillator: stock's row, Type in the free 4th slot; BE4.44 W1 (docs/plan-wt-fm.md 2): its FM in Analog's knob
# numbers, slot 4 empty: OSC1 Sub FM, OSC2 FM 1>2 (D2: every OSC1 type feeds B), then FM Atk / FM Dec
WT_ROW1 = [LEVEL, PITCH, POSITION, TYPE, 0, SUBFM, FMATK, FMDEC]
WT_ROW2 = [LEVEL, PITCH, POSITION, TYPE, 0, FM12, FMATK, FMDEC]
STOCK_ROW = [LEVEL, PITCH, POSITION, 0, 0, 0, 0, 0]               # stock's own (np_ok false: none of ours)
# P3 2.2: an Analog row's slot 5 Sub FM; OSC2's slot 7 FM 1>2 only while OSC1 is Analog (OD3: hidden). BE4.42: slot 6
# (BE4.41's Bit Depth) is empty, so FM 1>2 stays on its encoder. BE4.43 (Jeff's design 2026-11-16): one FM amount per
# oscillator in slot 5 (OSC1 Sub FM; OSC2 FM 1>2 while OSC1 is Analog, else Sub FM) and its envelope, FM Atk / FM Dec in 6 / 7
AN_ROW1 = [LEVEL, PITCH, SHAPE, TYPE, GLIDE, SUBFM, FMATK, FMDEC]   # OSC1 Analog: Shape for Position, Glide
AN_ROW2 = [LEVEL, PITCH, SHAPE, TYPE, SYNC, FM12, FMATK, FMDEC]     # OSC2 Analog, OSC1 Analog: Sync 1>2, FM 1>2
# BE4.44 W4 (reviewed change, D2's last case): next to a Wavetable OSC1 too, OSC2's slot 5 is FM 1>2 (a Wavetable OSC1 feeds
# B), so an Analog OSC2's row is the same whatever OSC1 is (BE4.43: Sub FM there)
AN_ROW2_WT = [LEVEL, PITCH, SHAPE, TYPE, SYNC, FM12, FMATK, FMDEC]  # OSC2 Analog, OSC1 Wavetable: Sync 1>2 (J1), FM 1>2
WT_CHILDREN = bytes.fromhex("0606080a0a09090b0b0c0d")           # a Wavetable track's children (stock's list)
# stock 1.6.8's synth page, measured here on stock's code (rc33 implA p0/t5.py, probe2.py and this suite's runs):
SLOT0, SLOT_STRIDE = 0x70, 0x468   # the row's 8 encoder widgets: view + SLOT0 + SLOT_STRIDE * slot
W_KEY, W_HANDLE = 0x9C, 0x34       # a widget's key (u16) and the object handle it edits (the 0xCF event's +4)
W_VALUE, W_TITLE = 0xA8, 0x3C8     # its value and title labels (SLOT_TITLE targets: their text at +0x31)
TEXT = 0x31                        # a label's text (SLOT_TITLE copies it there)
SHOWN = 0x11D88                    # the shown row: byte view + [view + 0x2434] + SHOWN (SYNTH_ROW_BIND)
PANE_TITLE = (0x29C0, 0x2A30)      # "WT1: <wav>" / "WT2: <wav>" (stock's '%s%.20s', not a PANE_TEXT site)
PANE_LABEL = (0x3114, 0x3474)      # the "No WAV" labels (VIEW_NOWAV_LABEL, + 0x360): children of the Granular waveform
GRANULAR = (0x2E90, 0x31F0)        # widget, hidden on the Wavetable page (measured: B2)
PANE_HINT = (0x37BC, 0x3A9C)       # the "Double-tap to load WAV" hints
PANE = (0x3550, 0x3830)            # the oscillator panes (rect (11,271,622,142) / (11,85,622,144), y up), class 0x081B6910
PARENT, HIDDEN = 0x2C, 0x30        # a widget's parent and hidden byte (UI_POST, the paint walk)
ORANGE, LIT = 0xF6, 0xF2           # stock's palette: the dial's pointer, a lit glyph
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
    def __init__(self, types=(0, 0), area=True, page=True, d0l=None, occupy=None):
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
        if occupy:                                              # a key of ours taken: stock's builder, the key, then np_keys
            self.call(S.REG_BUILD, self.malloc(0x400))
            self.u.mem_write(STRS + 0x3000, b"Busy\0")
            self.u.mem_write(self.r32(S.REGISTRY) + occupy * 32, struct.pack("<HBBI", occupy, 10, 0, STRS + 0x3000))
            self.call(SYM["np_keys"])
        else:
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
        self.wavs, self.title_hits, self.fills, self.stock_paints = {}, [], [], []
        def getstr(uc, a, sz, _):                              # a kept WAV: the oscillator's Filename (harness answer)
            h = struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 4))[0]
            if uc.reg_read(UC_ARM_REG_R2) == 0x81 and h in self.wavs:
                uc.mem_write(uc.reg_read(UC_ARM_REG_R3), self.wavs[h].encode() + b"\0"); uc.reg_write(UC_ARM_REG_R0, 1)
                uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, getstr, begin=S.GET_STRING, end=S.GET_STRING)
        if BUILT:
            u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.title_hits.append(uc.reg_read(UC_ARM_REG_R0)),
                       begin=S.PANE_TITLE_SITE, end=S.PANE_TITLE_SITE)
            u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.fills.append(uc.reg_read(UC_ARM_REG_R1)), begin=S.GFX_FILL, end=S.GFX_FILL)
            u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.stock_paints.append(uc.reg_read(UC_ARM_REG_R0)),
                       begin=S.WT_PANE_PAINT, end=S.WT_PANE_PAINT)
            self.marks, self.our_paints = [], []
            tick = word(STOCK, S.WT_PANE_PAINT_SLOT - 4) & ~1     # the pane class's slot 0: its tick (0x081844E0)
            def mark(uc, a, sz, _):                             # a pane's repaint requests as its tick takes them
                w = uc.reg_read(UC_ARM_REG_R0)
                self.marks.append((w, *uc.mem_read(w + 0x31, 2)))
            u.hook_add(UC_HOOK_CODE, mark, begin=tick, end=tick)
            if HAVE:
                u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.our_paints.append(uc.reg_read(UC_ARM_REG_R0)),
                           begin=SYM["an_wave_paint"], end=SYM["an_wave_paint"])
            render.boot_gfx(e)                                  # the frame buffer and surface GFX_FILL draws into
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
    # ---- identity
    def visible(self, a):
        """No hidden byte from the widget up its parent chain (stock's paint walk skips a hidden child and its subtree)."""
        for _ in range(16):
            if not a:
                return True
            if self.u.mem_read(a + HIDDEN, 1)[0]:
                return False
            a = self.r32(a + PARENT)
        return False
    def titles_(self): return [self.text(self.view + o) for o in PANE_TITLE]
    def hints(self): return [self.text(self.view + o) for o in PANE_HINT]
    def paint(self, pane, full=0, partial=1):
        """The pane class's paint slot (D1) with a paint context {full, partial, surface}; returns our FILL calls."""
        self.fills.clear(); self.stock_paints.clear()
        self.u.mem_write(render.CTX, bytes([full, partial, 0, 0]) + struct.pack("<I", render.SURF))
        self.call(self.r32(self.r32(self.view + PANE[pane]) + 4), self.view + PANE[pane], render.CTX)
        return len(self.fills)
    def glass(self, pane):
        """The pane's pixels as the screen shows them (render.ui: glass[Y][X], Y down), and its centre there."""
        g = render.ui(self.e)
        x, y, w, h = struct.unpack("<4i", bytes(self.u.mem_read(self.view + PANE[pane] + 4, 16)))
        top = 480 - (y + h)
        return g[top:top + h, x:x + w], (w // 2, h - h // 2)
    def asked(self, pane=0, dt=50):
        """One view tick: the repaint requests (full, partial) pane `pane` carried into stock's pane tick."""
        self.marks.clear(); self.tick(dt=dt)
        got = [(f, p) for w, f, p in self.marks if w == self.view + PANE[pane]]
        return got[-1] if got else None
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
        for i in (1, 3, 5, 6):
            self.assertEqual(bl_target(getattr(S, f"PANE_TEXT_{i}")), SYM["an_pane_text"], f"A7 hint {i}")
        for i in (2, 4):
            self.assertTrue(same(getattr(S, f"PANE_TEXT_{i}")), f"T2: the hidden 'No WAV' label's site {i} is stock's")
        self.assertEqual(word(IMG, S.WT_PANE_PAINT_SLOT), SYM["an_wave_paint"] | 1, "D1")
        for i in (1, 2, 3):
            self.assertEqual(bl_target(getattr(S, f"VALUE_TEXT_{i}")), SYM["an_value_text"], f"V1 / Y1 value text {i}")
        for site, wrap, fn, ldr in (("PANE_TITLE_SITE", "wrap_pane_title", "an_pane_title", "ldr.w r2, [sp, #0x32c]"),
                                    ("BROWSE_POST_SITE", "wrap_browse", "an_browse", "ldr r2, [sp, #0x20]")):
            self.assertEqual(bl_target(getattr(S, site)), SYM[wrap], site)
            ins = list(MD.disasm(IMG[SYM[wrap] - BASE:SYM[wrap] - BASE + 8], SYM[wrap]))
            self.assertEqual(f"{ins[0].mnemonic} {ins[0].op_str}", ldr, f"{site}: the shim takes the handle from stock's frame")
            self.assertEqual((ins[1].mnemonic, int(ins[1].op_str[1:], 16)), ("b.w", SYM[fn]))

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
        """Two texts reach the six sites: "No WAV" (the labels, 2 / 4) and "Double-tap to load WAV" (the hints, 1 / 3 / 5 / 6);
        stock's "Loading..." is set at a site of its own (not patched)."""
        got = {i: STOCK[text_at(getattr(S, f"PANE_TEXT_{i}")) - BASE:][:24].split(b"\0")[0] for i in range(1, 7)}
        self.assertEqual({i for i, t in got.items() if t == b"No WAV"}, {2, 4})
        self.assertEqual({i for i, t in got.items() if t == b"Double-tap to load WAV"}, {1, 3, 5, 6})
        self.assertEqual(text_at(S.PANE_TEXT_2), S.STR_NO_WAV)

    def test_t0_strings(self):
        """T0 (J2): the row prefixes and the mod destination format, in place (each its one literal reference: build.py
        checks it), zero-padded to their stock slots; no other word of the image points at them."""
        if DISABLED:
            self.skipTest("BE_DISABLE=analog: Disabled checks the strings")
        for site, want in (("STR_PREFIX_OSC1", b"OSC1: \0\0"), ("STR_PREFIX_OSC2", b"OSC2: \0\0"),
                           ("STR_DEST_OSC_FMT", b"OSC%d: %s\0\0\0")):
            a = getattr(S, site)
            self.assertEqual(IMG[a - BASE:a - BASE + len(want)], want, site)
            refs = [i for i in range(0, len(STOCK) - 3, 4) if word(IMG, BASE + i) == a]
            self.assertEqual(len(refs), 1, f"{site}: one literal")


@unittest.skipUnless(DISABLED, "only on a BE_DISABLE=analog build")
class Disabled(unittest.TestCase):
    def test_a_bisect_leaves_the_page_stock(self):
        for site in ["SYNTH_VIEW_TICK_SLOT", "PAGE_KEYS_COPY", "SYNTH_VIEW_EV_SLOT", "PANE_TITLE_SITE", "WT_PANE_PAINT_SLOT",
                     "BROWSE_POST_SITE"] + [f"PANE_TEXT_{i}" for i in range(1, 7)] + [f"VALUE_TEXT_{i}" for i in (1, 2, 3)]:
            self.assertTrue(same(getattr(S, site)), site)
        for site in ("STR_PREFIX_OSC1", "STR_PREFIX_OSC2", "STR_DEST_OSC_FMT"):
            self.assertTrue(same(getattr(S, site), 12 if site == "STR_DEST_OSC_FMT" else 8), site)
        r = Rig(types=(1, 1)); r.shows.clear(); r.tick(); r.tick()
        self.assertEqual(r.rows(), [STOCK_ROW] * 2, "stock's rows: no Type, no Analog layout")
        self.assertEqual(r.shows, [], "no refresh of ours")
        self.assertEqual(r.wtitle(2), "Position")
        self.assertEqual(r.hints(), ["Double-tap to load WAV"] * 2)
        self.assertEqual(r.titles_(), ["WT1: ", "WT2: "])


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Rows(unittest.TestCase):
    """A6 + A10: each oscillator row's keys follow its own Type; stock binds the shown row's widgets from them."""
    def test_the_harness_reads_stocks_widgets(self):
        """A stock row (both Wavetable) bound by stock's page build: the widget offsets used here are right."""
        r = Rig(); r.tick()
        self.assertEqual(r.widgets()[:3], [(LEVEL, r.h1), (PITCH, r.h1), (POSITION, r.h1)])
        self.assertEqual([r.wtitle(i) for i in range(3)], ["Level", "Pitch", "Position"])

    def test_rows_per_type_for_each_oscillator(self):
        cases = {(0, 0): (WT_ROW1, WT_ROW2), (1, 0): (AN_ROW1, WT_ROW2), (0, 1): (WT_ROW1, AN_ROW2_WT), (1, 1): (AN_ROW1, AN_ROW2)}
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
        self.assertEqual([r.wtitle(i) for i in range(8)], ["Level", "Pitch", "Shape", "Type", "Glide", "Sub FM", "FM Atk",
                                                           "FM Dec"])
        self.assertEqual([r.wvalue(i) for i in (2, 3, 4, 5, 6, 7)], ["Saw", "Analog", "0.0%", "0.0%", "0 ms", "Inf"],
                         "Shape 48 (V1: the saw), Glide 0; P3: Sub FM 0 (the template's defaults); BE4.43: Atk 0, Dec Inf")
        r.show_row(1)
        self.assertEqual([r.wtitle(i) for i in range(8)], ["Level", "Pitch", "Shape", "Type", "Sync 1>2", "FM 1>2", "FM Atk",
                                                           "FM Dec"], "J5; BE4.43: one amount (FM 1>2), its envelope")
        self.assertEqual([r.wvalue(i) for i in (4, 5, 6, 7)], ["Off", "0.0%", "0 ms", "Inf"])
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

    def test_the_fm_slots(self):
        """BE4.43 (Jeff's design; P3 2.2 / OD3 before it): one FM amount per Analog oscillator in slot 5, bound to it: OSC1's
        Sub FM; OSC2's FM 1>2 while OSC1 is Analog, else (mixed: OSC1 Wavetable, whose signal the M7 cannot read) its Sub FM,
        so OSC2 always has an FM source; then the oscillator's own FM Atk / FM Dec in slots 6 / 7 (no Bit Depth). A Type flip
        of OSC1 swaps OSC2's slot 5 within one tick (fix_rows), the shown row rebound.
        BE4.44 W1 (reviewed change, docs/plan-wt-fm.md 2, D2): a Wavetable row has them too, at the same knob numbers: OSC1
        Sub FM, OSC2 FM 1>2 whatever OSC1 is (D2), FM Atk / FM Dec. An Analog OSC2 next to a Wavetable OSC1 keeps BE4.43's Sub
        FM until W4, where a Wavetable OSC1 feeds B (so its slot 5 shows what plays).
        BE4.44 W4 (reviewed change, D2's last case, plan 8 W4's exit): OSC2's slot 5 is FM 1>2 in all four type combinations;
        a Type flip of OSC1 leaves OSC2's row as it is (nothing to swap, no repaint of it)."""
        for types in ((0, 0), (1, 0), (0, 1), (1, 1)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                for row in (0, 1):
                    k = r.rows()[row]
                    an = types[row] == 1
                    amount = FM12 if row == 1 else SUBFM
                    self.assertEqual(k[5:8], [amount, FMATK, FMDEC], f"row {row}")
                    self.assertEqual(k.count(SUBFM) + k.count(FM12), 1, f"row {row}: one amount")
                    self.assertEqual(k[4] == 0, not an, f"row {row}: slot 4 empty on a Wavetable row")
                    r.show_row(row)
                    h = (r.h1, r.h2)[row]
                    self.assertEqual(r.widgets()[5:8], [(amount, h), (FMATK, h), (FMDEC, h)])
                    self.assertEqual([r.wtitle(i) for i in (5, 6, 7)], ["FM 1>2" if amount == FM12 else "Sub FM", "FM Atk",
                                                                        "FM Dec"])
                    self.assertEqual([r.wvalue(i) for i in (5, 6, 7)], ["0.0%", "0 ms", "Inf"], "the texts (fm_time_text)")
        r = Rig(types=(0, 1)); r.tick(); r.show_row(1)
        self.assertEqual((r.widgets()[5], r.wtitle(5)), ((FM12, r.h2), "FM 1>2"), "OSC1 Wavetable: OSC2 shows FM 1>2 (W4)")
        r.shows.clear(); r.edit(r.h1, TYPE, 1); r.tick()
        self.assertEqual(r.rows()[1], AN_ROW2, "OSC1 -> Analog: still FM 1>2")
        self.assertEqual((r.widgets()[5], r.wtitle(5)), ((FM12, r.h2), "FM 1>2"), "still bound on the shown row")
        r.edit(r.h1, TYPE, 0); r.tick()
        self.assertEqual(r.rows()[1], AN_ROW2_WT, "OSC1 -> Wavetable: FM 1>2 still (W4)")
        self.assertEqual(AN_ROW2_WT, AN_ROW2, "W4: one Analog OSC2 row")
        self.assertEqual((r.widgets()[5], r.wtitle(5)), ((FM12, r.h2), "FM 1>2"))

    def test_a_taken_fm_key_leaves_the_page_stock(self):
        """P3 OD2 (one group, eight since BE4.43): Sub FM's key taken by the registry (bit 4) means none of ours is written, the black box
        says keys=TAKEN:272 (0x110), and the page is stock's whatever the Types say (np_ok)."""
        r = Rig(types=(1, 1), occupy=SUBFM); r.tick(); r.tick()
        self.assertIn(" keys=TAKEN:272 ", r.black_box())
        self.assertEqual(r.rows(), [STOCK_ROW] * 2, "stock's rows: no Type, no Analog layout")
        self.assertEqual(r.wtitle(2), "Position")

    def test_the_fm_slots_edit_their_keys(self):
        """A turn of slots 5..7 is stock's 0xCF -> 0xD0 for the oscillator and our key, in the descriptor's range: the
        type-10 amounts 0..1000; BE4.43: FM Atk 0..127 and FM Dec 0..128 one value a detent (type 2, as Shape)."""
        r = Rig(types=(1, 1)); r.tick()
        self.assertEqual(r.turn(5, 0x3200 * 1000), [(r.h1, SUBFM, 1000)], "OSC1's Sub FM stops at 1000")
        r.show_row(1)
        self.assertEqual(r.turn(5, 0x3200 * 1000), [(r.h2, FM12, 1000)], "OSC2's FM 1>2 stops at 1000")
        self.assertEqual(r.turn(6, 0x3200), [(r.h2, FMATK, 1)], "FM Atk 0 -> 1")
        self.assertEqual(r.turn(6, 0x3200 * 1000), [(r.h2, FMATK, 127)], "FM Atk stops at 127")
        self.assertEqual(r.turn(7, -0x3200), [(r.h2, FMDEC, 127)], "FM Dec Inf (128) -> 127")
        self.assertEqual(r.turn(7, 0x3200 * 1000), [(r.h2, FMDEC, 128)], "FM Dec stops at 128 (Inf)")
        self.assertEqual(r.turn(7, -0x3200 * 1000), [(r.h2, FMDEC, 0)], "and at 0")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Heal(unittest.TestCase):
    """A Type edit (stock's 0xD0 path, no page rebuild) is shown within one tick pass."""
    def test_a_type_edit_is_shown_within_one_tick(self):
        r = Rig(); r.tick(); r.tick()
        self.assertEqual(r.rows(), [WT_ROW1, WT_ROW2])
        r.shows.clear()
        r.edit(r.h1, TYPE, 1)
        r.tick()
        self.assertEqual(r.rows(), [AN_ROW1, WT_ROW2])
        self.assertEqual(r.shows, [1], "one refresh of the shown row")
        self.assertEqual(r.widgets()[:5], [(k, r.h1) for k in AN_ROW1[:5]])
        r.edit(r.h2, TYPE, 1); r.tick()
        self.assertEqual(r.rows(), [AN_ROW1, AN_ROW2])
        r.edit(r.h1, TYPE, 0); r.tick()
        self.assertEqual(r.rows(), [WT_ROW1, AN_ROW2_WT], "OSC1 back to Wavetable: OSC2's row keeps Sync 1>2 (J1) and, since "
                         "BE4.44 W4, FM 1>2")
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
        self.assertEqual(r.rows()[0], WT_ROW1, "turning Type on the page switches the layout on the next pass")
        self.assertEqual(r.turn(2, 0x3200)[0][1], POSITION, "slot 2 edits stock's Position again")

    def test_an_oscillator_that_turns_analog_gets_its_settings_again(self):
        """The M7 keeps Shape / Sync / Glide (P3: Sub FM, FM 1>2) only while the oscillator is Analog; an edit of Type
        alone does not send them, so the tick does: SET_PROPERTY(track, key, value, index) as stock's own edit does, once,
        the five keys (P3 2.4: without the FM keys a Type flip on the page would lose the FM values; BE4.42: Bit Depth gone);
        BE4.43: seven, the FM envelope too."""
        r = Rig(); r.tick()
        r.set(r.w2, SHAPE, 100); r.set(r.w2, SYNC, 1); r.set(r.w2, SUBFM, 700); r.set(r.w2, FM12, 333)
        r.set(r.w2, FMATK, 20); r.set(r.w2, FMDEC, 40)
        r.edit(r.h2, TYPE, 1)
        self.assertEqual([(k, v, i) for _, k, v, i in r.props], [(TYPE, 1, 1)], "stock's edit: Type alone")
        r.props.clear(); r.tick()
        th = r.r32(r.track + 0x10)
        self.assertEqual(r.props, [(th, SHAPE, 100, 1), (th, SYNC, 1, 1), (th, GLIDE, 0, 1), (th, SUBFM, 700, 1),
                                   (th, FM12, 333, 1), (th, FMATK, 20, 1), (th, FMDEC, 40, 1)])
        r.props.clear(); r.tick(); r.tick()
        self.assertEqual(r.props, [], "once")
        self.assertIn(" resends=1", r.black_box())
        r.set(r.w1, SUBFM, 5); r.edit(r.h1, TYPE, 1); r.props.clear(); r.tick()
        self.assertEqual([(k, v, i) for _, k, v, i in r.props], [(SHAPE, 48, 0), (SYNC, 0, 0), (GLIDE, 0, 0), (SUBFM, 5, 0),
                                                                 (FM12, 0, 0), (FMATK, 0, 0), (FMDEC, 128, 0)],
                         "OSC1: the same seven (FM 1>2 harmless; BE4.43: the envelope at the template's defaults)")
        r = Rig(types=(1, 1)); r.props.clear(); r.tick(); r.tick()
        self.assertEqual(r.props, [], "an oscillator Analog before the page saw it: stock's push sent everything")

    def test_an_oscillator_that_turns_wavetable_gets_its_fm_again(self):
        """BE4.44 W1 (docs/plan-wt-fm.md 2, "Resend on a Type flip"): the M7 forgets an Analog oscillator's area when it
        leaves Analog, and a Wavetable oscillator now keeps its FM settings (Sub FM / FM 1>2, FM Atk, FM Dec), so a flip to
        Wavetable sends the same seven keys once (Shape / Sync / Glide go along: the M7 ignores them on a Wavetable
        oscillator). Both directions, each oscillator; a Type rewritten with the same kind (Wavetable 0 -> 7) sends nothing."""
        r = Rig(types=(1, 1)); r.tick()
        r.set(r.w1, SUBFM, 640); r.set(r.w1, FMATK, 3); r.set(r.w1, FMDEC, 40)
        r.edit(r.h1, TYPE, 0)
        r.props.clear(); r.tick()
        th = r.r32(r.track + 0x10)
        self.assertEqual(r.props, [(th, SHAPE, 48, 0), (th, SYNC, 0, 0), (th, GLIDE, 0, 0), (th, SUBFM, 640, 0),
                                   (th, FM12, 0, 0), (th, FMATK, 3, 0), (th, FMDEC, 40, 0)], "OSC1 -> Wavetable: its seven")
        r.props.clear(); r.tick(); r.tick()
        self.assertEqual(r.props, [], "once")
        r.set(r.w2, FM12, 900); r.edit(r.h2, TYPE, 0); r.props.clear(); r.tick()
        self.assertEqual([(k, v, i) for _, k, v, i in r.props if k in (FM12, FMATK, FMDEC)],
                         [(FM12, 900, 1), (FMATK, 0, 1), (FMDEC, 128, 1)], "OSC2 -> Wavetable: its FM 1>2 and envelope")
        r.edit(r.h2, TYPE, 7); r.props.clear(); r.tick()
        self.assertEqual(r.props, [], "Wavetable to another Wavetable value: nothing")
        self.assertIn(" resends=2", r.black_box())


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Defensive(unittest.TestCase):
    """Stock stores what a file says (test_native_props): Type is Analog only when it is exactly 1, and no value of ours
    indexes anything on the page."""
    def test_only_type_1_is_analog(self):
        for t in (2, 7, 0x101, -1):
            with self.subTest(type=t):
                r = Rig(types=(t, t)); r.tick(); r.tick()
                self.assertEqual(r.rows(), [WT_ROW1, WT_ROW2])
                self.assertEqual(r.hints(), ["Double-tap to load WAV"] * 2)
                self.assertEqual(r.titles_(), ["OSC1: ", "OSC2: "])
                self.assertEqual(r.paint(0), 0, "no dial")
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
                self.assertEqual(r.wvalue(2), "Sine" if shape < 0 else "Pulse 8%", "V1 reads the clamped Shape (P3 Q3: 0 sine)")
                r.show_row(1)
                self.assertIn(r.wvalue(4), ("Off", "On"))
                self.assertEqual(r.turn(2, -0x3200), [], "stock's widget holds a value it cannot place at its bottom")
                [(h, k, v)] = r.turn(2, 0x3200 * 3)
                self.assertEqual((h, k), (r.h2, SHAPE))
                self.assertTrue(0 <= v <= 127, v)

    def test_the_fm_values_the_widgets_show(self):
        """P3 OD4, pinned: what stock's widgets show for our new keys, in range and for a hand-edited file's out-of-range
        values (stock stores those raw: test_native_props). Type 10 (Sub FM, FM 1>2) shows the stored value / 10 as a
        percentage, past 100 % too (accepted, as Glide: the M7 clamps at 1000). BE4.42: no Bit Depth widget. BE4.43: OSC2's
        one amount, FM 1>2, in slot 5 (OSC1 Analog)."""
        cases = ((0, "0.0%"), (500, "50.0%"), (1000, "100.0%"), (1, "0.1%"), (5000, "500.0%"), (-3, "-0.3%"))
        for amt, want_amt in cases:
            with self.subTest(amount=amt):
                r = Rig(types=(1, 1), page=False)
                for o in (r.w1, r.w2):
                    r.set(o, SUBFM, amt); r.set(o, FM12, amt)
                r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view); r.notify(0xA8)
                r.tick(); r.tick()
                self.assertEqual(r.wvalue(5), want_amt, "OSC1")
                r.show_row(1)
                self.assertEqual((r.wtitle(5), r.wvalue(5)), ("FM 1>2", want_amt), "OSC2")

    def test_the_fm_envelope_shows_its_times(self):
        """BE4.43: FM Atk / FM Dec read the time the M7 plays (fm_time_text, p3_golden fm_env_time: n(x) blocks of 128 / 48
        ms, typed here from the golden's table), on both rows, through stock's bind and the tick: Atk 0 "0 ms" (instant),
        Dec 128 "Inf"; the spec's sample points (1 = 5.3 ms, 20 = 83 ms, 48 = 384 ms, 80 = 1.52 s, 112 = 5.51 s, 127 =
        10.0 s); Dec 0 one block, "2.7 ms"; a hand-edited file's out-of-range values read as the M7 clamps them (Atk past 127
        as 127, Dec past 128 as Inf, negatives as 0)."""
        cases = ((0, "0 ms", "2.7 ms"), (1, "5.3 ms", "5.3 ms"), (3, "11 ms", "11 ms"), (20, "83 ms", "83 ms"),
                 (48, "384 ms", "384 ms"), (80, "1.52 s", "1.52 s"), (112, "5.51 s", "5.51 s"), (127, "10.0 s", "10.0 s"),
                 (128, "10.0 s", "Inf"), (500, "10.0 s", "Inf"), (-4, "0 ms", "2.7 ms"))
        for v, atk, dec in cases:
            with self.subTest(value=v):
                r = Rig(types=(1, 1), page=False)
                for o in (r.w1, r.w2):
                    r.set(o, FMATK, v); r.set(o, FMDEC, v)
                r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view); r.notify(0xA8)
                r.tick(); r.tick()
                self.assertEqual((r.wvalue(6), r.wvalue(7)), (atk, dec), "OSC1")
                r.show_row(1)
                self.assertEqual((r.wvalue(6), r.wvalue(7)), (atk, dec), "OSC2")
        r = Rig(types=(1, 1)); r.tick(); r.show_row(1)
        [(h, k, v)] = r.turn(7, -0x3200 * 3)
        r.edit(h, k, v); r.tick()
        self.assertEqual((v, r.wvalue(7)), (125, "9.24 s"), "a turn: Inf -> 125 = 3464 blocks")

    def test_the_type_widget_with_a_bad_value(self):
        """Y1: a Type stock's list widget cannot name (it shows 'Analog' for 7 and -1, measured on stock's widget) reads
        'Wavetable', as the page and the M7 treat it; nothing is written (no 0xD0, the model keeps 7: a later version's value
        survives a save). Only the oscillator rows' Type widget is touched."""
        for t in (7, -1, 0x101):
            with self.subTest(type=t):
                r = Rig(types=(t, 0)); r.posts.clear(); r.tick(); r.tick()
                self.assertEqual(r.rows()[0], WT_ROW1)
                self.assertEqual((r.wtitle(3), r.wvalue(3)), ("Type", "Wavetable"))
                self.assertEqual(r.prop(r.w1, TYPE), t)
                self.assertEqual([p for p in r.posts if p[0] == 0xD0], [])
                r.show_row(2); r.tick()
                self.assertNotEqual(r.widgets()[3][0], TYPE, "row 2's slot 3 is not Type: left alone")
                self.assertNotEqual(r.wvalue(3), "Wavetable")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Panes(unittest.TestCase):
    """A7: an Analog pane's hint is empty (the dial is its content); T2: the hidden 'No WAV' labels are stock's."""
    def test_pane_hints_per_oscillator(self):
        for types in ((1, 0), (0, 1), (1, 1), (0, 0)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertEqual(r.hints(), ["" if t else "Double-tap to load WAV" for t in types], "stock's row refresh, A7")
                self.assertNotIn("Analog", r.text(r.view + PANE_LABEL[0]) + r.text(r.view + PANE_LABEL[1]), "T2")

    def test_the_hint_sites_per_pane(self):
        """an_pane_text on the built page: an Analog pane's hint is empty, a Wavetable pane's and any other slot's stock's
        (the label slots are not ours any more: stock's text there too)."""
        r = Rig(types=(1, 0)); r.tick()
        for pane, analog in ((0, True), (1, False)):
            for slot in (PANE_HINT[pane], PANE_LABEL[pane]):
                with self.subTest(pane=pane, slot=hex(slot)):
                    r.call(SYM["an_pane_text"], r.view + slot, STR_DOUBLE_TAP)
                    self.assertEqual(r.text(r.view + slot), "" if analog and slot == PANE_HINT[pane] else "Double-tap to load WAV")
        r.call(SYM["an_pane_text"], STRS + 0x400, S.STR_NO_WAV)
        self.assertEqual(r.e.text(STRS + 0x400 + TEXT), "No WAV", "not a pane of a synth view: stock's text")

    def test_stale_handles_in_a_reused_view(self):
        """B3: a view built again over another page's handles (the page build writes the row handles after the titles and
        hints): page_tick refreshes each row whose handle changed, whatever its Type, so neither a stale Analog title / hint
        on a Wavetable oscillator nor the reverse survives one tick (measured stale before B3: verifier e2)."""
        for types, stale in (((0, 1), (6, 5)), ((1, 0), (6, 5)), ((0, 0), (5, 6))):
            with self.subTest(types=types, stale=stale):
                r = Rig(types=types, page=False)
                r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view)
                for i, h in enumerate(stale):
                    r.w32(r.view + S.SYNTH_VIEW_HANDLES + 4 * i, h)
                r.notify(0xA8); r.tick()
                self.assertEqual(r.hints(), ["" if t else "Double-tap to load WAV" for t in types])
                self.assertEqual([t.split("  ")[0] for t in r.titles_()],
                                 [f"OSC{i + 1}: Analog" if t else f"OSC{i + 1}: " for i, t in enumerate(types)])


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Titles(unittest.TestCase):
    """T0 / T1 / W1: the pane titles, through stock's own row refresh (PANE_TITLE_SITE and the shim)."""
    def test_titles_per_type(self):
        cases = {(0, 0): ["OSC1: ", "OSC2: "], (1, 0): ["OSC1: Analog  DSP 0%", "OSC2: "],
                 (0, 1): ["OSC1: ", "OSC2: Analog  DSP 0%"], (1, 1): ["OSC1: Analog  DSP 0%", "OSC2: Analog"]}
        for types, want in cases.items():
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertEqual(r.titles_(), want, "T0 prefixes; T1: the first Analog oscillator carries the M7's load")

    def test_titles_with_a_wav(self):
        """A Wavetable oscillator's title is stock's (its WAV's name after 'OSC1: '); an Analog one with a WAV still loaded
        says so (W1: stock keeps it and plays it again as Wavetable); within SLOT_TITLE's 31 characters."""
        r = Rig(types=(1, 0), page=False)
        r.wavs = {r.h1: "Basic Shapes.wav", r.h2: "Vocal Formants.wav"}
        r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view); r.notify(0xA8); r.tick()
        self.assertEqual(r.titles_(), ["OSC1: Analog +WAV DSP 0%", "OSC2: Vocal Formants"])
        r.edit(r.h1, TYPE, 0); r.tick()
        self.assertEqual(r.titles_()[0], "OSC1: Basic Shapes", "stock's title again (it drops the extension)")
        r.edit(r.h2, TYPE, 1); r.tick()
        self.assertEqual(r.titles_(), ["OSC1: Basic Shapes", "OSC2: Analog +WAV DSP 0%"])
        r.edit(r.h1, TYPE, 1); r.tick()
        self.assertEqual(r.titles_(), ["OSC1: Analog +WAV DSP 0%", "OSC2: Analog (WAV kept)"], "no load figure: the long marker")

    def test_one_form_per_state(self):
        """Review rev_m4_1: the WAV marker does not change with the load (it did: ' (WAV kept)' -> ' (WAV)' -> nothing as the
        figure grew). Next to the load it is ' +WAV' and one space, within SLOT_TITLE's 31 characters (it compares and
        keeps 0x1F) in the longest case; without a WAV, two spaces."""
        for wav, forms in ((True, ("OSC1: Analog +WAV DSP 63%", "OSC1: Analog +WAV DSP 100%", "OSC1: Analog +WAV DSP 63% OVER",
                                   "OSC1: Analog +WAV DSP 100% OVER")),
                           (False, ("OSC1: Analog  DSP 63%", "OSC1: Analog  DSP 100%", "OSC1: Analog  DSP 63% OVER",
                                    "OSC1: Analog  DSP 100% OVER"))):
            r = Rig(types=(1, 0), page=False)
            if wav:
                r.wavs = {r.h1: "x.wav"}
            r.view = r.malloc(0x13000); r.call(S.SYNTH_VIEW_CTOR, r.view); r.notify(0xA8); r.tick()
            seen, total, over, t = 1000, 600000, 0, 2000
            Indicator.load(None, r, seen, total, over); Indicator.run_tick(None, r, t)
            for (pm, o), want in zip(((630, 0), (1000, 0), (630, 1), (1000, 1)), forms):
                with self.subTest(wav=wav, pm=pm, over=o):
                    seen += 100; total += pm * 100; over += o; t += 3000      # 3 s apart: an OVER from the step before is gone
                    Indicator.load(None, r, seen, total, over)
                    got = Indicator.run_tick(None, r, t)
                    self.assertEqual(got, want)
                    self.assertLessEqual(len(got), 31)

    def test_the_hook_alone_with_stale_row_handles(self):
        """Review rev_m4_2: at PANE_TITLE_SITE the view's row handles are 0 or the previous page's, so OSC2's title takes
        OSC1's Type from the track (its first oscillator), not from row 0's handle. Stock's 0xC3 refresh runs with no tick
        of ours (the 250 ms retitle cannot hide the hook's text)."""
        for types, stale, want in (((0, 1), "h2", "OSC2: Analog  DSP 0%"), ((1, 1), 0, "OSC2: Analog")):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                r.u.mem_write(r.view + PANE_TITLE[1] + TEXT, b"x\0")
                r.w32(r.view + S.SYNTH_VIEW_HANDLES, r.h2 if stale == "h2" else 0)   # row 0: an Analog one / none
                r.title_hits.clear()
                r.notify(0xC3, r.h2)
                self.assertIn(r.view + PANE_TITLE[1], r.title_hits, "through PANE_TITLE_SITE")
                self.assertEqual(r.titles_()[1], want)

    def test_a_type_edit_retitles_through_stocks_refresh(self):
        """A Type edit (0xD0, no rebuild): page_tick's 0xC3 refreshes the row, the hook composes the title (with the handle
        from stock's frame); back to Wavetable, stock's text."""
        r = Rig(); r.tick()
        r.title_hits.clear(); r.edit(r.h1, TYPE, 1); r.tick()
        self.assertEqual(r.titles_(), ["OSC1: Analog  DSP 0%", "OSC2: "])
        self.assertIn(r.view + PANE_TITLE[0], r.title_hits, "through PANE_TITLE_SITE")
        r.edit(r.h1, TYPE, 0); r.tick()
        self.assertEqual(r.titles_(), ["OSC1: ", "OSC2: "])

    def test_the_hook_takes_the_handle_from_stocks_frame(self):
        """an_pane_title(slot, text, handle): the handle decides, not the view's row handle (0 / stale at the site)."""
        r = Rig(types=(1, 0)); r.tick()
        r.u.mem_write(STRS + 0x300, b"stock\0")
        for h, want in ((r.h1, "OSC2: Analog"), (r.h2, "stock"), (0, "stock")):
            r.call(SYM["an_pane_title"], r.view + PANE_TITLE[1], STRS + 0x300, h)
            self.assertEqual(r.titles_()[1], want)
        r.call(SYM["an_pane_title"], STRS + 0x400, STRS + 0x300, r.h1)
        self.assertEqual(r.e.text(STRS + 0x400 + TEXT), "stock", "not a synth view's title")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Indicator(unittest.TestCase):
    """T1 (Jeff's 'visible CPU'): the first Analog oscillator's title shows the M7's total LOAD ('DSP', not 'CPU'), from the
    black box's counters (D0L: seen, permille sum, peak, bins <50 / 50-80 / 80-100 / 100+ %), updated by the tick."""
    def load(self, r, seen, pm_sum, over):
        r.u.mem_write(D0L, struct.pack("<7I", seen, pm_sum, 0, 0, 0, 0, over))

    def run_tick(self, r, t, pane=0):
        r.ms(t); r.ui_tick(); r.ms(t + 1)
        r.tick(dt=0)
        return r.text(r.view + PANE_TITLE[pane])

    def test_the_title_shows_the_load_and_marks_overruns(self):
        for types, pane in (((1, 0), 0), ((0, 1), 1), ((1, 1), 0)):
            with self.subTest(types=types):
                r = Rig(types=types)
                n = f"OSC{pane + 1}: Analog  "
                self.load(r, 1000, 600000, 0); self.run_tick(r, 2000, pane)
                self.load(r, 1100, 600000 + 63000, 0)
                self.assertEqual(self.run_tick(r, 2600, pane), n + "DSP 63%")
                self.load(r, 1200, 663000 + 90000, 2)
                self.assertEqual(self.run_tick(r, 3200, pane), n + "DSP 90% OVER", "a block over budget in the last 2 s")
                self.load(r, 1300, 753000 + 50000, 2)
                self.assertEqual(self.run_tick(r, 5300, pane), n + "DSP 50%", "OVER goes after 2 s")
                self.assertEqual(r.u.mem_read(r.view + PANE_TITLE[pane] + 0x5C, 1)[0], 1, "marked for a redraw")
                if types == (1, 1):
                    self.assertEqual(r.text(r.view + PANE_TITLE[1]), "OSC2: Analog", "one figure: the first Analog oscillator's")

    def test_the_title_updates_with_no_refresh(self):
        """The hook runs only on stock's refreshes (none while nothing changes, M); the tick re-titles with the same composer,
        so the figure moves while the title site is never hit, and an unchanged text is not written again."""
        r = Rig(types=(1, 0))
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        r.title_hits.clear(); seen = []
        for i, pm in enumerate((120, 340, 560, 340)):
            self.load(r, 1100 + 100 * i, 600000 + sum((120, 340, 560, 340)[:i + 1]) * 100, 0)
            seen.append(self.run_tick(r, 2600 + 600 * i))
        self.assertEqual(seen, [f"OSC1: Analog  DSP {p // 10}%" for p in (120, 340, 560, 340)])
        self.assertEqual(r.title_hits, [], "no refresh of stock's")

    def test_a_save_restarting_the_counters_is_not_a_spike(self):
        r = Rig(types=(1, 0), d0l=(1000, 600000, 0, 0, 0, 0, 5))   # the boot's first look only takes the counters
        self.run_tick(r, 2000)
        self.load(r, 10, 7000, 0)                               # LOADREQ: a save starts a new stretch
        self.run_tick(r, 2600)
        self.load(r, 110, 7000 + 40000, 0)
        self.assertEqual(self.run_tick(r, 3200), "OSC1: Analog  DSP 40%")

    def test_no_analog_oscillator_and_no_dsp(self):
        r = Rig(); r.titles.clear()
        self.run_tick(r, 2000)
        self.assertEqual({t for a, t in r.titles if a in (r.view + PANE_TITLE[0], r.view + PANE_TITLE[1])},
                         {"OSC1: ", "OSC2: "}, "both Wavetable: stock's titles (B3's refresh of the new rows)")
        r = Rig(types=(0, 1), area=False)
        self.assertEqual(self.run_tick(r, 2000, 1), "OSC2: Analog  DSP off", "the M7 has not taken the area")

    def test_the_figure_moves_with_the_first_analog_oscillator(self):
        """OSC1 back to Wavetable (no rebuild): its title is stock's again (0xC3) and the figure moves to OSC2."""
        r = Rig(types=(1, 1))
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        self.load(r, 1100, 663000, 0)
        self.assertEqual(self.run_tick(r, 2600), "OSC1: Analog  DSP 63%")
        r.edit(r.h1, TYPE, 0)
        self.run_tick(r, 3200)
        self.assertEqual(r.titles_(), ["OSC1: ", "OSC2: Analog  DSP 63%"])
        r.edit(r.h2, TYPE, 0)
        self.run_tick(r, 3800)
        self.assertEqual(r.titles_(), ["OSC1: ", "OSC2: "])

    def test_over_budget_blocks_with_analog_are_counted(self):
        r = Rig(types=(1, 0))
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        r.w32(SA + 0x40, 50)                                    # the M7: blocks with an Analog voice
        self.load(r, 1100, 700000, 3); self.run_tick(r, 2600)
        self.load(r, 1200, 800000, 4); self.run_tick(r, 3200)   # no Analog block in between
        self.assertIn(" over_with_analog=3 ", r.black_box())


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Visibility(unittest.TestCase):
    """What the player can see: no hidden byte up the parent chain (B2: BE4.40 P1 wrote the indicator into a hidden label)."""
    def test_titles_dial_and_load_are_visible(self):
        for types in ((1, 0), (0, 1), (1, 1)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                for i in range(2):
                    self.assertTrue(r.visible(r.view + PANE_TITLE[i]), f"title {i}")
                    self.assertTrue(r.visible(r.view + PANE[i]), f"pane {i} (the dial)")
                self.assertTrue(any("DSP" in t for t in r.titles_()), "the load is in a title")
                for i in range(2):
                    self.assertFalse(r.visible(r.view + PANE_LABEL[i]), "B2: the 'No WAV' label (its Granular parent is hidden)")
                    self.assertTrue(r.u.mem_read(r.view + GRANULAR[i] + HIDDEN, 1)[0])

    def test_the_paint_walk_skips_a_hidden_pane(self):
        """Stock's own walk (WIDGET_PAINT_KIDS) draws no dial for a hidden pane, and does for a shown one."""
        r = Rig(types=(1, 0)); r.tick()
        r.u.mem_write(render.CTX, bytes([1, 1, 0, 0]) + struct.pack("<I", render.SURF))
        r.our_paints.clear(); r.call(S.WIDGET_PAINT_KIDS, r.view, render.CTX)
        self.assertIn(r.view + PANE[0], r.our_paints)
        r.u.mem_write(r.view + PANE[0] + HIDDEN, b"\1")
        r.our_paints.clear(); r.call(S.WIDGET_PAINT_KIDS, r.view, render.CTX)
        self.assertNotIn(r.view + PANE[0], r.our_paints)


def angle_of(px, centre):
    """Screen angle (degrees, counter-clockwise from +x) of the centroid of the given pixels around the centre."""
    import math
    ys, xs = px
    return math.degrees(math.atan2(-(ys.mean() - centre[1]), xs.mean() - centre[0])) % 360


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Dial(unittest.TestCase):
    """D1 / D2: the Shape dial, drawn with stock's GFX_FILL into the frame buffer and read back as pixels. J3, P3 Q3: five
    glyphs at the anchors (docs/plan-p3-fm-bits.md 4.6, typed here: 0 sine, 24 triangle, 48 saw, 88 square, 127 the 8 %
    pulse; plateaus 0..2, 22..26, 45..48), a pointer over 270 degrees (225 at 0 to -45 at 127), glyphs lit only with 3/4
    of the blend."""
    # where the glyphs sit: at their anchor's angle, 70 px across and 57 px up from the dial's centre (m4_synth.c dial)
    AT = (0, 24, 48, 88, 127)

    @staticmethod
    def lit_glyphs(g, c):
        """The glyphs (0 sine .. 4 pulse) with lit pixels in a 30 x 24 box around their place."""
        import math
        got = set()
        for k, s in enumerate(Dial.AT):
            a = math.radians(225 - s * 270 / 127)
            x, y = round(c[0] + 70 * math.cos(a)), round(c[1] - 57 * math.sin(a))
            if (g[y - 12:y + 12, x - 15:x + 15] == LIT).any():
                got.add(k)
        return got
    def draw(self, shape, pane=0, types=(1, 0)):
        r = Rig(types=types); r.tick()
        r.set((r.w1, r.w2)[pane], SHAPE, shape)
        render.clear(r.e)
        n = r.paint(pane, full=1)
        return r, n

    def test_pointer_and_lit_glyphs(self):
        """P3 Q3: which glyph is lit, from the plan's weights (4.6, OD6) and the 3/4 rule: the sine's 1 - (s - 2) / 20 >= 3/4 up
        to 7, the triangle's from 17 to 30 (1 - (s - 26) / 19), the saw's from 41 ((s - 26) / 19) to 58 (BE4.40's saw >
        square: 48 + 40 / 4), the square 78..97, the pulse 118..127; nothing lit in the middle of a morph; nothing but the
        glyphs is lit (BE4.40's wide saw sector is gone)."""
        import numpy as np
        for shape, want in ((0, {0}), (2, {0}), (7, {0}), (8, set()), (12, set()), (16, set()), (17, {1}), (24, {1}),
                            (30, {1}), (31, set()), (36, set()), (40, set()), (41, {2}), (45, {2}), (48, {2}), (58, {2}),
                            (59, set()), (68, set()), (78, {3}), (88, {3}), (97, {3}), (108, set()), (118, {4}), (127, {4})):
            with self.subTest(shape=shape):
                r, n = self.draw(shape)
                self.assertGreater(n, 100, "drawn with stock's fills")
                self.assertEqual(r.stock_paints, [], "stock's wavetable paint is skipped on an Analog pane")
                g, c = r.glass(0)
                pw = (225 - shape * 270 / 127) % 360
                got = angle_of(np.nonzero(g == ORANGE), c)
                self.assertLess(min(abs(got - pw), 360 - abs(got - pw)), 8, f"pointer at {got:.0f}, want {pw:.0f}")
                self.assertEqual(self.lit_glyphs(g, c), want, "the lit glyphs")
                self.assertEqual(bool((g == LIT).any()), bool(want), "nothing else lit")

    def test_each_plateau_lights_as_one(self):
        """P3 Q3 (OD6): on a plateau (0..2 sine, 22..26 triangle, 45..48 saw) the lit pixels are the same (only the pointer
        moves), and each plateau's differ from the others'. Replaces BE4.40's "0..48 is one saw"."""
        import numpy as np
        lit = {s: self.draw(s)[0].glass(0)[0] == LIT for s in (0, 1, 2, 22, 24, 26, 45, 46, 48)}
        for a, b in ((0, 1), (0, 2), (22, 24), (26, 24), (45, 48), (46, 48)):
            self.assertTrue(np.array_equal(lit[a], lit[b]), f"{a} and {b}")
        for a, b in ((0, 24), (24, 48), (0, 48)):
            self.assertFalse(np.array_equal(lit[a], lit[b]), f"{a} and {b}")

    def test_out_of_range_shapes_clamp(self):
        import numpy as np
        for bad, good in ((1000, 127), (-5, 0), (0x7FFFFFFF, 127)):
            with self.subTest(shape=bad):
                self.assertTrue(np.array_equal(self.draw(bad)[0].glass(0)[0], self.draw(good)[0].glass(0)[0]))

    def test_stock_paints_wavetable_panes(self):
        for types, pane in (((1, 0), 1), ((7, 0), 0), ((0, 0), 0)):
            with self.subTest(types=types, pane=pane):
                r = Rig(types=types); r.tick()
                self.assertEqual(r.paint(pane, full=1), 0)
                self.assertEqual(r.stock_paints, [r.view + PANE[pane]])

    def test_the_title_is_not_painted_over(self):
        """The fill covers the pane's rect only; the title label below it (and the rest of the screen) is untouched."""
        r = Rig(types=(1, 0)); r.tick()
        render.clear(r.e, 0x33); r.paint(0, full=1)
        g = render.ui(r.e)
        x, y, w, h = struct.unpack("<4i", bytes(r.u.mem_read(r.view + PANE[0] + 4, 16)))
        top = 480 - (y + h)
        outside = g.copy(); outside[top:top + h, x:x + w] = 0x33
        self.assertTrue((outside == 0x33).all())

    def test_redraws(self):
        """D2: nothing drawn on a partial repaint when nothing changed; a Shape change marks the pane (the partial flag the
        view's tick ORs in) and the next paint draws, at most every 33 ms; a full repaint always draws."""
        r = Rig(types=(1, 0)); r.tick()
        r.paint(0, full=1)
        self.assertEqual(r.paint(0), 0, "unchanged")
        self.assertEqual(r.asked(), (0, 0), "nothing to redraw")
        r.set(r.w1, SHAPE, 90)
        self.assertEqual(r.asked(), (0, 1), "a partial repaint asked")
        self.assertGreater(r.paint(0), 100)
        r.set(r.w1, SHAPE, 100); r.ms(r.now() + 10)
        self.assertEqual(r.paint(0), 0, "within 33 ms of the last draw")
        r.ms(r.now() + 30)
        self.assertGreater(r.paint(0), 100)
        self.assertGreater(r.paint(0, full=1), 100, "a full repaint")
        self.assertEqual(r.paint(0), 0)
        self.assertIn(" dials=", r.black_box())

    def test_press_and_frame(self):
        """Stock's pane states are kept: pressed fills the pane 0xF6 (no dial), framed adds the outline."""
        r = Rig(types=(1, 0)); r.tick(); r.paint(0, full=1)
        p = r.view + PANE[0]
        press = r.r32(r.r32(p) + 0x30)                          # the pane class's slot 12 (stock's press, 0x0818477D)
        r.u.mem_write(EV, struct.pack("<HHIII", 0x79, 0, 0, 0, 0)); r.call(press, p, EV)
        r.ms(r.now() + 40); r.paint(0)
        g, c = r.glass(0)
        self.assertTrue((g == ORANGE).all(), "pressed: stock's highlight")
        r.u.mem_write(EV, struct.pack("<HHIII", 0x7A, 0, 0, 0, 0)); r.call(press, p, EV)
        r.ms(r.now() + 40); r.paint(0)
        g, c = r.glass(0)
        self.assertLess((g == ORANGE).sum(), g.size // 20, "released: the dial again")

    def test_leaving_analog_asks_for_a_full_repaint(self):
        """Our fill must not stay under stock's pane (stock draws no background): a pane that leaves Analog gets the full
        repaint flag once (with the partial one: stock's frames draw only with ctx+1); a hidden pane is drawn afresh when it
        shows again."""
        r = Rig(types=(1, 0)); r.tick(); r.paint(0, full=1)
        r.edit(r.h1, TYPE, 0)
        self.assertEqual(r.asked(), (1, 1), "a full repaint")
        self.assertEqual(r.asked(), (0, 0), "once")
        r.edit(r.h1, TYPE, 1)
        self.assertEqual(r.asked(), (0, 1), "back to Analog: drawn")
        r.paint(0); self.assertEqual(r.asked(), (0, 0))
        r.u.mem_write(r.view + PANE[0] + HIDDEN, b"\1"); r.tick(dt=50)
        r.u.mem_write(r.view + PANE[0] + HIDDEN, b"\0")
        self.assertEqual(r.asked(), (0, 1), "shown again: drawn afresh")

    def wav_frames(self, r, pane):
        """Pane `pane` with a WAV's frames (harness stub: the pane paint's frame fetch, its bl at WT_PANE_PAINT + 0x4C,
        answers 1; the samples at pane+0x1FC a zero buffer): returns a counter of stock's frame line draws (the bl at
        WT_PANE_PAINT + 0xC0) and the panes' frame dirty bytes (+0x38 + 0x1C k + 0x19). Measured on stock 1.6.8's paint
        (review rev_m4_2 probe4.py)."""
        FRAME_FETCH, FRAME_LINE = bl_target(S.WT_PANE_PAINT + 0x4C), bl_target(S.WT_PANE_PAINT + 0xC0)
        self.assertTrue(FRAME_FETCH and FRAME_LINE, "the pane paint's frame fetch and line draw")
        buf = r.malloc(0x1000)
        r.w32(r.view + PANE[pane] + 0x1FC, buf)
        def fetch(uc, a, sz, _): uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        r.u.hook_add(UC_HOOK_CODE, fetch, begin=FRAME_FETCH, end=FRAME_FETCH)
        lines = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: lines.append(1), begin=FRAME_LINE, end=FRAME_LINE)
        r.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
        dirty = lambda p: [r.u.mem_read(r.view + PANE[p] + 0x38 + 0x1C * k + 0x19, 1)[0] for k in range(16)]
        return lines, dirty

    def test_a_full_repaint_redraws_stocks_frames(self):
        """Review rev_m4_2: stock draws a frame only while its dirty byte is set and clears it once drawn, so the full
        repaint (the root clears the screen) left the other pane's wavetable blank. Both panes' frames are marked with it:
        OSC2's WAV is drawn again on that repaint."""
        r = Rig(types=(1, 0)); r.tick()
        lines, dirty = self.wav_frames(r, 1)
        for _ in range(20):
            r.paint(1)
            if not any(dirty(1)):
                break
        self.assertFalse(any(dirty(1)), "stock drew OSC2's frames (and cleared their bytes)")
        r.paint(0, full=1); r.edit(r.h1, TYPE, 0)
        self.assertEqual(r.asked(), (1, 1))
        self.assertEqual((dirty(0), dirty(1)), ([1] * 16, [1] * 16), "both panes' frames marked")
        lines.clear(); r.paint(1, full=1, partial=1)
        self.assertGreater(len(lines), 100, "OSC2's wavetable drawn again after the clear")

    def test_a_page_build_clears_a_dial(self):
        """Review rev_m4_1: a track change (0xA8, the page built again) from an Analog OSC1 to a Wavetable one: the build
        asks for no full repaint itself, and stock's no-WAV pane draws no background, so a dial this view showed gets the
        full repaint (and the frames marked); a view that showed no dial is left alone."""
        r = Rig(types=(1, 0)); r.tick(); r.paint(0, full=1)
        lines, dirty = self.wav_frames(r, 1)
        for _ in range(20):
            r.paint(1)
        r.notify(0xA8)
        self.assertEqual(bytes(r.u.mem_read(r.view + PANE[0] + 0x31, 2)), b"\1\1", "full and partial asked")
        self.assertEqual(dirty(1), [1] * 16)
        r = Rig(types=(0, 0)); r.tick(); r.paint(0, full=1); r.asked()
        r.notify(0xA8)
        self.assertEqual(r.u.mem_read(r.view + PANE[0] + 0x31, 1)[0], 0, "no dial: no full repaint (the build's own partial)")

    def test_the_glyphs_read_as_the_sound(self):
        """Review rev_m4_1: the saw rises (the kernel's 2p - 1, analog_kernel.cpp) and falls back at each cycle's end, two
        cycles per glyph; the pulse shows both edges and a high run of at least 3 px. P3 Q3b (OD14: the Q3 pair, one cycle
        each from the bottom, both read as a caret on the snapshot): the sine and the triangle are told apart by their form,
        not by a pixel or two of roundness. The sine is one smooth cycle as it is drawn everywhere (sin, from the middle: up
        to a crest, down through the middle to a trough, back), its crest and trough each held for at least 4 columns (round);
        the triangle two cycles in the kernel's phase, a zigzag of straight flanks (each column moves the same 3 +- 1 px) to
        two sharp points (at most 3 columns at the top: the 2-px fill) over three bottom points. Neither has an edge. Each
        criterion fails on the Q3 pair (both started at the bottom with one peak in the middle). The sine and the triangle are
        the pulse's height (M 19 px; Q3b review: "more than 14 px" passed a sine 4 px short; the saw's ramp, BE4.40's, stops
        a step short of the top: 17). Glass Y is down; each anchor lights only its own glyph (test_pointer_and_lit_glyphs),
        so all lit pixels are it."""
        import numpy as np
        def runs(idx):                                        # groups of neighbouring columns, as (first, last)
            out = []
            for i in idx:
                if out and i - out[-1][1] <= 1: out[-1][1] = i
                else: out.append([i, i])
            return out
        prof = {}
        for s in (0, 24):
            r, _ = self.draw(s)
            ys, xs = np.nonzero(r.glass(0)[0] == LIT)
            cols = sorted(set(xs))
            top = np.array([ys[xs == x].min() for x in cols]); bot = np.array([ys[xs == x].max() for x in cols])
            self.assertLess(int(max(bot - top)), 16, f"Shape {s}: no edge (an edge spans the glyph's 18 px, below)")
            self.assertGreater(int(max(bot) - min(top)), 14, f"Shape {s}: full height")
            prof[s] = (top, bot, runs([i for i in range(len(cols)) if top[i] == min(top)]),
                       runs([i for i in range(len(cols)) if bot[i] == max(bot)]))
        top, bot, crest, trough = prof[0]                     # the sine
        n, mid = len(top), (int(max(bot)) + int(min(top))) / 2
        with self.subTest(glyph="sine"):
            for i in (0, n - 1):
                self.assertLessEqual(abs((top[i] + bot[i]) / 2 - mid), 2, f"column {i}: the cycle starts and ends mid-height")
            self.assertEqual((len(crest), len(trough)), (1, 1), "one crest, one trough: one cycle")
            self.assertLess(crest[0][1], n / 2, "the crest in the first half")
            self.assertGreater(trough[0][0], n / 2, "the trough in the second half")
            self.assertGreaterEqual(min(crest[0][1] - crest[0][0], trough[0][1] - trough[0][0]) + 1, 4,
                                    f"round: crest {crest}, trough {trough}")
        top, bot, peaks, lows = prof[24]                      # the triangle
        with self.subTest(glyph="triangle"):
            self.assertEqual(len(peaks), 2, f"two cycles: two points ({peaks})")
            self.assertLessEqual(max(b - a + 1 for a, b in peaks), 3, f"sharp points ({peaks})")
            self.assertEqual(len(lows), 3, f"three bottom points ({lows})")
            self.assertEqual(lows[0][0], 0, "from the bottom: the kernel's phase")
            for (_, a), (b, _) in zip(lows, peaks):           # the rising flanks: bottom point -> top point
                d = np.diff(top[a:b + 1])
                self.assertTrue(all(-4 <= v <= -2 for v in d), f"a straight flank: {list(d)}")
            for (_, a), (b, _) in zip(peaks, lows[1:]):       # the falling ones
                d = np.diff(top[a:b + 1])
                self.assertTrue(all(2 <= v <= 4 for v in d), f"a straight flank: {list(d)}")
        self.assertNotEqual(len(prof[0][2]), len(prof[24][2]), "the two glyphs differ in form: one crest against two points")
        r, _ = self.draw(48)
        ys, xs = np.nonzero(r.glass(0)[0] == LIT)          # the saw glyph
        top = {x: ys[xs == x].min() for x in set(xs)}
        cols = sorted(top)
        up = sum(top[b] < top[a] for a, b in zip(cols, cols[1:]))
        down = sum(top[b] > top[a] for a, b in zip(cols, cols[1:]))
        self.assertGreater(up, 3 * down, f"a rising ramp ({up} columns up, {down} down)")
        span = {x: ys[xs == x].max() - ys[xs == x].min() for x in cols}
        self.assertGreaterEqual(sum(v >= 16 for v in span.values()), 2, "the falling edges")
        r, _ = self.draw(127)
        g, (cx, cy) = r.glass(0)
        ys, xs = np.nonzero(g == LIT)
        cols = sorted(set(xs))
        span = {x: ys[xs == x].max() - ys[xs == x].min() for x in cols}
        self.assertGreaterEqual(sum(v >= 16 for v in span.values()), 4, "both edges of two pulses")
        high = sorted(set(xs[ys == ys.min()]))
        self.assertGreaterEqual(len(high), 3, "a high run of 3 px or more")
        tall = {}                                             # Q3b review: the pulse's height (was > 14 px only)
        for s in (0, 24, 127):
            ys, _ = np.nonzero(self.draw(s)[0].glass(0)[0] == LIT)
            tall[s] = int(ys.max() - ys.min())
        self.assertEqual(len(set(tall.values())), 1, f"the glyphs' full heights (Shape: px) {tall}")

    def test_cost(self):
        """One dial draw on the emulated M4: instructions and fills (recorded in the plan doc; the device's DMA2D time is not
        modelled)."""
        from unicorn import UC_HOOK_CODE
        r = Rig(types=(1, 0)); r.tick()
        n = [0]
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: n.__setitem__(0, n[0] + 1))
        fills = r.paint(0, full=1)
        print(f"\n  dial draw: {n[0]} instructions, {fills} fills", file=sys.stderr)
        self.assertLess(n[0], 400_000)


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class ValueTexts(unittest.TestCase):
    """V1: an Analog oscillator's Shape value reads what the kernel plays (typed here from dsp/analog.cpp an_shape and
    analog_kernel.cpp: blend 2 shape to 48..88, pulse width 50 % - 42 % (s - 88) / 39 above; P3 Q3, plan 4.6 / OD6: the
    triangle's share (s - 2) / 20 between the sine and the triangle plateaus, the saw's (s - 26) / 19 after it, rounded)."""
    def test_shape_texts(self):
        r = Rig(types=(1, 0)); r.tick()
        for shape, want in ((0, "Sine"), (1, "Sine"), (2, "Sine"), (3, "Sin>Tri 5%"), (12, "Sin>Tri 50%"), (21, "Sin>Tri 95%"),
                            (22, "Tri"), (24, "Tri"), (26, "Tri"), (27, "Tri>Saw 5%"), (36, "Tri>Saw 53%"),
                            (44, "Tri>Saw 95%"), (45, "Saw"), (46, "Saw"),
                            (48, "Saw"), (49, "Saw>Sq 3%"), (64, "Saw>Sq 40%"), (87, "Saw>Sq 98%"), (88, "Square"),
                            (89, "Pulse 49%"), (100, "Pulse 37%"), (127, "Pulse 8%")):
            with self.subTest(shape=shape):
                r.set(r.w1, SHAPE, shape); r.tick()
                self.assertEqual(r.wvalue(2), want)

    def test_a_turn_shows_the_text_in_the_same_tick(self):
        """A turn is applied by stock's widget in stock's tick: our text replaces its number before the paint."""
        r = Rig(types=(1, 0)); r.tick()
        [(h, k, v)] = r.turn(2, 0x3200 * 16)
        self.assertEqual((k, v), (SHAPE, 64))
        r.edit(h, k, v); r.tick()
        self.assertEqual(r.wvalue(2), "Saw>Sq 40%")

    def test_wavetable_rows_are_stocks(self):
        r = Rig(); r.tick()
        self.assertEqual((r.wtitle(2), r.wvalue(2)), ("Position", "0"))

    def test_a_row_switch_shows_our_text_at_once(self):
        """Reviews rev_m4_1 / rev_m4_2: stock's row bind (SYNTH_ROW_SHOW) and its 0xC3 refresh write the widget's own number
        or label; through VALUE_TEXT_1..3 ours is written instead, with no tick in between (it read '64' / 'Analog')."""
        r = Rig(types=(1, 0)); r.set(r.w1, SHAPE, 64); r.tick()
        r.show_row(1); r.show_row(0)
        self.assertEqual(r.wvalue(2), "Saw>Sq 40%")
        r.notify(0xC3, r.h1)
        self.assertEqual(r.wvalue(2), "Saw>Sq 40%")
        r = Rig(types=(7, 0)); r.tick()
        r.show_row(1); r.show_row(0)
        self.assertEqual(r.wvalue(3), "Wavetable")
        r.show_row(2)
        self.assertNotIn(r.wvalue(3), ("Wavetable", "Saw>Sq 40%"), "row 2's widgets are stock's")

    def test_an_out_of_range_type_turns_from_what_it_shows(self):
        """Review rev_m4_1 (Y1): stock's two-label widget kept Type 7 / -1 at its last entry ('Analog'): right did nothing
        and left wrote 0. It is put at entry 0, what it reads ('Wavetable'): right writes 1, left writes nothing (the model
        keeps the value). Also right after a row switch (the bind put it back at entry 1)."""
        detent = 12800                                     # one entry of the two-label widget's step position (M)
        for t in (7, -1):
            with self.subTest(type=t):
                r = Rig(types=(t, 0)); r.tick(); r.tick()
                self.assertEqual(r.turn(3, detent), [(r.h1, TYPE, 1)], "right: Analog")
                r = Rig(types=(t, 0)); r.tick(); r.tick()
                self.assertEqual(r.turn(3, -detent), [], "left: already Wavetable")
                self.assertEqual(r.prop(r.w1, TYPE), t)
                r.show_row(1); r.show_row(0)
                self.assertEqual(r.turn(3, detent), [(r.h1, TYPE, 1)], "after a row switch")
        r = Rig(types=(1, 0)); r.tick()
        self.assertEqual(r.turn(3, -detent), [(r.h1, TYPE, 0)], "an in-range Type: stock's widget")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class ResetRow(unittest.TestCase):
    """H1: WTOscReset (0xC6, row 2 'Osc + Config') is hidden while both oscillators are Analog; the row heals as fix_rows."""
    def row2(self, r): return [r.h16(r.view + S.SYNTH_VIEW_KEYS + 32 + 2 * i) for i in range(8)]

    def test_hidden_only_with_both_analog(self):
        stock = Rig(types=(0, 0)); stock.tick(); base = self.row2(stock)
        self.assertIn(0xC6, base, "stock's row 2 has WTOscReset")
        for types in ((1, 0), (0, 1)):
            with self.subTest(types=types):
                r = Rig(types=types); r.tick()
                self.assertEqual(self.row2(r), base)
        r = Rig(types=(1, 1)); r.tick()
        self.assertEqual(self.row2(r), [0 if k == 0xC6 else k for k in base])
        r.show_row(2)
        self.assertNotIn(0xC6, [k for k, _ in r.widgets()], "stock's bind: an empty slot")
        r.edit(r.h2, TYPE, 0); r.tick()
        self.assertEqual(self.row2(r), base, "one Wavetable again: back")
        self.assertIn(0xC6, [k for k, _ in r.widgets()], "and bound (row 2 is the shown row)")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Browse(unittest.TestCase):
    """B1': event 0x7B (ev+8 = the oscillator) on a Wavetable track: stock posts 0xA8 {osc} then 0x3F (its page 0x14); on an
    Analog oscillator the 0x3F is filtered (the selection stays), on a Wavetable one both go as stock's."""
    def test_double_tap_outcome(self):
        r = Rig(types=(1, 0)); r.tick()
        for idx, h, analog in ((0, r.h1, True), (1, r.h2, False)):
            with self.subTest(osc=idx):
                r.posts.clear()
                r.u.mem_write(EV, struct.pack("<HHIII", 0x7B, 0, 0, idx, 0))
                r.call(S.SYNTH_VIEW_EVENT, r.view, EV)
                codes = [(c, a) for c, _, a, _, _ in r.posts]
                self.assertEqual(codes[0], (0xA8, h), "selected either way")
                self.assertEqual(any(c == 0x3F for c, _ in codes), not analog)
        self.assertIn(" browse_blocked=1", r.black_box())

    def test_the_press_is_stocks(self):
        """The pane's own press (0x79: highlight, 0x7C {osc}) is not touched."""
        r = Rig(types=(1, 0)); r.tick(); r.posts.clear()
        r.u.mem_write(EV, struct.pack("<HHIII", 0x79, 0, 0, 0, 0))
        r.call(r.r32(r.r32(r.view + PANE[0]) + 0x30), r.view + PANE[0], EV)
        self.assertEqual({(c, a) for c, _, a, _, _ in r.posts}, {(0x7C, r.h1)}, "(UI_POST logs each level it climbs)")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class DestNames(unittest.TestCase):
    """T0 (J2): stock's DEST_NAME names a Wavetable oscillator's mod destinations 'OSC1: ..' / 'OSC2: ..' (its own format,
    run here on the built image)."""
    def test_mod_destination_names(self):
        r = Rig(page=False)
        view = r.malloc(0x120000)
        r.w32(view + 0x11CD08, r.r32(r.track + 0x10))     # the view's track (DEST_NAME's own-track case, 0x08159A84..90)
        got = []
        for h in (r.h1, r.h2):
            for key in (LEVEL, PITCH, POSITION):
                r.w32(STRS + 0x10, h); r.u.mem_write(STRS + 0x40, bytes(64))
                r.call(S.DEST_NAME, view, STRS + 0x10, key, STRS + 0x40, 0x40, 0)
                got.append(r.e.text(STRS + 0x40))
        self.assertEqual(got, ["OSC1: Level", "OSC1: Pitch", "OSC1: Position", "OSC2: Level", "OSC2: Pitch", "OSC2: Position"])


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class BlackBox(unittest.TestCase):
    def test_black_box_line(self):
        r = Rig(types=(1, 1)); r.tick()
        r.u.mem_write(SA + 0x40, struct.pack("<16I", 10, 1000, 30000, 2, 7, 128, 0, 9, 1, 0, 0, 3, 20, 4, 0, 0))
        lines = r.black_box().splitlines(keepends=True)          # with drums: the ANALOG and DRUMS lines
        line = next(l for l in lines if l.startswith("ANALOG"))
        for part in ("ANALOG gen=1", "keys=ok", "oscs=2", "| blocks=10", "avg_cyc=1600", "peak_cyc=30000", "osc_peak=2",
                     "share<5%=9 5-15=1 15-30=0 30+=0", "voices_peak=3", "renders=20", "n_max=128", "props=7", "roots=4",
                     "over_with_analog=0", "| ui page_rows=1 heals=1 resends=0 dials=0 browse_blocked=0"):
            self.assertIn(part, line)
        self.assertTrue(line.endswith("\r\n"))
        self.assertIn("shape_mods=0 fm=0 fmx=0/0 wt=0/0 |", line, "P3: the FM stats line, nothing counted (BE4.42: no bits=; "
                      "BE4.44 W3: wt=)")

    def test_the_fm_stats_fields(self):
        """P3 3.3: fm= and fmx= (skipped / missed) from the M7's FM stats line at +0x100 (synth_shared.h SaFmStats); BE4.42:
        word +4 (BE4.41's bits=) is not printed. BE4.44 W3: wt= (+16, Wavetable reader blocks) / (+20, of those the mip
        changes); +24 and +28 are spare, not printed."""
        r = Rig(types=(1, 1)); r.tick()
        r.u.mem_write(SA + 0x100, struct.pack("<8I", 41, 17, 3, 2, 55, 6, 0xEE, 0xEE))
        line = next(l for l in r.black_box().splitlines() if l.startswith("ANALOG"))
        self.assertIn(" fm=41 fmx=3/2 wt=55/6 |", line)
        self.assertNotIn("bits=", line, "BE4.42: no bits= field")
        self.assertNotIn("238", line, "the spare words are not printed")

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
