/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1 diagnostic, M7 side: record each hop of the pad -> MIDI-out path.
 *
 * Called from four 4-byte entry trampolines (see build.py). Integer-only
 * (built soft-float), no allocation, no I/O. Interrupts are masked only
 * while one record slot is reserved and filled.
 *
 * Hooks:
 *   h_input  (engine, ev)      FUN_08059980  engine input-event handler
 *   h_send   (queue, ev, lr)   FUN_08065600+8 msg_send, after Lab's own hook
 *   h_ring   (obj, ev)         FUN_08082640  post to the M4 MIDI ring
 *   h_consume(queue, idx, lr)  FUN_080655a0  queue read (consumer identity)
 */
#include "diag_shared.h"

#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define DCCMVAC (*(volatile u32 *)0xE000EF68u) /* clean D-cache line to PoC */
#define DCIMVAC (*(volatile u32 *)0xE000EF5Cu) /* invalidate D-cache line */

#define DEDUPE ((volatile u32 *)PRIV_BASE)          /* 32 x {queue, lr} */
#define DEDUPE_N 16u
/* M7-private line 0x3003C1C0: flags[8] | region ptr (C8) | region gen (CC) | GEN (D0) */
#define FLAGS ((volatile u8 *)(PRIV_BASE + 0x80u)) /* armed "log the first X after a press":
                                                        [0] input AE, [1] send AE, [2] send 0x0E,
                                                        [3] send 0x5F, [4] ring 0x5F */
#define GEN (*(volatile u32 *)(PRIV_BASE + 0x90u)) /* M4 boot generation last seen */
_Static_assert(PRIV_BASE + 0x88u == M7_REGION_CACHE, "private line layout");

static inline void dsb(void) { __asm volatile("dsb" ::: "memory"); }
static inline u32 irq_save(void) {
    u32 p;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(p)::"memory");
    return p;
}
static inline void irq_restore(u32 p) { __asm volatile("msr primask, %0" ::"r"(p) : "memory"); }

static void clean(volatile const void *p, u32 n) {
    u32 a = (u32)p & ~31u, end = (u32)p + n;
    dsb();
    for (; a < end; a += 32) DCCMVAC = a;
    dsb();
}

static int m4_ready(void) {
    /* The M7 never writes the M4 header line, so invalidating it is safe. */
    DCIMVAC = (u32)M4H;
    dsb();
    if (M4H->magic != DIAG_MAGIC) return 0;
    if (M7H->magic != DIAG_MAGIC || GEN != M4H->r[0]) {
        /* First call this boot (or M4 rebooted): reset our side. */
        for (u32 i = 0; i < DEDUPE_N * 2; i++) DEDUPE[i] = 0;
        for (u32 i = 0; i < 8; i++) FLAGS[i] = 0;
        GEN = M4H->r[0];
        M7H->wr = M4H->rd; /* start empty relative to the M4 */
        M7H->dropped = 0;
        M7H->dedupe_full = 0;
        for (u32 i = 0; i < 4; i++) M7H->r[i] = 0;
        RINGSTAT->max_fill = RINGSTAT->overflows = RINGSTAT->posts = 0;
        RINGSTAT->min_msp = RINGSTAT->min_sp = 0xFFFFFFFFu;
        RINGSTAT->heartbeat = 0;
        clean(RINGSTAT, sizeof(RingStat));
        M7H->magic = DIAG_MAGIC;
        clean(M7H, sizeof(M7Head));
    }
    return 1;
}

/* Stack-depth probe: our hooks add stack on top of stock code, sometimes in
 * interrupts, and the M7 main stack's bottom (0x24048DA0) sits directly above
 * Lab's state and our slice. Record the lowest SP we ever see. */
void diag_sp(void) {
    u32 sp;
    __asm volatile("mov %0, sp" : "=r"(sp));
    if (RINGSTAT->min_sp == 0) return; /* not initialised yet this boot */
    int changed = 0;
    if (sp < RINGSTAT->min_sp) { RINGSTAT->min_sp = sp; changed = 1; }
    if (sp >= M7_STACK_BOTTOM && sp < M7_STACK_TOP && sp < RINGSTAT->min_msp) { RINGSTAT->min_msp = sp; changed = 1; }
    if (changed) clean(RINGSTAT, sizeof(RingStat));
}

static void put(u32 kind, u32 lr, u32 obj, const u32 *ev, u32 extra) {
    u32 p = irq_save();
    if (!m4_ready()) { irq_restore(p); return; }
    u32 wr = M7H->wr;
    if (wr - M4H->rd >= DIAG_RECORDS) { /* M4 behind: never overwrite unread records */
        M7H->dropped++;
        clean(M7H, sizeof(M7Head));
        irq_restore(p);
        return;
    }
    volatile Rec *r = &RECS[wr % DIAG_RECORDS];
    r->cyc = CYCCNT;
    r->kind = kind | (wr << 16);
    r->lr = lr;
    r->obj = obj;
    for (u32 i = 0; i < 6; i++) r->ev[i] = ev ? ev[i] : 0;
    r->extra = extra;
    r->pad_ = 0;
    clean(r, sizeof(Rec));
    M7H->wr = wr + 1; /* publish only after the record is visible */
    clean(M7H, sizeof(M7Head));
    irq_restore(p);
}

static int interesting_send(u32 t) {
    switch (t) {
    case 0x5B: case 0x5C:                       /* MIDI-out note on/off (bend 0x5D / CC 0x5E: streams, v3.44) */
    case 0xB9: case 0xBA:                       /* note on/off to an object (seen in FUN_0805d200) */
    case 0x02: case 0x03:                       /* internal note on/off to a track (FUN_08062020) */
    /* (pad 0xAC/0xAD are covered by the PAD records; their 3-4 queue copies are not logged) */
        return 1;
    }
    return 0;
}

/* Per-pad strikes for strike-velocity step programming (m4_step.c): stock's
 * velocity formula, peak over the press and its first pressure samples. */
static u32 strike_vel(const u32 *ev) {
    u32 raw = ev[4] & 0xFFFFu;
    return (raw > 0xD000u ? 0xD000u : raw) * 127u / 0xD000u;
}
static void strike(const u32 *ev, u32 t) {
    u32 p = ev[3];
    if (p >= 16u) return;
    if (t == 0xACu) {
        STRIKES->seq[p] = (u8)(STRIKES->seq[p] + 1u);
        STRIKES->vel[p] = (u8)strike_vel(ev);
        STRIKE_PEAK[p] = STRIKE_PEAK_SAMPLES;
    } else if (t == 0xAEu && STRIKE_PEAK[p]) {
        STRIKE_PEAK[p] = (u8)(STRIKE_PEAK[p] - 1u);
        u32 v = strike_vel(ev);
        if (v <= STRIKES->vel[p]) return;
        STRIKES->vel[p] = (u8)v;
    } else {
        if (t == 0xADu) STRIKE_PEAK[p] = 0;
        return;
    }
    clean(STRIKES, sizeof(Strikes));
}

/* M2 pressure capture (see CapSlot in diag_shared.h). */
#define UWTICK (*(volatile u32 *)0x2401EDB8u)    /* stock M7 HAL tick, ms */
static volatile CapBlock *cap_block(void) {
    DCIMVAC = (u32)CAPCFG & ~31u;                 /* M4-written line: never dirty here */
    dsb();
    u32 b = CAPCFG->block;
    if (CAPCFG->magic != CAPCFG_MAGIC || CAPCFG->check != ~b || !b) return 0;
    return (volatile CapBlock *)b;
}
static void cap_fill(volatile CapSlot *s, u32 idx) {   /* hold the last value through idx */
    u8 last = s->s[s->n - 1];
    for (u32 i = s->n; i <= idx; i++) s->s[i] = last;
    if (idx >= s->n) s->n = idx + 1u;
}
static void cap(const u32 *ev, u32 t) {
    u32 p = ev[3];
    if (p >= 16u) return;
    volatile CapBlock *b = cap_block();
    if (!b) return;
    u32 now = UWTICK, seq = STRIKES->seq[p];
    volatile CapSlot *s = &b->slot[p][seq & 1u];
    if (t == 0xACu) {                             /* strike() has counted this press */
        s->done = 0; s->seq = seq; s->t0 = now; s->t_end = now;
        s->shift = 0; s->s[0] = (u8)strike_vel(ev); s->n = 1;
        return;
    }
    if (s->seq != seq || s->done || !s->n) return;
    u32 idx = ((now - s->t0) / CAP_MS) >> s->shift;
    while (idx >= CAP_SAMPLES) {                   /* held long: halve the resolution */
        for (u32 i = 0; i < CAP_SAMPLES / 2u; i++) s->s[i] = s->s[2u * i];
        s->n = (s->n + 1u) / 2u; s->shift++;
        idx = ((now - s->t0) / CAP_MS) >> s->shift;
    }
    if (t == 0xAEu) {
        cap_fill(s, idx);
        s->s[idx] = (u8)strike_vel(ev);
    } else if (t == 0xADu) {
        cap_fill(s, idx);
        s->t_end = now; s->done = 1;
    }
}

__attribute__((used, retain)) void h_input(u32 engine, const u32 *ev) {
    u32 t = ev[0] & 0xFF;
    diag_sp();
    if (t == 0xAC) FLAGS[0] = 1;
    if (t >= 0xAC && t <= 0xAE) { strike(ev, t); cap(ev, t); }
    if (t == 0xAC || t == 0xAD || (t == 0xAE && FLAGS[0])) {
        if (t == 0xAE) FLAGS[0] = 0; /* only the first pressure event after a press */
        put(K_PAD, 0, engine, ev, 0);
    }
}

__attribute__((used, retain)) void h_send(u32 queue, const u32 *ev, u32 lr) {
    u32 t = ev[0] & 0xFF;
    diag_sp();
    if (t == 0xAC) FLAGS[1] = FLAGS[2] = FLAGS[3] = FLAGS[4] = 1;
    /* Raw pressure 0xAE, native poly pressure 0x0E and our 0x5F flood at up to
     * audio-tick / 100 Hz rates: log only the first of each after a press. */
    if (interesting_send(t) || (t == 0xAE && FLAGS[1]) || (t == 0x0E && FLAGS[2]) || (t == 0x5F && FLAGS[3])) {
        if (t == 0xAE) FLAGS[1] = 0;
        if (t == 0x0E) FLAGS[2] = 0;
        if (t == 0x5F) FLAGS[3] = 0;
        put(K_SEND, lr, queue, ev, 0);
    }
}

__attribute__((used, retain)) void h_ring(u32 obj, const u32 *ev) {
    u32 t = ev[0] & 0xFF;
    /* Ring fill before this post. rd (obj+0x2340) is written only by the M4 and
     * sits alone in its cache line (entries end at +0x2340, wr is at +0x2360),
     * so invalidating that line is safe. The native writer has no full check:
     * posting with 16 unread entries overwrites the oldest. */
    diag_sp();
    DCIMVAC = (obj + 0x2340u) & ~31u;
    dsb();
    u32 fill =*(volatile u32 *)(obj + 0x2360u) - *(volatile u32 *)(obj + 0x2340u);
    if (m4_ready()) { /* also initialises our side on the first call each boot */
        RINGSTAT->posts++;
        if (fill > RINGSTAT->max_fill) RINGSTAT->max_fill = fill;
        if (fill >= MIDI_RING_SLOTS) RINGSTAT->overflows++;
        clean(RINGSTAT, sizeof(RingStat));
    }
    if (fill >= MIDI_RING_SLOTS) {       /* always log an overwrite, whatever the type */
        put(K_RING, 0, obj, ev, 0x100u | fill);
        return;
    }
    if (t >= 0x61 && t <= 0x63) return; /* clock/start/stop: too frequent, not needed */
    /* Bend, CC and channel pressure come as modulation streams (v3.41+: up to
     * 400/s per port). Tracing each one filled the 256 KB session log within
     * seconds and kept the UI task formatting records (BE-MT1 froze its UI loop
     * after 142 s; BE-MT3's first save then wrote 0 bytes and later saves had
     * nothing left to add). Overwrites are still logged above. */
    if (t == 0x5D || t == 0x5E || t == 0x60) return;
    if (t == 0x5F) {                     /* our pressure: first after each press only */
        if (!FLAGS[4]) return;
        FLAGS[4] = 0;
    }
    put(K_RING, 0, obj, ev, fill);
}

/* D0 (v3.64): copies of stock's figures for the M4 black box (see diag_shared.h).
 * 0x2401D9D8: +0 blocks, +4 wrong-half arrivals (mostly wait timeouts), +0x18 worst overrun
 * (0 or negative) with +0xC least remaining headroom on the normal path.
 * 0x2401D9F8: +0xC gaps under 0x200, +0x10 gaps over 0x600, +8 largest gap (these wrap at
 * 0x800 and include pre-wait jitter), +0x14 wait timeouts, +0x18 most polls, +0x1C waits.
 * 0x2401D33C >> 1: DMA half events; events - blocks = renders dropped.
 * The heap list is only followed through aligned addresses inside the heap, so a walk
 * racing the allocator can give a wrong figure but not a fault. */
#define G(a) (*(volatile u32 *)(a))
#define HEAP_LO 0x2401EDC0u
#define HEAP_HI 0x24048DA0u
#define HEAP_WALK_MAX 64u
/* Stock's ring object through the alias stock's M7 writes it by (shareable, not cached). */
#define LOADOBJ 0x100451BCu
#define BLOCK_BUDGET_DIV256 5000u                /* 1,280,000 cycles, stock's constant */

static void load_clear(volatile D0Load *l) {
    l->seen = l->sum = l->peak = 0;
    for (u32 i = 0; i < sizeof(M7Probe) / 4u; i++) ((volatile u32 *)PROBE)[i] = 0;
    for (u32 i = 0; i < 4u; i++) l->h[i] = 0;
}

#define G8(a) (*(volatile u8 *)(a))
#define G16(a) (*(volatile u16 *)(a))
#define LINK 0x2401D830u                         /* stock's Controller link object */
#define PANEL_BOOTED 0x2401D0AAu                 /* Controller word: nonzero right after it boots */
#define PANEL_CRC 0x2401D0B4u
#define PANEL_TYPE 0x2401D070u                   /* frame type: 3 = panel data */
#define PANEL_BUTTONS 0x2401D078u
#define SPI1_HANDLE 0x2401E1B8u

static void load_block(void) {
    volatile D0Count *c = D0C;
    u32 idx = G(LOADOBJ + 0x408u);
    if (idx == c->last_idx) return;
    volatile D0Link *k = D0LINK;                 /* once a block: a slower sample misses presses */
    u32 b = G16(PANEL_BUTTONS);
    if (b != k->btn_prev) { k->btn_changes++; k->btn_or |= b ^ k->btn_prev; k->btn_prev = b; }
    u32 booted = G16(PANEL_BOOTED) != 0, crc = G(PANEL_CRC);
    if (booted && !k->boot_prev) k->fw_boots++;
    k->boot_prev = booted;
    if (crc != k->crc_prev) { k->crc_changes++; k->crc_prev = crc; }
    u32 start = G(LOADOBJ + 0x414u);
    if (idx == c->last_idx + 1u) {               /* value and period of the block just finished */
        u32 period = (start - D0A->prev_start) >> 8;
        u32 pm = float_permille(G(LOADOBJ + 8u + ((idx - 1u) & 0xFFu) * 4u));
        if (pm > 1000u) pm = 1000u;              /* a ratio by construction; guards the product */
        if (period > 4000000u) period = 4000000u;
        pm = pm * period / BLOCK_BUDGET_DIV256;
        volatile D0Load *l = D0L;
        l->seen++; l->sum = l->sum > 0xFFFFFFFFu - pm ? 0xFFFFFFFFu : l->sum + pm;
        if (pm > l->peak) l->peak = pm;
        l->h[pm < 500u ? 0 : pm < 800u ? 1 : pm < 1000u ? 2 : 3]++;
        volatile M7Probe *q = PROBE;
        u32 seq = G(0x2401D678u + 4u);
        if (q->seq_n >= 0x100000u) { q->seq_sum >>= 1; q->seq_n >>= 1; }   /* cycles >> 8: no overflow */
        q->seq_sum += seq >> 8; q->seq_n++;
        if (seq > q->seq_peak) q->seq_peak = seq;
    } else {
        c->missed++;                             /* several blocks passed, or stock restarted */
    }
    c->last_idx = idx;
    D0A->prev_start = start;
}

/* Stock's block count only rises within a boot. Lower than our copy means a reset the M4
 * hasn't reported yet: leave the previous run's figures for its boot print. GEN moves soon
 * after that print, and the new-boot path in d0_publish then clears them. */
static int d0_unreported_reset(void) {
    return D0A->magic == D0_MAGIC && D0A->gen == GEN && G(0x2401D9D8u) < D0C->blocks;
}

/* One read-only price check: cycles for 8 Device reads (M4 pool) and 8 cached fills (free ext pool
 * space; clean + invalidate keeps any data there). Interrupts off for about 3 us, a few times a second.
 * The lines are below the region, so maintenance never touches it. A fill can evict a region line
 * that shares its set (the region spans every set), which only writes it back: no data is lost. */
#define PROBE_FREE 0xC5FF0000u
_Static_assert(PROBE_FREE + 8u * 32u <= M1_REGION_ADDR, "the price check stays out of the region");
static void mem_probe(void) {
    u32 b = CAPCFG->block;                        /* only an address: read here, not followed unless in range */
    if (b < 0xC6000000u || b >= 0xC8000000u || (b & 31u)) return;
    volatile M7Probe *q = PROBE;
    u32 c = PROBE_FREE, s = 0, pm;
    __asm volatile("mrs %0, primask\n cpsid i" : "=r"(pm)::"memory");
    for (u32 i = 0; i < 8u; i++) *(volatile u32 *)0xE000EF70u = c + i * 32u;   /* DCCIMVAC */
    dsb();
    u32 t0 = *(volatile u32 *)0xE0001004u;
    for (u32 i = 0; i < 8u; i++) s += *(volatile u32 *)(b + i * 32u);
    dsb();
    u32 t1 = *(volatile u32 *)0xE0001004u;
    for (u32 i = 0; i < 8u; i++) s += *(volatile u32 *)(c + i * 32u);
    dsb();
    u32 t2 = *(volatile u32 *)0xE0001004u;
    __asm volatile("msr primask, %0" ::"r"(pm) : "memory");
    (void)s;
    q->dev_sum += t1 - t0; q->fill_sum += t2 - t1; q->mem_n++;
    if (t1 - t0 > q->dev_max) q->dev_max = t1 - t0;
    if (t2 - t1 > q->fill_max) q->fill_max = t2 - t1;
}

static void d0_publish(void) {
    volatile D0Top *d = D0A;
    volatile D0Count *c = D0C;
    volatile D0Load *l = D0L;
    u32 free = 0, largest = 0, chunks = 0, bad = 0;
    for (u32 p = G(0x24000178u); p; p = G(p)) {
        if (p < HEAP_LO || p >= HEAP_HI - 8u || (p & 3u) || chunks >= HEAP_WALK_MAX) { bad = 1; break; }
        u32 size = G(p + 4u);
        free += size;
        if (size > largest) largest = size;
        chunks++;
    }
    DCIMVAC = (u32)LOADREQ;                      /* M4-written line: never dirty here */
    dsb();
    u32 req = LOADREQ->magic == D0_MAGIC ? LOADREQ->req : 0u;
    if (d->magic != D0_MAGIC || d->gen != GEN) { /* new boot; D2 holds garbage after a cold start */
        d->magic = D0_MAGIC; d->gen = GEN; d->heap_min = 0xFFFFFFFFu;
        c->missed = 0;
        load_clear(l); l->ack = req;
        volatile D0Link *n = D0LINK;
        n->restarts = 0; n->btn_changes = 0; n->btn_or = 0; n->rx_prev = 0;
        n->fw_boots = 0; n->crc_changes = 0;
    } else if (l->ack != req) {
        load_clear(l); l->ack = req;
        c->missed = 0;
    }
    if (!(RINGSTAT->heartbeat & 0x3FFFu)) mem_probe();
    clean(PROBE, sizeof(M7Probe));
    d->heap_free = free; d->chunks = chunks; d->largest = largest; d->bad = bad;
    if (!bad && free < d->heap_min) d->heap_min = free;
    d->dtcm_used = G(0x2401B0B8u); d->axi_used = G(0x24000448u);
    d->ext_left = G(0x24000160u); d->shared_left = G(0x2400016Cu);
    d->wrong_half = G(0x2401D9D8u + 4u);
    d->head_min = d->wrong_half ? G(0x2401D9D8u + 0x18u) : G(0x2401D9D8u + 0xCu);
    d->gap_over = G(0x2401D9F8u + 0x10u); d->gap_max = G(0x2401D9F8u + 8u);
    c->blocks = G(0x2401D9D8u); c->events = G(0x2401D33Cu) >> 1;
    c->waited = G(0x2401D9F8u + 0x1Cu); c->timeouts = G(0x2401D9F8u + 0x14u);
    c->max_polls = G(0x2401D9F8u + 0x18u); c->gap_under = G(0x2401D9F8u + 0xCu);
    volatile D0Link *k = D0LINK;
    k->rx = G(LINK + 0x194u); k->bad = G(LINK + 0x19Cu); k->tx = G(LINK + 0x190u);
    if (k->rx < k->rx_prev) k->restarts++;
    k->rx_prev = k->rx;
    k->version = G16(LINK + 0x1A2u); k->reboots = G(0x24000450u);
    k->spi_state = G8(SPI1_HANDLE + 0x81u);
    k->frame_type = G8(PANEL_TYPE);
    clean(k, sizeof(D0Link));
    clean(d, sizeof(D0Top));
    clean(c, sizeof(D0Count) + sizeof(D0Load));
    clean((volatile const void *)M1_REGION_ADDR, REGION_STATS_BYTES);   /* cached since v3.70; the M4 prints them */
}

/* Queue reads happen every audio block on the M7: a cheap heartbeat for the M4's
 * "M7 stopped" watch, and a frequent stack-depth sample. (Consumer discovery,
 * used to map the pad path, is no longer logged.) */
__attribute__((used, retain)) void h_consume(u32 queue, u32 idx, u32 lr) {
    (void)queue; (void)lr;
    if (idx != 0 || M7H->magic != DIAG_MAGIC) return;
    diag_sp();
    int stale = d0_unreported_reset();
    if (!stale) load_block();
    RINGSTAT->heartbeat++;
    if ((RINGSTAT->heartbeat & 63u) == 0) clean(RINGSTAT, sizeof(RingStat));
    if (!stale && (RINGSTAT->heartbeat & 255u) == 0) d0_publish();
}

/* For m7_polyat.c: voice-action records and counters for the M4 trailer line. */
void diag_put(u32 kind, u32 lr, u32 obj, const u32 *ev, u32 extra) { put(kind, lr, obj, ev, extra); }

void diag_stat(u32 idx, u32 value) {
    if (idx >= 4 || M7H->magic != DIAG_MAGIC) return;
    M7H->r[idx] = value;
    clean(M7H, sizeof(M7Head));
}
