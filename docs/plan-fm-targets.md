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

**Open (for Jeff):** piano-roll lanes on Sub FM / FM 1>2. They would need an M4 hook in the builder's type match that
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

## Sprint X2 (M7 / DSP), the plan carried forward (not built)

**Storage per root.**
- Take amount plus source per key, per slot, as a voice-relative offset, or an absolute address if the source is global.
- **First verify that the per-voice sources sit at fixed voice offsets.** WT_ROUTE resolves `env[i]` at voice + 0x184 + 0xFC i, the LFOs and so on per voice (S, be445 S1's note). That has to be checked for every source id the matrix offers.

**Hooks.**
- Slot 10 (0x08081B60) gets a trampoline for 0x18B / 0x132 only. Every other key goes to stock.
- Slot 3 messages from 0x08134700 must be accepted and ignored, or stored as stock does for its fixed slot. Decide from what stock's PARAM slot 4 means (S, to trace).
- Slot 9 (clear) runs per voice with no key. X2's store needs its own clear on the same path, or an epoch.

**Per block.**
- Compute `v = clamp(knob + 1000 * sum amount_i * src_i, 0, 1000)` for each of the three keys of a routed oscillator.
- Feed the result to the existing msub / m12 ramp (Q4: travel, then the square law).
- The FM gate also sees "has routes", so a routed oscillator with knob 0 renders FM.
- OSC2 Sub FM's base is its saved value.

**Identity.** With no route on these keys, today's path must run bit for bit: P3Identity, FmEnv, WtFmW1..W4 unchanged.

**Costs to measure and report:**
- the routed cost per voice-block;
- the cost with no routes (about 0: one flag load);
- the DSP bytes, keeping >= 1 KB free.

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
