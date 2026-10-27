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
#define M1_REGION_BYTES 0x2000u   /* 8 KB since v3.25 (curve players) */
#define REGION_CURVE_STATS 32u     /* curve_starts, curve_sent, curve_late, curve_skipped (m7_polyat.c) */
#define REGION_CURVE_PEAK 52u      /* curve_peak, curve_thinned, curve_dest (port | ch << 8 | mode << 16) */
#define REGION_INTERNAL 64u        /* int_dest, live_int, int_sent (M2-2b internal instruments) */
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
/* v3 curve word (tag 0xB7, M2, melodic notes only): a recorded pressure curve
 *   [23:17] condition (as above)   [16:3] curve handle 1..16383   [2:0] locks
 * No retrig fields: hits/div/ramp read as their defaults. */
#define STEP_TAG_CURVE 0xB7u
static inline int step_valid(u32 w) { return (w >> 24) == STEP_TAG || (w >> 24) == STEP_TAG_V1; }
static inline int step_any(u32 w) { return step_valid(w) || (w >> 24) == STEP_TAG_CURVE; }
static inline u32 step_handle(u32 w) { return (w >> 24) == STEP_TAG_CURVE ? (w >> 3) & 0x3FFFu : 0u; }
static inline u32 step_cond(u32 w) { return step_any(w) ? (w >> 17) & 0x7Fu : 0u; }
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
static inline u32 step_locks(u32 w) { return (w >> 24) == STEP_TAG || (w >> 24) == STEP_TAG_CURVE ? w & 7u : 0u; }
/* A curve handle wins: a note with a curve carries no retrig fields. */
static inline u32 step_make(u32 cond, u32 hits, u32 div, int ramp, u32 locks, u32 handle) {
    if (handle & 0x3FFFu)
        return (STEP_TAG_CURVE << 24) | ((cond & 0x7Fu) << 17) | ((handle & 0x3FFFu) << 3) | (locks & 7u);
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

/* M7 stall profile (v3.17, m7_stall.c prof_sample): the M7 SysTick (1 ms)
 * samples the interrupted PC/LR while the main audio loop has made no
 * progress for >= PROF_START_MS, every PROF_EVERY_MS, plus one raw stack copy
 * at PROF_STACK_MS. A new stall starts a new capture, so the last (fatal)
 * stall survives the reset. M7 writes and cleans; the M4 prints it at boot.
 * Lives in the D2 gap above the canary (0x3003D000.., see m4_diag.c). */
#define PROF_SAMPLES 40u
#define PROF_STACK_WORDS 48u
#define PROF_START_MS 50u
#define PROF_EVERY_MS 20u
#define PROF_STACK_MS 300u
typedef struct {
    u32 magic, gen, stalls, n, idle_ms, seen_last, stack_ms, frame;   /* one cache line */
    u32 pc[PROF_SAMPLES], lr[PROF_SAMPLES];
    u16 at_ms[PROF_SAMPLES];
    u32 stack[PROF_STACK_WORDS];
} Prof;
/* ---- M2 pressure capture (v3.22) -------------------------------------------
 * The M7 (h_input, m7_diag.c) records each pad press's pressure on a fixed
 * clock (CAP_MS per sample, from the M7 HAL tick) into one of two ping-pong
 * slots per pad (slot = press count & 1, so a just-released note survives the
 * next press of that pad). Past CAP_SAMPLES the slot halves its resolution
 * (shift++). Values are stock's 0..127 pressure scale. The block lives in SDRAM
 * from the M4's bump allocator (the M7 reaches 0xC6.. through its default map,
 * uncached), published once per boot in CAPCFG (M4 writes, M7 invalidates and
 * reads; trusted only with its magic, check word and boot generation). */
#define CAP_MS 8u
#define CAP_SAMPLES 1024u
typedef struct {
    u32 seq;        /* the pad's press count (STRIKES->seq) this slot holds */
    u32 t0, t_end;  /* M7 HAL ms at press / release */
    u32 n;          /* samples filled */
    u32 shift;      /* resolution halvings (each sample = CAP_MS << shift ms) */
    u32 done;       /* 1 once released */
    u32 r_[2];
    u8 s[CAP_SAMPLES];
} CapSlot;          /* 1056 bytes */
typedef struct { CapSlot slot[16][2]; } CapBlock;
typedef struct { u32 magic, block, check, gen, r_[4]; } CapCfg;
#define CAPCFG ((volatile CapCfg *)0x3003D820u)
#define CAPCFG_MAGIC 0x31504143u /* "CAP1" */

/* M4 side (m4_step.c): curves attached to recorded melodic notes. The pool
 * (SDRAM, bump-allocated on first use) holds [u16 n][n samples] per curve at
 * 16 samples per step; handles index the table (1-based). Each attach is
 * logged to CURVELOG for the black box.
 * Persistence (v3.26): on project save, a curve note's XML word carries its
 * 1-based index in this save (savelist); right after stock writes
 * ...\project.xml the black box writes ...\curves.bnt next to it (stock never
 * sees curve data). Sidecar format, little endian:
 *   "BNTC" u16 version=1 u16 count, then per curve: u16 n, u16 len, len bytes
 *   encoded: 0x00-0x7F literal sample; 0x80-0xBF repeat previous (b&0x3F)+1
 *   times; 0xC0-0xFF two deltas d1=((b>>3)&7)-4, d2=(b&7)-4 (two samples). */
#define SIDECAR_MAGIC 0x43544E42u /* "BNTC" */
#define CURVE_POOL_BYTES 0x100000u   /* 1 MB (v3.26) */
#define CURVE_HANDLES 16384u         /* the word's 14-bit field: 1..16383 */
typedef struct {
    u32 magic, gen, pool, used, table, next, claims, misses;   /* first line: the M7 reads it */
    u32 savelist, nsave;             /* handles of curve notes in the save in progress (u16 each) */
    u32 sc_count, sc_bytes, sc_status;   /* last sidecar written */
    u32 wbuf;                        /* sidecar write buffer, SDRAM: the SD controller's DMA cannot
                                        reach D2 SRAM (the M4 stack), so file data never comes from it */
} CurveState;
#define SIDECAR_WBUF 4096u
_Static_assert(sizeof(CurveState) <= 64u, "curve state reaches the curve log");
#define CURVES ((volatile CurveState *)0x3003D840u)
#define CURVES_MAGIC 0x31565243u /* "CRV1" */
typedef struct {
    u32 seq;              /* 0 = empty; bumped per entry */
    u16 handle, n_out;    /* curve handle, samples stored (16/step) */
    u16 pad, rec_pad;     /* capture slot's pad; rec[0] >> 16 */
    u16 pitch, len_steps; /* rec[4] & 0xFFFF; rec[2] / 960 */
    u16 n_in, shift;      /* captured samples, resolution halvings */
    u32 dur_ms, len_ticks, r_;
    u8 first[32];         /* first 32 stored samples */
} CurveLogEntry;
_Static_assert(sizeof(CurveLogEntry) == 64, "curve log entry is 64 bytes");
#define CURVELOG ((volatile CurveLogEntry *)0x3003D880u)
#define CURVELOG_N 16u
#define CLAIMED ((volatile u32 *)0x3003DC80u)   /* [pad*2 + slot]: claimed press seq (M4) */
/* M2-1 diagnosis (M4): callers of stock's note insert, by return address. */
typedef struct { u32 magic, gen, total, last_kind, lr[8], count[8]; } InsLog;
#define INSLOG ((volatile InsLog *)0x3003DD00u)
#define INSLOG_MAGIC 0x31534E49u /* "INS1" */
/* M4: melodic note-on binds a pitch to the capture slot just pressed (pitch
 * | 0x10000 so 0 means free); note-off looks it up. */
typedef struct { u32 pitch, pad, seq, r_; } Bind;
#define BINDS ((volatile Bind *)0x3003DD60u)
#define BINDS_NEXT (*(volatile u32 *)0x3003DE60u)
/* M2-3b (M4): the document stock is reading (its reader 0x08130A18 is hooked),
 * and the curves its sidecar gave: sidecar index i -> handle base + i - 1. */
typedef struct { u32 magic, doc_seq, attempted, base, count, status; char path[160]; } LoadState;
#define LOADSTATE ((volatile LoadState *)0x3003DE80u)
#define LOADSTATE_MAGIC 0x31444C42u /* "BLD1" */

/* M2-5: per-track aftertouch settings, stored as differences from the default
 * so zeroed memory means "record on, send poly". Indexed by track number (the
 * model's selected-track value). M4-only. */
#define AT_POLY 0u
#define AT_OFF 1u
#define AT_CHAN 2u                   /* v3.30: behaves as Off until channel pressure exists */
typedef struct { u32 magic, gen; u8 rec_off[16], out_mode[16]; u32 pending; } AtSet;
#define AT_SET ((volatile AtSet *)0x3003DF40u)
#define AT_SET_MAGIC 0x32544541u /* "AET2": + pending (v3.31) */
_Static_assert(0x3003DF40u + sizeof(AtSet) <= 0x3003DF80u, "AT settings reach the dest table");
/* Published for the M7 (M4 writes, M7 invalidates and reads): each track's MIDI
 * destination and send mode. A curve's player looks its port/channel up here;
 * no match means the default (poly). */
typedef struct { u8 port, ch, mode, valid; } AtDest;
typedef struct { u32 magic, seq; AtDest d[8]; } AtDestTable;
#define AT_DEST ((volatile AtDestTable *)0x3003DF80u)
#define AT_DEST_MAGIC 0x31544441u /* "ADT1" */
_Static_assert(0x3003DF80u + sizeof(AtDestTable) <= 0x3003DFC0u, "AT dest table");
/* status: 0 none yet, 1 not a project.xml, 2 no sidecar, 3 bad data, 4 ok,
 * 5 pool/handles full (partial), 9 no curve memory */
_Static_assert(0x3003D880u + CURVELOG_N * sizeof(CurveLogEntry) <= 0x3003DC80u, "curve log");

#define PROF ((volatile Prof *)0x3003D000u)
#define PROF_MAGIC 0x31464F50u /* "POF1" */
_Static_assert(0x3003D000u + sizeof(Prof) <= 0x3003D400u, "profile past its 1 KB");

#endif
