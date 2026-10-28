# Reproducing bento-community-lab builds (WSL / Ubuntu 24.04)

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

- `build_m1.sh`: builds into `~/bento/out/m1-<timestamp>`. Passes `BE_DISABLE`,
  `BE_ENABLE`, `BE_LABEL`, `BE_TAG` through (see [`mods/m1/README.md`](../../mods/m1/README.md#build-and-test)).
- `test_m1.sh`: runs every emulated suite against the newest build.
- `package_m1.sh`: makes a flashing kit under `flash/bento_enhanced-v<version>/`. It
  contains your stock firmware: keep it private.

```
tr -d '\r' < build_m1.sh | bash        # e.g. BE_DISABLE=inta,rmidi to bisect
tr -d '\r' < test_m1.sh | bash
tr -d '\r' < package_m1.sh | bash
```
