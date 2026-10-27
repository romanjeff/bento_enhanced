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
CFG, MAGIC = 0x3003C1E0, 0x31474144
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
        w32(u, 0x3000004C, 0x20380000); w32(u, 0x30000048, 0x203C0000)   # native bump allocator range
        w32(u, 0x30031484, REG)
        for k in (0x1F2, 0x1F0):                           # some keys at the top are taken natively
            u.mem_write(REG + k * 32, struct.pack("<H", k))
        w32(u, VIEW + 0x113E4, 0x1234); w32(u, VIEW + 0x113DC, CONT); w32(u, VIEW + 0x11400, 2)
        w32(u, VIEW + 0x11CD00, 0x5678)                   # piano view: sequence context
        self.notes = []; self.attrs = {}; self.log = []; self.selected_steps = set()
        self.files = {}; self.fils = {}; self.opened = []
        u.hook_add(UC_HOOK_CODE, self.hook)

    def ret(self, v=0):
        self.u.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
        self.u.reg_write(UC_ARM_REG_PC, self.u.reg_read(UC_ARM_REG_LR))

    def hook(self, u, a, size, _):
        r = lambda i: u.reg_read(UC_ARM_REG_R0 + i)
        if a == 0x08120148:                                      # f_open(fil, path, mode): read-only files
            path = cstr(u, r(1)); self.opened.append(path)
            if path in self.files and r(2) == 0x01:
                self.fils[r(0)] = [path, 0]; self.ret(0)
            else:
                self.ret(4)                                      # FR_NO_FILE
            return
        if a == 0x08120464:                                      # f_read(fil, buf, n, &got)
            f = self.fils[r(0)]; data = self.files[f[0]][f[1]:f[1] + r(2)]
            u.mem_write(r(1), data); w32(u, r(3), len(data)); f[1] += len(data); self.ret(0)
            return
        if a == 0x08120974:                                      # f_close
            self.fils.pop(r(0), None); self.ret(0)
            return
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
            pad = r32(u, VIEW + 0x11400)
            if r(2):
                self.notes.append(note(step, pad=pad, vel=r32(u, VIEW + 0x114C0), nid=100 + step))
            else:
                self.notes = [n for n in self.notes if not (n[1] // 960 == step and (n[0] >> 16) == pad)]
            self.log.append(("set", step, r(2))); self.ret()
        elif a == 0x0815D1F0:                                     # rest of stock's press handler
            self.log.append(("press", r(1))); self.ret()
        elif a == 0x0813130C:                                     # stock: refresh after an edit
            self.log.append("touch"); self.ret()
        elif a == 0x0812DC74 and r(1) == 0x131:                 # selected track index
            self.ret(getattr(self, "selected", 2))
        elif a == 0x0812DC74 and r(1) in (0x0F, 0x10):          # a track's MIDI out port / channel
            port, ch = getattr(self, "dest", (1, 3))
            self.ret(port if r(1) == 0x0F else ch)
        elif a == 0x08133960:                                     # find track -> handle
            self.model_calls = getattr(self, "model_calls", 0) + 1
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

    def test_curve_note_saves_its_sidecar_index(self):
        """v3.26: the per-boot handle never reaches the project; the word carries the note's
        1-based index in this save, and the handle goes into the save list."""
        g = Rig(); sp = STACK - 0x100
        g.u.mem_write(0x3003D840, struct.pack("<10I", 0x31565243, 1, 0x20300000, 8, 0x20310000, 5, 0, 0,
                                              0x20320000, 0))
        w = (0xB7 << 24) | (4 << 17) | (3 << 3) | 1               # condition 4, handle 3, velocity lock
        g.u.mem_write(sp + 8, struct.pack("<6I", *note(0, word=w)))
        g.u.reg_write(UC_ARM_REG_R4, NODE); g.u.reg_write(UC_ARM_REG_SP, sp)
        g.u.emu_start(0x0812F4F0 | 1, 0x0812F4F4, count=200000)
        self.assertEqual(g.attrs, {"be_step_v1": (0xB7 << 24) | (4 << 17) | (1 << 3) | 1}, "index 1, not handle 3")
        self.assertEqual(struct.unpack("<H", g.u.mem_read(0x20320000, 2))[0], 3, "save list holds the handle")
        self.assertEqual(r32(g.u, 0x3003D840 + 36), 1, "nsave")
        self.assertEqual(bytes(g.u.mem_read(sp + 8 + 20, 4)), struct.pack("<I", w), "the note in RAM keeps its handle")

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
        self.assertEqual(len(keys), 6, "4 step keys + AT Rec + AT Out (v3.30)")
        self.assertEqual(sorted(keys, reverse=True), [0x1F1, 0x1EF, 0x1EE, 0x1ED, 0x1EC, 0x1EB], "empty keys from the top")
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

class AtSettings(unittest.TestCase):
    """M2-5: piano roll Sequence view, encoders 5/6 = AT Rec (Off/On) and AT Out
    (Off/Poly/Channel) for the selected track; published for the M7 by the track's
    MIDI out port/channel; AT Rec Off stops curves attaching."""
    AT_SET, AT_DEST = 0x3003DF40, 0x3003DF80

    def rig(self, selected=2):
        g = Rig(); g.selected = selected; g.dest = (1, 3)
        g.u.mem_write(VIEW + 0x125DAC, b"\x01")                 # Sequence view (not note edit)
        g.call(PIANO_HEADER, VIEW)
        return g

    def slot_key(self, g, s):
        return struct.unpack("<H", g.u.mem_read(VIEW + 0x11BCC0 + 2 * s, 2))[0]

    def turn(self, g, key, value):
        ev = 0x20100000
        g.u.mem_write(ev, struct.pack("<5I", 0xCF, 0, key, value, 0))
        g.log.clear()
        return g.call(PIANO_EVENT, VIEW, ev)

    def test_defaults_shown_on_encoders_5_and_6(self):
        g = self.rig()
        rec, out = self.slot_key(g, 4), self.slot_key(g, 5)
        self.assertEqual((rec, out), (0x1EC, 0x1EB))
        self.assertIn(("slot", rec, 1), g.log, "AT Rec: On")
        self.assertIn(("slot", out, 1), g.log, "AT Out: Poly")
        self.assertIn(("title", "AT Rec"), g.log)
        self.assertEqual(self.slot_key(g, 6), 0, "note-edit Condition slot untouched in Sequence view")

    def test_turning_sets_the_selected_track_and_publishes(self):
        g = self.rig(selected=2)
        self.assertEqual(self.turn(g, self.slot_key(g, 5), 0), 0, "handled")
        self.assertEqual(bytes(g.u.mem_read(self.AT_SET + 8 + 16 + 2, 1))[0], 1, "track 2: out Off")
        self.assertEqual(r32(g.u, self.AT_DEST), 0x31544441, "published")
        d = bytes(g.u.mem_read(self.AT_DEST + 8 + 4 * 2, 4))
        self.assertEqual(tuple(d), (1, 3, 1, 1), "track 2: port 1, ch 3, mode Off, valid")
        self.turn(g, self.slot_key(g, 4), 0)
        self.assertEqual(bytes(g.u.mem_read(self.AT_SET + 8 + 2, 1))[0], 1, "track 2: rec Off")

    def test_a_change_marks_the_project_modified(self):
        g = self.rig()
        g.u.mem_write(0x30024B00 + 0x36C4, b"\x00")
        self.turn(g, self.slot_key(g, 5), 0)
        self.assertEqual(bytes(g.u.mem_read(0x30024B00 + 0x36C4, 1))[0], 1, "modified: Save enabled")
        self.assertIn("touch", g.log)

    def test_rec_off_stops_curves_attaching(self):
        c = CurveAttach(); g = c.rig()
        g.selected = 2
        g.u.mem_write(self.AT_SET, struct.pack("<II", 0x32544541, 1) + bytes([0, 0, 1]) + bytes(29))
        c.slot(g, 5, 1, 1, [10, 20])
        self.assertEqual(c.note_off(g, 60)[5], 0, "AT Rec Off: no curve")
        g.u.mem_write(self.AT_SET + 8 + 2, b"\x00")
        self.assertEqual(c.note_off(g, 60)[5] >> 24, 0xB7, "back On: curve")


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
    STRIKE = 0x3003C960

    def rig(self, base=0):
        g = Rig(); w32(g.u, VIEW + 0x114C0, 100); w32(g.u, VIEW + 0x113F4, base)
        g.u.mem_write(0x30024B00 + 0x12C, bytes([0x27])); w32(g.u, 0x30034D48, 0x081B3680)
        StrikeButton().generate(g)                    # first UI tick: per-pad state exists before any press
        return g

    def strike(self, g, seq, vel, pad=4):
        g.u.mem_write(self.STRIKE + pad, bytes([seq & 0xFF]))        # per-pad press counter
        g.u.mem_write(self.STRIKE + 16 + pad, bytes([vel]))          # per-pad peak velocity

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
        self.tap(g, 4); self.tap(g, 4)                # off, on again: no new press of pad 4
        self.assertEqual([n[4] >> 16 for n in g.notes][-1], 100, "a strike is used once")

    def test_pads_hit_together_each_get_their_own_velocity(self):
        g = self.rig()
        for pad, vel in ((2, 30), (5, 90), (9, 61)):
            self.strike(g, 1, vel, pad=pad)
        w32(g.u, 0x30035F40, 5000)
        for pad in (2, 5, 9):
            g.call(0x0815D1EC, VIEW, pad)             # all three pressed
        for pad in (9, 2, 5):
            g.call(0x0815D114, VIEW, pad)             # released in another order
        self.assertEqual(sorted((n[1] // 960, n[4] >> 16) for n in g.notes), [(2, 30), (5, 90), (9, 61)])

    def test_removing_a_step_changes_nothing(self):
        g = self.rig(); g.notes = [note(4, vel=90, nid=1)]; self.strike(g, 1, 37)
        self.tap(g, 4)
        self.assertIn(("set", 4, 0), g.log)
        self.assertEqual(g.notes, [], "removed, and the strike didn't create anything")

    def test_scrolled_view_uses_the_absolute_step(self):
        g = self.rig(base=16); w32(g.u, VIEW + 0x11404, 16)          # bank 16 shown (pads = steps 16..31)
        self.strike(g, 1, 64, pad=3)
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

    def test_survives_v3_11_leftovers_in_reserved_ram(self):
        """Reserved RAM survives a reset: v3.11 left StrikeSet {'SVE2', off, view=UI ms}.
        v3.12 read that field as a pointer and faulted on every boot (boot loop)."""
        for stale in (2204624, 1, 0xDEADBEE0):
            g = self.rig(); g.drum_header()
            g.u.mem_write(0x3003C948, struct.pack("<6I", 0x32455653, 0, stale, 0, 0, 0))
            changed, recs = self.generate(g)                        # must not fault
            self.assertEqual(recs[4], ("Strike Vel", 1, 0x10F0))
        g = self.rig(); g.drum_header()                             # same layout, unchecked pointer
        g.u.mem_write(0x3003C948, struct.pack("<6I", 0x33455653, 0, 0xDEADBEE0, 0, 0, 0))
        self.assertEqual(self.generate(g)[1][4], ("Strike Vel", 1, 0x10F0))

    def test_no_button_for_a_pitched_track(self):
        g = self.rig(kind=0x10); g.drum_header()                    # piano roll (pitched instrument)
        changed, recs = self.generate(g)
        self.assertNotIn(0x10F0, [r[2] for r in recs])
        self.assertEqual(r32(g.u, 0x3003C948 + 16) & 0xFF, 0xF0, "diag: drum=0, slot=none")

    def test_button_shows_even_if_the_piano_header_ran_last(self):
        g = self.rig(); g.drum_header(); g.call(0x081599BC, VIEW)
        changed, recs = self.generate(g)
        self.assertEqual(recs[4][2], 0x10F0)
        self.assertEqual(r32(g.u, 0x3003C948 + 16) & 0x1FF, 0x141, "diag: drum=1, slot=4, inserted")

    def test_button_toggles_lit_and_the_behaviour(self):
        g = self.rig(); g.drum_header()
        ev = 0x20100000; g.u.mem_write(ev, struct.pack("<HH4I", 0x10F0, 0, 0, 0, 0, 0))
        self.assertEqual(g.call(0x0815D600, VIEW, ev), 0, "handled")
        self.assertEqual(self.generate(g)[1][4], ("Strike Vel", 0, 0x10F0), "outline off")
        sv = StrikeVelocity(); sv.STRIKE = 0x3003C960
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

    def press(self, g, seq, vel, ms, pad=2, tick=True):
        StrikeButton().generate(g)                    # per-pad state exists (old presses don't count)
        g.u.mem_write(0x3003C960 + pad, bytes([seq])); g.u.mem_write(0x3003C970 + pad, bytes([vel]))
        w32(g.u, 0x30035F40, ms)
        if tick:
            StrikeButton().generate(g)                # a UI tick sees the strike (stamp)

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
        w32(g.u, 0x30035F40, 10050); g.call(0x0815D1EC, VIEW, 2)   # the Step view's press used pad 2's strike
        self.assertEqual(self.insert(g, note(2, nid=0), 10100)[4] >> 16, 100)

    def test_insert_before_any_ui_tick_still_counts_as_fresh(self):
        g = self.rig(); self.press(g, 5, 44, 10000, tick=False)
        self.assertEqual(self.insert(g, note(2, nid=0), 10010)[4] >> 16, 44)

    def test_sequence_page_pads_are_steps(self):
        g = self.rig(); self.press(g, 3, 20, 10000, pad=5); self.press(g, 4, 110, 10000, pad=9)
        self.assertEqual(self.insert(g, note(9, pad=2, nid=0), 10100)[4] >> 16, 110, "step 9 <- pad 9")
        self.assertEqual(self.insert(g, note(21, pad=2, nid=0), 10100)[4] >> 16, 20, "step 21 <- pad 5 (page 2)")

    def test_any_fresh_pad_when_the_position_doesnt_match(self):
        g = self.rig(); self.press(g, 3, 77, 10000, pad=11)
        self.assertEqual(self.insert(g, note(4, pad=2, nid=0), 10100)[4] >> 16, 77)

    def test_insert_uses_the_note_s_own_pad(self):
        g = self.rig(); self.press(g, 5, 44, 10000, pad=2); self.press(g, 7, 99, 10000, pad=6)
        self.assertEqual(self.insert(g, note(12, pad=6, nid=0), 10100)[4] >> 16, 99, "no pad at step 12: drum's pad")
        self.assertEqual(self.insert(g, note(12, pad=2, nid=0), 10100)[4] >> 16, 44)

    def test_off_means_off(self):
        g = self.rig(); self.press(g, 5, 44, 10000)
        w32(g.u, 0x3003C948 + 4, 1)                   # StrikeSet.off
        self.assertEqual(self.insert(g, note(2, nid=0), 10100)[4] >> 16, 100)

class CurveAttach(unittest.TestCase):
    """M2-1: melodic live recording (FUN_081373EE). Note-on inserts the note with length 0
    (insert returns to 0x081377CE) and binds its pitch to the oldest held, unbound pad;
    note-off sets the length through the model update 0x0812E0CE (returns to
    0x081378F8), which attaches that pad's pressure curve (16 samples/step, tag 0xB7)."""
    BLOCK, SLOT, ON_LR, OFF_LR = 0x20200000, 1056, 0x081377CF, 0x081378F9

    def rig(self, kind=0x10):                               # melodic (piano roll)
        g = StrikeButton().rig(kind)
        w32(g.u, 0x3000004C, 0x20280000); w32(g.u, 0x30000048, 0x203C0000)   # room for the pool
        w32(g.u, 0x3003D820 + 4, self.BLOCK); w32(g.u, 0x3003D820 + 8, ~self.BLOCK & 0xFFFFFFFF)
        w32(g.u, 0x3003D820, 0x31504143)
        g.u.mem_write(0x3003DD60, bytes(0x104))               # binding table: empty
        return g

    def slot(self, g, pad, k, seq, samples, t0=1000, t_end=1056, done=1):
        a = self.BLOCK + (pad * 2 + k) * self.SLOT
        g.u.mem_write(a, struct.pack("<8I", seq, t0, t_end, len(samples), 0, done, 0, 0) + bytes(samples))

    def release(self, g, pad, k):
        w32(g.u, self.BLOCK + (pad * 2 + k) * self.SLOT + 20, 1)

    insert = StrikeInsert.insert                            # the real patched insert entry

    def note_on(self, g, pitch, lr=None):
        g.u.reg_write(UC_ARM_REG_LR, self.ON_LR if lr is None else lr)
        return self.insert(g, [1, 2 * 960, 0, 0, pitch | (90 << 16), 0], 10000)

    def note_off(self, g, pitch, length=960, word=0, lr=None):
        at = 0x20110000
        g.u.mem_write(at, struct.pack("<6I", 1, 2 * 960, length, 7, pitch | (90 << 16), word))
        g.u.reg_write(UC_ARM_REG_SP, STACK)
        for i, v in enumerate((0x30024B00, 0x1234, at)):
            g.u.reg_write(UC_ARM_REG_R0 + i, v)
        g.u.reg_write(UC_ARM_REG_LR, self.OFF_LR if lr is None else lr)
        g.u.emu_start(0x0812E0CE | 1, 0x0812E0D2, count=200000)
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK - 8 - 8, "replayed push + sub")
        return list(struct.unpack("<6I", g.u.mem_read(at, 24)))

    def curve(self, g, h):
        pool, table = r32(g.u, 0x3003D840 + 8), r32(g.u, 0x3003D840 + 16)
        off = r32(g.u, table + 4 * h)
        n = struct.unpack("<H", g.u.mem_read(pool + off, 2))[0]
        return list(g.u.mem_read(pool + off + 2, n))

    def test_note_on_binds_note_off_attaches(self):
        g = self.rig()
        self.slot(g, 5, 1, 1, [10, 20, 30, 40, 50, 60, 70, 80], done=0)   # pad 5 held
        self.note_on(g, 60)
        self.release(g, 5, 1)
        w = self.note_off(g, 60)[5]
        self.assertEqual((w >> 24, (w >> 3) & 0x3FFF), (0xB7, 1))
        data = self.curve(g, 1)
        self.assertEqual(len(data), 16, "960 ticks = one step = 16 samples")
        self.assertEqual((data[0], data[-1]), (10, 80))
        self.assertEqual(data, sorted(data))
        log = g.u.mem_read(0x3003D880 + 64, 64)
        self.assertEqual(struct.unpack_from("<IHHH", log, 0), (1, 1, 16, 5), "seq, handle, n_out, pad")

    def test_chord_maps_pad_by_pad(self):
        g = self.rig()
        self.slot(g, 5, 1, 1, [100] * 4, t0=1000, done=0)    # pressed first
        self.slot(g, 9, 1, 1, [20] * 4, t0=1004, done=0)     # pressed 4 ms later
        self.note_on(g, 60); self.note_on(g, 64)               # note-ons in press order
        self.release(g, 5, 1); self.release(g, 9, 1)
        w64 = self.note_off(g, 64)[5]; w60 = self.note_off(g, 60)[5]   # released in any order
        self.assertEqual(self.curve(g, (w64 >> 3) & 0x3FFF)[0], 20, "pitch 64 <- pad 9")
        self.assertEqual(self.curve(g, (w60 >> 3) & 0x3FFF)[0], 100, "pitch 60 <- pad 5")

    def test_unbound_note_off_takes_the_latest_release(self):
        g = self.rig()
        self.slot(g, 1, 1, 1, [5, 5], t_end=2000)
        self.slot(g, 9, 1, 1, [99, 99], t_end=3000)
        w = self.note_off(g, 62)[5]
        self.assertEqual(self.curve(g, (w >> 3) & 0x3FFF)[0], 99)

    def test_other_callers_and_drum_tracks_get_no_curve(self):
        g = self.rig()
        self.slot(g, 5, 1, 1, [10, 20])
        self.assertEqual(self.note_off(g, 60, lr=0x08159001)[5], 0, "not the recording note-off")
        g = self.rig(kind=0x0E)
        self.slot(g, 5, 1, 1, [10, 20])
        self.assertEqual(self.note_off(g, 60)[5], 0, "drum track")

    def test_condition_and_locks_kept_alongside_the_curve(self):
        g = self.rig()
        self.slot(g, 5, 1, 1, [10, 20])
        w = self.note_off(g, 60, word=(0xB6 << 24) | (12 << 17) | 1)[5]
        self.assertEqual((w >> 24, (w >> 17) & 0x7F, w & 7), (0xB7, 12, 1))

def encode_curve(data):
    """The sidecar encoding (diag_shared.h): literals, runs, two packed deltas."""
    out, prev, i = [], None, 0
    while i < len(data):
        v, run = data[i], 0
        while prev is not None and i + run < len(data) and data[i + run] == prev and run < 64:
            run += 1
        if run:
            out.append(0x80 | (run - 1)); i += run; continue
        if prev is not None and i + 1 < len(data):
            d1, d2 = v - prev, data[i + 1] - v
            if -4 <= d1 <= 3 and -4 <= d2 <= 3:
                out.append(0xC0 | ((d1 + 4) << 3) | (d2 + 4)); prev = data[i + 1]; i += 2; continue
        out.append(v); prev = v; i += 1
    return bytes(out)


def sidecar(curves):
    blob = b"BNTC" + struct.pack("<HH", 1, len(curves))
    for c in curves:
        enc = encode_curve(c) if isinstance(c, list) else c[1]
        blob += struct.pack("<HH", len(c) if isinstance(c, list) else c[0], len(enc)) + enc
    return blob


class SidecarLoad(unittest.TestCase):
    """M2-3b: a project's curve notes get their curves back from ...\curves.bnt. The
    document reader (0x08130A18) remembers the path; the first curve note decodes
    the sidecar into the pool; index i -> handle base + i - 1."""
    PATH = 0x20140000
    CURVES = [[10, 11, 12, 13, 13, 13, 13, 40, 41, 42], [5] * 30, list(range(0, 64)) + list(range(64, 0, -1))]

    def rig(self, project=r"\Projects\H\project.xml", files=None):
        g = Rig()
        w32(g.u, 0x3000004C, 0x20280000); w32(g.u, 0x30000048, 0x203C0000)   # room for the pool
        g.u.mem_write(0x3003DE80, bytes(0xB8))                    # load state: empty
        g.files = files if files is not None else {r"\Projects\H\curves.bnt": sidecar(self.CURVES)}
        g.u.mem_write(self.PATH, project.encode() + bytes(1))
        g.u.reg_write(UC_ARM_REG_R0, self.PATH); g.u.reg_write(UC_ARM_REG_SP, STACK)
        g.u.emu_start(0x08130A18 | 1, 0x08130A1C, count=200000)  # the reader's entry: path remembered
        self.assertEqual(g.u.reg_read(UC_ARM_REG_SP), STACK - 36, "replayed push.w {r4-r11, lr}")
        return g

    def load(self, g, word):
        sp = STACK - 0x100
        g.attrs = {"be_step_v1": word}
        g.u.mem_write(sp + 0x24, struct.pack("<6I", *note(0))); w32(g.u, sp + 0x3C, 0xABCD)
        g.u.reg_write(UC_ARM_REG_R4, NODE); g.u.reg_write(UC_ARM_REG_SP, sp)
        g.u.emu_start(0x0812FBB4 | 1, 0x0812FBB8, count=2000000)
        return r32(g.u, sp + 0x24 + 20)

    def curve(self, g, h):
        pool, table = r32(g.u, 0x3003D840 + 8), r32(g.u, 0x3003D840 + 16)
        off = r32(g.u, table + 4 * h)
        n = struct.unpack("<H", g.u.mem_read(pool + off, 2))[0]
        return list(g.u.mem_read(pool + off + 2, n))

    def test_round_trip(self):
        g = self.rig()
        w2 = self.load(g, (0xB7 << 24) | (6 << 17) | (2 << 3) | 1)   # index 2, condition 6, velocity lock
        base = r32(g.u, 0x3003DE80 + 12)
        self.assertEqual((w2 >> 24, (w2 >> 17) & 0x7F, w2 & 7), (0xB7, 6, 1))
        self.assertEqual((w2 >> 3) & 0x3FFF, base + 1, "index 2 -> base + 1")
        for i, c in enumerate(self.CURVES):
            self.assertEqual(self.curve(g, base + i), c, f"curve {i + 1} decoded exactly")
        w1 = self.load(g, (0xB7 << 24) | (1 << 3))
        self.assertEqual((w1 >> 3) & 0x3FFF, base, "index 1 -> base; the file was read once")
        self.assertEqual(g.opened.count(r"\Projects\H\curves.bnt"), 1)
        self.assertEqual(r32(g.u, 0x3003DE80 + 20), 4, "status ok")

    def test_missing_sidecar_keeps_condition_and_locks(self):
        g = self.rig(files={})
        w = self.load(g, (0xB7 << 24) | (6 << 17) | (1 << 3) | 1)
        self.assertEqual((w >> 24, (w >> 17) & 0x7F, w & 7), (0xB6, 6, 1), "no curve; condition + lock kept")
        self.assertEqual(r32(g.u, 0x3003DE80 + 20), 2)

    def test_track_settings_load_with_the_project(self):
        trailer = b"TRKS" + struct.pack("<H", 16) + bytes([0, 1] + [0] * 14) + bytes([2, 1] + [0] * 14)
        g = self.rig(files={r"\Projects\H\curves.bnt": sidecar(self.CURVES) + trailer})
        g.u.mem_write(0x3003DF40, struct.pack("<II", 0x32544541, 1) + bytes([1] * 32))   # previous project's
        self.load(g, 0)                                       # any note: the sidecar loads
        at = bytes(g.u.mem_read(0x3003DF40 + 8, 32))
        self.assertEqual((at[0], at[1], at[16], at[17]), (0, 1, 2, 1), "rec/out from the sidecar")
        self.assertEqual(at[2:16], bytes(14), "others back to defaults")

    def test_loading_never_walks_the_track_model(self):
        """v3.30 published AT destinations at the first note of a load, walking the
        model's track list while stock was rebuilding it: restart loop on autoload."""
        g = self.rig()
        g.model_calls = 0
        self.load(g, (0xB7 << 24) | (1 << 3)); self.load(g, 0)
        self.assertEqual(g.model_calls, 0, "no track-model calls during a load")
        self.assertEqual(r32(g.u, 0x3003DF80), 0, "M7 table cleared: defaults until published")
        self.assertEqual(r32(g.u, 0x3003DF40 + 40), 1, "publish pending")

    def test_project_without_sidecar_resets_settings(self):
        g = self.rig(files={})
        g.u.mem_write(0x3003DF40, struct.pack("<II", 0x32544541, 1) + bytes([1] * 32))
        self.load(g, 0)
        self.assertEqual(bytes(g.u.mem_read(0x3003DF40 + 8, 32)), bytes(32))

    def test_patch_documents_never_open_a_sidecar(self):
        g = self.rig(project=r"\Patches\P\patch.xml")
        self.load(g, (0xB7 << 24) | (1 << 3))
        self.assertEqual(g.opened, [])
        self.assertEqual(r32(g.u, 0x3003DE80 + 20), 1)

    def test_damaged_curve_plays_nothing_others_load(self):
        bad = (8, bytes([0x10, 0xFF, 0xFF, 0xFF]))               # deltas run below 0
        g = self.rig(files={r"\Projects\H\curves.bnt": sidecar([self.CURVES[0], bad, self.CURVES[2]])})
        self.load(g, (0xB7 << 24) | (1 << 3))
        base = r32(g.u, 0x3003DE80 + 12)
        self.assertEqual(self.curve(g, base), self.CURVES[0])
        self.assertEqual(self.curve(g, base + 1), [], "damaged: empty, plays nothing")
        self.assertEqual(self.curve(g, base + 2), self.CURVES[2])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])

