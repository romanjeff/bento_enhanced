# Plan S6n: Analog as a native per-oscillator Type (1.6.8)

Jeff (2026-11-09): showing Analog as its own instrument, when it is really a Wavetable track that we've fooled, is hacky; "we can do better". This plan judges two changes, (A) native properties and (B) a per-oscillator Type, and sets out how to build them. A genuinely new track type is out of scope and appears only as the contrast (section 9).

Base: HEAD 455b9ba (BE4.31 code at fbd0443, plus two docs commits), stock 1.6.8.

Evidence labels:
- **M**: measured, i.e. run in the emulator on stock code.
- **S**: static, read from the image.
- **I**: inferred.

The investigators' scripts:
- (A) `scratchpad\rc32\natprops\`: `np.py 1.6.8 patched|stock`, `np161.py`, `m7.py`.
- (B) `scratchpad\rc32\invB\`: `p3.py`, `p4.py`, `p6.py inject|stock`, `p7.py`, `p8.py`.

Both folders are under `C:\Users\rjrom\AppData\Local\Temp\claude\C--Users-rjrom-bento-mods\69931413-5dfa-4316-a637-0fee2cef4235\`. The designer ran no new emulator jobs: every M below comes from those runs. `docs/plan-drum-synth-track.md` did not exist when this was written (section 5; **[V] was "section 7"**).

## Verdict

| | Verdict | Why, in one line |
|---|---|---|
| **(A) Native properties** | **GO** | Stock already stores, saves, loads, copies and forwards to the M7 any property an object has. Our keys only need to be in two of stock's tables (M, end to end on a patched stock image). |
| **(B) Per-oscillator Type** | **GO, on top of (A)** | WT1 and WT2 are separate stock objects, each with its own handle. Edits by handle go to index 0 / 1 and reach each voice's WT1 / WT2 (M). **[V] The page side is S/I, not M:** the 0xCF forwarder 0x08161158 copies the widget event's handle (ev+4) into the 0xD0 unchanged (S). That each pane's widgets carry their own oscillator's handle is inferred from stock: OSC2's Position (the same key 0xC9) must edit WT2. No script ran the page. Type becomes one more property of each. The 9th menu entry goes. |
| (B) without (A) | not recommended | It works on AN4 (2 Type bits per entry), but every handle hook stays. It is the same hack with more bits. |
| Undo | not on offer, no loss | Stock 1.6.8 has no undo for parameter edits at all (S: the undo actions 0x1034 / 0x1044 are handled only in the sequence, piano and drum views). Today's Analog has none either. |

**[V] Undo row:** partly re-derived (S). `movw #0x1034` / `#0x1044` appear at only 13 sites in the whole image, 0x08158124..0x0815F530, all view code and none in the model. I did not re-derive which views those are.

### Verification (adversarial review, 2026-11-09)

The reviewer's own scripts are in `scratchpad\rc32\verif\` (same parent folder): `stat.py`, `callers.py`, `base.py`, `t1.py`..`t6.py`, run on stock 1.6.8 (1.6.1 where noted). Corrections in this file are marked **[V]**.

| Claim | Verdict | Evidence |
|---|---|---|
| Our keys can be in the template, and every stock object keeps its array | **confirmed** | M (`t1.py`): the **full** set of 7 keys was appended to the type-6 run (0x304 entries, 1 pad), with the patch applied before the first creation. Of 50 objects (every template type as a child, plus 10 track types), 49 are key-for-key, kind and default identical to stock. Type 6 = stock's 7 keys in order + our 7 with defaults (Shape 48). |
| The template has no other users or bounds | **confirmed** | S (`stat.py`): the only words pointing at the table start are 0x0812EA44 / 0x0812EB4C. Each is loaded twice: 0x0812EA0C uses EA44, and 0x0812EA48 uses EB4C at 0x0812EA68 and 0x0812EAC6. A sweep of 0x08100000..0x08199000 for #0x2FA..#0x2FD finds only the three bounds named. Three more words (0x08130748, 0x08130E98, 0x081356F0) point at 0x0819AA34 = exactly the table end. That is a **separate** 5-key u16 list (0x1A9, 0x1AA, 0x1A6, 0x1A5, 0x18C) used by the writer and reader, unaffected because N1 leaves the original table in place. The original table must never be reused as free space. |
| Stock's project writer and reader carry them by themselves | **confirmed, at project level** | M (`t2.py patched`): the real project writer 0x08131220 ran with only the file write 0x0813184C stubbed, and wrote all 7 tokens on both oscillator `<cell>`s. The real project reader 0x081313EC ran with only the file read 0x0813190C stubbed (stock's own parser 0x0810AB76 was fed the text). It restored every value on WT1 and WT2 (14 of 14), plus wavepos. The investigators had run only INST_LOAD on hand-built cells. |
| Clone / CHILD_DUP copy them | **confirmed** | M: NODE_CLONE gave new handles (track 4 -> 30, WT2 6 -> 32) with all 14 values kept; CHILD_DUP (handle 56) kept all 7. |
| Setter cannot add a key | **confirmed** | M: 0x0812ECC2(track, 0x1C0, 1) leaves the count at 44 and the key absent. |
| 0xD0 edits forward every key with index 0 / 1 | **confirmed** | M: 0x08132B78 for all 7 keys on WT1 and WT2 gives set_property(track 4, key, value, 0 for WT1 / 1 for WT2). |
| ...and stock clamps the value | **refuted (new finding)** | M: 0xD0 stores and forwards 5000 and -3 unchanged, for every key. INST_LOAD also stores out-of-range file values raw, stock's own included (`t2.py range`: bento_bits=99, bento_osc_type=-7, wavepos=99999 are all stored as is). The M7 entry 0x08059960 case 0x3E has no clamp either (S). The M7 and M4 readers of our keys must therefore treat every value defensively: Type is Analog only when == 1; Bits, Shape, Glide and the FM amounts are clamped where they are used. |
| The M7 entry and synth root deliver our keys intact and stock ignores them | **confirmed** | S: case 0x3E sends keys < 0x1F2 to the broadcast-flag test at 0x08059A40, then the handle test at 0x08059988. Only handle 0xFFFFFFF1 reaches the 0x18C..0x1B6 store; any other handle is posted to the track mailbox (0x0809F8A0). M (`t4.py`): root 0x0807BC20 with each of the 7 keys gives index 0 -> both voices' WT1 slot 8, index 1 -> WT2, index 3 -> none, with **zero** memory writes outside the stack. The reference 0xC9 writes each oscillator. Index 2 (the sub) was not run. |
| Stock ignores the new attributes on load | **confirmed** | M: 1.6.8's full project reader on the patched file keeps stock's 7 type-6 keys, loads wavepos=321, and a stock re-save drops every `bento_*`. M (`t6.py`): INST_LOAD of 1.6.1 (0x0812FAAC) and of 1.6.8, with all 7 new tokens plus the three BE4.22 names, reads only the two stock attributes, keeps the count, and logs nothing. |
| BE4.22..BE4.31 open a new file as plain WT | **confirmed (S, our code)** | `an_load` reads `bento_analog` on every object's params. With it absent it clears the entry, so the new names are ignored. |
| The padded size must be 0x308 or more | **refuted** | 0x2FC + 7 = 0x303, and 0x304 encodes. Checked by keystone with a capstone round trip and run end to end. Every multiple of 4 from 0x300 to 0x3FC encodes (imm8 0xC0..0xFF << 2), so the pad is never more than 3 entries. |
| Type 0xFF pad is inert | **corrected** | M: stock's CHILD_CREATE(0xFF) fails ("Unable to initialize cell"). With the pad it **succeeds**, making an object with one property (key 0). This is harmless only while nothing asks for type 0xFF. The reader decodes cell types by name (0x08131168), so test that no name maps to 0xFF. |
| Emulator method | **note** | Unicorn keeps translated blocks: patching 0x0812EA48's bounds after it has run once left type 47 (the last run) with 7 of 14 keys (M, `t1.py` first version). test_native_props must run on the built image, or patch before any creation. |
| Project load reaches the M7 | **unsettled** (still P0); settled in P0 results: M | S: the file task loads into a scratch store (0x08127ACE -> 0x081313EC, store at task+0x330) and posts the result with 0x08100590. The adoption into the live model and its push were not traced. Candidates are the 0x0813E4B8 callers 0x0813FA1C / 0x0813FEC8. The push looks generic: there are 9 `bl` sites of the property iterator 0x0812EE5E at 0x0813E64C..0x0813F22A. A backward push-scan attributes them to 0x0813E4B8 (S, a coarse attribution). |
| Boot order risk (section 7, row 1) | **resolved by reasoning** | N2 registers our tokens inside the same REG_BUILD call (the only `bl` to 0x08165848 is at 0x08131FD4, S). So our tokens exist whenever stock's do. A load before REG_BUILD would lose stock's own parameters too. |
| Not re-derived | **unsettled** | M7 free bytes, the mixed-voice rules, T5 (live row-key change), flash budget (19,780 B), the drum section, undo per view, and the migration conversion itself. M7 free bytes, T5 and the flash budget: settled in P0 results (M). |

The main reason for GO: the BE4.21 bug class (copies renumber handles, so our side table loses track of them) **stops being possible**, because the state lives inside stock's own object. Today we guard against it with four hooks and a 16-entry table. After this change there is nothing left to guard.

## P0 results (measured 2026-11-09, recorded 2026-11-14)

The implementer's probes are in `scratchpad\rc33\implA\p0\` (same parent folder). Re-run on 2026-11-14 on the working tree's build (`b.sh p0doc 1.6.8`; image 0x187F98). test_native_props passed (14 tests, 1 skipped: no BE4.22 kit), and test_analog passed (29 tests).

| P0 item | Answer | Label | Evidence |
|---|---|---|---|
| Boot order: REG_BUILD vs the boot project load | No risk. N2 puts our descriptors inside the only REG_BUILD call, so they exist whenever stock's do. Where the real boot load sits relative to model init was not traced. | S (+ M for the order the rig uses) | `dump.sh`: 0x08131FD4 `bl 0x08165848` is the only call (REG_BUILD_SITE). test_native_props' Rig runs model init's registry build through the N2 wrapper, then every Project test reads a project. |
| The project-load push path | The load handler commits the scratch store with STORE_COPY_ALL (`bl` at 0x0814094A, inside the load commit 0x08126768), then calls LOAD_PUSH_ALL 0x0813FEC8 (`bl` at 0x08140998). That runs PROP_PUSH 0x0813E4B8 (model, handle, 1) for each top-level object (0x0813FFBA). It is the plan's candidate 0x0813FEC8, and the push is the same per-child loop as after a create. | M (WT1), I (WT2) | test_native_props `test_the_push_after_a_load_reaches_the_m7`: WT1's 4 keys arrive with index 0 and the loaded values (1, 127, 0, 77), after stock's 0xC9 = 321. The push stops at WT1's file reference because the sample loader is stubbed, so WT2 is covered only by PROP_PUSH (`test_the_push_after_a_create_sends_type_first`). `p0/x2.sh`, `x3.sh`, `dbgpush2.py`. |
| M7 value integrity | Values arrive unchanged on the M4 side and from the root to slot 8. an_prop clamps them where it stores them. The M7 entry 0x08059960 case 0x3E was not run, only read (no clamp). | M (M4, root to slot 8, an_prop), S (M7 entry) | test_analog `Props`: `test_stocks_root_sends_each_key_to_the_right_oscillator` (WT_ROOT_PROPERTY 0x0807BC20, index 0 / 1 to all 10 voices' WT1 / WT2, the sub neither; 50 props counted) and `test_settings_are_clamped_where_they_are_stored` (Shape 1000 -> 127, -5 -> 0; Glide 5000 -> 1000; Sync 7 -> 0). rc32 `verif\t4.py`: the root makes no writes. |
| T5: stock's widgets take rewritten row keys without a page rebuild | **Yes**, on a refresh. SYNTH_ROW_BIND 0x08160A1C reads the shown row's keys again on every call. SYNTH_ROW_SHOW 0x08160E4C and notifications 0xC0 / 0xC3 / 0xC4 all bind the new keys; no 0xA8 rebuild is needed. A rewrite with no refresh is not picked up. The slot's object depends on the key: keys > 0xDA (except 0x151) and keys <= 0xC5 bind to the row's handle. All 4 of our keys are > 0xDA, so they bind to the row's handle. A key in 0xC6..0xDA does not (0xCB went to handle 15). Same result on the stock image and on the built image. | M | `p0/t5.py` (stock 1.6.8 page, ctor 0x0815FFD8): row 0 slot 2 0xC9 -> 0xC5 gives CONTEXT_GET (5, 0xC5) and title "Pulse Width"; nothing without a refresh. The page tick uses SYNTH_ROW_SHOW (m4_synth.c `page_tick`). |
| The oscillator's free bytes (write watchpoints) | **None outside the kernel area +0x10C..+0x1CC.** The bytes outside it that stock never writes after construction hold constructor values (pointers such as 0x080AD290, floats 1.0, coefficient runs). They are configuration, not free space (I). In this run the WT render wrote only +0x10C..+0x16B of the kernel area; +0x16C..+0x1CC stayed zero. **Contradicts 3.3 rule 1:** N3 uses neither a free byte nor an M7-local table. It keeps AnProps at the top of the kernel area (+0x1C0..+0x1CC), valid only while the oscillator is Analog (magic and Type == 1). Leaving Analog zeroes the whole area. Shape / Sync / Glide sent while the oscillator is Wavetable are dropped. They come back from the push (Type goes first, in template order) or from the page tick's `resend` when the page sees a Wavetable -> Analog change. | M (writes), I (the never-written bytes are in use), S (our code) | `p0/oscfree.py` on stock 1.6.8, voice 0 WT1 / WT2, 0x1F0 B each: 4 note starts, slot-8 writes of every stock key, 30 render blocks per note with a real table. test_analog `test_type_is_analog_only_when_exactly_1` (a Wavetable oscillator's area is left untouched) and `test_leaving_analog_zeroes_the_whole_area`. dsp/analog.h AN_PROPS_OFF 0xB4. |
| Template count encoding | P1 has 4 keys: 0x2FC + 4 = **0x300, which encodes with no pad entry** (`cmp.w #0x300`, `movw r7,#0x2ff`). The 0x304 / one-pad case comes with P3's 3 keys (0x303). | M | manifest `synth.np_template_n` = 0x300. test_native_props `Template` (4 tests: sites, copy = stock + ours after the type-6 run, every object's array, the setter cannot add a key). `dump.sh`: stock's type-6 run is entries 154..160. |
| Size of the removals (flash budget) | **Budget met; NP1 stays the table copy.** Same groups (drums built), HEAD (BE4.31 code) 0x1885F0 -> working tree 0x189958: +4,968 B, which includes the 6,144 B table (0x300 x 8). The removals therefore give back 1,176 B more than the N2 / N3 / page / resend code adds. **That is about 1.2 KB, not the "several KB" 3.2 inferred.** Default 1.6.8 build (drums now default-off): 0x187F98, **21,404 B free** (BE4.31: 19,780 B). With drums: 14,812 B free. | M (sizes), I (the net split: per-item removal sizes were not isolated) | `git archive HEAD` built to `~/rv33p0doc/be431` (19,780 B free, as noted); `b.sh p0doc 1.6.8` and `b.sh p0docdr 1.6.8 BE_ENABLE=drums`; proven max 0x18D334. |
| (3.4) Does the encoder widget clamp before 0xCF? | **Not settled.** ENC_TURN 0x08173FA4 maps a step position to a value through the descriptor type (0x081735F0), and the disassembly was not resolved into a yes or no. This does not matter for safety, because the readers clamp (an_prop M; the page: test_synth_ui, being rewritten). A9 is gone anyway. | open | `p0/x12.sh`, `x13.sh` (static read only) |

NP1 / NP2 / NP3: decided (Decision, end of file). NP1 = table copy (built), NP2 = drop the menu entry, NP3 = no migration.

## 1. What Jeff sees and does

- **The instrument menu goes back to stock: 8 entries, no "Analog".** Pick **Wavetable**.
- **On a Wavetable track, the two oscillator panes are titled OSC1 and OSC2.**
  - The pane text reads "OSC1: <wav name>", or "OSC1: Analog".
  - The title change is already decided in plan-analog-fm FD8. Here it applies to every Wavetable track, because every one can now be Analog.
- **Each oscillator row gets a Type control: Wavetable / Analog.**
  - OSC1 and OSC2 are independent, so OSC1 Analog with OSC2 playing a wavetable is a normal patch.
  - A new track starts as Wavetable / Wavetable, exactly like stock.
- **The rows change with Type** (proposed; slot 3 is free on 1.6.8 and slots 4..7 are free):

| Row | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| OSC1, Wavetable | Level | Pitch | Position | **Type** | Glide (NP4) | | | |
| OSC1, Analog | Level | Pitch | **Shape** | **Type** | Glide | Sub FM | Bit Depth | |
| OSC2, Wavetable | Level | Pitch | Position | **Type** | | | | |
| OSC2, Analog | Level | Pitch | **Shape** | **Type** | Sync | Sub FM | Bit Depth | FM 1>2 |

  - Shape (0..127, sine > triangle > saw > square > pulse) replaces Position on an Analog oscillator. It is a separate key, so Position keeps the wavetable frame.
  - Controls that would do nothing are not shown: see 3.3 for what each one needs. FM 1>2 needs both oscillators Analog, so on a mixed track it shows "--".
- **A wavetable loaded on an oscillator that is switched to Analog** stays loaded and is saved with the project. Switching back plays it again at its old Position.
- **Saving, loading, presets, copy / paste and track duplicate** all keep Type and every Analog control, through stock's own code.
- **The project file** gets attributes such as `bento_osc_type="1"` on each oscillator's `<cell>` params, written under stock's naming. Stock firmware opening the project ignores them and plays plain Wavetable (M).
- **Old Analog projects (saved by BE4.22 .. BE4.31)** open with both oscillators set to Analog and Sync / Glide kept (section 6), if Jeff wants that (NP3).

## 2. How stock stores properties (the evidence for A)

- **The objects.**
  - A track is a 0x92C B node (vtable 0x08199188).
  - Its children are 0x18 B objects made by CHILD_CREATE 0x0812F878.
  - Each object keeps its properties in its own array at +8, with the count at +0xC. Each entry is 8 B: {u32 value, or a char* for strings; u16 key; u8 kind, 1 = integer, 2 = string} (S).
  - A Wavetable track (type 0x12) gets 11 children from 0x0819923C: `06 06 08 0A 0A 09 09 0B 0B 0C 0D` (M).
  - WT1 and WT2 are two type-6 objects, each with its own handle and the same keys {0x03, 0xC4, 0xC7, 0xC9, 0x23, 0x26, 0x81} (M).
- **Which keys an object gets.**
  - The flash template 0x08199254 lists them: 0x2FC entries of {u8 type, u8 0, u16 key, u32 default}, one contiguous run per type. Type 6 is entries 154..160 (M).
  - 0x0812EA48 builds the array once, at creation.
  - The template is referenced only through the literal words 0x0812EA44 and 0x0812EB4C. Its bounds appear only as `cmp.w #0x2fc` (0x0812EA1C, 0x0812EA80) and `movw r7,#0x2fb` (0x0812EA6A) (S).
  - The setter 0x0812ECC2 only changes a key the object already has. **Setting a value can never add a property** (S), which is why a key must be in the template.
- **Naming.**
  - The registry has 498 descriptors of 32 B; the XML token is at +0x18.
  - Key to token is 0x08168558, token to key 0x08168580 (a token is at most 31 characters).
  - REG_BUILD runs once, from model init (`bl` at 0x08131FD4), which UI init calls at 0x08164B5A (S).
- **Saving.**
  - The instrument writer 0x08130484 writes every property under its token, and skips a property with no token (it logs "No Valid Description").
  - The project writer calls it for each `<track>` and each child `<cell>` (S).
  - 0x081301E0 is only the sequence writer (S).
- **Loading.** INST_LOAD 0x081309A0 matches each attribute's token to a key the object has (0x0812F11A) and skips the others (M). It reads every track and every cell, for projects and presets.
  - **[V]** The project reader 0x081313EC creates each track (0x0812E7C4) and loads it (INST_LOAD at 0x081315FC). For each `<cell>` it then runs CHILD_CREATE 0x08131514, so the cell gets the template's keys with their defaults, and INST_LOAD at 0x081314D0 (types 6 / 7, after setting 0x81 from the file name) or 0x08131528 (S; run end to end, M). Values are stored **unclamped** (M).
- **Copies.**
  - NODE_CLONE 0x0812F310 copies the whole array through 0x0812F164: count × 8 bytes, strings duplicated, plus the mod-route list.
  - CHILD_DUP 0x0812F91E (H4's site) uses the same 0x0812F164 (S, M).
- **Edits.**
  - The page turns encoder event 0xCF into 0xD0 {child handle, key, value}.
    - **[V]** The forwarder 0x08161158 copies ev+4 (the handle), ev+8 (the key; 0xD3 becomes 0xD2), ev+0xC and ev+0x10 as they are. It does no range check (S).
  - The model handler 0x08141EA4 sends it to 0x08132B78. That finds the child, counts its index within its run of same-type children (WT1 = 0, WT2 = 1, sub = 2), and calls set_property 0x0810B920(engine, track handle, key, value, index) for **any** key.
  - Then it sets the property and touches the project (marks it unsaved, S, M).
    - **[V]** It **does not clamp**: 5000 and -3 are stored and forwarded as they are, for all 7 keys (M, `t2.py`).
  - Keys with special cases in 0x08132B78 (0x36 / 0x37, 0x58, 0x143, 0x14E, 0x150) must be avoided.
- **Reads.** CONTEXT_GET's generic path, store get 0x0812EB96, answers any property of a child (M).
- **The full push.** 0x0813E4B8 sends every property of every child, with its index, to the M7. It runs after a menu create (0x0813F858), after a preset apply (0x0813F476), and from 5 other callers (M).
- **The M7.**
  - The engine input 0x08059960 (case 0x3E) posts the message to the track's mailbox.
    - **[V]** For a key below 0x1F2 it first checks the broadcast flag at engine+0x8CB2+key (0x08059A40), then the handle at 0x08059988. A track handle goes to 0x0809F8A0, which binary-searches the track and posts. No clamping (S).
  - The synth root 0x0807BC20 then sends it, for index ≤ 2, to every voice's oscillator[index] through vtable slot 8 (0x08081A80, word 0x080ACBD4).
  - Slot 8 handles 0x03 and 0xC4..0xCC and **ignores every other key** (M, S).
  - The root's own switch sends unknown keys to its default case (S, M). A forwarded key we don't handle is therefore harmless on unpatched M7 code (M).

### The patched-stock round trip (A's `np.py patched`, B's `p6.py inject`; M)

Both patches did the same thing, with the image patched in emulator memory only:
- relocated the template with one extra type-6 entry, padded to 0x300 entries;
- set the two literal words and the three immediates (0x300 / 0x2FF);
- added one descriptor with a real token.

| Check | Result |
|---|---|
| New WT1 / WT2 | both have the key, default 0 |
| Setter on WT2 | WT2 = 1, WT1 stays 0 |
| Clone | new handles (WT2 6 -> 22), values kept |
| Writer | `bento_osc_type="1" wavepos="321"` on the cell's params |
| Reader into a fresh WT1 | value restored |
| 0xD0 edit of WT2 | property set; set_property(track 4, key, 1, index 1) sent to the M7 |
| CONTEXT_GET | returns the value |
| Push after create | key sent with index 0 and with index 1 |
| M7 0x0807BC20, 3 voices | index 0 reaches each voice's WT1 slot 8, index 1 reaches WT2 |

The push run faulted later, inside the envelope child (pc 0x08121EE4), because of the harness stubs. That happened after every result above.

**[V] Re-run with the full key set** (`verif\t1.py`, `t2.py`, `t4.py`; M):
- All 7 keys of 3.1 were in the template: 0x304 entries, 1 pad, `cmp.w #0x304` / `movw #0x303`.
- All 7 had descriptors with real tokens.
- The writer and reader were stock's **project-level** functions (0x08131220 / 0x081313EC), with only the file I/O stubbed.
- Every row above holds for every key. Two additions:
  - nothing clamps values (see Verification);
  - the M7 root makes no writes for any of the 7 keys.

### Unknown attributes (asked)

| Firmware | Result | Label |
|---|---|---|
| Stock 1.6.8, stock 1.6.1 | INST_LOAD returns normally, reads `wavepos` / `level`, skips `bento_osc_type` and `bento_unknown_xyz`; the property count is unchanged; nothing is logged; stock's next save drops them | M |
| **[V]** Stock 1.6.8, the full project reader | the patched project opens: stock's 7 type-6 keys, wavepos kept; a re-save has no `bento_*` | M (`t2.py stockread`) |
| **[V]** Stock 1.6.1 / 1.6.8, all 7 new tokens + `bento_analog*` | skipped, count unchanged, nothing logged | M (`t6.py`) |
| v3.72 (on 1.6.1, with no X2) | same as stock 1.6.1 | I |
| BE4.22 .. BE4.31 | `an_load` (X2) reads only `bento_analog*` / `bento_drum_*`, then stock's reader skips the new names. A new project opens as plain Wavetable | S (our code) |

So rolling back to any earlier firmware is safe. A project saved by this design and then re-saved on old firmware loses its Analog settings, which is the same as today's behaviour with stock.

### Limits (asked)

- **The registry does not grow.** Our descriptors go into existing empty keys below 0x1F2, as today.
  - Growing it past 0x1F2 would touch 26 immediates across both cores, including the M7's own registry copy (0x08068B42..0x08068BFA, S). **Don't.**
- **The template grows from 0x2FC to 0x304 entries** (**[V] corrected**: it said 0x300, and then 0x308 or more).
  - 0x2FD..0x2FF have no `cmp.w` immediate encoding. **[V]** Every multiple of 4 from 0x300 to 0x3FC does (imm8 0xC0..0xFF rotated). So the count is the next multiple of 4 and the pad is at most 3 entries.
  - The synth set's 7 entries give 0x303, which pads to **0x304 with one pad entry**. This was measured end to end: `cmp.w #0x304`, `movw r7,#0x303`.
  - **[V]** The pad entries are type 0xFF. That type is not inert: CHILD_CREATE(0xFF) fails on stock but succeeds on the patched table, making a one-property object (key 0, M). The test is therefore that no reader type name maps to 0xFF.
- **Memory is negligible.** Stock's store pools are 1.5 MB (0x08126204 / 0x08126222, I), and 7 keys × 8 B × 2 oscillators × 8 tracks is under 1 KB.

## 3. Design

### 3.1 Keys (one per function, shared by both oscillators)

Keys are per object, so one key serves both WT1 and WT2. That removes plan-analog-fm FD4's shared-key trick and the need for 0x1A8.

| Function | Key | Descriptor | Token | Default | Note |
|---|---|---|---|---|---|
| Type | 0x1C0 | type 7 list Wavetable / Analog | `bento_osc_type` | 0 | today's Sync key, new meaning; old files used other tokens |
| Glide | 0x1B7 | as today: type 10, 0..1000 (**[V]** was "0..100 class"; `synth_keys()` sets 0..AN_GLIDE_MAX = 1000, the Pulse Width class) | `bento_glide` | 0 | WT1's property (NP4) |
| Sync | 0x1AB | type 7 Off / On | `bento_sync` | 0 | WT2's property |
| Shape | 0x1A7 | integer 0..127 | `bento_shape` | 48 (saw, as today's default sound) | NP6 |
| Bit Depth | 0x1A4 | type 7 Full / 12 / 8 / 4 | `bento_bits` | 0 (Full) | |
| Sub FM | 0x18B | type 10, 0..1000, "%" | `bento_subfm` | 0 | plan-analog-fm 3.3 |
| FM 1>2 | 0x132 | type 10, 0..1000, "%" | `bento_fm12` | 0 | WT2's property |

- **0x1AB / 0x1A7 / 0x1A4** are the three drum allow-list keys S6c-2 left unclaimed. They sit in the M7 range 0x18C..0x1B6, but that range is stored (0x08059990) only for the object 0xFFFFFFF1, the reverb, behind `cmn.w r3,#0xf; bne` at 0x08059988 (S, S6c-2 review).
  - Our messages carry a track handle, so they never match.
  - The difference from the drums: these keys **do** now reach set_property. test_keys must log that, and confirm the value arrives intact at the oscillator.
- **[V] Values are never range-checked by stock** (M: 0xD0 edits, INST_LOAD of a file). Every user of these keys must read them defensively:
  - the M7 render and N3's store;
  - the page (A7, A8, A10 and the tick heal);
  - Type means Analog only when it is exactly 1;
  - Bits, Shape, Glide and the FM amounts are clamped where they are used.
  
  A hand-edited or corrupt project.xml must not reach an out-of-range table index.
- **0x132 / 0x18B** are the FM plan's keys. The Track FX plan loses them, as plan-analog-fm 3.4 already says.
- All keys are written all or nothing by N2 (below). test_keys re-runs the clean-key scan and also checks the special-case list of 0x08132B78.
- A close call: the keys can be renumbered freely until the first kit ships, after which they are fixed by the tokens. The **tokens**, not the key numbers, are what the files carry. A later renumbering therefore costs nothing in compatibility, as long as the tokens stay.

### 3.2 The three mechanisms

- **N1, the template (M4 data).**
  - Copy stock's template into our M4 flash block and append our entries at the **end of the type-6 run**, so every stock key keeps its place in the array.
  - Pad with type 0xFF entries to an encodable count.
  - Patch the 2 literal words and the 3 immediates.
  - Cost: about 6.2 KB of data and 5 patches, with zero code. Stock does the rest.
    - **[V]** 0x304 × 8 = 6,176 B.
    - The 5 patches are the 2 literal words (each is loaded twice, at 0x0812EA12 / EA32 and 0x0812EA68 / EAC6) and the 3 immediates.
    - The original table stays in place: 0x0819AA34, just past its end, is a separate key list with 3 readers.
  - **Close call (NP1):** the alternative is a wrapper at the exit of 0x0812EA48 that grows the type-6 array, about 150-200 B of code (estimate). Its costs:
    - it must allocate from the same pool that NODE_DESTROY frees (unverified);
    - 0x0812EA0C would miss our defaults, although its callers only ask for fixed stock keys (S);
    - it adds a code path to every object creation.
  - BE4.31 has 19,780 B free below 0x18D334 (A, S). The table takes 6 KB of that, and the removals in section 5 give some back (size measured in P0, I: several KB). P0 results: about 1.2 KB net, M; 21,404 B free.
  - **Recommendation:** the table copy. A diff of the data against stock is easy to review, and there is no new runtime code.
- **N2, the descriptors (M4 code).**
  - Redirect the `bl REG_BUILD` at 0x08131FD4 to a wrapper: stock's builder, then our descriptors with their real tokens, all or nothing.
  - That replaces today's lazy `synth_keys()`, which would be too late: the boot project load may run before the synth UI's first use, and descriptors with empty tokens are skipped by the writer.
  - claim()'s keys (0x1C1 and up) are untouched.
- **N3, the M7 (data patch plus our code).**
  - Point the oscillator's slot-8 word 0x080ACBD4 at ours, as A2 does for slot 0.
  - Our keys are stored per oscillator object, with no handles: the M7 gets them per oscillator, by index. Every other key goes on to 0x08081A80 unchanged.
  - Slot 0 (A2) then reads the oscillator's own Type: Wavetable goes to stock's render, Analog to `an_osc`.

### 3.3 The M7: one oscillator Analog, the other Wavetable

Today's `an_osc` assumes both oscillators are Analog. B found a real hazard there (S):
- **osc+0x10C belongs to stock when the oscillator plays a wavetable.** It is the history that the kernel read at 0x080A2000 reads and writes.
- Today the voice's own state (note, glide, the sub's state) lives in WT1's AnOsc, and the sync master writes into WT2's.

The rules:
1. **Where our properties live.** The values N3 stores must not live at osc+0x10C, or a Wavetable oscillator's render overwrites its Shape, Bits and so on. P0 finds bytes in the oscillator object that stock never writes after construction, by running stock's WT render with write watchpoints. If there are none, a small M7-local table keyed by the oscillator pointer.
   - P0 results (M): there are none. N3 keeps the values in the kernel area while the oscillator is Analog instead, which departs from this rule.
   - Pointer reuse cannot leave stale values: stock pushes every child property after it creates an instrument. For the project-load path this is still I; P0 verifies it. Settled in P0 results: M (WT1), I (WT2).
2. **The voice state** lives in the first Analog oscillator (WT1 if it is Analog, otherwise WT2), and is reset when that changes.
3. **Leaving Analog** runs `forget()` on that oscillator, so stock finds a zeroed history.
4. **Sync:**
   - with Wavetable OSC1 and Analog OSC2, our master phase runs at OSC1's rate in WT2's AnOsc, using stock's own frequency formula. That it sounds right is inferred (same frequency, a different phase origin), so it is a device check;
   - with Analog OSC1 and Wavetable OSC2, Sync is inert and hidden.
5. **Sub FM and Bit Depth** act only on an Analog oscillator.
6. **FM 1>2** needs both oscillators Analog. Stock's render applies level and mixes inside itself, so there is no clean OSC1 signal to take (S).
7. **Glide** acts on Analog oscillators. Whether it can also move a Wavetable oscillator through osc+0x04 depends on whether stock rewrites that at note start, which has not been traced (NP4).
8. AnOsc grows from 156 to 164 B of its 192 B (B, I).

**Test that matters most:** in a mixed voice, the Wavetable oscillator's output must be bit-identical to stock's for the same notes.

### 3.4 The M4 page

- **A6 stays.** It sets the rows' slot keys after the page's `memcpy`.
- **Type-dependent rows use the tick-heal pattern S6c-2 proved for drums** (`dr_heal`).
  - The synth view's tick (A10) reads each oscillator's Type with CONTEXT_GET (stock's generic path, so no X3).
  - If the row's keys don't match the Type, it rewrites view+0x11CAC and refreshes.
  - A Type edit is shown within one pass.
  - Whether stock's widgets take new keys on a refresh without a page rebuild is not traced; this is P0's T5 run in the display emulator. Settled in P0 results: M, yes, on SYNTH_ROW_SHOW.
  - **Fallback:** a fixed layout. Shape sits in slot 4, Position stays in slot 2 on both types, and the inert controls show "--".
- **A7 works per pane** ("OSC1: Analog" / "OSC1: <wav>"). The pane index comes from the slot offset (0x29C0 + 0x70·i, B, S).
- **A8** (CPU indicator, shape dial) and **A10's "No WAV" label** check the pane's oscillator Type instead of the track's flag.
- **Edits go through stock.** 0xCF becomes 0xD0, which runs set_property and touches the project.
  - **A9 goes** for our keys, provided the descriptor ranges clamp in stock's encoder path.
    - **[V]** Measured: the 0xD0 step does **not** clamp (M), and the 0xCF -> 0xD0 forwarder 0x08161158 does not either (S).
    - Any clamp must come from the encoder widget that builds 0xCF from the descriptor. P0 measures that. Not settled in P0 results (open); the readers clamp.
    - Either way the readers must clamp (3.1), because files are not clamped.

## 4. What is removed

| Item | Today | After P1 (synths) | After P4 (drums) |
|---|---|---|---|
| A3 / A4 / A5, the 9th menu entry | Analog's entry | **gone** | gone |
| AN4 (slice table, published synth table, seqlock, boot stamp) | Analog's flags, Sync, Glide | **gone** | gone |
| M7 per-root handle cache (B1, root+0x0C lines) | Analog lookup | **gone** | the drums' `knob_entry` still uses handles until P4 |
| H1 OBJ_CTOR 0x0812E79C | AN4 + DK4 | Analog part gone; stays for DK4 | **gone** |
| H2 clone word 0x08199194 / H3 destroy word 0x08199190 | AN4 + DK4 | stays for DK4 | **gone** |
| H4 CHILD_DUP 0x0812F91E | DK4 | stays | **gone** |
| X1 save 0x081304DE | `bento_analog*`, `bento_drum_*` | Analog part gone | **gone** |
| X2 load 0x081309A0 | both | **migration-only** (section 6), or gone if NP3 = no | migration only, or gone |
| X3 CONTEXT_GET 0x08134A70 | our keys | synth keys gone; drums keep it | **gone** |
| A9 synth view event slot | our keys | **gone** (if P0 confirms; gone in P1, see P0 results) | gone |
| `synth_keys()` (lazy, empty tokens) | | replaced by N2 | |
| DK4 table, D6's edit swallow | drum knobs | stays | **gone** (D6 keeps only the page's row logic, if needed) |

**Added:** N1 (data + 5 patches), N2 (one `bl`), N3 (one M7 word plus the per-oscillator store).

**Kept:** A1, A2, A6, A7, A8, A10, and the drums' D1-D5 page and render sites.

The `analog` bisect group becomes N1-N3 plus the page sites. `BE_DISABLE=analog` must leave the template and registry stock, and test_keys checks that.

## 5. How it applies to the drums

- **The same idea fits:** the Kick's knobs 0x1AE..0x1B4 and its engine become properties of the pad cells (types 1 and 2, 32 keys each). Stock would then save, load, copy and duplicate them, ending DK4, H1-H4 and X1-X3, and closing plan-synths-168 D9 (pad copy, duplicate and undo by copy).
- **Not proven yet.** Three things are open:
  1. **Pads reach the M7 by their own cell handle** (ENGINE_PROPERTY loop 0x0813B904). SAMPLER_PROCESS case 0x3E drops keys above 0xE5 except 0x1DF (0x08091B46, S), so the M7 needs a new code hook there to take our keys.
  2. **The pad page's edit path** with D6 forwarding instead of swallowing has not been traced. Stock's D6 would post the edit up to the model as a pad property (0x08154AB2, S), and that is probably what we want now.
  3. **Engine selection** today comes from the file name (`BE Kick.wav`). It can stay that way, or become a property such as `bento_drum_engine`.
- **Recommendation:** do the synths first (P1-P3), then the drums as P4.
  - P4 depends on the concurrent drum feasibility plan (`docs/plan-drum-synth-track.md`). If that plan picks a new drum-synth track built on Wavetable or One-Shot objects, the same N1-N3 apply to its object types and P4 merges into it.
  - Until P4, H1-H4 / X1-X3 / DK4 keep serving the drums unchanged, and test_drum_ui must stay green through P1-P3.

## 6. Migration (NP3)

- **Old files.** BE4.22 .. BE4.31 write `bento_analog`, `bento_analog_sync` and `bento_analog_glide` on the **track's** params.
  - Whether Jeff saved any is unknown: BE4.22's device checklist is still open, and BE4.31 requires it to pass first.
- **The conversion.** The reader handles the track before it creates the track's cells, in the same task (S).
  - **[V] corrected addresses:** the track's INST_LOAD is at 0x081315FC. The cells are created at 0x08131514 and loaded at 0x081314D0 (types 6 / 7) or 0x08131528. The plan had given 0x081314D0 as the track's load.
  - The order holds, and X2 at INST_LOAD's entry sees the track and then its cells. So a cut-down X2 can:
  - on a track with `bento_analog=1`, remember {sync, glide};
  - on the next two type-6 cells, set Type = Analog, Glide on WT1 and Sync on WT2;
  - convert Position (the old shape, 0..255) to Shape (0..127) through a small table, so saw stays saw and square stays square;
  - drop the remembered values at the next track.
- **Saving.** New saves never write the old track attributes. BE4.31 then opens a new project as plain Wavetable, which is an acceptable rollback.
- **Presets** carry the same attributes and go through the same reader, so they migrate too (I).
- **Recommendation:** ask Jeff.
  - If he has no Analog projects or presets he wants to keep, drop migration and X2 entirely (less code, one hook fewer).
  - Otherwise keep the cut-down X2 for one or two releases, then remove it.

## 7. Risks

| Risk | Label | Mitigation |
|---|---|---|
| The boot project load runs before REG_BUILD, so our tokens are unknown at the first load | **[V] resolved by reasoning (S + I):** N2 adds our tokens inside the only REG_BUILD call (0x08131FD4), so they exist exactly when stock's do | test_native_props loads a project immediately after model init |
| **[V] new:** out-of-range values reach our code (hand-edited file, a widget without a clamp) | M: stock stores and forwards them raw | defensive reads (3.1); a test loads a file with bento_bits=99, bento_osc_type=-7, bento_shape=1000 and checks render and page |
| The project-load path does not push child properties to the M7 the way 0x0813E4B8 does after a create | I (stock's own Position must reach the M7 somehow) | P0: trace it, and log set_property during a project load in the emulator. Settled in P0 results: M |
| A forwarded value is clamped or changed on the way (the M7 registry copy, the broadcast flags at engine+0x8CB2) | S: only 0x05 / 0x27 / 0x31 are flagged. **[V]** M4 side M: unchanged (5000 and -3 pass). M7 entry S: no clamp. Root to slot 8 M: delivered with index | test: the value at slot 8 equals the value sent, for each key at its extremes |
| A live row-key change is not picked up by stock's widgets | not traced | P0 T5 run; the fixed-layout fallback (3.4). Settled in P0 results: M, picked up on a refresh |
| A mixed voice corrupts stock's wavetable history | S (B's hazard) | the rules in 3.3; the bit-identical WT test |
| The padded template count does not encode, or type 0xFF collides | **[V]** M: 0x304 encodes and works. The pad makes type 0xFF creatable | test: no reader type name maps to 0xFF and no CHILD_CREATE type is 0xFF |
| Flash space (about 6 KB of data) | S: 19,780 B free | P0 measures the removals; NP1 fallback is the wrapper. Settled in P0 results: M, 21,404 B free |
| Keys forwarded by set_property hit a stock M7 case we missed (computed keys, u16 tables are invisible to the scan) | S, limit of the evidence | device check: edit every new control and confirm no stock parameter on the track changes |
| Every saved project carries our attributes on every oscillator cell, even at default | M: stock ignores them | none needed; noted for Jeff |
| An M7-only reset loses our per-oscillator values until the next push | I (same as stock's own properties) | none beyond stock's behaviour |

## 8. Tests

- **New: test_native_props.py (1.6.8, on the built image).**
  - Every stock object type's array after CHILD_CREATE equals stock's, key for key, with ours appended only on type 6.
  - Clone, CHILD_DUP and the writer / reader round trip (A's and B's runs, as tests).
  - 0xCF -> 0xD0 -> set_property with our descriptors, clamped at the range ends. **[V]** The 0xD0 step does not clamp (M), so this test must start at the encoder widget. Readers clamp regardless.
  - **[V]** Run on the built image, or apply the patch before the first object is created: Unicorn's translated-block cache kept 0x0812EA48's old bounds after an in-memory patch (M).
  - **[V]** Use the project-level writer 0x08131220 and reader 0x081313EC with file I/O stubbed (stub 0x0813184C; feed stock's parser 0x0810AB76 from 0x0813190C), as `verif\t2.py` does.
  - CONTEXT_GET of each key.
  - The push after create, and a project load, both reach the M7 with each key and index.
  - Stock 1.6.8 and 1.6.1 reading our file skip the attributes.
- **test_keys:** the new allow-list; clean scan, including the special cases of 0x08132B78; the N2 all-or-nothing behaviour; the template patches (2 literals, 3 immediates) and `BE_DISABLE=analog` leaving them stock.
- **test_analog / M7:**
  - mixed voice: the WT oscillator is bit-identical to stock's;
  - switching Type: forget() runs, no stale state;
  - Sync with WT OSC1 and Analog OSC2;
  - the voice state moves when OSC1 changes Type;
  - our slot-8 store survives N blocks of the WT render.
- **test_synth_ui:** rows per Type (all 4 combinations); the tick heal after a Type edit; titles "OSC1: Analog" / "OSC1: <wav>"; the menu has 8 entries.
- **test_inst_xml:** rewritten around stock's own writer and reader, plus the migration fixture (a BE4.22 project.xml): Type, Sync, Glide and Shape end up on the right cells.
- **test_handles:** the Analog cases retire. The drum cases stay until P4.
- **test_drum_ui:** unchanged and green through P1-P3.
- **Mutation tests:** Type stored on the track instead of the child; a key missing from the template; the Shape remap off by one anchor; forget() skipped.

## 9. The contrast: a genuinely new track type

A real "Analog" track type would need:
- a new type byte, with its own children list and template run;
- the instrument menu (A3-A5 again);
- a page class;
- the M7 root class, voice allocation and every type switch on both cores (0x12 / 0x13 tests appear throughout, such as 0x08132B78's walk).

Stock 1.6.1 and 1.6.8 would not load such a track at all, so rollback would break projects. It is far more work for a worse result. (B) gives the honest version of the same thing: Analog is a property of an oscillator, and stock already has two oscillators per Wavetable track.

## 10. Sprints

| Sprint | Work | Exit (offline) | Device check |
|---|---|---|---|
| **P0: measure** (no kit) | Boot order of REG_BUILD vs the boot project load; the project-load push path; M7 value integrity; the T5 row-key rewrite run; the oscillator's free bytes (watchpoints); template count encoding; size of the removals | each item answered M or S, written into this plan; NP1 / NP2 / NP3 decided (done: P0 results; the encoder clamp is open) | none |
| **P1: native Type, Sync, Glide, Shape** (kit BE4.32) | N1-N3; per-oscillator M7 rules (3.3); page rows / titles / tick heal; remove A3-A5, AN4, the Analog parts of H1 / X1-X3, A9, the M7 handle cache | all tests in 8 green except FM / Bits; the size budget met; `BE_DISABLE=analog` stock-identical for the touched sites | menu has 8 entries; mixed OSC1 Analog + OSC2 WT plays both; Type switch with a wav loaded and back; save / reload / preset / copy-paste keep everything; stock 1.6.8 opens the project as plain WT; no stock parameter changes on any edit |
| **P2: migration** (only if NP3 = yes; same kit) | cut-down X2; Shape remap | the migration fixture passes; BE4.31 opens a new project as plain WT | open a real BE4.22 / BE4.31 Analog project: it sounds the same, then save and reload |
| **P3: FM and Bit Depth** (kit BE4.33) | plan-analog-fm on native keys (0x18B, 0x132, 0x1A4); its AN4 second word is dropped | plan-analog-fm section 8 tests on native properties | plan-analog-fm's device checks |
| **P4: drums** (after the drum feasibility plan) | pad properties; the SAMPLER_PROCESS hook 0x08091B46; D6 forwards; remove DK4, H1-H4, X1, X3 | test_drum_ui and test_native_props for pads; pad copy keeps knobs (closes D9) | Kick knobs survive save / reload / pad copy / duplicate |

- **Order:** P1 before plan-analog-fm's FM sprint. Building FM on AN4's second word first and then migrating it would mean doing that work twice.
- **Commits:** each sprint commits after its offline exit (standing OK). It is not pushed without asking.

## 11. Open decisions for Jeff

| # | Decision | Options | Recommendation |
|---|---|---|---|
| NP1 | How our keys get into the template | table copy (about 6 KB data, no code) / wrapper on 0x0812EA48 (about 200 B code, an allocator assumption) | table copy, unless P0's size budget fails (it holds: P0 results) |
| NP2 | The Analog menu entry | drop it / keep it as a shortcut that only sets both Types | drop it: it is the hack you asked to remove, and it costs 3 hooks |
| NP3 | Migrate BE4.22-BE4.31 Analog projects | yes (cut-down X2) / no | depends on whether you saved any you care about. No means less code |
| NP4 | Glide on a Wavetable oscillator | only Analog oscillators / try the osc+0x04 follow (untraced) | only Analog in P1; trace later |
| NP5 | Inert controls | hide them (tick heal) / show "--" in a fixed layout | hide them, if P0's T5 run allows (it does: P0 results) |
| NP6 | Shape's default | 48 (saw, today's sound) / 0 (sine) | 48 |
| NP7 | OSC1 / OSC2 titles | always on Wavetable tracks / only when one is Analog | always: one consistent name, which FD8 already approved |

## Decision (Jeff, 2026-11-09)
- (B) per-oscillator Type: **yes** ("a nice touch"). The separate "Analog" menu entry goes.
- No migration needed: Jeff has no saved dev-build projects he cares about (skip NP3 conversion; keep the
  no-crash guarantee on stock 1.6.8 / older BE builds).
- Proposed (main session): the drums use the same native-property mechanism from the start (track flag on the
  One-Shot track object, engine + knobs on each pad object) instead of DK4 + H1-H4, see
  docs/plan-drum-synth-track.md.
