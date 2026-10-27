/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Per-step retrigger (ratchets), M7 scheduler. R2 prototype.
 *
 * Stock posts each accepted note once, through note_route (0x0805F8E0), with a
 * sample offset into the current audio block. Its event queues are per block,
 * so we cannot post future-dated events; instead we keep pending bursts and
 * post each extra hit from the block that contains it:
 *   retrig_accept  after stock's note_route at 0x08060B9A: queue a burst of N
 *                  hits at tick + k * division (playback ticks, 960 per quarter);
 *                  any new note on the same track + pad cancels a pending burst
 *   retrig_emit    at the entry of the timed-note processor (0x08060820), once
 *                  per track per block: post the hits that fall in this window
 *                  through note_route, using stock's tick -> sample conversion
 *                  (0x080A2200), so tempo is followed.
 * Hits more than LATE_SAMPLES behind (transport stopped, clip changed) are
 * dropped. Velocity of hit k (1..N) = vel * (1 + ramp% * k / N), 1..127.
 *
 * Prototype scope: settings are hard-coded for pad 16 (index 15) until the
 * per-note storage and encoders exist (R3/R4); hits go to the internal sound
 * only (MIDI out is masked while a hit is posted; MIDI retriggers need their
 * own note-offs).
 */
#include "diag_shared.h"

#define FN(a, t) ((t)((a) | 1u))
#define NOTE_ROUTE 0x0805F8E0u
#define TICK_TO_OFFSET 0x080A2200u   /* (clock, &tick64) -> sample offset from clock[0] */
#define MIDI_OUT_FLAG 0x86D1u        /* track byte checked by note_route */
#define LATE_SAMPLES 2400            /* 50 ms at 48 kHz */
#define BURSTS 16u

typedef struct {
    u32 track, router;
    u32 tick_lo, tick_hi;            /* the stock hit (k = 0) */
    u16 note, pad, vel, div;         /* div in playback ticks */
    u8 n, k, used, pad_;
    int ramp;                        /* percent, -100..100 */
} Burst; /* 32 bytes */

_Static_assert(BURSTS * sizeof(Burst) <= RETRIG_BYTES, "burst table larger than its slot");
void *m1_retrig_table(void); /* m7_polyat.c: validated for this boot, zeroed with the region */
static volatile Burst *bursts(void) { return (volatile Burst *)m1_retrig_table(); }
void curve_start(u32 track, u32 note, u32 word, u32 tick_lo, u32 tick_hi);   /* m7_polyat.c (M2-2) */
void curve_emit(u32 track, u32 clock, int start, int len);

typedef void (*route_fn)(u32 track, u32 note, u32 vel, u32 timing, u32 pad, u32 router);
typedef int (*offset_fn)(u32 clock, const u32 *tick64);

static const u16 DIV_TICKS[11] = {480, 320, 720, 240, 160, 360, 120, 80, 180, 60, 40};
/* 1/8, 1/8T, 1/8., 1/16, 1/16T, 1/16., 1/32, 1/32T, 1/32., 1/64, 1/64T */

static u32 ramp_vel(u32 vel, int ramp, u32 k, u32 n) {
    int v = (int)vel + (int)vel * ramp * (int)k / (100 * (int)n);
    return v < 1 ? 1u : v > 127 ? 127u : (u32)v;
}

/* Settings from the note's per-step word (diag_shared.h, written by m4_step.c). */
static int settings(u32 pad, const u32 *rec_word, u32 *n, u32 *div, int *ramp) {
    (void)pad;
    u32 w = *rec_word;
    *n = step_hits(w);
    *div = DIV_TICKS[step_div(w)];
    *ramp = step_ramp(w);
    return *n != 0;
}

/* csp = the caller's stack: [0] pad, [1] router, +0x20 tick64, +0x3C note word. */
__attribute__((used, retain)) void retrig_accept(u32 track, u32 note, const u32 *csp, u32 clock, u32 vel) {
    curve_start(track, note, csp[0x3C / 4], csp[0x20 / 4], csp[0x24 / 4]);   /* recorded pressure */
    volatile Burst *b = bursts();
    if (!b) return;
    (void)clock;
    u32 pad = csp[0] & 0xFFFFu, n, div;
    int ramp;
    volatile Burst *free_b = 0;
    for (u32 i = 0; i < BURSTS; i++) {
        if (b[i].used && b[i].track == track && b[i].pad == pad) b[i].used = 0;   /* cut off */
        if (!b[i].used && !free_b) free_b = &b[i];
    }
    if (!settings(pad, &csp[0x3C / 4], &n, &div, &ramp) || !free_b) return;
    free_b->track = track; free_b->router = csp[1];
    free_b->tick_lo = csp[0x20 / 4]; free_b->tick_hi = csp[0x24 / 4];
    free_b->note = (u16)note; free_b->pad = (u16)pad; free_b->vel = (u16)vel; free_b->div = (u16)div;
    free_b->n = (u8)n; free_b->k = 1; free_b->ramp = ramp;
    free_b->used = 1;
}

/* Called at the timed-note processor's entry: (track, clock, start, len, router). */
__attribute__((used, retain)) void retrig_emit(u32 track, u32 clock, int start, int len, u32 router) {
    curve_emit(track, clock, start, len);
    volatile Burst *b = bursts();
    if (!b) return;
    (void)router;
    for (u32 i = 0; i < BURSTS; i++) {
        volatile Burst *x = &b[i];
        if (!x->used || x->track != track) continue;
        while (x->used && x->k <= x->n) {
            u32 add = (u32)x->div * x->k, t[2];
            t[0] = x->tick_lo + add;
            t[1] = x->tick_hi + (t[0] < add);
            int off = FN(TICK_TO_OFFSET, offset_fn)(clock, t);
            if (off >= start + len) break;                     /* due in a later block */
            if (off < start - LATE_SAMPLES) { x->used = 0; break; }
            if (off < start) off = start;
            volatile u8 *midi = (volatile u8 *)(track + MIDI_OUT_FLAG);
            u8 keep = *midi;
            *midi = 0;
            FN(NOTE_ROUTE, route_fn)(track, x->note, ramp_vel(x->vel, x->ramp, x->k, x->n), (u32)off, x->pad, x->router);
            *midi = keep;
            x->k++;
        }
        if (x->k > x->n) x->used = 0;
    }
}
