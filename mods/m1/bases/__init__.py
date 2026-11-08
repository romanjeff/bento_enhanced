# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-base symbol tables: every stock address, RAM address, struct offset, registry key, event
id and stock-dependent constant bento_enhanced uses, by name, for each stock firmware we build on.

    from bases import select, from_build
    S = select(stock_image_bytes)        # build.py: the table whose sha256 matches the image
    S.NOTE_ROUTE                         # value (code addresses without the Thumb bit)
    S.orig("COND_GATE_SITE")             # expected original bytes at a patch site
    S = from_build(out_dir)              # tests / emulator: the table a build was made from

The same names reach the C and asm sources through the generated header (S.c_header():
`#define S_NOTE_ROUTE 0x0805F8E0u`), written into the build directory by build.py.

Kinds: code (flash code; patch sites carry orig bytes), data (flash data: tables, literals,
vtable slots, strings), ram (stock RAM globals), offset (stock struct offsets), key (stock
registry / parameter keys), event (stock event / page / button / type ids: enums), const (other
stock-dependent values: counts, init values, fingerprints), ours (our own placement and ids; per
base because they can move).

Image checks (CHECKS at the end of each table, written by tools/addrmap/fingerprints.py or the
generator): every code / data entry has `fp`, the first 16 hex digits of the sha256 of the 16
image bytes at its address; every RAM entry has `lits`, the number of aligned words in the image
equal to it. build.py verifies both (verify_image), so an address edited in a table without
rechecking it in the image is refused.

A table entry may carry REVIEW (a string): the entry is a draft (e.g. machine-mapped for a new
base). A build refuses a table with a REVIEW entry that an enabled feature uses, and lists them
(build.py); the generated header and asm symbols leave every REVIEW entry out, and any reader
(build.py itself, tests, the display emulator) gets an error when it reads a REVIEW entry, unless it
asked for the draft (load(..., allow_draft=True)). A missing name is an error, never a fallback to
another base's value.

status "absent" (value None, no REVIEW): a reviewed entry that does not exist on this base (S2: the
D2 RAMCHK blocks on 1.6.8, whose D2 is stock's). It counts as reviewed, it is not in the header (C
code tests `#ifdef S_<NAME>`), and reading it is an error; `S.has(name)` asks.
"""
import hashlib, importlib, json
from pathlib import Path
from typing import NamedTuple, Optional

TABLES = ("stock_161", "stock_168")
KINDS = ("code", "data", "ram", "offset", "key", "event", "const", "ours")
IMAGE_BASE = 0x08040000
FP_BYTES = 16


class Sym(NamedTuple):
    kind: str
    value: object                 # int, tuple of ints, bytes or str; None = not mapped yet
    orig: Optional[bytes] = None  # patch sites: the bytes stock has there
    note: str = ""
    was: object = None            # drafts: the value in the table this one was derived from
    status: str = ""              # drafts: exact / moved / inferred / changed / unmatched / fact / reviewed / ours
    REVIEW: Optional[str] = None  # drafts: why a person has to check this entry
    fp: Optional[str] = None      # code / data: fingerprint of the image bytes at the address (CHECKS)
    lits: Optional[int] = None    # ram: number of literal words equal to the address (CHECKS)


def _mk(kind):
    def f(value, orig=None, note="", **kw):
        if isinstance(orig, str):
            orig = bytes.fromhex(orig)
        return Sym(kind, value, orig, note, **kw)
    f.__name__ = kind
    return f


code, data, ram, offset, key, event, const, ours = (_mk(k) for k in KINDS)


def fingerprint(img, addr):
    """The fp of an address: first 16 hex digits of sha256 over the 16 image bytes there. Where
    those are all zero (e.g. the start of a table), any address in the zero run would match, so the
    distance to the first non-zero byte (within 4 KB) and the 16 bytes from there are hashed too."""
    o = addr - IMAGE_BASE
    b = bytes(img[o:o + FP_BYTES])
    if not any(b):
        nz = next((k for k in range(o, min(len(img), o + 4096)) if img[k]), o)
        b += (nz - o).to_bytes(4, "little") + bytes(img[nz:nz + FP_BYTES])
    return hashlib.sha256(b).hexdigest()[:16]


def word_counts(img):
    """Counter of the image's aligned words (for literal_count)."""
    import struct
    from collections import Counter
    return Counter(struct.unpack_from(f"<{len(img) // 4}I", img))


def literal_count(img, value, counts=None):
    """How many aligned words of the image equal value."""
    return (counts or word_counts(img))[value]


def checked(s):
    """Which image check an entry gets: 'fp', 'lits' or None."""
    if s.kind in ("code", "data") and isinstance(s.value, int):
        return "fp"
    if s.kind == "ram" and isinstance(s.value, int):
        return "lits"
    return None


class Base:
    def __init__(self, module, allow_draft=False):
        self.module = module.__name__.rsplit(".", 1)[-1]
        self.version = module.VERSION
        self.label = module.LABEL
        self.sha256 = module.SHA256
        self.size = module.SIZE
        self.allow_draft = allow_draft
        self.syms = dict(module.SYMS)
        for n, s in self.syms.items():
            if not isinstance(s, Sym) or s.kind not in KINDS:
                raise SystemExit(f"{self.module}: {n} is not a table entry")
        for n, (fp, lits) in getattr(module, "CHECKS", {}).items():
            if n not in self.syms:
                raise SystemExit(f"{self.module}: CHECKS names {n}, which is not in SYMS")
            self.syms[n] = self.syms[n]._replace(fp=fp, lits=lits)

    def _sym(self, name):
        syms = self.__dict__.get("syms", {})
        if name not in syms:
            raise AttributeError(f"{self.__dict__.get('module')}: no symbol {name}")
        s = syms[name]
        if s.REVIEW is not None and not self.allow_draft:
            raise SystemExit(f"{self.module}: {name} is a draft entry (REVIEW: {s.REVIEW}); "
                             f"load the table with allow_draft=True to use unreviewed values")
        return s

    def __getattr__(self, name):
        s = self._sym(name)
        if s.status == "absent":
            raise SystemExit(f"{self.module}: {name} does not exist on this base ({s.note})")
        if s.value is None:
            raise SystemExit(f"{self.module}: {name} has no value for this base ({s.REVIEW})")
        return s.value

    def has(self, name):
        """The entry exists on this base and can be read (reviewed, with a value, not absent)."""
        s = self.syms.get(name)
        return s is not None and s.status != "absent" and s.value is not None and \
            (s.REVIEW is None or self.allow_draft)

    def is_reviewed(self, name):
        """A person (or the 1.6.1 table) settled it: no REVIEW, and a value or status absent."""
        s = self.syms.get(name)
        return s is not None and s.REVIEW is None and (s.value is not None or s.status == "absent")

    def orig(self, name):
        s = self._sym(name)
        if s.orig is None:
            raise SystemExit(f"{self.module}: {name} has no expected original bytes")
        return s.orig

    def unreviewed(self, names=None):
        """Draft entries (REVIEW, or no value without being absent); with names, only those (a name
        the table lacks counts as unreviewed too)."""
        pick = self.syms if names is None else names
        return [(n, self.syms.get(n)) for n in pick if not self.is_reviewed(n)]

    def require_reviewed(self):
        bad = self.unreviewed()
        if bad:
            lines = "\n".join(f"  {n:28} {s.kind:6} {s.status or '-':9} {s.REVIEW or 'no value'}" for n, s in bad)
            raise SystemExit(f"base table {self.module} ({self.label}) is a draft: {len(bad)} entries are not reviewed; "
                             f"refusing to build.\n{lines}")

    def verify_image(self, img):
        """Problems (list of str) between this table and its image: fingerprints of code / data
        entries, literal counts of RAM entries. Entries without a value are skipped (a draft)."""
        bad, counts = [], word_counts(img)
        for n, s in self.syms.items():
            c = checked(s)
            if c == "fp":
                if s.fp is None:
                    bad.append(f"{n}: no fingerprint in CHECKS (run tools/addrmap/fingerprints.py)")
                elif fingerprint(img, s.value) != s.fp:
                    bad.append(f"{n} at {s.value:#x}: image bytes {fingerprint(img, s.value)}, the table's fingerprint {s.fp}")
            elif c == "lits":
                if s.lits is None:
                    bad.append(f"{n}: no literal count in CHECKS (run tools/addrmap/fingerprints.py)")
                elif counts[s.value] != s.lits:
                    bad.append(f"{n} {s.value:#x}: {counts[s.value]} literal(s) in the image, the table says {s.lits}")
        return bad

    def c_header(self):
        """C defines for every reviewed integer entry (S_<NAME>): a draft value never reaches C, so
        a source that uses an unreviewed (or absent) entry does not compile."""
        out = [f"/* Generated by build.py from bases/{self.module}.py ({self.label}); do not edit. */",
               "#ifndef STOCK_SYMS_H", "#define STOCK_SYMS_H",
               f'#define S_BASE_LABEL "{self.label}"']
        for n, s in self.syms.items():
            if isinstance(s.value, int) and not isinstance(s.value, bool) and s.REVIEW is None:
                note = f"{s.kind}: {s.note}" if s.note else s.kind
                v = f"0x{s.value:08X}" if s.kind in ("code", "data", "ram", "ours") else str(s.value) if s.kind == "const" else f"0x{s.value:X}"
                out.append(f"#define S_{n} {v}u   /* {note.replace('*/', '* /')} */")
        out.append("#endif")
        return "\n".join(out) + "\n"

    def asm_symbols(self):
        """Template symbols for the asm sources (@S_NAME@)."""
        return {f"S_{n}": f"{s.value:#x}" for n, s in self.syms.items() if isinstance(s.value, int) and s.REVIEW is None}

    def marker_header(self):
        """For build.py's reference scan: every entry with an integer value (draft or not) defined
        as a marker token, so the preprocessor shows which names a compile unit really uses.
        Absent entries stay undefined (as in c_header), so `#ifdef S_<NAME>` picks the same code."""
        out = ["#ifndef STOCK_SYMS_H", "#define STOCK_SYMS_H", '#define S_BASE_LABEL "scan"']
        out += [f"#define S_{n} __S_REF_{n}" for n, s in self.syms.items()
                if s.status != "absent" and (s.value is None or isinstance(s.value, int))]
        return "\n".join(out + ["#endif"]) + "\n"


def load(name, allow_draft=False):
    if name not in TABLES:
        raise SystemExit(f"unknown base table {name!r}; known: {', '.join(TABLES)}")
    return Base(importlib.import_module(f"{__name__}.{name}"), allow_draft)


def find_sha(sha, allow_draft=False):
    """The table for this sha256, or None."""
    for t in TABLES:
        b = load(t, allow_draft)
        if b.sha256 == sha:
            return b
    return None


def by_sha(sha, allow_draft=False):
    b = find_sha(sha, allow_draft)
    if b is not None:
        return b
    raise SystemExit(f"no base table for an image with sha256 {sha}; known bases: "
                     + ", ".join(f"{load(t).label} ({load(t).sha256[:12]})" for t in TABLES))


def select(img, allow_draft=False):
    """The table for a stock image (bytes), by sha256."""
    return by_sha(hashlib.sha256(img).hexdigest(), allow_draft)


def from_build(out_dir, stock_img=None, allow_draft=False):
    """The table a build (or kit folder with manifest.json) was made from. Older manifests have
    no `base` field: their base_sha256 identifies it. With stock_img, also check that it is that
    base's image (a test run against the wrong stock image is refused)."""
    man = json.loads((Path(out_dir) / "manifest.json").read_text())
    b = load(man["base"], allow_draft) if "base" in man else by_sha(man["base_sha256"], allow_draft)
    if b.sha256 != man["base_sha256"]:
        raise SystemExit(f"{out_dir}: manifest base {man.get('base')} does not match its base_sha256")
    if stock_img is not None and hashlib.sha256(stock_img).hexdigest() != b.sha256:
        raise SystemExit(f"stock image is not {b.label}, the base of {out_dir}")
    return b


def verify_expected(b, expected, names=None):
    """Problems (list of str) between a table's keys / offsets / events (and REG_KEYS) and the
    independent record in expected_values.py (EXPECTED[b.module]); with names, only those entries
    (build.py: the ones this build uses)."""
    exp = expected.get(b.module)
    if exp is None:
        return [f"expected_values.py has no record for {b.module}"]
    bad = []
    for n, s in b.syms.items():
        if s.kind not in EXPECT_KINDS and n not in EXPECT_NAMES:
            continue
        if names is not None and n not in names:
            continue
        if n not in exp:
            bad.append(f"{n}: not in expected_values.py ({b.module})")
        elif exp[n] is None and s.value is not None:
            bad.append(f"{n}: the table has {s.value:#x}, expected_values.py has no independent value yet")
        elif exp[n] != s.value:
            v = "None" if s.value is None else f"{s.value:#x}"
            bad.append(f"{n}: the table has {v}, expected_values.py has {exp[n] if exp[n] is None else hex(exp[n])}")
    for n in exp:
        if n not in b.syms and (names is None or n in names):
            bad.append(f"{n}: in expected_values.py but not in {b.module}")
    return bad


EXPECT_KINDS = ("offset", "key", "event")
EXPECT_NAMES = ("REG_KEYS", "HEADER_ACTIONS", "HEADER_BUTTON_COUNT")
