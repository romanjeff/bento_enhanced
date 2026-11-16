# SPDX-License-Identifier: GPL-3.0-or-later
"""P3 independent host golden model (docs/plan-p3-fm-bits.md 6.3): numpy, float64, shares no C code.

    python p3_golden.py          # self-test (no image, no build dir)

Written from the plan text only (4.6, 4.7, OD6, OD11, OD4), before and without reading the DSP sources. Q2's part is the
Bit Depth quantiser, Q3's the shapes below the saw (after the Bit Depth notes), Q5's the sub waves as modulators (OD10),
the through-zero FM phase, the sync roles with the slave reset rule (4.3, OD5) and the Bessel check; Q6's FM 1>2 (OD15's modulator
grid, buffer B, the two-oscillator voice, the DC-drift and aliasing checks: last). Q2b extends the quantiser with OD12's dc (written
from OD12's decision text, quoted below; the kernel's an_crush was already built, so this part is not "before the DSP").

Bit Depth (Q2; 4.7, OD11 = (b), Jeff 2026-11-15): a true b-bit two's-complement grid on the oscillator's 96 kHz block s,
    q = clamp(rint(x * S), -S, S - 1) / S,   S = 2^(b-1): 2048, 128, 8 for b = 12, 8, 4
- Exactly 2^b levels k/S, k in -S..S-1. The top clips at (S-1)/S, the bottom at -1 (asymmetric by one step), so BLEP
  overshoot above +1 lands on (S-1)/S and 1.0 is never an output.
- rint is round half to even, as VRINTX.F32 in the default FPSCR mode (RN). numpy's rint does the same (I: IEEE 754
  roundTiesToEven in both). A tie k + 0.5 goes to the even k.
- Setting -> b (4.7, OD4: out-of-range plays `4`, as the widget shows): 0 Full, 1 12, 2 8, 3 or any other value 4.

Tolerances, and why (M: arithmetic):
- **Same float32 input -> bit exact (tolerance 0).** S is a power of two, so x * S and k * (1/S) (or k / S) are exact in
  float32 for every |x| < 2^100; rint and the clamp are exact; every k/S (|k| <= 2048) fits a 24-bit mantissa. So the
  float64 model on the kernel's own float32 s equals the M7's float32 q in every bit, including the sign of zero
  (rint(-0.3) = -0.0, and -0.0 / S = -0.0). Compare with `crush_bits` (uint32 patterns) for byte-for-byte checks;
  `==` treats -0.0 and 0.0 alike.
- **Input from another model (e.g. the golden sine against the kernel's sinq, max 1e-5 in 6.3) -> one step at ties.**
  An input error e can move x * S across a half step when it is within e * S of k + 0.5, and then q differs by exactly
  1/S (0.125 at 4 bits): no float tolerance on q is meaningful. `compare` allows one step only on those samples
  (`near_tie`, margin e * S) and is exact elsewhere. The allowance is not a loosened tolerance: it is the set of samples
  whose correct level is undefined at that input precision, and `compare` reports how many there were.
- Compare at the post-pass point (s before SMOOTH_APPLY, the value B is written from: BD1). After Level the gain multiply
  rounds again, so a post-Level check needs the same gain applied to q and its own one-ulp float32 bound.
- Non-finite input raises: NaN through VCMP/VSEL clamps depends on the operand order, which the plan does not fix, and
  the kernel's NaN guards (test_analog T1) keep NaN out of s.

Mutations (6.6, Q2): `mutants` gives the wrong quantisers the plan names: mid-tread without the clamp (OD11 (a)), the
top clamped at S, plus round half away from zero (C `roundf`), truncation and a symmetric clip. `discriminates` counts
the samples where a case's block separates each one from the correct q; a crush case is useful only if its count is > 0.
To catch the missing bottom clamp a block needs x * S < -S - 0.5 strictly (at exactly -S - 0.5 the tie goes to the even
-S, which is in range); to catch the top at S it needs x * S >= S - 0.5.

Bit Depth about the pulse's DC (Q2b; OD12 = (b), Jeff 2026-11-15: "crush s + dc, then subtract dc, so the DC-free pulse
sits on the grid's range; B stays DC-free; dc follows a Shape ramp part by part"). A spec change, so the model changes:
    q = clamp(rint((x + dc) * S), -S, S - 1) / S - dc
- dc is the kernel's DC term of the part the sample belongs to (the pulse's 2 w - 1, w its width; 0 for the saw, the square
  and below the saw). It is a float32 value the caller supplies, a scalar or one per sample (a ramp's parts); the model
  does not derive it from Shape (4.7 / OD12 do not fix the kernel's arithmetic; the caller pins that).
- x + dc and the final - dc are float32 operations, each rounded once (I: the M7 holds s and dc as float32). The model adds
  in float64 and rounds to float32: for two float32 operands that equals the float32 add / subtract (double rounding is
  innocuous at 53 >= 2 * 24 + 2 bits, Figueroa 1995). (x + dc) * S, the clamp and the level / S stay exact (S a power of 2).
- dc 0 is Q2's crush in every bit, sign of zero included: the add of a zero dc and the subtract of it are the identity (a
  -0.0 input stays -0.0; plain float arithmetic would give -0.0 + 0.0 = +0.0). So every Q2 result stands where dc is 0.
- For one dc: q + dc takes at most 2^b values, exactly 2^b on a sweep of x + dc over [-1.3, 1.3]; q itself is 2^b values
  dc off the grid (`levels(q, b, dc)` takes dc back off, within the subtract's rounding: |q + dc - k/S| <= 2^-24 since
  |k/S - dc| < 2). The clip acts on x + dc: (S-1)/S - dc at the top, -1 - dc at the bottom.
- Mutations (6.6, Q2b): `mutants(x, b, dc)` adds OD12's wrong forms: dc not added (Q2's crush on the DC-free pulse,
  OD12 (a)), dc added but not subtracted, the sign of dc flipped (subtracted first, added after).

Shapes 0..48 (Q3; 4.6, OD6 as recommended): written before Q3's kernel, from the plan and test_analog's BE4.40 model().
- Map: kcoord(Shape) -> k, linear between the knots (0, -1) (2, -1) (22, -0.5) (26, -0.5) (45, 0) (48, 0): plateaus sine
  0..2, triangle 22..26, saw 45..48. tri_weight(k) = tw: 2k + 2 on [-1, -0.5], -2k above. weights(Shape) = (w_sine,
  w_tri, w_saw) = (1 - tw, tw, 0) or (0, tw, 1 - tw). Above 48 raises (BE4.40's map, test_analog model()); below 0 is sine.
- Waves on p in [0, 1): saw 2p - 1 (S: BE4.40's, and M: render(.., 47, law="f32") equals BE4.40's an_render at k = 0
  within 1.1e-7, end phase and MASTER event bytes identical, n = 128, 4 increments x 2 phases, scratch p3/q3/bbsaw.py);
  triangle 1 - 4|p - 0.5| (-1 at p = 0, +1 at 0.5: the corners 4.6 names); sine -cos(2 pi p), in phase with the
  triangle. **The triangle's and sine's sign and phase are this model's choice (I): 4.6 fixes the corners, not the
  polarity. Q3's kernel must match it, or a reviewed edit flips both here; never a fit.**
- render(inc, shape, n, phase, delayed, law, bl) -> Render(y, phase, delayed, ev, wraps, halves): one free-running part
  (Shape held), the kernel's delayed form: y[i] is the wave at the phase at the start of sample i plus the corrections,
  y[0] = delayed. Edges where p_start < edge <= p_end, fraction fr = (edge - p_start) / inc. The wrap: the saw step -2
  w_saw (2-point polyBLEP, so scaled by 1 - tw) and the triangle trough's kink +8 tw inc per sample (2-point polyBLAMP);
  p = 0.5: kink -8 tw inc. Both are the unit triangle kernel's residuals: BLEP +J(1-fr)^2/2 before, -J fr^2/2 after;
  BLAMP K(1-fr)^3/6 before, K fr^3/6 after. The sine gets nothing. ev: the MASTER byte, round(fr * 254) at each wrap.
  wraps, halves: crossings of p = 1 and 0.5 (6.5's edge counts). inc: not > 0 -> 0, above INC_MAX = f32(0.45) -> INC_MAX.
  steady() renders after a warm-up so every kept sample has both halves of its corrections.
- law "f64" (asked for: per-sample float64 p += inc, p -= 1) or "f32" (the same steps rounded to float32: the kernel's
  phase state, M above). Waves and corrections are float64 in both.

Tolerances, and why:
- **Sample by sample, use law "f32": tol_time() = TOL_F32 (1e-5, 6.3) + w_sine * sinq_err.** On the same phase law the
  only differences are float32 rounding in the kernel (M: 1.1e-7 on the BE4.40 saw) and the kernel's polynomial sine
  (4.6). sinq_err is the polynomial's stated max error on [0, 1), from its design (Q3 states it), not a measured residual;
  the THD check then has to agree with it. Expected-change set (6.2: Shape < 45), so no BE4.40 reference exists there.
- **Not law "f64" against the kernel past a few samples.** The float32 add rounds the same way every sample within a
  binade, so the phase error is a bias that grows with n (M, phase_drift): 1.3e-6 / 1.2e-5 / 5.1e-5 at 256 / 2048 / 8192
  samples for note 60 (inc 0.002725), 2.4e-5 at 2048 for inc 0.1. Times the slope near a BLEP'd wrap (about 2 / inc per
  unit phase: 734 at note 60) that is about 1e-3 at one 2N block. tol_time(law="f64") gives that bound, for the record.
- **The f64 law is the exact reference.** M: steady(law="f64") equals reference() (the analytic blend, triangle and saw
  parts x sinc^2(h inc), every alias folded, h <= 2^20) within 2.6e-12, for Shapes 0, 1, 12, 23, 24, 36, 45.5, 47 at 11,
  301 and 1501 cycles in 4096 samples (inc 0.0027 / 0.073 / 0.366). That proves the BLEP/BLAMP signs, scales, fractions
  and the one-sample delay at once: the 2-point corrections sample the triangle-kernel-filtered wave exactly. Mutation
  sizes against a 1e-5 tolerance (M, note 60, 2048 samples, scratch p3/q3/mut.py): BLAMP sign flipped 3.3e-3 / 6.6e-3 /
  3.1e-3 at Shapes 12 / 24 / 36, BLAMP missing 1.6e-3 / 3.3e-3 / 1.6e-3, triangle plateau 22..26 dropped 9.6e-3 at 12 and
  8.9e-2 at 36; a saw BLEP not scaled by 1 - tw is a step of up to tw (I: arithmetic, 0.5 at Shape 36).
- **Spectra: spectrum(x, cycles, phase0)**, coherent (coherent_inc: n a power of two, cycles odd, so inc is an exact float32
  and the harmonics' aliases fall between the harmonic bins), no window. harm: complex X_h referred to phase 0; off_db:
  off-harmonic energy against the fundamental; thd_db. M (golden, f64, 4096 samples), off_db band-limited / naive:
  Shape 12 -95.1 / -82.7, 24 -88.2 / -75.7, 36 -42.5 / -26.4, 47 -38.9 / -22.7 at 11 cycles; 12 -48.7 / -38.3,
  24 -41.7 / -31.4, 36 -26.7 / -11.8, 47 -23.1 / -8.1 at 301 cycles. Shape 0: THD and off_db below -300 dB (f64), below
  -120 dB with the f32 phase law (self-test bounds). So the golden's own figures are far below the 6.3 Bessel need of
  about -55 dB: **a Shape-0 kernel render's thd_db and off_db measure sinq (plus float32), and the 6.3 Bessel tolerance
  comes from that figure**; for the triangle and blends, the kernel's off_db should match the golden's to about +-0.1 dB
  (I: the residual difference is the sample error <= 1e-5, about -100 dB against a 1.0 fundamental).
- Not modelled: sync slave resets and the an_reshape value-jump correction between AN_RAMP parts (4.6: value jump only;
  chain render() calls with the returned state for a held Shape per part), FM (Q5), Shape > 48. (Q5's fm_render below
  covers sync, FM and Shape > 48; Q3's render stays as written.)

Through-zero linear FM (Q5; 4.3, OD5, FD3, OD10, 6.3, 6.5, 6.6): written from the plan text, Q3's model and test_analog's
BE4.40 model() / k_of / dc_of, before and without reading any FM implementation (no DSP C/C++ read for it).
- Index (FD3): fm_index(v) = 8 (v/1000)^2, v clamped 0..1000; index_ramp(v0, v1, N, off, n): I linear from I(v0) at sample
  0 to I(v1) at sample N (so a part continues the block's ramp, and a held next block continues it with no step).
- Modulator: sub_wave / sub_mod, the sub's sine, triangle, saw, rev saw and square (DC-free, OD10), float64 phase, polarity
  and origin this model's (I). Noise is not modelled. For sample-by-sample checks feed the kernel's own A buffer instead
  (Q4: A = stock's sub up to dc, worst 0): Q5 checks the FM kernel given A, Q4 checked A.
- Increments: increments(inc, [(I, a), ...], law, fma, clamp): di = inc + sum I_j a_j, a_j in increment units (A =
  inc_sub (y - dc), Q6's B = inc1 s); fm_increments(inc, I, m, inc_sub): the task's inc + kin m, kin = I inc_sub. Then the
  clamp |di| <= INC_MAX = f32(0.45), NaN -> 0. law "f32": operands float32, each term folded in order, fma (one rounding,
  exact: TwoSum fixes float64's double rounding) or not; a statement of the kernel's arithmetic, never a fit.
- Carrier: FmWave(k), any k in [-1, 1]: Q3's blend below 0, BE4.40's saw / square / pulse above (k_of_shape(Shape) maps
  0..127). Sine -1, triangle -0.5, saw 0, square 0.5, pulse 1 (w 0.08).
- fm_render(di, k | shape, phase, delayed, role, sync, law, fma, width, bl, mutant, inc0) -> FmRender(y, phase, delayed,
  ev, ev_near, edges, turns): the phase moves linearly by di[i] within sample i; every crossing of a wrap, the pulse edge
  or the triangle peak gets a 2-point polyBLEP (J = s dV) and polyBLAMP (K = |di| dS), s the direction, fraction
  (pos - p) / di: forward p < pos <= end, backward end < pos <= p (the mirror). MASTER writes a byte at every wrap either
  way (OD5). SLAVE: a byte resets the phase at at = e / 254 to 0 moving forward or to 1- moving backward (OD5, one edge),
  di == 0 no reset, with the value BLEP and Q3's slope BLAMP (OD14). The delayed form and Q3's conventions throughout;
  steady_fm and reference_k (any k, either direction) are the constant-increment reference.
- Sideband check: fm_sine_time / fm_sine_expect (closed form and Jacobi-Anger for sine -> sine, exact for the discrete
  accumulator: beta = I (w/2) / sin(w/2), the modulator half a sample early), bessel_check(y, cc, cm, I, pc0, qm0, phi,
  eps_c, eps_m, eps32, walk) with bessel_tol, bessel_j (no scipy in the venv).

Tolerances, and why (M: the self-test; I where marked):
- **f64 is exact** where it can be checked exactly: at a constant increment (either sign) steady_fm equals reference_k
  within 1e-9 for k = -1 .. 1 at 11 and 301 cycles in 4096 (the corrections sample the triangle-filtered wave exactly, so
  the backward signs and fractions are right); with no modulation it is Q3's render bit for bit (both laws); the sine
  carrier under FM equals the closed form within 1e-10 at I = 1, 2 and 8 through zero, and the closed form's spectrum
  equals Jacobi-Anger within 1e-12. Varying di has no closed form for the corrected waves; there the checks are symmetries,
  exact in exact arithmetic (rounding only, 1e-9 / 1e-12): time reversal (render(-di[::-1]) from the end phase is the
  block backwards, through zero, every k) and reflection (a backward slave from p = the forward slave from 1 - p, times
  +1 for k in [-1, -0.5] and -1 for k in [0, 0.5], where wave(1 - p) = +-wave(p)).
- **Against the kernel sample by sample: law "f32", the same di bit for bit** (feed the kernel's A and indices, state its
  fma), tolerance tol_time's (TOL_F32 + w_sine SINQ_ERR; for an FmWave k < 0 use weights' w_sine, 0 for k >= 0). Assert
  the end phase equal first: if it is not, di's arithmetic differs (fix the statement, not the tolerance). The f64 law
  drifts from float32 (Q3's phase_drift), so not f64 past a few samples. Under FM the per-sample corrections use the
  sample's own di (the 2-point form; with di changing every sample it is an approximation of the band-limited wave of
  order the per-sample change of di, I: the kernel's form as 4.3 states it, and what the golden must match).
- **Events: bytes equal, except ev_near samples (+-1)**: the fraction's float32 rounding can carry fr 254 + 0.5 across an
  integer there (margin 1e-4 of a byte).
- **Edge counts (6.5)**: edges counts crossings by kind and direction, the wrap always (the master needs its fraction even
  where nothing jumps), the pulse edge only when blend > 0, the peak only when tw > 0. Which of them cost a VDIV is the
  kernel's statement; resets cost none (at is a byte).
- **The double edge (6.6, OD5's p = 0) is output-equivalent here (M):** both corrections sit at the same fraction and sum
  to wave(1-) - wave(p), value and slope, so the output matches the one-edge rule up to rounding, except the sample after
  a byte-254 backward reset (nothing is left of the sample, so the mutant's wrap lands in the next one with that sample's
  di). The mutant's trace is one extra backward wrap per backward reset: 6.5's VDIV / edge count must be the red test, not
  the output.
- **Bessel (sine -> sine, 6.3), derived, not fitted:** every bin, |Y_meas - Y_expect| <= bessel_tol = eps_c + 1.01 (pi beta
  eps_m + 2 pi walk) + eps32 (Y = fft / n). The bin bound is the sample bound (|(1/n) sum x e| <= max |x|); eps_c the
  carrier's shaper error; pi beta eps_m the modulator's error integrated into the carrier phase (zero-mean periodic error,
  Dirichlet + Parseval: <= I eps_m / 2 cycles, I: its harmonics below the modulator's Nyquist); walk the float32 phase law's
  own error (fm_phase_walk on the block's di: ~1e-6 cycles over 8192 samples, M); eps32 6.3's 1e-5. With SINQ_ERR on both:
  3.48e-4 a bin at I = 1 (one-sided 7.0e-4: 0.31 dB on the J3 bin at ratio 1, under 6.3's 0.5 dB) and 6.05e-4 at I = 2
  (M). Against the worst-shaped errors of that size (a 3rd-harmonic and a lobed one) the check uses 5-8 % of it (M), so it
  is conservative; it still fails an index 2 % off, the modulator one sample late, and the linear law (M). At Sub FM's
  sine (stock's table, not sinq) pass that table's stated error as eps_m.

FM 1>2, OSC1 -> OSC2 (Q6; 4.1, 4.4 E3 (a), 4.5, 4.7 BD1, OD5, OD15 = (b) Jeff 2026-11-16, 6.3, 6.6): written from the plan text,
this file's Q2b / Q5 parts and test_analog's harness API (fm_golden, fm_capture, FmBench), before and without reading any
FM 1>2 implementation (no DSP C/C++ read for it). Stock's FIR taps are stock data read off the 1.6.8 image (S, below).
- Modulator (BD1, OD15): B[i] = f32(f32(inc1) q_B[i]), q_B = crush_mod(OSC1's pre-Level s, its Bit Depth, its part dc)
  = clamp(rint((s + dc) S), -S, S) / S - dc (the top at S: B's grid only; the audio keeps crush()); s itself at Full. inc1
  OSC1's base increment (kin = I inc1, 4.1), not its FM'd di1. Level never enters (E3 (a): OSC1 at Level 0 modulates the
  same). b_buffer; mutants after_level, before_crush, audio_grid (OD15 not applied), b_late, kin_fm.
- Index (FD3, Q5 as built): an_index32(v) = f32(f32(v^2) f32(8e-6)); index_line(v0, v1, N) = (ia, dia), dia = f32(f32(I1 - I0)
  / N) or 0; line_values: I_j = f32(ia + dia j) over the block frame j (m12's ramp the same rule as msub's, I: 4.1).
- Voice: fm12_voice(n, inc1, k1, inc2, k2, a, is1, is2, i12, bits, state1, state2, sync, level1, off, law, fma, widths, dc,
  order, mutant) -> Voice12(y1, q1, B, di1, r1, di2, y2, q2, r2): OSC1 = inc1 + I_s1 A (master when sync), its audio crush
  q1, B from y1, OSC2 = inc2 + I_s2 A + I_12 B folded in `order` ("ab" = 4.3's text), slave on OSC1's bytes when sync
  (OD5), its audio crush q2. Chained calls with off and the returned states are one call (state is (phase, delayed)).
  "no_b" mutant: OSC2 ignores B (useB false).
- Drift (6.3): drift(di, inc, window) {L, travel, expect, cycles, cents, ok |cycles| <= DRIFT_BIN = 1}; whole_cycles(di_mod)
  a window of whole forward modulator cycles; crush_drift(s, b, inc1, I12, inc2, dc, window) {mod, grid} in cents: the
  crush's own share of OSC2's mean frequency, OD15's B and the audio grid on B (the mutant).
- Aliasing (6.3): STOCK_FIR, decimate(y, taps) (circular, one output per two inputs), fir_response, alias_db(y, cycles)
  (off-harmonic / harmonic energy, plan-analog-fm 4.3's metric), alias_case(route, cc, cm, I, k_c, k_m, wave, fs, n48).
- Bessel (6.3): fm12_bessel(y2, cc, cm, I, pc0, qm0, walk): OSC1 at Shape 0 from qm0 plays -cos 2 pi (qm0 + i inc1), so B is
  fm_sine_time's modulator with phi = pi; eps_m = FM12_EPS_M = SINQ_ERR + TOL_F32 (OSC1's stated sample error).

Tolerances, and why (M: the self-test, scratch p3/q6g; I where marked):
- **B: bit for bit (tolerance 0).** S a power of two, the product of two float32 values exact in float64, one rounding each
  (M: crush_mod equals numpy float32 arithmetic in every bit on 65,536 random x and kernel-range dc). The form
  f32(f32(inc1) q) is a statement of the kernel's arithmetic: if the kernel folds inc1 into the crush (inc1 g / S - inc1
  dc, or one VFMA), restate it here from the disassembly; never a tolerance.
- **OSC2 sample by sample: Q5's rules unchanged** (law "f32", fma, the same di bit for bit: feed the kernel's own A and B and
  lines; end phase equal first; fm_tol by OSC2's k and role; master bytes +-1 on ev_near). The fold order of the two terms
  ("ab" / "ba") changes di by at most 2^-24 (M) and is a statement: read it off the kernel.
- **Bessel, sine -> sine (FM 1>2):** bessel_tol with FM12_EPS_M: 3.80e-4 a bin at I = 1 (0.34 dB on the J3 bin at ratio 1,
  under 6.3's 0.5 dB), 6.68e-4 at I = 2. The golden's own f32 renders use 0.07-0.4 % of it (ratio 1 at I = 1 and
  an_index32(354) = 1.0025280, 3:1 at I = 2, 1:3 at I = 1); B one sample late, the index 2 % off and y1 after a Level of
  0.98 fail by 17-31 x it. OSC1 at Full only: a crushed modulator has no Bessel law.
- **DC drift (6.3, 100 blocks, I = 8 through zero):** travel over whole modulator cycles within DRIFT_BIN (1 cycle, Q5's
  Square-sub precedent): Sub FM by a DC-free Square at PW 30 %, a sine and a triangle sub pass, the raw Square fails (-106
  cycles); FM 1>2 from a free OSC1 at Shapes 48 / 88 / 127 x Full / 12 / 8 / 4 passes (worst 0.03 cycles). The audio grid on
  B (OD15 not applied) fails the bin at 8 and 4 bits on the square (-2.1 / -33.7 cycles) but not at 12 (-0.13: under a
  bin over 100 blocks), so 12 bits is seen by crush_drift (-3.3 cents against CRUSH_DRIFT_CENTS 1) and by B bit for bit.
  Over an arbitrary window a zero-mean modulator's partial cycle alone moves the travel by up to I max|m| / 2 cycles
  (|integral of a zero-mean wave over a part cycle| <= P max|m| / 2), so use whole cycles or a coherent block.
- **crush_drift bound: CRUSH_DRIFT_CENTS = 1 cent at 12 and 8 bits, a free OSC1** (M, sweep of Shapes 0..127 x notes
  36..108, I12 = 8, OSC1 = OSC2: worst +0.25 / +0.32 cents). Measured, not derived: the only derived bound (|mean
  rounding| <= 1 / 2S) is 702 cents at 4 bits, useless. The audio-grid mutant: -3.4 / -55 / -1190 cents on the square,
  -2.5 / -40 / -790 at Shape 100, -0.5 / -8 / -136 at 127, -0.04 / -3 / -208 on the sine (12 / 8 / 4 bits).
- **Aliasing (6.3): 96 kHz + stock's FIR + decimation against the same voice rendered at 48 kHz** (the old design; its
  absolute figures, plan-analog-fm 4.3, had an unrecorded modulator, so the comparison is same-case): M at n48 16384, I = 4
  ratio 1 unless noted, dB off/harmonic 48k -> 96k+FIR: FM 1>2 saw carrier, sine OSC1 436 Hz -29.8 -> -37.4, 1731 Hz -22.4
  -> -27.6; saw OSC1 -25.1 -> -31.3, -18.0 -> -26.4; I = 8 -15.3 -> -26.1; square / square 436 Hz I = 8 -25.7 -> -34.8;
  sine / sine 1731 Hz -119.5 -> -130.1; Sub FM saw carrier, sine sub an octave down 436 Hz -30.1 -> -38.2, 1731 Hz -24.1 ->
  -31.6, I = 8 -15.7 -> -27.1; sine carrier -103.7 -> -114.3. 5.2 to 11.3 dB below; the self-test asserts > 3 dB at n48
  8192. Plain decimation (no FIR) is worse than 48 kHz (-20.2 against -29.8): the FIR is load-bearing.
- **STOCK_FIR (S):** 11 symmetric taps, DC gain 0.9511 (-0.44 dB), |H| -2.2 dB at 20 kHz, -10.0 at 24, -27.8 at 28, about
  -20 from 32 to 48 kHz: a weak stopband (I: the old plan's "the decimator partly removes its images" holds, partly).

Found in Q6 (for the plan; M unless marked):
- **OSC1's own Sub FM puts a real DC into B.** The sub is an octave (or two) below OSC1, so OSC1's FM spectrum has its
  sideband f1 - 2 f_sub at 0 Hz: the correct, band-limited y1 has a mean (M: +0.126 for a saw OSC1 at Sub FM 50 %, I = 2,
  sine sub), and FM 1>2 turns it into an OSC2 detune of I12 inc1 mean(y1): about +1200 cents at FM 1>2 100 % (M). Sweep
  (Shapes 0..127, notes 36..108, Sub FM I = 0.5 / 2 / 8 on OSC1): |mean(y1)| up to 0.37 (the square, note 108, I = 2), so
  with OSC1 = OSC2 the mean increment 1 + I12 mean(y1) reaches 0.63 (-800 cents) at FM 1>2 35 % (I12 = 1) and reverses
  sign at 100 %. Its sign follows the Shape and J_2(I). 4.5's "B is OSC1's
  audible signal, including OSC1's own FM" makes this the specified behaviour, but it is a far larger detune than the
  one OD15 removed. Options for Jeff (not this file's call): accept (FM character); a DC blocker on B (a one-pole high-pass
  per block, a few instructions a sample, B no longer bit for bit OSC1's crushed s); or B from OSC1 before its own Sub FM
  (not available: one render). The 6.3 drift check is therefore stated for a free OSC1; with OSC1's own FM the golden
  predicts the drift exactly (I12 sum(B)) and a test should compare against it, not against 0.
- **OD15 removes the grid's top-clip DC, not the rounding's own mean.** Where a wave's extreme falls between two levels the
  rounding has a steady mean: Shape 36 peaks at 0.474, which 4 bits rounds up to 0.5 on every cycle: +16.6 cents at note
  60, +30.0 at note 96 (FM 1>2 100 %, a free OSC1; a steady rate: +38.6 cycles over 400 blocks); with OSC1's own Sub FM
  (I = 2) its phase dwells on a few values: -139 cents at 4 bits, -3.1 at 8. No sample reaches the top there, so OD15
  changes nothing. 12 and 8 bits from a free OSC1 stay within 0.32 cents. So "no OSC2 detune at any depth" (OD15) holds
  for the top clip's term at every depth, and in total only at 12 and 8 bits from a free OSC1.
"""
import math, sys
import numpy as np

BITS = (12, 8, 4)                       # the crushed settings 1, 2, 3 (Full is 0)


def bits_of(setting):
    """Bit Depth setting value -> b, or None for Full. Out of range (hand-edited files, OD4) plays 4, as the widget."""
    return {0: None, 1: 12, 2: 8}.get(int(setting), 4)


def scale(b):
    """S = 2^(b-1): 2048, 128, 8."""
    if b not in BITS: raise ValueError(f"b must be one of {BITS}, not {b!r}")
    return float(1 << (b - 1))


def _x(x, f32):
    x = np.asarray(x, dtype=np.float64)
    if f32: x = x.astype(np.float32).astype(np.float64)        # the kernel's s is float32 (no-op on captured samples)
    if not np.all(np.isfinite(x)): raise ValueError("non-finite input: the quantiser's NaN/Inf result is unspecified")
    return x


def _f32(v): return np.asarray(v, dtype=np.float64).astype(np.float32).astype(np.float64)


def _dc(dc, shape):
    """dc as a float64 array of x's shape; it must be finite float32 values (the kernel's)."""
    d = np.broadcast_to(np.asarray(dc, dtype=np.float64), shape)
    if not np.all(np.isfinite(d)): raise ValueError("non-finite dc")
    if not np.array_equal(d, _f32(d)): raise ValueError("dc must be float32 values (the kernel's own)")
    return d


def _on(x, d, f32):
    """x + dc as the M7 adds it: one float32 rounding; a zero dc is the identity (sign of zero kept)."""
    y = x + d
    if f32: y = _f32(y)
    return np.where(d == 0, x, y)


def _grid(x, b, f32, dc, top):
    """clamp(rint((x + dc) S), -S, top) / S - dc, the add and the subtract one float32 rounding each, a zero dc the
    identity. top S - 1: the audio grid (OD11 (b)); top S: FM 1>2's modulator grid (OD15 (b))."""
    x = _x(x, f32)
    if b is None: return x.copy()
    S = scale(b); d = _dc(dc, x.shape)
    g = np.clip(np.rint(_on(x, d, f32) * S), -S, S - 1 if top == "audio" else S) / S
    out = g - d
    if f32: out = _f32(out)
    return np.where(d == 0, g, out)


def crush(x, b, f32=True, dc=0.0):
    """The crushed block (float64 array; every value is an exact float32). b None (Full) returns x unchanged (any dc).
    f32: round x to float32 first, as the M7 holds s; pass False only to study a float64 signal on its own.
    dc (Q2b, OD12): the part's DC term, a float32 scalar or one per sample: the grid acts on x + dc, and dc comes off after."""
    return _grid(x, b, f32, dc, "audio")


def crush_bits(x, b, dc=0.0):
    """crush() as the uint32 IEEE patterns of its float32 values: for byte-for-byte checks (sign of zero included)."""
    return crush(x, b, dc=dc).astype(np.float32).view(np.uint32)


def levels(q, b, dc=0.0):
    """Grid indices k = (q + dc) * S (int64). Raises if any value is off the grid (beyond the subtract's rounding when dc is
    not 0: |q + dc - k/S| <= 2^-24) or outside -S..S-1."""
    S = scale(b); q = np.asarray(q, dtype=np.float64); d = _dc(dc, q.shape)
    k = (q + d) * S; kr = np.rint(k)
    off = np.abs(k - kr) > np.where(d == 0, 0.0, 2.0 ** -24 * S)
    if np.any(off): raise AssertionError(f"{b}-bit: {int(np.sum(off))} values off the 1/{S:g} grid")
    if k.size and (kr.min() < -S or kr.max() > S - 1):
        raise AssertionError(f"{b}-bit: index range {kr.min():g}..{kr.max():g} outside {-S:g}..{S - 1:g}")
    return kr.astype(np.int64)


def check_levels(q, b, dc=0.0):
    """On-grid (about dc), in range, and at most 2^b distinct values; dc must be one value (a constant-dc part), since a
    ramp's parts each have their own 2^b. Returns the distinct-level count (of q itself)."""
    if np.ndim(dc): raise ValueError("check_levels: one dc (a constant-dc part)")
    levels(q, b, dc)
    n = int(np.unique(np.asarray(q, dtype=np.float64)).size)
    if n > 1 << b: raise AssertionError(f"{b}-bit: {n} levels > {1 << b}")
    return n


def check_clip(x, q, b, f32=True, dc=0.0):
    """The asymmetric clip of x + dc: every (x + dc) * S >= S - 0.5 gives (S-1)/S - dc, every one <= -S - 0.5 gives -1 - dc,
    and (q + dc) = +1.0 never appears. Returns (n_top, n_bottom), the clipped sample counts, so a case can assert it reached
    each side."""
    x = _x(x, f32); q = np.asarray(q, dtype=np.float64); S = scale(b); d = _dc(dc, x.shape)
    y = _on(x, d, f32); k = levels(q, b, d)
    top, bot = y * S >= S - 0.5, y * S <= -S - 0.5
    if np.any(k >= S): raise AssertionError(f"{b}-bit: +1.0 in the output (top clamped at S, not S - 1)")
    if not np.all(k[top] == S - 1): raise AssertionError(f"{b}-bit: top not clipped at (S-1)/S")
    if not np.all(k[bot] == -S): raise AssertionError(f"{b}-bit: bottom not clipped at -1")
    return int(top.sum()), int(bot.sum())


def near_tie(x, b, err):
    """Mask of samples whose x * S is within err * S of a half step: an input error <= err may change their level."""
    S = scale(b); t = np.asarray(x, dtype=np.float64) * S
    return np.abs(t - np.floor(t) - 0.5) <= err * S


def compare(got, x, b, err=0.0, dc=0.0):
    """got (the M7's crushed block) against crush(x, b, dc=dc). err = 0: x is the M7's own float32 s, so every sample must
    match exactly. err > 0: x comes from another model within err of s; samples near a tie (near_tie, of x + dc) may differ
    by exactly one step, all others exactly. Returns the number of tie samples that did differ; raises on any other
    mismatch."""
    got = np.asarray(got, dtype=np.float64); want = crush(x, b, f32=err == 0.0, dc=dc)
    if got.shape != want.shape: raise AssertionError(f"shape {got.shape} != {want.shape}")
    d = np.abs(levels(got, b, dc) - levels(want, b, dc))
    tie = near_tie(np.asarray(x, dtype=np.float64) + dc, b, err) if err > 0 else np.zeros(d.shape, bool)
    bad = (d > 1) | ((d == 1) & ~tie)
    if np.any(bad):
        i = int(np.flatnonzero(bad)[0])
        raise AssertionError(f"{b}-bit: {int(bad.sum())} samples differ, first [{i}] x={x[i]!r} got={got[i]!r} want={want[i]!r}")
    return int(np.sum(d == 1))


def mutants(x, b, dc=0.0):
    """The wrong quantisers 6.6 must catch (plus three plausible slips), each on the same float32 input; with a dc (Q2b,
    OD12) the Q2 forms act on x + dc as the correct one does, and OD12's own wrong forms are added."""
    x = _x(x, True); S = scale(b); d = _dc(dc, x.shape); y = _on(x, d, True); t = y * S
    off = lambda g: np.where(d == 0, g, _f32(g - d))
    away = np.sign(t) * np.floor(np.abs(t) + 0.5)                # C roundf: ties away from zero
    m = {
        "mid_tread_no_clamp": off(np.rint(t) / S),               # OD11 (a)
        "top_clamped_at_S": off(np.clip(np.rint(t), -S, S) / S),
        "half_away_from_zero": off(np.clip(away, -S, S - 1) / S),
        "truncate": off(np.clip(np.trunc(t), -S, S - 1) / S),
        "symmetric_clip": off(np.clip(np.rint(t), -(S - 1), S - 1) / S),
    }
    if np.any(d != 0):
        m["dc_not_added"] = crush(x, b)                          # OD12 (a): Q2's grid on the DC-free pulse
        m["dc_not_subtracted"] = np.clip(np.rint(t), -S, S - 1) / S
        m["dc_sign_flipped"] = crush(x, b, dc=-d)
    return m


def discriminates(x, b, dc=0.0):
    """{mutant: number of samples where it differs from crush(x, b, dc=dc)}: a crush case's block must score > 0 on each
    mutant it is meant to kill."""
    want = crush(x, b, dc=dc)
    return {k: int(np.sum(v != want)) for k, v in mutants(x, b, dc).items()}


def probe(b, dc=0.0):
    """A crafted float32 block for b: every tie at the bottom, middle and top of the range, -0.0, a negative value that
    rounds to -0.0, overshoot past +1 and below -1 (beyond the tie at -S - 0.5), and both exact ends. dc (Q2b): the same
    points for x + dc (the block is shifted by -dc; exact, so still every tie, for a dc of few bits such as -0.75; for a
    kernel dc of 24 bits the float32 shift moves each point by an ulp, so near-ties on both sides instead)."""
    S = scale(b)
    k = np.r_[-S - 3, -S - 1, -S - 0.5, -S, -S + 0.5, -2.5, -1.5, -0.5, -0.3, 0.0, 0.3, 0.5, 1.5, 2.5,
              S - 1.5, S - 1, S - 0.5, S, S + 0.5, S + 3]
    x = np.r_[k / S, -0.0, 1.06, -1.06]
    x = x.astype(np.float32).astype(np.float64)
    return x if dc == 0 else _f32(x - float(np.float32(dc)))


# ---- Q3: the shapes below the saw (4.6, OD6) -------------------------------------------------------------------------------
# Anchors and plateaus (OD6 as recommended, Jeff 2026-11-15): Shape value -> kernel coordinate k, linear between the knots.
SHAPE_SINE, SHAPE_TRI, SHAPE_SAW = 0, 24, 48
PLATEAUS = ((0, 2), (22, 26), (45, 48))          # sine, triangle, saw (one-sided below 48: 48..127 is BE4.40's)
_KNOTS_S = (0.0, 2.0, 22.0, 26.0, 45.0, 48.0)
_KNOTS_K = (-1.0, -1.0, -0.5, -0.5, 0.0, 0.0)
INC_MAX = float(np.float32(0.45))               # the kernel's increment clamp (test_analog model(): f32(0.45))
LAWS = ("f64", "f32")


def kcoord(shape):
    """Shape value (0..48, float: 1/512-modulated values too) -> kernel coordinate k: -1 sine, -0.5 triangle, 0 saw.
    Below 0 plays sine (I: as the V1 text, 6.7). Above 48 is BE4.40's map (test_analog model()), not this model: raises."""
    s = np.asarray(shape, dtype=np.float64)
    if not np.all(np.isfinite(s)): raise ValueError("non-finite Shape")
    if np.any(s > 48): raise ValueError("Shape > 48 is BE4.40's kernel (test_analog model()), not the Q3 model")
    k = np.interp(np.maximum(s, 0.0), _KNOTS_S, _KNOTS_K)
    return float(k) if k.ndim == 0 else k


def tri_weight(k):
    """tw: 2k + 2 on [-1, -0.5] (sine -> triangle), -2k on (-0.5, 0] (triangle -> saw). 1 at k = -0.5, 0 at -1 and 0."""
    k = np.asarray(k, dtype=np.float64)
    if np.any((k < -1) | (k > 0)): raise ValueError("k outside [-1, 0]")
    tw = np.where(k <= -0.5, 2 * k + 2, -2 * k)
    return float(tw) if tw.ndim == 0 else tw


def weights(shape):
    """(w_sine, w_tri, w_saw) for one Shape value: (1 - tw, tw, 0) for k <= -0.5, (0, tw, 1 - tw) above. They sum to 1."""
    k = kcoord(float(shape)); tw = tri_weight(k)
    return (1.0 - tw, tw, 0.0) if k <= -0.5 else (0.0, tw, 1.0 - tw)


# Conventions (S: test_analog model(), BE4.40's saw; I: sine and triangle, see the module notes): phase p in [0, 1), rising
# saw 2p - 1 that falls by 2 at the wrap, triangle -1 at p = 0 and +1 at 0.5 (its corners), sine -cos(2 pi p) in phase with
# the triangle. All three have mean 0 and peak 1.
def sine(p): return -np.cos(2 * np.pi * np.asarray(p, dtype=np.float64))
def tri(p): return 1.0 - 4.0 * np.abs(np.asarray(p, dtype=np.float64) - 0.5)
def saw(p): return 2.0 * np.asarray(p, dtype=np.float64) - 1.0


def naive(p, shape):
    """The naive (aliasing) blend at phase p (vectorised over p) for one Shape value."""
    ws, wt, wsaw = weights(shape)
    return ws * sine(p) + wt * tri(p) + wsaw * saw(p)


def _blep(fr, jump):
    """2-point polyBLEP for a step `jump` at fraction fr in (0, 1] of the sample (from its start): (c0, c1), added to the
    sample before the step (the delayed one, at distance fr) and the one after (at distance 1 - fr). The residual of a step
    filtered by the unit triangle kernel (width +-1 sample): +J (1-e)^2 / 2 before, -J (1-a)^2 / 2 after."""
    return 0.5 * jump * (1 - fr) ** 2, -0.5 * jump * fr ** 2


def _blamp(fr, kink):
    """2-point polyBLAMP for a slope change `kink` (output units per sample) at fraction fr: (c0, c1). The same kernel
    integrated once: +K (1 - |d|)^3 / 6 on both sides, d the sample's distance from the kink."""
    return kink * (1 - fr) ** 3 / 6, kink * fr ** 3 / 6


class Render(tuple):
    """(y, phase, delayed, ev, wraps, halves): y float64[n]; the end phase and the delayed sample (the state for the next
    call); ev uint8[n], 255 or the wrap fraction * 254 rounded (the MASTER event byte, test_analog model()); wraps and
    halves, the number of crossings of p = 1 and of p = 0.5 (the edges a VDIV count is checked against, 6.5)."""
    __slots__ = ()
    _f = ("y", "phase", "delayed", "ev", "wraps", "halves")
    def __new__(cls, *a): return tuple.__new__(cls, a)
    def __getattr__(self, k):
        try: return self[self._f.index(k)]
        except ValueError: raise AttributeError(k) from None


def clamp_inc(inc):
    """The kernel's increment rule: not > 0 (NaN too) -> 0 (the phase stops), above INC_MAX -> INC_MAX."""
    inc = float(inc)
    return 0.0 if not inc > 0 else min(inc, INC_MAX)


def render(inc, shape, n, phase=0.0, delayed=0.0, law="f64", bl=True):
    """One free-running render of n samples at the 96 kHz/2N domain's increment inc (cycles per sample), Shape 0..48 held
    for the call (one AN_RAMP part; chain calls with the returned phase and delayed). Output sample i is the wave at the
    phase at the start of sample i plus the corrections, one sample late as the kernel's delayed form: y[0] = delayed.
    law "f64": per-sample float64 p += inc, p -= 1 at the wrap. "f32": the same steps rounded to float32 (inc too), the
    kernel's phase state (see the tolerance notes); everything else is float64 in both. bl False: no BLEP/BLAMP (naive)."""
    if law not in LAWS: raise ValueError(f"law must be one of {LAWS}")
    ws, wt, wsaw = weights(shape)
    inc = clamp_inc(inc)
    r32 = (lambda v: float(np.float32(v))) if law == "f32" else (lambda v: v)
    inc = r32(inc); p = r32(float(phase) % 1.0); d = float(delayed)
    jump = -2.0 * wsaw                                           # the saw part falls by 2 at the wrap
    kink = 8.0 * wt * inc                                        # triangle slope +-4 per cycle: change 8 * inc per sample
    y = np.empty(n); ev = np.full(n, 255, np.uint8); wraps = halves = 0
    for i in range(n):
        c0 = c1 = 0.0
        end = r32(p + inc)
        if p < 0.5 <= end:                                       # triangle peak: slope +4 -> -4
            halves += 1
            if bl and kink:
                a, b = _blamp((0.5 - p) / inc, -kink); c0 += a; c1 += b
        if end >= 1.0:                                           # wrap: saw step, triangle trough (slope -4 -> +4)
            wraps += 1; fr = (1.0 - p) / inc
            ev[i] = max(0, min(254, int(fr * 254 + 0.5)))
            if bl and jump:
                a, b = _blep(fr, jump); c0 += a; c1 += b
            if bl and kink:
                a, b = _blamp(fr, kink); c0 += a; c1 += b
            end = r32(end - 1.0)
        y[i] = d + c0
        d = ws * float(sine(end)) + wt * float(tri(end)) + wsaw * float(saw(end)) + c1
        p = end
    return Render(y, p, d, ev, wraps, halves)


def phase_drift(inc, n, phase=0.0):
    """max |p_f64 - p_f32| over n samples (wrapped to [-0.5, 0.5)): how far the float64 law leaves the kernel's float32
    phase. It grows with n (the float32 add rounds the same way each sample in a binade, so it is a bias, not a walk)."""
    inc = clamp_inc(inc); i32 = float(np.float32(inc))
    p, q, worst = float(phase) % 1.0, float(np.float32(float(phase) % 1.0)), 0.0
    for _ in range(n):
        p += inc; p -= 1.0 if p >= 1.0 else 0.0
        q = float(np.float32(q + i32)); q = float(np.float32(q - 1.0)) if q >= 1.0 else q
        e = (p - q + 0.5) % 1.0 - 0.5; worst = max(worst, abs(e))
    return worst


TOL_F32 = 1e-5                                   # plan 6.3: float32 kernel vs this float64 model at the same phase law


def tol_time(shape, inc, n, law="f32", sinq_err=0.0, phase=0.0):
    """Sample-by-sample tolerance for the kernel against render(..., law):
    - TOL_F32 (6.3) for the kernel's float32 arithmetic on the same phase law;
    - + w_sine * sinq_err: the kernel's sine is a polynomial (4.6), this model's is exact. sinq_err is the polynomial's
      stated max error on [0, 1), a figure the Q3 code must state (and the THD check confirm), never a fitted residual;
    - law "f64" only: + phase_drift * (the wave's largest slope per unit phase, saw near the BLEP'd wrap ~ 2 / inc)."""
    ws, wt, wsaw = weights(shape); tol = TOL_F32 + ws * sinq_err
    if law == "f64":
        inc = clamp_inc(inc)
        slope = ws * 2 * np.pi + wt * 4 + wsaw * (2 + (2 / inc if inc else 0))
        tol += phase_drift(inc, n, phase) * slope
    return tol


# ---- spectra (coherent sampling: an integer number of cycles in the block) -------------------------------------------------
def coherent_inc(cycles, n):
    """inc = cycles / n, checked: n a power of two (so inc is an exact float32 and the f32 law can stay coherent) and
    gcd(cycles, n) = 1 (every sample a distinct phase, and aliases of the harmonics fall between the harmonic bins)."""
    import math
    if n < 8 or n & (n - 1): raise ValueError("n must be a power of two >= 8")
    if not 1 <= cycles < n // 2 or math.gcd(cycles, n) != 1: raise ValueError("cycles must be odd and below n / 2")
    inc = cycles / n
    if inc > INC_MAX: raise ValueError("cycles / n above the kernel's increment clamp")
    return inc


class Spectrum(tuple):
    """(dc, harm, harm_energy, off_energy, total, thd, thd_db, off_db). harm: complex one-sided amplitudes X_h, h = 1..H
    below Nyquist, referred to phase 0 (x = sum Re(X_h e^{i 2 pi h p})): -1 for the sine, -8/(pi^2 h^2) for the triangle's
    odd h, 2i/(pi h) for the saw. Energies are mean squares; dc^2 + harm_energy + off_energy = total (Parseval).
    thd = sqrt(sum_{h>=2} |X_h|^2) / |X_1|; off_db = 10 log10(off_energy / (|X_1|^2 / 2)): the off-harmonic energy
    (aliasing, noise) against the fundamental."""
    __slots__ = ()
    _f = ("dc", "harm", "harm_energy", "off_energy", "total", "thd", "thd_db", "off_db")
    def __new__(cls, *a): return tuple.__new__(cls, a)
    def __getattr__(self, k):
        try: return self[self._f.index(k)]
        except ValueError: raise AttributeError(k) from None


def _db(r): return -np.inf if r <= 0 else 20 * np.log10(r)


def spectrum(x, cycles, phase0=0.0):
    """Harmonic / off-harmonic split of a block holding exactly `cycles` periods (coherent_inc), no window. phase0: the
    phase of x[0], so harm is referred to phase 0 (with the delayed form, a render's y[0] is the wave at its start phase)."""
    x = np.asarray(x, dtype=np.float64); n = x.size
    coherent_inc(cycles, n)
    X = np.fft.rfft(x) / n                                       # X[k] = mean(x e^{-i 2 pi k j / n})
    H = (n // 2 - 1) // cycles
    h = np.arange(1, H + 1); bins = h * cycles
    harm = 2 * X[bins] * np.exp(-2j * np.pi * h * phase0)
    pw = 2 * np.abs(X) ** 2; pw[0] = abs(X[0]) ** 2; pw[n // 2] = abs(X[n // 2]) ** 2
    off = np.ones(pw.size, bool); off[0] = False; off[bins] = False
    harm_e, off_e, total = float(pw[bins].sum()), float(pw[off].sum()), float(np.mean(x * x))
    a1 = abs(harm[0]); thd = float(np.sqrt(np.sum(np.abs(harm[1:]) ** 2)) / a1) if a1 else np.inf
    off_db = 10 * np.log10(off_e / (a1 * a1 / 2)) if a1 and off_e > 0 else (-np.inf if a1 else np.inf)
    return Spectrum(float(X[0].real), harm, harm_e, off_e, total, thd, _db(thd), off_db)


def ideal(shape, h, inc=None):
    """Complex one-sided amplitudes X_h of the continuous blend (Spectrum's convention), h an int array >= 1. inc given: the
    band-limited model's spectrum, the triangle and saw parts times sinc^2(h inc) (the unit triangle kernel the 2-point
    BLEP/BLAMP samples exactly), the sine part unchanged (the kernel corrects nothing there)."""
    h = np.asarray(h, dtype=np.float64); ws, wt, wsaw = weights(shape)
    odd = (h % 2 == 1)
    S = np.where(h == 1, -1.0, 0.0) + 0j
    T = np.where(odd, -8 / (np.pi ** 2 * h ** 2), 0.0) + 0j
    W = 2j / (np.pi * h)
    g = 1.0 if inc is None else np.sinc(h * clamp_inc(inc)) ** 2
    return ws * S + (wt * T + wsaw * W) * g


def reference(shape, cycles, n, phase0=0.0, hmax=1 << 20):
    """The exact sampled band-limited block (what render(law="f64") must produce in its steady state), from ideal() with
    every harmonic up to hmax folded onto its alias bin: x_j = sum_h Re(X_h e^{i 2 pi h (phase0 + j cycles / n)}). The
    tail past hmax is below 1e-8 for inc >= 1/1024 (the saw's |X_h| sinc^2 ~ 2 / (pi^3 inc^2 h^3))."""
    inc = coherent_inc(cycles, n)
    h = np.arange(1, hmax + 1)
    G = ideal(shape, h, inc) * np.exp(2j * np.pi * h * phase0) * (n / 2)
    m = (h * cycles) % n
    Y = np.zeros(n, complex); np.add.at(Y, m, G); np.add.at(Y, (-m) % n, np.conj(G))
    return np.fft.ifft(Y).real


def steady(inc, shape, n, law="f64", phase=0.0, warm=None):
    """render() after `warm` samples (default n) from (phase, delayed = naive(phase)), so every sample of the returned
    block has both its BLEP/BLAMP halves. Returns (Render of the n kept samples, the phase of its first sample)."""
    warm = n if warm is None else warm
    pre = render(inc, shape, warm, phase, float(naive(phase, shape)), law)
    return render(inc, shape, n, pre.phase, pre.delayed, law), pre.phase


# ---- Q5: through-zero linear FM (4.3, OD5, FD3, OD10; 6.3, 6.5, 6.6) ---------------------------------------------------
SINQ_ERR = 8.1e-5            # plan 4.6 (Q3, M in numpy before the kernel): sinq's stated max error, the design figure
FM_MUTANTS = ("no_mirror", "no_backward", "reset_p0", "rinc")
SUB_WAVES = ("sine", "triangle", "saw", "rev_saw", "square")
EV_NONE = 255


def fm_index(v, law="square"):
    """FD3: Sub FM / FM 1>2 amount v (0..1000, clamped as an_prop stores it) -> index I = 8 (v/1000)^2, 0..8.
    law "linear" (8 v/1000) is 6.6's mutant, for tests that must turn red on it."""
    v = np.clip(np.asarray(v, dtype=np.float64), 0, 1000) / 1000
    I = 8 * v * v if law == "square" else 8 * v
    return float(I) if I.ndim == 0 else I


def index_ramp(v0, v1, N, off=0, n=None, law="square"):
    """The index per sample of a 2N block whose amount moves v0 (last block's msub / m12) -> v1: linear in I from I(v0) at
    sample 0 to I(v1) at sample N (the next block's first), I_j = I0 + (I1 - I0) j / N, j = off .. off + n - 1 (a part
    of the block; n default N - off). So chunked parts continue one ramp and a held next block continues it without a step
    (the ramp's end convention is this model's, I: 4.3 says "linear from block start to block end over the whole 2N")."""
    n = N - off if n is None else n
    I0, I1 = fm_index(v0, law), fm_index(v1, law)
    return I0 + (I1 - I0) * (off + np.arange(n, dtype=np.float64)) / N


def sub_wave(wave, p, pw=0.5):
    """The sub's waveform as a modulator at phase p in [0, 1), unit amplitude, DC-free: sine sin 2 pi p, triangle
    1 - 4 |p - 1/2|, saw 2p - 1, rev_saw 1 - 2p, square (p < pw ? 1 : -1) - (2 pw - 1) (OD10: its DC removed). Noise is not
    modelled (a stride-stepped table: Q4's copy test covers it). Polarities and phase origins are this model's (I): stock's
    are in its sub core, which this file does not read. Sample-by-sample checks therefore feed the kernel's own A buffer
    (Q4 proved it equals stock's sub, worst 0 up to dc) as the modulator, not these; these serve the spectral, mean and
    Bessel checks, where only the shape class matters."""
    p = np.asarray(p, dtype=np.float64)
    if wave == "sine": m = np.sin(2 * np.pi * p)
    elif wave == "triangle": m = 1.0 - 4.0 * np.abs(p - 0.5)
    elif wave == "saw": m = 2.0 * p - 1.0
    elif wave == "rev_saw": m = 1.0 - 2.0 * p
    elif wave == "square": m = np.where(p < pw, 1.0, -1.0) - (2.0 * pw - 1.0)
    else: raise ValueError(f"wave must be one of {SUB_WAVES} (noise is not modelled), not {wave!r}")
    return m


def sub_mod(wave, inc_sub, n, phase=0.0, pw=0.5):
    """(m, end phase): n modulator samples, the sub's phase accumulated per sample in float64 (q += inc_sub, q -= 1 at the
    wrap), m[i] at the phase at the start of sample i."""
    q = float(phase) % 1.0; qs = np.empty(n)
    for i in range(n):
        qs[i] = q; q += inc_sub; q -= math.floor(q)
    return sub_wave(wave, qs, pw), q


def clamp_fm(di):
    """4.3's clamp: di in [-INC_MAX, INC_MAX] (INC_MAX = f32(0.45): one wrap a sample at most, as the 2-point BLEP needs);
    NaN -> 0 (the phase stops; the kernel's NaN guards keep NaN out, test_analog T1)."""
    d = np.asarray(di, dtype=np.float64)
    return np.clip(np.where(np.isnan(d), 0.0, d), -INC_MAX, INC_MAX)


def _fma32(a, b, c):
    """f32(a + b c) rounded once (VFMA), a, b, c float32 values (arrays). b c is exact in float64; a + b c rounds in float64,
    which can land on a float32 midpoint the exact sum is not on (double rounding): TwoSum's error term decides those."""
    a, b, c = (np.asarray(x, dtype=np.float64) for x in (a, b, c))
    p = b * c; s = a + p; bb = s - a; e = (a - (s - bb)) + (p - bb)
    r = s.astype(np.float32); rf = r.astype(np.float64)
    other = np.where(s > rf, np.nextafter(r, np.float32(np.inf)), np.nextafter(r, np.float32(-np.inf))).astype(np.float64)
    mid = (rf != s) & (e != 0) & ((rf + other) / 2 == s)
    return np.where(mid, np.where((e > 0) == (other > rf), other, rf), rf)


def increments(inc, terms=(), law="f64", fma=True, clamp=True):
    """Per-sample carrier increments di = inc + sum_j index_j * a_j (4.3: di = inc + ia a[i] + ib b[i]), a_j the modulator
    in increment units (A = inc_sub (y - dc), B = inc1 s), index_j the index I (scalar or per sample: index_ramp), then 4.3's
    clamp (clamp False: 6.6's "clamp missing" mutant). law "f64": float64. "f32": the kernel's form, every operand rounded to
    float32 first and each term folded in order, fma True as f32(d + i a) (one rounding: clang contracts at -O2, I) or False
    as f32(d + f32(i a)); the order and contraction are a statement about the kernel's arithmetic (read them off its
    disassembly), never a fit."""
    if law not in LAWS: raise ValueError(f"law must be one of {LAWS}")
    n = max([np.size(a) for _, a in terms] + [np.size(i) for i, _ in terms] + [1])
    if law == "f64":
        d = np.full(n, float(inc))
        for i, a in terms: d = d + np.asarray(i, dtype=np.float64) * np.asarray(a, dtype=np.float64)
    else:
        d = np.full(n, float(np.float32(inc)))
        for i, a in terms:
            i32, a32 = (np.broadcast_to(_f32(x), (n,)) for x in (i, a))
            d = _fma32(d, i32, a32) if fma else _f32(d + _f32(i32 * a32))
    return clamp_fm(d) if clamp else d


def fm_increments(inc, I, m, inc_sub, clamp=True):
    """The task's form, float64: inc_i = inc + kin m[i], kin = I inc_sub (I scalar or per sample), clamped."""
    d = float(inc) + np.asarray(I, dtype=np.float64) * float(inc_sub) * np.asarray(m, dtype=np.float64)
    return clamp_fm(d) if clamp else d


def k_of_shape(shape):
    """Shape 0..127 -> kernel coordinate k in [-1, 1]: kcoord below 48 (Q3), BE4.40's map above (test_analog k_of)."""
    s = float(shape)
    if not math.isfinite(s): raise ValueError("non-finite Shape")
    return kcoord(s) if s <= 48 else (s - 48) * (0.5 / 40) if s <= 88 else 0.5 + (min(s, 127.0) - 88) * (0.5 / 39)


class FmWave:
    """The carrier at kernel coordinate k, any k in [-1, 1] (clamped above 1). k < 0: Q3's blend ws sine + tw tri + wsaw saw.
    k >= 0: BE4.40's (test_analog model(); plan 4.7's dc law) (2p - 1)(1 - blend) + ((p < w ? 1 : -1) - dc) blend,
    blend = min(1, 2k), w = 0.5 - 0.84 (k - 0.5) above 0.5 else 0.5, dc = 2w - 1. law "f32" rounds blend / w / dc as BE4.40's
    model does (width= overrides w, e.g. with the kernel's own float32 width: an edge's position error moves its fraction by
    err / di). value(p) and slope(p) (per unit phase) read p = 0 as the right limit and p = 1.0 as the left limit.
    edges: (position in [0, 1), dV, dS, kind) with dV / dS the jumps of value / slope crossing it upward (right minus left):
    the wrap at 0 always (the master needs it even when nothing jumps), the triangle peak at 1/2 if tw > 0, the pulse edge
    at w if blend > 0."""
    def __init__(self, k, law="f64", width=None):
        k = float(k)
        if not math.isfinite(k): raise ValueError("non-finite k")
        r32 = (lambda v: float(np.float32(v))) if law == "f32" else (lambda v: v)
        self.k = k = max(-1.0, min(k, 1.0)); self.low = k < 0
        if self.low:
            tw = tri_weight(k); self.ws, self.tw, self.wsaw = (1.0 - tw, tw, 0.0) if k <= -0.5 else (0.0, tw, 1.0 - tw)
            self.edges = [(0.0, -2.0 * self.wsaw, 8.0 * self.tw, "wrap")]
            if self.tw: self.edges.append((0.5, 0.0, -8.0 * self.tw, "half"))
        else:
            k = r32(k); self.blend = b = r32(min(1.0, r32(2 * k)))
            w = r32(0.5 - r32(r32(k - 0.5) * r32(0.84))) if k > 0.5 else 0.5
            self.w = w = float(width) if width is not None else w
            self.dc = r32(r32(2 * w) - 1)
            self.edges = [(0.0, -2.0 * (1.0 - b) + 2.0 * b, 0.0, "wrap")]
            if b: self.edges.append((w, -2.0 * b, 0.0, "width"))

    def value(self, p):
        if self.low: return self.ws * float(sine(p)) + self.tw * float(tri(p)) + self.wsaw * float(saw(p))
        return (2.0 * p - 1.0) * (1.0 - self.blend) + ((1.0 if p < self.w else -1.0) - self.dc) * self.blend

    def slope(self, p):
        if self.low:
            return self.ws * 2 * math.pi * math.sin(2 * math.pi * p) + self.tw * (4.0 if p < 0.5 else -4.0) + self.wsaw * 2.0
        return 2.0 * (1.0 - self.blend)


class FmRender(tuple):
    """(y, phase, delayed, ev, ev_near, edges, turns): y float64[n] in the kernel's delayed form (y[0] = delayed); the end
    phase (1.0 = the left limit, after a backward reset or a backward wrap that rounds to 1) and delayed sample (the state
    for the next call); ev uint8[n] (MASTER: the wrap's byte, 255 elsewhere); ev_near bool[n] (a byte within float32 rounding
    of the next one: compare those +-1); edges {wrap_f, wrap_b, width_f, width_b, half_f, half_b, resets, resets_b}
    (crossings by kind and direction, 6.5's edge counts; resets_b the backward ones); turns (net unwrapped phase travel,
    not for a slave: its resets jump)."""
    __slots__ = ()
    _f = ("y", "phase", "delayed", "ev", "ev_near", "edges", "turns")
    def __new__(cls, *a): return tuple.__new__(cls, a)
    def __getattr__(self, k):
        try: return self[self._f.index(k)]
        except ValueError: raise AttributeError(k) from None


def fm_render(di, k=None, shape=None, phase=0.0, delayed=0.0, role="free", sync=None, law="f64", fma=True, width=None,
              bl=True, mutant=None, inc0=None):
    """One through-zero render of len(di) samples (4.3) on the carrier k (or Shape: k_of_shape), di the per-sample
    increments (increments() / fm_increments(); not clamped again here, so the clamp mutant shows). The phase moves
    linearly within a sample by di[i]; output sample i is the wave at the phase at the start of sample i plus the
    corrections, one sample late (y[0] = delayed), as Q3's render. At rest (di constant > 0, k < 0) it is Q3's render bit
    for bit.
    - Edges: every crossing of a wrap (integer phase), the pulse edge w or the triangle peak 1/2 inside the sample, at
      fraction fr = (pos - p) / di from the sample's start, in time order. Forward: p < pos <= end. Backward (di < 0):
      end < pos <= p (the mirror: p = 0 reads 0+, so leaving it backward crosses at fr = 0; landing on an edge from above
      has not crossed it). A 2-point polyBLEP J and polyBLAMP K per crossing, J = s dV (s the direction: a backward crossing
      steps the other way: the mirror), K = |di| dS (the time slope's change; the triangle's peak is -8 tw |di| either way).
    - role "master": ev[i] = round(fr 254) at each wrap, forward or backward (OD5; at most one a sample under the clamp).
      "slave": sync bytes (255 = none, else at = e / 254); a reset needs di != 0 (di == 0: none). It runs to at, steps
      to wave(0) moving forward or to wave(1-) moving backward (OD5: p = 1, one edge, then on backward from 1-), a BLEP of
      the value jump and a BLAMP of di (slope(target) - slope(p)) (Q3's reset BLAMP, OD14; 0 for k >= 0), then the rest
      of the sample from the target.
    law "f64": float64 phase (the exact reference). "f32": the kernel's phase state, di and phase rounded to float32 per step
    (p + di once; a partial step p + di len with fma as increments(); at = f32(e f32(1/254)), the rest f32(1 - at); the
    byte f32(fr 254 + 0.5)); waves and corrections float64 in both, as Q3.
    mutant (6.6): "no_mirror" (forward J and K on backward crossings), "no_backward" (no correction on them), "reset_p0"
    (a backward reset to p = 0: the double edge), "rinc" (fractions from the block's base increment inc0: E2's rinc)."""
    if law not in LAWS: raise ValueError(f"law must be one of {LAWS}")
    if role not in ("free", "master", "slave"): raise ValueError("role must be free, master or slave")
    if mutant not in (None,) + FM_MUTANTS: raise ValueError(f"mutant must be one of {FM_MUTANTS}")
    if (k is None) == (shape is None): raise ValueError("give k or shape")
    wv = FmWave(k if k is not None else k_of_shape(shape), law, width)
    f32law = law == "f32"
    r32 = (lambda v: float(np.float32(v))) if f32law else (lambda v: v)
    di = np.asarray(di, dtype=np.float64); n = di.size
    if f32law: di = _f32(di)
    if role == "slave":
        sync = np.asarray(sync, dtype=np.int64)
        if sync.size != n: raise ValueError("sync must hold one byte per sample")
    rinc = 1.0 / float(inc0) if mutant == "rinc" else None
    p = 1.0 if float(phase) == 1.0 else r32(float(phase) % 1.0); p0 = p; d = float(delayed)
    y = np.empty(n); ev = np.full(n, EV_NONE, np.uint8); near = np.zeros(n, bool)
    E = dict(wrap_f=0, wrap_b=0, width_f=0, width_b=0, half_f=0, half_b=0, resets=0, resets_b=0)
    third = r32(1 / 254)

    def adv(ps, x, length):
        if length == 1.0: return r32(ps + x)
        if not f32law: return ps + x * length
        return float(_fma32(ps, x, length)) if fma else r32(ps + r32(x * length))

    def add(c, fr, J, K):
        if J:
            a, b = _blep(fr, J); c[0] += a; c[1] += b
        if K:
            a, b = _blamp(fr, K); c[0] += a; c[1] += b

    def part(ps, x, t0, length, c, i, rec):
        if x == 0 or length <= 0: return ps
        end = adv(ps, x, length); s = 1 if x > 0 else -1
        lo, hi = min(ps, end), max(ps, end); hits = []
        for e, dV, dS, kind in wv.edges:
            for m in range(math.floor(lo) - 1, math.floor(hi) + 2):
                pos = e + m
                hit = (ps < pos <= end or ps == pos == 1.0) if s > 0 else (end < pos <= ps and not ps == pos == 1.0)
                if hit:
                    fr = t0 + ((pos - ps) * s * rinc if rinc is not None else (pos - ps) / x)
                    hits.append((min(max(fr, t0), t0 + length), kind, dV, dS))
        for fr, kind, dV, dS in sorted(hits, key=lambda h: h[0]):
            E[f"{kind}_{'f' if s > 0 else 'b'}"] += 1
            if kind == "wrap" and rec:
                if f32law:
                    f = r32(fr); t = float(_fma32(0.5, f, 254.0) if fma else r32(r32(f * 254) + 0.5)); u = f * 254 + 0.5
                else:
                    t = u = fr * 254 + 0.5
                ev[i] = max(0, min(254, int(t))); near[i] = abs(u - round(u)) < 1e-4
            if bl and not (s < 0 and mutant == "no_backward"):
                J, K = (dV, x * dS) if mutant == "no_mirror" else (s * dV, abs(x) * dS)
                add(c, fr, J, K)
        if end >= 1.0 or end < 0.0: end = r32(end - math.floor(end))
        return end

    for i in range(n):
        c = [0.0, 0.0]; x = float(di[i])
        if role == "slave" and sync[i] != EV_NONE and x != 0:
            at = r32(int(sync[i]) * third) if f32law else int(sync[i]) / 254
            p = part(p, x, 0.0, at, c, i, False)
            tgt = 0.0 if (x > 0 or mutant == "reset_p0") else 1.0
            E["resets"] += 1; E["resets_b"] += x < 0
            if bl: add(c, at, wv.value(tgt) - wv.value(p), x * (wv.slope(tgt) - wv.slope(p)))
            p = part(tgt, x, at, r32(1.0 - at), c, i, False)
        else:
            p = part(p, x, 0.0, 1.0, c, i, role == "master")
        y[i] = d + c[0]
        d = wv.value(p) + c[1]
    turns = None if role == "slave" else (p - p0) + E["wrap_f"] - E["wrap_b"]
    return FmRender(y, p, d, ev, near, E, turns)


def steady_fm(inc, k, n, phase=0.0, warm=None, law="f64", mutant=None):
    """fm_render at a constant increment inc (either sign) after `warm` samples (default n) from (phase, wave(phase)), so
    every kept sample has both correction halves. Returns (FmRender of the n kept samples, the phase of its first)."""
    warm = n if warm is None else warm; wv = FmWave(k, law)
    pre = fm_render(np.full(warm, float(inc)), k=k, phase=phase, delayed=wv.value(phase % 1.0), law=law, mutant=mutant)
    return fm_render(np.full(n, float(inc)), k=k, phase=pre.phase, delayed=pre.delayed, law=law, mutant=mutant), pre.phase


def fm_phase_walk(di, phase=0.0):
    """max |p_f32 - p_f64| (wrapped) over the block for one increment sequence: the float32 phase law's own error against
    the float64 one (di rounded to float32 included), the `walk` term of bessel_tol. A property of the law, computed here."""
    p, q, worst = float(phase) % 1.0, float(np.float32(float(phase) % 1.0)), 0.0
    for x in np.asarray(di, dtype=np.float64):
        p += x; p -= math.floor(p)
        q = float(np.float32(q + float(np.float32(x)))); q = float(np.float32(q - math.floor(q)))
        worst = max(worst, abs((p - q + 0.5) % 1.0 - 0.5))
    return worst


def _ideal_wave(wv, h, inc):
    """X_h (Spectrum's convention) of FmWave wv, the triangle, saw and pulse parts band-limited by sinc^2(h inc) (the unit
    triangle kernel the 2-point corrections sample exactly at a constant increment), the sine part not."""
    h = np.asarray(h, dtype=np.float64); odd = h % 2 == 1; g = np.sinc(h * abs(inc)) ** 2
    W = 2j / (np.pi * h)
    if wv.low:
        S = np.where(h == 1, -1.0, 0.0) + 0j; T = np.where(odd, -8 / (np.pi ** 2 * h ** 2), 0.0) + 0j
        return wv.ws * S + (wv.tw * T + wv.wsaw * W) * g
    P = 4 * (1 - np.exp(-2j * np.pi * h * wv.w)) / (2j * np.pi * h)
    return ((1 - wv.blend) * W + wv.blend * P) * g


def reference_k(wv, cycles, n, phase0=0.0, hmax=1 << 20):
    """The exact sampled band-limited block of FmWave wv at the constant increment cycles / n (cycles signed: negative runs
    the phase backward), every harmonic up to hmax folded onto its alias bin (Q3's reference, any k, both directions)."""
    inc = cycles / n
    if not 0 < abs(inc) <= INC_MAX: raise ValueError("increment out of range")
    h = np.arange(1, hmax + 1)
    G = _ideal_wave(wv, h, inc) * np.exp(2j * np.pi * h * phase0) * (n / 2)
    m = (h * cycles) % n
    Y = np.zeros(n, complex); np.add.at(Y, m, G); np.add.at(Y, (-m) % n, np.conj(G))
    return np.fft.ifft(Y).real


# ---- Bessel sidebands, sine -> sine (6.3) ---------------------------------------------------------------------------------
def bessel_j(k, x, M=256):
    """J_k(x), integer k (array), by the trapezoid rule on J_k(x) = (1/2pi) int cos(k t - x sin t) dt over one period
    (spectrally exact for a periodic integrand: error ~ J_{M-|k|}(x), below 1e-16 for |x| <= 20, |k| <= 64)."""
    t = 2 * np.pi * np.arange(M) / M; k = np.asarray(k, dtype=np.float64)
    v = np.mean(np.cos(np.multiply.outer(k, t) - x * np.sin(t)), axis=-1)
    return float(v) if v.ndim == 0 else v


def _beta(I, inc_m):
    """The discrete accumulator's exact index: sum_{j<i} cos(theta0 + j w) = [sin(theta_i - w/2) - sin(theta0 - w/2)] /
    (2 sin(w/2)), w = 2 pi inc_m, so the sampled phase is the continuous FM's with I (w/2) / sin(w/2) (1 + 1.2e-5 at note
    60) and the modulator half a sample early."""
    w = 2 * np.pi * inc_m
    return I * (w / 2) / math.sin(w / 2), w


def fm_sine_time(n, inc_c, inc_m, I, pc0, qm0, phi=np.pi):
    """Closed form of the sine carrier (k = -1: -cos 2 pi p) FM'd by m[j] = cos(2 pi (qm0 + j inc_m) + phi) (phi pi: Q3's
    -cos sine, OSC1 at Shape 0; -pi/2: a sin sub) under p[i+1] = p[i] + inc_c + I inc_m m[i], p[0] = pc0; y[i] = wave(p[i])
    (what fm_render gives with delayed = wave(pc0)): y_i = -cos(Phi_i + beta sin psi_i), psi_i = 2 pi qm0 + phi + (i - 1/2)
    w, Phi_i = 2 pi (pc0 + i inc_c) - beta sin psi_0."""
    beta, w = _beta(I, inc_m); i = np.arange(n, dtype=np.float64)
    psi = 2 * np.pi * qm0 + phi + (i - 0.5) * w
    return -np.cos(2 * np.pi * (pc0 + i * inc_c) - beta * math.sin(psi[0]) + beta * np.sin(psi))


def fm_sine_expect(n, cc, cm, I, pc0, qm0, phi=np.pi, kmax=None):
    """Y = fft(y) / n of fm_sine_time for coherent integers cc, cm (carrier / modulator cycles in n), by Jacobi-Anger:
    y_i = -sum_k J_k(beta) cos(Phi_0 + k psi_0 + 2 pi (cc + k cm) i / n), each term folded onto bins +-(cc + k cm) mod n
    (through zero and past Nyquist alike). At ratio 1 the bins mix J_k with J_{-k-2}; the check compares whole bins."""
    beta, w = _beta(I, cm / n)
    kmax = int(beta + 40) if kmax is None else kmax
    k = np.arange(-kmax, kmax + 1); J = bessel_j(k, beta)
    psi0 = 2 * np.pi * qm0 + phi - 0.5 * w
    ph = 2 * np.pi * pc0 - beta * math.sin(psi0) + k * psi0
    A = -J / 2 * np.exp(1j * ph); b = (cc + k * cm) % n
    Y = np.zeros(n, complex); np.add.at(Y, b, A); np.add.at(Y, (-b) % n, np.conj(A))
    return Y


def bessel_tol(I, inc_m, eps_c=SINQ_ERR, eps_m=SINQ_ERR, eps32=TOL_F32, walk=0.0):
    """Per-bin bound on |Y_meas - Y_expect| (Y = fft / n; a one-sided amplitude's bound is twice it), derived from the
    sines' stated error (I where marked):
    - |Y_err[b]| <= max_i |y_err_i| for every bin (|(1/n) sum x e^{..}| <= max |x|), so it suffices to bound the samples.
    - eps_c: the carrier's shaper (sinq, max error SINQ_ERR) errs by <= eps_c at any phase.
    - eps_m: the modulator's error e_m (max eps_m) adds I inc_m e_m to each di, so the carrier phase errs by I inc_m
      sum_j e_m(q_j). With e_m zero-mean and periodic (sinq of the triangle: odd harmonics), harmonic h's running sum is
      <= |c_h| / sin(pi h inc_m) (Dirichlet) ~ |c_h| / (pi h inc_m) (I: h inc_m << 1/2 where c_h matters), and Parseval +
      Cauchy-Schwarz give sum |c_h| / h <= sqrt(2) eps_m sqrt(pi^2 / 8) = (pi / 2) eps_m, so the phase error is <= I eps_m / 2
      cycles and the sample error <= 2 pi x that = pi beta eps_m (beta the discrete index).
    - walk: the float32 phase law's own error in cycles (fm_phase_walk on the block's di), times 2 pi.
    - eps32: the kernel's float32 arithmetic on the samples (6.3's 1e-5).
    The two slope terms use 2 pi x 1.01 (sinq's slope within 1 % of the exact sine's, I). At I = 1 this is 3.45e-4 a bin
    (one-sided 6.9e-4), 0.30 dB on the J3 bin at ratio 1 against 6.3's 0.5 dB need; at I = 2 one-sided 1.2e-3."""
    beta, _ = _beta(I, inc_m)
    return eps_c + 1.01 * (math.pi * beta * eps_m + 2 * math.pi * walk) + eps32


def bessel_check(y, cc, cm, I, pc0, qm0, phi=np.pi, eps_c=SINQ_ERR, eps_m=SINQ_ERR, eps32=TOL_F32, walk=0.0):
    """y (n samples of a sine carrier at Shape 0 FM'd by a sine, y[i] = carrier at phase p[i], p[0] = pc0, the modulator's
    m[0] at phase qm0, see fm_sine_time) against fm_sine_expect, every bin within bessel_tol. Integer cc, cm (coherent);
    the clamp must not engage (inc_c + I inc_m < INC_MAX, raises otherwise). Returns {ok, worst, tol, bins}: worst the
    largest |Y_meas - Y_expect|, bins a list of (bin, one-sided expected, measured, dB error, dB bound) for the bins whose
    expected |Y| is above tol (the sidebands; DC holds J_1's fold at ratio 1)."""
    y = np.asarray(y, dtype=np.float64); n = y.size
    if not (0 < cc < n // 2 and 0 < cm < n // 2): raise ValueError("cc, cm must be coherent bin counts below n / 2")
    if cc / n + I * cm / n >= INC_MAX: raise ValueError("the clamp would engage: no Bessel law")
    Y = np.fft.fft(y) / n; X = fm_sine_expect(n, cc, cm, I, pc0, qm0, phi)
    tol = bessel_tol(I, cm / n, eps_c, eps_m, eps32, walk); err = np.abs(Y - X); worst = float(err.max())
    one = lambda b: 1 if b in (0, n // 2) else 2                  # DC (a through-zero sideband at ratio 1) and Nyquist
    bins = [(b, one(b) * abs(X[b]), one(b) * abs(Y[b]), float(20 * np.log10(abs(Y[b]) / abs(X[b]))),
             float(20 * np.log10(1 + tol / abs(X[b])))) for b in range(n // 2 + 1) if abs(X[b]) > tol]
    return dict(ok=worst <= tol, worst=worst, tol=tol, bins=bins)


def _fm_sine_with_errors(n, inc_c, inc_m, I, pc0, qm0, phi, eps_c, eps_m, e1):
    """Self-test helper: the FM'd sine with an error of eps_c e1(p) on the carrier and of eps_m e1(q) on the modulator, e1
    max 1, zero-mean, half-wave antisymmetric (the shape of sinq's error class)."""
    q = (qm0 + inc_m * np.arange(n)) % 1.0
    m = np.cos(2 * np.pi * q + phi) + eps_m * e1((q + phi / (2 * np.pi)) % 1.0)
    di = fm_increments(inc_c, I, m, inc_m)
    p = (pc0 + np.r_[0.0, np.cumsum(di)[:-1]]) % 1.0
    return -np.cos(2 * np.pi * p) + eps_c * e1(p)


# ---- Q6: FM 1>2, OSC1 -> OSC2 (4.1, 4.4 E3 (a), 4.5, 4.7 BD1, OD5, OD15 = (b); 6.3, 6.6) ---------------------------------
FM12_MUTANTS = ("after_level", "before_crush", "audio_grid", "b_late", "kin_fm", "no_b")
FM12_EPS_M = SINQ_ERR + TOL_F32     # OSC1 as the Bessel modulator: sinq's stated error + its float32 sample bound (6.3)
DRIFT_BIN = 1.0                     # 6.3 / Q5's precedent: the mean frequency within one bin of the window (1 cycle)
CRUSH_DRIFT_CENTS = {12: 1.0, 8: 1.0, 4: None}   # OD15: the crush's share of OSC2's mean frequency, a free OSC1 (M: worst
                                    # 0.25 / 0.32 cents); 4 bits has no bound: the extremes' rounding (Q6 notes, M: +30 cents)
# Stock 1.6.8's 2x decimator 0x0807FCC0 (S: its 11 VFMA taps, the literals at 0x0807FDD0..0x0807FDE7 as its chain reads
# them: x[1] s10, x[0] s11, x[2] s12, x[3] s13, x[4] s14, x[5] s0, x[6] s14, x[7] s13, x[8] s12, x[9] s10, x[10] s11; one
# output per two inputs). Data read off the stock image, not our C; a test should re-read the six words and assert them.
_FIR6 = (-0.044159356504678726, 0.05654638633131981, -0.09867829829454422, 0.027138808742165565, 0.30596286058425903,
         0.4574306607246399)        # s10, s11, s12, s13, s14, s0
STOCK_FIR = (_FIR6[1], _FIR6[0], _FIR6[2], _FIR6[3], _FIR6[4], _FIR6[5], _FIR6[4], _FIR6[3], _FIR6[2], _FIR6[0], _FIR6[1])


def crush_mod(x, b, f32=True, dc=0.0):
    """OD15 (b) (Jeff 2026-11-16): FM 1>2's modulator is crushed with its top clamp at S, not S - 1:
    q_B = clamp(rint((x + dc) S), -S, S) / S - dc. The audio keeps crush() (OD11 (b)); only B takes this. The grid is then
    symmetric (2^b + 1 levels, rint odd), so a +1 high keeps its level and B carries only rounding, no grid DC: the -f_hi / S
    mean shift of crush() (Q2b) is gone at every depth. b None (Full) returns x (rounded to float32 when f32)."""
    return _grid(x, b, f32, dc, "mod")


def b_buffer(s, inc1, b=None, dc=0.0, level=1.0, mutant=None, di1=None, f32=True):
    """Buffer B (4.1, BD1, OD15): B[i] = f32(f32(inc1) q_B[i]), q_B = crush_mod(OSC1's pre-Level block s, its Bit Depth b,
    its part's dc) (s itself at Full), inc1 OSC1's base increment (before its own FM: kin = I inc1, 4.1). One rounding: the
    product of two float32 values is exact in float64. Level never enters (BD1: before Level; E3 (a): a silent OSC1 still
    modulates); `level` exists for the mutant. That form is a statement of the kernel's arithmetic (read it off the
    disassembly): compare B bit for bit; if the kernel folds inc1 into the crush differently, restate the form here.
    mutant (6.6 and plausible slips): "after_level" (B = inc1 f32(level q): y1 taken after Level), "before_crush" (B from s
    with a Bit Depth set), "audio_grid" (B from crush(): OD15 not applied, the grid's DC), "b_late" (B[i] from OSC1's sample
    i - 1), "kin_fm" (kin from OSC1's FM'd increment di1, not its base inc1). f32 False: all in float64 (law "f64")."""
    if mutant not in (None,) + FM12_MUTANTS: raise ValueError(f"mutant must be one of {FM12_MUTANTS}")
    r = _f32 if f32 else (lambda v: np.asarray(v, dtype=np.float64))
    q = (crush(s, b, f32, dc) if mutant == "audio_grid" else _x(s, f32) if mutant == "before_crush"
         else crush_mod(s, b, f32, dc))
    if mutant == "after_level": q = r(q * float(r(level)))
    if mutant == "b_late": q = np.r_[q[:1], q[:-1]]
    k = r(di1) if mutant == "kin_fm" else float(r(inc1))
    return r(k * q)


def an_index32(v):
    """FD3's index in the kernel's stated arithmetic (Q5 as built: an_index, `I(v) = f32(v^2) x f32(8e-6)`, one rounding):
    fm_index within float32 rounding; exact 2 at 500 and 8 at 1000. v clamped 0..1000 as an_prop stores it."""
    v = int(min(max(int(v), 0), 1000))
    return float(np.float32(float(np.float32(v * v)) * float(np.float32(8e-6))))


def index_line(v0, v1, N=256):
    """The index line (ia, dia) of a 2N block whose amount moves v0 (msub / m12, last block's) -> v1, as Q5 states it for
    Sub FM (an_osc: ia = I(v0), dia = f32(f32(I(v1) - ia) / N), 0 when they are equal; m12 the same rule, 4.1 / 4.4).
    I_j = f32(ia + dia j) (one VFMA), j the block frame: line_values. The float64 form is index_ramp."""
    i0, i1 = an_index32(v0), an_index32(v1)
    return i0, (0.0 if i1 == i0 else float(np.float32(float(np.float32(i1 - i0)) / N)))


def line_values(line, off, n):
    """I_j = f32(ia + dia j) for the block frames j = off .. off + n - 1 (chunked parts continue one line). line: (ia, dia)
    float32 values, or a scalar index (dia 0)."""
    ia, dia = (line, 0.0) if np.ndim(line) == 0 else line
    j = off + np.arange(n, dtype=np.float64)
    return _fma32(np.full(n, float(np.float32(ia))), np.full(n, float(np.float32(dia))), j)


def wave_dc(k, law="f32", width=None):
    """A part's dc (OD12, the crush's argument): the pulse's 2 w - 1 above k = 0.5, 0 at and below (saw, square, LOW). The
    kernel's own an_dc is the authority for a bit-for-bit check (pass it as dc=); this is FmWave's float32 form."""
    return FmWave(k, law, width).dc if k > 0.5 else 0.0


class Voice12(tuple):
    """fm12_voice's result: (y1, q1, B, di1, r1, di2, y2, q2, r2). y1 OSC1's render (pre-crush, pre-Level), q1 its audio
    crush (OD11 (b) grid), B buffer B, di1 / di2 the increments each oscillator rendered with, r1 / r2 their FmRender (end
    state, master bytes, edges), y2 OSC2's render, q2 its audio crush."""
    __slots__ = ()
    _f = ("y1", "q1", "B", "di1", "r1", "di2", "y2", "q2", "r2")
    def __new__(cls, *a): return tuple.__new__(cls, a)
    def __getattr__(self, k):
        try: return self[self._f.index(k)]
        except ValueError: raise AttributeError(k) from None


def fm12_voice(n, inc1, k1, inc2, k2, a=None, is1=0.0, is2=0.0, i12=0.0, bits=(None, None), state1=(0.0, None),
               state2=(0.0, None), sync=False, level1=1.0, off=0, law="f32", fma=True, widths=(None, None), dc=(None, None),
               order="ab", mutant=None):
    """One voice-block (or a part of one: frames off .. off + n - 1) of OSC1 -> OSC2 (4.1), both oscillators Analog:
    - OSC1: di1 = inc1 + I_s1 A (increments, law / fma as stated; no Sub FM: inc1 alone, clamped), rendered on k1 in role
      "master" when sync else "free" (OD5: a byte at every wrap, either way). q1 = crush(y1, bits[0], dc1) (its audio).
    - B = b_buffer(y1, inc1, bits[0], dc1) (BD1, OD15; level1 only reaches the "after_level" mutant: E3 (a)).
    - OSC2: di2 = inc2 + I_s2 A + I_12 B, folded in `order` ("ab": A first, 4.3's text; "ba"), rendered on k2 in role
      "slave" on OSC1's bytes when sync (OD5: any master wrap resets; backward to p = 1) else "free"; q2 its audio crush.
    a: buffer A from frame off (increment units, the sub copy: sub_a-style or the kernel's own), None for 0. is1 / is2 /
    i12: an index line (ia, dia) over the absolute frame (index_line), or a scalar. state1 / state2: (phase, delayed),
    delayed None -> the wave at phase (a steady start). dc: each oscillator's part dc, None -> wave_dc(k). widths: the
    kernel's float32 pulse widths (FmWave's width=). One call covers the whole range: chained calls on the returned states
    (r1 / r2 .phase / .delayed) with the next off are the same render (the golden's state is (phase, delayed) only), so
    AN_EVENTS chunks and Shape parts need no separate path here.
    mutant: FM12_MUTANTS: b_buffer's, plus "no_b" (OSC2 ignores B: 4.1's useB false)."""
    if order not in ("ab", "ba"): raise ValueError("order must be ab or ba")
    a = np.zeros(n) if a is None else np.asarray(a, dtype=np.float64)
    if a.size != n: raise ValueError("a must hold n samples (from frame off)")
    w1, w2 = FmWave(k1, law, widths[0]), FmWave(k2, law, widths[1])
    d1 = wave_dc(k1, "f32", widths[0]) if dc[0] is None else dc[0]          # a float32 constant in the kernel
    d2 = wave_dc(k2, "f32", widths[1]) if dc[1] is None else dc[1]
    I1, I2, I12 = (line_values(x, off, n) for x in (is1, is2, i12))
    t1 = [(I1, a)] if np.any(I1 != 0) else []
    di1 = np.broadcast_to(increments(inc1, t1, law=law, fma=fma), (n,)).copy()      # no terms: one value
    p1, e1 = state1; p2, e2 = state2
    r1 = fm_render(di1, k=k1, phase=p1, delayed=w1.value(p1 % 1.0 if p1 != 1.0 else 1.0) if e1 is None else e1,
                   role="master" if sync else "free", law=law, fma=fma, width=widths[0])
    f32 = law == "f32"
    y1 = r1.y; q1 = crush(y1, bits[0], f32, d1)
    B = b_buffer(y1, inc1, bits[0], d1, level1, None if mutant == "no_b" else mutant, di1, f32)
    terms = [(I2, a), (I12, B)] if order == "ab" else [(I12, B), (I2, a)]
    terms = [t for t in terms if np.any(t[0] != 0) and not (mutant == "no_b" and t[1] is B)]
    di2 = np.broadcast_to(increments(inc2, terms, law=law, fma=fma), (n,)).copy()
    r2 = fm_render(di2, k=k2, phase=p2, delayed=w2.value(p2 % 1.0 if p2 != 1.0 else 1.0) if e2 is None else e2,
                   role="slave" if sync else "free", sync=r1.ev if sync else None, law=law, fma=fma, width=widths[1])
    q2 = crush(r2.y, bits[1], f32, d2)
    return Voice12(y1, q1, B, di1, r1, di2, r2.y, q2, r2)


# ---- 6.3 DC drift (mean frequency) --------------------------------------------------------------------------------------
def unwrapped(di, phase0=0.0):
    """The unwrapped phase at the start of each sample and after the last (n + 1 values, float64 cumulative sum): what the
    increments add up to, for windows and travel (the float32 phase law's own bias is fm_phase_walk's)."""
    return float(phase0) + np.r_[0.0, np.cumsum(np.asarray(di, dtype=np.float64))]


def whole_cycles(di_mod, phase0=0.0):
    """[t0, t1): the window from the first to the last sample at which a modulator's unwrapped phase reaches a new integer
    moving forward (a sub: always forward; OSC1 without through-zero FM of its own), so it holds whole modulator cycles up
    to one sample's travel at each end. None if fewer than two."""
    P = unwrapped(di_mod, phase0)[:-1]
    fl = np.floor(P); up = np.flatnonzero((fl[1:] > fl[:-1]) & (np.diff(P) > 0)) + 1
    return (int(up[0]), int(up[-1])) if up.size >= 2 else None


def drift(di, inc, window=None):
    """6.3's DC-drift figure for one carrier: over window [t0, t1) (default all), travel = sum(di) against the unmodulated
    inc L. Returns {L, travel, expect, cycles (travel - inc L), cents (1200 log2(mean di / inc)), ok (|cycles| <= DRIFT_BIN)}.
    'Within one bin' is Q5's criterion (the Square sub test). Use whole modulator cycles (whole_cycles), or a coherent block:
    over an arbitrary window a zero-mean modulator's partial cycle alone moves the travel by up to I max|m| / 2 cycles."""
    di = np.asarray(di, dtype=np.float64)
    t0, t1 = (0, di.size) if window is None else window
    L = t1 - t0; travel = float(np.sum(di[t0:t1])); expect = float(inc) * L
    return dict(L=L, travel=travel, expect=expect, cycles=travel - expect,
                cents=1200 * math.log2(travel / expect) if travel > 0 and expect > 0 else float("nan"),
                ok=abs(travel - expect) <= DRIFT_BIN)


def crush_drift(s, b, inc1, I12, inc2, dc=0.0, window=None):
    """OD15's figure: the crush's own share of OSC2's mean frequency, in cents: 1200 log2(1 + I12 mean(B - B_Full) / inc2)
    over the window, B_Full = f32(inc1 s) (BD1 at Full), for OD15 (b) ("mod", b_buffer) and for the audio grid on B
    ("grid", the "audio_grid" mutant: 4.7's -3 / -55 / -1200 cents at 12 / 8 / 4 bits on a square, I = 8, OSC1 = OSC2).
    s: OSC1's uncrushed float32 block."""
    s = _x(s, True); t0, t1 = (0, s.size) if window is None else window
    full = b_buffer(s, inc1)[t0:t1]
    c = lambda B: 1200 * math.log2(1 + float(I12) * float(np.mean(B[t0:t1] - full)) / float(inc2))
    return dict(mod=c(b_buffer(s, inc1, b, dc)), grid=c(b_buffer(s, inc1, b, dc, mutant="audio_grid")))


# ---- 6.3 aliasing after the FIR model -----------------------------------------------------------------------------------
def decimate(y, taps=STOCK_FIR):
    """The 2x decimator on a periodic (coherent) 96 kHz block, circularly: out[j] = sum_k taps[k] y[(2 j + k) mod n], the
    stock FIR's one output per two inputs (its group delay is a fixed shift: no effect on the spectral energies)."""
    y = np.asarray(y, dtype=np.float64); n = y.size
    if n % 2: raise ValueError("n must be even")
    idx = (2 * np.arange(n // 2)[:, None] + np.arange(len(taps))[None, :]) % n
    return y[idx] @ np.asarray(taps, dtype=np.float64)


def fir_response(f, taps=STOCK_FIR, fs=96000.0):
    """|H(f)| of the FIR at the 96 kHz input rate (f in Hz, array)."""
    f = np.asarray(f, dtype=np.float64); k = np.arange(len(taps))
    return np.abs(np.exp(-2j * np.pi * np.multiply.outer(f / fs, k)) @ np.asarray(taps, dtype=np.float64))


def alias_db(y, cycles):
    """The old plan's metric (plan-analog-fm 4.3): off-harmonic / harmonic energy, dB, of a coherent block holding `cycles`
    periods of the voice's common fundamental (every FM sideband is a harmonic of it; aliases fall between)."""
    sp = spectrum(y, cycles)
    return 10 * math.log10(sp.off_energy / sp.harm_energy) if sp.off_energy > 0 else -np.inf


def alias_case(route, cc, cm, I, k_c, k_m=-1.0, wave="sine", fs=96000, n48=16384, taps=STOCK_FIR):
    """alias_db of one steady FM voice-block, the same sound at both rates: n48 output samples (0.34 s at n48 16384), the
    carrier holding cc cycles and the modulator cm in that time (coherent; gcd(cc, cm) odd: the common fundamental), index I
    (kin = I inc_mod, so I means the same at both rates), law f64 (a design figure).
    route "fm12": the modulator is OSC1's band-limited render at k_m (B = inc_m y1); "sub": the sub's naive wave (stock's
    sub has no BLEP) at inc_m, A = inc_m m. fs 96000: rendered at 2 n48 samples then decimate(taps) (the device); fs 48000:
    rendered at n48 directly (the old 48 kHz design, plan-analog-fm 4.3's figures). Returns alias_db.
    The sub's wave must be continuous (sine, triangle): a naive square or saw sampled coherently can put one sample exactly
    on its jump, and that sample's rounding moves the carrier's block travel by I inc_m 2 cycles (M: 0.58 at cm 591, I 8),
    so the block is no longer periodic and the leakage swamps the figure (+16 dB measured)."""
    if fs not in (48000, 96000): raise ValueError("fs is 48000 or 96000")
    n = n48 * (fs // 48000); inc_c, inc_m = cc / n, cm / n; g = math.gcd(cc, cm)
    if route == "fm12":
        m = steady_fm(inc_m, k_m, n)[0].y
    elif route == "sub":
        if wave not in ("sine", "triangle"): raise ValueError("alias_case's sub wave: sine or triangle (continuous)")
        m = sub_mod(wave, inc_m, n)[0]
    else: raise ValueError("route is fm12 or sub")
    di = clamp_fm(inc_c + I * inc_m * m); wv = FmWave(k_c)
    pre = fm_render(di, k=k_c, phase=0.0, delayed=wv.value(0.0))
    y = fm_render(di, k=k_c, phase=pre.phase, delayed=pre.delayed).y
    return alias_db(decimate(y, taps) if fs == 96000 else y, g)


def fm12_bessel(y2, cc, cm, I, pc0, qm0, walk=0.0, eps_c=SINQ_ERR, eps_m=FM12_EPS_M, eps32=TOL_F32):
    """6.3's Bessel check for FM 1>2, sine -> sine (OSC1 and OSC2 at Shape 0): bessel_check with OSC1's own sample as the
    modulator. OSC1 at Shape 0 from phase qm0 (delayed = its wave there) plays y1[i] = -cos 2 pi (qm0 + i inc1), so
    B[i] = inc1 y1[i] is fm_sine_time's m with phi = pi and inc_m = inc1 (= cm / n): OSC2 (from pc0, cc / n) sees exactly that
    law. eps_m defaults to OSC1's stated per-sample error, sinq + float32 (FM12_EPS_M); B's own rounding (2^-24 relative)
    is below it. Use OSC1 at Full: a crushed modulator has no Bessel law."""
    return bessel_check(y2, cc, cm, I, pc0, qm0, np.pi, eps_c, eps_m, eps32, walk)


# ---- self-test ----------------------------------------------------------------------------------------------------------
def _selftest():
    import unittest

    class BitDepth(unittest.TestCase):
        def test_settings(self):
            self.assertEqual([bits_of(v) for v in (0, 1, 2, 3, 7, -1)], [None, 12, 8, 4, 4, 4])
            self.assertEqual([scale(b) for b in BITS], [2048.0, 128.0, 8.0])
            x = probe(8); self.assertTrue(np.array_equal(crush(x, None), x))

        def test_four_bit_by_hand(self):                         # OD11: -8..+7 in steps of 1/8, ties to even
            x = np.array([-1.2, -1.0, -0.0625, -0.03, 0.0625, 0.1875, 0.8125, 0.875, 0.9375, 1.0, 1.3])
            want = np.array([-8, -8, -0.0, -0.0, 0, 2, 6, 7, 7, 7, 7]) / 8   # -0.5 -> -0, 0.5 -> 0, 1.5 -> 2, 6.5 -> 6
            q = crush(x, 4); self.assertTrue(np.array_equal(q, want), q * 8)
            self.assertTrue(np.signbit(q[2]) and np.signbit(q[3]) and not np.signbit(q[4]))

        def test_ties_go_to_even(self):
            for b in BITS:
                S = scale(b); k = np.arange(-S, S - 1)          # ties k + 0.5 for k = -S .. S-2
                q = crush((k + 0.5) / S, b) * S
                self.assertTrue(np.all(q % 2 == 0), b)
                self.assertTrue(np.all((q == k) | (q == k + 1)), b)

        def test_levels_and_clip_on_a_full_sweep(self):          # every level reached, exactly 2^b, both clips
            for b in BITS:
                x = np.linspace(-1.3, 1.3, 200001); q = crush(x, b)
                self.assertEqual(check_levels(q, b), 1 << b)
                top, bot = check_clip(x, q, b); self.assertGreater(top, 0); self.assertGreater(bot, 0)
                self.assertEqual(q.max(), (scale(b) - 1) / scale(b)); self.assertEqual(q.min(), -1.0)

        def test_float32_exact(self):                            # model in float64 == float32 arithmetic, every bit
            rng = np.random.default_rng(4410)
            x32 = (rng.standard_normal(65536) * 0.7).astype(np.float32)
            for b in BITS:
                S = np.float32(scale(b))
                with np.errstate(all="raise"):
                    q32 = np.minimum(np.maximum(np.rint(x32 * S), -S), S - np.float32(1)) * (np.float32(1) / S)
                self.assertTrue(np.array_equal(q32.view(np.uint32), crush_bits(x32, b)), b)

        def test_compare(self):
            x = probe(4); q = crush(x, 4)
            self.assertEqual(compare(q, x, 4), 0)
            bad = q.copy(); bad[10] += 1 / 8                     # one step off on a non-tie sample (0.3 -> got 1)
            with self.assertRaises(AssertionError): compare(bad, x, 4)
            y = x + 1e-5 * np.sign(np.sin(np.arange(x.size)))   # another model's input, within 1e-5
            self.assertGreaterEqual(compare(q, y, 4, err=2e-5), 0)
            with self.assertRaises(AssertionError): compare(bad, y, 4, err=2e-5)

        def test_probe_kills_every_mutant(self):                 # 6.6: the crafted block separates each wrong grid
            for b in BITS:
                for name, n in discriminates(probe(b), b).items(): self.assertGreater(n, 0, (b, name))

        def test_checks_reject_wrong_grids(self):
            x = probe(8)
            with self.assertRaises(AssertionError): check_clip(x, mutants(x, 8)["top_clamped_at_S"], 8)
            with self.assertRaises(AssertionError): levels(mutants(x, 8)["mid_tread_no_clamp"], 8)
            with self.assertRaises(AssertionError): levels(x, 8)              # uncrushed input is off the grid
            with self.assertRaises(ValueError): crush([0.0, np.nan], 8)

        # ---- Q2b (OD12 = (b)): the grid about the part's dc
        def test_dc_zero_is_q2s_grid(self):                      # every bit, sign of zero included
            rng = np.random.default_rng(4412)
            for b in BITS:
                S = scale(b)
                for x in (probe(b), _f32(rng.standard_normal(4096) * 0.7)):
                    q2 = np.clip(np.rint(x * S), -S, S - 1) / S
                    self.assertTrue(np.array_equal(crush_bits(x, b, dc=0.0), q2.astype(np.float32).view(np.uint32)), b)
            self.assertTrue(np.signbit(crush([-0.0], 8, dc=0.0)[0]))

        def test_dc_by_hand(self):                               # 4 bits about dc -0.75: the pulse 1.75 / -0.25 (w 1/8)
            x = np.array([1.75, -0.25, 0.8125, 1.9, -0.4])           # x + dc = 1, -1, 1/16 (a tie), 1.15, -1.15
            q = crush(x, 4, dc=-0.75)
            self.assertTrue(np.array_equal(q, [1.625, -0.25, 0.75, 1.625, -0.25]), q)
            self.assertTrue(np.array_equal(levels(q, 4, -0.75), [7, -8, 0, 7, -8]))
            self.assertEqual(check_clip(x, q, 4, dc=-0.75), (2, 1))
            self.assertTrue(np.array_equal(crush(x, None, dc=-0.75), _f32(x)))          # Full: untouched (any dc)

        def test_dc_float32_exact(self):                         # == the float32 add, grid, subtract, every bit
            rng = np.random.default_rng(4413)
            x32 = (rng.standard_normal(65536) * 0.9).astype(np.float32)
            d32 = (-0.84 * rng.random(65536)).astype(np.float32)
            for b in BITS:
                S = np.float32(scale(b))
                with np.errstate(all="raise"):
                    g = np.minimum(np.maximum(np.rint((x32 + d32) * S), -S), S - np.float32(1)) * (np.float32(1) / S)
                    q32 = g - d32
                self.assertTrue(np.array_equal(q32.view(np.uint32), crush_bits(x32, b, dc=d32)), b)

        def test_dc_levels_and_clip_on_a_sweep(self):            # one dc: exactly 2^b values, both clips about dc
            d = float(np.float32(-0.84))
            for b in BITS:
                S = scale(b); x = _f32(np.linspace(-1.3, 1.3, 200001) - d); q = crush(x, b, dc=d)
                self.assertEqual(check_levels(q, b, d), 1 << b)
                top, bot = check_clip(x, q, b, dc=d); self.assertGreater(top, 0); self.assertGreater(bot, 0)
                self.assertEqual(q.max(), float(np.float32((S - 1) / S - d)), b)
                self.assertEqual(q.min(), float(np.float32(-1 - d)), b)
            with self.assertRaises(ValueError): check_levels(q, 4, np.full(q.shape, d))
            with self.assertRaises(ValueError): crush([0.1], 8, dc=0.1)       # not a float32 value

        def test_the_pulse_keeps_its_mean(self):                 # OD12's point: the DC-free pulse stays DC-free
            for w in (0.08, 0.25, 0.4):
                d = float(np.float32(2 * w - 1)); P = 400; hi = int(round(w * P))
                one = np.r_[np.full(hi, 1 - d), np.full(P - hi, -1 - d)]; x = _f32(np.tile(one, 50))
                for b in BITS:
                    S = scale(b); q = crush(x, b, dc=d)
                    self.assertLess(abs(np.mean(q - x) + hi / P / S), 2.0 ** -23, (w, b))   # the +1 high loses one step
                    self.assertGreater(abs(np.mean(crush(x, b) - x)), 2 * hi / P / S, (w, b))   # OD12 (a): over twice

        def test_probe_about_dc_kills_every_mutant(self):
            for b in BITS:
                for name, n in discriminates(probe(b, -0.75), b, -0.75).items(): self.assertGreater(n, 0, (b, name))
                self.assertEqual(len(mutants(probe(b, -0.75), b, -0.75)), 8)

    class Shapes(unittest.TestCase):                             # Q3 (4.6, OD6)
        CASES = (0, 1, 12, 23, 24, 36, 45.5, 47)

        def test_anchors_and_plateaus(self):
            s = [0, 1, 2, 12, 22, 24, 26, 35.5, 45, 47, 48, -3]
            self.assertEqual(list(kcoord(s)), [-1, -1, -1, -0.75, -0.5, -0.5, -0.5, -0.25, 0, 0, 0, -1])
            self.assertEqual([weights(v) for v in (0, 12, 24, 35.5, 47)],
                             [(1, 0, 0), (0.5, 0.5, 0), (0, 1, 0), (0, 0.5, 0.5), (0, 0, 1)])
            self.assertAlmostEqual(kcoord(12 + 1 / 512), -0.75 + 0.5 / 20 / 512, delta=1e-15)  # 1/512-modulated
            with self.assertRaises(ValueError): kcoord(48.5)
            with self.assertRaises(ValueError): kcoord(np.nan)

        def test_waves_by_hand(self):
            p = np.array([0, 0.25, 0.5, 0.75])
            self.assertTrue(np.allclose(sine(p), [-1, 0, 1, 0]) and np.array_equal(tri(p), [-1, 0, 1, 0]))
            self.assertTrue(np.array_equal(saw(p), [-1, -0.5, 0, 0.5]))
            q = np.linspace(0, 1, 4097)[:-1]                     # the blend is continuous in Shape
            for a in (1.9, 21.9, 25.9, 44.9):
                self.assertLess(np.max(np.abs(naive(q, a) - naive(q, a + 0.2))), 0.03, a)

        def test_render_is_the_band_limited_spectrum(self):     # BLEP / BLAMP signs, scales and the one-sample delay
            for c in (11, 301, 1501):
                inc = coherent_inc(c, 4096)
                for sh in self.CASES:
                    r, p0 = steady(inc, sh, 4096)
                    err = np.max(np.abs(r.y - reference(sh, c, 4096, p0)))
                    self.assertLess(err, 1e-9, (c, sh))
                    self.assertGreater(np.max(np.abs(render(inc, sh, 4096, p0, float(naive(p0, sh)), bl=False).y[1:]
                                                      - r.y[1:])), 1e-4 if sh > 2 else -1, (c, sh))

        def test_chained_parts_equal_one_render(self):
            a = render(0.0123, 36, 300, 0.3, 0.1)
            b = render(0.0123, 36, 117, 0.3, 0.1); b2 = render(0.0123, 36, 183, b.phase, b.delayed)
            self.assertTrue(np.array_equal(a.y, np.r_[b.y, b2.y]) and a.phase == b2.phase and a.delayed == b2.delayed)

        def test_band_limiting_lowers_the_aliasing(self):       # off-harmonic energy, golden against naive, 301/4096
            inc = coherent_inc(301, 4096)
            for sh in (12, 24, 36, 47):
                r, p0 = steady(inc, sh, 4096)
                nv = render(inc, sh, 4096, p0, float(naive(p0, sh)), bl=False)
                self.assertLess(spectrum(r.y, 301, p0).off_db, spectrum(nv.y, 301, p0).off_db - 10, sh)

        def test_pure_sine(self):                                # Shape 0: no harmonics, no aliasing (f64); f32 phase
            for law, lim in (("f64", -250), ("f32", -120)):
                r, p0 = steady(coherent_inc(11, 4096), 0, 4096, law)
                sp = spectrum(r.y, 11, p0)
                self.assertLess(sp.thd_db, lim, law); self.assertLess(sp.off_db, lim, law)
                self.assertAlmostEqual(sp.harm[0], -1, delta=1e-5)

        def test_spectrum_conventions(self):
            n, c = 2048, 7; j = np.arange(n); p = 0.2 + j * c / n
            X = {1: 0.5 - 0.25j, 3: 0.1j, 4: -0.02}
            x = 0.05 + sum((v * np.exp(2j * np.pi * h * p)).real for h, v in X.items()) + 1e-3 * np.cos(2 * np.pi * 100 * j / n)
            sp = spectrum(x, c, 0.2)
            for h, v in X.items(): self.assertAlmostEqual(sp.harm[h - 1], v, delta=1e-12)
            self.assertAlmostEqual(sp.dc, 0.05, delta=1e-12); self.assertAlmostEqual(sp.off_energy, 0.5e-6, delta=1e-15)
            self.assertAlmostEqual(sp.dc ** 2 + sp.harm_energy + sp.off_energy, sp.total, delta=1e-12)
            self.assertTrue(np.allclose(ideal(47, [1, 2]), [2j / np.pi, 1j / np.pi]))
            self.assertTrue(np.allclose(ideal(24, [1, 2, 3]), [-8 / np.pi ** 2, 0, -8 / (9 * np.pi ** 2)]))
            with self.assertRaises(ValueError): spectrum(x, 8, 0.0)      # not coprime with n

        def test_f32_law_and_the_drift_bound(self):
            for inc in (261.63 / 96000, 0.0123, 0.2):
                a = render(inc, 36, 512, 0.1, 0.0, "f64"); b = render(inc, 36, 512, 0.1, 0.0, "f32")
                self.assertEqual(float(np.float32(b.phase)), b.phase)
                self.assertLess(np.max(np.abs(a.y - b.y)), tol_time(36, inc, 512, "f64", phase=0.1) - TOL_F32, inc)
            self.assertLess(phase_drift(0.0123, 256), phase_drift(0.0123, 4096))
            self.assertEqual(tol_time(24, 0.01, 256), TOL_F32); self.assertEqual(tol_time(0, 0.01, 256, sinq_err=3e-5), TOL_F32 + 3e-5)

        def test_edges_and_events(self):
            r = render(coherent_inc(301, 4096), 24, 4096, 0.05, 0.0)
            self.assertEqual((r.wraps, r.halves), (301, 301))   # phase 0.05 -> 301.05: no crossing on a block edge
            w = np.flatnonzero(r.ev != 255); self.assertEqual(w.size, r.wraps)
            self.assertTrue(np.all(r.ev[w] <= 254))
            self.assertEqual(render(0.0, 12, 8, 0.4).wraps, 0)                 # inc <= 0 stops the phase
            self.assertTrue(np.all(render(-1.0, 12, 8, 0.4, float(naive(0.4, 12))).y == naive(0.4, 12)))
            self.assertEqual(clamp_inc(0.9), INC_MAX)

    class FM(unittest.TestCase):                                 # Q5 (4.3, OD5, FD3, OD10, 6.3)
        KS = (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0)

        def test_index_law_and_ramp(self):                       # FD3: I = 8 (v/1000)^2, the ramp linear over 2N
            self.assertEqual(list(fm_index([0, 250, 500, 1000, 5000, -3])), [0, 0.5, 2, 8, 8, 0])
            self.assertEqual(float(fm_index(500, law="linear")), 4.0)               # the 6.6 mutant
            r = index_ramp(0, 1000, 256); self.assertEqual((r[0], r[128]), (0.0, 4.0))
            self.assertTrue(np.array_equal(np.r_[index_ramp(300, 700, 256, 0, 128), index_ramp(300, 700, 256, 128)],
                                           index_ramp(300, 700, 256)))           # chunked parts continue one ramp
            nxt = index_ramp(700, 700, 256)[0]; self.assertAlmostEqual(r[-1] + (r[1] - r[0]), 8.0, delta=1e-12)
            self.assertEqual(nxt, fm_index(700))

        def test_sub_waves_are_dc_free(self):                    # OD10: the Square's DC removed, every wave mean 0
            for w in SUB_WAVES:
                for pw in (0.25, 0.5, 0.625):                     # 512 samples a cycle, exact dyadic phases
                    m, _ = sub_mod(w, 1 / 512, 4096, 0.0, pw)
                    dc = {"saw": -1 / 512, "rev_saw": 1 / 512}.get(w, 0.0)   # a sampled ramp's mean is -+inc (4.2's residual)
                    self.assertLess(abs(np.mean(m) - dc), 1e-12, (w, pw))
                    self.assertLessEqual(np.max(np.abs(m)), 2.0)
            m, _ = sub_mod("square", 1 / 400, 4000, 0.0, 0.3)
            self.assertEqual(sorted(set(np.round(m, 12))), [-0.6, 1.4])           # +-1 - (2 pw - 1)
            self.assertAlmostEqual(np.mean(m + (2 * 0.3 - 1)), -0.4, delta=1e-12)  # the raw square's DC (the hazard)
            with self.assertRaises(ValueError): sub_wave("noise", 0.1)

        def test_mean_frequency_with_the_square_sub(self):       # 6.3: DC-free -> the carrier's mean increment unchanged
            inc, isub, I = 261.63 / 96000, 1 / 400, 8.0
            m, _ = sub_mod("square", isub, 4000, 0.0, 0.3)
            di = fm_increments(inc, I, m, isub)
            self.assertAlmostEqual(np.mean(di), inc, delta=1e-15)
            raw = fm_increments(inc, I, m + (2 * 0.3 - 1), isub)
            self.assertAlmostEqual(np.mean(raw) - inc, I * isub * (2 * 0.3 - 1), delta=1e-15)   # -3.2 x inc_sub at pw 0.3
            r = fm_render(di, k=0.0, phase=0.1, delayed=float(FmWave(0.0).value(0.1)))
            self.assertAlmostEqual(r.turns, 4000 * inc, delta=1e-9)             # 100 blocks' worth of phase: no drift

        def test_increments_laws(self):
            rng = np.random.default_rng(4415); n = 4000
            inc = float(np.float32(0.0123)); I = _f32(rng.random(n) * 8); a = _f32(rng.standard_normal(n) * 0.01)
            sep = increments(inc, [(I, a)], law="f32", fma=False, clamp=False)
            want = np.float32(inc) + I.astype(np.float32) * a.astype(np.float32)          # numpy float32: two roundings
            self.assertTrue(np.array_equal(sep, want.astype(np.float64)))
            got = increments(inc, [(I, a)], law="f32", fma=True, clamp=False)
            from fractions import Fraction as F
            for j in range(0, n, 7):                              # fma: the float32 nearest the exact value
                ex = F(inc) + F(float(I[j])) * F(float(a[j])); g = np.float32(got[j])
                for nb in (np.nextafter(g, np.float32(np.inf)), np.nextafter(g, np.float32(-np.inf))):
                    self.assertLessEqual(abs(F(float(g)) - ex), abs(F(float(nb)) - ex), j)
            self.assertEqual(float(_fma32(2.0 ** 24 + 2, 1 + 2.0 ** -23, -(1 - 2.0 ** -23))), 2.0 ** 24 + 2)  # double rounding
            self.assertEqual(float(np.float32(2.0 ** 24 + 2 + (1 + 2.0 ** -23) * -(1 - 2.0 ** -23))), 2.0 ** 24)   # (the trap)
            c = increments(0.3, [(8.0, np.array([0.1, -0.2, np.nan, 0.0]))])
            self.assertEqual(list(c), [INC_MAX, -INC_MAX, 0.0, 0.3])
            self.assertEqual(list(increments(0.3, [(8.0, np.array([0.1, -0.2]))], clamp=False)), [1.1, 0.3 - 1.6])

        def test_no_modulation_is_q3s_render(self):              # k < 0, both laws, bit for bit
            for k in (-1.0, -0.75, -0.5, -0.25):
                sh = float(np.interp(k, _KNOTS_K[1:5], _KNOTS_S[1:5])) if k > -1 else 0.0
                self.assertEqual(kcoord(sh), k)
                for law in LAWS:
                    a = render(0.0123, sh, 300, 0.3, 0.1, law)
                    b = fm_render(np.full(300, 0.0123), k=k, phase=0.3, delayed=0.1, law=law)
                    self.assertTrue(np.array_equal(a.y, b.y) and a.phase == b.phase and a.delayed == b.delayed, (k, law))
                    self.assertEqual((a.wraps, a.halves), (b.edges["wrap_f"], b.edges["half_f"] if k > -1 else a.halves))

        def test_constant_increments_are_band_limited_both_ways(self):   # forward and mirrored corrections, every k
            for c in (11, 301):
                for k in self.KS:
                    wv = FmWave(k)
                    for s in (1, -1):
                        r, p0 = steady_fm(s * c / 4096, k, 4096, phase=0.37)
                        err = np.max(np.abs(r.y - reference_k(wv, s * c, 4096, p0)))
                        self.assertLess(err, 1e-9, (c, k, s))
                        if s < 0 and k != -1:                     # the mirror mutants are visible here
                            for mu in ("no_mirror", "no_backward"):
                                q, _ = steady_fm(s * c / 4096, k, 4096, phase=0.37, mutant=mu)
                                self.assertGreater(np.max(np.abs(q.y - r.y)), 1e-4, (c, k, mu))

        def test_time_reversal(self):                            # through zero, varying di: y_rev[n - i] = y[i]
            rng = np.random.default_rng(4416); n = 900
            j = np.arange(n); di = 0.04 + 0.2 * np.sin(2 * np.pi * j / 170) + 0.02 * rng.standard_normal(n)
            self.assertTrue(np.any(di < 0) and np.any(di > 0))
            for k in self.KS:
                f = fm_render(di, k=k, phase=0.41, delayed=0.0)
                b = fm_render(-di[::-1], k=k, phase=f.phase, delayed=0.0)
                self.assertLess(np.max(np.abs(b.y[1:][::-1] - f.y[1:])), 1e-9, k)
                self.assertGreater(f.edges["wrap_b"], 5); self.assertGreater(f.edges["wrap_f"], 5)
                self.assertEqual((b.edges["wrap_f"], b.edges["wrap_b"]), (f.edges["wrap_b"], f.edges["wrap_f"]))
                if k != -1:
                    for mu in ("no_mirror", "no_backward"):
                        g = fm_render(di, k=k, phase=0.41, delayed=0.0, mutant=mu)
                        self.assertGreater(np.max(np.abs(g.y - f.y)), 1e-4, (k, mu))

        def test_master_events_both_ways(self):                  # OD5: every wrap writes a byte, forward or backward
            j = np.arange(700); di = 0.01 + 0.3 * np.sin(2 * np.pi * j / 97)
            r = fm_render(di, k=0.0, phase=0.5, role="master")
            w = np.flatnonzero(r.ev != 255)
            self.assertEqual(w.size, r.edges["wrap_f"] + r.edges["wrap_b"]); self.assertGreater(r.edges["wrap_b"], 10)
            p = 0.5
            for i in range(700):                                 # independent: the wrap's fraction from the f64 phase
                e = p + di[i]
                if e >= 1 or e < 0:
                    fr = (1 - p) / di[i] if e >= 1 else p / -di[i]
                    self.assertEqual(r.ev[i], max(0, min(254, int(fr * 254 + 0.5))), i)
                p = e - math.floor(e)
            self.assertTrue(np.all(fm_render(di, k=0.0, phase=0.5).ev == 255))   # free: no bytes

        def test_backward_reset_is_the_mirrored_forward_one(self):  # OD5's p = 1: reflection p -> 1 - p
            rng = np.random.default_rng(4417); n = 600
            j = np.arange(n); di = 0.03 + 0.12 * np.sin(2 * np.pi * j / 133) + 0.01 * rng.standard_normal(n)
            ev = np.full(n, 255, np.uint8); at = rng.choice(n, 60, replace=False); ev[at] = rng.integers(0, 255, 60)
            ev[at[:4]] = (0, 254, 254, 0)
            for k, sgn in ((-1.0, 1), (-0.75, 1), (-0.5, 1), (0.0, -1), (0.25, -1), (0.5, -1)):
                a = fm_render(di, k=k, phase=0.23, delayed=0.1, role="slave", sync=ev)
                b = fm_render(-di, k=k, phase=1 - 0.23, delayed=sgn * 0.1, role="slave", sync=ev)
                self.assertLess(np.max(np.abs(a.y - sgn * b.y)), 1e-12, k)
                self.assertEqual(a.edges["resets"], int(np.sum((ev != 255) & (di != 0))))
                self.assertEqual(a.edges["resets_b"], b.edges["resets"] - b.edges["resets_b"])
                # 6.6's double edge (reset to 0, then at once the backward wrap) puts its two corrections at the same
                # fraction, and they sum to the one-edge rule's jump wave(1-) - wave(p) (value and slope): the same output
                # up to rounding, except on the sample after a byte-254 reset (nothing left of the sample: the mutant
                # stays at 0, its wrap lands in the next sample with that sample's di). Its trace is the extra edge.
                m = fm_render(di, k=k, phase=0.23, delayed=0.1, role="slave", sync=ev, mutant="reset_p0")
                after = np.zeros(n, bool); after[1:] = (ev[:-1] == 254) & (di[:-1] < 0)
                self.assertLess(np.max(np.abs(m.y - a.y)[~after]), 1e-12, k)
                back = int(np.sum((ev != 255) & (ev != 254) & (di < 0)))
                self.assertGreaterEqual(m.edges["wrap_b"] - a.edges["wrap_b"], back, k); self.assertGreater(back, 10)

        def test_one_edge_at_a_backward_reset(self):
            ev = np.full(4, 255, np.uint8); ev[1] = 127
            for k in (0.0, 0.5, -0.5):
                a = fm_render(np.full(4, -0.01), k=k, phase=0.5, role="slave", sync=ev)
                m = fm_render(np.full(4, -0.01), k=k, phase=0.5, role="slave", sync=ev, mutant="reset_p0")
                self.assertEqual((a.edges["wrap_b"], m.edges["wrap_b"]), (0, 1), k)
                self.assertLess(np.max(np.abs(a.y - m.y)), 1e-15, k)          # equivalent output (above): count it
                self.assertAlmostEqual(a.phase, 1 - 0.01 * (1 - 127 / 254) - 0.02, delta=1e-12)
            z = fm_render(np.zeros(4), k=0.0, phase=0.5, role="slave", sync=ev)   # di == 0: no reset
            self.assertEqual((z.edges["resets"], z.phase), (0, 0.5))

        def test_through_zero_sine_is_the_closed_form(self):     # the phase law against Jacobi-Anger, I = 1, 2, 8
            n = 4096
            for cc, cm, I, phi in ((11, 11, 1.0, np.pi), (11, 11, 2.0, np.pi), (21, 7, 2.0, -np.pi / 2), (3, 11, 8.0, 0.3)):
                inc_c, inc_m = cc / n, cm / n
                m = np.cos(2 * np.pi * (0.13 + inc_m * np.arange(n)) + phi)
                di = fm_increments(inc_c, I, m, inc_m); self.assertLess(np.max(np.abs(di)), INC_MAX)
                if I == 8: self.assertTrue(np.any(di < 0))
                r = fm_render(di, k=-1.0, phase=0.29, delayed=float(FmWave(-1.0).value(0.29)))
                t = fm_sine_time(n, inc_c, inc_m, I, 0.29, 0.13, phi)
                self.assertLess(np.max(np.abs(r.y - t)), 1e-10, (cc, cm, I))
                Y = np.fft.fft(t) / n
                self.assertLess(np.max(np.abs(Y - fm_sine_expect(n, cc, cm, I, 0.29, 0.13, phi))), 1e-12, (cc, cm, I))
                chk = bessel_check(r.y, cc, cm, I, 0.29, 0.13, phi, eps_c=0, eps_m=0, eps32=1e-10)
                self.assertTrue(chk["ok"], chk["worst"])

        def test_bessel_values(self):
            want = {(0, 1): 0.7651976865579666, (1, 1): 0.4400505857449335, (2, 1): 0.1149034849319005,
                    (3, 1): 0.0195633539826684, (0, 2): 0.2238907791412357, (3, 2): 0.1289432494744021, (-3, 2): -0.1289432494744021}
            for (k, x), v in want.items(): self.assertAlmostEqual(float(bessel_j(k, x)), v, delta=1e-15)

        def test_bessel_check_tolerance(self):                   # derived from SINQ_ERR; holds against worst-shaped errors
            n, cc, cm = 4096, 11, 11
            for I in (1.0, 2.0):
                tol = bessel_tol(I, cm / n)
                if I == 1.0:                                      # 6.3's need: 0.5 dB on the J3 bin (one-sided 2 tol)
                    j3 = 2 * abs(fm_sine_expect(n, cc, cm, I, 0.0, 0.0)[4 * cm])
                    self.assertLess(20 * np.log10(1 + 2 * tol / j3), 0.5)
                for e1 in (lambda p: np.cos(6 * np.pi * p), lambda p: np.sign(np.sin(2 * np.pi * p)) * np.sin(10 * np.pi * p) ** 2):
                    y = _fm_sine_with_errors(n, cc / n, cm / n, I, 0.29, 0.13, np.pi, SINQ_ERR, SINQ_ERR, e1)
                    chk = bessel_check(y, cc, cm, I, 0.29, 0.13)
                    self.assertTrue(chk["ok"], (I, chk["worst"], chk["tol"]))
                # what it must catch: the index 2 % off, the modulator one sample late, the linear law at v = 354
                for bad in (dict(I=I * 1.02), dict(qm0=0.13 - cm / n), dict(I=float(fm_index(354, "linear")) * I)):
                    args = dict(I=I, qm0=0.13); args.update(bad)
                    m = np.cos(2 * np.pi * (args["qm0"] + cm / n * np.arange(n)) + np.pi)
                    r = fm_render(fm_increments(cc / n, args["I"], m, cm / n), k=-1.0, phase=0.29,
                                  delayed=float(FmWave(-1.0).value(0.29)))
                    self.assertFalse(bessel_check(r.y, cc, cm, I, 0.29, 0.13)["ok"], (I, bad))

        def test_f32_law(self):                                  # kernel phase state: float32 steps, phase exact float32
            j = np.arange(1000); di = increments(0.01, [(4.0, 0.02 * np.sin(2 * np.pi * j / 77))], law="f32")
            ev = np.full(1000, 255, np.uint8); ev[::37] = 100
            for role, sy in (("free", None), ("master", None), ("slave", ev)):
                a = fm_render(di, k=0.75, phase=0.3, role=role, sync=sy, law="f32")
                b = fm_render(di, k=0.75, phase=0.3, role=role, sync=sy, law="f64")
                self.assertEqual(float(np.float32(a.phase)), a.phase)
                self.assertLess(np.max(np.abs(a.y - b.y)), 1e-3, role)
                self.assertGreater(np.max(np.abs(a.y - b.y)), 0.0, role)
            self.assertLess(fm_phase_walk(di, 0.3), 1e-5)

        def test_mutants_differ(self):                           # 6.6's FM rows that live in the model
            j = np.arange(512); m = np.sin(2 * np.pi * j / 64)
            di = fm_increments(0.02, 8.0, m, 1 / 64); dn = increments(0.02, [(8.0, m / 64)], clamp=False)
            self.assertGreater(np.max(np.abs(dn)), 0.12)
            base = fm_render(di, k=0.25, phase=0.2)
            self.assertGreater(np.max(np.abs(fm_render(di, k=0.25, phase=0.2, mutant="rinc", inc0=0.02).y - base.y)), 1e-3)
            hi = fm_increments(0.4, 8.0, m, 1 / 64)              # the clamp engages: |di| <= 0.45
            self.assertEqual(np.max(np.abs(hi)), INC_MAX)
            self.assertGreater(np.max(np.abs(increments(0.4, [(8.0, m / 64)], clamp=False))), 0.5)

    class FM12(unittest.TestCase):                               # Q6 (4.1, 4.4 E3 (a), BD1, OD5, OD15 = (b), 6.3)
        NOTE60 = float(np.float32(440 * 2 ** (-9 / 12) / 96000))   # note 60 at 96 kHz (the device's domain)
        N100 = 100 * 256                                         # 100 device blocks (2N = 256)

        def test_the_modulators_grid_keeps_the_top(self):        # OD15 (b): top at S, the audio grid untouched
            x = np.array([1.0, 1.06, 0.9375, 0.875, -1.0, -1.06, -0.0625, 0.0])
            self.assertTrue(np.array_equal(crush_mod(x, 4), [1.0, 1.0, 1.0, 0.875, -1.0, -1.0, -0.0, 0.0]))
            self.assertTrue(np.array_equal(crush(x, 4), [0.875, 0.875, 0.875, 0.875, -1.0, -1.0, -0.0, 0.0]))
            for b in BITS:
                S = scale(b); x = probe(b); m, a = crush_mod(x, b), crush(x, b)
                self.assertTrue(np.array_equal(crush_mod(-x, b).view(np.uint64), (-m).view(np.uint64)), b)   # odd: rint
                hi = np.rint(x * S) >= S
                self.assertTrue(np.array_equal(m[~hi], a[~hi]) and np.all(m[hi] == 1.0), b)   # differs only at the top
                q = crush_mod(np.linspace(-1.3, 1.3, 200001), b)
                self.assertEqual(np.unique(q).size, (1 << b) + 1, b)                 # 2^b + 1 levels, symmetric
                self.assertEqual((q.min(), q.max()), (-1.0, 1.0))
            rng = np.random.default_rng(4420)                     # float32-exact, about a kernel dc too
            x32 = (rng.standard_normal(65536) * 0.9).astype(np.float32); d32 = (-0.84 * rng.random(65536)).astype(np.float32)
            for b in BITS:
                S = np.float32(scale(b))
                with np.errstate(all="raise"):
                    g = np.minimum(np.maximum(np.rint((x32 + d32) * S), -S), S) * (np.float32(1) / S)
                self.assertTrue(np.array_equal((g - d32).view(np.uint32), _f32(crush_mod(x32, b, dc=d32)).astype(np.float32).view(np.uint32)))
            self.assertTrue(np.array_equal(crush_mod([1.75, -0.25], 4, dc=-0.75), [1.75, -0.25]))   # the pulse's +1 high
            self.assertTrue(np.array_equal(crush_mod(x, None), _f32(x)))

        def test_the_square_carries_no_grid_dc(self):            # OD15's point, on the naive square (+-1 on the grid)
            x = np.tile(np.r_[np.ones(200), -np.ones(200)], 20)
            for b in BITS:
                self.assertEqual(float(np.mean(crush_mod(x, b))), 0.0, b)
                self.assertAlmostEqual(float(np.mean(crush(x, b))), -0.5 / scale(b), delta=1e-15)   # Q2b's -f_hi / S

        def test_b_buffer(self):                                 # BD1 / OD15 form, one rounding; the mutants differ
            rng = np.random.default_rng(4421); n = 512
            s = _f32(np.sign(np.sin(2 * np.pi * np.arange(n) / 61)) * 0.999 + 0.001 * rng.standard_normal(n))
            inc1 = 0.0027250234
            for b in (None,) + BITS:
                q = crush_mod(s, b).astype(np.float32)
                want = (np.float32(inc1) * q).view(np.uint32)
                self.assertTrue(np.array_equal(b_buffer(s, inc1, b).astype(np.float32).view(np.uint32), want), b)
                self.assertTrue(np.array_equal(b_buffer(s, inc1, b, level=0.0), b_buffer(s, inc1, b)), b)   # Level never enters
            di1 = _f32(inc1 * (1 + 0.5 * np.sin(np.arange(n) / 9)))
            base = b_buffer(s, inc1, 4)
            for mu in ("after_level", "before_crush", "audio_grid", "b_late", "kin_fm"):
                got = b_buffer(s, inc1, 4, level=0.5, mutant=mu, di1=di1)
                self.assertGreater(int(np.sum(got != base)), 0, mu)
            self.assertTrue(np.array_equal(b_buffer(s, inc1, None, mutant="before_crush"), b_buffer(s, inc1)))   # Full: same
            with self.assertRaises(ValueError): b_buffer(s, inc1, mutant="nope")

        def test_the_index_line(self):                           # FD3 in the kernel's float32 form; parts continue it
            for v in (0, 1, 250, 354, 500, 707, 999, 1000):
                self.assertLessEqual(abs(an_index32(v) - fm_index(v)), 2.0 ** -22 * max(fm_index(v), 2.0 ** -20), v)
            self.assertEqual((an_index32(500), an_index32(1000), an_index32(5000), an_index32(-3)), (2.0, 8.0, 8.0, 0.0))
            ia, dia = index_line(0, 1000); self.assertEqual(ia, 0.0)
            self.assertLessEqual(abs(ia + 256 * dia - 8.0), 1e-6 * 8)
            self.assertEqual(index_line(700, 700), (an_index32(700), 0.0))
            L = index_line(300, 900); Lv = line_values(L, 0, 256)
            self.assertTrue(np.array_equal(np.r_[line_values(L, 0, 128), line_values(L, 128, 128)], Lv))
            self.assertLessEqual(float(np.max(np.abs(Lv - (L[0] + L[1] * np.arange(256))))), 2.0 ** -21)   # one VFMA
            self.assertLessEqual(abs(Lv[-1] + L[1] - an_index32(900)), 1e-6 * 8)   # it reaches the next block's I
            self.assertTrue(np.array_equal(line_values(2.0, 5, 3), [2.0, 2.0, 2.0]))

        def case(self, n=512, sync=False, sub=True, bits=(None, None), level1=1.0, mutant=None, k=(0.0, 0.25), law="f32",
                 off=0, i12=(0.72, 0.0), states=((0.1, None), (0.3, None)), is1v=2.0):
            inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
            m, _ = sub_mod("sine", isub, n, 0.2); a = _f32(isub * m) if sub else None
            return fm12_voice(n, inc, k[0], inc * 1.5, k[1], a=a, is1=(is1v, 0.0) if sub else 0.0,
                              is2=(8.0, 0.0) if sub else 0.0, i12=i12, bits=bits, state1=states[0], state2=states[1],
                              sync=sync, level1=level1, off=off, law=law, mutant=mutant)

        def test_a_silent_osc1_still_modulates(self):            # E3 (a), BD1: B is before Level
            for sync in (False, True):
                a, z = self.case(sync=sync), self.case(sync=sync, level1=0.0)
                self.assertTrue(np.array_equal(a.y2, z.y2) and np.array_equal(a.B, z.B), sync)
                m = self.case(sync=sync, level1=0.0, mutant="after_level")
                self.assertTrue(np.array_equal(m.B, np.zeros_like(m.B)), sync)       # y1 after Level 0: no FM 1>2
                nb = self.case(sync=sync, mutant="no_b")
                self.assertTrue(np.array_equal(m.y2, nb.y2), sync)
                self.assertGreater(float(np.max(np.abs(m.y2 - a.y2))), 1e-3, sync)

        def test_the_voice_is_its_parts(self):                   # 4.1's composition, stated once; parts chain
            for sync in (False, True):
                for bits in ((None, None), (8, 4), (4, None)):
                    v = self.case(sync=sync, bits=bits, k=(0.9, -0.3))
                    inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
                    m, _ = sub_mod("sine", isub, 512, 0.2); a = _f32(isub * m)
                    di1 = increments(inc, [(line_values((2.0, 0.0), 0, 512), a)], law="f32")
                    r1 = fm_render(di1, k=0.9, phase=0.1, delayed=FmWave(0.9, "f32").value(0.1), role="master" if sync else "free", law="f32")
                    d1 = wave_dc(0.9); B = b_buffer(r1.y, inc, bits[0], d1)
                    di2 = increments(inc * 1.5, [(np.full(512, 8.0), a), (np.full(512, 0.72), B)], law="f32")
                    r2 = fm_render(di2, k=-0.3, phase=0.3, delayed=FmWave(-0.3, "f32").value(0.3), role="slave" if sync else "free",
                                   sync=r1.ev if sync else None, law="f32")
                    for got, want in ((v.y1, r1.y), (v.B, B), (v.di2, di2), (v.y2, r2.y), (v.q1, crush(r1.y, bits[0], dc=d1)),
                                      (v.q2, crush(r2.y, bits[1]))):
                        self.assertTrue(np.array_equal(got, want), (sync, bits))
                    p = self.case(n=256, sync=sync, bits=bits, k=(0.9, -0.3))  # two parts of 128 = one call of 256
                    h = self.case(n=128, sync=sync, bits=bits, k=(0.9, -0.3))
                    inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
                    m2, _ = sub_mod("sine", isub, 256, 0.2); a2 = _f32(isub * m2)
                    h2 = fm12_voice(128, inc, 0.9, inc * 1.5, -0.3, a=a2[128:], is1=(2.0, 0.0), is2=(8.0, 0.0), i12=(0.72, 0.0),
                                    bits=bits, state1=(h.r1.phase, h.r1.delayed), state2=(h.r2.phase, h.r2.delayed), sync=sync, off=128)
                    self.assertTrue(np.array_equal(np.r_[h.y2, h2.y2], p.y2) and np.array_equal(np.r_[h.B, h2.B], p.B), (sync, bits))
                    self.assertEqual((h2.r2.phase, h2.r2.delayed), (p.r2.phase, p.r2.delayed))

        def test_the_fold_order_is_a_statement(self):            # ab / ba differ in rounding only: read it off the kernel
            a = self.case(n=2048); inc = self.NOTE60 * 4
            isub = float(np.float32(inc / 2)); m, _ = sub_mod("sine", isub, 2048, 0.2); A = _f32(isub * m)
            ab = increments(inc * 1.5, [(np.full(2048, 8.0), A), (np.full(2048, 0.72), a.B)], law="f32")
            ba = increments(inc * 1.5, [(np.full(2048, 0.72), a.B), (np.full(2048, 8.0), A)], law="f32")
            self.assertTrue(np.array_equal(ab, a.di2))
            d = np.abs(ab - ba); self.assertGreater(int(np.sum(d > 0)), 0)
            self.assertLessEqual(float(np.max(d)), 2.0 ** -24)

        def test_sync_master_and_slave_under_both_routes(self):  # OD5: every master wrap, either way, resets the slave
            for sub, back in ((False, False), (True, True)):
                v = self.case(n=2048, sync=True, sub=sub, i12=(8.0, 0.0), k=(-0.75, -0.5), is1v=8.0)   # OSC1 -1 at a wrap: di2 < 0
                ev = v.r1.ev; e1, e2 = v.r1.edges, v.r2.edges
                self.assertEqual(int(np.sum(ev != EV_NONE)), e1["wrap_f"] + e1["wrap_b"])
                self.assertEqual(e1["wrap_b"] > 0, back, "OSC1's own through-zero Sub FM writes backward bytes")
                self.assertEqual(e2["resets"], int(np.sum((ev != EV_NONE) & (v.di2 != 0))))
                self.assertGreater(e2["resets_b"], 0, "the slave goes through zero at some resets (p = 1)")
                self.assertTrue(np.any(v.di2 < 0) and np.any(v.di2 > 0))
                free = self.case(n=2048, sync=False, sub=sub, i12=(8.0, 0.0), k=(-0.75, -0.5), is1v=8.0)
                self.assertTrue(np.array_equal(free.y1, v.y1) and np.array_equal(free.B, v.B))   # the master's sound is its own
                self.assertGreater(float(np.max(np.abs(free.y2 - v.y2))), 1e-2)
                mu = fm_render(v.di2, k=-0.5, phase=0.3, delayed=FmWave(-0.5, "f32").value(0.3), role="slave", sync=ev,
                               law="f32", mutant="reset_p0")
                self.assertGreater(mu.edges["wrap_b"], e2["wrap_b"], "the double edge (6.6) counts")

        def test_bessel_sine_to_sine(self):                      # 6.3: OSC1 Shape 0 -> OSC2 Shape 0, ratio 1 and 3:1
            n = 4096
            for cc, cm, I, law in ((11, 11, 1.0, "f64"), (11, 11, 1.0, "f32"), (11, 11, an_index32(354), "f32"),
                                   (33, 11, 2.0, "f32"), (7, 21, 1.0, "f32")):
                inc2, inc1 = cc / n, cm / n
                v = fm12_voice(n, inc1, -1.0, inc2, -1.0, i12=I, state1=(0.13, None), state2=(0.29, None), law=law)
                if law == "f64":
                    chk = fm12_bessel(v.y2, cc, cm, I, 0.29, 0.13, eps_c=0, eps_m=0, eps32=1e-10)
                else:
                    chk = fm12_bessel(v.y2, cc, cm, I, 0.29, 0.13, walk=fm_phase_walk(v.di2, 0.29))
                self.assertTrue(chk["ok"], (cc, cm, I, law, chk["worst"], chk["tol"]))
                for mu, args in (("b_late", {}), (None, dict(i12=I * 1.02)), ("after_level", dict(level1=0.98))):
                    kw = dict(i12=I, state1=(0.13, None), state2=(0.29, None), law=law, mutant=mu); kw.update(args)
                    w = fm12_voice(n, inc1, -1.0, inc2, -1.0, **kw)
                    self.assertFalse(fm12_bessel(w.y2, cc, cm, I, 0.29, 0.13)["ok"], (cc, cm, mu, args))
            tol = bessel_tol(1.0, 11 / n, eps_m=FM12_EPS_M)          # 6.3's need: 0.5 dB on J3 at I = 1, ratio 1
            j3 = 2 * abs(fm_sine_expect(n, 11, 11, 1.0, 0.0, 0.0)[4 * 11])
            self.assertLess(20 * np.log10(1 + 2 * tol / j3), 0.5)

        def test_no_dc_drift_over_100_blocks(self):              # 6.3, I = 8 through zero, OSC1 free (no own FM)
            inc, N = self.NOTE60, self.N100
            for shape in (48, 88, 127):
                k = k_of_shape(shape)
                for b in (None,) + BITS:
                    v = fm12_voice(N, inc, k, inc, 0.0, i12=8.0, bits=(b, None), state1=(0.1, None), state2=(0.3, None))
                    self.assertLess(float(v.di2.min()), 0.0, "through zero")
                    w = whole_cycles(v.di1, 0.1); d = drift(v.di2, inc, w)
                    self.assertTrue(d["ok"], (shape, b, d))
                    c = crush_drift(v.y1, b, inc, 8.0, inc, dc=wave_dc(k), window=w)
                    if CRUSH_DRIFT_CENTS.get(b) is not None:
                        self.assertLessEqual(abs(c["mod"]), CRUSH_DRIFT_CENTS[b], (shape, b, c))
                    else:
                        self.assertLessEqual(abs(c["mod"]), 1.0, (shape, b, c))   # these Shapes' extremes are on the grid
                    if shape == 88 and b is not None:             # the grid's DC (4.7: -3 / -55 / -1200 cents)
                        self.assertLess(c["grid"], {12: -2.0, 8: -40.0, 4: -1000.0}[b], (b, c))
                        g = fm12_voice(N, inc, k, inc, 0.0, i12=8.0, bits=(b, None), state1=(0.1, None), state2=(0.3, None),
                                       mutant="audio_grid")
                        self.assertEqual(drift(g.di2, inc, w)["ok"], b == 12, (b, "12 bits: under a bin; crush_drift sees it"))

        def test_the_crush_dc_od15_leaves(self):                 # found in Q6 (M): rounding of extremes between levels
            # OD15 removes the top clip's -f_hi / S. What is left is the rounding's own mean over the wave's values, nonzero
            # where an extreme falls between two levels: Shape 36 peaks at tw = 0.474 (k = -0.237), which rounds up to 0.5 at
            # 4 bits on every cycle; under OSC1's own Sub FM the phase also dwells (di1 -> 0) on a few values. A steady rate:
            # travel grows with the window (M: +9.8 cycles in 100 blocks, +38.6 in 400, note 96).
            for shape, note, I1, b, lo, hi in ((36, 96, 0.0, 4, 20.0, 40.0), (36, 60, 0.0, 4, 10.0, 25.0),
                                               (75, 36, 2.0, 4, -160.0, -100.0), (36, 96, 0.0, 8, -1.0, 1.0),
                                               (36, 96, 0.0, 12, -1.0, 1.0)):
                inc = float(np.float32(440 * 2 ** ((note - 69) / 12) / 96000)); isub = float(np.float32(inc / 2))
                m, _ = sub_mod("sine", isub, self.N100, 0.1); k = k_of_shape(shape)
                v = fm12_voice(self.N100, inc, k, inc, 0.0, a=_f32(isub * m), is1=I1, i12=8.0, bits=(b, None),
                               state1=(0.1, None), state2=(0.3, None))
                c = crush_drift(v.y1, b, inc, 8.0, inc, dc=wave_dc(k))
                self.assertTrue(lo < c["mod"] < hi, (shape, note, I1, b, c))      # M: +30.0, +16.6, -139.2, then < 1
                if b == 4: self.assertEqual(c["mod"], c["grid"], "no sample reaches the top: OD15 changes nothing here")

        def test_sub_fm_has_no_dc_drift(self):                   # 6.3 for Sub FM (carried from Q5): OD10's DC-free square
            inc, N = self.NOTE60, self.N100; isub = float(np.float32(inc / 2))
            for wave, pw, raw in (("square", 0.3, False), ("square", 0.3, True), ("sine", 0.5, False), ("triangle", 0.5, False)):
                m, _ = sub_mod(wave, isub, N, 0.1, pw)
                if raw: m = m + (2 * pw - 1)
                v = fm12_voice(N, inc, 0.0, inc, 0.0, a=_f32(isub * m), is2=8.0, state1=(0.1, None), state2=(0.3, None))
                self.assertLess(float(v.di2.min()), 0.0)
                d = drift(v.di2, inc, whole_cycles(np.full(N, isub), 0.1))
                self.assertEqual(d["ok"], not raw, (wave, raw, d))

        def test_osc1s_own_sub_fm_puts_dc_in_b(self):            # found in Q6 (M): a real DC, not a crush artefact
            # A sub an octave below OSC1 is commensurate: OSC1's FM spectrum has its sideband f1 - 2 f_sub at 0 Hz, so its
            # (correct, band-limited) render has a mean, and FM 1>2 turns that mean into an OSC2 detune of I12 inc1 mean(y1).
            inc, N = self.NOTE60, self.N100; isub = float(np.float32(inc / 2))
            m, _ = sub_mod("sine", isub, N, 0.1); a = _f32(isub * m); w = whole_cycles(np.full(N, isub), 0.1)
            v = fm12_voice(N, inc, 0.0, inc, 0.0, a=a, is1=2.0, i12=8.0, state1=(0.1, None), state2=(0.3, None))
            t0, t1 = w; dc = float(np.mean(v.y1[t0:t1]))
            self.assertGreater(dc, 0.1)                           # M: +0.126 (Sub FM 50 % on a saw OSC1)
            d = drift(v.di2, inc, w)
            self.assertFalse(d["ok"]); self.assertGreater(d["cents"], 1000)   # M: about +1200 cents at FM 1>2 100 %
            self.assertAlmostEqual(d["cycles"], 8.0 * float(np.sum(v.B[t0:t1])), delta=1e-6 * abs(d["cycles"]) + 1e-9)
            u = fm12_voice(N, inc, 0.0, inc, 0.0, a=a, is1=0.0, i12=8.0, state1=(0.1, None), state2=(0.3, None))
            self.assertTrue(drift(u.di2, inc, whole_cycles(u.di1, 0.1))["ok"])  # without OSC1's own FM: none

        def test_the_fir_and_decimate(self):
            h = np.asarray(STOCK_FIR)
            self.assertEqual(len(h), 11); self.assertTrue(np.array_equal(h, h[::-1]))
            self.assertAlmostEqual(float(h.sum()), 0.95105146, delta=1e-7)     # S: a DC gain of -0.44 dB
            n = 4096; j = np.arange(n)
            for c in (11, 301, 1501):                             # a tone at c / n x 96 kHz: decimated, scaled by |H|
                x = np.cos(2 * np.pi * c * j / n); y = decimate(x)
                self.assertAlmostEqual(float(np.max(np.abs(np.fft.rfft(y)))) / (n / 4), float(fir_response(c / n * 96000)),
                                       delta=1e-9)
            self.assertTrue(np.array_equal(decimate(x, (0, 1)), x[1::2]))

        def test_aliasing_after_the_fir_is_below_48k(self):      # 6.3: the same voice rendered at 48 kHz (the old design)
            for case in (("fm12", 149, 149, 4.0, 0.0, -1.0), ("fm12", 591, 591, 4.0, 0.0, 0.0), ("sub", 1182, 591, 8.0, 0.0, -1.0)):
                a48 = alias_case(*case, fs=48000, n48=8192); a96 = alias_case(*case, fs=96000, n48=8192)
                self.assertLess(a96, a48 - 3.0, (case, a48, a96))     # M (n48 16384): 5.2 .. 11.3 dB below
                if case[0] == "fm12" and case[1] == 149:            # the FIR is load-bearing: plain decimation is worse
                    self.assertGreater(alias_case(*case, fs=96000, n48=8192, taps=(0, 1)), a48)
            with self.assertRaises(ValueError): alias_case("sub", 298, 149, 4.0, 0.0, wave="square")

    L = unittest.defaultTestLoader
    r = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([L.loadTestsFromTestCase(c) for c in (BitDepth, Shapes, FM, FM12)]))
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(_selftest())
