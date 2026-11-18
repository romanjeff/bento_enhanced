# SPDX-License-Identifier: GPL-3.0-or-later
"""Export a release as a web patch: the ops that turn stock bento1.bin into the release's bento1.bin.

    python tools/export_patch.py STOCK RELEASE [--version 4.46] [--out web/patches] [--min-copy 8]
    python tools/export_patch.py STOCK RELEASE --survey        (table of patch size against --min-copy)

STOCK is the official 1.6.8 firmware (folder, bento1.bin / bento2.bin, or the download's .zip; as make_kit.py).
RELEASE is the release's bento1.bin as make_kit.py built it from the release's image_commit, or that kit's
folder (bento_enhanced-v<ver>/). Its sha256 must be the published one in tools/release_hashes.json.

Writes web/patches/v<ver>.json and adds the release to web/patches/index.json. The patch holds only bytes we
authored (the WRITE payload) plus offsets: every run of >= MIN_COPY bytes that also occurs anywhere in stock
becomes a COPY op from the user's own stock image, and so does every run of 4..N-1 bytes equal to stock bytes the
release overwrote (a stock instruction relocated out of a patch site); constant runs become FILL ops.
See docs/web-patcher.md.

The patch is self-verified before it is written: applying it to stock must give the release's sha256, and the
zero-stock-bytes check (check_zero_stock) must find nothing. Never writes or commits any firmware.
"""
import argparse, base64, hashlib, json, sys
from collections import Counter
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
HASHES = TOOLS / "release_hashes.json"
WEB = REPO / "web"
FORMAT = "bento_enhanced-webpatch"
FORMAT_VERSION = 1
MIN_COPY = 8          # N: see docs/web-patcher.md, "The zero-stock-bytes rule"
MERGE_GAP = 3         # unchanged bytes (< 4, below the reported short-run floor) bridged between two changed ranges
SHORT_FLOOR = 4       # matching runs of SHORT_FLOOR..N-1 bytes are reported, not removed
MAX_CANDIDATES = 64   # stock positions kept per N-gram when choosing the longest COPY


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- apply and check (the tests use these too)

def base_image(stock1, size):
    """The output starts as stock bento1.bin, truncated or zero-extended to the release's size."""
    return bytearray(stock1[:size]) + bytearray(max(0, size - len(stock1)))


def apply_patch(stock1, patch):
    """The reference implementation of web/patcher.js applyPatch: ops in order, COPY reads the ORIGINAL stock."""
    out = base_image(stock1, patch["output_size"])
    payload = base64.b64decode(patch["payload_b64"])
    if sha(payload) != patch["payload_sha256"]:
        raise ValueError("payload sha256 mismatch")
    p = 0
    for op in patch["ops"]:
        kind = op[0]
        if kind == "W":
            _, dst, n = op
            out[dst:dst + n] = payload[p:p + n]
            p += n
        elif kind == "C":
            _, dst, src, n = op
            if src + n > len(stock1):
                raise ValueError(f"COPY source out of stock: {op}")
            out[dst:dst + n] = stock1[src:src + n]
        elif kind == "F":
            _, dst, n, v = op
            out[dst:dst + n] = bytes([v]) * n
        else:
            raise ValueError(f"unknown op {op!r}")
        if len(out) != patch["output_size"]:
            raise ValueError(f"op {op} runs past output_size")
    if p != len(payload):
        raise ValueError("payload not fully consumed")
    return bytes(out)


def write_spans(patch):
    """The WRITE payload as maximal contiguous output spans [(dst, bytes)]: adjacent WRITE ops are joined, so
    splitting a stock run across two WRITEs cannot hide it from the check."""
    payload = base64.b64decode(patch["payload_b64"])
    spans, p = [], 0
    for op in patch["ops"]:
        if op[0] != "W":
            continue
        _, dst, n = op
        data = payload[p:p + n]
        p += n
        if spans and spans[-1][0] + len(spans[-1][1]) == dst:
            spans[-1] = (spans[-1][0], spans[-1][1] + data)
        else:
            spans.append((dst, data))
    return spans


def stock_grams(stock1, wanted, n, cap=MAX_CANDIDATES):
    """{gram: [stock positions]} for the n-byte strings in `wanted` that occur in stock (<= cap positions each)."""
    found = {}
    if not wanted:
        return found
    for j in range(len(stock1) - n + 1):
        g = stock1[j:j + n]
        if g in wanted:
            lst = found.setdefault(g, [])
            if len(lst) < cap:
                lst.append(j)
    return found


def displaced_grams(stock1, out, n, lo=SHORT_FLOOR):
    """{k: {gram: stock pos}} for k in lo..n-1: the k-byte windows of stock lying wholly inside a range the
    release changed (a patch site or a region it overwrites). Such a run in a WRITE is a relocated stock
    instruction, not a coincidence, so it is COPYed like a long run."""
    ranges = diff_ranges(base_image(stock1, len(out)), out)
    res = {}
    for k in range(lo, n):
        d = {}
        for a, b in ranges:
            for j in range(a, min(b, len(stock1)) - k + 1):
                d.setdefault(stock1[j:j + k], j)
        res[k] = d
    return res


def check_relocated(stock1, patch, out=None):
    """Violations of the relocated-instruction rule: [(dst, stock_pos, hex)] for WRITE runs of 4..N-1 bytes equal
    to stock bytes the release overwrote."""
    out = out if out is not None else apply_patch(stock1, patch)
    dg = displaced_grams(stock1, out, patch["min_copy"])
    bad = []
    for dst, d in write_spans(patch):
        for k, grams in dg.items():
            for i in range(len(d) - k + 1):
                g = d[i:i + k]
                if g in grams:
                    bad.append((dst + i, grams[g], g.hex()))
    return bad


def check_zero_stock(stock1, patch, n=None):
    """Violations of the rule: [(dst, stock_pos, hex)] for every n-byte window of a WRITE span found in stock."""
    n = n or patch["min_copy"]
    spans = write_spans(patch)
    wanted = {d[i:i + n] for _, d in spans for i in range(len(d) - n + 1)}
    hits = stock_grams(stock1, wanted, n, cap=1)
    bad = []
    for dst, d in spans:
        for i in range(len(d) - n + 1):
            g = d[i:i + n]
            if g in hits:
                bad.append((dst + i, hits[g][0], g.hex()))
    return bad


def short_runs(stock1, patch, lo=SHORT_FLOOR):
    """Maximal runs of lo..N-1 WRITE bytes that also occur somewhere in stock (likely coincidences):
    Counter {length: count} and the bytes they cover."""
    n = patch["min_copy"]
    spans = write_spans(patch)
    sets = {}
    for k in range(lo, n):
        wanted = {d[i:i + k] for _, d in spans for i in range(len(d) - k + 1)}
        sets[k] = set(stock_grams(stock1, wanted, k, cap=1))
    hist, covered = Counter(), 0
    for _, d in spans:
        best = [0] * len(d)
        for i in range(len(d)):
            for k in range(n - 1, lo - 1, -1):
                if i + k <= len(d) and d[i:i + k] in sets[k]:
                    best[i] = k
                    break
        mask = bytearray(len(d))
        for i, k in enumerate(best):
            if k and not (i > 0 and best[i - 1] >= k + 1):   # not inside the previous position's longer run
                hist[k] += 1
            for t in range(i, i + k):
                mask[t] = 1
        covered += sum(mask)
    return hist, covered


# ---------------------------------------------------------------- export

def diff_ranges(base, out, gap=MERGE_GAP):
    """[start, end) ranges where out differs from base, bridging gaps of <= gap unchanged bytes."""
    ranges, i, size = [], 0, len(out)
    while i < size:
        if out[i] == base[i]:
            i += 1
            continue
        j = i
        while j < size and out[j] != base[j]:
            j += 1
        if ranges and i - ranges[-1][1] <= gap:
            ranges[-1][1] = j
        else:
            ranges.append([i, j])
        i = j
    return ranges


def make_ops(stock1, out, n):
    base = base_image(stock1, len(out))
    ranges = diff_ranges(base, out)
    wanted = {out[i:i + n] for a, b in ranges for i in range(a, b - n + 1)}
    grams = stock_grams(stock1, wanted, n)
    dgrams = displaced_grams(stock1, out, n)
    ops, payload = [], bytearray()
    wstart, wbuf = None, bytearray()

    def flush():
        nonlocal wstart, wbuf
        if wbuf:
            ops.append(["W", wstart, len(wbuf)])
            payload.extend(wbuf)
        wstart, wbuf = None, bytearray()

    for a, b in ranges:
        i = a
        while i < b:
            r = 1
            while i + r < b and out[i + r] == out[i]:
                r += 1
            if r >= n:                                  # a constant run: FILL (not stock content)
                flush()
                ops.append(["F", i, r, out[i]])
                i += r
                continue
            g = out[i:i + n] if i + n <= b else None
            if g is not None and g in grams:            # occurs in stock: COPY, the longest candidate
                cands = list(grams[g])
                if i not in cands and i + n <= len(stock1) and stock1[i:i + n] == g:
                    cands.insert(0, i)                  # same offset first: a tie keeps the unmoved source
                best_src, best_len = None, 0
                for s in cands:
                    L = n
                    while i + L < b and s + L < len(stock1) and out[i + L] == stock1[s + L]:
                        L += 1
                    if L > best_len:
                        best_src, best_len = s, L
                flush()
                ops.append(["C", i, best_src, best_len])
                i += best_len
                continue
            k = next((k for k in range(n - 1, SHORT_FLOOR - 1, -1) if i + k <= b and out[i:i + k] in dgrams[k]), 0)
            if k:                                       # a relocated stock instruction: COPY it from its site
                flush()
                ops.append(["C", i, dgrams[k][out[i:i + k]], k])
                i += k
                continue
            if wstart is None:
                wstart = i
            wbuf.append(out[i])
            i += 1
        flush()
    return ops, bytes(payload), ranges


def build_patch(stock, out, version, n, label, extra):
    ops, payload, ranges = make_ops(stock["bento1.bin"], out, n)
    patch = {
        "format": FORMAT, "format_version": FORMAT_VERSION,
        "version": version, "label": label, "base": extra["base"],
        "stock_sha256": {k: sha(v) for k, v in stock.items()},
        "stock_size": {k: len(v) for k, v in stock.items()},
        "output_sha256": sha(out), "output_size": len(out),
        "image_commit": extra.get("image_commit"), "device_tested": extra.get("device_tested"),
        "min_copy": n,
        "notes": "Ops in order on a copy of stock bento1.bin truncated / zero-extended to output_size. "
                 "W [dst, len]: the next len payload bytes (ours). C [dst, src, len]: from the ORIGINAL stock "
                 "bento1.bin. F [dst, len, byte]: a constant run. See docs/web-patcher.md.",
        "ops": ops,
        "payload_sha256": sha(payload),
        "payload_b64": base64.b64encode(payload).decode(),
    }
    counts = Counter(op[0] for op in ops)
    stats = {
        "changed_ranges": len(ranges), "changed_bytes": sum(b - a for a, b in ranges),
        "write_ops": counts["W"], "write_bytes": len(payload),
        "copy_ops": counts["C"], "copy_bytes": sum(op[3] for op in ops if op[0] == "C"),
        "copy_ops_short": sum(1 for op in ops if op[0] == "C" and op[3] < n),
        "copy_bytes_short": sum(op[3] for op in ops if op[0] == "C" and op[3] < n),
        "fill_ops": counts["F"], "fill_bytes": sum(op[2] for op in ops if op[0] == "F"),
    }
    return patch, stats


def load_release(path):
    """(bento1 bytes, manifest dict or None) from a bento1.bin or a make_kit kit folder."""
    p = Path(path).expanduser()
    if p.is_dir():
        img = p / "01_TEST_FIRMWARE" / "bento1.bin"
        man = p / "manifest.json"
    else:
        img, man = p, p.parent.parent / "manifest.json"
    if not img.is_file():
        sys.exit(f"ERROR: no release bento1.bin at {img}")
    return img.read_bytes(), (json.loads(man.read_text(encoding="utf-8")) if man.is_file() else None)


def update_index(db, patch, path):
    idx_path = path / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.is_file() else {}
    base = db["base"]
    vers = {}
    for v, h in db["stock"].items():
        for name, s in h.items():
            vers.setdefault((s, name), []).append(v)
    known = {s: f"stock {' / '.join(v)} {name}" for (s, name), v in vers.items()}
    for v, r in db["releases"].items():
        known[r["bento1.bin"]] = f"bento_enhanced v{v} bento1.bin (already modded: use your stock {base} file)"
    rels = {r["version"]: r for r in idx.get("releases", [])}
    rels[patch["version"]] = {"version": patch["version"], "label": patch["label"],
                              "file": f"v{patch['version']}.json", "output_sha256": patch["output_sha256"],
                              "device_tested": patch["device_tested"]}
    order = sorted(rels.values(), key=lambda r: [int(x) if x.isdigit() else x for x in r["version"].split(".")],
                   reverse=True)
    idx = {"format": FORMAT, "format_version": FORMAT_VERSION, "base": base,
           "stock_sha256": dict(db["stock"][base]), "known_sha256": known,
           "default": order[0]["version"], "releases": order}
    idx_path.write_text(json.dumps(idx, indent=1) + "\n", encoding="utf-8", newline="\n")
    return idx_path


def main():
    sys.path.insert(0, str(TOOLS))
    from make_kit import read_stock   # noqa: E402  (folder / .bin / zip, as make_kit takes it)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stock", help="stock firmware: folder, bento1.bin / bento2.bin, or the download's .zip")
    ap.add_argument("release", help="the release bento1.bin (built by make_kit.py) or its kit folder")
    ap.add_argument("--version", help="release version (default: from the kit's manifest or release_hashes.json)")
    ap.add_argument("--out", default=str(WEB / "patches"), help="patch folder (default web/patches)")
    ap.add_argument("--min-copy", type=int, default=MIN_COPY, help=f"N, the zero-stock-bytes run length (default {MIN_COPY})")
    ap.add_argument("--checklist", help="DEVICE_CHECKLIST text (default docs/checklist-v<ver>.txt)")
    ap.add_argument("--survey", action="store_true", help="print patch size against N (4..16) and exit")
    ap.add_argument("--allow-untested", action="store_true", help="export a release not yet device_tested")
    a = ap.parse_args()

    db = json.loads(HASHES.read_text(encoding="utf-8"))
    base = db["base"]
    stock, where = read_stock(a.stock)
    for k in stock:
        if sha(stock[k]) != db["stock"][base][k]:
            sys.exit(f"ERROR: {where}: {k} is not the official stock {base}")
    out, man = load_release(a.release)
    h = sha(out)
    version = a.version or (man or {}).get("version") or next(
        (v for v, r in db["releases"].items() if r["bento1.bin"] == h), None)
    rel = db["releases"].get(version)
    if rel is None:
        sys.exit(f"ERROR: no release {version!r} in tools/release_hashes.json (sha256 {h})")
    if rel["bento1.bin"] != h:
        sys.exit(f"ERROR: {a.release}: sha256 {h} is not v{version}'s published {rel['bento1.bin']}")
    if rel.get("device_tested") is not True and not a.allow_untested:
        sys.exit(f"ERROR: v{version} is not device_tested in release_hashes.json (--allow-untested to export anyway)")
    label = (man or {}).get("label") or f"BE{version} (bento_enhanced v{version} on stock {base})"
    extra = {"base": base, "image_commit": rel.get("image_commit"), "device_tested": rel.get("device_tested")}

    if a.survey:
        print(" N  W ops  W bytes  C ops  C bytes  F ops  json KB")
        for n in range(4, 17):
            p, s = build_patch(stock, out, version, n, label, extra)
            print(f"{n:2} {s['write_ops']:6} {s['write_bytes']:8} {s['copy_ops']:6} {s['copy_bytes']:8} "
                  f"{s['fill_ops']:6} {len(json.dumps(p, separators=(',', ':'))) / 1024:8.1f}")
        return

    patch, stats = build_patch(stock, out, version, a.min_copy, label, extra)
    cl = Path(a.checklist) if a.checklist else REPO / "docs" / f"checklist-v{version}.txt"
    patch["kit"] = {"name": f"bento_enhanced-v{version}",
                    "checklist": cl.read_bytes().replace(b"\r\n", b"\n").decode("utf-8") if cl.is_file() else None}

    # Self-verification: the patch reproduces the release, and publishes no stock run.
    if sha(apply_patch(stock["bento1.bin"], patch)) != h:
        sys.exit("ERROR: applying the exported patch to stock does not reproduce the release (not written)")
    bad = check_zero_stock(stock["bento1.bin"], patch)
    if bad:
        sys.exit(f"ERROR: {len(bad)} WRITE window(s) of {a.min_copy} bytes occur in stock, e.g. {bad[:3]} (not written)")
    bad = check_relocated(stock["bento1.bin"], patch, out)
    if bad:
        sys.exit(f"ERROR: {len(bad)} relocated stock run(s) left in WRITEs, e.g. {bad[:3]} (not written)")
    hist, covered = short_runs(stock["bento1.bin"], patch)
    stats["short_runs"] = {str(k): hist[k] for k in sorted(hist)}
    stats["short_run_bytes"] = covered
    patch["stats"] = stats

    dest = Path(a.out)
    dest.mkdir(parents=True, exist_ok=True)
    f = dest / f"v{version}.json"
    f.write_text(json.dumps(patch, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    idx = update_index(db, patch, dest)
    print(f"v{version}: {label}")
    print(f"  output {patch['output_size']} bytes sha256 {h}: reproduced from stock by the patch (verified)")
    print(f"  N={a.min_copy}: {stats['write_ops']} WRITE ops / {stats['write_bytes']} bytes ours, "
          f"{stats['copy_ops']} COPY ops / {stats['copy_bytes']} bytes from stock, "
          f"{stats['fill_ops']} FILL ops / {stats['fill_bytes']} bytes; no WRITE window of {a.min_copy} bytes occurs in stock")
    print(f"  of the COPYs, {stats['copy_ops_short']} ({stats['copy_bytes_short']} bytes) are relocated stock "
          f"instructions shorter than {a.min_copy} bytes (from the sites the release overwrote)")
    print(f"  short runs ({SHORT_FLOOR}..{a.min_copy - 1} bytes) also in stock, by length: {stats['short_runs']} "
          f"({covered} payload bytes, {100 * covered / max(1, stats['write_bytes']):.1f}%)")
    print(f"  wrote {f} ({f.stat().st_size} bytes) and {idx}")


if __name__ == "__main__":
    main()
