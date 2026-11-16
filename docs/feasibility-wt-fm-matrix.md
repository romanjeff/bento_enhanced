# Feasibility: FM on Wavetable oscillators (a) and FM amounts in the mod matrix (b)

Labels: **M** = measured in the emulator, **S** = read from disassembly of stock 1.6.8 `~/bento/stock-1.6.8/bento1.bin` (capstone), **I** = inferred. I edited nothing in any repo. The scratch scripts are in `C:\Users\rjrom\AppData\Local\Temp\claude\9acdfe94ba00fd09\71a55988-76ea-4e33-b531-3ea1a26f5f35\scratchpad\feas\`: `da.py` (disassembler), `tab.py` (destination tables), `wtcost.py` (emulator cost of stock's WT render), `vt.py`, `refs.py`, `strs.py`.

## Verdicts
- **(a) FM on Wavetable oscillators: GO, with conditions.** Use our own through-zero FM table reader, used only while an FM amount or its ramp is non-zero. Stock's path stays untouched at FM = 0, so it is exact by construction. Estimated cost: about 2.5â€“3.5 KB DSP and 3 sprints plus a device check. The main risk is device CPU from SDRAM cache misses, which can't be measured offline.
- **Side finding that corrects plan-p3 4.5:** stock's WT render is not monolithic. Its pre-Level signal sits in the scratch buffer between two separate calls, so a Wavetable OSC1 can feed FM 1>2 by calling stock's own kernel. That is about 0.3 KB, not "several KB with its own exactness proof".
- **(b) FM amounts as mod matrix destinations: GO, by extending the Wavetable destination table.** No destination can be borrowed. Estimated cost: about 0.4 KB M4, about 0.8â€“1 KB DSP, 2 sprints. A routing saves as `<modsource dest="bento_subfm" â€¦>` on the oscillator cell. Stock reads that back as a routing with key 0, which does nothing.

---

## (a) Stock 1.6.8's wavetable path (S unless marked)

**The render, `WT_OSC_RENDER` 0x08081C40.** It runs in this order:
1. It asks the object at osc+0x18 for the table (vtable +0x20, key [osc+0x1C]). With no table it returns.
2. Pitch: detune, then PARAM(+0x70), then note octave (0x0804F680), then exp2 (0x0804F5E0). `rate = hz / C / div(+0x108)`, stored at +0x1E8.
3. Position: `usat8(PARAM(+0x30) * 255)` gives an **integer frame 0..255**, stored at +0x20.
4. Level: `0x0804F360(PARAM(+0xB0))`, then the smoother is set (0x08071A20). At level 0 and not moving, it returns.
5. **Kernel read `0x080A2000(osc+0x10C, table, frame, scratch, rate, n)` writes the raw signal into scratch.**
6. Smoother apply `0x08071820(osc+0xF8, scratch, 0, n)`, which is Level.
7. Mix `0x08052760(out, scratch)`.

Steps 6 and 7 are the same helpers `an_osc` already calls (S_SMOOTH_APPLY, S_MIX_INTO). So the pre-Level WT signal is readable in scratch after step 5.

**Table format and mip levels.** Samples are float32. There are 7 octave mips at fixed offsets:

| Mip (D) | Frame length | Offset |
|---|---|---|
| 1 | 2048 | 0 |
| 2 | 1024 | 0x80000 |
| 4 | 512 | 0xC0000 |
| 8 | 256 | 0xE0000 |
| 16 | 128 | 0xF0000 |
| 32 | 64 | 0xF8000 |
| 64 | 32 | 0xFC000 |

- Address = `table + off + frame*len + idx`. One table is 1 M samples (4 MB).
- **The mip is chosen once per note** (flag kernel+0xD8, set by the note-start reset 0x080A1FC0): the first D where rate/D < 1.25.
- M: note 60 plays D = 8 (len 256); note 96 and above play D = 64 (len 32).

**Interpolation.** Two-point linear, phase in Q4.28 fixed point (resampler 0x08050D00, unrolled Ã—4). Persistent state: frac +0xC0, idx +0xC4, current frame +0xC8, mip offset / D / len +0xCC / +0xD0 / +0xD4. Stock's phase is (idx + frac) / len.

**Anti-aliasing.** In the 2N 96 kHz domain a step below 1.25 table samples gives a top harmonic below 60 kHz. Its images fold above 36 kHz, and the decimating FIR 0x0807FCC0 removes them (I).
- The audio band stays clean up to a step of about 1.5. That leaves about +3.2 semitones of upward pitch modulation in the worst case, because the mip is not re-chosen during a note (I).
- Stock itself aliases at the top mip (D = 64, 16 harmonics) above about 4.5 kHz fundamental (I).

**Morphing.** There is no interpolation between frames: Position is an integer frame per block. A new frame takes effect **only at the cycle wrap**, with a 16-sample crossfade (0x080A2200: weights 1/16..15/16 over the guard). Kernel+0x00..+0xBF is a guard / crossfade scratch rebuilt before every use, so it is transient. Only +0xC0..+0xD8 persists.

**Cost of stock's WT render (M, `wtcost.py`, n = 256, sine table):**

| Note | Instructions per render |
|---|---|
| 24â€“48 | 6,717 |
| 60 | 7,021 |
| 72 | 7,307 |
| 96 | 8,705 |
| 120 | 13,948 |

That is about 26â€“28 instructions a sample, and the kernel itself is about 9â€“10 of those (I).

### Options for an FM'd WT carrier
| | Through-zero linear FM (same law as Analog: di = inc + IÂ·A + JÂ·B) | PM, DX-style (read at p + kÂ·m) |
|---|---|---|
| Knob behaviour | Same as the Analog oscillator's Sub FM / FM 1>2 (FD3 square law, kin = IÂ·inc_mod). A Type flip keeps a similar sound. | Different timbre for non-sine modulators |
| Modulator buffers | Uses A and B as they are (already in increment units) | Needs amplitude-unit buffers or a division |
| Sub Saw / Square / Rev Saw as modulator | Steps the frequency between two values, which is musical and bandlimited by the mip | A **phase jump** with no BLEP: wideband aliasing and clicks |
| DC in the modulator | Detunes (already solved by OD10's DC-free A) | No detune |
| Cost per sample | About the same | About the same |

**Recommendation: through-zero FM.**

Reader design:
- Free role only (stock has no WT sync slave).
- Linear interpolation, phase moving in either direction, wrap modulo len.
- Mip chosen **per block** from the peak step bound `(|inc| + IÂ·max|A| + JÂ·max|B|)Â·2048/D < 1.5`, with no extra pass. A mip change between blocks only drops harmonics that lie above 24 kHz after decimation (I).
- On a Position change, crossfade old â†’ new frame over that block (two reads only in that block).
- Clamp |di| as the Analog kernel does.
- Cost of per-block mip: it is slightly dull at high index during the low-frequency half of the swing. The alternative is a per-sample mip crossfade at about 2Ã— the reads (open question 2).

**Exactness at FM = 0.** Switch to stock's render (the A2 trampoline returns 0, or calls stock). The reader works on stock's own persistent fields, so there is nothing to emulate:
- It reads idx/frac/frame on entry.
- After the ramp down it writes the phase back at the **note's mip D**, which we save because we change +0xCC/D0/D4.
- It honours the mip-pending flag at note start.

Bit identity at FM = 0 is then trivial. The thing to test is continuity across the switch (phase error 0, no step). Not bit-equality of the reader against stock's kernel, which isn't needed.

**Where the WT oscillator's values live:**
- **M4: nothing new.** Every type-6 object already stores 0x18B / 0x132 (plan-p3 3.1, S). `want_row` returns early on `!me`, and stock's WT rows have slots 4..7 empty (S: `{0xC4,0x03,0xC9,0,â€¦}`). So it is about 3 lines: Sub FM in slot 5, FM 1>2 in slot 7.
- **M7:** `an_prop` drops these keys while the oscillator is Wavetable (S, analog.cpp:216â€“234). The 0x1F0-byte oscillator has no free word (fields end at +0x1ED, S). Options:
  - **(i, recommended)** A "WTFM" tag plus AnProps / msub / m12 in the transient guard (+0x1C0), saved and restored around stock's render call (16 B, a few instructions; S that stock rewrites the guard before every use).
  - (ii) A side table in SA spare (+0x4600..+0x6000, 6.5 KB).

**FM 1>2 from a Wavetable OSC1 (now cheap, S).** In `an_osc`:
1. Call stock's own steps 1â€“5 for OSC1. The pitch/rate path is already bit-equal to stock (588/588, M in plan-be440).
2. Write B = inc1Â·scratch.
3. Call smoother and mix.

About 0.3 KB, with bit identity testable on the existing `Mixed` rig. New risk (I): user wavetables can have a DC offset, and DC in B detunes OSC2 (the OD10 / OD15 hazard again). A per-frame DC correction needs a mean per frame. Open question 3.

**Code size (I).** Total about 2.5â€“3.5 KB:
- reader 0.9â€“1.4 KB
- WT path in `an_osc` plus handback/mip â‰ˆ 0.6 KB
- props save/restore â‰ˆ 0.2 KB
- WT OSC1 as B â‰ˆ 0.3 KB
- M4 â‰ˆ 0.2 KB (separate budget, about 16 KB free)

Against about 12.8 KB free plus about 1 KB from removing Bit Depth, with P4 drums needing 4â€“6 KB, about 4â€“7 KB would be left.

**CPU per voice (I).** FM'd WT render â‰ˆ stock's 6.7â€“7.3k + about 2.5â€“3.5k (reader at about 16â€“22 instructions a sample vs the kernel's 9â€“10) â‰ˆ 9.5â€“11k. Add A's fill if not shared (sine copy 4,398, M in Q5). A voice with both oscillators FM'd is about 23â€“27k, below Analog's full FM voice (30,753, M).

**Risks:**
1. **SDRAM cache misses (top risk, device only).** Stock reads sequentially (one miss per 8 samples). FM jumps around inside the frame. It is mitigated naturally because a high index selects a high mip (D â‰¥ 8 means a 1 KB frame, which fits the 16 KB L1). The bad case is a moderate index at a low carrier (D = 1, 8 KB frames, 10+ voices).
2. Top-mip aliasing at high carrier Ã— index (stock already aliases there).
3. Phase handback errors at mip changes. Needs golden plus continuity tests.
4. WT DC under FM 1>2 (above).

## (b) Mod matrix: how stock does it (S)

**Destination tables (M4 flash).** Each entry is 24 B: `{u8 type, u8 instance @+4, u16 key @+8, fmt @+0xC, name @+0x10, u8 row-id @+0x14}`.
- **The Wavetable track's table is 0x081A0D1C, 23 entries, count word 0x081A0D14.**
- Type 6 (oscillator), instances 0 and 1, have exactly three keys: **Pitch (3), Level (0xC4), Position (0xC9)**.
- The rest: type 8 the sub (3, 0xC4, PW 0xC5), type 10 filters (0xCE, 0x33), type 9 envelopes (0x1E, 0x1F, 0x21), type 11 LFOs (0x4C, 0x4B).
- Granular has its own 37-entry table at 0x081A0F44.

**Who reads the table:**
- The Mod Matrix row filler 0x0814DA70 (track type 18). It **hard-codes `movs r2,#0x17` and `add.w r7,r4,#0x228`**. The name is sprintf(entry fmt `'WT1: %s'` 0x0818EAB8, key title).
- The destination enumerator 0x08159358.
- DEST_NAME 0x08159A5C.
- 0x081346B8's caller at 0x08134790.
- The literal users are 0x081347E0, 0x0814DB8C, 0x0815941C, 0x08159C88. The count users are 0x081347DC, 0x08159414, 0x08159C84.
- The view's arrays hold about 60 rows (keys view+0x8AC50, handles +0x8ACCC, ids +0x8ADBC), so 26 rows fit.

**Routing storage (M4).** A routing is a **type-1 child node of the destination object**: `{+0xC amount, +0x10 source, +0x12 dest key, +0x14 slot}` (MX_ROUTE 0x08132204).

**Save format.** Writer 0x08130582, reader 0x08130CA8:
```xml
<modsource dest="KEY_TOKEN" src="lfo1" slot="0" amount="..."/>
```
It is written inside the oscillator's element. `dest` is the key's registry token (0x08168558). On load, `0x08168580(token)` scans the 0x1F2 registry entries and **returns 0 on a miss**.
- So stock 1.6.8, or a rollback to BE4.40, loads a `dest="bento_subfm"` routing as a node with key 0. Nothing routes key 0, so it is inert (I).
- Its re-save writes no `dest` attribute, leaving a dead `<modsource>` (I).
- On BE the token resolves to 0x18B through N2's registry entry (S for the token on cells; I for this path; test it).

**M7 side.**
- The Wavetable oscillator class (vtable 0x080ACBB4) **slot 9, 0x08081C20** clears the routed slots of Position, Pitch and Level (0x080A0880).
- **Slot 10, 0x08081B60** connects them: key 0xC4 â†’ +0xB0, 0xC9 â†’ +0x30, 3 â†’ +0x70, through PARAM_CONNECT 0x080A0920. **Any other key is `bx lr`, silently ignored.**
- A PARAM object (0x40 B) has slot 0 for the knob, **3 routed slots** at +0x08 / +0x10 / +0x18, and a fixed slot 4 at +0x20. PARAM_VALUE sums amount Ã— source+4 **once per block** and clamps.
- Routing arrives per voice for oscillator[index]. Test_analog showed slot 2 reached on Position (M, plan-be440).

### Routes
1. **Borrow a destination: not possible (S).** Every Wavetable-track destination means something on an Analog oscillator or voice:
   - Position is already taken by Shape (J4).
   - Sub PW is real when the sub is Square.
   - Sub Level is audible.

   After (a), Position also means something on an FM'd WT oscillator.
2. **Extend the table (recommended).**
   - **M4:** relocate the 23-entry table into our code area as 26 entries: OSC1 Sub FM `{6,0,0x18B}`, OSC2 Sub FM `{6,1,0x18B}`, OSC2 FM 1>2 `{6,1,0x132}`. Then repoint the 4 literals, set the count word to 26, and patch 0x0814DA70's `#0x17` to `#0x1A` and `#0x228` to `#0x270` (addw). Names come for free from the registry titles ("WT1: Sub FM", within the 16 B row name).
   - The row-id byte +0x14 has an unknown use. Pick unused ids (0x13..0x4B are taken); see the open questions.
   - **M7:** trampolines on slots 9 and 10 for keys 0x18B / 0x132; everything else goes to stock.
   - Storage per voice would be 3 keys Ã— 3 slots Ã— 8 B = 72 B. For about 16 roots Ã— 10 voices that is about 11.5 KB, too big for SA spare. Store **per root**: amount plus source as a voice-relative offset, or absolute if the source is global. That is about 1.2 KB (I: needs checking that per-voice sources sit at fixed voice offsets).
   - Per block: `v = clamp(knob + 1000Â·Î£ amountáµ¢Â·srcáµ¢, 0, 1000)`, the same rule as J4 (no slot 0, no clamp bending). It feeds the existing msub / m12 ramp. The FM gate also checks "has routes".
   - With none routed, today's path runs bit for bit.

**What block-rate FM depth sounds like (I).** PARAM_VALUE updates every 2N block: 256 frames at 96 kHz = 2.67 ms = 375 Hz. msub / m12 already ramp the index linearly across each block, so it is piecewise-linear with **no zipper**.
- LFO up to about 20â€“30 Hz: the error of linear interpolation is about âˆ’50 dB of the modulation depth, so it is indistinguishable from continuous.
- Envelopes: DX-style brightness envelopes work. An attack under 2.7 ms becomes a one-block ramp, which is click-free.
- Velocity â†’ depth: clean per-note brightness. The first block of a note ramps in (OD16).
- S&H sources: each step becomes a 2.7 ms glide.
- LFO above about 100 Hz: block sampling aliases the modulation, and above 187 Hz it folds. Audio-rate depth modulation is impossible, which is what the audio-rate routes are for.
- Amounts are held to 1/1000 of knob travel; the ramp hides that.
- The square law makes a symmetric LFO swing brighter at the top (open question 4).
- Note that stock's own Pitch modulation steps once per block with no ramp, so this is smoother than stock.

**Code (I).** M4 about 0.4 KB (table 26 Ã— 24 B plus patches). DSP about 0.8â€“1 KB (two trampolines, store, per-block sum, gate).

## Sprints (each with an offline exit and the full suite green)
**(a)**
- **W1:** M7 storage for WT values (save/restore with tag), `an_prop` for WT, M4 rows. Exit: WT with FM 0 is bit-identical to BE4.41 everywhere.
- **W2:** the TZ table reader, per-block mip, frame crossfade, phase handback, and the golden model in p3_golden.
- **W3:** integration: A and B for WT carriers, ramps, E3 / skip, black box, costs, plus WT OSC1 as the FM 1>2 source if Jeff wants it.
- Then a device check: CPU at many voices, low carrier with moderate index.

**(b)**
- **X1 (M4):** the table, rows and names. Tests: routing create / save / load / stock-load (key 0).
- **X2 (M7):** slots 9 and 10, per-root store, the sum, the gate and identity.

Doing (b) without (a) gives rows that do nothing on WT oscillators: either accept that or hide them dynamically (hook 0x0814DA70). Order: (b) is smaller and stands alone for Analog; (a) after P4's size is known.

## Open questions for Jeff
1. FM law on WT carriers: through-zero FM like Analog (recommended) or DX-style PM?
2. Mip under FM: per block (cheaper, slightly dull at high index) or a per-sample mip crossfade (about 2Ã— the reads)?
3. FM 1>2 with a **Wavetable OSC1** as modulator is now cheap (it calls stock's kernel). In scope? If yes, do we accept or correct wavetable DC, which detunes OSC2?
4. Mod scaling: modulate knob travel v (then the square law, I = 8(v/1000)Â²) or the index I directly?
5. Matrix rows: always show the 3 new rows (inert where they don't apply) or hide them per configuration?
6. Priority vs P4 drums for DSP space: (a) + (b) is about 3.5â€“4.5 KB.
7. Device check needed anyway: the Mod Matrix row names come from the `'WT1: %s'` format at 0x0818EAB8, which T0 left stock. Does BE4.41's matrix page show "WT1: â€¦" rather than "OSC1: â€¦"?
8. Use of the row-id byte (+0x14, 0x13..0x4B, unique per row) is not traced. Some id has to be chosen for the new rows. Should the id use be traced first (it may be a CC-learn or assignment id)?
