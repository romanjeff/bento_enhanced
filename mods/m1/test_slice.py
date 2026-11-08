# SPDX-License-Identifier: GPL-3.0-or-later
"""S2 (1.6.8 port): where our reset-surviving RAM is, and how both cores reach it (Unicorn 2.1.3).

    python test_slice.py <stock bento1.bin> <build dir>

Runs on any build (1.6.1 and 1.6.8). Uses test_diag.py's emulator and FatFS rig.
1. Slice location: stock's own reset handlers (both cores) and M7 MPU setup run in the emulator;
   our RAM is above every stack, .bss and heap, below M7 MPU R2, mapped Normal write-back on the
   M7, and no stock word or movt points into it.
2. Alignment: on 1.6.8 the M4 reaches our RAM through a Device mapping (M4Mpu runs stock's M4 MPU
   setup); the emulator faults any unaligned M4 access there (test_diag.py's hook, active in every
   suite run), shown here to work.
3. Cache: the black box's M7 producers and M4 consumers with the M7 data cache modelled
   (M7Cache): everything reaches the M4, and the M7 rereads the M4's lines it cached before the M4
   booted; with cleans (or invalidates) ignored, it does not.
4. RAMCHK: no D2 block on 1.6.8; the build's slice check and its refusal of unported features.
"""
import struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_diag as D            # parses the same argv: <stock image> <build dir>
S, D2, BASE, SENT, CH10, DIAG = D.S, D.D2, D.BASE, D.SENT, D.CH10, D.DIAG
OMITTED, SYM7, SYM4, M4H, RINGSTAT = D.OMITTED, D.SYM7, D.SYM4, D.M4H, D.RINGSTAT
OUR_RAM, RAMCHK, M4_FROM, SLICE_IN_AXI, ALIGN_FAULTS = D.OUR_RAM, D.RAMCHK, D.M4_FROM, D.SLICE_IN_AXI, D.ALIGN_FAULTS
ALIGN_RANGES = D.ALIGN_RANGES
emu, call, w32, r32, movt = D.emu, D.call, D.w32, D.r32, D.movt
PLANT = D.BaseFirmwareChecks.PLANT

def stock_boot(img, vec, limit=30_000_000):
    """Run one core's stock reset handler (vector table at vec) up to its call of main, with every
    peripheral read as zero RAM. Returns (pc reached, the call's address, [(pc, addr, size)] of the
    RAM writes, lowest SP)."""
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, img)
    for a, n in ((0x00000000, 0x10000), (0x10000000, 0x48000), (0x20000000, 0x20000), (0x24000000, 0x80000),
                 (0x30000000, 0x48000), (0x38000000, 0x10000), (0x38800000, 0x1000), (0x40000000, 0x20000000),
                 (0xC0000000, 0x8000000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    w = lambda a: struct.unpack_from("<I", img, a - BASE)[0]
    sp, reset = w(vec), w(vec + 4) & ~1
    k = img[reset - BASE:reset - BASE + 0x400].find(bytes.fromhex("9047fee7"))   # blx r2 (main); b .
    assert k > 0, "no call of main in the reset handler"
    writes, low = [], [sp]
    def hw(uc, acc, a, size, v, _):
        if a < 0x40000000 or 0xC0000000 <= a < 0xE0000000:
            writes.append((uc.reg_read(UC_ARM_REG_PC), a, size))
        s = uc.reg_read(UC_ARM_REG_SP)
        if 0x1000 < s < low[0]: low[0] = s
    u.hook_add(UC_HOOK_MEM_WRITE, hw)
    u.reg_write(UC_ARM_REG_SP, sp)
    u.emu_start(reset | 1, reset + k, count=limit)
    return u.reg_read(UC_ARM_REG_PC), reset + k, writes, low[0]


def stock_mpu(img, fn=None):
    """Run stock's MPU setup (the M7's S.MPU_SETUP, or fn: the M4's S.M4_MPU_SETUP; its HAL calls
    included) and decode what it programs: {region: (base, size, rasr)}, and MPU_CTRL."""
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, img); u.mem_write(SENT, b"\xfe\xe7")
    for a, n in ((0x20000000, 0x20000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0x40000000, 0x20000000), (0xE0000000, 0x100000)):
        u.mem_map(a, n)
    regs, rnr, ctrl = {}, [0], [0]
    def hw(uc, acc, a, size, v, _):
        if a == 0xE000ED94: ctrl[0] = v
        elif a == 0xE000ED98: rnr[0] = v & 15
        elif a == 0xE000ED9C:
            if v & 0x10: rnr[0] = v & 15
            regs.setdefault(rnr[0], [0, 0])[0] = v & ~31
        elif a == 0xE000EDA0: regs.setdefault(rnr[0], [0, 0])[1] = v
    u.hook_add(UC_HOOK_MEM_WRITE, hw, begin=0xE000ED90, end=0xE000EDBF)
    call(u, S.MPU_SETUP if fn is None else fn)
    return {n: (b, 1 << (((r >> 1) & 31) + 1), r) for n, (b, r) in regs.items()}, ctrl[0]


def m7_attr(regions, ctrl, a):
    """The M7's view of address a: (region or None, TEX, C, B) of the highest enabled region that
    covers it (sub-region disables honoured), or the default map's (privileged default enabled)."""
    for n in sorted(regions, reverse=True):
        base, size, r = regions[n]
        if not r & 1 or not base <= a < base + size:
            continue
        if size >= 256 and (r >> 8) & (1 << ((a - base) // (size // 8))):
            continue                                  # sub-region disabled
        return n, (r >> 19) & 7, (r >> 17) & 1, (r >> 16) & 1
    assert ctrl & 4, "no region and no default map: a fault"
    assert 0x20000000 <= a < 0x40000000
    return None, 1, 1, 1                              # default map, SRAM: Normal write-back write-allocate


class SliceLocation(unittest.TestCase):
    """S2: where our reset-surviving RAM is on this base, checked against stock's own code run in
    the emulator (its reset handlers and the M7 MPU setup), not only against the table."""

    def test_stock_startup_of_both_cores_never_writes_our_ram(self):
        for core, vec in (("M7", BASE), ("M4", M4_FROM)):
            with self.subTest(core=core):
                pc, stop, writes, low = stock_boot(CH10, vec)
                self.assertEqual(pc, stop, f"{core} reset handler did not reach main")
                hits = [(hex(p), hex(a)) for p, a, n in writes if any(lo <= a < hi for lo, hi in OUR_RAM)]
                self.assertEqual(hits, [], f"{core} startup (.data copy, .bss zeroing, constructors) writes our RAM")
                self.assertGreater(len(writes), 1000, "the startup really ran (.bss zeroing)")
                if core == "M7":   # .bss, heap and stack all end below us; SystemInit's own stack is at the AXI top
                    axi = [a for p, a, n in writes if 0x24000000 <= a < 0x24080000]
                    self.assertTrue(all(a < S.M7_STACK_TOP or a >= 0x2407F000 for a in axi), "an M7 startup write between the stack top and SystemInit's stack")  # addr-ok: SystemInit's temporary stack below 0x24080000
                    self.assertTrue(any(S.M7_HEAP_END <= a < S.M7_STACK_TOP for a in axi), "the stack was used")

    def test_stack_heap_and_vectors_stay_below_our_ram(self):
        m7_sp = struct.unpack_from("<I", CH10, 0)[0]
        m4_sp = struct.unpack_from("<I", CH10, M4_FROM - BASE)[0]
        self.assertEqual(m7_sp, S.M7_STACK_TOP)
        self.assertLessEqual(S.M7_HEAP_END, S.M7_STACK_TOP)
        for lo, hi in OUR_RAM:
            self.assertFalse(lo < m4_sp <= hi, f"M4 stack top {m4_sp:#x} inside {lo:#x}..{hi:#x}")
            if lo >> 24 == 0x24:
                self.assertGreaterEqual(lo, S.M7_STACK_TOP, "below the M7 stack top")
        if SLICE_IN_AXI:   # 1.6.8: the guard band, then the slice, then the top block, up to M7 R2
            self.assertEqual((S.RAMCHK_AXI, S.RAMCHK_AXI_END, S.D2_SLICE_END), (S.M7_STACK_TOP + 0x20, D2, 0x24050000))  # addr-ok: M7 MPU R2 base (test_m7_mpu checks it)
            self.assertEqual(D2 % 0x1000, 0, "4 KB aligned (an MPU region could cover it exactly)")

    def test_no_stock_word_points_into_our_ram(self):
        """The image words whose value falls in our RAM are instruction encodings (OUR_RAM_WORDS):
        each decodes as Thumb instructions there, and no PC-relative load reads it."""
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
        md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
        words = struct.unpack_from(f"<{len(CH10) // 4}I", CH10)
        found = [BASE + 4 * i for i, w in enumerate(words) if any(lo <= w < hi for lo, hi in OUR_RAM)]
        self.assertEqual(found, sorted(S.OUR_RAM_WORDS))
        loads = set()
        for k in range(0, len(CH10) - 3, 2):
            h1 = struct.unpack_from("<H", CH10, k)[0]
            if h1 >> 11 == 0b01001:                                   # ldr rt, [pc, #imm8 * 4]
                loads.add(((BASE + k + 4) & ~3) + (h1 & 0xFF) * 4)
            elif (h1 & 0xFF7F) == 0xF85F:                             # ldr.w rt, [pc, #+-imm12]
                h2 = struct.unpack_from("<H", CH10, k + 2)[0]
                loads.add(((BASE + k + 4) & ~3) + ((h2 & 0xFFF) if h1 & 0x80 else -(h2 & 0xFFF)))
        for a in found:
            self.assertNotIn(a, loads, f"{a:#x} is read as a literal")
            ins = list(md.disasm(CH10[a - BASE:a - BASE + 4], a))   # one 32-bit or two 16-bit instructions
            self.assertEqual(sum(i.size for i in ins), 4, f"{a:#x} does not decode as instructions")

    def test_m7_mpu_maps_our_ram_cacheable_and_nothing_device_over_it(self):
        """The cache strategy rests on this: the M7 sees all of our RAM as Normal write-back memory
        (1.6.8: MPU R1, no write-allocate; 1.6.1: the default map), so M7 writes the M4 reads are
        cleaned and M4 lines are invalidated before the M7 reads them (CacheModel), and no Device
        region (1.6.8's new R14) lies over it."""
        regions, ctrl = stock_mpu(CH10)
        if S.module == "stock_168":
            self.assertEqual(regions[14][:2], (0x3003C000, 0x4000), "1.6.8's new R14 over 1.6.1's slice")  # addr-ok: stock's SAI DMA buffers
        for lo, hi in OUR_RAM:
            for a in range(lo, hi, 32):
                n, tex, c, b = m7_attr(regions, ctrl, a)
                self.assertEqual((tex in (0, 1), c, b), (True, 1, 1), f"{a:#x}: region {n} is not Normal write-back")
        if SLICE_IN_AXI:
            self.assertEqual(m7_attr(regions, ctrl, D2)[0], 1)
            self.assertEqual(m7_attr(regions, ctrl, S.D2_SLICE_END)[1:], (0, 0, 1), "Device from the slice's end")
        self.assertEqual(sum(1 for n in regions if regions[n][2] & 1), 15 if S.module == "stock_168" else 14)


class M4Mpu(unittest.TestCase):
    """S2: how the M4 reaches our RAM, from stock's own M4 MPU setup run in the emulator: a region
    with full access covers all of it. In AXI (1.6.8's slice; the RAMCHK block above the M7 stack on
    both) it is Device memory (TEX 0 C 0 B 1), which is why the M4 code is built -mstrict-align on
    1.6.8 and every suite faults an unaligned M4 access there (ALIGN_RANGES); in D2 (1.6.1's slice)
    it is Normal write-back."""

    def test_the_m4_reaches_our_ram_through_a_full_access_region(self):
        regions, ctrl = stock_mpu(CH10, S.M4_MPU_SETUP)
        self.assertTrue(ctrl & 1, "the M4 MPU is enabled")
        device = []
        for lo, hi in OUR_RAM:
            for a in range(lo, hi, 32):
                n, tex, c, b = m7_attr(regions, ctrl, a)
                self.assertIsNotNone(n, f"{a:#x}: no M4 region (default map)")
                self.assertEqual((regions[n][2] >> 24) & 7, 3, f"{a:#x}: region {n} is not full access")
                if (tex, c, b) == (0, 0, 1):
                    device.append(a)
                else:
                    self.assertEqual((c, b), (1, 1), f"{a:#x}: region {n} is neither Device nor Normal write-back")
        self.assertEqual(sorted(set(device)), sorted({a for lo, hi in ALIGN_RANGES for a in range(lo, hi, 32)}),
                         "the suites check alignment exactly where the M4 sees Device memory")
        if SLICE_IN_AXI:
            self.assertIn(D2, device, "1.6.8: the slice is Device on the M4")
        self.assertEqual(regions[0][:2], (0x24040000, 0x40000), "R0: AXI from 0x24040000, 256 KB")  # addr-ok: stock's M4 MPU R0
        self.assertEqual(ALIGN_FAULTS, [], "nothing unaligned so far")


class Alignment(unittest.TestCase):
    """S2: the M4's MPU maps AXI as Device, where an unaligned access faults: the emulator must
    catch one (so the suites prove our M4 code makes none), and the M7 (Normal memory) may."""

    def run_at(self, at, code, addr):
        u = emu(DIAG)
        u.mem_write(at, code)
        n = len(ALIGN_FAULTS)
        try:
            call(u, at, 0, addr)
        except AssertionError as e:
            del ALIGN_FAULTS[n:]
            return str(e)
        return None

    def test_unaligned_m4_access_is_caught(self):
        at = D2 if SLICE_IN_AXI else S.RAMCHK_AXI                # 1.6.1: its slice is D2 (Normal), RAMCHK axi is AXI
        ldr = bytes.fromhex("08687047")                          # ldr r0, [r1]; bx lr
        m4 = S.APPEND_FROM + 0x40000 & ~3                         # free flash in the emulator, M4 half
        self.assertIn("unaligned M4 access", self.run_at(m4, ldr, at + 0x9A2) or "")
        self.assertIsNone(self.run_at(m4, ldr, at + 0x9A4), "aligned is fine")
        self.assertIsNone(self.run_at(m4, bytes.fromhex("08887047"), at + 0x9A2), "ldrh at 2 is aligned")
        self.assertIsNone(self.run_at(S.M7_LIMIT, ldr, at + 0x9A2), "the M7 sees Normal memory")
        if not SLICE_IN_AXI:
            self.assertIsNone(self.run_at(m4, ldr, D2 + 0x9A2), "1.6.1's D2 slice is Normal memory for the M4")

    def test_m4_code_is_built_strict_align_on_1_6_8(self):
        # S4+S5: 1.6.8 also compiles the recorded-word black-box log (-DBE_REC_WORD_LOG, m4_step.c / m4_diag.c)
        flags = {"stock_161": [], "stock_168": ["-mstrict-align", "-DBE_REC_WORD_LOG"]}[S.module]
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build
        self.assertEqual(list(build.BASE_SETTINGS[S.module]["m4_flags"]), flags)


class M7Cache:
    """A model of the M7's data cache over our RAM, write-allocate write-back (stricter than 1.6.8's
    R1, which doesn't allocate on a write miss): M7 code sees its cached lines, the M4 sees memory.
    DCCMVAC writes a line back, DCIMVAC drops it, DCCIMVAC does both; switching cores swaps what
    the emulator's memory holds. clean=False ignores every clean, inval=False every invalidate
    (the negative controls)."""
    CMO = {0xE000EF68: "clean", 0xE000EF5C: "inval", 0xE000EF70: "both"}

    def __init__(self, u, clean=True, inval=True):
        self.u, self.clean_ok, self.inval_ok, self.lines, self.core, self.lost = u, clean, inval, {}, None, []
        for lo, hi in OUR_RAM:
            u.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self.access, begin=lo, end=hi - 1)
        u.hook_add(UC_HOOK_MEM_WRITE, self.cmo, begin=0xE000EF50, end=0xE000EF7F)
        u.ctl_flush_tb()

    def mine(self, a):
        return any(lo <= a < hi for lo, hi in OUR_RAM)

    def switch(self, core):
        u = self.u
        for line, e in self.lines.items():
            if core == "m4":                          # memory view: the cache's bytes aside, memory's in
                e["cache"] = bytes(u.mem_read(line, 32)); u.mem_write(line, e["real"])
            else:                                     # back to the M7: its cached bytes, stale or not
                now = bytes(u.mem_read(line, 32))
                if e["dirty"] and now != e["real"]:
                    self.lost.append(line)            # both cores wrote the line: one write is lost
                e["real"] = now; u.mem_write(line, e["cache"])
        self.core = core

    def view(self, core):
        """Show the emulator's memory as this core sees it (for the harness to read or write)."""
        if core != self.core:
            self.switch(core)

    def access(self, uc, acc, a, size, v, _):
        core = "m4" if uc.reg_read(UC_ARM_REG_PC) >= M4_FROM else "m7"
        if core != self.core:
            self.switch(core)
        if core == "m7":
            for line in range(a & ~31, a + size, 32):
                e = self.lines.setdefault(line, dict(real=bytes(uc.mem_read(line, 32)), dirty=False))
                if acc == UC_MEM_WRITE:
                    e["dirty"] = True

    def cmo(self, uc, acc, a, size, v, _):
        op, line = self.CMO.get(a), v & ~31
        if op is None or line not in self.lines:
            return
        e = self.lines[line]
        if op in ("clean", "both") and self.clean_ok:
            e["real"] = bytes(uc.mem_read(line, 32)) if self.core != "m4" else e["cache"]
            e["dirty"] = False
        if op in ("inval", "both") and (self.inval_ok or op == "both"):
            if self.core != "m4":
                uc.mem_write(line, e["real"])
            del self.lines[line]


class CacheModel(D.FatFsRig):
    """S2: the cache strategy. Our code keeps one writer per 32-byte line, cleans every line the
    M7 writes for the M4 and invalidates every M4 line before the M7 reads it (diag_shared.h,
    unchanged by the move to AXI). Run the black box's M7 producers and M4 consumers with the M7
    cache modelled: the M4 must still see everything; with cleans ignored it must not."""
    live = False

    def scenario(self, clean):
        self.cache = M7Cache(self.u, clean)
        self.tick(1000)                                       # M4 boot: header, config, M4 head
        ev = 0x24070000  # addr-ok: harness
        self.u.mem_write(ev, bytes([S.EV_PAD_PRESS, 0, 0, 0]) + struct.pack("<5I", 0, 1, 3, 0x9000, 0))
        call(self.u, SYM7["h_input"], 0x24071000, ev)  # addr-ok: harness (engine)
        w32(self.u, RINGSTAT + 20, 255)                       # (M7's view) the next queue read is the 256th
        call(self.u, SYM7["h_consume"], 0, 0, 0)            # heartbeat 256: the D0 publish
        if "stall" not in OMITTED:
            call(self.u, SYM7["stall_progress"])
        self.tick(1100)                                       # M4 drains the trace
        call(self.u, SYM4["dump_after_save"], 0)
        return self.fs.get("\\BENTO_SAVES.log", b"").decode(), bytes(self.u.mem_read(r32(self.u, M4H + 12), r32(self.u, M4H + 16)))   # M4 head r[1] buffer, r[2] length

    def test_the_m4_sees_every_m7_line_through_the_model(self):
        saves, log = self.scenario(clean=True)
        self.assertIn(b"PAD  0000", log, "the trace record and its head reached the M4")
        self.assertIn("LOAD render blocks=", saves, "the D0 lines reached the M4")
        self.assertIn("heartbeat=256", saves, "RingStat reached the M4")
        self.assertEqual(self.cache.lost, [], "a line written by both cores")
        self.assertTrue(self.cache.lines, "the M7 did cache lines of our RAM")

    def test_without_cleans_the_m4_misses_them(self):
        saves, log = self.scenario(clean=False)
        self.assertNotIn(b"PAD  0000", log)
        self.assertNotIn("LOAD render blocks=", saves)

    # ---- the other direction: lines the M4 writes and the M7 reads (M4 head, CAPCFG, LOADREQ) ----
    ROUNDS, PAIRS = 3, 6                                       # 36 records: two wraps of the 17-record ring

    def pad(self, t, pad, ms):
        w32(self.u, S.UWTICK, ms)
        ev = 0x24070000  # addr-ok: harness
        self.u.mem_write(ev, bytes([t, 0, 0, 0]) + struct.pack("<5I", 0, 1, pad, 0x9000, 0))
        call(self.u, SYM7["h_input"], 0x24071000, ev)  # addr-ok: harness (engine)

    def publish(self):
        """One D0 publish (h_consume's 256th queue read), RingStat set in the M7's own view."""
        self.cache.view("m7")
        w32(self.u, RINGSTAT + 20, r32(self.u, RINGSTAT + 20) | 255)    # heartbeat: the next read is a 256th
        call(self.u, SYM7["h_consume"], 0, 0, 0)

    def m4_word(self, a):
        self.cache.view("m4")
        return r32(self.u, a)

    def m4_side_scenario(self, inval):
        """As on the device, the M7 runs first: its hooks read the M4 head and CAPCFG (garbage, so
        they do nothing) and cache those lines before the M4 has booted. Then the M4 boots and
        publishes them, the M7 traces 36 pad events over three M4 drain rounds, captures a press,
        and acks two LOADREQ advances (each save starts a new load stretch)."""
        self.cache = M7Cache(self.u, inval=inval)
        self.pad(S.EV_PAD_PRESS, 3, 10)                       # before the M4: m4_ready() and cap_block() say no
        self.pad(S.EV_PAD_RELEASE, 3, 20)
        cached_early = {a & ~31 for a in (M4H, D2 + 0x1820)} <= set(self.cache.lines)   # CAPCFG at SLICE(0x1820)
        self.tick(1000)                                       # M4 boot: M4 head, CAPCFG (with its block), config
        ms = 1000
        for r in range(self.ROUNDS):
            for k in range(self.PAIRS):
                ms += 20; self.pad(S.EV_PAD_PRESS, 5, ms)
                ms += 30; self.pad(S.EV_PAD_RELEASE, 5, ms)
            self.tick(1100 + 100 * r)                         # the M4 drains (and moves its read index)
        self.publish()                                        # caches LOADREQ (req 0)
        acks = []
        for n in range(2):
            call(self.u, SYM4["dump_after_save"], 0)        # LOADREQ->req++ (M4)
            self.publish()
            acks.append((self.m4_word(D2 + 0x9E0 + 4), self.m4_word(D2 + 0xFE0 + 28)))   # LOADREQ->req, D0L->ack
        log = bytes(self.u.mem_read(self.m4_word(M4H + 12), self.m4_word(M4H + 16)))
        capb = self.m4_word(D2 + 0x1820 + 4)                  # CAPCFG->block
        slots = [struct.unpack("<6I", self.u.mem_read(capb + (5 * 2 + k) * D.PressureCapture.SLOT, 24)) for k in (0, 1)] if capb else []
        dropped = self.m4_word(D2 + 0x100 + 8)                # M7H->dropped
        return cached_early, log, acks, slots, dropped

    def test_the_m7_rereads_every_m4_line_it_cached_before(self):
        cached_early, log, acks, slots, dropped = self.m4_side_scenario(inval=True)
        self.assertTrue(cached_early, "the M7 cached the M4 head and CAPCFG lines before the M4 booted")
        n = 2 * self.ROUNDS * self.PAIRS
        self.assertEqual([k for k in range(n) if b"pad  %04x" % k not in log.lower()], [],
                         "every record reached the M4 (a stale M4 read index stops the M7 after 17)")
        self.assertEqual(dropped, 0)
        self.assertEqual(acks, [(1, 1), (2, 2)], "each LOADREQ advance acked (D0L->ack follows LOADREQ->req)")
        self.assertTrue(any(s[5] == 1 and s[3] >= 1 for s in slots), "a press captured into CAPCFG's block")
        self.assertEqual(self.cache.lost, [], "a line written by both cores")

    def test_without_invalidates_the_m7_acts_on_stale_lines(self):
        cached_early, log, acks, slots, dropped = self.m4_side_scenario(inval=False)
        self.assertTrue(cached_early)
        self.assertNotIn(b"PAD  0000", log, "the M7 never sees the M4 head's magic")
        self.assertNotEqual(acks, [(1, 1), (2, 2)])
        self.assertFalse(any(s[5] == 1 for s in slots), "no capture: CAPCFG still looks unpublished")


class SliceChecks(unittest.TestCase):
    """build.py check_slice_assumptions: stock passes; a new reference into our RAM is refused."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build
        cls.b = build

    def refused(self, addr, data, want):
        img = bytearray(CH10)
        img[addr - BASE:addr - BASE + len(data)] = data
        with self.assertRaises(SystemExit) as ctx:
            self.b.check_slice_assumptions(img, S)
        self.assertIn(want, str(ctx.exception))

    def test_stock_passes(self):
        self.b.check_slice_assumptions(CH10, S)
        self.assertEqual(self.b.our_ram(S), OUR_RAM)

    def test_a_literal_into_our_ram_is_refused(self):
        for a in (D2, D2 + 0x1FFC, S.RAMCHK_AXI, S.D2_SLICE_END - 4):
            self.refused(PLANT, struct.pack("<I", a), "image words pointing into our RAM")

    def test_movt_of_our_ram_is_refused(self):
        self.refused(PLANT + 2, movt(3, D2 >> 16), "movt")

    def test_our_ram_inside_the_m4_stack_is_refused(self):
        """Not only the M4's initial SP: our RAM anywhere between M4_STACK_BOTTOM and it."""
        class Over:                                           # this base with the M4 stack reaching down over our RAM
            def __getattr__(_, n): return D2 - 0x100 if n == "M4_STACK_BOTTOM" else getattr(S, n)
        img = bytearray(CH10)
        img[M4_FROM - BASE:M4_FROM - BASE + 4] = struct.pack("<I", S.D2_SLICE_END + 0x100)   # SP above it
        with self.assertRaises(SystemExit) as ctx:
            self.b.check_slice_assumptions(img, Over())
        self.assertIn("overlaps the M4's stack", str(ctx.exception))

    def test_a_moved_m7_stack_is_refused(self):
        self.refused(BASE, struct.pack("<I", S.M7_STACK_TOP + 0x1000), "initial stack pointer")


class BuildRefusal(unittest.TestCase):
    """S2: on a base whose table is still a draft, a build is refused exactly when an enabled
    feature is not ported yet or uses an unreviewed entry, and names them; the black box alone builds."""
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build
        cls.b = build
        cls.off = {g for g, _ in build.PATCH_GROUPS} - {"trace", "stall", "irq30", "bbox"}
        # The parts ported to 1.6.8 (independent list: a change to BASE_SETTINGS must show here).
        # S3: the M7 parts; S4+S5: cond_ui, poly_at, inta_live, step, hold.
        cls.s3 = {"poly_cap", "retrig", "cond_engine", "nmod", "inta_curve", "cond_ui", "poly_at", "inta_live", "step", "hold"}
        cls.later = {"mx"}                                         # not ported (deferred, off by default on 1.6.8)

    def test_black_box_alone_builds(self):
        inc, omitted, refused, _ = self.b.plan(S, self.off)
        self.assertEqual(refused, {})
        self.assertTrue({"core", "trace", "stall", "irq30", "bbox"} <= inc)
        if S.module == "stock_168":
            self.assertEqual(omitted, self.later, "S3: a ported group is built even when disabled (sites left stock)")
        self.assertEqual(self.off, self.s3 | self.later)

    def test_any_later_feature_is_refused_with_its_entries(self):
        """On 1.6.8 each part not ported yet (cls.later) is refused alone and named."""
        if S.module == "stock_161":
            self.skipTest("every feature is ported to 1.6.1")
        for g in sorted(self.later):
            with self.subTest(group=g):
                inc, omitted, refused, _ = self.b.plan(S, self.later - {g})
                self.assertEqual(list(refused), [g])
                msg = self.b.refuse_message(S, refused, omitted)
                self.assertIn(f"  {g} (", msg)
                self.assertIn("not yet ported to stock-1.6.8", msg)
                for n in refused[g]:
                    self.assertIn(n, msg)

    def test_draft_values_never_reach_c(self):
        header = S.c_header()
        for n, s in S.syms.items():
            if s.REVIEW is not None or s.status == "absent":
                self.assertNotIn(f"#define S_{n} ", header)


class RamchkOn168(D.FatFsRig):
    """S2: RAMCHK on 1.6.8 has no D2 blocks (1.6.8's D2 is stock's)."""
    live = False
    hb, m7_ready, heap, save_text = (D.ReadinessDiagnostics.hb, D.ReadinessDiagnostics.m7_ready,
                                     D.ReadinessDiagnostics.heap, D.ReadinessDiagnostics.save_text)
    def test_ramchk_has_no_d2_blocks_on_1_6_8(self):
        """S2: 1.6.8's D2 is stock's (M4 stack up to 0x3003AAA8, SAI DMA buffers from 0x3003C000):
        no RAMCHK block there, and a pattern over its D2 is never written."""
        if S.has("RAMCHK_D2A"):
            self.skipTest("1.6.1 keeps its D2 blocks")
        self.assertEqual([n for n, _, _ in RAMCHK], ["axi", "top"])
        ours = []                                         # D2 writes made by our code (not stock's, not the harness)
        def hook(uc, acc, a, size, v, _):
            pc = uc.reg_read(UC_ARM_REG_PC)
            if S.M7_TRAMP <= pc < S.M7_LIMIT or S.APPEND_FROM <= pc < 0x081E0000:
                ours.append((hex(pc), hex(a)))
        self.u.hook_add(UC_HOOK_MEM_WRITE, hook, begin=0x30000000, end=0x30048000 - 1)
        self.u.ctl_flush_tb()
        self.tick(1000); self.m7_ready(); self.heap(); self.hb(); text = self.save_text()
        self.assertEqual(ours, [], "the black box wrote D2 on 1.6.8")
        self.assertNotIn("d2a", text); self.assertNotIn("d2b", text)
        self.assertIn(" | top changed=0", text)
        for _, lo, hi in RAMCHK:
            self.assertTrue(0x24000000 <= lo < hi <= S.D2_SLICE_END, hex(lo))


def tearDownModule():
    assert not ALIGN_FAULTS, f"unaligned M4 accesses to our RAM: {[(hex(p), hex(a), n) for p, a, n in ALIGN_FAULTS]}"


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])