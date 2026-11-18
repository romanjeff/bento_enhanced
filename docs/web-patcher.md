# The web patcher

`web/` is a single static page that makes the same flashing kit as `tools/make_kit.py`, in the browser,
from the user's own stock firmware, with nothing to install. It is meant for GitHub Pages
(`.github/workflows/pages.yml`, inert until enabled: see Deploying).

The site never holds 1010music's bytes. Per release it holds a **patch**: the bytes this project
authored plus instructions to copy everything else out of the user's own stock `bento1.bin`. The
result is checked against the release's published SHA-256 before anything can be downloaded, so the
page can only ever hand out the exact image `make_kit.py` builds (and the author tested).

| file | what |
|---|---|
| `web/index.html`, `web/style.css` | the page (no external scripts, fonts or CDNs; CSP allows only the site itself) |
| `web/patcher.js` | all the logic, plain JS, no build step; the same file runs under node for the tests |
| `web/patches/index.json` | the releases offered (newest first, `default` = newest), the stock hashes, known hashes for messages |
| `web/patches/v<ver>.json` | one release's patch |
| `tools/export_patch.py` | makes a patch from stock + the release image, self-verifies it |
| `tools/test_web_patch.py` | the tests (below) |

## What the page does

1. The user drops (or picks) `bento1.bin` and `bento2.bin`, or the 1010music download's zip
   (stored or deflated entries, in any folder; deflate uses the browser's `DecompressionStream`).
2. Each file is identified by its SHA-256 (WebCrypto), never its name. Anything but the exact stock
   1.6.8 pair is refused, with a reason when the hash is known (stock 1.6.1, an already-modded
   image).
3. The user picks a release (default: the newest). All patches are fetched when the page loads, so
   from then on it works offline.
4. The page checks the stock hashes again, the patch payload's SHA-256, applies the ops, and checks
   the result's SHA-256 against the release's. Any mismatch stops it: nothing is offered.
5. It offers the patched `bento1.bin` alone, and a zip kit (written by `patcher.js` itself, stored, with
   CRC-32) laid out like make_kit's:

```
bento_enhanced-v<ver>/
  01_TEST_FIRMWARE/bento1.bin   the patched image          (== make_kit's, byte for byte)
  01_TEST_FIRMWARE/bento2.bin   the user's stock bento2.bin
  03_RESTORE_STOCK/bento1.bin   the user's stock pair
  03_RESTORE_STOCK/bento2.bin
  DEVICE_CHECKLIST.txt          docs/checklist-v<ver>.txt, carried in the patch
  README.txt                    install steps, the way back, disclaimers (web kit only)
  SHA256SUMS.txt                make_kit's format and order
```

The one deliberate difference: **no `manifest.json`**. The build manifest's `writes` list has the
`original_hex` of every patch site, which is stock bytes, so it is never published. The web kit has
`README.txt` instead.

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
  "ops": [["W", 1206, 4], ["C", dst, src, len], ["F", dst, len, 0], ...],
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
- `["F", dst, len, byte]`: `len` copies of `byte`.

An op out of bounds rejects the whole patch. The payload is one base64 string with its own SHA-256 (a
damaged download is caught before applying). The reference implementation is
`tools/export_patch.py apply_patch`; `web/patcher.js applyPatch` is the same thing and the tests hold
them to the same output.

## The zero-stock-bytes rule

The policy: **the published patch contains no stock content.** Concretely, `export_patch.py`
enforces, and re-checks before it writes anything:

1. **No WRITE holds a run of N = 8 or more bytes that occurs anywhere in stock `bento1.bin`.** Any such
   run, at any offset (not only the same offset), becomes a COPY from the user's file. Adjacent WRITEs
   are checked as one span, so splitting a run across two WRITEs cannot hide it.
2. **No WRITE holds a run of 4 to 7 bytes equal to stock bytes the release overwrote.** When the mod
   hooks a function it moves the instructions it overwrites into a trampoline; those are stock
   instructions, not coincidences, however short. They become COPYs from their original site (about
   100 per release, ~400 bytes).
3. **Constant runs** (8 or more of one byte value, mostly zero padding) are FILL ops.

What is left in WRITEs is ours, except short coincidental runs: 4 to 7 bytes that also happen to occur
somewhere in stock. Compiled Thumb code shares short sequences with any other Thumb code (a
`push {r4, lr}`, a `bx lr`, a small constant, a pointer to a stock function); a 4-byte window matches
somewhere in 1.5 MB of firmware easily. They are reported, not removed:

| v | WRITE bytes | COPY ops (bytes) | of them relocated, < 8 bytes | FILL bytes | runs of 4 / 5 / 6 / 7 bytes also in stock | bytes in them |
|---|---|---|---|---|---|---|
| 4.44 | 102,190 | 349 (4,417) | 92 (373) | 13,866 | 6,745 / 1,972 / 522 / 173 | 31,229 (30.6%) |
| 4.46 | 105,153 | 383 (4,862) | 100 (405) | 13,840 | 6,965 / 2,045 / 545 / 179 | 32,301 (30.7%) |

(Runs are maximal: a 6-byte run is not counted again as two 5-byte ones.)

**Why N = 8.** `export_patch.py --survey` exports with N = 4 to 16 (v4.46, rules 1 to 3; JSON size
without the checklist text):

| N | WRITE ops | WRITE bytes | COPY ops | COPY bytes | patch JSON |
|---|---|---|---|---|---|
| 4 | 6083 | 76,084 | 7114 | 33,765 | 351 KB |
| 6 | 1386 | 101,017 | 1030 | 8,944 | 178 KB |
| 8 | 779 | 105,153 | 383 | 4,862 | 159 KB |
| 12 | 710 | 106,567 | 231 | 3,498 | 157 KB |
| 16 | 719 | 107,069 | 190 | 2,996 | 156 KB |

Below 8 the COPY count climbs steeply (each step down roughly doubles it), which looks like the
coincidence tail: short sequences any two pieces of Thumb code share. From 8 up the curve is nearly
flat, which suggests the 8-plus matches are mostly real shared content, and those are all COPYed.
(This is read off the curve; no baseline against unrelated ARM code was measured.) 8 bytes is two
ARM words, or two to four Thumb instructions: long enough that a chance match
against 1.5 MB of firmware is rare, short enough to catch copied table entries and instruction pairs. Rule 2 covers the one known source of genuine stock bytes shorter
than that. A stricter N = 4 is possible (2.2x the size, ~13,000 ops) and would mostly remove
coincidences; the CI guard in `pages.yml` refuses a patch with N below 8.

## Adding a release

1. Add the release to `tools/release_hashes.json` as usual (image_commit, `device_tested: true` once
   it passed its checklist on hardware; the exporter refuses an untested release without
   `--allow-untested`).
2. Build the kit from the release's image commit with `make_kit.py`; it must print MATCH.
3. Export, from the repo at the commit that will carry the patch (the checklist text comes from its
   `docs/checklist-v<ver>.txt`):

   ```
   python tools/export_patch.py <stock 1.6.8 folder or zip> <the kit folder bento_enhanced-v<ver>>
   ```

   It writes `web/patches/v<ver>.json` and updates `web/patches/index.json` (newest first, `default` =
   newest; edit `default` by hand to offer an older one first). It prints the stats for the table above.
4. Test, with every kit you have:

   ```
   python tools/test_web_patch.py <stock> --kit <kit v4.44> --kit <kit v4.46> ...
   ```

5. Commit `web/patches/` (only `.json`: never a `.bin`, a kit or a zip). The Pages workflow deploys on
   the next push to `main` that touches `web/`, or on a published release.

A new stock base (say 1.6.9) means new patches: each patch names the exact stock pair it applies to,
and the page refuses everything else.

## Tests

`tools/test_web_patch.py <stock> [--kit DIR ...]`, in the build venv (WSL: `~/bento/venv`):

- (a) every committed patch applied to stock gives its published SHA-256 and, with `--kit`, make_kit's
  image; re-exporting from the kit gives the committed patch byte for byte;
- (b) both rules hold for every committed patch, and mutants are caught: a 16-byte COPY forced back to a
  WRITE (which still gives the right image, so a hash check alone would not notice), the same split
  over two WRITEs, a relocated 4-byte instruction as a WRITE;
- (c) `web/patcher.js` under node (`node`, WSL's interop `node.exe`, or `pip install nodejs-bin`;
  skipped with a message if none): its image equals make_kit's, its zip opens with every CRC right, the
  firmware files and their SHA256SUMS lines equal make_kit's kit, and a deflated 1010music-style zip
  works as input;
- (d) refusals: one bit flipped in `bento1.bin` or `bento2.bin`, the files swapped, a damaged payload,
  a changed payload byte (with a matching payload hash: caught by the output hash), an op out of bounds.

## Deploying

`.github/workflows/pages.yml` uploads `web/` as the Pages site. It does nothing until:

1. Settings > Pages > Source: **GitHub Actions**;
2. Settings > Secrets and variables > Actions > Variables: `PAGES_ENABLED` = `true`.

It then runs on a push to `main` touching `web/`, on a published release, or by hand. Before uploading
it refuses `.bin` / `.zip` / `.hex` / `.dfu` / `.elf` files and anything over 1 MB under `web/`, and
checks each patch against `index.json`. Deploying on a release runs from the release's tag: the
`github-pages` environment allows only the default branch out of the box, so add a tag rule
(Settings > Environments > github-pages) or drop the `release` trigger.

## Limits

- The page needs a secure context (https, or `http://localhost`) for WebCrypto, and `fetch` does not
  work from `file://`. To try it locally: `cd web && python3 -m http.server`, then
  <http://localhost:8000/>.
- Opening the 1010music zip needs `DecompressionStream` (Chrome / Edge 80+, Firefox 113+, Safari
  16.4+). Older browsers: extract the zip and pick the two files.
- The whole image is built in memory (about 1.6 MB plus the 3.3 MB zip): fine on phones.
