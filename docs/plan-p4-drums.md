# P4: Drum Synth track on native properties (BE4.50+)

Final plan, 2026-11-16. Base: HEAD a80d334 (BE4.41), stock 1.6.8. The designer's draft went through two verifier passes. Every issue that held up has been applied, and the Review log at the end records each one. This plan replaces the storage and M4 sections of docs/plan-drum-synth-track.md (S7) and the P4 row of docs/plan-native-props.md. Section 0.2 lists the parts of S7 it keeps.

Labels: **M** measured (emulator on stock 1.6.8 or a real build), **S** static (disassembly or source), **I** inferred. Every claim carries one. "Map" means the four P4 investigation maps (state, gates36, gates25, budget/audit). "Re-checked" marks a load-bearing item that was checked again by hand at final review.

---

## 0. Status

### 0.1 Verdict

**GO, confirmed by P4-0 (2026-11-16, 0.6).** The original verdict was "GO, with an offline gate sprint (P4-0) first". No fixed requirement looks impossible to meet cleanly. Three preconditions (A, B, C) still need an end-to-end run in the emulator. Static evidence now covers most of A and B (0.4). Precondition C is turned into a planned feature, the M4 re-push (3.6), so it no longer depends on what stock happens to do.

If B fails after all, the knobs can't be edited. That is NO-GO, and drums are dropped (Jeff's rule).

Review raised three design problems the draft had missed, and all three are now fixed in the plan:
- **Re-strike timing.** The handoff had to move from render time to VOICE_START (3.4).
- **Post-chain step.** The re-strike click from stock's post chain needs the one-block crossfade back (3.4).
- **Speaker safety.** The first kit needs a drum-engine cap so it doesn't drop out in its own acceptance test (4, P4-D6).

### 0.2 What changed since S7 (2026-11-09) and P4's sketch

| Topic | S7 / P4 sketch said | Now | Label |
|---|---|---|---|
| Storage | DK4 (M4 slice, 64-128 entries) plus a new DT4 track table; H1-H4 carry them; X1/X2 save and load | Native template keys: engine + 7 knobs on every type-1 pad (saminst), a flag on the type-0x0E track. Stock saves, loads, clones, duplicates and copies them. DK4, DT4, H1-H4, X1, X2 and X3 all go | M (p4np) |
| Pad object types | "types 1 and 2" (plan-native-props 5) | **Type 1 only.** Type 2 is loopinst; samasst (type 5) is unchanged | M |
| Template size | 0x30D, padded to 0x310 (budget map) | **Drums builds only:** 0x303 + 9 = 0x30C, a multiple of 4, so no pad entry is needed. The **default build** stays BE4.41's 0x303 padded to 0x304 with the type-0xFF entry (M1). np_template has to be generalised (3.3 N1) | M (prototype); S (build.py:1720-1748, re-checked) |
| How the M7 learns pad settings | The M4 publishes 64-128 knob lines to SA_DKTAB with a seqlock | Stock pushes pad keys by **pad handle** through ENGINE_PROPERTY (message 0x3E). A new SAMPLER_PROCESS trampoline stores them per root. The builder and the M7-take re-push call PROP_PUSH explicitly (3.3, 3.6) | M (push seen on the M4 side, project load); S (transport, M7 side) |
| Engine key | 0x1A0, 0x1A2, 0x1A4, 0x1A7, 0x1AB | 0x1A4/0x1A7/0x1AB went to P3. Use **0x1A8 = engine index, 0x1A2 = track flag** | S / M |
| Engine values | "1..5" / ids 1, 3, 5, 7, 9 | Key 0x1A8 stores a dense **index 0..5** (0 = none, 1 Kick, 2 Snare, 3 HiHat, 4 Clap, 5 Tom). The M7 maps it to synth_shared.h's ids 1/3/5/7/9 (3.1) | S |
| Menu | Drum Synth = cell 9, count 8 -> 10 | Analog's entry is gone (BE4.40). Drum Synth = **cell 8** (0x0819ADD0), count 8 -> **9** | S |
| Build state | The drums group was assumed to build | **Broken since d21225d** (P3 Q2): DSP_REFUSED_LIBM refuses dsp_lib.cpp's own `powf`/`sinf`. With a scratch exemption it builds and the drum suites pass. **Fixed in P4-0** (P4-D1 (a), 0.5) | M (build.py:661/719, dsp_lib.cpp:63/85) |
| Stale tests | none noted | test_handles (10) and test_inst_xml (3) fail on every drums build because they assert BE4.22-4.31 Analog state. **Retired in P4-0** (0.5) | M |
| DSP window | "+0.5 KB binder, 1-3 KB per engine" | 6,772 B free with today's Kick. Four more engines plus the crossfade and the efficiency pass **do not reliably fit** in the window (4) | M / I |
| Re-strike | S7: handoff at render; orphan freed "on its next render"; crossfade as a known-risk fix | Handoff **at VOICE_START** (binder site), orphan freed in the re-strike block, one-block crossfade **in scope** | S (re-checked: S7 plan lines 199-206) |
| Copy paths (D9) | needed H2/H4 | NODE_CLONE, CHILD_DUP, STORE_COPY_ALL and STORE_CLONE_H carry keys and flag natively (M). A drum pad pasted into a plain One-Shot keeps engine != 0, so it needs a normalizer that recognises the clipboard's two-step copy (3.2) | M / S |

**Unchanged from S7** (S; HEAD touches none of these addresses):
- Menu kind 4 makes a stock One-Shot (type 0x0E) with the same step page.
- The M7 binder sits at the VOICE_START call sites 0x08095656 / 0x080949E2 (r4 = voice, r5 = root) and uses `dr_provider`.
- The desc+4 sentinel.
- D1 detects drum voices by provider pointer.
- Orphan release goes through 0x08063E80.
- "Not a new track type".

### 0.3 S7-0 gate answers (all six)

| Gate | Answer | Label |
|---|---|---|
| **1** Pack with filename "" | Device (Jeff, 2026-11-09): Pack + reload of a project with a stock-Unloaded pad showed no errors. A builder-made pad being identical to a stock-Unloaded one is not proven, so device item 8 repeats this on a real Drum Synth track | M (device) / I |
| **2** Empty-pad tap / Edit | Device: both open the WAV browser. Offline: PAD_OPEN 0x08152D44 decides for both the tap (0x08152E64) and header Edit 0x40 (0x08153096). Branch A: no pad object (0x08152D64). Branch B: file name 0x81 empty (`ldrb.w r3,[sp,#0x40]` at 0x08152DB6, `cbnz` at 0x08152DBA). A nonzero byte there gives stock's loaded-pad posts {0xA8},{0xA9},{3}, which open page 8 | M (device + emulator); S (stub not built) |
| **3** cmd 9 with "" | Silent. Handler 0x08111C58 tests isEmpty 0x081079DE and returns at 0x08111C76. **Consequence:** drop S7's "skip cmd 9 for empty names" fix | M + S |
| **4** M4 slice room for DK4 | Moot: P4 has no DK4. Its place is taken by the M7 per-root settings table (3.5) | S |
| **5** Pad-grid label / colour | Device: an empty pad shows a blank "+" and is white on the hardware. Offline: GRID_REBUILD 0x081537E0 labels via PADCELL_SET_NAME 0x0817B940 (a name shows the label and hides the "+", M). Hardware: empty pads get base colour 0; the white is most likely the **selected** colour 0x64FFFF. Several Bank-view behaviours are also gated on PAD_HAS_FILE 0x081525AC (3.3) | M; I (white = selection); S (sites) |
| **6** Note-off 0x1C / desc+4 | Closed. The M7 handles 0x1C itself (flag[0x1C] = 1, ENGINE_CTOR 0x0805B61A). The sentinel 0xD5000000\|id stays as free defence | M + S |

### 0.4 Preconditions (P4-0 must answer them)

All answered by P4-0; the results and the corrections they forced are in 0.6. The text below is the pre-P4-0 state.

- **A. Pad keys reach the pad's M7 root: S, likely.**
  - The transport is traced end to end on paper (S):
    - M4 ENGINE_PROPERTY 0x0810B898 posts message 0x3E {+8 handle, +0xC key, +0x10 value}.
    - The ring is flow-controlled at 28 outstanding messages (0x0810B8F6..0x0810B916), so a heavy push costs latency, not lost messages.
    - The one-shot root vtable (DRUM_ROOT_VT 0x080ACE3C) slot 2 = 0x080950E0 tail-calls SAMPLER_PROCESS 0x0808F4A0 (at 0x08095128).
    - SAMPLER_PROCESS drains the inbox for handle root+0x0C (0x0808F4BE..C4) and reads key = uxth [sp+0x34] and value = [sp+0x38] (0x0808FE9C..0x0808FEA2).
  - The value is the raw 32-bit word, so N2's descriptors must be an integer type and the trampoline reads [sp+0x38] as an int (S; tested).
  - **What is still missing is one run (M).** It has to use a **menu-built** track with no save/reload: a pad's ENGINE_PROPERTY reaches its root, and the binder fires on the first note. Stock pushes a new track inside its create function *before* any pads exist (PROP_PUSH at 0x0813F858, S). The builder therefore has to push the track itself (3.3).
  - The 3.7 fallback is a contingency only, off the critical path.
- **B. Pad-page edits store the value and reach the M7: S, likely.**
  - INST_EVENT re-posts 0xCF up the view tree (0x08154AB2).
  - The model's 0xCF case 0x0813D7B0 is key-agnostic except for key 0x61. It stores the value with 0x0812ECF4(store, [ev+4] handle, key, value), then calls ENGINE_PROPERTY(engine, handle, key, value) (0x0813D816..0x0813D832, S).
  - ENC_TURN posts {0xCF, +0x34 handle, key, value} (S).
  - The earlier "MODEL_CHILD_EDIT 0xD0 stored nothing" result (M) tested the synth view's re-post path, not this one.
  - What is left:
    - Measure 0xCF with a pad handle through 0x0813D7B0 (stored value + ENGINE_PROPERTY).
    - Confirm that the page's slot handle (+0x34) is the saminst.
    - Remove D6's swallow, which today eats drum-key edits. That removal is **planned for P4-3**, not conditional.
- **C. The M7 table after an M7 (re)take: resolved by design.**
  - The settings table is zeroed when the M7 takes the area (3.5).
  - The M4 re-pushes every flagged track when it sees a new M7 generation (3.6).
  - So whether stock re-pushes after an M7 restart no longer matters. P4-0 still records whether it does (S/M) as a cross-check.
- **Also traced in P4-0, not blocking:**
  - stock's One-Shot preset/kit importer (PATCH_READ rejected the One-Shot form, M);
  - undo, actually run (today I, through STORE_COPY_ALL);
  - that PROP_PUSH pushes default-0 values (loop 0x0813B904..0x0813B924 shows no value filter: S/I, measure it);
  - the model handler cases for 0x3F / 0x102D / 0x103B (3.3, event filter), and template 2 ("Sample Dashboard") header actions;
  - every place a 0x0E track's instrument kind is shown (F1 identity, P4-D8);
  - whether Bank-view hardware-pad play and step entry depend on anything other than PAD_HAS_FILE.

### 0.5 P4-0 repair (BE4.50 P4-0, 2026-11-16)

**P4-D1 = (a), done.** dsp_lib.cpp defines `be_powf` / `be_sinf` / `be_log10f`. dsp/shim/cmath declares them and maps `powf` / `sinf` / `log10f` to them with `always_inline` C++ forwarders, so the vendored DaisySP files (unchanged, hashes still pass) call ours. DSP_REFUSED_LIBM is unchanged and strict. The drums ELF has no libm-named symbol and no `_Z4sinff`-style leftover forwarder, and DSP code stays 29,836 B (M). test_drums Math calls `be_sinf` / `be_powf`. Default build: `cmp` against m1-be441 is identical (M).

**Retired: the 13 stale cases.** On a drums build each one failed, and each asserted BE4.22-4.31 AN4 state (slice + 0xE20) that P1/BE4.40 retired. Today the H/X sites serve DK4 only (M).
- test_handles Copies.test_a_loaded_analog_track_is_analog_and_saves_again: deleted. It covered the AN4 load/save through H2/X1/X2, and DK4's version is test_drum_ui Xml.test_a_project_round_trip_through_stocks_copies.
- test_handles Copies.test_without_h2_the_load_loses_analog: deleted. It was the H2 mutation on AN4, and DK4's is test_drum_ui test_without_h2_a_load_loses_the_knobs.
- test_handles Copies.test_paste_carries_analog_and_a_plain_source_makes_no_entry: deleted. It covered AN4 paste, and DK4's is test_drum_ui test_a_track_copy_and_paste_carries_its_pads_knobs.
- test_handles Copies.test_eight_analog_tracks_survive_a_load_and_a_save: deleted. It tested AN4's 16-entry capacity, and DK4's is test_drum_ui test_a_full_table_gives_up_copies_never_live_pads.
- test_handles Destroy.test_remove_and_a_commit_free_the_entries: deleted. It covered AN4's H3, and DK4's is test_drum_ui test_removing_a_track_drops_its_pads_entries.
- test_handles Destroy.test_a_thirteenth_object_is_destroyed_by_stock_and_its_entry_with_it: deleted. It asserted the AN4 entry, and **Not covered on DK4:** no test_drum_ui case drives STORE_CREATE's refusal path (the 13th object destroyed through slot 2). Left uncovered because P4-3 removes H3 (review, S).
- test_handles NewObjects.test_a_stale_entry_for_the_next_handle_is_not_inherited [create], [copy], [clone_h] (3): rewritten on DK4 (slice + 0x2000), since H1 is live code in drums builds until P4-3 and test_drum_ui had no such case.
- test_handles NewObjects.test_entries_from_an_earlier_boot_are_gone_at_boot: deleted. It covered AN4's header, and DK4's is test_drum_ui test_entries_from_an_earlier_boot_are_gone_at_boot.
- test_inst_xml RoundTrip.test_analog_track_saves_and_loads: deleted. The bento_analog* X1/X2 round trip no longer exists (P1: native keys, test_native_props), and DK4's is test_drum_ui Xml.
- test_inst_xml RoundTrip.test_loading_a_plain_track_clears_a_stale_line: deleted. It covered AN4's X2, and DK4's is test_drum_ui test_a_pad_read_without_attributes_loses_a_stale_entry.
- test_inst_xml RoundTrip.test_a_load_before_the_m7_took_the_area_is_kept: deleted. It covered AN4's handshake. **Partly covered:** test_drum_ui Edits/Tick cover an edit or tick before the handshake, but no DK4 case does an XML load (X2) before it. Left so because P4-3 removes X2 (review, S).

**Also retired: seven cases that passed vacuously** (review count, M: the HEAD files pass exactly these seven on a drums build). They only touched the dead AN4 layout, so they could not fail.
- test_handles Copies.test_only_a_wavetable_saves_our_attributes, Destroy.test_without_h3_the_entry_stays: deleted. They had no AN4 to act on, and the DK4 H3 mutation is test_drum_ui's.
- test_handles NewObjects.test_without_h1_the_new_object_inherits, test_nothing_happens_at_a_cold_boot and test_the_constructor_wrapper_keeps_every_register (its slow path): rewritten on DK4.
- test_inst_xml test_plain_wavetable_writes_nothing_of_ours: became Writer.test_a_wavetable_writes_nothing_of_ours (no bento_analog* or bento_drum_* attribute).
- test_inst_xml Stock.test_stock_reader_ignores_our_attributes: became test_stock_reader_ignores_the_drums_attributes. Stock's reader reads a params element that carries bento_drum_engine / k1..k7 without a fault, and the object is untouched. It is a smoke check on a Wavetable object, not the rollback item: no test yet runs stock's reader on a saminst carrying the drum keys (P4-1, stock 1.6.8 opens the drum project).

Kept as is: test_handles Sites (the three sites, the bisect case) and Stock (stock's handle findings).

### 0.6 P4-0 results (2026-11-16)

**Verdict: GO (M).** A and B hold end to end in the emulator on stock code, C is confirmed as needing 3.6, and nothing found is a NO-GO criterion. Several plan sections changed (3.2, 3.3, 3.4, 5, 7, 8, decisions); they are listed at the end of this section. Gate work ran on scratch copies of the working tree with the repair in place (`scratch/p4-0/{A,B,model,kick}`); an independent review re-checked A, B, C and the repair (`scratch/p4-0/V`).

**Repair (M).** P4-D1 (a) done (0.5). Drums build (`BE_ENABLE=drums`): DSP code 29,836 B, image 0x18AA98, every suite green including analog's no-libm test. Default build: `cmp` against m1-be441 identical.

**A. Pad keys reach the pad's M7 root on a menu-built track: holds (M).**
- Track made by stock's create 0x0813F730 (kind 4), 16 (saminst, samasst) pairs by a builder prototype, then PROP_PUSH(model, track, 0), no save/reload.
- PROP_PUSH posted 1,172 ring messages with 40 flow-control waits (M). Per pad: one 0x48, 55 × 0x3E to the **pad handle** ending ...0x1DF, 0x1A8, 0x1AE..0x1B4, then 10 × 0x3F. It also sends one {0x3E, handle 0xFFFFFFFE, key 0xAC, value = pad handle} per pad: **this is what gives each pad an M7 root** (M).
- M7 (stock's ENGINE_CTOR, engine init and H_INPUT 0x08059960; SAMPLER_PROCESS per root per block): all 16 pads own a root, and all 8 keys arrive at the trampoline site 0x0808FEA2 with the right value and root (880 hits), at CHUNK 1/28/64/2000 and on the drums image (M). Case 0xAC = 0x08059CE0 takes the first root with +0xC == 0xFFFFFF00 (S+M).
- **PROP_PUSH pushes zero values** (pad 3 left all-zero: eight zeros arrive) (M).
- A mailbox holds 64 entries and a pad gets 66 per push. Only the flow-control-free CHUNK 2000 run lost messages (each pad's trailing 0x3F); our keys come earlier (M).
- First note {0x12, pad, 60, 100} reached the binder site 0x08095656 with r5 = the pad's root, on a pad with no sample (root+0x674 = 0) (M; binder not built).
- **Caveat (review, S): the binder site is conditional.** VOICE_START tests root+0x625 at 0x08095644 (`ldrb r3,[r5,#0x625]`; `bne.w 0x08095900`); when it is nonzero, allocation goes through 0x08087D60 and rejoins at 0x08095660, after the site. root+0x625 is SAMPLER_PROCESS key **0x3A** (dispatch 0x08091902 stores value != 0; cleared at init 0x0808FC8E), registry name **"MultiSam"** (bool). A tested only MultiSam = 0. Whether 0x080949E2's function has the same split is I. See 3.4 and P4-D11.
- Limits (I): create called directly (not through MENU_HANDLER); builder is a Python prototype; ring drain and once-per-block mailbox clear are harness; the note went straight to the mailbox, not through the sequencer.

**B. Pad-page edits store the value and reach ENGINE_PROPERTY: holds (M).**
- Slot handle = saminst: stock page select 0x081547EA picks the type-1 child whose 0x73 = pad, INST_CELL = saminst, and the cell-set loop 0x081545BC..0x081545F0 copies it into all 24 slots (S); every slot widget's +0x34 = the saminst (M).
- With D6 restored to stock INST_EVENT and an N1 prototype template: ENC_TURN on Kick Dec → stock INST_EVENT 0x08154970 → UI_POST 0x08154AB2 → page host 0x081702F4 → MODEL_HANDLER → case 0xCF 0x0813D7B0 → 0x0812ECF4(store, saminst, 0x1AF, 66) → ENGINE_PROPERTY(engine, saminst, 0x1AF, 66) at 0x0813D832 → 0xC3 tail. Only that saminst changed (M). Same for 0x1AE, 0x2E, 0x1A8, 0x1B4.
- Read-back with X3 at stock bytes: CONTEXT_GET returns the stored values (M).
- Without N1 the setter can't add the key: nothing stored, ENGINE_PROPERTY still sent (M). So N1 (P4-1) is required.
- D6 as built swallows the edit (expected; P4-3 removes it). **D6 forwarding and X3 removal must ship together**, or the page shows DK4's stale values (M); a P4-3 test pins the pair.
- Case 0xCF is key-agnostic for our keys: special keys are 0x22..0x25, 0x61, 0x188, 0x1DF, 0x14E; key 6 goes to the engine as 5 (S, matches M).
- Limits: the page host is a fake parent around stock's handler; the rig has no engine object (ENGINE_PROPERTY r0 = 0), so arrival at the M7 is A's evidence (I).

**C. Stock re-push after an M7 restart: none (S; review S).**
- PROP_PUSH 0x0813E4B8 has exactly 7 direct callers and LOAD_PUSH_ALL 0x0813FEC8 exactly 2; neither is in a literal pool (S, review bl scan). They are preset apply 0x0813F440 (two), menu create, Paste Track (function 0x0813F8EC, gate [model+0xC351] == 3, only caller model case **0x1003**, menu cell 0x0819AC18), bank finalize 0x0813FA1C, LOAD_PUSH_ALL itself (two), the project loader and the bank-load / New Project path. None is an M7-status path.
- After an M7-only restart all 128 roots come back free and stock never re-sends 0xAC, so stock sample pads would lose their roots too (I). 3.6's re-push is required; since PROP_PUSH re-sends 0xAC it also restores root assignment (S/M).

**Trace items (M on stock 1.6.8 with a One-Shot fixture, S otherwise).**
- **Sample actions: no single model choke point (M/S).**
  - 0x3F (Bank header Load, case 0x08142682) only does NAVIGATE(0x14) to the browser. Page-8 Load WAV / Load WAVs post **0xD** (case 0x08142816, NAV 0x14). INST_EVENT ignores 0x3F.
  - 0x102D (Unload, case 0x081426A0) calls UNLOAD 0x0813BC6C (M: the file cleared).
  - 0x103B (Rename) **has no model case**; three views call RENAME 0x08141974 directly (0x081533A2, 0x0815508C, 0x08146A9E) (S).
  - Two more file actions: 'WAV' tab 0x8 (case 0x0814261C, NAV 0x11 waveform) and Edit WAV 0x31 (case 0x08142288, NAV 0x24: Normalize/Save rewrite the file) (M/S).
  - The real choke points are the operations: **SAMPLE_ASSIGN 0x0813B514** (browser 0x08164320/0x08164436, Load All 0x0813BAA4, view 0x0814ABEC, rename 0x08141C54), **UNLOAD 0x0813BC6C** (0x102D, Cut 0x0813BDD0, Paste 0x0813BE38, 0x0813E08C), **RENAME 0x08141974** (S). Plus NAVIGATE 0x08143D44 to pages 0x14 / 0x11 / 0x24 (S).
- **Template 2** (0x0819BEF8, page 8): header = four empty slots, 'Dashboard'/0x3 and 'WAV'/0x8; no file action in the header. The page-8 file actions are menu rows 0x0819AEC0 / 0x0819B050 / 0x0819B9B0 (S).
- **One-Shot preset importer: found, no normalizer needed (S/M).** Preset apply 0x0813F440 clones the whole preset track with STORE_CLONE_H 0x0812E600, copies a fixed key list (no 0x1A2) from an old track if one exists, CHILD_DUPs the old track's 0x18/0x1D children, then PROP_PUSH. Run with a marker key: a new 0x0E track carrying the preset's track and pad keys (M). Flag and pad engines come from the same preset, so a stock kit gives a plain sample track and a drum patch a drum track. Not run: the old-track branch with a real 0xC434 record.
- **Undo is sequence-only (S/M).** Undo 0x1034 → 0x0813C9C2 restores the UNDO_SNAP 0x08134D38 snapshot, which only sequence edits take (11 callers). A knob edit through 0xCF then Undo: value unchanged, snapshot invalid (M). Device item 7's "Undo after a knob edit" tested nothing (changed below).
- **PAD_HAS_FILE 0x081525AC is touchscreen-only (S).** Its three callers are touch toggle / cell display. Hardware pad play routes on the M7 (0x08085FC0), and the step-entry / record inserts (NOTE_INSERT 0x0812EF64 callers 0x0813CAB0, 0x081383EC, 0x08138B16, 0x0813CE5C) read no Filename (S, scan).
- **New hardware dependency: BUTTON_EVENT 0x08144750, BTN_INST (id 10) on page 7** reads the selected pad's Filename at 0x0814490E: empty → NAV 0x14 (browser), loaded → NAV 8 (M). A drum pad would open the WAV browser; it needs a stub like PAD_OPEN's (3.3). Whether NAV 8 from there runs D5's page conversion is I.

**P4-D8 surface list (where the kind or sample identity shows).**

| # | Surface | Where | Prominence | Label |
|---|---|---|---|---|
| 1 | Bank page title "<track>: One-Shots" | format 0x0818E5EC, header builder 0x081435BC, type 0x0E at 0x081437A6 → 0x081437E2 | **high** (the track's main page) | M ('LoFi Kit: One-Shots') |
| 2 | Step-view pad labels "No WAV" for an empty Filename | 0x0818F098, literals 0x0815FAA4 / 0x0815FFC8 (in 0x0815FED4 / 0x0815F758) | **high** (every drum pad would read "No WAV") | S; the page (likely 0x27) is I |
| 3 | Instrument menu cell "One-Shot" | 0x0819AD44 (id 0x1026, label 0x0818E334) | medium; a current-instrument marker is I | S |
| 4 | Page-8 title "Sample Dashboard", 'WAV' tab; INST title = file name (blank on drum pads) | template 2; 0x0815403C → +0x80B4 | medium | S / M |
| 5 | Patch browser type filter "One-Shot" | 0x081A2060 in 0x081A2050..0x081A206C | low-medium; preselect is I | S |
| 6 | Patch folder 'UserPatches\OneShots' | 0x0818E21C | low | S |
| 7 | Track Config, Load/Save Track, pages 0x14 / 0x17 / 0x27 headers | — | none (track name only) | M |
| 8 | M7 type-name copy | 0x080AA430..0x080AA468 | none (no display) | I |

**Kick profile and efficiency candidates (M; for the DaisySP Kick in today's drums build).**
- One Kick voice-block = **52,608 instructions** (test_drums Cost). Split: SyntheticBassDrum::Process 25,344; be_powf 7,936 (2 calls/sample, memo hit); Svf::Process 6,144 (5 outputs, 1 used); be_sinf 5,211; rand 3,328; render loop 3,144; stock calls 1,114 (M, per-PC over blocks 1-11).
- About 113 instructions/sample are block-constant work done per sample (2 powf, setup, 3 of 5 vdivs) (S). Filter coefficients are not recomputed per sample (S). The first block after a start costs ~12 k more (M).
- Prototypes (own block renderer of the same algorithm), all passing test_drums:
  - **KF1** block-rate coefficients, state in locals, inline xorshift, SVF low only: 29,245 (−44 %), **bit-identical** over 49,536 samples, 29,016 B DSP.
  - **KF2** KF1 + polynomial sine: 26,039 (−50 %), max diff 1.56e-7.
  - **KF3** KF2 + click/SVF gate below 1e-7: 17,895 in tail blocks; one whole hit 20.33 M → **7.17 M (−65 %)**; +280 B DSP. Today's Cost test (blocks 1-11) never sees the gate.
- Not built (I): vmaxnm/vminnm output stage (~1.2 k/block), register tuning (10-20 %). The 2 soft-clip divides are the sound.
- **Overtaken by docs/p4-engine-spec.md** (Jeff, 2026-11-16: own engines, no DaisySP): its lean Kick runs at ≤ 45 instructions/sample against KF3's ~140 ungated. The KF prototypes stay as reference and as a BE4.50 fallback (P4-D12). Device CPI, denormal behaviour and FPSCR.FZ remain device item 12.

**Plan changes made by P4-0** (applied below in place):
- 3.2: the track flag *is* pushed (handle −2, SET_PROPERTY), but nothing on the M7 uses it (M/S); the importer bullet is resolved; Paste Track noted.
- 3.3: sample-action filter moved from model cases to SAMPLE_ASSIGN / UNLOAD / RENAME entry + a NAVIGATE gate; new INST-button stub; step-view "No WAV" label and Bank title sites (P4-D8).
- 3.4: the binder requires MultiSam (0x3A) = 0 on drum pads (P4-D11).
- 5 / 7 / 8 / decisions: tests, device items 7, 14 and 15, risks, P4-D8 and P4-D10..D12.
- 5's DaisySP goldens: superseded for P4-5 by docs/p4-engine-spec.md (own engines, each with its own reference). BE4.50's Kick keeps today's kick_ref golden.

**Not done in P4-0 (I):** the real menu path through MENU_HANDLER; the preset importer's old-track branch; the 0x080949E2 MultiSam split; the INST-button NAV 8 page conversion; full re-runs of A's harnesses by the reviewer (outputs and code re-checked instead).

---

## 1. Requirements

### 1.1 Must (Jeff's fixed requirements; not reopened)

| # | Requirement | Where it is met |
|---|---|---|
| F1 | A clean, separate **"Drum Synth" track** with its own instrument-menu entry | menu cell 8 + action (3.3); identity surfaces audited in P4-0 (P4-D8) |
| F2 | One-Shot layout, 16 pads, the **same** One-Shot step sequencer | a stock type-0x0E track; the builder adds 16 pads; PAD_HAS_FILE hook so per-pad behaviours act as on loaded pads |
| F3 | Pad Edit (and tap) lead to a **drum-synthesis page** with no sample controls | PAD_OPEN hook + D5 page conversion; Load/Unload/Rename filtered at the model choke point, covering page 8 too (3.3) |
| F4 | **No carrier WAV, no file-name trigger** | carrier detector and `dr_page_engine`'s `BE Kick.wav` match removed; engine is a property (S) |
| F5 | At most 16 drum sounds per track | one engine per pad, 16 pads |
| F6 | **Mono pads**: a new hit re-strikes the pad's single engine (envelopes and click restart, body continues, no click), whatever Poly/Mode, including retriggers | per-root cells, handoff at VOICE_START, one-block crossfade (3.4) |
| F7 | If F1-F6 can't be done cleanly, drop drums | NO-GO criteria (8) |
| F8 | No migration of old dev projects | X2 removed outright |
| F9 | Stock 1.6.8 opens a drum project without crashing | M (emulator); device item 9 |

### 1.2 Must (derived)

- **M1** Drums stay a bisect group (`BE_ENABLE=drums`). The default build is byte-identical to BE4.41 at every drum site. Its template is BE4.41's (0x304 with the 0xFF entry), and the registry is stock-identical apart from P3's keys (test_keys, test_native_props).
- **M2** Drum pads survive save, reload, power cycle, copy/paste (clipboard), duplicate, track copy, undo and Pack.
- **M3** A drum pad never becomes a synth pad on a sample track, and a sample track never gains synth pads (3.2 invariant). Old engine lines never bind to new sample pads (3.5).
- **M4** No audio dropouts in typical use. CPU is measured, not assumed, and the first kit ships with a cap (4).
- **M5** Stock sample pads give bit-identical output (test_drums stock-parity group).
- **M6** The drums build builds (libm guard fixed), and every suite is green or retired with a reason.

### 1.3 Should

- **S1** Pad label = engine name on the touchscreen; hardware pads lit in the track colour (gate 5).
- **S2** Per-engine knob names on row 1 (the page supplies them; the registry title is global per key).
- **S3** Four more engines: Snare, HiHat, Clap, Tom (BE4.60; Clap may slip, P4-D9).
- **S4** BE4.41 and older BE images open a drum project without crashing (BE4.41: M; BE4.22/4.31: I).

### 1.4 Nice

- **N1** A kit layout by default once the engines exist (D-S7-1).
- **N2** An engine graphic in the waveform pane.
- **N3** Per-engine knob memory. Ruled out: about 28 more keys, none available (S).

---

## 2. What Jeff sees

**Making a track.**
- The instrument menu has a ninth entry, **Drum Synth**, after the eight stock instruments (S).
- Choosing it makes a normal One-Shot track underneath, with 16 pads already set to drum engines (BE4.50: all Kick, D-S7-1). The pad grid opens.
- Where the 9th cell sits on the menu grid wasn't rendered. Analog showed in that cell on BE4.22-4.31 (I).
- Elsewhere (track name "Track %d", any instrument-kind display) it may read as One-Shot until P4-0's identity audit decides (P4-D8) (S for the name, I for other surfaces).

**The pad grid.**
- Each pad's touchscreen label shows its engine name instead of "+" (S1).
- Hardware pads light in the track's dim colour like loaded pads, go bright on press, and show stock's selection colour on the selected pad.
- Per-pad 0x1DF behaviour (most likely pad mute, I) works as on a loaded pad: the PAD_HAS_FILE hook lets stock post it, and the LED wrapper keeps stock's "0 when 0x1DF is set" colouring (S).

**Playing.**
- Same sequencer and step page: conditions, retrigs, choke, mute, sends.
- Each pad is one drum. Hitting it again while it rings re-strikes it: the hit restarts, the tone carries on, and the voice handover is cross-faded over one block, whatever the pad's Mode. Fast retrigs behave the same way.
- BE4.50 limits how many drum engines run at once (6-8, a build constant, P4-D6). Beyond that the oldest fades out over one block.

**Opening a pad** (tap or Edit).
- You get the drum page, never the browser.
- Load, Unload WAV and Rename WAV do nothing on a drum pad, from the Bank view, from the pad page or from any other view (P4-D5: disabled).

| Row | Slots |
|---|---|
| 1 Drum | Engine (Kick..Tom; "none" not offered), then the engine's knobs (Kick: Tune, Kick Dec, Tone, Accent, Sweep, Sweep Dec, Dirt). Unused slots are blank |
| 2 Mix | stock's Level, Pan, sends, Cutoff, Resonance, Choke |
| 3 Env / LFO | stock's row; new drum pads default to Attack 0, Sustain full (D-S7-2) |

**Engines planned for P4-5** (titles set by the page, S2):
- Snare: Tune, Decay, Tone, Snappy, Accent, FM
- HiHat: Tune, Decay, Tone, Noise, Accent, Open/Closed
- Clap: Tone, Decay, Spread, Accent
- Tom: Tune, Decay, Tone, Sweep, Accent

The 7 knob values are shared across engines, and turning Engine resets them to that engine's defaults (D-S7-3).

**On other firmware.**
- Stock 1.6.8: the track opens as a One-Shot with 16 empty pads. It is silent, nothing faults, and a re-save drops every `bento_*` (M, emulator).
- BE4.41 and the default BE4.5x build behave the same way (M for BE4.41).
- BE4.22-4.31: their X2 reads `bento_drum_engine` / `k1..k7` into DK4. The engine attribute now holds an *index* (1 = Kick, same as the old Kick id; 2-5 aren't valid old ids, and X2 makes no entry for an unknown engine, S m4_synth.c:1212). With no carrier nothing binds, so the track is silent (I; P4-3 round trip).

**Copying.**
- Copy/paste or duplicate a drum pad within the track, or the whole track: engine and knobs come along (M for single CHILD_DUP; the real two-step clipboard path is tested in P4-3).
- Paste a drum pad onto a **sample** One-Shot track: it becomes an empty sample pad (3.2).
- Paste a sample pad onto a drum track: P4-D3.

---

## 3. Architecture

### 3.1 Keys

| Key | Object | Token | Range / default | Evidence |
|---|---|---|---|---|
| 0x1A2 | track, type 0x0E | `bento_drumtrack` | 0/1, default 0 | Unused in the stock 1.6.8 keyscan; no immediate in either core (M). No 32-bit word hits; the 16-bit hits are tbh offsets or tables that look like Unicode case maps (S; I for the case-map reading). Also an `ldrh` struct offset in LINK_VERSION (stock_168.py:205), not a key use; the review entry says so (S) |
| 0x1A8 | pad, type 1 | `bento_drum_engine` | **index 0..5**, default 0. The drum page offers 1..5 only | unused, no immediates (M). Dense range because ENC_TURN steps through every value in the descriptor's range (S) |
| 0x1AE..0x1B4 | pad, type 1 | `bento_drum_k1..k7` | 0..127, per engine table | already reserved and reviewed (KEY_DR_FIRST, ALLOW['drums']) (S) |

- **Engine index -> id.** The M7 maps index 1..5 to synth_shared.h ids 1/3/5/7/9 in the trampoline. The page, label and heal code use the index directly (S). Engine 0 isn't reachable from the drum page, so a drum pad can't turn into an empty sample pad by a knob turn.
- **Descriptor type.** N2 descriptors are integer-typed (the M7 gets the raw word, 0.4 A) (S).
- **Range class.** The range-class test 0x18C..0x1B6 has one M7 test (0x08059990), which applies only to reverb object 0xFFFFFFF1 (type 0x1B) (S). The only M4 0x1B6 test (0x0813B4BC) is an exact-key compare (S, verifier-confirmed).
- **No clash with P1/P3 keys.** Those are on type 6; the reverb neighbours 0x1A1/0x1A5/0x1A6/0x1A9/0x1AA/0x1AC/0x1AD are on type 0x1B (M, tpl.txt).
- **Track FX** keeps 17 unused range-class keys, including 0x1A0 (S).

### 3.2 Track flag, pad engine and the binding rule

- **Who reads what.**
  - The track flag 0x1A2 is **M4-only in practice**: menu identity, UI hooks, the page. PROP_PUSH does push the track's own keys, under handle 0xFFFFFFFE through SET_PROPERTY (0x1A2 at create and again in the builder's push, M), and H_INPUT posts them to the −2 mailbox (0x080599C0 → 0x08059A80). The only −2 consumer found, 0x080672A0, acts on keys 0x27, 0xBD and 5 only (S; other consumers not searched exhaustively, I). So the flag carries no track identity on the M7. P4-1's key review records the −2 path (P4-0 correction).
  - **The M7 binds a voice to a drum engine if and only if** its root's settings line has the current M4 boot generation, a handle equal to root+0x0C, and engine != 0 (3.5).
- **Invariant, kept by the M4: engine != 0 only on pads of a flagged track.** How each path keeps it:
  - **Builder:** writes the engine on flagged tracks only.
  - **CHILD_DUP normalizer** at the old H4 site 0x0812F91E: zero 0x1A8 **only when OBJ_TYPE(parent) == 0x0E and its 0x1A2 == 0**. Any other parent is left alone, in particular the clipboard's type-0x17 holder (STORE_CREATE(0x17) at 0x08124E66, no 0x1A2 key), since a clipboard copy is two CHILD_DUPs: copy into the clipboard store (0x08124DF0, CHILD_DUP at 0x08124E4C..50), then paste into the destination (0x08124EE2 / 0x08124FA8) (S). Other CHILD_DUP callers: instrument replace 0x0813F6A2/0x0813F6F2 (S). Knobs are left as they are; they're harmless at engine 0.
  - **NODE_CLONE / STORE_COPY_ALL / STORE_CLONE_H:** whole-track copies move flag and pads together (M).
  - **Preset / kit importer** 0x0813F440: clones the whole preset track (STORE_CLONE_H), so flag and engines arrive together; no normalizer (P4-0, S/M). The old-track branch copies a fixed key list without 0x1A2 and CHILD_DUPs only 0x18/0x1D children (S).
  - **Paste Track** (model case 0x1003 → 0x0813F8EC) re-runs PROP_PUSH, which re-sends 0xAC (S, P4-0 review).
  - **Hand-edited XML:** out of scope (I).
- **Why the gate isn't on the M7 side.**
  - The M7 would need the track flag per root. Stock pushes it only to the −2 (global) mailbox, with no track identity (P4-0, M/S).
  - Gating on "engine != 0 and no sample id" was rejected: a stale root+0x674 sample id on a reused root can play an old sample (S7 3.3).

### 3.3 M4 sites

**Removed** (all under `BE_ENABLE=drums`; none in the default build):

| Site | What | Why it can go |
|---|---|---|
| DK4 table, `publish()` to SA_DKTAB, seqlock/generation | pad engine + knobs | native props + M7 table |
| `drum_keys()` with EMPTY token, KICK titles | descriptors | N2 |
| H1 OBJ_CTOR 0x0812E79C (`an_forget`), H2 clone 0x08199194, H3 destroy 0x08199190 | carry / clear DK4 | stock copies native keys (M). H1's cross-M4-boot job moves to the boot-generation tag on the M7 table (3.5) |
| H4 CHILD_DUP 0x0812F91E (`dr_dup`) | carries DK4 | replaced by the normalizer at the same site |
| X1 0x081304DE, X2 0x081309A0, X3 0x08134A70 | save / load / context | stock handles native keys (M); F8 |
| `dr_page_engine`'s `BE Kick.wav` match | file-name trigger | F4 |
| `tools/carriers/`, CARRIER_* defines | carrier WAVs | F4 |

**Kept or rewritten:**
- D5 `dr_refresh`: page conversion on 0xA9, rows by engine index.
- D6 `dr_page_event`: changes from "swallow drum-key edits" to "let them through to stock's 0xCF path" (P4-3, unconditional, 0.4 B). On an engine change it also refreshes the label (0xC3) and the knob defaults.
- INST_TICK `dr_view_tick` (CPU title) and `dr_heal` (now reads the engine with CONTEXT_GET).

**New:**

| Site | Patch | Notes |
|---|---|---|
| N1 template | drums builds: +1 key on type 0x0E, +8 on type 1; 0x303 -> 0x30C, no 0xFF pad. Default build: unchanged BE4.41 template | np_template takes a per-type key list, with the same contiguity check and "not already present" check for runs 1 and 0x0E as for 6 (S, build.py:1720-1748 today handles only type 6) |
| N2 descriptors | real tokens and titles; integer type; min/max per key (engine 0..5) | registry title is global; the page overrides it per engine |
| MENU_CELL 0x0819ADD0 | {ACT 0x10F4, "Drum Synth", 0, 0, 1} | as BE4.22-4.31 (S) |
| MENU_COUNT 0x08142EB4 | `movs r3,#8` -> `#9` | (S) |
| MENU_VT_SLOT 0x081A13F4 wrap (MENU_HANDLER 0x0815095C) | for our id: record the handle at the target index (0x08134DBC), post {0x42,0,4,track}, then flag and build **only if** a different handle with OBJ_TYPE 0x0E and no type-1 children is now there; post {1}. Any other id goes to stock | 0x0813F730 can return without creating anything when the model+0xA5F0/+0xA5F4 lock matches (0x0813F740..0x0813F768, S). UI_POST and model case 0x42 run synchronously (S) |
| Builder | 16 × (saminst, samasst) in child order [1,5,1,5,...]. Each saminst: 0x73 = pad index, engine = 1 (Kick), knobs = Kick defaults, Attack 0 / Sustain full. Each samasst: 0x81 = "". Then **PROP_PUSH(model, track, 0)**, as stock does after a preset apply (0x0813F476) | Stock's create pushes the track at 0x0813F858 before any pad exists (S). Pad order isn't handle order (g25: pad 0 = handle 45, pad 8 = handle 43, M), so 0x73 is set explicitly. Type 5 has no 0x73 (tpl.txt, M) |
| PAD_OPEN 0x08152DB6 | `ldrb.w r3,[sp,#0x40]` -> `bl` stub: load the byte; if the track (+0x6B50) is flagged and the pad's engine != 0, r3 = 1 | branch A can't fire once the builder has run; build.py checks the original bytes |
| PAD_HAS_FILE 0x081525AC | entry hook: return 1 for engine != 0 pads on flagged tracks, else stock | one hook covers its three callers: 0x081525FE (posts {0xCF, pad, 0x1DF, v}), 0x081526A8, and cell refresh 0x0815277C via 0x081527C6 (S) |
| Sample-action filter (P4-0 corrected) | **entry hooks on the operations**: SAMPLE_ASSIGN 0x0813B514, UNLOAD 0x0813BC6C and RENAME 0x08141974 return without acting when the target pad has engine != 0 on a flagged track; **NAVIGATE 0x08143D44** to pages 0x14 (browser), 0x11 (waveform) and 0x24 (Edit WAV) is dropped for such a pad. The enables (0x6C builds at 0x08145EB6/0x08145ECE and the page-8 menu build 0x0815419C/0x0815420C) are greyed for drum pads (P4-D5) | No model choke point exists (P4-0, M/S): Load is 0x3F / 0xD (NAV only), Unload 0x102D, Rename has no model case (views call RENAME directly), 'WAV' 0x8 and Edit WAV 0x31 navigate. Each operation's callers are listed in 0.6. Cut (0x0813BDD0 → UNLOAD) would copy without clearing a drum pad (P4-D10). SAMPLE_ASSIGN already returns early on a locked track (0x0813B55C), so an early return there is a stock-shaped path (S) |
| INST button 0x0814490E (BUTTON_EVENT 0x08144750, BTN_INST on page 7) | stub at the Filename read: a drum pad on a flagged track reads as loaded, so it goes to NAV 8 (the drum page) instead of NAV 0x14 | P4-0 (M): empty → browser, loaded → page 8. Whether NAV 8 from here runs D5's conversion is checked in P4-3 (I) |
| Step-view pad label "No WAV" (0x0815FAA4 / 0x0815FFC8 in 0x0815F758 / 0x0815FED4), per P4-D8 | S1-style label hook: the engine name for drum pads on a flagged track | P4-0 (S); the page (likely 0x27) is confirmed in P4-3 |
| Bank title "<track>: One-Shots" (0x081437E2), per P4-D8 | "<track>: Drum Synth" on a flagged track | P4-0 (M); only if P4-D8 says hook it |
| GRID_REBUILD loop exit 0x081539A0 | `add.w r3,r6,#0x6b00` -> `bl` stub: on a flagged track, PADCELL_SET_NAME(view+0x50+0x400p, engine name) for each pad with a handle; replay the add.w | refresh after an engine change: post 0xC3 (key != 0x1DF) to 0x08153C58 |
| LED refresh 0x0813AF68 | `bl 0x0810B898` -> wrapper. It **saves r0-r3** (they hold ENGINE_PROPERTY(engine, 0xFFFFFFFE, 0xC0, 1), set up at 0x0813AF5C..66). On a flagged track: 0x08125ADE(r5, p, 0x08142A98(r6)) for each pad **without 0x1DF set**. Then it restores r0-r3 and tail-calls | reached only for type 0x0E (cmp at 0x0813AF3C, S) |
| CHILD_DUP normalizer 0x0812F91E | see 3.2 | |
| M7-take re-push (3.6) | on the page tick: if SA's M7 generation moved, PROP_PUSH every flagged track | replaces "precondition C" |

The stubs at 0x081539A0 (r6 = view) and 0x0813AF68 (r4/r5/r6) rely on callee-saved registers that are stable for this image only. build.py checks the original bytes at every new site (S).

### 3.4 M7: trampoline, binder, re-strike

**SAMPLER_PROCESS trampoline.**
- Retarget `bhi.w 0x08091B46` at 0x0808FEA2 (T3, ±1 MB; reaches 0x080DA000) (S).
- For keys 0x1A8 and 0x1AE..0x1B4 (r3 = key, r4 = root, value = int [sp+0x38]):
  - if area_ok and the generation is current, write the root's line (3.5) and branch back to 0x0808F4E0;
  - otherwise count a drop in DSTATS and branch back.
- Any other key goes to 0x08091B46 unchanged (it stores only 0x1DF, S). The trampoline is correct even if another branch to 0x08091B46 exists, because that block stays stock.

**Binder** at VOICE_START 0x08095656 / 0x080949E2 (`bl 0x08087D00` at HEAD, S re-checked by verifier). r4 = new voice, r5 = root.
- If the root's line binds (3.2): desc+0 = &dr_provider, desc+4 = 0xD5000000|id, desc+0x24 = 0, **and do the handoff here** (below).
- Otherwise: stock.
- Lookup is O(1): the line is indexed by root slot (3.5).
- **MultiSam (P4-0 review, S).** The 0x08095656 site runs only when root+0x625 == 0: VOICE_START branches at 0x08095644 to 0x08095900 / 0x08087D60 when it is set, rejoining at 0x08095660. root+0x625 is key 0x3A "MultiSam". Per P4-D11, drum pads pin 0x3A = 0 (builder, normalizer; the page never shows it), and a Rig test sets 0x3A = 1 on a drum pad to show what happens. Whether 0x080949E2's function has the same split is checked in P4-2.

**Re-strike handoff (F6), at voice start, not render.** Stock renders a root's voices in list order and VOICE_START appends the new voice (0x08095540..0x08095550; render loop 0x08090772..0x080907B8) (S). A render-time handoff therefore lets the old voice run the engine first in the re-strike block, which plays the engine twice and jumps the body (S, the draft's flaw). Instead, at the binder:
1. If the root has no live cell: take a free one, zero its engine state (the only zeroing point), owner = v_new, trigger pending.
2. If the root's cell is live and v_new == owner (stock reused the same voice object): trigger pending, release nothing.
3. Otherwise: prev_owner = owner, owner = v_new, trigger pending, xfade = 1 block.

**Render (D1, by provider pointer).** Per block, per cell:
- **The first render of (root, any voice) in this block runs the engine once** (guard: cell.last_block == block counter). It applies a pending trigger (envelopes and click restart, body kept: DaisySP `Trig()`, S) and writes the block into a shared 1-block scratch buffer tagged (root, block).
  - Render order makes one shared buffer enough: a root's voices are walked together by its own loop (S), so no other root renders in between (I, checked by a Rig test).
- Every voice of that root outputs from the scratch buffer:
  - **owner:** unity, or a linear fade-in over the block if xfade is set;
  - **prev_owner during xfade:** a fade-out over the block, then zeros, stock release 0x08063E80(v+0x58), cursor = frames + 1, DRUM_POST (VFY-measured, S7 3.4);
  - **any other voice on that root** (an orphan from earlier): zeros + release.
- Orphans are identified by the render's (root, voice) pair, never by a stored pointer alone, because stock may already have given that voice to another pad (S/I).

Why the crossfade is in scope: VOICE_START re-initialises the new voice's filter/envelope/LFO objects (voice+0x1C0..0x5D4, 0x08095470..0x08095536) and it carries its own velocity. So a continuous engine still steps at the voice boundary with Cutoff < max, Decay/Sustain not full, a different velocity, or an LFO (S). The fade makes the handover one block long. Cost: one extra multiply-add per sample on re-strike blocks only (I).

**Cell liveness and reclaim.** A cell is free when its owner hasn't rendered it for one block (last_block is stale). That happens on stock voice steal at pool exhaustion, choke, mute or quiet-end. A stolen voice now rendering another root is checked against *that* root's cell, so it is never treated as an orphan of the first (S/I). 32 cells match the 32-voice shared one-shot pool (DRUM_POOL_N, S).

**Drum-engine cap (BE4.50: build constant, 6-8; P4-D6).** When a start would exceed the cap, the oldest live cell's owner fades out over one block (same fade path), and its cell is reused.

**Removed:** the carrier detector (`carrier_id`, `test_carrier`, `carrier()`, drums.cpp:175-263) and `knob_entry` (SA_DKTAB reader) (S).

### 3.5 Synth-area layout (drums, under `BE_ENABLE=drums`)

| Offset | Size | Use |
|---|---|---|
| 0x0080 | | SA_DSTATS (unchanged; adds a dropped-write counter and a cap-steal counter) |
| 0x0100 / 0x0800..0x1000 | | P3's FMSTATS / FMBUF (untouched) |
| **0x1000..0x1800** | 128 × 16 B | M7 settings table, **one line per fixed one-shot root** (128 roots made at boot, 0x08058C80..0x08058CE6, S). Line: handle (4), M4 boot gen (4), engine index (1), k1..k7 (7) |
| **0x1800..0x1A00** | 512 B | shared re-strike scratch buffer (128 frames × 4 B) + tag (I: block size 128 assumed from 2.67 ms; P4-2 checks) |
| 0x1A00..0x2400 | ~2.5 KB | free |
| **0x2400..0x5400** | 32 × 384 B | cells: 64 B header (owner, prev_owner, root, last_block, xfade, trigger-pending, serial) + 320 B engine state |
| **0x5400..0x5600** | 512 B | DRUN (moved from 0x4400) |
| 0x6000.. | | Track FX reservation, untouched |

- **Root slot index.** It comes from the root's position in the engine's root table (+0x758). Deriving it cheaply (pointer arithmetic vs. a scan at take time into a small map) is a P4-2 item (I). The table can never overflow and needs no eviction.
- **Handle check.** A line written for handle H binds only while root+0x0C == H. A pad deleted and its root reused by a new pad: the new pad's push overwrites the line (PROP_PUSH pushes zeros too, S/I measured in P4-0). Until then the handle differs and nothing binds.
- **M4 boot generation.** OBJ_CTOR hands out handles from a per-boot monotonic counter (stock_168.py note, S), so handles repeat only across an M4 reboot. Lines carry the M4 boot generation (config line r[0], synth_shared.h:49). The trampoline writes the current one, and the binder ignores lines from any other generation. This replaces H1/`an_forget`'s job (S; whether M4-only restarts happen on device is I).
- **Lifecycle.**
  - The table is zeroed in synth_take, before or at the same time as the generation moves, never lazily in run() (S: today synth_take zeroes only 0x0..0x20, 0x40..0x200, 0x400..0x800, m7_polyat.c:292-311; run() zeroes only CELLS/DRUN/DSTATS).
  - The trampoline writes only when area_ok (the take can wait for pool margin, m7_polyat.c:227-248, polled per block :366-370).
  - Pushes that arrive before the take are dropped and counted, and the M4 re-push (3.6) restores them.

### 3.6 M4 re-push on M7 take

- On the page tick, the M4 compares SA's M7 generation (m7gen) with the last one it saw. If it moved (first take after boot, M7 retake), it calls PROP_PUSH(model, track, 0) for every flagged track.
- This covers pushes that arrived before the take, M7 restarts, and precondition C.
- Cost: one compare per tick, and on a generation change one push per drum track. Ring flow control turns that into latency, not loss (S).

### 3.7 Contingency if precondition A fails

If pad-handle messages don't reach the root, the M4 publishes the table itself: CONTEXT_GET per drum pad, on the page tick and on project load, into SA+0x1000 keyed by handle with a seqlock, which is today's path with native props as its source.
- It stays clean (no carrier, no file name) and keeps the H/X removals.
- Cost: about 0.5-1 KB of M4 code (I).
- It's off the critical path because A is S-likely.

---

## 4. Budgets

| Budget | Now (HEAD, drums on) | P4 delta | After | Label |
|---|---|---|---|---|
| DSP window, 36,608 B | 29,836 used, 6,772 free | −0.4 KB detector; +0.6-1.0 KB binder/provider/handoff/crossfade/cap | about 6.1-6.6 KB free (Kick only) | M / I |
| + P4-5 engines | | Snare 1.6 + HiHat ~1.2 + Tom ~0.4 + Clap 1-2 + 4 × 0.4 glue = 5.8-6.8 KB, plus the efficiency-pass code (unbudgeted) | **does not reliably fit** (about −0.7 to +0.8 KB) | I on M sizes |
| Mitigation, planned from P4-5 start | | non-hot engine code (init, coefficient setup) in the M7 C slice (4,088 B free) or `-Os`; Clap may slip (P4-D9) | | M (free space) / I |
| Engine sizes | Snare 1,604 B, HiHat ~1.1-1.3 KB, sizeof Snare 288 B / HiHat 220 B | measured on Playground's **modified** copies; re-measure on unmodified upstream 2c72eaf (5) | provisional | M (on wrong source) |
| M7 trampolines (1 KB at 0x080DA000) | 752 B used | SAMPLER_PROCESS (~60 B) + 2 binders (~60 B) | about 150 B spare | S / I |
| M4 image vs max 0x18D334 | 0x18AA98, 10,396 B free | removes DK4, H1-H4, X1-X3, the name match; adds menu, builder, PAD_OPEN, PAD_HAS_FILE, filter, label, LED stubs, normalizer, re-push, N1 (+36 B) | probably 8-11 KB free | M / I |
| SA area | cells to 0x4400, DRUN to 0x4600 | table 0x1000, scratch 0x1800, cells 0x2400..0x5400, DRUN 0x5400 | all below 0x6000 | S |
| Engine state | Kick 180 B | ≤ 320 B per cell | RingMod HiHat (464 B) doesn't fit (P4-D4) | M |
| Project size | | 8 attributes on **every** One-Shot pad, 1 on every One-Shot track | | M (writer) |
| Push load | | +8 ENGINE_PROPERTY per pad on every One-Shot track (128 per 16-pad track) | flow-controlled: latency, not loss | S (flow control) / I (latency) |
| CPU per Kick voice-block | 52,608 instr, 4.11 % at 1 CPI | one engine per pad; the crossfade adds a little only on re-strike blocks | | M |
| CPU, busy kit | 6 Kicks ≈ 25 %, 16 ≈ 66 %, 32 ≈ 131 % at 1 CPI | voices end at −96 dB after 32 quiet blocks (QUIET 0.0000158f, QUIET_BLOCKS 32), so Kick tails hold engines for seconds | **BE4.50 ships a 6-8 engine cap**; SPACED already runs 59-65 % LOAD (1.6.1-era logs) | M (cost) / I |

**Consequence.** BE4.50's DRUMS stats line gives the first device CPI, and the cap is then tuned (P4-D6). An earlier quiet-end (about −72 dB) for drum voices is an option in the same decision. The efficiency pass and an engine-placement plan are must-haves before the engine count grows.

---

## 5. Tests

**Goldens per engine, against the vendored unmodified upstream.**
- Repo policy stays: dsp/daisysp holds unmodified upstream (commit 2c72eaf) files, hash-checked by test_drums Vendored. dsp/host/kick_ref.cpp builds the same files with host libm and the firmware's xorshift rand() (S).
- Snare and HiHat are vendored the same way, with hashes added to Vendored. Host refs are built like kick_ref.cpp, seeded per cell.
- Playground's copy is **not** used: it is modified (click_level_, UpdateCoefficients, cached coefficients), and its Snare/HiHat provenance is unrecorded (M, diff).
- Any efficiency change is our own glue, with its own golden against the unmodified ref.
- The emulated M7 render (test_drums Rig) must match within a stated tolerance. Bit-exact is impossible because dsp_lib's sinf/powf differ from libm (I).
- One golden per engine: a single hit, 3 re-strikes at 5/20/80 ms (host driver re-triggers one instance with no reset), and knob extremes.
- **Re-strike click test, after stock's post chain:**
  - conditions: Cutoff < max, Decay/Sustain not full, velocity 127 then 30, an LFO on pitch;
  - pass: the largest sample-to-sample jump across the re-strike block, against the same render with the re-strike moved one block later, stays under an **absolute** threshold set in P4-2 (not the engine's own slope).

**M7 (test_drums).**
- Trampoline:
  - stores our 8 keys (engine index mapped to id);
  - passes 0x1DF and all other keys to stock, checked byte for byte against stock's 0x1DF store;
  - reads the value as an int;
  - drops (and counts) writes before area_ok.
- Binder binds if and only if: gen current, handle == root+0x0C, engine != 0. A drum pad with MultiSam (0x3A) = 1: pinned back to 0, or the outcome documented (P4-D11).
- Stale lines:
  - lines written under boot gen N don't bind under N+1;
  - a reused root with a different handle doesn't bind;
  - a warm retake zeroes the table;
  - a push before the take is dropped, and the re-push restores it.
- Stock sample pads stay bit-identical (existing group).
- Re-strike, with old-then-new voice order inside one block:
  - the engine runs once per block;
  - the body is continuous;
  - the orphan fades and is released in that block.
- The same voice object reused: no release, a trigger only.
- The shared scratch buffer is never read under the wrong (root, block) tag.
- Reclaim: voice steal at pool exhaustion, choke, a re-strike after quiet-end (cell zeroed as specified), a stolen voice rendering another root.
- Cap: start N+1 fades out the oldest and reuses its cell.

**M4 (test_drum_ui, test_native_props, test_synth_ui, test_keys).**
- Template:
  - drums builds: 0x30C, every type except 1 and 0x0E stock-identical apart from P3's type 6;
  - default build: BE4.41's 0x304 with 0xFF.
- Tokens on pads and track survive a write/read round trip.
- Menu: cell, count, posts. With the 0xA5F0 lock set, nothing is flagged or built.
- Builder: 16 pairs in [1,5,...] order, 0x73 = 0..15, samasst 0x81 = "", defaults. PROP_PUSH runs after the build, and a pad ENGINE_PROPERTY is seen with no save/reload.
- PAD_OPEN:
  - drum pad: {0xA8},{0xA9},{3};
  - sample pad: stock's sequence.
- PAD_HAS_FILE: 1 for drum pads on flagged tracks, stock otherwise. 0x1DF toggle posts on a drum pad.
- Sample-action filter:
  - SAMPLE_ASSIGN / UNLOAD / RENAME on a drum pad do nothing, reached through their real callers (browser Load, Load All, Unload 0x102D from the Bank view, page 8 and 0x08147034's view, Cut, Paste, the three rename views);
  - NAVIGATE to 0x14 / 0x11 / 0x24 for a drum pad is dropped (Bank Load 0x3F, page-8 0xD, 'WAV' 0x8, Edit WAV 0x31);
  - the INST button on page 7 with a drum pad selected goes to the drum page;
  - normal on sample One-Shot and Loop tracks;
  - 0x6C enables greyed.
- Labels named. LEDs: dim track colour, 0 when 0x1DF is set. **0xC0 ENGINE_PROPERTY arguments unchanged** on drum and sample One-Shot LED refreshes.
- Clipboard, through the real copy (0x08124DF0) and paste (0x08124EE2 / 0x08124FA8):
  - drum track -> same drum track keeps the engine;
  - drum track -> sample One-Shot zeroes it;
  - the type-0x17 clipboard holder keeps it.
- Re-push: an M7 generation change triggers PROP_PUSH for flagged tracks only.
- Stock 1.6.8 and BE4.41 open the drum project (p4np rig); BE4.22/4.31 open it (rc31 m4ui e1 harness).
- test_synth_ui: MENU_* sites stock in default builds, patched in drums builds.

**Mutations** (each must turn a test red):
- trampoline: passes our key to stock; swallows 0x1DF; ignores area_ok; reads the value as float;
- binder: ignores the engine; ignores the handle; ignores the boot gen;
- re-strike: handoff at render instead of start; engine run twice per block; body reset; orphan not released; release on same-voice reuse; no crossfade; cells keyed by voice;
- cap ignored;
- UI: filter on SAMPLE_ASSIGN only (UNLOAD/RENAME unhooked); NAVIGATE gate removed; INST-button stub ignores the flag; normalizer removed; normalizer that tests "parent flag == 0" without the type check; PAD_OPEN stub ignores the flag; PAD_HAS_FILE hook ignores the flag; filter only at the Bank view; label stub skips SET_NAME; LED wrapper clobbers r0-r3;
- template/menu: template count 0x30B; a misspelled token; MENU_COUNT left at 8;
- builder: builder without PROP_PUSH; DRUN overlapping the cells.

**Retired / fixed.**
- The 13 AN4-era Analog cases in test_handles and test_inst_xml are retired (the state they assert was retired in P1). Done in P4-0; the per-case reasons are in 0.5.
- test_analog's no-libm test follows the guard fix (P4-D1). Done in P4-0: it passes unchanged on the drums ELF (0.5).
- B's pairing: a P4-3 test shows D6 forwarding with X3 still in place reads stale values, so both ship together (0.6).

---

## 6. Sprints

Each sprint ends offline-green and is committed (standing OK). Nothing is pushed without asking. No kit is flashed until the previous kit's checklist has passed.

| Sprint | Kit | Work | Exit (offline) |
|---|---|---|---|
| **P4-0 gates + repair** (done 2026-11-16, GO; 0.6) | none | fix the libm guard (P4-D1); retire the 13 stale tests; emulate A on a menu-built track (builder prototype + PROP_PUSH, no reload); measure B (0xCF with a pad handle through 0x0813D7B0; slot handle = saminst); measure PROP_PUSH zero values; trace the model cases for 0x3F/0x102D/0x103B and template 2's header actions; find the One-Shot preset importer; run undo; audit instrument-kind surfaces (P4-D8); trace hardware-pad play / step entry for PAD_HAS_FILE dependencies; record whether stock re-pushes after an M7 restart | drums build green on every suite; A and B answered M with addresses; GO confirmed or NO-GO written here |
| **P4-1 native keys** | internal | N1 (generalised np_template, drums-only), N2 integer descriptors, key reviews, test_keys ALLOW | template and XML tests; stock 1.6.8 and BE4.41 open the project; default build unchanged |
| **P4-2 M7** | internal | trampoline + gen/area_ok checks, root-indexed table + zero in synth_take, binder by engine, provider, D1 by provider, handoff at start, once-per-block engine, scratch buffer, crossfade, reclaim, cap constant, SA layout move; detector and `knob_entry` removed; vendor nothing new yet | test_drums including the new groups; Kick golden vs kick_ref; click test after post chain; mutations red |
| **P4-3 track + grid + edit** | **BE4.50** | menu (with lock check), builder (recipe + PROP_PUSH), PAD_OPEN, PAD_HAS_FILE, sample-action filter, label and LED post-passes, normalizer, M7-take re-push, **D6 forwarding** + engine knob (1..5) so Jeff can edit the Kick; remove DK4, H1-H4, X1-X3, the name match, carriers | test_drum_ui green; clipboard round trips; XML round trips across firmware; every mutation red; `checklist-v4.50.txt` |
| **P4-4 page** | BE4.51 | per-engine titles from the page, envelope defaults, Poly Mode hidden, heal on engine change, label refresh on engine change | page tests per engine (unbuilt engines show blank rows) |
| **P4-5 engines + CPU** | BE4.60 | vendor Snare/HiHat unmodified + hashes + host refs; re-measure sizes; Snare, HiHat, Tom (Clap per P4-D9); efficiency pass as own glue; engine placement (C slice / -Os); cap retuned from BE4.50/4.51 numbers | goldens per engine; cost per engine within budget; DSP window fits with margin |

---

## 7. Device checklist items

**BE4.50** (`checklist-v4.50.txt`):
1. The menu shows **Drum Synth** as the 9th entry, and it makes a track with 16 labelled pads in the track colour.
2. Tap and Edit on every pad open the drum page. Load, Unload and Rename are greyed/inert on drum pads from the grid, the pad page and the header. They still work on a sample One-Shot and on a Loop track.
3. Knob edits (engine knob shows only Kick..Tom) are audible straight after creating the track, with no save/reload (precondition A on a built track).
4. Kicks play from the sequencer with conditions, retrigs, choke, mute and sends. Live hardware-pad play and step entry on drum pads work. The per-pad 0x1DF toggle (mute?) works and the LED follows it.
5. Re-strike: fast rolls and retrigs on one pad, in Poly and Mono, with Cutoff down, short Decay, mixed velocities and an LFO: no click, no double ring.
6. Knob edits survive save, power cycle and reload.
7. Copy/paste a drum pad within the track, and onto a sample One-Shot (it becomes an empty sample pad). Duplicate the track, and Paste Track. Undo after a step edit on the drum track (stock's undo is sequence-only, P4-0). Load a One-Shot preset/kit onto a drum track and a sample track if the firmware offers it.
8. Pack, then reload (gate 1 on a builder-made track).
9. Open the project on stock 1.6.8: silent, no crash, no toast. Re-save it, then reopen on BE4.50.
10. Load a project with 8 One-Shot sample tracks: load time, no glitches (push load).
11. M7 reset or retake, if one can be provoked: pads still sound afterwards (re-push). Otherwise note that it couldn't be provoked.
12. DRUMS stats line with 1, 6 and the cap's worth of ringing Kicks on SPACED: LOAD numbers (first device CPI). Check that the cap steal is inaudible as a click.
13. On a *stock* One-Shot track, is only the selected empty pad white? (Settles the gate-5 inference.)
14. Where does the track show its kind? Check P4-0's list (0.6): the Bank title, the step-view pad labels, the instrument menu (current marker?), the patch browser filter. Record what still reads "One-Shot" or "No WAV" (P4-D8).
15. The INST button on the Bank page with a drum pad selected opens the drum page, not the WAV browser (P4-0).

**BE4.51:** every row on every engine; turning Engine updates the titles, the label and the defaults; stock keys on rows 2 and 3 act; no sample control anywhere on a drum pad.

**BE4.60:** each engine by ear; LOAD with a busy kit; cap value confirmed.

---

## 8. Risks

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Precondition A fails | low / medium | contingency 3.7 |
| Precondition B fails (no clean pad-key edit path) | low / **fatal** | stock's 0xCF path is S-traced; NO-GO criterion 1 if the run disagrees |
| Builder pads not pushed until reload | was certain / high | builder calls PROP_PUSH; test + device item 3 |
| Re-strike click from the post chain (filter/env/LFO/velocity reset on the new voice) | medium / high | one-block crossfade in P4-2; click test after post chain; device item 5; NO-GO 2 |
| Re-strike handoff ordering (engine run twice, orphan late) | was certain at render time / high | handoff at VOICE_START; once-per-block guard; Rig order tests |
| Old engine lines bind to new sample pads (handle reuse across M4 boot, root reuse) | low-medium / high (breaks M3/M5) | boot-gen + handle check per line; table zeroed on take |
| Pushes before the M7 take lost; M7 retake empties the table | medium / medium | trampoline area_ok + drop counter; M4 re-push on generation change |
| Sample actions reachable from page 8 or other views | was certain with a Bank-only filter / medium | model choke-point filter; fallback wraps |
| Per-pad behaviour (0x1DF, play, step entry) gated on "has file" | low (P4-0: touchscreen only, plus the INST button) / medium | PAD_HAS_FILE hook; INST-button stub; device items 4, 15 |
| Clipboard copy zeroes the engine (type-0x17 holder) | was certain with the naive normalizer / medium | type-0x0E test; real copy/paste tests |
| The preset/kit importer moves an engine onto an unflagged track | low (P4-0: whole-track clone) / medium | old-track branch not run; device item 7 |
| A drum pad with MultiSam (0x3A) set skips the binder site | low / medium (pad silent or plays as sample) | pin 0x3A = 0 (P4-D11); Rig test |
| A file action reaches a drum pad through a caller the entry hooks miss | low (P4-0 lists all callers, S) / medium | hooks at the operations, not the posters; NAVIGATE gate |
| Push and XML load on **every** One-Shot project | low / low-medium | flow control (latency, not loss); device item 10 |
| CPU: busy kit over budget at an unknown CPI | high / high | BE4.50 cap 6-8; stats line; efficiency pass; earlier quiet-end option |
| DSP window: four engines don't reliably fit | high / medium | C slice / -Os for non-hot code from P4-5 start; Clap may slip (P4-D9) |
| Engine sizes measured on modified Playground copies | certain / low-medium | re-measure on vendored upstream in P4-5 |
| The scratch buffer assumes a root's voices render consecutively | low / high | Rig tag test; fallback per-cell buffer is too big (16 KB), so failing this would need a different crossfade design (I) |
| Hook sites depend on this image's callee-saved registers | low / high | build.py byte checks at every site |
| LED wrapper clobbers the 0xC0 property arguments | was certain if unhandled / medium | save r0-r3; test |
| Menu action acts on an old track when the lock blocks the create | low / medium | handle before/after check; lock test |
| Drum track reads as "One-Shot" in some places | medium / low | P4-0 audit; P4-D8 |
| Another branch to 0x08091B46 | low / low | 0x08091B46 left stock |
| flag[0x1C] rewritten at runtime | very low / low | sentinel kept |
| A builder pad differs from a stock-Unloaded pad for Pack | low / medium | device item 8 |

**NO-GO criteria (drop drums):**
1. No clean edit path for pad keys (B).
2. Mono re-strike can't be made click-free on the device, crossfade included.
3. Stock 1.6.8 faults on a drum project on the device.

---

## Decisions (2026-11-16)

- **No DaisySP (Jeff):** the drum engines are our own lean designs (docs/p4-engine-spec.md: <= 60 instructions per sample
  per voice, <= 1.5 KB code, <= 320 B state). They go in at **P4-2**, replacing the DaisySP Kick, and BE4.50 ships all five
  engines (window measured: 10,254 B free once the DaisySP Kick is removed, M). **P4-D12 is overridden:** BE4.50 does not
  ship the DaisySP Kick. The KF1-KF3 profile stays as reference only.
- **No marker files, not even for a preview (Jeff):** the first playable drum kit is BE4.50 on the real Drum Synth track.
- **P4-D1 = (a)** (done in P4-0). Taken as recommended (Claude, told to Jeff): **P4-D2** keys 0x1A8 + 0x1A2; **P4-D4**
  SquareNoise HiHat (superseded by the lean HiHat); **P4-D7** action 0x10F4; **P4-D8** hook only the Bank title and the
  step-view "No WAV" labels, tracks named "Drums %d"; **P4-D10** Cut greyed on drum pads; **P4-D11** MultiSam (key 0x3A)
  pinned to 0 on drum pads. Still open for Jeff: **P4-D3** (sample pad pasted onto a drum track), **P4-D5** (Load /
  Unload / Rename: disable recommended), **P4-D6** (engine cap: with lean engines, start at 16, tune from the DRUMS line).

## Open decisions for Jeff

**Settled:** P4-D1 = (a), done in P4-0.

**Carried from S7:**
- **D-S7-1** All Kick in BE4.50, a kit layout later. Recommendation: unchanged.
- **D-S7-2** Keep stock's envelope row. Recommendation: unchanged.
- **D-S7-3** One shared set of 7 knob values, reset to defaults on an engine change. Recommendation: accept. With native keys and no spare keys it is the only practical option (S).
- **D-S7-5** is replaced by P4-D5.

**New:**

| # | Decision | Options | Recommendation |
|---|---|---|---|
| P4-D1 | Fix for the broken drums build (libm guard vs dsp_lib's `powf`/`sinf`) | (a) rename to `be_powf`/`be_sinf`, mapped in dsp/shim's math.h/cmath so the vendored DaisySP calls them; (b) exempt symbols defined in our own objects | **(a).** In a linked ELF every symbol is defined, libm members included, so (b) can't tell them apart. (a) keeps the guard strict (S, verifier-confirmed) |
| P4-D2 | Keys | engine 0x1A8 + flag 0x1A2, or another pair from the 19 | **0x1A8 + 0x1A2.** Measured end to end in the prototype; 0x1A0 + 16 others stay for Track FX |
| P4-D3 | A **sample** pad pasted onto a drum track (engine 0, with a file) | (a) it plays as a sample, and the page, label and filters act as stock for that pad (every stub tests engine != 0); (b) the normalizer sets engine = Kick and clears the file; (c) the paste is refused on drum tracks | **(a).** Nothing is destroyed or silently ignored (D-S7-4 reasoning). It bends F1 (a drum track can hold a sample pad); choose (b) if you read "no sample controls" strictly. (c) needs another hook in the paste path |
| P4-D4 | HiHat variant | SquareNoise/Linear, 220 B (fits 384 B cells); RingMod/Swing, 464 B (512 B cells, needs the cells to start at 0x1A00) | **SquareNoise/Linear**, subject to re-measurement on the unmodified upstream source |
| P4-D5 | Load / Unload / Rename on drum pads | hide; disable (greyed via 0x6C, inert at the model); reroute Load to the drum page | **Disable.** Visible but inert is honest, and the model-level filter makes it inert from every view |
| P4-D6 | Drum-engine cap | (a) no cap; (b) a fixed cap in BE4.50 (build constant) with oldest-steal over a one-block fade, retuned from device numbers; (c) (b) plus ending drum voices at about −72 dB instead of −96 dB | **(b) at 6, raise after BE4.50's DRUMS line; try (c) only if the tails audibly cut.** (a) risks the first kit dropping out in its own acceptance test. 6 ≈ 25 % at 1 CPI on top of SPACED's ~60 % (I) |
| P4-D7 | Menu action id | reuse 0x10F4 (ex-Analog, reviewed) or the next free id after 0x10F3 | **0x10F4.** Already reviewed, nothing else uses it |
| P4-D8 | Track identity outside the menu | (a) accept "One-Shot" everywhere, name tracks "Drums %d"; (b) hook every surface in 0.6's list; (c) hook the two high-prominence surfaces only (Bank title, step-view "No WAV" labels) and accept the rest | **(c) plus the "Drums %d" name** (P4-0 audit done, 0.6). The "No WAV" labels are not optional in practice: every drum pad would read "No WAV" on the step page, which misleads. The menu cell, page-8 title and browser filter are medium/low and cost a hook each |
| P4-D10 | Cut on a drum pad (Cut = copy + UNLOAD; the UNLOAD hook makes it copy-only) | (a) accept copy-only for drum pads; (b) make UNLOAD on a drum pad reset it to engine Kick + defaults instead of refusing; (c) grey Cut for drum pads | **(c).** Consistent with P4-D5 (inert, greyed): a Cut that leaves the pad behind looks like a bug. (b) is more useful but invents a meaning for Unload |
| P4-D11 | MultiSam (0x3A) on drum pads skips the binder site | (a) pin 0x3A = 0 on drum pads (builder, normalizer; D5 never shows it); (b) also hook the 0x08095900 / 0x08087D60 path | **(a).** 0x3A has no meaning for a drum pad, the page doesn't offer it, and (b) is a third M7 site for a state the UI can't reach. Revisit only if hand-edited XML matters |
| P4-D12 | Which Kick BE4.50 ships, given docs/p4-engine-spec.md (own engines) | (a) today's DaisySP Kick unchanged, cap 6, lean engines in P4-5; (b) KF1 (bit-identical, −44 %) as interim glue; (c) pull the spec's lean Kick forward into P4-2 | **(a).** BE4.50's job is proving the track (A, B, re-strike, cap, CPI line); today's Kick is already golden-tested. (c) puts an engine still on the review's fix list (re-strike clipping) into the riskiest kit. (b) is work thrown away in P4-5 |
| P4-D9 | Clap in BE4.60 | (a) ship all four engines; (b) Snare, HiHat, Tom first, Clap in a later kit if the window allows | **(b).** Clap is the least-sized engine (1-2 KB) and the window doesn't reliably fit all four |

---

## Review log

Two verifier passes reviewed the designer's draft (same date). Every issue is listed below with its disposition. None was dropped outright. Where the two verifiers overlapped, the fixes were combined. Spot checks by hand at final review: S7 plan lines 195-206 (render-time handoff, "next render", crossfade as a known risk): confirmed. build.py:1718-1750 (np_template handles only the type-6 run and always pads with 0xFF): confirmed. synth_shared.h:20-25/49 (the M7 zeroes on take; the boot field is the M4 boot generation): confirmed. All three are S.

**Verifier 1**
1. *(major)* **Builder pads never pushed.** Stock's PROP_PUSH runs before the builder; 0x73 and child order unspecified. **Applied:** builder recipe + PROP_PUSH (3.3), A measured on a menu-built track (0.4), test, mutation, device item 3.
2. *(major)* **Normalizer zeroes on clipboard copy** (type-0x17 holder). **Applied:** the normalizer tests OBJ_TYPE == 0x0E (3.2); real two-step copy/paste tests and mutation.
3. *(major)* **Bank-view filter misses page 8 and other posters of Unload/Rename.** **Applied:** model choke-point filter with a wrap fallback; template 2 header audit; tests from page 8 and 0x08147034's view.
4. *(major)* **PAD_HAS_FILE gates 0x1DF and cell refresh; the LED wrapper overrode 0x1DF.** **Applied:** PAD_HAS_FILE hook; LED wrapper skips 0x1DF pads; device item 4. 0x1DF = mute stays I.
5. *(major)* **Stale lines across M4 reboot; H1 removal.** **Applied,** merged with verifier 2 #3: boot-gen + handle per root-indexed line (3.5); H1's job moves there.
6. *(minor)* **B is mostly closed by stock's 0xCF path.** **Applied:** B relabelled S-likely, measurement in P4-0, D6 forwarding moved unconditionally into P4-3, risk likelihood lowered.
7. *(minor)* **The menu action can flag an old track under the model lock.** **Applied:** before/after handle check; lock test.
8. *(minor)* **Sparse engine ids vs ENC_TURN stepping; engine 0 reachable.** **Applied:** dense index 0..5, page range 1..5, M7 maps to ids. The BE4.22-4.31 compatibility note was updated (index 1 = old Kick id; 2-5 make no DK4 entry, S).
9. *(minor)* **The LED wrapper must preserve r0-r3** (0xC0 property). **Applied:** spec, test, mutation.
10. *(minor)* **F1 identity surfaces not audited.** **Applied:** P4-0 item, device item 14, P4-D8.
11. *(minor)* **The "no 0xFF pad" claim holds only in drums builds; np_template is type-6-only.** **Applied:** 0.2 row, N1 row, M1, template tests. Re-checked in build.py (S).

**Verifier 2**
1. *(major)* **Render-time handoff runs the engine twice and frees the orphan late;** same-voice reuse not handled. **Applied:** handoff at VOICE_START, once-per-block guard, orphan identified by (root, voice), same-voice case; Rig tests and mutations. **Added at final review (I):** the handoff and the crossfade are reconciled with a shared 512 B (root, block)-tagged scratch buffer, because the orphan renders before the owner. It relies on a root's voices rendering consecutively (S on the loop, I on no interleaving; a test plus a risk row).
2. *(major)* **Post-chain step at re-strike; crossfade and risk row dropped; weak click test.** **Applied:** crossfade in P4-2 scope and budget; risk row restored; click test after the post chain with an absolute threshold against a shifted-re-strike reference.
3. *(major)* **Handle-keyed table fills with sample pads; evict-oldest drops live lines; linear scan.** **Applied:** one line per fixed root (128), handle stored and checked, no eviction, O(1). Combined with V1 #5's boot gen.
4. *(major)* **Table lifecycle unspecified** (take zeroing, area_ok, pre-take pushes). **Applied:** zero in synth_take; trampoline gated on area_ok with a drop counter; M4 re-push on M7 generation change (3.6), which also absorbs precondition C.
5. *(major)* **Goldens against Playground's modified DaisySP.** **Applied:** vendored unmodified upstream + hashes + kick_ref-style host refs; the size figures in section 4 are marked provisional.
6. *(major)* **DSP budget arithmetic overstated.** **Applied:** "does not reliably fit"; placement plan from the start of P4-5; P4-D9 (Clap may slip); efficiency pass and crossfade named in the budget.
7. *(major)* **BE4.50 uncapped will likely drop out in its own checklist.** **Applied:** cap 6-8 as a build constant in BE4.50 (3.4); P4-D6 reframed as tuning; earlier quiet-end as option (c); checklist item 12 updated.
8. *(minor)* **"33 pads on 32 cells" is impossible; cell liveness undefined.** **Applied:** reclaim by last_block; steal, choke and quiet-end tests replace the 33-pad test.
9. *(minor)* **A is S-likely with the traced transport; fallback off the critical path;** int value. **Applied:** 0.4 A, 3.7 renamed contingency, risk likelihood lowered, integer descriptors, int-read test and mutation.

**Confirmed by verifiers** (kept as written): key absence and no P1/P3 clash; exact-key 0x1B6 test; synchronous UI_POST/0x42; kind-4 create shape; LED site only for 0x0E; CHILD_DUP callers and template references; stock 0xCF store path; PAD_OPEN branch logic; stock/BE4.41 opens and bento_* drop; X2 ignores engine 0; SAMPLER_PROCESS 0x3E layout and the 0x08091B46 behaviour; trampoline reach; root vtable and inbox; ENGINE_PROPERTY layout and flow control; PROP_PUSH without a value filter; legato early-return unreachable on One-Shot pads; binder sites and registers; 128 fixed roots; SA arithmetic; CPU arithmetic; detector location; F4 by design; P4-D1 reasoning.

**P4-0 review** (independent verifier, 2026-11-16; scratch/p4-0/V). Repair, A and B hold; no NO-GO. Applied:
1. 0.5 said six vacuous cases; it is seven (M). Fixed.
2. Destroy.test_a_thirteenth_object... is not covered by test_drum_ui (S). 0.5 now says so.
3. test_a_load_before_the_m7_took_the_area_is_kept is only partly covered (no X2 load before the handshake) (S). 0.5 now says so.
4. test_inst_xml's Stock test runs on a Wavetable, not a saminst, so it is not the rollback item (S). Docstrings and 0.5 corrected; the real check is P4-1's.
5. The README bullet ran past the line width. Rewrapped.
6. New caveat on A: the binder site depends on MultiSam = 0 (S). 0.6, 3.4, P4-D11.
7. A's unresolved caller 0x0813F8FA is Paste Track (0x0813F8EC, case 0x1003), not an M7 path (S). 0.6 C, 3.2.
8. Full runner on the drums build after the rewrite (the repair report had skipped it): every suite OK (M).
