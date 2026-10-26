/* SPDX-License-Identifier: GPL-3.0-or-later */
/* M1 diagnostic: shared M7 -> M4 log layout (stock 1.6.1 + Lab CH10 1.6HA).
 *
 * Purpose: find which code turns a pad press on an External track into a
 * MIDI note-on, by logging every hop: pad event -> queue -> consumer ->
 * MIDI-out event -> cross-core MIDI ring. The M7 only writes RAM; the M4
 * formats and writes \BENTO_DIAG.log on its UI thread when the pads are idle.
 *
 * RAM (since v3.16): our whole slice is 0x3003C000..0x3003C9A0, in the top of
 * D2 SRAM2 that no stock linker section uses (M4 .bss ends 0x30036CF0, heap
 * 0x30036CF0..0x30037CF0, stack 0x30037CF0..0x300390F0; the M4 reset code
 * briefly uses a stack below 0x30040000; SRAM3 from 0x30040000 holds the MIDI
 * ring). M4 MPU region 2 maps 0x30000000..0x30080000; the M7 reaches it through
 * its default map (cacheable: same clean/invalidate rules as before). Until
 * v3.15 it lived at 0x24048400 by lowering the M7 heap end, which broke
 * loading large projects. Layout inside the slice is unchanged (offset -0x24048400
 * + 0x3003C000).
 *
 * Ownership, one writer per 32-byte cache line:
 *   0x3003C100 M7 header   (M7 writes, M4 reads)
 *   0x3003C120 M4 header   (M4 writes, M7 never reads)
 *   0x3003C140 M7 private  diag: dedupe table 16 x 8 B
 *   0x3003C1C0 M7 private  diag: flags, generation
 *   0x3003C1E0 config      (M4 writes once at boot, M7 reads): M1 state region
 *   0x3003C200 M7 stats    (M7 writes, M4 reads): MIDI ring fill / overflows
 *   0x3003C2C0 records     (M7 writes, M4 reads), 17 x 48 bytes
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

#define M7H ((volatile M7Head *)0x3003C100u)
#define M4H ((volatile M4Head *)0x3003C120u)
#define PRIV_BASE 0x3003C140u
#define RECS ((volatile Rec *)0x3003C2C0u)
#define M7_REGION_CACHE 0x3003C1C8u /* M7 private: cached region pointer */

typedef struct {
    u32 magic;
    u32 region;        /* M7-owned state region in the M4 SDRAM pool (0 = none yet) */
    u32 region_bytes;
    u32 r[5];          /* r[0] = M4 boot generation the region belongs to */
} Cfg; /* 32 bytes */
#define CFG ((volatile Cfg *)0x3003C1E0u)
#define M1_REGION_BYTES 0x1000u
#define RETRIG_BYTES 512u          /* 16 bursts x 32 B, inside the M1 region */

/* Per-step word: the native note record's +20 word (notes, kind 1, only;
 * stock uses +20 for automation/XY/scene items). Saved as XML attribute
 * "be_step_v1" (the value carries its own format tag). Common to both:
 *   [23:17] condition (0 none, 1..74 = stored value - 99)
 *   [16:13] retrig hits 0..15   [12:9] division index + 1 (0 = default 1/16)
 * v1 (tag 0xB5, v3.3):  [8] spare   [7:0] ramp, signed percent
 * v2 (tag 0xB6, v3.4+): [8:3] ramp index (0 = 0 %, else ramp/5 + 21: 1..41)
 *                       [2:0] locks: bit0 velocity, bit1 probability, bit2 offset
 * Every field at its default -> the word is 0 (a stock-identical note). */
#define STEP_TAG_V1 0xB5u
#define STEP_TAG 0xB6u
#define STEP_DIV_DEFAULT 3u   /* 1/16 in DIV order: 1/8 1/8T 1/8. 1/16 1/16T 1/16. 1/32 1/32T 1/32. 1/64 1/64T */
#define STEP_DIVS 11u
#define LOCK_VEL 1u
#define LOCK_PROB 2u
#define LOCK_OFFSET 4u
static inline int step_valid(u32 w) { return (w >> 24) == STEP_TAG || (w >> 24) == STEP_TAG_V1; }
static inline u32 step_cond(u32 w) { return step_valid(w) ? (w >> 17) & 0x7Fu : 0u; }
static inline u32 step_hits(u32 w) { return step_valid(w) ? (w >> 13) & 0xFu : 0u; }
static inline u32 step_div(u32 w) {
    u32 f = step_valid(w) ? (w >> 9) & 0xFu : 0u;
    return f && f <= STEP_DIVS ? f - 1u : STEP_DIV_DEFAULT;
}
static inline int step_ramp(u32 w) {
    if ((w >> 24) == STEP_TAG_V1) return (int)(signed char)(w & 0xFFu);
    if ((w >> 24) != STEP_TAG) return 0;
    u32 f = (w >> 3) & 0x3Fu;
    return f && f <= 41u ? ((int)f - 21) * 5 : 0;
}
static inline u32 step_locks(u32 w) { return (w >> 24) == STEP_TAG ? w & 7u : 0u; }
static inline u32 step_make(u32 cond, u32 hits, u32 div, int ramp, u32 locks) {
    u32 f = div == STEP_DIV_DEFAULT ? 0u : div + 1u;
    if (ramp < -100) ramp = -100;
    if (ramp > 100) ramp = 100;
    u32 r = ramp ? (u32)(ramp / 5 + 21) : 0u;
    if (!cond && !hits && !f && !r && !(locks & 7u)) return 0;
    return (STEP_TAG << 24) | ((cond & 0x7Fu) << 17) | ((hits & 0xFu) << 13) | ((f & 0xFu) << 9) | (r << 3) |
           (locks & 7u);
}

/* Strike velocity (programming drum steps by hit strength), per pad.
 * The M7 counts each press of pad p (input event 0xAC) in seq[p] and keeps
 * vel[p] = the peak of stock's velocity formula over the press and its first
 * STRIKE_PEAK_SAMPLES pressure samples (0xAE), so a quick hit isn't read at
 * first contact. One line, M7-written and cleaned, read by the M4 (v3.12). */
typedef struct { u8 seq[16]; u8 vel[16]; } Strikes;
#define STRIKES ((volatile Strikes *)0x3003C960u)
#define STRIKE_PEAK ((volatile u8 *)0x3003C090u)   /* M7-private countdown per pad (stall line spare) */
#define STRIKE_PEAK_SAMPLES 8u
/* The on/off setting (header button "Strike Vel", m4_step.c); M4-only.
 * pads: per-pad UI state block (bump-allocated once per boot, m4_step.c), trusted
 * only while r[2] == ~pads; r[0], r[1]: header-button diagnostics.
 * Reserved RAM survives resets: change the magic whenever this layout changes
 * (v3.12 kept v3.11's magic, read its old tick field as a pointer: boot loop). */
typedef struct { u32 magic, off, pads, r[3]; } StrikeSet;
#define STRIKE_SET ((volatile StrikeSet *)0x3003C948u)
#define STRIKE_SET_MAGIC 0x33455653u /* "SVE3" */
#define STRIKE_ACTION 0x10F0u        /* header button action id (unused by stock) */

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
#define M7_STACK_BOTTOM 0x24048DA0u /* stock M7 stack bottom (heap end below it) */
#define RINGSTAT ((volatile RingStat *)0x3003C200u)
#define MIDI_RING_SLOTS 16u
_Static_assert(sizeof(Rec) == 48, "record size");
_Static_assert(0x3003C2C0u + DIAG_RECORDS * sizeof(Rec) <= 0x3003C600u, "records overrun our slice");

#endif
