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
static const u32 STOCK_DEFAULT[3] = {100u, 0u, 0u};   /* velocity 100, probability Always, offset 0 */

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
static u32 legacy_locks(const u32 *e) {   /* no lock data: anything not at stock's default is a lock */
    u32 l = 0;
    for (u32 f = 0; f < 3; f++)
        if (note_val(e, f) != STOCK_DEFAULT[f]) l |= LOCK_BIT[f];
    return l;
}
static u32 with_locks(u32 w, u32 locks) {
    return step_make(step_cond(w), step_hits(w), step_div(w), step_ramp(w), locks);
}

/* ---- project save / load ------------------------------------------------ */

__attribute__((used, retain)) void step_save(void *node, const void *rec) {
    if (B(rec, 0) != 1) return;
    u32 w = W(rec, 20);
    if (!step_valid(w)) return;
    void *a = FN(XML_ADD, void *(*)(void *, const char *))(node, ATTR);
    if (a) FN(XML_SET_U32, void (*)(void *, u32))(a, w);
}

__attribute__((used, retain)) void step_load(void *node, void *rec) {
    if (B(rec, 0) != 1) return;
    u32 w = 0, x = 0;
    void *a = FN(XML_FIND, void *(*)(void *, const char *))(node, ATTR);
    if (a && !FN(XML_GET_U32, int (*)(void *, u32 *))(a, &x) && step_valid(x)) w = x;
    u32 prob = B(rec, 1), cond = step_cond(w);
    if (prob >= 100 && prob <= 173) {               /* v2.4..v3.2 condition in the probability byte */
        if (!cond) cond = prob - 99;
        B(rec, 1) = 0;                              /* probability: Always */
    }
    u32 locks = step_locks(w);
    if ((w >> 24) != STEP_TAG) locks = legacy_locks((const u32 *)rec);   /* no lock data saved yet */
    W(rec, 20) = step_make(cond, step_hits(w), step_div(w), step_ramp(w), locks);
}

/* ---- registry keys -------------------------------------------------------- */

typedef struct { u32 magic, gen, ok; u16 key[4]; u32 r[3]; } StepUI;   /* one M4-only line */
#define SU ((volatile StepUI *)0x24048C80u)
#define SU_MAGIC 0x31505453u /* "STP1" */
_Static_assert(sizeof(StepUI) <= 32, "step UI state is one cache line");

static const char *const TITLES[4] = {"Condition", "Retrig", "Division", "Ramp"};
static const char *const *const CHOICES[4] = {COND_LABELS, HITS_LABELS, DIV_LABELS, RAMP_LABELS};
static const u32 COUNTS[4] = {COND_N + 1u, 16u, STEP_DIVS, RAMP_N};

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }

/* Claim four empty registry keys (from the top), once per boot. */
static int claim(void) {
    if (SU->magic != SU_MAGIC || SU->gen != boot_gen()) {
        SU->ok = 0; SU->r[0] = SU->r[1] = SU->r[2] = 0; SU->gen = boot_gen(); SU->magic = SU_MAGIC;
    }
    if (SU->ok) return 1;
    u32 r = REGISTRY;
    if (!r) return 0;
    u16 keys[4]; u32 n = 0;
    for (u32 k = REG_KEYS - 1; k >= 0x100 && n < 4; k--)
        if (!H(r + k * 32, 0)) keys[n++] = (u16)k;
    if (n < 4) return 0;
    for (u32 i = 0; i < 4; i++) {
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
    return step_make(cond, hits, div, ramp, step_locks(w));
}

/* ---- drum step sequencer header and encoders ---------------------------- */

static int drum_step_view(void *v) { return v && !B(v, 0x113E0) && W(v, 0x113E4) && W(v, 0x113DC); }

/* ---- defaults + locks (velocity / probability / offset) ------------------
 * A pad's default (per clip) is the value its unlocked notes share; stock's
 * "new step" values (view +0x114C0/C4/C8) hold it while editing. Stock's three
 * edit functions (0x0815C7C4/8AC/990) are wrapped: param_pre reads the old
 * default, stock runs, param_post then
 *   - nothing selected (stock changed the default): unlocked notes that had
 *     the old default take the new one; an unlocked note with another value
 *     (e.g. recorded live) is locked instead of overwritten;
 *   - steps selected (stock edited them): lock each where it now differs from
 *     the default, unlock where it equals it. */
static int matches(void *v, const u32 *e);
static const u32 DEFAULT_AT[3] = {0x114C0u, 0x114C4u, 0x114C8u};

static void publish(void *v) {
    FN(PUBLISH, void (*)(void *, void *, void *))(MODEL, (u8 *)v + 0x113E4, (void *)W(v, 0x113DC));
    FN(GRID, void (*)(void *))(v);
}

__attribute__((used, retain)) u32 param_pre(void *v, u32 f) { return f < 3 ? W(v, DEFAULT_AT[f]) : 0u; }

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

/* On switching clip or pad: stock's single "new step" defaults become this
 * pad's, i.e. the most common value among its unlocked notes. */
static void load_defaults(void *v) {
    void *container = (void *)W(v, 0x113DC);
    u32 pad = W(v, 0x11400), e[6];
    for (u32 f = 0; f < 3; f++) {
        u8 count[128];
        for (u32 i = 0; i < 128; i++) count[i] = 0;
        u32 any = 0;
        for (u32 j = 0; j < 511; j++) {
            if (!FN(NOTE_GET, int (*)(void *, u32, u32 *))(container, j, e)) break;
            if ((e[0] & 0xFFu) != 1u || (e[0] >> 16) != pad || (step_locks(e[5]) & LOCK_BIT[f])) continue;
            int x = (int)note_val(e, f);
            u32 bin = f == F_OFFSET ? (u32)((x + 470) / 10) : (u32)x;
            if (bin < 128 && count[bin] < 255) count[bin]++;
            any = 1;
        }
        if (!any) continue;
        u32 best = 0;
        for (u32 i = 1; i < 128; i++)
            if (count[i] > count[best]) best = i;
        W(v, DEFAULT_AT[f]) = f == F_OFFSET ? (u32)((int)best * 10 - 470) : best;
    }
}

__attribute__((used, retain)) void step_header_before(void *v) {
    if (!drum_step_view(v) || !claim()) return;
    for (u32 i = 0; i < 4; i++) DRUM_KEYS[4 + i] = SU->key[i];
    u32 seq = W(v, 0x113E4), pad = W(v, 0x11400);
    if (SU->r[0] != seq || SU->r[1] != pad || SU->r[2] != (u32)v) {
        SU->r[0] = seq; SU->r[1] = pad; SU->r[2] = (u32)v;
        load_defaults(v);
    }
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

__attribute__((used, retain)) void piano_header_after(void *v) {
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
__attribute__((used, retain)) int piano_event(void *v, const u32 *ev) {
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
