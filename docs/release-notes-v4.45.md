# bento_enhanced v4.45 (stock 1.6.8): FM amounts in the Mod Matrix

BE4.45 lets the Mod Matrix move the FM amounts. It builds on BE4.44 (FM on Wavetable and Analog oscillators). Plan and
measurements: `docs/plan-fm-targets.md`. Device checklist: `docs/checklist-v4.45.txt`.

## What it does

- **Three new Mod Matrix destinations** on a Wavetable track:
  - **OSC1: Sub FM**, after OSC1: Position;
  - **OSC2: FM 1>2** and **OSC2: Sub FM**, after OSC2: Position.

  They work on Analog and on Wavetable oscillators, from any source of the list (velocity, pressure, envelopes, LFOs,
  the seq lanes, modwheel, macros).
- **A routing moves the knob's travel.** The depth played is `knob + amount x source`, in knob units, held to 0..100 %,
  then the usual depth curve. Several routings on one destination add up. With the knob at 0, a routing alone turns FM
  on. With nothing routed, every track sounds exactly as on BE4.44.
- **OSC2: Sub FM plays only routed.** It has had no knob since BE4.43. Routed, the sub modulates OSC2 alongside FM 1>2.
- **The FM envelope still applies.** FM Atk / FM Dec shape the routed depth as they shape the knob's.
- **Smooth.** The depth follows its source once per audio block (2.7 ms) and ramps across each block, so slow LFOs,
  envelopes, pressure and velocity sound continuous. An LFO above about 100 Hz on a depth aliases; that is what the
  audio-rate FM sources are for.
- **The oscillator rows read "OSC1: ..." / "OSC2: ...".** Stock and BE4.44 showed "WT1: ..." / "WT2: ..." on this page.
- **Black box:** the ANALOG line in BENTO_SAVES.log has `x2lost=`, the routings the M7 could not keep (more routed
  Wavetable roots than it has room for: 8). It should read 0.

## Compatibility

- Projects save these routings as stock saves any routing. BE4.44 loads them and keeps them (they do nothing there).
  Stock 1.6.8 loads them as routings to nothing (inert) and **drops them when it saves**: a project saved on stock comes
  back to BE4.45 without them.
- Nothing else in the save format changed.
- The piano roll's destination list (X3, in this image): the lanes also offer OSC1: Sub FM, OSC2: FM 1>2 and OSC2: Sub FM,
  each on its own oscillator. A lane is saved as a routing from seq1..seq4, like any lane; every other list is stock's.

## Known limits

- seq1..seq4 routed to the same FM destination share one slot, as on every stock destination: the last one wins.
  Removing one of two such routings silences the other until the project is reloaded (stock's rule too).
- A piano-roll lane whose routing is edited on the Mod Matrix page to another source keeps playing next to it, even
  after that routing is removed, until the project is reloaded. Stock does the same on its own destinations
  (measured offline on OSC1: Position).
- A Wavetable OSC2's Sub FM routing starts from 0 even if an old project stored a value for it.
- **CPU:** routing OSC2: Sub FM adds a whole second FM term to OSC2. On the heaviest voice (both oscillators Wavetable,
  full FM, Position moving every block) that is about 37.3k instructions a voice-block, about 5.6 % over the heaviest
  voice of BE4.44 (35.3k). The other two destinations stay within it. A piano-roll lane on OSC2: Sub FM is the same
  routing, so it costs the same (not yet ruled on: see the plan's X3 open items).
- The routings are kept in the M7's own memory, set up when it starts. A project loaded before that point would play
  them as unrouted until it is loaded again (not seen offline; the device checklist has a cold-boot check).

## Measured (emulator)

- Nothing routed: BE4.44's output and voice state byte for byte (8 cases, all type pairs).
- Routed: bit for bit the same as turning the knob to the same depth, including the clamps, several routings summed,
  root sources (modwheel, seq lanes), mixed sync, several voices of one track each with its own source, and the ramp out
  on removal.
- CPU: about 260 instructions per voice-block for two routed destinations on the heaviest voice (35.26k, within its
  35.31k ceiling, also with every slot used and the store full); with nothing routed, unchanged within about 0.2 %.
- Piano-roll lanes: a lane is the same routing, from seq1..seq4, bit for bit (the same depth per block as velocity
  routed there, and as the knob turned to that depth). It survives the M7's clear of a track's routings as a lane on a
  stock destination does.
- DSP code 32,868 B (7,836 B free); 15,184 B of M4 room left to the proven image maximum.
