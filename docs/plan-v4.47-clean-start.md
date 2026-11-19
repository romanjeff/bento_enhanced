# BE4.47: clean start on every boot

Jeff's decisions (2026-11-18), after BE4.46 reset-looped on the device before the boot screen (quick light-up, reset,
repeat; with or without USB, with the SD card out; stock 1.6.8 booted; a real power cut always cleared it; not
reproducible later):

- **R1 start clean on every boot.** Nothing of ours reuses or trusts RAM left by an earlier run, whatever its magic or
  generation words say. Everything starts from scratch at boot. The project file is what persists ("that's what project
  save is for").
- **R2 no dependence on dates, times or the backup domain.** The RTC and its backup registers may be printed as untrusted
  data, never used for control flow, indexing, division, loop bounds or pointers.
- **R3 the black box's log files start fresh when missing**, and missing or odd SD state never blocks or crashes boot.

Labels: **S** = source or disassembly, **M** = measured (emulated), **I** = inferred.

## 1. What changed (the short version)

| Where | Change |
|---|---|
| build.py `clean_start` (core, every build) | The M7 reset handler, at stock's C++ init-array loop (CTRL_STARTUP_SITE 0x080404B6, after stock's .data / .bss setup), zeroes all of our internal RAM: AXI 0x2404CDC0..0x24050000 (RAMCHK axi, the slice, RAMCHK top), 12.8 KB, then inits the hold shortcuts' state line (as before), replays the two displaced literal loads and returns. The stub was the hold group's (state line only); it is core now. |
| m4_diag.c `poll` | A new boot is "the M4 header has no magic" only. v4.46 also took "the HAL tick went backwards" (`now < r[3]`) and otherwise trusted the header as the last run left it (section 3). |
| m4_diag.c `init` | No "previous boot" printing (the list in section 6). The canary and RAMCHK patterns are armed every boot. |
| m4_diag.c `poll` | Watch 1 (stock's crash count in a backup register changing mid-session raised an alarm) is gone (R2). CFG r[1] no longer holds a backup register. |
| m4_diag.c audio monitor | No RTC kept for the next boot; its snapshot is boot-local. |
| m4_diag.c `counters` | Follows the Region's stats only once the M7 has taken the Region this boot (SYNTH_TAKE). |
| m4_diag.c `dump_after_save` | R3: a save that finds BENTO_SAVES.log or BENTO_CRASHES.log gone starts the new file with this boot's header or line (`fresh ...`). |
| m7_polyat.c `synth_take` | The whole 32 KB synth area is zeroed at the take (v4.46: only the M7's lines) and its generation is 1 on every boot (v4.46: the old header's + 1). |
| m4_synth.c `ready` | The M4 writes the synth area only once SYNTH_TAKE (a slice word the M7 writes right after the take) says the area was taken this boot. |
| m7_diag.c | `d0_unreported_reset` (kept the last run's D0 figures for the next boot's print) is gone. |
| Version | BE4.47 on the boot screen, BASE_SETTINGS 4.47; default build otherwise as v4.46. |

Why one zeroing stub on the M7 and none on the M4 (the choice was close):
- Every reset of this board is a system reset, so both cores restart together. Stock sets both window watchdogs' reset
  scope to the whole system (S: M7 0x080A1000 `movs r0, #1; ... bl 0x08047F20` ORs RCC_GCR (0x58024400 + 0xA0) with 1;
  M4 0x08108542 `movs r0, #2; bl 0x081196F4` with 2: HAL_RCCEx_WWDGxSysResetConfig, WW1RSC / WW2RSC). Faults reset with
  SYSRESETREQ (S: 0x05FA0004 at 0x0804B0A8, 0x08106754, 0x08107B24). So "an M7-only reset" or "an M4-only reset", which
  v4.46's generation logic was built for, does not happen (I, strong: the RCC_GCR bits from the HAL function's shape and
  the reference manual).
- The M7's reset handler runs right at reset and is the earliest code of ours on either core. None of our M4 code runs
  before the M4's UI task, except `an_forget` from the OBJ_CTOR hook during the M4's static constructors (S: m4_synth.c),
  which only clears drum-table entries or reads: a race with the zeroing leaves zeros or entries that `dk4()` re-zeroes
  (its magic is gone). An M4 stub as well would have meant a second patch in the M4 reset path for no gain.
- The rejected alternative was keeping the generation logic and fixing its holes one by one (section 3 shows two). That
  keeps exactly the class of bug that caused the loop.
- 1.6.1 (legacy, v3.72) keeps its slice in D2 SRAM, whose clock the M7 reset handler cannot assume: only its AXI block
  is zeroed there, and its old behaviour otherwise stays. Nothing ships on 1.6.1.

## 2. State inventory (S) and dispositions

Our RAM on 1.6.8 (build.py `our_ram`, bases/stock_168.py): AXI 0x2404CDC0..0x2404D000 (RAMCHK axi), the slice
0x2404D000..0x24050000 (offsets below; its upper part from +0x2000 holds DK4 and RAMCHK top), and in SDRAM the M1 Region
0xC5FFC000 (16 KB), the synth area 0xC5FF4000 (32 KB), the price-check scratch 0xC5FF0000 (8 lines, read only), plus
the M4's per-boot allocations from stock's UI bump allocator. **No DTCM, no SRAM4 (0x38000000), no backup SRAM
(0x38800000) of ours**: our code is linked without .data / .bss (build.py refuses any) and no source names those
addresses (S: grep). "v4.46" says what survived a reset and how it was trusted; "4.47" what it is now.

| Slice offset (owner) | What | v4.46 across a reset | 4.47 |
|---|---|---|---|
| +0x000 AMON (M4) | audio DMA monitor, stall snapshot, RTC at the last pass | snapshot kept by magic, printed next boot; RTC kept | zeroed at reset; boot-local; no RTC |
| +0x080 ST7 (M7), +0x090 STRIKE_PEAK (M7) | M7 stall tracking and record; per-pad peak countdown | record printed by the next boot (magic); tracking re-armed by the CFG generation | zeroed; record boot-local, not printed |
| +0x100 M7H (M7), +0x140..0x1C0 dedupe / flags (M7) | trace header, dedupe table | trusted while GEN == the M4's generation | zeroed |
| +0x120 M4H (M4) | black box header: gen, buffer, length, last poll ms | **trusted unless magic was missing or the tick went back** (section 3) | zeroed; new boot = no magic |
| +0x1C8 region cache, +0x1D0 GEN, +0x1D8 SYNTH_TAKE (M7) | the Region pointer and generation, the take word | cache trusted for the same generation; take count "since the slice was new" | zeroed; SYNTH_TAKE is now the M4's proof of this boot's take |
| +0x1E0 CFG (M4) | config line: magic, region, boot generation, crash count at boot, heartbeat watch | **the generation every hook of both cores compares with**; stale before the M4's first pass | zeroed; generation 1 every boot; r[1] spare |
| +0x200 RINGSTAT (M7), +0x2C0 RECS (M7) | ring stats, heartbeat, 17 trace records | printed as "previous boot" by the next boot | zeroed; not printed |
| +0x660 COUNTERS, +0x860 CHEAD (M7) | conditions counters | re-inited once per M4 generation | zeroed; same code inits them |
| +0x880 SU (M4) | step UI keys | re-claimed per generation | zeroed |
| +0x8A0 CTRL_STATE (M7) | hold shortcuts | inited by the startup stub | zeroed, then inited by the same stub |
| +0x8C0..0x948 controls S / INST / BOOT / UI / LIGHTS (M4) | hold shortcuts UI | re-inited per generation | zeroed |
| +0x948 STRIKE_SET (M4) | Strike Vel setting, **pointer to the per-pad block in SDRAM** | pointer trusted with its check word and the block's generation | zeroed; reallocated per boot |
| +0x960 STRIKES (M7) | press counts, velocities | used as "old presses" baseline | zeroed |
| +0x9A0 D0A, +0x9E0 LOADREQ, +0xF20 PROBE, +0xF60 SCAN_HI, +0xF80 D0LINK, +0xFC0 D0C, +0xFE0 D0L (M7 / M4) | D0 load and memory figures, save stretch, link | the last run's figures kept for the next boot's print | zeroed; not printed |
| +0xA00 CANARY (M4) | 1.5 KB pattern | armed once, kept across resets | zeroed, armed every boot |
| +0xE20 AN4 (M4) | synth UI state (np_keys answer, counters) | kept by magic, counters reset per generation | zeroed |
| +0x1000 PROF (M7), +0x1400 MPROF (M4) | stall profiles | printed by the next boot | zeroed; not printed; worst gaps per boot still in every save |
| +0x1800 LOG_LIVE, +0x1804 CLOG_DONE (M4) | tests' opt-in, curve log cursor | kept | zeroed |
| +0x1820 CAPCFG (M4) | **pressure capture block pointer** (SDRAM) | trusted by the M7 with magic and check word, no generation | zeroed; republished at the M4's first pass |
| +0x1840 CURVES (M4) | **curve pool, table, save list, write buffer pointers** (SDRAM) | trusted for the same generation | zeroed; reallocated on first use |
| +0x1880 CURVELOG, +0x1C80 CLAIMED, +0x1D00 INSLOG, +0x1D50 RECWORD, +0x1D60 BINDS (M4) | curve and insert diagnostics, binds | reset per boot or per generation | zeroed |
| +0x1E80 LOADSTATE (M4) | the document being read (path, sidecar base) | sequence number carried | zeroed |
| +0x1F40 AT_SET, +0x1F80 AT_DEST (M4) | per-track AT settings, the M7's destination table | reset per generation; AT_DEST trusted by magic | zeroed |
| +0x1FC0 MODHEAD (M4) | **ModCfg pointer** (SDRAM) | trusted with check word and range | zeroed; reallocated per boot |
| +0x2000 DK4 (M4) | drum knob table | kept by magic | zeroed (drums are omitted on 1.6.8 by default) |
| RAMCHK axi / top (M4) | patterns | armed once, kept | zeroed, armed every boot |

| SDRAM | v4.46 | 4.47 |
|---|---|---|
| M1 Region (M7) | zeroed (its struct, 0x2440 B) at each take | the same (it was already clean) |
| synth area (M7 lines, M4 header and drum knob lines, drum cells and run state, X2 / cache lines) | only the M7's lines zeroed; the M4's lines and the drums' run state kept; generation = old header + 1 | all 32 KB zeroed, cleaned and invalidated at the take; generation 1 |
| price-check scratch | read and clean-invalidated only | the same |
| black box buffer (256 KB), capture block (33 KB) | reached through M4H / CAPCFG pointers of the last run (section 3) | allocated at the M4's first pass; the capture block zeroed, the buffer written before read |
| curve pool / table / save list / write buffer, ModCfg, PadState | reached through the last run's pointers when the generation matched | reallocated; table entries and pool written before read (S: m4_step.c), ModCfg and PadState zeroed |
| stock's Wavetable oscillator kernel area (+0x10C..+0x1CC, our AnOsc and AnProps) | read before written | **not changed** (section 7) |

**Reads of the RTC and its backup domain (all in m4_diag.c, S):**

| Read | v4.46 | 4.47 |
|---|---|---|
| RTC_TR / RTC_DR (0x58004000 / 04) in the boot header and each CRASHES line | printed as masked hex | printed as masked hex (untrusted, any value safe) |
| RTC_TR / RTC_DR into AMON every pass, printed by the next boot | kept | removed |
| BKP0..31 (0x58004050..) in the header, each save, alarms, CRASHES lines | printed as hex / decimal | printed (untrusted) |
| BKP24 (crash count) into CFG r[1] at boot and compared every pass (alarm, LOG_LIVE card write) | control flow | removed |

No other code of ours (M7, DSP, the other M4 blocks) reads the RTC, the backup registers or backup SRAM (S: grep for
0x5800xxxx, 0x388xxxxx). Our timing uses the HAL ticks and the cycle counter only.

## 3. The v4.46 defect (M, emulated; the likeliest root cause of the loop: I)

v4.46 decided "this is a new boot" from RAM: `poll()` re-inited the black box only when the M4 header had no magic, or
the HAL tick had gone backwards (`now < r[3]`, r[3] = the last pass's tick), or the buffer word was 0. A warm reset
keeps internal SRAM, so the magic stays. If the run before the reset died at or before the tick at which this boot makes
its first UI-loop pass, the tick has not gone backwards and **the init is skipped**: the black box keeps the last run's
buffer pointer, length, capture block and generation. This boot's allocator has not given it that memory; stock's next
allocations get it, and the black box appends its log text into them, while the M7 writes pad pressure captures into the
old capture block. Boots are deterministic (the first pass lands at the same tick), so the next reset repeats it: a loop
that ends only when RAM is lost (a power cut) or a boot happens to make its first pass a little earlier.

`test_clean_start.ResetLoop.test_a_run_that_died_at_its_first_pass` reproduces it on the v4.46 image: a run that dies
right after its first pass (1000 ms), then three resets each with the first pass at 1000 ms. On v4.46 the stock objects
allocated after the black box's pass (0xC6000000, the allocator's first block) carry our log text on the first warm boot
already (M). On 4.47 they are intact on all three (M).

A second hole, same cause: stock's app init runs before the UI loop's first pass (S: defaultTask 0x08114E34 calls the
app object's vtable +4, then enters the loop whose `bl` is m4_tick; M: the whole-core boot emulator reaches our M4 code
in an_value_text before m4_tick). Until the first pass, every hook saw the last run's CFG generation, so state stamped
with it passed for this boot's. `test_hooks_before_the_first_pass_see_no_last_run`: after a DUNGEONS session and a reset,
stock allocates its boot objects over the last run's curve pool, then a project load reads DUNGEONS' sidecar before the
first pass: v4.46 decodes its 37 curves (3380 B) into stock's objects (M); 4.47 does not touch them (M).

Why this fits the device report (I): the loop needs a first reset close to a boot's first pass (a power-button press or
a USB power-path glitch during boot, the "off on USB" path, a crash right after boot). It does not need the SD card or
USB. It ends with a power cut (RAM lost, magic gone). A stock session in between helps only if the next BE boot lands its
first pass earlier, or if the stock session ended with a power loss; both are possible, neither is proven. The emulation
does not show a fault inside our code with these inputs (it has no stock objects at those addresses to break), so the
exact crash that followed the corruption on the device is not known.

Not reproduced: a fault from RTC or backup register values (none of our code used them for anything but printing and the
one comparison); a fault from decayed SDRAM in the DSP (fuzzed, section 7).

## 4. Tests (test_clean_start.py, new; the suites in section 8)

The board is test_diag's emulator with the built image (both cores' code) and its FatFS stub. A reset puts stock's state
back at its start (the UI allocator, the M7 pool counters, the HAL ticks, DMA2 / SAI registers), keeps the backup domain
and SDRAM (or decays it), leaves our internal RAM as the last run left it (or pre-fills it), then runs the image's M7
reset path from CTRL_STARTUP_SITE to the next instruction. A boot is the M4's first passes, the M7's first trace record
and audio blocks (the reservation), a pad press (the capture), stock allocating 512 KB of objects, and a save. The clean
boot state (our internal RAM, the Region, the synth area, the log text, the capture block, the allocator) must equal a
cold boot's (everything of ours zero before the reset), and nothing may fault or hang (every call must return).

| Test | Inputs | v4.46 | 4.47 |
|---|---|---|---|
| StartupStub | our RAM 0xA5, 256 B of stock RAM either side | fails (only the hold line is touched) | our RAM zero, the hold line inited, nothing else written |
| CleanBoot (a) | a full session: DUNGEONS' curves.bnt loaded (37 curves), presses, a save, 10 min; valid magic everywhere | fails | equal to cold |
| CleanBoot (b) | the same, SDRAM 0.1 %, 2 %, 30 % bit-flipped | fails | equal to cold |
| CleanBoot (c, d, e) | all-0, all-1, random, and random with every magic word at its place; random SDRAM | fails (the synth area) | equal to cold |
| ResetLoop | the run dies after its first pass; three resets | fails: stock's objects get our log text | intact |
| hooks before the first pass | DUNGEONS load before the first pass, over the last run's pool | fails: sidecar decoded into stock's objects | intact |
| RtcUntrusted (f) | RTC / BKP all-0, all-1, random, garbage BCD, time going backwards; a new crash count mid-session | fails (alarm) | equal to cold with the same registers, log lengths aside |
| LogsStartFresh | no log files; both deleted mid-session; a card that refuses every open | fails (a recreated SAVES starts with a bare summary) | both created with header / boot line, recreated with header / `fresh` line; refusing card: no write, no fault, black box up |

The DUNGEONS sidecar (Jeff's project, AT curves) is `mods/m1/logs/be446_DUNGEONS_curves.bnt`.

## 5. R3: why no log files appeared after a BE4.46 boot

By design since v3.21 / v3.68 the black box touches the card only right after stock's own project write, from the same
task (stock's FatFS and SD driver take no locks; earlier writes from elsewhere broke the card). **A boot alone writes
nothing**: the files appear at the first project save of a boot (S). That save creates missing files: our appends use
FA_OPEN_APPEND and stock's f_open creates the file on FR_NO_FILE (S: 0x08120776 `tst.w r4, #0x1c; bne 0x08120538`,
0x08120538 `cmp r3, #4` -> dir_register 0x0811FCD4). The likeliest reading of the report is a boot without a save (I).
Two v4.46 cases where even a save could miss them: a skipped init (section 3: `hdr_gen` already set, so no boot line and
no header for that boot), and a recreated file starting with a bare summary. 4.47 fixes both: every boot's first save
writes the boot line and header, and a later save that finds a file gone starts it with them again. Our files are in the
card's root (no folder to create); the sidecar goes next to the project.xml stock has just written. A card that refuses
every open is tested: no write, no fault.

**Not done (a decision for Jeff):** writing the files at boot. It would make them appear without a save, but it is a card
access outside stock's own write path, exactly what v3.19..v3.21 found unsafe. Kept as v4.46.

## 6. What was dropped

All of it was "the next boot prints what the last run left in RAM":
- the boot header's "previous boot: last UI-loop pass at ms .., rtc .." line;
- "as found at boot" (D0 load, audio, memory and link figures of the last run, RAMCHK as found);
- the audio DMA stall snapshot printed at the next boot (the live alarm stays, in RAM);
- the M7 stall record and the M7 stall profile printed at the next boot (the stall group is off by default; the M7 still
  captures, boot-local);
- the M4 main-task profile printed at the next boot (irq30, off by default; the worst gaps still reach every save);
- "previous boot (from RAM)": the last run's counters and its last 17 trace records;
- the D2/slice canary and RAMCHK as a cross-reset detector (armed every boot now: they show in-session changes only);
- the boot generation as a power-cycle counter (it is 1 on every boot);
- Watch 1, the mid-session "NEW CRASH RECORD" alarm and its `live` CRASHES line (R2);
- the M7 synth area's "M7-only reset" path (generation moved, the M4's table kept): never needed (system resets only).

Kept: stock's own crash record (RTC backup registers) printed in every boot's header and in every save summary, as
untrusted hex. It is stock's, written by stock's fault handlers, and the only post-mortem left (it names the fault that
caused a reset).

## 7. Residual and open items

- **Stock's oscillator kernel area (R1, not changed).** Analog's per-oscillator state lives in stock's Wavetable
  oscillator object (+0x10C..+0x1CC, stock SDRAM). Stock's constructor leaves that area as the memory held it (M:
  WT_OSC_CTOR on 0xA5-filled memory leaves all 192 bytes 0xA5), so after a warm reset an oscillator stock places where an
  Analog one was can read the last run's AN_MAGIC and Type 1 until the project's push sends its Type (which resets or
  clears the area, S: an_prop). Fuzzed: 60 runs of a voice whose kernel areas were bit-flipped or randomised with the
  magic and Type kept, renders and note starts: no fault, no hang (M). Fixing it means zeroing the area at construction:
  a trampoline on stock's constructor call, which changes a stock path for every Wavetable oscillator. Jeff's call.
- **The M7 side starts at its first trace record.** h_consume (and with it the Region and synth area take) waits until
  the M7 header has its magic, which the first traced event sets (S: m7_diag.c). That is how every power-on of v4.46
  already behaved (cold RAM); after the clean start every boot behaves so. Unchanged here.
- The whole-board loop is shown with our code on an emulated board; the device's own sequence (which reset started it,
  which stock object broke first) is not measured.
- 1.6.1 is not covered (section 1).

## 8. Device check (BE4.47, device_tested false until done)

1. Flash, boot, wait 25 s, save any project: BENTO_SAVES.log starts with "BENTO black box, firmware BE4.47, boot gen 1"
   and "clean start (BE4.47)"; BENTO_CRASHES.log gets a `boot fw=BE4.47 ... gen=1` line.
2. Delete both files on the card, boot, save: both come back with their header / line.
3. Soft resets (power button off/on with USB connected; the "off on USB" path) x10, then a boot with DUNGEONS: boots
   every time, gen=1 in every header, DUNGEONS' AT curves play.
4. The v4.46 checklist's Analog, FM and drum-free items: unchanged (spot check).
