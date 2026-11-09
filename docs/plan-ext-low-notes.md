# External tracks: every MIDI note 0..127 from pads and steps (plan, 2026-11-08, rev 2)

Requirement (Jeff): an External (MIDI) track must be able to send every MIDI note 0..127, from pads (live)
and from steps (sequenced, including pad hits recorded into steps and played back). A drum-machine map is
not the requirement. The motivating case, finger-drumming an Elektron Syntakt on its auto channel (12
tracks on notes 0..11, C-2..B-2 in Elektron naming), is covered once 0..127 works. 1.6.8 only (BE4.x).
The 1.6.1 build must stay byte-identical.

Evidence: workflow rc21 round 2. Scratch `rc21/ui/r2/` (M4 + router: `chain.py` end-to-end pad press to
MIDI bytes, `m7range.py`, `m4oct.py`, `m4midi.py`, `regnote.py`, `reach.py`, `q11.py`) and `rc21/SEQ/r2/`
(M7: `exp22.py`, `exp22n.py`, `pads22.py`, `xml22.py`), plus `rc21/design/sentinels.py` (the five
sentinel sites below, disassembled from the stock 1.6.8 image). Runs used real stock 1.6.8 code in the
emulator, and the SEQ runs also used BE4.10 (`5785b05e...`).
**M** = measured (run in the emulator), **R** = read in the disassembly but not run, **I** = inferred.
Nothing below has been checked on the device.

Rev 2 corrects three claims of rev 1: External's pad mode is not proven to be 3 on 1.6.8, the pad Note
On/Off call path is not External-only, and Note Mod wraps rather than clamps at the edges. All are
covered below.

**[Review rc21/adv, 2026-11-08] Corrections inline, each marked.**
1. External is track type **0x16**. 0x15 is the master track. With that fixed, External's pad mode is 3 (R),
   and stock's External sync clears the linear flag, so F is not needed (R).
2. The A0/A1 question can't be settled by a track-sync scan. The drum map has a live drum-mode input, so A1
   is the safer default.
3. A1's "+0x10 fallback" is risky.
4. The Note Mod helper needs care on non-MIDI drum tracks.

Everything else re-checked (the five sites, the M4 drop, the Step Note clamp, the router reach, the
recording raw note, the hooks) holds. Evidence: `scratchpad/rc21/adv/` (`emu5.py`, `scale2.py`, `reg.py`,
`tbh.py`, `xref.py`).

## Short answer (push-back first)
**It already works on stock 1.6.8 except for one note: 0.**
- M, end to end on stock code: 176 pad presses (Chromatic, Root C, Pad Row Offset -5..+5) through the M7
  pad router, PAD_NOTE_ON and the M4 MIDI task produced 127 distinct MIDI notes, 1..127. Note 0 was the
  only one missing. It went out as 44.
- Steps and recordings: notes 1..127 reach MSG_SEND unchanged, and the M4 sends them unchanged (M).
- The M4 MIDI task sends note 0 correctly when it gets one (M: `90 00 64` / `80 00 64`). It never gets
  one, because all five M7 MIDI note senders read note 0 as "use the pad's own drum note" and swap it for
  36..52 first.
- The top end needs no work (see "Top end" below).

So the core fix is **five 2-byte patches on the M7** (or five small trampolines if internal tracks
turn out to send MIDI too), plus making two of our own hooks (retrigger, Note Mod) follow the same rule.
Everything else is optional: typing a note-0 step by hand (option B), a guard against a possible stale
linear-mode flag (option F), and per-track pad layouts (C). A Syntakt preset or a per-pad note map is
not needed.

Two caveats on "covered implicitly":
- **Scale and Root are global** (one value for all tracks in the router). 0..127 from the pads needs
  Chromatic with Root A, A#, B or C (M: Root D starts at 2, Root G# at 8). A project that uses another
  scale or root on its melodic tracks can't reach every note from External pads at the same time. Steps
  are not affected.
- **Notes are spread over 11 octave positions.** At any one Pad Row Offset the pads show 16 notes. That
  is how the stock UI works, not a limit of the MIDI path.

## What stock 1.6.8 does today

### How a pad press becomes a note (M7, not the M4)
- The pads are scanned on the M7, which posts the press (event 0xAC, note field 0) straight to the pad
  router (`0x0808DEBA`) (R). The M4 gets a copy only (case 0xAC at `0x08141060`, then `0x08134A28`), turns
  it into UI events 0xB0/0xB1, and sends the router its settings: Pad Row Offset (key 0x176), pad mode
  (0xC0), Scale/Root (0x8D/0x8E) and the linear flag (0x175). The only note-carrying M4->M7 messages
  (sender `0x0810BAA4`) are sample previews (`0x0813A15A`, note 60) (R).
- External has **no per-pad note map**. Pad Note (key 0x048) is a sampler setting with "Default" at
  index 0, with no External use found (R).
- Router, press handler `0x080851C0`, melodic mode at `0x08085340`: `pos = pad<=7 ? pad+8 : pad-8`
  (pad index 8 is the lowest note). Scale path `0x0809E740(scale, pos)`, clamped 0..127 at `0x080856DE`
  (`movs r1,#0x7f`). Linear path (flag `+0x4B4[trk]` set by key 0x175, handler `0x080872AC`):
  `PAD_NOTE_CLAMP` 0..163 (`0x080A25E0`, `cmp #0xa3`). The note goes to `+0x34[pad]` and is posted with
  **pad field 0**. The release and pressure paths reuse `+0x34[pad]`.
- An incoming-note override field (ev+0x14) also uses 0 as "work it out" (`0x08085342`) (R, not run, and
  not known what drives it: possibly MIDI in).

**[Correction, review rc21/adv] External's pad mode is 3 (melodic) on 1.6.8 (R).** Rev 2 used the wrong
type id. The M4 class table (`0x0812FDF0..0x0812FE94`: {id, name, tag}) gives `extrack`/`TRKE` = **0x16**.
0x15 is `mastrack`/`TRKM`, the master track; it is the "deliberately invalid" raw value 21 of
`external-tracks.md`. In the pad-mode switch (`0x0813ACE6`: type - 0xE, tbh `0x0813ACF4`), 0x16 goes to
`0x0813AFBE`, which sends {0xC0, 3}. 0x15 goes to `0x0813AFD0`, which sends {0xC0, 0}. The router stores
the 0xC0 value in `+0x4D0` (jump table `0x08086564` -> `0x0808707C`, `strb` at `0x0808709A`), and mode 3 is
the melodic path `0x08085340`. 1.6.1 is the same: 0x16 goes to `0x08139882` (mode 3) and 0x15 to
`0x08139894` (mode 0). That matches the 1.6.1 device logs (48, 53, 67, 68), so the contradiction is gone.
The N0 device check is now only a confirmation. (Rev 2 text, superseded: "the M4 switch sends pad mode 0
for External (type 0x15, `0x0813AFD0`) ... not proven for 1.6.8".)

### Pad Row Offset (M4 `0x081433B8`, M)
Button 1 is up and button 8 is down. For External the range check (`0x081434F4`) allows -5..+5 and sends
{0x176, value, track}. At +5, up sends nothing, and at -5, down sends nothing (M). Slicer and grain chop
use a different range (minimum 0, top from the slice count).

| Pad Row Offset (Chromatic, Root C) | Pads 0..7 | Pads 8..15 |
|---|---|---|
| -5 | 8..15 | 0..7 (pad 8 = "0", sends 44) |
| -4 | 20..27 | 12..19 |
| 0 (default) | 68..75 | 60..67 |
| +5 | 127 on every pad (capped) | 120..127 |

Offsets -5..+5 together reach every note 0..127 (M). For the Syntakt, offset -5 puts tracks 1..8 on pads
8..15 and tracks 9..12 on pads 0..3. The physical pad order (which row is index 8..15) is assumed.

### Note 0 is replaced at five M7 sites (M on stock and BE4.10)
Each test sits right after the track's MIDI-out check (`ldrb [track+0x86D1]`: MIDI out = channel != 0,
written only at `0x08062B7A`). Mapping: pad 0..7 -> pad+0x2C (44..51), pad 8..15 -> pad+0x1C (36..43),
anything else -> 0x34 (52). Only the MIDI copy changes. The internal bus copy keeps 0.

| Path | Function | Sentinel test (bytes) | Fall-through (note 0 kept) | Our msg_send hook |
|---|---|---|---|---|
| sequencer Note On (note_route) | `0x0805FC80` | `0x0805FC9E` `beq 0x0805FD60` (`5fd0`) after `cmp r1,#0` | `mov sl,r1` | POLYCAP_SEQ_ON_SITE `0x0805FCCC` |
| sequencer Note Off | `0x080601A0` | `0x080601B8` `beq 0x08060260` (`52d0`) after `cmp r1,#0` | `mov r8,r1` | POLYCAP_SEQ_OFF_SITE `0x080601E4` |
| pad Note On | `0x080602A0` | `0x080602C6` `cbz r5,0x08060300` (`ddb1`) | r5 unchanged | POLYCAP_PAD_ON_SITE `0x080602F0` |
| pad Note Off | `0x080603E0` | `0x08060402` `cbz r5,0x08060440` (`edb1`) | r5 unchanged | POLYCAP_PAD_OFF_SITE `0x08060428` |
| stop / all notes off | `0x08061D00` | `0x08061DF0` `beq 0x08061D40` (`a6d0`) after `cmp.w sb,#0` | `mov sl,sb` | POLYCAP_ALL_OFF_SITE `0x08061D86` |

Each mapping block only writes the note register and branches back to the fall-through's next
instruction (R, `sentinels.py`). So "never map" is exactly "the branch is never taken".

M results: pad On/Off with note 0 send 44 (pad 0), 36 (pad 8), 52 (pad 16), and notes 1, 11, 12, 60, 126,
127, 128, 163 unchanged. A whole SEQ_PLAYER step with note 0 sends +44@10 / -44@110 (pad 9: 37). On and
Off always match, so nothing hangs. But a note-0 step and a real 44 count as one voice: overlapping, the
poly cap sends Off 44 at 74, then On 44.

**The M7 cannot tell External from internal here (R).** One track object type (0x8798 bytes) serves all
eight tracks: one allocation site (`0x08058A78` -> constructor `0x08062FA0`), and the internal-sound flag
+0x86D2 is set to 1 in the constructor and never cleared (`0x080631B0`). The pad Note On/Off path is
reached by every track type. The 36..52 map looks like a GM drum map for internal kit tracks that send
MIDI. **Whether the UI ever gives an internal track a MIDI channel (+0x86D1 = 1) is not settled.** It
decides which fix applies (option A0 vs A1 below).

**[Added, review rc21/adv] Evidence that the map is live for drum-mode tracks (R, leans toward A1).**
- Melodic mode (3) posts the pad press to the track with pad field 0 (`0x0808537A`; the event init
  `0x080A10C0` zeroes it). So on a live External press the map only ever turns 0 into 44.
- Drum mode (1, `0x08085220`) posts note = override (normally 0). When the track key `+0x32C[idx]` is set,
  the router also sends a copy to the track with **the real pad index** in ev+0x14 (`strh r4` at
  `0x080855E0`). That is exactly the input the 36..51 map needs: pad 8 -> 36, the MPC-style bottom row. The
  same happens for recorded drum steps (note 0 + pad p) through note_route.
- MIDI OutCh is registry key 0x10 (`midioutchan`, 17 entries, index 0 = "None"). The M4 setting forwarder
  sends 0x0F/0x10 to the M7 by key, for any track (`0x08132DD2 -> 0x08132DDE..F2`). A second key, 0x1C7
  ("MIDI OutCh", `mrecmidich`, 1..16), is forwarded as key 0x10 too (`0x08132F28..4C`).
- So rev 2's N0 method (a reachability scan from the track sync's type cases) **cannot settle A0/A1**. The
  channel comes from the settings editor and project load, not from the sync. Settle it on the device: does
  a Sample/Loop track's settings offer MIDI OutCh? Or find which track pages list key 0x10.
- Also, `+0x86D1` is written by the constructor too (`strh r4=1` at `+0x86D0`, `0x080631AC`: +0x86D0 = 1,
  +0x86D1 = 0), not only at `0x08062B7A`.

### M4 MIDI-out task `0x08105214` (M, every note 0..170, On and Off)
- Notes 0..127 go out unchanged. Note 0: `90 00 64`, `80 00 64`.
- Notes 128..170 are dropped, On and Off alike (guard `0x08105412`), so a dropped note can't hang.
- Velocity is sent `& 0x7F`. Velocity 0 goes out as `90 nn 00`, and velocity 128 as `90 nn 00` too.
  Receivers read both as Note Off. None of our hooks should ever produce 128 (L1 test).

### Note edit fields (M4)
- **Step Note (key 0x183):** the edit clamps to 1..127 (`0x0815A996..0x0815A9A0`). M: 0+0 -> 1, 0+1 -> 1,
  12-12 -> 1, 127+12 -> 127. **A recorded note-0 step whose Note is then edited becomes 1.**
- Labels: the Step Note list is the shared 128-entry note list one entry in (`0x081A2D74` =
  `0x081A2D70` + 4). The piano roll's `note - 1` index (`0x0815BBDC`) stays in bounds, and a pitch-0 step
  reads "None". The other label path (`0x0815BC9C`) also gives "None". Stock has no "C-1 (0)" label (M for
  the list contents. The screen drawing itself was not run).
- Root Note 0x034, Low/High 0x07A/0x07B, Start/End 0x1C9/0x1CA, duck Note 0x1EC, Pad Note 0x048: index 0
  is "None"/"Default", so the lowest real value is 1 (M). None of these is on the External send path.
- Not traced: the piano roll's lowest insertable row, and the default note of a newly tapped step.

### Recording and project.xml
- With track +0x30 set, pad Note On builds the record message from the **raw** note: 0 stays 0, pad 0
  (M). The M4 recording handler `0x08138B08` copies it word for word into the step
  (`0x08138D10..26`) with no clamp (R).
- project.xml writer `0x081301E0` / reader `0x08130A10`: pitch 0, 1, 11, 12, 60, 126, 127, 128, 163, 255
  round-trip unchanged, and so does pad 5/9 (M).
- **So today a recorded note-0 step saves as `pitch="0"` and plays back as 44.**
- be_step_v1 (+0x14) is independent of the note (M: `b6004000` round-trips on pitch 0, 12, 127 and with
  pad 9. Stock's reader zeroes it, as expected).

### Top end (M)
- Pads: the scale path caps at 127 (`0x080856DE`), so at +5 several pads all send 127. No scale, root or
  offset produced a note above 127 (all 23 scales x 12 roots).
- Linear mode (flag 0x175 = 1, which stock sets for slicer and grain chop) is the only place the 0..163
  clamp applies, and it tops out at **91** (+5 gives 76..91). It loses 92..127.
- Steps: the UI stops at 127. Only a hand-edited project.xml pitch of 128..255 goes above, and it
  round-trips, reaches MSG_SEND, and the M4 drops both On and Off.

**Recommendation: keep the drop, don't cap.** A capped 128..163 would share On/Off pairing with a real
127 and cut it off: the same collision as 0/44, moved to the top. Dropping is symmetric and practically
unreachable on External. Our hooks already follow it (BE_NOTES_ABOVE_127: Note Mod, retrig and curves
skip notes above 127). No work needed.

**[Correction, review rc21/adv] No stale linear flag on External (R).** The router keeps 0x175 per track
slot, and only the 0x175 handler changes it (`0x080872AC`: track = ev+0x10 <= 7, flag = ev+0x14 != 0). The
track sync's type switch (`0x0813E510`, tbh `0x0813E51E`) sends External (0x16) to **`0x0813F01E`**, not
`0x0813F0F8`. `0x0813F0F8` is the master track (0x15) case. The External case ends with
{0x175, track [sp+0x10], flag 0} at `0x0813F0E8..0x0813F0F2`, so it clears the flag. The slicer-family case
sends flag 1 (`0x0813E924..36`). An External track in a former slicer slot gets 0..127 again, provided the
sync runs on a type change, which is its job. **Option F is not needed.** N0's slicer -> External device check
stays as a cheap confirmation. (Rev 2 text, superseded: "never sends 0x175 for External ... External case at
`0x0813F0F8`".)

### Our hooks with note 0 (BE4.10, M)
- **Poly cap (h_note):** runs after the mapping and sees 44 (above). Correct once 0 is literal.
- **Retrigger:** `m7_retrig.c` `midi_note()` sends `note & 0x7F` and bypasses the mapping. A 2-hit
  ratchet on a note-0 step sends +44, then +0/-0 for the ratchet hits. **An existing bug** that the core
  fix removes (stock's hit becomes 0 too). Notes 1..127 are correct, and notes above 127 get no ratchet
  hits (by design).
- **Note Mod (`m7_mod.c`):**
  - `nm_drum()` (`return !note`) moves a note 0 as a pad: 0+2 -> 45.
  - A melodic shift that lands exactly on 0 is replaced by stock: note 4 shifted down 2 goes out as **44**.
  - `scale_shift()` wraps by an octave at both ends: 126+2 -> 117, 127+2 -> 119, 1-2 -> 9 (rev 1 called
    this a clamp: wrong).
  - The `x < 0 ? 0` clamp in the drum branch is dead code (drum means note == 0).
  - Notes above 127 pass unshifted.
- **Curves / poly AT:** a note-0 step starts its curve players on note 0 (2 starts) while 44 sounds, so AT
  goes to a note that isn't playing. Correct once 0 is literal.
- **be_step_v1:** unaffected (above).

## Root causes
1. **Note 0 is the "pad's own drum note" sentinel at five M7 MIDI sites.** On a MIDI-out track, note 0
   with pad 0 always becomes 44. This is the only thing between the stock UI and full 0..127 output.
2. **The UI reserves 0 as "None/Default"** in every note field. That only matters for typing a note-0 step
   by hand or editing a recorded one. Pads reach 0, and recording stores 0.
3. Side issues, not blockers: global Scale/Root (convenience), and the possible stale linear flag (would
   cap External at 91).

## Design options

### A. Literal note 0 on the MIDI path (the core fix, M7)
Make note 0 go out as 0 at the five sentinel sites. Two ways, decided by one fact from N0: **can a
non-External track have MIDI out (+0x86D1 = 1)?**

**A0: never map (if no internal track sends MIDI).** Replace each sentinel branch with a 16-bit `nop`
(`00bf`): five 2-byte patches, no code, no trampolines. The mapping only runs on MIDI-out tracks (it sits
behind the +0x86D1 check). If only External has MIDI out, the mapping is dead for everything else, and
removing it is exactly right.
- Cost: well under a sprint. Five table entries in `stock_168.py` with their stock bytes.
- Risk: very low. The fall-through is stock's own non-zero path, and On, Off and all-off change together,
  so no hangs.
- If A0 were used and internal kit tracks *can* send MIDI, their "pad sound" hits would send 0 instead
  of 36..52. That is why N0 has to settle the question first.

**A1: map only when the note has an internal sound (if internal tracks do send MIDI).** Keep stock's map
when the track has a real bus key for that pad (per-pad key `+0x86DC+4*pad` != 0xFFFFFF00, or the `+0x10`
fallback that note_route uses), and send 0 literally otherwise. External has neither (known from v3.71:
External's table[0] is empty, and bus_post drops a note with no key). Each site becomes a `b.w` to a
trampoline that replays the covered instruction(s) and calls one C helper `be_map_note0(track, pad)`.
- Cost: about 1 sprint (5 trampolines, a ~30-line helper, tests).
- Risk: low-medium. The helper reads track fields that the stop path and note_route already read. The
  edge: an internal track whose pad has no sound would send 0 (a silent pad: harmless).
- Note Mod's drum branch already uses the same kind of test (`!+0x86D1 && key == 0xFFFFFF00`), so the
  helper is shared, not new logic.
- **Test-rig caveat (M):** `Player(ext=True)` gives pad 0 a key (F4, so the bus copy shows ON 0). An A1
  test must clear it, and N0 must confirm what a real External track's +0x86DC/+0x10 look like.
- **[Added, review rc21/adv] Predicate risk:** if a real External track's `+0x10` holds a real key (it is the
  "inst key" that note_route bus-posts to, since +0x86D2 is always 1), the "+0x10 fallback" half of the
  predicate says "map" for External and A1 fixes nothing. The map is only meaningful for pad <= 15 with a
  per-pad key (drum mode). Prefer **map iff pad <= 15 and `+0x86DC+4*pad` != 0xFFFFFF00**, and drop the
  +0x10 fallback unless N0 shows External's +0x10 is 0xFFFFFF00.
- **[Added, review rc21/adv] Note Mod is not "the same test":** `m7_mod.c:434` runs only when MIDI out is
  **off**. The new `nm_drum()` must stay true for note 0 on non-MIDI internal tracks (drum hits still move
  by pad). Under A0, "false on MIDI-out tracks" changes Note Mod for a drum track with MIDI out (if one
  exists): its note 0 would be shifted melodically, which also changes the internal bus note.

**Rejected: patching inside h_note (rev 1's "A2").** At seq On (`0x0805FCCC`) the raw note survives in
`sb`, but at pad On (`0x080602F0`) r5 already holds the mapped note and the event pointer is gone (M7
read). Pad On can't be fixed there. It would also tie the feature to poly_cap.

**Rejected: an M4-published "this is External" flag** (`AT_DEST`, `m4_step.c` `at_publish`): only
republished on load or AT edits, so it goes stale on a track type or channel change. It is indexed via
`+0x86D4` and depends on poly_at.

**Our hooks under A (A0 or A1):**
- Poly cap: sees 0, counts it as its own voice. This also fixes the 0/44 collision.
- Retrigger: correct as it is, because `midi_note()` sending the raw 0 now matches stock's hit. Under A1
  it must call the same helper (an internal pad-sound ratchet must map like stock's hit). Do this under A0
  too, as a one-line call, so A0 -> A1 needs no retrig change.
- Note Mod: `nm_drum()` becomes "note 0 and the helper says map" (A0: always false on MIDI-out tracks).
  Then a note 0 on External moves by pitch, and a melodic shift that lands on 0 sends 0. Keep the octave
  wrap at both ends: it keeps the note in the scale, and clamping would pile notes on 0 and 127. Delete
  or fix the dead `x < 0` clamp.
- Curves / poly AT: correct as they are (their note 0 now matches the sounding note).
- be_step_v1 and notes above 127: no interaction.
- 1.6.1: these sites are identical on 1.6.1 but must not change there. Table entries go in `stock_168.py`
  only, and the C changes sit behind the group's omit define (`-DBE_OMIT_NOTE0` when the group is
  omitted, like `analog` / `-DBE_OMIT_SYNTH`), so the 1.6.1 image stays byte-identical.

**project.xml:** nothing new is stored. `pitch="0"` is already written and read. Opened on stock 1.6.8 or
on v3.72 (1.6.1 base, same sentinel code), such a step loads fine and plays as 44: wrong note, but
graceful. Projects made on stock open unchanged. Their note-0 steps change from 44 to 0, which is the
point of the fix. If a project relied on note-0 steps playing 44, that is the one behaviour change.

### B. Step Note field down to 0 (M4)
- Edit clamp min 1 -> 0 (`0x0815A996..0x0815A9A0`). Show "C-1 (0)" instead of "None" for 0 (label paths
  `0x0815BBDC` and `0x0815BC9C`): one extra string in our M4 append, with a branch for note 0 at each
  label path. The stock list can't grow in place, and the piano roll's index stays in bounds today.
  Find the piano roll's lowest insertable row and the new-step default (not traced) and lower the row
  limit if it stops at 1.
- Need: medium. Recording already gets 0 into a step, but editing that step turns it into 1, and it reads
  "None". The requirement says "from steps", so I'd count a step you can't type at 0 as a gap.
- Cost: about half a sprint. Risk: medium (step-edit and piano-roll code, our riskiest area historically).
- Only useful together with A. Without A, a note-0 step plays 44.
- project.xml: nothing new (pitch 0 is already valid).

### F. Clear the linear flag on External (M4, only if N0 reproduces it)
**[Correction, review rc21/adv] Not needed:** stock's External sync case already sends {0x175, track, 0}
(`0x0813F0E8..F2`, see "Top end"). Kept below for the record only.
In the M4 track sync's External case (`0x0813F0F8`), also send {0x175, 0, track}, the message stock
already sends for some other types. Cost: small, one M4 site. Risk: low. It restores 92..127 on an
External track that inherited a slicer slot.

### C. Per-track pad layout for External (deferred)
A per-track "Chromatic from note N" setting that ignores the global Scale/Root on External (router hook at
the 0x176 base write `0x08087286`, setting stored in our sidecar). It removes the global-scale caveat.
Cost: about 1 sprint (M7 site + M4 UI + sidecar). Not needed for 0..127. Build it only if the global Scale
gets in the way in real projects.

### Not proposed
- **Per-pad note map (any pad -> any note):** 2+ sprints, a new 16-cell editor. 0..127 is already
  reachable without it, and a mapped 0 would still need A.
- **Syntakt/Elektron preset:** with A, it is three stock settings (Chromatic, Root C, offset -5), saved
  in a template project.
- **Capping notes above 127:** see "Top end".

## Recommendation
1. **Build A, choosing A0 or A1 by N0's answer.** I expect A0 (the internal tracks probably have no MIDI
   channel, I). Either way, the retrig and Note Mod changes go in through the shared helper.
   **[Correction, review rc21/adv]** The evidence leans the other way. The map has a live drum-mode input
   (`0x080855E0`), and MIDI OutCh is generic key 0x10 with a "None" entry. Default to A1 with the per-pad-key
   predicate unless N0 shows that no internal track can set MIDI OutCh.
2. **Build B after A** (one more half sprint), because the requirement includes steps and a recorded 0
   should survive an edit. If Jeff only plays and records from the pads, B can wait.
3. **F only if N0 shows the stale flag on the device.** **[Correction, review rc21/adv]** Not expected: stock
   clears it for External (`0x0813F0F2`).
4. Keep the top-end drop. Defer C, and don't build the per-pad map or a preset.

Close calls:
- A0 vs A1: A0 is five NOPs and has no logic of its own to get wrong, but it is only correct if no
  internal track sends MIDI. A1 is correct in both cases and costs about a sprint more. I'd rather
  measure the fact than pay for A1 as insurance. But if N0 can't settle it, A1 is the safe default.
- B now vs later: cheap, but it touches the piano roll. It comes after A is proven on the device so the
  two risks don't land together.

## Sprints
Feature groups for build.py (1.6.8 only):
- **`note0`**, title "Literal MIDI note 0 (M7)": the five sentinel patches (A0 or A1), the helper, and the
  retrig / Note Mod changes. Depends on nothing (the poly cap is independent). In `stock_168` `groups`.
  `stock_161` lists it in `default_off` and omits it (like `analog`).
- **`note0_ui`**, title "Step Note down to 0 (M4)": option B (and F if needed). Same 1.6.1 handling.
  Without `note0` it does nothing useful, so build.py should drop `note0_ui` when `note0` is disabled
  (like analog follows poly_cap/bbox).
- New table entries in `mods/m1/bases/stock_168.py` only, each with its stock bytes, and `reviewed`
  status with the evidence in the note, as for the existing sites.
- Version line: the next 1.6.8 line after BE4.20 (S6a).

### N0: settle the unknowns (emulator + one device session on BE4.10, no new build)
- Emulator: does any M4 path give a non-External track type a MIDI channel (an M7 message that ends in the
  +0x86D1 write at `0x08062B7A` with a non-zero channel)? Reachability scan as in `reach.py`, from each
  type case of the track sync `0x0813E4B8`. **This decides A0 vs A1.**
  **[Correction, review rc21/adv]** This scan can't decide it. Key 0x10 (and 0x1C7) reach the M7 through the
  generic setting forwarder (`0x08132DDE..F2`, `0x08132F28..4C`) for whichever track the edit or load
  targets. Decide it instead by which track pages list key 0x10 (M4 page tables), by a project load of a
  Sample track with `midioutchan` set, or on the device (first device item below). Re-check that every
  type case in reach scans uses External = 0x16, not 0x15.
- Emulator: the +0x86DC table and the +0x10 key of a real External track, built by running the stock M4
  track setup into the M7 handler (not the `Player(ext=True)` fixture).
- Emulator: the piano roll's lowest insertable row and the new-step default note (for B).
- Device, MIDI monitor, current BE4.10, project backed up:
  - Does any internal track type (sampler, kit, wavetable) offer a MIDI out channel in the UI? (Cross-check
    for the emulator answer.)
  - External track, Chromatic / Root C: press all 16 pads at offsets -5, 0 and +5 and compare with the
    table above. This settles the pad-mode question and the physical pad order. Expect pad "0" -> 44.
  - Stale flag: make a slot a slicer, switch it to External, press pads at +5. 120..127 = fine, 76..91 =
    stale flag (build F).
  - Record a phrase including the "0" pad, play back (expect 44), save and reload, open the step's Note
    field (expect "None", and an edit gives 1).
- Exit: a written answer to each item. A0 or A1 chosen from a run, not a read. Go/no-go for F.

### N1: literal note 0 (`note0`)
- Build: the five patches (A0) or trampolines + helper (A1), retrig `midi_note()` through the helper,
  `nm_drum()` through the helper, dead clamp removed. All C changes behind `BE_OMIT_NOTE0`.
- Emulator tests, new `mods/m1/test_note0.py`, real stock code, BE image vs stock image:
  - End to end (the `chain.py` approach, kept as a test): pad presses at offsets -5..+5, Chromatic, Root
    C -> M4 MIDI bytes. **Exactly 128 distinct notes 0..127**, each On paired with its Off. On stock: 127
    notes, with 0 -> 44 (the test proves it would catch a regression).
  - PAD_NOTE_ON/OFF and SEQ_PLAYER on an External track: note 0 sends 0 for pads 0, 8, 9 and 16. Notes 1,
    11, 12, 60, 126, 127 unchanged. 128 and 163 still dropped by the M4.
  - Stop while a note 0 sounds: the all-off path sends Off 0.
  - A1 only: an internal track with MIDI out and a key on the pad still sends 44/36/52, and the External
    fixture has its pad-0 key cleared.
  - Retrigger: every ratchet hit on a note-0 step is 0, On/Off paired.
  - Note Mod: note 0 on External moves by pitch (+2 -> 2). 4-2 sends 0. 126+2 -> 117 (wrap kept). Above
    127 unshifted.
  - Poly cap of 1 with notes 0 and 44 alternating: they are separate voices, with correct steal/Off pairs.
  - Curves: AT goes out on note 0 for a note-0 step.
  - No hook emits velocity 0 or 128 for a Note On with a non-zero source velocity.
  - Recording: the record message keeps 0. project.xml pitch 0 round-trips (`test_step_xml`).
  - Regression: test_s3_m7, test_notemod_route, test_notemod, test_retrig, test_polyat, test_step_xml and
    the full `test_m1.sh` pass. The 1.6.1 build's SHA is unchanged. `BE_DISABLE=note0` gives a 1.6.8 image
    whose five sites and C code match the current build.
- Exit: all green, and the build manifest lists `note0`.

### N2: device check of N1 (MIDI monitor, then Syntakt)
- MIDI monitor: sweep all 16 pads at every offset -5..+5. The union is 0..127, each On followed by its
  Off. Fast hold/release: no stuck notes. Stop while holding the "0" pad: Off 0 is sent.
- Steps at 0, 1, 11, 12, 60, 126, 127 (0 via recording): played back exactly. Save, reload: the same.
- Ratchet and Note Mod on a note-0 step: the monitor shows 0 (and the shifted notes), never 44.
- A1 only: an internal track with MIDI out still sends its drum notes.
- Syntakt on its auto channel, offset -5: all 12 tracks from the pads, record a 12-track pattern, play it
  back.
- Open the saved project on stock 1.6.8: it loads, and note-0 steps play as 44 (expected, documented).
- Exit: Jeff's OK on the Syntakt.

### N3: Step Note down to 0 (`note0_ui`, and F if N0 said so)
- Build B (and F).
- Emulator: the edit clamp gives 0+0 -> 0, 1-1 -> 0, 0-12 -> 0, 127+12 -> 127. Both label paths give
  "C-1 (0)" for 0 and unchanged labels for 1..127. The registry entry is unchanged for other fields. F: the
  External track sync posts {0x175, 0, track}, and the router's flag is 0 after slicer -> External.
- Device: type a step at 0, it plays 0. Edit a recorded 0 up and back down. F: the slicer -> External
  check gives 120..127 at +5. Open on stock 1.6.8: loads, shows "None" (expected).
- Exit: all green, and Jeff's OK.

### Later, only if wanted: C (per-track layout).

## Open decisions for Jeff (with my recommendation)
1. **A0 vs A1:** let N0's measurement decide (A0 if no internal track can send MIDI). If N0 can't settle
   it, A1. Do you know whether any internal track on the Bento has a MIDI out channel? That would answer
   it before N0.
2. **B (typing a step at note 0):** recommend yes, after N2, because steps are part of the requirement and
   a recorded 0 currently turns into 1 when edited. Say no if pads + recording are enough.
3. **Notes above 127:** recommend keep stock's drop (no cap).
4. **Note Mod at the edges:** recommend keep the octave wrap (126+2 -> 117), not a clamp.
5. **Global Scale/Root:** 0..127 from External pads needs Chromatic with Root A..C. Recommend living with
   it, and C only if it gets in the way.
6. **Behaviour change for existing projects:** a note-0 step on a MIDI track played 44 and will now play
   0. Recommend accepting it (it is the fix). No compatibility switch.

## Risks
- ~~External's pad mode on 1.6.8 is unproven~~ **[Correction, review rc21/adv]** Settled by reading (R): mode 3
  (External = type 0x16 -> `0x0813AFBE`). The device check remains a confirmation only.
- **[Added, review rc21/adv]** The drum map is designed for drum-mode tracks (router copy with the real pad,
  `0x080855E0`), and MIDI OutCh is a generic per-track key. So A0 is only safe if no Sample/Loop/kit track
  can set MIDI OutCh. Until that is shown, treat A1 as the default, not A0.
- **The A0/A1 fact is wrong:** an internal track sends MIDI after all, and A0 changes its drum notes.
  Mitigation: N0 measures it, and N2 has a check on the device.
- ~~The stale linear flag (R) caps an External track at 91. N0 check, F fixes it.~~ **[Correction, review
  rc21/adv]** Stock's External sync case clears the flag (`0x0813F0F2`). Not a risk unless the device shows it.
- The physical pad order is assumed (index 8..15 = the bottom row). N0 check.
- Not traced: the router override field ev+0x14 (0 = "work it out", so a note 0 coming in that way would
  be lost: possibly MIDI in, outside this requirement), the other Note On sender object (port +0x8C,
  `0x0805CD9C`, `0x0805D5E8`, `0x0805D694`, `0x0805D6D4`, `0x0805DA82`, which has no sentinel and passes 0
  through), what the M4 recording path receives during normal recording (track +0x30 clear), and whether
  there is a track transpose above the step note.
- Stock velocity: light pad hits can give velocity 0 (`90 nn 00`), which receivers read as Note Off. This
  is stock behaviour, not in scope, but it can look like a lost note 0 on the monitor.
- 1.6.1 byte-identity: guarded by `default_off` + omit define and checked by the 1.6.1 SHA test in N1.

## N1 built: BE4.22 (2026-11-08, offline; device check N2 open)
Option **A1**, group `note0` (1.6.8 only: `default_off` + `-DBE_OMIT_NOTE0` on 1.6.1, which stays byte-identical to v3.72).
Jeff settled the A0/A1 question on the device: internal tracks can have a MIDI Out channel, so stock's map stays for them.
Scratch: `rc23/implN/` (`final.sh`, `mut.sh`, `preds.py`, `ctor.py`, `scan.py`).

- **Where.** Not the five sentinel branches but the five out-of-line drum-note blocks they jump to (`NOTE0_<site>_MAP`,
  re-derived: seq On `0x0805FD60`, seq Off `0x08060260`, pad On `0x08060300`, pad Off `0x08060440`, all-off `0x08061D40`;
  sentinels at `0x0805FC9E`, `0x080601B8`, `0x080602C6`, `0x08060402`, `0x08061DF0` as above). Each block has exactly one
  predecessor, the sentinel (branch decode at every halfword of the M7 image), so a `b.w` at its entry runs only for note 0
  on a MIDI-out track and every other note stays on stock's own path, byte for byte. The trampoline calls
  `note0_site(track, pad, router, site)` (`m7_note0.c`), puts the answer in the sender's note register and goes back to
  `NOTE0_<site>_BACK` (the event build). Five 4-byte sites, 160 B of trampolines.
- **The rule** (one function for On, Off, all-off, retrigger hits, Note Mod and curves): stock's drum note iff pad <= 15,
  the per-pad key `+0x86DC + 4*pad` is not 0xFFFFFF00 **and the router still has that key** (its bus table, count
  `+0x508`, the table bus_post searches); otherwise MIDI note 0. The last test is an addition to the review's predicate:
  the M4 sends 0xA1 (the only writer besides the constructor) only for an instrument's own pads (`0x081329E8`, nothing for
  External), and the M7 makes its eight track objects once (`0x08058A60` loop, the only constructor call), so a slot that
  held a kit and became External can keep the kit's old keys (R). Such a stale key is not on the bus, counts as "no
  sound", and is counted apart in the black box.
- **External's real keys (M + R):** the constructor sets all 16 to 0xFFFFFF00 (`ctor.py`), the MIDI channel setting does
  not touch them, and no M4 path sends 0xA1 for External. The +0x10 fallback is not used (as the review asked).
- **Our hooks:** retrigger hits use the same rule (BE4.21 sent 44, then 0); Note Mod's drum test is "note 0 and (no MIDI
  out, or the rule maps)", the dead `x < 0` clamp is gone, a kit hit is not moved onto a pad without a sound; the External
  curve player presses the note the Note On sent. Poly cap needed no change (0 and 44 are two voices now).
- **Black box:** the Region's last line (`REGION_NOTE0` 0x2400): `note0 zero= stale= drum= last= key=` in the save line
  (last = site | sound << 4 | slot << 8 | pad << 16). The device run should show `drum` only on kits and `zero` on External.
- **Tests:** `test_note0.py` (20 tests, real stock code incl. the ring post and the M4 MIDI task to bytes; 326 stock basic
  blocks): notes 0, 1, 11, 12, 60, 126, 127, 128 on External, a kit with MIDI out and a stale key, for pad press, step,
  recording, stop / all-off and a 2-hit ratchet. With note0 out of the image (1.6.1, `BE_DISABLE=note0`) the same suite
  expects and gets stock's 44. 9 mutants all caught (`mut.sh`).
- **Behaviour changes:** a note-0 step / pad on a MIDI track without an internal sound for that pad now sends 0 (was 44,
  Jeff's decision 5); a pad index above 15 (not reachable from a 16-pad kit) sends 0, not 52.
- **Open (device, N2):** the plan's N2 list, plus: kit -> External on the same slot (the stale-key path: expect `stale`
  counts and note 0 out), and a kit with MIDI out (expect 36..51, `drum` counts).
- **N3 (`note0_ui`) not built.** Found so far: the clamp `cmp r0,#1; it lt; movlt r0,#1` at `0x0815A99C` (after the top
  clamp at `0x0815A996`, store `strh r0,[sp,#0x40]`, back to `0x0815A82C`); the Step Note list is indexed `note - 1`
  (`0x0815BBDC`, key 0x183; key 0x184 when the view's `+0x1DAE` drum flag is set), so note 0 reads list[-1] = "None"; the
  scale-folded label path (`+0x52AE` and `+0x54B0` > 0) is `0x0815BC9C` (a 128-entry text table, index checked <= 127).
  "External only" can follow m4_step.c's `drum_track_selected()` (selected track -> object -> `vt[0]()`) with a reviewed
  `TYPE_EXTERNAL` = 0x16. Still to trace: the piano roll's lowest insertable row and a new step's default note.

## N1 / N3 at integration: the BE4.22 kit (2026-11-08, offline; device check N2 open)

- **N1 (`note0`)** is in the kit as built above, together with the Analog instrument-handle fix (docs/plan-synths-168.md,
  "Instrument handles (BE4.22)"). The two touch different code (M7 note senders; M4 object store hooks). Kit
  `flash\bento_enhanced-v4.22`, BE4.22 = d38244cd... before the review round below (which replaces it; see there),
  checklist `docs/checklist-v4.22.txt` (= the kit's DEVICE_CHECKLIST.txt): N2's MIDI monitor and Syntakt items, the
  internal drum track with a MIDI Out channel (A1: 36..51 as stock), the handle items, the black box lines, rollback.
  `BE_DISABLE=note0` on 1.6.8 = ed24b802... (the five blocks stock; test_note0 expects and gets 44 there). 1.6.1 is
  still v3.72 (25877c59...), with every old bisect name.
- **N3 (`note0_ui`) is not built**, as this plan orders it (after N2's device check). The name is refused by build.py
  (an unknown group). Consequence for requirement 1 until N3: a note-0 *step* comes from recording a note-0 pad (or a
  project made that way); the Step Note field still stops at its lowest stock value (the clamp at `0x0815A99C`). The
  checklist asks for the value it shows, not for 0. What N3 needs is in "N1 built" above (clamp, list index, label
  path, `TYPE_EXTERNAL`), plus the piano roll's lowest row and a new step's default note.

## BE4.22 review round (rc23: behaviour + build review, 2026-11-08)

Rebuilt kit `flash\bento_enhanced-v4.22` (replaces the unflashed d38244cd... kit): BE4.22 =
724ddd70a2efc79ead56431373624ecd34b1110fdf40b9f2e353b04afff36f8d; `BE_DISABLE=note0` 6fc36b9e...; `BE_DISABLE=analog`
5c5d12ba...; 1.6.1 still v3.72 (25877c59...).

- **On / Off pairing (behaviour S2, build I6).** BE4.22 as first built worked the rule out again at every Off, so an
  instrument change while a note-0 note sounded could send the On as 44 and the Off as 0 (or the reverse) and leave
  a note hanging on the receiver; stock's fixed map could not. Now the two On sites (1 sequencer, 3 pad) remember
  their decision per (track slot `+0x86D4`, pad) in the Region (`note0_on[8]`: [15:0] pads with an On, [31:16] the
  mapped ones; zeroed with the Region), and the Off sites (2, 4, 5) and ratchet hits (`m1_note0_sent`) send that.
  Curves keep `m1_note0_map` (they start at the On, where both agree). The black box line has a sixth word,
  `held=`: Offs that kept their On's note where the rule now gives the other one (the device evidence for the
  checklist's switch item). Left open: two overlapping note-0 Ons on one (slot, pad) whose rule changed between them
  (the first Off follows the second On); a Region re-zero between On and Off (the rule is worked out again).
  Road not taken: per-voice tracking (exact, but a table per sounding note for a corner of a corner).
- **Router lookup at both ends (behaviour S4).** test_note0 `Router`: kit keys first and last in the table, a one-key
  table, an empty table; the `i + 1 < n` mutant now fails.
- **The plan's exit sweep (build S2).** test_note0 `Sweep` runs stock's M7 pad router (`PADR_CTOR`, `PADR_LOOP`,
  `SCALE_INIT`; `BUS_FIND`, `BUS_POST_KEY`, `PADR_RELEASE` stubbed as chain.py did; six new 1.6.8-only reviewed
  entries, absent on 1.6.1, so the class skips there): 176 presses, Chromatic, root C, offsets -5..+5, through
  stock's External pad Note On / Off, the ring post and the M4 MIDI task: exactly 0..127, each On followed by its
  Off. On `BE_DISABLE=note0` the same test expects and gets 1..127 (0 goes out as 44). Also new: the stale key on
  pad 0 (where a melodic press arrives), pad press and step; and Note Mod with a stale key on the destination pad
  of a kit with MIDI out (behaviour note 2, mutant A: the hit stays where it is).
- **Black box line clean (build I4).** `region()` cleans the note-0 line after zeroing the Region, so a save before
  any note prints zeros, not what the cache line held.
- **Checklist.** v4.21's section 0 (SPACED_A, PLAIN_T, never load SPACED) is now in v4.22's section 0; the recorded
  note-0 step's Step Note field shows "None" and must not be edited (an edit makes it note 1); the bottom-left pad
  assumption says what to do if another pad sends 0; Pad Row Offset is per track; a switch-while-sounding item.
- **Seen, unchanged (Jeff's decisions 2 and 5):** an internal melodic track with a MIDI Out channel still sends 44
  for a note-0 step (its pad 0 has its own key, so A1 keeps stock's map), and Note Mod treats it as a pad hit there.
  Kits with MIDI out still get no live pressure on drum pads (stock).

## Backlog (Jeff, 2026-11-08: low priority)
- N3: Step Note field down to 0 on External tracks (needs the M4 code-region move first; 152 B left in M4_CODE).
- Internal melodic tracks with MIDI Out send note 0 literally (decide by the router's per-track pad mode, +0x4D0);
  internal drum-kit tracks keep stock's 36..51 per-pad map.
- Decided: a new Analog instrument starts with fresh init parameters (BE4.22 behaviour).
