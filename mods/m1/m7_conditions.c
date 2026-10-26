/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Trigger conditions (A:B) for every sequencer track: M7 side.
 *
 * Ported from bento-community-lab's T2 module (k16_work/bento_controls/
 * conditions.c, GPL-3.0-or-later): same data model and decisions, plus
 * self-initialisation (Lab zeroed the counters from its startup hook).
 *
 * Data: the native per-note probability byte. Values below 100 keep their
 * native meaning; 100..134 are A:B conditions in the order 1:2, 2:2, 1:3, 2:3,
 * 3:3, ..., 8:8 (35 values). A note with condition A:B plays on pass A of every
 * B passes of its sequence (passes counted per sequence, from launch/restart).
 *
 * Hooks (see build.py):
 *   cond_decide(seq, value)  probability gate 0x08060B64: 0 skip, 1 play,
 *                            2 use the native probability decision
 *   cond_loop(seq)           sequence loop boundary 0x08061740: count passes
 * Counters: 16 x 32 B at 0x24048A60, M7-only, inside the RAM we reserve by
 * lowering the heap end. A sequence whose owner or playing row changes starts
 * counting again.
 */
#include "diag_shared.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))

typedef struct { u32 sequence, owner, row, loop, boundaries, r[3]; } Counter;
typedef struct { u32 magic, gen, r[6]; } CondHead;

#define COUNTERS ((volatile Counter *)0x24048A60u)
#define NCOUNTERS 16u
#define CHEAD ((volatile CondHead *)0x24048C60u)
#define COND_MAGIC 0x31444E43u /* "CND1" */
_Static_assert(0x24048C60u + sizeof(CondHead) <= 0x24048DA0u, "conditions state past the M7 stack bottom");

/* Our RAM survives a reset: (re)initialise once per M4 boot generation, or on
 * first use if the M4 has not published one yet. */
static void ensure_init(void) {
    /* The config line is written only by the M4: invalidate before reading. */
    *(volatile u32 *)0xE000EF5Cu = (u32)CFG; /* DCIMVAC */
    __asm volatile("dsb" ::: "memory");
    u32 gen = (CFG->magic == DIAG_MAGIC) ? CFG->r[0] : 0u;
    if (CHEAD->magic == COND_MAGIC && CHEAD->gen == gen) return;
    volatile u32 *p = (volatile u32 *)COUNTERS;
    for (u32 i = 0; i < NCOUNTERS * sizeof(Counter) / 4; i++) p[i] = 0;
    CHEAD->gen = gen;
    CHEAD->magic = COND_MAGIC;
}

static volatile Counter *entry(u32 seq) {
    ensure_init();
    u32 owner = W(seq, 12), row = W(seq, 0x86C0);
    volatile Counter *free_c = 0;
    for (u32 i = 0; i < NCOUNTERS; i++) {
        volatile Counter *c = &COUNTERS[i];
        if (c->sequence == seq) {
            if (c->owner != owner || c->row != row) {
                c->owner = owner; c->row = row; c->loop = 0; c->boundaries = 0;
            }
            return c;
        }
        if (!c->sequence && !free_c) free_c = c;
    }
    if (!free_c) return 0;
    free_c->owner = owner; free_c->row = row; free_c->loop = 0; free_c->boundaries = 0;
    free_c->sequence = seq;
    return free_c;
}

/* Called at each loop boundary of a sequence (seq+0x14 < 0 = launch/restart). */
__attribute__((used, retain)) void cond_loop(u32 seq) {
    volatile Counter *c = entry(seq);
    if (!c) return;
    if (c->boundaries && (int)W(seq, 0x14) >= 0) c->loop++;
    else c->loop = 0;
    c->boundaries = 1;
}

/* 0 = skip, 1 = play, 2 = original probability decision. */
__attribute__((used, retain)) u32 cond_decide(u32 seq, u32 value) {
    if (value < 100 || value >= 135) return 2;
    u32 i = value - 100, b = 2;
    while (i >= b) { i -= b; b++; }
    volatile Counter *c = entry(seq);
    if (!c) return 1; /* counter table full: behave like native "always" */
    return c->loop % b == i;
}
