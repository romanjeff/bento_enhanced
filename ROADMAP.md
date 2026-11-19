# Roadmap

Status as of 2026-11-18, **release [v4.47](https://github.com/romanjeff/bento_enhanced/releases/tag/v4.47)**.
**Target firmware: stock Bento 1.6.8** (since 2026-11-07; up to v3.40 the builds sat on stock 1.6.1).
Our builds patch your own copy of the official firmware; nothing here ever touches the bootloader.
Several features are ports of
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s work.
Build it yourself with [`BUILDING.md`](https://github.com/romanjeff/bento_enhanced/blob/v4.47/BUILDING.md); details of
everything below are in [`mods/m1/`](https://github.com/romanjeff/bento_enhanced/tree/v4.47/mods/m1).

## Done (on hardware)

- **Port to stock 1.6.8** (BE4.0x-4.1x, November 2026): every v3.x feature rebuilt on the new official firmware.
- **Synth engine: feature complete in v4.46** (Analog / Wavetable / FM).
  - An **Analog** (virtual-analog) oscillator type on Wavetable tracks.
  - **FM** on Analog and Wavetable oscillators: **Sub FM** (the sub oscillator modulates its oscillator) and **FM 1>2**
    (OSC1 modulates OSC2). Linear, through-zero FM: a patch keeps its timbre across the keyboard.
  - A per-oscillator **FM envelope** (FM Atk / FM Dec) on the FM amount.
  - The FM amounts as **Mod Matrix destinations** (OSC1 Sub FM, OSC2 FM 1>2, OSC2 Sub FM), with **piano-roll automation
    lanes** on them, so pressure, LFOs, envelopes, velocity or a lane can drive FM.
  - **FM Env 1 / FM Env 2** as Mod Matrix sources.
- **Aftertouch:** live poly aftertouch out of External tracks (polyphony cap 6), and **aftertouch sequencing**: per-note
  pressure recorded in the piano roll and played back on External tracks (poly or channel pressure) and on internal
  instruments. **AT Rec** and **AT Out** (Off / Poly / Channel) per track.
- **Sequencer:** trigger conditions; per-step retriggers (hits, division, ramp); pad defaults with per-step locks; strike
  velocity; hold-to-inspect steps.
- **UI:** hold shortcuts (TRACKS / LAUNCH / MIXER / INST).
- **Diagnostics:** black box on the SD card (crash records, audio stall records). **Clean start** on every boot (v4.47):
  nothing of ours is carried over in RAM from an earlier run, which fixed BE4.46's boot loop.
- **Build it yourself:** one command builds a kit from your own stock 1.6.8 and checks it is byte for byte the image
  tested on hardware.

## Now: per-track compressor and clipper

A **compressor** and a **clipper** (with Drive) on each of the 8 tracks, in the mixer: a button in each strip's free
spot between the reverb send and solo/mute, lit when active, opening that track's settings (fallback: a second item in
the hamburger menu next to stock's Compressor). Our own lean DSP code, saved per track with the project. The clipper's
code is shared with the drums' soft limiter. A read-only feasibility study comes first (hook point, CPU for 8 tracks,
room left for the drums), then about 3-4 sprints.

## Next: Drum Synth track

A clean, separate **Drum Synth** track in the instrument menu: 16 pads, each a synthesized drum (Kick, Snare, HiHat,
Tom, Clap) on the same sequencer as everything else, with mono re-strike per pad and no click. Our own lean engines, no
third-party DSP. Partly built (native keys and the audio-core engine with the Kick); the track, grid, editing page and
the other engines remain, about 6-9 sprints. The main risks are audio-core room and the CPU of a busy kit.

## Later

- **Generative sequencing:** Euclidean patterns and other generators built around the touchscreen. They need far more
  new interface than anything so far, so they start with a full planning pass. Generators should write ordinary notes,
  so their results save and load like anything else.
- **Settings UI** for the polyphony cap, and pressure / MIDI-out velocity response curves (linear / exp / log).
- **Aftertouch on drum (one-shot) tracks** (recording is piano-roll only for now).
- **A web patcher** (GitHub Pages) so friends can patch their stock firmware in the browser. Low priority:
  `BUILDING.md` covers it today.

## Further plans

**F — LFO + envelope + mod matrix with CC destinations for External tracks.**
Channel-level (per-track) modulation: **2 LFOs and 2 envelopes**.
- Each envelope can optionally retrigger per note, and optionally loop with a repeat count (1–32 or infinite).
- Each LFO can optionally retrigger per note.
- Routed through a mod matrix to outgoing MIDI CCs (later velocity, pitch bend, aftertouch), sharing the same DIN
  output budget as the aftertouch output.
- **The 4 native automation lanes (A–D) get CC targets** on External tracks.
- **Mod Wheel amount** as a CC1 destination inside F rather than a separate control.
- Open approach question: reuse the native per-voice modulation engine, or a small dedicated engine clocked by the native
  sequencer. External tracks have no voices, which may make the native route a poor fit.

**D — Master saturation.** Stock already has a sidechain ducker, and the per-track clipper (Now) covers per-track drive.
What remains is a **master saturator next to the compressor**, with character-setting parameters (drive and
tone/character controls, to be designed).

**B — MIDI note (Key) as a mod source for Multisample.** Only if cheap.

## Parked

**C — Project Settings values on PROJ/SEQ pages.** Research is complete enough to build, but it's deprioritized. Its
PROJ half conflicts with Lab's PROJ-as-Shift.
