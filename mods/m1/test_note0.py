#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""BE4.22 note0: literal MIDI note 0 at stock's five M7 note senders (docs/plan-ext-low-notes.md, option A1).

    python test_note0.py <stock bento1.bin> <m1 build dir>

REAL (stock code from the patched image, at the base table's addresses): the whole sequence player SEQ_PLAYER with
note_route and the sequencer Note Off, stock's pad Note On / Off (PAD_NOTE_ON / PAD_NOTE_OFF, also with the track's
recording flag set), stock's stop / all notes off (NM_ALL_OFF), the track constructor and its setting messages
(0x10 MIDI channel, 0xA1 pad key), msg_send, bus_post; the M7 -> M4 ring post (H_RING_SITE) and the M4's MIDI-out task
(M4_MIDI_TASK) that turns the events into MIDI bytes. OURS: the five note-0 trampolines and note0_site, the poly cap,
retrigger, Note Mod and curve hooks, as built into the image. STUBBED: the tick math and voice start (test_s3_m7's
rig), the M4 sink calls (test_polyat's M4MidiTask).

Not run: the M7 code that moves events from the MIDI-out queue (router + 0x7C8) to the ring post; the events go to
the ring post as msg_send queued them, timing 0.

The tracks:
  External   MIDI out (channel setting 0x10), every per-pad key 0xFFFFFF00: what the constructor leaves and what
             the M4 never changes for External (it sends 0xA1 only for an instrument's pads, 0x081329E8). test_s3_m7's
             Player(ext=True) gives pad 0 the sampler key 0xF4 (a track with MIDI out AND a sound on pad 0), so the
             External fixture here sets it back to 0xFFFFFF00.
  kit        an internal drum track with MIDI out: 16 pads, each with a key the router delivers to.
  stale      External with a key left on pad 8 (also pad 0, the pad a melodic press arrives on) that the router no
             longer has (a slot that held a kit).
The instrument switch while a note sounds is the router losing the kit's keys (stock's bus remove 0x0809FA40 when the
kit's engines are freed; the pad keys stay) or gaining one. The plan's end-to-end sweep runs stock's M7 pad router
(PADR_LOOP, 1.6.8 table entries; skipped where the base has none) for every pad at Pad Row Offset -5..+5.
Golden values below are typed from stock's drum map and the plan (pad 0..7 -> 44..51, 8..15 -> 36..43, else 52), not
read from our code. With note0 left out of the image (1.6.1) or disabled (BE_DISABLE=note0) the same tests expect
stock's behaviour, so the suite also shows it would catch a regression.
"""
import json, struct, sys, unittest
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
from unicorn.arm_const import *
sys.path.insert(0, str(Path(__file__).resolve().parent))
MAN = json.loads((Path(sys.argv[2]) / "manifest.json").read_text())
import test_s3_m7 as T            # reads (and drops) argv[1:3]: the stock image and the build
from expected_values import BEHAVIOUR

R, S = T.R, T.S
NOTE0 = "note0" not in MAN.get("omitted", []) and "note0" not in MAN.get("disabled", [])
HIGH = BEHAVIOUR[S.module]["PAD_NOTE_MAX"] > 127          # 1.6.8: stock's notes reach 163, its M4 drops > 127
NOTES = (0, 1, 11, 12, 60, 126, 127) + ((128,) if HIGH else ())
NOKEY = 0xFFFFFF00
KIT = {p: 0x200 + p for p in range(16)}
STALE_PAD, STALE_KEY = 8, 0x2FF
VEL = 100
CH = 3                                                       # the MIDI channel setting the fixtures use
RING = 0x24068000                                            # addr-ok: harness (inside the rig's AXI map)
NOTE0_LINE = S.REGION + 0x2400                               # diag_shared.h REGION_NOTE0 (typed here)


def drum_note(pad):
    """Stock's map (sentinel blocks): pad 0..7 -> pad + 0x2C, 8..15 -> pad + 0x1C, else 0x34."""
    return 44 + pad if pad <= 7 else 28 + pad if pad <= 15 else 52


def golden(note, pad, sound):
    """The MIDI note stock + this build send for (note, pad); sound: the pad has an internal sound."""
    if note:
        return note
    if NOTE0 and not sound:
        return 0
    return drum_note(pad)


class P(T.Player):
    """test_s3_m7's Player, keeping every event msg_send put in the MIDI-out queue (all types, raw)."""
    def __init__(self, kind, stale_pad=STALE_PAD, **kw):
        self.raw = []
        if kind == "kit":
            super().__init__(keys=tuple(KIT.values()), ext=True, **kw)
            for p, k in KIT.items():
                self.param(0xA1, k, p)
        else:
            super().__init__(ext=True, **kw)
            self.param(0xA1, NOKEY, 0)                       # undo the rig's sampler key on pad 0
            if kind == "stale":
                self.param(0xA1, STALE_KEY, stale_pad)
        self.kind = kind

    def sound(self, pad):
        return self.kind == "kit" and pad <= 15

    def take(self, base):
        out = super().take(base)
        if base == R.ROUTER + 0x7C8:
            self.raw += out
        return out

    def pad_keys(self):
        return [R.r32(self.u, R.T + 0x86DC + 4 * p) for p in range(16)]

    def all_off(self):
        """Stock's stop / all notes off (track, timing, router), then what it queued."""
        self.call(S.NM_ALL_OFF, R.T, 0, R.ROUTER)
        for m in self.take(R.ROUTER + 0x7C8):
            if m[0] in (0x5B, 0x5C):
                self.midi.append(("ON" if m[0] == 0x5B else "OFF", self.now, struct.unpack("<I", m[16:20])[0]))
        return self

    def line(self):
        return struct.unpack("<6I", self.u.mem_read(NOTE0_LINE, 24))

    def route(self, keys):
        """Set the router's key table as stock's bus add / remove leave it: sorted {key, mailbox index} from +8, count
        at +0x508 (BUS_POST and the bus lookup binary-search it)."""
        self.keys = sorted(keys)
        R.w32(self.u, R.ROUTER + 0x508, len(self.keys))
        for i, k in enumerate(self.keys):
            R.w32(self.u, R.ROUTER + 8 + 8 * i, k); R.w32(self.u, R.ROUTER + 12 + 8 * i, i)
        return self

    def kit_gone(self):
        """The slot's kit is replaced (External): its engines are freed, so its keys leave the router; the track's
        pad keys stay (stale)."""
        return self.route([R.UI])

    def kit_back(self, pad, key=STALE_KEY):
        """An instrument with a sound on pad arrives (0xA1 key, and the router delivers to it)."""
        self.param(0xA1, key, pad)
        return self.route(list(self.keys) + [key])

    def m4(self):
        """The queued MIDI events through the real ring post and the real M4 MIDI-out task: [(port, bytes)]."""
        evs = [m for m in self.raw if m[0] in (0x5B, 0x5C)]
        out = []
        for k in range(0, len(evs), 15):                     # the ring has 16 slots
            out += m4_task(self, [bytes(m[:4]) + bytes(4) + m[8:24] for m in evs[k:k + 15]])
        return out


def m4_task(g, evs):
    """Post evs with stock's ring post (M7, H_RING_SITE, in g's rig) into a fresh ring object, then run stock's M4
    MIDI task on a copy of it."""
    g.u.mem_write(RING, bytes(0x2380))
    for e in evs:
        g.u.mem_write(T.SCRATCH + 0x200, e)
        g.call(S.H_RING_SITE, RING, T.SCRATCH + 0x200)
    ring = bytes(g.u.mem_read(RING, 0x2380))
    assert struct.unpack_from("<I", ring, S.MIDI_RING_WR)[0] == len(evs), "the ring post took every event"
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000); u.mem_write(R.BASE, R.IMG[:0x1C0000]); u.mem_write(R.SENT, b"\xfe\xe7")
    for a, n in ((0x24000000, 0x80000), (0x30000000, 0x48000)):
        u.mem_map(a, n)
    u.mem_write(RING, ring)
    R.w32(u, S.M4MIDI_RING_PTR, RING)
    u.mem_write(S.M4_MIDI_TASK_ARG, b"\x00\x01")             # byte +1: enabled
    sent = []
    def send(uc, a, size, _):
        m = bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 0x14))
        sent.append((uc.reg_read(UC_ARM_REG_R2), m[:m[0x10]]))
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    def ret(uc, a, size, _): uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    u.hook_add(UC_HOOK_CODE, send, begin=S.M4MIDI_SEND, end=S.M4MIDI_SEND)
    for a in (S.M4MIDI_DONE, S.M4MIDI_FLUSH):
        u.hook_add(UC_HOOK_CODE, ret, begin=a, end=a)
    u.reg_write(UC_ARM_REG_R0, S.M4_MIDI_TASK_ARG); u.reg_write(UC_ARM_REG_SP, 0x2407F000)   # addr-ok: harness
    u.reg_write(UC_ARM_REG_LR, R.SENT | 1)
    u.emu_start(S.M4_MIDI_TASK | 1, R.SENT, count=400000)
    assert R.r32(u, RING + S.MIDI_RING_RD) == len(evs), "the M4 task drained the ring"
    return sent


def m4_bytes(events):
    """Golden MIDI bytes for (kind, note) events on channel setting CH (1-based, the status nibble is CH - 1): On =
    9n note vel, Off = 8n note 0x64, nothing
    for a note above 127 (the M4 drops it, On and Off alike)."""
    out = []
    for kind, note in events:
        if note > 127:
            continue
        out.append(bytes([0x90 | CH - 1, note, VEL]) if kind == "ON" else bytes([0x80 | CH - 1, note, 0x64]))
    return out


def paired(test, midi):
    """Every Off follows an On of its note, and nothing is left sounding."""
    open_ = {}
    for kind, _, n in midi:
        open_[n] = open_.get(n, 0) + (1 if kind == "ON" else -1)
        test.assertGreaterEqual(open_[n], 0, f"an Off {n} without its On: {midi}")
    test.assertEqual({n: c for n, c in open_.items() if c}, {}, f"left sounding: {midi}")


class Fixtures(unittest.TestCase):
    """What the tracks are (real constructor and setting messages)."""

    def test_external_track_has_no_pad_keys(self):
        """The constructor leaves all 16 per-pad keys at 0xFFFFFF00, and the MIDI channel setting does not change
        them: an External track's keys (the M4 sends no 0xA1 for External)."""
        g = R.Rig(keys=())
        self.assertEqual(set(R.r32(g.u, R.T + 0x86DC + 4 * p) for p in range(16)), {NOKEY})
        g.param(0x10, CH)
        self.assertEqual(set(R.r32(g.u, R.T + 0x86DC + 4 * p) for p in range(16)), {NOKEY})
        self.assertEqual(g.u.mem_read(R.T + 0x86D1, 1), b"\x01", "MIDI out on")

    def test_fixtures(self):
        self.assertEqual(set(P("ext").pad_keys()), {NOKEY}, "the rig's pad-0 sampler key is cleared")
        self.assertEqual(P("kit").pad_keys(), list(KIT.values()))
        st = P("stale").pad_keys()
        self.assertEqual((st[STALE_PAD], set(st[:STALE_PAD] + st[STALE_PAD + 1:])), (STALE_KEY, {NOKEY}))


class PadPress(unittest.TestCase):
    """Stock's pad Note On / Off (the live pad path) for every note, External, kit and a stale key."""

    def check(self, kind, note, pad, rec=False):
        g = P(kind)
        if rec:                                              # as the plan's run (rc21 exp22 D)
            if R.r32(g.u, R.ROUTER) == 0:
                R.w32(g.u, R.ROUTER, T.SCRATCH + 0x800); g.u.mem_write(T.SCRATCH + 0x800, bytes(0x80))
            g.u.mem_write(R.T + 0x30, b"\x01"); R.w32(g.u, R.ROUTER + 0x1A48, 0)
        g.pad(True, note, pad=pad, vel=VEL).pad(False, note, pad=pad)
        want = golden(note, pad, g.sound(pad))
        self.assertEqual(g.midi, [("ON", 0, want), ("OFF", 0, want)], f"{kind} note {note} pad {pad}")
        self.assertEqual([m for _, m in g.m4()], m4_bytes([("ON", want), ("OFF", want)]))
        return g

    def test_external_every_note(self):
        for note in NOTES:
            for pad in (0, 8, 9, 16):
                with self.subTest(note=note, pad=pad):
                    self.check("ext", note, pad)

    def test_kit_keeps_stock_drum_notes(self):
        for note in NOTES:
            for pad in (0, 7, 8, 15):
                with self.subTest(note=note, pad=pad):
                    self.check("kit", note, pad)

    def test_kit_pad_16_has_no_sound(self):
        """A pad index above 15 has no per-pad key: note 0 there is 0 (stock: 52). Not reachable from a 16-pad kit."""
        self.check("kit", 0, 16)

    def test_stale_key_counts_as_no_sound(self):
        g = self.check("stale", 0, STALE_PAD)
        if NOTE0:
            self.assertEqual(g.line()[:3], (0, 2, 0), "black box: two stale (On, Off), no zero, no drum")

    def test_stale_key_on_pad_0(self):
        """Review rc23 B2: the realistic kit -> External slot: the kit's key left on pad 0, where a melodic External
        press arrives (pad field 0). Pad press and step both send 0."""
        g = P("stale", stale_pad=0)
        g.pad(True, 0, pad=0, vel=VEL).pad(False, 0, pad=0)
        g.notes([dict(t=10, len=100, note=0, pad=0)]); g.play(4)
        want = golden(0, 0, False)
        self.assertEqual([(k, n) for k, _, n in g.midi], [("ON", want), ("OFF", want)] * 2)
        if NOTE0:
            self.assertEqual(g.line()[:3], (0, 4, 0), "four stale decisions")

    def test_black_box_counts(self):
        if not NOTE0:
            self.skipTest("note0 is not in this image")
        g = self.check("ext", 0, 0)
        z, st, dr, last, key, _ = g.line()
        self.assertEqual((z, st, dr), (2, 0, 0))
        self.assertEqual((last & 15, last >> 4 & 15, last >> 16, key), (4, 0, 0, NOKEY), "last: pad Off, no key, pad 0")
        g = self.check("kit", 0, 9)
        z, st, dr, last, key, _ = g.line()
        self.assertEqual((z, st, dr), (0, 0, 2))
        self.assertEqual((last & 15, last >> 4 & 15, last >> 16, key), (4, 2, 9, KIT[9]))
        g = self.check("ext", 60, 0)
        self.assertEqual(g.line()[:3], (0, 0, 0), "a non-zero note never reaches the block")

    def test_recording_keeps_the_raw_note(self):
        """With the track's +0x30 flag the pad path also builds the record message (REC_MSG_5 / _6) from the RAW note:
        0 stays 0 with its pad, and MIDI goes out by the same rule."""
        for kind, pad in (("ext", 0), ("kit", 8)):
            for note in NOTES:
                with self.subTest(kind=kind, note=note):
                    g = self.check(kind, note, pad, rec=True)
                    n = R.r32(g.u, R.ROUTER + 0x1A48)
                    recs = [struct.unpack_from("<6I", g.u.mem_read(R.ROUTER + 0xE48 + 48 * i, 24)) for i in range(n)]
                    self.assertEqual([(w[0] & 0xFF, w[0] >> 16, w[4] & 0xFFFF) for w in recs], [(5, pad, note), (6, pad, note)])


class Sequenced(unittest.TestCase):
    """One step through the whole stock player, then stop / all-off, then a ratchet."""

    def test_step_every_note(self):
        for kind, pad in (("ext", 0), ("ext", 9), ("kit", 0), ("kit", 9)):
            for note in NOTES:
                with self.subTest(kind=kind, note=note, pad=pad):
                    g = P(kind); g.notes([dict(t=10, len=100, note=note, pad=pad)]); g.play(4)
                    want = golden(note, pad, g.sound(pad))
                    self.assertEqual(g.midi, [("ON", 10, want), ("OFF", T.off_lands(110), want)])
                    self.assertEqual([m for _, m in g.m4()], m4_bytes([("ON", want), ("OFF", want)]))

    def test_stop_ends_a_sounding_note(self):
        """A long step, then stock's stop / all notes off: its Off is the note the On sent."""
        for kind, pad in (("ext", 0), ("kit", 8)):
            for note in NOTES:
                with self.subTest(kind=kind, note=note):
                    g = P(kind); g.notes([dict(t=10, len=5000, note=note, pad=pad)]); g.play(2)
                    want = golden(note, pad, g.sound(pad))
                    self.assertEqual(g.midi, [("ON", 10, want)], "sounding")
                    g.all_off()
                    self.assertEqual(g.midi[1:], [("OFF", g.now, want)], "stop sends its Off")
                    paired(self, g.midi)
                    self.assertEqual([m for _, m in g.m4()], m4_bytes([("ON", want), ("OFF", want)]))

    def test_stop_ends_a_held_pad_note(self):
        """The live path (no pending step): a held pad, then stop. Stock's stop ends sequenced notes only, so the pad's
        own release still ends it; nothing is sent twice for note 0."""
        g = P("ext"); g.pad(True, 0, pad=0); g.all_off(); g.pad(False, 0, pad=0)
        want = golden(0, 0, False)
        self.assertEqual([(k, n) for k, _, n in g.midi], [("ON", want), ("OFF", want)])

    def test_retrigger_hits_send_the_step_note(self):
        """A 2-hit ratchet (1/16): every MIDI hit is the step's own MIDI note, each On with its Off (BE4.21 sent 44, then
        0 for the hits). Notes above 127 get no MIDI hits (by design) and stock's own pair is dropped by the M4."""
        for kind, pad in (("ext", 0), ("kit", 8)):
            for note in NOTES:
                with self.subTest(kind=kind, note=note):
                    g = P(kind); g.notes([dict(t=10, len=100, note=note, pad=pad, word=T.RETRIG_2)]); g.play(12)
                    want = golden(note, pad, g.sound(pad))
                    # without note0 (BE4.21 / v3.72) the hits of a note-0 step go out as the raw 0 after stock's 44
                    self.assertEqual({n for _, _, n in g.midi}, {want} if NOTE0 or note else {want, 0})
                    ons = [t for k, t, n in g.midi if k == "ON"]
                    self.assertEqual(ons, [10] if note > 127 else [10, 250, 490])
                    paired(self, g.midi)
                    self.assertEqual([m for _, m in g.m4()], m4_bytes([(k, n) for k, _, n in g.midi]))


class Hooks(unittest.TestCase):
    """Our hooks with note 0: poly cap, Note Mod, curves; velocities."""

    def test_poly_cap_counts_0_and_44_as_two_voices(self):
        g = P("ext"); g.notes([dict(t=10, len=300, note=0), dict(t=74, len=40, note=44)]); g.play(8)
        if NOTE0:
            self.assertEqual(g.midi, [("ON", 10, 0), ("ON", 74, 44), ("OFF", T.off_lands(114), 44), ("OFF", T.off_lands(310), 0)])
        else:                                                # stock: one voice, retriggered
            self.assertEqual(g.midi[:3], [("ON", 10, 44), ("OFF", 74, 44), ("ON", 74, 44)])
        paired(self, g.midi)

    def test_poly_cap_steals_a_literal_0(self):
        """Seven held pads, 0 first: the seventh steals 0, and 0's own release is swallowed."""
        g = P("ext")
        for n in (0, 1, 2, 3, 4, 5, 6):
            g.pad(True, n, pad=0)
        z = golden(0, 0, False)
        self.assertEqual(g.midi, [("ON", 0, n) for n in (z, 1, 2, 3, 4, 5)] + [("OFF", 0, z), ("ON", 0, 6)])
        g.midi = []
        for n in (0, 1, 2, 3, 4, 5, 6):
            g.pad(False, n, pad=0)
        self.assertEqual(g.midi, [("OFF", 0, n) for n in (1, 2, 3, 4, 5, 6)])

    def test_note_mod_moves_an_external_note_0_by_pitch(self):
        """External: a note 0 is MIDI note 0, so Note Mod (+2 scale steps of C major) moves it like any note: 0 -> 4 (E).
        BE4.21 took it for a pad hit and moved the pad (0 -> pad 1 -> 45). The octave wrap at the top is kept (plan,
        measured on BE4.10: 126 + 2 -> 117)."""
        for note, want in ((0, 4 if NOTE0 else 45), (60, 64), (126, 117)):
            with self.subTest(note=note):
                g = P("ext"); g.notes([dict(t=10, len=100, note=note)], amount=T.P2); g.play(4)
                self.assertEqual(g.midi, [("ON", 10, want), ("OFF", T.off_lands(110), want)])

    def test_note_mod_moves_a_kit_hit_by_pad(self):
        """Kit: a note 0 is a pad hit; it moves by one pad (P2 = 4 pads x 286 / 1000), and stock's drum note follows."""
        g = P("kit"); g.notes([dict(t=10, len=100, note=0, pad=3)], amount=T.P2); g.play(4)
        self.assertEqual(g.midi, [("ON", 10, 48), ("OFF", T.off_lands(110), 48)], "pad 3 -> pad 4 -> 44 + 4")

    def test_note_mod_kit_hit_stays_off_a_silent_pad(self):
        """Kit with pad 4 emptied: the hit stays on pad 3 rather than becoming MIDI note 0 on a pad without a sound."""
        g = P("kit"); g.param(0xA1, NOKEY, 4)
        g.notes([dict(t=10, len=100, note=0, pad=3)], amount=T.P2); g.play(4)
        want = 47 if NOTE0 else 48
        self.assertEqual(g.midi, [("ON", 10, want), ("OFF", T.off_lands(110), want)])

    def test_note_mod_kit_hit_stays_off_a_stale_pad(self):
        """Kit with a key on pad 4 that the router no longer has (review rc23 behaviour note 2, mutant A): no sound
        there, so the hit stays on pad 3. A key-only test would move it onto pad 4, which then goes out as note 0."""
        g = P("kit"); g.param(0xA1, STALE_KEY, 4)
        g.notes([dict(t=10, len=100, note=0, pad=3)], amount=T.P2); g.play(4)
        want = 47 if NOTE0 else 48
        self.assertEqual(g.midi, [("ON", 10, want), ("OFF", T.off_lands(110), want)])

    def test_curve_presses_the_sounding_note(self):
        """A note-0 step with a recorded curve: the External player's poly AT (0x5F) goes to the note that sounds."""
        for kind, pad in (("ext", 0), ("kit", 8)):
            with self.subTest(kind=kind):
                g = P(kind).curves(); g.notes([dict(t=10, len=200, note=0, pad=pad, word=T.CURVE_WORD)]); g.play(6)
                want = golden(0, pad, g.sound(pad))
                at = {struct.unpack("<I", m[16:20])[0] for m in g.raw if m[0] == 0x5F}
                self.assertEqual(at, {want} if NOTE0 else {0}, "AT on the sounding note (BE4.21: on 0 while 44 sounds)")
                self.assertIn(("ON", 10, want), g.midi)

    def test_note_on_velocities(self):
        """No path sends a Note On with velocity 0 or 128 for a source velocity of 100."""
        g = P("kit"); g.notes([dict(t=10, len=100, note=0, pad=8, word=T.RETRIG_2)]); g.play(12)
        g.pad(True, 0, pad=0).pad(False, 0, pad=0)
        vels = [struct.unpack("<I", m[20:24])[0] for m in g.raw if m[0] == 0x5B]
        self.assertTrue(vels and all(1 <= v <= 127 for v in vels), vels)


class Router(unittest.TestCase):
    """The router test sees every entry of the key table (review rc23 S4: no kit key was ever first or last)."""

    def test_kit_keys_at_both_ends_of_the_table(self):
        g = P("kit").route(KIT.values())                        # 0x200 first, 0x20F last
        for pad in (0, 15):
            g.midi = []
            g.pad(True, 0, pad=pad, vel=VEL).pad(False, 0, pad=pad)
            self.assertEqual([n for _, _, n in g.midi], [drum_note(pad)] * 2, f"pad {pad}")

    def test_a_one_key_table(self):
        g = P("kit").route([KIT[15]])
        for pad, sound in ((15, True), (0, False)):
            g.midi = []
            g.pad(True, 0, pad=pad, vel=VEL).pad(False, 0, pad=pad)
            self.assertEqual([n for _, _, n in g.midi], [golden(0, pad, sound)] * 2, f"pad {pad}")

    def test_an_empty_table(self):
        g = P("kit").route([])
        g.pad(True, 0, pad=8, vel=VEL).pad(False, 0, pad=8)
        self.assertEqual([n for _, _, n in g.midi], [golden(0, 8, False)] * 2)


class Switch(unittest.TestCase):
    """Review rc23 S2 / I6: the instrument changes while a note-0 note sounds. Its Off (sequencer Off, stop, pad release,
    ratchet hits) is the note its On sent, so nothing hangs on the receiver. Stock (no note0) never changes its note."""

    def held(self, g, n):
        if NOTE0:
            self.assertEqual(g.line()[5], n, "black box held=")

    def test_step_off_after_the_kit_is_replaced(self):
        g = P("kit"); g.notes([dict(t=10, len=300, note=0, pad=8)]); g.play(2)
        self.assertEqual(g.midi, [("ON", 10, 36)])
        g.kit_gone().play(12)
        self.assertEqual(g.midi, [("ON", 10, 36), ("OFF", T.off_lands(310), 36)])
        self.held(g, 1)

    def test_stop_after_the_kit_is_replaced(self):
        g = P("kit"); g.notes([dict(t=10, len=5000, note=0, pad=8)]); g.play(2)
        g.kit_gone().all_off()
        self.assertEqual([(k, n) for k, _, n in g.midi], [("ON", 36), ("OFF", 36)])
        self.assertEqual([m for _, m in g.m4()], m4_bytes([("ON", 36), ("OFF", 36)]))
        self.held(g, 1)

    def test_pad_release_after_the_kit_is_replaced(self):
        g = P("kit"); g.pad(True, 0, pad=0, vel=VEL); g.kit_gone(); g.pad(False, 0, pad=0)
        self.assertEqual([(k, n) for k, _, n in g.midi], [("ON", 44), ("OFF", 44)])
        self.held(g, 1)

    def test_external_becomes_a_kit_while_note_0_sounds(self):
        g = P("ext"); g.notes([dict(t=10, len=5000, note=0, pad=8)]); g.play(2)
        want = golden(0, 8, False)
        g.kit_back(8).all_off()
        self.assertEqual([(k, n) for k, _, n in g.midi], [("ON", want), ("OFF", want)])
        self.held(g, 1)
        g.midi = []                                              # the next On follows the new instrument
        g.pad(True, 0, pad=8, vel=VEL).pad(False, 0, pad=8)
        self.assertEqual([n for _, _, n in g.midi], [36, 36])

    def test_ratchet_hits_after_the_kit_is_replaced(self):
        g = P("kit"); g.notes([dict(t=10, len=100, note=0, pad=8, word=T.RETRIG_2)]); g.play(2)
        self.assertEqual(g.midi[:1], [("ON", 10, 36)])
        g.kit_gone().play(10)
        # without note0 (BE4.21 / v3.72) the hits of a note-0 step go out as the raw 0 after stock's 36
        self.assertEqual({n for _, _, n in g.midi}, {36} if NOTE0 else {36, 0}, "every hit is the step's note")
        self.assertEqual([t for k, t, n in g.midi if k == "ON"], [10, 250, 490])
        paired(self, g.midi)

    def test_nothing_held_when_nothing_changes(self):
        g = P("kit"); g.notes([dict(t=10, len=100, note=0, pad=8, word=T.RETRIG_2)]); g.play(12)
        g.pad(True, 0, pad=3, vel=VEL).pad(False, 0, pad=3)
        self.held(g, 0)


# The plan's N1 exit test (docs/plan-ext-low-notes.md, "N1 tests"): stock's M7 pad router turns pad presses into
# Note Ons; External, Chromatic, root C, Pad Row Offset -5..+5, all 16 pads (176 presses).
SWEEP = all(S.has(n) for n in ("PADR_CTOR", "PADR_LOOP", "PADR_RELEASE", "SCALE_INIT", "BUS_FIND", "BUS_POST_KEY"))


def pad_router_presses():
    """Run stock's pad router (constructor, message loop, scale table init: real code from this image) with the M4's
    param messages (0x3E: 0xC0 pad mode 3, 0x175 linear 0, 0x8D scale 0 = Chromatic, 0x8E root 3 = C, 0x176 Pad Row
    Offset) and a press / release (0xAC / 0xAD) per pad. Returns [(offset, pad, note, pad field)] of each posted Note
    On. Stubbed: the queue read (feeds the messages), the bus lookup (a mailbox), the posts (recorded), and the held-pad
    release helper (PADR_RELEASE) on mode / offset changes."""
    stop, router, bus = 0x20000000, 0x24100000, 0x24110000          # addr-ok: harness
    u = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    u.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_M7)
    u.mem_map(0x08000000, 0x200000); u.mem_write(R.BASE, R.IMG[:0x1C0000])
    for a, n in ((0x20000000, 0x100000), (0x24000000, 0x200000), (0x30000000, 0x80000), (0x38000000, 0x10000)):
        u.mem_map(a, n)
    u.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc, acc, addr, size, val, _: (uc.mem_map(addr & ~0xFFF, 0x1000), True)[1])
    def call(fn, *args):
        for i, v in enumerate(args): u.reg_write(UC_ARM_REG_R0 + i, v)
        u.reg_write(UC_ARM_REG_SP, 0x2007F000); u.reg_write(UC_ARM_REG_LR, stop | 1)   # addr-ok: harness
        u.emu_start(fn | 1, stop, count=2000000)
    queue, posts = [], []
    def ret(uc): uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    def get(uc, a, size, _):                                     # (queue, idx, &msg) -> 1 while messages remain
        i, buf = uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2)
        if i < len(queue): uc.mem_write(buf, queue[i])
        uc.reg_write(UC_ARM_REG_R0, int(i < len(queue))); ret(uc)
    def find(uc, a, size, _): uc.reg_write(UC_ARM_REG_R0, bus + 0x8000); ret(uc)
    def post(uc, a, size, _): posts.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R1), 24))); ret(uc)
    u.hook_add(UC_HOOK_CODE, get, begin=S.H_CONSUME_SITE, end=S.H_CONSUME_SITE)   # the queue read's entry
    u.hook_add(UC_HOOK_CODE, find, begin=S.BUS_FIND, end=S.BUS_FIND)
    for a in (S.BUS_POST, S.BUS_POST_KEY, S.MSG_SEND):
        u.hook_add(UC_HOOK_CODE, post, begin=a, end=a)
    u.hook_add(UC_HOOK_CODE, lambda uc, a, size, _: ret(uc), begin=S.PADR_RELEASE, end=S.PADR_RELEASE)
    def run(msgs):
        queue[:] = msgs; posts.clear(); call(S.PADR_LOOP, router, bus); return list(posts)
    def msg(t, *w): return struct.pack("<B3xIIIII", t, *[x & 0xFFFFFFFF for x in (w + (0,) * 5)[:5]])
    def param(key, a): return msg(0x3E, 0, 0, key, a, 0)
    call(S.SCALE_INIT); call(S.PADR_CTOR, router)
    R.w32(u, router + S.ROUTER_SEL_TRACK, 0)
    run([param(0xC0, 3)])
    out = []
    for off in range(-5, 6):
        run([param(0x175, 0), param(0x8D, 0), param(0x8E, 3), param(0x176, off)])
        for pad in range(16):
            on = [struct.unpack("<6I", p) for p in run([msg(0xAC, 0, 0, pad, 0xD000, 0)]) if p[0] == 2]
            run([msg(0xAD, 0, 0, pad, 0, 0)])
            if on:
                out.append((off, pad, on[0][3], on[0][5] & 0xFFFF))
    return out


@unittest.skipUnless(SWEEP, "the base table has no pad router entries (1.6.1)")
class Sweep(unittest.TestCase):
    def test_every_midi_note_from_the_pads(self):
        """Every press's Note On through stock's External pad Note On / Off, the ring post and the M4 MIDI task: the
        union is exactly 0..127 (stock: 127 notes, 0 goes out as 44), each On followed by its Off."""
        presses = pad_router_presses()
        self.assertEqual(len(presses), 176, "every pad at every offset posts a Note On")
        self.assertEqual({n for _, _, n, _ in presses} & set(range(128)), set(range(128)), "the router reaches 0..127")
        g = P("ext")
        for _, _, note, pf in presses:
            g.pad(True, note, pad=pf, vel=VEL).pad(False, note, pad=pf)
        out = [m for _, m in g.m4()]
        ons = [b[1] for b in out if b[0] >> 4 == 9]
        self.assertEqual(set(ons), set(range(128)) if NOTE0 else set(range(1, 128)))
        self.assertEqual(len(out), 2 * len(ons))
        self.assertEqual(out, [b for n in ons for b in (bytes([0x90 | CH - 1, n, VEL]), bytes([0x80 | CH - 1, n, 0x64]))])


class Sites(unittest.TestCase):
    """The image: five patched blocks (note0 built and enabled) or five stock ones."""

    def test_five_sites(self):
        names = ("SEQ_ON", "SEQ_OFF", "PAD_ON", "PAD_OFF", "ALL_OFF")
        img = R.IMG
        for n in names:
            a = getattr(S, f"NOTE0_{n}_MAP") if S.has(f"NOTE0_{n}_MAP") else None
            if a is None:
                self.assertFalse(NOTE0); continue
            b = img[a - R.BASE:a - R.BASE + 4]
            with self.subTest(site=n):
                if NOTE0:
                    self.assertNotEqual(b, S.orig(f"NOTE0_{n}_MAP"), "patched")
                    hw1, hw2 = struct.unpack("<HH", b)
                    self.assertEqual((hw1 & 0xF800, hw2 & 0xD000), (0xF000, 0x9000), "a b.w")
                else:
                    self.assertEqual(b, S.orig(f"NOTE0_{n}_MAP"), "stock's block")


if __name__ == "__main__":
    unittest.main(verbosity=2)
