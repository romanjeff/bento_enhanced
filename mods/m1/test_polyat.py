# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for M1 poly aftertouch + M1b polyphony cap (Unicorn 2.1.3).

    python test_polyat.py <CH10 bento1.bin> <m1 build dir>

Poly AT
  - the External handler's patched branch leaves every non-0x0E event type
    exactly as stock; 0x0E for a sounding note posts {0x5F,0,port,ch,note,val};
  - rate control: changed values only, <= 1 send / 10 ms / note with later
    flush, per-port budget (4-burst, 400/s); pressure only for sounding notes.
Poly cap (h_note behind the five redirected post sites)
  - every site posts exactly what CH10 posts while under the cap;
  - 7th note steals the oldest (Note Off), its later native Note Off is
    swallowed; a repeated note retriggers one voice; caps are per track;
  - non-note events and everything before the M4 region exists pass through.
M4
  - our 0x5F handler makes the native Note On case's calls with 0xA0 for 0x90;
    only jump-table slot 0x5F changed.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())   # the base table this build was made from
D2 = S.D2_SLICE                                                # our shared slice (per base)

BASE = 0x08040000
SENT = 0x08000100
STACK = 0x2407F000  # addr-ok: harness
CYC = 0xE0001004
ENGINE = 0x24060000              # stock posts MIDI-out events with msg_send(ENGINE + 0x7C8, ev) (addr-ok: harness)
QUEUE = ENGINE + 0x7C8
CFG = D2 + 0x1E0
REGION = S.REGION
LEFT, CURSOR, POOL_END, LEFT0 = S.POOL_LEFT_RAM, S.POOL_CUR_RAM, S.EXT_POOL_END, 3405824   # stock's ext pool
DCCMVAC = 0xE000EF68
OFF = set(json.loads((Path(sys.argv[2]) / "manifest.json").read_text()).get("disabled", []))
# sizeof(Region): BE4.22 (note0 in the image, 1.6.8) adds the note-0 line, 32-aligned (diag_shared.h REGION_NOTE0), and the
# remembered Ons (8 words) at its end
NOTE0 = "note0" not in json.loads((Path(sys.argv[2]) / "manifest.json").read_text()).get("omitted", [])
REGION_SIZE = 0x2400 + 64 if NOTE0 else 9208
# S6a: with the synth groups in the image (1.6.8; also in a BE_DISABLE=analog bisect) the one reservation takes the synth
# area below the Region too: 32 KB more, and only while the pool keeps 1 MB (typed here, not read from synth_shared.h).
SYNTH = "analog" not in json.loads((Path(sys.argv[2]) / "manifest.json").read_text()).get("omitted", [])
SYNTH_BYTES, SYNTH_MARGIN = 0x8000, 0x100000
TOTAL = 0x4000 + (SYNTH_BYTES if SYNTH else 0)
SA = REGION - SYNTH_BYTES                                       # the synth area (synth_shared.h)
SCAN_HI = D2 + 0xF60
MAGIC = 0x31474144
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]
NOTE_SITES = [(S.POLYCAP_SEQ_ON_SITE, UC_ARM_REG_R4), (S.POLYCAP_PAD_ON_SITE, UC_ARM_REG_R4), (S.POLYCAP_SEQ_OFF_SITE, UC_ARM_REG_R4),
              (S.POLYCAP_PAD_OFF_SITE, UC_ARM_REG_R4), (S.POLYCAP_ALL_OFF_SITE, UC_ARM_REG_R6)]

CH10 = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
SYM7 = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}
MAN = json.loads((OUT / "manifest.json").read_text())
OMITTED = set(MAN.get("omitted", []))      # S3: groups not in this image (1.6.8: poly_at and inta_live are S5)
POLY_AT, INTA_LIVE = "poly_at" not in OMITTED, "inta_live" not in OMITTED
POLY = int(MAN["layout"]["M4_POLY"], 16) if POLY_AT else None
CHAN = POLY + 0x80 if POLY_AT else None                              # build.py M4_CHAN
S5 = "S5 on this base: the External handler hook (h_poly) and the M4 MIDI handlers are not in this image"

def emu(img, region=True):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (S.REGION & ~0xFFFF, 0x10000), (S.EXT_POOL_END, 0x200000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    pool(u)
    if region:   # what the M4 publishes at boot
        u.mem_write(CFG, struct.pack("<8I", MAGIC, REGION, 0x1000, 1, 0, 0, 0, 0))
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]
def pool(u, left=LEFT0, end=POOL_END):
    """The ext pool's counters as stock's startup leaves them (cursor + left is constant)."""
    w32(u, LEFT, left); w32(u, CURSOR, end - left)

def track(u, port=1, ch=2, midi=1, base=0x24064000):  # addr-ok: harness
    u.mem_write(base, bytes(0x8800))
    u.mem_write(base + 0x31, bytes([port, ch]))
    u.mem_write(base + 0x86D1, bytes([midi]))
    return base

def queued(u):
    """Events in the native msg_send queue: 24-byte entries, count at +0x600."""
    n = r32(u, QUEUE + 0x600)
    return [struct.unpack("<6I", u.mem_read(QUEUE + 24 * i, 24)) for i in range(n)]

def call(u, fn, *args):
    for i, v in enumerate(args):
        u.reg_write(UC_ARM_REG_R0 + i, v)
    u.reg_write(UC_ARM_REG_SP, STACK)
    u.reg_write(UC_ARM_REG_LR, SENT | 1)
    u.emu_start(fn | 1, SENT, count=400000)

class Voices:
    """Drive h_note directly, as the five post-site stubs do."""
    def __init__(self, img=None, region=True):
        self.u = emu(img or IMG, region)
        self.t1 = track(self.u, port=1, ch=2, base=0x24064000)  # addr-ok: harness
        self.t2 = track(self.u, port=1, ch=3, base=0x2406D000)  # addr-ok: harness
        w32(self.u, CYC, 10_000_000)

    def ev(self, t, note, w1=0, port=1, ch=2, vel=100):
        at = 0x24070000  # addr-ok: harness
        self.u.mem_write(at, struct.pack("<6I", t, w1, port, ch, note, vel))
        return at

    def on(self, note, tr=None, **k): call(self.u, SYM7["h_note"], QUEUE, self.ev(0x5B, note, **k), tr or self.t1)
    def off(self, note, tr=None, **k): call(self.u, SYM7["h_note"], QUEUE, self.ev(0x5C, note, **k), tr or self.t1)
    def pressure(self, note, value, tr=None):
        at = 0x24070100  # addr-ok: harness
        self.u.mem_write(at, struct.pack("<6I", 0x0E, 0, 0x71, note, value, 0))
        call(self.u, SYM7["h_poly"], tr or self.t1, at, ENGINE)
    def out(self): return [(e[0], e[4]) for e in queued(self.u)]

class RegionReservation(unittest.TestCase):
    """v3.70: the region is the top 16 KB of the M7's ext pool, taken by lowering stock's counter
    (m7_polyat.c region()). What a reset leaves differs per core: the M4's generation lives in D2,
    the pool counters in M7 RAM that stock's startup restores."""

    def setUp(self):
        self.v = Voices()
        self.u = self.v.u

    def state(self): return r32(self.u, LEFT), r32(self.u, CURSOR)
    def reserved(self): return LEFT0 - TOTAL, POOL_END - LEFT0
    def new_gen(self): w32(self.u, CFG + 12, r32(self.u, CFG + 12) + 1)
    def touch(self): call(self.u, SYM7["curve_emit"], 1, 0, 0, 128)   # region() and nothing else: midi_q is 0
    def trash(self):
        """Whatever a reset leaves in SDRAM: old contents, and lines that never reached it."""
        self.u.mem_write(REGION, bytes([0xA5]) * 0x4000)

    def assert_fresh(self):
        b = bytes(self.u.mem_read(REGION, REGION_SIZE + 4))
        self.assertEqual(struct.unpack_from("<I", b, 0)[0], 0x36424D31, "magic")
        tokens = [struct.unpack_from("<I", b, 80 + 8 * i + 4)[0] for i in range(4)]
        self.assertEqual(tokens, [4] * 4, "port budgets full")
        self.assertEqual(b[4:80], bytes(76), "stats, midi_q, bus")
        self.assertEqual(b[112:REGION_SIZE], bytes(REGION_SIZE - 112), "voices, pressure, bursts, players, channels, mod, note mod")
        self.assertEqual(b[REGION_SIZE:], bytes([0xA5]) * 4, "not a byte past sizeof(Region)")
        self.assertEqual((r32(self.u, SCAN_HI), r32(self.u, SCAN_HI + 4)), (0, 0))

    def test_first_use_reserves_once(self):
        self.assertEqual(self.state(), (LEFT0, POOL_END - LEFT0), "nothing before the first use")
        self.trash(); self.touch()
        self.assertEqual(self.state(), self.reserved(), "left falls 16 KB, the cursor stays")
        self.assert_fresh()
        for n in (60, 61, 62): self.v.on(n)
        self.touch()
        self.assertEqual(self.state(), self.reserved(), "no second drop")
        self.assertEqual(self.v.out(), [(0x5B, 60), (0x5B, 61), (0x5B, 62)])

    def test_m4_reboot_zeroes_without_a_second_drop(self):
        self.v.on(60)
        self.trash(); self.new_gen(); self.touch()
        self.assertEqual(self.state(), self.reserved())
        self.assert_fresh()

    def test_zeroing_cleans_the_note0_line(self):
        """BE4.22 (review rc23 I4): the M4 prints the note-0 line (REGION + 0x2400, typed here); after zeroing the region
        the M7 cleans it, so a save before any note-0 note reads zeros, not what a reset left in SDRAM."""
        lines = []
        def w(uc, k, a, n, v, _):
            if a == DCCMVAC: lines.append(v & 0xFFFFFFFF)
        self.v.on(60)
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, w); self.u.ctl_flush_tb()
        self.trash(); self.new_gen(); self.touch()
        self.u.hook_del(h)
        self.assert_fresh()
        self.assertEqual(REGION + 0x2400 in lines, NOTE0, lines)

    def test_both_cores_reset_reserves_again(self):
        self.touch()
        self.trash(); pool(self.u); self.new_gen(); self.touch()
        self.assertEqual(self.state(), self.reserved())
        self.assert_fresh()

    def test_m7_only_reset_with_the_same_generation_reserves_again(self):
        """The generation compare alone would take the fast path: the pool would stay open, and
        the region would hold whatever of it was still dirty in the lost cache."""
        for n in range(60, 67): self.v.on(n)        # 60 stolen: its native Note Off is owed
        self.trash(); w32(self.u, SCAN_HI, 77); w32(self.u, SCAN_HI + 4, 77)
        pool(self.u)                                # stock's startup; D2 keeps the generation and our pointer
        self.touch()
        self.assertEqual(self.state(), self.reserved())
        self.assert_fresh()

    def test_m7_only_reset_forgets_what_the_old_stock_owed(self):
        for n in range(60, 67): self.v.on(n)        # 60 stolen: its native Note Off is owed
        pool(self.u)
        self.u.mem_write(QUEUE + 0x600, bytes(4))
        self.v.off(60)
        self.assertEqual(self.v.out(), [(0x5C, 60)], "a Note Off of the new run is not swallowed")

    def test_unknown_pool_shapes_stay_off_and_notes_pass(self):
        for name, left, end in (("tail too small", 0x3000, POOL_END), ("one byte short", 0x3FFF, POOL_END),
                                ("other end", LEFT0, 0xC5F00000), ("past the end", LEFT0, POOL_END + 0x4000),  # addr-ok: harness
                                ("not set up", 0, 0)):
            with self.subTest(name):
                v = Voices(); u = v.u
                pool(u, left, end)
                before = bytes(u.mem_read(LEFT, 8))
                u.mem_write(REGION, bytes([0xA5]) * 16)
                for n in range(60, 70): v.on(n)
                self.assertEqual(v.out(), [(0x5B, n) for n in range(60, 70)], "no cap: every note passes")
                self.assertEqual(bytes(u.mem_read(LEFT, 8)), before, "counters untouched")
                self.assertEqual(bytes(u.mem_read(REGION, 16)), bytes([0xA5]) * 16, "region untouched")
                pool(u)                             # the known shape: it comes up
                call(u, SYM7["curve_emit"], 1, 0, 0, 128)
                self.assertEqual((r32(u, LEFT), r32(u, REGION)), (LEFT0 - TOTAL, 0x36424D31))

    def test_an_interrupted_allocation_only_turns_the_state_off_for_that_call(self):
        """Stock's allocator lowers left, then raises the cursor, with interrupts on."""
        self.v.on(60)
        w32(self.u, LEFT, r32(self.u, LEFT) - 0x20)
        self.touch()
        self.assertEqual(r32(self.u, REGION), 0x36424D31)
        self.v.on(61)                               # passes, not tracked
        w32(self.u, CURSOR, r32(self.u, CURSOR) + 0x20)
        self.v.on(60)                               # state kept: same note, so a retrigger
        out = self.v.out()
        self.assertEqual(out, [(0x5B, 60), (0x5B, 61), (0x5C, 60), (0x5B, 60)])
        self.assertEqual(self.state(), (LEFT0 - TOTAL - 0x20, POOL_END - LEFT0 + 0x20), "stock's allocation kept, no second drop")

    def test_stock_allocating_below_the_reservation_is_left_alone(self):
        self.v.on(60)
        w32(self.u, LEFT, r32(self.u, LEFT) - 0x1000); w32(self.u, CURSOR, r32(self.u, CURSOR) + 0x1000)
        self.v.on(60)
        self.assertEqual(self.v.out(), [(0x5B, 60), (0x5C, 60), (0x5B, 60)], "same state: a retrigger")

    def test_a_pointer_from_older_firmware_is_not_followed(self):
        """CFG survives a reset; only the M4's own boot code replaces it."""
        OLD = 0xC6100000  # addr-ok: harness
        w32(self.u, CFG + 4, OLD)
        writes = []
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: writes.append(a), begin=OLD, end=OLD + 0x4000)
        for n in range(60, 70): self.v.on(n)
        self.u.hook_del(h)
        self.assertEqual(self.v.out(), [(0x5B, n) for n in range(60, 70)])
        self.assertEqual(writes, [])
        self.assertEqual(self.state(), (LEFT0, POOL_END - LEFT0), "no reservation either")

    def test_the_counter_is_lowered_with_interrupts_masked(self):
        seen = []
        def w(uc, k, a, n, v, _):
            if a == LEFT: seen.append((v, uc.reg_read(UC_ARM_REG_PRIMASK)))
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, w)
        args = 0x24070200                           # nm_off takes the region before it masks anything (addr-ok: harness)
        self.u.mem_write(args, struct.pack("<4I", self.v.t1, 60, 0, 0))
        self.assertEqual(self.u.reg_read(UC_ARM_REG_PRIMASK), 0)
        call(self.u, SYM7["nm_off"], args)
        self.u.hook_del(h)
        self.assertEqual(seen, [(LEFT0 - TOTAL, 1)])
        self.assertEqual(self.u.reg_read(UC_ARM_REG_PRIMASK), 0, "and back on")

    # ---- S6a: the synth area, taken in the same reservation (m7_polyat.c synth_take, synth_shared.h)
    def synth_head(self): return struct.unpack("<5I", self.u.mem_read(SA, 20))
    def take_word(self): return r32(self.u, D2 + 0x1D8)

    def test_synth_area_comes_with_the_region(self):
        if not SYNTH:
            self.skipTest("no synth groups in this image (1.6.1): the v3.70 reservation")
        self.u.mem_write(SA, bytes([0xA5]) * SYNTH_BYTES)
        self.touch()
        self.assertEqual(self.state(), self.reserved(), "one drop of 48 KB")
        self.assertEqual(self.synth_head(), (0x37534542, 1, 2, 0xC000, 1), "M7 header: magic, generation 1, layout 2, total, boot 1")
        self.assertEqual(self.take_word(), 0xA5 << 24 | 1, "SYNTH_TAKE: both taken, no Region-only take")
        b = bytes(self.u.mem_read(SA, 0x800))
        self.assertEqual(b[0x14:0x20] + b[0x40:0x200] + b[0x400:0x800], bytes(0xC + 0x1C0 + 0x400), "the M7's lines zeroed")
        self.assertEqual(b[0x20:0x40] + b[0x200:0x400], bytes([0xA5]) * 0x220, "the M4's lines left alone")
        self.assertEqual(bytes(self.u.mem_read(SA + 0x800, 0x7800)), bytes([0xA5]) * 0x7800, "nothing else written")

    def test_m7_only_reset_moves_the_synth_generation(self):
        if not SYNTH:
            self.skipTest("no synth groups in this image")
        self.touch()
        self.u.mem_write(SA + 0x200, b"M4TABLE!")
        pool(self.u); self.touch()                  # stock's startup restored the counters: taken again
        self.assertEqual(self.state(), self.reserved())
        self.assertEqual(self.synth_head()[:2], (0x37534542, 2), "generation 2: the M4 republishes")
        self.assertEqual(bytes(self.u.mem_read(SA + 0x200, 8)), b"M4TABLE!", "the M4's table survives an M7-only reset")
        self.v.on(60); self.touch()
        self.assertEqual(self.synth_head()[1], 2, "no new generation without a new take")

    def test_synth_reservation_keeps_a_megabyte_else_the_region_alone(self):
        """Review S1 (build): a pool with less than 1 MB to spare still gets the Region (poly cap, curves, Note Mod), as
        v4.10 did; only the synth area is left out, and SYNTH_TAKE says so for the black box."""
        if not SYNTH:
            self.skipTest("no synth groups in this image")
        for left, taken in ((TOTAL + SYNTH_MARGIN - 4, False), (TOTAL + SYNTH_MARGIN, True)):
            with self.subTest(left=hex(left)):
                v = Voices(); u = v.u
                pool(u, left)
                u.mem_write(SA, struct.pack("<5I", 0x37534542, 9, 2, 0xC000, 1))   # a header a previous take left
                call(u, SYM7["curve_emit"], 1, 0, 0, 128)
                self.assertEqual(r32(u, LEFT), left - (TOTAL if taken else 0x4000))
                self.assertEqual(r32(u, SA), 0x37534542 if taken else 0, "a live header only when taken (else cleared: free memory)")
                self.assertEqual(r32(u, REGION), 0x36424D31, "the Region is up either way")
                self.assertEqual(r32(u, D2 + 0x1D8), 0xA5 << 24 | (1 if taken else 1 << 8 | 2))
                for n in range(60, 67): v.on(n)
                self.assertIn((0x5C, 60), v.out(), "the poly cap works: the 7th note stole the 1st")
                call(u, SYM7["curve_emit"], 1, 0, 0, 128)
                self.assertEqual(r32(u, LEFT), left - (TOTAL if taken else 0x4000), "no second drop on the next call")

    def test_region_only_below_the_header_leaves_stocks_memory_alone(self):
        """Less than the whole 48 KB free: the header's address may be stock's, so it is not written."""
        if not SYNTH:
            self.skipTest("no synth groups in this image")
        v = Voices(); u = v.u
        pool(u, 0x6000)
        u.mem_write(SA, b"STOCK!!!")
        call(u, SYM7["curve_emit"], 1, 0, 0, 128)
        self.assertEqual((r32(u, LEFT), r32(u, REGION)), (0x2000, 0x36424D31))
        self.assertEqual(bytes(u.mem_read(SA, 8)), b"STOCK!!!")

    def test_the_header_names_a_new_m4_boot(self):
        """Review S2 (build): the M4 trusts a header only for its own boot; an M4-only reset moves the generation."""
        if not SYNTH:
            self.skipTest("no synth groups in this image")
        self.touch()
        self.new_gen(); self.touch()
        self.assertEqual(self.synth_head()[1:], (1, 2, 0xC000, 2), "same take (generation 1), stamped for boot 2")


    def test_the_check_costs_two_reads_of_one_line_on_the_fast_path(self):
        self.touch()
        acc = []
        def m(uc, k, a, n, v, _):
            if S.POOL_LEFT_RAM - 0x20 <= a < S.POOL_LEFT_RAM + 0x20: acc.append((k == UC_MEM_WRITE, a))
        h = self.u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, m)
        self.u.ctl_flush_tb()                       # code that already ran doesn't see a later hook
        self.touch()
        self.u.hook_del(h)
        self.assertEqual(sorted(acc), [(False, LEFT), (False, CURSOR)], "one region() call: two loads, no write")

    def test_a_call_landing_in_the_zero_loop_after_a_take_zeroes_too(self):
        """M7-only reset, the same generation: the cached pointer and generation still match, and a
        first caller like nm_off zeroes unmasked. A call in that window mustn't take the fast path
        onto a half-zeroed region, which would swallow a Note Off of the new run on the old owed[]."""
        u = self.u
        for n in range(60, 67): self.v.on(n)        # 60 stolen: its native Note Off is owed
        self.assertEqual(r32(u, REGION + 16), 1, "the old run stole one")
        pool(u)
        u.mem_write(QUEUE + 0x600, bytes(4))
        u.mem_write(REGION + REGION_SIZE, bytes([0xA5]) * 4)   # assert_fresh: not a byte past sizeof(Region)
        stop = REGION + 60                          # mid stats; owed[] is much further up
        def w(uc, k, a, n, v, _):
            if a == stop: uc.emu_stop()
        h = u.hook_add(UC_HOOK_MEM_WRITE, w)
        u.ctl_flush_tb()
        args = 0x24070200  # addr-ok: harness
        u.mem_write(args, struct.pack("<4I", self.v.t1, 99, 0, 0))
        u.reg_write(UC_ARM_REG_R0, args); u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(SYM7["nm_off"] | 1, SENT, count=400000)
        u.hook_del(h)
        pc = u.reg_read(UC_ARM_REG_PC)
        self.assertNotEqual(pc, SENT, "stopped in the zero loop")
        self.assertEqual(u.reg_read(UC_ARM_REG_PRIMASK), 0, "this window is unmasked")
        regs = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_XPSR]
        saved = [u.reg_read(r) for r in regs]
        stack = bytes(u.mem_read(STACK - 0x400, 0x400))   # call() runs on the same stack
        written = []
        h = u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: written.append(a), begin=REGION, end=REGION + REGION_SIZE - 1)
        u.ctl_flush_tb()
        self.v.off(60)
        u.hook_del(h)
        self.assertEqual(self.v.out(), [(0x5C, 60)], "not swallowed")
        self.assertGreaterEqual(len(set(written)), REGION_SIZE, "it zeroed the whole region itself")
        self.assertEqual(r32(u, REGION), 0x36424D31)
        for r, v in zip(regs, saved): u.reg_write(r, v)
        u.mem_write(STACK - 0x400, stack)
        u.ctl_flush_tb()
        u.emu_start(pc | 1, SENT, count=400000)     # the interrupted call finishes
        self.assertEqual(self.state(), self.reserved(), "no second drop")
        self.assert_fresh()

    def test_a_reset_before_the_m4_republishes_zeroes_and_its_new_generation_zeroes_again(self):
        """Both cores reset, and the M7 gets here while D2 still holds the old config. It can't tell
        this from an M7-only reset, so it zeroes: the M4's "previous boot" print then reads 0 (README)."""
        self.v.on(60)
        self.trash(); pool(self.u); self.touch()    # the old generation, still in CFG
        self.assertEqual(self.state(), self.reserved())
        self.assert_fresh()
        self.trash(); self.new_gen(); self.touch()  # the M4 republishes
        self.assertEqual(self.state(), self.reserved(), "no second drop")
        self.assert_fresh()

    def test_only_a_build_without_the_publish_hook_cleans_stats_from_region(self):
        """d0_publish runs from h_consume. With its site stock (trace off) the M4 would see frozen stats."""
        self.touch()
        lines = []
        def w(uc, k, a, n, v, _):
            if a == DCCMVAC and v & 0xF0000000 == 0xC0000000: lines.append(v & 0xFFFFFFFF)
        h = self.u.hook_add(UC_HOOK_MEM_WRITE, w)
        self.u.ctl_flush_tb()
        ALL = [REGION, REGION + 0x20, REGION + 0x40]
        def at(cyc):
            lines.clear(); w32(self.u, CYC, cyc); self.touch(); return list(lines)
        if "trace" not in OFF:
            self.assertEqual(at(1_000_000_000), [], "d0_publish's job")
        else:
            self.assertEqual(IMG[S.H_CONSUME_SITE - BASE:S.H_CONSUME_SITE + 4 - BASE], S.orig("H_CONSUME_SITE"), "h_consume is unpatched")
            self.assertEqual(at(1_000_000_000), ALL, "the three lines the M4 prints")
            self.assertEqual(at(1_000_000_000 + 13 * 480000 - 1), [], "rate limited")
            self.assertEqual(at(1_000_000_000 + 13 * 480000), ALL, "again after 13 ms")
        self.u.hook_del(h)

class PolyCap(unittest.TestCase):
    def test_sites_post_like_ch10_under_the_cap(self):
        for site, treg in NOTE_SITES:
            for t in (0x5B, 0x5C):
                with self.subTest(site=hex(site), type=hex(t)):
                    res = []
                    for img in (CH10, IMG):
                        u = emu(img)
                        tr = track(u)
                        ev = 0x24070000  # addr-ok: harness
                        u.mem_write(ev, struct.pack("<6I", t, 7, 1, 2, 60, 99))
                        u.reg_write(UC_ARM_REG_R0, QUEUE); u.reg_write(UC_ARM_REG_R1, ev)
                        u.reg_write(treg, tr); u.reg_write(UC_ARM_REG_SP, STACK)
                        w32(u, CYC, 10_000_000)
                        u.emu_start(site | 1, site + 4, count=400000)
                        res.append(queued(u))
                    self.assertEqual(res[0], res[1])
                    self.assertEqual(res[1], [(t, 7, 1, 2, 60, 99)])

    def test_seventh_note_steals_oldest_and_swallows_its_off(self):
        v = Voices()
        for n in range(60, 66):
            v.on(n)
        self.assertEqual(v.out(), [(0x5B, n) for n in range(60, 66)])
        v.on(70)                                   # 7th: Note Off for 60 first, then 70
        self.assertEqual(v.out()[6:], [(0x5C, 60), (0x5B, 70)])
        v.off(60)                                  # native off for the stolen note: swallowed
        self.assertEqual(len(v.out()), 8)
        v.off(61)                                  # a live note's off passes
        self.assertEqual(v.out()[-1], (0x5C, 61))
        v.on(71)                                   # back under the cap: no steal
        self.assertEqual(v.out()[-1], (0x5B, 71))
        self.assertEqual(sum(1 for t, _ in v.out() if t == 0x5C), 2)

    def test_steal_note_off_keeps_sequencer_timing_word(self):
        v = Voices()
        for n in range(60, 66):
            v.on(n)
        v.on(70, w1=0x1234)
        self.assertEqual(queued(v.u)[6][1], 0x1234, "stolen note's Note Off rides with the new note's timing")

    def test_repeated_note_retriggers_one_voice(self):
        v = Voices()
        v.on(60); v.on(60)                         # pad + sequencer on the same note
        self.assertEqual(v.out(), [(0x5B, 60), (0x5C, 60), (0x5B, 60)])
        v.off(60)                                  # first native off: swallowed (owed by the retrigger)
        self.assertEqual(len(v.out()), 3)
        v.off(60)                                  # second: the voice really ends
        self.assertEqual(v.out()[-1], (0x5C, 60))
        for n in range(61, 67):                    # retrigger did not leak a voice
            v.on(n)
        self.assertNotIn((0x5C, 61), v.out())

    def test_caps_are_per_track(self):
        v = Voices()
        for n in range(60, 66):
            v.on(n, tr=v.t1)
        for n in range(40, 46):
            v.on(n, tr=v.t2, ch=3)
        self.assertEqual(sum(1 for t, _ in v.out() if t == 0x5C), 0, "12 voices over 2 tracks: no steals")

    def test_unknown_note_off_passes(self):
        v = Voices()
        v.off(50)
        self.assertEqual(v.out(), [(0x5C, 50)])

    def test_passthrough_without_region(self):
        v = Voices(region=False)
        for n in range(60, 70):
            v.on(n)
        self.assertEqual(v.out(), [(0x5B, n) for n in range(60, 70)])

    @unittest.skipUnless(POLY_AT, S5)
    def test_pressure_only_for_sounding_notes(self):
        v = Voices()
        for n in range(60, 67):                    # 60 gets stolen
            v.on(n)
        n0 = len(v.out())
        v.pressure(60, 50)                         # stolen: silent
        self.assertEqual(len(v.out()), n0)
        v.pressure(66, 50)                         # sounding: sent
        self.assertEqual(queued(v.u)[-1], (0x5F, 0, 1, 2, 66, 50))

@unittest.skipUnless(POLY_AT, S5)
class PolyHook(unittest.TestCase):
    def run_branch(self, img, r3, note=60, val=50, midi=1, sounding=True):
        """Enter at the handler's `cmp r3,#3` (0x08062464); stop at stock's loop head."""
        v = Voices(img)
        if sounding and img is IMG:
            v.on(note)
        u = v.u
        n0 = len(queued(u))
        sp = STACK - 0x100
        u.mem_write(sp + 0x20, struct.pack("<6I", r3, 0, 0x71, note, val, 0))
        u.mem_write(v.t1 + 0x86D1, bytes([midi]))
        for i, r in enumerate(REGS[:13]):
            u.reg_write(r, 0x01010101 * (i + 1))
        u.reg_write(UC_ARM_REG_R3, r3); u.reg_write(UC_ARM_REG_R6, v.t1); u.reg_write(UC_ARM_REG_R8, ENGINE)
        u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, 0x08062001)  # addr-ok: harness
        u.emu_start(S.POLY_DISPATCH | 1, S.POLY_RESUME, count=40000)
        return u, [u.reg_read(r) for r in REGS], bytes(u.mem_read(0x24000000, 0x80000)), queued(u)[n0:]

    def test_other_types_untouched(self):
        dead = STACK - 0x24000000 - 0x1000
        for r3 in (0x05, 0x0D, 0x0F, 0x30):
            with self.subTest(type=hex(r3)):
                a, b = self.run_branch(CH10, r3), self.run_branch(IMG, r3, sounding=False)
                self.assertEqual(a[1], b[1])
                self.assertEqual(a[2][:dead], b[2][:dead])
                self.assertEqual(b[3], [])

    def test_0x0e_posts_poly_pressure(self):
        u, regs, _, new = self.run_branch(IMG, 0x0E, note=64, val=77)
        self.assertEqual(new, [(0x5F, 0, 1, 2, 64, 77)])
        self.assertEqual(regs, self.run_branch(CH10, 0x0E)[1], "registers at the loop head identical to stock")

    def test_midi_out_off_posts_nothing(self):
        self.assertEqual(self.run_branch(IMG, 0x0E, midi=0)[3], [])

@unittest.skipUnless(POLY_AT, S5)
class LiveAtOut(unittest.TestCase):
    """M2-5c: live pad pressure follows the track's AT Out (M4 table at 0x3003DF80,
    channel 1-based). Off sends nothing; Channel sends the highest held note's
    pressure as event 0x60 and 0 once all are released; no table = poly."""
    def rig(self, mode):
        v = Voices()
        v.u.mem_write(D2 + 0x1F80, struct.pack("<II", 0x31544441, 1) + bytes([1, 3, mode, 1]) + bytes(28))
        return v

    def at(self, v, ms): w32(v.u, CYC, 10_000_000 + ms * 480_000)
    def chan(self, v): return [(e[3], e[4]) for e in queued(v.u) if e[0] == 0x60]

    def test_off_sends_no_pressure(self):
        v = self.rig(1); v.on(60)
        v.pressure(60, 50)
        self.assertEqual([e for e in queued(v.u) if e[0] in (0x5F, 0x60)], [])

    def test_channel_follows_the_highest_held_note(self):
        v = self.rig(2); v.on(60); v.on(64)
        self.at(v, 0); v.pressure(60, 50)
        self.at(v, 10); v.pressure(64, 90)
        self.at(v, 20); v.pressure(60, 70)                         # 90 still the highest: nothing new
        self.assertEqual(self.chan(v), [(2, 50), (2, 90)], "the track channel byte; no poly")
        self.assertFalse([e for e in queued(v.u) if e[0] == 0x5F])
        v.off(64)
        self.at(v, 30); v.pressure(60, 71)                         # 64 released: 71 now the highest
        self.assertEqual(self.chan(v)[-1], (2, 71))
        self.at(v, 40); v.off(60)                                  # last one released: 0 at once
        self.assertEqual(self.chan(v)[-1], (2, 0))

    def test_no_table_is_poly(self):
        v = Voices(); v.on(60)
        v.pressure(60, 50)
        self.assertEqual([e[0] for e in queued(v.u) if e[0] in (0x5F, 0x60)], [0x5F])

@unittest.skipUnless(INTA_LIVE, "S5 on this base: LIVE_SITE (h_live) is not hooked in this image")
class LiveInternal(unittest.TestCase):
    """M2-2b: live pad pressure on an internal track (hook at the pad router's
    post, h_live(bus, ev, router)): Poly passes through untouched; Off drops it;
    Channel gives every pressed note the highest pressure; drum tracks and
    External tracks pass through."""
    BUS_POST, ROUTER, KEY, IDX = S.BUS_POST, 0x24050000, 0x0123, 3  # addr-ok: harness

    def rig(self, mode=None, drum=False, ext=False):
        v = Voices()
        u = v.u
        w32(u, self.ROUTER + S.ROUTER_SEL_TRACK, self.IDX)
        w32(u, self.ROUTER + S.ROUTER_PAD_KEYS + 4 * self.IDX, 0xFFFFFF00 if drum else self.KEY)
        if mode is not None:
            e = bytes(4 * self.IDX) + bytes([0, 2 if ext else 0, mode, 1])
            u.mem_write(D2 + 0x1F80, struct.pack("<II", 0x31544441, 1) + e + bytes(32 - len(e)))
        v.posts = []
        def post(uc, addr, size, _):
            ev = struct.unpack("<6I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))
            v.posts.append((uc.reg_read(UC_ARM_REG_R0), ev[0], ev[2], ev[3], ev[4]))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, post, begin=self.BUS_POST, end=self.BUS_POST)
        return v

    def press(self, v, note, value, ms=0):
        w32(v.u, CYC, 10_000_000 + ms * 480_000)
        at = 0x24070100  # addr-ok: harness
        v.u.mem_write(at, struct.pack("<6I", 0x0E, 0, self.KEY, note, value, 0))
        call(v.u, SYM7["h_live"], ENGINE, at, self.ROUTER)

    def test_poly_and_no_table_pass_through(self):
        for mode in (None, 0):
            v = self.rig(mode)
            self.press(v, 60, 50)
            self.assertEqual(v.posts, [(ENGINE, 0x0E, self.KEY, 60, 50)], mode)

    def test_off_drops_it(self):
        v = self.rig(1)
        self.press(v, 60, 50)
        self.assertEqual(v.posts, [])

    def test_drum_and_external_tracks_pass_through(self):
        for kw in (dict(mode=1, drum=True), dict(mode=1, ext=True)):
            v = self.rig(**kw)
            self.press(v, 60, 50)
            self.assertEqual(len(v.posts), 1, kw)

    def test_channel_highest_to_every_pressed_note(self):
        v = self.rig(2)
        self.press(v, 60, 50, 0)
        self.assertEqual([p[3:] for p in v.posts], [(60, 50)])
        self.press(v, 64, 90, 10)
        self.assertEqual(sorted(p[3:] for p in v.posts[1:]), [(60, 90), (64, 90)])
        n = len(v.posts)
        self.press(v, 64, 0, 20)                                   # released
        self.assertEqual([p[3:] for p in v.posts[n:]], [(60, 50), (64, 0)])

    def test_channel_keeps_a_high_note_apart(self):
        """1.6.8: notes 140 and 12 on one key are two notes (as note & 0x7F they shared one entry and 140's
        release dropped 12's pressure); every post carries the note stock sent."""
        if not HIGH_NOTES:
            self.skipTest("stock never produces a note above 127 on this base")
        v = self.rig(2)
        self.press(v, 12, 50, 0); self.press(v, 140, 90, 10)
        self.assertEqual(sorted(p[3:] for p in v.posts[1:]), [(12, 90), (140, 90)])
        n = len(v.posts)
        self.press(v, 140, 0, 20)                                  # 140 released: 12 keeps its own 50
        self.assertEqual([p[3:] for p in v.posts[n:]], [(12, 50), (140, 0)])

    def test_stale_live_note_stops_counting(self):
        v = self.rig(2)
        self.press(v, 60, 90, 0)
        self.press(v, 64, 40, 400)                                 # 60 not updated for 400 ms
        self.assertEqual([p[3:] for p in v.posts[1:]], [(64, 40)])


@unittest.skipUnless(POLY_AT, S5)
class PolyRate(unittest.TestCase):
    def setUp(self):
        self.v = Voices()
        for n in range(40, 52):
            self.v.on(n) if n < 46 else None
        self.base = len(queued(self.v.u))

    def at(self, ms): w32(self.v.u, CYC, 50_000_000 + ms * 480_000)
    def sent(self): return [(e[4], e[5]) for e in queued(self.v.u)[self.base:]]

    def test_changed_values_only_and_100hz(self):
        self.at(0); self.v.pressure(40, 10)
        self.at(3); self.v.pressure(40, 20)      # < 10 ms since the last send: held back
        self.at(5); self.v.pressure(40, 20)
        self.assertEqual(self.sent(), [(40, 10)])
        self.at(12); self.v.pressure(41, 5)      # any later event flushes the held-back 40:20
        self.assertEqual(self.sent(), [(40, 10), (41, 5), (40, 20)])
        self.at(30); self.v.pressure(40, 20)      # unchanged value: never re-sent
        self.assertEqual(len(self.sent()), 3)

    def test_per_port_budget(self):
        self.at(0)
        for n in range(40, 46):
            self.v.pressure(n, 50)
        self.assertEqual(len(self.sent()), 4, "burst capped at 4 per port")
        self.at(25)
        self.v.pressure(45, 51)
        self.assertGreaterEqual(len(self.sent()), 6, "refilled tokens flush the held-back notes")

from expected_values import BEHAVIOUR
HIGH_NOTES = BEHAVIOUR[S.module]["PAD_NOTE_MAX"] > 127      # 1.6.8: stock's notes reach 163


@unittest.skipUnless(POLY_AT, S5)
class M4MidiTask(unittest.TestCase):
    """Stock's own M4 MIDI-out task (M4_MIDI_TASK) on this base, with M7 events in its ring: its ring read,
    the type dispatch through MIDI_JT and the message builders are stock's; only the sink calls (SEND,
    DONE, FLUSH) are stubbed. SEND records (sink, port, bytes). 1.6.8 recompiled this task (other registers,
    a guard that drops notes above 127), so this is the evidence that our copies of its Note On case fit."""
    RING = 0x24060000                                         # addr-ok: harness

    def run_task(self, *events):
        u = emu(IMG)
        w32(u, S.M4MIDI_RING_PTR, self.RING)
        u.mem_write(S.M4_MIDI_TASK_ARG, b"\x00\x01")           # byte +1: enabled
        for k, e in enumerate(events):
            u.mem_write(self.RING + 0x21C0 + 24 * k, struct.pack("<6I", *e))
        w32(u, self.RING + S.MIDI_RING_RD, 0); w32(u, self.RING + S.MIDI_RING_WR, len(events))
        sent = []
        def send(uc, a, size, _):
            m = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 0x14))
            sent.append((uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R2), m[:m[0x10]]))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        def ret(uc, a, size, _): uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, send, begin=S.M4MIDI_SEND, end=S.M4MIDI_SEND)
        for a in (S.M4MIDI_DONE, S.M4MIDI_FLUSH):
            u.hook_add(UC_HOOK_CODE, ret, begin=a, end=a)
        u.reg_write(UC_ARM_REG_R0, S.M4_MIDI_TASK_ARG); u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(S.M4_MIDI_TASK | 1, SENT, count=200000)
        self.assertEqual(r32(u, self.RING + S.MIDI_RING_RD), len(events), "the task drained the ring")
        self.assertTrue(all(s == S.M4MIDI_SINK for s, _, _ in sent))
        return [(p, m.hex()) for _, p, m in sent]

    def test_note_on_control(self):
        self.assertEqual(self.run_task((0x5B, 0, 1, 2, 60, 100)), [(1, "923c64")])

    def test_poly_pressure(self):
        self.assertEqual(self.run_task((0x5F, 0, 1, 2, 60, 0x55)), [(1, "a23c55")])

    def test_channel_pressure(self):
        """M2-5b: 0x60 -> status 0xD0, channel (word 3), ONE data byte (word 4)."""
        self.assertEqual(self.run_task((0x60, 0, 3, 5, 0x40, 0)), [(3, "d540")])

    def test_mixed_stream_in_order(self):
        evs = [(0x5B, 0, 1, 2, 60, 100), (0x5F, 0, 1, 2, 60, 0x55), (0x60, 0, 1, 2, 0x22, 0), (0x5C, 0, 1, 2, 60, 0)]
        self.assertEqual(self.run_task(*evs), [(1, "923c64"), (1, "a23c55"), (1, "d222"), (1, "823c64")])

    def test_note_above_127(self):
        """1.6.8 stock drops a Note On above 127; our poly pressure for one is dropped the same way (as
        note & 0x7F it would press note 12). 1.6.1 stock masks (no such notes there)."""
        if not HIGH_NOTES:
            self.skipTest("stock never produces a note above 127 on this base")
        self.assertEqual(self.run_task((0x5B, 0, 1, 2, 140, 100), (0x5F, 0, 1, 2, 140, 0x55)), [])

    def test_only_slots_5f_60_changed(self):
        jt = lambda img: [struct.unpack_from("<I", img, S.MIDI_JT - BASE + 4 * i)[0] for i in range(9)]
        a, b = jt(CH10), jt(IMG)
        self.assertEqual((b[4], b[5]), (POLY | 1, CHAN | 1))
        self.assertEqual(a[:4] + a[6:], b[:4] + b[6:])


@unittest.skipUnless(POLY_AT and HIGH_NOTES, "1.6.8 only: notes above 127")
class PressureAbove127(unittest.TestCase):
    """1.6.8's pad router sends the pad's Note On note (router +0x34[pad], up to 163) with its pressure."""

    def test_external_no_pressure_for_a_high_note(self):
        v = Voices(); v.on(12); v.on(140)                      # 140 passes h_note untracked
        n = len(queued(v.u))
        v.pressure(140, 50)
        self.assertEqual([e for e in queued(v.u)[n:] if e[0] == 0x5F], [], "not as note 12")
        v.pressure(12, 60)
        self.assertEqual([(e[0], e[4], e[5]) for e in queued(v.u)[n:] if e[0] == 0x5F], [(0x5F, 12, 60)], "control")


@unittest.skipUnless(POLY_AT, S5)
class M4Handler(unittest.TestCase):
    def trace(self, start):
        self.CALLS = (S.M4MIDI_BEGIN, S.M4MIDI_STATUS, S.M4MIDI_CHANNEL, S.M4MIDI_DATA, S.M4MIDI_SEND, S.M4MIDI_RESULT,
                      S.M4MIDI_DONE)                     # read here: unreviewed on a base where poly_at is S5
        u = emu(IMG)
        log = []
        def hook(uc, addr, size, _):
            log.append((addr, uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
            uc.reg_write(UC_ARM_REG_R0, 0x30000123)  # addr-ok: harness
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in self.CALLS:
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)
        for r, v in ((UC_ARM_REG_R5, 0x30005550), (UC_ARM_REG_R6, 0x30006660), (UC_ARM_REG_R7, 0x40),  # addr-ok: harness
                     (UC_ARM_REG_R8, 0x02), (UC_ARM_REG_R9, 0x01), (UC_ARM_REG_R11, 0x55)):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.emu_start(start | 1, S.M4_LOOP, count=2000)
        return log

    def setUp(self):
        if S.module != "stock_161":
            self.skipTest("register-level trace with 1.6.1's registers (M4MidiTask runs stock's task on every base)")

    def test_same_calls_as_native_note_on_except_status(self):
        native, ours = self.trace(S.M4_NOTE_ON_CASE), self.trace(POLY)
        self.assertEqual(len(native), 8)  # init, status, channel, note, value, send, 2 finishing calls
        self.assertEqual(len(native), len(ours))
        i = [k for k, c in enumerate(native) if c[0] == S.M4MIDI_STATUS][0]
        self.assertEqual((native[i][2], ours[i][2]), (0x90, 0xA0))
        self.assertEqual(native[:i] + native[i + 1:], ours[:i] + ours[i + 1:])

    def test_channel_pressure_handler(self):
        """M2-5b: 0x60 -> status 0xD0, channel (word 3), ONE data byte (word 4)."""
        native, ours = self.trace(S.M4_NOTE_ON_CASE), self.trace(CHAN)
        i = [k for k, c in enumerate(native) if c[0] == S.M4MIDI_STATUS][0]
        want = list(native)
        want[i] = (want[i][0], want[i][1], 0xD0, want[i][3])
        second = [k for k, c in enumerate(native) if c[0] == S.M4MIDI_DATA][1]   # the velocity byte
        del want[second]
        self.assertEqual(ours, want)
        self.assertEqual([c[2] for c in ours if c[0] == S.M4MIDI_DATA], [0x40], "the value from word 4")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
