/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Synth area (S6a, docs/plan-synths-168.md section 4): 32 KB of SDRAM directly below the M1 Region,
 * taken by the M7 in the same reservation as the Region (m7_polyat.c region_reserve), so stock's
 * ext pool loses 48 KB in one step and the Region keeps its address.
 *
 *   EXT_POOL_END - SYNTH_TOTAL .. EXT_POOL_END - M1_REGION_RESERVE   synth area (this file)
 *   EXT_POOL_END - M1_REGION_RESERVE .. EXT_POOL_END                  M1 Region (diag_shared.h)
 *
 * Rules (the slice's): one writer per 32-byte line. The M7 reaches the area through its write-back
 * mapping (MPU R8): it invalidates (DCIMVAC) an M4 line before reading it and cleans (DCCMVAC) every
 * line it writes for the M4. The M4 has no data cache. M4 table lines carry a seqlock (head, tail):
 * the M7 accepts a line only when head == tail and both are even, all from one line fill.
 *
 * Who holds the truth (review 2026-11-08): the M4's own table in our reset-surviving slice (AN4, m4_synth.c),
 * which needs no M7 and so takes a project load at any time, even before the M7 has taken the area. The
 * table here is its published copy for the M7, written only by the M4's UI task. The M7 takes the area as soon as
 * the M4's config line is up (h_consume, every audio block), or at the first note in a build without event tracing.
 *
 * Handshake: the area is stock pool memory until the M7 has taken the reservation. The M4 writes
 * nothing here before it has seen the M7 header (SA_M7_MAGIC) stamped for this M4 boot (boot = the config
 * line's generation). The M7 zeroes and cleans its own lines when it takes the reservation (every M7 boot,
 * also after an M7-only reset) and bumps the generation; it never writes an M4 line. A new generation or a new
 * M4 boot makes the M4 publish its whole table again (and move its serial, so the M7's per-root cache refills).
 * When the pool has less than SYNTH_POOL_MARGIN, the M7 takes the Region alone: SYNTH_TAKE says so, the header
 * is cleared while it is still free memory, and nothing is Analog this M7 boot.
 * Included by m7_polyat.c (unless BE_OMIT_SYNTH), the DSP ELF (dsp/) and m4_synth.c; C and C++. */
#ifndef SYNTH_SHARED_H
#define SYNTH_SHARED_H

#define SYNTH_BYTES 0x8000u                                 /* 32 KB */
#define SYNTH_TOTAL (0x4000u + SYNTH_BYTES)                 /* + the M1 Region (M1_REGION_RESERVE) */
#define SYNTH_AREA (S_EXT_POOL_END - SYNTH_TOTAL)           /* 1.6.8: 0xC5FF4000 */
#define SYNTH_POOL_MARGIN 0x100000u                         /* taken only if the pool keeps 1 MB */
#define SA(off) (SYNTH_AREA + (off))
/* How the M7 took its reservation this boot: a word of its private slice line (M7_REGION_CACHE + 0x10), which it
 * cleans after writing, read by the M4's black box. [7:0] mode, [23:8] Region-only takes since the slice was new,
 * [31:24] SYNTH_TAKE_TAG (anything else: never written, read as 0). */
#define SYNTH_TAKE (*(volatile unsigned *)(S_D2_SLICE + 0x1D8u))
#define SYNTH_TAKE_FULL 1u                                  /* Region + synth area */
#define SYNTH_TAKE_REGION 2u                                /* the Region alone: the pool kept less than the margin */
#define SYNTH_TAKE_TAG 0xA5u

/* +0x0000 M7 header (M7 writes and cleans, M4 reads) */
#define SA_M7 0x0000u
#define SA_M7_MAGIC 0x37534542u                             /* "BES7" */
#define SA_LAYOUT 2u                                        /* 2: boot stamp, glide 0..1000 (S6a review) */
typedef struct {
    unsigned magic, gen, layout, total;                     /* gen: +1 on every take (never 0) */
    unsigned boot;                                          /* the M4 boot generation (config line r[0]) this is for */
    unsigned handle_seen, root_seen, handle_ok;             /* one-shot check: root+0x0C vs the table */
} SaM7;
/* +0x0020 M4 header (M4 writes, M7 reads) */
#define SA_M4 0x0020u
#define SA_M4_MAGIC 0x34534542u                             /* "BES4" */
typedef struct {
    unsigned magic, m7gen, bootgen, serial;                 /* serial: +2 on every publish (even) */
    unsigned r4, r5, r6, r7;
} SaM4;
/* +0x0040 M7 stats, two lines (M7 writes and cleans after each block, M4 reads) */
#define SA_STATS 0x0040u
typedef struct {
    unsigned blocks;          /* blocks with an Analog voice rendered (halved with sum16, see below) */
    unsigned sum16;           /* DWT cycles in our hooks per such block, >> 4, summed; both halve near 2^31 */
    unsigned peak;            /* most cycles in one block */
    unsigned osc_peak;        /* most oscillators rendered in one block */
    unsigned r4;              /* (spare: blocks over 100 % LOAD are counted by the M4, AN4 'over') */
    unsigned n_max;           /* the largest frame count seen */
    unsigned sync_skipped;    /* oscillator renders with sync skipped because n > SYNC_FRAMES */
    unsigned hist[4];         /* Analog's share of the block: < 5, 5-15, 15-30, 30+ % */
    unsigned voices_peak;     /* most voice entries in one block */
    unsigned renders;         /* oscillator renders (all) */
    unsigned misses;          /* per-root cache refills */
    unsigned r14, r15;
} SaStats;                    /* 64 bytes: blocks +0, sum16 +4, peak +8, osc_peak +12, n_max +20, sync_skipped +24,
                                 hist +28, voices_peak +44, renders +48, misses +52 */
/* +0x0400 M7 private: the DSP's running state (two lines) */
#define SA_RUN 0x0400u
/* +0x0200 Analog track table, 16 lines (M4 writes, M7 reads): the M4's AN4 table, published */
#define SA_ANTAB 0x0200u
#define SA_ANTAB_N 16u
#define AN_ON 1u
#define AN_SYNC 2u
#define AN_GLIDE_MAX 1000u                                  /* Glide 0..1000 = 0..2 s, stock's encoder units */
typedef struct {
    unsigned head, handle, flags, glide;                    /* glide 0..AN_GLIDE_MAX */
    unsigned r4, r5, r6, tail;
} SaTrack;
/* +0x0600 Analog per-root cache, 16 x 32 B (M7 private) */
#define SA_ANCACHE 0x0600u
#define SA_ANCACHE_N 16u
/* +0x0800 M4 only: synth UI state (2 KB, unused since the review: the M4's state is in AN4) */
#define SA_UI 0x0800u
/* +0x1000.. drums (S6c), +0x6000.. Track FX: reserved, see the plan */

#define SA_DCIMVAC 0xE000EF5Cu
#define SA_DCCMVAC 0xE000EF68u
#define SA_DCCIMVAC 0xE000EF70u

#endif
