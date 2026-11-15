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

enum Regime { SAW, PULSE, MIXED };
enum Role { FREE, MASTER, SLAVE };

struct Shape {
    float inc, rinc, blend, width, dc;
};

inline void set_shape(Shape &k, float shape) {
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

/* Advance the phase over [begin, begin + length) of one sample; corrections into now / next. Returns the
 * wrap position in the sample (0..1), or -1 if the phase did not wrap. */
template <int R> inline float advance(float &p, const Shape &k, float begin, float length, float &now, float &next) {
    float wrap = -1.f;
    if (length <= 0.f) return wrap;
    float end = p + k.inc * length;
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

template <int R, int ROLE>
void run(AnOsc &o, const Shape &k, float *out, unsigned n, unsigned char *events) {
    float p = o.phase, delayed = o.delayed;
    for (unsigned i = 0; i < n; i++) {
        float now = 0.f, next = 0.f;
        unsigned e = ROLE == SLAVE ? events[i] : AN_NO_EVENT;
        if (ROLE == SLAVE && e != AN_NO_EVENT && k.inc > 0.f) {
            float at = (float)e * (1.f / 254.f);
            advance<R>(p, k, 0.f, at, now, next);
            edge(at, wave<R>(0.f, k) - wave<R>(p, k), now, next);
            p = 0.f;
            advance<R>(p, k, at, 1.f - at, now, next);
        } else {
            float wrap = advance<R>(p, k, 0.f, 1.f, now, next);
            if (ROLE == MASTER) {
                unsigned char m = AN_NO_EVENT;
                if (wrap >= 0.f) { int f = (int)(wrap * 254.f + .5f); m = (unsigned char)(f < 0 ? 0 : f > 254 ? 254 : f); }
                events[i] = m;
            }
        }
        out[i] = delayed + now;
        delayed = wave<R>(p, k) + next;
    }
    o.phase = p;
    o.delayed = delayed;
}

template <int ROLE> void by_regime(AnOsc &o, const Shape &k, float *out, unsigned n, unsigned char *events) {
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

/* inc: cycles per sample. shape 0..1. master: write this oscillator's wraps into events; slave: reset at
 * the events (n <= AN_EVENTS when either is set). */
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
 *   q = clamp(rint(x S), -S, S - 1) / S,   S = 2^(b-1): bits 1 -> 12 (2048), 2 -> 8 (128), 3 (and past it, OD4) -> 4 (8).
 * Exactly 2^b levels; the top clips at (S-1)/S, the bottom at -1. S is a power of two, so x S and q / S are exact and the
 * result is p3_golden.crush bit for bit (sign of zero included). rint is VRINTX (round half to even, FPSCR's RN):
 * __builtin_rintf, since plain rintf is a library call under -fno-builtin (build.py refuses it by name, as fminf / fmaxf:
 * the clamp is two compares). bits 0 (Full): untouched (an_osc does not call it then). */
extern "C" void an_crush(float *s, unsigned n, unsigned bits) {
    if (!bits) return;
    float S = bits == 1u ? 2048.f : bits == 2u ? 128.f : 8.f;
    float r = bits == 1u ? 1.f / 2048.f : bits == 2u ? 1.f / 128.f : 1.f / 8.f, lo = -S, hi = S - 1.f;
    for (unsigned i = 0; i < n; i++) s[i] = __builtin_fminf(__builtin_fmaxf(__builtin_rintf(s[i] * S), lo), hi) * r;
}

/* BE4.40 Shape modulation (J4): the shape changes between two an_render calls (from -> to) at the current phase. The
 * waveform steps there by wave_to(p) - wave_from(p) (a pulse edge too, where the width moved past p); this is that step's
 * correction at fraction 0 of the next sample (edge(0, jump): half of it now, none in the one after), added to the sample
 * already in `delayed`. The MIXED form is every regime's waveform (blend 0 / 1 give the saw's / the pulse's). */
extern "C" void an_reshape(AnOsc *o, float from, float to) {
    Shape a, b;
    set_shape(a, from); set_shape(b, to);
    float p = o->phase;
    o->delayed += .5f * (wave<MIXED>(p, b) - wave<MIXED>(p, a));
}
