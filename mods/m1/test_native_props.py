# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog's settings as native stock properties (BE4.40, docs/plan-native-props.md N1 / N2), on stock 1.6.8's own code.

    python test_native_props.py <stock bento1.bin> <m1 build dir> [<BE4.22 kit dir>]
    (BE_REF: the BE4.40 reference build dir, default ~/bento/out/m1-be440: test_be440_opens_a_p3_project)

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
# (docs/plan-p3-fm-bits.md 2.1): Sub FM, Bit Depth, FM 1>2 after Glide, all 0
OURS = [(0x1C0, "bento_osc_type", 0), (0x1A7, "bento_shape", 48), (0x1AB, "bento_sync", 0), (0x1B7, "bento_glide", 0),
        (0x18B, "bento_subfm", 0), (0x1A4, "bento_bits", 0), (0x132, "bento_fm12", 0)]
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


# setup_wt's values of our keys (Type, Shape, Sync, Glide, Sub FM, Bit Depth, FM 1>2), each different from its default
V1 = [1, 127, 0, 77, 250, 2, 0]
V2 = [1, 5, 1, 0, 1000, 3, 640]


def setup_wt(r):
    """A Wavetable track, WT1 Analog (Shape 127, Glide 77, Sub FM 250, Bit Depth 8), WT2 Analog (Shape 5, Sync on, Sub FM
    1000, Bit Depth 4, FM 1>2 640), WT1's Position 321."""
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
        self.assertEqual((n, len(pad)), (0x304, 1), "seven keys: 0x2FC + 7 = 0x303 -> 0x304, one pad entry (P3 Q1; BE4.40's four gave 0x300)")

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
                             "Sync, Glide, Sub FM, Bit Depth, FM 1>2)")
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
        """Stock stores what the file says: the readers clamp (the M7: test_analog, P3 Bit Depth 7 / -1 -> 3 there; the page:
        test_synth_ui)."""
        bad = self.text.replace('bento_osc_type="1" bento_shape="127"', 'bento_osc_type="-7" bento_shape="1000"', 1)
        bad = bad.replace('bento_subfm="250" bento_bits="2"', 'bento_subfm="5000" bento_bits="7"', 1)
        bad = bad.replace('bento_subfm="1000" bento_bits="3" bento_fm12="640"', 'bento_subfm="-3" bento_bits="-1" bento_fm12="99999"', 1)
        self.assertEqual(bad.count("99999") + bad.count('bento_bits="7"'), 2, "both cells edited")
        r = Rig(); c = r.children(r.wt_tracks(r.load(bad))[0])
        self.assertEqual(r.ours(c[0]), [-7, 1000, 0, 77, 5000, 7, 0])
        self.assertEqual(r.ours(c[1]), [1, 5, 1, 0, -3, -1, 99999])

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
        its next save keeps bento_shape but drops bento_subfm / bento_bits / bento_fm12 (release note, checklist item 10)."""
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
        for tok in ("bento_subfm", "bento_bits", "bento_fm12"):
            self.assertNotIn(tok, again, f"{tok}: dropped on BE4.40's save")

    def test_the_preset_reader_keeps_them(self):
        """P3 M7 / 6.1 (review R20), the reader half of the preset file: stock's patch import PATCH_READ (0x0813163C, after
        'Importing patch %s', S) reads <document><session><cell>..: the first cell's type picks the track (wavetable ->
        wttrack), every cell becomes a child through CELL_TYPE_OF, CHILD_CREATE and INST_LOAD, the same key-generic reader as
        the project's. A preset of this track in that form (the project text with the <track> element taken out) comes back
        with all seven values on both oscillators. The writer half is not found offline: PROJECT_WRITE (which the preset code
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
    """P3 3.1 (review R13): the template's first pad entry (type 0xFF, key 0) makes type 0xFF creatable. Stock's default
    lookup matches on (type, key) (0x0812EA0C..42, S), so the pad is reachable only through an object of type 0xFF: the tests
    pin the hazard and show that no reader path asks for that type."""
    def test_child_create_of_the_pad_type_fails_on_stock_and_succeeds_on_p3(self):
        a, b = Rig(), Rig(img=STOCK, wrap=False)
        for r in (a, b):
            r.parent = r.create(r.live, S.TYPE_WAVETABLE)
        x, y = a.child(a.live, PAD, a.parent), b.child(b.live, PAD, b.parent)
        self.assertIsNone(y, "stock: no template entry of type 0xFF, so no object")
        self.assertIsNotNone(x, "P3: the pad entry makes one (the hazard this class guards)")
        self.assertEqual([(k, v) for v, k, _ in a.props_of(x)], [(0, 0)], "one property, key 0")

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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
