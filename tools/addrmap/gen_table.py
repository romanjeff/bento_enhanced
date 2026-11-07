# SPDX-License-Identifier: GPL-3.0-or-later
"""Draft the next base's symbol table from the current one.

    python -I gen_table.py OLD.bin NEW.bin --from stock_161 --facts facts_161_168 --to stock_168 [--version 1.6.8]

Reads mods/m1/bases/<from>.py, maps every entry to the new image and writes mods/m1/bases/<to>.py
(with its CHECKS block: fingerprints / literal counts of the new image, see fingerprints.py).
Every entry gets the mapped value, `was` (its value in <from>) and `status`:

    exact      same value, same bytes (code/data) or same meaning (RAM, keys, offsets, constants)
    moved      new value, same code or content (matcher: identical function, or identical tokens with
               consistent branch targets), with high confidence
    inferred   a key from the facts' range rule (no title to match it by); REVIEW unless
               expected_values.py confirms it
    changed    the code there changed (the hook's assumptions have to be read again), or the value
               was derived differently than by a 1:1 match
    unmatched  no value (None)
    fact       taken from the verified facts (facts_<from>_<to>/facts.py FACTS), not from the matcher
    reviewed   settled by a person in a later sprint (facts.py REVIEWED), re-checked against the image
    ours       our own placement / ids, as in <from>

and REVIEW="why" on anything that is not exact, not moved with high confidence and not a verified
fact without caveats. Beyond the matcher, every entry without REVIEW is cross-checked between the
two images: code = the first instructions with branch targets and PC-relative operands masked,
data = the first 16 bytes with flash pointers masked, RAM = the number of literals, keys / offsets /
events = mods/m1/expected_values.py. A disagreement makes it REVIEW. build.py refuses a table with
any REVIEW entry (or any entry without a value) and lists them: a draft never builds, and no value
of the old base is used silently.

Inputs are the two stock images (never copied into the repo; only 4-byte instruction encodings at
patch sites and fingerprints end up in the table, as for 1.6.1) and the facts directory:
  facts.py            hand-verified values and statuses (rc14 hand checks, layout work), REVIEWED
  registry_keys.txt   measured registry key renumbering (old -> new)
  ram_xlate.json      per-core votes: RAM literal value in old -> value at the same literal in new
  hand_checks.csv     the matcher's results checked by hand (29 sites; 1 was wrong)
"""
import argparse, csv, hashlib, importlib, importlib.util, json, os, re, struct, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
M1 = os.path.join(HERE, "..", "..", "mods", "m1")
sys.path.insert(0, HERE)
sys.path.insert(0, M1)
import capstone
from match import Matcher, BASE
import bases
from bases.literal_check import classify
import fingerprints
from expected_values import EXPECTED

INSNS = 6            # instructions compared per code entry
BRANCH = re.compile(r"(b|bl|blx|cbz|cbnz)(eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?(\.w|\.n)?")
SHA_RANGES = {       # const fingerprints: (from entry, to entry) of the code they hash
    "MPU_SETUP_SHA": ("MPU_SETUP", "MPU_SETUP_END"),
    "PROB_PIANO_REF_SHA": ("PROB_PIANO_REF_SITE", "PROB_PIANO_REF_BACK"),
    "PROB_PIANO_EDIT_SHA": ("PROB_PIANO_EDIT_SITE", "PROB_PIANO_EDIT_BACK"),
}


def load_facts(d):
    spec = importlib.util.spec_from_file_location("facts", os.path.join(d, "facts.py"))
    f = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(f)
    keys = {}
    for line in open(os.path.join(d, "registry_keys.txt")):
        if line.startswith("#") or "->" not in line:
            continue
        o, _, n = line.split()[:3]
        keys.setdefault(int(o, 16), []).append(int(n, 16))
    rx = json.load(open(os.path.join(d, "ram_xlate.json")))
    ram = {}
    for core, m in rx.items():
        for o, c in m.items():
            ram.setdefault(int(o, 16), Counter()).update({int(n, 16): k for n, k in c.items()})
    hand = {}
    for i, row in enumerate(csv.DictReader(open(os.path.join(d, "hand_checks.csv"), newline="")), 2):
        if None in row or None in row.values():
            raise SystemExit(f"hand_checks.csv line {i}: wrong number of fields (quote a field with commas)")
        hand[int(row["old"].split("/")[0], 16)] = row
    return f, keys, ram, hand


def add_review(e, why):
    e["review"] = f"{e['review']}; {why}" if e["review"] else why


class Gen:
    def __init__(self, old_img, new_img, old, facts_dir, to):
        self.O, self.N, self.old, self.to = old_img, new_img, old, to
        self.facts, self.keys, self.ram, self.hand = load_facts(facts_dir)
        self.m = Matcher(old_img, new_img, verbose=True)
        self.cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        self.out = {}

    # ---------------------------------------------------------------- helpers
    def nb(self, a, n):
        return bytes(self.N[a - BASE:a - BASE + n])

    def ob(self, a, n):
        return bytes(self.O[a - BASE:a - BASE + n])

    def hand_note(self, a):
        h = self.hand.get(a)
        return f"; hand check: {h['my_verdict']} ({h['evidence']})" if h else ""

    def map_flash(self, a):
        """(new, status, review, method) for one flash address."""
        r = self.m.map(a)
        new, st, conf = r["new"], r["status"], r["conf"]
        f = r.get("func") or {}
        why = f"matcher {st}/{conf or '-'} ({r['method'][:70]})"
        h = self.hand.get(a)
        if h and h["my_verdict"].startswith("WRONG"):
            return None, "unmatched", f"{why}; hand check says WRONG ({h['evidence']}): needs a fact", r["method"]
        if new is None:
            return None, "unmatched", why + self.hand_note(a), r["method"]
        if new == a and st in ("exact", "same-code-moved"):
            return new, "exact", None, r["method"]              # same address, same code
        if st in ("exact", "same-code-moved") and conf == "high":
            return new, "moved", None, r["method"]
        if st == "same-code-moved":
            return new, "moved", why + self.hand_note(a), r["method"]
        return (new, "changed", why + (f"; function {f.get('fstatus')}: {f.get('fnote', '')[:60]}" if f else "")
                + self.hand_note(a), r["method"])

    def relocated(self, kind, old_site, new_site, ob, nb):
        """The bytes differ only by relocation: the same branch to the mapped target, or (data) a
        pointer to the mapped target."""
        if kind == "data" and len(ob) == 4:
            vo, vn = struct.unpack("<I", ob)[0], struct.unpack("<I", nb)[0]
            r = self.m.map(vo & ~1) if 0x08040000 <= vo < 0x08200000 else None
            return bool(r and r["new"] is not None and r["new"] | (vo & 1) == vn
                        and r["status"] in ("exact", "same-code-moved"))
        io = next(self.cs.disasm(ob, old_site, 1), None)
        inn = next(self.cs.disasm(nb, new_site, 1), None)
        if not io or not inn or io.size != len(ob) or io.mnemonic != inn.mnemonic:
            return False
        if io.mnemonic in ("bl", "b.w", "b", "blx") and io.op_str.startswith("#") and inn.op_str.startswith("#"):
            r = self.m.map(int(io.op_str[1:], 16))
            return r["new"] is not None and r["new"] == int(inn.op_str[1:], 16)
        return False

    def map_ram(self, v):
        c = self.ram.get(v)
        if c:
            (n, k), tot = c.most_common(1)[0], sum(c.values())
            if k == tot or (k >= 10 and k >= 0.95 * tot):      # odd votes come from data decoded as literals
                return n, ("exact" if n == v else "moved"), None
            return n, "changed", f"RAM literal votes disagree: {dict((hex(a), b) for a, b in c.items())}"
        # not a literal anywhere (a field of a global): infer from the unanimous literals around it
        def near(below):
            cs = [o for o in self.ram if (o <= v if below else o > v) and abs(v - o) < 0x2000 and (o >> 16) == (v >> 16)]
            for o in sorted(cs, reverse=below):
                (n, k), tot = self.ram[o].most_common(1)[0], sum(self.ram[o].values())
                if k == tot and k >= 2:
                    return o, n - o, k
            return None
        lo, hi = near(True), near(False)
        if lo and hi and lo[1] == hi[1]:
            return v + lo[1], "changed", (f"no literal of it; between the literals {lo[0]:#x} and {hi[0]:#x} "
                                          f"(both delta {lo[1]:+#x}): inferred")
        if lo:
            return v + lo[1], "changed", f"no literal of it; inferred from the literal {lo[0]:#x} below (+{v - lo[0]:#x}, delta {lo[1]:+#x})"
        return None, "unmatched", "no literal of it and none near it"

    def emit(self, name, s, value, status, review, orig=None, note=None):
        self.out[name] = dict(kind=s.kind, value=value, orig=orig, note=s.note if note is None else note,
                              was=s.value, status=status, review=review)

    # ---------------------------------------------------------------- entries
    def run(self):
        F = self.facts
        for name, s in self.old.syms.items():
            if name in getattr(F, "REVIEWED", {}):
                self.reviewed(name, s, F.REVIEWED[name])
                continue
            if name in F.FACTS:
                value, status, why = F.FACTS[name]
                orig = None
                if s.orig is not None and value is not None:
                    orig = self.nb(value, len(s.orig))
                    if name == "VERSION_STR":
                        pass   # the version string: its new text is expected
                    elif orig != s.orig:
                        why = (why or "") + f"; bytes at the site changed: {s.orig.hex()} -> {orig.hex()}"
                        status = "changed"
                self.emit(name, s, value, status, why if status != "fact" or F.FACT_NEEDS_REVIEW.get(name) else None,
                          orig, note=s.note)
                if F.FACT_NEEDS_REVIEW.get(name):
                    self.out[name]["review"] = F.FACT_NEEDS_REVIEW[name] + (f"; {why}" if why else "")
                continue
            if name in F.REVIEW_OURS:
                self.emit(name, s, s.value, "ours", F.REVIEW_OURS[name])
                continue
            k = s.kind
            if k in ("code", "data"):
                if isinstance(s.value, tuple):
                    vals, sts, whys = [], [], []
                    for a in s.value:
                        n, st, why, _ = self.map_flash(a)
                        vals.append(n); sts.append(st); whys.append(why)
                    worst = max(sts, key=["exact", "moved", "changed", "unmatched"].index)
                    rv = "; ".join(f"{a:#x}: {w}" for a, w in zip(s.value, whys) if w) or None
                    if None in vals:
                        self.emit(name, s, None, "unmatched", rv); continue
                    self.emit(name, s, tuple(vals), worst, rv)
                    continue
                n, st, why, method = self.map_flash(s.value)
                if k == "data" and n is not None and n != s.value and method.startswith("content") \
                        and not any(self.ob(s.value, 8)):
                    why = (why + "; " if why else "") + ("content match on a window that starts with zeros: "
                                                         "low confidence, find the code that loads it")
                    st = "changed"
                orig = None
                if s.orig is not None and n is not None:
                    orig = self.nb(n, len(s.orig))
                    if orig != s.orig:
                        moved = self.relocated(s.kind, s.value, n, s.orig, orig)
                        if moved:
                            if st == "exact":
                                st = "moved"
                        else:
                            why = (why + "; " if why else "") + f"bytes at the site changed: {s.orig.hex()} -> {orig.hex()}"
                            if st in ("exact", "moved"):
                                st = "changed"
                self.emit(name, s, n, st, why, orig)
            elif k == "ram":
                n, st, why = self.map_ram(s.value)
                self.emit(name, s, n, st, why)
            elif k == "key":
                ns = self.keys.get(s.value)
                rule = next((d for lo, hi, d in getattr(F, "KEY_RANGES", ()) if lo <= s.value <= hi), None)
                if not ns and rule is not None:
                    self.emit(name, s, s.value + rule, "inferred", None,     # expected_values decides REVIEW
                              note=(s.note + " " if s.note else "") + "[key range rule]")
                elif not ns:
                    self.emit(name, s, None, "unmatched", f"key {s.value:#x} not in the measured renumbering")
                elif len(ns) > 1 or any(o != s.value and ns == self.keys[o] for o in self.keys):
                    self.emit(name, s, ns[0], "changed", f"key {s.value:#x}: the renumbering maps several old keys to {ns[0]:#x}")
                else:
                    self.emit(name, s, ns[0], "exact" if ns[0] == s.value else "moved", None)
            elif k == "offset":
                self.emit(name, s, None, "unmatched",
                          "struct offset: not measured for this base (the matcher maps addresses only); measure it, "
                          "record it in expected_values.py and in facts.py REVIEWED")
            elif k == "event":
                self.emit(name, s, None, "unmatched",
                          "stock event / page / button / type id: not measured for this base; find it in the code "
                          "that posts or compares it, record it in expected_values.py and in facts.py REVIEWED")
            elif k == "const":
                self.const(name, s)
            elif k == "ours":
                self.emit(name, s, s.value, "ours", "our placement: confirm it is still free on this base")
        for name, why in getattr(F, "HINTS", {}).items():
            e = self.out.get(name)
            if e is None:
                raise SystemExit(f"facts.py HINTS names {name}, which is not in {self.old.module}")
            if e["review"] or e["value"] is None:
                add_review(e, why)
        return self.out

    def reviewed(self, name, s, r):
        """A person's review from a later sprint: use it, but check it against the new image."""
        value, why = r["value"], None
        orig = bytes.fromhex(r["orig"]) if r.get("orig") else None
        if orig is None and s.orig is not None:
            why = "REVIEWED entry has no orig (the bytes expected at this patch site)"
        elif orig is not None and self.nb(value, len(orig)) != orig:
            why = f"REVIEWED orig {orig.hex()} is not what the image holds ({self.nb(value, len(orig)).hex()})"
        self.emit(name, s, value, "reviewed", why, orig,
                  note=(s.note + " " if s.note else "") + f"[reviewed: {r.get('evidence', '?')}]")

    def const(self, name, s):
        o = self.out
        if name == "POOL_LEFT_INIT" or name == "POOL_CUR_INIT":
            a = o["POOL_INIT_FLASH"]["value"]
            if a is None:
                return self.emit(name, s, None, "unmatched", "POOL_INIT_FLASH is unmatched")
            v = struct.unpack_from("<I", self.N, a - BASE + (0 if name == "POOL_LEFT_INIT" else 4))[0]
            return self.emit(name, s, v, "exact" if v == s.value else "changed",
                             None if v == s.value else f"the pool's initial value changed: {s.value:#x} -> {v:#x}")
        if name in SHA_RANGES or name == "ALLOC_BUMP_SHA":
            if name == "ALLOC_BUMP_SHA":
                lo = o["ALLOC_BUMP"]["value"]
                hi = lo + self.old.ALLOC_BUMP_LEN if lo else None
            else:
                lo, hi = (o[x]["value"] for x in SHA_RANGES[name])
            if lo is None or hi is None:
                return self.emit(name, s, None, "unmatched", "its range is unmatched")
            v = hashlib.sha256(self.N[lo - BASE:hi - BASE]).hexdigest()
            return self.emit(name, s, v, "exact" if v == s.value else "changed",
                             None if v == s.value else f"the code it fingerprints changed ({lo:#x}..{hi:#x}): read it again")
        if name == "ALLOC_BUMP_LEN":
            return self.emit(name, s, s.value, "exact", None)
        return self.emit(name, s, None, "unmatched", "stock constant without a rule or fact: measure it")

    # ---------------------------------------------------------------- derived checks
    def derived(self):
        """Values the build checks against the image itself: recompute them from the new image and
        flag any disagreement with what the mapping says."""
        o, N = self.out, self.N
        words = struct.unpack_from(f"<{len(N) // 4}I", N)

        def lits(v):
            return tuple(BASE + 4 * i for i, w in enumerate(words) if w == v)
        for name, ram in (("POOL_LEFT_LITS", "POOL_LEFT_RAM"), ("POOL_CUR_LITS", "POOL_CUR_RAM")):
            e, r = o[name], o[ram]["value"]
            if r is None or e["value"] is None:
                continue
            found = lits(r)
            if found != tuple(sorted(e["value"])):
                add_review(e, f"literals of {ram} in the new image are {[hex(x) for x in found]}, "
                              f"the mapping gives {[hex(x) for x in e['value']]}")
                e["status"] = "changed"
            elif e["status"] == "moved":
                self.verified(e, f"the new image's literals of {ram} are exactly these")
        for name, ram in (("M7_HEAP_END_LITS", "M7_HEAP_END"), ("M7_STACK_BOTTOM_LITS", "M7_HEAP_END")):
            e, r = o[name], o[ram]["value"]
            if e["value"] is None or r is None:
                continue
            bad = [hex(a) for a in e["value"] if struct.unpack_from("<I", N, a - BASE)[0] != r]
            if bad:
                add_review(e, f"{bad} do not hold {ram} {r:#x}")
                e["status"] = "changed"
            elif e["status"] == "moved":
                self.verified(e, f"each holds {ram}")
        e = o["CTRL_STARTUP_LITS"]
        site = o["CTRL_STARTUP_SITE"]["value"]
        if site is not None and e["value"] is not None:
            got = tuple(struct.unpack_from("<I", N, ((a + 4) & ~3) + (struct.unpack_from("<H", N, a - BASE)[0] & 0xFF) * 4 - BASE)[0]
                        for a in (site, site + 2))
            if got != e["value"]:
                add_review(e, f"the site loads {[hex(x) for x in got]}")
                e["status"] = "changed"
            elif e["status"] == "moved":
                self.verified(e, "CTRL_STARTUP_SITE loads exactly these")
        # the M7 zero gap our M7 code lives in
        lo, hi = self.old.CTRL_INPUT, 0x080E0000
        if not any(N[lo - BASE:hi - BASE]) and not any(self.O[lo - BASE:hi - BASE]):
            for name in ("M7_TRAMP", "M7_CODE", "M7_LIMIT", "CTRL_INPUT", "CTRL_STARTUP"):
                if o[name]["status"] == "ours":
                    o[name].update(status="exact", review=None)
                    o[name]["note"] += f" [verified: {lo:#x}..{hi:#x} is all zero in both images]"

    def verified(self, e, how):
        """A matcher-confidence review settled by checking the new image directly."""
        if e["review"] and "matcher same-code-moved" in e["review"] and "changed" not in e["review"]:
            e["review"] = None
        e["note"] = (e["note"] + " " if e["note"] else "") + f"[verified: {how}]"

    # ---------------------------------------------------------------- cross-checks between the images
    def masked(self, img, a):
        """The first INSNS instructions at a, with branch targets and PC-relative operands masked."""
        out = []
        for i in self.cs.disasm(bytes(img[a - BASE:a - BASE + 4 * INSNS + 8]), a):
            op, mn = i.op_str, i.mnemonic
            if BRANCH.fullmatch(mn) or "pc" in op or mn.startswith("adr"):
                op = "*"
            w = struct.unpack_from("<I", img, (i.address & ~3) - BASE)[0]
            if classify(w):                  # inside the main image or a RAM bank (literal_check's memory map)
                mn, op = "lit", "*"          # a literal-pool word decoded as code: an address, which relocates
            out.append((mn, op))
            if len(out) == INSNS:
                break
        return out

    def masked_data(self, img, a):
        """The first 16 bytes at a as words, flash pointers masked (they relocate)."""
        ws = struct.unpack_from("<4I", img, a - BASE)
        return tuple("ptr" if 0x08040000 <= w < 0x08200000 else w for w in ws)

    def crosscheck(self):
        """Every entry the draft would accept without REVIEW: compare it between the two images."""
        old_counts, new_counts = bases.word_counts(self.O), bases.word_counts(self.N)
        exp = EXPECTED.get(self.to)
        for name, e in self.out.items():
            s = self.old.syms[name]
            if e["kind"] in bases.EXPECT_KINDS or name in bases.EXPECT_NAMES:
                if exp is None:
                    add_review(e, f"expected_values.py has no record for {self.to}")
                elif name not in exp:
                    add_review(e, f"not in expected_values.py ({self.to})")
                elif exp[name] is None:
                    if e["value"] is not None:
                        add_review(e, "expected_values.py has no independent value for it yet")
                elif exp[name] != e["value"]:
                    add_review(e, f"expected_values.py has {exp[name]:#x}")
                continue
            if e["review"] or e["value"] is None or e["status"] in ("fact", "reviewed", "ours"):
                continue
            v, w = e["value"], s.value
            if not isinstance(v, int) or not isinstance(w, int):
                continue
            if e["kind"] == "code":
                x, y = self.masked(self.O, w), self.masked(self.N, v)
                if x != y:
                    same = sum(1 for p, q in zip(x, y) if p == q)
                    add_review(e, f"first {INSNS} instructions differ ({same} same, branches / PC-relative masked)")
            elif e["kind"] == "data":
                if self.masked_data(self.O, w) != self.masked_data(self.N, v):
                    add_review(e, "first 16 bytes differ (flash pointers masked)")
            elif e["kind"] == "ram":
                a, b = old_counts[w], new_counts[v]
                if a != b and not (min(a, b) >= 10 and abs(a - b) <= max(3, 0.05 * a)):
                    add_review(e, f"literal count {a} in the old image, {b} in the new")

    # ---------------------------------------------------------------- output
    def write(self, path, to, version, src):
        def fmt(kind, v):
            if v is None:
                return "None"
            if isinstance(v, tuple):
                return "(" + ", ".join(fmt(kind, x) for x in v) + ("," if len(v) == 1 else "") + ")"
            if isinstance(v, (bytes, bytearray)):
                return repr(bytes(v))
            if isinstance(v, str):
                return repr(v)
            if kind in ("code", "data", "ram", "ours"):
                return f"0x{v:08X}"
            if kind == "const":
                return str(v)
            return f"0x{v:X}"
        sha = hashlib.sha256(self.N).hexdigest()
        n_by = Counter(e["status"] for e in self.out.values())
        rev = [n for n, e in self.out.items() if e["review"] or e["value"] is None]
        L = ["# SPDX-License-Identifier: GPL-3.0-or-later",
             f'"""Stock Bento {version} main image: DRAFT drafted by tools/addrmap/gen_table.py from {src}.py.',
             "",
             "Every entry: the value mapped to this image, `was` (its value in the source table) and `status`",
             "(exact / moved / inferred / changed / unmatched / fact / reviewed / ours). REVIEW marks what a",
             "person has to check; build.py refuses this table while any entry has REVIEW or no value, and",
             "any other reader gets an error on a REVIEW entry unless it loads the draft (allow_draft=True).",
             "Do not edit this file to review an entry: record the review in",
             "tools/addrmap/facts_<from>_<to>/facts.py REVIEWED (and keys / offsets / events in",
             "mods/m1/expected_values.py), then rerun gen_table.py.",
             f"Counts at generation: {dict(sorted(n_by.items()))}; REVIEW {len(rev)} of {len(self.out)}.",
             '"""',
             "from . import code, data, ram, offset, key, event, const, ours",
             "",
             f'VERSION = "{version}"',
             f'LABEL = "stock-{version}"',
             f'SHA256 = "{sha}"',
             f"SIZE = 0x{len(self.N):X}            # bytes; the image ends at 0x{BASE + len(self.N):08X}",
             "",
             "SYMS = dict("]
        for name, e in self.out.items():
            args = [fmt(e["kind"], e["value"])]
            if e["orig"] is not None:
                args.append(repr(e["orig"].hex()) if e["kind"] in ("code", "data") else repr(e["orig"]))
            if e["note"]:
                args.append(f"note={e['note']!r}")
            args.append(f"was={fmt(e['kind'], e['was'])}")
            args.append(f"status={e['status']!r}")
            if e["review"] or e["value"] is None:
                args.append(f"REVIEW={(e['review'] or 'no value')!r}")
            L.append(f"    {name}={e['kind']}({', '.join(args)}),")
        L.append(")")
        open(path, "w", newline="\n").write("\n".join(L) + "\n")
        return n_by, rev


def main():
    p = argparse.ArgumentParser()
    p.add_argument("old_image"); p.add_argument("new_image")
    p.add_argument("--from", dest="src", default="stock_161")
    p.add_argument("--to", default="stock_168")
    p.add_argument("--version", default="1.6.8")
    p.add_argument("--facts", default=os.path.join(HERE, "facts_161_168"))
    a = p.parse_args()
    O, N = open(a.old_image, "rb").read(), open(a.new_image, "rb").read()
    old = bases.load(a.src)
    if hashlib.sha256(O).hexdigest() != old.sha256:
        raise SystemExit(f"{a.old_image} is not {old.label}")
    bad = old.verify_image(O)
    if bad:
        raise SystemExit(f"{a.src} disagrees with its own image:\n  " + "\n  ".join(bad))
    g = Gen(O, N, old, a.facts, a.to)
    g.run()
    g.derived()
    g.crosscheck()
    out = os.path.join(M1, "bases", a.to + ".py")
    n_by, rev = g.write(out, a.to, a.version, a.src)
    new = bases.Base(importlib.reload(importlib.import_module(f"bases.{a.to}")), allow_draft=True)
    fingerprints.write(out, fingerprints.checks(new, N))
    print(f"wrote {os.path.normpath(out)}: {len(g.out)} entries, by status {dict(sorted(n_by.items()))}, REVIEW {len(rev)}")
    for n in rev:
        e = g.out[n]
        v = "None" if e["value"] is None else (hex(e["value"]) if isinstance(e["value"], int) else str(e["value"])[:40])
        w = hex(e["was"]) if isinstance(e["was"], int) else str(e["was"])[:30]
        print(f"  REVIEW {n:24} {e['kind']:6} {e['status']:9} {w:>12} -> {v:12} {e['review'] or 'no value'}")


if __name__ == "__main__":
    main()
