/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Analog oscillator kernel (S6a), the per-sample part: BLEP saw -> square -> narrow pulse, hard sync.
 *
 * A readable rewrite of bento-playground's AnalogKernel (eu4_work/drums/analog.h at 9fc7787,
 * GPL-3.0-or-later; ideas from its subtractive.h): one sample per output sample, two-sample polynomial
 * BLEP corrections at the pulse edge and at the wrap, fractional hard-sync resets. The native sub
 * oscillator, filters, envelopes and mixing follow in stock's voice code.
 *
 * Shape 0..1: blend = min(1, 2 shape) fades saw into pulse; above 0.5 the pulse narrows from 50 % to
 * 8 % (width = 0.5 - 0.84 (shape - 0.5)). A sample's correction straddles it and the next one, hence
 * `delayed`.
 * P3 Q3 (docs/plan-p3-fm-bits.md 4.6): shape -1..0 is the LOW regime below the saw: -1 sine, -0.5 triangle, 0 saw, tw the
 * triangle's weight (2 shape + 2 on [-1, -0.5], the rest sine; -2 shape above, the rest saw). The triangle 1 - 4 |p - 0.5|
 * (-1 at p = 0, +1 at 0.5) gets a two-point polyBLAMP at both corners, the saw part its BLEP scaled by 1 - tw, the sine
 * (sinq of the triangle: -cos 2 pi p, in phase with it) none. Shape >= 0 is BE4.40's code and result, bit for bit.
 * P3 Q3b: the two plateaus (sine ws = 1, triangle tw = 1) run_flat: LOW's own sample only where a corner or a slave's event
 * may fall, a correction-free stretch between (the triangle LOW's bit for bit, the sine within 1.8e-7); the blends keep LOW.
 *
 * Efficiency (docs/plan-synths-168.md section 6, measured by test_analog.py):
 *   E1  the loop is specialised by template: the shape regime (pure saw, pure pulse, mixed) and the
 *       oscillator's sync role (free, master, slave), so the per-sample role tests and the "no event"
 *       stores leave the loop. A regime whose correction is multiplied by zero skips it: bit-identical.
 *   E2  one reciprocal of the increment per block: edges multiply instead of divide (the last bit of an
 *       edge position can differ; test_analog's golden tolerance covers it).
 *   This file is built -O2 (the hooks -Os). AN_E1 / AN_E2 = 0 builds the upstream-shaped loop, for the
 *   before / after measurement only (never in a kit).
 * Single precision only: f-suffixed constants, -Wdouble-promotion -Werror, and the build refuses any
 * double or soft-float helper in the ELF. */
#include "analog.h"

#ifndef AN_E1
#define AN_E1 1
#endif
#ifndef AN_E2
#define AN_E2 1
#endif

namespace {

enum Regime { SAW, PULSE, MIXED, LOW };
enum Role { FREE, MASTER, SLAVE };

struct Shape {
    float inc, rinc, blend, width, dc;
    float tw, ws, wsaw;               /* LOW: the triangle's, the sine's and the saw's weights (they sum to 1) */
    int low;                          /* shape < 0: the LOW regime (one generic loop per role, plan 4.6) */
};

/* sin(pi/2 x), x in [-1, 1]: odd 5th order (rc26 fm_proto.cpp, its fit.py), p(1) = 1 in float32. Design maximum error
 * 8.1e-5 (M: 8.07e-5 dense in float64, 8.08e-5 evaluated in float32); as the sine of the triangle, THD -81.9 dB, the largest
 * harmonic the 5th at -82.2 dB (M, scratch p3/q3/impl/sinq.py, measured before this kernel). No table, no division. */
inline float sinq(float x) {
    float x2 = x * x;
    return x * (1.570243f + x2 * (-.641711175f + x2 * .0714682192f));
}
inline float tri(float p) { return 1.f - 4.f * __builtin_fabsf(p - .5f); }   /* VABS (plain fabsf is libm: plan 4.7) */

inline void set_shape(Shape &k, float shape) {
    k.low = shape < 0.f;
    if (k.low) {                      /* P3 Q3: -1 sine .. -0.5 triangle .. 0 saw (NaN is not < 0: the saw, as BE4.40) */
        if (shape < -1.f) shape = -1.f;
        int st = shape <= -.5f;
        k.tw = st ? 2.f * shape + 2.f : -2.f * shape;
        k.ws = st ? 1.f - k.tw : 0.f;
        k.wsaw = st ? 0.f : 1.f - k.tw;
        return;                       /* (blend, width and dc are not read by LOW, nor the weights by the others) */
    }
    if (!(shape >= 0.f)) shape = 0.f;
    if (shape > 1.f) shape = 1.f;
    k.blend = shape * 2.f > 1.f ? 1.f : shape * 2.f;
    k.width = .5f - (shape > .5f ? shape - .5f : 0.f) * .84f;
    k.dc = 2.f * k.width - 1.f;
}

inline float recip(const Shape &k, float x) {      /* x / inc */
#if AN_E2
    return x * k.rinc;
#else
    return x / k.inc;
#endif
}

template <int R> inline float wave(float p, const Shape &k) {
    if (R == LOW) { float t = tri(p); return k.tw * t + k.ws * sinq(t) + k.wsaw * (2.f * p - 1.f); }
    if (R == SAW) return 2.f * p - 1.f;
    if (R == PULSE) return (p < k.width ? 1.f : -1.f) - k.dc;
    return (2.f * p - 1.f) * (1.f - k.blend) + ((p < k.width ? 1.f : -1.f) - k.dc) * k.blend;
}

/* A step of size `jump` at `fraction` of this sample: half of the residual now, half in the next one. */
inline void edge(float fraction, float jump, float &now, float &next) {
    float t = 1.f - fraction;
    now += jump * .5f * t * t;
    next -= jump * .5f * fraction * fraction;
}

/* A slope change of `kink` (output units per sample) at `fraction` of this sample: the two-point polyBLAMP, the same kernel
 * as edge()'s integrated once (P3 Q3: the triangle's corners). */
inline void corner(float fraction, float kink, float &now, float &next) {
    float t = 1.f - fraction;
    now += kink * (1.f / 6.f) * t * t * t;
    next += kink * (1.f / 6.f) * fraction * fraction * fraction;
}

/* Advance the phase over [begin, begin + length) of one sample; corrections into now / next. Returns the
 * wrap position in the sample (0..1), or -1 if the phase did not wrap. */
template <int R> inline float advance(float &p, const Shape &k, float begin, float length, float &now, float &next) {
    float wrap = -1.f;
    if (length <= 0.f) return wrap;
    float end = p + k.inc * length;
    if (R == LOW) {                   /* the triangle's top at 0.5 (slope +4 -> -4 a cycle); at the wrap its bottom (-4 -> +4)
                                       * and the saw part's step; with inc <= 0.45 at most one of the two places a sample */
        float kink = 8.f * k.inc * k.tw;
        if (p < .5f && end >= .5f) corner(begin + recip(k, .5f - p), -kink, now, next);
        if (end >= 1.f) {
            wrap = begin + recip(k, 1.f - p);
            corner(wrap, kink, now, next);
            edge(wrap, -2.f * k.wsaw, now, next);
            end -= 1.f;
        }
        p = end;
        return wrap;
    }
    if (R != SAW && p < k.width && end >= k.width)
        edge(begin + recip(k, k.width - p), R == PULSE ? -2.f : -2.f * k.blend, now, next);
    if (end >= 1.f) {
        wrap = begin + recip(k, 1.f - p);
        edge(wrap, R == SAW ? -2.f : R == PULSE ? 2.f : -2.f + 4.f * k.blend, now, next);
        end -= 1.f;
        if (R != SAW && end >= k.width) edge(wrap + recip(k, k.width), R == PULSE ? -2.f : -2.f * k.blend, now, next);
    }
    p = end;
    return wrap;
}

/* One sample of regime R: the phase over it (a corner, a wrap, a pulse edge, with their corrections), or a slave's reset
 * at e (the event byte; AN_NO_EVENT: none). Writes out_i, returns the master's event byte (AN_NO_EVENT: no wrap). */
template <int R> __attribute__((always_inline)) inline
unsigned char sample(float &p, float &delayed, const Shape &k, float &out_i, unsigned e) {
    float now = 0.f, next = 0.f;
    unsigned char m = AN_NO_EVENT;
    if (e != AN_NO_EVENT && k.inc > 0.f) {
        float at = (float)e * (1.f / 254.f);
        advance<R>(p, k, 0.f, at, now, next);
        edge(at, wave<R>(0.f, k) - wave<R>(p, k), now, next);
        /* P3 Q3, LOW: the slope steps too (the saw's does not, the pulse's is 0): from the triangle's -4 past its top and
         * the sine's 2 pi sin 2 pi p (= 2 pi sinq(tri(p + 1/4))) to the triangle's +4 and the sine's 0 at p = 0 */
        if (R == LOW) {
            float q = p + .25f;
            corner(at, k.inc * ((p < .5f ? 0.f : 8.f) * k.tw - 6.2831853f * k.ws * sinq(tri(q < 1.f ? q : q - 1.f))),
                   now, next);
        }
        p = 0.f;
        advance<R>(p, k, at, 1.f - at, now, next);
    } else {
        float wrap = advance<R>(p, k, 0.f, 1.f, now, next);
        if (wrap >= 0.f) { int f = (int)(wrap * 254.f + .5f); m = (unsigned char)(f < 0 ? 0 : f > 254 ? 254 : f); }
    }
    out_i = delayed + now;
    delayed = wave<R>(p, k) + next;
    return m;
}

template <int R, int ROLE>
void run(AnOsc &o, const Shape &k, float *out, unsigned n, unsigned char *events) {
    float p = o.phase, delayed = o.delayed;
    for (unsigned i = 0; i < n; i++) {
        unsigned char m = sample<R>(p, delayed, k, out[i], ROLE == SLAVE ? events[i] : AN_NO_EVENT);
        if (ROLE == MASTER) events[i] = m;
    }
    o.phase = p;
    o.delayed = delayed;
}

/* P3 Q3b: LOW's two plateaus, the sine (ws = 1) and the triangle (tw = 1), which run<LOW> rendered at the whole blend's
 * cost every sample (1.77 x a saw voice, Q3). A sample where the phase may reach a corner (1/2, the wrap) or a slave has
 * an event goes the long way, low_step: LOW's own sample (out of line, shared), so those samples are LOW's bit for bit.
 * Between them runs a stretch of m samples that cannot reach the next corner lim (1/2 or 1): after m steps p += inc, each
 * rounded to float32 below 1 (at most 2^-25 off), p_m <= p + m (inc + 2^-25); m = floor((lim - p) g) with
 * g = rinc (1 - 2^-16) - rinc^2 2^-22 keeps p_m < lim (rinc^2 2^-22 inc >= rinc 2^-25 covers the steps' roundings, 2^-16
 * those of lim - p, rinc, g and the product, 2^-24 each). Such a sample has no correction (LOW's now = next = 0), so
 * out = delayed, delayed = the wave at the new phase: the triangle tri(p), LOW's bit for bit; the sine, on a half cycle,
 * sinq of t = 4 s (p - c) (c 1/4 rising, s 1; 3/4 falling, s -1), the 4 and s folded into sinq's coefficients (powers of
 * two: exact), one subtraction where tri() takes three: p - c rounds once where 1/4 - |p - 1/2| may round twice, so within
 * 1.8e-7 of LOW's (M). The phase (p += inc, -1 at the wrap in low_step) is LOW's law, bit for bit. */
__attribute__((noinline)) unsigned char low_step(float *p, float *delayed, float inc, float rinc, float tw, float *out_i,
                                                 unsigned e) {
    Shape k;                          /* (by value: an address-taken Shape would spill an_render's for every regime) */
    k.inc = inc; k.rinc = rinc; k.tw = tw; k.ws = 1.f - tw; k.wsaw = 0.f; k.low = 1;
    return sample<LOW>(*p, *delayed, k, *out_i, e);
}
enum Plateau { SINE, TRI };
/* Above it the plateaus take run<LOW>: two corners a cycle, each a long-way sample (~100 instructions), outweigh the stretch
 * near inc 0.1 (M, an_render free per sample: sine / triangle 32.8 / 30.0 against LOW's 35.3 at inc 0.09, 40.5 / 38.0
 * against 36.3 at 0.125). 0.09 is 8.6 kHz at the device's 96 kHz (above note 120), 4.3 kHz in Rig()'s 48 kHz. */
const float FLAT_INC = .09f;
/* m samples of the stretch (fewer if a slave meets an event: that sample goes the long way); y, ev and p, delayed move on.
 * The sine in pairs (the loop's count and branch once per two), an odd one after them. */
template <int F, int ROLE> __attribute__((always_inline)) inline
void stretch(float *&y, unsigned char *&ev, unsigned m, float &p, float &delayed, float inc, int lo) {
    if (F == SINE) {
        float c = lo ? .25f : .75f, s = lo ? 1.f : -1.f;
        float s0 = s * (4.f * 1.570243f), s1 = s * (64.f * -.641711175f), s2 = s * (1024.f * .0714682192f);
        auto one = [&](float *q) {
            p += inc;
            float d = p - c, w = d * d;
            *q = delayed;
            delayed = d * (s0 + w * (s1 + w * s2));
        };
        float *stop = y + (m & ~1u);
#pragma clang loop unroll(disable)
        for (; y != stop; y += 2) {
            if (ROLE == SLAVE && (ev[0] != AN_NO_EVENT || ev[1] != AN_NO_EVENT)) {
                if (ev[0] == AN_NO_EVENT) { one(y++); ev++; }   /* (review: ev[0] in the stretch, only ev[1] the long way) */
                return;
            }
            one(y); one(y + 1);
            if (ROLE == MASTER) ev[0] = ev[1] = AN_NO_EVENT;
            if (ROLE != FREE) ev += 2;
        }
        if ((m & 1u) && !(ROLE == SLAVE && *ev != AN_NO_EVENT)) {
            one(y++);
            if (ROLE == MASTER) *ev = AN_NO_EVENT;
            if (ROLE != FREE) ev++;
        }
    } else {
        float *stop = y + m;
#pragma clang loop unroll(disable)
        for (; y != stop; y++) {
            if (ROLE == SLAVE && *ev != AN_NO_EVENT) return;
            p += inc;
            *y = delayed;
            delayed = tri(p);
            if (ROLE == MASTER) *ev = AN_NO_EVENT;
            if (ROLE != FREE) ev++;
        }
    }
}
template <int F>
__attribute__((noinline)) void run_flat(AnOsc &o, float inc, float rinc, float *out, unsigned n, unsigned char *events,
                                        int role) {
    float p = o.phase, delayed = o.delayed;
    float g = rinc * (1.f - 1.f / 65536.f) - rinc * rinc * (1.f / 4194304.f);
    float *y = out, *end = out + n;
    unsigned char *ev = events;
    while (y != end) {
        int lo = p < .5f;
        float x = ((lo ? .5f : 1.f) - p) * g;
        unsigned m = x >= 1.f ? (x < 65536.f ? (unsigned)x : 65536u) : 0u, left = (unsigned)(end - y);
        if (m > left) m = left;
        if (role == FREE) stretch<F, FREE>(y, ev, m, p, delayed, inc, lo);
        else if (role == MASTER) stretch<F, MASTER>(y, ev, m, p, delayed, inc, lo);
        else stretch<F, SLAVE>(y, ev, m, p, delayed, inc, lo);
        if (y == end) break;
        float lp = p, ld = delayed;      /* (copies: p, delayed with their address taken would live on the stack) */
        unsigned char e = low_step(&lp, &ld, inc, rinc, F == TRI ? 1.f : 0.f, y++, role == SLAVE ? *ev : AN_NO_EVENT);
        p = lp; delayed = ld;
        if (role == MASTER) *ev = e;
        if (role != FREE) ev++;
    }
    o.phase = p;
    o.delayed = delayed;
}

template <int ROLE> void by_regime(AnOsc &o, const Shape &k, float *out, unsigned n, unsigned char *events) {
    if (k.low) {                      /* P3 Q3: one generic regime below the saw; Q3b: its plateaus on their own, below FLAT_INC */
        if (k.tw == 1.f && k.inc < FLAT_INC) run_flat<TRI>(o, k.inc, k.rinc, out, n, events, ROLE);
        else if (k.ws == 1.f && k.inc < FLAT_INC) run_flat<SINE>(o, k.inc, k.rinc, out, n, events, ROLE);
        else run<LOW, ROLE>(o, k, out, n, events);
        return;
    }
#if AN_E1
    if (k.blend <= 0.f) run<SAW, ROLE>(o, k, out, n, events);
    else if (k.blend >= 1.f) run<PULSE, ROLE>(o, k, out, n, events);
    else
#endif
        run<MIXED, ROLE>(o, k, out, n, events);
}

}  // namespace

/* Phase and sync events only (WT1 silent but the sync master: plan E3). The phase moves exactly as in
 * an_render; `delayed` restarts from the waveform when the oscillator is heard again (one sample). */
extern "C" void an_track(AnOsc *o, float inc, unsigned char *events, unsigned n) {
    if (!(inc > 0.f)) inc = 0.f;
    if (inc > .45f) inc = .45f;
    float p = o->phase, rinc = inc > 0.f ? 1.f / inc : 0.f;
    for (unsigned i = 0; i < n; i++) {
        float end = p + inc;
        unsigned char m = AN_NO_EVENT;
        if (end >= 1.f) {
            float wrap = (1.f - p) * rinc;
            int f = (int)(wrap * 254.f + .5f);
            m = (unsigned char)(f < 0 ? 0 : f > 254 ? 254 : f);
            end -= 1.f;
        }
        if (events) events[i] = m;
        p = end;
    }
    o->phase = p;
    o->delayed = 0.f;
}

/* BE4.40 mixed sync: WT1 (a Wavetable oscillator, stock's render) as the master of an Analog WT2. The master's phase is
 * o->mphase:mphase_lo, a 64-bit fraction of a cycle, advanced by inc's exact value (an_inc64): stock's own phase is the sum
 * of rate / 2048 from 0 at a note start (M, wf/sync kstate.py), exactly while stock's per-sample step rate / D is at least
 * 0.5 (no truncation, plan fix 3). Below that (a note under about 23 Hz at D 1, or pitch modulated down more than about 0.3
 * octave at D > 1: D is chosen at the note start) stock's reader rounds its f32 fraction (0x08050E6A) and the copy differs
 * by that rounding: M, rev_m7_1 m8.py, 1.1e-6 smp after 8 s at note 12 (I: under 1e-3 smp an hour).
 * Its wraps are placed analytically into events (0..254 where in the sample, as an_track writes them): a few operations per
 * wrap, not per sample. The last sample's wrap is decided by the exact phase, so a wrap on a chunk edge is neither lost
 * nor doubled (M: Golden test_master_edges). events 0: only the phase moves (WT2 silent). n <= AN_EVENTS (an_osc chunks a
 * longer block).
 * After the designer's prototype (wf/sync apply_proto.py an_master, measured against an exact model in mtest.py). */
static inline unsigned char ev_byte(float wrap) {
    int f = (int)(wrap * 254.f + .5f);
    return (unsigned char)(f < 0 ? 0 : f > 254 ? 254 : f);
}
static inline float q64f(unsigned long long x) {    /* a 64-bit fraction (2^64 = 1) as a float, no library helper */
    return (float)(unsigned)(x >> 32) * (1.f / 4294967296.f) + (float)(unsigned)x * (1.f / 4294967296.f / 4294967296.f);
}
extern "C" void an_master(AnOsc *o, float inc, unsigned char *events, unsigned n) {
    if (!(inc > 0.f)) inc = 0.f;
    if (inc > .45f) inc = .45f;
    unsigned long long q = an_inc64(inc), p = ((unsigned long long)o->mphase << 32) | o->mphase_lo;
    unsigned long long pe = p + (unsigned long long)n * q;  /* the phase after these n samples, exactly (mod one cycle) */
    if (events && n) {
        unsigned *ew = (unsigned *)events;
        for (unsigned i = 0; i < (n + 3u) / 4u; i++) ew[i] = 0xFFFFFFFFu;
        if (o->mrestart) events[0] = 0;                   /* the master restarted at the note start: so does the slave */
        if (q) {
            float rinc = 1.f / inc, t0 = (p ? q64f(0ull - p) : 1.f) * rinc;
            for (unsigned j = 0; ; j++) {
                float tw = t0 + (float)j * rinc;
                unsigned i = (unsigned)tw;
                float fr = tw - (float)i;
                if (fr == 0.f && i) { i--; fr = 1.f; }
                if (i >= n) break;
                events[i] = ev_byte(fr);
            }
            /* the chunk's edge, exactly: a float time within rounding of it may name the wrong side (the next chunk
             * starts from the exact phase, so a wrong side here would lose a wrap or give one twice). At most one wrap
             * per sample and none in two neighbouring samples (inc <= 0.45). */
            unsigned long long pl = pe - q;               /* the phase before the last sample */
            if (pe < pl) {                                /* in the last sample; the float time may also have put it a hair */
                events[n - 1u] = ev_byte(q64f(0ull - pl) * rinc);    /* early, at the end of the one before (fixed: */
                if (n > 1u) events[n - 2u] = AN_NO_EVENT;           /* rev_m7_1 m10.py, 442 of 4000; a restart is at 0, */
            }                                                       /* and with p 0 no wrap is before sample 2) */
            else if (n > 1u && events[n - 1u] != AN_NO_EVENT) {
                events[n - 1u] = AN_NO_EVENT;             /* not in the last sample: the next chunk's, or ... */
                if (pl < q) events[n - 2u] = ev_byte(q64f(0ull - (pl - q)) * rinc);   /* ... the end of the one before */
            }
        }
    }
    o->mrestart = 0;
    o->mphase = (unsigned)(pe >> 32);
    o->mphase_lo = (unsigned)pe;
}

/* inc: cycles per sample. shape -1..1 (P3 Q3: below 0 the LOW regime). master: write this oscillator's wraps into events;
 * slave: reset at the events (n <= AN_EVENTS when either is set). */
extern "C" void an_render(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role) {
    Shape k;
    if (!(inc > 0.f)) inc = 0.f;
    if (inc > .45f) inc = .45f;
    k.inc = inc;
    k.rinc = inc > 0.f ? 1.f / inc : 0.f;
    set_shape(k, shape);
    if (role == AN_MASTER && events) by_regime<MASTER>(*o, k, out, n, events);
    else if (role == AN_SLAVE && events) by_regime<SLAVE>(*o, k, out, n, events);
    else by_regime<FREE>(*o, k, out, n, events);
}

/* P3 Q2 Bit Depth (docs/plan-p3-fm-bits.md 4.7, OD11 = (b)): a true b-bit two's-complement grid on the block, in place,
 *   q = clamp(rint((x + dc) S), -S, S - 1) / S - dc,   S = 2^(b-1): bits 1 -> 12 (2048), 2 -> 8 (128), 3 (and past it,
 *   OD4) -> 4 (8).
 * P3 Q2b (OD12 = (b), Jeff 2026-11-15): dc is the kernel's own DC term of the part (an_dc), so the DC-free pulse goes back on
 * its [-1, 1] range for the grid and comes off it again: B (Q6) stays DC-free, 12 / 8 bits near-transparent above Shape 88
 * (the +1 high still loses one step, as the square's does). Exactly 2^b levels for one dc; the output sits dc off the grid.
 * The top clips at (S-1)/S, the bottom at -1 (of x + dc). S is a power of two, so (x + dc) S and the level / S are exact;
 * the add and the subtract round once each (float32): p3_golden.crush bit for bit. dc 0 is Q2's grid bit for bit, sign of
 * zero included (x + -0 is x, q - +0 is q). rint is VRINTX (round half to even, FPSCR's RN): __builtin_rintf, since plain
 * rintf is a library call under -fno-builtin (build.py refuses it by name, as fminf / fmaxf: the clamp is two compares).
 * bits 0 (Full): untouched (an_osc does not call it then). */
extern "C" void an_crush(float *s, unsigned n, unsigned bits, float dc) {
    if (!bits) return;
    float S = bits == 1u ? 2048.f : bits == 2u ? 128.f : 8.f;
    float r = bits == 1u ? 1.f / 2048.f : bits == 2u ? 1.f / 128.f : 1.f / 8.f, lo = -S, hi = S - 1.f;
    float up = dc != 0.f ? dc : -0.f, down = dc != 0.f ? dc : 0.f;
    for (unsigned i = 0; i < n; i++)
        s[i] = __builtin_fminf(__builtin_fmaxf(__builtin_rintf((s[i] + up) * S), lo), hi) * r - down;
}

/* P3 Q2b (OD12): the DC term the kernel takes off its waveform at `shape`, set_shape's k.dc in the same arithmetic (the
 * pulse's 2 width - 1; up to 0.5 the width is 0.5, so 0 exactly): a part's dc for an_crush. 0 for the saw, the square,
 * below the saw (LOW has no DC term) and NaN. (set_shape itself is 184 B here: its LOW branch.) test_analog pins it to the
 * kernel's pulse levels, 1 - dc and -1 - dc, bit for bit. */
extern "C" float an_dc(float shape) {
    if (!(shape > .5f)) return 0.f;
    if (shape > 1.f) shape = 1.f;
    float width = .5f - (shape - .5f) * .84f;
    return 2.f * width - 1.f;
}

/* BE4.40 Shape modulation (J4): the shape changes between two an_render calls (from -> to) at the current phase. The
 * waveform steps there by wave_to(p) - wave_from(p) (a pulse edge too, where the width moved past p); this is that step's
 * correction at fraction 0 of the next sample (edge(0, jump): half of it now, none in the one after), added to the sample
 * already in `delayed`. The MIXED form is every regime's waveform at shape >= 0 (blend 0 / 1 give the saw's / the
 * pulse's). P3 Q3 wave_any: the LOW form below 0, MIXED (unchanged, bit for bit) at and above. Still a value jump only, no
 * BLAMP for the slope change of one AN_RAMP step (plan 4.6: small, I; ShapeLow's click test measures the result). */
static float wave_any(float p, const Shape &k) { return k.low ? wave<LOW>(p, k) : wave<MIXED>(p, k); }
/* (out of line, and chosen before any Shape is built: deciding after both set_shape calls made an_reshape +41 % (+56 % with
 * the Shapes passed to an out-of-line form) on P3Identity's mod case, the case +2.2 % against M1's +2 % (M); this way +9 %,
 * the case +0.9 %) */
__attribute__((noinline)) static void reshape_low(AnOsc *o, float from, float to) {
    Shape a, b;
    set_shape(a, from); set_shape(b, to);
    o->delayed += .5f * (wave_any(o->phase, b) - wave_any(o->phase, a));
}
extern "C" void an_reshape(AnOsc *o, float from, float to) {
    if (from < 0.f || to < 0.f) { reshape_low(o, from, to); return; }
    Shape a, b;
    set_shape(a, from); set_shape(b, to);
    float p = o->phase;
    o->delayed += .5f * (wave<MIXED>(p, b) - wave<MIXED>(p, a));
}

/* P3 Q4 (docs/plan-p3-fm-bits.md 4.2, OD10): the sub oscillator's waveform for this block, as stock's sub core will render
 * it after both oscillators (0x08056720, read by hand; rc26 fm_proto.cpp sub_copy, re-proven at 2N), into a[0..n) in
 * increment units: a[i] = (y[i] - dc) inc 2^-32. One shot from the core as it is before the sub renders: its phase +8 is
 * this block's start, inc this block's increment (an_osc's sub_inc), pend this block's pulse width (Square only; the core
 * latches it into its width +0x18 at the first wrap, as stock's 0x08056964..70). The phase is a local: the sub's own
 * render moves it. dc (OD10): the Square's 1 - 2 pw, so Sub FM by a Square does not detune its carrier; the others 0.
 * Waves 1 Rev Saw, 2 Triangle, 3 Square, 4 Sine (the core's table +4, linear between entries), 5 Noise (S_SUB_NOISE_TABLE,
 * stepped by the core's stride +0x10, not the increment). Stock's core has 10 waves (its LFOs'); the sub's key 0xC3 stores
 * value + 1 unclamped (0x080A0CC6), so a hand-edited file can reach 6..10 (or 0): their copy is 0 (no Sub FM), as for no
 * render. Stock's own constants K31 / K30 (0x08056A30 / A38: a hair below 2^-31 / 2^-30) and its operation order, so y is
 * stock's sample bit for bit (test_analog SubCopy: worst 0).
 * The Square's dc is the new width's from the block's start; before the first wrap the old width +0x18 plays, so a PW
 * change leaves a DC of 2 (pend - w) 2^-32 until that wrap (accepted, like the Rev Saw's residual: plan 4.2).
 * minsize (M, scratch p3/q4 variant.py): 460 B against 1,968 at this file's -O2 (unrolled loops), for +9 % (the Sine) to
 * +42 % (Noise) on sub_fill (its stock helpers included; more on the copy's own loop), +430..880 instructions a
 * voice-block with Sub FM; the 14 KB ceiling is the scarcer budget for Q5 / Q6. Loop pragmas instead (no unroll): 536 B
 * and the Sine slower than minsize; all but the Sine's: 1,004 B. */
extern "C" __attribute__((minsize)) void an_sub_copy(const void *core, float *a, unsigned n, unsigned inc, unsigned pend) {
    const unsigned char *c = (const unsigned char *)core;
    unsigned p = *(const unsigned *)(c + 8), wave = c[0];
    float f = (float)inc * 2.3283064365386963e-10f;                    /* 2^-32: exact after vcvt's one rounding */
    constexpr float K31 = 4.6566001055126094e-10f, K30 = 9.3132002110252190e-10f;
    switch (wave) {
    case 1:
        for (unsigned i = 0; i < n; i++, p += inc)
            a[i] = (p < inc ? -1.f + 2.f * ((float)p / (float)inc) : -1.f + (float)(~p) * K31) * f;
        return;
    case 2:
        for (unsigned i = 0; i < n; i++, p += inc) {
            int s = (int)p;
            a[i] = (-1.f + (float)(s >= 0 ? s : ~s) * K30) * f;
        }
        return;
    case 3: {
        unsigned w = *(const unsigned *)(c + 0x18);
        float dc = 1.f - (float)pend * 4.6566128730773926e-10f;          /* 1 - 2 pw: f32(pend) x 2^-31, one rounding (fma) */
        for (unsigned i = 0; i < n; i++, p += inc) {
            float y;
            if (p < inc) { y = 1.f - 2.f * ((float)p / (float)inc); if (pend) { w = pend; pend = 0; } }
            else if (p < w) y = -1.f;
            else if (p < w + inc) y = -1.f + 2.f * ((float)(p - w) / (float)inc);
            else y = 1.f;
            a[i] = (y - dc) * f;
        }
        return;
    }
    case 4: {
        const float *t = *(const float *const *)(c + 4);
        for (unsigned i = 0; i < n; i++, p += inc) {
            float x = t[p >> 20], y = t[(p + 0x100000u) >> 20];
            a[i] = (x - (x - y) * ((float)(int)(p & 0xFFFFFu) * 9.5367431640625e-07f)) * f;
        }
        return;
    }
    case 5: {
        const float *t = (const float *)S_SUB_NOISE_TABLE;
        unsigned stride = *(const unsigned *)(c + 0x10);
        for (unsigned i = 0; i < n; i++, p += stride) a[i] = t[p & 0xFFFu] * f;
        return;
    }
    default:
        for (unsigned i = 0; i < n; i++) a[i] = 0.f;
    }
}
