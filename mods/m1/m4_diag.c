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

static void zero(void *p, u32 n) { for (u32 i = 0; i < n; i++) ((u8 *)p)[i] = 0; }

typedef struct { char *buf; u32 len; } Out;

static void puts_(Out *o, const char *s) {
    while (*s && o->len < BUF_BYTES - 1) o->buf[o->len++] = *s++;
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

static int new_boot(u32 now) {
    return M4H->magic != DIAG_MAGIC || now < M4H->r[3] || M4H->r[1] == 0;
}

static void init(u32 now) {
    u32 gen = (M4H->magic == DIAG_MAGIC) ? M4H->r[0] + 1u : 1u;
    M4H->magic = 0; /* M7 stops logging while we rebuild */
    u32 cur = BUMP_CUR, end = BUMP_END, buf = 0;
    /* Preflight exactly like Lab: the native allocator crashes on overflow. */
    if (end > cur && end - cur >= BUF_BYTES + 32u) {
        u32 got = FN(0x08104C08, u32 (*)(u32))(BUF_BYTES);
        if (got == cur) buf = got;
    }
    M4H->r[0] = gen;
    M4H->r[1] = buf ? buf : 1u; /* 1 = allocation refused; stay disabled this boot */
    M4H->r[2] = 0;
    M4H->r[3] = now;
    M4H->r[4] = 0;
    M4H->r[5] = now;
    M4H->rd = (M7H->magic == DIAG_MAGIC) ? M7H->wr : 0u;
    if (buf) {
        Out o = {(char *)buf, 0};
        puts_(&o, "BENTO M1 poly-AT v1 + diagnostics (CH10 1.6HA), boot gen ");
        dec(&o, gen);
        puts_(&o, "\r\nfields: ms cyc kind seq lr obj ev0..ev5 extra (hex except ms)\r\n");
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
    }
    return "????";
}

static void flush(void) {
    u32 file[22];
    zero(file, sizeof(file));
    if (FN(0x08120148, u32 (*)(void *, const char *, u32))(file, "\\BENTO_DIAG.log", 10)) return;
    u32 done = 0;
    FN(0x08120604, u32 (*)(void *, const void *, u32, u32 *))(file, (const void *)M4H->r[1], M4H->r[2], &done);
    FN(0x08120974, u32 (*)(void *))(file);
    M4H->r[4] = M4H->r[2];
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
