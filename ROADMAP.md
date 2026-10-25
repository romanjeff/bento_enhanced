# Roadmap

Status as of 2026-10-25 (Milestone 1 reached). **Target firmware: stock Bento 1.6.1**, building on
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
work. Their CH10 / 1.6HA release reproduces byte for byte with our toolchain
(see [`tools/lab-build-wsl/`](tools/lab-build-wsl/)). Our first feature (poly aftertouch) runs on
hardware; its source isn't published yet. Earlier research was done on 1.5.32 and is being re-located in 1.6.1
([`tools/ghidra-1.6.1/anchors_1532_to_161.txt`](tools/ghidra-1.6.1/anchors_1532_to_161.txt)).

## Priority order

**0. Hardware loop (prerequisite).** Flash Lab's CH10 as-is and confirm it boots.
Then flash a minimal build of our own inside their build system. Stock 1.6.1's
"Last crash info" page (core, fault address, CFSR) is the first debugging tool.

**1. A — External-track pad pressure out over MIDI.** *Top priority.* Bento's 16
pads each have an independent pressure sensor, and the raw per-pad values reach
the audio core unmerged (see [`docs/external-tracks.md`](docs/external-tracks.md)).
Plan: Polyphonic Key Pressure (0xA0) by default, Channel Pressure (0xD0) as an
option. Known design constraints:
- External tracks don't currently record which pad produced which note. This
  bookkeeping has to be added.
- DIN MIDI bandwidth (~1,000 messages/s): pressure changes every audio tick, so
  output is **sent on clock ticks (24 PPQ) while running**, at a fixed equivalent
  rate while stopped (open: confirm), **changed values only**, running status, and
  a global budget where note on/off always win.
- **Milestone 1: reached 2026-10-25.** Poly aftertouch out of an External
  track on hardware, verified on a MIDI monitor and a poly-AT synth (per-note
  LFO rate following each pad). How it works: stock already computes per-note
  pressure (same curve as velocity, correct note from the keyboard mapping)
  and delivers it to the External track, which drops it. The mod forwards it
  as `0xA0 note value` on the track's MIDI port/channel at up to 100 Hz per
  note (changed values only), under a global output budget.
- Next for A: polyphony cap (default 6, max 8; oldest-first stealing with
  forced note-offs, sequencer notes included), then a settings UI (cap,
  linear/exp/log curves for pressure and MIDI-out velocity), pressure for all
  MIDI-out track types, and recording pressure into the sequencer.
- Next step after M1: record pressure into the sequencer.

**2. F — LFO + envelope + mod matrix with CC destinations for External tracks.**
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

**3. D — Saturation.** Stock 1.6 added a native sidechain ducker
(Source Track / ext input / pad / MIDI note / clock; attack/hold/release/
depth/threshold), so the sidechain part of D is dropped. The remaining scope is
a **master saturator next to the compressor**, with character-setting
parameters (drive and tone/character controls, to be designed). Lab's per-track
soft clipper doesn't cover this. Registry IDs must avoid Lab's
`0x128–0x12D` (chord editor), which overlap the compressor-range gap we
found in 1.5.32.

**4. B — MIDI note (Key) as a mod source for Multisample.** Only if cheap.
The native voice binding for Key is known (`source 14 → voice+0xBC`).

## Parked

**C — Project Settings values on PROJ/SEQ pages.** Research is complete enough to
build (the SEQ header is the same view Lab's Euclidean bank rewrites), but it's
deprioritized. Its PROJ half conflicts with Lab's PROJ-as-Shift.
