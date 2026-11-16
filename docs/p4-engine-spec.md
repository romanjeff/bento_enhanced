## P4-5 engine specs: own lean engines, no DaisySP (prototypes, 2026-11-16)

Decision (Jeff, 2026-11-16): no DaisySP dependency. Every engine is our own code with hard budgets: **≤ 60 instructions per sample per voice** (Unicorn, 128-frame block at 48 kHz; one block is 1.28 M cycles at 480 MHz), **≤ 1,536 B code**, **≤ 320 B state** (the plan 3.5 cell). No libm, no per-sample transcendental calls, early voice end, and mono re-strike without a click.

Labels: **M** measured (Unicorn on the M7 ELF, built with build.py's compiler and flags), **S** from source or disassembly, **I** inference or judgement. "Rev" means the independent reviewer's figure. Where the reviewer's figure differs from the designer's, the reviewer's figure is used.

Prototypes live in `scratchpad/engines/<engine>/`. The repo was not modified.

### Review status: four of five engines need fixes

| Engine | Review verdict | What blocks it |
|---|---|---|
| Kick | **fix** | Clips on re-strike (peak up to 1.204, +1.6 dBFS). Denormal claim false. |
| Snare | **fix** | Reaches the ±1 clip on plausible settings and on re-strikes. 12 B code margin. |
| HiHat | **fix (3 major)** | Velocity has almost no level range. Loudness follows Tune and clips at ratio 1. Oscillators collapse onto each other at high Tune × ratio. |
| Clap | **fix** | The "air" term turns it into a hiss: centroid 6.8 kHz, not a ~1 kHz band. Voice end is -65 dB, not -70. |
| Tom | **pass** | Minor issues only: aliasing at ratio 8, clamp at extremes, code-window pressure. |

None of them failed outright. Every fix is a constant change or control-rate code, and each fits the cost budget. The fixes do add code, though, against already thin size margins (see 4.x below).

---

### P4-5.1 Kick (BE Kick)

**Reference design (I).** TR-909 bass drum structure with the parameter set of Plaits' synthetic bass drum: a sine oscillator with an exponential pitch sweep, an exponential VCA and a short pulse+noise click. Why the 909 and not the 808: the 808 is a pinged resonator with almost no sweep, so Sweep and Sweep Decay would mean little. The 909/Plaits structure maps one to one onto the 7 knobs, and it still covers 808-like sounds (preset `808_long`: low Tune, long Decay, small Sweep, dark Tone).

**Algorithm (S).**
- *Per block:* all knob curves are computed here. `exp2_` uses the exponent bits plus a 7th-order polynomial (error < 2e-6). `expneg_` is a 4th-order Taylor series used for every decay factor. A 2^x polynomial is too coarse for factors this close to 1: 1e-5 error in k is a 50 % error in a 6 s decay.
- *Per sample, no branches:*
  - LCG noise.
  - Phase: `ph += inc + dinc*pe + dfm*nz; ph -= rint(ph)` (VRINTX), then `pe *= kp`.
  - Sine: parabola plus one correction, about 7e-4 from a true sine.
  - VCA `a - b`, the difference of two decays, so the attack comes from b.
  - Click `ce(0.5+nz)` with `ce *= kc`, lasting 2.5 ms.
  - Dirt: cubic soft clip with drive 1..10, plus noise FM.
  - Tone: one-pole low-pass.
- *Hit:* `level = max(level_new, e)`, `a = level`, `b = level - e`, so the VCA rises from its current value. Then `pe = 1` and the click is set. Phase, the Tone filter and the RNG carry on.

**Knobs (0..127, v = k/127)**

| Knob | Mapping | Default |
|---|---|---|
| Tune | 25·2^(3v) Hz, 25..200 Hz (~28 c/step) × pad pitch ratio | 44 (≈ 51 Hz) |
| Decay | T60 = 0.05·2^(7v) s, 50 ms..6.4 s | 60 (≈ 0.49 s) |
| Tone | one-pole LP 300·2^(6v) Hz, 300 Hz..19.2 kHz | 64 (≈ 2.4 kHz) |
| Accent | body (0.55+0.45a), click (0.1+0.9a), sweep (0.6+0.4·a·vel), attack 2→0.5 ms | 80 |
| Sweep | peak pitch ratio 1+15v² (up to 4 oct), capped at 0.45 fs | 56 (3.9×) |
| Sweep Decay | pitch tau 1 ms·2^(7.6v), 1..194 ms | 50 (≈ 8 ms) |
| Dirt | drive 1+9v, wet v, noise FM ±0.5v²·f0 | 12 |
| Velocity | body ×(0.25+0.75vel), click ×vel (see D7 below) | — |

**Re-strike (M, measure_full.log).** The VCA never steps, and a soft hit does not pull a louder tail down. The click measure is the residual energy above 1 kHz, relative to a fresh hit:
- With the click on, a re-strike scores +0.1..+2.3 dB, so it sounds like a fresh hit.
- With the click off, the worst case is -3.8 dB.
- A naive hard restart scores up to +17 dB.

What remains is a slope kink from the pitch re-sweep. A pitch-glide variant was tried and rejected.

**Voice end (S/M).** Each block the engine returns a provable upper bound on all later output, `B = max(|lp|, (1-mix)X + mix·min(drive·X, 1))` with `X = a + |b| + 1.5ce`. The voice ends when B < 5.01e-4 (**-66 dBFS**). This costs about 10 instructions per block and 0 per sample. In the 0.5 s after the end, the output peaks at -66.2..-67.1 dBFS (M).

Measured lifetimes: default 0.52 s, `808_long` 2.84 s, `909_punch` 0.30 s. The claim that "stock's release softens the end" is **unverified (Rev)**: drums.cpp returns early once a cell is CELL_DONE. The step is at most -66 dBFS, though.

**Cost, size, state.**

| | Designer | Reviewer |
|---|---|---|
| Instructions per sample (worst case, M) | 45.2 (437 knob sets × ratios 0.25/1/8, first-hit, running and re-strike blocks) | 45.20 confirmed (150 sets, ratios 0.0625..16) |
| Code at -O2 + minsize (M) | 1,376 B | engine ≈ 1,370..1,376 B |
| Code at -Os (M) | — | .text 0x576 = 1,398 B incl. bench, **43.0 instr/sample** |
| State (S) | 76 B | — |

The loop has 41 instructions, with no VDIV, no calls and no libm (Rev, S). Build note (Rev): the file is not in build.py's DSP_OPT, so it would build at -Os. That is fine, but it should be decided on purpose. The DrumEngine glue is not counted, and the margin is about 160 B.

**Review corrections**
1. **Headroom (medium, Rev M).** "Peak ≤ 1.000" holds for single hits only. On a re-strike with long Decay, high Accent and open Tone, the full click lands on a body still near 0.8:
   - knobs (40,127,127,127,0,0,0): 1.014 with hits one block apart, 1.133 on a 16th roll at 120 BPM;
   - random search: up to **1.204 (+1.6 dBFS)**.
   - The default and `808_long` presets stay under 1 on a 32nd roll.

   *Fix:* in trigger, `ce = min(ce, max(0, (1-level)/1.5))`. This costs about 0 per sample. Then re-run the peak scan with roll and flam events included.
2. **Denormals (low, Rev M).** The claim that the "-66 dB end stays ~1e-34 above denormals" is false. With FZ off, b goes subnormal at 0.107 s and stays stuck there, and so does ce at 0.213 s. The design *does* depend on FZ+DN. Stock's reset sets FZ+DN (S, plan-synths-168), so it works today. *Fix:* flush b, ce and pe below 1e-20 per block (≈ 6 instructions per block), or verify FPSCR.FZ on the device.
3. **The reference is a codegen and precision check, not design validation (low).** ref.py was written from kick_spec.txt, which transcribes kick.cpp. Musical settings match to ≤ 8.3e-5 against f32-rounded coefficients. `zap` reaches -47 dB through f32 decay-factor drift. The degenerate all-127 setting at ratio 8 diverges at full scale.

**Test plan**
- *Reference model:* ref.py (float64) with coefficients rounded to f32. Tolerance 1e-4 on the 6 musical presets over the first 0.5 s. Use a spectral or envelope tolerance, not a sample tolerance, for `zap` and long notes.
- *Golden:* the M7 ELF output hashes for presets default, `808_long`, `909_punch`, `dirty`, `zap` and `short_click`, plus a re-strike roll.
- *Assertions:*
  - Peak ≤ 1.0 over single hits **and** rolls and flams (gaps 1..47 blocks) across the knob grid. This test fails today.
  - Re-strike HF residual ≤ fresh hit + 3 dB with the click on.
  - The voice-end bound holds: max over the next 0.5 s < -66 dBFS.
  - Worst-case cost ≤ 60 instructions per sample; code ≤ 1,536 B including glue.
  - No subnormal state with FZ off, once the flush is added.
- *Mutations (each must make a test fail):*
  - reset phase on trigger (re-strike test);
  - `level = level_new` without max() (ducking or click);
  - drop the click headroom cap (peak test);
  - use exp2_ instead of expneg_ for decay factors (decay-time test);
  - off-by-one in the -66 dB threshold (voice-end test);
  - remove the 0.45 fs sweep cap (aliasing or peak test).

**Open issues**
- D7 velocity double-count (see the cross-engine section).
- Accent is per pad, not per step.
- Knob range change: 0..127 replaces KICK_LO/HI in Hz, and the M4 value text needs the new curves.
- The -66 dBFS threshold is absolute, before stock's gain and sends.
- Plan 3.4's crossfade and the real voice path are not exercised.
- Sound quality is I until Jeff listens.

---

### P4-5.2 Snare

**Reference design (I).** TR-808 snare: two bridged-T-style resonant modes at a 1:1.84 ratio (I, from circuit write-ups), pinged by a short pulse, plus a separately enveloped noise "snappy" path. The FM knob adds a TR-909-style pitch sweep (up to 4×).

Why: a linear resonator excited by a pulse gives click-free re-strike by superposition, because the new pulse adds to the ring that is already there. It is also cheap. A Plaits-style 5-mode body would cost about 2.5× as much. 909-style oscillators would need a phase reset or a separate VCA on re-strike.

**Algorithm (S)**
- *Per block, only when knobs or the ratio change:*
  - Own exp2 (degree 5, 2.4e-6) and decay `k = exp2(-1.4427/(tau·fs))`.
  - Chamberlin coefficient `f = 2x - x³/3` with F clamped to fs/6 (≤ 1 cent low).
  - Exact damping `q = (1-k²)/f`.
  - Sweep depth `f_im = min(f·D, 1-f)`, which keeps the filter stable.
- *Trigger:*
  - `h = vel/127·accent`.
  - Pulse decay 1.1-0.7h ms, so a harder hit is brighter.
  - Input gains normalised so each mode rings at about h.
  - `p = h, m = 1, en = h`. The resonators and the noise filter are untouched.
- *Per sample:*
  - Two Chamberlin SVFs with `fa = f + f_m·m`.
  - Decays for p, m and en.
  - LCG noise into a one-pole LP (Tone), then a fixed 1 kHz one-pole HP.
  - `y = o1·bp1 + o2·bp2 + on·en·hp`, then a hard clamp to ±1 (VMINNM/VMAXNM).

**Knobs (0..127, u = v/127)**

| Knob | Mapping | Default |
|---|---|---|
| Tune | F = 100·2^(2u) Hz, 100..400 × pad ratio; mode 2 = 1.84 F | 64 (202 Hz, M) |
| Decay | body tau 20 ms·2^(5u), 20..640 ms; mode 2 = 0.6×; snappy tau 35..560 ms | 50 |
| Tone | noise LP 1.2·2^(3u) kHz, 1.2..9.6 kHz; mode-2 level (0.15+0.45u)·body; noise gain ×(1-0.4u) | 64 |
| Snappy | noise level 1.2u²(1-0.4·tone); body ducks ×(1-0.45u) | 80 |
| Accent | hit strength 0.4+0.6u (level, pulse brightness, noise) | 90 |
| FM | 909 sweep, start depth 3u² (≤ 4× pitch), sweep decay 2.5..12.5 ms | 30 |
| k7 | unused | 0 |
| Velocity | multiplies h (see D7) | — |

**Re-strike (M).** p, m and en restart. The resonators and the noise filter carry on.
- *Body only:* the re-struck output equals the old ring plus a fresh hit within 9.1e-7 (**-121 dBFS**). It is exact superposition, with no engine click.
- *FM 127:* phase stays continuous and the frequency steps up. That is a slope change (max |dy| 1.35× a fresh hit), not a step.
- *Default:* max |dy| 1.15×.
- *Roll levels (Rev M):* because re-strike is superposition, the hit-to-hit level depends on the gap against the body period. The tail RMS of the second hit ranges from -3.4 to +4.0 dB at default and from -2.8 to +5.5 dB body-only. This is authentic linear-808 behaviour and should be documented as intended. A possible mitigation is to scale the new pulse by (1 - c·|body|), one multiply at trigger.

**Voice end (S/M).** At block end the engine computes the bound `o1(|lp1|+|bp1|) + o2(|lp2|+|bp2|) + on·en + p` (≈ 15 instructions per block). When it is < 5e-4 (**-66 dBFS**), the block fades linearly to 0, the state is zeroed and done is set. Later blocks write zeros and return 0. The reviewer confirmed the block before the end peaks at 2.6..4.8e-4, followed by a clean linear fade.

Lifetimes: default 0.69 s, Decay 0 0.23 s, Decay 127 4.3 s.

**Cost, size, state.**

| | Designer | Reviewer |
|---|---|---|
| Steady (M) | — | 36.7 instr/sample |
| Trigger block (M) | — | 37.3 |
| Knob change + trigger (M) | — | 40.7 |
| End-fade block, worst case (M) | 42.8 | 42.8 |
| Code at -Os (M) | 1,524 B | 1,524 B; margin **12 B** |
| State (S) | 120 B | — |

Function sizes (M): snare_block 552, coefs 564, trigger 168, exp2_ 124, decay 28, coef 52.

The inner loop has 37 instructions, one VCVT, and no VDIV, VSQRT or BL (Rev, S). Correction (Rev): build.py's `DSP_OPT.get(name, "-Os")` already defaults to -Os, so no DSP_OPT entry is needed. The file just must **not** be added to the -O2 list.

**Review corrections**
1. **Headroom (medium, Rev M).** The claim that "single hits ≤ 0.68, rolls ≤ 0.975, nothing realistic clips" is wrong.
   - Single hits: Tone + Snappy + Accent at 127 with FM 0 reaches 1.0, and all knobs at max clip.
   - Double hits: 223 of 1,944 grid cases reach the rail, at gaps of 2.7..32 ms.
   - A body-only bass snare (0, 64, 127, 0, 127) is clean as a single hit at 0.693. Re-struck in phase at 10.7, 21 and 32 ms, it hard-clips 281, 171 and 60 samples. That is audible on a low tone.
   - Cause: o1+o2 reaches 1.38·body at Tone 127, and the noise term adds up to about 0.7.

   *Fix (0 B, constants only):* use `o1 = (0.78-0.3u)·body` to hold o1+o2 constant across Tone, and lower the top of the noise curve so the worst single hit over all 6 extremes is ≤ 0.7. Then re-run the single-hit plus double-hit grid.
2. **Code size (medium).** 1,524 B is measured on the prototype's entry point, not the DrumEngine apply/trigger/render API, so integrating as is will probably cross 1.5 KB. *Precondition for integration:* move exp2_, decay and coef (≈ 204 B) to a shared drum-maths helper counted once, which leaves about 1.32 KB of snare-specific code. Then re-measure through the real glue.
3. **Reference match understated (low).** Worst |diff| is **4.7e-3 (-46 dBFS)** at Tune 89, Decay 127, ratio 8, and 2.8e-3 at Tune 127, Decay 119, ratio 4. The designer quoted 3.06e-4. The cause is the cubic 2·sin coefficient being about 1.8e-4 low at the 5.9 kHz mode 2 (0.3 cent), with phase drift building over long rings. It is not "float32 drift". ref_snare.py also still flushes p, m and en below 1e-15, which the C++ dropped.
4. **Snappy range (low, musical).** Energy above 1 kHz overtakes the body only at Snappy 127 (-4.3 dB). The default is +5.6 dB tonal. A noise-dominant 909 crack is out of reach. Raising the curve has to go together with fix 1.
5. **D7 citation.** The velocity rule is plan-synths-168 D7, not plan-p4-drums P4-D7.

**Test plan**
- *Reference model:* ref_snare.py (float64). Add an option to use the same cubic coefficient, and remove or flag the 1e-15 flush. Tolerance 5e-5 on the cubic-matched reference for long rings at ratio ≤ 8.
- *Golden:* the 13 WAV scenarios, as ELF output hashes.
- *Assertions:*
  - Superposition residual (body-only, FM 0) < -110 dBFS.
  - Peak ≤ 1 with no clipped samples over the single-hit plus double-hit grid (gaps 1..24 blocks). **This test fails today.**
  - Voice-end bound and linear fade.
  - Cost ≤ 60; code ≤ 1,536 B through the glue.
- *Mutations:*
  - zero lp/bp on trigger (superposition test);
  - drop the `1-f` sweep cap (stability test at FM 127, ratio 8);
  - use a non-exact q (decay-time test);
  - remove the end fade (step at voice end);
  - swap mode-2 ratio or level constants (spectral-peak test at 201/370 Hz).

**Open issues**
- Code margin and the shared helper.
- The denormal flush was removed for size, so m relies on FZ. Restoring it costs about 40 B.
- Roll-level unevenness from superposition is intended 808 behaviour; Jeff should judge it.
- The hard clip is the only limiter; a cubic soft clip needs about 16 B.
- D7 velocity.
- Mode ratio 1.84, mode-2 decay 0.6×, the fixed 1 kHz HP, and Decay driving both body and snappy are all I.

---

### P4-5.3 HiHat

**Reference design.** TR-808 hi-hat (S for topology and frequencies, I for "convincing"):
- six free-running square oscillators at 205.3, 304.4, 369.6, 522.7, 540 and 800 Hz (Werner/Abel/Smith, DAFx 2014), summed;
- a band-pass, an exponential VCA and a high-pass;
- a Plaits-style noise crossfade.

Why: the archetypal machine hat. The squares are integer adds (UADD16, two oscillators per word), and every stage is a recurrence. The RingMod HiHat (464 B) fails the 320 B cell (P4-D4).

**Algorithm (S).**
- *Per sample, 37 instructions in the loop:*
  - Six 16-bit phases stepped with UADD16; `sq` = how many phases are past half a turn.
  - Noise: xorshift32 (same cost as an LCG, better low bits).
  - Mix `x = gm(sq-3) + gn·noise`; centring at sq-3 avoids a DC step on a fresh cell.
  - TPT SVF band-pass at Q 1.4.
  - VCA `env *= kd`.
  - One-pole HP at 0.7·fc.
  - Clamp to ±1 (VMAXNM/VMINNM).
- *Per block:* hh_apply, 361 instructions. tan uses a Padé [5/4] approximation, the decay uses a series, and 2^x is a degree-4 polynomial.
- *Closed mode:* once env is 12 dB below the hit, the decay factor is squared, giving a "tick then choke" shape.

**Knobs (0..127). Defaults {64, 48, 72, 20, 80, 0, 0}.**

| Knob | Mapping | Default |
|---|---|---|
| Tune | 2^((v-64)/32), ±2 oct around 808 pitch, × ratio; per-lane cap 0.45 fs (**see fix 3**) | 64 |
| Decay | closed T60 25..400 ms; open 0.15..3 s | 48 |
| Tone | BP centre 1.6·2^(3v/127) kHz, 1.6..12.8 kHz, ×(1+0.3acc), cap 16 kHz; HP at 0.7 fc; level compensation over Tone | 72 (≈ 5.2 kHz; **Rev: too dark, see fix 6**) |
| Noise | metal (1-n²)/3, noise 0.7n(2-n) | 20 |
| Accent | acc = v/127·vel; level (0.35+0.65acc)·1.3; BP +30 % | 80 |
| Open/Closed | ≥ 64 is open | 0 |
| k7 | unused | 0 |

**Re-strike (M, click.py).** `env = max(env, level)`, the 808's capacitor behaviour. The closed tail stage resets; phases, noise, SVF and HP carry on.
- LF click energy is -57.3 dB at worst, equal to the no-handover baseline within 0.5 dB.
- The largest step is ≤ 0.99× the hit's own p99 |dy|.
- A hard voice steal measures -37.1 dB for comparison.

*Decision for Jeff:* max() means a soft hit on a louder ring does not restart the envelope. The literal "restart" rule (`env = level`) ducks soft hits in a roll. Either is a one-line change.

**Voice end (S/M).** When env < hit·5e-4 (**-66 dB re that hit**, one compare per block), the engine writes zeros and returns peak 0. While writing zeros it still costs 5.95 instructions per sample, because the loop is not skipped. drums.cpp's -96 dB / 32-block QUIET rule then frees the cell **85 ms later**. Lifetimes: closed at Decay 0 is 18.7 ms; open at the longest Decay is about 3.3 s.

**Cost, size, state.**
- Worst case **40.49 instructions per sample (M)**, on a re-strike block (trigger 12 + apply 361 + render 4,810 per 128 frames). Steady render is 37.5.
- Code **1,420 B at -Os (M)**, byte-identical on the reviewer's rebuild: hh_apply 708, hh_render 428, exp2c 128, hh_init 78, hh_trigger 48. That leaves 116 B, with glue not counted.
- State **104 B (S)**.

**Review corrections**
1. **Velocity has almost no level range (major, Rev M).** acc = Accent·vel. The span from velocity 1 down to 0 is:
   - Accent 0: 0 dB (velocity does nothing);
   - Accent 80: 6.5 dB;
   - Accent 127: 8.8 dB.

   Hat grooves depend on ghost notes, so this fails "Accent and velocity". *Fix:* make velocity a full level curve (e.g. `0.1+0.9·vel²`, ≥ 20 dB) and Accent a depth on top of it. Resolve D7 first so velocity is applied once, either in the engine or by stock's route. Test: velocity 1→0.1 span ≥ 18 dB.
2. **Loudness follows Tune, and it clips at ratio 1 (major, Rev M).** RMS goes from -24.7 dBFS at Tune 0 to -10.5 dBFS at Tune 127. Tune 127 clips at ratio 1, and 88 of 360 grid configurations clip, up to 1,985 samples at ratio 8. *Fix:* scale gm by the effective pitch (one exp2c per block, ≈ 30 instructions per block). Requirement: no clipping at ratio ≤ 2 and Accent 127.
3. **The cluster collapses and aliases at high Tune × ratio (major, Rev M).** The per-lane cap pins the upper oscillators all at 21.6 kHz, which destroys the 808 ratios. Alias power relative to signal:
   - Tune 127, ratio 8, Tone 0: **+2.2 dB**;
   - Tune 127, ratio 2: -17.8 dB;
   - defaults: about -13 dB, the same as Plaits' naive squares and masked there.

   *Fix:* cap the common scale so the 800 Hz oscillator stays at or below about 6..8 kHz. PolyBLEP would break the budget.
4. **A NaN velocity poisons the cell (minor, S).** The clamp lets NaN through, the SVF state is never reset, and VMAXNM turns a NaN output into full-scale -1 DC. *Fix:* `vel = !(vel > 0) ? 0 : min(vel, 1)`.
5. **Code headroom (minor).** Fixes 1..3 add an estimated 40..80 B, and the glue is not counted. *Fix:* fold the compensation exponents into one exp2c call and use a pre-scaled increment table in hh_apply.
6. **Default too dark for an 808 (minor, Rev M).** At default, the 2..6 kHz band carries about the same energy as 6..12 kHz, and above 12 kHz is -15 dB. *Fix:* move the default Tone to about 95..105 (fc 7.5..9 kHz), or fix the HP nearer the 808's corner. Label "808 similarity" as I until compared against a recording or the Werner model.
7. **Coarse tuning at low ratios (minor).** The floor-truncated 16-bit increments give 24..150 cents per step. *Fix:* round to nearest (+0.5f).

**Test plan**
- *Reference model:* ref_hh.py (float64). It agrees to ≤ 3e-4 on the prototype's quantised phase grid. Free-running cases with drifted lanes (ratio 8 / Tune 127, knob moves) need a spectral tolerance, not a sample tolerance.
- *Golden:* the 10 WAV scenarios as ELF hashes.
- *Assertions:*
  - Velocity span ≥ 18 dB (**fails today**).
  - No clipping at ratio ≤ 2 (**fails today**).
  - Alias power ≤ -12 dB at all Tune × ratio corners (**fails today**).
  - Level within ±2 dB across Tone and across Tune.
  - Re-strike LF click ≤ warm baseline + 1 dB when the new hit dominates.
  - NaN velocity gives silence, not DC.
  - Cost ≤ 60; code ≤ 1,536 B including glue.
- *Mutations:*
  - per-lane cap restored (alias test);
  - `env = level` (soft-roll level test documents the choice);
  - drop the Tone compensation (level test);
  - sq not centred (DC step on a fresh cell);
  - remove the closed tail stage (decay-shape test).

**Open issues**
- D7 velocity.
- The DrumCell.values comment says 0..100 while the engine uses 0..127.
- Open/closed choke is left to stock's choke groups. WAV 10 only simulates it.
- The idle zero-fill could be skipped if render may return early.
- The Unicorn rig re-asserts FZ after VCMP, an emulator quirk.

---

### P4-5.4 Clap

**Reference design (I).** TR-808/909 analog handclap: noise through a VCA driven by a burst of sawtooth pulses (the "several hands" smear) plus a slower reverb-tail envelope, then a band-pass around 1 kHz. Neither DaisySP nor Plaits/Peaks has a clap model, so the circuit is the reference.

One deliberate change: the VCA sits *before* the filter. That is closer to an impulse exciting the cavity of the hands, and it band-limits every envelope step, so re-strike is click-free by construction (S).

**Algorithm (S).**
- *Per sample, 23-instruction loop:*
  - LCG noise.
  - Event counter.
  - Envelope `e = p + t` with decays on p and t.
  - Input `x = noise·e·qi`.
  - Chamberlin SVF.
  - `y = clamp(bp + h·hp, ±1)`.
- *Pulse events:* 4 per hit, about 40 instructions each.
  - Amplitudes {0.80, 0.90, 0.75, 1.0}.
  - Uneven spacing ratios {1, 0.82, 1.24}.
  - The main pulse sets `t = max(t, 0.55g)`.
- *Per block (≈ 155 instructions):*
  - Sine as a degree-5 Taylor series.
  - `qi = q·sqrt(1500/fc)` via inline VSQRT. `__builtin_sqrtf` links compiler_rt's sqrtf under -fno-builtin, which check_dsp_elf refuses. **This applies to every engine.**
  - exp via a Taylor series, 2^x via a polynomial.
- Order of apply and trigger within a block does not matter.

**Knobs.** The prototype uses **0..100**, the others 0..127; see the cross-engine section. Defaults {45, 40, 50, 50, 0, 0, 0}.

| Knob | Mapping | Default |
|---|---|---|
| Tone | BP fc 600·2^(2.5T) Hz, 600..3,394 Hz, ×(1+0.3a) × ratio, clamp 200..7,000 Hz; "air" h = 0.6T (**see fix 1**); Q 1.7 | 45 |
| Decay | tail T60 50 ms·2^(5D), 50 ms..1.6 s | 40 |
| Spread | pulse spacing 1+24·S² ms, 1..25 ms (**see fix 4**); pulse tau max(0.25 ms, spacing·(0.4-0.2a)) | 50 |
| Accent | a = Accent·vel; g = 2.4(0.55+0.45a), fc +30 %, snappier pulses | 50 |
| k5..k7 | unused | 0 |

**Re-strike (M).** Trigger sets i = 0 and the counter to 1. The SVF state, the RNG and the tail carry on; the tail is only raised by max() at the new main pulse.

The plan's section 5 metric is the largest sample jump in the re-strike block. A re-strike scores 0.9..1.34× the first hit's own attack jump, and the excess is the old tail. A clap re-strike is by definition a new noise attack, so **P4-2 must set a threshold relative to the first-hit attack for this engine**. The HF-residual metric does not discriminate for a noise source: a hard reset scored no worse than carry-on. That is reported as a negative result.

**Voice end (S/M).** The engine ends when the pulse train is done and p+t < 5e-4. This costs about 6 instructions per block. Lifetimes: default 240 ms, all knobs at 0 53 ms. Decay 100 runs longer than 1.87 s, and CAP_FRAMES bounds it.

**Correction (Rev M):** the last block before the cut is as loud as **-65 dB re peak (-65.3 dBFS)** at Tone 100 / Accent 100, and -65.2 dB re peak at all knobs 0, against the claimed "< -70 dB". The cause is qi (up to 2.74) and the air term. *Fix:* E_END ≈ 2e-4, or scale the threshold by qi·(1+h). This is free.

**Cost, size, state.**
- Worst case **24.5 instructions per sample (M)**: 3,135 per block on a 216-point grid with re-strikes. Steady tail 22.6. Small blocks raise it: n = 16 gives 44.7 (Rev M), still within 60.
- Code **1,284 B at -O2 / 1,188 B at -Os (M)**, at the same cost, so build at -Os.
- State **88 B (S)**.

**Review corrections**
1. **The air term makes it a hiss (major, Rev M).** `h·hp` adds unfiltered HP noise up to 24 kHz.
   - Default: spectral peak 1,460 Hz but **centroid 6,773 Hz**; about 28 % of the energy is above 10 kHz.
   - Tone 100: centroid 10.3 kHz.
   - The equal-loudness claim breaks too: dark peaks at 0.58 while default and bright clip at 1.0.

   *Fix:* use h = 0.25·T², or low-pass the air term at 8..10 kHz (+2..3 instructions per sample), or use the SVF's notch or peak output. Include h in the loudness normalisation. Target: 0.5..5 kHz dominates at the defaults.
2. **Denormal claim false (minor, Rev M).** With FZ off, p goes subnormal while the voice still rings (43 blocks from about 950 ms). It is harmless with stock's FZ+DN. *Fix:* correct the text, and optionally flush p < 1e-6 per block.
3. **Voice end at -65 dB**, see above.
4. **Spread too wide (minor).** At Spread 100 the main hit lands at 78 ms, which reads as a flam or a late hit. *Fix:* spacing 1 + 12·S² ms, or start the tail at the trigger. Decide by listening.
5. **The reference shares the SVF and LCG equations (minor).** It verifies the code against the spec, not the acoustic claims. *Add:* a measured BP frequency response at fc = 200, 1,000 and 7,000 Hz, and loudness against Tone.
6. **Edge cases (nit).**
   - After the last pulse the counter wraps, and `PULSE_AMP[4]` would be read after 2^32 samples. *Fix:* guard idx ≥ 4 in pulse().
   - vel 0 at Accent 0 plays a full hit, so the binder must never call trigger for a note-off.

**Test plan**
- *Reference model:* ref.py (float64, exact exp/sin/sqrt, closed-form segments). It matches the prototype to 1.34e-5 over 7 goldens. Keep a tolerance of 5e-5.
- *Golden:* the 7 golden cases plus the WAV scenarios.
- *Assertions:*
  - Band-energy split: 0.5..5 kHz is the largest band at default (**fails today**).
  - Measured BP centre within ±5 % of fc.
  - Peak ≤ 1 across Tone and Accent (**fails today**).
  - Last block before done ≤ -70 dB re peak in all corners (**fails today**).
  - Re-strike jump ≤ 1.5× the first-hit attack jump (the engine-specific threshold).
  - Cost ≤ 60 at n = 16 and n = 128.
- *Mutations:*
  - move the VCA after the filter (re-strike jump test);
  - reset the SVF state on trigger;
  - use a single fixed spacing ratio (pulse-schedule test);
  - remove the max() on the tail (tail-continuity test);
  - drop qi (loudness-against-Tone test).

**Open issues**
- Knob range 0..100 against 0..127.
- `DrumEngine::trigger(c)` has no velocity argument.
- The tail and the snap share one band-pass; the 808 tail is darker (+3..4 instructions per sample to split them).
- Level-matching against the other engines.
- **P4-D9 (b) still applies:** Clap ships after Snare, HiHat and Tom if the window allows.

---

### P4-5.5 Tom (review: pass)

**Reference design (I).** TR-909 tom: a VCO with an exponential pitch envelope (the "doom" glide, here the Sweep knob) and a waveshaper (here sine toward a rounded square), a noise "stick" burst through a low-pass, an exponential VCA, and a 909-style accent bus.

Rejected: an 808/Plaits bridged-T resonator. A swept resonator needs per-sample coefficient maths, which breaks the budget, and the 808 tom barely bends, so Sweep would be weak.

**Algorithm (S).** Stock's M7 reset sets FZ+DN (S: 0x080403EC..0x0804040E).
- *Per sample, 41 instructions + branch, with no VDIV, VSQRT or BL:*
  - Decays for amp, pitch and stick envelopes.
  - Two cascaded 0.35 ms one-poles on the amp, so a re-strike's level step becomes an S-curve.
  - Phase with VRINTX wrap.
  - Parabolic sine (error 1.1e-3).
  - Soft clip `u - u³/3` with peak normalisation.
  - LCG noise into a one-pole LP.
  - Clamp to ±1.
- *Per block:*
  - apply(), 139 instructions, with **4 VDIVs** (Rev; the spec said 3).
  - render() head computes the sweep depth and the end test.
- *Trigger (45 instructions):* restarts e, pe and the stick. Phase resets only if the voice was idle, so a fresh hit starts at a zero crossing.

**Knobs (0..127). Defaults {64, 64, 40, 48, 64, 0, 0}.**

| Knob | Mapping | Default |
|---|---|---|
| Tune | 45·2^(3.2k) Hz, 45..413 Hz × ratio; increment cap 0.12 cycles | 64 (138 Hz) |
| Decay | T60 50 ms·2^(5.3k), 50 ms..1.97 s; pitch tau 0.15·T60, clamped 8..80 ms | 64 (0.32 s) |
| Tone | drive 0.5+1.6t+0.4a (sine to rounded square); stick level 0.3t; stick 3..15 ms, LP ≈ 0.6..6 kHz | 40 |
| Sweep | start +2.5·k·hs octaves; w = min(b(2^D-1), 0.45-b) | 48 |
| Accent | amp 0.55+0.4a, drive, sweep, stick ×(0.7+0.3a) | 64 |
| k6, k7 | unused | 0 |
| Velocity | **timbre only** (sweep 0.6+0.4v, stick 0.5+0.5v); level stays with stock per D7 | — |

**Re-strike (M).** Body only, knobs (50, 90, 0, 0, 64), at 8 phases. Energy above 3 kHz:

| Case | dBFS above 3 kHz |
|---|---|
| Our re-strike | -65.7..-82.3 |
| Naive reset | -33.2..-53.1 |
| Fresh hit | -81.2 |

The largest step is 0.0123, against 0.0121 for a new note and 0.255 for a naive reset. At default knobs, with the stick, the re-strike is within 0.4 dB of a fresh hit.

At full Sweep and Accent (Rev M), the worst case is -53.7 dB against -62.9 dB fresh, with the step equal to the fresh hit's (0.0831 against 0.0815). This is the intended 909 pitch reset. Add it to the click table.

**Voice end (S/M).** The bound `max(e, h, g) + ne` < 5e-4 (**-66 dBFS absolute**), checked at about 8 instructions per block. On that block, e and ne go to 0 and the 2-pole smoother fades g to about 2e-9, so the final step is about -175 dB. The engine then returns exactly 0.

Lifetimes: default 344 ms, high tom 267 ms, floor tom 971 ms, 909 boom 1.51 s, max_all 2.16 s, min_all 59 ms. The idle zero-fill costs 550 instructions per block.

**Cost, size, state.**
- Worst case **44.21 instructions per sample (M)**: 5,659 per block over 144 runs × 30 blocks, ratios 0.25/1/8.
- Code **1,356 B at -O2 (M)**: apply 468, render 504, trigger 188, exp2_ 100. Reviewer's rebuild is byte-identical.
- State **76 B (S)**.

`#pragma clang loop unroll(disable)` is load-bearing: the first build was 2,420 B.

**Review notes (all minor)**
1. **Aliasing at high ratio (Rev M).** Non-harmonic energy at Tune 127 / Tone 127: -55.7 dB at ratio 1, -46.1 dB at ratio 4, **-33.6 dB at ratio 8**. Sweep can start at 18.7 kHz. *Fix, control rate only:* cap the sweep start at about 0.12-b and scale drive down with the peak frequency.
2. **Window pressure.** The plan budgeted the Tom at about 0.4 KB and the measured figure is 1.36 KB. *Fix:* build apply, trigger and start minsize or -Os and keep render at -O2. Update plan section 4.
3. **Clamp at extremes.** e + ne can reach 1.25: max_all clips 67 samples, electro_zap 1. NaN maps to -1. *Fix:* scale so e_max + stick_max ≤ 1, and add a NaN guard on ratio.
4. **Docs.** 4 VDIVs, not 3. tom_ref.py's docstring describes the end test wrongly. The reference has the same author, so its f32coef match (1.05e-3) validates the implementation, not the design.

**Test plan**
- *Reference model:* tom_ref.py with f32-rounded coefficients, tolerance 1.1e-3 at max_all and 1e-4 at the named settings. The raw float64 reference diverges by 0.12 through phase drift and is unusable for sample tests.
- *Golden:* the 11 WAV scenarios as ELF hashes.
- *Assertions:*
  - Re-strike HF ≤ fresh hit + 16 dB body-only, and ±1 dB with the stick.
  - Largest step ≤ 1.1× a fresh hit's.
  - apply/trigger order gives bit-identical output.
  - Non-harmonic energy ≤ -45 dB at ratio 8 after fix 1.
  - No clipping at musical presets.
  - Return value exactly 0 after the end.
- *Mutations:*
  - always reset phase on trigger (step test);
  - a single-pole smoother (HF test);
  - drop the idle-only phase reset (fresh-hit zero-crossing test);
  - remove the sweep cap (alias test);
  - move the end-test bound from max(e, h, g) to e alone (premature-end test).

**Open issues**
- Binder changes: a velocity byte in DrumCell; return 0 means ended now; only trigger() is called on re-strike, never start(), which re-seeds the RNG; the cell layout moves from 192 B / 0..100 to the plan's 320 B / 0..127.
- The RNG seed comes from the cell address, so the stick is not bit-reproducible if cell assignment changes.

---

### P4-5.6 Cross-engine issues (decide before P4-2/P4-5 integration)

1. **Velocity (plan-synths-168 D7, not P4-D7).** Engines handle velocity three different ways today:
   - Kick, Snare and HiHat scale level by velocity;
   - Clap scales the Accent amount;
   - Tom uses velocity for timbre only.

   Stock's pad route may apply velocity again. **Jeff's call:**
   - (a) the engines own velocity and the stock route is off on drum pads; or
   - (b) the binder passes vel = 127 and engines treat velocity as timbre only, as the Tom does.

   The HiHat's velocity curve has to be fixed under either option.
2. **Knob range.** Kick, Snare, HiHat and Tom use 0..127 (plan 3.1). Clap and today's `DrumCell.values` / KICK_LO/HI use 0..100 or Hz. Unify on 0..127. The M4 value text needs the per-engine curves.
3. **Interface.**
   - `trigger(c)` gains velocity, or the binder stores it in the cell.
   - `render` returning 0 (or done) should end the voice immediately, instead of the -96 dB / 32-block QUIET rule. That rule holds every engine 85 ms or more, and holds the DaisySP Kick for seconds.
   - Re-strike calls only trigger().
4. **Voice-end threshold.** All engines use -66 dB. It is absolute for Kick, Snare and Tom, relative to the hit for HiHat, and relative to the envelope for Clap (which really ends at -65 dB, fix pending). It is applied before stock's gain and sends, so with heavy pad gain or a long reverb send it could be audible. Consider a per-engine QUIET, and verify end steps through the real drums.cpp path.
5. **Denormals.** Every engine relies on stock's FZ+DN in practice (S: reset code; plan-synths-168). The Kick, Snare and Clap specs claimed independence and were wrong. **One device check of FPSCR on the M7 audio path settles it.** Alternatively, add per-block flushes at about 6 instructions per block and 20..40 B each.
6. **Shared maths.** exp2, decay-factor, sine and VSQRT helpers are duplicated across 5 engines (about 100..200 B each). A shared drum-maths unit (like dsp_lib's be_powf) saves an estimated 0.4..0.8 KB (I). It is a precondition for the Snare.
7. **Optimisation level.** Kick (-O2 + minsize), Snare (-Os, must not join the -O2 list), HiHat (-Os), Clap (-Os is smaller at the same cost) and Tom (-O2 with an unroll pragma; split apply to -Os). Each engine's DSP_OPT placement must be explicit, with size and cost re-measured in the real translation unit.
8. **Peak safety.** Kick, Snare, HiHat and Clap hit or exceed the ±1 clamp on plausible settings. Every engine's peak test must include rolls and flams (re-strike gaps 1..47 blocks), not only single hits.
9. **References.** Every reference model was written by the engine's own designer from its spec. They are codegen and precision checks, not design validation. Design validation means listening, plus targeted acoustic checks: BP response, band splits, pitch glide, and level against knobs.
10. **The plan 3.4 crossfade** was exercised by no prototype. Each engine's own output is continuous across a re-strike; the post-chain step still needs the rig test.

### P4-5.7 Summary

| Engine | Reference | Instr/sample worst (M) | Code B (M) | State B (S) | Voice end | Re-strike | Review |
|---|---|---|---|---|---|---|---|
| Kick | 909 BD / Plaits synthetic BD | 45.2 (-O2); 43.0 at -Os (Rev) | 1,376 (margin 160) | 76 | provable bound < -66 dBFS | VCA continuous, phase kept, max() level | **fix**: re-strike peak 1.204; FZ dependence |
| Snare | 808 SD (2 modes + snappy) + 909 sweep | 42.8 (end block); 36.7 steady | 1,524 at -Os (**margin 12**) | 120 | state bound < -66 dBFS + block fade | exact superposition (-121 dBFS residual) | **fix**: clips on plausible hits and re-strikes; size |
| HiHat | 808 HH (6 squares, BP, VCA, HP) | 40.49 | 1,420 at -Os (margin 116) | 104 | env < -66 dB re hit, then QUIET +85 ms | max() envelope, body kept | **fix (3 major)**: velocity, Tune loudness and clip, aliasing |
| Clap | 808/909 clap (VCA before BP) | 24.5 (44.7 at n = 16) | 1,284 -O2 / 1,188 -Os | 88 | p+t < 5e-4; **Rev: -65 dB re peak** | new burst into continuous SVF | **fix**: air hiss; end level |
| Tom | 909 tom (VCO + sweep + shaper + stick) | 44.21 | 1,356 -O2 (margin 180) | 76 | bound < -66 dBFS + smoothed fade | 2-pole S-curve VCA, phase kept | **pass** (minor: aliasing at ratio 8) |
| **Total** | | | **6,960 B** (5 engines, no glue) | | | | |

### P4-5.8 Kit-level CPU and size estimate

All CPU figures are Unicorn instruction counts at 1 CPI, against 1.28 M cycles per 128-frame block (480 MHz).

| Scenario | Instructions per block | Share of the block |
|---|---|---|
| One voice, worst engine (Kick 45.2 × 128) | 5.8 k | 0.45 % |
| Today's DaisySP Kick, for comparison (411/sample) | 52.6 k | 4.1 % |
| **8 overlapping voices, all at the worst case** | 46.3 k | **3.6 %** |
| 16 pads all sounding (one voice per pad, mono re-strike) | 92.6 k | 7.2 % |
| All 32 cells (plan 3.5) at the worst case | 185 k | 14.5 % |
| Control-rate overhead (apply/trigger, about 150..520 instructions per voice-block) | — | already inside the worst-case figures |

Plan section 4 measured 6 DaisySP Kicks at ≈ 25 % and 32 at ≈ 131 %, and BE4.50 ships a 6..8 engine cap. With these engines a 16-voice kit fits in under 10 % at 1 CPI. The VDIV/VFMA dependency chains could push the real CPI to about 1.3..1.5 (I), which is still ≤ 11 % for 16 voices. **The voice cap can rise to 16..32 once the BE4.50 DRUMS stats line gives the real M7 CPI.**

Early voice end matters too. A typical groove of a kick at 0.5 s, a snare at 0.7 s, closed hats under 0.1 s, a clap at 0.24 s and a tom at 0.35 s holds about 3..5 voices at once, against seconds of tail per hit under the -96 dB rule. Instruction counts are not cycles, though; flash wait states and FP latency are unmeasured.

**DSP window (I on M sizes).**
- Engines: 6.96 KB measured, plus glue (≈ 0.4 KB × 5 = 2 KB, plan section 4), comes to **about 9 KB**.
- Available: about 6.1..6.6 KB in the window after the binder, plus whatever removing the DaisySP Kick (drum_kick, SynthBassDrum, Svf) frees. That amount is not measured here.
- Shared maths would save about 0.4..0.8 KB, and moving control-rate code to -Os, minsize or the C slice saves more.

So **the five engines do not fit without the efficiency pass**. This is consistent with plan risk "DSP window: four engines don't reliably fit" and with P4-D9 (b): Snare, HiHat and Tom first, Clap later. Measure the DaisySP removal first; it decides how tight the window is.

### P4-5.9 Listening notes (Jeff's acceptance test: every "musically convincing" claim is I until then)

All WAVs are rendered from the M7 ELFs in Unicorn, so they are bit-exact to the prototypes. Paths are under `C:\Users\rjrom\AppData\Local\Temp\claude\9acdfe94ba00fd09\71a55988-76ea-4e33-b531-3ea1a26f5f35\scratchpad\engines\`.

**Kick (`kick\wav\`)**
- Character: `kick_default.wav`, `kick_808_long.wav`, `kick_909_punch.wav`, `kick_dirty.wav`, `kick_zap.wav`, `kick_short_click.wav`, `kick_high_tom.wav`.
- Velocity and accent: `kick_velocity_1_.75_.5_.25_.1.wav`, `kick_accent0.wav`.
- Re-strike: `kick_roll_default.wav`, `kick_roll_808_long.wav`.
- **A/B:** `kick_roll_808_bodyonly_ours.wav` against `kick_roll_808_bodyonly_NAIVE_hard_restart.wav`. This is the clearest demonstration of the re-strike design.
- Pitch: `kick_pitch_-12_0_+7_+12.wav`.
- Listen for: does the 808_long tail feel like an 808? Is the default sweep (63 Hz settling to 50 Hz) punchy enough? The re-strike clipping issue is *not* in these files.

**Snare (`snare\wav\`)**
- `01_default_x4.wav`.
- Knob sweeps: `02_tune_…`, `03_decay_…` (the Decay-127 hit is cut at the end of the file), `04_tone_…`, `05_snappy_0_40_80_127.wav`, `06_accent_…`, `07_fm_…`, `08_velocity_…`.
- Presets: `09_808ish.wav`, `10_909ish.wav`.
- Rolls: `11_restrike_roll_default.wav`, `12_restrike_roll_long_fm.wav`.
- Flam: `13_flam_restrike_3ms_vs_fresh.wav`. The second hit quantises into near anti-phase and almost cancels; that is the superposition behaviour to judge.
- Listen for: is the roll level evenness acceptable? Is there enough snappy range at 127 for a 909 crack?

**HiHat (`hihat\wav\`)**
- `01_closed_default_8ths.wav`, `02_open_default.wav`.
- Knob sweeps: `03_closed_tone_…`, `04_open_noise_…`, `05_closed_tune_…` (listen for the loudness rise with Tune), `06_decay_…`.
- Velocity: `07_accent127_vel_1_.75_.5_.25_then_accent0.wav`. Ghost notes barely drop in level; this is the major velocity issue.
- Rolls: `08_restrike_roll_open.wav`, `09_restrike_roll_closed_32nds.wav`.
- `10_groove_closed_plus_open_choked.wav` (the choke is simulated).
- Listen for: is the default too dark for an 808? The reviewer suggests Tone ≈ 100.

**Clap (`clap\out\`; WAVs sit in out\, not wav\)**
- Presets: `clap_default.wav`, `clap_tight_808.wav`, `clap_909ish.wav`, `clap_big_room.wav`, `clap_dark.wav`, `clap_bright_accent.wav`, `clap_snap_spread0.wav`.
- Re-strike: `clap_restrike_roll.wav`, `clap_restrike_5_20_80ms.wav`.
- `clap_pitch_sweep.wav`, `clap_groove_120bpm.wav`.
- Listen for: hiss above 10 kHz at default and bright settings. `clap_dark.wav` (air term 0) is closest to the intended band-pass character. Also listen for how late the main hit feels at high Spread.
- Plots: `clap\out\clap_waveforms.png`, `clap\out\clap_spectra.png`.

**Tom (`tom\wav\`)**
- Presets: `tom_default.wav`, `tom_high_tom.wav`, `tom_floor_tom.wav`, `tom_909_boom.wav`, `tom_clean_sine.wav`, `tom_electro_zap.wav`.
- Extremes: `tom_min_all.wav`, `tom_max_all.wav`. max_all and electro_zap hit the clamp.
- Re-strike: `tom_restrike_roll.wav`, `tom_fill_3pads.wav`.
- `tom_velocity_20_50_80_110_127.wav`. Velocity changes timbre only, not level.
- Listen for: whether the ranges should be frozen (Tune 45..413 Hz, Sweep +2.5 octaves).
