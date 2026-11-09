# SPDX-License-Identifier: GPL-3.0-or-later
"""Analog state and stock's instrument handles (BE4.22, H1-H3), on stock's own M4 object store in the built image.

    python test_handles.py <stock bento1.bin> <m1 build dir>

Every M4 object gets its handle (+0x10) from one base constructor (OBJ_CTOR) and a counter that only goes up
(OBJ_HANDLE_CTR); a copy (NODE_CLONE, slot 3 of the node vtable) always gets a new one; every top-level object leaves
through NODE_DESTROY (slot 2). Loads go into a scratch store and are committed by a copy (STORE_COPY_ALL), saves write a
snapshot copy. So the Analog table (AN4, keyed by handle) needs: H1 (the constructor drops entries from an earlier boot
before the counter reaches them: nothing new inherits Analog), H2 (a copy gets its source's state: BE4.21 lost
Analog across a load, a save and a paste), H3 (a destroyed object's entry goes).

What runs: the built image from the M4 reset's .data copy and static constructors on (emu.py), stock's store code
(STORE_INIT, STORE_POOL_INIT, STORE_CREATE, STORE_REMOVE, STORE_COPY_ALL, STORE_CLONE_H), its node constructor, clone
and destroy through the patched sites, stock's instrument XML writer and reader (X1 / X2), the parameter registry from
stock's builder. Nothing of stock's is stubbed. Each H-site is also put back to stock's bytes in the emulator: the test
that covers it must then fail (the BE4.21 behaviour), so a build without it cannot pass.

AN4 is typed here independently: our slice + 0xE20, a 32-word header (2 serial, 9 carried, 10 dropped, 11 forgot,
24 full, 25 evicted), then 16 x {handle, flags | glide << 4} (flags: 1 Analog, 2 Sync).
Skipped on a build without the analog group (1.6.1); a BE_DISABLE=analog bisect runs only the class that checks its
three sites are stock's.
"""
import json, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools" / "display_emulator"))
sys.path.insert(0, str(HERE))
import emu
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, \
    UC_ARM_REG_R12, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_SP
from bases import from_build
STOCK = Path(sys.argv[1]).read_bytes()
S = from_build(sys.argv[2], STOCK)
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
del sys.argv[1:3]
BUILT = "analog" not in MAN.get("omitted", []) and "synth.an_forget" in MAN.get("m4_symbols", {})
DISABLED = BUILT and "analog" in MAN.get("disabled", [])
HAVE = BUILT and not DISABLED
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}

BASE = 0x08040000
AN4 = S.D2_SLICE + 0xE20 if BUILT else 0
AN4_MAGIC = 0x32344E41
CFG = S.D2_SLICE + 0x1E0 if BUILT else 0
HEAP = 0xC1000000                  # harness heap (registry builder, stores)  # addr-ok: harness
STRS = 0xC0F00000                  # harness strings, out cells  # addr-ok: harness
DOC = 0xC0E00000                   # the XML document  # addr-ok: harness
SITES = ("OBJ_CTOR", "NODE_VT_DESTROY", "NODE_VT_CLONE")


def word(img, a): return struct.unpack_from("<I", img, a - BASE)[0]


class Rig:
    def __init__(self, prior=None, ctors=True):
        """prior: AN4 entries [(handle, flags, glide)] a previous boot left in the slice (the slice survives a reset)."""
        e = self.e = emu.Emu(IMG, init_ctors=False, base=S)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness
        if prior is not None:
            self.u.mem_write(AN4, struct.pack("<I", AN4_MAGIC) + bytes(0xFC))
            for i, (h, f, g) in enumerate(prior):
                self.put(h, f, g, i)
        self.ctor_handles, self.stores = [], []
        self.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: self.ctor_handles.append(e.r(S.OBJ_HANDLE_CTR)),
                        begin=S.OBJ_CTOR + 4, end=S.OBJ_CTOR + 4)
        if ctors:
            e.ctors()
        e.w(CFG, 0x31474144); e.w(CFG + 12, 1)                    # CFG: boot generation 1 (the black box's)
        heap = [HEAP]
        def malloc(uc, a, sz, _):
            n = uc.reg_read(UC_ARM_REG_R0); p = heap[0]; heap[0] += (n + 7) & ~7
            uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
        self.reg_obj = self.malloc(0x400)
        self.call(S.REG_BUILD, self.reg_obj, count=40_000_000)    # stock's parameter registry (the reader matches by it)
        self.call(SYM["an_tick"])                                 # the table exists (an4())
        self.live = self.store()
        e.w(S.MODEL, self.live)                                   # [MODEL]: the live project's store
        self.u.mem_write(STRS, b"instrument\0params\0")

    def malloc(self, n): return self.call(S.M4_MALLOC, n)
    def call(self, fn, *args, count=20_000_000): return self.e.call(fn, *args, count=count)
    def r32(self, a): return self.e.r(a)
    def w32(self, a, v): self.e.w(a, v)
    def stock_site(self, name):
        """Put one H-site back to stock's bytes (as BE4.21 had it)."""
        a = getattr(S, name)
        self.u.mem_write(a, STOCK[a - BASE:a - BASE + 4])

    # ---- stock's object store
    def store(self):
        s = self.malloc(0x6C)
        self.call(S.STORE_INIT, s); self.call(S.STORE_POOL_INIT, s, 0x40000)
        self.stores.append(s)
        return s
    def create(self, store, kind=None):
        self.w32(STRS + 0x40, 0)
        ok = self.call(S.STORE_CREATE, store, S.TYPE_WAVETABLE if kind is None else kind, STRS + 0x40)
        o = self.r32(STRS + 0x40)
        assert ok and o, "stock made the object"
        return o
    def objects(self, store): return [self.r32(store + 4 * i) for i in range(self.r32(store + 0x30))]
    def handle(self, o): return self.r32(o + 0x10)
    def handles(self, store): return [self.handle(o) for o in self.objects(store)]
    def copy_all(self, dst, src): self.call(S.STORE_COPY_ALL, dst, src, count=40_000_000)
    def clone_h(self, dst, h, src):
        """STORE_CLONE_H: its out cell gets the copy's handle; the copy (object) from dst's list."""
        self.w32(STRS + 0x44, 0)
        self.call(S.STORE_CLONE_H, dst, h, src, STRS + 0x44)
        nh = self.r32(STRS + 0x44)
        got = [o for o in self.objects(dst) if self.handle(o) == nh]
        return got[0] if nh and got else 0
    def remove(self, store, h): self.call(S.STORE_REMOVE, store, h)

    # ---- AN4 (typed here)
    def put(self, h, flags, glide=0, i=0):
        self.u.mem_write(AN4 + 0x80 + 8 * i, struct.pack("<2I", h, flags | glide << 4))
    def entries(self):
        return [(h, w & 15, (w >> 4) & 0x3FF) for h, w in struct.iter_unpack("<2I", bytes(self.u.mem_read(AN4 + 0x80, 0x80)))]
    def state(self, h):
        """(flags, glide) of a handle, None if it has no entry."""
        got = [(f, g) for x, f, g in self.entries() if x == h and f & 1]
        return got[0] if got else None
    def hdr(self, k): return self.r32(AN4 + 4 * k)
    def analog(self, o, flags=1, glide=0):
        """Make object o Analog the way the menu or a load leaves it (an entry for its handle)."""
        free = [i for i, (x, f, g) in enumerate(self.entries()) if not f & 1]
        self.put(self.handle(o), flags, glide, free[0])

    # ---- stock's instrument XML (as test_inst_xml)
    def writer(self):
        for a in range(S.INST_SAVE_SITE & ~1, S.INST_SAVE_SITE - 0x200, -2):
            if IMG[a - BASE:a - BASE + 6] == bytes.fromhex("2de9f04f91b0"):
                return a
        raise AssertionError("no writer prologue before INST_SAVE_SITE")
    def save(self, o):
        """Stock's instrument writer on object o: (its element, the params element)."""
        self.u.mem_write(DOC, bytes(0x1000))
        self.call(S.XML_DOC_INIT, DOC, 1, 0)
        parent = self.call(S.XML_NEW_ELEMENT, DOC, STRS); self.call(S.XML_APPEND_CHILD, DOC, parent)
        self.call(self.writer(), DOC, parent, o, count=40_000_000)
        return parent, self.call(S.XML_CHILD, parent, STRS + 11)
    def attrs(self, params):
        out = []
        for n in ("bento_analog", "bento_analog_sync", "bento_analog_glide"):
            self.u.mem_write(STRS + 0x100, n.encode() + b"\0")
            a = self.call(S.XML_FIND, params, STRS + 0x100)
            if not a:
                out.append(None); continue
            self.w32(STRS + 0x80, 0)
            out.append(self.r32(STRS + 0x80) if self.call(S.XML_GET_U32, a, STRS + 0x80) == 0 else "?")
        return out


@unittest.skipUnless(BUILT, "no analog group in this image (1.6.1, or left out)")
class Sites(unittest.TestCase):
    @unittest.skipUnless(HAVE, "analog disabled in this build")
    def test_each_site_goes_to_our_code(self):
        self.assertEqual(IMG[S.OBJ_CTOR - BASE:S.OBJ_CTOR - BASE + 4], emu_asm_bw(S.OBJ_CTOR, SYM["wrap_ctor"]))
        self.assertEqual(word(IMG, S.NODE_VT_DESTROY), SYM["an_destroy"] | 1)
        self.assertEqual(word(IMG, S.NODE_VT_CLONE), SYM["an_clone"] | 1)
        self.assertEqual((word(STOCK, S.NODE_VT_DESTROY), word(STOCK, S.NODE_VT_CLONE)), (S.NODE_DESTROY | 1, S.NODE_CLONE | 1))

    @unittest.skipUnless(DISABLED, "only a BE_DISABLE=analog bisect")
    def test_a_bisect_leaves_the_three_sites_stock(self):
        """S6c-2: the three sites are shared with the drums (their pads' knobs): stock's only when both groups are off."""
        drums_on = "drums" not in MAN.get("omitted", []) and "drums" not in MAN.get("disabled", [])
        for n in SITES:
            a = getattr(S, n)
            if drums_on:
                self.assertNotEqual(IMG[a - BASE:a - BASE + 4], STOCK[a - BASE:a - BASE + 4], f"{n}: kept for the drums")
            else:
                self.assertEqual(IMG[a - BASE:a - BASE + 4], STOCK[a - BASE:a - BASE + 4], n)


def emu_asm_bw(at, to):
    """b.w encoding (T4), typed here."""
    off = to - (at + 4)
    s = (off >> 24) & 1; i1 = (off >> 23) & 1; i2 = (off >> 22) & 1
    j1, j2 = (~i1 ^ s) & 1, (~i2 ^ s) & 1
    hi = 0xF000 | s << 10 | (off >> 12) & 0x3FF
    lo = 0x9000 | j1 << 13 | j2 << 11 | (off >> 1) & 0x7FF
    return struct.pack("<HH", hi, lo)


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, left out or disabled)")
class Stock(unittest.TestCase):
    """What stock does with handles (the investigation's findings, re-run on every build)."""
    def test_handles_only_go_up_and_copies_get_new_ones(self):
        r = Rig()
        self.assertEqual(r.r32(S.OBJ_HANDLE_CTR), 4, "the M4's static constructors took handles 1..3")
        self.assertEqual(r.ctor_handles[:3], [1, 2, 3])
        scratch = r.store()
        objs = [r.create(scratch) for _ in range(3)]
        hs = [r.handle(o) for o in objs]
        r.copy_all(r.live, scratch)
        live = r.handles(r.live)
        self.assertEqual(len(live), 3)
        self.assertTrue(min(live) > max(hs), f"a load commit's copies have new handles: {hs} -> {live}")
        addrs = r.objects(r.live)
        r.copy_all(r.live, scratch)                                   # a second commit: destroy all, copy again
        self.assertEqual(r.objects(r.live), addrs, "stock's pool hands the same addresses back")
        again = r.handles(r.live)
        self.assertTrue(min(again) > max(live), f"but never the same handles: {live} -> {again}")
        h = again[0]
        r.remove(r.live, h)
        self.assertGreater(r.handle(r.create(r.live)), max(again), "remove + create (the menu's replace): a new handle")
        self.assertEqual(sorted(set(r.ctor_handles)), sorted(r.ctor_handles), "no handle given out twice")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, left out or disabled)")
class Copies(unittest.TestCase):
    """H2: a project load (scratch store, then STORE_COPY_ALL into the live store), a save (a snapshot copy, then the
    writer), a preset apply / paste (STORE_CLONE_H)."""
    def round_trip(self, r):
        """An Analog track's XML (stock's writer), loaded as stock loads a project: the reader into a scratch object,
        then the commit copy into the live store; then saved as stock saves: a snapshot copy, then the writer."""
        src = r.create(r.store())
        r.analog(src, 3, 370)                                         # Analog, Sync, Glide 370
        parent, params = r.save(src)
        self.assertEqual(r.attrs(params), [1, 1, 370], "stock's writer on a real node writes our attributes")
        r.u.mem_write(AN4 + 0x80, bytes(0x80))                        # a new session: nothing in the table
        scratch = r.store()
        obj = r.create(scratch)
        r.call(S.INST_LOAD, parent, obj, count=40_000_000)            # stock's reader (X2 first)
        self.assertEqual(r.state(r.handle(obj)), (3, 370), "the reader marks the scratch object")
        r.copy_all(r.live, scratch)                                   # the load commit
        live = r.objects(r.live)
        snap = r.store()
        r.copy_all(snap, r.live)                                      # the save snapshot
        return live[0], r.objects(snap)[0]

    def test_a_loaded_analog_track_is_analog_and_saves_again(self):
        r = Rig()
        live, snap = self.round_trip(r)
        self.assertEqual(r.state(r.handle(live)), (3, 370), "the live track (a copy with a new handle) is Analog")
        self.assertEqual(r.attrs(r.save(snap)[1]), [1, 1, 370], "and the save writes it")
        self.assertGreaterEqual(r.hdr(9), 2, "carried")

    def test_without_h2_the_load_loses_analog(self):
        """BE4.21 (the clone slot stock's): the live copy has no entry, the save writes nothing of ours."""
        r = Rig(); r.stock_site("NODE_VT_CLONE")
        live, snap = self.round_trip(r)
        self.assertIsNone(r.state(r.handle(live)))
        self.assertEqual(r.attrs(r.save(snap)[1]), [None] * 3)

    def test_paste_carries_analog_and_a_plain_source_makes_no_entry(self):
        r = Rig()
        a, b = r.create(r.live), r.create(r.live)
        r.analog(a, 1, 900)
        clip = r.store()
        ca = r.clone_h(clip, r.handle(a), r.live)                     # copy
        cb = r.clone_h(clip, r.handle(b), r.live)
        pa = r.clone_h(r.live, r.handle(ca), clip)                    # paste
        pb = r.clone_h(r.live, r.handle(cb), clip)
        self.assertTrue(all((ca, cb, pa, pb)))
        self.assertEqual([r.state(r.handle(o)) for o in (ca, pa)], [(1, 900)] * 2)
        self.assertEqual([r.state(r.handle(o)) for o in (b, cb, pb)], [None] * 3)

    def test_only_a_wavetable_saves_our_attributes(self):
        """In place of BE4.21's sweep (which retired an entry whose object was no longer a Wavetable): the writer
        asks the object's own type."""
        r = Rig()
        o = r.create(r.live, S.TYPE_ONESHOT)
        r.analog(o, 3, 5)
        self.assertEqual(r.attrs(r.save(o)[1]), [None] * 3)

    def test_eight_analog_tracks_survive_a_load_and_a_save(self):
        """The table's 16 entries: 8 live tracks and the 8 copies of a commit or a snapshot in progress at most;
        a full table gives up the oldest copy (the load's scratch), never a live track."""
        r = Rig()
        scratch = r.store()
        objs = [r.create(scratch) for _ in range(8)]
        for i, o in enumerate(objs):
            r.analog(o, 1 | (i & 1) << 1, 10 * i)
        r.copy_all(r.live, scratch)                                   # load commit: 8 scratch + 8 live entries
        live = r.objects(r.live)
        self.assertEqual([r.state(r.handle(o)) for o in live], [(1 | (i & 1) << 1, 10 * i) for i in range(8)])
        for _ in range(2):                                            # two saves: each snapshot needs 8 more
            snap = r.store()
            r.copy_all(snap, r.live)
            self.assertEqual([r.attrs(r.save(o)[1]) for o in r.objects(snap)], [[1, i & 1, 10 * i] for i in range(8)])
        self.assertEqual([r.state(r.handle(o)) for o in live], [(1 | (i & 1) << 1, 10 * i) for i in range(8)],
                         "every live track kept its entry")
        self.assertEqual(r.hdr(24), 0, "full=0")
        self.assertGreaterEqual(r.hdr(25), 8, "the scratch copies were given up")


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, left out or disabled)")
class NewObjects(unittest.TestCase):
    """H1 (decision 7): a new object never inherits Analog state, whatever made it."""
    def test_entries_from_an_earlier_boot_are_gone_at_boot(self):
        """The slice survives an M4 reset, the counter does not: the first static constructor drops them all."""
        r = Rig(prior=[(1, 1, 0), (2, 3, 5), (5, 1, 0), (0x500, 1, 0)])
        self.assertEqual([e for e in r.entries() if e[0]], [])
        self.assertEqual(r.hdr(11), 4, "forgot=4")

    def test_nothing_happens_at_a_cold_boot(self):
        """No table yet (no magic): the constructors only read the slice (the table is made by the first tick)."""
        e = emu.Emu(IMG, init_ctors=False, base=S)
        e.u.mem_write(AN4, b"\xA5" * 0x100)                           # canary-like garbage
        writes = []
        from unicorn import UC_HOOK_MEM_WRITE
        e.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: writes.append(a), begin=AN4, end=AN4 + 0xFF)
        e.ctors()
        self.assertEqual(writes, [])
        self.assertGreaterEqual(e.r(S.OBJ_HANDLE_CTR), 4, "the constructors ran")

    def test_a_stale_entry_for_the_next_handle_is_not_inherited(self):
        for how in ("create", "copy", "clone_h"):
            with self.subTest(how):
                r = Rig()
                src = r.create(r.store())                             # a plain source
                ctr = r.r32(S.OBJ_HANDLE_CTR)
                r.put(ctr, 3, 400, 0); r.put(ctr + 1, 1, 0, 1); r.put(ctr + 40, 1, 0, 2)   # left by an earlier boot
                if how == "create": o = r.create(r.live)
                elif how == "copy": r.copy_all(r.live, self.one(r, src)); o = r.objects(r.live)[0]
                else: o = r.clone_h(r.live, r.handle(src), self.one(r, src))
                self.assertEqual(r.handle(o), ctr, "the new object got the handle the stale entry named")
                self.assertIsNone(r.state(ctr), "and no Analog state with it")
                self.assertEqual([e for e in r.entries() if e[0]], [], "every entry at or above the counter went")

    @staticmethod
    def one(r, src):
        """src's store (a store of its own)."""
        for s in r.stores:
            if src in r.objects(s):
                return s
        raise AssertionError("src's store")

    def test_without_h1_the_new_object_inherits(self):
        r = Rig(); r.stock_site("OBJ_CTOR")
        ctr = r.r32(S.OBJ_HANDLE_CTR)
        r.put(ctr, 3, 400, 0)
        o = r.create(r.live)
        self.assertEqual((r.handle(o), r.state(ctr)), (ctr, (3, 400)), "BE4.21: the stale entry is inherited")

    def test_the_constructor_wrapper_keeps_every_register(self):
        """Stock's constructor writes only r1, r2, ip and the flags; its callers may keep r3 across it."""
        for stale in (False, True):
            with self.subTest(stale=stale):
                r = Rig()
                buf = r.malloc(0x40)
                if stale: r.put(r.r32(S.OBJ_HANDLE_CTR), 1, 0, 0)        # the slow path: an_forget clears it
                regs = [UC_ARM_REG_R0 + i for i in range(13)]
                vals = {reg: 0x11110000 + i for i, reg in enumerate(regs)}
                vals[UC_ARM_REG_R0] = buf
                got = run_keep(r, S.OBJ_CTOR, vals)
                for i, reg in enumerate(regs):
                    if i in (1, 2, 12): continue                           # stock's own scratch registers
                    self.assertEqual(got[reg], vals[reg], f"r{i}")
                self.assertEqual(r.r32(buf), S.OBJ_BASE_VT, "stock's vtable store (replayed)")
                self.assertEqual(r.r32(buf + 0x10), r.r32(S.OBJ_HANDLE_CTR) - 1, "stock's handle")


def run_keep(r, fn, vals):
    """Call fn with the given registers (r0..r12) and return them all after it."""
    u = r.u
    for reg, v in vals.items():
        u.reg_write(reg, v)
    u.reg_write(UC_ARM_REG_SP, r.e.sp); u.reg_write(UC_ARM_REG_LR, emu.RET | 1)
    u.emu_start(fn | 1, emu.RET, count=1_000_000)
    return {reg: u.reg_read(reg) for reg in vals}


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, left out or disabled)")
class Destroy(unittest.TestCase):
    """H3: STORE_REMOVE (the instrument menu's replace), the store clear before a load commit, a failed create."""
    def test_remove_and_a_commit_free_the_entries(self):
        r = Rig()
        a, b = r.create(r.live), r.create(r.live)
        r.analog(a, 1); r.analog(b, 3, 20)
        ha = r.handle(a)
        r.remove(r.live, ha)
        self.assertIsNone(r.state(ha))
        self.assertEqual(r.state(r.handle(b)), (3, 20))
        self.assertEqual(r.hdr(10), 1, "dropped=1")
        r.copy_all(r.live, r.store())                                 # a commit of an empty project clears the live store
        self.assertEqual([e for e in r.entries() if e[0]], [])
        self.assertEqual(r.hdr(10), 2)

    def test_without_h3_the_entry_stays(self):
        r = Rig(); r.stock_site("NODE_VT_DESTROY")
        a = r.create(r.live); h = r.handle(a)
        r.analog(a, 1)
        r.remove(r.live, h)
        self.assertEqual(r.state(h), (1, 0))

    def test_a_thirteenth_object_is_destroyed_by_stock_and_its_entry_with_it(self):
        """STORE_CREATE destroys an object the store has no room for (12 top-level objects): through slot 2."""
        r = Rig()
        for _ in range(12):
            r.create(r.live)
        ctr = r.r32(S.OBJ_HANDLE_CTR)
        r.put(ctr, 1, 0, 0)                                           # stale: H1 drops it first
        drops = []
        r.u.hook_add(UC_HOOK_CODE, lambda uc, a, sz, _: drops.append(r.r32(uc.reg_read(UC_ARM_REG_R0) + 0x10)),
                     begin=SYM["an_destroy"], end=SYM["an_destroy"])
        r.w32(STRS + 0x40, 0)
        self.assertEqual(r.call(S.STORE_CREATE, r.live, S.TYPE_WAVETABLE, STRS + 0x40), 0, "stock refuses the 13th")
        self.assertEqual(r.r32(S.OBJ_HANDLE_CTR), ctr + 1, "after making it")
        self.assertEqual(drops, [ctr], "and destroying it through slot 2 (our H3)")
        self.assertEqual([e for e in r.entries() if e[0]], [])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
