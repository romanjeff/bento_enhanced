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
(Both are fixed in the build: Reset off is seeded, and the review fix below re-seeds after a freeze.)

Not built: the reverse case (Analog OSC1 master, Wavetable OSC2 slave). It is feasible (M), but it writes stock's
kernel fields, the resets alias, and the WT-bit-identical guarantee would go. Its row stays hidden.

Page: the Sync row ("Sync 1>2") shows whenever OSC2 is Analog, whatever OSC1's Type. It is hidden when OSC2 is
Wavetable.

Cost (verifier, device domain, M): synced WT2 +2.3k instructions per voice-block at note 60, +4.6k at note 120. 80
voices with high masters come to about 370k of the 1.28M-cycle block (I). This goes to the device CPU check, not a cap.
Worst case (review M7, M, `rev_m7_2/cost2.py`, n = 256): with the master's increment at the 0.45 clamp (note 127, WT1
octave +2, pitch mod +2) `an_master`'s per-wrap loop and the slave's resets double that: mixed WT2 +7.1k (14100 vs 7043
free; Shape 100 +7.2k, 17351 vs 10139), both-Analog voice +5.2k (19940 vs 14711). 80 voices so: about 570k (I).

### Track S: M results (built, 2026-11-14)

Built as designed, with the verifiers' fixes 1-4. Rig: `test_analog` `Rig(dev=True)` (divider 2, 2N = 256 frames),
pinned to stock's engine init `WT_ENGINE_INIT` 0x0807F180 and to stock's voice path (`Device`: vt[19] sizing, the post
chain's decimation to N, 261.63 Hz at 48 kHz). Wraps and errors below are in 96 kHz oscillator samples.

| Item | As built | Evidence |
|---|---|---|
| B1 before | 7bde5a0: `sync_skipped` on 48 of 48 renders (osc rig), 80 of 80 (voice path); both new Device tests fail | M |
| Chunking | per 128-frame chunk; both Analog: chunk 0's wraps in WT2's events, chunk 1's in WT1's own (WT1 is never a slave), so AnOsc keeps one events[128]; > 256 frames: free, counted. Mixed: `an_master` per chunk, no limit | M: 256 = 2 x 128 within 1e-6 (both), wraps and phase exact (mixed) |
| Phase copy | 64-bit fraction (`mphase`:`mphase_lo`), advanced by the float's exact value (`an_inc64`, no helper): no truncation at any rate above 0.04 Hz (fix 3, wider phase rather than rounding). It equals stock's phase exactly while stock's per-sample step rate / D is at least 0.5; below that (a note under about 23 Hz at D 1, or pitch modulated down more than about 0.3 octave at D > 1: D is set at the note start) stock's reader rounds its f32 fraction (0x08050E6A) and the copy differs by that | M: Golden, 8 rates 20 Hz..21 kHz x steady / walk / vibrato x 400 chunks: phase bit-exact, no missing or extra wrap, each within 1/254. Below 0.5: 1.1e-6 smp after 8 s at note 12, 6.8e-8 at note 60 held 1.5 oct down (M, `rev_m7_1/m8.py`); under 1e-3 smp an hour (I) |
| Chunk edge | the last sample's wrap decided from the exact phase (a float time within rounding of the edge would lose a wrap or give it twice); a wrap in the last sample also clears the sample before, where the float time can put it a hair early (review M7) | M: Golden edges (wrap on the edge, the sample before, +-1 LSB, and 1..2^40 units after the last sample's start); `rev_m7_1/m10.py` 4000 cases: 442 doubled before the fix, 0 after |
| Rate | `rate_of(o1, wt1_oct(o1))`: the copy advances by exactly n x stock's +0x1E8 / 2048. `wt1_oct` undoes PARAM_VALUE's cache store (param+0x3C, 0x080A0A52 `vstr s0, [r0, #0x3c]`: WT1+0xAC), which stock's render never makes with no WAV (review M7) | M: 384 cases note x fine x octave x pitch mod, 0 differ (blocks right after a frozen kernel are checked as seeds); WT1's object stock's byte for byte with no WAV too (was: +0xAC differs, `rev_m7_2/probe.py`) |
| Freeze | stock's WT1 kernel does not run at level 0 with the smoother at rest (0x08081D22..38; an envelope or LFO can clamp OSC1 Level to 0) or with no WAV (0x08081C64). `watch`: a digest of WT1's kernel phase after its render, per block (`msnap`, 4 B); unchanged = frozen, the copy runs on (the classic patch keeps its wraps); changed after frozen = running again, the copy is seeded from it (review M7) | M: `rev_m7_1/m2.py` WT1 level 0 for 6 blocks then back 179 -> 0 smp, WAV loaded mid-note 8.57 -> 0; Sync test_stocks_kernel_running_again_reseeds_the_copy |
| Far above the mip | with WT1's pitch about 4 octaves or more above its note start's mip (rate / D over about 32: fine tune near +50 st plus octave and modulation), stock's kernel phase steps whole table cycles per block and its index can leave the table (idx -69 seen); the copy then follows the formula and is seeded when stock's phase moves again in range | M (test_the_master_rate_is_stocks, `fix_m7/cap2.py`); not reached with fine 0: octave +1.5 and pitch mod at its +2 oct clamp give rate / D 7.9 (M; octave +2: about 11, I) |
| Note start | S1: slot 2 0x080ACBBC -> `tramp_note` (an_note, then `b.w WT_NOTE_START`); acts only when WT2 is Analog with Sync 1 (fix 2); WTOscReset off: no restart, the copy is re-seeded | M; S (stock's note start bytes unchanged after it) |
| Seeding | on engage (Sync on, WT2 -> Analog, Reset-off note): (idx << (64 - log2 len)) + (frac << (32 - log2 len)), minus this block's advance (WT1 renders first) | M |
| AnOsc | 156 -> **172 B** (+8 phase, +4 flags and Track M's ramp Shape, +4 the freeze digest; limit 180, FM's +4 still fits) | static_assert |

Alignment (worst |wrap - WT1 cycle start|, M, `impl_SM/meas.py`): notes 24..108 0.0016-0.0038; octave -2..+2
0.0017-0.0036; vibrato +-1 st 0.0020; slide -1 oct -> 0 0.0019; retrigger / new note 0.0018 / 0.0019, restarts at
frames 0, 4864, 9728 only; Sync on mid-note 0.0019, no restart; WT2 -> Analog mid-note 0.0019; **WTOscReset off
0.0019** (was 17.3 smp: now seeded); 4 s notes at 12 / 24: 0.0020 (no drift); No WAV note 48: period 733.878 smp
(stock's pitch table, 12-TET 733.873). WT1 level 1 -> 0 -> 1 (was 273 smp until the next note) and a WAV loaded
mid-note: 0.0018 after the review fix (re-seeded, M). Sync on -> off -> on mid-note: 0.0019, no restart (M).

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
| Ramp (review M7) | a Shape that changed since the last block (modulation or the knob) ramps over the block: a step every AN_RAMP = 32 frames, linear from the last block's Shape, the last part at the new Shape exactly; each step's jump wave_new(p) - wave_old(p) (a pulse edge too, where the width moved past p) corrected at fraction 0 (`an_reshape`, half into the sample in `delayed`). A modulated Shape is held to 1/512 of a step (`mshape`, u16); a whole Shape stays exact, so no modulation is still bit-identical. No ramp after a silent block | M: the state after every block is the kernel's driven that way, bit for bit, output to 1e-5 (ShapeMod test_a_shape_change_ramps_over_the_block) |
| Ramp quality | error against a step every sample (`fix_m7/zip.py`, note 48): saw > square, Shape 68 +- 19, LFO 2 / 8 Hz: rms 1.0e-2 / 3.9e-2 (a step per block) -> 1.3e-3 / 4.8e-3; pulse, Shape 100 +- 25: 9.4e-2 / 1.8e-1 -> 1.8e-2 / 6.1e-2. Left: in the pulse regime an edge that the width moves past lands on the 32-frame grid (2-sample corrected, not at its exact time: up to about dw / inc, ~3 smp at 130 Hz with an 8 Hz deep PWM), so the pulse peak error stays about 2 | M |
| Cost (n = 256, one Analog osc render) | none routed 8357 (7bde5a0 8307: +50 incl. the sync checks); one source at 0 8378; one moving 8390; four 8452. With the ramp (`fix_m7/cost.py`, note 60): steady +58 (Shape 68: 9994 -> 10052); Shape moving every block +1.1..1.5k (Shape 68 under an LFO 10006 -> 11471, Shape 100 9971 -> 11093), only while it moves | M (impl_SM/jcost.py; fix_m7/cost.py) |
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

### Track I as built (2026-11-14, emulator only, not flashed)

Sites (all in the analog group; facts.py REVIEWED, stock_168 regenerated, `absent` in stock_161; M = test_synth_ui):

| Item | Site | Result |
|---|---|---|
| T0 | 0x0818F144 / 0x0818F14C "OSC1: " / "OSC2: ", 0x0818EFD8 "OSC%d: %s" (zero-padded slots; build.py checks each one literal, 0x08161504 / 08, 0x08159C8C) | M: titles "OSC1: ", "OSC2: Vocal Formants"; stock's DEST_NAME run on the image gives "OSC1: Position", "OSC2: Level". The packed "WT1: %s" / "WT2: %s" at 0x0818EAB8 belong to another table (0x081A0D18, users 0x081347D4 / 0x08159408), not DEST_NAME's: left stock (S). Review: that table is a name list, {type 6, oscillator, key (3 / 0xC4 / 0xC9), fmt +0xC, id +0x14 (0x13 / 0x14 / 0x15 / 0x1D ...)}; 0x0814D940 (37 entries) and 0x0814DA70 (23 entries, table 0x081A0F44) snprintf fmt with the key's title into 16-byte names at app+0x8ACC8 next to the id (S). Where it is shown, and whether the names are only displayed (not stored or compared), is not found: left stock, device check (the MIDI CC / assign list: "WT1: Pitch" next to "OSC1" elsewhere) |
| T1 / W1 | bl 0x0815F96C -> shim `ldr.w r2,[sp,#0x32c]; b.w an_pane_title` | S: [sp+0x32C] is the handle stock reads key 0xC9 with next (0x0815F97E). One composer `title_text()` for the hook and the 250 ms tick: "OSC1: Analog", "  DSP 63%", " OVER" (2 s); a kept WAV: " +WAV" and one space next to the load, " (WAV kept)" without it (OSC2 while OSC1 is Analog). One form per state (review: the marker used to shrink as the figure grew); the longest, "OSC1: Analog +WAV DSP 100% OVER", is SLOT_TITLE's 31 characters. The hook takes OSC1's Type for OSC2's title from the selected track's first oscillator (the view's row handles are 0 / stale there, M). M: the figure moves over ticks with 0 hits on the site; the hook alone (no tick) titles right with stale row handles |
| T2 | PANE_TEXT_2 / 4 (the "No WAV" label) stock again; A7 keeps the hints (1 / 3 / 5 / 6): "" on an Analog pane | M: the label's parent 0x2E90 / 0x31F0 is hidden; titles and panes visible up the parent chain |
| B3 | page_tick: 0xC3 for every row whose handle changed | M: stale handles (6, 5) in a reused view: titles and hints right after one tick |
| D1 | 0x081B6914 (pane class slot 1) -> an_wave_paint | An Analog pane: stock's paint skipped, the pane rect filled with its own frame background (+0x2DD, 0xF1), the J3 dial, stock's pressed fill (0xF6) / frame kept, then the children (WIDGET_PAINT_KIDS). The title label is outside the pane rect (M: (6,236,632,30) below (11,271,622,142)). Glyphs (review): two cycles over 24 columns, amplitude 9; the saw rises (the kernel's 2p - 1) and falls back each cycle; square and pulse show both edges, the pulse high for 3 of 12 columns. Lit with 3/4 of the blend as before: Shape 89..97 ("Pulse 49..40%") lights the square glyph and 98..117 none (by design: the text is the exact reading, the glyph the nearest shape). One draw: 96,446 instructions, 156 fills (M, emulator; DMA2D time not modelled) |
| D2 | AN4 cache {view, handle, key, t} per pane | The tick marks the pane's partial byte (+0x32) when the dial is out of date (at most every 33 ms). A pane that leaves Analog, and a page build (0xA8, a track change) of a view that showed a dial, ask for a full repaint: both bytes (+0x31: the root clears the screen on ctx+0, 0x081701F8; +0x32: stock's frames draw only with ctx+1, 0x0818455E, S) and both panes' frames marked (WT_PANE_FRAMES_DIRTY 0x0818497A: stock draws a frame only while its +0x19 byte is set and clears it once drawn, 0x081845AE..0x08184672; review: the other pane's wavetable stayed blank after the clear, M). Every other widget of the view draws again on a full repaint (M: the view painted twice from a cleared screen, the same 140,413 px). A hidden pane draws afresh when shown. M: no fill on an unchanged partial repaint; OSC2's frames drawn again after OSC1 leaves Analog (frame fetch stubbed, test_synth_ui Dial) |
| B1' | bl 0x0816108A (UI_POST of 0x3F) -> shim `ldr r2,[sp,#0x20]; b.w an_browse` | Deviation: 0x0814D48C / 0x13B is stock's MIXER_EVENT (the mixer strips' toggle, S), not the oscillator's path. The synth view's 0x7B (ev+8 = the oscillator) on a Wavetable track posts 0xA8 {osc} then 0x3F, which the app turns into its page 0x14 (S). 0x7B is a double-tap (S): the press widgets 0x0816C340 / 0x08187040 post 0x79 {id}, then 0x7B {id} when the press comes within 0x1F4 ms of the last (cmp.w r3,#0x1f4 at 0x0816C362 / 0x08187044, time 0x300378F8). For an Analog oscillator the 0x3F is not posted (the select and the pane's 0x79 / 0x7C press stay stock's, M). The header's 0xEE / 0xEF (-> 0x1D, page 0xD) and the menus' "Load WAV" stay open: explicit loads (W1 shows them) |
| Y1 | tick, before and after stock's tick; VALUE_TEXT_1..3 | M: Type 7 / -1 / 0x101 read "Wavetable"; the model keeps the value, no 0xD0. Review: stock's two-label widget held 7 at entry 1 ("Analog": right did nothing, left wrote 0). The tick puts it at entry 0 (ENC_POS_SET on +0x80, +0x98 last index and +0xA0 value = 0; ENC_TURN posts only when the index moves off +0x98 and the value differs from +0xA0, 0x08173FE0..FFA, S): right writes 1, left nothing. M: also right after a row switch |
| V1 | VALUE_TEXT_1..3 (0x08173824 / 0x08173AE2 / 0x08173CFC: the encoder widget's bl SLOT_TITLE of its value text) -> an_value_text; and the tick | M: "Saw", "Saw>Sq 40%", "Square", "Pulse 37%" (blend and width from an_shape / the kernel; AN_SHAPE_SAW_END / AN_SHAPE_SQUARE now in synth_shared.h for both cores, DSP bytes unchanged, M). Review: stock's bind and 0xC3 refresh wrote "64" / "Analog" until the next tick (a frame's flash if a paint came between). The hook writes ours at the source; on a bind the widget still holds the old row's handle when its text is written (M), so the object is the shown row's (byte view + [view+0x2434] + 0x11D88, SYNTH_ROW_BIND 0x08160A50..5E, S). Every encoder widget's text passes the hook; only a synth view's on the Wavetable page is changed |
| H1 | fix_rows, row 2 | M: 0xC6 leaves row 2 only while both are Analog; stock's bind shows an empty slot |

Size: synth UI 10,806 -> 13,728 B; image 0x1880A0 -> **0x188C08** (default), 0x18A5E0 with BE_ENABLE=drums; proven max
0x18D334 (18,220 B / 11,604 B left). About 2.9 KB, not the 2 KB planned: title_text 756 B, an_wave_paint with the dial
1,230 B. The 1.6.1 build is byte-identical to v3.72.

Still-to-look-at, recorded:
- Browser title (S, partial): 0x3F opens page 0x14 through 0x08143D44; the page title generator 0x081435BC picks among
  "%s: Wavetable" / "%s: Granular" / "%s: WAV %d" by object type (0x08143718..0x08143B70); no oscillator name is in it.
  The popup's own text is a device check (B1' keeps it from opening on an Analog pane).
- Shift functions on the WT page (S): the synth view's handler 0x08160F00 has no button (0x73) case: 0x0B / 0x0C, 0x79
  (row select while +0x117D8), 0x7A, 0x7B (loads, B1'), 0xCD (the header's 0xEE / 0xEF / 0x16 buttons), 0xCF, 0xED,
  0xEE / 0xEF (load), 0x111 / 0x112. Shift combinations are the controls layer's, not the page's.
- Preset / track names after a WAV load (S, partial): a WAV reaches the page only as the oscillator's Filename (key 0x81,
  the title); "UserPatches\Wavetable" is the preset folder (0x08142D6C). Whether a load renames the track or preset is
  not found statically: device check.
- "%s: Wavetable" (S): the synth page's header title, chosen by the track's type 0x12 at 0x0814372E (also 0x0814ED10).
  Per track, not per oscillator: left as is (an Analog oscillator is a property of a Wavetable track).

Tests (WSL emulator): test_synth_ui 51 (identity: Sites, Disabled, Panes, Titles, Indicator, Visibility, Dial,
ValueTexts, ResetRow, Browse, DestNames), every suite green on the default build; on BE_ENABLE=drums all green except
test_inst_xml (3) and test_handles (10), which fail identically on 7bde5a0; BE_DISABLE=analog: every new site stock
(including the 12-byte T0 string: a site, not a block), test_synth_ui Disabled green. PNGs: scratch wf/impl_m4/shots.

Device checks added: the dial redraw on the device's paint (partial / full, no flicker, M4 load while turning Shape);
which widget sends 0x7B (a double-tap, S) over the pane and that page 0x14 is the WAV browser; the press highlight on an
Analog pane; the title fits on the 632 px label with "+WAV" and "OVER"; a Type switch with a WAV loaded leaves no dial
behind.

Review fixes (rev_m4_1 / rev_m4_2; scratch wf/fix_m4): D2 full repaint marks both panes' frames (major: the other
pane's wavetable stayed blank); a page build of a view with a dial asks for the full repaint (track change Analog OSC1 ->
Wavetable OSC1); glyphs redrawn (rising saw, two cycles, both edges, pulse 3 px); W1 one title form per state; OSC2's
hook title takes OSC1's Type from the track; Y1 the Type widget at entry 0; V1 / Y1 at the widget's own text writes
(VALUE_TEXT_1..3, no one-frame "64" / "Analog"; stock's static constructors write encoder texts too, so the hook finds
the synth view before it reads AN4: M, test_handles' cold boot, no AN4 write before the first tick); build.py checks T0's single reference on the base image (aligned words
and movw / movt pairs). Device checks from the review: switch rows with Shape 64 and Type 7 (no flash of the number); a
track change from an Analog OSC1 to a Wavetable OSC1 leaves no dial; OSC2's wavetable stays drawn when OSC1 leaves
Analog; Type 7 turns right to Analog and left to nothing; where the "WT1: Pitch" name list (app+0x8ACC8) is shown.

## Tests

- test_analog: the device-shaped rig (B1); chunked n = 256 equals two 128 renders; mixed sync within 0.01 smp of WT1's
  cycle starts at notes 24..108, with vibrato, glide on WT1, octave -2..+2, retrigger and legato through slot 2; Sync on
  mid-note and Type switch mid-note (seeded); Sync on -> off -> on and stock's kernel frozen then running (re-seeded);
  WT1 bit-identical, with and without a WAV; both-Analog sync at n = 256; two-voice isolation; J4 modulation and its
  ramp; cost. Long sample lists are compared with `same()` (the first difference named: assertEqual's diff of 8192
  floats never returns). The suite skips on a `BE_DISABLE=analog` image (the DSP ELF is still in it).
- test_synth_ui: visibility (the hidden flag up the parent chain) for titles, dial and CPU; the CPU title updates over
  ticks with no refresh; stale-handle titles and hints (B3); Sync row per J5 / J1; Y1; V1; H1; dial pixels at a few
  Shape values; B1' outcome filter; T0 strings including the mod destination list.
- BE_DISABLE=analog stays stock for every new site.

## Device checks (checklist-v4.40.txt)

The P1 items, then: the black box `n_max=` (expect 256); both-Analog and mixed sync audible on a sweep; Sync on
mid-note; LFO -> OSC1: Position on an Analog osc moves the pulse width; OSC titles, "DSP nn%" live while playing;
the dial follows Shape; double-tap on an Analog pane; WAV loaded then Type -> Analog -> back; CPU with SPACED plus
synced voices; the 0xC3 notify from the tick under rapid Type switching. Review M7 additions: PWM with a 2..8 Hz LFO
(listen for a zipper or clicks: the Shape ramp steps every 32 frames); mixed sync with an LFO or envelope taking OSC1
Level to 0 and back (the slave stays locked); the CPU check also as a high sweep on WT1's pitch with many synced voices
(the clamped-increment worst case, about 570k).
