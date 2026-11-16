/* SPDX-License-Identifier: GPL-3.0-or-later */
/* The small C library of the hard-float DSP ELF (S6a, S6c): memset / memcpy (the compiler may emit calls to them) and
 * the single-precision math the vendored DaisySP files call (daisysp/README.md), named be_powf / be_sinf / be_log10f:
 * the shim's <cmath> maps powf / sinf / log10f to them (P4-D1), so the ELF never defines a libm name and build.py's
 * DSP_REFUSED_LIBM stays strict. No libc or libm is linked, and the
 * build refuses any soft-float or double helper (build.py check_dsp_elf), so everything here is float only.
 *
 * Accuracy, measured in the emulator against the host's libm (test_drums.py Math asserts these bounds): powf(2, x)
 * about 1e-6 relative, powf(a, b) about 2e-6 relative, sinf about 4e-6 absolute for |x| <= 50 (the Kick passes at
 * most 2 pi x 1.75). They are plain polynomials after a range reduction; nothing reads a table or calls stock code,
 * so they behave the same in the emulator and on the device. test_drums.py's host reference of the Kick uses the
 * host's libm, so its comparison covers these too.
 *
 * powf(2, b) keeps its last two results in the drum run state (drums.h DrumRun.pow_memo): DaisySP's Kick computes
 * two envelope coefficients per sample from knob values that are constant for the whole block (synthbassdrum.cpp
 * Process), so all but two of a block's 2 x n calls return a stored result, bit for bit what the computation gives
 * (review S5, rc25). The ELF has no RAM of its own, so the memo lives in the synth area. */
#include "drums.h"

extern "C" void *memset(void *d, int c, unsigned n) {
    u8 *p = (u8 *)d;
    while (n--) *p++ = (u8)c;
    return d;
}
extern "C" void *memcpy(void *d, const void *s, unsigned n) {
    u8 *p = (u8 *)d;
    const u8 *q = (const u8 *)s;
    while (n--) *p++ = *q++;
    return d;
}

static inline int floor_int(float x) {
    int n = (int)x;
    return (float)n > x ? n - 1 : n;
}

/* 2^x: x = n + f (f in [0, 1)), 2^f = e^(f ln 2) by its Taylor series to the 8th power (error < 2e-7 for f < 1),
 * 2^n through the exponent bits. Below 2^-126 (and NaN) it is 0, above 2^127 it saturates there. */
static float exp2_(float x) {
    if (!(x > -126.f)) return 0.f;
    if (x > 127.f) x = 127.f;
    int n = floor_int(x);
    float f = (x - (float)n) * 0.6931471806f;
    float y = 1.f + f * (1.f + f * (.5f + f * (1.f / 6.f + f * (1.f / 24.f + f * (1.f / 120.f + f * (1.f / 720.f +
              f * (1.f / 5040.f + f * (1.f / 40320.f))))))));
    union { u32 u; float f; } s = {(u32)(n + 127) << 23};
    return y * s.f;
}

/* log2(a) for a > 0: a = m 2^e with m in [sqrt(1/2), sqrt(2)), ln m = 2 atanh(z), z = (m - 1) / (m + 1), |z| < 0.172,
 * by its series to z^11 (error < 1e-9). Not positive or not finite: the caller's problem (powf only passes a > 0). */
static float log2_(float a) {
    union { float f; u32 u; } v = {a};
    int e = (int)((v.u >> 23) & 0xFFu) - 127;
    v.u = (v.u & 0x7FFFFFu) | 0x3F800000u;
    if (v.f > 1.4142135624f) { v.f *= .5f; e++; }
    float z = (v.f - 1.f) / (v.f + 1.f), q = z * z;
    float ln = 2.f * z * (1.f + q * (1.f / 3.f + q * (1.f / 5.f + q * (1.f / 7.f + q * (1.f / 9.f + q * (1.f / 11.f))))));
    return (float)e + ln * 1.4426950409f;
}

/* powf for the bases DaisySP uses (2 for its envelope and tone coefficients, the filter resonance 0..1): a <= 0 gives
 * 0 (no complex results, no negative bases in the Kick), a == 1 or b == 0 gives exactly 1, 2^b skips the logarithm
 * and is remembered (above). A zeroed memo entry (b's bits 0) could only match b == +0, which has returned already. */
extern "C" float be_powf(float a, float b) {
    if (b == 0.f || a == 1.f) return 1.f;
    if (!(a > 0.f)) return 0.f;
    if (a != 2.f) return exp2_(b * log2_(a));
    union { float f; u32 u; } k = {b};
    DrumRun *r = DRUM_RUN;
    for (u32 i = 0; i < 2u; i++)
        if (r->pow_memo[i].b == k.u) {
            union { u32 u; float f; } y = {r->pow_memo[i].y};
            return y.f;
        }
    union { float f; u32 u; } y = {exp2_(b)};
    r->pow_memo[1] = r->pow_memo[0];
    r->pow_memo[0].b = k.u; r->pow_memo[0].y = y.u;
    return y.f;
}

extern "C" float be_log10f(float x) { return x > 0.f ? log2_(x) * 0.3010299957f : -1e30f; }

/* sin(x): reduced to (-pi, pi] by whole turns, then to [-pi/2, pi/2] by symmetry, Taylor to x^13 (error < 1e-8 there).
 * Exact enough for |x| up to a few hundred (the Kick passes 2 pi (phase + 0.75), phase in [0, 1); its click filter
 * passes at most pi / 4). */
extern "C" float be_sinf(float x) {
    const float pi = 3.1415926536f, twopi = 6.2831853072f;
    x -= (float)floor_int(x * 0.1591549431f) * twopi;
    if (x > pi) x -= twopi;
    if (x > .5f * pi) x = pi - x;
    else if (x < -.5f * pi) x = -pi - x;
    float q = x * x;
    return x * (1.f + q * (-1.f / 6.f + q * (1.f / 120.f + q * (-1.f / 5040.f + q * (1.f / 362880.f +
           q * (-1.f / 39916800.f + q * (1.f / 6227020800.f)))))));
}
