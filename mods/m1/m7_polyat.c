/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1: polyphonic aftertouch out of External tracks (M7 side).
 *
 * Stock already turns pad pressure into per-note poly pressure: the pad
 * router (FUN_08085540, case 0xAE) scales raw pressure with the same
 * min(raw,0xD000)*127/0xD000 curve it uses for velocity, resolves the pad's
 * note through the track's keyboard mapping, and posts internal event 0x0E
 * {w2 target, w3 note, w4 value} to the track's own queue. The External
 * track's handler (FUN_08062020) has no case for 0x0E and drops it.
 *
 * The hook at 0x08062466 (the handler's "neither note-on nor note-off"
 * branch) calls h_poly(track, event, engine) for 0x0E. We post a MIDI-out
 * event of unused type 0x5F to the same queue stock's note-on producer
 * (FUN_0805FF00) uses, with the same layout:
 *     {0x5F, 0, port = track+0x31, channel = track+0x32, note, value}
 * The M4 side turns 0x5F into 0xA0 (Polyphonic Key Pressure).
 *
 * Output control (DIN carries ~1000 messages/s, and the M7->M4 MIDI ring has
 * only 16 unchecked slots):
 *   - per (track, note): changed values only, at most one send per 10 ms
 *     (100 Hz); a value held back is sent later by a flush pass;
 *   - global budget: 400 sends/s, bursts of up to 4.
 * Integer-only; called on the M7 engine thread, in the same context stock
 * uses to post note-ons.
 */
#include "diag_shared.h"

#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define MSG_SEND(q, ev) ((void (*)(u32, const u32 *))(0x08065600u | 1u))((q), (ev))

#define MS_CYC 480000u                /* M7 at 480 MHz */
#define MIN_GAP (10u * MS_CYC)        /* 100 Hz per note */
#define TOKEN_CYC (MS_CYC * 5u / 2u)  /* one send per 2.5 ms = 400/s */
#define TOKEN_MAX 4u

typedef struct {
    u32 track;      /* track object (0 = free) */
    u32 cyc;        /* time of last send */
    u8 note, last, pend, pval;
    u8 port, ch, used, pad_;
} Ent; /* 16 bytes */

typedef struct {
    u32 magic;
    u32 refill_cyc;
    u32 tokens;
    u32 sent, deferred, budget_drops, stolen;
    u32 r_;
    Ent e[12]; /* 32 B header + 12 x 16 B = 224 B = POLY_BYTES; covers the 8-note poly cap */
} Poly;

#define P ((volatile Poly *)POLY_BASE)
#define POLY_MAGIC 0x54415031u /* "1PAT" */
#define NENT 12u
_Static_assert(sizeof(Poly) <= POLY_BYTES, "poly state overruns its slice");

static void init(void) {
    volatile u8 *b = (volatile u8 *)P;
    for (u32 i = 0; i < sizeof(Poly); i++) b[i] = 0;
    P->refill_cyc = CYCCNT;
    P->tokens = TOKEN_MAX;
    P->magic = POLY_MAGIC;
}

static int take_token(u32 now) {
    u32 dt = now - P->refill_cyc;
    if (dt >= TOKEN_CYC) {
        u32 add = dt / TOKEN_CYC;
        u32 t = P->tokens + add;
        P->tokens = t > TOKEN_MAX ? TOKEN_MAX : t;
        P->refill_cyc += add * TOKEN_CYC;
        if (now - P->refill_cyc >= TOKEN_CYC) P->refill_cyc = now; /* long idle */
    }
    if (!P->tokens) { P->budget_drops++; return 0; }
    P->tokens--;
    return 1;
}

static void post(u32 engine, volatile Ent *e, u32 value, u32 now) {
    u32 ev[6] = {0x5F, 0, e->port, e->ch, e->note, value};
    MSG_SEND(engine + 0x7C8u, ev);
    e->last = (u8)value;
    e->cyc = now;
    e->pend = 0;
    P->sent++;
}

static volatile Ent *lookup(u32 track, u32 note, u32 now) {
    volatile Ent *free_e = 0, *oldest = &P->e[0];
    for (u32 i = 0; i < NENT; i++) {
        volatile Ent *e = &P->e[i];
        if (e->used && e->track == track && e->note == note) return e;
        if (!e->used && !free_e) free_e = e;
        if (e->used && (now - e->cyc) > (now - oldest->cyc)) oldest = e;
    }
    volatile Ent *e = free_e ? free_e : oldest;
    if (!free_e) P->stolen++;
    e->used = 1;
    e->track = track;
    e->note = (u8)note;
    e->last = 0xFF; /* force the first value out */
    e->pend = 0;
    e->cyc = now - MIN_GAP;
    return e;
}

static void flush(u32 engine, u32 now) {
    for (u32 i = 0; i < NENT; i++) {
        volatile Ent *e = &P->e[i];
        if (e->used && e->pend && now - e->cyc >= MIN_GAP) {
            if (!take_token(now)) return;
            post(engine, e, e->pval, now);
        }
    }
}

/* track: the External track object (r6), ev: the 24-byte event (sp+0x20),
 * engine: the context stock passes to its own note-on producer (r8). */
__attribute__((used, retain)) void h_poly(u32 track, const u32 *ev, u32 engine) {
    if (P->magic != POLY_MAGIC) init();
    u32 now = CYCCNT;
    if ((ev[0] & 0xFF) != 0x0E) return;
    if (!*(volatile u8 *)(track + 0x86D1u)) return; /* MIDI out off: stock's own check */
    u32 note = ev[3] & 0x7F, value = ev[4] & 0x7F;
    volatile Ent *e = lookup(track, note, now);
    e->port = *(volatile u8 *)(track + 0x31u);
    e->ch = *(volatile u8 *)(track + 0x32u);
    if (value == e->last) {
        e->pend = 0;
    } else if (now - e->cyc >= MIN_GAP && take_token(now)) {
        post(engine, e, value, now);
    } else {
        e->pend = 1;
        e->pval = (u8)value;
        P->deferred++;
    }
    flush(engine, now);
}
