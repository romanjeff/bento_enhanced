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
- **S3, M7 features (moved only):** poly cap, retriggers, conditions, curve playback, Note Mod engine,
  Mod Matrix runtime. Suites green on 1.6.8.
- **S4, M4 features (moved only, plus key renumbering):** inspect a step, hold edit, defaults, per-step
  cond/retrig UI, AT Rec/Out keys.
- **S5, changed hooks:** HEADER_GEN (strike velocity), CTRL consumer (hold shortcuts), MIDI 0x5F/0x60
  handlers (register remap, note > 127 check), LIVE_SITE (router +0x40, live AT). EXT_BUILD and MX
  (Mod Matrix, External mod rows) are deferred. Device regression on SPACED. **Milestone M2: parity.**
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
## Risks
1. Slice relocation: the M4's Device mapping of AXI faults on unaligned access; cache maintenance on M7.
   (S2: handled offline, see above; the device run confirms.)
2. Registry renumbering touches every UI feature and our claimed keys.
3. About 440 address references: hand edits would introduce errors, hence generated tables and byte checks.
4. Unknown until S0: whether 1.6.8 writes to a project on load, and what Pack/Save As do with curves.bnt.
5. Less flash headroom (32 KB) and no spare D2 for synths: SDRAM only.
6. 1010music may ship another update during the port; the table approach is the mitigation.
7. Tests: every rig hard-codes stock addresses; until they read the table, a green suite on 1.6.8 means nothing.
