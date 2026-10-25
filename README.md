# bento_enhanced

Community firmware research and (eventually) mods for the [1010music Bento](https://1010music.com/product/bento) — a portable sampling/synthesis groovebox.

**Not affiliated with, endorsed by, or supported by 1010music.** Bento is a trademark of its owner. This project contains no 1010music firmware and never will — bring your own official `.bin` files from [1010music's downloads page](https://1010music.com/downloads). Everything here is independent reverse-engineering research plus (eventually) patches that apply to a copy of the official firmware you supply yourself.

## Status

This repo holds research, documentation, tooling, and **firmware mods**. The first one, [poly aftertouch out of External tracks](mods/m1/), works on hardware. Target firmware is **stock 1.6.1**, building on [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab). See [`ROADMAP.md`](ROADMAP.md) for priorities and where each feature stands.

## Why this exists

Bento is very new (2025/2026) and has no public reverse-engineering community yet. Its older sibling, the **Blackbox**, does — see [Related projects](#related-projects) below — and shares real lineage with Bento (same hardware vendor conventions, same internal dev-shop naming patterns) even though it's not the same firmware or framework generation. This project starts from scratch on Bento specifically, using the same general methodology the Blackbox community already validated: static analysis (Ghidra), emulated verification before touching real hardware where possible, and append-only patching that never rewrites the stock image in place.

## On safety

Quoted directly from a 1010music developer (Discord, re: Blackbox modding — the same guidance was given for Bento):

> As long as you're willing to take responsibility for it & people recognize it's at-your-own-risk, go for it. And a note for developers: don't overwrite the bootloader section of flash (if you're hacking you know what this means) — this *ought* to make it possible for people to reflash our existing production firmware through the usual channel.

That's the one rule every patch here follows without exception: **never touch the bootloader.** It's what keeps the official recovery path working no matter what a custom build gets wrong. Modifying your device's firmware is entirely at your own risk and may affect your warranty.

## Hardware, in brief

Bento runs on **two separate STM32H7 microcontrollers**, not one:

- **STM32H747XIH6** (Cortex-M7 @ 480MHz + Cortex-M4 @ 240MHz) runs `bento1.bin`. The M7 runs the audio/synthesis engine and sequencer playback; the **M4 runs the UI** (corrected 2026-10-23).
- **STM32H750XBH6** (single Cortex-M7 @ 480MHz, running from external QSPI flash) runs `bento2.bin`, the "controller". Its exact role is being re-examined.

Full detail, with how this was determined, in [`docs/architecture.md`](docs/architecture.md).

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — hardware, the two-chip split, firmware identification
- [`docs/bento-os.md`](docs/bento-os.md) — how Bento's firmware works in 1.6.1: image/core split, memory map, parameter registry, UI framework, note model, audio insert points (largely from bento-community-lab's research)
- [`docs/mod-matrix.md`](docs/mod-matrix.md) — the modulation source table, what's confirmed vs. open
- [`docs/ui-architecture.md`](docs/ui-architecture.md) — the UI widget/event/property framework: the scene graph, SessionMgr, the two parallel property systems, and why SEQ's encoders behave differently from SCENE's/Met-Gain's
- [`docs/external-tracks.md`](docs/external-tracks.md) — External track type, MIDI-out pipeline
- [`docs/project-settings.md`](docs/project-settings.md) — global settings storage (BPM/Scale/Root/Swing) and how it's accessed
- [`docs/compressor.md`](docs/compressor.md) — the master Compressor's current structure
- [`docs/references.md`](docs/references.md) — official docs and community projects this work draws on

## Related projects

- [t4kbsv99sn-source/bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab) — the first Bento-native firmware mod project (1.6.1). Its design records are the main source for [`docs/bento-os.md`](docs/bento-os.md).

This project's methodology and some early hardware cross-checks drew on the Blackbox community's own work:

- [viktorakos95/blackbox-mod](https://github.com/viktorakos95/blackbox-mod) — the most feature-rich Blackbox mod (master compressor models, sidechain ducking, chords, lo-fi modes)
- [pocketrave/blackbox](https://github.com/pocketrave/blackbox) — sequencer/clip-launcher mod
- [Blindsmyth/custom-blackbox-fw](https://github.com/Blindsmyth/custom-blackbox-fw) — nested preset folders; particularly well-organized hardware docs
- [mstaack/blackbox-rs](https://github.com/mstaack/blackbox-rs) — independent Rust board-support crate for Blackbox's hardware

None of this repository's code is derived from those projects — Bento is different hardware and a different firmware framework. The *techniques* (Ghidra-based static analysis, emulated verification, append-only patching) transfer; specific addresses do not.

## License

GPL-3.0-or-later — see [`LICENSE`](LICENSE). Covers the code and documentation in this repository only (never 1010music's firmware). Relicensed from MIT on 2026-10-25, when the repo started carrying firmware mods built on [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab) (also GPL-3.0-or-later).
