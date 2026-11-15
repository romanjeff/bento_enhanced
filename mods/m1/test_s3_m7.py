#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""S3 (1.6.8 port): the M7 features inside stock's own sequencer, at each base's own addresses.

    python test_s3_m7.py <stock bento1.bin> <m1 build dir>

Runs on any build with the M7 features (1.6.1 v3.72 and the 1.6.8 S3 build). It extends
test_notemod_route's rig (whose stock code it reuses) from one pass to the WHOLE sequence player.

REAL (stock code from the patched image, at the table's addresses for its base): the sequence player
SEQ_PLAYER per audio block, with what it calls: the loop-boundary function, the timed-note processor,
the pending note-off pass (the 1.6.8 note-off change), note_route, the sequencer Note Off, msg_send,
bus_post; track constructor and message loop for the setup; stock's note clamp PAD_NOTE_CLAMP; stock's
pad Note On / Off PAD_NOTE_ON / PAD_NOTE_OFF (the live pad path through two poly cap sites).
OURS (as patched into the image): retrigger accept stub and emit trampoline (with curve_emit, mod_block,
nm_block), Note Mod nm_on / nm_off / the off stubs, the condition gate and loop hooks, the poly cap
stubs and h_note. STUBBED: the tick math (1 tick = 1 sample, as test_notemod_route), SAMPLE_TO_TICK
(identity), voice start. Unicorn's Cortex-M7 has no double-precision FPU: F64 below executes the
player's few f64 instructions in Python.

Expectations that differ between bases come from expected_values.BEHAVIOUR (from the disassembly,
independent of the tables): where a sequenced note-off lands in the audio block, stock's note clamp,
the extra loop boundary of 1.6.8's play count.
"""
import re, struct, sys, unittest
from pathlib import Path
from unicorn.arm_const import *
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
sys.path.insert(0, str(Path(__file__).resolve().parent))
BUILD_DIR = sys.argv[2]
import test_notemod_route as R      # reads (and drops) argv[1:3]: the stock image and the build
from expected_values import BEHAVIOUR
import build

S = R.S
B = BEHAVIOUR[S.module]
BLK = 64
SCRATCH = 0x24060000   # addr-ok: harness
SLICE_COUNTERS = S.D2_SLICE + 0x660        # m7_conditions.c COUNTERS (16 x 32 B)
STEP_TAG = 0xB6                            # diag_shared.h step_make
P2 = R.P2                                  # Note Mod: square, range 1, +2 scale steps in C major


class F64:
    """The double-precision instructions the player uses, decoded on first execution."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)

    def __init__(self):
        self.ops = {}

    @staticmethod
    def reg(name):
        return getattr(sys.modules["unicorn.arm_const"], f"UC_ARM_REG_{name.upper()}")

    def getd(self, u, n):
        return struct.unpack("<d", struct.pack("<Q", u.reg_read(self.reg(f"d{n}")) & (2 ** 64 - 1)))[0]

    def setd(self, u, n, v):
        u.reg_write(self.reg(f"d{n}"), struct.unpack("<Q", struct.pack("<d", v))[0])

    def step(self, u, a, size):
        op = self.ops.get(a, 0)
        if op == 0:
            i = next(self.md.disasm(bytes(u.mem_read(a, size)), a, count=1), None)
            op = self.ops[a] = (i.mnemonic, i.op_str) if i and (".f64" in i.mnemonic or (
                i.mnemonic == "vmov" and re.match(r"r\w+, r\w+, d\d+$", i.op_str))) else None
        if not op:
            return False
        mn, args = op[0], [x.strip() for x in op[1].split(",")]
        num = lambda x: int(x[1:])
        if mn == "vmov.f64" and args[1].startswith("#"):
            self.setd(u, num(args[0]), float(args[1][1:]))
        elif mn == "vmov.f64":
            self.setd(u, num(args[0]), self.getd(u, num(args[1])))
        elif mn == "vcvt.f64.f32":
            s = struct.unpack("<f", struct.pack("<I", u.reg_read(self.reg(args[1])) & 0xFFFFFFFF))[0]
            self.setd(u, num(args[0]), s)
        elif mn in ("vadd.f64", "vsub.f64", "vmul.f64", "vdiv.f64"):
            x, y = self.getd(u, num(args[1])), self.getd(u, num(args[2]))
            self.setd(u, num(args[0]), {"vadd.f64": x + y, "vsub.f64": x - y, "vmul.f64": x * y, "vdiv.f64": x / y}[mn])
        elif mn == "vmov":
            q = struct.unpack("<Q", struct.pack("<d", self.getd(u, num(args[2]))))[0]
            u.reg_write(self.reg(args[0]), q & 0xFFFFFFFF); u.reg_write(self.reg(args[1]), q >> 32)
        else:
            raise RuntimeError(f"F64: {mn} {op[1]} at {a:#x} is not emulated")
        u.reg_write(UC_ARM_REG_PC, (a + size) | 1)
        return True


class Player(R.Rig):
    """test_notemod_route's rig, playing a clip through the whole stock player block by block."""
    def __init__(self, keys=(R.F4,), ext=False, step_idx=10, clip=960, mode=0):
        self.f64, self.loops, self.routes, self.cur = F64(), 0, [], 0
        super().__init__(keys=keys)
        self.param(0xA1, R.F4, 0)                              # pad 0 plays the sampler (key 0xF4)
        if ext:
            self.param(0x10, 3)                                # MIDI channel: an External track
        u = self.u
        R.w32(u, R.SEQ + 0, step_idx); R.w32(u, R.SEQ + 0x30, clip); R.w32(u, R.SEQ + 0x34, 0)
        R.w32(u, R.T + 0x14, 0xFFFFFFFF); R.w32(u, R.T + 0x18, 0); R.w32(u, R.T + 0x1C, 0)   # from a restart
        u.mem_write(R.T + 0x30, bytes([mode]))
        self.bus, self.midi = [], []                           # (type, abs sample, note) that landed

    def hook(self, u, a, size, _):
        if self.f64.step(u, a, size):
            return
        if a == S.COND_LOOP_SITE:
            self.loops += 1
        elif a == S.NOTE_ROUTE:
            self.routes.append(self.cur + u.reg_read(UC_ARM_REG_R3))
        elif a == S.SAMPLE_TO_TICK:
            r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
            u.mem_write(r(0), bytes(u.mem_read(r(2), 8))); self.ret(r(0)); return
        super().hook(u, a, size, _)

    def notes(self, recs, amount=0):
        """recs: dict(t, len, note, pad=0, prob=0, word=0) (24-byte records, as the processor reads them)."""
        u = self.u
        for i, x in enumerate(recs):
            u.mem_write(R.POOL + 24 * i, struct.pack("<BBHIII", 1, x.get("prob", 0), x.get("pad", 0), x["t"], x["len"], 0)
                        + struct.pack("<HHI", x["note"], 100, x.get("word", 0)))
            u.mem_write(R.IDX + 2 * i, struct.pack("<H", i))
        R.w32(u, R.SEQ + 0xC, len(recs)); R.w32(u, R.SEQ + 0x10, 0)
        self.nm(amount)

    def take(self, base):
        u = self.u; n = R.r32(u, base + 0x600)
        out = [bytes(u.mem_read(base + 24 * i, 24)) for i in range(n)]
        R.w32(u, base + 0x600, 0)
        return out

    def play(self, blocks):
        u = self.u
        for _ in range(blocks):
            self.cur = self.now
            u.mem_write(R.CLOCK, struct.pack("<q", self.now))
            self.call(S.SEQ_PLAYER, R.T, R.CLOCK, R.ROUTER, R.CLOCK + 0x100, count=8000000)
            for i, k in enumerate(self.keys):
                if k != R.UI:
                    for m in self.take(R.MB + 0x604 * i):
                        if m[0] in (0x12, 0x13):
                            t, key, note = struct.unpack("<iII", m[4:16])
                            self.bus.append(("ON" if m[0] == 0x12 else "OFF", self.cur + t, note, key))
            for m in self.take(R.ROUTER + 0x7C8):
                if m[0] in (0x5B, 0x5C):
                    t = struct.unpack("<i", m[4:8])[0]
                    self.midi.append(("ON" if m[0] == 0x5B else "OFF", self.cur + t, struct.unpack("<I", m[16:20])[0]))
            self.now += BLK
        return self

    def pad(self, on, note, pad=0, vel=100):
        """Stock's pad Note On / Off (PAD_NOTE_ON / PAD_NOTE_OFF, the live pad path of an External track)
        with a real event: ev +0xC note, +0x10 velocity, +0x14 pad. What reached the MIDI queue lands
        in self.midi (stock posts these with timing 0)."""
        u = self.u
        u.mem_write(SCRATCH + 0x100, bytes(12) + struct.pack("<IIH6x", note, vel, pad))
        self.call(S.PAD_NOTE_ON if on else S.PAD_NOTE_OFF, R.T, SCRATCH + 0x100, R.ROUTER)
        for m in self.take(R.ROUTER + 0x7C8):
            if m[0] in (0x5B, 0x5C):
                t = struct.unpack("<i", m[4:8])[0]
                self.midi.append(("ON" if m[0] == 0x5B else "OFF", self.now + t, struct.unpack("<I", m[16:20])[0]))
        return self

    def curves(self, samples=(10, 20, 30, 40)):
        """One recorded curve, handle 1, as the M4 publishes it (CurveState at SLICE+0x1840, test_retrig)."""
        u = self.u
        R.w32(u, CURVE_TABLE + 4, 0)
        u.mem_write(CURVE_POOL, struct.pack("<H", len(samples)) + bytes(samples))
        u.mem_write(S.D2_SLICE + 0x1840, struct.pack("<8I", 0x31565243, 1, CURVE_POOL, 8, CURVE_TABLE, 2, 0, 0))
        return self

    def curve_stats(self):
        """Region curve_starts, curve_skipped (REGION_CURVE_STATS = 32: starts, sent, late, skipped)."""
        s = struct.unpack("<4I", self.u.mem_read(S.REGION + 32, 16))
        return s[0], s[3]


CURVE_POOL, CURVE_TABLE = 0xC6180000, 0xC61C0000   # addr-ok: harness (test_retrig's), in the mapped SDRAM
CURVE_WORD = 0xB7 << 24 | 1 << 3                   # diag_shared.h step_make: curve tag, handle 1
RETRIG_2 = STEP_TAG << 24 | 2 << 13                # two hits at 1/16 (test_retrigger_word_two_hits_at_1_16)


def off_lands(t_off):
    """Where stock puts a sequenced note-off that is due at sample t_off (expected_values.BEHAVIOUR)."""
    start = t_off // BLK * BLK                 # the first block whose end is past it
    timing = t_off - start if B["NOTE_OFF_TIMING"] == "offset" else start + BLK - t_off
    return start + timing


class NoteOffTiming(unittest.TestCase):
    """The release note 'sequenced note-offs land at the right time within the audio block' (1.6.8):
    NM_OFF_SITE_1 passes a different timing. Note Mod rewrites only the note and pad there, so a moved
    note's off keeps stock's timing for this base and reaches the moved pitch on the right bus key."""

    def test_stock_note_off_lands_where_this_base_puts_it(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60)]); g.play(4)
        self.assertEqual(g.bus, [("ON", 10, 60, R.F4), ("OFF", off_lands(110), 60, R.F4)])
        self.assertEqual(off_lands(110), {"offset": 110, "mirrored": 82}[B["NOTE_OFF_TIMING"]])

    def test_note_mod_off_reaches_the_moved_note_at_stock_time(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60)], amount=P2); g.play(4)
        self.assertEqual(g.bus, [("ON", 10, 64, R.F4), ("OFF", off_lands(110), 64, R.F4)])

    def test_external_note_mod_midi_off_at_stock_time(self):
        g = Player(ext=True); g.notes([dict(t=10, len=100, note=60)], amount=P2); g.play(4)
        self.assertEqual(g.midi, [("ON", 10, 64), ("OFF", off_lands(110), 64)])

    def test_two_moved_notes_each_off_on_its_own_pitch(self):
        recs = [dict(t=10, len=60, note=60), dict(t=74, len=40, note=62)]
        g = Player(); g.notes(recs, amount=P2); g.play(4)
        ons = {t: n for k, t, n, _ in g.bus if k == "ON"}
        offs = {t: n for k, t, n, _ in g.bus if k == "OFF"}
        self.assertEqual(sorted(ons), [10, 74])
        self.assertEqual(offs, {off_lands(70): ons[10], off_lands(114): ons[74]})
        self.assertNotEqual(ons[10], 60, "Note Mod moved the notes")


class Engines(unittest.TestCase):
    """Conditions, retriggers and the loop counter with real stock frames around our hooks."""

    def test_clip_loops_four_times_in_48_blocks(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60)]); g.play(48)
        self.assertEqual(g.loops, 4)
        self.assertEqual(g.routes, [10, 970, 1930, 2890])

    def test_condition_word_1_of_2_plays_every_other_pass(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60, word=STEP_TAG << 24 | 1 << 17)]); g.play(48)
        self.assertEqual(g.routes, [10, 1930])

    def test_legacy_condition_byte_1_of_2(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60, prob=100)]); g.play(48)
        self.assertEqual(g.routes, [10, 1930])

    def test_retrigger_word_two_hits_at_1_16(self):
        g = Player(); g.notes([dict(t=10, len=100, note=60, word=STEP_TAG << 24 | 2 << 13)]); g.play(12)
        self.assertEqual(g.routes, [10, 250, 490])
        self.assertEqual([t for k, t, n, _ in g.bus if k == "ON"], [10, 250, 490])

    def test_play_count_mode_loop_boundaries_per_base(self):
        """1.6.8 wraps the step count after 256 steps (play count) and calls the loop boundary: cond_loop
        counts that as a pass, where 1.6.1 never did. 60-tick steps, 300 blocks = 320 steps."""
        g = Player(step_idx=14, mode=1); g.notes([dict(t=10, len=20, note=60)]); g.play(300)
        self.assertEqual(g.loops, B["LOOP_CALLS_COUNT_MODE_320"])
        c = [struct.unpack_from("<8I", g.u.mem_read(SLICE_COUNTERS + 32 * i, 32)) for i in range(16)]
        mine = [x for x in c if x[0] == R.T]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0][3], 0, "cond_loop's pass count: 1.6.8's 256-step wrap is not a pass (1.6.1 phase)")
        self.assertEqual(mine[0][4], 1, "the restart was seen")

    def test_play_count_condition_keeps_the_161_phase(self):
        """(+0x30 count-on mode, which S3 took for Play Count: see test_count_on_mode_does_not_loop_the_clip.)
        A 2:2 condition word in a Play Count clip never plays on 1.6.1 (no pass after the restart); the same
        on 1.6.8, whose 256-step wrap would otherwise make it play from step 256 (sample 15370) on.
        Control: 1:2 on the same note plays."""
        for cond, want_any in ((2, False), (1, True)):    # cond = value - 99: 1 = 1:2, 2 = 2:2
            g = Player(step_idx=14, mode=1)
            g.notes([dict(t=10, len=20, note=60, word=STEP_TAG << 24 | cond << 17)]); g.play(300)
            with self.subTest(cond=cond):
                self.assertEqual(bool(g.routes), want_any, g.routes)

    def test_count_on_mode_does_not_loop_the_clip(self):
        """What track byte +0x30 is (S4+S5 review round): with it set, a 16-step clip (960 ticks) does NOT loop at its
        length on either base: the note at tick 10 plays once and, on 1.6.8 only, again after the 256-step wrap. A
        Play Count clip repeats its content, so +0x30 is not 1.6.8's Play Count (which 1.6.1 also lacks, while this
        mode is in both players); with +0x30 set the track also posts its live notes to the M4's recording handler
        (REC_MSG_5 / REC_MSG_6), so it is most likely open-length recording. The cond_loop rule (the wrap is not a
        pass) is about this mode; Play Count clips loop through the normal boundary (not traced: device check)."""
        g = Player(step_idx=14, mode=1); g.notes([dict(t=10, len=20, note=60)]); g.play(300)
        self.assertEqual(g.routes[0], 10)
        self.assertEqual([t for t in g.routes[1:] if t < 256 * 60], [], "no repeat at the clip length (960 ticks)")
        n = Player(step_idx=14); n.notes([dict(t=10, len=20, note=60)]); n.play(48)
        self.assertGreater(len(n.routes), 1, "control: the same clip with +0x30 clear loops at its length")


class PolyCap(unittest.TestCase):
    """The poly cap on an External track, with the five msg_send sites in real stock frames."""

    def test_seventh_note_steals_the_oldest_and_its_own_off_is_swallowed(self):
        recs = [dict(t=10 + 8 * i, len=300, note=60 + i) for i in range(7)]
        g = Player(ext=True); g.notes(recs); g.play(8)
        self.assertEqual([x for x in g.midi if x[0] == "ON"], [("ON", 10 + 8 * i, 60 + i) for i in range(7)])
        offs = [x for x in g.midi if x[0] == "OFF"]
        self.assertEqual(offs[0], ("OFF", 58, 60), "stolen at the 7th Note On")
        self.assertEqual(sorted(n for _, _, n in offs), list(range(60, 67)), "60's native off swallowed")

    def test_pad_path_seventh_pad_steals_and_its_release_is_swallowed(self):
        """The device checklist's poly cap test (hold 7 pads on an External track) runs stock's pad
        Note On / Off, not the sequencer: POLYCAP_PAD_ON_SITE / _OFF_SITE in their real frames."""
        g = Player(ext=True)
        for n in range(60, 67):
            g.pad(True, n)
        self.assertEqual(g.midi, [("ON", 0, n) for n in range(60, 66)] + [("OFF", 0, 60), ("ON", 0, 66)])
        g.midi = []
        for n in range(60, 67):
            g.pad(False, n)
        self.assertEqual(g.midi, [("OFF", 0, n) for n in range(61, 67)], "60 was stolen: its release is swallowed")

    def test_pad_path_retriggered_note_is_not_doubled(self):
        """Note 60 on twice (two pads on one note), then both released: an Off before the second On,
        the first release swallowed (owed), the second goes out. One voice, nothing left hanging."""
        g = Player(ext=True)
        g.pad(True, 60, pad=0).pad(True, 60, pad=1).pad(False, 60, pad=0)
        self.assertEqual(g.midi, [("ON", 0, 60), ("OFF", 0, 60), ("ON", 0, 60)])
        g.pad(False, 60, pad=1)
        self.assertEqual(g.midi[3:], [("OFF", 0, 60)])


class RetrigMidi(unittest.TestCase):
    """Control for NotesAbove127: the same ratchet on note 60 does send its MIDI hits."""

    def test_retrig_midi_hits_on_an_external_track(self):
        g = Player(ext=True); g.notes([dict(t=10, len=100, note=60, word=RETRIG_2)]); g.play(12)
        self.assertEqual([t for k, t, n in g.midi if k == "ON" and n == 60], [10, 250, 490])


class CurveStart(unittest.TestCase):
    """Control for NotesAbove127: a curve word on note 60 starts its players. The rig's track has MIDI
    out and an instrument (sampler key 0xF4), so two: External (poly AT) and internal (inta_curve)."""

    def test_curve_word_starts_a_player(self):
        g = Player(ext=True).curves(); g.notes([dict(t=10, len=100, note=60, word=CURVE_WORD)]); g.play(4)
        self.assertEqual(g.curve_stats(), (2, 0), "curve_starts, curve_skipped")


class NoteClamp(unittest.TestCase):
    """Stock's note + delta clamp, run on each base: 0..127 on 1.6.1, 0..163 on 1.6.8."""

    def test_stock_note_clamp(self):
        g = R.Rig()
        def clamp(note, delta):
            g.u.mem_write(SCRATCH, struct.pack("<H", note))
            v = g.call(S.PAD_NOTE_CLAMP, SCRATCH, delta & 0xFFFFFFFF)
            return v - (1 << 32) if v & 0x80000000 else v
        top = B["PAD_NOTE_MAX"]
        self.assertEqual([clamp(100, 20), clamp(100, 50), clamp(160, 10), clamp(5, -10)],
                         [120, min(150, top), min(170, top), 0])


class NotesAbove127(unittest.TestCase):
    """1.6.8's note clamp allows 0..163 and its M4 drops MIDI notes above 127 (BE_NOTES_ABOVE_127)."""

    def setUp(self):
        if B["PAD_NOTE_MAX"] <= 127:
            self.skipTest("stock never produces a note above 127 on this base")

    def test_poly_cap_passes_a_high_note_untracked(self):
        """Tracked as note & 0x7F, note 140 would be 12: the real 12 would retrigger it and its own
        Note Off would be swallowed (hanging)."""
        g = Player(ext=True); g.notes([dict(t=10, len=300, note=140), dict(t=74, len=40, note=12)]); g.play(8)
        self.assertEqual(g.midi, [("ON", 10, 140), ("ON", 74, 12), ("OFF", off_lands(114), 12), ("OFF", off_lands(310), 140)])

    def test_pad_path_high_note_neither_steals_nor_is_counted(self):
        """Stock's pad router clamps to 0..163 on 1.6.8, so the pad Note On site can post 140. Six held
        notes plus 140: nothing stolen (tracked, 140 would be the 7th voice and steal 60)."""
        g = Player(ext=True)
        for n in list(range(60, 66)) + [140]:
            g.pad(True, n)
        self.assertEqual(g.midi, [("ON", 0, n) for n in list(range(60, 66)) + [140]])
        g.midi = []
        g.pad(False, 140).pad(False, 60)
        self.assertEqual(g.midi, [("OFF", 0, 140), ("OFF", 0, 60)])

    def test_retrig_sends_no_midi_hit_for_a_high_note(self):
        """A ratchet on note 140 of an External track: stock's own On / Off go out, the MIDI hits do
        not (as note & 0x7F = 12 they would hit or hang a real 12). Control: RetrigMidi below."""
        g = Player(ext=True); g.notes([dict(t=10, len=100, note=140, word=RETRIG_2)]); g.play(12)
        self.assertEqual(g.routes, [10, 250, 490], "the hits still play on the track's instrument")
        self.assertEqual(g.midi, [("ON", 10, 140), ("OFF", off_lands(110), 140)])

    def test_no_curve_for_a_high_note(self):
        """A curve word on note 140 starts no player (it would send poly AT on note 12). Control:
        CurveStart below."""
        g = Player(ext=True).curves(); g.notes([dict(t=10, len=100, note=140, word=CURVE_WORD)]); g.play(4)
        self.assertEqual(g.curve_stats(), (0, 1), "curve_starts, curve_skipped")

    def test_note_mod_leaves_a_high_note_unshifted(self):
        g = Player(ext=True); g.notes([dict(t=10, len=100, note=140)], amount=P2); g.play(4)
        self.assertEqual(g.midi, [("ON", 10, 140), ("OFF", off_lands(110), 140)])


class GroupSplit(unittest.TestCase):
    """S3 split poly / cond / inta into the part S3 ports and the S4/S5 part (build.py)."""
    S3 = {"poly_cap", "retrig", "cond_engine", "nmod", "inta_curve", "cond_ui", "poly_at", "inta_live", "step", "hold"}   # + S4/S5
    LATER = {"mx"}                             # deferred (Jeff): left out by default on 1.6.8 (BASE_SETTINGS default_off)
    # S6a: what each base leaves out by default (1.6.1: analog, which exists on 1.6.8 only; 1.6.8: mx), typed here
    OFF_BY_DEFAULT = {"stock_161": {"analog", "note0", "drums"}, "stock_168": {"mx"}}   # BE4.22: note0, S6c: drums are 1.6.8 only

    def test_aliases_keep_the_old_names(self):
        self.assertEqual(build.ALIASES, {"poly": ("poly_cap", "poly_at"), "cond": ("cond_engine", "cond_ui"),
                                         "inta": ("inta_curve", "inta_live")})
        groups = {g for g, _ in build.PATCH_GROUPS}
        self.assertTrue(self.S3 | self.LATER <= groups)
        self.assertFalse({"poly", "cond", "inta"} & groups)

    def test_each_s3_part_plans_with_every_later_part_off(self):
        for g in sorted(self.S3):
            with self.subTest(group=g):
                inc, omitted, refused, _ = build.plan(S, self.OFF_BY_DEFAULT[S.module] | (self.S3 - {g}))
                self.assertEqual(refused, {})
                self.assertIn(g, inc)

    def test_default_build_lists_every_later_part(self):
        inc, omitted, refused, _ = build.plan(S, set())
        later = self.OFF_BY_DEFAULT[S.module]
        self.assertEqual(set(refused), later)
        bs = build.BASE_SETTINGS[S.module]
        self.assertEqual(set(bs.get("default_off", ())) - set(bs.get("omit_off", ())), later, "build() leaves them out by default")
        if S.module == "stock_168":                               # BE4.40: the carrier drums are off and left out unless asked for
            self.assertEqual(set(bs.get("omit_off", ())), {"drums"})
            self.assertIn("drums", omitted)
        msg = build.refuse_message(S, refused, omitted)
        self.assertIn("Build without them: BE_DISABLE=" + ",".join(sorted(later)), msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
