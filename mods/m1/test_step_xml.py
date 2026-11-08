# SPDX-License-Identifier: GPL-3.0-or-later
"""be_step_v1 through stock's own note writer, XML library and note reader (S4+S5).

    python test_step_xml.py <stock bento1.bin> <m1 build dir>

test_step.py's SaveLoad stubs everything past our save / load sites, so on a new base it proves our
logic, not the sites. Here nothing is stubbed: notes go into the writer's container, stock's per-sequence
note writer (the function around STEP_SAVE_SITE) writes them into a document built by stock's own XML
code, and stock's note reader (around STEP_LOAD_SITE) reads them back into a fresh container. Our
wrappers run inside, at the base's real sites, with the real stack frames. Every allocator starts from
the M4's own .data image (M4_DATA_INIT). Rig from the step-data investigator (scratch rc19/stepdata/xmlrt.py).
"""
import struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_BLOCK
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
STOCK = Path(sys.argv[1]).read_bytes()
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
del sys.argv[1:3]

BASE = 0x08040000
SENT = 0x20000100                       # harness return pad (DTCM: no M4 code can address it)  # addr-ok: harness
STACK = 0x2001F000                      # harness stack, DTCM  # addr-ok: harness
STRS = 0x20010000                       # harness strings  # addr-ok: harness
DOC = 0xC0000000                        # the document object (SDRAM; stock's allocators hand out other SDRAM)  # addr-ok: harness
OBJ, CH = 0xC0300000, 0xC0300100        # the reader's target: an object whose child list holds one container  # addr-ok: harness
W1 = (0xB6 << 24) | (5 << 17) | (3 << 13) | 1     # a v2 per-step word: condition 5, 3 hits, velocity lock
RECS = [[1, 0, 960, 11, 60 | (100 << 16), W1],                  # kind | prob << 8 | pad << 16, time, length, id, pitch | vel << 16, word
        [1 | (50 << 8), 960 * 4, 480, 12, 62 | (90 << 16), 0],     # plain note with a probability
        [1 | (120 << 8), 960 * 8, 960, 13, 64 | (80 << 16), 0],    # pre-v3.3 condition in the probability byte (120 = 21)
        [2, 960 * 2, 960, 14, 3, 0x1234]]                          # automation: +0x14 is stock's own value2


def same(r):
    """What a save / load keeps of a record besides the word: kind | prob | pad, time, length, pitch | velocity."""
    return [r[0], r[1], r[2], r[4]]


def round_trip(image, recs=RECS):
    """(records read back, stock M4 basic blocks executed outside our code)."""
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, image[:0x200000 - (BASE - 0x08000000)])
    for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xC0000000, 0x8000000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    u.mem_write(0x30000000, image[S.M4_DATA_INIT - BASE:S.M4_DATA_INIT - BASE + S.M4_DATA_INIT_LEN])   # the M4's first .data block
    u.mem_write(SENT, b"\xfe\xe7")
    blocks = set()
    u.hook_add(UC_HOOK_BLOCK, lambda uc, a, sz, _: blocks.add(a) if 0x08100000 <= a < S.APPEND_FROM else None)
    w32 = lambda a, x: u.mem_write(a, struct.pack("<I", x & 0xFFFFFFFF))
    r32 = lambda a: struct.unpack("<I", u.mem_read(a, 4))[0]
    def call(fn, *args):
        for i, x in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, x & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(fn | 1, SENT, count=5_000_000)
        return u.reg_read(UC_ARM_REG_R0)
    u.mem_write(STRS, b"track\0")
    w32(S.D2_SLICE + 0x1E0, 0x31474144); w32(S.D2_SLICE + 0x1E0 + 12, 1)   # our CFG line: boot generation 1
    for i, r in enumerate(recs):
        u.mem_write(S.NOTE_WRITE_SRC + 24 * i, struct.pack("<6I", *r))
    w32(S.NOTE_WRITE_SRC + 0x3000, len(recs))                               # container: 512 x 24 B, count at +0x3000
    u.mem_write(DOC, bytes(0x1000))
    call(S.XML_DOC_INIT, DOC, 1, 0)
    parent = call(S.XML_NEW_ELEMENT, DOC, STRS); call(S.XML_APPEND_CHILD, DOC, parent)
    call(S.NOTE_SEQ_WRITER, DOC, parent)
    u.mem_write(OBJ, bytes(0x40)); u.mem_write(CH, bytes(0x3100))
    w32(OBJ + 0x14, CH); u.mem_write(CH + 8, b"\x02"); w32(CH + 4, 0)     # child list: one type-2 child, its container at +0xC
    call(S.NOTE_SEQ_READER, 0, parent, OBJ)
    n = r32(CH + 0xC + 0x3000)
    return [list(struct.unpack("<6I", u.mem_read(CH + 0xC + 24 * i, 24))) for i in range(n)], len(blocks)


class StockAlone(unittest.TestCase):
    """The base's own firmware, no mod: what a save on it does to our data."""
    @classmethod
    def setUpClass(cls):
        cls.out, cls.blocks = round_trip(STOCK)

    def test_every_record_comes_back_in_order(self):
        """Kind, pad, time, length, pitch and velocity (the id at +0xC is not saved: the reader leaves it 0)."""
        self.assertEqual([same(r) for r in self.out], [same(r) for r in RECS])

    def test_reader_zeroes_the_note_word(self):
        """Stock's reader sets +0x14 to 0 for notes (it never writes or reads be_step_v1): a project saved and
        reloaded on stock loses the per-step data (the checklist works on a copy of SPACED for this)."""
        self.assertEqual([r[5] for r in self.out[:3]], [0, 0, 0])

    def test_automation_value2_survives(self):
        self.assertEqual(self.out[3][5], 0x1234)
        self.assertEqual([(r[0] >> 8) & 0xFF for r in self.out], [0, 50, 120, 0], "stock keeps the probability byte")


class Mod(unittest.TestCase):
    """This build: our save / load sites inside stock's writer and reader."""
    @classmethod
    def setUpClass(cls):
        cls.out, cls.blocks = round_trip(IMG)

    def test_word_round_trips(self):
        self.assertEqual(self.out[0][5], W1)
        self.assertEqual(same(self.out[0]), same(RECS[0]))

    def test_plain_note_unchanged(self):
        self.assertEqual(same(self.out[1]) + [self.out[1][5]], same(RECS[1]) + [0])

    def test_legacy_condition_moves_into_the_word(self):
        """Probability byte 120 (condition 21, pre-v3.3) -> byte 0, word tag 0xB6 with condition 21."""
        self.assertEqual((self.out[2][0] >> 8) & 0xFF, 0)
        self.assertEqual(self.out[2][5], (0xB6 << 24) | (21 << 17))

    def test_automation_value2_untouched(self):
        self.assertEqual(same(self.out[3]) + [self.out[3][5]], same(RECS[3]) + [0x1234])

    def test_stock_code_ran(self):
        self.assertGreater(self.blocks, 300, "the writer, the XML library and the reader are stock's")


class RecordedNotes(unittest.TestCase):
    """The per-step word stock gives a recorded note (S4+S5 review). The M4's recording handler (MEL_NOTE_ON's caller)
    copies words 0..5 of an M7 message into the new note, so its +0x14 is the message's word 5. Every message that
    reaches that handler (byte +0x18 = 1) is built by one of the M7 recorder's two producers and goes through the bus
    (BUS_APPEND, only they call it; the forwarder reads it with BUS_TAKE). Here both producers run on the build's M7
    code with a stack full of 0xA5, so a word they left unset would show: word 5 must be 0 in both."""
    T, NOTE, BUS, P, Q, OUT = 0xC0400000, 0xC0600200, 0xC0500000, 0xC0600000, 0xC0600100, 0xC0600400  # addr-ok: harness

    @classmethod
    def setUpClass(cls):
        u = cls.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG[:0x200000 - (BASE - 0x08000000)])
        u.mem_map(0x20000000, 0x20000); u.mem_map(0xC0000000, 0x1000000)
        u.mem_write(SENT, b"\xfe\xe7")
        u.mem_write(STACK - 0x1000, b"\xa5" * 0x1000)                       # garbage below the harness stack
        w32 = lambda a, x: u.mem_write(a, struct.pack("<I", x & 0xFFFFFFFF))
        # the producers' own reads (both bases): track +0x30 recording, +0x31/+0x32 bytes, +0xC word,
        # +0x8000+0x6C0 -> object (+0x28 halfword), +0x8000+0x6D1 = 0 (no second post); note +0xC, +0x10, +0x14;
        # [bus] -> clock (+0x30, 8 bytes)
        u.mem_write(cls.T + 0x30, b"\x01\x07\x09"); w32(cls.T + 0xC, 3)
        w32(cls.T + 0x8000 + 0x6C0, cls.Q); u.mem_write(cls.Q + 0x28, b"\x34\x12"); u.mem_write(cls.T + 0x8000 + 0x6D1, b"\0")
        w32(cls.NOTE + 0xC, 0x55); w32(cls.NOTE + 0x10, 0x66); u.mem_write(cls.NOTE + 0x14, b"\x3c\x00")
        w32(cls.BUS, cls.P); u.mem_write(cls.P + 0x30, struct.pack("<Q", 0x1122334455667788))
        def call(fn, *args):
            for i, x in enumerate(args):
                u.reg_write(UC_ARM_REG_R0 + i, x & 0xFFFFFFFF)
            u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
            u.emu_start(fn | 1, SENT, count=200_000)
            return u.reg_read(UC_ARM_REG_R0)
        call(S.REC_MSG_5, cls.T, cls.NOTE, cls.BUS)
        call(S.REC_MSG_6, cls.T, cls.NOTE, cls.BUS)
        cls.msgs = []
        for i in range(3):
            if not call(S.BUS_TAKE, cls.BUS, i, cls.OUT):
                break
            cls.msgs.append(bytes(u.mem_read(cls.OUT, 48)))

    def test_both_producers_posted_one_message_each(self):
        self.assertEqual([struct.unpack_from("<I", m)[0] & 0xFF for m in self.msgs], [5, 6])

    def test_they_reach_the_recording_handler(self):
        """Byte +0x18 = 1: the M4 consumer's case that calls the recording handler (6 goes elsewhere)."""
        self.assertEqual([m[0x18] for m in self.msgs], [1, 1])

    def test_word_5_is_zero(self):
        """So a recorded note's +0x14 is 0, as for notes made on the UI and notes loaded by stock's reader."""
        self.assertEqual([struct.unpack_from("<6I", m)[5] for m in self.msgs], [0, 0])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
