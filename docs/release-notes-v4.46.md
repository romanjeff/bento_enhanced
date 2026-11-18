# bento_enhanced v4.46 (stock 1.6.8): FM amounts in the Mod Matrix, FM Env 1 / 2 as sources

BE4.46 lets the Mod Matrix move the FM amounts, gives the piano roll lanes on them, and adds each oscillator's FM envelope
as a modulation source. It builds on BE4.44 (FM on Wavetable and Analog oscillators) and combines two lanes that were
built and checked apart:

- **FM amounts as destinations, and lanes on them** (built as BE4.45; its kit was never released). Plan and
  measurements: `docs/plan-fm-targets.md` (X1, X2, X3 and their reviews).
- **FM Env 1 / FM Env 2 as sources** (built as a separate 4.46 kit, `-envsrc`). Plan and measurements:
  `docs/feasibility-fm-env-sources.md` (S1, S2 and their reviews).

Device checklist: `docs/checklist-v4.46.txt`. Everything below was measured in the emulator; nothing in this release has
been run on a device yet.

## What it does

### FM amounts as Mod Matrix destinations

- **Three new rows** on a Wavetable track's Mod Matrix page:
  - **OSC1: Sub FM**, after OSC1: Position;
  - **OSC2: FM 1>2** and **OSC2: Sub FM**, after OSC2: Position.

  They work on Analog and on Wavetable oscillators, from any source of the list.
- **A routing moves the knob's travel.** The depth played is `knob + amount x source`, in knob units, held to 0..100 %,
  then the usual depth curve. Several routings on one row add up. With the knob at 0, a routing alone turns FM on.
- **OSC2: Sub FM plays only routed.** It has had no knob since BE4.43. Routed, the sub modulates OSC2 next to FM 1>2.
- **The FM envelope still applies.** FM Atk / FM Dec shape the routed depth as they shape the knob's.
- **Smooth.** The depth follows its source once per audio block (2.7 ms) and ramps across each block. An LFO above
  about 100 Hz on a depth aliases; the audio-rate FM sources are for that.
- **The oscillator rows read "OSC1: ..." / "OSC2: ...".** Stock and BE4.44 showed "WT1: ..." / "WT2: ..." on this page.
- **Piano-roll lanes.** The lanes' destination list offers OSC1: Sub FM, OSC2: FM 1>2 and OSC2: Sub FM, each on its own
  oscillator. A lane is saved and played as a routing from seq1..seq4, like any lane.
- **Black box:** the ANALOG line in BENTO_SAVES.log has `x2lost=`, the FM routings the M7 could not keep (more routed
  Wavetable roots than it has room for: 8). It should read 0.

### FM Env 1 / FM Env 2 as sources

- **Two more sources, right after Envelope 2:** FM Env 1 (OSC1's FM envelope, its FM Atk / FM Dec) and FM Env 2 (OSC2's).
  The Source list is now: [None], Velocity, Pressure, Envelope 1, Envelope 2, **FM Env 1, FM Env 2**, LFO 1, LFO 2,
  Mod Seq, Key, Mod Wheel, Macro X, Macro Y, Auto A..D.
- **Per voice, one-shot, restarted at every note**, like two extra envelopes, on Analog and Wavetable oscillators.
- **It runs without any FM.** The envelope runs whenever FM Atk / FM Dec is set, at any FM amount.
- **The value is the FM envelope's own** (0..1), at the start of each audio block. An unset envelope (FM Atk 0, FM Dec
  Inf) gives a constant 1, as the FM depth does. A voice's first FM Env routing, made mid-note, reads 1 (the unset value)
  for one block, not 0 (kept on purpose).
- **It moves once per audio block (2.7 ms)**, as stock's envelopes do; a Pitch routing from FM Env steps at that rate
  too.
- Granular and the other track types keep stock's source list.

### The two together

- **FM Env 1 / 2 routed to an FM row** move that FM amount with the FM envelope's value of the same block: offline, bit
  for bit the knob turned to `knob + amount x FM Env` every block. The FM envelope then acts twice (it moves the depth
  and still shapes it).

With nothing routed to the new rows and no FM Env routing, every track sounds exactly as on BE4.44 (offline: output and
voice state byte for byte).

## Projects and compatibility

- **Projects that use the mod's features are not supported on stock firmware: they usually open, but may not play
  correctly.** In detail:
- An FM-row routing is saved as stock saves any routing, with the destination `bento_subfm` / `bento_fm12`.
  - BE4.44 loads it and keeps it (it does nothing there).
  - Stock 1.6.8 loads it as a routing to nothing (inert) and **drops it when it saves**: a project saved on stock comes
    back to BE4.46 without it.
- An FM Env routing is a stock routing: `src="env3"` (FM Env 1) or `src="lfo3"` (FM Env 2), stock's Envelope 3 / LFO 3
  ids, which a Wavetable voice does not have on stock.
  - Stock 1.6.8 and BE4.44 open it and keep it, but play nothing from it. Their page shows the slot as [None] (or
    "Envelope 3" / "LFO 3" after a one-row refresh). Saving there keeps it.
  - **Editing that slot there removes it** (stock's page sends it back with source None on an amount edit). Back on
    BE4.46, an untouched routing works again.
- BE4.46 opens every BE4.44 project unchanged.
- Changing a track's instrument type starts a new matrix (stock's instrument replace makes a new track), so an FM Env
  routing never turns into a real Envelope 3 / LFO 3 routing of another engine.

## Known limits

- **CPU, OSC2: Sub FM routed** adds a whole second FM term to OSC2. On the heaviest voice (both oscillators Wavetable, full
  FM, Position moving every block) that is 37.3k instructions a voice-block from velocity or a lane, 37.35k from FM Env 2,
  37.42k from FM Env 2 with both FM envelopes running, and at worst 37.57k (every matrix slot used, routings on 8
  tracks, envelopes running): 5.6 % to 6.4 % over BE4.44's heaviest voice (35,309). A lane on OSC2: Sub FM costs the same.
  **Accepted as a known limit** (Jeff, 2026-11-17): it only matters with all 8 voices of the heaviest Wavetable voice
  sounding, where voice stealing and glitches are normal anyway. Checklist section 8 still measures it on the device.
- **CPU, the other two rows** stay within the ceiling on one routed track (35.15k from velocity, 35.21k from FM Env).
  In the worst layout (every matrix slot used, routings on 8 tracks) with the FM envelopes running they are up to 83
  instructions (0.24 %) over it: 35.39k from FM Env, 35.32k from velocity (so not from FM Env alone). Same
  8-voice-only case as above; recorded, not ruled on separately.
- seq1..seq4 routed to the same FM row share one slot, as on every stock destination: the last one wins. Removing one of
  two such routings silences the other until the project is reloaded (stock's rule too).
- A lane whose routing is edited on the Mod Matrix page to another source keeps playing next to it, even after that
  routing is removed, until the project is reloaded. Stock does the same on its own destinations.
- A Wavetable OSC2's Sub FM routing starts from 0 even if an old project stored a value for it. Accepted (Jeff,
  2026-11-17): no control on the page sets that value.
- The FM-row routings are kept in the M7's own memory, set up when it starts. A project loaded before that point would
  play them as unrouted until it is loaded again (not seen offline; the checklist has a cold-boot check).
- FM Env routed to an Envelope or LFO parameter acts one block late (stock has the same lag between its own sources).
- Source names: offline, only the Mod Matrix page's own name call was found; any other screen that names a source through
  another path may show "Envelope 3" / "LFO 3" (checklist section 2 asks).

## Measured (emulator)

- Nothing routed: BE4.44's output, voice state and FM stats byte for byte (X2Identity, 8 cases, all type pairs);
  P3Identity (against BE4.40, 48 cases) worst 1.0179 (BE4.45: 1.0177), under its 1.95 % stop line.
- FM rows: routed = the knob turned to the same depth, bit for bit, including the clamps, several routings summed, root
  sources, mixed sync, several voices each with its own source, the ramp out on removal, and lanes.
- FM Env: the published value is the FM envelope's own e0 (against the reference model, block by block), and routed to
  an FM row the depth of each block is that block's value.
- CPU, instructions per voice-block (an_enter and both oscillator renders), steady median:

| Voice | Instructions |
|---|---|
| Heaviest existing voice, knobs (Wavetable both, Sub FM / FM 1>2 1000, Position every block, note 24) | 34,892 |
| the same depths routed from velocity (one track) | 35,151 |
| the same depths from two lanes, 7 laned tracks ahead | 35,193 |
| every matrix slot used, routings on 8 tracks, from velocity | 35,261 |
| from FM Env 1 / 2 (envelopes unset: the same sound) | 35,212 |
| from FM Env 1 / 2, both FM envelopes running (FM Atk 3, Dec Inf) | 35,282 (one level-change block 44,555) |
| every slot, 8 tracks, FM Env in slot 0 (unset / envelopes running) | 35,322 / **35,392** (over: see Known limits) |
| every slot, 8 tracks, velocity in slot 0, envelopes running | 35,319 (over) |
| + OSC2: Sub FM routed from velocity or a lane | 37,291 (over: accepted) |
| + OSC2: Sub FM from FM Env 2 (envelopes unset / running) | 37,352 / 37,422, one block 44,633 (over: accepted) |
| + OSC2: Sub FM, every slot, 8 tracks, envelopes running | 37,566 (over: accepted) |
| BE4.44's worst chord voice (note 24, light FM, Position every block), no FM Env routing | 34,809 (heaviest block 39,441) |
| the same with an FM Env routing | 34,870 (heaviest block 39,502) |

  The ceiling is 35,309 (BE4.44's heaviest voice); one level-change block per note may reach 46,000. Without any FM Env
  routing the cost is BE4.45's + 2..3 a voice-block.
- Sizes: DSP code 33,068 B, 7,572 B free of its window; synth UI 16,970 B; image 0x1898B4, 14,976 B under the size proven
  on the device. The DSP trampoline block grew from 0x100 to 0x140 B (the two lanes' trampolines need 0x102 B).
