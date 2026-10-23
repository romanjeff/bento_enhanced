# Architecture

## Two chips, three cores

Confirmed from a direct 1010music developer statement (forum post) plus an independent hardware teardown identifying exact part numbers:

| Chip | Cores | Flash | Role | Firmware file |
|---|---|---|---|---|
| **STM32H747XIH6** | Cortex-M7 @ 480MHz + Cortex-M4 @ 240MHz | 2MB internal | M7: audio engine + sequencer playback. M4: the UI (see "Which core owns what" below) | `bento1.bin` |
| **STM32H750XBH6** | Cortex-M7 @ 480MHz, single-core | 128KB internal only — too small for a real app, runs from external QSPI | "UI and disk streaming" per the developer's post; actual role now an open question (see below) | `bento2.bin` (self-identifies as `BENTOCONTROLLER 1.0.10-QSPI`) |

One correction worth keeping straight: the developer's forum post describes the second core as "a second M7 core." The STM32H747 is a well-documented part and is actually **Cortex-M7 + Cortex-M4**, not M7+M7 — confirmed against ST's own published specs. The firmware itself shows the M4 running the **UI application** (see below). Whether the M4 also runs the reverb, as the post says, is unverified.

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

## Which core owns what (corrected 2026-10-23)

**Earlier versions of this document said the UI lives in `bento2.bin`. That was wrong.** `bento1.bin` contains three images, each with its own vector table (checked in both 1.5.32 and 1.6.1):

| Flash range | Image | Initial SP |
|---|---|---|
| `0x08040000..0x080E0000` | M7 application: audio engine, sequencer playback, pad input | AXI SRAM (`0x2404CDA0` in 1.6.1) |
| `0x080E0000..0x08100000` | Disk mode (`MobiusDiskMode`) | `0x24029898` |
| `0x08100000..` end | **M4 application: the UI**: SessionMgr/model, widgets, pages, parameter registry, project XML, Undo | D2 SRAM (`0x300390F0` in 1.6.1) |

The UI therefore runs on the H747's **Cortex-M4**, and all of this project's UI tracing (functions at `0x0812xxxx`–`0x0818xxxx`, SessionMgr in `0x3002xxxx`) was in that M4 image all along. The earlier conclusion came from not finding page-name strings such as `PROJ`. `SEQ` and `SessionMgr` strings *are* present, and page names are probably built differently. [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab) independently confirms the split: its UI code runs under the M4's MPU, which permits execution only from `0x08100000..0x08200000`. The full map is in [`bento-os.md`](bento-os.md).

This also explains the earlier "no ADC references" finding. Pad events reach `bento1.bin` from outside: the M7 image has a pad input hook that forwards to the M4 UI through a native queue. What `bento2.bin` (the H750 "controller") actually does is now an **open question**. Scanning pads/LEDs and relaying them is a plausible role, but unverified. The "UI and disk streaming" description above comes from the developer's forum post and should be re-read with this in mind.

## No existing public teardown

As of this research, no public hardware teardown or chip-ID writeup exists for Bento anywhere (forums, press, the usual channels) — everything in this document came from direct binary inspection of the official firmware files, not from a secondary source. Binary-level identification turned out to be sufficient for firmware-modding purposes without needing a physical teardown.
