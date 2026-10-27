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

typedef struct { u32 magic, gen, ok; u16 key[6]; u32 r[2]; } StepUI;   /* one M4-only line */
#define SU ((volatile StepUI *)0x3003C880u)
#define SU_MAGIC 0x32505453u /* "STP2": six keys since v3.30 */
_Static_assert(sizeof(StepUI) <= 32, "step UI state is one cache line");

static const char *const AT_REC_LABELS[2] = {"Off", "On"};
static const char *const AT_OUT_LABELS[3] = {"Off", "Poly", "Channel"};
static const char *const TITLES[6] = {"Condition", "Retrig", "Division", "Ramp", "AT Rec", "AT Out"};
static const char *const *const CHOICES[6] = {COND_LABELS, HITS_LABELS, DIV_LABELS, RAMP_LABELS, AT_REC_LABELS, AT_OUT_LABELS};
static const u32 COUNTS[6] = {COND_N + 1u, 16u, STEP_DIVS, RAMP_N, 2u, 3u};

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }

/* Claim six empty registry keys (from the top), once per boot. */
static int claim(void) {
    if (SU->magic != SU_MAGIC || SU->gen != boot_gen()) {
        SU->ok = 0; SU->r[0] = SU->r[1] = SU->r[2] = 0; SU->gen = boot_gen(); SU->magic = SU_MAGIC;
    }
    if (SU->ok) return 1;
    u32 r = REGISTRY;
    if (!r) return 0;
    u16 keys[6]; u32 n = 0;
    for (u32 k = REG_KEYS - 1; k >= 0x100 && n < 6; k--)
        if (!H(r + k * 32, 0)) keys[n++] = (u16)k;
    if (n < 6) return 0;
    for (u32 i = 0; i < 6; i++) {
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
        if (rid(a) != STRIKE_ACTION && rid(b) != STRIKE_ACTION) continue;
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
__attribute__((used, retain)) int strike_toolbar(void *model, void *out, int changed) {
    (void)model;
    strike_init();
    volatile PadState *ps = pads();
    for (u32 p = 0; ps && p < 16; p++)
        if (STRIKES->seq[p] != ps->seen[p]) { ps->seen[p] = STRIKES->seq[p]; ps->seen_ms[p] = UI_TICK_MS; }
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
    at_publish();
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
#define AT_SLOT_REC 4u
#define AT_SLOT_OUT 5u
static u32 sel_track(void) { return FN(0x0812DC74u, u32 (*)(void *, u32))((u8 *)MODEL + 4, 0x131); }

static volatile AtSet *at_set(void) {
    volatile AtSet *a = AT_SET;
    if (a->magic != AT_SET_MAGIC || a->gen != boot_gen()) {
        for (u32 i = 0; i < 16; i++) { a->rec_off[i] = 0; a->out_mode[i] = 0; }
        a->gen = boot_gen(); a->magic = AT_SET_MAGIC;
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
    }
    FN(PIANO_HEADER, void (*)(void *))(v);
    return 1;
}
