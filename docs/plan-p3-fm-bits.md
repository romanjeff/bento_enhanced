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
| F0 bug 2 (glide runs at half the set time) | **Still open.** `glide_coef` (analog.cpp:364-374) computes `0.001^(n/(s*48000))` with n = 2N. The glide tests run in `Rig()` (48 kHz, n = 128), so they cannot see it. | S |
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
silent OSC1 still renders when it is a modulator); the mid-tread Bit Depth quantiser in the 96 kHz domain; BD1.

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
   Bit Depth post-pass on s (if bits != 0)
   if OSC2 Analog && (fm12(o2) > 0 || m12(o2) > 0): B[i] = inc1 * s[i]; fmtag |= B   (BD1: after crush, before Level)
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

### 4.4 E3 and skip rules (analog.cpp:428-444)
- (a) OSC1 is silent, OSC2 is Analog, and `fm12 > 0 || m12 > 0`: do not take the `(silent && !master)` early return.
  Render into s, crush, write B, skip SMOOTH/MIX.
- (b) OSC1 is a silent master (both-Analog sync) with **`I_s1 > 0 || msub > 0`**: `an_track` ignores FM, so run
  `an_render_fm` in the MASTER role into s, not mixed. Testing msub too means a master ramping its Sub FM down does not
  fall back to `an_track` mid-ramp. With both at 0, keep `an_track` for bit identity.
- (c) OSC1 is silent and only OSC2 has Sub FM: OSC1 may still skip, and OSC2 fills A itself.
- (d) OSC2 is silent: skip as today. It is nobody's modulator.
- **Skip path resets the ramps.** When an oscillator takes the silent skip, set `msub = m12 = 0`, as the skip path does
  for `mshape` (S). Otherwise the oscillator would ramp from a stale amount when it is heard again. After silence the
  amount starts at its set value with no ramp.
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
  OSC1's audible (crushed) signal, including OSC1's own FM.

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
    model measures it first in Q3, and the Bessel tolerance (6.3) is set from that figure.
  - k = 0 still dispatches to SAW, so Shape 45..127 runs today's instances (bit identity).
- `an_reshape` (analog_kernel.cpp:222-227): replace `wave<MIXED>` with `wave_any(p, Shape)`. It must call
  `wave<MIXED>` unchanged for k >= 0 (bit for bit) and the LOW form only for k < 0. The correction is still a value jump
  only. At AN_RAMP = 32-frame steps the slope change per step is tiny (I), so the reshape gets no BLAMP. Test 6.4
  measures the click energy against BE4.40's saw->square ramp.
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
- A post-pass after the part loop and before `S_SMOOTH_APPLY` (after analog.cpp:480, before :482), over s[0..n). Skipped
  for Full.
- `q = __builtin_rintf(x * S) * (1/S)`, mid-tread, with S = 2^(b-1): 2048, 128 and 8 for 12, 8 and 4 bits.
  - **Use `__builtin_rintf`, not `rintf`.** The DSP build uses -ffreestanding -fno-builtin -nostdlib, so plain `rintf`
    compiles to an unresolved `bl`. `__builtin_rintf` inlines to VRINTX.F32 (M: q.cpp compiled with the project flags,
    build.py:666-669). Add `rintf` to DSP_REFUSED.
  - Mid-tread at S = 2^(b-1) gives 2^b + 1 levels (17 at "4-bit": -8..+8), plus a few more from BLEP overshoot above 1.
    It is symmetric, has no DC and does not clip. A true two's-complement b-bit grid would clamp to [-S, S-1] and clip
    the top. OD11 asks which one; the recommendation is mid-tread.
- The overshoot above 1 is kept, not clipped (I: the overshoot is small, and clipping would add asymmetric distortion).
- The crush runs in the 96 kHz domain ahead of the FIR, so the decimator partly removes its images above 24 kHz (I).
- BD1: B is written from the quantised s, before Level.
- Because it is a post-pass, the non-FM loops stay untouched (M1).

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
| DSP window 0x080D0100..0x080D9000 (36,608 B) | 9,708 B used, 26,900 B free | **~10-13 KB** in all. an_render's 9 instances are 4,640 B (~515 B each, M). The generic FM loop has per-sample mirrored edges, triangle BLAMPs both ways, sinq, two modulator reads, a ramp and a clamp, and SLAVE inlines advance twice, so ~1.5-2.5 KB per instance x 3. Plus sub copy ~2 KB, LOW x 3 + shape code ~2 KB, Bit Depth + glue ~0.5 KB. **Ceiling: P3 <= 14 KB**, measured at the end of Q3 and Q5. | ~20-23 KB used; with drums (~6 KB) ~26-29 KB, 7-10 KB free | M (now) / I (adds) |
| M4 image vs PROVEN_MAX 0x18D334 | 0x188DF8, 17,724 B free | ~1.6 KB: template +32 B, descriptors/labels/resend/rows ~0.4 KB, dial + V1 text ~1 KB, an_print FM line ~0.1 KB | ~16 KB free | M / I |
| AnOsc | 172 / 180 B | msub, m12 (4 B) | 176 / 180 B | S |
| AnProps | 12 B | 0 | 12 B | S |
| Shared area | SA_UI 2 KB unused; SA+0x100..0x200 undefined | A 1 KB + B 1 KB in SA_UI; SA_FMSTATS 32 B at SA+0x100 | | S |
| Run | r9..r15 spare | r9 fmtag, r10 inc_sub | r11..r15 spare | S |
| CPU per voice-block, n = 256 (hooks only) | saw 13,192; mixed 19,840; saw+sync 15,785; mixed+sync 22,180 | full FM voice (sine sub -> both, 1>2, saw) | **Gate (FD6 restated): generic full-FM saw voice <= 1.3 x the specialised full-FM voice at 2N, provisionally ~31 k** | M / I |
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
  clamp; the events; and the mid-tread quantiser.
- Compared with the emulator sample by sample, max error <= 1e-5 (float32 vs float64), for: Sub FM with sine, saw,
  square and noise subs; FM 1>2 and both routes together; a sync master and slave with FM (including di < 0 at the
  reset); mixed sync with Sub FM; each Bit Depth; Shape 0, 12, 24, 36 and 47.
- **Square sub DC case (new):** Sub FM, Square sub at PW 30 %, I = 8. The mean carrier frequency equals the unmodulated
  one (DC-free), within one bin.
- Spectral checks:
  - First measure `sinq`'s THD in the golden model.
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
  saw->square ramp figure.
- Glide in Rig(dev=True): Glide 500 (1 s) reaches 0.001 of the interval at 1 s +- 1 block. This fails at HEAD (OD1).

### 6.5 VDIV and cost counts
- Hook VDIV.F32 in Unicorn and **count by PC range**: inside `an_render_fm`, inside `sub_copy`, and everywhere else.
  The rest includes existing non-kernel divisions in rate_of (/23.4375, /F(0x108)), pitch_oct (/1000, /12) and
  glide_coef (M: `a/23.4375f/b` compiles to two VDIVs with the project flags).
  - `an_render_fm`: count == the golden model's edge count (wraps, pulse edges, triangle corners), +- 0. Bound <= 2n per
    render, with a worst-case cost test: deep through-zero, 8 % pulse, high OSC1 ratio.
  - `sub_copy`: count == the golden model's sub edge count (saw wrap, both square ramps).
  - FM off: today's count, which is 1 per `an_render` call (so a Shape ramp gives one per part, e.g. 8), not 1 per
    oscillator.
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
fails).

### 6.7 Existing tests that encode BE4.40 behaviour (edit as reviewed changes, each with its new value)
| Test | Today | After | Sprint |
|---|---|---|---|
| test_analog.py:1239-1240 (`run_(20,-1.0)` vs `run_(0)`, "clamped at 0") | both saw | both sine (Shape 0); fix the "saw" label | Q3 |
| test_analog.py:935-936 `test_settings_are_clamped_where_they_are_stored` | K_SYNC 7 -> 0, -1 -> 0 | 7 -> 1, -1 -> 1 if OD4 takes the Sync change | Q1 |
| test_synth_ui.py:494 out-of-range page | negative Shape -> `Saw` | -> `Sine` | Q3 |
| test_synth_ui.py:781, :940-953 V1 tables, `test_out_of_range_shapes_clamp` | Shape <= 48 -> `Saw` | five-shape texts | Q3 |
| test_keys.py:233 | `cover == [SHAPE, SYNC]` | `[SHAPE, SYNC, BITS]` | Q1 |
| test_native_props.py:237 | 0 pads | 1 pad | Q1 |

---

## 7. Sprints (each: an offline exit, the full suite green, a commit after each; push only on Jeff's word)

| # | Sprint | Work | Offline exit |
|---|---|---|---|
| Q0 | Glide fix (if OD1 = in P3) | `glide_coef`: `s * 48000 * F(osc,0x108)` (2 on the device). The coef cache key is unchanged (n is already in it). Add the Rig(dev=True) glide-time test. | The 6.4 glide test is red at HEAD and green after; Rig() glide outputs bit-identical |
| Q1 | Keys, storage, harness; no sound | template keys + pad; `NP_KEYS` 7 keys; `want_row` slots 5..7 + the hidden rule; `resend` with 6 keys; `an_prop` stores (+ OD4 Sync line); AnOsc +4 B; SA_FMBUF, SA_FMSTATS and the tag in synth_shared.h; fmtag clear in an_enter; **P3Identity harness** (second build dir, capture/replay, one-ulp control); type-0xFF tests; BE4.40-opens-P3 test; preset round trip if the entry is found; 6.7 Q1 edits | 6.1 green; 6.2 identity green; manifest template_n 0x304 |
| Q2 | Bit Depth | post-pass quantiser (`__builtin_rintf`, DSP_REFUSED `rintf`), `bits=` stat | golden 6.3 crush cases; identity at Full; BD placement mutations |
| Q3 | Five shapes | an_shape map, set_shape for k < 0, LOW regime x 3, `wave_any`, sinq THD measured, V1 text, five-glyph dial, 6.7 Q3 edits | identity 45..127; golden sine/tri/blends; click test; UI snapshots; DSP size logged against the 14 KB ceiling |
| Q4 | Sub copy and buffers | `sub_inc`/`sub_copy` port with the DC-free Square (OD10) and the PARAM_VALUE cache restore; buffer A; n > 256 guard (nothing audible uses it yet) | 6.4 sub copy worst 0 (up to dc) in the voice path, 5 waves x 2 levels; sub object byte-identical; copy cost recorded |
| Q5 | Sub FM (OSC1, OSC2) | `an_render_fm` x 3 roles incl. the di <= 0 slave rule; msub ramp and skip reset; E3 (b)/(c); mixed-voice path; `fm=` stat; measure the specialised delta at 2N and fix the gate | golden Sub FM cases incl. sync master/slave and mixed sync; VDIV counts by PC range; cost gate; DSP size; mutations |
| Q6 | FM 1>2 | B write (BD1), E3 (a), useB + fmtag, m12 ramp, `fmx=` | golden FM 1>2, combined and Level 0 cases; Bessel check (sine/sine); full-FM cost within the gate |
| Q7 | Kit BE4.41 | build; size and cost tables into this doc; device checklist; black box lines; release notes (glide time change, Shape < 45 change, rollback drops the 3 values) | full suite green; manifest sizes within section 5; checklist written |

The order puts the zero-risk plumbing (Q1) and the separable features (Q2, Q3) before the hard DSP (Q5, Q6), so each
commit can ship on its own. Close call: Q3 could come after FM. It stays before, because the FM loop has to generate
the sine/triangle terms anyway, and testing it against a finished LOW regime is easier.

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

---

## 8. Device checklist (BE4.41; BE4.40's own checklist runs first, as it is not flashed yet)
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
8. Sync 1>2 on plus Sub FM on OSC1: the slave follows the FM'd master. Also mixed sync (OSC1 Wavetable) with Sub FM on
   OSC2.
9. Shape sweep 0..127 by knob and by modulation (Position routed): sine, triangle, saw at 48, square, pulse. The dial
   glyphs light.
10. Save, reload, copy/paste a track, save and load a preset **file**: all three values are kept. Stock 1.6.8 opens the
    project. BE4.40 opens it too, and drops the three values on its next save (expected).
11. If Q0 shipped: Glide 50 % = 1 s (stopwatch or tuner).
12. CPU: an 8-voice chord with full FM, then the same on several tracks. Record DSP %, `peak`, `voices_peak`, `fm=` and
    `bits=`, with no `OVER`.
13. An old BE4.40 project with Shape < 45 now plays sine or triangle (expected; no migration).

---

## 9. Risks

| Risk | Effect | Mitigation | Label |
|---|---|---|---|
| FM costs about 2x the old plan at 2N | +9-10 % instructions per root of 10 full-FM voices; multiplied across tracks; OVER on dense patches | gate measured in Q5; specialise the hot case (saw carrier, sine sub) only if it fails; device item 12 | I |
| Device CPI unknown (flash execution) | instruction counts understate cycles | device item 12 records `peak` with `fm=` | I |
| The generic loop hoists a division into every sample | ~257 VDIVs per render | VDIV count by PC range (6.5) on every build | I |
| DSP code larger than estimated | squeezes the drums' room | 14 KB ceiling measured at Q3 and Q5 | I |
| Square sub DC detunes the carrier | pitch drifts as Sub FM rises | DC-free copy (OD10); golden mean-frequency case | S/I |
| sub_copy disturbs the sub via PARAM_VALUE's cache | the copy or the sub's render is no longer exact | save/restore around the calls; sub-object identity test | S |
| Stale A or B between voices | FM from another voice | fmtag cleared right after `r->voice`; `Run.voice` check; mutations | S |
| E3 cases missed | silent FM 1>2, or wrong sync wraps from a silent master | E3 (a)/(b) tests incl. the ramp-down; mutations | S |
| Backward wraps and slave resets | resets that sound wrong under deep through-zero sync; double edges at di < 0 | golden model; the one-edge reset rule; OD5 | I |
| The Shape remap changes J4 and old projects below 45 | an audible change | checklist item 13; no migration (decided); 6.7 edits | S |
| LOW regime BLAMP / sinq quality | aliasing on bright blends; Bessel test too strict | THD measured first; spectral checks | I |
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
| OD11 | Bit Depth grid | (a) mid-tread, S = 2^(b-1): 2^b + 1 levels, symmetric, no clip; (b) true b-bit two's complement, clamped to [-S, S-1] | **(a)**. It has no DC and no top clip, and at 12/8 bits the extra level is inaudible. At 4 bits it is 17 levels instead of 16, so the label is nominal. Choose (b) if you want a stricter "4-bit" grit with an asymmetric top clip. |
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
