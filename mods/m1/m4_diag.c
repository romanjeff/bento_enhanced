/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1 diagnostic, M4 side: drain the M7 log, format it as text, and write
 * \BENTO_DIAG.log from the UI thread (defaultTask) once the pads have been
 * idle for IDLE_MS. Never runs in an interrupt: it is reached only through
 * the redirected BL in the defaultTask loop (see build.py).
 *
 * File I/O uses the stock FatFS entry points Lab's K6/K7 builds used on
 * hardware (f_open 0x08120148, f_write 0x08120604, f_close 0x08120974;
 * FIL = 0x58 bytes). The whole session buffer is rewritten each flush, so no
 * seek/append support is needed.
 *
 * M4-owned state lives in the M4 header line: r[0] boot generation,
 * r[1] buffer, r[2] length, r[3] last poll ms, r[4] saved length,
 * r[5] last record ms.
 */
#include "diag_shared.h"

#define FN(a, t) ((t)((a) | 1u))
#define TICK_MS (*(volatile u32 *)0x3001FE2Cu)     /* native HAL tick (Lab-verified) */
#define BUMP_CUR (*(volatile u32 *)0x3000004Cu)    /* native UI bump allocator */
#define BUMP_END (*(volatile u32 *)0x30000048u)
#define BUF_BYTES 0x10000u
#define IDLE_MS 1500u

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

/* Keep the previous boot's log: after a crash/restart, that is the evidence. */
static void preserve_previous(u8 *scratch) {
    u32 src[22], dst[22];
    zero(src, sizeof(src));
    zero(dst, sizeof(dst));
    if (F_OPEN(src, LOG, FA_READ)) return; /* no log yet */
    if (!F_OPEN(dst, PREV, FA_CREATE_WRITE)) {
        for (u32 guard = 0; guard < 256; guard++) { /* logs are <= 64 KB */
            u32 got = 0, put = 0;
            if (F_READ(src, scratch, SCRATCH_BYTES, &got) || !got) break;
            if (F_WRITE(dst, scratch, got, &put) || put != got) break;
        }
        F_CLOSE(dst);
    }
    F_CLOSE(src);
}

static int new_boot(u32 now) {
    return M4H->magic != DIAG_MAGIC || now < M4H->r[3] || M4H->r[1] == 0;
}

static void init(u32 now) {
    u32 gen = (M4H->magic == DIAG_MAGIC) ? M4H->r[0] + 1u : 1u;
    M4H->magic = 0; /* M7 stops logging while we rebuild */
    CFG->magic = 0; /* ...and stops using the M1 region */
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
    /* Hand the region to the M7; it initialises and owns it from here on. */
    CFG->region = region;
    CFG->region_bytes = region ? M1_REGION_BYTES : 0u;
    CFG->r[0] = gen;
    CFG->magic = DIAG_MAGIC;
    M4H->r[0] = gen;
    M4H->r[1] = buf ? buf : 1u; /* 1 = allocation refused; stay disabled this boot */
    M4H->r[2] = 0;
    M4H->r[3] = now;
    M4H->r[4] = 0;
    M4H->r[5] = now;
    M4H->rd = (M7H->magic == DIAG_MAGIC) ? M7H->wr : 0u;
    if (buf) {
        Out o = {(char *)buf, 0};
        puts_(&o, "BENTO M1b poly-AT + poly cap v2.1 + diagnostics (CH10 1.6HA), boot gen ");
        dec(&o, gen);
        /* Stock's crash record: RTC backup registers, written by the M7 fault
         * handlers (UsageFault vector 6, WWDG IRQ 0) and kept across reset. */
        volatile u32 *bkp = (volatile u32 *)0x58004050u; /* RTC_BKP0R..BKP31R */
        puts_(&o, "\r\nstock crash record: count="); dec(&o, bkp[24]);
        puts_(&o, " cause="); hex(&o, bkp[25], 8);
        puts_(&o, " pc="); hex(&o, bkp[31], 8);
        puts_(&o, " core="); hex(&o, bkp[27], 8);
        puts_(&o, " tick="); hex(&o, bkp[26], 8);
        puts_(&o, " x23="); hex(&o, bkp[23], 8);
        puts_(&o, " msp="); hex(&o, bkp[8], 8);
        puts_(&o, " psp="); hex(&o, bkp[9], 8);
        puts_(&o, "\r\nrtc_bkp0..31:");
        for (u32 i = 0; i < 32; i++) { puts_(&o, " "); hex(&o, bkp[i], 8); }
        preserve_previous((u8 *)buf + BUF_BYTES - SCRATCH_BYTES);
        puts_(&o, "\r\nfields: ms cyc kind seq lr obj ev0..ev5 extra (hex except ms)\r\n"
                  "VOIC records: lr 1 = retrigger, 2 = steal, 3 = swallowed native note-off; obj = track; x = note\r\n");
        M4H->r[2] = o.len;
    }
    M4H->magic = DIAG_MAGIC; /* publish last */
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

/* Write only what is new since the last flush: the first write this boot
 * creates the file, later ones append. No full rewrite of up to 64 KB on the
 * UI thread. */
static void flush(void) {
    u32 saved = M4H->r[4], len = M4H->r[2];
    if (len <= saved) return;
    u32 file[22];
    zero(file, sizeof(file));
    if (F_OPEN(file, LOG, saved ? FA_APPEND_WRITE : FA_CREATE_WRITE)) return;
    u32 done = 0;
    F_WRITE(file, (const char *)M4H->r[1] + saved, len - saved, &done);
    F_CLOSE(file);
    M4H->r[4] = len;
}

static void poll(void) {
    u32 now = TICK_MS;
    if (new_boot(now)) init(now);
    M4H->r[3] = now;
    if (M4H->r[1] <= 1u) return;
    Out o = {(char *)M4H->r[1], M4H->r[2]};
    u32 n = 0;
    while (M4H->rd != M7H->wr && n < DIAG_RECORDS) {
        u32 rd = M4H->rd;
        volatile Rec *r = &RECS[rd % DIAG_RECORDS];
        u32 kind = r->kind & 0xFFFFu, seq = r->kind >> 16;
        dec(&o, now); puts_(&o, " "); hex(&o, r->cyc, 8); puts_(&o, " ");
        puts_(&o, kind_name(kind)); puts_(&o, " "); hex(&o, seq, 4);
        if (seq != (rd & 0xFFFFu)) puts_(&o, "!SEQ");
        puts_(&o, " lr="); hex(&o, r->lr, 8);
        puts_(&o, " obj="); hex(&o, r->obj, 8);
        puts_(&o, " ev=");
        for (u32 i = 0; i < 6; i++) { hex(&o, r->ev[i], 8); puts_(&o, i < 5 ? "," : ""); }
        puts_(&o, " x="); hex(&o, r->extra, 8);
        puts_(&o, "\r\n");
        M4H->rd = rd + 1u;
        M4H->r[5] = now;
        n++;
    }
    M4H->r[2] = o.len;
    if (o.len > M4H->r[4] && now - M4H->r[5] >= IDLE_MS) {
        /* Append the M7's drop counters to the saved copy so losses are visible. */
        Out t = o;
        puts_(&t, "# m7 dropped="); dec(&t, M7H->dropped);
        puts_(&t, " dedupe_full="); dec(&t, M7H->dedupe_full);
        puts_(&t, " | poly sent="); dec(&t, M7H->r[0]);
        puts_(&t, " budget_drops="); dec(&t, M7H->r[1]);
        puts_(&t, " stolen="); dec(&t, M7H->r[2] & 0xFFFFu);
        puts_(&t, " retrig="); dec(&t, M7H->r[2] >> 16);
        puts_(&t, " swallowed="); dec(&t, M7H->r[3]);
        puts_(&t, " | midi ring max_fill="); dec(&t, RINGSTAT->max_fill);
        puts_(&t, " overflows="); dec(&t, RINGSTAT->overflows);
        puts_(&t, " at ms "); dec(&t, now); puts_(&t, "\r\n");
        M4H->r[2] = t.len;
        flush();
    }
}

/* Replaces `bl 0x08101c78` at 0x08114b10 in the defaultTask loop. */
__attribute__((used, retain)) void m4_tick(u32 a, u32 b) {
    FN(0x08101C78, orig_fn)(a, b);
    poll();
}
