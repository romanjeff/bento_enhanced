# Bento Playground (Alpha 1.0): review and feature triage

Reviewed 2026-10-31 against bento_enhanced v3.63 (ca265d3).
Source: https://github.com/hunting-ghosts/bento-playground (commit 9fc7787; squashed history,
internal source commit faa3bc6). Static review only. The Playground image was **not** rebuilt here.

## What it is

Playground is the continuation of bento-community-lab. Lab upstream (c0ac188, "Publish Bento
Playground P27 development snapshot") now carries the Playground dev snapshot, so Lab is
effectively superseded. Our local Lab checkout (302ebf4, CH10) is two commits behind.

- Target: stock 1.6.1 `bento1.bin` only (stock sha 14475e9d…, same as ours). Controller untouched.
- Alpha 1.0 application: 1,703,576 B (0x19FE98), sha ca569652…. "hardware_acceptance: pending".
  Tested by one person on one device.
- Validation claims: 865 pass, 5 skip, 1 inherited DSP failure. The full suite was not rerun after
  the release-label change.
- Distribution: offline browser patcher (HTML+JS, plus a Python CLI) that builds the install ZIP
  from the user's own stock pair and includes a `03_STOCK_RESTORE` folder.

## Structural finding: the two images are mutually exclusive

Playground cannot be layered with our build, and we can't take its modules as drop-ins.
Every feature we want has to be **re-hosted into our layout and chained at shared hooks**,
the same way we ported Lab's conditions, hold-step and K16 controls.

**Shared hook sites.** 58 of the 120 flash addresses in our `build.py` also appear in Playground
source. Both projects patch these sites:

| Site | Ours | Playground |
|---|---|---|
| 0x0808D1FE pad input, 0x080404B6 startup | hold shortcuts | K16 input + W21/P26 extensions |
| 0x0813F98C UI consumer, 0x0813BD10 selection | hold shortcuts | fx_dispatch chain |
| 0x0819DD20 mixer vtable | `mixer_render` | `pg_mixer_*` full mixer replacement |
| 0x08060B64 / 0x08061740 conditions gate/loop | conditions + inverses/1ST/PRE | Lab T2 + live_skip (Euclid) |
| 0x08060B9A, 0x08060820 note accept/emit | retriggers | step locks / melodic send |
| 0x0812F4F0 / 0x0812FBB4 note XML save/load | `be_step_v1` | lock attrs |
| 0x081599BC / 0x0815A598 piano header/events | AT Rec/Out, Condition, Note Mod | chord, seq nav, bars |
| 0x0815C27C / 0x0815CAA0 drum header/encoders | per-step encoders 5-8 | lock UI |
| 0x0815D1EC / 0x0815D600 / 0x08134800 | strike velocity, header button | seq nav, Euclid |
| 0x08131244 modulation routing read | External mod rows | MOD quick-mod, motion |
| 0x08040588 / 0x080A3BDC / 0x080A3E94 heap end | → 0x24048400 | → 0x24040000 |
| 0x0818BAD4 version string | `BE3.x` | `Alpha 1.0` |

**Overlapping memory.**

| Region | Ours | Playground |
|---|---|---|
| M7 cave | 0x080D9000–0x080DF000 | 0x080D0000–~0x080DCC00+ (input, arp 0x080D2500, melodic locks 0x080D4400, send 0x080DAC00, adapters 0x080DBA00/0x080DC000, TF2 0x080DC400+) |
| M4 append | 0x081B4000–~0x081BF500 | 0x081B3F40–~0x081DFE98 |
| RAM | D2 SRAM 0x3003C000–0x3003E000; **M7 heap stock since v3.16** | 0x24040000–0x24048DA0 (~36 KB carved from the M7 heap); Lab STATE line 0x24048CA0 |

**Data-format overlap: the note record's +20 word.** It holds our `be_step_v1` (retrig,
condition, curve link). Playground stores its two parameter locks there instead
(`LOCK_TAG 0x51000000`, kind in bits 16–23, value in 0–15). The XML attribute names are distinct
(`be_step_v1` vs `bento_lock1_v1` / `bento_lock2_v1` / `bento_lock_condition_v1`), so project
files don't corrupt each other, but **in RAM one word can't hold both**. To port its locks we'd
need a second per-note store (sidecar, as with curves.bnt) or a combined encoding.
Their condition loader rejects values ≥135, so our inverse/1ST/PRE conditions degrade to
"none" if a project of ours is opened in Playground.

## Research findings worth absorbing

1. **Flash ceiling.** Playground ships a 0x19FE98-byte image (ending 0x081DFE98), and its build
   asserts `< 0x081E0000` (it confirms our "verify error 19" boundary). Our `PROVEN_MAX_SIZE` is
   0x18D334, so if their image boots on our device we gain about 75 KB of proven budget. Cheap
   to verify: flash Alpha 1.0 once from our stock pair, then restore.
2. **RAM: a risk, not headroom (corrected).** They move heap end to 0x24040000, which takes
   about 36 KB (21 %) of the 168 KB M7 heap (0x2401EDC0..0x24048DA0). Our own history disagrees
   with that being safe. In v3.13–v3.15 a **2.4 KB** shave made SPACED boot-loop on load
   (allocator 0x080A3DFC returned NULL), and v3.16 with the stock heap fixed it. Their status doc
   admits full save/reload needs more coverage. Their drum and FX state is hardcoded into that
   carved range, so porting it means re-placing it. Discriminating test: load SPACED on Alpha 1.0.
3. **Display emulator** (`research/display_emulator`, ~1 kLOC). A Unicorn M4 emulator with
   DMA2D emulation renders real firmware widgets to PNG (their user-guide screenshots come from
   it). It uses the same toolchain we already use for tests.
4. **Native automation A–D.** Motion recording found the lane/route allocation rules (first
   genuinely unused lane, keep existing nonzero routes, other clips can reserve lanes). This is
   direct prior art for F's "A–D lanes get CC targets on External tracks".
5. **UI vtable map.** Slot tables for launcher, tracks, mixer, bars and macro strip
   (0x081B2328, 0x081B2838, 0x081B2944, 0x081B298C, 0x081B2E88, 0x081B35C0, 0x081B38D0, …).
   This is useful reference for any future page work.
6. **Encoder scaling.** Registry key 0xA8 and the "inherited synth ×5 / native ×6" encoder
   multipliers.
7. **Release discipline.** Pinned functional hash before the identity patch, SOURCE_MANIFEST
   hashes, and byte-exact restore checks. Our build has expect-bytes, but no manifest/patcher.

## Feature triage

Ranked by fit with our ROADMAP (soak → generative sequencing → settings UI; F/F5 in progress;
D saturation; B keytracking).

### Adopt (high value, low conflict)

| Item | Why | Cost / risk |
|---|---|---|
| **Offline patcher** | Our users must build from source with Python today. A patcher that applies a write manifest to the user's stock pair (no firmware distributed) is the biggest usability gain available. | Tooling only, no firmware change. `build.py` already emits expect/replace writes. GPL-compatible. |
| **Display emulator** | Catches "button not shown" class bugs (v3.62/v3.63) without a flash cycle. | Integrate into `mods/m1` tests. Their render scaffolding assumes their palette and layout, so adapt it. |
| **Flash-ceiling device check** | +75 KB budget if confirmed. | One flash + restore session. |

### Study as design input (don't port code as-is)

| Item | Why |
|---|---|
| **Euclidean (drum + 4-row melodic)** | This is our "Next: generative sequencing". It uses the same principle we chose (generators write ordinary native notes). Its known problems (Copy/Move/Double/Undo metadata, Double Seq dropping notes) are the risk list for our planning pass. You never got Lab's version working, so plan our own. |
| **Motion recording** | Prior art for F's A–D lanes on External tracks. |
| **Track FX DSP** (`tf2_work/track_fx/track_fx.cpp`) | Soft clipper/comp source for D (master saturator). Reuse the DSP, not their per-track integration. |

### Worth considering (moderate value, needs hook merge)

| Item | Notes |
|---|---|
| **Arpeggiator** | Compact (M7 ≤0xB00 B), per track, Off/On/Latch, 5 modes, 1–4 oct, gate. It runs through their P15 note-send routing, which interacts with our note_route / poly-cap / Note Mod hooks. On External tracks it would also hit our DIN budget. |
| **Encoder speed** (fine / ×2 / ×4) | Self-contained header. Needs a home for the mode setting. |
| **PLAY-held slot mutes** | Small live feature. Theirs only targets track 1; ours should target the selected one-shot track. |
| **Macros** (8 × 3 targets) | Useful but large UI surface. It needs the PROJ/Perform page, which conflicts with our PROJ decision. |

### Skip for now

- **Drum synths, Analog synth, MOD quick-mod.** Large M7 DSP, CPU load they themselves flag,
  WAV-selector carrier workflow, and voice-engine hooks. Off our MIDI/sequencing direction.
- **Perform on PROJ.** Conflicts with our PROJ handling (removed from the modifier mask in v2.6).
- **Visual overhaul and custom Tracks/Mixer/Launch renders.** Taste, plus a large vtable surface
  that collides with our `CTRL_MIXER_VT`.
- **Chord entry and progressions.** Large. Revisit only if you want it musically.
- **Two-lock parameter locks.** They need the +20-word storage redesign first (see above).

## Caveats on Playground quality

Single tester. Self-described as built "on vibe and enthusiasm" with AI assistants. Hardware
acceptance is formally pending, there's an open DSP test failure, and the author admits CPU
artefacts. The source is dense one-liner Python/C, which is hard to review. Treat its addresses
as strong leads that we re-verify in Ghidra, never as facts.

## Not done in this review

- Rebuilding Alpha 1.0 and diffing it byte-for-byte against stock and our v3.63. The permission
  check blocked running third-party build code. Script ready: `wsl/build_playground.sh`.
  It would turn the shared-site table above from "referenced" into "actually patched".
