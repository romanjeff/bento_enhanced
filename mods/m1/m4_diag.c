/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1 diagnostic, M4 side ("black box"): drain the M7 trace, format it as text
 * and write \BENTO_DIAG.log from the UI thread (defaultTask). Never runs in an
 * interrupt: it is reached only through the redirected BL in the defaultTask
 * loop (see build.py).
 *
 * Normal logging: records are formatted as they arrive; the file is written
 * (created on the first write of a boot, appended afterwards) once the pads have
 * been idle for IDLE_MS.
 *
 * Crash capture: every poll compares the stock crash counter (RTC backup
 * register 24, written by the M7/M4 fault handlers) with the value at boot, and
 * watches the M7 heartbeat. On a new crash record, or the heartbeat stopping
 * while the UI keeps running (an M7-only crash), it immediately appends the
 * crash record, the M7 counters (incl. lowest stack pointers) and the last
 * trace records, and writes the file without waiting for idle.
 *
 * Post-mortem: our RAM slice survives a reset, so at boot the previous boot's
 * last trace records and counters are written into the header.
 *
 * File I/O uses the stock FatFS entry points Lab's K6/K7 builds used on
 * hardware (FIL = 0x58 bytes); append mode relies on stock f_open honouring the
 * seek-end bit.
 *
 * M4-owned state: M4 header r[0] boot generation, r[1] buffer, r[2] length,
 * r[3] last poll ms, r[4] saved length, r[5] last record ms; config line r[1]
 * crash count at boot, r[2] last heartbeat, r[3] ms of last heartbeat change,
 * r[4] flags (bit0 heartbeat seen moving, bit1 stall reported).
 */
#include "diag_shared.h"

#define FN(a, t) ((t)((a) | 1u))
#define TICK_MS (*(volatile u32 *)0x3001FE2Cu)     /* native HAL tick (Lab-verified) */
#define BUMP_CUR (*(volatile u32 *)0x3000004Cu)    /* native UI bump allocator */
#define BUMP_END (*(volatile u32 *)0x30000048u)
#define BUF_BYTES 0x40000u                          /* 256 KB session log */
#define IDLE_MS 1500u
#define STALL_MS 3000u
#define BKP ((volatile u32 *)0x58004050u)           /* RTC_BKP0R..BKP31R: stock crash record */

typedef void (*orig_fn)(u32, u32);

static void zero(void *p, u32 n) { for (u32 i = 0; i < n; i++) ((volatile u8 *)p)[i] = 0; }

typedef struct { char *buf; u32 len; } Out;

#define SCRATCH_BYTES 512u /* tail of the log buffer: file-copy scratch, never text */

static void puts_(Out *o, const char *s) {
    while (*s && o->len < BUF_BYTES - SCRATCH_BYTES - 64u) o->buf[o->len++] = *s++;
}
static void hex(Out *o, u32 v, u32 digits) {
    char t[9];
    for (u32 i = 0; i < digits; i++) t[i] = "0123456789abcdef"[(v >> (4 * (digits - 1 - i))) & 15];
    t[digits] = 0;
    puts_(o, t);
}
static void dec(Out *o, u32 v) {
    char t[11]; u32 n = 0;
    do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v && n < 10);
    char r[11]; for (u32 i = 0; i < n; i++) r[i] = t[n - 1 - i];
    r[n] = 0; puts_(o, r);
}

/* Stock FatFS (same entry points Lab's K6/K7 diagnostics used). */
#define F_OPEN(f, p, m) FN(0x08120148, u32 (*)(void *, const char *, u32))((f), (p), (m))
#define F_READ(f, b, n, d) FN(0x08120464, u32 (*)(void *, void *, u32, u32 *))((f), (b), (n), (d))
#define F_WRITE(f, b, n, d) FN(0x08120604, u32 (*)(void *, const void *, u32, u32 *))((f), (b), (n), (d))
#define F_CLOSE(f) FN(0x08120974, u32 (*)(void *))((f))
#define FA_READ 0x01u
#define FA_CREATE_WRITE 0x0Au /* FA_CREATE_ALWAYS | FA_WRITE */
#define FA_APPEND_WRITE 0x32u /* FA_OPEN_APPEND | FA_WRITE: stock f_open honours the seek-end bit */
static const char LOG[] = "\\BENTO_DIAG.log";
static const char PREV[] = "\\BENTO_DIAG_prev.log";

/* Keep the previous boot's log. Done at the first real write of a boot (the
 * card is certainly mounted by then; at the first UI-loop pass it may not be). */
static void preserve_previous(u8 *scratch) {
    u32 src[22], dst[22];
    zero(src, sizeof(src));
    zero(dst, sizeof(dst));
    if (F_OPEN(src, LOG, FA_READ)) return; /* no log yet */
    if (!F_OPEN(dst, PREV, FA_CREATE_WRITE)) {
        for (u32 guard = 0; guard < 1024; guard++) { /* logs are <= 256 KB */
            u32 got = 0, put = 0;
            if (F_READ(src, scratch, SCRATCH_BYTES, &got) || !got) break;
            if (F_WRITE(dst, scratch, got, &put) || put != got) break;
        }
        F_CLOSE(dst);
    }
    F_CLOSE(src);
}

/* Crash history: one line per boot (at its first log write) and one per crash
 * record that appears mid-session, appended forever to BENTO_CRASHES.log. The
 * session log only keeps two boots; this keeps every reset attributable. */
/* RTC wall clock (BCD). Reading TR locks DR's shadow until DR is read. */
#define RTC_TR (*(volatile u32 *)0x58004000u)
#define RTC_DR (*(volatile u32 *)0x58004004u)
static void rtc_print(Out *o, u32 tr, u32 dr) {
    hex(o, dr & 0xFF1F3Fu, 6); puts_(o, "-"); hex(o, tr & 0x3F7F7Fu, 6);   /* yymmdd-hhmmss */
}
static void rtc_now(Out *o) { u32 tr = RTC_TR, dr = RTC_DR; rtc_print(o, tr, dr); }
/* The visible label (stock's version string, patched by build.py). */
static void version(Out *o) {
    char v[9]; const volatile char *p = (const volatile char *)0x0818BAD4u;
    for (u32 i = 0; i < 8; i++) v[i] = p[i];
    v[8] = 0; puts_(o, v);
}
static const char HIST[] = "\\BENTO_CRASHES.log";
static void history(const char *why) {
    Out o = {(char *)M4H->r[1] + BUF_BYTES - SCRATCH_BYTES, 0};   /* scratch: free between copies */
    puts_(&o, why);
    puts_(&o, " fw="); version(&o);
    puts_(&o, " rtc="); rtc_now(&o);
    puts_(&o, " gen="); dec(&o, M4H->r[0]);
    puts_(&o, " ms="); dec(&o, TICK_MS);
    puts_(&o, " count="); dec(&o, BKP[24]);
    puts_(&o, " cause="); hex(&o, BKP[25], 8);
    puts_(&o, " pc="); hex(&o, BKP[31], 8);
    puts_(&o, " core="); hex(&o, BKP[27], 8);
    puts_(&o, " lr="); hex(&o, BKP[11], 8);
    puts_(&o, " frame_pc="); hex(&o, BKP[12], 8);
    puts_(&o, "\r\n");
    u32 file[22], done = 0;
    zero(file, sizeof(file));
    if (F_OPEN(file, HIST, FA_APPEND_WRITE)) return;
    F_WRITE(file, o.buf, o.len, &done);
    F_CLOSE(file);
}

/* Write only what is new since the last flush. */
static void flush(void) {
    u32 saved = M4H->r[4], len = M4H->r[2];
    if (len <= saved) return;
    if (!saved) {
        preserve_previous((u8 *)M4H->r[1] + BUF_BYTES - SCRATCH_BYTES);
        history("boot");
    }
    u32 file[22];
    zero(file, sizeof(file));
    if (F_OPEN(file, LOG, saved ? FA_APPEND_WRITE : FA_CREATE_WRITE)) return;
    u32 done = 0;
    F_WRITE(file, (const char *)M4H->r[1] + saved, len - saved, &done);
    F_CLOSE(file);
    M4H->r[4] = len;
}

static const char *kind_name(u32 k) {
    switch (k) {
    case K_PAD: return "PAD ";
    case K_SEND: return "SEND";
    case K_RING: return "RING";
    case K_CONSUME: return "CONS";
    case K_VOICE: return "VOIC";
    }
    return "????";
}

static void rec_line(Out *o, u32 ms, volatile Rec *r) {
    dec(o, ms); puts_(o, " "); hex(o, r->cyc, 8); puts_(o, " ");
    puts_(o, kind_name(r->kind & 0xFFFFu)); puts_(o, " "); hex(o, r->kind >> 16, 4);
    puts_(o, " lr="); hex(o, r->lr, 8);
    puts_(o, " obj="); hex(o, r->obj, 8);
    puts_(o, " ev=");
    for (u32 i = 0; i < 6; i++) { hex(o, r->ev[i], 8); puts_(o, i < 5 ? "," : ""); }
    puts_(o, " x="); hex(o, r->extra, 8);
    puts_(o, "\r\n");
}

static void crash_record(Out *o) {
    puts_(o, "stock crash record: count="); dec(o, BKP[24]);
    puts_(o, " cause="); hex(o, BKP[25], 8);
    puts_(o, " pc="); hex(o, BKP[31], 8);
    puts_(o, " core="); hex(o, BKP[27], 8);
    puts_(o, " | frame r0-r3,r12,lr,pc,xpsr (bkp6-13):");
    for (u32 i = 6; i <= 13; i++) { puts_(o, " "); hex(o, BKP[i], 8); }
    puts_(o, "\r\nrtc_bkp0..31:");
    for (u32 i = 0; i < 32; i++) { puts_(o, " "); hex(o, BKP[i], 8); }
    puts_(o, "\r\n");
}

/* Audio DMA stall monitor. The M7 main audio loop (FUN_0808DF00) refreshes the
 * window watchdog only while the audio DMA advances; stock checks DMA2 streams
 * 5 and 7 there. Stock's crash record (cause 5) cannot say why audio stopped,
 * so we sample those streams from the M4 every UI-loop pass: if a stream seen
 * running stops moving (NDTR unchanged >= STALL_DMA_MS, or EN cleared), we
 * snapshot its registers into M4-only RAM that survives the coming reset, log
 * an alarm and a history line, and print the snapshot again at the next boot. */
#define DMA2_BASE 0x40020400u
#define STALL_DMA_MS 30u
typedef struct {
    u32 magic, gen, ndtr[2], since[2], seen, reported;
    u32 snap_magic, snap_gen, snap_ms, snap_why, lisr, hisr;
    u32 regs[2][6];      /* CR, NDTR, PAR, M0AR, M1AR, FCR */
    u32 periph[2][2];    /* if PAR is a SAI DR: SR (DR-8), CR1 (DR-0x1C) */
    u32 rtc_tr, rtc_dr;  /* RTC at the last UI-loop pass (survives the reset) */
} AudioMon;
#define AMON ((volatile AudioMon *)0x3003C000u)   /* M4-only; start of our D2 slice (v3.16) */
#define AMON_MAGIC 0x314E4F4Du /* "MON1" */
#define SNAP_MAGIC 0x50414E53u /* "SNAP" */
_Static_assert(0x3003C000u + sizeof(AudioMon) <= 0x3003C080u, "audio monitor overlaps the M7 stall record");
static const u32 STREAMS[2] = {5u, 7u};
static volatile u32 *dma_stream(u32 n) { return (volatile u32 *)(DMA2_BASE + 0x10u + 0x18u * n); }

static void snap_print(Out *o, const char *title) {
    puts_(o, title); puts_(o, " at ms "); dec(o, AMON->snap_ms);
    puts_(o, " gen "); dec(o, AMON->snap_gen);
    puts_(o, ": stream "); dec(o, AMON->snap_why & 0xFFu);
    puts_(o, (AMON->snap_why & 0x100u) ? " stopped advancing" : " disabled");
    puts_(o, " | DMA2 LISR="); hex(o, AMON->lisr, 8); puts_(o, " HISR="); hex(o, AMON->hisr, 8);
    for (u32 i = 0; i < 2; i++) {
        puts_(o, "\r\n  S"); dec(o, STREAMS[i]); puts_(o, " CR,NDTR,PAR,M0AR,M1AR,FCR =");
        for (u32 j = 0; j < 6; j++) { puts_(o, " "); hex(o, AMON->regs[i][j], 8); }
        puts_(o, " | periph SR="); hex(o, AMON->periph[i][0], 8); puts_(o, " CR1="); hex(o, AMON->periph[i][1], 8);
    }
    puts_(o, "\r\n");
}

/* Returns 1 when a stall was detected on this pass. */
static int audio_watch(Out *o, u32 now) {
    if (AMON->magic != AMON_MAGIC || AMON->gen != M4H->r[0]) {
        u32 keep_magic = AMON->snap_magic;      /* the snapshot must survive into the next boot */
        for (u32 i = 0; i < 8; i++) ((volatile u32 *)AMON)[i] = 0;
        AMON->snap_magic = keep_magic;
        AMON->gen = M4H->r[0];
        AMON->magic = AMON_MAGIC;
    }
    if (AMON->reported) return 0;
    for (u32 i = 0; i < 2; i++) {
        volatile u32 *s = dma_stream(STREAMS[i]);
        u32 cr = s[0], nd = s[1], bit = 1u << i, why = 0;
        if (cr & 1u) {
            if (!(AMON->seen & bit) || nd != AMON->ndtr[i]) { AMON->ndtr[i] = nd; AMON->since[i] = now; AMON->seen |= bit; }
            else if (now - AMON->since[i] >= STALL_DMA_MS) why = 0x100u | STREAMS[i];
        } else if (AMON->seen & bit) {
            why = STREAMS[i];
        }
        if (!why) continue;
        AMON->snap_ms = now; AMON->snap_gen = M4H->r[0]; AMON->snap_why = why;
        AMON->lisr = *(volatile u32 *)DMA2_BASE; AMON->hisr = *(volatile u32 *)(DMA2_BASE + 4u);
        for (u32 k = 0; k < 2; k++) {
            volatile u32 *t = dma_stream(STREAMS[k]);
            for (u32 j = 0; j < 6; j++) AMON->regs[k][j] = t[j];
            /* Only read status from a SAI block data register (A at +0x20, B at
             * +0x40 of SAI1..4): SR and CR1 reads have no side effects there. */
            u32 par = AMON->regs[k][2], blk = par & 0xFFFu, sai = par & ~0x3FFu;
            int periph = (blk & 0x3FFu) == 0x20u || (blk & 0x3FFu) == 0x40u;
            periph = periph && (sai == 0x40015800u || sai == 0x40015C00u || sai == 0x40016000u || sai == 0x58005400u);
            AMON->periph[k][0] = periph ? *(volatile u32 *)(par - 8u) : 0u;
            AMON->periph[k][1] = periph ? *(volatile u32 *)(par - 0x1Cu) : 0u;
        }
        AMON->snap_magic = SNAP_MAGIC;
        AMON->reported = 1;
        puts_(o, "!!! AUDIO DMA STALL at ms "); dec(o, now); puts_(o, "\r\n");
        snap_print(o, "audio stall");
        return 1;
    }
    return 0;
}

/* Native bump allocator (0x08104C08): advance-only, so free space now is this
 * boot's minimum. Sizing data for M2's pressure-curve pool. */
/* D2 canary (v3.16): our slice moved into D2 SRAM2 that no stock linker
 * section uses. Fill the 1.5 KB above it once with a known pattern (it
 * survives resets) and report any word that changes: nonzero = something in
 * stock uses this area after all. */
#define CANARY ((volatile u32 *)0x3003CA00u)
#define CANARY_WORDS ((0x3003D000u - 0x3003CA00u) / 4u)
#define CANARY_MAGIC 0x59524E43u /* "CNRY" */
static u32 canary_word(u32 i) { return 0xA5C30000u ^ (i * 0x9E3779B1u); }
static void canary_arm(void) {
    if (CANARY[0] == CANARY_MAGIC) return;
    for (u32 i = 1; i < CANARY_WORDS; i++) CANARY[i] = canary_word(i);
    CANARY[0] = CANARY_MAGIC;
}
static void ram(Out *o) {
    u32 cur = BUMP_CUR, end = BUMP_END, bad = 0;
    puts_(o, " | ram bump cur="); hex(o, cur, 8);
    puts_(o, " end="); hex(o, end, 8);
    puts_(o, " free="); dec(o, end > cur ? end - cur : 0u);
    if (CANARY[0] != CANARY_MAGIC) bad = CANARY_WORDS;
    else for (u32 i = 1; i < CANARY_WORDS; i++) bad += CANARY[i] != canary_word(i);
    puts_(o, " d2 canary changed="); dec(o, bad);
}

static void counters(Out *o) {
    puts_(o, "m7 dropped="); dec(o, M7H->dropped);
    puts_(o, " | poly sent="); dec(o, M7H->r[0]);
    puts_(o, " budget_drops="); dec(o, M7H->r[1]);
    puts_(o, " stolen="); dec(o, M7H->r[2] & 0xFFFFu);
    puts_(o, " retrig="); dec(o, M7H->r[2] >> 16);
    puts_(o, " swallowed="); dec(o, M7H->r[3]);
    puts_(o, " | midi ring max_fill="); dec(o, RINGSTAT->max_fill);
    puts_(o, " overflows="); dec(o, RINGSTAT->overflows);
    puts_(o, " | m7 stack min_msp="); hex(o, RINGSTAT->min_msp, 8);
    if (RINGSTAT->min_msp >= M7_STACK_BOTTOM && RINGSTAT->min_msp < M7_STACK_TOP) {
        puts_(o, " ("); dec(o, RINGSTAT->min_msp - M7_STACK_BOTTOM); puts_(o, " B above bottom)");
    }
    puts_(o, " min_sp="); hex(o, RINGSTAT->min_sp, 8);
    puts_(o, " heartbeat="); dec(o, RINGSTAT->heartbeat);
    ram(o);
    if (STRIKE_SET->magic == STRIKE_SET_MAGIC) {   /* Strike Vel header button (m4_step.c) */
        puts_(o, " | strike btn calls="); dec(o, STRIKE_SET->r[0]);
        puts_(o, " drum="); dec(o, STRIKE_SET->r[1] & 1u);
        puts_(o, " slot="); hex(o, (STRIKE_SET->r[1] >> 4) & 0xFu, 1);
        puts_(o, " ins="); dec(o, (STRIKE_SET->r[1] >> 8) & 1u);
        puts_(o, " off="); dec(o, STRIKE_SET->off);
    }
    puts_(o, " | audio dma seen running: S5="); dec(o, AMON->seen & 1u);
    puts_(o, " S7="); dec(o, (AMON->seen >> 1) & 1u);
}

/* The last DIAG_RECORDS trace records, oldest first, regardless of whether they
 * were already logged (after a crash these are the final moments). */
static void ring_tail(Out *o, u32 ms) {
    if (M7H->magic != DIAG_MAGIC) { puts_(o, "(no M7 trace in RAM)\r\n"); return; }
    u32 wr = M7H->wr;
    for (u32 i = DIAG_RECORDS; i > 0; i--) {
        u32 s = wr - i;
        volatile Rec *r = &RECS[s % DIAG_RECORDS];
        if ((r->kind >> 16) != (s & 0xFFFFu)) continue; /* slot not written in this sequence */
        puts_(o, "  T "); rec_line(o, ms, r);
    }
}

/* M7 stall record (m7_stall.c), written just before a watchdog reset. */
typedef struct {
    u32 last, armed, gen, snapped, pad_[4];   /* M7 tracking line */
    u32 magic, sgen, cycles, primask, basepri, faultmask, icsr, msp;
    u32 iser[2], ispr[2], iabr[2];
    u32 lisr, hisr, s5cr, s5ndtr, s7cr, s7ndtr;
    u32 mux[2], sai_sr[2];
} Stall7;
#define ST7 ((volatile Stall7 *)0x3003C080u)
#define STALL7_MAGIC 0x38544C53u /* "SLT8": layout grew (SAI status) */
static void stall7_print(Out *o) {
    puts_(o, "M7 audio stall record (from RAM): gen "); dec(o, ST7->sgen);
    puts_(o, ", no audio progress for "); dec(o, ST7->cycles / 480000u); puts_(o, " ms");
    puts_(o, "\r\n  primask="); hex(o, ST7->primask, 1); puts_(o, " basepri="); hex(o, ST7->basepri, 2);
    puts_(o, " faultmask="); hex(o, ST7->faultmask, 1); puts_(o, " icsr="); hex(o, ST7->icsr, 8);
    puts_(o, " msp="); hex(o, ST7->msp, 8);
    puts_(o, "\r\n  NVIC irq32-63 en/pend/act="); hex(o, ST7->iser[0], 8); puts_(o, "/"); hex(o, ST7->ispr[0], 8);
    puts_(o, "/"); hex(o, ST7->iabr[0], 8);
    puts_(o, " irq64-95 en/pend/act="); hex(o, ST7->iser[1], 8); puts_(o, "/"); hex(o, ST7->ispr[1], 8);
    puts_(o, "/"); hex(o, ST7->iabr[1], 8);
    puts_(o, " (DMA2 S5=irq68 bit4, S7=irq70 bit6)");
    puts_(o, "\r\n  DMA2 LISR="); hex(o, ST7->lisr, 8); puts_(o, " HISR="); hex(o, ST7->hisr, 8);
    puts_(o, " S5 CR/NDTR="); hex(o, ST7->s5cr, 8); puts_(o, "/"); hex(o, ST7->s5ndtr, 4);
    puts_(o, " S7 CR/NDTR="); hex(o, ST7->s7cr, 8); puts_(o, "/"); hex(o, ST7->s7ndtr, 4);
    puts_(o, "\r\n  DMAMUX S5/S7="); hex(o, ST7->mux[0], 8); puts_(o, "/"); hex(o, ST7->mux[1], 8);
    puts_(o, " SAI SR S5/S7="); hex(o, ST7->sai_sr[0], 8); puts_(o, "/"); hex(o, ST7->sai_sr[1], 8);
    puts_(o, " (SR bit0 OVRUDR, 4 CNRDY, 5 AFSDET, 6 LFSDET)");
    puts_(o, "\r\n");
}

static void init(u32 now) {
    u32 gen = (M4H->magic == DIAG_MAGIC) ? M4H->r[0] + 1u : 1u;
    u32 cur = BUMP_CUR, end = BUMP_END, buf = 0, region = 0;
    /* Preflight exactly like Lab: the native allocator crashes on overflow.
     * One check covers both allocations (each is rounded up to 32 bytes). */
    if (end > cur && end - cur >= BUF_BYTES + M1_REGION_BYTES + 64u) {
        u32 got = FN(0x08104C08, u32 (*)(u32))(BUF_BYTES);
        if (got == cur) buf = got;
        cur = BUMP_CUR;
        got = FN(0x08104C08, u32 (*)(u32))(M1_REGION_BYTES);
        if (got == cur) region = got;
    }
    u32 len = 0;
    if (buf) {
        Out o = {(char *)buf, 0};
        puts_(&o, "BENTO black box, firmware "); version(&o);
        puts_(&o, ", boot gen "); dec(&o, gen);
        puts_(&o, ", rtc "); rtc_now(&o);
        if (M4H->magic == DIAG_MAGIC || M4H->r[0]) {
            puts_(&o, "\r\nprevious boot: last UI-loop pass at ms "); dec(&o, M4H->r[3]);
            puts_(&o, ", rtc "); rtc_print(&o, AMON->rtc_tr, AMON->rtc_dr);
        }
        puts_(&o, "\r\nat boot (after the black box's own buffers):"); ram(&o);   /* canary as found at boot */
        canary_arm();
        puts_(&o, "\r\n");
        crash_record(&o);
        if (AMON->snap_magic == SNAP_MAGIC) {     /* survives the reset the stall caused */
            snap_print(&o, "audio stall snapshot (from RAM)");
            AMON->snap_magic = 0;
        }
        if (ST7->magic == STALL7_MAGIC) {
            stall7_print(&o);
            ST7->magic = 0;              /* the M7 never rewrites magic until its next stall */
        }
        /* Post-mortem: our RAM slice survives a reset; before anything resets it,
         * write the previous boot's last records and counters. */
        puts_(&o, "previous boot (from RAM): ");
        counters(&o);
        puts_(&o, "\r\n");
        ring_tail(&o, 0);
        puts_(&o, "fields: ms cyc kind seq lr obj ev0..ev5 extra (hex except ms)\r\n"
                  "VOIC records: lr 1 = retrigger, 2 = steal, 3 = swallowed native note-off; obj = track; x = note\r\n");
        len = o.len;
    }
    M4H->magic = 0; /* M7 stops logging while we rebuild */
    CFG->magic = 0; /* ...and stops using the M1 region */
    /* Hand the region to the M7; it initialises and owns it from here on. */
    CFG->region = region;
    CFG->region_bytes = region ? M1_REGION_BYTES : 0u;
    CFG->r[0] = gen;
    CFG->r[1] = BKP[24];          /* crash count at boot: a change = a new crash */
    CFG->r[2] = RINGSTAT->heartbeat;
    CFG->r[3] = now;
    CFG->r[4] = 0;
    CFG->magic = DIAG_MAGIC;
    M4H->r[0] = gen;
    M4H->r[1] = buf ? buf : 1u;   /* 1 = allocation refused; stay disabled this boot */
    M4H->r[2] = len;
    M4H->r[3] = now;
    M4H->r[4] = 0;
    M4H->r[5] = now;
    M4H->rd = (M7H->magic == DIAG_MAGIC) ? M7H->wr : 0u;
    M4H->magic = DIAG_MAGIC;      /* publish last */
}

static void alarm(Out *o, u32 now, const char *why) {
    puts_(o, "!!! "); puts_(o, why); puts_(o, " at ms "); dec(o, now); puts_(o, "\r\n");
    crash_record(o);
    counters(o);
    puts_(o, "\r\nlast trace records:\r\n");
    ring_tail(o, now);
}

static void poll(void) {
    u32 now = TICK_MS;
    if (M4H->magic != DIAG_MAGIC || now < M4H->r[3] || M4H->r[1] == 0) init(now); /* new boot */
    M4H->r[3] = now;
    if (M4H->r[1] <= 1u) return;
    Out o = {(char *)M4H->r[1], M4H->r[2]};
    int urgent = 0, crashed = 0;

    /* Watch 1: a new stock crash record while we are running (e.g. the M7
     * faulted and parked while the UI stays alive). */
    if (BKP[24] != CFG->r[1]) {
        CFG->r[1] = BKP[24];
        alarm(&o, now, "NEW CRASH RECORD");
        urgent = crashed = 1;
    }
    /* Watch 2: the M7 heartbeat (bumped every audio block) stops. */
    u32 hb = RINGSTAT->heartbeat;
    if (hb != CFG->r[2]) {
        CFG->r[2] = hb; CFG->r[3] = now; CFG->r[4] |= 1u;
    } else if ((CFG->r[4] & 3u) == 1u && now - CFG->r[3] >= STALL_MS) {
        CFG->r[4] |= 2u;
        alarm(&o, now, "M7 HEARTBEAT STOPPED (audio core not running)");
        urgent = 1;
    }
    /* Watch 3: the audio DMA stops (the M7 watchdog follows ~0.28 s later). */
    int stalled = audio_watch(&o, now);
    AMON->rtc_tr = RTC_TR; AMON->rtc_dr = RTC_DR;
    if (stalled) urgent = 1;

    u32 n = 0;
    while (M4H->rd != M7H->wr && n < DIAG_RECORDS) {
        u32 rd = M4H->rd;
        volatile Rec *r = &RECS[rd % DIAG_RECORDS];
        if ((r->kind >> 16) != (rd & 0xFFFFu)) puts_(&o, "!SEQ ");
        rec_line(&o, now, r);
        M4H->rd = rd + 1u;
        M4H->r[5] = now;
        n++;
    }
    M4H->r[2] = o.len;
    if (urgent || (o.len > M4H->r[4] && now - M4H->r[5] >= IDLE_MS)) {
        puts_(&o, "# "); counters(&o);
        puts_(&o, " at ms "); dec(&o, now); puts_(&o, "\r\n");
        M4H->r[2] = o.len;
        flush();
        if (crashed) history("live");
        if (stalled) history("stall");
    }
}

/* Replaces `bl 0x08101c78` at 0x08114b10 in the defaultTask loop. */
__attribute__((used, retain)) void m4_tick(u32 a, u32 b) {
    FN(0x08101C78, orig_fn)(a, b);
    poll();
}
