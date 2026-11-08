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

/* inc: cycles per sample. shape 0..1. master: write this oscillator's wraps into events; slave: reset at
 * the events (n <= AN_EVENTS when either is set). */
extern "C" void an_render(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role) {
    Shape k;
    if (!(inc > 0.f)) inc = 0.f;
    if (inc > .45f) inc = .45f;
    if (!(shape >= 0.f)) shape = 0.f;
    if (shape > 1.f) shape = 1.f;
    k.inc = inc;
    k.rinc = inc > 0.f ? 1.f / inc : 0.f;
    k.blend = shape * 2.f > 1.f ? 1.f : shape * 2.f;
    k.width = .5f - (shape > .5f ? shape - .5f : 0.f) * .84f;
    k.dc = 2.f * k.width - 1.f;
    if (role == AN_MASTER && events) by_regime<MASTER>(*o, k, out, n, events);
    else if (role == AN_SLAVE && events) by_regime<SLAVE>(*o, k, out, n, events);
    else by_regime<FREE>(*o, k, out, n, events);
}
