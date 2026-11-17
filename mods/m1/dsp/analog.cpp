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
 *   - (P3 Q2 Bit Depth, BE4.41: removed in BE4.42 after device use, "not enough difference in practice": no crush, no
 *     key 0x1A4, no bits=; docs/plan-p3-fm-bits.md, BE4.42);
 *   - P3 Q4 Sub FM's modulator (plan 4.2, OD10): the sub oscillator's block, copied once per voice-block into buffer A
 *     (SA_FMBUF_A) by the first Analog oscillator with Sub FM, from the sub's state before its own render (stock's formulas,
 *     its cache words put back: sub_fill). P3 Q5: Sub FM plays it, through-zero linear FM of the oscillator's
 *     increment (an_render_fm, the index ramped over the block from msub), also for a silent sync master (E3 (b)),
 *     counted in SA_FMSTATS (fm=);
 *   - P3 Q6 FM 1>2 (plan 4.1, 4.4 E3 (a), 4.5, BD1): an Analog OSC1's block before Level, times its increment (an_feed),
 *     into buffer B (SA_FMBUF_B) while an Analog OSC2 has FM 1>2 (a silent OSC1 renders for it); OSC2
 *     adds it to its increment as a second term (I12 ramped from m12), counted as fm=, or fmx= missed where B was not
 *     written this voice-block.
 *   - BE4.43 (docs/plan-p3-fm-bits.md BE4.43, Jeff's design 2026-11-16): one FM amount per oscillator, OSC1's Sub FM and
 *     OSC2's FM 1>2 while OSC1 is Analog (else OSC2's Sub FM: OSC2's Sub FM no longer plays next to an Analog OSC1), times
 *     the oscillator's own FM envelope (FM Atk / FM Dec, fm_env_step: p3_golden fm_env's float32 law, at block rate on the
 *     index lines' two ends), retriggered at every note start (an_note, slot 2: per voice, legato included).
 *   - BE4.44 W1 (docs/plan-wt-fm.md 3.1 .. 3.3): a Wavetable oscillator keeps FM settings too (Sub FM on OSC1, FM 1>2 on OSC2,
 *     FM Atk / FM Dec), in its own area at AnOsc / AnProps's offsets behind a tag pair (wt_init, wt_prop, wt_settle), and
 *     OSC1 the feed-B bit (wt_feed); an_osc sends a tagged one to wt_fm_osc, which runs stock's render body with the saved
 *     runs put back around it (tier 2) and steps its FM envelope. W2: our table reader under FM (tier 3, wt_body, wt_play,
 *     dsp/wt_reader.cpp). W3: Sub FM's A into a Wavetable OSC1 and FM 1>2's B from an Analog OSC1 into a Wavetable OSC2
 *     (feeds), D3 (Sub FM plays as 0 while an Analog OSC2 is synced to the Wavetable OSC1), BE_WT_MIP=off (WT_MIP_OFF), the
 *     black box's wt= (SaFmStats) and our reader blocks in SaStats.
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
#include "wt_reader.h"
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
    u32 peak_b;                    /* BE4.44 W3 review: B's known peak (float bits): OSC1's increment x its Shape's peak
                                    * (an_peak), set with fmtag B by the Analog OSC1 that wrote B (wt_body's pa for B) */
    u32 r12, r13, r14, r15;
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
    g->note = (u16)AN_NO_NOTE; g->fmk = AN_FE_START; g->glide = 0.f; g->sub_applied = 0.f; g->sub_written = 0.f;
    g->mphase = g->mphase_lo = 0; g->mrestart = g->mlive = 0; g->mshape = (u16)AN_NO_SHAPE; g->msnap = 0;
    g->msub = g->m12 = 0; g->fme = 0.f;
}
/* the whole kernel area to zero: stock's render then starts from a zero history (review note N1) */
static void forget(void *osc) {
    volatile u32 *a = (volatile u32 *)state(osc);
    for (u32 i = 0; i < AN_AREA / 4u; i++) a[i] = 0;
}
/* BE4.44 W1 (docs/plan-wt-fm.md 3.2, 3.3): a Wavetable oscillator's tag pair (analog.h WT_TAG_HI), and its mip read
 * bounded (a byte out of 0..WT_MIPS - 1 is HOME: it can never index the mip table). */
static inline u32 wt_tag(const void *osc) { return WT_TAG_HI | ((u32)osc >> 4 & 0xFFFFu); }
__attribute__((noinline)) static int wt_tagged(void *osc) {
    u32 t = wt_tag(osc);
    return state(osc)->magic == t && W(state(osc), 4) == ~t;
}
static inline u32 wt_mip(const AnOsc *g) { u32 m = g->wt.mipcur; return m < WT_MIPS ? m : WT_HOME; }
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
 * as its history (WT1 gives the sub oscillator its glide offset back first). Shape, Sync, Glide and (P3) Sub FM and FM 1>2
 * are kept only while the oscillator is Analog (the area is stock's history otherwise), clamped to their ranges.
 * P3 OD4: the list plays what stock's widget shows for a value it has no label for (a hand-edited file): Sync any value but
 * 0 is On (M: test_synth_ui); the type-10 amounts clamp to 0..1000 while the widget shows the raw value (as Glide).
 * BE4.42: Bit Depth (0x1A4) is no longer ours. BE4.43: FM Atk (0x1A4 again, a new token) and FM Dec (0x1A0), the
 * oscillator's FM envelope, clamped to 0..127 / 0..128 (128 = Inf); a fresh Analog oscillator starts at Atk 0 / Dec Inf
 * (the template's defaults: env 1, BE4.42's sound) until the push brings its own. Runs in the M7's synth message loop,
 * between renders. */
static int ours(u32 key) {
    return key == S_KEY_OSC_TYPE || key == S_KEY_OSC_SHAPE || key == S_KEY_OSC_SYNC || key == S_KEY_OSC_GLIDE ||
           key == S_KEY_OSC_SUBFM || key == S_KEY_OSC_FM12 || key == S_KEY_OSC_FMATK || key == S_KEY_OSC_FMDEC;
}
static inline int env_set(u32 a, u32 d) { return (a | (d ^ AN_FM_DEC_INF)) != 0; }   /* BE4.43: AnProps.envf */
static u16 amount(int v) { return (u16)(v < 0 ? 0 : v > (int)AN_FM_MAX ? AN_FM_MAX : (u32)v); }
/* P3 Q5 (FD3), BE4.42 (deeper, Jeff's device feedback "more moderate than expected"): an FM amount 0..1000 (the knob's
 * travel) to its index, I = 16 (v / 1000)^1.5 (BE4.41: 8 (v / 1000)^2; 50 % now 5.66, was 2), in float32 as
 * I = f32(f32(v sqrt(v)) K), K = f32(16 / 1000^1.5): v exact, VSQRT correctly rounded, two roundings (p3_golden.an_index32
 * bit for bit; exact 2 at 250 and 16 at 1000, M). The square root is VSQRT inline: __builtin_sqrtf keeps a sqrtf call for
 * errno under -fno-builtin (build.py refuses libm by name). */
static inline float an_sqrt(float x) { float r; __asm__("vsqrt.f32 %0, %1" : "=t"(r) : "t"(x)); return r; }
static inline float an_index(u32 v) { float f = (float)v; return f * an_sqrt(f) * (float)(16.0 / 31622.776601683792); }
static u32 clamp_to(int v, u32 top) { return v < 0 ? 0u : v > (int)top ? top : (u32)v; }

/* BE4.44 W1 (docs/plan-wt-fm.md 3.1, 3.2 "Tagging", 3.3): a Wavetable oscillator's FM settings. While it is untagged its area
 * is stock's table history, never read as settings: the first value that is not a default writes the whole default state
 * first (AnProps at the template's defaults, fmk DEC at j 0 (env 1, held while unset: an envelope set mid-note starts from
 * 1, as on Analog), msub = m12 = 0, fme 0, mipcur HOME, wflags and wxf 0), then the tag pair, then the value. A default
 * value on an untagged oscillator writes nothing (a BE4.43 project at its defaults leaves stock's bytes alone). D2: OSC2's
 * slot 5 is FM 1>2 whatever OSC1 is, so a Wavetable OSC2's Sub FM and a Wavetable OSC1's FM 1>2 play nothing and are not
 * kept here (the M4 keeps and saves them). Untag (wt_settle): only when nothing is left to play or to finish: no amount,
 * no ramp memory, the mip home, no envelope, not feeding B. */
__attribute__((noinline, minsize)) static void wt_init(void *osc) {
    AnOsc *g = state(osc);
    AnProps *p = props(osc);
    *p = AnProps();
    p->fmdec = (u8)AN_FM_DEC_INF;
    g->fmk = (u16)(AN_FE_DEC << 13); g->msub = g->m12 = 0; g->fme = 0.f;
    g->wt.mipcur = (u8)WT_HOME; g->wt.wflags = 0; g->wxf = 0;
    u32 t = wt_tag(osc);
    g->magic = t; W(g, 4) = ~t;
}
__attribute__((noinline, minsize)) static void wt_settle(void *osc) {
    AnOsc *g = state(osc);
    AnProps *p = props(osc);
    if (!(p->subfm | p->fm12 | g->msub | g->m12 | p->envf | (g->wt.wflags & WT_FEED_B)) && wt_mip(g) == WT_HOME) g->magic = 0;
}
__attribute__((noinline, minsize)) static void wt_prop(void *osc, u32 which, u32 key, int v) {
    if (key == (which ? S_KEY_OSC_SUBFM : S_KEY_OSC_FM12)) return;
    u32 x = key == S_KEY_OSC_FMATK ? clamp_to(v, AN_FM_ATK_MAX) : key == S_KEY_OSC_FMDEC ? clamp_to(v, AN_FM_DEC_INF) : amount(v);
    if (!wt_tagged(osc)) {
        if (x == (key == S_KEY_OSC_FMDEC ? AN_FM_DEC_INF : 0u)) return;
        wt_init(osc);
    }
    AnProps *p = props(osc);
    if (key == S_KEY_OSC_SUBFM) p->subfm = (u16)x;
    else if (key == S_KEY_OSC_FM12) p->fm12 = (u16)x;
    else {
        if (key == S_KEY_OSC_FMATK) p->fmatk = (u8)x;
        else p->fmdec = (u8)x;
        p->envf = (u8)env_set(p->fmatk, p->fmdec);
    }
    wt_settle(osc);
}
/* 3.5's feed-B bit: a Wavetable OSC1 feeds B while OSC2 (Analog, or Wavetable and tagged) has FM 1>2, so the audio path never
 * asks voice_of for it. Recomputed on every Type and FM 1>2 message of either oscillator, so any message order (a project's
 * push, a resend) ends right; tagging OSC1 for it if needed. */
__attribute__((noinline, minsize)) static void wt_feed(u8 *v) {
    void *o1 = v + S_VOICE_WT1, *o2 = v + S_VOICE_WT2;
    if (is_analog(o1)) return;
    u32 want = (is_analog(o2) || wt_tagged(o2)) && props(o2)->fm12 ? WT_FEED_B : 0u;
    AnOsc *g = state(o1);
    if (!wt_tagged(o1)) {
        if (!want) return;
        wt_init(o1);
    }
    g->wt.wflags = (u8)((g->wt.wflags & ~WT_FEED_B) | want);
    wt_settle(o1);
}
extern "C" __attribute__((used)) int an_prop(void *osc, void *msg) {
    u32 key = *(volatile u16 *)((u8 *)msg + MSG_KEY);
    int v = (int)W(msg, MSG_VALUE);
    if (!ours(key)) return 0;
    AnOsc *g = state(osc);
    AnProps *p = props(osc);
    u32 which = 2;
    u8 *vo = voice_of(osc, &which);
    if (key == S_KEY_OSC_TYPE) {
        if (v == 1) {
            if (!is_analog(osc)) {
                forget(osc); reset(g);
                p->type = 1; p->shape = (u8)AN_SHAPE_DEFAULT; p->fmdec = (u8)AN_FM_DEC_INF;
            }
        } else if (g->magic == AN_MAGIC) {
            if (vo && which == 0) sub_follow(g, vo, 0.f);
            forget(osc);
        }
    } else if (is_analog(osc)) {
        if (key == S_KEY_OSC_SHAPE) p->shape = (u8)clamp_to(v, AN_SHAPE_MAX);
        else if (key == S_KEY_OSC_SYNC) p->sync = v != 0 ? 1u : 0u;
        else if (key == S_KEY_OSC_GLIDE) p->glide = (u16)clamp_to(v, AN_GLIDE_MAX);
        else if (key == S_KEY_OSC_SUBFM) p->subfm = amount(v);
        else if (key == S_KEY_OSC_FM12) p->fm12 = amount(v);
        else {
            if (key == S_KEY_OSC_FMATK) p->fmatk = (u8)clamp_to(v, AN_FM_ATK_MAX);
            else p->fmdec = (u8)clamp_to(v, AN_FM_DEC_INF);   /* (S_KEY_OSC_FMDEC) */
            p->envf = (u8)env_set(p->fmatk, p->fmdec);
        }
    } else if (vo && (key == S_KEY_OSC_SUBFM || key == S_KEY_OSC_FM12 || key == S_KEY_OSC_FMATK || key == S_KEY_OSC_FMDEC)) {
        wt_prop(osc, which, key, v);                          /* BE4.44 W1: a Wavetable oscillator's FM settings */
    }
    if (vo && (key == S_KEY_OSC_TYPE || key == S_KEY_OSC_FM12)) wt_feed(vo);
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
/* BE4.44 W3 review: the peak of an Analog oscillator's waveform at shape k (an_shape's), from the kernel's set_shape / wave:
 * the LOW regime's weights sum to 1 (1); the saw..square blend (k <= 0.5) 1; past the square the pulse narrows, its DC-free
 * high side 1 - dc = 1 + (k - 0.5) 1.68 (1.84 at k 1). Non-decreasing in k, so a block's ramp k0 -> k1 peaks at the larger.
 * The edges' two-point residuals stay between the step's ends; the corners' (LOW) are below inc / 0.75 (I): not counted.
 * (p3_golden.an_peak) */
static inline float an_peak(float k) { return k > .5f ? 1.f + (k - .5f) * 1.68f : 1.f; }

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

/* The block just finished: its figures into the stats lines (only blocks where an Analog voice ran, or since BE4.44 W3 a
 * Wavetable oscillator played our reader: r->oscs, wt_body). */
static void finish(Run *r) {
    if (r->voices | r->oscs) {                                /* (BE4.44 W3: or a Wavetable FM render, wt_body) */
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
        clean(SA(SA_FMSTATS), sizeof(SaFmStats));             /* P3: fm= (Q5), fmx= (Q4, Q6) */
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
        if ((u16)note != g->note) {                           /* a new note: slide from the track's last one (BE4.43: 16 bits) */
            u32 last = e->last_note != NO_NOTE ? e->last_note : note;
            g->glide = glide ? (float)((int)last - (int)note) * (1.f / 12.f) : 0.f;
            g->note = (u16)note; e->last_note = note;
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

/* P3 (plan 4.1 .. 4.5, 4.8): an oscillator render with an FM amount, or a ramp down still to run, set (an_osc's gate;
 * out of line, so the FM-off path is an_osc's alone: M1). 1 = it renders under FM with fi.
 * Q4: Sub FM's modulator in buffer A, once per voice-block: the first oscillator that wants it fills it (OSC1; OSC2 when OSC1
 * did not: Wavetable, skipped, or no Sub FM) and tags it; valid for the whole voice-block, since nothing writes the sub
 * before it renders. Only for the voice an_enter named (the tag is that voice's). Longer blocks than the buffers: no FM,
 * counted (fmx= skipped).
 * Q5: Sub FM plays where the oscillator has it, or a ramp down still to run (msub), and A holds this voice-block's sub
 * (longer blocks, or a render with no an_enter of its voice: plain, no FM). The index ramps linearly over the block from
 * last block's amount to this one's (FD3: I = 16 (v / 1000)^1.5 since BE4.42), the parts continuing one line; msub keeps the amount the
 * block ended on (0 when it played no FM: heard again, it ramps in from 0, OD16).
 * Q6 (4.1, 4.5): FM 1>2 the same way on OSC2 (useB: twelve, its OSC1 Analog), from buffer B where OSC1 wrote it this
 * voice-block (fmtag B); wanted in a block the buffers hold but B not written (a render with no an_enter of its voice, or
 * an OSC1 that skipped) it plays without, counted (fmx= missed); m12 is msub's twin. Both routes: a = A on Sub FM's line,
 * b = B on FM 1>2's (4.3's order); one route: a its buffer, b = a at a zero line. */
/* BE4.43 (docs/plan-p3-fm-bits.md BE4.43): the FM envelope, one oscillator, one block (p3_golden fm_env, law "f32": the spec,
 * written before this code). env multiplies the index lines' two ends (fm_setup): from I(v0) e0 at frame 0 to I(v1) e1 at
 * frame 2N, so the envelope is stepped one block ahead and consecutive blocks of a note join into one ramp; nothing per
 * sample. Stages by a block counter (AnOsc.fmk), never by a float compare: AN_FE_START (a note started: e0 = 0, or 1 for
 * Atk 0: a hard retrigger whatever the last block ended on), ATK (boundary k of n_a = AN_FM_ENV_BLOCKS[Atk]: e = f32(f32(k)
 * f32(1 / n_a)), exactly 1 at n_a), DEC (j blocks after it: 1.0, then f32(e r) a block, r = ENV_R[Dec] = f32(0.001^(1 / n_d)),
 * 0 from j = n_d: -60 dB at the time Dec names, then nothing, no denormals), END (0). Dec Inf holds 1 (and stays at j 0, so a
 * Dec turned down from Inf mid-note decays from there); Atk turned mid-attack rescales the ramp at the next block; while
 * the envelope is unset an_osc holds it at DEC j 0, e 1 (one set mid-note starts from 1: no restart of the attack). Cost: a
 * counter, a VDIV in the attack (1 / n_a, the same value the spec's precomputed step is), one VMUL in the decay; the caller
 * two VMULs on the line's ends. Returns e0 (this block's start), *end = e1. */
static const u16 ENV_BLOCKS[128] = AN_FM_ENV_BLOCKS;
static const u32 ENV_R[128] = {   /* f32(0.001^(1 / AN_FM_ENV_BLOCKS[d])), as words (test_analog FmEnv: = p3_golden's) */
    0x3A83126Fu, 0x3D0186E2u, 0x3DCCCCCDu, 0x3E361887u, 0x3E809BCCu, 0x3EBEDA51u, 0x3ED7E89Bu, 0x3EEDA63Cu,
    0x3F004DCEu, 0x3F0FF59Au, 0x3F167A2Bu, 0x3F21866Cu, 0x3F263DE0u, 0x3F2E6928u, 0x3F353BEFu, 0x3F383D52u,
    0x3F3D95E5u, 0x3F423224u, 0x3F463625u, 0x3F49BD67u, 0x3F4CDD33u, 0x3F50EE8Eu, 0x3F534DB0u, 0x3F5572C0u,
    0x3F584E6Cu, 0x3F5ACE32u, 0x3F5C4DD9u, 0x3F5E56C3u, 0x3F602794u, 0x3F61C8E2u, 0x3F634199u, 0x3F650259u,
    0x3F663098u, 0x3F679CC3u, 0x3F68949Cu, 0x3F69C205u, 0x3F6AD298u, 0x3F6C04B5u, 0x3F6CE1CCu, 0x3F6DDCACu,
    0x3F6E9398u, 0x3F6F64EFu, 0x3F702383u, 0x3F70F2CFu, 0x3F71900Du, 0x3F723C99u, 0x3F72D9C2u, 0x3F73697Fu,
    0x3F74026Bu, 0x3F748D7Cu, 0x3F750C80u, 0x3F7580F6u, 0x3F75EC23u, 0x3F765C9Cu, 0x3F76CFF8u, 0x3F772DD2u,
    0x3F778ED5u, 0x3F77E7DBu, 0x3F78428Bu, 0x3F7895A0u, 0x3F78E206u, 0x3F792F43u, 0x3F797637u, 0x3F79BD51u,
    0x3F79FEA0u, 0x3F7A3FA0u, 0x3F7A7B5Au, 0x3F7AB67Bu, 0x3F7AECDBu, 0x3F7B2278u, 0x3F7B56FCu, 0x3F7B8741u,
    0x3F7BB3C4u, 0x3F7BE1E1u, 0x3F7C0C48u, 0x3F7C357Du, 0x3F7C5B79u, 0x3F7C805Fu, 0x3F7CA5BAu, 0x3F7CC817u,
    0x3F7CE931u, 0x3F7D07BDu, 0x3F7D265Fu, 0x3F7D43B7u, 0x3F7D5FCAu, 0x3F7D7A9Fu, 0x3F7D943Eu, 0x3F7DACB1u,
    0x3F7DC403u, 0x3F7DDAE8u, 0x3F7DF0A7u, 0x3F7E054Fu, 0x3F7E1975u, 0x3F7E2C8Du, 0x3F7E3F17u, 0x3F7E50A1u,
    0x3F7E619Bu, 0x3F7E7200u, 0x3F7E8179u, 0x3F7E90AEu, 0x3F7E9F00u, 0x3F7EACC2u, 0x3F7EBA2Fu, 0x3F7EC6CFu,
    0x3F7ED317u, 0x3F7EDF00u, 0x3F7EEA2Cu, 0x3F7EF525u, 0x3F7EFF93u, 0x3F7F097Du, 0x3F7F1329u, 0x3F7F1C72u,
    0x3F7F253Eu, 0x3F7F2DC7u, 0x3F7F3608u, 0x3F7F3DE9u, 0x3F7F456Eu, 0x3F7F4CACu, 0x3F7F53B4u, 0x3F7F5A62u,
    0x3F7F60D9u, 0x3F7F6708u, 0x3F7F6CF2u, 0x3F7F72AFu, 0x3F7F7827u, 0x3F7F7D71u, 0x3F7F8282u, 0x3F7F8764u,
};
__attribute__((noinline)) static void fm_env_step(AnOsc *g, const AnProps *p, float *env) {
    u32 a = p->fmatk, d = p->fmdec, st = (u32)g->fmk >> 13, k = g->fmk & 0x1FFFu;
    float e;
    if (st == AN_FE_START) { st = a ? AN_FE_ATK : AN_FE_DEC; k = 0; e = a ? 0.f : 1.f; }
    else e = st == AN_FE_DEC && k == 0 ? 1.f : g->fme;     /* (DEC j 0 is 1: also an_osc's hold while unset) */
    float e0 = e;
    if (st == AN_FE_ATK) {
        u32 na = a ? ENV_BLOCKS[a] : 0u;
        if (++k >= na) { st = AN_FE_DEC; k = 0; e = 1.f; }
        else e = (float)k * (1.f / (float)na);
    } else if (d >= AN_FM_DEC_INF) { st = AN_FE_DEC; k = 0; e = 1.f; }
    else if (st == AN_FE_DEC) {
        if (++k >= ENV_BLOCKS[d]) { st = AN_FE_END; k = 0; e = 0.f; }
        else { float rr; __builtin_memcpy(&rr, &ENV_R[d], 4); e = e * rr; }
    } else e = 0.f;
    g->fmk = (u16)(st << 13 | k); g->fme = e;
    env[0] = e0; env[1] = e;
}

/* BE4.43: env[0] / env[1] the oscillator's FM envelope over this block (fm_env_step, read only while the envelope is set;
 * 1 / 1 with FM Atk 0 / Dec Inf, the defaults: then every line is BE4.42's bit for bit). Sub FM is OSC1's, and OSC2's only while OSC1 is not Analog (twelve: OSC2 has
 * FM 1>2 instead; its Sub FM value is kept but plays nothing, its msub ramps out once). */
__attribute__((noinline)) static int fm_setup(Run *r, u8 *v, AnOsc *self, const AnProps *p, int twelve, u32 n, FmIn *fi,
                                              const float *env) {
    u32 v1 = twelve ? 0 : p->subfm, v0 = self->msub, x1 = twelve ? p->fm12 : 0, x0 = twelve ? self->m12 : 0;
    float e0 = 1.f, e1 = 1.f;
    if (p->envf) { e0 = env[0]; e1 = env[1]; }
    int mine = r->voice == (u32)v, fs = 0, fb = 0;
    if (!(v1 | v0 | x1 | x0)) ;                               /* (OSC1 feeding B only: no FM of its own) */
    else if (n > AN_FM_FRAMES) FMSTATS->fm_skipped++;
    else {
        if ((v1 || v0) && mine) {
            if (!(r->fmtag & AN_FMTAG_A)) {
                float f = sub_fill(v + S_VOICE_SUB, (float *)SA(SA_FMBUF_A), n);
                __builtin_memcpy(&r->inc_sub, &f, 4);
                r->fmtag |= AN_FMTAG_A;
            }
            fs = 1;
        }
        if (x1 || x0) {
            fb = mine && (r->fmtag & AN_FMTAG_B);
            if (!fb) FMSTATS->fm_missed++;
        }
    }
    self->msub = fs ? (u16)v1 : 0;
    self->m12 = fb ? (u16)x1 : 0;
    if (!fs && !fb) return 0;
    const float *ba = (const float *)SA(SA_FMBUF_A), *bb = (const float *)SA(SA_FMBUF_B);
    float i0 = an_index(fs ? v0 : x0) * e0, i1 = an_index(fs ? v1 : x1) * e1;
    fi->a = fs ? ba : bb; fi->ia = i0; fi->dia = i1 != i0 ? (i1 - i0) / (float)n : 0.f; fi->off = 0;
    fi->b = fi->a; fi->ib = fi->dib = 0.f;
    if (fs && fb) {
        float j0 = an_index(x0) * e0, j1 = an_index(x1) * e1;
        fi->b = bb; fi->ib = j0; fi->dib = j1 != j0 ? (j1 - j0) / (float)n : 0.f;
    }
    FMSTATS->fm++;                                            /* the black box's fm= (renders with FM) */
    return 1;
}

/* P3 Q6 (plan 4.1): OSC1 feeds buffer B while an Analog OSC2 has FM 1>2, or its ramp down still to run (m12), for the voice
 * an_enter named, in a block the buffer holds (out of line: an_osc calls it only behind its FM gate). BE4.43: and, first,
 * the oscillator's FM envelope steps (fm_env_step) while it is set (AnProps.envf, in the gate word: so it keeps time every
 * block of the note, heard or not, with or without an amount, before the silent paths return; at the defaults the FM-off
 * path pays one byte OR). Returns B to feed, or 0. */
__attribute__((noinline)) static float *feeds(Run *r, u8 *v, u32 which, int a2, void *o2, u32 n, AnOsc *self,
                                             const AnProps *p, float *env) {
    if (p->envf) fm_env_step(self, p, env);
    /* BE4.44 W3 (docs/plan-wt-fm.md 8 W3): also for a Wavetable OSC2 with FM 1>2 (tagged: its fm12 / m12 at AnProps / AnOsc's
     * offsets), a carrier of B through the reader (wt_body) */
    return which == 0 && (a2 || wt_tagged(o2)) && (props(o2)->fm12 || state(o2)->m12) && n <= AN_FM_FRAMES && r->voice == (u32)v
               ? (float *)SA(SA_FMBUF_B) : 0;
}

/* S1, slot 2: the oscillator's note start (osc, ?, &note), before stock's. WT1 Wavetable starting a note with WTOscReset on
 * restarts stock's phase (its kernel reset zeroes index and fraction), so an Analog WT2 synced to it restarts its copy, and
 * its slave restarts with it (mrestart); with WTOscReset off stock's phase runs on (only its fraction is zeroed), so the
 * copy is seeded from stock's phase at the next block. The same call as stock's reset, so every caller of slot 2 (stock's
 * voice note start 0x0807C7F8, a retrigger, a legato note) is covered (I). Plan fix 2: nothing unless WT2's Sync is on. */
extern "C" __attribute__((used)) void an_note(void *osc) {
    if (is_analog(osc)) { state(osc)->fmk = AN_FE_START; return; }   /* BE4.43: the FM envelope retriggers, per voice */
    if (wt_tagged(osc)) {                                     /* BE4.44 W1 (plan 3.1): the same retrigger, and the mip home, */
        AnOsc *g = state(osc);                                /* so the note's first block sees stock's fresh D (tier 2) */
        g->fmk = AN_FE_START; g->wt.mipcur = (u8)WT_HOME;
    }
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

template <int V> struct Tf { static constexpr int value = V; };   /* (an_osc's two part loops) */

/* BE4.44 W1 (docs/plan-wt-fm.md 3.3): a Wavetable oscillator whose magic has the tag's high half (an_osc's only extra work
 * on its non-Analog return; out of line, so an_osc's Analog paths are not touched). Tier 1, the pair not ours (a table's
 * NaN word, a check word gone): 0, stock's render, untouched. Tier 2, tagged: stock's own render body (S_WT_OSC_RENDER, not
 * the trampolined slot), with the saved runs (analog.h WT_RUN*) taken before it and put back after it: stock writes the
 * area before it reads it (plan 3.2, M), so its samples and its kernel state (+0xC0 .. +0xD8) are its own bit for bit, and
 * the settings survive its rewrites. Then the FM envelope keeps the note's time (fm_env_step while it is set; held at DEC j
 * 0 while unset, as an_osc does on Analog). W1: no reader yet, so a gate that is not 0 (an amount, ramp memory, a mip held:
 * plan 3.3) plays as gate 0. */
/* five words with one LDM and one STM (the settings run; the compiler copies it with a loop or a memcpy call otherwise) */
static inline __attribute__((always_inline)) void copy5(void *to, const void *from) {
    __asm volatile("ldm %1, {r0, r1, r2, r3, r12}\n\tstm %0, {r0, r1, r2, r3, r12}"
                   :: "r"(to), "r"(from) : "r0", "r1", "r2", "r3", "r12", "memory");
}
static_assert(WT_RUN2_W == 5, "copy5: the settings run");
/* ---- BE4.44 W2 (docs/plan-wt-fm.md 3.4): the reader, tier 3 ------------------------------------------------------------
 * Stock's kernel state, right above the area (kernel + 0xC0 .. 0xD8, kmodel.py's St): frac f32, idx, the frame playing (-1:
 * none yet), the mip's offset in the table (floats), D, len (2048 / D), the pending flag (the note start's: the mip ladder
 * runs on the next block). The reader never writes off / D / len except where stock's own ladder would (the pending flag). */
struct WtK {
    float frac;
    int idx, frame;
    u32 off, D, len;
    u8 flag;
};
#define WTK(osc) ((WtK *)((u8 *)(osc) + S_OSC_KERNEL + AN_AREA))
/* D5 (Jeff, 2026-11-16: "in between, leaning bright"): the mip under FM is the finest level k at or above the note's whose
 * peak step P (base-table samples a sample) satisfies P / 2^k < WT_MIP_THR, stock's ladder rule with its 1.25 raised: one
 * named constant, set at build time only (build.py BE_WT_MIP, default WT_MIP_DEFAULT; the manifest's wt_mip; a version
 * suffix -mipX when not the default), so W3/W4 can build a cleaner and a brighter alternate image. Hysteresis is derived
 * from it: back to a finer level only below WT_MIP_THR x WT_MIP_HYST (at stock's 1.25: 1.05, plan 3.4). */
#ifndef WT_MIP_THR
#error "WT_MIP_THR comes from build.py (BE_WT_MIP, default WT_MIP_DEFAULT)"
#endif
#define WT_MIP_HYST 0.84f
/* Stock 1.6.8's mip builder (M4 0x08112544..0x081126DE: six levels, each from the one before as one stream of all 256 frames,
 * through the 11-tap decimator 0x08113DF0 reset to 12 zeros per level 0x08113F10; docs/plan-wt-fm.md "W2: mip trace"): level
 * k's sample i is centred on level k - 1's position 2i - 7, so on level 0's 2^k i - 7 (2^k - 1). Reading level k at the same
 * sound as level 0 is reading it 7 (2^k - 1) / 2048 of a cycle later: WT_MIP_SKEW(k) in Q32 (2^32 / 2048 = 2^21, exact). The
 * note's own level is stock's phase as it is; another level reads at the difference of their skews. */
#define WT_MIP_SKEW(k) ((7u * ((1u << (k)) - 1u)) << 21)
static inline u32 wt_mip_off(u32 k) { return 0x100000u - (0x100000u >> k); }   /* level k's first float (k = 0 .. 6) */

/* One reader block, from stock's kernel state (WtK) and the block's rate and Position frame, with the FM line fi (0: none,
 * the mip-home block); its samples to s. Plan 3.4: the pending flag's ladder (stock's, bit for bit: kmodel), the phase in
 * (Q32 at the note's mip), the mip choice (WT_MIP_THR, hysteresis; the note's level while fi is 0), one change a block, the
 * phase out (idx, frac at the note's len; the frame playing), mipcur (HOME once the mip-home block is back at the note's
 * level). One change a block (W2 review: each change waits one block at most): a Position change crossfades the frame and
 * defers a wanted mip change (WT_MIP_WAIT); the next block changes the mip and defers the frame. Without the bit a Position
 * moving every block (an LFO, the knob) held the mip off for good: no coarser level under FM, no mip home after it. */
/* BE4.44 W3 (D5's bright alternate, Jeff 2026-11-17: "never go darker"): build.py BE_WT_MIP=off defines WT_MIP_OFF. The reader
 * then stays at stock's level for the note under any FM (the D5 rows' "note level" column): no peak, no mip choice, so no
 * mip-change block and no mip-home block (mipcur stays HOME: the last FM block, its line ending at index 0 at stock's own
 * level, hands back straight to stock's render). Aliasing under heavy FM is the accepted price. */
#ifndef WT_MIP_OFF
/* the finest level k >= k0 with x < thr 2^k (6 at most) */
__attribute__((noinline)) static u32 wt_mip_for(float x, float thr, u32 k) {
    while (k < WT_MIPS - 1u && x >= thr * (float)(1u << k)) k++;
    return k;
}
#endif
enum { WT_RD_PLAIN = 0, WT_RD_FRAME = 1, WT_RD_MIP = 2 };     /* wt_play's block: rd_q, rd_qx, rd_qm (the black box's wt=) */
/* pa: max |a| when it is known (buffer A: inc_sub x the sub wave's peak, wt_body), else 0: a is scanned. W2 review: a scan
 * over one block follows a slow sub's phase (at notes 24..36 its period spans blocks) and flipped the level with the sub's
 * cycle; the known peak is steady. b (B, OSC1's output, from W3) is scanned. */
struct WtBlk { const float *tab; const FmIn *fi; float *s; float rate; u32 n, pos; float pa; };
static_assert(sizeof(WtBlk) == 28, "the test harness writes it");
__attribute__((noinline)) static u32 wt_play(void *osc, const WtBlk *bk) {
    WtK *K = WTK(osc);
    AnOsc *g = state(osc);
    float rate = bk->rate;
    if (K->flag) {                                            /* stock's ladder on this block's rate (0x080A2000) */
        float x = rate;
        u32 k = 0;
        while (x >= 1.25f && k < WT_MIPS - 1u) { x *= .5f; k++; }
        K->D = 1u << k; K->len = 2048u >> k; K->off = wt_mip_off(k);
        K->idx = K->idx % (int)K->len; K->flag = 0;
    }
    u32 l = 31u - (u32)__builtin_clz(K->len | 1u), kn = 11u - l;   /* (len checked by the caller) */
    u32 pos = bk->pos, fcur = (u32)K->frame;
    if (fcur > 255u) fcur = pos;                              /* -1: take the frame, no crossfade */
    const FmIn *fi = bk->fi;
    WtRd rd;
    rd.out = bk->s; rd.n = bk->n; rd.inc = rate * (1.f / 2048.f);
    rd.ph = ((u32)K->idx << (32u - l)) + (u32)(K->frac * (float)(1u << (32u - l)));
    FmIn none;                                                /* (no FM: a zero line on buffer A, finite: + 0 a adds +0) */
    none.a = none.b = (const float *)SA(SA_FMBUF_A); none.ia = none.dia = none.ib = none.dib = 0.f;
    rd.fi = fi ? fi : &none;
    u32 kc = wt_mip(g), kt = kn;
    if (kc == WT_HOME || kc < kn) kc = kn;
#ifndef WT_MIP_OFF
    if (fi) {
        /* the peak step: |inc| + max |I| max |A| + max |J| max |B| (the lines' two ends; A's known peak, else every 4th
         * frame; B every 4th frame) */
        float nf = (float)rd.n, ma = 0.f, mb = 0.f;
        for (u32 i = 0; i < rd.n; i += 4u) {
            ma = __builtin_fmaxf(ma, __builtin_fabsf(fi->a[i]));
            mb = __builtin_fmaxf(mb, __builtin_fabsf(fi->b[i]));
        }
        if (bk->pa != 0.f) ma = bk->pa;
        float I = __builtin_fmaxf(__builtin_fabsf(fi->ia), __builtin_fabsf(fi->ia + fi->dia * nf));
        float J = __builtin_fmaxf(__builtin_fabsf(fi->ib), __builtin_fabsf(fi->ib + fi->dib * nf));
        float x = (rd.inc + I * ma + J * mb) * 2048.f;
        u32 up = wt_mip_for(x, WT_MIP_THR, kn), dn = wt_mip_for(x, WT_MIP_THR * WT_MIP_HYST, kn);
        kt = up > kc ? up : dn < kc ? dn : kc;
    }
#endif
    u32 lc = 11u - kc, wf = g->wt.wflags, wait = 0, what = WT_RD_PLAIN;
    rd.l0 = rd.l1 = lc; rd.o0 = WT_MIP_SKEW(kc) - WT_MIP_SKEW(kn); rd.d1 = 0;
    rd.t0 = bk->tab + wt_mip_off(kc) + (fcur << lc);
    if (pos != fcur && (kt == kc || !(wf & WT_MIP_WAIT))) {   /* rd_qx: the frame, at the mip playing (a mip change waits) */
        rd.t1 = bk->tab + wt_mip_off(kc) + (pos << lc);
        wt_rdf(&rd);
        if (kt != kc) wait = WT_MIP_WAIT;
        kt = kc;
        what = WT_RD_FRAME;
    } else if (kt != kc) {                                    /* rd_qm: the mip, the frame fixed (a frame change waits) */
        u32 lt = 11u - kt;
        rd.l1 = lt; rd.d1 = WT_MIP_SKEW(kt) - WT_MIP_SKEW(kc);
        rd.t1 = bk->tab + wt_mip_off(kt) + (fcur << lt);
        wt_rdx(&rd);
        pos = fcur;
        what = WT_RD_MIP;
    } else {
        wt_rd(&rd);
    }
    K->idx = (int)(rd.ph >> (32u - l));
    K->frac = (float)(rd.ph << l) * (1.f / 4294967296.f);
    K->frame = (int)pos;
    g->wt.wflags = (u8)((wf & ~WT_MIP_WAIT) | wait);
#ifdef WT_MIP_OFF
    g->wt.mipcur = (u8)WT_HOME;                               /* (off: only the note's level, so no mip is ever held) */
#else
    g->wt.mipcur = (u8)(!fi && kt == kn ? WT_HOME : kt);
#endif
    return what;
}

/* Tier 3 (plan 3.3, 3.5's body): stock's render body in stock's order, our reader for its kernel: the table (no WAV: nothing,
 * as stock), the rate (rate_of, stock's bit for bit), the frame, Level and its smoother, the silent rule (D9 (b): a silent
 * oscillator freezes, as stock's, unless it feeds B), then the FM line (fm_setup: Sub FM from A on OSC1; OSC2's FM 1>2 from
 * B, which an Analog OSC1 writes since W3: feeds), the reader, SMOOTH_APPLY and MIX_INTO. 0: not handled (no run state, no
 * voice, a kernel state out of range, or nothing to play: the caller plays tier 2).
 * BE4.44 W3: D3 (Jeff, 2026-11-17): while an Analog OSC2 is synced to this Wavetable OSC1 (mixed sync: the slave follows a
 * copy of WT1's phase at its plain rate), OSC1's Sub FM plays as 0, its value kept: the line's target is 0 and msub is
 * dropped at once (no ramp out: one FM block would leave WT1's phase off the copy until the next note). With no mip held
 * the block is then stock's render (tier 2), bit for bit: mixed sync plays as BE4.43. The black box (SaFmStats wt= / wt_mip,
 * SaStats renders and cycles): every block the reader plays is counted, as an Analog render is in an_osc (a block with no
 * WAV or frozen at level 0 plays nothing, as stock's, and is not). */
__attribute__((noinline)) static int wt_body(void *osc, void *scratch, void *out, const float *env) {
    u32 which = 2;
    u8 *v = voice_of(osc, &which);
    u32 len = WTK(osc)->len;
    if (!area_ok() || !v || len < 32u || len > 2048u || (len & (len - 1u))) return 0;
    Run *r = RUN;
    u32 c0 = CYCCNT;
    WtBlk bk;
    void *obj = (void *)W(osc, 0x18);
    bk.tab = 0;
    if (!FN(W(W(obj, 0), 0x20), int (*)(void *, u32, u32, const float **))(obj, W(osc, 0x1C), 0, &bk.tab)) return 1;
    bk.n = FN(S_BUF_FRAMES, u32 (*)(void *))(out);
    param_fn param = FN(S_PARAM_VALUE, param_fn);
    bk.rate = rate_of(osc, pitch_oct(osc));
    int position = (int)(param((u8 *)osc + OSC_POSITION_PARAM) * 255.f);
    bk.pos = (u32)(position < 0 ? 0 : position > 255 ? 255 : position);
    float level = FN(S_LEVEL_CURVE, float (*)(float))(param((u8 *)osc + OSC_LEVEL_PARAM));
    FN(S_SMOOTH_SET, void (*)(void *, float))((u8 *)osc + OSC_SMOOTH, level);
    F(osc, OSC_RATE) = bk.rate;
    W(osc, OSC_POSITION) = bk.pos;
    AnOsc *g = state(osc);
    int silent = level == 0.f && !W(osc, OSC_MOVING);
    if ((silent && !(g->wt.wflags & WT_FEED_B)) || bk.n > 1024u || bk.n > W(scratch, S_BUF_CAPACITY)) {
        g->msub = g->m12 = 0;                                 /* frozen (stock's level 0); heard again, FM ramps in from 0 */
        return 1;
    }
    FmIn fi;
    const AnProps *p = props(osc);
    AnProps d3;
    if (which == 0) {                                         /* D3: an Analog OSC2 synced to this OSC1 */
        void *o2 = v + S_VOICE_WT2;
        if (is_analog(o2) && props(o2)->sync == 1u) {
            d3 = *p; d3.subfm = 0; p = &d3; g->msub = 0;
            /* W3 review: with no mip held, stock's render whatever feed-B says (a hidden FM 1>2 on the synced OSC2 sets it,
             * and nothing reads B from a Wavetable OSC1 before W4): the index-0 reader's Q32 step would drift WT1's phase
             * off the slave's copy. A held mip still gets its one mip-home block */
            if (wt_mip(g) == WT_HOME) return 0;
        }
    }
    bk.fi = fm_setup(r, v, g, p, which == 1, bk.n, &fi, env) ? &fi : 0;
    /* W3 review: a block longer than the FM buffers (fm_setup: no line, fmx=) with a mip held: the mip goes home at once
     * (stock's level, no crossfade) rather than the reader's zero line reading past buffer A */
    if (!bk.fi && bk.n > AN_FM_FRAMES) { g->wt.mipcur = (u8)WT_HOME; g->wt.wflags &= (u8)~WT_MIP_WAIT; return 0; }
    /* W2 review: no line this block and no mip held (an amount that plays nothing: OSC2's FM 1>2 with B not written this
     * voice-block, fmx=; a WT OSC2's hidden Sub FM, D2; Sub FM under D3) and not feeding B: stock's render (tier 2), bit for
     * bit, not the reader at index 0. What this body set above (Level's smoother target, rate, frame) stock's body sets again
     * from the same inputs */
    if (!bk.fi && wt_mip(g) == WT_HOME && !(g->wt.wflags & WT_FEED_B)) return 0;
    bk.pa = 0.f;
#ifndef WT_MIP_OFF
    /* A's known peak (wt_play): inc_sub x the sub wave's (1; the Square's 1 + |1 - 2 pw|, its DC-free form, from the width
     * its core plays) */
    if (bk.fi && fi.a == (const float *)SA(SA_FMBUF_A)) {
        const u8 *core = v + S_VOICE_SUB + SUB_CORE;
        float pk = 1.f;
        if (core[0] == 3u) pk += __builtin_fabsf(1.f - (float)W(core, 0x18) * 4.6566128730773926e-10f);
        __builtin_memcpy(&bk.pa, &r->inc_sub, 4);
        bk.pa *= pk;
    } else if (bk.fi) {
        /* W3 review: B on its own route (a Wavetable OSC2's FM 1>2: fm_setup gives a = B) has its known peak too, OSC1's
         * increment x its Shape's peak (an_osc, peak_b): at low notes OSC1's period spans blocks, and a scan of B flipped
         * the level with OSC1's phase (as A's did at W2). W4: a Wavetable OSC1 writing B must set peak_b (0: scanned) */
        __builtin_memcpy(&bk.pa, &r->peak_b, 4);
    }
#endif
    bk.s = FN(S_SCRATCH_PTR, float *(*)(void *))(scratch);
    FN(S_SCRATCH_SIZE, void (*)(void *, u32))(scratch, bk.n);
    u32 what = wt_play(osc, &bk);
    volatile SaFmStats *fs = FMSTATS;
    fs->wt++;
    if (what == WT_RD_MIP) fs->wt_mip++;
    if (!silent) {
        FN(S_SMOOTH_APPLY, void (*)(void *, float *, u32, u32))((u8 *)osc + OSC_SMOOTH, bk.s, 0, bk.n);
        FN(S_MIX_INTO, void (*)(void *, void *))(out, scratch);
    }
    r->oscs++;                                                /* (the black box: a render of ours, its cycles) */
    r->cyc += CYCCNT - c0;
    return 1;
}

__attribute__((noinline)) static int wt_fm_osc(void *osc, void *scratch, void *out) {
    AnOsc *g = state(osc);
    u32 t = wt_tag(osc);
    if (g->magic != t || W(g, 4) != ~t) return 0;
    AnProps *p = props(osc);
    float env[2];                                             /* the FM envelope keeps the note's time, every block */
    if (p->envf) fm_env_step(g, p, env);
    else g->fmk = (u16)(AN_FE_DEC << 13);
    /* the gate (plan 3.3): an amount, ramp memory, or a mip held (the mip-home block) */
    u32 x;
    __builtin_memcpy(&x, &g->msub, 4);
    if ((x | p->subfm | p->fm12 | (wt_mip(g) != WT_HOME)) && wt_body(osc, scratch, out, env)) return 1;
    u32 *k = (u32 *)g;                                       /* (not volatile: the call takes osc, so nothing is cached across it) */
    u32 a0 = k[WT_RUN0 / 4u], a1 = k[WT_RUN0 / 4u + 1u], b0 = k[WT_RUN1 / 4u], b1 = k[WT_RUN1 / 4u + 1u], c[WT_RUN2_W];
    static_assert(WT_RUN0_W == 2 && WT_RUN1_W == 2, "two words each");
    copy5(c, k + WT_RUN2 / 4u);
    FN(S_WT_OSC_RENDER, void (*)(void *, void *, void *))(osc, scratch, out);
    k[WT_RUN0 / 4u] = a0; k[WT_RUN0 / 4u + 1u] = a1; k[WT_RUN1 / 4u] = b0; k[WT_RUN1 / 4u + 1u] = b1;
    copy5(k + WT_RUN2 / 4u, c);
    return 1;
}

/* A2, the oscillator render (r0 osc, r1 scratch buffer, r2 output buffer): 1 = rendered (or silent), 0 = stock's render
 * (a Wavetable oscillator: its state is stock's, untouched). */
extern "C" __attribute__((used)) int an_osc(void *osc, void *scratch, void *out) {
    AnOsc *self = state(osc);
    if (self->magic != AN_MAGIC)                              /* a Wavetable oscillator (BE4.44: tagged, wt_fm_osc) */
        return __builtin_expect(self->magic >> 16 == WT_TAG_HI >> 16, 0) ? wt_fm_osc(osc, scratch, out) : 0;
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
    /* P3 (plan 4.8): the FM gate, one word: this oscillator's Sub FM, its ramp memory (msub, m12: one load; OSC1's m12 is
     * 0) and its FM 1>2 (OSC2's, from an Analog OSC1); OSC1 also OSC2's FM 1>2 and its ramp (it feeds B). All 0 (FM off):
     * today's path, and msub / m12 are 0 already. BE4.43: OSC2's amount is FM 1>2 next to an Analog OSC1, else Sub FM. P3 Q6 (4.1): OSC1 feeds buffer B while an Analog OSC2 has FM 1>2, or its
     * ramp down still to run, for the voice an_enter named, in a block the buffer holds; so (E3 (a), 4.4) a silent OSC1
     * renders then too, unheard. */
    u32 x;
    AnProps *pp = props(osc);
    __builtin_memcpy(&x, &self->msub, 4);
    /* (BE4.44 W3: a Wavetable OSC2 with the tag's high half may have FM 1>2 too: feeds checks the whole pair; an untagged one's
     * bytes are stock's, so at worst a stray feeds call that finds nothing) */
    if (which == 0) { x |= pp->subfm; if (a2 || slave->magic >> 16 == WT_TAG_HI >> 16) x |= props(o2)->fm12 | slave->m12; }
    else x |= a1 ? pp->fm12 : pp->subfm;                      /* BE4.43: OSC2's one amount (FM 1>2 next to an Analog OSC1) */
    float env[2];                                             /* (feeds writes it while envf; in memory: no float live here) */
    /* (feeding B; BE4.43: feeds also steps the FM envelope while it is set, AnProps.envf, so it keeps time without an
     * amount too; FM itself is x's alone, below) */
    /* BE4.43 review: an envelope that is unset (Atk 0 / Dec Inf) is held at 1 in every block of the note: stage DEC at j 0,
     * e 1, what fm_env_step itself leaves for Atk 0 / Inf. So an envelope set mid-note (an Atk turned up from 0, a Dec
     * turned down from Inf, or one set again after being unset) starts from 1 at that block whatever came before: an Atk
     * takes effect at the next note, a Dec decays from there. A note start (an_note: AN_FE_START) whose values arrive
     * before the note's first block still attacks. One halfword store a block on the FM-off path (fme is not written:
     * fm_env_step reads DEC at j 0 as e 1, which is what it always holds there). */
    if (!pp->envf) self->fmk = (u16)(AN_FE_DEC << 13);
    float *bo = __builtin_expect((x | pp->envf) != 0, 0) ? feeds(r, v, which, a2, o2, n, self, pp, env) : 0;
    /* S6c review S3: SCRATCH_SIZE sets the size only when the buffer holds n, and never grows it, so a larger block
     * than the scratch buffer is rendered as silence rather than written past it (stock's are 2 x 128 frames) */
    if ((silent && !master && !bo) || n > 1024u || n > W(scratch, S_BUF_CAPACITY)) {
        if (msync) an_master(self, minc, 0, n);               /* the master's phase moves on */
        self->mshape = (u16)AN_NO_SHAPE;                      /* heard again: from its Shape then, no ramp */
        self->msub = self->m12 = 0;                           /* (P3: its FM amounts ramp in from 0: OD16) */
        r->cyc += CYCCNT - c0;
        return 1;
    }
    float inc = rate * (1.f / 2048.f);                        /* a 2048-sample cycle at the native rate */
    if (role != AN_FREE) STATS->synced++;                     /* the black box's synced= (B1: sync in chunks, below) */
    /* P3 Q4 / Q5 / Q6 (plan 4.1 .. 4.5, 4.8): an FM amount, or a ramp down still to run: fm_setup (out of line) */
    FmIn fi;
    int fm = __builtin_expect(x != 0, 0) ? fm_setup(r, v, self, pp, which == 1 && a1, n, &fi, env) : 0;
#if AN_E3
    if (silent && !bo) {                                      /* a silent master: only the wraps for WT2 (feeding B: E3 (a)) */
        self->mshape = (u16)AN_NO_SHAPE;
        if (fm) {
            /* E3 (b) (plan 4.4): an_track knows no FM, so a silent master with Sub FM (or its ramp down) renders, unheard,
             * into the scratch block for its wraps, at its stored Shape (the wraps do not depend on it; delayed stays
             * consistent for when it is heard again) */
            float *s = FN(S_SCRATCH_PTR, float *(*)(void *))(scratch);
            FN(S_SCRATCH_SIZE, void (*)(void *, u32))(scratch, n);
            float k = an_shape((float)props(osc)->shape);
            for (u32 off = 0; off < n; off += AN_EVENTS) {
                fi.off = off;
                an_render_fm(self, inc, k, s + off, n - off < AN_EVENTS ? n - off : AN_EVENTS, off ? g->events : slave->events,
                             AN_MASTER, &fi);
            }
        } else {
            for (u32 off = 0; off < n; off += AN_EVENTS)
                an_track(self, inc, off ? g->events : slave->events, n - off < AN_EVENTS ? n - off : AN_EVENTS);
        }
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
    float k0 = ramp ? an_shape((float)sq0 * (1.f / (float)AN_SHAPE_Q)) : k1;
    u32 step = ramp ? (k0 < 0.f || k1 < 0.f ? AN_RAMP_LOW : AN_RAMP) : role == AN_FREE ? n : AN_EVENTS;   /* (P3 Q3: LOW) */
    u32 parts = step ? (n + step - 1u) / step : 0u;
    float *s = FN(S_SCRATCH_PTR, float *(*)(void *))(scratch);
    FN(S_SCRATCH_SIZE, void (*)(void *, u32))(scratch, n);
    /* Q6's buffer B (BD1): each part of OSC1's, once rendered (its samples are final: an_reshape only moves `delayed`), goes
     * to B before Level, times OSC1's increment (an_feed, at B + the part's offset); a silent OSC1 (E3 (a)) feeds it too.
     * BE4.42: Bit Depth's crush (part_post, an_crush, an_dc) is gone, so B is OSC1's own s. The parts: one loop,
     * instantiated twice (P3 Q6, M1): with FM or B to feed (X), or today's path alone */
    auto parts_loop = [&](auto X) __attribute__((always_inline)) {
        float kp = k0;
        for (u32 j = 0, off = 0; j < parts; j++, off += step) {
            u32 m = n - off < step ? n - off : step, co = off % AN_EVENTS;
            u8 *ev = slave->events;                           /* (unused by AN_FREE) */
            if (role != AN_FREE) {
                /* sync chunks of AN_EVENTS frames, each with its own events: both Analog, chunk 0's wraps are in WT2's
                 * events, chunk 1's in WT1's own (unused otherwise: WT1 is never a slave); mixed, WT2's, made at the chunk's
                 * start */
                ev = (msync ? self->events : off >= AN_EVENTS ? g->events : slave->events) + co;
                if (msync && !co) an_master(self, minc, self->events, n - off < AN_EVENTS ? n - off : AN_EVENTS);
            }
            float k = !ramp || j + 1u == parts ? k1 : k0 + (k1 - k0) * ((float)(j + 1u) / (float)parts);
            if (k != kp) an_reshape(self, kp, k);
            kp = k;
            if (X.value && fm) { fi.off = off; an_render_fm(self, inc, k, s + off, m, ev, role, &fi); }   /* (Q5: the parts) */
            else an_render(self, inc, k, s + off, m, ev, role);
            if (X.value && bo) an_feed(s + off, bo + off, m, inc);   /* (after FM too) */
        }
    };
    if (fm || bo) parts_loop(Tf<1>());
    else parts_loop(Tf<0>());
    if (bo) {
        r->fmtag |= AN_FMTAG_B;
        float pb = inc * an_peak(k0 > k1 ? k0 : k1);          /* B's known peak (wt_body: a Wavetable OSC2's pa) */
        __builtin_memcpy(&r->peak_b, &pb, 4);
    }
    if (!silent) {
        FN(S_SMOOTH_APPLY, void (*)(void *, float *, u32, u32))((u8 *)osc + OSC_SMOOTH, s, 0, n);
        FN(S_MIX_INTO, void (*)(void *, void *))(out, scratch);
    }
    r->cyc += CYCCNT - c0;
    return 1;
}

/* memset / memcpy (the compiler may emit calls to them): dsp_lib.cpp, shared with the drums (S6c). */
