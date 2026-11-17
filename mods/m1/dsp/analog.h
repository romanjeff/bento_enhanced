/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Analog oscillator state and kernel interface (S6a; BE4.40 per oscillator). The state lives in the oscillator's own
 * native wavetable-kernel area (oscillator +0x10C .. +0x1CC, S_OSC_KERNEL, 192 B), which stock's wavetable render owns
 * while the oscillator plays a wavetable and never touches while it is Analog (our render replaces stock's): no RAM of
 * ours per voice. Stock's note start writes only +0x1CC..+0x1E4, above it (measured; BE4.40 P0: no byte of the oscillator
 * outside that area is free of stock's writes, rc33 implA p0/oscfree.py).
 * BE4.40: the area holds AnOsc (the kernel's state, from +0x10C) and, at its top, the oscillator's own settings as stock
 * forwarded them (AnProps, +0x10C + AN_PROPS_OFF): an oscillator is Analog exactly while magic == AN_MAGIC and its Type is
 * 1. Leaving Analog zeroes the whole area at once (forget), so stock's render finds a zero history.
 * After bento-playground's AnalogOsc (eu4_work/drums/analog.h at 9fc7787, GPL-3.0-or-later). */
#ifndef DSP_ANALOG_H
#define DSP_ANALOG_H

#define AN_MAGIC 0x4f4c4e41u          /* "ANLO" */
#define AN_NO_EVENT 255u              /* events[]: 0..254 = where in the sample the master wrapped */
#define AN_NO_NOTE 0xFFFFu            /* AnOsc.note: none yet */
enum { AN_FE_START = 0, AN_FE_ATK = 1, AN_FE_DEC = 2, AN_FE_END = 3 };   /* BE4.43: AnOsc.fmk >> 13 */
#define AN_EVENTS 128u                /* one event array: the frames of one sync chunk (an_osc renders longer blocks in chunks) */
#define AN_SYNC_FRAMES 256u           /* both-Analog sync: two chunks (WT2's events, then WT1's own); mixed sync has no limit */
#define AN_AREA 0xC0u                 /* the native kernel area: +0x10C .. +0x1CC */
#define AN_PROPS_OFF 0xB4u            /* AnProps at its top: +0x1C0 .. +0x1CC (AnOsc must end below it) */
enum { AN_FREE = 0, AN_MASTER = 1, AN_SLAVE = 2 };
enum { AN_LIVE_NEW = 1, AN_LIVE_RUNS = 2, AN_LIVE_FROZEN = 3 };   /* AnOsc.mlive while the copy is WT1's */
#define AN_SHAPE_Q 512u               /* a modulated Shape is held to 1/512 of a step (a whole Shape is exact) */
#define AN_NO_SHAPE 0xFFFFu
#define AN_RAMP 32u                   /* a Shape change ramps over its block in steps of AN_RAMP frames */
#define AN_RAMP_LOW 16u               /* P3 Q3: ... and of AN_RAMP_LOW where it starts or ends below the saw (k < 0): that map
                                       * moves k twice as fast a Shape step (0.5 over 19 / 20 Shapes, saw > square 0.5 over 40),
                                       * so at AN_RAMP its click was up to 1.08 x saw > square's, at this 0.5 to 0.75 x (M,
                                       * test_analog ShapeLow); a divisor of AN_EVENTS */

struct AnOsc {
    unsigned magic;
    float phase, delayed;
    /* hard sync, the master's wraps of the current chunk: in WT2's state frames 0..127 of the block; in WT1's state (both
     * Analog: WT1 is never a slave) frames 128..255 */
    unsigned char events[AN_EVENTS];
    /* glide, kept in WT1's state for the voice: the note playing (BE4.43: 16 bits, AN_NO_NOTE none; stock's note numbers
     * are far below it), the pitch offset still to slide (octaves), what of it was last added to the sub oscillator, and
     * the sub's +0x04 as we left it (stock rewrites that field at every note start: then its new value is the base, review
     * 2026-11-08) */
    unsigned short note;
    /* BE4.43 (docs/plan-p3-fm-bits.md BE4.43, p3_golden fm_env): the FM envelope's position, both oscillators: stage
     * (AN_FE_*) << 13 | blocks into it (at most 3750). AN_FE_START (0, also after reset / forget): a note started (an_note,
     * every caller of slot 2) and the next block starts the envelope at 0 (1 for FM Atk 0) */
    unsigned short fmk;
    float glide, sub_applied, sub_written;
    /* BE4.40 mixed sync (WT1 Wavetable, WT2 Analog), in WT2's state: WT1's cycle phase as a 64-bit fraction (mphase the
     * high word: 2^32 = one cycle; mphase_lo below it), exact for any float rate (plan fix 3); mrestart: WT1's note start
     * restarted it (the slave restarts with it at frame 0 of the next block); mlive: 0 the copy is not WT1's (cleared while
     * mixed sync is off: the next engage seeds it from stock's phase, plan fix 2), else AN_LIVE_*: stock's kernel watched
     * through msnap, a digest of WT1's kernel phase after WT1's render (unchanged over a block: stock's kernel did not run,
     * at level 0 or with no WAV, and the copy runs on; changed again after that: it runs again, the copy is seeded from it) */
    unsigned mphase, mphase_lo;
    unsigned char mrestart, mlive;
    unsigned short mshape;            /* Shape modulation (J4): the Shape the last block ended on x AN_SHAPE_Q, AN_NO_SHAPE none */
    unsigned msnap;
    /* P3 (docs/plan-p3-fm-bits.md 3.3, 4.4): the FM amounts the last block ended on (0..1000; the FM sprint ramps from them
     * over the next block): msub the Sub FM, m12 FM 1>2 (OSC2's). 0 after reset / forget, on the silent skip and after a
     * block with no FM: 0 is a real amount, so the first block heard after that ramps the index in from 0 (2.7 ms at 2N;
     * Q5: plan 4.4's "no ramp" was not what 0 does; OD16). P3 Q5: msub ramps Sub FM (an_osc); Q6: m12 FM 1>2 (0 on OSC1) */
    unsigned short msub, m12;
    float fme;                        /* BE4.43: the envelope at the end of the last block stepped (env x the amount's index) */
};
static_assert(sizeof(AnOsc) == 180, "Analog state: 180 bytes at oscillator +0x10C (none left below AnProps)");
static_assert(sizeof(AnOsc) <= AN_PROPS_OFF, "Analog state must stay below the oscillator's settings (AnProps)");

/* An oscillator's settings, as stock's set_property delivered them (N3: dsp/analog.cpp an_prop), each clamped when stored.
 * P3 Q1: subfm and fm12 are stored (and so reach the M7 with every push, edit and resend). P3 Q5: subfm plays (Sub FM,
 * an_render_fm); Q6: fm12 plays (OSC2's FM 1>2, from an Analog OSC1). BE4.42: Bit Depth is gone. BE4.43: byte +3 (BE4.41's
 * Bit Depth) is FM Atk and +10 FM Dec, the oscillator's FM envelope (key 0x1A4 is FM Atk's now; a BE4.41 project's
 * bento_bits is still not read: FM Atk's token is bento_fmatk). Sub FM is OSC1's FM, and OSC2's only while OSC1 is not
 * Analog (then OSC2's is FM 1>2): the envelope scales the oscillator's own. */
struct AnProps {
    unsigned char type;               /* 1 = Analog (a value of exactly 1; anything else is Wavetable) */
    unsigned char shape;              /* 0..AN_SHAPE_MAX */
    unsigned char sync;               /* 0 / 1 (WT2's: the slave) */
    unsigned char fmatk;              /* BE4.43: FM Atk 0..AN_FM_ATK_MAX (0 instant) */
    unsigned short glide;             /* 0..AN_GLIDE_MAX (WT1's: the voice's) */
    unsigned short subfm, fm12;       /* Sub FM, FM 1>2 0..AN_FM_MAX (fm12 is OSC2's; WT1's copy is kept but never read) */
    unsigned char fmdec;              /* BE4.43: FM Dec 0..AN_FM_DEC_INF (Inf: hold, the default) */
    unsigned char envf;               /* BE4.43: 1 while FM Atk != 0 or FM Dec != Inf (the envelope runs), set by an_prop */
};
static_assert(sizeof(AnProps) == 12, "settings: 12 bytes");
static_assert(AN_PROPS_OFF + sizeof(AnProps) == AN_AREA, "the settings end where stock's own fields start (+0x1CC)");
static_assert(AN_EVENTS % AN_RAMP == 0 && AN_EVENTS % AN_RAMP_LOW == 0, "a ramp's parts tile the sync chunks");

/* A float's exact value as a 64-bit fraction (2^64 = 1): m x 2^(e - 150) x 2^64 = m << (e - 86). For x < 0.5 the shift
 * is at most 39 (no overflow; callers clamp to 0.45); below 2^-40 low bits go (0.04 Hz at 96 kHz). 0 for 0, a denormal or a negative. */
static inline unsigned long long an_inc64(float x) {
    unsigned b;
    __builtin_memcpy(&b, &x, 4);
    int e = (int)((b >> 23) & 0xFFu);
    if (!(x > 0.f) || !e) return 0;
    unsigned long long m = (b & 0x7FFFFFu) | 0x800000u;
    int s = e - 86;
    return s >= 0 ? m << s : s > -24 ? m >> -s : 0;
}

/* P3 Q5 (docs/plan-p3-fm-bits.md 4.3): an_render_fm's modulation, the sample i of the call being the block's frame off + i:
 * di = inc + I a[off + i], I = ia + dia (off + i) (the block's index ramp, the same line across its parts). P3 Q6: plus a
 * second term J b[off + i], J = ib + dib (off + i), folded after the first (4.3's order: di = (inc + I a) + J b, one VFMA
 * each). an_osc passes a = A (the sub's copy) and b = B (OSC1's) with both routes on; one route alone is a, with b the same
 * buffer at a zero line (ib = dib = 0: + 0 b adds +0 to a finite b, so di is the one-term form bit for bit). */
struct FmIn {
    const float *a;                   /* the first modulator (A; B for FM 1>2 alone), increment units, from frame 0 */
    float ia, dia;                    /* its index at frame 0 and its step per frame */
    unsigned off;                     /* the call's first frame in the block */
    const float *b;                   /* P3 Q6: the second modulator (B: FM 1>2 next to Sub FM), from frame 0 */
    float ib, dib;                    /* its index line (0, 0: none) */
};

extern "C" void an_render(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role);
extern "C" void an_render_fm(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role,
                             const FmIn *f);
extern "C" void an_track(AnOsc *o, float inc, unsigned char *events, unsigned n);
extern "C" void an_master(AnOsc *o, float inc, unsigned char *events, unsigned n);
extern "C" void an_reshape(AnOsc *o, float from, float to);
extern "C" void an_sub_copy(const void *core, float *a, unsigned n, unsigned inc, unsigned pend);   /* P3 Q4: the sub's
                                                                           * block in increment units, DC-free (OD10) */
extern "C" void an_feed(const float *s, float *b, unsigned n, float inc);   /* P3 Q6: buffer B from a part (BD1),
                                                                           * b = s inc (BE4.42: no Bit Depth grid) */

#endif
