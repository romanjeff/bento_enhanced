# Plan S6: Analog oscillators and drum synths on stock 1.6.8

Drafted 2026-11-08 against BE4.10 (9a62ed2, S4+S5). Source: bento-playground 9fc7787, read only.
This plan supersedes the 1.6.1-era parts of [`plan-playground-integration.md`](plan-playground-integration.md):
its section 1 (image size, M7 window, RAM), every address in section 2, R1/R2/R4, the E1/E2 checklists
(Mod Matrix destinations are now deferred) and the sprint order. Its audit table (2026-11-03) still holds
where this plan doesn't restate it. Background: [`playground-review.md`](playground-review.md),
[`plan-168-port.md`](plan-168-port.md).

Evidence: three offline investigations (static analysis, emulation, compiles with our toolchain; nothing
has run on the device), scratch `rc20/analog/`, `rc20/drums/`, `rc20/infra/`. Values marked (M) are
measured, (I) inferred.

## 1. Requirements

**Must**
1. **Analog (VA) oscillators on Wavetable tracks:** an "Analog" instrument menu entry. WT1/WT2 play a
   BLEP saw -> square -> 8 % pulse shape (on the Position knob), with Sync and Glide. The native sub
   oscillator, filters, envelopes, LFOs and mixing run downstream unchanged. The flag, Sync and Glide are
   saved in project.xml and presets (`bento_analog*` attributes).
2. **Drum synths:** Kick, Snare, HiHat, Clap and Tom on one-shot pads, behaving like one-shot pads in our
   firmware (checklist in section 9).
3. **No regressions.** 1.6.1 stays frozen and byte-identical to v3.72 (25877c59...). On 1.6.8 a build
   with the synth groups disabled differs from BE4.10 only in the listed reservation constants. Plain
   Wavetable tracks and sample pads behave as on stock whenever no synth is involved.
4. **Safety rules:** image at most 0x18D334, nothing at or above 0x081E0000, bootloader untouched, no
   FatFS calls from our code (except right after stock's project write), no firmware bytes in the repo,
   and build refusals for every assumption below.
5. **Measurement instead of limits (Jeff, 2026-11-08):** no voice cap on Analog tracks and no bypass that
   drops Analog voices. What we need instead: a per-engine CPU line in the black box, instruction counts
   in the emulator, and a visible on-screen load indicator.
6. **Memory:** synth state goes in SDRAM, through **one** combined ext-pool reservation with the M1 Region.
   The Region keeps its address.
7. **Registry:** synth keys come from a reviewed allow-list. They are reserved all-or-nothing, never
   Playground's numbers, and covered by test_keys.
8. **Licences:** upstream is rewritten readably and credited. Keep the GPL notice (analog.h), the MIT
   notice (DaisySP) and the LGPL notice (the DaisySP-LGPL parts, Track FX, later).
9. **Carriers:** our own generated WAVs, shipped in the kit for Jeff to copy to the card.
10. **Rollback:** the v4.10 kit and stock restore stay valid at every step. A project saved on BE4.20
    opens on v4.10 without a crash (an Analog track becomes a plain, empty Wavetable track).

**Nice**
- The efficiency items S6a doesn't adopt (section 6), and ITCM placement.
- The pane drawing the shape the oscillator really plays, with modulation (upstream P4).
- A drum menu entry (needs a FatFS decision, D6).
- Encoder x5 feel on the synth knobs.
- Knob values surviving pad copy / duplicate / undo.
- Carrier detection in the UI before a pad is first played.

**Out of scope or deferred:** synth knobs as Mod Matrix destinations (deferred with `mx`); synth parameter
step locks (+0x14 belongs to be_step_v1); upstream's hidden Subtractive engine and its diagnostics; the
EQ / comp / clip Track FX strip (after the synths; memory and keys are reserved for it below).

## 2. What the investigations established

- **M7 code is unchanged on 1.6.8 (M).** Every function Analog and the drums hook or call is the 1.6.1
  code, moved. Masked compares show 0 differences for:
  - Analog: oscillator render, note start, ctor, kernel reset / read, voice render, root ctor, voice post,
    and all helpers;
  - drums: the render entry (1789 instructions), post chain, both voice-start functions, and their helpers.
  A prototype on stock 1.6.8 oscillator code played note 60 at 261.4 Hz (261.6 expected).
- **The M4 UI side moved for real (M):**
  - the waveform pane object grew by 0x168 (label +0x26C, colours +0x2DC / +0x2DD);
  - the synth view grew by 0x1B50 (key rows at view+0x11CAC);
  - menu action ids moved by +2 (Wavetable is now 0x102C);
  - the instrument-page event codes moved by +2 / +3.
- **None of the new sites overlaps a BE4.10 site (M).** Checked against every code site in
  `stock_168.py`.
- **Playground's keys can't be used (M).** 6 of its 10 drum keys and its Sync key land on native or
  claimed keys on 1.6.8, and its "owned" check then turns the page off without saying so.
- **Budgets fit without raising anything (M):**
  - M7: 36 KB block free below our code;
  - M4: 32,308 B left;
  - SDRAM: about 3.4 MB of pool slack;
  - keys: 34 clean slots for 31 needed.
- **Toolchain trap (M):**
  - `-mfloat-abi=hard -mfpu=...` with plain `-mcpu=cortex_m7` silently builds soft-float. The CPU string
    must carry the FPU.
  - zig links compiler_rt anyway, so one stray double pulls in about 700 B of soft-double code.

## 3. Hook census

"Unique" means no BE4.10 hook touches the site. "Shared" means more than one synth engine uses it: one
dispatcher per site from the start, with Analog as its first client. Playground's 1.6.1 addresses are
leads only; each one was checked against our 1.6.1 image bytes and re-derived on 1.6.8.

### Analog (S6a)

| # | Site | Kind | 1.6.1 (Playground) | 1.6.8 | Stock bytes / word | Status | Chaining |
|---|---|---|---|---|---|---|---|
| A1 | Wavetable root vtable 0x080ACF68 slot 18 (voice render) | vtable word | 0x080AC7E0 | **0x080ACFB0** | 0x0809CA81 | unique | asm trampoline saves r0-r3, leaves the stack arguments alone, calls our enter, tails to stock 0x0809CA80 |
| A2 | Oscillator class slot 0 (render) | vtable word | 0x080AC3E4 | **0x080ACBB4** | 0x08081C41 | unique | ours returns "handled", or falls through to stock 0x08081C40 with the arguments intact |
| A3 | Instrument menu cell 8 | data | 0x08198298 (table 0x081981F8 + 160) | **0x0819ADD0** (table 0x0819AD30) | {0, 0x0818C5F0, 0, 0, 1} | unique | - |
| A4 | Menu cell count `movs r3,#8` | code | 0x08141370 | **0x08142EB4** | identical context | unique | - |
| A5 | Menu view handler slot | vtable word | 0x0819E52C (0x0814ECD5) | **0x081A13F4** (0x0815095D) | | unique (a drum menu entry, D6, would share it) | ours handles our action id, else stock |
| A6 | Wavetable page key copy, `bl memcpy` | code (bl) | 0x0815F68E | **0x08161446** | bl 0x08189466 | unique | memcpy, then set our keys in rows 0/1 slot 3 (free on 1.6.8) |
| A7 | Pane text, x6 `bl SLOT_TITLE` | code (bl) | 0x0815D968, DB22, DC0E, E1AE, E1B8, E1E2 | **0x0815F714, F8CE, F9BA, FF5E, FF68, FF92** | bl 0x081809DE | unique | our text on Analog tracks, else stock's call. FF5E has no literal load before it, so its string is inferred: T5 must show it |
| A8 | Wavetable pane tick / paint | vtable words | 0x081B3A18 / 0x081B3A1C | **0x081B6910** (0x081844E1) / **0x081B6914** (0x08184551) | | unique | stock first, then our overlay on Analog tracks |
| A9 | Synth view event slot (0xCF on our keys) | vtable word | (Playground: model event entry 0x081403EC) | **0x081A178C** (0x08160F01) | | unique | ours takes 0xCF for SYNTH_KEYS, everything else goes to stock. Fallback (D4): model event entry 0x08141EA4 (code patch, every page) |

Not ported:
- the global retitle of the stock Position descriptor (0xC9): we set the slot title after the page build
  (SLOT_TITLE pattern);
- the granular key copy 0x08161364;
- Playground's action id 0x1052. Ours is **0x10F4**, after ACT_NM 0x10F3; stock ends at 0x104A.

### Shared infrastructure (Analog first, drums in S6c, FX later)

| # | Site | Kind | 1.6.1 | 1.6.8 | Stock bytes | Notes |
|---|---|---|---|---|---|---|
| X1 | Instrument / pad-params XML save | code | 0x0812F5EA | **0x081304DE** | `80460024` (mov r8,r0; movs r4,#0) | r0 = new params element, r6 = object; dispatch on object type. Not in NOTE_SEQ_WRITER, where STEP_SAVE_SITE 0x081303E4 sits (instrument writer 0x08130484). T3 runs both hooks together |
| X2 | Instrument XML load entry | code | 0x0812FAAC | **0x081309A0** | `f0b585b0` | (node, object); callers: project load 0x081313EC and 0x0813163C (probably preset load). Separate from STEP_LOAD_SITE 0x08130AA8 and DOC_READ |
| X3 | CONTEXT_GET entry (value reads for private keys) | code | 0x08133614 | **0x08134A70** | push {r4-r7,lr}; sub sp,#0xc | 343 callers, including our m4_step.c (its 0x8D/0x8E reads). The first test is the key range; anything not in SYNTH_KEYS passes through untouched |
| X4 | synth_keys() | none | - | - | - | runs from the synth UI ELF's first use, once per boot generation (section 5) |
| X5 | Combined SDRAM reservation | none | - | - | - | inside region() (m7_polyat.c), under `#ifndef BE_OMIT_SYNTH` (section 4) |
| X6 | Black-box call-out | our code | - | - | - | about 20 B in m4_diag.c: a guarded call into the synth UI ELF at save time |

### Drums (S6c, S6d adds no site)

| # | Site | Kind | 1.6.1 (Playground) | 1.6.8 | Stock bytes | Status | Chaining |
|---|---|---|---|---|---|---|---|
| D1 | Per-voice one-shot render entry | code (entry) | 0x08091820 | **0x08092320** | 2de9f04f | unique | on a 0 return, replay `push.w {r4-r11,lr}` and continue at 0x08092324 |
| D2 | Voice-start A, in 0x080946A0 | code (mid) | 0x08093CDA | **0x080947DA** | 95f82536 `ldrb.w r3,[r5,#0x625]` | unique | replay the ldrb; r4 = voice, r5 = root |
| D3 | Voice-start B, in VOICE_START 0x08095260 | code (mid) | 0x08094ADE | **0x080955DE** | d5ed417a `vldr s15,[r5,#0x104]` | unique, but inside a function our S3 code relies on (no byte overlap) | VFP registers are live: save d0-d7 and APSR, replay the vldr |
| D4 | Native post chain | call only | 0x08092D00 | 0x08093800 | - | - | we call it with (root, voice, temp, out) |
| D5 | Instrument-page refresh | code (entry) | 0x08152144 | **0x08153DF4** | 2de9f04f | unique | before / after wrapper; the event codes inside moved by +2 / +3, reviewed before the wrapper is written |
| D6 | Instrument-page events | code (entry) | 0x08152C50 | **0x08154970** | 70b5f8b0 | unique | codes 0x102B -> 0x102D, 0x1045 / 46 -> 0x1048 / 49 |
| D7 / D8 | Pad-params XML save / load | = X1 / X2 | | | | shared | drum client of the dispatcher (`bento_drum_*`) |
| (D9) | Encoder x5 | code | 0x08171E44 | 0x08173FA4 | f0b587b0 | optional | not planned; descriptor ranges first |

Dropped or deferred from Playground's 22 drum sites:
- **Menu entry, 3 sites.** One of them is our CTRL consumer 0x08141408, and the entry needs FatFS or
  stock's file-onto-pad loader (D6).
- **Mod Matrix, 5 sites** (0x0808FE8E, ~0x0814EAEE, 0x08130596, 0x08130D46, 0x08142412): deferred with `mx`.
- **6 paint filters:** replaced by the widget hidden byte B(w,0x30), set from refresh-before.
- **3 diagnostic loader hooks:** dropped.

Whether the drums also need X3 for their knob values is decided in S6c; the X3 dispatcher is built ready
for it.

**Table entries.**
- Every Analog and drum site has a 1.6.1 counterpart. It goes into `stock_161.py` by hand with
  fingerprints, its 1.6.1 bytes checked against our image, and its 1.6.8 value is reviewed in `facts.py`
  (REVIEWED, plus `expected_values.py` for keys, offsets and events). The groups stay unported on 1.6.1,
  so they are omitted there. The infra dry run showed 1.6.1 stays 25877c59... with such an entry.
- Entries with no 1.6.1 counterpart are `ours(None)` on 1.6.1 and REVIEWED on 1.6.8: M7_DSP, M7_DSP_LIMIT,
  SYNTH_AREA, SYNTH_KEYS, SYNTH_UI, ACT_ANALOG. M7_IMAGE_END has one (1.6.1: 0x080CF49C).
- Struct offsets (pane +0x26C / +0x2DC, view +0x11CAC, root +0x0C, voice +0x7A4 / +0x994 / +0x4C, kernel
  +0x10C) become `offset` entries now, not bare constants. The S4+S5 review note about bare layout offsets
  should not grow.

## 4. Memory map

### M7 code (1.6.8)
Stock's zero gap is 0x080CFC5C..0x080E0000, 66,468 B. 0x080CFC5C is the M7 .data end literal at
0x08040568 (M).

| Range | Size | Use |
|---|---|---|
| 0x080CFC5C..0x080D0000 | 932 B | guard, stays zero |
| **0x080D0000..0x080D9000** | 36,864 B | **new: hard-float DSP ELF** (M7_DSP .. M7_DSP_LIMIT), entry table at the origin |
| 0x080D9000..0x080DA000 | 4 KB | CTRL_INPUT 296 B, CTRL_STARTUP 88 B (BE4.10, unchanged) |
| 0x080DA000..0x080DA400 | 1 KB | trampolines, 592 B used (unchanged; the new trampolines live in the DSP ELF) |
| 0x080DA400..0x080DF000 | 19 KB | soft-float m7.elf, 14,240 B used (unchanged) |
| 0x080DF000..0x080E0000 | 4 KB | free, untouched |

- **Expected use:** Analog about 2.3-2.7 KB plus glue and stats; the drums 7-9.5 KB; Track FX about
  7.7 KB later. Total about 20 KB of 36 KB.
- **Build check (`check_dsp_assumptions`, like `check_slice_assumptions`):**
  - the window is zero in the base;
  - M7_DSP is at or above M7_IMAGE_END;
  - no literal or `movt` points into the window;
  - the ELF fits.
- **One DSP ELF, not one per engine.** It is a close call: per-engine ELFs would keep fixed addresses in
  a bisect, but they would need fixed sub-windows and duplicate the math shims. With one ELF, each engine's
  sources are compiled in when its group is in the image, and the entry table has fixed slots (an absent
  engine's slot is a stub that returns 0).
- **Not used:** the SD-reader image's tail (0x080F89D0..0x08100000) and bank 2 through PRIVDEFENA. Both
  are untested and not needed.

### M4 code
- **New block SYNTH_UI** from BE4.10's IMAGE_END (0x081C5500), sized at link time, checked against
  0x081CD334. That leaves 32,308 B.
- **Estimates:**
  - Analog UI 3-4.5 KB;
  - drum UI 3-5 KB (the drums investigator's rewrite estimate) to 8-11 KB (infra's estimate from
    upstream's size);
  - CPU indicator and black-box print about 1 KB;
  - Track FX later 3-7 KB.
  Total 15-22 KB.
- Built `-mstrict-align` (BASE_SETTINGS m4_flags). Soft-float: the UI does integer work only.
- The black-box block is full (441 B left), so the synth's black-box print lives in SYNTH_UI, reached
  through X6.
- If drums plus FX come close to the limit, repacking COND_UI on 1.6.8 only (4 KB slack) is the reserve.

### SDRAM: one combined reservation
- **Why combined (M):** `region_reserve()` treats a pool end that is neither the pool's end nor the Region
  as "not ours" and leaves the Region off for the whole boot, without any visible failure. A second,
  separate reserver would silently switch off the poly cap, curves and Note Mod state.
- **The change (m7_polyat.c and diag_shared.h, all under `#ifndef BE_OMIT_SYNTH`):**
  - `TOTAL = M1_REGION_RESERVE (0x4000) + SYNTH_BYTES (0x8000) = 0xC000`;
  - the fast path compares `CUR + LEFT` with `EXT_POOL_END - TOTAL`;
  - the reservation is taken only if `left >= TOTAL + 1 MB` (running the pool out hangs the M7 at
    0x080A3B20, `b .`, and then the watchdog resets it);
  - the M1 Region stays at 0xC5FFC000 with its bytes unchanged;
  - `check_region_assumptions` scans [EXT_POOL_END - TOTAL, END) and its `shared_define` reads TOTAL.
- **1.6.1:** the synth groups are omitted, so `BE_OMIT_SYNTH` is passed and the code is identical. A
  1.6.8 bisect with `BE_DISABLE=analog,drums` keeps the larger reservation (it isolates code, not memory).
  Its manifest diff against BE4.10 must be only these constants and the label.
- **Dependency:** the reservation lives in poly_cap's region(), and the M4 publishes CFG from bbox. So
  `analog` and `drums` need `poly_cap` and `bbox`: disabling either one also disables the synths (like
  step -> mx).
- **Expected `ext left` on 1.6.8:** 3,356,904 (I), that is the predicted 3,389,672 with the Region minus
  32,768. The v4.10 log Jeff is producing gives the real base figure.

Synth area **0xC5FF4000..0xC5FFC000** (SYNTH_AREA, 32 KB). PROBE_SCRATCH (0xC5FF0000, 256 B) stays below,
with 16 KB of margin.

| Offset | Size | Writer | Contents |
|---|---|---|---|
| +0x0000 | 32 B | M7 | Header: magic, area generation (+1 on every take), layout version, TOTAL |
| +0x0020 | 32 B | M4 | M4 header: magic, the M7 generation it has seen, boot generation, table serials |
| +0x0040 | 448 B | M7 | Stats lines: Analog (2 lines), drums (2), spare. Cleaned after each write |
| +0x0200 | 512 B | M4 | Analog track table: 16 lines {seq_head, handle, flags (analog, sync), glide, ..., seq_tail} |
| +0x0400 | 512 B | M7 | Analog publish: shape per track (nice-to-have P4), the handle confirmation |
| +0x0600 | 512 B | M7 private | Analog per-root cache {root, serial, mode, glide, last note}, 16 entries |
| +0x0800 | 2 KB | M4 only | Synth UI state: pane redraw, CPU indicator history, drum page state |
| +0x1000 | 4 KB | M4 | Drum knob table: header + 127 lines {seq_head, handle, engine, flags, values[8], seq_tail}, open addressing by hash(handle) & 127 |
| +0x2000 | 1 KB | M7 | Drum publish: engine confirmations per handle |
| +0x2400 | 8 KB | M7 private | Drum cells: 32 x 256 B (largest engine state: HiHat 220 B) |
| +0x4400 | 512 B | M7 private | RNG state, block counter, allocation cursor |
| +0x4600 | 6.5 KB | - | spare |
| +0x6000 | 8 KB | - | reserved for Track FX (about 5.2 KB of state + mailbox) |

**Rules (the slice's rules):**
- One writer per 32-byte line.
- The M7 invalidates (DCIMVAC) M4 lines before it reads them, and cleans (DCCMVAC) the lines it writes
  for the M4.
- M4 entries use a seqlock: accepted only when head == tail and both are even, all from one line fill.
- The M4 has no data cache and sees SDRAM as Normal through its R3.

**Generation handshake (new, not in the investigations):**
- The M4 writes the area only after it has seen the M7 header's magic and generation.
- The M7 zeroes the whole area when it takes the reservation, including after an M7-only reset.
- So whenever the generation changes, the M4 re-publishes its tables from the model (the track and pad
  objects hold the truth). Otherwise an M7-only reset would silently turn every Analog track back into
  plain Wavetable until the project is reloaded.

**Oscillator state:**
- Analog's oscillator state is 152 B at oscillator +0x10C..+0x1A4, inside stock's Wavetable object. That
  object comes from the ext pool too (SDRAM, cached), so there is no per-voice RAM of ours.
- Stock's kernel reset writes only oscillator +0x1CC..+0x1E4 (M).

## 5. Registry keys

- **The problem:** an empty slot is not an unused key. 10 of claim()'s 11 keys on 1.6.8 are property keys
  stock still uses through set_property or M7 commands. That was inherited from v3.72 and has been fine on
  the device; claim() stays unchanged, which keeps 1.6.1 identical.
- **Plan:**
  - Add `synth_keys()` in the synth UI ELF, driven by a reviewed 1.6.8-only tuple `SYNTH_KEYS`
    (cross-checked in `expected_values.py`).
  - It runs once per boot generation: check that every listed key is empty; if so, write all their
    descriptors; if any is taken, write none, turn the synth UI off and log it in the black box.
  - Every listed key is below claim()'s lowest (0x1C1), so the result is the same whichever runs first.
- **Allow-list,** from the clean keys: empty after stock's builder, no immediate in either core, not
  claimed.
  - Analog: Sync **0x1C0**, Glide **0x1B7**.
  - Drums (10): 0x1B4, 0x1B3, 0x1B2, 0x1B1, 0x1B0, 0x1AF, 0x1AE, 0x1AB, 0x1A7, 0x1A4.
  - Track FX (19, later): 0x1A2, 0x1A0, 0x19F ... 0x18B, 0x132, 0x12D (the next 19 of the clean list).
    That leaves 3 spare: 0x12A, 0x129, 0x128.
  - S6a reviews all 12 Analog and drum keys and writes descriptors only for groups in the image.
- **Descriptors:**
  - Sync is a type-7 list (Off / On), like claim().
  - Glide 0..100 needs a numeric descriptor type. Copy the type and format of a reviewed native 0..100
    key (open item in S6a).
  - Shape uses the native Position key 0xC9, retitled per page.
- **test_keys on 1.6.8 (T6):**
  - stock's builder, then claim(), then synth_keys(): the sets are disjoint, and every listed key was
    empty before and has a descriptor after;
  - zero immediates for each listed key in both cores, so a future base that uses one fails;
  - one key pre-occupied: nothing written, UI off;
  - stock's encoder event path driven for each synth key with set_property (0x0810B898 / 0x0810B920)
    logged: no call carries a synth key;
  - mutant: Sync hard-coded to 0x1CF is caught.
- **Limit of the evidence:** keys computed as base + i, or stored in u16 flash tables, are invisible to
  the scan. The device check therefore edits Sync and Glide and confirms no native parameter on the track
  changed (section 10).

## 6. CPU: budget, measurement, and efficiency

### Budget (estimates, about 2x spread)
- **Block:** 1,280,000 cycles = 2.67 ms at 480 MHz, which is 128 frames at 48 kHz (I; S6a confirms the
  frame count).
- **SPACED today:** LOAD average 59-65 %; 0.03-0.3 % of blocks over budget.
- **Analog (emulator):**
  - WT1 about 7.1 k and WT2 about 6.8-7.7 k instructions per block, so about 14 k per voice: 1.1-2 % at
    1-1.8 CPI;
  - a root has 10 voices, so a fully sounding track costs 11-20 %;
  - one such track on SPACED means about 70-85 % average; two push sustained load past 100 %.
  - Not known: the net cost against a native Wavetable voice with a table loaded (Analog replaces stock's
    384-instruction kernel read), and whether idle voices render at all.
- **Drums, per voice (at about 1 CPI, ±2x):** Kick 2.8 %, Tom 2.6 %, HiHat 1.7 %, Snare 1.4 %, Clap 0.5 %.

### What happens on overload: the evidence (Jeff asked)
- **Reset: no path found from CPU load alone.**
  - Stock refreshes the watchdog only when audio makes progress (window about 0.28 s; site 0x0808EA34).
  - A late render delays progress but doesn't stop it. A reset would need one block to stall for about
    105 block periods.
  - Even 8 Analog tracks at 10 voices (model: about 160 %) don't come near that.
  - The one hang we know is ext-pool exhaustion (`b .` at 0x080A3B20), which is memory, not CPU. The
    reservation's 1 MB margin covers it.
  - Static analysis only; never tested on the device.
- **Sequencer slip: plausible (I).**
  - The player advances per rendered block (TICKS_PER_BLOCK; test_s3_m7 runs it that way).
  - A render that misses its DMA half is dropped (AUDIO `events - blocks`), and with it 2.67 ms of musical
    time.
  - Inside the box that sounds like a hiccup. Against external MIDI clock, other gear or our MIDI-out
    timing, it is a real drift.
- **Link side effects: possible, unconfirmed.** Sustained overrun mis-framing is one of the candidates for
  the unexplained input freeze and corrupted Controller frames.
- So overload glitches, and may also slip timing. That is enough to propose a guard, but not to build one
  before measuring. The guard is open decision D1, not S6a work.

### Measurement (S6a must)
- **M7, in the DSP ELF:**
  - DWT CYCCNT around the enter and oscillator hooks, accumulated per block;
  - the block boundary comes from a LOAD_RING_IDX change, so m7.elf is untouched.
  - The stats lines hold:
    - blocks with Analog active, cycles sum and per-block peak;
    - the most oscillators rendered in one block;
    - a histogram of Analog's share of the block (<5, 5-15, 15-30, 30+ %);
    - blocks over 100 % LOAD while Analog was active;
    - the largest frame count seen (n);
    - blocks where sync was skipped because n > 128;
    - a one-shot handle check: root+0x0C compared with the M4 handle in the table.
- **M4:** at save time an `ANALOG ...` line beside LOAD in BENTO_SAVES.log (through X6, written only from
  the save path that already runs after stock's project write).
- **On screen:** the Analog pane's text line shows `CPU nn%` (from the black box's live LOAD counters,
  differenced every 500 ms), plus `!` if any block went over budget in the last 2 s. This is the "visible
  limit": it shows the load, it doesn't limit anything.
- **Emulator:**
  - T2 records instructions per voice-block for 1 and 10 voices, with and without sync, as an upper-bound
    assert, so a regression in the DSP fails a suite;
  - T2 also runs a native Wavetable voice, for the net-cost comparison.
- **Device (S6b):** DWT figures plus CPI = cycles / emulator instructions.

### Efficiency: ways to make Analog cheaper on the M7
Per-block parameter work is already per block upstream: pitch, level and shape are computed once per
oscillator per block. What remains is the per-sample kernel (about 35-40 instructions per sample, I) plus
stock's helpers, which a native voice pays too.

| # | Change | Est. saving | Risk to sound | S6a? |
|---|---|---|---|---|
| E1 | **Specialise the loop**: separate paths for free-running / sync master / sync slave, and for blend 0 (pure saw), blend 1 (pure pulse) and mixed. Hoists the per-sample `sync && i<128` / `master && i<128` tests and the 255 store out of the loop | 15-30 % of the kernel | none (bit-identical to the reference, tested) | **yes** |
| E2 | **One reciprocal of inc per block**: edges use multiplies instead of 3 divides (vdiv about 14 cycles) | under 1 % at 260 Hz, about 5 % at 4 kHz | last-bit edge position, inaudible; tolerance test | **yes** |
| E3 | **Skip silent oscillators**: WT2 at level 0 and not a sync slave renders nothing (upstream does); WT1 at level 0 as sync master runs only the phase/event tracker | up to 50 % of a voice in those settings | none (the output is silence) | **yes** |
| E4 | **Skip idle voices**: if T2 shows slot 18 runs for voices whose amplitude envelope is idle, render nothing for them. This is not dropping a voice: the post would output exact zero | up to (10 - sounding) / 10 of Analog's cost | a wrong idle test cuts notes. Adopted only with a bit-identical T2 proof, else moved to S6b | **conditional** |
| E5 | **No libm**: glide coefficient from stock's exp2 0x080518C0, cached when the glide time changes, instead of powf per block | about 0.01 % (needed anyway: no libm) | none | **yes** |
| E6 | **-O2 for the kernel file only** (zig cc is clang, which ignores `optimize` attributes), -Os elsewhere | 10-20 % of the kernel, +about 350 B | none | **yes** (kept only if T2's count drops) |
| E7 | **Throttle the shape publish**: clean the display line every 8th block and once per root, not per oscillator per block | about 100 cycles per oscillator-block | none (display only) | yes, if P4 is in |
| E8 | 2x unrolled free-running path (CMSIS style) for the M7's dual issue | 5-15 % more | none if bit-identical | S6b, only if the kernel dominates |
| E9 | DSP code in ITCM (copied at boot by CTRL_STARTUP), against flash plus the 16 KB I-cache shared with all of stock | 0-15 %, unknown | none to sound; a crash if stock uses ITCM (not surveyed) | S6b experiment, after a static ITCM-use scan |
| E10 | Hot state in DTCM | - | DTCM is an active stock arena, about full | **rejected** |
| E11 | State in the AXI slice | about 0.04 % per oscillator at most (5 lines in SDRAM per block) | the slice is the M4's Device memory and nearly full | **rejected** (oscillator state stays in stock's object) |
| E12 | Fixed point | negative: stock's buffers are float, the FPU is single-cycle, conversions cost | quantisation | **rejected** |
| E13 | Cheaper BLEP or a table | none: the 2-point polynomial is about 4 FMAs per edge; a table means more memory traffic | - | **rejected** (a higher-order BLEP is a sound upgrade with a cost, not a saving) |

My estimate for the S6a set (E1-E3, E5, E6) is 15-30 % off a voice in emulator instructions, more with E4
when few voices sound. T2 records before and after.

**Drums, later (S6c/S6d, measured first):**
- **Stop at -96 dB:** when an engine's envelope is below about -96 dB, write cursor = frames so the native
  voice ends. Big savings with overlapping hits; the tail it truncates is inaudible.
- **Per block, not per sample:** move static coefficient updates (filter and resonator settings from
  knobs) to per block. Pitch envelopes stay per sample: that sweep is the kick's sound. Est. 10-30 % on
  Kick/Tom.
- **Cheaper noise:** xorshift32 per cell instead of rand().
- **Math:** stock's exp2 and a polynomial sin instead of libm.

## 7. Analog design notes (S6a)

- **Hooks:**
  - The enter hook (A1) records the voice and the track's mode from the per-root cache. A miss rereads the
    16-line Analog table (a few lines, not upstream's 128-entry scan).
  - The oscillator hook (A2) handles WT1/WT2 of Analog voices only. The sub oscillator is a different
    class and stays native.
- **Fixes to upstream logic:**
  1. A per-root cache replaces upstream's single-entry cache, which missed on every lookup with two Analog
     tracks.
  2. Glide's last note is per track (in the per-root cache), not global.
  3. The glide offset added to the sub (voice+0x4C, +4) is removed when the track stops being Analog.
     T2 shows whether stock rewrites the field.
  4. Sync events cover 128 frames (the 128 B array fits the 0xC0 state limit). If n > 128, sync is
     skipped for that block and counted; S6a confirms n = 128.
- **The M7 <-> M4 key is root+0x0C = the M4 track handle.** Playground relies on this from one unit's use.
  S6a traces it offline: the root ctor is called once from the project loader 0x080586C4 with what the M4
  sent. The black box confirms it on the device.
- **Switching back to plain Wavetable** leaves our bytes in the kernel history: at most a one-block
  glitch, accepted.
- **ABI:**
  - The DSP ELF is hard-float. Its entry points take only integers and pointers, so the vtable words
    point at it directly and our soft-float m7.elf never calls a float function.
  - Stock's reset sets FPSCR to flush-to-zero and default-NaN. Host reference tests use a tolerance (FMA
    is emitted too).

## 8. Carrier WAV plan (S6c)

- **Generator:** `tools/carriers/gen_carriers.py`, deterministic, our own code. It writes `BE Kick.wav`,
  `BE Snare.wav`, `BE HiHat.wav`, `BE Clap.wav` and `BE Tom.wav`: mono, 48 kHz, 16-bit.
  - 32 signature samples (the 0xB1E70A5C pattern at ±1 LSB);
  - then 32 samples carrying a "BE" magic, the engine id and a format version;
  - then silence: about 4,800 + id frames (about 9.6 KB each, not 288 KB). That works only because we own
    the cursor.
- **Kit:** `package_m1.sh` runs the generator into `flash\bento_enhanced-v4.30\Drum Synths BE\` and
  records each file's SHA-256 in the kit manifest. The WAVs are never committed. The checklist tells Jeff
  to copy the folder to the card. Our code creates no file and calls no FatFS.
- **M7 (authoritative):** the carrier is identified from the voice's descriptor, i.e. the sample already
  in SDRAM:
  1. frame-count prefilter;
  2. mono (0x08054F40);
  3. rate 48000 (0x08054DC0);
  4. 32 samples read (0x08054D60) against the signature, then the magic.
  Playground's 144019 + id - 1 carriers are also accepted.
- **M4 (UI only):** base-name match on the file node's Filename (key 0x81, through 0x08134D6A, which reads
  model data, not the card). If the two disagree, the M7 wins for audio:
  - a renamed carrier plays the synth behind a sample page;
  - a real "BE Kick.wav" that isn't a carrier plays as a sample.
- **Engine choice:** a per-pad engine override in the knob table, saved as `bento_drum_engine`, wins over
  the carrier's id.

## 9. One-shot parity checklist (drums)

Rule: synth state is per **native voice**. The synth writes into the voice's own scratch, then calls the
native post chain. Columns: how it holds, then how it is shown (offline / device).

| # | Behaviour | How | Offline | Device |
|---|---|---|---|---|
| 1 | Conditions incl. NOT / 1ST / PRE | upstream of the sampler, inherent | test_s3_m7 path unchanged | 1:2 and NOT on a synth pad |
| 2 | Retriggers incl. MIDI-off variants | case 0x12 / 0x13 unchanged | voice restart test | 1/16 x4 retrig on a Kick pad |
| 3 | Strike velocity | reaches voice velocity; DSP never multiplies by it | **velocity applied once** (mutant: double multiply) | synth pad vs sample pad, with and without a Velocity->Level route |
| 4 | Hold-step / inspect | M4 only | - | hold a synth step, edit |
| 5 | Pad defaults (velocity / probability / offset) | native parameters | - | defaults on a synth pad |
| 6 | Per-pad level / pan | post chain | post chain applied (output scales with level) | |
| 7 | Sends | track mixer | - | reverb / delay send on a synth pad |
| 8 | Native filter / amp envelope | post chain | filter and envelope state advance | cutoff and decay audibly act |
| 9 | Choke (per track in 1.6.8) and pad mute | native stop or release; the cell is freed when the voice ends | native stop ends the cell | choke group synth <-> sample pad; pad mute |
| 10 | Recorded aftertouch | pressure +0xC4 through routes | - | record AT, Pressure->Filter route |
| 11 | Poly / mono, voice stealing | native; cells follow native voices | two voices on a Poly pad; same-voice restart touches no other cell | Poly vs Mono pad, fast rolls |
| 12 | Poly cap, Note Mod | upstream | - | Note Mod on a track with synth pads |
| 13 | Pitch, fine tune, pitch modulation | native ratio formula (evaluator + exp2) | ratio equals stock's computation | tune +12 / fine |
| 14 | LFO1 | we tick 0x08064FA0 on voice+0x5D8 as the entry does | LFO1 state equals native | LFO1 -> pitch |
| 15 | Sample-only parameters (Start, Length, Reverse, Slice) | ignored, hidden on the synth page | none breaks the synth | |
| 16 | Loop on (root+0xEC) | zeros once the synth is done; voice kept natively | voice stays, output zero | |
| 17 | Voice end | cursor = min(elapsed, frames - 1) while ringing, frames when done; native end check; never voice[0] | cursor ends the voice | no click at the tail |
| 18 | Save / load of knobs | X1 / X2, `bento_drum_*` | stock writer / reader round trip | save, power cycle, reload |
| 19 | Launch state on pads, gray notes on muted pads (1.6.8) | native | - | look |
| 20 | Carrier missing / renamed | falls back to the sample | detector negatives | rename test |
| Known gap | Pad copy / duplicate / undo | knobs revert to defaults (D9) | - | documented in the checklist |
| Gate | Carrier on Sampler / Loop / Shredder / Multisample tracks | synth only on one-shot drum tracks (D8) | non-one-shot root passes through | carrier on a Sampler track stays near-silent |

**Voice allocation rewrite:**
- Cells are keyed by (native voice pointer, start serial).
- Both voice-start hooks call `drum_voice(voice, root)`. It frees or reuses the voice's own cell and takes
  a free one otherwise. A restart of the same voice retriggers only that voice.
- A cell is freed when its voice's active byte is 0, when the voice restarts on a non-synth sample, or
  when it has had no render for 2 or more blocks of our own M7 block counter (not the M4 tick).
- The pool has 32 cells. Over 32: D2.

## 10. Toolchain and build groups

**Groups:** `analog` (S6a) and `drums` (S6c).
- Add both to PATCH_GROUPS (with distinct description prefixes), GROUP_NAMES, GROUP_TITLES, SOURCES and
  KNOWN_NAMES.
- **1.6.1:** BASE_SETTINGS `groups` becomes an explicit tuple, plus `default_off=("analog","drums")`.
  `plan()` would otherwise include any group whose entries resolve.
- **OMIT_DEFINES:**
  - `-DBE_OMIT_ANALOG`, `-DBE_OMIT_DRUMS`;
  - `-DBE_OMIT_SYNTH` when both are omitted (the reservation, the m4_diag.c call-out, the layout).
- **Dependencies:** `analog` / `drums` need `poly_cap` and `bbox` (section 4).
- **test_m1.sh:** the NEEDS table gets the new suites. Both copies stay identical (`wsl/` and
  `tools/lab-build-wsl/`).

**DSP ELF (`dsp.elf`, new source kind):**
- zig 0.15.1, C++:
  - `-target thumb-freestanding-eabihf -mcpu=cortex_m7+fp_armv8d16sp -mfloat-abi=hard`;
  - `-Os` (the kernel file -O2);
  - `-ffreestanding -nostdlib -nostdlib++ -fno-exceptions -fno-rtti -fno-threadsafe-statics`;
  - `-fno-unwind-tables -fno-asynchronous-unwind-tables`;
  - `-Wdouble-promotion -Werror`.
  Clang has no `-fsingle-precision-constant`: write f-suffixed constants, and the symbol check catches the
  rest.
- No `-mstrict-align`: that flag is the M4's.
- Our own memset / memcpy inside the ELF; no libm (our own sin and exp shims, or stock's exp2).
- **The build refuses:**
  - any undefined symbol;
  - `__aeabi_f*` (the soft-float trap);
  - `__aeabi_d*` and `__aeabi_*ldivmod`;
  - `.data`, `.bss`, `.init_array` and `.ARM.exidx`;
  - an origin other than M7_DSP, or an ELF past M7_DSP_LIMIT.
- **Single precision only.** Unicorn's M7 has no f64, so SP keeps the DSP runnable in our suites. Stock's
  own double instructions keep test_s3_m7's F64 shim.
- **Layout:** `mods/m1/dsp/`:
  - `analog.cpp` (our rewrite; header credits bento-playground analog.h, GPL-3.0-or-later);
  - `dsp_entry.s` (the vtable trampolines: save r0-r3 and keep the stack arguments; for D3, d0-d7 and APSR);
  - `dsp.ld`;
  - later `daisysp/` (the vendored subset, unmodified files with their MIT notice, plus `LICENSE`) and
    `drums/*.cpp`.
  - The S6a ELF has no third-party code beyond the credited rewrite.

**M4 synth UI ELF (`m4_synth.c`, `m4_synth.ld`) at SYNTH_UI.**

**Exit-check script:**
- copy rc19's `final.sh`, `c161.sh` and `cov.sh` to `rc2x/<agent>/` and keep the cases: c161, ident,
  t161, b168, t168, ref, pkg, tab, cov, names, eol;
- new cases:
  - `dsp`: each refusal, with a soft-float build, a .data build and an oversize build;
  - `bis168`: the `BE_DISABLE=analog` manifest against BE4.10;
  - `keys`: T6.

## 11. Sub-sprints

### S6a: synth keys + Analog (kit BE4.20)
Offline work:
- [ ] **a0 Infrastructure:**
  - the groups and refusals (section 10);
  - M7_DSP window and `check_dsp_assumptions`;
  - the combined reservation and `check_region_assumptions` with TOTAL;
  - SYNTH_AREA layout header and generation handshake;
  - `synth_keys()` and test_keys T6;
  - the SYNTH_UI block, the X6 call-out and the black-box ANALOG line.
- [ ] **a1 Open facts, settled offline before any hook code.** Each goes into facts.py with evidence:
  - frames per render (n = 128?);
  - the caller of slot 18, and whether it skips idle voices (decides E4 and the worst case);
  - root+0x0C = the M4 handle (trace from 0x080586C4);
  - the synth view's Position field (+0x11CA4) and the pane parent pointer +0x2C;
  - M4 type byte 18 = Wavetable;
  - Glide's descriptor type;
  - whether 0xCF on the synth page reaches A9 (T4);
  - whether the encoder bar rereads through CONTEXT_GET.
- [ ] **a2 M7 Analog:** our kernel rewrite with E1-E3, E5, E6; the enter / oscillator hooks; the per-root
  cache, per-track glide, sub-offset cleanup and the n > 128 sync guard; the stats.
- [ ] **a3 M4 Analog:**
  - the menu (A3-A5, action 0x10F4; Wavetable 0x102C clears Analog; the native action posts
    {0x42,0,6,idx}, with the selected track from SCALE_CONTEXT + KEY_SEL_TRACK 0x131);
  - the key copy A6; the pane text and paint A7 / A8 at 1.6.8 offsets; A9;
  - X1 / X2 dispatcher (`bento_analog`, `bento_analog_sync`, `bento_analog_glide`) and X3;
  - the CPU indicator.
- [ ] **a4 Tests,** in new suites test_analog.py and test_inst_xml.py, plus test_keys:
  - **T1**, our DSP on stock's oscillator ctor and note start:
    - pitch, sync, glide decay, level 0 silent;
    - saw / square / 8 % pulse;
    - NaN guards, n = 64 / 256;
    - a non-Analog oscillator passes through.
  - **T2**, the whole voice:
    - stock's root ctor with a bump-allocator stub, then slot 18 through the built image;
    - 1 and 10 voices; Analog off bit-identical to stock; instruction-count bounds;
    - a native Wavetable voice for comparison; E4's proof.
  - **T3**: the stock writer 0x08130484 and reader 0x081309A0 with our hooks, together with
    STEP_SAVE/LOAD: `bento_*` round trip; a non-Wavetable object untouched; the preset caller 0x0813163C.
  - **T4**: synth page builder 0x081612E4: keys only on Analog tracks; CONTEXT_GET answers Sync / Glide and
    passes 0x8D / 0x8E; 0xCF taken before the model; menu cell 9 shows "Analog"; 0x102C clears it.
  - **T5**: pane tick and paint at +0x26C / +0x2DC in the display emulator.
  - **T6**: keys (section 5).
  - **Mutants:**
    - 1.6.1 pane offsets;
    - WT2 at +0x36C8;
    - the oscillator hook always returning 0;
    - sync events not cleared;
    - a soft-float DSP ELF (refused);
    - Sync hard-coded to 0x1CF;
    - a second reserver (the Region must stay on: test_slice);
    - the M4 writing before the generation handshake.
- [ ] **a5:** kit `flash\bento_enhanced-v4.20` (new folder) with `docs/checklist-v4.20.txt`.

Exit (offline):
- 1.6.1 is 25877c59... and every old bisect builds unchanged;
- the 1.6.8 default build is BE4.20, and every suite is green on both bases;
- `BE_DISABLE=analog,drums` on 1.6.8 differs from BE4.10 only in the listed constants and the label;
- every refusal fires;
- image at most 0x18D334; the DSP inside its window;
- every new entry REVIEWED (none machine-mapped only);
- coverage shows stock's voice render, oscillator, voice post, XML writer / reader, page builder and pane
  code executed.

Device checks (checklist-v4.20.txt; starts only after v4.10 passed):
- [ ] **Boot:** BE4.20 label; `ext left` = the v4.10 value - 32,768; Region stats live (curves sent
  counts); RAMCHK quiet; the black-box handle check says root+0x0C = handle.
- [ ] **Menu:** the 9th cell is "Analog" and creates an Analog track; choosing Wavetable turns it back.
- [ ] **Sound:** note 60 at about 261.6 Hz on a tuner; Position sweeps saw -> square -> pulse; Sync
  on / off audibly different; Glide 0 / 50 / 100; native filter / envelope / LFO / sub act; a 10-note
  chord.
- [ ] **Keys:** editing Sync and Glide changes no native parameter on that track or the project (a short
  watch list on the Wavetable pages).
- [ ] **Save and reload,** power cycle, preset save / load: Analog, Sync and Glide come back. A plain
  Wavetable track with a WAV is unchanged.
- [ ] **CPU spot check:** SPACED plus one Analog track with 1, 4 and 10 voices. The ANALOG and LOAD lines
  are logged, `CPU nn%` is visible, and there is no reset (the full check is S6b).
- [ ] **Regression:** the v4.10 core items (curves, conditions, retrig, AT, hold shortcuts).
- [ ] **Rollback:** to v4.10 (the Analog track becomes an empty Wavetable track, no crash) and to stock.

### S6b: CPU re-check on the device
- [ ] SPACED plus 0 / 1 / 2 / 4 Analog tracks (sustained chords), and stopped runs for the
  `H_play = (seq_avg - f*S)/(1-f)` correction.
- [ ] Record:
  - LOAD average and bins, over-budget blocks;
  - AUDIO `events - blocks` (dropped renders), `wrong_half`, `head_min`;
  - ANALOG cycles per voice-block, and CPI from T2's counts;
  - a native Wavetable track with a table loaded, for the net cost.
- [ ] Listen for glitches. Check timing drift against an external MIDI clock or a recording of a click
  track.
- [ ] If the kernel dominates: E8 (unroll); after a static ITCM-use scan, an E9 experiment kit (BE4.21x).
  Otherwise nothing.

Exit:
- a written result in this doc: per-voice cost, the load per Analog track on SPACED, whether renders drop
  or timing slips;
- D1 decided with that evidence; the drum budget (D2) set.

### S6c: first drum engine, Kick (kit BE4.30)
- [ ] Sites D1-D3, D5, D6 reviewed (1.6.1 counterpart by hand + fingerprints; 1.6.8 in facts.py),
  including the +2 / +3 event codes and the registry keys inside D5 / D6.
- [ ] The DaisySP subset vendored with its MIT notice; `rand()` replaced by per-cell xorshift state in the
  synth area.
- [ ] Cells, `drum_voice()`, the render hook (knobs, pitch, scratch, cursor, LFO1, post chain); the
  carrier detector; the block counter; the drum stats line.
- [ ] Carrier generator and kit folder (section 8).
- [ ] UI: refresh / event wrappers, the hidden byte instead of the paint filters, the knob table,
  `bento_drum_*` through the shared X1 / X2, the 10 drum keys.
- [ ] Suite test_drums.py on stock's real path (sampler -> VOICE_START -> hooks -> render entry -> post
  chain, as test_s3_m7 does), covering:
  - synth output; velocity once; two voices on a Poly pad;
  - a restart on the same voice; a native stop ending the cell; the cursor ending the voice;
  - Loop on; the 32-cell overflow (D2); the non-one-shot gate (D8);
  - the Kick DSP against a host build, with a tolerance;
  - mutants for each.

Exit: as S6a's, for `drums`. Device: the section 9 checklist on a Kick pad, plus the S6a regression and
rollback items.

### S6d: Snare909, Clap, HiHat, Tom (kit BE4.40)
- [ ] Each engine is DSP plus a knob map; no new site.
- [ ] HiHat noise from the per-cell RNG; Tom shares the Kick's DaisySP code.
- [ ] The drum efficiency items, measured in the emulator.
- [ ] Device: each engine's knobs; a parity spot check (items 3, 9, 11, 17); SPACED with all five engines
  plus one Analog track, logged as in S6b.

**Milestone M3** = S6d passing on the device.

## 12. Upstream design choices we don't take

| Upstream | Problem in our firmware | Ours |
|---|---|---|
| Fixed registry keys 0x1A0..0x1D0, 0x142 | 6 drum keys and Sync collide on 1.6.8 (Sync = our AT Rec and a native M7 command key); the "owned" check fails silently | reviewed allow-list, all-or-nothing, logged |
| Global retitle of descriptor 0xC9 (Position) | renames Position on every page and track | slot title after the page build |
| Drum voices tracked per pad; note-on frees the pad's slots, older voices restart | Poly pads play mono with a hard cut | cells per native voice |
| Output multiplied by voice velocity | velocity twice with a route; a response without one | routes only |
| 16 slots freed on a 5 s M4 tick or a full carrier | about 8 busy pads exhaust them; the M7 reads M4 RAM at a 1.6.1 address | freed when the native voice ends, M7 block counter |
| Writing voice[0] to end a voice | clicks | cursor = frames, native end path |
| `rand()` global state | our ELF loader refuses .data; shared state across voices | per-cell RNG in the synth area |
| Fixed RAM carved from the M7 heap (0x24040000..0x24048000) | a 2.4 KB heap cut once boot-looped SPACED | SDRAM area, one reservation |
| FatFS on the M4 UI (carrier header reads, folder and file creation) | breaks our no-FatFS rule | kit carriers, model-only detection |
| 288 KB carriers | wastes sample memory per pad | about 9.6 KB, our cursor |
| 1-entry Analog track cache, 128-entry scan per voice per block | misses every time with two Analog tracks | per-root cache, a 16-line table |
| Global glide "last note" | glide breaks across tracks | per track |
| Sub glide offset never removed | stuck detune after leaving Analog | removed on mode change |
| Sync events assume n <= 128 silently | wrong sync for other block sizes | checked, counted |
| Analog depends on the drum knob table (engine 13) | Analog can't ship first | separate small table |
| Menu action id 0x1052 | private id inside stock's space | 0x10F4 |
| Per-step synth locks in the note's +0x14 word | be_step_v1 owns it | out of scope |
| 6 paint-filter hooks | 6 extra sites | widget hidden byte |

Where our investigators went further than Jeff's decisions, I took these out:
- infra's governor (a per-voice deadline and an adaptive cap), for Analog;
- the drums investigator's default cap of 8 cells with stealing: that breaks one-shot parity before any
  measurement says it is needed.
Both are open decisions now (D1, D2).

## 13. Risks

| Risk | Why it bites | Mitigation |
|---|---|---|
| CPU overload with no cap | 2 fully sounding Analog tracks on SPACED model above 100 %; renders may drop and timing slip | S6a measurement and indicator; S6b before drums; D1 |
| Hidden key use | base + i keys and flash tables are invisible to the immediate scan | all-or-nothing allow-list; T6's set_property log; the device watch list |
| root+0x0C is not the handle | Analog settings would land on the wrong track or none | a1 trace; black-box check before anything else on the device |
| X3 on CONTEXT_GET | 343 callers: a bug breaks every page; a little M4 time on every call | key-range test first; T4 runs stock pages through it; pass-through test for m4_step's own reads |
| A9 misses 0xCF | edits on our keys reach the model | T4 decides; fallback to the model event entry (D4) |
| Reservation change | a wrong fast path silently turns off the Region (poly cap, curves, Note Mod) | one code path, check_region_assumptions on TOTAL, a test_slice mutant, `ext left` and curve stats on the device |
| M7-only reset | zeroes the synth area under the M4 | generation handshake, M4 re-publish |
| Soft-float or double creep | silent library calls, wrong ABI with stock's s0 returns | symbol refusals; the CPU string fixed in build.py |
| Stock drops `bento_*` on its own save | a project saved on stock loses Analog / knobs | documented; rollback check |
| Carrier on a melodic Sampler track | plays the synth pitched | D8 gate |
| M4 room | 32 KB; drums plus FX could reach 22 KB | sized at link time; COND_UI repack reserve |
| Device time | four kits, each needing Jeff's run | every kit has an offline exit first; the checklists are short and ordered |
| Next base (1.6.9) | more vtable and offset entries to re-derive | everything in tables, the offsets as entries |

## 14. Open decisions for Jeff

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Last-resort CPU guard | (a) none; (b) "brake": when LOAD over the last 8 blocks averages above 110 % **and** a render was dropped, Analog / drum output goes silent for 64 blocks (voices, envelopes and the sequencer untouched), counted and shown | decide after S6b. Build (b) in S6c, on by default, only if S6b shows dropped renders or a timing slip with at most 2 Analog tracks on SPACED. It is a bypass, so it needs your OK |
| D2 | Drum voice limit | 32 cells; over that (a) steal the oldest with a one-block fade, (b) the new hit plays near-silence; a CPU cap or not | 32 cells, (a), no CPU cap until S6b's numbers say otherwise (same policy as Analog) |
| D3 | On-screen load indicator | `CPU nn%` + `!` on the Analog pane; or the black box only | the pane indicator (about 300 B); the drum page gets the same in S6c |
| D4 | Edit intercept | synth view event slot A9 (a word, synth page only) or the model event entry (code, every page) | A9; the model entry only if T4 fails |
| D5 | Persistence | `bento_*` XML attributes (presets carry them) or the sidecar | XML (your 2026-11-03 decision stands) |
| D6 | Drum menu entry | none (load "BE Kick.wav" in the browser) or a menu entry through stock's file-onto-pad loader (stock's FatFS, on our request) | none for S6c / S6d; revisit after M3 |
| D7 | Velocity -> Accent | routes only (parity), or also drive the engine's Accent | routes only. First check on the device whether a fresh pad has a Velocity->Level route |
| D8 | Carriers outside one-shot drum tracks | gate (synth only on one-shot tracks) or allow pitched synths on Sampler tracks | gate |
| D9 | Pad copy / duplicate / undo | knobs revert to defaults (known gap), or remap handles later | accept for M3 |
| D10 | claim()'s keys on property keys | keep (device-proven on 1.6.1) or move to clean keys on 1.6.8 only | keep, unless v4.10 shows AT Rec / Out anomalies |
| D11 | Glide behaviour | always from the track's previous note (upstream) or legato only | always, per track |
| D12 | First drum engine | Kick (brings the DaisySP, hard-float and RNG risks forward; the clearest signal) or Snare909 (our code, smaller) | Kick |

## 15. Blocking S6a

1. **v4.10 on the device.** S6a's offline work can start now. BE4.20 flashes only after the v4.10
   checklist passed, and its `ext left` reading sets the reservation check's expected value.
2. **The a1 facts,** before hook code: frames per render; whether slot 18 runs for idle voices; root+0x0C
   = the M4 handle; the view's Position field and the pane parent pointer; type byte 18; Glide's
   descriptor type. The handle question is the one that could change the design: if it fails, the table
   key moves to a track index.
3. **Jeff's answers to D3 and D4** (D1, D2 and D6-D12 can wait for S6b / S6c).
4. **Toolchain:** the hard-float DSP ELF path and its refusals have to exist in build.py before any DSP
   code is linked (a0).

## S6a: synth keys + Analog (2026-11-08, offline; device check open)

*Superseded where the review round below says otherwise (BE4.21: memory ownership, the take, Glide's units, the
CPU indicator, retirement, the checklist). The v4.20 kit stays as built and is not to be flashed.*

Built and tested offline: `BENTO_BASE=1.6.8 build_m1.sh` is **BE4.20** (VERSION 4.20), kit `flash\bento_enhanced-v4.20`,
checklist `docs/checklist-v4.20.txt` (flash only after v4.10 passed). 1.6.1 is still 25877c59... (v3.72), every
old bisect too. Scratch: `rc20/s6a/` (scripts named below).

**What is in it (group `analog`, 1.6.8 only; on in a plain 1.6.8 build, `BE_DISABLE=analog` for a bisect).**
- M7, hard-float DSP ELF (`dsp/analog.cpp` hooks, `dsp/analog_kernel.cpp` kernel, `dsp/analog.h`, `dsp/dsp.ld`) at
  M7_DSP + 0x100 = 0x080D0100, 6,656 B of the 36 KB window; two trampolines at M7_DSP (build.py assembles them,
  `m7_dsp()`); A1 (root slot 18) and A2 (oscillator slot 0) are vtable words. The kernel is a readable rewrite of
  bento-playground's analog.h (GPL-3.0-or-later, credited in the file), no third-party code.
- M4, `m4_synth.c` at SYNTH_UI = 0x081C5500 (BE4.10's image end): entry table (slot 0 `an_tick`, slot 1 `an_print`,
  called from m4_diag.c's m4_tick and save dump: X6), the menu label, the X1 / X2 / X3 wrappers, then the code
  (3,503 B). Sites A3, A4 (menu cell 9 and count), A5, A9 (vtable words), A6, A7 (bl), X1, X2, X3 (entries).
- Memory: one reservation of 48 KB (m7_polyat.c `region_reserve`, under `#ifndef BE_OMIT_SYNTH`): the synth area
  0xC5FF4000..0xC5FFC000 (`synth_shared.h`) below the unchanged Region; taken only if the pool keeps 1 MB.
- Black box: `ANALOG gen= keys= tracks= | blocks= avg_cyc= (pm=) peak_cyc= share<5%=.. 5-15= 15-30= 30+= osc_peak=
  voices_peak= renders= n_max= sync_skipped= misses= | handle seen= ok= | ui menus= edits= saves= loads= full=` at
  every save.

**a1 facts (settled offline, in facts.py with evidence).**
- Frames per render: 128. Engine buffers are allocated with capacity 0x80 (`movs r1,#0x80; bl 0x08052FC0`, five
  sites, one 0x400 buffer), and stock's load figure divides by 1,280,000 cycles (literal 0x138800 at 0x0805833C):
  2.67 ms at 480 MHz = 128 frames at 48 kHz. A render can be shorter (buffer views); sync covers up to 128 and a
  longer block is counted (`sync_skipped`), never wrong.
- Slot 18 runs for active voices only: its one caller (0x0807AFCE) skips a voice whose byte 0 is clear
  (0x0807AFB4), and the driver clears it when the amp envelope ends (0x0807BA62). So **E4 is moot**: stock already
  renders nothing for idle voices; not built.
- root+0x0C = the key the M4 created the instrument with: root vtable slot 10 is `str r1,[r0,#0xc]` (0x08054540),
  the engine input handler gives a free root (key 0xFFFFFF00) the key from the M4's message (0x08059C66..76) and
  finds roots by it (0x08059C0C). On the M4 an object's handle is its +0x10 (RESOLVE_OBJECT matches it). Static
  evidence on both cores; the black box's `handle ... ok=1` confirms it on the device (checklist item).
- M4 type 0x12 = Wavetable: 10 `blx; cmp r0,#0x12` sites on both bases (and 0x13: 13 / 13, 0x0E: 5 / 5).
- Glide's descriptor: type 10, 0..1000, as stock's Pulse Width (0xC5; `regdump.py`); our token is "" (a pointer to
  our empty string). Sync: a type 7 two-entry list, as claim().
- 0xCF on the synth page: the synth view's handler takes it itself (tbh index 2, 0x08161158) and re-posts it to
  the model as 0xD0; A9 (the view's slot) sees it first. **D4 settled offline for A9**; the model-entry fallback is
  not needed. Not verified on the device yet.
- The synth view's Position field and the pane parent pointer: not needed (no pane drawing in S6a, A8 not built).

**Decisions and deviations from the plan (implementer).**
- **One synth group, `analog`**, `-DBE_OMIT_SYNTH` when it is left out (1.6.1). No `BE_OMIT_ANALOG` yet (nothing
  shares a source with drums); S6c splits it. 1.6.1's BASE_SETTINGS groups are now an explicit tuple
  (`GROUPS_V372`) with `default_off=("analog",)`: its manifest lists analog as omitted and disabled; the image is
  unchanged.
- **SYNTH_AREA and SYNTH_UI are not table entries**: the area is derived from EXT_POOL_END in `synth_shared.h`, so
  m7_polyat.c (shared with 1.6.1) names no 1.6.8-only entry; SYNTH_UI is our layout (build.py `layout()`), and
  m4_diag.c gets it as `-DBE_SYNTH_UI` (only when analog is in the image and not disabled), not as an `S_` name
  (core code must not need a 1.6.8-only entry).
- **M7_IMAGE_END not added**: `check_dsp_assumptions` checks the window plus a 0x300 B guard below it are zero, no
  PC-relative load reads a word pointing into it, the words that do point into it are exactly DSP_WINDOW_WORDS
  (7 data words in the SD-reader image and M4 rodata, as on 1.6.1), and no movt builds its upper half.
- **The trampolines live in build.py** (keystone, check_branches), not in a `dsp_entry.s`: the existing M7
  trampolines are built that way. They save r0-r3 (+r4 for alignment) and lr; no VFP registers (AAPCS: s0-s15 are
  the callee's, and neither slot takes a float argument).
- **The M7 never writes an M4 line, so it does not zero them** when it takes the area (it zeroes its own: header,
  stats, run state, root cache). The M4's table survives an M7-only reset; the generation moves and the M4 bumps
  its serial. A new M4 boot generation zeroes the M4's lines; Analog flags come back from the project's XML. The
  plan's "M4 re-publishes from the model" has nothing to publish from: stock's track objects have no field of ours.
- **No `early` counter**: before the handshake the M4 may not write the area at all, not even a count. A load that
  early would be lost; the checklist compares `tracks=` with the project after an autoload.
- **.ARM.exidx**: clang emits a "cannot unwind" entry per function on ARM even with -fno-unwind-tables; dsp.ld
  drops them (as m7.ld) and refuses `.ARM.extab` (real unwind data); `.init_array` is KEEP'd so a constructor is
  refused, not silently dropped.
- **Glide's coefficient uses stock's exp2 0x080518C0** (`EXP2`, 2^x from the ±10-octave table), not OCT_RATIO
  (0x0804F5E0 is the same table times 32.703 Hz: the pitch, Hz). Cached per root for (glide, n) (E5).
- **The Track FX key list is not reviewed here**: the plan's "0x19F ... 0x18B" is not contiguous-clean (0x19A is
  stock's Freeze); test_keys checks Analog's 2 and the drums' 10.
- **D3 (on-screen CPU indicator) not built**: it needs A8 (pane tick) and is Jeff's open decision; the black box
  line is there. **A8 (pane tick / paint) not built**: the panes keep stock's drawing; A7 retitles their text.
- **Menu cell write in a bisect**: the 20-byte cell is written even with `BE_DISABLE=analog` (only writes of 8 B or
  less are skipped), but the count stays 8, so the cell is never shown.

**Efficiency (emulator, instructions per voice-block for an_enter + WT1 + WT2, n = 128, note 48, `measure.py`;
each variant compiled by build.py's compile_dsp at the real placement).**

| variant | bytes | saw | mixed | pulse | mixed+sync | silent master+sync | saw+glide |
|---|---|---|---|---|---|---|---|
| upstream-shaped (no E1-E3, E5; kernel -Os) | 3,276 | 9,950 | 9,950 | 9,690 | 11,118 | 9,720 | 10,377 |
| only E1 | 5,012 | 7,484 | 9,968 | 8,662 | 11,131 | 9,733 | 7,574 |
| only E2 | 3,284 | 9,958 | 9,958 | 9,698 | 11,123 | 9,725 | 10,385 |
| only E3 | 3,452 | 9,946 | 9,946 | 9,686 | 11,114 | 7,650 | 10,372 |
| only E5 | 3,292 | 9,951 | 9,951 | 9,691 | 11,119 | 9,721 | 10,351 |
| only E6 (kernel -O2) | 3,304 | 9,954 | 9,954 | 9,694 | 10,990 | 9,592 | 10,381 |
| all but E6 | 5,212 | 7,489 | 9,969 | 8,667 | 11,132 | 7,658 | 7,552 |
| **all (S6a)** | **6,656** | **6,999** | **9,977** | **8,677** | **11,012** | **7,668** | **7,062** |

- E1 (specialised loops): -25 % saw, -11 % pulse, 0 % in the mixed regime (it is the generic loop); +1.7 KB.
- E2 (one reciprocal per block): no instruction saving by construction (a vdiv is one instruction); its saving is
  in cycles (vdiv about 14 cycles), which the emulator does not count; within the golden tolerance.
- E3 (a silent sync master only tracks its phase): -21 % in that setting.
- E5 (glide coefficient cached): -26 instructions per block, as predicted (negligible; needed anyway: no libm).
- E6 (-O2 for the kernel): -6.5 % saw, -1 % sync, 0 elsewhere; +1.4 KB. Kept (the plan's rule: T2's count drops).
- E4: moot (see a1). E7-E13: not adopted (plan).
- Whole voice (T2, stock's root ctor and slot 18 through our trampoline): native Wavetable voice with no table
  6,458 instructions per block; Analog saw 12,789: **about 6,300 per Analog voice-block on top of the native
  voice**. At 1-1.8 CPI that is 0.5-0.9 % of the 1,280,000-cycle block per voice, 5-9 % for 10 voices; to be
  measured on the device (S6b, the ANALOG line). The net cost against a native voice *with* a table loaded is not
  measured (the rig has no wavetable).

**Tests (new: test_analog 20, test_inst_xml 5, test_synth_ui 10; extended: test_keys +5, test_polyat +3,
test_diag 1 assertion; test_slice / test_s3_m7 know analog is 1.6.8-only).**
- test_analog: stock's oscillator ctor and note start, our hooks through stock's own vtable words, stock's
  helpers: note 60 = 261.6 Hz (±0.5), an octave doubles it, saw / square (50 %) / 8 % pulse, level 0 silent
  without running the kernel, glide from the track's last note and its decay (0.001^(n / (s·48000)), ±0.002),
  the sub follows and gets its offset back when the track stops being Analog, hard sync (periodic at the master's
  period; control without sync), 256-frame blocks render without sync and are counted, a non-Analog oscillator
  and an area that is not ours both go to stock's render, the per-root cache refills only when the M4's serial
  moves (two tracks: 2 refills, upstream's one-entry cache missed on every lookup). Golden: `an_render` against an
  independent float32 model of upstream's algorithm (5 shapes x 3 rates, master events, slave resets, NaN / range
  guards), tolerance 2e-5. T2: stock's WT root ctor (10 voices from the named allocator, stubbed as a bump
  allocator), slot 18 through the root's vtable word into stock's voice code and post. Cost bounds per regime.
- test_inst_xml (T3): stock's instrument writer, XML library, registry builder and instrument reader:
  bento_analog / _sync / _glide written for an Analog track only, read back into the table; a plain track's load
  clears a stale line; nothing written into the area before the M7 took it; stock's reader ignores our attributes.
- test_keys (T6): the allow-list is empty after stock's builder and below claim()'s 11 keys; no movw / mov.w /
  cmp.w loads either core's code with any of the 12 (a scan of all forms finds 40 add / and / bic with the same
  numbers: struct offsets; the control, KEY_SEL_TRACK, is found); synth_keys() writes both descriptors on the real
  registry, and writes none when one key is taken (black box: which one).
- test_synth_ui (T4, partial): the menu cell and count in the image, every site's target, our action = stock's
  Wavetable action ({0x42, 0, 6, track}, then {0x16}) + the flag, stock's Wavetable action clears it, the page keys
  (Glide / Sync in rows 0 / 1 slot 3, Analog tracks only), CONTEXT_GET answers our keys without stock's body and
  passes the rest, encoder edits of our keys never reach stock's handler, pane texts, the black box line. **Stubbed**
  there: the selected-track lookups, UI_POST, PROJECT_TOUCH, SLOT_TITLE and the native handlers; stock's page
  builder 0x081612E4 and the menu code are not run (T4's full form, and T5, remain open).
- Mutants (`mut.sh`, each caught): the oscillator hook never taking a voice (test_analog: 8 failures, 24 errors), the master
  never writing its wraps (1), the M4 marking before the handshake (test_inst_xml), no 1 MB margin (test_polyat),
  the 16 KB reservation on 1.6.8 (30), Glide in WT2's slot (test_synth_ui), our edits forwarded to the model, glide
  starting from the voice's own note instead of the track's (2). DSP refusals (`dsp_ref.py`): a soft-float CPU
  string, .data, .bss, an implicit and an explicit double, a 64-bit divide, an undefined symbol, a static
  constructor and an oversize ELF are all refused; the real sources build.
- Counts: 1.6.8 diag 90 s6, polyat 48 s2, conditions 18, holdstep 18, controls 62, retrig 43, step 95 s16, mod 24
  s20, notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 9, analog 20,
  inst_xml 5, synth_ui 10. 1.6.1: as BE4.10's run except polyat 48 s6 (+3 skipped) and keys 9 s5 (+5 skipped);
  the three new suites are not run (analog omitted).

**Budgets after S6a.** Image 0x1863E8 (limit 0x18D334: 28,492 B left for M4 code). DSP window: 30,208 B left.
m7.elf 14,464 B (+224: the reservation). M4 black box 24,116 B (+24: the two call-outs; 460 B left). SDRAM: 32 KB
more reserved (`ext left` expected = v4.10's - 32,768).

**Open / not done in S6a.** D3 (CPU on screen) and A8 (pane drawing); T4 on stock's page builder and menu code, T5;
the device run (checklist-v4.20.txt); the net cost against a loaded wavetable; the M4-side encoder path's
set_property log (T6's last item); Track FX keys. D1 / D2 wait for S6b as planned.

**Exit (offline), 2026-11-08.** 1.6.1: 25877c59... for the default build and the 9 S1 reference bisects (c161),
through the helpers too (t161), all 15 suites green. 1.6.8: BE4.20 = c583cd58b968f3ab8074f7f01a98cb5297788731e0e8498b2c73509cb94af184
(reproduced twice), 18 suites green, every group's `BE_DISABLE` builds (analog off with poly_cap / bbox / diag),
`BE_DISABLE=analog` differs from the BE4.10 kit only in the M7 code (the reservation), the label, the black box
marker and the unreachable DSP / synth UI blocks and menu cell; every DSP refusal fires; every new entry REVIEWED
(regeneration reproduces stock_168.py; only the 19 mx entries open); literal check clean; all LF. Stock basic
blocks run (stockcov.py): test_analog 534 (M7), test_inst_xml 720 (M4), test_synth_ui 42, test_keys 409. Not
covered: stock's page builder and pane code (T4 / T5 open). Kit `flash\bento_enhanced-v4.20` (SHA256SUMS.txt).

## S6a review round: BE4.21 (2026-11-08, offline; device check open)

Two reviews of BE4.20 (behaviour, build / plan) found 3 blockers and 12 should-fixes. All are addressed below; the
image is **BE4.21**, kit `flash\bento_enhanced-v4.21`, checklist `docs/checklist-v4.21.txt`. BE4.20 was never
flashed; its kit stays as it was (the rule: existing kits are not modified) and is not to be flashed. Scratch:
`rc20/s6a2/` (`final.sh`, `mut.sh`, `dataend.py`, `cost.sh`; the static traces `callers.py`, `up.py`, `sdis.py`,
`words.py`, `mcmp2.py`).

**Design changes.**
- **Who holds the truth.** The M4's Analog table is now its own: **AN4**, 256 B of our reset-surviving slice
  (`AN4_SLICE` = slice + 0xE20 .. + 0xF20, taken from the end of the black box's canary, which now stops there on
  bases with the synth groups; 1.6.1 keeps its canary). An entry is {handle, flags | glide << 4 | seconds << 16}.
  It needs no M7, so a project load is kept whenever it happens. The synth area's 16 lines are AN4's published copy,
  written only by the UI task (`an_tick`, and right after a UI edit) once the handshake holds. The plan's "re-publish
  after an M7-only reset" now has something to publish from.
- **Why the old design lost loads (static evidence).** Stock's project and preset loads run in their own task: the
  instrument reader 0x081309A0 is called from the project loader 0x081313EC, which is called only from command
  handlers dispatched by the command queue consumer 0x0812B27C, which runs from 0x08114EA0: the entry of stock's
  "pcmStreamer" task (its thread definition at 0x08198ACC: name, entry), not defaultTask (0x08114E34, the one before
  it, whose loop calls our m4_tick). So a load can come at any
  time, also before the black box's first tick publishes the config line. `an_load` and `an_save` run in that task;
  every AN4 change is made with interrupts masked (a few stores); only the UI task writes the synth area.
- **The take.** `h_consume` (once per audio block, trace group) calls `m1_region_poll()` -> `region()`, so the
  Region and the synth area are taken at the first block after the config line, not at the first note (under
  `#if !defined(BE_OMIT_SYNTH) && !defined(BE_NO_ANALOG)`: 1.6.1 and the `BE_DISABLE=analog` bisect keep the
  v4.10 timing). Without trace (`BE_DISABLE=trace`) the take stays at the first note; nothing is lost then (AN4).
- **The Region alone when the pool is short.** If the pool keeps less than SYNTH_TOTAL + 1 MB, `region_reserve`
  takes the Region alone (16 KB, as v4.10), clears a stale synth header while its address is still free memory, and
  writes `SYNTH_TAKE` (M7 private slice line + 0x10, cleaned): mode and a count of Region-only takes. The fast path
  accepts both pool ends (synth area, Region). The black box prints the reason ("area not taken (the pool kept less
  than 1 MB: Region only, times=n)").
- **Boot stamp.** The M7 header carries `boot` = the config line's generation (`SA_LAYOUT` 2). The M7 stamps it at
  the take and restamps it when `region()` sees a new M4 generation; the M4's `ready()` requires it, so a header a
  reset left in SDRAM is never trusted.
- **Root reuse (B1).** The per-root cache keeps the handle it was filled for and refills when root+0x0C differs:
  stock frees a root with key 0xFFFFFF00 (0x08059C28) and gives the next track the first free root (0x08059C5E).
- **The sub's glide offset (S1).** `sub_follow()`: the AnOsc state (now 156 B) remembers the sub's +0x04 as we left
  it; if stock rewrote it (voice note start 0x0807C7F8..C80C), its value is the new base. Leaving Analog also zeroes
  both oscillators' state once (N1: the native kernel's history is zeros, not our phase and 0xFF events).
- **Glide units (S2).** Stored and published exactly as the encoder gives it, 0..1000 (= 0..2 s); the DSP uses
  s = glide x 0.002. (The descriptor is unchanged: type 10, 0..1000.) BE4.20's 0..100 was never on a device.
- **Retirement (S3).** Any of stock's instrument menu actions (`ACT_INST_FIRST` 0x1025 .. `ACT_WAVETABLE` 0x102C,
  reviewed from the menu table) clears the current track's entry; a project or preset load of a non-Analog
  instrument clears its handle; a sweep (one entry every 250 ms) retires an entry whose handle no longer resolves to
  a Wavetable on two visits and is at least 30 s old (a load in progress is not mistaken for a dead track); a full
  table takes the oldest such entry. A new boot gives kept entries the boot's time. Picking Analog again keeps
  Sync / Glide (N7).
- **A deferred instrument swap (S5).** Whether stock's swap is done when UI_POST returns is unproven (UI_POST
  0x08171D94 bubbles synchronously up the view tree, but what handles {0x42, ...} was not traced). The menu action
  marks the handle at once if it is a Wavetable, and `an_tick` watches the track for a new Wavetable handle for 10 s;
  any other instrument action cancels the wait.
- **CPU indicator (S4 / D3).** Hazard 4 of the task and requirement 5 make a visible indicator required, so D3 is
  no longer optional: built as **A10**, the synth view class's tick slot (0x081A175C, slot 0 = SYNTH_VIEW_TICK
  0x0815F580; 1.6.1 0x0819E894 / 0x0815D7D0, the same shape, offsets of the grown view). At most four times a second,
  on an Analog track, it sets WT1's "No WAV" label (view + `VIEW_NOWAV_LABEL` 0x3114, the widget stock's builder
  titles at 0x0815FF5E; the same offset on 1.6.1) to "Analog CPU nn%" (+ " !" for 2 s after a block over budget) with
  stock's SLOT_TITLE (copies, marks only a change), then calls stock's tick. The figure is the M7's LOAD average from
  the black box's counters (D0L), differenced every 500 ms in `an_tick`; "Analog (DSP off)" when the M7 has not taken
  the area. The ANALOG line adds `load_pm` and `over_with_analog` (blocks over 100 % while an Analog voice rendered:
  the `over` stat, now counted by the M4 in AN4 because it is the M4's figure; the M7 stats line keeps a spare word).
  Not drawn: the pane's wave (A8). Where the label shows is a device item.
- **Key ranges (S4, build).** test_keys decodes stock's key-range tests (sub rX, rY, #K; cmp rX, #N; bhi default;
  adr + ldr.w pc word table, following a test that repeats itself): Sync 0x1C0 is in M7 0x0805D4F4 (0x1B8..0x1D1)
  and M4 0x08132E80 / E88 (0x1B8..0x1C7), and both tables send it to the default case; Glide 0x1B7 is in none. The
  drum keys' one range, M7 0x08059990 (0x18C..0x1B6, stored into a native table: no default case), is pinned for S6c
  to re-review. facts.py KEY_AN_* cite this.
- **Smaller ones.** `an_osc` tests `area_ok()`, not the header magic (N2). `check_dsp_assumptions` also checks the M7
  .data end (`M7_DATA_END`, the startup table word at 0x08040568) is below the window and its guard (N3; dataend.py
  shows it refuses). A `BE_DISABLE=analog` bisect now leaves the 20-byte menu cell stock (`patch(..., site=True)`,
  N1 of the build review). `m4_synth.ld`'s comment says + 0x100.

**Argued, not changed.**
- N3 (behaviour): `area_ok()` reading the pool counters can, during one of stock's allocations, say "not ours" for
  one call: that oscillator then renders natively (silent) for one block. Kept: it is the price of never using the
  area when it is not ours (N2), and allocations happen at project load.
- N6 (build): `an_context` answers for the selected track. Stock asks for our two keys only while the synth page
  shows that track (the keys are in no other page's table).
- N8: glide decays once per block (2.67 ms steps). Inaudible at musical glide times; very short glides step.
  Per-sample glide is an S6b item if Jeff hears it.
- The M7 header vs SDRAM after an M7-only reset: between the reset and the M7's first block the old header (same
  boot stamp) is still there, while the pool is stock's again. The M4 writes only on a change in that window, and
  stock's allocator reaches the pool's top 48 KB last. Accepted, a window of milliseconds.
- A handle stock reuses for a *new* instrument (not loaded, not made from the menu on that track) within 30 s of the
  old one's removal would inherit the old Analog flag. Handles of loaded instruments are rewritten by the load; the
  menu clears the replaced handle. Not traced how the M4 allocates handles; a device item covers the common paths.
- T4 / T5 in full (stock's page builder 0x081612E4 and menu code in the emulator): still not done. Instead the
  design no longer depends on the unproven ordering (the deferred-swap watch), the indicator test runs stock's real
  SLOT_TITLE on the label, and test_synth_ui runs 67 stock basic blocks (was 42).

**Exit (offline), 2026-11-08.**
- 1.6.1: default 25877c59526fc95e4ae5122e7f41128f36815aac950ff8713a372461142cec3f = the v3.72 kit, and the 9 S1 reference
  bisects (c161); the 18 old BE_DISABLE names IDENTICAL to HEAD 9a62ed2 (ident); 15 suites green with the same
  executed counts (diag 91 s7, polyat 50 s8, keys 10 s6: every new test skips on 1.6.1; the three Analog suites do
  not run).
- 1.6.8: **BE4.21 = 85291c75b0e31d0f8cb4dd151cad8583a2fd83b4e4c7aaeae222d5abede81a8e** (reproduced), image 0x186BC0
  (26,484 B left below 0x18D334), last write 0x081C6BC0. 18 suites green: diag 91 s6, polyat 50 s2, conditions 18,
  holdstep 18, controls 62, retrig 43, step 95 s16, mod 24 s20, notemod 16, notemod_route 8, notemod_real 14, slice 20,
  s3_m7 25, step_xml 11, keys 10, analog 23, inst_xml 5, synth_ui 24.
- Every BE_DISABLE group builds; analog goes off with poly_cap, bbox, poly, diag. `BE_DISABLE=analog` (71491fac...)
  against the v4.10 kit: the DSP and synth UI blocks (unreachable), the M7 code (14,240 -> 14,664 B: the reservation,
  the fallback, the boot stamp), the M4 black box code (same size: the canary bound), the label. mx still refused,
  misspelt names refused, every DSP refusal fires, regenerating stock_168.py is identical, literal check clean, only
  the 19 mx entries open.
- 16 review mutants (`mut.sh`, each on a copy of the tree; the repo is never edited) are all caught.
- Instructions per voice-block (test_analog Cost, n = 128): saw 7,042, mixed 10,431, square 10,431, pulse 8,748,
  saw+sync 8,225, mixed+sync 11,502 (BE4.20 with the same suite: 7,023 / 10,412 / 10,412 / 8,729 / 8,206 / 11,483: the
  fixes cost 19 per voice-block). T2 whole voice: native 6,472, Analog saw 12,808, **extra 6,336** (BE4.20: 6,331).
- Budgets: DSP 6,840 B of the window (29,768 B left); m7.elf 14,680 B of 19 KB; synth UI 5,513 B code; SDRAM as
  BE4.20 (48 KB). Stock basic blocks run: test_analog 548 (M7), test_inst_xml 720, test_keys 409, test_synth_ui 67.

## Instrument handles (BE4.22, 2026-11-08, offline; device check open)

*Supersedes, in the review round above: Retirement (S3) beyond the menu clear, the deferred-swap watch (S5), the boot
re-timing of kept entries, and the open risk "a handle stock reuses for a new instrument ... within 30 s".* Jeff's
decision 7: Analog state is fully overwritten when stock creates a new instrument, so nothing can inherit it.
Investigation `rc23/handles/` (ctr.py, scan10.py, uses.py, lits.py, probe1/2.py); implementation `rc23/implH/`
(dis8.py, probe2.py re-run, mut.sh, final.sh).

**What stock does (1.6.8, static + emulator; test_handles re-runs it on every build).**
- One base constructor, `OBJ_CTOR` 0x0812E79C (10 instructions, no frame), gives every M4 object its handle (+0x10)
  from `OBJ_HANDLE_CTR` 0x30000088 (its only store and only literal; .data sets it to 1, the static constructors take
  1..3). It only goes up: **a handle is never reused within a boot**. Pool addresses are reused (a second load commit
  gets the same addresses with new handles); handles are not.
- Every copy is made by the generic node's clone (`NODE_CLONE` 0x0812F310, vtable word `NODE_VT_CLONE` 0x08199194),
  which runs `OBJ_CTOR`: **a copy always has a new handle**. Loads go into a scratch store (PresetMgr 0x30020760 +0x330)
  and are committed by `STORE_COPY_ALL` into the live store; a save writes a snapshot copy (+0x334); a preset apply and
  the clipboard use `STORE_CLONE_H`.
- Every top-level object leaves through `NODE_DESTROY` 0x0812E8CA (`NODE_VT_DESTROY` 0x08199190): the store clear
  before a commit, `STORE_REMOVE` (the instrument menu's replace 0x0813F730), a failed `STORE_CREATE`.
- The instrument menu's swap is synchronous: UI_POST -> the root view's slot 0x30 (0x081702F4) -> the model command
  handler 0x08141EA4 -> case 0x42 -> 0x0813F730 (REMOVE, then CREATE), all before UI_POST returns.

**What that meant for BE4.21 (a bug, not only the named risk).** `an_load` marked the *scratch* object's handle, but
the live track is a clone with a new handle, so a loaded project or preset came up without Analog. `an_save` looked up
the *snapshot* clone's handle: no entry, so `bento_analog` was never written. Copy / paste lost it too (the cause of
D9). test_inst_xml could not see this (it saves and loads the same object). test_handles reproduces it: with the clone
word put back to stock's, the live copy has no entry and the save writes nothing. BE4.21 was never flashed. The named
risk (a reused handle inheriting the flag) is nil within a boot, but real across an M4 reset (the table survives in the
slice, the counter starts again).

**The hooks (group `analog`, 1.6.8 only; `BE_DISABLE=analog` leaves all three sites stock).**
- **H1, `OBJ_CTOR`** (4 bytes `074a0260` -> `b.w` to a wrapper in the synth UI block): push r0-r3, r12, lr (stock's
  constructor writes only r1, r2, ip and the flags, so a caller may keep r3 across it; the wrapper keeps everything),
  `an_forget()`, pop, replay `ldr r2,=OBJ_BASE_VT; str r2,[r0]` (build.py checks the literal), `b.w` back.
  `an_forget` drops every entry whose handle is at or above the counter: such a handle names no object of this boot.
  In practice the first of stock's static constructors at M4 boot (counter 1) drops the whole table the previous boot
  left; a new object of this boot can never find an entry. Before the table exists (no magic: a cold start) it only
  reads. Cost: a 16-entry scan per object (unlocked; the lock only when there is something to drop).
- **H2, `NODE_VT_CLONE`** -> `an_clone`: stock's clone, then, if the source has an entry, the copy gets the same flags
  and Glide. The load commit, preset apply / save, the save snapshot and paste all keep Analog, and `an_load` /
  `an_save` work as written.
- **H3, `NODE_VT_DESTROY`** -> `an_destroy`: the object's entry goes, then stock's destroy. Children are freed by
  stock directly (never through slot 2) and never carry an entry.
- **Gone:** the 30 s sweep, `AN4_FAIL`, `AN4_RETIRE_S`, the boot re-timing, the 10 s watch (`pend_*`) and the entry
  word's seconds. **Kept:** the menu's clear of the current track for any stock instrument action (stock may keep the
  object when Wavetable is picked on a Wavetable track: 0x081376C4 not traced); the immediate mark after UI_POST, now
  with `menu_miss` counted when the track is not a Wavetable then (the device evidence that the swap is synchronous);
  N7 (picking Analog on an Analog track keeps Sync / Glide), now also when stock makes a new object [changed in the
  review round below: only when the track keeps its object]. `an_save` writes
  our attributes only for an object whose own type is Wavetable (in place of the sweep's type check).
- **Capacity (AN4_N = 16, unchanged; the M7's 16 lines unchanged).** Entries now also exist for copies: a load's
  scratch, a save's snapshot, the clipboard. At most 8 live tracks plus the 8 copies of one commit or snapshot in
  progress are needed at once, so a full table gives up its *oldest copy*: the lowest handle that does not resolve in
  the live store ([MODEL], RESOLVE_OBJECT). Handles only go up, so that is the scratch before the snapshot before a
  paste. A live track is never given up; with no copy to give up, the mark is lost and counted (`full`). Roads not
  taken: moving the entry on the load commit and dropping it after `an_save` (fewer entries, but it depends on which
  store a copy comes from and on a save writing each object once: not proven); 4 more AN4 entries in the freed header
  words (needs a live-only publish to keep the M7's 16 lines). Eviction needs neither.
- **AN4 header:** words 7-8 spare, 9 `carried`, 10 `dropped`, 11 `forgot` (counted from the table's cold start, so
  H1's work at boot shows), 25 `evicted` (was `retired`), 28 `menu_miss`. Black box: `... full= evicted= menu_miss= |
  handles ctr= carried= dropped= forgot=`.
- **New reviewed facts (1.6.8 only, absent on 1.6.1):** OBJ_CTOR, OBJ_BASE_VT, OBJ_HANDLE_CTR, NODE_VT_DESTROY,
  NODE_DESTROY, NODE_VT_CLONE, NODE_CLONE (build), STORE_INIT, STORE_POOL_INIT, STORE_CREATE, STORE_REMOVE,
  STORE_COPY_ALL, STORE_CLONE_H (tests). PresetMgr and the menu replace are documented here, not table entries (no code
  or test uses them).

**Tests.** test_handles (new: 16, 1 of them only on a bisect). It runs stock's .data copy and static constructors, its
store, node constructor, clone and destroy through the patched sites, its instrument writer / reader on real nodes and
its registry; nothing of stock's is stubbed:
- handles only go up and copies get new ones (address reuse included);
- the real round trip: writer -> reader into a scratch object -> commit copy -> the live track is Analog -> snapshot
  copy -> the writer writes it;
- paste carries, a plain source makes no entry; 8 Analog tracks survive a load and two saves (scratch copies given up,
  no live entry lost, full=0); only a Wavetable saves our attributes;
- entries of an earlier boot are gone after the static constructors (forgot=4); a cold boot writes nothing into the
  slice; a stale entry for the next handle is not inherited by a create, a commit copy or a clone by handle; the
  constructor wrapper keeps r0, r3-r11 and does stock's stores;
- remove and a commit free the entries; a 13th object, which stock refuses, is destroyed through H3;
- each H-site put back to stock's bytes in the emulator makes its test fail (BE4.21's behaviour); the bisect class
  checks the three sites are stock's on `BE_DISABLE=analog`.

test_synth_ui: the deferred-swap and sweep tests are replaced (the swap inside UI_POST is marked; no watch afterwards;
menu_miss; N7 with a new object; a full table gives up the oldest copy; a table of live tracks counts `full`; entries
of an earlier boot dropped by the tick; a dead handle is not swept). test_inst_xml: the rig's counter is set above its
handle. Mutants (`rc23/implH/mut.sh`, each on a copy of the tree), all caught: H1 dropping only `== counter`, H2 not
carrying, H3 not dropping, eviction ignoring liveness, no type check in an_save, the wrapper not keeping r3, the menu
not keeping Sync / Glide, the tick not dropping stale entries, the H1 patch left out, menu_miss not counted.

**Device items (checklist-v4.22.txt).** Save an Analog project, power-cycle, load it: still Analog with its Sync /
Glide (if v4.21's checklist is run first, its save / load item should fail exactly so); copy / paste an Analog track;
a new Wavetable from the menu after a warm reset is plain; the ANALOG line's `forgot`, `carried`, `dropped`,
`menu_miss`.

**Risk left.** H1 runs inside stock's static constructors at M4 boot, the earliest code of ours on the M4: it reads the
slice (AXI SRAM, which stock's own M4 code also uses) and, after a warm reset, writes it with interrupts masked for a
few stores. test_handles runs stock's real init array through it; the device's first boot of BE4.22 is the check.

### BE4.22 review round (rc23, 2026-11-08)

- **Picking Analog again (behaviour S5, build S1).** As first built, the menu copied Sync and Glide from the
  destroyed object onto the new one stock's case 0x42 creates, while stock's own Wavetable settings on it start from
  defaults: half old, half new, and against Jeff's decision 7 (a new instrument inherits nothing). Now Sync / Glide
  are kept only if the track still has the same handle after UI_POST (stock kept its object); a new handle starts as
  AN_ON with Sync Off and Glide 0. The same-handle case is counted as `menu_same` (AN4 word 29, was r29; black box
  `menu_same=`): it is also what a deferred swap would look like, which `menu_miss` could not see on a plain
  Wavetable track (behaviour I3). Open for Jeff: if he wants N7 back for this case, it is one line.
- **AN4 magic "AN42" (build I5).** The table's layout changed in BE4.22, so a table left by v4.21 in the slice is
  started again instead of being read with v4.21's `pend_*` words as `carried / dropped / forgot`.
- **facts.py OBJ_CTOR** says 13 instructions (it listed 13 and said 10).
- Tests: test_synth_ui's N7 test is split into "same object keeps Sync / Glide (menu_same=1)" and "a new object
  starts as a plain Analog (menu_same=0)"; the black box test reads `menu_same=0`.
