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
