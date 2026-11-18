# bento_enhanced v4.46 (stock 1.6.8): FM Env 1 / FM Env 2 as Mod Matrix sources

BE4.46 lets each oscillator's FM envelope modulate anything in a Wavetable track's Mod Matrix. It builds on BE4.44 (FM on
Wavetable oscillators). Plan and measurements: `docs/feasibility-fm-env-sources.md` (the decisions and "S1 / S2 as
built"). Device checklist: `docs/checklist-v4.46.txt`.

(BE4.45 is the label of a parallel lane, FM amounts as Mod Matrix destinations. It is not in this image; the two will be
merged later.)

## What it does

- **Two more sources on a Wavetable track's Mod Matrix page, right after Envelope 2:**
  - **FM Env 1**: OSC1's FM envelope (its FM Atk / FM Dec);
  - **FM Env 2**: OSC2's.

  The page's Source list is now: [None], Velocity, Pressure, Envelope 1, Envelope 2, **FM Env 1, FM Env 2**, LFO 1,
  LFO 2, Mod Seq, Key, Mod Wheel, Macro X, Macro Y, Auto A..D.
- **Per voice, one-shot, restarted at every note**, like two extra envelopes. Each voice of a chord has its own.
- **Analog and Wavetable oscillators alike** (BE4.44 gave Wavetable oscillators their FM envelope).
- **It runs without any FM.** The envelope runs whenever FM Atk / FM Dec is set, at any FM amount, so with Sub FM and
  FM 1>2 at 0 it is simply a third / fourth envelope.
- **The value is the FM envelope's own** (0..1, the same curve and times as the FM depth). It is the value at the start
  of each audio block, which is also where the FM depth's ramp starts.
- **An unset envelope** (FM Atk 0 and FM Dec Inf) gives a constant 1, as the FM depth does: a routing from it is a fixed
  offset.

Nothing else changes. Granular and the other track types keep stock's source list.

## Projects

- A routing from FM Env is a stock routing: `src="env3"` (FM Env 1) or `src="lfo3"` (FM Env 2). These are stock's
  Envelope 3 / LFO 3 ids, which a Wavetable voice does not have on stock. No new project keys.
- **Stock 1.6.8 and BE4.44** (the rollbacks) open such a project and keep the routing, but play nothing from it: their
  Wavetable voice has no source for those ids. Their Mod Matrix page shows the slot as [None]. Saving there keeps it.
  **Editing that slot there removes it**: stock's page sends the slot back with source None on an amount edit (and a new
  source replaces it). Back on BE4.46, an untouched routing works again.
- BE4.46 opens every BE4.44 project unchanged.
- Changing a track's instrument type starts a new matrix (stock's instrument replace makes a new track), so an FM Env
  routing never turns into a real Envelope 3 / LFO 3 routing of another engine.

## Timing

- The value moves once per audio block (2.7 ms), as stock's envelopes do; a Pitch routing steps at that rate too.
- Routed to an Envelope or LFO parameter, it acts one block late: those tick before the oscillators in stock's order.
  Stock has the same lag between its own sources there.

## CPU and size

Offline instruction counts per voice and audio block (an_enter and both oscillator renders). The device's figure is the
"DSP nn%" on OSC1's title.

| Voice | No FM Env routing | With FM Env routed |
|---|---|---|
| Stock Wavetable voice | 14,160 (BE4.44 14,155) | 14,209 |
| Analog full FM, FM Atk 3 / Dec 8 | 28,115 | 28,168 |
| Full Wavetable voice, FM Atk 3 | 27,266 | 27,339 |
| BE4.44's heaviest steady voice (note 24, light FM, Position every block) | 34,765 (BE4.44 34,760) | 34,826 (heaviest block 39,458) |

- Without a routing on FM Env the cost is +5 a voice-block (one load and a branch); the first routing on a voice switches
  its publish on for that voice. The heaviest steady routed voice is 483 under the existing ceiling (35,309).
- The page's work (list, names, the validator) runs only when the Mod Matrix page builds or edits its rows.
- Sizes: DSP 31,084 B (9,620 B free of the window); synth UI 16,054 B (+208); the image 0x189520, 15,892 B under the
  size proven on the device.

## Known limits

- The source list order and names are BE's on the Mod Matrix page; any other screen that shows a source name through
  another path was not found offline (checklist section 2 asks).
- FM amounts are not Mod Matrix destinations in this release (the BE4.45 lane).
- On stock and BE4.44 an FM Env routing is kept but silent, and editing its slot removes it (above).
