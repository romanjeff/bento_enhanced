/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Analog oscillator state and kernel interface (S6a). The state lives in the oscillator's own native
 * wavetable-kernel area (oscillator +0x10C, S_OSC_KERNEL), which an Analog track never uses: no RAM
 * of ours per voice. Stock's kernel reset writes only +0x1CC..+0x1E4 there (measured), above it.
 * After bento-playground's AnalogOsc (eu4_work/drums/analog.h at 9fc7787, GPL-3.0-or-later). */
#ifndef DSP_ANALOG_H
#define DSP_ANALOG_H

#define AN_MAGIC 0x4f4c4e41u          /* "ANLO" */
#define AN_NO_EVENT 255u              /* events[]: 0..254 = where in the sample the master wrapped */
#define AN_EVENTS 128u                /* sync works for blocks of up to this many frames */
enum { AN_FREE = 0, AN_MASTER = 1, AN_SLAVE = 2 };

struct AnOsc {
    unsigned magic;
    float phase, delayed;
    unsigned char events[AN_EVENTS];  /* in WT2's state: WT1's wraps of this block (hard sync) */
    /* glide, kept in WT1's state for the voice: the note playing, the pitch offset still to slide
     * (octaves), what of it was last added to the sub oscillator, and the sub's +0x04 as we left it (stock
     * rewrites that field at every note start: then its new value is the base, review 2026-11-08) */
    unsigned note;
    float glide, sub_applied, sub_written;
};
static_assert(sizeof(AnOsc) == 156, "Analog state: 156 bytes at oscillator +0x10C");
static_assert(sizeof(AnOsc) <= 0xC0, "Analog state must stay below the native kernel's own fields (+0x1CC)");

extern "C" void an_render(AnOsc *o, float inc, float shape, float *out, unsigned n, unsigned char *events, int role);
extern "C" void an_track(AnOsc *o, float inc, unsigned char *events, unsigned n);

#endif
