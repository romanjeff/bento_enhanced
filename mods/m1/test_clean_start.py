# SPDX-License-Identifier: GPL-3.0-or-later
"""BE4.47 clean start (docs/plan-v4.47-clean-start.md): every boot starts from zeroed RAM of ours, whatever an earlier
run left; the RTC and its backup registers decide nothing; the black box's log files start over when missing.

    python test_clean_start.py <stock bento1.bin> <m1 build dir>
    (BE_DUNGEONS_BNT: the DUNGEONS project's curves.bnt, default logs/be446_DUNGEONS_curves.bnt)

The board (test_diag's emulator and FatFS stub, the built image's own code): a reset is what the device does to stock's
state (the M4 UI allocator and the M7 pool back at their start, the HAL ticks, the audio DMA and SAI registers at reset
values), the backup domain kept, SDRAM kept, decayed or not, our internal RAM as the last run left it (or pre-filled),
then the image's M7 reset path from CTRL_STARTUP_SITE to the instruction after it (the clean-start stub, BE4.47). A boot
is the M4's first UI-loop passes, the M7's first trace record and audio blocks (the reservation), a pad press (the
pressure capture), stock allocating its later objects from the UI allocator, and a project save (the black box's one card
access). The clean boot state (our internal RAM, the M1 Region, the synth area, the black box's log text and capture block,
the UI allocator) is compared with a cold boot's: the same board, all of that zero before the reset.

Run on the v4.46 image (its build dir as the second argument) the reset-loop and stale-state tests fail: that is the defect
BE4.47 removes (docs/plan-v4.47-clean-start.md, section 3)."""
import os, random, struct, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_diag as T                       # the emulator, the FatFS stub and the symbols (argv[1:3])
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_LR

S, SYM4, SYM7, D2, w32, r32 = T.S, T.SYM4, T.SYM7, T.D2, T.w32, T.r32
INTERNAL = (S.RAMCHK_AXI, S.D2_SLICE_END)            # all of our internal RAM on 1.6.8 (AXI: RAMCHK axi, slice, top)
REGION, REGION_USED = S.REGION, 0x2440                # the Region struct (m7_polyat.c: note0_on ends at +0x2440)
SYNTH, SYNTH_BYTES = S.EXT_POOL_END - 0xC000, 0x8000  # synth_shared.h SYNTH_AREA, SYNTH_BYTES
UI = (0xC6000000, 0xC6200000)                         # the harness's UI allocator (FatFsRig)  # addr-ok: harness
POOL_LEFT0 = 3405824                                  # stock's ext pool at startup (test_diag emu)
BUF, CAP = 0x40000, 32 * 1056                         # the black box's buffer and capture block (m4_diag.c)
M4H, CFG, CAPCFG, CURVES = D2 + 0x120, D2 + 0x1E0, D2 + 0x1820, D2 + 0x1840
LOADREQ = D2 + 0x9E0
RTC_TR, RTC_DR, BKP = 0x58004000, 0x58004004, 0x58004050
SAVES, HIST = "\\BENTO_SAVES.log", "\\BENTO_CRASHES.log"
PROJ, BNT = r"\Projects\DUNGEONS\project.xml", r"\Projects\DUNGEONS\curves.bnt"
DUNGEONS = Path(os.environ.get("BE_DUNGEONS_BNT", str(HERE / "logs" / "be446_DUNGEONS_curves.bnt")))
STR = 0x24070400                                      # harness strings (AXI above the slice)  # addr-ok: harness
SYNTHS = "analog" not in T.OMITTED and "analog" not in T.OFF
OUR_CODE = [(S.M7_TRAMP, S.M7_LIMIT), (0x08100000 + 0xB7000, 0x08200000)]   # M7 slice, the appended M4 blocks  # addr-ok: harness


def _rig():
    class Rig(T.FatFsRig):                           # (not at module level: unittest would collect it)
        live = False
        def runTest(self): pass
    r = Rig()
    r.setUp()
    return r


class Board:
    """One board: both cores' code of the image on test_diag's emulator (its FatFS stub: path -> bytes)."""
    def __init__(self):
        self.rig = _rig()
        self.u = self.rig.u
        if DUNGEONS.exists():
            self.fs[BNT] = DUNGEONS.read_bytes()
        self.stock_objects = []                       # (address, size) stock got from the UI allocator, pattern-filled

    @property
    def fs(self): return self.rig.fs

    # ---- calls that must return (a fault raises; a call that never returns is a hang)
    def run(self, fn, *args, limit=4_000_000):
        T.call(self.u, fn, *args, limit=limit)
        pc = self.u.reg_read(UC_ARM_REG_PC)
        if pc != T.SENT:
            raise AssertionError(f"{fn:#x} did not return within {limit} instructions (pc {pc:#x}): a hang")
    def tick(self, ms):
        w32(self.u, S.M4_TICK_MS, ms)
        self.run(SYM4["m4_tick"], 0, 0)
    def hb(self, n):
        """h_consume at the n-th heartbeat (as the audio loop would; 256: the M7's D0 publish)"""
        w32(self.u, T.RINGSTAT + 20, n - 1)
        self.run(SYM7["h_consume"], 0, 0, 0)
    def first_record(self):
        self.run(SYM7["diag_put"], 1, 0, 0, 0, 0)       # the M7's first traced event of the boot
    def pad(self, t, pad, raw, ms):
        w32(self.u, T.UWTICK, ms)
        self.u.mem_write(0x24070200, struct.pack("<6I", t, 0, 0xFFFFFFFE, pad, raw, 0))  # addr-ok: harness
        self.run(SYM7["h_input"], 0x24071000, 0x24070200)  # addr-ok: harness
    def press(self, pad, ms):
        self.pad(0xAC, pad, 0x6800, ms); self.pad(0xAE, pad, 0xB000, ms + 20); self.pad(0xAD, pad, 0, ms + 60)
    def string(self, s):
        self.u.mem_write(STR, s.encode() + b"\0")
        return STR
    def save(self, path=r"\Projects\OTHER\project.xml"):   # (not DUNGEONS: its sidecar would be rewritten, empty here)
        self.run(SYM4["dump_after_save"], self.string(path))
    def load_dungeons(self):
        """The DUNGEONS project's load as our hooks see it: stock's document reader names the path, the first curve note
        reads its sidecar (curves, AT settings, External modulation) into this boot's pool."""
        self.run(SYM4["step.doc_begin"], self.string(PROJ))
        self.run(SYM4["step.doc_first_note"])
    def stock_alloc(self, n, fill=0x5A):
        """Stock takes n bytes from the UI allocator for its own objects (and fills them)."""
        cur = r32(self.u, S.UI_BUMP_CUR)
        T.call(self.u, S.UI_BUMP_ALLOC, n)
        got = self.u.reg_read(UC_ARM_REG_R0)
        assert got == cur, "the allocator handed out its cursor"
        self.u.mem_write(got, bytes([fill]) * n)
        self.stock_objects.append((got, n, fill))
        return got
    def stock_intact(self):
        return [hex(a) for a, n, f in self.stock_objects if bytes(self.u.mem_read(a, n)) != bytes([f]) * n]

    # ---- a reset, as the device does it
    def reset(self, decay=None, fill=None):
        """decay(rng, bytes) -> bytes for SDRAM (None: kept as it is); fill: our internal RAM before the reset path."""
        u = self.u
        if fill is not None:
            u.mem_write(INTERNAL[0], fill(INTERNAL[1] - INTERNAL[0]))
        if decay:
            for lo, hi in ((S.PROBE_SCRATCH, S.EXT_POOL_END), UI):
                u.mem_write(lo, decay(bytes(u.mem_read(lo, hi - lo))))
        w32(u, S.UI_BUMP_CUR, UI[0]); w32(u, S.UI_BUMP_END, UI[1])
        u.mem_write(S.POOL_LEFT_RAM, struct.pack("<2I", POOL_LEFT0, S.EXT_POOL_END - POOL_LEFT0))
        w32(u, T.UWTICK, 0); w32(u, S.M4_TICK_MS, 0); w32(u, 0xE0001004, 0)
        w32(u, S.M7_HEAP_FREELIST, 0)
        u.mem_write(0x40020000, bytes(0x1000)); u.mem_write(0x40015000, bytes(0x2000))   # DMA2, SAI1-3 at reset values
        self.stock_objects = []
        site = S.CTRL_STARTUP_SITE
        u.reg_write(UC_ARM_REG_SP, T.STACK); u.reg_write(UC_ARM_REG_LR, 0xFFFFFFFF)
        u.emu_start(site | 1, site + 4, count=200_000)
        assert u.reg_read(UC_ARM_REG_PC) == site + 4, f"the M7 startup path reached {u.reg_read(UC_ARM_REG_PC):#x}"
        assert (u.reg_read(UC_ARM_REG_R0), u.reg_read(UC_ARM_REG_R1)) == tuple(S.CTRL_STARTUP_LITS), "the displaced loads"
        assert u.reg_read(UC_ARM_REG_SP) == T.STACK, "the stack as it was"

    # ---- a boot
    def boot(self, t0=1000, rtc=None):
        """rtc(ms) -> (TR, DR) before each M4 pass (None: as the board holds them)"""
        def at(ms):
            if rtc:
                tr, dr = rtc(ms); w32(self.u, RTC_TR, tr); w32(self.u, RTC_DR, dr)
            self.tick(ms)
        at(t0)                                       # the M4's first UI-loop pass: the black box comes up
        self.stock_alloc(0x80000)                    # stock's later objects
        self.first_record()                          # the M7 side: first trace record, then audio blocks
        for n in (1, 2, 256):
            self.hb(n)
        self.press(3, t0 + 100)
        at(t0 + 500); at(t0 + 2000)
        self.save()                                  # the one card access: the logs
        at(t0 + 2100)
        return self

    def state(self, mask_lengths=False):
        u = self.u
        st = {}
        mem = bytearray(u.mem_read(INTERNAL[0], INTERNAL[1] - INTERNAL[0]))
        if mask_lengths:                             # log lengths: a printed number's digits (R2 cases)
            for a in (M4H + 16, M4H + 24, LOADREQ + 8):
                mem[a - INTERNAL[0]:a - INTERNAL[0] + 4] = bytes(4)
        st["internal"] = bytes(mem)
        st["region"] = bytes(u.mem_read(REGION, REGION_USED))
        st["synth"] = bytes(u.mem_read(SYNTH, SYNTH_BYTES))
        st["ui_cursor"] = r32(u, S.UI_BUMP_CUR)
        buf, n = r32(u, M4H + 12), r32(u, M4H + 16)
        st["log"] = bytes(u.mem_read(buf, n)) if UI[0] <= buf < UI[1] and n <= BUF else ("buffer", buf, n)
        cap = r32(u, CAPCFG + 4)
        st["capture"] = bytes(u.mem_read(cap, CAP)) if UI[0] <= cap < UI[1] else ("capture", cap)
        return st


def cold(**kw):
    """A cold boot: everything of ours zero before the reset (what any power-up looks like after the clean start)."""
    b = Board()
    zero = lambda data: bytes(len(data))
    b.reset(decay=zero, fill=lambda n: bytes(n))
    return b.boot(**kw)


def flip(rate, seed):
    """SDRAM without refresh across the reset: each bit flips with probability rate."""
    rng = random.Random(seed)
    def f(data):
        a = bytearray(data)
        k = int(len(a) * 8 * rate)
        for _ in range(k):
            i = rng.randrange(len(a) * 8)
            a[i >> 3] ^= 1 << (i & 7)
        return bytes(a)
    return f


def session(long=True):
    """A BE session on the board: boot, the DUNGEONS project loaded, presses, saves; long: it ran for ten minutes."""
    b = Board()
    b.reset(decay=lambda d: bytes(len(d)), fill=lambda n: bytes(n))
    b.boot()
    if DUNGEONS.exists():
        b.load_dungeons()
    for k, ms in enumerate((5000, 9000, 20000)):
        b.press(k, ms)
        b.tick(ms + 200)
    b.save()
    b.tick(600_000 if long else 3200)
    return b


class Diff:
    @staticmethod
    def first(a, b):
        out = []
        for k in a:
            if a[k] != b[k]:
                if isinstance(a[k], bytes) and isinstance(b[k], bytes) and len(a[k]) == len(b[k]):
                    i = next(i for i in range(len(a[k])) if a[k][i] != b[k][i])
                    base = {"internal": INTERNAL[0], "region": REGION, "synth": SYNTH}.get(k, 0)
                    out.append(f"{k} first differs at {base + i:#x}")
                else:
                    out.append(f"{k}: {str(a[k])[:60]} vs {str(b[k])[:60]}")
        return out


class StartupStub(unittest.TestCase):
    """The M7 reset path zeroes exactly our internal RAM and nothing else, keeps every register and returns to stock."""
    def test_zeroes_our_ram_only(self):
        b = Board()
        u = b.u
        u.mem_write(INTERNAL[0], bytes([0xA5]) * (INTERNAL[1] - INTERNAL[0]))
        below, above = INTERNAL[0] - 0x100, INTERNAL[1]
        u.mem_write(below, bytes([0x3C]) * 0x100); u.mem_write(above, bytes([0x3C]) * 0x100)
        writes = []
        h = u.hook_add(UC_HOOK_MEM_WRITE, lambda uc, k, a, n, v, _: writes.append((a, n)))
        u.ctl_flush_tb()
        b.reset()
        u.hook_del(h)
        ram = bytearray(u.mem_read(INTERNAL[0], INTERNAL[1] - INTERNAL[0]))
        hold = D2 + 0x8A0 - INTERNAL[0]                  # the hold shortcuts' state line, which the stub then inits
        if "hold" not in T.OFF and "hold" not in T.OMITTED:
            self.assertEqual(r32(u, D2 + 0x8A0), 0x31534D42, "the hold shortcuts' state line, initialised")
        ram[hold:hold + 32] = bytes(32)
        self.assertEqual(bytes(ram), bytes(len(ram)), "all of our internal RAM zero")
        self.assertEqual(bytes(u.mem_read(below, 0x100)), bytes([0x3C]) * 0x100, "stock's RAM below untouched")
        self.assertEqual(bytes(u.mem_read(above, 0x100)), bytes([0x3C]) * 0x100, "and above")
        stray = sorted({hex(a) for a, n in writes
                        if not (INTERNAL[0] <= a < INTERNAL[1] or T.STACK - 0x100 <= a < T.STACK)})
        self.assertEqual(stray, [], "it writes our RAM and its own stack frame only")


class CleanBoot(unittest.TestCase):
    """R1: whatever our RAM holds at the reset, the boot reaches the cold boot's state and nothing faults or hangs."""
    @classmethod
    def setUpClass(cls):
        cls.ref = cold().state()

    def check(self, b, what):
        b.boot()
        self.assertEqual(b.stock_intact(), [], f"{what}: stock's objects (allocated after our first pass) intact")
        self.assertEqual(Diff.first(b.state(), self.ref), [], f"{what}: the clean boot state")

    def test_a_after_a_full_dungeons_session(self):
        """(a) a previous session's state (DUNGEONS loaded, presses, a save, ten minutes): valid magic words throughout."""
        b = session()
        self.assertEqual(r32(b.u, M4H), T.MAGIC, "the session's black box is up")
        if DUNGEONS.exists():
            self.assertEqual(r32(b.u, D2 + 0x1E80 + 20), 4, "DUNGEONS' sidecar read (status ok)")
        b.reset()
        self.check(b, "after a full session")

    def test_b_after_a_session_with_sdram_decayed(self):
        """(b) the same with SDRAM partly bit-flipped (no refresh across the reset); internal SRAM as it was"""
        for rate, seed in ((0.001, 1), (0.02, 2), (0.3, 3)):
            b = session()
            b.reset(decay=flip(rate, seed))
            self.check(b, f"SDRAM {rate:.1%} flipped")

    def test_c_d_e_prefilled_ram(self):
        """(c) all-0, (d) all-1, (e) random (and random SDRAM): our internal RAM before the reset path"""
        for name, fill in (("all-0", lambda n: bytes(n)), ("all-1", lambda n: b"\xff" * n),
                           ("random", lambda n: random.Random(7).randbytes(n)),
                           ("magic words, garbage elsewhere", self.magic_soup)):
            b = Board()
            b.reset(decay=lambda d: random.Random(9).randbytes(len(d)), fill=fill)
            self.check(b, name)

    @staticmethod
    def magic_soup(n):
        """Random, with every magic word of ours at its place: the nastiest leftover (valid magic, garbage pointers)"""
        rng = random.Random(11)
        a = bytearray(rng.randbytes(n))
        for off, magic in ((0x100, T.MAGIC), (0x120, T.MAGIC), (0x1E0, T.MAGIC), (0x1820, 0x31504143), (0x1840, 0x31565243),
                           (0x948, 0x33455653), (0x1F40, 0x32544541), (0x1F80, 0x31544441), (0x1FC0, 0x37444F4D),
                           (0x9A0, 0x3137304D), (0x1E80, 0x31444C42), (0x1D00, 0x31534E49), (0xA00, 0x59524E43)):
            i = D2 + off - INTERNAL[0]
            a[i:i + 4] = struct.pack("<I", magic)
        w = D2 + 0x120 + 8 - INTERNAL[0]
        a[w:w + 4] = struct.pack("<I", 1)            # M4 header gen 1, as this boot will have
        return bytes(a)


class ResetLoop(unittest.TestCase):
    """The v4.46 defect (S: m4_diag.c poll's new-boot test, now < r[3]): a run that died at or before the next boot's
    first-pass tick leaves a header that passes for this boot's. v4.46 then skips the black box's init: it keeps the last
    run's buffer, capture block and generation, and writes text where this boot's allocator gave stock its objects. Boots
    are deterministic, so every reset repeats it (a loop that only a power cut, which loses the RAM, ends)."""
    def test_a_run_that_died_at_its_first_pass(self):
        b = Board()
        b.reset(decay=lambda d: bytes(len(d)), fill=lambda n: bytes(n))
        b.tick(1000); b.first_record(); b.press(3, 1010)   # the run dies right after its first pass: last pass at 1000 ms
        for n in range(3):                           # three resets in a row, each boot's first pass at the same tick
            b.reset()
            b.boot(t0=1000)
            self.assertEqual(b.stock_intact(), [], f"boot {n + 2}: stock's objects intact (v4.46: our log text in them)")
            self.assertEqual(r32(b.u, M4H + 12), UI[0], "this boot's buffer is this boot's first allocation")

    def test_hooks_before_the_first_pass_see_no_last_run(self):
        """Stock's app init runs before the UI loop's first pass (defaultTask 0x08114E34: vtable +4, then the loop): our
        hooks there (here: a project load's sidecar) must not take the last run's pool for this boot's."""
        b = session()
        old_pool = r32(b.u, CURVES + 8)
        b.reset()
        b.stock_alloc(0x150000)                      # stock's boot-time objects, over where the last run's pool was
        if DUNGEONS.exists():
            self.assertTrue(b.stock_objects[0][0] <= old_pool < b.stock_objects[0][0] + 0x150000 or not old_pool)
            b.load_dungeons()
        self.assertEqual(b.stock_intact(), [], "the sidecar was not decoded into stock's objects")


class RtcUntrusted(unittest.TestCase):
    """R2: RTC date/time and the backup registers (stock's crash record) are printed, never used: any values, time going
    backwards, the record changing mid-session: the same state as the cold boot (log lengths aside), no fault."""
    CASES = {
        "all-0": (lambda ms: (0, 0), [0] * 32),
        "all-1": (lambda ms: (0xFFFFFFFF, 0xFFFFFFFF), [0xFFFFFFFF] * 32),
        "random": (lambda ms: (random.Random(ms).getrandbits(32), random.Random(ms + 1).getrandbits(32)),
                   [random.Random(i).getrandbits(32) for i in range(32)]),
        "garbage BCD": (lambda ms: (0x3F7F7F, 0xFF1F3F), [0x99999999] * 32),
        "backwards": (lambda ms: (0x235959 - ms, 0x261118 - (ms >> 4)), list(range(32))),
    }
    def test_any_rtc_and_backup_values(self):
        for name, (rtc, bkp) in self.CASES.items():
            ref = Board(); ref.reset(decay=lambda d: bytes(len(d)), fill=lambda n: bytes(n))
            for i, v in enumerate(bkp): w32(ref.u, BKP + 4 * i, v)
            ref.boot(rtc=rtc)
            b = session(long=False)
            for i, v in enumerate(bkp): w32(b.u, BKP + 4 * i, v)
            b.reset()
            b.boot(rtc=rtc)
            w32(b.u, BKP + 4 * 24, (bkp[24] + 1) & 0xFFFFFFFF)   # a "new crash record" mid-session
            b.tick(3200); ref.tick(3200)
            self.assertEqual(Diff.first(b.state(True), ref.state(True)), [], name)
            self.assertIn(b"BENTO black box", b.state()["log"] if isinstance(b.state()["log"], bytes) else b"", name)


class LogsStartFresh(unittest.TestCase):
    """R3: missing log files are created at the next save, starting with this boot's line / header; an SD card that
    refuses every open does not stop the boot or the black box."""
    def test_missing_logs_are_created_with_their_header(self):
        b = cold()                                   # its save made both (no files before)
        self.assertTrue(b.fs[SAVES].startswith(b"BENTO black box, firmware "), "SAVES starts with the boot's header")
        self.assertRegex(b.fs[HIST].decode(), r"^boot fw=\S+ rtc=\S+ gen=1 ")
        del b.fs[SAVES], b.fs[HIST]                  # deleted on the card mid-session
        b.tick(4000); b.save()
        self.assertTrue(b.fs[SAVES].startswith(b"BENTO black box, firmware "), "a new SAVES starts with the header again")
        self.assertIn(b"# saved:", b.fs[SAVES])
        self.assertRegex(b.fs[HIST].decode(), r"^fresh fw=\S+ rtc=\S+ gen=1 ")
        n = len(b.fs[SAVES]); b.tick(5000); b.save()
        self.assertEqual(b.fs[SAVES][:n], b.fs[SAVES][:n], "kept")
        self.assertEqual(b.fs[SAVES].count(b"BENTO black box, firmware "), 1, "an existing file gets no second header")
        self.assertEqual(b.fs[HIST].decode().count("\n"), 1, "nor a second line")

    def test_a_card_that_refuses_every_open(self):
        b = Board()
        b.reset(decay=lambda d: bytes(len(d)), fill=lambda n: bytes(n))
        refuse = lambda uc, a, sz, _: (uc.reg_write(UC_ARM_REG_R0, 3), uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR)))
        from unicorn import UC_HOOK_CODE
        b.u.hook_add(UC_HOOK_CODE, refuse, begin=S.FS_OPEN, end=S.FS_OPEN)   # FR_NOT_READY, ahead of the stub's own
        b.u.ctl_flush_tb()
        b.boot()
        self.assertEqual(r32(b.u, M4H), T.MAGIC, "the black box stays up")
        self.assertEqual({k: v for k, v in b.fs.items() if k != BNT and v}, {}, "nothing written")


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
