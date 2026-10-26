# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for the per-step Condition / Retrigger UI (m4_step.c).

    python test_step.py <stock 1.6.1 bento1.bin> <build dir>

Runs the real patched sites (note XML save/load wrappers with stock-shaped
stack frames, drum encoder and piano event wrappers, header wrappers). Stock's
XML attribute API, note lists, slot/title setters and header builders are
replaced by Python stubs that record what our code asks for.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import *

BASE, SENT = 0x08040000, 0x08000100
OUT = Path(sys.argv[2])
IMG = (OUT / "bento1.bin").read_bytes()
MAN = json.loads((OUT / "manifest.json").read_text())
CFG, MAGIC = 0x240485E0, 0x31474144
REG = 0x30040000                       # fake registry (499 x 32 B)
VIEW = 0x20000000                      # drum step view / piano view (big)
CONT = 0x20200000                      # fake note container (drum) / selection list (piano)
NODE = 0x20300000
STACK = 0x2407E000

XML_FIND, XML_GET, XML_ADD, XML_SET = 0x08109C64, 0x08109566, 0x08109CDC, 0x0810958E
NOTE_GET, NOTE_PUT, PUBLISH, GRID = 0x0810D2C0, 0x0810D1AA, 0x08136544, 0x0815C4B8
STEP_MATCH, ANY_SELECTED, SNAPSHOT = 0x0815B9C0, 0x0815BAB0, 0x0815BA04
SLOT_SET, SLOT_TITLE, BAR_TEXT = 0x08171C1C, 0x0817E85E, 0x08170656
PIANO_SELECTED, PIANO_COMMIT = 0x0815884C, 0x08157860
DRUM_HEADER, DRUM_ENCODER, PIANO_HEADER, PIANO_EVENT = 0x0815C27C, 0x0815CAA0, 0x081599BC, 0x0815A598
STOCK_DRUM_HEADER, STOCK_PIANO_HEADER = 0x0815C280, 0x081599C0   # our replays continue here

STORED = [170, 171, 172, 173] + [v for k in range(35) for v in (100 + k, 135 + k)]
DIVS = ["1/8", "1/8T", "1/8.", "1/16", "1/16T", "1/16.", "1/32", "1/32T", "1/32.", "1/64", "1/64T"]

def step_make(cond=0, hits=0, div=3, ramp=0, locks=0):
    """v2 per-step word (diag_shared.h)."""
    f = 0 if div == 3 else div + 1
    r = 0 if ramp == 0 else int(ramp / 5) + 21
    if not cond and not hits and not f and not r and not locks:
        return 0
    return (0xB6 << 24) | ((cond & 0x7F) << 17) | ((hits & 0xF) << 13) | ((f & 0xF) << 9) | (r << 3) | (locks & 7)

def fields(w):
    """(condition, hits, division, ramp) from a v1 or v2 word."""
    tag = w >> 24
    if tag not in (0xB5, 0xB6):
        return (0, 0, 3, 0)
    f = (w >> 9) & 0xF
    if tag == 0xB5:
        r = w & 0xFF; ramp = r - 256 if r > 127 else r
    else:
        r = (w >> 3) & 0x3F; ramp = (r - 21) * 5 if 1 <= r <= 41 else 0
    return ((w >> 17) & 0x7F, (w >> 13) & 0xF, f - 1 if 1 <= f <= 11 else 3, ramp)

def locks(w):
    return w & 7 if w >> 24 == 0xB6 else 0

def w32(u, a, v): u.mem_write(a, struct.pack("<I", v & 0xFFFFFFFF))
def r32(u, a): return struct.unpack("<I", u.mem_read(a, 4))[0]
def cstr(u, a):
    s = b""
    while True:
        c = bytes(u.mem_read(a, 1))
        if c == b"\0": return s.decode()
        s += c; a += 1

def note(step, pad=2, prob=0, word=0, vel=100, nid=1):
    return [1 | (prob << 8) | (pad << 16), step * 960, 960, nid, vel << 16, word]

class Rig:
    def __init__(self):
        u = self.u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
        u.mem_map(0x08000000, 0x200000); u.mem_write(BASE, IMG); u.mem_write(SENT, b"\xfe\xe7")
        for a, n in ((0x20000000, 0x400000), (0x24000000, 0x80000), (0x30000000, 0x48000), (0xE0000000, 0x100000)):
            u.mem_map(a, n)
        w32(u, CFG, MAGIC); w32(u, CFG + 12, 1)            # M4 boot generation 1
        w32(u, 0x30031484, REG)
        for k in (0x1F2, 0x1F0):                           # some keys at the top are taken natively
            u.mem_write(REG + k * 32, struct.pack("<H", k))
        w32(u, VIEW + 0x113E4, 0x1234); w32(u, VIEW + 0x113DC, CONT); w32(u, VIEW + 0x11400, 2)
        w32(u, VIEW + 0x11CD00, 0x5678)                   # piano view: sequence context
        self.notes = []; self.attrs = {}; self.log = []; self.selected_steps = set()
        u.hook_add(UC_HOOK_CODE, self.hook)

    def ret(self, v=0):
        self.u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def hook(self, u, a, size, _):
        r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
        if a == XML_FIND:
            name = cstr(u, r(1)); self.ret(0x20310000 + len(name) if name in self.attrs else 0); self._last = name
        elif a == XML_GET:
            w32(u, r(1), self.attrs[self._last]); self.ret(0)
        elif a == XML_ADD:
            self._last = cstr(u, r(1)); self.attrs[self._last] = None; self.ret(0x20320000)
        elif a == XML_SET:
            self.attrs[self._last] = r(1); self.ret()
        elif a == NOTE_GET:
            i = r(1)
            if i < len(self.notes):
                u.mem_write(r(2), struct.pack("<6I", *self.notes[i])); self.ret(1)
            else:
                self.ret(0)
        elif a == NOTE_PUT:
            rec = list(struct.unpack("<6I", u.mem_read(r(1), 24)))
            for n in self.notes:
                if n[3] == rec[3]: n[:] = rec
            self.log.append(("put", rec[3])); self.ret()
        elif a in (PUBLISH, GRID, PIANO_COMMIT):
            self.log.append({PUBLISH: "publish", GRID: "grid", PIANO_COMMIT: "commit"}[a]); self.ret()
        elif a == STEP_MATCH:
            self.ret(1 if r(1) in self.selected_steps else 0)
        elif a == ANY_SELECTED:
            self.ret(1 if self.selected_steps else 0)
        elif a == SNAPSHOT:
            sel = [n for n in self.notes if n[1] // 960 in self.selected_steps]
            if sel: u.mem_write(r(1), struct.pack("<6I", *sel[0]))
            self.ret(1 if sel else 0)
        elif a == PIANO_SELECTED:
            if self.notes: u.mem_write(r(3), struct.pack("<6I", *self.notes[0]))
            self.ret()
        elif a == SLOT_SET:
            self.log.append(("slot", r(1), r(2))); self.ret()
        elif a == SLOT_TITLE:
            self.log.append(("title", cstr(u, r(1)))); self.ret()
        elif a == BAR_TEXT:
            self.log.append(("text", r(1), cstr(u, r(2)) if r(2) else None)); self.ret()
        elif a == 0x0815BCB4:                                     # step has a note? (view, visible idx)
            step = r32(u, VIEW + 0x113F4) + r(1)
            self.ret(1 if any(n[1] // 960 == step and (n[0] >> 16) == r32(u, VIEW + 0x11400) for n in self.notes) else 0)
        elif a == 0x0815CE98:                                     # stock set step (view, idx, on)
            step = r32(u, VIEW + 0x113F4) + r(1)
            if r(2):
                self.notes.append(note(step, pad=r32(u, VIEW + 0x11400), vel=r32(u, VIEW + 0x114C0), nid=100 + step))
            self.log.append(("set", step, r(2))); self.ret()
        elif a == 0x0815D1F0:                                     # rest of stock's press handler
            self.log.append(("press", r(1))); self.ret()
        elif a == 0x0812DC74 and r(1) == 0x131:                 # selected track index
            self.ret(2)
        elif a == 0x08133960:                                     # find track -> handle
            w32(u, r(2), 0x77); self.ret(1)
        elif a == 0x0812D80E:                                     # resolve handle -> instrument object
            obj, vt = 0x20140000, 0x20140100
            w32(u, obj, vt); w32(u, vt, 0x081FF101); w32(u, r(2), obj); self.ret(1)
        elif a == 0x081FF100:                                     # instrument vtable[0]: type
            self.ret(getattr(self, "track_kind", 0x0E))
        elif a == 0x08134804:                                     # stock header generator body
            out = r(1)
            u.mem_write(out + 0x74, bytes(6 * 48))
            for slot, label, tab, aid in ((1, b"Step", 1, 0x1046), (2, b"Sequence", 1, 0x1047), (5, b"Undo", 0, 0x1032)):
                rec = out + 0x74 + slot * 48
                u.mem_write(rec, label); u.mem_write(rec + 0x22, bytes([tab])); u.mem_write(rec + 44, struct.pack("<H", aid))
            sp = u.reg_read(UC_ARM_REG_SP)
            for i, reg in enumerate((UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10)):
                u.reg_write(reg, r32(u, sp + 4 * i))
            u.reg_write(UC_ARM_REG_R0, 0)
            u.reg_write(UC_ARM_REG_SP, sp + 32); u.reg_write(UC_ARM_REG_PC, r32(u, sp + 28))
        elif a in (STOCK_DRUM_HEADER, STOCK_PIANO_HEADER):
            # stand-in for stock's header body: return from the replayed prologue's frame
            self.log.append("stock-header")
            sp = u.reg_read(UC_ARM_REG_SP)
            n = 7 if a == STOCK_DRUM_HEADER else 9
            u.reg_write(UC_ARM_REG_SP, sp + 4 * n)
            u.reg_write(UC_ARM_REG_PC, r32(u, sp + 4 * (n - 1)))
            for i, reg in enumerate((UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                     UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)[:n - 1]):
                u.reg_write(reg, r32(u, sp + 4 * i))

    def call(self, addr, *args, stop=SENT):
        for i, v in enumerate(args):
            self.u.reg_write(UC_ARM_REG_R0 + i, v)
        self.u.reg_write(UC_ARM_REG_SP, STACK); self.u.reg_write(UC_ARM_REG_LR, SENT | 1)
        self.u.emu_start(addr | 1, stop, count=500000)
        return self.u.reg_read(UC_ARM_REG_R0)

    def keys(self):
        return [k for k in range(499) if struct.unpack("<H", self.u.mem_read(REG + k * 32, 2))[0] == k
                and r32(self.u, REG + k * 32 + 4) and k not in (0x1F2, 0x1F0)]

    def drum_header(self):
        self.call(DRUM_HEADER, VIEW)

class SaveLoad(unittest.TestCase):
    def run_save(self, rec):
        g = Rig(); sp = STACK - 0x100
        g.u.mem_write(sp + 8, struct.pack("<6I", *rec))
        g.u.reg_write(UC_ARM_REG_R4, NODE); g.u.reg_write(UC_ARM_REG_SP, sp)
        g.u.emu_start(0x0812F4F0 | 1, 0x0812F4F4, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), sp)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_R3), rec[0] & 0xFF, "displaced ldrb replayed")
        return g.attrs

    def run_load(self, rec, attrs):
        g = Rig(); g.attrs = dict(attrs); sp = STACK - 0x100
        g.u.mem_write(sp + 0x24, struct.pack("<6I", *rec)); w32(g.u, sp + 0x3C, 0xABCD)
        g.u.reg_write(UC_ARM_REG_R4, NODE); g.u.reg_write(UC_ARM_REG_SP, sp)
        g.u.emu_start(0x0812FBB4 | 1, 0x0812FBB8, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_R1), sp + 0x24, "displaced add replayed")
        self.assertEqual(g.u.reg_read(UC_ARM_REG_R0), 0xABCD, "displaced ldr replayed")
        return list(struct.unpack("<6I", g.u.mem_read(sp + 0x24, 24)))

    def test_save_writes_only_our_words_on_notes(self):
        w = step_make(cond=5, hits=3, div=4, ramp=-60)
        self.assertEqual(self.run_save(note(0, word=w)), {"be_step_v1": w})
        self.assertEqual(self.run_save(note(0)), {}, "plain note: nothing extra in the project")
        self.assertEqual(self.run_save(note(0, word=0x51000005)), {}, "foreign word (Lab lock) not saved as ours")
        auto = note(0, word=step_make(hits=2)); auto[0] = (auto[0] & ~0xFF) | 2
        self.assertEqual(self.run_save(auto), {}, "automation event: +20 is stock's value2")

    def test_load_round_trip(self):
        w = step_make(cond=7, hits=15, div=10, ramp=100)
        self.assertEqual(self.run_load(note(0), {"be_step_v1": w})[5], w)
        self.assertEqual(self.run_load(note(0), {})[5], 0)
        self.assertEqual(self.run_load(note(0), {"be_step_v1": 0x51000005})[5], 0, "foreign value ignored")

    def test_load_migrates_conditions_out_of_the_probability_byte(self):
        for v in (100, 135, 170, 173):
            rec = self.run_load(note(0, prob=v), {})
            self.assertEqual((rec[0] >> 8) & 0xFF, 0, "probability -> Always")
            self.assertEqual(fields(rec[5])[0], v - 99, "condition moved to the word")
        rec = self.run_load(note(0, prob=50), {})
        self.assertEqual(((rec[0] >> 8) & 0xFF, rec[5]), (50, 0), "plain probability untouched, no lock data invented")
        rec = self.run_load(note(0, prob=120), {"be_step_v1": step_make(hits=2)})
        self.assertEqual(fields(rec[5]), (21, 2, 3, 0), "migration keeps retrigger settings")

class DrumEncoders(unittest.TestCase):
    def setup(self):
        g = Rig()
        g.notes = [note(0, nid=1), note(4, nid=2), note(4, pad=3, nid=3)]
        g.selected_steps = {4}
        g.drum_header()
        return g

    def test_four_keys_claimed_and_put_in_slots_5_to_8(self):
        g = self.setup()
        keys = g.keys()
        self.assertEqual(len(keys), 4)
        self.assertEqual(sorted(keys, reverse=True), [0x1F1, 0x1EF, 0x1EE, 0x1ED], "empty keys from the top")
        table = struct.unpack("<8H", g.u.mem_read(0x300000BC, 16))
        self.assertEqual(table[4:], (0x1F1, 0x1EF, 0x1EE, 0x1ED))
        titles = [cstr(g.u, r32(g.u, REG + k * 32 + 4)) for k in table[4:]]
        self.assertEqual(titles, ["Condition", "Retrig", "Division", "Ramp"])
        counts = [r32(g.u, REG + k * 32 + 12) for k in table[4:]]
        self.assertEqual(counts, [75, 16, 11, 41])
        cond_labels = r32(g.u, REG + table[4] * 32 + 8)
        names = [cstr(g.u, r32(g.u, cond_labels + 4 * i)) for i in range(75)]
        self.assertEqual(names[:6], ["Off", "1ST", "!1ST", "PRE", "!PRE", "1:2"])
        self.assertEqual(names[-1], "!8:8")
        self.assertIn("stock-header", g.log)

    def test_header_shows_the_selected_step(self):
        g = Rig(); g.notes = [note(4, word=step_make(cond=STORED[5] - 99, hits=3, div=4, ramp=-60))]
        g.selected_steps = {4}; g.drum_header()
        slots = [(k, v) for t, k, v in [x for x in g.log if x[0] == "slot"]]
        self.assertEqual(slots, [(0x1F1, 6), (0x1EF, 3), (0x1EE, 4), (0x1ED, 8)])
        self.assertEqual([x[1] for x in g.log if x[0] == "title"], ["Condition", "Retrig", "Division", "Ramp"])
        self.assertFalse(any(x[0] == "text" and x[2] == "--" for x in g.log))

    def test_no_selection_shows_dashes(self):
        g = Rig(); g.drum_header()
        self.assertEqual([x for x in g.log if x[0] == "text" and x[2] == "--"],
                         [("text", 4, "--"), ("text", 5, "--"), ("text", 6, "--"), ("text", 7, "--")])

    def edit(self, g, which, value):
        key = struct.unpack("<8H", g.u.mem_read(0x300000BC, 16))[4 + which]
        g.log.clear()
        return g.call(DRUM_ENCODER, VIEW, key, value)

    def test_edits_apply_to_selected_steps_on_this_pad_only(self):
        g = self.setup()
        self.assertEqual(self.edit(g, 1, 3), 0, "handled: stock's function returns 0")
        self.assertEqual(fields(g.notes[1][5]), (0, 3, 3, 0))
        self.assertEqual(g.notes[0][5], 0, "unselected step untouched")
        self.assertEqual(g.notes[2][5], 0, "other pad untouched")
        self.assertIn("publish", g.log); self.assertIn("grid", g.log); self.assertIn("stock-header", g.log)
        self.edit(g, 2, 4); self.edit(g, 3, 8); self.edit(g, 0, 6)
        self.assertEqual(fields(g.notes[1][5]), (STORED[5] - 99, 3, 4, -60))

    def test_clearing_every_field_restores_a_stock_note(self):
        g = self.setup()
        self.edit(g, 1, 3); self.edit(g, 3, 0)
        self.edit(g, 1, 0); self.edit(g, 3, 20)
        self.assertEqual(g.notes[1][5], 0)

    def test_values_clamp(self):
        g = self.setup()
        self.edit(g, 1, 99); self.edit(g, 3, 999)
        self.assertEqual(fields(g.notes[1][5])[1:], (15, 3, 100))

    def test_nothing_selected_does_nothing(self):
        g = self.setup(); g.selected_steps = set()
        self.assertEqual(self.edit(g, 1, 3), 0)
        self.assertEqual([n[5] for n in g.notes], [0, 0, 0])

    def test_stock_keys_go_to_stock(self):
        g = self.setup()
        g.u.reg_write(UC_ARM_REG_SP, STACK)
        for i, v in enumerate((VIEW, 0x184, 50)):
            g.u.reg_write(UC_ARM_REG_R0 + i, v)
        g.u.emu_start(DRUM_ENCODER | 1, DRUM_ENCODER + 4, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_R3), VIEW + 0x11000, "displaced add replayed")
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK)

class Piano(unittest.TestCase):
    def test_condition_on_slot_7_and_edit_commits(self):
        g = Rig(); g.notes = [note(2, nid=9)]
        g.call(PIANO_HEADER, VIEW)
        self.assertIn("stock-header", g.log)
        key = struct.unpack("<H", g.u.mem_read(VIEW + 0x11BCC0 + 12, 2))[0]
        self.assertEqual(key, 0x1F1)
        self.assertIn(("slot", 0x1F1, 0), g.log)
        ev = 0x20100000
        g.u.mem_write(ev, struct.pack("<5I", 0xCF, 0, key, 3, 0))
        g.log.clear()
        g.u.reg_write(UC_ARM_REG_R1, ev)
        self.assertEqual(g.call(PIANO_EVENT, VIEW, ev), 0)
        self.assertEqual(fields(g.notes[0][5])[0], STORED[2] - 99, "PRE")
        self.assertIn("commit", g.log)

    def test_other_piano_events_go_to_stock(self):
        g = Rig(); g.call(PIANO_HEADER, VIEW)
        ev = 0x20100000
        g.u.mem_write(ev, struct.pack("<5I", 0xCF, 0, 0x184, 3, 0))
        g.u.reg_write(UC_ARM_REG_SP, STACK)
        g.u.reg_write(UC_ARM_REG_R0, VIEW); g.u.reg_write(UC_ARM_REG_R1, ev)
        g.u.emu_start(PIANO_EVENT | 1, PIANO_EVENT + 4, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK - 20 - 0x174, "replayed push + sub")

class LegacyLocks(unittest.TestCase):
    """Notes without lock data load unlocked (v3.4's 100/Always/0 rule locked
    typical steps whose value simply wasn't 100; it is gone)."""
    def load(self, rec, attrs=None):
        return SaveLoad.run_load(SaveLoad(), rec, attrs or {})

    def test_no_lock_data_means_unlocked(self):
        for rec in (note(0), note(0, vel=80), note(0, prob=30)):
            self.assertEqual(self.load(rec)[5], 0)

    def test_v1_words_keep_settings(self):
        v1 = (0xB5 << 24) | (5 << 17) | (3 << 13) | ((4 + 1) << 9) | ((-60) & 0xFF)
        w = self.load(note(0, vel=70), {"be_step_v1": v1})[5]
        self.assertEqual((fields(w), locks(w)), ((5, 3, 4, -60), 0))

    def test_v2_lock_bits_survive_a_round_trip(self):
        w = step_make(hits=2, locks=1)
        self.assertEqual(self.load(note(0), {"be_step_v1": w})[5], w)

class Defaults(unittest.TestCase):
    VEL, PROB, OFFSET = 0x0815C7C4, 0x0815C8AC, 0x0815C990

    def rig(self, notes, selected=()):
        g = Rig(); g.notes = notes; g.selected_steps = set(selected)
        w32(g.u, VIEW + 0x114C0, 100); w32(g.u, VIEW + 0x114C4, 0); w32(g.u, VIEW + 0x114C8, 0)
        return g

    def vel(self, n): return n[4] >> 16

    def test_default_change_moves_unlocked_notes_only(self):
        g = self.rig([note(0, nid=1), note(1, nid=2), note(2, vel=80, nid=3),
                      note(3, vel=60, word=step_make(locks=1), nid=4), note(4, pad=3, nid=5)])
        g.call(self.VEL, VIEW, 90)
        self.assertEqual(r32(g.u, VIEW + 0x114C0), 90, "stock set the default")
        self.assertEqual([self.vel(n) for n in g.notes], [90, 90, 80, 60, 100])
        self.assertEqual([locks(n[5]) for n in g.notes], [0, 0, 1, 1, 0], "recorded 80 got locked, other pad untouched")
        self.assertIn("publish", g.log)

    def test_editing_a_held_step_locks_it_and_back_to_default_unlocks(self):
        g = self.rig([note(0, nid=1), note(1, nid=2)], selected={1})
        g.call(self.VEL, VIEW, 70)
        self.assertEqual((self.vel(g.notes[1]), locks(g.notes[1][5])), (70, 1))
        g.call(self.VEL, VIEW, 100)
        self.assertEqual((self.vel(g.notes[1]), g.notes[1][5]), (100, 0), "back to default: plain note again")
        g.selected_steps = set(); g.call(self.VEL, VIEW, 110)
        self.assertEqual([self.vel(n) for n in g.notes], [110, 110])

    def test_locked_note_keeps_value_through_default_changes(self):
        g = self.rig([note(0, nid=1), note(1, nid=2)], selected={1})
        g.call(self.VEL, VIEW, 70)
        g.selected_steps = set()
        g.call(self.VEL, VIEW, 70); g.call(self.VEL, VIEW, 40)
        self.assertEqual([self.vel(n) for n in g.notes], [40, 70], "a lock is a lock even if it once matched")

    def test_stock_field_reset_does_not_lock_the_steps(self):
        # the v3.4 device bug: steps at 80, stock reset its field to 100, encoder turned
        g = self.rig([note(0, vel=80, nid=1), note(1, vel=80, nid=2), note(2, vel=50, nid=3)])
        w32(g.u, VIEW + 0x114C0, 100)
        g.call(self.VEL, VIEW, 90)
        self.assertEqual([self.vel(n) for n in g.notes], [90, 90, 50])
        self.assertEqual([locks(n[5]) for n in g.notes], [0, 0, 1])

    def test_probability_and_offset_defaults(self):
        g = self.rig([note(0, nid=1), note(1, prob=20, nid=2)])
        g.call(self.PROB, VIEW, 49)                                  # list index 49 -> stored 50
        self.assertEqual([(n[0] >> 8) & 0xFF for n in g.notes], [50, 20])
        self.assertEqual(locks(g.notes[1][5]), 2)
        g.call(self.OFFSET, VIEW, 12)                                # 120 ticks
        self.assertEqual([n[1] for n in g.notes], [120, 960 + 120])

    def test_switching_pad_loads_its_defaults(self):
        g = self.rig([note(0, vel=80, nid=1), note(1, vel=80, nid=2), note(2, vel=50, nid=3),
                      note(3, vel=30, word=step_make(locks=1), nid=4), note(0, pad=5, vel=64, nid=5)])
        g.drum_header()
        self.assertEqual(r32(g.u, VIEW + 0x114C0), 80, "most common unlocked value on pad 2")
        w32(g.u, VIEW + 0x11400, 5); g.drum_header()
        self.assertEqual(r32(g.u, VIEW + 0x114C0), 64)
        w32(g.u, VIEW + 0x114C0, 100); g.drum_header()
        self.assertEqual(r32(g.u, VIEW + 0x114C0), 64, "stock's reset of its field is corrected from the notes")


class StrikeVelocity(unittest.TestCase):
    STRIKE = 0x24048D60

    def rig(self, base=0):
        g = Rig(); w32(g.u, VIEW + 0x114C0, 100); w32(g.u, VIEW + 0x113F4, base)
        return g

    def strike(self, g, seq, vel, pad=4):
        g.u.mem_write(self.STRIKE, struct.pack("<8I", 0x4B525453, seq, pad, vel, 0, 0, 0, 0))

    def tap(self, g, pad, ms=5000):
        w32(g.u, 0x30035F40, ms)
        g.call(0x0815D1EC, VIEW, pad)                 # press (our hook, then stock's handler)
        g.call(0x0815D114, VIEW, pad)                 # stock's release -> toggle

    def test_tapped_step_takes_the_strike_and_locks(self):
        g = self.rig(); self.strike(g, 1, 37)
        self.tap(g, 4)
        self.assertIn(("press", 4), g.log)
        self.assertEqual([(n[1] // 960, n[4] >> 16, locks(n[5])) for n in g.notes], [(4, 37, 1)])

    def test_strike_equal_to_default_is_not_a_lock(self):
        g = self.rig(); self.strike(g, 1, 100)
        self.tap(g, 2)
        self.assertEqual([(n[4] >> 16, n[5]) for n in g.notes], [(100, 0)])

    def test_stale_or_touch_keeps_the_default(self):
        g = self.rig(); self.strike(g, 1, 37); self.tap(g, 4)
        g.call(0x0815D114, VIEW, 6)                   # touchscreen toggle: no press
        self.assertEqual([n[4] >> 16 for n in g.notes], [37, 100], "touch = default")
        g = self.rig(); self.strike(g, 1, 37); self.tap(g, 4)
        self.tap(g, 7)                                # no new pad press seen by the M7 (same seq)
        self.assertEqual([n[4] >> 16 for n in g.notes], [37, 100], "a strike is used once")

    def test_removing_a_step_changes_nothing(self):
        g = self.rig(); g.notes = [note(4, vel=90, nid=1)]; self.strike(g, 1, 37)
        self.tap(g, 4)
        self.assertIn(("set", 4, 0), g.log)
        self.assertEqual(g.notes[0][4] >> 16, 90)

    def test_scrolled_view_uses_the_absolute_step(self):
        g = self.rig(base=16); self.strike(g, 1, 64)
        self.tap(g, 3)
        self.assertEqual([(n[1] // 960, n[4] >> 16) for n in g.notes], [(19, 64)])

    def test_drum_menu_is_stock(self):
        stock = Path(sys.argv[1]).read_bytes()
        for a, n in ((0x081415F8, 2), (0x08198E00, 20)):
            self.assertEqual(IMG[a - BASE:a - BASE + n], stock[a - BASE:a - BASE + n], hex(a))

class StrikeButton(unittest.TestCase):
    OUT, ROOT = 0x20180000, 0x30034D48

    def rig(self, kind=0x0E):
        g = Rig()
        g.u.mem_write(0x30024B00 + 0x12C, bytes([0x27]))           # SEQ page
        w32(g.u, self.ROOT, 0x081B3680)                             # header root vtable
        g.track_kind = kind                                         # selected track's instrument type
        return g

    def generate(self, g):
        changed = g.call(0x08134800, 0x30024B00, self.OUT, 0x20190000, 1)
        recs = []
        for i in range(6):
            raw = bytes(g.u.mem_read(self.OUT + 0x74 + i * 48, 48))
            recs.append((raw[:raw.index(b"\0")].decode(), raw[0x21], struct.unpack_from("<H", raw, 44)[0]))
        return changed, recs

    def test_button_left_of_undo_lit_while_on(self):
        g = self.rig(); g.drum_header()
        changed, recs = self.generate(g)
        self.assertEqual(changed, 1)
        self.assertEqual(recs[4], ("Strike Vel", 1, 0x10F0))
        self.assertEqual([r[0] for r in recs], ["", "Step", "Sequence", "", "Strike Vel", "Undo"])
        self.assertEqual(bytes(g.u.mem_read(self.ROOT + 0x117C, 1))[0], 1, "layout re-armed so the slot shows")

    def test_no_button_for_a_pitched_track(self):
        g = self.rig(kind=0x10); g.drum_header()                    # piano roll (pitched instrument)
        changed, recs = self.generate(g)
        self.assertNotIn(0x10F0, [r[2] for r in recs])
        self.assertEqual(r32(g.u, 0x24048D48 + 16) & 0xFF, 0xF0, "diag: drum=0, slot=none")

    def test_button_shows_even_if_the_piano_header_ran_last(self):
        g = self.rig(); g.drum_header(); g.call(0x081599BC, VIEW)
        changed, recs = self.generate(g)
        self.assertEqual(recs[4][2], 0x10F0)
        self.assertEqual(r32(g.u, 0x24048D48 + 16) & 0x1FF, 0x141, "diag: drum=1, slot=4, inserted")

    def test_button_toggles_lit_and_the_behaviour(self):
        g = self.rig(); g.drum_header()
        ev = 0x20100000; g.u.mem_write(ev, struct.pack("<HH4I", 0x10F0, 0, 0, 0, 0, 0))
        self.assertEqual(g.call(0x0815D600, VIEW, ev), 0, "handled")
        self.assertEqual(self.generate(g)[1][4], ("Strike Vel", 0, 0x10F0), "outline off")
        sv = StrikeVelocity(); sv.STRIKE = 0x24048D60
        w32(g.u, VIEW + 0x114C0, 100); sv.strike(g, 1, 37); sv.tap(g, 4)
        self.assertEqual(g.notes[-1][4] >> 16, 100, "off: tapped step gets the default")
        g.call(0x0815D600, VIEW, ev)
        self.assertEqual(self.generate(g)[1][4][1], 1, "on again")

    def test_sequence_tab_press_arrives_at_the_piano_view_object(self):
        g = self.rig(); g.drum_header()
        ev = 0x20100000; g.u.mem_write(ev, struct.pack("<HH4I", 0x10F0, 0, 0, 0, 0, 0))
        self.assertEqual(g.call(0x0815A598, VIEW, ev), 0, "handled by the piano-roll event hook")
        self.assertEqual(self.generate(g)[1][4], ("Strike Vel", 0, 0x10F0))

    def test_press_on_a_pitched_track_is_ignored(self):
        g = self.rig(kind=0x10)
        ev = 0x20100000; g.u.mem_write(ev, struct.pack("<HH4I", 0x10F0, 0, 0, 0, 0, 0))
        g.u.reg_write(UC_ARM_REG_SP, STACK); g.u.reg_write(UC_ARM_REG_R0, VIEW); g.u.reg_write(UC_ARM_REG_R1, ev)
        g.u.emu_start(0x0815A598 | 1, 0x0815A59C, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_PC), 0x0815A59C, "went on to stock")

    def test_other_drum_events_go_to_stock(self):
        g = self.rig()
        ev = 0x20100000; g.u.mem_write(ev, struct.pack("<HH4I", 0xCE, 0, 0, 0, 0, 0))
        g.u.reg_write(UC_ARM_REG_SP, STACK); g.u.reg_write(UC_ARM_REG_R0, VIEW); g.u.reg_write(UC_ARM_REG_R1, ev)
        g.u.emu_start(0x0815D600 | 1, 0x0815D604, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK - 4 - 0x6C, "replayed push {lr}; sub sp, #0x6c")

class StrikeInsert(unittest.TestCase):
    """Notes created by any view (drum Sequence page) through stock's insert 0x0812E070."""
    def rig(self, kind=0x0E):
        g = StrikeButton().rig(kind)
        return g

    def press(self, g, seq, vel, ms):
        g.u.mem_write(0x24048D60, struct.pack("<8I", 0x4B525453, seq, 3, vel, 0, 0, 0, 0))
        w32(g.u, 0x30035F40, ms)
        StrikeButton().generate(g)                    # a UI tick sees the strike (stamp)

    def insert(self, g, rec, ms):
        w32(g.u, 0x30035F40, ms)
        at = 0x20110000; g.u.mem_write(at, struct.pack("<6I", *rec))
        g.u.reg_write(UC_ARM_REG_SP, STACK)
        for i, v in enumerate((0x30024B00, 0x1234, at)):
            g.u.reg_write(UC_ARM_REG_R0 + i, v)
        g.u.emu_start(0x0812E070 | 1, 0x0812E074, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK - 16 - 0x20, "replayed push + sub")
        return list(struct.unpack("<6I", g.u.mem_read(at, 24)))

    def test_fresh_strike_sets_velocity_and_locks_once(self):
        g = self.rig(); self.press(g, 5, 44, 10000)
        rec = self.insert(g, note(2, nid=0), 10200)
        self.assertEqual((rec[4] >> 16, locks(rec[5])), (44, 1))
        rec = self.insert(g, note(3, nid=0), 10250)
        self.assertEqual((rec[4] >> 16, rec[5]), (100, 0), "a strike is used once")

    def test_stale_strike_is_ignored(self):
        g = self.rig(); self.press(g, 5, 44, 10000)
        self.assertEqual(self.insert(g, note(2, nid=0), 10600)[4] >> 16, 100)

    def test_pitched_tracks_and_non_notes_untouched(self):
        g = self.rig(kind=0x10); self.press(g, 5, 44, 10000)
        self.assertEqual(self.insert(g, note(2, nid=0), 10100)[4] >> 16, 100)
        g = self.rig(); self.press(g, 5, 44, 10000)
        auto = note(2, nid=0); auto[0] = (auto[0] & ~0xFF) | 2
        self.assertEqual(self.insert(g, auto, 10100), auto)

    def test_step_view_path_is_not_applied_twice(self):
        g = self.rig(); self.press(g, 5, 44, 10000)
        w32(g.u, 0x24048C80 + 20, 5)                  # SU->r[0]: the Step view's press consumed seq 5
        self.assertEqual(self.insert(g, note(2, nid=0), 10100)[4] >> 16, 100)

    def test_off_means_off(self):
        g = self.rig(); self.press(g, 5, 44, 10000)
        w32(g.u, 0x24048D48 + 4, 1)                   # StrikeSet.off
        self.assertEqual(self.insert(g, note(2, nid=0), 10100)[4] >> 16, 100)

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
