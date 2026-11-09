# Plan S7: a Drum Synth track (no carrier WAVs) on stock 1.6.8

Status: investigation and design only (2026-11-09). Nothing was built, flashed or committed for it. The code
is unchanged since fbd0443 (BE4.31). HEAD f47ac54 adds only docs.
Inputs: two read-only investigations, M7 (audio side) and M4 (UI and model), both running stock 1.6.8 code in
the emulator. Their scripts and logs are kept in the scratch folders `rc31/m7/` and `rc31/m4ui/`, listed in
section 12.

Labels: **[M]** measured in the emulator on stock 1.6.8 bytes (and on BE4.31 / BE4.22 where it says so).
**[S]** read from the 1.6.8 disassembly. **[I]** inferred, not yet shown.

**Adversarial verification (2026-11-09, rc31/vfy).** Every load-bearing claim was re-derived with new scripts
(not the investigators'): an M7 run on stock's real one-shot path with a different binder (a Python hook at
the call site) and provider, a new M4 project-load harness that lets the track pass and stock's sample-manager
enqueue run to the end, a registry dump, a fresh BE4.22 build, and the disassembly. Corrections are marked
**[VFY]** in place. Summary: the GO stands. Two claims were overstated (BE4.31 keeps drum attributes only for
Kick pads; the prototype's "D1 renders the Kick" ran through the carrier detector, not the final detection).
One new gate was found (a note-off message that carries desc+4, gate 6). The menu row's "kind→type table" is a
function. Evidence: section 12.

## 0. The short answer

**GO, with one gate sprint first.** A cleanly separated Drum Synth track can be built. It has its own
instrument-menu entry, 16 pads with no files, a drum-only edit page on every pad, One-Shot's own step
sequencer, and mono pads that re-strike. It is **not** a new track type. It is a One-Shot track that we flag
as ours, the way Analog is a flagged Wavetable track. This is the only way to get the *same* sequencer, and it
is also the lower-risk road (section 9).

The carrier-WAV approach goes away completely: no WAV, no card file, no file browser step, no detection by
file name or by sample content.

Six things are still open [VFY: was five; item 6 is new], and any of them could turn the GO into "revisit". They are cheap to settle
offline, and Sprint S7-0 settles them before any feature code is written:
1. What Pack does with a pad whose Filename is `""`.
2. What stock does when you tap a pad whose sample child has an empty filename: the pad page, or the file
   browser.
3. What stock's sample manager does with load command 9 for `""`: a silent failure, or a message on the
   screen.
4. Whether the M4 slice has room for 128 pad entries (8 tracks × 16 pads).
5. What the pad grid draws as the name of an empty pad.
6. **[VFY, new]** What stock's note-off message 0x1C does with desc+4 (section 3.3). [S] VOICE_NOTE_OFF
   0x08095140 sends it at 0x080951C0..0x0809520C (`ldr r3,[ip,#4]`: desc+4, plus root+0x670/0x672), for a
   voice whose root+0x100 == 3. root+0x100 is set to (value + 1) by a pad property at 0x08091046 [I: LaunchMode;
   One-Shot pads default to 0 → 1]. The fix is cheap: desc+4 gets a sentinel that is never an asset handle.

**[VFY] Gates 1-3 and 5 concern a state stock itself makes.** A device-written project (sd_backups
2026-11-08 SPACED, line 986) holds a `samasst` with `filename=""` that kept its old `samlen`: a sample child
whose file was removed (on a Loop track) [M, file]. The drum pads use the same state. So the cheapest S7-0 step
is a device check on **stock behaviour**, with no build: Unload a One-Shot pad, then tap it, Pack the project,
and reload it. [I] that Unload on a One-Shot pad leaves the same `saminst` + `samasst("")` pair: the Unload
event 0x102D is posted at 0x08154E08, and its model side was not traced.

Section 10 says what would make it NO-GO.

## 1. Item by item

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | A "Drum Synth" entry in the instrument menu creates the track | **GO** | [S] The menu table at 0x0819AD30 has 20-byte cells. Analog uses index 8; index 9 (0x0819ADE4) is blank, so the count becomes 10. The handler posts {0x42, 0, kind 4, track}: kind 4 is stock's One-Shot (type 0x0E). Then we flag the new handle, as Analog's A5 does. **[VFY] confirmed.** [S] Stock's cells 8 and 9 are `{0, 0x0818C5F0, 0, 0, 1}` (empty, enabled); BE4.31 has Analog in 8 and the count patched 8 → 9 (MENU_COUNT), so a 10th cell is the same two writes. Cells 10 and 11 of the same table are Unload (0x102D) and Rename WAV (0x103B), so the count must stop at 10. Instrument replace 0x0813F730: `tbb` on kind-1, and kind 4 → `mov.w sb,#0xe` (0x0813F7E0). [M] Run on stock, kind 4 makes a new type 0x0E object. Where the 10th cell sits on the grid was not shown [I]. |
| 2 | 16 pads, each with no file | **GO** (gates 1-3, 5) | [M] A pad is two model children: `saminst` (type 1, position in 0x73) followed by its `samasst` (type 5, Filename 0x81). The sample child must exist, because stock's pad lookup 0x0815473C takes "the next child" as the pad's sample. With `filename=""`, stock still sends the M7 0xAC (a root for the pad), the sample-index record and 0xBF, and asks its sample manager for cmd 9 `""`. The loader returned 1 on every variant, with no crash. **[VFY] confirmed, with stronger evidence.** The rc31/m4ui runs never finished the track pass: all of them, including the unmodified stock control, stopped in a full message ring (0x0810BC30 / 0x0810B898 wait loops) after the 16 pads. In a new harness, which drains the rings the way the M7 would and lets stock's cmd 9 enqueue 0x0810E384 run for real, the pass returns [M]. The control kit and the all-`""` kit give the same counts: 773 property sends, 0xAC ×16, 0xBF ×16, cmd 9 ×16. [M] **A new stock One-Shot track has no pads** (instrument replace kind 4: one child, type 0x18), so the builder is required. [S] The pad lookup 0x0815473C and the page select 0x081547EA (cell set, file set, 0xA8 / 0xA9 / 0x03) never read the Filename. |
| 3 | The pad plays a synth with no sample | **GO** | [M] On stock's real one-shot path: an empty pad is refused at voice start (run 1). With our binder at the lookup call, the voice starts, our D1 renders the Kick, and the voice ends by itself (runs 2, 3, 6). SAMPLE_FIND, stock's provider, the sample cache and the card are never touched. **[VFY] confirmed for stock's path; corrected for D1.** Re-run with a different binder and provider on stock 1.6.8 [M]. Unflagged: refused (lookup 1, find 1). Flagged: lookup 0 and find 0. The provider streams a 2000-frame 200 Hz sine, and stock's own render and post chain put it on the output (peak 0.5). The voice ends by itself after 16 blocks (= ceil(2000/128)). Provider slots used: 2, 6, 7, 9 only. Both start sites (0x08095656, 0x080949E2) give the same result. [S] Stock's lookup only stores the provider (0x08054EE0) and copies 0x24 B of info (0x08054E40): no refcount, no open. VOICE_START clears the descriptor (0x08054F20 at 0x08095572, start A at 0x0809476C) before the lookup, so the binder's 3 words are enough [M: 3-word and full 0x24-B binds identical, also on a reused descriptor]. **Correction:** the "D1 renders the Kick" runs (3, 6) worked only because the prototype provider served a carrier's signature samples, so BE4.31's *content* detector fired. With a zero-reading provider (the final design) BE4.31's D1 does not detect: carriers 0, drum starts 0, silent, ended after 38 blocks [M]. The provider-pointer detection is therefore [I] until S7-1 builds it. |
| 4 | The same step sequencer as One-Shot | **GO** | [S] The track *is* type 0x0E, and the Sequence button picks the drum Step page by `cmp r0,#0xe`. [M] Saved steps address pads by `chan` = pad position. Conditions, retriggers, probability, Note Mod and poly AT are upstream of the voice and unchanged. |
| 5 | Each pad mono, re-struck on a new hit | **GO** (needs a D1 change) | [M] Stock does not do this on its own: Poly 4 and Mono both leave two Kick instances ringing (run 4). [M] Our fix frees the old voice in the same block through stock's own end check (run 5; the cursor alone does not, run 5b). The design is one instance per pad (section 3.4 [VFY: was "4.4"]). **[VFY] release mechanism confirmed** [M]: release 0x08063E80(v+0x58), then cursor past the end, then one DRUM_POST gives env 4 and an inactive voice. Cursor only gives env 3, still active. Run 4 (two instances ring) was not re-run. |
| 6 | A drum-only edit page: all three rows per engine, engine select, no sample controls | **GO** (two RE items) | [S] The three 8-slot key rows (view+0x703C, stride 0x50) are written by D5/`dr_heal` today for one row, so all three is the same write from a 24-key table. Stock keys in the table (Level 0x02, Pan 0x04, sends 0x36/0x37, filter 0x32/0x33, Choke 0x31, Decay 0x1F) still go to stock through D6/X3. Still to find: the page event that opens the browser, Unload (0x102D) and Rename WAV (0x103B), all three to be dropped on drum pads. **[VFY] keys confirmed** [M, stock registry]: 0x02 Level, 0x04 Pan, 0x31 Choke Grp, 0x32 Filter, 0x33 Resonance, 0x36 Delay Send, 0x37 ReverbSend, 0x1E-0x21 A/D/S/R, 0x2E Poly Mode. The engine-key candidates 0x1A0, 0x1A2, 0x1A4, 0x1A7 and 0x1AB are all empty in stock. The three-row extension is [S] (same arrays, `row_keys(view,row)`); `dr_heal` today checks only row 1's first key, so it must check all three rows. Unload is posted from the pad page at 0x08154E08 / 0x0815420C (menu build), Rename at 0x0815419C. |
| 7 | project.xml saves it; other firmware opens it without crashing | **GO** | [M] Stock 1.6.8 and BE4.22 load the track as a plain One-Shot track with 16 empty pads and an intact sequence, with no crash. BE4.31 keeps `bento_drum_*` on the pads. Nothing reads a track flag yet, as expected. v3.72 / 1.6.1 was not emulated [I: same loader and XML library]. **[VFY] stock 1.6.8 and BE4.22 confirmed; BE4.31 corrected.** In the new harness (track pass run to the end), stock 1.6.8 and a fresh BE4.22 build (8d66cec) both load the project: 16 `saminst`/`samasst("")` pairs, no fault. The writer gives back all 36 seqevents, no `bento_*` attribute and `filename=""` [M]. **BE4.31 keeps the drum attributes only on engine-1 (Kick) pads.** With `bento_drum_engine="3"` and all of k1..k7, BE4.31 writes no drum attributes [M]. So a Snare / Clap / HiHat / Tom pad saved on BE4.31 loses its settings. BE4.31 also drops the track flag, so the whole track comes back on this build as a plain kit with silent empty pads. v3.72 / 1.6.1 is still not emulated. |
| 8 | A genuinely new track type instead | **NO** | Section 9. |

## 2. What Jeff sees (the user-facing design)

### Making one
- Press the instrument menu on an empty track. A tenth entry, **Drum Synth**, sits next to Analog on the third
  row.
- Picking it gives a track that looks like a One-Shot kit: 16 pads, all of them already playable. Defaults:
  pad 1 Kick, pad 2 Snare, pad 3 Clap, pad 4 Closed HiHat, pads 5-6 Tom low / high, the rest Kick. This is a
  proposal; the defaults are one table.
- There is no file to load and nothing to copy to the card.

### Sequencing
- The Sequence button gives One-Shot's drum step page, exactly as for a sample kit: 16 lanes, steps,
  conditions (incl. NOT / 1ST / PRE), probability, retriggers / ratchets, per-step velocity, hold-step edit,
  Note Mod and recorded aftertouch.
- Steps and pattern data are stock's, saved by stock. We add nothing to the sequencer.

### A pad's edit page
Select a pad and open its page. Instead of the sample page you get the drum page, three rows of eight knobs.
Sample controls (Start, Length, Reverse, Interp, Loop, Loop Fade, Loop Mode, LaunchMode, Slice) are not on it,
and the browser, Unload and Rename WAV are not offered on a drum pad.

| Row | Slots 1-8 | Notes |
|---|---|---|
| **1 Drum** | Engine, then the engine's own knobs (up to 7) | The knobs change with the engine. Unused slots are blank. |
| **2 Mix** | Level, Pan, Reverb send, Delay send, Cutoff, Resonance, Choke group, (8th: velocity amount if stock has a per-pad key for it, else blank) | All stock parameters, acting through stock's post chain on the synth exactly as on a sample. |
| **3 Env / LFO** | stock's envelope / LFO row, as it is today | It acts through the post chain (parity items 8, 14). Defaults on a new drum pad: Attack 0, Sustain full, so the engine's own decay shapes the hit. Decision D-S7-2. |

Row 1 per engine. The Kick is S6c-1's map. The others are DaisySP's or upstream's parameters; their exact
lists are settled in S7-4. Each engine fits in seven knobs:

| Engine | Knobs (slots 2-8) |
|---|---|
| Kick | Tune, Decay, Tone, Accent, Sweep, Sweep Dec, Dirt |
| Snare | Tune, Decay, Tone, Snappy, Accent, (FM amt), - |
| Clap | Tone, Decay, Spread, Accent, - , - , - |
| HiHat | Tune, Decay, Tone, Noise, Accent, Open/Closed, - |
| Tom | Tune, Decay, Tone, Sweep, Accent, - , - |

Other things on the page:
- **Engine** is a knob. Turning it changes the pad's sound and row 1's titles at once. Each engine keeps its
  own last knob values per pad (decision D-S7-3: one shared set of 7 values or per-engine memory; the
  shared set is simpler and is the default proposal).
- **The waveform pane** has no waveform. Its title reads the engine name and the CPU readout as today
  (`Kick CPU 63% drums 4%`). Later, optionally, an engine graphic, drawn with Analog's pane tick/paint
  hooks (A8).
- **Poly Mode is gone** from the page: pads are mono by design.

### Playing
- Each pad is mono. A new hit on a ringing pad re-strikes it: the click and envelopes restart and the body
  carries on, like a real drum hit again. This holds whatever stock's Poly setting would say, and for
  retriggers and ratchets.
- Level, pan, sends, filter, envelope, LFO1, velocity routes, choke between pads, pad mute and pitch from
  Note Mod all act as on a sample pad, because the synth runs inside the native voice.

### On other firmware
| Firmware | Opening a Drum Synth project | Saving it there |
|---|---|---|
| This build | the track as made | everything kept |
| Stock 1.6.8, BE4.22 [M] | a plain One-Shot track with 16 **silent** empty pads; the sequence is intact; no crash | drops our attributes: back on this build it is a plain One-Shot kit with empty pads (the drum sounds are lost; the sequence survives) |
| BE4.30 / BE4.31 [M load, I silence] | the same silent One-Shot track (no carrier, so no Kick) | keeps `bento_drum_*` on the pads but drops the track flag. **[VFY] corrected [M, BE4.31]:** it keeps them only on Kick (engine 1) pads and drops them on any other engine. Since the flag is dropped too, the track is a plain kit back on this build. |
| v3.72 / 1.6.1 [I] | expected the same as stock 1.6.8 (same loader and XML code); not emulated | expected the same |

There is one thing to tell users: saving a Drum Synth project on other firmware loses the drum settings.

## 3. Architecture

### 3.1 The shape
```
 M4 (UI)                                                  M7 (audio)
 menu "Drum Synth" -> One-Shot track + DT flag            stock One-Shot engine, 32-voice pool
   builder: 16 x {saminst 0x73=i, samasst filename=""}    0xAC: a root per pad (stock)
   stock track pass 0x0813E4B8 -> 0xAC / props / 0xBF     note-on -> VOICE_START 0x08095260
 DK4 table: pad handle -> {engine, knobs}                   bl @0x08095656 -> dr_bind(root, ...)
   publish only pads of flagged tracks  -----------------> knob lines: handle -> engine|USED, knobs
 drum page (D5/D6/tick): three rows per engine              synth pad: desc = {&dr_provider, engine}
 XML: bento_drumtrack on track, bento_drum_* on pads        else: stock's 0x08087D00 (sample)
                                                          D1 0x08092320: desc+0 == &dr_provider ->
                                                            per-pad instance, render, D4 post chain
```

### 3.2 Which pads are synth pads (the one fact the M7 needs from the M4)
- The truth is on the M4:
  - a **track flag table** (DT4, like AN4): track handle → flag;
  - the existing **DK4 pad table**: pad handle → engine and knobs.
- The M4 publishes a pad's line with `DK_USED` only when the pad is live, its parent track is flagged, and
  its sample child's Filename is `""`. The last condition is a safety net: if a sample ever lands on a drum
  pad by a path we did not block (for example a kit preset applied onto the track), that pad plays the
  sample rather than silently ignoring it. Decision D-S7-4.
- The M7 binder reads the published line at each voice start. This is the same scan as `knob_entry`, once
  per start and not per block. After an M7 reset the generation handshake republishes the lines, so the
  binding survives the reset.
- Rejected: tagging the pad through 0xBF. An M7 reset would lose the tag, and stock's own file loads would
  overwrite it. Also rejected: "no sample means synth", which would turn every empty pad on every kit into a
  Kick.

### 3.3 The M7 binder and provider [M prototype, final form I]
- Two call-only rewrites: the `bl 0x08087D00` at **0x08095656** (VOICE_START) and at **0x080949E2** (voice
  start A, message 0x4E). r5 = root at both [S, checked].
  - These are clean call-site swaps, not mid-function trampolines. That is the distinction rc25 B1 drew:
    no saved VFP state, no replayed instructions.
- `dr_bind(bank_pair, &id, desc)`:
  - If root+0x0C has a USED line: desc+0 = `&dr_provider`, desc+4 = engine, desc+0x24 = 0; return 1.
  - **[VFY] Change desc+4 to a sentinel, `0xD5000000 | engine`, never a bare 1..5.** Stock treats desc+4 as
    the sample's file id: its own lookup copies the asset handle there, and the handles are small numbers
    (44, 46, ... in the loads), so 1..5 can be a real asset's handle. [S] At least one stock path sends desc+4
    out: the note-off message 0x1C at 0x080951EA (gate 6). The provider ignores the file argument, and D1
    gets the engine from the knob line, so nothing of ours needs desc+4 to be a small number.
  - [VFY, S] Three words are enough. The descriptor is a plain value: stock's lookup stores the provider
    (0x08054EE0: `str r1,[r0]`) and copies 0x24 B of info (0x08054E40). It takes no reference and opens
    nothing, and both voice starts clear the descriptor (0x08054F20) before the lookup. [M] The 3-word and full
    binds behave the same.
  - Otherwise tail-call stock.
  - It ignores root+0x674, so a stale sample id on a reused root does not matter.
- `dr_provider` is one word in the synth area: a vtable in the DSP ELF.
  - info: CAP_FRAMES = 12 s, so stock's clamp arithmetic stays in its normal range.
  - rate: 48000. channels: 1. read: zeros. Every other slot returns 0.
  - [M] Only slots 2, 6, 7 and 9 were used across start, render and post.
- The record-buffer path (root+0x625: 0x0809590E, 0x080947F4) is not bound. A drum pad never records, so
  that path plays stock's record slot. Add the two sites only if a device check shows the path in use.
- Cost: about 200 B of DSP code and 4 B of synth area.

### 3.4 D1 and the mono re-strike [M mechanism, design I]
- D1 detects a synth voice by `desc->provider == &dr_provider`: one compare. This replaces the carrier
  detector (prefilter, mono, rate, 32-sample signature, magic).
- **Cells are keyed by root (pad), not by voice.** Each cell has an `owner voice` and the start serial it
  last saw. The pool stays at 32, because each live instance is owned by exactly one live voice.
- On a new start serial for root R on voice v_new:
  - If R's cell is playing on v_old: the owner becomes v_new and the instance is triggered again. The
    envelopes and click restart; the body state is kept.
  - If R has no cell: take a free one and zero its engine state. This is the only zeroing point, per Jeff's
    mono decision.
- An orphaned v_old, on its next render: write zeros, call stock's release 0x08063E80(v_old+0x58), set
  cursor = frames + 1, call D4. [M run 5] stock's end check frees it in that block (envelope state 4,
  voice[0] = 0).
- An engine change, or a different engine id in desc+4, re-inits the cell's engine.
- Loop on (root+0xEC) is ignored on synth pads: the voice ends when the synth is done.
- Known risk: the gain step at a re-strike (old voice envelope level → new voice attack), the same as a mono
  sample pad. If it clicks on the device, the fix is a one-block crossfade: the instance's buffer goes to
  both voices, fading out on the orphan and in on the new one.

### 3.5 The M4 builder [S, M for the track pass]
- The menu action posts stock's One-Shot create. Then, on the new track, for i in 0..15:
  - CHILD_CREATE 0x0812F878 a `saminst`, set 0x73 = i;
  - create its `samasst` with Filename `""`, inserted at i+1, as stock's own sample-onto-pad path 0x0812C20A
    does;
  - make a DK4 entry with the default engine.
- Then flag the track in DT4 and call stock's track pass 0x0813E4B8, which sends the M7 everything [M].
  [VFY: the pass now measured to its return, with the rings drained. The rc31/m4ui runs stopped in a ring wait
  after the 16 pads (section 1, row 2). A new stock One-Shot track has no pads (kind 4 → one type 0x18
  child), so the 16 pairs are entirely ours.]
- [VFY, new risk, I] The builder's 16 CHILD_CREATEs run outside stock's model command (case 0x42 is only the
  replace). Whether stock's undo snapshot and its project "dirty" state include them is not shown. Test it in
  test_drum_ui (undo right after the create, then save), not by analogy with Analog, which adds no children.
- New pads get drum-friendly defaults through stock's property setters: envelope Attack 0, Sustain full.
- Undo: the create goes through the same path as Analog's, so it behaves as Analog's menu create does.
  [VFY: true only of the replace itself. The 16 children are not part of Analog's path; see the new-risk item
  above.]
  Checked in test_drum_ui.

### 3.6 The page
- D5 / tick / `dr_heal` write all three rows from `DR_PAGE[engine][24]`. The detection is the pad's DK4 /
  DT4 membership, not its file name, so the 250 ms re-ask goes away.
- D6:
  - Drum keys and the Engine key are ours. An edit sets DK4, publishes it, and is never forwarded to the
    model (as today).
  - Stock keys are forwarded as usual.
  - Browser-open, Unload (0x102D) and Rename WAV (0x103B) are dropped on drum pads. The browser-open event
    is found in S7-0.
- **Keys.**
  - Engine knobs reuse the 7 claimed keys 0x1AE..0x1B4, as generic k1..k7.
  - Engine select needs **one new key** from the reviewed 0x18C..0x1B6 class. Candidates: 0x1A0, 0x1A2,
    0x1A4, 0x1A7, 0x1AB. Each needs its own review (keyscan + test_keys allow-list).
  - X3's contiguity test becomes "the 7-key range or the one engine key".

### 3.7 project.xml
- Track element: `bento_drumtrack="1"`, written by X1 and read by a new X2 reader into DT4. H1-H3 carry it on
  copy, clone and destroy, as Analog's flag.
- Pad elements: `bento_drum_engine` (1..5) and `bento_drum_k1..k7`, as BE4.31. The attribute names are
  unchanged, so a BE4.31 project's knob values load.
- Sample children are saved by stock with `filename=""` [M].
- A pad without attributes on a flagged track gets the default engine for its position.

### 3.8 Hook census: kept, changed, removed, new

| Item | Status | Note |
|---|---|---|
| D1 render entry 0x08092320 | **changed** | detection by provider pointer; per-pad cells; orphan release |
| D4 post chain 0x08093800 (call) | kept | |
| Kick DSP `drum_kick.cpp`, DaisySP vendoring, powf memo, math | kept | |
| `knob_entry`, knob lines, seqlock | kept, extended | 128 lines (+0x1000..+0x2000, below the cells at +0x2400 [S]) |
| D5 / D6 / tick / `dr_heal` / `dr_stale_turn` | kept, extended | three rows, engine key, dropped sample actions |
| DK4 table, `dk_*`, publish | kept, extended | 128 entries (gate 4), publish filter by track flag |
| X1 / X2 / X3, H1-H4 | kept, extended | new track flag through the same dispatcher |
| CPU readout, DRUMS line | kept | engine name in the title |
| Keys 0x1AE..0x1B4 | kept | plus one engine key |
| **New:** binder sites at 0x08095656, 0x080949E2; `dr_bind`, `dr_provider` | new | M7 |
| **New:** menu cell 9, menu action, builder, DT4, `bento_drumtrack` | new | M4 |
| Carrier WAVs, `tools/carriers/gen_carriers.py`, the kit's `Drum Synths BE\` folder and its manifest lines | **removed** | |
| Carrier detector `carrier_id` / `test_carrier` / `carrier` and its run-state cache | **removed** | |
| M4 file-name match: `ENGINES[].name`, `dr_page_engine` by base name, 250 ms re-ask | **removed** | |
| Per-voice cells and zero-per-start | **removed** | replaced by per-pad cells |
| The "Config row only" layout, Poly Mode on the Kick row | **removed** | |
| Plan section 8 (carrier plan); parity row 20 (carrier missing / renamed) and the D8 carrier gate | **retired** | the gate becomes "only flagged pads bind" |

Thrown away **either way** (GO or NO-GO): the carrier generator and WAVs, the carrier detector, the file-name
page switch, and the tests that exist only for them (test_drums' Detector and Carriers groups, test_drum_ui's
name-match cases). On NO-GO, the whole drums group is also switched off and removed from the kit (section 10).
That covers D1, the page hooks, DK4, the drum keys and the Kick DSP. The shared X1-X3 / H1-H4 sites stay for
Analog.

## 4. Memory, keys, CPU

- **M7 code:** binder, provider and per-pad cells, about +0.5 KB of DSP ELF, minus the detector (about
  -0.4 KB). Each extra engine is about 1-3 KB [I, from the Kick's size].
- **Synth area:**
  - knob lines: 64 → 128 × 32 B = 4 KB (+0x1000..+0x2000);
  - cells: 32 × 256 B unchanged at +0x2400 [S]. Each engine's state must fit 256 B; the HiHat / Snare
    DaisySP objects are checked in S7-4, and the cell grows if needed (room up to +0x6000).
- **M4 slice:**
  - DK4 at 128 entries is 128 + 128 × 16 = 2176 B, against 1152 B today. It must fit in RAMCHK top
    (gate 4). The fallback is 64 entries with DT4 eviction of non-live pads, which allows 4 drum tracks.
  - DT4: 16 track entries × 8 B, about 256 B like AN4.
- **Keys:** 7 kept + 1 new (section 3.6).
- **CPU** [M for the Kick]:
  - 52.6 k instructions per Kick voice-block, 4.1 % of the block. Mono per pad caps instances at one per
    ringing pad (and 32 in all).
  - A busy kit, say 6 pads ringing, is about 25 % on top of SPACED's 59-65 % LOAD. That is near the edge, so
    **S7-4's efficiency pass is a must, not a nice-to-have.** Per-block coefficients and a cheaper sine come
    first.
  - Each engine gets a budget (target ≤ 3 % per voice-block), measured by test_drums' Cost group, and the
    DRUMS histogram on the device.

## 5. Risks

| Risk | Likelihood / impact | Handling |
|---|---|---|
| Tapping an empty pad opens the browser rather than the page (gate 2) | medium / high | S7-0 finds the decision. If it can't be intercepted cleanly, fall back to a placeholder name (v4 `BE Drum Synth`, measured to load), at the cost of gate 3 failing visibly. If both fail: NO-GO. |
| cmd 9 `""` makes stock show a load error at each project load (gate 3) | medium / medium | S7-0 emulates pcmStreamer's path, then a device check. Fix: skip cmd 9 for `""` in our builder's flow, or accept a silent failure. |
| Pack aborts on `""` (gate 1) | unknown / high (breaks Pack for the whole project) | S7-0 emulates Pack 0x0812A590. Fix: skip drum pads in Pack's loop (one site). |
| Paths that put a sample on a drum pad (kit preset, paste from a sample kit, MIDI sample import) | medium / low | publish filter `filename == ""` (3.2), so the pad plays the sample; test the preset apply in the emulator |
| A drum pad pasted onto a sample kit carries its DK4 entry (H4) | medium / low | the publish filter needs a flagged parent track, so it plays as an empty sample pad (silent); D-S7-4 |
| Re-strike click on a ringing body | medium / low | device check; one-block crossfade fix (3.4) |
| CPU with several engines ringing | medium / medium | per-engine budget; the efficiency pass; a cap only if the numbers demand it |
| Message 0x4E's sender and any M7→M4 status reading desc+4 as a file id | low / medium | S7-0 RE; the binder at 0x080949E2 already covers 0x4E's start. **[VFY] one site found [S]:** note-off 0x08095140 sends message 0x1C with desc+4 at 0x080951C0..0x0809520C when root+0x100 == 3. Fix: the desc+4 sentinel (3.3); find the receiver in S7-0 (gate 6) |
| [VFY, new] Builder children outside stock's model command (undo / dirty state) | unknown / medium | test_drum_ui: undo straight after the create; save without other edits (3.5) |
| Pad-grid labels show nothing for empty pads (gate 5) | high / low | find the label draw; else accept blank labels for this kit (D-S7-5) |
| Old firmware's save drops our attributes | certain / low | documented in the checklist and release notes |
| 1.6.1 / v3.72 behaviour not emulated | low / low | the drums group stays 1.6.8 only (`absent` on 1.6.1), as S6c |

## 6. Tests (offline)

- **test_drums (M7), on stock's real one-shot path:**
  - binder: flagged / unflagged / stale 0xBF id / no line / M7 reset republish;
  - both start sites; the provider slots;
  - D1 provider detection: a bit-identical sample path for non-synth pads;
  - re-strike: Poly 4, Mono, retrig x4, choke, mute; the orphan freed in one block; one instance per pad;
    zeroing only at a cell's first use;
  - engine change mid-ring; Loop on ignored; the Cost group per engine.
  - Mutants: no binder fallback, a wrong provider compare, no orphan release, cursor-only release (must fail
    as run 5b), zero per start.
- **test_drum_ui (M4), on stock's real page and model:**
  - the menu entry creates a flagged track with 16 pad/sample pairs, through stock's track pass (0xAC ×16,
    no non-empty cmd 9);
  - three rows per engine; stock keys reach stock; drum and engine keys are never forwarded;
  - browser, Unload and Rename are dropped on drum pads and work on sample pads;
  - the publish filter (flag, live, `""`);
  - H1-H4 carry DT4 and DK4 through copy, paste, undo and preset;
  - the capacity at 128.
- **XML round trips [M harness from `rc31/m4ui/e1.py`]:** this build ↔ this build; this build → stock 1.6.8
  → this build; this build → BE4.22 / BE4.31. No crash; the expected attributes kept or dropped.
- **Pack and preset apply on a drum track (gate 1, D-S7-4).**
- **Bisect:** `BE_DISABLE=drums` leaves One-Shot byte-identical to stock and Analog untouched.

## 7. Sprints

| Sprint | Kit | Content | Exit (offline) | Device check |
|---|---|---|---|---|
| **S7-0 gates** | none | RE and emulation only: (1) Pack with `""`; (2) the empty-pad tap: page or browser, and where it is decided; (3) cmd 9 `""` through pcmStreamer: silent or a message; (4) slice room for 128 DK4 entries; (5) pad-grid label draw; the 0x4E sender; any M7→M4 use of desc+4 [VFY: (6) the receiver of note-off message 0x1C (0x080951EA). First do the stock device check on an unloaded pad (section 0), which may settle 1-3 and 5 with no RE] | each gate answered with an address and a run, or a fallback chosen; the GO confirmed or turned into NO-GO in this file | none |
| **S7-1 M7** | (internal) | binder + provider; D1 by provider pointer; per-pad cells + re-strike; carrier detector removed; Loop ignored; 128 knob lines | test_drums green incl. the new groups and mutants; the Kick golden unchanged; stock sample pads bit-identical | none on its own: it needs S7-2 to make pads |
| **S7-2 M4 track** | **BE4.40** | menu cell 9 + action; builder; DT4 + H1-H4; `bento_drumtrack`; publish filter; drop browser / Unload / Rename; carriers and name match removed | test_drum_ui green; the XML round trips across firmware; Pack fix if gate 1 needs it | make a Drum Synth track; 16 Kicks play from the sequencer; conditions, retrigs, choke, mute, sends; re-strike on fast rolls (no click, no double ring); save, power cycle, reload; open on stock 1.6.8 (silent, no crash); Pack; the DRUMS line |
| **S7-3 page** | BE4.41 | engine key (reviewed); three rows per engine table; pane title; envelope defaults; Poly Mode gone | page tests per engine (with the engines not yet built shown as blank rows); key review in test_keys | every row on the device; turning Engine; stock keys act; no sample controls anywhere on a drum pad |
| **S7-4 engines + CPU** | BE4.50 | Snare, Clap, HiHat, Tom (old S6d); efficiency pass; per-engine budgets | golden per engine vs host DaisySP; Cost within budget; cell-size asserts | each engine by ear; LOAD with a busy kit on SPACED; the DRUMS histogram |

Each kit gets its checklist (`docs/checklist-v4.40.txt` and on), as before. Nothing is flashed until the
previous kit's checklist has passed.

## 8. Open decisions for Jeff

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D-S7-1 | Default engines on a new track | all Kick; or a kit layout (Kick, Snare, Clap, HiHat, Toms ...) | kit layout, once S7-4's engines exist; all Kick in BE4.40 |
| D-S7-2 | Row 3 | keep stock's envelope / LFO row (defaults A 0, S full); or a drum row (one Decay, LFO rate / depth only) | keep stock's: it is tested parity, and one table changes it later |
| D-S7-3 | Knob memory across an engine change | one shared set of 7 values; or per-engine values per pad | shared: fewer bytes and XML attributes; reset to the engine's defaults on change |
| D-S7-4 | A sample arriving on a drum pad by a path we did not block | it plays as a sample (publish filter `""`); or it is ignored and the synth plays | play it as a sample: nothing is silently ignored |
| D-S7-5 | Pad-grid labels | blank for BE4.40; or find and hook the label draw | decide after gate 5 |

## 9. Why not a genuinely new track type

- **Surface.** Type numbers 0x0E..0x16 are tested throughout both cores [S]: 5 One-Shot tests, 10 Wavetable,
  13 Granular, 19 Loop, plus the menu's kind→type table 0x0813F730 [VFY: a function, stock's instrument replace, with a `tbb` switch on kind; not a data table], the track pass, the sample load, the page
  tables, the Sequence-page choice and the M7 root classes. A new type means a model class, an XML type
  string and an M7 engine, and every one of those tests reviewed and patched.
- **It loses the requirement.** The drum Step page is chosen by `cmp r0,#0xe`, so a new type gets the same
  sequencer only by patching that test, and every other One-Shot test the sequencer and voice path depend on.
  The voice parity (conditions, retriggers, choke, sends, filter, envelope, LFO, poly AT, Note Mod) would have
  to be rebuilt, not inherited.
- **Other firmware.** Stock 1.6.8, BE4.22 and v3.72 would meet an unknown track type in the project file
  [I: the track dropped, or worse]. The flagged One-Shot track opens there as a plain kit [M].
- **Separation.** The user sees the same separation either way: its own menu entry, its own pages, no files.
  The difference is only underneath, and underneath One-Shot is exactly what we want to share.

## 10. What would make it NO-GO, and what would have to be true to revisit

NO-GO if S7-0 finds any of these with no clean fix:
- A drum pad can't open its page without a file, and a placeholder name brings up a visible load error.
- Pack can't be made to skip drum pads without patching its copy loop in more than one place.
- The slice can't hold one entry per pad, and eviction can't keep a live project's pads.

On NO-GO:
- The drums group is switched off by default and removed from the kit; we don't ship the carrier hack.
- The Kick DSP and tests stay in the tree, unbuilt, for a revisit.
- To revisit, one of these must become true: a stock event path that opens a pad's page with no file, a
  Pack-safe placeholder, or more M4 slice.

## 11. Second thoughts (where this could be wrong)

- **The empty `""` sample child is the least-proven piece.** The emulator shows that the model, loader,
  writer and track pass accept it. It does not show stock's *screens* with it: the pad grid, the waveform pane,
  the browser decision. That is why S7-0 exists before any build.
- **The binder trusts the M4's line at start time.** A pad whose line is published late (project load before
  the handshake) plays nothing until the line appears. That is better than a Kick on a sample pad. DK4 is
  M4-only and publishes once the handshake holds, as today.
- **Close call: binding in the M7 vs faking a loaded sample from the M4.** The M4 investigator rejected the
  M4-side fake (writing stock's sample manager, pool and engine object). The M7 binder replaces two `bl`s and
  touches none of stock's sample machinery [M], so this one is not close.
- **Close call: mono by re-owning the cell vs suppressing the new voice.** Suppressing would patch stock's
  voice choice and lose the per-hit velocity and envelope restart. Re-owning keeps both, at the cost of the
  one-block click risk.

## 12. Evidence files

- M7: `C:\Users\rjrom\AppData\Local\Temp\claude\C--Users-rjrom-bento-mods\69931413-5dfa-4316-a637-0fee2cef4235\scratchpad\rc31\m7\`
  `exp_synthpad.py`, `exp_output.txt`, `exp.sh`, `build.sh`, `da.py`, `xref.py`, `jt.py`, `words.py`. Build tree:
  WSL `~/rv31m7/tree`, `~/rv31m7/out168` (BE4.31 sha 5d2c6366..., test_drums 33 OK).
- M4: `C:\Users\rjrom\AppData\Local\Temp\claude\C--Users-rjrom-bento-mods\69931413-5dfa-4316-a637-0fee2cef4235\scratchpad\rc31\m4ui\`
  (`e1.py` harness, project variants v1-v4, logs) and WSL `~/rv31m4ui/`.
- Background: `docs/plan-synths-168.md` sections 3 (D1-D9), 8, 9, S6c, S6c-2, and the mono decision.
- [VFY] Verification: `C:\Users\rjrom\AppData\Local\Temp\claude\C--Users-rjrom-bento-mods\69931413-5dfa-4316-a637-0fee2cef4235\scratchpad\rc31\vfy\`
  - `vfy_m7.py` / `m7.sh`, output `~/rv31vfy/vfy_m7.out`: V1-V7, stock's one-shot path with a hook binder.
  - `vfy_load.py` + `mk.py` / `ld.sh`, `ld2.sh`, `ld3.sh`, `b422.sh`: the loader, the full track pass, the
    writer and the instrument replace, on stock 1.6.8, BE4.31 and BE4.22. Outputs are `~/rv31vfy/out_*.xml`.
  - `regdump.py`: stock's registry keys.
  - `s*.sh`: disassembly reads (`~/rv31vfy/m7.txt`, `m4.txt`).
  - Builds: BE4.31 `~/rv31vfy/out168` (sha 5d2c6366..., identical to the investigators'); BE4.22
    `~/rv31vfy/out422` (8d66cec).

## Device answers to the gates (Jeff, 2026-11-09, BE4.22 = stock behaviour here)
- Gate 2: tapping an unloaded One-Shot pad opens the **file browser**; the pad's "Edit" also opens the file
  browser. So S7-2 must intercept both on drum pads and open the drum page instead (find where stock decides
  "no file -> browser").
- Gate 1: Pack and reload of a project with an unloaded pad: no errors seen.
- Gate 5: an empty pad is a blank square with a "+" in the touchscreen pad grid, and a white pad (not the
  track's colour) on the hardware pads. For drum pads we want a name/label and the track colour: hook the
  grid label / pad colour decision (find it in S7-0), else accept blank + white as a first step.
- Gates 3, 4, 6 remain for S7-0 (offline).
