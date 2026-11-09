/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Literal MIDI note 0 (BE4.22, group note0, 1.6.8 only; docs/plan-ext-low-notes.md option A1).
 *
 * Stock's five M7 MIDI note senders (sequencer Note On / Off, pad Note On / Off, stop / all notes off) read
 * note 0 as "the pad's own drum note" and send pad 0..7 -> 44..51, 8..15 -> 36..43, else 52 instead. On an
 * External track that loses MIDI note 0 (it always goes out as 44). Each sender's drum-note block runs only
 * for note 0 on a MIDI-out track; build.py points the block's entry at a trampoline that calls note0_site()
 * and puts its answer in the sender's note register, then goes back to where stock builds the MIDI event.
 *
 * The rule (one rule for On, Off and all-off, so a note that went out also gets its Off): stock's drum note
 * where the track has an internal sound on that pad, MIDI note 0 everywhere else. "An internal sound on that
 * pad" = pad <= 15, the track's per-pad bus key (+0x86DC + 4 * pad, 0xFFFFFF00 = none, written only by the
 * constructor and the 0xA1 setting) is set, and the router still delivers to that key (its sorted key table:
 * count at +0x508, {key, mailbox} at +8 + 8 * i, which bus_post 0x0809F8A0 searches; a note with no entry is
 * dropped there). The last test is ours, not the plan's: the M4 sends 0xA1 only for an instrument's own pads
 * (0x081329E8: kits loop over their children, nothing for External), and the M7's eight track objects are made
 * once (0x08058A60 loop), so a slot that held a kit and became External can keep the kit's old keys. Such a
 * stale key has no entry in the router (the kit's engines are gone) and counts as "no sound" (counted apart:
 * the black box's 'stale').
 *
 * On and Off go out as the same note (review rc23 S2/I6): the rule is worked out at the two Note On sites (1, 3) and
 * remembered per (track slot +0x86D4, pad); the Off sites (2, 4, 5) and ratchet hits send what the last On on that
 * (slot, pad) sent, so replacing the instrument while a note-0 note sounds (the kit's keys leave the router) cannot
 * turn its Off into another note. Only where nothing is remembered (pad > 15, slot > 7, the Region not up or zeroed
 * since the On) is the rule worked out again. Not covered: two overlapping note-0 Ons on one (slot, pad) whose rule
 * changed between them (the first one's Off then follows the second On).
 *
 * Retrigger MIDI hits (m7_retrig.c: m1_note0_sent), Note Mod's drum test (m7_mod.c) and curve playback
 * (m7_polyat.c, at the On: m1_note0_map) use the same rule, so they send what stock's own hit sent.
 */
#include "diag_shared.h"

#define NOKEY 0xFFFFFF00u
#define PAD_KEYS 0x86DCu               /* track: per-pad bus keys, 16 x u32 */
#define BUS_COUNT 0x508u               /* router: number of bus keys (bus_post 0x0809F8A0: ldr.w r2,[r0,#0x508]) */
#define BUS_KEYS 8u                    /* router: {key, mailbox index} x count, from +8 */
#define BUS_MAX ((BUS_COUNT - BUS_KEYS) / 8u)   /* the table cannot run past its count word */

enum { N0_NOKEY = 0, N0_STALE = 1, N0_LIVE = 2 };

static u32 pad_sound(u32 track, u32 pad, u32 router, u32 *key) {
    *key = NOKEY;
    if (pad > 15u) return N0_NOKEY;
    u32 k = *(volatile u32 *)(track + PAD_KEYS + 4u * pad);
    *key = k;
    if (k == NOKEY) return N0_NOKEY;
    if (!router) return N0_STALE;
    u32 n = *(volatile u32 *)(router + BUS_COUNT);
    if (n > BUS_MAX) n = BUS_MAX;
    for (u32 i = 0; i < n; i++)
        if (*(volatile u32 *)(router + BUS_KEYS + 8u * i) == k) return N0_LIVE;
    return N0_STALE;
}

static u32 drum_note(u32 pad) { return pad <= 7u ? pad + 0x2Cu : pad + 0x1Cu; }   /* stock's map, pad <= 15 */

/* The MIDI note a note 0 goes out as on this track and pad: stock's drum note, or 0. */
u32 m1_note0_map(u32 track, u32 pad, u32 router) {
    u32 key;
    return pad_sound(track, pad, router, &key) == N0_LIVE ? drum_note(pad) : 0u;
}

/* The black box line (Region + REGION_NOTE0, m7_polyat.c): [0] sent as 0 (no key), [1] sent as 0 (a stale key),
 * [2] mapped, [3] the last decision: site | sound << 4 | track slot (+0x86D4) << 8 | pad << 16, [4] its key,
 * [5] held: Offs that sent their On's note where the rule now gives the other one. */
volatile u32 *m1_note0_line(void);
/* Region (m7_polyat.c, M7 only): the remembered Ons, per track slot: [15:0] pads with an On, [31:16] those mapped. */
volatile u32 *m1_note0_ons(void);

static u32 irq_off(void) { u32 p; __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory"); return p; }
static void irq_on(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }
static volatile u32 *on_word(u32 track, u32 pad) {
    u32 slot = *(volatile u32 *)(track + 0x86D4u);
    volatile u32 *t = slot < 8u && pad <= 15u ? m1_note0_ons() : 0;
    return t ? t + slot : 0;
}

/* The note a note 0 on this track and pad went out as at its last On (for ratchet hits); the rule if none. */
u32 m1_note0_sent(u32 track, u32 pad, u32 router) {
    volatile u32 *w = on_word(track, pad);
    u32 v = w ? *w : 0u, b = 1u << (pad & 15u);
    if (!(v & b)) return m1_note0_map(track, pad, router);
    return (v & (b << 16)) ? drum_note(pad) : 0u;
}

/* From the five trampolines (build.py NOTE0_SITES): site 1 sequencer On, 2 sequencer Off, 3 pad On, 4 pad Off,
 * 5 stop / all off. Returns the note the sender puts in its MIDI event. */
__attribute__((used, retain)) u32 note0_site(u32 track, u32 pad, u32 router, u32 site) {
    u32 key, s = pad_sound(track, pad, router, &key), mapped = s == N0_LIVE, held = 0;
    volatile u32 *w = on_word(track, pad);
    if (w) {
        u32 p = irq_off(), b = 1u << pad, v = *w;
        if (site == 1u || site == 3u) *w = ((v | b) & ~(b << 16)) | (mapped ? b << 16 : 0u);
        else if ((v & b) && (((v >> 16) & b) != 0u) != mapped) { mapped = !mapped; held = 1; }
        irq_on(p);
    }
    volatile u32 *l = m1_note0_line();
    if (l) {
        l[mapped ? N0_LIVE : s == N0_LIVE ? N0_NOKEY : s]++;
        l[5] += held;
        l[3] = (site & 15u) | (s << 4) | ((*(volatile u32 *)(track + 0x86D4u) & 0xFFu) << 8) | ((pad & 0xFFFFu) << 16);
        l[4] = key;
        __asm volatile("dsb" ::: "memory");
        *(volatile u32 *)0xE000EF68u = (u32)l;          /* DCCMVAC: the M4 reads this line */
    }
    return mapped ? drum_note(pad) : 0u;
}
