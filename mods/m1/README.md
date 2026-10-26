# M1: polyphonic aftertouch out of External tracks

**Status:** poly aftertouch and the per-track polyphony cap work on hardware. **Since
v2.3 M1 is built directly on stock Bento 1.6.1.** Earlier builds sat on top of
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
CH10, but CH10 crashed on the device without any of this code (an M7 HardFault in
`HAL_DMA_IRQHandler`) and showed a sequencer/snap-mode problem. Lab's trigger
conditions are being ported separately (S2).

Holding pads on an External track sends per-note **Polyphonic Key Pressure**
(`0xA0 note value`) on the track's MIDI port and channel, alongside the normal notes.
It has been verified on a MIDI monitor and on a poly-AT synth, with each held note's
aftertouch driving its own LFO rate.

## How it works

Stock firmware already does most of the work and then throws the result away:

1. Each pad's pressure reaches the audio core (M7) unmerged. The pad router
   (`FUN_08085540`) turns every pressure change into an internal **`0x0E` event
   `{target, note, value}`**. `value` uses the **same curve as velocity**
   (`min(raw, 0xD000) × 127 / 0xD000`), and `note` comes from the track's keyboard
   mapping.
2. The event is delivered to the External track's own handler (`FUN_08062020`),
   which has no case for `0x0E` and **drops it**.

The mod adds that missing case:

| Where | Change |
|---|---|
| M7 `0x08062466` | The handler's "neither note-on nor note-off" branch now goes through a trampoline. Every event type other than `0x0E` continues exactly as stock. |
| M7 `h_poly` (`m7_polyat.c`) | If the track's MIDI out is on (`track+0x86D1`): post `{0x5F, 0, port=track+0x31, ch=track+0x32, note, value}` to the MIDI-out queue stock's own note-on producer uses (`msg_send(engine+0x7C8, …)`) |
| M4 `0x081BEC00` | Handler for event type `0x5F`: a copy of stock's Note On case with status `0x90` → `0xA0`. The builder checks the copy against the native bytes. |
| M4 `0x081052A4` | Jump-table slot `0x5F` (unused in stock) → that handler |

**M1b: polyphony cap (per track, hard-wired 6 until the settings UI).** All
External MIDI notes are posted through five `bl msg_send` sites; each now goes
through an 8-byte stub that adds the track object (`r4`, or `r6` at the stop/all-off
site) and calls `h_note`:

| Site | Role |
|---|---|
| `0x0805F92C` | sequencer Note On (`note_route`) |
| `0x0805FF50` | pad Note On |
| `0x0805FE44` | sequencer Note Off |
| `0x08060088` | pad Note Off |
| `0x08061966` | Note Off on stop / all-off |

- A Note On past 6 sounding notes sends a Note Off for the **oldest** first. That
  note's later native Note Off is **swallowed**, so it can't cut a newer note on the
  same number.
- A Note On for a note that's **already sounding retriggers the same voice**
  (Note Off, then Note On) and owes one native Note Off.
- Everything else passes through unchanged. Before the state region exists (very
  early boot), all notes pass through.
- Pressure is only sent for sounding notes, so stolen notes go quiet.
- State (voice lists for 16 tracks × 8 voices, 72 pressure entries, per-port
  budgets) lives in a 4 KB region the M4 allocates once at boot and the M7 owns.
  The pointer is tied to the M4 boot generation, so nothing stale survives a reset.

**Output control.** DIN MIDI carries ~1,000 messages/s, and the M7 → M4 MIDI ring
has only 16 slots with no overflow check. So pressure is sent:
- per note: **changed values only, at most every 10 ms (100 Hz)**. A held-back
  value goes out with the next pressure event for the track;
- per DIN port: at most **400 messages/s**, in bursts of up to 4 (v1 had one global budget).

Measured on hardware: minimum gap 10.6 ms, no repeated values
(`logs/M1_run1_polyat.log`).

## Diagnostics (development builds)

**v2.2 (black box).** Device logs from v2.1 showed crashes outside our code: two
M7 faults on floating-point instructions in stock code (`vmul.f32` in DSP, `vpop`
in the M7 message loop, the latter leaving the audio engine parked while the UI kept
running) and one M4 fault in the USART1 interrupt. MIDI ring overflow was ruled out
(peak 4/16). v2.2 adds:
- **live crash capture:** the UI thread watches the stock crash counter and an M7
  heartbeat; a new crash or a stopped audio core immediately writes the crash record,
  counters and the last trace records;
- **stack-depth probe:** every hook records the lowest M7 stack pointer (the M7 stack
  bottom at `0x24048DA0` sits directly above Lab's state and our slice);
- **post-mortem:** the previous boot's last trace records and counters (they survive
  reset in RAM) go into the next boot's header;
- the previous-log copy moved to the first real write (on the device it ran before
  the SD card was mounted); 256 KB buffer; leaner logging.

**v2.1 (crash evidence).** After one unexplained instant reboot during M1b
testing (not reproducible), the logger was extended:
- every boot's log header carries **stock's crash record** from the RTC backup
  registers (written by the M7 fault handlers and kept across reset): crash count,
  cause, **faulting PC**, core, timestamp, stack pointers, plus all 32 raw registers;
- the previous boot's log is copied to `\BENTO_DIAG_prev.log` before anything
  is written, so a crashed session survives the reboot;
- writes append only new text (stock FatFS honours `FA_OPEN_APPEND`) instead of
  rewriting up to 64 KB on the UI thread;
- every post to the 16-slot M7→M4 MIDI ring measures its fill. The summary line
  reports `max_fill` and `overflows`, and any overwrite is always logged. Leading
  hypothesis for the reboot: ring overflow during note bursts;
- the voice and pressure updates run with interrupts masked (re-entrancy guard).


`m7_diag.c` / `m4_diag.c` log the pad → MIDI path. The M7 writes records to a
RAM ring; the M4's UI thread writes `\BENTO_DIAG.log` to the SD card after the
pads have been idle for 1.5 s. The logs in `logs/` are the device runs that
located the path (`diag_run1_v2.log`, `diag_run2_v3.log`) and the first
poly-AT session. The 64 KB buffer fills quickly when every pressure message is
logged; M1b will sample instead.

## Memory and code placement (stock 1.6.1)

- **M7 heap end lowered** from `0x24048DA0` to `0x24048400` (three literals:
  `0x08040588`, `0x080A3BDC`, `0x080A3E94`; the other occurrences are the stack
  bottom and its sentinels, untouched). This reserves `0x24048400–0x24048DA0`, below
  the M7 stack and inside the M4's MPU window: our slice `0x24048500–0x24048A00`, and
  `0x24048A60+` for the trigger-condition counters.
- M7 code `0x080DA000–0x080DB800`: inside stock's zero gap after the M7 image's data.
- M4 code **appended** after stock's end (`0x081B3F3C`): `0x081B4000–0x081B5000`
  reserved for the condition UI, black box from `0x081B5000`, `0x5F` handler at
  `0x081B6580`. The image is `0x176600` bytes, under the largest size the bootloader
  has accepted (`0x18D334`).
- **Never touch `0x081E0000`+.** The M7's MPU maps it as a 128 KB read-only data
  region, most likely persistent storage, and the bootloader refused an image
  reaching it.
  This stays clear of Lab's overlay (≤ `0x081BD8C0`) and of a word CH10 references
  at `0x081BF6C0`.
- **The image must stay exactly CH10-sized.** The bootloader rejected a 0x1A0800-byte
  image ("unable to program / verify main firmware"), and the builder enforces this.

## Build and test

Uses Lab's pinned toolchain (zig cc, keystone, Unicorn 2.1.3). See
[`tools/lab-build-wsl/`](../../tools/lab-build-wsl/): `build_m1.sh` builds from
CH10, and `test_m1.sh` runs `test_diag.py` and `test_polyat.py` (168 emulated checks).

```
python build.py --base <CH10 bento1.bin> --out <new dir>
python test_diag.py   <CH10 bento1.bin> <out dir>
python test_polyat.py <CH10 bento1.bin> <out dir>
```

The builder checks the original bytes at every patch site and the base image's hash,
and proves that reversing every write reproduces CH10. Tool gotchas it guards against:
- keystone mis-encodes Thumb-2 conditional branches to absolute targets, so
  conditional branches are hand-encoded and checked against the original instruction;
- zig cc strips symbols without `-g`;
- lld emits an ELF-header segment below the link origin, which is never written.

`package_m1.sh` makes a flashing kit. **It contains your stock firmware: keep it
private.** Install with the normal bootloader procedure (bento1.bin + bento2.bin on
the card root, hold power, REC). Restore by installing CH10 or stock the same way.

## Not yet

- Pressure for other MIDI-out track types (Granular, Multisample, Wavetable). They
  have their own handlers, so the hook moves to the pad router's `0x0E` emission.
- Settings UI (poly cap, linear/exp/log curves); recording pressure into the sequencer.

## Trigger conditions (since v2.4; NOT, 1ST, PRE since v2.9)

A port of [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
T2 trigger conditions (GPL-3.0-or-later), on stock 1.6.1, for **every sequencer track
type**, extended. Turn a note's **probability** clockwise past "Always":

`1ST, !1ST, PRE, !PRE, 1:2, !1:2, 2:2, !2:2, 1:3, !1:3, … 8:8, !8:8` (74 conditions)

- `A:B` plays on pass A of every B passes of the clip (counted from launch/restart);
  `!A:B` on every other pass.
- `1ST` plays only on the first pass after launch/restart; `!1ST` on every later pass.
- `PRE` plays if the most recent `A:B`/`!A:B`/`1ST`/`!1ST` decision on the same
  sequence passed (false until there is one); `!PRE` the opposite. PRE steps don't
  change that state; plain % probability doesn't count (stock rolls it after us).

Stored in the native probability byte, with **stable values** so projects saved by
earlier versions keep their meaning: `A:B` 100–134 (as Lab), `!A:B` 135–169, `1ST`
170, `!1ST` 171, `PRE` 172, `!PRE` 173. The list order is a separate display mapping
(two 74-byte lookup tables). Stock firmware shows these notes as plain probability.

- M7: `m7_conditions.c` (`cond_decide` at the probability gate `0x08060B64`,
  `cond_loop` at the loop boundary `0x08061740`); counters `16 × 32 B` at `0x24048A60`
  (`r[0]` = PRE state), re-initialised each boot.
- M4: probability list 100 → 174 entries, labels at `0x081B4400`, lookup tables at
  `0x081B4300`/`0x081B4380`, Lab's five piano-roll/drum editor conversions rewritten to
  use them (`0x081B4000`).
- Planned: Condition and Probability as **separate encoders** (both editors), together
  with the retrigger groundwork (per-note storage + XML).
- `test_conditions.py`: every condition over two cycles, NOT = inverse, 1ST across a
  restart, PRE semantics and reset, labels, drum and piano-roll conversions for every
  value including stepping through the list one click at a time.

## Inspect a step (since v2.5; reworked in v2.7)

Stock already selects a held step (pad or touch): while you hold it, the encoders show
and edit that step. But releasing a step held for under a second toggles it, so a quick
hold-and-edit also deleted it. **Now any encoder edit while holding keeps the step on
release.** After release, the encoders show the defaults for new steps again, as on
stock (v2.5 kept the last step selected after release, as Lab does; Jeff preferred
stock's behaviour). One-shot (drum) step sequencer only; the piano roll selects notes
by touch natively.

- M4 only: `m4_holdstep.c` `step_edit`, from Lab's T2 holdstep (GPL-3.0-or-later), at
  the three step-editor encoder handlers (velocity `0x0815C7C4`, condition
  `0x0815C8AC`, timing `0x0815C990`). No RAM state; selection code untouched.
- `test_holdstep.py`: stock's own press/release/touch/edit code runs. On plain stock
  exactly the 7 edit-protection tests fail.

## Crash history (since v2.7)

`BENTO_CRASHES.log` on the card gets one line per boot (at its first log write) and one
per crash record that appears mid-session: `boot|live gen ms count cause pc core lr
frame_pc`. It is only ever appended, so every reset stays attributable even when the
session log (current + previous boot only) has moved on.

## Hold shortcuts (since v2.6)

Port of Lab's K16 hold controls (GPL-3.0-or-later) as in CH10, **without PROJ as Shift**:

| Hold | Pads | Release |
|---|---|---|
| TRACKS | top row: track 1–8 instrument page; bottom row: its sequencer (playing clip if any) | stays on the destination |
| LAUNCH | bottom row: start the track's selected clip; top row: stop it | back to your screen if used (pad or encoder); a plain tap stays, as on stock |
| MIXER | shows Solo/Mute; top row: solo, bottom row: mute; encoders work | as LAUNCH |
| INST (drum sequencer only) | pick a sound | back to the sequencer with that sound selected; no pick = stay in INST |

While a hold button is down, pads don't play notes. PROJ is not a modifier: PROJ + pad
plays as on stock.

- M7: `m7_controls_input.s` at `0x080D9000` (pad receiver `0x0808D1FE`), startup at
  `0x080D9800` (`0x080404B6`) initialises only its own state line `0x24048CA0`. Lab's
  startup also zeroed lock RAM that is heap in our layout, and its UI startup zeroed
  `0x24048000–0x24048800` (our black-box slice): both dropped.
- M4: `m4_controls.c` (consumer `0x0813F98C`, INST selection observer `0x0813BD10`,
  mixer render vtable `0x0819DD20`); state `0x24048CC0–0x24048D48`, reset per boot.
- `test_controls.py`: Lab's input and UI tests (stock's real pad receiver and UI queue
  run) plus ours for PROJ, encoder-as-use and boot reset. `lab_receiver_fixture.py` is
  Lab's fixture, unchanged. With Lab's PROJ mask the PROJ tests fail (checked).

## Audio DMA stall monitor (since v2.8)

Investigating idle restarts (stock crash cause 5, M7 watchdog). The M7 main audio loop
(`FUN_0808DF00`) refreshes the window watchdog (`WWDG1`, ~0.28 s) **only while the
audio DMA advances**, and checks DMA2 streams 5 and 7 itself. A watchdog record there
therefore means "audio DMA stopped", but stock records nothing about why. The M4 now
samples DMA2 S5/S7 every UI-loop pass; if a stream seen running stops (NDTR unchanged
≥ 30 ms, or disabled) it snapshots `LISR/HISR`, both streams' `CR NDTR PAR M0AR M1AR
FCR` and the SAI `SR/CR1` behind `PAR` (only for SAI data registers) into M4-only RAM
`0x24048400` that survives the reset, logs `!!! AUDIO DMA STALL`, adds a `stall` line to
`BENTO_CRASHES.log`, and prints the snapshot again at the next boot. The `#` status
line shows whether each stream has been seen running.

Layout change: the M4 poly-AT handler moved to its own block `0x081B8000`; the logger
slot is now `0x081B5000–0x081B6C00`; inspect-a-step wrappers `0x081B6C00`, code `0x081B6D00`.

## Version label (since v3.0)

Builds show **`BE<version>`** (Bento Enhanced) where stock shows `1.6.1`: on the boot
screen and in the `<version>-cont-<n>` info string. One `VERSION` constant in
`build.py` drives the label, the binary marker and the manifest; kits are packaged as
`flash/bento_enhanced-v<version>/`. The boot screen copies only the first 6 bytes of
stock's 8-byte version slot (`0x0818BAD4`, used by `FUN_08162950` and
`FUN_08131024`), so the label is at most 6 characters (`BE3.10` fits). v3.0 = v2.9 +
the label.

## Black box v3.1: who stalled, when, and in which firmware

- **M7 audio stall record** (`m7_stall.c`): the main audio loop's watchdog refresh
  (`bl 0x080A0460` at `0x0808DF54`, progress) and message pump (`bl 0x080816C0` at
  `0x0808DFB4`, waiting) are hooked. After 100 ms without audio progress (the watchdog
  fires at ~280 ms) the M7 records, once per boot, PRIMASK/BASEPRI/FAULTMASK, ICSR,
  MSP, the NVIC enable/pending/active words for IRQ 32–95 (DMA2 S5 = IRQ 68, S7 = 70)
  and the DMA2 flags and S5/S7 state, at `0x240484A0` (tracking line `0x24048480`
  kept separate so a cached copy can't resurrect a cleared record). Printed at the next
  boot. This separates "DMA stopped" from "DMA running but its interrupt not serviced".
- **Header:** firmware label (read from the patched version string), boot generation,
  RTC wall clock, and the previous boot's last UI-loop pass (ms and RTC). A reset long
  after the last pass happened while the UI loop wasn't running (e.g. SD Reader Mode).
- **`BENTO_CRASHES.log`** lines now carry `fw=` and `rtc=`.
- **Layout:** one table in `build.py` (logger 10 KB, hold-step, controls, poly handler);
  linker-script ORIGIN/LENGTH and the tests' addresses come from it (manifest `layout`).

## Retrigger scheduler (R2 prototype, v3.2)

`m7_retrig.c`. Stock posts each accepted note once through `note_route`
(`0x0805F8E0`) with a sample offset into the current block; its event queues are per
block, so extra hits can't be posted ahead of time. Instead:
- **accept:** a stub replaces `bl note_route` at `0x08060B9A`: stock's call runs
  unchanged, then the note's 64-bit tick (caller `sp+0x20`) queues a burst of N hits at
  `tick + k × division` (playback ticks, 960 per quarter). A new note on the same track +
  pad cancels a pending burst.
- **emit:** at the entry of the timed-note processor (`0x08060820`, once per track per
  block) each due hit goes through `note_route` with its offset from stock's own tick →
  sample conversion (`0x080A2200`), so tempo is followed; > 50 ms late = dropped.
- Velocity of hit k of N: `vel × (1 + ramp% × k / N)`, clamped 1–127.
- Burst table (16 × 32 B) lives in the M1 region (validated per boot, zeroed with it).
- **Prototype scope:** fixed settings on **pad 16** of every track (3 hits, 1/16T,
  −60 %) until per-note storage and the encoders exist; internal sound only (MIDI-out
  is masked while a hit is posted; MIDI ratchets need their own note-offs).
- `test_retrig.py` drives the real patched sites (accept stub with a stock-shaped caller
  frame, processor-entry trampoline): block placement and ramp, cut-off by the next hit
  on the pad, other pads/tracks untouched, late drop, MIDI masked and restored.

## Per-step Condition and Retrigger encoders (v3.3)

**Drum step sequencer:** encoders 5–8 = **Condition, Retrig, Division, Ramp** for the
held/selected step(s). **Piano roll:** encoder 7 = **Condition** for the selected
note(s). **Probability is plain percentages again**; a step plays only if its
condition passes *and* its probability roll passes.

- **Storage:** the note record's `+20` word (notes only; stock uses `+20` for
  automation/XY/scene items): tag `0xB5`, condition (7 bits), retrigger hits (4),
  division index + 1 (4, 0 = 1/16), ramp (signed 8 bits, %). All defaults → word 0, a
  stock-identical note. Saved as XML attribute `be_step_v1` (save hook `0x0812F4F0`,
  load hook `0x0812FBB4`, where stock has just parsed `cond` and is about to insert the
  record). **Migration:** conditions saved by v2.4–v3.2 in the probability byte
  (100–173) move into the word on load and probability becomes Always.
- **Encoders:** four empty parameter-registry keys (`*0x30031484`, 499 × 32 B) are
  claimed per boot from the top (`0x1F2` down) and registered as lists (labels generated
  from `build.py` into `step_tables.h`). Drum: keys go into stock's step-view slot table
  `0x300000BC` entries 5–8 before stock's header builder (`0x0815C27C`); values and titles
  are set after it; encoder events (`0x0815CAA0`) edit the matching held/selected steps
  (stock's matcher), publish and redraw. Piano: slot 7 (empty in stock's flash key table
  `0x0819E7FC`) is bound after stock's header (`0x081599BC`); events (`0x0815A598`) edit
  the selection list and commit through stock's `FUN_08157860`.
- **Playback:** the condition gate reads the word (`cond_decide(seq, prob, word)`): a
  condition there gates, then stock rolls the probability. The retrigger scheduler takes
  hits/division/ramp from the word (pad-16 prototype removed). Legacy byte conditions
  still play if any remain.
- `test_step.py` (13): XML round trip, foreign/automation words ignored, migration,
  key claiming and labels, header slots, drum edits (selected steps, this pad only,
  clamping, clear-to-stock, nothing selected), stock keys pass through, piano slot 7 and
  edit/commit, other piano events pass through. `test_conditions` / `test_retrig` /
  `test_holdstep` updated (word conditions vs byte conditions agree for every value).

## Defaults and locks for velocity / probability / offset (v3.4)

Drum step sequencer. **With no step held**, turning Velocity/Probability/Offset sets
the **pad default (per clip)** and every step on that pad that isn't locked follows it.
**Editing a held step locks** that parameter for the step; **setting it back to the
default unlocks** it. Steps whose value differs from the default without a lock (e.g.
recorded live) are locked when the default changes, never overwritten. Switching pad or
clip loads that pad's default (the most common value among its unlocked steps).

- Lock bits live in the per-step word, **format v2** (tag `0xB6`: ramp as a 6-bit index
  in 5 % steps, 3 lock bits); v1 words (v3.3) are still read and rewrite as v2 on edit.
- The pad default is recomputed from the notes (most common unlocked value) before
  every edit and on every header refresh: stock resets its own "new step" fields to
  100 / Always / 0 (`0x0815C060`, `0x0815CDB2`), so they can't hold the default (v3.4
  trusted them, and steps got locked instead of moved: fixed in v3.5).
- Notes without lock data (older projects) load unlocked; a value that differs from the
  pad default is locked, not overwritten, the first time the default changes.
- Notes keep their concrete values: stock firmware plays them exactly the same.
- Stock's three edit functions are wrapped (hold-step release protection, `param_pre`,
  stock's edit, `param_post`). `test_step.py` drives stock's real edit functions.

## Strike velocity (v3.6)

Drum step sequencer (step and sequence views): **a step you tap in takes the velocity
of the hit** (locked if it differs from the pad default). Touchscreen taps still get the
default. **On/off: 5th item in the hamburger menu, "Strike Vel: On/Off"** (default on;
kept across warm reboots, not power-off).

- M7: `h_input` records each pad press (`0xAC`) with stock's velocity formula
  (`min(raw, 0xD000) × 127 / 0xD000`, 0 → 100) in an M7-written line `0x24048D60`.
- M4: the drum step pad-press handler (`0x0815D1EC`) takes a fresh strike as pending;
  stock's toggle (`0x0815D114`) is wrapped and, when it creates a step, gives the new
  note that velocity. A strike is used once and only within 3 s.
- Menu: the drum step menu (id `0xFA`) is registered from a 10-slot table with count 4
  (`0x081415F8`); count → 5 and slot 5 (`0x08198E00`) = `{0x10F0, label in RAM
  0x24048D50, 0, 0, 1}`. Stock copies the label *pointer*, so the text can change. The
  selection bubbles up (`FUN_0816FC34`) to the UI consumer, where `controls_dispatch`
  toggles the setting. Stock copies label text into the menu buttons only when a
  *different* menu opens (`FUN_0817FFF8` caches the menu index at `+0xFE0` of the static
  header widget `0x30034D48`); the toggle clears that cache so the new label shows (v3.7).
- Velocity curve unchanged (stock's), as decided; tapped steps match live playing.
