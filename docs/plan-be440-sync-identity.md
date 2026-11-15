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

## Track M: Shape modulation (M7, J4)

On an Analog oscillator, the modulation sum that stock applies to the Position parameter (key 0xC9, destination
"OSC1: Position" after J2) drives Shape instead: effective Shape = clamp(Shape + scaled Position modulation, 0..127).
The scaling maps the destination's full range onto Shape's. Design it from stock's Position param (PARAM_VALUE of the
osc's Position param object, the same helper the master rate uses for pitch): read the modulated value each block,
subtract the unmodulated base, and add it to Shape. Wavetable oscillators are untouched (bit-identical). Must show:
LFO -> Position on an Analog osc moves the pulse width (M), with no modulation the output is bit-identical to today,
the cost per block, and the black box counts it.

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
