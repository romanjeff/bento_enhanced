# BE4.45 (branch be445x): FM amounts as Mod Matrix destinations

Labels: **M** = measured (emulator, byte compare), **S** = read from the stock 1.6.8 disassembly, **I** = inferred.

The plan is part (b) of `docs/feasibility-wt-fm-matrix.md` (on main), "Route 2: extend the table", sprints X1 / X2.
The W4 DSP layout this builds on is `docs/plan-wt-fm.md`, "W4 as built". FM Env 1 / 2 as matrix sources
(`docs/feasibility-fm-env-sources.md`, branch be445, 5f9951e) is a separate, later feature. Nothing here uses its sites
(the connect's tbh stubs, the voice slack + 0xB84 .. + 0xB90, the source list), so it can be rebased on top.

## Decisions (Jeff)

- **Destinations.** There are exactly three:
  - **OSC1: Sub FM** (key 0x18B, instance 0);
  - **OSC2: FM 1>2** (0x132, instance 1);
  - **OSC2: Sub FM** (0x18B, instance 1). It has had no knob since BE4.43. Its base is its saved value, normally 0, so the matrix alone drives it. That value is kept and saved as today.
- **Oscillator types.** They work on Analog and on Wavetable oscillators (BE4.44 has WT FM).
- **Q4.** Modulate knob travel v, then apply the existing law. This is the same rule as J4: `v = clamp(knob + 1000 * sum amount_i * src_i, 0, 1000)`.
- **Q5.** The three rows always show.
- **Q7.** Check which prefix the rows show, and make the names read sensibly (below).
- **Q8.** Trace the row-id byte first (below).
- **Routes off.** With no route on these keys, today's path runs bit for bit.
- **Storage.** Routes are stored per root.
- **FM gate.** The gate sees "has routes".
- **Stock.** Stock loads such a route as an inert key 0.
- **Budgets.** Synth first. The default build keeps >= 1 KB free in the DSP window. The P3Identity stop line is 1.95 %. The heaviest steady voice must stay under 35,309.

## Sprint X1 (M4) as built: the table, rows, names; routings stored, inert in sound

As built after the X1 review (its dispositions are at the end of this section). The one review change to the design:
the piano roll's destination list keeps stock's table (section "The piano roll's list stays stock's").

### Q8 first: the row-id byte (+0x14) is the page's CC column, display only (S, M)

**It is a MIDI CC number.** The values match standard CC numbers:
- Freq/Cut is 0x4A (74), Res is 0x47 (71).
- Env 1 Attack 0x49 (73), Decay 0x4B (75), Release 0x48 (72).

**Where the byte goes (S):**
- The row filler `MX_WT_ROWS` (0x0814DA70) copies it into the view, at byte `view + 0x8ADBC + row`.
- The other fillers (0x0814D9F8, 0x0814E9E6, 0x0814EB8A, 0x0814ED44 .. 0x0814EDA4) and two clears (0x0814DF90, 0x0814E112) only write that array.
- **Its one reader is `MX_ROWS_BUILD` 0x0814E798:**
  - `ldrb r4, [r3, #1]!` reads the byte;
  - 0 gives the cell text "" (literal 0x0818C5F0);
  - any other value gives `snprintf("%d")` (0x0818F0B4);
  - either way the text goes into column 7 of the row (0x0816D536).
- test_step's External rows already rely on this: CC 74 shows "74", and Pitch Bend's 0 shows blank (`m4_step.c`: "the CC column (0 = blank)").

**Nothing else reads it (S).**
- In the table's own users, only the row filler reads +0x14:
  - 0x08134700 reads type and key;
  - the enumerator 0x08159358 reads type and key;
  - DEST_NAME reads type, key and name.
- No other code reaches the table (next section).
- The ids are not unique keys. Granular's table and the WT table share them (0x13 / 0x14 / 0x15 ...).

**Chosen: 0 for all three new rows.** The column says which CC stock maps to a destination, and no CC maps to Sub FM or FM 1>2, so a blank column is the honest value. A nonzero "unused" id would show a CC number that controls nothing.

**The plan was wrong here:** the "0x13..0x4B are taken" range is not all taken. The used ids are 3, 9, 0x0C..0x1F, 0x23, 0x29, 0x2E..0x35 and 0x47..0x4B.

### The table and every reference to it (S; build.py refuses any other result)

**Stock's table.**
- `MX_WT_DEST_TAB` is 0x081A0D1C, 23 entries of 24 B: `{u8 type, u32 instance +4, u16 key +8, fmt +0xC, name +0x10, u8 CC +0x14}`.
- It ends exactly where Granular's table starts (0x081A0F44).
- Its count word is `MX_WT_DEST_COUNT` 0x081A0D14.

**Users (scan.py over the whole image).** The list is complete:
- Aligned words holding the table's address:
  - 0x081347E0: 0x08134700, which posts the keys of one object type;
  - 0x0814DB8C: the row filler;
  - 0x0815941C: the enumerator;
  - 0x08159C88: DEST_NAME.
- Aligned words holding the count word's address: 0x081347DC, 0x08159414 and 0x08159C84. Each sits 4..8 B before one of the table literals.
- No movw / movt pair builds either address.
- No word points inside the table.

The row filler does not read the count word. It hard-codes the count twice:
- `movs r2, #0x17` at 0x0814DA92 (`MX_WT_ROWS_N`): the count it stores at view + 0x8AC4D;
- `add.w r7, r4, #0x228` at 0x0814DA9E (`MX_WT_ROWS_END`): the end of both of its loops.

**The view holds 60 rows (S).** MX_REFRESH clears 0x78 B of keys and 0x3C B of CC bytes (0x0814E0FE..16), and the handles up to + 0x8ADBC. So 26 rows fit:

| Array | Offset | 26 rows end at |
|---|---|---|
| keys | + 0x8AC50 | + 0x8AC84 |
| handles | + 0x8ACCC | + 0x8AD34 |
| CC bytes | + 0x8ADBC | + 0x8ADD6 |
| names, 16 B each | + 0x8AE04 | + 0x8AFA4 |

**Built (build.py `mx_dest_table` / `mx_dest_patches`, analog group):**
- **The new table.** Stock's 23 entries are copied with ours inserted, each after its oscillator's Position:
  - OSC1: Pitch, Level, Position, **Sub FM**;
  - OSC2: Pitch, Level, Position, **FM 1>2**, **Sub FM**;
  - then stock's 14 other rows, unchanged.

  The table is 26 x 24 B, 40 B of strings and its own u32 count word (26), at the end of the synth UI block (after
  N1's template): 668 B (M).
- **Patches:**
  - three table literals point at our copy: 0x081347E0 (0x08134700), 0x0814DB8C (the row filler), 0x08159C88 (DEST_NAME);
  - their two count literals point at our count word: 0x081347DC (`MX_WT_DEST_N_LIT_1`), 0x08159C84 (`MX_WT_DEST_N_LIT_4`);
  - `movs r2, #0x1a`;
  - `addw r7, r4, #0x270`.
- **Left stock's:** the enumerator's table literal 0x0815941C and count literal 0x08159414 (`MX_WT_DEST_N_LIT_3`),
  stock's table and stock's count word (still 23). Each count literal has exactly one loader, a pc-relative ldr in its
  own function (0x08134790, 0x081593B4, 0x08159B32; S, every pc-relative load of the image).
- **Refusals.** The build refuses a stock table whose layout, oscillator rows or count differ from the above, one that
  already holds our keys, any other reference to the table or count word, and literals that do not hold stock's values.

**The order costs nothing.**
- Rows are stored by key, so no index is persisted.
- The enumerator's callers (0x08159D1E, 0x08159D9C) step until it returns 0 (S).

### Names (Q7)

**What stock and BE4.44 show (M).** The row filler's text is `snprintf(name, 16, entry fmt, entry name or the key's registry title)`.
- Stock's oscillator rows use the fmt "WT1: %s" / "WT2: %s" (0x0818EAB8 / 0x0818EAC0). The emulator run of stock's filler gives "WT1: Pitch" .. "WT2: Position".
- BE4.44 left that table stock, so its Mod Matrix page also says "WT1: ..". T0 renamed DEST_NAME's format and the page prefixes, but not this table's.

**Built.**
- Our copy points every oscillator row at our own "OSC1: %s" / "OSC2: %s" strings. All nine read "OSC1: Pitch" .. "OSC2: Sub FM", consistent with the pane titles and DEST_NAME (identity T0 / J2).
- Stock's strings are left in flash, unreferenced. This is a change to the six stock rows' text. It is one line to undo (`MX_DEST_FMT`).
- Our three rows carry their own name (+0x10: "Sub FM", "FM 1>2"). The filler then never asks the registry, whose title would be null if N2 ever left our keys out.
- The longest name is "OSC1: Position" (14 + NUL), within the 16 B field.

### The piano roll's list stays stock's (X1 review; S, M)

**What the list is (S).** The destination list builder 0x08159CA8 is called only from the piano roll: 0x08159EFC (in
0x08159E80) and 0x0815C40E (in `PIANO_EVENT`). It is the piano roll's destination picker. It walks the enumerator
0x08159358 (its only callers, 0x08159D1E / 0x08159D9C) and names entries with DEST_NAME (0x08159DCC).

**Why our rows cannot go there as they are (S, M).**
- The enumerator yields (type, key) with no instance, and the builder matches each child against each row by type only
  (`cmp r3, r8` at 0x08159DA8), deduplicating by name (0x0817CCB2).
- So with our table the list gave every type-6 key to both oscillators: 27 entries instead of 23 (M, emulator), among
  them **"OSC1: FM 1>2"** (OSC1, 0x132: not one of the three) and **"OSC2: Sub FM"**, which has no knob.
- A lane there would write a value with no knob to see or reset it.

**Built.** The enumerator keeps stock's table and count word (above). The list is stock's, entry for entry (only T0's
"OSC1: / OSC2:" rename of DEST_NAME's format, as in BE4.44), and offers none of ours
(`FmTargets.test_the_piano_roll_list_is_stocks`, which runs 0x08159CA8 on this build and on stock).

**Still on our table:** DEST_NAME (its other caller is 0x0815A002, in 0x08159F6C) names our keys if asked, and
0x08134700 posts our keys (below).

**Open (for Jeff), settled in X3** (Jeff approved the lanes; see "Sprint X3"): piano-roll lanes on Sub FM / FM 1>2. They would need an M4 hook in the builder's type match that
skips (6, 0x132) on instance 0 and (6, 0x18B) on instance 1, plus knowing what a lane stores and how it reaches the M7
(not traced). Not in X1 or X2's scope as decided.

### Routings: create, save, load, stock and BE4.44 (M, test_native_props FmTargets)

**Create.** MX_EDIT posts the model's 0xD1 (`MX_SET_ROUTE` 0x081347E4):
- the event is `{0xD1, handle, key | 1 << 16, source | slot << 16, amount}`;
- it never reads the table (S);
- it keeps the routing as a type-1 node in the object's routing list. The first node is at obj + 0x14, the next at + 4: `{+0xC amount, +0x10 source, +0x12 key, +0x14 slot}`;
- source 0 removes the node.

`MX_ROUTE` reads each one back by (handle, key, slot).

**Save.** Stock's project writer writes `<modsource dest="bento_subfm|bento_fm12" src=... slot=... amount=.../>` in the oscillator's cell. BE's reader restores the same nodes.

**Stock 1.6.8** has no such token, so its reader's lookup gives 0 and each of our routings becomes a key-0 node:
- nothing routes key 0, so it is inert;
- stock's own Position routing loads as it was;
- stock's next save writes those three as `<modsource>` without `dest` (dead);
- BE reads them back as key 0, dead for good.

The plan's "I" is now M.

**BE4.44** (be444w4f) is the likely rollback. It has the tokens (N2 since BE4.42), so:
- the routings load **with their keys**;
- it shows no row for them (23 rows);
- its M7 ignores them, so they are inert;
- its next save keeps them.

BE4.41 and later have the tokens too (I). BE4.40 does not, so it gives key 0 like stock (I).

### Inert in sound until X2 (S, M)

**What the M7 does with them.** The routings now reach the M7 like stock's:
- 0x081346B8 / the model's set route post a 0x3F message;
- WT_ROUTE (0x0807C8A0) resolves the source and calls the oscillator class's slot 10 (0x08081B60);
- slot 10 routes only 0xC4 / 0xC9 / 3. Any other key is `bx lr`.
- Slot 9 (clear-all) is a separate message (0x0807B254) and does not take a key.

**Tests:**
- `ShapeMod.test_x1_fm_destinations_are_inert_until_x2`: slot 10 called for both keys on both oscillators (Analog), slots 0..3, writes no byte outside the stack. The voice with Sub FM 300 and FM 1>2 200 renders bit for bit as without them. Position still routes (control).
- `FmTargets.test_the_per_type_key_post_carries_our_keys`. 0x08134700 (model, obj, type) posts one 0x3F `{obj, key, slot 3, source 0}` per table row of a type (stock: 3, 0xC4, 0xC9 twice). For type 6 it now posts our keys too: 3, 0xC4, 0xC9, 0x18B, 3, 0xC4, 0xC9, 0x132, 0x18B. Stock's slot 10 ignores them.

**X2 must handle this message.** It is slot 3 (PARAM_CONNECT's r2 = 3, which the feasibility note reads as the PARAM's fixed slot at + 0x20: I), source 0, and the caller's r3 in the message's instance field (+2). The plan did not list it.
It matches rows by type only, like the piano roll's list, so **one object gets every type-6 key, the other oscillator's
too**: OSC1 gets a post for 0x132 (and OSC2's 0x18B row and OSC1's are both sent to each). X2's slot-10 hook must
ignore 0x132 on instance 0, and must not treat these source-0 posts as clearing a matrix route of another slot.

**The image against BE4.44 (M, byte compare of an untagged build, ~/bento/out/be445x1fu, against be444w4f).** The only
differences are:
- the three table literals, the two count literals and the two row-filler bounds;
- the version digit (0x0818DD99) and the marker string's digit (0x081BD185, "v4.44" -> "v4.45");
- the 668 B appended (the table, its strings and its count word).

The M7, the DSP window and every other M4 byte are unchanged. A tagged build (`BE_TAG=X1`) differs in more places only
because its marker string is longer ("v4.45-X1"): the M4 code after the marker moves by 2 bytes, and so does one bl
displacement in stock code that reaches it (0x08131342). That is the tag, not X1.

### Sizes (M, ~/bento/out/be445x1f, after the review)

- **DSP:** 30,884 B, **9,820 B free** of 40,704 (unchanged).
- **M4:** M4 code 24,424 B and M7 C slice 15,368 B (unchanged). The manifest's synth UI figure is now 16,514 B, which is 15,846 (code and N1 template, unchanged) + 668.
- **X1's table:** 668 B in the synth UI block (26 x 24 + 40 B of strings + the 4 B count word).
- **Image:** 0x1896EC, **15,432 B to PROVEN_MAX** (BE4.44: 16,100). That is the M4 room left for X2's M4 part and for P4.
- **Kit image:** `BE_TAG=X1`, sha256 6d5cf83df4c71d3eb99f556cd4452aedcc3f00f0cd487e9fb54664c8cbcefd39 (not a release kit;
  before the review: e22fd49c...b6539e6f). Untagged: 76b4471fad6ac07a4c3935336d48b2229b55fedd977ef21e55e6f9a941d08c4c.

### CPU (M, the suite on be445x1f)

There is no M7 or DSP change (byte-identical, above), and the measurements are BE4.44's (P3Identity re-measured on
be445x1f, the same figures):
- P3Identity, 48 cases: worst **1.0165** (mod dev=False, 111,708 / 109,899), total 1.0087. The stop line is 1.95 %.
- WtFmW4 voices: the heaviest steady voice is 34,760 (note 24, light FM, Position every block), against the 35,309 ceiling. Its one-block maximum is 39,392.

The routed cost per voice-block belongs to X2: nothing runs for these keys yet.

### Suites (M, ~/bento/out/be445x1f after the review, from the worktree's mods/m1; skips in brackets)

All OK:
- diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20);
- notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10;
- **native_props 31** (22 + 9), **analog 211** (210 + 1), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32;
- p3_golden self-test 52.

Drums suites: not run (the default build leaves drums out). The 1.6.1 build still builds (no analog group there, so no X1 patch).

### X1 review: dispositions

1. **Medium (both reviewers): the piano roll's list offered a fourth destination, "OSC1: FM 1>2", and the knobless
   OSC2 Sub FM; the doc said three; no test ran the list.** Holds (reproduced: 27 entries vs stock's 23). Fixed by
   keeping that list on stock's table (Jeff's decision is exactly three destinations, on the Mod Matrix). Variant of the
   reviewer's option (a): instead of a new "23" word for the enumerator, our table carries its own count word (26) and
   the two other count literals point at it, so the enumerator's bytes, stock's table and stock's count word are all
   stock's. The list's screen is identified (S): its only callers are in the piano roll. The doc is corrected and
   `test_the_piano_roll_list_is_stocks` asserts the exact list against stock's. The cross-instance 0x3F post of
   0x08134700 (OSC1 gets 0x132) is kept and carried to X2 (its slot-10 hook ignores 0x132 on instance 0).
2. **Low: "every M4 code byte unchanged" is not literally true for a tagged image.** Holds. Compared an untagged build
   against be444w4f (only the intended words, the version digit and the marker digit differ) and reworded the claim.
3. **Low: facts.py evidence strings cut off and apostrophes dropped.** Holds. All 15 X1 entries rewritten with the full
   reviewed text from stock_168.py, quoted, apostrophes kept.
4. **Low: some mutants were caught only by structural asserts; the DEST_NAME test cannot tell the tables apart.**
   Holds. Added behavior coverage: the piano-roll list test, and a memory-read hook requiring DEST_NAME to read our
   table. Image-level mutants (M, ~/bento/out/be445x1f-*): the enumerator on our table with 26 (the pre-review X1),
   on our table with 23, and DEST_NAME back on stock's table each fail two tests (the behavior test and the
   structural one). The structural asserts stay as regression guards.
5. **Info: mutation sweep, no defect.** No action.

## Sprint X2 (M7 / DSP) as built: the routings play

The design is the plan's (slot 9 and 10 trampolines, a per-root store, a per-block sum and clamp into msub / m12, the gates
see the routings). Below: what had to be traced first, what was built, and where the plan was wrong.

### How a routing reaches an oscillator (S)

- The root's message loop (0x0807ACE0..) takes a 0x3F message to WT_ROUTE (`bl 0x0807C8A0` at 0x0807B280).
- **WT_ROUTE** (0x0807C8A0..0x0807E4C6) reads `{+2 instance, +0xC key, +0x10 slot, +0x14 source id, +0x16 amount}`. The
  amount becomes `f32(amount) x f32(0.001)` (0x0807C8D4). For each of the root's voices (root + 0x14 + 4 i, count from the
  root's vtable 0x50: 10) it resolves the source **for that voice** and calls slot 10 of oscillator[instance]
  (`ldr.w fp, [ip, #0x28]; blx fp` at 0x0807CB38, and the per-source copies of that call).
- **Slot 10** (0x08081B60) is `(osc, source pointer, key, slot, amount s0)`. It routes keys 0xC4 / 0xC9 / 3 to
  PARAM_CONNECT and `bx lr`s on any other key.
- **Slot 9** (0x08081C20) is `(osc)`: it clears the routed slots of Position, Pitch and Level. The root's clear (message
  0x48) calls it per voice for WT1, WT2 and the sub (0x0807B242..5C). It has no key.
- **Slot 10 has no root argument**, and per-root storage needs the root. WT_ROUTE does `mov r4, r0` at 0x0807C8B0, and
  no other instruction in 0x0807C8A0..0x0807E4C6 writes r4 (every instruction's written registers, x2 r4b.py). So at
  every slot-10 call it makes, r4 is the root.

### Q8 continued: the source ids, traced (M, `FmTargetsX2.test_sources_sit_at_fixed_voice_offsets_and_the_store_finds_them`)

The plan asked to verify that per-voice sources sit at fixed voice offsets. For every id of stock's Wavetable source list
(0x081A1304), on a real root (stock's ctor and engine init, 10 voices), the pointer stock's own connect stores is:

| Ids | Source | Pointer | Connect slot |
|---|---|---|---|
| 0x01, 0x02 | env 1, env 2 | voice + 0x1B0, + 0x2AC | the matrix slot |
| 0x04, 0x05 | LFO 1, LFO 2 | voice + 0x3B8, + 0x4B8 | the matrix slot |
| 0x08 | seq | voice + 0x79C | the matrix slot |
| 0x0D, 0x0E, 0x20 | velocity, key, pressure (aftertouch) | voice + 0x34, + 0x44, + 0x3C | the matrix slot |
| 0x1A, 0x1E, 0x1F | modwheel, macro X, macro Y | root + 0x28C, + 0x2DC, + 0x2E4 | the matrix slot |
| 0x09..0x0C | seq1..seq4 | root + 0x140, + 0x148, + 0x150, + 0x158 | **3, whatever the matrix slot** |

The same offset in all 10 voices, or one root address: **no source of this list needs an absolute address** (the plan
allowed for one). Ids 9..12 go to stock's connect slot 3 (PARAM slot 4, + 0x20): WT_ROUTE's case for them
(0x0807CEF0..) passes `movs r3, #3` and `root + 0xF8 + 8 id` (S). That is what "PARAM slot 4" means, the plan's open
point: the mod seq lanes' slot. 0x08134700's per-type post (X1: `{key, slot 3, source 0}`) clears it.

The FM Env sources of the later feature (ids 3 / 6, `docs/feasibility-fm-env-sources.md`) resolve to voice + 0xB84 /
+ 0xB88, inside the voice's 0xBA0 bytes: the store's voice-relative form holds them as they are.

### What was built (dsp/analog.cpp, build.py, synth_shared.h)

**Two trampolines** (build.py `m7_dsp`, in the DSP trampoline block, after the four of BE4.40..BE4.44):
- slot 9 -> `x2_clear(osc)`, then stock's slot 9 always;
- slot 10 -> `x2_connect(osc, source, key | slot << 16, root, amount s0)`, then stock's slot 10 always (it keeps its own
  keys and ignores ours). `root` is r4 only when the call's return address lies inside WT_ROUTE
  (`lr - WT_ROUTE < WT_ROUTE_LEN`), else 0. s0 is saved for stock's (vpush {s0, s1}).
- build.py refuses unless both vtable words hold stock's functions and **WT_ROUTE's 0x1C26 bytes hash to WT_ROUTE_SHA**,
  the bytes whose r4 was read.

**The Analog oscillator equivalent is the same two trampolines.** An Analog oscillator is a Wavetable-class oscillator
with Type Analog (the class vtable 0x080ACBB4, BE4.40 N3), so there is no separate class to hook.

**The store** (`X2Root`, SA + 0x440..+ 0x600, inside the area the M7 zeroes at its take; the count in the run state's
word 1, Run.x2n):
- 8 entries of 56 B, one per Wavetable root (8 tracks): `{root, handle, u16 src[12], s16 amt[12]}`, key k (OSC1 Sub FM,
  OSC2 FM 1>2, OSC2 Sub FM) slot s at [4 k + s];
- `src` is the source's offset in the voice (1 .. 0xB9F), or 0x8000 | its offset in the root; 0 is none. `amt` is the
  routing's amount (-1000..1000), recovered from s0 by rounding (stock's s0 is `f32(amount) x f32(0.001)`, and we
  recompute exactly that).
- An entry belongs to its root while root + 0x0C is the handle it was made with (stock reuses roots: the glide lines'
  rule). A stale entry is dropped when its root is next seen. With the store full, entries whose root has another
  handle now are dropped first; a routing that still does not fit, or a source outside the voice and the root (only a
  MIDI CC id, >= 0x8000, could be: not in the Wavetable list), is not kept and counted once (SaFmStats.x2_lost, +24).

**x2_connect:**
- takes only keys 0x18B / 0x132, slots 0..3, a root, and an oscillator whose voice is one of the root's 10;
- ignores 0x132 on instance 0 (0x08134700 posts every type-6 key to both oscillators, X1);
- keeps slot 3 as stock keeps it (the seq lanes; the per-type post's source 0 clears it, as on stock);
- source 0 clears the slot. When a slot that held a routing clears, `x2_tidy` runs over **all** the root's voices at once
  (the connect comes per voice; after the first the entry may be gone). It drops the entry once it routes nothing,
  untags a Wavetable oscillator nothing holds any more (wt_settle's rule), and calls wt_feed, so a Wavetable OSC1 keeps
  feeding B while OSC2's m12 ramps down, exactly as after the knob turned to 0.

**x2_clear** (slot 9, per voice and oscillator): clears this oscillator's keys in its root's entry (OSC1: Sub FM;
OSC2: FM 1>2 and Sub FM), then the same tidy.

**Per voice-block** (`x2_enter`, called from an_enter while Run.x2n != 0, before the all-Wavetable early return):
- Q4: `v = clamp(knob + 1000 sum amt_i src_i, 0, 1000)`, in float32 as `m = fma(f32(amt_i) x f32(0.001), [src_i + 4], m)`
  over slots 0..3 in order (stock's PARAM_VALUE's order and its VFMA), then `fma(m, 1000, knob)`, rounded to the
  nearest whole amount (msub / m12 hold whole amounts: the next block's ramp starts where this one ended). NaN plays 0.
- The knob is the oscillator's stored Sub FM / FM 1>2. OSC2's Sub FM has no knob: the base is its stored value. An
  Analog OSC2 keeps it (an_prop); a Wavetable OSC2 does not (BE4.44 W4 D2: wt_prop drops it), so there the base is 0.
- The result goes to Run.xm (which keys are routed) and Run.xv[3]. fm_setup plays xv in place of the knob for this voice
  only, into the existing msub / m12 ramps. **OSC2's Sub FM, which plays nothing unrouted (D2), plays while routed** as
  the second term next to FM 1>2 (A on Sub FM's line, B on FM 1>2's; the kernel's two-term line).
- A Wavetable oscillator a routing needs is tagged here (wt_init) before the voice-block renders. That is its own key;
  OSC1 is also tagged for OSC2's FM 1>2, which it feeds.

**The FM gate sees the routings** (one load and branch each when nothing is routed):
- an_osc's FM word (Analog): OSC1 or's in Sub FM and FM 1>2 routed (it feeds B), OSC2 FM 1>2 and Sub FM;
- wt_fm_osc's gate (Wavetable, `x2_gate`): the same;
- feeds (an Analog OSC1 feeds B) and wt_body's feed-B test (a Wavetable OSC1): OSC2's FM 1>2 routed;
- D3 (a Wavetable OSC1 synced to an Analog OSC2): a routed OSC1 Sub FM plays as 0, as its knob does.

**Nothing routed** (Run.x2n 0): an_enter stores Run.xm = 0 and tests x2n; each gate tests Run.xm. Every value is
today's: X2Identity (below) shows it byte for byte against BE4.44.

### Results (M, ~/bento/out/be445x2a unless named)

(These are the numbers from before the review. For CPU, sizes, suites and the kit, "X2 as built after the review"
below supersedes them.)

**Identity, nothing routed** (`X2Identity`, new): BE4.44 (be444w4f, its own image and DSP symbols) and this build run the
same 8 cases on the device's path (stock's root, voice render and sub). The cases are the four type pairs, with FM knobs,
FM envelopes, a stock Position routing moving every block, a retrigger, and a stored OSC2 Sub FM. Every output sample,
the whole voice's 0xBA0 bytes and the FM stats words are **equal**. P3Identity (vs BE4.40), FmEnv and WtFmW1..W4 are
unchanged and green.

**The law, bit for bit** (`FmTargetsX2`): the routed voice equals the same voice with the knob turned to `v`, every block:
- Analog OSC1 Sub FM, Analog OSC2 FM 1>2, Wavetable OSC1 Sub FM, Wavetable OSC2 FM 1>2 (from a Wavetable and from an
  Analog OSC1);
- knob 200, amount 600, source values 0, 0.5, 1, -1, 2, 0.25, -0.25, 0.125, 0.75, 0.375, so both clamps are hit;
- removal (source 0 through WT_ROUTE) and the root's clear (slot 9 per voice) at block 4 equal the knob turned to 0
  there, its ramp out included. The store is empty afterwards and both kernel areas end as the knob's run left them
  (Analog and Wavetable, OSC1 Sub FM and OSC2 FM 1>2).
- Knob 0 with a routing renders FM (all three destinations, both types): the gate sees it.
- OSC2 Sub FM: its stored value alone plays nothing (as BE4.44); routed, msub = base + travel (350 on an Analog OSC2 with
  base 100, 250 on a Wavetable one), next to FM 1>2's 300, nothing missed.

**CPU** (instructions per voice-block, an_enter and both oscillators' renders, note 60, the median of the steady blocks /
the max; `FmTargetsX2.test_cost`):

| Voice | Knobs (today's path) | The same sound routed | + OSC2 Sub FM routed |
|---|---|---|---|
| Analog both (Sub FM 300, FM 1>2 300) | 28,071 / 28,385 | 28,425 / 28,739 | 30,815 / 31,210 |
| Wavetable both (the same) | 27,181 / 29,793 | 27,585 / 30,197 | 30,360 / 32,202 |

- **The routed cost**, two routed destinations, the same sound: **+354 (Analog) / +404 (Wavetable) instructions per
  voice-block** (x2_enter, its tags and the gates). Gated at 600. OSC2 Sub FM routed adds an FM term today has none of
  (+2.4k / +2.8k), still within the heaviest existing voice (35,309): max 32,202. **(Wrong as a claim about the
  heaviest voice, and superseded: this voice is a light one. See "X2 review" below: on the heaviest voice the routing
  went over the ceiling; after the fix two routed keys stay within it and OSC2 Sub FM routed is 5.6 % over.)**
- **The cost with nothing routed:** X2Identity's instructions, this build / BE4.44, are 1.0006 .. 1.0021 over the 8
  cases (worst: a Wavetable OSC1 next to an Analog OSC2 with OSC2's Sub FM stored). That is about 15..60 instructions per
  voice-block: the loads and branches above, and the compiler's layout of the functions that grew.
- P3Identity (vs BE4.40, the stop line 1.95 %), 48 cases: worst **1.0177** (mod dev=False, 111,848 / 109,899; X1:
  1.0165), total 1.0098. 0.18 % of margin is left to the stop line.
- WtFmW4 (nothing routed): the heaviest steady voice **34,806** (note 24, light FM, Position every block; BE4.44 34,760),
  against the 35,309 ceiling; its one-block maximum 39,438 (BE4.44 39,392).

**Sizes:**
- **DSP:** 32,652 B, **8,052 B free** of 40,704 (BE4.44 / X1: 30,884, 9,820 free): **+1,768 B**. The largest parts:
  x2_connect 396, x2_enter 304, x2_clear 176, x2_tidy 120, an_osc +124, fm_setup +72, an_enter +68, wt_body +68 (the
  message-loop functions at `minsize`).
- The DSP trampolines: 0x70 + 44 B of the 0x100 block.
- **M7 C slice, M4 and the synth UI block:** unchanged (15,368 / 24,424 / 16,514 B). X2 is M7 only.
- **Image:** 0x1896EC, **15,432 B to PROVEN_MAX** (0x18D334; unchanged since X1): the M4 room left.
- SA: the store's 448 B are in the run-state page (+0x440..+0x600), free on main too.

**Kit and images:**
- Release kit `flash\bento_enhanced-v4.45` (boot label BE4.45; not on the SD card): bento1.bin sha256
  dd78cd75cf65a5e90a60281bd17644371daeb69feb63b0664db7ee512ea5a7aa (~/bento/out/be445x2rel, byte-identical to be445x2a,
  the build every suite ran on), DEVICE_CHECKLIST.txt = docs/checklist-v4.45.txt, release notes
  docs/release-notes-v4.45.md.
- Sprint image `BE_TAG=X2` (~/bento/out/be445x2): sha256 0f3fd52805fc2016eb59d7e45faa9342845bf6b3926c223e956fd4390c41cb7a.

**Suites** (M, ~/bento/out/be445x2a, from the worktree's mods/m1; skips in brackets). All OK:
- diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20);
- notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10;
- native_props 31, **analog 224** (211 + FmTargetsX2 12 + X2Identity 1), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16),
  note0 32;
- p3_golden self-test 52. Drums suites: not run (the default build leaves drums out).

### Rollback and stock

Nothing about the saved form changed in X2 (X1's tests still hold): stock 1.6.8 loads these routings as inert key 0, and
BE4.44 keeps them with their keys, inert. On BE4.45 a project's push brings them to the M7 through WT_ROUTE like stock's.

### Open (for Jeff)

1. **A Wavetable OSC2's stored Sub FM is not the routed base** (it is 0 there). It is kept by the M4 and saved, but BE4.44
   W4 (D2) stopped the M7 keeping it. It is normally 0 (no knob since BE4.43); a nonzero one only comes from an old
   project. Keeping it on the M7 would tag the oscillator for a value that plays nothing unrouted (today's path would
   change), so it was left. Say if it matters.
2. **Seq lanes (seq1..seq4) share one slot per destination**, as on stock: two of them routed to the same FM destination
   keep only the last, whatever the matrix slots (stock's own rule for its destinations, WT_ROUTE's slot 3).
3. **The FM Env sources feature, when rebased on top:** its publish in an_enter must come before x2_enter, or an FM Env
   routed to an FM amount reads the previous block's value.
4. **Device check** (checklist-v4.45.txt): the routings at the device's message order (a project's push, the clear
   before a resend), CPU at 8 voices with routings, the sound of block-rate FM depth.
5. **(X2 review) OSC2 Sub FM routed on the heaviest voice is 37.3k, 5.6 % over the 35,309 ceiling.** It is a whole FM
   term BE4.44 never plays. The choices: accept it as a known miss (as W3/W4's FM Atk peaks were; gated at its measured
   level); restrict it (for example, play OSC2's routed Sub FM only while OSC2 is Analog: the second review measured
   that within the ceiling, 31.9k with both Analog and 34.7k next to a Wavetable OSC1, before the fix); or a
   cheaper two-term kernel later (its own sprint). The device's 8-voice figure (checklist 5) should decide.
6. **(X2 review) A routing that reaches the M7 before it takes its area is lost until the project is loaded again.**
   The window is probably closed (the take comes at the first audio block after the M4's config line), but that order is
   device behaviour. If the checklist's cold-boot item fails: an M4 re-push of the Wavetable tracks' FM routings on a
   generation change, shared with the drums' re-push (plan-p4-drums 3.6).

### X2 review: dispositions (two reviews of 2472b8e; M on ~/bento/out/be445x2r unless named)

Each issue was checked against the code and, where it was about CPU or a missing test, measured or mutated first.

**R2-1 (high), CPU on the heaviest voice: holds, fixed for two of the three destinations.**
- Verified. Wavetable both, Sub FM and FM 1>2 at 1000, Position every block, note 24: knobs 34,889. Before the fix,
  the same sound routed was 35,295, and over the 35,309 ceiling with every slot used (35,369) or with 7 routed roots ahead
  in the store (35,351). The plan's "max 32,202" above came from a light voice (knobs 300, no Position modulation).
- Where it went (Span per function, before): x2_enter 340 per voice-block. Of that, x2_mask's loop over the 12 slots was
  118, x2_find 15, the tag tests 21, and the per-key slot loops the rest.
- Fix (dsp/analog.cpp x2_enter, the per-voice-block path only):
  - a plain scan for the entry (4 instructions per entry ahead);
  - a key's four slots tested as two words (no x2_mask);
  - the slot loop unrolled.
  - x2_find / x2_mask stay for the message loop.
  - x2_enter is now 193 a voice-block on this voice (228 before the unroll).
- After the fix (`FmTargetsX2.test_cost_heaviest_voice_routed`, new; every block gated at HEAVIEST_VOICE), notes 24 / 36,
  steady median / max:

| Heaviest voice, Position every block | note 24 | note 36 | gate |
|---|---|---|---|
| knobs (today's path) | 34,889 / 34,891 | 34,879 / 34,881 | |
| the same sound routed (2 keys, velocity) | 35,148 / 35,150 | 35,138 / 35,140 | 35,309 |
| 2 keys x 4 slots (velocity, env 1, env 2, seq1 in slot 3) | 35,216 / 35,218 | 35,206 / 35,208 | 35,309 |
| that, with 7 routed roots ahead in the store | 35,258 / 35,260 | 35,248 / 35,250 | 35,309 |
| unrouted voice, 8 routed roots in the store | 34,954 / 34,956 | 34,944 / 34,946 | 35,309 |
| + OSC2 Sub FM routed (3 keys) | 37,288 / 37,290 | 37,288 / 37,290 | 37,700 |

- Two routed keys now cost +259 a voice-block on this voice (was +406). The worst two-key case is 49 under the ceiling.
  That margin is thin, but it is the worst of this sweep: all slots used and the store full at once.
- **OSC2 Sub FM routed is 37.3k, 5.6 % over the ceiling.** That is not overhead. It is a whole second FM term on OSC2
  (A on Sub FM's line, B on FM 1>2's), which BE4.44 never plays on any voice (BE4.43 removed it: "OSC2's Sub FM term is
  gone", 31,412 -> 28,929). Making it cheaper means a cheaper two-term kernel, not a change to X2. It is gated at its
  measured level (SUB2_HEAVY_MAX 37,700), the way W3/W4 gated their known misses, and it goes to Jeff (below).
- Corrected: the plan line above, the release notes ("within the heaviest existing voice (35.3k)" had mixed the voice
  with the ceiling), and checklist section 5, which now aims at this worst case with pressure as the source.

**R2-4, cost while some other root is routed: holds, reported, made smaller.** Once any root has an entry (x2n != 0),
every voice runs x2_enter's scan. Before: +86 for an unrouted voice with 8 entries in the store. After the scan change:
+65 (34,954 vs 34,889 above). A per-root flag was not built. The only per-root line the M7 keeps (lookup()'s Root) exists
only for an Analog OSC1, and keeping a flag in step with the store costs more code than the 4 instructions per entry it
would save.

**R1-1, R1-2, R1-3, R1-6 (a, b), R2-2, R2-3: the gaps in the tests hold; tests added.** The reviewers' surviving
mutations, re-run on a copy against this build (scratchpad be445x/x2r/mut, FmTargetsX2 + X2Identity):

| Mutation | What it breaks | Caught by |
|---|---|---|
| VOICE0 (every voice reads voice 0's source) | per-voice sources | test_each_voice_reads_its_own_sources |
| ROOTREL (root sources read as voice offsets) | modwheel, macros, seq1..4 | test_root_sources_seq_slot_and_the_sum_play_as_the_law |
| SLOT3 (slots 0..2 only) | the seq lanes' slot | the same |
| SUM (only the last slot plays) | the sum over slots | the same |
| XMOR (OSC1 Sub FM forced routed whenever any key is) | the other key's knob | the same (the other knob at 300) |
| ROUND (truncation) | rounding to nearest | the same (0.7 of a unit), and the pair tests |
| FMAORDER2 (`m x 1000` then `+ knob`, two roundings) | the final fused rounding | the same ("the final FMA's rounding": 764, not 765) |
| FMAORDER (`m * 1000.f + knob` in one expression) | none: **equivalent**. clang's default -ffp-contract=on fuses it into the same VFMA (FMAORDER2 is the real change) | survives, as it must |
| D3 (routed OSC1 Sub FM kept under mixed sync) | D3 | test_routed_osc1_sub_fm_follows_mixed_sync_as_the_knob |
| TIDY1 (tidy only voice 0) | voice 1's feed of B on the ramp out | test_removal_tidies_every_voice_of_the_root |
| AMTCLAMP (no clamp to +-1000) | the amount's range | test_amounts_keep_their_value_through_the_route_handler |
| MASKWORD (a key's slots 2..3 not seen) | the new word-wise mask | test_a_key_routed_only_in_its_upper_slots_plays (added after the sweep showed it) |
| HANDLE (no handle check in x2_enter) | a reused root | test_routes_follow_the_root_and_its_handle |
| HASVOICE (no x2_has_voice) | a root that does not hold the voice | test_connect_guards |
| TRAMPLR (r4 passed whatever lr is) | the lr check | test_connect_guards (r4 = the real root, lr outside) |
| TRAMPS0 (no vpush / vpop of s0, s1) | s0 for stock's slot 10 | test_connect_guards (the bytes) |
| none (control) | | all green |

- R1-6 (c), x2_clear clearing all keys on either oscillator: **not a test, by design.** Stock's 0x48 clear calls slot 9
  for both oscillators of every voice (0x0807B242..5C), so the per-oscillator split cannot be seen from stock's path. The
  reviewer said the same.
- R2-3, TRAMPS0: x2_connect does not touch s0 on stock's keys at this build's layout, so dropping vpush / vpop changes
  nothing anyone can hear. The test pins the trampoline's bytes, and Position's stored amount next to our keys' connects.

**R2-6, the over-read: holds, fixed.** A source is kept only if the float at src + 4 lies inside the voice's 0xBA0 bytes
(offset 1..0xB98) or the root's 0x2F0 (0..0x2E8). Stock's 15 sources are at most voice + 0x79C and root + 0x2E4. FM Env's
later voice + 0xB84 / 0xB88 still fit.

**R1-5, R2-5, x2_lost not visible on the device: holds, fixed.** The ANALOG line prints ` x2lost=` after `wt=`
(m4_synth.c, +28 B). The checklist routes all 8 tracks and reads it. It also states that a save on stock drops these
routings (test_native_props' stock-load test shows the destination dropped).

**R1-4, a routing that arrives before the M7 takes its area: holds in part. Checklist item added; the re-push goes to
Jeff.**
- Verified. x2_connect returns early when !area_ok(), without counting (the counter is itself in the area). synth_take
  zeroes +0x400..+0x800, the store included.
- When it can happen. The take runs from h_consume at the first queue read after the M4 publishes its config line
  (m7_diag.c -> m1_region_poll -> region_reserve -> synth_take). So it comes before any routing message, unless the M4
  posts a project's routings before it publishes that line. The drums plan allows for exactly that ("a project load before
  the handshake", plan-synths-168), and the order is device behaviour (I).
- After an M7-only reset, stock's own engine state is rebuilt too, so stock's routings would be just as lost there.
- Applied: a cold-boot item in the checklist (auto-load, then play without touching the matrix), and the limit in the
  release notes.
- Not built: an M4 re-push on a generation change. That means walking each Wavetable object's routing list (obj + 0x14)
  for keys 0x18B / 0x132 and posting them again (MX_KEY_POST 0x081346B8). It is the same mechanism the drums plan (3.6,
  on main, paused) needs, so it should be one shared feature, built after the device shows whether the window exists.

**R1-7, a Wavetable OSC2's routed Sub FM base is 0, not its saved value: holds as a deviation, already disclosed; Jeff
decides.** The reviewers' cheap alternative (keep the value per root next to the X2 entry) needs the value before any
routing exists. It arrives as a property message, possibly with no entry yet, so it would mean a second per-root store
that also exists unrouted. Today the value only comes from a pre-BE4.43 project. Not built; open 1 stands.

### X2 as built after the review (M, ~/bento/out/be445x2r; release build be445x2rrel byte-identical)

- **CPU.**
  - The heaviest-voice table above.
  - `FmTargetsX2.test_cost` (light voice, note 60), knobs / routed / + OSC2 Sub FM, median / max: Analog 28,071 /
    28,385, 28,278 / 28,592, 30,652 / 31,047; Wavetable 27,181 / 29,793, 27,438 / 30,050, 30,197 / 32,039. The
    routing's own cost is +207 / +257 (was +354 / +404).
  - Nothing routed:
    - X2Identity (vs BE4.44, all byte for byte) worst 1.00209, the same as before the fix (the change is on the routed
      path only).
    - P3Identity worst **1.0177** (111,848 / 109,899), total 1.0098: unchanged, 0.18 % under the 1.95 % stop line.
    - WtFmW4's heaviest steady voice 34,806 (unchanged).
- **Sizes.**
  - DSP 32,860 B, **7,844 B free** of 40,704 (+208 B over X2: the lean x2_enter, its unroll +136, the source bounds).
  - Synth UI block 16,542 B (+28: `x2lost=`).
  - M7 C slice 15,368 B and M4 24,424 B: unchanged.
  - Image 0x189704: **15,408 B to PROVEN_MAX** (0x18D334), the M4 room.
- **Suites** (from the worktree's mods/m1; skips in brackets). All OK:
  - diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20);
  - notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10;
  - native_props 31, **analog 233** (232 in the suite run, plus `test_a_key_routed_only_in_its_upper_slots_plays`, added
    after the mutation sweep and run on its own: OK), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32;
  - p3_golden 52. Drums: not run (the default build leaves them out).
- **Mutation sweep**: the table above. 16 mutants and a control. All killed but FMAORDER, which is equivalent.
- **Images.**
  - Release `~/bento/out/be445x2rrel` (label BE4.45): bento1.bin sha256
    f87e3d0c95a2b0e9150890e3d1ae0b96093b4381d0158748bee4b1950c240245. It is the kit's 01_TEST_FIRMWARE.
  - Sprint image `BE_TAG=X2R` (`~/bento/out/be445x2rt`): 8dbcd1fe04a77db79389e46e55b2b977639c0e36c979afce536a68272b30cacc.
  - The X2 images and kit above (dd78cd75..., 0f3fd528...) are superseded.

## Sprint X3 (M4) as built: piano-roll lanes on the three FM depths

Jeff approved lanes on OSC1 Sub FM, OSC2 FM 1>2 and OSC2 Sub FM (reversing X1's "the piano roll's list stays stock's").
The trace comes first, because it decides the design: **a lane is a Mod Matrix routing from seq1..seq4**. X2 already
plays that on the M7, so X3 is an M4 change only: the list, and nothing in the M7 or the DSP.

### Trace: what a lane is (S; M where a test runs it)

**The list is the lane's destination picker (S).**
- PR_DEST_LIST (0x08159CA8) is called only from the piano roll.
- The lane is a byte of the piano view at view + 0x125D34 (0..3, lanes A..D). The list's entries are numbered from it:
  `lane + 9` is the source id, seq1..seq4. These ids are the same as in stock's Wavetable source list (X2's table: ids
  0x09..0x0C).
- Each entry carries a state from 0x0815973C: 1 = this (handle, key) is already routed from this lane (with its amount
  and slot); 2 = routed from another lane; 3 = full. 0x0815973C walks the object's routings (0x081321F8) for the key.

**The toggle stores a routing (S, M: `FmTargets.test_a_lane_on_each_destination`).**
- PR_LANE_TOGGLE (0x0815A1BC, PIANO_EVENT case 0x118, (view, amount)) acts on the selected entry (list + 0x10EF04).
- **On:** it takes the first matrix slot 0..2 that has no routing for this (handle, key). Through UI_POST it posts the
  model's 0xD1 `{handle, key, seq id | slot << 16, amount}`: the same event MX_EDIT posts (only the key's high half, an
  unread flag, differs).
- **Off** (amount 0, on an entry in state 1): it posts the same event with source 0 on the entry's slot.
- So **a lane is stored exactly as a Mod Matrix routing:** a type-1 node in the oscillator's routing list
  `{key, source 9..12, slot 0..2, amount}`. There is no separate lane store. M: the toggle on OSC1 Sub FM gives the node
  `(0x18B, 9, 0, 1000)`.

**What reaches the M7 (S, M).**
- MX_SET_ROUTE calls MX_KEY_POST, which posts 0x3F `{+2 instance, +8 track handle, +0xC key, +0x10 slot, +0x14 source,
  +0x16 amount}`.
- M: for a lane, `(track, 0x18B, slot 0, source 9, 1000, instance 0)`. The removal posts **slot 3**, source 0
  (MX_SET_ROUTE sets r5 = 3 when the removed node's source is 9..12, 0x08134890).
- On the M7, WT_ROUTE puts a seq source in connect slot 3 whatever the matrix slot (X2, S).
- So a lane is X2's path exactly: **the same route message X2's slot-10 hook handles**, in slot 3, with the root source
  root + 0x140 + 8 lane. It is not a direct parameter set. X2's x2_connect already keeps slot 3, and the removal's slot 3,
  source 0 clears it.

**The project format (S, M: `test_lanes_save_and_load`).**
- Stock's writer writes `<modsource dest="bento_subfm|bento_fm12" src="seq1..seq4" slot="0" amount=".."/>` in the
  oscillator's cell, as for any routing. Stock's reader restores it. The list after a load shows each lane (state 1, the
  amount).
- The lane's **values** are not in the routing. They are the clip's `seqevent type="auto"` records (kind 2: value = lane,
  value2 = the level; `logs/v322_HUBauto_project.xml` has some, test_step_xml keeps their value2). Nothing about them
  changed.
- On stock 1.6.8 and BE4.44 a lane loads as X1 says for any of these routings: stock gives key 0 (inert) and drops it
  when it saves; BE4.44 keeps it, inert.

**How the values move on the M7 (S, M).**
- Stock's sequence player posts a lane event as 0xA8 `{+8 lane, +0xC value}` to the track's bus (M: the player run with
  one kind-2 record, scratch x3/probe_auto.py: `a8 .. lane .. f4010000` for value2 500).
- The Wavetable root's message loop does not handle 0xA8: it ignores types above 0x4F except 0x84 (S). It reads the lanes
  instead. **WT_ROOT_BLOCK** (the root's slot 2, 0x0807F2A0) asks the object at root + 0x13C for each lane 0..3:
  `vt[0](object, [root + 0x284], lane)`. It keeps s0 at **root + 0x144 + 8 lane**, the seq1..seq4 value that WT_ROUTE
  points the connect at. Then it runs the root's block (0x0807AC60).
- The object at root + 0x13C is the engine, stored at 0x08058BE0 right after WT_ENGINE_INIT (S).
- So the value is read **once per root per block, before any voice renders**, and x2_enter reads it in the same block
  (M: `FmLanesX3.test_stock_reads_the_four_lanes_into_seq1_to_seq4` runs stock's lane read).
- **Not traced (I):** how the engine turns the 0xA8 events into the value it returns (held, or slid by "Mod Seq Slide").
  It is stock's and the same for every destination. The rig stands in for the engine with an object that answers the
  lane's value.

### Design

**The list.**
- The enumerator's two literals (MX_WT_DEST_LIT_3 / N_LIT_3) now point at our table and count word, as the other users
  did since X1 (MX_DEST_KEEP is empty).
- The builder's type match (PR_DEST_MATCH, 0x08159DA4: `ldrb.w r3, [sp, #0x26]; cmp r3, r8`, then stock's bne) becomes
  `bl wrap; cmp r0, #0`. The wrapper (synth UI block) calls **`pr_dest_skip(frame, r4, r8, sb)`** (m4_synth.c).
- Any row other than our three keys on a type-6 child gets stock's test (type == the child's type).
- Our keys are kept only when the table row's instance is the child's:
  - the table row is `enumerator index - 30 - Granular's count`, the enumerator's own rule;
  - the child's instance is the number of type-6 siblings before it, DEST_NAME's rule.
- Result: OSC1 gets Sub FM, and OSC2 gets FM 1>2 then Sub FM, each right after its Position. That is the Mod Matrix's
  order. There is no OSC1: FM 1>2.
- Every other entry, and every other track type's list, is stock's.

**Refusals (build.py `pr_dest_patch`).** The build refuses:
- a site that is not stock's type match;
- a type match not followed by the bne to the next row;
- an enumerator whose bytes (code and literal pool) do not hash to PR_DEST_ENUM_SHA, the code whose index rule was read;
- enumerator literals that do not hold Granular's count word, stock's count word and stock's table.

**Why this way and not another.**
- *Index arithmetic vs. a second, piano-only table.* A table ordered for type-only matching (OSC2's FM 1>2 before a single
  Sub FM row) would avoid the index rule. It costs about 600 B of M4 and a fourth copy of the rows. The arithmetic is
  guarded by the enumerator's hash, so it was preferred.
- *Filtering only our keys vs. every type-6 row by instance.* The two give the same stock entries in the same order. Only
  our keys were filtered, so stock's rows still go through stock's exact test.
- A BE_DISABLE=analog bisect keeps X1's literals (it did since X1), so pr_dest_skip filters there too.

**The M7: nothing new.**
- A lane on one of the three is X2's routing in slot 3: X2's store, its sum (Q4: `v = clamp(knob + 1000 x amount x lane)`)
  and its gates.
- The M7 and the DSP of X3's image are X2's, byte for byte. The untagged image differs from be445x2rrel only at
  0x0812EA44 and up (M4): the synth UI block, the moved table's literals and the bl displacements into the block.

### Tests (M, ~/bento/out/be445x3)

**test_native_props `FmTargets` (X3, +4; X1's `test_the_piano_roll_list_is_stocks` replaced):**
- `test_the_piano_roll_list_offers_ours_on_their_own_oscillator`: PR_DEST_LIST run on this build and on stock. The list is
  stock's 23 entries with T0's rename, plus ours at their places; no OSC1: FM 1>2. The enumerator's code is stock's, and
  the site is bl, cmp, then stock's bne.
- `test_every_other_tracks_list_is_stocks`:
  - three device projects (logs/) read by stock's reader on both images and made live; every track's list is equal,
    entry for entry, on this build and on stock;
  - Granular, multisample and one-shot tracks have lists; loop and type 0x16 tracks have none, here as on stock;
  - a Wavetable track's 17 other entries (the sub, filters, envelopes, LFOs) are stock's.
- `test_a_lane_on_each_destination`, for lanes A, B and D on OSC1 Sub FM, OSC2 FM 1>2 and OSC2 Sub FM:
  - stock's toggle gives the 0xD1, the routing node and the 0x3F to the M7 (track, key, slot 0, seq id, amount,
    instance);
  - the list shows each entry on its lane;
  - off removes the node and posts slot 3, source 0.
- `test_lanes_save_and_load`: the three `<modsource src="seqN">` elements are written; reading them back restores the
  nodes, and the loaded list shows each lane.

**test_analog `FmLanesX3` (4):**
- `test_stock_reads_the_four_lanes_into_seq1_to_seq4`:
  - stock's WT_ROOT_BLOCK, up to its bl, asks the engine object for lanes 0..3 with [root + 0x284] and keeps the values at
    root + 0x144 + 8 lane;
  - with no engine it reads nothing;
  - WT_ROUTE stores seq1..seq4 as root + 0x140 + 8 lane in slot 3.
- `test_a_lane_plays_its_destination_per_block`, for 3 destinations x Analog / Wavetable:
  - per block, stock's lane read drives the lane, the other lanes are busy, a Position lane on OSC1 plays, and the other
    FM knob is at 300;
  - the voice is bit for bit the same routing from velocity (X2's path), and for OSC1 Sub FM / OSC2 FM 1>2 the knob
    turned to v;
  - msub / m12 after each block is x2_law(base, amount x lane) and moves;
  - the store holds the lane in slot 3 and nothing else; the Position lane alone stores nothing of ours.
- `test_two_lanes_on_one_destination_keep_the_last`: stock's shared slot 3 (X2 open 2).
- `test_cost_heaviest_voice_lanes` (below).

### CPU (M, `FmLanesX3.test_cost_heaviest_voice_lanes`)

The voice is X2's heaviest: Wavetable both, Position every block, depths at 1000 (lanes at 1.0, amount 1000: the same
sound as the knobs, asserted). Instructions per voice-block, steady median / max:

| Heaviest voice | note 24 | note 36 | gate |
|---|---|---|---|
| knobs (today's path) | 34,889 / 34,891 | 34,879 / 34,881 | |
| 2 lanes (seq1 on OSC1 Sub FM, seq2 on OSC2 FM 1>2) | 35,148 / 35,150 | 35,138 / 35,140 | 35,309 |
| + seq4 on OSC2 Sub FM | 37,288 / 37,290 | 37,288 / 37,290 | 37,700 (X2's SUB2_HEAVY_MAX) |

- These are X2's routed figures exactly: a lane is a routing, and the source kind does not change x2_enter.
- **Two lanes: 159 under the 35,309 ceiling.** X2's worst two-key case (all slots and a full store) is 35,260.
- **OSC2 Sub FM from a lane is 5.6 % over**, the same known miss as X2 open 5. Lanes make it easier to reach (a lane is
  one tap), which is a reason to settle open 5.
- The lane read itself is stock's: once per root per block, with or without lanes.
- Nothing routed: unchanged (no M7 byte changed). P3Identity, X2Identity and WtFmW4 run in the suite.

### Sizes (M, ~/bento/out/be445x3)

- **Synth UI block: 16,762 B** (X2 16,542: +220 B). That is pr_dest_skip and osc_instance, the 24 B wrapper, and the
  block's layout.
- **Image 0x1897E4: 15,184 B to PROVEN_MAX** (X2: 15,408). This is the M4 room left.
- M4 code 24,424 B, M7 C slice 15,368 B, DSP 32,860 B (7,844 B free): unchanged.

### Images

- Untagged `~/bento/out/be445x3` (label BE4.45, the one every suite ran on): sha256
  f255663aa831bdbe1143476e4c5f1339ed5a1101481d947681792c8f342ea6e9.
- Sprint image `BE_TAG=X3` (`~/bento/out/be445x3t`, marker "v4.45-X3"): sha256
  54d0d1a2bcd1fb4f1fff0e21434389081586ec68ed8d0276c66f8f26b078d734. No kit was packaged.
- The 1.6.1 build and a BE_DISABLE=analog bisect build still build.

### Suites

From the worktree's mods/m1 on ~/bento/out/be445x3 (skips in brackets). All OK:
- diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20);
- notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10;
- **native_props 34** (X2's 31 - 1 replaced + 4), **analog 237** (X2's 233 + FmLanesX3 4), inst_xml 5 (5),
  synth_ui 64 (2), handles 16 (16), note0 32;
- p3_golden 52.
- Drums: not run (the default build leaves them out).

Mutants of pr_dest_skip (scratch x3/mut.sh, a build each): the instance test dropped (every type-6 row kept, the
pre-review X1 list), and only "OSC1: FM 1>2" dropped (the Sub FM-first order on OSC2). Each fails
`test_the_piano_roll_list_offers_ours_on_their_own_oscillator`.

### Open (for Jeff)

1. **OSC2 Sub FM from a lane on the heaviest voice is 37.3k, 5.6 % over the ceiling** (X2 open 5). It is offered
   because it was approved. If the device's 8-voice figure (checklist section 6) shows OVER, the choices are X2's: accept
   it, restrict OSC2's routed Sub FM to an Analog OSC2, or a cheaper two-term kernel.
2. **OSC2's list order is FM 1>2, then Sub FM**, as on the Mod Matrix page.
3. **Two lanes on the same FM depth:** the piano roll does not make the second. An entry already routed from another lane
   is in state 2, and stock's toggle does nothing on state 2 (0x0815A1FC). Two seq lanes routed to one depth from the
   Mod Matrix keep only the last (stock's slot 3 rule, X2 open 2; `test_two_lanes_on_one_destination_keep_the_last`).
4. **Not traced (I): the engine's 0xA8 -> lane value step** (held or slid). It is stock's and destination-independent.
   The device checklist (section 6) covers how it sounds.
5. **Device check:** checklist-v4.45.txt section 6 (the list on each oscillator, a lane per depth, save / reload, CPU
   with lanes).

## Tests added in X1

- **test_native_props `FmTargets` (9):**
  - the table: stock's with ours, the names, a blank CC, the literals (ours, and the enumerator's left stock's), our
    count word and the two count literals on it, stock's count word still 23, the bounds, stock's table untouched;
  - the rows on the page, on this build and on stock (Q7);
  - DEST_NAME names ours and reads our table (a memory-read hook: the name alone cannot tell the tables apart, since
    with stock's table DEST_NAME falls back to the registry title, the same words);
  - the piano roll's list (0x08159CA8, run on this build and on stock): stock's entries exactly, none of ours, the
    enumerator's bytes stock's (X1 review);
  - the per-type key post;
  - create and read back, and remove;
  - save and load;
  - stock 1.6.8 loads as key 0 and re-saves without dest;
  - BE4.44 loads with keys and keeps them (BE_REF444, default ~/bento/out/be444w4f).
- **test_analog:** `ShapeMod.test_x1_fm_destinations_are_inert_until_x2`.

## Tests added in X2

- **test_analog `FmTargetsX2` (12):** the trampolines installed (stock's words were stock's functions); every source of
  the Wavetable list at a fixed voice offset or in the root, and the store decoding to stock's pointer in stock's slot for
  all 10 voices (the table above); only WT_ROUTE stores, only our three destinations, slots 0..3, the per-type post's
  slot 3 clear, OSC1's FM 1>2 ignored, stock's keys still connected; routed = the knob, bit for bit (Analog OSC1 Sub FM,
  Analog OSC2 FM 1>2, Wavetable OSC1 Sub FM, Wavetable OSC2 FM 1>2 from either OSC1); knob 0 plus a routing renders FM (3
  destinations x 2 types); OSC2 Sub FM only routed; removal and clear = the knob to 0 (8 cases); per root and handle, the
  store full, a freed root's entry reclaimed, x2_lost once a routing; the cost.
- **test_analog `X2Identity` (1):** nothing routed, BE4.44 byte for byte (8 cases; BE_REF444, default
  ~/bento/out/be444w4f); `WtVoice` takes a rig for it.
- **test_analog `ShapeMod`:** X1's inert test is now `test_slot_10_called_from_outside_the_route_handler_keeps_nothing`
  (the same checks: a direct call stores and writes nothing).
- **X2 review, test_analog `FmTargetsX2` (+9, now 21):** each voice reads its own sources (two voices of one root);
  root sources, seq1 in slot 3, three slots summed and the rounding against `x2_law` (the law in float32, with a
  searched case where the final fused rounding gives 764 and two roundings 765), the other key's knob at 300; a key
  routed only in slot 2 or by a seq lane; routed OSC1 Sub FM under mixed sync (D3); every amount -1000..1000 through
  WT_ROUTE, and +-1000 past it; OSC2 Sub FM's removal; the tidy over all the root's voices (two voices, remove and
  clear, three type pairs); the connect guards; the heaviest voice routed (`test_cost_heaviest_voice_routed`). `pair()`
  takes OSC2's Sync and extra knobs. test_analog takes test names after its two paths.
- **X2 review, test_synth_ui `BlackBox`:** the ANALOG line's `x2lost=` (+24), +28 still not printed.

## Plan items found wrong (feasibility-wt-fm-matrix.md, part b)

1. **"Names come for free from the registry titles ('WT1: Sub FM')".** The rows do take the registry title, but under stock's "WT1:" format. BE4.44's page says "WT1: Pitch" (Q7, M). X1 uses "OSC1: / OSC2:" for all oscillator rows.
2. **"Row-id byte: unknown use; 0x13..0x4B are taken".** It is the CC column, display only, and not every id in that range is taken (Q8 above). New rows: 0 (blank).
3. **"A rollback to BE4.40 loads it as key 0".** That holds for BE4.40 (I). BE4.41 .. BE4.44 have the tokens, keep the keys, and save them back (BE4.44: M).
4. **"Stored as a type-1 child node".** It is in the object's routing list (obj + 0x14, linked by + 4), not in NODE_CHILDREN.
5. **Not in the plan:**
   - the table also feeds the piano roll's destination list (0x08159CA8, S), which has no notion of instance; X1 keeps
     that list on stock's table (above);
   - 0x08134700's per-type 0x3F slot-3 posts now carry our keys, by type only (OSC1 gets 0x132 too). X2 must handle
     slot 3 and ignore 0x132 on instance 0.
6. **Confirmed as the plan said:**
   - the four literal users and three count users (complete, M scan);
   - the row filler's `#0x17` / `#0x228`;
   - the view's capacity (60 rows).

### Found wrong at X2 (part b and this doc's X2 plan)

7. **"Storage per root: amount plus source as a voice-relative offset, or an absolute address if the source is global".**
   No source of the Wavetable list is global: each is at a fixed voice offset or in the root (M, the table above). The
   store keeps voice- or root-relative offsets only; anything else is not kept and counted.
8. **"3 keys x 3 slots".** Stock's connect has 4 slots: WT_ROUTE puts seq1..seq4 (ids 9..12) in slot 3 (PARAM slot 4)
   whatever the matrix slot (S, 0x0807CEF0..; M). The store keeps 4 per key, as stock's PARAMs do. This also settles the
   plan's open "slot 3 messages from 0x08134700": stored as stock stores them, so that post's source 0 clears slot 3.
9. **"About 1.2 KB" of store** (16 roots x ...). Built: 8 entries x 56 B = 448 B (8 tracks: one Wavetable root each), in
   the run-state page.
10. **"DSP about 0.8-1 KB".** Built: 1,768 B (the message-loop functions at minsize).
11. **Per root, but slot 10 carries no root.** The plan did not say how the connect finds it. Built: the trampoline takes
    WT_ROUTE's r4, only for a call that returns into WT_ROUTE (S; the build checks WT_ROUTE's bytes by sha256).
12. **"Slot 9 (clear) runs per voice with no key; X2's store needs its own clear on the same path, or an epoch".** Own
    clear, as planned. But the connect and the clear both come per voice, and the entry can go at the first voice, so the
    tidy (untagging, OSC1's feed of B for OSC2's ramp down) runs over all the root's voices at once.
13. **"OSC2 Sub FM's base is its saved value".** True for an Analog OSC2. A Wavetable OSC2's stored Sub FM is not on the
    M7 (BE4.44 W4 D2), so its base is 0 (open 1).
14. **"The FM gate also checks has routes".** One gate in the plan; four in the code: an_osc's FM word, wt_fm_osc's gate,
    feeds (an Analog OSC1 feeding B) and wt_body's feed-B test (a Wavetable OSC1).
15. **be445 S1's source listing** (scratch out_ids.txt, used by the FM Env sources feasibility) has ids 9..12 at
    voice + 0x79C. It read OSC1 Pitch's slot 1, which still held id 8's pointer: ids 9..12 go to slot 4, from the root
    (above). Only id 8 is voice + 0x79C.
