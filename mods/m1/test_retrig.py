# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for the retrigger scheduler (settings from the per-step word).

    python test_retrig.py <stock 1.6.1 bento1.bin> <build dir>

Runs the real patched sites: the accept stub around stock's bl note_route at
0x08060B9A (with a caller frame laid out as stock's timed-note processor has
it) and the entry trampoline of the timed-note processor at 0x08060820.
Stock's note_route (0x0805F8E0) is replaced by a recorder; the tick -> sample
conversion (0x080A2200) by offset = tick - clock[0] (1 tick = 1 sample).
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
CFG, REGION, MAGIC = 0x3003C1E0, 0xC6100000, 0x31474144
CLOCK = 0x24071000
ROUTER = 0x24060000
S = 0x2407E000                       # caller stack pointer at the bl site
NOTE_ROUTE, TO_OFFSET = 0x0805F8E0, 0x080A2200
ACCEPT, AFTER_ACCEPT, EMIT, AFTER_EMIT = 0x08060B9A, 0x08060B9E, 0x08060820, 0x08060824

OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()

WORD = (0xB5 << 24) | (3 << 13) | ((4 + 1) << 9) | ((-60) & 0xFF)   # 3 hits, 1/16T, -60 %

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

class Rig:
    def __init__(self, region=True):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG); u.mem_write(SENT, b"\xfe\xe7")
        for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x200000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        if region:
            u.mem_write(CFG, struct.pack("<8I", MAGIC, REGION, 0x1000, 1, 0, 0, 0, 0))
        self.t1, self.t2 = 0x24064000, 0x2406D000
        for t in (self.t1, self.t2):
            u.mem_write(t, bytes(0x8800)); u.mem_write(t + 0x86D1, b"\x01\x01")   # MIDI out + internal on
        self.calls = []
        u.hook_add(UC_HOOK_CODE, self.hook)

    def hook(self, u, addr, size, _):
        r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
        if addr == NOTE_ROUTE:
            sp = u.reg_read(UC_ARM_REG_SP)
            self.calls.append(dict(track=r(0), note=r(1), vel=r(2), timing=r(3), pad=r32(u, sp) & 0xFFFF,
                                   router=r32(u, sp + 4), midi=bytes(u.mem_read(r(0) + 0x86D1, 1))[0]))
        elif addr == TO_OFFSET:
            tick = r32(u, r(1)); off = (tick - r32(u, r(0))) & 0xFFFFFFFF
            u.reg_write(UC_ARM_REG_R0, off)
        else:
            return
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def accept(self, track, pad, tick, note=36, vel=100, clock0=1000, word=None):
        u = self.u
        w32(u, CLOCK, clock0)
        u.mem_write(S, bytes(0x90))
        w32(u, S, pad); w32(u, S + 4, ROUTER); w32(u, S + 0x20, tick); w32(u, S + 0x24, 0)
        w32(u, S + 0x3C, (WORD if pad == 15 else 0) if word is None else word)
        for i, v in enumerate((track, note, vel, tick - clock0)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_R6, CLOCK); u.reg_write(UC_ARM_REG_SP, S)
        u.emu_start(ACCEPT | 1, AFTER_ACCEPT, count=200000)
        assert u.reg_read(UC_ARM_REG_SP) == S and u.reg_read(UC_ARM_REG_R6) == CLOCK

    def emit(self, track, start, length, clock0=1000):
        u = self.u
        w32(u, CLOCK, clock0); w32(u, S, ROUTER)
        for i, v in enumerate((track, CLOCK, start, length)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, S)
        u.emu_start(EMIT | 1, AFTER_EMIT, count=200000)
        assert u.reg_read(UC_ARM_REG_SP) == S - 36, "replayed push.w {r4-r11, lr}"
        assert [u.reg_read(UC_ARM_REG_R0 + i) for i in range(4)] == [track, CLOCK, start, length]

    def hits(self): return [(c["timing"], c["vel"]) for c in self.calls[1:]]

class Retrig(unittest.TestCase):
    def test_stock_note_is_routed_unchanged(self):
        g = Rig(); g.accept(g.t1, pad=3, tick=1000)
        self.assertEqual(g.calls, [dict(track=g.t1, note=36, vel=100, timing=0, pad=3, router=ROUTER, midi=1)])

    def test_burst_lands_in_the_right_blocks_with_the_ramp(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.emit(g.t1, 0, 100); self.assertEqual(g.hits(), [])
        g.emit(g.t1, 100, 100); self.assertEqual(g.hits(), [(160, 80)])
        g.emit(g.t1, 200, 200); self.assertEqual(g.hits(), [(160, 80), (320, 60)])
        g.emit(g.t1, 400, 200); self.assertEqual(g.hits(), [(160, 80), (320, 60), (480, 40)])
        g.emit(g.t1, 600, 2000); self.assertEqual(len(g.hits()), 3, "burst finished")

    def test_hits_go_to_internal_sound_only(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 1000)
        self.assertEqual([c["midi"] for c in g.calls], [1, 0, 0, 0])
        self.assertEqual(bytes(g.u.mem_read(g.t1 + 0x86D1, 1))[0], 1, "MIDI flag restored")
        self.assertTrue(all(c["pad"] == 15 and c["note"] == 36 and c["router"] == ROUTER for c in g.calls))

    def test_other_pads_and_tracks_unaffected(self):
        g = Rig(); g.accept(g.t1, pad=14, tick=1000); g.emit(g.t1, 0, 2000)
        self.assertEqual(g.hits(), [])
        g.accept(g.t1, pad=15, tick=1000); g.emit(g.t2, 0, 2000)
        self.assertEqual(len(g.calls), 2, "a burst only fires from its own track's processor")

    def test_next_hit_on_the_same_pad_cuts_the_burst(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.emit(g.t1, 100, 100)                              # hit 1 at 160
        g.accept(g.t1, pad=15, tick=1250)                   # next step on pad 16 (new burst)
        g.emit(g.t1, 200, 1000)
        self.assertEqual([c["timing"] for c in g.calls], [0, 160, 250, 410, 570, 730],
                         "stock 0, hit 160, stock 250 (cuts), new burst 410/570/730; old 320/480 never fire")

    def test_another_pad_does_not_cut(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.accept(g.t1, pad=2, tick=1100)
        g.emit(g.t1, 0, 1000)
        self.assertEqual([c["timing"] for c in g.calls if c["pad"] == 15][1:], [160, 320, 480])

    def test_late_burst_is_dropped(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.emit(g.t1, 5000, 100)                             # far past (transport stopped)
        self.assertEqual(g.hits(), [])

    def test_slightly_late_hit_is_posted_at_block_start(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.emit(g.t1, 170, 100)                              # hit 1 (160) is 10 samples late
        self.assertEqual(g.hits()[0], (170, 80))

    def test_settings_come_from_the_word(self):
        g = Rig(); g.accept(g.t1, pad=3, tick=1000, word=(0xB5 << 24) | (2 << 13) | ((0 + 1) << 9) | 20)   # 2 x 1/8, +20 %
        g.emit(g.t1, 0, 2000)
        self.assertEqual(g.hits(), [(480, 110), (960, 120)])
        g = Rig(); g.accept(g.t1, pad=15, tick=1000, word=0x51000005)   # Lab lock word: not ours
        g.emit(g.t1, 0, 2000)
        self.assertEqual(g.hits(), [])

    def test_v2_words_and_lock_bits_dont_matter_to_playback(self):
        w = (0xB6 << 24) | (2 << 13) | ((4 + 1) << 9) | ((-20 // 5 + 21) << 3) | 5     # 2 x 1/16T, -20 %, locks
        g = Rig(); g.accept(g.t1, pad=3, tick=1000, word=w); g.emit(g.t1, 0, 2000)
        self.assertEqual(g.hits(), [(160, 90), (320, 80)])

    def test_no_region_no_bursts(self):
        g = Rig(region=False); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 2000)
        self.assertEqual(len(g.calls), 1)

class CurvePlayback(unittest.TestCase):
    """M2-2: a note whose word carries a curve handle (tag 0xB7) plays its recorded
    pressure as poly aftertouch (0x5F) on its External track. v3.27: per block only
    the latest due value, sent when the note's share of its port allows."""
    CURVES, POOL, TABLE, MSG_SEND, CYC = 0x3003D840, 0xC6180000, 0xC61C0000, 0x08065600, 0xE0001004
    MS = 480000

    def rig(self, samples=(10, 10, 20, 30)):
        g = Rig()
        u = g.u
        w32(u, self.TABLE + 4, 0)                                   # handle 1 -> pool offset 0
        u.mem_write(self.POOL, struct.pack("<H", len(samples)) + bytes(samples))
        u.mem_write(self.CURVES, struct.pack("<8I", 0x31565243, 1, self.POOL, 8, self.TABLE, 2, 0, 0))
        g.sent = []
        def send(uc, addr, size, _):
            ev = struct.unpack("<6I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))
            g.sent.append((uc.reg_read(UC_ARM_REG_R0), ev[0], ev[2], ev[4], ev[5], r32(uc, self.CYC)))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, send, begin=self.MSG_SEND, end=self.MSG_SEND)
        w32(u, self.CYC, 1000 * self.MS)
        return g

    def start(self, g, track=None, note=60, word=(0xB7 << 24) | (1 << 3), tick=1000):
        g.accept(g.t1 if track is None else track, pad=0, tick=tick, note=note, word=word)
        w32(g.u, REGION + 48, 0x2403A118)                           # MIDI-out queue seen at a note-on

    def emit(self, g, track, start, length, ms):
        w32(g.u, self.CYC, ms * self.MS)
        g.emit(track, start, length)

    def values(self, g):
        return [(x[3], x[4]) for x in g.sent]

    def test_latest_due_value_per_block(self):
        g = self.rig(); self.start(g)
        self.emit(g, g.t1, 0, 40, 1010)                             # k0 10, k1 10, k2 20 due: 20 goes
        self.assertEqual(self.values(g), [(60, 20)])
        self.assertEqual(r32(g.u, REGION + 56), 1, "the unsent 10 was thinned")
        self.emit(g, g.t1, 40, 40, 1030)                            # k3 30: the last value
        self.assertEqual(self.values(g)[-1], (60, 30))
        self.assertEqual(r32(g.u, REGION + 32), 1, "curve_starts")
        n = len(g.sent); self.emit(g, g.t1, 80, 40, 1050)
        self.assertEqual(len(g.sent), n, "finished after its last sample")

    def test_a_note_waits_its_gap(self):
        g = self.rig(samples=(10, 20, 30, 40, 50, 60, 70, 80))
        self.start(g)
        self.emit(g, g.t1, 0, 16, 1010)                             # k0, k1 due -> 20
        self.emit(g, g.t1, 16, 15, 1011)                            # 1 ms later: k2 30 waits (8 ms gap)
        self.assertEqual(self.values(g), [(60, 20)])
        self.emit(g, g.t1, 31, 15, 1020)                            # 9 ms after the send: latest (k3 40)
        self.assertEqual(self.values(g)[-1], (60, 40))

    def test_other_tracks_and_plain_notes_send_nothing(self):
        g = self.rig(); self.start(g, word=0)
        self.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual(g.sent, [])
        g = self.rig(); self.start(g)
        self.emit(g, g.t2, 0, 100, 1010)                            # another track's block
        self.assertEqual(g.sent, [])

    def test_midi_out_off_or_bad_handle_starts_nothing(self):
        g = self.rig(); g.u.mem_write(g.t1 + 0x86D1, bytes(1)); self.start(g)
        self.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual(g.sent, [])
        g = self.rig(); self.start(g, word=(0xB7 << 24) | (5 << 3))   # handle 5 >= next
        self.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual((g.sent, r32(g.u, REGION + 44)), ([], 1), "skipped")

    def test_late_player_is_dropped(self):
        g = self.rig(); self.start(g)
        self.emit(g, g.t1, 5000, 40, 1010)                          # transport jumped far ahead
        self.assertEqual((g.sent, r32(g.u, REGION + 40)), ([], 1), "late")


class CurveDestMode(unittest.TestCase):
    """M2-5: the player looks its track's MIDI port/channel up in the M4's table;
    AT Out Off (or Channel, until v3.31) starts nothing; no match = poly."""
    def table(self, g, port, ch, mode):
        g.u.mem_write(0x3003DF80, struct.pack("<II", 0x31544441, 1) + bytes([port, ch, mode, 1]) + bytes(28))

    def test_off_for_this_destination(self):
        pb = CurvePlayback(); g = pb.rig()
        g.u.mem_write(g.t1 + 0x31, bytes([2, 5]))               # this track: port 2, ch 5
        self.table(g, 2, 5, 1)
        pb.start(g)
        pb.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual((g.sent, r32(g.u, REGION + 44)), ([], 1), "skipped")
        self.assertEqual(r32(g.u, REGION + 60), 2 | (5 << 8) | (1 << 16), "mapping recorded")

    def test_other_destination_or_no_table_plays(self):
        pb = CurvePlayback(); g = pb.rig()
        g.u.mem_write(g.t1 + 0x31, bytes([2, 5]))
        self.table(g, 2, 6, 1)                                  # Off, but for channel 6
        pb.start(g)
        pb.emit(g, g.t1, 0, 40, 1010)
        self.assertTrue(g.sent, "default poly")


class CurveWorstCase(unittest.TestCase):
    """Jeff's worst case: 8 External tracks x 6 voices = 48 curve notes at once, all
    on one MIDI port, each curve changing on every sample (a sawtooth, 256 samples
    = 16 steps). Timing model: 15 ticks per curve sample ~ 7.8 ms at 120 BPM, so a
    32-tick block ~ 16.7 ms. Checks: no curve is cut off, the port never exceeds
    its budget (400/s + a 4-message burst), every note keeps updating, and the M7
    cost per block stays small."""
    TRACKS, VOICES, N = 8, 6, 256
    BLOCK, BLOCK_MS = 4, 2.08                                     # ~2 ms of audio per block

    def test_48_notes_on_one_port(self):
        pb = CurvePlayback()
        g = pb.rig(samples=tuple((i * 7) % 120 for i in range(self.N)))
        tracks = [0x24010000 + t * 0x9000 for t in range(self.TRACKS)]
        for t in tracks:
            g.u.mem_write(t, bytes(0x8800)); g.u.mem_write(t + 0x86D1, b"\x01\x01")
        for ti, t in enumerate(tracks):
            for v in range(self.VOICES):
                pb.start(g, track=t, note=40 + ti * 8 + v)
        self.assertEqual(r32(g.u, REGION + 32), 48, "all 48 started")
        steps = 0
        ins = [0]
        def count(uc, a, sz, _): ins[0] += 1
        blocks = (self.N * 15) // self.BLOCK + 3
        worst = 0
        for b in range(blocks):
            ms = 1000 + int(b * self.BLOCK_MS)
            h = g.u.hook_add(UC_HOOK_CODE, count) if b == 400 else None
            before = ins[0]
            for t in tracks:
                pb.emit(g, t, b * self.BLOCK, self.BLOCK, ms)
            if h is not None:
                g.u.hook_del(h)
                worst = ins[0] - before
        seconds = blocks * self.BLOCK_MS / 1000.0
        self.assertEqual(r32(g.u, REGION + 52), 48, "peak players: none cut off")
        self.assertEqual(r32(g.u, REGION + 40), 0, "nothing late")
        total = len(g.sent)
        self.assertLessEqual(total, int(400 * seconds) + 4 + 48, "port budget (+ one final value each)")
        per_note = {}
        for x in g.sent:
            per_note.setdefault(x[3], []).append(x[5])
        self.assertEqual(len(per_note), 48, "every note was heard")
        slowest = min(len(v) for v in per_note.values())
        self.assertGreaterEqual(slowest, int(seconds / (48 * 0.0025)) - 4, "each note keeps updating (~1/(48 x 2.5 ms))")
        spread = max(len(v) for v in per_note.values()) - slowest
        self.assertLessEqual(spread, 3, "fair: every note gets about the same number of updates")
        self.assertLess(worst, 60000, "M7 instructions for one block across 8 tracks")
        print(f"\n  worst case: {total} sends in {seconds:.2f} s ({total / seconds:.0f}/s), slowest note "
              f"{slowest} updates, {worst} M7 instructions per block (8 tracks x 48 players)")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
