# SPDX-License-Identifier: GPL-3.0-or-later
"""P3 independent host golden model (docs/plan-p3-fm-bits.md 6.3): numpy, float64, shares no C code.

    python p3_golden.py          # self-test (no image, no build dir)

Written from the plan text only (4.6, 4.7, OD6, OD4), before and without reading the DSP sources: Q3's shapes below the saw,
Q5's sub waves as modulators (OD10), the through-zero FM phase, the sync roles with the slave reset rule (4.3, OD5) and the
Bessel check; Q6's FM 1>2 (buffer B, the two-oscillator voice, the DC-drift and aliasing checks: last).
BE4.42 (docs/plan-p3-fm-bits.md, BE4.42; Jeff's device feedback 2026-11-16): Bit Depth is removed from the build, and with it
this file's Q2 / Q2b quantiser (crush, its grid, levels, probe and mutants), OD15's modulator grid (crush_mod) and the crush's
drift figure (crush_drift): B is OSC1's own pre-Level block times its increment. The FM index law is deeper: FD3 is now
I = 16 (v/1000)^1.5 (BE4.41: 8 (v/1000)^2), fm_index / an_index32 below.

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
- Index (FD3, BE4.42): fm_index(v) = 16 (v/1000)^1.5, v clamped 0..1000 (BE4.41's 8 (v/1000)^2 is the "square" mutant);
  index_ramp(v0, v1, N, off, n): I linear from I(v0) at sample 0 to I(v1) at sample N (so a part continues the block's ramp, and a held next block continues it with no step).
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

FM 1>2, OSC1 -> OSC2 (Q6; 4.1, 4.4 E3 (a), 4.5, 4.7 BD1, OD5, 6.3, 6.6): written from the plan text, this file's Q5 part and
test_analog's harness API (fm_golden, fm_capture, FmBench), before and without reading any FM 1>2 implementation (no DSP
C/C++ read for it). Stock's FIR taps are stock data read off the 1.6.8 image (S, below).
- Modulator (BD1): B[i] = f32(f32(inc1) s[i]), s OSC1's pre-Level block (BE4.42: no Bit Depth, so no grid on it; BE4.41's
  OD15 grid is gone with it). inc1 OSC1's base increment (kin = I inc1, 4.1), not its FM'd di1. Level never enters (E3 (a):
  OSC1 at Level 0 modulates the same). b_buffer; mutants after_level, b_late, kin_fm.
- Index (FD3, BE4.42 as built): an_index32(v) = f32(f32(v sqrt32(v)) f32(16 / 1000^1.5)), sqrt32 the correctly rounded
  float32 square root (VSQRT); index_line(v0, v1, N) = (ia, dia), dia = f32(f32(I1 - I0) / N) or 0; line_values:
  I_j = f32(ia + dia j) over the block frame j (m12's ramp the same rule as msub's, I: 4.1).
- Voice: fm12_voice(n, inc1, k1, inc2, k2, a, is1, is2, i12, state1, state2, sync, level1, off, law, fma, widths, order,
  mutant) -> Voice12(y1, B, di1, r1, di2, y2, r2): OSC1 = inc1 + I_s1 A (master when sync), B from y1, OSC2 = inc2 + I_s2 A
  + I_12 B folded in `order` ("ab" = 4.3's text), slave on OSC1's bytes when sync (OD5). Chained calls with off and the
  returned states are one call (state is (phase, delayed)). "no_b" mutant: OSC2 ignores B (useB false).
- Drift (6.3): drift(di, inc, window) {L, travel, expect, cycles, cents, ok |cycles| <= DRIFT_BIN = 1}; whole_cycles(di_mod)
  a window of whole forward modulator cycles.
- Aliasing (6.3): STOCK_FIR, decimate(y, taps) (circular, one output per two inputs), fir_response, alias_db(y, cycles)
  (off-harmonic / harmonic energy, plan-analog-fm 4.3's metric), alias_case(route, cc, cm, I, k_c, k_m, wave, fs, n48).
- Bessel (6.3): fm12_bessel(y2, cc, cm, I, pc0, qm0, walk): OSC1 at Shape 0 from qm0 plays -cos 2 pi (qm0 + i inc1), so B is
  fm_sine_time's modulator with phi = pi; eps_m = FM12_EPS_M = SINQ_ERR + TOL_F32 (OSC1's stated sample error).

Tolerances, and why (M: the self-test, scratch p3/q6g; I where marked):
- **B: bit for bit (tolerance 0).** The product of two float32 values is exact in float64, so one rounding. The form
  f32(f32(inc1) s) is a statement of the kernel's arithmetic (an_feed): restate it from the disassembly if that changes;
  never a tolerance.
- **OSC2 sample by sample: Q5's rules unchanged** (law "f32", fma, the same di bit for bit: feed the kernel's own A and B and
  lines; end phase equal first; fm_tol by OSC2's k and role; master bytes +-1 on ev_near). The fold order of the two terms
  ("ab" / "ba") changes di by at most 2^-24 (M) and is a statement: read it off the kernel.
- **Bessel, sine -> sine (FM 1>2):** bessel_tol with FM12_EPS_M: 3.80e-4 a bin at I = 1 (0.34 dB on the J3 bin at ratio 1,
  under 6.3's 0.5 dB), 6.68e-4 at I = 2. The golden's own f32 renders use 0.07-0.4 % of it (ratio 1 at I = 1 and
  an_index32(157) (BE4.42's law near I = 1; BE4.41 used an_index32(354) = 1.0025280 of its own law), 3:1 at I = 2, 1:3
  at I = 1); B one sample late, the index 2 % off and y1 after a Level of 0.98 fail by 17-31 x it.
- **DC drift (6.3, 100 blocks, I = 8 through zero):** travel over whole modulator cycles within DRIFT_BIN (1 cycle, Q5's
  Square-sub precedent): Sub FM by a DC-free Square at PW 30 %, a sine and a triangle sub pass, the raw Square fails (-106
  cycles); FM 1>2 from a free OSC1 at Shapes 48 / 88 / 127 passes (worst 0.03 cycles; BE4.42: also at I = 16, the new
  law's 100 %). Over an arbitrary window a zero-mean modulator's partial cycle alone moves the travel by up to I max|m| / 2 cycles
  (|integral of a zero-mean wave over a part cycle| <= P max|m| / 2), so use whole cycles or a coherent block.
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
  one OD15 removed (BE4.42: the deeper law doubles I12 at 100 %, 16 for 8, so this detune doubles with it). Options for
  Jeff (not this file's call): accept (FM character); a DC blocker on B (a one-pole high-pass per block, a few
  instructions a sample, B no longer bit for bit OSC1's s); or B from OSC1 before its own Sub FM
  (not available: one render). The 6.3 drift check is therefore stated for a free OSC1; with OSC1's own FM the golden
  predicts the drift exactly (I12 sum(B)) and a test should compare against it, not against 0.

BE4.43 FM envelope (Jeff's design 2026-11-16, docs/plan-p3-fm-bits.md BE4.43), written from the design text before any M7
envelope code, so this is the spec the M7 follows: fm_env_blocks / fm_env_consts / fm_env / fm_env_lines below.
- Per oscillator: FM Atk a 0..127, FM Dec d 0..128 (FM_DEC_INF = 128, shown Inf: the top of the knob is the longest decay,
  one byte, and the stored default 128 is distinct from every time). Effective index = the amount's index x env, env at
  block rate (T_BLOCK = 128 frames at 48 kHz = 2N = 256 at 96 kHz, 2.667 ms), linear between block boundaries.
- Time curve (I): n(x) = 1 + round((e^(L x) - 1) / L) blocks, L = 0.0393823365313980 (e^(127 L) - 1 = 3749 L): 1 block
  (2.7 ms) at 0, 10.0 s (3750) at 127, slope 1 block a step at 0 so all 128 values are distinct block counts (a pure
  exponential 2.7 ms .. 10 s collapses to 100 distinct counts at block rate), about 4 % a step at the top. 20 = 83 ms,
  32 = 173 ms, 48 = 384 ms, 64 = 776 ms, 80 = 1.52 s, 96 = 2.90 s, 112 = 5.51 s. Atk 0 is instant (not n(0)).
- Shape (I): attack linear in index, 0 -> 1 over n_a blocks (e_k = k / n_a): the index is already the brightness control
  (sidebands about I + 1), so linear in I is an even swell and lands exactly on 1 by a counter; decay exponential,
  e = 0.001^(j / n_d) (-60 dB at n_d blocks: the time a Dec value names), then 0 (at most 0.016 index at 100 %, no
  denormals). Inf holds 1. A note start resets the envelope (hard retrigger, per voice): e0 = 0 (1 for Atk 0).
- Stated float32 arithmetic (law f32): step = f32(1 / n_a), r = f32(0.001^(1 / n_d)); attack f32(f32(k) step), 1.0 at
  k = n_a; decay 1.0 then f32(e r) a block; every stage change by a block counter, never a float compare. Lines:
  ia = f32(I(v0) e0), end = f32(I(v1) e1), dia = f32(f32(end - ia) / N), index_line's rule, so Atk 0 / Dec Inf is
  index_line bit for bit. M: |f32 - f64| env 4.0e-6 at Dec 127 (FM_ENV_TOL 4e-5 the stated bound).
- Cost: per oscillator-block a counter, one VMUL for the envelope, two VMULs on the line's ends; nothing a sample.
"""
import math, sys
import numpy as np

def _x(x, f32):
    x = np.asarray(x, dtype=np.float64)
    if f32: x = x.astype(np.float32).astype(np.float64)        # the kernel's s is float32 (no-op on captured samples)
    if not np.all(np.isfinite(x)): raise ValueError("non-finite input")
    return x


def _f32(v): return np.asarray(v, dtype=np.float64).astype(np.float32).astype(np.float64)


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


FM_LAWS = ("pow15", "square", "linear")


def fm_index(v, law="pow15"):
    """FD3 (BE4.42): Sub FM / FM 1>2 amount v (0..1000, clamped as an_prop stores it) -> index I = 16 (v/1000)^1.5, 0..16
    (50 % 5.66, 25 % 2). Mutants, for tests that must turn red on them: "square" is BE4.41's law 8 (v/1000)^2 (0..8),
    "linear" 6.6's 16 v/1000."""
    if law not in FM_LAWS: raise ValueError(f"law must be one of {FM_LAWS}")
    v = np.clip(np.asarray(v, dtype=np.float64), 0, 1000) / 1000
    I = 16 * v * np.sqrt(v) if law == "pow15" else 8 * v * v if law == "square" else 16 * v
    return float(I) if I.ndim == 0 else I


def index_ramp(v0, v1, N, off=0, n=None, law="pow15"):
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


# ---- Q6: FM 1>2, OSC1 -> OSC2 (4.1, 4.4 E3 (a), 4.5, 4.7 BD1, OD5; 6.3, 6.6) ----------------------------------------------
FM12_MUTANTS = ("after_level", "b_late", "kin_fm", "no_b")
FM12_EPS_M = SINQ_ERR + TOL_F32     # OSC1 as the Bessel modulator: sinq's stated error + its float32 sample bound (6.3)
DRIFT_BIN = 1.0                     # 6.3 / Q5's precedent: the mean frequency within one bin of the window (1 cycle)
# Stock 1.6.8's 2x decimator 0x0807FCC0 (S: its 11 VFMA taps, the literals at 0x0807FDD0..0x0807FDE7 as its chain reads
# them: x[1] s10, x[0] s11, x[2] s12, x[3] s13, x[4] s14, x[5] s0, x[6] s14, x[7] s13, x[8] s12, x[9] s10, x[10] s11; one
# output per two inputs). Data read off the stock image, not our C; a test should re-read the six words and assert them.
_FIR6 = (-0.044159356504678726, 0.05654638633131981, -0.09867829829454422, 0.027138808742165565, 0.30596286058425903,
         0.4574306607246399)        # s10, s11, s12, s13, s14, s0
STOCK_FIR = (_FIR6[1], _FIR6[0], _FIR6[2], _FIR6[3], _FIR6[4], _FIR6[5], _FIR6[4], _FIR6[3], _FIR6[2], _FIR6[0], _FIR6[1])


def b_buffer(s, inc1, level=1.0, mutant=None, di1=None, f32=True):
    """Buffer B (4.1, BD1): B[i] = f32(f32(inc1) s[i]), s OSC1's pre-Level block, inc1 OSC1's base increment (before its own
    FM: kin = I inc1, 4.1). One rounding: the product of two float32 values is exact in float64. Level never enters (BD1:
    before Level; E3 (a): a silent OSC1 still modulates); `level` exists for the mutant. That form is a statement of the
    kernel's arithmetic (an_feed; read it off the disassembly): compare B bit for bit. BE4.42: no Bit Depth, so no grid
    (BE4.41's OD15 crush_mod and its before_crush / audio_grid mutants are retired).
    mutant (6.6 and plausible slips): "after_level" (B = inc1 f32(level s): y1 taken after Level), "b_late" (B[i] from
    OSC1's sample i - 1), "kin_fm" (kin from OSC1's FM'd increment di1, not its base inc1). f32 False: all in float64."""
    if mutant not in (None,) + FM12_MUTANTS: raise ValueError(f"mutant must be one of {FM12_MUTANTS}")
    r = _f32 if f32 else (lambda v: np.asarray(v, dtype=np.float64))
    q = _x(s, f32)
    if mutant == "after_level": q = r(q * float(r(level)))
    if mutant == "b_late": q = np.r_[q[:1], q[:-1]]
    k = r(di1) if mutant == "kin_fm" else float(r(inc1))
    return r(k * q)


def an_index32(v):
    """FD3's index in the kernel's stated arithmetic (BE4.42 as built: an_index, I(v) = f32(f32(v sqrt32(v)) K),
    K = f32(16 / 1000^1.5), sqrt32 the correctly rounded float32 square root, VSQRT; v is exact in float32): fm_index within
    float32 rounding; exact 2 at 250 and 16 at 1000. v clamped 0..1000 as an_prop stores it."""
    v = np.float32(min(max(int(v), 0), 1000))
    p = np.float32(v * np.sqrt(v))                              # numpy's float32 sqrt is IEEE correctly rounded
    return float(np.float32(p * np.float32(16.0 / 1000.0 ** 1.5)))


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


# ---- BE4.43 FM envelope (Jeff's design 2026-11-16; docs/plan-p3-fm-bits.md, BE4.43): the spec the M7 follows -------------
# Written from the design text only, before any M7 envelope code exists. Per oscillator: FM Atk a (0..127), FM Dec d (0..127,
# FM_DEC_INF = 128 shown "Inf"). The effective index = the amount's index (Sub FM / FM 1>2, FD3) x env. All block rate.
T_BLOCK = 128 / 48000.0             # one voice block: 128 frames at 48 kHz = 2N = 256 frames at 96 kHz, 2.667 ms
FM_ENV_N = 256                      # the index line's length in the 96 kHz domain (index_line's N)
FM_DEC_INF = 128                    # FM Dec's top value: hold 1 (BE4.42's static depth)
FM_ENV_FLOOR = 1e-3                 # the decay's end level, -60 dB: the time a Dec value names is the time to reach it
FM_ENV_MAX_BLOCKS = 3750            # 10.0 s / T_BLOCK, exactly
FM_ENV_MUTANTS = ("lin_decay", "exp_attack", "no_floor", "env_end_late", "no_retrig")


def _fm_env_L():
    """L solving e^(127 L) - 1 = (FM_ENV_MAX_BLOCKS - 1) L (bisection, float64): the curve's slope is 1 block a step at 0."""
    lo, hi = 1e-3, 1.0
    for _ in range(200):
        m = (lo + hi) / 2
        if math.expm1(127 * m) - (FM_ENV_MAX_BLOCKS - 1) * m > 0: hi = m
        else: lo = m
    return lo


FM_ENV_L = _fm_env_L()              # 0.0393823365..., e^L = 1.04017 a step at the top
FM_ENV_BLOCKS = tuple(1 + int(round(math.expm1(FM_ENV_L * x) / FM_ENV_L)) for x in range(128))


def fm_env_blocks(x):
    """The time curve, in whole blocks (the spec; the M7 holds this 128-entry table, or computes it with the same rounding):
    n(x) = 1 + round((e^(L x) - 1) / L), x = 0..127, L = FM_ENV_L. An offset exponential: n(0) = 1 block (2.67 ms), slope
    1 block a step at 0 (so every value is a distinct block count: 128 distinct times, 1 .. 3750 blocks), n(127) = 3750 =
    10.0 s, about 4 % a step at the top. Atk 0 is instant (not n(0)); Dec 128 is Inf (no n)."""
    x = int(x)
    if not 0 <= x <= 127: raise ValueError("x must be 0..127")
    return FM_ENV_BLOCKS[x]


def fm_env_time(x, stage):
    """Seconds a value names: stage "atk": 0 -> 0 (instant), else n(x) T_BLOCK (0 -> 1 linear); "dec": 128 -> inf, else
    n(x) T_BLOCK (1 -> FM_ENV_FLOOR, then 0). For the labels (ms below 1 s, s above: the UI's choice)."""
    if stage not in ("atk", "dec"): raise ValueError("stage must be atk or dec")
    x = int(x)
    if stage == "atk" and x == 0: return 0.0
    if stage == "dec" and x == FM_DEC_INF: return math.inf
    return fm_env_blocks(x) * T_BLOCK


def _f32s(v): return float(np.float32(v))


def fm_env_consts(atk, dec):
    """The per-oscillator constants the M7 derives when FM Atk / FM Dec change (stated float32 arithmetic):
    (n_a, step, n_d, r). n_a: attack blocks (0 for Atk 0); step = f32(1 / n_a) (0 for Atk 0); n_d: decay blocks (None for
    Inf); r = f32(FM_ENV_FLOOR ^ (1 / n_d)) the per-block decay factor (1 for Inf; Dec 0: n_d 1, r 0.001)."""
    a, d = int(atk), int(dec)
    if not 0 <= a <= 127: raise ValueError("atk must be 0..127")
    if not 0 <= d <= FM_DEC_INF: raise ValueError("dec must be 0..128")
    n_a = 0 if a == 0 else fm_env_blocks(a)
    n_d = None if d == FM_DEC_INF else fm_env_blocks(d)
    return n_a, (0.0 if n_a == 0 else _f32s(1.0 / n_a)), n_d, (1.0 if n_d is None else _f32s(FM_ENV_FLOOR ** (1.0 / n_d)))


def _env_seq(atk, dec, count, law="f64", mutant=None):
    """env at the boundaries k = 0 .. count of one note (the spec, fm_env_at's rules), sequentially: the f32 decay is a
    recurrence, so this is the M7's own order of operations."""
    n_a, step, n_d, r = fm_env_consts(atk, dec)
    out = []; e = np.float32(1.0)
    for k in range(count + 1):
        if k < n_a:
            if mutant == "exp_attack": v = 1.0 - FM_ENV_FLOOR ** (k / n_a)
            else: v = _f32s(np.float32(k) * np.float32(step)) if law == "f32" else k / n_a
        else:
            j = k - n_a
            if n_d is None: v = 1.0
            elif j >= n_d and mutant != "no_floor": v = 0.0
            elif mutant == "lin_decay": v = 1.0 - j / n_d
            elif law == "f32":
                if j > 0: e = np.float32(e * np.float32(r))
                v = float(e)
            else: v = FM_ENV_FLOOR ** (j / n_d)
        out.append(v)
    return out


def fm_env_at(atk, dec, k, law="f64", mutant=None):
    """env at block boundary k >= 0 after a note start (k = 0: the note's first block's start). The continuous spec, at
    boundaries: attack (Atk > 0) e_k = k / n_a for k <= n_a (linear 0 -> 1; e_{n_a} = 1 exactly); Atk 0: e_0 = 1 (instant,
    n_a = 0). Then decay from k_a = n_a: Inf holds 1; else e_{k_a + j} = FM_ENV_FLOOR^(j / n_d) for j < n_d (exponential,
    -60 dB at n_d blocks), and 0 from j = n_d on (the end: inaudible, at most 0.016 index at 100 %, and no denormals).
    law "f32": the stated arithmetic the M7 follows (a block counter decides every stage change, so no float compare and
    no accumulated attack): attack e = f32(f32(k) step), e_{n_a} = 1.0 exactly; decay e_{k_a} = 1.0,
    e_{k_a + j} = f32(e_{k_a + j - 1} r) (one VMUL a block), 0 at j = n_d.
    Mutants (tests must turn red on them): "lin_decay" (1 - j / n_d), "exp_attack" (1 - FLOOR^(k / n_a)), "no_floor" (the
    decay never snaps to 0); fm_env adds "env_end_late" and "no_retrig"."""
    if int(k) < 0: raise ValueError("k must be >= 0")
    return _env_seq(atk, dec, int(k), law, mutant)[int(k)]


def fm_env(atk, dec, nblocks, notes=(0,), law="f64", mutant=None):
    """(e0, e1): env at the start and the end of each of nblocks voice blocks of one oscillator through notes (the blocks
    where a note starts on this voice, ascending, the first 0). A note start resets the envelope (retrigger per voice, hard:
    e0 of that block is e(0), 0, or 1 for Atk 0, whatever the last block ended on). Within a note, block b at boundary
    k = b - start: e0 = e_k, e1 = e_{k+1} (the M7 steps the envelope one block ahead: the line's end needs e_{k+1}).
    Mutants: fm_env_at's, "env_end_late" (e1 = e_k: the envelope lags a block, a staircase), "no_retrig" (only the
    first note starts it)."""
    notes = sorted(set(int(b) for b in notes))
    if not notes or notes[0] != 0: raise ValueError("notes must start at block 0")
    if mutant == "no_retrig": notes = [0]
    e0 = np.zeros(nblocks); e1 = np.zeros(nblocks)
    for i, s in enumerate(notes):
        end = min(notes[i + 1] if i + 1 < len(notes) else nblocks, nblocks)
        if s >= end: continue
        seq = _env_seq(atk, dec, end - s, law, mutant)
        e0[s:end] = seq[:end - s]
        e1[s:end] = seq[:end - s] if mutant == "env_end_late" else seq[1:end - s + 1]
    return e0, e1


def fm_env_lines(atk, dec, v0, v1, nblocks, notes=(0,), N=FM_ENV_N, law="f64", mutant=None):
    """The per-block index lines of one oscillator's FM (its own amount: OSC1 Sub FM; OSC2 FM 1>2, or Sub FM when OSC1 is not
    Analog) under its envelope. v0 / v1: the amount's per-block endpoints as today (last block's msub / m12 -> this block's;
    scalars: held), arrays of nblocks. Block b's line runs from I(v0_b) e0_b at frame 0 to I(v1_b) e1_b at frame N (the next
    block's first; index_line's end convention), linear between: so a held amount and a held envelope continue one ramp,
    and the envelope costs nothing per sample (the line's two endpoints are two VMULs a block).
    law "f64": a list of (start, end) float64 (fm_index; I_j = start + (end - start) j / N, index_ramp's form);
    law "f32": a list of (ia, dia) index lines in the kernel's stated arithmetic for line_values: ia = f32(I0 e0),
    end = f32(I1 e1), dia = f32(f32(end - ia) / N), 0 when equal (index_line's rule; I = an_index32, e the f32 envelope).
    Atk 0 / Dec Inf: e = 1, so the lines are index_line's bit for bit (BE4.42 projects sound unchanged)."""
    v0 = np.broadcast_to(np.asarray(v0, dtype=np.float64), (nblocks,))
    v1 = np.broadcast_to(np.asarray(v1, dtype=np.float64), (nblocks,))
    e0, e1 = fm_env(atk, dec, nblocks, notes, law, mutant)
    out = []
    for b in range(nblocks):
        if law == "f32":
            ia = _f32s(np.float32(an_index32(v0[b])) * np.float32(e0[b]))
            end = _f32s(np.float32(an_index32(v1[b])) * np.float32(e1[b]))
            out.append((ia, 0.0 if end == ia else _f32s(_f32s(end - ia) / N)))
        else:
            out.append((fm_index(v0[b]) * e0[b], fm_index(v1[b]) * e1[b]))
    return out


def fm_env_samples(lines, N=FM_ENV_N, law="f64"):
    """The per-sample index of consecutive blocks' lines (N frames each, 96 kHz domain): index_ramp's form for "f64",
    line_values for "f32" lines."""
    if law == "f32": return np.concatenate([line_values(l, 0, N) for l in lines])
    j = np.arange(N, dtype=np.float64) / N
    return np.concatenate([s + (e - s) * j for s, e in lines])


FM_ENV_TOL = 4e-5   # |f32 env - f64 env| bound: the decay's f32 recurrence, j (rounding of r + one VMUL) <= j 1.2e-7 r^j,
                    # max near j = n_d / ln 1000 (about 2.0e-5 at n_d = 3750); the attack's one rounding 6e-8. Times I <= 16.


class Voice12(tuple):
    """fm12_voice's result: (y1, B, di1, r1, di2, y2, r2). y1 OSC1's render (pre-Level), B buffer B, di1 / di2 the
    increments each oscillator rendered with, r1 / r2 their FmRender (end state, master bytes, edges), y2 OSC2's render.
    (BE4.42: BE4.41's audio crushes q1 / q2 are gone with Bit Depth.)"""
    __slots__ = ()
    _f = ("y1", "B", "di1", "r1", "di2", "y2", "r2")
    def __new__(cls, *a): return tuple.__new__(cls, a)
    def __getattr__(self, k):
        try: return self[self._f.index(k)]
        except ValueError: raise AttributeError(k) from None


def fm12_voice(n, inc1, k1, inc2, k2, a=None, is1=0.0, is2=0.0, i12=0.0, state1=(0.0, None),
               state2=(0.0, None), sync=False, level1=1.0, off=0, law="f32", fma=True, widths=(None, None),
               order="ab", mutant=None):
    """One voice-block (or a part of one: frames off .. off + n - 1) of OSC1 -> OSC2 (4.1), both oscillators Analog:
    - OSC1: di1 = inc1 + I_s1 A (increments, law / fma as stated; no Sub FM: inc1 alone, clamped), rendered on k1 in role
      "master" when sync else "free" (OD5: a byte at every wrap, either way).
    - B = b_buffer(y1, inc1) (BD1; level1 only reaches the "after_level" mutant: E3 (a)).
    - OSC2: di2 = inc2 + I_s2 A + I_12 B, folded in `order` ("ab": A first, 4.3's text; "ba"), rendered on k2 in role
      "slave" on OSC1's bytes when sync (OD5: any master wrap resets; backward to p = 1) else "free".
    a: buffer A from frame off (increment units, the sub copy: sub_a-style or the kernel's own), None for 0. is1 / is2 /
    i12: an index line (ia, dia) over the absolute frame (index_line), or a scalar. state1 / state2: (phase, delayed),
    delayed None -> the wave at phase (a steady start). widths: the kernel's float32 pulse widths (FmWave's width=). One call covers the whole range: chained calls on the returned states
    (r1 / r2 .phase / .delayed) with the next off are the same render (the golden's state is (phase, delayed) only), so
    AN_EVENTS chunks and Shape parts need no separate path here.
    mutant: FM12_MUTANTS: b_buffer's, plus "no_b" (OSC2 ignores B: 4.1's useB false)."""
    if order not in ("ab", "ba"): raise ValueError("order must be ab or ba")
    a = np.zeros(n) if a is None else np.asarray(a, dtype=np.float64)
    if a.size != n: raise ValueError("a must hold n samples (from frame off)")
    w1, w2 = FmWave(k1, law, widths[0]), FmWave(k2, law, widths[1])
    I1, I2, I12 = (line_values(x, off, n) for x in (is1, is2, i12))
    t1 = [(I1, a)] if np.any(I1 != 0) else []
    di1 = np.broadcast_to(increments(inc1, t1, law=law, fma=fma), (n,)).copy()      # no terms: one value
    p1, e1 = state1; p2, e2 = state2
    r1 = fm_render(di1, k=k1, phase=p1, delayed=w1.value(p1 % 1.0 if p1 != 1.0 else 1.0) if e1 is None else e1,
                   role="master" if sync else "free", law=law, fma=fma, width=widths[0])
    f32 = law == "f32"
    y1 = r1.y
    B = b_buffer(y1, inc1, level1, None if mutant == "no_b" else mutant, di1, f32)
    terms = [(I2, a), (I12, B)] if order == "ab" else [(I12, B), (I2, a)]
    terms = [t for t in terms if np.any(t[0] != 0) and not (mutant == "no_b" and t[1] is B)]
    di2 = np.broadcast_to(increments(inc2, terms, law=law, fma=fma), (n,)).copy()
    r2 = fm_render(di2, k=k2, phase=p2, delayed=w2.value(p2 % 1.0 if p2 != 1.0 else 1.0) if e2 is None else e2,
                   role="slave" if sync else "free", sync=r1.ev if sync else None, law=law, fma=fma, width=widths[1])
    return Voice12(y1, B, di1, r1, di2, r2.y, r2)


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
    is below it."""
    return bessel_check(y2, cc, cm, I, pc0, qm0, np.pi, eps_c, eps_m, eps32, walk)


# ---- self-test ----------------------------------------------------------------------------------------------------------
def _selftest():
    import unittest

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

        def test_index_law_and_ramp(self):                       # FD3 (BE4.42): I = 16 (v/1000)^1.5, the ramp linear over 2N
            got = fm_index([0, 250, 1000, 5000, -3, 500, 750])
            self.assertEqual(list(got[:5]), [0, 2, 16, 16, 0])
            self.assertAlmostEqual(float(got[5]), 8 / math.sqrt(2), delta=1e-12)               # 50 %: 5.657 (BE4.41: 2)
            self.assertAlmostEqual(float(got[6]), 16 * 0.75 ** 1.5, delta=1e-12)               # 75 %: 10.39 (BE4.41: 4.5)
            self.assertEqual(list(fm_index([250, 500, 1000], law="square")), [0.5, 2, 8])     # BE4.41's law (a mutant now)
            self.assertEqual(float(fm_index(500, law="linear")), 8.0)               # the 6.6 mutant
            self.assertTrue(np.all(fm_index(np.arange(1, 1000)) > fm_index(np.arange(1, 1000), "square")))   # deeper everywhere
            r = index_ramp(0, 1000, 256); self.assertEqual((r[0], r[128]), (0.0, 8.0))
            self.assertTrue(np.array_equal(np.r_[index_ramp(300, 700, 256, 0, 128), index_ramp(300, 700, 256, 128)],
                                           index_ramp(300, 700, 256)))           # chunked parts continue one ramp
            nxt = index_ramp(700, 700, 256)[0]; self.assertAlmostEqual(r[-1] + (r[1] - r[0]), 16.0, delta=1e-12)
            self.assertEqual(nxt, fm_index(700))
            with self.assertRaises(ValueError): fm_index(500, law="cube")

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

    class FM12(unittest.TestCase):                               # Q6 (4.1, 4.4 E3 (a), BD1, OD5, 6.3)
        NOTE60 = float(np.float32(440 * 2 ** (-9 / 12) / 96000))   # note 60 at 96 kHz (the device's domain)
        N100 = 100 * 256                                         # 100 device blocks (2N = 256)

        def test_b_buffer(self):                                 # BD1 form, one rounding; the mutants differ
            rng = np.random.default_rng(4421); n = 512
            s = _f32(np.sign(np.sin(2 * np.pi * np.arange(n) / 61)) * 0.999 + 0.001 * rng.standard_normal(n))
            inc1 = 0.0027250234
            want = (np.float32(inc1) * s.astype(np.float32)).view(np.uint32)
            self.assertTrue(np.array_equal(b_buffer(s, inc1).astype(np.float32).view(np.uint32), want))
            self.assertTrue(np.array_equal(b_buffer(s, inc1, level=0.0), b_buffer(s, inc1)))   # Level never enters
            di1 = _f32(inc1 * (1 + 0.5 * np.sin(np.arange(n) / 9)))
            base = b_buffer(s, inc1)
            for mu in ("after_level", "b_late", "kin_fm"):
                got = b_buffer(s, inc1, level=0.5, mutant=mu, di1=di1)
                self.assertGreater(int(np.sum(got != base)), 0, mu)
            for mu in ("nope", "audio_grid", "before_crush"):    # (BE4.41's grid mutants: retired with Bit Depth)
                with self.assertRaises(ValueError): b_buffer(s, inc1, mutant=mu)

        def test_the_index_line(self):                           # FD3 in the kernel's float32 form; parts continue it
            for v in range(1001):                               # every amount: two float32 roundings (M: 1.7e-7 worst)
                self.assertLessEqual(abs(an_index32(v) - fm_index(v)), 2.0 ** -22 * max(fm_index(v), 2.0 ** -20), v)
            self.assertEqual((an_index32(250), an_index32(1000), an_index32(5000), an_index32(-3)), (2.0, 16.0, 16.0, 0.0))
            ia, dia = index_line(0, 1000); self.assertEqual(ia, 0.0)
            self.assertLessEqual(abs(ia + 256 * dia - 16.0), 1e-6 * 16)
            self.assertEqual(index_line(700, 700), (an_index32(700), 0.0))
            L = index_line(300, 900); Lv = line_values(L, 0, 256)
            self.assertTrue(np.array_equal(np.r_[line_values(L, 0, 128), line_values(L, 128, 128)], Lv))
            self.assertLessEqual(float(np.max(np.abs(Lv - (L[0] + L[1] * np.arange(256))))), 2.0 ** -21)   # one VFMA
            self.assertLessEqual(abs(Lv[-1] + L[1] - an_index32(900)), 1e-6 * 16)  # it reaches the next block's I
            self.assertTrue(np.array_equal(line_values(2.0, 5, 3), [2.0, 2.0, 2.0]))

        def case(self, n=512, sync=False, sub=True, level1=1.0, mutant=None, k=(0.0, 0.25), law="f32",
                 off=0, i12=(0.72, 0.0), states=((0.1, None), (0.3, None)), is1v=2.0):
            inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
            m, _ = sub_mod("sine", isub, n, 0.2); a = _f32(isub * m) if sub else None
            return fm12_voice(n, inc, k[0], inc * 1.5, k[1], a=a, is1=(is1v, 0.0) if sub else 0.0,
                              is2=(8.0, 0.0) if sub else 0.0, i12=i12, state1=states[0], state2=states[1],
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
                for bits in (None,):                            # (BE4.42: one case; BE4.41 also ran its Bit Depth pairs)
                    v = self.case(sync=sync, k=(0.9, -0.3))
                    inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
                    m, _ = sub_mod("sine", isub, 512, 0.2); a = _f32(isub * m)
                    di1 = increments(inc, [(line_values((2.0, 0.0), 0, 512), a)], law="f32")
                    r1 = fm_render(di1, k=0.9, phase=0.1, delayed=FmWave(0.9, "f32").value(0.1), role="master" if sync else "free", law="f32")
                    B = b_buffer(r1.y, inc)
                    di2 = increments(inc * 1.5, [(np.full(512, 8.0), a), (np.full(512, 0.72), B)], law="f32")
                    r2 = fm_render(di2, k=-0.3, phase=0.3, delayed=FmWave(-0.3, "f32").value(0.3), role="slave" if sync else "free",
                                   sync=r1.ev if sync else None, law="f32")
                    for got, want in ((v.y1, r1.y), (v.B, B), (v.di2, di2), (v.y2, r2.y)):
                        self.assertTrue(np.array_equal(got, want), (sync, bits))
                    p = self.case(n=256, sync=sync, k=(0.9, -0.3))  # two parts of 128 = one call of 256
                    h = self.case(n=128, sync=sync, k=(0.9, -0.3))
                    inc = self.NOTE60 * 4; isub = float(np.float32(inc / 2))
                    m2, _ = sub_mod("sine", isub, 256, 0.2); a2 = _f32(isub * m2)
                    h2 = fm12_voice(128, inc, 0.9, inc * 1.5, -0.3, a=a2[128:], is1=(2.0, 0.0), is2=(8.0, 0.0), i12=(0.72, 0.0),
                                    state1=(h.r1.phase, h.r1.delayed), state2=(h.r2.phase, h.r2.delayed), sync=sync, off=128)
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
            for cc, cm, I, law in ((11, 11, 1.0, "f64"), (11, 11, 1.0, "f32"), (11, 11, an_index32(157), "f32"),
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

        def test_no_dc_drift_over_100_blocks(self):              # 6.3, I = 8 and 16 (BE4.42's 100 %) through zero, OSC1 free
            inc, N = self.NOTE60, self.N100
            for shape in (48, 88, 127):
                k = k_of_shape(shape)
                for I in (8.0, 16.0):
                    v = fm12_voice(N, inc, k, inc, 0.0, i12=I, state1=(0.1, None), state2=(0.3, None))
                    self.assertLess(float(v.di2.min()), 0.0, "through zero")
                    w = whole_cycles(v.di1, 0.1); d = drift(v.di2, inc, w)
                    self.assertTrue(d["ok"], (shape, I, d))

        def test_sub_fm_has_no_dc_drift(self):                   # 6.3 for Sub FM (carried from Q5): OD10's DC-free square
            inc, N = self.NOTE60, self.N100; isub = float(np.float32(inc / 2))
            for wave, pw, raw in (("square", 0.3, False), ("square", 0.3, True), ("sine", 0.5, False), ("triangle", 0.5, False)):
                m, _ = sub_mod(wave, isub, N, 0.1, pw)
                if raw: m = m + (2 * pw - 1)
                v = fm12_voice(N, inc, 0.0, inc, 0.0, a=_f32(isub * m), is2=8.0, state1=(0.1, None), state2=(0.3, None))
                self.assertLess(float(v.di2.min()), 0.0)
                d = drift(v.di2, inc, whole_cycles(np.full(N, isub), 0.1))
                self.assertEqual(d["ok"], not raw, (wave, raw, d))

        def test_osc1s_own_sub_fm_puts_dc_in_b(self):            # found in Q6 (M): a real DC, not an artefact
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

    class FmEnv(unittest.TestCase):                              # BE4.43 (Jeff's design 2026-11-16): the FM envelope
        def test_the_time_curve(self):
            n = FM_ENV_BLOCKS
            self.assertEqual(len(n), 128); self.assertEqual((n[0], n[1], n[127]), (1, 2, FM_ENV_MAX_BLOCKS))
            self.assertTrue(all(b > a for a, b in zip(n, n[1:])))                   # 128 distinct times, monotone
            self.assertAlmostEqual(n[127] * T_BLOCK, 10.0, delta=1e-12)            # 3750 blocks = 10.0 s
            self.assertAlmostEqual(math.exp(FM_ENV_L), 1.04017, delta=1e-5)
            # the table is the contract: no entry is within 1e-6 of a rounding tie, so any careful generator agrees
            frac = [math.expm1(FM_ENV_L * x) / FM_ENV_L % 1.0 for x in range(1, 127)]
            self.assertGreater(min(abs(f - 0.5) for f in frac), 1e-6)
            self.assertEqual((fm_env_time(0, "atk"), fm_env_time(FM_DEC_INF, "dec")), (0.0, math.inf))
            self.assertAlmostEqual(fm_env_time(0, "dec"), T_BLOCK, delta=1e-15)
            with self.assertRaises(ValueError): fm_env_blocks(128)
            with self.assertRaises(ValueError): fm_env_consts(128, 0)
            with self.assertRaises(ValueError): fm_env_consts(0, 129)

        def test_defaults_are_bit_for_bit_be442(self):                             # Atk 0, Dec Inf: e = 1 everywhere
            for v0, v1 in ((0, 0), (250, 250), (157, 1000), (1000, 3), (499, 500)):
                L32 = fm_env_lines(0, FM_DEC_INF, v0, v1, 40, notes=(0, 7, 31), law="f32")
                self.assertTrue(all(l == index_line(v0, v1) for l in L32), (v0, v1))
                L64 = fm_env_lines(0, FM_DEC_INF, v0, v1, 40, notes=(0, 7, 31))
                self.assertTrue(all(l == (fm_index(v0), fm_index(v1)) for l in L64))

        def test_attack_by_hand(self):
            a = 20; na = fm_env_blocks(a); self.assertEqual(na, 31)               # 31 blocks = 82.7 ms
            e0, e1 = fm_env(a, FM_DEC_INF, 40)
            self.assertTrue(np.array_equal(e0[:na + 1], np.arange(na + 1) / na))  # linear 0 -> 1, exactly 1 at n_a
            self.assertTrue(np.all(e0[na:] == 1.0) and np.all(e1[na - 1:] == 1.0))
            self.assertTrue(np.array_equal(e1[:-1], e0[1:]))                     # one ramp across block boundaries
            f0, _ = fm_env(1, FM_DEC_INF, 4); self.assertEqual(list(f0), [0.0, 0.5, 1.0, 1.0])  # Atk 1: 2 blocks
            g0, g1 = fm_env(0, FM_DEC_INF, 3); self.assertEqual((g0[0], g1[0]), (1.0, 1.0))    # Atk 0: instant

        def test_decay_by_hand(self):
            for d in (0, 1, 40, 90, 127):
                nd = fm_env_blocks(d); e0, e1 = fm_env(0, d, nd + 3)
                j = np.arange(nd)
                self.assertTrue(np.allclose(e0[:nd], FM_ENV_FLOOR ** (j / nd), rtol=1e-12, atol=0), d)
                self.assertTrue(np.all(e0[nd:] == 0.0) and e1[nd - 1] == 0.0, d)  # -60 dB at n_d blocks, then 0
                self.assertGreater(e0[nd - 1], FM_ENV_FLOOR)
                self.assertAlmostEqual(nd * T_BLOCK, fm_env_time(d, "dec"), delta=1e-15)
            e0, e1 = fm_env(0, 0, 3); self.assertEqual((list(e0), list(e1)), ([1.0, 0.0, 0.0], [0.0, 0.0, 0.0]))
            a, d = 10, 30; na, nd = fm_env_blocks(a), fm_env_blocks(d)            # attack then decay from k_a = n_a
            e0, _ = fm_env(a, d, na + nd + 2)
            self.assertEqual(e0[na], 1.0); self.assertAlmostEqual(e0[na + 1], FM_ENV_FLOOR ** (1 / nd), delta=1e-15)
            self.assertEqual(e0[na + nd], 0.0)

        def test_retrigger_per_note(self):
            a, d = 20, 40; e0, e1 = fm_env(a, d, 120, notes=(0, 50, 51, 90))
            r0, r1 = fm_env(a, d, 50)
            for s, end in ((0, 50), (51, 90)):
                self.assertTrue(np.array_equal(e0[s:end], r0[:end - s]) and np.array_equal(e1[s:end], r1[:end - s]))
            self.assertEqual((e0[50], e1[50], e0[90]), (0.0, r1[0], 0.0))         # hard reset, from whatever it was
            self.assertGreater(e1[89], 0.1)                                     # so a step at the note's boundary
            i0, _ = fm_env(0, 40, 60, notes=(0, 30)); self.assertEqual(i0[30], 1.0)   # Atk 0: back to 1

        def test_lines_and_samples(self):
            a, d = 12, 60; nb = 80
            L = fm_env_lines(a, d, 600, 600, nb, notes=(0, 40))
            y = fm_env_samples(L); I = fm_index(600)
            self.assertEqual(y.size, nb * FM_ENV_N)
            e0, e1 = fm_env(a, d, nb, notes=(0, 40))
            self.assertTrue(np.allclose([s for s, _ in L], I * e0, rtol=0, atol=1e-15))
            j = np.arange(FM_ENV_N) / FM_ENV_N                                  # within a note: continuous
            b = 20; self.assertAlmostEqual(y[(b + 1) * FM_ENV_N], L[b][1], delta=1e-15)
            self.assertTrue(np.allclose(y[b * FM_ENV_N:(b + 1) * FM_ENV_N], L[b][0] + (L[b][1] - L[b][0]) * j, atol=1e-15))
            self.assertEqual(y[40 * FM_ENV_N], 0.0)                              # the retrigger: back to 0
            # an amount that moves (msub v0 -> v1) and the envelope: endpoints multiply, the line stays one VFMA
            v0 = np.linspace(0, 1000, nb); v1 = np.r_[v0[1:], 1000]
            M = fm_env_lines(a, d, v0, v1, nb)
            ee0, ee1 = fm_env(a, d, nb)
            self.assertTrue(all(abs(m[0] - fm_index(p) * q) < 1e-15 and abs(m[1] - fm_index(r) * s) < 1e-15
                                for m, p, q, r, s in zip(M, v0, ee0, v1, ee1)))
            self.assertTrue(all(M[b][1] == M[b + 1][0] for b in range(nb - 1)))

        def test_f32_stated_arithmetic_within_tolerance(self):
            worst = 0.0
            for a, d in ((0, 127), (127, 127), (1, 0), (64, 64), (90, 100), (5, FM_DEC_INF)):
                n = (fm_env_blocks(a) if a else 0) + (fm_env_blocks(d) if d != FM_DEC_INF else 10) + 3
                e64, _ = fm_env(a, d, n); e32, _ = fm_env(a, d, n, law="f32")
                worst = max(worst, float(np.max(np.abs(e64 - e32))))
                self.assertTrue(np.all(e32[e64 == 0] == 0) and np.all(e32[e64 == 1] == 1), (a, d))  # counters decide
                L32 = fm_env_lines(a, d, 1000, 1000, n, law="f32")
                L64 = fm_env_lines(a, d, 1000, 1000, n)
                y32 = fm_env_samples(L32, law="f32"); y64 = fm_env_samples(L64)
                self.assertLess(float(np.max(np.abs(y32 - y64))), 16 * FM_ENV_TOL + 2e-5, (a, d))
            self.assertLess(worst, FM_ENV_TOL)                                    # M: 4.0e-6 at Dec 127 (bound 2.4e-5)

        def test_mutants_turn_red(self):
            spec = {m: None for m in FM_ENV_MUTANTS}
            for m in FM_ENV_MUTANTS:
                a, d, notes = (20, 40, (0, 60)) if m == "no_retrig" else (20, 40, (0,))
                n = 200 if m == "no_floor" else 120
                r = fm_env(a, d, n, notes)
                x = fm_env(a, d, n, notes, mutant=m)
                spec[m] = max(float(np.max(np.abs(r[0] - x[0]))), float(np.max(np.abs(r[1] - x[1]))))
                self.assertGreater(spec[m], 100 * FM_ENV_TOL / 16, m)       # at I = 16, far above the f32 bound
            self.assertGreater(spec["lin_decay"], 0.2)

    L = unittest.defaultTestLoader
    r = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([L.loadTestsFromTestCase(c) for c in (Shapes, FM, FM12, FmEnv)]))
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(_selftest())
