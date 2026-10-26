/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Trigger conditions (A:B) for every sequencer track: M7 side.
 *
 * Ported from bento-community-lab's T2 module (k16_work/bento_controls/
 * conditions.c, GPL-3.0-or-later): same data model and decisions, plus
 * self-initialisation (Lab zeroed the counters from its startup hook).
 *
 * Data: the native per-note probability byte. Values below 100 keep their
 * native meaning. Ours (stable, so saved projects keep their meaning):
 *   100..134 A:B in the order 1:2, 2:2, 1:3, 2:3, 3:3, ..., 8:8 (Lab T2)
 *   135..169 !A:B (same order), 170 1ST, 171 !1ST, 172 PRE, 173 !PRE.
 * A:B plays on pass A of every B passes of its sequence (passes counted per
 * sequence, from launch/restart); !A:B on every other pass. 1ST plays on the
 * first pass only. PRE plays if the most recent A:B / !A:B / 1ST / !1ST
 * decision on the sequence passed. The UI shows its own order (build.py).
 *
 * Hooks (see build.py):
 *   cond_decide(seq, value)  probability gate 0x08060B64: 0 skip, 1 play,
 *                            2 use the native probability decision
 *   cond_loop(seq)           sequence loop boundary 0x08061740: count passes
 * Counters: 16 x 32 B at 0x3003C660, M7-only, inside our D2 SRAM slice
 * (diag_shared.h). A sequence whose owner or playing row changes starts
 * counting again.
 */
#include "diag_shared.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))

typedef struct { u32 sequence, owner, row, loop, boundaries, r[3]; } Counter;
typedef struct { u32 magic, gen, r[6]; } CondHead;

#define COUNTERS ((volatile Counter *)0x3003C660u)
#define NCOUNTERS 16u
#define CHEAD ((volatile CondHead *)0x3003C860u)
#define COND_MAGIC 0x31444E43u /* "CND1" */
_Static_assert(0x3003C860u + sizeof(CondHead) <= 0x3003C9A0u, "conditions state past the M7 stack bottom");

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
                c->owner = owner; c->row = row; c->loop = 0; c->boundaries = 0; c->r[0] = 0;
            }
            return c;
        }
        if (!c->sequence && !free_c) free_c = c;
    }
    if (!free_c) return 0;
    free_c->owner = owner; free_c->row = row; free_c->loop = 0; free_c->boundaries = 0; free_c->r[0] = 0;
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

/* 0 = skip, 1 = play, 2 = original probability decision.
 * Stored values (kept stable so saved projects keep their meaning):
 *   100..134 A:B, 135..169 !A:B (same order), 170 1ST, 171 !1ST, 172 PRE, 173 !PRE.
 * PRE = result of the most recent A:B / !A:B / 1ST / !1ST decision on this
 * sequence (false until there is one); PRE steps do not change it. */
#define PRE(c) ((c)->r[0])
static u32 decide(volatile Counter *c, u32 value);

/* value = the probability byte; word = the note's +20 word (m4_step.c).
 * A condition in the word gates the step and stock's probability then rolls
 * as usual (condition AND probability). A legacy condition in the byte
 * (projects not yet migrated) plays without a probability roll, as before. */
__attribute__((used, retain)) u32 cond_decide(u32 seq, u32 value, u32 word) {
    u32 wc = step_cond(word);
    if (wc) {
        volatile Counter *c = entry(seq);
        if (!c) return 2;
        return decide(c, 99u + wc) ? 2u : 0u;
    }
    if (value < 100 || value > 173) return 2;
    volatile Counter *c = entry(seq);
    if (!c) return 1; /* counter table full: behave like native "always" */
    return decide(c, value);
}

static u32 decide(volatile Counter *c, u32 value) {
    if (value >= 172) return (PRE(c) != 0) ^ (value == 173);
    u32 pass;
    if (value >= 170) {
        pass = (c->loop == 0) ^ (value == 171);
    } else {
        u32 i = value - (value >= 135 ? 135u : 100u), b = 2;
        while (i >= b) { i -= b; b++; }
        pass = (c->loop % b == i) ^ (value >= 135);
    }
    PRE(c) = pass;
    return pass;
}
