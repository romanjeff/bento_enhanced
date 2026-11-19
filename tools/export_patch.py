# SPDX-License-Identifier: GPL-3.0-or-later
"""Export a release as a web patch: the ops that turn stock bento1.bin into the release's bento1.bin.

    python tools/export_patch.py STOCK RELEASE [--version 4.46] [--out web/patches] [--min-copy 8]
    python tools/export_patch.py STOCK RELEASE --survey        (table of patch size against --min-copy)

STOCK is the official 1.6.8 firmware (folder, bento1.bin / bento2.bin, or the download's .zip; as make_kit.py).
RELEASE is the release's bento1.bin as make_kit.py built it from the release's image_commit, or that kit's
folder (bento_enhanced-v<ver>/). Its sha256 must be the published one in tools/release_hashes.json.

Writes web/patches/v<ver>.json and adds the release to web/patches/index.json. The patch holds only bytes we
authored (the WRITE payload) plus offsets: every run of >= MIN_COPY bytes that also occurs anywhere in stock
bento1.bin or bento2.bin becomes a COPY op from the user's own stock file, and so does every run of 4..N-1 bytes
equal to stock bytes the release overwrote (a stock instruction relocated out of a patch site); constant runs become
FILL ops. See docs/web-patcher.md.

The patch is self-verified before it is written: applying it to stock must give the release's sha256, and every
zero-stock-bytes check (check_policy) must find nothing. Only device-tested releases go to web/ by default
(--allow-untested writes elsewhere only). --publish-untested puts a release that is not yet device_tested on the site
as a TEST BUILD: it keeps device_tested false in the patch and index.json, is marked test_build in index.json, its
kit is named ...-UNTESTED, and it is never the default (the default is the newest device-tested release).
Never writes or commits any firmware.
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
NEAR_GRAM = 4         # near-copy check: 4-byte matches ...
NEAR_MAX_OCC = 64     # ... of grams occurring 1..this many times in a stock file (rarer grams locate a region)
NEAR_WIN = 64         # in any window of this many bytes (or the whole sequence, if shorter, from 16 bytes),
NEAR_BAND = 16        # matches against one stock region (diagonals within two adjacent bands of this width)
NEAR_FRAC = 0.5       # must cover less than this fraction of the window


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- apply and check (the tests use these too)

def base_image(stock1, size):
    """The output starts as stock bento1.bin, truncated or zero-extended to the release's size."""
    return bytearray(stock1[:size]) + bytearray(max(0, size - len(stock1)))


def apply_patch(stock1, patch, stock2=None):
    """The reference implementation of web/patcher.js applyPatch: ops in order, COPY reads the ORIGINAL stock
    (C from bento1.bin, D from bento2.bin)."""
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
        elif kind == "D":
            _, dst, src, n = op
            if stock2 is None or src + n > len(stock2):
                raise ValueError(f"COPY (bento2) source out of stock: {op}")
            out[dst:dst + n] = stock2[src:src + n]
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
    """The WRITE payload as maximal contiguous output spans [(dst, bytes)], in output order: WRITEs adjacent in
    the output are joined whatever their order in the op list, so splitting a stock run across several WRITEs
    (in any order) cannot hide it from the checks."""
    payload = base64.b64decode(patch["payload_b64"])
    pieces, p = [], 0
    for op in patch["ops"]:
        if op[0] != "W":
            continue
        _, dst, n = op
        pieces.append((dst, payload[p:p + n]))
        p += n
    pieces.sort(key=lambda t: t[0])
    spans = []
    for dst, data in pieces:
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
    """{k: {gram: stock pos}} for k in lo..n-1: the k-byte windows of stock that overlap a range the release
    changed (a patch site or a region it overwrites), including windows straddling its edges (a byte of the
    overwritten instruction may happen to equal the new one, so the range starts later). Such a run in a WRITE
    is a relocated stock instruction, not a coincidence, so it is COPYed like a long run."""
    ranges = diff_ranges(base_image(stock1, len(out)), out)
    res = {}
    for k in range(lo, n):
        d = {}
        for a, b in ranges:
            for j in range(max(0, a - k + 1), min(b + k - 1, len(stock1)) - k + 1):
                d.setdefault(stock1[j:j + k], j)
        res[k] = d
    return res


def check_relocated(stock1, patch, out=None, stock2=None):
    """Violations of the relocated-instruction rule: [(dst, stock_pos, hex)] for WRITE runs of 4..N-1 bytes equal
    to stock bytes the release overwrote."""
    out = out if out is not None else apply_patch(stock1, patch, stock2)
    dg = displaced_grams(stock1, out, patch["min_copy"])
    bad = []
    for dst, d in write_spans(patch):
        for k, grams in dg.items():
            for i in range(len(d) - k + 1):
                g = d[i:i + k]
                if g in grams:
                    bad.append((dst + i, grams[g], g.hex()))
    return bad


def payload_stream(patch):
    return base64.b64decode(patch["payload_b64"])


def _seqs(patch):
    """What the checks scan: every WRITE span (output order) and the raw payload stream as one sequence."""
    return [("dst 0x%x" % dst, d) for dst, d in write_spans(patch)] + [("payload", payload_stream(patch))]


def _stock(stock):
    return stock if isinstance(stock, dict) else {"bento1.bin": stock}


def check_zero_stock(stock, patch, n=None):
    """Rule 1 violations: [(where, stock file, stock pos, hex)] for every n-byte window, of a WRITE span (joined in
    output order) or of the raw payload stream (so WRITEs scattered over distant offsets, which the stream holds
    side by side, cannot hide a run), that occurs in a stock file. stock: {name: bytes}, or bento1's bytes."""
    n = n or patch["min_copy"]
    seqs = _seqs(patch)
    wanted = {d[i:i + n] for _, d in seqs for i in range(len(d) - n + 1)}
    bad = []
    for name, sb in _stock(stock).items():
        hits = stock_grams(sb, wanted, n, cap=1)
        for where, d in seqs:
            for i in range(len(d) - n + 1):
                g = d[i:i + n]
                if g in hits:
                    bad.append((f"{where}+{i}", name, hits[g][0], g.hex()))
    return bad


def check_structure(patch, stock_size=None):
    """Op-shape violations [(op index, why)]: every op inside the output and its source inside its stock file;
    no two ops writing the same output byte (a later op could put stock bytes back between WRITEs); every FILL
    at least N long (a short FILL could do the same)."""
    n, size = patch["min_copy"], patch["output_size"]
    stock_size = stock_size or patch.get("stock_size", {})
    bad, iv = [], []
    for k, op in enumerate(patch["ops"]):
        kind = op[0]
        if kind == "W":
            dst, ln = op[1], op[2]
        elif kind in ("C", "D"):
            dst, src, ln = op[1], op[2], op[3]
            lim = stock_size.get("bento1.bin" if kind == "C" else "bento2.bin")
            if src < 0 or lim is None or src + ln > lim:
                bad.append((k, f"source out of stock: {op}"))
        elif kind == "F":
            dst, ln = op[1], op[2]
            if ln < n:
                bad.append((k, f"FILL shorter than N={n}: {op}"))
        else:
            bad.append((k, f"unknown op {op!r}"))
            continue
        if ln <= 0 or dst < 0 or dst + ln > size:
            bad.append((k, f"out of the output: {op}"))
        iv.append((dst, dst + ln, k))
    iv.sort()
    end, end_k = -1, None
    for a, b, k in iv:
        if a < end:
            bad.append((k, f"overlaps op {end_k}"))
        if b > end:
            end, end_k = b, k
    return bad


def near_index(sb, wanted, k=NEAR_GRAM, max_occ=NEAR_MAX_OCC):
    """{gram: [positions]} in stock bytes sb, for the wanted k-grams occurring 1..max_occ times there."""
    found = {}
    for j in range(len(sb) - k + 1):
        g = sb[j:j + k]
        if g in wanted:
            found.setdefault(g, []).append(j)
    return {g: p for g, p in found.items() if len(p) <= max_occ}


def near_copies(seq, idx, k=NEAR_GRAM, win=NEAR_WIN, band=NEAR_BAND, frac=NEAR_FRAC):
    """Windows of seq in which k-gram matches against ONE stock region cover >= frac of the window: a stock run
    with sparse edits (a byte changed every few), or pieces of one stock region drifting slowly against it.
    A region is a diagonal (stock pos - seq pos) within two adjacent bands. [(seq pos, diagonal, covered)]."""
    if len(seq) < 16:
        return []
    by_band = {}
    for i in range(len(seq) - k + 1):
        for p in idx.get(seq[i:i + k], ()):
            by_band.setdefault((p - i) // band, []).append((i, p - i))
    w = min(win, len(seq))
    need = max(1, int(w * frac))
    bad = []
    for b in sorted(by_band):
        hits = sorted(by_band[b] + by_band.get(b + 1, []))
        if len(hits) * k < need:
            continue
        # A window worth flagging can be taken to start at a hit: try each hit as the start. Covered = the bytes
        # the hits inside [start, start + w) cover on BOTH sides (the union of their seq intervals, and of their
        # stock intervals): a copy maps distinct bytes to distinct bytes, while a repeated pattern of ours
        # ("CC 90", "CC 80", ...; a nop sled) matching one short stock spot again and again covers only that spot.
        for j, (s0, diag) in enumerate(hits):
            win_hits = [(i, i + d) for i, d in hits[j:j + 4 * w] if i < s0 + w]
            if len(win_hits) * k < need:
                continue
            cov = min(_union([a for a, _ in win_hits], k, s0 + w), _union([b for _, b in win_hits], k))
            if cov >= need:
                bad.append((s0, diag, cov, win_hits))
                break
    return bad


def _union(starts, k, limit=None):
    """Bytes covered by the intervals [s, s + k) (clipped below limit when given)."""
    cov, reach = 0, None
    for s in sorted(starts):
        lo = s if reach is None else max(s, reach)
        hi = s + k if limit is None else min(s + k, limit)
        if hi > lo:
            cov += hi - lo
            reach = hi
    return cov


def check_near_copy(stock, patch, detail=False):
    """Near-copy violations over the WRITE spans (output order) and the payload stream, against each stock file:
    [(where, stock file, diagonal, covered bytes)]; with detail, also the matches [(output dst, stock pos)]."""
    k = NEAR_GRAM
    spans = write_spans(patch)
    seqs = [("dst 0x%x" % dst, d, dst) for dst, d in spans] + [("payload", payload_stream(patch), None)]
    wanted = {d[i:i + k] for _, d, _ in seqs for i in range(len(d) - k + 1)}
    pay_dst = []                                        # payload offset -> output offset
    for op in patch["ops"]:
        if op[0] == "W":
            pay_dst.extend(range(op[1], op[1] + op[2]))
    bad = []
    for name, sb in _stock(stock).items():
        idx = near_index(sb, wanted)
        for where, d, base in seqs:
            for s0, diag, cov, hits in near_copies(d, idx):
                v = (f"{where}+{s0}", name, diag, cov)
                if detail:                              # the matches, in output offsets, where a gram is contiguous there
                    to = (lambda i: base + i) if base is not None else (lambda i: pay_dst[i])
                    v += ([(to(i), sp) for i, sp in hits if to(i + k - 1) == to(i) + k - 1],)
                bad.append(v)
    return bad


def check_policy(stock, patch, out=None):
    """Every zero-stock-bytes check as {rule: [violations]}; all lists empty when the patch is clean.
    stock: {"bento1.bin": ..., "bento2.bin": ...}."""
    return {
        "structure": check_structure(patch, {k: len(v) for k, v in stock.items()}),
        "no_stock_run": check_zero_stock(stock, patch),
        "no_relocated": check_relocated(stock["bento1.bin"], patch, out, stock.get("bento2.bin")),
        "no_near_copy": check_near_copy(stock, patch),
    }


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


def make_ops(stock1, out, n, stock2=b"", forced=None):
    """forced: {output offset: (op kind "C" / "D", stock pos, len)}: matches the near-copy check flagged, COPYed."""
    forced = forced or {}
    base = base_image(stock1, len(out))
    ranges = diff_ranges(base, out)
    wanted = {out[i:i + n] for a, b in ranges for i in range(a, b - n + 1)}
    grams = stock_grams(stock1, wanted, n)
    grams2 = stock_grams(stock2, wanted, n)
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
            f = forced.get(i)
            if f and i + f[2] <= b:                     # part of a near copy of a stock region: COPY it
                flush()
                ops.append([f[0], i, f[1], f[2]])
                i += f[2]
                continue
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
            if g is not None and g in grams2:           # occurs only in bento2.bin: COPY from there (D)
                best_src, best_len = None, 0
                for s in grams2[g]:
                    L = n
                    while i + L < b and s + L < len(stock2) and out[i + L] == stock2[s + L]:
                        L += 1
                    if L > best_len:
                        best_src, best_len = s, L
                flush()
                ops.append(["D", i, best_src, best_len])
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
    forced = {}
    for _ in range(8):                                  # COPY what the near-copy check flags, until it flags nothing
        patch, stats = _build_patch(stock, out, version, n, label, extra, forced)
        bad = check_near_copy(stock, patch, detail=True)
        new = {dst: ("C" if name == "bento1.bin" else "D", sp, NEAR_GRAM)
               for _, name, _, _, hits in bad for dst, sp in hits if dst not in forced}
        if not new:
            break
        forced.update(new)
    stats["copy_ops_near"] = len(forced)
    return patch, stats


def _build_patch(stock, out, version, n, label, extra, forced):
    ops, payload, ranges = make_ops(stock["bento1.bin"], out, n, stock["bento2.bin"], forced)
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
                 "bento1.bin. D [dst, src, len]: from stock bento2.bin. F [dst, len, byte]: a constant run. "
                 "See docs/web-patcher.md.",
        "ops": ops,
        "payload_sha256": sha(payload),
        "payload_b64": base64.b64encode(payload).decode(),
    }
    counts = Counter(op[0] for op in ops)
    stats = {
        "changed_ranges": len(ranges), "changed_bytes": sum(b - a for a, b in ranges),
        "write_ops": counts["W"], "write_bytes": len(payload),
        "copy_ops": counts["C"], "copy_bytes": sum(op[3] for op in ops if op[0] == "C"),
        "copy2_ops": counts["D"], "copy2_bytes": sum(op[3] for op in ops if op[0] == "D"),
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


UNTESTED_TAG = "-UNTESTED"   # in a test build's kit name (and so its zip's folder and file name)


def kit_name(version, tested):
    return f"bento_enhanced-v{version}" + ("" if tested else UNTESTED_TAG)


def vkey(v):
    return [int(x) if x.isdigit() else x for x in v.split(".")]


def update_index(db, patch, path, untested_ok=False):
    """(path/index.json, its new content with the patch added); main writes it. A device-tested release as before;
    one with device_tested false only with untested_ok (--publish-untested), as a test build (test_build true),
    never the default."""
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
    tested = patch["device_tested"]
    if tested is not True and not (tested is False and untested_ok):
        sys.exit("ERROR: index.json lists device-tested releases only (--publish-untested: as a test build)")
    rels = {r["version"]: r for r in idx.get("releases", [])}
    entry = {"version": patch["version"], "label": patch["label"],
             "file": f"v{patch['version']}.json", "output_sha256": patch["output_sha256"],
             "device_tested": tested}
    if tested is not True:
        entry["test_build"] = True
    rels[patch["version"]] = entry
    order = sorted(rels.values(), key=lambda r: vkey(r["version"]), reverse=True)
    default = next((r["version"] for r in order if r["device_tested"] is True), None)
    if default is None:
        sys.exit("ERROR: index.json needs at least one device-tested release (the default); a test build is never it")
    idx = {"format": FORMAT, "format_version": FORMAT_VERSION, "base": base,
           "stock_sha256": dict(db["stock"][base]), "known_sha256": known,
           "default": default, "releases": order}
    return idx_path, idx


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
    ap.add_argument("--allow-untested", action="store_true",
                    help="export a release not yet device_tested, to an --out outside web/ only (never published)")
    ap.add_argument("--publish-untested", action="store_true",
                    help="put a release not yet device_tested on the site as a TEST BUILD (device_tested stays false, "
                         "test_build in index.json, kit ...-UNTESTED, never the default)")
    a = ap.parse_args()
    if a.allow_untested and a.publish_untested:
        sys.exit("ERROR: --allow-untested (outside web/, a look) and --publish-untested (the site) are exclusive")
    if a.allow_untested and not a.survey:
        o = Path(a.out).resolve()
        if o == WEB.resolve() or WEB.resolve() in o.parents:
            sys.exit("ERROR: --allow-untested writes only outside web/ (to put it on the site as a labelled test "
                     "build: --publish-untested)")

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
    tested = rel.get("device_tested")
    if tested not in (True, False):
        sys.exit(f"ERROR: v{version}: device_tested in release_hashes.json must be true or false, not {tested!r}")
    if tested is not True and not (a.allow_untested or a.publish_untested):
        sys.exit(f"ERROR: v{version} is not device_tested in release_hashes.json (--allow-untested to export it "
                 f"outside web/; --publish-untested to put it on the site as a test build)")
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
    patch["kit"] = {"name": kit_name(version, tested is True),
                    "checklist": cl.read_bytes().replace(b"\r\n", b"\n").decode("utf-8") if cl.is_file() else None}

    # Self-verification: the patch reproduces the release, and publishes no stock run.
    if sha(apply_patch(stock["bento1.bin"], patch, stock["bento2.bin"])) != h:
        sys.exit("ERROR: applying the exported patch to stock does not reproduce the release (not written)")
    for rule, bad in check_policy(stock, patch, out).items():
        if bad:
            sys.exit(f"ERROR: zero-stock-bytes rule {rule}: {len(bad)} violation(s), e.g. {bad[:3]} (not written)")
    hist, covered = short_runs(stock["bento1.bin"], patch)
    stats["short_runs"] = {str(k): hist[k] for k in sorted(hist)}
    stats["short_run_bytes"] = covered
    patch["stats"] = stats

    dest = Path(a.out)
    look = tested is not True and a.allow_untested       # a look outside web/: the patch only, no index.json
    if not look:
        idx, idx_new = update_index(db, patch, dest, untested_ok=a.publish_untested)   # may refuse: before writing
    dest.mkdir(parents=True, exist_ok=True)
    f = dest / f"v{version}.json"
    f.write_text(json.dumps(patch, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    if look:
        idx = "no index.json (--allow-untested)"
    else:
        idx.write_text(json.dumps(idx_new, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"v{version}: {label}" + ("" if tested is True else
          f"  ** TEST BUILD: device_tested false, kit {patch['kit']['name']}" +
          ("" if look else f", not the default (default v{idx_new['default']})") + " **"))
    print(f"  output {patch['output_size']} bytes sha256 {h}: reproduced from stock by the patch (verified)")
    print(f"  N={a.min_copy}: {stats['write_ops']} WRITE ops / {stats['write_bytes']} bytes ours, "
          f"{stats['copy_ops']} COPY ops / {stats['copy_bytes']} bytes from stock bento1, "
          f"{stats['copy2_ops']} / {stats['copy2_bytes']} from bento2, "
          f"{stats['fill_ops']} FILL ops / {stats['fill_bytes']} bytes; every zero-stock-bytes check clean")
    print(f"  of the COPYs, {stats['copy_ops_short']} ({stats['copy_bytes_short']} bytes) are relocated stock "
          f"instructions shorter than {a.min_copy} bytes (from the sites the release overwrote)")
    print(f"  short runs ({SHORT_FLOOR}..{a.min_copy - 1} bytes) also in stock, by length: {stats['short_runs']} "
          f"({covered} payload bytes, {100 * covered / max(1, stats['write_bytes']):.1f}%)")
    print(f"  wrote {f} ({f.stat().st_size} bytes) and {idx}")


if __name__ == "__main__":
    main()
