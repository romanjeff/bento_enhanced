# bento_enhanced v4.44 (stock 1.6.8): FM on Wavetable oscillators

BE4.44 gives Wavetable oscillators the same FM as Analog ones. It builds on BE4.43 (one FM amount per oscillator plus its
FM envelope). Plan and measurements: `docs/plan-wt-fm.md`. Device checklist: `docs/checklist-v4.44.txt`.

## What it does

- **Sub FM on a Wavetable OSC1.** The sub oscillator modulates OSC1's pitch, the same way it modulates an Analog OSC1.
- **FM 1>2 into OSC2, from either OSC1 type.** OSC1 modulates OSC2's pitch for all four type combinations:
  - Analog to Analog (as in BE4.43);
  - Analog to Wavetable;
  - Wavetable to Wavetable;
  - Wavetable to Analog.
- **The same FM as Analog:**
  - through-zero linear FM;
  - the same depth curve (the knob's 0..100 % gives index 0..16, steeper at the top);
  - the same FM Atk / FM Dec envelope, restarted at every note, per voice.

  An FM amount of 0 plays the oscillator exactly as stock does.
- **The modulator is OSC1 before its Level.** Turning OSC1's Level to 0 mutes OSC1, but OSC2 still hears it, so you can
  hear the FM tone on its own. This works the same with a Wavetable OSC1.
- **Anti-aliasing under FM.** A Wavetable oscillator under heavy FM reads a smoother copy of its table (one mip level
  coarser), changed with a crossfade, so it stays clean.
  - Light FM keeps the note's own level: up to about a third of the knob, nothing gets duller.
  - The top of the knob trades some aliasing for brightness. This is deliberate: the "crazy and interesting" end.
  - Two alternate kits move that trade-off. They are the same firmware with one constant changed:
    - `-mip1.5`, boot label `BEM1.5`: a little cleaner and a little darker;
    - `-mipoff`, boot label `BEMOFF`: never darker than the note's own level, with more aliasing at the top.
- **A table's DC is kept out of FM 1>2.** A frame whose waveform sits off centre (a narrow pulse, a half-wave shape) would
  otherwise detune OSC2 at any FM 1>2 amount, and even make it run backwards. Each frame's average is removed from the
  modulator, so OSC2 stays in tune. As on Analog, OSC1's own Sub FM can still move OSC2's pitch: that is part of the
  FM sound.

## The controls

Knob numbers are the same as Analog's. Changing an oscillator's Type leaves its FM knobs where they are.

| Row | Knobs 1..8 |
|---|---|
| OSC1, Wavetable | Level, Pitch, Position, Type, -, **Sub FM, FM Atk, FM Dec** |
| OSC2, Wavetable | Level, Pitch, Position, Type, -, **FM 1>2, FM Atk, FM Dec** |
| OSC1, Analog (unchanged) | Level, Pitch, Shape, Type, Glide, Sub FM, FM Atk, FM Dec |
| OSC2, Analog | Level, Pitch, Shape, Type, Sync 1>2, **FM 1>2**, FM Atk, FM Dec |

OSC2's knob 6 is now FM 1>2 for every OSC1 type (see the behaviour changes below).

## Behaviour changes from BE4.43

- **An Analog OSC2 next to a Wavetable OSC1.**
  - Knob 6 is now FM 1>2. BE4.43 showed OSC2's Sub FM there.
  - OSC2's Sub FM no longer plays, whatever OSC1's type. Its value is kept and saved.
  - In a BE4.43 project with this layout:
    - a Sub FM set on that OSC2 stops playing;
    - an FM 1>2 value that OSC2 still held from earlier now plays. BE4.43 kept it but hid it.

    To clear a value you don't want, turn OSC2's FM 1>2 (knob 6) to 0 and save.
- **Mixed sync** (Wavetable OSC1 as master, Analog OSC2 synced to it, Sync 1>2 on):
  - OSC1's Sub FM plays as 0 while OSC2 is synced to it. The value is kept and comes back when Sync is off.
  - FM 1>2 keeps working under sync, and sync stays sample-accurate, as in BE4.43. While synced, OSC1 plays stock's own
    playback, so the slave's copy of its phase stays exact. A once-per-block re-alignment to OSC1's real phase is only a
    safety net (for example the block right after Sync is turned on mid-note).
- **A Wavetable OSC1 at Level 0 that feeds FM 1>2 keeps running** so OSC2 still has its modulator. You can't hear it.
  Its playback position then differs from where stock's would be when you turn it back up. The return is smooth: Level
  ramps in as usual.

## Projects

- **Projects that use the mod's features are not supported on stock firmware**: they usually open,
  but may not play correctly. There are no new project keys:
  - stock 1.6.8 ignores the FM values;
  - BE4.43 keeps them, but plays Wavetable oscillators as stock.
- BE4.44 opens BE4.43 projects. The only change is the Analog OSC2 next to a Wavetable OSC1 case above.

## CPU

Offline instruction counts per voice and audio block (note 60 and full FM depth unless the row says otherwise). The
device's figure is the "DSP nn%" on OSC1's title.

| Voice | Median | Heaviest block |
|---|---|---|
| Stock Wavetable voice | 14.2k | |
| Analog full FM voice, BE4.43's heaviest FM voice | 28.9k | |
| Full Wavetable FM voice (Sub FM on OSC1, FM 1>2 Wavetable to Wavetable) | 27.4k | |
| The same with Position moving every block (an LFO on Position) | 34.5k | |
| Low notes (C0..C1), light FM (Sub FM 5..30 %, FM 1>2 30 %), Position moving every block | 34.8k | 39.4k |
| The same with FM Atk on both oscillators | | 43.9k |
| Wavetable OSC1 feeding an Analog OSC2 | 19.9k | |
| Wavetable OSC1 feeding a Wavetable OSC2 | 19.2k | |

- The heaviest steady case is a low note with light FM and an LFO on Position: every block reads a new frame at a fine
  level. Its heaviest block (39.4k) is the one where OSC2 settles its level after the note starts.
- The FM Atk case's heaviest blocks are the few where a rising FM depth makes an oscillator change its mip level.
- The first block of a note no longer crossfades the mip level, so it costs about the same as the blocks after it.

## Known limits

- At high notes under heavy FM, the reader is already on its coarsest level and aliases, as stock does above about 4.5 kHz.
- The FM envelope and the mip choice move once per audio block (2.7 ms).
- A note whose FM envelope has decayed to 0 still costs the FM's CPU.
