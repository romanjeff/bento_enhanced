# Plan: mixed sync, Shape modulation and Analog identity (BE4.40, stock 1.6.8)

Drafted 2026-11-14 on 6adc04f (S6n P1). Ships in the same kit as P1: **BE4.40** (Jeff, 2026-11-14). The drum-track
plan's kits moved up to BE4.50+.

Evidence: a design workflow, one designer and two adversarial verifiers per track, all in the emulator on stock 1.6.8.
The full designs, verdicts and screenshots are kept in the session scratch (`wf/design_results.json`,
`wf/sync/`, `wf/identity/shots/`). Labels: **M** measured, **S** static, **I** inferred.

## Decisions (Jeff, 2026-11-14)

| # | Decision |
|---|---|
| J1 | Mixed sync (Wavetable OSC1 -> Analog OSC2): build it in 4.40 |
| J2 | Naming: OSC1 / OSC2 titles on every WT track, and mod destinations renamed to match (`WT%d: %s` -> `OSC%d: %s`, data only). Stock's "Osc + Config" tile stays |
| J3 | The dial shows only what the kernel plays: 0..48 is one "saw" sector, then saw > square > pulse. The five-shape dial comes with sine / triangle in the FM sprint |
| J4 | Shape modulation is **in 4.40**: on an Analog oscillator, the modulation routed to its Position destination drives Shape (LFO -> pulse width) |
| J5 | Sync's title is **"Sync 1>2"** (same key 0x1AB and token `bento_sync`) |

## Bugs found in BE4.40 (6adc04f) that this plan fixes

| Bug | Evidence |
|---|---|
| **B1. Sync never engages on the device, both-Analog included.** Stock renders WT oscillators at 2N (root vtable slot 19 = `movs r0,#2`; decimation 0x0807FCC0), so `an_osc` sees n = 256 > AN_EVENTS = 128 and every block takes the "long block: free-running" path. This is plan-analog-fm's F0 bug 1, never fixed. The tests ran at n = 128 with divider 1.0, a 48 kHz domain the device doesn't use | S, M (both verifiers, separately: sync_skipped on every block at n = 256) |
| **B2. The CPU indicator is invisible.** "Analog CPU nn%" and A7's "Analog" label are written into the No-WAV label at view+0x3114 / +0x3474, which belongs to the Granular waveform widget and is hidden in all five Wavetable-page states. test_synth_ui checked the text, not visibility | M (rendered) |
| **B3. A stale Analog hint stays on a Wavetable oscillator.** page_tick posts 0xC3 for a new handle only when its Type is 1 | M |

## Track S: sync (M7)

**Design: option A.** WT2's AnOsc keeps its own Q32 copy of OSC1's phase (`mphase`, `mrestart`; AnOsc 156 -> 164 B,
limit 180). The rate comes from stock's own pitch formula on WT1's fields (`pitch_oct` / `rate_of` factored out of
an_osc): bit-equal to stock's +0x1E8 in 588 of 588 cases (M). It works with No WAV, the classic silent-master patch.
The wraps are placed analytically (`an_master`, -O2) and fed to the existing AN_SLAVE path. A new hook on the osc
class's slot 2 (note start, 0x080ACBBC) restarts the copy on the same call that restarts stock's WT1. The slave
restarts with it (event at frame 0).

Required by the verifiers (all must be in):
1. **Chunk the render** when n > AN_EVENTS: per chunk of <= AN_EVENTS frames, `an_master` for that chunk's events,
   then the AN_SLAVE render. events[] does not grow (AN_EVENTS = 256 would make AnOsc 292 B). **The same chunking fixes
   both-Analog sync (B1).**
2. **Mid-note engage** (Sync turned on, WT2 Type -> Analog): seed `mphase` from stock's read-only kernel readout
   (idx + frac) / len (kernel+0xC4 / +0xC0 / +0xD4) when WT1 has a table, else continue from the free-running copy.
   `an_note` sets `mrestart` only when WT2's Sync == 1; clear it whenever msync is off. Measured today: 17-175 samples
   off until the next note, plus a spurious slave reset.
3. **No truncation bias:** round the Q32 increment to nearest (or a wider phase). Restate every number for the
   device domain (96 kHz, n = 256): exact above 187.5 Hz, not 93.75.
4. **A device-shaped rig** in test_analog: divider +0x108 = 2, 2N frames, decimated. Every sync test runs there.

Known remaining misalignments (accepted, M): WTOscReset off (stock's phase runs on, ours restarts) and OSC1's level
to 0 and back (stock's phase freezes). Both last until the next note, and both are audible only when OSC1 is.

Not built: the reverse case (Analog OSC1 master, Wavetable OSC2 slave). It is feasible (M), but it writes stock's
kernel fields, the resets alias, and the WT-bit-identical guarantee would go. Its row stays hidden.

Page: the Sync row ("Sync 1>2") shows whenever OSC2 is Analog, whatever OSC1's Type. It is hidden when OSC2 is
Wavetable.

Cost (verifier, device domain, M): synced WT2 +2.3k instructions per voice-block at note 60, +4.6k at note 120. 80
voices with high masters come to about 370k of the 1.28M-cycle block (I). This goes to the device CPU check, not a cap.

### Track S: M results (built, 2026-11-14)

Built as designed, with the verifiers' fixes 1-4. Rig: `test_analog` `Rig(dev=True)` (divider 2, 2N = 256 frames),
pinned to stock's engine init `WT_ENGINE_INIT` 0x0807F180 and to stock's voice path (`Device`: vt[19] sizing, the post
chain's decimation to N, 261.63 Hz at 48 kHz). Wraps and errors below are in 96 kHz oscillator samples.

| Item | As built | Evidence |
|---|---|---|
| B1 before | 7bde5a0: `sync_skipped` on 48 of 48 renders (osc rig), 80 of 80 (voice path); both new Device tests fail | M |
| Chunking | per 128-frame chunk; both Analog: chunk 0's wraps in WT2's events, chunk 1's in WT1's own (WT1 is never a slave), so AnOsc keeps one events[128]; > 256 frames: free, counted. Mixed: `an_master` per chunk, no limit | M: 256 = 2 x 128 within 1e-6 (both), wraps and phase exact (mixed) |
| Phase copy | 64-bit fraction (`mphase`:`mphase_lo`), advanced by the float's exact value (`an_inc64`, no helper): no truncation at any rate above 0.04 Hz (fix 3, wider phase rather than rounding) | M: Golden, 8 rates 20 Hz..21 kHz x steady / walk / vibrato x 400 chunks: phase bit-exact, no missing or extra wrap, each within 1/254 |
| Chunk edge | the last sample's wrap decided from the exact phase (a float time within rounding of the edge would lose a wrap or give it twice) | M: Golden edges (wrap on the edge, the sample before, +-1 LSB) |
| Rate | `rate_of(o1, pitch_oct(o1))`: the copy advances by exactly n x stock's +0x1E8 / 2048 | M: 384 cases note x fine x octave x pitch mod, 0 differ |
| Note start | S1: slot 2 0x080ACBBC -> `tramp_note` (an_note, then `b.w WT_NOTE_START`); acts only when WT2 is Analog with Sync 1 (fix 2); WTOscReset off: no restart, the copy is re-seeded | M; S (stock's note start bytes unchanged after it) |
| Seeding | on engage (Sync on, WT2 -> Analog, Reset-off note): (idx << (64 - log2 len)) + (frac << (32 - log2 len)), minus this block's advance (WT1 renders first) | M |
| AnOsc | 156 -> **168 B** (+8 phase, +4 flags; limit 180, FM's +4 still fits) | static_assert |

Alignment (worst |wrap - WT1 cycle start|, M, `impl_SM/meas.py`): notes 24..108 0.0016-0.0038; octave -2..+2
0.0017-0.0036; vibrato +-1 st 0.0020; slide -1 oct -> 0 0.0019; retrigger / new note 0.0018 / 0.0019, restarts at
frames 0, 4864, 9728 only; Sync on mid-note 0.0019, no restart; WT2 -> Analog mid-note 0.0019; **WTOscReset off
0.0019** (was 17.3 smp: now seeded); 4 s notes at 12 / 24: 0.0020 (no drift); No WAV note 48: period 733.878 smp
(stock's pitch table, 12-TET 733.873). Remaining (accepted): WT1 level 1 -> 0 -> 1, 273 smp until the next note.

WT1 bit-identical to stock with WT2 synced (output and its 0x1F0 B object, vibrato and a new note) (M). Unchanged
from 7bde5a0, bit for bit (M, `impl_SM/ident.py`): free Analog oscillators in every regime at n = 128 and 256,
both-Analog sync at n = 128, glide, a mixed voice with Sync off.

Cost, instructions per voice-block at n = 256 (M, test_analog Cost): WT2's render next to stock's WT1, Sync off -> on:
note 24 6454 -> 8791 (+2337), note 60 6460 -> 8855 (+2395), note 96 6502 -> 9188 (+2686), note 120 6658 -> 10262
(+3604); stock's WT1 render 7092 at note 60. Both Analog, all hooks: saw 13076, saw + sync 15651 (+2575), mixed + sync
22046, saw + sync at note 120 16580. `an_master`(128): at most 1930. Code: DSP 7000 -> 8864 B (default build; 14924 B
with drums, of the 36 KB window); synth UI 10806 -> 10858 B; writes 100 -> 101. Black box: `synced=` counts synced
renders. Not measured on the device: n_max, the CPU check.

M4: `want_row` shows Sync on OSC2's row whenever OSC2 is Analog (the `osc1` argument kept, unused, so `fix_rows` is
untouched); the Sync descriptor title is "Sync 1>2" (J5); `an_print` prints `synced=` and `shape_mods=`.

## Track M: Shape modulation (M7, J4)

On an Analog oscillator, the modulation sum that stock applies to the Position parameter (key 0xC9, destination
"OSC1: Position" after J2) drives Shape instead: effective Shape = clamp(Shape + scaled Position modulation, 0..127).
The scaling maps the destination's full range onto Shape's. Design it from stock's Position param (PARAM_VALUE of the
osc's Position param object, the same helper the master rate uses for pitch): read the modulated value each block,
subtract the unmodulated base, and add it to Shape. Wavetable oscillators are untouched (bit-identical). Must show:
LFO -> Position on an Analog osc moves the pulse width (M), with no modulation the output is bit-identical to today,
the cost per block, and the black box counts it.

### Track M: M results (built, 2026-11-14)

| Item | As built | Evidence |
|---|---|---|
| Source | stock's Position parameter (osc+0x30): PARAM_VALUE 0x080A09A0 sums amount x source+4 over the slots in its order bytes (+0x28) and clamps; slot 0 is the knob (the ctor's source at osc+0x28, value = key 0xC9 / 256), slots 1..4 the routed sources (PARAM_CONNECT 0x080A0920 via the osc class's slot 10 0x08081B60, key 0xC9) | S; M (routing reaches slot 2, order [0, 2]) |
| Rule | effective Shape = clamp(Shape + 127 x (sum over slots 1..4), 0..127); Position's 1.0 (frames 0..255) = Shape's 127 | M |
| Deviation | the plan says "PARAM_VALUE minus the base". Built: the same sum without slot 0, unclamped. PARAM_VALUE clamps base + modulation to -1..1, so a Position knob left anywhere (hidden on an Analog page) would bend the modulation's range; this way it does not | M: knob at 200 gives the same output as no knob |
| Pulse width | Square (88) + 0.2: duty 0.226 as computed (+-0.01); an LFO sweeps the duty over 0.2+; amount x source as stock; clamps at 0 and 127 bit-equal to Shape 0 / 127 | M (ShapeMod) |
| Bit identity | none routed, a source at 0, the knob alone: Shape as stored, bit for bit (a whole Shape through the float an_shape gives the integer version's results) | M; and unchanged from 7bde5a0 (ident.py) |
| Wavetable osc | untouched: Position modulation on a Wavetable osc is stock's, output and object | M |
| Cost (n = 256, one Analog osc render) | none routed 8357 (7bde5a0 8307: +50 incl. the sync checks); one source at 0 8378; one moving 8390; four 8452 | M (impl_SM/jcost.py) |
| Black box | `shape_mods=` counts renders whose Shape was moved | M |

## Track I: identity (M4)

| Item | What | Site / note |
|---|---|---|
| T0 | Title prefixes "WT1: " / "WT2: " -> "OSC1: " / "OSC2: " (data) and mod destination format `WT%d: %s` -> `OSC%d: %s` at 0x0818EFD8 (12-byte slot), same commit | literal users 0x0816144E / 0x08161454; check that DEST_NAME output is display-only; inside the analog group |
| T1 | An Analog oscillator's title reads "OSC1: Analog"; the first Analog one adds "DSP nn%" (the M7's **total** load, so not "CPU") and "OVER" on overruns | hook bl 0x0815F96C with an asm shim passing the handle from the caller's frame `[sp,#0x32C]` (the view's row handle isn't written yet there: M). One composer `title_text()` used by the hook **and** by the tick every 250-500 ms (the hook runs only on refreshes, M). SLOT_TITLE marks only on change |
| T2 | Remove the writes into the hidden No-WAV label (B2); A7 keeps only what is visible | |
| B3 | page_tick posts 0xC3 for every row whose handle changed, whatever its Type | |
| D1 | Shape dial in the pane paint (0x081B6914): rect-fill-only drawing (stock FILL 0x08168D62), J3 sectors, glyphs lit by blend weight (none mid-morph), a vertical edge on the saw glyph | on an Analog pane: skip stock's frame paint (no draw-then-cover), children only; else tail-call stock |
| D2 | Redraw: our own last-drawn cache {view, handle, type, shape} in AN4 (stock's pane dirty flag is cleared before paint and shared page-wide: M/S); invalidated in an_page_keys | rate-limited (~30/s) |
| B1' | Double-tap on an Analog pane: don't open the WAV browser. Filter the browser outcome (app 0x7C handler 0x0814D48C, action 0x13B) for an Analog osc handle, not the press (0x79/0x7A are press/release, and 0x7C also selects) | device check: which gesture is which |
| Y1 | A Type value other than 0/1 shows "Wavetable" (no write: a future value survives a save), gated on the shown row 0/1 and slot 3 = Type | |
| V1 | Shape value text: "Saw", "Saw>Sq 40%", "Square", "Pulse 30%" | descriptor formatter if stock has one, else the tick's value label |
| H1 | Hide WTOscReset (0xC6, row 2) when both oscillators are Analog | rows heal, as fix_rows |
| W1 | A WAV still attached to an Analog osc: the title says so ("OSC1: Analog (WAV kept)") | |

Everything is patched inside the analog group, with stock_168 / facts entries (fingerprints over the T0 literal users
and the vtable words). `BE_DISABLE=analog` must stay stock. Budget: about 2 KB of the 21 KB.

Still to look at (M or S recorded in the doc when done): the browser popup's title, Shift functions on the WT page,
preset / track default names after a WAV load, the "%s: Wavetable" header screen.

## Tests

- test_analog: the device-shaped rig (B1); chunked n = 256 equals two 128 renders; mixed sync within 0.01 smp of WT1's
  cycle starts at notes 24..108, with vibrato, glide on WT1, octave -2..+2, retrigger and legato through slot 2; Sync on
  mid-note and Type switch mid-note (seeded); WT1 bit-identical; both-Analog sync at n = 256; two-voice isolation; J4
  modulation; cost.
- test_synth_ui: visibility (the hidden flag up the parent chain) for titles, dial and CPU; the CPU title updates over
  ticks with no refresh; stale-handle titles and hints (B3); Sync row per J5 / J1; Y1; V1; H1; dial pixels at a few
  Shape values; B1' outcome filter; T0 strings including the mod destination list.
- BE_DISABLE=analog stays stock for every new site.

## Device checks (checklist-v4.40.txt)

The P1 items, then: the black box `n_max=` (expect 256); both-Analog and mixed sync audible on a sweep; Sync on
mid-note; LFO -> OSC1: Position on an Analog osc moves the pulse width; OSC titles, "DSP nn%" live while playing;
the dial follows Shape; double-tap on an Analog pane; WAV loaded then Type -> Analog -> back; CPU with SPACED plus
synced voices; the 0xC3 notify from the tick under rapid Type switching.
