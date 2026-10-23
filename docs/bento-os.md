# How Bento's firmware works (1.6.1)

A map of Bento's application firmware ("MobiusFramework") as of official
**1.6.1**. Most of it is distilled from
[bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)'s
per-release design records, written in our own words with their findings credited,
and cross-checked against this project's own 1.5.32 research. It exists so that new
features can be built the way the firmware expects instead of being
rediscovered.

**Every address below belongs to 1.6.1** (`bento1.bin` SHA-256
`14475e9d5068f5659d0fe0b00fc1278c1479c43ed9fda3d40654d5f3a7a6bbb3`,
`bento2.bin` `8100906327093463fb58f531ee28583423a9881ea3a7d5b77874a7700735e99d`).
Our older 1.5.32 addresses are a different build. The structures are mostly the
same, but the addresses are not.

Evidence levels used below:
- **[device]**: observed on real hardware (by Lab's maintainer or by us).
- **[emulated]**: Lab ran the actual native ARM code under Unicorn with real
  native constructors. Strong evidence, but not hardware.
- **[static]**: read from disassembly or decompiled code only.

## 1. What's in `bento1.bin`: three images, two cores

`bento1.bin` is not one program. It contains three separate vector tables
(checked directly in both 1.5.32 and 1.6.1):

| Flash range | Image | Initial SP (1.6.1) | Core |
|---|---|---|---|
| `0x08040000..0x080E0000` | Main application: audio engine, voices, sequencer playback, **pad input** | `0x2404CDA0` (AXI SRAM) | Cortex-M7 |
| `0x080E0000..0x08100000` | Disk mode (USB mass storage) image (`MobiusDiskMode`) | `0x24029898` | — |
| `0x08100000..~0x081B4000` | UI application: SessionMgr/model, widgets, pages, parameter registry, project XML, Undo | `0x300390F0` (D2 SRAM) | Cortex-M4 |

`0x08000000..0x08040000` is the bootloader and installer. Never touch it.
`bento2.bin` is the separate "controller" image. Lab never modifies it, and
its full role is still open (see [`architecture.md`](architecture.md)).

**MPU executable regions** [emulated, from the native MPU setup code]:
the M4 UI context can execute only from `0x08100000..0x08200000` (MPU control = 1,
*no* default memory map for privileged code). The M7 "input" context runs
`0x08000000..0x08100000`. New UI code therefore has to sit in the M4 range and
new audio code in the M7 range. Lab's first candidate failed this rule, and
Unicorn does not enforce the MPU on its own, so the test missed it.

**Code caves Lab uses (1.6.1):** M7 audio from `0x080D0000` up to `0x080E0000`
(the zero gap after the initialized-data source ends at `0x080CF49C`, with
synths at `0x080D5800..0x080DC000` and FX at `0x080DC000..`). M4 UI code is
appended after the image end (`0x081B3F3C`), plus `0x081B9000..0x081BA000` and
`0x081C0000..0x081CC000`. The stock installer accepts a longer, appended image
**[device]**. It does **not** accept one that is not word-aligned: a build two
bytes short of 4-byte alignment failed to program **[device]**.

## 2. Memory map

| Range | Owner | Notes |
|---|---|---|
| `0x24000000..0x240001D0` | M7 | initialized data (copied from `0x080CF2CC`) |
| `0x240001D0..0x2401EDBC` | M7 | zeroed data |
| `0x2401EDC0..0x24048DA0` | M7 | heap; free-list head `0x24000178` |
| `0x24048DA0..0x2404CDA0` | M7 | initial stack |
| `0x24040000..0x24080000` | shared | the slice of AXI SRAM the native M4 MPU permits |
| `0x3000xxxx` | M4 | D2 SRAM. UI bump allocator: cursor `0x3000004C`, end `0x30000048`; allocator `0x08104C08` |
| `0xC6000000..0xC8000000` | M4 | SDRAM bump pool (start/end from reset data at `0x081B3E38`+0x48/0x4C). Large UI scratch goes here |
| `0x10040000..0x1004FFFF` | M7 | 64 KB region (input-context MPU region 5) where native sample-descriptor providers live |

Reserving fixed RAM safely [emulated, plus device boots]: shrink the M7 heap by
lowering its end. The value `0x24048DA0` appears as a literal in three places
that **all** have to change: startup heap length `0x08040588`, the free-pointer
bound check `0x080A3BDC`, and the allocator span check `0x080A3E94`. Other
occurrences of that value are the stack bottom and sentinels, and must not be
touched. Lab's TF2 build moves the heap end to `0x24040000`, giving 8 KiB for
mod state.

Known failure modes **[device]**: three builds booted to a black screen with the
pads lit. One cause was M4-written state placed at `0x2403A400`, outside the
M4's permitted SRAM. Another was floating-point save/restore in code reached
during early startup, before the FPU is enabled. An MPU fix alone did not cure
it.

## 3. Cross-core communication

- **Pad input path:** pads arrive on the **M7**. The pad event hook is at
  `0x0808D1FE`. A native shared queue (`ENQUEUE` `0x08082221`, producer global
  `0x2401BD3C`) carries messages to the M4's outer consumer at `0x0813F98C`,
  which goes on to dispatch `0x0813F2FD`. Track selection is `0x0813BC3D`. Pads
  0–7 are the top row and 8–15 the bottom **[device]**.
- **Mod-defined mailboxes** (Lab's pattern, worth copying): the M4 writes a
  32-byte-aligned block behind an odd/even sequence counter (a seqlock). The M7
  invalidates those cache lines before reading and throws away torn or odd
  reads. Each cache line has exactly one writing core.
- The M4 never calls DSP code, and the M7 never allocates memory, touches XML,
  or draws.

## 4. Parameter registry (the "472-entry table")

The UI-side parameter descriptor table: in 1.6.1 it is a 0x3E60-byte allocation
holding **499 entries** at 32 bytes each. It is built by `0x081639A8`, and the
generic descriptors live around `0x30031484`. This is the same structure
documented for 1.5.32 in [`ui-architecture.md`](ui-architecture.md) ("system 1",
472 entries at `0x30038600`). It grew by 27 entries between the two versions.

Rules for adding a parameter ID [emulated]:
- **Run the real native constructor to find empty IDs.** Lab's tests with a
  zero-filled stand-in registry missed real collisions: `0x1E5` is Attack
  (type 20, range 1..20), and `0x1AE` and `0x1D4..0x1D6` are also taken.
- IDs at `0x1F4` and above are outside the allocation.
- Check other mods' "lazy" registrations as well as the native table. Lab
  already uses `0x128..0x12D`, `0x190..0x19F`, `0x1A0..0x1B0`, `0x1B1..0x1D0`
  (Track FX) and `0x1D1..0x1D3`. **Any of our new IDs must avoid these if we
  want to coexist with Lab's builds.**
- Writing a descriptor makes a control *display*. It does not wire it to
  storage or audio. That matches our own B4 finding (label renders, value
  stays dead).

## 5. UI framework

Builds on the scene-graph and event-bubbling model in
[`ui-architecture.md`](ui-architecture.md).

- **Reusable settings page:** native `GenericProperties` page `0x1A`. Lab
  repurposes it for its own pages while a mod-owned mode flag is set (Track FX,
  chord editor). This is likely the cleanest route for our project-settings
  features too, compared with retrofitting SEQ's table.
- **Encoder parameter bars:** a view's 8 slots, for example on the piano roll at
  `view+0x55F0+0x38+i*0x468`. The screen is 1280 logical pixels wide, each slot
  159 px, at y=406 with height 70. Per-slot text override at `slot+0x2C8`:
  `0x08170656(bar, idx, NULL)` clears it. `0x08171C1C` configures a key,
  `0x08171108` restores labels, ranges and types, and `0x08171640` re-reads titles
  from the registry. Encoder turns go through `0x08171E44`. Lab calls this an
  "event 0xCF" path, while our 1.5.32 notes say encoder turn = `0xCB`; see the
  open questions.
- **One-shot SEQ view** (page `0x27`): event entry `0x0815D600`, arrow branch
  `0x0815D57E` (Left=0, Up=1, Down=8, Right=9), step header `0x0815C27C`. The
  view-side key/value binding arrays at `view+0x113AC`/`0x113BC` are where the
  header's encoder bindings live. **This is probably the runtime-computed binding
  our static analysis of SEQ's 8-slot table couldn't find.**
- **Piano-roll view**: constructor `0x0815930C`, vtable `0x0819E7B0`, event
  handler `0x0815A598`, header refresh `0x081599BC`. Sequence at
  `view+0x11CD00`, owner `+0x11CD08`, length `+0x11CD18`.
- **Menus:** Undo pop-up tables F9 and FA, built by `0x08177D48` and applied by
  `0x0817818C`, which rejects more than 9 cells. They lay out bottom-up within
  each column. The permanent top header is a 0x1C4-byte struct with six 48-byte
  action records at `+0x74`. It is generated by `0x08134800`, buttons are built
  by `0x0817FAF8`, and actions are emitted by `0x0817FE70`.
- **Drawing:** native leaf draw functions are waveform `0x08183854`, envelope
  `0x08168768`, LFO `0x0817DDFC`, buttons `0x0816A97C`, labels `0x0817E780` and
  rectangles `0x08179120`. Palette index 0xF1 is the background (black) and 0xF2
  is white. Lab intercepts at function entry and walks parents to decide
  whether to suppress drawing [device: confirmed clean on screen].
- **Buttons:** TRACKS is logical 2 (raw mask `0x2000`) **[device]**. SEQ is
  index 11. `HAL_GetTick` is `0x081152C0` (counter `0x3001FE2C`), used for
  double-tap timing.

## 6. Sequencer and note model

- **Note record, 24 bytes** [emulated]: `+0` kind (1 = note), `+1`
  probability/condition, `H+2` drum/instrument index, `W+4` time, `W+8`
  length, `W+12` ID, `H+16` pitch, `H+18` velocity, `W+20` extension. Up to 511
  events per container.
- **Time base:** stored positions use 960 units per *step*, while the playback
  clock uses 960 ticks per *quarter note* (a default 16th = 240 ticks). The
  step-size table is at `0x080BDBEC` and the 64-bit conversions at
  `0x080A21C0`/`0x080A2200`.
- **Edit APIs (M4):** root-sequence lookup `0x0812DFE8`. Assign ID and insert
  `0x0812E070` (call it once only). Bulk delete with an Undo snapshot
  `0x08136FB4`. Explicit Undo snapshot `0x081338DC`. Upsert and publish to the
  audio engine `0x08136544`. Edits made this way save through the normal
  project format and are covered by native Undo.
- **Playback (M7):** the timed-note processor is `0x08060820` (its router is
  passed as the 5th stack argument). Clip slots are 16 × 848 bytes at
  `sequence+240`, with owner at `+12` and the playing flag at byte `+20`. The
  sound route is `0x0805F8E0`, and it also handles MIDI forwarding and pad
  notifications. The probability gate is `0x08060B64`, the condition counter
  `0x08061740`, and the accepted-note handler (message `0x12`) `0x0808FA90`.
  Velocity is written as a float /127 to `voice+0xB8`.

## 7. Audio engine (M7)

- **Per-track insert point:** `0x080A0510` runs after track gain and before
  pan, ducking, sends and summing, on the track's own scratch buffer
  (≤1024 frames per channel). This is where Lab's per-track EQ/compressor
  sits, and it is the natural place for any per-track DSP we add.
- **Sampler root:** `0x080AC66C`.
- **Modulation** (relevant to our [`mod-matrix.md`](mod-matrix.md) and to
  External-track modulation, Feature F): `0x08092F20` copies native modulation
  parameters into the voice (Level `+0x180`, Pitch `+0x300`, …). Each
  parameter's data is 64 bytes: a base value, up to 3 user sources and the A–D
  automation contribution. Voice route records are at `root+0x590`, with the
  count at `+0x5FC`, 12 bytes each: float amount, `H+4` target, `H+6` source,
  `B+8` slot. Source dispatch binds **4 → `voice+0x610` (LFO1), 13 →
  `+0xB4` (velocity), 14 → `+0xBC` (key), 32 and 33 → `+0xC4` (pressure)**.
  These IDs match our independently decoded 34-entry source table. The 32/33
  mapping also settles in code our open "two Pressure entries" question: both
  read the same voice field. Attach a source with `0x0809FD00` and evaluate with
  `0x0809FD80`. The shared target table `0x0819E428` has keys
  `2,3,4,1E,1F,21,4C,4B,32,33` and is shared with automation, so don't edit it
  globally. Mod commands are emitted at `0x0813325C`.

## 8. Persistence

The project params-node save hook is at `0x0812F5EA` and the load hook at
`0x0812FAAC`. Lab adds its own versioned attributes there (e.g.
`bento_kick_v1_*`, `bento_eu2_*`). **Stock firmware ignores unknown attributes
when loading, and may drop them on its next save** [static/emulated]. Mod-only
data can therefore be lost if a project is saved under stock firmware.

## 9. Lessons about the method

- Unicorn **2.1.4** executes Thumb-2 conditional (IT-block) code incorrectly.
  Pin **2.1.3**.
- Never patch in the middle of a 32-bit Thumb instruction. Check the original
  bytes at every patch site against the stock image.
- Tests that use fake ("fixture") registries, menus or headers have passed for
  features that then didn't appear on the device. Lab's Select and Chord
  buttons still don't show up on hardware. Prefer running native
  constructors, and treat "displays in emulation" as weak evidence.
- Lab's toolchain is Zig's bundled clang (installed via pip) with `-Os -flto`
  and no fast-math. Functions called only from patch sites need
  `__attribute__((retain))` or the linker drops them.

## 10. How this maps onto our 1.5.32 findings

| Our 1.5.32 finding | 1.6.1 counterpart | Status |
|---|---|---|
| "UI lives in `bento2.bin`" | UI is the M4 image inside `bento1.bin` | **Corrected**, see `architecture.md` |
| 472-entry param table `0x30038600` | 499-entry registry, constructor `0x081639A8` | Same structure, grown |
| SEQ 8-slot table has no static xrefs | SEQ view binding arrays `view+0x113AC/0x113BC` | Likely the runtime binding; verify |
| Encoder event `0xCB` | Lab: encoder turns via native `0xCF` path | **Open**: different layer or a version change |
| Mod sources 4/13/14/32/33 | Voice bindings in `0x08092F20` | Confirmed independently |
| Two "Pressure" source IDs | Both read `voice+0xC4` | Resolved |
| PROJ page values (Feature C) | Lab turns PROJ into Shift | **Design conflict** if both are installed |
| Compressor (Feature D) | Lab adds a separate per-track strip at `0x080A0510` | No overlap: ours targets the native master compressor |

## Open questions

- What `bento2.bin` (the "controller") actually does now that UI is known to be
  M4.
- `0xCB` vs `0xCF` for encoder turns.
- Whether our SessionMgr address (`0x30025a20` in 1.5.32) and the bubbling and
  property functions moved, and where to in 1.6.1.
- The installer's exact erase and program ranges: unknown to everyone. Lab
  relies on 1010music's assurance that reinstalling stock recovers the device.
