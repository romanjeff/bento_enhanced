# Plan S6f: OSC1 / OSC2 labels and audio-rate FM on Analog tracks (1.6.8)

Drafted 2026-11-08 against c333216 (BE4.22). Read-only investigations: scratch `rc26/ui/` (pane titles, keys,
descriptors, the sub's M4 -> M7 path) and `rc26/dsp/` (the sub on the M7, a through-zero FM kernel prototype,
sine / triangle shapes, costs). Nothing in this plan has run on the device. (M) = measured in Unicorn or a
build, (S) = static disassembly, (I) = inferred. Background: [`plan-synths-168.md`](plan-synths-168.md)
sections 3, 4, 6, 7 and the S6a / BE4.21 / BE4.22 sections.

**This sprint follows S6c.** S6c (Kick, kit BE4.30) is editing `analog.cpp`, `dsp.ld`, `m4_synth.c`,
`synth_shared.h`, `build.py` and is moving the M4 code layout right now. This work is kit **BE4.31**, built on
S6c's tree after BE4.30 is merged. Every byte count below is a request against S6c's new layout (section 7),
not a claim on today's.

## 0. What we are waiting on from the device (Jeff asked)

Two things gate or inform this sprint. Neither has been run: v4.22 has not been flashed.

1. **The v4.22 checklist, run on the device** (after v4.10 has passed). From it this sprint needs:
   - **The tuner reading** (v4.21 checklist section 3, first item: note 60 should read about 261.6 Hz). This is
     now a **blocking** item, because of a new finding: stock's engine init gives every oscillator a rate
     divider of 2 on Wavetable tracks (0x0807F180, called from 0x08058BD0; the Wavetable root's vtable slot 19
     returns 2 at 0x080A3420), stored at osc+0x108, and `an_osc` divides by it. `test_analog` skips that init
     step. With it run (`rc26/dsp/proto/pitchcheck.py`), our OSC1 plays **130.79 Hz at note 60, an octave low**
     (M). Stock's own wavetable path divides by the same field, so it may be compensated by table scaling we
     haven't traced; it is not yet a confirmed bug. FM ratios are defined against the oscillator pitch, so
     this has to be settled before FM is tuned (F0 below).
   - **The CPU spot check** (v4.21 checklist section 5) and the `ANALOG` black-box line (`avg_cyc`,
     `peak_cyc`, the share histogram, `over_with_analog`): the first real per-voice cost on the device.
   - The Analog handle check (`handle ... ok=1`) and save / load, which this sprint builds on.
2. **S6b, the full CPU check** (SPACED + 0 / 1 / 2 / 4 Analog tracks, dropped renders, CPI). Not blocking:
   the no-cap policy (S6 requirement 5) stands, and the indicator shows the load. But full FM adds about 40 %
   to an Analog voice (section 6), so S6b's numbers tell Jeff how many FM'd Analog tracks SPACED can carry.

## 1. Requirements

**Must**
1. **Labels:** on Analog tracks the oscillator pane titles read **OSC1** / **OSC2** instead of "WT1: <name>" /
   "WT2: <name>". Plain Wavetable and Granular tracks are unchanged.
2. **FM routings, audio rate, linear, through-zero:** OSC1 -> OSC2, SUB -> OSC1, SUB -> OSC2, each with its own
   amount. OSC2 -> OSC1 is not possible (stock renders OSC1 first; OSC2's output would be one block, 2.67 ms,
   late, which is a delay, not FM). Out of scope, said once so it isn't asked again.
3. **The sub as modulator:** our kernel reproduces stock's sub signal (waveform, pitch, glide, pulse width,
   retrigger) while stock's own sub stays audible as now.
4. **Zero cost and no change when off:** with all three amounts at 0 an Analog voice renders bit-identically to
   BE4.30 and through the same loops.
5. **Persistence:** the three amounts are saved as `bento_analog_*` attributes in project.xml and presets, and
   survive copy / paste and the handle rules of BE4.22.
6. **No regressions / rollback:** as S6 requirements 3, 4 and 10. A BE4.31 project opens on v4.10 / v4.22 /
   stock without a crash; the FM attributes are ignored there.
7. **Measured, not capped:** the black box and the emulator count FM's cost (S6 requirement 5).

**Nice (ordered)**
1. **A sine and triangle at the start of Position** (Jeff's item 3). I recommend promoting the **sine** to must:
   linear FM on a saw or square carrier at a useful index is mostly bright noise and aliases audibly at 48 kHz
   (section 4.3); the classic FM sounds need sine carriers. Without it requirement 2 delivers less than Jeff
   expects.
2. "Sub + Config" instead of "Osc + Config" for the sub's pane, so "OSC" names only OSC1 / OSC2.
3. OSC1 / OSC2 phase reset at note start following stock's WTOscReset (0xC6), so FM attacks are repeatable
   (section 4.6).
4. "OSC%d: %s" in stock's modulation destination names (deferred: section 2.3).
5. Later: Mod Matrix control of the FM amounts. The design leaves room (the M7 reads the amounts per block), but
   the Mod Matrix (group `mx`) is not ported to 1.6.8, so nothing is built for it now.

**Not the mechanism:** Mod Matrix routing of oscillators. It runs at block rate (one value per 128 frames), which
is a slow wobble, not FM.

## 2. Label change

### 2.1 Where the titles come from (S, registry builder emulated)
- The synth page builder 0x081612E4, Wavetable branch 0x0816143C, stores 11 title prefixes at view+0x11D5C..+0x11D84
  ("WT1: " 0x0818F144, "WT2: " 0x0818F14C, "Osc: ", ...) **after** our A6 key-row memcpy, so A6 can't change them.
- Only the row refresh 0x0815F758 reads them: `snprintf(buf, 0x40, "%s%.20s", prefix, wav_name)` then
  SLOT_TITLE(view + 0x29C0 + 0x70*i, buf), at two sites:
  - **0x0815F96C** (`21f037f8`, bl SLOT_TITLE): the Wavetable oscillator path (object type 6). Hook this one.
  - 0x0815F84A: the Granular path (type 7). Stays stock.
- Today an Analog pane shows "WT1: " plus nothing, or a stale wavetable name.

### 2.2 Design
- A 7th `bl` site (the A7 pattern of PANE_TEXT_1..6) at 0x0815F96C -> new `an_pane_title(slot, text)`:
  on `analog(current())` and text starting "WT1:" / "WT2:", it passes "OSC1" / "OSC2" (the stale name is
  dropped); otherwise it tail-calls stock's SLOT_TITLE with the original arguments.
- A separate function, **not** `an_pane_text`: that one maps every non-"No WAV" text to the shape string and
  would mangle titles.
- The site collides with nothing in `stock_168.py`; fingerprint the stock bytes in build.py as for A7.
- The CPU indicator ("Analog CPU nn%") is pane **text** (A7), not the title, so it is unaffected.
- Optional, recommended: "Sub + Config" at 0x0815FA82 (`20f0acff`) through the same handler (one more site,
  about 35 B).

### 2.3 Not built now
- **Modulation destination names** "WT%d: %s" (DEST_NAME 0x08159A5C, snprintf at 0x08159B8A): needs a naked
  shim that swaps r2 and keeps the stack argument, its two callers (0x08159DCC, 0x0815A002) are on a screen
  nobody has identified, and whether `current()` is the right track there is unknown. Defer until that screen
  is found in the display emulator; it is cosmetic.
- Track header "%s: Wavetable": stays. An Analog track is a Wavetable track to stock, and the menu entry says
  Analog.
- Encoder labels ("Level", "Pitch", "Position") are registry titles shared with the sub; nothing to change.

## 3. FM controls

### 3.1 Three amounts, not source + amount
Jeff's request can be read as one source selector plus one amount per carrier. I recommend **three independent
amounts**:
- a selector can't drive OSC2 from OSC1 and the sub at different depths at once (a common patch: sub for body,
  OSC1 for bite);
- it saves no keys and no storage bits (UI investigation (b): enum + 2 amounts needs 22 bits, 3 amounts 30;
  both overflow the AN4 word, so both need the second word anyway).

### 3.2 Placement (4 free encoders per oscillator row, slots 4..7)

| Row | Slot 0..3 (today) | Slot 4 | Slot 5 |
|---|---|---|---|
| OSC1 | Level, Pitch, Position, Glide | **Sub FM** | - |
| OSC2 | Level, Pitch, Position, Sync | **Sub FM** | **FM 1>2** |

Sub FM sits in the same slot on both rows (the UI investigation put it in slot 5 on OSC2; same-slot is easier to
find by hand). The sub's row is full (8 controls), so its FM controls must live on the carriers' rows, which is
also where they read naturally ("how much does the sub modulate *me*"). Slots 4..7 on the oscillator rows have
never been shown on the device (S6a's Glide / Sync in slot 3 haven't been flashed either); stock's Osc row uses
all 8, so the risk is small, but it is a device item.

### 3.3 Descriptor, range, taper, default
- **Descriptor type 10, 0..1000, token ""**, displayed "0.0%".."100.0%" (same as Pulse Width 0xC5 and Glide;
  the format is decoded statically from the formatter's `tbh` at 0x08173A7A and matched against the registry).
- **Mapping to the modulation index:** `I = 8 * (v/1000)^2`. So 25 % = I 0.5 (warm), 50 % = I 2 (bright,
  through zero for SUB at its 1:2 ratio), 100 % = I 8 (harsh). The square law gives fine control where FM is
  most musical; a linear 0..8 puts the useful range in the first quarter of the knob.
  - Close call: show the index instead of a percent. No stock descriptor type prints "I 2.00" from 0..1000
    except a raw integer; a percent is honest enough and costs nothing. Revisit if Jeff wants it.
- **Index, not depth in Hz:** `kin = I * inc_mod` (the modulator's phase increment), so a patch keeps its timbre
  across the keyboard (DX-style). The deviation is `I * f_mod`.
- **Default 0** for all three: a new Analog instrument and every BE4.22 project sound exactly as before.

### 3.4 Keys (3 needed)
- **0x132 and 0x18B:** clean in both cores, no stock range test covers them (S). They are on the Track FX list
  (21 clean for 19 needed, so taking two leaves exactly 19: tell the FX plan).
- **Third key, two options:**
  - (a) **0x1A8**: clean by immediates, but inside the M7 native table store 0x08059990 (0x18C..0x1B6), the same
    risk class S6c is re-reviewing for its drum keys. Use it if S6c's review (T6 set_property log) clears that
    class.
  - (b) **Two keys only:** one "Sub FM" key on both rows, told apart by the event's object handle (ev+4) and
    CONTEXT_GET's ctx. About 60 B more code and one new unverified assumption (that the two rows' objects are
    distinguishable in both paths).
  - Recommendation: wait for S6c's verdict on 0x18C..0x1B6; (a) if cleared, (b) otherwise.
- **Don't use** the plan's "3 spare" keys 0x128 / 0x129 / 0x12A: each has a real M7 case in the tbh at
  0x08071CE2 (0x128 -> 0x08072320). 0x148 too (0x0808B494). Section 5 of `plan-synths-168.md` should be
  corrected (a docs change for whoever owns it; not done here).
- All three go through claim()'s all-or-nothing allow-list and test_keys, as the existing synth keys.

### 3.5 Storage
- **AN4 gets a second word per entry:** `fm = a12 | sub2 << 10 | sub1 << 20` (10 bits each, the encoder's own
  value, so no rounding and no stuck knob). AN4 grows 256 -> 320 B: AN4_SLICE 0xE20 -> 0xDE0, the slice canary
  shrinks 0x420 -> 0x3E0 B (constants only). S6c may also be taking canary space: coordinate.
  - Rejected: 7-bit amounts packed into the 18 spare bits. If the encoder steps in units finer than the
    quantum, the read-back value rounds and the knob sticks.
- **AN4_MAGIC "AN43":** a warm-reset table from BE4.30 is started again (as I5 did for AN42). Cost: Analog
  flags kept across a warm reset are lost once, at the first boot of BE4.31; the project on the card is
  unaffected.
- **Publish:** `fm` in SaTrack.r4 (spare), SA_LAYOUT 2 -> 3 (the M7 refuses a layout it doesn't know, as now).
- **M7 side:** the Root cache line is full (32 B), so the cache carries the three amounts as 8 bits each in
  `mode` bits 8..31 (index resolution about 1/255 of full scale: inaudible as a static setting; per-block
  smoothing of `kin` (4 B per oscillator, in AnOsc's spare 36 B) avoids zipper noise when a knob is turned).
- **XML:** `bento_analog_fm_sub1`, `bento_analog_fm_sub2`, `bento_analog_fm_12`, integers 0..1000 on the
  track's params element, written by `an_save` and read by `an_load` with default 0, clamped to 1000.
  Written only when the track is Analog (as the other attributes).
  - **Stock 1.6.8:** ignores unknown attributes and drops them on its own save (the known risk in
    `plan-synths-168.md` section 13). The track becomes a plain Wavetable track.
  - **v4.10 / v4.22:** v4.22 reads `bento_analog*` it knows and ignores the FM ones (S, it looks names up;
    a test loads a BE4.31 project through BE4.22's `an_load` to confirm). Saving there drops the FM amounts and
    keeps Analog / Sync / Glide. v4.10 has no Analog: plain empty Wavetable, as requirement 10 already covers.
  - **v3.72 (1.6.1):** frozen and byte-identical, never sees 1.6.8 projects in our workflow. If one is opened
    there, its reader treats our attributes as unknown like stock 1.6.1 does (I, not tested; v3.72 has no
    Analog code). Not a BE4.31 test item.
- Copy / paste / preset: the second word rides on the same mark / lookup / clone path as the first (H1-H3).

## 4. FM design (M7)

### 4.1 Signal flow per voice-block
Stock's order: OSC1 (voice+0x7A4), OSC2 (+0x994), then the sub (+0x4C). Our A2 hook renders OSC1 and OSC2.

```
OSC1 render (A2):
  if any amount > 0:
    s[i] = sub_copy(sub, n)                 # stock's sub signal for this block (4.2), into the shared buffer
    OSC1 with inc_i = inc1 + k_s1 * s[i]    # through-zero kernel (4.3)
    buf[i] = k_s2 * s[i] + k_12 * y1[i]     # OSC2's modulator, in increment units; y1 = OSC1 before Level
OSC2 render (A2):
    OSC2 with inc_i = inc2 + buf[i]
sub render: stock, untouched, audible as today
```
- `k_s1 = I_s1 * inc_sub`, `k_s2 = I_s2 * inc_sub`, `k_12 = I_12 * inc1`.
- y1 is taken **before** OSC1's Level, so OSC1 at level 0 is a pure modulator (the classic 2-op patch). So E3's
  "skip a silent OSC1" must be off whenever `k_12 > 0`; E3 for OSC2 stays.
- One 512 B float buffer serves all voices (voices render one after another), not one per voice. Proposed home:
  the unused SA_UI area (synth area +0x0800, M7-private use, the M4 never reads it). Dependency on S6c not
  claiming it.

### 4.2 The sub copy (pushback on "own phase")
Jeff's plan was an own phase at the sub's unmodulated pitch. The DSP investigation found a better way:
- When OSC1 renders, stock's sub has **not yet rendered this block**, so its core (sub+0x20) still holds this
  block's starting phase (u32 at core+8).
- Our copy reads that phase, recomputes the increment exactly as stock does (`sub_inc`, from +0x0C detune,
  +0x10 note, +0x84 pitch modulation, +0x04 octave incl. our glide, +0xDC divider), and generates the samples
  with stock's formulas and constants (0x2FFFFFD2 / 0x307FFFD2), stock's sine table and the Square's latched
  pulse width (core+0x14 / +0x18). It writes nothing back.
- Result (M, `subcheck.py`): **bit-exact** against stock's sub render, all five waveforms, 12 blocks per case,
  across note changes, glide, retrigger on / off, PW 30 %, detune; increments equal every block. No state, no
  drift, and it follows the sub's pitch modulation and retrigger for free, which an own phase would not.
- **Noise** is covered (stock's random stride at core+0x10). It FMs into a noisy, gritty tone, which is what a
  user picking Noise as modulator asks for.
- **Square DC:** subtract `1 - 2*PW` in the copy only, so a non-50 % square doesn't detune the carriers.
- **Amplitude: unit, not the sub's Level** (my recommendation, open decision FD1). Reasons: the main use of a
  modulator is inaudible (sub Level 0, FM on); the M7 constructor's Level default is 0, so "follow Level" would
  make Sub FM silently do nothing on a fresh instrument. The copy therefore ignores +0xC8 / +0x44.
- **Must test: does stock advance the sub's phase when its Level is 0?** If stock skips the sub's render at
  level 0, core+8 freezes and our copy would play a constant. Fallback if so: our own phase in AnOsc's spare
  bytes (Jeff's original plan), reset from stock's retrigger flag. Decided by an emulator test in F2 before the
  kernel work builds on it.
- Cost (M): `sub_inc` 76 instructions, then 679 (Noise) .. 2222 (Sine with stock's table) per block; stock's own
  sub render is 2560-3816.

### 4.3 Through-zero kernel
- Per sample `inc_i = inc + kin * m[i]`, clamped to |inc_i| <= 0.45 (`vminnm` / `vmaxnm`): at most one wrap per
  sample, which the 2-point polyBLEP needs.
- Edges: `(threshold - p) / inc_i`, one VDIV per edge (1-4 per block); E2's per-block reciprocal no longer
  holds under FM. PolyBLEP residuals depend only on the fraction, unchanged.
- Negative increments mirror the forward half: same thresholds, jumps negated, a wrap below 0. A direction
  change inside a sample needs no correction (no jump there).
- Hard sync with FM: the master records a wrap in either direction; the slave resets to 0 and keeps its
  direction (a backward slave wraps at once: two coincident corrections, correct). Tested finite (M, T5 case).
- Checked (M): zero modulation matches today's kernel within 1.2e-7; backward runs match mirrored forward runs
  (saw 2.9e-5, triangle 1.2e-6).
- Aliasing (M, off-harmonic / harmonic energy, polyBLEP vs naive): saw 434 Hz -31.3 vs -15.1 dB at I = 4 through
  zero; 1734 Hz -18.8 vs -8.5 dB. PolyBLEP stays 10-16 dB ahead, but **deep FM of high saws aliases audibly**
  (no oversampling at 48 kHz). Triangle and sine are far cleaner (sine -85 dB at 1734 Hz, I = 4).

### 4.4 Loop variants: pushback on the full specialised set
The prototype's 91 loop variants are 53 KB; the DSP window is 36 KB (36,608 B) and S6c's drums need it. Even a
pared specialised set is about 20 KB. I recommend:
- **The existing non-FM loops stay exactly as they are** (requirement 4).
- **FM uses one generic through-zero loop per role** (free / sync master incl. writing OSC2's buffer / sync
  slave): the shape as a blend computed per sample, about 3 KB in all. Always through-zero (the forward-only
  skip saves 3 instructions per sample and doubles the loops).
- Specialise later, only the cases the S6b numbers and Jeff's patches show are hot (e.g. sine carrier FM).
- Exit gate: T-count per voice-block for the generic loop within 1.3x of the prototype's specialised figures
  (section 6), else specialise the saw and sine cases before shipping.
- **VDIV hoisting:** the compiler once hoisted the wrap division out of its branch (129 VDIVs per block, about
  14 cycles each). The test counts VDIVs per block, not only instructions (`kernelcheck.py` does).

### 4.5 Sine / triangle shapes (nice 1, sine recommended as must)
- **Triangle** `1 - 4|p - 1/2|`, corners where the square's edges are, two-point polyBLAMP (slope change
  ±8|inc|). 22.7 instructions per sample (the first try with corners at 1/4, 3/4 cost 43).
- **Sine** = odd 5th-order polynomial of the triangle (1.570243, -0.641711175, 0.0714682192; max error 8.1e-5,
  worst harmonic -82 dB, no libm). No corrections.
- **Position mapping** (Position 0xC9 stays type 1, 0..255, raw display): sine -> triangle -> saw -> square ->
  8 % pulse. Proposed anchors: 0 sine, 48 triangle, 96 saw, 176 square, 255 pulse, each with a ±3 plateau so a
  pure shape is easy to hit and runs its cheap loop. Today's saw -> square -> pulse gets 160 of the 256 steps
  instead of 256: still fine for a shape morph.
- **This changes the meaning of Position in saved Analog patches.** No Analog patch exists on the device yet
  (v4.21 never flashed, v4.22 not yet). So the cheap moment is now, before BE4.22's device run produces patches,
  or at least before Jeff relies on them. Doing it later needs a version attribute and a remap on load.
  Recommendation: do it in BE4.31 and say so in the v4.22 checklist notes ("Position will be remapped").
- Rejected alternative: a separate Wave selector key. One more key in a scarce set and one more slot, for a
  choice Position already expresses.
- Non-FM loops for the new shapes: sine gets its own (2650 instructions per block); triangle and the two morph
  ranges use the generic loop with `kin = 0` at first (code budget), specialised only if measured hot.

### 4.6 Phase reset (nice 3)
FM timbre depends on the phase relation of carrier and modulator at the attack. The sub resets at note start when
WTOscReset (0xC6) is on (sub+0xE0); OSC1 / OSC2 run free, so with FM every note's attack sounds a little
different. Option: on Analog tracks, OSC1 / OSC2 also reset when stock's note start marks them (osc+0x1D4 = -1,
outside AnOsc: write a marker after each render, a -1 means "note started"; `forget()` restores -1). It should
follow 0xC6 if stock's own WT oscillators honour it (to verify, S). It also changes the sound of
non-FM patches when 0xC6 is on (on by default): today OSC1 / OSC2 run free. Recommendation: build it in F4 if the
check is clean, following 0xC6, and say in the checklist that non-FM Analog attacks now start at phase 0.

## 5. M4 changes (summary)
- Site at 0x0815F96C (title), optional 0x0815FA82.
- `synth_keys`: 3 descriptors and titles; `an_page_keys`: k[4], k[12], k[13]; `an_context` and `an_view_event`
  for the three keys; AN4 second word in mark / lookup / clone / menu (the BE4.22 menu rule applies: a new
  handle starts at 0); publish to SaTrack.r4; XML save / load; black box (`fm=` count of tracks with any amount,
  and a per-block FM CPU share from the M7 stats).
- Picking Analog again on an Analog track with a new handle resets the amounts to 0, as Sync / Glide.

## 6. CPU cost
Unicorn instructions per 128-frame block, note 60, build.py's DSP flags (M, prototype):

| Case | Today | With FM |
|---|---|---|
| One oscillator, saw | 1739 | 3037 (through-zero), 3685 (sync master writing OSC2's buffer) |
| One oscillator, mixed shape | 3401 | 4559 / 5715 |
| Sub copy | - | 76 + 679..2222 |
| Full voice: sub sine -> both, OSC1 -> OSC2, saw | 12.8 k | about 18.3 k (+5.5 k, +43 %) |

- 10 voices, full FM: about +4 % of the 1.28 M-cycle block at 1 CPI (S6b gives the real CPI). A fully sounding
  Analog track was estimated at 11-20 %; with full FM about 16-28 %.
- The generic loop (4.4) will cost more than these specialised figures; the exit gate bounds it at 1.3x.
- All amounts 0: no change (the existing loops run, the sub copy is skipped).
- The black box's ANALOG line gains `fm_blocks=` (blocks with FM active) so the device data separates the cost.

## 7. Budget and dependencies on S6c's new layout

| Resource | Needed | Today (c333216) | Dependency |
|---|---|---|---|
| M7 DSP code | <= 8 KB: sub copy about 2 KB, 3 generic FM loops about 3 KB, sine loop and shape code about 2 KB, glue | 6,840 B used of 36,608 B | S6c's drums share the window: needs 8 KB free after BE4.30 |
| M7 state | AnOsc +4 B (kin smoother), 156 -> 160 of 192 B; global 512 B buffer + 4 B | - | SA_UI area (+0x0800) not taken by S6c |
| M7 / M4 shared | SaTrack.r4; SA_LAYOUT 3; Root cache `mode` bits 8..31 | spare | S6c not using r4 / mode bits |
| M4 code (SYNTH_CODE) | about 900 B (title 60, FM about 800, sine text 8, optional Sub title 35) | about 26 KB before SYNTH_LIMIT, shared with the drum UI | S6c's new layout keeps >= 1 KB for this |
| M4 wrapper block | 24 B (CONTEXT_GET X3 entries) | about 92 B free | S6c's move keeps it, or gives an equivalent |
| M4_CODE window | 0 B | 152 B left | none |
| Slice | AN4 +64 B (canary 0x420 -> 0x3E0) | - | S6c's canary use |
| Registry keys | 0x132, 0x18B, + 0x1A8 or a shared key | clean | S6c's key review; FX plan loses 2 |

Assumption stated plainly: these bytes will be available in S6c's layout. If S6c's merge leaves less than this
in the DSP window, the shapes (4.5) go first, then the triangle / morph specialisations, not the FM.

## 8. Tests
All offline, in the existing suites, on stock code in the emulator where stock code is involved.

**test_synth_ui (M4)**
- Display emulator (T5 style) running 0x0815F758 on an Analog, a plain Wavetable (with a table name) and a
  Granular track: OSC1 / OSC2 on Analog only, "WT1: name" on plain, Granular untouched; with the optional site,
  "Sub + Config" on Analog only.
- The emulated registry builder: the three descriptors (type 10, 0..1000), titles, slot positions, the key
  allow-list (all-or-nothing, test_keys).
- Encoder event -> AN4 second word -> CONTEXT_GET reads it back exactly (no rounding) for 0, 1, 999, 1000.
- AN4: magic AN43 restarts an AN42 table; mark / lookup / clone / menu carry `fm`; a new handle starts at 0.
- XML: round trip; a BE4.22 project (no FM attributes) loads with 0; a BE4.31 project through BE4.22's
  `an_load` keeps Analog / Sync / Glide; values > 1000 clamp.
- Publish: SaTrack.r4 and SA_LAYOUT 3; the M7 refuses layout 2 / accepts 3.

**test_analog (M7, stock's real voice path)**
- The engine init step 0x0807F180 added to the rig (rate divider 2), with a pitch assert at note 60, after F0
  decides what the right answer is.
- Sub copy vs stock's sub render, bit-exact: five waveforms, note changes, glide, retrigger, PW latch, detune,
  pitch modulation through +0x84, **sub Level 0** (the open question in 4.2).
- All amounts 0: output bit-identical to BE4.30 and the same instruction count (requirement 4).
- **Golden values from an independent host build:** a Python / numpy reference (written separately from the
  kernel: per-sample phase accumulation in float64, naive waveforms plus the documented polyBLEP / BLAMP
  corrections) for each routing, through-zero and sync cases; tolerance as the existing kernel tests (FMA, FTZ).
  Plus spectral checks: the 1:2 sub -> sine case puts energy at the Bessel-predicted sidebands for I = 1, 2.
- Mirror test (backward == mirrored forward), the clamp (|inc_i| > 0.45 input stays finite and bounded), sync +
  FM finite, DC of the square modulator removed.
- Budgets: instructions and **VDIVs** per voice-block, asserted as upper bounds for the generic loop.

**Mutations** (each must fail a test, on a copy of the tree): FM amount ignored per routing (3); sub copy reading
the smoother (+0x84 / +0xE4) instead of the base; sign of the backward jump flipped; the clamp removed; E3 skip
left on with FM 1>2 > 0; y1 taken after Level; DC term dropped; the title hook applied to Granular / plain
Wavetable; the second AN4 word not cloned; `fm` not published; the XML default not 0; Position anchors shifted.

## 9. Sprints

**F0. Pitch question (offline now, plus the v4.22 tuner reading).** Measure stock's WT oscillator with a real
table in the emulator through 0x0807F180: does stock compensate the divider of 2? If stock plays 261.6 Hz and
ours 130.8 Hz, it is a BE4.22 bug: fix in `an_osc` (one constant) in BE4.31, or as its own small kit if Jeff
wants it sooner. Exit: a written verdict here, agreed with the device tuner reading.

**F1. Labels.** Title site and `an_pane_title`; optional Sub title. Exit: display-emulator test, mutants, bisect
class (site stock on `BE_DISABLE=analog`).

**F2. Sub copy.** `sub_inc`, `sub_copy`, the Level-0 test first. Exit: bit-exact suite, Level-0 question
answered (or the own-phase fallback built and tested).

**F3. FM kernel.** Generic through-zero loops per role, the shared buffer, smoothers, E3 rule. Exit: golden
tests, mirror, budgets incl. VDIVs, zero-amount bit-identity, mutants; DSP code within its budget.

**F4. M4 controls and storage.** Keys (after S6c's key review), descriptors, AN4 second word, publish, XML,
black box. Optional phase reset (4.6). Exit: test_synth_ui as section 8, mutants.

**F5. Shapes** (if Jeff agrees with the remap). Sine / triangle, anchors, the sine loop. Exit: alias figures
within 2 dB of the prototype, zero-FM shapes tested.

**F6. Kit BE4.31** (on S6c's BE4.30; flash only after BE4.30's checklist passed). Device checklist:
- the tuner at note 60 (and the sub an octave below on a plain WT track for comparison);
- titles OSC1 / OSC2 (and Sub) on Analog; plain WT and Granular as stock;
- each FM knob audible on its own; Sub FM with sub Level 0 still modulates; Sub waveform change changes the FM
  timbre; FM 1>2 with OSC1 Level 0 still modulates;
- through zero: OSC2 sine, FM 1>2 at 100 %, pitch steady (no drop-out or octave jump), no DC thump;
- Sync + FM: no blow-up, no silence;
- Position: sine at 0, triangle, saw, square, pulse at their anchors;
- all amounts 0: sounds as BE4.30 (A/B by ear on the same patch);
- save / power cycle / load, a preset, copy / paste, picking Analog again (amounts to 0);
- rollback: the project on v4.22 (Analog / Sync / Glide kept, no FM) and on stock (plain WT, no crash);
- CPU: one track, 10-note chord, all FM on, the indicator and the ANALOG line with `fm_blocks`; SPACED + 1 FM'd
  Analog track; listen for crackle.

## 10. Risks

| Risk | Why it bites | Mitigation |
|---|---|---|
| BE4.22 an octave low | FM ratios and the tuner are wrong; every Analog patch shifts when fixed | F0 first; fix before patches exist |
| Stock skips the sub's render at Level 0 | the copy plays a constant: Sub FM dead with the sub silent | F2 test first; own-phase fallback |
| Render order or core layout differs from the trace | the copy reads a phase already advanced | bit-exact test on stock's real voice path; black-box `fm_blocks` |
| DSP window after S6c | 8 KB may not be free | generic loop; shapes are cut first |
| Generic loop slower than estimated | FM'd tracks cost more than section 6 | 1.3x exit gate; specialise hot cases |
| Compiler hoists VDIVs | invisible in instruction counts, about 1.8 k cycles a block | VDIV count asserted |
| Aliasing at deep FM on high saws | harsh, inharmonic | sine / triangle carriers; documented, no oversampling |
| Keys 0x18C..0x1B6 class | a native table store on the M7 | S6c's review decides 0x1A8; shared-key fallback |
| Slots 4..7 on oscillator rows unproven | controls not shown or misrouted | stock's Osc row uses 8; device item |
| Position remap after patches exist | saved patches change shape | do it in BE4.31, before patches |
| AN43 restart | warm-reset Analog flags lost once at first boot | the card's projects are unaffected; noted in the checklist |
| CPU with several FM'd tracks | +43 % per voice | S6b numbers; indicator; D1 guard remains open |

## 11. Open decisions for Jeff

| # | Decision | Options | Recommendation |
|---|---|---|---|
| FD1 | Sub FM amplitude | unit (independent of sub Level) / follow sub Level | unit: the sub as an inaudible modulator is the main use, and Level defaults to 0 on the M7 |
| FD2 | Controls | 3 amounts / source selector + amount | 3 amounts (more patches, same cost) |
| FD3 | Amount scale | square law to index 8, shown as % / linear | square law |
| FD4 | Third key | 0x1A8 after S6c's review / one shared Sub FM key | 0x1A8 if cleared |
| FD5 | Sine / triangle at the start of Position | yes, remap now / a separate selector / no | yes, now; sine as a must |
| FD6 | DSP code | generic FM loop (about 3 KB) / specialised set (about 20 KB) | generic first |
| FD7 | Phase reset of OSC1 / OSC2 at note start | follow 0xC6 / free-running as today | follow 0xC6, if stock's WT honours it |
| FD8 | Extra renames | "Sub + Config" / "OSC%d" destinations / neither | Sub yes; destinations deferred |
| FD9 | Octave fix, if F0 confirms the bug | in BE4.31 / its own kit now | its own small kit only if you want to play Analog before S6c lands; otherwise BE4.31 |

## Decisions (Jeff, 2026-11-08)
- **FD5:** yes, sine / triangle at the start of Position, remapped now (in BE4.31, before patches matter).
- **FD1:** Sub FM amplitude is unit, independent of the sub's Level.
- **FD7:** no phase reset: OSC1 / OSC2 stay free-running (section 4.6 is not built).
- **FD8:** no extra renames: "Osc + Config" stays as it is; only the WT1 / WT2 titles become OSC1 / OSC2.
- Open: FD2-FD4 and FD6 follow the recommendations unless Jeff says otherwise; FD9 depends on F0.

## F0 verdict (2026-11-09, checked and independently verified in the emulator, rc27/f0check + rc27/f0verify)
- **No octave bug.** Wavetable oscillators run at 96 kHz: stock sizes WT1 / WT2 / sub buffers to 2N (root vt[19]
  0x080A3420 returns 2), renders, then decimates by 2 through the 11-tap FIR 0x0807FCC0. The divider of 2 at
  osc+0x108 compensates exactly. Stock WT, BE4.22 an_osc and stock's sub all play 130.81 / 261.62 / 523.25 Hz at
  notes 48 / 60 / 72. The false 130.79 Hz read a 2N buffer as 48 kHz.
- **Two real BE4.22 bugs from the same fact** (static + device-shaped rig, depend on the device block N = 128,
  to confirm with the black box `n_max=`):
  1. **Hard sync never engages on the device:** an_osc gets n = 2N = 256 > AN_EVENTS = 128, and
     `sync && n > AN_EVENTS` renders free-running (analog.cpp, analog.h AN_EVENTS). Fix: AN_EVENTS = 256 (state
     size check: AnOsc lives at osc+0x10C..+0x1CC) or a per-block event cursor.
  2. **Glide runs at half the set time:** glide_coef uses s * 48000 while n counts 96 kHz samples. Fix: use
     48000 * [osc+0x108].
  Tests to add: the rig runs the engine init, renders 2N and decimates through 0x0807FCC0; assert 261.63 Hz at
  48 kHz, sync audibly engaged at n = 256, glide time within 5 %.

## Added request: Bit Depth per oscillator (Jeff, 2026-11-09)
- **What:** OSC1 and OSC2 each get **Bit Depth**: Full / 12 / 8 / 4 (enum, default Full). Not on the sub.
- **Where in the signal:** on our oscillator output (±1), before stock's level / smoothing / mix, in the 96 kHz
  domain (stock's 2x decimator FIR follows). Symmetric mid-tread rounding to 2^(N-1) steps per polarity
  (`q = round(x * s) / s`, s = 2^(N-1) - 1), no dither, no sample-rate reduction (not asked for).
- **Cost:** Full = nothing (skipped). On: about 3-4 instructions per sample per oscillator (multiply, round
  `vrintn`, multiply), well under 5 % of an Analog voice.
- **With FM (recommendation, decision BD1):** OSC1's crushed signal is the FM modulator (what you hear is what
  modulates: gritty, stepped FM). Alternative: modulate with the clean signal.
- **Controls / storage:** one more encoder per oscillator row (OSC1 then uses 2 of its 4 free slots, OSC2 3 of
  4); 2 bits per oscillator in AN4 (fits the second word's spare bits); `bento_analog_bits1/2` in project.xml.
  Keys are the scarce part: FM needs 3 and Bit Depth 2 more, so use the shared-key approach for Bit Depth (one
  key, the oscillator told apart by the event's object handle, about 60 B of M4 code) unless the key scan finds
  enough clean keys.
- **Tests:** quantiser against an independent host model at each depth; Full bit-identical to today; mutations.
- Built in the Analog FM sprint (same files), after the drum work.
