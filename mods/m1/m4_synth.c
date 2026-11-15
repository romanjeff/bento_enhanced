/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Synth UI on the M4 (Analog oscillators on Wavetable tracks; the drum page), docs/plan-native-props.md and
 * docs/plan-synths-168.md.
 *
 * BE4.40 (native properties, docs/plan-native-props.md): an oscillator's Type (Wavetable / Analog), Shape, Sync and Glide
 * are stock properties of the oscillator object itself (WT1 / WT2, type 6): build.py appends our four keys to the type-6
 * run of stock's property template (N1), and np_keys() gives them descriptors with real XML tokens inside stock's one
 * registry build (N2). Stock then creates, stores, saves (bento_osc_type, bento_shape, bento_sync, bento_glide on each
 * oscillator's <cell> params), loads, clones, copies, reads (CONTEXT_GET), edits (0xCF -> 0xD0) and forwards them to the
 * M7 with the oscillator's index (set_property), where dsp/analog.cpp stores them per oscillator (N3). Nothing here holds
 * Analog state: the BE4.22..BE4.31 table (AN4), its handle hooks (H1-H3), the XML hooks (X1, X2), the value hook (X3), the
 * menu entry (A3-A5) and the edit hook (A9) are gone for Analog. Stock never range-checks a value (a file is read raw), so
 * every reader here clamps, and Type is Analog only when it is exactly 1.
 * Sites (1.6.8; build.py writes them):
 *   N2     model init's bl REG_BUILD -> a wrapper: stock's builder, then np_keys (all four descriptors, or none)
 *   A6     the Wavetable page's key copy (bl memcpy) -> an_page_keys: Type in each oscillator row's free 4th slot, and the
 *          Analog layout (Shape instead of Position, Glide / Sync) for an oscillator that is Analog
 *   A7     the four pane hint texts (bl SLOT_TITLE, PANE_TEXT_1/3/5/6) -> an_pane_text: an Analog pane's hint is empty (the
 *          dial is its content). BE4.40 identity T2: the "No WAV" label sites (PANE_TEXT_2/4) are stock again: that label
 *          is a child of the Granular waveform widget, hidden on the Wavetable page (M)
 *   A10    the synth view's tick slot -> an_view_tick: puts each oscillator row's keys right for its Type (stock's own row
 *          bind rereads them, SYNTH_ROW_SHOW), sends an oscillator that just became Analog its other settings again (the M7
 *          keeps them only while it is Analog), re-titles the Analog panes (T1: the DSP load), marks a dial for a redraw
 *          (D2); before and after stock's tick, the value texts of the shown row (Y1 Type, V1 Shape)
 *   BE4.40 identity (docs/plan-be440-sync-identity.md track I): T0 the prefixes "WT1: " / "WT2: " and the mod destination
 *          format "WT%d: %s" become "OSC..." (data, build.py); T1 the Wavetable pane title (bl SLOT_TITLE at PANE_TITLE_SITE,
 *          an asm shim hands over the row's handle from stock's frame) -> an_pane_title; D1 the pane class's paint slot ->
 *          an_wave_paint (the Shape dial); B1' the 0x3F post of a double-tap on a Wavetable pane (BROWSE_POST_SITE, a shim
 *          with the oscillator's handle) -> an_browse: none for an Analog oscillator; V1 / Y1 the encoder widget's three
 *          value-text writes (bl SLOT_TITLE, VALUE_TEXT_1..3) -> an_value_text
 *   X6     m4_diag.c calls the entry table: slot 0 an_tick (every UI loop pass), slot 1 an_print (save time: the ANALOG and
 *          DRUMS lines of the black box)
 * The drum page (S6c-2, BE4.31; group drums, default-off on 1.6.8 since BE4.40, built only with BE_ENABLE=drums), after
 * bento-playground's K7 Kick UI (eu4_work/integration/kick_ui.c, 9fc7787, GPL-3.0-or-later), rewritten: the pad's knobs in
 * our own table (DK4, the slice), seven reviewed keys, no menu entry, no FatFS, no Mod Matrix. Its sites:
 *   D5     the instrument page's refresh entry -> dr_refresh: on a Kick pad the Config row shows the Kick's knobs
 *   D6     the page's event slot -> dr_page_event: encoder edits of the drum keys set the pad's knobs (never the model)
 *          the page's tick slot -> dr_view_tick: the CPU readout in the page's title label, and dr_heal
 *   X1-X3  instrument XML save / load -> an_save / an_load (bento_drum_engine, bento_drum_k1..k7), CONTEXT_GET's drum keys
 *   H1-H4  stock's object lifecycle (OBJ_CTOR, NODE_VT_CLONE, NODE_VT_DESTROY, CHILD_DUP) for DK4's handles
 * (X1-X3 and H1-H4 served both synths until BE4.31; since BE4.40 they are the drums' alone and only drum builds have them.)
 * Tasks: stock's project loads and saves run in its "pcmStreamer" task (command queue 0x0812B27C); everything of ours that
 * runs there is the drums' (an_load / an_save), which change DK4 only with interrupts masked. The rest runs in the UI task.
 * Integer code only (soft-float M4 build); strict-align on 1.6.8 like every M4 source. */
#include "diag_shared.h"
#include "synth_shared.h"

#define FN(a, t) ((t)((a) | 1u))
#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define H(p, o) (*(volatile u16 *)((u8 *)(p) + (o)))
#define B(p, o) (*(volatile u8 *)((u8 *)(p) + (o)))
#define MODEL ((void *)S_MODEL)
#define REGISTRY (*(volatile u32 *)S_REGISTRY)
#define TICK_MS (*(volatile u32 *)S_M4_TICK_MS)
#define M7HDR ((volatile SaM7 *)SA(SA_M7))
#define M4HDR ((volatile SaM4 *)SA(SA_M4))
#define STATS ((volatile SaStats *)SA(SA_STATS))

#ifdef BE_NO_ANALOG                                 /* a BE_DISABLE=analog bisect: N1-N3 and the page sites stay stock, */
#define ANALOG_ON 0                                 /* the code stays referenced and does nothing */
#else
#define ANALOG_ON 1
#endif
#if defined(BE_OMIT_DRUMS) || defined(BE_NO_DRUMS)  /* the drum page off (left out, or BE_DISABLE=drums) */
#define DRUMS_ON 0
#else
#define DRUMS_ON 1
#endif
#ifndef BE_OMIT_DRUMS
#define DRUMS_BUILT 1
#else
#define DRUMS_BUILT 0
#endif
#define HANDLE_CTR (*(volatile u32 *)S_OBJ_HANDLE_CTR)

/* ---- the synth UI's own state (diag_shared.h AN4_SLICE, 256 B, M4 only) ---------------------------------------
 * BE4.40: no Analog table any more (Analog lives in stock's objects); the block keeps the boot generation, np_keys'
 * answer, the CPU indicator (also the drum page's) and the black box counters. "AN50": a BE4.22..BE4.31 table left in the
 * slice by a warm reset is started again. */
#define AN4_MAGIC 0x30354E41u                /* "AN50" */
typedef struct { u32 view, h, key, t; } DialCache;
typedef struct {
    u32 magic, gen;                         /* gen: the config line's boot generation this block last saw */
    u32 keys;                                /* np_keys: 1 ok, 0x100 | taken bits, 0x200 no registry, 0 not run this boot */
    u32 keys_gen;                            /* the boot np_keys last ran in */
    u32 src;                                 /* the Wavetable page's stock key table (A6's memcpy source) */
    u32 heals, resends, page_rows;           /* rows put right by the tick; oscillators sent their settings again; A6 */
    u32 cpu_t, seen, sum, h3;                /* CPU indicator: last update, the LOAD counters (D0L) then */
    u32 over_t, cpu_pm, over, stats_blocks;  /* last block over budget, average LOAD, over-budget blocks with Analog */
    u32 label_t, cpu_ok, oscs, oscs_t;       /* the label's last update; the black box's Analog oscillator count */
    u32 last_h[2], last_t[2];                /* the synth page's oscillator rows as the tick last saw them: handle, Type */
    u32 browse, dials;                       /* B1': double-tap loads not opened on an Analog pane; D1: dials drawn
                                                (BE4.31..BE4.40 kept the label indicator's view / pane here: a warm reset
                                                leaves a stale cache, which only redraws a dial) */
    DialCache dial[2];                       /* D2: what each pane's dial last showed (key: DIAL_*), and when */
    u32 r34[30];
} An4;
_Static_assert(sizeof(An4) == AN4_SLICE_END - AN4_SLICE, "the synth UI state fills its 256 B");
#define AN4 ((volatile An4 *)SLICE(AN4_SLICE))

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }
__attribute__((unused)) static void dsb(void) { __asm volatile("dsb" ::: "memory"); }
static u32 lock(void) { u32 p; __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory"); return p; }
static void unlock(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }
static void bump(volatile u32 *c) { u32 p = lock(); (*c)++; unlock(p); }   /* a counter two tasks may move */

static volatile An4 *an4(void) {
    volatile An4 *a = AN4;
    if (a->magic != AN4_MAGIC) {             /* a cold start (slice garbage), an older layout, or the canary pattern */
        u32 p = lock();
        if (a->magic != AN4_MAGIC) {
            for (u32 i = 0; i < sizeof(An4) / 4u; i++) ((volatile u32 *)a)[i] = 0;
            a->magic = AN4_MAGIC;
        }
        unlock(p);
    }
    return a;
}

#if DRUMS_BUILT
/* ---- DK4 (S6c-2): the M4's drum knob table (diag_shared.h DK4_SLICE, 1152 B, M4 only) -------------------------------
 * Who holds the truth, as AN4: a pad's knob values live here, keyed by the pad's object handle (the instrument page's
 * cell, a child of its track node, whose M7 root carries the same handle at +0x0C), so a load works before the M7 has
 * taken the synth area. An entry: handle (0 = free), word ([3:0] engine id, [8] DK_USED), v0 = knobs 0..3, v1 = knobs
 * 4..7 (byte i = knob i, in the engine's units). A pad with no entry plays and shows its engine's defaults; an entry is
 * made by the first edit on the page or by a load that carries bento_drum_* attributes. Published to the synth area's
 * 64 knob lines (SA_DKTAB) by the UI task only, one line per entry. */
#define DK4_MAGIC 0x31344B44u                /* "DK41" */
#define DK4_N SA_DKTAB_N
typedef struct {
    u32 magic, gen, serial, pub, pub_m7gen, pub_boot;        /* as AN4 */
    u32 keys;                                                 /* the drum keys: 1 ok, 0x100 | taken key index + 1, 0 not yet */
    u32 carried, dups, dropped, forgot, clone_skips;          /* H2 (children of a cloned track), H4, H3, H1, H2 mismatches */
    u32 pages, edits, saves, loads, full, evicted, refused;   /* per boot: Kick pages shown, knob edits, XML, capacity */
    u32 d_blocks, d_sum, drum_pm, d_ok;                       /* the CPU readout's drum share (DSTATS differenced) */
    u32 view, label_t, heals;                                 /* the page the cache below is for; readout; row repairs */
    u32 c_cell, c_file, c_t, c_eng, r30, r31;                 /* dr_page_engine's last answer for (view, cell, file) */
    struct { u32 handle, word, v0, v1; } e[DK4_N];
} Dk4;
#if DRUMS_BUILT
_Static_assert(sizeof(Dk4) == DK4_SLICE_END - DK4_SLICE, "DK4 fills its 1152 B");
#endif
#define DK4 ((volatile Dk4 *)SLICE(DK4_SLICE))
#define KEY_DR S_KEY_DR_FIRST
#define DR_TYPE_CELL 1u                               /* the instrument page's cell: a pad, type 1 or 2 (0x081360D0 */
#define DR_TYPE_CELL2 2u                              /* gives both the same rows) */
static volatile Dk4 *dk4(void) {
    volatile Dk4 *d = DK4;
    if (d->magic != DK4_MAGIC) {             /* a cold start, or RAMCHK's pattern left by BE4.30 */
        u32 p = lock();
        if (d->magic != DK4_MAGIC) {
            for (u32 i = 0; i < sizeof(Dk4) / 4u; i++) ((volatile u32 *)d)[i] = 0;
            d->magic = DK4_MAGIC;
        }
        unlock(p);
    }
    return d;
}
/* The engines this build plays (dsp/drums.cpp ENGINES) and how the page knows a carrier: its file's base name, as the
 * carrier generator writes it (tools/carriers/gen_carriers.py). A renamed carrier plays the synth (the M7 tests the
 * sample itself) behind the sample page (plan section 8). */
typedef struct { u32 id; const char *file; const char *const *names; const u8 *defaults, *lo, *hi; } DrEngine;
static const char *const KICK_NAMES[DRUM_KEYS_N] = {"Tune", "Kick Dec", "Tone", "Accent", "Sweep", "Sweep Dec", "Dirt"};
/* ("Kick Dec", not "Decay": the bottom row's envelope Decay is on the same page, review rc29 S4) */
static const u8 KICK_DEF[DRUM_KNOBS] = KICK_DEFAULTS, KICK_MIN[DRUM_KNOBS] = KICK_LO, KICK_MAX[DRUM_KNOBS] = KICK_HI;
static const DrEngine ENGINES[] = {{ENGINE_KICK, "BE Kick.wav", KICK_NAMES, KICK_DEF, KICK_MIN, KICK_MAX}};
#define ENGINES_N (sizeof(ENGINES) / sizeof(ENGINES[0]))
static const DrEngine *dr_engine(u32 id) {
    for (u32 i = 0; i < ENGINES_N; i++) if (ENGINES[i].id == id) return &ENGINES[i];
    return 0;
}
static u32 knob_of(u32 v0, u32 v1, u32 j) { return ((j < 4u ? v0 : v1) >> (8u * (j & 3u))) & 0xFFu; }
static int dk_find(volatile Dk4 *d, u32 h) {
    if (!h) return -1;
    for (u32 i = 0; i < DK4_N; i++) if (d->e[i].handle == h && (d->e[i].word & DK_USED)) return (int)i;
    return -1;
}
/* A pad's values (its engine's defaults where it has no entry) into v[DRUM_KNOBS]; the entry's engine, else dflt. */
static u32 dk_values(u32 h, u32 dflt, u8 *v) {
    volatile Dk4 *d = dk4();
    u32 p = lock();
    int i = dk_find(d, h);
    u32 w = i < 0 ? 0u : d->e[i].word, v0 = i < 0 ? 0u : d->e[i].v0, v1 = i < 0 ? 0u : d->e[i].v1;
    unlock(p);
    u32 id = i < 0 ? dflt : w & 15u;
    const DrEngine *e = dr_engine(id);
    for (u32 j = 0; j < DRUM_KNOBS; j++) v[j] = i >= 0 ? (u8)knob_of(v0, v1, j) : e ? e->defaults[j] : 0u;
    return id;
}
/* Is h a pad of the live project ([MODEL]: its top-level objects' children)? A load's scratch, a save's snapshot and the
 * clipboard hold copies with other handles. */
static int dk_live(u32 h) {
    void *store = (void *)W(MODEL, 0);
    if (!h || !store) return 0;
    u32 n = W(store, S_STORE_COUNT);
    for (u32 i = 0; i < n && i < 12u; i++) {
        void *o = (void *)W(store, 4u * i);
        u32 k = o ? W(o, S_NODE_CHILD_N) : 0u;
        for (u32 c = 0; c < k && c < 0x244u; c++) {
            void *ch = (void *)W(o, S_NODE_CHILDREN + 4u * c);
            if (ch && W(ch, 0x10) == h) return 1;
        }
    }
    return 0;
}
/* Set (engine != 0) or clear (engine 0) the entry of h. A full table gives up its oldest copy (the lowest handle that is
 * not a pad of the live project), never a live pad; with none to give up the edit is lost and counted (full). */
static void dk_mark(u32 h, u32 engine, const u8 *v) {
    if (!h) return;
    volatile Dk4 *d = dk4();
    u32 v0 = 0, v1 = 0;
    for (u32 j = 0; engine && j < DRUM_KNOBS; j++) {
        if (j < 4u) v0 |= (u32)v[j] << (8u * j); else v1 |= (u32)v[j] << (8u * (j - 4u));
    }
    for (int pass = 0; pass < 2; pass++) {
        u32 victim = 0;
        if (pass)
            for (u32 k = 0; k < DK4_N; k++) {
                u32 x = d->e[k].handle;
                if (x && x != h && (!victim || x < victim) && !dk_live(x)) victim = x;
            }
        u32 p = lock();
        int i = dk_find(d, h);
        if (i < 0 && engine) {
            for (u32 k = 0; i < 0 && k < DK4_N; k++) if (!(d->e[k].word & DK_USED) || !d->e[k].handle) i = (int)k;
            for (u32 k = 0; i < 0 && victim && k < DK4_N; k++)
                if (d->e[k].handle == victim) { i = (int)k; d->evicted++; }
            if (i < 0 && !pass) { unlock(p); continue; }
            if (i < 0) d->full++;
        }
        if (i >= 0) {
            if (engine) { d->e[i].handle = h; d->e[i].v0 = v0; d->e[i].v1 = v1; d->e[i].word = DK_USED | (engine & 15u); }
            else { d->e[i].word = 0; d->e[i].handle = 0; }
            d->serial++;
        }
        unlock(p);
        return;
    }
}
/* H-hook helpers: does the table name h (unlocked: the common case is an empty table) */
static int dk_any(volatile Dk4 *d) {
    if (d->magic != DK4_MAGIC) return 0;
    for (u32 k = 0; k < DK4_N; k++) if (d->e[k].handle) return 1;
    return 0;
}
static void dk_copy(u32 from, u32 to, volatile u32 *count) {
    u8 v[DRUM_KNOBS];
    volatile Dk4 *d = DK4;
    u32 p = lock();
    int i = dk_find(d, from);
    unlock(p);
    if (i < 0 || !to) return;
    u32 id = dk_values(from, 0, v);
    dk_mark(to, id, v);
    bump(count);
}

/* ---- H1 / H2 / H3: stock's object lifecycle, for DK4's handles (the drums' only since BE4.40) ------------------------
 * H1: OBJ_CTOR's first act (build.py's wrapper keeps every register): the counter is the handle the object is about to get;
 * every entry at or above it is from an earlier boot. Also runs for stock's static constructors at M4 boot: before the table
 * exists (no magic) it only reads. */
__attribute__((used, retain)) void an_forget(void) {
    u32 ctr = HANDLE_CTR;
    if (!ctr) return;                                          /* (never on the device: .data is copied first) */
    volatile Dk4 *d = DK4;
    if (d->magic != DK4_MAGIC) return;
    u32 any = 0;
    for (u32 k = 0; k < DK4_N; k++) any |= d->e[k].handle >= ctr;
    if (!any) return;
    u32 p = lock(), n = 0;
    ctr = HANDLE_CTR;
    for (u32 k = 0; k < DK4_N; k++)
        if (d->e[k].handle >= ctr) { d->e[k].word = 0; d->e[k].handle = 0; n++; }
    if (n) { d->serial++; d->forgot += n; }
    unlock(p);
}
/* H3: NODE_DESTROY (obj, pool): every top-level object's way out. Stock frees its children (a track's pads) directly, so
 * their drum entries go here. */
__attribute__((used, retain)) u32 an_destroy(void *obj, void *pool, u32 r2, u32 r3) {
    volatile Dk4 *d = DK4;
    if (obj && dk_any(d)) {
        u32 n = W(obj, S_NODE_CHILD_N);
        for (u32 c = 0; c < n && c < 0x244u; c++) {
            void *ch = (void *)W(obj, S_NODE_CHILDREN + 4u * c);
            u32 ph = ch ? W(ch, 0x10) : 0u;
            if (dk_find(d, ph) >= 0) { dk_mark(ph, 0, 0); bump(&d->dropped); }
        }
    }
    return FN(S_NODE_DESTROY, u32 (*)(void *, void *, u32, u32))(obj, pool, r2, r3);
}
/* H2: NODE_CLONE (src, pool, &out) -> ok: stock clones a node's children (the pads) in order, each with a new handle
 * (0x0812F2B4, not through a vtable), so the pads' drum entries are carried pairwise; a copy whose child count differs (a
 * child stock failed to copy) is skipped and counted (clone_skips), not guessed at. */
__attribute__((used, retain)) u32 an_clone(void *src, void *pool, void **out) {
    u32 ok = FN(S_NODE_CLONE, u32 (*)(void *, void *, void **))(src, pool, out);
    if (ok && src && out && *out && *out != src) {
        volatile Dk4 *d = DK4;
        if (dk_any(d)) {
            u32 n = W(src, S_NODE_CHILD_N);
            if (n != W(*out, S_NODE_CHILD_N)) bump(&d->clone_skips);
            else
                for (u32 c = 0; c < n && c < 0x244u; c++) {
                    void *s = (void *)W(src, S_NODE_CHILDREN + 4u * c), *t = (void *)W(*out, S_NODE_CHILDREN + 4u * c);
                    if (s && t && B(s, 4) == B(t, 4) && dk_find(d, W(s, 0x10)) >= 0) dk_copy(W(s, 0x10), W(t, 0x10), &d->carried);
                }
        }
    }
    return ok;
}
/* H4 (S6c-2): CHILD_DUP (store, type, parent, src, &out) -> ok: stock's copy of one child (a pad) by its properties into a
 * new child (a new handle): the clipboard's copy and paste and the instrument replace (0x0812F91E's 9 callers). The copy
 * gets src's drum entry. The entry replays stock's first two instructions (naked: the stack argument stays in place). */
__attribute__((naked, noinline)) static u32 stock_dup(void *store, u32 type, void *parent, void *src, void **out) {
    __asm volatile("push {r4, r5, r6, lr}\n\t sub sp, #8\n\t movw ip, %[lo]\n\t movt ip, %[hi]\n\t bx ip"
                   :: [lo] "i"((S_CHILD_DUP + 5u) & 0xFFFFu), [hi] "i"((S_CHILD_DUP + 5u) >> 16));
}
__attribute__((used, retain)) u32 dr_dup(void *store, u32 type, void *parent, void *src, void **out) {
    u32 ok = stock_dup(store, type, parent, src, out);
    volatile Dk4 *d = DK4;
    if (ok && src && out && *out && dk_any(d) && dk_find(d, W(src, 0x10)) >= 0) dk_copy(W(src, 0x10), W(*out, 0x10), &d->dups);
    return ok;
}
#endif

/* ---- the synth area: the handshake (synth_shared.h) ------------------------------------------------------------
 * ready(): the M7 holds the reservation and its header is stamped for this M4 boot. Only then may anything be written
 * there (before, the area is stock's pool memory). UI task only. Since BE4.40 the M4 publishes only the drums' knob
 * lines (Analog's settings reach the M7 as stock's own properties); the Analog CPU figures still come from the M7's
 * stats line there. */
static int ready(void) {
    volatile SaM7 *m = M7HDR;
    u32 boot = boot_gen();
    return boot && m->magic == SA_M7_MAGIC && m->layout == SA_LAYOUT && m->total == SYNTH_TOTAL && m->gen &&
           m->boot == boot;
}
#if DRUMS_BUILT
/* A drum knob line (the M4 has no data cache: the stores reach SDRAM in order, dsb between the seqlock steps; the M7
 * invalidates the lines before it reads them, drums.cpp knob_entry). */
static void put_knobs(volatile SaKnobs *t, u32 h, u32 word, u32 v0, u32 v1) {
    u32 s = (t->head + 2u) & ~1u;
    t->head = s | 1u; t->tail = s | 1u; dsb();
    t->handle = (word & DK_USED) ? h : 0u; t->word = (word & DK_USED) ? DK_USED : 0u;   /* no engine override (0) */
    t->v0 = v0; t->v1 = v1; dsb();
    t->tail = s; t->head = s; dsb();
}
#define KTAB ((volatile SaKnobs *)SA(SA_DKTAB))
#endif
/* DK4 into the synth area when it changed, or for a new M7 generation / M4 boot. The M4 header's drum_serial moves by 2 on
 * each publish; its Analog serial stays 2 (nothing of Analog is published since BE4.40). */
static void publish(volatile An4 *a) {
    (void)a;
#if DRUMS_BUILT
    if (!DRUMS_ON || !ready()) return;
    volatile SaM7 *m = M7HDR;
    u32 gen = m->gen, boot = m->boot;
    volatile Dk4 *d = dk4();
    if (d->pub == d->serial && d->pub_m7gen == gen && d->pub_boot == boot) return;
    u32 p = lock();
    volatile SaM4 *h = M4HDR;
    if (h->magic != SA_M4_MAGIC || h->bootgen != boot) {
        h->magic = 0; dsb();
        h->serial = 2u; h->drum_serial = 0; h->r5 = h->r6 = h->r7 = 0;
    }
    for (u32 i = 0; i < DK4_N; i++) put_knobs(&KTAB[i], d->e[i].handle, d->e[i].word, d->e[i].v0, d->e[i].v1);
    h->drum_serial += 2u;
    d->pub = d->serial; d->pub_m7gen = gen; d->pub_boot = boot;
    h->m7gen = gen; h->bootgen = boot; dsb();
    h->magic = SA_M4_MAGIC; dsb();
    unlock(p);
#endif
}

/* ---- registry keys -----------------------------------------------------------------------------------------------
 * N2 (BE4.40): our four oscillator keys get their descriptors inside stock's one registry build (REG_BUILD_SITE's wrapper),
 * so they have their XML tokens before any project or preset is read. All four must be empty after stock's builder (the
 * reviewed allow-list: test_keys runs stock's builder, decodes stock's key-range tests and scans both cores for
 * immediates); if any is taken, none is written, the keys stay stock's and the black box says which (keys= TAKEN:bits).
 * A descriptor (32 B): +0 key, +2 type, +4 title, +8 labels and +0xC their count (a list, type 7), +0x10 / +0x14 the range,
 * +0x18 the XML token. Type and Sync are lists as stock's Poly Mode (0x2E); Shape an integer in [0, 127] as stock's Shift
 * (0x1AD, type 2); Glide 0..1000 shown as a percentage as stock's Pulse Width (0xC5, type 10). */
static const char T_TYPE[] = "Type", T_SHAPE[] = "Shape", T_SYNC[] = "Sync", T_GLIDE[] = "Glide";
static const char K_TYPE[] = "bento_osc_type", K_SHAPE[] = "bento_shape", K_SYNC[] = "bento_sync", K_GLIDE[] = "bento_glide";
static const char *const TYPE_LABELS[2] = {"Wavetable", "Analog"};
static const char *const SYNC_LABELS[2] = {"Off", "On"};
typedef struct { u16 key; u8 type; const char *title; const char *const *labels; u32 n; u32 lo, hi; const char *token; } Desc;
static const Desc NP_KEYS[4] = {
    {S_KEY_OSC_TYPE, 7, T_TYPE, TYPE_LABELS, 2, 0, 0, K_TYPE},
    {S_KEY_OSC_SHAPE, 2, T_SHAPE, 0, 0, 0, AN_SHAPE_MAX, K_SHAPE},
    {S_KEY_OSC_SYNC, 7, T_SYNC, SYNC_LABELS, 2, 0, 0, K_SYNC},
    {S_KEY_OSC_GLIDE, 10, T_GLIDE, 0, 0, 0, AN_GLIDE_MAX, K_GLIDE},
};
#define NP_N (sizeof(NP_KEYS) / sizeof(NP_KEYS[0]))
static volatile u8 *desc_of(u32 r, u32 key) { return (volatile u8 *)(r + key * 32u); }
static int np_mine(u32 r) {
    for (u32 i = 0; i < NP_N; i++) {
        volatile u8 *k = desc_of(r, NP_KEYS[i].key);
        if (H(k, 0) != NP_KEYS[i].key || W(k, 4) != (u32)NP_KEYS[i].title || W(k, 24) != (u32)NP_KEYS[i].token) return 0;
    }
    return 1;
}
/* N2: after stock's REG_BUILD (the wrapper in build.py), in model init. */
__attribute__((used, retain)) void np_keys(void) {
    volatile An4 *a = an4();
    a->keys_gen = boot_gen();
    if (!ANALOG_ON) { a->keys = 0; return; }
    u32 r = REGISTRY;
    if (!r) { a->keys = 0x200u; return; }
    if (np_mine(r)) { a->keys = 1; return; }
    u32 taken = 0;
    for (u32 i = 0; i < NP_N; i++) if (H(desc_of(r, NP_KEYS[i].key), 0)) taken |= 1u << i;
    if (taken) { a->keys = 0x100u | taken; return; }
    for (u32 i = 0; i < NP_N; i++) {
        const Desc *s = &NP_KEYS[i];
        volatile u8 *k = desc_of(r, s->key);
        for (u32 j = 0; j < 8; j++) W(k, j * 4u) = 0;
        B(k, 2) = s->type; W(k, 4) = (u32)s->title; W(k, 8) = (u32)s->labels; W(k, 12) = s->n;
        W(k, 16) = s->lo; W(k, 20) = s->hi; W(k, 24) = (u32)s->token;
    }
    for (u32 i = 0; i < NP_N; i++) H(desc_of(r, NP_KEYS[i].key), 0) = (u16)NP_KEYS[i].key;   /* the keys last: all or none */
    a->keys = 1;
}
static int np_ok(void) { return ANALOG_ON && an4()->keys == 1u; }
#if DRUMS_BUILT
/* S6c-2: the drum keys, KEY_DR + i for knob i (all seven or none), type 2 (an integer in [lo, hi], as stock's Shift
 * 0x1AD), titled with the Kick's knob names; DK4's keys word says which one was taken, if any. Reviewed: empty after
 * stock's builder, no immediate in either core, and their one range test (M7 0x08059990, keys 0x18C..0x1B6) is reached
 * only for set_property messages addressed to the reverb object 0xFFFFFFF1 (test_keys); D6 (dr_page_event) keeps every
 * edit of them away from the model, so no set_property ever carries one (test_drum_ui). */
static const char NO_TOKEN_DR[] = "";
static int drum_keys(void) {
    volatile Dk4 *d = dk4();
    u32 r = REGISTRY;
    if (!r) return 0;
    const DrEngine *e = &ENGINES[0];
    int mine = 1;
    for (u32 i = 0; i < DRUM_KEYS_N; i++) {
        volatile u8 *k = (volatile u8 *)(r + (KEY_DR + i) * 32u);
        if (H(k, 0) != KEY_DR + i || W(k, 4) != (u32)e->names[i]) mine = 0;
    }
    if (mine) { d->keys = 1; return 1; }
    for (u32 i = 0; i < DRUM_KEYS_N; i++)
        if (H((volatile u8 *)(r + (KEY_DR + i) * 32u), 0)) { d->keys = 0x100u | (i + 1u); return 0; }
    for (u32 i = 0; i < DRUM_KEYS_N; i++) {
        volatile u8 *k = (volatile u8 *)(r + (KEY_DR + i) * 32u);
        for (u32 j = 0; j < 8; j++) W(k, j * 4u) = 0;
        B(k, 2) = 2; W(k, 4) = (u32)e->names[i]; W(k, 16) = e->lo[i]; W(k, 20) = e->hi[i]; W(k, 24) = (u32)NO_TOKEN_DR;
    }
    for (u32 i = 0; i < DRUM_KEYS_N; i++) H((volatile u8 *)(r + (KEY_DR + i) * 32u), 0) = (u16)(KEY_DR + i);
    d->keys = 1;
    return 1;
}
#endif

#define TYPE_WT_OSC 6u                                 /* a Wavetable oscillator object (WT1 / WT2: the track's first two type-6 children) */
static u32 obj_type(void *object) { return ((u32 (*)(void *))W((void *)W(object, 0), 0))(object); }
/* ---- the selected track and an oscillator's Type -------------------------------------------------------------------- */
static u32 sel_track(void) { return FN(S_GET_PROPERTY, u32 (*)(void *, u32))((u8 *)MODEL + 4, S_KEY_SEL_TRACK); }
static u32 track_handle(u32 track) {
    u32 h = 0;
    return FN(S_FIND_TRACK, u32 (*)(void *, u32, u32 *))(MODEL, track, &h) ? h : 0u;
}
static void *resolve(u32 h) {
    void *obj = 0;
    return h && FN(S_RESOLVE_OBJECT, u32 (*)(void *, u32, void **))((void *)W(MODEL, 0), h, &obj) ? obj : 0;
}
/* A property of any object of the live project (a track or a child), as the page reads it: stock's CONTEXT_GET (its generic
 * path, the store get). 0 when it cannot be read. */
static u32 prop(u32 h, u32 key, u32 *out) {
    u32 ctx = h;
    *out = 0;
    return h && FN(S_CONTEXT_GET, u32 (*)(void *, u32 *, u32, u32 *))(MODEL, &ctx, key, out);
}
/* Analog only when Type is exactly 1 (stock stores what a file says, unclamped). */
static int analog_osc(u32 h) { u32 t = 0; return np_ok() && prop(h, S_KEY_OSC_TYPE, &t) && t == 1u; }
/* text into a bounded buffer (the titles, the black box) */
typedef struct { char *buf; u32 *len; u32 max; } Out;
static void puts_(Out *o, const char *s) { while (*s && *o->len < o->max) o->buf[(*o->len)++] = *s++; }
static void dec(Out *o, u32 v) {
    char t[11]; u32 n = 0;
    do { t[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10u);
    char r[11]; for (u32 i = 0; i < n; i++) r[i] = t[n - 1u - i];
    r[n] = 0; puts_(o, r);
}

/* ---- the Wavetable page's oscillator rows --------------------------------------------------------------------------
 * The page keeps 8 keys per row at view + SYNTH_VIEW_KEYS (16 B a row: row 0 = WT1, row 1 = WT2) and each row's object at
 * view + SYNTH_VIEW_HANDLES; stock's row bind (SYNTH_ROW_BIND) rereads the shown row's keys on every call and binds each
 * slot to the row's object (keys above 0xDA: the row's own handle), so a row whose keys we rewrite is shown with them by
 * SYNTH_ROW_SHOW (docs/plan-native-props.md P0 T5, measured on stock's page). Stock's rows 0 / 1 are {Level, Pitch, Position,
 * 0, 0, 0, 0, 0} (the flash table A6 copies). Ours, per Type (slots 5..7 stay free for the FM sprint: Sub FM, Bit Depth,
 * FM 1>2):
 *   Wavetable: Level, Pitch, Position, Type
 *   Analog:    Level, Pitch, Shape, Type, then Glide (OSC1) / Sync (OSC2, only with OSC1 Analog too: the sync master is
 *              our OSC1 kernel; a Wavetable master is not built, docs/plan-native-props.md 3.3 rule 4)
 * Only slots stock leaves empty get our keys, and slot 2 only while it is stock's key there. */
#define ROW_BYTES 16u
static volatile u16 *osc_row_keys(void *view, u32 row) { return (volatile u16 *)((u8 *)view + S_SYNTH_VIEW_KEYS + row * ROW_BYTES); }
static u32 row_handle(void *view, u32 row) { return W(view, S_SYNTH_VIEW_HANDLES + 4u * row); }
static void want_row(const u16 *stock, u32 row, int me, int osc1, u16 *k) {
    for (u32 i = 0; i < 8u; i++) k[i] = stock[i];
    if (!k[3]) k[3] = (u16)S_KEY_OSC_TYPE;
    if (!me) return;
    if (k[2]) k[2] = (u16)S_KEY_OSC_SHAPE;
    if (!k[4]) k[4] = row == 0u ? (u16)S_KEY_OSC_GLIDE : osc1 ? (u16)S_KEY_OSC_SYNC : 0u;
}
/* H1 (BE4.40 identity): row 2 ("Osc + Config") is stock's, without WTOscReset while both oscillators are Analog (it acts
 * on stock's wavetable playback only, dsp/analog.cpp has no reset); the slot is left empty, as stock leaves its free ones. */
static int fix_reset_row(void *view, const u16 *stock, int both) {
    volatile u16 *v = osc_row_keys(view, 2);
    int changed = 0;
    for (u32 i = 0; i < 8u; i++) {
        u16 k = stock[2u * 8u + i];
        if (both && k == (u16)S_KEY_WT_OSC_RESET) k = 0;
        if (v[i] != k) { v[i] = k; changed = 1; }
    }
    return changed;
}
/* The view's two oscillator rows (and row 2, H1) as they should be for the oscillators' Types now: 1 = rewritten. stock:
 * the page's own key table (A6's source). */
static int fix_rows(void *view, const u16 *stock) {
    int a0 = analog_osc(row_handle(view, 0)), a1 = analog_osc(row_handle(view, 1)), changed = 0;
    for (u32 row = 0; row < 2u; row++) {
        u16 k[8];
        want_row(stock + row * 8u, row, row ? a1 : a0, a0, k);
        volatile u16 *v = osc_row_keys(view, row);
        for (u32 i = 0; i < 8u; i++) if (v[i] != k[i]) { v[i] = k[i]; changed = 1; }
    }
    return fix_reset_row(view, stock, a0 && a1) | changed;
}
static void full_repaint(void *view);
/* A6: the page builder's bl memcpy(view + SYNTH_VIEW_KEYS, the Wavetable page's key table, 0xB0). The row handles may still
 * name the previous page's objects here: the tick puts the rows right on its next pass (fix_rows) either way. A dial this
 * view shows is cleared by a full repaint (a track change from an Analog OSC1 to a Wavetable one: stock's pane draws no
 * background and the 0xA8 build asks for no full repaint, M); the dials are drawn afresh (D2). */
__attribute__((used, retain)) void *an_page_keys(void *dst, const void *src, u32 n) {
    void *r = FN(S_M4_MEMCPY, void *(*)(void *, const void *, u32))(dst, src, n);
    if (np_ok() && n >= 3u * ROW_BYTES) {
        volatile An4 *a = an4();
        void *view = (u8 *)dst - S_SYNTH_VIEW_KEYS;
        a->src = (u32)src;
        a->page_rows++;
        a->last_h[0] = a->last_h[1] = 0;     /* a new page: its pane texts were set before its rows had objects (page_tick) */
        if (a->dial[0].view == (u32)view || a->dial[1].view == (u32)view) full_repaint(view);
        a->dial[0].view = a->dial[1].view = 0;
        fix_rows(view, (const u16 *)src);
    }
    return r;
}
/* Is this view the Wavetable page (its first row an oscillator, type 6, of a Wavetable track)? */
static int wt_page(void *view) {
    u32 h = row_handle(view, 0);
    return h && FN(S_OBJ_TYPE, u32 (*)(void *, u32 *))(MODEL, &h) == TYPE_WT_OSC;
}

/* ---- the oscillator panes (BE4.40 identity, docs/plan-be440-sync-identity.md track I) --------------------------------
 * Pane o (0 = OSC1, 1 = OSC2) of a synth view: the widget at VIEW_WT_PANE + WT_PANE_STRIDE o (class WT_PANE_PAINT_SLOT - 4,
 * its parent the view), its hint label at + WT_PANE_HINT, its title label at VIEW_PANE_TITLE + PANE_TITLE_STRIDE o (a child
 * of the view below the pane, M: rects (11,271,622,142) / (6,236,632,30)). A view is a synth view when its first word is
 * the class SYNTH_VIEW_TICK_SLOT. */
static void *pane_w(void *view, u32 o) { return (u8 *)view + S_VIEW_WT_PANE + S_WT_PANE_STRIDE * o; }
static void *pane_title(void *view, u32 o) { return (u8 *)view + S_VIEW_PANE_TITLE + S_PANE_TITLE_STRIDE * o; }
/* The synth view that has `slot` at offset base + stride o, and o; -1 if none. */
static int view_of(void *slot, u32 base, u32 stride, void **view) {
    for (u32 o = 0; o < 2u; o++) {
        u32 off = base + stride * o;
        u8 *v = (u8 *)slot - off;
        if ((u32)slot > off && W(v, 0) == S_SYNTH_VIEW_TICK_SLOT) { *view = v; return (int)o; }
    }
    return -1;
}
static void slot_title(void *slot, const char *text) { FN(S_SLOT_TITLE, void (*)(void *, const char *))(slot, text); }

/* A7: an Analog pane's hint is empty (the dial is the pane's content, D1; T2: the "Analog" text of BE4.40 went to the hidden
 * "No WAV" label, whose sites are stock's again). */
__attribute__((used, retain)) void an_pane_text(void *slot, const char *text) {
    void *view = 0;
    int o = np_ok() ? view_of(slot, S_VIEW_WT_PANE + S_WT_PANE_HINT, S_WT_PANE_STRIDE, &view) : -1;
    if (o >= 0 && wt_page(view) && analog_osc(row_handle(view, (u32)o))) text = "";
    slot_title(slot, text);
}

/* ---- T1 / W1: the pane title of an Analog oscillator ---------------------------------------------------------------
 * "OSC1: Analog", " (WAV kept)" while the oscillator still has a WAV (stock keeps it, and plays it again as Wavetable), and
 * on the first Analog oscillator "  DSP 63%": the M7's total LOAD (D0L, cpu_update; not this oscillator's share, hence
 * not "CPU"), " OVER" for 2 s after a block over budget, "DSP off" before the M7 has taken the synth area. SLOT_TITLE
 * compares and keeps 31 characters (0x081809E6, S): " +WAV" next to the load (below). One composer for the title hook
 * (on stock's refreshes) and the tick (every 250 ms: the hook is not called while nothing changes, M). */
#define TITLE_MAX 31u
static int has_wav(u32 h) {
    char path[256];                            /* GET_STRING copies at most 255 characters and a 0 (0x0812EDC2, S) */
    path[0] = 0;
    FN(S_GET_STRING, u32 (*)(void *, u32 *, u32, char *))(MODEL, &h, S_KEY_FILENAME, path);
    return path[0] != 0;
}
/* One form per state (W1, review rev_m4_1): the marker does not change with the load. Where the load is shown the WAV
 * marker is " +WAV" and the separator one space, so the longest ("OSC1: Analog +WAV DSP 100% OVER") is 31; elsewhere
 * " (WAV kept)" and two spaces (at most 12 + 11 or 12 + 2 + 8 + 5). osc1: OSC1 is Analog (OSC2 then shows no load). */
static void title_text(u32 o, u32 h, int osc1, char *buf) {
    volatile An4 *a = an4();
    char dsp[16]; u32 dl = 0;
    Out d = {dsp, &dl, sizeof(dsp) - 1u};
    if (o == 0 || !osc1) {
        if (!ready()) puts_(&d, "DSP off");
        else {
            puts_(&d, "DSP "); dec(&d, (a->cpu_pm + 5u) / 10u); puts_(&d, "%");
            if (a->over_t && TICK_MS - a->over_t < 2000u) puts_(&d, " OVER");
        }
    }
    dsp[dl] = 0;
    int wav = has_wav(h);
    u32 len = 0; Out out = {buf, &len, TITLE_MAX};
    puts_(&out, o ? "OSC2: Analog" : "OSC1: Analog");
    if (wav) puts_(&out, dl ? " +WAV" : " (WAV kept)");
    if (dl) { puts_(&out, wav ? " " : "  "); puts_(&out, dsp); }
    buf[len] = 0;
}
/* The handle of the track's oscillator k (its k-th type-6 child), 0 if none. */
static u32 nth_osc(void *track, u32 k) {
    u32 n = W(track, S_NODE_CHILD_N);
    for (u32 c = 0; c < n && c < 0x244u; c++) {
        void *ch = (void *)W(track, S_NODE_CHILDREN + 4u * c);
        if (ch && obj_type(ch) == TYPE_WT_OSC && !k--) return W(ch, 0x10);
    }
    return 0;
}
/* T1: the Wavetable branch's bl SLOT_TITLE (PANE_TITLE_SITE: slot = view + VIEW_PANE_TITLE + 0x70 row, the "%s%.20s" text),
 * through build.py's shim, which hands over the row's object handle from stock's frame ([sp, #0x32C]: the handle stock
 * reads key 0xC9 with right after, 0x0815F97E, S). The view's row handles are not written yet there (0 or the previous
 * page's: M, verifier e1), so OSC2's title takes OSC1's Type from the selected track's first oscillator when h is that
 * track's second (review rev_m4_2: no load figure on OSC2 from a stale row handle until the tick). */
__attribute__((used, retain)) void an_pane_title(void *slot, const char *text, u32 h) {
    char buf[TITLE_MAX + 1u];
    void *view = 0;
    int o = np_ok() ? view_of(slot, S_VIEW_PANE_TITLE, S_PANE_TITLE_STRIDE, &view) : -1;
    if (o >= 0 && analog_osc(h)) {
        void *t = o ? resolve(track_handle(sel_track())) : 0;
        u32 h1 = t && nth_osc(t, 1) == h ? nth_osc(t, 0) : row_handle(view, 0);
        title_text((u32)o, h, analog_osc(h1), buf);
        text = buf;
    }
    slot_title(slot, text);
}

/* ---- B1': a double-tap on an Analog pane opens no WAV browser ------------------------------------------------------
 * Stock's synth view, event 0x7B (ev+8 = the oscillator 0 / 1) on a Wavetable track: 0xA8 {the oscillator} (selects it),
 * then 0x3F {view + 0x2438} (0x0816104C..8A), which the app turns into its page 0x14 (0x08142682 -> 0x08143D44, S). The
 * shim at BROWSE_POST_SITE (that 0x3F's bl UI_POST) passes the oscillator's handle ([sp, #0x20], the 0xA8 just posted):
 * for an Analog one the 0x3F is not posted (the press, its highlight and the selection stay stock's). Which touch is 0x7B
 * and that page 0x14 is the WAV browser are device checks (I). Stock's other load paths (the header's 0xEE / 0xEF actions
 * -> 0x1D, menus) are explicit and stay open: the title then says "(WAV kept)". */
__attribute__((used, retain)) void an_browse(void *view, void *ev, u32 h) {
    if (np_ok() && W(view, 0) == S_SYNTH_VIEW_TICK_SLOT && analog_osc(h)) { bump(&an4()->browse); return; }
    FN(S_UI_POST, void (*)(void *, void *))(view, ev);
}

/* ---- D1 / D2: the Shape dial in an Analog pane (after Bento Playground's pane hook, GPL-3.0-or-later: the paint slot
 * 0x081B3A18 + 4 on 1.6.1; this one is drawn with stock's rect fill only) -------------------------------------------------
 * J3: only what the kernel plays (dsp/analog.cpp an_shape: Shape 0..AN_SHAPE_SAW_END is one saw, then a saw > square morph to
 * AN_SHAPE_SQUARE, then the pulse narrows to 8 % at 127): a 270-degree ring open at the bottom with the saw sector
 * (0..48) drawn wider, glyphs for saw (in the sector), square (88) and pulse (127), each lit only while it has 3/4 of the
 * blend (none in the middle of a morph), and an orange pointer at Shape. The five-shape table comes with sine / triangle
 * (the FM sprint), in the same commit as the kernel. Integer only; pane coordinates have y up (M). */
static void fill(int x, int y, int w, int h, u32 c, u32 surf) {
    int r[4] = {x, y, w, h};
    FN(S_GFX_FILL, void (*)(const int *, u32, u32))(r, c, surf);
}
static const short QS[17] = {0, 402, 799, 1189, 1567, 1931, 2276, 2598, 2896, 3166, 3406, 3612, 3784, 3920, 4017, 4076, 4096};
static int sin64(int a) {                     /* a in 1/64 turn, Q12 */
    a &= 63; int q = a >> 4, i = a & 15, v = (q & 1) ? QS[16 - i] : QS[i];
    return q >= 2 ? -v : v;
}
static int sin256(int a) { int lo = a >> 2, f = a & 3; return (sin64(lo) * (4 - f) + sin64(lo + 1) * f) >> 2; }
static int angle(u32 shape) { return 160 - (int)(shape * 192u / AN_SHAPE_MAX); }   /* 1/256 turn: 0 -> 225 deg, 127 -> -45 */
#define C_DIM 0xF3u                           /* stock's palette entries (pane paint 0x08184550 / render.py, M) */
#define C_SECTOR 0xF5u
#define C_LIT 0xF2u
#define C_POINTER 0xF6u
/* Two cycles of glyph k (0 saw, 1 square, 2 pulse) over 24 columns, amplitude 9, centred on (cx, cy), y up: the saw rises
 * and falls back at each cycle's end as the kernel's 2p - 1 (analog_kernel.cpp), the square and the pulse show both edges;
 * the pulse is high for 3 of a cycle's 12 columns (8 % would be one: too thin to read), review rev_m4_1. Each column joins
 * the last one's level, which draws the vertical edges. */
#define GLYPH_N 24
#define GLYPH_AMP 9
static void glyph(u32 k, int cx, int cy, u32 c, u32 surf) {
    int prev = 0;
    for (int i = 0; i <= GLYPH_N; i++) {
        int p = i * 128 / GLYPH_N & 63;
        int y = cy + (k == 0u ? (p - 32) * GLYPH_AMP / 32 : p < (k == 1u ? 32 : 16) ? GLYPH_AMP : -GLYPH_AMP);
        int lo = i && prev < y ? prev : y, hi = i && prev > y ? prev : y;
        fill(cx - GLYPH_N / 2 + i, lo, 2, hi - lo + 2, c, surf);
        prev = y;
    }
}
/* lit glyphs for Shape s: bit k when glyph k has at least 3/4 of the blend (saw 0..58, square 78..97, pulse 118..127) */
#define MORPH (AN_SHAPE_SQUARE - AN_SHAPE_SAW_END)
#define NARROW (AN_SHAPE_MAX - AN_SHAPE_SQUARE)
static u32 lit(u32 s) {
    u32 m = 0;
    if (s <= AN_SHAPE_SAW_END + MORPH / 4u) m |= 1u;
    if (s >= AN_SHAPE_SQUARE - MORPH / 4u && (s <= AN_SHAPE_SQUARE || 4u * (s - AN_SHAPE_SQUARE) <= NARROW)) m |= 2u;
    if (s > AN_SHAPE_SQUARE && 4u * (s - AN_SHAPE_SQUARE) >= 3u * NARROW) m |= 4u;
    return m;
}
static void dial(void *w, u32 s, u32 surf) {
    int x = (int)W(w, 4), y = (int)W(w, 8), wd = (int)W(w, 12), ht = (int)W(w, 16);
    if (wd < 180 || ht < 132) return;          /* too small for the dial (never on 1.6.8's page: 622 x 142, M) */
    int cx = x + wd / 2, cy = y + ht / 2;
    u32 m = lit(s);
    for (u32 v = 0; v <= AN_SHAPE_MAX; v += 2u) {                     /* the ring, in 2/127 steps of Shape */
        int an = angle(v), sector = v <= AN_SHAPE_SAW_END, t = sector ? 5 : 3;
        fill(cx + sin256(an + 64) * 42 / 4096 - t / 2, cy + sin256(an) * 42 / 4096 - t / 2, t, t,
             sector ? (s <= AN_SHAPE_SAW_END ? C_LIT : C_SECTOR) : C_DIM, surf);
    }
    static const u8 AT[3] = {AN_SHAPE_SAW_END / 2u, AN_SHAPE_SQUARE, AN_SHAPE_MAX};
    for (u32 k = 0; k < 3u; k++) {
        int an = angle(AT[k]);
        glyph(k, cx + sin256(an + 64) * 70 / 4096, cy + sin256(an) * 57 / 4096, m & (1u << k) ? C_LIT : C_DIM, surf);
    }
    int an = angle(s), c = sin256(an + 64), sn = sin256(an);
    for (int r = 6; r <= 34; r += 2) fill(cx + c * r / 4096 - 1, cy + sn * r / 4096 - 1, 3, 3, C_POINTER, surf);
    fill(cx - 3, cy - 3, 7, 7, C_POINTER, surf);
}
/* What pane o's dial shows now (0: no dial: not the Wavetable page, or a Wavetable oscillator): Shape (clamped: stock stores
 * what a file says), and stock's own pane states it would draw: pressed (+2: stock fills the pane 0xF6, 0x08184748) and
 * framed (+3, in the colour at +0: 0x08184760). */
#define DIAL_ON 0x80000000u
#define DIAL_PRESSED 0x100u
#define DIAL_FRAMED 0x200u
#define DIAL_MS 33u                                       /* at most ~30 redraws a second */
static u32 shape_of(u32 h) {
    u32 v = 0;
    prop(h, S_KEY_OSC_SHAPE, &v);
    return (int)v < 0 ? 0u : v > AN_SHAPE_MAX ? AN_SHAPE_MAX : v;
}
static volatile u8 *pane_flags(void *w) { return (volatile u8 *)w + S_WT_PANE_FLAGS; }  /* frame colour, background, pressed, framed */
static u32 dial_key(void *view, u32 o, u32 *h) {
    *h = row_handle(view, o);
    if (!wt_page(view) || !analog_osc(*h)) return 0;
    volatile u8 *f = pane_flags(pane_w(view, o));
    return DIAL_ON | shape_of(*h) | (f[2] ? DIAL_PRESSED : 0u) | (f[3] ? DIAL_FRAMED | (u32)f[0] << 16 : 0u);
}
/* D1: the pane class's paint slot (pane, ctx: +0 a full repaint (the root clears the screen first, 0x081701F8), +4 the
 * surface). An Analog pane: stock's paint is skipped (no wavetable frames, marker or hint drawn and then covered), the pane
 * is filled with its own frame background (+1, stock's tile colour) and the dial drawn on a full repaint or when it shows
 * something else than last time (AN4's cache: stock clears the pane's dirty byte before paint and shares the flag page-wide,
 * M / S 0x081844E0), at most every DIAL_MS; then the pane's children (the hint, empty: A7). Any other pane: stock's. */
__attribute__((used, retain)) void an_wave_paint(void *w, void *ctx) {
    void *view = 0;
    int o = np_ok() ? view_of(w, S_VIEW_WT_PANE, S_WT_PANE_STRIDE, &view) : -1;
    u32 h = 0, key = o >= 0 && W(w, S_WIDGET_PARENT) == (u32)view ? dial_key(view, (u32)o, &h) : 0u;
    if (!key) { FN(S_WT_PANE_PAINT, void (*)(void *, void *))(w, ctx); return; }
    volatile An4 *a = an4();
    volatile DialCache *c = &a->dial[o];
    u32 now = TICK_MS;
    int full = B(ctx, 0) != 0;
    if ((full || c->view != (u32)view || c->h != h || c->key != key) && (full || now - c->t >= DIAL_MS)) {
        u32 surf = W(ctx, 4);
        volatile u8 *f = pane_flags(w);
        fill((int)W(w, 4), (int)W(w, 8), (int)W(w, 12), (int)W(w, 16), key & DIAL_PRESSED ? C_POINTER : f[1], surf);
        if (!(key & DIAL_PRESSED)) dial(w, key & 0xFFu, surf);
        if (key & DIAL_FRAMED) FN(S_GFX_FRAME, void (*)(void *, u32, u32))((u8 *)w + 4, f[0], surf);
        c->view = (u32)view; c->h = h; c->key = key; c->t = now;
        a->dials++;
    }
    FN(S_WIDGET_PAINT_KIDS, void (*)(void *, void *))(w, ctx);
}
static int shown(void *w) {                                /* no hidden widget up the parent chain */
    for (u32 i = 0; w && i < 16u; i++, w = (void *)W(w, S_WIDGET_PARENT)) if (B(w, S_WIDGET_HIDDEN)) return 0;
    return 1;
}
/* A full repaint of the page (both request bytes: ctx+0 makes the root clear the screen, 0x081701F8; the frames are drawn
 * only with ctx+1, 0x0818455E): every
 * widget of the synth view draws again on it (M: fix_m4/pfull.py, the view painted twice from a cleared screen: the same
 * 140,413 px) except stock's Wavetable frames, each drawn only while its dirty byte (+0x19) is set and cleared once drawn
 * (0x081845AE..0x08184672), so both panes' frames are marked first (WT_PANE_FRAMES_DIRTY, as stock's row refresh does for a
 * row with a WAV, 0x0815F6F8). Review rev_m4_2: without that, the other pane's wavetable stayed blank. */
static void full_repaint(void *view) {
    for (u32 o = 0; o < 2u; o++) FN(S_WT_PANE_FRAMES_DIRTY, void (*)(void *))(pane_w(view, o));
    B(pane_w(view, 0), S_WT_PANE_REDRAW) = 1;
    B(pane_w(view, 0), S_WT_PANE_REDRAW + 1u) = 1;
}
/* D2 (the tick): a pane whose dial is out of date is marked for a partial repaint (its dirty byte, which stock's pane tick
 * 0x081844E0 ORs into the page's flags: then the paint walk calls an_wave_paint); a pane that leaves Analog gets a full
 * repaint (full_repaint), so stock's pane is not drawn over our fill; a hidden pane is drawn afresh when it shows again. */
static void dial_tick(volatile An4 *a, void *view, u32 now) {
    for (u32 o = 0; o < 2u; o++) {
        void *w = pane_w(view, o);
        volatile DialCache *c = &a->dial[o];
        u32 h = 0, key = dial_key(view, o, &h);
        if (!shown(w)) { c->view = 0; continue; }
        if (!key) {
            if (c->view == (u32)view) { full_repaint(view); c->view = 0; }
            continue;
        }
        if ((c->view != (u32)view || c->h != h || c->key != key) && now - c->t >= DIAL_MS) B(w, S_WT_PANE_REDRAW + 1u) = 1;
    }
}

/* ---- Y1 / V1: the shown row's value texts ------------------------------------------------------------------------------
 * At the widget's own text writes (an_value_text) and in the tick, before and after stock's. Y1: a Type value stock's two-label
 * list cannot name (not 0 / 1: the oscillator plays Wavetable) reads "Wavetable"; nothing is written to the model, so a
 * later version's value survives a save. V1: an Analog oscillator's Shape reads what the kernel plays (dsp/analog.cpp an_shape,
 * analog_kernel.cpp: blend = 2 shape, pulse width 0.5 - 0.84 (shape - 0.5)). Only widgets bound to our keys: Type and Shape
 * are only in the oscillator rows. */
static void shape_text(u32 s, char *t) {
    u32 len = 0;
    Out o = {t, &len, 15u};
    if (s <= AN_SHAPE_SAW_END) puts_(&o, "Saw");
    else if (s < AN_SHAPE_SQUARE) { puts_(&o, "Saw>Sq "); dec(&o, ((s - AN_SHAPE_SAW_END) * 200u / MORPH + 1u) / 2u); puts_(&o, "%"); }
    else if (s == AN_SHAPE_SQUARE) puts_(&o, "Square");
    else { puts_(&o, "Pulse "); dec(&o, (100u * NARROW - 84u * (s - AN_SHAPE_SQUARE) + NARROW) / (2u * NARROW)); puts_(&o, "%"); }
    t[len] = 0;
}
/* The text slot widget wd (key, object h) shows instead of stock's, 0 for stock's: an out-of-range Type (Y1), an Analog
 * oscillator's Shape (V1). */
static const char *value_text(u8 *wd, u32 h, char *t) {
    u32 key = H(wd, S_SLOT_KEY), v = 0;
    if (key == S_KEY_OSC_TYPE && prop(h, key, &v) && v > 1u) return TYPE_LABELS[0];
    if (key == S_KEY_OSC_SHAPE && analog_osc(h)) { shape_text(shape_of(h), t); return t; }
    return 0;
}
/* Y1, the encoder: stock's two-label widget keeps an out-of-range Type at its last entry (step position SLOT_STEP: index
 * 1, value SLOT_CUR 1, "Analog"), so a right turn did nothing and a left one wrote 0 (M, review rev_m4_1). The widget is
 * put at entry 0, what it now reads: index 0, its last-posted index (SLOT_STEP_LAST) and value 0. ENC_TURN posts when the
 * index moves off the last one and the new entry's value differs from SLOT_CUR (0x08173FE0..FFA), so right writes 1
 * (Analog), left nothing (already "Wavetable"); the model keeps its value until then. */
#define STEP_INDEX 0xCu                       /* the step position's index (ENC_POS_SET: str r1, [r0, #0xc], 0x081742D4, S) */
static void type_entry(u8 *wd) {
    u32 v = 0;
    if (H(wd, S_SLOT_KEY) != S_KEY_OSC_TYPE || !prop(W(wd, S_SLOT_HANDLE), S_KEY_OSC_TYPE, &v) || v <= 1u) return;
    if (!W(wd, S_SLOT_STEP + STEP_INDEX) && !W(wd, S_SLOT_STEP_LAST) && !W(wd, S_SLOT_CUR)) return;
    FN(S_ENC_POS_SET, void (*)(void *, u32))(wd + S_SLOT_STEP, 0);
    W(wd, S_SLOT_STEP_LAST) = 0;
    W(wd, S_SLOT_CUR) = 0;
}
static void value_texts(void *view) {
    for (u32 i = 2; i < 4u; i++) {
        u8 *wd = (u8 *)view + S_SYNTH_SLOT0 + S_SYNTH_SLOT_STRIDE * i;
        char t[16];
        const char *s = value_text(wd, W(wd, S_SLOT_HANDLE), t);
        if (s) slot_title(wd + S_SLOT_VALUE, s);
        type_entry(wd);
    }
}
/* The synth view whose encoder slot widget is wd (view + SYNTH_SLOT0 + SYNTH_SLOT_STRIDE i, i < 8), 0 if none. */
static void *slot_view(u8 *wd) {
    for (u32 i = 0; i < 8u; i++) {
        u32 off = S_SYNTH_SLOT0 + S_SYNTH_SLOT_STRIDE * i;
        u8 *v = wd - off;
        /* every encoder widget of every page comes here: a word read below its RAM block's base (each block starts on a
         * 16 MB boundary: DTCM, AXI, D2, D3, SDRAM) could fault, so v stays in wd's 16 MB */
        if (((u32)v >> 24) == ((u32)wd >> 24) && (u32)wd > off && W(v, 0) == S_SYNTH_VIEW_TICK_SLOT) return v;
    }
    return 0;
}
/* V1 / Y1 at the source: the encoder widget's own value-text writes (VALUE_TEXT_1..3: bl SLOT_TITLE(widget + SLOT_VALUE,
 * text) in its value updates, 0x08173824 / 0x08173AE2 / 0x08173CFC, S), on a row bind (SYNTH_ROW_SHOW), stock's 0xC3 / 0xC4
 * refreshes and a turn: stock's number or label never reaches the screen between two ticks (review rev_m4_1 / rev_m4_2,
 * M: "64" and "Analog" after a row switch). Every widget of this class on every page comes through here: only a synth
 * view's, on the Wavetable page, is touched. The tick (value_texts) still covers an oscillator whose Type changed under a
 * row that stock did not rewrite. On a bind the widget's text is written with its new key but still the old row's object
 * (M: Shape with OSC2's handle on a switch to row 0), so the object is the shown row's (SYNTH_VIEW_SHOWN: the byte
 * view + [view + SYNTH_VIEW_SHOWN] + SYNTH_SHOWN_ROW, SYNTH_ROW_BIND 0x08160A50..5E; Type and Shape are on rows 0 / 1 only:
 * our keys above 0xDA are bound to the row's own handle there, 0x08160D8C). */
__attribute__((used, retain)) void an_value_text(void *label, const char *text) {
    char t[16];
    /* slot_view first: stock's static constructors write encoder texts too (M: test_handles cold boot), before the model
     * and np_keys; np_ok() there would make the AN4 table (an4()) or trust a previous boot's keys word. No synth view
     * exists then, so AN4 is not touched. */
    u8 *wd = (u8 *)label - S_SLOT_VALUE;
    void *view = slot_view(wd);
    if (view && np_ok()) {
        if (wt_page(view)) {
            u32 row = B((u8 *)view + W(view, S_SYNTH_VIEW_SHOWN), S_SYNTH_SHOWN_ROW);
            const char *s = row < 2u ? value_text(wd, row_handle(view, row), t) : 0;
            if (s) text = s;
        }
    }
    slot_title(label, text);
}

/* ---- an oscillator that just became Analog gets its other settings again ------------------------------------------------
 * The M7 keeps an oscillator's Shape / Sync / Glide only while it is Analog (in the oscillator's kernel area, which stock's
 * wavetable render owns otherwise: docs/plan-native-props.md 3.3 rule 1, P0). A stock push (project load, preset, menu
 * create, paste) sends Type first and then the rest (the template's order); an edit of Type alone does not, so the tick
 * sends them: SET_PROPERTY(engine, track, key, value, index) as stock's own edit does (MODEL_CHILD_EDIT 0x08132C5C). */
static u32 child_index(void *track, u32 h, u32 *index) {
    u32 n = W(track, S_NODE_CHILD_N), k = 0;
    for (u32 c = 0; c < n && c < 0x244u; c++) {
        void *ch = (void *)W(track, S_NODE_CHILDREN + 4u * c);
        if (!ch || obj_type(ch) != TYPE_WT_OSC) continue;
        if (W(ch, 0x10) == h) { *index = k; return 1; }
        k++;
    }
    return 0;
}
static void resend(u32 h) {
    u32 th = track_handle(sel_track()), index = 0;
    void *t = resolve(th);
    if (!t || !child_index(t, h, &index) || index > 1u) return;
    static const u16 KEYS[3] = {S_KEY_OSC_SHAPE, S_KEY_OSC_SYNC, S_KEY_OSC_GLIDE};
    for (u32 i = 0; i < 3u; i++) {
        u32 v = 0;
        if (!prop(h, KEYS[i], &v)) continue;
        FN(S_SET_PROPERTY, void (*)(u32, u32, u32, u32, u32, u32, u32))(*(volatile u32 *)S_ENGINE_PTR, th, KEYS[i], v, index, 0, 0);
    }
    bump(&an4()->resends);
}
/* The tick's pass over the synth page: rows for the Types now (and SYNTH_ROW_SHOW when one changed), and a resend for an
 * oscillator the page saw turn Analog. */
static void page_tick(volatile An4 *a, void *view) {
    if (!np_ok() || !view || !a->src || !wt_page(view)) return;
    u32 panes = 0;
    for (u32 row = 0; row < 2u; row++) {
        u32 h = row_handle(view, row), t = 0;
        prop(h, S_KEY_OSC_TYPE, &t);
        if (h == a->last_h[row] && a->last_t[row] != 1u && t == 1u) resend(h);
        /* the pane's texts (A7) and title (T1) are for another oscillator or Type: the page was built (stock set them before
         * the rows had objects, or from a reused view's old handles), or the Type changed; stock's own row refresh only runs
         * for the shown row (SYNTH_ROW_SHOW). B3 (BE4.40 identity): a new handle is refreshed whatever its Type (a stale
         * Analog text on a Wavetable oscillator, M) */
        if (h != a->last_h[row] || (a->last_t[row] == 1u) != (t == 1u)) panes |= 1u << row;
        a->last_h[row] = h; a->last_t[row] = t;
    }
    if (fix_rows(view, (const u16 *)a->src)) {
        a->heals++;
        FN(S_SYNTH_ROW_SHOW, void (*)(void *))(view);
    }
    for (u32 row = 0; row < 2u; row++)
        if (panes & (1u << row)) {                       /* stock's "child changed": that row refreshed, the shown row bound */
            u32 ev[4] = {0xC3u, row_handle(view, row), 0, 0};
            FN(S_SYNTH_VIEW_NOTIFY, u32 (*)(void *, void *))(view, ev);
        }
}

/* ---- the CPU indicator (D3) ---------------------------------------------------------------------------------------
 * The M7's LOAD counters (D0L: blocks seen, permille sum, blocks over 100 %; d0_publish cleans them about every 0.68 s, a
 * save restarts them), differenced every 500 ms in an_tick. It shows the load, it limits nothing. */
static void cpu_update(volatile An4 *a, u32 now) {
    if (now - a->cpu_t < 500u) return;
    a->cpu_t = now;
    volatile D0Load *l = D0L;
    u32 seen = l->seen, sum = l->sum, h3 = l->h[3];
    if (!a->cpu_ok || seen < a->seen || h3 < a->h3) {        /* this boot's first look, or a save started a new stretch */
        a->seen = seen; a->sum = sum; a->h3 = h3; a->cpu_ok = 1;
        return;
    }
    if (seen == a->seen) return;
    a->cpu_pm = (sum - a->sum) / (seen - a->seen);
    if (h3 != a->h3) {
        a->over_t = now | 1u;
        u32 blocks = ready() ? STATS->blocks : a->stats_blocks;
        if (blocks != a->stats_blocks) a->over += h3 - a->h3;  /* over budget while an Analog voice rendered */
        a->stats_blocks = blocks;
    }
    a->seen = seen; a->sum = sum; a->h3 = h3;
}
/* A10: the synth view's tick (view, flags). The rows and the resend (page_tick); at most 4 times a second the Analog panes'
 * titles (T1: the same composer as the title hook, so the DSP figure moves while nothing refreshes the page; SLOT_TITLE
 * marks a label only when its text changed, 0x081809EA, S); the dials (D2); the value texts (Y1, V1) around stock's tick.
 * T2: nothing goes to the hidden "No WAV" labels any more. */
__attribute__((used, retain)) void an_view_tick(void *view, void *flags) {
    volatile An4 *a = an4();
    u32 now = TICK_MS;
    int page = ANALOG_ON && view && np_ok() && a->src && wt_page(view);
    if (ANALOG_ON && view) page_tick(a, view);
    if (page && now - a->label_t >= 250u) {
        a->label_t = now;
        for (u32 o = 0; o < 2u; o++) {
            u32 h = row_handle(view, o);
            if (!analog_osc(h)) continue;                   /* a Wavetable title is stock's (page_tick's 0xC3 refreshes it) */
            char t[TITLE_MAX + 1u];
            title_text(o, h, analog_osc(row_handle(view, 0)), t);
            slot_title(pane_title(view, o), t);
        }
    }
    if (page) dial_tick(a, view, now);
    if (page) value_texts(view);                            /* Y1: the Type widget at its entry before stock's tick turns it */
    FN(S_SYNTH_VIEW_TICK, void (*)(void *, void *))(view, flags);
    if (page) value_texts(view);
}

#if DRUMS_BUILT
/* ---- S6c-2: the instrument (pad) page of a Kick pad ---------------------------------------------------------------
 * Stock's instrument page view (vtable INST_TICK_SLOT) shows a pad: the cell (its parameters, S_INST_CELL), the file
 * node (S_INST_FILE), the track's type (S_INST_TRACK_TYPE), and three rows of eight keys (S_INST_KEYS, row stride
 * S_INST_ROW_STRIDE), copied from the page table (S_INST_NATIVE_KEYS, One-Shot: Main / Config / envelope + LFO) whenever
 * the cell changes (0x0815459C), then D5 (INST_REFRESH) reads each key's value with CONTEXT_GET and refreshes the bars.
 * On a Kick pad the Config row (Reverse, LaunchMode, Interp, Poly Mode, Choke Grp, Loop Mode, Loop Fade, -) becomes the
 * Kick's seven knobs and Poly Mode: Reverse, Interp and Loop Fade do nothing to a synth; LaunchMode, Choke Grp and Loop
 * Mode still act and come back with the native row on any other pad (docs/plan-synths-168.md "S6c-2"). Main and the
 * envelope / LFO row are the pad's own and act on the Kick (the post chain). The waveform stays (the carrier's). */
static int ascii_eq(const char *a, const char *b) {          /* case-insensitive, whole string */
    for (; *a && *b; a++, b++) {
        char x = *a, y = *b;
        if (x >= 'a' && x <= 'z') x = (char)(x - 32);
        if (y >= 'a' && y <= 'z') y = (char)(y - 32);
        if (x != y) return 0;
    }
    return !*a && !*b;
}
/* The engine of the pad the page shows (0: not a drum page): a One-Shot track's pad whose file's base name is one of
 * the engines' carriers. Model reads only (stock's string property, no card access). */
static u32 dr_page_engine(void *view) {
    if (!view || B(view, S_INST_TRACK_TYPE) != S_TYPE_ONESHOT) return 0;
    u32 cell = W(view, S_INST_CELL), file = W(view, S_INST_FILE);
    if (!cell || !file) return 0;
    u32 t = FN(S_OBJ_TYPE, u32 (*)(void *, u32 *))(MODEL, &cell);
    if (t != DR_TYPE_CELL && t != DR_TYPE_CELL2) return 0;
    char path[260];
    path[0] = 0; path[259] = 0;
    FN(S_GET_STRING, u32 (*)(void *, u32 *, u32, char *))(MODEL, &file, S_KEY_FILENAME, path);
    path[259] = 0;
    const char *base = path;
    for (const char *s = path; *s; s++) if (*s == '\\' || *s == '/') base = s + 1;
    for (u32 i = 0; i < ENGINES_N; i++) if (ascii_eq(base, ENGINES[i].file)) return ENGINES[i].id;
    return 0;
}
static volatile u16 *row_keys(void *view, u32 row) { return (volatile u16 *)((u8 *)view + S_INST_KEYS + row * S_INST_ROW_STRIDE); }
static volatile u16 *native_keys(void *view, u32 row) { return (volatile u16 *)((u8 *)view + S_INST_NATIVE_KEYS + row * 16u); }
#define DR_ROW 1u
/* D5's stock entry, its first instruction replayed (naked: r0 stays the view). */
__attribute__((naked, noinline)) static void stock_refresh(void *view) {
    __asm volatile("push.w {r4-r11, lr}\n\t movw ip, %[lo]\n\t movt ip, %[hi]\n\t bx ip"
                   :: [lo] "i"((S_INST_REFRESH + 5u) & 0xFFFFu), [hi] "i"((S_INST_REFRESH + 5u) >> 16));
}
/* D5 (INST_REFRESH entry): the Config row's keys before stock's refresh reads them. On a Kick page our seven keys and
 * Poly Mode; on any other page, a row we left behind (the sample changed under the page) gets stock's keys back. */
__attribute__((used, retain)) void dr_refresh(void *view) {
    volatile u16 *k = row_keys(view, DR_ROW), *n = native_keys(view, DR_ROW);
    volatile Dk4 *d = dk4();
    u32 e = DRUMS_ON && d->keys == 1u ? dr_page_engine(view) : 0u;
    d->view = (u32)view; d->c_cell = W(view, S_INST_CELL); d->c_file = W(view, S_INST_FILE); d->c_t = TICK_MS; d->c_eng = e;
    if (e) {
        if (k[0] != KEY_DR) d->pages++;
        for (u32 i = 0; i < DRUM_KEYS_N; i++) k[i] = (u16)(KEY_DR + i);
        k[DRUM_KEYS_N] = (u16)S_KEY_POLY_MODE;
    } else if (k[0] == KEY_DR) {
        for (u32 i = 0; i < 8u; i++) k[i] = n[i];
    }
    stock_refresh(view);
}
/* The rows can disagree with the pad without D5 running (review rc29 S1): slot 13's event 0xA8 runs stock's cell set
 * alone (0x0815514A -> 0x0815443C: the page table's keys copied into the rows, no refresh; stock's page select
 * 0x081547EA posts it after its own cell set and file select), and 0xC5 (the file changed) sets only the title
 * (0x08155122). dr_page_engine's answer is kept for the page's (view, cell, file) and asked again after 250 ms (a file
 * renamed under the same node), so the tick can compare on every pass for a few loads. */
static u32 dr_engine_now(volatile Dk4 *d, void *view, u32 now) {
    u32 cell = W(view, S_INST_CELL), file = W(view, S_INST_FILE);
    if (d->view != (u32)view || d->c_cell != cell || d->c_file != file || now - d->c_t >= 250u) {
        d->c_eng = d->keys == 1u ? dr_page_engine(view) : 0u;
        d->view = (u32)view; d->c_cell = cell; d->c_file = file; d->c_t = now;
    }
    return d->c_eng;
}
/* A Kick page whose row is stock's, or another page that still shows the Kick's keys: D5 again (it decides afresh). */
static int dr_heal(volatile Dk4 *d, void *view, u32 e) {
    if ((e != 0u) == (row_keys(view, DR_ROW)[0] == KEY_DR)) return 0;
    d->heals++;
    dr_refresh(view);
    return 1;
}
/* An encoder turn of one of the row's first seven slots while it shows stock's keys (the cell set just ran) on a Kick
 * page: the slot the player turned showed a Kick knob, not the Reverse .. Loop Fade now under it. Dropped, row repaired. */
static int dr_stale_turn(volatile Dk4 *d, void *view, u32 key) {
    volatile u16 *k = row_keys(view, DR_ROW);
    if (!DRUMS_ON || d->keys != 1u || !key || k[0] == KEY_DR) return 0;
    u32 hit = 0;
    for (u32 i = 0; i < DRUM_KEYS_N; i++) hit |= k[i] == key;
    return hit && dr_page_engine(view) ? dr_heal(d, view, 1u) : 0;
}
/* CONTEXT_GET's stock entry, its first two instructions replayed (as X3's own path to stock). */
__attribute__((naked, noinline)) static u32 stock_context(void *model, u32 *ctx, u32 key, u32 *out) {
    __asm volatile("push {r4, r5, r6, r7, lr}\n\t sub sp, #0xc\n\t movw ip, %[lo]\n\t movt ip, %[hi]\n\t bx ip"
                   :: [lo] "i"((S_CONTEXT_GET + 5u) & 0xFFFFu), [hi] "i"((S_CONTEXT_GET + 5u) >> 16));
}
/* X3 for the drum keys: the knob of the pad CONTEXT_GET asks about (ctx points at its handle: the page's row handle). */
__attribute__((used, retain)) u32 dr_context(void *model, u32 *ctx, u32 key, u32 *out) {
    volatile Dk4 *d = DK4;                                   /* keys not ours (taken, or not claimed yet): stock's */
    if (!DRUMS_ON || d->magic != DK4_MAGIC || d->keys != 1u) return stock_context(model, ctx, key, out);
    u8 v[DRUM_KNOBS];
    dk_values(ctx ? *ctx : 0u, ENGINE_KICK, v);
    *out = key - KEY_DR < DRUM_KEYS_N ? v[key - KEY_DR] : 0u;
    return 1;
}
/* D6 (INST_EV_SLOT): an encoder edit (EV_ENCODER: ev+8 key, ev+12 the slot's new value) of a drum key sets the pad's
 * knob (clamped to the engine's range), publishes it and refreshes the page; it never reaches the model (stock would post
 * it up the view tree to the model as a property of the pad: 0x08154AB2); one on a page that is no longer a Kick's is
 * dropped and the row put right. While the keys are not ours (DK4 keys != 1: taken by stock) they are stock's keys.
 * A turn of the row stock's cell set has just rewritten under a Kick page is dropped (dr_stale_turn). The rest is stock's. */
__attribute__((used, retain)) u32 dr_page_event(void *view, void *ev) {
    volatile Dk4 *dd = dk4();
    if (DRUMS_ON && dd->keys == 1u && H(ev, 0) == S_EV_ENCODER && W(ev, 8) - KEY_DR < DRUM_KEYS_N) {   /* our keys only */
        u32 j = W(ev, 8) - KEY_DR, e = dr_page_engine(view);
        const DrEngine *g = dr_engine(e);
        if (!g) dr_heal(dd, view, 0u);                        /* a Kick row left on another page: stock's back, turn dropped */
        if (g) {
            u32 cell = W(view, S_INST_CELL);
            u8 v[DRUM_KNOBS];
            int x = (int)W(ev, 12);
            u32 id = dk_values(cell, e, v);
            if (id != e) { id = e; for (u32 i = 0; i < DRUM_KNOBS; i++) v[i] = g->defaults[i]; }   /* another engine's entry */
            u8 nv = (u8)(x < (int)g->lo[j] ? g->lo[j] : x > (int)g->hi[j] ? g->hi[j] : (u32)x);
            volatile Dk4 *d = dk4();
            if (nv != v[j] || dk_find(d, cell) < 0) { v[j] = nv; dk_mark(cell, id, v); publish(an4()); }
            d->edits++;
            FN(S_PROJECT_TOUCH, void (*)(void *))(MODEL);
            dr_refresh(view);                                 /* the slot shows the value as stored */
        }
        return 0;                                             /* never forwarded to the model */
    }
    if (H(ev, 0) == S_EV_ENCODER && dr_stale_turn(dd, view, W(ev, 8))) return 0;
    return FN(S_INST_EVENT, u32 (*)(void *, void *))(view, ev);
}
/* The instrument page's tick (INST_TICK_SLOT): on a Kick page, the page title reads "Kick CPU 63% drums 4%" (the M7's
 * LOAD, and the drums' share of the blocks they rendered in, both over the last 500 ms) with " !" for 2 s after a block
 * over budget, "Kick (DSP off)" before the M7 has taken the synth area; at most 4 times a second (SLOT_TITLE marks the
 * label only when the text changed). Stock sets the title back to the file's name whenever the pad or its file changes. */
__attribute__((used, retain)) void dr_view_tick(void *view, void *flags) {
    volatile Dk4 *d = dk4();
    volatile An4 *a = an4();
    u32 now = TICK_MS;
    u32 e = DRUMS_ON && view ? dr_engine_now(d, view, now) : 0u;
    if (DRUMS_ON && view) dr_heal(d, view, e);               /* every pass: a compare; D5 only on a mismatch */
    if (DRUMS_ON && view && now - d->label_t >= 250u) {
        d->label_t = now;
        const DrEngine *g = dr_engine(e);
        if (g) {
            char text[40]; u32 len = 0;
            Out o = {text, &len, sizeof(text) - 1u};
            puts_(&o, g->names == KICK_NAMES ? "Kick" : "Drum");
            if (!ready()) puts_(&o, " (DSP off)");
            else {
                puts_(&o, " CPU "); dec(&o, (a->cpu_pm + 5u) / 10u); puts_(&o, "%");
                puts_(&o, " drums "); dec(&o, (d->drum_pm + 5u) / 10u); puts_(&o, "%");
                if (a->over_t && now - a->over_t < 2000u) puts_(&o, " !");
            }
            text[len] = 0;
            FN(S_SLOT_TITLE, void (*)(void *, const char *))((u8 *)view + S_INST_TITLE, text);
        }
    }
    FN(S_INST_TICK, void (*)(void *, void *))(view, flags);
}
#endif

#if DRUMS_BUILT
/* ---- X1 / X2: project and preset XML (the drums' only: Analog's settings are stock properties since BE4.40) ---------- */
static void attr(void *params, const char *name, u32 v) {
    void *a = FN(S_XML_ADD, void *(*)(void *, const char *))(params, name);
    if (a) FN(S_XML_SET_U32, void (*)(void *, u32))(a, v);
}
/* S6c-2 (D5 / D7 / D8 XML): a pad with a knob entry gets bento_drum_engine (the engine id, 1 = Kick) and bento_drum_k1 ..
 * bento_drum_k7 (knob i + 1, in the engine's units) on its params element. Pads (children of a track node) are written
 * and read by the same writer / reader as instruments; stock ignores attributes it does not know and drops them on its
 * own save. Generic names: the knob map belongs to the engine (S6d's engines reuse them). */
static const char AT_DR_ENGINE[] = "bento_drum_engine";
static const char *const AT_DR_K[DRUM_KEYS_N] = {"bento_drum_k1", "bento_drum_k2", "bento_drum_k3", "bento_drum_k4",
                                                 "bento_drum_k5", "bento_drum_k6", "bento_drum_k7"};
static int is_cell(void *object) { u32 t = obj_type(object); return t == DR_TYPE_CELL || t == DR_TYPE_CELL2; }
static void dr_save(void *object, void *params) {
    u32 h = W(object, 0x10);
    volatile Dk4 *d = DK4;
    if (!dk_any(d) || dk_find(d, h) < 0 || !is_cell(object)) return;
    u8 v[DRUM_KNOBS];
    u32 id = dk_values(h, 0, v);
    attr(params, AT_DR_ENGINE, id);
    for (u32 j = 0; j < DRUM_KEYS_N; j++) attr(params, AT_DR_K[j], v[j]);
    bump(&d->saves);
}
static u32 get(void *params, const char *name, u32 dflt) {
    u32 v = 0;
    void *a = FN(S_XML_FIND, void *(*)(void *, const char *))(params, name);
    return a && !FN(S_XML_GET_U32, int (*)(void *, u32 *))(a, &v) ? v : dflt;
}
/* A pad read from a project or preset: its knobs (each clamped to the engine's range, a missing one at its default), or,
 * without bento_drum_engine (or with an engine this build has not), no entry (a stale one is cleared). */
static void dr_load(void *params, void *object) {
    u32 h = W(object, 0x10);
    if (!h || !is_cell(object)) return;
    const DrEngine *g = params ? dr_engine(get(params, AT_DR_ENGINE, 0)) : 0;
    if (!g) {
        volatile Dk4 *d = DK4;
        if (dk_any(d) && dk_find(d, h) >= 0) dk_mark(h, 0, 0);
        return;
    }
    u8 v[DRUM_KNOBS];
    for (u32 j = 0; j < DRUM_KNOBS; j++) {
        u32 x = j < DRUM_KEYS_N ? get(params, AT_DR_K[j], g->defaults[j]) : g->defaults[j];
        v[j] = (u8)(x < g->lo[j] ? g->lo[j] : x > g->hi[j] ? g->hi[j] : x);
    }
    dk_mark(h, g->id, v);
    bump(&dk4()->loads);
}
__attribute__((used, retain)) void an_save(void *object, void *params) {
    if (!object || !params) return;
    if (DRUMS_ON) dr_save(object, params);
}
__attribute__((used, retain)) void an_load(void *node, void *object) {
    if (!node || !object) return;
    void *params = FN(S_XML_CHILD, void *(*)(void *, const char *))(node, "params");
    if (DRUMS_ON) dr_load(params, object);
}
#define DSTATS ((volatile SaDrumStats *)SA(SA_DSTATS))
/* The drums' share of the blocks they rendered in over the last 500 ms (the M7's stats: cycles >> 4 summed, blocks). */
static void drum_cpu(volatile Dk4 *d) {                     /* in step with cpu_update's 500 ms */
    u32 blocks = ready() ? DSTATS->blocks : 0u, sum = ready() ? DSTATS->sum16 : 0u;
    if (!d->d_ok || blocks < d->d_blocks || sum < d->d_sum) { d->d_blocks = blocks; d->d_sum = sum; d->d_ok = 1; d->drum_pm = 0; return; }
    d->drum_pm = blocks == d->d_blocks ? 0u : ((sum - d->d_sum) / (blocks - d->d_blocks)) * 16u / 1280u;
    d->d_blocks = blocks; d->d_sum = sum;
}
#endif

/* ---- X6: the UI tick and the black box lines -------------------------------------------------------------------------
 * A new boot (the config line's generation moved) restarts the per-boot counters (np_keys' answer is this boot's already:
 * it runs in model init, before the first tick). */
__attribute__((used, retain)) void an_tick(void) {
    volatile An4 *a = an4();
    u32 now = TICK_MS, g = boot_gen();
    if (g && g != a->gen) {
        u32 p = lock();
        a->gen = g; a->src = 0;
        a->heals = a->resends = a->page_rows = a->over = a->cpu_ok = a->dials = a->browse = 0;
        a->dial[0].view = a->dial[1].view = 0;
        a->last_h[0] = a->last_h[1] = a->last_t[0] = a->last_t[1] = 0;
#if DRUMS_BUILT
        volatile Dk4 *d = dk4();
        d->gen = g; d->keys = 0; d->d_ok = 0;
        d->pages = d->edits = d->saves = d->loads = d->full = d->evicted = d->refused = d->heals = 0;
        d->view = 0;                                        /* the page cache names nothing of this boot */
#endif
        unlock(p);
    }
#if DRUMS_BUILT
    an_forget();
    if (DRUMS_ON) drum_keys();
#endif
    u32 t0 = a->cpu_t;
    cpu_update(a, now);
#if DRUMS_BUILT
    if (DRUMS_ON && a->cpu_t != t0) drum_cpu(dk4());
#else
    (void)t0;
#endif
    publish(a);
}

static void field(Out *o, const char *name, u32 v) { puts_(o, name); dec(o, v); }
#if DRUMS_BUILT
/* S6c: the DRUMS line, from the M7's drum stats lines (cleaned after each block with a drum voice): the hook's average and
 * peak share of the 1,280,000-cycle block, the share histogram, and what the voices did. S6c-2: then the page's counters
 * (DK4): keys, Kick pages shown, knob edits, XML saves / loads, pads with knobs, capacity, and the handle hooks (carried: pads
 * of a cloned track, dups: a pad copied by stock's child copy, dropped, forgot, clone_skips). */
static void drums_ui(Out *o) {
    volatile Dk4 *d = dk4();
    u32 n = 0;
    for (u32 i = 0; i < DK4_N; i++) n += (d->e[i].word & DK_USED) && d->e[i].handle ? 1u : 0u;
    puts_(o, " | ui keys="); puts_(o, d->keys == 1u ? "ok" : d->keys ? "TAKEN" : "-");
    if (d->keys > 1u) field(o, ":", d->keys & 0xFFu);
    field(o, " pages=", d->pages); field(o, " heals=", d->heals); field(o, " edits=", d->edits); field(o, " saves=", d->saves); field(o, " loads=", d->loads);
    field(o, " pads=", n); field(o, " full=", d->full); field(o, " evicted=", d->evicted);
    field(o, " | handles carried=", d->carried); field(o, " dups=", d->dups); field(o, " dropped=", d->dropped);
    field(o, " forgot=", d->forgot); field(o, " clone_skips=", d->clone_skips);
}
static void drums_line(Out *o) {
    puts_(o, "DRUMS");
    if (!ready()) { puts_(o, " area not taken"); drums_ui(o); puts_(o, "\r\n"); return; }
    volatile SaDrumStats *s = DSTATS;
    u32 blocks = s->blocks, avg = blocks ? (s->sum16 / blocks) << 4 : 0u;
    field(o, " blocks=", blocks);
    field(o, " avg_cyc=", avg); field(o, " (pm=", avg / 1280u); puts_(o, ")");
    field(o, " peak_cyc=", s->peak); field(o, " (pm=", s->peak / 1280u); puts_(o, ")");
    field(o, " share>=5%=", s->hist[0]); field(o, " >=15=", s->hist[1]); field(o, " >=30=", s->hist[2]);
    field(o, " voices_peak=", s->cells_peak); field(o, " renders=", s->renders);
    field(o, " | starts=", s->starts); field(o, " retrigs=", s->retrigs); field(o, " quiet_ends=", s->quiet_ends);
    field(o, " cap_ends=", s->cap_ends);
    field(o, " | detects=", s->detects); field(o, " carriers=", s->carriers); field(o, " unbuilt=", s->unbuilt);
    field(o, " knob_reads=", s->knob_reads); field(o, " n_max=", s->n_max);
    field(o, " unbound=", s->unbound); field(o, " short_bufs=", s->short_bufs); field(o, " knob_hits=", s->knob_hits);
    drums_ui(o);
    puts_(o, "\r\n");
}
#endif
/* The live project's Analog oscillators (Wavetable tracks' type-6 children whose Type is 1), for the black box. */
static u32 count_oscs(void) {
    void *store = (void *)W(MODEL, 0);
    u32 n = 0;
    if (!store || !np_ok()) return 0;
    u32 objs = W(store, S_STORE_COUNT);
    for (u32 i = 0; i < objs && i < 12u; i++) {
        void *o = (void *)W(store, 4u * i);
        if (!o || obj_type(o) != S_TYPE_WAVETABLE) continue;
        u32 k = W(o, S_NODE_CHILD_N);
        for (u32 c = 0; c < k && c < 0x244u; c++) {
            void *ch = (void *)W(o, S_NODE_CHILDREN + 4u * c);
            if (ch && obj_type(ch) == TYPE_WT_OSC && analog_osc(W(ch, 0x10))) n++;
        }
    }
    return n;
}
__attribute__((used, retain)) void an_print(char *buf, u32 *len, u32 max) {
    Out o = {buf, len, max};
#if DRUMS_BUILT
    if (DRUMS_ON) drums_line(&o);
#endif
    if (!ANALOG_ON) return;
    volatile An4 *a = an4();
    volatile SaM7 *m = M7HDR;
    u32 take = SYNTH_TAKE;
    if ((take >> 24) != SYNTH_TAKE_TAG) take = 0;              /* never written since the slice was new */
    puts_(&o, "ANALOG");
    if (!ready()) {
        puts_(&o, " area not taken");
        if ((take & 0xFFu) == SYNTH_TAKE_REGION) { field(&o, " (the pool kept less than 1 MB: Region only, times=", (take >> 8) & 0xFFFFu); puts_(&o, ")"); }
        else if (m->magic == SA_M7_MAGIC) { field(&o, " (header for boot ", m->boot); field(&o, ", this boot ", boot_gen()); puts_(&o, ")"); }
    } else {
        field(&o, " gen=", m->gen);
        if ((take >> 8) & 0xFFFFu) field(&o, " region_only_takes=", (take >> 8) & 0xFFFFu);
    }
    puts_(&o, " keys="); puts_(&o, a->keys == 1u ? "ok" : a->keys ? "TAKEN" : "-");
    if (a->keys > 1u) field(&o, ":", a->keys & 0x3FFu);
    field(&o, " oscs=", count_oscs());
    if (ready()) {
        /* M7 stats line (cleaned by the M7 after each block): average and peak share of the 1,280,000-cycle block */
        volatile SaStats *s = STATS;
        u32 blocks = s->blocks, sum16 = s->sum16;
        field(&o, " | blocks=", blocks);
        u32 avg = blocks ? (sum16 / blocks) << 4 : 0u;
        field(&o, " avg_cyc=", avg); field(&o, " (pm=", avg / 1280u); puts_(&o, ")");
        field(&o, " peak_cyc=", s->peak); field(&o, " (pm=", s->peak / 1280u); puts_(&o, ")");
        field(&o, " share<5%=", s->hist[0]); field(&o, " 5-15=", s->hist[1]); field(&o, " 15-30=", s->hist[2]);
        field(&o, " 30+=", s->hist[3]);
        field(&o, " osc_peak=", s->osc_peak); field(&o, " voices_peak=", s->voices_peak);
        field(&o, " renders=", s->renders); field(&o, " n_max=", s->n_max); field(&o, " sync_skipped=", s->sync_skipped);
        field(&o, " props=", s->props); field(&o, " roots=", s->misses);
    }
    field(&o, " | load_pm=", a->cpu_pm); field(&o, " over_with_analog=", a->over);
    field(&o, " | ui page_rows=", a->page_rows); field(&o, " heals=", a->heals); field(&o, " resends=", a->resends);
    field(&o, " dials=", a->dials); field(&o, " browse_blocked=", a->browse);
    puts_(&o, "\r\n");
}
