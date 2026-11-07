#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Note Mod through stock's live path, for internal and External tracks (v3.71).

    python test_notemod_route.py <stock bento1.bin> <m1 build dir>

test_notemod.py stubs note_route and Note Off and assumed melodic notes carry pad 0xFFFF. They
carry pad 0, so BE3.59-3.70 treated internal melodic notes as drum hits and lost every moved one.

REAL (from the image): track constructor 0x08062A80; track message loop for the key table
param and MIDI channel param; 0x08060820 sequencer processor; iterator 0x08095DA0; accept
stub; nm_on; note_route; bus_post; msg_send; pending loop; off stub; nm_off; Note Off;
message read; sampler process 0x0808E9A0 up to its note-start call.
STUBBED: tick math 0x080A1D60/0x080A2240/0x080A2220/0x080A21C0/0x080A2200 (integers),
and 0x08094760 (the voice start) which only records its arguments.
"""
import unittest
import struct, sys
from unicorn import *
from unicorn.arm_const import *

IMG = open(sys.argv[2] + '/bento1.bin', 'rb').read()
del sys.argv[1:3]
BASE, SENT = 0x08040000, 0x08000100
CFG, REGION, MAGIC = 0x3003C1E0, 0xC5FFC000, 0x31474144
MODHEAD, MODCFG, MOD_MAGIC = 0x3003DFC0, 0xC6200000, 0x37444F4D
NM_AT = MODCFG + 12 + 8 * 232
KEY_AT = NM_AT + 8 * 12
T, SEQ, CLOCK, ROUTER, MB, ENG, POOL, IDX, STK, INBOX = (0x24010000, 0x24020000, 0x24021000, 0x24030000,
    0x24040000, 0x24070000, 0x24022000, 0x24023000, 0x2407E000, 0x24024000)
NOKEY, SAMPLER_KEY, UI = 0xFFFFFF00, 0xF4, 0xFFFFFFFC
PARAM = 0x3E
SQUARE, P2 = 6, 286         # square at phase 0, range 1, C major: +2 scale steps


def w32(u, a, v): u.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack('<I', u.mem_read(a, 4))[0]


class Rig:
    def __init__(self, keys=(SAMPLER_KEY,)):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG[:0x1C0000]); u.mem_write(SENT, b'\xfe\xe7')
        for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000), (0xC5FF0000, 0x10000), (0xC6000000, 0x400000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.reg_write(UC_ARM_REG_C1_C0_2, u.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        u.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        w32(u, 0xE000ED88, 0xF << 20)
        w32(u, 0x24000160, 3405824); w32(u, 0x24000164, 0xC6000000 - 3405824)
        u.mem_write(CFG, struct.pack('<8I', MAGIC, REGION, 0x3000, 1, 0, 0, 0, 0))
        u.mem_write(MODCFG, struct.pack('<3I', MOD_MAGIC, 1, 2))
        u.mem_write(MODHEAD, struct.pack('<4I', MOD_MAGIC, 1, MODCFG, ~MODCFG & 0xFFFFFFFF))
        w32(u, KEY_AT, 1 | 3 << 8 | 1 << 16)         # scale 1 (major), root list 3 = C, enabled
        self.voice, self.now, self.lastpc = [], 0, 0
        u.hook_add(UC_HOOK_CODE, self.hook)
        self.call(0x08062A80, T)                     # real constructor
        self.k10 = r32(u, T + 0x10)
        self.keys = sorted(set(keys) | {UI})         # bus, as 0x0809F040 and 0x0809ED20 read it
        w32(u, ROUTER + 4, MB); w32(u, ROUTER + 0x508, len(self.keys))
        for i, k in enumerate(self.keys):
            w32(u, ROUTER + 8 + 8 * i, k); w32(u, ROUTER + 12 + 8 * i, i)
        w32(u, T + 0x86C0, SEQ); u.mem_write(T + 0x20, b'\x01')
        w32(u, T + 0x18, 0x7FFFFFFF); w32(u, T + 0x1C, 0x07FFFFFF)
        w32(u, T + 0xC, 0x77)                        # the track's own bus key
        u.mem_write(SEQ, bytes(0x60)); w32(u, SEQ + 0x28, 1)
        w32(u, SEQ + 8, IDX); w32(u, SEQ + 0x18, POOL + 0x800); w32(u, POOL + 0x800, POOL)

    def hook(self, u, a, size, _):
        self.lastpc = a
        r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
        if a == 0x080A1D60: self.ret(64)
        elif a == 0x080A2240:
            u.mem_write(r(0), struct.pack('<II', r(2), r(3))); self.ret(r(0))
        elif a == 0x080A2220:
            u.mem_write(r(0), bytes(u.mem_read(r(2), 8))); self.ret(r(0))
        elif a == 0x080A21C0:
            c = struct.unpack('<q', u.mem_read(r(1), 8))[0]; o = r(2)
            if o & 0x80000000: o -= 1 << 32
            u.mem_write(r(0), struct.pack('<q', c + o)); self.ret(r(0))
        elif a == 0x080A2200:
            t = struct.unpack('<q', u.mem_read(r(1), 8))[0]; c = struct.unpack('<q', u.mem_read(r(0), 8))[0]; self.ret(t - c)
        elif a == 0x08094760:
            self.voice.append(('voice_start', 'note=%d' % r(3))); self.ret(0)

    def ret(self, v=None):
        if v is not None: self.u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def call(self, fn, *args, stop=SENT, sp=STK, count=4000000):
        u = self.u
        for i, v in enumerate(args): u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, sp); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        u.emu_start(fn | 1, stop, count=count)
        return u.reg_read(UC_ARM_REG_R0)

    def param(self, pid, val, idx=0):
        """Real msg_send then the real track message loop 0x08062080..0x080621E0."""
        u = self.u
        ev = struct.pack('<BBHIIIIHH', PARAM, 0, 0, 0, 0x77, pid, val, idx, 0)
        u.mem_write(STK + 0x400, ev); w32(u, INBOX + 0x600, 0)
        self.call(0x08065600, INBOX, STK + 0x400)
        u.mem_write(STK, bytes(0xA0)); w32(u, STK + 4, INBOX); w32(u, STK + 8, r32(u, ROUTER))
        u.reg_write(UC_ARM_REG_R6, T); u.reg_write(UC_ARM_REG_R8, ROUTER)
        u.reg_write(UC_ARM_REG_R10, 0); u.reg_write(UC_ARM_REG_SP, STK)
        u.emu_start(0x08062080 | 1, 0x080621E0, count=400000)

    def nm(self, amount):
        self.u.mem_write(NM_AT, struct.pack('<4B2HB3x', SQUARE, 0, 6, 0, 400, amount, 0))

    def seq(self, recs):
        u = self.u
        for i, x in enumerate(recs):
            u.mem_write(POOL + 24 * i, struct.pack('<BBHIII', 1, 0, x['pad'], x['t'], x['len'], 0) + struct.pack('<HHI', x['note'], 100, 0))
            u.mem_write(IDX + 2 * i, struct.pack('<H', i))
        w32(u, SEQ + 0xC, len(recs)); w32(u, SEQ + 0x10, 0)

    def block(self):
        u = self.u
        u.mem_write(CLOCK, struct.pack('<q', self.now))
        u.mem_write(STK, struct.pack('<I', ROUTER))
        self.call(0x08060820, T, CLOCK, 0, 64)
        u.reg_write(UC_ARM_REG_SP, STK); w32(u, STK + 0x10, CLOCK); w32(u, STK + 0x14, ROUTER)
        u.reg_write(UC_ARM_REG_R5, T); u.reg_write(UC_ARM_REG_R8, 64)
        u.emu_start(0x08060494 | 1, 0x0806050A, count=2000000)
        self.now += 64

    def mailbox(self, base):
        u = self.u
        n = r32(u, base + 0x600); out = []
        for i in range(n):
            out.append(bytes(u.mem_read(base + 24 * i, 24)))
        w32(u, base + 0x600, 0) if False else None
        return out

    def drain(self):
        """Every message that really landed, by destination; then the real sampler reads its own."""
        u = self.u; log = []
        for i, k in enumerate(self.keys):
            if k == UI: continue
            for b in self.mailbox(MB + 0x604 * i):
                if b[0] in (0x12, 0x13):
                    key, note, vel = struct.unpack('<III', b[8:20])
                    log.append(('ON' if b[0] == 0x12 else 'OFF', 'bus %#x' % k, 'evkey=%#x' % key, 'note=%d' % note))
        for b in self.mailbox(ROUTER + 0x7C8):
            if b[0] in (0x5B, 0x5C):
                log.append(('MIDI_ON' if b[0] == 0x5B else 'MIDI_OFF', 'note=%d' % struct.unpack('<I', b[0x10:0x14])[0]))
        return log

    def sampler(self, key=SAMPLER_KEY):
        """Real 0x0808E9A0 message loop on a bare object with this key: what voice start sees."""
        u = self.u
        u.mem_write(ENG, bytes(0x800)); w32(u, ENG + 0xC, key)
        u.mem_write(STK, bytes(0x100))
        self.voice = []
        self.call(0x0808E9A0, ENG, ROUTER, stop=0x0808ECA0)
        return self.voice


F4 = SAMPLER_KEY
KIT = {p: 0x200 + p for p in range(4)}


def play(table=None, ext=False, recs=None, amount=P2, keys=(F4,)):
    g = Rig(keys=keys)
    for pad, k in (table or {}).items(): g.param(0xA1, k, pad)
    if ext: g.param(0x10, 3)                 # the real MIDI channel param
    g.seq(recs or [dict(t=10, len=100, note=60, pad=0)]); g.nm(amount)
    for _ in range(6): g.block()
    return g, g.drain()


class NoteModLive(unittest.TestCase):
    def test_control_depth_0(self):
        g, d = play({0: F4}, amount=0)
        self.assertEqual(g.sampler(), [('voice_start', 'note=60')])

    def test_internal_melodic_reaches_voice_at_scale_step(self):
        g, d = play({0: F4})
        self.assertEqual([x for x in d if x[0] in ('ON', 'OFF')],
                         [('ON', 'bus 0xf4', 'evkey=0xf4', 'note=64'), ('OFF', 'bus 0xf4', 'evkey=0xf4', 'note=64')])
        self.assertEqual(g.sampler(), [('voice_start', 'note=64')])

    def test_external_melodic(self):
        g, d = play(ext=True)
        self.assertEqual(d, [('MIDI_ON', 'note=64'), ('MIDI_OFF', 'note=64')])

    def test_kit_moves_to_filled_pad(self):
        g, d = play(KIT, keys=tuple(KIT.values()), recs=[dict(t=10, len=100, note=0, pad=1)], amount=500)
        self.assertEqual(g.sampler(0x203), [('voice_start', 'note=0')])

    def test_kit_never_moves_to_empty_pad(self):
        g, d = play(KIT, keys=tuple(KIT.values()), recs=[dict(t=10, len=100, note=0, pad=3)], amount=500)
        self.assertEqual(g.sampler(0x203), [('voice_start', 'note=0')])

    def test_a_melodic_note_0_is_not_lost(self):
        g, d = play({0: F4}, recs=[dict(t=10, len=100, note=0, pad=0)])
        self.assertEqual(g.sampler(), [('voice_start', 'note=0')])

    def test_note_off_goes_where_the_moved_note_went(self):
        g, d = play({0: F4})
        self.assertEqual([x[2:] for x in d], [('evkey=0xf4', 'note=64')] * 2)

    def test_full_depth_moves_a_whole_octave_on_an_internal_track(self):
        g, d = play({0: F4}, amount=1000)
        self.assertEqual(g.sampler(), [('voice_start', 'note=72')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
