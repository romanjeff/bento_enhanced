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
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE = 0x08040000
SENT = 0x08000100
STACK = 0x2407F000
CYC = 0xE0001004
ENGINE = 0x24060000              # stock posts MIDI-out events with msg_send(ENGINE + 0x7C8, ev)
QUEUE = ENGINE + 0x7C8
CFG = 0x240485E0
REGION = 0xC6100000
MAGIC = 0x31474144
REGS = [UC_ARM_REG_R0 + i for i in range(13)] + [UC_ARM_REG_SP, UC_ARM_REG_LR]
NOTE_SITES = [(0x0805F92C, UC_ARM_REG_R4), (0x0805FF50, UC_ARM_REG_R4), (0x0805FE44, UC_ARM_REG_R4),
              (0x08060088, UC_ARM_REG_R4), (0x08061966, UC_ARM_REG_R6)]

CH10 = Path(sys.argv[1]).read_bytes()
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
SYM7 = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m7_symbols"].items()}

def emu(img, region=True):
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000)
    u.mem_write(BASE, img)
    u.mem_write(SENT, b"\xfe\xe7")
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC6000000, 0x200000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    if region:   # what the M4 publishes at boot
        u.mem_write(CFG, struct.pack("<8I", MAGIC, REGION, 0x1000, 1, 0, 0, 0, 0))
    return u

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]

def track(u, port=1, ch=2, midi=1, base=0x24064000):
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
        self.t1 = track(self.u, port=1, ch=2, base=0x24064000)
        self.t2 = track(self.u, port=1, ch=3, base=0x2406D000)
        w32(self.u, CYC, 10_000_000)

    def ev(self, t, note, w1=0, port=1, ch=2, vel=100):
        at = 0x24070000
        self.u.mem_write(at, struct.pack("<6I", t, w1, port, ch, note, vel))
        return at

    def on(self, note, tr=None, **k): call(self.u, SYM7["h_note"], QUEUE, self.ev(0x5B, note, **k), tr or self.t1)
    def off(self, note, tr=None, **k): call(self.u, SYM7["h_note"], QUEUE, self.ev(0x5C, note, **k), tr or self.t1)
    def pressure(self, note, value, tr=None):
        at = 0x24070100
        self.u.mem_write(at, struct.pack("<6I", 0x0E, 0, 0x71, note, value, 0))
        call(self.u, SYM7["h_poly"], tr or self.t1, at, ENGINE)
    def out(self): return [(e[0], e[4]) for e in queued(self.u)]

class PolyCap(unittest.TestCase):
    def test_sites_post_like_ch10_under_the_cap(self):
        for site, treg in NOTE_SITES:
            for t in (0x5B, 0x5C):
                with self.subTest(site=hex(site), type=hex(t)):
                    res = []
                    for img in (CH10, IMG):
                        u = emu(img)
                        tr = track(u)
                        ev = 0x24070000
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

    def test_pressure_only_for_sounding_notes(self):
        v = Voices()
        for n in range(60, 67):                    # 60 gets stolen
            v.on(n)
        n0 = len(v.out())
        v.pressure(60, 50)                         # stolen: silent
        self.assertEqual(len(v.out()), n0)
        v.pressure(66, 50)                         # sounding: sent
        self.assertEqual(queued(v.u)[-1], (0x5F, 0, 1, 2, 66, 50))

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
        u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, 0x08062001)
        u.emu_start(0x08062464 | 1, 0x08062080, count=40000)
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

class M4Handler(unittest.TestCase):
    CALLS = (0x08105D56, 0x08105C44, 0x08105C5E, 0x08105CFE, 0x08105904, 0x08105C4C, 0x08105B74)

    def trace(self, start):
        u = emu(IMG)
        log = []
        def hook(uc, addr, size, _):
            log.append((addr, uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)))
            uc.reg_write(UC_ARM_REG_R0, 0x30000123)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        for a in self.CALLS:
            u.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)
        for r, v in ((UC_ARM_REG_R5, 0x30005550), (UC_ARM_REG_R6, 0x30006660), (UC_ARM_REG_R7, 0x40),
                     (UC_ARM_REG_R8, 0x02), (UC_ARM_REG_R9, 0x01), (UC_ARM_REG_R11, 0x55)):
            u.reg_write(r, v)
        u.reg_write(UC_ARM_REG_SP, STACK)
        u.emu_start(start | 1, 0x081050DE, count=2000)
        return log

    def test_same_calls_as_native_note_on_except_status(self):
        native, ours = self.trace(0x0810509A), self.trace(0x081B6580)
        self.assertEqual(len(native), 8)  # init, status, channel, note, value, send, 2 finishing calls
        self.assertEqual(len(native), len(ours))
        i = [k for k, c in enumerate(native) if c[0] == 0x08105C44][0]
        self.assertEqual((native[i][2], ours[i][2]), (0x90, 0xA0))
        self.assertEqual(native[:i] + native[i + 1:], ours[:i] + ours[i + 1:])

    def test_only_slot_5f_changed(self):
        jt = lambda img: [struct.unpack_from("<I", img, 0x08105294 - BASE + 4 * i)[0] for i in range(9)]
        a, b = jt(CH10), jt(IMG)
        self.assertEqual(b[4], 0x081B6581)
        self.assertEqual(a[:4] + a[5:], b[:4] + b[5:])

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
