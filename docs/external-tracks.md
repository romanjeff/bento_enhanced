# External tracks

All findings below are from `bento1.bin` (the STM32H747 audio chip) — see [`architecture.md`](architecture.md).

## What an External track is

External tracks route audio input through Bento's mixer while sending MIDI note/control data out to external gear — the bridge between Bento's sequencer and an external MIDI instrument. Confirmed internal identity: class name `extrack`, 4-character save-tag `TRKE`. Full track-type/tag registry, useful reference for other track-type work:

```
samtrack       -> TRKS    (Sample)
looptrack      -> TRLP    (Loop)
slicertrack    -> TRSL    (Slicer)
multisamtrack  -> TRMS    (Multisample)
wttrack        -> TRKW    (Wavetable)
grtrack        -> TRKG    (Granular)
grainchoptrack -> TRGC
extrack        -> TRKE    (External)
mastrack       -> TRKM
cliptrack      -> TRCP
```

## TrackType normalization

A jump table at `0x08143c88` (inside `0x08143c28`, reached from the error string `"GetCurrentSelectedTrackType unknown track type"` at `0x08184338`) maps 9 raw instrument-type values (14–22) to 8 normalized values (1–8), with one raw value (21) deliberately invalid. Which normalized value specifically means "External" wasn't pinned down — would need tracing each track class's constructor for its literal type-ID argument.

## The INST page, for an External track

Bento's physical UI has 9 page buttons (TRACKS, LAUNCH, SONG, MIXER, FX, PROJ, INST, SEQ, SCENE). **TRACKS** selects the active track; **INST** shows that track's deeper parameters, with content that depends on the selected track's type. For an External track, INST shows the view whose title string is `"External"` — this is that track type's specific INST content, not a separate unrelated page.

### MIDI-out field layout

The per-track MIDI-out parameters — `midioutport`, `midioutchan`, `midiouttype`, `midibend`, MIDI In Port/Chan, CC1/CC2 Port/Chan, `midinotemap` — are all written by **one hand-expanded field-initializer function**, `0x08065940`–`0x08065a7e`, which stores name pointers and constants directly to fixed struct byte offsets (roughly `0x1f0`–`0x3a0` and `0xce0`–`0xd00` on one struct, `0xc00`–`0xc20` on a second).

This is *not* a generic "register a parameter" loop that a new entry could just be appended to — it's a hand-written sequence of individual field initializations. Adding a new field (e.g. a pressure-output-mode toggle, or a Mod Wheel amount control) means: find a free struct offset, then add a matching initializer block in this same function following its existing pattern. Mechanical, well-bounded work once a free offset is confirmed — not a new subsystem to build.

### A resolved ambiguity

Early research found `midivol`/`midipan`/`midicc` in a string cluster near the mod-source names (`aftertouch`, `key`, etc.) and wasn't sure whether they belonged to the same table. They don't — they're the **mod-matrix destination list** (see [`mod-matrix.md`](mod-matrix.md)), a separate string cluster from the External page's own MIDI-out fields above.

## Per-pad pressure and the pad→note gap

Jeff's own reasoning drove this thread: Bento's 16 pads each have their
own physical pressure sensor, so true polyphonic aftertouch output
(independent pressure per simultaneously-held pad, not one merged
per-track value) should be achievable regardless of how the stock
firmware's own mod-matrix happens to use that data.

**Confirmed: the raw per-pad data genuinely is independent, all the way
through.** The inter-chip link (SPI1 + DMA2, 72-byte packets — cross-
confirmed against the UI/disk chip's own send side) delivers a direct,
unindirected 16-entry array (one `u16` per physical pad, no indirection),
refreshed every audio tick. Pad transitions become tagged events
(`0xac` press, `0xad` release, `0xae` value-change on every change) that
each explicitly carry the pad index, pushed into a generic, type-
agnostic broadcast dispatcher. **These events are never pre-collapsed on
the way through** — every `0xae` event still carries its own pad index
and fresh pressure value when it reaches its one subscriber.

**The real finding, and it reframes this feature's actual scope:**
tracing where pad-indexed events go next found that pad-to-note
bookkeeping is something **each track type builds for itself**, not a
shared mechanism External tracks could simply tap into. Slicer has its
own static pad→note table; Loop/Slice tracks maintain their own live
active-note list. The shared internal voice-allocator used by
synthesizing track types never sees pad index at all — only
`(track, note, velocity)`. External tracks don't do internal synthesis,
so they never touch that allocator either, and nothing else in the
firmware reads raw pad pressure except the hardware-scan function itself.
**External tracks almost certainly have no existing pad-to-note
bookkeeping today.** This isn't "not found yet" — every place such
bookkeeping would have to live, by analogy with every other track type,
demonstrably doesn't exist for this one.

**Practical implication:** building genuine per-note aftertouch output
for External tracks means adding pad-to-note bookkeeping ourselves,
following the pattern Slicer/Loop-Slice already establish (not a generic
mechanism to extend, but not a novel one either — a known, working
pattern to replicate for a track type that doesn't have it yet).

One open sub-question, blocked by a specific, nameable Ghidra tooling
limit rather than a dead end: whether the single subsystem that receives
every pad event ultimately stores pressure per-pad or collapses it
somewhere downstream — two of its vtable slots were merged into one
oversized function listing by Ghidra's own analysis, and separating them
needs a function-boundary split (an analysis-only operation, not
firmware modification).

## Open questions

- No existing code was found anywhere in the firmware that builds a Channel Pressure (0xD0) or Polyphonic Key Pressure (0xA0) MIDI *output* message — only the *receive* side exists so far (see [`mod-matrix.md`](mod-matrix.md)). Building an output encoder is new work, though the receive-side byte-packing conventions are a solid template.
- Whether the pad-event subsystem's stored pressure is per-pad or collapsed (see above) — the one remaining concrete gap for this feature.
- Whether individual tracks' audio is addressable before the final mix (relevant to sidechain-style features elsewhere in this project) is covered in [`compressor.md`](compressor.md), not here.
