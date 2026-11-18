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
- An 18-entry WT list: stock's 16 entries, with FM Env 1/2 placed after Env 2. Install it with investigation 2's wrapper on the row filler 0x0814DA70, which rewrites view+0x8ACC8 and the count at +0x8AC4E. Repointing the shared literal 0x0814EDF8 would also change types 15 and 19. (Superseded: see "S2 as built", plan items found wrong 2 / 3. The filler writes neither; S2 patches MX_BUILD's bl to it, and the literal is shared with Granular only.)
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
2. **The voice tail.** That the slack is unused is inferred. (S1 fix: now S. The pool is a bump allocator inside the zeroed bss; see "S1 fix" below.) The be442 test rig allocates voices as 0xB88 (test_analog.py:250 and others), so the rig must allocate 0xBA0, or the new tests will write past the voice.
3. **The validator erases unknown sources**, so a file opened on stock or BE4.43 loses the routing once the matrix page is visited (S). This applies to either id choice, so it is not a tie-breaker. Note it in the release notes.
4. **Reused ids carry a meaning on other engines** (I). On engines that have an env3/lfo3, ids 3 and 6 are real sources. If a track's type can change while keeping its routings, an FM Env routing could become a real Env 3 / LFO 3 routing. Check whether a type change clears the matrix. (S1 fix: the same connect also serves a second root class, 0x5A, which would have meant writes into a live oscillator. It is now gated; see "S1 fix" below. Whether a type change keeps routes is still open, for S2.)
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

## S1 as built (BE4.46, branch be445, 2026-11-17)

Commits 5f9951e (stubs, publish, tests; labelled BE4.45 then), 37f49f2 (label 4.46: 4.45 is the parallel destinations
lane's; the write-trace test's harness pool literals marked addr-ok, which the build's literal check had refused, so
5f9951e did not build), and this section. Image: ~/bento/out/be446-s1 (BE_TAG=s1).

**What was built (S).**
- M7 (build.py fmenv_connect): ids 3 / 6 of both tbh tables (idx 0x0807CA7E entries [2] / [5], sub 0x0807C926 entries
  [1] / [4]) point at a stub in each table's padding (0x0807CAC4, 0x0807C966). (As first built, a 24 B stub that wrote
  FMENV_ON and 1.0 unconditionally; the S1 fix below replaced it with a 14 B stub plus a gated body.) FM Env 1 is the float at voice + 0xB88, FM Env 2 at + 0xB8C, FMENV_ON at + 0xB90
  (synth_shared.h FMENV_*, which build.py reads). The movw at 0x0809CB40 is not patched (the slack is the pool's rounding;
  fmenv_connect refuses a base where it is not #0xB88). The build refuses a base whose tables, padding or joins are not
  stock's.
- DSP: an_enter calls fm_env_publish only while FMENV_ON is nonzero (sticky per voice once any route to 3 / 6 is
  connected); fm_env_e0 is fm_env_step's first line, read only: START 0 (1 for FM Atk 0), DEC at j 0 1, else fme; 1 for
  an unset envelope and for an untagged Wavetable oscillator. Analog and tagged Wavetable oscillators alike (BE4.44's WT FM
  envelopes): the plan's "a WT oscillator gives 0" is replaced by decision 6. an_note writes the note's start value when
  FMENV_ON is set. fm_env_step and the FmEnv goldens are untouched.
- Tests (test_analog FmEnvSrc, 6): the stubs and tables against stock's; the connect for every id 0..35 on both paths
  against stock's (only 3 / 6 differ, by exactly the slot, FMENV_ON and 1.0); the published values against p3_golden
  fm_env's f32 e0 block by block (Analog, tagged and untagged Wavetable, mixed) and Filter 1 / OSC2 Pitch / an Analog OSC1's
  Shape reading them; the publish changes nothing else (output and voice bytes); a write trace of the slack over a root's
  life (stock and this build: no write until 3 / 6 are routed, then only the stubs, fm_env_publish and an_note); the cost.
  P3Identity and FmEnv stay green (nothing routed).

**Sizes (M).**
- DSP 31,084 B, **9,620 B free** of 40,704 (BE4.44 30,884 / 9,820: +200 B: fm_env_e0 100, fm_env_publish 34, an_note
  +46 (first written as +40), an_enter +24, alignment). The doc's 60-100 B estimate was low: e0 covers the tagged-Wavetable case and the
  Analog / WT type test, and an_note's write. No ceiling raised: the >= 1 KB rule holds with 8.6 KB to spare.
- M7 stubs 48 B in existing padding; image size unchanged, 0x189450, 16,100 B to PROVEN_MAX; M4 code 24,424 B, synth UI
  15,846 B, M7 C slice 15,368 B (unchanged: S1 has no M4 change).

**CPU (M, instructions).**
- P3Identity, 48 cases: worst **1.0170** (mod dev=False, 111,768 / 109,899; BE4.44 1.0165, 111,708), total 1.0091
  (1.0087). Under the 1.95 % stop line; no ceiling raised.
- Per voice-block (FmEnvSrc.test_cost; an_enter and both renders; median / max), without a route to 3 / 6 / with one:

  | Voice | No route | Routed | Publish a call | an_enter no route / routed |
  |---|---|---|---|---|
  | stock WT voice | 14,160 / 14,394 | 14,209 / 14,443 | 46 | 64 / 113 |
  | Analog full FM, FM Atk 3 / Dec 8 | 28,115 / 28,863 | 28,168 / 28,914 | 50 | 163 / 216 |
  | full WT voice, FM Atk 3 | 27,266 / 43,738 | 27,339 / 43,813 | 71 | 105 / 178 |
  | full WT voice, note 24, light FM, Position every block | 34,765 / 39,397 | **34,826** / 39,458 | 58 | 105 / 166 |

  No route: +5 a voice-block against BE4.44 (14,155 -> 14,160; the heaviest steady voice 34,760 -> 34,765: the load and
  branch). Routed: +49 .. +73. The heaviest steady voice routed is 34,826, 483 under the 35,309 ceiling (every block but
  its one level-change block, 39,458 <= W4_ATK_MAX 46,000). No CPU ceiling raised.

**Plan items found wrong.**
- DSP size: 200 B, not 60-100 B (above).
- "A Wavetable oscillator gives 0": superseded by decision 6 (tagged WT oscillators publish their own e0; untagged ones 1,
  as an unset envelope).
- The early-return write for an all-Wavetable voice is not needed: fm_env_publish runs before an_enter's `!a1 && !a2`
  return, so both values are written on every routed voice-block.
- The stub also writes FMENV_ON and 1.0 (not in the doc's 32 B sketch): the flag is what makes the unrouted cost +5, and
  1.0 keeps a reader before the next an_enter off the slack's old bytes.
- On an S1-only image ids 3 / 6 cannot be reached from the UI: the M4 picker does not list them and the validator
  (0x0814DB94) erases them on a WT track. S1 is device-inert until S2.

**Overlap with the destinations lane (be445x, BE4.45).** S1 changes only the M7 routing connect's two tables and padding
(0x0807C922 / 0x0807CA7A paths), the DSP (an_enter / an_note, two new functions) and the base tables (WT_ROUTE, FMENV_*,
WT_VOICE_ALLOC). The destinations patch oscillator slots 9 / 10 (0x08081B60) on M7 and relocate the WT destination table
(0x081A0D1C) and the row filler 0x0814DA70 on M4: no shared bytes in S1. Merge points: build.py GROUP_NAMES (the WT group's
list) and m7_dsp, stock_168.py SYMS / CHECKS (adjacent new entries), analog.cpp an_enter, and the DSP window (both add
code: 9,620 B free here before theirs). S2 will touch the same M4 page: the source list rides on the row filler
0x0814DA70 that the destinations lane also patches (its `#0x17` / `#0x228`), so S2 must wrap that function after the
destinations' patch lands (merge destinations first), keeping the source-list change in its own wrapper. (Superseded:
see "S2 as built", plan item found wrong 2 and the overlap section. S2 patches MX_BUILD's call to the filler, not the
filler, so no byte is shared with X1 and the merge order does not matter for these bytes.)

**Suites (M, ~/bento/out/be446-s1, built from 37f49f2, sha256 1dca5f27...f170; from the worktree's mods/m1; skips in
brackets).** All OK: diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24
(20), notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10, native_props 22, **analog
216** (210 + FmEnvSrc 6), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32, p3_golden self-test 52; drums /
drum_ui not run (drums is left out of the default image).

## S1 fix (BE4.46, 2026-11-17): review dispositions

Two reviews of f5c5756. Commit 245b345 has the code and tests; this section is the doc. Image: ~/bento/out/be446-s1f (BE_TAG=s1f).

**What changed (S: built-image disassembly).**
- The pad stubs are now 14 B: `addw r1, voice, #0xB88` (id 6), `b.n`, `addw r1, voice, #0xB84` (id 3), then `b.w` to the body. The rest of each pad is `nop.n`.
- Each body is 42 B among the DSP trampolines: 0x080D0060 for the idx path (voice r5, scratch r3), 0x080D0090 for the sub path (voice r3, scratch r2). It runs:
  - `ldr tmp, [voice, #0x178]; subw tmp, #0x7A4; cmp tmp, voice; bne` to stock's own `movs r1, #0`. This is the gate: it only passes a Wavetable voice.
  - `ldr tmp, [voice, #0xB90]; cbnz tmp, done`, then on the first connect only `str r1` to FMENV_ON and 1.0 into both values.
  - `done: b.w` the join.
- The trampoline block now uses 0x00..0xBA of its 0x100. A drums trampoline would take 0xC0..0xDE, so it still fits.
- The DSP, the image size, M4 and M7 C sizes are all unchanged. The connect runs per message, not per block, so CPU is unchanged.
- New base entries:
  - WT_VOICE_OSC1 (0x0809CB52, `addw r7, r4, #0x7a4`, then `strd r7, r6, [r4, #0x178]`). fmenv_connect refuses a base where these differ.
  - ROOT_5A_CTOR (0x0809C9A0, tests only).
- The flags at the joins are set before they are read: on the idx path by `adds r0, r5, r0`, on the sub path by `cmp r6, #2`.

**Dispositions.**
1. **Shared connect, class-0x5A root (high): accepted and fixed.**
   - Verified (S):
     - The vtable slot 2 of 0x080ACF08 (class 0x5A), of 0x080ACF68 (WT) and of the abstract base 0x080ACB44 is 0x0807F2A1. These are the only three words with that value in the image.
     - The 0x5A ctor allocates `movw r2, #0xfe8` and places OSC1 / OSC2 at +0x7A8 / +0xBC8 (`strd` at +0x178).
     - The WT ctor places them at +0x7A4 / +0x994.
     - an_enter and an_note hook only WT vtables, so the 0x5A voices never reach the DSP side.
   - Gate as the previous fix agent proposed it: `[voice+0x178] == voice+0x7A4`. It is not a root-vtable check, because the voice is the register both paths already hold.
   - The 0x5A ("very likely Granular", I) root still does Env 3 / LFO 3 stock's way, which there is no source at all.
   - Test test_other_root_is_stock: every id 0..35 to 4 destinations gives root and voice bytes equal to stock's, the body ran, and the FMENV_ON store never ran.
2. **1.0 on every connect (medium): accepted.**
   - 1.0 is now written on a voice's first connect only, into both values. The first connect is when FMENV_ON is 0: boot-zero, see 4.
   - Kept rather than dropped: the first block after a mid-note first connect then reads the unset value 1 (decision 3), not 0.
   - Test test_a_reconnect_keeps_the_values: three later connects, on both ids and all three instance paths, leave FMENV_ON and the mid-attack values alone and do set the amount.
3. **Mutants B / P4 (DEC j 0 rule) and D / P6 (envf test) (low, both reviews): accepted, test added.**
   - Reviewer 2 called P4 equivalent. That holds only for fm_env_step's own writes. an_osc's unset hold writes fmk = DEC j 0 without touching fme. So after an END (fme 0), "unset, then set again" leaves a stale fme of 0 that only the DEC j 0 rule hides.
   - Test test_unset_and_set_again_mid_note: Atk 1 / Dec 2 runs to END, then is unset mid-note, then Dec 2 is set again. Published:
     - unset: 1 from the next block;
     - held: 1, with control fmk DEC j 0, fme 0;
     - set again: 1, then fm_env_step's value.
   - Mutation check on scratch copies (s1fix2/mut.sh):
     - G (gate removed): killed (test_other_root_is_stock, test_stubs_and_tables)
     - O (1.0 on every connect): killed (test_a_reconnect_keeps_the_values, test_stubs_and_tables)
     - B (DEC j 0 rule removed): killed (test_unset_and_set_again_mid_note)
     - P6 (envf test removed): killed (test_unset_and_set_again_mid_note)
4. **Slack zero at allocation (reviewer 2 low, "unverified"): rejected, now S.**
   - Pool kind 2 (0x080A0720) bumps a counter at 0x24000448 from base 0x24001080, with a cap of 0x1A000, no header and no free.
   - The M7 reset (0x08040492) byte-clears 0x240001D0..0x2401EDBC, which covers the counter and the whole pool (to 0x2401B080).
   - So FMENV_ON is 0 at every voice's allocation, and the self-validating flag compare is not needed.
   - The evidence is in synth_shared.h and in risk 2 above.
5. **Unrouted cost not pinned (reviewer 2 low): accepted.**
   - BE4.44 was measured with the same harness (s1fix2/en.py, on ~/bento/out/be446-base444). an_enter per call is 59 / 156.62 / 99.56 / 99.56, and the voice-block medians are 14,155 / 28,109 / 27,261 / 34,760.
   - test_cost now asserts that, without a route, both stay within these + 8. Measured: an_enter 64 / 162.62 / 104.56 / 104.56, medians 14,160 / 28,115 / 27,266 / 34,765. That is the +5 load and branch.
   - **Merge note:** a lane that adds to an_enter, such as the destinations lane, moves these bounds. Re-measure on the merged base rather than loosen them.
6. **Doc (reviewer 1 low, reviewer 2 info): accepted.**
   - Slack evidence is now S (4).
   - The shared-connect risk is in risk 4 and in 1 above.
   - an_note +46 is corrected above.
   - Note for the destinations lane, below.

**Note for the destinations lane (be445x).**
- WT_ROUTE's instance 0 / 1 path calls each oscillator's slot 10 (0x0807CB38) with r1 = the source.
- For ids 3 / 6 on a Wavetable voice, that source is the FM Env pseudo-source voice + 0xB84 / + 0xB88. It is not a stock envelope or LFO object.
- Any FM-amount destination hook on slot 10 (0x08081B60) must therefore treat a source only as PARAM_VALUE does, {?, float at +4}, and never dereference it as an object.
- On the class-0x5A root these ids still give no source (r1 0).
- Merge points added by this fix:
  - m7_dsp's trampoline block: the two bodies sit right after the note trampoline;
  - GROUP_NAMES (WT_VOICE_OSC1);
  - stock_168 SYMS / CHECKS (two entries);
  - test_cost's BE4.44 bounds.

**Sizes (M).**
- DSP 31,084 B, 9,620 B free (unchanged).
- Trampolines: 0xBA of 0x100.
- Image 0x189450 (unchanged).
- M4 24,424 B, synth UI 15,846 B, M7 C slice 15,368 B (all unchanged).

**CPU (M).**
- P3Identity: 48 cases, worst 1.0170 (mod dev=False, 111,768 / 109,899), total 1.0091 (the same as S1).
- FmEnvSrc.test_cost: the same as S1 in every row, because the body runs only on connect messages:
  - no route: 14,160 / 28,115 / 27,266 / 34,765;
  - routed: heaviest steady 34,826, 483 under 35,309.

**Suites (M, ~/bento/out/be446-s1f, BE_TAG=s1f, sha256 8fd8e1d5...49ce; from the worktree's mods/m1).** All OK: diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20), notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10, native_props 22, **analog 219** (216 + FmEnvSrc 3 new: 9), inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32, p3_golden self-test 52. drums and drum_ui were not run, because the default image leaves drums out.

**Still open for S2:**
- does a WT -> other type change keep routes (risk 4)?
- the M4 list, names and validator.

**Next: S2 (M4: the 18-entry WT list with FM Env 1 / 2 after Env 2, names, the validator), then the device check.**

## S2 as built (BE4.46, branch be445, 2026-11-17)

Commit 859a914 has the M4 code, base entries and tests. The next commit has this section, the checklist, the release
notes and a test robustness fix. Images: ~/bento/out/be446-s2 (BE_TAG=s2) and ~/bento/out/be446-s2-rel (no tag: the
kit).

**What was built (S: the built image's disassembly).** All of it is in m4_synth.c, in the synth UI block. No M7 or DSP
byte changes.
- **The list.**
  - Stock: MX_BUILD's Wavetable case (type 0x12 of its tbh at 0x0814E94A, at 0x0814ECF6) stores stock's list
    (0x081A1304) and 16 at view + 0x8ACC8 / + 0x8AC4E, then calls the row filler (bl at 0x0814ED08).
  - That bl now calls mx_wt_rows. It stores our list and 18, then calls the filler.
  - Our list is stock's with 3, 6 after Env 2, plus a trailing 0 ("Plan items found wrong", item 4):
    [0, 13, 32, 1, 2, **3, 6**, 4, 5, 8, 14, 26, 30, 31, 9, 10, 11, 12, 0].
  - Untouched: stock's list and both of its literals. One is MX_BUILD's 0x0814EDF8, which Granular's case (0x0814ECB4)
    also loads; the other is the validator's 0x0814DBF0.
  - build.py refuses a base whose list does not start with None, contains id 3 or 6, lacks Env 2, or is referenced by
    anything other than these two literals (one_reference).
  - It also refuses a build whose C list is not the one derived from stock's.
- **The validator** (0x0814DB94; its entry `mov ip, r1; ldrh r1, [r1, #4]` is replayed). mx_src_valid returns at once
  for source 3 / 6 when [view + 0x8AC4C] is 0x12. Every other case goes through stock's body.
- **The names** (0x0814D8F0; its entry `push {lr}; mov lr, r0` is replayed). mx_src_name returns "FM Env 1" (3) or
  "FM Env 2" (6) on a Wavetable page. Elsewhere stock's name is returned: the registry's display name, "Envelope 3" /
  "LFO 3" for these ids.
- **Callers.** No word of the image holds the address of the filler, the validator or the name function. Each has only
  bl callers: the filler 1, the validator 3, the name 2. The entry patches therefore cover every caller.
- **New base entries** (stock_168; absent in stock_161; facts.py; CHECKS regenerated):
  - MX_WT_SRC_LIST, MX_WT_SRC_LIT, MX_SRC_VALID_LIT, MX_WT_ROWS_SITE, MX_WT_ROWS_FILL, MX_SRC_VALID, MX_SRC_NAME;
  - MXV_TYPE, MXV_SRC_LIST and MXV_SRC_N (offsets, also recorded in expected_values.py).
- **The save format** is stock's, with no hook: `<modsource ... src="env3" | "lfo3">`.

**Tests (test_native_props FmEnvMatrix, 8; M).**
1. **The sites.** Stock's list is as typed; the list and its literals are untouched; the validator's and name's bodies are
   stock's.
2. **The Wavetable page.**
   - It shows our list with its trailing 0. The names are "[None]", "Velocity", ..., "Envelope 2", "FM Env 1",
     "FM Env 2", "LFO 1", ..., "Auto D".
   - The rows are what the image's own filler gives. This holds after the merge too, when the filler is X1's.
   - Control: on stock the page keeps 0x081A1304 and 16.
3. **Validator and names**, for every view type 0..0x14, 0x20 and 0xFF, each with every id 0..35:
   - they equal stock's except at (0x12, 3) and (0x12, 6);
   - types 0x0F and 0x13 (Granular) still erase 3 / 6.
4. **The Source encoder** (stock's MX_EDIT on OSC1 Pitch):
   - index i posts list[i] for i 0..17;
   - index 18 (the count) posts 0, and index 19 posts nothing;
   - the posted 0xD1 makes the routing (slot 0, and slot 2 on OSC2 Level).
5. **An amount edit of an FM Env slot** keeps its source here. On stock it posts source 0, so the routing is removed. A
   LFO 1 routing is kept on both (control).
6. **Save / load.** The file has src="env3" x 2, "lfo3" x 1 and the LFO 1 control, and no bento_ token. This build reads
   it back.
7. **Stock 1.6.8 and BE4.44** (~/bento/out/be444w4f) load the routings as ids 3 / 6 and keep them. Their next save writes
   them again, and this build reads that save back.
8. **Instrument replace** (stock's 0x0813F730) on a routed track, kind 1 (to Granular, 0x13) and kind 6 (to Wavetable):
   - each gives a new track, with a new handle and its own children;
   - no routing is left on the track or on any child;
   - three of its calls stop the rig and are stubbed: 0x08133C08 before the destroy, and 0x0813E4B8 / 0x0813D430 after
     the new children. None of them can carry a routing from the destroyed track.

**Mutation check (M, scratch be446/s2/mut.sh, each on a copy of the worktree):**
- T (no Wavetable-type test): RED in test_every_other_page_type_and_id_is_stocks.
- L (list not installed): RED in test_the_source_encoder_picks_them and test_the_wavetable_page_lists_them_after_env_2.
- V (validator always stock's): RED in test_an_amount_edit_... and test_every_other_....
- N (names always stock's): RED in test_every_other_... and test_the_wavetable_page_....
- Z (trailing 0 changed): the build refuses it (fmenv_matrix's list check).

**Suites (M, ~/bento/out/be446-s2, BE_TAG=s2, sha256 2cc8bb81...c514; from the worktree's mods/m1; skips in brackets).**
All OK: diag 92 (6), polyat 51 (2), conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20),
notemod 16, notemod_route 8, notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10, **native_props 30** (22 +
FmEnvMatrix 8), analog 219, inst_xml 5 (5), synth_ui 64 (2), handles 16 (16), note0 32, p3_golden self-test 52. drums and
drum_ui were not run: the default image leaves drums out.

**Sizes (M).**
- Synth UI 16,054 B (S1: 15,846). The +208 B are three functions, the 38 B list and two strings; the plan estimated
  0.15-0.2 KB.
- Image 0x189520 (S1: 0x189450), 15,892 B to PROVEN_MAX.
- DSP 31,084 B, **9,620 B free** of 40,704 (unchanged; the >= 1 KB rule holds).
- M4 code 24,424 B and M7 C slice 15,368 B (unchanged).
- Byte diff of the S1-fix image (be446-s1f) against be446-s2: below the synth UI block, only the three sites and the
  version label's sites differ (BE_TAG s1f vs s2). No byte differs in the M7, DSP or trampoline areas.
- The 1.6.1 build (analog absent) builds: 0x182500.

**CPU (M).** S2 changes no M7 or DSP byte (the diff above), so the audio path is S1's. Re-measured on be446-s2:
- P3Identity, 48 cases: worst **1.0170** (mod dev=False, 111,768 / 109,899), total 1.0091. The stop line is 1.95 %.
- FmEnvSrc.test_cost (median / max) is the same as S1 in every row:

  | Voice | No route | With a route on 3 / 6 |
  |---|---|---|
  | stock WT voice | 14,160 / 14,394 | 14,209 / 14,443 |
  | Analog full FM, FM Atk 3 / Dec 8 | 28,115 / 28,863 | 28,168 / 28,914 |
  | full WT voice, FM Atk 3 | 27,266 / 43,738 | 27,339 / 43,813 |
  | full WT voice, note 24, light FM, Position every block | 34,765 / 39,397 | 34,826 / 39,458 |

  The heaviest steady routed voice is 483 under the 35,309 ceiling. No DSP or CPU ceiling was raised.
- The M4 hooks run only when the matrix page builds, refreshes or edits its rows. The validator and the name add about 10
  instructions a call on top of stock's.

**Plan items found wrong.**
1. **The validator does not erase the model's routing (S, M).** The plan said "the validator stores 0 into the routing ...
   after that, the routing is erased in memory and the next save drops it", and the S2 test plan said the routing is
   "inert and kept until the page is opened". Both are wrong.
   - The validator writes its caller's copy. MX_EDIT, MX_BAR and MX_ROWS_BUILD each pass a stack copy that MX_ROUTE
     filled (0x08132266..72 copies amount, source | key and slot out).
   - So opening or refreshing the page on stock or BE4.44 changes nothing in the model. The slot shows [None], and a save
     keeps the routing.
   - The routing is lost only by an edit of that slot:
     - an amount edit posts the zeroed source, and MX_SET_ROUTE removes a source-0 routing (test_an_amount_edit_...);
     - a source edit replaces it.
   - The release notes and checklist section 6 say this.
2. **The row filler does not write the list (S).** The plan said to "install it with investigation 2's wrapper on the row
   filler 0x0814DA70, which rewrites view + 0x8ACC8 and the count at + 0x8AC4E". The filler writes neither: its clear
   starts at + 0x8ACCC (`str r1, [r3, #4]!` from + 0x8ACC8). MX_BUILD's Wavetable case stores both before its bl.
   - S2 patches that bl (0x0814ED08), not the filler.
   - **No byte overlaps the destinations lane.** X1 patches inside the filler (0x0814DA92, 0x0814DA9E, its literal
     0x0814DB8C) and the destination table's other users.
   - So the task's note "S2 must wrap that function after the destinations' patch lands" is not needed.
   - mx_wt_rows calls whatever filler the image has. After the merge the page gets X1's 26 rows and S2's 18 sources,
     whichever merges first.
3. **"Repointing the shared literal 0x0814EDF8 would also change types 15 and 19" is half right (S).**
   - That literal is shared by MX_BUILD's 0x12 and 0x13 (Granular) cases.
   - Type 0x0F never gets a list from MX_BUILD: it falls to the switch's default case. It shares only the validator's
     list, through its own literal 0x0814DBF0.
   - Neither literal is patched.
4. **MX_EDIT reads one past the list (S, M; not in the plan).**
   - Its bound is `cmp count, index; blt` (0x0814DC34), so index == count is read.
   - On stock that reads the next list's first halfword, 0 (None).
   - Our list keeps this with its trailing 0. The encoder's range is count - 1 (MX_BAR 0x0814E266).
5. **Risk 4, does a type change keep routes: no** (M with three calls stubbed; S for the path).
   - The instrument replace destroys the old track (0x0812E43E). It creates the new type with fresh children
     (0x0812E7C4, 0x0812F878).
   - Also S: **no stock source list holds 3 or 6** (the lists at 0x081A1304, 0x081A1324 and 0x081A1334). So no stock
     page offers or keeps Env 3 / LFO 3. On the M4 these ids were unused everywhere, which lowers the reuse risk further.
6. **Names.** The registry's display names for these ids are "Envelope 3" / "LFO 3", not "Env 3". Stock's list says
   "Envelope 1" / "Envelope 2", and the new entries read "FM Env 1" / "FM Env 2" as decided.
7. **Kit naming.** The sprint brief said "BE4.45 kit". The label is 4.46 (4.45 is the destinations lane's), so the kit is
   flash\bento_enhanced-v4.46, boot label BE4.46.

**Overlap with the destinations lane (be445x, BE4.45) at the merge.**
- **No shared bytes in the image** (item 2). Untouched here: the destination table 0x081A0D1C, its four literals, its
  count and the filler's bounds.
- **Text merge points:**
  - build.py GROUP_NAMES: both add lines at the end of the analog tuple;
  - synth_ui's patch list: X1's mx_dest_patches next to S2's fmenv_matrix;
  - stock_168 / stock_161 SYMS and CHECKS: adjacent new entries;
  - facts.py and expected_values.py;
  - m4_synth.c: S2's block sits before the CPU indicator;
  - test_native_props.py: both append a class at the end.
- **Fold at the merge:**
  - **MX_WT_ROWS_FILL is X1's MX_WT_ROWS** (the same address): keep one name;
  - the tests' MX_SET_ROUTE_FN, fe_routes and fe_set duplicate X1's MX_SET_ROUTE, routes and set_route;
  - MX_EDIT_FN stands in for the mx group's draft MX_EDIT entry.
- **Size.** Synth UI bytes grow in both lanes. The image stays far under PROVEN_MAX (X1 alone adds about 0.7 KB).
- **CPU.** S2 adds nothing to an_enter, so S1's bounds in test_cost are the ones to re-measure on the merged base.

**Kit (built from be446-s2-rel; git-ignored, never committed).** flash\bento_enhanced-v4.46, boot label BE4.46.
Contents:
- 01_TEST_FIRMWARE: the image and stock 1.6.8's bento2.bin;
- 03_RESTORE_STOCK;
- manifest.json;
- DEVICE_CHECKLIST.txt (= docs/checklist-v4.46.txt);
- SHA256SUMS.txt (verified).

bento1.bin sha256 ae671b35a3cc183542568c8d3f20509786372f492738902e5000ebe19d5ec985 (be446-s2-rel; the tagged be446-s2 is
2cc8bb81...c514).

**Next:** the device check (checklist-v4.46.txt), then the merge with be445x (destinations first).

## S2 review dispositions (BE4.46, 2026-11-17)

Two reviews (rebuilds be446-S2r1 / S2r2 byte-identical to the kit image; suites green; their own mutant sets). Five
low-severity issues; all accepted after checking:
1. **Stock's one-row refresh skips the validator (accepted, docs; S).** Checked in stock's disassembly: MX_ROWS_BUILD's
   message path (r1 non-null, 0x0814E440..0x0814E53C) bounds the id (<= 0x21) and calls MX_SRC_NAME (0x0814E53C) with
   no MX_SRC_VALID; only the full rebuild runs the validator (0x0814E696) before the name (0x0814E6AC). So on a
   stock / BE4.44 rollback an FM Env slot shows [None] after a full rebuild but may show "Envelope 3" / "LFO 3" after a
   routing echo. Checklist section 6 and the release notes now say either is expected. On BE4.46 both paths name it
   FM Env 1 / 2. No code change.
2. **The C replay stubs were not tied to build.py's replay text (accepted, code; M).** fmenv_matrix() now checks the built
   bytes of stock_valid / stock_src_name: exactly `<displaced>; movw r3, #site+5 lo; movt r3, #site+5 hi; bx r3`
   assembled at the stub's address, and its first 4 bytes are stock's entry. The replay text exists once in build.py and
   is checked against both stock and the C. Mutants on a scratch copy (scratch be446/s2fix/mut.sh), each refused by
   the build: the validator replay `ldrh r1, [r1, #6]` (the reviewer's R2, which used to build), the name replay
   `mov lr, r1`, and the name's resume at +7. The release image is unchanged (sha256 ae671b35...c985).
3. **The plan text still said what S2 proved wrong (accepted, docs).** "Superseded: see S2 as built" markers at the M4
   costs bullet (the filler and the shared literal) and at the S1 merge note (wrap the filler after the destinations);
   the history stays.
4. **One step past Auto D (accepted, docs).** Checklist section 2: MX_EDIT accepts index == count, so one step past
   Auto D may show [None], as stock's page does; not a bug.
5. **No test asserted the rollback's displayed name (accepted, test; M).** FmEnvMatrix.test_every_other_page_type_and_id
   now records, per (type, id), both names MX_ROWS_BUILD can show: the id's (its one-row refresh) and the validated
   copy's (its full rebuild). Stock Wavetable 3 / 6: "Envelope 3" / "LFO 3" and "[None]"; ours: FM Env 1 / 2 on both.
   Every other (type, id) is still stock's on all three fields. Calling MX_ROWS_BUILD itself on stock would need its
   widget rows; the two calls are the ones it makes (issue 1), so this tests the same composition.

Rebuilt into ~/bento/out/be446-s2f-rel (release, sha256 ae671b35...c985, the same image as be446-s2-rel: the change is
the build guard and the tests) and the tagged ~/bento/out/be446-s2f (BE_TAG=s2f). Suites on be446-s2f-rel, from the worktree's mods/m1 (M, skips in brackets), all OK: diag 92 (6), polyat 51 (2),
conditions 18, holdstep 18, controls 62, retrig 43, step 95 (16), mod 24 (20), notemod 16, notemod_route 8,
notemod_real 14, slice 20, s3_m7 25, step_xml 11, keys 10, native_props 30, analog 219, inst_xml 5 (5), synth_ui 64 (2),
handles 16 (16), note0 32, p3_golden 52; drums / drum_ui not run (drums omitted). Sizes are S2's (DSP 31,084 B, 9,620 B free; synth UI 16,054 B; image 0x189520). CPU is unchanged from S2 as
built (no M7, DSP or M4 byte changed): P3Identity worst 1.0170, heaviest routed steady voice 34,826 vs the 35,309
ceiling; no ceiling raised.

**Kit.** flash\bento_enhanced-v4.46-envsrc (git-ignored, never committed), packaged with package_m1.sh from
be446-s2f-rel, plus DEVICE_CHECKLIST.txt (= docs/checklist-v4.46.txt) and SHA256SUMS.txt regenerated over all files.
It replaces the earlier flash\bento_enhanced-v4.46 kit (same image; that folder had the old checklist). bento1.bin
sha256 ae671b35a3cc183542568c8d3f20509786372f492738902e5000ebe19d5ec985; DEVICE_CHECKLIST.txt b8a8ff3c...ba0a;
SHA256SUMS.txt verified (sha256sum -c).

## Merged into BE4.46 (branch be446)

Merged with the destinations lane (be445x, X1..X3). Conflicts, resolutions, the re-measured test_cost bounds, CPU,
sizes and suites: `docs/plan-fm-targets.md`, "BE4.46 merge" (and "BE4.46 merge review": CPU worst cases, Jeff's
rulings of 2026-11-17). The kit `flash\bento_enhanced-v4.46-envsrc` is superseded
by `flash\bento_enhanced-v4.46` (the combined image).
