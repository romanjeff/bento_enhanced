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

/* Write only what is new since the last flush. */
static void flush(void) {
    u32 saved = M4H->r[4], len = M4H->r[2];
    if (len <= saved) return;
    if (!saved) preserve_previous((u8 *)M4H->r[1] + BUF_BYTES - SCRATCH_BYTES);
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
        puts_(&o, "BENTO M1 black box (stock 1.6.1 + poly-AT, conditions, inspect), boot gen ");
        dec(&o, gen);
        puts_(&o, "\r\n");
        crash_record(&o);
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
    int urgent = 0;

    /* Watch 1: a new stock crash record while we are running (e.g. the M7
     * faulted and parked while the UI stays alive). */
    if (BKP[24] != CFG->r[1]) {
        CFG->r[1] = BKP[24];
        alarm(&o, now, "NEW CRASH RECORD");
        urgent = 1;
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
    }
}

/* Replaces `bl 0x08101c78` at 0x08114b10 in the defaultTask loop. */
__attribute__((used, retain)) void m4_tick(u32 a, u32 b) {
    FN(0x08101C78, orig_fn)(a, b);
    poll();
}
