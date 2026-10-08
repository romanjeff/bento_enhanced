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

## Open feasibility question

The sidechain source feature's entire feasibility rests on one question: is any individual track's signal addressable *before* the final mix-down to Out 1, or does everything reach compressor-adjacent code already summed? If tracks are summed before the compressor's code ever runs, a sidechain tap needs an earlier point in the mixer architecture, not just a new input to the existing detector. This is under active investigation — check this repo's issue tracker or commit history for the current state, since this file will lag behind the investigation itself.
