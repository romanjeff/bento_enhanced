# SPDX-License-Identifier: GPL-3.0-or-later
# Copied unchanged from bento-community-lab k16_work/bento_mods/tests/receiver_fixture.py
# (GPL-3.0-or-later). Set BENTO_STOCK_IMAGE to the stock 1.6.1 bento1.bin.
"""Bounded original-Thumb receiver tests with a Python interception callback.

No firmware instructions are replaced and no hardware connection is made.
Selection intents remain host-side symbols: queue transport and UI delivery are
NOT implemented. Synthetic RAM is not a proposed device allocation.
Run from the workspace root: python hook_checkpoint/test_interception.py
"""
from pathlib import Path
import hashlib
import random
import struct
import unittest
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

import os
IMAGE = Path(os.environ.get('BENTO_STOCK_IMAGE', 'handoff/Bento_Work_Handoff_2026-10-16/01_BENTO_FIRMWARE/1.6.1/bento1.bin'))
B = IMAGE.read_bytes()
assert hashlib.sha256(B).hexdigest() == '14475e9d5068f5659d0fe0b00fc1278c1479c43ed9fda3d40654d5f3a7a6bbb3'
BASE, OBJ, PKT, ENGINE, UIQUEUE = 0x08040000, 0x24010000, 0x24020000, 0x24040000, 0x24030000
STOP, HOOK, NEXT = 0x2407f000, 0x0808d1fe, 0x0808d1f0
assert B[HOOK-BASE:HOOK-BASE+4] == bytes.fromhex('9c42f6d0')


class Receiver:
    def __init__(self, intercept=False):
        self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.u.mem_map(BASE, 0x180000)
        self.u.mem_write(BASE, B)
        self.u.mem_map(0x24000000, 0x80000)
        self.word(0x2401bd3c, UIQUEUE)
        self.word(OBJ + 0x8c8, ENGINE)
        self.u.mem_write(OBJ + 0x1321, b'\x01')
        self.half(PKT + 8, 3)
        self.intercept = intercept
        self.owned = set()
        self.records = []
        self.intents = []
        self.timeline = []
        self.observations = []
        self.u.hook_add(UC_HOOK_CODE, self.callback)

    def word(self, address, value):
        self.u.mem_write(address, struct.pack('<I', value & 0xffffffff))

    def half(self, address, value):
        self.u.mem_write(address, struct.pack('<H', value))

    def readword(self, address):
        return struct.unpack('<I', self.u.mem_read(address, 4))[0]

    def callback(self, u, address, size, userdata):
        if address == HOOK:
            new, old, pad, obj = (u.reg_read(r) for r in
                                  [UC_ARM_REG_R4, UC_ARM_REG_R3, UC_ARM_REG_R7, UC_ARM_REG_R11])
            assert obj == OBJ
            mask = struct.unpack('<H', u.mem_read(obj + 0x1338, 2))[0]
            cached = u.mem_read(obj + 0x15ba, 1)[0]
            self.observations.append((pad, old, new, mask, cached))
            if self.intercept:
                consume = False
                if pad in self.owned:
                    consume = True
                    if new == 0:
                        self.owned.remove(pad)
                elif old == 0 and new > 0 and pad < 8 and mask & 0x2000:
                    self.owned.add(pad)
                    self.intents.append(pad)
                    self.timeline.append(('symbolic_select', pad))
                    consume = True
                if consume:
                    # Bypass the entire stock per-pad event construction. NEXT
                    # recomputes flags and advances the original loop normally.
                    u.reg_write(UC_ARM_REG_PC, NEXT | 1)
            return

        r0, r1, r2 = (u.reg_read(r) for r in [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2])
        if address in (0x080595e0, 0x08082220):
            record = bytes(u.mem_read(r1, 24))
            destination = 'engine' if address == 0x080595e0 else 'ui'
            self.records.append((destination, record))
            self.timeline.append((destination, record[0], struct.unpack_from('<I', record, 12)[0]))
            return  # Execute ORIGINAL queue writer and its memory barrier.
        if address == 0x080a3f0e:
            u.mem_write(r0, bytes(u.mem_read(r1, r2)))
            result = r0
        elif address == 0x080a2e00:
            result = 1  # Packet validation deliberately bypassed.
        elif address == 0x080a3ea0:
            result = int(bytes(u.mem_read(r0, r2)) != bytes(u.mem_read(r1, r2)))
        elif address == 0x080a04a0:
            u.mem_write(r0, bytes(24))
            result = r0
        elif address == 0x08058600:
            result = 0  # Additional transport-button side path, out of scope.
        else:
            return
        u.reg_write(UC_ARM_REG_R0, result)
        u.reg_write(UC_ARM_REG_PC, u.reg_read(UC_ARM_REG_LR))

    def packet(self, mask=0, pads=None):
        pads = [0] * 16 if pads is None else pads
        assert len(pads) == 16
        self.half(PKT + 0x10, mask)
        for index, value in enumerate(pads):
            self.half(PKT + 0x22 + index * 2, value)
        self.records, self.intents, self.timeline, self.observations = [], [], [], []
        self.u.reg_write(UC_ARM_REG_R0, OBJ)
        self.u.reg_write(UC_ARM_REG_R1, PKT)
        self.u.reg_write(UC_ARM_REG_SP, 0x2407e000)
        self.u.reg_write(UC_ARM_REG_LR, STOP | 1)
        self.u.emu_start(0x0808d081, STOP, count=50000)
        assert self.u.reg_read(UC_ARM_REG_PC) == STOP
        return self.records

    def events(self, destination='engine'):
        return [(r[0], struct.unpack_from('<I', r, 12)[0], struct.unpack_from('<I', r, 16)[0])
                for d, r in self.records if d == destination]

