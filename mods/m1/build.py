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

# Our M7 code sits in stock's zero gap after the M7 image's data: S.M7_TRAMP (trampolines, 1 KB since v3.59),
# S.M7_CODE .. S.M7_LIMIT (19 KB since v3.59) and the hold shortcuts' S.CTRL_INPUT, S.CTRL_STARTUP (1.6.1: below
# M7_TRAMP; 1.6.8 since BE4.44 W0: the page above M7_LIMIT, so the DSP window reaches M7_TRAMP; check_ctrl_placement).
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
# S4+S5 ported the rest except mx (Jeff deferred the Mod Matrix page and the External rows): cond_ui, poly_at,
# inta_live, step, hold. default_off: groups a plain build leaves out on that base (1.6.8: mx; BE_ENABLE asks for
# one, and is refused while it is not ported). m4_note_on: how stock's MIDI-out task holds an event in its Note On
# case (registers per event word, the message in r5, the MIDI sink); 1.6.8 recompiled the task (note_on_case).
# BE_PLAY_COUNT_WRAP (1.6.8): cond_loop gets the boundary call's return address, so 1.6.8's new 256-step wrap in the
# count-on mode (track +0x30; S3 called it Play Count, it is most likely open-length recording) is not counted as a pass (A:B keeps the 1.6.1 phase). BE_REC_WORD_LOG (1.6.8): the black box
# counts the +0x14 words stock gives recorded melodic notes (their producer is not traced; expected all 0).
# S6a: the synth groups exist on 1.6.8 only, so 1.6.1 lists its groups (every group up to v3.72) and leaves analog out
# (default_off, then omitted: plan() would otherwise build any group whose entries resolve). 1.6.8 adds analog (BE4.20; BE4.21 after its review: the v4.20 kit, never flashed, stays as it was).
# BE4.40: drums is default-off on 1.6.8 (omit_off: not built at all unless BE_ENABLE asks, like a group that is not ported) (Jeff rejected the carrier-WAV drums; the code stays for the Drum Synth track, and
# BE_ENABLE=drums builds it): a plain 1.6.8 build leaves it out (omitted, -DBE_OMIT_DRUMS), with the sites only it uses.
# BE4.22 (docs/plan-ext-low-notes.md): note0 (literal MIDI note 0 at stock's five M7 note senders), 1.6.8 only, the same
# way: default_off and omitted on 1.6.1. (note0_ui, the Step Note field down to 0, is not built yet: plan sprint N3.)
GROUPS_V372 = ("trace", "stall", "irq30", "bbox", "poly_cap", "retrig", "cond_engine", "nmod", "inta_curve", "poly_at",
               "inta_live", "cond_ui", "step", "hold", "mx")
BASE_SETTINGS = {
    "stock_161": dict(version="3.72", m4_flags=(), m7_flags=(), groups=GROUPS_V372, default_off=("analog", "note0", "drums"),
                      m4_note_on=dict(msg="", ch="r8", note="r7", val="r11", port="r9", sink="r6", sink_lit=False)),
    "stock_168": dict(version="4.46", cond_ui_bytes=0x200, m4_flags=("-mstrict-align", "-DBE_REC_WORD_LOG"), prop_template_n=0x2FC, m7_flags=("-DBE_NOTES_ABOVE_127", "-DBE_PLAY_COUNT_WRAP"),
                      groups=("trace", "stall", "irq30", "bbox", "poly_cap", "retrig", "cond_engine", "nmod", "inta_curve",
                              "poly_at", "inta_live", "cond_ui", "step", "hold", "analog", "note0", "drums"), default_off=("mx", "drums"), omit_off=("drums",),
                      m4_note_on=dict(msg="add r5, sp, #4; ", ch="r9", note="r6", val="r4", port="r7", sink="r4", sink_lit=True)),
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
ENABLED = {g for g in os.environ.get("BE_ENABLE", "").split(",") if g}
# BE4.44 W2 (docs/plan-wt-fm.md D5, decided: "in between, leaning bright"): the Wavetable FM mip threshold is one constant
# (dsp/analog.cpp WT_MIP_THR, its hysteresis derived from it). BE_WT_MIP builds another value (W3 / W4's cleaner and brighter
# alternate device images); the manifest records it (wt_mip), and a value other than the default suffixes the version
# (-mip2.0, after any BE_TAG) so an alternate image is never mistaken for the default on the device. Stock's own ladder is
# 1.25 (clean). This is the one place the default lives: analog.cpp has no fallback (#error), the tests read the manifest.
WT_MIP_DEFAULT = 1.75
WT_MIP_OFF = "off"   # BE4.44 W3 (D5's bright alternate, Jeff 2026-11-17 "never go darker"): the reader stays at stock's level for the note
def wt_mip_threshold(text=None):
    """BE_WT_MIP as a float in [1.0, 4.0] (below 1 is brighter than the table holds; 4 skips two levels' worth), or "off"
    (W3: never coarser than stock's level for the note under any FM, the D5 rows' "note level" column)."""
    text = os.environ.get("BE_WT_MIP", "") if text is None else text
    if not text:
        return WT_MIP_DEFAULT
    if text.strip().lower() == WT_MIP_OFF:
        return WT_MIP_OFF
    try:
        v = float(text)
    except ValueError:
        raise SystemExit(f"BE_WT_MIP={text!r}: not a number (or off)")
    if not 1.0 <= v <= 4.0:
        raise SystemExit(f"BE_WT_MIP={text!r}: outside 1.0 .. 4.0")
    return v
WT_MIP = wt_mip_threshold()
def wt_mip_suffix(v=None):
    """The version's suffix for a non-default threshold ('' at the default; -mipoff for off)."""
    v = WT_MIP if v is None else v
    if v == WT_MIP_OFF:
        return "-mipoff"
    return "" if v == WT_MIP_DEFAULT else f"-mip{v:g}" if v != int(v) else f"-mip{v:.1f}"
def wt_mip_defines(v=None):
    """The compiler's view of it: WT_MIP_THR always (analog.cpp #errors without it); off adds WT_MIP_OFF=1 (the threshold is
    then not read; stock's 1.25 is passed only so the one source compiles the same way)."""
    v = WT_MIP if v is None else v
    return ["-DWT_MIP_THR=1.25f", "-DWT_MIP_OFF=1"] if v == WT_MIP_OFF else [f"-DWT_MIP_THR={v!r}f"]
DISABLED = (DEFAULT_OFF - ENABLED) | {g for g in os.environ.get("BE_DISABLE", "").split(",") if g}
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
# S4+S5 split the Mod Matrix page and the External INST rows (mx) out of step (Jeff deferred them on 1.6.8).
# mx's code lives in m4_step.c and its hooks call into it, so mx can't run without step: BE_DISABLE=step
# still leaves every per-step site stock (the same image as before the split; the manifest also lists mx).
if "step" in DISABLED:
    DISABLED.add("mx")
# A group left out of the image (omitted: not ported to the base) whose code shares a source with a built
# group: the define that compiles it out of that source. Passed only when the group is omitted, never for a
# bisect (BE_DISABLE leaves the code in the image), so a base where it builds keeps its bytes.
# S6a: analog left out (1.6.1) -> BE_OMIT_SYNTH: m7_polyat.c keeps the v3.70 reservation, byte for byte.
# BE4.22: note0 left out (1.6.1) -> BE_OMIT_NOTE0: the shared M7 / M4 sources keep their v3.72 code.
# S6c: analog left out -> BE_OMIT_ANALOG, drums -> BE_OMIT_DRUMS, both (1.6.1) -> also BE_OMIT_SYNTH (omit_flags): the
# shared synth code (the reservation, the black box call-out, the slice tables) goes only when neither synth is built.
OMIT_DEFINES = {"mx": "-DBE_OMIT_MX", "analog": "-DBE_OMIT_ANALOG", "note0": "-DBE_OMIT_NOTE0", "drums": "-DBE_OMIT_DRUMS"}
SYNTH_GROUPS = ("analog", "drums")
DRUM_KEYS_N = 7                       # S6c-2: synth_shared.h DRUM_KEYS_N (the X3 wrapper's range; test_drum_ui checks both)
def omit_flags(groups):
    """The defines for these left-out groups (OMIT_DEFINES), with BE_OMIT_SYNTH when every synth group is among them."""
    f = [OMIT_DEFINES[g] for g in sorted(set(groups) & set(OMIT_DEFINES))]
    return f + (["-DBE_OMIT_SYNTH"] if set(SYNTH_GROUPS) <= set(groups) else [])
OMITTED = set()                       # set by build(): the groups this image leaves out
PATCH_GROUPS = [("mx", ("Per-step: External", "Per-step: Mod Matrix", "Per-step: modulation routing")),   # before step
                ("step", ("Per-step", "Step edit", "Strike", "Step inspection", "Curves")),
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
                ("nmod", ("Note Mod",)),                            # F5 Note Mod (+ -DBE_NO_NMOD)
                ("analog", ("Analog",)),                            # S6a Analog oscillators on Wavetable tracks (1.6.8)
                ("drums", ("Drums",)),                              # S6c drum synths on one-shot pads (1.6.8)
                ("note0", ("Literal note 0",))]                     # BE4.22 literal MIDI note 0 at stock's five senders (1.6.8)
if "diag7" in DISABLED:
    DISABLED |= {"trace", "stall"}
if "diag" in DISABLED:
    DISABLED |= {"trace", "stall", "irq30", "bbox"}
# S6a: the synth area is taken with the Region (poly_cap's region(), m7_polyat.c), and the M4 publishes the config line
# the M7 needs for it from the black box (bbox): without either the synths have no memory, so they go off too.
if DISABLED & {"poly_cap", "bbox"}:
    DISABLED |= {"analog", "drums"}
# S4+S5 review: a misspelt name (BE_DISABLE=inta_lve) used to build the default image silently, so a
# "bisect" kit would really be the default. Every name must be a group, an alias, or a define-only switch
# (rmidi: -DBE_NO_RMIDI in m7_retrig.c; modtest: -DBE_NO_MODTEST in m4_step.c).
DEFINE_ONLY = {"rmidi", "modtest"}
KNOWN_NAMES = {g for g, _ in PATCH_GROUPS} | set(ALIASES) | {"diag", "diag7"} | DEFINE_ONLY | DEFAULT_OFF
_unknown = sorted((ENABLED | DISABLED) - KNOWN_NAMES)
if _unknown:
    raise SystemExit(f"unknown BE_DISABLE/BE_ENABLE name(s): {', '.join(_unknown)}; known: {', '.join(sorted(KNOWN_NAMES))}")
def patch_group(why):
    return next((g for g, prefixes in PATCH_GROUPS if why.startswith(prefixes)), None)
# S6c-2: the sites both synth groups use start "Synth:"; a bisect leaves them stock only when both groups are off. Since
# BE4.40 there are none (X1-X3 and H1-H3 are the drums' alone, "Drums:"); the rule stays for a later shared site.
SHARED_SYNTH_PREFIX = "Synth:"
def site_disabled(why):
    if why.startswith(SHARED_SYNTH_PREFIX):
        return set(SYNTH_GROUPS) <= DISABLED
    return patch_group(why) in DISABLED
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

# BE4.22 note0 (m7_note0.c): stock's five note-0 drum-note blocks (NOTE0_<name>_MAP, entered only from the sentinel
# test, for note 0 on a MIDI-out track) and where each goes back (NOTE0_<name>_BACK, the MIDI event build). (name, black
# box site id, track register, pad register, router operand, note register): the block's entry becomes a b.w to a
# trampoline that calls note0_site(track, pad, router, id) and puts the answer in the note register, keeping r0-r3, r12
# and lr (the Off sender's pad r3 is used again after MSG_SEND).
NOTE0_SITES = [
    ("SEQ_ON", 1, "r4", "r6", "[sp, #0x5c]", "r10"),   # note_route: router = its 6th argument
    ("SEQ_OFF", 2, "r4", "r3", "[sp, #0x38]", "r8"),   # sequencer Note Off: router = its 5th argument
    ("PAD_ON", 3, "r4", "r8", "r10", "r5"),
    ("PAD_OFF", 4, "r4", "r8", "r9", "r5"),
    ("ALL_OFF", 5, "r6", "r7", "r8", "r10"),
]
TRAMP_SAVE = 32                                         # push.w {r0-r3, r12, lr} + sub sp, #8 (keeps sp 8-aligned)

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
    ("m4_step.c", "m4_step", ("step", "mx")),
    ("dsp/analog.cpp", "dsp", ("analog",)), ("dsp/analog_kernel.cpp", "dsp", ("analog",)),   # S6a: hard-float DSP ELF
    ("dsp/wt_reader.cpp", "dsp", ("analog",)),                                           # BE4.44 W2: the WT FM reader loops
    ("dsp/wt_mipx.cpp", "dsp", ("analog",)),                                             # BE4.44 W2: its mip-change loop (-Os)
    ("dsp/dsp_lib.cpp", "dsp", ("analog", "drums")),                                     # S6c: memset / memcpy, math
    ("dsp/drums.cpp", "dsp", ("drums",)), ("dsp/drum_kick.cpp", "dsp", ("drums",)),      # S6c: the drum synths
    ("dsp/daisysp/Source/Drums/synthbassdrum.cpp", "dsp", ("drums",)),                   # S6c: DaisySP (MIT, unmodified)
    ("dsp/daisysp/Source/Filters/svf.cpp", "dsp", ("drums",)),
    ("m4_synth.c", "m4_synth", ("analog", "drums")),
    ("m7_note0.c", "m7", ("note0",)),                       # BE4.22: after the others, so their code keeps its place
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
                "M4_NOTE_ON_CASE", "MIDI_JT", "EV_MIDI_POLY_AT", "MIDI_JT_FIRST", "EV_MIDI_CHAN_AT", "M4_NOTE_ON_END",
                "M4MIDI_SINK"),
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
             "STEP_DEFAULTS_READ", "HEADER_GEN", "DRUM_EVENT", "NOTE_INSERT", "NOTE_UPDATE", "DOC_READ", "HOLD_TAP_PAD",
             "HOLD_TAP_TOUCH"),
    "mx": ("EXT_BUILD", "EXT_EVENT", "EV_ENCODER", "MX_BUILD", "MX_ROUTE", "MX_EDIT", "MX_BAR", "MX_EVENT"),
    "analog": ("M7_DSP", "M7_DSP_LIMIT", "DSP_WINDOW_WORDS", "WT_VOICE_VT_SLOT", "WT_VOICE_RENDER", "WT_OSC_VT_SLOT", "WT_OSC_RENDER",
               "PAGE_KEYS_COPY", "M4_MEMCPY", "PANE_TEXT_1", "PANE_TEXT_3", "PANE_TEXT_5",   # A7: the hints (T2: 2 / 4 stock)
               "PANE_TEXT_6", "SLOT_TITLE", "M7_TRAMP", "EXT_POOL_END", "PROBE_SCRATCH",   # M7_TRAMP: S6a's guard (BE4.44 W0)
               "SYNTH_VIEW_TICK_SLOT", "SYNTH_VIEW_TICK", "M7_DATA_END",   # S6a review
               # BE4.40 native properties (docs/plan-native-props.md): N1 the template copy (2 literal words, 3 immediates),
               # N2 the registry build's bl, N3 the oscillator's slot 8 (BE4.22's handle sites and the menu are gone)
               "PROP_TEMPLATE", "PROP_TEMPLATE_LIT_A", "PROP_TEMPLATE_LIT_B", "PROP_TEMPLATE_CMP_A", "PROP_TEMPLATE_CMP_B",
               "PROP_TEMPLATE_LAST", "REG_BUILD_SITE", "REG_BUILD", "WT_OSC_PROP_SLOT", "WT_OSC_PROPERTY",
               "KEY_OSC_TYPE", "KEY_OSC_SHAPE", "KEY_OSC_SYNC", "KEY_OSC_GLIDE",
               "KEY_OSC_SUBFM", "KEY_OSC_FM12",   # P3 Q1 (docs/plan-p3-fm-bits.md 2.1): Sub FM, FM 1>2 (BE4.42: Bit Depth gone)
               "KEY_OSC_FMATK", "KEY_OSC_FMDEC",  # BE4.43 (docs/plan-p3-fm-bits.md BE4.43): the FM envelope, per oscillator
               # BE4.40 mixed sync (docs/plan-be440-sync-identity.md Track S): S1 the oscillator's slot 2 (note start)
               "WT_OSC_NOTE_SLOT", "WT_NOTE_START",
               # BE4.45 X2 (docs/plan-fm-targets.md): the oscillator's slots 9 (clear) and 10 (connect), the route handler
               "WT_OSC_CLEAR_SLOT", "WT_OSC_CLEAR", "WT_OSC_CONNECT_SLOT", "WT_OSC_CONNECT", "WT_ROUTE", "WT_ROUTE_LEN",
               "WT_ROUTE_SHA",
               # BE4.40 identity (docs/plan-be440-sync-identity.md track I): T0 the strings and their one literal each, T1 the
               # title site, D1 the pane paint slot, B1' the 0x3F post; the review fixes: V1 / Y1 the value-text sites and the
               # step position, D2 the frames' redraw
               "STR_PREFIX_OSC1", "STR_PREFIX_OSC2", "STR_DEST_OSC_FMT", "PREFIX_LIT_OSC1", "PREFIX_LIT_OSC2", "DEST_FMT_LIT",
               "PANE_TITLE_SITE", "WT_PANE_PAINT_SLOT", "WT_PANE_PAINT", "BROWSE_POST_SITE", "UI_POST",
               "VALUE_TEXT_1", "VALUE_TEXT_2", "VALUE_TEXT_3", "ENC_POS_SET", "WT_PANE_FRAMES_DIRTY",
               # BE4.45 X1 (docs/plan-fm-targets.md): the Wavetable Mod Matrix destination table, its users, the row filler's bounds
               "MX_WT_DEST_TAB", "MX_WT_DEST_COUNT", "MX_WT_DEST_LIT_1", "MX_WT_DEST_LIT_2", "MX_WT_DEST_LIT_3", "MX_WT_DEST_LIT_4",
               "MX_WT_DEST_N_LIT_1", "MX_WT_DEST_N_LIT_3", "MX_WT_DEST_N_LIT_4", "MX_WT_ROWS_N", "MX_WT_ROWS_END",
               # BE4.45 X3: the piano roll's destination list (its type match, the enumerator and its index rule), the lane
               # toggle and the Wavetable root's lane read (tests)
               "PR_DEST_LIST", "PR_DEST_MATCH", "PR_DEST_ENUM", "PR_DEST_ENUM_LEN", "PR_DEST_ENUM_SHA", "MX_GRAN_DEST_COUNT",
               "PR_LANE_TOGGLE", "WT_ROOT_BLOCK", "WT_ROOT_LANES_END",
               # X3 review: the builder's bytes (its frame), the root's clear-all (message 0x48) and the loop's next message (tests)
               "PR_DEST_LIST_LEN", "PR_DEST_LIST_SHA", "WT_ROOT_CLEAR_ALL", "WT_ROOT_MSG_NEXT",
               # BE4.46 S1 (docs/feasibility-fm-env-sources.md): FM Env 1 / 2 as routing sources 3 / 6 (fmenv_connect)
               "FMENV_IDX_TBH", "FMENV_IDX_PAD", "FMENV_IDX_NEXT", "FMENV_SUB_TBH", "FMENV_SUB_PAD", "FMENV_SUB_NEXT",
               "WT_VOICE_ALLOC", "WT_VOICE_OSC1",   # (S1 fix: the connect's Wavetable-voice gate)
               # BE4.46 S2: the Wavetable Mod Matrix page lists them (the source list, the validator, the names)
               "MX_WT_SRC_LIST", "MX_WT_SRC_LIT", "MX_SRC_VALID_LIT", "MX_WT_ROWS_SITE", "MX_WT_ROWS", "MX_SRC_VALID",
               "MX_SRC_NAME", "MXV_TYPE", "MXV_SRC_LIST", "MXV_SRC_N", "TYPE_WAVETABLE"),
    # S6c (1.6.8): the M7 site (D1, the render entry; review B1 retired the two voice-start sites) and the synth area / DSP
    # window it shares with analog. The shared blocks are checked and built under whichever synth group is in the image
    # (build(): synth_g); a BE_DISABLE bisect of one of them leaves the other's code and memory working.
    "drums": ("M7_DSP", "M7_DSP_LIMIT", "DRUM_RENDER", "EXT_POOL_END",
              # S6c-2: the drum page (D5 refresh entry, D6 event slot, the page's tick slot), the pad copy (H4), and the
              # synth UI sites only the drums use since BE4.40 (X1-X3, H1-H3)
              "INST_REFRESH", "INST_EV_SLOT", "INST_EVENT", "INST_TICK_SLOT", "INST_TICK", "CHILD_DUP", "KEY_DR_FIRST",
              "INST_SAVE_SITE", "INST_LOAD", "CONTEXT_GET", "OBJ_CTOR", "OBJ_BASE_VT", "OBJ_HANDLE_CTR", "NODE_VT_DESTROY",
              "NODE_DESTROY", "NODE_VT_CLONE", "NODE_CLONE", "DSP_WINDOW_WORDS", "M7_DATA_END", "PROBE_SCRATCH", "M7_TRAMP"),
    "note0": ("NOTE0_SEQ_ON_MAP", "NOTE0_SEQ_ON_BACK", "NOTE0_SEQ_OFF_MAP", "NOTE0_SEQ_OFF_BACK", "NOTE0_PAD_ON_MAP",
              "NOTE0_PAD_ON_BACK", "NOTE0_PAD_OFF_MAP", "NOTE0_PAD_OFF_BACK", "NOTE0_ALL_OFF_MAP", "NOTE0_ALL_OFF_BACK"),
}
GROUP_TITLES = {"core": "black box core (always built)", "trace": "M7 event tracing", "poly_cap": "poly cap (M7)",
                "poly_at": "poly AT: External handler + M4 MIDI 0x5F/0x60 handlers (S5)",
                "inta_curve": "curve playback on internal instruments (M7)", "inta_live": "internal live AT (LIVE_SITE, S5)",
                "retrig": "retrigger + curve playback hooks", "nmod": "Note Mod engine (M7)", "stall": "M7 stall record + profile",
                "cond_engine": "trigger conditions engine (M7)", "cond_ui": "probability editor clamps (M4, S4)",
                "irq30": "M4 watchdog profile", "bbox": "black box M4 hooks",
                "hold": "hold shortcuts", "step": "per-step UI / strike / curves / Note Mod UI",
                "mx": "Mod Matrix page + External INST rows (deferred on 1.6.8)",
                "analog": "Analog oscillators on Wavetable tracks (S6a, 1.6.8)",
                "drums": "drum synths on one-shot pads: the Kick (S6c, 1.6.8)",
                "note0": "literal MIDI note 0 at stock's M7 note senders (BE4.22, 1.6.8)"}
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
        cpu = "cortex_m7" if src.startswith(("m7_", "dsp/")) else "cortex_m4"
        cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "thumb-freestanding-eabi", f"-mcpu={cpu}", "-E", "-P",
               "-ffreestanding", "-nostdlib", "-include", str(hdr), f"-I{d}",
               *([f"-I{HERE / x}" for x in DSP_INCLUDES] if src.startswith("dsp/") else []),
               *[f"-DBE_NO_{g.upper()}" for g in sorted(disabled)], *wt_mip_defines(), *flags, str(path)]
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
    # a source shared with an OMIT_DEFINES group (m4_step.c: step + mx): what it uses without that group's code
    lean = {src: source_refs(base, src, disabled, tuple(omit_flags(gs)))
            for src, _, gs in SOURCES if any(g in OMIT_DEFINES for g in gs)}
    def needs(g):
        n = set(GROUP_NAMES[g])
        for src, _, gs in SOURCES:
            if (gs == () and g == "core") or g in gs:
                n |= lean[src] if src in lean and g not in OMIT_DEFINES else refs[src]
        return n
    included, omitted, refused = {"core"}, set(), {}
    omit_off = set(BASE_SETTINGS[base.module].get("omit_off", ())) - ENABLED   # BE4.40: default-off and left out of the image
    for g in GROUP_NAMES:
        if g == "core":
            continue
        if g in omit_off:
            omitted.add(g)
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
        default_off = set(BASE_SETTINGS[base.module].get("default_off", ()))
        asked = sorted(set(refused) & default_off & ENABLED)          # on by BE_ENABLE only: dropping it is enough
        omit_off = set(BASE_SETTINGS[base.module].get("omit_off", ())) - ENABLED   # already left out: nothing to disable
        off = sorted((set(refused) | omitted) & {g for g, _ in PATCH_GROUPS} - set(asked) - omit_off)
        if asked:
            lines.append(f"Build without them: drop {','.join(asked)} from BE_ENABLE (left out by default on {base.label})")
        if off:
            lines.append(f"Build without them: BE_DISABLE={','.join(off)}")
    return "\n".join(lines)


def layout(S):
    """Our placement for this base: the appended M4 blocks in order, and our shared lines."""
    L = dict(M7_TRAMP=S.M7_TRAMP, M7_CODE=S.M7_CODE, M7_LIMIT=S.M7_LIMIT, CTRL_INPUT=S.CTRL_INPUT,
             CTRL_STARTUP=S.CTRL_STARTUP, APPEND_FROM=S.APPEND_FROM)
    L["COND_UI"] = S.APPEND_FROM                               # probability clamp helpers
    # S6c (BE4.30, 1.6.8 only: BASE_SETTINGS cond_ui_bytes): the black box had 152 B left in its 24 KB (BE4.22: 24,424 of
    # 24,576 B), so COND_UI (84 B used of 4 KB) shrinks to 512 B and the black box starts 0xE00 lower (28,160 B);
    # M4_LIMIT and every block after it keep their address. 1.6.1 keeps 0x1000 (its image stays v3.72's).
    # Documented in mods/m1/README.md (memory layout) and docs/plan-synths-168.md section 4.
    cond = BASE_SETTINGS[S.module].get("cond_ui_bytes", 0x1000)
    L["COND_UI_LIMIT"] = L["M4_CODE"] = S.APPEND_FROM + cond   # black box (m4_diag.c)
    L["M4_LIMIT"] = S.APPEND_FROM + 0x7000  # = M4_CODE + 24 KB since v3.64 (1.6.8 from BE4.30: 28,160 B); 20 KB since v3.54
    L["HOLD_WRAP"] = L["M4_LIMIT"]; L["HOLD_CODE"] = L["HOLD_WRAP"] + 0x100; L["HOLD_LIMIT"] = L["HOLD_WRAP"] + 0x400
    L["CTRL_WRAP"] = L["HOLD_LIMIT"]; L["CTRL_CODE"] = L["CTRL_WRAP"] + 0x200; L["CTRL_LIMIT"] = L["CTRL_WRAP"] + 0x1000
    L["STEP_WRAP"] = L["CTRL_LIMIT"]; L["STEP_CODE"] = L["STEP_WRAP"] + 0x600   # m4_step.c
    L["STEP_LIMIT"] = L["STEP_WRAP"] + 0x6000                   # 24 KB since v3.62; 20 KB since v3.41
    L["M4_POLY"] = L["STEP_LIMIT"]; L["M4_POLY_LIMIT"] = L["M4_POLY"] + 0x100   # 0x5F handler (72 B), 0x60 handler at +0x80
    L["M4_CHAN"] = L["M4_POLY"] + 0x80
    # S6a (analog): synth UI entry table + wrappers, then m4_synth.c, sized at link time up to the proven image size
    L["SYNTH_UI"] = L["M4_POLY_LIMIT"]; L["SYNTH_CODE"] = L["SYNTH_UI"] + 0x100
    L["SYNTH_LIMIT"] = (BASE + PROVEN_MAX_SIZE) & ~3
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
    VERSION += wt_mip_suffix()
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
            + "#ifndef BE_OMIT_MX\n"                     # used by the Mod Matrix code only (mx)
            + f"static const unsigned char DEST_TYPE[{len(d)}] = {{" + ", ".join(str(x[1]) for x in d) + "};\n"
            + f"static const unsigned char DEST_NUM[{len(d)}] = {{" + ", ".join(str(x[2]) for x in d) + "};\n"
            + "#endif\n")

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

def compile_elf(srcs, cpu, ld, out, origin, limit, extra=()):
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
           *[f"-DBE_NO_{g.upper()}" for g in sorted(DISABLED)], *omit_flags(OMITTED),
           *extra, *map(str, srcs), "-o", str(out)]
    subprocess.run(cmd, check=True)
    return out.read_bytes()


# ---- S6a: the hard-float DSP ELF (docs/plan-synths-168.md section 10) -----------------------------
# The CPU string must carry the FPU: -mfloat-abi=hard with plain -mcpu=cortex_m7 silently builds soft-float (zig 0.15.1,
# checked: every float operation becomes a __aeabi_f* call). The entry points take integers and pointers only, so stock's
# vtables can point at them and our soft-float m7.elf never calls a float function. -O2 for the kernel file, -Os for the
# rest (clang ignores optimize attributes). `defines`: measurement builds only (test_analog.py's before / after runs).
DSP_CPU = ("-target", "thumb-freestanding-eabihf", "-mcpu=cortex_m7+fp_armv8d16sp", "-mfloat-abi=hard")
DSP_OPT = {"analog_kernel.cpp": "-O2", "wt_reader.cpp": "-O2", "drum_kick.cpp": "-O2", "synthbassdrum.cpp": "-O2", "svf.cpp": "-O2"}
# S6c: the vendored DaisySP files (dsp/daisysp, unmodified) find their includes here; dsp/shim stands in for the C / C++ library
# headers they name (searched before the compiler's own), so no libc / libm declaration (and no double overload) reaches them.
DSP_INCLUDES = ("dsp/shim", "dsp/daisysp/Source", "dsp/daisysp/Source/Utility")
DSP_REFUSED_SYMBOLS = re.compile(r"^(__aeabi_(f|d|u?i2[fd]|u?l2[fd]|c[fd]|u?ldivmod)"          # soft float / double, 64-bit divide
                                 r"|__(add|sub|mul|div|neg|fix|float|extend|trunc|cmp|eq|ne|lt|le|gt|ge|un)\w*[sdt]f2?$)")
DSP_REFUSED_SECTIONS = (".data", ".bss", ".init_array", ".ARM.extab")
# P3 Q2 (docs/plan-p3-fm-bits.md 4.7): libm names the DSP must not call. Under -fno-builtin a plain rintf / fmaxf / floorf ..
# is a call, and zig's compiler_rt defines most of the float family (M, Q2 review: fmaxf 78 B, floorf 140, sqrtf 292, sinf
# 1360, ..; rintf it lacks, so lld refuses that one), so a typo links a software routine with no undefined symbol. The
# whole family by name, float and double forms (the soft-double helpers are DSP_REFUSED_SYMBOLS'); before the
# undefined-symbol refusal, so the message names the cause. __builtin_rintf / __builtin_fmaxf .. inline (VRINTX, VMAXNM).
DSP_REFUSED_LIBM = re.compile(r"^(rint|nearbyint|l?l?round|l?l?rint|fmin|fmax|floor|ceil|trunc|sqrt|fabs|fmod|remainder|"
                              r"copysign|frexp|ldexp|modf|hypot|cbrt|a?sinh?|a?cosh?|a?tanh?|atan2|sincos|exp|exp2|expm1|"
                              r"log|log2|log10|log1p|pow)[fl]?$")


def compile_dsp(srcs, ld, out, origin, limit, defines=()):
    """The DSP sources, each at its own optimisation level, linked at [origin, limit) (ld's MEMORY set as compile_elf does)."""
    text, n = re.subn(r"ORIGIN = 0x[0-9A-Fa-f]+, LENGTH = 0x[0-9A-Fa-f]+",
                      f"ORIGIN = {origin:#x}, LENGTH = {limit - origin:#x}", Path(ld).read_text())
    if n != 1:
        raise SystemExit(f"{ld}: expected one MEMORY line")
    ldo = out.with_suffix(".ld")
    ldo.write_text(text)
    syms_h = out.parent / "stock_syms.h"
    syms_h.write_text(S.c_header())
    common = [sys.executable, "-m", "ziglang", "c++", *DSP_CPU, "-std=c++17", "-g", "-ffreestanding", "-fno-builtin",
              "-fno-stack-protector", "-nostdlib", "-nostdlib++", "-fno-exceptions", "-fno-rtti", "-fno-threadsafe-statics",
              "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
              "-Wall", "-Werror", "-Wdouble-promotion", "-include", str(syms_h), f"-I{out.parent}",
              *[f"-I{HERE / d}" for d in DSP_INCLUDES],
              *[f"-DBE_NO_{g.upper()}" for g in sorted(DISABLED)], *wt_mip_defines(), *defines]
    objs = []
    for src in srcs:
        o = out.parent / ("dsp_" + Path(src).stem + ".o")
        subprocess.run([*common, DSP_OPT.get(Path(src).name, "-Os"), "-c", str(src), "-o", str(o)], check=True)
        objs.append(str(o))
    subprocess.run([*common, "-Wl,--gc-sections", f"-Wl,--script={ldo}", "-Wl,--entry=0", *objs, "-o", str(out)], check=True)
    e = out.read_bytes()
    check_dsp_elf(e, origin, limit)
    return e


def elf_sections(e):
    """{name: (type, addr, size)} of an ELF's sections, and its symbols [(name, value, shndx, info)]."""
    shoff = struct.unpack_from("<I", e, 32)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", e, 46)
    secs = [struct.unpack_from("<10I", e, shoff + i * shentsize) for i in range(shnum)]
    names = e[secs[shstrndx][4]:secs[shstrndx][4] + secs[shstrndx][5]]
    nm = lambda o, tab: tab[o:].split(b"\0")[0].decode()
    out = {nm(s[0], names): (s[1], s[3], s[5]) for s in secs}
    syms = []
    for s in secs:
        if s[1] == 2:
            strtab = secs[s[6]]
            tab = e[strtab[4]:strtab[4] + strtab[5]]
            for j in range(0, s[5], s[9]):
                n, val, _, info, _, shndx = struct.unpack_from("<IIIBBH", e, s[4] + j)
                syms.append((nm(n, tab), val, shndx, info))
    return out, syms


def check_dsp_elf(e, origin, limit):
    """Refuse a DSP ELF that would not run as built: undefined symbols, any soft-float or double helper (the soft-float
    trap above; one stray double pulls in about 700 B of compiler_rt), RAM data, constructors, unwind tables."""
    secs, syms = elf_sections(e)
    for n in DSP_REFUSED_SECTIONS:
        if n in secs and secs[n][2]:
            raise SystemExit(f"DSP ELF: section {n} ({secs[n][2]} B) is not allowed (no RAM data, constructors or unwind tables)")
    libm = sorted({n for n, v, shndx, info in syms if DSP_REFUSED_LIBM.match(n)})
    if libm:
        raise SystemExit(f"DSP ELF: libm routines {libm} linked (compiler_rt's software ones, -fno-builtin): "
                         f"use the __builtin_* forms (VRINTX, VMAXNM ..) or compares")
    und = sorted({n for n, v, shndx, info in syms if n and shndx == 0})
    if und:
        raise SystemExit(f"DSP ELF: undefined symbols {und}")
    bad = sorted({n for n, v, shndx, info in syms if DSP_REFUSED_SYMBOLS.match(n)})
    if bad:
        raise SystemExit(f"DSP ELF: soft-float / double helpers linked in {bad}: single precision, hard float only "
                         f"(the CPU string must carry the FPU: {' '.join(DSP_CPU)})")
    text = secs.get(".text")
    if not text or text[1] != origin or text[1] + text[2] > limit:
        raise SystemExit(f"DSP ELF: .text at {text and hex(text[1])} + {text and text[2]} is not at its origin {origin:#x} inside {limit:#x}")


M7_IMAGE_END = 0x080E0000      # the M7 image's flash ends here; the disk-mode image starts (docs/architecture.md)


def check_dsp_assumptions(img, S):
    """S6a: the DSP window (M7_DSP .. M7_DSP_LIMIT) is in stock's zero gap after the M7 image's data, below our M7 slice,
    and nothing in the image points into it (a literal, or a movt of its upper half). BE4.44 W0: the bound is our M7 slice's
    start, M7_TRAMP (it was CTRL_INPUT, which then sat right below M7_TRAMP; above M7_LIMIT that bound would let the window
    run over the trampolines and the C slice)."""
    def fail(what): raise SystemExit(f"DSP window ({S.label}, S6a): {what}; re-verify M7_DSP for this firmware")
    lo, hi = S.M7_DSP, S.M7_DSP_LIMIT
    if lo & 0xFFF or hi & 0xFFF or not lo < hi <= S.M7_TRAMP:
        fail(f"{lo:#x}..{hi:#x} is not a 4 KB aligned window below our M7 slice (M7_TRAMP {S.M7_TRAMP:#x})")
    if any(img[lo - 0x300 - BASE:hi - BASE]):
        fail("the window (and the 0x300 B guard below it) is not all zero in the base image")
    data_end = struct.unpack_from("<I", img, S.M7_DATA_END - BASE)[0]     # review N3: zeros alone can be a .data tail
    if not (BASE < data_end and data_end + 0x300 <= lo):
        fail(f"the M7 image's flash data ends at {data_end:#x} (M7_DATA_END), not below the window and its guard")
    # Words whose value falls in the window: only the ones the table lists (data, not literals), and none of them read by
    # a PC-relative load (decoding every halfword as one, so data counts too: conservative).
    words = struct.unpack_from(f"<{len(img) // 4}I", img)
    near = tuple(BASE + 4 * i for i, w in enumerate(words) if lo <= w < hi)
    if near != tuple(sorted(S.DSP_WINDOW_WORDS)):
        fail(f"image words pointing into the window changed: {[hex(a) for a in near]}")
    reads = set()
    for k in range(0, len(img) - 4, 2):
        h = struct.unpack_from("<H", img, k)[0]
        if 0x4800 <= h <= 0x4FFF:
            reads.add(((BASE + k + 4) & ~3) + (h & 0xFF) * 4)
        elif h in (0xF8DF, 0xF85F):
            imm = struct.unpack_from("<H", img, k + 2)[0] & 0xFFF
            reads.add(((BASE + k + 4) & ~3) + (imm if h == 0xF8DF else -imm))
    if reads & set(near):
        fail(f"a PC-relative load reads a word pointing into the window: {[hex(a) for a in sorted(reads & set(near))]}")
    for k in range(0, len(img) - 3, 2):
        h1, h2 = struct.unpack_from("<HH", img, k)
        if (h1 & 0xFBF0) == 0xF2C0 and not h2 & 0x8000:
            v = (h1 & 15) << 12 | (h1 >> 10 & 1) << 11 | (h2 >> 12 & 7) << 8 | h2 & 255
            if v == lo >> 16:
                fail(f"movt #{v:#x} at {BASE + k:#x} may build an address in the window")

def check_ctrl_placement(S, window):
    """BE4.44 W0: the hold shortcuts' M7 stubs, [CTRL_INPUT, CTRL_STARTUP + 0x100) (the input hook below CTRL_STARTUP, the
    startup stub's 0x100 B slot: hold_shortcuts' own bounds), lie clear of our M7 slice [M7_TRAMP, M7_LIMIT) and of the DSP
    window (window: (M7_DSP, M7_DSP_LIMIT) when the build has one, else None), and end at or below the M7 image's end.
    (Zero in the base image: patch() checks the bytes it replaces.)"""
    def fail(what): raise SystemExit(f"hold-shortcut stubs ({S.label}, BE4.44 W0): {what}")
    lo, mid, hi = S.CTRL_INPUT, S.CTRL_STARTUP, S.CTRL_STARTUP + 0x100
    if lo & 3 or mid & 3 or not lo < mid:
        fail(f"CTRL_INPUT {lo:#x} / CTRL_STARTUP {mid:#x}: not word aligned, in order")
    spans = [("our M7 slice (M7_TRAMP .. M7_LIMIT)", S.M7_TRAMP, S.M7_LIMIT)]
    if window:
        spans.append(("the DSP window (M7_DSP .. M7_DSP_LIMIT)", *window))
    for what, a, b in spans:
        if lo < b and a < hi:
            fail(f"{lo:#x}..{hi:#x} overlaps {what} {a:#x}..{b:#x}")
    if hi > M7_IMAGE_END:
        fail(f"{lo:#x}..{hi:#x} runs past the M7 image's end {M7_IMAGE_END:#x} (the disk-mode image)")


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

def synth_bytes():
    m = re.search(r"^#define SYNTH_BYTES (0x[0-9A-Fa-f]+)u", (HERE / "synth_shared.h").read_text(), re.M)
    if not m:
        raise SystemExit("synth_shared.h: no SYNTH_BYTES")
    return int(m.group(1), 16)

def synth_define(name):
    m = re.search(rf"^#define {name} (0x[0-9A-Fa-f]+)u", (HERE / "synth_shared.h").read_text(), re.M)
    if not m:
        raise SystemExit(f"synth_shared.h: no {name}")
    return int(m.group(1), 16)

def fmenv_offsets():
    """BE4.46 S1: the voice-tail layout of FM Env 1 / 2 (synth_shared.h FMENV_*, the DSP's): (src, val, on, slack end)."""
    o = tuple(synth_define(n) for n in ("FMENV_SRC", "FMENV_VAL", "FMENV_ON", "FMENV_SLACK_END"))
    src, val, on, end = o
    if not (val == src + 4 and on >= val + 8 and on + 4 <= end and src % 4 == 0 and on < 0x1000):
        raise SystemExit(f"synth_shared.h FMENV_*: layout {[hex(x) for x in o]} is not src, src + 4 (two values), flag")
    return o

# BE4.46 S1 (docs/feasibility-fm-env-sources.md, "Recommended route", M7): stock's Wavetable routing connect (WT_ROUTE,
# message 0x3F) picks the source object by id in two tbh tables, the oscillator / envelope / LFO / filter path (instances
# 0 / 1, indexed by id - 1, the voice in r5) and the sub path (instances 2.., indexed by id - 2, the voice in r3); ids 3 and
# 6 (Env 3 / LFO 3, which a Wavetable voice does not have) go to "no source" (movs r1, #0) in both. Their four entries are
# repointed at a stub in each table's alignment padding (nops, no branch into them: S): r1 = the voice's tail (FMENV_SRC,
# + 4 for 6), then on to a body among the DSP trampolines (fmenv_body). S1 fix (review, S): the same connect also serves the
# class-0x5A root (ROOT_5A_CTOR: voices of 0xFE8 B whose OSC1 lives at + 0x7A8, so voice + 0xB84.. is inside a live
# oscillator there), so the body first checks the voice is a Wavetable one ([voice + VOICE_OSC1] == voice + 0x7A4, as
# WT_ROOT_CTOR leaves it: WT_VOICE_OSC1) and otherwise takes stock's own movs r1, #0 (no source: stock's behaviour, byte for
# byte). On a Wavetable voice it sets FMENV_ON on the first connect only and then writes 1.0 into both values (the unset
# value, until the next an_enter / an_note publishes), so a later connect (an amount step) never overwrites a published
# value. Scratch: r3 (idx path) / r2 (sub path), written before any read on every path from the join (S); the flags are set
# before any read there too (idx: adds r0, r5, r0; sub: cmp r6, #2).
FMENV_STUB_BYTES = 14
FMENV_OSC1_PTR, FMENV_OSC1_AT = 0x178, 0x7A4          # the voice's OSC1 pointer and where a Wavetable voice's OSC1 is (S)
def fmenv_stub(at, voice, body):
    """(bytes, offset of id 6's entry, offset of id 3's entry): addw r1, voice, #src + 4 (id 6); b.n join; addw r1, voice,
    #src (id 3); join: b.w body."""
    src = fmenv_offsets()[0]
    parts = [asm(f"addw r1, {voice}, #{src + 4:#x}", at), b"", asm(f"addw r1, {voice}, #{src:#x}", at + 6)]
    join = at + 10
    parts[1] = struct.pack("<H", 0xE000 | ((join - (at + 4 + 4)) >> 1 & 0x7FF))         # b.n join (T2, encoded by hand)
    parts.append(asm(f"b.w #{body:#x}", join))
    code = b"".join(parts)
    if [len(p) for p in parts] != [4, 2, 4, 4] or len(code) != FMENV_STUB_BYTES:
        raise SystemExit(f"FM Env stub at {at:#x}: unexpected encoding {[len(p) for p in parts]}")
    check_branches(code, at, {join, body}, f"FM Env stub at {at:#x}")
    return code, 0, 6

def fmenv_body(voice, tmp, nxt):
    """The connect's body for one path (r1 = the stub's pointer): not a Wavetable voice -> stock's movs r1, #0 (nxt - 2);
    else FMENV_ON 0 (first connect) -> FMENV_ON = r1 and both values 1.0; then the join (nxt). 42 B."""
    src, val, on, end = fmenv_offsets()
    return (f"ldr.w {tmp}, [{voice}, #{FMENV_OSC1_PTR:#x}]; subw {tmp}, {tmp}, #{FMENV_OSC1_AT:#x}; cmp {tmp}, {voice}; "
            f"bne other; ldr.w {tmp}, [{voice}, #{on:#x}]; cbnz {tmp}, done; str.w r1, [{voice}, #{on:#x}]; "
            f"mov.w {tmp}, #0x3f800000; str.w {tmp}, [{voice}, #{val:#x}]; str.w {tmp}, [{voice}, #{val + 4:#x}]; "
            f"done: b.w #{nxt:#x}; other: b.w #{nxt - 2:#x}")

def fmenv_connect(img_base, S, patch, tramp):
    """BE4.46 S1: place the two bodies (tramp: among the DSP trampolines), patch the two tables and place the two stubs (see
    FMENV_STUB_BYTES). Refuses a base where the entries do not go to stock's "no source" path, the join is not right after
    its movs r1, #0, the padding is not all nops, the voice is not allocated as 0xB88 B (the slack: synth_shared.h), or a
    Wavetable voice's OSC1 is not at + 0x7A4 (the gate). Returns {name: body address}."""
    src, val, on, end = fmenv_offsets()
    def fail(what): raise SystemExit(f"FM Env sources (BE4.46 S1, {S.label}): {what}")
    if img_base[S.WT_VOICE_ALLOC - BASE:S.WT_VOICE_ALLOC - BASE + 4] != asm("movw r2, #0xb88", S.WT_VOICE_ALLOC) or end != 0xBA0:
        fail("the Wavetable voice is not allocated as 0xB88 B (its slack 0xB88..0xBA0 is FM Env's)")
    o1 = S.WT_VOICE_OSC1
    if img_base[o1 - BASE:o1 - BASE + 4] != asm(f"addw r7, r4, #{FMENV_OSC1_AT:#x}", o1) or \
            img_base[o1 + 0x18 - BASE:o1 + 0x1C - BASE] != asm(f"strd r7, r6, [r4, #{FMENV_OSC1_PTR:#x}]", o1 + 0x18):
        fail(f"WT_ROOT_CTOR does not place OSC1 at voice + {FMENV_OSC1_AT:#x} (the gate's test)")
    bodies = {}
    for tbh, n, pad, padn, nxt, voice, tmp, first, name in (
            ("FMENV_IDX_TBH", 6, "FMENV_IDX_PAD", 28, "FMENV_IDX_NEXT", "r5", "r3", 1, "idx"),
            ("FMENV_SUB_TBH", 5, "FMENV_SUB_PAD", 26, "FMENV_SUB_NEXT", "r3", "r2", 2, "sub")):
        t, p, x = getattr(S, tbh), getattr(S, pad), getattr(S, nxt)
        if img_base[x - 2 - BASE:x - BASE] != b"\x00\x21":
            fail(f"{nxt} {x:#x} does not follow stock's movs r1, #0")
        nops = img_base[p - BASE:p - BASE + padn]
        if nops != (b"\x00\xbf" if padn % 4 else b"") + b"\xaf\xf3\x00\x80" * (padn // 4):
            fail(f"{pad} {p:#x} is not {padn} B of nops")
        ent = list(struct.unpack_from(f"<{n}H", img_base, t - BASE))
        i3, i6 = 3 - first, 6 - first
        if t + 2 * ent[i3] != x - 2 or t + 2 * ent[i6] != x - 2:
            fail(f"{name} table: ids 3 / 6 do not go to stock's no-source path {x - 2:#x}")
        body = bodies[name] = tramp(fmenv_body(voice, tmp, x), {x, x - 2}, f"FM Env connect body, {name} path")
        code, o6, o3 = fmenv_stub(p, voice, body)
        if len(code) > padn:
            fail(f"the stub does not fit {pad}")
        ent[i3], ent[i6] = (p + o3 - t) // 2, (p + o6 - t) // 2
        # (the rest of the padding: nop.n, so no half of a stock nop.w is left after the stub's b.w; never reached)
        patch(p, code + b"\x00\xbf" * ((padn - len(code)) // 2), S.orig(pad),
              f"FM Env sources (BE4.46 S1): routing connect stub, {name} path (ids 3 / 6 -> voice + {src:#x} / {src + 4:#x})")
        patch(t, struct.pack(f"<{n}H", *ent), S.orig(tbh),
              f"FM Env sources (BE4.46 S1): routing connect {name} table, ids 3 / 6 -> the stub")
    return bodies

def check_region_assumptions(img, S, synth=False):
    """Refuse a base whose ext pool no longer has room for the Region where we put it. S6a (synth): the same
    reservation also takes the synth area below it (synth_shared.h), so the whole of it is checked."""
    def word(a): return struct.unpack_from("<I", img, a - BASE)[0]
    def fail(what): raise SystemExit(f"Region move (v3.70): {what}; re-verify the reservation for this firmware")
    lo = S.REGION
    hi = S.EXT_POOL_END
    if lo + shared_define("M1_REGION_RESERVE") != hi:
        fail("the Region does not end at the pool's end")
    if synth:
        lo -= synth_bytes()                     # one reservation: synth area + Region (m7_polyat.c region_reserve)
        if lo & 0xFFF:
            fail(f"the synth area {lo:#x} is not 4 KB aligned")
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
PUSH_R4_R10_LR = "push.w {r4, r5, r6, r7, r8, r9, r10, lr}"
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
    global DISABLED, OMITTED
    s = bases.select(base_img)                   # refuses an unknown image; REVIEW entries can't be read
    if s.module in BASE_SETTINGS:                # per-base default-off groups (1.6.8: mx, deferred); BE_ENABLE asks for one
        DISABLED = DISABLED | (set(BASE_SETTINGS[s.module].get("default_off", ())) - ENABLED)
    included, omitted, refused, needs = plan(s, DISABLED)
    if refused:                                  # an enabled feature (or core) uses a draft entry
        raise SystemExit(refuse_message(s, refused, omitted))
    OMITTED = set(omitted)
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
    synth = inc("analog") or inc("drums")              # S6c: the synth area, DSP window and synth UI serve both
    synth_g = "analog" if inc("analog") else "drums"
    check_region_assumptions(base_img, S, synth=synth)
    if synth:
        with S.group(synth_g):
            check_dsp_assumptions(base_img, S)
            window = (S.M7_DSP, S.M7_DSP_LIMIT)
    check_ctrl_placement(S, window if synth else None)    # core entries (+ the window read above under its group)
    check_slice_assumptions(base_img, S)
    check_sites(base_img, S)
    out.mkdir(parents=True, exist_ok=False)
    img = bytearray(base_img)
    writes = []
    skipped = []

    def patch(addr, new, expect, why, site=False):
        if (len(new) <= 8 or site) and site_disabled(why):   # bisect build: leave stock's site alone
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
            # 1.6.8 (BE_PLAY_COUNT_WRAP): cond_loop(seq, caller's lr) tells the 256-step Play Count wrap apart
            lr_arg = "ldr r1, [sp, #0x3c]; " if "-DBE_PLAY_COUNT_WRAP" in BASE_SETTINGS[S.module]["m7_flags"] else ""
            loop = asm(save + f"ldr r0, [sp, #8]; {lr_arg}bl {s7['cond_loop']:#x}; " + restore     # r0 = the sequence
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
    if inc("note0"):
        with S.group("note0"):
            tramps += note0_trampolines(base_img, patch, s7, M7_TRAMP + len(tramps))
    if M7_TRAMP + len(tramps) > M7_CODE:
        raise SystemExit("trampolines overrun into M7 code")
    patch(M7_TRAMP, tramps, bytes(len(tramps)), "M1 diag: M7 trampolines")
    patch(M7_CODE, code7, bytes(len(code7)), "M1 diag: M7 capture code")
    sd, dsp_bytes = {}, 0
    if synth:
        with S.group(synth_g):
            sd, dsp_bytes = m7_dsp(base_img, patch, sources, out, inc)

    # --- M4: compile, append, redirect the defaultTask-loop BL
    m4_elf = compile_elf([HERE / "m4_diag.c"], "cortex_m4", HERE / "m4.ld", out / "m4.elf", M4_CODE, M4_LIMIT,
                         extra=(f"-DBE_SYNTH_UI={SYNTH_UI:#x}u",) if synth else ())
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
            per_step(base_img, img, patch, extend, sources, s4, sh, out, inc)

    if M4_CODE + len(code4) + 20 > M4_LIMIT:
        raise SystemExit("M4 code + marker overrun")
    if M4_CODE + len(code4) + len(MARKER) > M4_LIMIT:
        raise SystemExit("M4 diag code + marker overruns its slice")
    if inc("poly_at"):
        with S.group("poly_at"):
            if BASE + len(img) < M4_POLY:                # a build without the per-step block (S2: never on 1.6.1)
                extend(M4_POLY, "M1 poly AT: extend image with erased (0xFF) flash up to the M4 handler block")
            m4_poly_handlers(base_img, img, patch, extend)
    synth_bytes_ = 0
    if synth:
        with S.group(synth_g):
            synth_bytes_ = synth_ui(base_img, img, patch, extend, sources, s4, out, inc)
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
        if not set(SYNTH_GROUPS) <= set(omitted):   # S6a / S6c: the DSP window and the synth UI block
            shown.update(M7_DSP=S.M7_DSP, SYNTH_UI=SYNTH_UI, SYNTH_CODE=SYNTH_CODE)
        for g, keys in (("hold", ("HOLD_WRAP", "HOLD_CODE", "CTRL_WRAP", "CTRL_CODE")), ("step", ("STEP_WRAP", "STEP_CODE")),
                        ("poly_at", ("M4_POLY",))):
            if g in omitted:
                for k in keys:
                    shown.pop(k)
        shown["IMAGE_END"] = BASE + len(img)
    man = dict(label=f"{DISPLAY} (bento_enhanced v{VERSION} on stock {S.version})", version=VERSION, display=DISPLAY, disabled=sorted(DISABLED), layout={k: hex(v) for k, v in shown.items()}, base=S.module, base_label=S.label, base_sha256=S.sha256, sha256=hashlib.sha256(img).hexdigest(),
               m7_symbols={k: hex(v) for k, v in s7.items()}, m4_symbols={k: hex(v) for k, v in s4.items()},
               m7_code_bytes=len(code7), m4_code_bytes=len(code4), trampoline_bytes=len(tramps), writes=writes)
    if synth:                                    # S6a / S6c
        man.update(dsp_symbols={k: hex(v) for k, v in sd.items()}, dsp_code_bytes=dsp_bytes, synth_code_bytes=synth_bytes_,
                   wt_mip=WT_MIP)
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
    if synth:
        print(f"  DSP code {dsp_bytes} B at {S.M7_DSP + DSP_TRAMP_BYTES:#x} (window to {S.M7_DSP_LIMIT:#x}); "
              f"synth UI {synth_bytes_} B at {SYNTH_CODE:#x}")
    print(f"  image {len(img):#x} bytes (proven max {PROVEN_MAX_SIZE:#x}); {len(writes)} writes, reversal to stock verified")
    return img, man


def note0_trampolines(base_img, patch, s7, t):
    """BE4.22 note0: each of stock's note-0 drum-note blocks starts with a b.w to a trampoline (appended after the others,
    so their addresses keep their places). The block's first instruction must be stock's pad test and BACK must start the
    event build (add r0, sp, ...: r0-r3, r12 and the flags are written before they are read there)."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
    md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
    name, out = {"r9": "sb", "r10": "sl"}, b""
    for site_name, sid, track, pad, router, reg in NOTE0_SITES:
        site, back = getattr(S, f"NOTE0_{site_name}_MAP"), getattr(S, f"NOTE0_{site_name}_BACK")
        first = next(md.disasm(S.orig(f"NOTE0_{site_name}_MAP"), site, 1))
        if first.mnemonic not in ("cmp", "cmp.w") or first.op_str != f"{name.get(pad, pad)}, #7":
            raise SystemExit(f"NOTE0_{site_name}_MAP: stock's block starts with {first.mnemonic} {first.op_str}, not cmp {pad}, #7")
        b0 = next(md.disasm(bytes(base_img[back - BASE:back - BASE + 4]), back, 1))
        if not (b0.mnemonic == "add" and b0.op_str.startswith("r0, sp, #")):
            raise SystemExit(f"NOTE0_{site_name}_BACK: {b0.mnemonic} {b0.op_str} is not the event build (add r0, sp, #...)")
        if router.startswith("[sp, #"):
            r_arg = f"ldr r2, [sp, #{int(router[6:-1], 16) + TRAMP_SAVE:#x}]"
        else:
            r_arg = f"mov r2, {router}"
        code = asm(f"push.w {{r0, r1, r2, r3, r12, lr}}; sub sp, #8; mov r0, {track}; mov r1, {pad}; {r_arg}; movs r3, #{sid}; "
                   f"bl {s7['note0_site']:#x}; mov {reg}, r0; add sp, #8; pop.w {{r0, r1, r2, r3, r12, lr}}; b.w {back:#x}", t)
        check_branches(code, t, {s7["note0_site"], back}, f"note-0 trampoline {site_name}")
        code += bytes(-len(code) % 4)
        out += code
        patch(site, asm(f"b.w {t:#x}", site), S.orig(f"NOTE0_{site_name}_MAP"),
              f"Literal note 0: {site_name.lower().replace('_', ' ')} drum-note block -> note0_site")
        t += len(code)
    return out


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
    # How the stock consumer holds the event at the site (S4/S5): 1.6.1 preloads it into registers
    # (r5 code, r6 context, r7 index, record copy at sp+0x220); 1.6.8 keeps only the record pointer r6
    # (sp+0x218) and reads the code with ldrb r3,[r6]. r4 is the model in both. Chosen by the bytes at the
    # site, so a base with other bytes is refused here (replays) instead of getting the wrong registers.
    CONSUMER = {
        "a5f11103": ("add.w r0, sp, #608; str r0, [sp]; mov r0, r4; mov r1, r5; mov r2, r6; mov r3, r7",   # record (orig sp+0x220)
                     "sub.w r3, r5, #17"),
        "3378113b": ("str r6, [sp]; mov r0, r4; ldrb r1, [r6]; ldr r2, [r6, #8]; ldr r3, [r6, #12]",
                     "ldrb r3, [r6]; subs r3, #0x11"),
    }
    if S.orig("CTRL_CONSUMER_SITE").hex() not in CONSUMER:
        raise SystemExit(f"CTRL_CONSUMER_SITE: no wrapper for stock's {S.orig('CTRL_CONSUMER_SITE').hex()}")
    args, displaced = CONSUMER[S.orig("CTRL_CONSUMER_SITE").hex()]
    consumer = asm(
        f"push.w {{r0-r12, lr}}; sub sp, #8; {args}; bl {sc['controls_dispatch']:#x}; "
        "cmp r0, #0; add sp, #8; pop.w {r0-r12, lr}; bne consumed; "
        f"{displaced}; b.w {S.CTRL_CONSUMER_SITE + 4:#x}; "                          # displaced instruction(s)
        f"consumed: b.w {S.CTRL_DISPATCH_NEXT:#x}", CTRL_WRAP)
    check_branches(consumer, CTRL_WRAP, {sc["controls_dispatch"], S.CTRL_CONSUMER_SITE + 4, S.CTRL_DISPATCH_NEXT}, "controls consumer")
    replays("CTRL_CONSUMER_SITE", displaced)
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


def per_step(base_img, img, patch, extend, sources, s4, sh, out, inc):
    """Per-step Condition / Retrigger UI, strike velocity, curves, Note Mod UI (m4_step.c); with mx, the External
    INST rows and the Mod Matrix page (their wrappers stay where they were, so a build with mx keeps its bytes)."""
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
    # 1.6.8 recompiled the generator (one register fewer, sub sp #0xc: the same 0x28 frame; args model r0, description
    # r1, &flag r2, r3 as before): the replay is the base's own prologue, by its bytes; any other prologue is refused.
    gen_push = {"2de9f047": PUSH_R4_R10_LR, "2de9f043": PUSH_R4_R9_LR}.get(S.orig("HEADER_GEN").hex())
    if gen_push is None:
        raise SystemExit(f"HEADER_GEN: no replay for stock's prologue {S.orig('HEADER_GEN').hex()}")
    replays("HEADER_GEN", gen_push)
    orig_gen = sblock(f"{gen_push}; b.w {S.HEADER_GEN + 4:#x}", {S.HEADER_GEN + 4}, "header replay")
    a = sblock(f"push {{r4, r5, r6, lr}}; mov r4, r0; mov r5, r1; bl {orig_gen:#x}; mov r2, r0; mov r0, r4; mov r1, r5; "
               f"bl {ss['strike_toolbar']:#x}; pop {{r4, r5, r6, pc}}", {orig_gen, ss["strike_toolbar"]}, "header wrapper")
    patch(S.HEADER_GEN, asm(f"b.w {a:#x}", S.HEADER_GEN), S.orig("HEADER_GEN"), "Strike Vel: header button (slot left of Undo)")
    a = sblock(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['strike_event']:#x}; cmp r0, #0; beq stock; "
               + restore + "movs r0, #0; bx lr; stock: " + restore + f"push {{lr}}; sub sp, #0x6c; b.w {S.DRUM_EVENT + 4:#x}",
               {ss["strike_event"], S.DRUM_EVENT + 4}, "drum event")
    replays("DRUM_EVENT", "push {lr}; sub sp, #0x6c")
    patch(S.DRUM_EVENT, asm(f"b.w {a:#x}", S.DRUM_EVENT), S.orig("DRUM_EVENT"), "Strike Vel: button action in the drum view")
    if inc("mx"):
        with S.group("mx"):   # deferred on 1.6.8, left out of its image there (m4_step.c: -DBE_OMIT_MX)
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


DSP_TRAMP_BYTES = 0x100                           # M7_DSP: our trampolines, then the DSP ELF


def m7_dsp(base_img, patch, sources, out, inc):
    """S6a A1 / A2 and S6c D1: the hard-float DSP ELF in its window, our trampolines below it (DSP_TRAMP_BYTES), and the
    sites: two vtable words of stock's Wavetable classes (analog), the one-shot pad engine's render entry (drums).
    The Analog trampolines keep r0-r3 and leave the stack arguments alone (slot 18 reads two of them)."""
    lo, hi = S.M7_DSP, S.M7_DSP_LIMIT
    at = lo + DSP_TRAMP_BYTES
    e = compile_dsp(sources("dsp"), HERE / "dsp" / "dsp.ld", out / "dsp.elf", at, hi)
    va, code, sd = elf_load(e, at, hi)
    if va != at or at + len(code) > hi:
        raise SystemExit(f"DSP code placement {va:#x}+{len(code)} outside its window")
    tramps = bytearray()
    def tramp(src, exits, what):
        while len(tramps) % 16:
            tramps.append(0)
        a = lo + len(tramps)
        c = asm(src, a)
        check_branches(c, a, exits, what)
        tramps.extend(c)
        return a
    if inc("analog"):
        if bytes(base_img[S.WT_VOICE_VT_SLOT - BASE:S.WT_VOICE_VT_SLOT - BASE + 4]) != struct.pack("<I", S.WT_VOICE_RENDER | 1) or \
                bytes(base_img[S.WT_OSC_VT_SLOT - BASE:S.WT_OSC_VT_SLOT - BASE + 4]) != struct.pack("<I", S.WT_OSC_RENDER | 1):
            raise SystemExit("Wavetable vtable slots do not hold stock's voice / oscillator render")
        voice = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; bl {sd['an_enter']:#x}; pop {{r0, r1, r2, r3, r4, lr}}; "
                      f"b.w {S.WT_VOICE_RENDER:#x}", {sd["an_enter"], S.WT_VOICE_RENDER}, "Analog voice trampoline")
        osc_at = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; bl {sd['an_osc']:#x}; cmp r0, #0; pop {{r0, r1, r2, r3, r4, lr}}; "
                       f"beq stock; bx lr; stock: b.w {S.WT_OSC_RENDER:#x}", {sd["an_osc"], S.WT_OSC_RENDER},
                       "Analog oscillator trampoline")
        # N3 (BE4.40): the oscillator's slot 8 (osc r0, msg r1): an_prop takes our four keys, every other key goes on to
        # stock's (which ignores ours anyway: it has no case for them)
        if bytes(base_img[S.WT_OSC_PROP_SLOT - BASE:S.WT_OSC_PROP_SLOT - BASE + 4]) != struct.pack("<I", S.WT_OSC_PROPERTY | 1):
            raise SystemExit("Wavetable oscillator slot 8 does not hold stock's set property")
        prop_at = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; bl {sd['an_prop']:#x}; cmp r0, #0; pop {{r0, r1, r2, r3, r4, lr}}; "
                        f"beq stock; bx lr; stock: b.w {S.WT_OSC_PROPERTY:#x}", {sd["an_prop"], S.WT_OSC_PROPERTY},
                        "Analog oscillator property trampoline")
        # S1 (BE4.40 mixed sync): the oscillator's slot 2, note start (osc r0, r1, &note r2): an_note, then stock's
        if bytes(base_img[S.WT_OSC_NOTE_SLOT - BASE:S.WT_OSC_NOTE_SLOT - BASE + 4]) != struct.pack("<I", S.WT_NOTE_START | 1):
            raise SystemExit("Wavetable oscillator slot 2 does not hold stock's note start")
        note_at = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; bl {sd['an_note']:#x}; pop {{r0, r1, r2, r3, r4, lr}}; "
                        f"b.w {S.WT_NOTE_START:#x}", {sd["an_note"], S.WT_NOTE_START}, "Analog note start trampoline")
        sd.update(tramp_voice=voice, tramp_osc=osc_at, tramp_prop=prop_at, tramp_note=note_at)
        # BE4.46 S1: FM Env 1 / 2 as routing sources 3 / 6 (its two connect bodies are trampolines here, right after the
        # note trampoline: S1 fix), then BE4.45 X2's slot 9 / 10 trampolines
        fe = fmenv_connect(base_img, S, patch, tramp)
        sd.update(tramp_fmenv_idx=fe["idx"], tramp_fmenv_sub=fe["sub"])
        # BE4.45 X2 (docs/plan-fm-targets.md): the oscillator's slot 10, connect (osc r0, source r1, key r2, slot r3, amount
        # s0), and slot 9, clear (osc r0). Ours first, then stock's always (it ignores our keys and keeps its own). The
        # connect passes the root: WT_ROUTE's r4 (its r0, kept there throughout: the sha below holds it to the bytes that
        # were read), only when the call returns into WT_ROUTE, else 0; and the key and slot in one word. s0 is kept for
        # stock's (s1 pads the push to 8 B).
        for name, slot, fn in (("WT_OSC_CLEAR_SLOT", "WT_OSC_CLEAR", "x2_clear"), ("WT_OSC_CONNECT_SLOT", "WT_OSC_CONNECT", "x2_connect")):
            if bytes(base_img[getattr(S, name) - BASE:getattr(S, name) - BASE + 4]) != struct.pack("<I", getattr(S, slot) | 1):
                raise SystemExit(f"Wavetable oscillator {name} does not hold stock's {slot}")
        route = bytes(base_img[S.WT_ROUTE - BASE:S.WT_ROUTE - BASE + S.WT_ROUTE_LEN])
        if hashlib.sha256(route).hexdigest() != S.WT_ROUTE_SHA:
            raise SystemExit("WT_ROUTE is not the route handler whose r4 was read (WT_ROUTE_SHA)")
        clear_at = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; bl {sd['x2_clear']:#x}; pop {{r0, r1, r2, r3, r4, lr}}; "
                         f"b.w {S.WT_OSC_CLEAR:#x}", {sd["x2_clear"], S.WT_OSC_CLEAR}, "X2 clear trampoline")
        conn_at = tramp(f"push {{r0, r1, r2, r3, r4, lr}}; vpush {{s0, s1}}; orr.w r2, r2, r3, lsl #16; "
                        f"movw r12, #{S.WT_ROUTE & 0xFFFF:#x}; movt r12, #{S.WT_ROUTE >> 16:#x}; sub.w r12, lr, r12; "
                        f"movw r3, #{S.WT_ROUTE_LEN:#x}; cmp r12, r3; ite lo; movlo r3, r4; movhs r3, #0; "
                        f"bl {sd['x2_connect']:#x}; vpop {{s0, s1}}; pop {{r0, r1, r2, r3, r4, lr}}; b.w {S.WT_OSC_CONNECT:#x}",
                        {sd["x2_connect"], S.WT_OSC_CONNECT}, "X2 connect trampoline")
        sd.update(tramp_clear=clear_at, tramp_conn=conn_at)
    an_end = len(tramps)
    if inc("drums"):
        with S.group("drums"):
            # D1, the per-voice render entry (root, voice, r2, r3, [sp] temp, [sp + 4] out): dr_render(root, voice, temp, out);
            # 1 = rendered (stock's render is not run; neither caller uses its result), else stock's prologue and body
            replays("DRUM_RENDER", PUSH_R4_R11_LR)
            d1 = tramp(f"push.w {{r0, r1, r2, r3, r12, lr}}; ldr r2, [sp, #24]; ldr r3, [sp, #28]; bl {sd['dr_render']:#x}; "
                       f"cmp r0, #0; pop.w {{r0, r1, r2, r3, r12, lr}}; beq stock; bx lr; stock: {PUSH_R4_R11_LR}; "
                       f"b.w {S.DRUM_RENDER + 4:#x}", {sd["dr_render"], S.DRUM_RENDER + 4}, "Drums D1 render trampoline")
            # (S6c review B1, rc25: no voice-start sites any more. Both voice starts write the voice's start serial after the
            # sample is open, so D1 recognises a start by it; DRUM_VSTART_A/B_SITE stay reviewed facts, unpatched.)
            sd.update(tramp_render=d1)
    if len(tramps) > DSP_TRAMP_BYTES:
        raise SystemExit("DSP trampolines overrun their block")
    if an_end:
        patch(lo, bytes(tramps[:an_end]), bytes(an_end),
              "Analog (S6a): M7 trampolines (voice enter, oscillator render, property, note start; BE4.46 S1: FM Env connect bodies; X2: clear, connect)")
    if len(tramps) > an_end:
        patch(lo + an_end, bytes(tramps[an_end:]), bytes(len(tramps) - an_end),
              "Drums (S6c): M7 trampoline (D1 render entry)")
    patch(at, code, bytes(len(code)), "Analog (S6a): M7 DSP code (hard float, dsp/; the drums since S6c)")
    if inc("analog"):
        patch(S.WT_VOICE_VT_SLOT, struct.pack("<I", sd["tramp_voice"] | 1), S.orig("WT_VOICE_VT_SLOT"),
              "Analog: Wavetable root slot 18 (voice render) -> an_enter, then stock's")
        patch(S.WT_OSC_VT_SLOT, struct.pack("<I", sd["tramp_osc"] | 1), S.orig("WT_OSC_VT_SLOT"),
              "Analog: Wavetable oscillator slot 0 (render) -> an_osc, else stock's")
        patch(S.WT_OSC_PROP_SLOT, struct.pack("<I", sd["tramp_prop"] | 1), S.orig("WT_OSC_PROP_SLOT"),
              "Analog: Wavetable oscillator slot 8 (set property, N3) -> an_prop, else stock's")
        patch(S.WT_OSC_NOTE_SLOT, struct.pack("<I", sd["tramp_note"] | 1), S.orig("WT_OSC_NOTE_SLOT"),
              "Analog: Wavetable oscillator slot 2 (note start, S1) -> an_note, then stock's")
        patch(S.WT_OSC_CLEAR_SLOT, struct.pack("<I", sd["tramp_clear"] | 1), S.orig("WT_OSC_CLEAR_SLOT"),
              "Analog: Wavetable oscillator slot 9 (clear routings, X2) -> x2_clear, then stock's")
        patch(S.WT_OSC_CONNECT_SLOT, struct.pack("<I", sd["tramp_conn"] | 1), S.orig("WT_OSC_CONNECT_SLOT"),
              "Analog: Wavetable oscillator slot 10 (connect a routing, X2) -> x2_connect, then stock's")
    if inc("drums"):
        with S.group("drums"):
            patch(S.DRUM_RENDER, asm(f"b.w {sd['tramp_render']:#x}", S.DRUM_RENDER), S.orig("DRUM_RENDER"),
                  "Drums: per-voice one-shot render entry (D1) -> dr_render, else stock's")
    return sd, len(code)


# BE4.40 N1 (docs/plan-native-props.md 3.1 / 3.2): our oscillator keys, appended to the type-6 run of stock's property
# template in this order (Type first: stock's push sends a child's properties in template order, and the M7 keeps the
# others only once the oscillator is Analog), with their defaults (Shape 48 = saw, NP6). P3 Q1 (docs/plan-p3-fm-bits.md
# 3.1): Sub FM 0x18B and FM 1>2 0x132 after Glide, both 0 (no FM). BE4.41 had Bit Depth 0x1A4 between them (0x2FC + 7 =
# 0x303, one pad); BE4.42 removed it: 0x2FC + 6 = 0x302, padded to 0x304 with two type-0xFF entries (the same count, so the
# patched immediates are BE4.41's; test_native_props: no reader path makes a type-0xFF object). BE4.43 (plan BE4.43): FM Atk
# 0x1A4 (0: instant) and FM Dec 0x1A0 (128: Inf) after FM 1>2, so a new oscillator and a BE4.42 project (no such attribute:
# the default) play env 1, BE4.42's sound: 0x2FC + 8 = 0x304, the same count, and no pad entry any more.
NP_TEMPLATE_KEYS = (("KEY_OSC_TYPE", 0), ("KEY_OSC_SHAPE", 48), ("KEY_OSC_SYNC", 0), ("KEY_OSC_GLIDE", 0),
                    ("KEY_OSC_SUBFM", 0), ("KEY_OSC_FM12", 0), ("KEY_OSC_FMATK", 0), ("KEY_OSC_FMDEC", 128))
NP_OSC_TYPE = 6                       # the Wavetable oscillator objects' template type (WT1 / WT2)
NP_PAD_TYPE = 0xFF                    # pad entries (an encodable count): no object type stock reads or makes (test_native_props)


def np_template(base_img, stock_n):
    """N1: stock's template (stock_n entries at PROP_TEMPLATE) with our keys after its type-6 run, padded with type-0xFF
    entries to the next count both bounds can encode (cmp.w: every multiple of 4 from 0x300 to 0x3FC). -> (bytes, count)"""
    t = S.PROP_TEMPLATE - BASE
    ents = [bytes(base_img[t + 8 * i:t + 8 * i + 8]) for i in range(stock_n)]
    run = [i for i, e in enumerate(ents) if e[0] == NP_OSC_TYPE]
    if not run or run != list(range(run[0], run[-1] + 1)):
        raise SystemExit("N1: the template's type-6 entries are not one run")
    keys = [getattr(S, k) for k, _ in NP_TEMPLATE_KEYS]
    if any(struct.unpack_from("<H", e, 2)[0] in keys for e in ents):
        raise SystemExit("N1: one of our keys is already in stock's template")
    if any(e[0] == NP_PAD_TYPE for e in ents):
        raise SystemExit("N1: stock's template already uses the pad type")
    add = [struct.pack("<BBHI", NP_OSC_TYPE, 0, k, d) for k, (_, d) in zip(keys, NP_TEMPLATE_KEYS)]
    new = ents[:run[-1] + 1] + add + ents[run[-1] + 1:]
    n = (len(new) + 3) & ~3
    if not 0x300 <= n <= 0x3FC:
        raise SystemExit(f"N1: {n:#x} entries: no cmp.w encoding")
    new += [struct.pack("<BBHI", NP_PAD_TYPE, 0, 0, 0)] * (n - len(new))
    return b"".join(new), n


# BE4.45 X1 (docs/plan-fm-targets.md): FM amounts as Mod Matrix destinations. Stock's Wavetable destination table
# (MX_WT_DEST_TAB, 23 entries) is copied into the synth UI block with three rows more, each after its oscillator's last
# stock row (Position): OSC1 Sub FM, OSC2 FM 1>2, OSC2 Sub FM. Names: the oscillator rows' format is ours, "OSC1: %s" /
# "OSC2: %s" (stock's "WT1: %s" / "WT2: %s" at 0x0818EAB8, now unreferenced: the identity T0 / J2 rename, which T0 left out
# because it was not DEST_NAME's), and our three rows carry their own name (+0x10) so the row filler never asks the registry
# (whose title would be null if N2 ever left our keys out). CC column (+0x14): 0, which the page shows blank (MX_ROWS_BUILD
# 0x0814E798: ids are read only there, for display; the column names the CC stock maps to a row, and none maps to ours).
# Users: the row filler (the Mod Matrix page), DEST_NAME and 0x08134700 (the per-type 0x3F posts) read our copy, which has
# its own count word (after the strings). The destination enumerator 0x08159358 (PR_DEST_ENUM) is the piano roll's
# destination list (PR_DEST_LIST, called from the piano roll), which matches rows by type only and takes no instance: X1
# kept it on stock's table (X1 review: 'OSC1: FM 1>2' and the knobless OSC2 Sub FM). BE4.45 X3 (Jeff: piano-roll lanes on
# the three) puts it on our copy too and replaces its type match with pr_dest_skip (m4_synth.c), which keeps our rows only on
# their own oscillator (pr_dest_patch below).
MX_DEST_NEW = ((0, "KEY_OSC_SUBFM", "Sub FM"), (1, "KEY_OSC_FM12", "FM 1>2"), (1, "KEY_OSC_SUBFM", "Sub FM"))
MX_DEST_STOCK_N = 23
MX_DEST_STOCK_T6 = [(0, 3), (0, 0xC4), (0, 0xC9), (1, 3), (1, 0xC4), (1, 0xC9)]   # (instance, key): Pitch, Level, Position
MX_DEST_FMT = (b"OSC1: %s", b"OSC2: %s")
MX_DEST_ENT = 24


def mx_dest_table(base_img, at):
    """X1: (bytes at `at`: the 26 entries, our strings, then the u32 entry count (our count word, the last 4 bytes);
    the entry count)."""
    t, n = S.MX_WT_DEST_TAB - BASE, struct.unpack_from("<I", base_img, S.MX_WT_DEST_COUNT - BASE)[0]
    if n != MX_DEST_STOCK_N:
        raise SystemExit(f"X1: stock's Wavetable destination count is {n}, not {MX_DEST_STOCK_N}")
    ents = [bytearray(base_img[t + MX_DEST_ENT * i:t + MX_DEST_ENT * (i + 1)]) for i in range(n)]
    for e in ents:   # {u8 type, 3 zero, u32 instance, u16 key, 2 zero, fmt, name, u8 id, 3 zero}
        if e[1:4] != bytes(3) or e[10:12] != bytes(2) or e[0x15:0x18] != bytes(3) or struct.unpack_from("<I", e, 4)[0] > 1:
            raise SystemExit(f"X1: stock's destination entry {bytes(e).hex()} is not the expected layout")
    t6 = [i for i, e in enumerate(ents) if e[0] == NP_OSC_TYPE]
    if t6 != list(range(t6[0], t6[0] + 6)) or [(struct.unpack_from("<I", ents[i], 4)[0], struct.unpack_from("<H", ents[i], 8)[0])
                                               for i in t6] != MX_DEST_STOCK_T6:
        raise SystemExit("X1: stock's oscillator destinations are not Pitch / Level / Position of WT1, then WT2")
    keys = {getattr(S, k) for _, k, _ in MX_DEST_NEW}
    if any(struct.unpack_from("<H", e, 8)[0] in keys for e in ents):
        raise SystemExit("X1: one of our keys is already a stock destination")
    total = n + len(MX_DEST_NEW)
    strs, addr = bytearray(), {}
    for s in list(MX_DEST_FMT) + sorted({x for _, _, x in MX_DEST_NEW}):
        b = s if isinstance(s, bytes) else s.encode()
        addr[b] = at + MX_DEST_ENT * total + len(strs)
        strs += b + bytes(4 - len(b) % 4)
    for i in t6:                                          # the oscillator rows read "OSC1: .." / "OSC2: .."
        struct.pack_into("<I", ents[i], 0xC, addr[MX_DEST_FMT[struct.unpack_from("<I", ents[i], 4)[0]]])
    out = list(ents)
    # each after its oscillator's last stock row, in MX_DEST_NEW's order: inserted last first, instance 1 before 0, so
    # every insert lands at a stock index (OSC1's after its Position at +3, OSC2's after its Position at +6)
    for inst, k, name in reversed(MX_DEST_NEW):
        e = bytearray(MX_DEST_ENT)
        e[0] = NP_OSC_TYPE
        struct.pack_into("<IHxxIIB", e, 4, inst, getattr(S, k), addr[MX_DEST_FMT[inst]], addr[name.encode()], 0)
        out.insert(t6[0] + 3 * (inst + 1), e)
    return b"".join(bytes(x) for x in out) + bytes(strs) + struct.pack("<I", total), total


# (table literal, its count literal) per user; X1 kept the enumerator's pair (LIT_3 / N_LIT_3) stock's, X3 moves it too
MX_DEST_USERS = (("MX_WT_DEST_LIT_1", "MX_WT_DEST_N_LIT_1"), ("MX_WT_DEST_LIT_2", None),
                 ("MX_WT_DEST_LIT_3", "MX_WT_DEST_N_LIT_3"), ("MX_WT_DEST_LIT_4", "MX_WT_DEST_N_LIT_4"))
MX_DEST_KEEP = ()
PR_GLOBAL_ROWS = 30          # PR_DEST_ENUM: 3 groups of 10 global keys before Granular's rows (m4_synth.c PR_GLOBAL_ROWS)


def pr_dest_patch(base_img, patch, wrap):
    """X3: PR_DEST_LIST's type match (ldrb.w r3, [sp, #0x26]; cmp r3, r8; then stock's bne to the next row) -> bl wrap;
    cmp r0, #0 (wrap: pr_dest_skip(frame, r4 the enumerator index, r8 the child's type, sb the track handle's address)).
    Refused unless the site is stock's, the bne after it is the one to the next row, and the enumerator is the code whose
    index rule (30 global rows, then Granular's count, then the Wavetable table) pr_dest_skip relies on (PR_DEST_ENUM_SHA),
    and (X3 review) the builder is the code whose frame pr_dest_skip reads ([sp + 0x26] type, [sp + 0x28] key, [sp + 0x2C]
    the child's handle, r4 the index, r8 the child's type, sb the track handle's address: PR_DEST_LIST_SHA)."""
    lst = bytes(base_img[S.PR_DEST_LIST - BASE:S.PR_DEST_LIST - BASE + S.PR_DEST_LIST_LEN])
    if hashlib.sha256(lst).hexdigest() != S.PR_DEST_LIST_SHA:
        raise SystemExit("X3: PR_DEST_LIST is not the builder whose frame was read (PR_DEST_LIST_SHA)")
    site = S.PR_DEST_MATCH
    if asm("ldrb.w r3, [sp, #0x26]; cmp r3, r8", site) != S.orig("PR_DEST_MATCH"):
        raise SystemExit("X3: PR_DEST_MATCH is not stock's type match")
    if base_img[site + 6 - BASE:site + 8 - BASE] != asm(f"bne {site - 0x10:#x}", site + 6):
        raise SystemExit("X3: PR_DEST_MATCH is not followed by the bne to the next row (16 B back)")
    enum = bytes(base_img[S.PR_DEST_ENUM - BASE:S.PR_DEST_ENUM - BASE + S.PR_DEST_ENUM_LEN])
    if hashlib.sha256(enum).hexdigest() != S.PR_DEST_ENUM_SHA:
        raise SystemExit("X3: PR_DEST_ENUM is not the enumerator whose index rule was read (PR_DEST_ENUM_SHA)")
    lits = ((S.PR_DEST_ENUM + 0xB0, S.MX_GRAN_DEST_COUNT), (S.PR_DEST_ENUM + 0xBC, S.MX_WT_DEST_COUNT),
            (S.PR_DEST_ENUM + 0xC4, S.MX_WT_DEST_TAB))
    for a, want in lits:
        if struct.unpack_from("<I", base_img, a - BASE)[0] != want:
            raise SystemExit(f"X3: the enumerator's literal at {a:#x} is not {want:#x}")
    c = asm(f"bl {wrap:#x}; cmp r0, #0", site)
    if len(c) != 6:
        raise SystemExit("X3: the type match's replacement is not 6 bytes")
    patch(site, c, S.orig("PR_DEST_MATCH"), "FM targets (BE4.45 X3): piano-roll destination list, our rows on their own oscillator")


def mx_dest_patches(base_img, patch, at, data, n):
    """X1: the table literals of the row filler, DEST_NAME and 0x08134700 -> our copy, their count literals -> our count
    word, the row filler's two bounds (it hard-codes 23); the enumerator's two literals, stock's table and stock's count
    word stay as they are. Refused unless, on the base image, the four table literals are every reference to the table
    (no other aligned word holds it or points inside it, no movw / movt pair builds it) and the three count literals,
    each 4..8 B before its table literal, are every reference to the count word."""
    tab, cnt = S.MX_WT_DEST_TAB, S.MX_WT_DEST_COUNT
    refs = one_reference(base_img, {tab, cnt})
    lits = sorted(getattr(S, x) for x, _ in MX_DEST_USERS)
    if sorted(refs[tab]) != lits:
        raise SystemExit(f"X1: the table is referenced at {[hex(a) for a in refs[tab]]}, not only by {MX_DEST_USERS}")
    for k in range(0, len(base_img) - 3, 4):
        w = struct.unpack_from("<I", base_img, k)[0]
        if tab < w < tab + MX_DEST_ENT * MX_DEST_STOCK_N:
            raise SystemExit(f"X1: the word at {BASE + k:#x} points inside the table ({w:#x})")
    nlits = [(getattr(S, l), getattr(S, c)) for l, c in MX_DEST_USERS if c]
    if sorted(refs[cnt]) != sorted(c for _, c in nlits) or any(not 0 < l - c <= 8 for l, c in nlits):
        raise SystemExit(f"X1: the count word's references {[hex(a) for a in refs[cnt]]} are not "
                         f"{[x for _, x in MX_DEST_USERS if x]}, each before its table literal")
    for x in [y for u in MX_DEST_USERS for y in u if y]:
        if S.orig(x) != struct.pack("<I", tab if x in dict(MX_DEST_USERS) else cnt):
            raise SystemExit(f"X1: {x} does not hold stock's {'table' if x in dict(MX_DEST_USERS) else 'count word'}")
    if struct.unpack_from("<I", data, len(data) - 4)[0] != n:
        raise SystemExit("X1: our table's count word is not its last word")
    for site, src in (("MX_WT_ROWS_N", f"movs r2, #{MX_DEST_STOCK_N:#x}"),
                      ("MX_WT_ROWS_END", f"add.w r7, r4, #{MX_DEST_ENT * MX_DEST_STOCK_N:#x}")):
        if asm(src, getattr(S, site)) != S.orig(site):
            raise SystemExit(f"X1: {site} is not stock's '{src}'")
    patch(at, data, b"\xff" * len(data), f"FM targets (BE4.45 X1): Wavetable Mod Matrix destinations ({n} entries, names)")
    for lit, nlit in MX_DEST_USERS:
        if lit in MX_DEST_KEEP:
            continue
        patch(getattr(S, lit), struct.pack("<I", at), S.orig(lit), f"FM targets: destination table literal ({lit})")
        if nlit:
            patch(getattr(S, nlit), struct.pack("<I", at + len(data) - 4), S.orig(nlit),
                  f"FM targets: destination count literal ({nlit}) -> our count word ({n})")
    for site, src in (("MX_WT_ROWS_N", f"movs r2, #{n:#x}"), ("MX_WT_ROWS_END", f"addw r7, r4, #{MX_DEST_ENT * n:#x}")):
        c = asm(src, getattr(S, site))
        if len(c) != len(S.orig(site)):
            raise SystemExit(f"X1: '{src}' is not {len(S.orig(site))} bytes")
        patch(getattr(S, site), c, S.orig(site), f"FM targets: row filler bound ({site}: {src})")


# BE4.46 S2 (docs/feasibility-fm-env-sources.md): FM Env 1 / FM Env 2 (stock's source ids 3 / 6, which S1's M7 connect
# gives to the oscillators' FM envelopes) in a Wavetable track's Mod Matrix: the page's source list, the routing validator and
# the source names (m4_synth.c mx_wt_rows, mx_src_valid, mx_src_name). The list is our copy (stock's stays, with its two
# literals, for Granular and the validator's other types); MX_BUILD's bl of the Wavetable row filler is the one site that
# installs it, so the filler's own bytes (which BE4.45 X1 patches for the destinations) stay stock's here.
FMENV_SOURCES = (3, 6)          # FM Env 1, FM Env 2, in that order
FMENV_AFTER = 2                 # right after Env 2 (source id 2)
FMENV_STOCK_N = 16              # stock's Wavetable list (MX_WT_SRC_LIST)


def fmenv_matrix(base_img, patch, code, ss):
    """S2: refused unless stock's list, its literals, the filler's call and both entries are what m4_synth.c assumes."""
    lst = S.MX_WT_SRC_LIST
    stock = list(struct.unpack_from(f"<{FMENV_STOCK_N}H", base_img, lst - BASE))
    if stock[0] != 0 or FMENV_AFTER not in stock or any(s in stock for s in FMENV_SOURCES):
        raise SystemExit(f"S2: stock's Wavetable source list {stock} is not None first, with Env 2 and without ids 3 / 6")
    refs = one_reference(base_img, {lst})[lst]
    if sorted(refs) != sorted([S.MX_WT_SRC_LIT, S.MX_SRC_VALID_LIT]):
        raise SystemExit(f"S2: the source list is referenced at {[hex(a) for a in refs]}, not only by its two literals")
    i = stock.index(FMENV_AFTER) + 1
    ours = stock[:i] + list(FMENV_SOURCES) + stock[i:] + [0]          # + the None MX_EDIT reads at index == count
    blob = struct.pack(f"<{len(ours)}H", *ours)
    if code.count(blob) != 1:
        raise SystemExit(f"S2: m4_synth.c's mx_wt_sources is not {ours} (stock's list with 3 / 6 after Env 2, then 0)")
    if S.orig("MX_WT_ROWS_SITE") != asm(f"bl {S.MX_WT_ROWS:#x}", S.MX_WT_ROWS_SITE):
        raise SystemExit("S2: MX_WT_ROWS_SITE is not stock's bl MX_WT_ROWS")
    # the C stubs (stock_valid / stock_src_name) replay stock's first instructions and resume at site + 5 (Thumb): their
    # bytes must be exactly stock's entry, then movw / movt r3 = site + 5, bx r3 (S2 review: one check ties the C to stock)
    for site, displaced, stub in (("MX_SRC_VALID", "mov ip, r1; ldrh r1, [r1, #4]", "stock_valid"),
                                  ("MX_SRC_NAME", "push {lr}; mov lr, r0", "stock_src_name")):
        replays(site, displaced)
        at, back = ss[stub] & ~1, getattr(S, site) + 5
        want = asm(f"{displaced}; movw r3, #{back & 0xFFFF:#x}; movt r3, #{back >> 16:#x}; bx r3", at)
        got = code[at - SYNTH_CODE:at - SYNTH_CODE + len(want)]
        if got != want or got[:4] != S.orig(site):
            raise SystemExit(f"S2: {stub} at {at:#x} is {got.hex()}, not stock's {site} entry replayed, resuming at {back:#x}")
    patch(S.MX_WT_ROWS_SITE, asm(f"bl {ss['mx_wt_rows']:#x}", S.MX_WT_ROWS_SITE), S.orig("MX_WT_ROWS_SITE"),
          "FM Env sources (BE4.46 S2): a Wavetable Mod Matrix page lists FM Env 1 / 2 after Env 2 (mx_wt_rows)")
    for site, fn, why in (("MX_SRC_VALID", "mx_src_valid", "sources 3 / 6 valid on a Wavetable track"),
                          ("MX_SRC_NAME", "mx_src_name", "3 / 6 named FM Env 1 / FM Env 2 on a Wavetable track")):
        c = asm(f"b.w {ss[fn]:#x}", getattr(S, site))
        if len(c) != 4:
            raise SystemExit(f"S2: {site}'s branch is not 4 bytes")
        patch(getattr(S, site), c, S.orig(site), f"FM Env sources (BE4.46 S2): {why} ({fn})")


def one_reference(img, addrs):
    """{addr: the aligned words of img holding addr}; fails if a movw / movt pair (movt within 4 instructions of the movw,
    same register) builds one of the addresses (T0's 'one literal reference')."""
    refs = {a: [] for a in addrs}
    for k in range(0, len(img) - 3, 4):
        w = struct.unpack_from("<I", img, k)[0]
        if w in refs:
            refs[w].append(BASE + k)
    imm16 = lambda h1, h2: (h1 & 15) << 12 | (h1 >> 10 & 1) << 11 | (h2 >> 12 & 7) << 8 | h2 & 255
    lows = {a & 0xFFFF: a for a in addrs}
    for k in range(0, len(img) - 3, 2):
        h1, h2 = struct.unpack_from("<HH", img, k)
        if (h1 & 0xFBF0) != 0xF240 or h2 & 0x8000 or imm16(h1, h2) not in lows:
            continue
        a, rd = lows[imm16(h1, h2)], h2 >> 8 & 15
        for j in range(k + 4, min(k + 20, len(img) - 3), 2):
            g1, g2 = struct.unpack_from("<HH", img, j)
            if (g1 & 0xFBF0) == 0xF2C0 and not g2 & 0x8000 and g2 >> 8 & 15 == rd and imm16(g1, g2) == a >> 16:
                raise SystemExit(f"T0: a movw / movt pair at {BASE + k:#x} builds {a:#x}")
    return refs


def synth_ui(base_img, img, patch, extend, sources, s4, out, inc):
    """M4: m4_synth.c after the poly AT handlers, its entry table (m4_diag.c calls it) and the wrappers; the Analog sites
    (BE4.40: N1 the template copy after the code, N2 the registry build, A6, A7, A10; identity: T0 strings, T1 the pane
    title, D1 the pane paint, B1' the double-tap's 0x3F) and the drums' (D5, D6, the page tick, X1-X3, H1-H4; only in a drums
    build since BE4.40)."""
    if BASE + len(img) != SYNTH_UI:
        raise SystemExit(f"synth UI block: the image ends at {BASE + len(img):#x}, not at {SYNTH_UI:#x}")
    e = compile_elf(sources("m4_synth"), "cortex_m4", HERE / "m4_synth.ld", out / "m4_synth.elf", SYNTH_CODE, SYNTH_LIMIT)
    va, code, ss = elf_load(e, SYNTH_CODE, SYNTH_LIMIT)
    if va != SYNTH_CODE:
        raise SystemExit(f"synth UI code placement {va:#x}")
    marker = b"BENTO_MOD:synth-ui(Analog oscillators as native properties, drum page; after bento-playground A1, K7)\0"
    end = (SYNTH_CODE + len(code) + len(marker) + 7) & ~7
    tpl_data, tpl_n, tpl_at = b"", 0, end
    mx_at = mx_n = 0                     # X1 (analog): the relocated Mod Matrix destination table
    an = inc("analog")
    if an:
        with S.group("analog"):
            stock_n = BASE_SETTINGS[S.module]["prop_template_n"]
            for site, src in (("PROP_TEMPLATE_CMP_A", f"cmp.w r3, #{stock_n:#x}"), ("PROP_TEMPLATE_CMP_B", f"cmp.w r3, #{stock_n:#x}"),
                              ("PROP_TEMPLATE_LAST", f"movw r7, #{stock_n - 1:#x}")):
                if asm(src, getattr(S, site)) != S.orig(site):
                    raise SystemExit(f"N1: {site} is not stock's '{src}'")
            for site in ("PROP_TEMPLATE_LIT_A", "PROP_TEMPLATE_LIT_B"):
                if S.orig(site) != struct.pack("<I", S.PROP_TEMPLATE):
                    raise SystemExit(f"N1: {site} does not hold PROP_TEMPLATE")
            tpl_data, tpl_n = np_template(base_img, stock_n)
            end = tpl_at + len(tpl_data)
            mx_at = (end + 3) & ~3                               # X1: the destination table after the template
            mx_data, mx_n = mx_dest_table(base_img, mx_at)
            end = mx_at + len(mx_data)
    if end > SYNTH_LIMIT:
        raise SystemExit(f"synth UI block ends at {end:#x}, past the proven image size ({SYNTH_LIMIT:#x})")
    extend(end, "Analog (S6a): extend image with erased (0xFF) flash for the synth UI block")
    head = asm(f"b.w {ss['an_tick']:#x}; b.w {ss['an_print']:#x}", SYNTH_UI)    # entry table: m4_diag.c slot 0 / slot 1
    if len(head) != 8:
        raise SystemExit("synth UI entry table is not two 4-byte branches")
    wrap = bytearray(head)
    def block(src, exits, what):
        while len(wrap) % 4:
            wrap.append(0)
        a = SYNTH_UI + len(wrap)
        c = asm(src, a)
        check_branches(c, a, exits, what)
        wrap.extend(c)
        return a
    n2 = t1 = b1 = pr = None
    if an:
        with S.group("analog"):
            # N2: model init's bl REG_BUILD (its only call) -> stock's builder, then np_keys (our four descriptors), r0 kept
            if S.orig("REG_BUILD_SITE") != asm(f"bl {S.REG_BUILD:#x}", S.REG_BUILD_SITE):
                raise SystemExit("REG_BUILD_SITE: not the expected bl REG_BUILD")
            n2 = block(f"push {{r4, lr}}; bl {S.REG_BUILD:#x}; mov r4, r0; bl {ss['np_keys']:#x}; mov r0, r4; pop {{r4, pc}}",
                       {S.REG_BUILD, ss["np_keys"]}, "Analog registry build")
            # T1 / B1' (BE4.40 identity): reached by stock's bl, so sp is still the caller's: the row's handle from its frame
            t1 = block(f"ldr.w r2, [sp, #0x32c]; b.w {ss['an_pane_title']:#x}", {ss["an_pane_title"]}, "Analog pane title")
            b1 = block(f"ldr r2, [sp, #0x20]; b.w {ss['an_browse']:#x}", {ss["an_browse"]}, "Analog double-tap 0x3F")
            # X3: PR_DEST_LIST's type match (reached by bl, so the builder's frame is at sp + 24 after the push): r0 the frame,
            # r1 = r4 the enumerator index, r2 = r8 the child's type, r3 = sb the track handle's address; r1-r3, ip kept
            pr = block(f"push.w {{r1, r2, r3, r12, lr}}; sub sp, #4; add r0, sp, #24; mov r1, r4; mov r2, r8; mov r3, sb; "
                       f"bl {ss['pr_dest_skip']:#x}; add sp, #4; pop.w {{r1, r2, r3, r12, pc}}", {ss["pr_dest_skip"]},
                       "FM targets X3: piano-roll row test")
    x1 = x2 = x3 = h1 = None
    if inc("drums"):
        with S.group("drums"):
            save = "push.w {r0-r12, lr}; mrs r0, apsr; push {r0, r1}; "
            restore = "pop {r0, r1}; msr apsr_nzcvq, r0; pop.w {r0-r12, lr}; "
            # X1: the instrument writer just appended its params element (r0); r6 = the instrument object (a pad)
            replays("INST_SAVE_SITE", "mov r8, r0; movs r4, #0")
            x1 = block(save + f"mov r0, r6; ldr r1, [sp, #8]; bl {ss['an_save']:#x}; " + restore
                       + f"mov r8, r0; movs r4, #0; b.w {S.INST_SAVE_SITE + 4:#x}", {ss["an_save"], S.INST_SAVE_SITE + 4}, "Drums XML save")
            # X2: the instrument reader's entry (node r0, object r1): ours first (stock skips attributes it does not know)
            replays("INST_LOAD", "push {r4, r5, r6, r7, lr}; sub sp, #0x14")
            x2 = block(save + f"ldr r0, [sp, #8]; ldr r1, [sp, #12]; bl {ss['an_load']:#x}; " + restore
                       + f"push {{r4, r5, r6, r7, lr}}; sub sp, #0x14; b.w {S.INST_LOAD + 4:#x}", {ss["an_load"], S.INST_LOAD + 4}, "Drums XML load")
            # X3: CONTEXT_GET (model, ctx, key r2, &value): the seven contiguous drum keys go to dr_context (one range test),
            # every other key straight to stock; only ip is used before the branch
            replays("CONTEXT_GET", "push {r4, r5, r6, r7, lr}; sub sp, #0xc")
            x3 = block(f"sub.w r12, r2, #{S.KEY_DR_FIRST:#x}; cmp.w r12, #{DRUM_KEYS_N - 1}; bls drum; "
                       f"push {{r4, r5, r6, r7, lr}}; sub sp, #0xc; b.w {S.CONTEXT_GET + 4:#x}; drum: b.w {ss['dr_context']:#x}",
                       {S.CONTEXT_GET + 4, ss["dr_context"]}, "Drums value reads")
            # H1 (BE4.22): OBJ_CTOR's entry (obj r0): an_forget first with every register kept (the constructor itself writes
            # only r1, r2, ip and the flags, so its callers may keep r3 across it), then stock's vtable store, back after it
            replays("OBJ_CTOR", "ldr r2, [pc, #0x1c]; str r2, [r0]")
            lit = ((S.OBJ_CTOR + 4) & ~3) + 0x1C
            if struct.unpack_from("<I", base_img, lit - BASE)[0] != S.OBJ_BASE_VT:
                raise SystemExit(f"OBJ_CTOR: its literal at {lit:#x} is not OBJ_BASE_VT")
            h1 = block(f"push.w {{r0, r1, r2, r3, r12, lr}}; bl {ss['an_forget']:#x}; pop.w {{r0, r1, r2, r3, r12, lr}}; "
                       f"movw r2, #{S.OBJ_BASE_VT & 0xFFFF:#x}; movt r2, #{S.OBJ_BASE_VT >> 16:#x}; str r2, [r0]; b.w {S.OBJ_CTOR + 4:#x}",
                       {ss["an_forget"], S.OBJ_CTOR + 4}, "Drums handles: object constructor")
    if SYNTH_UI + len(wrap) > SYNTH_CODE:
        raise SystemExit("synth UI wrappers overrun into its code")
    patch(SYNTH_UI, bytes(wrap), b"\xff" * len(wrap), "Analog (S6a): synth UI entry table, wrappers")
    patch(SYNTH_CODE, code + marker, b"\xff" * (len(code) + len(marker)), "Analog (S6a): synth UI code (m4_synth.c)")
    if an:
        with S.group("analog"):
            patch(tpl_at, tpl_data, b"\xff" * len(tpl_data),
                  f"Analog (BE4.40): N1 property template ({tpl_n:#x} entries: stock's, our keys after the type-6 run)")
            for site in ("PROP_TEMPLATE_LIT_A", "PROP_TEMPLATE_LIT_B"):
                patch(getattr(S, site), struct.pack("<I", tpl_at), S.orig(site), f"Analog: N1 template literal ({site})")
            for site, src in (("PROP_TEMPLATE_CMP_A", f"cmp.w r3, #{tpl_n:#x}"), ("PROP_TEMPLATE_CMP_B", f"cmp.w r3, #{tpl_n:#x}"),
                              ("PROP_TEMPLATE_LAST", f"movw r7, #{tpl_n - 1:#x}")):
                c = asm(src, getattr(S, site))
                if len(c) != 4:
                    raise SystemExit(f"N1: '{src}' is not one 4-byte instruction")
                patch(getattr(S, site), c, S.orig(site), f"Analog: N1 template bound ({site}: {src})")
            mx_dest_patches(base_img, patch, mx_at, mx_data, mx_n)
            pr_dest_patch(base_img, patch, pr)
            patch(S.REG_BUILD_SITE, asm(f"bl {n2:#x}", S.REG_BUILD_SITE), S.orig("REG_BUILD_SITE"),
                  "Analog: N2 registry build -> stock's, then our oscillator keys' descriptors")
            # A10: the synth view's tick slot (vtable word); A6 / A7: bl sites
            if S.orig("SYNTH_VIEW_TICK_SLOT") != struct.pack("<I", S.SYNTH_VIEW_TICK | 1):
                raise SystemExit("SYNTH_VIEW_TICK_SLOT does not hold stock's SYNTH_VIEW_TICK")
            patch(S.SYNTH_VIEW_TICK_SLOT, struct.pack("<I", ss["an_view_tick"] | 1), S.orig("SYNTH_VIEW_TICK_SLOT"),
                  "Analog: synth page tick: oscillator rows per Type, resend, CPU indicator")
            for site_name, target, fn, why in [("PAGE_KEYS_COPY", "M4_MEMCPY", "an_page_keys", "Wavetable page keys: Type, the Analog rows")] + \
                    [(f"PANE_TEXT_{i}", "SLOT_TITLE", "an_pane_text", f"Wavetable pane hint {i}") for i in (1, 3, 5, 6)] + \
                    [("PANE_TITLE_SITE", "SLOT_TITLE", None, "identity T1: an Analog pane's title (shim: the row's handle)"),
                     ("BROWSE_POST_SITE", "UI_POST", None, "identity B1': no WAV browser from a double-tap on an Analog pane")] + \
                    [(f"VALUE_TEXT_{i}", "SLOT_TITLE", "an_value_text", f"identity V1 / Y1: encoder value text {i}") for i in (1, 2, 3)]:
                site = getattr(S, site_name)
                orig = asm(f"bl {getattr(S, target):#x}", site)
                if S.orig(site_name) != orig:
                    raise SystemExit(f"{site_name}: not the expected bl {target}")
                to = ss[fn] if fn else t1 if site_name == "PANE_TITLE_SITE" else b1
                patch(site, asm(f"bl {to:#x}", site), orig, f"Analog: {why}")
            # T0 (BE4.40 identity, J2): "WT1: " / "WT2: " -> "OSC1: " / "OSC2: " (the page's row prefixes: the pane titles) and
            # the mod destination format "WT%d: %s" -> "OSC%d: %s", in their own zero-padded slots; each string has one
            # literal reference (checked here on the base image: the named literal is the only aligned word holding the
            # string's address, and no movw / movt pair builds it), so nothing else of stock's is renamed
            t0 = (("STR_PREFIX_OSC1", "PREFIX_LIT_OSC1", b"OSC1: "), ("STR_PREFIX_OSC2", "PREFIX_LIT_OSC2", b"OSC2: "),
                  ("STR_DEST_OSC_FMT", "DEST_FMT_LIT", b"OSC%d: %s"))
            t0_refs = one_reference(base_img, {getattr(S, site) for site, _, _ in t0})
            for site, lit, text in t0:
                orig = S.orig(site)
                if S.orig(lit) != struct.pack("<I", getattr(S, site)):
                    raise SystemExit(f"T0: {lit} does not hold {site}")
                if t0_refs[getattr(S, site)] != [getattr(S, lit)]:
                    raise SystemExit(f"T0: {site} is referenced at {[hex(a) for a in t0_refs[getattr(S, site)]]}, not only by {lit}")
                if len(text) + 1 > len(orig) or orig[orig.index(b"\0"):] != bytes(len(orig) - orig.index(b"\0")):
                    raise SystemExit(f"T0: '{text.decode()}' does not fit {site}'s zero-padded slot")
                was = orig.split(b"\0")[0].decode()
                patch(getattr(S, site), text.ljust(len(orig), b"\0"), orig, f"Analog (identity T0): {was!r} -> {text.decode()!r}",
                      site=True)                         # (12 bytes: a site all the same, stock's under BE_DISABLE=analog)
            fmenv_matrix(base_img, patch, code, ss)
            # D1: the Wavetable pane class's paint slot -> an_wave_paint (the Shape dial on an Analog pane, else stock's paint)
            if S.orig("WT_PANE_PAINT_SLOT") != struct.pack("<I", S.WT_PANE_PAINT | 1):
                raise SystemExit("WT_PANE_PAINT_SLOT does not hold stock's WT_PANE_PAINT")
            patch(S.WT_PANE_PAINT_SLOT, struct.pack("<I", ss["an_wave_paint"] | 1), S.orig("WT_PANE_PAINT_SLOT"),
                  "Analog (identity D1): Wavetable pane paint -> the Shape dial on an Analog pane")
    if inc("drums"):
        with S.group("drums"):
            for slot, target, fn, why in (("NODE_VT_DESTROY", "NODE_DESTROY", "an_destroy", "Drums: handles (H3): node destroy drops its pads' entries"),
                                          ("NODE_VT_CLONE", "NODE_CLONE", "an_clone", "Drums: handles (H2): node clone carries its pads' knobs to the copy")):
                if S.orig(slot) != struct.pack("<I", getattr(S, target) | 1):
                    raise SystemExit(f"{slot} does not hold stock's {target}")
                patch(getattr(S, slot), struct.pack("<I", ss[fn] | 1), S.orig(slot), why)
            patch(S.INST_SAVE_SITE, asm(f"b.w {x1:#x}", S.INST_SAVE_SITE), S.orig("INST_SAVE_SITE"), "Drums: instrument XML save -> bento_drum_*")
            patch(S.INST_LOAD, asm(f"b.w {x2:#x}", S.INST_LOAD), S.orig("INST_LOAD"), "Drums: instrument XML load -> bento_drum_*")
            patch(S.CONTEXT_GET, asm(f"b.w {x3:#x}", S.CONTEXT_GET), S.orig("CONTEXT_GET"), "Drums: CONTEXT_GET answers the drum keys")
            patch(S.OBJ_CTOR, asm(f"b.w {h1:#x}", S.OBJ_CTOR), S.orig("OBJ_CTOR"), "Drums: handles (H1): object constructor -> an_forget first")
            # S6c-2: the instrument page. D5: the refresh entry (view r0) -> dr_refresh, which calls stock's body through
            # its own replay of the first instruction (m4_synth.c stock_refresh); D6 and the tick: vtable words.
            replays("INST_REFRESH", PUSH_R4_R11_LR)
            patch(S.INST_REFRESH, asm(f"b.w {ss['dr_refresh']:#x}", S.INST_REFRESH), S.orig("INST_REFRESH"),
                  "Drums: instrument page refresh (D5) -> dr_refresh (the Kick's keys in the Config row)")
            for slot, target, fn, why in (("INST_EV_SLOT", "INST_EVENT", "dr_page_event", "instrument page events (D6): knob edits"),
                                          ("INST_TICK_SLOT", "INST_TICK", "dr_view_tick", "instrument page tick: the Kick's CPU readout")):
                if S.orig(slot) != struct.pack("<I", getattr(S, target) | 1):
                    raise SystemExit(f"{slot} does not hold stock's {target}")
                patch(getattr(S, slot), struct.pack("<I", ss[fn] | 1), S.orig(slot), f"Drums: {why}")
            # H4: stock's child copy (a pad by its properties: clipboard copy / paste, instrument replace) -> dr_dup, which
            # calls stock's body through its replay of the first two instructions (m4_synth.c stock_dup)
            replays("CHILD_DUP", "push {r4, r5, r6, lr}; sub sp, #8")
            patch(S.CHILD_DUP, asm(f"b.w {ss['dr_dup']:#x}", S.CHILD_DUP), S.orig("CHILD_DUP"),
                  "Drums: handles (H4): a pad's child copy carries its knobs")
    s4.update({f"synth.{k}": v for k, v in ss.items()})
    s4.update({"synth.np_template": tpl_at if an else 0, "synth.np_template_n": tpl_n})
    s4.update({"synth.mx_dest": mx_at, "synth.mx_dest_n": mx_n,
               "synth.mx_dest_count": mx_at + len(mx_data) - 4 if mx_at else 0})
    if n2 is not None:
        s4.update({"synth.wrap_reg_build": n2, "synth.wrap_pane_title": t1, "synth.wrap_browse": b1, "synth.wrap_pr_dest": pr})
    if inc("drums"):
        s4.update({"synth.wrap_save": x1, "synth.wrap_load": x2, "synth.wrap_context": x3, "synth.wrap_ctor": h1})
    return len(code) + len(tpl_data) + (len(mx_data) if an else 0)   # X1: the destination table counts too


def note_on_case(status, two_data=True, native=False, guard=False):
    """Stock's MIDI-out Note On case (M4_NOTE_ON_CASE..M4_NOTE_ON_END) as a template, with this base's registers
    (BASE_SETTINGS m4_note_on: event word 3 channel, 4 note, 5 value, 2 port; the message at sp+4 in r5; the
    sink object). 1.6.8 recompiled the task: every case sets r5 itself and loads the sink from a literal.
    native=True: stock's own form (status 0x90), checked against the image; our copies load the sink with
    movw/movt (no literal pool) and, guard=True, drop a note above 127 as stock does for 0x5B / 0x5C on 1.6.8."""
    r = BASE_SETTINGS[S.module]["m4_note_on"]
    ux = lambda d, s_: f"uxtb.w {d}, {s_}" if s_ in ("r8", "r9", "r10", "r11", "r12") else f"uxtb {d}, {s_}"
    head = (f"cmp {r['note']}, #0x7f; bgt out; " if guard else "") + r["msg"]
    head += (f"mov r0, r5; bl {S.M4MIDI_BEGIN:#x}; movs r1, #{status:#x}; mov r0, r5; bl {S.M4MIDI_STATUS:#x}; "
             f"{ux('r1', r['ch'])}; mov r0, r5; bl {S.M4MIDI_CHANNEL:#x}; {ux('r1', r['note'])}; mov r0, r5; bl {S.M4MIDI_DATA:#x}; ")
    if two_data:
        head += f"{ux('r1', r['val'])}; mov r0, r5; bl {S.M4MIDI_DATA:#x}; "
    sink = "" if not r["sink_lit"] or native else f"movw {r['sink']}, #{S.M4MIDI_SINK & 0xFFFF:#x}; movt {r['sink']}, #{S.M4MIDI_SINK >> 16:#x}; "
    tail = (f"{ux('r2', r['port'])}; mov r1, r5; mov r0, {r['sink']}; bl {S.M4MIDI_SEND:#x}; "
            f"mov r0, r5; bl {S.M4MIDI_RESULT:#x}; mov r1, r0; mov r0, {r['sink']}; bl {S.M4MIDI_DONE:#x}")
    return head, sink, tail


def check_native_note_on(base_img):
    """Our handlers copy stock's Note On case: the template with 0x90 must be the image's bytes, the sink it
    loads must be M4MIDI_SINK, and the case must end in the loop head."""
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB as CT, CS_MODE_MCLASS
    md = Cs(CS_ARCH_ARM, CT | CS_MODE_MCLASS)
    r = BASE_SETTINGS[S.module]["m4_note_on"]
    native = bytes(base_img[S.M4_NOTE_ON_CASE - BASE:S.M4_NOTE_ON_END - BASE])
    head, _, tail = note_on_case(0x90, native=True)
    h = asm(head.rstrip("; "), S.M4_NOTE_ON_CASE)
    at = S.M4_NOTE_ON_CASE + len(h)
    if r["sink_lit"]:
        i = next(md.disasm(native[len(h):len(h) + 2], at), None)
        if not i or i.mnemonic != "ldr" or not i.op_str.startswith(f"{r['sink']}, [pc, #"):
            raise SystemExit(f"Note On case: no sink load at {at:#x}")
        lit = ((at + 4) & ~3) + int(i.op_str.split("#")[1].rstrip("]"), 0)
        if struct.unpack_from("<I", base_img, lit - BASE)[0] != S.M4MIDI_SINK:
            raise SystemExit(f"Note On case: the sink literal at {lit:#x} is not M4MIDI_SINK")
        at += 2
    t = asm(tail, at)
    if h + native[len(h):len(h) + at - S.M4_NOTE_ON_CASE - len(h)] + t != native:
        raise SystemExit("our copy of the Note On case does not match the native bytes")
    if S.M4_NOTE_ON_END != S.M4_LOOP:                          # 1.6.8: the case branches back to the loop head
        i = next(md.disasm(bytes(base_img[S.M4_NOTE_ON_END - BASE:S.M4_NOTE_ON_END - BASE + 4]), S.M4_NOTE_ON_END), None)
        if not i or i.mnemonic not in ("b", "b.w") or int(i.op_str[1:], 16) != S.M4_LOOP:
            raise SystemExit(f"Note On case: {S.M4_NOTE_ON_END:#x} is not a branch to M4_LOOP")
    jt0 = struct.unpack_from("<I", base_img, S.MIDI_JT - BASE)[0]
    if jt0 != S.M4_NOTE_ON_CASE | 1:
        raise SystemExit(f"MIDI_JT[0] {jt0:#x} is not the Note On case")


def m4_poly_handlers(base_img, img, patch, extend):
    """Poly AT (M4): MIDI task jump-table slots 0x5F / 0x60 -> copies of stock's Note On case."""
    guard = "-DBE_NOTES_ABOVE_127" in BASE_SETTINGS[S.module]["m7_flags"]    # 1.6.8: stock drops notes > 127
    head, sink, tail = note_on_case(0xA0, guard=guard)
    h = asm(head + sink + tail + f"; out: b.w {S.M4_LOOP:#x}", M4_POLY)        # 0x5F: Polyphonic Key Pressure
    check_branches(h, M4_POLY, {S.M4MIDI_BEGIN, S.M4MIDI_STATUS, S.M4MIDI_CHANNEL, S.M4MIDI_DATA, S.M4MIDI_SEND,
                                S.M4MIDI_RESULT, S.M4MIDI_DONE, S.M4_LOOP}, "M4 0x5F handler")
    if M4_POLY + len(h) > M4_POLY_LIMIT or BASE + len(img) != M4_POLY:
        raise SystemExit("M4 poly handler block layout")
    extend(M4_POLY_LIMIT, "M1 poly AT: extend image with erased (0xFF) flash for the M4 handler")
    check_native_note_on(base_img)
    patch(M4_POLY, h, b"\xff" * len(h), "M1 poly AT: M4 0x5F handler (Note On case copy, status 0xA0)")
    old = struct.pack("<I", S.M4_LOOP | 1)                     # unused slots point at the loop
    jt_5f = S.MIDI_JT + 4 * (S.EV_MIDI_POLY_AT - S.MIDI_JT_FIRST)
    if S.EV_MIDI_CHAN_AT != S.EV_MIDI_POLY_AT + 1:
        raise SystemExit("the channel pressure handler's slot must follow the poly pressure slot")
    patch(jt_5f, struct.pack("<I", M4_POLY | 1), old, "M1 poly AT: MIDI task jump table slot 0x5F -> handler")
    # M2-5b: event 0x60 {port w2, ch w3, value w4} -> Channel Pressure 0xD0 (two bytes)
    head, sink, tail = note_on_case(0xD0, two_data=False)
    hc = asm(head + sink + tail + f"; b.w {S.M4_LOOP:#x}", M4_CHAN)
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
