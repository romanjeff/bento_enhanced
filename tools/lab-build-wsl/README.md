# Reproducing bento-community-lab builds (WSL / Ubuntu 24.04)

> **Dev-only.** These scripts are the author's own setup (hard-coded `/mnt/c/Users/...` and
> `~/bento` paths, Lab's requirements). To build bento_enhanced you don't need any of them:
> follow [`BUILDING.md`](../../BUILDING.md) (`requirements.txt` + `tools/make_kit.py`).

Lab's build assumes a POSIX host with Python 3.12, `cc`/`g++` and git. Ubuntu
24.04 ships Python 3.12.3, so WSL is the low-friction route on Windows.

1. `setup_lab_build.sh`: one-time setup. It installs build-essential and
   python3.12-venv, clones Lab into `~/bento/lab`, copies your stock 1.6.1
   pair into `~/bento/stock-1.6.1` (edit the `/mnt/c/...` source path), and
   creates a venv with Lab's pinned requirements.
2. `build_ch10.sh`: an independent CH10 / 1.6HA rebuild with the full test
   suite, then compares the output against Lab's pinned hash in
   `tools/release_targets.json`.

Run with CRLF stripped if the files were checked out on Windows:
`tr -d '\r' < build_ch10.sh | bash`.

Verified 2026-10-25: K16 baseline `5e72971b…` and CH10 `0e850706…`
reproduced byte for byte, 361/361 checks passed.

Caveat: the public repo has only `main`. The `release/*` refs pinned in
`release_targets.json` aren't published, so `prepare_tester.py` can't resolve
them, and older builders (e.g. `build_eu10.py`) run against current sources
and fail. Build the target that matches `main`.

## bento_enhanced (mods/m1)

Uses the same venv and `~/bento/stock-1.6.1` from step 1. The scripts hard-code this
checkout's `/mnt/c/...` path: edit it for yours.

- `build_m1.sh`: builds into `~/bento/out/m1-<timestamp>`. `BENTO_BASE=1.6.1|1.6.8` picks
  the stock image (`~/bento/stock-<base>`, default 1.6.1). Passes `BE_DISABLE`,
  `BE_ENABLE`, `BE_LABEL`, `BE_TAG` through (see [`mods/m1/README.md`](../../mods/m1/README.md#build-and-test)).
- `test_m1.sh`: runs the emulated suites against the newest build, on that build's base
  (manifest `base_label`). A build that leaves feature groups out (manifest `omitted`, e.g. the
  1.6.8 S3 image) runs only the suites whose groups it contains: the `NEEDS` table in the script
  says which groups each suite needs, and a skipped suite prints `not run: ...` with the reason.
  Add a new suite there.
- `package_m1.sh`: makes a flashing kit under `flash/bento_enhanced-v<version>/`, with the
  stock pair of the build's own base (checked against the manifest's `base_sha256`). It
  contains your stock firmware: keep it private.
- `build_m1_off.sh`: a bisect build with every feature group off (logging on), label BEoff, tag off.

These are the copies the S1-S5 checks ran (the working copies live in `bento_mods/wsl/`; keep
the two the same).

```
tr -d '\r' < build_m1.sh | bash        # e.g. BE_DISABLE=inta,rmidi to bisect
tr -d '\r' < test_m1.sh | bash
tr -d '\r' < package_m1.sh | bash
```
