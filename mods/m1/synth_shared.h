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
 * line's generation) and SYNTH_TAKE says the M7 took the area this boot. BE4.47 (clean start): the M7 zeroes the whole
 * area when it takes the reservation (once per boot: every reset is a system reset), the M4's lines too, before any M4
 * write can happen, and its generation is 1 on every boot (it bumped it from the last run's header until v4.46). A new generation or a new
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
 * cleans after writing, read by the M4's black box. [7:0] mode, [23:8] Region-only takes this boot (since the slice was new until v4.46),
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
    unsigned drum_serial;                                   /* S6c: +2 on every publish of the drum knob lines (even);
                                                               0 until the M4 publishes them (S6c-2): defaults only */
    unsigned r5, r6, r7;
} SaM4;
/* +0x0040 M7 stats, two lines (M7 writes and cleans after each block, M4 reads) */
#define SA_STATS 0x0040u
typedef struct {
    unsigned blocks;          /* blocks with an Analog voice rendered (halved with sum16, see below) */
    unsigned sum16;           /* DWT cycles in our hooks per such block, >> 4, summed; both halve near 2^31 */
    unsigned peak;            /* most cycles in one block */
    unsigned osc_peak;        /* most oscillators rendered in one block */
    unsigned props;           /* BE4.40: set-property messages of our keys an oscillator took (N3, dsp/analog.cpp an_prop) */
    unsigned n_max;           /* the largest frame count seen */
    unsigned sync_skipped;    /* oscillator renders with both-Analog sync skipped because n > AN_SYNC_FRAMES (256: 2N) */
    unsigned hist[4];         /* Analog's share of the block: < 5, 5-15, 15-30, 30+ % */
    unsigned voices_peak;     /* most voice entries in one block */
    unsigned renders;         /* oscillator renders (all) */
    unsigned misses;          /* per-root glide line refills (BE4.40: the glide's last note per root only) */
    unsigned synced;          /* BE4.40: oscillator renders with hard sync engaged (master or slave; both Analog or mixed) */
    unsigned shape_mods;      /* BE4.40 J4: Analog renders whose Shape was moved by modulation routed to Position */
} SaStats;                    /* 64 bytes: blocks +0, sum16 +4, peak +8, osc_peak +12, n_max +20, sync_skipped +24,
                                 hist +28, voices_peak +44, renders +48, misses +52, synced +56, shape_mods +60 */
/* +0x0100 M7 FM stats, one line (P3, docs/plan-p3-fm-bits.md 3.3: SaStats has no free word; M7 writes and cleans after each
 * block, as SA_STATS, M4 reads: an_print's fm= / fmx=). +0x0100..+0x0200 was undefined; the M7 zeroes +0x40..+0x200
 * when it takes the area (m7_polyat.c synth_take), so a build whose DSP does not count yet reads 0 (Q4: fm_skipped for Sub
 * FM's buffer A; Q5: fm, the renders with Sub FM; Q6: fm_missed, FM 1>2 without B; BE4.42: +4, Bit Depth's bits= count
 * through BE4.41, is unused and stays 0) */
#define SA_FMSTATS 0x0100u
typedef struct {
    unsigned fm;              /* renders with FM (Sub FM or FM 1>2; BE4.44 W3: Analog or Wavetable) */
    unsigned r1;              /* BE4.42: unused (BE4.41's bits=, the renders Bit Depth crushed) */
    unsigned fm_skipped;      /* renders with FM set but n > AN_FM_FRAMES (the modulator buffers): rendered without it */
    unsigned fm_missed;       /* OSC2 renders that wanted OSC1's signal (FM 1>2) but buffer B was not valid this voice-block */
    unsigned wt;              /* BE4.44 W3: Wavetable renders through our reader (dsp/analog.cpp wt_body: tier 3, FM or a mip
                               * held), the black box's wt= */
    unsigned wt_mip;          /* BE4.44 W3: of those, the blocks that changed the mip level (rd_qm), wt=.../mip */
    unsigned x2_lost;         /* BE4.45 X2: Mod Matrix routings on Sub FM / FM 1>2 the M7 could not keep (its store full:
                               * SA_X2_N routed roots; or a source neither in the voice nor in the root) */
    unsigned r7;
} SaFmStats;                  /* 32 bytes: fm +0, fm_skipped +8, fm_missed +12, wt +16, wt_mip +20, x2_lost +24 */
/* +0x0400 M7 private: the DSP's running state (two lines) */
#define SA_RUN 0x0400u
/* +0x0440 M7 private (BE4.45 X2, docs/plan-fm-targets.md): the Mod Matrix routings on Sub FM (key 0x18B) and FM 1>2 (0x132),
 * per Wavetable root (stock keeps a destination's routings in the voice's own parameter object; these keys have none):
 * SA_X2_N entries of 56 B (8 tracks: one per Wavetable root), used from the front (dsp/analog.cpp X2Root; their count is in the run state). Inside what the
 * M7 zeroes at its take (+0x400..+0x800); free on main too (its run state is the same two lines) */
#define SA_X2 0x0440u
#define SA_X2_N 8u
/* +0x0200 (BE4.21..BE4.31: the M4's Analog track table, published; unused since BE4.40, when an oscillator's Type, Shape,
 * Sync and Glide became stock properties that reach the M7 as set_property messages: docs/plan-native-props.md) */
#define AN_GLIDE_MAX 1000u                                  /* Glide 0..1000 = 0..2 s, stock's encoder units */
#define AN_FM_MAX 1000u                                     /* P3: Sub FM, FM 1>2 0..1000 (0..100.0 % of the knob; the FM
                                                               sprint plays index 16 (v / 1000)^1.5, BE4.42; BE4.41 8 (v / 1000)^2) */
/* BE4.43 (docs/plan-p3-fm-bits.md BE4.43; p3_golden.py fm_env, the spec): the FM envelope per oscillator, env x the FM
 * amount's index at block rate (2N = 256 frames at 96 kHz = 2.667 ms). FM Atk 0..127 (0 instant), FM Dec 0..128 (128 = Inf,
 * the top of the knob next to the longest decay: hold 1 = BE4.42's static depth, the default). A value's time in blocks is
 * AN_FM_ENV_BLOCKS (p3_golden.FM_ENV_BLOCKS, the contract: n(x) = 1 + round((e^(L x) - 1) / L), 1 .. 3750 blocks = 2.7 ms
 * .. 10.0 s, 128 distinct). Attack linear in index 0 -> 1 over n blocks; decay exponential to -60 dB at n blocks, then 0.
 * Both cores read it (the M7's envelope, the M4's value texts) */
#define AN_FM_ATK_MAX 127u
#define AN_FM_DEC_INF 128u
#define AN_FM_ENV_BLOCKS { \
    1, 2, 3, 4, 5, 7, 8, 9, 10, 12, 13, 15, 16, 18, 20, 21, \
    23, 25, 27, 29, 31, 34, 36, 38, 41, 44, 46, 49, 52, 55, 58, 62, \
    65, 69, 72, 76, 80, 85, 89, 94, 98, 103, 108, 114, 119, 125, 131, 137, \
    144, 151, 158, 165, 172, 180, 189, 197, 206, 215, 225, 235, 245, 256, 267, 279, \
    291, 304, 317, 331, 345, 360, 376, 392, 408, 426, 444, 463, 482, 502, 524, 546, \
    569, 592, 617, 643, 670, 698, 727, 757, 788, 821, 855, 890, 927, 965, 1005, 1046, \
    1089, 1134, 1180, 1229, 1279, 1331, 1386, 1442, 1501, 1563, 1626, 1693, 1762, 1833, 1908, 1986, \
    2066, 2150, 2238, 2329, 2423, 2521, 2624, 2730, 2841, 2956, 3075, 3200, 3329, 3464, 3604, 3750 }
#define AN_SHAPE_MAX 127u                                   /* Shape 0..127: sine > triangle > saw > square > pulse (P3 Q3,
                                                               docs/plan-p3-fm-bits.md 4.6; BE4.40: saw > square > pulse) */
#define AN_SHAPE_SINE 0u                                    /* P3 Q3 (OD6), one-sided plateaus: 0..2 plays the sine, */
#define AN_SHAPE_SINE_END 2u
#define AN_SHAPE_TRI_LO 22u                                 /* 2..22 morphs it to the triangle, 22..26 the triangle, */
#define AN_SHAPE_TRI 24u
#define AN_SHAPE_TRI_HI 26u
#define AN_SHAPE_SAW_LO 45u                                 /* 26..45 morphs it to the saw, 45..48 the saw (BE4.40: 0..48), */
#define AN_SHAPE_SAW_END 48u                                /* 48 the saw's anchor (dsp/analog.cpp an_shape), */
#define AN_SHAPE_SQUARE 88u                                 /* 48..88 morphs it to a square, 88..127 narrows the pulse:
                                                               the kernel's map and the M4's dial / value text (m4_synth.c)
                                                               read these, so they change together (both cores, one kit) */
#define AN_SHAPE_DEFAULT 48u                                /* saw (the BE4.22..BE4.31 sound); N1's template default */
/* +0x0600 Analog per-root glide lines, 16 x 32 B (M7 private): the track's last note (glide starts from it) and the
 * glide coefficient cache */
#define SA_ANCACHE 0x0600u
#define SA_ANCACHE_N 16u
/* +0x0800 M7 private (P3, docs/plan-p3-fm-bits.md 3.3): the FM modulator buffers of the voice-block being rendered, two x
 * AN_FM_FRAMES floats in increment units: A (+0x800) the sub oscillator's copy, B (+0xC00) OSC1's pre-Level block times
 * OSC1's increment (an_feed; also for a silent OSC1; BE4.42: no Bit Depth grid any more, OD15 / OD18 moot).
 * Valid only while the run state's voice is that voice and its fmtag bit says so (dsp/analog.cpp). The M4 never reads or
 * writes it (SA_UI, the M4's synth UI state, unused since the review: the M4's state is in AN4) */
#define SA_FMBUF 0x0800u
#define AN_FM_FRAMES 256u                                   /* one buffer: 2N, the device's oscillator block */
#define SA_FMBUF_A SA_FMBUF
#define SA_FMBUF_B (SA_FMBUF + 4u * AN_FM_FRAMES)
/* BE4.46 S1 (docs/feasibility-fm-env-sources.md): FM Env 1 / FM Env 2 (OSC1's / OSC2's FM envelope) as mod-matrix sources
 * 3 / 6 of a Wavetable voice, in the voice's allocation slack: stock's root ctor allocates a voice as 0xB88 B (0x0809CB40)
 * and its pool rounds that to 0xBA0 (0x080A05E6), so voice + 0xB88 .. + 0xB9F is ours (S; no stock code writes it: M,
 * test_analog FmEnvSrc). The pool (kind 2, 0x080A0720) is a bump allocator from 0x24001080, never freed, no header, inside
 * the bss the M7 reset zeroes (0x08040492: 0x240001D0 .. 0x2401EDBC), so the slack is 0 at allocation (S). Stock's routing
 * connect (0x0807C8A0, build.py fmenv_connect's stubs and bodies) gives source 3 the pointer voice + FMENV_SRC and source 6
 * voice + FMENV_SRC + 4, on a Wavetable voice only ([voice + 0x178] == voice + 0x7A4: the class-0x5A root shares the
 * connect); PARAM_VALUE reads a source's float at +4, so FM Env 1 is the float at voice + FMENV_VAL and FM Env 2 the next.
 * On a voice's first such connect the body writes the pointer into FMENV_ON (nonzero: a route to 3 or 6 was connected on
 * this voice since its allocation, sticky) and 1.0 into both values (later connects leave them); while FMENV_ON is 0 the DSP publishes
 * nothing (an_enter: one load and a branch). Published: e0, the envelope's value at the block's start (fm_env_step's
 * first line, read only), 1 for an unset envelope (Atk 0 / Dec Inf) and an untagged Wavetable oscillator. build.py reads
 * these four (fmenv_offsets) */
#define FMENV_SRC 0xB84u
#define FMENV_VAL 0xB88u
#define FMENV_ON 0xB90u
#define FMENV_SLACK_END 0xBA0u
/* +0x0080 drum stats, four 32 B lines (M7 writes and cleans after each block with a drum voice, M4 reads) */
#define SA_DSTATS 0x0080u
typedef struct {
    unsigned blocks;          /* blocks with a drum voice rendered (halved with sum16) */
    unsigned sum16;           /* DWT cycles in the drum hook per such block, >> 4, summed */
    unsigned peak;            /* most cycles in one block */
    unsigned cells_peak;      /* most drum voices rendered in one block */
    unsigned renders;         /* drum voice-blocks rendered */
    unsigned starts;          /* drum synth starts (a carrier start whose engine is built) */
    unsigned retrigs;         /* of those, starts on a voice whose synth was still ringing (a restart of that voice) */
    unsigned detects;         /* carrier tests run (a carrier-length sample seen for the first time on a pad) */
    unsigned carriers;        /* of those, carriers */
    unsigned unbuilt;         /* starts on a carrier whose engine is not in this build (played natively: near silence) */
    unsigned quiet_ends;      /* voices ended at -96 dB */
    unsigned cap_ends;        /* voices ended at the length cap */
    unsigned knob_reads;      /* knob table reads (a start, or a new M4 drum serial) */
    unsigned n_max;           /* the largest frame count seen */
    unsigned unbound;         /* renders with no cell for their voice: more voices than DRUM_CELLS (stock has 32: never) */
    unsigned short_bufs;      /* renders handed to stock because the block was empty, over 1024 frames or past the scratch */
    unsigned hist[3];         /* blocks where the drums took >= 5 % / >= 15 % / >= 30 % of the 1,280,000-cycle block */
    unsigned knob_hits;       /* S6c-2: knob table reads that found the pad's line (its handle = root +0x0C: device evidence) */
    unsigned r20[12];
} SaDrumStats;                /* 128 B: blocks +0, sum16 +4, peak +8, cells_peak +12, renders +16, starts +20, retrigs +24,
                                 detects +28, carriers +32, unbuilt +36, quiet_ends +40, cap_ends +44, knob_reads +48,
                                 n_max +52, unbound +56, short_bufs +60, hist +64, knob_hits +76 */
/* +0x1000 drum knob table, 64 lines (M4 writes, M7 reads): the M4's table (DK4, m4_synth.c), published once the handshake
 * holds (S6c-2; the M7 reads it only once the M4 header's drum_serial is not 0). A line: seqlocked as SaTrack; handle =
 * the pad's object handle (the instrument page's cell, = its M7 root's +0x0C); word [3:0] engine override (0 = the
 * carrier's; S6c-2 has no engine knob and publishes 0), [8] in use; v0 knob values 0..3 (byte i = knob i), v1 knobs 4..7. */
#define SA_DKTAB 0x1000u
#define SA_DKTAB_N 64u
#define DK_USED 0x100u
typedef struct {
    unsigned head, handle, word, v0, v1, r5, r6, tail;
} SaKnobs;
/* +0x2400 drum cells, DRUM_CELLS x 256 B (M7 private, dsp/drums.h) */
#define SA_DCELLS 0x2400u
#define DRUM_CELLS 32u                                      /* one per voice of stock's one-shot pool (S_DRUM_POOL_N) */
#define DRUM_CELL_BYTES 256u
/* +0x4400 drum run state (M7 private, 512 B: block counter, the current cell's RNG, powf memo, carrier cache) */
#define SA_DRUN 0x4400u
/* +0x4600.. spare, +0x6000.. Track FX: reserved, see the plan */
/* The drum keys (S6c-2): seven contiguous keys of the reviewed allow-list, S_KEY_DR_FIRST + i for knob i, all or nothing
 * (m4_synth.c synth_keys). The Kick uses all seven. */
#define DRUM_KNOBS 8u
#define DRUM_KEYS_N 7u
/* The Kick's knobs (S6c-2: one place for both cores; dsp/drum_kick.cpp plays them, m4_synth.c shows and stores them):
 * Tune in Hz before the pad's pitch, the rest 0..100 % of DaisySP's own 0..1 parameters. Defaults after upstream K7. */
#define KICK_DEFAULTS {55, 65, 45, 65, 55, 25, 25, 0}
#define KICK_LO {25, 0, 0, 0, 0, 0, 0, 0}
#define KICK_HI {180, 100, 100, 100, 100, 100, 100, 0}
/* Carrier WAVs (tools/carriers/gen_carriers.py; docs/plan-synths-168.md section 8): mono 48 kHz 16-bit, frames =
 * CARRIER_FRAMES + engine id; 32 samples of the signature at +-1 LSB, then 32 carrying CARRIER_WORD(id), then
 * silence. Playground's carriers (PG_CARRIER_FRAMES + id - 1 frames, the signature only) are accepted too. */
#define CARRIER_SIGNATURE 0xB1E70A5Cu
#define CARRIER_FRAMES 4800u
#define CARRIER_VERSION 1u
#define CARRIER_WORD(id) (0x42450000u | (CARRIER_VERSION << 8) | (id))   /* "BE", version, engine id */
#define PG_CARRIER_FRAMES 144019u
#define ENGINE_KICK 1u                                      /* engine ids: Playground's (Kick 1, Snare 3, HiHat 5, */
#define ENGINE_SNARE 3u                                     /* Clap 7, Tom 9) */
#define ENGINE_HIHAT 5u
#define ENGINE_CLAP 7u
#define ENGINE_TOM 9u

#define SA_DCIMVAC 0xE000EF5Cu
#define SA_DCCMVAC 0xE000EF68u
#define SA_DCCIMVAC 0xE000EF70u

#endif
