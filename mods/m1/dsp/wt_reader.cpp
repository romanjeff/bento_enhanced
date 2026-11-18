/* SPDX-License-Identifier: GPL-3.0-or-later */
/* BE4.44 W2 (docs/plan-wt-fm.md 3.4): the Wavetable table reader under FM, its per-sample loops rd_q and rd_qx (rd_qm:
 * wt_mipx.cpp). Everything else of the reader (stock's mip ladder, the mip choice with its hysteresis, the one-change-per-block rule, the phase handoff, mip home,
 * level 0) is control-rate code in analog.cpp (wt_play); these two loops are -O2 (build.py DSP_OPT) like the Analog kernel.
 *
 * Phase: u32 Q32, 2^32 = one cycle of the table, so a wrap in either direction (through-zero FM) is integer overflow, exact.
 * Each sample reads the table at the phase, then advances it (stock's resampler order: sample 0 is read at the entry phase,
 * so the first reader sample is stock's continuation): the read is at r = ph + o (o the read's phase offset: stock's mip
 * levels are not phase-aligned, analog.cpp WT_MIP_SKEW), index r >> (32 - l) in a table of 2^l samples, fraction
 * (r << l) / 2^32 (VCVT.F32.U32 #32), linear interpolation with one VFMA. The increment: Analog's index line,
 * di = fma(fma(dib, j, ib), b, fma(fma(dia, j, ia), a, inc)) (an_render_fm's 4.3 order), clamped to +-0.45 with
 * VMAXNM / VMINNM, to Q32 with VCVT.S32.F32 #32 (toward zero). One route alone: b = a at a zero line (+0 b adds +0). */
#include "wt_reader.h"
using namespace wtr;

/* rd_q: one read (the mip and frame playing). */
extern "C" void wt_rd(WtRd *g) {
    float *out = g->out;
    const float *t = g->t0, *a = g->fi->a, *b = g->fi->b;
    u32 l = g->l0, sh = 32u - l, mask = (1u << l) - 1u, n = g->n, o = g->o0, r = g->ph + o;
    const Line q = line_of(g);
    float j = 0.f;
    for (u32 i = 0; i < n; i++, j += 1.f) {
        float d = step_of(q, a, b, i, j);
        u32 k = r >> sh;
        float f = uq(r << l), s0 = t[k], s1 = t[(k + 1u) & mask];
        out[i] = fma32(f, s1 - s0, s0);
        r += q32(d);
    }
    g->ph = r - o;
}

/* BE4.44 W4 (Jeff, "Decisions before W4": the unrolled reader): rd_q on a one-route line (B's term zero on b = a: every
 * Wavetable carrier's, Sub FM's A or FM 1>2's B alone), two samples a turn. rd_q's step fma(fma(dib, j, ib), b, fma(fma(dia, j,
 * ia), a, inc)) is then fma(fma(dia, j, ia), a, inc) to the bit: fma(0, b, x) adds a zero to x (b = a: a NaN in a is a NaN in
 * both forms; a -0 sum becomes +0, the same Q32 step), and on a constant line (dia = 0: an amount held with no envelope
 * moving, and every no-FM block's zero line) fma(ia, a, inc), fma(0, j, ia) being ia. So the samples and the phase are rd_q's
 * bit for bit (test_analog WtFmW4), without the second term's loads, VFMAs and copies, the constant line's j, and half the
 * loop's own count. wt_play picks them (wt_rdc constant, wt_rdr a ramp); rd_q stays for two routes. */
template <int RAMP> static inline __attribute__((always_inline)) void rd_one(WtRd *g) {
    float *out = g->out;
    const float *t = g->t0, *a = g->fi->a;
    u32 l = g->l0, sh = 32u - l, mask = (1u << l) - 1u, n = g->n, o = g->o0, r = g->ph + o;
    const float inc = g->inc, ia = g->fi->ia, dia = g->fi->dia;
    float j = 0.f;
    auto one = [&](float *y, const float *x) __attribute__((always_inline)) {
        float d = fma32(RAMP ? fma32(dia, j, ia) : ia, *x, inc);
        if (RAMP) j += 1.f;
        d = __builtin_fminf(__builtin_fmaxf(d, -.45f), .45f);
        u32 k = r >> sh;
        float f = uq(r << l), s0 = t[k], s1 = t[(k + 1u) & mask];
        *y = fma32(f, s1 - s0, s0);
        r += q32(d);
    };
    for (float *end = out + (n & ~1u); out != end; out += 2, a += 2) { one(out, a); one(out + 1, a + 1); }
    if (n & 1u) one(out, a);
    g->ph = r - o;
}
extern "C" void wt_rdc(WtRd *g) { rd_one<0>(g); }
extern "C" void wt_rdr(WtRd *g) { rd_one<1>(g); }

/* W4: rd_qx on a one-route line (wt_rdf's bit for bit, as above), two samples a turn: the frame crossfade of a Position moving
 * under FM. The weight (i + 1) / n is j x dw with j counted up first (i + 1 exactly, as wt_rdf's j + 1). */
extern "C" void wt_rdfc(WtRd *g) {
    float *out = g->out;
    const float *t0 = g->t0, *t1 = g->t1, *a = g->fi->a;
    u32 l = g->l0, sh = 32u - l, mask = (1u << l) - 1u, n = g->n, o = g->o0, r = g->ph + o;
    const float inc = g->inc, ia = g->fi->ia, dia = g->fi->dia;
    float j = 0.f, dw = 1.f / (float)n;
    auto one = [&](float *y, const float *x) __attribute__((always_inline)) {
        float d = __builtin_fminf(__builtin_fmaxf(fma32(fma32(dia, j, ia), *x, inc), -.45f), .45f);
        j += 1.f;
        u32 k = r >> sh, k1 = (k + 1u) & mask;
        float f = uq(r << l);
        float p = fma32(f, t0[k1] - t0[k], t0[k]), v = fma32(f, t1[k1] - t1[k], t1[k]);
        *y = fma32(j * dw, v - p, p);
        r += q32(d);
    };
    for (float *end = out + (n & ~1u); out != end; out += 2, a += 2) { one(out, a); one(out + 1, a + 1); }
    if (n & 1u) one(out, a);
    g->ph = r - o;
}

/* rd_qx: a Position change at the mip playing: read 0's frame t0 to the new frame t1 across the block, weight (i + 1) / n
 * (the last sample is the new frame's alone); one index and fraction for both (same level, same phase). */
extern "C" void wt_rdf(WtRd *g) {
    float *out = g->out;
    const float *t0 = g->t0, *t1 = g->t1, *a = g->fi->a, *b = g->fi->b;
    u32 l = g->l0, sh = 32u - l, mask = (1u << l) - 1u, n = g->n, o = g->o0, r = g->ph + o;
    const Line q = line_of(g);
    float j = 0.f, dw = 1.f / (float)n;
    for (u32 i = 0; i < n; i++, j += 1.f) {
        float d = step_of(q, a, b, i, j);
        u32 k = r >> sh, k1 = (k + 1u) & mask;
        float f = uq(r << l);
        float p = fma32(f, t0[k1] - t0[k], t0[k]), v = fma32(f, t1[k1] - t1[k], t1[k]);
        out[i] = fma32((j + 1.f) * dw, v - p, p);
        r += q32(d);
    }
    g->ph = r - o;
}

/* BE4.44 W4 (plan 3.5 step 5, D4): buffer B from a Wavetable OSC1, b = f32(f32(s - m) inc): its block before Level less the
 * frame's mean m (analog.cpp wt_frame_dc), times its increment (an_feed's form with the DC out). */
extern "C" void wt_feed_b(const float *s, float *b, unsigned n, float inc, float m) {
    /* eight a turn on two pointers (W4 review: the low-note Position-LFO voice sat 0.4 % over the heaviest existing voice;
     * the index loop's overhead was 1.5 instructions a sample), then the tail; the same arithmetic, sample by sample */
    for (const float *e = s + (n & ~7u); s < e; s += 8, b += 8) {
        float x0 = s[0], x1 = s[1], x2 = s[2], x3 = s[3], x4 = s[4], x5 = s[5], x6 = s[6], x7 = s[7];
        b[0] = (x0 - m) * inc; b[1] = (x1 - m) * inc; b[2] = (x2 - m) * inc; b[3] = (x3 - m) * inc;
        b[4] = (x4 - m) * inc; b[5] = (x5 - m) * inc; b[6] = (x6 - m) * inc; b[7] = (x7 - m) * inc;
    }
    for (unsigned i = 0; i < (n & 7u); i++) b[i] = (s[i] - m) * inc;
}
