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
    pressure as poly aftertouch (0x5F) on its External track: sample k at the note's
    start + 15k playback ticks, changed values only."""
    CURVES, POOL, TABLE, MSG_SEND = 0x3003D840, 0xC6180000, 0xC61C0000, 0x08065600

    def rig(self, samples=(10, 10, 20, 30)):
        g = Rig()
        u = g.u
        w32(u, self.TABLE + 4, 0)                                   # handle 1 -> pool offset 0
        u.mem_write(self.POOL, struct.pack("<H", len(samples)) + bytes(samples))
        u.mem_write(self.CURVES, struct.pack("<8I", 0x31565243, 1, self.POOL, 8, self.TABLE, 2, 0, 0))
        g.sent = []
        def send(uc, addr, size, _):
            ev = struct.unpack("<6I", uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))
            g.sent.append((uc.reg_read(UC_ARM_REG_R0), ev[0], ev[4], ev[5]))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        u.hook_add(UC_HOOK_CODE, send, begin=self.MSG_SEND, end=self.MSG_SEND)
        return g

    def start(self, g, word=(0xB7 << 24) | (1 << 3)):
        g.accept(g.t1, pad=0, tick=1000, note=60, word=word)       # region initialised here
        w32(g.u, REGION + 48, 0x2403A118)                           # MIDI-out queue seen at a note-on

    def test_samples_due_per_block_changed_values_only(self):
        g = self.rig(); self.start(g)
        g.emit(g.t1, 0, 40)                                         # k0 @0, k1 @15 (same), k2 @30
        self.assertEqual(g.sent, [(0x2403A118, 0x5F, 60, 10), (0x2403A118, 0x5F, 60, 20)])
        g.emit(g.t1, 40, 40)                                        # k3 @45
        self.assertEqual(g.sent[-1], (0x2403A118, 0x5F, 60, 30))
        self.assertEqual(r32(g.u, REGION + 32), 1, "curve_starts")
        self.assertEqual(r32(g.u, REGION + 36), 3, "curve_sent")
        n = len(g.sent); g.emit(g.t1, 80, 40)
        self.assertEqual(len(g.sent), n, "finished after its last sample")

    def test_other_tracks_and_plain_notes_send_nothing(self):
        g = self.rig(); self.start(g, word=0)
        g.emit(g.t1, 0, 100)
        self.assertEqual(g.sent, [])
        g = self.rig(); self.start(g)
        g.emit(g.t2, 0, 100)                                        # another track's block
        self.assertEqual(g.sent, [])

    def test_midi_out_off_or_bad_handle_starts_nothing(self):
        g = self.rig(); g.u.mem_write(g.t1 + 0x86D1, b"\x00"); self.start(g)
        g.emit(g.t1, 0, 100)
        self.assertEqual(g.sent, [])
        g = self.rig(); self.start(g, word=(0xB7 << 24) | (5 << 3))   # handle 5 >= next
        g.emit(g.t1, 0, 100)
        self.assertEqual((g.sent, r32(g.u, REGION + 44)), ([], 1), "skipped")

    def test_late_player_is_dropped(self):
        g = self.rig(); self.start(g)
        g.emit(g.t1, 5000, 40)                                      # transport jumped far ahead
        self.assertEqual((g.sent, r32(g.u, REGION + 40)), ([], 1), "late")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
