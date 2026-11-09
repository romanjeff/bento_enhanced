/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Drum synths on one-shot pads, M7 side (S6c: the Kick; docs/plan-synths-168.md sections 8, 9 and "S6c").
 *
 * A pad plays a drum synth when its sample is one of our carrier WAVs (tools/carriers/gen_carriers.py, shipped in
 * the kit's "Drum Synths BE" folder): stock loads it as an ordinary one-shot sample, and this hook renders the synth
 * in its place. Everything else about the pad stays stock's, because the synth only replaces the sample read.
 *
 * One site, D1: the per-voice one-shot render entry (root, voice, ..., temp, out). dr_render() returns 0 for stock's
 * render, or 1 after rendering the voice into its own scratch buffer, writing its sample cursor and calling stock's
 * post chain (filter, amp envelope, level / pan, sends) exactly as stock's render ends.
 *
 * Starts are seen here, not in the voice starts (review B1, rc25): both of stock's voice starts clear the voice's
 * sample descriptor, look the pad's sample up, open it and only then write the voice's start serial (S_VOICE_SERIAL,
 * from the engine's start counter). So the first render of a start is the first moment the descriptor names this
 * start's sample. Each native voice has its own cell for good (the engine's pool has exactly 32 voices, DRUM_CELLS =
 * 32); a render whose serial differs from its cell's is a new start: the carrier test runs once, and its answer holds
 * until the next start of that voice. A mono pad's legato re-pitch keeps the serial (VOICE_START returns before the
 * descriptor is touched), so the synth keeps ringing at the new pitch, as a sample would.
 *
 * After bento-playground's K7 (eu4_work/drums/kick.cpp kick_voice / kick_render / kick_marker at 9fc7787,
 * GPL-3.0-or-later), rewritten for our layout, with the plan's changes (section 12):
 *   - state per native voice, not per pad (upstream's per-pad slots made Poly pads mono with a hard cut);
 *   - no velocity multiply (velocity reaches the sound through the pad's routes in the post chain, D7);
 *   - a voice ends by its cursor: min(elapsed, frames - 1) while it rings; when the synth is done, frames (forward)
 *     or 0 (a reverse pad), and stock's own render then starts the release and its end check ends the voice; never by
 *     writing the voice's active byte (upstream's, which clicks);
 *   - noise from a xorshift32 per cell (rand() below), not one global state;
 *   - the carrier is recognised from the voice's descriptor (frames, mono, 48 kHz, the signature and our word in its
 *     first 64 samples, through stock's own sample reader: no card access), cached per pad;
 *   - the gate (D8): only roots of stock's one-shot pad class (S_DRUM_ROOT_VT). On 1.6.8 the M7 makes these roots only
 *     for the M4's property 0xAC, which the M4 sends only for One-Shot tracks' pads (facts.py DRUM_ROOT_VT).
 * The knob values come from the M4's table, published in the synth area (synth_shared.h SA_DKTAB), once the M4 has
 * published (its drum serial is not 0): the pad's handle is its root's +0x0C. A pad with no entry plays the engine's
 * defaults. Pitch is stock's: the same ratio formula as stock's render (voice +0x48 times 2^(pitch modulation + fine
 * tune)), and LFO1 is ticked as stock's render does.
 * Measurement: DWT cycles in the hook per audio block, into the drum stats lines (m4_synth.c prints them). */
#include "drums.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define F(p, o) (*(volatile float *)((u8 *)(p) + (o)))
#define B(p, o) (*(volatile u8 *)((u8 *)(p) + (o)))
#define FN(a, t) ((t)((a) | 1u))
#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define POOL_LEFT (*(volatile u32 *)S_POOL_LEFT_RAM)
#define POOL_CUR (*(volatile u32 *)S_POOL_CUR_RAM)
#define M7HDR ((volatile SaM7 *)SA(SA_M7))
#define M4HDR ((volatile SaM4 *)SA(SA_M4))
#define DSTATS ((volatile SaDrumStats *)SA(SA_DSTATS))
#define KTAB ((volatile SaKnobs *)SA(SA_DKTAB))
#define CELLS ((DrumCell *)SA(SA_DCELLS))
#define RUN DRUM_RUN
static_assert(sizeof(SaDrumStats) == 128 && sizeof(SaKnobs) == 32, "drum line layout");
static_assert(SA_DSTATS + sizeof(SaDrumStats) <= 0x200u, "drum stats inside the M7 stats lines");
static_assert(SA_DCELLS + DRUM_CELLS * DRUM_CELL_BYTES <= SA_DRUN && SA_DRUN + 512u <= 0x6000u, "drum layout");
static_assert(SA_DKTAB + SA_DKTAB_N * sizeof(SaKnobs) <= SA_DCELLS, "drum knob lines");
static_assert(DRUM_CELLS == S_DRUM_POOL_N, "one cell per voice of stock's one-shot voice pool");

#define QUIET 0.0000158f                  /* -96 dB */
#define QUIET_BLOCKS 32u                  /* in a row (85 ms at 128 frames: longer than a 20 Hz period) */
#define MIN_FRAMES 480u                   /* ... and never in the first 10 ms */
#define CAP_FRAMES (48000u * 12u)         /* the longest a drum voice rings: 12 s (the Kick's longest decay is ~7 s) */
#define MAX_FRAMES 1024u                  /* the largest block we render (stock's are 128) */

static const DrumEngine *const ENGINES[] = {&kick_engine};

static inline void dsb() { __asm volatile("dsb" ::: "memory"); }
static inline void inval(u32 a, u32 n) {
    dsb();
    for (u32 x = a & ~31u; x < a + n; x += 32u) *(volatile u32 *)SA_DCIMVAC = x;
    dsb();
}
static inline void clean(u32 a, u32 n) {
    dsb();
    for (u32 x = a & ~31u; x < a + n; x += 32u) *(volatile u32 *)SA_DCCMVAC = x;
    dsb();
}
/* The area is ours (as an_enter's area_ok: the M7 took the reservation this boot). */
static inline int area_ok() { return M7HDR->magic == SA_M7_MAGIC && POOL_CUR + POOL_LEFT == SYNTH_AREA; }
/* A pointer stock could hand us (as upstream's memory()): DTCM, AXI SRAM, D2 SRAM or its 0x1000_0000 alias (sample
 * providers live there), SDRAM. Only memory-map constants: no stock address. */
static inline int ram_ptr(u32 p) {
    return p - 0x10000000u < 0x48000u || p - 0x20000000u < 0x20000u || p - 0x24000000u < 0x80000u ||
           p - 0x30000000u < 0x48000u || p - 0xC0000000u < 0x8000000u;
}

/* rand() for the vendored DaisySP (shim/stdlib.h): xorshift32 on the current cell's state. */
extern "C" int rand(void) {
    u32 *s = (u32 *)RUN->rng_cell;
    if (!s) s = &RUN->rng_spare;
    u32 v = *s;
    v ^= v << 13; v ^= v >> 17; v ^= v << 5;
    *s = v;
    return (int)(v & 0x7FFFFFFFu);
}

static const DrumEngine *engine(u32 id) {
    for (u32 i = 0; i < sizeof(ENGINES) / sizeof(ENGINES[0]); i++)
        if (ENGINES[i]->id == id) return ENGINES[i];
    return 0;
}

/* ---- blocks and statistics ------------------------------------------------------------------------------------ */
static void finish(DrumRun *r) {
    if (r->voices) {
        volatile SaDrumStats *s = DSTATS;
        u32 c = r->cyc;
        s->blocks++;
        s->sum16 += c >> 4;
        if (s->sum16 >= 0x80000000u) { s->sum16 >>= 1; s->blocks >>= 1; }
        if (c > s->peak) s->peak = c;
        if (r->voices > s->cells_peak) s->cells_peak = r->voices;
        u32 pm = c / 1280u;                                   /* per mille of the 1,280,000-cycle block */
        if (pm >= 50u) s->hist[0]++;
        if (pm >= 150u) s->hist[1]++;
        if (pm >= 300u) s->hist[2]++;
        clean(SA(SA_DSTATS), sizeof(SaDrumStats));
    }
    r->cyc = r->voices = 0;
}
/* The M4's drum serial, or 0 while it has published no knob line for this generation of ours (its header is not for
 * this generation, or its serial is still 0): then every pad plays its defaults and the table is not read. */
static u32 m4_drum_serial() {
    inval(SA(SA_M4), 32u);
    volatile SaM4 *h = M4HDR;
    return h->magic == SA_M4_MAGIC && h->m7gen == M7HDR->gen ? h->drum_serial : 0u;
}
/* The run state for this take of the area (cells and run state start over when the M7 took it again), then the
 * block: a change of stock's LOAD ring index is a new audio block. */
static DrumRun *run() {
    DrumRun *r = RUN;
    u32 gen = M7HDR->gen;
    if (r->magic != DRUN_MAGIC || r->gen != gen) {
        for (u32 i = 0; i < DRUM_CELLS * DRUM_CELL_BYTES / 4u; i++) ((volatile u32 *)CELLS)[i] = 0;
        for (u32 i = 0; i < sizeof(DrumRun) / 4u; i++) ((volatile u32 *)r)[i] = 0;
        volatile SaDrumStats *s = DSTATS;
        for (u32 i = 0; i < sizeof(SaDrumStats) / 4u; i++) ((volatile u32 *)s)[i] = 0;
        clean(SA(SA_DSTATS), sizeof(SaDrumStats));
        r->gen = gen; r->magic = DRUN_MAGIC; r->rng_spare = 0x51B37A29u;
    }
    u32 idx = W(S_LOAD_RING, S_LOAD_RING_IDX);
    if (idx != r->idx) {
        finish(r);
        r->idx = idx;
        r->block++;
        r->knob_serial = m4_drum_serial();
    }
    return r;
}

/* ---- the knob table (the M4's, published): engine override and values for a pad ------------------------------- */
/* One pass over the published lines: the pad's engine override (0: none) and its raw values (0xFF: no entry). */
static u32 knob_entry(DrumRun *r, u32 handle, u8 *raw) {
    for (u32 j = 0; j < DRUM_KNOBS; j++) raw[j] = 0xFFu;
    if (!r->knob_serial || !handle) return 0;
    DSTATS->knob_reads++;
    inval(SA(SA_DKTAB), SA_DKTAB_N * sizeof(SaKnobs));
    for (u32 i = 0; i < SA_DKTAB_N; i++) {
        volatile SaKnobs *k = &KTAB[i];
        u32 head = k->head, h = k->handle, w = k->word, v0 = k->v0, v1 = k->v1, tail = k->tail;
        if ((head & 1u) || head != tail || h != handle || !(w & DK_USED)) continue;
        for (u32 j = 0; j < DRUM_KNOBS; j++) raw[j] = (u8)(((j < 4u ? v0 : v1) >> (8u * (j & 3u))) & 0xFFu);
        DSTATS->knob_hits++;                                  /* S6c-2: the M4's handle is this root's +0x0C */
        return w & 15u;
    }
    return 0;
}
/* The values an engine plays: its defaults where the pad has none, every value inside the knob's range. */
static void knob_values(const DrumEngine *e, const u8 *raw, u8 *values) {
    for (u32 j = 0; j < DRUM_KNOBS; j++) {
        u32 v = raw[j] == 0xFFu ? e->defaults[j] : raw[j];
        values[j] = (u8)(v < e->lo[j] ? e->lo[j] : v > e->hi[j] ? e->hi[j] : v);
    }
}

/* ---- the carrier detector -------------------------------------------------------------------------------------- */
/* The engine id a carrier of this length would have, else 0: frames 4800 + id (ours) or 144018 + id (Playground's),
 * id odd 1..15. Every ordinary sample stops here, with one frame-count read. */
static u32 carrier_id(u32 frames, u32 *ours) {
    u32 id = frames - CARRIER_FRAMES;
    *ours = 1;
    if (id <= 15u && (id & 1u)) return id;
    id = frames - (PG_CARRIER_FRAMES - 1u);
    *ours = 0;
    return id <= 15u && (id & 1u) ? id : 0u;
}
/* Engine id of a carrier of that length, else 0: mono, 48 kHz, read through stock's descriptor helpers (the sample is
 * in SDRAM): the signature, then (ours) CARRIER_WORD(id). */
static u32 test_carrier(void *desc, u32 id, u32 ours) {
    if (FN(S_DESC_STEREO, u32 (*)(void *))(desc)) return 0;
    if (FN(S_DESC_RATE, u32 (*)(void *))(desc) != 48000u) return 0;
    float s[32];
    auto read = FN(S_DESC_READ, void (*)(void *, float *, u32, u32, u32));
    for (u32 i = 0; i < 32; i++) s[i] = 0.f;
    read(desc, s, 32, 0, 0);
    float mag = s[0] < 0.f ? -s[0] : s[0];
    if (!(mag > 1e-8f && mag <= 1.000001f)) return 0;
    for (u32 i = 0; i < 32; i++)
        if (s[i] != ((CARRIER_SIGNATURE >> i) & 1u ? mag : -mag)) return 0;
    if (!ours) return id;
    for (u32 i = 0; i < 32; i++) s[i] = 0.f;
    read(desc, s, 32, 32, 0);
    u32 word = 0;
    for (u32 i = 0; i < 32; i++) {
        if (s[i] == mag) word |= 1u << i;
        else if (s[i] != -mag) return 0;
    }
    return word == CARRIER_WORD(id) ? id : 0;
}
/* The voice's sample (its open descriptor) is a carrier: its engine id, else 0. The same pad, provider, file and
 * length give the same answer without reading (the voices of one pad have descriptors of their own). */
static u32 carrier(DrumRun *r, void *root, void *desc, u32 *frames_out) {
    u32 provider = W(desc, 0), file = W(desc, 4);
    if (!ram_ptr(provider)) return 0;
    u32 frames = FN(S_DESC_FRAMES, u32 (*)(void *))(desc), ours;
    *frames_out = frames;
    u32 id = carrier_id(frames, &ours);
    if (!id) return 0;
    for (u32 i = 0; i < DRUM_CACHE_N; i++) {
        auto &c = r->cache[i];
        if (c.root == (u32)root && c.provider == provider && c.file == file && c.frames == frames) return c.id;
    }
    volatile SaDrumStats *s = DSTATS;
    s->detects++;
    id = test_carrier(desc, id, ours);
    if (id) s->carriers++;
    auto &c = r->cache[r->cache_next++ % DRUM_CACHE_N];
    c.root = (u32)root; c.provider = provider; c.file = file; c.frames = frames; c.id = id;
    return id;
}

/* ---- cells: one per native voice ------------------------------------------------------------------------------ */
/* The voice's cell, bound on first sight: open addressing from a hash of the pointer (the voices are 0x630 B apart).
 * 0 only if more than DRUM_CELLS voices ever rendered, which stock's 32-voice pool rules out (counted: unbound). */
static DrumCell *cell(DrumRun *r, void *voice) {
    u32 i = ((u32)voice * 0x9E3779B1u) >> 27;
    for (u32 k = 0; k < DRUM_CELLS; k++, i = (i + 1u) & (DRUM_CELLS - 1u)) {
        DrumCell *c = &CELLS[i];
        if (c->voice == (u32)voice) return c;
        if (!(r->bound & (1u << i))) {
            for (u32 j = 0; j < 16u; j++) ((volatile u32 *)c)[j] = 0;
            c->voice = (u32)voice; c->state = CELL_NEW;
            r->bound |= 1u << i;
            return c;
        }
    }
    DSTATS->unbound++;
    return 0;
}
static_assert((DRUM_CELLS & (DRUM_CELLS - 1u)) == 0u, "the cell hash wraps by mask");

/* A start not seen before (a new serial): is it a carrier whose engine is built? Then the cell starts its synth. */
static void start(DrumRun *r, DrumCell *c, void *root, void *voice, u32 serial) {
    u32 was = c->state;
    c->root = (u32)root; c->serial = serial; c->state = CELL_SAMPLE;
    void *desc = (void *)W(voice, S_VOICE_DESC);
    if (!ram_ptr((u32)desc)) return;
    u32 frames = 0, id = carrier(r, root, desc, &frames);
    if (!id) return;
    u32 handle = W(root, S_ROOT_HANDLE);
    u8 raw[DRUM_KNOBS];
    u32 override = knob_entry(r, handle, raw);
    const DrumEngine *e = engine(override ? override : id);   /* the pad's own choice wins over the carrier's id */
    if (!e) { DSTATS->unbuilt++; return; }                  /* not in this build: stock plays the carrier */
    c->handle = handle; c->engine = e->id; c->frames = frames; c->elapsed = 0;
    c->last_block = r->block; c->quiet = 0;
    c->rng = 0x51B37A29u ^ (++r->starts * 0x9E3779B9u);
    if (!c->rng) c->rng = 1u;
    knob_values(e, raw, c->values);
    c->knobs = r->knob_serial;
    c->state = CELL_PLAYING;
    /* a fresh engine object: DaisySP's Init leaves some members to the object's storage (SyntheticBassDrum's
     * transient_env_lp_), so a reused cell would start from the last hit's value (test_drums Golden found it) */
    for (u32 i = 0; i < sizeof(c->engine_state) / 4u; i++) ((volatile u32 *)c->engine_state)[i] = 0;
    r->rng_cell = (u32)&c->rng;
    e->start(c);
    e->trigger(c);
    r->rng_cell = 0;
    DSTATS->starts++;
    if (was == CELL_PLAYING) DSTATS->retrigs++;
}

/* Stock's pitch ratio for the voice this block (its render at S_DRUM_RENDER, the same operations in the same order):
 * voice +0x48 x 2^(the pitch parameter + fine tune / 1000 / 12), at most 8. */
static float pitch_ratio(void *root, void *voice) {
    float fine = (float)(int)W(root, S_ROOT_FINE) / 1000.f / 12.f;
    float p = FN(S_PARAM_VALUE, float (*)(void *))((u8 *)voice + S_VOICE_PITCH_PARAM);
    float x = FN(S_EXP2, float (*)(float))(fine + p);
    float ratio = F(voice, S_VOICE_RATIO) * x;
    return ratio > 8.f ? 8.f : ratio;
}

/* ---- D1: the per-voice render (root, voice, temp, out): 1 = rendered, 0 = stock's render ------------------------- */
extern "C" __attribute__((used)) int dr_render(void *root, void *voice, void *temp, void *out) {
    u32 c0 = CYCCNT;
    if (!root || !voice || W(root, 0) != S_DRUM_ROOT_VT || !area_ok()) return 0;   /* D8: one-shot pads only */
    DrumRun *r = run();
    DrumCell *c = cell(r, voice);
    if (!c) { r->cyc += CYCCNT - c0; return 0; }
    u32 serial = W(voice, S_VOICE_SERIAL);
    if (c->state == CELL_NEW || c->serial != serial || c->root != (u32)root) start(r, c, root, voice, serial);
    int loop = W(root, S_ROOT_LOOP) != 0;
    if (c->state == CELL_SAMPLE || (c->state == CELL_DONE && !loop)) { r->cyc += CYCCNT - c0; return 0; }
    volatile SaDrumStats *s = DSTATS;
    u32 n = FN(S_BUF_FRAMES, u32 (*)(void *))(out);
    if (!n || n > MAX_FRAMES || n > W(temp, S_BUF_CAPACITY)) {   /* review S3: never past the scratch buffer */
        s->short_bufs++;
        r->cyc += CYCCNT - c0;
        return 0;
    }
    if (n > s->n_max) s->n_max = n;
    FN(S_LFO_TICK, void (*)(void *, u32))((u8 *)voice + S_VOICE_LFO1, n);   /* as stock's render does first */
    float ratio = pitch_ratio(root, voice);
    if (ratio == 0.f) { r->cyc += CYCCNT - c0; return 1; }   /* stock renders nothing and skips the post chain */
    float *x = FN(S_SCRATCH_PTR, float *(*)(void *))(temp);
    FN(S_SCRATCH_SIZE, void (*)(void *, u32))(temp, n);
    if (c->state == CELL_DONE) {                              /* Loop on: the voice stays, the synth is silent */
        for (u32 i = 0; i < n; i++) x[i] = 0.f;
        W(voice, S_VOICE_CURSOR) = c->frames - 1u; W(voice, S_VOICE_CURSOR + 4u) = 0;
    } else {
        const DrumEngine *e = engine(c->engine);
        if (c->knobs != r->knob_serial) {                    /* the M4 published new knob values */
            u8 raw[DRUM_KNOBS];
            knob_entry(r, c->handle, raw);
            knob_values(e, raw, c->values);
            c->knobs = r->knob_serial;
        }
        r->rng_cell = (u32)&c->rng;
        e->apply(c, ratio);
        float peak = e->render(c, x, n);
        r->rng_cell = 0;
        c->elapsed += n;
        c->quiet = peak < QUIET ? c->quiet + 1u : 0u;
        int quiet = c->quiet >= QUIET_BLOCKS && c->elapsed >= MIN_FRAMES, cap = c->elapsed >= CAP_FRAMES;
        if (quiet || cap) {
            c->state = CELL_DONE;
            if (quiet) s->quiet_ends++; else s->cap_ends++;
        }
        /* the cursor: inside the carrier while the synth rings; at the end stock's render reaches next (frames when
         * playing forward, 0 on a reverse pad, voice +0x54 = 0), so it starts the release there; Loop on keeps it inside */
        u32 cursor = c->elapsed < c->frames ? c->elapsed : c->frames - 1u;
        if (c->state == CELL_DONE && !loop) cursor = B(voice, S_VOICE_FORWARD) ? c->frames : 0u;
        W(voice, S_VOICE_CURSOR) = cursor; W(voice, S_VOICE_CURSOR + 4u) = 0;
        s->renders++;
    }
    c->last_block = r->block;
    r->voices++;
    r->cyc += CYCCNT - c0;
    FN(S_DRUM_POST, void (*)(void *, void *, void *, void *))(root, voice, temp, out);   /* stock's own ending */
    return 1;
}
