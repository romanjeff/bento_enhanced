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
 * Counters: 16 x 32 B at SLICE(0x660), M7-only, inside our D2 SRAM slice
 * (diag_shared.h). A sequence whose owner or playing row changes starts
 * counting again.
 */
#include "diag_shared.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))

typedef struct { u32 sequence, owner, row, loop, boundaries, r[3]; } Counter;
typedef struct { u32 magic, gen, r[6]; } CondHead;

#define COUNTERS ((volatile Counter *)SLICE(0x660u))
#define NCOUNTERS 16u
#define CHEAD ((volatile CondHead *)SLICE(0x860u))
#define COND_MAGIC 0x31444E43u /* "CND1" */
_Static_assert(SLICE(0x860u) + sizeof(CondHead) <= SLICE(0x9A0u), "conditions state past the M7 stack bottom");

/* (Re)initialise once per boot (BE4.47: our RAM is zeroed at every reset; BOOT_GEN, diag_shared.h, is 1 from the
 * reset on, on 1.6.1 the M4's published generation). */
static void ensure_init(void) {
    /* The config line is written only by the M4: invalidate before reading. */
    *(volatile u32 *)0xE000EF5Cu = (u32)CFG; /* DCIMVAC */
    __asm volatile("dsb" ::: "memory");
    u32 gen = BOOT_GEN();
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
#ifdef BE_PLAY_COUNT_WRAP
/* 1.6.8 count-on mode (S3 took it for Play Count; review: it is most likely open-length recording, see
 * test_s3_m7 test_count_on_mode_does_not_loop_the_clip): with the track's flag (byte +0x30) set, the player's first boundary call
 * (COND_LOOP_CALL_1) is made only at a restart (count < 0) or at 1.6.8's new 256-step wrap (0x08060AE6,
 * after FUN_08061E80). 1.6.1 has no such wrap: it is not a pass, so A:B keeps the 1.6.1 phase. lr: the
 * boundary function's return address (the loop trampoline passes the caller's lr). */
__attribute__((used, retain)) void cond_loop(u32 seq, u32 lr) {
    if ((lr & ~1u) == S_COND_LOOP_CALL_1 + 4u && *(volatile u8 *)(seq + 0x30u) && (int)W(seq, 0x14) >= 0) return;
#else
__attribute__((used, retain)) void cond_loop(u32 seq) {
#endif
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
