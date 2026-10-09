# Roadmap

Status as of this writing. Research-only — no patches have shipped yet.

## In progress

**A. External-track pad pressure out over MIDI.** Bento already has a unified internal "Pressure" value (pad sensor + incoming MIDI aftertouch, merged) feeding its mod matrix today, but it never reaches outgoing MIDI for External tracks. Planned: default to Polyphonic Key Pressure (0xA0), configurable to Channel Pressure (0xD0) from the track's INST page. A receive-side MIDI aftertouch parser already exists in the firmware and is the template for this (see [`docs/mod-matrix.md`](docs/mod-matrix.md)).

**B. MIDI note as a mod source, for Multisample tracks.** Confirmed on real 1.5.32 hardware: `Key` is already available as a mod source on Wavetable and Granular tracks — only Multisample is missing it (the official manual's "Granular-only" claim is outdated for current firmware). Scope narrowed accordingly: find and open whatever currently excludes Multisample specifically.

**C. Project Settings values exposed on PROJ and SEQ pages.** Root Note, Scale, BPM, and Swing currently require navigating into a Project Settings submenu. Plan: surface all four on PROJ's available encoders, and BPM+Swing on SEQ's two rightmost encoders — reading/writing the exact same underlying values (confirmed accessible via one shared accessor function, see [`docs/project-settings.md`](docs/project-settings.md)), not separate copies.

**D. Master compressor: sidechain source, HP/LP filter, saturation.** See [`docs/compressor.md`](docs/compressor.md) for current state and the open feasibility question (whether individual tracks are addressable pre-mix for a true sidechain tap).

**E. Mod Wheel amount on the External track's INST page.** A new per-track MIDI-out control, same scope class as (A).

## Deferred — recorded, not yet started

**F. Full modulation system for External tracks.** The longer-term direction: 2 LFOs, 2 envelopes, and a mod sequencer — the same class of modulation sources Granular tracks already have — available on External tracks, targeting outgoing MIDI CCs as well as velocity, pitch, aftertouch, pitch bend, and bank change. This is essentially porting Granular's internal modulation system to drive MIDI output rather than internal synthesis. Substantial scope; intentionally not started until (A)–(E) are further along.
