# External track modulation: design (draft for review)

Status: **design, not built.** Decisions below; F1 (engine) next. It comes out of the F0 research sprint (2026-10-27) and
the planning decisions recorded below. Addresses are stock 1.6.1.

## Goal

External (MIDI) tracks get what internal instruments have, sent as MIDI:

- **LFO 1–2 and Envelope 1–2** per External track;
- a **mod matrix**: MIDI destinations, each with up to 3 sources and amounts;
- later: **CC pairs** on the INST page (a target and a value, sent at sequencer start and
  on change), and **automation lanes A–D with MIDI targets**.

### Decisions so far

- **Precedence**: a CC pair's value is the base. An automation lane replaces it while
  the lane has data. LFO/envelope modulation is added on top, clamped to the range.
- **Retrigger** (LFO restart, envelope start) happens on **every note-on** of the track,
  even with other notes held.
- **Must-haves first**: LFOs, envelopes and the mod matrix. CC pairs and automation lanes
  come after.
- **Resolution**: 7-bit is fine for pitch bend, channel pressure, mod wheel and breath.
  Program change is a nice-to-have for CC pairs.

## What stock already has (and how it's built)

| Piece | Stock implementation | Reuse for External |
|---|---|---|
| Parameter registry | 499 keys, all registered in `lab_param_registry_construct` (`0x081639A8`); 354 used | ~138 free keys (we use 6); this feature needs ~20 |
| LFO / envelope parameters | **one key set for every instance**: env Attack `0x1E`, Decay `0x1F`, Sustain `0x20`, Release `0x21`; LFO Shape `0x4A` (9 shapes), Rate `0x4B`, Depth `0x4C`, Restart `0x4D`, Beat Sync `0x4E`, synced Rate `0x4F` | the same keys, labels and lists: the External view supplies values per instance |
| INST dashboard rows | per-type tables of 8 keys per row (`0x0819D610…`); the row generator `FUN_08134B58` returns 3 extra rows for internal types and none for External | External's view (`0x0819DB08`) has **one** encoder bar (`view+0x3D0`, filled by `FUN_08147138` from `0x0819DB40`). It has no row support |
| Mod Matrix page | view vtable `0x0819DE20…`; event handler `FUN_0814C690`; row builder `FUN_0814CC14` (per instrument type: dest keys, source list, count, 16-byte row names); routing access `FUN_08131244(model, ctx, dest key, slot, &routing)` | hook the row builder and routing access for External tracks: rows are MIDI destinations, routing data is ours |
| Matrix encoders | Line `0x16D`, Source 1–3 `0x171/173/175` (0–34), Amount 1–3 `0x172/174/176` (±1000) | unchanged |
| MIDI out (M4 task) | event `0x5D` pitch bend, `0x5E` CC, plus our `0x5F` poly AT and `0x60` channel pressure | no new M4 handlers expected (program change: maybe one) |

## Design

### UI

1. **External INST page: row sets on the one bar** (approach "c"). The existing bar
   shows one 8-encoder row at a time: **Main** (stock: Level, Pan, MIDI…), **LFO 1**,
   **LFO 2**, **Env 1**, **Env 2**, and later **CC pairs**. A header button (as Strike Vel
   was added, v3.9) or the arrow buttons step through them, and the row name shows in the
   header. We fill the slots and handle their encoder events ourselves, the technique
   proven by the AT settings (v3.33). Stacked rows like internal dashboards would mean
   building new widgets: possible later if this feels clumsy.
   - LFO row: Shape, Rate (free) or Sync Rate, Beat Sync, Depth, Restart (+ spare).
   - Env row: Attack, Decay, Sustain, Release, Loop (Off / 1–32 / ∞, attack–decay cycles) (+ spare).
2. **Modulation page**: stock's Mod Matrix page, opened from a header button on the
   External INST page (External has no hamburger menu). Its own "Dashboard" button
   returns. For an External track, the hooked row builder supplies:
   - **rows** = 16 destination slots (the view's arrays hold up to ~59). Each row shows its
     chosen destination's name ("Mod Wheel", "Cutoff (74)", "CC 85"). Keys are private
     numbers, so **no registry keys are used**. Encoder 8 ("Dest") picks the destination;
   - **sources** = the ones our engine provides (LFO 1–2, Env 1–2, Velocity, Pressure, …);
   - the **CC column** shows each row's CC number.
   Routing reads and writes for an External context go to our storage.

### Engine (M7)

- Per External track: 2 LFOs and 2 envelopes, evaluated once per audio block (~2 ms).
- **Clock**: tempo-synced rates come from the sequencer's playback ticks, the same
  conversion the curve players use. Free rates come from the cycle counter.
- **Events**: retrigger on every note-on (`h_note` already sees them). Sequencer start
  sends the CC-pair values (later sprint).
- **Output** per destination: base + Σ(source × amount), clamped. **Only changed values
  are sent**, at most every 8 ms per destination, from the port's token bucket, **shared
  with aftertouch and with notes always first**. The M7→M4 MIDI ring has 16 slots and
  no overflow check: a modulation burst must never displace a Note Off.

### Storage and saving

- Settings live in our reset-surviving D2 state with a magic word; the destination
  table is published to the M7 as for AT Out.
- They are saved in the project's `curves.bnt` as a new chunk (as `TRKS` was added),
  so `project.xml` stays stock-readable and stock ignores it.

## Sprints

| Sprint | Content | Device test |
|---|---|---|
| F0 | research (this document) | — |
| F1 | M7 engine: LFO 1–2, Env 1–2, fixed test routing (e.g. LFO 1 → Mod Wheel), budget, retrigger | hear/see a CC sweep that follows tempo |
| F2a | INST row sets (LFO / Env editing on the External bar), saving | edit and save LFO/env settings |
| F2b | Modulation page: menu button, row builder + routing hooks, source list | full routing from the matrix |
| F3 | CC pairs (+ program change), sent at start and on change; base for modulation | |
| F4 | Automation lanes A–D → MIDI targets | |

## Risks

- **Navigating to the Mod Matrix page from a page without a menu**: we have only ever added
  header buttons and menu items; switching pages programmatically still needs tracing.
- **The Matrix view assumes an internal track** in code paths not yet traced (row 0 CC
  column, macros, the "Filter %s" naming). Every hook must be limited to External contexts.
- **MIDI bandwidth**: DIN carries ~1,000 messages/s. 8 tracks × several modulated
  destinations at 125 Hz would exceed it, so the budget will thin modulation first,
  and a busy project will update modulation more coarsely.
- **Ring overflow** (16 slots, no check): modulation must stay within the existing
  per-port budget, and notes keep priority.

## Decisions (Jeff, 2026-10-27)

1. **16 destination rows.** Each row picks its destination from one list: named messages
   first (**Pitch Bend, Channel Pressure, Mod Wheel, Breath, Volume, Pan**, then other
   standard-named CCs such as Expression, Foot, Cutoff 74, Resonance 71, Attack 73,
   Release 72), then **CC 0–127 by number**, still showing a standard name where one
   exists. The matrix page uses 7 encoders (Line, Source 1–3, Amount 1–3), so **encoder
   8 becomes "Dest"** for the selected row, and the rightmost column shows the choice.
2. **Sources**: LFO 1–2, Env 1–2, Velocity, Pressure (highest held note), Key. **No incoming
   MIDI.**
3. **Row selection on the INST page**: native pages show several rows and **touch selects
   the row** that the encoders edit. F2a first checks whether the External view can show
   stacked rows with touch selection (new bar widgets). Fallback: one bar with a header
   button that switches row sets. Jeff hears about it before it is built.
4. **LFO shapes**: stock's nine.
5. **Envelope loop**: attack–decay cycling (sustain/release are not looped).

## Planned: Note Mod (scale-quantized note modulation, all tracks)

Decisions (Jeff, 2026-10-28):

- **When**: the offset is sampled when a sequenced note starts and kept for the whole
  note (its Note Off goes out for the transposed pitch). Held notes are never re-pitched.
- **Which notes**: sequenced notes only; live pads play what is pressed.
- **Rolls**: every hit of a roll repeats the first hit's transposed pitch; the next real
  sequenced note samples again.
- **Where**: its own small **Note Mod** section per track, on External *and* internal
  tracks (independent of stock's synth LFOs, which run per voice and can't be read).
  Settings: source shape (stock's nine, incl. Random = new value each cycle), rate or
  synced rate, Triggered, amount, and **Range ±1–4 octaves**.
- **Quantizing**: offsets count **scale steps** of the project's scale and root; a note
  outside the scale snaps to the nearest scale note first.

Engineering notes:
- Hook point: the sequencer's note path (`note_route`, before stock posts the MIDI
  0x5B and internal 0x12 events), so both track kinds are covered in one place.
- A per-note map (original -> sent pitch) per track: Note Offs, the poly cap's voice
  list, retrigger bursts and aftertouch curve players must all use the sent pitch.
- Open research: where the project's scale and root live at runtime
  ([`project-settings.md`](project-settings.md) has the stored settings).
