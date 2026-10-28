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
    u8 note, last, pend, pval, port, ch, used, chan;   /* chan: AT Out Channel, pval = current */
} PEnt;

typedef struct {
    u32 refill, tokens;
} Bucket;

/* M2-2: a recorded pressure curve playing on an External track. */
typedef struct {
    u32 track, tick_lo, tick_hi;     /* the note's start (playback ticks, 960 per quarter) */
    u32 data;                        /* the curve's samples (pool, SDRAM) */
    u32 cyc;                         /* CYCCNT of the last send */
    u16 n, k;                        /* samples, next due */
    u8 note, last, want, used, port, ch, chan, kind;   /* chan: AT Out Channel (aggregated);
                                                          kind 1: internal instrument, port|ch<<8 = bus key */
    u32 sp16;                        /* ticks between samples x 16: the note's duration / samples (v3.43) */
} Player; /* 36 bytes */

/* M2-5b: one channel-pressure destination (port/channel) for AT Out Channel:
 * sends the highest current value of its sounding curve notes. */
typedef struct {
    u32 cyc;                         /* CYCCNT of the last send */
    u8 port, ch, last, used;         /* last: 0xFF = nothing sent yet */
} Chan;
#define CHANS 8u                     /* one per track */
#define PLAYERS 64u                  /* 8 tracks x 8 voices: no curve is ever cut off by another */
#define CURVE_MIN_GAP (8u * MS_CYC)  /* per note, when alone on its port */
#define CURVE_SHARE (MS_CYC * 5u / 2u)   /* + one token interval per curve note on the port */
#define CURVE_TICKS 15u              /* 16 samples per step at 1/16 (240 ticks per step): fallback */
/* Ticks per step for the sequence's step-size index: stock's float table
 * 0x080BDBEC (quarter notes per step: 32, 16, 8, 4, 2, 4/3, 1, 2/3, 1/2, 1/3,
 * 1/4, 1/6, 1/8, 1/12, 1/16) x 960, as the timed-note processor (0x08060820)
 * scales a note's length: duration = ticks per step x length / 960. */
static const u16 STEP_TICKS[15] = {30720, 15360, 7680, 3840, 1920, 1280, 960, 640, 480, 320, 240, 160, 120, 80, 60};
#define LATE_SAMPLES 2400            /* 50 ms at 48 kHz: transport stopped / clip changed */

typedef struct {
    u32 magic;
    u32 sent, deferred, budget_drops, stolen, retrig, swallowed, pent_reuse;
    u32 curve_starts, curve_sent, curve_late, curve_skipped;   /* REGION_CURVE_STATS (M4 reads) */
    u32 midi_q;                      /* MIDI-out queue (engine + 0x7C8), seen at note-on */
    u32 curve_peak, curve_thinned;   /* most players at once; values superseded before sending */
    u32 curve_dest;                  /* last start: port | ch << 8 | mode << 16 (mapping check) */
    u32 int_dest;                    /* last internal start: key | idx << 16 | mode << 24 (0x86D4 check) */
    u32 live_int;                    /* last live internal: router idx | key << 8 | mode << 24 */
    u32 int_sent;                    /* 0x0E posts to internal instruments */
    u32 bus;                         /* engine (note_route's router): internal posts go through it */
    Bucket b[PORTS];
    Voices v[TRACKS];
    PEnt p[PENT];
    u32 bursts[RETRIG_BYTES / 4]; /* m7_retrig.c burst table (zeroed with the region) */
    Player pl[PLAYERS];
    Chan cd[CHANS];
    u32 chan_sent, live_off;
    Chan ic[CHANS];                  /* M2-2b: internal channel aggregates (port|ch<<8 = key) */
    u32 mod[MOD_RUN_BYTES / 4u];     /* F1: m7_mod.c runtime (zeroed with the region) */
} Region;
_Static_assert(__builtin_offsetof(Region, curve_starts) == REGION_CURVE_STATS, "curve stats offset");
_Static_assert(__builtin_offsetof(Region, curve_peak) == REGION_CURVE_PEAK, "curve peak offset");
_Static_assert(__builtin_offsetof(Region, int_dest) == REGION_INTERNAL, "internal stats offset");

#define REGION_MAGIC 0x35424D31u /* "1MB5": Player 36 B (v3.43) */

static volatile Region *region(void);
/* m7_retrig.c (R4): is this note sounding on the track (voice list)? */
static volatile Voices *voices(volatile Region *r, u32 track, int create);
static int find(volatile Voices *v, u32 note);
static inline u32 irq_save(void);
static inline void irq_restore(u32 p);
int m1_note_sounding(u32 track, u32 note) {
    volatile Region *r = region();
    u32 p = irq_save();
    volatile Voices *v = r ? voices(r, track, 0) : 0;
    int s = v && find(v, note & 0x7Fu) >= 0;
    irq_restore(p);
    return s;
}

/* m7_retrig.c: the burst table, valid only for this boot's region (0 if none). */
void *m1_retrig_table(void) {
    volatile Region *r = region();
    return r ? (void *)r->bursts : 0;
}

/* m7_mod.c (F1): its runtime slice, the port budget, and what it modulates from. */
static int take_token(volatile Region *r, u32 port, u32 now);
void *m1_mod_run(void) {
    volatile Region *r = region();
    return r ? (void *)r->mod : 0;
}
int m1_token(u32 port, u32 now) {
    volatile Region *r = region();
    return r && take_token(r, port, now);
}
int m1_notes_held(u32 track) {
    volatile Region *r = region();
    volatile Voices *v = r ? voices(r, track, 0) : 0;
    return v ? v->n : 0;
}
u32 m1_track_pressure(u32 track) {                   /* highest live or recorded pressure now */
    volatile Region *r = region();
    u32 best = 0;
    if (!r) return 0;
    volatile Voices *v = voices(r, track, 0);
    for (u32 i = 0; i < PENT; i++) {
        volatile PEnt *e = &r->p[i];
        if (!e->used || e->track != track || !v || find(v, e->note) < 0) continue;
        u32 val = e->chan ? e->pval : e->last;
        if (val <= 0x7Fu && val > best) best = val;
    }
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (p->used && p->track == track && !p->kind && p->want <= 0x7Fu && p->want > best) best = p->want;
    }
    return best;
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
void mod_note(u32 track, u32 note, u32 vel, int on);   /* m7_mod.c (F1) */
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

static void chan_pass(volatile Region *r, u32 now, u32 *on_port);
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
        mod_note(track, note, ev[5], 1);           /* F1: retrigger LFOs/envelopes */
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
        mod_note(track, note, 0, 0);               /* F1: envelope gate */
        if (r->midi_q) { u32 on_port[PORTS] = {0}; chan_pass(r, CYCCNT, on_port); }   /* AT Channel: drop it now */
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

static u32 at_mode(u32 port, u32 ch, u32 *how);

/* Called from the 0x08062466 trampoline: (track r6, event at sp+0x20, engine r8).
 * AT Out (M2-5c): Off drops live pressure (capture is upstream, AT Rec still
 * records it); Channel feeds the destination's aggregate, shared with recorded
 * curves on it; Poly as always. */
static void poly_body(u32 track, const u32 *ev, u32 engine) {
    if ((ev[0] & 0xFF) != 0x0E) return;
    volatile Region *r = region();
    if (!r) return;
    if (!*(volatile u8 *)(track + 0x86D1u)) return; /* MIDI out off: stock's own check */
    u32 now = CYCCNT, note = ev[3] & 0x7F, value = ev[4] & 0x7F, how;
    u32 port = *(volatile u8 *)(track + 0x31u), ch = *(volatile u8 *)(track + 0x32u);
    u32 mode = at_mode(port, ch, &how);
    volatile Voices *v = voices(r, track, 0);
    if (mode == AT_OFF) {
        r->live_off++;
    } else if (v && find(v, note) >= 0) {           /* only notes that are sounding */
        volatile PEnt *e = pent(r, track, note, now);
        e->port = (u8)port;
        e->ch = (u8)ch;
        e->chan = mode == AT_CHAN;
        if (e->chan) {
            e->pval = (u8)value; e->pend = 0;
            if (r->midi_q) { u32 on_port[PORTS] = {0}; chan_pass(r, now, on_port); }
        } else if (value == e->last) {
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

/* AT Out for a port/channel (AT_POLY when unknown). The M4's channel parameter
 * is 1-based (1 = MIDI ch 1), the M7's byte 0-based; M4 value 0 (non-External
 * tracks) never matches. Device log v3.33: track 1 = M4 0/1/Off, M7 port 0
 * byte 0; tracks 2-4 = M4 0/0/Poly. *how = 1 + matched track, 0 = none. */
static u32 at_mode(u32 port, u32 ch, u32 *how) {
    DCIMVAC = (u32)AT_DEST & ~31u; DCIMVAC = ((u32)AT_DEST + 32u) & ~31u;   /* M4-written lines */
    __asm volatile("dsb" ::: "memory");
    *how = 0;
    if (AT_DEST->magic == AT_DEST_MAGIC)
        for (u32 i = 0; i < 8; i++)
            if (AT_DEST->d[i].valid && AT_DEST->d[i].port == port && AT_DEST->d[i].ch == ch + 1u) {
                *how = 1u + i;
                return AT_DEST->d[i].mode;
            }
    return AT_POLY;
}

/* The curve spans the note as it plays NOW: sample spacing = the note's current
 * duration / its samples, with stock's duration = ticks per step x length / 960
 * (the timed-note processor). So a new step size or a longer/shorter note
 * stretches or squeezes the curve (v3.42 and earlier: 15 ticks, right only for
 * an unedited note at 1/16). Sequence object track+0x86C0: word 0 = step-size
 * index, byte +0x38 = lengths in steps (else raw ticks). Without a usable
 * length: 16 samples per step (the recorded spacing, 60 length units each). */
static u32 sample_ticks16(u32 track, u32 len, u32 n) {
    u32 seq = *(volatile u32 *)(track + 0x86C0u), tps = 240u, steps = 1u;
    if (seq && !(seq & 3u) && ((seq >= 0x24000000u && seq < 0x24080000u) || (seq >= 0xC0000000u && seq < 0xC8000000u))) {
        steps = *(volatile u8 *)(seq + 0x38u) != 0;
        u32 idx = *(volatile u32 *)seq;
        if (idx < 15u) tps = STEP_TICKS[idx];
    }
    if (len && n && len < 0x10000u) {                          /* < 68 steps: tps x len fits */
        u32 dur = steps ? tps * len / 960u : len;              /* ticks */
        u32 sp = dur * 16u / n;
        return sp ? sp : 1u;
    }
    return steps ? tps : 60u * 16u;                            /* (ticks per step / 16) x 16 */
}

static void start_player(volatile Region *r, u32 track, u32 note, u32 data, u32 n, u32 tick_lo, u32 tick_hi,
                         u32 kind, u32 port, u32 ch, u32 chan, u32 len) {
    volatile Player *slot = 0;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (p->used && p->track == track && p->kind == kind && p->note == (note & 0x7Fu)) { slot = p; break; }   /* retrigger */
        if (!p->used && !slot) slot = p;
    }
    if (!slot) { r->curve_skipped++; return; }      /* 64 busy: never steal another note's curve */
    slot->track = track; slot->tick_lo = tick_lo; slot->tick_hi = tick_hi;
    slot->data = data; slot->n = (u16)n; slot->k = 0;
    slot->note = (u8)(note & 0x7Fu); slot->last = 0xFF; slot->want = 0xFF;
    slot->cyc = CYCCNT - CURVE_MIN_GAP * 8u;        /* the first value may go at once */
    slot->port = (u8)port; slot->ch = (u8)ch;
    slot->chan = (u8)chan; slot->kind = (u8)kind;
    slot->sp16 = sample_ticks16(track, len, n);
    slot->used = 1;
    r->curve_starts++;
}

/* AT Out for an internal track, by track index (UI order). The index is
 * note_route's track+0x86D4 (sent to the UI with each note); unconfirmed on
 * the device: the save log prints it (int_dest) next to the M4's table.
 * Only entries the M4 marks non-External (channel parameter 0) apply. */
static u32 int_mode(u32 idx) {
    DCIMVAC = (u32)AT_DEST & ~31u; DCIMVAC = ((u32)AT_DEST + 32u) & ~31u;
    __asm volatile("dsb" ::: "memory");
    if (idx < 8u && AT_DEST->magic == AT_DEST_MAGIC && AT_DEST->d[idx].valid && !AT_DEST->d[idx].ch)
        return AT_DEST->d[idx].mode;
    return AT_POLY;
}

/* The instrument's bus key for a note, exactly as note_route picks it for its
 * 0x12 note event: the per-pad table track+0x86DC[pad] unless 0xFFFFFF00,
 * else the track's u16 at +0x10. v3.37-3.39 used +0x10 alone: 0 on a granular
 * track whose key (table, as the pad router's) was 0xF4 - every post was lost. */
static u32 inst_key(u32 track, u32 pad) {
    u32 key = *(volatile u16 *)(track + 0x10u);
    if (pad < 16u) {
        u32 k = *(volatile u32 *)(track + (pad + 0x21B6u) * 4u + 4u);
        if (k != 0xFFFFFF00u) key = k;
    }
    return key & 0xFFFFu;
}

static void curve_start_body(u32 track, u32 note, u32 word, u32 tick_lo, u32 tick_hi, u32 bus, u32 pad, u32 len) {
    volatile Region *r = region();
    u32 h = step_handle(word), n = 0, data;
    int ext = *(volatile u8 *)(track + 0x86D1u), inst = *(volatile u8 *)(track + 0x86D2u);
    if (!r || !h || (!ext && !inst)) return;        /* no MIDI out, no instrument: nothing to drive */
    if (!(data = curve_data(h, &n))) { r->curve_skipped++; return; }
    if (ext) {
        u32 port = *(volatile u8 *)(track + 0x31u), ch = *(volatile u8 *)(track + 0x32u), how;
        u32 mode = at_mode(port, ch, &how);
        r->curve_dest = port | (ch << 8) | (mode << 16) | (how << 24);
        if (mode == AT_POLY || mode == AT_CHAN) start_player(r, track, note, data, n, tick_lo, tick_hi, 0, port, ch, mode == AT_CHAN, len);
        else r->curve_skipped++;                    /* Off */
    }
#ifdef BE_NO_INTA
    inst = 0;                                       /* group inta off: External only */
#endif
    if (inst && bus) {                              /* M2-2b: the track's own instrument */
        u32 key = inst_key(track, pad), idx = *(volatile u32 *)(track + 0x86D4u);
        u32 mode = int_mode(idx);
        r->bus = bus;
        r->int_dest = (key & 0xFFFFu) | ((idx & 0xFFu) << 16) | (mode << 24);
        if (mode == AT_POLY || mode == AT_CHAN) start_player(r, track, note, data, n, tick_lo, tick_hi, 1, key & 0xFF, (key >> 8) & 0xFF, mode == AT_CHAN, len);
        else r->curve_skipped++;
    }
}

/* Per track per audio block: catch this track's players up to the samples now
 * due (keeping only the latest value). Then one send pass over ALL players:
 * while the port has tokens, send the eligible note that has waited longest
 * (a finished note's final value first). Eligible = value changed and its
 * share of the port has elapsed: max(8 ms, notes on the port x 2.5 ms). Total
 * demand then stays within the port budget and oldest-first means no note
 * starves, whatever order the tracks are processed in. */
#define CURVE_FINAL_WAIT (50u * MS_CYC)  /* a finished note's last value: give up after this */

/* M2-5b AT Out Channel: per destination, the highest current value of its
 * sounding notes (a note leaves the set once its curve has ended); 0 once
 * none sound, so nothing is left pressed. Changed values only, at most one
 * send per 8 ms, from the port's budget. Returns active destinations per port
 * (each costs the port like one poly note). */
static u32 chan_dest(volatile Region *r, u32 port, u32 ch, u32 now) {
    u32 freed = CHANS;
    for (u32 j = 0; j < CHANS; j++) {
        if (r->cd[j].used && r->cd[j].port == port && r->cd[j].ch == ch) return j;
        if (!r->cd[j].used && freed == CHANS) freed = j;
    }
    if (freed < CHANS) {
        r->cd[freed].port = (u8)port; r->cd[freed].ch = (u8)ch; r->cd[freed].last = 0xFF;
        r->cd[freed].cyc = now - CURVE_MIN_GAP; r->cd[freed].used = 1;
    }
    return freed;                                       /* CHANS: 8 busy (cannot: 8 tracks) */
}

static void chan_pass(volatile Region *r, u32 now, u32 *on_port) {
    u8 val[CHANS] = {0}, has[CHANS] = {0}, live[CHANS] = {0};
    for (u32 i = 0; i < PENT; i++) {                    /* live notes: count while sounding */
        volatile PEnt *e = &r->p[i];
        if (!e->used || !e->chan || e->track < 0x20000u) continue;   /* internal (key tag): int_chan */
        volatile Voices *v = voices(r, e->track, 0);
        if (!v || find(v, e->note) < 0) { e->chan = 0; e->used = 0; continue; }   /* released */
        u32 d = chan_dest(r, e->port, e->ch, now);
        if (d == CHANS) continue;
        live[d] = 1; has[d] = 1;
        if (e->pval > val[d]) val[d] = e->pval;
    }
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (!p->used || !p->chan || p->kind) continue;
        u32 d = CHANS, freed = CHANS;
        for (u32 j = 0; j < CHANS; j++) {
            if (r->cd[j].used && r->cd[j].port == p->port && r->cd[j].ch == p->ch) { d = j; break; }
            if (!r->cd[j].used && freed == CHANS) freed = j;
        }
        if (d == CHANS && (d = freed) < CHANS) {
            r->cd[d].port = p->port; r->cd[d].ch = p->ch; r->cd[d].last = 0xFF;
            r->cd[d].cyc = now - CURVE_MIN_GAP; r->cd[d].used = 1;
        }
        if (d < CHANS) {
            live[d] = 1;
            if (p->want <= 0x7Fu) { has[d] = 1; if (p->want > val[d]) val[d] = p->want; }
        }
        if (p->k >= p->n) p->used = 0;                    /* ended: counted once more, then leaves */
    }
    for (u32 j = 0; j < CHANS; j++) {
        volatile Chan *c = &r->cd[j];
        if (!c->used) continue;
        if (live[j] && !has[j]) continue;                 /* notes sounding, no value due yet */
        u32 want = live[j] ? val[j] : 0u;
        if (want == c->last || (!live[j] && c->last == 0xFF)) {
            if (!live[j]) c->used = 0;                    /* settled at 0 (or never sent): done */
            else on_port[c->port % PORTS]++;
            continue;
        }
        on_port[c->port % PORTS]++;
        if (now - c->cyc < CURVE_MIN_GAP || !take_token(r, c->port, now)) continue;
        u32 ev[6] = {0x60, 0, c->port, c->ch, want, 0};
        MSG_SEND(r->midi_q, ev);
        c->last = (u8)want; c->cyc = now;
        r->chan_sent++;
    }
}

static void curve_send_pass(volatile Region *r, u32 now) {
    u32 on_port[PORTS] = {0}, active = 0;
    chan_pass(r, now, on_port);
    for (u32 i = 0; i < PLAYERS; i++)
        if (r->pl[i].used) { if (!r->pl[i].chan && !r->pl[i].kind) on_port[r->pl[i].port % PORTS]++; active++; }
    if (active > r->curve_peak) r->curve_peak = active;
    for (u32 round = 0; round < PLAYERS; round++) {
        volatile Player *best = 0;
        u32 best_age = 0;
        for (u32 i = 0; i < PLAYERS; i++) {
            volatile Player *p = &r->pl[i];
            if (!p->used || p->chan || p->kind) continue; /* channel: chan_pass; internal: int_pass */
            int done = p->k >= p->n;
            if (p->want == p->last || p->want > 0x7Fu) {      /* nothing to send */
                if (done) p->used = 0;
                continue;
            }
            u32 age = now - p->cyc;
            if (done && age >= CURVE_FINAL_WAIT + CURVE_MIN_GAP) { p->used = 0; continue; }
            u32 gap = on_port[p->port % PORTS] * CURVE_SHARE;
            if (gap < CURVE_MIN_GAP) gap = CURVE_MIN_GAP;
            if (!done && age < gap) continue;
            if (done) age |= 0x80000000u;                     /* final values first */
            if (!best || age > best_age) { best = p; best_age = age; }
        }
        if (!best || !take_token(r, best->port, now)) return;
        u32 ev[6] = {0x5F, 0, best->port, best->ch, best->note, best->want};
        MSG_SEND(r->midi_q, ev);
        best->last = best->want; best->cyc = now;
        r->curve_sent++;
        if (best->k >= best->n) best->used = 0;
    }
}

static void int_pass(volatile Region *r, u32 now);
static void curve_emit_body(u32 track, u32 clock, int start, int len) {
    volatile Region *r = region();
    if (!r || (!r->midi_q && !r->bus)) return;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (!p->used || p->track != track) continue;
        while (p->k < p->n) {
            u32 add = (p->sp16 * p->k) >> 4, t[2];
            t[0] = p->tick_lo + add;
            t[1] = p->tick_hi + (t[0] < add);
            int off = FN7(TICK_TO_OFFSET, offset_fn)(clock, t);
            if (off >= start + len) break;                     /* due in a later block */
            if (off < start - LATE_SAMPLES) { p->used = 0; r->curve_late++; break; }
            u8 v = ((volatile u8 *)p->data)[p->k] & 0x7Fu;
            if (p->want != p->last && v != p->want) r->curve_thinned++;   /* unsent value superseded */
            p->want = v;
            p->k++;
        }
    }
    if (r->midi_q) curve_send_pass(r, CYCCNT);
    if (r->bus) int_pass(r, CYCCNT);
}

/* ---- M2-2b: internal instruments ---------------------------------------------
 * Stock's voice engine (FUN_080945E0, case 0x0E) sets each sounding voice of
 * the event's note to value/127 (voice +0xC8): the pad router's live pressure.
 * We post the same event {0x0E, 0, key, note, value} through the engine's bus
 * (FUN_0809ECA0, key -> the instrument's queue). Poly: each note its own
 * curve. Channel: one value per track, the highest of its sounding notes
 * (curves and live pads), posted to every one of them. No MIDI budget: the
 * queue (64 entries per track per block) is drained every block; at most one
 * post per note per INT_GAP. */
#define BUS_POST 0x0809ECA0u
#define INT_GAP (4u * MS_CYC)
#define KEY_TAG 0x10000u                 /* PEnt.track for a live internal note: key | tag */
#define LIVE_STALE (300u * MS_CYC)       /* no update for this long: the pad was let go */

static void int_post(volatile Region *r, u32 key, u32 note, u32 value) {
    u32 ev[6] = {0x0E, 0, key, note & 0x7Fu, value & 0x7Fu, 0};
    FN7(BUS_POST, void (*)(u32, const u32 *))(r->bus, ev);
    r->int_sent++;
}

/* Channel for one key: highest current value over its channel curve notes and
 * live notes still pressed (value > 0); posted to each of them on a change. */
static void int_chan(volatile Region *r, u32 key, u32 now, int force) {
    u32 best = 0, any = 0;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (p->used && p->kind && p->chan && (p->port | (p->ch << 8)) == key && p->want <= 0x7Fu) {
            any = 1; if (p->want > best) best = p->want;
        }
    }
    for (u32 i = 0; i < PENT; i++) {
        volatile PEnt *e = &r->p[i];
        if (e->used && e->chan && e->track == (key | KEY_TAG) && e->pval && now - e->cyc > LIVE_STALE) e->used = 0;
        if (e->used && e->chan && e->track == (key | KEY_TAG) && e->pval) { any = 1; if (e->pval > best) best = e->pval; }
    }
    volatile Chan *c = 0;
    for (u32 j = 0; j < CHANS; j++)
        if (r->ic[j].used && (r->ic[j].port | (r->ic[j].ch << 8)) == key) { c = &r->ic[j]; break; }
    if (!c) {
        if (!any) return;
        for (u32 j = 0; j < CHANS && !c; j++)
            if (!r->ic[j].used) { c = &r->ic[j]; c->port = key & 0xFF; c->ch = (key >> 8) & 0xFF; c->last = 0xFF; c->cyc = now - INT_GAP; c->used = 1; }
        if (!c) return;
    }
    if (!any) { c->used = 0; return; }              /* voices end with their notes */
    if (best == c->last || (!force && now - c->cyc < INT_GAP)) return;
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (p->used && p->kind && p->chan && (p->port | (p->ch << 8)) == key) int_post(r, key, p->note, best);
    }
    for (u32 i = 0; i < PENT; i++) {
        volatile PEnt *e = &r->p[i];
        if (e->used && e->chan && e->track == (key | KEY_TAG) && e->pval) int_post(r, key, e->note, best);
    }
    c->last = (u8)best; c->cyc = now;
}

static void int_pass(volatile Region *r, u32 now) {
    for (u32 i = 0; i < PLAYERS; i++) {
        volatile Player *p = &r->pl[i];
        if (!p->used || !p->kind) continue;
        u32 key = p->port | (p->ch << 8);
        int done = p->k >= p->n;
        if (p->chan) {
            int_chan(r, key, now, 0);
            if (done) p->used = 0;
            continue;
        }
        if (p->want <= 0x7Fu && p->want != p->last && (done || now - p->cyc >= INT_GAP)) {
            int_post(r, key, p->note, p->want);
            p->last = p->want; p->cyc = now;
        }
        if (done) p->used = 0;
    }
}

/* Live pads on internal tracks (hook at the pad router's post 0x08085B6C:
 * bus r0, event r1, router r5). The router's selected track (+0x2C8) is the
 * UI index; a per-pad key (+0x2CC[idx] == 0xFFFFFF00) is a drum track: as is.
 * External tracks pass on to their own handler (h_poly applies AT Out there). */
static void live_body(u32 bus, const u32 *ev, u32 router) {
    volatile Region *r = region();
    u32 idx = *(volatile u32 *)(router + 0x2C8u);
    if (!r || idx >= 8u || *(volatile u32 *)(router + 0x2CCu + idx * 4u) == 0xFFFFFF00u) {
        FN7(BUS_POST, void (*)(u32, const u32 *))(bus, ev); return;
    }
    DCIMVAC = (u32)AT_DEST & ~31u; DCIMVAC = ((u32)AT_DEST + 32u) & ~31u;
    __asm volatile("dsb" ::: "memory");
    int internal = AT_DEST->magic == AT_DEST_MAGIC && AT_DEST->d[idx].valid && !AT_DEST->d[idx].ch;
    u32 mode = internal ? AT_DEST->d[idx].mode : AT_POLY, key = ev[2] & 0xFFFFu, note = ev[3] & 0x7Fu, now = CYCCNT;
    r->live_int = idx | (key << 8) | (mode << 24);
    if (!internal || mode == AT_POLY) { FN7(BUS_POST, void (*)(u32, const u32 *))(bus, ev); return; }
    if (mode == AT_OFF) { r->live_off++; return; }
    r->bus = bus;
    volatile PEnt *e = pent(r, key | KEY_TAG, note, now);
    e->chan = 1; e->pend = 0; e->pval = (u8)(ev[4] & 0x7Fu); e->cyc = now;
    if (!e->pval) {                                 /* released: drop it, then re-aggregate */
        int_chan(r, key, now, 1);
        u32 ev0[6] = {0x0E, 0, key, note, 0, 0};    /* this voice may still ring: let it go */
        FN7(BUS_POST, void (*)(u32, const u32 *))(bus, ev0);
        e->used = 0;
        return;
    }
    int_chan(r, key, now, 1);
}

__attribute__((used, retain)) void h_live(u32 bus, const u32 *ev, u32 router) {
    diag_sp();
    u32 p = irq_save();
    live_body(bus, ev, router);
    irq_restore(p);
}

void curve_start(u32 track, u32 note, u32 word, u32 tick_lo, u32 tick_hi, u32 bus, u32 pad, u32 len) {
    u32 p = irq_save();
    curve_start_body(track, note, word, tick_lo, tick_hi, bus, pad, len);
    irq_restore(p);
}
void curve_emit(u32 track, u32 clock, int start, int len) {
    u32 p = irq_save();
    curve_emit_body(track, clock, start, len);
    irq_restore(p);
}
