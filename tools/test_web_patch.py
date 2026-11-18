# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the web patcher: tools/export_patch.py, web/patches/*.json and web/patcher.js.

    python tools/test_web_patch.py STOCK [--kit KIT_DIR ...]

STOCK: the official 1.6.8 firmware (folder, .bin or zip, as make_kit.py takes it).
--kit: a kit folder made by tools/make_kit.py (bento_enhanced-v<ver>/) from the release's image_commit; with it
the exporter is re-run and must give the committed patch byte for byte, and the browser's kit is compared with
make_kit's file for file. Without kits the committed patches are still checked against the published hashes.

  (a) round trip: every committed patch applied to stock gives its release's published sha256
      (and the make_kit image, with --kit); the exporter reproduces the committed patch (with --kit);
  (b) zero stock bytes: no WRITE window of N bytes occurs in stock, no relocated stock run is left in a WRITE,
      and mutants that put stock bytes back into WRITEs (whole, split in two, a relocated instruction) are caught;
  (c) web/patcher.js under node: the image and the kit zip, compared with make_kit's (with --kit) and with the
      stock files; the 1010music zip as input. Skipped (with a message) when there is no node;
  (d) refusals: one byte flipped in bento1.bin or bento2.bin, a damaged payload, a patched payload byte.
Exit 0 when everything run passed. Nothing is written outside a temporary folder.
"""
import argparse, base64, copy, hashlib, io, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
WEB = REPO / "web"
sys.path.insert(0, str(TOOLS))
import export_patch as ep          # noqa: E402
from make_kit import read_stock    # noqa: E402

FAILS = []


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        FAILS.append(what)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load_patches():
    idx = json.loads((WEB / "patches" / "index.json").read_text(encoding="utf-8"))
    pats = {r["version"]: json.loads((WEB / "patches" / r["file"]).read_text(encoding="utf-8")) for r in idx["releases"]}
    return idx, pats


def with_write(patch, i_op, data, split=False):
    """A copy of patch with op i_op replaced by WRITE(s) of `data` at its dst (payload spliced in place)."""
    p = copy.deepcopy(patch)
    pay = base64.b64decode(p["payload_b64"])
    pos = sum(op[2] for op in p["ops"][:i_op] if op[0] == "W")
    dst = p["ops"][i_op][1]
    new = [["W", dst, len(data)]] if not split else [["W", dst, len(data) // 2], ["W", dst + len(data) // 2, len(data) - len(data) // 2]]
    p["ops"][i_op:i_op + 1] = new
    pay = pay[:pos] + data + pay[pos:]
    p["payload_b64"], p["payload_sha256"] = base64.b64encode(pay).decode(), sha(pay)
    return p


def find_node():
    """[node command], path converter: node in PATH, node.exe through WSL interop, or the nodejs-bin package."""
    n = shutil.which("node")
    if n:
        return [n], str
    n = shutil.which("node.exe")
    if n:
        def win(p):
            return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, check=True).stdout.strip()
        return [n], (win if shutil.which("wslpath") else str)
    try:
        import nodejs  # noqa: F401  (pip install nodejs-bin)
        return [sys.executable, "-m", "nodejs"], str
    except ImportError:
        return None, None


RUNNER = r"""
const fs = require("fs");
const BEP = require(process.argv[2]);
const cfg = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const rd = (p) => new Uint8Array(fs.readFileSync(p));
const eq = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
(async () => {
  const s1 = rd(cfg.stock1), s2 = rd(cfg.stock2), res = { versions: {}, refusals: {} };
  const index = JSON.parse(fs.readFileSync(cfg.index, "utf8"));
  for (const [ver, file] of Object.entries(cfg.patches)) {
    const patch = JSON.parse(fs.readFileSync(file, "utf8"));
    const k = await BEP.buildKit(s1, s2, patch, new Date(2026, 0, 1, 12, 0, 0));
    fs.writeFileSync(cfg.out + "/bento1-" + ver + ".bin", k.image);
    fs.writeFileSync(cfg.out + "/" + k.zipName, k.zip);
    res.versions[ver] = { imageSha: k.imageSha, zipName: k.zipName };
  }
  const z = await BEP.filesFromZip(rd(cfg.stockZip));
  res.zipInput = { bento1: !!z["bento1.bin"] && eq(z["bento1.bin"], s1), bento2: !!z["bento2.bin"] && eq(z["bento2.bin"], s2) };
  res.identify = { b1: (await BEP.identify(s1, index)).role, b2: (await BEP.identify(s2, index)).role };
  const patch = JSON.parse(fs.readFileSync(cfg.patches[index.default], "utf8"));
  const tries = {
    bento1_flipped: async () => { const b = s1.slice(); b[cfg.flipAt] ^= 1;
      res.identify.flipped = (await BEP.identify(b, index)).role; return BEP.buildKit(b, s2, patch); },
    bento2_flipped: async () => { const b = s2.slice(); b[100] ^= 0x80; return BEP.buildKit(s1, b, patch); },
    swapped: async () => BEP.buildKit(s2, s1, patch),
    payload_damaged: async () => { const p = JSON.parse(JSON.stringify(patch));
      p.payload_b64 = (p.payload_b64[0] === "A" ? "B" : "A") + p.payload_b64.slice(1); return BEP.buildKit(s1, s2, p); },
    payload_byte_changed: async () => { const p = JSON.parse(fs.readFileSync(cfg.tampered, "utf8")); return BEP.buildKit(s1, s2, p); },
    op_out_of_bounds: async () => { const p = JSON.parse(JSON.stringify(patch)); p.ops.push(["F", p.output_size - 2, 4, 0]);
      return BEP.buildKit(s1, s2, p); },
  };
  for (const [name, f] of Object.entries(tries)) {
    try { await f(); res.refusals[name] = null; } catch (e) { res.refusals[name] = String(e.message); }
  }
  fs.writeFileSync(cfg.out + "/result.json", JSON.stringify(res, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stock")
    ap.add_argument("--kit", action="append", default=[], help="a make_kit kit folder (bento_enhanced-v<ver>)")
    a = ap.parse_args()
    db = json.loads((TOOLS / "release_hashes.json").read_text(encoding="utf-8"))
    stock, _ = read_stock(a.stock)
    s1, s2 = stock["bento1.bin"], stock["bento2.bin"]
    assert sha(s1) == db["stock"][db["base"]]["bento1.bin"] and sha(s2) == db["stock"][db["base"]]["bento2.bin"], "not stock"
    idx, pats = load_patches()
    kits = {}
    for k in a.kit:
        m = json.loads((Path(k) / "manifest.json").read_text(encoding="utf-8"))
        kits[m["version"]] = Path(k)

    print("== web/ holds no firmware")
    bins = [p for p in WEB.rglob("*") if p.is_file() and p.suffix.lower() in (".bin", ".zip", ".hex", ".dfu")]
    check(not bins, f"no .bin / .zip / .hex / .dfu files under web/ {bins or ''}")
    check(idx["default"] == idx["releases"][0]["version"], f"index default is the newest release (v{idx['default']})")

    print("== (a) round trip")
    for v, p in pats.items():
        rel = db["releases"][v]
        out = ep.apply_patch(s1, p)
        check(sha(out) == rel["bento1.bin"] == p["output_sha256"], f"v{v}: patch on stock gives the published sha256 {rel['bento1.bin'][:12]}")
        check(p["stock_sha256"] == db["stock"][p["base"]], f"v{v}: patch names the stock {p['base']} pair")
        if v in kits:
            check(out == (kits[v] / "01_TEST_FIRMWARE" / "bento1.bin").read_bytes(), f"v{v}: patch output == make_kit's 01_TEST_FIRMWARE/bento1.bin")
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run([sys.executable, str(TOOLS / "export_patch.py"), a.stock, str(kits[v]), "--out", td],
                                   capture_output=True, text=True)
                lf = lambda f: f.read_bytes().replace(b"\r\n", b"\n")   # noqa: E731  (a checkout with core.autocrlf)
                same = r.returncode == 0 and lf(Path(td) / f"v{v}.json") == lf(WEB / "patches" / f"v{v}.json")
                check(same, f"v{v}: export_patch.py from the make_kit image reproduces the committed patch byte for byte"
                      + ("" if r.returncode == 0 else f" (exit {r.returncode}: {r.stderr.strip()[-300:]})"))
        else:
            print(f"  skip  v{v}: no --kit for it, exporter determinism and make_kit comparison not run")

    print("== (b) zero stock bytes")
    for v, p in pats.items():
        n = p["min_copy"]
        check(ep.check_zero_stock(s1, p) == [], f"v{v}: no WRITE window of {n} bytes occurs in stock")
        check(ep.check_relocated(s1, p) == [], f"v{v}: no relocated stock run (4..{n - 1} bytes) left in a WRITE")
        hist, cov = ep.short_runs(s1, p)
        print(f"        v{v}: short runs 4..{n - 1} bytes also in stock (reported, allowed): {dict(sorted(hist.items()))}, {cov} bytes")
        long_c = next(i for i, op in enumerate(p["ops"]) if op[0] == "C" and op[3] >= 2 * n)
        _, dst, src, ln = p["ops"][long_c]
        m1 = with_write(p, long_c, s1[src:src + ln])
        check(sha(ep.apply_patch(s1, m1)) == p["output_sha256"], f"v{v} mutant: a COPY of {ln} bytes forced to WRITE still gives the right image ...")
        check(len(ep.check_zero_stock(s1, m1)) > 0, f"v{v} mutant: ... and the zero-stock-bytes check catches it")
        m2 = with_write(p, long_c, s1[src:src + ln], split=True)
        check(len(ep.check_zero_stock(s1, m2)) > 0, f"v{v} mutant: the same run split over two adjacent WRITEs is caught")
        short_c = next(i for i, op in enumerate(p["ops"]) if op[0] == "C" and op[3] < n)
        _, dst, src, ln = p["ops"][short_c]
        m3 = with_write(p, short_c, s1[src:src + ln])
        check(sha(ep.apply_patch(s1, m3)) == p["output_sha256"] and len(ep.check_relocated(s1, m3)) > 0,
              f"v{v} mutant: a relocated {ln}-byte stock instruction forced to WRITE is caught")

    print("== (c) web/patcher.js under node")
    node, conv = find_node()
    if not node:
        print("  skip  no node (install Node.js, or: python -m pip install nodejs-bin); patcher.js not run")
    else:
        ver = subprocess.run(node + ["--version"], capture_output=True, text=True).stdout.strip()
        print(f"        node {ver}: {' '.join(node)}")
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "s1.bin").write_bytes(s1)
            (td / "s2.bin").write_bytes(s2)
            with zipfile.ZipFile(td / "stock.zip", "w", zipfile.ZIP_DEFLATED) as z:   # like the 1010music download
                z.writestr("Bento 1.6.8/bento1.bin", s1)
                z.writestr("Bento 1.6.8/bento2.bin", s2)
                z.writestr("Bento 1.6.8/readme.txt", "x")
            d = idx["default"]
            pay = bytearray(base64.b64decode(pats[d]["payload_b64"]))
            pay[len(pay) // 2] ^= 0xFF                  # a changed payload byte with a matching payload hash
            t = dict(pats[d], payload_b64=base64.b64encode(bytes(pay)).decode(), payload_sha256=sha(bytes(pay)))
            (td / "tampered.json").write_text(json.dumps(t), encoding="utf-8")
            (td / "out").mkdir()
            cfg = {"stock1": conv(td / "s1.bin"), "stock2": conv(td / "s2.bin"), "stockZip": conv(td / "stock.zip"),
                   "index": conv(WEB / "patches" / "index.json"), "out": conv(td / "out"), "flipAt": 0x1000,
                   "tampered": conv(td / "tampered.json"),
                   "patches": {v: conv(WEB / "patches" / f"v{v}.json") for v in pats}}
            (td / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
            (td / "runner.js").write_text(RUNNER, encoding="utf-8")
            r = subprocess.run(node + [conv(td / "runner.js"), conv(WEB / "patcher.js"), conv(td / "cfg.json")],
                               capture_output=True, text=True)
            check(r.returncode == 0, "patcher.js ran" + ("" if r.returncode == 0 else f": {r.stderr.strip()[-800:]}"))
            if r.returncode == 0:
                res = json.loads((td / "out" / "result.json").read_text(encoding="utf-8"))
                check(res["zipInput"] == {"bento1": True, "bento2": True}, "the 1010music-style zip (deflated, in a folder) gives the stock pair")
                check(res["identify"]["b1"] == "bento1.bin" and res["identify"]["b2"] == "bento2.bin", "files recognised by sha256, not name")
                for v, p in pats.items():
                    img = (td / "out" / f"bento1-{v}.bin").read_bytes()
                    check(sha(img) == db["releases"][v]["bento1.bin"] == res["versions"][v]["imageSha"], f"v{v}: patcher.js image has the published sha256")
                    if v in kits:
                        check(img == (kits[v] / "01_TEST_FIRMWARE" / "bento1.bin").read_bytes(), f"v{v}: patcher.js image == make_kit's, byte for byte")
                    zp = td / "out" / res["versions"][v]["zipName"]
                    with zipfile.ZipFile(zp) as z:
                        check(z.testzip() is None, f"v{v}: {zp.name} opens, every CRC32 right")
                        root = p["kit"]["name"] + "/"
                        names = sorted(z.namelist())
                        want = sorted(root + f for f in ("01_TEST_FIRMWARE/bento1.bin", "01_TEST_FIRMWARE/bento2.bin",
                                      "03_RESTORE_STOCK/bento1.bin", "03_RESTORE_STOCK/bento2.bin", "DEVICE_CHECKLIST.txt",
                                      "README.txt", "SHA256SUMS.txt"))
                        check(names == want, f"v{v}: zip layout {root}{{01_TEST_FIRMWARE,03_RESTORE_STOCK,...}}")
                        rd = lambda f: z.read(root + f)   # noqa: E731
                        check(rd("01_TEST_FIRMWARE/bento1.bin") == img and rd("01_TEST_FIRMWARE/bento2.bin") == s2
                              and rd("03_RESTORE_STOCK/bento1.bin") == s1 and rd("03_RESTORE_STOCK/bento2.bin") == s2,
                              f"v{v}: zip firmware files: patched + stock bento2, stock pair to restore")
                        check(rd("DEVICE_CHECKLIST.txt").decode() == p["kit"]["checklist"], f"v{v}: DEVICE_CHECKLIST.txt is the patch's checklist")
                        sums = rd("SHA256SUMS.txt").decode()
                        lines = sums.splitlines()
                        ok = all(sha(rd(l.split("  ./", 1)[1])) == l.split("  ", 1)[0] for l in lines)
                        check(ok and len(lines) == len(want) - 1 and lines == sorted(lines, key=lambda l: l.split("  ", 1)[1].encode()),
                              f"v{v}: SHA256SUMS.txt covers every other file, right hashes, make_kit's order")
                        readme = rd("README.txt").decode()
                        check(all(s in readme for s in ("01_TEST_FIRMWARE", "ROOT of the SD card", "Long-press power", "REC",
                                                        "03_RESTORE_STOCK", "not supported on stock firmware", "Keep it private",
                                                        "Not affiliated with 1010music", "bootloader")),
                              f"v{v}: README has the install steps, the way back and the disclaimers")
                        if v in kits:
                            kd = kits[v]
                            same = all(rd(f) == (kd / f).read_bytes() for f in ("01_TEST_FIRMWARE/bento1.bin", "01_TEST_FIRMWARE/bento2.bin",
                                                                              "03_RESTORE_STOCK/bento1.bin", "03_RESTORE_STOCK/bento2.bin"))
                            check(same, f"v{v}: zip's four firmware files == make_kit kit's, byte for byte")
                            klines = {l.split("  ", 1)[1]: l for l in (kd / "SHA256SUMS.txt").read_text().splitlines()}
                            fw = [l for l in lines if ".bin" in l]
                            check(fw == [klines[l.split("  ", 1)[1]] for l in fw], f"v{v}: SHA256SUMS.txt firmware lines == make_kit's")
                            kc = (kd / "DEVICE_CHECKLIST.txt").read_bytes()
                            print(f"        v{v}: DEVICE_CHECKLIST.txt " + ("== make_kit's" if kc == rd("DEVICE_CHECKLIST.txt") else
                                  "differs from this make_kit kit's (the patch carries docs/checklist at export time; the kit, the one at image_commit)"))
                            print(f"        v{v}: the web kit has README.txt and no manifest.json (the build manifest lists stock bytes)")

                print("== (d) refusals")
                ref = res["refusals"]
                check(res["identify"].get("flipped") is None, "bento1.bin with one bit flipped is not recognised as stock")
                for name, words in (("bento1_flipped", "not the official stock"), ("bento2_flipped", "not the official stock"),
                                    ("swapped", "not the official stock"), ("payload_damaged", "damaged"),
                                    ("payload_byte_changed", "not the published"), ("op_out_of_bounds", "bad FILL op")):
                    check(ref[name] is not None and words in ref[name], f"refused: {name} ({ref[name]})")
    if not node:
        print("== (d) refusals (python reference only: no node)")
    b = bytearray(s1)
    b[0x1000] ^= 1
    check(sha(ep.apply_patch(bytes(b), pats[idx["default"]])) != pats[idx["default"]]["output_sha256"],
          "python apply on stock with one bit flipped does not give the release sha256")

    print(f"\n{len(FAILS)} failed" + (": " + "; ".join(FAILS) if FAILS else ", all passed"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
