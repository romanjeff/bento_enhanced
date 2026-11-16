# SPDX-License-Identifier: GPL-3.0-or-later
"""Stock's instrument handles and the constructor hook H1, on stock's own M4 object store in the built image.

    python test_handles.py <stock bento1.bin> <m1 build dir>

Every M4 object gets its handle (+0x10) from one base constructor (OBJ_CTOR) and a counter that only goes up
(OBJ_HANDLE_CTR); a copy (NODE_CLONE, slot 3 of the node vtable) always gets a new one; every top-level object leaves
through NODE_DESTROY (slot 2). Loads go into a scratch store and are committed by a copy (STORE_COPY_ALL).

History: BE4.22-4.31 used these sites (H1 constructor, H2 clone, H3 destroy) for the Analog table AN4 (slice + 0xE20).
P1 (BE4.40) retired AN4 (Analog is a native Type); since then the three sites serve only the drums' DK4 table (a
BE_ENABLE=drums build). P4-0 (docs/plan-p4-drums.md 5, "Retired / fixed") retired this file's 13 AN4 cases with
test_inst_xml's: DK4's copy (H2), destroy (H3) and earlier-boot cases are test_drum_ui's (Xml / Handles classes). What
stays here, typed on DK4 where a table is needed: stock's handle findings, the three sites, and H1 (a new object never
inherits a stale entry; with stock's constructor it does; the wrapper keeps every register). P4-3 removes H1-H3 with DK4.

DK4 is typed here independently: our slice + 0x2000, a 32-word header (10 forgot), then 64 x {handle, word (engine |
0x100 used), v0, v1}. Skipped on a build without the drums' handle hooks (1.6.1, a default 1.6.8 build); a
BE_DISABLE=analog bisect runs only the class that checks the three sites.
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
DK4 = S.D2_SLICE + 0x2000 if BUILT else 0
DK4_MAGIC = 0x31344B44
CFG = S.D2_SLICE + 0x1E0 if BUILT else 0
HEAP = 0xC1000000                  # harness heap (registry builder, stores)  # addr-ok: harness
STRS = 0xC0F00000                  # harness strings, out cells  # addr-ok: harness
SITES = ("OBJ_CTOR", "NODE_VT_DESTROY", "NODE_VT_CLONE")


def word(img, a): return struct.unpack_from("<I", img, a - BASE)[0]


class Rig:
    def __init__(self, ctors=True):
        e = self.e = emu.Emu(IMG, init_ctors=False, base=S)
        self.u = e.u
        self.u.mem_map(0xC4000000, 0x4000000)  # addr-ok: harness
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
        self.call(S.REG_BUILD, self.reg_obj, count=40_000_000)    # stock's parameter registry
        self.call(SYM["an_tick"])                                 # the tables exist (DK4 among them)
        assert self.r32(DK4) == DK4_MAGIC, "an_tick made DK4"
        self.live = self.store()
        e.w(S.MODEL, self.live)                                   # [MODEL]: the live project's store

    def malloc(self, n): return self.call(S.M4_MALLOC, n)
    def call(self, fn, *args, count=20_000_000): return self.e.call(fn, *args, count=count)
    def r32(self, a): return self.e.r(a)
    def w32(self, a, v): self.e.w(a, v)
    def stock_site(self, name):
        """Put one H-site back to stock's bytes."""
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

    # ---- DK4 (typed here)
    def put(self, h, eng, i):
        """A DK4 entry for handle h (as an earlier boot or the drum page leaves it): engine eng, knobs 1..7."""
        self.u.mem_write(DK4 + 0x80 + 16 * i, struct.pack("<4I", h, 0x100 | eng, 0x04030201, 0x00070605))
    def entries(self):
        out = []
        for i in range(64):
            h, w, v0, v1 = struct.unpack("<4I", self.u.mem_read(DK4 + 0x80 + 16 * i, 16))
            if h or w:
                out.append((h, w))
        return out
    def state(self, h):
        """The engine of a handle's used entry, None if it has none."""
        got = [w & 15 for x, w in self.entries() if x == h and w & 0x100]
        return got[0] if got else None
    def hdr(self, k): return self.r32(DK4 + 4 * k)


@unittest.skipUnless(BUILT, "no drums handle hooks in this image (1.6.1, or a default 1.6.8 build)")
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


@unittest.skipUnless(HAVE, "no drums handle hooks in this image (1.6.1, a default 1.6.8 build, or disabled)")
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


@unittest.skipUnless(HAVE, "no drums handle hooks in this image (1.6.1, a default 1.6.8 build, or disabled)")
class NewObjects(unittest.TestCase):
    """H1: a new object never inherits a stale DK4 entry, whatever made it (create, a commit copy, a clone)."""
    def test_nothing_happens_at_a_cold_boot(self):
        """No table yet (no magic): the constructors only read the slice (the table is made by the first tick)."""
        e = emu.Emu(IMG, init_ctors=False, base=S)
        e.u.mem_write(DK4, b"\xA5" * 0x480)                           # canary-like garbage
        writes = []
        from unicorn import UC_HOOK_MEM_WRITE
        e.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: writes.append(a), begin=DK4, end=DK4 + 0x47F)
        e.ctors()
        self.assertEqual(writes, [])
        self.assertGreaterEqual(e.r(S.OBJ_HANDLE_CTR), 4, "the constructors ran")

    def test_a_stale_entry_for_the_next_handle_is_not_inherited(self):
        for how in ("create", "copy", "clone_h"):
            with self.subTest(how):
                r = Rig()
                src = r.create(r.store())                             # a plain source
                ctr = r.r32(S.OBJ_HANDLE_CTR)
                forgot = r.hdr(10)
                r.put(ctr, 1, 0); r.put(ctr + 1, 1, 1); r.put(ctr + 40, 1, 2)   # left by an earlier boot
                if how == "create": o = r.create(r.live)
                elif how == "copy": r.copy_all(r.live, self.one(r, src)); o = r.objects(r.live)[0]
                else: o = r.clone_h(r.live, r.handle(src), self.one(r, src))
                self.assertEqual(r.handle(o), ctr, "the new object got the handle the stale entry named")
                self.assertIsNone(r.state(ctr), "and no drum state with it")
                self.assertEqual(r.entries(), [], "every entry at or above the counter went")
                self.assertEqual(r.hdr(10) - forgot, 3, "forgot += 3")

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
        r.put(ctr, 1, 0)
        o = r.create(r.live)
        self.assertEqual((r.handle(o), r.state(ctr)), (ctr, 1), "stock's constructor: the stale entry is inherited")

    def test_the_constructor_wrapper_keeps_every_register(self):
        """Stock's constructor writes only r1, r2, ip and the flags; its callers may keep r3 across it."""
        for stale in (False, True):
            with self.subTest(stale=stale):
                r = Rig()
                buf = r.malloc(0x40)
                if stale: r.put(r.r32(S.OBJ_HANDLE_CTR), 1, 0)          # the slow path: an_forget clears it
                regs = [UC_ARM_REG_R0 + i for i in range(13)]
                vals = {reg: 0x11110000 + i for i, reg in enumerate(regs)}
                vals[UC_ARM_REG_R0] = buf
                got = run_keep(r, S.OBJ_CTOR, vals)
                for i, reg in enumerate(regs):
                    if i in (1, 2, 12): continue                           # stock's own scratch registers
                    self.assertEqual(got[reg], vals[reg], f"r{i}")
                self.assertEqual(r.r32(buf), S.OBJ_BASE_VT, "stock's vtable store (replayed)")
                self.assertEqual(r.r32(buf + 0x10), r.r32(S.OBJ_HANDLE_CTR) - 1, "stock's handle")
                if stale: self.assertEqual(r.entries(), [], "the slow path ran")


def run_keep(r, fn, vals):
    """Call fn with the given registers (r0..r12) and return them all after it."""
    u = r.u
    for reg, v in vals.items():
        u.reg_write(reg, v)
    u.reg_write(UC_ARM_REG_SP, r.e.sp); u.reg_write(UC_ARM_REG_LR, emu.RET | 1)
    u.emu_start(fn | 1, emu.RET, count=1_000_000)
    return {reg: u.reg_read(reg) for reg in vals}


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
