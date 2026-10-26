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
/* M7-private line 0x240485C0: flags[8] | region ptr (C8) | region gen (CC) | GEN (D0) */
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
        clean(RINGSTAT, sizeof(RingStat));
        M7H->magic = DIAG_MAGIC;
        clean(M7H, sizeof(M7Head));
    }
    return 1;
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
    case 0x5B: case 0x5C: case 0x5D: case 0x5E: /* MIDI-out note on/off, bend, CC */
    case 0xB9: case 0xBA:                       /* note on/off to an object (seen in FUN_0805d200) */
    case 0xAC: case 0xAD:                       /* pad press/release */
    case 0x02: case 0x03:                       /* internal note on/off to a track (FUN_08062020) */
        return 1;
    }
    return 0;
}

__attribute__((used, retain)) void h_input(u32 engine, const u32 *ev) {
    u32 t = ev[0] & 0xFF;
    if (t == 0xAC) FLAGS[0] = 1;
    if (t == 0xAC || t == 0xAD || (t == 0xAE && FLAGS[0])) {
        if (t == 0xAE) FLAGS[0] = 0; /* only the first pressure event after a press */
        put(K_PAD, 0, engine, ev, 0);
    }
}

__attribute__((used, retain)) void h_send(u32 queue, const u32 *ev, u32 lr) {
    u32 t = ev[0] & 0xFF;
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
    DCIMVAC = (obj + 0x2340u) & ~31u;
    dsb();
    u32 fill = *(volatile u32 *)(obj + 0x2360u) - *(volatile u32 *)(obj + 0x2340u);
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
    if (t == 0x5F) {                     /* our pressure: first after each press only */
        if (!FLAGS[4]) return;
        FLAGS[4] = 0;
    }
    put(K_RING, 0, obj, ev, fill);
}

__attribute__((used, retain)) void h_consume(u32 queue, u32 idx, u32 lr) {
    if (idx != 0) return;
    u32 free_slot = DEDUPE_N;
    for (u32 i = 0; i < DEDUPE_N; i++) {
        u32 q = DEDUPE[2 * i], l = DEDUPE[2 * i + 1];
        if (q == queue && l == lr) return; /* pair already logged */
        if (q == 0 && free_slot == DEDUPE_N) free_slot = i;
    }
    if (free_slot == DEDUPE_N) {
        M7H->dedupe_full++;
        return;
    }
    if (!m4_ready()) return; /* don't burn a dedupe slot before the M4 can log it */
    DEDUPE[2 * free_slot] = queue;
    DEDUPE[2 * free_slot + 1] = lr;
    put(K_CONSUME, lr, queue, 0, idx);
}

/* For m7_polyat.c: voice-action records and counters for the M4 trailer line. */
void diag_put(u32 kind, u32 lr, u32 obj, const u32 *ev, u32 extra) { put(kind, lr, obj, ev, extra); }

void diag_stat(u32 idx, u32 value) {
    if (idx >= 4 || M7H->magic != DIAG_MAGIC) return;
    M7H->r[idx] = value;
    clean(M7H, sizeof(M7Head));
}
