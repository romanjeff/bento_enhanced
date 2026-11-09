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

/* ---- H1 / H2 / H3: stock's object lifecycle (any task: the storage task's loads and saves, the UI task's menu
 * and clipboard). AN4 is read without the lock first: the common case (nothing to do) costs a 16-entry scan. */
/* H1: OBJ_CTOR's first act (build.py's wrapper keeps every register): the counter is the handle the object is about
 * to get; every entry at or above it is from an earlier boot. Also runs for stock's static constructors at M4 boot:
 * before the table exists (no magic) it only reads. */
__attribute__((used, retain)) void an_forget(void) {
    volatile An4 *a = AN4;
    if (a->magic != AN4_MAGIC) return;
    u32 ctr = HANDLE_CTR, any = 0;
    if (!ctr) return;                                          /* (never on the device: .data is copied first) */
    for (u32 k = 0; k < AN4_N; k++) any |= a->e[k].handle >= ctr;
    if (!any) return;
    u32 p = lock(), n = 0;
    ctr = HANDLE_CTR;
    for (u32 k = 0; k < AN4_N; k++)
        if (a->e[k].handle >= ctr) { a->e[k].word = 0; a->e[k].handle = 0; n++; }
    if (n) { a->serial++; a->forgot += n; }
    unlock(p);
}
/* H3: NODE_DESTROY (obj, pool): every top-level object's way out (store clear, remove, a failed create). */
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
    return FN(S_NODE_DESTROY, u32 (*)(void *, void *, u32, u32))(obj, pool, r2, r3);
}
/* H2: NODE_CLONE (src, pool, &out) -> ok: the copy has a new handle (OBJ_CTOR) and gets src's Analog state. */
__attribute__((used, retain)) u32 an_clone(void *src, void *pool, void **out) {
    u32 ok = FN(S_NODE_CLONE, u32 (*)(void *, void *, void **))(src, pool, out);
    if (ok && src && out && *out && *out != src) {
        u32 glide = 0, f = lookup(W(src, 0x10), &glide);
        if (f) { mark(W(*out, 0x10), f, glide); bump(&an4()->carried); }
    }
    return ok;
}

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
static void publish(volatile An4 *a) {
    if (!ready()) return;
    volatile SaM7 *m = M7HDR;
    u32 gen = m->gen, boot = m->boot;
    if (a->pub == a->serial && a->pub_m7gen == gen && a->pub_boot == boot) return;
    u32 p = lock();
    volatile SaM4 *h = M4HDR;
    if (h->magic != SA_M4_MAGIC || h->bootgen != boot) {
        h->magic = 0; dsb();
        h->serial = 2u; h->drum_serial = 0; h->r5 = h->r6 = h->r7 = 0;
    }
    for (u32 i = 0; i < AN4_N; i++) {
        u32 w = a->e[i].word, f = flags_of(w) & AN_ON ? flags_of(w) & (AN_ON | AN_SYNC) : 0u;
        put_line(&TAB[i], a->e[i].handle, f, glide_of(w));
    }
    h->m7gen = gen; h->bootgen = boot; h->serial += 2u; dsb();
    h->magic = SA_M4_MAGIC; dsb();
    a->pub = a->serial; a->pub_m7gen = gen; a->pub_boot = boot;
    unlock(p);
}

/* ---- registry keys: a reviewed allow-list, all or nothing --------------------------------------
 * Both keys must be empty after stock's builder (test_keys runs it), carry no immediate in either core, fall only
 * in stock's key-range tests as their default case (test_keys), and be below claim()'s (m4_step.c) lowest, so the
 * order of the two claims does not matter. If either is taken, nothing is written, the Analog keys stay off and the
 * black box says so. The registry is rebuilt every boot: our descriptors are checked, not a flag remembered. */
static const char SYNC_TITLE[] = "Sync", GLIDE_TITLE[] = "Glide", NO_TOKEN[] = "";
static const char *const SYNC_LABELS[2] = {"Off", "On"};
static int synth_keys(void) {
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

/* ---- X1 / X2: project and preset XML ----------------------------------------------------------- */
static const char AT_ON[] = "bento_analog", AT_SYNC[] = "bento_analog_sync", AT_GLIDE[] = "bento_analog_glide";
static void attr(void *params, const char *name, u32 v) {
    void *a = FN(S_XML_ADD, void *(*)(void *, const char *))(params, name);
    if (a) FN(S_XML_SET_U32, void (*)(void *, u32))(a, v);
}
__attribute__((used, retain)) void an_save(void *object, void *params) {
    if (!object || !params) return;
    u32 glide = 0, f = lookup(W(object, 0x10), &glide);       /* the object's handle (RESOLVE_OBJECT) */
    if (!f || ((u32 (*)(void *))W((void *)W(object, 0), 0))(object) != S_TYPE_WAVETABLE) return;   /* Wavetables only */
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
/* Every instrument stock reads (project or preset), at any time: AN4 needs no M7, so nothing is lost before the M7
 * has taken the synth area; an_tick publishes it then. */
__attribute__((used, retain)) void an_load(void *node, void *object) {
    if (!node || !object) return;
    u32 h = W(object, 0x10);
    void *params = FN(S_XML_CHILD, void *(*)(void *, const char *))(node, "params");
    if (!params || get(params, AT_ON, 0) != 1u) { if (analog(h)) mark(h, 0, 0); return; }
    u32 sync = get(params, AT_SYNC, 0), glide = get(params, AT_GLIDE, 0);
    mark(h, AN_ON | (sync ? AN_SYNC : 0u), glide);
    bump(&an4()->loads);
}

/* ---- X6: the UI tick and the black box line ----------------------------------------------------
 * A new boot (the config line's generation moved) restarts the per-boot counters; the handle counters (carried,
 * dropped, forgot) run from the table's cold start, so H1's work at boot, before this first tick, shows. Entries
 * from an earlier boot are H1's (an_forget, here too: they would only take slots). */
__attribute__((used, retain)) void an_tick(void) {
    if (!ANALOG_ON) return;                         /* S6c: a BE_DISABLE=analog bisect (drums on): no Analog keys or table */
    volatile An4 *a = an4();
    u32 now = TICK_MS, g = boot_gen();
    if (g && g != a->gen) {
        u32 p = lock();
        a->gen = g; a->keys = 0;
        a->menus = a->edits = a->saves = a->loads = a->full = a->evicted = a->menu_miss = a->menu_same = a->over = a->cpu_ok = 0;
        unlock(p);
    }
    an_forget();
    synth_keys();
    cpu_update(a, now);
    publish(a);
}

static void field(Out *o, const char *name, u32 v) { puts_(o, name); dec(o, v); }
#if !defined(BE_OMIT_DRUMS) && !defined(BE_NO_DRUMS)
/* S6c: the DRUMS line, from the M7's drum stats lines (cleaned after each block with a drum voice): the hook's average
 * and peak share of the 1,280,000-cycle block, the share histogram, and what the voices did. */
#define DSTATS ((volatile SaDrumStats *)SA(SA_DSTATS))
static void drums_line(Out *o) {
    puts_(o, "DRUMS");
    if (!ready()) { puts_(o, " area not taken\r\n"); return; }
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
    field(o, " unbound=", s->unbound); field(o, " short_bufs=", s->short_bufs);
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
