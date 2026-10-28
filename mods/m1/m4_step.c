/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Per-step Condition and Retrigger (drum step sequencer), M4 UI side.
 *
 * Data: the per-step word in the note record's +20 (diag_shared.h), saved as
 * XML attribute "be_step_v1" next to stock's attributes:
 *   step_save  stock note writer 0x0812F4F0 (record at caller sp+8, node r4)
 *   step_load  stock note reader 0x0812FBB4 (record at caller sp+0x24, node r4),
 *              after stock parsed "cond" (the probability byte) and before it
 *              inserts the record. Projects from v2.4..v3.2 kept conditions in
 *              the probability byte (100..173): they move into the word and
 *              probability becomes Always.
 * UI: encoders 5..8 of the drum step sequencer = Condition, Retrig, Division,
 * Ramp. Stock fills the 8 encoder slots from a key table (0x300000BC; entries
 * 5..8 are empty) and the parameter registry (*0x30031484, 499 x 32 B). We
 * claim four empty registry keys once per boot, put them in the table before
 * stock builds the header, and set their values/titles after it:
 *   step_header_before / step_header_after  around 0x0815C27C
 *   step_encoder                            at 0x0815CAA0 (view, key, value)
 * Edits apply to the held/selected steps (stock's own matching, as Lab's
 * locks do); with nothing selected the slots show "--" and turning does
 * nothing. Built on bento-community-lab's step-lock UI (GPL-3.0-or-later).
 */
#include "diag_shared.h"
#include "step_tables.h"

#define FN(a, t) ((t)((a) | 1u))
#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define H(p, o) (*(volatile u16 *)((u8 *)(p) + (o)))
#define B(p, o) (*(volatile u8 *)((u8 *)(p) + (o)))
#define MODEL ((void *)0x30024B00u)
#define REGISTRY (*(volatile u32 *)0x30031484u)
#define REG_KEYS 499u
#define DRUM_KEYS ((volatile u16 *)0x300000BCu)   /* step view's 8 slot keys */
#define UI_TICK_MS (*(volatile u32 *)0x30035F40u)

#define XML_FIND 0x08109C64u      /* (node, name) -> attr */
#define XML_GET_U32 0x08109566u   /* (attr, &v) -> 0 ok */
#define XML_ADD 0x08109CDCu       /* (node, name) -> attr */
#define XML_SET_U32 0x0810958Eu   /* (attr, v) */
#define NOTE_GET 0x0810D2C0u      /* (container, i, rec) -> ok */
#define NOTE_PUT 0x0810D1AAu      /* (container, rec) */
#define PUBLISH 0x08136544u       /* (MODEL, view+0x113E4, container) */
#define GRID 0x0815C4B8u          /* (view) */
#define STEP_MATCH 0x0815B9C0u    /* (view, absolute step) -> held/selected */
#define ANY_SELECTED 0x0815BAB0u  /* (view) */
#define SNAPSHOT 0x0815BA04u      /* (view, rec) -> selected note */
#define SLOT_SET 0x08171C1Cu      /* (slot, key, value, 1) */
#define SLOT_TITLE 0x0817E85Eu    /* (slot + 0x3C8, text) */
#define BAR_TEXT 0x08170656u      /* (bar, slot, text or 0) */

static const char ATTR[] = "be_step_v1";

/* ---- note fields (velocity / probability / offset) ----------------------- */
/* rec: [0] kind | prob << 8 | pad << 16, [1] time (960 per step), [4] pitch | vel << 16 */
enum { F_VEL, F_PROB, F_OFFSET };
static const u32 LOCK_BIT[3] = {LOCK_VEL, LOCK_PROB, LOCK_OFFSET};

static int offset_of(u32 t) { int q = (int)(t % 960u); return q > 480 ? q - 960 : q; }
static u32 note_val(const u32 *e, u32 f) {
    if (f == F_VEL) { u32 v = e[4] >> 16; return v ? v : 100u; }
    if (f == F_PROB) return (e[0] >> 8) & 0xFFu;
    return (u32)offset_of(e[1]);
}
static void note_set(u32 *e, u32 f, u32 v) {
    if (f == F_VEL) e[4] = (e[4] & 0xFFFFu) | (v << 16);
    else if (f == F_PROB) e[0] = (e[0] & ~0xFF00u) | ((v & 0xFFu) << 8);
    else {
        int o = (int)v;
        if (o > 470) o = 470;
        if (o < -470) o = -470;
        e[1] = (u32)((int)e[1] - offset_of(e[1]) + o);
    }
}
static u32 with_locks(u32 w, u32 locks) {
    return step_make(step_cond(w), step_hits(w), step_div(w), step_ramp(w), locks, step_handle(w));
}

/* ---- project save / load ------------------------------------------------ */

static volatile CurveState *curves(void);
static u32 curve_for_index(u32 idx);
__attribute__((used, retain)) void step_save(void *node, const void *rec) {
    if (B(rec, 0) != 1) return;
    u32 w = W(rec, 20);
    if (!step_any(w)) return;                       /* incl. curve words (M2) */
    u32 h = step_handle(w);
    if (h) {                                        /* the saved word carries the sidecar index */
        volatile CurveState *c = curves();
        u32 idx = 0;
        if (c && c->savelist && c->nsave + 1u < CURVE_HANDLES) {
            ((volatile u16 *)c->savelist)[c->nsave] = (u16)h;
            idx = ++c->nsave;
        }
        w = step_make(step_cond(w), 0, STEP_DIV_DEFAULT, 0, step_locks(w), idx);
        if (!w) return;
    }
    void *a = FN(XML_ADD, void *(*)(void *, const char *))(node, ATTR);
    if (a) FN(XML_SET_U32, void (*)(void *, u32))(a, w);
}

static void doc_first_note(void);
__attribute__((used, retain)) void step_load(void *node, void *rec) {
    if (B(rec, 0) != 1) return;
    doc_first_note();                               /* the project's sidecar: curves + track settings */
    u32 w = 0, x = 0;
    void *a = FN(XML_FIND, void *(*)(void *, const char *))(node, ATTR);
    if (a && !FN(XML_GET_U32, int (*)(void *, u32 *))(a, &x) && step_any(x)) w = x;
    u32 h = 0, idx = step_handle(w);
    if (idx) h = curve_for_index(idx);              /* sidecar index -> this boot's handle (or 0) */
    u32 prob = B(rec, 1), cond = step_cond(w);
    if (prob >= 100 && prob <= 173) {               /* v2.4..v3.2 condition in the probability byte */
        if (!cond) cond = prob - 99;
        B(rec, 1) = 0;                              /* probability: Always */
    }
    /* Notes without lock data load unlocked: the pad default is the value most of
     * them share, and any other value gets locked (not overwritten) the first
     * time the default changes. */
    /* A curve note's saved word carries its index in the project's sidecar; it
     * gets this boot's handle for that curve, or none (condition and locks kept). */
    W(rec, 20) = step_make(cond, step_hits(w), step_div(w), step_ramp(w), step_locks(w), h);
}

/* ---- registry keys -------------------------------------------------------- */

typedef struct { u32 magic, gen, ok; u16 key[10]; } StepUI;   /* one M4-only line */
#define SU ((volatile StepUI *)0x3003C880u)
#define SU_MAGIC 0x35505453u /* "STP5": ten keys (+ Dest) since v3.48 */
#define SU_KEYS 10u
_Static_assert(sizeof(StepUI) <= 32, "step UI state is one cache line");

static const char *const AT_REC_LABELS[2] = {"Off", "On"};
static const char *const AT_OUT_LABELS[3] = {"Off", "Poly", "Channel"};
static const char *const LOOP_LABELS[34] = {"Off", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
    "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31",
    "32", "Forever"};
/* Beat Sync / Triggered are our own Off/On lists: stock's toggle keys (0x4E, 0x4D,
 * type 6) showed a blank value when our row was refilled with them on (v3.46). */
static const char *const TITLES[SU_KEYS] = {"Condition", "Retrig", "Division", "Ramp", "AT Rec", "AT Out", "Loop",
                                            "Beat Sync", "Triggered", "Dest"};
static const char *const *const CHOICES[SU_KEYS] = {COND_LABELS, HITS_LABELS, DIV_LABELS, RAMP_LABELS, AT_REC_LABELS,
                                                    AT_OUT_LABELS, LOOP_LABELS, AT_REC_LABELS, AT_REC_LABELS, DEST_LABELS};
static const u32 COUNTS[SU_KEYS] = {COND_N + 1u, 16u, STEP_DIVS, RAMP_N, 2u, 3u, 34u, 2u, 2u, DEST_N};

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }
static void mod_defaults(void);
static volatile ModCfg *mod_cfg_block(void);
static void mod_write_begin(volatile ModCfg *m);
static void mod_write_end(volatile ModCfg *m);

/* Claim SU_KEYS empty registry keys (from the top), once per boot. */
static int claim(void) {
    if (SU->magic != SU_MAGIC || SU->gen != boot_gen()) {
        SU->ok = 0; SU->gen = boot_gen(); SU->magic = SU_MAGIC;
    }
    if (SU->ok) return 1;
    u32 r = REGISTRY;
    if (!r) return 0;
    u16 keys[SU_KEYS]; u32 n = 0;
    for (u32 k = REG_KEYS - 1; k >= 0x100 && n < SU_KEYS; k--)
        if (!H(r + k * 32, 0)) keys[n++] = (u16)k;
    if (n < SU_KEYS) return 0;
    for (u32 i = 0; i < SU_KEYS; i++) {
        u32 d = r + keys[i] * 32u;
        for (u32 j = 0; j < 8; j++) W(d, j * 4) = 0;
        H(d, 0) = keys[i]; B(d, 2) = 7;                 /* list parameter, as Lab's pickers */
        W(d, 4) = (u32)TITLES[i]; W(d, 8) = (u32)CHOICES[i]; W(d, 12) = COUNTS[i];
        SU->key[i] = keys[i];
    }
    SU->ok = 1;
    return 1;
}

/* ---- field <-> encoder value -------------------------------------------- */

static u32 cond_to_index(u32 cond) {                    /* word field -> list index (0 = Off) */
    if (!cond) return 0;
    for (u32 i = 0; i < COND_N; i++)
        if (COND_STORED[i] == cond + 99u) return i + 1u;
    return 0;
}
static u32 field_value(u32 w, u32 which) {
    switch (which) {
    case 0: return cond_to_index(step_cond(w));
    case 1: return step_hits(w);
    case 2: return step_div(w);
    default: return (u32)((step_ramp(w) + 100) / RAMP_STEP);
    }
}
static u32 with_field(u32 w, u32 which, u32 v) {
    u32 cond = step_cond(w), hits = step_hits(w), div = step_div(w);
    int ramp = step_ramp(w);
    if (v >= COUNTS[which]) v = COUNTS[which] - 1u;
    switch (which) {
    case 0: cond = v ? COND_STORED[v - 1u] - 99u : 0u; break;
    case 1: hits = v; break;
    case 2: div = v; break;
    default: ramp = (int)v * RAMP_STEP - 100; break;
    }
    return step_make(cond, hits, div, ramp, step_locks(w), step_handle(w));
}

/* ---- drum step sequencer header and encoders ---------------------------- */

static int drum_step_view(void *v) { return v && !B(v, 0x113E0) && W(v, 0x113E4) && W(v, 0x113DC); }

/* ---- defaults + locks (velocity / probability / offset) ------------------
 * A pad's default (per clip) is the value its unlocked notes share; stock's
 * "new step" values (view +0x114C0/C4/C8) hold it while editing. Stock's three
 * edit functions (0x0815C7C4/8AC/990) are wrapped: param_pre computes the old
 * default, stock runs, param_post then
 *   - nothing selected (stock changed the default): unlocked notes that had
 *     the old default take the new one; an unlocked note with another value
 *     (e.g. recorded live) is locked instead of overwritten;
 *   - steps selected (stock edited them): lock each where it now differs from
 *     the default, unlock where it equals it. */
static int matches(void *v, const u32 *e);
static void strike_init(void);
static const u32 DEFAULT_AT[3] = {0x114C0u, 0x114C4u, 0x114C8u};

static void publish(void *v) {
    FN(PUBLISH, void (*)(void *, void *, void *))(MODEL, (u8 *)v + 0x113E4, (void *)W(v, 0x113DC));
    FN(GRID, void (*)(void *))(v);
}

static int pad_default(void *v, u32 f, u32 *out);
__attribute__((used, retain)) u32 param_pre(void *v, u32 f) {
    if (f >= 3) return 0;
    u32 d;
    if (drum_step_view(v) && pad_default(v, f, &d)) {
        W(v, DEFAULT_AT[f]) = d;      /* stock's edit compares/assigns against the real default */
        return d;
    }
    return W(v, DEFAULT_AT[f]);
}

__attribute__((used, retain)) void param_post(void *v, u32 f, u32 old) {
    if (f >= 3 || !drum_step_view(v)) return;
    void *container = (void *)W(v, 0x113DC);
    u32 pad = W(v, 0x11400), dflt = W(v, DEFAULT_AT[f]), bit = LOCK_BIT[f], e[6], changed = 0;
    int selected = FN(ANY_SELECTED, int (*)(void *))(v);
    if (!selected && dflt == old) return;
    for (u32 j = 0; j < 511; j++) {
        if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(container, j, e)) break;
        if ((e[0] & 0xFFu) != 1u || (e[0] >> 16) != pad) continue;
        u32 locks = step_locks(e[5]), val = note_val(e, f), w = e[5];
        if (selected) {
            if (!matches(v, e)) continue;
            w = with_locks(e[5], val != dflt ? locks | bit : locks & ~bit);
        } else if (!(locks & bit)) {
            if (val == old) note_set(e, f, dflt);
            else w = with_locks(e[5], locks | bit);     /* an individual value: keep it */
        } else {
            continue;
        }
        if (w == e[5] && !(!selected && val == old)) continue;
        e[5] = w;
        FN(NOTE_PUT, void (*)(void *, u32 *))(container, e);
        changed = 1;
    }
    if (changed) publish(v);
}

/* The pad's default = the most common value among its unlocked notes in this
 * clip. Stock resets its "new step" fields to 100 / Always / 0 (0x0815C060,
 * 0x0815CDB2, e.g. on returning to the view), so they can't hold our state:
 * the default is recomputed from the notes and copied into stock's field. */
static int pad_default(void *v, u32 f, u32 *out) {
    void *container = (void *)W(v, 0x113DC);
    u32 pad = W(v, 0x11400), e[6], any = 0;
    u8 count[128];
    for (u32 i = 0; i < 128; i++) count[i] = 0;
    for (u32 j = 0; j < 511; j++) {
        if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(container, j, e)) break;
        if ((e[0] & 0xFFu) != 1u || (e[0] >> 16) != pad || (step_locks(e[5]) & LOCK_BIT[f])) continue;
        int x = (int)note_val(e, f);
        u32 bin = f == F_OFFSET ? (u32)((x + 470) / 10) : (u32)x;
        if (bin < 128 && count[bin] < 255) count[bin]++;
        any = 1;
    }
    if (!any) return 0;
    u32 best = 0;
    for (u32 i = 1; i < 128; i++)
        if (count[i] > count[best]) best = i;
    *out = f == F_OFFSET ? (u32)((int)best * 10 - 470) : best;
    return 1;
}

static void sync_defaults(void *v) {
    for (u32 f = 0; f < 3; f++) {
        u32 d;
        if (pad_default(v, f, &d)) W(v, DEFAULT_AT[f]) = d;
    }
}

__attribute__((used, retain)) void step_header_before(void *v) {
    if (!drum_step_view(v) || !claim()) return;
    for (u32 i = 0; i < 4; i++) DRUM_KEYS[4 + i] = SU->key[i];
    sync_defaults(v);
}

__attribute__((used, retain)) void step_header_after(void *v) {
    if (!drum_step_view(v) || !SU->ok || DRUM_KEYS[4] != SU->key[0]) return;
    u32 e[6] = {0};
    int has = FN(SNAPSHOT, int (*)(void *, u32 *))(v, e) && (e[0] & 0xFFu) == 1u;
    void *bar = (u8 *)v + 0xEBF0;
    for (u32 i = 0; i < 4; i++) {
        void *slot = (u8 *)bar + 0x38 + (4 + i) * 0x468;
        B(slot, 0x2C8) = 0;                                   /* clear an old text override */
        FN(SLOT_SET, void (*)(void *, u32, int, u32))(slot, SU->key[i], (int)field_value(has ? e[5] : 0, i), 1);
        FN(SLOT_TITLE, void (*)(void *, const char *))((u8 *)slot + 0x3C8, TITLES[i]);
        if (!has) FN(BAR_TEXT, void (*)(void *, u32, const char *))(bar, 4 + i, "--");
    }
}

static int matches(void *v, const u32 *e) {
    if ((e[0] & 0xFFu) != 1u || (e[0] >> 16) != W(v, 0x11400)) return 0;
    int t = (int)e[1], q = t % 960;
    if (q > 480) q -= 960;
    return FN(STEP_MATCH, int (*)(void *, u32))(v, (u32)((t - q) / 960));
}

static void protect_release(void *v) {   /* same as m4_holdstep.c: no toggle on release after an edit */
    u32 past = UI_TICK_MS - 1001u;
    if (!past) past = 0xFFFFFFFFu;
    for (u32 i = 0; i < 32; i++)
        if (W(v, 0x11420 + i * 4)) W(v, 0x11420 + i * 4) = past;
}

/* 1 = handled (ours), 0 = stock key. */
__attribute__((used, retain)) int step_encoder(void *v, u32 key, int value) {
    if (!SU->ok || !drum_step_view(v)) return 0;
    u32 which = 4;
    for (u32 i = 0; i < 4; i++)
        if (key == SU->key[i]) which = i;
    if (which == 4) return 0;
    if (!FN(ANY_SELECTED, int (*)(void *))(v)) return 1;
    protect_release(v);
    void *container = (void *)W(v, 0x113DC);
    u32 e[6], changed = 0;
    for (u32 j = 0; j < 511; j++) {
        if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(container, j, e)) break;
        if (!matches(v, e)) continue;
        u32 w = with_field(e[5], which, value < 0 ? 0u : (u32)value);
        if (w == e[5]) continue;
        e[5] = w;
        FN(NOTE_PUT, void (*)(void *, u32 *))(container, e);
        changed = 1;
    }
    if (changed) {
        FN(PUBLISH, void (*)(void *, void *, void *))(MODEL, (u8 *)v + 0x113E4, container);
        FN(GRID, void (*)(void *))(v);
    }
    FN(0x0815C27Cu, void (*)(void *))(v);   /* rebuild the header: shows the new values */
    return 1;
}

/* ---- strike velocity: a new step takes its pad's hit velocity ------------
 * The M7 keeps a per-pad strike (STRIKES: press counter + peak velocity).
 * Step view: the pad press (0x0815D1EC) marks that pad pending; when stock's
 * toggle (0x0815D114) then creates the step, the note gets that pad's peak
 * velocity. Several pads pressed together each use their own strike.
 * Any view (drum Sequence page): stock's insert (0x0812E070, strike_insert)
 * applies the strike of the note's pad if it is fresh and unused.
 * Applied strikes are locked if they differ from the pad default. */
#define STEP_HAS_NOTE 0x0815BCB4u   /* (view, visible index) */
#define STRIKE_WINDOW_MS 3000u
#define STRIKE_FRESH_MS 500u
#define BUMP_CUR (*(volatile u32 *)0x3000004Cu)
#define BUMP_END (*(volatile u32 *)0x30000048u)
#define BUMP_ALLOC 0x08104C08u

typedef struct {
    u32 gen;
    u8 used[16];        /* press counter last used to set a velocity, per pad */
    u8 seen[16];        /* press counter last seen by a UI tick, per pad */
    u32 seen_ms[16];    /* ... and when */
    u32 pend_ms;        /* time of the latest pending Step-view press */
    u16 pend;           /* Step view: pads pressed, awaiting their toggle */
    u16 r_;
} PadState;

static void strike_init(void) {
    if (STRIKE_SET->magic == STRIKE_SET_MAGIC) return;
    STRIKE_SET->off = 0;          /* on by default */
    STRIKE_SET->pads = 0; STRIKE_SET->r[2] = ~0u;
    STRIKE_SET->magic = STRIKE_SET_MAGIC;
}
static int strike_off(void) { strike_init(); return STRIKE_SET->off != 0; }

/* This boot's per-pad state (0 if the native allocator has no room). */
static volatile PadState *pads(void) {
    strike_init();
    volatile PadState *ps = (volatile PadState *)STRIKE_SET->pads;
    if (ps && STRIKE_SET->r[2] == ~(u32)ps && ps->gen == boot_gen()) return ps;   /* never follow an unchecked pointer */
    u32 cur = BUMP_CUR, end = BUMP_END;
    if (end <= cur || end - cur < sizeof(PadState) + 64u) return 0;   /* the native allocator faults on overflow */
    u32 got = FN(BUMP_ALLOC, u32 (*)(u32))(sizeof(PadState));
    if (got != cur) return 0;
    ps = (volatile PadState *)got;
    for (u32 i = 0; i < sizeof(PadState); i++) ((volatile u8 *)ps)[i] = 0;
    for (u32 p = 0; p < 16; p++) ps->used[p] = ps->seen[p] = STRIKES->seq[p];   /* old presses don't count */
    ps->gen = boot_gen();
    STRIKE_SET->pads = (u32)ps; STRIKE_SET->r[2] = ~(u32)ps;
    return ps;
}

static void apply_strike(void *v, u32 *e, u32 vel) {
    u32 dflt;
    if (!v || !pad_default(v, F_VEL, &dflt)) dflt = v ? W(v, 0x114C0) : 0u;
    note_set(e, F_VEL, vel);
    u32 locks = step_locks(e[5]);
    e[5] = with_locks(e[5], vel != dflt ? locks | LOCK_VEL : locks & ~LOCK_VEL);
}

__attribute__((used, retain)) void strike_press(void *v, u32 pad) {
    volatile PadState *ps;
    if (pad >= 16 || !drum_step_view(v) || strike_off() || !(ps = pads())) return;
    u32 seq = STRIKES->seq[pad];
    if (seq == ps->used[pad]) return;                         /* no new press of this pad */
    ps->used[pad] = (u8)seq;
    ps->pend |= (u16)(1u << pad);
    ps->pend_ms = UI_TICK_MS;
}

__attribute__((used, retain)) u32 toggle_pre(void *v, u32 idx) {
    return idx < 32 && drum_step_view(v) ? (u32)FN(STEP_HAS_NOTE, int (*)(void *, u32))(v, idx) : 1u;
}

static int pad_default(void *v, u32 f, u32 *out);
__attribute__((used, retain)) void toggle_post(void *v, u32 idx, u32 had) {
    volatile PadState *ps = pads();
    if (!ps || !drum_step_view(v)) return;
    u32 step = W(v, 0x113F4) + idx, pad = step - W(v, 0x11404), bit = 1u << (pad & 15u);
    if (pad >= 16 || !(ps->pend & bit)) return;
    ps->pend &= (u16)~bit;
    if (had || UI_TICK_MS - ps->pend_ms > STRIKE_WINDOW_MS) return;
    u32 vel = STRIKES->vel[pad];
    if (!vel) return;
    void *container = (void *)W(v, 0x113DC);
    u32 drum = W(v, 0x11400), e[6];
    for (u32 j = 0; j < 511; j++) {
        if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(container, j, e)) break;
        if ((e[0] & 0xFFu) != 1u || (e[0] >> 16) != drum) continue;
        int t = (int)e[1], q = offset_of(e[1]);
        if ((u32)((t - q) / 960) != step) continue;
        apply_strike(v, e, vel);
        FN(NOTE_PUT, void (*)(void *, u32 *))(container, e);
        publish(v);
        return;
    }
}

/* ---- piano roll: Condition on encoder 7 (note-edit mode) ------------------
 * Stock's note-edit keys come from a flash table [Event, Start, Note, Length,
 * Velocity, Probability, 0, Auto]; slot 7 is bound after stock's header, as
 * Lab's Bars encoder bound its slot. Edits go through the selection list
 * (view+0x122D28) and stock's commit FUN_08157860, like stock's own edits. */
#define PIANO_SLOT 6u
#define PIANO_SEL 0x122D28u
#define PIANO_SELECTED 0x0815884Cu   /* (view, sel list, &n, rec) */
#define PIANO_COMMIT 0x08157860u     /* (view) */
#define PIANO_HEADER 0x081599BCu     /* (view): patched entry */

static int piano_note_view(void *v) { return v && !B(v, 0x125DAC) && W(v, 0x11CD00); }

static int piano_selected(void *v, u32 *rec) {
    u32 n = 0;
    for (u32 i = 0; i < 6; i++) rec[i] = 0;
    FN(PIANO_SELECTED, void (*)(void *, void *, u32 *, u32 *))(v, (u8 *)v + PIANO_SEL, &n, rec);
    return (rec[0] & 0xFFu) == 1u;
}

static void at_header(void *v);
__attribute__((used, retain)) void piano_header_after(void *v) {
    if (v && B(v, 0x125DAC) && claim()) { at_header(v); return; }   /* Sequence view: AT settings */
    if (!piano_note_view(v) || !claim()) return;
    if (H(v, 0x11BCC0 + PIANO_SLOT * 2)) return;           /* slot taken (stock changed?): leave it */
    u32 rec[6];
    int has = piano_selected(v, rec);
    u32 value = field_value(has ? rec[5] : 0, 0);
    void *bar = (u8 *)v + 0x55F0, *slot = (u8 *)bar + 0x38 + PIANO_SLOT * 0x468;
    H(v, 0x11BCC0 + PIANO_SLOT * 2) = SU->key[0];
    W(v, 0x11BCD0 + PIANO_SLOT * 4) = value;
    W(v, 0x11BCA0 + PIANO_SLOT * 4) = W(v, 0x11CD00);
    FN(BAR_TEXT, void (*)(void *, u32, const char *))(bar, PIANO_SLOT, 0);
    FN(SLOT_SET, void (*)(void *, u32, int, u32))(slot, SU->key[0], (int)value, 1);
    FN(SLOT_TITLE, void (*)(void *, const char *))((u8 *)slot + 0x3C8, TITLES[0]);
    if (!has) FN(BAR_TEXT, void (*)(void *, u32, const char *))(bar, PIANO_SLOT, "--");
}

/* Piano-roll event handler entry 0x0815A598 (view, event). 1 = handled. */
int strike_event(void *v, const u16 *ev);
static int at_event(void *v, const u32 *ev);
__attribute__((used, retain)) int piano_event(void *v, const u32 *ev) {
    if ((ev[0] & 0xFFFFu) == STRIKE_ACTION) return strike_event(v, (const u16 *)ev);
    if (SU->ok && v && B(v, 0x125DAC) && (ev[0] & 0xFFFFu) == 0xCFu && (ev[2] == SU->key[4] || ev[2] == SU->key[5]))
        return at_event(v, ev);
    if (!SU->ok || !piano_note_view(v) || (ev[0] & 0xFFFFu) != 0xCFu || ev[2] != SU->key[0]) return 0;
    void *sel = (u8 *)v + PIANO_SEL;
    u32 e[6], changed = 0;
    int value = (int)ev[3];
    for (u32 j = 0; j < 511; j++) {
        if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(sel, j, e)) break;
        if ((e[0] & 0xFFu) != 1u) continue;
        u32 w = with_field(e[5], 0, value < 0 ? 0u : (u32)value);
        if (w == e[5]) continue;
        e[5] = w;
        FN(NOTE_PUT, void (*)(void *, u32 *))(sel, e);
        changed = 1;
    }
    if (changed) FN(PIANO_COMMIT, void (*)(void *))(v);
    FN(PIANO_HEADER, void (*)(void *))(v);
    return 1;
}

/* ---- "Strike Vel" button in the header bar (drum step / sequence view) ----
 * From bento-community-lab's header_actions.c (GPL-3.0-or-later), which put
 * its Select toggle in the same place. Stock's header description (0x1C4 B,
 * generated each UI tick by 0x08134800) holds six 48-byte action records at
 * +0x74: +0x00 label, +0x20 empty, +0x21 active (lit outline), +0x22 tab group,
 * +0x2C action id. On SEQ page 0x27 the free slots are 3 and 4 (between
 * Sequence and Undo); we take the rightmost free normal slot, left of Undo.
 * Stock applies a slot's visibility only while the header root's layout flag
 * (+0x117C) is armed (page change), so header_sync re-arms it when our button
 * differs from the applied header (root+0x34). The button's action id arrives
 * at the drum view's event handler (0x0815D600). */
#define HEADER_ROOT ((volatile u8 *)0x30034D48u)
#define HEADER_ROOT_VTABLE 0x081B3680u
#define REC(p, i) ((volatile u8 *)(p) + 0x74u + (u32)(i) * 48u)
#define MODEL_PAGE (*(volatile u8 *)((u8 *)MODEL + 0x12C))
static const char STRIKE_LABEL[] = "Strike Vel";

static u32 rid(volatile const u8 *r) { return (u32)r[44] | ((u32)r[45] << 8); }

/* Put (or update) our lit/unlit button; 1 if the description changed. */
static int header_action_lit(void *out, u32 id, const char *label, int lit) {
    for (u32 i = 0; i < 6; i++) {
        volatile u8 *d = REC(out, i);
        if (rid(d) != id) continue;
        int change = d[0x20] != 0 || d[0x21] != (lit ? 1 : 0);
        d[0x20] = 0; d[0x21] = lit ? 1 : 0;
        u32 n = 0;
        do { if (d[n] != (u8)label[n]) change = 1; d[n] = (u8)label[n]; } while (label[n++] && n < 31);
        d[31] = 0;
        return change;
    }
    for (int i = 5; i >= 0; i--) {                   /* rightmost free normal slot */
        volatile u8 *d = REC(out, i);
        if (rid(d) || d[0] || d[0x22]) continue;
        for (u32 j = 0; j < 48; j++) d[j] = 0;
        u32 n = 0;
        do { d[n] = (u8)label[n]; } while (label[n++] && n < 31);
        d[31] = 0; d[0x21] = lit ? 1 : 0; d[44] = (u8)id; d[45] = (u8)(id >> 8);
        return 1;
    }
    return 0;
}

static void header_sync(void *out) {
    if (*(volatile u32 *)HEADER_ROOT != HEADER_ROOT_VTABLE) return;
    for (u32 i = 0; i < 6; i++) {
        volatile u8 *a = REC(out, i), *b = REC(HEADER_ROOT + 0x34, i);
        u32 ia = rid(a), ib = rid(b);
        if (ia != STRIKE_ACTION && ib != STRIKE_ACTION && ia != ROWS_ACTION && ib != ROWS_ACTION && ia != MOD_ACTION && ib != MOD_ACTION)
            continue;
        if (rid(a) != rid(b) || a[0x20] != b[0x20] || a[0x21] != b[0x21] || a[0x22] != b[0x22]) {
            HEADER_ROOT[0x117C] = 1;
            return;
        }
    }
}

/* After stock generated the header description (0x08134800 returns "changed"). */
/* Is the selected track a one-shot (drum) instrument? Page 0x27 is shared by
 * the drum step sequencer and the piano roll; the track type tells them apart
 * regardless of which view object stock refreshed last (v3.9 used a flag set
 * by the header hooks, and the button never appeared on the device). Same
 * native lookups as the INST-hold shortcut (m4_controls.c). */
static int drum_track_selected(void) {
    u32 track = FN(0x0812DC74u, u32 (*)(void *, u32))((u8 *)MODEL + 4, 0x131), h = 0;
    if (!FN(0x08133960u, u32 (*)(void *, u32, u32 *))(MODEL, track, &h)) return 0;
    void *obj = 0;
    if (!FN(0x0812D80Eu, u32 (*)(void *, u32, void **))((void *)W(MODEL, 0), h, &obj) || !obj) return 0;
    return ((u32 (*)(void *))W((void *)W(obj, 0), 0))(obj) == 0x0Eu;
}

/* Diagnostics in StrikeSet r[] (printed in the black box status line):
 * r[0] calls on page 0x27, r[1] last: drum | slot << 4 (0xF = none) | change << 8. */
static volatile AtSet *at_set(void);
static void at_publish(void);
static void mod_cfg(void);
static void ext_toolbar(void *out, int *changed);
__attribute__((used, retain)) int strike_toolbar(void *model, void *out, int changed) {
    (void)model;
    if (at_set()->pending) at_publish();            /* after a project load (UI context) */
    mod_cfg();                                      /* F1: once per boot */
    strike_init();
    volatile PadState *ps = pads();
    for (u32 p = 0; ps && p < 16; p++)
        if (STRIKES->seq[p] != ps->seen[p]) { ps->seen[p] = STRIKES->seq[p]; ps->seen_ms[p] = UI_TICK_MS; }
    ext_toolbar(out, &changed);
    if (MODEL_PAGE == 0x27) {
        int drum = drum_track_selected(), ins = 0;
        if (drum) ins = header_action_lit(out, STRIKE_ACTION, STRIKE_LABEL, !STRIKE_SET->off);
        changed |= ins;
        u32 slot = 0xF;
        for (u32 i = 0; i < 6; i++)
            if (rid(REC(out, i)) == STRIKE_ACTION) slot = i;
        STRIKE_SET->r[0]++;
        STRIKE_SET->r[1] = (u32)drum | (slot << 4) | ((u32)ins << 8);
    }
    header_sync(out);
    return changed;
}

/* Drum view event handler entry (0x0815D600). 1 = handled. */
static int drum_track_selected(void);
/* The button's action reaches whichever SEQ view object is showing: the drum
 * step view (0x0815D600) in Step, the piano-roll view object (0x0815A598) in
 * the Sequence tab, which stock draws with the piano view even for drum tracks. */
__attribute__((used, retain)) int strike_event(void *v, const u16 *ev) {
    (void)v;
    if (ev[0] != STRIKE_ACTION || !drum_track_selected()) return 0;
    strike_init();
    STRIKE_SET->off = !STRIKE_SET->off;
    volatile PadState *ps = pads();
    if (ps) ps->pend = 0;                             /* drop pending strikes */
    return 1;
}

/* Any view's new note (stock's insert, 0x0812E070: model, sequence, record):
 * covers the drum Sequence page, which adds steps through the piano-roll view
 * object rather than the drum toggle. The note's pad (its drum index) strike
 * applies once, if fresh (pressed < 500 ms ago, or so recently that no UI tick
 * has seen it yet) and not already used by the Step view's path, so bulk
 * inserts (Double Seq, Paste) and late touchscreen entries keep their velocity.
 * Locked (the view and its pad default aren't known here). */
/* Pad p's strike if usable here: unused, and fresh (unseen by any UI tick yet,
 * or first seen < 500 ms ago). Returns its velocity, or 0. */
static u32 fresh_vel(volatile PadState *ps, u32 p) {
    u32 seq = STRIKES->seq[p];
    if (seq == ps->used[p] || !STRIKES->vel[p]) return 0;
    if (seq == ps->seen[p] && UI_TICK_MS - ps->seen_ms[p] > STRIKE_FRESH_MS) return 0;
    return STRIKES->vel[p];
}

/* ---- M2-1: pressure curves for live-recorded melodic notes ------------------
 * The pad's capture slot (M7, CapBlock) is resampled to 16 samples per step
 * over the note's final length and stored in the pool; the handle goes into
 * the note word (tag 0xB7). See bind_pitch / curve_update for the trigger. */
static volatile CurveState *curves(void) {
    volatile CurveState *c = CURVES;
    if (c->magic == CURVES_MAGIC && c->gen == boot_gen()) return c->pool ? c : 0;
    c->magic = 0; c->pool = c->table = c->used = c->claims = c->misses = 0; c->next = 1;
    c->savelist = c->nsave = c->sc_count = c->sc_bytes = c->sc_status = c->wbuf = 0;
    for (u32 i = 0; i < 32; i++) CLAIMED[i] = 0xFFFFFFFFu;
    u32 need = CURVE_POOL_BYTES + CURVE_HANDLES * 6u + SIDECAR_WBUF, cur = BUMP_CUR, end = BUMP_END;
    if (end > cur && end - cur >= need + 192u) {          /* the native allocator faults on overflow */
        u32 pool = FN(BUMP_ALLOC, u32 (*)(u32))(CURVE_POOL_BYTES);
        u32 cur2 = BUMP_CUR, table = FN(BUMP_ALLOC, u32 (*)(u32))(CURVE_HANDLES * 4u);
        u32 cur3 = BUMP_CUR, list = FN(BUMP_ALLOC, u32 (*)(u32))(CURVE_HANDLES * 2u);
        u32 cur4 = BUMP_CUR, wbuf = FN(BUMP_ALLOC, u32 (*)(u32))(SIDECAR_WBUF);
        if (pool == cur && table == cur2 && list == cur3 && wbuf == cur4) {
            c->pool = pool; c->table = table; c->savelist = list; c->wbuf = wbuf;
        }
    }
    c->gen = boot_gen(); c->magic = CURVES_MAGIC;
    return c->pool ? c : 0;
}

static volatile CapSlot *pick_slot(u32 rec_pad, u32 *pad_out, u32 *idx_out) {

    volatile CapBlock *b = (volatile CapBlock *)CAPCFG->block;
    if (CAPCFG->magic != CAPCFG_MAGIC || CAPCFG->check != ~(u32)b || !b) return 0;
    volatile CapSlot *best = 0;
    for (u32 p = 0; p < 16; p++)
        for (u32 k = 0; k < 2; k++) {
            volatile CapSlot *s = &b->slot[p][k];
            if (!s->done || !s->n || CLAIMED[p * 2 + k] == s->seq) continue;
            if (p == rec_pad) { *pad_out = p; *idx_out = p * 2 + k; return s; }   /* the record's own pad */
            if (!best || (int)(s->t_end - best->t_end) > 0) { best = s; *pad_out = p; *idx_out = p * 2 + k; }
        }
    return best;
}

static void log_curve(u32 handle, u32 pad, const u32 *rec, volatile CapSlot *s, u32 n_out, const u8 *out) {
    u32 newest = 0;
    for (u32 i = 0; i < CURVELOG_N; i++) if (CURVELOG[i].seq > newest) newest = CURVELOG[i].seq;
    volatile CurveLogEntry *e = &CURVELOG[(newest + 1u) % CURVELOG_N];
    e->seq = 0;
    e->handle = (u16)handle; e->n_out = (u16)n_out; e->pad = (u16)pad; e->rec_pad = (u16)(rec[0] >> 16);
    e->pitch = (u16)(rec[4] & 0xFFFFu); e->len_steps = (u16)(rec[2] / 960u);
    e->n_in = s ? (u16)s->n : 0; e->shift = s ? (u16)s->shift : 0;
    e->dur_ms = s ? s->t_end - s->t0 : 0; e->len_ticks = rec[2];
    for (u32 j = 0; j < 32; j++) e->first[j] = out && j < n_out ? out[j] : 0;
    e->seq = newest + 1u;
}

static void attach_from(u32 *rec, volatile CapSlot *s, u32 pad, u32 idx) {
    volatile CurveState *c = curves();
    if (!c || !s || c->next >= CURVE_HANDLES) {
        if (c) c->misses++;
        log_curve(0, pad, rec, s, 0, 0);
        return;
    }
    u32 n_in = s->n, n_out = (rec[2] + 59u) / 60u;               /* 16 per step at 960 ticks/step */
    if (n_out < 1u) n_out = 1u;
    if (n_out > 4095u) n_out = 4095u;
    if (c->used + 2u + n_out > CURVE_POOL_BYTES) { c->misses++; log_curve(0, pad, rec, s, 0, 0); return; }
    u8 *dst = (u8 *)(c->pool + c->used);
    dst[0] = (u8)n_out; dst[1] = (u8)(n_out >> 8);
    for (u32 i = 0; i < n_out; i++) {                              /* linear resample */
        u32 pos = n_out > 1u ? i * (n_in - 1u) * 256u / (n_out - 1u) : 0u, j = pos >> 8, f = pos & 255u;
        u32 a = s->s[j], b = j + 1u < n_in ? s->s[j + 1u] : a;
        dst[2 + i] = (u8)((a * (256u - f) + b * f) >> 8);
    }
    u32 h = c->next++;
    ((volatile u32 *)c->table)[h] = c->used;
    c->used += (2u + n_out + 3u) & ~3u;
    CLAIMED[idx] = s->seq;
    c->claims++;
    rec[5] = step_make(step_cond(rec[5]), 0, STEP_DIV_DEFAULT, 0, step_locks(rec[5]), h);
    log_curve(h, pad, rec, s, n_out, dst + 2);
}

/* M2-1 diagnosis: who inserts notes? The last 8 distinct callers (return
 * addresses) with counts, and a total; printed by the black box at save. */
static void log_inserter(u32 lr, const u32 *rec) {
    volatile InsLog *g = INSLOG;
    if (g->magic != INSLOG_MAGIC || g->gen != boot_gen()) {
        for (u32 i = 0; i < 8; i++) { g->lr[i] = 0; g->count[i] = 0; }
        g->total = 0; g->gen = boot_gen(); g->magic = INSLOG_MAGIC;
    }
    g->total++;
    g->last_kind = rec[0];
    for (u32 i = 0; i < 8; i++) {
        if (g->lr[i] == (lr & ~1u)) { g->count[i]++; return; }
        if (!g->lr[i]) { g->lr[i] = lr & ~1u; g->count[i] = 1; return; }
    }
}

/* Melodic live recording (FUN_081373EE): note-on inserts the note with length
 * 0 (insert returns to MEL_NOTE_ON), note-off sets its length through the
 * model update 0x0812E0CE (returns to MEL_NOTE_OFF). The record carries no pad,
 * so note-on binds the note's pitch to the oldest held pad not yet bound
 * (note-ons arrive in press order, so chords map pad by pad) and note-off
 * attaches that slot's curve. */
#define MEL_NOTE_ON 0x081377CEu
#define MEL_NOTE_OFF 0x081378F8u
static volatile CapBlock *cap_block4(void) {
    u32 b = CAPCFG->block;
    return CAPCFG->magic == CAPCFG_MAGIC && CAPCFG->check == ~b && b ? (volatile CapBlock *)b : 0;
}
static void bind_pitch(const u32 *rec) {
    volatile CapBlock *b = cap_block4();
    if (!b || (rec[0] & 0xFFu) != 1u || drum_track_selected()) return;
    volatile CapSlot *best = 0; u32 bp = 16;
    for (u32 p = 0; p < 16; p++)
        for (u32 k = 0; k < 2; k++) {
            volatile CapSlot *s = &b->slot[p][k];
            if (s->done || !s->n) continue;
            int taken = 0;
            for (u32 i = 0; i < 16; i++) if (BINDS[i].pad == p && BINDS[i].seq == s->seq && BINDS[i].pitch) taken = 1;
            if (taken) continue;
            if (!best || (int)(s->t0 - best->t0) < 0) { best = s; bp = p; }   /* oldest: note-ons arrive in press order */
        }
    if (!best) return;
    volatile Bind *e = &BINDS[BINDS_NEXT++ & 15u];
    e->pitch = (rec[4] & 0xFFFFu) | 0x10000u; e->pad = bp; e->seq = best->seq;
}
static int at_rec_off(void);
__attribute__((used, retain)) void curve_update(u32 *rec, u32 lr) {
    if ((lr & ~1u) != MEL_NOTE_OFF || (rec[0] & 0xFFu) != 1u || !rec[2] || drum_track_selected()) return;
    if (at_rec_off()) return;                       /* AT Rec Off on this track */
    volatile CapBlock *b = cap_block4();
    u32 pitch = (rec[4] & 0xFFFFu) | 0x10000u, pad = 16, idx = 0;
    volatile CapSlot *s = 0;
    for (u32 i = 0; b && i < 16; i++) {
        volatile Bind *e = &BINDS[i];
        if (e->pitch != pitch || e->pad >= 16u) continue;   /* the table survives resets */
        volatile CapSlot *c = &b->slot[e->pad][e->seq & 1u];
        e->pitch = 0;
        if (c->seq == e->seq && c->n) { s = c; pad = e->pad; idx = e->pad * 2u + (e->seq & 1u); }
        break;
    }
    if (!s) s = pick_slot(16, &pad, &idx);               /* no binding: the latest release */
    attach_from(rec, s, pad, idx);
}

__attribute__((used, retain)) void strike_insert(u32 *rec, u32 lr) {
    volatile PadState *ps;
    log_inserter(lr, rec);
    if ((lr & ~1u) == MEL_NOTE_ON) bind_pitch(rec);
    if ((rec[0] & 0xFFu) != 1u || strike_off() || !(ps = pads()) || !drum_track_selected()) return;
    /* Which pad made this note? The Sequence page's pads are the 16 steps of the
     * shown page (as in the Step view); live entry uses the drum's own pad;
     * otherwise the freshest unused strike of any pad (one hit, any layout). */
    int t = (int)rec[1];
    u32 cand[2] = {((u32)((t - offset_of(rec[1])) / 960)) & 15u, rec[0] >> 16}, pad = 16, vel = 0;
    for (u32 k = 0; k < 2 && !vel; k++)
        if (cand[k] < 16 && (vel = fresh_vel(ps, cand[k]))) pad = cand[k];
    for (u32 p = 0, best = 0; !vel && p < 16; p++) {
        if (!fresh_vel(ps, p)) continue;
        u32 age = STRIKES->seq[p] == ps->seen[p] ? UI_TICK_MS - ps->seen_ms[p] + 1u : 0u;   /* unseen = newest */
        if (pad == 16 || age < best) { pad = p; best = age; }
    }
    if (pad < 16 && !vel) vel = fresh_vel(ps, pad);
    if (!vel) return;
    ps->used[pad] = STRIKES->seq[pad];
    rec[4] = (rec[4] & 0xFFFFu) | (vel << 16);
    rec[5] = with_locks(rec[5], step_locks(rec[5]) | LOCK_VEL);
}


/* ---- M2-3b: load curves from the sidecar (format in diag_shared.h) ---------
 * doc_begin runs at the entry of stock's document reader (0x08130A18, path in
 * r0) for every document it reads (projects, patches, PROJ browsing): it only
 * remembers the path. The first curve note of that document then reads
 * ...\curves.bnt next to a ...\project.xml through the 4 KB SDRAM buffer (the
 * SD DMA cannot reach D2) and decodes every curve into the pool with
 * consecutive handles; index i maps to base + i - 1. Same task as stock's load. */
#define F_OPEN(f, p, m) FN(0x08120148, u32 (*)(void *, const char *, u32))((f), (p), (m))
#define F_READ(f, b, n, d) FN(0x08120464, u32 (*)(void *, void *, u32, u32 *))((f), (b), (n), (d))
#define F_CLOSE(f) FN(0x08120974, u32 (*)(void *))((f))

__attribute__((used, retain)) void doc_begin(const char *path) {
    volatile LoadState *ls = LOADSTATE;
    u32 seq = ls->magic == LOADSTATE_MAGIC ? ls->doc_seq + 1u : 1u;
    ls->magic = 0;
    u32 n = 0;
    while (path && path[n] && n < sizeof(ls->path) - 12u) { ls->path[n] = path[n]; n++; }
    ls->path[n] = 0;
    ls->doc_seq = seq; ls->attempted = 0; ls->base = 0; ls->count = 0; ls->status = 0;
    ls->magic = LOADSTATE_MAGIC;
}

static volatile AtSet *at_set(void);
static void at_defaults(void);
static void at_publish(void);
typedef struct { u32 *file; volatile u8 *buf; u32 n, pos, eof; } Reader;
static int rd_byte(Reader *r) {
    if (r->pos == r->n) {
        if (r->eof) return -1;
        u32 got = 0;
        if (F_READ(r->file, (void *)r->buf, SIDECAR_WBUF, &got) || !got) { r->eof = 1; return -1; }
        r->n = got; r->pos = 0;
    }
    return r->buf[r->pos++];
}
static int rd_u16(Reader *r) {
    int a = rd_byte(r), b = rd_byte(r);
    return a < 0 || b < 0 ? -1 : a | (b << 8);
}

static void load_sidecar(volatile LoadState *ls, volatile CurveState *c) {
    char name[160];
    u32 n = 0, cut = 0;
    while (ls->path[n] && n < sizeof(name) - 12u) {
        name[n] = ls->path[n];
        if (name[n] == '\\' || name[n] == '/') cut = n + 1u;
        n++;
    }
    static const char want[] = "project.xml", side[] = "curves.bnt";
    for (u32 i = 0; i < sizeof(want) - 1u; i++) {
        char ch = cut + i < n ? name[cut + i] : 0;
        if (ch >= 'A' && ch <= 'Z') ch = (char)(ch + 32);
        if (ch != want[i]) { ls->status = 1; return; }
    }
    for (u32 i = 0; i < sizeof(side); i++) name[cut + i] = side[i];
    at_defaults();                                  /* a project's own settings, or the defaults */
    mod_defaults();                                 /* F2a: likewise the External modulation */
    /* M2-4: a project load replaces every note, so no earlier curve can still be
     * referenced: start the pool over (it grew by a whole project per load).
     * Nothing referenced is moved; players still holding old data are dropped
     * by the M7 once the transport stops (late). */
    c->used = 0; c->next = 1; c->nsave = 0;
    u32 file[22];
    for (u32 i = 0; i < 22; i++) file[i] = 0;
    if (F_OPEN(file, name, 0x01u)) { ls->status = 2; return; }
    Reader r = {file, (volatile u8 *)c->wbuf, 0, 0, 0};
    int m0 = rd_byte(&r), m1 = rd_byte(&r), m2 = rd_byte(&r), m3 = rd_byte(&r);
    int ver = rd_u16(&r), count = rd_u16(&r);
    if (m0 != 'B' || m1 != 'N' || m2 != 'T' || m3 != 'C' || ver != 1 || count < 0) {
        F_CLOSE(file); ls->status = 3; return;
    }
    ls->base = c->next;
    ls->status = 4;
    for (int k = 0; k < count; k++) {
        int cn = rd_u16(&r), len = rd_u16(&r);
        if (cn < 0 || len < 0) { ls->status = 3; break; }
        if (cn > 4095 || c->next >= CURVE_HANDLES || c->used + 2u + (u32)cn + 4u > CURVE_POOL_BYTES) {
            ls->status = 5; break;
        }
        u8 *dst = (u8 *)(c->pool + c->used);
        u32 got = 0, prev = 0x100, bad = 0;
        for (int b = 0; b < len; b++) {                 /* decode; never past cn samples */
            int v = rd_byte(&r);
            if (v < 0) { ls->status = 3; break; }
            if (bad) continue;
            if (v < 0x80) {
                if (got < (u32)cn) dst[2 + got++] = (u8)v;
                prev = (u32)v;
            } else if (v < 0xC0) {
                if (prev > 0x7Fu) { bad = 1; continue; }
                for (u32 q = 0; q <= ((u32)v & 0x3Fu) && got < (u32)cn; q++) dst[2 + got++] = (u8)prev;
            } else {
                if (prev > 0x7Fu) { bad = 1; continue; }
                int p1 = (int)prev + (((v >> 3) & 7) - 4), p2 = p1 + ((v & 7) - 4);
                if (p1 < 0 || p1 > 127 || p2 < 0 || p2 > 127) { bad = 1; continue; }
                if (got < (u32)cn) dst[2 + got++] = (u8)p1;
                if (got < (u32)cn) dst[2 + got++] = (u8)p2;
                prev = (u32)p2;
            }
        }
        if (ls->status == 3) break;
        u32 ok = !bad && got == (u32)cn && cn > 0;     /* a damaged curve keeps its slot, plays nothing */
        dst[0] = (u8)(ok ? cn : 0); dst[1] = (u8)(ok ? (cn >> 8) : 0);
        ((volatile u32 *)c->table)[c->next] = c->used;
        c->used += (2u + (ok ? (u32)cn : 0u) + 3u) & ~3u;
        c->next++;
        ls->count++;
    }
    if (ls->status == 4) {                          /* optional trailer: "TRKS" u16 n, rec_off[n], out_mode[n] */
        int t0 = rd_byte(&r), t1 = rd_byte(&r), t2 = rd_byte(&r), t3 = rd_byte(&r), tn = rd_u16(&r);
        if (t0 == 'T' && t1 == 'R' && t2 == 'K' && t3 == 'S' && tn > 0) {
            volatile AtSet *a = at_set();
            for (int i = 0; i < tn; i++) { int v = rd_byte(&r); if (i < 16 && v >= 0) a->rec_off[i] = (u8)(v ? 1 : 0); }
            for (int i = 0; i < tn; i++) { int v = rd_byte(&r); if (i < 16 && v >= 0) a->out_mode[i] = (u8)(v <= 2 ? v : 0); }
            /* F2a: optional "MODS" u16 tracks, u16 record size, then the records */
            int m0 = rd_byte(&r), m1 = rd_byte(&r), m2 = rd_byte(&r), m3 = rd_byte(&r), mn = rd_u16(&r), sz = rd_u16(&r);
            volatile ModCfg *mc = mod_cfg_block();
            if (m0 == 'M' && m1 == 'O' && m2 == 'D' && m3 == 'S' && mn > 0 && sz > 0 && mc) {
                mod_write_begin(mc);
                for (int t = 0; t < mn; t++)
                    for (int i = 0; i < sz; i++) {
                        int v = rd_byte(&r);
                        if (v < 0) break;
                        if (t < (int)MOD_TRACKS && i < (int)sizeof(ModTrack)) ((volatile u8 *)&mc->t[t])[i] = (u8)v;
                    }
                mod_write_end(mc);
            }
        }
    }
    F_CLOSE(file);
}

static void doc_first_note(void) {
    volatile LoadState *ls = LOADSTATE;
    if (ls->magic != LOADSTATE_MAGIC || ls->attempted) return;
    ls->attempted = 1;
    volatile CurveState *c = curves();
    if (!c || !c->wbuf) { ls->status = 9; return; }
    load_sidecar(ls, c);
    AT_DEST->magic = 0;                             /* M7: defaults until published */
    at_set()->pending = 1;                          /* publish from the UI tick, never mid-load:
                                                       v3.30 walked the track list here and crashed
                                                       every autoload (restart loop) */
}

static u32 curve_for_index(u32 idx) {
    volatile LoadState *ls = LOADSTATE;
    if (ls->magic != LOADSTATE_MAGIC) return 0;
    doc_first_note();
    return idx && idx <= ls->count ? ls->base + idx - 1u : 0u;
}


/* ---- M2-5: per-track aftertouch settings ------------------------------------
 * Piano roll, Sequence view: encoder 5 "AT Rec" (Off/On), 6 "AT Out"
 * (Off/Poly/Channel) for the selected track. Recording checks AT Rec when a
 * curve attaches; the M7 checks AT Out by the note's MIDI port/channel in the
 * published destination table. Saved in the project's sidecar (TRKS). */
#define PROJECT_DIRTY 0x36C4u     /* app byte: project modified (Save enabled) */
#define PROJECT_TOUCH 0x0813130Cu /* (app): stock's refresh after an edit */
#define AT_SLOT_REC 4u
#define AT_SLOT_OUT 5u
static u32 sel_track(void) { return FN(0x0812DC74u, u32 (*)(void *, u32))((u8 *)MODEL + 4, 0x131); }

static volatile AtSet *at_set(void) {
    volatile AtSet *a = AT_SET;
    if (a->magic != AT_SET_MAGIC || a->gen != boot_gen()) {
        for (u32 i = 0; i < 16; i++) { a->rec_off[i] = 0; a->out_mode[i] = 0; }
        a->pending = 1; a->gen = boot_gen(); a->magic = AT_SET_MAGIC;
    }
    return a;
}
static void at_defaults(void) {
    volatile AtSet *a = at_set();
    for (u32 i = 0; i < 16; i++) { a->rec_off[i] = 0; a->out_mode[i] = 0; }
}
static int at_rec_off(void) {
    u32 t = sel_track();
    return t < 16u && at_set()->rec_off[t];
}

/* Each track's MIDI out port/channel (registry keys 0x0F/0x10 on its model
 * object) with its send mode, for the M7. */
static void at_publish(void) {
    volatile AtSet *a = at_set();
    volatile AtDestTable *t = AT_DEST;
    t->magic = 0;
    for (u32 i = 0; i < 8; i++) {
        u32 h = 0; void *obj = 0;
        t->d[i].valid = 0;
        if (!FN(0x08133960u, u32 (*)(void *, u32, u32 *))(MODEL, i, &h)) continue;
        if (!FN(0x0812D80Eu, u32 (*)(void *, u32, void **))((void *)W(MODEL, 0), h, &obj) || !obj) continue;
        t->d[i].port = (u8)FN(0x0812DC74u, u32 (*)(void *, u32))(obj, 0x0F);
        t->d[i].ch = (u8)FN(0x0812DC74u, u32 (*)(void *, u32))(obj, 0x10);
        t->d[i].mode = a->out_mode[i];
        t->d[i].valid = 1;
    }
    t->seq++;
    t->magic = AT_DEST_MAGIC;
    a->pending = 0;
}

/* F1: the External modulation settings, once per boot: an SDRAM block (32-byte
 * aligned: the M7 invalidates it by cache line) published in the D2 header with
 * a check word. Empty settings until F2 edits them; a modtest build
 * (BE_ENABLE=modtest) routes LFO 1 -> Mod Wheel (Pos Tri, 1/4), Env 1 -> CC 74 and
 * LFO 2 -> Pitch Bend (Sine, 1 bar, 15 %) on every External track. */
static void mod_cfg(void) {
    volatile ModHead *h = MODHEAD;
    if (h->magic == MODHEAD_MAGIC && h->gen == boot_gen() && h->check == ~h->cfg) return;
    if (!boot_gen() || h->tried == boot_gen() + 1u) return;   /* once per boot, success or not */
    h->tried = boot_gen() + 1u;
    h->magic = 0;
    u32 need = sizeof(ModCfg) + 32u, cur = BUMP_CUR, end = BUMP_END;
    if (!boot_gen() || end <= cur || end - cur < need + 192u) return;
    u32 p = FN(BUMP_ALLOC, u32 (*)(u32))(need);
    if (p != cur) return;
    volatile ModCfg *c = (volatile ModCfg *)((p + 31u) & ~31u);
    for (u32 i = 0; i < sizeof(ModCfg); i++) ((volatile u8 *)c)[i] = 0;
    c->gen = boot_gen(); c->seq = 2; c->magic = MODHEAD_MAGIC;
    h->cfg = (u32)c; h->check = ~(u32)c; h->gen = boot_gen(); h->magic = MODHEAD_MAGIC;
    mod_defaults();                                 /* nothing, or the modtest routing */
}

/* ---- F2a: LFO / envelope rows on the External INST page -----------------------
 * The External view (vtable 0x0819DB08) has one 8-encoder bar (view+0x3D0),
 * filled by FUN_08147138 from a descriptor {u32 ctx[8]; u16 key[8]; u32 val[8]}
 * through 0x08170394. A header button cycles the bar between stock's Main row and
 * our rows LFO 1, LFO 2, Env 1, Env 2, built from stock's own LFO / envelope keys
 * (their titles, lists and display formats) plus our Loop list. The builder hook
 * fills our rows; the view's event hook takes their encoder turns (0xCF) and the
 * button (ROWS_ACTION). Edits go to the selected track's ModTrack (odd seq while
 * writing, so the M7 re-reads it). */
#define EXT_BAR 0x3D0u
#define UI_POST 0x0816FC34u        /* (view, event): bubbles up the view tree */
#define BAR_SET 0x08170394u
#define EXT_BUILD 0x08147138u
#define K_LFO_SHAPE 0x4Au
#define K_LFO_RATE 0x4Bu
#define K_LFO_DEPTH 0x4Cu
#define K_LFO_SRATE 0x4Fu
#define K_ENV_A 0x1Eu
#define K_ENV_D 0x1Fu
#define K_ENV_S 0x20u
#define K_ENV_R 0x21u
#define ROWS 5u
static const char *const ROW_NAMES[ROWS] = {"Main", "LFO 1", "LFO 2", "Env 1", "Env 2"};

static volatile ModCfg *mod_cfg_block(void) {
    volatile ModHead *h = MODHEAD;
    u32 c = h->cfg;
    if (h->magic != MODHEAD_MAGIC || h->check != ~c || c < 0xC0000000u || c >= 0xC8000000u || (c & 31u)) return 0;
    volatile ModCfg *m = (volatile ModCfg *)c;
    return m->magic == MODHEAD_MAGIC ? m : 0;
}
static volatile ModTrack *mod_track(void) {
    volatile ModCfg *m = mod_cfg_block();
    u32 t = sel_track();
    return m && t < MOD_TRACKS ? &m->t[t] : 0;
}
static u32 row_now(void) { u32 r = MODHEAD->row; return r < ROWS ? r : 0u; }

/* The keys and values of row r (1-4) for track t. */
static void row_desc(u32 r, volatile ModTrack *t, u16 *key, u32 *val) {
    for (u32 i = 0; i < 8; i++) { key[i] = 0; val[i] = 0; }
    if (r == 1u || r == 2u) {
        volatile ModLfo *l = &t->lfo[r - 1u];
        key[0] = K_LFO_SHAPE; val[0] = l->shape < 9u ? l->shape : 0u;
        key[1] = l->sync ? K_LFO_SRATE : K_LFO_RATE; val[1] = l->sync ? (l->srate < 15u ? l->srate : 6u) : l->rate;
        key[3] = K_LFO_DEPTH; val[3] = l->depth;
        if (claim()) {
            key[2] = SU->key[7]; val[2] = l->sync ? 1u : 0u;        /* Beat Sync: Off / On */
            key[4] = SU->key[8]; val[4] = l->restart ? 1u : 0u;     /* Triggered: Off / On */
        }
    } else if (r == 3u || r == 4u) {
        volatile ModEnv *e = &t->env[r - 3u];
        key[0] = K_ENV_A; val[0] = e->a; key[1] = K_ENV_D; val[1] = e->d;
        key[2] = K_ENV_S; val[2] = e->s; key[3] = K_ENV_R; val[3] = e->r;
        if (claim()) { key[4] = SU->key[6]; val[4] = e->loop <= 33u ? e->loop : 0u; }
    }
}

/* Builder hook (view): 1 = our row filled the bar, 0 = stock builds Main. */
__attribute__((used, retain)) int ext_build(void *v) {
    MODHEAD->view = (u32)v; MODHEAD->page = MODEL_PAGE;
    u32 r = row_now();
    volatile ModTrack *t = mod_track();
    if (!r || !t) return 0;
    struct { u32 ctx[8]; u16 key[8]; u32 val[8]; } d;
    row_desc(r, t, d.key, d.val);
    for (u32 i = 0; i < 8; i++) d.ctx[i] = W(v, 0x34);
    FN(BAR_SET, void (*)(void *, void *))((u8 *)v + EXT_BAR, &d);
    return 1;
}

static void mod_write_begin(volatile ModCfg *m) { m->seq |= 1u; }
static void mod_write_end(volatile ModCfg *m) { m->seq = (m->seq | 1u) + 1u; }

/* Event hook (view, ev): 1 = ours (handled), 0 = stock's. */
__attribute__((used, retain)) int ext_event(void *v, const u32 *ev) {
    u32 type = ev[0] & 0xFFFFu;
    if (type == MOD_ACTION) {                       /* open the Mod Matrix as stock's "Modulation" menu item does */
        u32 ctx = W(v, 0x34);
        MODHEAD->mx_ctx = ctx;
        struct { u16 type, pad; u32 a, b, c, d; } m = {0xA8u, 0, ctx, 0, 0, 0};
        FN(UI_POST, void (*)(void *, void *))(v, &m);   /* the matrix view takes this track */
        m.type = 0x0Au;
        FN(UI_POST, void (*)(void *, void *))(v, &m);   /* page change (as the INST dashboard posts) */
        return 1;
    }
    if (type == ROWS_ACTION) {
        MODHEAD->row = (row_now() + 1u) % ROWS;
        FN(EXT_BUILD, void (*)(void *))(v);            /* refill (our hook or stock's Main) */
        return 1;
    }
    u32 r = row_now();
    if (type != 0xCFu || !r) return 0;
    volatile ModCfg *m = mod_cfg_block();
    volatile ModTrack *t = mod_track();
    if (!m || !t) return 0;
    u32 key = ev[2], value = ev[3];
    u16 keys[8]; u32 vals[8];
    row_desc(r, t, keys, vals);
    int mine = 0;
    for (u32 i = 0; i < 8; i++) if (keys[i] && keys[i] == key) mine = 1;
    if (!mine) return 0;
    mod_write_begin(m);
    if (r <= 2u) {
        volatile ModLfo *l = &t->lfo[r - 1u];
        if (key == K_LFO_SHAPE) l->shape = (u8)(value < 9u ? value : 8u);
        else if (key == K_LFO_RATE) l->rate = (u16)(value > 1000u ? 1000u : value);
        else if (key == K_LFO_SRATE) l->srate = (u8)(value < 15u ? value : 14u);
        else if (key == SU->key[7]) l->sync = value != 0;
        else if (key == K_LFO_DEPTH) l->depth = (u16)(value > 1000u ? 1000u : value);
        else if (key == SU->key[8]) l->restart = value != 0;
    } else {
        volatile ModEnv *e = &t->env[r - 3u];
        u16 v16 = (u16)(value > 1000u ? 1000u : value);
        if (key == K_ENV_A) e->a = v16; else if (key == K_ENV_D) e->d = v16;
        else if (key == K_ENV_S) e->s = v16; else if (key == K_ENV_R) e->r = v16;
        else e->loop = (u8)(value <= 33u ? value : 33u);
    }
    mod_write_end(m);
    ext_build(v);                                   /* show the new value (and Rate <-> Sync Rate) */
    if (*(volatile u32 *)HEADER_ROOT == HEADER_ROOT_VTABLE) HEADER_ROOT[0x117C] = 1;
    return 1;
}

/* Header (every UI tick): our button while the External INST page is shown. */
static void ext_toolbar(void *out, int *changed) {
    volatile ModHead *h = MODHEAD;
    if (h->magic != MODHEAD_MAGIC || !h->view || MODEL_PAGE != (u8)h->page) return;
    u32 r = row_now();
    *changed |= header_action_lit(out, ROWS_ACTION, ROW_NAMES[r], r != 0u);
    *changed |= header_action_lit(out, MOD_ACTION, "Mod", 0);
}

/* ---- F2b: stock's Mod Matrix page for External tracks ----------------------------
 * Opened by our "Mod" button with the External track's context (MODHEAD->mx_ctx).
 * For that context the matrix view (vtable 0x0819DE20) gets 16 rows of MIDI
 * destinations instead of a synth's parameters: private row keys 0xF000+i (no
 * registry keys), names and the CC column from the row's destination, our
 * sources (stock ids: None, LFO 1-2, Env 1-2, Velocity, Pressure, Key). Routing
 * reads (0x08131244) and edits (0x0814BFF0) for those keys use the selected
 * track's ModTrack. Encoder 8 is "Dest" (stock's matrix uses 7). Back: INST. */
#define MX_ROWS 16u
#define MX_KEY 0xF000u
#define MX_LIST 0x2970u            /* view: the row list widget */
#define MX_LINE 0x86B5Cu           /* view: selected row (-1 none) */
#define MX_CTX 0x8AC44u
#define MX_DESC 0x8ABF4u           /* view: bar descriptor {ctx[8]; key[8]; val[8]} */
static const u16 MX_SOURCES[8] = {0x00, 0x04, 0x05, 0x01, 0x02, 0x0D, 0x20, 0x0E};
static const u8 MX_TO_OURS[8] = {MOD_SRC_NONE, MOD_SRC_LFO1, MOD_SRC_LFO2, MOD_SRC_ENV1, MOD_SRC_ENV2,
                                 MOD_SRC_VEL, MOD_SRC_PRESS, MOD_SRC_KEY};

static int mx_ours(void *v) { u32 c = MODHEAD->mx_ctx; return c && W(v, MX_CTX) == c; }

static u32 dest_index(volatile ModDest *d) {
    for (u32 i = 0; i < DEST_N; i++)
        if (DEST_TYPE[i] == d->type && (d->type != MOD_DEST_CC || DEST_NUM[i] == d->num)) return i;
    return 0;
}

static void mx_row(void *v, u32 i, volatile ModDest *d) {
    u32 k = dest_index(d);
    const char *s = k ? DEST_LABELS[k] : "---";
    volatile char *n = (volatile char *)v + 0x8AE04u + i * 16u;
    u32 j = 0;
    for (; s[j] && j < 15u; j++) n[j] = s[j];
    for (; j < 16u; j++) n[j] = 0;
    B(v, 0x8ADBCu + i) = d->type == MOD_DEST_CC ? d->num : 0;   /* the CC column (0 = blank) */
}

static void mx_redraw(void *v) {
    struct { u16 type, pad; u32 a, b, c, d; } e = {0xC3u, 0, 0, 0, 0, 0};
    FN(0x0814C568u, void (*)(void *))(v);              /* bar (our hook adds Dest) */
    FN(0x0814C7C4u, void (*)(void *, void *))(v, &e);  /* every row */
}

/* Row builder (view, &ctx): 1 = ours. */
__attribute__((used, retain)) int mx_build(void *v, const u32 *ctx) {
    u32 c = MODHEAD->mx_ctx;
    volatile ModTrack *t = mod_track();
    if (!c || !ctx || *ctx != c || !t) return 0;
    if (W(v, MX_CTX) != c) {                          /* another track: back to the top, as stock */
        FN(0x0816B9FAu, void (*)(void *, u32))((u8 *)v + MX_LIST, 0);
        FN(0x0816BB5Cu, void (*)(void *, u32))((u8 *)v + MX_LIST, 0);
    }
    W(v, MX_CTX) = c; W(v, 0x8AC48u) = c;
    B(v, 0x8AC4Cu) = 0x20;                            /* no stock instrument kind */
    W(v, 0x8ACC8u) = (u32)MX_SOURCES; B(v, 0x8AC4Eu) = 8; B(v, 0x8AC4Du) = MX_ROWS;
    W(v, 0x8ADF8u) = sel_track(); W(v, 0x8ADFCu) = 0;
    for (u32 i = 0; i < MX_ROWS; i++) {
        H(v, 0x8AC50u + i * 2u) = (u16)(MX_KEY + i);
        W(v, 0x8ACCCu + i * 4u) = c;
        mx_row(v, i, &t->d[i]);
    }
    if ((int)W(v, MX_LINE) < 0 || W(v, MX_LINE) >= MX_ROWS) W(v, MX_LINE) = 0;
    FN(0x0814C4FCu, void (*)(void *))(v);
    mx_redraw(v);
    return 1;
}

/* Routing read (key, slot, out): -1 = stock's, else found (out: amount, source id). */
__attribute__((used, retain)) int mx_route(u32 key, u32 slot, u32 *out) {
    if (key < MX_KEY || key >= MX_KEY + MX_ROWS) return -1;
    volatile ModTrack *t = mod_track();
    if (!t || slot > 2u || !out) return 0;
    volatile ModDest *d = &t->d[key - MX_KEY];
    u32 s = d->src[slot];
    if (!s || s >= MOD_SRC_N) return 0;
    out[0] = (u32)(int)d->amt[slot];
    out[1] = 0; out[2] = 0;
    ((volatile u16 *)out)[2] = MX_SOURCES[s];        /* ours -> stock id (same order) */
    return 1;
}

/* Matrix edit (view, ev {.., key, value}): 1 = ours. */
__attribute__((used, retain)) int mx_edit(void *v, const u32 *ev) {
    if (!mx_ours(v)) return 0;
    volatile ModCfg *m = mod_cfg_block();
    volatile ModTrack *t = mod_track();
    u32 line = W(v, MX_LINE), key = ev[2];
    int value = (int)ev[3];
    if (!m || !t || line >= MX_ROWS || key < 0x171u || key > 0x176u) return 1;
    u32 j = (key - 0x171u) / 2u;
    volatile ModDest *d = &t->d[line];
    mod_write_begin(m);
    if (!((key - 0x171u) & 1u)) {                     /* Source 1-3: list index */
        if (value >= 0 && value < 8) d->src[j] = MX_TO_OURS[value];
    } else {                                          /* Amount 1-3: -1000..1000 */
        d->amt[j] = (short)(value < -1000 ? -1000 : value > 1000 ? 1000 : value);
    }
    mod_write_end(m);
    mx_redraw(v);
    return 1;
}

/* After stock refreshed the bar: Dest on encoder 8 for the selected row. */
__attribute__((used, retain)) void mx_bar_after(void *v) {
    if (!mx_ours(v) || !claim()) return;
    volatile ModTrack *t = mod_track();
    u32 line = W(v, MX_LINE);
    if (!t || line >= MX_ROWS) return;
    H(v, MX_DESC + 0x20u + 7u * 2u) = SU->key[9];
    W(v, MX_DESC + 0x30u + 7u * 4u) = dest_index(&t->d[line]);
    W(v, MX_DESC + 7u * 4u) = W(v, MX_CTX);
    FN(BAR_SET, void (*)(void *, void *))((u8 *)v + 0x38u, (u8 *)v + MX_DESC);
}

/* Matrix events (view, ev): 1 = ours. */
__attribute__((used, retain)) int mx_event(void *v, const u32 *ev) {
    if (!mx_ours(v)) return 0;
    u32 type = ev[0] & 0xFFFFu;
    if (type == 2u) return 1;                         /* "Dashboard": not for External (use INST) */
    if (type != 0xCFu || !claim() || ev[2] != SU->key[9]) return 0;
    volatile ModCfg *m = mod_cfg_block();
    volatile ModTrack *t = mod_track();
    u32 line = W(v, MX_LINE), k = ev[3];
    if (!m || !t || line >= MX_ROWS) return 1;
    if (k >= DEST_N) k = DEST_N - 1u;
    volatile ModDest *d = &t->d[line];
    mod_write_begin(m);
    d->type = DEST_TYPE[k]; d->num = DEST_NUM[k];
    mod_write_end(m);
    mx_row(v, line, d);
    mx_redraw(v);
    return 1;
}

/* Project load: a project's own settings, or none (modtest: the test routing). */
static void mod_defaults(void) {
    volatile ModCfg *m = mod_cfg_block();
    if (!m) return;
    mod_write_begin(m);
    for (u32 i = 0; i < sizeof(m->t); i++) ((volatile u8 *)m->t)[i] = 0;
#ifndef BE_NO_MODTEST
    for (u32 t = 0; t < MOD_TRACKS; t++) {
        volatile ModTrack *x = &m->t[t];
        x->lfo[0].shape = 3; x->lfo[0].sync = 1; x->lfo[0].srate = 6; x->lfo[0].depth = 1000;
        x->lfo[1].shape = 4; x->lfo[1].sync = 1; x->lfo[1].srate = 3; x->lfo[1].depth = 1000;
        x->env[0].a = 100; x->env[0].d = 300; x->env[0].s = 0; x->env[0].r = 300;
        x->d[0].type = MOD_DEST_CC; x->d[0].num = 1; x->d[0].src[0] = MOD_SRC_LFO1; x->d[0].amt[0] = 1000;
        x->d[1].type = MOD_DEST_CC; x->d[1].num = 74; x->d[1].src[0] = MOD_SRC_ENV1; x->d[1].amt[0] = 1000;
        x->d[2].type = MOD_DEST_PB; x->d[2].src[0] = MOD_SRC_LFO2; x->d[2].amt[0] = 150;
    }
#endif
    mod_write_end(m);
}

static const u8 OUT_TO_INDEX[3] = {1, 0, 2};   /* mode Poly/Off/Channel -> list Off/Poly/Channel */
static const u8 INDEX_TO_OUT[3] = {AT_OFF, AT_POLY, AT_CHAN};

static void at_slot(void *v, u32 s, u32 which, u32 value) {
    if (H(v, 0x11BCC0 + s * 2) && H(v, 0x11BCC0 + s * 2) != SU->key[which]) return;   /* stock uses it */
    void *bar = (u8 *)v + 0x55F0, *slot = (u8 *)bar + 0x38 + s * 0x468;
    H(v, 0x11BCC0 + s * 2) = SU->key[which];
    W(v, 0x11BCD0 + s * 4) = value;
    W(v, 0x11BCA0 + s * 4) = W(v, 0x11CD00);
    FN(BAR_TEXT, void (*)(void *, u32, const char *))(bar, s, 0);
    FN(SLOT_SET, void (*)(void *, u32, int, u32))(slot, SU->key[which], (int)value, 1);
    FN(SLOT_TITLE, void (*)(void *, const char *))((u8 *)slot + 0x3C8, TITLES[which]);
}

static void at_header(void *v) {
    u32 t = sel_track();
    at_publish();                                   /* ports/channels may have changed on INST */
    if (t >= 16u) return;
    volatile AtSet *a = at_set();
    at_slot(v, AT_SLOT_REC, 4, a->rec_off[t] ? 0u : 1u);
    at_slot(v, AT_SLOT_OUT, 5, OUT_TO_INDEX[a->out_mode[t] <= 2 ? a->out_mode[t] : 0]);
}

static int at_event(void *v, const u32 *ev) {
    u32 t = sel_track();
    int value = (int)ev[3];
    if (t < 16u) {
        volatile AtSet *a = at_set();
        if (ev[2] == SU->key[4]) a->rec_off[t] = value <= 0;
        else a->out_mode[t] = INDEX_TO_OUT[value < 0 ? 0 : value > 2 ? 2 : value];
        at_publish();
        /* Mark the project modified so Save lights up, as stock's own edits do
         * (e.g. the recording handler: strb 1 -> app + 0x36C4, then 0x0813130C). */
        B(MODEL, PROJECT_DIRTY) = 1;
        FN(PROJECT_TOUCH, void (*)(void *))(MODEL);
    }
    FN(PIANO_HEADER, void (*)(void *))(v);
    return 1;
}
