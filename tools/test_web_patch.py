# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the web patcher: tools/export_patch.py, web/patches/*.json and web/patcher.js.

    python tools/test_web_patch.py STOCK [--kit KIT_DIR ...]

STOCK: the official 1.6.8 firmware (folder, .bin or zip, as make_kit.py takes it).
--kit: a kit folder made by tools/make_kit.py (bento_enhanced-v<ver>/) from the release's image_commit; with it
the exporter is re-run and must give the committed patch byte for byte, and the browser's kit is compared with
make_kit's file for file. Without kits the committed patches are still checked against the published hashes.

  (a) round trip: every committed patch applied to stock gives its release's published sha256
      (and the make_kit image, with --kit); the exporter reproduces the committed patch (with --kit);
      every offered release is device-tested in tools/release_hashes.json;
  (b) zero stock bytes: every check of export_patch.check_policy is clean for every committed patch, and mutants
      that put stock bytes back are caught: a COPY forced to WRITE (whole, split in two, split in 4-byte pieces
      out of order, scattered over distant offsets), sparse edits (7-byte WRITEs between 1-byte FILLs or COPYs),
      a relocated instruction, a bento2.bin run, overlapping ops;
  (c) web/patcher.js under node: the image and the kit zip, compared with make_kit's (with --kit) and with the
      stock files; the 1010music zip as input, and damaged zips. Skipped (with a message) when there is no node;
  (d) refusals: one byte flipped in bento1.bin or bento2.bin, a damaged payload, a patched payload byte, an
      untested release.
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


def with_ops(patch, i_op, new_ops, data=b""):
    """A copy of patch with op i_op replaced by new_ops, whose WRITEs take `data` (in op order) from the payload
    position where op i_op was."""
    p = copy.deepcopy(patch)
    pay = base64.b64decode(p["payload_b64"])
    pos = sum(op[2] for op in p["ops"][:i_op] if op[0] == "W")
    p["ops"][i_op:i_op + 1] = new_ops
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
  const modded = rd(cfg.out + "/bento1-" + index.default + ".bin");
  res.identify.moddedNote = (await BEP.identify(modded, index)).note;
  res.identify.otherNote = (await BEP.identify(new Uint8Array([1, 2, 3]), index)).note;
  res.zipEdge = {};
  for (const [name, f] of Object.entries(cfg.edgeZips)) {
    try { const z = await BEP.filesFromZip(rd(f)); res.zipEdge[name] = { ok: Object.keys(z).sort(), b1: !!z["bento1.bin"] && eq(z["bento1.bin"], s1) }; }
    catch (e) { res.zipEdge[name] = { error: String(e.message) }; }
  }
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
    untested: async () => { const p = JSON.parse(JSON.stringify(patch)); p.device_tested = false; return BEP.buildKit(s1, s2, p); },
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

    print("== web/ holds no firmware, and offers only device-tested releases")
    other = [str(p) for p in WEB.rglob("*") if p.is_file() and p.suffix.lower() not in (".html", ".js", ".css", ".json")]
    check(not other, f"web/ holds only .html / .js / .css / .json files {other or ''}")
    fw = {sha(s1), sha(s2)} | {r["bento1.bin"] for r in db["releases"].values()} | set(idx["known_sha256"])
    check(not [p for p in WEB.rglob("*") if p.is_file() and sha(p.read_bytes()) in fw], "no file in web/ is a firmware file (by sha256)")
    check(idx["default"] == idx["releases"][0]["version"], f"index default is the newest release (v{idx['default']})")
    for v, p in pats.items():
        rel = db["releases"].get(v, {})
        r = next(r for r in idx["releases"] if r["version"] == v)
        check(rel.get("device_tested") is True and p["device_tested"] is True and r["device_tested"] is True
              and p["output_sha256"] == rel.get("bento1.bin") == r["output_sha256"],
              f"v{v}: device-tested in release_hashes.json, the patch and index.json; sha256 is the published one")

    print("== (a) round trip")
    for v, p in pats.items():
        rel = db["releases"][v]
        out = ep.apply_patch(s1, p, s2)
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
            with tempfile.TemporaryDirectory() as td:
                r = subprocess.run([sys.executable, str(TOOLS / "export_patch.py"), a.stock, str(kits[v]), "--allow-untested"],
                                   capture_output=True, text=True)
                check(r.returncode != 0 and "outside web/" in r.stderr, f"v{v}: export --allow-untested into web/ is refused")
        else:
            print(f"  skip  v{v}: no --kit for it, exporter determinism and make_kit comparison not run")

    print("== (b) zero stock bytes")
    for v, p in pats.items():
        n = p["min_copy"]
        out = ep.apply_patch(s1, p, s2)
        pol = ep.check_policy(stock, p, out)
        for rule, bad in pol.items():
            check(bad == [], f"v{v}: {rule}: clean" + (f" ({len(bad)} violations, e.g. {bad[:2]})" if bad else ""))
        hist, cov = ep.short_runs(s1, p)
        print(f"        v{v}: short runs 4..{n - 1} bytes also in stock (reported, allowed): {dict(sorted(hist.items()))}, {cov} bytes")
        right = lambda m: sha(ep.apply_patch(s1, m, s2)) == p["output_sha256"]   # noqa: E731
        # the longest COPY from bento1: the stock run the mutants put back
        i_c = max((i for i, op in enumerate(p["ops"]) if op[0] == "C"), key=lambda i: p["ops"][i][3])
        _, dst, src, ln = p["ops"][i_c]
        run = s1[src:src + ln]
        check(ln >= 64, f"v{v}: the longest COPY ({ln} bytes) is long enough for every mutant")
        m = with_write(p, i_c, run)
        check(right(m) and ep.check_zero_stock(stock, m), f"v{v} mutant: a COPY of {ln} bytes forced to WRITE (same image) is caught")
        m = with_write(p, i_c, run, split=True)
        check(right(m) and ep.check_zero_stock(stock, m), f"v{v} mutant: the same run split over two adjacent WRITEs is caught")
        q = ln // 8 * 8                                              # an even number of 4-byte pieces
        order = [j ^ 1 for j in range(q // 4)]                      # 4-byte pieces, each pair swapped
        new = [["W", dst + 4 * j, 4] for j in order] + ([["C", dst + q, src + q, ln - q]] if ln > q else [])
        m = with_ops(p, i_c, new, b"".join(run[4 * j:4 * j + 4] for j in order))
        check(right(m) and ep.check_zero_stock(stock, m), f"v{v} mutant: the run as 4-byte WRITEs out of order (same image) is caught")
        new = [["W", 0x100 + 0x20000 * j, 4] for j in range(4)]    # scattered: only the payload holds it whole
        m = with_ops(p, i_c, [p["ops"][i_c]] + new, run[:16])
        check(ep.check_zero_stock(stock, m), f"v{v} mutant: 16 stock bytes as four WRITEs at distant offsets are caught (payload stream)")
        def sparse(x, gap_kind, ln=128):
            """Stock bytes x..x+ln written back in place (an unpatched region: same image) as 7-byte WRITEs, every
            8th byte by a 1-byte FILL or COPY: the payload holds the region minus every 8th byte."""
            new = []
            for j in range(0, ln, 8):
                new.append(["W", x + j, 7])
                new.append(["F", x + j + 7, 1, s1[x + j + 7]] if gap_kind == "F" else ["C", x + j + 7, x + j + 7, 1])
            m = copy.deepcopy(p)
            pay = base64.b64decode(m["payload_b64"]) + b"".join(s1[x + j:x + j + 7] for j in range(0, ln, 8))
            m["ops"] += new
            m["payload_b64"], m["payload_sha256"] = base64.b64encode(pay).decode(), sha(pay)
            return m
        # Stock code where the sparse version has no 8-byte stock run, so rule 1 alone misses it (in a repetitive
        # table the dropped bytes are redundant and rule 1 fires too), in a region the release leaves unchanged.
        x_sp = next((x for x in range(0x20000, 0x80000, 0x2000) if out[x:x + 128] == s1[x:x + 128]
                     and not ep.check_zero_stock(stock, sparse(x, "C"))), None)
        check(x_sp is not None, f"v{v}: an unpatched stock region whose sparse copy rule 1 misses exists (for the next mutants)")
        if x_sp is not None:
            for gap_kind in ("F", "C"):
                m = sparse(x_sp, gap_kind)
                pol = ep.check_policy(stock, m)
                caught = pol["structure"] if gap_kind == "F" else pol["no_near_copy"]
                check(right(m) and not pol["no_stock_run"] and caught,
                      f"v{v} mutant: sparse copy of stock 0x{x_sp:x} (128 bytes), 7-byte WRITEs between 1-byte "
                      f"{'FILLs' if gap_kind == 'F' else 'COPYs'} (same image, no 8-byte run) is caught by "
                      f"{'structure' if gap_kind == 'F' else 'no_near_copy'}")
        m = copy.deepcopy(p)
        m["ops"].append(["C", dst, src, 4])                          # a second op over the same bytes
        check(right(m) and ep.check_structure(m), f"v{v} mutant: an op overwriting an earlier one is caught")
        i_s = next(i for i, op in enumerate(p["ops"]) if op[0] == "C" and op[3] < n)
        _, dst, src, ln = p["ops"][i_s]
        m = with_write(p, i_s, s1[src:src + ln])
        check(right(m) and ep.check_relocated(s1, m, stock2=s2), f"v{v} mutant: a relocated {ln}-byte stock instruction forced to WRITE is caught")
        i_d = next((i for i, op in enumerate(p["ops"]) if op[0] == "D"), None)
        if i_d is not None:
            _, dst, src, ln = p["ops"][i_d]
            m = with_write(p, i_d, s2[src:src + ln])
            check(right(m) and any(b[1] == "bento2.bin" for b in ep.check_zero_stock(stock, m)),
                  f"v{v} mutant: a {ln}-byte bento2.bin run forced to WRITE is caught")
        else:
            print(f"        v{v}: no bento2 COPY (D) op, bento2 mutant not run")

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
            good = (td / "stock.zip").read_bytes()
            cmt = b"PK\x05\x06 is in this comment"
            (td / "comment.zip").write_bytes(good[:-2] + len(cmt).to_bytes(2, "little") + cmt)
            (td / "trunc.zip").write_bytes(good[:len(good) // 2] + good[-22:])
            with zipfile.ZipFile(td / "stored.zip", "w", zipfile.ZIP_STORED) as z:
                z.writestr("x/BENTO1.BIN", s1)
                z.writestr("__MACOSX/x/._bento1.bin", b"\0" * 100)
            cfg = {"stock1": conv(td / "s1.bin"), "stock2": conv(td / "s2.bin"), "stockZip": conv(td / "stock.zip"),
                   "index": conv(WEB / "patches" / "index.json"), "out": conv(td / "out"), "flipAt": 0x1000,
                   "tampered": conv(td / "tampered.json"),
                   "edgeZips": {k: conv(td / f"{k}.zip") for k in ("comment", "trunc", "stored")},
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
                ze = res["zipEdge"]
                check(ze["comment"].get("ok") == ["bento1.bin", "bento2.bin"], f"a zip whose comment holds PK\\5\\6 still opens ({ze['comment']})")
                check("damaged" in ze["trunc"].get("error", ""), f"a truncated zip says it is damaged ({ze['trunc']})")
                check(ze["stored"].get("b1") is True and ze["stored"]["ok"] == ["bento1.bin"],
                      f"a stored zip with an upper-case BENTO1.BIN and a __MACOSX ._ file gives the right file ({ze['stored']})")
                mn = res["identify"]["moddedNote"]
                check("already modded" in mn and "not stock" not in mn, f"an already-modded image is named once ({mn})")
                check("exactly" in res["identify"]["otherNote"], f"an unknown file's refusal says what is needed ({res['identify']['otherNote']})")
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
                        cl = rd("DEVICE_CHECKLIST.txt").decode()
                        check(cl.endswith(p["kit"]["checklist"]) and cl.startswith("This is the author's device checklist"),
                              f"v{v}: DEVICE_CHECKLIST.txt is the patch's checklist under a preface for kit users")
                        sums = rd("SHA256SUMS.txt").decode()
                        lines = sums.splitlines()
                        ok = all(sha(rd(l.split("  ./", 1)[1])) == l.split("  ", 1)[0] for l in lines)
                        check(ok and len(lines) == len(want) - 1 and lines == sorted(lines, key=lambda l: l.split("  ", 1)[1].encode()),
                              f"v{v}: SHA256SUMS.txt covers every other file, right hashes, make_kit's order")
                        readme = rd("README.txt").decode()
                        check(all(s in readme for s in ("01_TEST_FIRMWARE", "ROOT of the SD card", "Long-press power", "REC",
                                                        "03_RESTORE_STOCK", "not supported on stock firmware", "Keep it private",
                                                        "Not affiliated with 1010music", "bootloader", "NO WARRANTY",
                                                        "Don't switch the Bento off")) and "always works" not in readme,
                              f"v{v}: README has the install steps, the way back and the disclaimers")
                        if v in kits:
                            kd = kits[v]
                            same = all(rd(f) == (kd / f).read_bytes() for f in ("01_TEST_FIRMWARE/bento1.bin", "01_TEST_FIRMWARE/bento2.bin",
                                                                              "03_RESTORE_STOCK/bento1.bin", "03_RESTORE_STOCK/bento2.bin"))
                            check(same, f"v{v}: zip's four firmware files == make_kit kit's, byte for byte")
                            klines = {l.split("  ", 1)[1]: l for l in (kd / "SHA256SUMS.txt").read_text().splitlines()}
                            fw = [l for l in lines if ".bin" in l]
                            check(fw == [klines[l.split("  ", 1)[1]] for l in fw], f"v{v}: SHA256SUMS.txt firmware lines == make_kit's")
                            kc = (kd / "DEVICE_CHECKLIST.txt").read_bytes().replace(b"\r\n", b"\n").decode()
                            print(f"        v{v}: DEVICE_CHECKLIST.txt " + ("== make_kit's, under the preface" if cl.endswith(kc) else
                                  "differs from this make_kit kit's (the patch carries docs/checklist at export time; the kit, the one at image_commit)"))
                            print(f"        v{v}: the web kit has README.txt and no manifest.json (the build manifest lists stock bytes)")

                print("== (d) refusals")
                ref = res["refusals"]
                check(res["identify"].get("flipped") is None, "bento1.bin with one bit flipped is not recognised as stock")
                for name, words in (("bento1_flipped", "not the official stock"), ("bento2_flipped", "not the official stock"),
                                    ("swapped", "not the official stock"), ("payload_damaged", "damaged"),
                                    ("payload_byte_changed", "not the published"), ("op_out_of_bounds", "bad FILL op"),
                                    ("untested", "not marked tested")):
                    check(ref[name] is not None and words in ref[name], f"refused: {name} ({ref[name]})")
    if not node:
        print("== (d) refusals (python reference only: no node)")
    b = bytearray(s1)
    b[0x1000] ^= 1
    check(sha(ep.apply_patch(bytes(b), pats[idx["default"]], s2)) != pats[idx["default"]]["output_sha256"],
          "python apply on stock with one bit flipped does not give the release sha256")

    print(f"\n{len(FAILS)} failed" + (": " + "; ".join(FAILS) if FAILS else ", all passed"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
