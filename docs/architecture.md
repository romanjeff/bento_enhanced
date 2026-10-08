# Architecture

## Two chips, three cores

Confirmed from a direct 1010music developer statement (forum post) plus an independent hardware teardown identifying exact part numbers:

| Chip | Cores | Flash | Role | Firmware file |
|---|---|---|---|---|
| **STM32H747XIH6** | Cortex-M7 @ 480MHz + Cortex-M4 @ 240MHz | 2MB internal | Audio processing (M7 does the bulk of it; M4 handles reverb) | `bento1.bin` |
| **STM32H750XBH6** | Cortex-M7 @ 480MHz, single-core | 128KB internal only — too small for a real app, runs from external QSPI | UI and disk streaming | `bento2.bin` (self-identifies as `BENTOCONTROLLER 1.0.10-QSPI`) |

One correction worth keeping straight: the developer's forum post describes the second core as "a second M7 core." The STM32H747 is a well-documented part and is actually **Cortex-M7 + Cortex-M4**, not M7+M7 — confirmed against ST's own published specs. The functional claim (reverb runs on the lesser core) is almost certainly still accurate; only the core-type wording was imprecise.

### Confirming it from the binaries themselves

Both files open with valid little-endian ARM Cortex-M vector tables:

- **`bento1.bin`**: initial SP `0x2404b188` (STM32H7's AXI SRAM region, `0x24000000`+), reset handler at `0x080403cd` (odd = Thumb bit set). Code is linked at `0x08040000` in internal flash — the first 256KB of the chip's flash is a separate installer, not part of this file.
- **`bento2.bin`**: initial SP `0x2000aaf0` (DTCM region), reset handler at `0x900003c3`. `0x90000000` is the memory-mapped OctoSPI external-flash window on STM32H7 parts — this chip's tiny 128KB internal flash can't hold a real application, so it executes in place from external QSPI, exactly as its flash size forces.

## Firmware identity

Debug path strings embedded in `bento1.bin` reveal the internal build system:

```
C:\Projects\code\matrixsw\MobiusFramework\MobiusDiskMode\Src\I2CPortMobius.cpp
```

Same dev shop (`matrixsw`) and file-naming convention that Blackbox's own firmware uses (`BoomboxFramework`, `I2CPortBoombox.cpp`) — but a different framework codename, `MobiusFramework`. This is the precise answer to 1010music's own marketing claim that Bento runs "a completely new operating system": true at the framework/codename level, not true in the sense of an unrelated codebase. Same team, same conventions, an evolved generation.

**Practical consequence:** Blackbox community knowledge (hook techniques, general code shape, peripheral conventions) is a useful starting point to verify, not a source of directly-reusable addresses. Bento's own `MobiusFramework` build has its own layout throughout.

## Which chip owns what

This mattered enough during research to call out explicitly, since it wasn't obvious up front: `bento1.bin` (the audio/M7+M4 chip) owns engine logic, global parameter storage, and the modulation subsystem — confirmed repeatedly by finding real, well-formed data structures for all of that there. It does **not** contain the UI chrome: there's no RTTI in this binary at all (built `-fno-rtti`) and no top-level page-navigation strings (`PROJ`, `SEQ`, etc.) anywhere in it. That logic is expected to live on `bento2.bin` instead, consistent with the "UI and disk streaming" role described above — any research into page layout, button-to-screen dispatch, or per-page encoder assignment should start on `bento2.bin`, not `bento1.bin`.

One more data point worth recording: `bento1.bin` has **zero references anywhere to its own ADC peripherals.** Front-panel pad pressure isn't read on the audio chip — it's almost certainly scanned by the UI/disk chip and relayed over the inter-chip link.

## No existing public teardown

As of this research, no public hardware teardown or chip-ID writeup exists for Bento anywhere (forums, press, the usual channels) — everything in this document came from direct binary inspection of the official firmware files, not from a secondary source. Binary-level identification turned out to be sufficient for firmware-modding purposes without needing a physical teardown.
