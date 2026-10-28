# Roadmap

Status as of 2026-10-27, **release [v3.40](https://github.com/romanjeff/bento_enhanced/releases/tag/v3.40)**.
**Target firmware: stock Bento 1.6.1.** Since v2.3 our builds sit directly on stock;
several features are ports of
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s work.
Details of everything below are in [`mods/m1/`](mods/m1/).

## Done (on hardware)

- **A — pad pressure out of External tracks.** Poly aftertouch (`0xA0`) since Milestone 1
  (2026-10-25), with a polyphony cap of 6 (oldest stolen, same note retriggers its voice).
- **M2 — aftertouch sequencing** (2026-10-27). Per-note pressure is recorded in the piano
  roll at 16 points per step and saved in a `curves.bnt` sidecar, so projects stay
  stock-readable. It plays back on External tracks (poly or channel pressure) and on
  internal instruments (stock's per-voice pressure). **AT Rec** and **AT Out**
  (Off / Poly / Channel) are set per track, and live pad pressure follows AT Out too.
- **Sequencer:** trigger conditions; per-step retriggers (hits, division, ramp); pad
  defaults with per-step locks; strike velocity; hold-to-inspect steps.
- **UI:** hold shortcuts (TRACKS / LAUNCH / MIXER / INST).
- **Diagnostics:** black box on the SD card (crash records, audio stall records).

## Now: stability

- **S3 — soak test** of v3.40 in real use: long sessions, many simultaneous aftertouch
  notes on internal and External tracks, repeated project loads.
- **Verify on hardware:** MIDI retriggers with their own note-offs (v3.39), and the
  curve pool clearing on each project load (v3.38).

## Next: generative sequencing

Euclidean patterns and other generative sequencer ideas built around Bento's
touchscreen. They need far more new interface than anything so far, so they start with
a full planning pass (requirements, UI, interaction with existing notes, risks) once
v3.40 has proven stable. Generators should write ordinary notes, so their results save,
load and stay stock-readable like anything else.

## Later

- **Settings UI** for the polyphony cap, and pressure / MIDI-out velocity response
  curves (linear / exp / log).
- **Aftertouch on drum (one-shot) tracks** (recording is piano-roll only for now).

## Further plans

**F — LFO + envelope + mod matrix with CC destinations for External tracks.**
Channel-level (per-track) modulation: **2 LFOs and 2 envelopes**.
- Each envelope can optionally retrigger per note, and optionally loop with a
  repeat count (1–32 or infinite).
- Each LFO can optionally retrigger per note.
- Routed through a mod matrix to outgoing MIDI CCs (later velocity, pitch bend,
  aftertouch), sharing the same DIN output budget as A.
- **The 4 native automation lanes (A–D) get CC targets** on External tracks.
- Open approach question: reuse the native per-voice modulation engine (see [`docs/bento-os.md`](docs/bento-os.md)
§7), or a small dedicated engine clocked by the native sequencer. External tracks
have no voices, which may make the native route a poor fit.
- **E — Mod Wheel amount** is planned as a CC1 destination inside F rather than
  a separate control.

**D — Saturation.** Stock 1.6 added a native sidechain ducker
(Source Track / ext input / pad / MIDI note / clock; attack/hold/release/
depth/threshold), so the sidechain part of D is dropped. The remaining scope is
a **master saturator next to the compressor**, with character-setting
parameters (drive and tone/character controls, to be designed). Lab's per-track
soft clipper doesn't cover this. Registry IDs must avoid Lab's
`0x128–0x12D` (chord editor), which overlap the compressor-range gap we
found in 1.5.32.

**B — MIDI note (Key) as a mod source for Multisample.** Only if cheap.
The native voice binding for Key is known (`source 14 → voice+0xBC`).

## Parked

**C — Project Settings values on PROJ/SEQ pages.** Research is complete enough to
build (the SEQ header is the same view Lab's Euclidean bank rewrites), but it's
deprioritized. Its PROJ half conflicts with Lab's PROJ-as-Shift.
