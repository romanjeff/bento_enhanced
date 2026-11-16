# SPDX-License-Identifier: GPL-3.0-or-later
"""P3 independent host golden model (docs/plan-p3-fm-bits.md 6.3): numpy, float64, shares no C code.

    python p3_golden.py          # self-test (no image, no build dir)

Written from the plan text only (4.6, 4.7, OD6, OD11, OD4), before and without reading the DSP sources. Later sprints add
the sub waves (OD10), the through-zero FM phase and the slave reset rule (4.3, OD5) here. Q2's part is the Bit Depth
quantiser, Q3's the shapes below the saw (after the Bit Depth notes).

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
  chain render() calls with the returned state for a held Shape per part), FM (Q5), Shape > 48.
"""
import sys
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


def crush(x, b, f32=True):
    """The crushed block (float64 array; every value is an exact float32). b None (Full) returns x unchanged.
    f32: round x to float32 first, as the M7 holds s; pass False only to study a float64 signal on its own."""
    x = _x(x, f32)
    if b is None: return x.copy()
    S = scale(b)
    return np.clip(np.rint(x * S), -S, S - 1) / S


def crush_bits(x, b):
    """crush() as the uint32 IEEE patterns of its float32 values: for byte-for-byte checks (sign of zero included)."""
    return crush(x, b).astype(np.float32).view(np.uint32)


def levels(q, b):
    """Grid indices k = q * S (int64). Raises if any value is off the grid or outside -S..S-1."""
    S = scale(b); k = np.asarray(q, dtype=np.float64) * S
    if not np.all(k == np.rint(k)): raise AssertionError(f"{b}-bit: {int(np.sum(k != np.rint(k)))} values off the 1/{S:g} grid")
    if k.size and (k.min() < -S or k.max() > S - 1):
        raise AssertionError(f"{b}-bit: index range {k.min():g}..{k.max():g} outside {-S:g}..{S - 1:g}")
    return k.astype(np.int64)


def check_levels(q, b):
    """On-grid, in range, and at most 2^b distinct values. Returns the distinct-level count."""
    n = int(np.unique(levels(q, b)).size)
    if n > 1 << b: raise AssertionError(f"{b}-bit: {n} levels > {1 << b}")   # unreachable after levels(); kept as the stated rule
    return n


def check_clip(x, q, b, f32=True):
    """The asymmetric clip: every x * S >= S - 0.5 gives (S-1)/S, every x * S <= -S - 0.5 gives -1, and +1.0 never
    appears. Returns (n_top, n_bottom), the clipped sample counts, so a case can assert it reached each side."""
    x = _x(x, f32); q = np.asarray(q, dtype=np.float64); S = scale(b)
    top, bot = x * S >= S - 0.5, x * S <= -S - 0.5
    if np.any(q >= 1.0): raise AssertionError(f"{b}-bit: +1.0 in the output (top clamped at S, not S - 1)")
    if not np.all(q[top] == (S - 1) / S): raise AssertionError(f"{b}-bit: top not clipped at (S-1)/S")
    if not np.all(q[bot] == -1.0): raise AssertionError(f"{b}-bit: bottom not clipped at -1")
    if np.any(q < -1.0): raise AssertionError(f"{b}-bit: output below -1 (bottom clamp missing)")
    return int(top.sum()), int(bot.sum())


def near_tie(x, b, err):
    """Mask of samples whose x * S is within err * S of a half step: an input error <= err may change their level."""
    S = scale(b); t = np.asarray(x, dtype=np.float64) * S
    return np.abs(t - np.floor(t) - 0.5) <= err * S


def compare(got, x, b, err=0.0):
    """got (the M7's crushed block) against crush(x, b). err = 0: x is the M7's own float32 s, so every sample must match
    exactly. err > 0: x comes from another model within err of s; samples near a tie (near_tie) may differ by exactly
    one step, all others exactly. Returns the number of tie samples that did differ; raises on any other mismatch."""
    got = np.asarray(got, dtype=np.float64); want = crush(x, b, f32=err == 0.0)
    if got.shape != want.shape: raise AssertionError(f"shape {got.shape} != {want.shape}")
    d = np.abs(levels(got, b) - levels(want, b))
    tie = near_tie(x, b, err) if err > 0 else np.zeros(d.shape, bool)
    bad = (d > 1) | ((d == 1) & ~tie)
    if np.any(bad):
        i = int(np.flatnonzero(bad)[0])
        raise AssertionError(f"{b}-bit: {int(bad.sum())} samples differ, first [{i}] x={x[i]!r} got={got[i]!r} want={want[i]!r}")
    return int(np.sum(d == 1))


def mutants(x, b):
    """The wrong quantisers 6.6 must catch (plus three plausible slips), each on the same float32 input."""
    x = _x(x, True); S = scale(b); t = x * S
    away = np.sign(t) * np.floor(np.abs(t) + 0.5)                # C roundf: ties away from zero
    return {
        "mid_tread_no_clamp": np.rint(t) / S,                    # OD11 (a)
        "top_clamped_at_S": np.clip(np.rint(t), -S, S) / S,
        "half_away_from_zero": np.clip(away, -S, S - 1) / S,
        "truncate": np.clip(np.trunc(t), -S, S - 1) / S,
        "symmetric_clip": np.clip(np.rint(t), -(S - 1), S - 1) / S,
    }


def discriminates(x, b):
    """{mutant: number of samples where it differs from crush(x, b)}: a crush case's block must score > 0 on each
    mutant it is meant to kill."""
    want = crush(x, b)
    return {k: int(np.sum(v != want)) for k, v in mutants(x, b).items()}


def probe(b):
    """A crafted float32 block for b: every tie at the bottom, middle and top of the range, -0.0, a negative value that
    rounds to -0.0, overshoot past +1 and below -1 (beyond the tie at -S - 0.5), and both exact ends."""
    S = scale(b)
    k = np.r_[-S - 3, -S - 1, -S - 0.5, -S, -S + 0.5, -2.5, -1.5, -0.5, -0.3, 0.0, 0.3, 0.5, 1.5, 2.5,
              S - 1.5, S - 1, S - 0.5, S, S + 0.5, S + 3]
    x = np.r_[k / S, -0.0, 1.06, -1.06]
    return x.astype(np.float32).astype(np.float64)


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

    L = unittest.defaultTestLoader
    r = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([L.loadTestsFromTestCase(BitDepth), L.loadTestsFromTestCase(Shapes)]))
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(_selftest())
