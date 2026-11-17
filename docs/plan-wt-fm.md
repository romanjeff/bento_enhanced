# BE4.44 (proposed): FM on Wavetable oscillators (final plan)

Labels: **M** = measured (emulator or build, scratch copies only), **S** = read from stock 1.6.8 disassembly or from source,
**I** = inferred / estimated. No repo was edited: builds ran on copies under scratchpad `wtfm\` (be442 ae367ab copy, a copy of
main's working tree, a trial merge, CPU trees `cpu\tree`, `cpu\tree-gate`, reader prototypes `cpu\rd\`). This is plan.md
with two verifier passes applied (Review log at the end).

## 0. Status (2026-11-16)

- **Plan only, nothing built in a repo.** It rests on three investigations: the DSP window budget, a trace of stock's
  Wavetable render and kernel, and the reader and gate prototypes.
- **Verdict: GO, conditional on growing the DSP window by one 4 KB page.** WT FM and all five lean drum engines fit together
  only if the window grows. Recommended: extend the window to 0x080DA000 and move the two hold-shortcut stubs to the
  **0x080DF000 page** (between M7_LIMIT and the disk-mode image at 0x080E0000), leaving the M7 C slice untouched.
  With that and two drum-side savings already planned, **about 1.6 to 4.5 KB** stays free after everything, including an
  explicit line for the drums' efficiency pass (section 5). The worst case is now thin (1.6 KB against a 1 KB margin), so
  WT FM's size is measured in the merged drums build at every sprint exit.
- **CPU:** the Analog FM-off path (P3Identity, 1.84 % against a 2 % bound) does not pay for WT FM. The gate prototype
  measured 1.72 % (codegen drift, not banked). An untagged WT oscillator costs +5 instructions a block (M). An FM'd WT
  oscillator costs about +50 % of stock's WT render (I, from M parts). A full WT FM voice comes out below Analog's full FM
  voice (I).
- **Corrections to the feasibility doc** (docs/feasibility-wt-fm-matrix.md):
  - Stock's kernel costs 15 to 16 instructions a sample, not 9 to 10 (M).
  - The recommended reader costs 29.1 a sample, not 16 to 22 (M).
  - The handback does **not** need to save the note's mip. The reader never writes +0xCC/+0xD0/+0xD4 (I, from the M phase
    test).
  - Storage in the guard is confirmed, **but only for bytes inside the saved runs**: stock rewrites k+0x00..+0x5F at every
    wrap, so no WT-only state may live there unsaved (3.2).
- **Open technical precondition (before W2):** stock's mip levels are assumed phase-aligned; that is untraced on real
  tables (risk 13).

## 1. Requirements

### Must
1. **R1.** A Wavetable oscillator gets the same per-oscillator FM controls as an Analog one in BE4.43:
   - OSC1: **Sub FM, FM Atk, FM Dec**.
   - OSC2: **FM 1>2** (Sub FM only when OSC1 cannot feed B; see D2), **FM Atk, FM Dec**.
   - Same keys (0x18B, 0x132, 0x1A4, 0x1A0), same index law I = 16 (v/1000)^1.5, same envelope spec (p3_golden fm_env), and
     the same 0..1000 / 0..127 / 0..128 ranges.
2. **R2.** FM 1>2 works for every carrier and modulator pair: Analog to WT, WT to WT, WT to Analog. B is OSC1's
   **pre-Level** signal times its increment, the same as BE4.42's an_feed.
3. **R3.** Through-zero linear FM with the same index line as Analog: di = fma(fma(dia, j, ia), a, inc), clamped to
   |di| <= 0.45. No PM.
4. **R4. Exact stock sound at FM = 0.** A WT oscillator never given WT FM work runs stock's render untouched. A tagged
   oscillator with gate 0 runs stock's render with byte-identical samples and identical persistent state (+0xC0..+0xD8),
   **including the note-start block**. Scope: "FM = 0" means no FM amount on this oscillator and no FM 1>2 drawn from it;
   a WT OSC1 that feeds a non-zero FM 1>2 is governed by D9.
5. **R5.** Changes are click-free in both directions: FM on and off, amount changes, the first push of a WT FM value, and
   Position changes under FM. A mip switch is never made without a crossfade.
6. **R6. Projects:**
   - Stock 1.6.8 opens a BE4.44 project.
   - BE4.43 opens a BE4.44 project. The keys are the same, so there is no new token.
   - No new native keys and no template change. The template stays 0x304, with no pad.
7. **R7.** The Analog FM-off path stays inside P3Identity's 2 % over BE4.40, re-measured on every change.
8. **R8.** The drums build (after the merge) fits WT FM plus all five engines plus the drums' efficiency pass in the DSP
   window, keeping **at least 1 KB** free.

### Should
- **S1.** Per-block mip from the peak-step bound, with hysteresis, so a high index does not alias at every note and a ramp
  near a threshold does not toggle.
- **S2.** The FM-off WT oscillator costs at most +40 instructions a block over stock (BE4.43 is +23; the gate prototype +28, M).
- **S3.** The full WT FM voice costs no more than Analog's full FM voice (28,929, M at BE4.43).
- **S4.** The black box shows WT FM as it shows Analog FM: the stats fields and the gate words.
- **S5.** A decision on WT DC in B, made from data (D4).

### Nice (not in BE4.44)
- WT FM amounts as mod-matrix destinations (feasibility (b), the LFO work, later as Jeff ranked it).
- A per-sample mip crossfade.
- An unrolled reader.
- Skipping the FM render when the envelope has decayed to 0.

## 2. What Jeff sees (user-facing rows)

| Row | Analog (BE4.43, unchanged) | Wavetable, BE4.43 | **Wavetable, BE4.44** |
|---|---|---|---|
| OSC1 | Level, Pitch, Shape, Type, Glide, Sub FM, FM Atk, FM Dec | Level, Pitch, Position, Type, then stock's empty slots | Level, Pitch, Position, Type, -, **Sub FM, FM Atk, FM Dec** |
| OSC2 | Level, Pitch, Shape, Type, Sync 1>2, FM 1>2, FM Atk, FM Dec | stock's | Level, Pitch, Position, Type, -, **FM 1>2, FM Atk, FM Dec** |

- **Knob numbers.** The FM controls keep their Analog knob numbers: slot 5 is FM, slots 6 and 7 are Atk and Dec. A
  Type flip leaves the FM knobs where they were.
  - Stock's WT rows have slots 4..7 empty (S). want_row currently returns early on `!me`, and needs a WT branch.
- **OSC2's slot 5.** BE4.43: Sub FM while OSC1 is Wavetable. BE4.44: every OSC1 type can feed B, so slot 5 is **FM 1>2
  in every combination**. OSC2's Sub FM becomes unreachable from the UI; its value is kept and saved. Behaviour change:
  see **D2**.
- **Brightness under light FM (new, I).** The mip is chosen from the peak step, at stock's 1.25 threshold. At note 60
  (note mip D = 8, step about 0.70) with the sub an octave down, the carrier drops to the next mip once
  0.70 x (1 + I/2) >= 1.25, i.e. I >= about 1.6, which is a **Sub FM amount of about 210**. So even light FM halves the
  carrier's harmonics (128 -> 64), and at I = 16 it plays from D = 64 (16 harmonics). The change is crossfaded, but it is
  an audible brightness step when FM is engaged, not a high-index corner. Decided in **D5**, measured by brightness rows
  at amounts 250 and 500 (7.4).
- **Texts.** Same as Analog: amounts 0..1000 as stock's widget shows them; times "0 ms" .. "10.0 s" and "Inf"
  (fm_time_text).
- **Resend on a Type flip.** The M4 resend (seven keys in BE4.43) must also run when the new Type is Wavetable. Check
  whether BE4.43 already does this (I); if not, about 1 line.

## 3. Design

### 3.1 Hook (S: exists)
- **Render, slot 0.** BE's A2 trampoline on WT_OSC_VT_SLOT 0x080ACBB4 slot 0 already runs `an_osc` first. It returns 0
  (stock render) when AnOsc.magic at osc+0x10C != AN_MAGIC.
  - New: on that early return only, `if (magic == WTFM_TAG && chk ok) return wt_fm_osc(...)`.
  - `wt_fm_osc` is **noinline, out of line**. an_osc gains only the compare on the non-Analog path.
  - Gate prototype: +72 B, P3Identity 1.84 % -> 1.72 % (M, not banked), untagged WT +5 a block (M).
- **Note start, slot 2.** `an_note` (before stock's note start), for a tagged WT oscillator: `fmk = AN_FE_START` (same
  retrigger as Analog) **and `mipcur = HOME`** (3.3). Without the second store, the first block of every note would see a
  stale mipcur against stock's freshly reset D = 1 and open the reader (verifier finding, accepted).
- **Properties, slot 8.** `an_prop` currently drops Sub FM, FM 1>2, FM Atk and FM Dec unless is_analog. Add a WT branch:
  1. **If the oscillator is untagged, first write the full default WT state** (3.2 "Tagging"), then store the incoming
     value. While untagged, the AnProps/AnOsc bytes are stock's table history, so they must never be read as settings.
  2. Recompute envf.
  3. Set or clear the tag (3.3).
  4. On OSC2's FM 1>2 key, and on OSC1's Type key, set or clear OSC1's **feed-B** bit (3.5).

### 3.2 Storage (option A, recommended)
- **Where.** The state lives in the WT oscillator's guard, osc+0x10C..+0x1CB, at the **same struct offsets as AnOsc /
  AnProps**:
  - magic (+0x10C) and the next word hold the tag pair;
  - fmk, msub/m12 and fme sit at their AnOsc offsets;
  - subfm, fm12, fmatk, fmdec and envf sit at their AnProps offsets (+0x1C0..+0x1CB);
  - **the WT-only state (mipcur, feed-B bit, frame-crossfade state: about 4 B) sits in a word adjacent to one of the saved
    runs, extending that run** (preferred: next to fmk). It must **not** go in the sync `events[]` (osc+0x118..), which is
    inside k+0x00..+0x5F and is rewritten by stock at every wrap (verifier finding, accepted).
- **Why these offsets.** `fm_setup`, `fm_env_step` and `an_feed` then work unchanged on a WT oscillator. That saves code
  in the tight window and leaves the Analog FM paths untouched.
- **Save and restore.**
  - The saved set is **four runs, about 32 B** (I): tag pair; fmk + WT state; msub/m12/fme; AnProps (12 B). The final byte
    count comes from the W1 audit; the earlier "16 B" and "28 B" figures are withdrawn.
  - Saved before and restored after **every** stock render or kernel call on a tagged oscillator.
  - Exact because stock writes the guard before reading it (M: write set +0x10C..+0x1CB, stale reads "none", 40
    voice-blocks). k+0x00..+0x5F is rewritten at every wrap; k+0x60..+0xBF on a frame change (S).
  - **mipcur is bounded on read** (stored as a mip index 0..6, HOME = 0xFF; any other value is treated as HOME and the
    reader re-enters from the note's mip). A corrupt byte can never index the 7-entry mip table out of range.
- **Tagging (untagged -> tagged).** an_prop writes, in this order: AnProps at defaults (subfm = fm12 = 0, fmatk = 0,
  fmdec = Inf, envf = 0); fmk = DEC at j 0 (or START at a note start); msub = m12 = 0; fme = 0; mipcur = HOME; feed-B from
  OSC2's current FM 1>2 value; then the tag pair; then the incoming value. Ramps therefore start from 0 and no envelope
  runs from table bytes.
- **W1 audit.** List every AnOsc and AnProps field that fm_setup, fm_env_step and an_feed read or write. Any field outside
  the saved runs is either added to a run or proven irrelevant to a WT oscillator.
- **Road not taken.**
  - A compact 24 B WT layout: fewer bytes saved, but needs pointer-taking variants of fm_setup and fm_env_step, more code,
    and a change to the Analog FM-on codegen.
  - Option B, a side table in SA spare: 16 roots x 10 voices x 2 oscillators x 24 B = 7.7 KB against 6.5 KB spare (I).

### 3.3 The exact-at-0 switch (three tiers)
1. **Untagged** (default; never given WT FM work, or handed back and untagged):
   - an_osc returns 0, so stock's render runs. Bit-exact by construction.
   - Cost: +5 instructions a block over BE4.43, +28 over stock (M).
2. **Tagged, gate 0.** No amount, no ramp memory, and mipcur == HOME. Tagged for one of three reasons: only the envelope
   is set; it feeds B (see D9 for level 0); or it is in its handback block.
   - `wt_fm_osc` saves the runs, calls S_WT_OSC_RENDER (0x08081C40, the stock body, not the trampoline), then restores.
   - If envf is set, it runs fm_env_step so the envelope keeps the note's time (Analog parity).
   - Note-start block: mipcur is HOME (set by an_note), so the gate stays 0 and stock's own pending ladder runs. Exact.
   - Cost: about 40 to 55 instructions for four save/restore runs, plus 39.5 for the envelope (I). Under 1 % of a WT
     oscillator-block.
3. **Gate != 0:** our reader (3.4).

**The tag.**
- It must be a value stock can never leave in the guard, because stock overwrites the guard with **table samples**.
- A single NaN-payload word (0x7FA5xxxx) can collide with a corrupt or hand-made float WAV holding that exact NaN.
- So the tag is two words: `t = 0x7FA50000 | (osc >> 4 & 0xFFFF)` and `~t` in the next word. +2 instructions on the tagged
  branch only (I).
- **Untag rule.** an_prop and the handback clear the tag only when amounts and ramp memory are 0, mipcur == HOME, envf is
  0 and feed-B is 0. Until then the oscillator stays in tier 2 or 3.

**Gate.**
- `x = msub | m12 | v_subfm | v_fm12 | (mipcur != HOME)`.
- HOME is a **state**, not a D value: "the reader is not holding a private mip". The reader writes a real index only while
  it runs; the mip-home block (handback option B) writes HOME after crossfading back to the note's D.

### 3.4 Our reader (rd_q, M prototype)
**Loop.**
- u32 Q32 phase, so a wrap in either direction is integer overflow and is exact.
- Analog's index line, `fma(fma(dia,j,ia),a,inc)`; for FM 1>2, the J*B term on the same line law.
- VMAXNM/VMINNM clamp at 0.45.
- `VCVT.S32.F32 #32` for the step, `VCVT.F32.U32 #32` for the fraction, two loads and an FMA lerp.
- 29 instructions a sample, 7,451 per 256-sample call, at D = 1, 8 and 64, and through zero at I = 16. 208 B (M).
- Matches a float64 model to <= 1.8e-6 at len 32 and <= 1.9e-7 at len 256 (M).
- Road not taken: rd_qt (2 a sample cheaper, but changes the index line's last bits, needing a second golden law); rd_f
  (+6 a sample, branches); stock's resampler (cannot go through zero: Q4.28 step must satisfy 0 < step < 8, S).

**Units.**
- inc = stock's rate / 2048. rate = f * 2048 / 96000 in base-table samples per 96 kHz sample (S+M).
- an_osc's rate_of is already bit-equal to stock's (M, plan-be440).

**Phase handoff with stock (I, from M).**
- On entry, Q32 phase = (idx + frac) / len_note * 2^32, from +0xC4/+0xC0 at the note's mip.
- After **every** reader block it writes idx and frac back in stock's units at the note's len.
- It **never** writes +0xCC/+0xD0/+0xD4. It reads at its own D' by shifting the phase (len'/len is a power of 2) and keeps
  D' in mipcur.
- So handback is "stop calling the reader". M: writing only +0xC0..+0xD8 and resuming stock is bit-identical to an
  uninterrupted run.
- The reader accepts frac in [0, 1]; frac == 1.0 can come from f32 rounding of Q28 (I).
- **Precondition (I, untraced):** the phase shift assumes mip k's sample i corresponds to sample i*2^k of mip 0. This was
  checked only on the plan's own synthetic saw mips. **Before W2**, trace stock's mip builder (the WAV load path that writes
  table+0x200000 and the other mip offsets) or cross-correlate mip D vs 2D on factory tables. If there is an offset, a
  per-level phase correction goes into both the reader and the golden, and the mip-home step is re-measured on real tables.

**Note-start block (reader active).**
- If the pending flag (+0xD8) is set, the reader first runs stock's mip ladder on that block's rate (6 compares, bit-exact
  in kmodel.py), then sets D/len/off, clears the flag and does idx %= len, exactly as stock does.
- Frame -1 means: take the frame with no crossfade.

**Mip under FM (S1).**
- Per block, peak step P = (|inc| + I*max|A| + J*max|B|) * 2048. Going **coarser**: D' = the smallest D with P / D < 1.25
  (stock's own threshold). Going **finer** (hysteresis): only when the finer mip's P / D < 1.05 (I; the exact value is set
  at W2 from the toggling test). D' equals the note's D whenever the index is small and the pitch unchanged.
  - Road not taken: threshold 1.5 (brighter, more mip changes); an rms-based bound (brighter, but aliasing at peaks).
    Chosen in D5.
- max|A| = inc_sub (known). max|B| needs a scan of about +2 a sample, fused where B is written.
  *As built (W2 review):* max|A| = inc_sub x the sub wave's peak (1; the Square's 1 + |1 - 2 pw|), passed to the reader
  (WtBlk.pa); a scan of A over one block followed a slow sub's phase at low notes and flipped the level with it. max|B|
  is a scan of every 4th sample in wt_play (not fused).
- **One change per block.** A block changes either the mip or the frame, not both. If Position changed, the frame
  crossfade (rd_qx) runs and a pending mip change is deferred one block (2.7 ms). Otherwise a mip change runs **rd_qm**
  (two mips, one frame, crossfade). This removes the two-mip x two-frame case (eight loads, two L values) from the design.
  The deferred block can alias slightly for 2.7 ms when the step grows (I); the aliasing rows include that case.
  *As built (W2 review):* "deferred one block" needs a memory. Without one, a Position that changes every block (an
  LFO, the knob) let the frame win every block and held the mip off for good, both ways (no coarser level under FM, no
  mip home after it). Rule: a frame change defers a wanted mip change and sets WT_MIP_WAIT (wflags bit 1); the next
  block changes the mip and defers the frame (the frame playing stays one block behind Position). So each change waits
  one block at most, alternating. The bit is set only while the reader holds a mip, so tier 2 never sees it set.
- At full index above about note 60, D' is pinned at 64 and aliases, as stock does above about 4.5 kHz (I). Documented.
- One block touches at most 384 contiguous samples per mip read, about 49 cache lines, against stock's 41 (I). An rd_qm
  block touches two mips (up to about 2x); hysteresis keeps those blocks rare. Device item 9 covers it.

**Entry and exit.**
- Entry block: start at the note's D (sample 0 equals stock's continuation), then crossfade D -> D' across the ramp-in
  block. msub and m12 already ramp from 0.
- Exit: handback B. The gate stays open one extra index-0 block that crossfades D' -> D, then writes mipcur = HOME; stock
  takes over after it. About 20 B, plus one extra reader block per FM-off transition.
- Road not taken: handback A, crossfading inside the ramp-out block (reads D content at a large step for 2.7 ms).

**Position under FM.**
- Stock changes frames only at a wrap, through the guard (S). Under through-zero FM, wraps go both ways and are frequent.
- In a block whose Position changed, rd_qx crossfades old -> new frame across the block: 39.1 a sample, 276 B as a
  separate loop (M).
- +0xC8 is set to the frame actually playing at the block's end, so a pending change after handback is crossfaded by stock
  at its next wrap.
- rd_qx stays a separate loop; merging it into rd_q (about +70 B) adds a per-sample cost to every block.

**Level 0.** Stock freezes the phase (S), and so does the reader, unless the oscillator feeds a non-zero FM 1>2. For that
case see **D9** (recommended: keep running for B, as Analog's E3(a)).

### 3.5 FM 1>2 from a Wavetable OSC1
- **Why not stock's render.** Stock's render applies Level in place (SMOOTH_APPLY on the scratch, S), so B cannot come
  from a stock render call.
- **Body.** The tagged OSC1 with feed-B runs stock's body itself:
  1. Table lookup through the vtable.
  2. Rate (rate_of) and frame (`usat8(trunc(PARAM*255))`).
  3. LEVEL_CURVE and SMOOTH_SET, and the silent check (modified per D9).
  4. Then **KERNEL 0x080A2000** (gate 0, with save and restore) **or our reader** (gate != 0).
  5. B = inc1 * scratch, written by an_feed (0 B new; or fused into rd_qb, +770 a block, 228 B, M).
  6. SMOOTH_APPLY and MIX_INTO.
- Size about 0.3 KB (I).
- Its output is **bit-equal to stock's render** on the Mixed rig in every block where it is not silent-while-feeding
  (D9 (b)); the divergence after a silent stretch is a named test.
- **Feed-B bit.** Set at an_prop time (on OSC2's FM 1>2 and on OSC1's Type), so the audio path never calls voice_of()
  for an untagged WT oscillator.
  - Self-heal: OSC2's FM-on render re-asserts the bit when it finds it missing. OSC2 then uses m12's target 0 for that
    block (silent by the ramp from 0), and B is there from the next block. This covers message order on project load.
- **WT DC in B** detunes OSC2 (the OD10/OD17 hazard again); decision D4.
- **Mixed sync** (WT1 master, Analog OSC2 slave) watches WT1's kernel phase and assumes a constant rate. Recommended for
  BE4.44: while OSC2 is an Analog slave synced to WT1, WT1's Sub FM plays as 0 (value kept). Decision D3.

### 3.6 Envelope reuse
- fm_env_step (out of line, 39.5 instructions a call, M) runs unchanged on the WT layout.
- an_note sets START and mipcur = HOME for a tagged WT oscillator.
- The BE4.43 "hold at 1 while unset" store is **not** added to the untagged WT path: an untagged oscillator has envf 0 and
  no state to hold.
- When an envelope or amount arrives mid-note, an_prop initialises fmk to "DEC at j 0" (env 1), BE4.43's "envelope set
  mid-note starts from 1", pinned for WT too.
- Retrigger is hard, as in BE4.43 (open point there, not reopened here).

## 4. Code size estimate (I, on M parts)

| Part | Bytes | Basis |
|---|---|---|
| an_osc tag compare + wt_fm_osc stub | 72 | M (gate prototype) |
| rd_q reader | 208 | M |
| rd_qx frame-crossfade loop | 276 | M |
| rd_qm mip-crossfade loop (two mips, one frame) | 220-300 | I (rd_qx-like; prototype at W2) |
| mip select with hysteresis, ladder replication, handback B, HOME logic, mipcur clamp | 250-400 | I |
| WT body (table, rate, frame, level, smoother, silent check, kernel call, four save/restore runs, two-word tag) | 450-700 | I |
| an_prop WT branch, default-state init on tagging, feed-B bit, untag rule | 200-320 | I |
| B from WT OSC1 (an_feed reuse) | 250-350 | I |
| envelope hook-up (an_note START + HOME, fmk init) | 60-100 | I |
| **Total DSP** | **about 2.0-2.7 KB estimated; planned against 2.4-3.8 KB** | budget uses the planned range until W3 measures |
| M4 rows, texts, resend | about 0.2 KB (planned <= 0.5 KB) | M4 headroom from the **merged** build at M0; p42a pre-merge has 10,156 B to PROVEN_MAX (M), shared with P4-3's drum UI |

## 5. THE BUDGET PLAN (DSP window)

**Starting point (M).**
- After merging be442 into main: **28,104 B used, 8,504 B free** of 36,608 (trial merge; re-verified independently).
  That is p42a + 24 B, the BE4.41 -> BE4.43 analog delta.
- Analog is 23,796 B. Drum code is 4,308 B: core 2,850, Kick plus adapter 1,438, padding 20.
- Re-measured after P4-2's in-flight step commits (M0 exit), since P4-2 edits build.py and the bases.

**Demand (I on M).**

| Item | Low | High | Owner |
|---|---|---|---|
| Four engines: prototypes sum 5,698 B (Snare 1,506 + HiHat 1,528 + Clap 1,308 + Tom 1,356; M on prototypes at -O2/mixed, so upper bounds at main's -Os) + shared knee 72 + about 40 B glue each | 5,930 | 6,300 | drums |
| Drum core growth (cap, stats, P4-2b minors) | 100 | 300 | drums |
| Drum efficiency pass (plan-p4-drums.md: "unbudgeted", a must-have before the engine count grows) | 300 | 1,000 | drums; range to be agreed with the P4 plan |
| WT FM DSP (section 4, planned range) | 2,400 | 3,800 | WT FM |
| **Total new** | **8,730** | **11,400** | |
| **Free without buy-backs** | **-226** | **-2,896** | |

WT FM plus the full drum set does **not** fit without making room.

**Buy-backs, in order (each a separate, reviewed commit; re-measure after each).**

| # | Buy-back | Bytes | CPU | Risk | Lane |
|---|---|---|---|---|---|
| 1 | **Grow the window to 0x080DA000.** Move CTRL_INPUT (296 B) to **0x080DF000** and CTRL_STARTUP (88 B) to **0x080DF800** (today's relative layout, one page up past M7_LIMIT). Set M7_DSP_LIMIT = 0x080DA000. M7_LIMIT and the C slice are unchanged. | **+4,096** | 0 | Low (below) | W0, on main |
| 1b | *Only if the 0x080DF000 page fails on the device:* CTRL stubs at the top of the C slice, reserving 0x200 (M7_LIMIT -> 0x080DEE00; C slice 4,085 -> about 3,573 B free, M/I) | +4,096 | 0 | Low | W0 |
| 2 | Drum control-rate core at minsize (dr_bind with handoff and take_cell, dr_prop) | **+136** (M on the main copy) | about 0 (control rate) | Low | P4-2b |
| 3 | Shared drum maths: one exp2/decay/coef for all five engines | **+300 to +500** (I; spec 0.4-0.8 KB) | 0 to one call a block | Low; already a precondition for the Snare | P4-2b |

**Result with buy-backs 1 (or 1b), 2 and 3.**
- Worst case: 8,504 + 4,096 - 11,400 + 136 + 300 = **1,636 B free**.
- Best case: 8,504 + 4,096 - 8,730 + 136 + 500 = **4,506 B free**.
- With WT FM at its estimate's top (2.7 KB) and every drum item at its high end: about **2.7 KB free**.
- Both bounds are above the 1 KB margin, but the worst case is thin. Gate: **at W3's exit, the measured WT FM size plus
  the drums lane's then-current sizes must leave >= 1 KB**; if not, take the reserve in order before W4.

**Why buy-back 1 is low risk (S/M), and what W0 must change.**
- The 0x080D9000 page holds only the two CTRL stubs (384 B; 296 B at 0x080D9000, 88 B at 0x080D9800, M). 3,712 B are zero.
- No stock word valued in 0x080D9000..0x080DA000 exists (M). The only stock words valued in 0x080DA000..0x080E0000 are
  0x0808586C = 0x080DF04F and 0x081AF024 = 0x080DA88B (M); neither is in the new window, so DSP_WINDOW_WORDS is unchanged.
- **New caution (I):** 0x080DF04F points into the page the CTRL stubs move to. Stock's flash there is zero, so it cannot be
  a live code pointer in stock (calling it would fault), but W0 decodes every PC-relative load of 0x0808586C (pclit.py
  method) and records the result in the base note before moving the stubs. If it is read as a pointer, use 1b.
- The 0x080DF000 page has never run our code; **neither has the C-slice top** (the C slice ends near 0x080DE00A in every
  image; max m7 .text 15,376 B over about 120 builds, M). The earlier "C-slice top already runs code" claim was false and
  is withdrawn. Both are in the same 128 KB flash sector (0x080C0000..0x080DFFFF) and the M7 executable range, and Lab
  1.6.1 ran FX code from 0x080DC000 upward (S, docs/bento-os.md). So the DF page is preferred: same device risk, no C-slice
  cost. The page ends at 0x080E0000, where the disk-mode image starts; the stubs end at 0x080DF900.
- The window cannot move down. The guard, 4 KB alignment and .data end make 0x080D0000 the floor (S).
- PROVEN_MAX limits only the image length, which the M4 tail sets; the image size is unchanged.
- **W0 build.py changes (S, from be442 build.py:741 and :1389-1407):**
  - The S6a guard `lo < hi <= S.CTRL_INPUT` stops protecting anything once CTRL_INPUT moves above M7_TRAMP (it would accept
    a window up to 0x080DF000, over the trampolines and the C slice). Replace it with `hi <= S.M7_TRAMP`, and fix its
    message.
  - Add a separate assertion: [CTRL_INPUT, CTRL_STARTUP + 0x100) is disjoint from [M7_DSP, M7_DSP_LIMIT) and from
    [M7_TRAMP, M7_LIMIT), and ends at or below 0x080E0000.
  - Mutation tests: a window limit of 0x080DB000 is refused; CTRL_INPUT inside the C slice is refused.
  - Edit the two CTRL base entries (re-reviewed notes) and M7_DSP_LIMIT.
  - test_controls' hold-shortcut tests re-run; one device check (D-W0).
  - The default build is no longer byte-identical to m1-be441, so DefaultImage gets a reviewed expected change.

**Reserve (in this order, only if a measurement needs it).**

| # | Option | Bytes | CPU | Note |
|---|---|---|---|---|
| R1 | Tom and Clap at -Os, with apply/trigger at minsize | -100 to -300 (I) | control rate | P4-5 lane |
| R2 | Four analog minsize changes (an_feed -176, reshape_low, low_step, fm_slow) | -596 (M) | FM-on and corner paths only; FM-off saw/square untouched. Re-gate Fm12/SubFm/ShapeLow costs | Low to medium |
| R3 | ENV_R computed per block | -416 (M) | +15 to 20 per decaying oscillator-block (I) | Spec change to p3_golden fm_env and FmEnv's table test, not a tolerance change |
| R4 | Integer drum control code (dr_prop, dr_bind) into the soft-float M7 C slice | -830 to -970 (I) | 0 | Medium: cross-ELF symbols. No longer competes with buy-back 1 (DF page); with 1b about 3.5 KB of C slice remains |
| R5 | Shift M7_TRAMP/M7_CODE up 4 KB (second page) | +4,096 | 0 | Medium: every M7 trampoline address moves; test_diag's 0x080DA2B1; 0x081AF024 enters DSP_WINDOW_WORDS. Conflicts with CTRL in the DF page, so only with 1b |

**Rejected.**
- Removing the analog plateau loops (-2,672 B): held sine and triangle voices cost about 1.8x more; touches an_render.
- analog_kernel.cpp at -Os (-1,804 B): an_render is the FM-off hot path, and the bound has only 0.16 % left.
- Drum files at -O2 (+2,232 B). minsize on run_flat (+28 B). Shrinking DSP_TRAMP_BYTES (<= 54 B).
- Dead code: about 0. --gc-sections already drops unreferenced code, and BE4.42 took out the rest.

**Fallback if both CTRL placements (1 and 1b) fail on the device.**
- Worst case: 8,504 - 11,400 + 136 + 300 = -2,460; with R1+R2+R3+R4 (1,942 to 2,282) it is **-518 to -178 B**: does not
  fit. Best case: 8,504 - 8,730 + 636 + 1,942 = **+2,352**: fits.
- So the outcome depends on measured sizes. At W3, in order: take R1-R4; slip B-from-WT-OSC1 (about -0.3 KB) to a later
  release; merge rd_qx into rd_q (about -0.2 KB, small per-sample cost); then the drums lane's own lever (P4-D9: Clap may
  slip). **None of these is automatic**; each is chosen from W3's measured numbers and Jeff's ranking.

**Budget restatement (replaces the per-feature "P3 ceiling" of 24,044 B).** WT FM takes analog past that self-imposed
ceiling (248 B left at BE4.43). In a 40,704 B window:
- **analog + FM <= 27,600 B** (23,796 + 3,800 planned WT FM);
- **drums (core + five engines + efficiency pass) <= 11,500 B** (worst case of the demand table: 2,850 + 1,438 + 20 +
  6,300 + 300 + 1,000 - 136 - 300 = 11,472);
- **unallocated margin >= 1,024 B** (the two caps leave 1,604), never spent without Jeff's word.
- **M4:** WT FM <= 0.5 KB; the rest of the merged build's PROVEN_MAX headroom (quoted at M0) is the drums lane's for P4-3.

Each DSP limit is checked by build.py from the ELF's symbol ownership (by file), with the same failure style as
check_dsp_assumptions. If the efficiency pass needs more than 1,000 B, the drums cap is renegotiated before code lands.

## 6. CPU plan against the 2 % bound

| Path | Figure | Label | Rule |
|---|---|---|---|
| Analog FM-off (P3Identity vs BE4.40) | 1.84 % at BE4.43; 1.72 % with the gate prototype | M | Don't bank the drop: codegen noise is about +/-0.15 % (I). All WT FM work stays in noinline wt_fm_osc; an_osc only gets the tag compare on its non-Analog return; an_render and an_render_fm are **not** edited. P3Identity re-run at every sprint exit. **Stop line 1.95 %**: if crossed, rework the layout (attributes, moving the compare) before anything else. |
| Untagged WT oscillator | +5 a block over BE4.43 (+28 over stock, 0.40 %) | M | WtOff.test_cost: <= stock + 40 a block (S2) |
| Tagged WT, gate 0 (envelope only, feeds B, handback block, note-start block) | about 40 to 55 + 39.5 env | I | Gated at <= 1.02x stock's render. Parallels BE4.43's accepted "envelope set, FM-off +1.1 %" |
| FM'd WT oscillator-block, steady | about 10.4 to 11.2k against stock 6.7 to 8.7k (about +3.7k, +50 %) | I on M parts | Reader 7,451 (M) |
| Position-change block | rd_qx +2,570 (M) | M | |
| Mip-change block | rd_qm about +2.5 to 3k (I) | I | Prototype and count at W2; hysteresis keeps these rare; never in the same block as a frame change |
| WT voices | Sub FM OSC1 only: about 22k. Both: about 26 to 27k. Stock WT1 feeding an FM'd WT2: about 18k. +2.6k per FM'd oscillator with a Position LFO | I | WtFm.test_cost: full WT FM voice (note 60, I = 16) <= 28,929 (S3). With Position moving every block, <= 35,309 (heaviest existing voice). **New cases:** Position LFO plus a decaying FM Dec at once (mip and frame both wanting to change); an index ramp sitting on a 1.25 x 2^k threshold (toggling) |
| Device | reader about 30 to 40 cycles a sample; full WT FM voice about 40 to 65k cycles = 3 to 5 % of a 1,277,952-cycle block | I | cpi-report lever #1 (IRQ-free cycle sampling) before judging on the device. Unrolling (about 24 to 25 a sample, +150 to 300 B) held in reserve |

**Not done first:** the cpi-report VDIV and branchless levers. The reader has no VDIV and no FP compare (M); VDIV exists
only on the Analog FM-on path; VSEL counts the same as a branch on the offline metric; both put P3Identity's byte identity
at risk (-0 and NaN with VMAXNM).

## 7. Tests

### 7.1 Independent golden: the table reader (p3_golden.py, new `wt_read`, numpy float64, shares no C)
- **Written from this spec before the M7 code**, as fm_env was: the mip layout (7 mips, offsets, len, and the per-level
  phase correction if the W2 trace finds one); the phase as a float64 fraction of a cycle; linear interpolation at the
  chosen mip; through-zero wrap both ways; the per-block mip rule (1.25 coarser, hysteresis finer) and its crossfade; the
  one-change-per-block rule; entry and exit (mip home) crossfades; the frame crossfade across a Position-change block;
  the level-0 rule (freeze, or D9 (b) for feeding); the index line from `index_line` / `an_index32`.
- Self-tests: FM = 0 at a fixed mip reproduces stock's pitch; a symmetric through-zero sweep returns to its start phase;
  the mip rule and hysteresis against a hand table.
- Golden mutants: wrap by clamp; mip fixed at the note's D; crossfade weights reversed; phase in the wrong units.
- **Tolerances:** device vs golden <= 2e-6 absolute at len 32, <= 2e-7 at len 256 (prototype 1.8e-6 / 1.9e-7, M).
  Through-zero, mip-crossfade and frame-crossfade blocks get one named bound each, set at W2 from measurements; none is
  loosened later.

### 7.2 Stock oracle: kmodel.py
- kmodel.py models stock's kernel, guard, crossfade and resampler; bit-exact on 1,728 emulator blocks (M).
- It becomes a fixture. Every handback test checks that the stock blocks after handback **equal kmodel's continuation from
  the handed-back state**, bit for bit, samples and +0xC0..+0xD8.

### 7.3 Identity at 0
- **Untagged.** Every WT case in test_analog (Mixed rig, both oscillators tabled, Position fixed and moving, note start,
  level 0, notes 24..120): stock 1.6.8 and BE4.44 give identical samples and oscillator memory (+0x10C..+0x1EF).
  Instruction delta <= +40 a block (S2). A BE4.43 project with every WT value at default plays byte for byte.
- **Tagged, gate 0** (envelope set with amount 0; handback block after the mip-home block):
  - samples bit-equal to stock; +0xC0..+0xD8 equal; the saved runs (including the WT-state word) restored exactly;
  - **three consecutive note starts, OscReset on and off: the note-start block is bit-equal** (catches a stale mipcur);
  - Position changes during tier 2 (stock's frame crossfade rewrites the AnProps range) keep the settings;
  - many wraps in tier 2 (stock rewrites k+0x00..+0x5F) keep mipcur, feed-B and the frame-crossfade state.
- **B from a WT OSC1** (D9 (b)): OSC1's output equals stock's render bit for bit while it is audible and has never been
  silent while feeding. A named divergence test: level 0 for N blocks while feeding, then level back: OSC1's phase has
  advanced (not stock's frozen phase), and the transition is click-free. Mixed sync under D3's rule plays exactly as BE4.43.
- **P3Identity** stays within 2 % (stop line 1.95 %) and byte-identical, all 50 cases.

### 7.4 Targeted tests
- Handback continuity: same mip, boundary step <= 0.02 (M: 0.0002..0.018 against stock's 0.39..0.53); after a mip
  excursion with option B, <= 0.02, not 0.21. **Repeated on factory tables** once the W2 mip-alignment trace is done.
- The note-start pending flag through the reader. frac == 1.0 accepted. idx always in [0, len).
- Ramps 0 -> 4 -> 0 over 3 blocks.
- **First push on an untagged oscillator:** a Position change (stock fills the guard with table bytes), then one Sub FM
  push: a clean ramp from 0, no envelope, no click.
- The envelope on WT (FmEnv's lines bit for bit, reusing the golden). Retrigger per voice. The mid-note "starts from 1" rule.
- The feed-B bit: set and cleared by OSC2's key and by OSC1's Type, in any message order (project load); the self-heal.
- The tag pair: a table holding the single-word tag value does not tag; a corrupted check word falls back to stock.
- mipcur out of range (forced byte) is treated as HOME; no out-of-table read.
- Type flips: WT->Analog->WT keeps the stored values through the resend; Analog->WT with FM set starts tier 3 cleanly.
- Mip pinned at 64 at high notes. The clamp at 0.45.
- Hysteresis: an index ramp parked on a threshold does not toggle the mip every block.
- Mip/frame in one block: Position LFO with a decaying FM Dec; the mip change is deferred one block, no click.
- Aliasing rows (6.3's metric) for a WT saw at notes 60, 72, 96 at 50 % and 100 %, plus **brightness rows at Sub FM 250
  and 500** at note 60 (D5's trade-off, measured).
- Costs: WtOff, WtFm, Fm12 with a WT carrier, the Position LFO case, the two new 6.x cases.
- Window budget assertions (section 5's restatement) and the W0 guard/CTRL assertions.
- test_native_props: stock 1.6.8 and BE4.43 open a BE4.44 project with WT FM values; BE4.44 opens BE4.43 projects,
  including the D2 case as a reviewed behaviour change.
- test_synth_ui: the WT rows, slot 5 on OSC2 in all four type combinations, texts, the resend on a flip to Wavetable.

### 7.5 Mutations (each must turn a test red)
1. The tag is a single finite-float word.
2. A missed restore of one run, **one mutation per run, including the WT-state word** (mipcur, feed-B, frame-crossfade).
3. The reader writes +0xCC/D0/D4.
4. +0xC8 is not updated to the playing frame.
5. The pending flag is ignored.
6. The mip is fixed at the note's D.
7. No mip-home block (handback option B removed).
8. Level 0 not frozen (non-feeding oscillator).
9. B taken after Level.
10. The clamp removed.
11. Wrap by compare that fails on reverse.
12. rd_qt's index law swapped in.
13. Frame crossfade weights reversed.
14. The feed-B bit never cleared.
15. The untag rule ignores envf.
16. an_osc's compare inlined into the Analog path; caught by P3Identity's cost.
17. Mixed-sync rule ignored.
18. frac == 1.0 rejected.
19. Tier 2 calls the trampolined slot instead of stock's body.
20. an_note leaves mipcur alone (no HOME at note start).
21. an_prop tags without writing the default state first.
22. mipcur read without the bound.
23. Hysteresis removed.
24. Mip and frame changed in the same block.
25. (build.py) the S6a guard reverted to `hi <= S.CTRL_INPUT`.

## 8. Sprints (each: offline exit, full suite green on both builds, a commit after each; push only on Jeff's word)

| Sprint | Where | Work | Offline exit |
|---|---|---|---|
| **M0 merge** | main, after P4-2's current step commits | Merge be442 (ae367ab) into main (section 11) | **Full merged build with a bento1.bin** (the trial built only the DSP side; its M4 compile failed on Bit Depth names). Default build = BE4.43's DSP (23,796 B) and passes BE4.43's suites. Drums build DSP size re-measured (28,104 B in the trial). Full test suite incl. test_drums. P3Identity 1.84 %. M4 headroom of the merged image recorded |
| **W0 window** | main | Buy-back 1: decode 0x0808586C's uses; move the CTRL stubs to 0x080DF000/0x080DF800; M7_DSP_LIMIT; the new S6a guard and CTRL assertions (section 5); DefaultImage's reviewed change; the budget restatement checks | Window 40,704 B. check_dsp_assumptions green, with its two new mutations red. test_controls hold shortcuts green. Drums build free = previous + 4,096 (M). Device item D-W0 queued |
| **W1 storage + switch** | branch be444 off main | The tag pair; an_prop's WT branch with default-state init; the WT-state word in a saved run; mipcur bound; the feed-B bit; tier 1/2 in wt_fm_osc; an_note (START + HOME); M4 rows, texts, resend; the field audit (3.2) | 7.3 untagged and tagged-gate-0 identity, incl. the note-start and many-wraps cases. P3Identity <= 1.95 %. WtOff cost. Mutations 1, 2, 14, 15, 16, 19, 20, 21, 22 red. **No reader yet: gate != 0 plays as gate 0** |
| **W2 reader + golden** | be444 | **First: trace stock's mip builder / measure mip alignment on factory tables.** Then p3_golden wt_read and self-tests; kmodel fixture; rd_q; ladder replication; per-block mip with hysteresis; rd_qm (prototype and count); one-change-per-block rule; handback B; rd_qx; level-0 rule | 7.1 tolerances. 7.2 handback bit-exact. 7.4 continuity rows (synthetic and factory). Mutations 3 to 8, 11 to 13, 18, 23, 24 red. Size measured: reader parts <= 1.5 KB |
| **W3 integration** | be444 | A and B into WT carriers (Sub FM on WT OSC1 / OSC2 per D2, FM 1>2 Analog->WT); envelope lines; ramps; E3 / skip; black box and stats; costs; brightness rows. **Moved from W4 (W2 review): D3's mixed-sync rule and mutation 17, before any W3 device image (W2's body already plays Sub FM on a WT OSC1). First: re-estimate section 6's WT rows and the S3 voice gate from W2's measured reader ("W2 as built")** | FmEnv lines on WT bit for bit. WtFm cost gates (S3), incl. the two new cases. Aliasing and brightness rows. P3Identity <= 1.95 %. **DSP size measured in the merged drums build: >= 1 KB free with the drums lane's then-current sizes, or the reserve is taken (section 5)** |
| **W4 WT OSC1 as B + release** | be444 | 3.5 body with feed-B; D9's level-0 rule; (D3's rule: moved to W3); D4 data (DC of the factory tables) and its decision; **D2's last case (deferred from W1): OSC2's slot 5 = FM 1>2 on an Analog OSC2 next to a Wavetable OSC1 (want_row's Analog branch, test_the_fm_slots)**; kit BE4.44 (default build); checklist-v4.44.txt; release notes | B-path identity per 7.3 and the divergence test. Mutations 9, 10, 17 red. **test_synth_ui: slot 5 on OSC2 is FM 1>2 in all four type combinations. A BE4.43 project with a hidden FM 1>2 on an Analog OSC2 next to a Wavetable OSC1 (it now plays): a reviewed behaviour change test (test_native_props / test_analog), or migrated to 0 if Jeff says so; checklist row.** All suites green on both builds. Sizes and CPU tables written into this plan |
| **merge back** | main | be444 -> main | Drums build: window free >= 1 KB plus whatever P4-2b/P4-5 still need (section 5 re-measured) |

## 9. Device checklist items (checklist-v4.44.txt; BE4.43's checklist first if not run)

1. **D-W0:** every hold shortcut works as on BE4.43 (stubs now at 0x080DF000). The only device risk of the window move; if
   it fails, rebuild with 1b and re-check.
2. A WT patch with all FM at 0, and a BE4.43 project with WT oscillators, sound identical to BE4.43 (A/B by ear on 3
   factory tables, Position swept).
3. Sub FM on WT OSC1, 0 -> 100 %, on 3 factory tables at notes 36, 60, 84: smooth, no stepping. **Note the brightness
   drop as FM engages (about amount 200 at note 60) and whether it bothers you (D5).** Note where the top of the knob
   becomes unusable.
4. FM 1>2 for Analog -> WT, WT -> WT and WT -> Analog. Listen for detune of OSC2 from WT DC (D4).
5. FM Atk / FM Dec on WT behave like Analog's: pluck, swell, Inf hold, retrigger per voice.
6. FM from 0 up and back to 0 during a held note: no click in or out (the mip-home block).
7. Sweeping Position with FM on: no click, no zipper. Same with an LFO on Position, and with a short FM Dec at once.
8. Mixed sync (WT1 master, Analog slave, Sync 1>2 on): the D3 rule as specified.
9. **CPU line:** 10 voices, both oscillators WT-FM'd, low note (D = 1 territory), moderate index (the SDRAM-miss case);
   the same at a high note; the same with a Position LFO. Compare with the Analog full FM voice, using cpi lever #1's
   IRQ-free sampling if it is in.
10. Stock 1.6.8 opens a BE4.44 project saved with WT FM values. BE4.43 opens it and plays the WT oscillators as stock.
11. A WT OSC1 feeding FM 1>2: OSC1 Level to 0 and back during a note (D9): no click, OSC2's FM continues.

## 10. Risks

| # | Risk | Label | Mitigation |
|---|---|---|---|
| 1 | SDRAM cache misses under FM (moderate index, low carrier, many voices); rd_qm blocks read two mips | I, device only | Footprint about 1.2x stock's per block, up to 2x in a mip-change block (I); hysteresis. Device item 9. Remedies: cap the index by note, or a voice count limit |
| 2 | Codegen drift eats the 0.16 % P3Identity margin | M (noise +/-0.15 %) | Everything out of line; stop line 1.95 %; re-measure every sprint |
| 3 | The window move breaks hold shortcuts on the device | I | Neither candidate page has run our code (M); same sector as code that has. Device item 1; 1b next; fallback in section 5 |
| 4 | A missed save/restore loses WT FM settings or WT state | I | All WT state in saved runs (3.2); mipcur bounded on read; mutation 2 per run; many-wraps and Position-change tests in tier 2 |
| 5 | Tag collision with a user table's NaN | I | Two-word, per-oscillator tag with a check word |
| 6 | A user wavetable's DC detunes OSC2 under FM 1>2 | I | D4: measure first, then decide |
| 7 | Mip pinned at 64 at high notes and full index, so it aliases | I | Same class as stock above 4.5 kHz; documented. Optional note-dependent index cap later |
| 8 | Merge conflicts | M (trial merge: DSP clean, M4 compile failed on Bit Depth names) | M0 is its own sprint with a full image as exit; expected conflicts listed in section 11 |
| 9 | Drum engines or the efficiency pass grow past their ranges (HiHat has 8 B margin) | I | Efficiency pass now budgeted (300-1,000 B); drums cap 11,500 B; 1 KB margin; reserve R1-R5; W3 gate |
| 10 | Props lost when stock's ctor rebuilds the oscillator (voice steal, project load) | I | Values come back with the push and resend; an_prop's tagging init makes that safe. Test with the RealEngine-style ctor path |
| 11 | D2's behaviour change surprises users of BE4.43 WT OSC1 + Analog OSC2 Sub FM patches | S | Release note; no migration (no saved dev projects, memory 2026-11-09) |
| 12 | Tier-2 calls to stock's render recurse or double-hook | I | Call 0x08081C40 (the body) directly. Mutation 19 |
| 13 | **Stock's mip levels are not phase-aligned** (a causal decimator would leave a fractional offset per level), so mip crossfades comb-filter and entry/exit carry a phase step | I, untraced | Trace or measure before W2; per-level phase correction in reader and golden if needed; continuity rows on factory tables |
| 14 | **Stock word 0x0808586C = 0x080DF04F** points into the CTRL stubs' new page | M (value), I (use) | W0 decodes its PC-relative loads first; if used as a pointer, use 1b |
| 15 | Mip and frame both need to change in one block; deferral aliases briefly | I | One-change-per-block rule; aliasing row for the deferred block |

## 11. Merge order between be442 and main (drums P4-2 / P4-3)

**Recommended order.**
1. **Wait for P4-2's in-flight step to commit.** It is editing main's working tree now (uncommitted: build.py,
   bases/stock_161.py, bases/stock_168.py, README.md). No merge or build runs from main until then.
2. **M0: merge be442 (ae367ab, BE4.43) into main next,** before P4-2b's engines and before P4-3's M4 work. Reasons:
   - Main is **8 commits** past the merge base 435b2f0, but only **2 are code** (0808101 P4-0, 3597e8e P4-1); the rest are
     docs and logs (M, git log). It only gets harder from here.
   - The trial merge's DSP side was clean (28,104 B, M). Its M4 side was not built through.
   - **Expected conflicts** (files changed on both sides, M, git diff --stat): m4_synth.c (Bit Depth names); build.py
     (NP_TEMPLATE_KEYS against P4-1's generalised np_template, plus whatever P4-2's step adds); bases/stock_168.py and
     bases/stock_161.py (KEY_OSC_FMATK/FMDEC); synth_shared.h; tests expected_values.py, test_keys.py, test_native_props.py.
     Re-list after P4-2's step commits.
   - P4-3 rewrites much of the M4 drum UI; merging after it means resolving Bit Depth removal inside a bigger diff.
   - Key check (S): main's bases claim neither 0x1A0 nor 0x1A4 for drums (drum keys 0x1A2, 0x1A8, 0x1AE..0x1B4).
3. **W0 on main** (window +4 KB), between P4 sprints. It helps P4-2b at once; both lanes need it.
4. **Branch be444 off main after W0** for W1 to W4. P4-2b / P4-3 / P4-4 continue on main in parallel.
   - WT FM touches dsp/analog*.cpp, the M4 WT rows and test_analog. Drums touch dsp/drums.cpp, be_*.cpp, the M4 drum UI
     and test_drums. Overlap: m4_synth.c (different functions), build.py, bases.
   - Rebase or merge be444 onto main at every sprint exit, so **the budget check always runs in the merged drums build**.
5. **Release order.** BE4.44 (WT FM, default build) ships from be444 before BE4.50 (P4-3, drums); be444 then merges into
   main and BE4.50 carries WT FM. If P4-3 is ready first, the order flips and section 5 is re-measured before W3's exit.

**Road not taken.** Developing WT FM on be442 and merging once at the end: WT FM's size would only meet the drums' size at
the end (the failure this plan exists to prevent), and the merge would land on top of P4-3's M4 rewrite.

## 12. Open decisions for Jeff (with recommendations)

1. **D1. Grow the DSP window by 4 KB**, moving the hold-shortcut stubs to the 0x080DF000 page (C-slice top as 1b).
   - Recommended: **yes**. It is the only zero-CPU buy-back that covers the gap; the analog trims cost CPU or change the
     envelope spec. Cost: one device check of the hold shortcuts, and a build.py guard fix.
2. **D2. OSC2's knob 5 when OSC1 is Wavetable.**
   - Recommended: **FM 1>2**, as your requirement says. Every OSC1 can now feed B.
   - Consequence: OSC2's Sub FM is no longer reachable in any combination; its value is kept and saved. BE4.43 patches with
     WT OSC1 + Analog OSC2 Sub FM change sound (Sub FM stops, any hidden FM 1>2 value plays).
   - Alternative: keep Sub FM on OSC2 while OSC1 is WT. Drops FM 1>2 from a WT OSC1 and saves about 0.3 KB.
   - **Decided: yes (2026-11-16). As built at W1, one case is deferred to W4 (needs your OK):** every Wavetable row follows D2,
     but an Analog OSC2 next to a Wavetable OSC1 still shows BE4.43's Sub FM in slot 5 until W4, when a Wavetable OSC1 can
     feed B. Switching it at W1 would replace a control that plays with one that is silent on the W1-W3 device images.
     Side effect until W4: a BE4.43 project with a hidden FM 1>2 on that OSC2 tags OSC1 (feed-B) and pays tier 2 (+1.0 to
     1.6 % of that oscillator's render, inside the 1.02 gate) for no sound. At W4 that hidden value plays (W4's row in 8).
3. **D3. Mixed sync with an FM'd WT OSC1** (WT1 master, Analog OSC2 slave).
   - Recommended for BE4.44: **WT1's Sub FM plays as 0 while OSC2 is synced to it**; value kept.
   - Alternative: the reader emits wrap events for the slave: more code and per-sample work, and backward wraps under
     through-zero make "sync" ill-defined.
4. **D4. WT DC in B** (it detunes OSC2).
   - Recommended: **measure the factory tables' per-frame DC at W4**; accept if small, as Analog's OD17 was.
   - If large: subtract a per-frame mean computed at frame change (about 60 B; len adds per frame change).
5. **D5. Mip under FM.**
   - Recommended: **per block with crossfade, coarser at 1.25 (stock's threshold), finer only below about 1.05
     (hysteresis), one mip-or-frame change per block.**
   - Trade-off you should hear first (device item 3): light FM (Sub FM about 200 at note 60) already drops one mip, an
     audible brightness step. Alternatives: threshold 1.5 (brighter, a little aliasing at peaks, more mip changes), or an
     rms-based bound (brighter still, more aliasing at peaks). Brightness rows at 250/500 will show the size of the step.
   - Not recommended now: a per-sample mip crossfade (about 2x reads and cache footprint).
   - **Decided (Jeff, 2026-11-16): in between, leaning bright.** (W2: built as WT_MIP_THR; default 1.75 from the rows in "W2 as built";
     BE_WT_MIP builds another, its version suffixed -mipX.) Some aliasing under heavy FM is accepted as character
     ("we want this engine to make crazy and interesting sounds"). The coarser-mip threshold is **one named constant**
     (hysteresis band derived from it), with the default set well above stock's clean 1.25, toward the bright end. W2
     measures brightness and alias rows at a few candidate values (about 1.5 / 1.75 / 2.0, plus 1.25 as the clean
     reference) and picks the default; W3/W4 also produce alternate device images, one a step cleaner and one a step
     brighter (BE_TAG suffixes), so Jeff chooses the final default by ear. The rms-based bound and the per-sample
     crossfade stay out of BE4.44.
6. **D6. Budget restatement.** Replace the P3 ceiling (24,044 B) with analog+FM <= 27,600 B, drums (incl. efficiency pass)
   <= 11,500 B, unallocated margin >= 1 KB never spent without your word, and WT FM <= 0.5 KB of M4. Recommended: **yes**,
   with the efficiency-pass range (300-1,000 B) confirmed against the P4 plan.
7. **D7. Merge timing.** Recommended: **merge be442 into main as soon as P4-2's current step commits** (section 11), then
   W0 on main, then be444 for WT FM.
8. **D8. Tagged-but-FM-0 cost.** An envelope set with no amount keeps a WT oscillator on our wrapper: under 1 % of its
   render, for Analog parity. Recommended: **accept**. Alternative: don't track the envelope without an amount (cheaper,
   but a mid-note amount then starts from a fresh envelope, unlike Analog).
9. **D9 (new). A WT OSC1 at Level 0 while feeding FM 1>2.**
   - (a) Freeze like stock: B = 0, OSC2's FM 1>2 goes silent while OSC1 is muted. Bit-exact with stock throughout, but
     unlike Analog (E3(a)), and muting OSC1 to hear "pure FM" would not work.
   - (b) Keep running for B (Analog E3(a)): OSC2's FM continues; OSC1's phase diverges from stock's frozen phase after a
     silent stretch (inaudible while silent; the patch is not "FM = 0" anyway).
   - Recommended: **(b)**, for parity with Analog and because "mute the modulator, hear the FM" is the normal FM workflow.
     The R4 identity claim is scoped accordingly and the divergence has a named test.
10. **D10 (conditional). If both CTRL placements fail on the device:** the worst case does not fit even with the reserve.
    Choose then, from W3's measured sizes, among: slip B-from-WT-OSC1, merge rd_qx into rd_q, or slip an engine (P4-D9).
    No action now.

## W0 result (2026-11-16, branch be444)

Jeff's change to section 8: synth release first, drums paused. M0 is skipped (no merge into main now); W0 and W1 run on
be444, branched from be442 c071ad4. D1 yes, D2 yes, D8 accept, D9 (b); D5 open (the mip threshold stays one constant).

- **0x0808586C decoded (S/M).** It is an instruction, `mov.w r8, #0xd` (bytes 4f f0 0d 08), mid-function near
  0x08085800, the same decode from both halfword alignments. No PC-relative load reaches it (ldr T1/T2, ldrd, vldr, adr,
  every halfword decoded), no image word equals it, and no movw #0xF04F / movt #0x080D exists in the image. So its value
  0x080DF04F is not a pointer, and buy-back 1 (the 0x080DF000 page) stands; 1b is not needed offline.
- **Done.** CTRL_INPUT 0x080DF000, CTRL_STARTUP 0x080DF800, M7_DSP_LIMIT 0x080DA000 (stock_168.py and facts.py, notes
  re-reviewed). S6a's bound is now M7_TRAMP. New `check_ctrl_placement`: the stubs stay clear of the slice and the window,
  and below 0x080E0000 (literal_check: 0x080E0000 is a GENUINE constant, the sector-7 boundary). test_analog WindowGuards:
  windows to M7_TRAMP + 0x1000 and to CTRL_INPUT are refused by the bound; stubs in the C slice, in the window, or past
  0x080E0000 are refused; 1b's layout is accepted. Mutations: the guard reverted to `hi <= S.CTRL_INPUT` (mutation 25),
  the overlap test removed, and the end test removed each turn WindowGuards red (M).
- **Default image (M):** same length (0x189430). The only changes are the two site branches (one byte each:
  0x080404B6 `b.w 0x080D9800` -> `b.w 0x080DF800`, 0x0808DCDE `b.w 0x080D9000` -> `b.w 0x080DF000`), and the two stubs
  (296 B and 88 B) moved verbatim from 0x080D9000 / 0x080D9800 to 0x080DF000 / 0x080DF800. There is no DefaultImage test in
  the suites to update (section 5 assumed one).
- **Window (M):** default build 23,796 B used, 16,908 B free of 40,704. Trial merge of main's committed HEAD 3597e8e
  (throwaway, scratch) with BE_ENABLE=drums: 29,860 B used, **10,844 B free**.
- **Correction to section 5 (M).** Main's committed HEAD carries 6,064 B of drum code in the DSP window (main alone: 29,836
  drums vs 23,772 default), not 4,308 B. So the starting point is 1,756 B worse than the 28,104 B in section 5. That
  figure was probably measured on main's working tree with P4-2's uncommitted step (I; main's working tree was not read
  for W0). If the committed figure holds, the worst case after buy-backs 1-3 is about -120 B (1,636 - 1,756), below the
  1 KB margin, and the best case about 2.75 KB. Re-measure when P4-2's step commits, and before W3's gate.

### W0 as built (after the W0 review, 2026-11-17)

Two reviewers re-built 82804c6 byte for byte, re-diffed it against c071ad4, re-decoded 0x0808586C and the page, and ran
mutations. Their findings and what was done:

- **Accepted, fixed: build() wiring untested (both reviewers, low).** WindowGuards called the checks directly, so deleting
  the `check_ctrl_placement` call from build(), or passing `None` for the window, left every suite green. New
  `test_build_runs_both_checks_with_its_window`: it wraps `check_dsp_assumptions` / `check_ctrl_placement` (the real ones
  still run), runs `build.build()` on stock 1.6.8 into an existing directory (so it can never write), stops after the
  placement check, and asserts both ran in that order with the window `(M7_DSP, M7_DSP_LIMIT)`. The reviewer's other
  option, folding the checks into one `check_layout()`, was not taken: it still needs a wiring test, and folding in
  `check_region_assumptions` / `check_slice_assumptions` would pull build-state globals into a unit test.
- **Accepted, fixed: bounds tested only with slack (both, low).** New `test_the_bounds_are_exact`: the startup slot may
  end exactly at 0x080E0000 and not 4 B past it; stubs may touch our M7 slice and a window on either side, not reach 4 B
  into them (both edges of each).
- **Mutations (M, private copy, all of test_analog against be444w0r): every one turned it red:** the build() call
  deleted, window passed as None, overlap `lo + 4 < b`, overlap `a < hi - 4`, end `> END + 0x7c`, end `>= END`, the
  `check_dsp_assumptions` call deleted, and the S6a guard reverted to `hi <= S.CTRL_INPUT` (mutation 25). Baseline green.
- **Accepted, fixed: device images could not be told from BE4.43 (low).** The 1.6.8 version is now 4.44 (build.py
  BASE_SETTINGS, the per-base rule). The D-W0 device image is built with `BE_TAG=w0`: label "v4.44-w0"
  (~/bento/out/be444w0r-dev). Later sprint device images take their own tag (w1, ...), so 4.44 alone means the release.
- **Accepted, fixed: CTRL_INPUT note (info).** "The only stock word" holds for aligned words only; 0x080684EE =
  0x080DF028 (unaligned, `bic r8, r8, #0xd`) is also in range and is code. Notes in stock_168.py and facts.py now say so.
- **Accepted, fixed: facts.py CTRL_INPUT had no `note=` (info).** A regeneration would have brought back 1.6.1's
  "below M7_TRAMP"; facts.py now carries "(zero gap, above M7_LIMIT)". The generator itself (gen_table.py:240
  TypeError in KEY_RANGES) crashes on c071ad4 too; not W0's, left open, so stock_168.py stays hand-synced with facts.py.
- **Accepted, fixed: README hold-shortcut bullet mixed the bases (info).** Split into 1.6.1 (0x080D9000 / 0x080D9800,
  site 0x0808D1FE) and 1.6.8 since BE4.44 (0x080DF000 / 0x080DF800, site 0x0808DCDE).
- **Confirmed (info): the 6 Vendored.test_hashes failures on the trial were CRLF from `core.autocrlf=true`** in the
  throwaway clone. Trial clones from now on use `git -c core.autocrlf=false clone`. Re-run (M): an LF clone of 250fdb2
  with main's committed 3597e8e merged (not committed; test_keys / test_native_props conflicts taken from be444), built
  with BE_ENABLE=drums: DSP 29,860 B, **10,844 B free**, synth UI 22,670 B, image 0x18AEF8 (9,276 B to PROVEN_MAX);
  test_drums 33 OK (1 skipped), test_drum_ui 46 OK, test_controls 62 OK.
- **Process note (info):** the two reviewers collided in one mutation directory; the second reviewer's rows from that
  window are not relied on here (the mutation list above was re-run in a private copy).

**Image:** the default build changes from 82804c6's only in the version label (4.43 -> 4.44); DSP 23,796 B, window free
16,908 B of 40,704, M7 C slice 15,368 B, M4 24,424 B, synth UI 15,818 B, image 0x189430 (16,132 B to PROVEN_MAX). CPU:
the DSP code is unchanged from BE4.43, P3Identity 1.0184 worst / 1.0097 total. Suites: all green on be444w0r (test_analog
152). Still owed: device item D-W0 (every hold shortcut, image v4.44-w0).

## W1 result (2026-11-17, branch be444)

Storage and the switch, no reader: a gate that is not 0 plays as gate 0. Build ~/bento/out/be444w1 (default, 1.6.8).

- **Layout as built (S).** Tag pair at +0 / +4 (magic, then its complement in phase's word). WT state in AnOsc.note's two
  bytes (mipcur, wflags: bit 0 feed-B) next to fmk, and glide's word as wxf (W2's frame-crossfade state, 0 until then).
  msub / m12 / fme and AnProps are contiguous, so the saved set is **three runs, 36 B** (+0..+7, +140..+147, +172..+191),
  not "four runs, about 32 B". Run 2 (five words) is copied with one LDM / STM. Field audit: fm_setup, fm_env_step, feeds and
  an_feed read or write only fmk (+142), msub / m12 (+172 / +174), fme (+176) and AnProps (+180..+191), all inside the
  runs; an_render / an_render_fm (phase, delayed at +4 / +8) are never called on a Wavetable oscillator.
- **Props (S).** an_prop: a Wavetable oscillator's Sub FM / FM 1>2 / FM Atk / FM Dec go to wt_prop (default state, tag,
  value; wt_settle untags when idle); every Type and FM 1>2 message of either oscillator recomputes OSC1's feed-B
  (wt_feed). an_note: START and mipcur HOME on a tagged oscillator. an_osc's non-Analog return compares the magic's high
  half and calls wt_fm_osc (noinline), which checks the pair, saves the runs, calls stock's body 0x08081C40, restores, and
  steps the envelope (or holds DEC j 0).
- **M4 (S).** Wavetable rows: Level, Pitch, Position, Type, -, OSC1 Sub FM / OSC2 FM 1>2, FM Atk, FM Dec. Texts are
  fm_time_text's (unchanged code, already per handle). The resend now also runs on a flip to Wavetable.
- **Tests (M).** test_analog WtFmW1 (10 tests): tier 1 stock's bit for bit (samples and the whole voice after every
  block; notes 24..120, three back-to-back note starts, WTOscReset on and off, Position fixed and moving, Level 0); tier 2
  stock's bit for bit outside the runs, same notes plus 16 blocks at note 120 (many wraps), Position moving (frame
  crossfades), the runs holding the settings and fme the golden envelope's (p3_golden fm_env f32) after every block; a
  write hook shows stock's render writes every run (so each restore matters); default-state init; the untag rule; the
  bounded mipcur; the tag pair (render side and props side); feed-B in any message order; note start; Type flips with the
  resend. test_synth_ui: the WT rows and texts, the resend on a flip to Wavetable. Reviewed changes: P3Identity's
  test_q1_plays_no_amount ("osc2 wavetable" and "mixed" now tag a Wavetable oscillator: runs masked, tier 2's own
  instructions carved out and bounded at 120 a call, M 69 / 37), Props' WT-untouched case (defaults only now), test_synth_ui
  WT_ROW -> WT_ROW1 / WT_ROW2.
- **Mutations (M, private copies under scratch, each built and run):** 1 (check word ignored in wt_fm_osc; and in
  wt_tagged, caught after adding test_the_props_side_reads_the_pair_too), 2 (each of the three runs not restored), 14, 15,
  19 (infinite recursion), 20, 21, 22: all red. **16:** moving the tag compare in front of the AN_MAGIC test turns
  P3Identity red on its 2 % bound (worst 1.0210); inlining wt_fm_osc into an_osc costs the Analog path nothing (worst stays
  1.0184) and is caught only because test_q1's Span cannot find the symbol, not by cost.
- **Sizes (M).** DSP 24,548 B used, **16,156 B free** of 40,704 (W0: 23,796): **+752 B** = an_prop +116, wt_prop 188,
  wt_feed 112, wt_fm_osc 132, wt_init 64, wt_settle 58, wt_tagged 34, an_osc +40, an_note +16. Synth UI 15,858 B (+40). M4
  code 24,424 B. Image 0x189458, **16,092 B to PROVEN_MAX**. Drums trial: see the commit's report line below.
- **CPU (M).** P3Identity 50 cases: worst **1.0184** (mod dev=False, unchanged from W0), total 1.0094. Untagged WT
  oscillator-block: +27 over stock. Tagged, gate 0: +64..+107 over stock, worst **1.0158 x** stock's render (gate 1.02).

**Plan items found wrong or changed in W1.**
1. Saved set: three runs / 36 B, not four / about 32 B (msub..AnProps is one run).
2. Size: W1's parts are 752 B against section 4's roughly 400-500 for the same rows (an_prop branch 200-320 + envelope 60-100
   + stub 72 + part of the body). Most of it is control-rate code (an_prop and helpers, 572 B, already minsize / noinline).
3. 3.1 step 4 (feed-B on OSC2's FM 1>2 and OSC1's Type) is not enough: an Analog OSC2 leaving Analog forgets its FM 1>2,
   so OSC2's Type must recompute it too. Built: any Type or FM 1>2 message of either oscillator.
4. 3.2 Tagging: "first write the full default state, then the value" would leave our bytes in a BE4.43 project's
   Wavetable oscillators pushed at their defaults (k+0x60.. is rewritten only at a frame change), breaking 7.3's memory
   identity. Built: a default value on an untagged oscillator writes nothing.
5. D2 storage: a Wavetable OSC1's FM 1>2 and a Wavetable OSC2's Sub FM play nothing, so the M7 does not keep them (a hidden
   value cannot tag an oscillator); the M4 keeps and saves them.
6. D2 on an Analog OSC2 next to a Wavetable OSC1 is **deferred to W4**: its slot 5 stays BE4.43's Sub FM (which plays) until a
   Wavetable OSC1 can feed B; flipping it now would replace a working control with a silent one.
7. D8 "under 1 % of its render": tier 2's own work is 37 (no envelope) / 69 (envelope) instructions a call, better than
   section 6's 40-55 + 39.5, but with the +27 every Wavetable oscillator pays the total is 1.0-1.6 % over stock's render
   (within the 1.02 gate, not under 1 %).
8. Mutation 16 is caught by P3Identity's cost only in the compare-reordering form (and only past 2 %, not the 1.95 % stop
   line); see above.
9. The P3Identity set itself tags Wavetable oscillators (FM 1>2 333 on an Analog OSC2 next to a Wavetable OSC1 sets
   feed-B), a reviewed change the plan did not list.

### W1 as built (after the W1 review, 2026-11-17)

Two reviewers rebuilt f2effcd byte for byte, ran every suite, re-ran the plan's mutations and 8 + 18 of their own (all red),
and probed low notes and mid-note tag/untag. The fixes are tests and this plan only: **the image is unchanged** (rebuilt
~/bento/out/be444w1r, byte-identical to be444w1). Findings and what was done:

- **Accepted, fixed: the 1.95 % stop line was not a test (medium).** P3Identity.check now also asserts every case
  `<= P3_STOP` (1.0195), beside M1's 1.02. Mutation 16's reordering form (1.0210) is red on both. Rejected: pinning an_osc's
  Analog FM-off path to W0's exact instruction count. Codegen noise is about +/-0.15 % (section 6), so an exact pin would
  churn on legitimate W2-W4 changes, and the stop line is the plan's own rule.
- **Accepted, fixed: the inlining form of mutation 16 was caught only by a symbol lookup (medium, part of the same
  finding).** New `test_wt_fm_osc_stays_out_of_line`: wt_fm_osc calls stock's render body (control; FN's movw/movt +
  blx), an_osc branches to wt_fm_osc and never calls stock's render body itself. Inlining costs the Analog path nothing
  (M), so no cost test can see it. Red on m16b (the symbol is gone) and on m19 (control).
- **Accepted, fixed: no audit of other stock writers to a tagged area (medium).** New
  `test_only_render_and_note_start_write_a_tagged_area`. S: the kernel's read and reset on osc + 0x10C have three callers
  in all of stock's M7 code (sweep at both alignments): the render (runs restored), the note start (tested), and the ctor.
  M: a write hook on a tagged OSC1. Stock's slot 8 (keys 0x03, 0xC4..0xCC, six values each), the class's slots 3..15 and
  the ctor write nothing there. The area is unchanged except for the note start's START / HOME, because slot 3 is the base
  class's note-on and reaches slot 2. A table change writes nothing: the table is the object at osc + 0x18. So no stock
  path leaves the tag pair over foreign settings bytes, and W2 needs no extra clearing. Device-only paths (project load
  re-creating voices) go through the ctor or the props, both covered.
- **Accepted, fixed: low notes and mid-note tag/untag were not in the suite (low).**
  - `test_tagged_gate0_low_notes`: tier 2 at notes 24 and 36 for 48 blocks with Position fixed, both push sets, and note
    24 with Position moving slowly. Same checks as tier2(); tier2() now takes notes / pos.
  - `test_tag_and_untag_mid_note`: at notes 24 and 60, OSC1 untags at block 9, OSC2 tags at block 13 (block 7's Sub FM is a
    D2 leave). Samples are bit-equal and the voice equals stock's outside the runs of any oscillator tagged so far. Once a
    frame change and a wrap have passed after the untag, OSC1's whole area is stock's again.
  - compare() takes a per-block tagged set and mask.
  - Red on mutation 2's run-0 build (9 failures). The run-1 and run-2 builds are red on the slow-Position low-note case.
- **Accepted, fixed: tier 2's own bound in P3Identity was loose (low/info, both reviewers).** It was 120 a call. It is now
  per case: 80 for "osc2 wavetable" (envelope, M 69) and 45 for "mixed" (feed-B only, M 37), about the measured value
  + 15 %.
- **Accepted, fixed: no end-to-end check that a project's push tags a WT oscillator (info).** New
  `test_a_projects_push_tags_its_wavetable_oscillators`: the load's push (Type first, template order) of two Wavetable
  oscillators with FM values, over Analog oscillators and over fresh Wavetable ones. Both are tagged, the settings are
  stored, the D2 leaves are dropped and OSC1 feeds B. Red on mutation 21 and on the reviewer's B (D2 leave). The XML and
  stock-1.6.8 rows of 7.4 (BE4.43 / stock opening a BE4.44 project with WT FM values) stay with W3/W4, when the values
  play: W1 changes no keys, tokens or XML code.
- **Accepted, documented: feed-B is set by wt_feed, not by wt_init (info).** This differs from 3.2's "init writes feed-B
  from OSC2's current FM 1>2". wt_init writes wflags = 0. wt_feed recomputes the bit on every Type and FM 1>2 message from
  either oscillator, and tags OSC1 itself when the bit is wanted. So "OSC1 untagged implies the bit is not wanted" holds:
  every input to it (both Types, OSC2's FM 1>2, OSC2's tag, which cannot drop while fm12 != 0) passes through wt_feed.
  Mutations A and H (reviewer) and 14 are red.
- **Needs Jeff: D2 on an Analog OSC2 next to a Wavetable OSC1 is deferred to W4 (medium, both reviewers).** The reason
  stands (switching it now would make a control that plays silent on W1-W3 images). It is now recorded under D2 in
  section 12 and in W4's row in section 8, with a W4 test row for BE4.43 projects that hold a hidden FM 1>2 there.
- **Accepted as transitional, documented: a hidden FM 1>2 tags OSC1 (low).** A BE4.43 project with a Wavetable OSC1 and an
  Analog OSC2 that holds a non-zero FM 1>2 (hidden by the layout until W4) tags OSC1 for feed-B and pays tier 2 (1.0-1.6 % of
  its render, within the 1.02 gate) for no sound until W4. Gating feed-B on W4 being built was not taken: it would add a
  build switch to undo at W4, for a cost already inside the gate.
- **Accepted: skip counts (info).** Sprint suite lists now give skips. inst_xml (5) and handles (16) are fully skipped on
  this build (as before W1; they are gated on HAVE/BUILT) and are not counted as coverage.

**Suites (M, ~/bento/out/be444w1r, from the worktree's mods/m1).** These are the counts as run (skips in brackets):
diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20), notemod 16,
notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10, native_props 22, **analog 167** (162 + 5 new),
synth_ui 64 (2), note0 32: all OK. inst_xml 5 (5) and handles 16 (16) are fully skipped, so they give no coverage. test_drums
33 (1) and test_drum_ui 46 (1) pass on the drums trial build.

**Sizes (M):** unchanged from the W1 result: DSP 24,548 B, 16,156 B free of 40,704. Image 0x189458, 16,092 B to PROVEN_MAX.
M4 24,424 B, synth UI 15,858 B. Drums trial re-verified: the trial3 merge (f2effcd + main's committed 3597e8e, not
committed) rebuilt with BE_ENABLE=drums gives DSP 30,612 B, **10,092 B free**, and image 0x18af20.
**CPU (M):** unchanged. P3Identity 50 cases: worst 1.0184 (mod dev=False, 111,922 / 109,899), under the stop line that is
now tested; total 1.0094. Tier 2's own cost is 69 a call with an envelope and 37 without. An untagged oscillator-block costs
+27 over stock; a tagged one at gate 0 costs +64..+107, worst 1.0158 x stock's render.

## W2 result (2026-11-17, branch be444)

The reader, its golden and its tests. Build ~/bento/out/be444w2 (default, 1.6.8, BE_WT_MIP unset = 1.75).

**Mip trace first (risk 13: confirmed, and corrected in the reader).**
- S: stock builds the levels on the M4 (WAV load, 0x08112544..0x081126DE): each of levels 1..6 from the one before, as ONE
  stream of all 256 frames, through an 11-tap decimator (0x08113DF0, taps at 0x08113EF8, the same STOCK_FIR as the M7's 2x
  decimator) reset to 12 zero samples per level (0x08113F10). Output m is centred on the previous level's sample 2m - 7.
- M: the decimator emulated on the stock image equals p3_golden.wt_build_levels bit for bit
  (WtFmW2.test_the_mip_builder_is_stocks).
- Consequences: level k lags level 0 by **7 (2^k - 1) / 2048 of a cycle** (level 1: 0.0034, level 6: 0.215; M on a sine
  table to 1e-9). The reader reads a level other than the note's at the difference of the two skews (WT_MIP_SKEW, exact in
  Q32: 7 (2^k - 1) << 21). Also from the trace: each level's gain is 0.951 (-0.44 dB a level: a crossfade 2 levels up is
  -0.9 dB, stock's own per-note behaviour); frame 0 of every level >= 1 starts with the zero padding, and every frame's
  first samples carry the previous frame's tail (stock's data, played as is).
- Factory WAVs are not on this machine (names in the image, files on the device's SD). The "factory" rows use tables built
  by stock's builder (the M-verified decimator) from synthetic WAV frames; the per-level skew is a property of the builder,
  not of the content.

**Built (S).** dsp/wt_reader.cpp (-O2): rd_q (wt_rd), rd_qx (wt_rdf, the frame crossfade, one index for both frames);
dsp/wt_mipx.cpp (-Os): rd_qm (wt_rdx, two levels). analog.cpp: wt_play (stock's ladder on the pending flag, Q32 phase in at
the note's len, the mip choice, one change a block, phase / frame / mipcur out), wt_mip_for, wt_body (stock's render body
in stock's order with the reader for its kernel; fm_setup for the line; D9 (b): silent freezes unless feeding B), and
wt_fm_osc's gate (an amount, ramp memory, a mip held). Loops read at the phase, then advance (stock's resampler order, so the
first reader sample is stock's continuation; the prototype advanced first). build.py: BE_WT_MIP (1.0 .. 4.0, default
WT_MIP_DEFAULT = 1.75) -> -DWT_MIP_THR, recorded as the manifest's wt_mip. wt_kmodel.py: the kernel model as a fixture.
p3_golden: wt_build_levels, wt_ladder, wt_peak, wt_mip_choice, wt_read_block (float64 phase), wt_fm_run,
wt_spectrum_rows; self-tests (WtRead, 6) with 7 golden mutants.

**Tests (M), test_analog WtFmW2 (9).**
- 7.1, device vs golden: plain blocks len 32 7.9e-7 (bound 2e-6), len 256 1.9e-8 (2e-7), len 2048 5.4e-8 (2e-7); named
  bounds set here: through zero 1.3e-7, mip change 4.9e-8, frame change 4.1e-8 (each bound 4e-7). Phase within 1e-7 of a
  cycle; frame, mipcur, off / D / len / flag as the golden's.
- 7.2: every stock block after a handback equals wt_kmodel's continuation bit for bit (samples and +0xC0..+0xD8); the
  fixture itself re-checked against stock's kernel (3 rates, 10 blocks, frames fixed and moving).
- 7.4 continuity (the step at a switch: |second difference| less its neighbours', bound 0.02; a plain |diff| is the
  signal's own slope at note 72, 0.034): synthetic aligned sine <= 0.0084 (the reader skews levels that table does not
  have), builder saw <= 0.0017, builder 8-harmonic set <= 0.0012; notes 48 / 72, same mip and a 2-level excursion through
  the mip-home block.
- Pending flag, frac 1.0, hysteresis (parked, and +-1 % around the threshold), one change a block, level 0 frozen /
  running while feeding B, D5's device choice = the golden's over Sub FM 0..1000 at notes 48 / 60 / 72.
- Mutations (M, private copies, each built and WtFmW2 run): 3, 4, 5, 6, 7, 8, 11, 12, 13, 18, 23, 24 all red. (7's first
  form, HOME written after the home block regardless, was equivalent; the form used hands back straight from the last FM
  block when its line ends at 0.)
- Reviewed W1 changes: an amount now opens the reader, so WtFmW1's "amounts" tier-2 rows became "feed-B" (OSC1 feeding B,
  gate 0, stock's bit for bit; OSC2's samples and kernel left out, reader=); the stock-rewrites-every-run control uses the
  envelope set; P3Identity's tier-2 bounds: "osc2 wavetable" 170 a call (M 148: no WAV in that rig, the body stops at the
  table as stock's render does), "mixed" 55 (M 48: the gate's loads).

**D5 (as decided: one constant, leaning bright). Proposed default: 1.75** (cleaner alternate 1.5, brighter 2.0).
Golden rows (stock-built saw table, sine Sub an octave down, steady index; level / centroid in carrier harmonics / energy
off the FM grid in dB; "note level" = the reader pinned at stock's level):

| note ~, Sub FM | note level | 1.25 | 1.5 | 1.75 | 2.0 |
|---|---|---|---|---|---|
| 60, 250 | L3 2.44 -38.9 | L4 2.02 -45.1 | L4 2.02 -45.1 | **L3 2.44 -38.9** | L3 2.44 -38.9 |
| 60, 500 | L3 3.86 -33.2 | L5 2.73 -45.7 | L4 3.35 -36.7 | **L4 3.35 -36.7** | L4 3.35 -36.7 |
| 60, 1000 | L3 9.53 -23.8 | L6 7.46 -38.2 | L6 7.46 -38.2 | **L5 8.16 -30.7** | L5 8.16 -30.7 |
| 72, 250 | L4 2.10 -35.1 | L5 1.70 -43.8 | L4 2.10 -35.1 | **L4 2.10 -35.1** | L4 2.10 -35.1 |
| 72, 500 | L4 3.38 -29.0 | L6 3.06 -42.1 | L5 2.92 -33.5 | **L5 2.92 -33.5** | L5 2.92 -33.5 |
| 72, 1000 | L4 8.23 -20.1 | L6 7.56 -28.0 | L6 7.56 -28.0 | **L6 7.56 -28.0** | L6 7.56 -28.0 |
| 96, any | L6 (pinned) | same | same | same | same |

Amount at which the level first steps, at the true note pitches (rate / D = 0.698 at each note's level): 1.25: 215;
1.5: 275; 1.75: **330**; 2.0: 380 (notes 60..84; note 48: 235 / 300 / 360 / 415). So at 1.75 the plan's "light FM halves
the harmonics" case (amount about 210) no longer happens: the note's level holds to about a third of the knob, and the heavy
end aliases about 7 dB more than stock's rule (-30.7 vs -38.2 dB at note 60, full Sub FM). The thresholds act in whole
levels, so 1.5 / 1.75 / 2.0 differ only in where the steps land; the alternates a step either side are 1.5 and 2.0.

**Sizes (M).** Default: DSP 26,932 B, **13,772 B free** of 40,704 (W1 24,548: +2,384). Reader parts 1,724 B: rd_q 256,
rd_qx 316, rd_qm 380, wt_play 728, wt_mip_for 44 (**the plan's 1.5 KB exit is missed by 188 B**); wt_body 604, wt_fm_osc
+46. Image 0x189458 (unchanged, 16,092 B to PROVEN_MAX). M4 24,424 B, synth UI 15,858 B (no M4 change). Drums trial (LF
clone of b290cb6 + main's committed 3597e8e merged, not committed, test_keys / test_native_props taken from be444,
BE_ENABLE=drums): DSP 32,996 B, **7,708 B free**, image 0x18af20 (9,236 B to PROVEN_MAX); test_drums and test_drum_ui OK
(1 skipped each).

**CPU (M).** P3Identity 50 cases: worst 1.0184 (unchanged), total 1.0092. Untagged WT oscillator-block +27 over stock;
tier 2 +75..+118, worst 1.0174 x stock's render (W1 1.0158: the gate's loads). Reader, a sample: rd_q 34.1, rd_qx 45.2,
rd_qm 55.2; a whole wt_play block (256, note 60): 9,626 / 12,453 / 15,066. The plan's 29.1 / 39.1 were one-route prototypes.

**Plan items found wrong or changed in W2.**
1. Risk 13 was real: stock's levels are skewed (above); a per-level phase correction is in the reader and the golden.
2. Stock's levels lose 0.44 dB each and frame 0 carries zero padding (not in the plan).
3. The reader prototype read after advancing; stock reads first. Built stock's order.
4. One shared rd_qx / rd_qm loop spills registers (60 a sample at -O2): rd_qx is its own loop (45.2), rd_qm the general one
   at -Os (55.2, rare). The B route costs every loop about 3 a sample (rd_q 34.1, not 29).
5. Reader parts 1,724 B, not <= 1.5 KB; wt_play (728) is about twice the plan's 250-400 for mip select / ladder / handback /
   HOME.
6. The mip choice's max |A| / max |B| is a scan of every 4th frame in wt_play (not fused into B's writer); the golden states
   the same stride.
7. 7.4's "|step| <= 0.02" as a plain first difference is the signal's own slope at note 72; measured as the second
   difference's excess over its neighbours instead.
8. Factory tables are not available offline; 7.4's "factory" rows use stock's builder on synthetic frames.
9. Pulled forward from W3: the body calls fm_setup, so Sub FM on a Wavetable OSC1 plays from buffer A at W2 (not yet tested
   at the voice level with a sounding sub: Rig's sub is silent; W3). OSC2's FM 1>2 next to an Analog OSC1 opens the reader
   at index 0 until W3 feeds B to a Wavetable carrier (fmx= missed): a transitional cost, inaudible (in the steady blocks
   measured, the reader at index 0 matched stock's samples bit for bit).
10. D5's thresholds act in whole levels; the "1.05" hysteresis is WT_MIP_HYST = 0.84 x the threshold.

### W2 as built (after the W2 review, 2026-11-17)

Two reviewers rebuilt b290cb6 / 16f273a byte for byte, ran every suite, re-ran the plan's mutations plus about 20 of their
own, and probed the reader on the device harness. They found two real defects (a starved mip, a blind continuity test)
and several untested paths. Fixes are in the reader, the body, the golden, the tests, build.py and this plan. Build
~/bento/out/be444w2f (default, 1.6.8, wt_mip 1.75). Findings and what was done:

1. **Accepted, fixed: the mip starved while Position moved every block (high, both reviewers; reproduced).** The
   one-change rule let a frame change win every block and reset the wanted mip, so with a Position LFO or a turning knob
   (1) heavy FM never left the note's level (it aliased at the "note level" row: -23 dB at note 60, full Sub FM), and
   (2) after FM ended on a coarse level, the mip-home block never ran: the oscillator stayed duller and quieter, never
   handed back, and paid the reader's cost until Position stopped. The golden had the same rule, so no device-vs-golden
   test could see it.
   - Rule (3.4, as built): a frame change defers a wanted mip change and sets **WT_MIP_WAIT** (wflags bit 1); the next
     block changes the mip and defers the frame. Each change waits one block at most, alternating, and the frame playing
     stays at most one block behind Position. The bit is set only while the reader holds a mip, so tier 2 never sees it.
   - Road not taken: "a mip change always wins". It needs no state, but it starves the frame during an FM attack whose
     wanted level climbs every block. The plan promised a one-block deferral, so the alternating rule keeps that promise.
   - Golden: wt_read_block mirrors it (st["wait"]), with a golden mutant "no_wait" (the first W2 rule).
   - Tests: `test_position_every_block` (device = golden every block, Position +1 every block, Sub FM attack then cut and
     attack then a six-block decay: the mip leaves the note's level under FM, is HOME within two blocks of the line's
     end, is never HOME while on another level, and the frame is never more than a block behind; the golden's no_wait
     fails the same assertions, as a control). Golden self-test `test_one_change_waits_one_block`.
2. **Accepted, fixed: the 7.4 continuity metric could not see a step (high, both reviewers; reproduced).** It
   subtracted the neighbouring second differences, and a jump of s is in the neighbour too, so it cancelled itself.
   - New metric (`WtFmW2.step_at_switches`): the line through b - 2, b - 1 predicts sample b; the step is the larger
     prediction error at b and b + 1 less the signal's own largest one over b - 8 .. b - 1 and b + 3 .. b + 9, samples the
     jump does not reach. Bound **WT_STEP_MAX = 0.01**, tightened from the plan's 0.02: measured worst 0.0040, and a
     skipped mip-home block steps 0.0159 / 0.0028 / 0.0422 / 0.0172 (saw n48 / n72, smooth n48 / n72), so 0.01 turns
     three of the four red where 0.02 caught one.
   - Re-measured rows (replace "W2 result"'s 7.4 numbers): synthetic aligned sine <= 0.0040 (note 72 mip excursion),
     builder saw <= 0.0000, builder 8-harmonic set <= 0.0001. Negative means the switch's error is below the signal's own
     curvature nearby. The builder saw at note 72 has stock's own largest second difference 0.44 (its edge), so on a saw a
     switch that lands near an edge is masked; the smooth tables carry the sensitivity.
   - Mutants that only this test can see, because check() and 7.2 follow the C and the golden alike: the handback phase
     moved by 1e-3 and by 1e-2 of a cycle in both the C and the golden. Both are red, on test_handback_and_continuity
     alone (1e-3: a step of 0.085 on the builder saw at note 48).
3. **Accepted, fixed: the B route (J b) and its peak were untested (medium).** wt_line gave b = a. Now b is its own sine
   (another ratio, phase and peak) whenever J != 0. New 7.1 rows: "B alone (mip change)" (I 0: the level moves on J max|B|
   alone; a control shows B's peak taken from A picks another level) and "two routes, B's term the larger" (a control
   shows B read from A is another block). Mutations "B term read from A", "B's peak scanned from A" and "J dropped from
   the peak" are red.
4. **Accepted, fixed: a scan of A follows a slow sub (medium; reproduced in the golden).** At notes 24..36 the sub's
   period spans blocks, so the block's max |A| moved with the sub's phase and the level flipped on about a third of the
   blocks (rd_qm blocks, and a 16-33 Hz timbre flutter). Now the body passes A's known peak (WtBlk.pa = inc_sub x the
   sub wave's peak: 1 for Rev Saw, Triangle, Sine, Noise; the Square's DC-free 1 + |1 - 2 pw| from the width its core
   plays), and wt_play uses it instead of the scan. B is still scanned (W3: its own low-note toggling, if any, is measured
   there with FM 1>2 into a WT carrier). Golden wt_peak takes pa. Tests: `test_low_notes_hold_their_level` (notes 24 / 36,
   Sub FM 400 / 800, 64 blocks: at most one level change), `test_a_peak_known_through_the_body` (through stock's root with
   a sounding Sine and Square sub: the level is the golden's for the known peak; a control shows the Square's factor
   moves a level).
   - Known limit: during a PW change the Square's factor uses the width its core latched (one wrap late), not the new
     one. Small and brief.
5. **Accepted, fixed: W2 behaviours that survived every test (medium).**
   - (a) Mutation 7 in its natural form (mipcur dropped from wt_fm_osc's gate): new `test_mip_home_through_the_render`
     (stock's root, a sounding sub, Sub FM 1000 then 0: coarse through the ramp-out block, HOME only after the mip-home
     block). Red.
   - (b) HOME set in a home block that changed the frame: red on test_position_every_block.
   - (c) The band's value: `test_hysteresis_band` holds kn + 2 with the peak at 0.90 x thr x 2^(kn + 1) and steps to
     kn + 1 at 0.78. 0.95 and 0.70 are both red.
   - (d) J dropped from the peak: red (item 3).
   - (e) The clamp: a 7.1 row "the clamp" (note 115, I 16 at the carrier's rate: steps to 0.6 a sample, both signs).
     Named bound TOL_RD_CLAMP 6e-6 (M 2.7e-6, len 32), phase within 5e-7 of a cycle (M 2.2e-7: f32 steps near 0.45 over
     256 samples). Clamp removed: red.
   - (f) Mutation 12 in a reassociated, unfused form: **still green, recorded, not pinned at W2.** 7.1's float64
     tolerances cannot pin the line's last bits; the implementer's drifting-accumulator form is red. Pinning the bit law
     needs an f32 model of an_render_fm's line; W3 reuses that law for WT carriers and can add it there if wanted.
6. **Partly accepted (medium): Sub FM on a WT OSC1 sounds at W2 without D3's mixed-sync rule.**
   - Recorded, not fixed here: W2 images play Sub FM on a Wavetable OSC1 while an Analog OSC2 is synced to it, and the
     slave follows a phase WT1 no longer has. D3 is still Jeff's open decision. Its rule (recommended: Sub FM plays as 0
     while synced) moves from W4 to **W3**, with mutation 17, before any W3 device image (section 8). **No W2 image goes
     to the device.**
   - Fixed: OSC2's transitional cases. A WT OSC2's amount is FM 1>2 per D2 (`twelve = which == 1` is right for a
     Wavetable OSC2 next to either OSC1 type), but until W3 / W4 there is no B for it, and a hidden Sub FM (kept from an
     Analog OSC2) opened the gate too. Both opened the reader at index 0 every block. Now wt_body hands a block with no
     line, no mip held and no feed-B to stock's render (tier 2): bit for bit stock's, at about +460 a voice-block over
     an untagged voice instead of +4,900 (M, stock's root, FM 1>2 400 on a WT OSC2). `test_no_line_plays_stocks_render`
     (notes 24 and 60: voice output and OSC2's kernel state the same bytes as with no amount). Note 24 is needed: at
     notes 47 / 60 the index-0 reader happens to equal stock's on every block, so a note-60-only form of the test stayed
     green on the fallback's removal (caught in this round's mutation run, then fixed). W4 must keep feed-B's index-0
     blocks on the reader (the condition already excludes feed-B).
7. **Accepted, figures here; the decisions are Jeff's (medium): budget and CPU against the plan.**
   - WT FM DSP so far: 24,548 - 23,796 = 752 (W1) + 2,560 (W2 as built) = **3,312 B**, against section 4/5's 2.4-3.8 KB
     for all of WT FM, with W3 (A / B into carriers) and W4 (B from a WT OSC1: about 0.25-0.35 KB) still to come. Part
     of W4 (wt_body) is already in.
   - Default DSP 27,108 B: **492 B under the D6 line "analog + FM <= 27,600 B"** (D6 is not decided; build.py does not
     enforce it).
   - Drums trial 7,532 B free against about 6-7.6 KB of pending drum demand plus the 1 KB margin.
   - Reader parts 1,796 B against the 1.5 KB exit (missed by 260 B of 1,536; the review's fixes added 72). Measured lever:
     wt_play and wt_body at minsize save only 64 B of DSP (M, scratch), not worth their control-rate cost.
   - CPU: a reader block is 9,636 at note 60 (section 6 planned 7,451, +29 %): the loops are 34.1 a sample, not 29, and
     wt_play adds about 900 a block of its own. A mip-change block is 15,077 (planned "+2.5 to 3k" over a plain block;
     it is +5.4k). Section 6's FM'd WT rows and the S3 voice gate (28,929) are re-estimated at W3's start from these
     figures, before W3's cost gates are written (section 8 W3 row).
8. **Accepted, fixed: D5's constant had stale copies and the test read the environment (low, both reviewers).**
   - analog.cpp has no fallback: `#error` unless build.py passes WT_MIP_THR (build.py also passes it to its
     preprocess-only source scan). The tests read the manifest only (`MAN["wt_mip"]`, no default); p3_golden's
     wt_read_block / wt_fm_run take thr with no default (its self-tests pass a fixture 1.75).
   - test_d5_threshold checks the manifest's value is in range and labelled, not equal to the test-time BE_WT_MIP, so an
     alternate image passes as built.
   - build.py: a non-default threshold suffixes the version (`-mip2.0`, `-mip1.5`, after any BE_TAG). The boot label
     (6 characters) is unchanged unless BE_LABEL is set; the kit name and marker carry the suffix.
9. **Accepted, corrected (low): "the reader at index 0 matched stock's bit for bit" ("W2 result" item 9) was wrong.**
   The Q32 step truncates toward zero at 2^-(21 + k) of a sample, so the reader runs a hair flat at low levels: within
   2.2e-5 and drifting about 6e-4 samples per 20 blocks at level 0, bit-equal only at some rates (notes 61..110: 17-19 of
   20 blocks). The transitional cases no longer use it (item 6). Rounding to nearest: rejected for now. VCVT's
   fixed-point form only truncates, so rounding costs an instruction a sample, and the error is far below audibility.
10. **Accepted, fixed (low, both reviewers): D5's brightness and alias rows.** wt_spectrum_rows now uses a rectangular
    window on exact bins (the run is periodic), an on-grid centroid (alias no longer counts as brightness), and a new
    brightness row: on-grid energy above harmonic 16 (level 6's table limit, so it measures what a coarser level
    removes). Window after the last level change (24 blocks, the last 16, levels asserted steady). The ranking and the
    default are unchanged.

**D5 (decided: one constant, leaning bright). Default: 1.75** (cleaner alternate 1.5, brighter 2.0; build with
BE_WT_MIP, version suffixed). Golden rows, stock-built saw table, sine sub an octave down, A's known peak; each cell is
level / on-grid centroid (carrier harmonics) / on-grid energy above harmonic 16 (dB) / alias (dB); "note level" = the reader
pinned at stock's level:

| note ~, Sub FM | note level | 1.25 | 1.5 | 1.75 | 2.0 |
|---|---|---|---|---|---|
| 60, 250 | L3 2.42 -19.3 -38.2 | L4 2.01 -19.0 -44.4 | L4 2.01 -19.0 -44.4 | **L3 2.42 -19.3 -38.2** | L3 2.42 -19.3 -38.2 |
| 60, 500 | L3 3.81 -15.9 -32.6 | L5 2.72 -17.7 -44.9 | L4 3.33 -17.2 -35.8 | **L4 3.33 -17.2 -35.8** | L4 3.33 -17.2 -35.8 |
| 60, 1000 | L3 9.11 -9.3 -23.0 | L6 7.44 -11.8 -37.5 | L6 7.44 -11.8 -37.5 | **L5 8.09 -10.8 -29.8** | L5 8.09 -10.8 -29.8 |
| 72, 250 | L4 2.08 -18.6 -34.8 | L5 1.69 -20.1 -44.0 | L4 2.08 -18.6 -34.8 | **L4 2.08 -18.6 -34.8** | L4 2.08 -18.6 -34.8 |
| 72, 500 | L4 3.32 -16.2 -29.2 | L6 3.05 -18.3 -42.1 | L5 2.89 -17.1 -33.4 | **L5 2.89 -17.1 -33.4** | L5 2.89 -17.1 -33.4 |
| 72, 1000 | L4 7.75 -10.2 -20.1 | L6 7.47 -11.5 -28.1 | L6 7.47 -11.5 -28.1 | **L6 7.47 -11.5 -28.1** | L6 7.47 -11.5 -28.1 |
| 96, any | L6 (pinned) | same | same | same | same |

- With A's known peak, the first step no longer depends on the note: Sub FM 215 (1.25), 275 (1.5), **330 (1.75)**, 380
  (2.0) at notes 48, 60, 72 and 84. The note-48 figures in "W2 result" came from the scan.
- Read brightness from the HF row, not the centroid. At note 72 / Sub FM 500 the centroid ranks L6 above L5 (3.05 vs
  2.89), but L5 keeps more energy above harmonic 16 (-17.1 vs -18.3 dB). At note 60 / Sub FM 250, 1.25's L4 shows -19.0
  against L3's -19.3: a normalisation effect of the FM spread, within 0.3 dB, not a brighter level.
- Why 1.75:
  - Light FM up to about a third of the knob keeps the note's level, so the "light FM halves the harmonics" step at
    about 210 is gone.
  - Full Sub FM at note 60 plays one level brighter than stock's rule (-10.8 vs -11.8 dB above harmonic 16) and aliases
    at -29.8 dB against -37.5 (about 8 dB more: the "character" D5 accepts).
  - Thresholds act in whole levels, so 1.5, 1.75 and 2.0 differ only in where the steps land. The alternates a step
    either side are 1.5 and 2.0.
- An alternate image builds with `BE_WT_MIP=2.0` (or 1.5). M: the 2.0 image is labelled v4.44-mip2.0, its manifest says
  wt_mip 2.0, its DSP is the same 27,108 B, and WtFmW2 passes on it with no BE_WT_MIP set at test time.

**Sizes (M).** Default: DSP **27,108 B**, 13,596 B free of 40,704 (W2 result 26,932: +176; wt_play 800 (+72), wt_body
708 (+104)). Reader parts 1,796 B (rd_q 256, rd_qx 316, rd_qm 380, wt_play 800, wt_mip_for 44); test bound 1,830.
wt_fm_osc 178. Image 0x189458, unchanged (16,092 B to PROVEN_MAX). M4 24,424 B, synth UI 15,858 B (no M4 change).
Drums trial (trialW2: the W2 trial clone with this fix's mods/m1 diff applied, the merge of main's committed 3597e8e still
uncommitted, BE_ENABLE=drums): DSP **33,172 B, 7,532 B free**, image 0x18af20 (9,236 B to PROVEN_MAX); test_drums 33 (1)
and test_drum_ui 46 (1) OK.

**CPU (M).** P3Identity 50 cases: worst 1.0184 (mod dev=False, 111,922 / 109,899), total 1.0092: unchanged, under the
1.95 % stop line. Untagged WT oscillator-block +27 over stock; tier 2 +75..+118, worst 1.0174 x stock's render
(unchanged). Reader: rd_q 34.1, rd_qx 45.2, rd_qm 55.2 a sample (unchanged); whole wt_play block at note 60: 9,636 /
12,468 / 15,077 (+10 to 15: the wait bit and pa). Transitional amount (FM 1>2 on a WT OSC2 with no B): +463 a voice-block
over an untagged voice (was +4,900).

**Suites (M, ~/bento/out/be444w2f, from the worktree's mods/m1; skips in brackets):** diag 92 (6), polyat 51 (2),
conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20), notemod 16, notemod_route 8, notemod_real
14, slice 20, s3_m7 25, step_xml 11, keys 10, native_props 22, **analog 182** (176 + 6 new), synth_ui 64 (2), note0 32,
p3_golden self-test 51 (+1): all OK (test_analog re-run after the last test edits on a fresh build, be444w2r,
byte-identical to be444w2f). inst_xml 5 (5) and handles 16 (16) are fully skipped (no coverage).

**Mutations (M, private copies of this tree, each built, WtFmW2 run; scratch W2fix/mut3.py, mut3.log).** Baseline
green.
- The plan's mutations, red: 3, 4, 5, 6, 7 (both forms: the gate's mip term dropped, and HOME straight from the last FM
  block), 8, 11, 12 (the drifting-accumulator form), 13, 18, 23, 24.
- The review's mutations, red: the first W2 rule (frame always wins); the wait bit never set; HOME in a home block that
  changed the frame; band 0.70 and 0.95; J dropped from the peak; B's peak scanned from A; B's term read from A; the
  clamp removed; A's known peak ignored (scanned); the Square's factor dropped; the tier-2 fallback removed (red once
  the test ran note 24, item 6); the handback phase moved by 1e-3 and 1e-2 of a cycle in the C and the golden.
- Green, recorded: mutation 12 in a reassociated, unfused form (item 5 (f)).

**Needs Jeff.**
- **D5's default: 1.75**, from the rows above (cleaner 1.5 / brighter 2.0 as alternate W3 / W4 images).
- **D3** (open): its rule moves into W3 (before any device image), so the choice is needed before W3's device image.
- **Budget:** WT FM is at 3,312 B with W3 and W4 to come, against the plan's 2.4-3.8 KB; the default DSP is 492 B under
  D6's 27,600 B line (D6 itself undecided); the drums trial has 7,532 B free against 6-7.6 KB pending plus the 1 KB
  margin. Before W3: whether to plan a reserve item now (R2's analog minsize -596 B; merging rd_qx into rd_q about -0.2
  KB at a per-sample cost), or wait for W3's measurement as section 5 says.
- **Reader-parts exit (1.5 KB) missed by 260 B:** accept as measured (the B route in all three loops, two loops kept
  apart because the shared one spills), or ask for the merge above.
- **Continuity bound:** 7.4's 0.02 is tightened to 0.01 in the test (measured 0.0040). Say if you would rather keep the
  plan's number.

## Review log

Two verifier passes; each finding was checked, and the checks that could be cheaply re-run were (be442 build.py:741 guard,
bases/stock_168.py CTRL/M7 entries, main `git log 435b2f0..HEAD`, plan-p4-drums.md efficiency-pass lines).

**Pass 1 (budget and merge).**
1. *S6a guard no longer protects after the move* (medium). **Accepted, verified** (build.py:741 `hi <= S.CTRL_INPUT`).
   Guard becomes `hi <= S.M7_TRAMP` plus a CTRL disjointness assertion and two mutations (W0, mutation 25).
2. *"C-slice top already runs code" is false; use the 0x080DF000 page* (medium). **Accepted.** CTRL moves to
   0x080DF000/0x080DF800; C slice untouched; claim withdrawn. Added caution of my own: stock word 0x0808586C = 0x080DF04F
   (from the same verifier's scan) points into that page, so W0 decodes its uses first; C-slice top at 0x200 kept as 1b.
   R4's conflict with buy-back 1 removed.
3. *Fallback omits buy-backs 2 and 3* (medium). **Accepted in method, superseded in number.** Buy-backs 2 and 3 are now in
   the fallback. But with the efficiency-pass row (item 4) and the design growth from pass 2, the fallback worst case is
   -518 to -178 B, worse than before, not 0.9-1.2 KB. B slipping is made conditional on W3's measurement, as suggested.
4. *Efficiency pass missing from demand* (medium). **Accepted, verified** (plan-p4-drums.md:575, :587). Row added at
   300-1,000 B (I; range to agree with the P4 plan); drums cap raised from 11,000 to 11,500 B to hold the worst case
   (11,472 B); margin between caps 1,604 B.
5. *Prototype sum is 5,698, knee counted twice* (low). **Accepted** (arithmetic checked). Low bound 5,930; noted -O2
   prototypes are upper bounds at main's -Os.
6. *Main is 8 commits past the base, not 2; more overlapping files* (low). **Accepted, verified** (8 commits, 2 code).
   Conflict list expanded; M0's exit is now a full image and full suite after P4-2's step commits.
7. *M4 headroom quoted from the wrong build* (low). **Accepted.** Merged-build headroom recorded at M0; p42a's 10,156 B
   cited; M4 line added to the restatement.
8. *Info: figures re-measured and confirmed; an_feed minsize is -176* (info). **Applied** (R2 note); R2's -596 total
   unchanged.

**Pass 2 (design).**
1. *Note-start block breaks tier-2 identity via mipcur != noteD* (high). **Accepted.** HOME sentinel set by an_note; gate
   uses `mipcur != HOME`; three-note-start identity test; mutation 20.
2. *WT-only bytes stored in events[], which stock rewrites; save set figures inconsistent* (high). **Accepted.** WT state
   moved into a saved run; mipcur bounded on read; save set restated as four runs, about 32 B (I), tier-2 cost re-estimated
   at 40-55; many-wraps test; mutation 2 per run, 22.
3. *Level-0 freeze vs feed-B identity contradict* (medium). **Accepted.** New D9; recommended (b) with scoped identity and a
   named divergence test; device item 11.
4. *Mip-crossfade loop uncounted; no mip+frame path; no hysteresis* (medium). **Accepted, with a narrower fix:** rather than
   prototype the two-mip x two-frame loop, a block changes mip or frame, not both (mip deferred one block). rd_qm added to
   size (220-300 B, I) and CPU tables, prototyped at W2; hysteresis added; two new cost cases; mutations 23, 24.
5. *Mip phase alignment untraced* (medium). **Accepted.** W2 starts with the trace; risk 13; continuity rows on factory
   tables.
6. *Untagged->tagged reads table bytes as settings* (medium). **Accepted.** an_prop writes the full default state before the
   first value; test and mutation 21.
7. *Light FM already drops a mip* (low). **Accepted** (arithmetic re-done: I >= about 1.6, amount about 210 at note 60).
   Stated in section 2, folded into D5, brightness rows and device item 3.

**Net effect.** The verdict stays GO with the window move, but the worst-case free space after everything drops from about
3.0 KB to about 1.6 KB (efficiency-pass row, corrected sums, and about +0.3 KB of design for HOME, state-run, init and
rd_qm). That makes W3's measured-size gate the real decision point, and makes the 1/1b device check more important.

## Decisions after W2 (Jeff, 2026-11-17)

- **D3: Sub FM mutes while synced.** A Wavetable OSC1's Sub FM plays as 0 while OSC2 is synced to it; the value is kept.
- **D5 alternates:** default 1.75; cleaner alternate 1.5; the brighter alternate is **"never go darker"** (the reader stays
  at stock's level for the note under any FM, the "note level" column), not 2.0, which differed from 1.75 only in where
  the first step lands. Build-selectable like the threshold (e.g. BE_WT_MIP=off), version suffixed.
- **Budget: optimise for the synth release; the drums budget is set aside until P4 resumes.** D6's drum cap is not
  adopted now; the synth must still fit the window with the 1 KB margin in the default build. When P4 resumes, re-measure
  and trim (rd_qx into rd_q, R2) if drums do not fit. Risk accepted: trims may be forced later.
- Defaults taken without asking: the reader-parts exit (1.5 KB) missed by 260 B is accepted; the continuity bound stays
  at W2's tighter 0.01.

## W3 result (2026-11-17, branch be444)

A and B into Wavetable carriers, the envelope on them, ramps, E3 / skip, D3, BE_WT_MIP=off, the black box, costs, aliasing and
brightness rows. Commit 5c81a90; builds ~/bento/out/be444w3b (default, 1.6.8, wt_mip 1.75) and ~/bento/out/be444w3b-off
(BE_WT_MIP=off, v4.44-mipoff). D3's rule is in, so a W3 image may go to the device (none built for it yet).

**CPU re-estimate at W3's start (before any W3 code; M on the W2 image be444w2r, scratch W3/cost0.py).** Per voice-block,
note 60, stock's builder saw (200 harmonics), a sine sub, I = 16; an_enter plus both oscillators' whole renders (stock's
render included where it plays): stock Wavetable voice 14,119; WT1 Sub FM 1000 25,181 (its entry block 30,944: the rd_qm
crossfade); the same with Position moving every block 28,245; Analog full FM voice 29,104 on this rig (BE4.43's 28,929 on
FmEnv's). From these (I on M):
- An FM'd Wavetable oscillator costs about +11.1k over stock's render including the sub copy (about 4.4k, once a
  voice-block), so about +6.7k for the reader and body alone: **about +95 % of stock's render, not section 6's +50 %** (the
  loops are 34.1 a sample, not 29, and wt_play adds about 0.9k a block).
- Position moving: +3.1k a voice-block (section 6: +2.6k).
- Voices: WT1 Sub FM about 25.2k (section 6: about 22k); FM 1>2 from an Analog OSC1 into a WT OSC2 (W3's new path) about
  24.2k; the same with the Analog OSC1's own Sub FM (the mixed full FM voice) about 31.2k; W4's full Wavetable voice (WT1
  Sub FM + WT1 -> WT2 FM 1>2) about 25.2 + 6.7 + 0.8 (B) = **32.7k, about 13 % over S3's 28,929**.
- Gates set from this (WtFmW3.W3_GATES): WT1 Sub FM <= 26,000 (and <= S3), A1 -> WT2 FM 1>2 <= 25,500, A1 Sub FM -> WT2
  FM 1>2 <= 32,000; Position every block and the two new cases: every block <= 35,309 (the heaviest existing voice).

**Built (S).**
- feeds / an_osc: an Analog OSC1 feeds B to a tagged Wavetable OSC2 with FM 1>2 or its ramp memory (an_osc's OSC1 gate reads
  OSC2's fm12 / m12 when its magic has the tag's high half; feeds checks the pair). The Wavetable OSC2 plays B through
  wt_body's fm_setup line; W2's transitional fallback is taken only when B is missing (fmx=).
- wt_body: D3 (below); each reader block counted: SaStats renders and cycles (finish() also flushes on r->oscs, so an
  all-Wavetable voice reaches the stats line), SaFmStats.wt (+16) and wt_mip (+20, rd_qm blocks: wt_play returns its loop).
  M4 an_print: ` wt=<reader blocks>/<mip changes>` after fmx=. fm= already counted Wavetable FM (fm_setup).
- D3 (Jeff, 2026-11-17): while an Analog OSC2 is synced to the Wavetable OSC1, OSC1's Sub FM plays as 0 (a local AnProps copy
  with subfm 0) and msub is dropped at once. Road not taken: ramping out over one block, which would leave WT1's phase off
  the slave's constant-rate copy until the next note. Value kept. With no mip held the block is stock's render (tier 2),
  so mixed sync plays as BE4.43.
- BE_WT_MIP=off: build.py passes WT_MIP_OFF (and stock's 1.25 for the unused threshold), version -mipoff, manifest wt_mip
  "off". wt_play skips the peak and the choice: the note's level, mipcur always HOME, so the ramp-out block (its line
  ending at index 0) hands back straight to stock's render; no rd_qm and no mip-home block. p3_golden.wt_read_block takes
  thr "off".
- The envelope, ramps, E3 and the skip needed no new code: wt_fm_osc steps fm_env_step (W1) and wt_body calls fm_setup (W2),
  the code Analog runs. W3 tests them on Wavetable carriers.

**Tests (M), test_analog WtFmW3 (11).**
- `wt_capture` taps every wt_play call (state, WtBlk, FmIn, samples, loop kind); `wt_replay` checks each against
  p3_golden.wt_read_block from its captured state. At the voice the phase bound is 1e-7 + 2^-23 sum |di| (M: 1.4e-7 at index
  16; 7.1's 1e-7 was set on W2's small synthetic lines), and the samples are checked against the band the golden spans when
  its start phase moves by the measured phase difference (a 1e-7-cycle difference moves a builder-saw sample at an edge by
  several 1e-6). Worst: 0.07 to 0.12 of the bound.
- A into a WT OSC1 (sine and builder saw, Position moving, Sub FM 0 -> 250 -> 1000 -> 500 -> 0 and on again): every block
  the golden's, every line the index law's (SubFm.lines), pa = inc_sub, then the mip-home block (a frame block first when
  Position moves), then stock's render; fm= counted.
- B from an Analog OSC1 into a WT OSC2 (Sub FM 0 / 600, and OSC1 silent: E3 (a)): B is check_feeds' golden, OSC2 reads
  exactly that B on FM 1>2's line, every block the golden's, fmx= 0, feeding stops after the ramp out.
- Envelope (the exit): FmEnv's four OSC1 cases on a WT OSC1, and FM 1>2 Atk 3 / Dec 4 on a WT OSC2: every line is
  fm_env_lines' f32 line **bit for bit and equal to the Analog carrier's line** in the same case. It keeps time through no
  amount and through Level 0 (frozen as stock's, msub dropped, from 0 when heard): the Analog lines again.
- Ramps 0 -> 4 -> 0 over 3 blocks at notes 48 / 72: ramp in, ramp out ending at 0, the mip-home block, then stock's kernel
  continues bit for bit from the handed-back state (wt_kmodel).
- Skip: 512-frame blocks with Sub FM 700 play stock 1.6.8's render bit for bit; fmx= counts each; no reader.
- D3: Sub FM 1000 vs 0 under mixed sync: the voice's output, OSC1's kernel and the slave's phase copy byte for byte, no reader
  call, value kept; sync off at block 6 plays FM (from 0), on again at block 10 stops it (at most the home block).
- BE_WT_MIP=off: build.py's parse, suffix and defines, and the golden, on every build; on the off image the note's level at
  notes 36 / 60 / 84 under index 16, nothing held, every block the golden's, stock's continuation after the handback.
- Black box: fm=, wt= and wt=.../mip equal what the tap saw; blocks=, renders= and peak_cyc= move for an all-Wavetable voice.
- Costs (below), and the alias / brightness rows (device = golden within 1e-5).
- Reviewed changes: Fm12.test_mixed_voices and both "amounts" tests ("osc2 wavetable": OSC1 now feeds the WT OSC2's FM 1>2;
  that rig has no WAV, so nothing reads it). P3Identity.test_q1_plays_no_amount drops "osc2 wavetable" (+13 % there: the FM
  parts loop and an_feed; it plays now), as Q6 moved the both-Analog cases out. test_synth_ui's black-box line (wt=). On an
  off image the W2 mip-rule tests skip (`wt_rule`).

**Mutations (M, private copies, scratch W3/mut.sh and mut2.sh; each built and its test run): all red.** 17 (D3 removed); D3
ramping out instead of dropping msub; feeds for an Analog OSC2 only; the OSC1 gate for an Analog OSC2 only; finish() on
voices only; wt= not counted; wt_mip also counting frame blocks (it survived the first form of the test, which had no frame
block; the test now moves Position under FM); the fm_env_step call dropped; msub kept while frozen; the skip played by the
reader; on an off build, the mip rule put back, and mipcur held under FM.

**Sizes (M).** Default: DSP 27,556 B, **13,148 B free** of 40,704 (W2 27,108: +448). BE_WT_MIP=off: DSP 27,108 B. Synth UI
15,886 B (+28, the wt= field), M4 code 24,424 B, M7 C slice 15,368 B. Image 0x189478, **16,060 B to PROVEN_MAX**. WT FM DSP so
far 3,760 B (W1 752 + W2 2,560 + W3 448) against section 4's planned 2.4-3.8 KB, with W4 to come. Drums trial, for
information only (an LF clone of 5c81a90 with main's committed 3597e8e merged, not committed, test_keys / test_native_props
from be444, BE_ENABLE=drums; scratch trialW3): DSP 33,612 B, **7,092 B free**, image 0x18af48 (9,196 B to PROVEN_MAX);
test_drums 33 (1 skipped) and test_drum_ui 46 (1 skipped) OK. No reserve taken (Jeff: the drums budget is set aside).

**CPU (M).** P3Identity, 48 cases: worst **1.0180** (mod dev=False, 111,873 / 109,899), total 1.0089, under the 1.95 % stop
line. Tier 2 +75..+118, worst 1.0174 x stock's render; untagged +27 (unchanged). Voices (median / max a voice-block, note 60,
I 16): stock WT 14,155; **WT1 Sub FM 25,283** (gate 26,000; S3 28,929); A1 -> WT2 FM 1>2 20,887 (gate 25,500); A1 Sub FM ->
WT2 FM 1>2 30,763 / 30,844 (gate 32,000; 6 % over S3); Analog full FM 28,936; WT1 Sub FM with Position every block 28,347 /
28,414; new case 1, Position LFO + FM Dec 12: 28,379 / 30,996; new case 2, an index parked on the 1.75 threshold (Sub FM
208, +-3 a block): 25,237 / 30,720 (the entry block), levels [3, 4, 4, ...]: one change, no toggling.

**Aliasing and brightness rows (M, device = golden).** Each cell: level, alias dB at 96 kHz / after stock's decimator, on-grid
energy above harmonic 16 dB.

| note ~, Sub FM | this build (1.75) | note level (off) | 1.25 |
|---|---|---|---|
| 60, 250 | L3 -38.2 / -42.3, -19.3 | L3 -38.2 / -42.3, -19.3 | L4 -44.4 / -49.5, -19.0 |
| 60, 500 | L4 -35.8 / -42.3, -17.2 | L3 -32.6 / -35.7, -15.9 | L5 -44.9 / -45.7, -17.7 |
| 60, 1000 | L5 -29.8 / -35.0, -10.8 | L3 -23.0 / -26.4, -9.3 | L6 -37.5 / -40.5, -11.8 |
| 72, 500 | L5 -33.4 / -40.0, -17.1 | L4 -29.2 / -34.6, -16.2 | L6 -42.1 / -43.5, -18.3 |
| 72, 1000 | L6 -28.1 / -33.2, -11.5 | L4 -20.1 / -25.2, -10.2 | L6 -28.1 / -33.2, -11.5 |
| 96, 500 | L6 -21.1 / -25.9, -20.3 | same | same |
| 96, 1000 | L6 -11.7 / -16.6, -15.2 | same | same |

The off image ("never go darker") keeps about 1.3 to 1.5 dB more energy above harmonic 16 at full Sub FM and aliases about 7
to 8 dB more than the default. At note 96 all three play level 6 (pinned).

**Suites (M, ~/bento/out/be444w3b, from the worktree's mods/m1; skips in brackets):** diag 92 (6), polyat 51 (2), conditions
18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20), notemod 16, notemod_route 8, notemod_real 14, slice 20,
s3_m7 25, step_xml 11, keys 10, native_props 22, **analog 193** (182 + 11), synth_ui 64 (2), note0 32, p3_golden self-test 51:
all OK. inst_xml 5 (5) and handles 16 (16) are fully skipped. On be444w3b-off: analog 193 with 8 skipped (the W2 mip-rule
tests); its two first-run failures (test_mip_off's plain bound at index 16, WtFmW2.test_sizes_and_cost with no rd_qm) were
fixed in the tests and re-run on it: OK.

**Plan items found wrong or changed in W3.**
1. Section 6's FM'd WT oscillator at "+50 % of stock's render" is about +95 % (reader and body +6.7k on a 7.1k render); the
   WT voice rows are higher (WT1 Sub FM 25.2k, not 22k).
2. **S3 will be missed at W4:** the full Wavetable FM voice is re-estimated at about 32.7k against 28,929 (I, +13 %), and the
   mixed full FM voice built at W3 measures 30,763 (+6 %). Levers if wanted: the unrolled reader (section 6: about 24-25 a
   sample, about -2.4k a reader block, +150-300 B), then rd_qx merged into rd_q. **Needs Jeff:** accept the re-estimated
   gates, or take the unroll in W4.
3. 7.1's phase bound (1e-7 of a cycle) and plain sample bounds hold for W2's synthetic lines on smooth tables only. At the
   voice (index 16, a builder saw) the f32 step rounding and the table's edges need a bound scaled by sum |di| and a
   phase-aware sample check (wt_replay). The kernel-level W2 bounds are unchanged.
4. "Sub FM on WT OSC1 / OSC2 per D2": nothing to build for OSC2 (D2 leaves its Sub FM unplayed since W1).
5. The black box's "gate words" (S4) were read as the stats fields; no per-oscillator gate dump was added (the black box has
   no per-voice lines). wt= counts reader blocks only, not tier-2, no-WAV or frozen blocks.
6. An Analog OSC1 now feeds B to a Wavetable OSC2 that has FM 1>2 even when that OSC2 has no WAV (silent): the cost of B for
   nothing, as for a silent Analog OSC2. Not gated on the WAV (it would need the table check in OSC1's path).
7. Section 8's W3 gate "DSP size measured in the merged drums build" is information only now (Jeff: the drums budget is set
   aside): 7,092 B free there, 13,148 B in the default build (>= 1 KB).
