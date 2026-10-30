# SPDX-License-Identifier: GPL-3.0-or-later
"""F5 Note Mod (m7_mod.c), emulated through the real patched sites.

    python test_notemod.py <stock 1.6.1 bento1.bin> <build dir>

The accept stub at 0x08060B9A (nm_on before stock's note_route), the two sequencer
Note Off call sites (0x080605D2, 0x08060A4C -> nm_off -> stock's 0x0805FE00) and the
stop / all-notes-off entry (0x080618E0 -> nm_all_off). Stock's note_route and Note
Off are recorders. Settings come from a ModCfg published through the D2 header.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE, SENT = 0x08040000, 0x08000100
CFG, REGION, MAGIC = 0x3003C1E0, 0xC6100000, 0x31474144
MODHEAD, MODCFG, MOD_MAGIC = 0x3003DFC0, 0xC6200000, 0x37444F4D
NM_AT = MODCFG + 12 + 8 * 232                    # ModNote nm[8] after the tracks
KEY_AT = NM_AT + 8 * 12
CLOCK, ROUTER, S = 0x24071000, 0x24060000, 0x2407E000
NOTE_ROUTE, NOTE_OFF, TO_OFFSET = 0x0805F8E0, 0x0805FE00, 0x080A2200
ACCEPT, OFF_SITES, ALL_OFF = 0x08060B9A, (0x080605D2, 0x08060A4C), 0x080618E0
MELODIC = 0xFFFF
SQUARE, SAW, RANDOM = 6, 1, 8
MAJOR, C, D = 1, 3, 5                            # stock's Root list starts at A

OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]


class Rig:
    def __init__(self):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG); u.mem_write(SENT, b"\xfe\xe7")
        for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x400000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.mem_write(CFG, struct.pack("<8I", MAGIC, REGION, 0x3000, 1, 0, 0, 0, 0))
        self.t = 0x24064000
        u.mem_write(self.t, bytes(0x8800)); u.mem_write(self.t + 0x86D1, b"\x01\x01")   # MIDI out + internal
        for p in range(16):
            w32(u, self.t + 0x86DC + 4 * p, 0x1000 + p)                                   # every pad has a sound
        u.mem_write(MODCFG, struct.pack("<3I", MOD_MAGIC, 1, 2))
        u.mem_write(MODHEAD, struct.pack("<4I", MOD_MAGIC, 1, MODCFG, ~MODCFG & 0xFFFFFFFF))
        self.key(MAJOR, C)
        self.on, self.off = [], []
        u.hook_add(UC_HOOK_CODE, self.hook)

    def hook(self, u, addr, size, _):
        r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
        sp = u.reg_read(UC_ARM_REG_SP)
        if addr == NOTE_ROUTE:
            self.on.append((r(1), r32(u, sp) & 0xFFFF))
        elif addr == NOTE_OFF:
            self.off.append((r(1), r(3) & 0xFFFF, r32(u, sp)))
        elif addr == TO_OFFSET:
            u.reg_write(UC_ARM_REG_R0, (r32(u, r(1)) - r32(u, r(0))) & 0xFFFFFFFF)
        else:
            return
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def nm(self, shape=SQUARE, sync=0, srate=6, restart=0, rate=400, amount=1000, rng=0, idx=0):
        self.u.mem_write(NM_AT + 12 * idx, struct.pack("<4B2HB3x", shape, sync, srate, restart, rate, amount, rng))

    def key(self, scale, root):
        w32(self.u, KEY_AT, scale | root << 8 | 1 << 16)

    def note_on(self, note, pad=MELODIC, tick=1000, word=0):
        u = self.u
        w32(u, CLOCK, 1000); u.mem_write(S, bytes(0x90))
        w32(u, S, pad); w32(u, S + 4, ROUTER); w32(u, S + 0x20, tick); w32(u, S + 0x24, 0); w32(u, S + 0x3C, word)
        for i, v in enumerate((self.t, note, 100, 0)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_R6, CLOCK); u.reg_write(UC_ARM_REG_SP, S)
        u.emu_start(ACCEPT | 1, ACCEPT + 4, count=400000)
        assert u.reg_read(UC_ARM_REG_SP) == S
        return self.on[-1]

    def note_off(self, note, pad=MELODIC, site=OFF_SITES[0]):
        u = self.u
        w32(u, S, ROUTER)
        for i, v in enumerate((self.t, note, 7, pad)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, S)
        u.emu_start(site | 1, site + 4, count=400000)
        assert u.reg_read(UC_ARM_REG_SP) == S
        return self.off[-1][:2]

    def all_off(self):
        u = self.u
        w32(u, self.t + 0x238, 0)                         # stock's own pending list: empty
        for i, v in enumerate((self.t, 9, ROUTER)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, S); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(ALL_OFF | 1, SENT, count=400000)


class NoteMod(unittest.TestCase):
    def test_off_by_default(self):
        g = Rig(); g.nm(amount=0)
        self.assertEqual(g.note_on(60), (60, MELODIC))
        self.assertEqual(g.note_off(60), (60, MELODIC))

    def test_scale_steps_and_note_off_follows(self):
        g = Rig(); g.nm(amount=286)                       # Square at phase 0: +2 steps of C major
        self.assertEqual(g.note_on(60), (64, MELODIC), "C -> E")
        self.assertEqual(g.note_off(60), (64, MELODIC), "its Note Off goes out as E")
        self.assertEqual(g.note_off(60), (60, MELODIC), "once: the map entry is gone")

    def test_full_amount_is_the_range_in_octaves(self):
        for rng, want in ((0, 72), (1, 84), (3, 108)):
            g = Rig(); g.nm(rng=rng)
            self.assertEqual(g.note_on(60)[0], want)

    def test_out_of_scale_note_snaps_first(self):
        g = Rig(); g.nm(amount=286)
        self.assertEqual(g.note_on(61)[0], 64, "C# -> C (nearest, lower on a tie) -> +2 = E")

    def test_root_is_counted_from_A(self):
        g = Rig(); g.nm(amount=143); g.key(MAJOR, D)       # +1 step of D major
        self.assertEqual(g.note_on(62)[0], 64, "D -> E")
        self.assertEqual(g.note_on(66, tick=2000)[0], 67, "F# -> G")

    def test_synced_phase_follows_the_song_position(self):
        g = Rig(); g.nm(shape=SAW, sync=1, srate=6)       # one cycle per quarter (960 ticks)
        self.assertEqual(g.note_on(60, tick=960 * 5 + 480)[0], 60, "mid-cycle: Saw = 0")
        self.assertEqual(g.note_on(60, tick=960 * 7 + 720)[0], 67, "3/4: +4 steps = G")

    def test_chord_notes_share_the_offset(self):
        g = Rig(); g.nm(shape=RANDOM, restart=1, amount=1000, rng=1)
        a = g.note_on(60, tick=4000)[0]; b = g.note_on(64, tick=4000)[0]
        steps = lambda x, base: [0, 2, 4, 5, 7, 9, 11].index(x % 12) + 7 * (x // 12) - ([0, 2, 4, 5, 7, 9, 11].index(base % 12) + 7 * (base // 12))
        self.assertEqual(steps(a, 60), steps(b, 64), "same tick: same scale steps")

    def test_drum_notes_move_by_pads_and_wrap(self):
        g = Rig(); g.nm(amount=286)                       # 2 "steps": with range 1 = 4 pads, 286 % -> 1 pad
        on = g.note_on(0, pad=3)
        self.assertEqual(on, (0, 4), "note 0 = a pad's own sound: the pad moves")
        self.assertEqual(g.note_off(0, pad=3), (0, 4))
        g.nm(amount=1000, rng=0)                          # +4 pads
        self.assertEqual(g.note_on(0, pad=14, tick=2000), (0, 2), "wraps in the kit")

    def test_second_note_off_site(self):
        g = Rig(); g.nm(amount=286)
        g.note_on(60)
        self.assertEqual(g.note_off(60, site=OFF_SITES[1]), (64, MELODIC))

    def test_stop_ends_the_shifted_notes(self):
        g = Rig(); g.nm(amount=286)
        g.note_on(60, tick=1000); g.note_on(62, tick=1000)
        g.all_off()
        self.assertEqual(sorted(x[0] for x in g.off), [64, 65], "E and F (D + 2 steps) end on stop")
        self.assertEqual({x[2] for x in g.off}, {ROUTER})
        g.off.clear()
        self.assertEqual(g.note_off(60), (60, MELODIC), "nothing left mapped")

    def test_retrigger_hits_repeat_the_sent_pitch(self):
        g = Rig(); g.nm(amount=286)
        word = (0xB5 << 24) | (3 << 13) | ((4 + 1) << 9)  # 3 hits
        g.note_on(60, tick=1000, word=word)
        u = g.u
        w32(u, CLOCK, 1000); w32(u, S, ROUTER)
        for i, v in enumerate((g.t, CLOCK, 0, 2000)):
            u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, S)
        u.emu_start(0x08060820 | 1, 0x08060824, count=400000)
        self.assertGreaterEqual(len(g.on), 2, "hits were posted")
        self.assertEqual({n for n, _ in g.on}, {64}, "every hit plays the first hit's pitch")

    def test_random_goes_both_ways_across_the_range(self):
        """BE3.59 (Jeff): Random only ever shifted down, by at most half the range."""
        g = Rig(); g.nm(shape=RANDOM, restart=1, amount=1000, rng=1)          # +/-2 octaves = +/-14 steps
        notes = []
        for i in range(40):
            notes.append(g.note_on(60, tick=1000 + 100 * i)[0]); g.note_off(60)
        self.assertGreater(max(notes), 72, "up by more than an octave")
        self.assertLess(min(notes), 48, "down by more than an octave")

    def test_free_run_phase_moves_between_notes(self):
        """Free-run: the shape keeps moving (per audio block), so notes get different offsets."""
        g = Rig(); g.nm(shape=SAW, rate=400, amount=1000, rng=1)               # ~0.8 Hz
        u = g.u
        def blocks(n):
            for _ in range(n):
                w32(u, 0xE0001004, r32(u, 0xE0001004) + 480000 * 40)          # 40 ms per block
                w32(u, CLOCK, 1000); w32(u, S, ROUTER)
                for i, v in enumerate((g.t, CLOCK, 0, 0)):
                    u.reg_write(UC_ARM_REG_R0 + i, v)
                u.reg_write(UC_ARM_REG_SP, S)
                u.emu_start(0x08060820 | 1, 0x08060824, count=400000)
        got = []
        for i in range(4):
            got.append(g.note_on(60, tick=1000 + 960 * i)[0]); g.note_off(60)
            blocks(8)                                                         # 0.32 s: ~1/4 cycle
        self.assertEqual(len(set(got)), 4, f"each note somewhere else on the Saw: {got}")
        self.assertEqual(got, sorted(got), "rising Saw, rising notes")

    def test_triggered_restarts_at_the_first_note_after_stop(self):
        """v3.61 (Jeff): Triggered = the shape starts over each run (not at every note)."""
        g = Rig(); g.nm(shape=SAW, restart=1, amount=1000, rng=1)
        run = lambda: [g.note_on(60, tick=1000 + 960 * i)[0] for i in range(3) if not self.blocks(g, 8 if i else 0)]
        first = run(); g.all_off(); g.off.clear()
        self.blocks(g, 20)                                                  # stopped: the phase waits
        self.assertEqual(run(), first, "same notes from the top")
        self.assertEqual(len(set(first)), 3, f"moving within the run: {first}")

    def test_triggered_random_repeats_every_run(self):
        g = Rig(); g.nm(shape=RANDOM, restart=1, amount=1000, rng=1)
        g.all_off()
        a = [g.note_on(60, tick=1000 + 100 * i)[0] for i in range(8)]; g.all_off()
        b = [g.note_on(60, tick=9000 + 100 * i)[0] for i in range(8)]
        self.assertEqual(a, b)
        self.assertGreater(len(set(a)), 3, "still random within a run")

    def blocks(self, g, n):
        u = g.u
        for _ in range(n):
            w32(u, 0xE0001004, r32(u, 0xE0001004) + 480000 * 40)
            w32(u, CLOCK, 1000); w32(u, S, ROUTER)
            for i, v in enumerate((g.t, CLOCK, 0, 0)):
                u.reg_write(UC_ARM_REG_R0 + i, v)
            u.reg_write(UC_ARM_REG_SP, S)
            u.emu_start(0x08060820 | 1, 0x08060824, count=400000)

    def test_unshifted_when_the_map_is_full(self):
        g = Rig(); g.nm(amount=286)
        for i in range(16):
            g.note_on(40 + 2 * i, tick=1000 + i)
        self.assertEqual(g.note_on(100, tick=5000)[0], 100, "no room: plays as written (its Note Off then matches)")


if __name__ == "__main__":
    unittest.main(argv=sys.argv[:1], verbosity=2)
