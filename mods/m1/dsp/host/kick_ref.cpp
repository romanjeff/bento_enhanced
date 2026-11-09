// SPDX-License-Identifier: GPL-3.0-or-later
// Host reference of the Kick for test_drums.py (S6c review B2): the vendored, unmodified DaisySP SyntheticBassDrum
// (../daisysp, MIT) built by the host compiler against the host's own libm and libc headers, driven the way
// docs/plan-synths-168.md specifies the Kick. It shares no code with the DSP ELF: not our math (dsp_lib.cpp), not
// our glue (drums.cpp, drum_kick.cpp), not the shim headers. Never part of the firmware (build.py does not list it).
//
//     kick_ref SEED BLOCKS N TUNE DECAY TONE ACCENT SWEEP SWEEP_DECAY DIRT RATIO...
//
// Writes BLOCKS x N little-endian float32 samples to stdout: Init(48000), Trig(), then per block the knob values
// (Tune in Hz times that block's pitch ratio, the rest as 0..100 % of DaisySP's 0..1 parameters, times 0.01f) and N
// samples of Process() x 0.6, NaN as 0, clipped to +-1. RATIO is one value per block (the last one repeats).
// rand() below replaces libc's for DaisySP: the noise contract of the plan (xorshift32 per voice, seeded with SEED).
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include "Drums/synthbassdrum.h"

static uint32_t rng;
extern "C" int rand(void) noexcept {
    uint32_t v = rng;
    v ^= v << 13; v ^= v >> 17; v ^= v << 5;
    rng = v;
    return (int)(v & 0x7FFFFFFFu);
}

int main(int argc, char **argv) {
    if (argc < 12) return 2;
    rng = (uint32_t)strtoul(argv[1], nullptr, 0);
    int blocks = atoi(argv[2]), n = atoi(argv[3]);
    float k[7];
    for (int i = 0; i < 7; i++) k[i] = (float)atoi(argv[4 + i]);
    static daisysp::SyntheticBassDrum d;   // zeroed storage: Init leaves transient_env_lp_ to it (as a fresh cell does)
    d.Init(48000.f);
    d.Trig();
    for (int b = 0; b < blocks; b++) {
        int a = 11 + b < argc ? 11 + b : argc - 1;
        float ratio = strtof(argv[a], nullptr);
        d.SetFreq(k[0] * ratio);
        d.SetDecay(k[1] * .01f);
        d.SetTone(k[2] * .01f);
        d.SetAccent(k[3] * .01f);
        d.SetFmEnvelopeAmount(k[4] * .01f);
        d.SetFmEnvelopeDecay(k[5] * .01f);
        d.SetDirtiness(k[6] * .01f);
        for (int i = 0; i < n; i++) {
            float x = d.Process() * 0.6f;
            if (!(x == x)) x = 0.f;
            x = x > 1.f ? 1.f : x < -1.f ? -1.f : x;
            fwrite(&x, 4, 1, stdout);
        }
    }
    return 0;
}
