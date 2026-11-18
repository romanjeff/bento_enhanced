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

## Sprint X1 (M4): the table, rows, names; routings stored, inert in sound

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

  The table is 26 x 24 B plus 40 B of strings, at the end of the synth UI block (after N1's template): 664 B (M).
- **Patches:**
  - the four literals point at our copy;
  - the count word holds 26 (patched in place, so the three count literals stay as they are);
  - `movs r2, #0x1a`;
  - `addw r7, r4, #0x270`.
- **Refusals.** The build refuses a stock table whose layout, oscillator rows or count differ from the above, and refuses one that already holds our keys.

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

**Also seen (S, M).** DEST_NAME's own list (0x08159DCC: the destination list of the view at + 0x11CD08, the piano roll's) lists every table row of a child's type. So it now offers "OSC1: Sub FM", "OSC2: FM 1>2" and "OSC2: Sub FM" as well (`FmTargets.test_dest_name_lists_them_too`).
- What a lane on them does is X2's business. Until then nothing routes them.
- Device check: whether that list is the piano roll's automation-lane picker, and what a lane on a WT destination stores.

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

**Firmware outside the M4 is byte-identical to BE4.44 (M, an image compare against be444w4f).** The only differences are:
- the four literals, the count word and the two row-filler bounds;
- the version digit (two bytes);
- the 664 B appended (the table and its strings).

The M7, the DSP window and every M4 code byte are unchanged.

### Sizes (M, ~/bento/out/be445x1)

- **DSP:** 30,884 B, **9,820 B free** of 40,704 (unchanged).
- **M4:** M4 code 24,424 B and M7 C slice 15,368 B (unchanged). The manifest's synth UI figure is now 16,510 B, which is 15,846 (code and N1 template, unchanged) + 664.
- **X1's table:** 664 B in the synth UI block.
- **Image:** 0x1896E8, **15,436 B to PROVEN_MAX** (BE4.44: 16,100). That is the M4 room left for X2's M4 part and for P4.
- **Kit image:** `BE_TAG=X1`, sha256 e22fd49c992f4ed50e12447442a0b6ee8a651da217494431eb9a2495b6539e6f (not a release kit).

### CPU (M, the suite on be445x1)

There is no M7 or DSP change (byte-identical, above), and the measurements are BE4.44's:
- P3Identity, 48 cases: worst **1.0165** (mod dev=False, 111,708 / 109,899), total 1.0087. The stop line is 1.95 %.
- WtFmW4 voices: the heaviest steady voice is 34,760 (note 24, light FM, Position every block), against the 35,309 ceiling. Its one-block maximum is 39,392.

The routed cost per voice-block belongs to X2: nothing runs for these keys yet.

### Suites (M, ~/bento/out/be445x1, from the worktree's mods/m1; skips in brackets)

All OK:
- diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20);
- notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10;
- **native_props 30** (22 + 8), **analog 211** (210 + 1), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32;
- p3_golden self-test 52.

Drums suites: not run (the default build leaves drums out). The 1.6.1 build still builds (no analog group there, so no X1 patch).

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

- **test_native_props `FmTargets` (8):**
  - the table: stock's with ours, the names, a blank CC, the literals, the count, the bounds, stock's table untouched;
  - the rows on the page, on this build and on stock (Q7);
  - DEST_NAME;
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
   - the table also feeds DEST_NAME's list (the piano roll's, I);
   - 0x08134700's per-type 0x3F slot-3 posts now carry our keys. X2 must handle slot 3.
6. **Confirmed as the plan said:**
   - the four literal users and three count users (complete, M scan);
   - the row filler's `#0x17` / `#0x228`;
   - the view's capacity (60 rows).
