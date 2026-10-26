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
 *   0x240485E0 config      (M4 writes once at boot, M7 reads): M1 state region
 *   0x24048600 M7 stats    (M7 writes, M4 reads): MIDI ring fill / overflows
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
    K_VOICE = 5,   /* poly cap: lr = 1 retrigger, 2 steal, 3 swallowed Note Off; extra = note */
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
#define M7_REGION_CACHE 0x240485C8u /* M7 private: cached region pointer */

typedef struct {
    u32 magic;
    u32 region;        /* M7-owned state region in the M4 SDRAM pool (0 = none yet) */
    u32 region_bytes;
    u32 r[5];          /* r[0] = M4 boot generation the region belongs to */
} Cfg; /* 32 bytes */
#define CFG ((volatile Cfg *)0x240485E0u)
#define M1_REGION_BYTES 0x1000u

typedef struct {
    u32 max_fill;  /* highest M7->M4 MIDI ring fill seen when posting (16 slots) */
    u32 overflows; /* posts made while 16 entries were still unread (overwrites) */
    u32 posts;
    u32 min_msp;   /* lowest SP seen in our hooks while on the M7 main stack */
    u32 min_sp;    /* lowest SP seen anywhere in our hooks (any stack) */
    u32 heartbeat; /* bumped on every queue read: stops when the M7 stops */
    u32 r[2];
} RingStat; /* 32 bytes */
#define M7_STACK_TOP 0x2404CDA0u    /* initial M7 MSP */
#define M7_STACK_BOTTOM 0x24048DA0u /* below it: Lab's state, then our slice */
#define RINGSTAT ((volatile RingStat *)0x24048600u)
#define MIDI_RING_SLOTS 16u
_Static_assert(sizeof(Rec) == 48, "record size");
_Static_assert(0x240486C0u + DIAG_RECORDS * sizeof(Rec) <= 0x24048A00u, "records overrun our slice");

#endif
