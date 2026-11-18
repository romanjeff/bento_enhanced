# bento_enhanced v4.45 (stock 1.6.8): FM amounts in the Mod Matrix

BE4.45 lets the Mod Matrix move the FM amounts. It builds on BE4.44 (FM on Wavetable and Analog oscillators). Plan and
measurements: `docs/plan-fm-targets.md`. Device checklist: `docs/checklist-v4.45.txt`.

## What it does

- **Three new Mod Matrix destinations** on a Wavetable track:
  - **OSC1: Sub FM**, after OSC1: Position;
  - **OSC2: FM 1>2** and **OSC2: Sub FM**, after OSC2: Position.

  They work on Analog and on Wavetable oscillators.
- **A routing moves the knob's travel.** The depth played is `knob + amount x source`, in knob units, held to 0..100 %,
  then the usual depth curve. With the knob at 0, a routing alone turns FM on. With nothing routed, every track sounds
  exactly as on BE4.44.
- **OSC2: Sub FM plays only routed.** It has had no knob since BE4.43. Routed, the sub modulates OSC2 alongside FM 1>2.
- **Smooth.** The depth follows its source once per audio block (2.7 ms) and ramps across each block, so slow LFOs,
  envelopes and velocity sound continuous. An LFO above about 100 Hz on a depth aliases; that is what the audio-rate FM
  sources are for.
- **The oscillator rows read "OSC1: ..." / "OSC2: ...".** Stock and BE4.44 showed "WT1: ..." / "WT2: ..." on this page.

## Compatibility

- Projects save these routings as stock saves any routing. BE4.44 loads them and keeps them (they do nothing there).
  Stock 1.6.8 loads them as routings to nothing (inert) and drops their destination when it saves.
- Nothing else in the save format changed.
- The piano roll's destination list is stock's (no lanes on these destinations).

## Known limits

- seq1..seq4 routed to the same FM destination share one slot, as on every stock destination: the last one wins.
- A Wavetable OSC2's Sub FM routing starts from 0 even if an old project stored a value for it.

## Measured (emulator)

- Nothing routed: BE4.44's output and voice state byte for byte (8 cases, all type pairs).
- Routed: bit for bit the same as turning the knob to the same depth, including the clamps and the ramp out on removal.
- CPU: about 400 instructions per voice-block for the routings themselves; with OSC2: Sub FM routed (a second FM term)
  the heaviest voice is 32.2k, within the heaviest existing voice (35.3k).
- DSP code 32,652 B (8,052 B free); the M4 room is unchanged (15,432 B to the proven maximum).
