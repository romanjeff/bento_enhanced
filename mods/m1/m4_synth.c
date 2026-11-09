/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Synth UI on the M4 (S6a: Analog oscillators on Wavetable tracks), docs/plan-synths-168.md.
 *
 * A port of bento-playground's A1 "Analog" UI (eu4_work/integration/kick_ui.c, 9fc7787, GPL-3.0-or-later),
 * rewritten for our layout: the track's Analog flag, Sync and Glide live in our own 16-entry table (AN4, in the
 * reset-surviving slice: diag_shared.h AN4_SLICE), not in the drum knob table, and the M7 reads a published copy
 * in the synth area (synth_shared.h). The registry keys come from a reviewed allow-list (synth_keys), not fixed
 * numbers. Hooks (all 1.6.8; build.py writes them):
 *   A3/A4  instrument menu: a 9th cell "Analog" (action S_ACT_ANALOG) in stock's flash table, count 9
 *   A5     the menu view's event slot -> an_menu_event: our action = stock's Wavetable action + flag; any of
 *          stock's instrument actions clears the flag of the track it replaces
 *   A6     the Wavetable page's key copy (bl memcpy) -> an_page_keys: Glide in WT1's free 4th slot,
 *          Sync in WT2's, on Analog tracks only
 *   A7     six pane texts (bl SLOT_TITLE) -> an_pane_text: "Analog" / "Saw > Square > Pulse"
 *   A9     the synth view's event slot -> an_view_event: encoder edits (0xCF) of our keys are ours;
 *          stock would forward them to the model as 0xD0 (0x08161158)
 *   A10    the synth view's tick slot -> an_view_tick: the CPU indicator in WT1's "No WAV" label (D3)
 *   X1/X2  instrument XML save / load -> an_save / an_load: bento_analog, bento_analog_sync,
 *          bento_analog_glide on the track's params element (stock ignores unknown attributes)
 *   X3     CONTEXT_GET -> an_context for our two keys only (the wrapper tests the key first)
 *   X6     m4_diag.c calls the entry table: slot 0 an_tick (every UI loop pass), slot 1 an_print
 *          (save time: the black box's ANALOG line)
 *   H1     stock's one base object constructor (OBJ_CTOR) -> an_forget first: a handle about to be given out
 *          carries no Analog state from an earlier boot (decision 7: nothing new inherits Analog)
 *   H2     the generic node's clone slot (NODE_VT_CLONE) -> an_clone: a copy (load commit, save snapshot, preset
 *          apply / save, paste) gets its source's Analog state under its own new handle
 *   H3     the generic node's destroy slot (NODE_VT_DESTROY) -> an_destroy: a freed instrument's entry goes with it
 * S6c-2 (BE4.31, docs/plan-synths-168.md "S6c-2"): the drum page, after bento-playground's K7 Kick UI
 * (eu4_work/integration/kick_ui.c, 9fc7787, GPL-3.0-or-later), rewritten: the pad's knobs in our own table (DK4, the
 * slice), seven reviewed keys, no menu entry (D6: the carrier is loaded from the browser), no FatFS, no Mod Matrix.
 *   D5     the instrument page's refresh entry -> dr_refresh: on a Kick pad the Config row shows the Kick's knobs
 *   D6     the page's event slot -> dr_page_event: encoder edits of the drum keys set the pad's knobs (never the model)
 *          the page's tick slot -> dr_view_tick: the CPU readout in the page's title label, and puts the row right
 *          when stock rewrote it without a refresh (dr_heal: the cell set of event 0xA8, a file change 0xC5)
 *   X1-X3  also for pads: bento_drum_engine, bento_drum_k1..k7; CONTEXT_GET answers the drum keys (dr_context)
 *   H1-H3  also drop / carry the pads' entries (a track's children: H2 walks them pairwise, H3 drops them)
 *   H4     stock's copy of one child (CHILD_DUP) -> dr_dup: a copied pad keeps its knobs
 * The sites X1-X3 and H1-H3 serve both synths (build.py "Synth:" writes): a bisect leaves them stock only when both are off.
 * Instrument handles (BE4.22, docs/plan-synths-168.md "Instrument handles"): every M4 object gets its handle from
 * OBJ_CTOR's counter, which only goes up within a boot, and a copy always gets a new one. With H1-H3 every AN4
 * entry names a live object of this boot; BE4.21's 30 s sweep and 10 s swap watch are gone.
 * Tasks: an_load and an_save run in stock's "pcmStreamer" task (command queue 0x0812B27C, entry 0x08114EA0: a
 * project load can come before the black box's first tick, review 2026-11-08), everything else in the UI task.
 * Both change AN4 only with interrupts masked (a few stores); only the UI task writes the synth area.
 * Position (key 0xC9) stays stock's: on an Analog track it is the oscillator's shape. Integer code
 * only (soft-float M4 build); strict-align on 1.6.8 like every M4 source. */
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
#define TAB ((volatile SaTrack *)SA(SA_ANTAB))

#define SYNC_KEY S_KEY_AN_SYNC
#define GLIDE_KEY S_KEY_AN_GLIDE

/* ---- AN4: the M4's table (diag_shared.h AN4_SLICE, 256 B, M4 only) ---------------------------------------
 * An entry: handle (0 = free) and a word: [3:0] flags (AN_ON, AN_SYNC), [13:4] glide 0..1000, [31:16] 0.
 * The table survives an M4 reset (the slice); stock's handle counter does not (it starts again at 1). So an entry
 * whose handle is not below the counter is from an earlier boot and names no object of this one: H1 drops every such
 * entry before the counter can reach it (in practice: all of them, at the first of stock's static constructors). */
#define AN4_MAGIC 0x32344E41u                /* "AN42": BE4.22's layout (a v4.21 table is started again, review rc23 I5) */
#define AN4_N SA_ANTAB_N
#ifdef BE_NO_ANALOG                                 /* S6c: a BE_DISABLE=analog bisect keeps the drums (and this ELF): */
#define ANALOG_ON 0                                 /* the tick and the ANALOG line do nothing, the code stays referenced */
#else
#define ANALOG_ON 1
#endif
#if defined(BE_OMIT_DRUMS) || defined(BE_NO_DRUMS)  /* S6c-2: the drum page (BE_DISABLE=drums: its sites stay stock, the */
#define DRUMS_ON 0                                  /* tick, keys and XML do no drum work) */
#else
#define DRUMS_ON 1
#endif
#ifndef BE_OMIT_DRUMS
#define DRUMS_BUILT 1
#else
#define DRUMS_BUILT 0
#endif
#define HANDLE_CTR (*(volatile u32 *)S_OBJ_HANDLE_CTR)
typedef struct {
    u32 magic, gen;                          /* gen: the config line's boot generation this table last saw */
    u32 serial, pub, pub_m7gen, pub_boot;    /* serial: +1 per change; pub*: what is published in the synth area */
    u32 keys, r7, r8;                        /* keys: synth_keys() 1 ok, 0x100 | taken bits, 0 not yet */
    u32 carried, dropped, forgot;            /* H2 copies given Analog, H3 entries freed, H1 / stale entries dropped */
    u32 cpu_t, seen, sum, h3;                /* CPU indicator: last update, the LOAD counters (D0L) then */
    u32 over_t, cpu_pm, over, stats_blocks;  /* last block over budget, average LOAD, over-budget blocks with Analog */
    u32 menus, edits, saves, loads, full, evicted, label_t, cpu_ok, menu_miss, menu_same, r30, r31;
    struct { u32 handle, word; } e[AN4_N];
} An4;
_Static_assert(sizeof(An4) == AN4_SLICE_END - AN4_SLICE, "AN4 fills its 256 B");
#define AN4 ((volatile An4 *)SLICE(AN4_SLICE))
static inline u32 flags_of(u32 w) { return w & 15u; }
static inline u32 glide_of(u32 w) { return (w >> 4) & 0x3FFu; }
static inline u32 word_of(u32 flags, u32 glide) {
    return (flags & 15u) | ((glide > AN_GLIDE_MAX ? AN_GLIDE_MAX : glide) << 4);
}

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }
static void dsb(void) { __asm volatile("dsb" ::: "memory"); }
static u32 lock(void) { u32 p; __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory"); return p; }
static void unlock(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }
static void bump(volatile u32 *c) { u32 p = lock(); (*c)++; unlock(p); }   /* a counter two tasks may move */

static volatile An4 *an4(void) {
    volatile An4 *a = AN4;
    if (a->magic != AN4_MAGIC) {             /* a cold start (slice garbage), or the canary pattern before the first use */
        u32 p = lock();
        if (a->magic != AN4_MAGIC) {
            for (u32 i = 0; i < sizeof(An4) / 4u; i++) ((volatile u32 *)a)[i] = 0;
            a->magic = AN4_MAGIC;
        }
        unlock(p);
    }
    return a;
}
static int find_i(volatile An4 *a, u32 h) {
    if (!h) return -1;
    for (u32 i = 0; i < AN4_N; i++)
        if (a->e[i].handle == h && (flags_of(a->e[i].word) & AN_ON)) return (int)i;
    return -1;
}
/* Analog flags of a handle: AN_ON | AN_SYNC, 0 if not Analog; glide into *g if given. */
static u32 lookup(u32 h, u32 *g) {
    volatile An4 *a = an4();
    u32 p = lock();
    int i = find_i(a, h);
    u32 w = i < 0 ? 0u : a->e[i].word;
    unlock(p);
    if (g) *g = glide_of(w);
    return i < 0 ? 0u : flags_of(w) & (AN_ON | AN_SYNC);
}
/* Is h an object of the live project (stock's store [MODEL])? A load's scratch copies, a save's snapshot and the
 * clipboard are other stores. */
static int live(u32 h) {
    void *obj = 0;
    return h && FN(S_RESOLVE_OBJECT, u32 (*)(void *, u32, void **))((void *)W(MODEL, 0), h, &obj) && obj;
}
/* A free slot (under the lock). Entries from an earlier boot never reach here: H1 dropped them at boot. */
static int free_slot(volatile An4 *a) {
    for (u32 k = 0; k < AN4_N; k++)
        if (!(flags_of(a->e[k].word) & AN_ON) || !a->e[k].handle) return (int)k;
    return -1;
}
/* flags 0: not Analog any more. A full table gives up its oldest copy: the lowest handle that is not in the live
 * project (handles only go up, so that is the copy made first: a load's scratch before a save's snapshot before a
 * paste). Live tracks are never given up; if there is no copy to give up, the mark is lost and counted (full). At
 * most 8 live tracks + the 8 copies of a load commit or a save snapshot in progress are needed at once. */
static void mark(u32 h, u32 flags, u32 glide) {
    if (!h) return;
    volatile An4 *a = an4();
    for (int pass = 0; pass < 2; pass++) {
        u32 victim = 0;
        if (pass)                                             /* stock's lookups outside the lock */
            for (u32 k = 0; k < AN4_N; k++) {
                u32 x = a->e[k].handle;
                if (x && x != h && (!victim || x < victim) && !live(x)) victim = x;
            }
        u32 p = lock();
        int i = find_i(a, h);
        if (i < 0 && flags) {
            i = free_slot(a);
            for (u32 k = 0; i < 0 && victim && k < AN4_N; k++)
                if (a->e[k].handle == victim) { i = (int)k; a->evicted++; }
            if (i < 0 && !pass) { unlock(p); continue; }
            if (i < 0) a->full++;
        }
        if (i >= 0) {
            if (flags) { a->e[i].handle = h; a->e[i].word = word_of(flags | AN_ON, glide); }
            else { a->e[i].word = 0; a->e[i].handle = 0; }
            a->serial++;
        }
        unlock(p);
        return;
    }
}

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

/* ---- H1 / H2 / H3: stock's object lifecycle (any task: the storage task's loads and saves, the UI task's menu
 * and clipboard). AN4 is read without the lock first: the common case (nothing to do) costs a 16-entry scan. */
/* H1: OBJ_CTOR's first act (build.py's wrapper keeps every register): the counter is the handle the object is about
 * to get; every entry at or above it is from an earlier boot. Also runs for stock's static constructors at M4 boot:
 * before the table exists (no magic) it only reads. */
__attribute__((used, retain)) void an_forget(void) {
    u32 ctr = HANDLE_CTR;
    if (!ctr) return;                                          /* (never on the device: .data is copied first) */
#if DRUMS_BUILT
    volatile Dk4 *d = DK4;                                     /* S6c-2: the drum knob table survives a reset as AN4 does */
    if (d->magic == DK4_MAGIC) {
        u32 any = 0;
        for (u32 k = 0; k < DK4_N; k++) any |= d->e[k].handle >= ctr;
        if (any) {
            u32 p = lock(), n = 0;
            ctr = HANDLE_CTR;
            for (u32 k = 0; k < DK4_N; k++)
                if (d->e[k].handle >= ctr) { d->e[k].word = 0; d->e[k].handle = 0; n++; }
            if (n) { d->serial++; d->forgot += n; }
            unlock(p);
        }
    }
#endif
    volatile An4 *a = AN4;
    if (a->magic != AN4_MAGIC) return;
    u32 any = 0;
    for (u32 k = 0; k < AN4_N; k++) any |= a->e[k].handle >= ctr;
    if (!any) return;
    u32 p = lock(), n = 0;
    ctr = HANDLE_CTR;
    for (u32 k = 0; k < AN4_N; k++)
        if (a->e[k].handle >= ctr) { a->e[k].word = 0; a->e[k].handle = 0; n++; }
    if (n) { a->serial++; a->forgot += n; }
    unlock(p);
}
/* H3: NODE_DESTROY (obj, pool): every top-level object's way out (store clear, remove, a failed create). Stock frees its
 * children (the pads of a track) directly, so their drum entries go here too (S6c-2). */
__attribute__((used, retain)) u32 an_destroy(void *obj, void *pool, u32 r2, u32 r3) {
    volatile An4 *a = AN4;
    u32 h = obj ? W(obj, 0x10) : 0u, any = 0;
    if (h && a->magic == AN4_MAGIC) {
        for (u32 k = 0; k < AN4_N; k++) any |= a->e[k].handle == h;
        if (any) {
            u32 p = lock();
            for (u32 k = 0; k < AN4_N; k++)
                if (a->e[k].handle == h) { a->e[k].word = 0; a->e[k].handle = 0; a->serial++; a->dropped++; }
            unlock(p);
        }
    }
#if DRUMS_BUILT
    volatile Dk4 *d = DK4;
    if (obj && dk_any(d)) {
        u32 n = W(obj, S_NODE_CHILD_N);
        for (u32 c = 0; c < n && c < 0x244u; c++) {
            void *ch = (void *)W(obj, S_NODE_CHILDREN + 4u * c);
            u32 ph = ch ? W(ch, 0x10) : 0u;
            if (dk_find(d, ph) >= 0) { dk_mark(ph, 0, 0); bump(&d->dropped); }
        }
    }
#endif
    return FN(S_NODE_DESTROY, u32 (*)(void *, void *, u32, u32))(obj, pool, r2, r3);
}
/* H2: NODE_CLONE (src, pool, &out) -> ok: the copy has a new handle (OBJ_CTOR) and gets src's Analog state. S6c-2: stock
 * clones a node's children (the pads) in order, each with a new handle (0x0812F2B4, not through a vtable), so the pads'
 * drum entries are carried pairwise here; a copy whose child count differs (a child stock failed to copy) is skipped and
 * counted (clone_skips), not guessed at. */
__attribute__((used, retain)) u32 an_clone(void *src, void *pool, void **out) {
    u32 ok = FN(S_NODE_CLONE, u32 (*)(void *, void *, void **))(src, pool, out);
    if (ok && src && out && *out && *out != src) {
        u32 glide = 0, f = lookup(W(src, 0x10), &glide);
        if (f) { mark(W(*out, 0x10), f, glide); bump(&an4()->carried); }
#if DRUMS_BUILT
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
#endif
    }
    return ok;
}
#if DRUMS_BUILT
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

/* ---- the synth area: the handshake and the published copy (synth_shared.h) -----------------------------------
 * ready(): the M7 holds the reservation and its header is stamped for this M4 boot. Only then may anything be
 * written there (before, the area is stock's pool memory). UI task only. */
static int ready(void) {
    volatile SaM7 *m = M7HDR;
    u32 boot = boot_gen();
    return boot && m->magic == SA_M7_MAGIC && m->layout == SA_LAYOUT && m->total == SYNTH_TOTAL && m->gen &&
           m->boot == boot;
}
static void put_line(volatile SaTrack *t, u32 h, u32 flags, u32 glide) {
    u32 s = (t->head + 2u) & ~1u;
    t->head = s | 1u; t->tail = s | 1u; dsb();
    t->handle = flags ? h : 0u; t->flags = flags; t->glide = glide; dsb();
    t->tail = s; t->head = s; dsb();
}
/* The whole table into the synth area when it changed, or for a new M7 generation / M4 boot (an M7-only reset
 * keeps the M4's lines there, a new M4 boot finds whatever the last one left: both are rewritten). */
/* S6c-2: a drum knob line (the M4 has no data cache: the stores reach SDRAM in order, dsb between the seqlock steps; the M7
 * invalidates the lines before it reads them, drums.cpp knob_entry). */
static void put_knobs(volatile SaKnobs *t, u32 h, u32 word, u32 v0, u32 v1) {
    u32 s = (t->head + 2u) & ~1u;
    t->head = s | 1u; t->tail = s | 1u; dsb();
    t->handle = (word & DK_USED) ? h : 0u; t->word = (word & DK_USED) ? DK_USED : 0u;   /* no engine override (0) */
    t->v0 = v0; t->v1 = v1; dsb();
    t->tail = s; t->head = s; dsb();
}
#define KTAB ((volatile SaKnobs *)SA(SA_DKTAB))
/* Both tables into the synth area, each when it changed, or for a new M7 generation / M4 boot. The M4 header carries a
 * serial per table (serial: Analog, drum_serial: drums), each moved by 2 on its table's publish. */
static void publish(volatile An4 *a) {
    if (!ready()) return;
    volatile SaM7 *m = M7HDR;
    u32 gen = m->gen, boot = m->boot;
    int an = ANALOG_ON && (a->pub != a->serial || a->pub_m7gen != gen || a->pub_boot != boot);
#if DRUMS_BUILT
    volatile Dk4 *d = dk4();
    int dk = DRUMS_ON && (d->pub != d->serial || d->pub_m7gen != gen || d->pub_boot != boot);
#else
    int dk = 0;
#endif
    if (!an && !dk) return;
    u32 p = lock();
    volatile SaM4 *h = M4HDR;
    if (h->magic != SA_M4_MAGIC || h->bootgen != boot) {
        h->magic = 0; dsb();
        h->serial = 2u; h->drum_serial = 0; h->r5 = h->r6 = h->r7 = 0;
    }
    if (an) {
        for (u32 i = 0; i < AN4_N; i++) {
            u32 w = a->e[i].word, f = flags_of(w) & AN_ON ? flags_of(w) & (AN_ON | AN_SYNC) : 0u;
            put_line(&TAB[i], a->e[i].handle, f, glide_of(w));
        }
        h->serial += 2u;
        a->pub = a->serial; a->pub_m7gen = gen; a->pub_boot = boot;
    }
#if DRUMS_BUILT
    if (dk) {
        for (u32 i = 0; i < DK4_N; i++) put_knobs(&KTAB[i], d->e[i].handle, d->e[i].word, d->e[i].v0, d->e[i].v1);
        h->drum_serial += 2u;
        d->pub = d->serial; d->pub_m7gen = gen; d->pub_boot = boot;
    }
#endif
    h->m7gen = gen; h->bootgen = boot; dsb();
    h->magic = SA_M4_MAGIC; dsb();
    unlock(p);
}

/* ---- registry keys: a reviewed allow-list, all or nothing --------------------------------------
 * Both keys must be empty after stock's builder (test_keys runs it), carry no immediate in either core, fall only
 * in stock's key-range tests as their default case (test_keys), and be below claim()'s (m4_step.c) lowest, so the
 * order of the two claims does not matter. If either is taken, nothing is written, the Analog keys stay off and the
 * black box says so. The registry is rebuilt every boot: our descriptors are checked, not a flag remembered. */
static const char SYNC_TITLE[] = "Sync", GLIDE_TITLE[] = "Glide", NO_TOKEN[] = "";
static const char *const SYNC_LABELS[2] = {"Off", "On"};
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
static int synth_keys(void) {
#if DRUMS_BUILT
    if (DRUMS_ON) drum_keys();
#endif
    if (!ANALOG_ON) return 0;
    volatile An4 *a = an4();
    u32 r = REGISTRY;
    if (!r) return 0;
    volatile u8 *sync = (volatile u8 *)(r + SYNC_KEY * 32u), *glide = (volatile u8 *)(r + GLIDE_KEY * 32u);
    if (H(sync, 0) == SYNC_KEY && W(sync, 4) == (u32)SYNC_TITLE && H(glide, 0) == GLIDE_KEY && W(glide, 4) == (u32)GLIDE_TITLE)
        return 1;
    if (H(sync, 0) || H(glide, 0)) { a->keys = 0x100u | (H(sync, 0) ? 1u : 0u) | (H(glide, 0) ? 2u : 0u); return 0; }
    for (u32 j = 0; j < 8; j++) { W(sync, j * 4u) = 0; W(glide, j * 4u) = 0; }
    B(sync, 2) = 7; W(sync, 4) = (u32)SYNC_TITLE; W(sync, 8) = (u32)SYNC_LABELS; W(sync, 12) = 2;   /* a list, as claim() */
    B(glide, 2) = 10; W(glide, 4) = (u32)GLIDE_TITLE; W(glide, 16) = 0; W(glide, 20) = AN_GLIDE_MAX;   /* numeric, as Pulse Width */
    W(glide, 24) = (u32)NO_TOKEN;
    H(sync, 0) = (u16)SYNC_KEY; H(glide, 0) = (u16)GLIDE_KEY;
    a->keys = 1;
    return 1;
}

/* ---- the selected track ------------------------------------------------------------------------ */
static u32 sel_track(void) { return FN(S_GET_PROPERTY, u32 (*)(void *, u32))((u8 *)MODEL + 4, S_KEY_SEL_TRACK); }
static u32 track_handle(u32 track) {
    u32 h = 0;
    return FN(S_FIND_TRACK, u32 (*)(void *, u32, u32 *))(MODEL, track, &h) ? h : 0u;
}
static u32 handle_type(u32 h) {
    void *obj = 0;
    if (!h || !FN(S_RESOLVE_OBJECT, u32 (*)(void *, u32, void **))((void *)W(MODEL, 0), h, &obj) || !obj) return 0xFFu;
    return ((u32 (*)(void *))W((void *)W(obj, 0), 0))(obj);
}
static u32 current(void) { return track_handle(sel_track()); }
static int analog(u32 h) { return lookup(h, 0) != 0; }

/* ---- A5: the instrument menu -------------------------------------------------------------------
 * Our action is stock's Wavetable action (0x08150BD6: {0x42, 0, 6, track}, then {0x16} up the view tree) plus the
 * flag. The swap is done when UI_POST returns (rc23 handles: UI_POST calls each parent's slot 0x30; the root view's,
 * 0x081702F4, calls the model command handler 0x08141EA4 directly, whose case 0x42 is the instrument replace
 * 0x0813F730: STORE_REMOVE (H3 drops the old entry), then STORE_CREATE (a new handle, H1)). So the flag goes on the
 * track's handle right then; if that is not a Wavetable, nothing is marked and menu_miss counts it (a device item).
 * BE4.22 (Jeff's decision 7): a new object stock made is a new instrument and starts as a plain Analog (Sync Off,
 * Glide 0); only if the track kept its object (the same handle after UI_POST) do its Sync and Glide stay (review N7).
 * menu_same counts that case: it is also what a deferred swap would look like (device evidence, review rc23 I3). */
__attribute__((used, retain)) u32 an_menu_event(void *view, void *ev) {
    u32 code = H(ev, 0);
    volatile An4 *a = an4();
    if (code == S_ACT_ANALOG) {
        u32 track = sel_track(), h0 = track_handle(track), g0 = 0, f0 = lookup(h0, &g0);
        u32 set[5] = {0x42u, 0, 6u, track, 0}, nav[5] = {0x16u, 0, 0, 0, 0};
        FN(S_UI_POST, void (*)(void *, void *))(view, set);
        u32 h = track_handle(track);
        if (h != h0) f0 = g0 = 0;                                 /* a new instrument: nothing carried */
        else a->menu_same++;
        if (handle_type(h) == S_TYPE_WAVETABLE) { mark(h, AN_ON | (f0 & AN_SYNC), f0 ? g0 : 0u); a->menus++; publish(a); }
        else a->menu_miss++;
        FN(S_UI_POST, void (*)(void *, void *))(view, nav);
        FN(S_PROJECT_TOUCH, void (*)(void *))(MODEL);
        return 0;
    }
    if (code >= S_ACT_INST_FIRST && code <= S_ACT_WAVETABLE) {   /* stock replaces the instrument: not Analog any more */
        mark(current(), 0, 0);                                    /* (also if stock keeps the object: Wavetable again) */
        publish(a);
    }
    return FN(S_MENU_HANDLER, u32 (*)(void *, void *))(view, ev);
}

/* ---- A6: the Wavetable page's slot keys -------------------------------------------------------- */
__attribute__((used, retain)) void *an_page_keys(void *dst, const void *src, u32 n) {
    void *r = FN(S_M4_MEMCPY, void *(*)(void *, const void *, u32))(dst, src, n);
    if (n >= 24u && analog(current()) && synth_keys()) {
        volatile u16 *k = (volatile u16 *)dst;
        if (!k[3]) k[3] = (u16)GLIDE_KEY;                     /* WT1's 4th slot */
        if (!k[11]) k[11] = (u16)SYNC_KEY;                    /* WT2's 4th slot */
    }
    return r;
}

/* ---- X3: value reads of our keys (the wrapper only calls this for them) ------------------------
 * For the selected track: stock asks for these two only while the synth page shows it. */
__attribute__((used, retain)) u32 an_context(void *model, void *ctx, u32 key, u32 *out) {
    (void)model; (void)ctx;
    u32 glide = 0, f = lookup(current(), &glide);
    *out = !f ? 0u : key == SYNC_KEY ? ((f & AN_SYNC) ? 1u : 0u) : glide;
    return 1;
}

/* ---- A9: encoder edits on the synth page ------------------------------------------------------- */
__attribute__((used, retain)) u32 an_view_event(void *view, void *ev) {
    u32 key = H(ev, 8);
    if (H(ev, 0) == S_EV_ENCODER && (key == SYNC_KEY || key == GLIDE_KEY)) {
        u32 h = current(), glide = 0, f = lookup(h, &glide);
        if (f) {
            int v = (int)W(ev, 12);
            u32 flags = f;
            if (key == SYNC_KEY) flags = v ? (flags | AN_SYNC) : (flags & ~AN_SYNC);
            else glide = (u32)(v < 0 ? 0 : v > (int)AN_GLIDE_MAX ? (int)AN_GLIDE_MAX : v);   /* stored as it comes */
            u32 old_g = 0, old_f = lookup(h, &old_g);
            if (flags != old_f || glide != old_g) {
                volatile An4 *a = an4();
                mark(h, flags, glide); a->edits++; publish(a);
            }
            FN(S_PROJECT_TOUCH, void (*)(void *))(MODEL);
        }
        return 0;                                             /* never forwarded to the model */
    }
    return FN(S_SYNTH_VIEW_EVENT, u32 (*)(void *, void *))(view, ev);
}

/* ---- A7: pane texts ---------------------------------------------------------------------------- */
__attribute__((used, retain)) void an_pane_text(void *slot, const char *text) {
    if (analog(current())) text = (u32)text == S_STR_NO_WAV ? "Analog" : "Saw > Square > Pulse";
    FN(S_SLOT_TITLE, void (*)(void *, const char *))(slot, text);
}

/* ---- the CPU indicator (D3) ---------------------------------------------------------------------
 * The M7's LOAD counters (D0L: blocks seen, permille sum, blocks over 100 %; d0_publish cleans them about every
 * 0.68 s, a save restarts them), differenced every 500 ms in an_tick. It shows the load, it limits nothing. */
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
typedef struct { char *buf; u32 *len; u32 max; } Out;
static void puts_(Out *o, const char *s) { while (*s && *o->len < o->max) o->buf[(*o->len)++] = *s++; }
static void dec(Out *o, u32 v) {
    char t[11]; u32 n = 0;
    do { t[n++] = (char)('0' + v % 10u); v /= 10u; } while (v && n < 10u);
    char r[11]; for (u32 i = 0; i < n; i++) r[i] = t[n - 1u - i];
    r[n] = 0; puts_(o, r);
}
/* A10: the synth view's tick (view, flags): WT1's "No WAV" label (view + S_VIEW_NOWAV_LABEL, the widget stock's page
 * builder titles, 0x0815FF5E) reads "Analog CPU 63%", "!" after a block over budget in the last 2 s. SLOT_TITLE
 * copies the text and marks the label only when it changed. Then stock's tick. */
__attribute__((used, retain)) void an_view_tick(void *view, void *flags) {
    volatile An4 *a = an4();
    u32 now = TICK_MS;
    if (view && now - a->label_t >= 250u && (a->label_t = now, analog(current()))) {   /* 4 times a second at most */
        char text[32]; u32 len = 0;
        Out o = {text, &len, sizeof(text) - 1u};
        if (!ready()) puts_(&o, "Analog (DSP off)");
        else {
            puts_(&o, "Analog CPU "); dec(&o, (a->cpu_pm + 5u) / 10u); puts_(&o, "%");
            if (a->over_t && now - a->over_t < 2000u) puts_(&o, " !");
        }
        text[len] = 0;
        FN(S_SLOT_TITLE, void (*)(void *, const char *))((u8 *)view + S_VIEW_NOWAV_LABEL, text);
    }
    FN(S_SYNTH_VIEW_TICK, void (*)(void *, void *))(view, flags);
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

/* ---- X1 / X2: project and preset XML ----------------------------------------------------------- */
static const char AT_ON[] = "bento_analog", AT_SYNC[] = "bento_analog_sync", AT_GLIDE[] = "bento_analog_glide";
static void attr(void *params, const char *name, u32 v) {
    void *a = FN(S_XML_ADD, void *(*)(void *, const char *))(params, name);
    if (a) FN(S_XML_SET_U32, void (*)(void *, u32))(a, v);
}
static u32 obj_type(void *object) { return ((u32 (*)(void *))W((void *)W(object, 0), 0))(object); }
#if DRUMS_BUILT
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
#endif
__attribute__((used, retain)) void an_save(void *object, void *params) {
    if (!object || !params) return;
#if DRUMS_BUILT
    if (DRUMS_ON) dr_save(object, params);
#endif
    if (!ANALOG_ON) return;
    u32 glide = 0, f = lookup(W(object, 0x10), &glide);       /* the object's handle (RESOLVE_OBJECT) */
    if (!f || obj_type(object) != S_TYPE_WAVETABLE) return;   /* Wavetables only */
    attr(params, AT_ON, 1u);
    attr(params, AT_SYNC, (f & AN_SYNC) ? 1u : 0u);
    attr(params, AT_GLIDE, glide);
    bump(&an4()->saves);
}
static u32 get(void *params, const char *name, u32 dflt) {
    u32 v = 0;
    void *a = FN(S_XML_FIND, void *(*)(void *, const char *))(params, name);
    return a && !FN(S_XML_GET_U32, int (*)(void *, u32 *))(a, &v) ? v : dflt;
}
#if DRUMS_BUILT
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
#endif
/* Every instrument stock reads (project or preset), at any time: AN4 needs no M7, so nothing is lost before the M7
 * has taken the synth area; an_tick publishes it then. */
__attribute__((used, retain)) void an_load(void *node, void *object) {
    if (!node || !object) return;
    u32 h = W(object, 0x10);
    void *params = FN(S_XML_CHILD, void *(*)(void *, const char *))(node, "params");
#if DRUMS_BUILT
    if (DRUMS_ON) dr_load(params, object);
#endif
    if (!ANALOG_ON) return;
    if (!params || get(params, AT_ON, 0) != 1u) { if (analog(h)) mark(h, 0, 0); return; }
    u32 sync = get(params, AT_SYNC, 0), glide = get(params, AT_GLIDE, 0);
    mark(h, AN_ON | (sync ? AN_SYNC : 0u), glide);
    bump(&an4()->loads);
}

/* ---- X6: the UI tick and the black box line ----------------------------------------------------
 * A new boot (the config line's generation moved) restarts the per-boot counters; the handle counters (carried,
 * dropped, forgot) run from the table's cold start, so H1's work at boot, before this first tick, shows. Entries
 * from an earlier boot are H1's (an_forget, here too: they would only take slots). */
#if DRUMS_BUILT
#define DSTATS ((volatile SaDrumStats *)SA(SA_DSTATS))
/* The drums' share of the blocks they rendered in over the last 500 ms (the M7's stats: cycles >> 4 summed, blocks). */
static void drum_cpu(volatile Dk4 *d) {                     /* in step with cpu_update's 500 ms */
    u32 blocks = ready() ? DSTATS->blocks : 0u, sum = ready() ? DSTATS->sum16 : 0u;
    if (!d->d_ok || blocks < d->d_blocks || sum < d->d_sum) { d->d_blocks = blocks; d->d_sum = sum; d->d_ok = 1; d->drum_pm = 0; return; }
    d->drum_pm = blocks == d->d_blocks ? 0u : ((sum - d->d_sum) / (blocks - d->d_blocks)) * 16u / 1280u;
    d->d_blocks = blocks; d->d_sum = sum;
}
#endif
__attribute__((used, retain)) void an_tick(void) {
    if (!ANALOG_ON && !DRUMS_ON) return;
    volatile An4 *a = an4();
    u32 now = TICK_MS, g = boot_gen();
    if (g && g != a->gen) {                         /* (S6c-2: also with Analog off: the CPU readout's state is here) */
        u32 p = lock();
        a->gen = g; a->keys = 0;
        a->menus = a->edits = a->saves = a->loads = a->full = a->evicted = a->menu_miss = a->menu_same = a->over = a->cpu_ok = 0;
#if DRUMS_BUILT
        volatile Dk4 *d = dk4();
        d->gen = g; d->keys = 0; d->d_ok = 0;
        d->pages = d->edits = d->saves = d->loads = d->full = d->evicted = d->refused = d->heals = 0;
        d->view = 0;                                        /* the page cache names nothing of this boot */
#endif
        unlock(p);
    }
    an_forget();                                    /* (both tables) */
    synth_keys();
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
#if !defined(BE_OMIT_DRUMS) && !defined(BE_NO_DRUMS)
/* S6c: the DRUMS line, from the M7's drum stats lines (cleaned after each block with a drum voice): the hook's average
 * and peak share of the 1,280,000-cycle block, the share histogram, and what the voices did. S6c-2: then the page's
 * counters (DK4): keys, Kick pages shown, knob edits, XML saves / loads, pads with knobs, capacity, and the handle hooks
 * (carried: pads of a cloned track, dups: a pad copied by stock's child copy, dropped, forgot, clone_skips). */
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
__attribute__((used, retain)) void an_print(char *buf, u32 *len, u32 max) {
    Out o = {buf, len, max};
#if !defined(BE_OMIT_DRUMS) && !defined(BE_NO_DRUMS)
    drums_line(&o);
#endif
    if (!ANALOG_ON) return;
    volatile An4 *a = an4();
    volatile SaM7 *m = M7HDR;
    u32 take = SYNTH_TAKE, n = 0;
    if ((take >> 24) != SYNTH_TAKE_TAG) take = 0;              /* never written since the slice was new */
    for (u32 i = 0; i < AN4_N; i++) n += (flags_of(a->e[i].word) & AN_ON) ? 1u : 0u;
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
    if (a->keys > 1u) field(&o, ":", a->keys & 3u);
    field(&o, " tracks=", n);
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
        field(&o, " misses=", s->misses);
        field(&o, " | handle seen=", m->handle_seen); field(&o, " ok=", m->handle_ok);
    }
    field(&o, " | load_pm=", a->cpu_pm); field(&o, " over_with_analog=", a->over);
    field(&o, " | ui menus=", a->menus); field(&o, " edits=", a->edits); field(&o, " saves=", a->saves);
    field(&o, " loads=", a->loads); field(&o, " full=", a->full); field(&o, " evicted=", a->evicted);
    field(&o, " menu_miss=", a->menu_miss); field(&o, " menu_same=", a->menu_same);
    field(&o, " | handles ctr=", HANDLE_CTR); field(&o, " carried=", a->carried); field(&o, " dropped=", a->dropped);
    field(&o, " forgot=", a->forgot);
    puts_(&o, "\r\n");
}
