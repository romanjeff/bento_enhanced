# bento_enhanced v4.47 (stock 1.6.8): clean start on every boot

BE4.47 fixes the boot. On BE4.46 the Bento could get stuck in a reset loop before the boot screen. The cause was our
code trusting memory that an earlier run had left behind. BE4.47 starts every boot from scratch. Everything a project
does is unchanged from BE4.46.

Plan, measurements and review: `docs/plan-v4.47-clean-start.md`. Device checklist: `docs/checklist-v4.47.txt` (not yet
run on hardware; `device_tested` is false in `tools/release_hashes.json` until it passes).

## The symptom, and how to get out of it

On BE4.46 the reset loop looked like this: the unit lights up briefly, resets, and repeats, never reaching the boot
screen. It happened with or without USB and with the card out. Stock 1.6.8 booted. It went away by itself only
sometimes.

**Recovery (any version):** cut the power for real. Unplug USB, hold the power button until the unit is fully off,
wait a few seconds, then power on. Powering off with the button while USB stays connected is not enough: the board
stays powered ("off on USB"), and memory keeps its contents.

## What changed

- **Every boot starts clean.** At reset, before stock's own start-up code goes on, the audio core zeroes all of our
  internal RAM (12.8 KB). Nothing of ours is reused from an earlier run, whatever magic words or counters it holds. Our
  SDRAM areas start over the same way at their first use in a boot. What persists from one session to the next is the
  project file: save the project.
- **Why that fixes the loop (reproduced in the emulator).** RAM keeps its contents across a reset and in the
  "off on USB" state. BE4.46 decided "is this a new boot?" from that RAM. A run that reset early in its boot left
  memory that looked like the new boot's own. The black box then kept the last run's buffers and wrote its log text into
  memory that stock had just been given for its own objects. Boots are deterministic, so every reset repeated it, and
  only a power cut (which loses the RAM) ended it. The emulator reproduces the corruption on the v4.46 image and shows it
  gone on 4.47. The exact stock crash that followed on the device was not measured.
- **The clock and the backup registers decide nothing.** The RTC date and time, and the backup registers (where stock
  keeps its crash record), can hold anything after the clock battery drains. They are only printed in the logs, as hex.
  BE4.46 also compared stock's crash count during a session; that is gone.
- **A project loaded at boot keeps its mod settings.** This was found in the review. When the boot-time auto-load ran
  before the UI's first pass, its AT curves, AT Rec / AT Out settings and External / Note Mod settings could be dropped.
  Earlier versions could do this on any cold boot. Now they stay.
- **Log files start over when missing.** If BENTO_SAVES.log or BENTO_CRASHES.log has been deleted (or is empty, as a
  full card leaves it), the next project save creates it again. It starts with this boot's header or line.
- **The logs are still written only when you save a project**, never at boot, as in every version since v3.21. Card
  writes outside stock's own save were what damaged cards in v3.19..v3.21. A boot without a save writes nothing.
- **Version:** BE4.47 on the boot screen.

## What the logs no longer show

All of these were the next boot printing what the last run had left in RAM. That RAM is now zeroed at reset, so there
is nothing left to print:

- the header's "previous boot" line, and the "as found at boot" figures;
- the last run's counters and its last trace records;
- the audio stall snapshot, the audio core's stall record and profile, and the UI core's watchdog profile, as printed
  at the next boot. They are still captured during a boot, and the worst gaps still appear in every save;
- the boot count: every header says "boot gen 1";
- the mid-session "NEW CRASH RECORD" alarm.

Still there: stock's own crash record (cause, pc, the backup registers), printed in every BENTO_CRASHES.log line and
in every save. It names what caused the last reset.

## Compatibility

- Projects are saved exactly as on BE4.46; BE4.46 opens BE4.47 projects and the other way round.
- **Projects that use the mod's features are not supported on stock firmware.** They usually open, but they may not play
  correctly: the mod's features (AT curves, conditions, FM rows, External / Note Mod and the rest) do nothing there, and
  stock may drop some of them when it saves.

## Known limits

- **Once per boot**, at the audio core's first traced event (usually the first note), it zeroes 41 KB of its own
  memory. This was estimated at 0.1..0.2 ms and not measured. With a heavy project already playing, that may make one
  audio block late, once. The checklist asks for it.
- **1.6.1 builds** (legacy) do not get the clean start; nothing ships on 1.6.1.
