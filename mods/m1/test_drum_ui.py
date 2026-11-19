# SPDX-License-Identifier: GPL-3.0-or-later
"""The drum page (S6c-2, BE4.31): a Kick pad's knobs on stock's instrument page, their table, XML and handles.

    python test_drum_ui.py <stock bento1.bin> <m1 build dir>

What runs: the built image from the M4 reset's .data copy and static constructors on (emu.py); stock's registry builder;
stock's object store, track and child constructors (STORE_CREATE, CHILD_CREATE), its string property setter and reader
(a file node's Filename, as the project loader sets it), the object type lookup; stock's instrument page constructor
(INST_CTOR), its notifications (INST_NOTIFY, the class's slot 13: 0xA8 runs stock's cell set, which takes the track the
model has selected (the store's first here), sets the cell and its track type, copies the page table (PAGE_TABLE: the
One-Shot rows) and its keys into the rows; 0xA9 the file select, which ends in D5; 0xC3 D5; 0xC5 the title only), its
refresh (D5, INST_REFRESH, through our entry patch), its event handler (D6, through slot 12) and tick (slot 0),
CONTEXT_GET (through X3), SLOT_TITLE, stock's instrument XML writer and reader (X1 / X2) on a pad as the project writer
/ loader call them per child, and stock's copies (STORE_COPY_ALL, STORE_CLONE_H, CHILD_DUP) and removal. Nothing of
stock's is stubbed or typed: UI_POST and ENGINE_PROPERTY (set_property) are only logged. A page is selected as stock's
page select 0x081547EA does it: the cell set, then the file select (then it posts 0xA8 again: Notify).

Typed independently of the code under test: DK4 (our slice + 0x2000: a 32-word header, then 64 x {handle, word
(engine | 0x100 used), v0 = knobs 0..3, v1 = knobs 4..7}; header 2 serial, 6 keys, 7 carried, 8 dups, 9 dropped,
10 forgot, 11 clone_skips, 12 pages, 13 edits, 14 saves, 15 loads, 16 full, 17 evicted, 21 drum_pm, 25 heals); the knob
lines (synth area + 0x1000, 32 B: head, handle, word, v0, v1, -, -, tail); the M4 header's drum_serial (synth area +
0x20 + 16); the M7's drum stats (synth area + 0x80: blocks, cycles >> 4 summed) and the black box's LOAD counters (D0L:
seen, permille sum, .., over-budget bin); the Kick's knob names, defaults and ranges (upstream K7's Kick and DaisySP's
SyntheticBassDrum, docs/plan-synths-168.md; "Kick Dec" for its decay, review rc29 S4); the keys 0x1AE..0x1B4 (the
reviewed allow-list); the attribute names (bento_drum_engine, bento_drum_k1..k7); the notification codes (stock's
slot 13 tbb, docs/plan-synths-168.md "S6c-2").
Each hook site is also put back to stock's bytes in the emulator: the test that covers it must then fail.
Skipped on a build without the drums (1.6.1); a BE_DISABLE=drums bisect runs only the class that checks its sites.
"""
import json, re, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
sys.path.insert(0, str(HERE))
import emu
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR
from bases import from_build
STOCK = Path(sys.argv[1]).read_bytes()
S = from_build(sys.argv[2], STOCK)
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
OUT_DIR = Path(sys.argv[2])
del sys.argv[1:3]
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}
BUILT = "drums" not in MAN.get("omitted", []) and "dr_refresh" in SYM
DISABLED = BUILT and "drums" in MAN.get("disabled", [])
HAVE = BUILT and not DISABLED

BASE = 0x08040000
HEAP = 0xC4100000                  # harness heap (registry, stores, the page; above the display emulator's frame buffer)  # addr-ok: harness
STRS = 0xC0F00000                  # harness strings and out cells  # addr-ok: harness
DOC = 0xC0E00000                   # the XML document  # addr-ok: harness
EV = 0xC0F01000                    # an event record  # addr-ok: harness
DK4 = S.D2_SLICE + 0x2000 if BUILT else 0
DK4_MAGIC = 0x31344B44
CFG = S.D2_SLICE + 0x1E0 if BUILT else 0
SA = S.REGION - 0x8000 if BUILT else 0
KEYS = list(range(0x1AE, 0x1B5))
NAMES = ["Tune", "Kick Dec", "Tone", "Accent", "Sweep", "Sweep Dec", "Dirt"]
DEFAULTS = [55, 65, 45, 65, 55, 25, 25]
LO = [25, 0, 0, 0, 0, 0, 0]
HI = [180, 100, 100, 100, 100, 100, 100]
# P4-1 (docs/plan-p4-drums.md 3.1, N2): the native keys' descriptors. The knobs are 0..127 (the lean engines' range,
# docs/p4-engine-spec.md), not DK4's LO / HI above (which D6 and X2 still clamp to until P4-3); the engine 0x1A8 is an index
# 0..5, the One-Shot track's flag 0x1A2 is 0 / 1
KNOB_LO, KNOB_HI = 0, 127
ENGINE_KEY, TRACK_KEY = 0x1A8, 0x1A2
ATTRS = ["bento_drum_k%d" % (i + 1) for i in range(7)]
POLY_MODE = 0x2E
D0L = S.D2_SLICE + 0xFE0 if BUILT else 0
TAKE = S.D2_SLICE + 0x1D8 if BUILT else 0   # SYNTH_TAKE (synth_shared.h): BE4.47, the M4 trusts the header only with it
TAKE_FULL = 0xA5 << 24 | 1                  # SYNTH_TAKE_TAG, SYNTH_TAKE_FULL: the M7 took Region + synth area this boot
DSTATS = SA + 0x80 if BUILT else 0
# slot 13's notifications (stock's tbb at 0x081550C2, rc29/s6c2 fix/tbb.py): the cell set alone, the file select (then D5),
# D5, the file changed (title only)
EV_CELL, EV_FILE, EV_REFRESH, EV_FILE_CHANGED = 0xA8, 0xA9, 0xC3, 0xC5
KICK ="\\Drum Synths BE\\BE Kick.wav"
# stock 1.6.8's One-Shot pad page (read from its flash table with the registry's titles, rc29/s6c2 re/rows.py):
# Main: Level, Pitch, Pan, Overdrive, Delay Send, Reverb Send, Filter, Resonance; Config: Reverse, LaunchMode, Interp,
# Poly Mode, Choke Grp, Loop Mode, Loop Fade, -; then Attack, Decay, Sustain, Release, Velocity, LFO Shape / Depth / Rate
ONESHOT_ROWS = [[0x02, 0x03, 0x04, 0xE5, 0x36, 0x37, 0x32, 0x33], [0x1C, 0x05, 0x3B, POLY_MODE, 0x31, 0x08, 0x49, 0x00],
                [0x1E, 0x1F, 0x20, 0x21, 0x5F, 0x4A, 0x4C, 0x4B]]


def word(img, a): return struct.unpack_from("<I", img, a - BASE)[0]


class Rig:
    def __init__(self, prior=None, take=True):
        e = self.e = emu.Emu(IMG, init_ctors=False, base=S)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness
        if prior is not None:                                     # DK4 entries an earlier boot left in the slice
            self.u.mem_write(DK4, struct.pack("<I", DK4_MAGIC) + bytes(0x47C))
            for i, (h, eng, vals) in enumerate(prior):
                self.put(h, eng, vals, i)
        e.ctors()
        e.w(CFG, 0x31474144); e.w(CFG + 12, 1)                    # CFG: boot generation 1
        if take:                                                  # the M7 took the area, for M4 boot 1 (and, BE4.47,
            self.u.mem_write(SA, struct.pack("<5I", 0x37534542, 1, 2, 0xC000, 1))   # its take word: m4_synth.c ready)
            e.w(TAKE, TAKE_FULL)
        heap = [HEAP]
        def malloc(uc, a, sz, _):
            n = uc.reg_read(UC_ARM_REG_R0); p = heap[0]; heap[0] += (n + 31) & ~31
            uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
        self.registry = self.malloc(0x400)
        self.call(S.REG_BUILD, self.registry, count=40_000_000)
        self.call(SYM["an_tick"])                                 # the keys, the tables
        self.stores = []
        self.live = self.store()
        e.w(S.MODEL, self.live)
        self.u.mem_write(STRS, b"instrument\0params\0")
        self.posts, self.props = [], []
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.posts.append(self.event(uc.reg_read(UC_ARM_REG_R1))),
                        begin=S.UI_POST, end=S.UI_POST)
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.props.append(
            tuple(uc.reg_read(r) for r in (UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))), begin=S.ENGINE_PROPERTY, end=S.ENGINE_PROPERTY)
        # (unicorn does not apply a hook to code it translated before the hook was added: the cache is dropped after these)
        self.titled, self.stock_ticks, self.stock_ctx = [], [], []
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.titled.append((uc.reg_read(UC_ARM_REG_R0), self.e.text(uc.reg_read(UC_ARM_REG_R1), 48))),
                        begin=S.SLOT_TITLE, end=S.SLOT_TITLE)
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.stock_ticks.append(1), begin=S.INST_TICK, end=S.INST_TICK)
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.stock_ctx.append(uc.reg_read(UC_ARM_REG_R2)), begin=S.CONTEXT_GET + 4, end=S.CONTEXT_GET + 4)
        self.u.ctl_remove_cache(0x08000000, 0x08400000)            # the static constructors ran SLOT_TITLE before these hooks  # addr-ok: harness

    def malloc(self, n): return self.call(S.M4_MALLOC, n)
    def call(self, fn, *args, count=20_000_000): return self.e.call(fn, *args, count=count)
    def r32(self, a): return self.e.r(a)
    def w32(self, a, v): self.e.w(a, v)
    def h16(self, a): return struct.unpack("<H", self.u.mem_read(a, 2))[0]
    def event(self, p):
        return struct.unpack("<HHIII", self.u.mem_read(p, 16)) if p else None
    def stock_site(self, name):
        a = getattr(S, name)
        self.u.mem_write(a, STOCK[a - BASE:a - BASE + 4])

    # ---- stock's store, tracks and their children
    def store(self):
        s = self.malloc(0x6C)
        self.call(S.STORE_INIT, s); self.call(S.STORE_POOL_INIT, s, 0x40000)
        self.stores.append(s)
        return s
    def create(self, store, kind):
        self.w32(STRS + 0x40, 0)
        assert self.call(S.STORE_CREATE, store, kind, STRS + 0x40), "stock made the object"
        return self.r32(STRS + 0x40)
    def child(self, store, kind, parent):
        self.w32(STRS + 0x40, 0)
        assert self.call(S.CHILD_CREATE, store, kind, parent, STRS + 0x40), "stock made the child"
        return self.r32(STRS + 0x40)
    def filename(self, f, text):
        self.u.mem_write(STRS + 0x500, text.encode() + b"\0")
        self.call(S.SET_STRING, f, S.KEY_FILENAME, STRS + 0x500)
    def pad(self, store=None, name=KICK, kind=None):
        """A track of stock's (One-Shot unless kind) with a pad (type 1) and its file node (type 5): (track, pad, file)."""
        store = store or self.live
        t = self.create(store, S.TYPE_ONESHOT if kind is None else kind)
        p = self.child(store, 1, t); f = self.child(store, 5, t)
        self.filename(f, name)
        return t, p, f
    def multi(self, names, store=None):
        """A One-Shot track with a pad and its file node per name (children 2 i, 2 i + 1): (track, [(pad, file)])."""
        store = store or self.live
        t = self.create(store, S.TYPE_ONESHOT)
        out = []
        for name in names:
            p = self.child(store, 1, t); f = self.child(store, 5, t)
            self.filename(f, name)
            out.append((p, f))
        return t, out
    def handle(self, o): return self.r32(o + 0x10)
    def children(self, o): return [self.r32(o + S.NODE_CHILDREN + 4 * i) for i in range(self.r32(o + S.NODE_CHILD_N))]
    def objects(self, store): return [self.r32(store + 4 * i) for i in range(self.r32(store + S.STORE_COUNT))]

    # ---- DK4 and the published lines (typed)
    def put(self, h, eng, vals, i):
        v = list(vals) + [0] * (8 - len(vals))
        self.u.mem_write(DK4 + 0x80 + 16 * i, struct.pack("<4I", h, 0x100 | eng, struct.unpack("<I", bytes(v[:4]))[0],
                                                          struct.unpack("<I", bytes(v[4:8]))[0]))
    def entries(self):
        out = []
        for i in range(64):
            h, w, v0, v1 = struct.unpack("<4I", self.u.mem_read(DK4 + 0x80 + 16 * i, 16))
            if h and w & 0x100:
                out.append((h, w & 15, list(struct.pack("<II", v0, v1))[:7]))
        return out
    def knobs(self, h):
        got = [(eng, v) for x, eng, v in self.entries() if x == h]
        return got[0] if got else None
    def hdr(self, k): return self.r32(DK4 + 4 * k)
    def lines(self):
        out = {}
        for i in range(64):
            head, h, w, v0, v1, _, _, tail = struct.unpack("<8I", self.u.mem_read(SA + 0x1000 + 32 * i, 32))
            if h:
                out[h] = (head, tail, w, list(struct.pack("<II", v0, v1))[:7])
        return out
    def drum_serial(self): return self.r32(SA + 0x20 + 16)

    # ---- stock's instrument page
    def page(self, track, pad, f):
        """Stock's page select (0x081547EA): the cell set, then the file select (which ends in D5), each through slot 13.
        The cell set takes the model's selected track: the store's first (every test pages a pad of its first track)."""
        v = self.view = getattr(self, "view", None) or self.malloc(0x9000)
        if not getattr(self, "built", False):
            self.call(S.INST_CTOR, v, count=80_000_000)
            self.built = True
        self.notify(EV_CELL, self.handle(pad))
        assert self.r32(v + S.INST_CELL) == self.handle(pad), "stock's cell set took the pad"
        self.notify(EV_FILE, self.handle(f))
        assert self.r32(v + S.INST_FILE) == self.handle(f), "stock's file select took the file"
        return v
    def notify(self, code, a=0):
        """A notification into the page's slot 13 (stock's INST_NOTIFY), as the view tree delivers it."""
        self.u.mem_write(EV, struct.pack("<HHIII", code, 0, a, 0, 0))
        return self.call(self.r32(self.r32(self.view) + 52), self.view, EV, count=80_000_000)
    def tick(self, ms):
        """The page's tick (slot 0) at a time."""
        self.w32(S.M4_TICK_MS, ms)
        self.u.mem_write(STRS + 0x600, bytes(8))
        return self.call(self.r32(self.r32(self.view)), self.view, STRS + 0x600, count=80_000_000)
    def refresh(self): self.notify(EV_REFRESH)
    def rows(self):
        return [[self.h16(self.view + S.INST_KEYS + S.INST_ROW_STRIDE * r + 2 * i) for i in range(8)] for r in range(3)]
    def values(self, r):
        return [self.r32(self.view + S.INST_KEYS + 0x10 + S.INST_ROW_STRIDE * r + 4 * i) for i in range(8)]
    def native(self, r):
        return [self.h16(self.view + S.INST_NATIVE_KEYS + 16 * r + 2 * i) for i in range(8)]
    def turn(self, key, value, handle=None):
        """An encoder edit as a slot posts it (EV_ENCODER, the row's handle, key, value) into the page's slot 12."""
        self.u.mem_write(EV, struct.pack("<HHIII", S.EV_ENCODER, 0, handle or self.r32(self.view + S.INST_CELL), key, value & 0xFFFFFFFF))
        return self.call(self.r32(self.r32(self.view) + 48), self.view, EV, count=80_000_000)
    def context(self, handle, key):
        self.w32(STRS + 0x60, handle); self.w32(STRS + 0x64, 0xDEAD)
        ok = self.call(S.CONTEXT_GET, S.MODEL, STRS + 0x60, key, STRS + 0x64)
        return ok, self.r32(STRS + 0x64)

    # ---- stock's instrument XML (the project writer / loader call these per child)
    def writer(self):
        for a in range(S.INST_SAVE_SITE & ~1, S.INST_SAVE_SITE - 0x200, -2):
            if IMG[a - BASE:a - BASE + 6] == bytes.fromhex("2de9f04f91b0"):
                return a
        raise AssertionError("no writer prologue before INST_SAVE_SITE")
    def save(self, o):
        self.u.mem_write(DOC, bytes(0x1000))
        self.call(S.XML_DOC_INIT, DOC, 1, 0)
        parent = self.call(S.XML_NEW_ELEMENT, DOC, STRS); self.call(S.XML_APPEND_CHILD, DOC, parent)
        self.call(self.writer(), DOC, parent, o, count=40_000_000)
        return parent, self.call(S.XML_CHILD, parent, STRS + 11)
    def attr(self, params, name):
        self.u.mem_write(STRS + 0x100, name.encode() + b"\0")
        a = self.call(S.XML_FIND, params, STRS + 0x100)
        if not a:
            return None
        self.w32(STRS + 0x80, 0)
        return self.r32(STRS + 0x80) if self.call(S.XML_GET_U32, a, STRS + 0x80) == 0 else "?"
    def drum_attrs(self, params):
        return [self.attr(params, "bento_drum_engine")] + [self.attr(params, n) for n in ATTRS]
    def set_attr(self, params, name, value):
        """Set an attribute (P4-1: stock's writer has written the drum names on every pad already: set that one, as an
        edited file would have it; add it if it is not there)."""
        self.u.mem_write(STRS + 0x100, name.encode() + b"\0")
        a = self.call(S.XML_FIND, params, STRS + 0x100) or self.call(S.XML_ADD, params, STRS + 0x100)
        self.call(S.XML_SET_U32, a, value)
    def prop(self, o, k):
        """o's property k (stock's array: count +0xC, {value, key, kind} x 8 B from +8), None if o has no such key."""
        n, a = self.r32(o + 0xC), self.r32(o + 8)
        for i in range(n):
            v, kk = struct.unpack("<IH", bytes(self.u.mem_read(a + 8 * i, 6)))
            if kk == k:
                return v
        return None
    def project(self, store):
        """Stock's project writer (PROJECT_WRITE) on a store, the file write (FILE_WRITER) replaced: the text."""
        if not hasattr(self, "texts"):
            self.texts = []
            def save_hook(uc, a, sz, _):
                pr = uc.reg_read(UC_ARM_REG_R1)
                p, n = self.r32(pr + 0xD4), self.r32(pr + 0xF0)
                self.texts.append(bytes(uc.mem_read(p, n)).rstrip(b"\0").decode("latin1"))
                uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            self.u.hook_add(UC_HOOK_CODE, save_hook, begin=S.FILE_WRITER, end=S.FILE_WRITER)
            self.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
        self.texts.clear()
        self.u.mem_write(STRS + 0x300, b"/p/project.xml\0")
        buf = self.malloc(0x200); self.u.mem_write(buf, bytes(0x200))
        assert self.call(S.PROJECT_WRITE, STRS + 0x300, buf, 0x200, store, count=120_000_000) and len(self.texts) == 1
        return self.texts[0]
    def load(self, element, o): self.call(S.INST_LOAD, element, o, count=40_000_000)
    def bare(self):
        """An instrument element with an empty params element, as save() makes them before stock's writer fills them."""
        self.u.mem_write(DOC, bytes(0x1000))
        self.call(S.XML_DOC_INIT, DOC, 1, 0)
        parent = self.call(S.XML_NEW_ELEMENT, DOC, STRS); self.call(S.XML_APPEND_CHILD, DOC, parent)
        params = self.call(S.XML_NEW_ELEMENT, DOC, STRS + 11); self.call(S.XML_APPEND_CHILD, parent, params)
        return parent, params


def bw(at, to):
    """b.w encoding (T4), typed here."""
    off = to - (at + 4)
    s = (off >> 24) & 1; i1 = (off >> 23) & 1; i2 = (off >> 22) & 1
    j1, j2 = (~i1 ^ s) & 1, (~i2 ^ s) & 1
    return struct.pack("<HH", 0xF000 | s << 10 | (off >> 12) & 0x3FF, 0x9000 | j1 << 13 | j2 << 11 | (off >> 1) & 0x7FF)


@unittest.skipUnless(BUILT, "no drums in this image (1.6.1, or left out)")
class Sites(unittest.TestCase):
    @unittest.skipUnless(HAVE, "drums disabled in this build")
    def test_each_site_goes_to_our_code(self):
        self.assertEqual(IMG[S.INST_REFRESH - BASE:S.INST_REFRESH - BASE + 4], bw(S.INST_REFRESH, SYM["dr_refresh"]))
        self.assertEqual(IMG[S.CHILD_DUP - BASE:S.CHILD_DUP - BASE + 4], bw(S.CHILD_DUP, SYM["dr_dup"]))
        self.assertEqual(word(IMG, S.INST_EV_SLOT), SYM["dr_page_event"] | 1)
        self.assertEqual(word(IMG, S.INST_TICK_SLOT), SYM["dr_view_tick"] | 1)
        self.assertEqual((word(STOCK, S.INST_EV_SLOT), word(STOCK, S.INST_TICK_SLOT)), (S.INST_EVENT | 1, S.INST_TICK | 1))
        self.assertEqual((word(STOCK, S.INST_TICK_SLOT + 52), word(IMG, S.INST_TICK_SLOT + 52)), (S.INST_NOTIFY | 1,) * 2,
                         "slot 13 (the notifications the tests drive) is stock's")
        for n in ("OBJ_CTOR", "NODE_VT_CLONE", "NODE_VT_DESTROY", "INST_SAVE_SITE", "INST_LOAD", "CONTEXT_GET"):
            a = getattr(S, n)
            self.assertNotEqual(IMG[a - BASE:a - BASE + 4], STOCK[a - BASE:a - BASE + 4], f"{n}: the shared site is ours")

    @unittest.skipUnless(DISABLED, "only a BE_DISABLE=drums bisect")
    def test_a_bisect_leaves_the_drum_sites_stock(self):
        for n in ("INST_REFRESH", "INST_EV_SLOT", "INST_TICK_SLOT", "CHILD_DUP"):
            a = getattr(S, n)
            self.assertEqual(IMG[a - BASE:a - BASE + 4], STOCK[a - BASE:a - BASE + 4], n)

    @unittest.skipUnless(HAVE, "drums disabled in this build")
    def test_the_build_and_the_header_agree_on_the_keys(self):
        src = (HERE / "synth_shared.h").read_text()
        self.assertIn("#define DRUM_KEYS_N 7u", src)
        self.assertIn("#define KICK_DEFAULTS {%s, 0}" % ", ".join(map(str, DEFAULTS)), src)
        self.assertIn("#define KICK_LO {%s, 0}" % ", ".join(map(str, LO)), src)
        self.assertIn("#define KICK_HI {%s, 0}" % ", ".join(map(str, HI)), src)
        self.assertEqual(S.KEY_DR_FIRST, KEYS[0])


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Keys(unittest.TestCase):
    def descriptor(self, r, k):
        e = bytes(r.u.mem_read(r.r32(S.REGISTRY) + 32 * k, 32))
        key, typ = struct.unpack_from("<HB", e)
        title = r.e.text(struct.unpack_from("<I", e, 4)[0], 16) if key else ""
        return key, typ, title, struct.unpack_from("<i", e, 16)[0], struct.unpack_from("<i", e, 20)[0]

    def test_the_seven_keys_carry_the_kicks_knobs(self):
        r = Rig()
        self.assertEqual([self.descriptor(r, k) for k in KEYS],
                         [(k, 2, n, KNOB_LO, KNOB_HI) for k, n in zip(KEYS, NAMES)], "type 2 (an integer, as stock's Shift)")
        self.assertEqual([self.descriptor(r, k) for k in (ENGINE_KEY, TRACK_KEY)],
                         [(ENGINE_KEY, 2, "Engine", 0, 5), (TRACK_KEY, 2, "Drum Track", 0, 1)], "P4-1: the engine and the flag")
        self.assertEqual(r.hdr(6), 1, "keys ok")
        self.assertEqual(self.descriptor(r, 0x1AD)[:3], (0x1AD, 2, "Shift"), "the native key below them is stock's")

    def test_one_key_taken_means_none_written(self):
        r = Rig()
        reg = r.r32(S.REGISTRY)
        for k in KEYS + [ENGINE_KEY, TRACK_KEY]:                    # a fresh registry, then a stranger on the fourth key
            r.u.mem_write(reg + 32 * k, bytes(32))
        r.u.mem_write(STRS + 0x700, b"Busy\0")
        r.u.mem_write(reg + 32 * KEYS[3], struct.pack("<HBBI", KEYS[3], 10, 0, STRS + 0x700))
        r.w32(DK4 + 4 * 6, 0)
        r.call(SYM["an_tick"])
        self.assertEqual([self.descriptor(r, k)[0] for k in KEYS], [0, 0, 0, KEYS[3], 0, 0, 0])
        self.assertEqual([self.descriptor(r, k)[0] for k in (ENGINE_KEY, TRACK_KEY)], [0, 0], "P4-1: all nine or none")
        self.assertEqual(r.hdr(6), 0x104, "the black box says which (taken, the fourth)")
        t, p, f = r.pad()
        r.page(t, p, f)
        self.assertEqual(r.rows()[1], r.native(1), "no keys, no Kick page")
        r.stock_ctx.clear(); r.posts.clear()
        r.context(r.handle(p), KEYS[3])
        self.assertEqual(r.stock_ctx, [KEYS[3]], "X3 gives the stranger's key to stock's CONTEXT_GET")
        r.turn(KEYS[3], 5)
        self.assertEqual((r.entries(), [e[3] for e in r.posts if e and e[0] == S.EV_ENCODER]), ([], [KEYS[3]]),
                         "and D6 gives its edits to stock")


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Page(unittest.TestCase):
    """D5 on stock's page: the Config row becomes the Kick's knobs and Poly Mode; everything else is stock's."""
    def test_a_kick_pad_shows_its_knobs(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        nat = [r.native(row) for row in range(3)]
        self.assertEqual(nat, ONESHOT_ROWS, "stock's One-Shot page table, as PAGE_TABLE copied it")
        rows = r.rows()
        self.assertEqual(rows[0], nat[0], "Main: stock's")
        self.assertEqual(rows[1], KEYS + [POLY_MODE], "Config: the Kick's seven knobs and Poly Mode")
        self.assertEqual(rows[2], nat[2], "the envelope / LFO row: stock's")
        self.assertEqual(r.values(1)[:7], DEFAULTS, "a pad with no entry shows the Kick's defaults (read through X3)")
        self.assertEqual(r.knobs(r.handle(p)), None, "showing the page makes no entry")
        self.assertEqual(r.hdr(12), 1, "pages=1")

    def test_a_sample_pad_is_stock(self):
        for name in ("\\Drums\\kick.wav", "\\Drum Synths BE\\BE Kick.wav.bak", "BE Kick", ""):
            with self.subTest(name=name):
                r = Rig()
                t, p, f = r.pad(name=name)
                r.page(t, p, f)
                self.assertEqual(r.rows()[1], r.native(1))

    def test_the_base_name_decides_in_any_case_and_path(self):
        for name in ("BE Kick.wav", "/Drum Synths BE/BE Kick.wav", "\\x\\be kick.WAV"):
            with self.subTest(name=name):
                r = Rig()
                t, p, f = r.pad(name=name)
                r.page(t, p, f)
                self.assertEqual(r.rows()[1], KEYS + [POLY_MODE])

    def test_other_track_types_are_stock(self):
        r = Rig()
        t, p, f = r.pad(kind=0x11)                                   # a Loop track's pad with the carrier
        r.page(t, p, f)
        self.assertEqual(r.u.mem_read(r.view + S.INST_TRACK_TYPE, 1)[0], 0x11, "the cell set took the Loop track's type")
        self.assertEqual(r.rows()[1], r.native(1))
        r.tick(1000)
        self.assertEqual(r.rows()[1], r.native(1), "and the tick leaves it")

    def test_a_kick_pad_after_others_on_its_track(self):
        """Pads 0 and 1 samples, pads 2 and 3 Kicks (children 4 and 6 of the track): the cell set picks each by handle."""
        r = Rig()
        t, pads = r.multi(["\\Drums\\a.wav", "\\Drums\\b.wav", KICK, KICK])
        for i, (p, f) in enumerate(pads):
            r.page(t, p, f)
            self.assertEqual(r.rows()[1], KEYS + [POLY_MODE] if i >= 2 else r.native(1), i)
        self.assertEqual(r.hdr(12), 2, "pages=2 (two Kick pads shown)")

    def test_without_d5_the_page_stays_stocks(self):
        r = Rig(); r.stock_site("INST_REFRESH")
        t, p, f = r.pad()
        r.page(t, p, f)
        self.assertEqual(r.rows()[1], r.native(1))


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Notify(unittest.TestCase):
    """Stock rewrites the rows without D5 (review rc29 S1): 0xA8 (stock's page select posts it after its own refresh) runs
    the cell set alone, 0xC5 (a file loaded onto the pad) only sets the title. The page's tick puts the row right; an
    encoder turn that comes first is not given to the stock key under the Kick's slot."""
    def kick_page(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE])
        return r, t, p, f

    def test_the_cell_set_alone_writes_stocks_row_and_the_tick_repairs_it(self):
        r, t, p, f = self.kick_page()
        r.notify(EV_CELL, r.handle(p))
        self.assertEqual(r.rows()[1], ONESHOT_ROWS[1], "stock's cell set: its own keys, no refresh (what the review found)")
        r.tick(1000)
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], "the tick: the Kick's row again")
        self.assertEqual(r.values(1)[:7], DEFAULTS, "refreshed (values read through X3)")
        self.assertEqual(r.hdr(25), 1, "heals=1")
        r.tick(1010)
        self.assertEqual(r.hdr(25), 1, "nothing to repair on the next pass")

    def test_a_turn_on_the_rewritten_row_is_not_stocks(self):
        r, t, p, f = self.kick_page()
        r.notify(EV_CELL, r.handle(p))
        r.posts.clear(); r.props.clear()
        for slot in range(7):                                        # Reverse .. Loop Fade under the Kick's slots
            r.notify(EV_CELL, r.handle(p))
            key = r.rows()[1][slot]
            self.assertEqual(r.turn(key, 1), 0)
            self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], slot)
        self.assertEqual(([e for e in r.posts if e and e[0] == S.EV_ENCODER], r.props), ([], []), "no stock edit made")
        self.assertIsNone(r.knobs(r.handle(p)), "and no knob changed")
        self.assertEqual(r.hdr(25), 7, "heals=7")
        r.turn(POLY_MODE, 1)                                         # (slot 3 of the rewritten row was Poly Mode: dropped above)
        self.assertIn((S.EV_ENCODER, 0, r.handle(p), POLY_MODE, 1), r.posts, "on the Kick's row Poly Mode (slot 7) is stock's")

    def test_the_sample_pages_config_row_stays_stocks(self):
        r = Rig()
        t, p, f = r.pad(name="\\Drums\\kick.wav")
        r.page(t, p, f)
        r.posts.clear()
        r.turn(0x1C, 1)                                              # Reverse on a sample pad
        self.assertIn(0x1C, [e[3] for e in r.posts if e and e[0] == S.EV_ENCODER])
        r.tick(1000)
        self.assertEqual((r.rows()[1], r.hdr(25)), (ONESHOT_ROWS[1], 0))

    def test_a_refresh_notification_shows_the_kick(self):
        r, t, p, f = self.kick_page()
        r.notify(EV_CELL, r.handle(p)); r.notify(EV_REFRESH)
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], "0xC3 is D5 (stock's handler, our entry)")
        r.notify(EV_CELL, r.handle(p)); r.notify(EV_FILE, r.handle(f))
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], "0xA9 ends in D5")

    def test_a_sample_loaded_under_the_page(self):
        r, t, p, f = self.kick_page()
        r.tick(1000)
        r.filename(f, "\\Drums\\kick.wav"); r.notify(EV_FILE_CHANGED, r.handle(f))
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], "0xC5 sets the title only (what the review found)")
        r.tick(1300)                                                 # the page's engine is asked again after 250 ms
        self.assertEqual(r.rows()[1], ONESHOT_ROWS[1], "stock's row back")
        r.filename(f, KICK); r.notify(EV_FILE_CHANGED, r.handle(f)); r.tick(1600)
        self.assertEqual(r.rows()[1], KEYS + [POLY_MODE], "and the Kick's again")
        self.assertEqual(r.hdr(25), 2, "heals=2")

    def test_a_drum_turn_on_a_page_that_is_no_longer_a_kick(self):
        r, t, p, f = self.kick_page()
        r.filename(f, "\\Drums\\kick.wav"); r.notify(EV_FILE_CHANGED, r.handle(f))
        r.posts.clear()
        self.assertEqual(r.turn(KEYS[0], 99), 0)
        self.assertEqual((r.entries(), [e for e in r.posts if e and e[3] in KEYS]), ([], []), "dropped")
        self.assertEqual(r.rows()[1], ONESHOT_ROWS[1], "and the row is stock's at once")

    def test_without_the_tick_slot_the_row_stays_stocks(self):
        r = Rig(); r.stock_site("INST_TICK_SLOT")
        t, p, f = r.pad()
        r.page(t, p, f)
        r.notify(EV_CELL, r.handle(p)); r.tick(1000)
        self.assertEqual(r.rows()[1], ONESHOT_ROWS[1])


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Edits(unittest.TestCase):
    """D6: an encoder edit of a drum key sets the pad's knob, publishes it and never reaches the model."""
    def test_an_edit_is_stored_published_shown_and_kept_from_the_model(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        s0 = r.drum_serial()
        r.posts.clear(); r.props.clear()
        self.assertEqual(r.turn(KEYS[1], 80), 0, "handled")
        want = DEFAULTS[:]; want[1] = 80
        self.assertEqual(r.knobs(r.handle(p)), (1, want), "an entry: the Kick, Decay 80, the rest defaults")
        head, tail, w, v = r.lines()[r.handle(p)]
        self.assertEqual((head == tail, head & 1, w, v), (True, 0, 0x100, want), "the published line (seqlock closed)")
        self.assertTrue(r.drum_serial() > s0 and r.drum_serial() % 2 == 0, "drum_serial moved (even)")
        self.assertEqual(r.values(1)[:7], want, "the page refreshed with the stored value")
        self.assertEqual([e for e in r.posts if e and e[0] == S.EV_ENCODER and e[3] in KEYS], [], "no UI_POST carries a drum key")
        self.assertEqual([x for x in r.props if x[1] in KEYS], [], "no set_property carries one")
        self.assertEqual(r.hdr(13), 1, "edits=1")

    def test_values_are_clamped_to_the_knobs_range(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        r.turn(KEYS[0], 300); r.turn(KEYS[6], -5)
        self.assertEqual(r.knobs(r.handle(p))[1][0], 180)
        self.assertEqual(r.knobs(r.handle(p))[1][6], 0)
        r.turn(KEYS[0], 3)
        self.assertEqual(r.knobs(r.handle(p))[1][0], 25)

    def test_other_keys_go_to_stock(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        r.posts.clear()
        r.turn(POLY_MODE, 1)
        self.assertIn((S.EV_ENCODER, 0, r.handle(p), POLY_MODE, 1), r.posts, "Poly Mode goes up the view tree as stock's")
        r.posts.clear()
        r.turn(0x1AD, 3); r.turn(0x1B5, 3)                           # the neighbours are stock's keys
        self.assertEqual([e[3] for e in r.posts if e[0] == S.EV_ENCODER], [0x1AD, 0x1B5])

    def test_a_drum_key_on_a_sample_page_is_dropped(self):
        r = Rig()
        t, p, f = r.pad(name="\\Drums\\kick.wav")
        r.page(t, p, f)
        r.posts.clear()
        r.turn(KEYS[2], 70)
        self.assertEqual((r.entries(), [e for e in r.posts if e and e[3] in KEYS]), ([], []))

    def test_without_d6_the_edit_reaches_the_model(self):
        r = Rig(); r.u.mem_write(S.INST_EV_SLOT, struct.pack("<I", S.INST_EVENT | 1))
        t, p, f = r.pad()
        r.page(t, p, f)
        r.posts.clear()
        r.turn(KEYS[1], 80)
        self.assertEqual(r.entries(), [])
        self.assertIn(KEYS[1], [e[3] for e in r.posts if e and e[0] == S.EV_ENCODER], "stock posts it to the model")

    def test_before_the_handshake_an_edit_is_kept_and_published_later(self):
        r = Rig(take=False)
        t, p, f = r.pad()
        r.page(t, p, f)
        r.turn(KEYS[3], 12)
        self.assertEqual(r.knobs(r.handle(p))[1][3], 12)
        self.assertEqual(r.lines(), {}, "nothing written into the area before the M7 took it")
        r.u.mem_write(SA, struct.pack("<5I", 0x37534542, 1, 2, 0xC000, 1))
        r.call(SYM["an_tick"])
        self.assertEqual(r.lines(), {}, "BE4.47: a header without this boot's take word may be the last run's: not trusted")
        r.e.w(TAKE, TAKE_FULL)
        r.call(SYM["an_tick"])
        self.assertEqual(r.lines()[r.handle(p)][3][3], 12, "the tick publishes it")


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Context(unittest.TestCase):
    """X3: CONTEXT_GET answers the drum keys for the handle it is asked about, and passes every other key to stock."""
    def test_drum_keys_and_their_neighbours(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        r.turn(KEYS[4], 9)
        hits = r.stock_ctx; hits.clear()
        self.assertEqual([r.context(r.handle(p), k)[1] for k in KEYS], DEFAULTS[:4] + [9] + DEFAULTS[5:])
        self.assertEqual(r.context(0x7777, KEYS[0]), (1, DEFAULTS[0]), "a handle with no entry: the defaults")
        self.assertEqual(hits, [])
        r.context(r.handle(p), 0x1AD); r.context(r.handle(p), 0x1B5); r.context(r.handle(p), POLY_MODE)
        self.assertEqual(hits, [0x1AD, 0x1B5, POLY_MODE], "stock's body for the rest")


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Xml(unittest.TestCase):
    """X1 / X2 on a pad, as the project writer and loader call stock's writer / reader per child (0x081312BA, 0x08131528),
    and stock's copies: a load's scratch -> the commit copy -> the live pad; a save's snapshot copy -> the writer."""
    def edited(self, r, store=None):
        t, p, f = r.pad(store)
        r.page(t, p, f)
        for k, v in zip(KEYS, (120, 10, 20, 30, 40, 50, 60)):
            r.turn(k, v)
        return t, p, f

    def test_the_writer_writes_a_kicks_knobs(self):
        """P4-1: X1 puts DK4's values into the pad's native keys (N1), and stock's writer writes them."""
        r = Rig()
        t, p, f = self.edited(r)
        self.assertEqual(r.drum_attrs(r.save(p)[1]), [1, 120, 10, 20, 30, 40, 50, 60])
        self.assertEqual([r.prop(p, k) for k in [ENGINE_KEY] + KEYS], [1, 120, 10, 20, 30, 40, 50, 60], "the pad's own keys")
        self.assertEqual(r.hdr(14), 1, "saves=1")
        self.assertEqual(r.drum_attrs(r.save(t)[1]), [None] * 8, "the track itself gets none")
        self.assertEqual(r.attr(r.save(t)[1], "bento_drumtrack"), 0, "but the flag (P4-1), 0 on a track the menu did not make")
        self.assertEqual(r.drum_attrs(r.save(f)[1]), [None] * 8, "nor its file node (type 5)")

    def test_a_project_has_each_name_once(self):
        """P4-1: stock's writer writes the native keys after X1's site, each over an attribute of that name, so X1 sets the
        pad's keys rather than adding the names (added ones come out with the object's zeros: M, this test with the old
        X1). Stock's project writer on the live store: each name once with DK4's values on the edited pad, stock's zeros on
        a plain one, the flag on the track."""
        r = Rig()
        t, p, f = self.edited(r)
        r.child(r.live, 1, t); r.child(r.live, 5, t)
        text = r.project(r.live)
        pads = re.findall(r'<cell type="saminst">\s*<params([^>]*)/?>', text)
        self.assertEqual(len(pads), 2)
        for a, want in zip(pads, ([1, 120, 10, 20, 30, 40, 50, 60], [0] * 8)):
            got = re.findall(r'(\w+)="([^"]*)"', a)
            names = [n for n, _ in got]
            self.assertEqual([names.count(n) for n in ["bento_drum_engine"] + ATTRS], [1] * 8)
            self.assertEqual([int(dict(got)[n]) for n in ["bento_drum_engine"] + ATTRS], want)
        self.assertEqual(text.count("bento_drumtrack="), 1)

    def test_a_pad_without_an_entry_writes_stocks_zeros(self):
        """P4-1: the names are native keys of every pad (N1), so stock writes them with the template's 0 (engine 0: none)."""
        r = Rig()
        t, p, f = r.pad()
        self.assertEqual(r.drum_attrs(r.save(p)[1]), [0] * 8)
        self.assertEqual(r.hdr(14), 0, "X1 wrote nothing")

    def test_a_project_round_trip_through_stocks_copies(self):
        r = Rig()
        t, p, f = self.edited(r)
        element, params = r.save(p)
        r.u.mem_write(DK4 + 0x80, bytes(0x400))                       # a new session: nothing in the table
        scratch = r.store()                                          # the loader: scratch track and its children
        st = r.create(scratch, S.TYPE_ONESHOT); sp = r.child(scratch, 1, st); r.child(scratch, 5, st)
        r.load(element, sp)
        self.assertEqual(r.knobs(r.handle(sp)), (1, [120, 10, 20, 30, 40, 50, 60]), "the reader marks the scratch pad")
        r.call(S.STORE_COPY_ALL, r.live, scratch, count=40_000_000)   # the commit
        lp = r.children(r.objects(r.live)[0])[0]
        self.assertNotEqual(r.handle(lp), r.handle(sp), "the live pad is a copy with a new handle")
        self.assertEqual(r.knobs(r.handle(lp)), (1, [120, 10, 20, 30, 40, 50, 60]), "H2 carried the pad's knobs")
        snap = r.store()
        r.call(S.STORE_COPY_ALL, snap, r.live, count=40_000_000)     # the save snapshot
        sp2 = r.children(r.objects(snap)[0])[0]
        self.assertEqual(r.drum_attrs(r.save(sp2)[1]), [1, 120, 10, 20, 30, 40, 50, 60], "and the save writes them")
        self.assertGreaterEqual(r.hdr(7), 2, "carried")
        r.call(SYM["an_tick"])
        self.assertIn(r.handle(lp), r.lines(), "the UI tick publishes the live pad's line")

    def test_a_track_copy_and_paste_carries_its_pads_knobs(self):
        """STORE_CLONE_H (clipboard copy / paste, preset apply): the track's clone through slot 3 (H2) and its pads."""
        r = Rig()
        t, p, f = self.edited(r)
        clip = r.store()
        def clone_h(dst, h, src):
            r.w32(STRS + 0x44, 0)
            r.call(S.STORE_CLONE_H, dst, h, src, STRS + 0x44, count=40_000_000)
            return [o for o in r.objects(dst) if r.handle(o) == r.r32(STRS + 0x44)][0]
        ct = clone_h(clip, r.handle(t), r.live)                       # copy
        pt = clone_h(r.live, r.handle(ct), clip)                      # paste
        cp, pp = r.children(ct)[0], r.children(pt)[0]
        self.assertEqual(len({r.handle(p), r.handle(cp), r.handle(pp)}), 3, "three pads, three handles")
        self.assertEqual([r.knobs(r.handle(x)) for x in (cp, pp)], [(1, [120, 10, 20, 30, 40, 50, 60])] * 2)
        self.assertIsNone(r.knobs(r.handle(r.children(pt)[1])), "the file node gets none")

    def test_without_h2_a_load_loses_the_knobs(self):
        r = Rig(); r.stock_site("NODE_VT_CLONE")
        t, p, f = self.edited(r)
        scratch = r.store()
        r.call(S.STORE_COPY_ALL, scratch, r.live, count=40_000_000)
        self.assertIsNone(r.knobs(r.handle(r.children(r.objects(scratch)[0])[0])))

    def test_the_reader_clamps_and_fills_in(self):
        """(P4-1: this build's writer writes all eight names on every pad, so the element with missing knobs, as a
        hand-edited file could have it, is made here: an instrument element and its empty params.)"""
        r = Rig()
        t, p, f = r.pad()
        element, params = r.bare()
        r.set_attr(params, "bento_drum_engine", 1)
        r.set_attr(params, ATTRS[0], 999); r.set_attr(params, ATTRS[2], 7)
        q = r.child(r.live, 1, t)
        r.load(element, q)
        self.assertEqual(r.knobs(r.handle(q)), (1, [180] + DEFAULTS[1:2] + [7] + DEFAULTS[3:]))
        self.assertEqual(r.hdr(15), 1, "loads=1")

    def test_a_pad_read_without_attributes_loses_a_stale_entry(self):
        r = Rig()
        t, p, f = self.edited(r)
        q = r.pad()[1]
        element, _ = r.save(q)                                       # a plain pad's element (P4-1: engine 0)
        r.load(element, p)
        self.assertIsNone(r.knobs(r.handle(p)))
        self.assertEqual([r.prop(p, k) for k in [ENGINE_KEY] + KEYS], [0] * 8, "and stock's reader stores the zeros")

    def test_an_engine_this_build_lacks_makes_no_entry(self):
        r = Rig()
        t, p, f = r.pad()
        element, params = r.save(p)
        r.set_attr(params, "bento_drum_engine", 3)                   # a Snare (S6d)
        r.load(element, p)
        self.assertIsNone(r.knobs(r.handle(p)))


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Handles(unittest.TestCase):
    """H1 / H3 extended to the pads, H4: stock's copy of one child."""
    def test_removing_a_track_drops_its_pads_entries(self):
        r = Rig()
        t, p, f = Xml.edited(None, r)
        r.call(S.STORE_REMOVE, r.live, r.handle(t))
        self.assertEqual(r.entries(), [])
        self.assertEqual(r.hdr(9), 1, "dropped=1")

    def four_pads(self, r):
        """A track of four pads, the last two Kicks with their own knobs (children 4 and 6: H2 / H3 walk past 0)."""
        t, pads = r.multi(["\\Drums\\a.wav", "\\Drums\\b.wav", KICK, KICK])
        for (p, f), tune in zip(pads[2:], (101, 102)):
            r.page(t, p, f); r.turn(KEYS[0], tune)
        self.assertEqual([r.knobs(r.handle(p)) for p, f in pads],
                         [None, None, (1, [101] + DEFAULTS[1:]), (1, [102] + DEFAULTS[1:])])
        return t, pads

    def test_a_track_of_several_pads_is_carried_pad_by_pad(self):
        r = Rig()
        t, pads = self.four_pads(r)
        clip = r.store()
        r.w32(STRS + 0x44, 0)
        r.call(S.STORE_CLONE_H, clip, r.handle(t), r.live, STRS + 0x44, count=40_000_000)     # H2
        ct = [o for o in r.objects(clip) if r.handle(o) == r.r32(STRS + 0x44)][0]
        kids = r.children(ct)
        self.assertEqual(len(kids), 8)
        self.assertEqual([r.knobs(r.handle(k)) for k in kids],
                         [None] * 4 + [(1, [101] + DEFAULTS[1:]), None, (1, [102] + DEFAULTS[1:]), None])
        self.assertEqual(r.hdr(7), 2, "carried=2")
        scratch = r.store()                                          # and a project's way: the whole store
        r.call(S.STORE_COPY_ALL, scratch, r.live, count=40_000_000)
        self.assertEqual([r.knobs(r.handle(k)) is not None for k in r.children(r.objects(scratch)[0])],
                         [False] * 4 + [True, False, True, False])

    def test_removing_a_track_of_several_pads_drops_each(self):
        r = Rig()
        t, pads = self.four_pads(r)
        r.call(S.STORE_REMOVE, r.live, r.handle(t))                 # H3
        self.assertEqual((r.entries(), r.hdr(9)), ([], 2), "both Kick pads' entries, dropped=2")

    def test_without_h3_the_entry_stays(self):
        r = Rig(); r.stock_site("NODE_VT_DESTROY")
        t, p, f = Xml.edited(None, r)
        r.call(S.STORE_REMOVE, r.live, r.handle(t))
        self.assertIsNotNone(r.knobs(r.handle(p)))

    def test_entries_from_an_earlier_boot_are_gone_at_boot(self):
        r = Rig(prior=[(1, 1, DEFAULTS), (5, 1, DEFAULTS), (0x900, 1, DEFAULTS)])
        self.assertEqual(r.entries(), [])
        self.assertEqual(r.hdr(10), 3, "forgot=3")

    def test_a_pad_copied_by_stock_keeps_its_knobs(self):
        r = Rig()
        t, p, f = Xml.edited(None, r)
        clip = r.store()
        holder = r.create(clip, 0x17)                                # as the clipboard holds a child (0x08124E66)
        r.w32(STRS + 0x44, 0)
        ok = r.e.call(S.CHILD_DUP, clip, 1, holder, p, STRS + 0x44)
        c = r.r32(STRS + 0x44)
        self.assertTrue(ok and c and r.handle(c) != r.handle(p))
        self.assertEqual(r.knobs(r.handle(c)), r.knobs(r.handle(p)))
        self.assertEqual(r.hdr(8), 1, "dups=1")

    def test_without_h4_the_copy_has_none(self):
        r = Rig(); r.stock_site("CHILD_DUP")
        t, p, f = Xml.edited(None, r)
        clip = r.store(); holder = r.create(clip, 0x17)
        r.w32(STRS + 0x44, 0)
        r.e.call(S.CHILD_DUP, clip, 1, holder, p, STRS + 0x44)
        self.assertIsNone(r.knobs(r.handle(r.r32(STRS + 0x44))))

    def test_a_full_table_gives_up_copies_never_live_pads(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        base = r.r32(S.OBJ_HANDLE_CTR)
        for i in range(64):                                          # 64 copies' entries (handles of no live pad)
            r.put(base + 100 + i, 1, DEFAULTS, i)
        r.turn(KEYS[0], 99)                                          # a live pad's edit
        self.assertEqual(r.knobs(r.handle(p))[1][0], 99)
        self.assertIsNone(r.knobs(base + 100), "the lowest copy went")
        self.assertEqual((r.hdr(17), r.hdr(16)), (1, 0), "evicted=1 full=0")


@unittest.skipUnless(HAVE, "no drums in this image (1.6.1, left out or disabled)")
class Tick(unittest.TestCase):
    """The page's tick: the CPU readout in the page's title label (the file name's place), then stock's tick."""
    def titles(self, r, take=True):
        got, stock = r.titled, r.stock_ticks
        got.clear(); stock.clear()
        r.u.mem_write(STRS + 0x600, bytes(8))
        for ms in (1000, 1600, 2200):
            r.w32(S.M4_TICK_MS, ms); r.call(SYM["an_tick"])
            r.call(r.r32(r.r32(r.view)), r.view, STRS + 0x600, count=80_000_000)
        return [t for w, t in got if w == r.view + S.INST_TITLE], len(stock)

    def test_a_kick_page_shows_the_load(self):
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        texts, n = self.titles(r)
        self.assertTrue(texts and all(x.startswith("Kick CPU ") and " drums " in x for x in texts), texts)
        self.assertEqual(n, 3, "stock's tick each time")

    def readout(self, r, ms, seen, pm_sum, over, blocks, cycles):
        """The black box's LOAD counters and the M7's drum stats at a time, the UI tick, then the page's tick: its title."""
        r.u.mem_write(D0L, struct.pack("<7I", seen, pm_sum, 0, 0, 0, 0, over))
        r.u.mem_write(DSTATS, struct.pack("<2I", blocks, cycles >> 4))
        r.w32(S.M4_TICK_MS, ms); r.call(SYM["an_tick"])
        r.titled.clear(); r.tick(ms + 1)
        got = [t for w, t in r.titled if w == r.view + S.INST_TITLE]
        return got[-1] if got else None

    def test_the_numbers(self):
        """CPU = the LOAD permille over the stretch, drums = the drum hook's cycles per drum block / 1,280,000."""
        r = Rig()
        t, p, f = r.pad()
        r.page(t, p, f)
        self.readout(r, 1000, 1000, 600000, 0, 100, 100 * 50000)                # the boot's first look: counters only
        self.assertEqual(self.readout(r, 1600, 1100, 663000, 0, 200, 100 * 50000 + 100 * 128000),
                         "Kick CPU 63% drums 10%", "63000 / 100 blocks; 128,000 cycles a drum block")
        self.assertEqual(self.readout(r, 2200, 1200, 753000, 2, 250, 100 * 50000 + 100 * 128000 + 50 * 409600),
                         "Kick CPU 90% drums 32% !", "a block over budget in the last 2 s")
        self.assertEqual(r.hdr(21), 320, "drum_pm=320")
        self.assertEqual(self.readout(r, 4800, 1300, 803000, 2, 250, 100 * 50000 + 100 * 128000 + 50 * 409600),
                         "Kick CPU 50% drums 0%", "no drum block in the stretch: 0; the ! gone after 2 s")

    def test_before_the_handshake(self):
        r = Rig(take=False)
        t, p, f = r.pad()
        r.page(t, p, f)
        self.assertEqual(set(self.titles(r)[0]), {"Kick (DSP off)"})

    def test_a_sample_page_keeps_stocks_title(self):
        r = Rig()
        t, p, f = r.pad(name="\\Drums\\kick.wav")
        r.page(t, p, f)
        self.assertEqual(self.titles(r)[0], [])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
