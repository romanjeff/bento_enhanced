# SPDX-License-Identifier: GPL-3.0-or-later
"""Build M1 (poly aftertouch + per-track poly cap for External tracks, with
black-box diagnostics) on a stock Bento main image.

    python build.py --base <stock bento1.bin> --out <new dir>

The base image picks its table by SHA-256 (bases/stock_*.py: every stock address,
RAM address, struct offset and key we use, by name); any other image is refused,
and so is a table with unreviewed (REVIEW) entries. Checks original bytes at
every patch site, the table's fingerprints of every stock address it names, its
offsets / keys / event ids against the independent record (expected_values.py),
writes bento1.bin + manifest.json, and proves that undoing every write
reproduces the base exactly. Run with Lab's venv (ziglang +
keystone). Earlier versions (<= v2.2) were built on Lab's CH10; CH10 itself
crashed on the device without any of this code, so M1 moved to stock.
"""
import argparse, hashlib, json, os, re, struct, subprocess, sys
from pathlib import Path
from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bases
from bases import literal_check
from expected_values import EXPECTED

BASE = 0x08040000
S = None   # the base's table (bases.Base), set by use_base(); stock addresses are S.<NAME>
# Largest main image the bootloader has accepted on the device (Lab CH10). A
# 0x1A0800-byte image reaching 0x081E0000 was refused; the M7 MPU maps
# 0x081E0000.. as a read-only data region (persistent storage).
PROVEN_MAX_SIZE = 0x18D334
FORBIDDEN_FROM = 0x081E0000

# M7 heap: stock ends it at S.M7_HEAP_END (three literals, identified by Lab).
# v2.3-v3.15 lowered it (1.6.1: 0x24048DA0 -> 0x24048400) for our RAM slice; that broke loading
# large projects (Jeff: SPACED). Since v3.16 the heap is stock and the slice
# lives in unlinked D2 SRAM2 (S.D2_SLICE; both cores' MPUs map it).

# Our M7 code sits in stock's zero gap after the M7 image's data: S.CTRL_INPUT, S.CTRL_STARTUP,
# then S.M7_TRAMP (trampolines, 1 KB since v3.59), S.M7_CODE .. S.M7_LIMIT (19 KB since v3.59).
# M4 code is appended after stock's image end, from S.APPEND_FROM (see layout() for the blocks).
# Per-base settings (S2): each stock firmware has its own version line, so a build's version
# comes from its base, never from an edit before the build. Bump the base's "version" for every
# build that goes on the device. m4_flags: extra compiler flags for the M4 code on that base.
# 1.6.8 keeps our slice in AXI SRAM, which the M4's MPU maps as Device memory, where an unaligned
# access faults: all M4 code is built strict-align there (zig cc 0.15.1 silently ignores
# -mno-unaligned-access; -mstrict-align takes effect). Not on 1.6.1: it changes m4_step's bytes.
# groups: the feature groups whose hooks a sprint has verified on that base (None = all). Entries
# the matcher mapped with confidence carry no REVIEW, but their hooks' assumptions are only checked
# when a sprint ports the feature: until then the group is refused when enabled and left out of
# the image when disabled (S2: only the black box, S3-S5 add the rest).
# m7_flags (S3): extra compiler flags for the M7 code on that base. BE_NOTES_ABOVE_127: 1.6.8's pad
# notes reach 0..163 (stock's clamp 0x080A25E0, 1.6.1: usat #7) and its M4 drops MIDI notes above 127;
# the poly cap, retrigger MIDI hits, curves and Note Mod pass such notes on untouched instead of
# treating them as note & 0x7F (a per-base flag, so the 1.6.1 build stays byte-identical).
# S3 ported the M7 parts: poly_cap, retrig (with External curve playback), cond_engine, nmod, inta_curve.
BASE_SETTINGS = {
    "stock_161": dict(version="3.72", m4_flags=(), m7_flags=(), groups=None),
    "stock_168": dict(version="4.01", m4_flags=("-mstrict-align",), m7_flags=("-DBE_NOTES_ABOVE_127",),
                      groups=("trace", "stall", "irq30", "bbox", "poly_cap", "retrig", "cond_engine", "nmod", "inta_curve")),
}
VERSION = DISPLAY = None              # set by use_base() from BASE_SETTINGS (display: boot label, max 6 characters)
# Bisect builds (diagnosis only): BE_DISABLE="step,hold" leaves those groups' stock
# call sites unpatched (their code stays in the image, unreachable); BE_LABEL sets
# the boot label (<= 6 chars) and BE_TAG suffixes the kit version.
# On a base whose table still has unreviewed entries (S2: 1.6.8), a disabled group that uses one
# is left out of the image altogether (its code is not even compiled: a draft value never reaches
# it), and an enabled group that uses one refuses the build (plan()).
# Off by default since v3.20 (Jeff's bisect, 2026-10-26): with the M7 stall hooks
# (main-loop progress/wait + SysTick profile) patched in, PROJ with a large project
# (SPACED) crash-restarted and the SD card dropped out ("insert microSD card");
# BS-C (stall off) was fine, BS-D (stall on) failed. irq30 (v3.18) is unproven.
# Code stays in the image, unreachable. BE_ENABLE="stall,irq30" turns them back on.
DEFAULT_OFF = {"stall", "irq30", "modtest"}   # modtest: F1 fixed routing (test builds only)
DISABLED = (DEFAULT_OFF - {g for g in os.environ.get("BE_ENABLE", "").split(",") if g})     | {g for g in os.environ.get("BE_DISABLE", "").split(",") if g}
# S3 split poly, cond and inta into the part S3 ports (M7 only) and the part a later sprint does, so the
# S3 part builds on 1.6.8 alone: poly_cap (S3) + poly_at (S5: External handler h_poly and the M4 MIDI
# 0x5F/0x60 handlers), cond_engine (S3: M7 gate and loop) + cond_ui (S4: the M4 probability clamps),
# inta_curve (S3: curve playback on internal instruments, -DBE_NO_INTA_CURVE when off) + inta_live (S5:
# LIVE_SITE, live pad AT). The old names stay as aliases (BE_DISABLE=poly turns off both halves and still
# passes -DBE_NO_POLY), so scripts and earlier bisects build what they built before.
ALIASES = {"poly": ("poly_cap", "poly_at"), "cond": ("cond_engine", "cond_ui"), "inta": ("inta_curve", "inta_live")}
for _a, _parts in ALIASES.items():
    if _a in DISABLED:
        DISABLED |= set(_parts)
PATCH_GROUPS = [("step", ("Per-step", "Step edit", "Strike", "Step inspection", "Curves")),
                ("hold", ("Hold shortcuts",)),
                ("cond_engine", ("Conditions",)),                   # M7 probability gate + loop counter
                ("cond_ui", ("Probability:",)),                     # M4 probability editor clamps
                ("retrig", ("Retrigger",)),
                ("poly_at", ("M1 poly AT: External", "M1 poly AT: MIDI task")),   # h_poly + M4 MIDI handlers
                ("poly_cap", ("M1b poly cap",)),                    # the five msg_send sites -> h_note
                ("trace", ("M1 diag: h_",)),                        # M7 event tracing hooks
                ("stall", ("Stall",)),                              # M7 stall record + SysTick profile
                ("irq30", ("M4 watchdog profile: IRQ30",)),         # M4 heartbeat-supervisor profile
                ("bbox", ("M1 diag: defaultTask", "Diag dump")),                # M4 black box (also hands poly AT its region)
                ("inta_curve", ()),                                 # M2-2b curves on internal instruments (-DBE_NO_INTA_CURVE)
                ("inta_live", ("Internal AT",)),                    # M2-2b live pad AT on internal instruments
                ("nmod", ("Note Mod",))]                            # F5 Note Mod (+ -DBE_NO_NMOD)
if "diag7" in DISABLED:
    DISABLED |= {"trace", "stall"}
if "diag" in DISABLED:
    DISABLED |= {"trace", "stall", "irq30", "bbox"}
def patch_group(why):
    return next((g for g, prefixes in PATCH_GROUPS if why.startswith(prefixes)), None)
# Stock's version string (S.VERSION_STR, 8-byte slot). Its two users: the boot-screen label
# (1.6.1 FUN_08162950 copies only the first 6 bytes) and "%s-cont-%d" (1.6.1 FUN_08131024).

# (name, site, displaced instruction(s) (the 4 bytes at the site), call setup); resume = site + 4
M7_HOOKS = [
    ("h_input",   "H_INPUT_SITE",   "push.w {r4,r5,r6,r7,r8,r9,lr}", ""),
    ("h_send",    "H_SEND_SITE",    "mov ip, r1; push {r4,r5,lr}",   "mov r2, lr; "),
    ("h_ring",    "H_RING_SITE",    "add.w ip, r0, #0x2000",         ""),
    ("h_consume", "H_CONSUME_SITE", "ldr.w r3, [r0, #0x600]",        "mov r2, lr; "),
]

# Poly AT, M7: External track handler (S.POLY_SITE), branch taken for event
# types that are neither 2 (note on) nor 3 (note off). r6 = track, r8 = the
# context stock passes to its note-on producer, event at sp+0x20.
# Poly AT, M4: MIDI task jump table (S.MIDI_JT, types 0x5B..0x63); slot 0x5F is unused
# (falls through to the loop S.M4_LOOP). Our handler copies stock's Note On
# case (S.M4_NOTE_ON_CASE..S.M4_LOOP) with status 0x90 -> 0xA0.

# Poly cap: the five `bl msg_send` sites that post External MIDI notes, and the
# register holding the track object at each (verified unmodified on every path).
NOTE_SITES = [  # (site, track register, role)
    ("POLYCAP_SEQ_ON_SITE", "r4", "sequencer Note On (note_route)"),
    ("POLYCAP_PAD_ON_SITE", "r4", "pad Note On (1.6.1 FUN_0805FF00)"),
    ("POLYCAP_SEQ_OFF_SITE", "r4", "sequencer Note Off (1.6.1 FUN_0805FE00)"),
    ("POLYCAP_PAD_OFF_SITE", "r4", "pad Note Off (1.6.1 FUN_08060040)"),
    ("POLYCAP_ALL_OFF_SITE", "r6", "Note Off, stop/all-off (1.6.1 FUN_080618E0)"),
]

# M2-2b: the pad router's pressure post (melodic and drum paths join here, S.LIVE_SITE):
# bl bus_post(bus r0, event r1) with the router object in r5.
# Retrigger (m7_retrig.c): S.NOTE_ROUTE, S.RETRIG_ACCEPT_SITE, S.RETRIG_EMIT_SITE.
# F5 Note Mod: stock's sequencer Note Off (track, note, timing, pad, router) and its two
# callers; stock's per-track stop / all-notes-off (track, timing, router).
NM_OFF_SITES = ("NM_OFF_SITE_1", "NM_OFF_SITE_2")

# Audio stall snapshot (idle-restart investigation): the M7 main audio loop
# refreshes the watchdog only on audio progress.
STALL_SITES = [  # (bl site, original target, our function, purpose)
    ("STALL_PROGRESS_SITE", "WD_REFRESH", "stall_progress", "Stall snapshot: main audio loop progress (watchdog refresh)"),
    ("STALL_CHECK_SITE", "MSG_PUMP", "stall_check", "Stall snapshot: main audio loop waiting (message pump)"),
]

# Trigger conditions (port of bento-community-lab T2, GPL-3.0-or-later).
# M7: probability gate and loop counter. Trampolines are Lab's, unchanged
# except for our function names.
# M4: extend the native probability list (100 labels "1%%".."99%%","Always" in
# 1.6.1 at 0x0819F30C) with 35 A:B labels, and convert values in the piano-roll and drum
# step editors. Helpers and the new label table live in our reserved block.
AB_LABELS = [f"{a}:{b}" for b in range(2, 9) for a in range(1, b + 1)]      # stored 100..134 (Lab T2)
# Stored values stay stable (saved projects keep their meaning): A:B 100..134,
# !A:B 135..169, 1ST 170, !1ST 171, PRE 172, !PRE 173. The list shows them as
# 1ST, !1ST, PRE, !PRE, then each A:B followed by its NOT.
COND_STORED = [170, 171, 172, 173] + [v for k in range(35) for v in (100 + k, 135 + k)]
COND_LABELS = ["1ST", "!1ST", "PRE", "!PRE"] + [l for x in AB_LABELS for l in (x, "!" + x)]
COND_N = len(COND_STORED)                                                    # 74 -> list of 174
COND_UI_SITES = [  # (site, helper)
    ("PROB_SHOW_PIANO_SITE", "show_piano"),
    ("PROB_SHOW_DRUM_SITE", "show_drum"),
    ("PROB_PIANO_REF_SITE", "piano_reference"),
    ("PROB_PIANO_EDIT_SITE", "piano_edit"),
]

# Inspect a step (from bento-community-lab T2 holdstep, GPL-3.0-or-later): only the
# edit-protects-release part; no selection kept after release.
HOLD_EDIT_SITES = ["STEP_EDIT_VEL", "STEP_EDIT_PROB", "STEP_EDIT_OFFSET"]   # velocity, condition, timing edit entries

# Hold shortcuts (port of bento-community-lab K16 hold controls, GPL-3.0-or-later).
# M7 input + startup code in stock's zero gap (S.CTRL_INPUT, S.CTRL_STARTUP); state on our
# M7-only line CTRL_STATE (magic, suppress mask, drops, enable, pad map).
HOLD_TAP_MS = 150   # Jeff: 400 -> 150 ms, confirmed on device v3.13

# ---- What each group builds, and which table entries it uses (S2) --------------------------------
# A build uses an entry if build.py reads it for a group it builds (GROUP_NAMES, checked against
# what the build really read: Tracked) or if a source it compiles names it (found by preprocessing
# the source with every entry defined as a marker: source_refs). "core" is built whatever is
# disabled: the black box's M7 and M4 code, the layout, the version label and the base checks.
M7_FEATURES = ("poly_cap", "poly_at", "inta_curve", "inta_live", "cond_engine", "retrig", "nmod")   # m7_polyat/_conditions/_retrig/_mod call each other
SOURCES = [  # (source, ELF, groups that need it; () = core), in compile order
    ("m7_diag.c", "m7", ()), ("m7_polyat.c", "m7", M7_FEATURES), ("m7_conditions.c", "m7", M7_FEATURES),
    ("m7_stall.c", "m7", ("stall",)), ("m7_retrig.c", "m7", M7_FEATURES), ("m7_mod.c", "m7", M7_FEATURES),
    ("m4_diag.c", "m4", ()), ("m4_holdstep.c", "m4_holdstep", ("step",)),
    ("m7_controls_input.s", "asm", ("hold",)), ("m4_controls.c", "m4_controls", ("hold",)),
    ("m4_step.c", "m4_step", ("step",)),
]
GROUP_NAMES = {
    "core": ("M7_TRAMP", "M7_CODE", "M7_LIMIT", "CTRL_INPUT", "CTRL_STARTUP", "APPEND_FROM", "D2_SLICE", "D2_SLICE_END",
             "RAMCHK_AXI", "RAMCHK_AXI_END", "RAMCHK_D2A", "RAMCHK_D2B", "OUR_RAM_WORDS", "M7_STACK_TOP",
             "M7_HEAP_END_LITS", "M7_HEAP_END", "VERSION_STR", "REGION", "EXT_POOL_END", "POOL_INIT_FLASH",
             "POOL_LEFT_INIT", "POOL_CUR_INIT", "ALLOC_BUMP", "ALLOC_BUMP_LEN", "ALLOC_BUMP_SHA", "POOL_LEFT_RAM",
             "POOL_LEFT_LITS", "POOL_CUR_RAM", "POOL_CUR_LITS", "MPU_SETUP", "MPU_SETUP_END", "MPU_SETUP_SHA",
             "M4_STACK_BOTTOM"),
    "trace": ("H_INPUT_SITE", "H_SEND_SITE", "H_RING_SITE", "H_CONSUME_SITE"),
    "poly_cap": ("MSG_SEND", "POLYCAP_SEQ_ON_SITE", "POLYCAP_PAD_ON_SITE", "POLYCAP_SEQ_OFF_SITE", "POLYCAP_PAD_OFF_SITE",
                 "POLYCAP_ALL_OFF_SITE"),
    "poly_at": ("EV_VOICE_PRESSURE", "POLY_RESUME", "POLY_SITE", "M4MIDI_BEGIN", "M4MIDI_STATUS",
                "M4MIDI_CHANNEL", "M4MIDI_DATA", "M4MIDI_SEND", "M4MIDI_RESULT", "M4MIDI_DONE", "M4_LOOP",
                "M4_NOTE_ON_CASE", "MIDI_JT", "EV_MIDI_POLY_AT", "MIDI_JT_FIRST", "EV_MIDI_CHAN_AT"),
    "inta_curve": ("BUS_POST",),
    "inta_live": ("LIVE_SITE", "BUS_POST"),
    "retrig": ("NOTE_ROUTE", "RETRIG_ACCEPT_SITE", "RETRIG_EMIT_SITE"),
    "nmod": ("NM_OFF_SITE_1", "NM_OFF_SITE_2", "NM_NOTE_OFF", "NM_ALL_OFF"),
    "stall": ("STALL_PROGRESS_SITE", "WD_REFRESH", "STALL_CHECK_SITE", "MSG_PUMP", "SYSTICK_HANDLER", "HAL_INCTICK"),
    "cond_engine": ("COND_GATE_SITE", "COND_SKIP", "COND_LOOP_SITE"),
    "cond_ui": ("PROB_SHOW_PIANO_SITE", "PROB_SHOW_DRUM_SITE", "PROB_PIANO_REF_SITE", "PROB_PIANO_REF_BACK",
                "PROB_PIANO_REF_SHA", "PROB_PIANO_EDIT_SITE", "PROB_PIANO_EDIT_BACK", "PROB_PIANO_EDIT_SHA"),
    "irq30": ("M4_WD_SUPERVISOR", "M4_WD_SITE"),
    "bbox": ("FILE_WRITER", "SAVE_WRITE_SITE", "M4_BL_TARGET", "M4_BL_SITE"),
    "hold": ("PRODUCER_GLOBAL", "ENQUEUE", "PAD_NEXT", "PAD_CHANGED", "CTRL_INPUT_SITE", "CTRL_STARTUP_SITE",
             "CTRL_STARTUP_LITS", "CTRL_CONSUMER_SITE", "CTRL_DISPATCH_NEXT", "CTRL_SELECTION_SITE",
             "MIXER_RENDER_VT_SLOT", "MIXER_RENDER"),
    "step": ("STEP_SAVE_SITE", "STEP_LOAD_SITE", "DRUM_HEADER", "DRUM_ENCODER", "PIANO_HEADER", "PIANO_EVENT",
             "STEP_EDIT_VEL", "STEP_EDIT_PROB", "STEP_EDIT_OFFSET", "STEP_PRESS", "STEP_PRESS_SKIP", "STEP_TOGGLE",
             "STEP_DEFAULTS_READ", "HEADER_GEN", "DRUM_EVENT", "EXT_BUILD", "EXT_EVENT", "EV_ENCODER", "MX_BUILD",
             "MX_ROUTE", "MX_EDIT", "MX_BAR", "MX_EVENT", "NOTE_INSERT", "NOTE_UPDATE", "DOC_READ", "HOLD_TAP_PAD",
             "HOLD_TAP_TOUCH"),
}
GROUP_TITLES = {"core": "black box core (always built)", "trace": "M7 event tracing", "poly_cap": "poly cap (M7)",
                "poly_at": "poly AT: External handler + M4 MIDI 0x5F/0x60 handlers (S5)",
                "inta_curve": "curve playback on internal instruments (M7)", "inta_live": "internal live AT (LIVE_SITE, S5)",
                "retrig": "retrigger + curve playback hooks", "nmod": "Note Mod engine (M7)", "stall": "M7 stall record + profile",
                "cond_engine": "trigger conditions engine (M7)", "cond_ui": "probability editor clamps (M4, S4)",
                "irq30": "M4 watchdog profile", "bbox": "black box M4 hooks",
                "hold": "hold shortcuts", "step": "per-step UI / strike / curves / Mod Matrix"}
assert set(GROUP_NAMES) == {"core"} | {g for g, _ in PATCH_GROUPS}


class Tracked:
    """The base table as build.py sees it: every entry read is recorded under the group whose code
    is being built (`with S.group("poly"):`), so a build can show it read only what GROUP_NAMES
    declares. Reading a REVIEW entry is an error (the table is loaded without allow_draft)."""
    def __init__(self, base):
        self.__dict__.update(_b=base, _cur="core", read={})

    def group(self, g):
        t = self
        class _G:
            def __enter__(self):
                self.prev, t.__dict__["_cur"] = t._cur, g
            def __exit__(self, *a):
                t.__dict__["_cur"] = self.prev
        return _G()

    def _note(self, name):
        if name in self._b.syms:
            self.read.setdefault(self._cur, set()).add(name)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self._note(name)
        return getattr(self._b, name)

    def orig(self, name):
        self._note(name)
        return self._b.orig(name)

    def has(self, name):
        self._note(name)
        return self._b.has(name)


def source_refs(base, src, disabled, flags=()):
    """Table names a source really uses in this build (after #ifdef BE_NO_<group>): the source is
    preprocessed with every entry defined as a marker token (bases.Base.marker_header)."""
    import tempfile
    path = HERE / src
    if src.endswith(".s"):
        return {m for m in re.findall(r"@S_([A-Z0-9_]+)@", path.read_text())}
    with tempfile.TemporaryDirectory() as d:
        hdr = Path(d) / "stock_syms.h"
        hdr.write_text(base.marker_header())
        write_step_tables(Path(d) / "step_tables.h")
        cpu = "cortex_m7" if src.startswith("m7_") else "cortex_m4"
        cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "thumb-freestanding-eabi", f"-mcpu={cpu}", "-E", "-P",
               "-ffreestanding", "-nostdlib", "-include", str(hdr), f"-I{d}",
               *[f"-DBE_NO_{g.upper()}" for g in sorted(disabled)], *flags, str(path)]
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return set(re.findall(r"__S_REF_([A-Za-z0-9_]+)", out))


def plan(base, disabled):
    """(included groups, omitted groups, refusals, needs): which groups this build builds on this
    base. A group needs its GROUP_NAMES and every entry the sources it needs use. A group that
    needs an unreviewed entry, or is not verified on this base (BASE_SETTINGS groups), is omitted
    (not built at all) when disabled and refuses the build when enabled; core must always build."""
    if base.module not in BASE_SETTINGS:
        raise SystemExit(f"build.py has no BASE_SETTINGS for {base.module} (version line, M4 flags, groups)")
    verified = BASE_SETTINGS[base.module]["groups"]
    m7_flags = BASE_SETTINGS[base.module]["m7_flags"]                 # the defines the M7 code is compiled with
    refs = {src: source_refs(base, src, disabled, m7_flags if src.startswith("m7_") else ()) for src, _, _ in SOURCES}
    def needs(g):
        n = set(GROUP_NAMES[g])
        for src, _, gs in SOURCES:
            if (gs == () and g == "core") or g in gs:
                n |= refs[src]
        return n
    included, omitted, refused = {"core"}, set(), {}
    for g in GROUP_NAMES:
        if g == "core":
            continue
        bad = [n for n, _ in base.unreviewed(sorted(needs(g)))]
        if not bad and (verified is None or g in verified):
            included.add(g)
        elif g in disabled:
            omitted.add(g)
        else:
            refused[g] = bad
    bad = [n for n, _ in base.unreviewed(sorted(needs("core")))]
    if bad:
        refused["core"] = bad
    return included, omitted, refused, {g: needs(g) for g in GROUP_NAMES}


def refuse_message(base, refused, omitted):
    lines = [f"{base.module} ({base.label}): enabled features are not ported to this base or use entries that are "
             f"not reviewed for it (tools/addrmap/README.md); refusing to build."]
    verified = BASE_SETTINGS[base.module]["groups"]
    for g, names in refused.items():
        why = [] if verified is None or g == "core" or g in verified else [f"not yet ported to {base.label}"]
        if names:
            why.append(f"{len(names)} unreviewed: " + ", ".join(names))
        lines.append(f"  {g} ({GROUP_TITLES[g]}): " + "; ".join(why))
    if "core" not in refused:
        off = sorted((set(refused) | omitted) & {g for g, _ in PATCH_GROUPS})
        lines.append(f"Build without them: BE_DISABLE={','.join(off)}")
    return "\n".join(lines)


def layout(S):
    """Our placement for this base: the appended M4 blocks in order, and our shared lines."""
    L = dict(M7_TRAMP=S.M7_TRAMP, M7_CODE=S.M7_CODE, M7_LIMIT=S.M7_LIMIT, CTRL_INPUT=S.CTRL_INPUT,
             CTRL_STARTUP=S.CTRL_STARTUP, APPEND_FROM=S.APPEND_FROM)
    L["COND_UI"] = S.APPEND_FROM                               # probability clamp helpers
    L["COND_UI_LIMIT"] = L["M4_CODE"] = S.APPEND_FROM + 0x1000  # black box (m4_diag.c)
    L["M4_LIMIT"] = L["M4_CODE"] + 0x6000   # 24 KB since v3.64 (20 KB since v3.54, 16 KB since v3.26: curve sidecar writer)
    L["HOLD_WRAP"] = L["M4_LIMIT"]; L["HOLD_CODE"] = L["HOLD_WRAP"] + 0x100; L["HOLD_LIMIT"] = L["HOLD_WRAP"] + 0x400
    L["CTRL_WRAP"] = L["HOLD_LIMIT"]; L["CTRL_CODE"] = L["CTRL_WRAP"] + 0x200; L["CTRL_LIMIT"] = L["CTRL_WRAP"] + 0x1000
    L["STEP_WRAP"] = L["CTRL_LIMIT"]; L["STEP_CODE"] = L["STEP_WRAP"] + 0x600   # m4_step.c
    L["STEP_LIMIT"] = L["STEP_WRAP"] + 0x6000                   # 24 KB since v3.62; 20 KB since v3.41
    L["M4_POLY"] = L["STEP_LIMIT"]; L["M4_POLY_LIMIT"] = L["M4_POLY"] + 0x100   # 0x5F handler (72 B), 0x60 handler at +0x80
    L["M4_CHAN"] = L["M4_POLY"] + 0x80
    L["CTRL_STATE"] = S.D2_SLICE + 0x8A0                       # M7-only line (hold shortcuts)
    L["INST_HOLD"] = S.D2_SLICE + 0x8F0                        # m4_controls.c InstHold
    L["LAYOUT"] = dict(M7_TRAMP=L["M7_TRAMP"], M7_CODE=L["M7_CODE"], M4_CODE=L["M4_CODE"], HOLD_WRAP=L["HOLD_WRAP"],
                       HOLD_CODE=L["HOLD_CODE"], CTRL_WRAP=L["CTRL_WRAP"], CTRL_CODE=L["CTRL_CODE"],
                       STEP_WRAP=L["STEP_WRAP"], STEP_CODE=L["STEP_CODE"], M4_POLY=L["M4_POLY"],
                       IMAGE_END=L["M4_POLY_LIMIT"])
    return L


def use_base(s):
    """Select the base table; our layout names (M4_CODE, HOLD_WRAP, ...) become module globals, and
    the version line comes from the base (BASE_SETTINGS)."""
    global S, MARKER, VERSION, DISPLAY, SETTINGS
    S = s
    if S.module not in BASE_SETTINGS:
        raise SystemExit(f"build.py has no BASE_SETTINGS for {S.module} (version line, M4 flags)")
    SETTINGS = BASE_SETTINGS[S.module]
    VERSION = SETTINGS["version"] + (f"-{os.environ['BE_TAG']}" if os.environ.get("BE_TAG") else "")
    DISPLAY = os.environ.get("BE_LABEL", f"BE{SETTINGS['version']}")
    globals().update(layout(s))
    MARKER = f"BENTO_MOD:bento_enhanced:v{VERSION}:{S.label}".encode() + bytes(1)
    return S


def ctrl_symbols():
    """Template symbols of the hold shortcuts' M7 asm (group hold)."""
    return dict(STATE=hex(CTRL_STATE), STATE_MAGIC="0x31534d42", EVENT_MAGIC="0x314d5442", EVENT_TAIL="0x31525442",
                PRODUCER_GLOBAL=hex(S.PRODUCER_GLOBAL), ENQUEUE=hex(S.ENQUEUE | 1), PAD_NEXT=hex(S.PAD_NEXT | 1),
                PAD_CHANGED=hex(S.PAD_CHANGED | 1), TRACKS_MASK="0x2000", LAUNCH_MASK="0x1000", MIXER_MASK="0x400",
                MODIFIERS_MASK="0x3400")             # Lab: 0x3500 (incl. PROJ 0x100)


def cond_ui_helpers():
    """Editor value conversions for the probability byte (Lab T2 sites). Since
    v3.3 conditions have their own encoder, so these only clamp: a legacy
    condition value (100..173) still in the byte shows as "Always" instead of
    indexing past stock's 100-entry list."""
    def show(store, back):   # stored byte r3 -> list index (0-based) in [store]
        return f"cmp r3, #100; blo dec; movs r3, #99; b store; dec: subs r3, #1; store: str r3, [{store}]; b.w {back:#x}"
    return [   # (name, source, exit): each helper replaces a 4-byte instruction and goes back after it or on
        ("show_piano", show("r6", S.PROB_SHOW_PIANO_SITE + 4), S.PROB_SHOW_PIANO_SITE + 4),
        ("show_drum", show("r1", S.PROB_SHOW_DRUM_SITE + 4), S.PROB_SHOW_DRUM_SITE + 4),
        # piano roll works in 1-based list values: 1..99 = %, 100 = Always
        ("piano_reference", "ldrb.w r3, [sp, #0x25]; cbz r3, always; cmp r3, #100; blo done; "
                            f"always: movs r3, #100; done: b.w {S.PROB_PIANO_REF_BACK:#x}", S.PROB_PIANO_REF_BACK),
        ("piano_edit", "ldrb.w r3, [sp, #0x19]; cbz r3, always; cmp r3, #100; blo add; always: movs r3, #100; "
                       "add: ldr r2, [sp, #0x84]; adds r3, r3, r2; cmp r3, #1; bge minok; movs r3, #1; "
                       "minok: cmp r3, #100; blt store; movs r3, #0; "
                       f"store: strb.w r3, [sp, #0x31]; b.w {S.PROB_PIANO_EDIT_BACK:#x}", S.PROB_PIANO_EDIT_BACK),
    ]

def check_branches(code, addr, allowed, what):
    """Keystone can mis-encode conditional branches: every branch in an assembled
    block must land inside the block or on an expected exit."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
    md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
    for i in md.disasm(code, addr):
        if i.mnemonic.startswith(("b", "cb")) and i.op_str.startswith("#") and i.mnemonic not in ("bic", "bics", "bfi", "bfc"):
            t = int(i.op_str[1:], 16)
            if not (addr <= t < addr + len(code) or t in allowed):
                raise SystemExit(f"{what}: branch at {i.address:#x} to {t:#x} is outside the block and not an expected exit")

ks = Ks(KS_ARCH_ARM, KS_MODE_THUMB)
def asm(src, addr):
    return bytes(ks.asm(src, addr)[0])

COND = {"eq": 0, "ne": 1}
def bcond_w(cond, src, dst):
    """Thumb-2 conditional branch, encoding T3 (+-1 MB), encoded by hand.
    Keystone mis-encodes conditional branches to absolute targets (asked for
    bne.w 0x08062080 at 0x08062466 it emitted a branch to 0x080C44EA)."""
    off = dst - (src + 4)
    if off & 1 or not -(1 << 20) <= off < (1 << 20):
        raise SystemExit(f"conditional branch {src:#x}->{dst:#x} out of range")
    imm = off & 0x1FFFFF
    S, J2, J1 = (imm >> 20) & 1, (imm >> 19) & 1, (imm >> 18) & 1
    imm6, imm11 = (imm >> 12) & 0x3F, (imm >> 1) & 0x7FF
    hw1 = 0xF000 | (S << 10) | (COND[cond] << 6) | imm6
    hw2 = 0x8000 | (J1 << 13) | (J2 << 11) | imm11
    return struct.pack("<HH", hw1, hw2)

DIV_LABELS = ["1/8", "1/8T", "1/8.", "1/16", "1/16T", "1/16.", "1/32", "1/32T", "1/32.", "1/64", "1/64T"]
RAMP_STEP = 5   # percent per encoder click: -100..+100 = 41 values

# F2b destination list (Jeff): named messages first, then every CC by number, a
# standard name attached where one exists. type: 0 none, 1 CC, 2 pitch bend, 3 channel AT.
CC_NAMES = {0: "Bank", 1: "Mod Wheel", 2: "Breath", 4: "Foot", 5: "Porta Time", 7: "Volume", 8: "Balance",
            10: "Pan", 11: "Expr", 12: "Effect 1", 13: "Effect 2", 64: "Sustain", 65: "Porta",
            66: "Sostenuto", 67: "Soft", 68: "Legato", 69: "Hold 2", 70: "Variation", 71: "Resonance",
            72: "Release", 73: "Attack", 74: "Cutoff", 75: "Decay", 76: "Vib Rate", 77: "Vib Depth",
            78: "Vib Delay", 84: "Porta Ctl", 91: "Reverb", 92: "Tremolo", 93: "Chorus", 94: "Detune", 95: "Phaser"}
DEST_NAMED = [("None", 0, 0), ("Pitch Bend", 2, 0), ("Aftertouch", 3, 0), ("Mod Wheel", 1, 1), ("Breath", 1, 2),
              ("Volume", 1, 7), ("Pan", 1, 10), ("Expression", 1, 11), ("Foot", 1, 4), ("Cutoff", 1, 74),
              ("Resonance", 1, 71), ("Attack", 1, 73), ("Release", 1, 72)]

def dest_list():
    out = list(DEST_NAMED)
    for n in range(128):
        if n in (0, 32) or n >= 120:   # Bank Select; channel-mode messages (All Notes Off, Reset...)
            continue
        if any(t == 1 and num == n for _, t, num in DEST_NAMED):   # already in the named part
            continue
        out.append((f"CC {n} {CC_NAMES[n]}" if n in CC_NAMES else f"CC {n}", 1, n))
    for label, _, _ in out:
        assert len(label) <= 15, label
    # Stock's list encoders keep the value in 7 bits: v3.50-3.51's 141 entries wrapped
    # after "CC 0 Bank" (141 - 127 = 14 reachable).
    assert len(out) <= 128, len(out)
    return out

def dest_tables():
    d = dest_list()
    return (f"#define DEST_N {len(d)}\n"
            + f"static const char *const DEST_LABELS[{len(d)}] = {{" + ", ".join(f'"{x[0]}"' for x in d) + "};\n"
            + f"static const unsigned char DEST_TYPE[{len(d)}] = {{" + ", ".join(str(x[1]) for x in d) + "};\n"
            + f"static const unsigned char DEST_NUM[{len(d)}] = {{" + ", ".join(str(x[2]) for x in d) + "};\n")

def write_step_tables(path):
    """Generated C tables for m4_step.c (single source: the lists above)."""
    def strs(name, items):
        return f"static const char *const {name}[{len(items)}] = {{" + ", ".join(f'"{x}"' for x in items) + "};\n"
    ramp = [f"{v:+d}%" if v else "0%" for v in range(-100, 101, RAMP_STEP)]
    text = ("/* Generated by build.py (write_step_tables); do not edit. */\n"
            f"#define COND_N {COND_N}\n#define RAMP_N {len(ramp)}\n#define RAMP_STEP {RAMP_STEP}\n"
            + strs("COND_LABELS", ["Off"] + COND_LABELS)
            + strs("HITS_LABELS", ["Off"] + [str(i) for i in range(1, 16)])
            + strs("DIV_LABELS", DIV_LABELS)
            + strs("RAMP_LABELS", ramp)
            + f"static const unsigned char COND_STORED[{COND_N}] = {{" + ", ".join(map(str, COND_STORED)) + "};\n"
            + dest_tables())
    path.write_text(text)

def compile_elf(srcs, cpu, ld, out, origin, limit):
    """ld's MEMORY ORIGIN/LENGTH are replaced by this build's layout (written next to the ELF)."""
    text, n = re.subn(r"ORIGIN = 0x[0-9A-Fa-f]+, LENGTH = 0x[0-9A-Fa-f]+",
                      f"ORIGIN = {origin:#x}, LENGTH = {limit - origin:#x}", Path(ld).read_text())
    if n != 1:
        raise SystemExit(f"{ld}: expected one MEMORY line")
    ld = out.with_suffix(".ld")
    ld.write_text(text)
    write_step_tables(out.parent / "step_tables.h")
    syms_h = out.parent / "stock_syms.h"
    syms_h.write_text(S.c_header())
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "thumb-freestanding-eabi", f"-mcpu={cpu}",
           "-mfloat-abi=soft", "-Os", "-g", "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-nostdlib",
           "-ffunction-sections", "-fdata-sections", "-Wall", "-Werror",
           "-Wl,--gc-sections", "-include", str(syms_h), f"-I{out.parent}", f"-Wl,--script={ld}", "-Wl,--entry=0",
           *(SETTINGS["m4_flags"] if cpu == "cortex_m4" else SETTINGS["m7_flags"]),
           *[f"-DBE_NO_{g.upper()}" for g in sorted(DISABLED)], *map(str, srcs), "-o", str(out)]
    subprocess.run(cmd, check=True)
    return out.read_bytes()

def elf_load(e, lo, hi):
    """Return (load address, bytes) of the single loadable segment inside [lo, hi), plus symbols.
    The linker also emits a PT_LOAD for its own ELF headers below ORIGIN; those bytes are never
    written to the image (they would land on Lab's code), and any other stray segment is an error."""
    phoff, shoff = struct.unpack_from("<II", e, 28)
    phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", e, 42)
    segs = []
    for i in range(phnum):
        t, off, va, _, fsz, msz, flags, _ = struct.unpack_from("<8I", e, phoff + i * phentsize)
        if t == 1 and msz:
            hdr_only = off == 0 and va + fsz <= lo  # ELF/program headers, not part of our code
            if hdr_only:
                continue
            if not (lo <= va and va + msz <= hi):
                raise ValueError(f"segment {va:#x}+{msz:#x} outside our slice")
            if msz != fsz:
                raise ValueError("unexpected .bss/zero-fill in segment: this code must have no RAM data")
            segs.append((va, e[off:off + fsz], flags))
    if len(segs) != 1:
        raise ValueError(f"expected one loadable segment, got {[(hex(v), len(b)) for v, b, _ in segs]}")
    syms = {}
    secs = [struct.unpack_from("<10I", e, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        strtab = secs[s[6]]
        names = e[strtab[4]:strtab[4] + strtab[5]]
        for j in range(0, s[5], s[9]):
            nm, val, _, info, _, _ = struct.unpack_from("<IIIBBH", e, s[4] + j)
            if info & 15 == 2:  # STT_FUNC
                syms[names[nm:].split(b"\0")[0].decode()] = val & ~1
    va, data, _ = segs[0]
    return va, data, syms

# v3.70: the Region sits in the top 16 KB of the M7's ext pool, which stock could one day use.
# The base table's sha256 refuses any other image, so nothing here can fire until a new
# firmware gets a table. Then it names what has to be checked again, instead of a build that
# works and quietly shares memory with stock. The addresses come from that base's table
# (S.POOL_*, S.ALLOC_BUMP*, S.MPU_SETUP*). The runtime check in region() stays as the second
# line: an unknown pool shape leaves our state off and notes pass.

def shared_define(name):
    m = re.search(rf"^#define {name} (0x[0-9A-Fa-f]+)u", (HERE / "diag_shared.h").read_text(), re.M)
    if not m:
        raise SystemExit(f"diag_shared.h: no {name}")
    return int(m.group(1), 16)

def check_region_assumptions(img, S):
    """Refuse a base whose ext pool no longer has room for the Region where we put it."""
    def word(a): return struct.unpack_from("<I", img, a - BASE)[0]
    def fail(what): raise SystemExit(f"Region move (v3.70): {what}; re-verify the reservation for this firmware")
    lo = S.REGION
    hi = S.EXT_POOL_END
    if lo + shared_define("M1_REGION_RESERVE") != hi:
        fail("the Region does not end at the pool's end")
    if word(S.POOL_INIT_FLASH) != S.POOL_LEFT_INIT or word(S.POOL_INIT_FLASH + 4) != S.POOL_CUR_INIT:
        fail("the pool's starting values changed")
    if S.POOL_LEFT_INIT + S.POOL_CUR_INIT != hi:
        fail("the pool no longer ends where the Region does")
    if hashlib.sha256(img[S.ALLOC_BUMP - BASE:S.ALLOC_BUMP - BASE + S.ALLOC_BUMP_LEN]).hexdigest() != S.ALLOC_BUMP_SHA:
        fail("the pool allocator changed")
    words = struct.unpack_from(f"<{len(img) // 4}I", img)
    def literals(v): return tuple(BASE + 4 * i for i, w in enumerate(words) if w == v)
    if literals(S.POOL_LEFT_RAM) != S.POOL_LEFT_LITS or literals(S.POOL_CUR_RAM) != S.POOL_CUR_LITS:
        fail("something else now addresses the pool's counters")
    if hashlib.sha256(img[S.MPU_SETUP - BASE:S.MPU_SETUP_END - BASE]).hexdigest() != S.MPU_SETUP_SHA:
        fail("the M7's MPU setup changed, so the Region may not be write-back any more")
    near = [BASE + 4 * i for i, w in enumerate(words) if lo <= w < hi]
    if near:
        fail(f"literal into the reserved range at {near[0]:#x}")
    for k in range(0, len(img) - 3, 2):             # movt rX, #hi16 of the reserved range
        h1, h2 = struct.unpack_from("<HH", img, k)
        if (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000:
            v = (h1 & 15) << 12 | (h1 >> 10 & 1) << 11 | (h2 >> 12 & 7) << 8 | h2 & 255
            if lo >> 16 <= v < hi >> 16:
                fail(f"movt into the reserved range at {BASE + k:#x}")

def our_ram(S):
    """Our RAM on this base, (lo, hi) each: the shared slice and the RAMCHK blocks around it."""
    r = [(S.D2_SLICE, S.D2_SLICE_END), (S.RAMCHK_AXI, S.RAMCHK_AXI_END)]
    if S.has("RAMCHK_D2A"):                                  # 1.6.1 only: D2 above the M4 stack
        r.append((S.RAMCHK_D2A, S.D2_SLICE))
    return r + [(S.RAMCHK_D2B, S.D2_SLICE_END)]


def check_slice_assumptions(img, S):
    """S2: refuse a base where stock code might reach our reset-surviving RAM. Its place was checked
    by hand (facts.py D2_SLICE); this keeps it checked: our ranges stay above the M7 stack and clear
    of the M4's stack (M4_STACK_BOTTOM up to its initial SP), and no image word points into them other
    than the instruction encodings the table lists (OUR_RAM_WORDS), and no movt builds an address there."""
    def word(a): return struct.unpack_from("<I", img, a - BASE)[0]
    def fail(what): raise SystemExit(f"Our RAM ({S.label}, S2): {what}; re-verify the slice for this firmware")
    ranges = our_ram(S)
    if S.D2_SLICE & 0xFFF or S.D2_SLICE_END - S.D2_SLICE < 0x2000 or S.RAMCHK_D2B != S.D2_SLICE + 0x2000:
        fail("the slice is not a 4 KB aligned block of at least 0x2000 bytes with the top block at +0x2000")
    if word(BASE) != S.M7_STACK_TOP:
        fail(f"the M7's initial stack pointer is {word(BASE):#x}, not M7_STACK_TOP")
    m4_sp = word(0x08100000)                                 # M4 vector table (the M4 image half)
    if not S.M4_STACK_BOTTOM < m4_sp:
        fail(f"the M4's initial stack {m4_sp:#x} is not above M4_STACK_BOTTOM {S.M4_STACK_BOTTOM:#x}")
    for lo, hi in ranges:
        if lo >> 24 == S.M7_STACK_TOP >> 24 and lo < S.M7_STACK_TOP:
            fail(f"{lo:#x} is below the M7 stack top {S.M7_STACK_TOP:#x}")
        if lo < m4_sp and S.M4_STACK_BOTTOM < hi:
            fail(f"{lo:#x}..{hi:#x} overlaps the M4's stack {S.M4_STACK_BOTTOM:#x}..{m4_sp:#x}")
    words = struct.unpack_from(f"<{len(img) // 4}I", img)
    found = tuple(BASE + 4 * i for i, w in enumerate(words) if any(lo <= w < hi for lo, hi in ranges))
    if found != tuple(sorted(S.OUR_RAM_WORDS)):
        new, gone = sorted(set(found) - set(S.OUR_RAM_WORDS)), sorted(set(S.OUR_RAM_WORDS) - set(found))
        fail(f"image words pointing into our RAM changed: new {[hex(a) for a in new]}, gone {[hex(a) for a in gone]}")
    tops = {lo >> 16 for lo, hi in ranges} | {(hi - 1) >> 16 for lo, hi in ranges}
    for k in range(0, len(img) - 3, 2):                      # movt rX, #hi16 of our RAM
        h1, h2 = struct.unpack_from("<HH", img, k)
        if (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000:
            v = (h1 & 15) << 12 | (h1 >> 10 & 1) << 11 | (h2 >> 12 & 7) << 8 | h2 & 255
            if v in tops:
                fail(f"movt #{v:#x} at {BASE + k:#x} may build an address in our RAM")


def check_sites(img, S):
    """Every patch site in the table still holds the bytes the table expects."""
    for n, s in S.syms.items():
        if s.orig is not None and s.kind in ("code", "data"):
            o = s.value - BASE
            if bytes(img[o:o + len(s.orig)]) != s.orig:
                raise SystemExit(f"{S.label}: {n} at {s.value:#x} holds {bytes(img[o:o + len(s.orig)]).hex()}, "
                                 f"the table expects {s.orig.hex()}")

# Prologue replays: the stock instruction(s) a wrapper re-executes after taking over a site.
PUSH_R4_R11_LR = "push.w {r4, r5, r6, r7, r8, r9, r10, r11, lr}"
PUSH_R4_R9_LR = "push.w {r4, r5, r6, r7, r8, r9, lr}"
def replays(site_name, displaced):
    """The instruction(s) a wrapper replays must be what stock has at the site (a new base
    whose prologue changed is refused here, not run with the old prologue)."""
    site = getattr(S, site_name)
    if asm(displaced, site) != S.orig(site_name):
        raise SystemExit(f"{site_name}: replayed {displaced!r} is not stock's {S.orig(site_name).hex()} at {site:#x}")

def replaced(img, site_name, back_name, sha_name):
    """A helper that takes over stock's code from a site up to a resume point re-implements it
    (with its registers and stack offsets): that code must be exactly what the table recorded."""
    lo, hi = getattr(S, site_name), getattr(S, back_name)
    if hashlib.sha256(img[lo - BASE:hi - BASE]).hexdigest() != getattr(S, sha_name):
        raise SystemExit(f"{site_name}..{back_name}: stock's code there is not what the helper re-implements ({sha_name})")

def build(base_img, out):
    s = bases.select(base_img)                   # refuses an unknown image; REVIEW entries can't be read
    included, omitted, refused, needs = plan(s, DISABLED)
    if refused:                                  # an enabled feature (or core) uses a draft entry
        raise SystemExit(refuse_message(s, refused, omitted))
    use_base(Tracked(s))
    inc = included.__contains__
    if len(base_img) != S.size:
        raise SystemExit(f"base image is {len(base_img):#x} bytes, {S.label} is {S.size:#x}")
    bad = S.verify_image(base_img)               # every address the table names, against the image
    if bad:
        raise SystemExit(f"{S.module} disagrees with its image (tools/addrmap/fingerprints.py --check):\n  " + "\n  ".join(bad))
    # offsets / keys / events, against the second record: every reviewed entry, used or not
    bad = bases.verify_expected(s, EXPECTED, names={n for n in s.syms if s.is_reviewed(n)})
    if bad:
        raise SystemExit(f"{S.module} disagrees with expected_values.py:\n  " + "\n  ".join(bad))
    if (HERE / "stock_syms.h").exists():
        raise SystemExit("mods/m1/stock_syms.h exists: the generated header belongs in the build directory only")
    bad = literal_check.check(str(HERE.parent.parent))
    if bad:
        raise SystemExit("bare stock addresses outside the base tables (bases/literal_check.py):\n  " + "\n  ".join(bad))
    check_region_assumptions(base_img, S)
    check_slice_assumptions(base_img, S)
    check_sites(base_img, S)
    out.mkdir(parents=True, exist_ok=False)
    img = bytearray(base_img)
    writes = []
    skipped = []

    def patch(addr, new, expect, why):
        if len(new) <= 8 and patch_group(why) in DISABLED:   # bisect build: leave stock's site alone
            skipped.append((addr, why)); return
        o = addr - BASE
        old = bytes(img[o:o + len(new)])
        if expect is not None and old != expect:
            raise SystemExit(f"{why}: original bytes at {addr:#x} are {old.hex()}, expected {expect.hex()}")
        img[o:o + len(new)] = new
        writes.append(dict(address=addr, size=len(new), original_hex=old.hex(), replacement_hex=new.hex(), purpose=why))

    def extend(to, why):
        """Grow the image with erased (0xFF) flash up to `to`."""
        pad = b"\xff" * (to - (BASE + len(img)))
        writes.append(dict(address=BASE + len(img), size=len(pad), original_hex="", replacement_hex=pad.hex(), purpose=why))
        img.extend(pad)

    def sources(elf):
        return [HERE / src for src, e, gs in SOURCES if e == elf and (not gs or any(inc(g) for g in gs))]

    # --- M7 heap: left at stock since v3.16 (our slice is in D2 SRAM2 on 1.6.1, AXI on 1.6.8)
    for a in S.M7_HEAP_END_LITS:
        if img[a - BASE:a - BASE + 4] != struct.pack("<I", S.M7_HEAP_END):
            raise SystemExit(f"heap literal at {a:#x} is not stock")

    # --- M7: compile, place, trampolines, entry patches
    m7_elf = compile_elf(sources("m7"), "cortex_m7", HERE / "m7.ld", out / "m7.elf", M7_CODE, M7_LIMIT)
    va, code7, s7 = elf_load(m7_elf, M7_CODE, M7_LIMIT)
    if va != M7_CODE or M7_CODE + len(code7) > M7_LIMIT:
        raise SystemExit(f"M7 code placement {va:#x}+{len(code7)} outside our slice")
    if any(base_img[M7_TRAMP - BASE:M7_LIMIT - BASE]):
        raise SystemExit("our M7 slice is not all zero in the base image")
    tramps = b""
    with S.group("trace"):
        for name, site_name, displaced, setup in M7_HOOKS:
            site, orig, resume = getattr(S, site_name), S.orig(site_name), getattr(S, site_name) + 4
            replays(site_name, displaced)
            t = M7_TRAMP + len(tramps)
            src = ("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
                   f"{setup}bl {s7[name]:#x}; "
                   "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
                   f"{displaced}; b.w {resume:#x}")
            code = asm(src, t)
            tramps += code + bytes(-len(code) % 4)
            patch(site, asm(f"b.w {t:#x}", site), orig, f"M1 diag: {name} entry trampoline")
            if len(asm(f"b.w {t:#x}", site)) != 4:
                raise SystemExit(f"{name}: branch is not 4 bytes")
    # S3: poly_at's trampoline stays first and the poly cap's five stubs follow, as before the split, so the
    # 1.6.1 trampoline block is byte-identical; a build without poly_at starts the stubs where it was.
    if inc("poly_at"):
        with S.group("poly_at"):
            t = M7_TRAMP + len(tramps)
            head = asm(f"cmp r3, #{S.EV_VOICE_PRESSURE:#x}", t)
            head += bcond_w("ne", t + len(head), S.POLY_RESUME)          # not ours -> stock path
            poly_src = ("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
                        "mov r0, r6; add r1, sp, #0x40; mov r2, r8; "   # event was at sp+0x20; we pushed 0x20
                        f"bl {s7['h_poly']:#x}; "
                        "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
                        f"b.w {S.POLY_RESUME:#x}")
            code = head + asm(poly_src, t + len(head))
            tramps += code + bytes(-len(code) % 4)
            new_branch = bcond_w("ne", S.POLY_SITE, t)
            if bcond_w("ne", S.POLY_SITE, S.POLY_RESUME) != S.orig("POLY_SITE"):
                raise SystemExit("hand encoder does not reproduce the original bne.w")
            patch(S.POLY_SITE, new_branch, S.orig("POLY_SITE"), "M1 poly AT: External handler 0x0E -> h_poly")
    if inc("poly_cap"):
        with S.group("poly_cap"):
            for site_name, reg, role in NOTE_SITES:
                site = getattr(S, site_name)
                stub = M7_TRAMP + len(tramps)
                code = asm(f"mov r2, {reg}; b.w {s7['h_note']:#x}", stub)   # h_note(queue, ev, track); returns to site+4
                tramps += code + bytes(-len(code) % 4)
                orig = asm(f"bl {S.MSG_SEND:#x}", site)
                if bytes(base_img[site - BASE:site - BASE + 4]) != orig:
                    raise SystemExit(f"{site:#x}: not the expected bl msg_send")
                patch(site, asm(f"bl {stub:#x}", site), orig, f"M1b poly cap: {role} -> h_note")
    if inc("inta_live"):
        with S.group("inta_live"):
            if "inta_live" not in DISABLED:
                stub = M7_TRAMP + len(tramps)
                code = asm(f"mov r2, r5; b.w {s7['h_live']:#x}", stub)    # h_live(bus, ev, router); returns to site+4
                tramps += code + bytes(-len(code) % 4)
            orig = asm(f"bl {S.BUS_POST:#x}", S.LIVE_SITE)
            if bytes(base_img[S.LIVE_SITE - BASE:S.LIVE_SITE - BASE + 4]) != orig:
                raise SystemExit(f"{S.LIVE_SITE:#x}: not the expected bl bus_post")
            patch(S.LIVE_SITE, asm(f"bl {stub:#x}", S.LIVE_SITE) if "inta_live" not in DISABLED else orig, orig,
                  "Internal AT: pad router pressure post -> h_live (AT Out on internal tracks)")
    if inc("retrig"):
        with S.group("retrig"):
            # Retrigger (m7_retrig.c): accept = stub around stock's bl note_route;
            # emit = entry trampoline of the timed-note processor.
            stub = M7_TRAMP + len(tramps)
            # F5 (v3.59): Note Mod picks the sent note / pad first (nm_on(track, note, &pad, caller sp));
            # note_route and retrig_accept (bursts, curves) get the sent ones.
            code = asm("push {r3, r4, r5, r6, r7, lr}; mov r4, r0; mov r5, r1; mov r6, r2; mov r7, r3; "
                       "ldr r0, [sp, #24]; ldr r1, [sp, #28]; sub sp, #8; str r0, [sp]; str r1, [sp, #4]; "   # pad, router
                       f"mov r0, r4; mov r1, r5; mov r2, sp; add r3, sp, #32; bl {s7['nm_on']:#x}; mov r5, r0; "
                       f"mov r0, r4; mov r1, r5; mov r2, r6; mov r3, r7; bl {S.NOTE_ROUTE:#x}; "
                       "ldr.w r12, [sp]; str r6, [sp]; str.w r12, [sp, #4]; "                                # vel, sent pad
                       "mov r0, r4; mov r1, r5; add r2, sp, #32; ldr r3, [sp, #20]; "                        # caller sp; caller r6 (clock)
                       f"bl {s7['retrig_accept']:#x}; add sp, #8; pop {{r3, r4, r5, r6, r7, pc}}", stub)
            check_branches(code, stub, {s7["nm_on"], S.NOTE_ROUTE, s7["retrig_accept"]}, "retrigger accept stub")
            tramps += code + bytes(-len(code) % 4)
            orig = asm(f"bl {S.NOTE_ROUTE:#x}", S.RETRIG_ACCEPT_SITE)
            patch(S.RETRIG_ACCEPT_SITE, asm(f"bl {stub:#x}", S.RETRIG_ACCEPT_SITE), orig, "Retrigger: accepted note -> note_route, then queue the burst")
            t = M7_TRAMP + len(tramps)
            code = asm("push.w {r0, r1, r2, r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
                       "ldr r12, [sp, #32]; sub sp, #8; str r12, [sp]; "                                       # 5th argument (router)
                       f"bl {s7['retrig_emit']:#x}; add sp, #8; "
                       "pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0, r1, r2, r3, r12, lr}; "
                       f"{PUSH_R4_R11_LR}; b.w {S.RETRIG_EMIT_SITE + 4:#x}", t)
            check_branches(code, t, {s7["retrig_emit"], S.RETRIG_EMIT_SITE + 4}, "retrigger emit trampoline")
            tramps += code + bytes(-len(code) % 4)
            replays("RETRIG_EMIT_SITE", PUSH_R4_R11_LR)
            patch(S.RETRIG_EMIT_SITE, asm(f"b.w {t:#x}", S.RETRIG_EMIT_SITE), S.orig("RETRIG_EMIT_SITE"),
                  "Retrigger: timed-note processor entry -> post due hits")
    if inc("nmod"):
        with S.group("nmod"):
            # F5 Note Mod: stock's sequencer Note Off (two call sites) goes out as the sent pitch;
            # stock's stop / all-notes-off ends the shifted notes first.
            for site_name in NM_OFF_SITES:
                site = getattr(S, site_name)
                stub = M7_TRAMP + len(tramps)
                code = asm(f"push {{r0, r1, r2, r3, r12, lr}}; mov r0, sp; bl {s7['nm_off']:#x}; "
                           f"pop {{r0, r1, r2, r3, r12, lr}}; b.w {S.NM_NOTE_OFF:#x}", stub)
                check_branches(code, stub, {s7["nm_off"], S.NM_NOTE_OFF}, "Note Mod note-off stub")
                tramps += code + bytes(-len(code) % 4)
                orig = asm(f"bl {S.NM_NOTE_OFF:#x}", site)
                if bytes(base_img[site - BASE:site - BASE + 4]) != orig:
                    raise SystemExit(f"{site:#x}: not the expected bl to stock's Note Off")
                patch(site, asm(f"bl {stub:#x}", site), orig, "Note Mod: sequencer Note Off -> the sent pitch")
            t = M7_TRAMP + len(tramps)
            code = asm(f"push {{r0, r1, r2, r3, r12, lr}}; bl {s7['nm_all_off']:#x}; pop {{r0, r1, r2, r3, r12, lr}}; "
                       f"{PUSH_R4_R11_LR}; b.w {S.NM_ALL_OFF + 4:#x}", t)
            check_branches(code, t, {s7["nm_all_off"], S.NM_ALL_OFF + 4}, "Note Mod all-off trampoline")
            tramps += code + bytes(-len(code) % 4)
            replays("NM_ALL_OFF", PUSH_R4_R11_LR)
            patch(S.NM_ALL_OFF, asm(f"b.w {t:#x}", S.NM_ALL_OFF), S.orig("NM_ALL_OFF"),
                  "Note Mod: stop / all-notes-off ends the shifted notes first")
    if inc("stall"):
        with S.group("stall"):
            # Audio stall snapshot: the main audio loop's two calls (see m7_stall.c).
            for site_name, target_name, fn, why in STALL_SITES:
                site, target = getattr(S, site_name), getattr(S, target_name)
                stub = M7_TRAMP + len(tramps)
                code = asm(f"push {{r0, lr}}; bl {s7[fn]:#x}; pop {{r0, lr}}; b.w {target:#x}", stub)
                check_branches(code, stub, {s7[fn], target}, f"stall stub {fn}")
                tramps += code + bytes(-len(code) % 4)
                orig = asm(f"bl {target:#x}", site)
                if bytes(base_img[site - BASE:site - BASE + 4]) != orig:
                    raise SystemExit(f"{site:#x}: not the expected bl {target:#x}")
                patch(site, asm(f"bl {stub:#x}", site), orig, why)
    if inc("cond_engine"):
        with S.group("cond_engine"):
            # Trigger conditions (Lab T2 trampolines): save r0-r12, lr, flags (64 B).
            save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
            restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
            t = M7_TRAMP + len(tramps)
            gate = asm(save + f"mov r0, r8; ldrb.w r1, [sp, #105]; ldr r2, [sp, #124]; bl {s7['cond_decide']:#x}; "   # value sp+0x29, word sp+0x3C
                       "cmp r0, #0; beq skip; cmp r0, #1; beq play; "
                       + restore + f"ldrb.w r3, [sp, #0x29]; b.w {S.COND_GATE_SITE + 4:#x}; "         # native probability
                       "play: " + restore + f"movs r3, #0; b.w {S.COND_GATE_SITE + 4:#x}; "          # always
                       "skip: " + restore + f"b.w {S.COND_SKIP:#x}", t)
            check_branches(gate, t, {S.COND_GATE_SITE + 4, S.COND_SKIP, s7["cond_decide"]}, "condition gate")
            tramps += gate + bytes(-len(gate) % 4)
            replays("COND_GATE_SITE", "ldrb.w r3, [sp, #0x29]")
            patch(S.COND_GATE_SITE, asm(f"b.w {t:#x}", S.COND_GATE_SITE), S.orig("COND_GATE_SITE"),
                  "Conditions (Lab T2): probability gate -> cond_decide")
            t = M7_TRAMP + len(tramps)
            loop = asm(save + f"ldr r0, [sp, #8]; bl {s7['cond_loop']:#x}; " + restore     # r0 = the sequence
                       + f"{PUSH_R4_R9_LR}; b.w {S.COND_LOOP_SITE + 4:#x}", t)
            check_branches(loop, t, {S.COND_LOOP_SITE + 4, s7["cond_loop"]}, "condition loop hook")
            tramps += loop + bytes(-len(loop) % 4)
            replays("COND_LOOP_SITE", PUSH_R4_R9_LR)
            patch(S.COND_LOOP_SITE, asm(f"b.w {t:#x}", S.COND_LOOP_SITE), S.orig("COND_LOOP_SITE"),
                  "Conditions (Lab T2): sequence loop boundary -> cond_loop")
    if inc("stall"):
        with S.group("stall"):
            # Stall profile (v3.17): the M7 SysTick handler is a lone `b.w HAL_IncTick`;
            # sample the interrupted frame first (m7_stall.c prof_sample).
            t = M7_TRAMP + len(tramps)
            # The M7 is bare-metal and always on MSP (SVC/PendSV are default stubs), so the
            # frame is at MSP. Keystone cannot assemble `mrs r0, msp`: emit F3EF 8008.
            tick = bytes.fromhex("eff30880") + asm(
                f"push {{r0, lr}}; bl {s7['prof_sample']:#x}; pop {{r0, lr}}; b.w {S.HAL_INCTICK:#x}", t + 4)
            check_branches(tick, t, {s7["prof_sample"], S.HAL_INCTICK}, "SysTick profile hook")
            tramps += tick + bytes(-len(tick) % 4)
            orig = asm(f"b.w {S.HAL_INCTICK:#x}", S.SYSTICK_HANDLER)
            if bytes(base_img[S.SYSTICK_HANDLER - BASE:S.SYSTICK_HANDLER - BASE + 4]) != orig:
                raise SystemExit("SysTick handler is not the expected b.w HAL_IncTick")
            patch(S.SYSTICK_HANDLER, asm(f"b.w {t:#x}", S.SYSTICK_HANDLER), orig,
                  "Stall profile: M7 SysTick samples the interrupted PC/LR while audio is stalled")
    if M7_TRAMP + len(tramps) > M7_CODE:
        raise SystemExit("trampolines overrun into M7 code")
    patch(M7_TRAMP, tramps, bytes(len(tramps)), "M1 diag: M7 trampolines")
    patch(M7_CODE, code7, bytes(len(code7)), "M1 diag: M7 capture code")

    # --- M4: compile, append, redirect the defaultTask-loop BL
    m4_elf = compile_elf([HERE / "m4_diag.c"], "cortex_m4", HERE / "m4.ld", out / "m4.elf", M4_CODE, M4_LIMIT)
    va4, code4, s4 = elf_load(m4_elf, M4_CODE, M4_LIMIT)
    if va4 != M4_CODE or M4_CODE + len(code4) > M4_LIMIT:
        raise SystemExit(f"M4 code placement {va4:#x}+{len(code4)} outside our slice")
    # Append: pad from stock's end to the end of our slice with 0xFF (erased
    # flash); the reversal check deletes this tail again.
    stock_end = BASE + len(img)
    if stock_end > S.APPEND_FROM:
        raise SystemExit("base image already reaches our append area")
    extend(M4_LIMIT, "M1: extend image with erased (0xFF) flash for appended M4 code")
    patch(M4_CODE, code4 + MARKER, None, "M1 diag: M4 black-box code (appended)")
    stub = (M4_CODE + len(code4) + len(MARKER) + 3) & ~3
    sup = b""
    if inc("irq30"):
        with S.group("irq30"):
            # M4 main-task watchdog profile (v3.18): the IRQ30 handler's `bl` to stock's
            # heartbeat supervisor goes through a stub that first calls main_prof(msp, psp).
            # Keystone cannot assemble `mrs r1, psp`: emit F3EF 8109.
            sup = asm("mov r0, sp", stub) + bytes.fromhex("eff30981")
            sup += asm(f"push {{r3, lr}}; bl {s4['main_prof']:#x}; pop {{r3, lr}}; b.w {S.M4_WD_SUPERVISOR:#x}", stub + len(sup))
            check_branches(sup, stub, {s4["main_prof"], S.M4_WD_SUPERVISOR}, "M4 watchdog profile stub")
            if stub + len(sup) > M4_LIMIT:
                raise SystemExit("M4 watchdog profile stub overruns the black-box block")
            patch(stub, sup, None, "M4 watchdog profile: stub before the heartbeat supervisor")
            orig = asm(f"bl {S.M4_WD_SUPERVISOR:#x}", S.M4_WD_SITE)
            if bytes(base_img[S.M4_WD_SITE - BASE:S.M4_WD_SITE - BASE + 4]) != orig:
                raise SystemExit(f"{S.M4_WD_SITE:#x}: not the expected bl to the heartbeat supervisor")
            patch(S.M4_WD_SITE, asm(f"bl {stub:#x}", S.M4_WD_SITE), orig,
                  "M4 watchdog profile: IRQ30 -> main_prof, then stock's heartbeat supervisor")
    with S.group("bbox"):
        # Diagnostics dump (v3.22): our only card access, right after stock's own
        # project-file write, from the same task (FUN_0813032C -> FUN_08130958).
        dstub = (stub + len(sup) + 3) & ~3
        dump = asm(f"push {{r3, r4, r5, lr}}; mov r5, r0; bl {S.FILE_WRITER:#x}; mov r4, r0; "   # r0 = the file's path
                   f"mov r0, r5; bl {s4['dump_after_save']:#x}; mov r0, r4; pop {{r3, r4, r5, pc}}", dstub)
        check_branches(dump, dstub, {S.FILE_WRITER, s4["dump_after_save"]}, "diagnostics dump stub")
        if dstub + len(dump) > M4_LIMIT:
            raise SystemExit("diagnostics dump stub overruns the black-box block")
        patch(dstub, dump, None, "Diag dump: stub around stock's project-file write")
        orig = asm(f"bl {S.FILE_WRITER:#x}", S.SAVE_WRITE_SITE)
        if bytes(base_img[S.SAVE_WRITE_SITE - BASE:S.SAVE_WRITE_SITE - BASE + 4]) != orig:
            raise SystemExit(f"{S.SAVE_WRITE_SITE:#x}: not the expected bl to stock's file writer")
        patch(S.SAVE_WRITE_SITE, asm(f"bl {dstub:#x}", S.SAVE_WRITE_SITE), orig,
              "Diag dump: project save writes the file, then the black box appends its log")

    if inc("cond_ui"):
        with S.group("cond_ui"):
            # --- Probability editor conversions (Lab T2 sites, clamp only since v3.3)
            ui = bytearray(); ui_syms = {}
            for name, src, back in cond_ui_helpers():
                while len(ui) % 4:
                    ui.append(0)
                a = COND_UI + len(ui)
                code = asm(src, a)
                check_branches(code, a, {back}, f"condition UI helper {name}")
                ui_syms[name] = a
                ui += code
            if COND_UI + len(ui) > COND_UI_LIMIT:
                raise SystemExit("probability conversion helpers overrun their block")
            patch(COND_UI, bytes(ui), b"\xff" * len(ui), "Probability editor conversions (clamp legacy condition values)")
            # each helper re-executes (or re-implements) what it takes over: check that against this base
            replays("PROB_SHOW_PIANO_SITE", "subs r3, #1; str r3, [r6]")      # show(): store register r6
            replays("PROB_SHOW_DRUM_SITE", "subs r3, #1; str r3, [r1]")       # show(): store register r1
            replays("PROB_PIANO_REF_SITE", "ldrb.w r3, [sp, #0x25]")
            replaced(base_img, "PROB_PIANO_REF_SITE", "PROB_PIANO_REF_BACK", "PROB_PIANO_REF_SHA")
            replays("PROB_PIANO_EDIT_SITE", "ldrb.w r3, [sp, #0x19]")
            replaced(base_img, "PROB_PIANO_EDIT_SITE", "PROB_PIANO_EDIT_BACK", "PROB_PIANO_EDIT_SHA")   # [sp,#0x84], [sp,#0x31]
            for site_name, name in COND_UI_SITES:
                site = getattr(S, site_name)
                patch(site, asm(f"b.w {ui_syms[name]:#x}", site), S.orig(site_name), f"Probability: {name} clamp")

    if inc("step"):
        with S.group("step"):
            # --- Inspect a step: encoder-edit wrappers at HOLD_WRAP, C at HOLD_CODE
            hold_elf = compile_elf(sources("m4_holdstep"), "cortex_m4", HERE / "m4_holdstep.ld", out / "m4_holdstep.elf", HOLD_CODE, HOLD_LIMIT)
            vah, codeh, sh = elf_load(hold_elf, HOLD_CODE, HOLD_LIMIT)
            if vah != HOLD_CODE:
                raise SystemExit(f"hold-step code placement {vah:#x}")
            extend(HOLD_LIMIT, "Inspect a step: extend image with erased (0xFF) flash")
            # (the three edit sites are wrapped in the per-step block: protection + defaults/locks)
            hold_marker = b"BENTO_MOD:step-edit-keeps-step(from Lab T2)\0"
            if HOLD_CODE + len(codeh) + len(hold_marker) > HOLD_LIMIT:
                raise SystemExit("hold-step code overruns its slice")
            patch(HOLD_CODE, codeh + hold_marker, b"\xff" * (len(codeh) + len(hold_marker)), "Inspect a step (Lab T2): code")
            s4.update({f"hold.{k}": v for k, v in sh.items()})

    if inc("hold"):
        with S.group("hold"):
            hold_shortcuts(base_img, img, patch, extend, sources, s4, out)

    if inc("step"):
        with S.group("step"):
            per_step(base_img, img, patch, extend, sources, s4, sh, out)

    if M4_CODE + len(code4) + 20 > M4_LIMIT:
        raise SystemExit("M4 code + marker overrun")
    if M4_CODE + len(code4) + len(MARKER) > M4_LIMIT:
        raise SystemExit("M4 diag code + marker overruns its slice")
    if inc("poly_at"):
        with S.group("poly_at"):
            if BASE + len(img) < M4_POLY:                # a build without the per-step block (S2: never on 1.6.1)
                extend(M4_POLY, "M1 poly AT: extend image with erased (0xFF) flash up to the M4 handler block")
            m4_poly_handlers(base_img, img, patch, extend)
    with S.group("bbox"):
        bl_check = asm(f"bl {S.M4_BL_TARGET:#x}", S.M4_BL_SITE)
        if bl_check != S.orig("M4_BL_SITE"):
            raise SystemExit("assembler disagrees with the original BL encoding")
        patch(S.M4_BL_SITE, asm(f"bl {s4['m4_tick']:#x}", S.M4_BL_SITE), S.orig("M4_BL_SITE"),
              "M1 diag: defaultTask loop BL -> m4_tick (calls the original first)")

    # --- visible version label
    if not 1 <= len(DISPLAY) <= 6 or not DISPLAY.isascii():
        raise SystemExit(f"display label {DISPLAY!r}: the boot screen shows at most 6 ASCII characters")
    patch(S.VERSION_STR, DISPLAY.encode().ljust(8, b"\0"), S.orig("VERSION_STR"), f"Visible version label {DISPLAY}")

    # --- invariants
    if len(img) > PROVEN_MAX_SIZE or BASE + len(img) > FORBIDDEN_FROM:
        raise SystemExit(f"image of {len(img):#x} bytes exceeds the size proven on the device ({PROVEN_MAX_SIZE:#x})")
    if len(img) % 4:
        raise SystemExit("image not word aligned (a misaligned image fails to program)")
    if any(w["address"] + w["size"] > FORBIDDEN_FROM for w in writes):
        raise SystemExit(f"a write reaches {FORBIDDEN_FROM:#x}")
    undo = bytearray(img)
    for w in reversed(writes):
        o = w["address"] - BASE
        if w["original_hex"]:
            undo[o:o + w["size"]] = bytes.fromhex(w["original_hex"])
        else:
            del undo[o:o + w["size"]]
    if bytes(undo) != base_img:
        raise SystemExit("reversal does not reproduce the stock base")
    # build.py read only what GROUP_NAMES declares for each group (plan() refuses from those lists)
    for g, names in S.read.items():
        extra = sorted(names - set(GROUP_NAMES[g]))
        if extra:
            raise SystemExit(f"build.py read {extra} while building {g}: add them to GROUP_NAMES[{g!r}]")

    (out / "bento1.bin").write_bytes(img)
    shown = dict(LAYOUT)
    if omitted:                                  # S2: only the blocks this image has, and where it really ends
        for g, keys in (("hold", ("HOLD_WRAP", "HOLD_CODE", "CTRL_WRAP", "CTRL_CODE")), ("step", ("STEP_WRAP", "STEP_CODE")),
                        ("poly_at", ("M4_POLY",))):
            if g in omitted:
                for k in keys:
                    shown.pop(k)
        shown["IMAGE_END"] = BASE + len(img)
    man = dict(label=f"{DISPLAY} (bento_enhanced v{VERSION} on stock {S.version})", version=VERSION, display=DISPLAY, disabled=sorted(DISABLED), layout={k: hex(v) for k, v in shown.items()}, base=S.module, base_label=S.label, base_sha256=S.sha256, sha256=hashlib.sha256(img).hexdigest(),
               m7_symbols={k: hex(v) for k, v in s7.items()}, m4_symbols={k: hex(v) for k, v in s4.items()},
               m7_code_bytes=len(code7), m4_code_bytes=len(code4), trampoline_bytes=len(tramps), writes=writes)
    if omitted:                                  # S2: groups left out of the image (not only unpatched)
        man["omitted"] = sorted(omitted)
        man["uses"] = {g: sorted(n for n in needs[g] if s.syms.get(n)) for g in sorted(included)}
    (out / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
    print(f"built {man['label']} sha256={man['sha256']}")
    if omitted:
        print(f"left out of the image (not ported to {S.label} yet): {sorted(omitted)}")
    if skipped:
        print(f"disabled groups {sorted(DISABLED)}; {len(skipped)} stock sites left unpatched:")
        for a, why in skipped:
            print(f"  {a:#x} {why}")
    print(f"  M7 code {len(code7)} B at {M7_CODE:#x}, trampolines {len(tramps)} B; M4 code {len(code4)} B at {M4_CODE:#x}")
    print(f"  image {len(img):#x} bytes (proven max {PROVEN_MAX_SIZE:#x}); {len(writes)} writes, reversal to stock verified")
    return img, man


def hold_shortcuts(base_img, img, patch, extend, sources, s4, out):
    """Hold shortcuts (Lab K16 port): M7 pad input + startup, M4 consumer/observer/mixer render."""
    syms = ctrl_symbols()
    def template(path, addr):
        src = "\n".join(x.split("//")[0] for x in path.read_text().splitlines())
        for k, v in {**syms, **S.asm_symbols()}.items():
            src = src.replace(f"@{k}@", v)
        if "@" in src:
            raise SystemExit(f"{path.name}: unresolved template symbol")
        return asm(src, addr)
    code = template(HERE / "m7_controls_input.s", CTRL_INPUT)
    if CTRL_INPUT + len(code) > CTRL_STARTUP:
        raise SystemExit("controls input code overruns its slice")
    patch(CTRL_INPUT, code, bytes(len(code)), "Hold shortcuts (Lab K16): M7 pad input hook")
    patch(S.CTRL_INPUT_SITE, asm(f"b.w {CTRL_INPUT:#x}", S.CTRL_INPUT_SITE), S.orig("CTRL_INPUT_SITE"),
          "Hold shortcuts (Lab K16): pad receiver -> input hook")
    lit = [struct.unpack_from("<I", base_img, ((a + 4) & ~3) + (struct.unpack_from("<H", base_img, a - BASE)[0] & 0xFF) * 4 - BASE)[0]
           for a in (S.CTRL_STARTUP_SITE, S.CTRL_STARTUP_SITE + 2)]
    startup = asm(
        f"push.w {{r0-r12, lr}}; ldr r0, ={CTRL_STATE:#x}; movs r1, #0; movs r2, #8; "
        "zero: str r1, [r0], #4; subs r2, #1; bne zero; "
        f"ldr r0, ={CTRL_STATE:#x}; movs r1, #1; strb r1, [r0, #12]; movs r1, #0; "
        "map: add.w r2, r0, #16; strb r1, [r2, r1]; adds r1, #1; cmp r1, #16; bne map; "
        f"ldr r1, ={syms['STATE_MAGIC']}; dsb; str r1, [r0]; pop.w {{r0-r12, lr}}; "
        f"ldr r0, ={lit[0]:#x}; ldr r1, ={lit[1]:#x}; "                       # the two displaced literal loads
        f"push {{r0, r1}}; ldr r0, ={(S.CTRL_STARTUP_SITE + 4) | 1:#x}; str r0, [sp, #4]; pop {{r0, pc}}", CTRL_STARTUP)
    if tuple(lit) != S.CTRL_STARTUP_LITS or CTRL_STARTUP + len(startup) > CTRL_STARTUP + 0x100:
        raise SystemExit("controls startup: unexpected literals or size")
    patch(CTRL_STARTUP, startup, bytes(len(startup)), "Hold shortcuts (Lab K16): M7 startup inits only its own state line")
    patch(S.CTRL_STARTUP_SITE, asm(f"b.w {CTRL_STARTUP:#x}", S.CTRL_STARTUP_SITE), S.orig("CTRL_STARTUP_SITE"),
          "Hold shortcuts (Lab K16): M7 startup -> state init")

    ctl_elf = compile_elf(sources("m4_controls"), "cortex_m4", HERE / "m4_controls.ld", out / "m4_controls.elf", CTRL_CODE, CTRL_LIMIT)
    vac, codec, sc = elf_load(ctl_elf, CTRL_CODE, CTRL_LIMIT)
    if vac != CTRL_CODE:
        raise SystemExit(f"controls code placement {vac:#x}")
    extend(CTRL_LIMIT, "Hold shortcuts: extend image with erased (0xFF) flash")
    consumer = asm(
        "push.w {r0-r12, lr}; sub sp, #8; add.w r0, sp, #608; str r0, [sp]; "   # record (orig sp+0x220)
        f"mov r0, r4; mov r1, r5; mov r2, r6; mov r3, r7; bl {sc['controls_dispatch']:#x}; "
        "cmp r0, #0; add sp, #8; pop.w {r0-r12, lr}; bne consumed; "
        f"sub.w r3, r5, #17; b.w {S.CTRL_CONSUMER_SITE + 4:#x}; "                    # displaced instruction
        f"consumed: b.w {S.CTRL_DISPATCH_NEXT:#x}", CTRL_WRAP)
    check_branches(consumer, CTRL_WRAP, {sc["controls_dispatch"], S.CTRL_CONSUMER_SITE + 4, S.CTRL_DISPATCH_NEXT}, "controls consumer")
    replays("CTRL_CONSUMER_SITE", "sub.w r3, r5, #17")
    sel_at = CTRL_WRAP + 0x80
    selection = asm(
        "push.w {r0-r3, r12, lr}; mrs r12, apsr; push.w {r12, lr}; "
        f"ldr r3, ={INST_HOLD:#x}; ldr r0, [r3]; cmp r0, #0; beq replay; cmp r2, #15; bhi replay; "
        "movs r0, #1; str r0, [r3, #20]; "                                        # INST->picked
        "replay: pop.w {r12, lr}; msr apsr_nzcvq, r12; pop.w {r0-r3, r12, lr}; "
        f"push {{r4-r7, lr}}; sub sp, #36; b.w {S.CTRL_SELECTION_SITE + 4:#x}", sel_at)
    check_branches(selection, sel_at, {S.CTRL_SELECTION_SITE + 4}, "INST selection observer")
    replays("CTRL_SELECTION_SITE", "push {r4-r7, lr}; sub sp, #36")
    if len(consumer) > 0x80 or sel_at + len(selection) > CTRL_CODE:
        raise SystemExit("controls wrappers overrun")
    patch(CTRL_WRAP, consumer, b"\xff" * len(consumer), "Hold shortcuts (Lab K16): UI consumer wrapper")
    patch(sel_at, selection, b"\xff" * len(selection), "Hold shortcuts (Lab K16): INST selection observer")
    ctl_marker = b"BENTO_MOD:hold-shortcuts(Lab K16 port)\0"
    if CTRL_CODE + len(codec) + len(ctl_marker) > CTRL_LIMIT:
        raise SystemExit("controls code overruns its slice")
    patch(CTRL_CODE, codec + ctl_marker, b"\xff" * (len(codec) + len(ctl_marker)), "Hold shortcuts (Lab K16): UI code")
    patch(S.CTRL_CONSUMER_SITE, asm(f"b.w {CTRL_WRAP:#x}", S.CTRL_CONSUMER_SITE), S.orig("CTRL_CONSUMER_SITE"),
          "Hold shortcuts (Lab K16): UI event consumer -> controls_dispatch")
    patch(S.CTRL_SELECTION_SITE, asm(f"b.w {sel_at:#x}", S.CTRL_SELECTION_SITE), S.orig("CTRL_SELECTION_SITE"),
          "Hold shortcuts (Lab K16): drum cell selection observer")
    if S.orig("MIXER_RENDER_VT_SLOT") != struct.pack("<I", S.MIXER_RENDER | 1):
        raise SystemExit("mixer vtable slot does not hold stock's render method")
    patch(S.MIXER_RENDER_VT_SLOT, struct.pack("<I", sc["mixer_render"] | 1), S.orig("MIXER_RENDER_VT_SLOT"),
          "Hold shortcuts (Lab K16): mixer render shows Solo/Mute while MIXER is held")
    s4.update({f"controls.{k}": v for k, v in sc.items()})


def per_step(base_img, img, patch, extend, sources, s4, sh, out):
    """Per-step Condition / Retrigger UI, strike velocity, curves, Mod Matrix rows (m4_step.c)."""
    st_elf = compile_elf(sources("m4_step"), "cortex_m4", HERE / "m4_step.ld", out / "m4_step.elf", STEP_CODE, STEP_LIMIT)
    vas, codes, ss = elf_load(st_elf, STEP_CODE, STEP_LIMIT)
    if vas != STEP_CODE:
        raise SystemExit(f"step code placement {vas:#x}")
    extend(STEP_LIMIT, "Per-step UI: extend image with erased (0xFF) flash")
    wrap = bytearray()
    def sblock(src, exits, what):
        while len(wrap) % 4:
            wrap.append(0)
        a = STEP_WRAP + len(wrap)
        code = asm(src, a)
        check_branches(code, a, exits, what)
        wrap.extend(code)
        return a
    save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
    restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
    # project save / load: node in r4, record on the caller's stack (+64 after our save)
    a = sblock(save + f"mov r0, r4; add r1, sp, #72; bl {ss['step_save']:#x}; " + restore
               + f"ldrb.w r3, [sp, #8]; b.w {S.STEP_SAVE_SITE + 4:#x}", {ss["step_save"], S.STEP_SAVE_SITE + 4}, "step save")
    replays("STEP_SAVE_SITE", "ldrb.w r3, [sp, #8]")
    patch(S.STEP_SAVE_SITE, asm(f"b.w {a:#x}", S.STEP_SAVE_SITE), S.orig("STEP_SAVE_SITE"), "Per-step: note XML save -> be_step_v1")
    a = sblock(save + f"mov r0, r4; add r1, sp, #100; bl {ss['step_load']:#x}; " + restore
               + f"add r1, sp, #0x24; ldr r0, [sp, #0x3c]; b.w {S.STEP_LOAD_SITE + 4:#x}", {ss["step_load"], S.STEP_LOAD_SITE + 4}, "step load")
    replays("STEP_LOAD_SITE", "add r1, sp, #0x24; ldr r0, [sp, #0x3c]")
    patch(S.STEP_LOAD_SITE, asm(f"b.w {a:#x}", S.STEP_LOAD_SITE), S.orig("STEP_LOAD_SITE"),
          "Per-step: note XML load -> be_step_v1 + migrate conditions out of the probability byte")
    # drum step sequencer header: before/after stock's builder
    replays("DRUM_HEADER", PUSH_R4_R9_LR)
    orig_drum = sblock(f"{PUSH_R4_R9_LR}; b.w {S.DRUM_HEADER + 4:#x}", {S.DRUM_HEADER + 4}, "drum header replay")
    a = sblock(f"push {{r4, lr}}; mov r4, r0; bl {ss['step_header_before']:#x}; mov r0, r4; bl {orig_drum:#x}; "
               f"mov r0, r4; bl {ss['step_header_after']:#x}; pop {{r4, pc}}",
               {ss["step_header_before"], orig_drum, ss["step_header_after"]}, "drum header")
    patch(S.DRUM_HEADER, asm(f"b.w {a:#x}", S.DRUM_HEADER), S.orig("DRUM_HEADER"), "Per-step: drum step header -> encoders 5-8")
    # drum encoder events (view, key, value)
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; ldr r2, [sp, #16]; bl {ss['step_encoder']:#x}; "
               "cmp r0, #0; beq stock; " + restore + "movs r0, #0; bx lr; "
               "stock: " + restore + f"add.w r3, r0, #0x11000; b.w {S.DRUM_ENCODER + 4:#x}",
               {ss["step_encoder"], S.DRUM_ENCODER + 4}, "drum encoder")
    replays("DRUM_ENCODER", "add.w r3, r0, #0x11000")
    patch(S.DRUM_ENCODER, asm(f"b.w {a:#x}", S.DRUM_ENCODER), S.orig("DRUM_ENCODER"), "Per-step: drum encoder events -> ours first")
    # piano roll header (after stock's builder) and events
    replays("PIANO_HEADER", PUSH_R4_R11_LR)
    orig_piano = sblock(f"{PUSH_R4_R11_LR}; b.w {S.PIANO_HEADER + 4:#x}", {S.PIANO_HEADER + 4}, "piano header replay")
    a = sblock(f"push {{r4, lr}}; mov r4, r0; bl {orig_piano:#x}; mov r0, r4; bl {ss['piano_header_after']:#x}; pop {{r4, pc}}",
               {orig_piano, ss["piano_header_after"]}, "piano header")
    patch(S.PIANO_HEADER, asm(f"b.w {a:#x}", S.PIANO_HEADER), S.orig("PIANO_HEADER"), "Per-step: piano header -> Condition on encoder 7")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['piano_event']:#x}; "
               "cmp r0, #0; beq stock; " + restore + "movs r0, #0; bx lr; "
               "stock: " + restore + f"push {{r4, r5, r6, r7, lr}}; sub sp, #0x174; b.w {S.PIANO_EVENT + 4:#x}",
               {ss["piano_event"], S.PIANO_EVENT + 4}, "piano event")
    replays("PIANO_EVENT", "push {r4, r5, r6, r7, lr}; sub sp, #0x174")
    patch(S.PIANO_EVENT, asm(f"b.w {a:#x}", S.PIANO_EVENT), S.orig("PIANO_EVENT"), "Per-step: piano events -> ours first")
    # stock's velocity / probability / offset edits (view, value): keep the step on
    # release (hold-step), then defaults + locks around stock's own edit
    for f, site_name in enumerate(HOLD_EDIT_SITES):
        site = getattr(S, site_name)
        replays(site_name, PUSH_R4_R11_LR)
        orig_edit = sblock(f"{PUSH_R4_R11_LR}; b.w {site + 4:#x}", {site + 4}, "edit replay")
        a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {sh['step_edit']:#x}; "
                   f"mov r0, r4; movs r1, #{f}; bl {ss['param_pre']:#x}; mov r6, r0; "
                   f"mov r0, r4; mov r1, r5; bl {orig_edit:#x}; "
                   f"mov r0, r4; movs r1, #{f}; mov r2, r6; bl {ss['param_post']:#x}; pop {{r4, r5, r6, pc}}",
                   {sh["step_edit"], ss["param_pre"], orig_edit, ss["param_post"]}, "edit wrapper")
        patch(site, asm(f"b.w {a:#x}", site), S.orig(site_name),
              f"Step edit {('velocity', 'probability', 'offset')[f]}: keep step on release + defaults/locks")
    # strike velocity: pad press (view, pad) and step toggle (view, idx)
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['strike_press']:#x}; " + restore
               + f"cmp r1, #15; bls stock; b.w {S.STEP_PRESS_SKIP:#x}; stock: b.w {S.STEP_PRESS + 4:#x}",
               {ss["strike_press"], S.STEP_PRESS_SKIP, S.STEP_PRESS + 4}, "strike press")
    if bytes(base_img[S.STEP_PRESS - BASE:S.STEP_PRESS - BASE + 4]) != asm(f"cmp r1, #15; bhi {S.STEP_PRESS_SKIP:#x}", S.STEP_PRESS):
        raise SystemExit("step press entry is not the expected cmp/bhi")
    patch(S.STEP_PRESS, asm(f"b.w {a:#x}", S.STEP_PRESS), S.orig("STEP_PRESS"), "Strike velocity: drum step pad press")
    replays("STEP_TOGGLE", "push {r3, r4, r5, lr}; cmp r1, #0x1f")
    orig_toggle = sblock(f"push {{r3, r4, r5, lr}}; cmp r1, #0x1f; b.w {S.STEP_TOGGLE + 4:#x}", {S.STEP_TOGGLE + 4}, "toggle replay")
    a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {ss['toggle_pre']:#x}; mov r6, r0; "
               f"mov r0, r4; mov r1, r5; bl {orig_toggle:#x}; "
               f"mov r0, r4; mov r1, r5; mov r2, r6; bl {ss['toggle_post']:#x}; pop {{r4, r5, r6, pc}}",
               {ss["toggle_pre"], orig_toggle, ss["toggle_post"]}, "toggle wrapper")
    patch(S.STEP_TOGGLE, asm(f"b.w {a:#x}", S.STEP_TOGGLE), S.orig("STEP_TOGGLE"), "Strike velocity: step toggle")
    # New-step defaults (v3.56): stock's add-step path copies view+0x114C0/C4/C8 into the
    # note it just made (0x0815D07C on); refreshes before that may have reset them.
    orig = asm("add.w r2, r4, #0x11000", S.STEP_DEFAULTS_READ)
    if bytes(base_img[S.STEP_DEFAULTS_READ - BASE:S.STEP_DEFAULTS_READ - BASE + 4]) != orig:
        raise SystemExit(f"{S.STEP_DEFAULTS_READ:#x}: not stock's new-step default read")
    a = sblock(save + f"ldr r0, [sp, #24]; bl {ss['toggle_defaults']:#x}; " + restore   # view (r4)
               + f"add.w r2, r4, #0x11000; b.w {S.STEP_DEFAULTS_READ + 4:#x}",
               {ss["toggle_defaults"], S.STEP_DEFAULTS_READ + 4}, "new-step defaults")
    patch(S.STEP_DEFAULTS_READ, asm(f"b.w {a:#x}", S.STEP_DEFAULTS_READ), orig, "Step page: a new step takes the pad's defaults")
    # "Strike Vel" header button: inject after stock's header generator; catch its action
    replays("HEADER_GEN", "push.w {r4, r5, r6, r7, r8, r9, r10, lr}")
    orig_gen = sblock(f"push.w {{r4, r5, r6, r7, r8, r9, r10, lr}}; b.w {S.HEADER_GEN + 4:#x}", {S.HEADER_GEN + 4}, "header replay")
    a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {orig_gen:#x}; mov r2, r0; mov r0, r4; mov r1, r5; "
               f"bl {ss['strike_toolbar']:#x}; pop {{r4, r5, r6, pc}}", {orig_gen, ss["strike_toolbar"]}, "header wrapper")
    patch(S.HEADER_GEN, asm(f"b.w {a:#x}", S.HEADER_GEN), S.orig("HEADER_GEN"), "Strike Vel: header button (slot left of Undo)")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['strike_event']:#x}; cmp r0, #0; beq stock; "
               + restore + "movs r0, #0; bx lr; stock: " + restore + f"push {{lr}}; sub sp, #0x6c; b.w {S.DRUM_EVENT + 4:#x}",
               {ss["strike_event"], S.DRUM_EVENT + 4}, "drum event")
    replays("DRUM_EVENT", "push {lr}; sub sp, #0x6c")
    patch(S.DRUM_EVENT, asm(f"b.w {a:#x}", S.DRUM_EVENT), S.orig("DRUM_EVENT"), "Strike Vel: button action in the drum view")
    # F2a: External INST page row sets: the bar builder (view) and the view's events (view, ev)
    a = sblock(save + f"ldr r0, [sp, #8]; bl {ss['ext_build']:#x}; cmp r0, #0; beq stock; " + restore + "bx lr; "
               "stock: " + restore + f"{PUSH_R4_R11_LR}; b.w {S.EXT_BUILD + 4:#x}",
               {ss["ext_build"], S.EXT_BUILD + 4}, "external bar builder")
    replays("EXT_BUILD", PUSH_R4_R11_LR)
    patch(S.EXT_BUILD, asm(f"b.w {a:#x}", S.EXT_BUILD), S.orig("EXT_BUILD"), "Per-step: External bar builder -> LFO/Env rows")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['ext_event']:#x}; cmp r0, #0; beq stock; "
               + restore + "movs r0, #0; bx lr; stock: " + restore + f"ldrh r3, [r1]; cmp r3, #{S.EV_ENCODER:#x}; b.w {S.EXT_EVENT + 4:#x}",
               {ss["ext_event"], S.EXT_EVENT + 4}, "external event")
    replays("EXT_EVENT", f"ldrh r3, [r1]; cmp r3, #{S.EV_ENCODER:#x}")
    patch(S.EXT_EVENT, asm(f"b.w {a:#x}", S.EXT_EVENT), S.orig("EXT_EVENT"), "Per-step: External view events -> row button + row encoders")
    # F2b: the Mod Matrix page for External tracks
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['mx_build']:#x}; cmp r0, #0; beq stock; " + restore + "bx lr; "
               "stock: " + restore + f"{PUSH_R4_R11_LR}; b.w {S.MX_BUILD + 4:#x}",
               {ss["mx_build"], S.MX_BUILD + 4}, "matrix rows")
    replays("MX_BUILD", PUSH_R4_R11_LR)
    patch(S.MX_BUILD, asm(f"b.w {a:#x}", S.MX_BUILD), S.orig("MX_BUILD"), "Per-step: Mod Matrix rows -> MIDI destinations (External)")
    a = sblock(save + f"ldr r0, [sp, #16]; ldr r1, [sp, #20]; ldr r2, [sp, #64]; bl {ss['mx_route']:#x}; "
               "cmn r0, #1; beq stock; str r0, [sp, #8]; " + restore + "bx lr; "
               "stock: " + restore + f"{PUSH_R4_R11_LR}; b.w {S.MX_ROUTE + 4:#x}",
               {ss["mx_route"], S.MX_ROUTE + 4}, "matrix routing read")
    replays("MX_ROUTE", PUSH_R4_R11_LR)
    patch(S.MX_ROUTE, asm(f"b.w {a:#x}", S.MX_ROUTE), S.orig("MX_ROUTE"), "Per-step: modulation routing read -> ours for External rows")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['mx_edit']:#x}; cmp r0, #0; beq stock; " + restore + "bx lr; "
               "stock: " + restore + f"{PUSH_R4_R11_LR}; b.w {S.MX_EDIT + 4:#x}",
               {ss["mx_edit"], S.MX_EDIT + 4}, "matrix edit")
    replays("MX_EDIT", PUSH_R4_R11_LR)
    patch(S.MX_EDIT, asm(f"b.w {a:#x}", S.MX_EDIT), S.orig("MX_EDIT"), "Per-step: Mod Matrix edits -> ours for External rows")
    replays("MX_BAR", PUSH_R4_R9_LR)
    orig_bar = sblock(f"{PUSH_R4_R9_LR}; b.w {S.MX_BAR + 4:#x}", {S.MX_BAR + 4}, "matrix bar replay")
    a = sblock(f"push {{r4, lr}}; mov r4, r0; bl {orig_bar:#x}; mov r0, r4; bl {ss['mx_bar_after']:#x}; pop {{r4, pc}}",
               {orig_bar, ss["mx_bar_after"]}, "matrix bar")
    patch(S.MX_BAR, asm(f"b.w {a:#x}", S.MX_BAR), S.orig("MX_BAR"), "Per-step: Mod Matrix bar -> Dest on encoder 8 (External)")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['mx_event']:#x}; cmp r0, #0; beq stock; "
               + restore + "movs r0, #0; bx lr; stock: " + restore + f"push {{r4, lr}}; sub sp, #0x18; b.w {S.MX_EVENT + 4:#x}",
               {ss["mx_event"], S.MX_EVENT + 4}, "matrix event")
    replays("MX_EVENT", "push {r4, lr}; sub sp, #0x18")
    patch(S.MX_EVENT, asm(f"b.w {a:#x}", S.MX_EVENT), S.orig("MX_EVENT"), "Per-step: Mod Matrix events -> Dest encoder (External)")
    # strike velocity for notes created by any view (drum Sequence page)
    a = sblock(save + f"ldr r0, [sp, #16]; ldr r1, [sp, #60]; ldr r2, [sp, #8]; ldr r3, [sp, #12]; bl {ss['strike_insert']:#x}; " + restore   # record, caller lr, model, sequence
               + f"push {{r4, r5, r6, lr}}; sub sp, #0x20; b.w {S.NOTE_INSERT + 4:#x}", {ss["strike_insert"], S.NOTE_INSERT + 4}, "note insert")
    replays("NOTE_INSERT", "push {r4, r5, r6, lr}; sub sp, #0x20")
    patch(S.NOTE_INSERT, asm(f"b.w {a:#x}", S.NOTE_INSERT), S.orig("NOTE_INSERT"), "Strike velocity: notes created by any view")
    # M2-1: melodic recording sets a note's length at note-off through this update
    a = sblock(save + f"ldr r0, [sp, #16]; ldr r1, [sp, #60]; bl {ss['curve_update']:#x}; " + restore   # record, caller lr
               + f"push {{r4, lr}}; sub sp, #8; b.w {S.NOTE_UPDATE + 4:#x}", {ss["curve_update"], S.NOTE_UPDATE + 4}, "note update")
    replays("NOTE_UPDATE", "push {r4, lr}; sub sp, #8")
    patch(S.NOTE_UPDATE, asm(f"b.w {a:#x}", S.NOTE_UPDATE), S.orig("NOTE_UPDATE"), "Curves: note update (recording note-off) -> curve_update")
    # M2-3b: remember which document stock is reading (path in r0)
    a = sblock(save + f"ldr r0, [sp, #8]; bl {ss['doc_begin']:#x}; " + restore
               + f"push.w {{r4, r5, r6, r7, r8, sb, sl, fp, lr}}; b.w {S.DOC_READ + 4:#x}", {ss["doc_begin"], S.DOC_READ + 4}, "document reader")
    replays("DOC_READ", "push.w {r4, r5, r6, r7, r8, sb, sl, fp, lr}")
    patch(S.DOC_READ, asm(f"b.w {a:#x}", S.DOC_READ), S.orig("DOC_READ"), "Curves: document reader -> remember the path (sidecar load)")
    # shorter tap/hold boundary for step inspection (Jeff: 400 ms)
    for site_name in ("HOLD_TAP_PAD", "HOLD_TAP_TOUCH"):
        site = getattr(S, site_name)
        replays(site_name, "cmp.w r3, #1000")
        patch(site, asm(f"cmp.w r3, #{HOLD_TAP_MS}", site), S.orig(site_name),
              f"Step inspection: hold > {HOLD_TAP_MS} ms (stock 1000) keeps the step on release")
    if STEP_WRAP + len(wrap) > STEP_CODE:
        raise SystemExit("step wrappers overlap the step code")
    patch(STEP_WRAP, bytes(wrap), b"\xff" * len(wrap), "Per-step: wrappers")
    step_marker = b"BENTO_MOD:per-step-condition-retrig\0"
    if STEP_CODE + len(codes) + len(step_marker) > STEP_LIMIT:
        raise SystemExit("step code overruns its slice")
    patch(STEP_CODE, codes + step_marker, b"\xff" * (len(codes) + len(step_marker)), "Per-step: UI code")
    s4.update({f"step.{k}": v for k, v in ss.items()})


def m4_poly_handlers(base_img, img, patch, extend):
    """Poly AT (M4): MIDI task jump-table slots 0x5F / 0x60 -> copies of stock's Note On case."""
    h = asm(f"mov r0, r5; bl {S.M4MIDI_BEGIN:#x}; "
            f"movs r1, #0xa0; mov r0, r5; bl {S.M4MIDI_STATUS:#x}; "           # Polyphonic Key Pressure
            f"uxtb.w r1, r8; mov r0, r5; bl {S.M4MIDI_CHANNEL:#x}; "             # channel (event word 3)
            f"uxtb r1, r7; mov r0, r5; bl {S.M4MIDI_DATA:#x}; "               # note (word 4)
            f"uxtb.w r1, r11; mov r0, r5; bl {S.M4MIDI_DATA:#x}; "            # pressure (word 5)
            f"uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl {S.M4MIDI_SEND:#x}; " # send on port (word 2)
            f"mov r0, r5; bl {S.M4MIDI_RESULT:#x}; mov r1, r0; mov r0, r6; bl {S.M4MIDI_DONE:#x}; "
            f"b.w {S.M4_LOOP:#x}", M4_POLY)
    if M4_POLY + len(h) > M4_POLY_LIMIT or BASE + len(img) != M4_POLY:
        raise SystemExit("M4 poly handler block layout")
    extend(M4_POLY_LIMIT, "M1 poly AT: extend image with erased (0xFF) flash for the M4 handler")
    # Cross-check: the native Note On case assembles to the same shape with 0x90.
    native = base_img[S.M4_NOTE_ON_CASE - BASE:S.M4_LOOP - BASE]
    ours_as_native = asm(h_src_native := (f"mov r0, r5; bl {S.M4MIDI_BEGIN:#x}; movs r1, #0x90; mov r0, r5; bl {S.M4MIDI_STATUS:#x}; "
            f"uxtb.w r1, r8; mov r0, r5; bl {S.M4MIDI_CHANNEL:#x}; uxtb r1, r7; mov r0, r5; bl {S.M4MIDI_DATA:#x}; "
            f"uxtb.w r1, r11; mov r0, r5; bl {S.M4MIDI_DATA:#x}; uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl {S.M4MIDI_SEND:#x}; "
            f"mov r0, r5; bl {S.M4MIDI_RESULT:#x}; mov r1, r0; mov r0, r6; bl {S.M4MIDI_DONE:#x}"), S.M4_NOTE_ON_CASE)
    if ours_as_native != native:
        raise SystemExit("our copy of the Note On case does not match the native bytes")
    patch(M4_POLY, h, b"\xff" * len(h), "M1 poly AT: M4 0x5F handler (Note On case copy, status 0xA0)")
    old = struct.pack("<I", S.M4_LOOP | 1)                     # unused slots point at the loop
    jt_5f = S.MIDI_JT + 4 * (S.EV_MIDI_POLY_AT - S.MIDI_JT_FIRST)
    if S.EV_MIDI_CHAN_AT != S.EV_MIDI_POLY_AT + 1:
        raise SystemExit("the channel pressure handler's slot must follow the poly pressure slot")
    patch(jt_5f, struct.pack("<I", M4_POLY | 1), old, "M1 poly AT: MIDI task jump table slot 0x5F -> handler")
    # M2-5b: event 0x60 {port w2, ch w3, value w4} -> Channel Pressure 0xD0 (two bytes)
    hc = asm(f"mov r0, r5; bl {S.M4MIDI_BEGIN:#x}; "
             f"movs r1, #0xd0; mov r0, r5; bl {S.M4MIDI_STATUS:#x}; "          # Channel Pressure
             f"uxtb.w r1, r8; mov r0, r5; bl {S.M4MIDI_CHANNEL:#x}; "            # channel (word 3)
             f"uxtb r1, r7; mov r0, r5; bl {S.M4MIDI_DATA:#x}; "              # pressure (word 4)
             f"uxtb.w r2, r9; mov r1, r5; mov r0, r6; bl {S.M4MIDI_SEND:#x}; "# send on port (word 2)
             f"mov r0, r5; bl {S.M4MIDI_RESULT:#x}; mov r1, r0; mov r0, r6; bl {S.M4MIDI_DONE:#x}; "
             f"b.w {S.M4_LOOP:#x}", M4_CHAN)
    if M4_POLY + len(h) > M4_CHAN or M4_CHAN + len(hc) > M4_POLY_LIMIT:
        raise SystemExit("M4 channel pressure handler layout")
    patch(M4_CHAN, hc, b"\xff" * len(hc), "M2-5b AT Out Channel: M4 0x60 handler (status 0xD0)")
    patch(jt_5f + 4, struct.pack("<I", M4_CHAN | 1), old, "M2-5b AT Out Channel: MIDI task jump table slot 0x60 -> handler")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    build(a.base.read_bytes(), a.out)
