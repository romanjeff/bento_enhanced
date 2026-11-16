# SPDX-License-Identifier: GPL-3.0-or-later
"""Our instrument XML hooks (X1 / X2) through stock's own instrument writer, XML library and instrument reader.

    python test_inst_xml.py <stock bento1.bin> <m1 build dir>

History: BE4.22-4.31 saved and loaded the Analog table AN4 (bento_analog / _sync / _glide) here. P1 (BE4.40) retired AN4
(Analog is a native Type, its keys saved by stock: test_native_props), and since then X1 / X2 serve only the drums' DK4
(a BE_ENABLE=drums build), whose pad round trips are test_drum_ui's (Xml class). P4-0 (docs/plan-p4-drums.md 5,
"Retired / fixed") retired this file's three AN4 round-trip cases with test_handles'. What stays, true today:
- a Wavetable instrument (type 0x12, the object our X1 sees for a synth track) gets no attribute of ours from the writer;
- stock's own reader (the base image's code, no hooks) reads a params element carrying the drums' attributes
  (bento_drum_engine, bento_drum_k1..k7) without a fault and leaves the object as it was. The object is the same
  Wavetable, not a saminst (where the drums' attributes really sit), so this is a smoke check of the reader, not the
  checklist's rollback item: stock reading a real drum pad is P4-1's (stock 1.6.8 opens the drum project).
An instrument object (type Wavetable, its handle at +0x10, no properties of its own) goes through stock's instrument
writer (the function around INST_SAVE_SITE) into a document built by stock's XML code.
Rig after test_step_xml.py (the step-data investigator's): every allocator starts from the M4's own .data image.
Skipped on a build without the X1 / X2 hooks (1.6.1, a default 1.6.8 build).
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_BLOCK, UC_HOOK_CODE
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bases import from_build
S = from_build(sys.argv[2], Path(sys.argv[1]).read_bytes())
STOCK = Path(sys.argv[1]).read_bytes()
IMG = (Path(sys.argv[2]) / "bento1.bin").read_bytes()
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
del sys.argv[1:3]
HAVE = "analog" not in MAN.get("omitted", []) and "analog" not in MAN.get("disabled", []) and "synth.an_save" in MAN.get("m4_symbols", {})
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}

BASE = 0x08040000
SENT = 0x20000100                       # harness return pad  # addr-ok: harness
STACK = 0x2001F000                      # harness stack, DTCM  # addr-ok: harness
STRS = 0x20010000                       # harness strings and the object's vtable  # addr-ok: harness
DOC = 0xC0000000                        # the document object  # addr-ok: harness
OBJ = 0xC0300000                        # the instrument object  # addr-ok: harness
HEAP = 0xC1000000                       # harness heap for the registry builder's allocations  # addr-ok: harness
SA = S.REGION - 0x8000 if HAVE else 0   # the synth area (synth_shared.h, typed here)
HANDLE = 0x5150
OURS = ["bento_analog", "bento_analog_sync", "bento_analog_glide", "bento_drum_engine"] + [f"bento_drum_k{i}" for i in range(1, 8)]


class Rig:
    def __init__(self, image):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, image[:0x200000 - (BASE - 0x08000000)])
        for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xC0000000, 0x8000000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        u.mem_write(0x30000000, image[S.M4_DATA_INIT - BASE:S.M4_DATA_INIT - BASE + S.M4_DATA_INIT_LEN])
        u.mem_write(SENT, b"\xfe\xe7")
        self.blocks = set()
        u.hook_add(UC_HOOK_BLOCK, lambda uc, a, sz, _: self.blocks.add(a) if 0x08100000 <= a < S.APPEND_FROM else None)
        self.w32(S.D2_SLICE + 0x1E0, 0x31474144); self.w32(S.D2_SLICE + 0x1E0 + 12, 1)   # CFG: our boot generation 1
        u.mem_write(SA, struct.pack("<5I", 0x37534542, 1, 2, 0xC000, 1))                  # the M7 took the area (gen 1)
        self.w32(S.OBJ_HANDLE_CTR, HANDLE + 1)    # stock's handle counter: HANDLE is an object of this boot (H1)
        u.mem_write(STRS, b"instrument\0")
        # the object: vtable[0] = type getter (movs r0, #0x12; bx lr), no properties, its handle at +0x10
        u.mem_write(STRS + 0x40, bytes([S.TYPE_WAVETABLE, 0x20, 0x70, 0x47]))
        self.w32(STRS + 0x80, (STRS + 0x40) | 1)
        u.mem_write(OBJ, bytes(0x40)); self.w32(OBJ, STRS + 0x80); self.w32(OBJ + 0x10, HANDLE)
        # the reader matches attribute tokens against the parameter registry: stock's builder fills it (as test_keys)
        heap = [HEAP]
        def malloc(uc, a, sz, _):
            n = uc.reg_read(UC_ARM_REG_R0); p = heap[0]; heap[0] += (n + 7) & ~7
            uc.reg_write(UC_ARM_REG_R0, p); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.mh = u.hook_add(UC_HOOK_CODE, malloc, begin=S.M4_MALLOC, end=S.M4_MALLOC)
        self.call(S.REG_BUILD, OBJ + 0x100, count=20_000_000)
        u.hook_del(self.mh)
        self.call(SYM["an_tick"])                 # our tables exist (as after the first UI tick)

    def w32(self, a, x): self.u.mem_write(a, struct.pack("<I", x & 0xFFFFFFFF))
    def r32(self, a): return struct.unpack("<I", self.u.mem_read(a, 4))[0]
    def call(self, fn, *args, count=5_000_000):
        u = self.u
        for i, x in enumerate(args):
            u.reg_write(UC_ARM_REG_R0 + i, x & 0xFFFFFFFF)
        u.reg_write(UC_ARM_REG_SP, STACK); u.reg_write(UC_ARM_REG_LR, SENT | 1)
        try:
            u.emu_start(fn | 1, SENT, count=count)
        except UcError as e:
            pc = u.reg_read(UC_ARM_REG_PC)
            raise AssertionError(f"{e} at pc {pc:#x}, lr {u.reg_read(UC_ARM_REG_LR):#x}")
        return u.reg_read(UC_ARM_REG_R0)

    def save(self):
        """Stock's instrument writer on a fresh document: (parent element, its params element)."""
        self.u.mem_write(DOC, bytes(0x1000))
        self.call(S.XML_DOC_INIT, DOC, 1, 0)
        parent = self.call(S.XML_NEW_ELEMENT, DOC, STRS); self.call(S.XML_APPEND_CHILD, DOC, parent)
        self.u.mem_write(STRS + 0x100, b"params\0")
        self.call(self.writer(), DOC, parent, OBJ)
        return parent, self.call(S.XML_CHILD, parent, STRS + 0x100)

    def writer(self):
        """The instrument writer: the function whose body holds INST_SAVE_SITE (push.w {r4-fp,lr}; sub sp,#0x44)."""
        for a in range(S.INST_SAVE_SITE & ~1, S.INST_SAVE_SITE - 0x200, -2):
            if self.u.mem_read(a, 6) == bytes.fromhex("2de9f04f91b0"):
                return a
        raise AssertionError("no writer prologue before INST_SAVE_SITE")

    def attr(self, params, name):
        self.u.mem_write(STRS + 0x200, name.encode() + b"\0")
        a = self.call(S.XML_FIND, params, STRS + 0x200)
        if not a:
            return None
        self.u.mem_write(STRS + 0x300, bytes(4))
        r = self.call(S.XML_GET_U32, a, STRS + 0x300)
        return self.r32(STRS + 0x300) if r == 0 else "?"

    def add(self, params, name, value):
        """An attribute written by stock's XML library (as our X1 writes one)."""
        self.u.mem_write(STRS + 0x200, name.encode() + b"\0")
        a = self.call(S.XML_ADD, params, STRS + 0x200)
        self.call(S.XML_SET_U32, a, value)


@unittest.skipUnless(HAVE, "no X1 / X2 hooks in this image (1.6.1, or a default 1.6.8 build)")
class Writer(unittest.TestCase):
    def test_a_wavetable_writes_nothing_of_ours(self):
        r = Rig(IMG)
        parent, params = r.save()
        self.assertTrue(params, "stock wrote the params element")
        self.assertEqual([r.attr(params, n) for n in OURS], [None] * len(OURS))
        self.assertGreater(len(r.blocks), 100, "stock's writer and XML library ran")


class Stock(unittest.TestCase):
    """The base's own firmware: its reader ignores our attributes on a Wavetable object (a smoke check; the rollback
    item on a real saminst is P4-1's)."""
    @unittest.skipUnless(HAVE, "needs this build's writer for the document")
    def test_stock_reader_ignores_the_drums_attributes(self):
        r = Rig(IMG)
        parent, params = r.save()
        for i, n in enumerate(OURS[3:]):
            r.add(params, n, [1, 64, 20, 64, 0, 90, 30, 0][i])
        self.assertEqual(r.attr(params, "bento_drum_k1"), 64, "the document carries them")
        before = bytes(r.u.mem_read(OBJ, 0x40))
        r.u.mem_write(BASE, STOCK)                                # the same memory, stock's code (no hooks)
        r.call(S.INST_LOAD, parent, OBJ)
        self.assertEqual(bytes(r.u.mem_read(OBJ, 0x40)), before, "read without a fault; the object untouched")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
