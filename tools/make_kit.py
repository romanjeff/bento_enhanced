# SPDX-License-Identifier: GPL-3.0-or-later
"""Build bento_enhanced on your own stock Bento firmware and package a flashing kit.

    python tools/make_kit.py STOCK OUT_DIR [--test] [--set BE_KNOB=value ...] [--force]

STOCK is the official firmware you downloaded from 1010music, in any of these forms:
  - the folder holding bento1.bin and bento2.bin (or a folder with exactly one such folder inside,
    e.g. the extracted download);
  - the path of bento1.bin or bento2.bin in that folder (the other one is taken from beside it);
  - the downloaded .zip itself (bento1.bin and bento2.bin anywhere in it, once each).
Both files are checked against the known SHA-256 of the stock release this version is built on
(tools/release_hashes.json); anything else is refused before building.

OUT_DIR gets the kit, OUT_DIR/bento_enhanced-v<version>/ (-custom added for a --set build without BE_TAG):
  01_TEST_FIRMWARE/   bento1.bin (this build) + bento2.bin (stock, unchanged)
  03_RESTORE_STOCK/   your stock bento1.bin + bento2.bin, to go back
  manifest.json       what the image contains
  DEVICE_CHECKLIST.txt  docs/checklist-v<version>.txt
  SHA256SUMS.txt      the hash of every file in the kit
The kit holds 1010music's firmware: it is for your own Bento only. Never share or upload it.
The build itself goes to OUT_DIR/build-<time>/ and is deleted once the kit is packaged, unless
--keep-build or a suite failed (then it stays; it holds a patched image too, so it is just as private).
A MISMATCH (a default build whose image is not the published one) packages no kit at all.

A default build (no --set) prints whether the built bento1.bin matches the published reference
hash for this version. --set passes a build knob (BE_DISABLE, BE_ENABLE, BE_LABEL, BE_TAG,
BE_WT_MIP; see mods/m1/README.md) to build.py; any BE_* variable already in your environment is
ignored unless given with --set, so a stray variable can never change a default build.
--test runs the emulated test suites against the build (15 to 25 minutes; test_analog is most of it).

Pure Python, no shell: the same on Linux, macOS, WSL and Windows.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
M1 = REPO / "mods" / "m1"
HASHES = Path(__file__).resolve().parent / "release_hashes.json"
NAMES = ("bento1.bin", "bento2.bin")
KNOBS = ("BE_DISABLE", "BE_ENABLE", "BE_LABEL", "BE_TAG", "BE_WT_MIP")

# The emulated suites and the feature groups each needs in the image (as tools/lab-build-wsl/test_m1.sh):
# a build that leaves a group out (manifest "omitted") skips the suites that need it.
SUITES = [("test_diag.py", ""), ("test_polyat.py", "poly_cap"), ("test_conditions.py", "cond_engine"),
          ("test_holdstep.py", "step"), ("test_controls.py", "hold"), ("test_retrig.py", "retrig"),
          ("test_step.py", "step"), ("test_mod.py", "retrig"), ("test_notemod.py", "nmod retrig"),
          ("test_notemod_route.py", "nmod retrig"), ("test_notemod_real.py", "step nmod"), ("test_slice.py", ""),
          ("test_s3_m7.py", "poly_cap retrig cond_engine nmod"), ("test_step_xml.py", "step"), ("test_keys.py", ""),
          ("test_analog.py", "analog"), ("test_inst_xml.py", "analog"), ("test_synth_ui.py", "analog"),
          ("test_handles.py", "analog"), ("test_note0.py", "poly_cap retrig cond_engine nmod"),
          ("test_drums.py", "drums"), ("test_drum_ui.py", "drums")]


def fail(msg):
    sys.stdout.flush()
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def lf(b):
    """Text files go into the kit with LF line endings, whatever the checkout (core.autocrlf) or the OS
    (Python's text mode in build.py) wrote, so a kit's SHA256SUMS is the same on Windows as on Linux."""
    return b.replace(b"\r\n", b"\n")


def planned_version(base):
    """The version build.py gives a build on this base without BE_TAG / BE_WT_MIP (its BASE_SETTINGS
    line), read from the source so an existing kit is refused before building. None if not found:
    the check after the build still applies."""
    src = (M1 / "build.py").read_text(encoding="utf-8")
    m = re.search(r'"stock_%s":\s*dict\(version="([^"]+)"' % re.escape(base.replace(".", "")), src)
    return m.group(1) if m else None


def kit_name(version, custom):
    """bento_enhanced-v<version>; a --set build without BE_TAG gets -custom, so it can never be
    mistaken for (or --force over) the verified default kit."""
    return f"bento_enhanced-v{version}" + ("-custom" if custom and "BE_TAG" not in custom else "")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read_stock(path):
    """The two stock files' bytes from a folder, one of the .bin files, or the download's .zip."""
    p = Path(path).expanduser()
    if not p.exists():
        fail(f"{p} does not exist. Give the folder (or .zip) of the stock firmware you downloaded from 1010music.")
    if p.is_file() and zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            found = {}
            for n in NAMES:
                m = [i for i in z.infolist() if not i.is_dir() and i.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == n]
                if len(m) != 1:
                    fail(f"{p}: expected one {n} in the zip, found {len(m)}. Extract it and give the folder instead.")
                found[n] = z.read(m[0])
            return found, f"{p} (zip)"
    if p.is_file():
        if p.name.lower() not in NAMES:
            fail(f"{p} is not bento1.bin, bento2.bin or a .zip.")
        p = p.parent
    if not all((p / n).is_file() for n in NAMES):
        subs = [d for d in p.iterdir() if d.is_dir() and all((d / n).is_file() for n in NAMES)]
        if len(subs) != 1:
            fail(f"{p}: no bento1.bin and bento2.bin here (nor in exactly one folder inside it).")
        p = subs[0]
    return {n: (p / n).read_bytes() for n in NAMES}, str(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stock", help="the stock firmware: folder, bento1.bin / bento2.bin, or the download's .zip")
    ap.add_argument("out", help="output folder (created if missing): the build and the kit go in here")
    ap.add_argument("--test", action="store_true", help="also run the emulated test suites (15 to 25 minutes)")
    ap.add_argument("--set", action="append", default=[], metavar="BE_KNOB=value", help="a build knob (developers)")
    ap.add_argument("--force", action="store_true", help="replace an existing kit folder of the same version")
    ap.add_argument("--keep-build", action="store_true", help="keep the build folder (build-<time>/) after packaging")
    a = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)   # piped (| tee) output keeps its order with stderr's ERROR lines

    if a.test:
        try:
            import unicorn, numpy, PIL  # noqa: F401  (the suites' packages)
        except ImportError as e:
            fail(f"--test needs the test packages ({e.name} is missing): python -m pip install -r requirements-test.txt")
    if sys.version_info < (3, 12):
        print(f"warning: Python {sys.version.split()[0]}; this is tested with Python 3.12", file=sys.stderr)
    db = json.loads(HASHES.read_text(encoding="utf-8"))
    base = db["base"]
    want = db["stock"][base]

    custom = {}
    for s in a.set:
        k, eq, v = s.partition("=")
        if not eq or k not in KNOBS:
            fail(f"--set {s}: expected one of {', '.join(KNOBS)} as KNOB=value")
        custom[k] = v
    out = Path(a.out).expanduser().resolve()
    # An existing kit is refused now, not after a minute of building (when its name is predictable).
    pv = planned_version(base) if not {"BE_TAG", "BE_WT_MIP"} & set(custom) else None
    if pv and (out / kit_name(pv, custom)).exists() and not a.force:
        fail(f"{out / kit_name(pv, custom)} already exists: move it away, or pass --force to replace it.")

    # 1. The stock firmware: both files must be the exact official release this version patches.
    stock, where = read_stock(a.stock)
    print(f"stock firmware: {where}")
    bad = []
    for n in NAMES:
        got = sha(stock[n])
        ok = got == want[n]
        print(f"  {n}  sha256 {got}  {'OK (stock ' + base + ')' if ok else 'NOT stock ' + base}")
        if not ok:
            other = [v for v, h in db["stock"].items() if h.get(n) == got]
            bad.append(f"{n} is stock {other[0]}, not {base}" if other else f"{n} is not the official {base} file")
    if bad:
        fail("; ".join(bad) + f".\nThis version builds only on the official Bento {base} firmware: download that "
             "exact version from 1010music and give its folder (or zip), unmodified.")

    # 2. Build (mods/m1/build.py), with only the knobs given here.
    env = {k: v for k, v in os.environ.items() if not k.startswith("BE_")}
    env.update(custom)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = out / f"build-{stamp}"
    base_bin = out / f"stock-{base}-bento1.bin.tmp"     # build.py reads the image from a path
    base_bin.write_bytes(stock["bento1.bin"])
    try:
        print(f"\nbuilding into {bdir} ..." + (f" knobs {custom}" if custom else " (default build)"), flush=True)
        r = subprocess.run([sys.executable, str(M1 / "build.py"), "--base", str(base_bin), "--out", str(bdir)],
                           cwd=str(M1), env=env)
    finally:
        base_bin.unlink()
    if r.returncode != 0:
        fail(f"build.py failed (exit {r.returncode}); see its output above. Nothing was packaged; "
             f"{bdir} is kept for diagnosis (private: it may hold a patched image).")
    man_bytes = (bdir / "manifest.json").read_bytes()
    man = json.loads(man_bytes)
    img = (bdir / "bento1.bin").read_bytes()
    if sha(img) != man["sha256"] or man["base_sha256"] != want["bento1.bin"]:
        fail("the build's manifest does not match its image or base")
    version = man["version"]

    # 3. Compare with the published reference. A MISMATCH packages no kit, so there is nothing to flash.
    got = man["sha256"]
    ref = db["releases"].get(version)
    verdict = None if custom or ref is None else got == ref["bento1.bin"]
    if verdict is False:
        print(f"\nbuilt {man['label']}")
        print(f"  bento1.bin sha256 {got}")
        print(f"  reference  sha256 {ref['bento1.bin']}")
        print("  MISMATCH: this is not the published image.")
        fail(f"MISMATCH: no kit was packaged. Don't flash anything from {bdir} (kept for diagnosis, private). "
             "See BUILDING.md, Troubleshooting.")

    # 4. Package the kit.
    kit = out / kit_name(version, custom)
    if kit.exists():
        if not a.force:
            fail(f"{kit} already exists: move it away, or pass --force to replace it.")
        shutil.rmtree(kit)
    (kit / "01_TEST_FIRMWARE").mkdir(parents=True)
    (kit / "03_RESTORE_STOCK").mkdir()
    (kit / "01_TEST_FIRMWARE" / "bento1.bin").write_bytes(img)
    (kit / "01_TEST_FIRMWARE" / "bento2.bin").write_bytes(stock["bento2.bin"])
    for n in NAMES:
        (kit / "03_RESTORE_STOCK" / n).write_bytes(stock[n])
    (kit / "manifest.json").write_bytes(lf(man_bytes))
    checklist = REPO / "docs" / f"checklist-v{version.split('-')[0]}.txt"
    if checklist.is_file():
        (kit / "DEVICE_CHECKLIST.txt").write_bytes(lf(checklist.read_bytes()))
    else:
        print(f"note: no {checklist.relative_to(REPO)}; the kit has no DEVICE_CHECKLIST.txt")
    # A build with the drum synths in it needs their carrier WAVs on the card (folder "Drum Synths BE").
    if "dr_render" in man.get("dsp_symbols", {}) and "drums" not in man.get("omitted", []) + man.get("disabled", []):
        r = subprocess.run([sys.executable, "-I", str(REPO / "tools" / "carriers" / "gen_carriers.py"), str(kit / "Drum Synths BE")])
        if r.returncode != 0:
            fail("gen_carriers.py failed")
    files = sorted((f for f in kit.rglob("*") if f.is_file() and f.name != "SHA256SUMS.txt"),
                   key=lambda f: ("./" + f.relative_to(kit).as_posix()).encode())
    sums = "".join(f"{sha(f.read_bytes())}  ./{f.relative_to(kit).as_posix()}\n" for f in files)
    (kit / "SHA256SUMS.txt").write_bytes(sums.encode())

    def report():
        print(f"\nkit: {kit}")
        print(f"built {man['label']}")
        print(f"  01_TEST_FIRMWARE/bento1.bin sha256 {got}")
        if custom:
            print("  custom build (--set): there is no published reference hash to compare with.")
        elif ref is None:
            print(f"  no published reference hash for v{version} in tools/release_hashes.json.")
        else:
            print(f"  reference      sha256 {ref['bento1.bin']}")
            print("  MATCH: byte for byte the image the author tested on hardware.")
    report()

    # 5. Optional: the emulated suites.
    failed = []
    if a.test:
        omitted = set(man.get("omitted", []))
        base_path = kit / "03_RESTORE_STOCK" / "bento1.bin"
        for t, needs in SUITES:
            miss = [g for g in needs.split() if g in omitted]
            if miss:
                print(f"== {t}: not run, this build leaves out {' '.join(miss)}")
                continue
            print(f"== {t}", flush=True)
            r = subprocess.run([sys.executable, t, str(base_path), str(bdir)], cwd=str(M1), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            print("\n".join(r.stdout.rstrip().splitlines()[-4:]))
            if r.returncode != 0:
                failed.append(t)
        print(f"\ntests: {len(failed)} suite(s) failed: {' '.join(failed)}" if failed else "\ntests: every suite run passed")
        report()   # the verdict again, so it is the last thing on screen after the suites' output
    if failed or a.keep_build:
        print(f"\nbuild folder kept: {bdir} (private: it holds a patched image; delete it when done)")
    else:
        shutil.rmtree(bdir)
    print("\nThe kit contains 1010music's firmware: keep it to yourself, never share or upload it.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
