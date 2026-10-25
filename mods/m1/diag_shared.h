/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1 diagnostic: shared M7 -> M4 log layout (stock 1.6.1 + Lab CH10 1.6HA).
 *
 * Purpose: find which code turns a pad press on an External track into a
 * MIDI note-on, by logging every hop: pad event -> queue -> consumer ->
 * MIDI-out event -> cross-core MIDI ring. The M7 only writes RAM; the M4
 * formats and writes \BENTO_DIAG.log on its UI thread when the pads are idle.
 *
 * RAM: 0x24048500..0x24048A00 is unclaimed by every CH10 source (Lab's
 * picker table ends 0x240484C0, condition counters start 0x24048A60),
 * outside the native heap (Lab moved its end to 0x24040000) and inside the
 * M4's MPU window (0x24040000..0x24080000).
 *
 * Ownership, one writer per 32-byte cache line:
 *   0x24048500 M7 header   (M7 writes, M4 reads)
 *   0x24048520 M4 header   (M4 writes, M7 never reads)
 *   0x24048540 M7 private  diag: dedupe table 16 x 8 B
 *   0x240485C0 M7 private  diag: flags, generation
 *   0x240485E0 M7 private  poly AT: rate-limit table + budget (224 B)
 *   0x240486C0 records     (M7 writes, M4 reads), 17 x 48 bytes
 */
#ifndef DIAG_SHARED_H
#define DIAG_SHARED_H

typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

#define DIAG_MAGIC 0x31474144u /* "DAG1" */
#define DIAG_RECORDS 17u

enum {
    K_PAD = 1,     /* engine input handler saw 0xAC/0xAD (or first 0xAE after a press) */
    K_SEND = 2,    /* msg_send(queue, event) for an interesting event type */
    K_RING = 3,    /* event posted to the M4 MIDI ring */
    K_CONSUME = 4, /* first time a (queue, consumer) pair reads a queue */
};

typedef struct {
    u32 cyc;   /* M7 DWT CYCCNT (480 MHz, wraps ~8.9 s) */
    u32 kind;  /* K_* | seq << 16 */
    u32 lr;    /* caller (return address), 0 if not meaningful */
    u32 obj;   /* queue / engine / ring object */
    u32 ev[6]; /* raw 24-byte event, undecoded on purpose */
    u32 extra; /* dequeue index, etc. */
    u32 pad_;
} Rec; /* 48 bytes */

typedef struct {
    u32 magic;
    u32 wr;        /* records written (monotonic) */
    u32 dropped;   /* records refused while the M4 was behind */
    u32 dedupe_full;
    u32 r[4];
} M7Head; /* 32 bytes */

typedef struct {
    u32 magic;
    u32 rd;        /* records consumed by the M4 (monotonic) */
    u32 r[6];
} M4Head; /* 32 bytes */

#define M7H ((volatile M7Head *)0x24048500u)
#define M4H ((volatile M4Head *)0x24048520u)
#define PRIV_BASE 0x24048540u
#define RECS ((volatile Rec *)0x240486C0u)
#define POLY_BASE 0x240485E0u
#define POLY_BYTES 0xE0u
_Static_assert(sizeof(Rec) == 48, "record size");
_Static_assert(0x240486C0u + DIAG_RECORDS * sizeof(Rec) <= 0x24048A00u, "records overrun our slice");

#endif
