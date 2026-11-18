/* SPDX-License-Identifier: GPL-3.0-or-later */
/* BE4.44 W2 (docs/plan-wt-fm.md 3.4): the reader's mip-change loop (rd_qm), apart from rd_q / rd_qx (wt_reader.cpp, -O2) so it
 * builds at -Os: it runs only in a block whose mip changes (the hysteresis keeps those rare), and with two levels' indices,
 * fractions and masks live it spills registers at -O2 as well (M at W2: 60 a sample at -O2). Same arithmetic. */
#include "wt_reader.h"
using namespace wtr;

/* rd_qm, a mip change (plan 3.4: a block changes the mip or the frame, never both): the old level (read 0) to the new one
 * (read 1, at phase r + d1, the two skews' difference, in a table of 2^l1 samples) across the block, weight (i + 1) / n. */
extern "C" void wt_rdx(WtRd *g) {
    float *out = g->out;
    const float *t0 = g->t0, *t1 = g->t1, *a = g->fi->a, *b = g->fi->b;
    u32 l0 = g->l0, l1 = g->l1, m0 = (1u << l0) - 1u, m1 = (1u << l1) - 1u, n = g->n, o = g->o0, r = g->ph + o, d1 = g->d1;
    const Line q = line_of(g);
    float j = 0.f, dw = 1.f / (float)n;
    for (u32 i = 0; i < n; i++, j += 1.f) {
        float d = step_of(q, a, b, i, j);
        u32 k = r >> (32u - l0), r1 = r + d1, k1 = r1 >> (32u - l1);
        float f = uq(r << l0), f1 = uq(r1 << l1);
        float p = fma32(f, t0[(k + 1u) & m0] - t0[k], t0[k]), v = fma32(f1, t1[(k1 + 1u) & m1] - t1[k1], t1[k1]);
        out[i] = fma32((j + 1.f) * dw, v - p, p);
        r += q32(d);
    }
    g->ph = r - o;
}
