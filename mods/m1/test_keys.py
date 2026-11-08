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
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
STOCK = Path(sys.argv[1]).read_bytes()
del sys.argv[1:3]

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


def registry():
    """{key: (title, type, choices)} as stock's builder leaves the registry."""
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, STOCK)
    for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xC0000000, 0x1000000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    u.mem_write(SENT, b"\xfe\xe7")
    heap = [HEAP]
    def malloc(uc, a, sz, _):                                  # stock's M4 malloc: a fresh block, 8-aligned
        n = uc.reg_read(UC_ARM_REG_R0); p = heap[0]; heap[0] += (n + 7) & ~7
        uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
    u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1); u.reg_write(UC_ARM_REG_R0, OBJ)
    u.emu_start(S.REG_BUILD | 1, SENT, count=20_000_000)
    r = struct.unpack("<I", u.mem_read(S.REGISTRY, 4))[0]
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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
