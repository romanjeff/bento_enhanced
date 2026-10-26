/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Audio stall snapshot, M7 side (idle-restart investigation).
 *
 * The M7 main audio loop FUN_0808DF00 refreshes WWDG1 (~0.28 s) only when the
 * audio DMA half-buffer counter advances; otherwise it spins through the
 * M4->M7 message pump. Stock's cause-5 record then says only where the CPU
 * happened to be. We hook the two calls in that loop:
 *   stall_progress  bl 0x080A0460 at 0x0808DF54 (watchdog refresh: progress)
 *   stall_check     bl 0x080816C0 at 0x0808DFB4 (message pump: no progress)
 * and, once per boot, when there has been no progress for STALL_MS, record
 * the M7's own view: interrupt masking, the NVIC state of the DMA2 stream 5/7
 * interrupts (IRQ 68/70), the active/pending vector, and the DMA flags. The
 * record lives in reset-surviving RAM; the M4 prints it at the next boot.
 *
 * RAM 0x3003C080..0x3003C100: line 0x3003C080 = M7 tracking (M7 only); lines
 * 0x3003C0A0.. = the record, written and cleaned by the M7 only when it snaps.
 * The M4 reads the record after the reset and clears its magic; the M7 never
 * dirties those lines otherwise, so a stale cached copy is never written back.
 */
#include "diag_shared.h"

#define CYCCNT (*(volatile u32 *)0xE0001004u)
#define DCCMVAC (*(volatile u32 *)0xE000EF68u)
#define DCIMVAC (*(volatile u32 *)0xE000EF5Cu)
#define ICSR (*(volatile u32 *)0xE000ED04u)
#define NVIC_ISER ((volatile u32 *)0xE000E100u)
#define NVIC_ISPR ((volatile u32 *)0xE000E200u)
#define NVIC_IABR ((volatile u32 *)0xE000E300u)
#define DMA2 ((volatile u32 *)0x40020400u)
#define STALL_MS 100u
/* Stock HAL tick (ms since this M7 boot; .bss, zeroed at reset). Our tracking
 * line survives resets, so without this guard the normal ~1.4 s display
 * bring-up of the NEXT boot looked like a stall (v3.1-v3.17 records). */
#define UWTICK (*(volatile u32 *)0x2401EDB8u)
#define BOOT_GUARD_MS 5000u
#define MS_CYC 480000u

typedef struct {
    u32 last, armed, gen, snapped, pad_[4]; /* tracking: its own cache line, rewritten on every
                                              audio block (the M4 never writes this line) */
    u32 magic, sgen, cycles, primask, basepri, faultmask, icsr, msp;
    u32 iser[2], ispr[2], iabr[2];          /* NVIC words 1 and 2: IRQ 32..95 */
    u32 lisr, hisr, s5cr, s5ndtr, s7cr, s7ndtr;
    u32 mux[2], sai_sr[2];                  /* streams 5, 7: DMAMUX1 request (which SAI block), its SR */
} Stall7;
#define ST ((volatile Stall7 *)0x3003C080u)
#define STALL7_MAGIC 0x38544C53u /* "SLT8": layout grew (SAI status); m4_diag.c matches */
_Static_assert(sizeof(Stall7) <= 0x80u, "stall record past 0x3003C100");

static void clean_all(void) {
    __asm volatile("dsb" ::: "memory");
    for (u32 a = 0x3003C080u; a < 0x3003C100u; a += 32) DCCMVAC = a;
    __asm volatile("dsb" ::: "memory");
}

/* Audio progressed (the loop is about to refresh the watchdog). */
__attribute__((used, retain)) void stall_progress(void) {
    DCIMVAC = (u32)CFG;                      /* M4-written line: never dirty here */
    __asm volatile("dsb" ::: "memory");
    u32 gen = CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u;
    if (ST->gen != gen) { ST->gen = gen; ST->snapped = 0; }
    ST->last = CYCCNT;
    ST->armed = 1;
}

/* No audio progress on this pass: snapshot once if it has lasted STALL_MS. */
__attribute__((used, retain)) void stall_check(void) {
    if (!ST->armed || ST->snapped || UWTICK < BOOT_GUARD_MS) return;
    u32 dt = CYCCNT - ST->last;
    if (dt < STALL_MS * MS_CYC) return;
    u32 v;
    ST->sgen = ST->gen;
    ST->cycles = dt;
    __asm volatile("mrs %0, primask" : "=r"(v)); ST->primask = v;
    __asm volatile("mrs %0, basepri" : "=r"(v)); ST->basepri = v;
    __asm volatile("mrs %0, faultmask" : "=r"(v)); ST->faultmask = v;
    __asm volatile("mrs %0, msp" : "=r"(v)); ST->msp = v;
    ST->icsr = ICSR;
    for (u32 i = 0; i < 2; i++) {
        ST->iser[i] = NVIC_ISER[1 + i];
        ST->ispr[i] = NVIC_ISPR[1 + i];
        ST->iabr[i] = NVIC_IABR[1 + i];
    }
    ST->lisr = DMA2[0]; ST->hisr = DMA2[1];
    volatile u32 *s5 = DMA2 + (0x10u + 0x18u * 5u) / 4u, *s7 = DMA2 + (0x10u + 0x18u * 7u) / 4u;
    ST->s5cr = s5[0]; ST->s5ndtr = s5[1]; ST->s7cr = s7[0]; ST->s7ndtr = s7[1];
    for (u32 i = 0; i < 2; i++) {            /* SAI status: did its clock stop, or did it flag an error? */
        u32 mux = *(volatile u32 *)(0x40020800u + 4u * (8u + 5u + 2u * i)), id = mux & 0x7Fu, sr = 0xFFFFFFFFu;
        u32 base = id == 87u || id == 88u ? 0x40015800u : id == 89u || id == 90u ? 0x40015C00u
                 : id == 113u || id == 114u ? 0x40016000u : 0u;
        if (base) sr = *(volatile u32 *)(base + ((id & 1u) ? 0x18u : 0x38u));   /* block A (odd id) ASR, B BSR */
        ST->mux[i] = mux; ST->sai_sr[i] = sr;
    }
    ST->magic = STALL7_MAGIC;
    ST->snapped = 1;
    clean_all();
}

/* Stall profile (v3.17): called from the SysTick vector (1 ms, see build.py)
 * with the interrupted exception frame. While the main loop has made no audio
 * progress, sample where the M7 actually is: the stall record above is only
 * taken once the loop runs again, so it cannot say what blocked it. Idle time
 * is counted in ticks, which keep coming even if the core sleeps. */
static void prof_clean(volatile const void *p, u32 n) {
    u32 a = (u32)p & ~31u, end = (u32)p + n;
    __asm volatile("dsb" ::: "memory");
    for (; a < end; a += 32) DCCMVAC = a;
    __asm volatile("dsb" ::: "memory");
}

__attribute__((used, retain)) void prof_sample(const u32 *frame) {
    volatile Prof *p = PROF;
    if (!ST->armed || UWTICK < BOOT_GUARD_MS) return;   /* booting: not a stall */
    if (p->magic != PROF_MAGIC || p->gen != ST->gen) {   /* first use this boot */
        p->magic = 0; p->gen = ST->gen; p->stalls = 0; p->n = 0; p->idle_ms = 0; p->stack_ms = 0;
        p->seen_last = ST->last; p->magic = PROF_MAGIC;
        prof_clean(p, 32);
        return;
    }
    if (ST->last != p->seen_last) {           /* audio progressed since the last tick */
        p->seen_last = ST->last;
        p->idle_ms = 0;
        return;
    }
    u32 idle = ++p->idle_ms;
    if (idle < PROF_START_MS) return;
    if (idle == PROF_START_MS) {              /* a new stall: start over (keep the latest) */
        p->stalls++; p->n = 0; p->stack_ms = 0;
    }
    u32 i = p->n;
    if (i < PROF_SAMPLES && idle >= PROF_START_MS + PROF_EVERY_MS * i) {
        p->pc[i] = frame[6]; p->lr[i] = frame[5]; p->at_ms[i] = (u16)idle;
        p->n = i + 1;
        prof_clean(&p->pc[i], 4); prof_clean(&p->lr[i], 4); prof_clean(&p->at_ms[i], 2);
    }
    if (!p->stack_ms && idle >= PROF_STACK_MS) {
        for (u32 k = 0; k < PROF_STACK_WORDS; k++) p->stack[k] = frame[k];
        p->frame = (u32)frame; p->stack_ms = idle;
        prof_clean(p->stack, sizeof(p->stack));
    }
    prof_clean(p, 32);
}
