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
#define AN_EVENTS 128u                /* one event array: the frames of one sync chunk (an_osc renders longer blocks in chunks) */
#define AN_SYNC_FRAMES 256u           /* both-Analog sync: two chunks (WT2's events, then WT1's own); mixed sync has no limit */
#define AN_AREA 0xC0u                 /* the native kernel area: +0x10C .. +0x1CC */
#define AN_PROPS_OFF 0xB4u            /* AnProps at its top: +0x1C0 .. +0x1CC (AnOsc must end below it) */
enum { AN_FREE = 0, AN_MASTER = 1, AN_SLAVE = 2 };

struct AnOsc {
    unsigned magic;
    float phase, delayed;
    /* hard sync, the master's wraps of the current chunk: in WT2's state frames 0..127 of the block; in WT1's state (both
     * Analog: WT1 is never a slave) frames 128..255 */
    unsigned char events[AN_EVENTS];
    /* glide, kept in WT1's state for the voice: the note playing, the pitch offset still to slide
     * (octaves), what of it was last added to the sub oscillator, and the sub's +0x04 as we left it (stock
     * rewrites that field at every note start: then its new value is the base, review 2026-11-08) */
    unsigned note;
    float glide, sub_applied, sub_written;
    /* BE4.40 mixed sync (WT1 Wavetable, WT2 Analog), in WT2's state: WT1's cycle phase as a 64-bit fraction (mphase the
     * high word: 2^32 = one cycle; mphase_lo below it), exact for any float rate (plan fix 3); mrestart: WT1's note start
     * restarted it (the slave restarts with it at frame 0 of the next block); mlive: the phase is WT1's (set at a note start
     * with Sync on, cleared while mixed sync is off: the next engage seeds it from stock's phase, plan fix 2) */
    unsigned mphase, mphase_lo;
    unsigned char mrestart, mlive, mspare[2];
};
static_assert(sizeof(AnOsc) == 168, "Analog state: 168 bytes at oscillator +0x10C");
static_assert(sizeof(AnOsc) <= AN_PROPS_OFF, "Analog state must stay below the oscillator's settings (AnProps)");

/* An oscillator's settings, as stock's set_property delivered them (N3: dsp/analog.cpp an_prop), each clamped when stored.
 * Bytes left for the FM / Bit Depth sprint: bits (2 bits used), subfm and fm12 (0..1000). */
struct AnProps {
    unsigned char type;               /* 1 = Analog (a value of exactly 1; anything else is Wavetable) */
    unsigned char shape;              /* 0..AN_SHAPE_MAX */
    unsigned char sync;               /* 0 / 1 (WT2's: the slave) */
    unsigned char bits;               /* (FM sprint: Bit Depth) */
    unsigned short glide;             /* 0..AN_GLIDE_MAX (WT1's: the voice's) */
    unsigned short subfm, fm12;       /* (FM sprint) */
    unsigned short spare;
};
static_assert(sizeof(AnProps) == 12, "settings: 12 bytes");
static_assert(AN_PROPS_OFF + sizeof(AnProps) == AN_AREA, "the settings end where stock's own fields start (+0x1CC)");

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

extern "C" void an_render(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role);
extern "C" void an_track(AnOsc *o, float inc, unsigned char *events, unsigned n);
extern "C" void an_master(AnOsc *o, float inc, unsigned char *events, unsigned n);

#endif
