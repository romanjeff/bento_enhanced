# SPDX-License-Identifier: GPL-3.0-or-later
"""P3 independent host golden model (docs/plan-p3-fm-bits.md 6.3): numpy, float64, shares no C code.

    python p3_golden.py          # self-test (no image, no build dir)

Written from the plan text only (4.7, OD11, OD4), before and without reading the DSP sources. Later sprints add the sub
waves (OD10), the through-zero FM phase and the slave reset rule (4.3, OD5) here; Q2's part is the Bit Depth quantiser.

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

    r = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BitDepth))
    return 0 if r.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(_selftest())
