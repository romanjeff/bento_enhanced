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
 *   - glide's "previous note" is per track (a per-root line, refilled when root+0x0C changes: stock reuses roots); its time
 *     counts the oscillator's own rate (P3 Q0: the divider at osc+0x108, 2 at the device's 2N; BE4.40 glided twice as fast);
 *   - the glide offset on the sub oscillator follows stock's own rewrites of that field and is taken back when WT1 stops
 *     being Analog;
 *   - hard sync at the device's block (BE4.40 B1, docs/plan-be440-sync-identity.md): stock renders Wavetable oscillators at
 *     2N = 256 frames, so sync runs in chunks of AN_EVENTS frames (both Analog: up to AN_SYNC_FRAMES, the master's wraps in
 *     WT2's events then WT1's; a longer block renders free-running and is counted);
 *   - mixed sync (Track S, J1): a Wavetable WT1 is the master of an Analog WT2 with Sync on. WT2 keeps its own exact copy of
 *     WT1's phase (stock's own pitch formula on WT1's fields, restarted by WT1's note start through slot 2), seeded from
 *     stock's phase when sync engages mid-note or stock's kernel runs again after a freeze; stock's WT1 is never written
 *     (PARAM_VALUE's cache store is undone, wt1_oct);
 *   - Shape modulation (Track M, J4): on an Analog oscillator the sources routed to its Position parameter move Shape,
 *     ramped over the block (a step every AN_RAMP frames, each with its correction: an_reshape);
 *   - P3 Q2 Bit Depth (docs/plan-p3-fm-bits.md 4.7, OD11): Full / 12 / 8 / 4 bits per oscillator, a two's-complement
 *     pass on the 96 kHz block before Level (an_crush, part by part about the kernel's dc: Q2b, OD12), counted in
 *     SA_FMSTATS (bits=);
 *   - P3 Q4 Sub FM's modulator (plan 4.2, OD10): the sub oscillator's block, copied once per voice-block into buffer A
 *     (SA_FMBUF_A) by the first Analog oscillator with Sub FM, from the sub's state before its own render (stock's formulas,
 *     its cache words put back: sub_fill). Q4: nothing plays it yet.
 * Four vtable words point at build.py's trampolines (in this window, before this ELF):
 *   A1  Wavetable root slot 18 (voice render): an_enter(root, voice), then stock's render
 *   A2  Wavetable oscillator slot 0 (render): an_osc(osc, scratch, out) != 0 means handled, else stock
 *   N3  Wavetable oscillator slot 8 (set property): an_prop(osc, msg) != 0 means ours, else stock's (keys 0x03, 0xC4..0xCC)
 *   S1  Wavetable oscillator slot 2 (note start): an_note(osc), then stock's (BE4.40 mixed sync)
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
/* BE4.40 mixed sync: stock's Wavetable oscillator, read only (S; wf/sync design, measured kstate.py / probe_param.py) */
#define OSC_RESET_FLAG 0x1EDu      /* WTOscReset (key 0xC6, WT_OSC_PROPERTY 0x08081AA8 strb (value != 0)); note start passes it
                                    * to the kernel reset 0x080A1FC0, which zeroes the index only when it is set; ctor 1 */
#define OSC_KFRAC 0x1CCu           /* the kernel's phase: f32 fraction (kernel +0xC0), u32 index (+0xC4) and table length */
#define OSC_KIDX 0x1D0u            /* (+0xD4, 2048 / D at mip D): stock's phase is exactly (idx + frac) / len (M, kstate.py: */
#define OSC_KLEN 0x1E0u            /* spread 0 at D 4 / 16 / 64, with vibrato); set by the note start and the mip select */
/* BE4.40 Shape modulation: stock's modulated parameter (PARAM_VALUE 0x080A09A0: value = clamp(sum over the slots in the
 * order bytes of amount x source+4, min, max)); slot 0 is the parameter's own value (the knob: the osc ctor's source at
 * osc+0x28 for Position), slots 1..4 the routed sources (PARAM_CONNECT 0x080A0920: (param, source, r2 0..3 -> slot r2 + 1,
 * amount s0), which rebuilds the order; the osc class's slot 10 0x08081B60 routes key 0xC9 to Position, osc+0x30) (S) */
#define PARAM_ORDER 0x28u
#define PARAM_COUNT 0x30u
#define PARAM_CACHE 0x3Cu          /* PARAM_VALUE stores its result here (0x080A0A52 vstr s0, [r0, #0x3c]) (S) */
#define SUB_DETUNE 0x04u           /* the sub oscillator's octave offset, reread by its render each block */
/* P3 Q4: the sub oscillator (voice +0x4C, its own class; its render 0x080A0DE0, read by hand) as its render reads it */
#define SUB_FINE_MILLI 0x0Cu       /* int, 1/1000 semitone (its key 3) */
#define SUB_NOTE 0x10u
#define SUB_PITCH_PARAM 0x84u      /* modulated parameter: pitch (octaves) */
#define SUB_OCT_DIV 0xDCu          /* u32: the octaves its rate is below 48 kHz's, plus 1 (2 at the device's 2N: engine init) */
#define SUB_PW_PARAM 0xE4u         /* modulated parameter: the Square's pulse width 0..1 */
#define SUB_CORE 0x20u             /* its oscillator core: wave byte +0, sine table +4, phase +8, stride +0x10, width +0x18 */
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
    u32 victim;
    u32 fmtag;                     /* P3 (docs/plan-p3-fm-bits.md 3.3): which FM buffer holds this voice-block's signal
                                    * (AN_FMTAG_A the sub's copy, AN_FMTAG_B OSC1's); 0 at every an_enter that names a voice,
                                    * meaningful only while voice is the voice rendering. P3 Q4: A set by sub_fill */
    u32 inc_sub;                   /* P3: buffer A's sub increment x 2^-32 (float bits), for the golden checks (Q4: set) */
    u32 r11, r12, r13, r14, r15;
};
static_assert(sizeof(Run) == 64, "two lines");
#define AN_FMTAG_A 1u
#define AN_FMTAG_B 2u
static_assert(sizeof(SaStats) == 64 && sizeof(SaM7) == 32 && sizeof(SaM4) == 32, "line layout");
#define RUN ((Run *)SA(SA_RUN))
static_assert(S_PROBE_SCRATCH + 0x100u <= SYNTH_AREA, "the M7's price-check scratch stays below the synth area");
#define ROOTS ((Root *)SA(SA_ANCACHE))
#define M7HDR ((volatile SaM7 *)SA(SA_M7))
#define STATS ((volatile SaStats *)SA(SA_STATS))
#define FMSTATS ((volatile SaFmStats *)SA(SA_FMSTATS))
static_assert(SA_RUN + sizeof(Run) <= SA_ANCACHE && SA_ANCACHE + SA_ANCACHE_N * sizeof(Root) <= SA_FMBUF, "layout");
/* P3: the FM buffers end where the drum knob table starts; the FM stats line sits after the drum stats, inside what the M7
 * zeroes at its take (+0x40..+0x200, m7_polyat.c synth_take) */
static_assert(SA_FMBUF_A + 4u * AN_FM_FRAMES == SA_FMBUF_B && SA_FMBUF_B + 4u * AN_FM_FRAMES == SA_DKTAB, "FM buffers A, B");
static_assert(sizeof(SaFmStats) == 32 && SA_DSTATS + sizeof(SaDrumStats) <= SA_FMSTATS && SA_FMSTATS + sizeof(SaFmStats) <= 0x200u,
              "FM stats line");

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
    g->mphase = g->mphase_lo = 0; g->mrestart = g->mlive = 0; g->mshape = (u16)AN_NO_SHAPE; g->msnap = 0;
    g->msub = g->m12 = 0;
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
 * as its history (WT1 gives the sub oscillator its glide offset back first). Shape, Sync, Glide and (P3) Sub FM, Bit Depth
 * and FM 1>2 are kept only while the oscillator is Analog (the area is stock's history otherwise), clamped to their ranges.
 * P3 OD4: the two lists play what stock's widget shows for a value it has no label for (a hand-edited file): Sync any
 * value but 0 is On, Bit Depth past its last label (or negative) is 4, the widget's last entry (M: test_synth_ui); the type-10
 * amounts clamp to 0..1000 while the widget shows the raw value (as Glide). Runs in the M7's synth message loop, between
 * renders. */
static int ours(u32 key) {
    return key == S_KEY_OSC_TYPE || key == S_KEY_OSC_SHAPE || key == S_KEY_OSC_SYNC || key == S_KEY_OSC_GLIDE ||
           key == S_KEY_OSC_SUBFM || key == S_KEY_OSC_BITS || key == S_KEY_OSC_FM12;
}
static u16 amount(int v) { return (u16)(v < 0 ? 0 : v > (int)AN_FM_MAX ? AN_FM_MAX : (u32)v); }
extern "C" __attribute__((used)) int an_prop(void *osc, void *msg) {
    u32 key = *(volatile u16 *)((u8 *)msg + MSG_KEY);
    int v = (int)W(msg, MSG_VALUE);
    if (!ours(key)) return 0;
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
        else if (key == S_KEY_OSC_SYNC) p->sync = v != 0 ? 1u : 0u;
        else if (key == S_KEY_OSC_GLIDE) p->glide = (u16)(v < 0 ? 0 : v > (int)AN_GLIDE_MAX ? AN_GLIDE_MAX : (u32)v);
        else if (key == S_KEY_OSC_SUBFM) p->subfm = amount(v);
        else if (key == S_KEY_OSC_FM12) p->fm12 = amount(v);
        else p->bits = (u8)((u32)v <= AN_BITS_MAX ? (u32)v : AN_BITS_MAX);
    }
    if (area_ok()) STATS->props++;
    return 1;
}

/* Shape 0..127 to the kernel's shape (-1 sine, -0.5 triangle, 0 saw, 0.5 square, 1 an 8 % pulse). Above 48 BE4.40's map,
 * bit for bit: 48..88 morphs saw to square, 88..127 narrows the square to the pulse. P3 Q3 (docs/plan-p3-fm-bits.md 4.6,
 * OD6): below it the kernel's LOW regime, linear between one-sided plateaus: 0..2 the sine, 22..26 the triangle, 45..48 the
 * saw (BE4.40 played the saw on all of 0..48, so 45..127 plays as before). s is a float (Shape plus its modulation, J4); for
 * a whole s every result is exact or one rounding (s - 48, s - 88, s - 45, s - 22 are exact). s is never below 0 or NaN here
 * (the stored Shape is unsigned and an_osc clamps a modulated one to 0..127 first), so the sine for those is only the
 * fall-through, not a behaviour (Q3 review: a mutation to the saw there is equivalent). */
static float an_shape(float s) {
    if (s > (float)AN_SHAPE_SAW_END) {
        if (s <= (float)AN_SHAPE_SQUARE) return (s - (float)AN_SHAPE_SAW_END) * (0.5f / (float)(AN_SHAPE_SQUARE - AN_SHAPE_SAW_END));
        if (s > (float)AN_SHAPE_MAX) s = (float)AN_SHAPE_MAX;
        return 0.5f + (s - (float)AN_SHAPE_SQUARE) * (0.5f / (float)(AN_SHAPE_MAX - AN_SHAPE_SQUARE));
    }
    if (s >= (float)AN_SHAPE_SAW_LO) return 0.f;
    if (s > (float)AN_SHAPE_TRI_HI) return (s - (float)AN_SHAPE_SAW_LO) * (0.5f / (float)(AN_SHAPE_SAW_LO - AN_SHAPE_TRI_HI));
    if (s >= (float)AN_SHAPE_TRI_LO) return -0.5f;
    if (s > (float)AN_SHAPE_SINE_END)
        return (s - (float)AN_SHAPE_TRI_LO) * (0.5f / (float)(AN_SHAPE_TRI_LO - AN_SHAPE_SINE_END)) - 0.5f;
    return -1.f;
}

/* J4 (Track M): the modulation stock routes to a parameter, without the parameter's own value: the sum PARAM_VALUE takes over
 * slots 1..4 (slot 0, the knob, left out), unclamped. An Analog oscillator's Position knob is not on its page (Shape is), so
 * whatever it was left at must not bend the modulation's range: only the routed part counts. */
static float param_mod(void *param) {
    u32 n = W(param, PARAM_COUNT);
    float m = 0.f;
    if (n > 5u) n = 5u;
    for (u32 i = 0; i < n; i++) {
        u32 k = *(volatile u8 *)((u8 *)param + PARAM_ORDER + i);
        if (k == 0u || k > 4u) continue;
        void *src = (void *)W(param, 8u * k);
        if (src) m += F(param, 8u * k + 4u) * F(src, 4u);
    }
    return m;
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
        clean(SA(SA_FMSTATS), sizeof(SaFmStats));             /* P3: bits= (Q2), fm= / fmx= (Q5, Q6) */
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
    r->fmtag = 0;                                             /* P3: no FM buffer is this voice's yet (before any return) */
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
/* An oscillator's pitch in octaves (before our glide) and its playback rate: stock's own formula and helpers in stock's
 * order (S_WT_OSC_RENDER 0x08081C8A..0x08081CEA), so a Wavetable oscillator's rate comes out bit for bit as stock's +0x1E8
 * (M, test_analog Mixed: note x fine x octave x pitch modulation). After the sync designer's prototype (wf/sync). */
static float pitch_oct(void *osc) {
    param_fn param = FN(S_PARAM_VALUE, param_fn);
    float oct = (float)(int)W(osc, OSC_DETUNE_MILLI) / 1000.f / 12.f + F(osc, OSC_OCTAVES);
    oct += param((u8 *)osc + OSC_PITCH_PARAM);
    u32 note = W(osc, OSC_NOTE);
    return oct + FN(S_NOTE_OCT, float (*)(void *))(&note);
}
static float rate_of(void *osc, float oct) { return FN(S_OCT_RATIO, float (*)(float))(oct) / 23.4375f / F(osc, OSC_RATE_DIV); }
/* WT1's pitch, for the master: PARAM_VALUE caches its result in the parameter (+0x3C, so WT1 +0xAC). With a table, stock's
 * render wrote the same value this block already (WT1 renders first); with no WAV its render returns before its pitch code
 * and never writes it, so the store is undone: stock's WT1 stays stock's, byte for byte (M: rev_m7_2 probe.py, test_analog
 * Sync test_wt1_is_stocks_bit_for_bit_with_wt2_synced, no-WAV case). Rather than a copy of PARAM_VALUE: stock's own
 * function, so the rate stays stock's bit for bit (test_the_master_rate_is_stocks). */
static float wt1_oct(void *o1) {
    u32 keep = W(o1, OSC_PITCH_PARAM + PARAM_CACHE);
    float oct = pitch_oct(o1);
    W(o1, OSC_PITCH_PARAM + PARAM_CACHE) = keep;
    return oct;
}

/* P3 Q4 (plan 4.2): the sub oscillator's increment this block, as its render will compute it: stock's formula in stock's
 * order (0x080A0E18..0x080A0E68, then 0x08056E40: OCT_RATIO / 48000 x 2^32, vcvt.u32), from the fields an_enter already set
 * (our glide is in +0x04). At +0xDC = 2 it is per 96 kHz sample, as the carrier's inc. */
static u32 sub_inc(u8 *sub) {
    float oct = (float)(int)W(sub, SUB_FINE_MILLI) / 1000.f / 12.f;
    oct = oct + FN(S_PARAM_VALUE, param_fn)(sub + SUB_PITCH_PARAM);
    oct = oct + F(sub, SUB_DETUNE);
    oct = oct - (float)(W(sub, SUB_OCT_DIV) - 1u);
    u32 note = W(sub, SUB_NOTE);
    float hz = FN(S_OCT_RATIO, float (*)(float))(FN(S_NOTE_OCT, float (*)(void *))(&note) + oct);
    return (u32)(hz / 48000.f * 4096.f * 1048576.f);
}
/* P3 Q4 (plan 4.2, OD10): buffer A for this voice-block: the sub's block in increment units, DC-free (an_sub_copy), and its
 * increment x 2^-32 returned. PARAM_VALUE stores its result in the parameter (+0x3C): the pitch's (sub +0xC0) and the
 * Square's width (sub +0x120) are put back, so the sub reaches its render byte for byte as stock left it (its render
 * computes both again from the same inputs: test_analog SubCopy). The width as stock's 0x08056440: vcvt.u32 of pw x 2^32,
 * held to 1..0xFFFFFFFE. Out of line: an_osc's other paths stay as they were (M1). */
__attribute__((noinline)) static float sub_fill(u8 *sub, float *a, u32 n) {
    param_fn param = FN(S_PARAM_VALUE, param_fn);
    u32 keep = W(sub, SUB_PITCH_PARAM + PARAM_CACHE);
    u32 inc = sub_inc(sub);
    W(sub, SUB_PITCH_PARAM + PARAM_CACHE) = keep;
    u32 pend = 0;
    if (*(volatile u8 *)(sub + SUB_CORE) == 3u) {             /* the Square */
        keep = W(sub, SUB_PW_PARAM + PARAM_CACHE);
        pend = (u32)(param(sub + SUB_PW_PARAM) * 4294967296.f);
        W(sub, SUB_PW_PARAM + PARAM_CACHE) = keep;
        pend = pend > 0xFFFFFFFEu ? 0xFFFFFFFEu : pend < 1u ? 1u : pend;
    }
    an_sub_copy(sub + SUB_CORE, a, n, inc, pend);
    return (float)inc * 2.3283064365386963e-10f;
}

/* S1, slot 2: the oscillator's note start (osc, ?, &note), before stock's. WT1 Wavetable starting a note with WTOscReset on
 * restarts stock's phase (its kernel reset zeroes index and fraction), so an Analog WT2 synced to it restarts its copy, and
 * its slave restarts with it (mrestart); with WTOscReset off stock's phase runs on (only its fraction is zeroed), so the
 * copy is seeded from stock's phase at the next block. The same call as stock's reset, so every caller of slot 2 (stock's
 * voice note start 0x0807C7F8, a retrigger, a legato note) is covered (I). Plan fix 2: nothing unless WT2's Sync is on. */
extern "C" __attribute__((used)) void an_note(void *osc) {
    u32 which = 2;
    u8 *v = voice_of(osc, &which);
    if (!v || which != 0 || is_analog(osc)) return;
    void *o2 = v + S_VOICE_WT2;
    if (!is_analog(o2) || props(o2)->sync != 1u) return;
    AnOsc *s = state(o2);
    if (*(volatile u8 *)((u8 *)osc + OSC_RESET_FLAG)) { s->mphase = s->mphase_lo = 0; s->mrestart = 1; s->mlive = AN_LIVE_NEW; }
    else s->mlive = 0;
}
/* Mixed sync engages mid-note (Sync turned on, WT2 just Analog, WTOscReset off): the copy takes stock's own phase, read from
 * its kernel ((idx + frac) / len, exact: len is a power of two) and taken back by this block's advance (WT1 renders first,
 * 0x0809CA8E). With no WAV stock's phase does not move (its render returns early) and WT1 is silent: any phase is right. */
static void seed(AnOsc *s, void *o1, float minc, u32 n) {
    u32 len = W(o1, OSC_KLEN), idx = W(o1, OSC_KIDX);
    float frac = F(o1, OSC_KFRAC);
    s->mrestart = 0; s->mlive = AN_LIVE_NEW;
    if (len < 32u || len > 2048u || (len & (len - 1u)) || idx >= len || !(frac >= 0.f && frac < 1.f)) return;
    u32 k = 31u - (u32)__builtin_clz(len);
    unsigned long long p = ((unsigned long long)idx << (64u - k)) + ((unsigned long long)(u32)(frac * 4294967296.f) << (32u - k));
    p -= (unsigned long long)n * an_inc64(minc > .45f ? .45f : minc);
    s->mphase = (u32)(p >> 32); s->mphase_lo = (u32)p;
}
/* Review M7 (rev_m7_1 m2.py): stock's kernel stops while WT1 is at level 0 with its smoother at rest (0x08081D22..38: an
 * envelope or LFO on OSC1 Level can clamp it to 0) and while it has no WAV (0x08081C64); the copy runs on through that (the
 * classic patch, OSC1 at 0 or with no WAV, keeps its wraps), but when the kernel runs again it is at its own phase. A
 * digest of WT1's kernel phase after WT1's render, each block: unchanged = frozen; changed after frozen = running again,
 * so the copy is seeded from it. A changed digest after a note start is no resume (an_note: AN_LIVE_NEW takes a fresh one);
 * a running kernel that happens to keep its digest (a whole number of table cycles per block) only gives one extra seed,
 * which is stock's phase anyway. */
static void watch(AnOsc *s, void *o1, float minc, u32 n) {
    u32 d = W(o1, OSC_KIDX) * 0x9E3779B1u ^ W(o1, OSC_KFRAC) ^ W(o1, OSC_KLEN) << 20;
    if (s->mlive == AN_LIVE_FROZEN && d != s->msnap) seed(s, o1, minc, n);
    s->mlive = s->mlive == AN_LIVE_NEW || d != s->msnap ? AN_LIVE_RUNS : AN_LIVE_FROZEN;
    s->msnap = d;
}
static float glide_coef(Root *e, u32 glide, u32 n, float div) {
    /* 0..1000 = 0..2 s to a thousandth of the interval: coef = 0.001 ^ (n / (s * 48000 * div)) = 2 ^ (-9.9658 n / (s * 48000
     * div)). n counts the oscillator's samples and div (osc+0x108) is their rate over 48 kHz: 2 on the device (2N frames in
     * the 96 kHz domain, stock's engine init 0x0807F180), 1 in Rig(), where * 1.f is exact (P3 Q0, F0 bug 2: BE4.40 left
     * div out and glided in half the set time on the device). The key stays (glide, n): the root's vt[19] factor sets both
     * the 2N and the divider, so a root's n implies its div (I). */
    u32 key = (glide << 16) | n;
#if AN_E5
    if (e && e->coef_key == key) return e->coef;
#endif
    float s = (float)glide * .002f;
    float c = FN(S_EXP2, float (*)(float))(-9.965784f * (float)n / (s * 48000.f * div));
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
    float oct = pitch_oct(osc);
    AnOsc *g = state(o1);                                     /* the voice's glide: WT1's (only while it is Analog) */
    if (a1) oct += g->glide;
    float rate = rate_of(osc, oct);
    int position = (int)(param((u8 *)osc + OSC_POSITION_PARAM) * 255.f);
    position = position < 0 ? 0 : position > 255 ? 255 : position;
    float level = FN(S_LEVEL_CURVE, float (*)(float))(param((u8 *)osc + OSC_LEVEL_PARAM));
    FN(S_SMOOTH_SET, void (*)(void *, float))((u8 *)osc + OSC_SMOOTH, level);
    F(osc, OSC_RATE) = rate;
    W(osc, OSC_POSITION) = (u32)position;
    int silent = level == 0.f && !W(osc, OSC_MOVING);
    /* hard sync, WT1 the master: both Analog (WT1's kernel writes its wraps; up to AN_SYNC_FRAMES), or mixed (WT1 Wavetable:
     * its phase copy in WT2's state moves here, whatever n, also while WT2 is silent) */
    int sync = a1 && a2 && props(o2)->sync == 1u;
    if (sync && n > AN_SYNC_FRAMES) { sync = 0; STATS->sync_skipped++; }
    int msync = which == 1 && !a1 && props(osc)->sync == 1u;
    float minc = 0.f;
    if (msync) {
        minc = rate_of(o1, wt1_oct(o1)) * (1.f / 2048.f);
        if (!self->mlive) seed(self, o1, minc, n);
        watch(self, o1, minc, n);
    } else if (which == 1) {
        self->mlive = 0; self->mrestart = 0;                  /* the next engage seeds; no stale restart (plan fix 2) */
    }
    if (n > STATS->n_max) STATS->n_max = n;
    u32 glide = props(o1)->glide;
    if (which == 0 && g->glide != 0.f && n <= 1024u) {        /* glide decays once per block, in WT1 */
        g->glide *= glide ? glide_coef(r->voice == (u32)v ? r->root : 0, glide, n, F(osc, OSC_RATE_DIV)) : 0.f;
        if (g->glide < .0005f && g->glide > -.0005f) g->glide = 0.f;
    }
    r->oscs++;
    AnOsc *slave = state(o2);
    int master = which == 0 && sync;
    int role = master ? AN_MASTER : which == 1 && (sync || msync) ? AN_SLAVE : AN_FREE;
    /* S6c review S3: SCRATCH_SIZE sets the size only when the buffer holds n, and never grows it, so a larger block
     * than the scratch buffer is rendered as silence rather than written past it (stock's are 2 x 128 frames) */
    if ((silent && !master) || n > 1024u || n > W(scratch, S_BUF_CAPACITY)) {
        if (msync) an_master(self, minc, 0, n);               /* the master's phase moves on */
        self->mshape = (u16)AN_NO_SHAPE;                      /* heard again: from its Shape then, no ramp */
        self->msub = self->m12 = 0;                           /* (P3: and from its FM amounts) */
        r->cyc += CYCCNT - c0;
        return 1;
    }
    float inc = rate * (1.f / 2048.f);                        /* a 2048-sample cycle at the native rate */
    if (role != AN_FREE) STATS->synced++;                     /* the black box's synced= (B1: sync in chunks, below) */
    /* P3 Q4 (plan 4.1, 4.2): Sub FM's modulator in buffer A, once per voice-block: the first oscillator that wants it fills it
     * (OSC1; OSC2 when OSC1 did not: Wavetable, skipped, or no Sub FM) and tags it; valid for the whole voice-block, since
     * nothing writes the sub before it renders. Only for the voice an_enter named (the tag is that voice's). Longer blocks
     * than the buffer: no FM, counted (fmx=). Q4: nothing plays A yet (Q5). */
    if (props(osc)->subfm || self->msub) {
        if (n > AN_FM_FRAMES) FMSTATS->fm_skipped++;
        else if (r->voice == (u32)v && !(r->fmtag & AN_FMTAG_A)) {
            float f = sub_fill(v + S_VOICE_SUB, (float *)SA(SA_FMBUF_A), n);
            __builtin_memcpy(&r->inc_sub, &f, 4);
            r->fmtag |= AN_FMTAG_A;
        }
    }
#if AN_E3
    if (silent) {                                             /* a silent master: only the wraps for WT2 */
        self->mshape = (u16)AN_NO_SHAPE;
        self->msub = self->m12 = 0;
        for (u32 off = 0; off < n; off += AN_EVENTS)
            an_track(self, inc, off ? g->events : slave->events, n - off < AN_EVENTS ? n - off : AN_EVENTS);
        r->cyc += CYCCNT - c0;
        return 1;
    }
#endif
    /* J4 (Track M): the modulation routed to Position moves Shape, Position's full range (1.0, frames 0..255) onto Shape's
     * (0..127), held to 1/AN_SHAPE_Q of a step; none routed: Shape as stored, bit for bit */
    float shape = (float)props(osc)->shape;
    float pm = param_mod((u8 *)osc + OSC_POSITION_PARAM);
    if (pm != 0.f) {
        shape += pm * (float)AN_SHAPE_MAX;
        shape = !(shape > 0.f) ? 0.f : shape > (float)AN_SHAPE_MAX ? (float)AN_SHAPE_MAX : shape;
        shape = (float)(u32)(shape * (float)AN_SHAPE_Q + .5f) * (1.f / (float)AN_SHAPE_Q);
        STATS->shape_mods++;                                  /* the black box's shape_mods= */
    }
    /* Review M7 (rev_m7_1 m7.py: a step once per block, 375 Hz, a bare pulse edge at the block start): a Shape that changed
     * since the last block (modulation, or the knob) ramps there over this block, a step every AN_RAMP frames (P3 Q3:
     * AN_RAMP_LOW where either end is below the saw), each step's jump corrected (an_reshape); the last part plays the new
     * Shape exactly. Unchanged: one shape, as before. */
    u32 sq = (u32)(shape * (float)AN_SHAPE_Q), sq0 = self->mshape;
    self->mshape = (u16)sq;
    float k1 = an_shape(shape);
    int ramp = sq0 != AN_NO_SHAPE && sq0 != sq;
    float k0 = ramp ? an_shape((float)sq0 * (1.f / (float)AN_SHAPE_Q)) : k1, kp = k0;
    u32 step = ramp ? (k0 < 0.f || k1 < 0.f ? AN_RAMP_LOW : AN_RAMP) : role == AN_FREE ? n : AN_EVENTS;   /* (P3 Q3: LOW) */
    u32 parts = step ? (n + step - 1u) / step : 0u;
    float *s = FN(S_SCRATCH_PTR, float *(*)(void *))(scratch);
    FN(S_SCRATCH_SIZE, void (*)(void *, u32))(scratch, n);
    /* P3 Q2 Bit Depth (plan 4.7, OD11): each part crushed as soon as it is rendered, before Level (SMOOTH_APPLY) and in the
     * 96 kHz domain ahead of stock's FIR; Full skips it (the loops are untouched: M1). Q2b (OD12 = (b)): about the part's
     * own dc (an_dc of its k: a Shape ramp moves the pulse's DC part by part), so the DC-free pulse is crushed on the grid's
     * range and stays DC-free. A part's samples are final once rendered (an_reshape only moves `delayed`). BD1: OSC1's FM
     * 1>2 buffer B (Q6) is written from s after this loop, before Level, also when OSC1 is silent (E3 (a)). */
    u32 bits = props(osc)->bits;
    for (u32 j = 0, off = 0; j < parts; j++, off += step) {
        u32 m = n - off < step ? n - off : step, co = off % AN_EVENTS;
        u8 *ev = slave->events;                               /* (unused by AN_FREE) */
        if (role != AN_FREE) {
            /* sync chunks of AN_EVENTS frames, each with its own events: both Analog, chunk 0's wraps are in WT2's events,
             * chunk 1's in WT1's own (unused otherwise: WT1 is never a slave); mixed, WT2's, made at the chunk's start */
            ev = (msync ? self->events : off >= AN_EVENTS ? g->events : slave->events) + co;
            if (msync && !co) an_master(self, minc, self->events, n - off < AN_EVENTS ? n - off : AN_EVENTS);
        }
        float k = !ramp || j + 1u == parts ? k1 : k0 + (k1 - k0) * ((float)(j + 1u) / (float)parts);
        if (k != kp) an_reshape(self, kp, k);
        kp = k;
        an_render(self, inc, k, s + off, m, ev, role);
        if (bits) an_crush(s + off, m, bits, an_dc(k));
    }
    if (bits) FMSTATS->bits++;                                /* (one per crushed render, as Q2's post-pass counted) */
    if (!silent) {
        FN(S_SMOOTH_APPLY, void (*)(void *, float *, u32, u32))((u8 *)osc + OSC_SMOOTH, s, 0, n);
        FN(S_MIX_INTO, void (*)(void *, void *))(out, scratch);
    }
    r->cyc += CYCCNT - c0;
    return 1;
}

/* memset / memcpy (the compiler may emit calls to them): dsp_lib.cpp, shared with the drums (S6c). */
