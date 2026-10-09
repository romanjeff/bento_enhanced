# Master compressor

## Current state (from the official user manual, Table 15-9)

Bento ships a master compressor on the Out 1 bus, configured from MIXER → Menu → Compressor:

| Knob | Parameter | Range |
|---|---|---|
| 1 | Compressor (on/off) | Off, On |
| 2 | Attack | 0–25ms |
| 3 | Release | 0–1000ms |
| 4 | Thresh | -48dB–0dB |
| 5 | Ratio | 1–100 |
| 6 | Gain (makeup) | -36dB–+36dB |
| 7 | AutoMkup | Off, On |
| 8 | Peak Limit | Off, On |

A straightforward feedforward design: no sidechain source, no sidechain filter, no saturation. It processes the Out 1 bus **after** the mix — the manual and the 1.5.30 release notes both confirm "Out 1 includes processing from the Mod FX, Reverb, Delay, Compressor, and DJ FX," i.e. everything is already summed by the time the compressor sees it.

## Planned additions

A second row of controls is planned:

1. **Sidechain source** — select a track whose signal drives the compressor's detector, independent of the signal actually being compressed. This needs to be a true external trigger: the compressor engages based on the selected track's level *regardless* of whether the main (Out 1) signal has itself crossed threshold — not a gate that requires both conditions.
2. **Switchable HP/LP sidechain filter** ahead of the detector.
3. **Saturation** — algorithm not yet decided.

## Parameter storage, confirmed

The compressor's 8 knobs live in one global parameter-descriptor table (472 entries, 0x20 bytes each) that covers every parameter in the firmware, not just the compressor — the same mechanism used for Project Settings (see [`project-settings.md`](project-settings.md)). Each entry: `{id, type, label pointer, enum table / On-Off table, enum count, min, max, storage-key pointer, flags}`.

| ID | Type | Label | Range | Storage key |
|---|---|---|---|---|
| `0x9b` | bool | Compressor | 0/1 | `globcomp` |
| `0x122` | numeric | Attack | 0–250 | `cattack` |
| `0x123` | numeric | Release | 0–10000 | `crelease` |
| `0x124` | dB | Thresh | neg–0 | `cthreshdb` |
| `0x125` | numeric | Ratio | 1–100 | `cratio` |
| `0x12d` | dB | Gain | neg–pos | `cgaindb` |
| `0x126` | bool | AutoMkup | 0/1 | `cautomakeup` |
| `0x12f` | bool | Peak Limit | 0/1 | `peaklimit` |

(`0x12e`/`cbypass` is a separate bypass flag, not one of the 8 manual-documented knobs.)

**`0x127`–`0x12c` — six consecutive IDs — are unused**, sitting right inside the compressor's own ID range. This is the planned landing spot for the new sidechain source, filter, and saturation parameters, using the same registration pattern already established for the existing 8.

**Reusable filter template:** a switchable-type filter (`filtertype`: Low Pass/High Pass/Band Pass/Notch, plus `cutoff`, `res`, `filtenable`) already exists per track/cell elsewhere in the firmware — a strong structural match for the planned HP/LP sidechain filter. Its DSP coefficient math hasn't been located yet, only its parameter shell.

## Open feasibility question

The sidechain source feature's entire feasibility rests on one question: is any individual track's signal addressable *before* the final mix-down to Out 1, or does everything reach compressor-adjacent code already summed? Per-track storage keys (`trkgain`, `trkpan`, `trkmute`, `trkfx1send`, `trkfx2send`, `outputbus`) confirm tracks are individually parameterized, which is suggestive — but the actual per-sample mix/sum routine, where this would need to be verified for real, hasn't been found or traced yet. **Current verdict: tractable but unverified.** The compressor's own gain-computer/detector code is similarly not yet located — immediate-value scans for its parameter IDs produced heavy false positives (collisions with unrelated struct offsets elsewhere in the firmware). This is the main open item blocking a confident design for this feature.
