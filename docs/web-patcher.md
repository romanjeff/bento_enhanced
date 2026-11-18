# The web patcher

`web/` is a single static page that makes the same flashing kit as `tools/make_kit.py`, in the browser,
from the user's own stock firmware, with nothing to install. It is meant for GitHub Pages
(`.github/workflows/pages.yml`, inert until enabled, deployed by hand: see Deploying).

The site contains none of 1010music's firmware: no run of 8 or more bytes from either stock file, and no
near-copy of a stock region, only short sequences any two pieces of ARM code share (the rule and its
checks are below). Per release it holds a **patch**: the bytes this project authored plus instructions to
copy everything else out of the user's own stock files. The result is checked against the release's
published SHA-256 before anything can be downloaded, so the page can only hand out the image whose hash
the patch names. That it is the image `make_kit.py` builds and the author tested is held by the steps
around it: the exporter takes only a device-tested release whose hash is the published one in
`tools/release_hashes.json`; `index.json` lists only device-tested releases; the page refuses any patch not
marked `device_tested`; and the deploy step checks every patch's hash against `release_hashes.json`.

| file | what |
|---|---|
| `web/index.html`, `web/style.css` | the page (no external scripts, fonts or CDNs; CSP allows only the site itself) |
| `web/patcher.js` | all the logic, plain JS, no build step; the same file runs under node for the tests |
| `web/patches/index.json` | the releases offered (device-tested only, newest first, `default` = newest), the stock hashes, known hashes for messages |
| `web/patches/v<ver>.json` | one release's patch |
| `tools/export_patch.py` | makes a patch from stock + the release image, self-verifies it |
| `tools/test_web_patch.py` | the tests (below) |

## What the page does

1. The user drops (or picks) `bento1.bin` and `bento2.bin`, or the 1010music download's zip
   (stored or deflated entries, in any folder; deflate uses the browser's `DecompressionStream`).
2. Each file is identified by its SHA-256 (WebCrypto), never its name. Anything but the exact stock
   1.6.8 pair is refused, with a reason when the hash is known (stock 1.6.1, an already-modded
   image) and what to do. Files over 4 MB (64 MB for a zip) are refused before they are read.
3. The user picks a release (default: the newest). All patches are fetched when the page loads, so
   from then on it works offline, as long as the page is not reloaded (there is no service worker).
4. The page checks the stock hashes again, the patch payload's SHA-256, applies the ops, and checks
   the result's SHA-256 against the release's. Any mismatch stops it: nothing is offered. A patch not
   marked `device_tested` is refused.
5. It offers one download, a zip kit (written by `patcher.js` itself, stored, with CRC-32) laid out like
   make_kit's. There is deliberately no "just bento1.bin" download: next to the stock `bento1.bin` in
   Downloads it would land as `bento1 (1).bin`, and the kit's folders keep the two apart.

```
bento_enhanced-v<ver>/
  01_TEST_FIRMWARE/bento1.bin   the patched image          (== make_kit's, byte for byte)
  01_TEST_FIRMWARE/bento2.bin   the user's stock bento2.bin
  03_RESTORE_STOCK/bento1.bin   the user's stock pair
  03_RESTORE_STOCK/bento2.bin
  DEVICE_CHECKLIST.txt          docs/checklist-v<ver>.txt, carried in the patch, under a preface
  README.txt                    install steps, the way back, disclaimers (web kit only)
  SHA256SUMS.txt                make_kit's format and order
```

The deliberate differences: **no `manifest.json`**. The build manifest's `writes` list has the
`original_hex` of every patch site, which is stock bytes, so it is never published. The web kit has
`README.txt` instead. And **DEVICE_CHECKLIST.txt has a preface** (`patcher.js checklistText`): the checklist
was written for the developer kit, so it names make_kit's folders, Windows paths, `manifest.json` and
`docs\...` files, and has "measured in the emulator" notes from before the hardware test. The preface says so
and points at the useful part ("What changed").

## Patch format (`format_version` 1)

```json
{
  "format": "bento_enhanced-webpatch", "format_version": 1,
  "version": "4.46", "label": "BE4.46 (bento_enhanced v4.46 on stock 1.6.8)", "base": "1.6.8",
  "stock_sha256": {"bento1.bin": "cb8f830e...", "bento2.bin": "81009063..."},
  "stock_size": {"bento1.bin": 1535540, "bento2.bin": 85696},
  "output_sha256": "d09bcff5...", "output_size": 1611956,
  "image_commit": "031edf1", "device_tested": true,
  "min_copy": 8,
  "ops": [["W", 1206, 4], ["C", dst, src, len], ["D", dst, src, len], ["F", dst, len, 0], ...],
  "payload_sha256": "...", "payload_b64": "...",
  "kit": {"name": "bento_enhanced-v4.46", "checklist": "..."},
  "stats": {...}
}
```

The output starts as stock `bento1.bin` truncated or zero-extended to `output_size`, then the ops run
in order:

- `["W", dst, len]`: the next `len` bytes of the payload (ours) at `dst`;
- `["C", dst, src, len]`: `len` bytes from the **original** stock `bento1.bin` at `src` (not from the
  output being built, so the order of COPYs never matters);
- `["D", dst, src, len]`: `len` bytes from stock `bento2.bin` at `src` (a run of ours that happens to occur
  in bento2: three 8-byte compiler idioms per release so far);
- `["F", dst, len, byte]`: `len` copies of `byte`.

An op out of bounds rejects the whole patch. The payload is one base64 string with its own SHA-256 (a
damaged download is caught before applying). The reference implementation is
`tools/export_patch.py apply_patch`; `web/patcher.js applyPatch` is the same thing and the tests hold
them to the same output.

## The zero-stock-bytes rule

The policy: **the published patch contains no stock content**, from either 1010music file. `export_patch.py`
builds the patch so that it holds, and re-checks every rule before it writes anything (`check_policy`);
`test_web_patch.py` checks the committed patches again, with mutants that each rule must catch:

1. **No 8-byte stock run** (`no_stock_run`). No run of N = 8 or more bytes that occurs anywhere in stock
   `bento1.bin` or `bento2.bin` is left in the payload. Such a run, at any offset, becomes a COPY (C from
   bento1, D from bento2). The check scans two ways, so the shape of the ops cannot hide a run: every WRITE
   span, with WRITEs joined by output offset whatever their order in the op list, and the raw payload stream
   as one sequence (WRITEs scattered over distant offsets lie side by side there).
2. **No relocated stock instruction** (`no_relocated`). No WRITE holds a run of 4 to 7 bytes equal to stock
   bytes in, or straddling the edge of, a range the release changed. When the mod hooks a function it moves
   the instructions it overwrites into a trampoline; those are stock instructions, not coincidences, however
   short. They become COPYs from their original site (about 130 to 160 per release, under 700 bytes).
3. **No near-copy** (`no_near_copy`). In no 64-byte window of a WRITE span or of the payload stream may
   4-byte matches against one stock region (stock offset minus our offset within a 32-byte band) cover half
   the window, counted on both sides (so a repeated pattern of ours matching one short stock spot again and
   again does not count). This catches a stock run with sparse edits (one byte changed every 7, say), which
   rule 1 cannot see. The exporter COPYs the matches such a window flags until none is flagged: 8 four-byte
   COPYs per release so far, from a stock parameter table (8-byte entries) that the release repacks as
   4-byte entries in the same order.
4. **Op shape** (`structure`). Every op inside the output and its source inside its stock file, no two ops
   writing the same output byte, and every FILL at least N bytes long: otherwise a later op or a 1-byte
   FILL could put stock bytes back between WRITEs.
5. **Constant runs** (8 or more of one byte value, mostly 0xFF / zero padding) are FILL ops.

What is left in WRITEs is ours, except short coincidental runs: 4 to 7 bytes that also happen to occur
somewhere in stock. Compiled Thumb code shares short sequences with any other Thumb code (a
`push {r4, lr}`, a `bx lr`, a small constant, a pointer to a stock function), and a 4-byte window matches
somewhere in 1.5 MB of firmware easily. The longer ones that sit next to patch sites are prologue and
epilogue idioms, and the printable ones are short functional labels (`Poly`, `Loop`, `1/16T`). Also left:
single bytes (and a few runs of 2 or 3) equal to stock at their own offset, where the exporter bridges a
gap of up to 3 unchanged bytes between two changed ones rather than start a new WRITE. They are reported,
not removed:

| v | WRITE bytes | COPY ops (bytes), bento1 | of them < 8 bytes | bento2 (D) | FILL bytes | runs of 4 / 5 / 6 / 7 bytes also in stock | bytes in them |
|---|---|---|---|---|---|---|---|
| 4.44 | 101,980 | 388 (4,603) | 131 (559) | 3 (24) | 13,866 | 6,692 / 1,960 / 517 / 169 | 30,988 (30.4%) |
| 4.46 | 104,857 | 443 (5,134) | 160 (677) | 3 (24) | 13,840 | 6,888 / 2,014 / 538 / 175 | 31,927 (30.4%) |

(Runs are maximal: a 6-byte run is not counted again as two 5-byte ones.)

**Why N = 8.** `export_patch.py --survey` exports with N = 4 to 16 (v4.46, every rule; COPY = bento1's;
JSON size without the checklist text):

| N | WRITE ops | WRITE bytes | COPY ops | COPY bytes | patch JSON |
|---|---|---|---|---|---|
| 4 | 6245 | 75,069 | 7013 | 33,312 | 357 KB |
| 6 | 1474 | 100,560 | 1064 | 9,041 | 181 KB |
| 8 | 826 | 104,857 | 443 | 5,134 | 161 KB |
| 12 | 746 | 106,145 | 326 | 3,920 | 159 KB |
| 16 | 732 | 106,247 | 385 | 3,818 | 160 KB |

Below 8 the COPY count climbs steeply (each step down roughly doubles it), which looks like the
coincidence tail: short sequences any two pieces of Thumb code share. From 8 up the curve is nearly
flat. One baseline supports reading the short runs as coincidence: the payload shares three 8-byte runs
with stock `bento2.bin` (85 KB of separate 1010music code from the same toolchain), all compiler idioms
such as `pop.w {..., pc}; push.w`, so same-toolchain code does produce chance matches even at 8 bytes,
and N = 8 already COPYs some coincidences. 8 bytes is two ARM words, or two to four Thumb instructions.
A stricter N = 4 is possible (2.2x the size, ~13,000 ops) and would mostly remove coincidences; the
deploy guard in `pages.yml` refuses a patch with N below 8.

## Adding a release

1. Add the release to `tools/release_hashes.json` as usual (image_commit, `device_tested: true` once
   it passed its checklist on hardware). The exporter refuses an untested release; `--allow-untested`
   exports one only to an `--out` outside `web/`, for a look, never to the site.
2. Build the kit from the release's image commit with `make_kit.py`; it must print MATCH.
3. Export, from the repo at the commit that will carry the patch (the checklist text comes from its
   `docs/checklist-v<ver>.txt`):

   ```
   python tools/export_patch.py <stock 1.6.8 folder or zip> <the kit folder bento_enhanced-v<ver>>
   ```

   It writes `web/patches/v<ver>.json` and updates `web/patches/index.json` (newest first, `default` =
   newest; edit `default` by hand to offer an older one first). It prints the stats for the table above.
   To drop an old release from the site, delete its entry from `index.json` and its `v<ver>.json`.
4. Test, with every kit you have:

   ```
   python tools/test_web_patch.py <stock> --kit <kit v4.44> --kit <kit v4.46> ...
   ```

5. Commit `web/patches/` (only `.json`: never a `.bin`, a kit or a zip; `.gitignore` ignores `*.zip`,
   kit folders and `manifest.json`). Merge to `main`, then deploy by hand (Deploying).

A new stock base (say 1.6.9) means new patches: each patch names the exact stock pair it applies to,
and the page refuses everything else.

## Tests

`tools/test_web_patch.py <stock> [--kit DIR ...]`, in the build venv (WSL: `~/bento/venv`), about 15 s
(25 s with kits):

- `web/` holds only `.html` / `.js` / `.css` / `.json` files, none of them a firmware file by hash, and every
  release offered is device-tested in `release_hashes.json`, the patch and `index.json`, with the published hash;
- (a) every committed patch applied to stock gives its published SHA-256 and, with `--kit`, make_kit's
  image; re-exporting from the kit gives the committed patch byte for byte; `--allow-untested` into `web/` is
  refused;
- (b) every rule of `check_policy` holds for every committed patch, and mutants are caught: a long COPY forced
  back to a WRITE (which still gives the right image, so a hash check alone would not notice), the same split
  over two WRITEs, as 4-byte WRITEs out of order, as WRITEs at distant offsets (only the payload stream holds
  it whole), a sparse copy (7-byte WRITEs between 1-byte FILLs, and between 1-byte COPYs: no 8-byte run, same
  image), an op overwriting another, a relocated 4-byte instruction as a WRITE, a bento2 run as a WRITE;
- (c) `web/patcher.js` under node (`node`, WSL's interop `node.exe`, or `pip install nodejs-bin`;
  skipped with a message if none): its image equals make_kit's, its zip opens with every CRC right, the
  firmware files and their SHA256SUMS lines equal make_kit's kit, a deflated 1010music-style zip works as
  input, and so do a stored zip with `BENTO1.BIN` and a `__MACOSX/._` file and a zip whose comment holds
  the end-record signature; a truncated zip is reported as damaged; refusal notes read right;
- (d) refusals: one bit flipped in `bento1.bin` or `bento2.bin`, the files swapped, a damaged payload,
  a changed payload byte (with a matching payload hash: caught by the output hash), an op out of bounds,
  a release not marked device-tested.

The node run covers `patcher.js`'s logic, not the page wiring (drag and drop, the secure-context check,
the busy state while building): no automated test covers that part. It has been checked only by loading the
page in headless Edge (it reaches "Ready"); drop, make and download have not been run by hand in a browser.

## Deploying

`.github/workflows/pages.yml` uploads `web/` as the Pages site. It runs **only by hand**
(Actions > Pages (web patcher) > Run workflow, on `main`), and does nothing until:

1. Settings > Pages > Source: **GitHub Actions**;
2. Settings > Secrets and variables > Actions > Variables: `PAGES_ENABLED` = `true`.

**Before every deploy, run `tools/test_web_patch.py` locally against stock** on the commit being deployed.
That is the only check of the zero-stock-bytes rules: CI has no stock firmware, so it cannot check them,
and it does not trust the patch's own `min_copy` as proof. What the deploy guard does check, from the
repository alone: `web/` holds only `.html` / `.js` / `.css` / `.json` files, none over 1 MB and none whose
SHA-256 is a firmware file's (stock, known or released); `index.json`'s stock hashes are
`release_hashes.json`'s; every release offered is `device_tested` in `release_hashes.json`, `index.json` and
its patch, and its `output_sha256` is the published one; N >= 8; only known op kinds.

There is deliberately no deploy on push or on a release: a push to `main` would publish whatever
`web/patches` holds before anyone ran the local check, and a release runs on a tag, which the
`github-pages` environment refuses by default (and whose `web/` may be older).

## Limits

- The page needs a secure context (https, or `http://localhost`) for WebCrypto; elsewhere (plain http on a
  LAN address) it says so and stays disabled. `fetch` does not work from `file://`. To try it locally: `cd web && python3 -m http.server`, then
  <http://localhost:8000/>.
- Opening the 1010music zip needs `DecompressionStream` with `deflate-raw` (Chrome / Edge 103+, Firefox
  113+, Safari 16.4+). Older browsers say so: extract the zip and pick the two files.
- The page depends on users being able to download 1.6.8 itself from 1010music. If 1010music stops
  offering it once a newer version ships, new users can't use the site until a release supports the new
  base; the page refuses other versions with "needs firmware 1.6.8 exactly".
- The whole image is built in memory (about 1.6 MB plus the 3.3 MB zip): fine on phones.
