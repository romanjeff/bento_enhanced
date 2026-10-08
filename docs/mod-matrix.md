# Modulation matrix

All findings below are from `bento1.bin` (the STM32H747 audio chip) — see [`architecture.md`](architecture.md).

## The mod-source table

A 34-entry descriptor table, built by an init routine at `0x08162ffc`, writes `{id, name, display}` records into RAM at `0x30038468`. Full decode, cross-checked two independent ways (two separate research passes landed on the same table and the same IDs):

```
1  env1       2  env2       3  env3
4  lfo1       5  lfo2       6  lfo3       7  lfo4
8  seq        9  seq1      10  seq2      11  seq3      12  seq4
13 velocity
14 key        -> "Key"
15-24 mod1..mod10            (the mod matrix's own 10 output slots, usable as sources for chaining)
25 pitchbend
26 modwheel
27 keytrig    -> "KEY"       (confirmed DISTINCT from id 14 "key" despite similar display text)
28 midivol
29 midipan
30 macrox
31 macroy
32 aftertouch -> "Pressure"
33 pressure   -> "Pressure"  (a second, separate entry with the same display label as 32)
0x8000 midicc -> "MIDI CC"   (sentinel: negative/flagged id, reads extra mchan/ccnum params)
```

## What's confirmed vs. open

**Confirmed:** `key` (14) and `keytrig` (27) are genuinely separate mod sources, not a casing difference of the same thing. `midivol`/`midipan`/`midicc` are destinations (modulation targets), not sources, despite living in a string cluster near the source names — resolved by finding the `ModRouting` struct layout (`src`/`dest`/`amount`/`slot` fields, `src < 0` reserved for the MIDI-CC pseudo-source).

**Open, and informative about the limits of static analysis:** `aftertouch` (32) and `pressure` (33) both display as "Pressure" in the UI. Three independent static-RE approaches — a full-program scan for every place both literal values are used near each other, a scan for the array-index pattern a flat `value[srcId]` lookup would produce, and tracing the generic message-bus dispatch as far as static analysis reasonably goes — all came back as genuine negatives, not "didn't look hard enough." This was resolved far more cheaply by checking the real device instead: **"Pressure" appears only once in the on-device mod-source picker.** So there's one unified value, not two competing sources — most likely a merge of the pad's physical pressure sensor and incoming MIDI aftertouch (Bento's 1.5.x changelog has a bug-fix entry, *"MIDI Aftertouch did not work as a Pressure modulation source"*, confirming such a merge point exists). Which of the two internal IDs is the live one is a cosmetic detail at this point, not a design blocker.

**Lesson for future research on this binary:** when static analysis stalls on "which of two code paths is actually live," check whether the question has a five-second answer on the real hardware before spending more agent time on it.

## A genuinely useful side-finding: MIDI aftertouch receive already exists

While chasing the above, a clean, well-formed MIDI status-byte parser turned up at `0x0806f920` (542 bytes) — not a guess, a real decompiled function. It handles:

- **Channel Pressure (0xD0):** tagged internal event `0xb`, 1 payload byte.
- **Poly Key Pressure (0xA0–0xAF):** tagged internal event `0xc`, **2 payload bytes — note number and pressure, both preserved.** The receive path is genuinely polyphonic, not collapsed to a single value.

Byte-extraction helpers: `FUN_0809d700` (status nibble), `FUN_0809d5a0` (channel nibble), `FUN_0809d720(msg, i)` (bounds-checked data byte). Both event types route through a generic message-bus dispatcher (`FUN_0809ac60`, binary search over a handler table) and a generic send primitive (`FUN_08062e00`) — standard OOP message-passing, not a hand-rolled switch.

This is a solid template for any future work that needs to *emit* MIDI aftertouch (rather than receive it) — the byte-packing conventions and message shape are already established elsewhere in this firmware.
