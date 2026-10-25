# M1: polyphonic aftertouch out of External tracks

**Status: working on hardware (2026-10-25).** Holding pads on an External track
sends per-note **Polyphonic Key Pressure** (`0xA0 note value`) on the track's
MIDI port and channel, alongside the normal notes. It has been verified on a MIDI
monitor and on a poly-AT synth, with each held note's aftertouch driving its own
LFO rate.

Built on top of [bento-community-lab](https://github.com/t4kbsv99sn-source/bento-community-lab)
**CH10 / 1.6HA**, which is built on stock Bento **1.6.1**. The builder refuses any
other base image.

## How it works

Stock firmware already does most of the work and then throws the result away:

1. Each pad's pressure reaches the audio core (M7) unmerged. The pad router
   (`FUN_08085540`) turns every pressure change into an internal **`0x0E` event
   `{target, note, value}`**. `value` uses the **same curve as velocity**
   (`min(raw, 0xD000) × 127 / 0xD000`), and `note` comes from the track's keyboard
   mapping.
2. The event is delivered to the External track's own handler (`FUN_08062020`),
   which has no case for `0x0E` and **drops it**.

The mod adds that missing case:

| Where | Change |
|---|---|
| M7 `0x08062466` | The handler's "neither note-on nor note-off" branch now goes through a trampoline. Every event type other than `0x0E` continues exactly as stock. |
| M7 `h_poly` (`m7_polyat.c`) | If the track's MIDI out is on (`track+0x86D1`): post `{0x5F, 0, port=track+0x31, ch=track+0x32, note, value}` to the MIDI-out queue stock's own note-on producer uses (`msg_send(engine+0x7C8, …)`) |
| M4 `0x081BEC00` | Handler for event type `0x5F`: a copy of stock's Note On case with status `0x90` → `0xA0`. The builder checks the copy against the native bytes. |
| M4 `0x081052A4` | Jump-table slot `0x5F` (unused in stock) → that handler |

**Output control.** DIN MIDI carries ~1,000 messages/s, and the M7 → M4 MIDI ring
has only 16 slots with no overflow check. So pressure is sent:
- per note: **changed values only, at most every 10 ms (100 Hz)**. A held-back
  value goes out with the next pressure event for the track;
- globally: at most **400 messages/s**, in bursts of up to 4.

Measured on hardware: minimum gap 10.6 ms, no repeated values
(`logs/M1_run1_polyat.log`).

## Diagnostics (development builds)

`m7_diag.c` / `m4_diag.c` log the pad → MIDI path. The M7 writes records to a
RAM ring; the M4's UI thread writes `\BENTO_DIAG.log` to the SD card after the
pads have been idle for 1.5 s. The logs in `logs/` are the device runs that
located the path (`diag_run1_v2.log`, `diag_run2_v3.log`) and the first
poly-AT session. The 64 KB buffer fills quickly when every pressure message is
logged; M1b will sample instead.

## Memory and code placement (on top of CH10)

- RAM `0x24048500–0x24048A00`: not claimed by any CH10 source; outside the native
  heap since Lab moved its end; inside the M4's MPU window.
- M7 code `0x080DA000–0x080DB800`: inside CH10's untouched zero gap.
- M4 code `0x081BE000–0x081BF000`: inside CH10's zero gap `0x081BD8A8–0x081C0000`.
  This stays clear of Lab's overlay (≤ `0x081BD8C0`) and of a word CH10 references
  at `0x081BF6C0`.
- **The image must stay exactly CH10-sized.** The bootloader rejected a 0x1A0800-byte
  image ("unable to program / verify main firmware"), and the builder enforces this.

## Build and test

Uses Lab's pinned toolchain (zig cc, keystone, Unicorn 2.1.3). See
[`tools/lab-build-wsl/`](../../tools/lab-build-wsl/): `build_m1.sh` builds from
CH10, and `test_m1.sh` runs `test_diag.py` and `test_polyat.py` (15 emulated checks).

```
python build.py --base <CH10 bento1.bin> --out <new dir>
python test_diag.py   <CH10 bento1.bin> <out dir>
python test_polyat.py <CH10 bento1.bin> <out dir>
```

The builder checks the original bytes at every patch site and the base image's hash,
and proves that reversing every write reproduces CH10. Tool gotchas it guards against:
- keystone mis-encodes Thumb-2 conditional branches to absolute targets, so
  conditional branches are hand-encoded and checked against the original instruction;
- zig cc strips symbols without `-g`;
- lld emits an ELF-header segment below the link origin, which is never written.

`package_m1.sh` makes a flashing kit. **It contains your stock firmware: keep it
private.** Install with the normal bootloader procedure (bento1.bin + bento2.bin on
the card root, hold power, REC). Restore by installing CH10 or stock the same way.

## Not yet

- **M1b:** polyphony cap (default 6, max 8), oldest-first stealing with forced
  note-offs, sequencer notes included; the stolen note's later native note-off is
  swallowed. A repeated note number retriggers the same voice.
- Pressure for other MIDI-out track types (Granular, Multisample, Wavetable). They
  have their own handlers, so the hook moves to the pad router's `0x0E` emission.
- Settings UI (poly cap, linear/exp/log curves); recording pressure into the sequencer.
