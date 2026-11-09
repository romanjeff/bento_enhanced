/* SPDX-License-Identifier: GPL-3.0-or-later */
/* The Kick (S6c): DaisySP's SyntheticBassDrum (daisysp/, MIT, unmodified; after Emilie Gillet's Plaits) in a drum cell.
 * Knob map after bento-playground's K7 Kick (eu4_work/drums/kick.cpp apply() at 9fc7787, GPL-3.0-or-later): Tune in
 * Hz times the pad's pitch ratio, then Decay, Tone, Accent, Sweep (FM envelope amount), Sweep Decay and Dirt as
 * 0..100 % of DaisySP's own 0..1 parameters, set once per block. Not taken from upstream: its Punch and Click
 * (they need DaisySP modified: an unclamped FM amount and a click level; S6d may add them as our own code), and its
 * output multiplied by the voice's velocity (velocity reaches the sound through the pad's routes only, decision D7).
 * Built -O2 with the vendored synthbassdrum.cpp / svf.cpp (the per-sample loop); see build.py DSP_OPT. */
#include "drums.h"
#include "Drums/synthbassdrum.h"

namespace {
const u8 DEFAULTS[DRUM_KNOBS] = {55, 65, 45, 65, 55, 25, 25, 0};    /* upstream K7's Kick (Dirt 25) */
const u8 LO[DRUM_KNOBS] = {25, 0, 0, 0, 0, 0, 0, 0};
const u8 HI[DRUM_KNOBS] = {180, 100, 100, 100, 100, 100, 100, 0};
static_assert(sizeof(daisysp::SyntheticBassDrum) <= sizeof(((DrumCell *)0)->engine_state), "the Kick fits a cell");

inline daisysp::SyntheticBassDrum *kick(DrumCell *c) { return (daisysp::SyntheticBassDrum *)c->engine_state; }

void start(DrumCell *c) { kick(c)->Init(48000.f); }

void apply(DrumCell *c, float ratio) {
    daisysp::SyntheticBassDrum *k = kick(c);
    const u8 *v = c->values;
    k->SetFreq((float)v[0] * ratio);
    k->SetDecay((float)v[1] * .01f);
    k->SetTone((float)v[2] * .01f);
    k->SetAccent((float)v[3] * .01f);
    k->SetFmEnvelopeAmount((float)v[4] * .01f);
    k->SetFmEnvelopeDecay((float)v[5] * .01f);
    k->SetDirtiness((float)v[6] * .01f);
}

void trigger(DrumCell *c) { kick(c)->Trig(); }

float render(DrumCell *c, float *out, u32 n) {
    daisysp::SyntheticBassDrum *k = kick(c);
    float peak = 0.f;
    for (u32 i = 0; i < n; i++) {
        float x = k->Process() * DRUM_GAIN;
        if (!(x == x)) x = 0.f;
        x = x > 1.f ? 1.f : x < -1.f ? -1.f : x;
        out[i] = x;
        float a = x < 0.f ? -x : x;
        if (a > peak) peak = a;
    }
    return peak;
}
}  // namespace

extern "C" const DrumEngine kick_engine = {ENGINE_KICK, DEFAULTS, LO, HI, start, apply, trigger, render};
