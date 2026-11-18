# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog's settings as native stock properties (BE4.40, docs/plan-native-props.md N1 / N2), on stock 1.6.8's own code.

    python test_native_props.py <stock bento1.bin> <m1 build dir> [<BE4.22 kit dir>]
    (BE_REF: the BE4.40 reference build dir, default ~/bento/out/m1-be440: test_be440_opens_a_p3_project;
     BE_REF441: the BE4.41 build dir, default ~/bento/out/m1-be441: test_a_be441_project_with_bit_depth_opens)

What runs: the built image from the M4 reset's .data copy and static constructors on (emu.py), model init's registry build
(our N2 wrapper: stock's REG_BUILD, then np_keys), stock's object store, track and child constructors (STORE_CREATE,
CHILD_CREATE: the patched property template, N1), its setter, NODE_CLONE / CHILD_DUP / STORE_COPY_ALL / STORE_CLONE_H,
CONTEXT_GET, the model's 0xD0 edit (MODEL_CHILD_EDIT), the full push (PROP_PUSH) and the push after a project load
(LOAD_PUSH_ALL), and stock's project writer (PROJECT_WRITE) and reader (PROJECT_READ) with only the file I/O replaced:
FILE_WRITER's text is captured, DOC_READ hands stock's own parser (XML_PARSE) the text. SET_PROPERTY (the post to the M7)
and ENGINE_PROPERTY are logged and return. The same reader then runs on stock 1.6.8 itself and on the BE4.22 kit's image
(its default path: <flash>/bento_enhanced-v4.22, the third argument overrides it), with the text this build wrote.
P3 Q1 (docs/plan-p3-fm-bits.md 3.1, 3.2, 6.1): three more keys (Sub FM, Bit Depth, FM 1>2), so the template has its first
type-0xFF pad entry, which makes type 0xFF creatable (CHILD_CREATE): Hazard pins that and shows no reader path asks for it.
The same text is read by BE4.40 (the likeliest rollback) and through stock's preset reader (PATCH_READ).
BE4.42 (docs/plan-p3-fm-bits.md, BE4.42): Bit Depth (0x1A4, bento_bits) is gone: six keys, two pad entries (the count stays
0x304). A project BE4.41 saved with Bit Depth set (written by the BE4.41 build itself, BE_REF441) opens here with its other
values, the bits value is not read, never reaches the M7, and the next save drops it.
BE4.43 (docs/plan-p3-fm-bits.md, BE4.43): the FM envelope's two keys, FM Atk 0x1A4 (bento_fmatk, default 0) and FM Dec 0x1A0
(bento_fmdec, default 128 = Inf): eight keys, 0x2FC + 8 = 0x304, so no pad entry any more (Hazard: type 0xFF is not creatable
again, as on stock). A BE4.41 project's bento_bits still is not read (0x1A4's token is bento_fmatk: FM Atk keeps its default);
a project BE4.42 saved (BE_REF442, default ~/bento/out/m1-be442, the v4.42 kit's image) opens with Atk 0 / Dec Inf, the
values that play BE4.42's static depth.
Typed independently of the code under test: our keys and tokens (plan-native-props 3.1, plan-p3-fm-bits 2.1), Shape's
default 48, stock's seven type-6 keys in order, the template count rule (cmp.w encodes every multiple of 4 from 0x300), the
set_property message's index (WT1 0, WT2 1). Skipped on a build without the analog group (1.6.1).
"""
import json, os, re, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
sys.path.insert(0, str(HERE))
import emu
from unicorn import UC_HOOK_CODE, UcError
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_LR, UC_ARM_REG_SP
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB
from bases import from_build
STOCK = Path(sys.argv[1]).read_bytes()
S = from_build(sys.argv[2], STOCK)
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
KIT422 = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE.parents[2] / "flash" / "bento_enhanced-v4.22"
REF440 = Path(os.environ.get("BE_REF", str(Path.home() / "bento" / "out" / "m1-be440")))   # BE4.40 (P3 Q1: the rollback)
REF441 = Path(os.environ.get("BE_REF441", str(Path.home() / "bento" / "out" / "m1-be441")))   # BE4.41 (BE4.42: Bit Depth's files)
REF442 = Path(os.environ.get("BE_REF442", str(Path.home() / "bento" / "out" / "m1-be442")))   # BE4.42 (BE4.43: no envelope)
del sys.argv[1:4]
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}
BUILT = "analog" not in MAN.get("omitted", []) and "np_keys" in SYM
DISABLED = BUILT and "analog" in MAN.get("disabled", [])
HAVE = BUILT and not DISABLED

BASE = 0x08040000
HEAP = 0xC4100000                  # harness heap  # addr-ok: harness
STRS = 0xC0F00000                  # harness strings and out cells  # addr-ok: harness
FEED = 0xC0C00000                  # the text DOC_READ hands the parser  # addr-ok: harness
STUB = 0x083F0000                  # our DOC_READ replacement (feeds the parser; executable: 0xC0000000+ is XN)  # addr-ok: harness
EV = 0xC0F01000                    # an event record  # addr-ok: harness
# our keys, tokens and defaults (docs/plan-native-props.md 3.1; Shape's default 48 = saw, NP6), in template order; P3 Q1
# (docs/plan-p3-fm-bits.md 2.1): Sub FM, FM 1>2 after Glide, both 0 (BE4.42: BE4.41's Bit Depth, between them, is gone)
# BE4.43: FM Atk (0x1A4, default 0: instant) and FM Dec (0x1A0, default 128: Inf) after FM 1>2
OURS = [(0x1C0, "bento_osc_type", 0), (0x1A7, "bento_shape", 48), (0x1AB, "bento_sync", 0), (0x1B7, "bento_glide", 0),
        (0x18B, "bento_subfm", 0), (0x132, "bento_fm12", 0), (0x1A4, "bento_fmatk", 0), (0x1A0, "bento_fmdec", 128)]
BITS = 0x1A4                       # BE4.41's Bit Depth key (bento_bits): BE4.42 had no such key; BE4.43's FM Atk (bento_fmatk)
FMATK, FMDEC = 0x1A4, 0x1A0
BE442_N = 6                        # BE4.42's keys: the first six
KEYS = [k for k, _, _ in OURS]
BE440_N = 4                        # BE4.40's keys: the first four
PAD = 0xFF                         # the template's pad type (build.py NP_PAD_TYPE)
STOCK_T6 = [0x03, 0xC4, 0xC7, 0xC9, 0x23, 0x26, 0x81]          # stock's type-6 keys, in order (plan section 2)
WT_CHILDREN = bytes.fromhex("0606080a0a09090b0b0c0d")           # a Wavetable track's children (stock's list, plan section 2)
KS = Ks(KS_ARCH_ARM, KS_MODE_THUMB)


class Rig:
    """One M4: the image's reset, model init's registry build, a live store as [MODEL]."""
    def __init__(self, img=IMG, base=S, wrap=True, sym=None):
        e = self.e = emu.Emu(img, init_ctors=False, base=base)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness
        e.ctors()
        self.heap = [HEAP]
        def malloc(uc, a, sz, _):
            n = uc.reg_read(UC_ARM_REG_R0); p = self.heap[0]; self.heap[0] += (n + 31) & ~31
            uc.mem_write(p, bytes(n)); uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
        self.registry = self.malloc(0x400)
        self.call((sym or SYM)["wrap_reg_build"] if wrap else S.REG_BUILD, self.registry, count=40_000_000)
        self.reg = self.r32(S.REGISTRY)
        self.live = self.store()
        e.w(S.MODEL, self.live)
        self.u.mem_write(STRS, b"instrument\0params\0")
        self.props, self.texts = [], []
        def setprop(uc, a, sz, _):
            sp = uc.reg_read(UC_ARM_REG_SP)
            self.props.append((uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), self.s32(uc.reg_read(UC_ARM_REG_R3)),
                               struct.unpack("<h", uc.mem_read(sp, 2))[0]))
            uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, setprop, begin=S.SET_PROPERTY, end=S.SET_PROPERTY)
        def ret0(uc, a, sz, _): uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, ret0, begin=S.ENGINE_PROPERTY, end=S.ENGINE_PROPERTY)
        # the push's waits, lock, engine helper and sample loader (PUSH_STUB_*): they need the engine and the card
        for stub in (S.PUSH_STUB_DELAY, S.PUSH_STUB_LOCK, S.PUSH_STUB_ENGINE, S.PUSH_STUB_SAMPLE):
            self.u.hook_add(UC_HOOK_CODE, ret0, begin=stub, end=stub)
        self.u.mem_map(0x04000000, 0x10000)  # addr-ok: harness (a child the push reaches writes near address 0 + 0x4000000 with the engine stubbed)
        def save_hook(uc, a, sz, _):                               # FILE_WRITER(path, writer object): its text, "written"
            pr = uc.reg_read(UC_ARM_REG_R1)
            p, n = self.r32(pr + 0xD4), self.r32(pr + 0xF0)
            self.texts.append(bytes(uc.mem_read(p, n)).rstrip(b"\0").decode("latin1"))
            uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, save_hook, begin=S.FILE_WRITER, end=S.FILE_WRITER)
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: uc.reg_write(UC_ARM_REG_PC, STUB | 1), begin=S.DOC_READ, end=S.DOC_READ)
        self.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness (hooks on code translated by the constructors)

    @staticmethod
    def s32(v): return v - (1 << 32) if v >= 1 << 31 else v
    def malloc(self, n): return self.call(S.M4_MALLOC, n)
    def call(self, fn, *args, count=40_000_000):
        try:
            return self.e.call(fn, *args, count=count)
        except UcError as x:
            raise AssertionError(f"{x} at pc {self.u.reg_read(UC_ARM_REG_PC):#x} lr {self.u.reg_read(UC_ARM_REG_LR):#x} in {fn:#x}")
    def r32(self, a): return self.e.r(a)
    def w32(self, a, v): self.e.w(a, v)
    def text(self, s):
        p = self.heap[0]; self.heap[0] += (len(s) + 32) & ~31
        self.u.mem_write(p, s.encode() + b"\0"); return p
    def buf(self, n):
        p = self.heap[0]; self.heap[0] += (n + 31) & ~31
        self.u.mem_write(p, bytes(n)); return p

    # ---- stock's store, tracks and their children
    def store(self):
        s = self.malloc(0x6C)
        self.call(S.STORE_INIT, s); self.call(S.STORE_POOL_INIT, s, 0x40000)
        return s
    def create(self, store, kind):
        self.w32(STRS + 0x40, 0)
        assert self.call(S.STORE_CREATE, store, kind, STRS + 0x40), f"stock made the object {kind:#x}"
        return self.r32(STRS + 0x40)
    def child(self, store, kind, parent):
        self.w32(STRS + 0x40, 0)
        ok = self.call(S.CHILD_CREATE, store, kind, parent, STRS + 0x40)
        return self.r32(STRS + 0x40) if ok else None
    def wavetable(self, store=None):
        """A Wavetable track with stock's children list: (track, WT1, WT2)."""
        store = store or self.live
        t = self.create(store, S.TYPE_WAVETABLE)
        for k in WT_CHILDREN:
            assert self.child(store, k, t)
        c = self.children(t)
        return t, c[0], c[1]
    def children(self, o): return [self.r32(o + S.NODE_CHILDREN + 4 * i) for i in range(self.r32(o + S.NODE_CHILD_N))]
    def handle(self, o): return self.r32(o + 0x10)
    def objects(self, store): return [self.r32(store + 4 * i) for i in range(self.r32(store + S.STORE_COUNT))]
    def props_of(self, o):
        n, a = self.r32(o + 0xC), self.r32(o + 8)
        return [struct.unpack("<IHB", bytes(self.u.mem_read(a + 8 * i, 7))) for i in range(n)]
    def props_cmp(self, o):
        """(value, key, kind) with a string property's text instead of its pointer."""
        return [(self.e.text(v, 64) if kind == 2 and v else v, k, kind) for v, k, kind in self.props_of(o)]
    def prop(self, o, k):
        for v, kk, _ in self.props_of(o):
            if kk == k:
                return self.s32(v)
        return None
    def ours(self, o): return [self.prop(o, k) for k in KEYS]
    def set(self, o, k, v): self.call(S.PROP_SET, o, k, v & 0xFFFFFFFF)
    def vtype(self, o): return self.call(self.r32(self.r32(o)), o)
    def context(self, h, key):
        self.w32(STRS + 0x60, h); self.w32(STRS + 0x64, 0xDEAD)
        ok = self.call(S.CONTEXT_GET, S.MODEL, STRS + 0x60, key, STRS + 0x64)
        return ok, self.s32(self.r32(STRS + 0x64))
    def edit(self, h, key, value):
        """The model's 0xD0 edit, as the synth view forwards an encoder slot's 0xCF (SYNTH_VIEW_EVENT 0x08161158)."""
        self.u.mem_write(EV, struct.pack("<HHIIi", 0xD0, 0, h, key, value))
        self.props.clear()
        self.call(S.MODEL_CHILD_EDIT, S.MODEL, EV)
        return list(self.props)

    # ---- stock's project writer and reader, only the file I/O replaced
    def save(self, store=None):
        self.texts.clear()
        ok = self.call(S.PROJECT_WRITE, self.text("/p/project.xml"), self.buf(0x200), 0x200, store or self.live, count=120_000_000)
        assert ok and len(self.texts) == 1, f"the project writer ran (ok {ok}, files {len(self.texts)})"
        return self.texts[0]
    def feed(self, text):
        b = text.encode("latin1")
        self.u.mem_write(FEED, b + b"\0")
        src = (f"push {{r4, lr}}; mov r0, r3; movw r1, #{FEED & 0xFFFF:#x}; movt r1, #{FEED >> 16:#x}; "
               f"movw r2, #{len(b) & 0xFFFF:#x}; movt r2, #{len(b) >> 16:#x}; movw r4, #{(S.XML_PARSE | 1) & 0xFFFF:#x}; "
               f"movt r4, #{S.XML_PARSE >> 16:#x}; blx r4; cmp r0, #0; ite eq; moveq r0, #1; movne r0, #0; pop {{r4, pc}}")
        self.u.mem_write(STUB, bytes(KS.asm(src, STUB)[0]))
    def load(self, text, reader=None):
        """stock's project reader (or reader: PATCH_READ, the preset file's, with the same arguments) into a new store"""
        st = self.store()
        self.feed(text)
        ok = self.call(reader or S.PROJECT_READ, self.text("/p/project.xml"), self.buf(0x200), 0x200, st, count=120_000_000)
        assert ok, "the reader read it"
        return st
    def tracks(self, store):
        out = []
        for i in range(16):
            self.w32(STRS + 0x50, 0)
            if not self.call(S.STORE_NTH, store, i, STRS + 0x50):
                break
            out.append(self.r32(STRS + 0x50))
        return out
    def wt_tracks(self, store): return [t for t in self.tracks(store) if self.vtype(t) == S.TYPE_WAVETABLE]


def cells(text):
    """{(cell index in its track, attribute): value} of every <cell>'s params element with one of our tokens, in order."""
    out = []
    for m in re.finditer(r"<cell[^>]*>\s*<params([^>]*)/?>", text):
        out.append(dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))))
    return out


# setup_wt's values of our keys (Type, Shape, Sync, Glide, Sub FM, FM 1>2), each different from its default
V1 = [1, 127, 0, 77, 250, 0, 20, 40]     # BE4.43: FM Atk 20, FM Dec 40
V2 = [1, 5, 1, 0, 1000, 640, 127, 0]    # BE4.43: FM Atk 127, FM Dec 0


def setup_wt(r):
    """A Wavetable track, WT1 Analog (Shape 127, Glide 77, Sub FM 250, FM Atk 20, FM Dec 40), WT2 Analog (Shape 5, Sync on,
    Sub FM 1000, FM 1>2 640, FM Atk 127, FM Dec 0), WT1's Position 321."""
    t, w1, w2 = r.wavetable()
    for o, vals in ((w1, V1), (w2, V2)):
        for k, v in zip(KEYS, vals):
            r.set(o, k, v)
    r.set(w1, 0xC9, 321)
    return t, w1, w2


@unittest.skipUnless(BUILT, "no analog group in this image")
class Template(unittest.TestCase):
    """N1: stock's template with our keys after its type-6 run, its two literals and three bounds."""
    def test_the_sites_point_at_our_copy(self):
        at, n = SYM["np_template"], SYM["np_template_n"]
        word = lambda a: struct.unpack_from("<I", IMG, a - BASE)[0]
        if DISABLED:
            for site in ("PROP_TEMPLATE_LIT_A", "PROP_TEMPLATE_LIT_B", "PROP_TEMPLATE_CMP_A", "PROP_TEMPLATE_CMP_B", "PROP_TEMPLATE_LAST", "REG_BUILD_SITE"):
                a = getattr(S, site)
                self.assertEqual(IMG[a - BASE:a - BASE + 4], STOCK[a - BASE:a - BASE + 4], f"BE_DISABLE=analog: {site} stock")
            return
        self.assertEqual(n % 4, 0); self.assertTrue(0x300 <= n <= 0x3FC, "cmp.w can encode the count")
        for site in ("PROP_TEMPLATE_LIT_A", "PROP_TEMPLATE_LIT_B"):
            self.assertEqual(word(getattr(S, site)), at, site)
        for site, src in (("PROP_TEMPLATE_CMP_A", f"cmp.w r3, #{n:#x}"), ("PROP_TEMPLATE_CMP_B", f"cmp.w r3, #{n:#x}"),
                          ("PROP_TEMPLATE_LAST", f"movw r7, #{n - 1:#x}")):
            a = getattr(S, site)
            self.assertEqual(IMG[a - BASE:a - BASE + 4], bytes(KS.asm(src, a)[0]), site)
        self.assertEqual(IMG[S.PROP_TEMPLATE - BASE:S.PROP_TEMPLATE - BASE + 0x2FC * 8], STOCK[S.PROP_TEMPLATE - BASE:S.PROP_TEMPLATE - BASE + 0x2FC * 8],
                         "stock's table stays in place (its end starts a separate key list)")

    def test_the_copy_is_stocks_with_ours_after_the_type_6_run(self):
        at, n = SYM["np_template"], SYM["np_template_n"]
        st = [STOCK[S.PROP_TEMPLATE - BASE + 8 * i:S.PROP_TEMPLATE - BASE + 8 * i + 8] for i in range(0x2FC)]
        ours = [IMG[at - BASE + 8 * i:at - BASE + 8 * i + 8] for i in range(n)]
        run = [i for i, e in enumerate(st) if e[0] == 6]
        self.assertEqual([struct.unpack_from("<H", st[i], 2)[0] for i in run], STOCK_T6)
        want = st[:run[-1] + 1] + [struct.pack("<BBHI", 6, 0, k, d) for k, _, d in OURS] + st[run[-1] + 1:]
        pad = [struct.pack("<BBHI", 0xFF, 0, 0, 0)] * (n - len(want))
        self.assertEqual(ours, want + pad)
        self.assertEqual((n, len(pad)), (0x304, 0), "eight keys: 0x2FC + 8 = 0x304, no pad entry (BE4.43; BE4.42's six "
                         "had two pads, BE4.41's seven one, BE4.40's four 0x300)")

    @unittest.skipUnless(HAVE, "BE_DISABLE=analog: the template is stock's")
    def test_every_object_gets_stocks_keys_and_type_6_ours_too(self):
        """CHILD_CREATE of every type the template has (under a Wavetable track) and the top-level tracks: the arrays are
        stock's key for key, kind and default, and the type-6 objects have ours appended with their defaults."""
        a, b = Rig(), Rig(img=STOCK, wrap=False)
        types = sorted({STOCK[S.PROP_TEMPLATE - BASE + 8 * i] for i in range(0x2FC)})
        for r in (a, b):
            r.parent = r.create(r.live, S.TYPE_WAVETABLE)
        for t in types:
            with self.subTest(type=t):
                x, y = a.child(a.live, t, a.parent), b.child(b.live, t, b.parent)
                self.assertEqual(x is None, y is None, "created on both or neither")
                if x is None:
                    continue
                px, py = a.props_cmp(x), b.props_cmp(y)
                if t == 6:
                    self.assertEqual(px[:len(py)], py)
                    self.assertEqual([(k, v) for v, k, _ in px[len(py):]], [(k, d) for k, _, d in OURS])
                else:
                    self.assertEqual(px, py)
        for kind in (S.TYPE_WAVETABLE, S.TYPE_ONESHOT):
            self.assertEqual(a.props_cmp(a.create(a.live, kind)), b.props_cmp(b.create(b.live, kind)), f"track {kind:#x}")

    @unittest.skipUnless(HAVE, "BE_DISABLE=analog")
    def test_the_setter_cannot_add_a_key(self):
        r = Rig(); t, w1, w2 = r.wavetable()
        n = len(r.props_of(t)); r.set(t, KEYS[0], 1)
        self.assertEqual((len(r.props_of(t)), r.prop(t, KEYS[0])), (n, None), "a track does not have the oscillator's keys")
        r.set(w2, KEYS[0], 1)
        self.assertEqual((r.prop(w1, KEYS[0]), r.prop(w2, KEYS[0])), (0, 1), "each oscillator is its own object")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Stock(unittest.TestCase):
    """Stock stores, copies, reads, edits and forwards our keys by itself."""
    def test_clone_and_child_dup_keep_them_under_new_handles(self):
        r = Rig(); t, w1, w2 = setup_wt(r)
        out = r.buf(8)
        self.assertTrue(r.call(S.NODE_CLONE, t, r.live + 0x58, out)); c = r.r32(out)
        cc = r.children(c)
        self.assertNotEqual(r.handle(cc[1]), r.handle(w2))
        self.assertEqual((r.ours(cc[0]), r.ours(cc[1])), (r.ours(w1), r.ours(w2)))
        par = r.create(r.live, S.TYPE_WAVETABLE); r.w32(out, 0)
        self.assertTrue(r.call(S.CHILD_DUP, r.live, 6, par, w2, out))
        self.assertEqual(r.ours(r.r32(out)), V2)
        dst = r.store(); r.call(S.STORE_COPY_ALL, dst, r.live)
        tt = r.wt_tracks(dst)[0]
        self.assertEqual([r.ours(x) for x in r.children(tt)[:2]], [V1, V2], "a load commit / save snapshot")
        r.w32(out, 0); r.call(S.STORE_CLONE_H, dst, r.handle(t), r.live, out)
        pasted = [o for o in r.objects(dst) if r.handle(o) == r.r32(out)]
        self.assertEqual(len(pasted), 1, "STORE_CLONE_H gives the copy's handle")
        self.assertEqual([r.ours(x) for x in r.children(pasted[0])[:2]], [V1, V2], "a preset apply / paste")

    def test_context_get_reads_each_key_of_each_oscillator(self):
        r = Rig(); t, w1, w2 = setup_wt(r)
        for o, want in ((w1, V1), (w2, V2)):
            self.assertEqual([r.context(r.handle(o), k) for k in KEYS], [(1, v) for v in want])

    def test_an_edit_is_stored_and_forwarded_with_the_oscillators_index(self):
        """0xD0 -> MODEL_CHILD_EDIT: the value is stored on that oscillator and SET_PROPERTY(track, key, value, index) goes
        to the M7 (WT1 0, WT2 1), unclamped (the M7 clamps: test_analog); the other oscillator keeps its own."""
        r = Rig(); t, w1, w2 = r.wavetable()
        for o, idx in ((w1, 0), (w2, 1)):
            for k in KEYS:
                for v in (1, 5000, -3):
                    with self.subTest(osc=idx, key=hex(k), value=v):
                        sent = r.edit(r.handle(o), k, v)
                        self.assertEqual(r.prop(o, k), v)
                        self.assertIn((r.handle(t), k, v, idx), sent)
        r.edit(r.handle(w2), KEYS[1], 9)
        self.assertEqual((r.prop(w1, KEYS[1]), r.prop(w2, KEYS[1])), (-3, 9))

    def test_the_push_after_a_create_sends_type_first(self):
        """PROP_PUSH (menu create, preset apply, a load): every oscillator's keys with its index, Type before the others
        (the M7 keeps Shape / Sync / Glide only once the oscillator is Analog)."""
        r = Rig(); t, w1, w2 = setup_wt(r)
        r.props.clear()
        r.call(S.PROP_PUSH, S.MODEL, r.handle(t), 1, count=120_000_000)
        for idx, want in ((0, V1), (1, V2)):
            got = [(k, v) for h, k, v, i in r.props if i == idx and k in KEYS]
            self.assertEqual(got, list(zip(KEYS, want)), f"oscillator {idx}: our keys in template order (P3: Type, Shape, "
                             "Sync, Glide, Sub FM, FM 1>2)")
            self.assertTrue(all(h == r.handle(t) for h, k, v, i in r.props if k in KEYS), "addressed to the track")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Project(unittest.TestCase):
    """Stock's project writer and reader carry our keys by themselves (no hook of ours runs there)."""
    @classmethod
    def setUpClass(cls):
        r = cls.r = Rig()
        cls.t, cls.w1, cls.w2 = setup_wt(r)
        cls.text = r.save()

    def test_the_writer_writes_our_tokens_on_both_oscillator_cells(self):
        c = cells(self.text)
        self.assertGreaterEqual(len(c), 2)
        for i, want in ((0, V1), (1, V2)):
            self.assertEqual(tuple(c[i].get(tok) for _, tok, _ in OURS), tuple(map(str, want)), f"cell {i}")
        self.assertEqual(c[0].get("wavepos"), "321")
        self.assertNotIn("bento_analog", self.text, "BE4.22..BE4.31's track attributes are gone")

    def test_the_reader_restores_them(self):
        r = Rig()
        st = r.load(self.text)
        t = r.wt_tracks(st)[0]; c = r.children(t)
        self.assertEqual([r.ours(c[0]), r.ours(c[1])], [V1, V2])
        self.assertEqual(r.prop(c[0], 0xC9), 321)

    def test_the_push_after_a_load_reaches_the_m7(self):
        """P0: a project load reaches the M7 through stock's own push. The reader fills a scratch store, the commit copies it
        into the live store (STORE_COPY_ALL, 0x0814094A), then LOAD_PUSH_ALL (0x08140998) pushes every object: our keys of
        WT1 arrive, after stock's own, with the loaded values. Here the push stops after WT1's file reference (the sample
        loader is stubbed), so WT2 is checked through PROP_PUSH above (the same per-child loop)."""
        r = Rig()
        st = r.load(self.text)
        r.call(S.STORE_COPY_ALL, r.live, st)
        t = r.wt_tracks(r.live)[0]
        r.props.clear()
        r.call(S.LOAD_PUSH_ALL, S.MODEL, count=200_000_000)
        got = [(k, v) for h, k, v, i in r.props if i == 0 and k in KEYS and h == r.handle(t)]
        self.assertEqual(got, list(zip(KEYS, V1)), "WT1, index 0")
        self.assertIn((r.handle(t), 0xC9, 321, 0), r.props, "stock's own Position the same way, before ours")

    def test_out_of_range_file_values_load_raw(self):
        """Stock stores what the file says: the readers clamp (the M7: test_analog; the page: test_synth_ui)."""
        bad = self.text.replace('bento_osc_type="1" bento_shape="127"', 'bento_osc_type="-7" bento_shape="1000"', 1)
        bad = bad.replace('bento_subfm="250"', 'bento_subfm="5000"', 1)
        bad = bad.replace('bento_subfm="1000" bento_fm12="640"', 'bento_subfm="-3" bento_fm12="99999"', 1)
        bad = bad.replace('bento_fmatk="20" bento_fmdec="40"', 'bento_fmatk="500" bento_fmdec="-9"', 1)      # BE4.43
        bad = bad.replace('bento_fmatk="127" bento_fmdec="0"', 'bento_fmatk="-1" bento_fmdec="129"', 1)
        self.assertEqual(bad.count("99999") + bad.count('bento_subfm="5000"') + bad.count('bento_fmatk="500"') + bad.count('bento_fmdec="129"'), 4,
                         "both cells edited")
        r = Rig(); c = r.children(r.wt_tracks(r.load(bad))[0])
        self.assertEqual(r.ours(c[0]), [-7, 1000, 0, 77, 5000, 0, 500, -9])
        self.assertEqual(r.ours(c[1]), [1, 5, 1, 0, -3, 99999, -1, 129])

    def test_a_be441_project_with_bit_depth_opens(self):
        """BE4.42 (Bit Depth removed): a project BE4.41 saved with Bit Depth set (8 bits on WT1, 4 on WT2: the BE4.41 build
        itself writes it, its own template and descriptors) opens here with every other value of ours and stock's, Bit
        Depth's bento_bits is not read (no descriptor, no token: the oscillators have no 0x1A4 property), the push after the
        load sends no 0x1A4 to the M7 (whose an_prop would not take it anyway: test_analog), and the next save drops it.
        BE4.43 (expected change): 0x1A4 is FM Atk's (token bento_fmatk), so the oscillators have a 0x1A4 property, at FM Atk's
        default 0 whatever bento_bits said, and the push sends that 0 (FM Dec its 128): the envelope plays 1, BE4.42's sound."""
        img = REF441 / "bento1.bin"
        if not img.exists():
            self.skipTest(f"no BE4.41 build at {REF441} (BE_REF441)")
        man = json.loads((REF441 / "manifest.json").read_text())
        self.assertEqual(man["version"], "4.41")
        sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
        self.assertEqual(sym["np_template_n"], 0x304, "BE4.41's template: seven keys, one pad")
        old = Rig(img=img.read_bytes(), sym=sym)
        t, w1, w2 = old.wavetable()
        for o, vals, bits in ((w1, V1, 2), (w2, V2, 3)):
            for k, v in zip(KEYS[:BE442_N], vals):                # (BE4.41's own keys but Bit Depth: BE4.42's six)
                old.set(o, k, v)
            old.set(o, BITS, bits)
            self.assertEqual(old.prop(o, BITS), bits, "control: BE4.41 stores Bit Depth")
        old.set(w1, 0xC9, 321)
        text = old.save()
        self.assertEqual((text.count('bento_bits="2"'), text.count('bento_bits="3"')), (1, 1), "control: BE4.41 wrote it")
        r = Rig()
        st = r.load(text)
        c = r.children(r.wt_tracks(st)[0])
        self.assertEqual([r.ours(o)[:BE442_N] for o in c[:2]], [V1[:BE442_N], V2[:BE442_N]], "every other value of ours")
        self.assertEqual([r.ours(o)[BE442_N:] for o in c[:2]], [[0, 128]] * 2,
                         "BE4.43: bento_bits is not read: FM Atk (0x1A4) keeps its default 0, FM Dec 128")
        self.assertEqual([k for _, k, _ in r.props_of(c[0])], STOCK_T6 + KEYS)
        self.assertEqual(r.prop(c[0], 0xC9), 321)
        r.call(S.STORE_COPY_ALL, r.live, st)
        r.props.clear()
        r.call(S.LOAD_PUSH_ALL, S.MODEL, count=200_000_000)
        self.assertGreater(len([1 for h, k, v, i in r.props if k in KEYS]), 0, "control: the push sends our keys")
        self.assertEqual({p[2] for p in r.props if p[1] == BITS}, {0}, "BE4.43: 0x1A4 reaches the M7 only as FM Atk's 0")
        again = r.save(st)
        self.assertNotIn("bento_bits", again, "dropped on BE4.42's save")
        self.assertIn('bento_subfm="1000"', again)
        self.assertIn('bento_fm12="640"', again)

    def test_a_be442_project_opens_with_the_envelope_at_its_defaults(self):
        """BE4.43: a project BE4.42 saved (the v4.42 kit's image, BE_REF442: its six keys, no envelope) opens here with its six
        values, FM Atk 0 and FM Dec 128 on both oscillators (the template's defaults: env 1, so it sounds as on BE4.42; the
        expected change is only OSC2's Sub FM next to an Analog OSC1, test_analog), the push sends them, the next save writes
        bento_fmatk="0" bento_fmdec="128"."""
        img = REF442 / "bento1.bin"
        if not img.exists():
            self.skipTest(f"no BE4.42 build at {REF442} (BE_REF442)")
        man = json.loads((REF442 / "manifest.json").read_text())
        self.assertEqual(man["version"], "4.42")
        sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
        old = Rig(img=img.read_bytes(), sym=sym)
        t, w1, w2 = old.wavetable()
        for o, vals in ((w1, V1), (w2, V2)):
            for k, v in zip(KEYS[:BE442_N], vals):
                old.set(o, k, v)
            self.assertIsNone(old.prop(o, FMDEC), "control: BE4.42 has no FM Dec")
        old.set(w1, 0xC9, 321)
        text = old.save()
        self.assertNotIn("bento_fmatk", text, "control: BE4.42 writes no envelope")
        r = Rig()
        st = r.load(text)
        c = r.children(r.wt_tracks(st)[0])
        self.assertEqual([r.ours(o) for o in c[:2]], [V1[:BE442_N] + [0, 128], V2[:BE442_N] + [0, 128]])
        r.call(S.STORE_COPY_ALL, r.live, st)
        r.props.clear()
        r.call(S.LOAD_PUSH_ALL, S.MODEL, count=200_000_000)
        self.assertEqual([(k, v) for h, k, v, i in r.props if i == 0 and k in (FMATK, FMDEC)], [(FMATK, 0), (FMDEC, 128)],
                         "the push: WT1's envelope at its defaults")
        again = r.save(st)
        self.assertEqual(again.count('bento_fmatk="0" bento_fmdec="128"'), 2, "saved with both oscillators' defaults")

    def test_stock_168_opens_it_as_a_plain_wavetable(self):
        """Stock 1.6.8 (no keys of ours in its template, no descriptors): the project opens, its oscillators have stock's
        seven keys and their values, and stock's next save drops our attributes."""
        r = Rig(img=STOCK, wrap=False)
        st = r.load(self.text)
        t = r.wt_tracks(st)[0]; c = r.children(t)
        self.assertEqual([k for _, k, _ in r.props_of(c[0])], STOCK_T6)
        self.assertEqual(r.prop(c[0], 0xC9), 321)
        again = r.save(st)
        self.assertNotIn("bento_", again)
        self.assertIn('wavepos="321"', again)

    def test_be422_opens_it_as_a_plain_wavetable(self):
        """BE4.22 (Jeff's device build): its an_load (X2) reads only bento_analog* on the track, finds none and leaves the
        track plain; stock's reader skips our cell attributes; nothing faults."""
        img = KIT422 / "01_TEST_FIRMWARE" / "bento1.bin"
        if not img.exists():
            self.skipTest(f"no BE4.22 kit at {KIT422}")
        man = json.loads((KIT422 / "manifest.json").read_text())
        self.assertEqual(man["version"], "4.22")
        sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
        r = Rig(img=img.read_bytes(), wrap=False)
        loads = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: loads.append(1), begin=sym["an_load"], end=sym["an_load"])
        r.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
        st = r.load(self.text)
        t = r.wt_tracks(st)[0]; c = r.children(t)
        self.assertGreater(len(loads), 2, "BE4.22's X2 ran for the track and its cells")
        self.assertEqual([k for _, k, _ in r.props_of(c[0])], STOCK_T6)
        an4 = S.D2_SLICE + 0xE20
        entries = struct.unpack("<32I", r.u.mem_read(an4 + 0x80, 128))
        self.assertTrue(all(entries[2 * i + 1] & 1 == 0 for i in range(16)), "no BE4.22 Analog entry was made")

    def test_be440_opens_a_p3_project(self):
        """P3 3.2 (review R14): the likeliest rollback, BE4.40 (its image and N2 wrapper, its manifest's symbols): its four
        keys load with their values, the three new tokens are skipped without a fault (it has no descriptor for them), and
        its next save keeps bento_shape but drops bento_subfm / bento_fm12 (release note, checklist item 10)."""
        img = REF440 / "bento1.bin"
        if not img.exists():
            self.skipTest(f"no BE4.40 build at {REF440} (BE_REF)")
        man = json.loads((REF440 / "manifest.json").read_text())
        self.assertEqual(man["version"], "4.40")
        sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
        self.assertEqual(sym["np_template_n"], 0x300, "BE4.40's template: its four keys, no pad")
        r = Rig(img=img.read_bytes(), sym=sym)
        st = r.load(self.text)
        c = r.children(r.wt_tracks(st)[0])
        for o, want in ((c[0], V1), (c[1], V2)):
            self.assertEqual([k for _, k, _ in r.props_of(o)], STOCK_T6 + KEYS[:BE440_N], "BE4.40's array: no P3 key added")
            self.assertEqual(r.ours(o)[:BE440_N], want[:BE440_N])
        self.assertEqual(r.prop(c[0], 0xC9), 321)
        again = r.save(st)
        self.assertIn('bento_shape="127"', again)
        for tok in ("bento_subfm", "bento_fm12", "bento_fmatk", "bento_fmdec"):     # (BE4.43: the envelope's too)
            self.assertNotIn(tok, again, f"{tok}: dropped on BE4.40's save")

    def test_be442_opens_a_be443_project(self):
        """BE4.43 review: the likeliest rollback now, BE4.42 (the v4.42 kit's image, BE_REF442, its manifest's symbols): its six
        keys load with their values, FM Atk / FM Dec are skipped without a fault (no descriptor), its push sends neither, and
        its next save drops bento_fmatk / bento_fmdec and keeps the rest. Its push also sends WT2's Sub FM (1000 here, next to
        an Analog WT1), which BE4.43 keeps and saves but does not play: BE4.42 plays it again (release note: rolling back
        brings back a hidden OSC2 Sub FM)."""
        img = REF442 / "bento1.bin"
        if not img.exists():
            self.skipTest(f"no BE4.42 build at {REF442} (BE_REF442)")
        self.assertIn("bento_fmatk", self.text); self.assertIn("bento_fmdec", self.text)
        man = json.loads((REF442 / "manifest.json").read_text())
        self.assertEqual(man["version"], "4.42")
        sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
        old = Rig(img=img.read_bytes(), sym=sym)
        st = old.load(self.text)
        c = old.children(old.wt_tracks(st)[0])
        for o, want in ((c[0], V1), (c[1], V2)):
            self.assertEqual([k for _, k, _ in old.props_of(o)], STOCK_T6 + KEYS[:BE442_N], "BE4.42's array: no envelope key")
            self.assertEqual(old.ours(o)[:BE442_N], want[:BE442_N])
            self.assertIsNone(old.prop(o, FMATK)); self.assertIsNone(old.prop(o, FMDEC))
        self.assertEqual(old.prop(c[0], 0xC9), 321)
        old.call(S.STORE_COPY_ALL, old.live, st)
        old.props.clear()
        old.call(S.LOAD_PUSH_ALL, S.MODEL, count=200_000_000)
        keys = {k for h, k, v, i in old.props}
        self.assertNotIn(FMATK, keys); self.assertNotIn(FMDEC, keys)
        self.assertIn((0x18B, V2[4], 1), {(k, v, i) for h, k, v, i in old.props},
                      "BE4.42 pushes WT2's Sub FM (hidden under an Analog WT1 on BE4.43) and plays it")
        again = old.save(st)
        for tok in ("bento_fmatk", "bento_fmdec"):
            self.assertNotIn(tok, again, f"{tok}: dropped on BE4.42's save")
        self.assertIn('bento_subfm="250"', again)
        self.assertIn('bento_subfm="1000"', again)

    def test_the_preset_reader_keeps_them(self):
        """P3 M7 / 6.1 (review R20), the reader half of the preset file: stock's patch import PATCH_READ (0x0813163C, after
        'Importing patch %s', S) reads <document><session><cell>..: the first cell's type picks the track (wavetable ->
        wttrack), every cell becomes a child through CELL_TYPE_OF, CHILD_CREATE and INST_LOAD, the same key-generic reader as
        the project's. A preset of this track in that form (the project text with the <track> element taken out) comes back
        with all six values on both oscillators. The writer half is not found offline: PROJECT_WRITE (which the preset code
        calls for 'patch.xml', 0x081299AC) writes <track> elements, which PATCH_READ does not take (M: it returns 0), so the
        preset file's writer stays device-only (I; checklist item 10)."""
        r = Rig()
        setup_wt(r)
        text = r.save()
        r.feed(text)
        self.assertFalse(r.call(S.PATCH_READ, r.text("/p/patch.xml"), r.buf(0x200), 0x200, r.store(), count=120_000_000),
                         "a project's <track> form is not a preset (pins the writer gap)")
        patch = re.sub(r'<track type="wttrack">\s*<params[^>]*/>', "", text, count=1).replace("</track>", "", 1)
        self.assertNotIn("<track", patch)
        back = r.load(patch, reader=S.PATCH_READ)
        tr = r.wt_tracks(back)
        self.assertEqual(len(tr), 1, "the first cell's type made a Wavetable track")
        c = r.children(tr[0])
        self.assertEqual([r.ours(c[0]), r.ours(c[1])], [V1, V2])
        self.assertEqual(r.prop(c[0], 0xC9), 321, "control: stock's own Position comes back the same way")


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class Hazard(unittest.TestCase):
    """P3 3.1 (review R13): the template's pad entries (type 0xFF, key 0; BE4.42: two) made type 0xFF creatable. Stock's default
    lookup matches on (type, key) (0x0812EA0C..42, S), so the pad is reachable only through an object of type 0xFF: the tests
    pin it and show that no reader path asks for that type. BE4.43: eight keys fill the count (0x304) and the pads are gone,
    so type 0xFF is not creatable, as on stock (the hazard is closed; the reader checks stay for a later pad)."""
    def test_child_create_of_the_pad_type_fails_on_stock_and_on_be443(self):
        a, b = Rig(), Rig(img=STOCK, wrap=False)
        for r in (a, b):
            r.parent = r.create(r.live, S.TYPE_WAVETABLE)
        x, y = a.child(a.live, PAD, a.parent), b.child(b.live, PAD, b.parent)
        self.assertIsNone(y, "stock: no template entry of type 0xFF, so no object")
        self.assertIsNone(x, "BE4.43: no pad entry, so no object (BE4.41 / BE4.42: one made, the hazard this class guarded)")
        self.assertEqual(SYM["np_template_n"], 0x304)

    def test_no_type_name_the_reader_accepts_is_the_pad_type(self):
        """The reader turns every <track type> / <cell type> into a type through CELL_TYPE_OF (its five callers, S): a 48-entry
        table {u32 type, name, label} (its address the literal at +0x38). Each name, run through CELL_TYPE_OF itself, gives
        its entry's type (the layout is read right), none is 0xFF, and an unknown or empty name gives 0 (skipped)."""
        r = Rig()
        tab = r.r32(S.CELL_TYPE_OF + 0x38)
        names = {}
        for i in range(48):
            ty, nm = struct.unpack("<II", bytes(r.u.mem_read(tab + 12 * i, 8)))
            self.assertTrue(nm, f"entry {i} has a name")
            names[r.e.text(nm, 32)] = ty
        self.assertEqual((names["wavetable"], names["wttrack"]), (6, S.TYPE_WAVETABLE), "control: the names this text uses")
        for name, ty in names.items():
            with self.subTest(name=name):
                self.assertNotEqual(ty, PAD)
                self.assertEqual(r.call(S.CELL_TYPE_OF, r.text(name)), ty, "CELL_TYPE_OF gives the entry's type")
        self.assertLess(max(names.values()), 0x40)
        for name in ("bogus", "", "padtype", "WAVETABLE"):
            self.assertEqual(r.call(S.CELL_TYPE_OF, r.text(name)), 0, f"{name!r}: unknown, skipped")

    def test_an_injected_or_unknown_element_creates_no_pad_type_object(self):
        """A project with an unknown cell type, an empty one, a cell without a type, an unknown element and an injected track
        loads; every object the reader creates (STORE_CREATE / CHILD_CREATE, both logged) is of a type other than 0xFF,
        and the track's real cells still load."""
        r = Rig()
        setup_wt(r)
        text = r.save()
        junk = ('<cell type="bogus"><params a="1"/></cell><cell type=""><params/></cell><cell><params/></cell>'
                '<pad type="255"><params/></pad><cell type="255"><params/></cell>')
        bad = text.replace('<cell type="wavetable">', junk + '<cell type="wavetable">', 1)
        bad = bad.replace('<track type="wttrack">', '<track type="bogustrack"><cell type="wavetable"><params/></cell></track>'
                          '<track type="wttrack">', 1)
        self.assertNotEqual(bad, text)
        made = []
        def log(uc, a, sz, _): made.append((a, uc.reg_read(UC_ARM_REG_R1)))
        for f in (S.STORE_CREATE, S.CHILD_CREATE):
            r.u.hook_add(UC_HOOK_CODE, log, begin=f, end=f)
        r.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
        st = r.load(bad)
        self.assertGreater(len(made), 5, "the reader created the track and its cells (control)")
        self.assertNotIn(PAD, [t for _, t in made], "no object of the pad type")
        self.assertEqual([r.vtype(o) for o in r.objects(st)].count(PAD), 0)
        c = r.children(r.wt_tracks(st)[0])
        self.assertEqual([r.ours(c[0]), r.ours(c[1])], [V1, V2], "the real cells still load")


# ---- BE4.46 S2 (docs/feasibility-fm-env-sources.md): FM Env 1 / 2 (source ids 3 / 6) in a Wavetable track's Mod Matrix
# Typed from stock (be446 s2 lists.py, independently of build.py): the Wavetable page's 16 sources and the registry's names.
# (Merge note for be445x: its FmTargets helpers routes / set_route / mx_rows overlap the fe_* ones below.)
REF444_S2 = Path(os.environ.get("BE_REF444", str(Path.home() / "bento" / "out" / "be444w4f")))   # BE4.44 (the rollback)
FE1, FE2 = 3, 6                    # stock's env3 / lfo3: FM Env 1 / FM Env 2 on a Wavetable voice (S1)
FE_STOCK_SRC = [0, 13, 32, 1, 2, 4, 5, 8, 14, 26, 30, 31, 9, 10, 11, 12]
FE_OUR_SRC = [0, 13, 32, 1, 2, FE1, FE2, 4, 5, 8, 14, 26, 30, 31, 9, 10, 11, 12]
FE_NAMES = {0: "[None]", 1: "Envelope 1", 2: "Envelope 2", 3: "Envelope 3", 4: "LFO 1", 5: "LFO 2", 6: "LFO 3", 8: "Mod Seq",
            9: "Auto A", 10: "Auto B", 11: "Auto C", 12: "Auto D", 13: "Velocity", 14: "Key", 26: "Mod Wheel",
            30: "Macro X", 31: "Macro Y", 32: "Pressure"}
FE_TYPES = list(range(0x15)) + [0x20, 0xFF]   # every view type the validator / name test (<= 0x13), and past them
MXV_VIEW = 0x8C000                 # the Mod Matrix view's size the harness gives (its fields up to +0x8AE04 + 60 x 16)
MXV_SEL = 0x86B5C                  # MX_EDIT / MX_BAR: the selected row (-1 none) (MX_EDIT's first load)
MXV_ROWS, MXV_KEYS, MXV_HANDLES = 0x8AC4D, 0x8AC50, 0x8ACCC
MX_SET_ROUTE_FN = 0x081347E4       # the model's 0xD1 (be445x's MX_SET_ROUTE)  # addr-ok: S2 tests (a base entry on be445x)
UI_POST_VIEW = 0x08171D94          # MX_EDIT's post of the 0xD1 (view, event)  # addr-ok: S2 tests (MX_EDIT's last call)
INST_REPLACE = 0x0813F730          # stock's instrument replace (model, kind, position)  # addr-ok: S2 tests (plan-drum-synth-track)
MX_EDIT_FN = 0x0814DBF8            # MX_EDIT (the mx group's entry is a 1.6.8 draft; hand-checked: be446 s2)  # addr-ok: S2 tests
KEY_POSITION = 0x73                # a track's position (instrument replace's key)
# instrument replace's calls the rig cannot run (each stops the emulation: voices / UI), stubbed: before the old track's
# destroy, the stop of its handle (0x0813F796); after the new track's children, its two set-ups (0x0813F858, 0x0813F860)
INST_REPLACE_STUBS = (0x08133C08, 0x0813E4B8, 0x0813D430)  # addr-ok: S2 tests (called by INST_REPLACE)


def fe_view(r, track, kind=None):
    view = r.buf(MXV_VIEW)
    r.w32(view + 0x8AC44, r.handle(track) if track else 0)
    r.u.mem_write(view + S.MXV_TYPE, bytes([S.TYPE_WAVETABLE if kind is None else kind]))
    return view


def bl_target(img, at):
    h1, h2 = struct.unpack_from("<HH", img, at - BASE)
    assert h1 & 0xF800 == 0xF000 and h2 & 0xD000 == 0xD000, f"bl at {at:#x}"
    s, j1, j2 = h1 >> 10 & 1, h2 >> 13 & 1, h2 >> 11 & 1
    off = s << 24 | (1 - (j1 ^ s)) << 23 | (1 - (j2 ^ s)) << 22 | (h1 & 0x3FF) << 12 | (h2 & 0x7FF) << 1
    return at + 4 + off - ((1 << 25) if s else 0)


def fe_rows(r, view, img):
    """MX_BUILD's Wavetable case from its list store on: stock's list and 16 into the view, then the call at MX_WT_ROWS_SITE
    (ours: mx_wt_rows; stock: the filler). (list address, its ids, [(key, handle)] of the rows)"""
    r.w32(view + S.MXV_SRC_LIST, S.MX_WT_SRC_LIST)
    r.u.mem_write(view + S.MXV_SRC_N, bytes([16]))
    r.call(bl_target(img, S.MX_WT_ROWS_SITE), view)
    lst, n = r.r32(view + S.MXV_SRC_LIST), r.u.mem_read(view + S.MXV_SRC_N, 1)[0]
    ids = list(struct.unpack(f"<{n}H", bytes(r.u.mem_read(lst, 2 * n))))
    rows = [(struct.unpack("<H", bytes(r.u.mem_read(view + MXV_KEYS + 2 * i, 2)))[0], r.r32(view + MXV_HANDLES + 4 * i))
            for i in range(r.u.mem_read(view + MXV_ROWS, 1)[0])]
    return lst, ids, rows


def fe_routes(r, o):
    """The routings on an object: its type-1 nodes {+0xC amount, +0x10 source, +0x12 key, +0x14 slot} (0x0812EE94's list)."""
    out, c = [], r.r32(o + 0x14)
    while c:
        if r.u.mem_read(c + 8, 1)[0] == 1:
            amt, src, key, slot = struct.unpack("<iHHh", bytes(r.u.mem_read(c + 0xC, 10)))
            out.append((key, src, slot, amt))
        c = r.r32(c + 4)
    return sorted(out)


def fe_set(r, h, key, src, slot, amount):
    """The model's 0xD1 as MX_EDIT posts it: {0xD1, +4 handle, +8 key | 1 << 16, +0xC source | slot << 16, +0x10 amount}."""
    r.u.mem_write(EV, struct.pack("<HHIIIi", 0xD1, 0, h, key | 1 << 16, src | slot << 16, amount))
    r.call(MX_SET_ROUTE_FN, S.MODEL, EV)


def fe_edit(r, view, row, key, value):
    """Stock's MX_EDIT (view, {+8 key, +0xC value}) on a selected row: the 0xD1 it posts (UI_POST_VIEW logged and returned)
    as (handle, key, source, slot, amount), or None."""
    posted = []
    def log(uc, a, sz, _):
        posted.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 0x14)))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    h = r.u.hook_add(UC_HOOK_CODE, log, begin=UI_POST_VIEW, end=UI_POST_VIEW)
    r.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
    r.w32(view + MXV_SEL, row)
    ev = r.buf(0x20)
    r.u.mem_write(ev, struct.pack("<IIIi", 0, 0, key, value))
    r.call(MX_EDIT_FN, view, ev)
    r.u.hook_del(h)
    if not posted:
        return None
    assert len(posted) == 1
    code, _, hh, k, src, amt = struct.unpack("<HHIIIi", posted[0])
    assert code == 0xD1 and k >> 16 in (1, 2), posted[0].hex()   # 1 a new routing, 2 an existing one's edit
    return hh, k & 0xFFFF, src & 0xFFFF, src >> 16, amt, k >> 16


def fe_post(r, ev):
    """The 0xD1 fe_edit logged, to the model as MX_EDIT posted it."""
    hh, key, src, slot, amt, mode = ev
    r.u.mem_write(EV, struct.pack("<HHIIIi", 0xD1, 0, hh, key | mode << 16, src | slot << 16, amt))
    r.call(MX_SET_ROUTE_FN, S.MODEL, EV)


@unittest.skipUnless(HAVE, "no analog group in this image (or BE_DISABLE=analog)")
class FmEnvMatrix(unittest.TestCase):
    """S2: on a Wavetable track's Mod Matrix page the source list is stock's with FM Env 1 / FM Env 2 (ids 3 / 6) right after
    Env 2, the validator keeps them and the names say "FM Env 1" / "FM Env 2"; everything else (Granular, the validator's
    other types, every other id) is stock's. Stock's MX_EDIT, row filler and model routing set run with the image's three
    sites; the project writer and reader of this build, stock 1.6.8 and BE4.44 read the routings back."""

    def test_the_sites_and_stocks_list(self):
        self.assertEqual(bl_target(IMG, S.MX_WT_ROWS_SITE), SYM["mx_wt_rows"])
        self.assertEqual(bl_target(STOCK, S.MX_WT_ROWS_SITE), S.MX_WT_ROWS_FILL, "stock's call (control)")
        for site, fn in (("MX_SRC_VALID", "mx_src_valid"), ("MX_SRC_NAME", "mx_src_name")):
            a = getattr(S, site)
            self.assertEqual(IMG[a - BASE:a - BASE + 4], bytes(KS.asm(f"b.w {SYM[fn]:#x}", a)[0]), site)
        lst = S.MX_WT_SRC_LIST
        self.assertEqual(list(struct.unpack_from("<16H", STOCK, lst - BASE)), FE_STOCK_SRC, "stock's list as typed")
        self.assertEqual(IMG[lst - BASE:lst - BASE + 32], STOCK[lst - BASE:lst - BASE + 32], "stock's list stays")
        for lit in ("MX_WT_SRC_LIT", "MX_SRC_VALID_LIT"):
            self.assertEqual(struct.unpack_from("<I", IMG, getattr(S, lit) - BASE)[0], lst, f"{lit}: Granular / types 15, 19")
        # the validator's and the name's bodies (past the replayed entry) and the filler are stock's bytes
        for lo, hi in ((S.MX_SRC_VALID + 4, S.MX_SRC_VALID + 0x60), (S.MX_SRC_NAME + 4, S.MX_SRC_NAME + 0x48),
                       (S.MX_WT_ROWS_FILL, S.MX_WT_ROWS_FILL + 0x124)):
            self.assertEqual(IMG[lo - BASE:hi - BASE], STOCK[lo - BASE:hi - BASE], f"{lo:#x}..{hi:#x}")

    def test_the_wavetable_page_lists_them_after_env_2(self):
        got = []
        for img in (IMG, STOCK):
            r = Rig(img=img, wrap=img is IMG)
            t, w1, w2 = r.wavetable()
            view = fe_view(r, t)
            lst, ids, rows = fe_rows(r, view, img)
            names = [r.e.text(r.call(S.MX_SRC_NAME, view, i), 32) for i in ids]
            end = struct.unpack("<H", bytes(r.u.mem_read(lst + 2 * len(ids), 2)))[0]
            got.append((lst, ids, names, end, rows, {r.handle(w1): "osc1", r.handle(w2): "osc2"}))
        (lst, ids, names, end, rows, h), (slst, sids, snames, _, srows, sh) = got
        self.assertEqual((slst, sids), (S.MX_WT_SRC_LIST, FE_STOCK_SRC), "stock (control)")
        self.assertEqual(snames, [FE_NAMES[i] for i in FE_STOCK_SRC])
        self.assertNotEqual(lst, S.MX_WT_SRC_LIST, "our own copy")
        self.assertEqual(ids, FE_OUR_SRC)
        self.assertEqual(end, 0, "then a 0 (None): what MX_EDIT reads at index == count")
        self.assertEqual(names, [FE_NAMES[i] for i in FE_OUR_SRC[:5]] + ["FM Env 1", "FM Env 2"] +
                         [FE_NAMES[i] for i in FE_OUR_SRC[7:]])
        role = lambda rr, hh: [(k, hh.get(x, "other")) for k, x in rr]
        self.assertEqual(role(rows, h), role(srows, sh), "the rows are stock's filler's")
        self.assertGreater(len(rows), 20)

    def test_every_other_page_type_and_id_is_stocks(self):
        """The validator and the names of every view type but Wavetable are stock's for every id 0..35, and a Wavetable
        page's other ids too: only (Wavetable, 3) and (Wavetable, 6) differ. Granular (0x13) and type 0x0F still erase them
        (stock's list, which Granular's page also shows: MX_BUILD's 0x13 case is not our site)."""
        res = []
        for img in (IMG, STOCK):
            r = Rig(img=img, wrap=img is IMG)
            out = {}
            view = fe_view(r, None)
            rec = r.buf(8)
            for kind in FE_TYPES:
                r.u.mem_write(view + S.MXV_TYPE, bytes([kind]))
                for src in range(36):
                    r.u.mem_write(rec, struct.pack("<IHH", 0x12345678, src, 0xBEEF))
                    r.call(S.MX_SRC_VALID, view, rec)
                    after = struct.unpack("<IHH", bytes(r.u.mem_read(rec, 8)))
                    out[kind, src] = (after, r.e.text(r.call(S.MX_SRC_NAME, view, src), 32))
            res.append(out)
        ours, stock = res
        self.assertEqual({k for k in stock if ours[k] != stock[k]}, {(S.TYPE_WAVETABLE, FE1), (S.TYPE_WAVETABLE, FE2)})
        for src, nm in ((FE1, "FM Env 1"), (FE2, "FM Env 2")):
            self.assertEqual(stock[S.TYPE_WAVETABLE, src], ((0x12345678, 0, 0xBEEF), FE_NAMES[src]), "stock erases the copy")
            self.assertEqual(ours[S.TYPE_WAVETABLE, src], ((0x12345678, src, 0xBEEF), nm))
            for kind in (0x0F, 0x13):
                self.assertEqual(ours[kind, src][0][1], 0, f"type {kind:#x} erases {src}")
        self.assertEqual(stock[S.TYPE_WAVETABLE, 4], ((0x12345678, 4, 0xBEEF), "LFO 1"), "a listed source is kept (control)")

    def test_the_source_encoder_picks_them(self):
        """Stock's MX_EDIT on the first row (OSC1 Pitch): the Source encoder's index i posts list[i] as the source (index 18,
        the count, reads our 0: None; 19 posts nothing). The posted 0xD1 makes the routing (MX_SET_ROUTE)."""
        r = Rig(); t, w1, w2 = r.wavetable()
        view = fe_view(r, t)
        _, ids, rows = fe_rows(r, view, IMG)
        self.assertEqual(rows[0], (3, r.handle(w1)), "row 0 is OSC1 Pitch")
        srcs = [fe_edit(r, view, 0, S.KEY_MX_FIRST, i) for i in range(20)]
        self.assertEqual([s[2] for s in srcs[:19]], FE_OUR_SRC + [0])
        self.assertIsNone(srcs[19])
        self.assertEqual({(s[0], s[1], s[3]) for s in srcs[:19]}, {(r.handle(w1), 3, 0)}, "OSC1 Pitch, slot 0")
        fe_post(r, srcs[5])
        self.assertEqual([(k, s, sl) for k, s, sl, _ in fe_routes(r, w1)], [(3, FE1, 0)])
        row = rows.index((0xC4, r.handle(w2)))                          # slot 2's source on OSC2's Level row
        e = fe_edit(r, view, row, S.KEY_MX_FIRST + 4, 6)
        self.assertEqual(e[:4], (r.handle(w2), 0xC4, FE2, 2))
        fe_post(r, e)
        self.assertEqual([(k, s, sl) for k, s, sl, _ in fe_routes(r, w2)], [(0xC4, FE2, 2)])

    def test_an_amount_edit_keeps_them_here_and_drops_them_on_stock(self):
        """With an FM Env routing in the model, an amount edit of its slot posts it back with its source here. On stock 1.6.8
        (BE4.44 has the same validator) the validator has zeroed MX_EDIT's copy, so the post carries source 0, which
        MX_SET_ROUTE takes as 'remove'. A LFO 1 routing (stock's list) is kept on both (control)."""
        for img, keeps in ((IMG, True), (STOCK, False)):
            r = Rig(img=img, wrap=img is IMG)
            t, w1, w2 = r.wavetable()
            view = fe_view(r, t)
            fe_rows(r, view, img)
            fe_set(r, r.handle(w1), 3, FE1, 0, 400)
            fe_set(r, r.handle(w1), 3, 4, 1, 200)
            e = fe_edit(r, view, 0, S.KEY_MX_FIRST + 1, 700)             # slot 0's amount
            self.assertEqual(e, (r.handle(w1), 3, FE1 if keeps else 0, 0, 700, 2))
            fe_post(r, e)
            fe_post(r, fe_edit(r, view, 0, S.KEY_MX_FIRST + 3, -300))  # slot 1's amount (LFO 1)
            want = [(3, FE1, 0, 700)] if keeps else []
            self.assertEqual(fe_routes(r, w1), sorted(want + [(3, 4, 1, -300)]), "ours" if keeps else "stock")

    def make(self, r):
        """A Wavetable track: OSC1 Pitch <- FM Env 1 (slot 0, 400), OSC2 Position <- FM Env 2 (slot 1, -250), the first
        filter's 0xCE <- FM Env 1 (slot 2, 1000), and OSC1 Position <- LFO 1 (slot 0, 300) as the control."""
        t, w1, w2 = r.wavetable()
        filt = [c for c in r.children(t) if r.vtype(c) == 10][0]
        for o, key, src, slot, amt in ((w1, 3, FE1, 0, 400), (w2, 0xC9, FE2, 1, -250), (filt, 0xCE, FE1, 2, 1000),
                                       (w1, 0xC9, 4, 0, 300)):
            fe_set(r, r.handle(o), key, src, slot, amt)
        return t

    def routed(self, r, t):
        c = r.children(t)
        filt = [o for o in c if r.vtype(o) == 10][0]
        return fe_routes(r, c[0]), fe_routes(r, c[1]), fe_routes(r, filt)

    WANT = ([(3, FE1, 0, 400), (0xC9, 4, 0, 300)], [(0xC9, FE2, 1, -250)], [(0xCE, FE1, 2, 1000)])

    def test_save_and_load(self):
        """The save format is stock's: src="env3" / "lfo3" on the destination's element; this build reads it back."""
        r = Rig(); t = self.make(r)
        self.assertEqual(self.routed(r, t), self.WANT)
        text = r.save()
        ms = re.findall(r"<modsource[^>]*>", text)
        self.assertEqual([sum(f'src="{s}"' in m for m in ms) for s in ("env3", "lfo3", "lfo1")], [2, 1, 1], ms)
        self.assertNotIn("bento_", " ".join(ms), "no token of ours")
        x = Rig(); st = x.load(text)
        self.assertEqual(self.routed(x, x.wt_tracks(st)[0]), self.WANT)

    def test_stock_168_and_be444_load_them_and_keep_them(self):
        """Stock 1.6.8 (and BE4.44, the rollback) read env3 / lfo3 as ids 3 / 6 and keep them (their M7 gives a Wavetable voice
        no source for either: S1's FmEnvSrc), and their next save writes them again: there the routing is lost only by an
        edit of its slot (test_an_amount_edit_keeps_them_here_and_drops_them_on_stock) or a new source."""
        r = Rig(); self.make(r); text = r.save()
        imgs = [("stock 1.6.8", STOCK, None)]
        if (REF444_S2 / "bento1.bin").exists():
            imgs.append(("BE4.44", (REF444_S2 / "bento1.bin").read_bytes(), REF444_S2))
        else:
            print(f"  (no BE4.44 build at {REF444_S2}: stock 1.6.8 only)")
        for what, img, ref in imgs:
            sym = None
            if ref is not None:
                man = json.loads((ref / "manifest.json").read_text())
                sym = {k[6:]: int(v, 16) for k, v in man["m4_symbols"].items() if k.startswith("synth.")}
            s = Rig(img=img, wrap=ref is not None, sym=sym)
            st = s.load(text)
            self.assertEqual(self.routed(s, s.wt_tracks(st)[0]), self.WANT, what)
            again = s.save(st)
            ms = re.findall(r"<modsource[^>]*>", again)
            self.assertEqual([sum(f'src="{x}"' in m for m in ms) for x in ("env3", "lfo3")], [2, 1], what)
            x = Rig(); self.assertEqual(self.routed(x, x.wt_tracks(x.load(again))[0]), self.WANT, f"{what}'s save, read here")

    def test_an_instrument_replace_drops_them(self):
        """Risk 4: a track whose type changes (stock's instrument replace, the menu's kinds) is a new object with new children,
        so no routing (and no FM Env one) carries over into a type whose M7 has a real Env 3 / LFO 3; a replace by a
        Wavetable track again (kind 6) gives a clean matrix too."""
        for kind in (1, 6):
            r = Rig(); t = self.make(r)
            def ret0(uc, a, sz, _): uc.reg_write(UC_ARM_REG_R0, 0); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
            for a in INST_REPLACE_STUBS:
                r.u.hook_add(UC_HOOK_CODE, ret0, begin=a, end=a)
            r.u.ctl_remove_cache(0x08000000, 0x08400000)  # addr-ok: harness
            pos, h = r.prop(t, KEY_POSITION), r.handle(t)
            self.assertEqual(len(self.routed(r, t)[0]), 2, "routed (control)")
            r.call(INST_REPLACE, S.MODEL, kind, pos, count=200_000_000)
            now = [o for o in r.tracks(r.live) if r.prop(o, KEY_POSITION) == pos]
            self.assertEqual(len(now), 1, f"kind {kind}: one track at the position")
            self.assertNotEqual(r.handle(now[0]), h, "a new object (the pool may reuse the memory)")
            self.assertEqual(r.vtype(now[0]), {1: 0x13, 6: S.TYPE_WAVETABLE}[kind])
            self.assertGreater(len(r.children(now[0])), 8, "with its new children")
            left = [n for o in [now[0]] + r.children(now[0]) for n in fe_routes(r, o)]
            self.assertEqual(left, [], f"kind {kind} (type {r.vtype(now[0]):#x}): no routing")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
