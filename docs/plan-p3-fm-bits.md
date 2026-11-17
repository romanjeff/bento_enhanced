# P3: FM, Bit Depth and the five-shape Analog oscillator (replaces plan-analog-fm.md's stale parts)

Base: HEAD ed93c39 = BE4.40 on stock 1.6.8. Proposed kit: **BE4.41**. Drums stay BE4.50+ and are out of scope.
Labels: **M** = measured (emulator/build), **S** = static (code/disassembly), **I** = inferred.
Jeff's decisions are taken as given and not reopened: FD1, FD2, FD3, FD5 (as restated), FD6, FD7, FD8, BD1, Bit Depth
Full/12/8/4 per oscillator (not on the sub), FM 1>2 = OSC1 -> OSC2 only, no migration of dev projects.
This revision applies the review of the design draft. The review log at the end lists each issue and what was done
with it.

---

## 0. Status: what changed since S6f (plan-analog-fm.md, 2026-11-08)

| Old plan item | Now | Label |
|---|---|---|
| F0 octave bug, tuner "blocking", FD9 | Resolved, there is no octave bug. WT1, WT2 and the sub all render at 2N = 256 frames in a 96 kHz domain (divider 2 at osc+0x108, sub +0xDC = 2), and stock's FIR 0x0807FCC0 decimates. Note 60 plays 261.62 Hz. | M |
| F0 bug 1 (sync never engages at n = 256) | Fixed in BE4.40 (B1). The render is chunked at AN_EVENTS = 128. Both-Analog sync works up to AN_SYNC_FRAMES = 256, and mixed sync has no limit. | S (M in plan-be440) |
| F0 bug 2 (glide runs at half the set time) | **Fixed in Q0** (see "Q0 as built"). BE4.40's `glide_coef` computed `0.001^(n/(s*48000))` with n = 2N. The glide tests ran in `Rig()` (48 kHz, n = 128), so they could not see it. | S (M after Q0) |
| F1 labels, title hook, 2.2, 2.3 | Done a different way in BE4.40 (T0/T1, J2). Not P3 work. | M |
| AN4 second word, AN43, canary shrink, SaTrack.r4, SA_LAYOUT 3, `bento_analog_fm_*` XML, an_save/an_load, 0x1A8, FD4 | All obsolete. Values are now stock properties (N1/N2/N3). AN4 is the 256 B UI block 'AN50'. Stock's writer writes the tokens on each `<cell>`. | S |
| Keys | 0x18B Sub FM (both rows), 0x132 FM 1>2 (OSC2 only), 0x1A4 Bit Depth (both rows). All three are empty after stock's REG_BUILD and after BE4.40's N2. None is an immediate in either core, and none is one of `claim()`'s keys. | M (test_keys FM_NEXT) |
| M7 storage | Already reserved: `AnProps.bits` (u8), `.subfm` and `.fm12` (u16) at osc+0x1C0..0x1CC. AnOsc uses 172 of 180 B. | S |
| Position anchors 0..255 (4.5) | Gone. Shape is its own key 0x1A7 (0..127, default 48 = saw). | S |
| Sub at Level 0 | **Stock never skips the sub render.** The phase advances by exactly 2N*inc at Level 0 (Noise by its stride). | M (p3/sub/lvl0.py) |
| Sub copy bit-exact | **Re-proven at the device shape** (+0xDC = 2, n = 256). A one-shot `sub_copy` started from OSC1-time state matches stock's raw core output (worst 0) for all 5 waves at Level 0 and 1000, across note changes, a +0x04 write, retrigger, PW 30 % and detune. Two independent 128-frame calls **fail for Square** (worst 2.0), and fail for Noise unless it steps by its stride. This proves the *raw* (non-DC-free) form only (see 4.2). | M |
| 512 B modulator buffer | Too small at 2N: 256 floats = **1 KB per signal**. | I (arithmetic) |
| Costs and budgets (old 6, 7) | These were n = 128 numbers. Restated in section 5. | M/I |
| 4.6 phase reset, Nice 2/3 | Not built (FD7, FD8). | S |

Still valid from the old plan: three routings and no OSC2 -> OSC1; the FD3 square law with kin = I * inc_mod; the
through-zero kernel with mirrored BLEPs and one division per edge; the VDIV-hoist watch; the one-shot sub copy; E3 (a
silent OSC1 still renders when it is a modulator); the Bit Depth quantiser in the 96 kHz domain (its grid is now OD11's
two's complement, 4.7); BD1.

---

## 1. Requirements (prioritised)

### Must
1. **M1 Zero cost and bit identity when off.** If all three FM amounts on an oscillator are 0 and its Bit Depth is Full,
   its output must be bit-identical to BE4.40 in the **identity set** (6.2). That set is every existing Cost, Voice,
   Sync, Shape-mod and Glide case with Shape >= 45 (whole values and 1/512-modulated), with glide cases only in `Rig()`.
   The instruction count must stay within +2 % of BE4.40.
   Two groups of cases are **expected to change**, and each gets its own golden or expected-diff test instead:
   - Shape < 45: these renders now play sine or triangle. test_analog.py:1239-1240 (`run_(20, -1.0)` / `run_(0)`,
     "clamped at 0", labelled saw) is one of them (S).
   - Glide in `Rig(dev=True)`, after Q0 (OD1): the time doubles to the correct value (S).
2. **M2 Sub FM on OSC1 and on OSC2** (FD2): independent amounts, amplitude unit (FD1: the sub's Level is ignored),
   through-zero, square law to index 8 (FD3). It must also work on a mixed voice (OSC1 Wavetable, OSC2 Analog).
3. **M3 FM 1>2**, OSC1 -> OSC2 only. OSC1 must be Analog. OSC1's crushed pre-Level signal is the modulator (BD1). It
   must work when OSC1's Level is 0.
4. **M4 Bit Depth Full/12/8/4 per Analog oscillator**, not on the sub. It runs in the 96 kHz domain, before Level and
   before the FIR.
5. **M5 Five shapes:** 0 sine, 24 triangle, 48 saw (default, unchanged), 88 square, 127 pulse at 8 %. Shape 48..127
   stays bit-identical to BE4.40. The M4 value text and the dial show all five shapes.
6. **M6 Works with sync:** both-Analog sync (OSC1 master with its own Sub FM, OSC2 slave with FM) and mixed sync (WT1
   master, OSC2 Analog slave with Sub FM).
7. **M7 Save, load, copy and presets** keep the three values through stock's own paths, with no new hooks. The values
   survive a Type flip. Offline coverage: project writer/reader, clone, child-dup, STORE_COPY_ALL, STORE_CLONE_H
   (preset apply and paste). **A preset written to a file and read back is device-only (I offline)**, unless Q1 finds the
   preset writer's entry and adds the round trip (6.1). This gap already exists for BE4.40's keys (S: no preset test in
   test_native_props).
8. **M8 No regressions** in test_keys, test_native_props, test_synth_ui, test_analog and test_drum_ui. Tests that encode
   BE4.40 behaviour which P3 changes on purpose are listed in 6.7 and edited there as reviewed changes.

### Should
9. **S1 Amount smoothing:** no 375 Hz zipper noise when an FM knob turns.
10. **S2 Glide time fix** (F0 bug 2). FM sidebands are only right during a glide if sub_inc and the carrier inc both
    move at the right rate. See OD1.
11. **S3 Black box:** `fm=` (renders with FM), `bits=` (crushed renders), `fmx=` (FM skipped or missed), next to
    `synced=` and `shape_mods=`. SaStats has no free word, so these need a new stats line (3.3).

### Nice (not in BE4.41)
- Mod Matrix destinations for the amounts (Nice 5, deferred).
- Specialised FM loops, only if the cost gate fails.
- FM 1>2 from a Wavetable OSC1 (4.5 explains why it cannot be done cleanly).

---

## 2. Controls

### 2.1 Keys and descriptors (M4: m4_synth.c `NP_KEYS[]`, N2 `np_keys()`)

| Key | Title | Type | Range / labels | Token | Default | Rows |
|---|---|---|---|---|---|---|
| 0x18B | `Sub FM` | 10 | 0..1000 (stock's widget: `0.0%`..`100.0%`) | `bento_subfm` | 0 | 0 and 1, slot 5 |
| 0x1A4 | `Bit Depth` | 7 | `Full`, `12`, `8`, `4` | `bento_bits` | 0 (Full) | 0 and 1, slot 6 |
| 0x132 | `FM 1>2` | 10 | 0..1000 | `bento_fm12` | 0 | 1 only, slot 7 |

- Type 10 shows a percentage, as Glide does (`0.0%` is M; the non-zero format value/10 is I, pinned by a test in 6.1).
  Here FD3's "shown as %" means percent of the knob's travel, and the DSP squares it: I = 8 * (v/1000)^2. No value-text
  hook is needed. (I: this reading of FD3 matches the old plan's 3.3.)
- Type 7 lists show their labels natively, so Bit Depth needs no value-text hook either.
- **`NP_KEYS` stays one all-or-none group, now 7 keys** (recommended in OD2). `np_ok()` and the `keys=` black-box field
  are unchanged (`keys == 1`, printed `ok`, S: m4_synth.c:422, :1320). The `0x100|taken` bits widen to 7.

### 2.2 Row layout (`want_row`, m4_synth.c:499)

```
row 0 (OSC1), Analog:  0xC4 Level | 0x03 Pitch | Shape | Type | Glide    | Sub FM | Bit Depth | (0)
row 1 (OSC2), Analog:  0xC4 Level | 0x03 Pitch | Shape | Type | Sync 1>2 | Sub FM | Bit Depth | FM 1>2 (only if OSC1 is Analog)
Wavetable rows:        stock's keys + Type in slot 3 (unchanged)
```
- Only slots that stock leaves empty get a key (`if (!k[5])`, as today). Stock's rows 0/1 are
  `{0xC4,0x03,0xC9,0,0,0,0,0}` (S, dump of 0x081A17E0), so slots 5..7 are empty.
- Slots 5..7 are real encoder slots: stock's row 2 uses all 8 (M). Keys above 0xDA bind to the row's handle (M, P0 T5).
- **Hidden rule:** on row 1, slot 7 holds `FM 1>2` only when `me && osc1`. This uses `want_row`'s unused `osc1`
  argument (remove the `(void)osc1`). While OSC1 is Wavetable, slot 7 stays 0, so the control is hidden (OD3).
- `fix_rows` heals the rows on every tick and on A6, so a Type flip of OSC1 shows or hides FM 1>2 within one tick (S).

### 2.3 Value texts
- In `value_text`/`value_texts` (m4_synth.c:817-846), only Shape changes: V1 gets the five-shape text (4.6). The loop
  stays on slots 2..3. Slots 5..7 get no text (OD3 = hide, OD4 = accept the type-10 out-of-range display).
- V1 texts: `Sine`, `Sin>Tri n%`, `Tri`, `Tri>Saw n%`, `Saw`, `Saw>Sq n%`, `Square`, `Pulse n%`. The last three are
  unchanged.

### 2.4 Resend and clamps
- `resend()` (m4_synth.c:902): `KEYS[3]` becomes `KEYS[6]` = Shape, Sync, Glide, Sub FM, Bit Depth, FM 1>2. FM 1>2 only
  matters for index 1, but sending it for index 0 is harmless. Without this change a Type flip on the page loses the FM
  values, because the M7 drops them while the oscillator is Wavetable (S).
- SET_PROPERTY is flow-controlled, not lossy: a 64-slot ring that spins on 0x08121A9C while the backlog is above 28, so
  no message is dropped (S, from the reviewer's reading of 0x0810B97C..0x0810B9C4; not re-read for this revision). The
  cost of 6 posts per resend is back-pressure: the view tick can stall briefly while the M7 is slow (I, small).
- The key filter in M7 `an_prop` (analog.cpp:175) gains 0x18B, 0x1A4 and 0x132. While `is_analog`, it stores:
  - `subfm = clamp(v, 0, 1000)`
  - `fm12 = clamp(v, 0, 1000)` (WT1's copy is stored but ignored)
  - `bits = (u32)v <= 3 ? v : 3`. An out-of-range value plays as `4`, which is what stock's widget shows (I; OD4).
- Becoming Analog (Type 1) still resets the state with Shape 48. `bits`, `subfm` and `fm12` start at 0 after
  `forget()`, and the real values then arrive from the push or the resend (template order: Type first, S).

---

## 3. Storage

### 3.1 Native properties (M4)
- `build.py`: append `(0x18B,0), (0x1A4,0), (0x132,0)` to `NP_TEMPLATE_KEYS` (1708-1712), after Glide. Push order
  follows template order (M).
- The count goes from 0x300 to 0x303, padded with one type-0xFF entry to **0x304**. The patched sites are LIT_A
  0x0812EA44, LIT_B 0x0812EB4C, CMP_A 0x0812EA1C, CMP_B 0x0812EA80 and LAST 0x0812EA6A (`movw r7,#0x303`). The data grows
  by 32 B (S).
- **This is the first kit that ships a pad entry** (BE4.40 has 0 pads, S: test_native_props.py:237). Type 0xFF is not
  inert: on the patched table CHILD_CREATE(0xFF) succeeds and makes a one-property object, where on stock it fails
  (S/M: plan-native-props.md:186). Stock's default lookup matches on (type, key) (S: 0x0812EA0C..0x0812EA42), so the pad
  is reachable only through a type-0xFF object. The load-bearing test is therefore "no reader path creates type 0xFF",
  and it does not exist yet. Q1 adds it (6.1).
- `np_template()` refuses a key that is already in stock's template (build.py:1727). None of the three is (S).
- Every type-6 object gets all three keys, so WT1 also saves `bento_fm12`. The M7 ignores it and the page never shows
  it.

### 3.2 XML and compatibility
- The tokens `bento_subfm`, `bento_bits` and `bento_fm12` go on each oscillator `<cell>`. Stock's instrument writer
  writes them, INST_LOAD reads them, and NODE_CLONE, CHILD_DUP, STORE_COPY_ALL and STORE_CLONE_H copy them. M for
  BE4.40's keys; I for the new ones (the paths are key-generic). Extending test_native_props proves it.
- Stock 1.6.8 ignores the tokens (M for BE4.40's; I for the new ones until 6.1 runs).
- **Rollback to BE4.40** is the most likely downgrade, because BE4.40 is flashed first. BE4.40 has no descriptors for
  the three tokens. Its reader should skip them (I), it then plays without FM or crush, a Shape below 45 plays saw, and
  its next save drops the three values (S: plan-native-props.md:177 states the same drop-on-resave for older
  firmware). This goes in a test (6.1), the release notes and the checklist.

### 3.3 M7 delivery and state
- Path N3: vtable slot 8 -> trampoline -> `an_prop(osc,msg)`, between renders (S). `an_osc` reads the props fresh on
  every render.
- `AnProps` (osc+0x1C0) does not grow: `bits`, `subfm` and `fm12` are already reserved.
- `AnOsc` spare 8 B (172..179):
  - **`u16 msub`**: last block's Sub FM amount, 0..1000.
  - **`u16 m12`**: last block's FM 1>2 amount (OSC2 only).
  - They drive the amount ramps. 4 B stay free. `static_assert(sizeof(AnOsc) == 176)`. `reset()`/`forget()` clear them,
    and so does the silent-skip path (4.4), as it does for `mshape` today.
- Shared modulator buffers: repurpose **SA_UI (SA+0x800, 2 KB)** as `SA_FMBUF`.
  - `A` = SA+0x800: 256 floats, the sub copy in increment units.
  - `B` = SA+0xC00: 256 floats, OSC1's crushed output in increment units.
  - They end exactly at SA_DKTAB 0x1000. The area is M7 write-back cached, and the M4 never reads or writes it (S, grep).
    Update the synth_shared.h comment and add a layout static_assert. (Alternative: the spare region at SA+0x4600.)
- Validity tag in `Run.r9` (`fmtag`): bit 0 = A valid, bit 1 = B valid.
  - `an_enter` clears it **right after `r->voice = voice` (analog.cpp:279) and before the `!a1 && !a2` return (:280)**.
    The earlier return at :269 (`!root || !voice || !area_ok()`) leaves `r->voice` stale too, and `an_osc` returns on
    `!area_ok()` before touching any shared area (S: analog.cpp:382). The tag is only meaningful while
    `Run.voice == v`.
  - Once filled, A stays valid for the whole voice-block, because nothing writes the sub before it renders (M; see 4.2
    for the one exception sub_copy itself would create, and its fix).
  - `Run.r10` holds A's `inc_sub` (float bits) for the golden checks.
- **New stats line `SA_FMSTATS` at SA+0x100** (32 B; SA+0x100..0x200 has no definition today, S: synth_shared.h).
  SaStats is static_asserted at exactly 64 B with every word used, and SA_DSTATS follows it at 0x80 (S:
  synth_shared.h:62-78, analog.cpp:104), so the new counters cannot go there.
  - Fields: `fm` (renders with FM), `bits` (crushed renders), `fm_skipped` (n > 256, no FM), `fm_missed` (OSC2 wanted B
    but it was not valid). The M7 writes it and cleans it after each block, like SA_STATS. `an_print` reads it.
  - SA_LAYOUT stays 2: the change only adds a region, both cores ship in one kit, and no existing offset moves (I; OD9).

---

## 4. DSP design (dsp/analog.cpp, dsp/analog_kernel.cpp, dsp/analog.h)

### 4.1 Signal flow per voice-block (render order OSC1, OSC2, sub, M)

```
an_enter(root,voice): r->voice = voice; Run.fmtag = 0; glide, sub +0x04 write
OSC1 (which 0, Analog):
   needA = I_s1 > 0 || msub > 0                     -> sub_copy 2N into A, fmtag |= A
   render 2N into s (FM loop if needA, else today's an_render), role FREE/MASTER
   per part: render (an_render / an_render_fm), then an_crush(s+off, m, bits, an_dc(k)) if bits != 0  (Q2b, OD12)
   if OSC2 Analog && (fm12(o2) > 0 || m12(o2) > 0): B[i] = inc1 * s[i]; fmtag |= B   (BD1: after crush, before Level)
      (Q6 as built: B per part, from the uncrushed s on OD15's grid, top at S (an_feed), before the audio crush;
       a silent OSC1 feeds B uncrushed: see "Q6 as built")
   SMOOTH_APPLY + MIX (only if !silent)
OSC2 (which 1, Analog):
   needA = I_s2 > 0 || msub > 0; if !(fmtag & A) -> sub_copy into A (mixed voice, or OSC1 skipped)
   useB  = OSC1 Analog && (I_12 > 0 || m12 > 0) && (fmtag & B)   (B missing -> no FM 1>2 this block, fm_missed++)
   render with inc_i = inc + I_s2*A[i] + I_12*B[i]; role FREE/SLAVE (both-Analog or mixed sync)
   Bit Depth, SMOOTH_APPLY, MIX
sub: stock's render, untouched
```
`inc1` is OSC1's base increment, before its own FM, so kin = I * inc_mod stays referenced to OSC1's set pitch. Both the
sub inc and the carrier inc are per-96 kHz-sample cycles, so kin is domain-invariant (S).

### 4.2 Sub copy
- Port `sub_copy` (rc26/dsp/proto/fm_proto.cpp:285) and `sub_inc` into analog.cpp. `sub_inc` follows stock's formula:
  oct = int(+0x0C)/1000/12 + param(+0x84) + f(+0x04) - (u32(+0xDC) - 1); hz from 0x0804F680 then 0x0804F5E0; inc =
  u32(hz/48000 * 2^32) (S). At +0xDC = 2 this is already in 96 kHz-sample units, like the carrier's `inc = rate/2048`
  (M).
- **Copy the whole 2N block once, before the chunk loop.** This is the form measured exact. A per-chunk copy is wrong:
  Square loses its latched PW at the chunk edge (M, worst 2.0).
- **PARAM_VALUE writes a cache.** It stores its result at param+0x3C (S: 0x080A0A52 `vstr s0,[r0,#0x3c]`; analog.cpp:82
  PARAM_CACHE). `sub_inc` therefore writes sub+0xC0, and the Square case's PW read writes sub+0x120. The sub's own render
  probably recomputes both from the same inputs (I), but the "sub byte-identical before its render" premise is then not
  true by construction. Fix: save and restore both cache words around the calls, as `wt1_oct` does (analog.cpp:311-319).
  Test in 6.4.
- `A[i] = inc_sub_f * (y_sub[i] - dc)`, with `inc_sub_f = inc_u32 * 2^-32`. **DC handling (OD10, recommended
  DC-free):**
  - Stock's raw Square sub at PW != 50 % has mean 1 - 2*pw. Through-zero FM by a modulator with DC shifts the carrier's
    mean increment by I*inc_sub*dc. At I = 8, PW 30 %, that is +3.2*inc_sub (S/I: arithmetic), a large detune that
    grows as Sub FM is turned up.
  - The prototype already has the `dcfree` flag for this (S: fm_proto.cpp sub_copy case 3: `dc = 1 - 2*pend*2^-32`).
    With it, Square subtracts its PW-derived DC; the other waves use dc = 0.
  - Exactness becomes "bit-exact up to the DC constant": the test checks raw = A/inc_sub_f + dc, worst 0 (6.4).
  - The Rev Saw copy has a one-sample ramp at the wrap, so its mean is not exactly 0. The residual is about 1/period
    and is accepted (I).
  - The sine sub uses the prototype's `exact` mode (stock's table), the one measured bit-exact (S: fm_proto.cpp).
  - Noise steps by its core+0x10 stride (M). The sub's Level is never read (FD1).
- If n > 256 (the buffer size), there is no FM that block: A and B are not filled, the oscillator renders plain, and
  `fm_skipped++`. On the device n is 256 (M).
- **Q4 as built (M/S, beyond this text):** the fill is `sub_fill` (analog.cpp) + `an_sub_copy` (analog_kernel.cpp,
  `minsize`), called from an_osc after the skip path and before E3's silent-master branch, once per voice-block (fmtag A),
  only for the voice an_enter named. A is `(y - dc) * inc 2^-32` in float32 and is tested bit for bit in that forward form
  ("raw = A/inc + dc" does not invert two float32 roundings: within 2^-22). The prototype's `exact` / `dcfree` flags are
  gone (always stock's table sine, always DC-free). Stock's core has **10** waves; the sub's key 0xC3 stores value + 1
  unclamped (0x080A0CC6, S), so a hand-edited file reaches 6..10 or 0: their copy is all 0 (no Sub FM). Noise's stride is
  **random per block** (`rand() % 256 + 1` after each render, 0x08056AFE..10, S); the copy reads this block's. The
  Square's dc is the new width's from the block's start, but before the first wrap the old width (+0x18) plays, so a PW
  change leaves a DC of 2 (pend - w) 2^-32 until that wrap (accepted, like the Rev Saw residual; at low sub pitches it can
  last more than one block: Q5's mean-frequency golden case holds PW constant, or bounds it). "Q4 as built".

### 4.3 FM kernel: one generic through-zero loop per role
- New function in analog_kernel.cpp:
  `an_render_fm(AnOsc*, float inc, float shape, float *out, u32 m, u8 *ev, int role, const FmIn *f)`.
  - `FmIn {const float *a, *b; float ia0, ia1, ib0, ib1; u32 off, n;}` points at A/B from the absolute frame `off`.
  - The indices ramp linearly from block start to block end over the whole 2N, so chunked parts continue the same ramp.
- Per sample: `di = inc + ia*a[i] + ib*b[i]`, clamped to [-0.45, 0.45]. The clamp keeps one wrap per sample, which the
  2-point BLEP needs. At I = 8, ratio 1 it engages for carriers above about 4.8 kHz at 96 kHz (I, arithmetic confirmed by
  review).
- Through-zero: p may move backward. A backward crossing of 0 (a wrap) or of `width` (the pulse edge) emits a mirrored
  BLEP (the step's sign flips). The edge fraction `t = (p - edge)/di` costs **one VDIV per edge**. E2's per-block `rinc`
  is invalid under FM. With a narrow pulse the phase can cross both 0 and `width` in one sample, so edges per render can
  reach about 2n (I).
- Shape: the generic loop computes the shape terms per sample from the block's `Shape`. That covers the saw, pulse and
  mixed weights and the new sine/triangle weights (4.6), with no specialisation by regime (FD6: generic first). The only
  template parameter is ROLE, so there are 3 instances (FREE, MASTER, SLAVE).
- **MASTER** writes an event byte for every wrap, forward or backward (OD5). With |di| <= 0.45 there is at most one wrap
  per sample, so the per-sample event array is exact, and `an_master`'s chunk-edge problem does not arise (S).
- **SLAVE** splits the sample at the event, but must define the case today's code never sees. `an_render`'s slave
  branch is gated on `k.inc > 0.f` (S: analog_kernel.cpp:93). Under FM, di can be <= 0. Resetting to p = 0 and then
  moving backward would wrap at once to ~1: a second edge in the same sample, with two overlapping BLEPs. Rule
  (recommended, OD5):
  - di > 0: as today. Step correction to `wave(0)`, p = 0, continue forward.
  - di < 0: reset to the cycle boundary in the direction of travel, i.e. p = 1 (the same point as 0, approached from
    above). Apply one step correction to `wave(1-)` and continue backward. That is one edge, not two.
  - di == 0: no reset (the phase is not moving).
  The golden model implements the same rule, and a mutation (the double-edge form) must turn a test red (6.6).
- The Shape ramp (J4, AN_RAMP parts) keeps working. The FM loop is called per part at the same place as `an_render`
  (analog.cpp:479), with `ev` and `s + off` as today. The `an_reshape` correction is unchanged.
- The non-FM path is today's `an_render` call, with unchanged instructions (M1).
- **Q5 as built, beyond this text:** the generic sample runs only where a sample crosses an edge or a slave has an event
  (`fm_slow`, out of line, shared by the roles); every other sample takes a correction-free fast path, versioned on the
  wave's form (LOW / MIXED): 6 small loops + 1 shared sample, not 3 instances (CPU: 41.6 -> 29.4 instructions a sample for
  the saw, the gate failed as written). VDIVs are counted over an_render_fm and fm_slow. See "Q5 as built".

### 4.4 E3 and skip rules (analog.cpp:428-444)
- (a) OSC1 is silent, OSC2 is Analog, and `fm12 > 0 || m12 > 0`: do not take the `(silent && !master)` early return.
  Render into s, crush, write B, skip SMOOTH/MIX. **Q6 as built:** render, write B per part (an_feed: OD15's grid, from
  the uncrushed s), no audio crush (nobody hears it; not counted in bits=), skip SMOOTH/MIX; see "Q6 as built".
- (b) OSC1 is a silent master (both-Analog sync) with **`I_s1 > 0 || msub > 0`**: `an_track` ignores FM, so run
  `an_render_fm` in the MASTER role into s, not mixed. Testing msub too means a master ramping its Sub FM down does not
  fall back to `an_track` mid-ramp. With both at 0, keep `an_track` for bit identity.
- (c) OSC1 is silent and only OSC2 has Sub FM: OSC1 may still skip, and OSC2 fills A itself.
- (d) OSC2 is silent: skip as today. It is nobody's modulator.
- **Skip path resets the ramps.** When an oscillator takes the silent skip, set `msub = m12 = 0`, as the skip path does
  for `mshape` (S). Otherwise the oscillator would ramp from a stale amount when it is heard again. After silence the
  amount starts at its set value with no ramp. (Q5, M: not so: msub 0 is a real amount, so the first block heard ramps the
  index in from 0, 2.7 ms; kept as built, open decision **OD16**.)
- Ramp-down: if a block's amount is 0 but `msub`/`m12` is still > 0, the block takes the FM path and ramps to 0. The last
  value is then set to 0, so the next block is bit-identical non-FM.

### 4.5 Mixed sync, and why a Wavetable OSC1 cannot modulate FM 1>2
- Mixed sync (WT1 stock, OSC2 Analog SLAVE via `an_master` events): OSC2's Sub FM works. OSC2 fills A and runs the
  SLAVE FM loop, and `an_master` is unaffected. FM 1>2 is not available, because OSC1 is not Analog.
- **A Wavetable OSC1 as the FM 1>2 modulator is out of scope (S).** Stock's WT render applies Level and mixes into the
  voice accumulator inside itself, so OSC1's pre-Level signal is never in a buffer we can read. Recovering it would mean
  running a second copy of stock's wavetable kernel: several KB of code with its own exactness proof. So the control is
  hidden (2.2), and the M7 ignores it (`useB` requires `is_analog(o1)`).
- Both-Analog sync: OSC1 as MASTER may itself be FM'd by Sub FM. OSC2 as SLAVE may be FM'd by Sub FM and FM 1>2. B is
  OSC1's pre-Level signal, including OSC1's own FM (Q6 as built: with a Bit Depth, on OD15's grid with its top at S, not
  the audio's crush; see OD17 for the DC OSC1's own Sub FM puts into it).

### 4.6 Shapes: sine and triangle below the saw
- `an_shape(s)`:
  - For s >= 48: BE4.40's formulas exactly (48 -> 0, 88 -> 0.5, 127 -> 1).
  - For s < 48: a negative kernel coordinate. k = -1 is sine, -0.5 triangle, 0 saw, linear between them, with
    one-sided plateaus. The widths are OD6; the recommendation is sine 0..2, triangle 22..26, saw 45..48.
  - BE4.40's saw plateau was 0..48, so 45..48 stays saw and nothing at or above 48 changes.
- Kernel `set_shape` clamps k < 0 to 0 today. A new branch for k < 0 sets `tw` (the triangle weight):
  - k in [-1, -0.5]: sine (1 - tw) + triangle tw
  - k in (-0.5, 0): triangle tw + saw (1 - tw)
  - This matches the prototype's ST/TS regimes (fm_proto.cpp:18).
- Non-FM render: add **one generic regime, LOW** (k < 0), to `by_regime`, x 3 roles = 3 instances. The alternative,
  SINE/TRI/ST/TS x 3 roles = 12 instances, would cost much more code (I).
  - The saw part's BLEP at the wrap is scaled by (1 - tw).
  - The triangle's corners at p = 0 and 0.5 get a 2-point polyBLAMP, scaled by tw * the slope change.
  - The sine uses the prototype's polynomial `sinq` (no table). Its accuracy (THD) is not stated anywhere yet. The golden
    model measures it first in Q3, and the Bessel tolerance (6.3) is set from that figure. **Measured (Q3, M):** max error
    8.1e-5, THD -81.9 dB (largest harmonic the 5th, -82.2 dB): 27 dB under the -55 dB the Bessel check needs.
  - **Q3 as built, beyond this text:** a SLAVE reset in LOW also steps the slope (triangle -4 -> +4 past its top, the
    sine's 2 pi sin 2 pi p -> 0), so it gets a BLAMP next to the value BLEP (one sinq per reset). See "Q3 as built".
  - k = 0 still dispatches to SAW, so Shape 45..127 runs today's instances (bit identity).
  - **Q3b:** the two plateaus (k = -1, -0.5) below inc 0.09 run their own path, `run_flat` (a correction-free stretch
    between corners, LOW's own sample at each corner): the triangle bit for bit LOW's, the sine within 1.8e-7. Its cost
    is pitch-dependent (two long-way samples a cycle): at or below the saw voice up to about note 72 (sine) / 90
    (triangle), above it rising to ~2.5 x the saw near FLAT_INC, always below Q3's LOW. The blends stay generic. See "Q3b
    as built" and its review fixes.
- `an_reshape` (analog_kernel.cpp:222-227): replace `wave<MIXED>` with `wave_any(p, Shape)`. It must call
  `wave<MIXED>` unchanged for k >= 0 (bit for bit) and the LOW form only for k < 0. The correction is still a value jump
  only. At AN_RAMP = 32-frame steps the slope change per step is tiny (I), so the reshape gets no BLAMP. Test 6.4
  measures the click energy against BE4.40's saw->square ramp. **Q3 (M): at AN_RAMP the criterion failed** (the LOW map
  moves k twice as fast a Shape step); a slope BLAMP in the reshape changed nothing measurable, so the fix is
  `AN_RAMP_LOW` = 16-frame steps for a ramp with either end below the saw ("Q3 as built").
- M4 (m4_synth.c):
  - synth_shared.h gains `AN_SHAPE_SINE 0` and `AN_SHAPE_TRI 24`, with the plateau constants beside them. Update the
    AN_SHAPE_SAW_END comment ("0..48 plays one saw").
  - V1 `shape_text` gains the two lower segments.
  - J3 dial (m4_synth.c:659-715): glyphs 0 sine, 1 triangle, 2 saw, 3 square, 4 pulse, with `AT[5] = {0,24,48,88,127}`.
    `lit()` applies the 3/4 rule per segment, and the saw sector is no longer drawn wide over 0..48.
  - The kernel and M4 constants change in the same commit (the synth_shared.h comment rule).
- Behaviour changes for the checklist: J4 Shape modulation below 48 now sweeps into triangle and sine (it used to clamp
  on the saw plateau). A BE4.40 project with Shape < 45 now plays sine or triangle (no migration, as decided).

### 4.7 Bit Depth
- Per part, inside an_osc's part loop (Q2b, OD12): `an_crush(s + off, m, bits, an_dc(k))` right after each part's
  `an_render` (Q5: after `an_render_fm`), before `S_SMOOTH_APPLY`. Skipped for Full (neither call is made). A part's
  samples are final once rendered (an_reshape only moves `delayed`), so this is a post-pass over s[0..n) with a per-part
  dc. (Q2 had one post-pass after the loop with no dc; a whole-block post-pass is wrong once a Shape ramp changes dc.)
- **True b-bit two's-complement grid (OD11 = (b), Jeff 2026-11-15; replaces the draft's mid-tread):**
  `q = clamp(__builtin_rintf(x * S), -S, S - 1) * (1/S)`, with S = 2^(b-1): 2048, 128 and 8 for 12, 8 and 4 bits.
  - Exactly 2^b levels (4-bit: -8..+7, steps of 1/8). The top clips at (S-1)/S, the bottom at -1: asymmetric by one
    step, so a full-scale signal gains a small DC and some even-order distortion (accepted: that is the "strict 4-bit"
    grit OD11 chose). (Superseded by Q2b, M: the 2-point BLEP never overshoots, s + dc stays in [-1, 1] on real
    blocks; the top is reached only by the +1 high itself, the bottom only by the crafted probe.)
  - **Found in Q2 (M): the top clip is not only overshoot.** The kernel's pulse is DC-free, `(p < w ? 1 : -1) - dc` with
    dc = 2w - 1, so its high is 2 - 2w: 1.26 at Shape 100, 1.84 at Shape 127. Every Shape above 88 drives s well past +1,
    and every Bit Depth (12 bits included) clips that high to (S-1)/S. At Shape 127 the pulse goes from 1.84 / -0.16 to
    0.9995 / -0.16 (12 bits) or 0.875 / -0.125 (4 bits): 4.7 to 6 dB less peak to peak (I: arithmetic on those levels) and no longer DC-free. The kernel never goes
    below -1 (M: lowest sample -1.000000 over saw, square, pulse, synced, notes 60..115), so the bottom clip is never
    reached by a real block. **And under FM 1>2 (Q6, BD1: B is the crushed s):** the clipped pulse has a mean, so OSC1 at
    any Bit Depth with Shape > 88 detunes OSC2 by kin * mean(B) = I * inc1 * mean(q) (I: arithmetic on the clipped
    levels, BLEP ignored): mean(q) = -0.067 at Shape 127 / 12 bits, -0.045 at 127 / 4 bits, -0.096 at Shape 100 /
    12 bits, so at I = 8 OSC2's mean increment moves by about -0.4 to -0.8 x inc1. Full has mean 0, so the detune
    switches on with Bit Depth: the hazard OD10 removed for the Square sub. **OD12 = (b)** (Jeff 2026-11-15), built in
    Q2b: `q = clamp(rint((x + dc) S), -S, S - 1) / S - dc`, dc the part's own DC term (`an_dc(k)`, the kernel's
    2w - 1, 0 at and below the square), so the pulse is crushed on its [-1, 1] range. M (Q2b): s + dc stays in
    [-1, 1] on real blocks; 12 / 8 bits are within one step of Full (the +1 high's); the mean moves by -f_hi / S only
    (-3.9e-5 at Shape 127 / 12 bits, against -0.070 before). Exactly 2^b levels per constant-dc part, dc off the grid.
  - **Left by OD12 (I/M, for Q6; OD15):** the same -f_hi / S shift on any signal whose high sits at +1, the square
    included (no dc there): OD11 (b)'s asymmetric top. Mean shift: -2.6e-4 at 12 bits (Shape 88, M), about -4e-3 at 8
    bits (I: f_hi / 128), **-0.065 at 4 bits** (M). Under FM 1>2 (detune I * inc1 * mean(q)) at I = 8, OSC1 = OSC2, a
    square OSC1 (f_hi ~ 0.5) moves OSC2 by about **-3 / -55 / -1200 cents** at 12 / 8 / 4 bits (I: 1200 log2(1 - 4 / S));
    a pulse scales with its f_hi (Shape 127, f_hi ~ 0.08: about -0.5 / -9 / -140 cents). So it is audible from 8 bits
    on, not only at 4. OD12 did not ask for this; open decision **OD15**, needed before Q6 writes B.
  - **Use `__builtin_rintf`, not `rintf`.** The DSP build uses -ffreestanding -fno-builtin -nostdlib, so plain `rintf`
    compiles to a `bl`. `__builtin_rintf` inlines to VRINTX.F32 (M: q.cpp compiled with the project flags, compile_dsp's
    `common`). As built (M, Q2): lld itself refuses a plain `rintf` (undefined symbol: compiler_rt has none), but zig's
    compiler_rt **defines** most of the rest of the float libm family under these flags (Q2 review: fmaxf 78 B, floorf
    140, sqrtf 292, roundf 148, truncf 72, expf 364, sinf 1360, all link with rc 0), so a plain call links a software
    routine with no undefined symbol and check_dsp_elf's undefined-symbol refusal never fires. The refusal that holds is
    by name: `DSP_REFUSED_LIBM` (build.py), the whole family (rint, round, fmin/fmax, floor/ceil/trunc, sqrt, fabs,
    fmod, sin/cos/tan and their hyperbolic and arc forms, exp/log/pow, with f / l / no suffix), checked before the
    undefined-symbol check. Load-bearing; tested (BitDepth.test_the_dsp_links_no_libm).
  - **The clamp has the same hazard:** plain `fminf`/`fmaxf` are library calls under -fno-builtin. Write it with two
    ternaries (VCMP + VSEL, or a branch) or `__builtin_fminf`/`__builtin_fmaxf` (VMINNM/VMAXNM on FPv5: I, check the
    disassembly). As built (M, disassembly): the builtins, which give VMAXNM + VMINNM (2 instructions a sample; the
    ternaries gave VCMP + VMRS + VSEL twice, 6); plain `fminf`/`fmaxf` are refused by DSP_REFUSED_LIBM (above).
  - rint rounds half to even, so a sample exactly on a half step rounds to the even level (I: the golden model must use
    numpy's `rint`, which does the same).
  - The golden model and the 6.3 crush cases use this grid.
- The crush runs in the 96 kHz domain ahead of the FIR, so the decimator partly removes its images above 24 kHz (I).
- BD1: B is written from the quantised s, before Level (after the part loop: every part is crushed by then).
  **Q6 as built:** B per part, before that part's audio crush, from the uncrushed s on OD15's grid (an_feed, top at S, the
  part's own dc); a silent OSC1 feeds B and is not crushed. See "Q6 as built".
- Because it runs outside an_render (per part, after it), the non-FM loops stay untouched (M1).

### 4.8 Zero cost when off
- Gates for each oscillator render:
  - `fm = I_s > 0 || msub > 0 || (which==1 && a1 && (fm12 > 0 || m12 > 0))`
  - `crush = bits != 0`
  - `feedB = which==0 && a2 && (props(o2)->fm12 > 0 || m12(o2) > 0)`
- If all three are false, the render takes exactly today's code path. The only extra work is loading and comparing the
  props words, a few instructions within the +2 % budget.
- Bit identity is checked by comparing the new build's outputs with BE4.40's outputs over the identity set (6.2), not
  by reasoning.

---

## 5. Budgets

| Budget | Now (BE4.40) | P3 adds | After | Label |
|---|---|---|---|---|
| DSP window 0x080D0100..0x080D9000 (36,608 B) | 9,708 B used, 26,900 B free | **~10-13 KB** in all. an_render's 9 instances are 4,640 B (~515 B each, M). The generic FM loop has per-sample mirrored edges, triangle BLAMPs both ways, sinq, two modulator reads, a ramp and a clamp, and SLAVE inlines advance twice, so ~1.5-2.5 KB per instance x 3. Plus sub copy ~2 KB, LOW x 3 + shape code ~2 KB, Bit Depth + glue ~0.5 KB. **Ceiling: P3 <= 14 KB**, measured at the end of Q3 and Q5. **Q5 (M): 21,404 B used; P3 11,696 B of 14,336, 2,640 B left for Q6** (an_render_fm 2,196, fm_slow 2,324, an_osc +420). **Q6 (M): 23,772 B used; P3 14,064 B of 14,336, 272 B left** (an_render_fm +864, an_feed 680, an_osc +232, fm_setup 424, part_post 104, feeds 68); 12,836 B free in the window. | ~20-23 KB used; with drums (~6 KB) ~26-29 KB, 7-10 KB free | M (now) / I (adds) |
| M4 image vs PROVEN_MAX 0x18D334 | 0x188DF8, 17,724 B free | ~1.6 KB: template +32 B, descriptors/labels/resend/rows ~0.4 KB, dial + V1 text ~1 KB, an_print FM line ~0.1 KB | ~16 KB free | M / I |
| AnOsc | 172 / 180 B | msub, m12 (4 B) | 176 / 180 B | S |
| AnProps | 12 B | 0 | 12 B | S |
| Shared area | SA_UI 2 KB unused; SA+0x100..0x200 undefined | A 1 KB + B 1 KB in SA_UI; SA_FMSTATS 32 B at SA+0x100 | | S |
| Run | r9..r15 spare | r9 fmtag, r10 inc_sub | r11..r15 spare | S |
| CPU per voice-block, n = 256 (hooks only) | saw 13,192; mixed 19,840; saw+sync 15,785; mixed+sync 22,180 | full FM voice (sine sub -> both, 1>2, saw). **Q3 (M), FM off:** a held LOW Shape (sine, triangle or a blend) ~23,470 = 1.18 x mixed, 1.77 x saw; tri+sync 25,833; a LOW Shape ramping every block 29,554 = 1.29 x the saw > square ramp (22,859). **Q3b (M):** at note 60 sine 13,100, triangle 11,760 (saw 13,236); sine+sync 14,357, tri+sync 13,215 (saw+sync 15,845); blends ~23,500 as Q3 (+20 a call); LOW ramp 30,242 = 1.32 x. The plateaus' cost rises with pitch (an_render free per sample against the saw: sine 1.00 x at note 72, 1.27 x at 96, 1.64 x at 108, 2.24 x at 119; triangle 0.98 x at 90, 1.44 x at 108, 2.05 x at 119), below Q3's LOW (~31-35) up to FLAT_INC (inc 0.09, note ~120), LOW's cost above. Worst case: a plateau slave under a master above ~24 kHz (an event every ~4 samples) costs more than LOW, up to 1.29 x at the clamp (sine 97.3 against 75.2 a sample) | **Gate (FD6 restated): generic full-FM saw voice <= 1.3 x the specialised full-FM voice at 2N, provisionally ~31 k**; **Q5 (M): fixed at 29,913 (Sub FM -> both) / 30,906 (Q6's full voice); the Sub FM voice 27,069 (0.905), synced 28,982.** **Q6 (M): the full voice 30,753 = 0.993 of 1.3 x (13,346 + 10,480) = 30,974 on this build's off voice; FM 1>2 alone 18,974; FM off worst +1.68 % (0.32 pt left).** Q6 review: Fm12.test_cost asserts both the gate on this
build's off voice and Q5's fixed 30,906 (the gate formula tracks the off voice, so the fixed figure keeps an FM-off
regression from widening it). The gate is the saw carrier's; a triangle carrier (Shape 24 on both, LOW's FM loop) measures **33,407 = 1.117 x it** (not gated; device item 12). **FM off, M1's +2 %:** Q5's worst P3Identity case is +1.57 %, so **~0.43 pt is left for Q6's** feedB / fm12 / m12 gates on the same path | M / I |
| Block budget | 1.28 M cycles per 128 output frames | per root with 10 full-FM voices: ~+110-130 k **instructions** (I). Device CPI is unmeasured (code runs from flash), so this is not a cycle figure. Worst case: several Analog tracks with FM chords multiply it, and it is not bounded offline. | | I |
| Message load | 4 set_property per oscillator per push | +3 per oscillator per push or resend | the ring blocks, it never drops (2.4) | S |

How the CPU gate is derived:
- FD6's old gate applied 1.3x to the **whole specialised voice**, not to its delta (S: plan-analog-fm.md:244, :292:
  "Full voice: sub sine -> both, OSC1 -> OSC2, saw | 12.8 k | 18.3 k (+5.5 k)", and "within 1.3x of the prototype's
  specialised figures").
- At 2N the per-sample delta scales by about 2.0, not by the hooks' total ratio 1.81 (13,192 / 7,304, M), because the
  1.81 includes per-block fixed cost that does not double. So +5.5 k becomes about +11 k (I).
- The +5.5 k row already contained the sine sub copy, so it needs no extra headroom for that.
- Faithful restatement: 1.3 x (13.2 k + 11 k) = about 31 k (I). This is provisional. Q5 measures the specialised delta
  at 2N with the prototype rig (rc26/dsp/proto) and fixes the gate from that measurement.
- **Fixed in Q5 (M, the prototype at 2N, scratch p3/q5/proto_cost.py):** a Sub FM saw oscillator +2,600, one writing B
  +3,364, the sine copy 4,398, sub_inc's stock part 118. Gate for Q5's voice (sub -> both) 1.3 x (13,294 + 9,716) =
  **29,913**; for Q6's full voice (+ OSC1 -> OSC2) 1.3 x (13,294 + 10,480) = **30,906**. Q5 measures 27,069 (0.905).
- A division hoisted into the per-sample loop would cost about 257 VDIVs per render at 2N (I). 6.5 watches for it.

---

## 6. Tests (mods/m1/test_*.py; suite `/mnt/c/Users/rjrom/bento_mods/wsl/test_m1.sh`)

### 6.1 Keys, native props and UI (test_keys.py, test_native_props.py, test_synth_ui.py)
- test_keys:
  - Move FM_NEXT into `ALLOW['analog']`.
  - `cover(...) == [SHAPE, SYNC, BITS]` (test_keys.py:233 today asserts `[SHAPE, SYNC]`). 0x1A4 is in 0x18C..0x1B6,
    which only acts on the reverb handle (M).
  - The registry test expects 7 descriptors (title, type, labels, range, token).
  - Taken case: pre-fill 0x18B. No key is written, `keys=` shows TAKEN with bit 4, and the page shows the stock rows.
- New runtime check (OD7): emulate the M7 set_property entry 0x08059960 with keys 0x1A4, 0x18B and 0x132 on an
  oscillator handle. The message must reach slot 8 / `an_prop` intact, and the flag table root+0x8CB2+key must be 0 for
  all seven keys.
- test_native_props:
  - OURS/KEYS get the 3 new keys.
  - The template has 0x304 entries with exactly 1 pad entry. `test_every_object_gets_stocks_keys` re-runs at 0x304.
  - **Type 0xFF hazard (new):** CHILD_CREATE(0xFF) fails on stock and succeeds on P3 (this pins the hazard). Every XML
    element and type name the reader accepts maps to a type other than 0xFF (scan the reader's name-to-type table). A
    project with an unknown or injected child element loads with no type-0xFF object created.
  - Push order: Type, Shape, Sync, Glide, Sub FM, Bit Depth, FM 1>2.
  - Writer/reader round trip of the 3 tokens. Out-of-range load: Bit Depth 7 and -1 give M7 bits 3.
  - Clone and child-dup keep the values. Stock 1.6.8 opens the file.
  - **`test_be440_opens_a_p3_project` (new):** load a P3 save with Rig(img = ~/bento/out/m1-be440/bento1.bin). The 4
    BE4.40 keys load with their values, the 3 new tokens are ignored without a fault, and a re-save contains
    `bento_shape` but no `bento_subfm`, `bento_bits` or `bento_fm12`.
  - Preset file round trip: if Q1 finds the preset writer's entry, feed it through the same DOC_READ stub. Otherwise
    M7's preset clause stays device-only (I).
- test_synth_ui:
  - Row 0: slots 5/6 = 0x18B/0x1A4, slot 7 = 0.
  - Row 1 with OSC1 Analog: slots 5/6/7 filled. With OSC1 Wavetable, slot 7 = 0, and it appears within one tick of a
    Type flip. Wavetable rows unchanged.
  - `wvalue` for Sub FM: 0 = `0.0%`, 500 = `50.0%`, 1000, **5000 and -3** (pins what the widget shows for out-of-range
    file values, OD4).
  - Bit Depth shows its labels. resend sends 6 keys.
  - Black box: an `an_print` string test for the `fm=`, `bits=` and `fmx=` fields.
- V1 and dial: the text for 0, 1, 12, 24, 36, 46, 48 (`Saw`), 49.., 88 and 127; lit masks at the anchors; a render.py
  snapshot.

### 6.2 Bit identity (test_analog.py, new class `P3Identity`)
- **Harness (new infrastructure, scheduled in Q1):** the rigs take one stock image and one build directory today (S:
  test_native_props.py:28-33), so P3Identity needs a second build-dir argument (~/bento/out/m1-be440) and a capture or
  replay of the cases. A control assertion proves the harness goes red on a deliberate one-ulp change.
- Identity set: every Cost, Voice, Sync, Shape-mod and Glide case with Shape >= 45, whole and 1/512-modulated, in Rig()
  and Rig(dev=True). Glide cases only in Rig() once Q0 lands. With all amounts 0 and Bit Depth Full, the new build must
  match byte for byte.
- Expected-change set, each with its own test: Shape < 45 (golden, 6.3); Rig(dev=True) glide after Q0 (6.4 glide-time
  test).

### 6.3 Independent host golden model (`p3_golden.py`, numpy, float64, shares no C code)
- It reimplements the sub waves from stock's formulas (with the DC-free Square, OD10); the through-zero phase with
  mirrored polyBLEP/BLAMP; the slave reset rule for di <= 0 (4.3); the index law I = 8(v/1000)^2, the ramp and the
  clamp; the events; and the clamped two's-complement quantiser (OD11, 4.7), about the part's dc (OD12, Q2b: `crush(x,
  b, dc=...)`, dc a float32 scalar or one per sample, supplied by the caller; dc 0 is Q2's grid bit for bit).
- Compared with the emulator sample by sample, max error <= 1e-5 (float32 vs float64), for: Sub FM with sine, saw,
  square and noise subs; FM 1>2 and both routes together; a sync master and slave with FM (including di < 0 at the
  reset); mixed sync with Sub FM; each Bit Depth; Shape 0, 12, 24, 36 and 47.
- **Square sub DC case (new):** Sub FM, Square sub at PW 30 %, I = 8. The mean carrier frequency equals the unmodulated
  one (DC-free), within one bin.
- Spectral checks:
  - First measure `sinq`'s THD in the golden model. (Q3, M: -81.9 dB in numpy before the kernel; the kernel's Shape 0
    measures -81.86 / -82.11 dB at 11 / 301 cycles. The Bessel tolerance can be set from that in Q6.)
  - FM 1>2 with **OSC1 and OSC2 both at Shape 0 (sine)**, ratio 1, I = 1: Bessel J0..J3 within a tolerance set from the
    measured THD. J3(1) = 0.0196 (-34 dB), so 0.5 dB on J3 needs the sine's harmonics and BLEP residue at about -55 dB
    (I: arithmetic).
  - Through-zero (I = 8): no DC drift over 100 blocks (phase mean).
  - Aliasing at 96 kHz after the FIR model is below the old 48 kHz figures.

### 6.4 Targeted tests
- Sub copy in the real voice path, Rig(dev=True), n = 256:
  - raw sub = A/inc_sub_f + dc (worst 0), for 5 waves x Level 0/1000.
  - Square with a PW change mid-block (the case that failed per chunk).
  - **The whole sub object is byte-identical at the sub render's entry with and without sub_copy** (covers the
    PARAM_VALUE cache restore).
- Mixed voice (OSC1 Wavetable): OSC2 fills A itself.
- Tag logic: if OSC1 skipped FM (n > 256), OSC2 must not use a stale B; `fm_missed` counts it. fmtag is 0 after each
  `an_enter` return path.
- E3: OSC1 at Level 0 with FM 1>2 > 0 gives the same OSC2 output as at Level 1000. A silent sync master with Sub FM
  produces event bytes equal to the golden model's. A silent master ramping Sub FM down stays on the FM path until the
  ramp ends.
- Ramp: an amount step 0 -> 1000 -> 0 gives a per-sample index that is linear across the 2N, and the block after is
  bit-identical non-FM. After a silent skip, `msub` and `m12` are 0.
- Shape ramps across the 45/48 boundary and across 22..26: click energy (high-pass RMS at the part edges) <= BE4.40's
  saw->square ramp figure. **Q3 as built:** the metric is the first difference of (ramp - per-sample ramp) at the two
  samples around each part edge, summed per block, RMS over the blocks (the same metric for both ramps, so 16 edges a
  block count as 16); it holds only with `AN_RAMP_LOW` = 16-frame steps for a ramp with either end below the saw (OD13).
- Glide in Rig(dev=True): Glide 500 (1 s) reaches 0.001 of the interval at 1 s +- 1 block. This fails at HEAD (OD1).

### 6.5 VDIV and cost counts
- Hook VDIV.F32 in Unicorn and **count by PC range**: inside `an_render_fm`, inside `sub_copy`, and everywhere else.
  The rest includes existing non-kernel divisions in rate_of (/23.4375, /F(0x108)), pitch_oct (/1000, /12) and
  glide_coef (M: `a/23.4375f/b` compiles to two VDIVs with the project flags).
  - `an_render_fm`: count == the golden model's edge count (wraps, pulse edges, triangle corners), +- 0. Bound <= 2n per
    render, with a worst-case cost test: deep through-zero, 8 % pulse, high OSC1 ratio.
  - `sub_copy`: count == the golden model's sub edge count (saw wrap, both square ramps).
  - FM off: today's count, which is 1 per `an_render` call (so a Shape ramp gives one per part, e.g. 8), not 1 per
    oscillator. (Q3b: "inside an_render" includes the plateaus' out-of-line run_flat and low_step.)
- Cost table at n = 256 (and 128): off cases within +2 % of BE4.40; the full-FM saw voice within the gate (section 5);
  the mixed + full-FM voice and the sub copy per wave recorded.

### 6.6 Mutations (each must turn a test red)
y1 taken after Level (BD1); B written before the quantiser; sub copy per 128-frame chunk; Noise advancing by inc;
Square DC not subtracted; PARAM_VALUE cache not restored; E3 (a) removed; E3 (b) still using an_track; E3 (b) gated on
I_s1 only (no msub); msub not reset on the skip path; fmtag not cleared in an_enter, or cleared after the `!a1 && !a2`
return; a linear index instead of the square law; the clamp missing; the per-block rinc reused under FM; backward wraps
not mirrored; the slave reset under di < 0 done as p = 0 (double edge); FM 1>2 read from props(o1); resend KEYS back to
3; slot 7 shown while OSC1 is Wavetable; an_shape's plateau centred on 48 (shifts 49..51); an out-of-range Bit Depth
played as Full; the pulse's DC term dropped in the FM loop; `rintf` instead of `__builtin_rintf` (link or DSP_REFUSED
fails); Q2 (OD11, Q0 review): the Bit Depth clamp missing (OD11 (a) mid-tread), and its top clamped at S instead of
S - 1; at least one 6.3 crush case reaches the top clip and one -1, so both turn red. (Q2b supersedes "drives s past +1
(BLEP overshoot)": the BLEP never overshoots and s + dc stays in [-1, 1]; the +1 high reaches the top, and the top-at-S
mutant stays red because (x + dc) S = S >= S - 0.5.)
(Q2 as built: real blocks pass the top at every b, the DC-free pulse above Shape 88, but the BE4.40 kernel's minimum is
exactly -1, so the bottom clamp is covered on an_crush's crafted probe only; Q3 and Q5 re-measure the lowest sample.)
Q2b (OD12): the crush's dc zero (OD12 (a)), its sign flipped, one dc per block or the first part's, dc not added, dc not
subtracted, the zero-dc sign-of-zero identity dropped, an_dc's slope or its 0.5 knee wrong, bits= counted per part.

### 6.7 Existing tests that encode BE4.40 behaviour (edit as reviewed changes, each with its new value)
| Test | Today | After | Sprint |
|---|---|---|---|
| test_analog.py:1239-1240 (`run_(20,-1.0)` vs `run_(0)`, "clamped at 0") | both saw | both sine (Shape 0); fix the "saw" label | Q3 |
| test_analog.py:935-936 `test_settings_are_clamped_where_they_are_stored` | K_SYNC 7 -> 0, -1 -> 0 | 7 -> 1, -1 -> 1 if OD4 takes the Sync change | Q1 |
| test_synth_ui.py:494 out-of-range page | negative Shape -> `Saw` | -> `Sine` | Q3 |
| test_synth_ui.py:781, :940-953 V1 tables, `test_out_of_range_shapes_clamp` | Shape <= 48 -> `Saw` | five-shape texts | Q3 |
| test_keys.py:233 | `cover == [SHAPE, SYNC]` | `[SHAPE, SYNC, BITS]` | Q1 |
| test_native_props.py:237 | 0 pads | 1 pad | Q1 |
| test_analog `BitDepth.test_real_blocks_reach_the_top_clip` (Q2) | the DC-free pulse drives s past +1 at every b (clipped hard) | retired: `test_real_blocks_sit_on_the_grids_range`, s + dc in [-1, 1], the top reached by the +1 high | Q2b |
| test_analog `BitDepth` amounts / per-oscillator cases (Q2) | golden crush with no dc (sync case's Shape 100 clipped) | golden crush with the captured per-part dc; the top-clip assertion's reason reworded (the +1 high) | Q2b |
| test_analog `P3Identity.check` / `test_q1_plays_no_amount` (Sub FM 700) | no fill; the whole count under M1's +2 % | fills 8 / 11 / 24 asserted; **a bound carve-out**: sub_fill's Span is left out of the +2 % (M1 is for FM off) and bounded on its own, `FILL_MAX` a call (Rig's zero sub 640 / 1,175 at n = 128 / 256, stock's root's default sub 2,005 / 3,890: measured +5 %) | Q4 |
| test_analog `P3Identity.AMOUNTS`, `test_q1_plays_no_amount`, `test_glide_dev_is_the_expected_change` (Sub FM 700) | Sub FM 700 stored, plays nothing; fills A | AMOUNTS (0, 0, 333), no fill, the whole count under +2 %; the Sub FM cases are `SubFm.test_the_amounts_cases_are_the_expected_change` (every FM call the golden's, fm=) | Q5 |
| test_analog BitDepth `expected_change`, `test_each_oscillator_crushes_at_its_own_depth` | SA_FMSTATS (0, bits, 0, ..) | (fm, bits, 0, ..): Sub FM 700 plays, fm= its renders (both runs alike) | Q5 |
| test_analog `Props.test_the_silent_paths_zero_the_fm_ramp_memory` | the oscillator that played keeps msub 123 | it ramps to Sub FM 0: msub 0 (m12 kept until Q6) | Q5 |
| test_analog `P3Identity.test_q1_plays_no_amount`, `test_glide_dev_is_the_expected_change` (FM 1>2 333) | stored, plays nothing | free, sync, silent master, glide, root voice -> `Fm12.test_the_amounts_cases_are_the_expected_change` (FM 1>2 plays, every call golden, B OSC1's); OSC2 Wavetable and mixed stay (FM 1>2 cannot play there); the glide case's amounts control removed | Q6 |
| test_analog `SubFm.test_the_amounts_cases_are_the_expected_change` (700, 0, 333) | FM 1>2 333 plays nothing | it plays where both are Analog: the replay folds B in, check_feeds checks B | Q6 |
| test_analog BitDepth `cases()`, `test_each_oscillator_crushes_at_its_own_depth` | (700, bits, 333) | (700, bits, **0**): with FM 1>2 playing the crush changes OSC2 through B (BD1); Bit Depth with FM 1>2 is Fm12's | Q6 |
| test_analog `Props.test_the_silent_paths_zero_the_fm_ramp_memory` | the oscillator that played keeps m12 (0, 456) | (0, 0): m12 ramps down as msub | Q6 |
| test_analog `Props.test_the_silent_paths_zero_the_fm_ramp_memory`, its E3 case (Q6 review) | m12 456 written on OSC2 too: since Q6 OSC1 feeds B for it (E3 (a)), so the case no longer took a silent-master path | OSC2's m12 written 0; two E3 cases, each path pinned by its calls: OSC1 (0, 456) -> an_track (one call a sync chunk, no FM call), OSC1 (123, 456) -> E3 (b)'s MASTER FM render (one call a chunk, no an_track); no feed in either | Q6 |
| test_analog SubCopy `test_the_copy_changes_nothing_else`, `test_other_waves_copy_silence` | output and whole voice byte for byte with Sub FM 0; gate + call < 40 instructions | the sub object's own bytes at its render; the zero copy's output within 1e-5 (the FM loop at rest); the instruction check to SubFm.test_cost | Q5 |

---

## 7. Sprints (each: an offline exit, the full suite green, a commit after each; push only on Jeff's word)

| # | Sprint | Work | Offline exit |
|---|---|---|---|
| Q0 | Glide fix (if OD1 = in P3) | `glide_coef`: `s * 48000 * F(osc,0x108)` (2 on the device). The coef cache key is unchanged (n is already in it; that covers the divider only because a root's vt[19] sets both its 2N and its divider, I: see Q0 as built). Add the Rig(dev=True) glide-time test. | The 6.4 glide test is red at HEAD and green after; Rig() glide outputs bit-identical |
| Q1 | Keys, storage, harness; no sound | template keys + pad; `NP_KEYS` 7 keys; `want_row` slots 5..7 + the hidden rule; `resend` with 6 keys; `an_prop` stores (+ OD4 Sync line); AnOsc +4 B; SA_FMBUF, SA_FMSTATS and the tag in synth_shared.h; fmtag clear in an_enter; **P3Identity harness** (second build dir, capture/replay, one-ulp control); type-0xFF tests; BE4.40-opens-P3 test; preset round trip if the entry is found; 6.7 Q1 edits | 6.1 green; 6.2 identity green; manifest template_n 0x304 |
| Q2 | Bit Depth | post-pass quantiser (`__builtin_rintf`, DSP_REFUSED `rintf`), `bits=` stat | golden 6.3 crush cases; identity at Full; BD placement mutations |
| Q2b | Bit Depth about the pulse's dc (OD12 = (b)) | an_crush on s + dc, dc off after, dc per kernel part (`an_dc`); p3_golden's crush with the dc argument; Q2's clip tests retired | 12 / 8 bits near-transparent on Shapes 88..127; crushed pulse mean ~0 (bound derived); <= 2^b levels per constant-dc part; identity at Full |
| Q3 | Five shapes | an_shape map, set_shape for k < 0, LOW regime x 3, `wave_any`, sinq THD measured, V1 text, five-glyph dial, 6.7 Q3 edits | identity 45..127; golden sine/tri/blends; click test; UI snapshots; DSP size logged against the 14 KB ceiling |
| Q3b | Plateau CPU, dial glyphs (OD14) | the sine and triangle plateaus' own loops (run_flat, LOW's sample at each corner; FLAT_INC); the sine and triangle glyphs redone (not taken as built) | pure sine / triangle at or below the saw voice (n = 256, note 60), LOW's output within the golden tolerances (max difference stated), Shape >= 45 bit-identical, DSP within 14 KB; glyphs that fail on the Q3 pair; snapshots at 1x and 4x |
| Q4 | Sub copy and buffers | `sub_inc`/`sub_copy` port with the DC-free Square (OD10) and the PARAM_VALUE cache restore; buffer A; n > 256 guard (nothing audible uses it yet) | 6.4 sub copy worst 0 (up to dc) in the voice path, 5 waves x 2 levels; sub object byte-identical; copy cost recorded |
| Q5 | Sub FM (OSC1, OSC2) | `an_render_fm` x 3 roles incl. the di <= 0 slave rule; msub ramp and skip reset; E3 (b)/(c); mixed-voice path; `fm=` stat; measure the specialised delta at 2N and fix the gate | golden Sub FM cases incl. sync master/slave and mixed sync; VDIV counts by PC range; cost gate; DSP size; mutations |
| Q6 | FM 1>2 | B write (BD1), E3 (a), useB + fmtag, m12 ramp, `fmx=` | golden FM 1>2, combined and Level 0 cases; Bessel check (sine/sine); 6.3's through-zero DC drift (I = 8, 100 blocks) and aliasing after the FIR model, for Sub FM and FM 1>2 (carried from Q5); full-FM cost within the gate; FM off within M1's +2 % (0.43 pt left after Q5). **Done:** "Q6 as built" |
| Q7 | Kit BE4.41 | build; size and cost tables into this doc; device checklist; black box lines; release notes (glide time change, Shape < 45 change, rollback drops the 3 values) | full suite green; manifest sizes within section 5; checklist written. **Done:** "Q7 as built" |

The order puts the zero-risk plumbing (Q1) and the separable features (Q2, Q3) before the hard DSP (Q5, Q6), so each
commit can ship on its own. Close call: Q3 could come after FM. It stays before, because the FM loop has to generate
the sine/triangle terms anyway, and testing it against a finished LOW regime is easier.

### Q0 as built (2026-11-15, working tree on 03dc6d3; build ~/bento/out/m1-p3q0)

OD1 = (a). What was built:
- **M7** (dsp/analog.cpp): `glide_coef(e, glide, n, div)` = `S_EXP2(-9.965784 * n / (s * 48000 * div))`, div =
  `F(osc, OSC_RATE_DIV)` (osc+0x108) of WT1, the oscillator that decays the voice's glide. In Rig() div is 1.0 and
  `* 1.f` is exact (no -ffast-math in the DSP flags, S), so Rig() is bit-identical. The cache key stays (glide, n): the
  root's vt[19] factor sets both the 2N and the divider, so a root's n implies its div (I; plan row Q0's "n is already
  in it" is true only through that).
- **test_analog**: `Oscillator.test_glide_takes_the_set_time_on_the_device` (6.4): Rig(dev=True), divider 2 asserted,
  Glide 500 from note 48 to 60; |glide| <= 0.001 of the interval first at **block 375** = 1.000 s (bound 1 s +- 1 block),
  and one block's ln(coef) within 5e-5 of exact. **HEAD: block 188 = 0.501 s, red** (M, ~/bento/out/m1-p3q0-head).
  The loop reuses one pair of buffers: Rig.render allocates two per call, and the harness heap runs out before ~375
  dev blocks (M: UC_ERR_WRITE_UNMAPPED).
- **P3Identity**: `test_glide` runs Rig() only; `test_q1_plays_no_amount` skips its dev glide case (both cases dropped,
  not loosened). New `test_glide_dev_is_the_expected_change` replays the dropped case on both builds: blocks 0..2
  byte-identical, the first difference in block 3 (WT1 decays after its own render, so WT2 hears it first), WT1's glide
  after the last slide (+1 octave, 4 decays) = c^4 on this build and c^8 on BE4.40, c = 0.001^(2N / (0.6 s * 96 kHz)),
  ln to 4 x 5e-5; stats words equal; instructions +2 %. HEAD: red (no difference). It also carries the dropped dev
  case's Q1 amounts control, on this build alone: the case with Sub FM 700, Bit Depth 2, FM 1>2 333 plays the case
  without them byte for byte (output, voice with the settings bytes masked, stats; the stored values checked).
- **Review (Q0)**: `Oscillator.test_glide_coef_follows_the_block_length`: one root, Glide 500, the block length 128,
  128, 64, 64, 32, 128 mid-slide; each block's ln(decay) = n ln(0.001) / 48000 to 5e-5. It guards the (glide, n) key,
  which no test saw before (a key without n was green on the first Q0 tree: the reviewer's mutation G). The divider is not tested
  apart from n: a root's divider changing under the same n is not a device state (vt[19], I), so a test of it would pin
  a design the key does not claim.
- The tolerance 5e-5 on ln(coef) is stock's exp2 table near 2^0, measured first: 1.1e-5..2.6e-5 at Glide 50, 300, 500,
  1000 (M; relative 0.01 %..0.14 %). The existing Rig() glide test's tolerances are unchanged.

Measured (M):
- Sizes: image **0x188F78** (Q1's, unchanged: M4 untouched), 17,340 B under PROVEN_MAX 0x18D334. DSP **9,836 B** (+8 on
  Q1, +128 on BE4.40's 9,708 of P3's 14 KB ceiling), 26,772 B free in the window.
- Cost: `Cost` tables identical to Q1 (n = 256: saw 13,195, mixed 19,843, saw+sync 15,788, mixed+sync 22,183; they play
  no glide). The coef is computed only on a cache miss (one VMUL and one load more). P3Identity: **53 cases** (Q1's 54,
  minus the two dev glide cases, plus the expected-change case), worst +0.07 % (silent sync master, as Q1), total
  +0.02 %; the changed dev glide case 181,860 vs BE4.40's 181,743 (+0.06 %).
- Mutations (scratch p3/q0/mut.sh, built from a copy, test_analog): div replaced by 2.f (red, 3: the Rig() glide
  tests `test_glide_starts_...`, `P3Identity.test_glide`, `test_q1_plays_no_amount`); `* div` deleted, the HEAD form
  (red, 2: the two new tests). Review round (scratch p3/q0/mutr.sh): the cache key without n (red, 1:
  `test_glide_coef_follows_the_block_length`, 3 subtests); a dev-only glide change gated on Bit Depth 2 stored (red, 1:
  the expected-change test's amounts control). Reviewer's own set (p3/Q0/mutr2.log): a dev-only rate error of 0.3 % is
  red in the expected-change test only, 0.15 % is green in every test: the dev glide tests resolve about 0.3 %, over a
  floor of stock's exp2 table (0.14 % at Glide 500).
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q0) green; test_analog 74 tests. Since Q1 the script honours O= and
  runs test_native_props (Q1's finding is fixed); test_drums / test_drum_ui are not run (drums omitted on 1.6.8, as before).

**Release note (BE4.41):** Glide now takes the time it is set to (Glide 50 % = 1 s to the new note). BE4.40 and earlier
glided twice as fast on the device; projects with Glide set will slide more slowly (no migration).

### Q1 as built (2026-11-15, working tree on c8d5bdb; build ~/bento/out/m1-p3q1)

Decisions applied: OD2 one 7-key group, OD3 hide, OD4 lists match the widget + accept the type-10 display, OD7 test
added, OD9 SA_LAYOUT stays 2. Nothing in the DSP reads the new values: Q1 plays exactly as BE4.40, with the new amounts
at 0 and with them set (Sub FM 700, Bit Depth 2, FM 1>2 333: M, P3Identity `test_q1_plays_no_amount`).

What was built:
- **Base table** (facts.py REVIEWED + expected_values.py, stock_168.py regenerated; the rerun on HEAD reproduced the
  committed table byte for byte first, M): `KEY_OSC_SUBFM` 0x18B, `KEY_OSC_BITS` 0x1A4, `KEY_OSC_FM12` 0x132, and three
  test-only entries: `ENGINE_CTOR` 0x0805B300 (the M7 engine ctor, the only writer of engine+0x8CB2 by immediate, S),
  `CELL_TYPE_OF` 0x08131168 (the reader's name -> type), `PATCH_READ` 0x0813163C (the preset import's reader).
- **M4** (m4_synth.c): `NP_KEYS[7]` (Sub FM type 10 0..1000, Bit Depth type 7 `Full/12/8/4`, FM 1>2 type 10 0..1000);
  `want_row` slots 5/6 on both Analog rows, slot 7 on row 1 only while OSC1 is Analog (uses `osc1`); `resend` 6 keys;
  `an_print` prints `fm= bits= fmx=<skipped>/<missed>` from SA_FMSTATS (the M4 half of S3, done now: cheap).
- **Template** (build.py): the 3 keys after Glide, default 0; `np_template_n` = **0x304, 1 pad** (M, manifest).
- **M7** (dsp/analog.cpp, analog.h): `an_prop` takes the 3 keys, clamps `subfm`/`fm12` to 0..1000 and
  `bits = (u32)v <= 3 ? v : 3`, Sync `v != 0` (OD4); `AnOsc` + `msub`, `m12` (176 / 180 B), cleared by `reset()`, by
  `forget()` and on both silent paths (skip and E3 master); `Run.fmtag` / `Run.inc_sub` replace r9 / r10, fmtag cleared
  right after `r->voice` (before the `!a1 && !a2` return). Nothing sets fmtag or writes SA_FMSTATS yet.
- **synth_shared.h**: `SA_FMSTATS` (+0x100, 32 B `SaFmStats`; inside the range synth_take already zeroes, +0x40..+0x200,
  S), `SA_FMBUF` (+0x800, replaces the unused SA_UI) with `SA_FMBUF_A/B`, `AN_FM_FRAMES` 256, `AN_FM_MAX`, `AN_BITS_MAX`;
  layout static_asserts in analog.cpp (the C++ side; the header is also C).

Measured (M unless marked):
- Sizes: image **0x188F78** (BE4.40 0x188DF8, +384 B), **17,340 B** under PROVEN_MAX 0x18D334. DSP **9,828 B** (+120),
  26,780 B free in the window. Synth UI 14,606 B (+382). AnOsc 176 / 180 B.
- Cost: `Cost` at n = 256: saw 13,195 (BE4.40 13,192), mixed 19,843, saw+sync 15,788, mixed+sync 22,183. P3Identity:
  54 cases, worst +0.07 % (silent sync master, 61,485 vs 61,445), total +0.02 % (M1 budget +2 %).
- **P3Identity** (test_analog, 9 tests, 54 cases): each case replayed on this build and on ~/bento/out/m1-be440 (its own
  image and manifest DSP symbols; BE_REF overrides the path), the same pushes on both. Byte-identical: every output
  sample, the whole voice afterwards (0xB88 B: both oscillators with kernel areas, the sub), the non-cycle stats words,
  mixed sync's master wraps. Cases, each in Rig() and Rig(dev=True): free-running Shape pairs over 45..127; Cost's square
  with Shape modulation; both-Analog sync (incl. a silent master, a slave gap, note 120, n = 512); mixed sync with vibrato
  and a new note; Shape modulation (1/512-held, free and synced); glide (Q0 must drop the dev glide case); OSC1 Analog
  next to a Wavetable OSC2; Voice's path (stock's root ctor, the settings through WT_ROOT_PROPERTY, voice 0 rendered
  through the root's vtable word 0x48 into tramp_voice, mix and output compared). The new keys are pushed at 0 in all of
  these; **`test_q1_plays_no_amount`** repeats 7 of them (free, sync, silent master, glide, OSC2 Wavetable, root voice,
  mixed; both rigs) with Sub FM 700, Bit Depth 2, FM 1>2 333 pushed to both builds, the voice compared with only those
  settings bytes masked, and a control that this build stored them. Q2 / Q5 / Q6 turn those into their expected-change
  cases. Control: a one-ulp change of one sample fails the comparison, a one-ulp change of WT1's level source changes
  the output, and the replay is deterministic.
  A representative set, not every Cost / Voice / Sync case of 6.2: the Sync class's mixed-sync state tests (retrigger,
  WTOscReset off, Sync toggled mid-note, two voices per track, no-WAV master) are not replayed against BE4.40; they keep
  their own assertions on this build. Cost's cases run through `Rig.table` there; here through the push (same keys).
- **OD7** (test_analog Props): the full ENGINE_CTOR does not run offline (it faults at 0x08064FF8 on boot state it
  expects), so its body runs with every call stubbed but memset: the flag table is 0x05 / 0x27 / 0x31 only (M under that
  stub; confirms the static reading). The built image's H_INPUT_SITE with each of the 7 keys x {0, 1, 1000, 5000, -3,
  0x7FFFFFFF} x index 0/1 on a track handle: one BUS_POST to engine+0x7190 with the message unchanged, never BUS_POST_KEY
  (control: key 0x05 is broadcast to 0xFFFFFFFE). The posted message through WT_ROOT_PROPERTY reaches `an_prop` on all 10
  voices. Not run: stock's mailbox drain between BUS_POST and the root (key-generic, I).
- **OD4 widget texts** (were I, now M, test_synth_ui): type 10 shows value/10 as a percentage: 0 `0.0%`, 1 `0.1%`, 500
  `50.0%`, 1000 `100.0%`, **5000 `500.0%`, -3 `-0.3%`**. Bit Depth 0..3 show their labels, **7 and -1 show `4`** (so the
  M7's 3 matches).
- **Type 0xFF hazard** (test_native_props `Hazard`): CHILD_CREATE(0xFF) fails on stock and succeeds on P3 (one property,
  key 0). The reader's name table (48 x 12 B {u32 type, name, label}, at the literal CELL_TYPE_OF+0x38) holds types
  0..47, none 0xFF; each name through CELL_TYPE_OF gives its type, unknown/empty names give 0. A project with unknown,
  empty, typeless and numeric cell types, an unknown element and an injected track loads with no type-0xFF create
  (STORE_CREATE / CHILD_CREATE logged) and the real cells intact.
- **BE4.40 opens a P3 project** (test_native_props): its 4 keys load with their values, its arrays have no P3 key, and its
  re-save keeps `bento_shape` and drops `bento_subfm`, `bento_bits`, `bento_fm12` (release note / checklist item 10).
  Stock 1.6.8 and BE4.22 open it too (existing tests, now with 7 tokens).
- Push order Type, Shape, Sync, Glide, Sub FM, Bit Depth, FM 1>2; writer/reader round trip, clone, child-dup,
  STORE_COPY_ALL, STORE_CLONE_H keep all 7 (values chosen off their defaults); out-of-range loads stay raw (Bit Depth 7 /
  -1, Sub FM 5000 / -3, FM 1>2 99999); the M7 clamps them (test_analog).
- test_keys: the 7 keys empty, not claimed, no immediate in either core; Bit Depth is the third key in the one native-store
  range (0x18C..0x1B6, reverb handle only); **no key-range test covers 0x18B or 0x132** (new assertion); taken Sub FM gives
  `0x110` and writes none of the 7; test_synth_ui: the page then shows stock's rows (`keys=TAKEN:272`).
- Mutations (6.6's Q1 ones plus three of Q1's own), each built from a scratch copy and run against its suite; all 7 red:
  resend KEYS back to 3 (test_synth_ui, 1 failure); slot 7 shown while OSC1 is Wavetable (4); an out-of-range Bit Depth
  played as Full (test_analog, 3); fmtag not cleared (2); fmtag cleared after the `!a1 && !a2` return (1); the skip
  path's clear changed to `msub = m12 = 1` (P3Identity sync, 2: the voice bytes differ; this was first reported as "msub
  not reset", which it is not: see below); Sync back to `v == 1` (3).
- Q1 review mutations (same method; scratch mut2.sh): an_osc's Shape plus `subfm * 0.01`, times 0.5 when `bits`, times
  0.5 when `fm12` (each red: 14 failures, all `test_q1_plays_no_amount`; with the amounts at 0 they were green);
  **the skip path's msub / m12 clear deleted** (red, 2: `Props.test_the_silent_paths_zero_the_fm_ramp_memory`, which
  writes them by hand before each block: skip and n > 1024); the E3 silent master's clear deleted (red, 1, same test);
  `reset()`'s clear deleted: **green, an equivalent mutation**: reset() runs only right after forget() has zeroed the
  whole area, so its clear is unobservable (kept for when a caller resets without forgetting).

Found during Q1 (corrections to this plan):
- **Preset file: only the reader half is offline.** PATCH_READ (0x0813163C, called after "Importing patch %s") reads
  `<document><session><cell ...>` (the first cell's type picks the track: 6 -> wttrack 0x12, 7 -> grtrack 0x13) through the
  same CELL_TYPE_OF + CHILD_CREATE + INST_LOAD path, and keeps all 7 values (M, `test_the_preset_reader_keeps_them`). The
  writer is not found: the preset code's `patch.xml` write (0x081299AC) is PROJECT_WRITE, which writes `<track>` elements,
  and PATCH_READ returns 0 on that form (M). So the preset file's writer, and the format it really writes, stay
  device-only (I; checklist item 10 stays).
- 3.3 / OD7 say "root+0x8CB2"; it is **engine+0x8CB2** (the M7 engine object, r0 of H_INPUT_SITE; S).
- 6.1's `cover == [SHAPE, SYNC, BITS]`: the test sorts by key, so it is `[BITS 0x1A4, SHAPE 0x1A7, SYNC 0x1AB]`.
- **wsl/test_m1.sh does not run test_native_props.py** (not in its list or NEEDS map), the suite that carries most of 3.1 /
  3.2. Q1 ran it explicitly (all suites were run by name, not through the script, which also picks the newest out dir).
- The kit still labels itself BE4.40 (build.py version); the BE4.41 label is Q7's.

### Q2 as built (2026-11-15, working tree on b90d90e; build ~/bento/out/m1-p3q2)

OD11 = (b) as decided; OD4 (Bit Depth past 3 plays 4 bits) was already stored by Q1 and now plays.

What was built:
- **M7 kernel** (dsp/analog_kernel.cpp, -O2): `an_crush(s, n, bits)`, extern "C" so the tests call it directly. It runs
  `q = fminf(fmaxf(rintf(x S), -S), S - 1) * (1/S)` with the `__builtin_` forms, S = 2048 / 128 / 8 for bits 1 / 2 / 3
  (and anything above 3), and leaves the block alone for bits 0. Disassembly (M): VMUL, VRINTX, VMAXNM, VMINNM, VMUL per
  sample, unrolled x4 by clang; no call, no VDIV.
- **M7 hook** (dsp/analog.cpp an_osc): `if (bits) { an_crush(s, n, bits); FMSTATS->bits++; }` right after the part loop
  and before `if (!silent) SMOOTH_APPLY + MIX`. That is the point where Q6 writes B (BD1), and it is also reached by a
  silent OSC1 once E3 (a) lets it through. Today a silent oscillator returns earlier (skip or E3 master), so it is not
  crushed or counted. `finish()` cleans the SA_FMSTATS line next to SA_STATS.
- **build.py**: `DSP_REFUSED_LIBM`, the libm name family (float, double, long double forms; review: compiler_rt links
  most of them silently), checked in check_dsp_elf before the undefined-symbol check.
- **p3_golden.py** is committed with Q2 (it was untracked through Q1; test_analog imports it).
- synth_shared.h / analog.h: comments only (SaFmStats.bits counts since Q2; AnProps.bits plays).

Tests (test_analog; golden = p3_golden.py, written independently from the plan text, used unchanged):
- **`BitDepth.test_an_crush_is_the_golden_grid`**: an_crush on the M7, settings 1/2/3, each run on the golden probe
  (every tie, -0.0, a value that rounds to -0.0, x S from -S - 3 to S + 3), on a sweep over -1.3..1.3 (20,001 points) and
  on 16,384 random samples. The result must equal `crush_bits` (the uint32 patterns) bit for bit. Every golden mutant
  differs on the probe, both clips are reached, and the sweep has exactly 2^b levels. Setting 0 leaves the block alone.
- **`BitDepth.test_the_amounts_cases_crush_and_nothing_else`** (the expected change from the Q1 open note): the same 7
  cases as `test_q1_plays_no_amount` (free, sync, silent sync master, glide, OSC2 Wavetable, root voice, mixed), run in
  Rig() and Rig(dev=True) with Sub FM 700, Bit Depth 2 and FM 1>2 333, plus the free case at Bit Depth 1, 3, 7 and -1
  (22 cases). Each case runs 3 times on this build: crushed, Full, and Full with the golden crush injected at Level's
  input. `level_taps` hooks stock's SMOOTH_APPLY only when it is called from the DSP window.
  - Every Level-input block = `crush_bits(Full block)`, tolerance 0.
  - Output, voice (P3 settings bytes masked) and stats of the crushed run = the injected run, byte for byte.
  - The output differs from Full.
  - `bits=` = the number of crushed renders; a Full run writes nothing to SA_FMSTATS.
  The reference is this build at Full rather than BE4.40, so the dev glide case is in too, and identity at Full stays
  P3Identity's job.
- **`BitDepth.test_real_blocks_reach_the_top_clip`**: real kernel blocks (Shapes 127/100/48/88/70, free and synced, notes
  60..115) go past the top at every b. The lowest sample is -1.000000 (M), so the bottom tie is never passed.
- **Reviewed edits (expected changes, no tolerance loosened):**
  - `P3Identity.AMOUNTS` (700, 2, 333) -> **(700, 0, 333)**: Bit Depth left the "plays nothing" cases.
  - `test_q1_plays_no_amount` and `test_glide_dev_is_the_expected_change`: the stored-value control now expects
    (bits, subfm, fm12) = **(0, 700, 333)**; Bit Depth 2's stored control moved into the new test.
  - `test_the_silent_paths_zero_the_fm_ramp_memory`: the "oscillator that played keeps msub/m12 (123, 456)" control is
    **unchanged**, because Q2 writes neither field. Only its comment is corrected: it said "Q2 ramps them", but the ramps
    are Q5's and Q6's (4.4, row Q5).

Measured (M):
- Sizes: image **0x188F78** (unchanged: M4 untouched; the DSP window lies inside the image's zero gap), 17,340 B under
  PROVEN_MAX 0x18D334. DSP **10,172 B**: +336 on Q0's 9,836 and +464 on BE4.40's 9,708, so P3 has used 464 B of its 14 KB
  ceiling. an_crush is 352 B, literal pool included (0x080D2650..0x080D27B0). The ternary clamp, VCMP + VMRS + VSEL
  twice, was 464 B and cost 4 instructions more per sample (6 against VMAXNM + VMINNM's 2). Leaving out the x4 unroll would save about 230 B at about +2
  instructions per sample (I). Kept unrolled: close call, CPU over size.
- Cost, off: `Cost` at n = 256 is saw 13,214 (Q0 13,195, +19), mixed 19,862, saw+sync 15,803, mixed+sync 22,198. The +19
  is the bits load and test per oscillator plus the SA_FMSTATS clean per block. P3Identity: 53 cases, worst **+0.26 %**
  (free (127, 45) Rig(): 65,852 vs 65,680), total +0.12 % (M1 +2 %).
- Cost, on: at most **8.56 instructions per crushed sample** including the call (free, Bit Depth 2, Rig(): 85,593 vs
  68,057 over 2,048 samples), so about 2.2 k per oscillator per 2N block on the device (I: 256 x 8.56).
- Mutations (scratch p3/q2/mut.sh, each built from a copy and run through the whole of test_analog; logs in p3/q2/mut/):
  every one is red or refused at build time.
  - Crush after SMOOTH_APPLY (BD1 placement): 23 failures.
  - No clamp (OD11 (a)): 24.
  - Top clamped at S: 24.
  - Bottom clamp alone missing: 9, **all in the an_crush probe test**; no voice-path case can see it.
  - Ties away from zero: 4, probe only.
  - Truncation: 32.
  - Symmetric clip: 21.
  - 12- and 4-bit S swapped: 14.
  - Only the first half of the block crushed: 23.
  - Slave not crushed: 6.
  - Crush skipped for Bit Depth 2 at n = 128: 7.
  - Out-of-range Bit Depth played as Full: 7.
  - `bits=` not counted: 23.
  - `rintf` for `__builtin_rintf`: the build is refused by ld.lld "undefined symbol: rintf", before check_dsp_elf runs.
  - `fmaxf` for `__builtin_fmaxf`: refused by **DSP_REFUSED_LIBM** "libm routines ['fmaxf'] linked".
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q2) green; test_analog 80 tests (Q0 74 + 3, + 3 from the review).

Found during Q2 (corrections to this plan):
- **The top clip is not only BLEP overshoot (4.7, OD11).** The DC-free pulse peaks at 2 - 2w (1.84 at Shape 127), so with
  any Bit Depth every Shape above 88 is clipped hard. Even 12 bits takes a Shape-127 pulse from 1.84 / -0.16 to
  0.9995 / -0.16 (M: real blocks). Under (a) mid-tread with no clamp, 12 and 8 bits would have been transparent there.
  The clipped pulse is no longer DC-free, so once Q6 writes B from the crushed s (BD1) it also detunes OSC2 under FM 1>2
  (I * inc1 * mean(q), about -0.4 to -0.8 x inc1 at I = 8: 4.7). **Open for Jeff: OD12** (options and recommendation in
  the table; needed before Q6). Q2 ships (a), accept, and its tests pin it.
- **6.6's "one crush case to -1" cannot be met by a real block:** the BE4.40 kernel's minimum is exactly -1 (the
  square's low), so a missing bottom clamp alone is visible only on crafted input (the an_crush probe). Q3's triangle and
  sine, and Q5's FM, may change this. test_real_blocks_reach_the_top_clip prints the lowest sample.
- **4.7's "check_dsp_elf already refuses any undefined symbol, so this only names the cause" was half true** (4.7 now
  corrected). `rintf` fails at the link (lld), so DSP_REFUSED_LIBM never sees it. A plain `fmaxf` **links**: zig's
  compiler_rt defines it, and most of the float libm family (review, M: floorf, sqrtf, roundf, truncf, expf, sinf too),
  so only a refusal by name stops it. Load-bearing; widened to the family and tested.

Review fixes (two reviews, 2026-11-15; rebuilt clean into ~/bento/out/m1-p3q2, sizes unchanged: image 0x188F78, DSP
10,172 B, an_crush unchanged):
- **OD12** (pulse clip and its FM 1>2 detune) added to the open-decisions table and to 4.7; needed before Q6.
- **p3_golden.py committed** with Q2 (one commit, as the sprint rule says; the reviewer's "model first, own commit" is
  not taken: the model's file is unchanged since it was written, which is what that history would show).
- **DSP_REFUSED_LIBM widened** to the libm family; the message names compiler_rt.
- **New tests** (test_analog 77 -> 80):
  - `BitDepth.test_each_oscillator_crushes_at_its_own_depth`: OSC1 / OSC2 at (12, 4), (Full, 8), (8, Full) bits, free
    and synced, both rigs; Full with each oscillator's own golden crush injected (level_taps now passes the oscillator,
    from SMOOTH_APPLY's r0 = osc + 0xF8) plays the crushed build byte for byte; bits= counts only crushed renders.
    voice_case takes a per-oscillator p3 pair.
  - `BitDepth.test_the_fm_stats_line_is_cleaned_with_the_stats`: DCCMVAC writes hooked; the SA_FMSTATS line is cleaned
    once per finished block, next to SA_STATS, each time with that block's bits= (2, 4, .. 14 over 8 blocks).
  - `BitDepth.test_the_dsp_links_no_libm`: the regex refuses 22 libm names; check_dsp_elf passes the shipped ELF and
    refuses it with one .strtab name changed to fmaxf; an_crush's disassembly has VRINTX / VMAXNM / VMINNM, no BL, no VDIV.
  - The crushed runs now assert the whole SA_FMSTATS line, (0, renders, 0, 0, 0, 0, 0, 0), not only bits=.
- **Mutations the review found green, now red** (scratch p3/q2/fix/mut.sh, logs there):
  - Extra `fm++` with the bits count: 34 failures, 1 error.
  - finish()'s SA_FMSTATS clean removed: 1 (the clean test).
  - DSP_REFUSED_LIBM disabled plus plain fmaxf: 1 (the libm test).
  - OSC1's Bit Depth for both: 14, 12 of them in the new per-oscillator test. OSC2's for both: 14 (12).
  - Plain fmaxf and plain floorf (compiler_rt links both): build refused by DSP_REFUSED_LIBM.


### Q3 as built (2026-11-15, working tree on d21225d; build ~/bento/out/m1-p3q3)

OD6 as recommended (plateaus sine 0..2, triangle 22..26, saw 45..48). p3_golden.py's Q3 section (written independently from
4.6 / 6.3 / 6.5 / 6.6 / OD6, before the kernel) is used unchanged; no bug was found in it.

What was built:
- **sinq measured first** (M, numpy, scratch p3/q3/impl/sinq.py, before any kernel code): the prototype's odd 5th-order
  `sin(pi/2 x)` has max error 8.07e-5 (8.08e-5 evaluated in float32), sinq(1) = 1 in float32; as the sine of the triangle
  its THD is **-81.9 dB**, the largest harmonic the 5th at -82.2 dB. SINQ_ERR = 8.1e-5 is the stated design figure the
  tests use (never a kernel residual).
- **M7 kernel** (analog_kernel.cpp): `set_shape` k < 0 -> `low` with tw / ws / wsaw (sum 1; NaN is not < 0, so the saw
  as BE4.40); `wave<LOW>` = tw tri + ws sinq(tri) + wsaw (2p - 1), tri = 1 - 4 |p - 1/2| (`__builtin_fabsf`, VABS) and the
  sine computed from it, so -cos 2 pi p in phase with the triangle, the polarity p3_golden chose; `advance<LOW>`: a
  polyBLAMP `corner()` of -8 inc tw at p = 1/2 and +8 inc tw at the wrap, the saw part's BLEP -2 wsaw; `by_regime` sends
  `low` to `run<LOW, ROLE>` before BE4.40's dispatch (one generic regime x 3 roles). k >= 0 is BE4.40's code path.
- **Beyond 4.6, SLAVE:** a reset in LOW steps the slope as well as the value (the triangle's -4 -> +4 when the reset lands
  past its top, the sine's 2 pi sin 2 pi p -> 0), so it adds a BLAMP `inc (wt (p < 1/2 ? 0 : 8) - ws 2 pi sinq(tri(p +
  1/4)))` at the event's fraction next to BE4.40's value BLEP. One sinq per reset, nothing per sample. The golden model
  does not cover sync (its own notes), so the test model `low_slave` (test_analog) is built from the golden's pieces.
- **an_reshape:** `wave_any` (LOW below 0, `wave<MIXED>` at and above), still a value jump only. The LOW branch is chosen on
  the two floats before any Shape is built and runs out of line (`reshape_low`): deciding after both `set_shape` calls cost
  an_reshape +41 % on P3Identity's mod case and the case +2.2 % (over M1's +2 %, M); this way an_reshape is +9 % and the
  case +0.98 %.
- **M7 hook** (analog.cpp): `an_shape` below 48 per OD6 (linear between the knots, -1 / -0.5 / 0 exactly on the plateaus;
  48..127 BE4.40's arithmetic unchanged). Its "s < 0 and NaN: the sine" is a fall-through only: the stored Shape is
  unsigned and an_osc clamps a modulated one to 0..127 first, so no caller passes either (review: an equivalent mutation).
- **Found, and changed (deviation from 4.6, flag for review): `AN_RAMP_LOW` = 16.** 6.4's click criterion failed with
  AN_RAMP for LOW too: per part edge 1.02 / 1.08 / 0.95 x the saw > square ramp's at LFO centres 24 / 28 / 46 (M, scratch
  p3/q3/impl/clicks.py), because the LOW map moves k twice as fast a Shape step (0.5 over 20 / 19 Shapes; saw > square
  0.5 over 40). A slope BLAMP in the reshape (tried, scratch exp_blamp.py) changed the figure in the third digit, nothing
  else: 4.6's "no BLAMP" stands (I -> M). **For Jeff: OD13.** So a ramp with either end below the saw (k0 < 0 or k1 < 0) steps every 16
  frames (`step = ... ? AN_RAMP_LOW : AN_RAMP`, static_assert: a divisor of AN_EVENTS); 45..127 ramps keep AN_RAMP
  (identity). Result (the test's metric, click energy per block): saw > square 0.0163, LOW 0.0083 / 0.0089 / 0.0135 at 24
  / 28 / 46. Cost: twice the parts in a LOW ramp block (below). One line to revert if Jeff prefers 4.6 as written.
- **synth_shared.h:** AN_SHAPE_SINE 0, _SINE_END 2, _TRI_LO 22, _TRI 24, _TRI_HI 26, _SAW_LO 45 beside _SAW_END 48 (the
  saw's anchor), _SQUARE 88; one comment rule for both cores.
- **M4** (m4_synth.c): V1 `shape_text` Sine, `Sin>Tri n%` (the triangle's share, (s - 2) / 20), Tri, `Tri>Saw n%` (the
  saw's, (s - 26) / 19), Saw, then BE4.40's three (a shared `morph()` keeps their rounding); the J3 dial: five glyphs
  (sine and triangle new, in the kernel's phase), `AT[5] = {0, 24, 48, 88, 127}`, `lit()` the 3/4 rule per segment (sine
  0..7, triangle 17..30, saw 41..58, square 78..97, pulse 118..127), the ring one uniform dotted arc (BE4.40's wide saw
  sector and C_SECTOR gone).

Tests:
- **test_analog `ShapeLow`** (8 tests, new):
  - `test_the_kernel_is_the_golden_model`: an_render at k = kcoord(Shape) for Shapes 0, 1, 12, 23, 24, 36, 44, 47, incs
    0.0027 (note 60), 0.0123, 0.2, 0.45, 1024 samples in two chained calls, against `render(law="f32")`: every sample within
    `tol_time(.., "f32", SINQ_ERR)` (worst 8.08e-5 = 0.89 of its tolerance, at Shape 0: sinq), the phase after each call
    equal to the golden's. Lowest sample -1.0000000 (Q2's note: LOW does not pass the bottom clip either).
  - `test_master_events_are_the_golden_wraps` (n = 128: the harness's event buffer), `test_slave_resets_band_limit_the_
    value_and_the_slope` (against `low_slave`, 1e-5 + 2 ws SINQ_ERR; no events = the free render bit for bit; control:
    `low_slave` without the reset BLAMP is > 1e-4 off), `test_one_division_per_render` (VDIV.F32 by PC inside an_render: 1
    per call, free / master / slave, Shapes 0 / 24 / 36 at inc 0.2).
  - `test_an_shape_through_the_voice` (Rig(dev=True)): the shape an_render gets for every whole Shape 0..48 and for
    1/512-held values next to each plateau edge = f32(kcoord) within 2 ulp, exact on the plateaus; the change's block has
    16 parts where an end is below the saw, else 8.
  - `test_spectra` (coherent 4096, no window, after 4096 warm-up): Shape 0's THD -81.86 dB at 11 cycles and -82.11 at 301
    (sinq's -81.87 within 1 dB; far below 6.3's -55 dB); its off-harmonic energy is sinq's own (-129.7 dB at 11, -94.4 at
    301: the 7th harmonic and up fold past Nyquist, so not the golden's < -120 dB) within 1 dB. Triangle and blends: the
    kernel's off_db = the golden's within 0.1 dB (golden note: I; M here, all within 0.08 dB): Shape 12 -95.07 / -48.74,
    24 -88.17 / -41.66, 36 -42.50 / -26.71, 47 -38.85 / -23.15 dB at 11 / 301 cycles; each at least 5 dB under the naive
    render (10 to 16 dB measured).
  - `test_cost`, `test_the_ramp_across_the_plateaus_clicks_no_more_than_saw_to_square` (6.4; figures above).
- **Reviewed edits (6.7 Q3, expected changes, no tolerance loosened):**
  - `ShapeMod.test_lfo_to_position_moves_the_pulse_width`: `run_(20, -1.0)` still equals `run_(0)`, now **the sine** (label
    "clamped at 0: the sine"); added: it differs from Shape 48 (the saw).
  - test_synth_ui `Defensive.test_out_of_range_values_on_the_page`: a negative Shape reads **`Sine`** (was `Saw`).
  - `ValueTexts.test_shape_texts`: **0 / 1 / 2 Sine, 3 `Sin>Tri 5%`, 12 `Sin>Tri 50%`, 21 `Sin>Tri 95%`, 22 / 24 / 26 Tri,
    27 `Tri>Saw 5%`, 36 `Tri>Saw 53%`, 44 `Tri>Saw 95%`, 45 / 46 / 48 Saw**; 49.. unchanged.
  - `Dial.test_pointer_and_lit_glyphs`: per-glyph lit sets at 24 Shapes (was: any LIT pixel at 11); `test_the_saw_sector_
    lights_as_one` -> **`test_each_plateau_lights_as_one`** (0 = 1 = 2, 22 = 24 = 26, 45 = 46 = 48, the three differ);
    `test_the_glyphs_read_as_the_sound`: the saw drawn at 48 (its glyph moved off the sector), sine and triangle added
    (start at the bottom, two peaks, no edge: span < 16 px, the 18-px edge test the saw and pulse already use).
  - P3Identity `test_free_running_shapes`: + (46, 49), (47, 50), (51, 45), so every whole Shape 45..51 is compared with
    BE4.40 (6.6's "plateau centred on 48").
- The `test_q1_plays_no_amount` cases all play Shape >= 48, so Q3 changes none of them (as the Q2 note expected).

Measured (M):
- Sizes: DSP **12,748 B**: +2,576 on Q2's 10,172, **+3,040 on BE4.40's 9,708: P3 has used 3.0 KB of its 14 KB ceiling**,
  23,860 B free in the window. an_render +1,792 B (3 LOW instances, ~600 B each, against 4.6/5's ~2 KB for "LOW x 3 + shape
  code"), reshape_low 660 B, an_shape 240 B (now out of line; an_osc -140), an_reshape +28. Image **0x1890C8** (+336 on
  Q2), **17,004 B under PROVEN_MAX** 0x18D334; synth UI 14,944 B (+338). Q3 is about 1 KB over 5's estimate for its part;
  with Q4 (~2 KB) and Q5's three FM instances (~1.5-2.5 KB each) the ceiling still holds on 5's figures (I), but Q5's
  measurement now matters.
- Cost, off (Shape >= 45): `Cost` at n = 256 saw 13,242 (Q2 13,214), mixed 19,882, saw+sync 15,843, mixed+sync 22,224; n =
  128 saw 7,354. P3Identity: **59 cases**, worst **+0.98 %** (mod dev=False, 110,971 vs 109,899: a ramp's 8 an_reshape
  calls, each two float compares more), total +0.35 % (M1 +2 %).
- Cost, LOW (`ShapeLow.test_cost`, hooks per voice-block, both oscillators): n = 256 sine 23,472, sine>tri 23,482, tri
  23,464, tri>saw 23,464, tri+sync 25,833, tri ramping 29,554 (saw > square ramping 22,859); n = 128 sine 12,549. So a
  held LOW shape costs **1.18 x the mixed saw > square voice** (19,882) and 1.77 x the saw: the generic loop pays the
  triangle, sinq and the saw term every sample (~20 instructions a sample more than the saw). A LOW ramp block 1.29 x the
  saw > square ramp (AN_RAMP_LOW's 16 parts). Specialising ST / TS (4.6's rejected alternative) would cut that at ~2 KB
  more code; not done (I: not needed by any budget yet).
- Dial (M4): one draw 116,784 instructions, 206 fills (BE4.40 / Q2: 96,446, 156): two more glyphs. Snapshots: scratch
  p3/q3/snap/dial_*.png (0, 2, 12, 24, 36, 48, 68, 88, 108, 127) and montage.png. **Note for the checklist:** at 24
  columns x amplitude 9 the sine and triangle glyphs are hard to tell apart (6 samples a half cycle: sine -9 -7 -5 0 5 7 9,
  triangle -9 -6 -3 0 2 5 9); the text (`Sine` / `Tri`) is exact. Device item 9 should judge it; one cycle a glyph for
  those two would separate them.
- Mutations (scratch p3/q3/mut/mutate.py, each built from a copy and run against its suite; logs in p3/q3/mut/logs): all
  red. Failures, test_cost's (an earlier draft's bound) left out:
  - an_shape's saw plateau centred on 48 (6.6): 6, P3Identity free shapes (49..51).
  - Triangle BLAMP sign flipped: 39 (golden 25, slave 9, spectra 5). BLAMP at the wrap missing: 36.
  - Saw BLEP not scaled by 1 - tw: 14. Triangle plateau dropped: 25 (an_shape through the voice).
  - Sine = the triangle (no sinq): 25. Sine polarity flipped (+cos): 52.
  - Slave reset BLAMP removed: 11 (slave test). reshape_low with wave<MIXED>: 1 (the click test only).
  - AN_RAMP_LOW = AN_RAMP: 1 (the voice's part count; the click test models the rule itself). The LOW decision made after
    `set_shape` (all reshapes through reshape_low): 4, P3Identity mod cost (worst +2.6 %).
  - AN_SHAPE_TRI_HI 27 in synth_shared.h (both cores): test_analog 20, test_synth_ui 4.
  - M4: glyphs sine / triangle swapped in AT: 6; `Sin>Tri` percent from 0 not 2: 2; the sine's 3/4 rule as 1/2: 2.
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q3) green; test_analog 88 tests (Q2 80 + 8), test_synth_ui 62 (one test
  renamed); p3_golden.py self-test 17 tests OK.

Found during Q3 (corrections to this plan):
- **6.4's click criterion fails at AN_RAMP for the LOW map** (above); fixed with AN_RAMP_LOW, a deviation from 4.6 to review.
- **6.3's "Shape 0 off-harmonic below -120 dB" holds only for the exact sine:** with sinq, at 301 cycles its harmonics fold
  to -94 dB. Harmless for the Bessel check (6.3 counts harmonics), but Q6's tolerance must count sinq's aliases at high
  carriers.
- **4.6 / 5 size estimate** for LOW x 3 + shape code (~2 KB) was low by ~1 KB (3.0 KB measured including reshape_low and
  the out-of-line an_shape).
- **Golden harness note confirmed:** the master role stays at n <= 128 in the tests (128-byte event buffer).
- 4.7 already states OD11's two's-complement grid (Q2 rewrote it); no mid-tread text is left in 4.7.

Review fixes (two reviews; each issue checked, build ~/bento/out/m1-p3q3, clean):
- **an_reshape's routing had no direct test (major).** The review's mutant "LOW only when the target is below 0" (a LOW
  Shape read through wave<MIXED>, blend / width / dc unset) stayed green; the old wave<MIXED> mutant and a wrong correction
  on the one step that crosses 0 were seen only by the aggregate click test. New `ShapeLow.test_an_reshape_takes_each_
  side_in_its_own_form`: LOW > LOW, LOW <> MIXED both ways across 0 (k = 0 is MIXED), at three phases, the step in
  `delayed` = 0.5 (wave_to - wave_from) within 1e-6 (the LOW side with the stated sinq), and k >= 0 pairs bit for bit as
  BE4.40's an_reshape (BE_REF).
- **sinq was pinned only to its error budget.** c1 off by 1e-5 (SINQ_ERR is 8.1e-5) stayed green. New `ShapeLow.test_the_
  sine_is_the_stated_sinq`: Shape 0 sample by sample = the stated polynomial at the kernel's float32 phases within 1e-6,
  Shape 12 the golden with its sine replaced by sinq, likewise.
- **The sine and triangle glyphs drew alike** (snapshot, and the mutant "sine drawn as the triangle" stayed green). Now one
  cycle each (12 columns a half cycle), the sine rounded to nearest: its top holds 5 columns, the triangle's point 3
  (the 2-px fill); their top profiles differ by 2 px in 18 of 26 columns (M). `Dial.test_the_glyphs_read_as_the_sound`:
  one peak in the middle (was two: the reviewed expected change), plus the profile and peak-width checks. OD14. Snapshots:
  scratch p3/q3fix/snap. Dial draw 115,481 instructions, 206 fills.
- **Deviations not on the decision list:** OD13 (AN_RAMP_LOW) and OD14 (the reset BLAMP, the one-cycle glyphs) added;
  6.4 records the 16-frame rule and the per-block metric.
- **LOW's CPU cost was only in this section:** now in 5 (CPU row), 9 (a risk row) and device item 12 (Shape 0 / 24 chords).
- **an_shape's "s < 0 / NaN: the sine" is unreachable** (equivalent mutation): comment and the note above reworded; no
  code change.
- Not taken: rebuilding the click test on the voice path's captured parts (review 1, optional). The ramp rule it models
  is pinned against the firmware by test_an_shape_through_the_voice's part count, and the new reshape test covers the
  per-step correction directly, so a voice-path capture would add harness, not coverage.
- Mutations rerun against the fixes (scratch p3/q3fix/mut, each built from a copy): reshape LOW only on the target 12
  failures, only on the source 9, reshape_low through wave<MIXED> 34, its correction zeroed 34, sinq c1 -.6417 6, c0
  1.5703 38, glyph sine = triangle 1, sine and triangle back at two cycles 3, sine phase 2, triangle inverted 2: all red.
  an_shape's negative branch to the saw stays green (equivalent, above).
- After the fixes: DSP 12,748 B (unchanged), image **0x1890D8** (+16 B, 16,988 B under PROVEN_MAX), synth UI 14,960 B
  (+16); test_analog 90 tests (+2), test_synth_ui 62.

### Q2b as built (2026-11-15, working tree on b9d8ec4; build ~/bento/out/m1-p3q2b)

OD12 = (b), OD13 = (a), OD14 (reset BLAMP kept, glyphs to Q3b) recorded in Decisions. Q2b is after Q3 in time but
touches only Bit Depth.

What was built:
- **M7 kernel** (analog_kernel.cpp): `an_crush(s, n, bits, dc)` = `clamp(rint((x + dc) S), -S, S - 1) / S - dc`. A zero
  dc adds -0.0 and subtracts +0.0, so dc 0 is Q2's grid bit for bit, sign of zero included (S: x + -0 = x, q - +0 = q).
  Per sample VADD, VMUL, VRINTX, VMAXNM, VMINNM, VFMA (the level x 1/S - dc fused: exact, because the product is exact),
  x4 unrolled. New `an_dc(shape)`, set_shape's `k.dc` in the same arithmetic (S: the same VFMA pair in both), 0 at and
  below 0.5 and for NaN. Taking it from set_shape itself compiled to 184 B (its LOW branch), so it is a separate 80 B
  function, pinned to the kernel's own pulse levels by a test.
- **M7 hook** (analog.cpp an_osc): the crush moved into the part loop: `if (bits) an_crush(s + off, m, bits, an_dc(k))`
  right after each part's `an_render`. A part's samples are final once rendered (an_reshape moves only `delayed`), so this
  is the post-pass with a per-part dc. `bits=` still counts one per crushed render, after the loop. BD1 unchanged: B (Q6)
  is written after the loop.
- **p3_golden.py** (a spec change, so the model changes, written from OD12's text): `crush(x, b, f32, dc)` with dc a
  float32 scalar or one per sample; the add and the subtract round once each to float32 (computed in float64 and rounded
  once: double rounding is harmless at 53 >= 2 x 24 + 2 bits); dc 0 is Q2's crush in every bit. `levels` / `check_levels` /
  `check_clip` / `compare` / `mutants` / `discriminates` / `probe` take dc; the new mutants are dc not added (OD12 (a)),
  not subtracted, sign flipped. Self-test 23 tests (Q3 17 + 6: dc 0 = Q2 bit for bit, 4-bit by hand about -0.75,
  float32-exact against numpy float32 on random x and dc, sweep levels and clips, the naive pulse's mean shift = -f_hi / S
  within 2^-23, the probe about -0.75 kills all 8 mutants).

Tests (test_analog BitDepth, 94 tests: Q3 90 - 1 retired + 5):
- **level_taps** also hooks an_crush's entry: per block the parts' calls (offset, n, setting, dc from s0) and a per-sample
  dc; `dc_feed` replays a crushed run's dc into a Full run's injected golden crush. `dc_of(k)` is the stated law
  (2 (0.5 - 0.84 (k - 0.5)) - 1), the independent check on every captured dc (1e-6).
- `test_an_crush_is_the_golden_grid`: Q2's probe, sweep and random blocks about dc 0, -0.75 (exact ties: every Q2 and
  OD12 mutant separated) and an_dc at Shapes 100 / 127 (OD12's mutants separated); sweep exactly 2^b; Full untouched at
  any dc.
- `test_an_dc_is_the_kernels_dc` (new): an_render at k plays exactly f32(1 - dc) / f32(-1 - dc) on its plateaus, Shapes
  88 + 1/512 .. 127.9; +0.0 at k 0, 0.25, just under the knee (87.6, 88 - 1/512), 0.5, LOW, NaN.
- `test_real_blocks_sit_on_the_grids_range` (**retires** Q2's `test_real_blocks_reach_the_top_clip`, 6.7): s + dc in
  [-1.000000000, 1.000000000] on real blocks (Shapes 88..127 free / synced, 48 / 70 synced, notes 60..115), while s itself
  reaches 1.84 (control). The top is still reached at every b (by the +1 high: 8,718 / 8,742 / 8,822 samples); the bottom
  tie is never passed.
- `test_12_and_8_bits_are_near_transparent_on_the_pulse` (new): |q - s| <= 1/S + 2^-23 at every sample, peak to peak lost
  <= 1/S + 2^-22, Shapes 88, 89, 100, 110, 120, 127 free and 100 / 127 / 88 synced. **M: worst exactly 1.000000 step** (the
  +1 high). Derivation in the docstring (one rounding on the add, rint <= 1/(2S) inside [-1, 1], the clip 1/S at +1, one
  rounding on the subtract). Control: OD12 (a) on the same blocks is off by up to 0.848 (0.8405 at Shape 127 / 12 bits).
- `test_the_crushed_pulse_keeps_its_mean` (new): **bound |mean(q - s) + f_hi / S| <= f_edge (1/S + 2^-23) + f_hi 2^-23**,
  derived per sample (low plateau e = 0 exactly, high plateau e = -1/S within 2^-23, edge samples within the transparency
  bound), both plateau facts asserted directly. M (free, note 60, 8 dev blocks): shift -3.9e-5 / -1.0e-2 at Shape 127,
  12 / 4 bits (OD12 (a): -0.070 / -0.049); -1.9e-4 / -4.9e-2 at Shape 100 ((a) -0.102 / -0.156). From Shape 100 (with a
  high plateau), (a) moves the mean over 100 / 10 / 1 x as far at 12 / 8 / 4 bits.
- `test_a_ramp_crushes_each_part_about_its_own_dc` (new): an LFO moves Shape 100 <-> 125.4 each block, free and synced,
  all three depths: every an_render part is followed by an an_crush on the same range with dc = an_dc(that part's k)
  bit for bit (and dc_of within 1e-6); a ramp block has 8 parts with 8 dcs in ramp order; the Level input = the golden crush
  of the Full block with that per-sample dc, bit for bit; each part passes check_levels (<= 2^b values, on its own grid);
  a held 4-bit block has <= 16 values; a whole ramp block is off the grid of any single one of its dcs. Full: no an_crush
  call at all (identity at Full; P3Identity's 59 cases stay byte-identical with BE4.40).
- **Reviewed edits (expected changes, no tolerance loosened):** the amounts and per-oscillator cases inject the golden crush
  with the captured dc (`check_dcs`: each block covered end to end by its parts, setting, one dc per held Shape = dc_of);
  the sync case's OSC2 at Shape 100 is the one case whose Level input changes. The amounts test's top-clip assertion
  keeps its value (> 0), with its reason reworded (the +1 high, not the 2 - 2w high).

Measured (M):
- Sizes: DSP **12,964 B** (+216 on Q3's 12,748; P3 has used 3,256 B of its 14 KB ceiling), 23,644 B free in the window.
  an_crush 448 B (+96: the add, the subtract, the zero-dc selects), an_dc 80 B, an_osc +36 (0x784 -> 0x7A8; +4 alignment). Image **0x1890D8** (unchanged:
  M4 untouched), 16,988 B under PROVEN_MAX 0x18D334.
- Cost, off: `Cost` n = 256 saw 13,236 (Q3 13,242), mixed 19,876, saw+sync 15,847, mixed+sync 22,228; n = 128 saw 7,348.
  P3Identity 59 cases, worst +0.97 % (mod dev=False, 110,965 vs 109,899), total +0.33 % (M1 +2 %).
- Cost, on: at most **10.77 instructions per crushed sample** including the calls (sync, Bit Depth 2, Rig(): 111,959 vs
  89,911 over 2,048 samples; Q2: 8.56), so about 2.8 k per oscillator per 2N block (I: 256 x 10.77). The +2.2 is the add
  and subtract per sample plus one an_dc call and one an_crush call per part (2 per synced 2N block, 8 in an AN_RAMP ramp).
- Mutations (scratch p3/q2b/mutate.py, each built from a copy, the whole of test_analog; logs p3/q2b/mut/): all red.
  dc 0 in an_osc (OD12 (a)): 50 failing (sub)tests; dc sign flipped: 48; one dc per block (k1): 6, first part's (k0): 6,
  both only in the ramp test; dc not added: 73; not subtracted: 74; the zero-dc sign-of-zero selects dropped: 3 (the
  probe's -0.0, grid test only); an_dc slope 0.8401: 74; an_dc's knee at 0.49: **green at first** (no test had a k in
  (0.49, 0.5)), red (1) after the knee cases were added to test_an_dc_is_the_kernels_dc; bits= counted per part: 6.
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q2b) green; test_analog 94 tests.

Found during Q2b (corrections to this plan):
- **OD12 removes the pulse's DC, not the grid's.** The +1 high still loses one step, on the square too (dc 0), so any
  signal with a high plateau at +1 keeps a mean shift of -f_hi / S: -2.6e-4 at 12 bits, **-0.065 at 4 bits on the square**
  (M). Under FM 1>2 (B = I inc1 q) at I = 8, OSC1 = OSC2, a square OSC1 detunes OSC2 by about -3 / -55 / -1200 cents
  at 12 / 8 / 4 bits (I, 4.7). OD12's "B stays DC-free" holds for the pulse's own DC at every depth, but not for the
  grid's: audible from 8 bits on. **Open: OD15** (needed before Q6).
- 4.7's "post-pass after the part loop" is now per part inside the loop (4.7 updated); the plan's "one add and one subtract
  per sample" holds, the per-part dc costs one an_dc call a part.

Review fixes (two reviews; each issue checked, rebuilt clean into ~/bento/out/m1-p3q2b, sizes unchanged: image 0x1890D8,
DSP 12,964 B; full suite green, test_analog 94):
- **The grid's DC was understated (major).** At I = 8, OSC1 = OSC2, a square OSC1 detunes OSC2 by about -3 / -55 / -1200
  cents at 12 / 8 / 4 bits (I: 1200 log2(1 - I f_hi / S), f_hi ~ 0.5), so 8 bits is half a semitone, not "holds". 4.7 and
  the note above restated per depth; the question is now **OD15** (every depth, before Q6). No code change.
- **Stale text (minor):** 4.1's pseudo-code (per-part crush with an_dc), 4.7's first bullet, the BLEP-overshoot sentences
  (4.7, OD11 in Decisions) and 6.6's "drives s past +1" marked superseded: s + dc stays in [-1, 1], the +1 high reaches
  the top, the top-at-S mutant still red.
- **an_osc +36, not +40 (minor):** M, llvm-objdump: 0x784 -> 0x7A8; corrected above.
- **an_dc's k > 1 clamp was untested (minor):** kept (it is set_shape's, so an_dc stays its k.dc for any caller), and
  test_an_dc_is_the_kernels_dc now checks k = 1 + 2^-23, 1.25, 2, inf: an_dc(1) bit for bit and an_render's own plateaus
  at that k. The 127.9 case (k_of caps it at 127, so a duplicate of 127) is gone. Mutant "clamp removed": red, 4 subtests
  (was green).
- **OD12's Decisions bullet** points to OD15; Jeff's wording unchanged.

### Q3b as built (2026-11-15, working tree on e240781; build ~/bento/out/m1-p3q3b)

Two fixes to Q3, as OD14 asked (glyphs) plus LOW's cost on the plateaus. OD12 / OD13 / OD14 were already recorded in Decisions
by Q2b; nothing new to record.

What was built:
- **M7 kernel** (analog_kernel.cpp): the plateaus get their own path, `run_flat<SINE | TRI>(o, inc, rinc, out, n, events, role)`
  (out of line, the role a run-time argument: one function per plateau), taken from `by_regime` when the Shape is exactly
  on a plateau (k.ws == 1: sine; k.tw == 1: triangle) **and inc < FLAT_INC = 0.09**; the blends, and the plateaus above
  FLAT_INC, keep run<LOW>. Inside, a sample where the phase may reach a corner (1/2 or the wrap), or a slave has an
  event, goes the long way: `low_step`, LOW's own per-sample body (`sample<LOW>`, out of line, shared by both plateaus and
  all roles), so those samples are LOW's bit for bit. Between corners a correction-free stretch of
  m = floor((lim - p) g) samples, g = rinc (1 - 2^-16) - rinc^2 2^-22, which cannot reach lim (the derivation is in the
  code: each float32 step p += inc errs by at most 2^-25 below 1; the rinc^2 term covers that, 2^-16 the roundings of
  lim - p, rinc, g and the product; no division: g is from E2's rinc). A stretch sample has LOW's now = next = 0, so it
  is out = delayed, delayed = the wave: the triangle tri(p), the same expression; the sine on a half cycle as
  sinq(4 s (p - c)) (c 1/4 rising, s 1; 3/4 falling, s -1) with 4 and s folded into sinq's coefficients (powers of two,
  exact), one subtraction for tri()'s three. The sine stretch runs in pairs (one loop count and branch per two
  samples, an odd one after), the triangle's one at a time. MASTER writes 255 per stretch sample, SLAVE stops a
  stretch at an event. `run<>` now takes its body from `sample<R>` (always_inline: BE4.40's regimes compile as before,
  P3Identity byte-identical, Cost unchanged to the instruction).
- **FLAT_INC (found, not in the brief):** at high notes the long way (~100 instructions, two corners a cycle) outweighs
  the stretch: an_render free per sample, plateau / LOW: 26.3 / 34.5 at inc 0.0625, 32.8 / 35.3 at 0.09, **40.5 / 36.3 at
  0.125, 52.6 / 37.5 at 0.174** (M, the first build without the threshold). So plateaus at inc >= 0.09 (8.6 kHz at 96 kHz,
  above note 120; 4.3 kHz in Rig()'s 48 kHz) take run<LOW>, as in Q3.
- **Two things that cost instructions everywhere, avoided (M):** passing the Shape by address to an out-of-line helper
  spilled an_render's Shape for every regime (saw +6 a call, P3Identity's mod case +0.7 %); `low_step` takes the fields
  by value and `run_flat` takes inc / rinc. Taking p and delayed's addresses for the long-way call kept them on the stack
  through the stretch (+3 a sample); the call goes through copies.
- **M4** (m4_synth.c `glyph()`): the sine is one smooth cycle sin 2 pi p from the middle (crest and trough 5 columns
  each), the triangle two cycles of straight flanks in the kernel's phase (from the bottom, points 3 columns wide). The
  sine's phase is no longer the kernel's (-cos): a glyph names the shape; a free oscillator's phase is not heard.

Tests:
- **`ShapeLow.test_the_plateaus_go_the_long_way_at_every_corner`** (new): 132 calls at incs 2^-20 (the bound's rinc^2 term
  at its largest), 1.3e-4, note 60, 0.0123, 0.073, 0.05, FLAT_INC (1 - 2^-20), FLAT_INC, 0.2, 0.45, each from 0 to 3
  increments below a corner, n 5 / 128 / 512, free, master (events pre-filled 0x55) and slave (random events). low_step's
  out_i hooked by PC: below FLAT_INC every corner sample (float32 phase law) and every slave event went the long way, at
  most 2 x corners + 2 did (M: 147 / 144 and 263 / 261 over the run); at and above FLAT_INC none did. Output the golden's
  within tol_time (slave: low_slave's), phase the float32 law's exactly, master bytes the golden's wraps and 255
  elsewhere.
- **`ShapeLow.test_cost`** (reviewed edit, bounds added, none loosened): + a sine+sync row; at n = 256 (the device's block)
  sine and triangle <= Cost's saw voice, sine+sync and tri+sync <= its saw+sync voice. The blends keep the 1.3 x mixed
  bound; n = 128 keeps it too (there the sine is 1.03 x the saw: below).
- **`ShapeLow.test_one_division_per_render`**: counts VDIV.F32 by PC over an_render, both run_flat and low_step (the
  plateaus' code is out of line now; asserted 4 ranges), still 1 per call.
- **test_synth_ui `Dial.test_the_glyphs_read_as_the_sound`** (reviewed edit, the Q3 sine / triangle criteria replaced):
  sine: starts and ends mid-height (within 2 px), one crest in the first half and one trough in the second, each held
  >= 4 columns; triangle: two points <= 3 columns wide, three bottom points (the first at column 0), every flank column
  moving 2..4 px the same way; the two differ in their crest count. Each sine criterion and the triangle's two-point one
  fails on the Q3 pair (both started at the bottom with one peak in the middle); saw and pulse checks unchanged.

Measured (M):
- **Max difference to Q2b's run<LOW>** (scratch p3/q3b/vsq2b.py: the same an_render calls on this build and on
  ~/bento/out/m1-p3q2b, 2,000 random calls, incs 1e-6..0.45 and powers of two, phases on and just below the corners, n 1
  to 1024, all roles, random slave events): **triangle bit for bit**, output and state (phase, delayed), master events
  identical; **sine within 1.79e-7 in all roles** (this run gave 1.19e-7 free / master, 1.79e-7 slave; the review's rerun,
  5,000 cases with n up to 1024 in every role, 1.79e-7 in each), phase bit for bit. The golden tolerances are unchanged and hold as before (LOW vs golden worst 8.08e-5 = 0.89 of its tolerance at
  Shape 0, the sinq term; `test_the_sine_is_the_stated_sinq` 1e-6). Shape >= 45: P3Identity's 59 cases byte-identical.
- **Cost** (`ShapeLow.test_cost`, hooks per voice-block, both oscillators): n = 256 **sine 13,100, triangle 11,760 against
  the saw voice's 13,236** (0.99 x, 0.89 x; Q3: 23,472 = 1.77 x); sine+sync 14,357, tri+sync 13,215 against saw+sync
  15,845 (Q3 tri+sync 25,833). n = 128 (Rig(): 48 kHz, twice the increment, so twice the corners a sample) sine 7,580,
  triangle 6,896 against the saw's 7,348: **the sine is 1.03 x there**, not at or below. an_render alone at note 60
  (inc 0.00273), per sample free / master / slave: sine 12.97 / 13.98 / 23.27, triangle 10.40 / 11.80 / 18.74, saw 13.32 /
  15.58 / 21.35, Q2b's LOW 31.8 / 34.5 / 44.0 (slave with an event every ~32 samples). At inc 0.0436 (4.2 kHz) sine /
  triangle 22.3 / 19.6 against LOW's 34.0; at and above FLAT_INC LOW's (35.3 at 0.09).
- **Blends: +20 instructions per an_render call** (630 -> 650 at n = 16, 8,242 -> 8,262 at n = 256, M: the dispatch's
  extra compares and an_render's changed layout), so held blends +0.1 % (sine>tri 23,516 against Q3's 23,482) and a LOW
  ramp block, 32 calls of 16 frames, **+2.3 %** (tri+ramp 30,242 against Q3's 29,554; still 1.32 x the saw > square ramp,
  test bound 1.5 x). Cost (Shape >= 45) unchanged to the instruction (saw 13,236, mixed 19,880, saw+sync 15,845, mixed+sync
  22,232); P3Identity 59 cases worst +1.06 % (mod dev=False 111,065 vs 109,899; Q2b +0.97 %), total +0.34 %.
- **Sizes:** DSP **15,572 B: +2,608 on Q2b's 12,964** (after the review fix **15,620 B, +2,656: P3 has used 5,912 B of
  its 14 KB, 8,424 B left**; run_flat<SINE> 816 B, <TRI> 488), as first built **P3 had used 5,864 B of its 14 KB (14,336 B)
  ceiling, 8,472 B left** for Q4 to Q6; 21,036 B free in the window. run_flat<SINE> 768 B, run_flat<TRI> 484, low_step 1,108, an_render
  +236 (0x1920 -> 0x1A0C). Tried and dropped (M): one run_flat per role, 6 instances (+1,008 B for -0.1 a sample);
  the sine stretch unrolled x4 by the compiler (17,516 B with the roles separate, 11.97 a sample); x2 by pragma (+360 B
  for less than the hand-paired loop). Image **0x1890D0** (-8 B: the glyph code), **16,996 B under PROVEN_MAX** 0x18D334;
  synth UI 14,952 B (-8). Dial draw 116,089 instructions, 206 fills.
- **Snapshots** (scratch p3/q3b/snap, from this build's M4 through test_synth_ui's Dial rig, the pane 622 x 142 px):
  `montage_1x.png` (Shapes 0, 12, 24, 36, 48, 68, 88, 127 at the device's pixel scale), `montage_4x.png` (nearest
  neighbour), each Shape's `dial_NNN_1x/4x.png`, and the dial region alone `crop_000/024/048_1x/4x.png`. At 1x the sine
  reads as the usual tilde-like wave and the triangle as a pointed zigzag (an M without verticals); the saw at 48 is the
  other zigzag, told apart by its vertical falls.
- Mutations (scratch p3/q3b/mut/mutate.py, each built from a copy, logs p3/q3b/mut/logs): all 19 red. Failing (sub)tests:
  sine dispatch removed 50 (test_cost, the long-way test, the VDIV test's 4 ranges), triangle dispatch removed 50;
  FLAT_INC 0.46 36, 0.05 36 (long-way test); the stretch one sample too long 87; g = rinc (no margin) 2 (test_spectra:
  a coherent inc lands a stretch on the corner); lim always 1 92; the sine's falling half not negated 57, c 1/4 on both
  halves 57; sinq c1 -0.6417 3 (the stated-sinq test, spectra); the slave's event check dropped from the sine stretch 19,
  from the triangle's 19; the master's 255 not written in the sine stretch 16 (long-way test, 0x55 fill); low_step's tw
  swapped 105. M4 (test_synth_ui): the Q3 pair restored 3, the sine from the bottom 1, the triangle one cycle 2, the
  sine drawn as the triangle 1, the sine two cycles 2.
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q3b) green; test_analog 95 tests (+1), test_synth_ui 62. (After the
  review fixes below: 98 and 62, green.)

Found during Q3b (corrections to this plan):
- **The plateau loop is not a win at every pitch** (FLAT_INC above): Q3's note "specialising would cut that" holds up to
  about inc 0.1 only. A plateau note above about note 120 costs Q3's LOW figure, not the saw's.
- **"At or below the saw" holds on the device's block (n = 256), not in Rig() (n = 128: sine 1.03 x)**: the long way's
  ~100 instructions come twice a cycle, and Rig()'s increment is twice the device's.
- **The 14 KB ceiling is tighter for Q5:** 8,424 B are left (8,472 before the review fix) against 5's estimate for Q4
  (~2 KB) plus Q5's three FM instances (~1.5-2.5 KB each) plus Q6: 6.5 to 9.5 KB (I). If Q5 lands at the high end, the plateau path (2.6 KB) is the
  first thing to trade (it can be compiled out: by_regime's two lines).
- 4.6 / 9's "specialise LOW into ST / TS (~2 KB more)" is superseded for the plateaus by run_flat (2.6 KB, M); the blends
  remain generic.
- Device item 9's glyph judgement is now about the triangle against the saw (both two-cycle zigzags), not the sine
  against the triangle.

Q3b review fixes (two reviews; figures above re-measured on the fixed build where they moved):
- **The plateaus' cost is pitch-dependent** (review 2): "at or below the saw" was measured at note 60 only. The long way
  costs ~200 instructions a cycle against ~20 for the saw, so the plateau overtakes the saw as the pitch rises. an_render
  free per sample, n = 256, this build (scratch p3/q3b/fix/cross.py), plateau / saw: sine 12.76 / 13.31 at note 60, 13.39 /
  13.33 at 72 (**crossover ~note 72**), 14.54 at 84 (1.09 x), 17.10 at 96 (1.27 x), 22.23 at 108 (1.64 x), 30.82 at 119
  (2.24 x); triangle 10.18 at 60, 13.07 at 90 (0.98 x, **crossover ~note 91**), 14.41 at 96 (1.07 x), 19.50 at 108 (1.44 x),
  28.21 at 119 (2.05 x). Q2b's generic LOW is 31.8..35.3 over the same range, so below FLAT_INC the plateaus never cost
  more than Q3 did. 4.6, 5, the risks row and device item 12 restated; test_cost still asserts note 60 only (the bound
  is the claim it makes).
- **A plateau slave under dense master events** (review 1): every event sample goes the long way and the stretch is
  recomputed, so with an event every few samples run_flat costs more than run<LOW>. The sine's pair loop also sent ev[0]
  the long way when only ev[1] held the event; **fixed** (ev[0] now in the stretch: one(), then low_step for ev[1]; +48 B).
  Slave at inc 0.0027, master saw events, n = 256, per sample this build / Q2b's LOW (scratch p3/q3b/fix/slavecost2.py):
  master inc 0.031: sine 20.4 / 43.7, triangle 18.2; 0.2: 50.4 / 55.3, 46.5; 0.25: 60.6 / 60.6 (was 85.6), 58.1; 0.45 (the
  clamp): **sine 97.3 / 75.2 = 1.29 x** (was 131.4 = 1.75 x), triangle 93.7 = 1.25 x. FLAT_INC looks at the slave's
  increment only; the master would need to be above ~24 kHz (tune, pitch modulation or Q5's deep Sub FM on it). Recorded,
  not gated: a per-block event count to choose run<LOW> would cost every slave block for a corner case.
- **Tests added** (review 2's major, both reviews' minors): `ShapeLow.test_the_stretch_bound_holds_on_long_stretches`
  (32 calls chosen on the host so that the bound without its rinc^2 term, float32 as compiled, overruns the corner:
  stretches of 5 to 250 samples at incs 1e-5..1e-3, n 512, all roles; the corner test's calls start within 3
  increments of a corner, where that term never matters); `ShapeLow.test_the_plateaus_are_lows_own_samples` (the
  plateaus against one low_step call per sample, the build's own LOW sample by symbol: the triangle bit for bit, output,
  phase, delayed, master bytes; the sine within 2e-7, M 1.19e-7 on its 36 calls, phase and master bytes bit for bit);
  `ShapeLow.test_only_the_exact_plateaus_run_flat` (k one float32 step off -1 and either side of -0.5, Shapes 3 / 21 / 27,
  free and slave: no low_step call, the Shapes against the golden / low_slave). The long-way bound tightened from
  2 x corners + 2 to corners + 2 (M 147 / 144, 263 / 261). test_synth_ui: the sine and triangle glyphs must be the
  pulse's height (19 px; the saw's BE4.40 ramp is 17: its last step stops short), not just > 14 px. test_cost's docstring
  figures made the plan's. test_analog 98 tests (+3).
- Mutations against the fixed tests (scratch p3/q3b/fix/mut/fmut.py, results.txt; each built from a copy): all red.
  g without the rinc^2 term 32 (the new long-stretch test; it survived the whole suite before), g = rinc 34 (that test
  and test_spectra), the sine's odd tail dropped 12 (the corner test's tightened bound, the long-stretch test), the sine
  dispatch loosened to ws >= .9 8 (the exact-plateau test; it survived before), the triangle's to tw >= .9 17, sinq's
  first coefficient 2 ulp off in the stretch 12 (the low_step equality test: an error ~1e-3 of the golden's tolerance),
  the triangle stretch's order swapped 84, the sine glyph at amplitude 7 1 (test_synth_ui; it survived before).
- Kept as is: the triangle and the saw glyph are the closest pair now (review 1); device item 9 judges it.

### Q4 as built (2026-11-15, working tree on 9db0b13; build ~/bento/out/m1-p3q4)

OD10 (DC-free Square) as decided. OD12 / OD13 / OD14 were recorded in Decisions by Q2b; nothing new to record. Nothing
audible reads A yet: P3Identity's 59 cases stay byte-identical with BE4.40, and the amounts cases (Sub FM 700) fill A and
still play BE4.40 byte for byte.

What was built:
- **Base table:** `SUB_NOISE_TABLE` 0x080CB9F8 (data, facts.py REVIEWED; stock_161.py absent; stock_168.py regenerated,
  only this entry changed): the sub core's 4096-float noise, its one literal 0x08056D78 (S). Test-only addresses come from
  the image at run time (the sub class's vtable slots 0 / 2 / 8 / 10; its smoother is SMOOTH_APPLY's r0 = sub + 0xCC), so no
  other entry.
- **M7 kernel** (analog_kernel.cpp): `an_sub_copy(core, a, n, inc, pend)`, the prototype's sub_copy without its `exact` /
  `dcfree` flags (always stock's table sine, always DC-free): a[i] = (y - dc) inc 2^-32, waves 1 Rev Saw, 2 Triangle, 3 Square
  (the width latched from pend at the first wrap, dc = 1 - pend 2^-31), 4 Sine (the core's table +4), 5 Noise (stepped by the
  core's stride +0x10); any other wave byte copies 0. Stock's operation order and constants (re-read in 0x08056720, S),
  `__attribute__((minsize))` (below).
- **M7 hook** (analog.cpp): `sub_inc` (stock's formula, 0x080A0E18..0x080A0E68 then 0x08056E40) and `sub_fill` (noinline:
  PARAM_VALUE's cache words +0xC0 (pitch) and, for the Square, +0x120 (width) saved and put back; the width as 0x08056440,
  1..0xFFFFFFFE). In an_osc after the skip path and before E3's silent-master branch: `if (subfm || msub)`: n > AN_FM_FRAMES
  -> `fm_skipped++`; else, for the voice an_enter named and A not yet tagged, fill A (SA_FMBUF_A), `Run.inc_sub` = inc 2^-32
  (float bits), `fmtag |= A`. So OSC1 fills when it has Sub FM, else OSC2 (OSC1 Wavetable, skipped, or no Sub FM); a
  silent sync master with Sub FM fills (Q5's E3 (b) will need it). **Invariant the tag rests on (I, for Q5):** an
  oscillator renders only from stock's voice render, i.e. slot 18 after A1's an_enter, which clears fmtag; an_osc does not
  compare the load-ring idx, so an oscillator render with no an_enter before it in a later audio block would find last
  block's tag for the same voice and play last block's A. Tested only as far as the voice check (another voice's render
  fills nothing). If Q5 finds a path that breaks it, tag the block too (r->idx at fill time).
- synth_shared.h: comment only (fm_skipped counts since Q4).

Tests (test_analog, new class `SubCopy`, 10 tests with the review's two; harness: stock's root ctor and engine init, the voice through the root's
vtable word, so stock's sub renders after OSC1 / OSC2; the sub's +0xDC = 2 and n = 256 asserted):
- `test_the_copy_is_stocks_sub_up_to_its_dc` (6.4): 5 waves x sub Level 0 / 1000, 12 device blocks with notes 48 / 61 / 36,
  a retrigger (same note, retrigger on), OSC1 Glide 300 (an_enter writes +0x04), fine 250, pitch modulation on +0x84 every
  block, the Square at PW 30 % then 65 % from block 6 (latched mid-block). Every block: **A = (raw - dc) inc 2^-32 in float32
  bit for bit, worst 0** (raw = the sub's core block at its smoother's input), Run.inc_sub, fmtag A; A / inc + dc - raw
  within 2^-22 (two roundings); dc {0.4, -0.3} for the Square, 0 else; one fill a block, by OSC1. Controls: each note start
  restarts the phase, the increment, +0x04 and the pitch cache move.
- `test_the_copy_changes_nothing_else`: Sub FM 700 against Sub FM 0, 5 waves: the output byte for byte, and **the whole
  voice at the sub render's entry byte for byte** (P3 bytes masked), the two cache words included (control: they move);
  per block, the instructions between the two minus sub_fill's are 0 < x < 40 (the gate and the call).
- `test_which_oscillator_fills`: OSC2 only, both (one fill, OSC1's), OSC1 Wavetable, OSC1 silent (OSC2 fills), msub with
  Sub FM 0, and (review) a silent sync master (OSC1 Level 0, OSC2 Sync: E3's branch) with Sub FM on OSC1 alone and on both:
  OSC1 fills; A exact in each.
- `test_each_voice_fills_its_own`: two voices of one root in one audio block, different waves / notes: each its own A.
- (review) `test_only_the_named_voice_fills`: voice 1's OSC1 rendered while Run.voice names voice 0: no fill, A and fmtag
  untouched; voice 0's OSC1 then fills its own sub's.
- (review) `test_the_increment_is_stocks`: an_sub_copy's increment argument = stock's core +0xC after its render, 24 blocks:
  a note start each block over 0..127, fine over both signs and odd values (-999..999), glide and pitch mod under it; A
  exact (Rev Saw).
- `test_other_waves_copy_silence`: wave key 5, 9, 255 (core bytes 6, 10, 0): A +0.0 (A holds a 0x5A pattern before every
  block of `play`, review), output unchanged.
- `test_longer_blocks_than_the_buffer_fill_nothing`: n = 512: A untouched, fmtag clear, fm_skipped = 6 (2 oscillators x 3
  blocks); n = 256 fills.
- `test_the_squares_width_at_its_ends`: PW 0 and 100 % (pend 1 / 0xFFFFFFFE), dc +-1.
- `test_the_copy_divides_at_the_subs_edges_only` (6.5, ahead of Q5's row): VDIV.F32 by PC inside an_sub_copy per block =
  the phase law's edges (Rev Saw wraps; Square wraps + rising-ramp samples with the latch), 0 for the others.
- **Reviewed edit (expected change):** P3Identity `check(..., fills=n)`: in `test_q1_plays_no_amount` (Sub FM 700) this
  build fills A once per voice-block (asserted: 8 / 11 / 24 per case); sub_fill's own instructions (Span: entry to return,
  stock helpers included) are recorded and left out of the +2 % bound, which still holds for the rest. Output, voice and
  stats unchanged. This is a bound carve-out (6.7), so (review) the copy has its own bound there: `FILL_MAX` a call (Rig's
  zero sub 640 / 1,175 at n = 128 / 256, stock's root's default sub 2,005 / 3,890; measured +5 %).

Measured (M):
- Sizes: DSP **16,452 B** (+832 on Q3b's 15,620: an_sub_copy 460, sub_fill 276, an_osc +96), **P3 has used 6,744 B of its
  14,336 B ceiling, 7,592 B left for Q5 / Q6**; 20,156 B free in the window. Image **0x1890D0** (unchanged: M4 untouched),
  16,996 B under PROVEN_MAX 0x18D334.
- **minsize, a close call:** an_sub_copy at the file's -O2 is 1,968 B (unrolled), at minsize 460 B. sub_fill per voice-block
  (2N, its stock helpers included) -O2 / minsize: Rev Saw 3,297 / 3,724, Triangle 2,516 / 2,952, Square 3,594 / 3,994,
  **Sine 4,830 / 5,256**, Noise 2,075 / 2,953. Loop pragmas (no unroll) on every loop: 536 B, the Sine 5,515 (worse than
  minsize); on all but the Sine: 1,004 B with the Sine at -O2 speed (scratch p3/q4 variant.py). Taken: minsize, because the
  14 KB ceiling is the scarcer budget for Q5's three FM instances (5: 4.5-7.5 KB); +430..880 instructions a voice-block
  with Sub FM on (one copy per voice, not per oscillator). If Q5 lands small, the Sine-at--O2 form (+544 B) is the first
  buy-back.
- Cost, off: `Cost` at n = 256 saw 13,244 (Q3b 13,236: +4 a render, the subfm / msub test), mixed 19,888, saw+sync 15,853,
  mixed+sync 22,240; n = 128 saw 7,356. P3Identity 59 cases, worst **+1.13 %** (mod dev=False 111,145 vs 109,899; Q3b
  +1.06 %), total +0.47 % (the amounts cases' gate and call now in it; M1 +2 %). Rig's zero sub (wave 0) costs 606 / 1,118 a
  fill at n = 128 / 256; stock's default sub at 2N 3,701.
- Mutations (scratch p3/q4 variants.py / variant.py, each built from a copy, the whole test_analog; logs p3/q4/var/logs):
  all red. Sub copy per 128-frame chunk (6.6; the second half from an advanced phase, width unlatched): 8 failing (sub)tests;
  Noise advancing by inc (6.6): copy test; Square dc not subtracted (6.6): 11; dc from the old width: 11; pitch cache not
  restored (6.6): 5 (changes_nothing_else); width cache not restored: 1 (same test); width latch removed: 7; Sine without
  interpolation: 3; fmtag not set (OSC2 fills again): 27; the gate without msub: 1; the n > 256 guard removed: 1 (the longer-blocks test);
  fm_skipped not counted: longer-blocks test; Run.inc_sub not stored: 20; sub_inc without the +0xDC term: 20; without +0x04
  (glide): 19; the fill only from OSC1: 6; the width's 1..0xFFFFFFFE hold removed: 1 (the width-ends test; green until that
  test was added). (The first mutation batch also saw a too-high control threshold in the VDIV test, since fixed; no mutant
  relied on it.)
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q4) green; test_analog 108 tests (Q3b 98 + 10 SubCopy), test_native_props 19, test_synth_ui 62, test_keys 10.

Found during Q4 (corrections to this plan):
- **Stock's sub core has 10 waves, and the sub's wave key is unclamped** (0x080A0CC6 strb value + 1; core dispatch 1..10,
  tbh at 0x08056732; S): waves 6..10 (unipolar sine, inverted ramps, a rand() sample-and-hold) or 0 are reachable from a
  hand-edited file. 4.2 lists 5. The copy plays 0 for them (no Sub FM), tested.
- **Noise's stride is random per block** (stock: `rand() % 256 + 1` after each render, 0x08056AFE..10; S). "Steps by its
  stride" (M) holds; the copy reads the stride stock will use this block.
- **"raw = A / inc_sub_f + dc, worst 0" (6.4) cannot hold in float32:** the store rounds twice. The test is the forward form,
  A = f32(f32(raw - dc) inc), bit for bit; the inverse within 2^-22.
- 5's "sub copy ~2 KB" holds at -O2 (2,244 B with sub_fill); 736 B as built (an_sub_copy at minsize + sub_fill; 832 with the gate in an_osc).
- The sub render also calls PARAM_VALUE on +0x44 (its level) and SMOOTH_SET at +0xCC; the copy touches neither (S).

Q4 review fixes (two reviews; each issue checked; rebuilt clean into ~/bento/out/m1-p3q4, the image byte-identical to the
reviewed one: only comments changed in the DSP; sizes and costs above unchanged):
- **Tests added for the four mutants that survived review 2** (scratch p3/q4fix/run.py, results.txt; each built from a copy,
  the whole test_analog): the fill gate moved below E3's silent-master branch (red: the two silent-sync-master cases), the
  `r->voice == v` check removed (red: test_only_the_named_voice_fills), the fine word unsigned and `/ 1000 / 12` folded to
  `/ 12000` (red: test_the_increment_is_stocks; both were bit-identical at fine 250 only). Also red with the tightened tests:
  the default case writing nothing (now test_other_waves_copy_silence too, A pre-filled), `hz * (2^32 / 48000)` (now the
  increment sweep too, not only one Square case), and a doubled copy (the new FILL_MAX bound, and SUB_COST_MAX).
- **The amounts cases' copy bound** (review 1): the carve-out named in 6.7, FILL_MAX asserted a call (above).
- **Comments** (review 1): the Square's dc is one rounding of f32(pend) x 2^-31 (fma), not exact; minsize's +9..42 % is
  sub_fill's with its stock helpers.
- **Doc** (review 1): the Square's partial-cycle DC after a PW change (4.2; Q5's mean-frequency case holds PW constant or
  bounds it); the tag's invariant (an oscillator renders only after A1's an_enter) recorded for Q5 above, not changed in
  Q4: tagging the block too costs every fill for a path the device does not have.

### Q5 as built (2026-11-16, working tree on 479cc79; build ~/bento/out/m1-p3q5)

OD5 as decided (any master wrap resets the slave; a backward slave resets to p = 1, one edge). OD12 / OD13 / OD14 were
recorded in Decisions by Q2b; nothing new to record. p3_golden.py's Q5 section (written from the plan, Q3's model and
test_analog's BE4.40 model alone, before and without any FM C/C++) is used with **one bug fixed** (below).

What was built:
- **M7 kernel** (analog_kernel.cpp): `an_render_fm(o, inc, shape, out, n, events, role, const FmIn *f)` (analog.h
  `FmIn {a, ia, dia, off}`: A from the block's frame 0, the index line, the call's first frame). Per sample
  `di = fma(fma(dia, off + i, ia), a[off + i], inc)` (float32, each fma one rounding), VMAXNM / VMINNM to +-0.45, then
  `end = p + di`. Any Shape: the carrier's terms are gathered once a call (`Fx`): k >= 0 BE4.40's MIXED regrouped as
  `A p + B + (p < w ? C : -C)` (the saw 2p - 1 and the pulse +-1 - dc one rounding each), k < 0 LOW's blend; one inner
  edge `m` (the pulse's width when blend > 0, the triangle's top when tw > 0, 2 = none) and the wrap, each with a
  forward value jump and slope change; a backward crossing is the mirror (jump negated, slope change |di| dS).
- **Fast path / long way (a deviation from 4.3's "3 instances", flag for review):** most samples cross nothing: they stay
  in [lo, hi), the edges either side of p (taken again after every long-way sample), and play `out = delayed, delayed =
  wave(end)` with no correction and no division. A sample that crosses, or a slave's event, goes the long way:
  `fm_slow` (noinline, shared by the three roles, the role a run-time argument, the state by copies as Q3b's low_step):
  4.3's generic sample, one VDIV per crossing (`(edge - p) / di`), the master's byte `(int) fma(fr, 254, 0.5)` either
  way, the slave's reset (OD5: 0 forward, 1.0 = the left limit backward, none at di == 0; the value BLEP and, in LOW, Q3's
  slope BLAMP, OD14). The fast loop is versioned on the wave's form (LOW / MIXED), so 6 small loops plus one fm_slow.
  Why (M, an_render_fm free per sample, the saw at note 60, index 8): the first build, 4.3 as written (one generic
  sample inlined per role), **41.6 instructions a sample** (5,012 B) and the Sub FM voice 33,952 against the gate's 29,913
  (red); gathered terms and one inner edge 40.6; the fast path 33.4 (3,932 B); versioned 29.4 (4,520 B). Close call: the
  versioning costs 592 B of the 14 KB for 4 instructions a sample.
- **`__builtin_fmaf` is a library call here** (found): under -fno-builtin it links compiler_rt's `fmaf`, which pulls in
  double helpers (`__aeabi_dadd` ...), and check_dsp_elf refuses the ELF. The kernel writes VFMA.F32 by name (`fma32`,
  inline asm); the phase law and di are stated with it (p3_golden `fma = True`).
- **M7 hook** (analog.cpp an_osc, 4.1 / 4.4 / 4.8): `fm = (subfm || msub) && Run.voice == v && (fmtag & A)`; the line
  `ia = I(msub)`, `dia = i1 != i0 ? (I(subfm) - ia) / n : 0` (one VDIV in the hook per FM render: the compiler
  computes the step and selects, so a held amount divides too; M, SubFm's ramp test counts it), `I(v) = f32(v^2) x
  f32(8e-6)` (`an_index`, FD3); `msub = fm ? subfm : 0`; `fm=` (SA_FMSTATS.fm) once per render with FM. The part loop
  calls `an_render_fm` in place of `an_render` with `fi.off = off` (the ramp parts and sync chunks continue one line),
  then the crush (Bit Depth after FM, OD12's per-part dc). **E3 (b):** a silent master with `fm` renders its chunks under
  FM in the MASTER role into the scratch block (its stored Shape: the wraps do not depend on it), unheard, so a ramp down
  stays on FM to its end; without `fm`, an_track as before. **E3 (c)** needed no code: a silent OSC1 that is no master
  takes its skip path, and OSC2 fills A (Q4's gate). The mixed voice needed none either: OSC2 fills A and runs its own
  role (FREE, or SLAVE on an_master's events). FM off: the `fm` test and the `fm ?` per part, nothing else (M1).
- Comments: analog.h (msub, AnProps), synth_shared.h (SaFmStats.fm counts since Q5).

Tests (test_analog, 108 -> 124 tests: + `FmKernel` 5, `SubFm` 11):
- **`FmKernel.test_the_kernel_is_the_golden_model`**: 243 cases (Shapes 0, 12, 24, 36, 48, 68, 88, 100, 127 x incs note 60
  at 96 kHz / 0.0123 / 0.2 x a sine sub an octave down at index 8, a DC-free Square at PW 30 % at index 1, a saw at 1.5 x
  ramping 0 -> 8 x free / master / slave), 2N in two parts of 128 (the line continuing), the slave on an FM'd golden
  master's bytes (forward and backward wraps). Each part: the end phase the golden's exactly (di's arithmetic as stated),
  every sample within `fm_tol` (TOL_F32 + ws SINQ_ERR; a slave twice the sine's term, Q3's slave bound), the master's
  bytes the golden's (+-1 on ev_near), and **the VDIVs inside an_render_fm and fm_slow (by PC) = the golden's dividing
  crossings, +- 0**, at most 2n. M: worst 0.89 of the tolerance (Shape 0: sinq), 414 backward resets, 1,989 backward
  wraps, the clamp reached (lowest di -0.45).
- `test_zero_modulation_is_the_plain_render` (A = 0: an_render's samples within the tolerance, its phase bit for bit, the
  bytes within 1: an_render multiplies by its 1 / inc), `test_the_mutants_the_golden_names_are_seen` (on deep through-zero
  cases, inc 0.0123, 256 samples: no_mirror 1.9e5 x the tolerance, no_backward 9.6e4, rinc 9.5e4; **reset_p0 0.80 x: the
  double edge plays the one-edge rule's output, as the golden's author found, so its red test is the edge count: 40 extra
  backward wraps there**), `test_the_index_is_the_square_law` (an_index = fm_index within float32 for v 0..1000; I(500) = 2,
  I(1000) = 8 exactly; never the linear law), `test_the_worst_case_divides_at_most_2n` (8 % pulse, a sine at 3 x the
  carrier at index 8, inc 0.2: 110 VDIVs / 128, free 77.1 / master 85.2 instructions a sample: the long way's call).
- **`SubFm`** (the device's path: stock's root, engine init, 2N, stock's sub; `fm_capture` hooks every an_render_fm call at
  its entry and at an_osc's return sites, and each call is replayed through the golden with its own A, line, state, role
  and bytes): both oscillators free (Shapes (48, 127), (24, 0); through zero; the line from 0 in the first block, msub
  starting at 0, then held at 8; fm= 12; the output not Sub FM 0's); every sub wave (Rev Saw, Triangle, Square, Sine,
  Noise: the call's own A, so Noise too, which the golden does not model); the index ramp over 8 blocks (0, 1000, 1000,
  500, 0, 0, 250, 1000: each block's line, the ramp down still FM, the block after it on an_render, msub the last amount);
  both-Analog sync (OSC1 MASTER with Sub FM, OSC2 SLAVE with Sub FM: each slave block's bytes = its master's); E3 (b) (a
  silent master with Sub FM 700, 700, 0, 0, 0: FM in blocks 0..2, the ramp down included, an_track from block 3); E3 (c)
  (OSC1 silent, no sync: only OSC2 fills and renders); the mixed voice (OSC1 Wavetable; OSC2 free and synced to it);
  **Bit Depth after FM** (8 / 4 bits, a pulse with a dc: the Level-input block = the golden crush of the FM calls' samples,
  bit for bit); **the Square sub keeps the pitch** (6.3, OD10: PW 30 %, index 8, over the window's 4 whole sub cycles the
  carrier travels 3.9986 cycles against inc L 3.9993: within one bin; the raw Square would add 12.8); the amounts cases as
  the expected change (below); the cost gate.
- **Reviewed edits (expected changes, no tolerance loosened):**
  - `P3Identity.AMOUNTS` (700, 0, 333) -> **(0, 0, 333)**: Sub FM left the "plays nothing" cases (FM 1>2 stays until Q6);
    `test_q1_plays_no_amount` and `test_glide_dev_is_the_expected_change` expect the stored (0, 0, 333), and with no Sub FM
    no fill (Q4's fill carve-out does not apply: the whole count is under +2 %). The seven cases with Sub FM 700 (both rigs)
    are **`SubFm.test_the_amounts_cases_are_the_expected_change`**: every FM call the golden's (Rig's own voices have a
    zero sub: di = inc, the FM loop at rest), fm= = the renders with FM.
  - BitDepth `expected_change` and `test_each_oscillator_crushes_at_its_own_depth`: the SA_FMSTATS line now has fm= = the
    FM renders (both runs alike, > 0) next to bits=; the golden-crush comparisons are unchanged (and now crush FM renders).
  - `Props.test_the_silent_paths_zero_the_fm_ramp_memory`: the oscillator that played, msub hand-set to 123 with Sub FM 0,
    ramps it down and ends at **0** (was: kept 123; m12 still kept, Q6).
  - SubCopy `test_the_copy_changes_nothing_else`: Sub FM 700 now plays, so the comparison is **the sub object's own bytes**
    at its render (voice +0x4C .. +0x178, the cache words included), not the whole voice and the output; Q4's instruction
    check (the gate and the call) went with it (SubFm.test_cost bounds the whole). `test_other_waves_copy_silence`: A = 0
    renders through the FM loop at rest (first edit: within 1e-5 of Sub FM 0's output, a loosened tolerance; **review
    fix:** byte for byte again, below).

Measured (M):
- **The specialised delta at 2N** (the rc26 prototype /root/rv26dsp/out/proto.elf, build.py's DSP flags, in Unicorn: scratch
  p3/q5/proto_cost.py; at n = 128 it reproduces plan-analog-fm 6's 1,733 / 3,037 / 2,222): a saw 3,397, a Sub FM saw
  through zero 5,997 (**+2,600**), with B written 6,761 (+3,364), master 6,543 / base 3,907, master writing B 7,307
  (+3,400); the sine copy (-O2) **4,398**. sub_inc's stock part (our sub_fill minus an_sub_copy) 118.
- **The gate, fixed:** Sub FM -> both (Q5's voice) 1.3 x (13,294 + 2 x 2,600 + 4,398 + 118) = **29,913**; the full voice
  for Q6 (sub -> both, OSC1 -> OSC2 writing B, free) 1.3 x (13,294 + 3,364 + 2,600 + 4,398 + 118) = **30,906** (the
  provisional 31 k held; plan 5's "+11 k" was 10.5 k). Measured on the device's path (SubFm.test_cost, an_enter + an_osc
  with their helpers, note 60, both saws, a sine sub): off 13,294, **Sub FM on both 27,069 = 0.905 of the gate** (2.04 x the
  off voice; sub_fill 5,256 of it), synced 28,982.
- an_render_fm per sample at note 60, index 8 (free / master / slave): saw 29.4 / 31.5 / 43.6, square 30.3 / 32.5 / 43.6,
  8 % pulse 30.3 / 32.5 / 47.5, triangle 41.4 / 43.5 / 60.5, sine 40.4 / 42.6 / 60.5 (an_render: 13.3 / 15.4 / 23.0 saw).
- Sizes: DSP **21,404 B** (+4,952 on Q4's 16,452: an_render_fm 2,196, fm_slow 2,324, an_osc +420), **P3 has used 11,696 B of
  its 14,336 B ceiling: 2,640 B left for Q6** (5's estimate for Q5 was 4.5-7.5 KB: within it), 15,204 B free in the window.
  Image **0x1890D0** (unchanged: M4 untouched), 16,996 B under PROVEN_MAX 0x18D334.
- Cost, off: `Cost` at n = 256 saw 13,272 (Q4 13,244), mixed 19,916, saw+sync 15,887, mixed+sync 22,274; n = 128 saw
  7,384. P3Identity 59 cases, worst **+1.57 %** (mod dev=False 111,627 vs 109,899; Q4 +1.13 %: the `fm ?` per part and the
  amounts' gate), total +0.64 % (M1 +2 %). BitDepth's crush cost unchanged (10.77 a crushed sample).
- Mutations (scratch p3/q5/mutate.py, each built from a copy of the repo, run against FmKernel / SubFm (Props for the skip
  path); logs p3/q5/mut/): all 16 red. Failing (sub)tests:
  E3 (b) still using an_track 3; E3 (b) gated on the amount only (no msub) 1; msub not reset on the skip path 2 (Props);
  the linear index law 1 (the ramp test's line); the clamp missing (+-4.5) 82; backward inner edge not mirrored 101,
  backward wrap not mirrored 115; the backward reset to p = 0 (the double edge) 49 (the VDIV / edge counts, as predicted);
  no reset when di < 0 48; the pulse's dc dropped from the FM wave 78; the ramp starting at the amount (no ramp) 3; the
  ramp restarted per part (off 0) **green at first** (every chunked call was replayed against its own A), red (2) once the
  sync test checked the chunks' line and offsets; fm= not counted 31; the fast path's lo left at 0 11; Sub FM on OSC1
  only 16 + 1 error; the crush skipped after FM 1.
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q5) green: test_analog 124 tests, test_native_props 19, test_synth_ui 62,
  test_keys 10 (test_drums / test_drum_ui not run: drums omitted on 1.6.8); p3_golden.py self-test 38 tests OK (after its fix).

Found during Q5 (corrections to this plan and to the golden model):
- **p3_golden bug (fixed, demonstrated):** fm_render's float32 master byte was `_fma32(f, 254, 0.5)`, i.e. f + 127, so
  every golden master byte was 127 (M: 81 of FmKernel's master cases red on it with the kernel's bytes spread over
  0..254). `_fma32(a, b, c)` is a + b c; the byte is f32(0.5 + f 254): now `_fma32(0.5, f, 254.0)`. One line; its self-test
  never compared a float32 byte against an independent one. No other change to the model.
- **The double edge (6.6) is not an output mutant** (the golden's author's finding, confirmed on the kernel's own cases:
  0.80 x the tolerance): OD5's p = 1 buys one edge fewer per backward reset (cost and the VDIV count), not a different
  sound. Its red test is 6.5's edge count (FmKernel's VDIV = edges; the reset_p0 kernel mutant above). OD5 stands as
  decided; nothing for Jeff unless he wants the rationale restated as "cost, not sound".
- **4.4's "after silence the amount starts at its set value with no ramp" is not what msub = 0 does:** 0 is a real
  amount, not a "no ramp" mark (mshape has AN_NO_SHAPE; msub has no sentinel), so an oscillator heard again ramps its
  index in from 0 over its first block (2.7 ms at 2N), as at a note's first block. Kept (Level ramps up from 0 there too,
  I: inaudible); a sentinel (0xFFFF) would change Q1's pinned zeros (review: a flag byte would not). Open: **OD16**.
- **4.3's "3 instances"** is 6 small fast loops + one shared long-way sample (above), for CPU: the gate failed as written.
- **5's DSP estimate held for Q5 (4.9 KB), but the ceiling is now tight for Q6:** 2,640 B left for B, E3 (a), the m12 ramp
  and the B term in the loop (the fast loop's second fma: 6 copies). If Q6 does not fit, the first trade is Q3b's plateau
  path (2.6 KB), then the fast loop's LOW / MIXED versioning (592 B, +4 instructions a sample).
- Not done in Q5 (6.3 rows outside the sprint's list): the through-zero DC-drift check over 100 blocks and the aliasing
  check after the FIR model; the Bessel check is Q6's (sine / sine needs FM 1>2). Review fix: both are now in Q6's exit (7).

Q5 review fixes (two reviews; each issue checked; rebuilt clean into ~/bento/out/m1-p3q5, the image byte-identical to the
reviewed one: only comments changed in the DSP; sizes and costs above unchanged; test_analog 124 -> 126 tests):
- **Tests for the review's surviving mutants** (scratch p3/q5fix/mut.py, results.txt; each built from a copy, run against
  SubCopy / SubFm / FmKernel / Props): E3 (b)'s chunks at `fi.off = 0` (both reviews): test_a_silent_master_renders_its_wraps
  _under_fm now checks its chunks' line and offsets (`lines`, off {0, 128}), as the sync test does. The gate without its tag
  term (a render past A's 256 floats on a longer block) and without `Run.voice == v`, and `msub = subfm` whatever played:
  test_longer_blocks_than_the_buffer_fill_nothing now asserts no an_render_fm call, fm= 0, msub 0 after the long blocks and
  the next 2N block's line from I(0); test_only_the_named_voice_fills renders voice 1's OSC1 (Sub FM 700) again after voice
  0 tagged A: an_render, no FM call, msub 0. E3 (b) at k = 0: new test_a_silent_master_heard_again_continues (OSC1 Shape
  100, silent two blocks then heard: one k throughout, the stored Shape's; the first heard call from the last silent
  call's state; the line continuing). OD5's corners: new FmKernel.test_the_corner_rules (golden-checked, VDIVs = its
  crossings): a master wrap at fraction exactly 0 from p = 0 backward and from p = 1.0 forward writes byte 0; a slave event
  at di == 0 exactly resets nothing (one at di > 0 does); p exactly on the pulse edge moving backward crosses it.
- **test_other_waves_copy_silence** (both reviews: a loosened tolerance): measured, the voice's output is Sub FM 0's byte
  for byte (worst 0), so the comparison is exact again, and each FM call is stated: A = +0.0 bit for bit, one call a block
  on OSC1, replayed through the golden (SubFm.replay).
- **Not restored: Q4's < 40-instruction gate-and-call check** in test_the_copy_changes_nothing_else (review 2: acceptable
  if noted). With Sub FM 700 now playing FM there is no FM-free pair to difference; the FM-off cost of the gate is
  P3Identity's (+2 % of BE4.40 with Sub FM 0), and the FM-on whole is SubFm.test_cost's gate.
- **`di_no_fma` is an equivalent mutant** (review 2, M: the same 38 VFMA / 14 VMUL / 21 VADD in an_render_fm): clang's
  contraction makes di's two VFMAs anyway; fma32's comment says the name keeps the law independent of -ffp-contract.
- **Comments** (review 1): analog.h's msub and an_osc's skip path said "heard again: no ramp" for the FM amounts; they
  now say the index ramps in from 0 (OD16).
- **Doc** (both reviews): 6.3's DC-drift and aliasing checks scheduled into Q6's exit (7); M1's remaining FM-off headroom
  (0.43 pt) and the triangle-carrier Sub FM voice (33,407, 1.117 x the saw's gate, measured: SubFm.test_cost prints it) in
  5 and 9; 4.4's departure as **OD16**.

### Q6 as built (2026-11-16, working tree on cfae2b9; build ~/bento/out/m1-p3q6)

OD15 = (b) as decided (B's grid with its top at S, the audio grid unchanged, FM 1>2 path only); OD5; BD1; FM 1>2 only when
OSC1 is Analog. **OD16 = (a)** recorded in Decisions (Claude's recommendation, Jeff told, no objection yet). p3_golden.py's
Q6 section (written from the plan, the Q2b / Q5 model and test_analog's harness API alone, before and without any FM 1>2
C/C++; stock's FIR taps read off the image) is used **unchanged** (no bug demonstrated; one weak self-test case noted below).

What was built:
- **M7 kernel** (analog_kernel.cpp): `an_feed(s, b, n, bits, dc, inc)`: B from one part of OSC1's rendered block, before
  its audio crush and before Level, `b = f32(inc q)`, q = s at Full, else `clamp(rint((s + dc) S), -S, S) / S - dc` (an_crush's
  arithmetic with the top at S: OD15 (b)); inc OSC1's base increment (kin = I inc1, 4.1). p3_golden.b_buffer bit for bit.
  `FmIn` gains a second term `{b, ib, dib}` folded after the first (`di = fma(J, b, fma(I, a, inc))`, 4.3's order, the
  golden's "ab"). **Deviation (flag):** the second term has its own loop, `fm_loop<ROLE, 2, 1>` for FREE and SLAVE only, the
  wave's form chosen per sample there (a predictable branch) instead of per call: 2 instances, not 6 more. Why (M): with the
  second term in every loop (a zero line for one route: +0 exactly) the full-FM voice measured 30,991 against its gate
  30,974 (1.001: red) and the Sub FM voice paid ~4 instructions a sample for nothing (an_render_fm 2,312 B); separate
  two-term loops for all 3 roles 3,456 B (P3 then 14,456 B, over the 14 KB ceiling); for FREE + SLAVE 3,064 B (the full
  voice 30,753: 0.993). **MASTER reads no second term** (OSC1 is the only
  master and never has FM 1>2; FmIn's contract says so; a master given one plays the first alone).
- **M7 hook** (analog.cpp an_osc, 4.1 / 4.4 / 4.5 / 4.8):
  - The FM gate is one word: the oscillator's Sub FM | its ramp memory (msub, m12 in one load) | FM 1>2 (OSC2, OSC1
    Analog) | for OSC1 OSC2's FM 1>2 and m12. All 0: today's path (msub / m12 are 0 already, no store).
  - `feeds()` (out of line, behind the gate): OSC1 feeds B while an Analog OSC2 has FM 1>2 or m12, for the voice an_enter
    named, n <= 256. Feeding, a silent OSC1 does not take the skip path (**E3 (a)**) nor the E3 (b) branch: it renders
    through the normal part loop (as the MASTER when synced: its wraps the slave's), feeds B, skips SMOOTH / MIX.
  - `fm_setup()` (out of line): Q4's fill, Q5's Sub FM line and Q6's FM 1>2 (useB = wanted && the voice an_enter named &&
    fmtag B; wanted in a block the buffers hold but B not written: plays without, `fm_missed++`, fmx= .../missed); m12 is
    msub's twin (the block's amount when it played FM 1>2, else 0; skip path 0). Both routes: a = A on Sub FM's line, b = B
    on FM 1>2's; FM 1>2 alone: a = B. n > 256: `fm_skipped++` only for an oscillator that wants FM itself (OSC2; OSC1's
    B write is not counted: OSC2 counts the skip).
  - `part_post()` (out of line, per part): `an_feed` (B + the part's offset) then the audio crush; `fmtag |= B` after the
    loop. **Deviation (flag):** 4.4 (a) said "render into s, crush, write B": a silent OSC1 is **not** crushed and not counted
    in bits= (nobody hears it; B takes the uncrushed s anyway, OD15). 4.1's "B[i] = inc1 s[i] after the part loop" is per
    part before the crush: OD15's B needs the uncrushed s.
  - **The part loop is instantiated twice** (an always-inline lambda, `Tf<0>` / `Tf<1>`): with FM, Bit Depth or B to feed,
    or today's loop alone. Why (M, an_osc's instructions on P3Identity's mod case, 20 renders): Q5 10,377; Q6 written
    inline 11,259 (+882: the case +2.37 %, over M1's +2 %); with the gate word, fm_setup / feeds / part_post out of line
    and one pointer for B, 11,261 to 11,411 (register allocation, not the added work); two loop instances 10,497 (+120).
    Cost: ~300 B of code (DSP 22,716 -> 23,020 B for that step).
- synth_shared.h / analog.h: comments (fm_missed counts since Q6; m12 plays; FmIn's second term).

Tests (test_analog 126 -> 139: + `Fm12` 13; golden = p3_golden.py, used unchanged):
- **Harness:** FmIn's 28 B layout in FmBench / fm_capture (`B`, `ib`, `dib`, the call's b samples); `fm_golden(..., b, ib,
  dib)` folds the second term after the first (a zero line adds +0 exactly, so every Q5 replay is unchanged); `replay`
  accepts a = A or B and checks b is B on two terms, a otherwise; fm_capture records an_feed's calls (`.feeds`: s at entry,
  B after, from every call site in the DSP) and an_render's (`.plain_calls`: out, n, inc, k); `.osc_divs` covers the hook's
  code (an_osc and fm_setup, where the lines divide). `check_feeds`: B every fed block, bit for bit p3_golden.b_buffer of the
  part's own s (an FM render's samples bit for bit; s off the audio grid somewhere with a Bit Depth: the crush is
  idempotent, so B from the crushed s would pass the golden otherwise), inc = the render's base inc, the setting, dc =
  an_dc(the part's k), parts tiling B, and OSC2's calls reading exactly those samples. `KBench`: OSC1 -> an_feed -> OSC2 on
  the kernel alone for the long coherent renders.
- `test_an_feed_is_the_golden_buffer`: settings 0..3 x dc (0, -0.75, Shapes 100 / 127's) x 3 incs on the golden probe,
  a sweep and random blocks: bit for bit; 2^b + 1 levels; +1 at +1 (4 bits: 1.0, the audio grid 7/8); every golden B
  mutant (audio grid, before the crush, after Level, one sample late) differs on the probe.
- `test_fm12_alone` (free, Shapes (48, 127), (0, 24), ratio 1: through zero), `test_both_routes` (Sub FM 100 / 70 % and FM
  1>2 50 %; OSC1 Full / 8 bits on the pulse (a dc) / 4 bits on the square: B holds inc1 where OSC1's audio is at 7/8, OD15),
  `test_the_fm12_index_ramps_over_the_block` (0, 1000, 1000, 500, 0, 0, 250, 1000: FmIn.a's line alone, FmIn.b's next to Sub
  FM 1000; the ramp down still FM, the block after it none; OSC1 feeds exactly those blocks; m12 the last amount; the hook's
  VDIVs the off count + 1 a line), `test_sync_master_and_slave` (OSC1 MASTER with Sub FM, OSC2 SLAVE with both: bytes =
  the master's, B the master's FM render, both chunked on their lines), `test_a_silent_osc1_still_modulates` (E3 (a): Level
  0 and Level 1 give OSC2 the same FM calls, Level-input blocks and B bit for bit, free and as a silent master; the silent
  OSC1 reaches no Level and bits= 0; a ramp down to 0 feeds through its last block, then the silent path: no render, or
  an_track when synced), `test_the_tag` (n = 512: fm_skipped 3, no feed, m12 0, the next 2N block from I(0); a fresh
  an_enter then OSC2 alone: no FM 1>2 from last block's B, fm_missed 1; another voice's OSC2: fm_missed 1; another voice's
  OSC1 between this voice's two feeds nothing),
  `test_mixed_voices` (OSC1 Wavetable: nothing, output FM 1>2 0's byte for byte; with Sub FM: one term on A; OSC2
  Wavetable: OSC1 feeds nothing), `test_the_amounts_cases_are_the_expected_change` (below): every FM call the golden's.
- 6.3: `test_bessel_sine_to_sine` (kernel: Shape 0 both, OSC1 -> an_feed -> OSC2, 1024 coherent samples; 11:11 at I 1 and
  I(354), 33:11 at 2, 7:21 at 1: within bessel_tol with FM12_EPS_M and the block's float32 walk; B late and I 2 % off fail
  it), `test_no_dc_drift_over_100_blocks` (kernel, 25,600 samples, index 8 through zero), `test_aliasing_after_the_fir_is_below_48k`
  (kernel at 96 kHz + STOCK_FIR + decimation vs the kernel at 48 kHz; the taps found once, in order, in stock's image; the
  golden's figures printed beside the kernel's), `test_cost` (below).
- **Reviewed edits (expected changes, no tolerance loosened):**
  - `P3Identity.test_q1_plays_no_amount`: free, sync, the silent sync master, glide and stock's root voice left it (FM 1>2
    333 plays there since Q6) for `Fm12.test_the_amounts_cases_are_the_expected_change` (the same cases, both rigs: every
    call golden, B OSC1's, only OSC2 under FM, fm= the renders, fmx= 0 / 0); it keeps FM 1>2 333 on the two voices where it
    cannot play (OSC2 Wavetable; OSC1 Wavetable, mixed), BE4.40 byte for byte. `test_glide_dev_is_the_expected_change`:
    its amounts control (FM 1>2 333 on the both-Analog glide case, played as without) is gone for the same reason.
  - `SubFm.test_the_amounts_cases_are_the_expected_change` (Sub FM 700, FM 1>2 333): FM 1>2 plays there too now: the
    replay folds B in, and check_feeds checks B; no B in OSC2 Wavetable / mixed.
  - BitDepth `cases()` and `test_each_oscillator_crushes_at_its_own_depth`: (700, bits, 333) -> **(700, bits, 0)**. With
    FM 1>2 playing, B is OSC1's crushed block (BD1), so the crush also changes OSC2 and "Full with the golden crush injected
    at Level's input" no longer describes the crushed run; Bit Depth with FM 1>2 is Fm12's (test_both_routes, the silent
    OSC1 case).
  - `Props.test_the_silent_paths_zero_the_fm_ramp_memory`: the oscillator that played ends at **(0, 0)** (Q5: (0, 456)):
    m12 ramps down as msub does.

Measured (M):
- Sizes: DSP **23,772 B** (+2,368 on Q5's 21,404: an_render_fm +864 (3,064), an_feed 680, an_osc
  +232 (2,708), fm_setup 424, part_post 104, feeds 68), **P3 has used 14,064 B of its 14,336 B ceiling, 272 B left**
  (within it: no overrun); 12,836 B free in the window. Image **0x1890D0** (unchanged: M4 untouched).
- an_feed is -O2's x4 unroll (680 B); not unrolled it would be ~400 B less at ~2 instructions a sample on every fed block
  (I): the first buy-back if Q7 needs bytes.
- Cost, FM off: `Cost` at n = 256 saw 13,324 (Q5 13,272), mixed 19,968, saw+sync 15,905, mixed+sync 22,292; n = 128 saw
  7,436. P3Identity 50 cases (Q5's 59 less the 9 amounts runs that moved to Fm12), worst **+1.68 %** (mod dev=False 111,747
  vs 109,899; Q5 +1.57 %), total +0.88 %: within M1's +2 %, **0.32 pt left**.
- Cost, FM on (Fm12.test_cost: the device path, note 60, saws, a sine sub): **the full voice (Sub FM 100 % on both, FM 1>2
  100 %) 30,753 = 0.993 of its gate** 1.3 x (13,346 + 3,364 + 2,600 + 4,398 + 118) = 30,974 (Q5 fixed it at 30,906 on its
  off voice 13,294: the same formula on this build's off voice); FM 1>2 alone 18,974; the full voice synced 32,928, with
  OSC1 at 8 bits 35,258, with triangle carriers 37,489 (1.210 x the gate: not gated, as Q5's triangle). Sub FM on both
  27,294 = 0.910 of its gate 29,981 (Q5 27,069: +225, the gate word and fm_setup's call). Worst case an_render_fm 77.7 /
  85.7 a sample (Q5's: the one-term loops are unchanged).
- VDIV by PC range (6.5): inside an_render_fm + fm_slow = the golden's dividing crossings in every replayed call (Q5's and
  Fm12's, the two-term loops included); inside an_feed 0; the hook's own (an_osc + fm_setup) = the off count + 1 a line
  in each FM block (two with both routes).
- 6.3 on the kernel: Bessel (sine -> sine) worst 1.7e-5 a bin against 3.8e-4 (I = 1) / 6.7e-4 (I = 2), J3 within 0.012 dB
  (bound 0.33 dB). DC drift over 100 blocks, index 8 through zero: FM 1>2 from a free OSC1 at Shapes 48 / 88 / 127, Full /
  12 / 8 / 4 bits within 0.025 cycles of the unmodulated travel, the crush's share within 0.39 cent (OD15's B; the audio grid
  on B would give -3.3 / -54 / -1,186 cents on the square); Sub FM by the DC-free Square -0.009 cycles (the raw Square
  -105.6: control). Aliasing after stock's FIR, against the kernel at 48 kHz: FM 1>2 saw carrier / sine OSC1 at 149 cycles
  -25.7 -> -34.3 dB, at 591 (a saw OSC1) -13.7 -> -19.3, Sub FM (298 / 149, index 8) -22.3 -> -31.0: 5.6 to 8.7 dB lower; the
  kernel's figures equal the golden's to 0.1 dB.
- Mutations (scratch p3/q6/mut.py, each built from a copy of mods/m1 into ~/bento/out/m1-p3q6mut/<name>, run against
  Fm12 / SubFm / Props / FmKernel; logs p3/q6/mutlogs/): **all 20 red** (failing (sub)tests). 6.6's: y1 after Level (B scaled by
  the Level, here 0 when silent) 6; B after the quantiser 29 + 2 errors; E3 (a) removed 1 (the silent free OSC1; a silent
  master still feeds through the master path). Q6's: B on the audio grid (OD15 not applied) 120; B without the part's dc 87;
  useB without the tag 1 and without the voice check 1 (test_the_tag); tag B never set 23 + 10; fm_missed not counted 1; m12
  not reset on the skip path 2 (Props); no m12 ramp, FM 1>2 alone 4 + 1, next to Sub FM 6; the fold order ba 7; the second
  term dropped (J b x 0) 17; OSC2 reading A as its second term 17; B written by the first part only 7; feeds() without the
  voice check **green at first** (another voice's OSC1 overwrote B before this voice's OSC2), red (1) once test_the_tag's
  case (4) renders voice 1's OSC1 between voice 0's two; OSC1 not feeding on m12 (the ramp down) 4; a silent OSC1 crushed and
  counted 2; FM 1>2 from a Wavetable OSC1 4.
- Full suite (wsl/test_m1.sh, O=~/bento/out/m1-p3q6) green: test_analog 139 tests (Q5 126 + 13 Fm12), test_native_props 19, test_synth_ui 62, test_keys 10, the
  rest as before (test_drums / test_drum_ui not run: drums omitted on 1.6.8); p3_golden.py self-test 53 tests OK.

Found during Q6 (for Jeff; the golden's author's findings, confirmed on the kernel):
- **OSC1's own Sub FM puts a real DC into B (OD17, open).** The sub is an octave (or two) below OSC1, so OSC1's FM
  spectrum has a sideband at 0 Hz: the correct band-limited y1 has a mean (golden, M: +0.126 for a saw OSC1 at Sub FM 50 %),
  and FM 1>2 turns it into an OSC2 detune of I12 inc1 mean(y1): about +1200 cents at FM 1>2 100 %; over Shapes and notes
  |mean(y1)| reaches 0.37 (-800 cents at FM 1>2 35 %, OSC2's mean increment reversing at 100 %). 4.5 specifies exactly
  this (B is OSC1's audible signal, its own FM included), but it is a far larger detune than the one OD15 removed. The
  6.3 drift check is therefore stated (and tested) for a free OSC1.
- **OD15 removes the top clip's DC, not the rounding's own mean (OD18, open).** At 4 bits a wave whose extreme falls between
  two levels is rounded the same way every cycle (Shape 36 peaks at 0.474 -> 0.5): +16.6 cents at note 60, +30 at 96 (FM 1>2
  100 %, a free OSC1); with OSC1's own Sub FM -139 cents at 4 bits, -3.1 at 8. 12 and 8 bits from a free OSC1 stay within
  0.32 cents (golden sweep). So OD15's "no detune at any depth" holds for the top clip's term; in total only at 12 / 8 bits
  from a free OSC1. The kernel's 4-bit cases at Shapes 48 / 88 / 127 sit on the grid (measured within 0.4 cent).
- **A cost corner, not handled (I):** OSC1 feeds B whenever OSC2 holds FM 1>2 (or m12), also when OSC2 then turns out
  silent and skips (E3 (d)): OSC2's Level is known only in its own render, after OSC1's. With OSC1 silent too, OSC1 renders a
  block nobody uses. Cost only (one OSC1 render and its B, ~4-5 k instructions a voice-block at 2N for a saw, I); a fix would read OSC2's Level
  source in OSC1's gate (stock's PARAM_VALUE on OSC2: its cache store to undo, as wt1_oct). Device item 12 can show it.
- **p3_golden: one weak self-test case, not edited** (no wrong result): `test_aliasing_after_the_fir_is_below_48k`'s Sub FM
  case (1182 / 591 cycles at index 8, n48 8192) hits the increment clamp at 48 kHz (di up to 0.72), so its 48 kHz figure is
  +16 dB, a figure of the clamp, and "> 3 dB below" holds trivially. The module docstring's figures were at n48 16384,
  unclamped. test_analog's kernel check uses 298 / 149 instead.

Q6 review fixes (two reviews; each issue checked; rebuilt clean into ~/bento/out/m1-p3q6, the image byte-identical to the
reviewed one: only comments changed in the DSP and synth_shared.h; sizes and costs above unchanged; test_analog 139 -> 140):
- **Tests for the review's surviving mutants** (review 2's rmut.py, scratch p3/Q6/mut; rerun against the new tests, scratch
  p3/q6/fix): the clamp moved above the second term (`two_clamp_before_b`, major: no two-term call reached |di| 0.45) ->
  new Fm12.test_the_two_term_loop_clamps (FmBench, FREE and SLAVE, Shapes 48 / 100 / 24, A and B +-0.24 whose sum passes
  +-0.45 while inc + I a alone does not; the golden's end phase exactly, samples within fm_tol, VDIVs = its crossings). The
  tag set by any crushed render, not only a feed (`tag_b_any_post`) -> test_the_tag case (5): voice 1's OSC1 at 4 bits
  after an_enter(voice 0), then voice 0's OSC2 alone: no FM 1>2, fmx= missed 1, bits= +1 (control).
- **A silent feeding OSC1 with its own Sub FM** (review 2): test_a_silent_osc1_still_modulates also runs fm = (1000, 0),
  free and synced: OSC1's FM calls (FREE / MASTER, through the normal parts, not E3 (b)) are the golden's and the heard
  case's bit for bit (y, end phase, the master's bytes), fm= the same, no an_track.
- **Props' E3 case** (review 2): it wrote OSC2's m12, so since Q6 OSC1 fed B and the case took no silent-master path. Now
  two E3 cases, each path pinned (6.7's new row). (Before Q6 the case's msub 123 already sent it through E3 (b), not
  an_track; the an_track case is new.)
- **The cost gate** (review 2): Fm12.test_cost also asserts Q5's fixed 30,906 (5).
- **Comments** (both reviews): synth_shared.h's SA_FMBUF called B "OSC1's crushed pre-Level signal"; an_feed's said "one
  rounding each" where clang contracts level * r - down into one VFMA (M: vrintx, vmaxnm, vminnm, vfma in an_feed's
  disassembly), the result still exact as level * r is.
- **Doc** (review 1): 4.1, 4.4 (a), 4.5 and 4.7 point to "Q6 as built" for B's place and grid; 9's cost row has Q6's figures.

### Q7 as built (2026-11-16, working tree on 08e9353; build ~/bento/out/m1-be441; kit flash\bento_enhanced-v4.41)

What was done:
- **Label:** build.py `BASE_SETTINGS["stock_168"]["version"]` 4.40 -> **4.41** (the one place BE4.40 set it, 6adc04f; the boot
  label `DISPLAY` = "BE" + version, written into S.VERSION_STR, which the black box's "firmware" line prints). No test pins
  this build's label: test_diag's `test_visible_version_label` derives it from the manifest, and test_native_props' "4.40"
  is the BE4.40 reference's manifest (REF440), unchanged. No expected-change edit was needed.
- **Image = Q6's + the label (M):** `cmp -l` of ~/bento/out/m1-p3q6/bento1.bin against m1-be441: 2 bytes differ, '0' ->
  '1' in the two copies of the version string; same length. So every Q0..Q6 measurement holds for the kit unchanged.
- **Kit:** wsl/package_m1.sh, as BE4.40's: 01_TEST_FIRMWARE (this image + stock 1.6.8 bento2.bin), 03_RESTORE_STOCK
  (stock 1.6.8, the base checked by its sha256 against the manifest), manifest.json, DEVICE_CHECKLIST.txt (=
  docs/checklist-v4.41.txt), SHA256SUMS.txt regenerated after the checklist was copied (as v4.40's lists it) and
  verified with `sha256sum -c`. **Found:** package_m1.sh took the build dir as `ls -d ~/bento/out/m1-* | tail -1`, i.e. by
  name: here m1-p3q6r2, not m1-be441. It now honours `O=` and otherwise takes the newest by time (test_m1.sh's rule);
  the script lives outside the repo (bento_mods/wsl).
- **Docs:** docs/checklist-v4.41.txt (section 8 made step by step), "Release notes (BE4.41)" below, final tables below.

Final sizes (M, manifests of ~/bento/out/m1-be440 and m1-be441):

| Item | BE4.40 | BE4.41 | Limit / left |
|---|---|---|---|
| Image (bento1.bin) | 0x188DF8 (1,609,208 B) | **0x1890D0 (1,609,936 B)**, +728 | PROVEN_MAX 0x18D334: **16,996 B** left |
| DSP code (window 0x080D0100..0x080D9000, 36,608 B) | 9,708 B | **23,772 B**, +14,064 | P3 ceiling 14,336 B: **272 B** left (no overrun); window **12,836 B** free (>= 9 KB rule holds) |
| Synth UI (M4) | 14,224 B | 14,952 B, +728 | in the image figure |
| M4 code / M7 code / trampolines | 24,424 / 15,368 / 752 B | unchanged | |
| Property template (np_template_n) | 0x300, 0 pads | **0x304, 1 pad** | |
| AnOsc | 172 / 180 B | 176 / 180 B | 4 B spare |
| Patched sites (writes) | 108 | 108 | |

Final costs (M; instructions per voice-block, n = 256 unless marked, hooks only; offline, not device cycles):

| Case | BE4.40 | BE4.41 | Gate / bound |
|---|---|---|---|
| Cost saw / mixed / saw+sync / mixed+sync (FM off) | 13,192 / 19,840 / 15,785 / 22,180 | 13,324 / 19,968 / 15,905 / 22,292 | M1: P3Identity worst **+1.68 %** (mod dev=False), total +0.88 %, bound +2 % |
| Cost saw, n = 128 | 7,304 | 7,436 | |
| Shape 0 / 24 held (FM off, note 60) | a saw there: 13,192 | 13,100 / 11,760 | <= the saw voice (ShapeLow.test_cost) |
| Shape blend (12, 36) / LOW ramp block | | ~23,500 / 30,242 | 1.3 x mixed / 1.5 x the saw > square ramp |
| Sub FM on both (saws, sine sub) | | 27,294 | 0.910 of 29,981 |
| FM 1>2 alone | | 18,974 | |
| Full FM (Sub FM both + FM 1>2) | | **30,753** | **0.993** of 1.3 x (13,346 + 10,480) = 30,974; Q5's fixed 30,906 also asserted |
| Full FM synced / OSC1 at 8 bits | | 32,928 / 35,258 | not gated |
| Sub FM, triangle carriers (Shape 24) | | 33,407 | 1.117 x the Sub FM gate, not gated |
| Full FM, triangle carriers (Shape 24) | | **37,489** | **1.210 x** the full gate: the heaviest measured voice, not gated (device item 12) |
| Bit Depth, per crushed sample | | <= 10.77 | (~2.8 k an oscillator a 2N block, I) |
| an_render_fm worst case per sample (8 % pulse, deep FM) | | 77.7 free / 85.7 master | 2n VDIVs a render |

The cost figures are Q6's measurements (the image is Q6's plus the label, above); test_m1.sh keeps only each suite's
last 15 lines, so it does not re-print them.
Full suite (wsl/test_m1.sh, O=~/bento/out/m1-be441) **green** (M): test_analog 140, test_native_props 19, test_synth_ui 62
(2 skipped), test_keys 10, test_note0 32, test_diag 92, the rest as before; test_drums / test_drum_ui not run (drums omitted
on 1.6.8).

## BE4.42 (2026-11-16, worktree be442 on 435b2f0; build ~/bento/out/m1-be442; kit flash\bento_enhanced-v4.42)

Device feedback (Jeff, BE4.41 on hardware, 2026-11-16): the Analog dial looks great; FM works but is "more moderate than
expected" (the sub as a sine modulator); Bit Depth "isn't enough difference in practice": remove it unless very cheap.
Decisions (Claude's, told to Jeff): (1) remove Bit Depth completely; (2) deeper FM, FD3 restated as
**I = 16 (v/1000)^1.5** for Sub FM and FM 1>2 (50 % = 5.66, 100 % = 16; BE4.41: 2 and 8), the |inc| <= 0.45 clamp kept;
(3) label 4.42, delta checklist docs/checklist-v4.42.txt.

Why this curve (the road not taken): 16 x^2 (the old shape doubled) leaves 50 % at 4 and the bottom half nearly as flat
as BE4.41's, which is what Jeff heard as "moderate"; 16 x (linear) puts I = 1.6 at 10 % and leaves no fine control near
zero. x^1.5 sits between: 25 % = 2 (BE4.41's 50 %), 63 % = 8 (BE4.41's 100 %), so the old range fills the lower two thirds
of the knob and the top third is new depth. A pitch-dependent cap was not added (see "What gets worse").

What changed (M unless marked):
- **Bit Depth removed.** m4_synth.c: Bit Depth's descriptor (0x1A4, "Bit Depth", Full / 12 / 8 / 4, bento_bits) out of
  NP_KEYS (one group of six now, still all or none), its row slot (Analog rows' slot 6 stays empty, so FM 1>2 keeps knob
  8), its labels, the resend list (five keys) and the black box's `bits=`. build.py: KEY_OSC_BITS out of the analog group's
  names and NP_TEMPLATE_KEYS: 0x2FC + 6 = 0x302, padded to **0x304 with two type-0xFF entries** (the count, so the patched
  immediates, are BE4.41's). dsp: an_crush, an_dc and part_post are gone, `ours()` / an_prop no longer take 0x1A4 (stock's
  slot 8 gets it and does nothing), AnProps byte +3 is `spare3` (0), SaFmStats +4 is `r1` (0). DSP_REFUSED_LIBM stays (it
  also guards the new VSQRT, below). stock_168.py keeps KEY_OSC_BITS as a reviewed empty key (test_keys: RETIRED, must
  stay empty).
- **FM 1>2's modulator B** is now OSC1's pre-Level block times its increment, no grid: `an_feed(s, b, n, inc)`,
  b = f32(s inc) (BE4.41's Full branch). OD15 and OD18 are moot (no grid); OD17 stands (see below).
- **Index law** (dsp/analog.cpp an_index): I = f32(f32(v sqrt(v)) K), K = f32(16 / 1000^1.5), the square root VSQRT
  inline (`__builtin_sqrtf` keeps a sqrtf call for errno under -fno-builtin, which DSP_REFUSED_LIBM would refuse). Exact
  2 at 250 and 16 at 1000; within 1.7e-7 relative of 16 (v/1000)^1.5 for every v (two roundings).
- **Golden model (p3_golden.py):** the Q2 / Q2b quantiser (crush, levels, probe, mutants), OD15's crush_mod and
  crush_drift, wave_dc and Voice12's q1 / q2 retired; b_buffer(s, inc1) without a grid (mutants after_level, b_late,
  kin_fm); fm_index / an_index32 on the new law ("square" = BE4.41's law, kept as a mutant; "linear" 16 x); the drift
  self-test also at I = 16; the Bessel case near I = 1 is an_index32(157) = 0.9953. Self-test: 36 tests.
- **Tests (reviewed expected changes, no tolerance loosened):** test_keys (six descriptors; 0x1A4 RETIRED, empty);
  test_native_props (template 0x304 with 2 pads; a project the BE4.41 build itself saves with Bit Depth 8 / 4 opens on
  BE4.42 with every other value, no 0x1A4 property, none pushed to the M7, bento_bits dropped on save); test_synth_ui
  (rows, titles, turns, resend list, black box without bits=); test_analog: BitDepth replaced by BitDepthGone (a BE4.41
  Bit Depth 12 / 8 / 4 / 7 pushed with Sub FM 700 plays byte for byte as 0, instructions included, free, synced and
  through stock's root; the FM stats clean; the libm refusal with an_index's VSQRT in fm_setup; the pulse width the FM
  golden needs, now a float32 statement kernel_dc32 pinned to an_render's plateaus since an_dc is gone), Props (0x1A4
  not ours), P3Identity's AMOUNTS (0, **2**, 333): a stale Bit Depth 2 plays BE4.40 byte for byte, check_feeds / Fm12
  without the grid, the index tests on the new law, the drift test at I = 8 and 16, aliasing adds FM 1>2 at I = 16.
  Fm12.test_cost gates the voice at the gate's own index (every amount 630: I = 7.998) and the 100 % voice against a
  restated budget (Cost, below).

Sizes (M, manifests of m1-be441 and m1-be442):

| Item | BE4.41 | BE4.42 | Limit / left |
|---|---|---|---|
| Image (bento1.bin) | 1,609,936 B | **1,609,816 B**, -120 | PROVEN_MAX 0x18D334: 17,116 B left |
| DSP code (window 36,608 B) | 23,772 B | **22,532 B, -1,240** | P3 ceiling 24,044 B: 1,512 B left; window 14,076 B free |
| Synth UI (M4) | 14,952 B | 14,830 B, -122 | in the image figure |
| M4 / M7 / trampolines | 24,424 / 15,368 / 752 B | unchanged | |
| Property template | 0x304, 1 pad | 0x304, 2 pads | |

Costs (M; instructions per voice-block, 2N = 256, note 60, saws, sine sub; hooks only, offline, not device cycles):

| Case | BE4.41 | BE4.42 | |
|---|---|---|---|
| FM off | 13,346 | 13,314 | -0.24 % (the FM gate's Bit Depth test gone) |
| Full FM at index 8 (BE4.41 100 %; BE4.42 every amount 630) | 30,753 | 30,696 | gate 30,932: 0.992 (BE4.41 0.993) |
| Full FM at 100 % (BE4.41 I = 8, BE4.42 I = 16) | 30,753 | **31,412** | +2.1 %; 1.016 x the gate; gated at 1.03 x it (Cost) |
| Full FM synced, 100 % | 32,928 | 33,988 | +3.2 % |
| Full FM, triangle carriers, 100 % | 37,489 | **38,666** | +3.1 %; 1.25 x the gate: still the heaviest voice |
| Sub FM on both, 100 % (gated) | 27,294 | 27,914 | +2.3 %; 0.932 of its gate 29,939 |
| Sub FM synced / triangle carriers, 100 % | 29,191 / 33,150 | 30,005 / 34,321 | +2.8 % / +3.5 % |
| FM 1>2 alone, 100 % | 18,974 | 19,065 | +0.5 % |
| Full FM with OSC1 at 8 bits (Bit Depth's own cost) | 35,258 | gone | 4,505 a voice-block; <= 11 instructions a crushed sample |
| P3Identity vs BE4.40 (FM off) | worst +1.68 %, total +0.88 % | worst +1.48 %, total +0.65 % | bound +2 % |

The cost of an FM render grows with the index (through zero, more wraps and edges to correct, each a VDIV), so the new
100 % costs about 2-3.5 % more than BE4.41's; at BE4.41's depth BE4.42 is marginally cheaper. **CPU freed by removing Bit
Depth:** about 4.5k instructions a voice-block wherever it was set, 32 a block when not.

**Cost (expected change, put to Jeff):** plan 5's gate (1.3 x (off + the specialised delta), fixed at Q5 for index 8) is
kept, unchanged, on the workload it was derived for (the full voice at index 8, every amount 630: 0.992). Index 16 is a
workload BE4.41 had no knob for, and it measures 1.016 x that gate. Rather than leave it ungated, Fm12.test_cost asserts it
against an explicit restated budget, **FM_DEPTH_ALLOW = 1.03 x the gate** (31,860 at today's off voice), named in the test
and here: the deeper index's extra edge corrections are accepted as the price of the depth Jeff asked for. 1.03 covers the
measured +2.1 % with about 1.4 % to spare, so a regression at the top of the knob is still caught; it is not applied to the
index-8 workload. If the device CPU line (checklist) shows the top of the knob is too heavy, the remedies are a lower
maximum index or a per-note cap, not a wider budget. Review follow-ups (M): Fm12.test_level_input_is_the_fm_render
(Level's input = the FM calls' y, both oscillators, free and synced: BE4.41's crush test without the crush; kills a
post-render nudge and a hidden crush that the voice replays missed), BitDepthGone.test_a_late_bit_depth_key_stores_nothing
(0x1A4 after FM 1>2 stores nothing; kills 0x1A4 back in `ours()`), SubFm.test_the_device_index_is_an_index32_for_a_sweep
(fm_setup's index line = an_index32 bit for bit over 1, 250, 1000 and every 11th amount; kills a reassociated law).
test_analog 133 -> 136.

Clamp and aliasing at the new depth (M, p3_golden, scratch be442/clamp_alias.py; sine modulator, device rate 96 kHz):
- **The |inc| <= 0.45 clamp** engages where inc (1 + I r) > 0.45 (r = modulator / carrier increment). Lowest MIDI note
  that reaches it: FM 1>2, OSC1 = OSC2: **100** at 100 % (BE4.41 111), 116 at 50 %; OSC1 a fifth up 93 (105); an octave up
  88 (100); Sub FM, sub an octave down: **111** (121); two octaves down 121 (never); both routes on OSC2 at 100 %: 93
  (105). Share of samples clamped: FM 1>2 OSC1 = OSC2 at note 108: 55 % at 100 % (BE4.41 0 %), at note 120: 79 % (55 %);
  Sub FM an octave down at note 120: 55 % (0 %). Below those notes nothing is clamped. The clamp is per sample (no
  state), so it flattens the swing rather than breaking the render (the kernel tests run into it: lowest di -0.45).
- **Aliasing** (6.3's metric: off-harmonic / harmonic energy after 96 kHz, stock's FIR and decimation; FM 1>2 sine OSC1 =
  OSC2; old 100 % I = 8 -> new 50 % I = 5.66 / new 100 % I = 16), dB:
  - sine carrier: notes 48..72 below -265 dB at every depth; **note 84: -191 -> -256 / -71**; **note 96: -47 -> -73 /
    -21**. So sine -> sine stays clean to C6 even at 100 %, and at C7 the top of the knob produces audible inharmonic
    components (BE4.41's 100 % was already -47 dB there).
  - saw carrier: note 60 -38.3 -> -39.0 / -35.4; note 72 -36.0 -> -35.7 / -31.3; note 96 -28.1 -> -29.0 / -21.3: 50 % is
    as clean as BE4.41's 100 %; 100 % is 3-7 dB worse.
  - Sub FM (sine sub an octave down) saw carrier: note 60 -41.9 -> -43.7 / -38.8; note 96 -34.9 -> -33.0 / -32.0; sine
    carrier at note 96: -177 -> -239 / -63.
  - Kernel against golden (Fm12.test_aliasing_after_the_fir_is_below_48k, new row): FM 1>2 149 / 149 cycles at I = 16,
    48 kHz -18.9 -> 96 kHz + FIR -30.6 dB (the 96 kHz design still more than 3 dB better than 48 kHz).
- **What gets worse, honestly:** the top third of the knob (above BE4.41's 100 %) aliases audibly on high notes (C7 and
  up with sine carriers, earlier with saws), and from about E7 the deepest swings are clamped; up to BE4.41's depth
  (63 %) nothing changed. **OD17's detune doubles at 100 %** (FM 1>2's I12 is 16, not 8, so OSC1's own Sub FM DC shifts
  OSC2 twice as far; Jeff kept OD17 = (a)). CPU at 100 % is up 2-3.5 % (table). No pitch-dependent cap was added: the
  checklist (section 5) asks Jeff where it becomes unusable; a cap (I limited so inc (1 + I r) <= 0.45, or an index
  roll-off above a note) is the follow-up if he wants one.

Kit: wsl/package_m1.sh with O=~/bento/out/m1-be442 (the script reads only the build dir and the stock images; its
gen_carriers call runs only for a build with the drums, which 1.6.8 leaves out), then docs/checklist-v4.42.txt copied in as
DEVICE_CHECKLIST.txt and SHA256SUMS.txt regenerated and checked with `sha256sum -c`.

## BE4.43 (2026-11-16, worktree be442 on bf177e2; build ~/bento/out/m1-be443; kit flash\bento_enhanced-v4.43)

Jeff's design (2026-11-16, after playing BE4.41): one FM amount per oscillator plus a per-oscillator FM envelope.
- Rows: OSC1 Level, Pitch, Shape, Type, Glide, **Sub FM, FM Atk, FM Dec**; OSC2 Level, Pitch, Shape, Type, Sync 1>2,
  **FM 1>2, FM Atk, FM Dec**. While OSC1 is not Analog, OSC2's slot 5 is **Sub FM** instead of FM 1>2 (Claude's default,
  told to Jeff: OSC2 keeps an FM source in the mixed voice), healed by the tick as the Type changes.
- **OSC2's Sub FM no longer plays while OSC1 is Analog** (its stored value is kept and plays again with a Wavetable OSC1).
- Effective index = the amount's index (FD3) x env, env 0 -> 1 over FM Atk, then 1 -> 0 over FM Dec; FM Dec's top = Inf
  holds 1 (BE4.42's static depth); FM Atk 0 instant; a hard retrigger per voice at note start. Defaults Atk 0 / Dec Inf.

The spec is p3_golden.py's fm_env (written from the design text before the M7 code; its module docstring "BE4.43 FM
envelope"; self-test 44 tests, 8 new, class FmEnv): encoding Atk 0..127, Dec 0..128 with 128 = Inf (the top of the knob
next to the longest decay, monotone, one byte, a default distinct from every time); the time curve n(x) = 1 + round((e^(L x)
- 1) / L) blocks of 2N (2.667 ms), L = 0.0393823365313980, 1 .. 3750 blocks (2.7 ms .. 10.0 s), all 128 values distinct
(the table FM_ENV_BLOCKS is the contract); attack linear in index (e = k / n_a), decay exponential to -60 dB at n_d blocks
then 0; Inf holds 1; the stated float32 law (step f32(1 / n_a), r f32(0.001^(1 / n_d)), attack f32(f32(k) step), decay
f32(e r) a block, stages by a counter); the index line from I(v0) e_k to I(v1) e_(k+1) (index_line's end convention).
**The M7 follows it bit for bit (M, test_analog FmEnv); no golden bug was found; the golden was not edited.**

What changed (M unless marked):
- **Keys.** FM Atk **0x1A4** (BE4.41's Bit Depth key, empty and unclaimed in BE4.42) and FM Dec **0x1A0**: the next of
  Track FX's 19 clean keys after 0x1A2 (docs/plan-synths-168.md 5; 0x1A2 and 0x1A8 stay P4's drum flag / engine, the P3
  keys are taken). Evidence (test_keys, now with both in ALLOW["analog"]): empty after stock's builder, not in stock's
  template, below claim()'s 11, no movw / mov.w / cmp.w immediate in either core, and the one stock range test covering
  them is the reverb's native store (M7 0x08059990, 0x18C..0x1B6, reached only for messages to 0xFFFFFFF1) as for Shape
  and Sync. **One 8-key group**, all or none (OD2's reasoning stands: a clash cannot happen on 1.6.8, a second group only
  hides the page). Track FX's pool goes from 17 to 16 range-class keys. bases: KEY_OSC_BITS renamed KEY_OSC_FMATK,
  KEY_OSC_FMDEC added (stock_168 reviewed notes, stock_161 None, expected_values).
- **M4 (m4_synth.c).** NP_KEYS[8]: FM Atk type 2 0..127 "FM Atk" bento_fmatk, FM Dec type 2 0..128 "FM Dec" bento_fmdec
  (integers as Shape: one value a detent). want_row: slot 5 FM 1>2 on OSC2 while OSC1 is Analog, else Sub FM; slots 6 / 7
  FM Atk / FM Dec on both Analog rows. fm_time_text: the value clamped as the M7 clamps it, then "0 ms" (Atk 0), "Inf"
  (Dec 128), else n(x) x 128 / 48 ms rounded: "2.7 ms" below 10 ms, "83 ms" below 1 s, "1.52 s", "10.0 s" (the M4 holds
  the block table too: synth_shared.h AN_FM_ENV_BLOCKS, both cores). value_texts in the tick now covers slots 6 / 7 too
  (a turn's text is rewritten from the model after the edit, as Shape's). The resend on a Type flip sends seven keys.
- **Template (build.py).** NP_TEMPLATE_KEYS += FM Atk 0, FM Dec 128: 0x2FC + 8 = **0x304, no pad entry** (the count and
  the patched immediates are BE4.42's; the type-0xFF pad hazard of R13 is gone: test_native_props Hazard restated).
- **M7 (dsp/analog.cpp, analog.h).** AnProps: +3 fmatk (BE4.42's spare3), +10 fmdec, +11 envf (1 while Atk != 0 or Dec !=
  Inf, kept by an_prop). AnOsc: note narrowed to 16 bits (AN_NO_NOTE 0xFFFF; stock's note numbers are far below it) to
  free **fmk** (u16: stage << 13 | blocks into it) at +142, and **fme** (f32, the envelope at the end of the last block) in
  the 4 bytes that were left at +176: AnOsc is 180 B, the area is full. an_prop: ours() takes both keys, clamps 0..127 /
  0..128, a fresh Analog state starts at Dec Inf. an_note (slot 2, before stock's): an Analog oscillator's fmk =
  AN_FE_START (the retrigger; every caller of slot 2, so legato notes too: I). fm_env_step (out of line): START gives e0 0
  (1 for Atk 0), ATK e = f32(k) f32(1 / n_a) with exactly 1 at n_a, DEC 1.0 then e r a block (r from a 128-word table,
  ENV_R = the golden's f32 r), END 0 from j = n_d, Inf holds 1; it returns e0 and e1 for the block and keeps e1. It runs
  in feeds() (already out of line), entered when the FM gate word or envf is non-zero, before the silent paths return, so
  the envelope keeps the note's time every block, heard or not, with or without an amount. fm_setup multiplies each line's
  ends: ia = f32(I(v0) e0), end = f32(I(v1) e1), dia as before. Sub FM on OSC2 is v1 = 0 while OSC1 is Analog (its msub
  ramps out once: the only block where both routes, and the two-term loop, still run).
- **Knob turned mid-note (Claude's choice, not in the golden, which holds its values over a note; pinned in FmEnv):**
  Dec from Inf to a time decays from that block as a fresh decay from 1 (Inf holds at its stage's start); Atk turned
  mid-attack rescales the ramp (e = k / n_a with the new n_a, k the blocks since the note); a value turned to Inf jumps
  to 1 at the next block (it ramps over that block's line, 2.7 ms).

Encoding and curve choices (the golden's, restated with the road not taken): 0 = Inf is not monotone; 127 = Inf loses a
time value; a pure exponential 2.7 ms .. 10 s collapses to 100 distinct block counts, the offset exponential keeps all
128 at about 4 % a step at the top. Linear attack in index: the index already sets the brightness (about I + 1
sidebands), so a linear rise is an even swell, cheap, and lands exactly on 1; an exponential attack (the analog RC
shape) would race to most of the depth early. Exponential decay is the classic: a Dec value names the time to -60 dB.

Cost (per oscillator per block; nothing per sample): a counter, a VDIV in the attack (1 / n_a, the same value as the
spec's precomputed step; no room in the area to keep it), one VMUL in the decay, the line's two VMULs in fm_setup. State
8 B per oscillator (fmk, fme, and the flag byte), tables 768 B in the DSP (the 128 u16 block table and the 128 f32 decay
factors) and the 256 B block table in the M4.

Sizes (M, manifests of m1-be442 and m1-be443):

| Item | BE4.42 | BE4.43 | Limit / left |
|---|---|---|---|
| Image (bento1.bin) | 1,609,816 B | **1,610,800 B**, +984 | PROVEN_MAX 0x18D334: 16,132 B left |
| DSP code (window 36,608 B) | 22,532 B | **23,788 B, +1,256** (768 of it the two tables) | P3 ceiling 24,044 B: **256 B left**; window 12,820 B free |
| Synth UI (M4) | 14,830 B | 15,818 B, +988 (256 the block table) | in the image figure |
| M4 / M7 / trampolines | 24,424 / 15,368 / 752 B | unchanged | |
| Property template | 0x304, 2 pads | 0x304, no pad | |

CPU (M; instructions, the hooks only, offline, not device cycles):

| Case | BE4.42 | BE4.43 | |
|---|---|---|---|
| FM-off voice, envelope unset (FmEnv / P3Identity: per oscillator-block) | | +5 to +9 an oscillator-block (P3Identity cases vs BE4.42: +0.10 to +0.20 %) | the gate's one byte OR; P3Identity vs BE4.40 worst 1.58 % (BE4.42 1.48 %), bound 2 % |
| FM-off voice, envelope set on both (Atk 20 / Dec 60) | | 13,331 -> 13,480 (+149, 1.1 %) a voice-block | fm_env_step 38 instructions a call |
| Full FM voice at 100 % (Sub FM OSC1, FM 1>2 OSC2; BE4.42 also Sub FM on OSC2) | 31,412 | **28,914** | OSC2's Sub FM term is gone; 0.93 x plan 5's gate |
| Full FM at index 8 (every amount 630) | 30,696 | 28,593 | gate 30,954: 0.924 |
| The same full voice with the envelope set on both (decaying) | | 28,995 -> 28,755 (-240) | a decaying index costs fewer edge corrections |
| Sub FM voice "on both" (SubFm.test_cost; OSC2's Sub FM now plays nothing) | 27,914 | 23,290 | |
| Full FM, triangle carriers, 100 % | 38,666 | 35,294 | still the heaviest voice |

A decayed envelope (env 0) still renders under FM: its CPU is the FM voice's whatever the depth. Skipping the FM render
at a zero line (an_render instead) would save that cost on plucks; it is not done: an_render and an_render_fm at index 0
are not proven to be the same samples and state, so it needs its own identity test. A follow-up if the device CPU line
asks for it.

Tests (reviewed expected changes, each marked BE4.43 in its docstring; no tolerance loosened):
- p3_golden.py: the envelope spec and its self-tests (above), committed with this.
- test_keys: ALLOW["analog"] eight keys, RETIRED empty, the registry test of the eight descriptors, a taken FM Dec takes
  the whole group, the range test covers FM Atk / Dec in the reverb's store only.
- test_native_props: OURS / V1 / V2 eight values; template 0x304 with no pad; out-of-range FM Atk / Dec load raw; a
  BE4.41 project with Bit Depth now opens with FM Atk 0 / Dec 128 (bento_bits still not read: 0x1A4's token is
  bento_fmatk; the push sends 0x1A4 only as FM Atk's 0); **new** test_a_be442_project_opens_with_the_envelope_at_its_defaults
  (the v4.42 kit's image saves, this build loads: six values, Atk 0 / Dec 128, pushed, saved as bento_fmatk="0"
  bento_fmdec="128"); BE4.40's rollback drops the two new tokens too; Hazard: type 0xFF is not creatable any more.
- test_synth_ui: the rows (AN_ROW1 / AN_ROW2 / AN_ROW2_WT), titles and values ("0 ms", "Inf"), the slot swap on OSC1's
  Type, the turns (FM Atk 0..127, FM Dec 0..128), the seven-key resend; **new** test_the_fm_envelope_shows_its_times (the
  spec's sample points, clamped file values, a turn re-texted by the tick).
- test_analog: Props (both keys stored and clamped, envf, the fresh state, the M7 input path with eight keys);
  P3Identity masks the envelope's own bytes (fmk where BE4.40's note had its high half, fme, fmdec, envf) and pushes the
  template's eight keys (its 0x1A4 value is FM Atk now: AMOUNTS' 2 is FM Atk 2, played as BE4.40 where no FM plays);
  BitDepthGone's first two tests restated (the envelope's defaults and clamped defaults play byte for byte as no
  envelope push, fm_env_step never entered; 0x1A4 late changes FM Atk alone); SubCopy / SubFm / Fm12: every case with
  OSC2's Sub FM next to an Analog OSC1 now expects it silent (no fill, no term, the output OSC2-Sub-FM-0's byte for byte
  where checked), test_both_routes now drives the switch (OSC1 Wavetable -> Analog mid-note: the one two-term block);
  **new class FmEnv** (7 tests): the tables in both cores are the golden's; OSC1's lines (Atk 5 / Dec 8 with a full decay
  and back-to-back retriggers, Atk 0 / Dec 0, Atk 1 / Inf, a moving amount) = fm_env_lines f32 bit for bit, and every
  golden mutant differs from them; each oscillator its own envelope (OSC1 Sub FM, OSC2 FM 1>2, OSC2's Sub FM silent), the
  mixed voice free and synced; the envelope keeps time without an amount and while silent; a knob turned mid-note; each
  voice retriggers its own; the cost. test_analog 136 -> 143 tests.

Release notes (BE4.43):
- **One FM amount per oscillator, and its FM envelope.** OSC1: Sub FM; OSC2: FM 1>2 (Sub FM while OSC1 is Wavetable).
  Each oscillator has FM Atk (0 ms .. 10 s, linear in depth) and FM Dec (2.7 ms .. 10 s to -60 dB, exponential, or Inf =
  hold), restarted at every note. Defaults Atk 0 / Inf: BE4.42's static depth.
- **Behaviour change:** OSC2's Sub FM no longer plays while OSC1 is Analog. A BE4.42 (or BE4.41) project that had it
  loses that Sub FM (its value is kept, and plays with a Wavetable OSC1). Everything else in a BE4.42 project sounds the
  same. A BE4.41 project's Bit Depth still is not read (FM Atk keeps 0).
- Rollback: BE4.42 and BE4.40 open a BE4.43 project and drop bento_fmatk / bento_fmdec on their next save.
- Known, open: the retrigger is hard (a new note restarts at 0 whatever the envelope was doing; legato notes too, I);
  a decayed envelope still costs the FM's CPU.

Open point for Jeff (new): a note that retriggers mid-envelope steps back to 0 at its first block (hard reset, the spec).
If you would rather have the attack resume from the current level (legato-style), that is a spec change in fm_env (the
golden) and fm_env_step, not a tolerance question. Same for whether a Glide (legato) note should retrigger at all.

Kit: wsl/package_m1.sh adapted (a scratch copy with O=~/bento/out/m1-be443; the script reads only the build dir and the
stock images, its gen_carriers call runs only for a drums build), then docs/checklist-v4.43.txt copied in as
DEVICE_CHECKLIST.txt and SHA256SUMS.txt regenerated and checked with `sha256sum -c`.

## Release notes (BE4.41)

New (Analog oscillators of a Wavetable track; plan 2, 4):
- **Sub FM** on OSC1 and OSC2 (0..100 %, squared to index 8): the track's sub oscillator FMs the oscillator, through
  zero. The sub's Level does not matter (it modulates at Level 0). A Square sub modulates without its DC, so its Pulse
  Width changes the timbre, not the pitch centre.
- **FM 1>2** on OSC2 (0..100 %), only while OSC1 is Analog (hidden otherwise): OSC1 FMs OSC2, also at OSC1 Level 0.
  OSC1's own Sub FM and its Bit Depth are part of the modulator (B is written on a grid with no top-clip DC, OD15).
- **Bit Depth** Full / 12 / 8 / 4 per oscillator (not the sub), before Level; a pulse is crushed about its own DC (OD12).
- **Shape 0..47**: sine (0..2), sine > triangle, triangle (22..26), triangle > saw, saw (45..48); 48..127 as BE4.40. The
  dial has five glyphs. Shape modulation below 48 now sweeps into triangle and sine.
- Black box: `fm=`, `bits=`, `fmx=<skipped>/<missed>` on the ANALOG line.

Behaviour changes (no migration, as decided):
- **Glide takes its set time** (50 % = 1 s): every glide on the device is twice as long as on BE4.40 (OD1, Q0).
- **Shape < 45 plays sine or triangle**: a BE4.40 project with Shape below 45 (BE4.40 played saw there) changes sound.
- **Rollback to BE4.40** opens a BE4.41 project (its four values kept; no FM, no crush, Shape < 45 as saw) and **drops
  Sub FM, Bit Depth and FM 1>2 on its next save**. Stock 1.6.8 opens it as plain Wavetable and drops every bento_* value
  on its save.
- The property template has a pad entry (type 0xFF) for the first time; no reader path creates one (Q1, tested).

Known, open (not changed in this kit):
- **OD17:** OSC1's own Sub FM gives B a DC, so FM 1>2 then shifts OSC2's pitch (up to about an octave at 100 %), moving
  with OSC1's Sub FM and Shape. The checklist (section 4) asks for a judgement.
- **OD18:** at 4 bits, a Shape whose peak falls between two levels gives OSC2 a small detune under FM 1>2 (+17 cents at
  Shape 36, note 60; -139 with OSC1's own Sub FM).
- OSC1 renders for OSC2's FM 1>2 even when OSC2 turns out silent (a cost corner, Q6).
- CPU on the device is unmeasured: full FM with triangle carriers is 2.8 x a BE4.40 saw voice offline (37.5 k / 13.2 k).

---

## 8. Device checklist (BE4.41; BE4.40's own checklist runs first, as it is not flashed yet; made step by step in docs/checklist-v4.41.txt)
1. Boot: the black box shows `keys=ok` and no `TAKEN`; the FM stats line prints.
2. Rows: the OSC1/OSC2 Analog rows show Sub FM and Bit Depth in slots 5/6, and FM 1>2 in OSC2's slot 7 only while OSC1
   is Analog. Flip OSC1 to Wavetable: slot 7 disappears within a tick.
3. Turn each new control and watch stock's neighbouring parameters. None may move: Velocity..Offset (0x181..0x18A); the
   selected track and cell (0x131, 0x133); the reverb parameters (0x18C..0x1B6 holds 0x1A4, on handle 0xFFFFFFF1).
4. Sub FM 0 -> 100 % on OSC1 with a sine sub: audible through-zero FM, no clicks while turning. At 0 the sound equals
   BE4.40 (A/B against a project saved with BE4.40).
5. Sub FM with a Square sub at PW 30 %: the pitch centre does not drift as Sub FM rises (OD10).
6. FM 1>2 with OSC1 at Level 0: OSC2 is still FM'd.
7. Bit Depth 12/8/4 on each oscillator; the sub is unaffected. With FM 1>2 on, OSC1's crush changes OSC2's timbre (BD1).
   Shape 100..127 at 12 bits sounds as Full, with no level drop (OD12, Q2b); a Shape LFO over 90..127 at 4 bits steps
   without clicks beyond the crush's own grit (the dc moves part by part).
8. Sync 1>2 on plus Sub FM on OSC1: the slave follows the FM'd master. Also mixed sync (OSC1 Wavetable) with Sub FM on
   OSC2.
9. Shape sweep 0..127 by knob and by modulation (Position routed): sine, triangle, saw at 48, square, pulse. The dial
   glyphs light. Judge whether the glyphs read apart (Q3b: the sine one smooth cycle from the middle, the triangle a
   two-cycle zigzag of straight flanks; the closest pair is now the triangle and the saw, both zigzags, the saw's falls
   vertical), and listen for clicks while an LFO sweeps 5..45 (Q3's AN_RAMP_LOW, OD13).
10. Save, reload, copy/paste a track, save and load a preset **file**: all three values are kept. Stock 1.6.8 opens the
    project. BE4.40 opens it too, and drops the three values on its next save (expected).
11. Glide 50 % = 1 s (stopwatch or tuner; Q0 shipped). BE4.40 took 0.5 s for the same setting.
12. CPU: an 8-voice chord with full FM, then the same on several tracks. Record DSP %, `peak`, `voices_peak`, `fm=` and
    `bits=`, with no `OVER`. Also (Q3, Q3b) an 8-voice chord at Shape 0, 12 and 24, FM off, on one and then several
    tracks (Q3b: around note 60 Shape 0 / 24 cost a saw voice or less, the blend at 12 ~1.77 x), the same chord in the
    upper register (Q3b: the plateaus rise with pitch, ~1.3 x the saw at note 96, ~2 x near 119, LOW's cost above 120),
    and with an LFO on Position (a LOW ramp, 1.32 x): peak, no `OVER`. Q5: the same chord with Sub FM 100 % on both oscillators
    (a sine sub; offline 2.04 x the saw voice at note 60, synced 2.18 x), then at Shape 0 / 24 (the FM loop's LOW form
    costs ~11 instructions a sample more than its saw).
13. An old BE4.40 project with Shape < 45 now plays sine or triangle (expected; no migration).

---

## 9. Risks

| Risk | Effect | Mitigation | Label |
|---|---|---|---|
| FM costs about 2x the old plan at 2N | +9-10 % instructions per root of 10 full-FM voices; multiplied across tracks; OVER on dense patches; **Q5 (M): the Sub FM voice 27,069 = 2.04 x the saw voice, within its gate (29,913); with triangle carriers 33,407 = 1.117 x that gate (the gate is the saw's: not gated)**; **Q6 (M): the full voice (Sub FM on both, FM 1>2) 30,753 = 0.993 x its gate (30,974; Q5's fixed 30,906 also
asserted), with triangle carriers 37,489 = 1.21 x (not gated)**; FM off worst +1.68 % (Q5 +1.57 %): **0.32 pt of M1's
+2 % left for Q7** | gate measured in Q5; specialise the hot case (saw carrier, sine sub) only if it fails; device item 12 | I |
| Device CPI unknown (flash execution) | instruction counts understate cycles | device item 12 records `peak` with `fm=` | I |
| The generic loop hoists a division into every sample | ~257 VDIVs per render | VDIV count by PC range (6.5) on every build | I |
| DSP code larger than estimated | squeezes the drums' room; Q3b left 8,424 B of the 14 KB for Q4 to Q6 (estimate 6.5-9.5 KB); **Q4 took 832 B (the copy at minsize): 7,592 B left for Q5 / Q6** (estimate 4.5-8 KB); **Q5 took 4,952 B: 2,640 B left for Q6** (M); **Q6 took 2,368 B: 272 B left** (M; an_feed unrolled is ~400 B to buy back; OD17 (b) would overrun, allowed while the window keeps 9 KB: 12.8 KB now) | 14 KB ceiling measured at Q3 and Q5; the plateau path (2.6 KB, Q3b) is the first to trade; the copy's Sine at -O2 (+544 B, -8 % its cost) is the first buy-back if Q5 lands small | M / I |
| Square sub DC detunes the carrier | pitch drifts as Sub FM rises | DC-free copy (OD10); golden mean-frequency case | S/I |
| sub_copy disturbs the sub via PARAM_VALUE's cache | the copy or the sub's render is no longer exact | save/restore around the calls; sub-object identity test | S |
| Stale A or B between voices | FM from another voice | fmtag cleared right after `r->voice`; `Run.voice` check; mutations | S |
| E3 cases missed | silent FM 1>2, or wrong sync wraps from a silent master | E3 (a)/(b) tests incl. the ramp-down; mutations | S |
| Backward wraps and slave resets | resets that sound wrong under deep through-zero sync; double edges at di < 0 | golden model; the one-edge reset rule; OD5 | I |
| The Shape remap changes J4 and old projects below 45 | an audible change | checklist item 13; no migration (decided); 6.7 edits | S |
| LOW regime BLAMP / sinq quality | aliasing on bright blends; Bessel test too strict | THD measured first; spectral checks | I |
| LOW regime cost (Q3, M; Q3b) | Q3: every sine / triangle patch cost ~1.77 x a saw voice with FM off. Q3b: the plateaus at or below the saw voice up to about note 72 (sine) / 91 (triangle), rising to ~2.2 x it at note 119, LOW's cost above inc 0.09 (note ~120), never above Q3's LOW below that except a plateau slave under a master above ~24 kHz (up to 1.29 x LOW); the blends (Sin>Tri, Tri>Saw) and LOW ramps still ~1.77 x / 1.32 x; dense blend chords across tracks, and FM on top in Q5, near OVER | device item 12's Shape 0 / 12 / 24 chords; if a blend fails, specialise ST / TS (~2 KB more, against the 8.4 KB left of the 14 KB ceiling) | M / I |
| The type-0xFF pad entry is reachable | a one-property object from a crafted file | reader-type scan and injected-element load test (Q1) | S |
| Rollback to BE4.40 | the 3 values silently dropped on its next save | BE4.40-opens-P3 test; release note; checklist item 10 | S/I |
| M7 key-flag path 0x08059960 never run | a new key mirrored to handle 0xFFFFFFFE | static reading (only 0x05/0x27/0x31 flagged); runtime test (OD7) | S |
| Key arithmetic near 0x18B or 0x132 that the scans cannot see | a stock parameter moves | device item 3 | I |
| Drums (BE4.50+) want SA_UI or SA+0x100 | buffer clash | SA_FMBUF/SA_FMSTATS named and static_asserted; drums use 0x2400..0x4600 | S |
| resend back-pressure | a brief view-tick stall while the M7 is slow | the ring blocks, it never drops (S); 6 posts only on a Type flip | I |

---

## Review
Two review passes checked the draft. Every issue was checked against the code or the reviewer's measurement. I re-read
these claims directly in the code: SaStats is full at 64 B (synth_shared.h:62-78, analog.cpp:104); PARAM_VALUE caches at
+0x3C (analog.cpp:82, wt1_oct :311-319); the slave branch is gated on `k.inc > 0.f` (analog_kernel.cpp:93);
test_analog.py:1239-1240 and :935-936; `np_ok` requires `keys == 1` and the black box prints `ok`/`TAKEN`
(m4_synth.c:422, :1320); `an_enter` sets `r->voice` at :279 and returns at :269 and :280; SA+0x100..0x200 is undefined.
I did not re-run the reviewers' compile of `rintf` or re-read the SET_PROPERTY ring disassembly. Both are accepted at the
reviewers' labels (M and S). No issue was dropped. One was applied in a different direction than its fix suggested
(OD2, below).

---

## Decisions (Jeff, 2026-11-15)
- **BE4.42** (2026-11-16, after Jeff's BE4.41 device run; Claude's decisions, told to Jeff): Bit Depth removed (OD11,
  OD12, OD15, OD18 moot from BE4.42 on); FD3 restated as I = 16 (v/1000)^1.5; section "BE4.42".
- **OD17 = (a), OD18 = (a)** (Jeff, 2026-11-16: "FM sound design has tuning quirks by nature"): B stays OSC1's
  signal with its own FM and its rounding; FM 1>2 may shift OSC2's pitch as OSC1's Sub FM, Shape or Bit Depth move. No
  high-pass on B. Release note, checklist section 4 records it as expected behaviour.
- **OD16 = (a)** (recorded in Q6, 2026-11-16): an oscillator heard again ramps its FM index in from 0 (msub / m12 = 0 after
  the silent skip), as built in Q5. Claude's recommendation, Jeff told, no objection yet.
- **OD15 = (b)** (Jeff, 2026-11-16): FM 1>2's modulator B is written from the crush with its top clamp at S, not
  S - 1, so B carries only unbiased rounding (no grid DC, no OSC2 detune at any depth). The audio keeps OD11 (b)'s grid.
  FM 1>2 path only.
- **OD1 = (a):** the glide fix ships in BE4.41 as sprint Q0. Release note: glides now take the set time (twice BE4.40's).
- **OD10 = (a):** Sub FM uses a DC-free Square (subtract 1 - 2*pw); exactness tested up to that constant.
- **OD11 = (b), not the recommendation:** true b-bit two's-complement grid. `q = clamp(rint(x*S), -S, S-1) / S`,
  S = 2^(b-1): exactly 2^b levels (4-bit: -8..+7 in steps of 1/8), the top clips asymmetrically. The golden model
  and the 6.3 crush cases use this grid; the BLEP overshoot above 1 clips at (S-1)/S. (Q2b, M: there is no overshoot;
  the +1 high reaches the top.)
- **OD5:** any master wrap (either direction) resets the slave; a backward slave resets to p = 1 (one edge).
- **OD12 = (b)** (2026-11-15, Q2's finding): crush `s + dc`, then subtract dc, so the DC-free pulse sits on the grid's
  range; B stays DC-free; dc follows a Shape ramp part by part. Q2's clip-pinning tests are retired as expected changes.
  Built in Q2b ("Q2b as built"). The grid's own top-clip DC (-f_hi / S, every depth) is not covered by it: OD15.
- **OD13 = (a), as built in Q3:** `AN_RAMP_LOW` = 16-frame steps for a ramp with either end below the saw.
- **OD14:** the SLAVE reset's slope BLAMP in LOW stays as built. The one-cycle sine / triangle dial glyphs are **redone
  in Q3b** (not taken as built).
- Taken as recommended (not asked): OD2 one 7-key group, OD3 hide, OD4 as recommended, OD6 plateaus
  sine 0..2 / triangle 22..26 / saw 45..48, OD7 add the test, OD8 one kit BE4.41, OD9 keep SA_LAYOUT 2, CPU gate
  1.3 x the specialised voice at 2N.

## Open decisions for Jeff (new ones only)

| # | Question | Options | Recommendation |
|---|---|---|---|
| OD1 | Glide half-time fix (F0 bug 2) | (a) in BE4.41 as sprint Q0; (b) a separate BE4.40a; (c) later | **(a)**. It is one line plus a dev-rig test, and it makes FM during a glide correct. Every glide on the device becomes twice as long as in BE4.40 (the set time is then right), so the release notes must say so. |
| OD2 | Key-clash granularity | (a) one 7-key all-or-none group; (b) two groups (Analog 4, FM 3) | **(a) one group**. The review showed (b) buys little. A clash cannot occur at runtime on 1.6.8: the registry is deterministic and test_keys measures all 7 keys empty at build time (M). Even with (b), the gating only hides the page: the template still gives every type-6 object the keys, and the M7 still plays file values (S: build.py:1717-1736, analog.cpp:172-197). And (b) needs `np_ok` and `an_print` reworked, because both treat anything but `keys == 1` as fully off. |
| OD3 | FM 1>2 while OSC1 is Wavetable | hide slot 7; show `--` | **Hide**. It matches BE4.40's pattern and needs no value-text hook. |
| OD4 | Out-of-range file values (hand-edited files only) | Bit Depth: play `4` (matches the widget) or Full. Sync: make the M7 match the widget (`v == 0 ? 0 : 1`) or keep `v == 1`. Sub FM / FM 1>2 (type 10): accept that the widget may show e.g. `500.0%` while the sound clamps at 100 % (as Glide does today), or add clamped value texts for slots 5..7 | **Match the widget for the lists** (Bit Depth -> 4, Sync -> On, one line; test 6.7 changes). **Accept the type-10 mismatch** like Glide, pinned by the 6.1 wvalue tests: a value-text hook for slots 5..7 costs code for files nobody writes by hand. |
| OD5 | Through-zero sync semantics | Backward wrap of the master: resets the slave (yes) or not (forward only). Slave reset when di < 0: to p = 1 (boundary in the travel direction, one edge) or to p = 0 (an immediate second wrap, two BLEPs) | **Yes, and p = 1**. Any cycle boundary resets (symmetric, simpler MASTER loop). Resetting to the boundary in the direction of travel gives one clean edge, where p = 0 makes a double edge in one sample. The golden model can render both if you want to hear them. |
| OD6 | Plateau widths at the new anchors | none; sine 0..2, triangle 22..26, saw 45..48 | **The recommended set**: the knob can land on the pure shapes, and the one-sided saw plateau keeps 48..127 identical. |
| OD7 | Runtime check of the M7 key-flag path 0x08059960 | emulated test in Q1; rely on the static reading | **Add the test**: cheap, and it covers the one unrun path that sees all seven keys. |
| OD8 | Kit label | BE4.41 for all of P3; or BE4.41 (Q0-Q3) + BE4.42 (FM) | **One kit, BE4.41**. Each sprint is committed, so it can still be split if FM slips. |
| OD9 | SA_LAYOUT with the new SA_FMSTATS line | keep 2; bump to 3 | **Keep 2**. The change only adds a region at an undefined offset, and both cores ship in one image (I). Bump it only if you want a mismatched-core boot to say so explicitly. |
| OD10 | DC of the Square sub as an FM modulator | (a) DC-free (subtract 1 - 2*pw, the prototype's `dcfree`); (b) raw (bit-exact copy, the carrier detunes with PW and depth: +3.2*inc_sub at I = 8, PW 30 %) | **(a) DC-free**. Turning Sub FM up should not change the pitch centre, and PW then shapes the timbre only. Exactness is still tested, up to the known DC constant. (b) is the analog-linear-FM behaviour, if you want it as a feature. |
| OD11 | Bit Depth grid | (a) mid-tread, S = 2^(b-1): 2^b + 1 levels, symmetric, no clip; (b) true b-bit two's complement, clamped to [-S, S-1] | **(a)** (superseded: Jeff chose (b), Decisions above; 4.7). It has no DC and no top clip, and at 12/8 bits the extra level is inaudible. At 4 bits it is 17 levels instead of 16, so the label is nominal. Choose (b) if you want a stricter "4-bit" grit with an asymmetric top clip. |
| OD12 | Bit Depth's clip of the DC-free pulse (Q2, 4.7) | Every Bit Depth, 12 bits too, clips the pulse's high (2 - 2w, 1.84 at Shape 127) for Shape > 88: 4.7 to 6 dB less peak to peak, and no longer DC-free, so under FM 1>2 (B = crushed s, BD1) OSC1 detunes OSC2 by I * inc1 * mean(q) (about -0.4 to -0.8 x inc1 at I = 8). (a) accept the clip as grit; then Q6 either subtracts B's mean (known only per block, or from the clipped levels) or accepts the detune as a feature. (b) crush `s + dc`, then subtract dc (the pulse's own range sits on the grid's [-1, 1)): B stays nearly DC-free and 12 / 8 bits nearly transparent (the +1 high still loses one step: 0.9995 at 12 bits, 0.875 at 4), but the dc must follow a Shape ramp part by part (the kernel knows it per part) and the output is offset from the grid by dc. (c) no clamp above S - 1: more than 2^b levels, so it is OD11 (a) again, not a variant | **Decided: (b)** (Decisions; built in Q2b). It keeps OD11 (b)'s grid for the signal the grid was meant for, keeps B DC-free as OD10 chose for the sub, and costs one add and one subtract per sample (the dc per part is already computed). (a) with a mean subtracted in Q6 is the fallback. Q2 ships (a) as built, pinned by its tests; the answer is needed before Q6 writes B. |
| OD13 | Shape ramp step below the saw (Q3, deviation from 4.6) | (a) `AN_RAMP_LOW` = 16 frames for a ramp with either end below the saw (k0 < 0 or k1 < 0), AN_RAMP = 32 elsewhere; (b) 4.6 as written, AN_RAMP = 32 for every ramp | **Decided: (a)**, as built (Decisions). At 32 frames 6.4's criterion fails: per part edge 1.02 / 1.08 / 0.95 x the saw > square ramp's click at LFO centres 24 / 28 / 46 (the LOW map moves k twice as fast a Shape step). At 16, per block (6.4's metric) 0.0083 / 0.0089 / 0.0135 against saw > square's 0.0163. A slope BLAMP in an_reshape changed nothing measurable, so it is not the fix. Cost: a LOW ramp block 29,554 instructions against 22,859 for the saw > square ramp (1.29 x); ramps at Shape >= 45 are untouched (identity). (b) is one line in an_osc plus the expected part count in ShapeLow; 6.4 would then need a looser criterion for the LOW map. |
| OD14 | Taken as built in Q3 (beyond 4.6, listed so you see them) | the SLAVE reset's slope BLAMP in LOW; the sine and triangle dial glyphs drawn one cycle each (the others two) | **Decided** (Decisions): the reset BLAMP kept as built; **the glyphs redone in Q3b**. (Recommendation was: keep both.) The reset BLAMP is the value BLEP's missing half: without it a synced triangle is off the exact band-limited wave by up to 0.12, with it ~1e-4 (both reviews measured it against a brute-force reference); one sinq per reset, nothing per sample. The glyphs: at two cycles the sine and triangle drew alike (snapshot); one cycle gives the sine a round top. Device item 9 judges it; reverting is one expression in `glyph()`. **Q3b:** redone (the sine one cycle from the middle, the triangle two cycles of straight flanks; "Q3b as built"). |
| OD15 | The grid's own DC under FM 1>2 (Q2b, 4.7; needed before Q6) | OD11 (b)'s top at (S-1)/S takes one step off any +1 high (the square, and the pulse after OD12), so mean(q) = -f_hi / S and B = inc1 q detunes OSC2 by I * inc1 * mean(q): at I = 8, OSC1 = OSC2, a square OSC1 gives about -3 / -55 / -1200 cents at 12 / 8 / 4 bits (I). (a) accept it as part of the grit; (b) Q6 writes B from the crush with its top clamp at S instead of S - 1 (the audio keeps OD11 (b); B gets the +1 level back, so its error is unbiased rounding; about one compare and one add per sample, in the FM 1>2 path only); (c) subtract a known -f_hi / S from B (f_hi is not known exactly per block: edges, sync, ramps); (d) a half-step (mid-rise) grid, which revisits OD11 for the audio too and puts a +1 / 2S offset on a zero input; (e) B from the uncrushed s (revisits BD1: the crush no longer colours FM 1>2) | **(b)**. It removes the detune at every depth for the cost of one extra clamp in the path that already writes B, and leaves Jeff's audio grid untouched. (e) is cheaper (zero) but loses the crushed modulator BD1 chose; (a) is defensible at 12 bits (-3 cents) but not at 8 (half a semitone). Close call between (b) and (e). |
| OD16 | An oscillator heard again (Q5, 4.4) ramps its Sub FM index in from 0 | 4.4 said: after the silent skip the amount starts at its set value, no ramp. As built, msub = 0 there (and after reset, a Type change, or a block with no FM), and 0 is a real amount, so the first block heard ramps I from 0 to the set amount over 2N (2.7 ms), as a project's first block does. (a) keep it (as built, no change); (b) a "no ramp" mark so the first heard block plays the set amount at once: a flag byte in AnOsc's 4 spare bytes (176 / 180), set on the skip path and cleared by the next render, Q1's msub zeros unchanged (a few instructions in the FM gate; the msub = 0xFFFF sentinel instead would change Q1's pinned zeros) | **(a)**. The oscillator returns from Level 0 through stock's Level smoother, which also ramps from 0 over its first block (S: an_osc's silent is Level 0 with the smoother not moving), so the index ramp sits under a ramp that already hides it (I: not separately audible); (b) costs code in the 2.6 KB Q6 has left and a state rule more to test, for a difference inside that Level ramp. Choose (b) if you want a struck FM attack on an oscillator brought back by Level automation to be at full index from its first sample. **Recorded (a)** in Decisions (Q6; m12 follows the same rule). |
| OD17 | OSC1's own Sub FM puts a DC into B (Q6, golden finding, 4.5) | The sub sits an octave (or two) below OSC1, so OSC1's FM spectrum has a sideband at 0 Hz and its correct, band-limited block has a mean (M: +0.126 for a saw at Sub FM 50 %; up to 0.37 over Shapes and notes). FM 1>2 turns it into an OSC2 detune of I12 inc1 mean(y1): about +1200 cents at FM 1>2 100 % with OSC1's Sub FM at 50 %, -800 cents at 35 % elsewhere, OSC2's mean increment reversing at 100 %; it moves as OSC1's Sub FM or Shape moves. (a) accept: 4.5 as written (B is OSC1's audible signal, its own FM included), the FM character; release note. (b) AC-couple B: a one-pole high-pass (a few Hz) on B, its state in AnOsc's 4 spare bytes, ~3 instructions a sample in an_feed; B is then no longer OSC1's crushed block bit for bit (the golden and check_feeds restated); about 100-200 B of code against 272 B left of the 14 KB ceiling (an overrun is allowed while the window keeps 9 KB free: 12.8 KB now). (c) B from OSC1 before its own Sub FM: not available (OSC1 renders once). | **(b)**, tentatively: analog through-zero FM inputs are usually AC-coupled for this reason, and a detune that follows OSC1's Sub FM knob is hard to play. But it changes BD1's "B is OSC1's signal" and costs CPU in the full-FM voice (0.99x of its gate now), so it is your call; (a) is the plan as written. It also covers most of OD18. |
| OD18 | The crush's own rounding mean under FM 1>2 (Q6, golden finding, OD15's limit) | OD15 removed the top clip's -f_hi / S; the rounding itself still has a mean where a wave's extreme falls between two levels and is rounded the same way every cycle: at 4 bits Shape 36 (peak 0.474 -> 0.5) detunes OSC2 by +16.6 cents at note 60, +30 at 96 (FM 1>2 100 %, free OSC1), -139 cents with OSC1's own Sub FM; 8 bits -3.1 cents at most there, 12 / 8 bits from a free OSC1 within 0.32 cents (golden sweep). (a) accept as 4-bit grit (OD15's "no detune at any depth" restated: the grid's DC, not the rounding's); (b) OD17 (b)'s high-pass removes it too; (c) dither B (noise, cost, no longer the crushed signal). | **(a)**, unless OD17 = (b) takes it anyway. |
| CPU gate | (not a new decision; FD6 restated) | | The draft's 27.5 k was tighter than FD6. The plan now uses FD6 faithfully: 1.3 x the specialised voice at 2N (~31 k, I), measured in Q5. Say so if you want the tighter target instead. |

---

## Review log

| # | Issue (reviewer, severity) | Disposition |
|---|---|---|
| R1 | M1/6.2 identity breaks on Shape < 45 renders and dev-rig glide after Q0 (1, major) | **Applied.** Checked test_analog.py:1239-1240. Identity set limited to Shape >= 45 and Rig() glide; expected-change set with its own tests (1 M1, 6.2, 6.7). |
| R2 | Square sub DC detunes the carrier; dcfree/exact flags unnamed (1, major) | **Applied.** DC-free with test up to dc, `exact` sine named, Rev Saw residual noted; OD10; golden mean-frequency case (4.2, 6.3, 6.4). |
| R3 | CPU gate 27.5 k is not FD6 restated: 1.3x on the delta, 1.81 vs ~2.0 scaling, double-counted sine headroom (1, major) | **Applied.** Checked against plan-analog-fm.md:244/:292 as quoted. Gate = 1.3 x the specialised voice at 2N, ~31 k provisional, measured in Q5 (5). |
| R4 | `rintf` becomes an unresolved call under -fno-builtin; mid-tread gives 2^b + 1 levels (1, minor) | **Applied** at the reviewer's M label (not re-compiled). `__builtin_rintf`, DSP_REFUSED `rintf`; grid choice OD11 (4.7). |
| R5 | PARAM_VALUE writes its cache at +0x3C, so sub_copy is not side-effect free (1, minor) | **Applied.** Confirmed at analog.cpp:82. Save/restore as in wt1_oct; sub-object identity test (4.2, 6.4). |
| R6 | VDIV "never >= n" not an invariant; hook count includes non-kernel divisions; off count is 1 per an_render call (1, minor) | **Applied.** Count by PC range, bound <= 2n plus a worst-case test, per-part off count (6.5). |
| R7 | SLAVE reset undefined for di <= 0 (`k.inc > 0` gate) (1, minor) | **Applied.** Confirmed at analog_kernel.cpp:93. One-edge rule (p = 1 for di < 0), golden + mutation, folded into OD5 (4.3). |
| R8 | DSP size estimate unsupported (1, minor) | **Applied.** ~10-13 KB (I), 14 KB ceiling measured at Q3 and Q5 (5). |
| R9 | Block budget treats instructions as cycles; per root only (1, minor) | **Applied.** Labelled instructions per root, multi-track worst case unbounded offline, device item records `peak`/`voices_peak` (5, 8). |
| R10 | Bessel check needs sine/sine and a stated sinq accuracy (1, minor) | **Applied.** Shape 0 on both; THD measured first; tolerance derived (4.6, 6.3). |
| R11 | E3 (b) must test msub; msub stale after a skip; fmtag cleared before early returns (1, minor) | **Applied.** Confirmed an_enter returns at :269/:280. Gate on `I_s1 > 0 || msub > 0`; reset on skip; clear right after `r->voice` (3.3, 4.4). |
| R12 | SaStats has no free word for fm/bits/fm_missed/fm_skipped (2, major) | **Applied.** Confirmed 64 B full. New SA_FMSTATS at SA+0x100 (confirmed undefined), `an_print` test, OD9 for SA_LAYOUT (3.3). |
| R13 | First shipped pad entry; type 0xFF not inert; no test that no reader path creates it (2, major) | **Applied.** Type-0xFF tests in Q1 (3.1, 6.1). |
| R14 | BE4.40 opening a P3 project untested; values dropped on its re-save (2, major) | **Applied.** `test_be440_opens_a_p3_project`, release note, checklist item 10 (3.2, 6.1). |
| R15 | Message ring risk misstated: the ring blocks, never drops; emulated count would measure nothing (2, minor) | **Applied** at the reviewer's S label (disassembly not re-read). Risk and Q1 test replaced by the static fact and a back-pressure note (2.4, 5, 9). |
| R16 | Two groups do not do what OD2 claimed (page-only gating; np_ok/an_print treat non-1 as off) (2, minor) | **Applied, recommendation reversed.** Confirmed m4_synth.c:422/:1320. One 7-key group recommended (OD2); checklist uses `keys=ok`. |
| R17 | Q1's identity exit needs a harness no sprint builds (2, minor) | **Applied.** P3Identity harness and the one-ulp control are in Q1's work (6.2, 7). |
| R18 | Existing tests that hard-code BE4.40 behaviour not listed (2, minor) | **Applied.** Checked test_analog.py:935-936. Table 6.7 with new values and sprints. |
| R19 | Type-10 out-of-range display mismatch not covered (2, minor) | **Applied.** wvalue tests at 1000/5000/-3; accepted like Glide under OD4 (6.1). |
| R20 | Preset file path not covered offline (2, minor) | **Applied.** M7's preset-file clause labelled device-only (I) unless Q1 adds the round trip; checklist item 10 (1 M7, 6.1). |
