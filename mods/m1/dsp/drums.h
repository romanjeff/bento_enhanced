/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Drum synths on one-shot pads, M7 side (S6c; docs/plan-synths-168.md sections 8, 9 and "S6c"). The state below lives
 * in the synth area (synth_shared.h): DRUM_CELLS cells of DRUM_CELL_BYTES, one per native voice of stock's one-shot
 * engine, and the run state. Both are M7 private; the M7 starts them over whenever it takes the area again (a new
 * generation). */
#ifndef DSP_DRUMS_H
#define DSP_DRUMS_H
#include "../synth_shared.h"

typedef unsigned u32;
typedef unsigned char u8;

/* A cell's state for the start it last saw (its voice's start serial): */
enum {
    CELL_NEW = 0,       /* bound to its voice, no start seen yet */
    CELL_SAMPLE = 1,    /* this start plays an ordinary sample (or a carrier whose engine is not built): stock renders */
    CELL_PLAYING = 2,   /* this start plays a drum synth: we render */
    CELL_DONE = 3,      /* the synth has finished: stock's render ends the voice (or, with Loop on, we render zeros) */
};

/* One cell per native voice, bound to it for the rest of this take of the area: stock's one-shot engine has a pool of
 * exactly 32 voices (S_DRUM_POOL_N, its pool constructor), so DRUM_CELLS cells always suffice. A start is recognised by
 * the voice's start serial (S_VOICE_SERIAL, written by both of stock's voice starts after the sample is open). */
struct DrumCell {
    u32 voice, root, handle, serial;      /* the voice (0: unbound); for the start seen: its root, pad handle, serial */
    u32 state, engine, frames, elapsed;   /* CELL_*; engine id; the carrier's frame count; frames rendered */
    u32 last_block, quiet, rng, knobs;    /* the block it last rendered in; quiet blocks in a row; xorshift32; knob serial */
    u8 values[DRUM_KNOBS];                /* the knob values in use (0..100; Tune 25..180 Hz) */
    u32 r14, r15;
    alignas(8) u8 engine_state[DRUM_CELL_BYTES - 64];
};
static_assert(sizeof(DrumCell) == DRUM_CELL_BYTES, "drum cell: 256 B");
static_assert(DRUM_CELLS <= 32u, "a cell is one bit of the 32-bit bound mask");

#define DRUM_CACHE_N 16u
/* The run state (one 512 B block): what the hooks keep between calls. */
struct DrumRun {
    u32 magic, gen;                       /* started for this take of the area (the M7 header's generation) */
    u32 idx, block;                       /* the LOAD ring index last seen; our block counter (one per new index) */
    u32 starts;                           /* drum starts so far (seeds each cell's noise) */
    u32 rng_cell;                         /* address of the rng word rand() works on (the cell being rendered) */
    u32 rng_spare;                        /* rand()'s state when no cell is current (never in practice) */
    u32 knob_serial;                      /* the M4's drum serial at this block (0: the M4 has published nothing) */
    u32 cyc, voices;                      /* this block: cycles in our hook, drum voices rendered */
    u32 bound;                            /* bit i: cell i is bound to a voice */
    struct { u32 b, y; } pow_memo[2];     /* powf(2, b) = y for the last two b (dsp_lib.cpp: constant within a block) */
    struct { u32 root, provider, file, frames, id; } cache[DRUM_CACHE_N];   /* carrier tests already made */
    u32 cache_next, r_[32];
};
static_assert(sizeof(DrumRun) == 512u, "drum run state: 512 B");
#define DRUN_MAGIC 0x4E524444u            /* "DDRN" */

/* An engine: the drum synth behind one carrier id. S6c builds the Kick; S6d adds the rest to the table in drums.cpp. */
struct DrumEngine {
    u32 id;
    const u8 *defaults;                   /* DRUM_KNOBS values */
    const u8 *lo, *hi;                    /* each knob's range */
    void (*start)(DrumCell *c);           /* the engine's Init, after the cell's header is set */
    void (*apply)(DrumCell *c, float ratio);   /* the knob values and the pitch ratio, once per block */
    void (*trigger)(DrumCell *c);
    float (*render)(DrumCell *c, float *out, u32 n);   /* n samples (gain 0.6, NaN -> 0, clipped to +-1); the peak */
};
#define DRUM_GAIN 0.6f                    /* upstream's level: a full-accent Kick peaks near 1 */
extern "C" const DrumEngine kick_engine;
/* The run state, for dsp_lib.cpp's powf memo (the area is ours whenever a drum engine runs). */
#define DRUM_RUN ((DrumRun *)SA(SA_DRUN))

#endif
