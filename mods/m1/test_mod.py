# SPDX-License-Identifier: GPL-3.0-or-later
"""F1: External track modulation engine (m7_mod.c), emulated.

Runs the built image's own mod_block / mod_note / h_note under Unicorn with a
stock-shaped clock record (tick anchor, sample anchor, BPM float) and a ModCfg
block published through the D2 header. MIDI-out events are captured at
msg_send. 120 BPM: 25 samples per tick, a quarter note = 960 ticks = 0.5 s.

usage: python test_mod.py <stock 1.6.1 bento1.bin> <build out dir>
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE, SENT, STACK, CYC = 0x08040000, 0x08000100, 0x2407F000, 0xE0001004
ENGINE = 0x24060000
QUEUE = ENGINE + 0x7C8
CFG, REGION, MAGIC = 0x3003C1E0, 0xC6100000, 0x31474144
MODHEAD, MODCFG, MOD_MAGIC = 0x3003DFC0, 0xC6200000, 0x31444F4D
CLOCK, TRACK = 0x24071000, 0x24064000
MSG_SEND = 0x08065600
MS = 480000
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
SYM7 = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}
CC, PB, AT = 1, 2, 3
LFO1, LFO2, ENV1, ENV2, VEL, PRESS, KEY = 1, 2, 3, 4, 5, 6, 7
TRACK_SZ = 8 + 8 + 24 + 16 * 12                                     # lfo[2] + ... per ModTrack (232 B)

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]
def f32(x): return struct.unpack("<I", struct.pack("<f", x))[0]


class Rig:
    def __init__(self, seq=2):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG); u.mem_write(SENT, b"\xfe\xe7")
        for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x400000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.mem_write(CFG, struct.pack("<8I", MAGIC, REGION, 0x3000, 1, 0, 0, 0, 0))
        u.mem_write(TRACK, bytes(0x8800))
        u.mem_write(TRACK + 0x31, bytes([1, 2])); u.mem_write(TRACK + 0x86D1, b"\x01")
        w32(u, TRACK + 0x86D4, 0)
        self.sent = []
        def send(uc, addr, size, _):
            if uc.reg_read(UC_ARM_REG_R0) == QUEUE:
                ev = struct.unpack("<6I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))
                self.sent.append((ev[0], ev[2], ev[3], ev[4], ev[5], self.ms))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, send, begin=MSG_SEND, end=MSG_SEND)
        self.ms = 0
        self.publish(seq)
        self.clock(0)

    def publish(self, seq=2, cfg=MODCFG):
        self.u.mem_write(cfg, struct.pack("<3I", MOD_MAGIC, 1, seq))
        self.u.mem_write(MODHEAD, struct.pack("<4I", MOD_MAGIC, 1, cfg, ~cfg & 0xFFFFFFFF))

    def track_cfg(self, idx=0, lfo=((0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 0)), env=((0, 0, 0, 0, 0), (0, 0, 0, 0, 0)), dests=()):
        b = b"".join(struct.pack("<4B2H", *l) for l in lfo)          # shape, sync, srate, restart, rate, depth
        b += b"".join(struct.pack("<4HB3x", *e) for e in env)       # a, d, s, r, loop
        ds = list(dests) + [(0, 0, (0, 0, 0), (0, 0, 0))] * (16 - len(dests))
        b += b"".join(struct.pack("<5Bx3h", t, n, *s, *a) for t, n, s, a in ds)
        assert len(b) == TRACK_SZ
        self.u.mem_write(MODCFG + 12 + idx * TRACK_SZ, b)

    def clock(self, sample, bpm=120.0):
        self.u.mem_write(CLOCK, struct.pack("<7I", sample, 0, 0, 0, 0, 0, f32(bpm)))

    def call(self, fn, *args):
        u = self.u
        for i, v in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, v & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(fn | 1, SENT, count=2_000_000)

    def block(self, ms, tick=None, track=TRACK):
        """One audio block at ms (cycle counter); song position at tick (default: follows ms)."""
        self.ms = ms
        w32(self.u, CYC, 10_000 * MS + ms * MS)
        self.clock((tick if tick is not None else ms * 2) * 25)    # 120 BPM: 2 ticks per ms
        self.call(SYM7["mod_block"], track, CLOCK, 0, ENGINE)

    def note(self, n, vel=100, on=True, track=TRACK):
        ev = 0x24070000
        self.u.mem_write(ev, struct.pack("<6I", 0x5B if on else 0x5C, 0, 1, 2, n, vel))
        self.call(SYM7["h_note"], QUEUE, ev, track)

    def cc(self, num):
        return [(e[4], e[5]) for e in self.sent if e[0] == 0x5E and e[3] == num]

    def pb(self):
        return [(e[3], e[5]) for e in self.sent if e[0] == 0x5D]


class Basics(unittest.TestCase):
    def test_nothing_without_routing(self):
        g = Rig(); g.track_cfg()
        for ms in range(0, 200, 10): g.block(ms)
        self.assertEqual(g.sent, [])

    def test_no_header_odd_seq_or_bad_check_sends_nothing(self):
        for setup in ("nohead", "odd", "badcheck"):
            g = Rig(seq=3 if setup == "odd" else 2)
            g.track_cfg(lfo=((3, 1, 6, 0, 0, 1000), (0,) * 6), dests=[(CC, 1, (LFO1, 0, 0), (1000, 0, 0))])
            if setup == "nohead": g.u.mem_write(MODHEAD, bytes(16))
            if setup == "badcheck": w32(g.u, MODHEAD + 12, 0)
            for ms in range(0, 200, 10): g.block(ms)
            self.assertEqual(g.sent, [], setup)

    def test_midi_out_off_or_index_out_of_range(self):
        for setup in ("midi", "idx"):
            g = Rig()
            g.track_cfg(lfo=((3, 1, 6, 0, 0, 1000), (0,) * 6), dests=[(CC, 1, (LFO1, 0, 0), (1000, 0, 0))])
            if setup == "midi": g.u.mem_write(TRACK + 0x86D1, b"\x00")
            else: w32(g.u, TRACK + 0x86D4, 9)
            for ms in range(0, 200, 10): g.block(ms)
            self.assertEqual(g.sent, [], setup)


class Lfo(unittest.TestCase):
    def rig(self, shape=3, sync=1, srate=6, rate=0, depth=1000, amt=1000, dest=(CC, 1), restart=0):
        g = Rig()
        g.track_cfg(lfo=((shape, sync, srate, restart, rate, depth), (0,) * 6),
                    dests=[(dest[0], dest[1], (LFO1, 0, 0), (amt, 0, 0))])
        return g

    def test_synced_pos_tri_quarter_follows_the_song_position(self):
        g = self.rig()
        for tick, want in ((0, 0), (240, 63), (480, 127), (720, 63)):
            g.sent.clear()
            g.block(tick // 2 + 1000, tick=tick)                   # 8 ms gaps never block here
            self.assertAlmostEqual(g.cc(1)[-1][0], want, delta=1, msg=f"tick {tick}")

    def test_synced_phase_ignores_block_timing(self):
        """Same song position -> same value, however blocks fall."""
        a, b = self.rig(), self.rig()
        a.block(1000, tick=300); b.block(5000, tick=300)
        self.assertEqual([v for v, _ in a.cc(1)], [v for v, _ in b.cc(1)])

    def test_tempo_changes_the_sample_to_tick_mapping(self):
        g = self.rig()
        g.ms = 1000; w32(g.u, CYC, 20_000 * MS)
        g.u.mem_write(CLOCK, struct.pack("<7I", 480 * 50, 0, 0, 0, 0, 0, f32(60.0)))   # 60 BPM: 50 samples/tick
        g.call(SYM7["mod_block"], TRACK, CLOCK, 0, ENGINE)
        self.assertAlmostEqual(g.cc(1)[-1][0], 127, delta=1, msg="tick 480 = half a quarter: the peak")

    def test_free_rate_cycles(self):
        g = self.rig(shape=1, sync=0, rate=500)                     # saw, 50 mHz << 5 = 1.6 Hz
        vals = []
        for ms in range(0, 1300, 10):
            g.block(ms); vals += [v for v, _ in g.cc(1)[-1:]]
        wraps = sum(1 for x, y in zip(vals, vals[1:]) if y < x - 60)
        self.assertEqual(wraps, 2, "1.6 Hz over 1.3 s: two saw resets")

    def test_restart_on_note_on(self):
        g = self.rig(shape=3, sync=0, rate=300, restart=1)           # 0.4 Hz Pos Tri, restarts
        g.block(0); g.block(1000)
        before = g.cc(1)[-1][0]
        g.note(60); g.block(1010)
        self.assertLess(g.cc(1)[-1][0], before, "phase back to the start of the saw")
        self.assertLessEqual(g.cc(1)[-1][0], 2)

    def test_bipolar_shape_clamps_at_base_zero_and_depth_scales(self):
        g = self.rig(shape=2, depth=500)                           # triangle at 50 % depth
        g.block(1000, tick=480)
        self.assertAlmostEqual(g.cc(1)[-1][0], 63, delta=1, msg="peak 0.5 x 127")
        g.block(1100, tick=0)
        self.assertEqual(g.cc(1)[-1][0], 0, "negative half clamps at 0")

    def test_pitch_bend_is_centred_and_14_bit(self):
        g = self.rig(shape=4, dest=(PB, 0), amt=150)                 # sine, 15 %
        g.block(1000, tick=240)                                    # quarter of the cycle: +1
        v = g.pb()[-1][0]
        self.assertAlmostEqual(v, 8192 + int(32767 * 150 / 4000), delta=4)
        g.block(1100, tick=720)                                    # three quarters: -1
        self.assertAlmostEqual(g.pb()[-1][0], 8192 - int(32767 * 150 / 4000), delta=4)


class Envelope(unittest.TestCase):
    def rig(self, a=100, d=300, s=500, r=300, loop=0):
        g = Rig()
        g.track_cfg(env=((a, d, s, r, loop), (0,) * 5), dests=[(CC, 74, (ENV1, 0, 0), (1000, 0, 0))])
        g.block(0)
        return g

    def run_to(self, g, start, end, step=5):
        for ms in range(start, end, step): g.block(ms)
        return g.cc(74)[-1][0] if g.cc(74) else None

    def test_attack_decay_sustain_release(self):
        g = self.rig()                                             # A 101 ms, D 901 ms, S 50 %, R 901 ms
        g.note(60)
        self.assertAlmostEqual(self.run_to(g, 5, 55), 63, delta=8, msg="half way up the attack")
        self.run_to(g, 55, 125)
        self.assertGreaterEqual(max(v for v, ms in g.cc(74) if ms >= 55), 125, "attack reached the top (8 ms sends may skip the exact peak)")
        self.assertAlmostEqual(self.run_to(g, 125, 1200), 63, delta=2, msg="settled at sustain")
        g.note(60, on=False)
        self.assertAlmostEqual(self.run_to(g, 1200, 1425), 32, delta=8, msg="half way down the release (full-scale rate)")
        self.assertEqual(self.run_to(g, 1425, 2300), 0)

    def test_gate_stays_open_while_any_note_is_held(self):
        g = self.rig()
        g.note(60); g.note(64)
        self.run_to(g, 5, 1200)
        g.note(60, on=False)
        self.assertAlmostEqual(self.run_to(g, 1200, 1500), 63, delta=2, msg="64 still held: sustain")
        g.note(64, on=False)
        self.assertLess(self.run_to(g, 1500, 1800), 50)

    def test_every_note_on_restarts_the_attack(self):
        g = self.rig()
        g.note(60); self.run_to(g, 5, 1200)                        # at sustain (63)
        g.note(64); v = self.run_to(g, 1200, 1260)
        self.assertGreater(v, 100, "re-attacked from the sustain level")

    def test_attack_decay_loop(self):
        g = self.rig(a=100, d=100, s=0, loop=2)                    # A 101 ms, D 101 ms, 2 extra cycles
        g.note(60)
        vals = []
        for ms in range(5, 900, 5):
            g.block(ms)
            if g.cc(74): vals.append(g.cc(74)[-1][0])
        peaks = sum(1 for x, y, z in zip(vals, vals[1:], vals[2:]) if y >= x and y > z and y > 120)
        self.assertEqual(peaks, 3, "the first attack plus two loops")
        self.assertEqual(vals[-1], 0, "then settles at sustain (0)")


class Sources(unittest.TestCase):
    def test_velocity_and_key(self):
        g = Rig()
        g.track_cfg(dests=[(CC, 20, (VEL, 0, 0), (1000, 0, 0)), (CC, 21, (KEY, 0, 0), (1000, 0, 0))])
        g.block(0); g.note(64, vel=100); g.block(20)
        self.assertEqual((g.cc(20)[-1][0], g.cc(21)[-1][0]), (100, 64))

    def test_three_sources_add_and_amounts_can_be_negative(self):
        g = Rig()
        g.track_cfg(dests=[(CC, 20, (VEL, KEY, VEL), (1000, 500, -500))])
        g.block(0); g.note(64, vel=100); g.block(20)
        self.assertAlmostEqual(g.cc(20)[-1][0], 100 + 32 - 50, delta=1)

    def test_channel_pressure_destination(self):
        g = Rig()
        g.track_cfg(dests=[(AT, 0, (VEL, 0, 0), (1000, 0, 0))])
        g.block(0); g.note(60, vel=90); g.block(20)
        self.assertEqual([e[3] for e in g.sent if e[0] == 0x60][-1], 90)


class Output(unittest.TestCase):
    def test_changed_values_only_and_8_ms_per_destination(self):
        g = Rig()
        g.track_cfg(lfo=((1, 0, 0, 0, 900, 1000), (0,) * 6), dests=[(CC, 1, (LFO1, 0, 0), (1000, 0, 0))])
        for ms in range(0, 100, 1): g.block(ms)
        times = [e[5] for e in g.sent]
        self.assertTrue(all(b - a >= 8 for a, b in zip(times, times[1:])), times)
        g2 = Rig()
        g2.track_cfg(dests=[(CC, 20, (VEL, 0, 0), (1000, 0, 0))])
        g2.block(0); g2.note(60); [g2.block(ms) for ms in range(20, 200, 10)]
        self.assertEqual([v for v, _ in g2.cc(20)], [0, 100], "each value goes out once")

    def test_port_budget_is_shared(self):
        g = Rig()
        dests = [(CC, 20 + i, (LFO1, 0, 0), (1000, 0, 0)) for i in range(16)]
        g.track_cfg(lfo=((1, 0, 0, 0, 1000, 1000), (0,) * 6), dests=dests)
        for ms in range(0, 1000, 2): g.block(ms)
        self.assertLessEqual(len(g.sent), 400 + 4, "at most 400/s from one port")
        self.assertGreater(len(g.sent), 300, "and the budget is used")

    def test_tracks_are_independent(self):
        g = Rig()
        t2 = 0x2406D000
        g.u.mem_write(t2, bytes(0x8800)); g.u.mem_write(t2 + 0x31, bytes([1, 3])); g.u.mem_write(t2 + 0x86D1, b"\x01")
        w32(g.u, t2 + 0x86D4, 1)
        g.track_cfg(0, dests=[(CC, 20, (VEL, 0, 0), (1000, 0, 0))])
        g.track_cfg(1, dests=[(CC, 21, (VEL, 0, 0), (1000, 0, 0))])
        g.block(0); g.block(0, track=t2)
        g.note(60, vel=50); g.block(20); g.block(20, track=t2)
        self.assertEqual(g.cc(20)[-1][0], 50)
        self.assertEqual(g.cc(21), [(0, 0)], "track 2 heard no note")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
