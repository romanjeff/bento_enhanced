# Build bento_enhanced for your own Bento

This page takes you from nothing to a flashing kit, built on your computer from **your own copy of
the official Bento firmware**. You don't need to know how to program. Follow it top to bottom and
type the commands exactly as shown, changing only what's in `<angle brackets>`.

**Not affiliated with 1010music.** Installing modified firmware is at your own risk. The mod never
touches the Bento's bootloader, so the official way back to stock always works (step 8).

## What you need

- A **Bento** and its SD card, plus a way to copy files to the card.
- A **computer**:
  - **Windows 10 or 11**: you'll use WSL (Ubuntu inside Windows). It's free and step 1 installs it.
  - **Linux** (x86-64): Ubuntu 24.04 or similar.
  - **macOS on Intel**: expected to work, not tested yet. **Apple Silicon (M1 to M4)**: not tested.
    One of the tools (keystone-engine) has no native Apple Silicon package; see Troubleshooting.
- **Python 3.12** and **git**. Step 1 installs both.
- About 1 GB of disk space and 15 minutes (plus 15 to 25 more for the optional tests).

**Patch in your browser** (once the site is live): the same firmware files, without installing anything, from
the project's web patcher. It applies the release to your own stock files in the page and checks the
result against the same published hash. How it works: [docs/web-patcher.md](docs/web-patcher.md).

You build a specific release, identified by its **tag** (for example `v4.46`). Each release says
which stock firmware it needs. **v4.46 needs stock Bento firmware 1.6.8**, exactly that version.

## 1. Install the tools

**Windows.** Open PowerShell as Administrator (Start menu, type `PowerShell`, right-click, *Run as
administrator*) and run:

```
wsl --install -d Ubuntu-24.04
```

Restart when asked. Ubuntu then opens a window and asks you for a new user name and password: pick
anything, and remember the password. From now on, every command on this page goes into that
**Ubuntu** window (Start menu, `Ubuntu`), not PowerShell. Install git and Python there:

```
sudo apt update
sudo apt install -y git python3.12-venv
```

**Linux (Ubuntu 24.04).** Open a terminal and run the same two `sudo apt` lines.

**macOS.** Install Python 3.12 from <https://www.python.org/downloads/macos/> (the "macOS 64-bit
universal2 installer" for 3.12.x). For git, run `git --version` in Terminal and accept the offer to
install the developer tools. Then use `python3.12` wherever this page says `python3.12`.

Check: `python3.12 --version` prints `Python 3.12.x`, and `git --version` prints a version.

## 2. Download the stock firmware from 1010music

Go to 1010music's downloads page (<https://1010music.com/downloads>, Bento section) and download
**firmware 1.6.8**. The firmware is two files, `bento1.bin` and `bento2.bin`. Keep them together
in one folder (its name doesn't matter), or leave them in the zip they came in: make_kit takes
either. The examples below use a zip called `bento-1.6.8.zip`; use your own file or folder name.

Note where it went. On Windows that is normally your Downloads folder, which Ubuntu sees as
`/mnt/c/Users/<you>/Downloads/`, where **`<you>` is your Windows user name** (this page uses
`<you>` for it from here on). Not sure of the name? In Ubuntu, run `ls /mnt/c/Users/` and pick the
folder with your name, not `Public`, `Default` or `All Users`.

If you use OneDrive, Windows may keep Downloads and Desktop inside it instead:
`/mnt/c/Users/<you>/OneDrive/Downloads/`. Check with `ls /mnt/c/Users/<you>/OneDrive/`.

This file is 1010music's, and this project never includes it: everyone downloads their own.

## 3. Get the source

```
cd ~
git clone https://github.com/romanjeff/bento_enhanced.git
cd bento_enhanced
git checkout v4.46
```

`git checkout` prints a note about a "detached HEAD": that is normal. Keep the clone in your Linux
home folder (`~`), not under `/mnt/c/`: it is faster there.

## 4. Install the Python packages

```
python3.12 -m venv ~/be-venv
source ~/be-venv/bin/activate
python -m pip install -r requirements.txt
```

The first line makes a private Python folder for this project (`~/be-venv`), the second switches to
it (your prompt now starts with `(be-venv)`), the third downloads the exact tool versions the
release was built with (zig, keystone-engine, capstone; about 100 MB). A notice that "a new release
of pip is available" is harmless: ignore it.

**Every time you open a new terminal** later, run `cd ~/bento_enhanced` and
`source ~/be-venv/bin/activate` again before step 5.

## 5. Build the kit

```
python tools/make_kit.py <the stock zip or folder> <where the kit goes>
```

On Windows, put the kit somewhere Windows can see. `C:\bento-kit` is easy to find (Ubuntu calls it
`/mnt/c/bento-kit`):

```
python tools/make_kit.py "/mnt/c/Users/<you>/Downloads/bento-1.6.8.zip" "/mnt/c/bento-kit"
```

Keep the quotes: they make paths with spaces (a user name like `John Smith`) work.

`make_kit.py` first checks that both stock files are the genuine 1.6.8 (it refuses anything else and
says why), then builds (under a minute on a recent computer), then packages the kit. It prints many
lines, and these are all normal:

- addresses like `0x808ea34`;
- `left out of the image (not ported to stock-1.6.8 yet): ['drums', 'mx']`, and a few stock sites
  "left unpatched" (Stall snapshot, watchdog and so on): those features are not part of v4.46.

## 6. Check the result

The last lines look like this:

```
kit: /mnt/c/bento-kit/bento_enhanced-v4.46
built BE4.46 (bento_enhanced v4.46 on stock 1.6.8)
  01_TEST_FIRMWARE/bento1.bin sha256 d09bcff574e3e13225626fe543e6b9d43f58f0437536f277b6319666ca1430b5
  reference      sha256 d09bcff574e3e13225626fe543e6b9d43f58f0437536f277b6319666ca1430b5
  MATCH: byte for byte the image the author tested on hardware.
```

**MATCH** means your file is identical, bit for bit, to the one tested on a real Bento. On a
**MISMATCH** no kit is made at all: see Troubleshooting.

To open the folder in Windows Explorer, run `explorer.exe /mnt/c/bento-kit` (or browse to
`C:\bento-kit`). The kit folder `bento_enhanced-v4.46` holds:

| | |
|---|---|
| `01_TEST_FIRMWARE/` | `bento1.bin` (the mod) and `bento2.bin` (stock, unchanged): what you install |
| `03_RESTORE_STOCK/` | your stock 1.6.8 pair: the way back |
| `DEVICE_CHECKLIST.txt` | what's new in this release and what to try on the device |
| `manifest.json`, `SHA256SUMS.txt` | what the image contains; the hash of every file |

Nothing else is left in the output folder: the temporary build folder is deleted once the kit is
packaged.

**Optional, for the curious: the test suites.** They run the built firmware's code in an emulator on
your computer. Install their packages, then run the step 5 command again with `--test --force` at
the end (`--force` because the kit from step 5 is already there; it is rebuilt the same):

```
python -m pip install -r requirements-test.txt
python tools/make_kit.py "/mnt/c/Users/<you>/Downloads/bento-1.6.8.zip" "/mnt/c/bento-kit" --test --force
```

This takes 15 to 25 minutes. It ends with `tests: every suite run passed` and the MATCH lines again.
If a suite fails while the hash says MATCH, the image is still the tested one, so it is safe to
flash; tell the person who sent you this page which suite failed (that points at your setup, not
the firmware).

## 7. Install it on the Bento

1. **Back up your SD card** (copy the whole card to a folder on your computer). A backup costs
   nothing, and it is your way back to projects exactly as they were (see step 8).
2. Copy **both** files from `01_TEST_FIRMWARE/` (`bento1.bin` and `bento2.bin`) to the **root** of
   the SD card (not in a folder), replacing any there. On Windows, eject the card (right-click it in
   Explorer, *Eject*) before you take it out.
3. Put the card in the Bento. **Long-press power** to start the bootloader, then press **REC** to
   install.
4. The boot screen shows **BE4.46**. Then go through `DEVICE_CHECKLIST.txt` if you like.

## 8. Going back to stock

Do step 7 with the two files from `03_RESTORE_STOCK/` instead. That is the official firmware,
installed the official way; the boot screen no longer shows `BE`. You can switch back and forth.
**Projects that use the mod's features are not supported on stock firmware.** They usually open,
but they may not play correctly: stock doesn't know the mod's features, so for example it ignores
FM, plays Analog oscillators as plain Wavetable, and plays recorded aftertouch as plain notes. Keep
a backup of any project you care about from before you used the mod. Projects that don't use the
mod's features behave as they always did.

## What you may not do

- **Never share the kit**, the output folder or any `.bin` from them: they contain 1010music's
  firmware. Each person builds their own from their own download, which is what this page is for.
  Don't upload them, post them or attach them anywhere.
- Don't change the bootloader, and don't flash an image that printed MISMATCH.
- This is a community project, not 1010music's: please don't ask 1010music support about it.

## Troubleshooting

- **`python3.12: command not found`**: step 1 didn't finish. On Ubuntu run the two `sudo apt` lines
  again. On Ubuntu 22.04 or older there is no Python 3.12 package: use Ubuntu 24.04
  (`wsl --install -d Ubuntu-24.04` on Windows).
- **`python: command not found`, or `No module named keystone` / `ziglang`**: the venv isn't
  active. Run `source ~/be-venv/bin/activate` (prompt shows `(be-venv)`), and step 4 if you skipped it.
- **`NOT stock 1.6.8`** / **`is stock 1.6.1, not 1.6.8`**: you gave a different firmware version or
  a modified file. Download 1.6.8 again from 1010music. Don't use a kit's `01_TEST_FIRMWARE` files
  as the stock input.
- **`no bento1.bin and bento2.bin here`** or **`does not exist`**: give the zip itself, or the
  folder that holds the two `.bin` files. Check the name with `ls /mnt/c/Users/<you>/Downloads/`
  (or `.../OneDrive/Downloads/`, step 2).
- **`error: unrecognized arguments`**: a path has a space in it. Put each path in quotes, as in
  step 5: `"/mnt/c/Users/John Smith/Downloads/bento-1.6.8.zip"`.
- **I can't find the kit in Windows**: run `explorer.exe /mnt/c/bento-kit` (or whatever output
  folder you gave). A `/mnt/c/Users/<you>/Desktop/...` folder may not be the Desktop Windows
  shows if OneDrive moved it; use `/mnt/c/bento-kit`.
- **`already exists`**: you built this version into that folder before. Delete the old kit folder,
  pick another output folder, or add `--force` (it is refused before building, so nothing is lost).
- **MISMATCH** (no kit is made): check you are on the tag (`git describe --tags` prints `v4.46`) with no changes
  (`git status` says *nothing to commit*), and that `python -m pip freeze` shows exactly the
  versions in `requirements.txt`. Also make sure no `BE_` setting was passed with `--set`. If it still
  doesn't match, tell the person who sent you this page. The `build-<time>` folder it leaves is
  for that diagnosis only: never flash anything from it, and delete it afterwards.
- **pip stops with `Read timed out`** (or another network error): the download was interrupted. Run
  the same `pip install` line again; it picks up where it stopped.
- **pip fails installing keystone-engine** (it tries to compile, asks for `cmake`): your platform
  has no ready-made package. Use Linux x86-64, WSL, or an Intel Mac. On Apple Silicon you can try
  the x86-64 side of the python.org universal Python through Rosetta (untested):
  `arch -x86_64 python3.12 -m venv ~/be-venv`, then `arch -x86_64 ~/be-venv/bin/python -m pip
  install -r requirements.txt` and `arch -x86_64 ~/be-venv/bin/python tools/make_kit.py ...`.
- **Native Windows (without WSL)**: see the note below. If anything misbehaves there, use WSL.
- **The Bento doesn't boot, or behaves oddly**: install `03_RESTORE_STOCK` (step 8). The bootloader
  is never touched, so that always works.

### Native Windows (without WSL)

WSL (above) is the supported route on Windows. Building directly in Windows also works: it was
checked once (Python 3.11.7, build only, no `--test`) and gave the same MATCH. If you want to try:

1. Install Python 3.12 from <https://www.python.org/downloads/windows/> (tick *Add python.exe to
   PATH*) and Git from <https://git-scm.com/download/win>.
2. In PowerShell, clone and check out as in step 3 (`cd ~`, then the same `git` lines).
3. Then, still in `~\bento_enhanced`:

```
py -3.12 -m venv $HOME\be-venv
& $HOME\be-venv\Scripts\python.exe -m pip install -r requirements.txt
& $HOME\be-venv\Scripts\python.exe tools\make_kit.py "$HOME\Downloads\bento-1.6.8.zip" C:\bento-kit
```

The kit is the same as a WSL build's, file for file (its `SHA256SUMS.txt` included). The test
suites need Python 3.12 (`requirements-test.txt`) and have not been run on native Windows.

---

Developers: `tools/make_kit.py` runs `mods/m1/build.py` (see [mods/m1/README.md](mods/m1/README.md#build-and-test)
for the build knobs and the test suites). The published reference hashes are in
[`tools/release_hashes.json`](tools/release_hashes.json). The shell scripts in
[`tools/lab-build-wsl/`](tools/lab-build-wsl/) are the author's own dev setup, not needed for this page.
