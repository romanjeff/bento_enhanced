/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Analog oscillators on Wavetable tracks, M7 side (S6a; docs/plan-synths-168.md section 7).
 *
 * A port of bento-playground's A1 hooks (eu4_work/drums/kick.cpp kick_analog_enter / kick_analog_osc at
 * 9fc7787, GPL-3.0-or-later), rewritten for our layout and with the plan's fixes:
 *   - which tracks are Analog comes from the M4's 16-line table in the synth area (synth_shared.h),
 *     cached per Wavetable root (16 entries) and refilled only when the M4's serial moves; upstream had a
 *     one-entry cache that missed on every lookup with two Analog tracks, and a 128-entry scan;
 *   - glide's "previous note" is per track (in the root's cache entry), not global;
 *   - an entry is the root's only while root+0x0C is the handle it was filled for (stock reuses roots);
 *   - the glide offset on the sub oscillator follows stock's own rewrites of that field and is taken back when
 *     the track is not Analog any more;
 *   - sync covers AN_EVENTS frames: a longer block renders free-running and is counted.
 * Two vtable words point at build.py's trampolines (in this window, before this ELF):
 *   A1  Wavetable root slot 18 (voice render): an_enter(root, voice), then stock's render
 *   A2  Wavetable oscillator slot 0 (render): an_osc(osc, scratch, out) != 0 means handled, else stock
 * Pitch, level, smoothing and mixing are stock's own helpers in stock's order (the oscillator render at
 * S_WT_OSC_RENDER, which is what this replaces for WT1 / WT2 of Analog voices): only the wavetable read
 * and its "table loaded" gate are ours. The sub oscillator (a different class) stays native.
 * Hard-float ELF; entry points take integers and pointers only. No libm: the glide coefficient is
 * stock's exp2 (S_EXP2: 2^x from a table over +-10 octaves; S_OCT_RATIO is the same table times C1's
 * 32.703 Hz, the pitch), cached per root while the glide time and block length stay (E5).
 * Measurement: DWT cycles in both hooks per audio block (a block = a change of stock's LOAD ring index),
 * into the stats lines the M4 prints at save time (m4_synth.c an_print). */
#include "analog.h"
#include "../synth_shared.h"

typedef unsigned u32;
typedef unsigned char u8;
#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define F(p, o) (*(volatile float *)((u8 *)(p) + (o)))
#define FN(a, t) ((t)((a) | 1u))
#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define POOL_LEFT (*(volatile u32 *)S_POOL_LEFT_RAM)
#define POOL_CUR (*(volatile u32 *)S_POOL_CUR_RAM)

#ifndef AN_E3
#define AN_E3 1      /* a silent sync master only tracks its phase */
#endif
#ifndef AN_E5
#define AN_E5 1      /* the glide coefficient is cached per root */
#endif

/* oscillator fields read the way stock's render reads them (S_WT_OSC_RENDER) */
#define OSC_DETUNE_MILLI 0x0Cu     /* int, 1/1000 semitone */
#define OSC_OCTAVES 0x04u          /* float */
#define OSC_PITCH_PARAM 0x70u      /* modulated parameter: pitch (octaves) */
#define OSC_NOTE 0x10u
#define OSC_POSITION_PARAM 0x30u   /* Position 0..1: the Analog shape */
#define OSC_LEVEL_PARAM 0xB0u
#define OSC_SMOOTH 0xF8u           /* level smoother */
#define OSC_MOVING 0x104u          /* smoother still moving: not silent at level 0 */
#define OSC_RATE_DIV 0x108u
#define OSC_POSITION 0x20u         /* written: the integer position, as stock */
#define OSC_RATE 0x1E8u            /* written: the playback rate, as stock */
#define SUB_DETUNE 0x04u           /* the sub oscillator's octave offset, reread by its render each block */
#define NO_NOTE 0xFFFFFFFFu

struct Root {                      /* per Wavetable root, M7 private */
    u32 root, serial, mode, glide; /* mode: AN_ON | AN_SYNC; glide 0..AN_GLIDE_MAX */
    u32 last_note, handle, coef_key;   /* last_note NO_NOTE: none yet; handle: root+0x0C when filled */
    float coef;
};
static_assert(sizeof(Root) == 32, "one line per root");
struct Run {                       /* the hooks' running state, M7 private (two lines) */
    u32 idx, serial, voice, mode;
    Root *root;
    u32 cyc, oscs, voices;
    u32 victim, r9, r10, r11, r12, r13, r14, r15;
};
static_assert(sizeof(Run) == 64, "two lines");
static_assert(sizeof(SaStats) == 64 && sizeof(SaM7) == 32 && sizeof(SaM4) == 32 && sizeof(SaTrack) == 32, "line layout");
#define RUN ((Run *)SA(SA_RUN))
static_assert(S_PROBE_SCRATCH + 0x100u <= SYNTH_AREA, "the M7's price-check scratch stays below the synth area");
#define ROOTS ((Root *)SA(SA_ANCACHE))
#define M7HDR ((volatile SaM7 *)SA(SA_M7))
#define M4HDR ((volatile SaM4 *)SA(SA_M4))
#define STATS ((volatile SaStats *)SA(SA_STATS))
#define TAB ((volatile SaTrack *)SA(SA_ANTAB))
static_assert(SA_RUN + sizeof(Run) <= SA_ANCACHE && SA_ANCACHE + SA_ANCACHE_N * sizeof(Root) <= SA_UI, "layout");

static inline void dsb() { __asm volatile("dsb" ::: "memory"); }
static inline void inval(u32 a, u32 n) {
    dsb();
    for (u32 x = a & ~31u; x < a + n; x += 32u) *(volatile u32 *)SA_DCIMVAC = x;
    dsb();
}
static inline void clean(u32 a, u32 n) {
    dsb();
    for (u32 x = a & ~31u; x < a + n; x += 32u) *(volatile u32 *)SA_DCCMVAC = x;
    dsb();
}

/* The area is ours: the M7 took the reservation this boot (m7_polyat.c synth_take). Both hooks test it: a reset can
 * leave the header and the run state in SDRAM while the pool is stock's again (review 2026-11-08). Two loads of one
 * cached line; read while stock's allocator is between its two counter writes it says "not ours" for that call, and
 * the oscillator then renders natively for one block (silent on an Analog track). */
static inline int area_ok() {
    return M7HDR->magic == SA_M7_MAGIC && POOL_CUR + POOL_LEFT == SYNTH_AREA;
}

/* The block just finished: its figures into the stats lines (only blocks where an Analog voice ran). */
static void finish(Run *r) {
    if (r->voices) {
        volatile SaStats *s = STATS;
        u32 c = r->cyc;
        s->blocks++;
        s->sum16 += c >> 4;
        if (s->sum16 >= 0x80000000u) { s->sum16 >>= 1; s->blocks >>= 1; }
        if (c > s->peak) s->peak = c;
        if (r->oscs > s->osc_peak) s->osc_peak = r->oscs;
        if (r->voices > s->voices_peak) s->voices_peak = r->voices;
        u32 pm = c / 1280u;                                   /* per mille of the 1,280,000-cycle block */
        s->hist[pm < 50u ? 0 : pm < 150u ? 1 : pm < 300u ? 2 : 3]++;
        s->renders += r->oscs;
        clean(SA(SA_STATS), sizeof(SaStats));
    }
    r->cyc = r->oscs = r->voices = 0;
}

/* The M4's serial, or 0 if its header is not for this generation of ours (then nothing is Analog). */
static u32 m4_serial() {
    inval(SA(SA_M4), 32u);
    volatile SaM4 *h = M4HDR;
    return h->magic == SA_M4_MAGIC && h->m7gen == M7HDR->gen ? (h->serial | 1u) : 0u;
}

/* The track's mode from the M4's table (seqlocked lines, one fill each), for the handle the root has now. */
static void refill(Root *e, void *root, u32 serial) {
    u32 handle = W(root, S_ROOT_HANDLE), mode = 0, glide = 0;
    if (serial) {
        inval(SA(SA_ANTAB), SA_ANTAB_N * 32u);
        for (u32 i = 0; i < SA_ANTAB_N; i++) {
            volatile SaTrack *t = &TAB[i];
            u32 head = t->head, h = t->handle, f = t->flags, g = t->glide, tail = t->tail;
            if ((head & 1u) || head != tail || h != handle || !(f & AN_ON)) continue;
            mode = AN_ON | (f & AN_SYNC);
            glide = g > AN_GLIDE_MAX ? AN_GLIDE_MAX : g;
            volatile SaM7 *m = M7HDR;                        /* the one-shot check the M4 prints */
            if (!m->handle_ok) { m->handle_seen = handle; m->root_seen = (u32)root; m->handle_ok = 1; clean(SA(SA_M7), 32u); }
            break;
        }
    }
    if (e->root != (u32)root || e->handle != handle || e->glide != glide) { e->last_note = NO_NOTE; e->coef_key = 0; }
    e->root = (u32)root; e->handle = handle; e->serial = serial; e->mode = mode; e->glide = glide;
    STATS->misses++;
}

/* Stock reuses roots: freeing a track sets the root's key to 0xFFFFFF00 and a new track takes the first free root
 * with its own handle (0x08059C28.., 0x08059C5E..), without any table change on the M4. So an entry is the root's
 * only while root+0x0C is still the handle it was filled for (review 2026-11-08: a plain Wavetable track on a
 * reused root played as Analog). */
static Root *lookup(Run *r, void *root) {
    Root *free_ = 0;
    for (u32 i = 0; i < SA_ANCACHE_N; i++) {
        Root *e = &ROOTS[i];
        if (e->root == (u32)root) {
            if (e->serial != r->serial || e->handle != W(root, S_ROOT_HANDLE)) refill(e, root, r->serial);
            return e;
        }
        if (!e->root && !free_) free_ = e;
    }
    Root *e = free_ ? free_ : &ROOTS[r->victim++ & (SA_ANCACHE_N - 1u)];
    e->root = 0;
    refill(e, root, r->serial);
    return e;
}

static AnOsc *state(void *osc) { return (AnOsc *)((u8 *)osc + S_OSC_KERNEL); }
static void reset(AnOsc *g) {
    g->magic = AN_MAGIC; g->phase = 0.f; g->delayed = 0.f;
    for (u32 i = 0; i < AN_EVENTS; i++) g->events[i] = (u8)AN_NO_EVENT;
    g->note = NO_NOTE; g->glide = 0.f; g->sub_applied = 0.f; g->sub_written = 0.f;
}
static void forget(AnOsc *g) { for (u32 i = 0; i < sizeof(AnOsc) / 4u; i++) ((volatile u32 *)g)[i] = 0; }

/* The glide offset on the sub oscillator (want = the offset that should be in it now). Stock rewrites sub +0x04 at
 * every voice note start (0x0807C7F8..C80C, vstr s16, [osc, #4]): if the field is not what we left there, stock's
 * new value is the base and nothing of ours is in it any more (review 2026-11-08: a stuck detune after a note
 * that came mid-glide). */
static void sub_follow(AnOsc *g, u8 *v, float want) {
    float cur = F(v + S_VOICE_SUB, SUB_DETUNE);
    if (g->sub_applied != 0.f && cur != g->sub_written) g->sub_applied = 0.f;
    float d = want - g->sub_applied;
    if (d != 0.f) {
        cur += d;
        F(v + S_VOICE_SUB, SUB_DETUNE) = cur;
        g->sub_applied = want; g->sub_written = cur;
    }
}

/* A1, per voice before stock's voice render (r0 root, r1 voice). */
extern "C" __attribute__((used)) void an_enter(void *root, void *voice) {
    u32 c0 = CYCCNT;
    if (!root || !voice || !area_ok()) return;
    Run *r = RUN;
    u32 idx = W(S_LOAD_RING, S_LOAD_RING_IDX);
    if (idx != r->idx) {                                      /* a new audio block */
        finish(r);
        r->idx = idx;
        r->serial = m4_serial();
    }
    Root *e = lookup(r, root);
    u8 *v = (u8 *)voice;
    AnOsc *g = state(v + S_VOICE_WT1);
    r->voice = (u32)voice; r->mode = e->mode; r->root = e;
    if (!(e->mode & AN_ON)) {
        /* Not (or no longer) Analog: give the sub oscillator back what our glide added to it, and leave the native
         * kernel's history zeros instead of our phase and 0xFF events (review note N1), once: native code that ran
         * since has overwritten our magic. */
        if (g->magic == AN_MAGIC) {
            sub_follow(g, v, 0.f);
            forget(g);
            AnOsc *g2 = state(v + S_VOICE_WT2);
            if (g2->magic == AN_MAGIC) forget(g2);
        }
        r->cyc += CYCCNT - c0;
        return;
    }
    r->voices++;
    if (g->magic != AN_MAGIC) reset(g);
    AnOsc *g2 = state(v + S_VOICE_WT2);
    if (g2->magic != AN_MAGIC) reset(g2);
    u32 note = W(v + S_VOICE_WT1, OSC_NOTE);
    if (note != g->note) {                                    /* a new note: slide from the track's last one */
        u32 last = e->last_note != NO_NOTE ? e->last_note : note;
        g->glide = e->glide ? (float)((int)last - (int)note) * (1.f / 12.f) : 0.f;
        g->note = note; e->last_note = note;
    }
    if (!e->glide || !(g->glide == g->glide)) g->glide = 0.f;
    sub_follow(g, v, g->glide);
    r->cyc += CYCCNT - c0;
}

typedef float (*param_fn)(void *);
static float glide_coef(Root *e, u32 n) {
    /* 0..1000 = 0..2 s to a thousandth of the interval: coef = 0.001 ^ (n / (s * 48000)) = 2 ^ (-9.9658 n / (s * 48000)) */
    u32 key = (e->glide << 16) | n;
#if AN_E5
    if (e->coef_key == key) return e->coef;
#endif
    float s = (float)e->glide * .002f;
    float c = FN(S_EXP2, float (*)(float))(-9.965784f * (float)n / (s * 48000.f));
    e->coef = c; e->coef_key = key;
    return c;
}

/* A2, the oscillator render (r0 osc, r1 scratch buffer, r2 output buffer): 1 = rendered (or silent). */
extern "C" __attribute__((used)) int an_osc(void *osc, void *scratch, void *out) {
    if (!area_ok()) return 0;                                 /* not the header's word for it: see area_ok */
    Run *r = RUN;
    u8 *v = (u8 *)r->voice;
    if (!v || !(r->mode & AN_ON)) return 0;
    u32 which = (u8 *)osc == v + S_VOICE_WT1 ? 0u : (u8 *)osc == v + S_VOICE_WT2 ? 1u : 2u;
    if (which > 1u) return 0;
    u32 c0 = CYCCNT;
    Root *e = r->root;
    u32 n = FN(S_BUF_FRAMES, u32 (*)(void *))(out);
    param_fn param = FN(S_PARAM_VALUE, param_fn);
    float oct = (float)(int)W(osc, OSC_DETUNE_MILLI) / 1000.f / 12.f + F(osc, OSC_OCTAVES);
    oct += param((u8 *)osc + OSC_PITCH_PARAM);
    u32 note = W(osc, OSC_NOTE);
    oct += FN(S_NOTE_OCT, float (*)(void *))(&note);
    AnOsc *g = state(v + S_VOICE_WT1);
    oct += g->glide;
    float ratio = FN(S_OCT_RATIO, float (*)(float))(oct);
    float rate = ratio / 23.4375f / F(osc, OSC_RATE_DIV);
    int position = (int)(param((u8 *)osc + OSC_POSITION_PARAM) * 255.f);
    position = position < 0 ? 0 : position > 255 ? 255 : position;
    float level = FN(S_LEVEL_CURVE, float (*)(float))(param((u8 *)osc + OSC_LEVEL_PARAM));
    FN(S_SMOOTH_SET, void (*)(void *, float))((u8 *)osc + OSC_SMOOTH, level);
    F(osc, OSC_RATE) = rate;
    W(osc, OSC_POSITION) = (u32)position;
    int silent = level == 0.f && !W(osc, OSC_MOVING);
    int sync = (r->mode & AN_SYNC) != 0;
    if (sync && n > AN_EVENTS) { sync = 0; STATS->sync_skipped++; }
    if (n > STATS->n_max) STATS->n_max = n;
    if (which == 0 && g->glide != 0.f && n <= 1024u) {         /* glide decays once per block, in WT1 */
        g->glide *= e->glide ? glide_coef(e, n) : 0.f;
        if (g->glide < .0005f && g->glide > -.0005f) g->glide = 0.f;
    }
    r->oscs++;
    AnOsc *self = state(osc), *slave = state(v + S_VOICE_WT2);
    int master = which == 0 && sync;
    if ((silent && !master) || n > 1024u) { r->cyc += CYCCNT - c0; return 1; }
    float inc = rate * (1.f / 2048.f);                        /* a 2048-sample cycle at the native rate */
#if AN_E3
    if (silent) {                                             /* a silent master: only the wraps for WT2 */
        an_track(self, inc, slave->events, n);
        r->cyc += CYCCNT - c0;
        return 1;
    }
#endif
    float *s = FN(S_SCRATCH_PTR, float *(*)(void *))(scratch);
    FN(S_SCRATCH_SIZE, void (*)(void *, u32))(scratch, n);
    an_render(self, inc, (float)position * (1.f / 255.f), s, n, slave->events,
              master ? AN_MASTER : which == 1 && sync ? AN_SLAVE : AN_FREE);
    if (!silent) {
        FN(S_SMOOTH_APPLY, void (*)(void *, float *, u32, u32))((u8 *)osc + OSC_SMOOTH, s, 0, n);
        FN(S_MIX_INTO, void (*)(void *, void *))(out, scratch);
    }
    r->cyc += CYCCNT - c0;
    return 1;
}

/* Our own memset / memcpy, in case the compiler emits a call to them (no libc in this ELF). */
extern "C" void *memset(void *d, int c, unsigned n) {
    u8 *p = (u8 *)d;
    while (n--) *p++ = (u8)c;
    return d;
}
extern "C" void *memcpy(void *d, const void *s, unsigned n) {
    u8 *p = (u8 *)d;
    const u8 *q = (const u8 *)s;
    while (n--) *p++ = *q++;
    return d;
}
