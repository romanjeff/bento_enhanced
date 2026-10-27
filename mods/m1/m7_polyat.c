/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1: polyphonic aftertouch + per-track polyphony cap for External tracks (M7).
 *
 * Poly AT (h_poly). Stock already turns pad pressure into per-note poly
 * pressure: the pad router (FUN_08085540, case 0xAE) scales raw pressure with
 * the velocity curve min(raw,0xD000)*127/0xD000, resolves the note through the
 * track's keyboard mapping and posts internal event 0x0E {w2 target, w3 note,
 * w4 value} to the track's queue. The External handler (FUN_08062020) drops it;
 * the hook at 0x08062466 sends it here and we post MIDI-out event 0x5F, which
 * the M4 turns into 0xA0.
 *
 * Polyphony cap (h_note). Every External MIDI note on/off is posted through
 * one of five `bl msg_send(engine+0x7C8, ev)` sites; each is redirected to a
 * stub that adds the track object, so this filter sees {queue, event, track}:
 *   - Note On for a note already sounding on the track: retrigger the same
 *     voice (Note Off, then Note On) and owe one native Note Off;
 *   - Note On past CAP sounding notes: Note Off for the oldest (stolen), owe
 *     its native Note Off;
 *   - Native Note Off for an owed note: swallowed (it would otherwise cut a
 *     newer note on the same number);
 *   - anything else: passed through unchanged.
 * Pressure is only sent for notes currently sounding, so stolen notes go quiet.
 *
 * Output control for pressure: per note, changed values only and at most one
 * send per 10 ms (100 Hz); per DIN port, at most 400 sends/s in bursts of 4.
 *
 * State lives in a region the M4 allocates once at boot (SDRAM bump pool) and
 * publishes in the config line; the M7 then owns it outright (initialises it,
 * the M4 never touches it again), so no cache line is shared. Until the
 * region exists, notes pass through and pressure is not sent.
 */
#include "diag_shared.h"

#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define DCIMVAC (*(volatile u32 *)0xE000EF5Cu)
#define MSG_SEND(q, ev) ((void (*)(u32, const u32 *))(0x08065600u | 1u))((q), (ev))
#define DIAG_PUT(kind, lr, obj, ev, extra) diag_put((kind), (lr), (obj), (ev), (extra))

#define CAP 6u                         /* M1.1 makes this a per-track setting (1..8) */
#define MAXV 8u
#define TRACKS 16u                     /* 8 External tracks with room to spare */
#define PENT 72u                       /* pressure entries: 8 tracks x 8 voices + spare */
#define MS_CYC 480000u                 /* M7 at 480 MHz */
#define MIN_GAP (10u * MS_CYC)         /* 100 Hz per note */
#define TOKEN_CYC (MS_CYC * 5u / 2u)   /* 400/s per port */
#define TOKEN_MAX 4u
#define PORTS 4u

void diag_put(u32 kind, u32 lr, u32 obj, const u32 *ev, u32 extra); /* m7_diag.c */
void diag_stat(u32 idx, u32 value);                                  /* m7_diag.c */
void diag_sp(void);                                                  /* m7_diag.c: stack-depth probe */

typedef struct {
    u32 track;
    u8 n, pad_[3];
    u8 notes[MAXV];      /* sounding notes, oldest first */
    u8 owed[128];        /* native Note Offs to swallow, per note */
} Voices;

typedef struct {
    u32 track, cyc;
    u8 note, last, pend, pval, port, ch, used, pad_;
} PEnt;

typedef struct {
    u32 refill, tokens;
} Bucket;

/* M2-2: a recorded pressure curve playing on an External track. */
typedef struct {
    u32 track, tick_lo, tick_hi;     /* the note's start (playback ticks, 960 per quarter) */
    u32 data;                        /* the curve's samples (pool, SDRAM) */
    u16 n, k;                        /* samples, next to send */
    u8 note, last, used, port, ch, pad_[3];
} Player; /* 28 bytes */
#define PLAYERS 16u
#define CURVE_TICKS 15u              /* 16 samples per step = 240 playback ticks */
#define LATE_SAMPLES 2400            /* 50 ms at 48 kHz: transport stopped / clip changed */

typedef struct {
    u32 magic;
    u32 sent, deferred, budget_drops, stolen, retrig, swallowed, pent_reuse;
    u32 curve_starts, curve_sent, curve_late, curve_skipped;   /* REGION_CURVE_STATS (M4 reads) */
    u32 midi_q;                      /* MIDI-out queue (engine + 0x7C8), seen at note-on */
    Bucket b[PORTS];
    Voices v[TRACKS];
    PEnt p[PENT];
    u32 bursts[RETRIG_BYTES / 4]; /* m7_retrig.c burst table (zeroed with the region) */
    Player pl[PLAYERS];
} Region;
_Static_assert(__builtin_offsetof(Region, curve_starts) == REGION_CURVE_STATS, "curve stats offset");

#define REGION_MAGIC 0x31424D31u /* "1MB1" */

static volatile Region *region(void);
/* m7_retrig.c: the burst table, valid only for this boot's region (0 if none). */
void *m1_retrig_table(void) {
    volatile Region *r = region();
    return r ? (void *)r->bursts : 0;
}
_Static_assert(sizeof(Region) <= M1_REGION_BYTES, "region larger than the M4 allocates");

static volatile Region *region(void) {
    /* The config line is written only by the M4, so invalidating it is safe.
     * Our cached pointer lives in M7-private RAM, which survives a reset: it is
     * trusted only for the M4 boot generation it was taken in (cfg r[0]). */
    volatile u32 *cache = (volatile u32 *)M7_REGION_CACHE; /* [0] pointer, [1] generation */
    DCIMVAC = (u32)CFG;
    __asm volatile("dsb" ::: "memory");
    if (CFG->magic != DIAG_MAGIC || !CFG->region) return 0;
    if (cache[0] == CFG->region && cache[1] == CFG->r[0]) return (volatile Region *)cache[0];
    volatile Region *r = (volatile Region *)CFG->region;
    volatile u8 *b = (volatile u8 *)r;
    for (u32 i = 0; i < sizeof(Region); i++) b[i] = 0;
    for (u32 i = 0; i < PORTS; i++) { r->b[i].refill = CYCCNT; r->b[i].tokens = TOKEN_MAX; }
    r->magic = REGION_MAGIC;
    cache[0] = CFG->region;
    cache[1] = CFG->r[0];
    return r;
}

static void stats(volatile Region *r) {
    diag_stat(0, r->sent);
    diag_stat(1, r->budget_drops);
    diag_stat(2, r->stolen | (r->retrig << 16));
    diag_stat(3, r->swallowed);
}

/* ---- voices ---------------------------------------------------------- */

static volatile Voices *voices(volatile Region *r, u32 track, int create) {
    volatile Voices *free_v = 0;
    for (u32 i = 0; i < TRACKS; i++) {
        if (r->v[i].track == track) return &r->v[i];
        if (!r->v[i].track && !free_v) free_v = &r->v[i];
    }
    if (!create || !free_v) return 0;
    free_v->track = track;
    return free_v;
}

static int find(volatile Voices *v, u32 note) {
    for (u32 i = 0; i < v->n; i++)
        if (v->notes[i] == note) return (int)i;
    return -1;
}

static void drop(volatile Voices *v, u32 i) {
    for (; i + 1 < v->n; i++) v->notes[i] = v->notes[i + 1];
    v->n--;
}

static void note_off(u32 q, const u32 *like, u32 note) {
    u32 ev[6] = {0x5C, like[1], like[2], like[3], note, 0};
    MSG_SEND(q, ev);
}

/* Called from the five redirected post sites: (queue, event, track). */
static void note_body(u32 q, const u32 *ev, u32 track);
static void poly_body(u32 track, const u32 *ev, u32 engine);

/* Voice lists and pressure state are shared by every caller; if any of the
 * five post sites (e.g. stop/all-off) runs in another M7 context, an
 * interleaved update could corrupt a list. Mask interrupts for the (short)
 * body; msg_send and diag_put are safe with interrupts masked. */
static inline u32 irq_save(void) {
    u32 p;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory");
    return p;
}
static inline void irq_restore(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }

__attribute__((used, retain)) void h_note(u32 q, const u32 *ev, u32 track) {
    diag_sp();
    u32 p = irq_save();
    note_body(q, ev, track);
    irq_restore(p);
}

__attribute__((used, retain)) void h_poly(u32 track, const u32 *ev, u32 engine) {
    diag_sp();
    u32 p = irq_save();
    poly_body(track, ev, engine);
    irq_restore(p);
}

static void note_body(u32 q, const u32 *ev, u32 track) {
    volatile Region *r = region();
    u32 type = ev[0] & 0xFF, note = ev[4] & 0x7F;
    if (!r || (type != 0x5B && type != 0x5C)) { MSG_SEND(q, ev); return; }
    volatile Voices *v = voices(r, track, type == 0x5B);
    if (!v) { MSG_SEND(q, ev); return; }
    if (type == 0x5B) {
        r->midi_q = q;                              /* M2-2: where curves go out */
        int i = find(v, note);
        if (i >= 0) {                              /* same note: retrigger the same voice */
            note_off(q, ev, note);
            v->owed[note]++;
            drop(v, (u32)i);
            r->retrig++;
            DIAG_PUT(K_VOICE, 1, track, ev, note);
        }
        while (v->n >= CAP) {                      /* steal the oldest */
            u32 old = v->notes[0];
            note_off(q, ev, old);
            v->owed[old]++;
            drop(v, 0);
            r->stolen++;
            DIAG_PUT(K_VOICE, 2, track, ev, old);
        }
        v->notes[v->n++] = (u8)note;
        MSG_SEND(q, ev);
    } else {
        if (v->owed[note]) {                       /* its voice was already released */
            v->owed[note]--;
            r->swallowed++;
            DIAG_PUT(K_VOICE, 3, track, ev, note);
            stats(r);
            return;
        }
        int i = find(v, note);
        if (i >= 0) drop(v, (u32)i);
        MSG_SEND(q, ev);
    }
    stats(r);
}

/* ---- pressure -------------------------------------------------------- */

static int take_token(volatile Region *r, u32 port, u32 now) {
    volatile Bucket *b = &r->b[port % PORTS];
    u32 dt = now - b->refill;
    if (dt >= TOKEN_CYC) {
        u32 add = dt / TOKEN_CYC, t = b->tokens + add;
        b->tokens = t > TOKEN_MAX ? TOKEN_MAX : t;
        b->refill += add * TOKEN_CYC;
        if (now - b->refill >= TOKEN_CYC) b->refill = now;
    }
    if (!b->tokens) { r->budget_drops++; return 0; }
    b->tokens--;
    return 1;
}

static void post(volatile Region *r, u32 engine, volatile PEnt *e, u32 value, u32 now) {
    u32 ev[6] = {0x5F, 0, e->port, e->ch, e->note, value};
    MSG_SEND(engine + 0x7C8u, ev);
    e->last = (u8)value;
    e->cyc = now;
    e->pend = 0;
    r->sent++;
}

static volatile PEnt *pent(volatile Region *r, u32 track, u32 note, u32 now) {
    volatile PEnt *free_e = 0, *oldest = &r->p[0];
    for (u32 i = 0; i < PENT; i++) {
        volatile PEnt *e = &r->p[i];
        if (e->used && e->track == track && e->note == note) return e;
        if (!e->used && !free_e) free_e = e;
        if (e->used && (now - e->cyc) > (now - oldest->cyc)) oldest = e;
    }
    volatile PEnt *e = free_e ? free_e : oldest;
    if (!free_e) r->pent_reuse++;
    e->used = 1;
    e->track = track;
    e->note = (u8)note;
    e->last = 0xFF; /* force the first value out */
    e->pend = 0;
    e->cyc = now - MIN_GAP;
    return e;
}

static void flush(volatile Region *r, u32 engine, u32 now) {
    for (u32 i = 0; i < PENT; i++) {
        volatile PEnt *e = &r->p[i];
        if (e->used && e->pend && now - e->cyc >= MIN_GAP) {
            if (!take_token(r, e->port, now)) continue;
            post(r, engine, e, e->pval, now);
        }
    }
}

/* Called from the 0x08062466 trampoline: (track r6, event at sp+0x20, engine r8). */
static void poly_body(u32 track, const u32 *ev, u32 engine) {
    if ((ev[0] & 0xFF) != 0x0E) return;
    volatile Region *r = region();
    if (!r) return;
    if (!*(volatile u8 *)(track + 0x86D1u)) return; /* MIDI out off: stock's own check */
    u32 now = CYCCNT, note = ev[3] & 0x7F, value = ev[4] & 0x7F;
    volatile Voices *v = voices(r, track, 0);
    if (v && find(v, note) >= 0) {                  /* only notes that are sounding */
        volatile PEnt *e = pent(r, track, note, now);
        e->port = *(volatile u8 *)(track + 0x31u);
        e->ch = *(volatile u8 *)(track + 0x32u);
        if (value == e->last) {
            e->pend = 0;
        } else if (now - e->cyc >= MIN_GAP && take_token(r, e->port, now)) {
            post(r, engine, e, value, now);
        } else {
            e->pend = 1;
            e->pval = (u8)value;
            r->deferred++;
        }
    }
    flush(r, engine, now);
    stats(r);
}


/* ---- M2-2: play recorded pressure curves on External tracks ----------------
 * curve_start (from retrig_accept, after stock posted a sequenced note) starts
 * a player when the note word carries a curve handle (tag 0xB7); curve_emit
 * (from retrig_emit, once per track per audio block) sends each due sample as
 * poly aftertouch: sample k is due at the note's start + k * 15 playback ticks,
 * converted with stock's tick -> sample offset (tempo-following). Only changed
 * values go out, within the per-port token budget shared with live pressure.
 * The pool (M4, SDRAM) is reached through CURVES, an M4-written D2 line. */
#define FN7(a, t) ((t)((a) | 1u))
#define TICK_TO_OFFSET 0x080A2200u
typedef int (*offset_fn)(u32 clock, const u32 *tick64);

static u32 curve_data(u32 handle, u32 *n) {
    DCIMVAC = (u32)CURVES & ~31u;                    /* M4-written line: never dirty here */
    __asm volatile("dsb" ::: "memory");
    volatile CurveState *c = CURVES;
    if (c->magic != CURVES_MAGIC || !c->pool || !c->table || !handle || handle >= c->next || handle >= CURVE_HANDLES)
        return 0;
    u32 off = ((volatile u32 *)c->table)[handle];
    if (off + 2u > CURVE_POOL_BYTES) return 0;
    volatile u8 *e = (volatile u8 *)(c->pool + off);
    *n = e[0] | ((u32)e[1] << 8);
    return *n && off + 2u + *n <= CURVE_POOL_BYTES ? c->pool + off + 2u : 0;
}

static void curve_start_body(u32 track, u32 note, u32 word, u32 tick_lo, u32 tick_hi) {
    volatile Region *r = region();
    u32 h = step_handle(word), n = 0, data;
    if (!r || !h || !*(volatile u8 *)(track + 0x86D1u)) return;   /* MIDI out off: nothing to drive */
    if (!(data = curve_data(h, &n))) { r->curve_skipped++; return; }
    volatile Player *slot = 0;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (p->used && p->track == track && p->note == (note & 0x7Fu)) { slot = p; break; }   /* retrigger */
        if (!p->used && !slot) slot = p;
    }
    if (!slot) slot = &r->pl[0];                     /* all busy: reuse the first */
    slot->track = track; slot->tick_lo = tick_lo; slot->tick_hi = tick_hi;
    slot->data = data; slot->n = (u16)n; slot->k = 0;
    slot->note = (u8)(note & 0x7Fu); slot->last = 0xFF;
    slot->port = *(volatile u8 *)(track + 0x31u); slot->ch = *(volatile u8 *)(track + 0x32u);
    slot->used = 1;
    r->curve_starts++;
}

static void curve_emit_body(u32 track, u32 clock, int start, int len) {
    volatile Region *r = region();
    if (!r || !r->midi_q) return;
    u32 now = CYCCNT;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (!p->used || p->track != track) continue;
        while (p->k < p->n) {
            u32 add = CURVE_TICKS * p->k, t[2];
            t[0] = p->tick_lo + add;
            t[1] = p->tick_hi + (t[0] < add);
            int off = FN7(TICK_TO_OFFSET, offset_fn)(clock, t);
            if (off >= start + len) break;                     /* due in a later block */
            if (off < start - LATE_SAMPLES) { p->used = 0; r->curve_late++; break; }
            u32 v = ((volatile u8 *)p->data)[p->k] & 0x7Fu;
            if (v != p->last && take_token(r, p->port, now)) {
                u32 ev[6] = {0x5F, 0, p->port, p->ch, p->note, v};
                MSG_SEND(r->midi_q, ev);
                p->last = (u8)v;
                r->curve_sent++;
            }
            p->k++;
        }
        if (p->k >= p->n) p->used = 0;
    }
}

void curve_start(u32 track, u32 note, u32 word, u32 tick_lo, u32 tick_hi) {
    u32 p = irq_save();
    curve_start_body(track, note, word, tick_lo, tick_hi);
    irq_restore(p);
}
void curve_emit(u32 track, u32 clock, int start, int len) {
    u32 p = irq_save();
    curve_emit_body(track, clock, start, len);
    irq_restore(p);
}
