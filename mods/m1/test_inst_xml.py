# SPDX-License-Identifier: GPL-3.0-or-later
"""bento_analog* through stock's own instrument writer, XML library and instrument reader (S6a, X1 / X2).

    python test_inst_xml.py <stock bento1.bin> <m1 build dir>

An instrument object (type Wavetable, its handle at +0x10, no properties of its own) goes through stock's
instrument writer (the function around INST_SAVE_SITE) into a document built by stock's XML code; our save hook
adds bento_analog / _sync / _glide to the params element for a track the M4's Analog table (AN4, our slice + 0xE20,
typed here: a 128 B header, then 16 x {handle, flags | glide << 4 | seconds << 16}) names. Then the table is
cleared and stock's instrument reader (INST_LOAD) reads the element back: our load hook restores the entry, with or
without the M7 (review 2026-11-08: a load before the M7 has taken the synth area is kept and published at the
handshake, by the UI tick).
Rig after test_step_xml.py (the step-data investigator's): every allocator starts from the M4's own .data image.
Skipped on a build without the analog group (1.6.1).
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
HAVE = "analog" not in MAN.get("omitted", []) and "synth.an_save" in MAN.get("m4_symbols", {})
SYM = {k[6:]: int(v, 16) for k, v in MAN.get("m4_symbols", {}).items() if k.startswith("synth.")}

BASE = 0x08040000
SENT = 0x20000100                       # harness return pad  # addr-ok: harness
STACK = 0x2001F000                      # harness stack, DTCM  # addr-ok: harness
STRS = 0x20010000                       # harness strings and the object's vtable  # addr-ok: harness
DOC = 0xC0000000                        # the document object  # addr-ok: harness
OBJ = 0xC0300000                        # the instrument object  # addr-ok: harness
HEAP = 0xC1000000                       # harness heap for the registry builder's allocations  # addr-ok: harness
SA = S.REGION - 0x8000 if HAVE else 0   # the synth area (synth_shared.h, typed here)
TAB = SA + 0x200
AN4 = S.D2_SLICE + 0xE20 if HAVE else 0
HANDLE = 0x5150


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
        self.take()                                                                        # the M7 took the area (gen 1)
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

    def take(self): self.u.mem_write(SA, struct.pack("<5I", 0x37534542, 1, 2, 0xC000, 1))   # M7 header, for M4 boot 1
    def line(self, i=0): return struct.unpack("<8I", self.u.mem_read(TAB + 32 * i, 32))
    def entry(self, i=0):
        h, w = struct.unpack("<2I", self.u.mem_read(AN4 + 0x80 + 8 * i, 8))
        return h, w & 15, (w >> 4) & 0x3FF
    def put(self, handle, flags, glide, i=0):
        """An AN4 entry (as the menu or a load leaves it)."""
        self.call(SYM["an_tick"])
        self.w32(AN4 + 0x80 + 8 * i, handle); self.w32(AN4 + 0x84 + 8 * i, flags | glide << 4)
        self.w32(AN4 + 8, self.r32(AN4 + 8) + 1)
    def clear(self): self.u.mem_write(AN4 + 0x80, bytes(0x80))


@unittest.skipUnless(HAVE, "no analog group in this image (1.6.1, or left out)")
class RoundTrip(unittest.TestCase):
    def test_analog_track_saves_and_loads(self):
        r = Rig(IMG)
        r.put(HANDLE, 3, 370)                                     # Analog, Sync on, Glide 370 (of 1000)
        parent, params = r.save()
        self.assertTrue(params, "stock wrote the params element")
        self.assertEqual([r.attr(params, n) for n in ("bento_analog", "bento_analog_sync", "bento_analog_glide")], [1, 1, 370])
        r.clear()                                                 # forget it
        r.call(S.INST_LOAD, parent, OBJ)
        self.assertEqual(r.entry(), (HANDLE, 3, 370), "handle, Analog | Sync, Glide back from the XML")
        r.call(SYM["an_tick"])
        line = r.line()
        self.assertEqual((line[1], line[2], line[3]), (HANDLE, 3, 370), "and published for the M7 by the UI tick")
        self.assertEqual(line[0], line[7], "a complete seqlocked line")
        self.assertFalse(line[0] & 1)
        self.assertGreater(len(r.blocks), 100, "stock's writer, XML library and reader ran")

    def test_plain_wavetable_writes_nothing_of_ours(self):
        r = Rig(IMG)
        r.put(0x9999, 1, 0)                                       # another track is Analog, not this one
        _, params = r.save()
        self.assertEqual([r.attr(params, n) for n in ("bento_analog", "bento_analog_sync", "bento_analog_glide")], [None] * 3)

    def test_loading_a_plain_track_clears_a_stale_line(self):
        r = Rig(IMG)
        parent, _ = r.save()                                      # no entry yet: a plain params element
        r.put(HANDLE, 1, 0)
        r.call(S.INST_LOAD, parent, OBJ)
        self.assertEqual(r.entry(), (0, 0, 0), "the handle is not Analog after loading a plain Wavetable")

    def test_a_load_before_the_m7_took_the_area_is_kept(self):
        """Review B1/B2: the boot's project load can come before the M7 has taken the synth area (the storage task runs
        on its own; the M7 takes the area once the config line is up). The entry waits in AN4, nothing is written into
        the area until the M7 header is there, then the UI tick publishes it."""
        r = Rig(IMG)
        r.put(HANDLE, 3, 370)
        parent, _ = r.save()
        r.clear(); r.u.mem_write(SA, bytes(0x8000))               # a new boot: the M7 has not taken the area yet
        r.call(S.INST_LOAD, parent, OBJ)
        r.call(SYM["an_tick"])
        self.assertEqual(bytes(r.u.mem_read(SA, 0x8000)), bytes(0x8000), "the handshake: not one M4 write into the area")
        self.assertEqual(r.entry(), (HANDLE, 3, 370), "but the M4 has it")
        _, params = r.save()
        self.assertEqual(r.attr(params, "bento_analog"), 1, "and a save before the handshake still writes it")
        r.take(); r.call(SYM["an_tick"])
        self.assertEqual(r.line()[1:4], (HANDLE, 3, 370), "published at the handshake")


class Stock(unittest.TestCase):
    """The base's own firmware: what it does with our attributes (it ignores them when loading, and drops them on
    its next save: the checklist's rollback items)."""
    @unittest.skipUnless(HAVE, "needs this build's document to read")
    def test_stock_reader_ignores_our_attributes(self):
        r = Rig(IMG)
        r.put(HANDLE, 3, 370)
        parent, _ = r.save()
        r.u.mem_write(BASE, STOCK)                                # the same memory, stock's code (no hooks)
        r.clear()
        r.call(S.INST_LOAD, parent, OBJ)
        self.assertEqual(r.r32(OBJ + 0x10), HANDLE, "read without a fault; the object untouched")
        self.assertEqual(r.entry(), (0, 0, 0), "stock does not know the attributes: nothing restored")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
