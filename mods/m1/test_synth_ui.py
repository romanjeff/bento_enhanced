# SPDX-License-Identifier: GPL-3.0-or-later
"""The Analog UI hooks on the M4 (S6a: A3-A7, A9, A10, X3, X6), m4_synth.c in the built image.

    python test_synth_ui.py <stock bento1.bin> <m1 build dir>

What runs: the built image's sites and wrappers (the menu cell and count, the patched bl / vtable words, the
CONTEXT_GET entry wrapper) and m4_synth.c, and stock's SLOT_TITLE where a test says so. Stubbed (hooks at their
entries, as test_step.py does): stock's selected-track lookups (GET_PROPERTY, FIND_TRACK, RESOLVE_OBJECT), UI_POST,
PROJECT_TOUCH, SLOT_TITLE (by default) and the three native handlers our vtable words chain to; they record what they
were given. The track's handle and instrument type can change between calls, as stock's instrument swap would. So
this proves our logic and that each site goes to it; stock's page builder and menu code around them do not run here
(docs/plan-synths-168.md T4 / T5: see the S6a section). Skipped on a build without the analog group (1.6.1).

The M4's own table (AN4, typed here independently: our slice + 0xE20, 256 B: a 128 B header, then 16 entries of
{handle, flags | glide << 4}) holds the truth; the synth area's table is its published copy, written only once the M7
header there is stamped for this M4 boot (review 2026-11-08). Stock's object handle counter (OBJ_HANDLE_CTR) is set
above every handle a test uses (they are objects of this boot); BE4.22's handle hooks themselves (H1-H3) run on stock's
own object store in test_handles.py. The UI_POST stub does stock's instrument swap itself when a test gives it one
(`swap`): stock's swap is done before UI_POST returns (rc23 handles investigation).
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
del sys.argv[1:3]
HAVE = "analog" not in MAN.get("omitted", []) and "synth.an_tick" in MAN.get("m4_symbols", {})
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack  # addr-ok: harness
SCR = 0x20010000             # harness events, buffers, the fake track objects  # addr-ok: harness
SA = S.REGION - 0x8000 if HAVE else 0
TAB = SA + 0x200
AN4 = S.D2_SLICE + 0xE20 if HAVE else 0
CFG = S.D2_SLICE + 0x1E0 if HAVE else 0
D0L = S.D2_SLICE + 0xFE0 if HAVE else 0
TAKE = S.D2_SLICE + 0x1D8 if HAVE else 0
M7_MAGIC, M4_MAGIC, AN4_MAGIC = 0x37534542, 0x34534542, 0x32344E41
TRACK, HANDLE = 3, 0x7A11
CTR = 0x10000                # stock's handle counter in the rig: above every handle used here
VIEW = 0x20014000            # harness synth view (only its WT1 label is touched)  # addr-ok: harness
MD = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)


class Rig:
    def __init__(self, kind=None, area=True, real_title=False):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG[:0x200000 - (BASE - 0x08000000)])
        for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xC0000000, 0x8000000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.mem_write(SENT, b"\xfe\xe7")
        self.log, self.stock_ran = [], set()
        self.handle = HANDLE                                  # what FIND_TRACK answers for TRACK
        self.objects = {}                                     # handle -> instrument type (RESOLVE_OBJECT)
        self.obj(HANDLE, S.TYPE_WAVETABLE if kind is None else kind)
        stubs = {S.GET_PROPERTY: self.get_property, S.FIND_TRACK: self.find_track, S.RESOLVE_OBJECT: self.resolve,
                 S.UI_POST: self.rec("post", 2), S.PROJECT_TOUCH: self.rec("touch", 1),
                 S.MENU_HANDLER: self.rec("menu_stock", 2, ret=7), S.SYNTH_VIEW_EVENT: self.rec("view_stock", 2, ret=1),
                 S.SYNTH_VIEW_TICK: self.rec("tick_stock", 2)}
        if not real_title:
            stubs[S.SLOT_TITLE] = self.rec("title", 2)
        for a, f in stubs.items():
            u.hook_add(UC_HOOK_CODE, f, begin=a, end=a)
        u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.stock_ran.add(a), begin=S.CONTEXT_GET + 4, end=S.CONTEXT_GET + 4)
        self.sa_writes = []
        u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: self.sa_writes.append(a), begin=SA, end=SA + 0x8000)
        self.w32(CFG, 0x31474144); self.w32(CFG + 12, 1)     # CFG: boot generation 1
        if area:
            self.take()
        self.w32(S.REGISTRY, 0xC1000000)          # an empty registry (stock's builder fills it on the device; test_keys runs it)  # addr-ok: harness
        self.ms(1000)
        self.w32(S.OBJ_HANDLE_CTR, CTR)
        self.swap = None                                      # (handle, type): what stock's instrument swap makes

    def take(self, gen=1, boot=1):
        """The M7 took the area: its header, stamped for M4 boot `boot`."""
        self.u.mem_write(SA, struct.pack("<5I", M7_MAGIC, gen, 2, 0xC000, boot))

    def obj(self, handle, kind):
        """A fake instrument object for `handle`: vtable[0] -> movs r0, #kind; bx lr."""
        at = SCR + 0x2000 + 0x40 * len(self.objects)
        self.u.mem_write(at + 0x20, bytes([kind, 0x20, 0x70, 0x47])); self.w32(at + 0x10, (at + 0x20) | 1)
        self.w32(at, at + 0x10); self.w32(at + 0x30, handle)
        self.objects[handle] = at

    def w32(self, a, x): self.u.mem_write(a, struct.pack("<I", x & 0xFFFFFFFF))
    def r32(self, a): return struct.unpack("<I", self.u.mem_read(a, 4))[0]
    def reg(self, i): return self.u.reg_read(UC_ARM_REG_R0 + i)
    def ms(self, t): self.w32(S.M4_TICK_MS, t)
    def ret(self, v):
        self.u.reg_write(UC_ARM_REG_R0, v); self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def get_property(self, u, a, sz, _):
        self.log.append(("prop", self.reg(1))); self.ret(TRACK if self.reg(1) == S.KEY_SEL_TRACK else 0)
    def find_track(self, u, a, sz, _):
        ok = self.reg(1) == TRACK and self.handle
        if ok: self.w32(self.reg(2), self.handle)
        self.ret(1 if ok else 0)
    def resolve(self, u, a, sz, _):
        at = self.objects.get(self.reg(1))
        if at: self.w32(self.reg(2), at)
        self.ret(1 if at else 0)
    def rec(self, name, nargs, ret=0):
        def f(u, a, sz, _):
            args = [self.reg(i) for i in range(nargs)]
            if name == "post":
                args.append(struct.unpack("<5I", u.mem_read(args[1], 20)))
                if args[-1][0] == 0x42 and self.swap:         # stock's instrument replace, inside UI_POST
                    h, kind = self.swap; self.obj(h, kind); self.handle = h
            if name == "title":
                args.append(bytes(u.mem_read(args[1], 32)).split(b"\0")[0].decode())
            self.log.append((name, *args)); self.ret(ret)
        return f

    def call(self, fn, *args):
        u = self.u
        for i, x in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, x & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        try:
            u.emu_start(fn | 1, SENT, count=2_000_000)
        except UcError as e:
            raise AssertionError(f"{e} at pc {u.reg_read(UC_ARM_REG_PC):#x}")
        return u.reg_read(UC_ARM_REG_R0)

    def tick(self): self.call(SYM["an_tick"])
    def put(self, flags, glide=0, handle=HANDLE, i=0):
        """An AN4 entry as the load path writes it, then the UI tick (publish)."""
        self.tick()
        self.w32(AN4 + 0x80 + 8 * i, handle); self.w32(AN4 + 0x84 + 8 * i, (flags | 1) | glide << 4)
        self.w32(AN4 + 8, self.r32(AN4 + 8) + 1)
        self.tick()
    def entry(self, i=0):
        h, w = struct.unpack("<2I", self.u.mem_read(AN4 + 0x80 + 8 * i, 8))
        return h, w & 15, (w >> 4) & 0x3FF
    def entries(self): return [self.entry(i) for i in range(16)]
    def line(self, i=0): return struct.unpack("<8I", self.u.mem_read(TAB + 32 * i, 32))
    def hdr(self, k): return self.r32(AN4 + 4 * k)
    def ev(self, code, key=0, value=0):
        self.u.mem_write(SCR + 0x200, struct.pack("<HHIIII", code, 0, 0, key, value, 0))
        return SCR + 0x200


def bl_target(a):
    i = next(MD.disasm(IMG[a - BASE:a - BASE + 4], a))
    return int(i.op_str[1:], 16)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Sites(unittest.TestCase):
    def word(self, a): return struct.unpack_from("<I", IMG, a - BASE)[0]

    def test_menu_has_a_ninth_cell_analog(self):
        cell = struct.unpack_from("<5I", IMG, S.MENU_CELL - BASE)
        self.assertEqual((cell[0], cell[2], cell[3], cell[4]), (S.ACT_ANALOG, 0, 0, 1))
        self.assertEqual(IMG[cell[1] - BASE:cell[1] - BASE + 7], b"Analog\0")
        self.assertEqual(IMG[S.MENU_COUNT - BASE:S.MENU_COUNT - BASE + 2], bytes.fromhex("0923"), "movs r3, #9")

    def test_each_site_goes_to_our_code(self):
        self.assertEqual(self.word(S.MENU_VT_SLOT), SYM["an_menu_event"] | 1)
        self.assertEqual(self.word(S.SYNTH_VIEW_EV_SLOT), SYM["an_view_event"] | 1)
        self.assertEqual(self.word(S.SYNTH_VIEW_TICK_SLOT), SYM["an_view_tick"] | 1)
        self.assertEqual(bl_target(S.PAGE_KEYS_COPY), SYM["an_page_keys"])
        for i in range(1, 7):
            self.assertEqual(bl_target(getattr(S, f"PANE_TEXT_{i}")), SYM["an_pane_text"], i)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Menu(unittest.TestCase):
    def test_menu_action_makes_the_track_analog(self):
        r = Rig(); r.tick()
        self.assertEqual(r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG)), 0)
        posts = [x for x in r.log if x[0] == "post"]
        self.assertEqual([p[3] for p in posts], [(0x42, 0, 6, TRACK, 0), (0x16, 0, 0, 0, 0)], "stock's Wavetable action, then back")
        self.assertIn(("touch", S.MODEL), r.log)
        self.assertEqual(r.entry()[:2], (HANDLE, 1))
        self.assertEqual(r.line()[1:3], (HANDLE, 1), "published at once (the UI task)")
        self.assertFalse([x for x in r.log if x[0] == "menu_stock"], "our action is not stock's")

    def test_the_swap_inside_ui_post_is_marked(self):
        """Stock's instrument replace runs inside UI_POST (STORE_REMOVE, then STORE_CREATE: a new handle); the new
        Wavetable handle is marked right after it, the old object (a one-shot track here) is not."""
        r = Rig(kind=S.TYPE_ONESHOT); r.tick()
        r.swap = (0x7A12, S.TYPE_WAVETABLE)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        self.assertEqual(r.entry()[:2], (0x7A12, 1))
        self.assertEqual(r.line()[1:3], (0x7A12, 1), "published at once")
        self.assertEqual((r.hdr(20), r.hdr(28)), (1, 0), "menus=1 menu_miss=0")

    def test_no_watch_after_the_action(self):
        """BE4.21's 10 s watch is gone: a Wavetable that appears on the track later (not made by this action) stays plain."""
        r = Rig(kind=S.TYPE_ONESHOT); r.tick()
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        r.obj(0x7A13, S.TYPE_WAVETABLE); r.handle = 0x7A13
        r.ms(1500); r.tick(); r.ms(5000); r.tick()
        self.assertEqual(r.entries(), [(0, 0, 0)] * 16)

    def test_menu_action_on_a_non_wavetable_result_marks_nothing(self):
        r = Rig(kind=S.TYPE_ONESHOT); r.tick()
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        r.ms(5000); r.tick()
        self.assertEqual(r.entries(), [(0, 0, 0)] * 16)
        self.assertEqual((r.hdr(20), r.hdr(28)), (0, 1), "menus=0 menu_miss=1 (the black box's device evidence)")

    def test_picking_analog_again_on_the_same_object_keeps_sync_and_glide(self):
        """Review N7: the track keeps its object (same handle after UI_POST): Sync and Glide stay; menu_same counts it."""
        r = Rig(); r.put(3, 400)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        self.assertEqual(r.entry(), (HANDLE, 3, 400), "review N7")
        self.assertEqual((r.hdr(20), r.hdr(28), r.hdr(29)), (1, 0, 1), "menus=1 menu_miss=0 menu_same=1")

    def test_a_new_object_starts_as_a_plain_analog(self):
        """Jeff's decision 7: stock makes a new object for the choice (case 0x42: REMOVE, CREATE): a new instrument,
        so it is Analog with Sync Off and Glide 0, nothing from the old one (whose entry goes with stock's destroy,
        H3: test_handles)."""
        r = Rig(); r.put(3, 400)
        r.swap = (0x7A14, S.TYPE_WAVETABLE)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        self.assertEqual(r.entry(1), (0x7A14, 1, 0), "AN_ON only, glide 0")
        self.assertEqual((r.hdr(20), r.hdr(28), r.hdr(29)), (1, 0, 0), "menus=1 menu_miss=0 menu_same=0")

    def test_any_stock_instrument_action_clears_analog(self):
        for code in (S.ACT_WAVETABLE, S.ACT_INST_FIRST, S.ACT_INST_FIRST + 3):
            with self.subTest(code=hex(code)):
                r = Rig(); r.put(3, 400)
                self.assertEqual(r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(code)), 7, "stock's handler's result")
                self.assertEqual(r.entry(), (0, 0, 0), "review S3: Analog -> another instrument retires the entry")
                self.assertEqual(r.line()[2], 0, "and the M7's copy")
                self.assertEqual([x[0] for x in r.log if x[0] == "menu_stock"], ["menu_stock"])
        r = Rig(); r.put(3, 400)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(0x3C))           # not an instrument action
        self.assertEqual(r.entry(), (HANDLE, 3, 400))


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Handshake(unittest.TestCase):
    def test_a_table_from_before_the_handshake_is_published_at_it(self):
        """Review B1/B2: a project loaded before the M7 has taken the area keeps its Analog tracks."""
        r = Rig(area=False)
        r.put(3, 250)
        self.assertEqual(r.entry(), (HANDLE, 3, 250), "the M4's table has it")
        self.assertEqual(r.sa_writes, [], "nothing written into stock's pool memory")
        r.take(); r.tick()
        self.assertEqual(r.line()[1:4], (HANDLE, 3, 250), "published once the M7 header is there")
        self.assertEqual(struct.unpack("<4I", r.u.mem_read(SA + 0x20, 16)), (M4_MAGIC, 1, 1, 4), "M4 header: gen, boot, serial")

    def test_a_header_stamped_for_another_boot_is_not_attached(self):
        """Review S2: a header a reset left in SDRAM (another M4 boot) is stock's memory now."""
        r = Rig(area=False); r.take(boot=7)
        r.put(1)
        self.assertEqual(r.sa_writes, [])

    def test_a_new_m7_generation_gets_the_table_again(self):
        r = Rig(); r.put(1, 10)
        r.u.mem_write(TAB, bytes(32))                            # (the M7 never writes these; force a difference)
        s0 = r.r32(SA + 0x2C)
        r.take(gen=2); r.tick()
        self.assertEqual(r.line()[1:4], (HANDLE, 1, 10))
        self.assertEqual(r.r32(SA + 0x24), 2, "m7gen")
        self.assertEqual(r.r32(SA + 0x2C), s0 + 2, "the serial moves: the M7 refills its per-root cache")
        n = len(r.sa_writes); r.tick()
        self.assertEqual(len(r.sa_writes), n, "nothing changed: nothing written")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Table(unittest.TestCase):
    """BE4.22: no sweep. A full table gives up its oldest copy (the lowest handle not in the live project); entries
    from an earlier boot (a handle not below stock's counter) are dropped."""
    def test_a_full_table_gives_up_the_oldest_copy(self):
        r = Rig()
        for i in range(16):
            r.obj(0x100 + i, S.TYPE_WAVETABLE); r.put(1, handle=0x100 + i, i=i)
        r.objects.pop(0x109); r.objects.pop(0x105)              # copies: not in the live project (RESOLVE_OBJECT fails)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))   # HANDLE: a 17th Analog object
        self.assertEqual(r.entry(5), (HANDLE, 1, 0), "the lowest copy's slot")
        self.assertEqual(r.entry(9)[0], 0x109, "the other copy stays")
        self.assertEqual([r.entry(i)[0] for i in range(16) if i not in (5, 9)], [0x100 + i for i in range(16) if i not in (5, 9)],
                         "live tracks are never given up")
        self.assertEqual((r.hdr(24), r.hdr(25)), (0, 1), "full=0 evicted=1")

    def test_a_table_of_live_tracks_loses_the_mark_and_counts_it(self):
        r = Rig()
        for i in range(16):
            r.obj(0x100 + i, S.TYPE_WAVETABLE); r.put(1, handle=0x100 + i, i=i)
        r.call(SYM["an_menu_event"], SCR + 0x300, r.ev(S.ACT_ANALOG))
        self.assertEqual([e[0] for e in r.entries()], [0x100 + i for i in range(16)])
        self.assertEqual((r.hdr(24), r.hdr(25)), (1, 0), "full=1 evicted=0")

    def test_entries_from_an_earlier_boot_are_dropped(self):
        """A handle not below stock's counter names no object of this boot (the counter starts again at reset, the
        table survives it): the tick drops it (H1 does it at the next object anyway: test_handles) and its slot is free."""
        r = Rig(); r.put(1, handle=HANDLE, i=0); r.put(3, 7, handle=CTR, i=1); r.put(1, handle=CTR + 0x500, i=2)
        self.assertEqual([e[0] for e in r.entries()[:3]], [HANDLE, 0, 0])
        self.assertEqual(r.hdr(11), 2, "forgot=2")
        self.assertEqual(r.line(1)[2], 0, "and the M7's copy")

    def test_a_dead_handle_is_not_swept(self):
        """BE4.21 retired an entry whose handle no longer resolved; stock's destroy (H3) does that now, so the tick
        leaves entries alone however long they wait."""
        r = Rig(); r.put(1, handle=0x6666, i=0)                 # no object for it in this rig
        t = 1000
        for _ in range(16 * 10):
            t += 260; r.ms(t); r.tick()
        r.ms(t + 60000); r.tick()
        self.assertEqual(r.entry(0), (0x6666, 1, 0))


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Page(unittest.TestCase):
    def test_page_keys_on_analog_tracks_only(self):
        tab = SCR + 0x400
        keys = [0xC4, 0x3, 0xC9, 0, 0, 0, 0, 0] * 2 + [0x11] * 72
        for analog in (False, True):
            with self.subTest(analog=analog):
                r = Rig()
                if analog: r.put(1)
                else: r.tick()
                r.u.mem_write(tab, struct.pack("<88H", *keys))
                r.call(SYM["an_page_keys"], SCR + 0x600, tab, 0xB0)
                got = struct.unpack("<88H", r.u.mem_read(SCR + 0x600, 0xB0))
                want = list(keys)
                if analog:
                    want[3], want[11] = S.KEY_AN_GLIDE, S.KEY_AN_SYNC
                self.assertEqual(list(got), want)

    def test_context_get_answers_our_keys_and_passes_the_rest(self):
        r = Rig(); r.put(3, 401)
        out = SCR + 0x700
        self.assertEqual(r.call(S.CONTEXT_GET, S.MODEL, SCR + 0x710, S.KEY_AN_SYNC, out), 1)
        self.assertEqual(r.r32(out), 1)
        self.assertEqual(r.call(S.CONTEXT_GET, S.MODEL, SCR + 0x710, S.KEY_AN_GLIDE, out), 1)
        self.assertEqual(r.r32(out), 401, "Glide exactly as stored, on the descriptor's 0..1000")
        self.assertEqual(r.stock_ran, set(), "stock's body never ran for them")
        try:
            r.call(S.CONTEXT_GET, S.MODEL, SCR + 0x710, S.KEY_SCALE, out)
        except AssertionError:
            pass                                                  # stock's body needs the real model: it ran, that is the point
        self.assertEqual(r.stock_ran, {S.CONTEXT_GET + 4}, "any other key goes to stock's code")

    def test_encoder_edits_of_our_keys_are_ours(self):
        r = Rig(); r.put(1, 0)
        self.assertEqual(r.call(SYM["an_view_event"], SCR + 0x300, r.ev(S.EV_ENCODER, S.KEY_AN_SYNC, 1)), 0)
        self.assertEqual(r.line()[2], 3, "Sync on")
        r.call(SYM["an_view_event"], SCR + 0x300, r.ev(S.EV_ENCODER, S.KEY_AN_GLIDE, 400))
        r.call(SYM["an_view_event"], SCR + 0x300, r.ev(S.EV_ENCODER, S.KEY_AN_GLIDE, 401))
        self.assertEqual(r.line()[3], 401, "review S2: one step up from 400 stays 401 (the value is stored as it comes)")
        r.call(SYM["an_view_event"], SCR + 0x300, r.ev(S.EV_ENCODER, S.KEY_AN_GLIDE, 5000))
        self.assertEqual(r.line()[3], 1000, "clamped")
        self.assertEqual(r.entry(), (HANDLE, 3, 1000))
        self.assertFalse([x for x in r.log if x[0] == "view_stock"], "never forwarded (stock would post 0xD0 to the model)")
        self.assertEqual(r.line()[0], r.line()[7])
        r.call(SYM["an_view_event"], SCR + 0x300, r.ev(S.EV_ENCODER, S.KEY_SCALE, 3))
        self.assertEqual([x[0] for x in r.log if x[0] == "view_stock"], ["view_stock"], "other keys: stock's handler")

    def test_pane_texts(self):
        for analog, given, want in ((True, "No WAV", "Analog"), (True, "sine.wav", "Saw > Square > Pulse"), (False, "No WAV", "No WAV")):
            with self.subTest(analog=analog, given=given):
                r = Rig()
                if analog: r.put(1)
                else: r.tick()
                text = S.STR_NO_WAV if given == "No WAV" else SCR + 0x800
                r.u.mem_write(SCR + 0x800, given.encode() + b"\0")
                r.call(SYM["an_pane_text"], SCR + 0x900, text)
                self.assertEqual([x[3] for x in r.log if x[0] == "title"], [want])


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class Indicator(unittest.TestCase):
    """D3 (required by Jeff's 'visible CPU limit'): WT1's 'No WAV' label shows the M7's LOAD, from the black box's
    counters (D0L: seen, permille sum, peak, bins <50 / 50-80 / 80-100 / 100+ %)."""
    def load(self, r, seen, pm_sum, over):
        r.u.mem_write(D0L, struct.pack("<7I", seen, pm_sum, 0, 0, 0, 0, over))

    def run_tick(self, r, t):
        r.ms(t); r.tick(); r.ms(t + 1)
        r.log.clear(); r.call(SYM["an_view_tick"], VIEW, SCR + 0xA00)
        return [x for x in r.log if x[0] in ("title", "tick_stock")]

    def test_the_label_shows_the_load_and_marks_overruns(self):
        r = Rig(); r.put(1)
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        self.load(r, 1100, 600000 + 63000, 0)
        got = self.run_tick(r, 2600)
        self.assertEqual([(x[0], x[1], x[-1]) for x in got], [("title", VIEW + S.VIEW_NOWAV_LABEL, "Analog CPU 63%"), ("tick_stock", VIEW, SCR + 0xA00)])
        self.load(r, 1200, 663000 + 90000, 2)
        self.assertEqual(self.run_tick(r, 3200)[0][3], "Analog CPU 90% !", "a block over budget in the last 2 s")
        self.load(r, 1300, 753000 + 50000, 2)
        self.assertEqual(self.run_tick(r, 5300)[0][3], "Analog CPU 50%", "the ! goes after 2 s")

    def test_a_save_restarting_the_counters_is_not_a_spike(self):
        r = Rig(); self.load(r, 1000, 600000, 5); r.put(1)           # the boot's first look only takes the counters
        self.run_tick(r, 2000)
        self.load(r, 10, 7000, 0)                                # LOADREQ: a save starts a new stretch
        self.run_tick(r, 2600)
        self.load(r, 110, 7000 + 40000, 0)
        self.assertEqual(self.run_tick(r, 3200)[0][3], "Analog CPU 40%")

    def test_plain_tracks_and_no_dsp(self):
        r = Rig(); r.tick()
        self.assertEqual(self.run_tick(r, 2000), [("tick_stock", VIEW, SCR + 0xA00)], "a plain track: stock's tick only")
        r = Rig(area=False); r.put(1)
        self.assertEqual(self.run_tick(r, 2000)[0][3], "Analog (DSP off)", "the M7 has not taken the area")

    def test_stocks_slot_title_puts_it_in_the_label(self):
        r = Rig(real_title=True); r.put(1)
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        self.load(r, 1100, 663000, 0); self.run_tick(r, 2600)
        label = VIEW + S.VIEW_NOWAV_LABEL
        self.assertEqual(bytes(r.u.mem_read(label + 0x31, 16)).split(b"\0")[0], b"Analog CPU 63%")
        self.assertEqual(r.u.mem_read(label + 0x5C, 1)[0], 1, "marked for a redraw")

    def test_over_budget_blocks_with_analog_are_counted(self):
        r = Rig(); r.put(1)
        self.load(r, 1000, 600000, 0); self.run_tick(r, 2000)
        r.w32(SA + 0x40, 50)                                     # the M7: blocks with an Analog voice
        self.load(r, 1100, 700000, 3); self.run_tick(r, 2600)
        self.load(r, 1200, 800000, 4); self.run_tick(r, 3200)    # no Analog block in between
        self.assertEqual(r.hdr(18), 3, "over_with_analog")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class BlackBox(unittest.TestCase):
    def line(self, r):
        buf, ln = SCR + 0x1000, SCR + 0xF00
        r.w32(ln, 0)
        r.call(SYM["an_print"], buf, ln, 0x800)
        return bytes(r.u.mem_read(buf, r.r32(ln))).decode()

    def test_black_box_line(self):
        r = Rig(); r.put(3, 40)
        r.u.mem_write(SA + 0x40, struct.pack("<16I", 10, 1000, 30000, 2, 0, 128, 0, 9, 1, 0, 0, 10, 20, 3, 0, 0))
        line = self.line(r)
        for part in ("ANALOG gen=1", "keys=ok", "tracks=1", "blocks=10", "avg_cyc=1600", "peak_cyc=30000", "osc_peak=2",
                     "n_max=128", "misses=3", "over_with_analog=0", "evicted=0", "menu_miss=0", "menu_same=0",
                     f"handles ctr={CTR} carried=0 dropped=0 forgot=0"):
            self.assertIn(part, line)
        self.assertTrue(line.endswith("\r\n"))

    def test_why_the_area_is_not_taken(self):
        r = Rig(area=False); r.put(1)
        r.w32(TAKE, 0xA5 << 24 | 2 << 8 | 2)                       # the M7: the Region alone, twice
        line = self.line(r)
        self.assertIn("ANALOG area not taken (the pool kept less than 1 MB: Region only, times=2)", line)
        self.assertIn("tracks=1", line, "the M4's table is there all the same")
        r = Rig(area=False); r.take(boot=7)
        self.assertIn("(header for boot 7, this boot 1)", self.line(r))


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
