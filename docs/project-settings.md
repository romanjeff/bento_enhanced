# Project Settings storage

All findings below are from `bento1.bin` (the STM32H747 audio chip) — see [`architecture.md`](architecture.md).

## Storage

One global flat descriptor array, 0x20-byte records, indexed by parameter ID, built by `FUN_08065700` (a byte-for-byte duplicate exists at `FUN_08160284`, ~0xFAB84 bytes later in the image — almost certainly a second firmware bank/slot, not a second logical subsystem).

Confirmed fields, matching the official user manual's documented ranges exactly:

| Field | ID | Type | Range | Save key |
|---|---|---|---|---|
| BPM | `0x8b` | numeric | 40–250 | `globtempo` |
| Scale | `0x8c` | enum | 23 values | `keymode` |
| Root Note | `0x8d` | enum | 12 values | `keyroot` |
| Swing | `0x8e` | numeric | 1–99 | `swing` |

## Access pattern

Read through a **universal accessor**, `FUN_08132f14(ctx, outBuf, paramId, &outVal)`, with **121 distinct callers** across the UI codebase — this is not a Project-Settings-private function. One concrete example, `FUN_08142b28`, calls it with `0x8c` and `0x8d` together from an entirely different screen context, to render a "Scale + Root Note" summary string.

This matters for anyone adding a new UI surface for these values: because access goes through one shared accessor rather than per-screen copies, **any new caller of `FUN_08132f14` for one of these IDs automatically reads/writes the same canonical value** every other surface uses — no custom synchronization logic needed to keep multiple on-screen controls for the same setting in sync.

## Where this doesn't reach

This accessor and the parameter table live entirely on the audio chip (`bento1.bin`). The top-level page-navigation UI — which encoder on which physical page maps to which parameter — is not here. See [`architecture.md`](architecture.md) for why that's expected to live on the other chip (`bento2.bin`) instead, and for the current state of that side of the research.
