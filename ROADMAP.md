# Roadmap

Status as of 2026-10-25. **Target firmware: stock Bento 1.6.1**, building on
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
work. Their CH10 / 1.6HA release reproduces byte for byte with our toolchain
(see [`tools/lab-build-wsl/`](tools/lab-build-wsl/)). No patches of our own have
shipped yet. Earlier research was done on 1.5.32 and is being re-located in 1.6.1
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
- DIN MIDI bandwidth (~1,000 messages/s) requires thinning and rate-capping;
  pressure changes every audio tick.

**2. F — LFO + envelope + mod matrix with CC destinations for External tracks.**
At least one LFO and one envelope routed through a mod matrix to outgoing MIDI
CCs (and later velocity, pitch bend, aftertouch). Open approach question: reuse
the native per-voice modulation engine (see [`docs/bento-os.md`](docs/bento-os.md)
§7), or a small dedicated engine clocked by the native sequencer. External tracks
have no voices, which may make the native route a poor fit.
- **E — Mod Wheel amount** is planned as a CC1 destination inside F rather than
  a separate control.

**3. D — Saturation.** Stock 1.6 added a native sidechain ducker
(Source Track / ext input / pad / MIDI note / clock; attack/hold/release/
depth/threshold), so the sidechain part of D is dropped. The remaining scope is
saturation. Placement (master vs. per-track) is still to be decided. Lab's
channel strip already provides a per-track soft clipper.

**4. B — MIDI note (Key) as a mod source for Multisample.** Only if cheap.
The native voice binding for Key is known (`source 14 → voice+0xBC`).

## Parked

**C — Project Settings values on PROJ/SEQ pages.** Research is complete enough to
build (the SEQ header is the same view Lab's Euclidean bank rewrites), but it's
deprioritized. Its PROJ half conflicts with Lab's PROJ-as-Shift.
