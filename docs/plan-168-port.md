# Rebase onto official 1.6.8 (plan, 2026-11-07)

Jeff: 1.6.8 (bug fixes and new features) becomes the base. The project is to get the working
features running on it, then add the drum and VA synths from bento-playground. Performance work
and hunting subtle bugs are lower priority: v3.71/v3.72 show no sound or performance problems.

Evidence: workflow map-161-to-168, scratch `rc14/` (matcher `map/match.py`, `map/addresses.csv`,
`layout/`, `behav/out/`, `verify/hand_checks.csv`). 1.6.8 lives only in WSL `~/bento/stock-1.6.8/`
and `C:\Users\rjrom\bento_mods\bento1.bin`; it is never committed or distributed.

## What changed (M = measured, I = inferred)
- **bento2.bin** (Controller) is byte-identical. **bento1.bin** grows 12,024 B and differs from 0x08040520 on.
- **Address map:** 313 stock addresses we use. 77 byte-identical, 148 same code moved, 87 in changed code,
  1 reported unmatched, which is `ALLOC_BUMP` at 0x080A0740 (M, hand check). Hand check: 1 wrong in 29.
  54 of 58 patch sites keep their original bytes after relocation; HEADER_GEN, EXT_BUILD and
  CTRL_CONSUMER_SITE changed, and the version string moved to 0x0818DD94.
- **Blocker: D2 0x3003C000..0x30040000 is now stock's** (M). Two 8 KB SAI DMA buffers, zeroed at boot,
  and a new M7 MPU region R14 (Device). All our reset-surviving shared state lives there. The M4 stack
  moved up (vector SP 0x3003AAA8), so the RAMCHK `d2a` block would overwrite it.
- **Registry renumbered** (M): 498 entries, not 499. Keys 0x13C..0x1D8 move down 2, 0x1D9..0x1F2 down 1;
  0x002..0x13A unchanged (incl. 0x4A..0x4E, 0x8D/0x8E, 0x131). `REG_KEYS = 499` would write past the end.
- **M4 globals moved** (M): MODEL 0x30024B00 -> 0x30025300, REGISTRY 0x30031484 -> 0x30032DE4, UI tick
  0x30035F40 -> 0x300378F8, model's modified flag +0x36C4 -> +0x3FF4. M7 globals, track offsets and
  view offsets we use are unchanged (track: M; views: I).
- **Unchanged** (M): ext pool start values and allocator semantics (Region at 0xC5FFC000 still works),
  M7 zero gap for our M7 slices, M4 MPU, project XML names and version (still 2), stock save path,
  note writer and reader (be_step_v1 behaves as on 1.6.1), the track-type switch.
- **Room:** append M4 code from 0x081B7000; current blocks end at image size 0x185500, 32,308 B under the
  bootloader's proven 0x18D334 (was 44,596 B).
- **Stock's new features** (strings, I): Pack project, Save As and load hardening, no-card recording
  message, RTC date fix (`lastknowndate`), one Input Gain (in2/in3 removed, in1 range doubled), slicer
  Cut Group, "Play Count". MIDI out drops notes > 127 (our 0x5F/0x60 handlers should match).
  **No overlap with our features, and no synth engines** (M, strings and code).

## Requirements, prioritised
Must:
1. Every feature that works on v3.72 works on 1.6.8, verified on the device against SPACED.
2. Black box first, so the device can report back during the port.
3. Never brick: build refuses a mismatched base; bootloader untouched; nothing at or above 0x081E0000.
4. Jeff's projects stay readable both ways; full card backup before each first flash.
5. A rollback image (v3.72 kit, 03_RESTORE_STOCK) at every step.
Should:
6. Next rebase (1.6.9 ...) is cheap: addresses come from a generated per-base table, not hand edits.
7. Drum synths, then VA synths, after parity (earlier decisions stand: vendored C++ in separate
   hard-float DSP ELFs, `bento_*` attributes in project.xml, a visible CPU limit).
Scope decisions (Jeff, 2026-11-07):
- **Deferred:** Mod Matrix and the External tracks' mod sources (INST rows, EXT_BUILD/MX hooks). Note Mod on
  the SEQ page stays in, so the ModCfg plumbing it shares is ported, just not those screens.
- **Must keep:** strike velocity (drum sequencer), even though its HEADER_GEN hook changed.
- **Dropped:** PLAY+pad mute; pad mutes are in the official firmware.
- **After the synths:** per-track EQ/comp/clip strip.

Release notes (1.6.8, read 2026-11-07) that touch our work:
- "Sequenced note-offs land at the right time within the audio block": fits the changed function around
  our Note Mod note-off site 0x080605D2 (now 0x56 similarity). Re-check nm_off's two sites in S3.
- "The audio output no longer crunches every so often": likely the DMA buffers moved into the new
  non-cacheable D2 region. Some crackle we chased on 1.6.1 may have been stock's; compare by ear in S0.
- "Choke groups are per track", "Launch State shown on Pads", "Notes added on a muted pad are gray":
  pad and LED paths changed; watch strike velocity and the pad LEDs in S5.
- "Pads Can Trigger all Slices" and Shredder fixes: slice mapping changed, relevant to Note Mod on slicers.
- Known issue: External dashboard MIDI In/Out indicators don't work (stock, not ours).

## Approach
- **Generated address tables.** The matcher maps each 1.6.1 address; a reviewed table per base
  (`addrs_161`, `addrs_168`) feeds build.py, the C sources (generated header) and the tests. The
  expected-byte checks at every patch site stay: a wrong address can't reach the device.
- **Keep 1.6.1 building** until 1.6.8 reaches parity on the device. Strong regression check: the 1.6.1
  build must stay byte-identical to v3.72 through the refactor. Then freeze 1.6.1.
- **Slice move (done in S2, see below):** AXI 0x2404D000..0x24050000 on 1.6.8 only. The M4 MPU maps it
  as Device, so all M4 code is built `-mstrict-align` on 1.6.8 (zig cc ignores `-mno-unaligned-access`);
  the M7 side stays write-back cached with the same cache maintenance. SDRAM (a larger ext-pool
  reservation) was the alternative; RAMCHK verifies AXI on the device.
- **Synth RAM:** the D2 blocks we had in mind are gone and AXI is taken by the slice, so synth state goes
  in SDRAM (a larger ext-pool reservation, cached on the M7). Plan it before S6.

## Sprints
- **S0, Jeff on stock 1.6.8** (before any port): full card backup; flash 1.6.8 and check rollback to
  v3.72 works; hash a v3.72 project, load it on 1.6.8 without saving, power off, compare hashes (does load
  write?); save on 1.6.8, check be_step_v1 is dropped as expected and whether curves.bnt survives; reopen
  that save on v3.72; Save As and Pack, check whether curves.bnt is copied; input levels and settings
  after rolling back; play SPACED for a baseline by ear.
- **S1, tooling:** matcher into `tools/`, reviewed tables, build.py `--base`, generated header, tests
  read the table. Exit: 1.6.1 build byte-identical to v3.72, all suites green on 1.6.1.
- **S2, memory + black box on 1.6.8:** slice to AXI, d2a/d2b dropped, REG_KEYS 498, MPU and allocator
  checks updated, version label. Exit: device boots 1.6.8 + black box, logs clean, RAMCHK quiet.
  **Milestone M1.** Offline part done (2026-11-07, section "S2" below); kit
  `flash\bento_enhanced-v4.00-bb168` with `DEVICE_CHECKLIST.txt` (optional second kit `-bb168s` with the
  stall hooks); the device run is open.
- **S3, M7 features (moved only):** poly cap, retriggers, conditions, curve playback, Note Mod engine
  (the Mod Matrix runtime is deferred with its screens). Suites green on 1.6.8. Offline part done
  (2026-11-07, section "S3" below); kit `flash\bento_enhanced-v4.01` (BE4.01); the device run is open.
- **S4, M4 features (moved only, plus key renumbering):** inspect a step, hold edit, defaults, per-step
  cond/retrig UI, AT Rec/Out keys.
- **S5, changed hooks:** HEADER_GEN (strike velocity), CTRL consumer (hold shortcuts), MIDI 0x5F/0x60
  handlers (register remap, note > 127 check), LIVE_SITE (router +0x40, live AT). EXT_BUILD and MX
  (Mod Matrix, External mod rows) are deferred. Device regression on SPACED. **Milestone M2: parity.**
  S4 and S5 were merged into one sprint (Jeff). Offline part done (2026-11-08, section "S4+S5" below): a plain
  1.6.8 build is BE4.10, everything but the deferred `mx` group; kit `flash\bento_enhanced-v4.10`; the device
  regression on SPACED (checklist-v4.10.txt) is open.
- **S6, synths:** SDRAM and CPU budget, then the first drum engine. **Milestone M3.**

## S2: black box on 1.6.8 (2026-11-07, offline; device check open)
Evidence: scratch `rc17/` (investigators' scripts, `v1scan.py` literal/movt scan, `v2dis.py` allocators,
`v3words.py` words into our RAM, `v4boot.py` startup emulation, `v5mpu.py` MPU emulation, `v6ring.py`,
`mut2.sh` cache mutations; after review `s3/`: `ev.py` entry checks, `offs*.py` field scans, `mut.sh`).
Reviews are in `tools/addrmap/facts_161_168/facts.py` REVIEWED.

**Where the slice went: AXI 0x2404D000..0x24050000, 1.6.8 only** (1.6.1 keeps D2 0x3003C000, byte-identical).
- Free in both versions: M7 .data/.bss 0x24000000..0x2401EDC0, heap ..0x24048DA0, stack ..0x2404CDA0 (vector
  SP); stock's own check 0x080A03F0 treats [stack top, 0x24050000) as slack. Both top-down AXI allocators
  (M7 0x0804AFC0, M4 0x08104EB0) start at 0x24080000 and cap at 0x30000 (floor 0x24050000; checked by
  disassembly). No literal or movw/movt points into it except 0x2404CDA0 and the R2 base 0x24050000; the 8
  aligned words that fall in it are instruction encodings no load reads (`OUR_RAM_WORDS`). Disk-mode image
  (0x080E0000) uses 0x24000000..0x24029898 and 0x24058000.. only. Emulated (test_slice): both cores' stock reset
  handlers, .data copy, .bss zeroing and constructors up to main, write nothing in our RAM (M7 writes nothing
  between its stack top and SystemInit's temporary stack at 0x2407F000..).
- Device evidence (1.6.1): RAMCHK `axi` over 0x2404CDC0..0x24050000 changed=0 in all 78 logged readings,
  across bootloader flashes and warm resets. On 1.6.8 that is S0's free check (first v3.72 boot after stock
  1.6.8) and S2's RAMCHK. The bootloader image isn't available, so it was not read.
- Why 0x2404D000, not 0x2404CDC0 (the table investigator proposed 0x2404CDC0, the memory investigator
  0x2404D000; I took the latter): RAMCHK `axi` 0x2404CDC0..0x2404D000 stays a guard band between the M7 stack
  and the slice, the slice gets 4 KB spare (uses +0..+0x2000; RAMCHK `top` covers the rest, as 1.6.1's d2b),
  and a 4 KB aligned base keeps an MPU fallback possible (R15 16 KB at 0x2404C000, SRD 0x03).
- Not SDRAM (grow the Region at 0xC5FFC000 down): retention there depends on the FMC being re-initialised
  and refreshed through the bootloader (unknown), the reservation only exists after poly's `region()` runs,
  and it is slower (~66 cycles an uncached read on 1.6.1). AXI has the device evidence.

**Cache strategy: unchanged code maintenance, no new MPU region.** Emulating stock's MPU setup (test_slice)
shows the M7 sees the whole slice through R1 (Normal, write-back, no write-allocate); on 1.6.1 D2 came
through the default map (write-back, write-allocate). Our code already keeps one writer per 32-byte line,
cleans every line the M7 writes for the M4 and invalidates each M4 line before the M7 reads it; no-write-
allocate only makes a missed write go straight to memory. test_slice's `M7Cache` models a write-allocate
write-back cache (stricter) and proves both directions:
- M7 -> M4: the black box's M7 producers reach the M4 through the model; with cleans ignored they don't.
- M4 -> M7 (added after review): as on the device the M7 runs first, so its hooks cache the M4 head and
  CAPCFG lines while they still hold garbage; then the M4 boots, the M7 traces 36 records over three M4
  drain rounds (two wraps of the 17-record ring: a stale M4 read index would stop it at 17), captures a press
  through CAPCFG and acks two LOADREQ advances. With invalidates ignored none of that happens.
- Mutations (scratch `rc17/s3/mut.sh`, rebuilt each time): deleting the M4-head, CAPCFG or LOADREQ invalidate,
  or the clean in `put()`, each fails test_slice (before the rework the three invalidates went unnoticed).
- Not modelled: h_ring's `DCIMVAC` on stock's ring line (+0x2340, M4-written only). The ring object is reached
  through the 0x1004xxxx alias, which M7 R5 maps shareable and uncached, so it has no effect there.
Not taken: a non-cacheable R15 (it means patching stock's MPU setup, uses the last free region, and makes the
M7-private hot lines uncached inside interrupt-masked hooks).

**Alignment.** The M4's MPU maps AXI as Device (R0 0x24040000 256 KB TEX0 C0 B1, identical setup at 0x081150E0,
1.6.1 0x08114D74), where an unaligned access faults. test_slice `M4Mpu` now runs stock's M4 MPU setup and
checks every line of our RAM: full access, Device in AXI, Normal write-back in D2. All M4 code is built
`-mstrict-align` on 1.6.8 (BASE_SETTINGS; zig cc 0.15.1 silently ignores `-mno-unaligned-access`, checked). For
this build the flag changes no instruction (the reviewer's diff: only the eabi attribute); it guards m4_step
for S4, where on 1.6.1 it would change one `strh`, which is why 1.6.1 doesn't use it. Every emulated test faults
an unaligned M4 access wherever the M4 sees Device memory (`ALIGN_RANGES`: 1.6.8's slice and, on both bases, the
RAMCHK `axi` block); test_slice shows the hook catches one. The 1.6.1 suites pass with it too.

**RAMCHK on 1.6.8:** `axi` 0x2404CDC0..0x2404D000 and `top` 0x2404F000..0x24050000; no D2 block (d2a would
sit on the M4 heap/stack, d2b on the SAI DMA buffers). `RAMCHK_D2A` is reviewed `absent` (new generator
status), `RAMCHK_D2B` keeps its 1.6.1 meaning (slice + 0x2000). The in-slice canary prints as "slice canary".
The names D2_SLICE / RAMCHK_D2B are 1.6.1's and read wrong on 1.6.8; renaming them touches both tables, every
source and test, so it waits for a sprint that changes those lines anyway.

**Build.** build.py now plans per patch group: an enabled group that isn't ported to the base (BASE_SETTINGS:
1.6.8 = trace, stall, irq30, bbox) or uses a REVIEW entry refuses the build and lists them; a disabled one is
left out of the image (not compiled). Machine-mapped entries without REVIEW (retrig's) don't make a feature
ported: that is S3-S5's check of each hook. Version per base: 1.6.1 3.72, 1.6.8 4.00 (BE4.00). New check:
`check_slice_assumptions`: our RAM above the M7 stack, clear of the whole M4 stack (`M4_STACK_BOTTOM` up to the
vector SP, after review; it only checked the SP before), no new word or movt into it. The manifest's `layout`
lists only the blocks the image has, and `IMAGE_END` is the image's real end (0x081BE000), when groups are left out.
Kit builds:
- `BENTO_BASE=1.6.8 BE_DISABLE=step,hold,cond,retrig,poly,inta,nmod BE_TAG=bb168` (`flash\bento_enhanced-v4.00-bb168`).
- Optional second step, same plus `BE_ENABLE=stall,irq30 BE_TAG=bb168s BE_LABEL=BE4stl`
  (`flash\bento_enhanced-v4.00-bb168s`): the four reviewed stall / IRQ30 sites patched in.

**Stall and irq30: off by default, and a separate kit.** The S2 brief lists stall/watchdog as part of the
black box. They stay default-off on 1.6.8 as on 1.6.1 because of Jeff's v3.20 bisect (stall hooks + SPACED
in PROJ crash-restarted and the card dropped out; BS-C without stall was fine). Both reviewers agreed with the
reason but pointed out the cost: with the M7 hooks unpatched, the device run can't check the four reviewed
1.6.8 sites, and "no M7 audio stall record" can't fire. So the default kit stays as is, and the `bb168s` kit
(label BE4stl) is an optional last step in the checklist, after the default kit passed. Jeff decides whether
to run it.

**Entries settled: 112 REVIEWED (+1 absent).** First pass (37): H_INPUT_SITE, STALL_PROGRESS_SITE,
STALL_CHECK_SITE, M4_WD_SITE, MPU_SETUP / _END / _SHA, ALLOC_BUMP_SHA, LOAD_RING, AUDIO_DMA_EVENTS, PANEL_BOOTED /
CRC / TYPE / BUTTONS, D2_SLICE / _END, RAMCHK_AXI / _END, RAMCHK_D2A (absent), RAMCHK_D2B, OUR_RAM_WORDS and the 16
event ids. Rework (75, review finding: entries the black-box build reads were only machine-mapped):
- the other trampoline and hook sites and their callees: H_SEND / H_RING / H_CONSUME_SITE, M4_BL_SITE /
  _TARGET, SAVE_WRITE_SITE, FILE_WRITER, FS_OPEN / READ / WRITE / CLOSE, UI_BUMP_ALLOC, M4_WD_SUPERVISOR,
  WD_REFRESH, MSG_PUMP, SYSTICK_HANDLER, HAL_INCTICK. Each is identical to 1.6.1 for 48 instructions with
  branches / PC-relative operands masked, and each site shows the same context side by side (`rc17/s3/ev.py`);
- the RAM the black box reads (25 words, literal counts equal in both images; M4_WD_COUNTERS +0xC), the ext
  pool figures and our zero-gap placement (M7_TRAMP .. CTRL_STARTUP);
- 20 new `offset` entries for the stock object fields m7_diag.c used as bare 1.6.1 numbers (MIDI_RING_RD/_WR
  +0x2340/+0x2360, LOAD_RING_*, LINK_TX/RX/BAD/VERSION, SPI_STATE, PASS_TIMER_LAST, AUDIO_BLOCK_*, AUDIO_GAP_*):
  each measured in 1.6.8's code (the main audio loop touches the same fields in the same order; the link's users
  and the HAL SPI state byte are identical), also in expected_values.py. h_ring's offsets are verified: M4 reader
  `add.w r0,sl,#0x2340` at 0x081053D0, M7 writer `[ip,#0x360]` after `+0x2000` at 0x08082F24, `movw #0x2360` at
  0x08082F4E;
- new: M4_MPU_SETUP (for test_slice) and M4_STACK_BOTTOM (for the build check).
What the build uses besides these is S1 `fact` entries with their own hand records (ALLOC_BUMP, APPEND_FROM,
EXT_POOL_END, PROBE_SCRATCH, REGION, VERSION_STR). REVIEW left: 131, all in groups not ported yet (UI_WALK_CTX,
UI_REQUEST, ACT_*, header, model and key entries, LIVE_SITE, NM_OFF_SITE_1, the M4 MIDI table, PROB_* ...).

**Known oddity in a black-box-only build (either base):** the M4 still points CFG->region at 0xC5FFC000 and
prints "curves started=.. sent=.." from it, though poly's `region()` never reserved it (only reads and M7
cleans, no writes). Those numbers mean nothing in this build; the checklist says so. Changing it would split
the diag code between bases and its tests for a cosmetic gain, so it stays.

**Exit (offline):** 1.6.1 sha 25877c59... = v3.72 kit, all suites unchanged, bisect builds identical; 1.6.8
black-box build passes every build check; test_diag (90, 7 skipped: stall/irq30 off, poly code absent) and
test_slice pass on it, and on the bb168s build. Device checklist in both kits.

## S3: the M7 features on 1.6.8 (2026-11-07, offline; device check open)
Evidence: scratch `rc18/` (investigators: `s3_table/`, `t/` table checks, `beh/` behaviour: `offtime.py`,
`fullplay.py`, `fd2.py`, `callmap.txt`, `allocs.txt`; implementer: `impl/fcmp.py` own masked compare of every
hooked function, `impl/callers.py`, `impl/stockcov.py` stock code each suite executes, `impl/mut.sh` mutants,
`impl/final.sh` the exit checks). Reviews in `tools/addrmap/facts_161_168/facts.py` REVIEWED (S3 block).

**Group split.** `poly` -> `poly_cap` (S3: the five msg_send sites -> h_note) + `poly_at` (S5: h_poly at
POLY_SITE and the M4 MIDI 0x5F/0x60 handlers); `cond` -> `cond_engine` (S3: gate + loop hooks) + `cond_ui` (S4:
the M4 PROB_* clamps); `inta` -> `inta_curve` (S3: curve playback on internal instruments, a code path inside
retrig's curve start; `-DBE_NO_INTA_CURVE`) + `inta_live` (S5: LIVE_SITE). The old names are aliases (`ALIASES`
in build.py): disabling one disables both halves and still passes `-DBE_NO_<OLD>`, so every 1.6.1 bisect builds
the same bytes. The poly_at trampoline is still emitted before the five poly cap stubs (1.6.1 layout unchanged).
Ported to 1.6.8: black box + `poly_cap retrig cond_engine nmod inta_curve`. Kit build:
`BENTO_BASE=1.6.8 BE_DISABLE=step,hold,cond_ui,poly_at,inta_live` (version 4.01, BE4.01). A default 1.6.8 build
is refused and lists the five S4/S5 parts with their unreviewed entries; each S3 part builds alone (the others
disabled). What each part does alone, on the device:
- `poly_cap`: complete with no other part and no M4 code (the black box core publishes the Region address).
- `retrig` (+ External curve playback), `inta_curve`, `nmod`, and conditions stored in the per-step word: in
  stock's path and harmless, but inactive until S4/S5, because their data comes from m4_step.c: the per-step
  word (be_step_v1, which stock 1.6.8 drops on load), the curve pool, the ModCfg block / Note Mod settings
  (published from strike_toolbar, the HEADER_GEN hook, S5). `nmod` and `inta_curve` also need retrig's two hooks.
- `cond_engine`: conditions still in the probability byte (100..173, pre-v3.3 projects) play.
**Open scope decision (Jeff; review called it a scope blocker, not a safety one):** no M4 code in S3. The
task said the M4 code these groups need that is "moved only" comes along, and that Note Mod's ModCfg plumbing is
ported. That plumbing (`mod_cfg`, `mod_cfg_block`, `strike_toolbar`) and the be_step_v1 load / save sites
(STEP_LOAD_SITE, STEP_SAVE_SITE, both status "moved") live only in m4_step.c, which is not in the 1.6.8 image. So on
BE4.01 nothing writes MODHEAD (SLICE+0x1FC0): Note Mod's config() returns 0 and Note Mod / F1 never act;
retriggers, curves and word conditions never get data. Four of the five S3 parts are dormant on the device and the
checklist can only check that they change nothing. Choose: (a) accept S3 as poly cap + dormant engines (the
offline evidence above stands) and do the m4_step.c cut first in S4; or (b) pull a 1.6.8-only M4 sub-part forward
into S3. The implementer's preference is (a). Option (b), a 1.6.8-only M4 sub-part
(be_step_v1 load/save, the curve pool, mod_cfg called from M4_BL_SITE), would make retriggers, curves and
conditions work on the device in S3, but it means cutting m4_step.c into sub-groups (`plan()` collects every `S_`
name a source uses, so today its S4/S5 entries block all of it) while keeping 1.6.1 byte-identical; that cut is
S4's first job. Mod Matrix runtime: m7_mod.c's F1 engine is compiled (it shares the file with Note Mod) and
inactive without the M4's ModCfg.

**Sites re-derived (34 entries reviewed, plus 3 new).** Every function we hook or call is the same code moved
(own masked compare `impl/fcmp.py`: 0 instruction diffs for note_route, sequencer Note Off, pad Note On / Off,
the timed-note processor (one flash literal), loop boundary, all-off, msg_send, External handler (word jump
table moved), track ctor, bus_post, tick helpers, voice start; the sampler's head up to its jump table), each
site at the same offset in its function, the same caller counts (Note Off 2, note_route 1, processor 1, loop 2,
all-off 6, player 1). Frame offsets the trampolines read (gate: track r8, byte sp+0x29, word sp+0x3C; accept:
pad sp+0x2A, length +0x30, tick +0x20, router at entry sp; emit: router 5th argument) are in identical code.
New entries (stock_161.py by hand + fingerprints, 1.6.8 reviewed): `PAD_NOTE_CLAMP` (the reason for the note
change below), `SEQ_PLAYER` and `SAMPLE_TO_TICK` (test_s3_m7 runs the player), and after review `PAD_NOTE_ON`
(0x080602A0, 1.6.1 0x0805FF00) and `PAD_NOTE_OFF` (0x080603E0, 1.6.1 0x08060040), used only by test_s3_m7. Entries the S3 build uses: 138,
all reviewed (129), hand-checked facts (8) or absent (1); none only machine-mapped.

**The note-off change (release note "sequenced note-offs land at the right time within the audio block").**
NM_OFF_SITE_1 (0x08060976, 1.6.1 0x080605D2) is in the pending note-off pass of the player. 1.6.1 passed
timing = (block start + samples per block) - off (`subs r2,r2,r1` at 0x080605C0: mirrored in the block); 1.6.8
passes max(0, off - block start) (`subs r2,r2,r0` 0x08060960, `bic r2,r2,r2,asr #31` 0x0806096A). The call is
otherwise the same (track, note entry+4, timing, pad entry+2, router [sp]). Our off stub saves r0-r3, nm_off
rewrites only the note and pad, so a moved note's off keeps stock's (now right) timing and goes to the moved
pitch on the right bus key: no code change. Shown on stock code of both bases (test_s3_m7, whole player): a note
at 10, length 100, 64-sample blocks: off at 82 on 1.6.1, 110 on 1.6.8; with Note Mod the off is at the same
sample, on note 64, key 0xF4; two overlapping notes keep their own pitches (1.6.1 also swaps their order).
NM_OFF_SITE_2 and NM_ALL_OFF are unchanged code with unchanged callers; no new Note Off caller exists.
Our own events (retrigger hits and closes, curve samples) already used "time - block start": they now agree
with stock's offs.

**Other base differences.**
- Notes above 127: 1.6.8's note clamp (PAD_NOTE_CLAMP) allows 0..163 (1.6.1: `usat #7`), and its M4 drops MIDI
  notes above 127 (release note). Tracking one as note & 0x7F would let note 140 steal or swallow a real 12
  (a hanging note). New per-base flag `BE_NOTES_ABOVE_127` (BASE_SETTINGS `m7_flags`, 1.6.8 only, so 1.6.1 stays
  byte-identical): h_note passes such a note through untracked, retrigger MIDI hits skip it, curves are not
  started for it, nm_on leaves it unshifted. Not done: h_poly / live AT (S5 has to do the same).
- Play count: in the player's count-on mode (track byte +0x30) 1.6.8 wraps the step count after 256 steps and
  calls the loop boundary: cond_loop counts that as a pass (1.6.1 never did). Normal looping is the same.
- Sampler, voice start, pad Note On/Off, choke-per-track, all-slices. Evidence, per function: pad Note On / Off
  identical (whole function); voice start identical over its first 442 instructions; the sampler's message loop
  (SAMPLER_PROCESS) compared case by case over its message jump table (review, `rc18/rev/jt.py`, rerun as
  `rc18/fix/rev_copy/sampler_cases.py`): every case body at the same function offset, 0 masked diffs, except cases
  0x3E and 0x3F (4 and 5 diffs, shorter on 1.6.8). The Note On (0x12) and Note Off (0x13) cases our Note Mod and
  inta_curve posts reach, and 0x0E (internal AT, curves), are identical; 0x3E / 0x3F are messages we never post,
  probably where a release-note change lives (not identified). So: the paths we post into are unchanged, not
  "nothing in the sampler changed". (S3 first said "nothing we hook or call changed" from the head of the sampler
  only.) Where choke-per-track and the slice changes live was not found; Note Mod on a slicer with more than 16
  slices (nm_drum treats pad > 15 as melodic, drum wrap mod 16) is a device / S4 check.
- Ext pool / Region: check_region_assumptions holds on 1.6.8 (allocator, pool start values, counter literals,
  MPU R8 setup), both images make the same ext-pool allocations from the same sites, 232 bytes less of fixed size
  on 1.6.8, and the pool is never reset. Not added: a literal count over the counter window
  (0x24000154..0x2400016C is the table of several pools' counters, with users in both images; it would need its
  own record of every user) and the pool-left getter's caller count (a read-only getter). The allocator's
  sha and both counters' literal counts already catch a new writer.

**Tests.** New suite `test_s3_m7.py` (16): the whole stock player SEQ_PLAYER per block (an f64 shim for its
double instructions, which Unicorn's M7 lacks) at each base's addresses with all our hooks in it: note-off
timing (stock, Note Mod internal and External, two notes), loop count, a condition word and a legacy byte 1:2,
a retrigger word, the play-count boundary, the poly cap's steal / swallow; stock's note clamp; notes above 127
(1.6.8 only); the group split. Per-base expectations in `expected_values.BEHAVIOUR`, from the disassembly. Each of
four mutants fails it (no BE_NOTES_ABOVE_127, nm_off a no-op, the off stub clobbering r2, cond_loop not counting).
Changes: test_polyat skips its S5 classes when poly_at / inta_live are left out; test_conditions skips the M4
clamps when cond_ui is; test_retrig's worst-case tracks moved off the 1.6.8 slice (tracks 6/7 were on CFG /
CURVES) with an overlap assert; test_slice's refusal tests follow the split; test_diag's "no M7 code" test
looks for the code itself. 1.6.1 counts unchanged.
After review, test_s3_m7 has 23 tests (1.6.1: 5 skipped, the notes-above-127 class): added stock's pad Note On /
Off run whole (new entries PAD_NOTE_ON / PAD_NOTE_OFF: the device checklist's 7-pad test is this path, which no
suite executed before) with steal + swallow and a double Note On; notes above 127 on the pad path, on a retrigger
word (no MIDI hit) and a curve word (no player), each with a note-60 control that shows the feature does act.
Mutants (`rc18/fix/mut.sh`, each fails it): retrig guard removed, curve guard removed, h_note guard removed,
wrong track register at the pad On / pad Off stub, swallow disabled.
**Which suite proves what on 1.6.8.** test_s3_m7 is the only proof that the condition and retrigger sites and
the frame offsets their trampolines read are right on 1.6.8: test_conditions and test_retrig call our hooks with
frames they build themselves, so a wrong COND_LOOP_SITE (review mutant: the next push.w {r4-r9,lr} function,
fingerprint recomputed) or a wrong cond word offset (sp+0x3C -> 0x38) leaves test_conditions green. They are
evidence for our code's logic on both bases, not for the 1.6.8 sites. Likewise test_notemod_route (sampler,
bus_post, the processor pass) is the proof for Note Mod's pad-key field, and test_s3_m7 + test_notemod_route for
the Note Mod off stubs.
**test_notemod_real is deferred to S4** (exit criterion 3 named it; not met in S3, by scope): it is an M4 test
(the firmware's own drum / piano view objects, the step-UI Note Mod button, header and page gate) and has no M7
part to run alone. Everything it exercises is in m4_step.c (`step`, S4). The suite selection in test_m1.sh
(versioned now: `tools/lab-build-wsl/test_m1.sh`, NEEDS table) prints "not run" for it on the S3 image. Its
stack at 0x3003F000 (in 1.6.8's SAI DMA buffers) has to move when it is ported.
Stock code each suite executes on the 1.6.8 build (impl/stockcov.py, distinct basic blocks outside our code):
test_s3_m7 265 (player, processor, pending pass, note_route, Note Off, all-off, loop boundary, msg_send, track
setup) and test_notemod_route 171 (as test_s3_m7 without the loop function, plus bus_post and the sampler) are
the evidence; test_slice 1158 (reset handlers, MPU setups) and test_diag 17 are S2's; test_polyat 8 (the five
sites, msg_send), test_notemod 22, test_retrig 8, test_conditions 2 (the gate and the loop entry) touch stock
code at the sites only; test_mod 1. test_notemod_real (M4 views, step group) does not run on an S3 build.

**Entries settled in S3:** the 34 in the S3 block (POLYCAP_* x5, MSG_SEND, NOTE_ROUTE, RETRIG_ACCEPT/EMIT_SITE,
TICK_TO_OFFSET, BUS_POST, NM_NOTE_OFF, NM_OFF_SITE_1/2, NM_ALL_OFF, COND_GATE_SITE, COND_SKIP, COND_LOOP_SITE,
POLY_SITE, POLY_RESUME, POLY_DISPATCH, TRACK_CTOR, TRACK_MSG_LOOP/_END, SEQ_PLAY_PASS/_END, SAMPLER_PROCESS,
SAMPLER_NOTE_START, VOICE_START, TICKS_PER_BLOCK, TICK64_ADD/COPY/MAKE, PRODUCER_GLOBAL) and the new
PAD_NOTE_CLAMP, SEQ_PLAYER, SAMPLE_TO_TICK. REVIEW left: 125 (131 - 6), all for S4/S5:
- cond_ui (3): PROB_PIANO_EDIT_BACK, PROB_PIANO_EDIT_SITE, PROB_SHOW_PIANO_SITE;
- poly_at (4): M4_LOOP, M4_NOTE_ON_CASE, MIDI_JT, MIDI_JT_FIRST; inta_live (1): LIVE_SITE;
- hold (42) and step (61, some shared with hold): the UI walk, header, model, key, page, button, action and
  Mod Matrix entries (`impl/needs3.py` lists them);
- used by no group (19, tests / emulator): DRUM_MENU_SITE/TABLE, GFX_CACHE_LDR, HEADER_BUILD, HEADER_BUTTONS and
  the button layout, HEADER_ROOT_INIT, HEADER_TPL_*, PAGE_SEQ_ALT, T2_LIST_SITE_1/2, UI_PAGES_CTOR/INIT, UI_WALK_CTX.

**Open after S3:** the device run (checklist-v4.01.txt); the M4 decision above; h_poly / live AT notes above
127 (S5); Note Mod on slicers above 16 slices; h_live and h_poly are compiled into the 1.6.8 image but not
reached (m7_polyat.c compiles whole; they read only reviewed / fact entries); the per-step word (note record
+0x14): on load, 1.6.8's note reader zeroes it for every item (`str r3,[sp,#0x38]` at 0x08130B3C, record at
sp+0x24) and writes it only for kind-3 items, never for notes (review), so loaded notes carry 0; notes created
from the UI or by recording are still unchecked (S4, before be_step_v1 writes words again);
**Play Count, for Jeff before S4:** should cond_loop count 1.6.8's extra loop boundary at the 256-step wrap of a
Play Count clip? Today it does, so an A:B condition in such a clip changes phase at each wrap (never on 1.6.1).
Inert in S3 except for legacy-byte conditions; the condition UI (S4) makes it reachable;
**SPACED data on 1.6.8:** any save on 1.6.8 drops be_step_v1, so the checklist saves only to a copy and restores
from the card backup on rollback;
**v4.00-bb168 is not rebuildable from the S3 tree** (label 4.01, BE_NOTES_ABOVE_127 changes the M7 code):
keep its kit, or rebuild from ce228f7; the M7 ModCfg reader still doesn't check the boot generation (old, low);
test_notemod_real's emulator stack 0x3003F000 lies in 1.6.8's SAI DMA buffers (S4). The M7 code still reads
stock track fields as bare offsets (0x31, 0x86D1, 0x86D4, ...): all are read by unchanged stock code here;
naming them waits for a base that moves one.
## S4+S5: v3.72 parity on 1.6.8 (2026-11-08, offline; device check open)
Jeff merged S4 and S5 into one offline sprint. Evidence: scratch `rc19/` (investigators `stepui/` header and views,
`stepdata/` be_step_v1 / Note Mod plumbing, `hold/` CTRL consumer and hold shortcuts, `s5parts/` poly_at / inta_live /
cond_ui / Play Count; implementer `impl/`: `bdis.py`, `callers.py`, `regscan.py`, `stk.py` disassembly helpers,
`vmoved0.py` masked compare of every moved entry, `regdump.py` registry builder run, `mut.sh` mutants, `stockcov.py` /
`cov.sh` coverage, `final.sh` the exit checks). Reviews in `tools/addrmap/facts_161_168/facts.py` REVIEWED (blocks
"S5", "S4+S5"); where two or three investigators re-derived the same entry independently (21 entries), facts.py
keeps each one's evidence; they agree on every value.

**Result.** A plain 1.6.8 build is the parity kit: `BENTO_BASE=1.6.8 build_m1.sh` -> BE4.10 (version 4.10), sha
5785b05e868ca442fa1c1fc32085c1382224f0808cbc4c3fed1d6e2d4d50ffb9, every group except `mx` (and stall / irq30 /
modtest, off by default as on 1.6.1). Kit `flash\bento_enhanced-v4.10` with `docs/checklist-v4.10.txt`. 1.6.1 still
builds v3.72 byte for byte (25877c59...), and every old `BE_DISABLE` bisect build is identical to HEAD's.
Every single-group `BE_DISABLE` build succeeds on 1.6.8 (poly_cap, poly_at, retrig, cond_engine, cond_ui, nmod,
inta_curve, inta_live, step, hold, trace, bbox, and the aliases poly / cond / inta / diag / diag7).
Entries the default 1.6.8 build uses: 307, all reviewed (293), facts (13) or absent (1); none only machine-mapped
(the 35 moved and 5 exact ones were reviewed this sprint: `vmoved0.py`, 0 masked diffs from each entry except
the UI_TICK_MS literal).

**Group split: `mx` out of `step`.** Jeff deferred the Mod Matrix page and the External INST rows. `mx` holds
EXT_BUILD, EXT_EVENT, MX_BUILD / ROUTE / EDIT / BAR / EVENT (+ EV_ENCODER for the replay); it is listed before
`step` in PATCH_GROUPS so the existing descriptions ("Per-step: External ...", "Per-step: Mod Matrix ...",
"Per-step: modulation routing ...") route to it and manifests keep their text. Its code stays in m4_step.c under
`#ifndef BE_OMIT_MX` (ext_build / ext_event / row helpers / the matrix code, ROW_NAMES, ext_toolbar becomes an empty
stub; DEST_TYPE / DEST_NUM in the generated step_tables.h). The define is passed only when the group is *omitted*
(not ported to the base), never for a bisect (OMIT_DEFINES in build.py), so a base where mx builds keeps its bytes;
`BE_NO_MX` (the investigators' first idea) would have been passed by `BE_DISABLE=step` and changed 1.6.1's bisect
images. plan() computes m4_step.c's entries twice: lean (with BE_OMIT_MX) for `step`, whole for `mx`, so step needs
none of the 19 mx entries. `BE_DISABLE=step` also disables `mx` (mx's hooks call into step's code): the same image
as before the split. 1.6.8 leaves mx out by default (`BASE_SETTINGS default_off`); `BE_ENABLE=mx` is refused and
lists its 19 unreviewed entries. Kept in step: mod_cfg / mod_cfg_block, Note Mod (nm_*), the SEQ page Note Mod button,
mod_defaults, the sidecar's MODS / NMOD records, claim() with all 11 keys (the key layout stays the same).
Consequence: External tracks keep Note Mod through the SEQ page button; the INST-page Note Mod row goes with mx.
**Decision (implementer, stepdata leaned the same way; Jeff to confirm):** without mx the F1 External modulation
engine sends nothing (m7_mod.c `mod_block_body` returns at once under BE_OMIT_MX). A v3.72 project's routings would
otherwise send CCs / bend / AT that no screen on BE4.10 can change; they still load and save, and play again on
v3.72 or once mx is ported. test_mod's engine classes need mx; `EngineOffWithoutMx` checks the silence.

**What 1.6.8 changed in our hooks, and what we did**
- HEADER_GEN (strike velocity, Note Mod button): recompiled prologue push.w {r4-r9,lr}; sub sp,#0xc (1.6.1
  {r4-r10,lr}; #8: the same 0x28 frame, the same arguments). The replay is chosen by the site's bytes
  (`2de9f047` / `2de9f043`), anything else refuses. Description and root grew 0x18: actions at +0x8C, armed flag
  +0x1194, buttons +0x564 (m4_step.c reads every offset from the table: no code change). Free slots on 0x27 are still
  3 and 4 (Strike Vel left of Undo), on 0x17 slot 0 (Note Mod).
- CTRL consumer (hold shortcuts): 1.6.8 copies the event record to sp+0x218 and keeps only a pointer in r6
  (`ldrb r3,[r6]; subs r3,#0x11` at the site); the wrapper is chosen by the site's bytes (`a5f11103` 1.6.1 registers,
  `3378113b` 1.6.8 record pointer), anything else refuses. Mixer pad-mode event 0x10C -> 0x10D, model fields pad
  LEDs 0x19C4 -> 0x19D0 and mixer mode 0x3C7C -> 0x45CC, keys Mute / Solo 0x13D/E -> 0x13B/C (all from the table).
- M4 MIDI task (live poly AT / AT Out): recompiled (channel r9, note r6, value r4, port r7, the sink from a literal,
  each case sets r5 and branches back; a new guard drops Note On / Off above 127). `note_on_case()` builds our 0x5F /
  0x60 handlers from BASE_SETTINGS `m4_note_on`; the native form assembled with 0x90 must equal the image's bytes
  between M4_NOTE_ON_CASE and M4_NOTE_ON_END, the sink literal must be M4MIDI_SINK, JT[0] the case. On 1.6.8 our
  0x5F handler drops a note above 127 as stock does.
- LIVE_SITE (internal live AT): same stub; the pad router now posts the pad's Note On note (router +0x34[pad],
  0..163). Notes above 127 (BE_NOTES_ABOVE_127, 1.6.8 only): h_poly sends no poly AT for them (as note & 0x7F it would
  press note N-128), h_live / int_post keep the full note (INT_NOTE 0xFF), so 140 and 12 are two entries.
- Probability clamps (cond_ui): no behaviour change (one stock fix in the timing path, outside what we replace).
- be_step_v1 save / load, NOTE_INSERT / UPDATE, DOC_READ: the same code (writer, reader, XML library identical);
  1.6.8's Save As saves into the new folder first (our sidecar hook writes a fresh curves.bnt there) and copies
  samples only; Pack reloads the project through the same load path (device check: unsaved edits and Pack).
- Model: MODEL_WORK 0x3C80 -> 0x45D0; the UI page set 0x689A98 -> 0x68B6D8 B (drum / piano views at +0x677738 /
  +0x2EF4D0, new entries UI_PAGES_SIZE / UI_PAGE_DRUM / UI_PAGE_PIANO for test_notemod_real).

**Play Count rule (Jeff's default).** In a Play Count clip (track byte +0x30) 1.6.8 adds a loop boundary at each
256-step wrap (player 0x080609C0 / 0x08060AE6, then the first boundary call COND_LOOP_CALL_1 0x0806072C). It must
not count as a pass: A:B keeps the 1.6.1 phase. `-DBE_PLAY_COUNT_WRAP` (1.6.8 only): the loop trampoline passes the
caller's lr (`ldr r1,[sp,#0x3c]`, after the 64-byte save) and cond_loop returns early when it is COND_LOOP_CALL_1 + 4
with +0x30 set and a count >= 0 (a restart, count < 0, still resets). The second boundary caller has no +0x30 test
(as 1.6.1), hence the lr and not the flag alone. test_s3_m7 `test_play_count_condition_keeps_the_161_phase`: on stock's
whole player, 2:2 never plays in a Play Count clip (without the fix it plays from step 256), 1:2 is the control; the
count test now expects 0 passes on both bases. Mutant (`[sp,#0x38]`) fails both.
**Correction (review round, `rc19/impl2`): track byte +0x30 is not Play Count.** The behaviour reviewer read the
rig as "2:2 never plays in a Play Count clip" and asked the checklist to expect that. Checking what the rig plays:
with +0x30 set a 16-step clip does not repeat at its length on either base (the note at tick 10 plays once in 320
steps; on 1.6.8 again only after the wrap), the mode exists in 1.6.1's player (Play Count is new in 1.6.8), and with
+0x30 set the track's message loop (codes 2 / 3 at 0x080629E0 / 0x0806298A) also posts live notes to the M4's
recording handler (REC_MSG_5 / REC_MSG_6, the producers above). So +0x30 is most likely open-length recording, and
the rule (the 256-step wrap is not a pass) is about that mode: unchanged, still 1.6.1's behaviour (1.6.1 never wraps).
Play Count clips presumably loop through the normal boundary, so A:B advances per repeat as in a normal loop; the
Play Count code was not traced. `test_count_on_mode_does_not_loop_the_clip` records the finding (with a +0x30-clear
control that loops); the checklist expects normal-loop behaviour for a Play Count clip and says what the other
outcome would mean. The tests keep their S3 names.

**The per-step word (+0x14) on 1.6.8.** Load: stock's reader zeroes it for every note (test_step_xml runs the reader:
stock alone drops the word, value2 of kind 2 survives). UI-created notes: drum Step add and Sequence page pad entry
both go through event 0xE9 (0x0813CAB0), which builds the record at sp+0x10 with +0x14 = 0 (`str r4,[sp,#0x24]` at
0x0813CAF0, r4 = 0) and passes it unchanged to NOTE_INSERT at 0x0813CC70 (no later store to that slot: `stk.py`).
NOTE_INSERT's other callers: automation insert (kind 2), the recording handler, and 0x0813CE5C (copies existing
records: the word moves with its note, intended). Recording: the handler copies words 0..5 of a 48-byte M7 message
(kind forced to 1) into the note, so +0x14 = message word 5. The message comes through a second M7 -> M4 ring in the
shared object (ENGINE_PTR + 0x2FC0, 64 x 48 B, read +0x3BC0 / write +0x3BE0; M7 enqueue 0x08082BA0, two callers:
the bus-mailbox forwarder 0x08057F60 and the engine-outbox forwarder 0x0808D540); the engine outbox's one writer
(0x0805A4E8) makes type 6 with word 5 = 0. **Recording shown (review fix, `rc19/impl2`):** the M4 consumer
dispatches a ring entry on its byte +0x18 (0x08140930: 1 -> the recording handler 0x08138B08, 6 -> 0x08138A04, which
inserts nothing), so the engine-outbox messages never become notes. The bus forwarder 0x08057F60 reads the M7 bus
(48-byte entries at +0xE48, count +0x1A48) with BUS_TAKE 0x0809FB40; the bus has one writer, BUS_APPEND 0x0809FCE0
(bl / b scan of the whole M7, both phases; no pointer to it; the other #0xe48 / #0xa48 stores are parameter-table
constructors), called only by REC_MSG_5 0x080602A0 and REC_MSG_6 0x080603E0. Both build the message with word 5 = 0
(`str r7,[sp,#0x1c]`, r7 = 0; and `strd r7,r7,[sp,#0x18]` with only the halfword at +0x18 rewritten) and +0x18 = 1,
instruction for instruction as 1.6.1 0x0805FF00 / 0x08060040 (so v3.72 always had 0 there too). test_step_xml
`RecordedNotes` runs both producers and BUS_TAKE on the build's M7 code with a 0xA5-filled stack: word 0 = 5 / 6,
+0x18 = 1, word 5 = 0 (mutant: the type-5 store removed -> 0xA5A5A5A5, caught). New table entries REC_MSG_5 / REC_MSG_6
/ BUS_APPEND / BUS_TAKE (reviewed, test only). The `-DBE_REC_WORD_LOG` black-box line ("INSERTS ... | recw n=..
nonzero=.. last=..", SLICE+0x1D50, the free 16 B between INSLOG and BINDS) stays on 1.6.8 as a device confirmation;
the checklist expects nonzero=0. Behaviour unchanged.

**Tests and rigs that run 1.6.8's own code** (distinct stock basic blocks on the default build, `impl/cov.sh`):
test_notemod_real 5501 (UI page set, drum / piano views, and new `HeaderThroughStock`: template loader, HEADER_TICK,
our HEADER_GEN wrapper, the generator body, strike_toolbar, stock's compare and HEADER_BUILD, the button widgets: Note
Mod slot 3, Strike Vel slot 4 lit, tabs and Undo kept; the step UI investigator's rig, finished by stubbing the RTOS lock
pair M4_LOCK_PAIR it recursed on), test_slice 1158 (S2), test_step_xml 442 (new: stock's note writer, XML library and
reader around our save / load sites; word round trip, legacy migration, value2), test_keys 409 (new: stock's registry
builder; every key we name has its 1.6.1 title / type / choice count on 1.6.8, REG_KEYS = 498, claim() takes 0x1E3,
0x1D9, 0x1D1..0x1CE, 0x1C8, 0x1C4..0x1C1, all free), test_s3_m7 282, test_notemod_route 171, test_controls 163
(`StockConsumer`, `StockButtons`, `StockMixer`: stock's consumer loop, button handler, mixer event / pad mode),
test_holdstep 127, test_step 84 (sites only; its header classes fake the generator), test_polyat 54 (`M4MidiTask`:
stock's MIDI-out task), test_notemod 22, test_diag 17, test_retrig 8, test_conditions 5, test_mod 1.
Counts (1.6.1 / 1.6.8): diag 90 s6 / 90 s6, polyat 45 s3 / 45 s2, conditions 18 / 18, holdstep 18 / 18, controls 62 /
62, retrig 43 / 43, step 95 / 95 s16 (ExtRows, ModMatrix need mx), mod 24 s1 / 24 s20, notemod 16 / 16, notemod_route
8 / 8, notemod_real 14 / 14, slice 20 s2 / 20, s3_m7 25 s5 / 25, step_xml 11 / 11, keys 4 / 4 (after the review round:
+1 notemod_real HEADER_ARMED, +1 s3_m7 count-on mode, +3 step_xml RecordedNotes; test_step_xml 442 includes 19 M7 blocks
of the recorder).
Mutants on 1.6.8 (`impl/mut.sh`), each caught: description actions at 1.6.1's +0x74 (HeaderThroughStock, and test_step's
NoteModSeq), load wrapper frame #96 (test_step_xml, 3 failures), save wrapper #76 (test_step_xml), consumer context
from [r6,#4] (test_controls), the 1.6.1 HEADER_GEN replay on 1.6.8 (build refuses), the recorded-word log keeping no
value (test_step). HEADER_ARMED at 1.6.1's offset survived the first round (in the rig stock re-applies a changed
description without our arm); the review fix adds `HeaderThroughStock.test_our_arm_is_the_flag_stock_reads_and_clears`:
every access to root+HEADER_ARMED is logged with its pc, ours must write 1, then stock must read 1 and write 0
(0x08182014) — the mutant now fails it, an unmutated control passes. The s5parts and hold investigators' mutants (h_poly guard, INT_NOTE, M4 guard,
channel register, loop lr offset; 1.6.1 consumer registers on 1.6.8, 0x10C on 1.6.8) were caught in their runs.
Rig memory: the display emulator's stack moved from 0x3003F000 (1.6.8's SAI DMA buffers) to the top of M7 DTCM
0x20020000: 128 KB no M4 code can address, so a deep call chain can't run into a stock global (I disagreed with
s5parts' M4 vector SP, a 0x1400 B stack with stock .bss below it, and with stepdata's 0x2003F000, outside the map).
test_diag's sidecar stack: M4_STACK_BOTTOM + 0x1310 (still 0x30039000 on 1.6.1).

**What remains**
- The device run (checklist-v4.10.txt; Jeff skips v4.00 / v4.01, so it starts with S0's go / no-go gate).
- `mx` (Mod Matrix page, External INST rows, F1 engine on): 19 REVIEW entries; the External view grew 0x1B50
  inside; stepui has evidence for EV_PAGE_CHANGE / EV_MX_TAKE_TRACK / EV_DASHBOARD(_ALT) (REVIEWED_MX_LATER, not
  merged); MXSRC_* and the matrix view layout are unmeasured.
- Pack with unsaved edits; PLAY+pad and the other hold buttons + pads
  on stock 1.6.8 (no button + pad combination found in 1.6.8's code); the claimed keys at runtime (the registry test
  covers the builder, not keys registered later); Note Mod on slicers above 16 slices (S3); 1.6.8's launch state on
  pads after an INST hold.
- M4 black-box block: 24092 of 24576 B used on 1.6.8 (484 B left).
- Hard-coded layout offsets outside the table (table review note): drum / piano view fields in m4_step.c (0x113DC..
  0x114C0, 0x11CD00, 0x125DAC, slot +0x2C8, bar +0x38 / stride 0x468), m4_controls.c pane +0x9A08, m7_controls_input.s
  receiver +0x1338 and the UI ring +0x2180 / +0x21A0. The reviewer scanned them: stock uses each the same way and
  the same number of times on both bases, so no defect on 1.6.8; the tests use the same constants, so no suite would
  catch a move. A next base needs that scan again, or these become table entries (not done here: it changes no
  1.6.8 byte and is a refactor across three sources).

**Review round (S4+S5).** Fixed: unknown `BE_DISABLE` / `BE_ENABLE` names are refused (build.py `KNOWN_NAMES`;
`inta_lve`, `stal` used to build the default image); the mx refusal says "drop mx from BE_ENABLE" instead of
suggesting `BE_DISABLE=mx`; test_notemod_real.py back to LF; HEADER_ARMED test; recorded notes' +0x14 shown (above);
checklist: Play Count expectation (normal-loop phases; the reviewer's "2:2 never" rested on +0x30 being Play
Count, which it is not), SPACED never loaded on 1.6.8 (computer copies
SPACED_S0 / SPACED_COPY, hashes before and after, hashes around the v3.72 open), a stock 1.6.8 baseline of every
hold button + pad, `rmidi` in the bisect list.
- After parity: S6 synths (SDRAM, CPU budget), then the per-track EQ / comp / clip strip.
**REVIEW list after S4+S5 (19, all mx):** EV_DASHBOARD, EV_DASHBOARD_ALT, EV_MX_REBUILD, EV_MX_SELECT, EV_MX_TAKE_TRACK,
EV_PAGE_CHANGE, EXT_BUILD, EXT_EVENT, MXSRC_ENV1, MXSRC_ENV2, MXSRC_KEY, MXSRC_LFO1, MXSRC_LFO2, MXSRC_NONE,
MXSRC_PRESS, MXSRC_VEL, MX_BUILD, MX_EDIT, MX_EVENT.
## Risks
1. Slice relocation: the M4's Device mapping of AXI faults on unaligned access; cache maintenance on M7.
   (S2: handled offline, see above; the device run confirms.)
2. Registry renumbering touches every UI feature and our claimed keys.
3. About 440 address references: hand edits would introduce errors, hence generated tables and byte checks.
4. Unknown until S0: whether 1.6.8 writes to a project on load, and what Pack/Save As do with curves.bnt.
5. Less flash headroom (32 KB) and no spare D2 for synths: SDRAM only.
6. 1010music may ship another update during the port; the table approach is the mitigation.
7. Tests: every rig hard-codes stock addresses; until they read the table, a green suite on 1.6.8 means nothing.
   (S3: they read the table; only suites that execute 1.6.8's own code are evidence, see the S3 list.)
8. S3 features other than the poly cap stay inactive on the device until their M4 data paths are ported (S4/S5). (S4+S5: ported; BE4.10 has them all, mx excepted.)
