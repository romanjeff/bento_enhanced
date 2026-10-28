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

    def accept(self, track, pad, tick, note=36, vel=100, clock0=1000, word=None, length=0):
        u = self.u
        w32(u, CLOCK, clock0)
        u.mem_write(S, bytes(0x90))
        w32(u, S, pad); w32(u, S + 4, ROUTER); w32(u, S + 0x20, tick); w32(u, S + 0x24, 0)
        w32(u, S + 0x3C, (WORD if pad == 15 else 0) if word is None else word)
        w32(u, S + 0x30, length)                                    # the note record's length (+8)
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

class RetrigMidi(unittest.TestCase):
    """R4: on a MIDI-out track each retrigger hit also goes to MIDI, straight to
    the queue (not through the poly cap). Original still held: Note Off + Note On
    (stock's Note Off ends the last); already released: Note On, closed by our
    own Note Off half a division (80 ticks) later. Nothing is left hanging."""
    QUEUE = ROUTER + 0x7C8
    SYM = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}

    def midi(self, g):
        n = r32(g.u, self.QUEUE + 0x600)
        evs = [struct.unpack("<6I", g.u.mem_read(self.QUEUE + 24 * i, 24)) for i in range(n)]
        return [(e[0], e[1], e[4], e[5] & 0xFFFF) for e in evs]

    def hold(self, g, note=36):
        """The original Note On through the poly cap (its voice list)."""
        ev = 0x24070000
        g.u.mem_write(ev, struct.pack("<6I", 0x5B, 0, 0, 0, note, 100))
        g.u.reg_write(UC_ARM_REG_R0, self.QUEUE); g.u.reg_write(UC_ARM_REG_R1, ev); g.u.reg_write(UC_ARM_REG_R2, g.t1)
        g.u.reg_write(UC_ARM_REG_SP, 0x2407F000); g.u.reg_write(UC_ARM_REG_LR, SENT | 1)
        g.u.emu_start(self.SYM["h_note"] | 1, SENT, count=200000)
        w32(g.u, self.QUEUE + 0x600, 0)                            # forget the original in the log

    def test_released_original_hits_close_themselves(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 1000)
        self.assertEqual(self.midi(g), [(0x5B, 160, 36, 80), (0x5C, 240, 36, 0x64),
                                        (0x5B, 320, 36, 60), (0x5C, 400, 36, 0x64),
                                        (0x5B, 480, 36, 40), (0x5C, 560, 36, 0x64)])

    def test_held_original_rearticulates(self):
        g = Rig(); self.hold(g); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 1000)
        self.assertEqual(self.midi(g), [(0x5C, 160, 36, 0x64), (0x5B, 160, 36, 80),
                                        (0x5C, 320, 36, 0x64), (0x5B, 320, 36, 60),
                                        (0x5C, 480, 36, 0x64), (0x5B, 480, 36, 40)])

    def test_close_waits_for_its_block(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000)
        g.emit(g.t1, 0, 200)                                       # hit at 160; its Note Off (240) later
        self.assertEqual(self.midi(g), [(0x5B, 160, 36, 80)])
        g.emit(g.t1, 200, 100)
        self.assertEqual(self.midi(g)[-1], (0x5C, 240, 36, 0x64))

    def test_new_note_on_the_pad_closes_an_open_hit(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 200)
        g.accept(g.t1, pad=15, tick=1210, word=0)                  # plain note cuts the burst off
        self.assertEqual(self.midi(g)[-1][:1] + self.midi(g)[-1][2:], (0x5C, 36, 0x64))
        g.emit(g.t1, 200, 2000)
        self.assertEqual(len(self.midi(g)), 2, "nothing more from the old burst")

    def test_stopped_transport_closes_an_open_hit(self):
        g = Rig(); g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 200)
        g.emit(g.t1, 9000, 100)                                    # far later: late
        self.assertEqual([e[0] for e in self.midi(g)], [0x5B, 0x5C])
        g.emit(g.t1, 9100, 100)
        self.assertEqual(len(self.midi(g)), 2)

    def test_midi_off_track_sends_no_midi(self):
        g = Rig(); g.u.mem_write(g.t1 + 0x86D1, b"\x00")
        g.accept(g.t1, pad=15, tick=1000); g.emit(g.t1, 0, 1000)
        self.assertEqual(self.midi(g), [])
        self.assertEqual(len(g.hits()), 3, "internal hits unchanged")


class CurvePlayback(unittest.TestCase):
    """M2-2: a note whose word carries a curve handle (tag 0xB7) plays its recorded
    pressure as poly aftertouch (0x5F) on its External track. v3.27: per block only
    the latest due value, sent when the note's share of its port allows."""
    CURVES, POOL, TABLE, MSG_SEND, CYC = 0x3003D840, 0xC6180000, 0xC61C0000, 0x08065600, 0xE0001004
    MS = 480000

    def rig(self, samples=(10, 10, 20, 30)):
        g = Rig()
        u = g.u
        for t in (g.t1, g.t2):
            u.mem_write(t + 0x86D2, b"\x00")                        # External only (internal: CurveInternal)
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

    def start(self, g, track=None, note=60, word=(0xB7 << 24) | (1 << 3), tick=1000, length=0):
        g.accept(g.t1 if track is None else track, pad=0, tick=tick, note=note, word=word, length=length)
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


class CurveStepSize(unittest.TestCase):
    """v3.43: curve samples follow the sequence's step size (stock: duration = ticks
    per step x length / 960), so changing 1/8 -> 1/2 stretches the curve with its
    notes. Sequence object at track+0x86C0: word 0 = step-size index, +0x38 = steps."""
    SEQ = 0x24072000

    def due(self, idx, steps=1, length=0):
        pb = CurvePlayback(); g = pb.rig(samples=(10, 20, 30, 40, 50, 60, 70, 80))
        w32(g.u, g.t1 + 0x86C0, self.SEQ)
        g.u.mem_write(self.SEQ, struct.pack("<I", idx) + bytes(0x34) + bytes([steps]))
        pb.start(g, length=length)
        times = {}
        for start in range(0, 1200, 4):                        # 4-tick blocks: when does each value appear
            pb.emit(g, g.t1, start, 4, 1010 + start)
            for _, _, _, note, v, _ in g.sent[len(times):]:
                times.setdefault(v, start)
        return times

    def test_sixteenth_steps_keep_15_ticks(self):
        t = self.due(10)
        self.assertAlmostEqual(t[80] - t[10], 7 * 15, delta=4)   # 4-tick blocks

    def test_half_note_steps_stretch_8x(self):
        t = self.due(4)                                        # 1/2: 1920 ticks per step -> 120 per sample
        self.assertAlmostEqual(t[30] - t[10], 2 * 120, delta=4)

    def test_eighth_steps(self):
        t = self.due(8)                                        # 1/8: 480 -> 30 per sample
        self.assertAlmostEqual(t[40] - t[10], 3 * 30, delta=4)

    def test_raw_tick_lengths(self):
        t = self.due(8, steps=0)                               # lengths in ticks: 60 per sample
        self.assertAlmostEqual(t[20] - t[10], 60, delta=4)

    def test_lengthened_note_stretches_its_curve(self):
        """8 samples recorded over half a step (480 units); the note was then
        lengthened to a whole step (960) at 1/16: 240 ticks / 8 = 30 per sample."""
        t = self.due(10, length=960)
        self.assertAlmostEqual(t[80] - t[10], 7 * 30, delta=4)

    def test_shortened_note_squeezes_its_curve(self):
        t = self.due(10, length=240)                          # quarter step: 60 ticks / 8 = 7.5
        self.assertAlmostEqual(t[80] - t[10], 52, delta=4)

    def test_note_length_and_step_size_combine(self):
        t = self.due(4, length=960)                           # 1/2 steps, one step: 1920 / 8 = 240
        self.assertAlmostEqual(t[20] - t[10], 240, delta=4)


class CurveDestMode(unittest.TestCase):
    """M2-5: the player looks its track's MIDI port/channel up in the M4's table;
    AT Out Off (or Channel, until v3.31) starts nothing; no match = poly."""
    def table(self, g, port, ch, mode, *more):
        e = bytes([port, ch, mode, 1]) + b"".join(bytes([p, c, m, 1]) for p, c, m in more)
        g.u.mem_write(0x3003DF80, struct.pack("<II", 0x31544441, 1) + e + bytes(32 - len(e)))

    def test_off_for_this_destination(self):
        pb = CurvePlayback(); g = pb.rig()
        g.u.mem_write(g.t1 + 0x31, bytes([2, 5]))               # M7: port 2, channel byte 5
        self.table(g, 2, 6, 1)                                  # M4: channel 6 (1-based), Off
        pb.start(g)
        pb.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual((g.sent, r32(g.u, REGION + 44)), ([], 1), "skipped")
        self.assertEqual(r32(g.u, REGION + 60), 2 | (5 << 8) | (1 << 16) | (1 << 24), "track 1's entry")

    def test_device_v333_non_external_tracks_never_match(self):
        """v3.33 on the device: track 1 = 0/1/Off, tracks 2-4 = 0/0/Poly (M4 channel
        0 = not External); M7 byte 0. The exact pass hit track 2: Off ignored."""
        pb = CurvePlayback(); g = pb.rig()
        g.u.mem_write(g.t1 + 0x31, bytes([0, 0]))
        self.table(g, 0, 1, 1, (0, 0, 0), (0, 0, 0), (0, 0, 0))
        pb.start(g)
        pb.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual(g.sent, [], "Off honoured")
        self.assertEqual(r32(g.u, REGION + 60) >> 24, 1, "matched track 1, not track 2")

    def test_other_destination_or_no_table_plays(self):
        pb = CurvePlayback(); g = pb.rig()
        g.u.mem_write(g.t1 + 0x31, bytes([2, 5]))
        self.table(g, 2, 9, 1)                                  # Off, but for another channel
        pb.start(g)
        pb.emit(g, g.t1, 0, 40, 1010)
        self.assertTrue(g.sent, "default poly")


class CurveChannelMode(unittest.TestCase):
    """M2-5b AT Out Channel: the track's curve notes become one Channel Pressure
    stream (event 0x60) on its port/channel: the highest current value of the
    sounding notes, changed values only, 0 when the last one ends."""
    def setUp(self):
        self.pb = CurvePlayback()

    def rig(self, samples):
        g = self.pb.rig(samples=samples)
        g.u.mem_write(g.t1 + 0x31, bytes([1, 3]))               # M7: port 1, channel byte 3
        CurveDestMode.table(None, g, 1, 4, 2)                     # M4: channel 4, Channel
        return g

    def chan(self, g):
        return [(x[2], x[3]) for x in g.sent if x[1] == 0x60]

    def test_single_note_goes_out_as_channel_pressure(self):
        g = self.rig((10, 10, 20, 30))
        self.pb.start(g)
        self.pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual([x[1] for x in g.sent], [0x60], "no poly for a Channel track")
        self.assertEqual(self.chan(g), [(1, 20)])
        self.pb.emit(g, g.t1, 40, 40, 1030)
        self.assertEqual(self.chan(g)[-1], (1, 30))
        self.pb.emit(g, g.t1, 80, 40, 1050)                       # ended: release to 0
        self.assertEqual(self.chan(g)[-1], (1, 0))
        n = len(g.sent); self.pb.emit(g, g.t1, 120, 40, 1070)
        self.assertEqual(len(g.sent), n, "settled: nothing more")

    def test_chord_sends_the_highest(self):
        g = self.rig((40, 40, 40, 40, 40, 40, 40, 40))
        self.pb.start(g, note=60)
        w32(g.u, self.pb.TABLE + 8, 0x40)                       # handle 2 -> a second curve
        g.u.mem_write(self.pb.POOL + 0x40, struct.pack("<H", 8) + bytes([90] * 8))
        w32(g.u, self.pb.CURVES + 20, 3)                        # next handle
        self.pb.start(g, note=64, word=(0xB7 << 24) | (2 << 3))
        self.pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual(self.chan(g), [(1, 90)], "one message, the louder note")
        self.assertFalse([x for x in g.sent if x[1] == 0x5F], "no poly")
        self.assertEqual(r32(g.u, REGION + 32), 2, "both notes started")

    def test_values_wait_the_gap(self):
        g = self.rig((10, 20, 30, 40, 50, 60, 70, 80))
        self.pb.start(g)
        self.pb.emit(g, g.t1, 0, 16, 1010)                        # 20
        self.pb.emit(g, g.t1, 16, 15, 1011)                       # 1 ms later: waits
        self.assertEqual(self.chan(g), [(1, 20)])
        self.pb.emit(g, g.t1, 31, 15, 1020)
        self.assertEqual(self.chan(g)[-1], (1, 40))

    def test_poly_track_unchanged(self):
        g = self.pb.rig()
        self.pb.start(g)
        self.pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual([x[1] for x in g.sent], [0x5F])

class CurveInternal(unittest.TestCase):
    """M2-2b: a recorded curve on an internal instrument track posts stock's live
    pressure event {0x0E, 0, key, note, value} through the engine bus (the router
    note_route got, 0x0809ECA0), as the pad router does. AT Out by track index
    (track+0x86D4) from the M4 table, applied only to non-External entries."""
    BUS_POST, KEY, IDX = 0x0809ECA0, 0x0123, 2

    def rig(self, mode=None, samples=(10, 10, 20, 30), ext_entry=False):
        pb = CurvePlayback()
        g = pb.rig(samples=samples)
        u = g.u
        u.mem_write(g.t1 + 0x86D1, b"\x00\x01")                    # no MIDI out; instrument
        u.mem_write(g.t1 + 0x10, struct.pack("<H", 0))             # device: +0x10 was 0 on a granular track
        w32(u, g.t1 + 0x86DC, self.KEY)                           # its per-pad key (pad 0), as note_route reads it
        w32(u, g.t1 + 0x86D4, self.IDX)
        if mode is not None:
            e = bytes(4 * self.IDX) + bytes([0, 5 if ext_entry else 0, mode, 1])
            u.mem_write(0x3003DF80, struct.pack("<II", 0x31544441, 1) + e + bytes(32 - len(e)))
        g.posts = []
        def post(uc, addr, size, _):
            ev = struct.unpack("<6I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))
            g.posts.append((uc.reg_read(UC_ARM_REG_R0), ev[0], ev[2], ev[3], ev[4]))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, post, begin=self.BUS_POST, end=self.BUS_POST)
        return pb, g

    def test_poly_posts_the_voice_pressure_event(self):
        pb, g = self.rig(0)
        pb.start(g)
        pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual(g.posts, [(ROUTER, 0x0E, self.KEY, 60, 20)])
        self.assertEqual(g.sent, [], "no MIDI")
        pb.emit(g, g.t1, 40, 40, 1030)
        self.assertEqual(g.posts[-1][3:], (60, 30), "last value")
        n = len(g.posts); pb.emit(g, g.t1, 80, 40, 1050)
        self.assertEqual(len(g.posts), n, "finished")
        self.assertEqual(r32(g.u, REGION + 64), self.KEY | (self.IDX << 16), "int_dest: key, idx, Poly")

    def test_key_falls_back_to_the_track_word(self):
        pb, g = self.rig(0)
        w32(g.u, g.t1 + 0x86DC, 0xFFFFFF00)                       # no per-pad key
        g.u.mem_write(g.t1 + 0x10, struct.pack("<H", 0x0456))
        pb.start(g); pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual(g.posts[0][2], 0x0456)

    def test_off_posts_nothing(self):
        pb, g = self.rig(1)
        pb.start(g); pb.emit(g, g.t1, 0, 100, 1010)
        self.assertEqual(g.posts, [])

    def test_no_table_or_external_entry_is_poly(self):
        for kw in (dict(mode=None), dict(mode=1, ext_entry=True)):
            pb, g = self.rig(**kw)
            pb.start(g); pb.emit(g, g.t1, 0, 40, 1010)
            self.assertEqual([x[3:] for x in g.posts], [(60, 20)], kw)

    def test_channel_gives_every_voice_the_highest(self):
        pb, g = self.rig(2, samples=(40,) * 8)
        pb.start(g, note=60)
        w32(g.u, pb.TABLE + 8, 0x40)
        g.u.mem_write(pb.POOL + 0x40, struct.pack("<H", 8) + bytes([90] * 8))
        w32(g.u, pb.CURVES + 20, 3)
        pb.start(g, note=64, word=(0xB7 << 24) | (2 << 3))
        pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual(sorted(x[3:] for x in g.posts), [(60, 90), (64, 90)])

    def test_external_and_instrument_both_play(self):
        pb, g = self.rig(None)
        g.u.mem_write(g.t1 + 0x86D1, b"\x01\x01")
        pb.start(g); pb.emit(g, g.t1, 0, 40, 1010)
        self.assertEqual((len(g.sent), len(g.posts)), (1, 1), "MIDI poly AT and the instrument")


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
            g.u.mem_write(t, bytes(0x8800)); g.u.mem_write(t + 0x86D1, b"\x01\x00")   # External only
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
