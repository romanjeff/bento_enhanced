# References

## Official

- [1010music downloads](https://1010music.com/downloads) — official firmware, the only legitimate source for the `.bin` files this project's patches will apply to.
- Bento user manual, quick-start guide, and release notes (1.3.17b manual plus 1.47/1.5.30 release notes used in this research) — available from 1010music; not redistributed here, see [`README.md`](../README.md).

## Community prior art (Blackbox)

Bento's older sibling has an active, independent reverse-engineering and modding community. This project draws on their published methodology, not their addresses (different hardware, different firmware framework — see [`architecture.md`](architecture.md)):

- [viktorakos95/blackbox-mod](https://github.com/viktorakos95/blackbox-mod) (MIT) — master compressor models, reverb/delay types, sidechain ducking, chords, lo-fi modes. Method: Ghidra + custom Python tooling + Unicorn-emulated testing before hardware; patches append new code after the stock image rather than rewriting in place.
- [pocketrave/blackbox](https://github.com/pocketrave/blackbox) — clip-launcher sequencer mod. Method: generates ARM Thumb-2 machine code directly in Python rather than compiling C; diff-only patch distribution.
- [Blindsmyth/custom-blackbox-fw](https://github.com/Blindsmyth/custom-blackbox-fw) — nested preset folders. Particularly well-organized hardware documentation (`docs/hardware.md`, `docs/re-notes.md`) that this project's own doc structure takes inspiration from.
- [mstaack/blackbox-rs](https://github.com/mstaack/blackbox-rs) — independent Rust embedded board-support crate bringing up Blackbox's hardware from scratch. The primary source for Blackbox's own confirmed hardware table (STM32H743XI, display/touch/audio codec pinout).
- [GnarlyAsparagus7/digiemu](https://github.com/GnarlyAsparagus7/digiemu) (GPL-2.0) — a ColdFire-family emulator project for a different manufacturer's hardware (Elektron), referenced here only as a methodology example: booting real stock firmware to a live UI with audio, for testing mods before flashing real hardware. No Bento-specific emulator currently exists.

Common thread across the Blackbox projects: Ghidra for static analysis, emulated testing (Unicorn) before anything touches real hardware, append-only patching that never rewrites the stock image, and diff/patch-only distribution that never redistributes 1010music's own firmware. This project follows the same principles.
