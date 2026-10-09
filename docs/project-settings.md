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

## BPM and Swing are a special case — a separate live tempo engine, not this table

Confirmed by decompiling the pages that already show BPM/Swing live
(SCENE, and a Metronome/Count-In settings page): these two values are
**not** transparently reachable through the universal accessor for
display purposes, even though they're registered in the same 472-entry
table with real storage keys (`globtempo`/`swing`). They're backed by a
separate, live tempo/clock-engine object — `FUN_08133a34` calls
`FUN_0812b4ce` to get a handle to it, then reads the current value at
`handle+0x10`.

Every page with a genuinely working BPM/Swing control has **hand-written
special-case code** — `if (id == 0x8b || id == 0x8e)` — that pre-fetches
from this engine before falling through to the generic accessor
(`FUN_08132f14`) for every other slot on that page. A page that places
these IDs into its own encoder-slot table *without* that special case
(confirmed directly: adding them to SEQ's table this way) gets a correct
label — since label text genuinely is generic and ID-driven — but a
value that's seeded from whatever unrelated field the generic path
happens to read, and resets every redraw tick.

**Practical implication for any future page that wants a live BPM/Swing
control:** it needs that same two-line special case, not just an entry
in its encoder-slot table. The write-side setter (encoder turn → commit
to the tempo engine) is still being traced as of this writing.
