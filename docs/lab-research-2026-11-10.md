# Lab research update (2026-11-10): cross-check against our 1.6.8 port

Read 2026-11-14. Source: bento-community-lab 960e4b1 (`research/`, commit e67ec39 "Research update").
bento-playground itself is unchanged (still 9fc7787). Their pages are the Playground team's 1.6.1 → 1.6.8
port notes: `firmware-1.6.8/CHANGES.md`, `HOOK_MAP.md` (196 sites), `porting/METHOD.md` and tools
(`fwmatch.py`, `hooksig.py`), plus 1.6.1 `MEMORY_MAP.md` / `NATIVE_INTERNALS.md`. Their evidence for 1.6.8 is
STATIC/EMU only. They have not run it on hardware.

Bottom line: **an independent second method agrees with our address map everywhere it overlaps. Nothing in
our tables changes.** Two device-test items and one headroom fact are new.

## What was checked (M = measured here)

| Check | Result |
|---|---|
| Address pairs (HOOK_MAP + CHANGES tables, 206 pairs) vs `stock_168.py` `was` → value (288 flash pairs) | **70 shared 1.6.1 addresses, 70 agree.** No entry of ours within ±0x20 of one of theirs has a different shift (M) |
| Registry keys: < 0x139 same, In 1–3 merged at 0x139, 0x13C..0x1D8 −2, ≥ 0x1D9 −1 | Same as ours (KEY_MUTE 0x13D→0x13B, KEY_MX_FIRST 0x171→0x16F, REG_KEYS 499→498) |
| Native keys new in 1.6.8: 0x19A, 0x1D2, 0x1D3 | Clear of our claims (Analog 0x1A7/0x1AB/0x1B7/0x1C0, drums 0x1AE..0x1B4). test_keys already knows 0x19A (Freeze) |
| UI/action ids: ≤ 0x1011 same, 0x1013..0x103B +2, ≥ 0x103C +3, top 0x104A | Same as ours (ACT_WAVETABLE 0x102A→0x102C, ACT_INST_FIRST 0x1023→0x1025; our 0x10F0..0x10F4 above stock) |
| Model event codes: 0x101..0x106 +1, 0x10C→0x10D | Same as ours (EV_MIXER_PAD_MODE 0x10C→0x10D); our 0xCF and 0xFE unaffected |
| Struct offsets: pad module +0x2C8→+0x308, pad lights +0x19C4→+0x19D0, root armed +0x117C→+0x1194 | Same as ours (ROUTER_SEL_TRACK, MODEL_PAD_LEDS, HEADER_ARMED) |
| Their "object offset 0x36C4 → 0x382C" vs our MODEL_DIRTY 0x36C4 → 0x3FF4 | **Not a conflict** (M). Theirs is a movw-indexed field in a UI view (1.6.1 0x0815F870 → 1.6.8 0x0816162C). Ours is the model's byte at +0x3000+0x6C4 → +0x3000+0xFF4: 15 strb/ldrb uses in each image |
| Undo during recording discards the take (new 0x08138548) | Already traced by us: NOTE_UPDATE's 4th caller 0x0813863E is in that function; curve_update filters on lr (facts.py NOTE_UPDATE) |
| Keystone mis-encodes conditional T3 branches to absolute targets | Already guarded in build.py (the branch re-check after every assembly) |

Their synth-page "Mod-mode view" vtable slots (1.6.1 0x0819E894 / 0x0819E8C4) are our A10 / A9
(1.6.8 0x081A175C / 0x081A178C): the same class. Playground hooked the same slots, which supports our choice.

## New for us

1. **Device test: Mod Seq Slide.** 1.6.8 adds a native "Mod Seq Slide" on knob 7 in Granular/Wavetable Mod mode
   (their reading of the code, I). A9 takes event 0xCF only for SYNTH_KEYS, so knob 7 in Mod mode should pass
   to stock. Add to the Analog device checklist: on an Analog Wavetable track in Mod mode, knob 7 still slides.
2. **Device test: Launch pad lights.** 1.6.8's Launch screen lights pads for playing clips, and Left/Right on the bank screen
   now step through all 16 pads (I). Check the hold shortcuts and anything of ours that writes MODEL_PAD_LEDS on those pages.
3. **Flash ceiling evidence (HW, theirs, 1.6.1, one unit).** Alpha 1.0 ends at 0x081DFE98 and works. An image
   reaching 0x081F8380 failed with "couldn't verify" / error 19. Our `PROVEN_MAX_SIZE` 0x18D334 is our own
   device proof. **Keep it**: M4 room (~32 KB) isn't the bottleneck yet. If we ever need room, raise it only after
   flashing an image past 0x18D334 on our unit. Hard limit stays `FORBIDDEN_FROM` 0x081E0000.
4. **M4 MPU (EMU + HW, 1.6.1).** M4 MPU_CTRL = 1 without PRIVDEFENA: region 1 is 0x08100000 / 1 MB and is the
   only executable one. Widening it to 0x08000000 / 2 MB lets M4 run code from bank 1 (their HW builds boot).
   Bank 1's zero gap already holds our M7 code and DSP, so this is a last-resort room option only. 1.6.8 sites:
   0x0811512A (base), 0x08115130 (size).
5. **Sampler voice internals (1.6.1 STATIC, M7 code unchanged at +0xB00).** These are relevant to the mono re-struck drum pads:
   a retrigger never continues the same voice; release is a 256-sample ramp to ~6 % then a one-sample cut;
   level follows velocity only through the native velocity route. The voice pool is 16 (global
   0x08072D40), the default polyphony is 4, and polyphony 1 takes the mono path 0x08094D00.
6. **Stock crash record in RTC BKP registers** (1.6.1 STATIC). This could be a black-box cross-check: BKP4/5 MMFAR/CFSR,
   BKP6..13 stacked frame, BKP24 crash count, BKP31 pc. Never write BKP0..3 (BKP0 = 4 boots disk mode).
   1.6.8 addresses not checked.

## Not adopted

- `fwmatch.py` / `hooksig.py`: our `tools/addrmap` already does this, and the two methods agree. Worth a run as a
  second opinion at the next firmware release. Their caveat applies to us too: the sites file contains stock
  bytes, so it must not be published.
- Their HOOK_MAP sites for features we don't have (chord, Euclid, mixer replacement, palette): no action.
