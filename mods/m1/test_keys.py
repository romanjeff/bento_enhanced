# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry keys on each base, from stock's own registry builder (S4+S5).

    python test_keys.py <stock bento1.bin> <m1 build dir>

1.6.8 renumbered the parameter registry (498 entries; keys 0x13C..0x1D8 down 2, 0x1D9.. down 1). Our code
names every stock key it uses through the base table (S.KEY_*), which expected_values.py checks against a
second record, but both come from reading the disassembly. Here stock's registry builder (REG_BUILD, the
code that allocates REG_KEYS x 32 B and fills it) runs in emulation on the base's own image, and each key
we use must carry the parameter we mean: the same title (and type / choice count) on every base. Then the
11 keys claim() (m4_step.c) takes at runtime, top down from REG_KEYS - 1, must be keys no stock entry has.
"""
import struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
import json
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
STOCK = Path(sys.argv[1]).read_bytes()
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
del sys.argv[1:3]
# S6a (T6): the synth keys. The plan's allow-list (docs/plan-synths-168.md section 5), typed here: Analog (in this
# image when the group is), the drums' ten (S6c) and Track FX's nineteen (later) must be clean too.
ANALOG = "analog" not in MAN.get("omitted", []) and "analog" not in MAN.get("disabled", []) and "synth.np_keys" in MAN.get("m4_symbols", {})
# (Track FX's 19 are picked and reviewed in its own sprint; 0x19A, for one, is stock's Freeze.)
# S6c-2: the drums claim seven contiguous keys of the plan's ten (0x1AE..0x1B4, one range test in the X3 wrapper); outside
# 0x18C..0x1B6 no clean key is left but Track FX's 0x132 / 0x18B, which the Analog FM plan takes.
# BE4.40: Analog's four native oscillator keys (docs/plan-native-props.md 3.1): Type, Shape, Sync, Glide.
ALLOW = {"analog": (0x1C0, 0x1A7, 0x1AB, 0x1B7), "drums": (0x1AE, 0x1AF, 0x1B0, 0x1B1, 0x1B2, 0x1B3, 0x1B4)}

BASE = 0x08040000
SENT = 0x20000100            # harness return pad  # addr-ok: harness
STACK = 0x2001F000           # harness stack (DTCM)  # addr-ok: harness
OBJ = 0x24070000             # the builder's argument (unused by what we read)  # addr-ok: harness
HEAP = 0xC0100000            # harness heap for the builder's allocations  # addr-ok: harness
# key name -> (title, type, choices), as on 1.6.1 (v3.72 was built on these); None: not a registry entry
WANT = {
    "KEY_DRUM_STEP_PARAM": ("Prob", 7, 100), "KEY_ENV_A": ("Attack", 10, 0), "KEY_ENV_D": ("Decay", 10, 0),
    "KEY_ENV_S": ("Sustain", 10, 0), "KEY_ENV_R": ("Release", 10, 0), "KEY_LFO_SHAPE": ("LFO Shape", 7, 9),
    "KEY_LFO_RATE": ("LFO Rate", 10, 0), "KEY_LFO_DEPTH": ("LFO Depth", 10, 0), "KEY_LFO_SRATE": ("LFO Rate", 7, 15),
    "KEY_LOOP_ROW": ("Group Index", 1, 0), "KEY_SEQ_ROW": ("Disp Pos", 1, 0), "KEY_SEL_CLIP": ("Selected Cell Pos", 1, 0),
    "KEY_MUTE": ("Mute", 6, 0), "KEY_SOLO": ("Solo", 6, 0), "KEY_SCALE": ("Scale", 7, 23), "KEY_ROOT": ("Root Note", 7, 12),
    "KEY_MX_FIRST": ("Source 1", 4, 0), "KEY_MX_LAST": ("Amount 3", 11, 0),
    "KEY_SEL_TRACK": None,   # a model property (GET_PROPERTY(model + 4, key)), not in the registry on either base
}
BARE = {0x0F: ("MIDIOutPrt", 7, 4), 0x10: ("MIDI OutCh", 7, 17)}   # m4_step.c at_publish reads these two directly


def registry(image=STOCK, after=None, entry=None):
    """{key: (title, type, choices)} as stock's builder (or entry: model init's call of it, BE4.40's N2 wrapper) leaves the
    registry (then after(u, registry), if given)."""
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, image[:0x200000 - (BASE - 0x08000000)])
    for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xC0000000, 0x1000000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    u.mem_write(SENT, b"\xfe\xe7")
    heap = [HEAP]
    def malloc(uc, a, sz, _):                                  # stock's M4 malloc: a fresh block, 8-aligned
        n = uc.reg_read(UC_ARM_REG_R0); p = heap[0]; heap[0] += (n + 7) & ~7
        uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
    u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1); u.reg_write(UC_ARM_REG_R0, OBJ)
    u.emu_start((entry or S.REG_BUILD) | 1, SENT, count=20_000_000)
    r = struct.unpack("<I", u.mem_read(S.REGISTRY, 4))[0]
    if after:
        after(u, r)
    out = {}
    for k in range(S.REG_KEYS):
        e = bytes(u.mem_read(r + k * 32, 32))
        key, typ = struct.unpack_from("<HB", e)
        if key:
            title = bytes(u.mem_read(struct.unpack_from("<I", e, 4)[0], 48)).split(b"\0")[0].decode("latin1")
            out[k] = (key, title, typ, struct.unpack_from("<I", e, 12)[0])
    return r, heap[0] - HEAP, out


class Registry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base, cls.used, cls.reg = registry()

    def test_size_is_reg_keys(self):
        """The builder's first allocation is the registry, REG_KEYS x 32 B, and its last entry is key REG_KEYS - 1."""
        self.assertEqual(self.base, HEAP)
        self.assertEqual(max(self.reg), S.REG_KEYS - 1)
        self.assertTrue(all(k == e[0] for k, e in self.reg.items()), "every entry sits at its own key")

    def test_every_key_we_name_is_the_parameter_we_mean(self):
        for name, want in WANT.items():
            with self.subTest(key=name):
                self.assertTrue(S.has(name), "reviewed, with a value, on this base")
                got = self.reg.get(getattr(S, name))
                self.assertEqual(got and got[1:], want, hex(getattr(S, name)))

    def test_bare_midi_out_keys(self):
        for k, want in BARE.items():
            self.assertEqual(self.reg[k][1:], want, hex(k))

    def test_claim_takes_keys_no_stock_entry_has(self):
        """claim(): 11 empty keys from REG_KEYS - 1 down, all >= 0x100 (m4_step.c); they must exist and be free."""
        free = [k for k in range(S.REG_KEYS - 1, 0xFF, -1) if k not in self.reg][:11]
        self.assertEqual(len(free), 11)
        self.assertTrue(all(0x100 <= k < S.REG_KEYS for k in free))
        print(f"\n  claim() on {S.label}: {', '.join(hex(k) for k in free)}", file=sys.stderr)


def immediates(img, ranges, wanted):
    """{value: [addresses]} of the Thumb-2 instructions in the ranges that load or compare one of `wanted` as a number:
    movw, mov.w and cmp.w (a key above 0xFF needs one of them; add / sub / and with the same number are struct offsets
    and masks: a scan of all forms finds 40 of those for these keys, none a key). Every halfword is decoded as an
    instruction start (conservative: data can only add hits)."""
    def expand(i, imm3, imm8):                       # ThumbExpandImm
        x = (i << 4) | (imm3 << 1) | (imm8 >> 7)
        if x < 8:
            return [imm8, imm8 << 16 | imm8, imm8 << 24 | imm8 << 8, imm8 * 0x01010101][x >> 1]
        v = 0x80 | (imm8 & 0x7F)
        return ((v >> x) | (v << (32 - x))) & 0xFFFFFFFF
    hits = {v: [] for v in wanted}
    for lo, hi in ranges:
        for k in range(lo - BASE, hi - BASE - 4, 2):
            h1, h2 = struct.unpack_from("<HH", img, k)
            if h2 & 0x8000 or (h1 & 0xF800) != 0xF000:
                continue
            i, imm3, imm8 = (h1 >> 10) & 1, (h2 >> 12) & 7, h2 & 0xFF
            if (h1 & 0xFBF0) == 0xF240:
                v = (h1 & 15) << 12 | i << 11 | imm3 << 8 | imm8                                 # movw
            elif (h1 & 0xFA00) == 0xF000 and (((h1 >> 5) & 15, h1 & 15) == (2, 15) or ((h1 >> 5) & 15, (h2 >> 8) & 15) == (13, 15)):
                v = expand(i, imm3, imm8)                                                       # mov.w / cmp.w
            else:
                continue
            if v in hits:
                hits[v].append(BASE + k)
    return hits


def key_ranges(img, ranges):
    """Stock's key-range tests in the ranges: (address, K, N, default, table) for each `sub(.w) rX, rY, #K` followed
    within 3 instructions by `cmp rX, #N` (keys K..K+N, K in 0x100..0x1FF), with the out-of-range branch's target
    (bhi) and, when the case is picked from a word table (`adr rT, #i; ldr.w pc, [rT, rX, lsl #2]`), the table."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    from capstone.arm import ARM_OP_IMM, ARM_OP_REG
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS); md.detail = True
    out = []
    for lo, hi in ranges:
        for a in range(lo, hi - 16, 2):
            h1 = struct.unpack_from("<H", img, a - BASE)[0]
            if (h1 & 0xFBE0) != 0xF1A0 and (h1 & 0xFBF0) != 0xF2A0:   # sub(s).w / subw with a 12-bit or modified immediate
                continue
            ins = list(md.disasm(img[a - BASE:a - BASE + 24], a, 6))
            if not ins or not ins[0].mnemonic.startswith("sub") or len(ins[0].operands) != 3 or ins[0].operands[2].type != ARM_OP_IMM:
                continue
            k = ins[0].operands[2].imm
            if not 0x100 <= k <= 0x1FF:
                continue
            dst = ins[0].operands[0].reg
            for j, c in enumerate(ins[1:4], 1):
                if c.mnemonic.startswith("cmp") and c.operands[0].type == ARM_OP_REG and c.operands[0].reg == dst \
                        and c.operands[1].type == ARM_OP_IMM:
                    default = table = fall = None
                    rest = ins[j + 1:]
                    if rest and rest[0].mnemonic.startswith("bhi"):
                        default = rest[0].operands[0].imm
                        if len(rest) > 2 and rest[1].mnemonic == "adr" and rest[2].mnemonic == "ldr.w" and rest[2].op_str.startswith("pc,"):
                            table = ((rest[1].address + 4) & ~3) + rest[1].operands[1].imm
                        elif len(rest) > 1:
                            fall = rest[1].address
                    out.append([a, k, c.operands[1].imm, default, table, fall])
                    break
    at = {e[0]: e for e in out}                   # a test that falls into the same test again (0x08132E80 -> E88): its table
    for e in out:
        f = at.get(e[5])
        if e[4] is None and f and f[1:4] == e[1:4]:
            e[4] = f[4]
    return [tuple(e[:5]) for e in out]


@unittest.skipUnless(S.module == "stock_168", "the synth keys are 1.6.8's (the group is left out on 1.6.1)")
class SynthKeys(unittest.TestCase):
    """T6: the reviewed allow-list against stock's registry and code; BE4.40's N2 (np_keys, m4_synth.c) on top of it."""
    @classmethod
    def setUpClass(cls):
        cls.base, cls.used, cls.reg = registry()
        cls.claimed = [k for k in range(S.REG_KEYS - 1, 0xFF, -1) if k not in cls.reg][:11]

    def test_the_table_names_the_allow_list(self):
        self.assertEqual((S.KEY_OSC_TYPE, S.KEY_OSC_SHAPE, S.KEY_OSC_SYNC, S.KEY_OSC_GLIDE), ALLOW["analog"])
        self.assertEqual(tuple(range(S.KEY_DR_FIRST, S.KEY_DR_FIRST + 7)), ALLOW["drums"])

    def test_every_listed_key_is_empty_and_not_claimed(self):
        keys = [k for g in ALLOW.values() for k in g] + list(FM_NEXT)
        self.assertEqual(len(set(keys)), 14)
        for k in keys:
            with self.subTest(key=hex(k)):
                self.assertNotIn(k, self.reg, "empty after stock's builder")
                self.assertNotIn(k, self.claimed, "not one of claim()'s 11")
                self.assertLess(k, min(self.claimed), "below claim()'s lowest: the two claims never meet")

    def test_no_listed_key_is_an_immediate_in_either_core(self):
        """A key stock loads by number would be in use even though the registry slot is empty (base + i keys and u16
        flash tables stay invisible to this: the device check edits each control and watches the native parameters).
        MODEL_CHILD_EDIT's own special cases are found (control): none of ours is one."""
        keys = ALLOW["analog"] + ALLOW["drums"] + FM_NEXT
        hits = immediates(STOCK, ((BASE, S.M7_DSP), (0x08100000, BASE + len(STOCK))), keys)   # M7 code up to the zero gap; the M4 half
        self.assertEqual({hex(k): [hex(a) for a in v] for k, v in hits.items() if v}, {})
        native = immediates(STOCK, ((0x08100000, BASE + len(STOCK)),), (S.KEY_SEL_TRACK,))[S.KEY_SEL_TRACK]
        self.assertGreater(len(native), 3, "control: a key stock does load by number is found")
        special = immediates(STOCK, ((S.MODEL_CHILD_EDIT, S.MODEL_CHILD_EDIT + 0x1C8),), (0x143, 0x14E, 0x150))
        self.assertTrue(all(special.values()), f"control: the 0xD0 edit's special cases are found there {special}")

    def test_stocks_key_range_tests_send_the_analog_keys_to_their_default(self):
        """Review S4: stock also tests keys by range (sub rX, rY, #K; cmp rX, #N), which the immediate scan cannot see.
        A word-table range covering one of our keys must send it to the range's out-of-range target (the default case).
        BE4.40: Shape 0x1A7 and Sync 0x1AB (and the drums' keys) fall in the one range that is a native table store, M7
        0x08059990 (0x18C..0x1B6), which is reached only for set_property messages to the object 0xFFFFFFF1 (the M4's
        reverb object); ours carry a track handle (MODEL_CHILD_EDIT, PROP_PUSH: test_native_props logs them)."""
        found = key_ranges(STOCK, ((BASE, S.M7_DSP), (0x08100000, BASE + len(STOCK))))
        cover = lambda keys: sorted((a, k) for a, lo, n, _, _ in found for k in keys if lo <= k <= lo + n)
        tables = [(a, k) for a, k in cover(ALLOW["analog"]) if next(t for b, _, _, _, t in found if b == a) is not None]
        stores = sorted({a for a, k in cover(ALLOW["analog"] + ALLOW["drums"] + FM_NEXT)} - {a for a, _ in tables})
        # reviewed (facts.py KEY_OSC_TYPE): M7 0x0805D4F4 and M4 0x08132E80 / E88 (the M4 one repeats its test), Type only
        self.assertEqual([(a >= 0x08100000, k) for a, k in tables], [(False, S.KEY_OSC_TYPE), (True, S.KEY_OSC_TYPE), (True, S.KEY_OSC_TYPE)],
                         f"the known table ranges: {[hex(a) for a, _ in tables]}")
        for a, lo, n, default, table in found:
            for k in ALLOW["analog"]:
                if table is not None and lo <= k <= lo + n:
                    with self.subTest(site=hex(a), key=hex(k)):
                        self.assertEqual(struct.unpack_from("<I", STOCK, table + 4 * (k - lo) - BASE)[0] & ~1, default,
                                         "the key's case is the default one")
        self.assertEqual(len(stores), 1, f"one native-store range covers Shape, Sync, the drum keys and Bit Depth: {[hex(a) for a in stores]}")
        self.assertEqual(sorted(k for a, k in cover(ALLOW["analog"]) if a == stores[0]), [S.KEY_OSC_SHAPE, S.KEY_OSC_SYNC])
        self.assertTrue(all(d is None for a, _, _, d, _ in found if a in stores), "it is no table dispatch: a native store")
        self.assertGreaterEqual(len(found), 4, "control: the scan finds stock's range tests")
        a = stores[0]
        pre = STOCK[a - 8 - BASE:a - BASE]
        self.assertEqual(pre[:4], bytes.fromhex("13f10f0f"), f"cmn.w r3, #0xf at {a - 8:#x}")
        self.assertEqual(pre[4:6][1] & 0xF0, 0xD0, "a conditional branch (bne) away from the store")
        self.assertEqual(pre[4:6][1] & 0x0F, 0x1, "ne")
        self.assertEqual(pre[6:8], bytes.fromhex("e368"), "ldr r3, [r4, #0xc]: the message's key, then the range test")

    @unittest.skipUnless(ANALOG, "no analog group in this image")
    def test_the_registry_build_writes_our_four_descriptors(self):
        """N2: model init's bl REG_BUILD goes to our wrapper (stock's builder, then np_keys): the four descriptors, with
        their XML tokens, exactly when stock's own exist."""
        got = self.build()
        self.assertEqual(got[S.KEY_OSC_TYPE], (S.KEY_OSC_TYPE, 7, "Type", 2, 0, 0, "bento_osc_type", ["Wavetable", "Analog"]))
        self.assertEqual(got[S.KEY_OSC_SHAPE], (S.KEY_OSC_SHAPE, 2, "Shape", 0, 0, 127, "bento_shape", []), "an integer 0..127, as stock's Shift")
        self.assertEqual(got[S.KEY_OSC_SYNC], (S.KEY_OSC_SYNC, 7, "Sync 1>2", 2, 0, 0, "bento_sync", ["Off", "On"]))
        self.assertEqual(got[S.KEY_OSC_GLIDE], (S.KEY_OSC_GLIDE, 10, "Glide", 0, 0, 1000, "bento_glide", []), "0..1000, as stock's Pulse Width")
        self.assertEqual(got["state"], 1, "keys ok")
        stock = registry()[2]
        self.assertEqual({k: v for k, v in got["reg"].items() if k not in ALLOW["analog"]}, stock, "every stock entry as stock's builder left it")

    @unittest.skipUnless(ANALOG, "no analog group in this image")
    def test_one_key_taken_means_none_written(self):
        got = self.build(occupy=S.KEY_OSC_SYNC)
        for k in (S.KEY_OSC_TYPE, S.KEY_OSC_SHAPE, S.KEY_OSC_GLIDE):
            self.assertEqual(got[k][0], 0, f"{k:#x} not written either")
        self.assertEqual(got[S.KEY_OSC_SYNC][:3], (S.KEY_OSC_SYNC, 10, "Busy"), "the occupant is left alone")
        self.assertEqual(got["state"], 0x104, "the black box says which one (taken, bit 2 = Sync)")

    def build(self, occupy=None):
        """The built image: model init's call, i.e. our N2 wrapper (or, to occupy a key first, stock's builder and then
        np_keys), and what the registry holds then."""
        wrap = int(MAN["m4_symbols"]["synth.wrap_reg_build"], 16)
        npk = int(MAN["m4_symbols"]["synth.np_keys"], 16)
        out = {}
        def after(u, r):
            if occupy:
                u.mem_write(STR, b"Busy\0")
                u.mem_write(r + occupy * 32, struct.pack("<HBBI", occupy, 10, 0, STR))
                u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
                u.emu_start(npk | 1, SENT, count=2_000_000)
            def text(a): return bytes(u.mem_read(a, 32)).split(b"\0")[0].decode() if a else ""
            for k in ALLOW["analog"]:
                e = bytes(u.mem_read(r + k * 32, 32))
                key, typ = struct.unpack_from("<HB", e)
                title, labels, n, lo, hi, tok = struct.unpack_from("<IIIiiI", e, 4)
                out[k] = (key, typ, text(title) if key else "", n, lo, hi, text(tok) if key else "",
                          [text(struct.unpack("<I", u.mem_read(labels + 4 * i, 4))[0]) for i in range(n)] if key and labels else [])
            out["state"] = struct.unpack("<I", u.mem_read(S.D2_SLICE + 0xE20 + 8, 4))[0]   # the synth UI state's keys word (header word 2)
        _, _, out["reg"] = registry(IMG, after, entry=None if occupy else wrap)
        return out


STR = 0x20010000             # harness string  # addr-ok: harness
# Keys the FM / Bit Depth sprint takes next (docs/plan-native-props.md 3.1): Bit Depth, Sub FM, FM 1>2. Kept clean here.
FM_NEXT = (0x1A4, 0x18B, 0x132)

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
