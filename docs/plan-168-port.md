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
- **Slice move:** proposed AXI 0x2404CDC0..0x24050000 (12.8 KB, unused by stock in both versions). Catch:
  the M4 MPU maps it as Device, so M4 code must never access it unaligned (build with
  `-mno-unaligned-access` and check packed structs); the M7 side stays write-back cached. Alternative:
  grow the ext-pool reservation at the top of SDRAM, which is cached on the M7 too but would put state
  the black box must read after a reset in SDRAM. AXI preferred; RAMCHK verifies it on 1.6.8.
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
  **Milestone M1.**
- **S3, M7 features (moved only):** poly cap, retriggers, conditions, curve playback, Note Mod engine,
  Mod Matrix runtime. Suites green on 1.6.8.
- **S4, M4 features (moved only, plus key renumbering):** inspect a step, hold edit, defaults, per-step
  cond/retrig UI, AT Rec/Out keys.
- **S5, changed hooks:** HEADER_GEN (strike velocity), CTRL consumer (hold shortcuts), MIDI 0x5F/0x60
  handlers (register remap, note > 127 check), LIVE_SITE (router +0x40, live AT). EXT_BUILD and MX
  (Mod Matrix, External mod rows) are deferred. Device regression on SPACED. **Milestone M2: parity.**
- **S6, synths:** SDRAM and CPU budget, then the first drum engine. **Milestone M3.**

## Risks
1. Slice relocation: the M4's Device mapping of AXI faults on unaligned access; cache maintenance on M7.
2. Registry renumbering touches every UI feature and our claimed keys.
3. About 440 address references: hand edits would introduce errors, hence generated tables and byte checks.
4. Unknown until S0: whether 1.6.8 writes to a project on load, and what Pack/Save As do with curves.bnt.
5. Less flash headroom (32 KB) and no spare D2 for synths: SDRAM only.
6. 1010music may ship another update during the port; the table approach is the mitigation.
7. Tests: every rig hard-codes stock addresses; until they read the table, a green suite on 1.6.8 means nothing.
