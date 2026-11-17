# FM Env 1 / FM Env 2 as mod-matrix sources: verdict

**Verdict: GO, with conditions.** The recommended route is investigation 1's design, corrected by one finding that both investigations missed: the M4 validator writes to the model, so a source it does not recognise is erased, not just displayed as None. It costs less than the FM-amount destinations and does not change the sound of BE4.43 until something is routed.

Labels: M = measured (emulator or byte comparison), S = read from the stock 1.6.8 disassembly, I = inferred. For this reconciliation I re-disassembled the sites below from the stock image (scripts v1.py and v2.py in this folder). I edited nothing in either repo.

## What I re-checked, and where the two investigations disagreed

| Point | Investigation 1 | Investigation 2 | Re-derived |
|---|---|---|---|
| Source ids that bind nothing on a WT voice | 3, 6, 7, 15-25, 27-29, 34, 35 | same | **M** (out_ids.txt). The idx tbh (0x0807CA7A, indexed by id-1) sends ids 3, 6 and 7 to the default 0x0807CAE0. The sub tbh (0x0807C922, indexed by id-2) sends them to 0x0807C9A0. **S** |
| Hook site | Repoint the tbh entries for ids 3 and 6 to stubs placed in the padding | b.w at 0x0807CB5C and 0x0807C980, at the "id > 0x21" check | Both are valid. The padding at 0x0807C966-0x0807C97F and 0x0807CAC4-0x0807CADF is alignment nops. Before each range is table data or the 0.001f literal (0x0807CAC0), and a scan of the whole M7 found no branch into either range. **S**. Note that 0x0807C980 is reached by `bhi` from 0x0807C916, and 0x0807CB5C by `bhi` from 0x0807CA6E. The tbh route only works for ids at or below 0x1F/0x20; ids 34/35 need investigation 2's site. |
| Where the value lives | In the voice's tail slack, voice+0xB88 and +0xB8C (allocator rounding) | A hash table in SA, 1.5 KB | The WT root ctor allocates each voice with `movw r2,#0xb88` (0x0809CB40). The allocator at 0x080A05E0 tests `lsls #0x1b` (already 32-aligned?) and otherwise does `bic #0x1f; adds #0x20`, so it rounds 0xB88 up to 0xBA0, leaving 24 B of tail. **S**. No be442 code uses voice+0xB88 or above. **S** (grep). That stock never touches it is **I**. **Slack wins**: no SA, no hash, no capacity limit. |
| Envelope timing | an_enter publishes e0 read-only, using fm_env_step's own first-line rule | Move fm_env_step into an_enter | Read-only e0 in an_enter is better. It leaves fm_env_step and the FmEnv golden lines untouched, and it matches the start of the FM index ramp. Order: env2/LFO/seq tick, then slot 18 (an_enter, then the renders), then env1, then the filters. **S** (both investigations agree). |
| M4 picker list | Shared by view types 15, 18 and 19 | Shared by Wavetable and Granular | The validator's mask 0xC8000 is bits 15, 18 and 19 (list 0x081A1304, count 16). **S**. Patch the WT branch only. |
| **Validator 0x0814DB94** | "shows as None" | not mentioned | **Correction.** If the routing's source is not in the type's list, the validator **stores 0 into the routing** (`strh r3,[ip,#4]`). It is called from MX_EDIT (0x0814DD3C) and from MX_ROWS_BUILD (0x0814E2A0, 0x0814E696). **S**. Two consequences: (a) the validator must be patched for either id choice, which investigation 2's plan lacked; (b) "survives a round trip to stock" only holds until someone opens that track's matrix page on stock or BE4.43. After that, the routing is erased in memory and the next save drops it. |
| M4 clamps ids before the M7 sees them? | n/a | Open risk | Ids 3 and 6 are inside the registry range 0..0x22, and stock already sends them for other engines. Reusing them makes this risk go away. With 34/35 it still needs a test. |
| Stock and BE4.43 identical in the routines involved | M (same.py) | M (route bytes) | Accepted. |

## Recommended route

Reuse two source ids that do nothing on a WT voice: **3 ("env3") and 6 ("lfo3")**. Both have room in the tbh tables, need no token hooks, are already in the registry range, and stock does not clamp them.

**M7 (connect, about 32 B, no DSP call).** In 0x0807C8A0, patch 4 tbh halfwords:
- idx table entries [2] and [5] point to stubs at 0x0807CAC4+: `addw r1, r5, #0xB84` (or `#0xB88`), then `b.w 0x0807CAE2`;
- sub table entries [1] and [4] point to stubs at 0x0807C966+: `addw r1, r3, #0xB84` (or `#0xB88`), then `b.w 0x0807C9A2`.

PARAM_VALUE then reads `[src+4]`, which is voice+0xB88 for FM Env 1 and voice+0xB8C for FM Env 2. Optionally, change the movw at 0x0809CB40 to #0xB90, which makes the tail explicit at no memory cost.

**DSP (publish, about 60-100 B).**
- In `an_enter`, for each oscillator:
  - Analog: write e0, computed by fm_env_step's first-line rule without stepping (START gives `a?0:1`; DEC with k==0 gives 1; otherwise fme).
  - Wavetable: write 0.
- The early return for an all-Wavetable voice (`!a1 && !a2`) must write both zeros before it returns.
- In `an_note` (slot 2, called per oscillator), write the start value, so the first block's env2/LFO/seq readers never see uninitialised pool memory.

**M4 (about 0.15-0.2 KB).**
- An 18-entry WT list: stock's 16 entries, with FM Env 1/2 placed after Env 2. Install it with investigation 2's wrapper on the row filler 0x0814DA70, which rewrites view+0x8ACC8 and the count at +0x8AC4E. Repointing the shared literal 0x0814EDF8 would also change types 15 and 19.
- A replacement validator that accepts 3 and 6 when the type is 18 (WT) and is otherwise stock.
- A name hook in 0x0814D8F0, WT only: 3 shows as "FM Env 1" and 6 as "FM Env 2".
- The save format does not change.

**Sprints (2, plus a device check).**
- **S1, M7 and DSP.** Stubs, publish and init. Emulator tests:
  - the connect gives voice+0xB84 and +0xB88;
  - Filter cutoff, Shape and Pitch routed from FM Env follow the golden fm_env e0 per block;
  - a WT oscillator gives 0;
  - P3Identity and FmEnv stay bit-exact with nothing routed;
  - a write trace over the voice's lifetime shows nothing else touching voice+0xB88..0xB9F.
- **S2, M4.** List, names and validator. Tests:
  - create, save and load;
  - the type 15/19 lists stay stock;
  - load the file on stock: the routing is inert and kept until the page is opened.
- **Device check.**

## Costs

- **M4:** about 0.15-0.2 KB (16 KB free).
- **M7:** about 32 B, in existing padding.
- **DSP:** about 60-100 B, against **248 B left** under the P3 ceiling.
- **RAM:** none (the voice slack already exists).
- **CPU:** about 15-25 instructions per voice-block (about 0.15%), against **0.16% headroom** on the P3Identity 2% bound. This must be measured, and may force a decision on the budget ceilings against P4 drums.
- **Readers:** stock's own cost, one VFMA per routed slot per block.

For comparison, investigation 2's route (new ids, SA hash table, moving the step) costs about 320-420 B DSP plus 1.5 KB of SA, which only fits on main after P4-3. That is the main reason to reject it.

## Risks

1. **Budgets are tight** (DSP 248 B, CPU 0.16%). Measure them in S1, and raise the ceiling explicitly if needed.
2. **The voice tail.** That the slack is unused is inferred. The be442 test rig allocates voices as 0xB88 (test_analog.py:250 and others), so the rig must allocate 0xBA0, or the new tests will write past the voice.
3. **The validator erases unknown sources**, so a file opened on stock or BE4.43 loses the routing once the matrix page is visited (S). This applies to either id choice, so it is not a tie-breaker. Note it in the release notes.
4. **Reused ids carry a meaning on other engines** (I). On engines that have an env3/lfo3, ids 3 and 6 are real sources. If a track's type can change while keeping its routings, an FM Env routing could become a real Env 3 / LFO 3 routing. Check whether a type change clears the matrix.
5. **Timing.** env2, LFO1/2 and mod seq tick before an_enter, so routing FM Env to their own parameters is one block late, the same lag stock already has. Pitch steps every 2.7 ms, as stock's envelopes do.
6. **An unset envelope** (Atk 0 / Dec Inf) publishes a constant 1, so a routing from it is a fixed offset.
7. **The publish depends on an_enter running.** If a block skips an_enter for a voice (area_ok false), the value holds at its last setting. That is benign once an_note has initialised it.
8. **Other UI paths** may show source names without going through 0x0814D8F0 (I, from the literal scan). Check on the device.

## Q5: does it run at FM amount 0, and should it exist on Wavetable oscillators?

The envelope already steps whenever it is set (`feeds()`: `if (p->envf) fm_env_step`), with or without an amount and while silent. **S**, and BE4.43's FmEnv test **M**. No change is needed.

Wavetable oscillators publish 0 for now. An envelope on them needs its own settings storage (an_prop drops the keys for WT, and stock's kernel area is overwritten, **M** kwrites.py) plus new M4 rows. That is about 0.3-0.5 KB more. It fits in the remaining 16 B of slack, but it is a different feature: really per-voice "Mod Env 1/2", and better done alongside WT FM.

## How this combines with the planned FM-amount destinations

Shared work:
- the same M4 matrix page (row filler wrapper, validator replacement, name hook);
- the same M7 connect function (0x0807C8A0) and its emulator rig (connect.py / route_emu.py);
- the same budget negotiation.

Do sources first: they are cheaper and higher value, and they build the M4 page hooks and the connect test rig the destinations will reuse. Routing FM Env to an FM-amount destination would duplicate the built-in envelope, so the two features hardly overlap in use.

## Open decisions for Jeff

1. **Which ids.** Reused ids 3 and 6 (recommended: cheaper, no clamp risk, but stock files will say env3/lfo3), or new ids 34/35 with bento_ tokens (self-describing, about 150 B more, needs the hooks at 0x0814E452 and 0x0807CB5C/C980, and the route is lost on stock either way because of the validator).
2. **List position.** After Env 2 is recommended; appending at the end is the fallback.
3. **Unset value.** Should an unset envelope publish 1 (consistent with FM depth) or 0 (an unset envelope modulates nothing)?
4. **e0 or e1.** Publish e0 (matches the FM index ramp start; recommended) or e1 (one block more current)?
5. **Budgets.** Approve raising the DSP and CPU ceilings if S1's measurements exceed the headroom.
6. **Wavetable oscillators.** Envelope now, or later as "Mod Env" alongside WT FM (recommended: later).

**Decided (Jeff, 2026-11-16): every recommendation accepted.**
1. Reuse ids 3 and 6 (Env 3 / LFO 3 slots).
2. Listed right after Env 2.
3. An unset envelope publishes 1 (consistent with FM depth).
4. Publish e0.
5. Raise the DSP and CPU ceilings only by what S1 measures, reported before it is spent.
6. Envelopes on Wavetable oscillators come with WT FM (BE4.44), not separately.
