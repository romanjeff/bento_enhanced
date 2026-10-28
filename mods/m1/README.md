# bento_enhanced firmware mods (stock Bento 1.6.1)

**Current release: [v3.40](https://github.com/romanjeff/bento_enhanced/releases/tag/v3.40)**
(shows as `BE3.40` on the boot screen). Built directly on **stock Bento 1.6.1**. Since
v2.3 nothing here depends on
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
images. Several features are ports of Lab's work (GPL-3.0-or-later) and are credited
in their sections below.

## Features

Verified on hardware unless marked otherwise.

| Feature | Where | Since |
|---|---|---|
| **Aftertouch sequencing**: record per-note pad pressure in the piano roll, play it back | piano roll, any melodic track | v3.24 (External), v3.40 (internal) |
| **AT Rec** (Off/On) and **AT Out** (Off/Poly/Channel) per track, saved with the project | piano roll Sequence view, encoders 5/6 | v3.33–v3.36 |
| **Live poly aftertouch** out of External tracks (`0xA0`), or channel pressure (`0xD0`) | External tracks | v1 (poly), v3.36 (follows AT Out) |
| **Polyphony cap of 6** per External track, oldest note stolen | External tracks | M1b |
| **Trigger conditions**: `A:B`, `!A:B`, `1ST`, `!1ST`, `PRE`, `!PRE` | step encoders (drum 5, piano roll 7) | v2.4 / v3.3 |
| **Per-step retriggers** (rolls): hits, division, velocity ramp | drum step encoders 6–8 | v3.3 |
| **MIDI retriggers** with their own note-offs (*not yet verified on hardware*) | External tracks | v3.39 |
| **Pad defaults and per-step locks** for velocity / probability / offset | drum step sequencer | v3.4 |
| **Strike velocity**: a tapped-in step takes the hit's velocity | drum step sequencer, "Strike Vel" button | v3.6 |
| **Inspect a step**: hold a step (150 ms) and edit it without deleting it | drum step sequencer | v2.5 / v3.12 |
| **Hold shortcuts** (TRACKS / LAUNCH / MIXER / INST + pads) | everywhere | v2.6 |
| **Black box**: crash and stall records on the SD card | `BENTO_DIAG.log`, `BENTO_CRASHES.log` | v2.1+ |

**Stock compatibility.** Projects saved with these features still open on stock 1.6.1
(checked on hardware). Stock plays recorded-aftertouch notes as plain notes and ignores
`curves.bnt`; it ignores trigger conditions and retriggers, and keeps velocities.

## Using aftertouch sequencing

1. On a melodic track, record in the piano roll as usual and play the pads with
   pressure. Each note keeps its own pressure curve (16 points per step).
2. In the piano roll's **Sequence** view, encoder 5 is **AT Rec** (On: capture pressure
   while recording) and encoder 6 is **AT Out**:
   - **Poly**: each note sends its own pressure (External: `0xA0`; internal instruments:
     stock's per-voice pressure, as when you play the pads);
   - **Channel**: one value per track, the highest pressure among its sounding notes
     (External: `0xD0`, and `0` once the last note ends; internal: every voice gets that
     value);
   - **Off**: no pressure out (it is still recorded if AT Rec is On).
   Live pad pressure follows AT Out too. Defaults: AT Rec On, AT Out Poly. Changing
   either marks the project modified.
3. **Save** the project. Curves go into `curves.bnt` next to `project.xml`, together with
   each track's AT Rec / AT Out.

Internal instruments need a patch that routes pressure to something audible. External
pressure is rate-limited: changed values only, at most every 8 ms per note (more on a
busy port), and at most 400 messages/s per MIDI port shared by all notes and tracks.
At most 64 curve notes play at once across the whole project; more are skipped and
counted in the log (`skipped`).

## Installing

Use the normal bootloader procedure. **This never touches the bootloader**, so
reinstalling stock always works.

1. Build (below) and package. `package_m1.sh` writes `flash/bento_enhanced-v<ver>/` with
   `01_TEST_FIRMWARE/` (this build) and `03_RESTORE_STOCK/` (your stock pair).
2. Copy `bento1.bin` and `bento2.bin` to the SD card root.
3. Hold power to start the bootloader, then press **REC** to install.
4. To go back, install the stock pair the same way.

The kit contains your stock firmware, so keep it private. No 1010music firmware is in
this repository.

## Build and test

You need a POSIX host (WSL Ubuntu 24.04 works) with Lab's pinned toolchain: zig cc,
keystone, Unicorn 2.1.3. See [`tools/lab-build-wsl/`](../../tools/lab-build-wsl/). The
scripts there hard-code this checkout's `/mnt/c/...` path, so edit it for yours.

```
python build.py --base <stock 1.6.1 bento1.bin> --out <new dir>
python test_<suite>.py <stock 1.6.1 bento1.bin> <out dir>     # or test_m1.sh: all suites
```

The suites are `test_diag`, `test_polyat`, `test_conditions`, `test_holdstep`,
`test_controls`, `test_retrig`, `test_step` and `test_mod`: 279 emulated tests (5 skipped in a
default build). They run the patched image's real code under Unicorn, including stock functions,
with hardware, FatFS and a few stock functions stubbed.

The builder checks:
- the base image's hash and the original bytes at every patch site;
- that undoing every write reproduces stock exactly;
- the image size limit (≤ `0x18D334`, the largest the bootloader has accepted);
- that nothing reaches `0x081E0000`.

Tool gotchas it guards against:
- keystone mis-encodes Thumb-2 conditional branches to absolute targets, so they are
  hand-encoded and checked against the original instruction;
- zig cc strips symbols without `-g`;
- lld emits an ELF-header segment below the link origin, which is never written.

**Build switches** (for bisecting on hardware):
- `BE_DISABLE=<group,...>` leaves a group's stock sites unpatched and passes `-DBE_NO_<GROUP>`
  to the compiler. Groups: `step`, `hold`, `cond`, `retrig`, `poly`, `inta` (internal
  aftertouch), `rmidi` (MIDI retriggers), `trace`, `bbox`, `stall`, `irq30`; `diag`
  covers the four diagnostic groups.
- `stall`, `irq30` and `modtest` are off by default; use `BE_ENABLE=` to turn them on.
  `modtest` (v3.41+, External modulation test): LFO 1 → Mod Wheel (Pos Tri, 1/4), Env 1 →
  CC 74, LFO 2 → Pitch Bend (Sine, 1 bar, 15 %) on every External track.
- `BE_LABEL=` sets the boot-screen label (6 characters at most) and `BE_TAG=` the
  version suffix.

## Memory and code placement (v3.41)

- **M7 code**: `0x080DA000–0x080DE000`, inside stock's all-zero gap after the M7 image's
  data. The gap runs to `0x080E0000`, and nothing in the image references it.
- **M4 code** is appended after stock's end: `0x081B5000` black box (16 KB), hold-step
  `0x081B9000`, controls `0x081B9400`, step/curves UI `0x081BA400` (20 KB since v3.41), MIDI-out
  handlers `0x081BF400` (`0x5F` poly, `0x60` channel). The image is `0x17F500` bytes (v3.41).
- **Never touch `0x081E0000`+.** The M7's MPU maps it as a 128 KB read-only data
  region (most likely persistent storage), and the bootloader refused an image reaching it.
- **M7 heap end lowered** from `0x24048DA0` to `0x24048400` (three literals), reserving
  `0x24048400–0x24048DA0` below the M7 stack for our state lines.
- **Reset-surviving shared state** in D2 SRAM `0x3003C000+` (M4 MPU region 2): config,
  capture, curve state, load state, AT settings and the destination table. Every
  structure has a magic word, and its pointers are validated before use (see
  [the boot loop](#the-v325v331-boot-loop)).
- **SDRAM**, bump-allocated once per boot: the M7 state region (12 KB since v3.41), the External
  modulation settings (v3.41, published at `0x3003DFC0`) and the curve pool
  (1 MB, 16384 handles, plus the save list and a 4 KB sidecar buffer).

## Not yet

- Settings UI for the polyphony cap and for pressure/velocity response curves.
- Aftertouch on drum (one-shot) tracks: recording is piano-roll only.
- Re-recording within one project only grows the curve pool. It is cleared on the
  next project load (v3.38).

---

## Poly aftertouch out of External tracks (M1)

Holding pads on an External track sends per-note **Polyphonic Key Pressure**
(`0xA0 note value`) on the track's MIDI port and channel, alongside the normal notes.
It has been verified on a MIDI monitor and on a poly-AT synth, with each held note's
aftertouch driving its own LFO rate.

### How it works

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
| M4 `0x081BD400` | Handler for event type `0x5F`: a copy of stock's Note On case with status `0x90` → `0xA0`. The builder checks the copy against the native bytes. |
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
  budgets) lives in a region (8 KB since v3.25) the M4 allocates once at boot and the M7 owns.
  The pointer is tied to the M4 boot generation, so nothing stale survives a reset.

**Output control.** DIN MIDI carries ~1,000 messages/s, and the M7 → M4 MIDI ring
has only 16 slots with no overflow check. So pressure is sent:
- per note: **changed values only, at most every 10 ms (100 Hz)**. A held-back
  value goes out with the next pressure event for the track;
- per DIN port: at most **400 messages/s**, in bursts of up to 4 (v1 had one global budget).

Measured on hardware: minimum gap 10.6 ms, no repeated values
(`logs/M1_run1_polyat.log`).

---

## Aftertouch sequencing (M2, v3.22–v3.40)

### Capture (M7) and recording (M4)

- **Capture**: the M7 pad input hook (`h_input`) samples each pad's pressure every
  **8 ms** into that pad's capture slot (two per pad, alternating per press), using the
  same curve as velocity. A press longer than 1024 samples (~8 s) halves the resolution
  as often as needed, so any hold fits.
- **Binding**: melodic recording (`FUN_081373EE`) inserts a note at note-on with length 0,
  through stock's insert (`0x0812E070`, caller `0x081377CE`). That note's pitch is bound
  to the oldest held, unbound capture slot (FIFO). The record carries no pad number, so
  press order is the link.
- **Attach**: at note-off stock sets the length (`0x0812E0CE`, caller `0x081378F8`). The
  capture is resampled linearly to **16 points per step** (sample k at the note's
  start + 15k playback ticks), stored in the curve pool, and its handle goes into the
  note's word. With AT Rec Off for the track, nothing is attached.
- **Note word** (`+20`, tag `0xB7`): condition [23:17], curve handle [16:3] (14 bits),
  locks [2:0]. A note without a curve keeps the `0xB6` step word or 0.

### Save and load: `curves.bnt`

Stock's `project.xml` stays stock-readable. The curves live in a sidecar next to it:

```
"BNTC"  u16 version = 1  u16 count
count x { u16 samples, u16 bytes, encoded bytes }
  0x00-0x7F  a literal value
  0x80-0xBF  repeat the previous value ((b & 0x3F) + 1) times
  0xC0-0xFF  two deltas: ((b >> 3) & 7) - 4, then (b & 7) - 4
optional "TRKS"  u16 n  rec_off[n]  out_mode[n]     (out_mode 0 Poly, 1 Off, 2 Channel)
```

- A curve note's XML word (`be_step_v1`) carries its **1-based sidecar index**, not the
  pool handle.
- **Save**: hooked after stock's own `project.xml` write (`0x0813044C`). The sidecar is
  written right then, from an SDRAM buffer: the SD card's DMA can't reach D2 SRAM or the
  M4 stack (v3.27 crashed on exactly that). Our code never touches the card at any other
  time. FatFS isn't reentrant, and the card access of builds up to v3.20 caused crashes.
- **Load**: the document reader hook (`0x08130A18`) remembers the path. At the first note
  of a `project.xml` load the pool starts over (v3.38), the sidecar is decoded, index i
  maps to handle base + i − 1, and `TRKS` restores AT Rec / AT Out. Nothing touches the
  track model during a load (v3.30 did and looped on autoload). The AT destination table
  is published from the next UI tick instead.

### Playback

- **Start**: the retrigger accept hook (after stock's `note_route`) starts a player for a
  curve note: 64 players, shared across all tracks. **Emit**: once per track per audio
  block, each player catches up to the samples now due, using stock's tick → sample
  conversion (`0x080A2200`), so tempo changes are followed. Only the latest value per
  block is kept.
- **AT Out lookup**: the M4 publishes per track its MIDI port, channel parameter and AT
  Out mode (`0x3003DF80`).
  - External tracks match on port and channel. The M4's channel is **1-based** (M7
    byte + 1), and 0 marks a non-External track (v3.33 matched exactly first and hit
    the wrong track).
  - Internal tracks use their track index, `track+0x86D4`, confirmed on hardware against
    the pad router's index.
- **External, Poly**: an oldest-first send pass across all players. A note is eligible
  once its value changed and its share of the port has elapsed: max(8 ms, notes on the
  port × 2.5 ms). Each port has a token bucket of 400/s. The M7 posts event `0x5F`;
  the M4 handler (jump-table slot `0x5F`, a copy of stock's Note On case) sends `0xA0`.
- **External, Channel**: one aggregate per port/channel, the highest current value of
  its sounding notes, live and recorded together. It sends `0` once none sound. Event
  `0x60` → M4 handler (slot `0x60`) → `0xD0` with one data byte.
- **Internal instruments**: stock's voice engine (`FUN_080945E0`, case `0x0E`) already
  sets each matching voice's pressure to value / 127 for the pad router's live events.
  Playback posts the same event `{0x0E, 0, key, note, value}` through the engine bus
  (`0x0809ECA0`), at most every 4 ms per note. The instrument key follows `note_route`'s
  rule: the per-pad table `track+0x86DC[pad]` unless `0xFFFFFF00`, otherwise
  `track+0x10` (v3.37–v3.39 used `+0x10` alone and posted to key 0). Channel mode
  posts the aggregate to every sounding note.
- **Live pressure and AT Out**:
  - External tracks: the External handler hook (`h_poly`) applies the mode.
  - Internal tracks: a hook at the pad router's post (`0x08085B6C`) applies it.
  - Drum tracks (per-pad keys) and External targets pass through that hook untouched.
  - Live internal Channel entries expire 300 ms after their last update.

### Settings UI

- **Encoders**: AT Rec and AT Out are registry lists in parameter keys claimed per boot
  (6 keys from the top, magic `STP2`). They are bound to slots 5/6 of the piano roll's
  Sequence view header (view flag `+0x125DAC`).
- **Save button**: a change sets the app's modified byte (`+0x36C4`) and calls stock's
  post-edit refresh (`0x0813130C`), as stock's own edits do, so Save lights up.

## MIDI retriggers (v3.39, not yet verified on hardware)

Until v3.39, retrigger hits went to the internal sound only (MIDI out was masked while
a hit was posted). Each hit on a MIDI-out track now also goes straight to the MIDI
queue. It deliberately bypasses the polyphony cap's same-note rule, which would owe a
native Note Off per hit that never comes, leaving the last note hanging.
- Original note still sounding: Note Off + Note On, re-articulating the voice. Stock's
  own Note Off still ends the last one.
- Original already released (short gate): Note On, then our own Note Off half a division
  later.
- An open hit is closed at once if the burst is cut off by a new note on the pad or
  dropped as late (transport stopped).

`BE_DISABLE=rmidi` restores internal-only hits.

## The v3.25–v3.31 boot loop

After running stock firmware once, every build with the black box looped at boot, even
builds that had run for hours. Re-flashing, disabling autoload and unplugging USB didn't
help, while stock booted. Bisect builds settled it: all groups off (B0) and step only (B1)
booted, and every build with the black box looped.

**Cause.** At boot, `init()` printed the previous boot's counters *before* re-initialising
its config line. Since v3.25, `counters()` followed `CFG->region`, a pointer in
reset-surviving D2 RAM, checking only that it was non-zero. After a cold start D2 holds
its power-up pattern, so the M4 faulted there, and every warm reset kept the same
garbage. **Fix (v3.32)**: the pointer is used only if the line's magic is ours and it is
an aligned SDRAM address. The sidecar writer also requires this boot's generation. The
rule now applied everywhere: **no pointer from reset-surviving RAM is followed without
a magic word and a range check.** A test boots with junk in that RAM.

---

# History of earlier features

Sections in the order they were added; later sections can supersede details in earlier ones.

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

## Inspect a step (since v2.5; reworked in v2.7; 400 ms hold since v3.11)

Stock already selects a held step (pad or touch): while you hold it, the encoders show
and edit that step. But releasing a step held for under a second toggles it, so a quick
hold-and-edit also deleted it. **Now any encoder edit while holding keeps the step on
release**, and since v3.12 a hold of **150 ms** (stock: 1000; v3.11: 400) counts as a hold, not a tap
(`cmp.w r3, #0x3E8` → `#400` at `0x0815D284` pad release and `0x0815D484` touch release). After release, the encoders show the defaults for new steps again, as on
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
- *Superseded: settings come from each note since v3.3, and MIDI hits are sent since
  v3.39 ([MIDI retriggers](#midi-retriggers-v339-not-yet-verified-on-hardware)).*
- **Prototype scope (v3.2):** fixed settings on **pad 16** of every track (3 hits, 1/16T,
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

## Strike velocity (v3.6; header button since v3.9)

Drum step sequencer (step and sequence views): **a step you tap in takes the velocity
of the hit** (locked if it differs from the pad default). Touchscreen taps get the
default. **On/off: the "Strike Vel" button in the bottom bar between Sequence and Undo**,
outlined while on (default on; kept across warm reboots, not power-off).

- M7: `h_input` keeps a **strike per pad** (since v3.12) in an M7-written line
  `0x24048D60`: a press counter and the **peak** of stock's velocity formula
  (`min(raw, 0xD000) × 127 / 0xD000`) over the press (`0xAC`) and its first 8 pressure
  samples (`0xAE`), so a quick hit isn't read at first contact. Pads hit together each
  keep their own strike. Peak countdowns live in the stall recorder's spare M7 bytes.
- M4: the drum step pad-press handler (`0x0815D1EC`) takes a fresh strike as pending;
  stock's toggle (`0x0815D114`) is wrapped and, when it creates a step, gives the new
  note that velocity (each pad its own; the UI's per-pad state is bump-allocated once per
  boot). A strike is used once and only within 3 s.
- Notes created by **any** view go through stock's insert (`0x0812E070`), hooked so the
  drum **Sequence** page (drawn by the piano-roll view object, with its own step entry)
  gets strike velocity too: a strike < 500 ms old, not already used, on a drum track,
  locked (v3.11). Bulk inserts (Double Seq, Paste) are never touched.
- Button: after stock generates the header description (`0x08134800`, six 48-byte
  action records at `+0x74`), our record goes into the rightmost free normal slot (left of
  Undo) with `+0x21` lit while on; the header root's layout flag (`0x30034D48+0x117C`) is
  re-armed when our record differs from the applied header, because stock only applies a
  slot's visibility at page change. Its action id `0x10F0` is caught at the drum view's
  event handler (`0x0815D600`) in Step view, and at the piano-roll view object's handler
  (`0x0815A598`) in Sequence view (stock draws the drum Sequence tab with the piano view).
  Shown on page `0x27` whenever the selected track is a one-shot (drum) instrument (type
  `0x0E`), never for pitched tracks (v3.9 used a view flag that the Sequence tab cleared).
  The black box status line shows `strike btn calls/drum/slot/ins/off`. Method and helpers from Lab's
  `header_actions.c` (their Select button).
- v3.6/v3.7 used a hamburger-menu item instead; it couldn't show its state reliably and
  was removed in v3.8.

## Diagnostics history (v2.1–v2.2)

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
