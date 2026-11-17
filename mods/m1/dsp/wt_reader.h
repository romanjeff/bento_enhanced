/* SPDX-License-Identifier: GPL-3.0-or-later */
/* BE4.44 W2 (docs/plan-wt-fm.md 3.4): the Wavetable reader's loops (dsp/wt_reader.cpp) and their argument block. */
#ifndef DSP_WT_READER_H
#define DSP_WT_READER_H
#include "analog.h"

struct WtRd {
    float *out;
    const float *t0, *t1;             /* read 0's and read 1's frame (wt_rdx only reads t1) */
    unsigned o0, d1;                  /* read 0's phase offset (Q32), read 1's relative to read 0 */
    unsigned l0, l1;                  /* log2 of each read's table length (5 .. 11) */
    unsigned ph;                      /* in / out: the phase at the note's mip, Q32 (2^32 = one cycle) */
    float inc;                        /* the base increment (cycles a sample) */
    const FmIn *fi;                   /* the modulators and their index lines (analog.h; off unused: one call a block) */
    unsigned n;
};

/* the loops' shared arithmetic (wt_reader.cpp, wt_mipx.cpp) */
namespace wtr {
typedef unsigned u32;
typedef int i32;

static inline __attribute__((always_inline)) float fma32(float a, float b, float c) {
    __asm("vfma.f32 %0, %1, %2" : "+t"(c) : "t"(a), "t"(b));
    return c;
}
static inline __attribute__((always_inline)) u32 q32(float d) {     /* d x 2^32, toward zero (|d| <= 0.45: no saturation) */
    i32 r;
    __asm("vcvt.s32.f32 %0, %0, #32" : "+t"(d));
    __builtin_memcpy(&r, &d, 4);
    return (u32)r;
}
static inline __attribute__((always_inline)) float uq(u32 x) {      /* x / 2^32 */
    float t;
    __builtin_memcpy(&t, &x, 4);
    __asm("vcvt.f32.u32 %0, %0, #32" : "+t"(t));
    return t;
}
/* the line's five values as locals (the loops store through out, which the compiler cannot prove apart from g) */
struct Line { float inc, ia, dia, ib, dib; };
static inline __attribute__((always_inline)) Line line_of(const WtRd *g) { return Line{g->inc, g->fi->ia, g->fi->dia, g->fi->ib, g->fi->dib}; }
static inline __attribute__((always_inline)) float step_of(const Line &q, const float *a, const float *b, u32 i, float j) {
    float d = fma32(fma32(q.dib, j, q.ib), b[i], fma32(fma32(q.dia, j, q.ia), a[i], q.inc));
    return __builtin_fminf(__builtin_fmaxf(d, -.45f), .45f);
}
}  // namespace wtr

extern "C" void wt_rd(WtRd *g);
extern "C" void wt_rdc(WtRd *g);                 /* (W4: rd_q on a one-route line, unrolled: constant, a ramp) */
extern "C" void wt_rdr(WtRd *g);
extern "C" void wt_rdfc(WtRd *g);                /* (W4: rd_qx on a one-route line) */
extern "C" void wt_feed_b(const float *s, float *b, unsigned n, float inc, float m);   /* (W4: B from a Wavetable OSC1) */
extern "C" void wt_rdf(WtRd *g);
extern "C" void wt_rdx(WtRd *g);                 /* (wt_mipx.cpp, -Os: rare, and it spills at -O2 too) */

#endif
