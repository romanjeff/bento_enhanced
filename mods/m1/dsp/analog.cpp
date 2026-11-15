/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Analog oscillators on Wavetable tracks, M7 side (S6a; per oscillator since BE4.40, docs/plan-native-props.md).
 *
 * A port of bento-playground's A1 hooks (eu4_work/drums/kick.cpp kick_analog_enter / kick_analog_osc at
 * 9fc7787, GPL-3.0-or-later), rewritten for our layout and with the plan's fixes:
 *   - BE4.40: which oscillators are Analog, and their Shape, Sync and Glide, are stock properties of the oscillator objects
 *     on the M4 (WT1 / WT2: each its own object). Stock forwards every edit, load, preset and paste to the M7 as a
 *     set_property message with the oscillator's index; the root sends it to every voice's oscillator[index] through the
 *     class's slot 8, where an_prop (N3) keeps it in the oscillator itself (AnProps, analog.h). No table of the M4's, no
 *     handles, no serial: the BE4.21..BE4.31 per-root handle cache is gone. Values are clamped here (stock never checks a
 *     range: a project file is read raw), and Type is Analog only when it is exactly 1;
 *   - glide's "previous note" is per track (a per-root line, refilled when root+0x0C changes: stock reuses roots);
 *   - the glide offset on the sub oscillator follows stock's own rewrites of that field and is taken back when WT1 stops
 *     being Analog;
 *   - sync covers AN_EVENTS frames: a longer block renders free-running and is counted.
 * Three vtable words point at build.py's trampolines (in this window, before this ELF):
 *   A1  Wavetable root slot 18 (voice render): an_enter(root, voice), then stock's render
 *   A2  Wavetable oscillator slot 0 (render): an_osc(osc, scratch, out) != 0 means handled, else stock
 *   N3  Wavetable oscillator slot 8 (set property): an_prop(osc, msg) != 0 means ours, else stock's (keys 0x03, 0xC4..0xCC)
 * Pitch, level, smoothing and mixing are stock's own helpers in stock's order (the oscillator render at
 * S_WT_OSC_RENDER, which is what this replaces for an Analog oscillator): only the wavetable read and its "table loaded"
 * gate are ours. A Wavetable oscillator, also next to an Analog one in the same voice, is stock's render untouched. The sub
 * oscillator (a different class) stays native.
 * Hard-float ELF; entry points take integers and pointers only. No libm: the glide coefficient is stock's exp2 (S_EXP2:
 * 2^x from a table over +-10 octaves; S_OCT_RATIO is the same table times C1's 32.703 Hz, the pitch), cached per root
 * while the glide time and block length stay (E5).
 * Measurement: DWT cycles in both render hooks per audio block (a block = a change of stock's LOAD ring index), into the
 * stats lines the M4 prints at save time (m4_synth.c an_print). */
#include "analog.h"
#include "../synth_shared.h"

typedef unsigned u32;
typedef unsigned short u16;
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
#define OSC_POSITION_PARAM 0x30u   /* Position 0..1 (the wavetable frame; an Analog oscillator plays Shape instead) */
#define OSC_LEVEL_PARAM 0xB0u
#define OSC_SMOOTH 0xF8u           /* level smoother */
#define OSC_MOVING 0x104u          /* smoother still moving: not silent at level 0 */
#define OSC_RATE_DIV 0x108u
#define OSC_POSITION 0x20u         /* written: the integer position, as stock */
#define OSC_RATE 0x1E8u            /* written: the playback rate, as stock */
#define SUB_DETUNE 0x04u           /* the sub oscillator's octave offset, reread by its render each block */
#define VOICE_OSC1 0x178u          /* the voice's oscillator pointers (stock's root ctor: WT1, WT2, sub; rc33 p0/probe3.py) */
#define VOICE_OSC2 0x17Cu
#define MSG_KEY 0x0Cu              /* a set_property message (SET_PROPERTY's layout): u16 key, u32 value */
#define MSG_VALUE 0x10u
#define NO_NOTE 0xFFFFFFFFu

struct Root {                      /* per Wavetable root, M7 private: the glide's memory */
    u32 root, handle, last_note;   /* last_note NO_NOTE: none yet; handle: root+0x0C when filled */
    u32 coef_key;
    float coef;
    u32 r5, r6, r7;
};
static_assert(sizeof(Root) == 32, "one line per root");
struct Run {                       /* the hooks' running state, M7 private (two lines) */
    u32 idx, r1, voice, r3;
    Root *root;
    u32 cyc, oscs, voices;
    u32 victim, r9, r10, r11, r12, r13, r14, r15;
};
static_assert(sizeof(Run) == 64, "two lines");
static_assert(sizeof(SaStats) == 64 && sizeof(SaM7) == 32 && sizeof(SaM4) == 32, "line layout");
#define RUN ((Run *)SA(SA_RUN))
static_assert(S_PROBE_SCRATCH + 0x100u <= SYNTH_AREA, "the M7's price-check scratch stays below the synth area");
#define ROOTS ((Root *)SA(SA_ANCACHE))
#define M7HDR ((volatile SaM7 *)SA(SA_M7))
#define STATS ((volatile SaStats *)SA(SA_STATS))
static_assert(SA_RUN + sizeof(Run) <= SA_ANCACHE && SA_ANCACHE + SA_ANCACHE_N * sizeof(Root) <= SA_UI, "layout");

static inline void dsb() { __asm volatile("dsb" ::: "memory"); }
static inline void clean(u32 a, u32 n) {
    dsb();
    for (u32 x = a & ~31u; x < a + n; x += 32u) *(volatile u32 *)SA_DCCMVAC = x;
    dsb();
}

/* The area is ours: the M7 took the reservation this boot (m7_polyat.c synth_take). The render hooks test it: a reset can
 * leave the header and the run state in SDRAM while the pool is stock's again (review 2026-11-08). Two loads of one
 * cached line; read while stock's allocator is between its two counter writes it says "not ours" for that call, and an
 * Analog oscillator is then silent for one block (its state, kept in the oscillator, is not touched). */
static inline int area_ok() {
    return M7HDR->magic == SA_M7_MAGIC && POOL_CUR + POOL_LEFT == SYNTH_AREA;
}

/* ---- an oscillator's state and settings (analog.h) ---------------------------------------------------------------- */
static AnOsc *state(void *osc) { return (AnOsc *)((u8 *)osc + S_OSC_KERNEL); }
static AnProps *props(void *osc) { return (AnProps *)((u8 *)osc + S_OSC_KERNEL + AN_PROPS_OFF); }
static int is_analog(void *osc) { return state(osc)->magic == AN_MAGIC && props(osc)->type == 1u; }
static void reset(AnOsc *g) {
    g->magic = AN_MAGIC; g->phase = 0.f; g->delayed = 0.f;
    for (u32 i = 0; i < AN_EVENTS; i++) g->events[i] = (u8)AN_NO_EVENT;
    g->note = NO_NOTE; g->glide = 0.f; g->sub_applied = 0.f; g->sub_written = 0.f;
}
/* the whole kernel area to zero: stock's render then starts from a zero history (review note N1) */
static void forget(void *osc) {
    volatile u32 *a = (volatile u32 *)state(osc);
    for (u32 i = 0; i < AN_AREA / 4u; i++) a[i] = 0;
}
/* The voice an oscillator belongs to, and which of its two it is: stock's root ctor keeps WT1 / WT2's pointers in the
 * voice (+0x178 / +0x17C), so the oscillator finds its voice without any state of ours. 0 if neither matches. */
static u8 *voice_of(void *osc, u32 *which) {
    u8 *v1 = (u8 *)osc - S_VOICE_WT1, *v2 = (u8 *)osc - S_VOICE_WT2;
    if (W(v1, VOICE_OSC1) == (u32)osc) { *which = 0; return v1; }
    if (W(v2, VOICE_OSC2) == (u32)osc) { *which = 1; return v2; }
    return 0;
}

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

/* N3, the oscillator's set property (r0 osc, r1 the message): 1 = one of our keys (stock's own slot 8 has no case for
 * them and would only return), 0 = stock's. Type 1 makes the oscillator Analog (a fresh state, default settings: the push
 * and the M4's resend bring the real ones after it); any other Type ends it now, before stock's render can read our state
 * as its history (WT1 gives the sub oscillator its glide offset back first). Shape, Sync and Glide are kept only while
 * the oscillator is Analog (the area is stock's history otherwise), clamped to their ranges. Runs in the M7's synth
 * message loop, between renders. */
extern "C" __attribute__((used)) int an_prop(void *osc, void *msg) {
    u32 key = *(volatile u16 *)((u8 *)msg + MSG_KEY);
    int v = (int)W(msg, MSG_VALUE);
    if (key != S_KEY_OSC_TYPE && key != S_KEY_OSC_SHAPE && key != S_KEY_OSC_SYNC && key != S_KEY_OSC_GLIDE) return 0;
    AnOsc *g = state(osc);
    AnProps *p = props(osc);
    if (key == S_KEY_OSC_TYPE) {
        if (v == 1) {
            if (!is_analog(osc)) {
                forget(osc); reset(g);
                p->type = 1; p->shape = (u8)AN_SHAPE_DEFAULT;
            }
        } else if (g->magic == AN_MAGIC) {
            u32 which = 2;
            u8 *vo = voice_of(osc, &which);
            if (vo && which == 0) sub_follow(g, vo, 0.f);
            forget(osc);
        }
    } else if (is_analog(osc)) {
        if (key == S_KEY_OSC_SHAPE) p->shape = (u8)(v < 0 ? 0 : v > (int)AN_SHAPE_MAX ? AN_SHAPE_MAX : (u32)v);
        else if (key == S_KEY_OSC_SYNC) p->sync = v == 1 ? 1u : 0u;
        else p->glide = (u16)(v < 0 ? 0 : v > (int)AN_GLIDE_MAX ? AN_GLIDE_MAX : (u32)v);
    }
    if (area_ok()) STATS->props++;
    return 1;
}

/* Shape 0..127 to the kernel's shape (0 saw, 0.5 square, 1 an 8 % pulse). The kernel has no sine / triangle yet (the
 * Shape sprint adds them, plan-analog-fm 4.5): until then 0..48 plays the saw; 48..88 morphs saw to square, 88..127
 * narrows the square to the pulse (the anchors of plan-analog-fm "Shape control"). */
static float an_shape(u32 s) {
    if (s <= AN_SHAPE_SAW_END) return 0.f;
    if (s <= AN_SHAPE_SQUARE) return (float)(s - AN_SHAPE_SAW_END) * (0.5f / (float)(AN_SHAPE_SQUARE - AN_SHAPE_SAW_END));
    if (s > AN_SHAPE_MAX) s = AN_SHAPE_MAX;
    return 0.5f + (float)(s - AN_SHAPE_SQUARE) * (0.5f / (float)(AN_SHAPE_MAX - AN_SHAPE_SQUARE));
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

/* The root's glide line. Stock reuses roots: freeing a track sets the root's key to 0xFFFFFF00 and a new track takes the
 * first free root with its own handle (0x08059C28.., 0x08059C5E..), so a line is the root's only while root+0x0C is still
 * the handle it was filled for (then the track's last note starts again). */
static Root *lookup(Run *r, void *root) {
    Root *free_ = 0;
    u32 handle = W(root, S_ROOT_HANDLE);
    for (u32 i = 0; i < SA_ANCACHE_N; i++) {
        Root *e = &ROOTS[i];
        if (e->root == (u32)root) {
            if (e->handle != handle) { e->handle = handle; e->last_note = NO_NOTE; e->coef_key = 0; STATS->misses++; }
            return e;
        }
        if (!e->root && !free_) free_ = e;
    }
    Root *e = free_ ? free_ : &ROOTS[r->victim++ & (SA_ANCACHE_N - 1u)];
    e->root = (u32)root; e->handle = handle; e->last_note = NO_NOTE; e->coef_key = 0;
    STATS->misses++;
    return e;
}

/* A1, per voice before stock's voice render (r0 root, r1 voice). The voice's glide lives in WT1's state, so it acts only
 * while WT1 is Analog (on every Analog oscillator of the voice and the sub, as before; NP4: no glide for a Wavetable OSC1). */
extern "C" __attribute__((used)) void an_enter(void *root, void *voice) {
    u32 c0 = CYCCNT;
    if (!root || !voice || !area_ok()) return;
    Run *r = RUN;
    u32 idx = W(S_LOAD_RING, S_LOAD_RING_IDX);
    if (idx != r->idx) {                                      /* a new audio block */
        finish(r);
        r->idx = idx;
    }
    u8 *v = (u8 *)voice;
    void *o1 = v + S_VOICE_WT1, *o2 = v + S_VOICE_WT2;
    int a1 = is_analog(o1), a2 = is_analog(o2);
    r->voice = (u32)voice; r->root = 0;
    if (!a1 && !a2) { r->cyc += CYCCNT - c0; return; }
    r->voices++;
    if (a1) {
        Root *e = lookup(r, root);
        r->root = e;
        AnOsc *g = state(o1);
        u32 glide = props(o1)->glide;
        u32 note = W(o1, OSC_NOTE);
        if (note != g->note) {                                /* a new note: slide from the track's last one */
            u32 last = e->last_note != NO_NOTE ? e->last_note : note;
            g->glide = glide ? (float)((int)last - (int)note) * (1.f / 12.f) : 0.f;
            g->note = note; e->last_note = note;
        }
        if (!glide || !(g->glide == g->glide)) g->glide = 0.f;
        sub_follow(g, v, g->glide);
    }
    r->cyc += CYCCNT - c0;
}

typedef float (*param_fn)(void *);
static float glide_coef(Root *e, u32 glide, u32 n) {
    /* 0..1000 = 0..2 s to a thousandth of the interval: coef = 0.001 ^ (n / (s * 48000)) = 2 ^ (-9.9658 n / (s * 48000)) */
    u32 key = (glide << 16) | n;
#if AN_E5
    if (e && e->coef_key == key) return e->coef;
#endif
    float s = (float)glide * .002f;
    float c = FN(S_EXP2, float (*)(float))(-9.965784f * (float)n / (s * 48000.f));
    if (e) { e->coef = c; e->coef_key = key; }
    return c;
}

/* A2, the oscillator render (r0 osc, r1 scratch buffer, r2 output buffer): 1 = rendered (or silent), 0 = stock's render
 * (a Wavetable oscillator: its state is stock's, untouched). */
extern "C" __attribute__((used)) int an_osc(void *osc, void *scratch, void *out) {
    AnOsc *self = state(osc);
    if (self->magic != AN_MAGIC) return 0;                    /* a Wavetable oscillator */
    if (props(osc)->type != 1u) { forget(osc); return 0; }    /* (an_prop ends Analog at once; this is the guard) */
    if (!area_ok()) return 1;                                 /* not the header's word for it: silent, state kept */
    u32 which = 2;
    u8 *v = voice_of(osc, &which);
    if (!v) return 1;
    Run *r = RUN;
    u32 c0 = CYCCNT;
    void *o1 = v + S_VOICE_WT1, *o2 = v + S_VOICE_WT2;
    int a1 = is_analog(o1), a2 = is_analog(o2);
    u32 n = FN(S_BUF_FRAMES, u32 (*)(void *))(out);
    param_fn param = FN(S_PARAM_VALUE, param_fn);
    float oct = (float)(int)W(osc, OSC_DETUNE_MILLI) / 1000.f / 12.f + F(osc, OSC_OCTAVES);
    oct += param((u8 *)osc + OSC_PITCH_PARAM);
    u32 note = W(osc, OSC_NOTE);
    oct += FN(S_NOTE_OCT, float (*)(void *))(&note);
    AnOsc *g = state(o1);                                     /* the voice's glide: WT1's (only while it is Analog) */
    if (a1) oct += g->glide;
    float ratio = FN(S_OCT_RATIO, float (*)(float))(oct);
    float rate = ratio / 23.4375f / F(osc, OSC_RATE_DIV);
    int position = (int)(param((u8 *)osc + OSC_POSITION_PARAM) * 255.f);
    position = position < 0 ? 0 : position > 255 ? 255 : position;
    float level = FN(S_LEVEL_CURVE, float (*)(float))(param((u8 *)osc + OSC_LEVEL_PARAM));
    FN(S_SMOOTH_SET, void (*)(void *, float))((u8 *)osc + OSC_SMOOTH, level);
    F(osc, OSC_RATE) = rate;
    W(osc, OSC_POSITION) = (u32)position;
    int silent = level == 0.f && !W(osc, OSC_MOVING);
    int sync = a1 && a2 && props(o2)->sync == 1u;             /* hard sync: both Analog (WT1 the master) */
    if (sync && n > AN_EVENTS) { sync = 0; STATS->sync_skipped++; }
    if (n > STATS->n_max) STATS->n_max = n;
    u32 glide = props(o1)->glide;
    if (which == 0 && g->glide != 0.f && n <= 1024u) {        /* glide decays once per block, in WT1 */
        g->glide *= glide ? glide_coef(r->voice == (u32)v ? r->root : 0, glide, n) : 0.f;
        if (g->glide < .0005f && g->glide > -.0005f) g->glide = 0.f;
    }
    r->oscs++;
    AnOsc *slave = state(o2);
    int master = which == 0 && sync;
    /* S6c review S3: SCRATCH_SIZE sets the size only when the buffer holds n, and never grows it, so a larger block
     * than the scratch buffer is rendered as silence rather than written past it (stock's are 128 frames) */
    if ((silent && !master) || n > 1024u || n > W(scratch, S_BUF_CAPACITY)) { r->cyc += CYCCNT - c0; return 1; }
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
    an_render(self, inc, an_shape(props(osc)->shape), s, n, slave->events,
              master ? AN_MASTER : which == 1 && sync ? AN_SLAVE : AN_FREE);
    if (!silent) {
        FN(S_SMOOTH_APPLY, void (*)(void *, float *, u32, u32))((u8 *)osc + OSC_SMOOTH, s, 0, n);
        FN(S_MIX_INTO, void (*)(void *, void *))(out, scratch);
    }
    r->cyc += CYCCNT - c0;
    return 1;
}

/* memset / memcpy (the compiler may emit calls to them): dsp_lib.cpp, shared with the drums (S6c). */
