/* SPDX-License-Identifier: GPL-3.0-or-later */
/* F1: modulation for External tracks (M7): 2 LFOs and 2 envelopes per track,
 * up to 16 MIDI destinations, each base + sum(source x amount), sent as MIDI.
 *
 * Settings (ModCfg, diag_shared.h) belong to the M4 and are read in place.
 * mod_block runs once per track per audio block (from retrig_emit); mod_note
 * runs for every External Note On / Note Off (from the poly cap's h_note).
 *
 * Timing:
 *   - free LFO rates and envelopes follow the cycle counter;
 *   - synced LFO rates follow the absolute song position: tick = tick anchor +
 *     (sample - sample anchor) x BPM / 3000 (stock's clock record, inverted
 *     from 0x080A2F00: samples = 48000 x ticks / 960 x 60 / BPM).
 * Every Note On restarts the envelopes' attack (from their current level) and,
 * with Restart on, the LFOs. The envelope gate closes when the track has no
 * sounding note left (the poly cap's voice list).
 *
 * Output: only changed values, at most every 8 ms per destination, taking the
 * port's token (400/s, shared with aftertouch; notes never wait for tokens).
 * Integer arithmetic only (no FP runtime in this build). */
#include "diag_shared.h"

#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define DCIMVAC (*(volatile u32 *)0xE000EF5Cu)
#define MS_CYC 480000u
#define DEST_GAP (8u * MS_CYC)
#define RING_PTR (*(volatile u32 *)0x2401BD3Cu)   /* M7->M4 ring object (Lab's PRODUCER_GLOBAL) */
#define RING_MAX_FILL 6u                       /* leave >= 10 of 16 slots for notes and UI events */
#define MOD_STAGE_OFF 0u
#define MOD_STAGE_A 1u
#define MOD_STAGE_D 2u
#define MOD_STAGE_S 3u
#define MOD_STAGE_R 4u

/* m7_polyat.c */
void *m1_mod_run(void);
int m1_token(u32 port, u32 now);
#define MSG_SEND 0x08065600u                  /* msg_send(engine + 0x7C8, ev): the MIDI-out queue */
u32 m1_track_pressure(u32 track);
int m1_notes_held(u32 track);

typedef struct {
    u32 track, cyc, nseq, seen;          /* track object, last block, Note On count, handled count */
    u32 phase[2];                        /* free-running LFO phase (Q32) */
    u32 soff[2];                         /* synced LFO restart offset (Q32) */
    u32 last_p[2];                       /* last effective phase (random S&H wraps) */
    u32 env[2];                          /* envelope level, 0..65536 */
    u32 rnd;                             /* LCG state */
    short rval[2];                       /* random LFO held value */
    u8 stage[2], loops[2];
    u8 gate, vel, key, cfg_ok;
    u16 last[MOD_DESTS];                 /* last value sent (0xFFFF = none) */
    u32 dcyc[MOD_DESTS];                 /* when it was sent */
} ModRun;
_Static_assert(MOD_TRACKS * sizeof(ModRun) + 16u <= MOD_RUN_BYTES, "mod runtime larger than its slice");

typedef struct { u32 seq, pad_[3]; ModRun t[MOD_TRACKS]; } ModState;

static const u16 SYNC_TICKS[15] = {30720, 15360, 7680, 3840, 1920, 1280, 960, 640, 480, 320, 240, 160, 120, 80, 60};
/* sin over a quarter wave, 65 points, Q15 */
static const u16 SINQ[65] = {
    0, 804, 1608, 2410, 3212, 4011, 4808, 5602, 6393, 7179, 7962, 8739, 9512, 10278, 11039, 11793,
    12539, 13279, 14010, 14732, 15446, 16151, 16846, 17530, 18204, 18868, 19519, 20159, 20787, 21403, 22005, 22594,
    23170, 23731, 24279, 24811, 25329, 25832, 26319, 26790, 27245, 27683, 28105, 28510, 28898, 29268, 29621, 29956,
    30273, 30571, 30852, 31113, 31356, 31580, 31785, 31971, 32137, 32285, 32412, 32521, 32609, 32678, 32728, 32757, 32767};

static ModState *state(void) { return (ModState *)m1_mod_run(); }

static volatile ModCfg *config(ModState *st) {
    DCIMVAC = (u32)MODHEAD & ~31u;
    __asm volatile("dsb" ::: "memory");
    volatile ModHead *h = MODHEAD;
    u32 c = h->cfg;
    if (h->magic != MODHEAD_MAGIC || h->check != ~c || c < 0xC0000000u || c >= 0xC8000000u || (c & 31u)) return 0;
    volatile ModCfg *cfg = (volatile ModCfg *)c;
    DCIMVAC = c;                                             /* the header line of the block */
    __asm volatile("dsb" ::: "memory");
    u32 seq = cfg->seq;
    if (cfg->magic != MODHEAD_MAGIC || (seq & 1u)) return 0;  /* M4 writing: use it next block */
    if (seq != st->seq) {                                     /* changed: drop every cached line of it */
        for (u32 a = c; a < c + sizeof(ModCfg); a += 32u) DCIMVAC = a;
        __asm volatile("dsb" ::: "memory");
        st->seq = seq;
    }
    return cfg;
}

/* 64 / 32 division without the runtime library. */
static unsigned long long udiv64(unsigned long long n, u32 d, u32 *rem) {
    unsigned long long q = 0, r = 0;
    for (int i = 63; i >= 0; i--) {
        r = (r << 1) | ((n >> i) & 1u);
        if (r >= d) { r -= d; q |= 1ull << i; }
    }
    if (rem) *rem = (u32)r;
    return q;
}

/* IEEE single -> Q16 (BPM; 0 for anything unusable). */
static u32 f2q16(u32 bits) {
    int e = (int)((bits >> 23) & 0xFFu) - 127;
    u32 m = (bits & 0x7FFFFFu) | 0x800000u;
    if ((bits >> 31) || e < -16 || e > 14) return 0;
    int sh = e - 23 + 16;                                     /* value = m * 2^(e-23), Q16 */
    return sh >= 0 ? m << sh : m >> -sh;
}

/* Absolute song position as a phase of a period in ticks (Q32), or 0 if no clock. */
static u32 sync_phase(const u32 *clock, int start, u32 period) {
    u32 bpm = f2q16(clock[6]);
    if (!bpm || !period) return 0;
    u32 ds = clock[0] + (u32)start - clock[4];
    if ((int)ds < 0) ds = 0;
    unsigned long long tq = udiv64((unsigned long long)ds * bpm, 3000u, 0);   /* ticks since the anchor, Q16 */
    tq += ((unsigned long long)clock[2] | ((unsigned long long)clock[3] << 32)) << 16;
    u32 rem;
    udiv64(tq, period << 16, &rem);                                            /* period <= 30720: fits */
    return (u32)udiv64((unsigned long long)rem << 16, period, 0);              /* rem / (period << 16) in Q32 */
}

static int sinq(u32 p) {                                      /* p: Q32 phase -> Q15 */
    u32 q = p >> 30, i = (p >> 24) & 63u, f = (p >> 16) & 0xFFu;
    u32 a = SINQ[i], b = SINQ[i + 1u];
    if (q & 1u) { a = SINQ[64u - i]; b = SINQ[63u - i]; }
    int v = (int)(a + (((int)b - (int)a) * (int)f >> 8));
    return q >= 2u ? -v : v;
}

static int shape(ModRun *r, u32 k, u32 sh, u32 p) {          /* Q15: bipolar or 0..32767 */
    int t = (int)(p >> 16);                                   /* 0..65535 */
    int tri = t < 32768 ? -32767 + 2 * t : 32767 - 2 * (t - 32768);
    switch (sh) {
    case 0: return 32767 - t;                                 /* Rev Saw */
    case 1: return t - 32768;                                 /* Saw */
    case 2: return tri;                                       /* Triangle */
    case 3: return (tri + 32767) >> 1;                        /* Pos Tri */
    case 4: return sinq(p);                                   /* Sine */
    case 5: return (sinq(p) + 32767) >> 1;                    /* Pos Sine */
    case 6: return t < 32768 ? 32767 : -32767;                /* Square */
    case 7: return t < 32768 ? 32767 : 0;                     /* Pos Square */
    default:                                                  /* Random: new value each cycle */
        if (p < r->last_p[k]) {
            r->rnd = r->rnd * 1664525u + 1013904223u;
            r->rval[k] = (short)((int)(r->rnd >> 16) - 32768 < -32767 ? -32767 : (int)(r->rnd >> 16) - 32768);   /* both signs (v3.59 and earlier: -32767..0 only) */
        }
        return r->rval[k];
    }
}

static u32 free_mhz(u32 rate) {                              /* 0..1000 -> 50 mHz .. 51.2 Hz, exponential */
    if (rate > 1000u) rate = 1000u;
    u32 base = 50u << (rate / 100u), f = rate % 100u;
    return base + base * f * 693u / 100000u;
}

static u32 env_us(u32 v) { if (v > 1000u) v = 1000u; return (1u + v * v / 100u) * 1000u; }   /* 1 ms .. 10 s */

static void env_step(ModRun *r, u32 k, volatile ModEnv *e, u32 dt_us) {
    u32 lvl = r->env[k], sus = (e->s > 1000u ? 1000u : e->s) * 65536u / 1000u;
    switch (r->stage[k]) {
    case MOD_STAGE_A: {
        u32 inc = dt_us * 65536u / env_us(e->a);
        if (lvl + inc >= 65536u) { lvl = 65536u; r->stage[k] = MOD_STAGE_D; } else lvl += inc;
        break;
    }
    case MOD_STAGE_D: {                                        /* loop: attack-decay cycles (to 0) */
        u32 dec = dt_us * 65536u / env_us(e->d);
        int again = e->loop && r->gate && (e->loop > 32u || r->loops[k]);
        u32 target = again ? 0u : sus;                         /* the last decay settles at sustain */
        if (lvl <= target + dec) {
            lvl = target;
            if (again) { if (e->loop <= 32u) r->loops[k]--; r->stage[k] = MOD_STAGE_A; }
            else r->stage[k] = MOD_STAGE_S;
        } else lvl -= dec;
        break;
    }
    case MOD_STAGE_S: lvl = sus; break;
    case MOD_STAGE_R: {
        u32 dec = dt_us * 65536u / env_us(e->r);
        if (lvl <= dec) { lvl = 0; r->stage[k] = MOD_STAGE_OFF; } else lvl -= dec;
        break;
    }
    default: lvl = 0; break;
    }
    if (!r->gate && r->stage[k] != MOD_STAGE_OFF && r->stage[k] != MOD_STAGE_R) r->stage[k] = MOD_STAGE_R;
    r->env[k] = lvl;
}

/* Unread entries in the M7->M4 ring (16 slots, no full check: an overwrite can
 * drop a Note Off or a button press). rd (+0x2340) is written only by the M4 and
 * sits alone in its cache line (as the black box's h_ring reads it). BE-MT4
 * peaked at 12 with modulation running: modulation waits while it is busy. */
static int ring_busy(void) {
    u32 obj = RING_PTR;
    if (!obj || (obj & 3u)) return 0;
    DCIMVAC = (obj + 0x2340u) & ~31u;
    __asm volatile("dsb" ::: "memory");
    return *(volatile u32 *)(obj + 0x2360u) - *(volatile u32 *)(obj + 0x2340u) >= RING_MAX_FILL;
}

static void mod_block_body(u32 track, const u32 *clock, int start, u32 router) {
    ModState *st = state();
    if (!st || !router || !*(volatile u8 *)(track + 0x86D1u)) return;   /* MIDI out off */
    u32 idx = *(volatile u32 *)(track + 0x86D4u);
    if (idx >= MOD_TRACKS) return;
    volatile ModCfg *cfg = config(st);
    if (!cfg) return;
    volatile ModTrack *t = &cfg->t[idx];
    ModRun *r = &st->t[idx];
    u32 now = CYCCNT;
    if (r->track != track) {                                  /* first block for this track object */
        for (u32 i = 0; i < sizeof(ModRun); i++) ((u8 *)r)[i] = 0;
        r->track = track; r->cyc = now; r->rnd = track ^ now;
        for (u32 i = 0; i < MOD_DESTS; i++) r->last[i] = 0xFFFFu;
    }
    int any = 0;
    for (u32 i = 0; i < MOD_DESTS && !any; i++) any = t->d[i].type != MOD_DEST_NONE;
    if (!any) { r->cyc = now; return; }
    u32 dt_us = (now - r->cyc) / 480u;
    if (dt_us > 50000u) dt_us = 50000u;
    r->cyc = now;
    int restart = r->seen != r->nseq;
    r->seen = r->nseq;
    int src[MOD_SRC_N] = {0};
    for (u32 k = 0; k < 2; k++) {                             /* LFOs */
        volatile ModLfo *l = &t->lfo[k];
        u32 p;
        if (l->sync) {
            p = sync_phase(clock, start, SYNC_TICKS[l->srate < 15u ? l->srate : 6u]);
            if (restart && l->restart) r->soff[k] = 0u - p;
            p += r->soff[k];
        } else {
            if (restart && l->restart) r->phase[k] = 0;
            u32 x = free_mhz(l->rate) * dt_us;                 /* mHz x us; x 2^32 / 1e9 = x 4.294967296 */
            r->phase[k] += x * 4u + (u32)(((unsigned long long)x * 1266874889u) >> 32);   /* wraps: phase is modular */
            p = r->phase[k];
        }
        int v = shape(r, k, l->shape, p);
        r->last_p[k] = p;
        src[MOD_SRC_LFO1 + k] = v * (int)(l->depth > 1000u ? 1000u : l->depth) / 1000;
    }
    for (u32 k = 0; k < 2; k++) {                             /* envelopes */
        volatile ModEnv *e = &t->env[k];
        if (restart) { r->stage[k] = MOD_STAGE_A; r->loops[k] = e->loop <= 32u ? e->loop : 0; }
        env_step(r, k, e, dt_us);
        src[MOD_SRC_ENV1 + k] = (int)(r->env[k] >> 1) > 32767 ? 32767 : (int)(r->env[k] >> 1);
    }
    src[MOD_SRC_VEL] = r->vel * 258;
    src[MOD_SRC_KEY] = r->key * 258;
    src[MOD_SRC_PRESS] = (int)m1_track_pressure(track) * 258;
    u32 port = *(volatile u8 *)(track + 0x31u), ch = *(volatile u8 *)(track + 0x32u);
    for (u32 i = 0; i < MOD_DESTS; i++) {
        volatile ModDest *d = &t->d[i];
        if (d->type == MOD_DEST_NONE) continue;
        int acc = 0;
        for (u32 j = 0; j < 3; j++) {
            u32 s = d->src[j];
            int a = d->amt[j];
            if (s && s < MOD_SRC_N && a) acc += src[s] * (a > 1000 ? 1000 : a < -1000 ? -1000 : a);
        }
        int v;
        if (d->type == MOD_DEST_PB) { v = 8192 + acc / 4000; v = v < 0 ? 0 : v > 16383 ? 16383 : v; }
        else { v = acc / 258000; v = v < 0 ? 0 : v > 127 ? 127 : v; }   /* full scale (32767 x 1000) = 127; base 0 until CC pairs (F3) */
        if ((u32)v == r->last[i] || now - r->dcyc[i] < DEST_GAP) continue;
        if (ring_busy()) break;                                /* notes and UI first: next block */
        if (!m1_token(port, now)) break;                       /* port budget spent: next block */
        /* Stock's CC case writes the value with its 14-bit writer (0x08105D10: status
         * & 0xEF == 0xA0 -> data byte = value >> 7), so a 7-bit value goes in as v << 7
         * (v3.41 sent 0..127 and every CC went out as 0). */
        u32 ev[6] = {0x5E, 0, port, ch, d->num & 0x7Fu, (u32)v << 7};
        if (d->type == MOD_DEST_PB) { ev[0] = 0x5D; ev[4] = (u32)v; ev[5] = 0; }
        else if (d->type == MOD_DEST_AT) { ev[0] = 0x60; ev[4] = (u32)v; ev[5] = 0; }
        ((void (*)(u32, const u32 *))(MSG_SEND | 1u))(router + 0x7C8u, ev);
        r->last[i] = (u16)v; r->dcyc[i] = now;
    }
}

/* Every External Note On / Note Off (h_note, interrupts masked). */
void mod_note(u32 track, u32 note, u32 vel, int on) {
    ModState *st = state();
    u32 idx = *(volatile u32 *)(track + 0x86D4u);
    if (!st || idx >= MOD_TRACKS || st->t[idx].track != track) return;
    ModRun *r = &st->t[idx];
    if (on) { r->nseq++; r->gate = 1; r->vel = (u8)(vel & 0x7Fu); r->key = (u8)(note & 0x7Fu); }
    else r->gate = (u8)(m1_notes_held(track) != 0);
}

static inline u32 irq_save(void) {
    u32 p;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory");
    return p;
}
static inline void irq_restore(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }

void mod_block(u32 track, const u32 *clock, int start, u32 router) {
    u32 p = irq_save();
    mod_block_body(track, clock, start, router);
    irq_restore(p);
}

/* ---- F5 Note Mod -----------------------------------------------------------------
 * One source per track (ModCfg.nm) shifts each sequenced note when it starts:
 *   nm_on      in the accept stub, before stock's note_route: picks the offset and
 *              records {original -> sent} in the track's map;
 *   nm_off     before stock's sequencer Note Off (0x0805FE00, two call sites): a
 *              mapped original goes out as the pitch that was sent;
 *   nm_all_off at the entry of stock's stop / all-notes-off (0x080618E0): ends every
 *              mapped note first (stock's own list holds the original pitches);
 *   nm_block   every audio block per track: advances the free-running phase.
 * Melodic notes move in steps of the project's scale (a note outside it snaps to the
 * nearest scale note first), drum notes (stock's rule: pad <= 15 with a sound) move
 * by pads, wrapping in the kit. Notes starting on the same tick share the offset
 * (chords keep their shape); retrigger hits and curves get the sent pitch from the
 * stub. Stock ends a pending note before starting one with the same note and pad
 * (0x08060A4C), so {original note, pad} is unique per track among sounding notes. */
void *m1_nm_run(void);
#ifndef FN
#define FN(a, t) ((t)((a) | 1u))
#endif
#define NM_MAP 16u
#define NOTE_OFF 0x0805FE00u               /* (track, note, timing, pad, router) */
typedef struct { u8 note, sent; u16 pad, spad; } NmMap;
typedef struct {
    u32 track, cyc, phase, last_p, rnd, tick_lo, tick_hi;
    int steps;
    short rval; u16 used;                  /* used: map slots in use (bits) */
    u8 has_tick, armed, pad_[2];          /* armed: Triggered, stopped: phase held at 0 until a note */
    NmMap m[NM_MAP];
} NmRun;
typedef struct { NmRun t[MOD_TRACKS]; u32 shifted, offs, full, alloff; } NmState;
_Static_assert(sizeof(NmState) <= NM_RUN_BYTES, "Note Mod runtime larger than its slice");

/* Stock's 23 scales (M7 table 0x2401DA18, Scale list order), semitones from the root. */
static const u8 SCALE_N[23] = {12, 7, 5, 7, 5, 6, 7, 7, 7, 7, 5, 5, 5, 6, 8, 7, 7, 7, 7, 7, 7, 7, 7};
static const u8 SCALE_OFS[23][12] = {
    {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}, {0, 2, 4, 5, 7, 9, 11}, {0, 2, 4, 7, 9}, {0, 2, 3, 5, 7, 8, 10},
    {0, 3, 5, 7, 10}, {0, 3, 5, 6, 7, 10}, {0, 1, 4, 5, 7, 8, 11}, {0, 1, 3, 6, 7, 8, 11}, {0, 2, 3, 6, 7, 8, 11},
    {0, 2, 4, 5, 6, 8, 10}, {0, 2, 5, 7, 10}, {0, 1, 5, 7, 8}, {0, 4, 5, 7, 11}, {0, 2, 4, 6, 8, 10},
    {0, 2, 3, 5, 6, 8, 9, 11}, {0, 2, 3, 5, 7, 8, 11}, {0, 2, 3, 5, 7, 9, 11}, {0, 2, 3, 5, 7, 9, 10},
    {0, 1, 3, 5, 7, 8, 10}, {0, 2, 4, 6, 7, 9, 11}, {0, 2, 4, 5, 7, 9, 10}, {0, 2, 3, 5, 7, 8, 10},
    {0, 1, 3, 5, 6, 8, 10}};

static int fdiv(int a, int b) { int q = a / b; return (a % b && (a < 0) != (b < 0)) ? q - 1 : q; }

/* note moved by steps of scale sc with root pitch class root (0 = C). */
static u32 scale_shift(u32 note, int steps, u32 sc, u32 root) {
    const u8 *s = SCALE_OFS[sc];
    int n = SCALE_N[sc], rel = (int)note - (int)root, oct = fdiv(rel, 12), pc = rel - oct * 12, d = 0, best = 99;
    for (int i = 0; i <= n; i++) {                 /* nearest degree; i == n: the next octave's root */
        int dist = pc - (i < n ? s[i] : 12);
        if (dist < 0) dist = -dist;
        if (dist < best) { best = dist; d = i; }
    }
    int idx = oct * n + d + steps, o2 = fdiv(idx, n), out = (int)root + o2 * 12 + s[idx - o2 * n];
    while (out > 127) out -= 12;
    while (out < 0) out += 12;
    return (u32)out;
}

static int nm_drum(u32 track, u32 note, u32 pad) {   /* note_route's rule for a pad's own sound */
    if (pad > 15u) return 0;
    if (!note) return 1;
    return *(volatile u8 *)(track + 0x86D2u) && *(volatile u32 *)(track + 0x86DCu + 4u * pad) != 0xFFFFFF00u;
}

static NmRun *nm_track(NmState *st, u32 track, u32 *idx) {
    *idx = *(volatile u32 *)(track + 0x86D4u);
    if (*idx >= MOD_TRACKS) return 0;
    NmRun *r = &st->t[*idx];
    if (r->track != track) {                     /* a new track object in this slot */
        for (u32 i = 0; i < sizeof(NmRun); i++) ((u8 *)r)[i] = 0;
        r->track = track; r->cyc = CYCCNT; r->rnd = track ^ CYCCNT;
    }
    return r;
}

static int nm_value(NmRun *r, volatile ModNote *nm, u32 p, int fresh) {
    if (nm->shape < 8u) {
        ModRun tmp;                               /* the periodic shapes need no state */
        return shape(&tmp, 0, nm->shape, p);
    }
    if (fresh || p < r->last_p) {                 /* Random: a new value per cycle (or per note) */
        r->rnd = r->rnd * 1664525u + 1013904223u;
        r->rval = (short)((int)(r->rnd >> 16) - 32768 < -32767 ? -32767 : (int)(r->rnd >> 16) - 32768);   /* both signs */
    }
    return r->rval;
}

__attribute__((used, retain)) u32 nm_on(u32 track, u32 note, u32 *pad, const u32 *csp) {
#ifdef BE_NO_NMOD
    (void)track; (void)pad; (void)csp; return note;
#else
    NmState *st = (NmState *)m1_nm_run();
    ModState *ms = state();
    if (!st || !ms) return note;
    u32 p0 = irq_save(), idx;
    volatile ModCfg *cfg = config(ms);
    NmRun *r = cfg ? nm_track(st, track, &idx) : 0;
    volatile ModNote *nm = r ? &cfg->nm[idx] : 0;
    u32 sent = note, opad = *pad & 0xFFFFu, spad = opad;
    if (!nm || !nm->amount) { irq_restore(p0); return note; }
    int drum = nm_drum(track, note, opad), steps;
    u32 key = cfg->key, sc = (key >> 16) & 1u && (key & 0xFFu) < 23u ? key & 0xFFu : 0u;
    u32 root = (key >> 16) & 1u ? (((key >> 8) & 0xFFu) + 9u) % 12u : 0u;   /* stock's Root list starts at A */
    if (r->has_tick && r->tick_lo == csp[8] && r->tick_hi == csp[9]) steps = r->steps;   /* a chord */
    else {
        u32 p;
        if (nm->restart && r->armed) { r->phase = 0; r->armed = 0; }   /* Triggered: the run starts here */
        if (nm->sync) {
            u32 period = SYNC_TICKS[nm->srate < 15u ? nm->srate : 6u], rem;
            udiv64((unsigned long long)csp[8] | ((unsigned long long)csp[9] << 32), period, &rem);
            p = (u32)udiv64((unsigned long long)rem << 32, period, 0);
        } else p = r->phase;
        int v = nm_value(r, nm, p, nm->restart);
        r->last_p = p;
        u32 range = (nm->range < 4u ? nm->range : 3u) + 1u;
        int span = (int)(drum ? range * 4u : range * SCALE_N[sc]);
        int num = v * (int)(nm->amount > 1000u ? 1000u : nm->amount) / 1000 * span;   /* |num| <= 32767 x 48 */
        steps = (num >= 0 ? num + 16383 : num - 16383) / 32767;
        r->steps = steps; r->tick_lo = csp[8]; r->tick_hi = csp[9]; r->has_tick = 1;
    }
    if (steps) {
        if (drum) {
            spad = (u32)(((int)opad + steps) % 16 + 16) % 16u;
            if (note) { int x = (int)note + steps; sent = (u32)(x < 0 ? 0 : x > 127 ? 127 : x); }
        } else sent = scale_shift(note & 0x7Fu, steps, sc, root);
    }
    if (sent != note || spad != opad) {
        u32 k = 0;
        while (k < NM_MAP && (r->used >> k & 1u)) k++;
        if (k == NM_MAP) { st->full++; sent = note; spad = opad; }   /* no room: play it unshifted */
        else {
            r->m[k].note = (u8)note; r->m[k].sent = (u8)sent; r->m[k].pad = (u16)opad; r->m[k].spad = (u16)spad;
            r->used |= (u16)(1u << k);
            *pad = (*pad & 0xFFFF0000u) | spad;
            st->shifted++;
        }
    }
    irq_restore(p0);
    return sent;
#endif
}

/* a = the saved r0-r3 of a call to stock's Note Off: track, note, timing, pad. */
__attribute__((used, retain)) void nm_off(u32 *a) {
    NmState *st = (NmState *)m1_nm_run();
    if (!st) return;
    u32 p0 = irq_save(), idx = *(volatile u32 *)(a[0] + 0x86D4u);
    NmRun *r = idx < MOD_TRACKS ? &st->t[idx] : 0;
    if (r && r->track == a[0] && r->used)
        for (u32 k = 0; k < NM_MAP; k++) {
            if (!(r->used >> k & 1u) || r->m[k].note != (a[1] & 0xFFu) || r->m[k].pad != (a[3] & 0xFFFFu)) continue;
            a[1] = r->m[k].sent; a[3] = (a[3] & 0xFFFF0000u) | r->m[k].spad;
            r->used &= (u16)~(1u << k);
            st->offs++;
            break;
        }
    irq_restore(p0);
}

/* Stock's stop / all-notes-off for a track: end the shifted notes first. */
__attribute__((used, retain)) void nm_all_off(u32 track, u32 timing, u32 router) {
    NmState *st = (NmState *)m1_nm_run();
    if (!st) return;
    u32 idx;
    NmRun *r = nm_track(st, track, &idx);            /* also a stop before this track's first note */
    if (!r) return;
    ModState *ms = state();
    volatile ModCfg *cfg = ms ? config(ms) : 0;
    if (cfg && cfg->nm[idx].restart) {                /* Triggered: the next run starts the shape over */
        r->phase = 0; r->armed = 1;
        r->rnd = 0x9E3779B9u * (idx + 1u);             /* Random: the same notes every run */
    }
    for (u32 k = 0; k < NM_MAP; k++) {
        if (!(r->used >> k & 1u)) continue;
        r->used &= (u16)~(1u << k);
        st->alloff++;
        FN(NOTE_OFF, void (*)(u32, u32, u32, u32, u32))(track, r->m[k].sent, timing, r->m[k].spad, router);
    }
    r->has_tick = 0;
}

void nm_block(u32 track) {
    NmState *st = (NmState *)m1_nm_run();
    ModState *ms = state();
    if (!st || !ms) return;
    u32 p0 = irq_save(), idx;
    volatile ModCfg *cfg = config(ms);
    NmRun *r = cfg ? nm_track(st, track, &idx) : 0;
    if (r) {
        volatile ModNote *nm = &cfg->nm[idx];
        u32 now = CYCCNT, dt_us = (now - r->cyc) / 480u;
        r->cyc = now;
        if (nm->amount && !nm->sync && !(nm->restart && r->armed)) {
            if (dt_us > 50000u) dt_us = 50000u;
            u32 x = free_mhz(nm->rate) * dt_us;
            r->phase += x * 4u + (u32)(((unsigned long long)x * 1266874889u) >> 32);
        }
    }
    irq_restore(p0);
}
