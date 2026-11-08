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
 * An entry: handle (0 = free) and a word: [3:0] flags (AN_ON, AN_SYNC, AN4_FAIL), [13:4] glide 0..1000, [31:16]
 * seconds (TICK_MS / 1000) when it was marked, or when this boot first saw it. */
#define AN4_MAGIC 0x31344E41u                /* "AN41" */
#define AN4_N SA_ANTAB_N
#define AN4_FAIL 8u                          /* the sweep found the handle not a Wavetable once */
#define AN4_RETIRE_S 30u                     /* never retire an entry younger than this (a project load in progress) */
#define PEND_MS 10000u                       /* the Analog menu action waits this long for stock's swap */
typedef struct {
    u32 magic, gen;                          /* gen: the config line's boot generation this table last saw */
    u32 serial, pub, pub_m7gen, pub_boot;    /* serial: +1 per change; pub*: what is published in the synth area */
    u32 keys, sweep_i, sweep_t;              /* keys: synth_keys() 1 ok, 0x100 | taken bits, 0 not yet */
    u32 pend_track, pend_handle, pend_t;     /* an Analog menu action waiting for stock's instrument swap */
    u32 cpu_t, seen, sum, h3;                /* CPU indicator: last update, the LOAD counters (D0L) then */
    u32 over_t, cpu_pm, over, stats_blocks;  /* last block over budget, average LOAD, over-budget blocks with Analog */
    u32 menus, edits, saves, loads, full, retired, label_t, cpu_ok, r28, r29, r30, r31;
    struct { u32 handle, word; } e[AN4_N];
} An4;
_Static_assert(sizeof(An4) == AN4_SLICE_END - AN4_SLICE, "AN4 fills its 256 B");
#define AN4 ((volatile An4 *)SLICE(AN4_SLICE))
static inline u32 flags_of(u32 w) { return w & 15u; }
static inline u32 glide_of(u32 w) { return (w >> 4) & 0x3FFu; }
static inline u32 word_of(u32 flags, u32 glide, u32 s) {
    return (flags & 15u) | ((glide > AN_GLIDE_MAX ? AN_GLIDE_MAX : glide) << 4) | (s << 16);
}

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }
static void dsb(void) { __asm volatile("dsb" ::: "memory"); }
static u32 lock(void) { u32 p; __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory"); return p; }
static void unlock(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }
static u32 secs(void) { return (TICK_MS / 1000u) & 0xFFFFu; }

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
/* flags 0: not Analog any more. keep: an entry already there keeps its Sync and Glide (picking Analog again). */
static void mark(u32 h, u32 flags, u32 glide, int keep) {
    if (!h) return;
    volatile An4 *a = an4();
    u32 now = secs(), p = lock();
    int i = find_i(a, h);
    if (i < 0 && flags) {
        for (u32 k = 0; i < 0 && k < AN4_N; k++)
            if (!(flags_of(a->e[k].word) & AN_ON)) i = (int)k;
        int best = -1;
        for (u32 k = 0, oldest = 0; i < 0 && k < AN4_N; k++) {   /* full: the oldest the sweep found dead */
            u32 w = a->e[k].word, age = (now - (w >> 16)) & 0xFFFFu;
            if ((flags_of(w) & AN4_FAIL) && (best < 0 || age > oldest)) { oldest = age; best = (int)k; }
        }
        if (i < 0) i = best;
        if (i < 0) a->full++;
    }
    if (i >= 0) {
        u32 w = a->e[i].word;
        if (keep && a->e[i].handle == h && (flags_of(w) & AN_ON)) { flags |= flags_of(w) & AN_SYNC; glide = glide_of(w); }
        if (flags) { a->e[i].handle = h; a->e[i].word = word_of(flags | AN_ON, glide, now); }
        else { a->e[i].word = 0; a->e[i].handle = 0; }
        a->serial++;
    }
    unlock(p);
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
        h->serial = 2u; h->r4 = h->r5 = h->r6 = h->r7 = 0;
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
 * flag. Whether stock has swapped the track's instrument when UI_POST returns is not proven (review S5): the flag
 * goes on the track's handle right away if it is a Wavetable then, and an_tick watches the track for a new
 * Wavetable handle for PEND_MS (any other instrument action cancels the wait). */
__attribute__((used, retain)) u32 an_menu_event(void *view, void *ev) {
    u32 code = H(ev, 0);
    volatile An4 *a = an4();
    if (code == S_ACT_ANALOG) {
        u32 track = sel_track();
        u32 set[5] = {0x42u, 0, 6u, track, 0}, nav[5] = {0x16u, 0, 0, 0, 0};
        FN(S_UI_POST, void (*)(void *, void *))(view, set);
        u32 h = track_handle(track);
        if (handle_type(h) == S_TYPE_WAVETABLE) { mark(h, AN_ON, 0, 1); a->menus++; publish(a); }
        a->pend_track = track; a->pend_handle = h; a->pend_t = TICK_MS | 1u;
        FN(S_UI_POST, void (*)(void *, void *))(view, nav);
        FN(S_PROJECT_TOUCH, void (*)(void *))(MODEL);
        return 0;
    }
    if (code >= S_ACT_INST_FIRST && code <= S_ACT_WAVETABLE) {   /* stock replaces the instrument: not Analog any more */
        a->pend_t = 0;
        mark(current(), 0, 0, 0);
        publish(a);
    }
    return FN(S_MENU_HANDLER, u32 (*)(void *, void *))(view, ev);
}
static void pending(volatile An4 *a, u32 now) {
    if (!a->pend_t) return;
    if (now - a->pend_t >= PEND_MS) { a->pend_t = 0; return; }
    u32 h = track_handle(a->pend_track);
    if (h && h != a->pend_handle && handle_type(h) == S_TYPE_WAVETABLE) {
        mark(h, AN_ON, 0, 1); a->menus++; a->pend_t = 0;
    }
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
                mark(h, flags, glide, 0); a->edits++; publish(a);
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
    if (!f) return;
    attr(params, AT_ON, 1u);
    attr(params, AT_SYNC, (f & AN_SYNC) ? 1u : 0u);
    attr(params, AT_GLIDE, glide);
    an4()->saves++;
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
    if (!params || get(params, AT_ON, 0) != 1u) { if (analog(h)) mark(h, 0, 0, 0); return; }
    u32 sync = get(params, AT_SYNC, 0), glide = get(params, AT_GLIDE, 0);
    mark(h, AN_ON | (sync ? AN_SYNC : 0u), glide, 0);
    an4()->loads++;
}

/* ---- X6: the UI tick and the black box line ----------------------------------------------------
 * A new boot (the config line's generation moved): every entry kept from the last boot gets this boot's time, so it
 * is retired only once the sweep has found its handle dead twice and AN4_RETIRE_S has passed (a project the boot
 * loads rewrites its own entries anyway). The sweep checks one entry every 250 ms. */
static void sweep(volatile An4 *a, u32 now) {
    if (now - a->sweep_t < 250u) return;
    a->sweep_t = now;
    u32 i = a->sweep_i++ % AN4_N, h = a->e[i].handle;
    if (!h || !(flags_of(a->e[i].word) & AN_ON)) return;
    int alive = handle_type(h) == S_TYPE_WAVETABLE;
    u32 s = secs(), p = lock();
    u32 w = a->e[i].word;
    if (a->e[i].handle == h && (flags_of(w) & AN_ON)) {
        if (alive) a->e[i].word = w & ~AN4_FAIL;
        else if ((flags_of(w) & AN4_FAIL) && ((s - (w >> 16)) & 0xFFFFu) >= AN4_RETIRE_S) {
            a->e[i].word = 0; a->e[i].handle = 0; a->serial++; a->retired++;
        } else a->e[i].word = w | AN4_FAIL;
    }
    unlock(p);
}
__attribute__((used, retain)) void an_tick(void) {
    volatile An4 *a = an4();
    u32 now = TICK_MS, g = boot_gen();
    if (g && g != a->gen) {
        u32 s = secs(), p = lock();
        for (u32 i = 0; i < AN4_N; i++) a->e[i].word = (a->e[i].word & 0xFFFFu & ~AN4_FAIL) | (s << 16);
        a->gen = g; a->keys = 0; a->pend_t = 0; a->sweep_t = now;
        a->menus = a->edits = a->saves = a->loads = a->full = a->retired = a->over = a->cpu_ok = 0;
        unlock(p);
    }
    synth_keys();
    pending(a, now);
    sweep(a, now);
    cpu_update(a, now);
    publish(a);
}

static void field(Out *o, const char *name, u32 v) { puts_(o, name); dec(o, v); }
__attribute__((used, retain)) void an_print(char *buf, u32 *len, u32 max) {
    Out o = {buf, len, max};
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
    field(&o, " loads=", a->loads); field(&o, " full=", a->full); field(&o, " retired=", a->retired);
    puts_(&o, "\r\n");
}
