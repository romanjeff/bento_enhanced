# bento_enhanced

Community firmware mods and reverse-engineering research for the [1010music Bento](https://1010music.com/product/bento), a portable sampling/synthesis groovebox.

**Not affiliated with, endorsed by, or supported by 1010music.** Bento is a trademark of its owner. This project contains no 1010music firmware and never will: you bring your own official `.bin` files from [1010music's downloads page](https://1010music.com/downloads), and the build patches that copy on your own computer.

## Status

**Current release: [v4.47](https://github.com/romanjeff/bento_enhanced/releases/tag/v4.47)** (`BE4.47` on the boot screen), built on **stock Bento 1.6.8** and tested on hardware. It fixes a reset loop at boot that BE4.46 could get into. **To build it for your own Bento, follow [`BUILDING.md`](BUILDING.md)**: one page, no programming needed, starting from your own download of the official 1.6.8 firmware. The build checks that your result is byte for byte the image tested on hardware.

**Patch in your browser:** [romanjeff.github.io/bento_enhanced](https://romanjeff.github.io/bento_enhanced/). No install; your stock files never leave your computer. How it works: [`docs/web-patcher.md`](docs/web-patcher.md).

The `main` branch is where development happens; always build a release from its tag, as BUILDING.md shows. (Up to [v3.40](https://github.com/romanjeff/bento_enhanced/releases/tag/v3.40), the mods were built on stock 1.6.1.)

### Highlights

- **Synth engine, feature complete in v4.46** (Analog / Wavetable / FM):
  - an **Analog** oscillator type next to stock's Wavetable;
  - **FM** on Analog and Wavetable oscillators: **Sub FM** (the sub oscillator modulates its oscillator) and **FM 1>2** (OSC1 modulates OSC2). It is linear, through-zero FM, so a patch keeps its timbre across the keyboard;
  - a per-oscillator **FM envelope** (FM Atk / FM Dec) on the FM amount;
  - the FM amounts as **Mod Matrix destinations** (OSC1 Sub FM, OSC2 FM 1>2, OSC2 Sub FM), so pressure, LFOs, envelopes or velocity can drive them, plus **piano-roll automation lanes** on them;
  - **FM Env 1 / FM Env 2** as Mod Matrix sources.
- **Aftertouch sequencing**: record per-note pad pressure in the piano roll and play it back, on External tracks (MIDI poly aftertouch or channel pressure) and on Bento's own instruments. **AT Rec** and **AT Out** (Off / Poly / Channel) are set per track and saved with the project.
- **Live poly aftertouch** out of External tracks, with a polyphony cap of 6.
- **Sequencer extras**: trigger conditions (`A:B`, `1ST`, `PRE` and their inverses), per-step retriggers with velocity ramps, pad defaults with per-step locks, strike velocity, and hold-to-inspect steps.
- **Hold shortcuts** for tracks, clip launch and mixer, and an on-card **black box** for crash and stall records. Since v4.47 every boot starts clean: nothing of the mod's is carried over from an earlier run.

**Coming next:** a per-track compressor and clipper in the mixer, then a Drum Synth track with its own synthesized drum engines.

**Projects that use the mods' features are not supported on stock firmware:** they usually open there, but may not play correctly. The mod never touches the bootloader, so going back to stock is the normal firmware install. Release notes, features, usage and internals: [release notes](https://github.com/romanjeff/bento_enhanced/blob/v4.47/docs/release-notes-v4.47.md), [`mods/m1/`](https://github.com/romanjeff/bento_enhanced/tree/v4.47/mods/m1). Some features are ports of [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s work, credited where they appear.

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
