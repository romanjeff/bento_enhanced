# Plan: Playground tooling, synth engines and Track FX

Drafted 2026-10-31 against v3.63. Source snapshot: bento-playground 9fc7787 (internal faa3bc6).
Pin to that snapshot. We port into our own layout and don't track upstream.
Background and address tables: [`playground-review.md`](playground-review.md).

## Scope (Jeff, 2026-10-31)

| Must | Next batch | Understand first |
|---|---|---|
| Drum synth engines (Kick, Snare, HiHat, Clap, Tom) | Offline patcher | Track FX (per-track EQ / comp / clipper) |
| Analog (VA) oscillators on Wavetable tracks | Display emulator | |
| All four learning points (flash ceiling, RAM, emulator, A–D lanes) | | |

## Audit 2026-11-03: corrections (these override the sections below)

Static analysis, emulation and compiles only; nothing has run on hardware in our tree. Each area had
an investigator and an adversarial verifier. Overall: feasible, no structural blocker, not ready to
build. CPU is the open question.

| Topic | Plan said | Audit found |
|---|---|---|
| Next gate | P0: flash Alpha 1.0 | Read-only diagnostic build on our firmware first (below). Alpha 1.0 stays useful as a listening reference |
| Image size | Raise `PROVEN_MAX_SIZE` | Not needed. M4 needs about 19–34 KB of the 48,692 B margin (estimate; no linked image yet) |
| M7 code | ~29 KB, should fit | ~29.3 KB in the 39,780 B block below our code; ~10.5 KB left (sizes compiled, stubs estimated) |
| RAM | Heap cut or DTCM | ~20.7 KB fits off-heap: AXI gap 0x2404CDC0–0x24050000 + D2 blocks, or trimmed D2-only (~461 B spare). None device-confirmed free. DTCM is an active stock arena, about full after emulated boot; only a fallback |
| SPACED heap theory | 2.4 KB cut caused the loop | Emulated boot leaves ~37.8 KB heap free, so doubtful. Our own FatFS calls are the likelier cause (inferred). Not tested on SPACED |
| Registry | Check claim order | **7 of our 11 claims collide with keys these features hardcode, and the failure is silent.** `claim()` must reserve all 31 first |
| Shared hooks | Many shared sites | 48 flash sites in the union; only 0x0813F98C and 0x080404B6 touch our writes. 0x08131244 needs no merge (Playground only calls it). Add 0x08093CDA and 0x08094ADE to the census |
| Drums | One render hook, parity mostly inherent | 22 sites. Voice logic must be rewritten: each note-on frees the pad's slots and older live voices restart with it (inferred). Velocity is applied twice on pads with a velocity route. ~8 busy pads can exhaust the 16 slots |
| Carriers | Loaded from SD | Their UI calls FatFS, which breaks our no-FatFS rule. Ship WAVs in the kit and identify them from the native model |
| Analog | No fixed RAM; depends on the drums' knob table | ~0.5 KB fixed RAM. Compiled and linked with no drum code, so it can ship first. M7 2,396 B, M4 3,278 B |
| Track FX | Page opened by double-tap | No dependency on `pg_mixer_*`. Double-tap chains in our wrapper. Fix the cache invalidate overrun into Meta before porting. Saved FX needs a flush after load |
| CPU (model, ~2x spread) | Unmeasured | Per block: synth voice 1–8 %, Analog voice 1.3–2.9 %, full strip 5.4–10.4 %. Everything on: 82–186 %, and the baseline is a censored pad-press sample, so 186 % is no ceiling. Overload should glitch rather than reset, and may slip the sequencer (both untested) |
| Effort | Not sized | At least 14–18 sessions and 9–13 flashes, before the known rewrites |

**Diagnostic build (D0, built as v3.64, kit in `flash/bento_enhanced-v3.64`):** no feature code.
Writes only its own log lines and a one-time pattern over the three candidate RAM blocks. Printed at
each save and, for the previous run, at boot:
- `LOAD`: render time as a share of the fixed 1,280,000-cycle block, since the last save. Stock's
  meter can't show this: its ratio stays under 1.0 and falls when renders run late. The scope
  includes message draining, so size DSP from `avg` and the histogram, not `peak`.
- `AUDIO`: stock's counters. Real overruns show in `wrong_half`/`head_min`; `events - blocks` = renders
  dropped; `waited`/`timeouts` near 0 when healthy. Gap figures wrap at 0x800, so they only support these.
- `M7MEM`: heap free, low point (sampled, from the first ring post after M4 init), largest chunk, pool use.
- `RAMCHK`: changed words per block, with lowest and highest address. A fill survives resets, so use the
  as-found line from a boot after a warm reset. Any change at the top of d2b is a real user: stock
  sets SP there at reset but never writes it.

**Decisions (Jeff, 2026-11-03):** vendored C++ with separate hard-float DSP ELFs: yes. `bento_*`
attributes in project.xml: yes (ends "stock-clean" files). FX page: 3-row tf2 first (2.9 KB, no
documented device use, so high on the device test list). D0 is next.

**Traps:** the shipped UI is `eu4_work/integration/kick_ui.c`, not `eu4_work/drums/kick_ui.c`.
A free registry slot isn't an unused key: 0x1D0 and 0x1D1 are native command keys, and v3.63's AT
Rec/AT Out sit on them. Check that our private events can't reach the native model.

**Not traced:** track index vs mixer strip index; M7 root+0xC vs M4 handle; patch/preset load; undo
and duplicate with handle-keyed tables; stale tables across project change; SD mode with FX active.

## 1. Image size in our context

**Short answer: the flash ceiling carries over unchanged. RAM and the M7 code window are what
differ in our context.**

- **Flash: transfers.** The limit is a property of the flash map, not of what's in the image.
  0x081E0000+ is the 128 KB persistent-data sector (M7 MPU region 12, read-only, XN), and the
  bootloader refuses to program into it. Our v1 rejection (0x1A0800 B, end 0x081E0800) crossed
  it, and Playground's 0x19FE98 B (end 0x081DFE98) stays under it. Both data points fit one rule:
  **end < 0x081E0000**. M4 code may execute anywhere in 0x08100000–0x081FFFFF (M4 MPU region 1).
  Our image is 0x181500 today. Moving `PROVEN_MAX_SIZE` to 0x19FE98 gives about 123 KB more for
  M4 (UI) code.
  - Remaining unknown: bootloader version differences between units. The maintainer's confirmation
    covers their unit. Your own Alpha 1.0 flash (P0.1) closes this.
- **M7 (audio) code: does NOT grow with the image.** All M7 code from both projects lives in the
  one zero gap 0x080CF49C–0x080E0000 (~67 KB). We use 0x080D9000–0x080DF000 today. Free:
  ~39 KB below us + ~4 KB above. Estimated drum + Analog DSP ≈ 21 KB (their slot 0x080D5800–
  0x080DAC00) and Track FX ≈ 8 KB. It should fit, but must be measured by compiling.
  - Possible fallback, untested: the M7 runs privileged with PRIVDEFENA, so 0x08100000–0x081DFFFF
    falls to the default map and should be executable by M7 too. This needs one emulated test plus
    one device test before we rely on it.
- **RAM: the real conflict.** Playground's engine and FX state is hardcoded at 0x24040000–
  0x24048000, inside a 36 KB cut from the M7 heap. We left the heap stock in v3.16 because a
  2.4 KB cut broke SPACED. See P0.2 and R1.

## 2. How their engines work

### Drum synths (`eu4_work/drums/kick.cpp` + vendored DaisySP, MIT)

- **Hook:** one site, the native per-voice one-shot render entry **0x08091820** (b.w to a
  wrapper). We don't touch this site today.
- **Selection via carrier WAVs.** A drum slot plays a near-silent WAV whose first 32 samples are
  a ±1 signature (0xB1E70A5C). Its frame count is 144019 + engine − 1 (Kick 1, Snare 3, HiHat 5,
  Clap 7, Tom 9, plus a hidden "Subtractive" 11). Files are loaded from an SD folder
  `Drum Synths/` (Kick.wav …). On stock firmware the slot just plays near-silence.
- **DSP:** DaisySP SyntheticBassDrum (Kick, Tom), 909-style Snare, HiHat, Clap (project code).
  Kick adds Punch (a 6 ms pitch dive) and a warm saturator. 16 slots, per-voice pools of
  288 B cells.
- **Parameters:** 7 knobs + Click per pad, UI-owned table (`KickKnobs`, 128 entries keyed by
  native handle, sequence-numbered entries so readers reject torn writes). They are registry
  keys **0x1A0–0x1A9, 0x1B0** on the one-shot INST page and are saved as XML attributes.
- **Extras we defer:** Mod Matrix destinations (they reuse the native route list at root+0x590;
  the shared hook 0x08131244 is our External-rows hook) and per-step synth locks (lock kinds
  12–29 in the note's +20 word, which conflicts with `be_step_v1`).
- **RAM:** about 13 KB (engine state ≤0x1500, knob/assignment tables ~2.5 KB, voice pools ~4.6 KB,
  model IDs + voice locks), at fixed 0x24042000–0x24048000.

### Analog (`analog.h`, GPL)

- An "Analog" entry in the instrument menu creates a native **Wavetable track (type 18)** with a
  flag. Two M7 vtable slots of the wavetable voice are redirected: **0x080AC7E0** (voice enter,
  note/glide) and **0x080AC3E4** (oscillator render). WT1/WT2 then render BLEP saw→square→8 %
  pulse with PWM and fractional hard sync. Native filters, envelopes, LFOs and Mod Matrix are
  untouched downstream.
- **No new fixed RAM.** Oscillator state sits in the WT kernel area (WT object +0x10C), which
  analog tracks don't use.
- **UI:** menu entry, page keys and waveform pane text/paint (M4 vtable slots 0x081B3A18/1C).
  Saved as `bento_analog*` attributes. It shares the knob table with the drums.
- This is the cheaper and lower-risk of the two engine ports.

## 3. Track FX: implementation and possibilities

**Implementation**

- **DSP** (`tf2_work/track_fx/track_fx.cpp`, ~180 lines): portable C++, with no firmware
  addresses and caller-owned memory. Per track: a 3-band EQ (low shelf, peaking mid with Q, high
  shelf; RBJ cookbook into DaisySP Biquads), then a compressor (DaisySP-LGPL; threshold, ratio,
  attack, release, makeup or auto-makeup, wet/dry mix), then a soft clipper (ceiling −12..0 dB).
  - Coefficients and gains are smoothed. Disabled sections cross-fade out and then sleep (zero
    CPU).
  - Settings are range-checked and applied atomically, and save as a CRC32-checked record.
  - It uses about 5.2 KB of state for 8 tracks.
- **Insert point (M7):** **0x080A0510**, inside the native per-track mixer function 0x080A04C0.
  It runs after the track Level is applied into the track's scratch buffer, and before pan,
  sidechain ducking, sends and the dry-bus sum. So the FX are **pre-pan and pre-send**: reverb
  and delay sends hear the processed signal. Planar float, at most 1024 frames per call.
- **UI to audio:** the M4 writes a seqlock mailbox (odd serial = writing) of 8 × 18 values in
  shared AXI RAM. The M7 polls it on each call and invalidates its D-cache lines before reading.
  The M7 never trusts a torn or out-of-range mailbox.
- **UI:** 19 new registry keys **0x1B1–0x1D0**, so native encoder bars render them. A dedicated
  page with EQ / Compressor / Clipper footer tabs is reached by double-tapping TRACKS. Settings
  persist through their XML save/load hooks 0x0812F5EA / 0x0812FAAC. The gain-reduction meter is
  unfinished.

**Possibilities**

1. **Any per-track insert at the same point**, using the same mailbox, registry-key and page
   pattern: drive or saturation, bit-crush or decimate, a DJ-style LP/HP filter, a transient
   shaper, a gate, stereo width.
   - **D (saturation) could be per-track instead of master-only.** That's cheaper to build
     because the hook already exists.
2. **Master-bus processing** (our original D: a saturator next to the master compressor). This
   needs a different hook in the master path (see `compressor.md`). The DSP pattern transfers;
   the insert point doesn't.
3. **Modulatable FX parameters.** Because the FX parameters are registry keys, they could become
   Mod Matrix or automation-lane destinations later. That ties into learning point 4 and F.
4. **Ordering choice.** Pre-send is fixed by this hook. A post-pan or post-duck insert would need
   another site in 0x080A04C0.
- **Caveats:** CPU is unmeasured (six biquads + compressor per active track; sleeps when off),
  and RAM placement has the same issue as the drums. Double-tap TRACKS has to coexist with our
  TRACKS hold shortcut.

## 4. Sprints

Each port starts with a **hook census**: every site the feature touches, classified as unique or
shared with ours. A shared site means a chained handler. Port code is rewritten readably and
credited (GPL / MIT / LGPL notices kept).

### P0: Device evidence (Jeff; no code)
- [ ] P0.1 Flash Alpha 1.0 from your stock pair via their patcher. Confirm it boots and shows Alpha 1.0.
- [ ] P0.2 Load HUB, then **SPACED manually** (not as the autoload project). If SPACED loads, our
      heap theory was wrong or incomplete and the heap is usable. If it loops, the theory is
      confirmed and the engine state has to move elsewhere. Then restore.
- [ ] P0.3 Spot-check the drum synths and Analog on a busy project: sound, CPU artefacts.

### T1: Display emulator (learning point 3)
- [ ] Port `research/display_emulator` into `bento_enhanced/tools/display_emulator/` (credit, GPL).
- [ ] Render our v3.63 pages. First target: reproduce the missing Note Mod header button offline.
- [ ] Add render checks to the m1 test suite (golden PNG or pixel-region assertions).

### T2: Offline patcher + flash ceiling (learning point 1)
- [ ] `build.py` emits a write manifest (address / original / replacement; it already has expect bytes).
- [ ] Adapt their HTML/JS + Python patcher: stock-hash check, byte-exact restore test, restore folder.
- [ ] After P0.1: `PROVEN_MAX_SIZE` → 0x19FE98 (keep `FORBIDDEN_FROM` 0x081E0000).

### R: Memory research (gates the engines)
- [ ] R1 State placement, decided with P0.2's result. Candidates:
      (a) heap cut (only if SPACED passes);
      (b) free D2: 0x300390F0–0x3003C000 (~12 KB) and 0x3003E000–0x30040000 (~8 KB; reset temp
      stack grows down from 0x30040000, so init after boot);
      (c) DTCM 0x20000000 (128 KB, M7-only; usage unsurveyed) for M7-only DSP state, with only the
      UI-shared tables (knobs, mailbox: ~3.5 KB) in D2.
- [ ] R2 M7 cave budget: compile drums, Analog and FX at our addresses and measure. Test the
      bank-2 M7 execution fallback only if needed.
- [ ] R3 A–D lane rules from their motion code → `docs/` (learning point 4; input to F).
- [ ] R4 Registry keys: their fixed 0x1A0–0x1D0 vs our 11 dynamic claims (scan from 0x1F2 down,
      skipping occupied keys). Make sure our claim never runs before their keys are registered.

### E1: Analog oscillators (smaller of the two engine ports; no fixed RAM)

### E2: Drum synths with one-shot parity (Jeff 2026-10-31: "behave just like the one-shot tracks")
Parity means parity with one-shot tracks **in our firmware**.
The synth writes into the voice's native scratch, then calls the native post-render chain
0x08092D00. Pitch ratio, velocity (voice+0xB8), LFO1 and native routes are honoured, so
most parity is inherent. Checklist:
- [ ] Inherent, verify only: our conditions, retriggers (including MIDI-off variants), strike
      velocity, hold-step, pad defaults; per-pad level/pan/sends; native filter/envelope after
      render; choke/mute; recorded AT through voice pressure.
- [ ] Mod Matrix: synth knobs as destinations (formerly E3, now required). Merge with our
      0x08131244 External-rows hook.
- [ ] Sample-only parameters acting on a carrier: start/end, reverse, loop, slice. Decide per
      parameter (ignore, map, or hide) and test that none of them breaks the synth.
- [ ] Not in scope: synth-parameter step locks. Our one-shot tracks have no parameter locks
      since we dropped Lab's plock. Revisit only if they get them.

### FX1: Track FX: their full per-track strip (Jeff 2026-10-31: "we can try that")
Includes the double-tap TRACKS entry, which has to coexist with our TRACKS hold shortcut.

### M1: PLAY + pad slot mute (Jeff 2026-11-03)
Hold PLAY while the transport is running, tap a pad: toggle that pad's mute on a one-shot track,
without triggering its note. Pads show which slots are live while PLAY is held. Mutes stay latched.
- Native mechanism, no new DSP: the same child property **0x1E0** and model event **0xCF** that the
  touchscreen slot mute uses (Playground `eu4_work/integration/play_mute_ui.c`; re-verify in Ghidra).
- Reuses our hold-shortcut path: M7 pad hook 0x0808D1FE → UI event 0xFE → consumer 0x0813F98C.
- Their version only addresses track 1. Ours should address the **selected one-shot track**
  (design choice, to confirm with Jeff; decide what happens when a melodic track is selected).
- Pad feedback needs their LED flush hook 0x081257F0 (`play_mute_flush`).
- Doesn't depend on R, E1, E2 or FX1, but shares the 0x0813F98C wrapper with drums and FX.

Order (revised 2026-11-03): D0 + v3.63 HDR → `claim()` key reservation → E1 → CPU re-check → E2 →
FX1. M1 can go any time. T1 and T2 continue alongside.

## 5. Risks

| Risk | Why it bites | Mitigation |
|---|---|---|
| M7 heap / RAM | 2.4 KB once broke SPACED; their design needs about 20 KB | P0.2 test; R1 placement off-heap |
| M7 CPU | Playground already admits artefacts; our mod engine and AT streams add load | DWT cycle counters per engine in the black box; measure before adding FX |
| Shared hooks | consumer, header generator, XML save/load, Mod Matrix route | Hook census per sprint; chained handlers; emulated regression suite |
| Carrier WAV UX | Drum sound depends on SD files travelling with the project | Ship carriers with the patcher; document it |
| +20 word | Synth step locks need it, but `be_step_v1` owns it | Defer locks; later a sidecar like curves.bnt |
| Code quality upstream | Dense, single-tester code | Rewrite while porting; our own tests |
| Licensing | DaisySP MIT, DaisySP-LGPL compressor | Both GPL-3 compatible; keep notices |

## Burst cost on SPACED (2026-11-03)

Same project, 7 tracks. Two separate runs, so the session mix differs and the features-on run also played 12 curves.

| | v3.67 features on | v3.68-off twin |
|---|---|---|
| LOAD average | 65.0% | 59.0% |
| 80-100% blocks | 15.2% | 6.4% |
| over 100% | 0.27% | 0.033% |
| peak | 157% | 181% |

The 6-point average gap is not evidence about our hooks: blocks under 50% were 11.5% (v3.67) and 30.0% (twin). Counting only blocks at 50% or above and assuming the quiet ones averaged 22-45%, the gap is -4.2 to +2.6 points. Over-budget blocks among playing blocks are still 0.30% vs 0.047%, but that is session-dependent too.

Implication for the ports: SPACED leaves little room, so strips and synth voices need a visible CPU limit. Strip and voice costs are still unmeasured estimates.

Staged plan (design workflow cut-remaining-m7-cost):
- **Stage 0, v3.69:** diagnostics only. Sample stock's timer at 0x2401D678+4 once a block, and price 8 Device reads against 8 cached fills about once a second. Printed as `seq` on the AUDIO line: seq_sum (cycles>>8), seq_n, seq_peak, dev_sum, fill_sum, mem_n, dev_max, fill_max. The timer covers the whole audio graph pass, not just the sequencer, so a difference between builds includes DSP, voices, cache, ISR time and session mix. Stopped blocks must be corrected for: H_play = (seq_avg - f*S)/(1-f), with f the under-50 bin share and S a stopped run on the same image. Decide the table move from L = dev_sum/(8*mem_n) and F = fill_sum/(8*mem_n): the saving is about 521*L - 32*F cycles per SPACED block (fitted from model output), so go at L of about 35 or more; below about 15 only the masked-window argument remains.
- **Stage 1, v3.70 (built, not yet run on the device):** move the Region to 0xC5FFC000, the top of the M7's cached ext pool (MPU R8 write-back, about 3.4 MB unused). No MPU write and no port RAM. The reservation check runs before region()'s fast path. Takes the last 16 KB of that pool while our firmware runs; stock gets it back on stock firmware. The M4 stats guard accepts only the exact address and the M7 cleans the 3 stats lines at each publish.
  - **Go decision, v3.69 on SPACED with live launching:** L 66-69 and F 87-88 while playing, so the formula gives about 2.5 points against the go line of L 35. Curves run: 58.8% average, 14.9% of playing blocks at 80-100%, 0.256% over budget. No-curves run: 55.2%, 8.1%, 0.072%. Estimated playing load is the same in both (73.8%), so the difference is bursts, not sustained work.
  - **SDRAM over on-chip RAM (Jeff, 2026-11-03):** on-chip would add 0.15-0.2 points on average (1-1.3 at worst, for peak fetches) and take 9.2 KB of about 12.3 KB of port RAM. Neither place is safer against a future 1010music update. Switching later is cheap. Reconsider if the ports are dropped, or if v3.70's bursts stay above no-curves and fetches look like the remaining cost.
  - **Future firmware:** `check_region_assumptions` in build.py lists what the Region's place depends on: the pool's starting values, the allocator, the counters' users, the MPU setup, and no references into the reserved range. STOCK_SHA already refuses other images, so it only fires once someone changes that hash. At runtime an unknown pool shape leaves our state off and notes pass.
  - **Expected on the device:** `ext left=3389440` once a hook has run; 3405824 means the reservation was not taken. The model predicts about 2.5 points less load while playing and fewer blocks over budget. Compare playing load, not the plain average, because the share of quiet blocks changes between sessions.
  - **Not known:** actual saving, real eviction rates, the zero loop's masked time when the first caller is masked, and whether the M7 or the M4 runs first after a reset (the "previous boot" curve stats may read 0 as a result).
- **Stage 2:** only if over-budget blocks stay well above the twin: stats() out of hooks, region()/config() once a block, send passes after the owning track.
- **Stage 3, not recommended:** remove PRIMASK.

Rejected: a new MPU region (no extra saving), the Region in D2/AXI (9.2 KB of about 12.3 KB port margin for 0.15-0.2 points if lines stay warm), full early-outs first (317 lines in the note path).

The input freeze is unexplained and no stage addresses it. Not reproduced since v3.66; the LINK line is the discriminator if it returns.

Static results (2026-11-03, read and emulated, not run on the device):
- Pressure in SPACED: no pressure route exists and no extra voices or DSP were found; the messages cost stock at most about 0.015% of budget (inferred). Hooks are modelled at 2.4-3.3 points of load with the Region as Device, and about 0.7 point cannot be recovered by moving memory.
- Corrupted frames: nothing in the readable code lets a frame latch persistent state on the Controller or the M7 input path. The Controller bootloader and SPI silicon behaviour are outside that result. Candidates, all unconfirmed: sustained overrun mis-framing, M7 SPI or interrupt loss, Controller SPI3 stuck, Controller in bootloader. Only a LINK line saved by touch during a freeze separates them.
- Masked windows: 54.8 us at 6 players with the Region as Device, against a limit of about 170 us from a from-memory FIFO figure; about 10 us more per player (inferred).
- A twin save strips curve words, and a features-image save of that file then rewrites curves.bnt with count=0. Run each test arm on its own project copy.

Card writes (2026-11-05): BENTO_SAVES.log had duplicated and lost bytes at 512-byte offsets since BE3.67: 32 of 98 boundaries in the v3.71 log. Stock's f_write hands the caller's pointer straight to the SD DMA for whole sectors, and the DMA rounds it down to 4 bytes, so our unaligned log source shifted each run. Treat numbers at those boundaries in old logs as unreliable (for example `355|36` was 3551936). Fixed in v3.72 by writing the log in pieces under one sector, so it always goes through FatFS's window. Not this fault in the checks so far: stock's project writes use one aligned buffer, curves.bnt is written from a 32-aligned buffer in whole chunks, and 16 project.xml and 10 curves.bnt versions in the backups showed no slips (about 95% sure of digit-level slips). Not cleared: stock's other writers (0x8103DF2; the likely peak-file writer at 0x8110F50/0x8110FB0) and most f_read sites. Still unexplained: the v3.71 gen 4 save wrote its header but no summary, and a stray `lencount="4473917"` in the Oct-25 SPACED backup (mid-sector, so not this fault).
