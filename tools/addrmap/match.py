# SPDX-License-Identifier: GPL-3.0-or-later
"""Bento stock firmware address matcher: maps addresses of one main image (bento1.bin at 0x08040000)
to another (built for 1.6.1 -> 1.6.8; nothing in it is specific to those two builds).

Method (all measured from the two images, no Ghidra needed):
 1. Two-pass Thumb-2 linear sweep of each image segment (capstone, M-class).
    Pass 1 collects literal-pool addresses (targets of PC-relative loads) and word
    jump tables (after `ldr pc, [pc, rX, lsl #2]`); pass 2 re-sweeps treating those
    words as data, so pools never desynchronise the decoder.
 2. Each instruction becomes a position-independent token: mnemonic + operands with
    branch targets, PC-relative offsets and adr immediates masked; immediates kept.
    Literal words become LITF (flash pointer), LITR (RAM pointer), or LIT:<value>.
 3. Anchors: 12-token windows that occur exactly once in each image; each anchor is
    extended to a maximal equal run; runs are applied longest-first (one-to-one).
 4. Gaps between consecutive anchored runs that are also consecutive in the new image
    are aligned with difflib (equal -> 'gap-equal', replace of equal length -> positional).
 5. Functions = BL/BLX targets + vector entries + odd flash pointers in literals/data.
    Function status: exact (identical bytes over the function extent incl. its pool),
    same-code-moved (identical tokens; branch targets and flash literals checked to map
    consistently through the map), changed (entry matched, body differs), unmatched.
 6. Data addresses: content search of a window starting at the address, with flash
    pointers in the window translated through the code map (or wildcarded), widened
    until unique; fallback = delta of nearest unique 24-byte data anchors on both sides.

API:  m = Matcher(old_bytes, new_bytes); m.map(addr) -> dict(new, status, conf, method, ...)
CLI:  python -I match.py OLD.bin NEW.bin 0x0805F8E0 ...

The images are read from the paths given; nothing of them is stored. gen_table.py uses this to draft
the next base's symbol table (mods/m1/bases/) from the current one.
"""
import bisect, difflib, re, struct, sys
from collections import defaultdict, Counter
import capstone

BASE = 0x08040000
# Segment starts are found from vector tables at fixed bases (M7 0x08040000, aux 0x080E0000, M4 0x08100000).
SEG_BASES = (0x08040000, 0x080E0000, 0x08100000)
K = 12

PCREL = re.compile(r"\[pc, #-?(0x[0-9a-f]+|\d+)\]")
FLASH = (0x08000000, 0x08200000)


def is_flash(v):
    return FLASH[0] <= v < FLASH[1]


def is_ram(v):
    return (0x20000000 <= v < 0x20020000 or 0x24000000 <= v < 0x24080000 or 0x30000000 <= v < 0x30048000
            or 0x38000000 <= v < 0x38010000 or 0x10000000 <= v < 0x10048000)


def is_periph(v):
    return 0x40000000 <= v < 0x60000000 or 0xE0000000 <= v < 0xE0100000


class Image:
    def __init__(self, data, name):
        self.d = data
        self.name = name
        self.end = BASE + len(data)
        self.segs = []
        bases = [b for b in SEG_BASES if b < self.end]
        for i, b in enumerate(bases):
            e = bases[i + 1] if i + 1 < len(bases) else self.end
            # trim trailing zero / 0xFF fill
            o = e - BASE
            while o > b - BASE and data[o - 1] in (0, 0xFF):
                o -= 1
            self.segs.append((b, BASE + ((o + 3) & ~3)))
        self.cs = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
        self.cs.detail = False
        self.lits = {}      # addr -> size (4/8)  literal words
        self.calls1 = set()
        self.jt = set()     # word jump-table entry addresses
        self.sweep(1)
        self.prune_literals()
        self.sweep(2)
        self.build_funcs()

    def prune_literals(self):
        """Pass-1 literals come partly from junk decodes of data; never let one cover a
        call target or vector entry (those are code for certain)."""
        sure = set()
        for (s, e) in self.segs:
            for k in range(1, 166):
                v = self.u32(s + 4 * k)
                if is_flash(v) and (v & 1):
                    sure.add(v & ~1)
        sure |= self.calls1
        # vtable methods: odd flash pointers in runs of >= 2 consecutive aligned words
        d, prev = self.d, False
        for o in range(0, len(d) - 7, 4):
            v, w = struct.unpack_from("<II", d, o)
            a_ok = (v & 1) and BASE <= v < self.end
            b_ok = (w & 1) and BASE <= w < self.end
            if a_ok and (b_ok or prev):
                sure.add(v & ~1)
            prev = a_ok
        for la in list(self.lits):
            n = self.lits[la]
            if any((la + k) in sure for k in range(0, n, 2)):
                if n == 8 and not any((la + k) in sure for k in (0, 2)):
                    self.lits[la] = 4
                else:
                    del self.lits[la]
        self.jt = {q for q in self.jt if q not in sure}

    def u32(self, a):
        return struct.unpack_from("<I", self.d, a - BASE)[0]

    def sweep(self, npass):
        toks, addrs, sizes, info = [], [], [], []
        lits = self.lits
        for (s, e) in self.segs:
            # the vector table is data: 4-byte words up to the first handler
            vt_end = min(s + 4 * 166, e)
            pc = s
            while pc < vt_end:
                v = self.u32(pc)
                toks.append("VEC" if (is_flash(v) or is_ram(v)) else "VEC:%x" % v)
                addrs.append(pc); sizes.append(4); info.append(("vec", v))
                pc += 4
            while pc < e:
                if npass == 2 and pc in lits:
                    n = lits[pc]
                    for k in range(0, n, 4):
                        if pc + k + 4 > e:
                            break
                        v = self.u32(pc + k)
                        if is_flash(v):
                            t = "LITF"
                        elif is_ram(v):
                            t = "LITR"
                        else:
                            t = "LIT:%x" % v
                        toks.append(t); addrs.append(pc + k); sizes.append(4); info.append(("lit", v))
                    pc += n
                    continue
                if npass == 2 and pc in self.jt:
                    v = self.u32(pc)
                    toks.append("JTF"); addrs.append(pc); sizes.append(4); info.append(("lit", v))
                    pc += 4
                    continue
                chunk = self.d[pc - BASE: min(e, pc + 0x400) - BASE]
                progressed = False
                for ins in self.cs.disasm(chunk, pc):
                    a = ins.address
                    if npass == 2 and any((a + k) in lits or (a + k) in self.jt for k in (0, 2) if k < ins.size):
                        if a + 2 in lits or a + 2 in self.jt:
                            # instruction would overlap a literal: emit half-word and resync
                            hw = struct.unpack_from("<H", self.d, a - BASE)[0]
                            toks.append("H:%04x" % hw); addrs.append(a); sizes.append(2); info.append(("h", None))
                            pc = a + 2
                        else:
                            pc = a
                        progressed = True
                        break
                    if a + ins.size > e:
                        pc = a
                        break
                    tok, inf = self.tokenize(ins, npass)
                    toks.append(tok); addrs.append(a); sizes.append(ins.size); info.append(inf)
                    pc = a + ins.size
                    progressed = True
                    if inf[0] == "jt" and npass == 1:
                        # word jump table follows (aligned)
                        q = (pc + 3) & ~3
                        while q + 4 <= e and is_flash(self.u32(q)) and (self.u32(q) & 1):
                            self.jt.add(q)
                            q += 4
                        break
                    if inf[0] == "jt" and npass == 2:
                        break
                if not progressed:
                    # undecodable half-word
                    hw = struct.unpack_from("<H", self.d, pc - BASE)[0]
                    toks.append("H:%04x" % hw); addrs.append(pc); sizes.append(2); info.append(("h", None))
                    pc += 2
        if npass == 2:
            self.toks, self.addrs, self.sizes, self.info = toks, addrs, sizes, info
            self.aidx = {a: i for i, a in enumerate(addrs)}

    def tokenize(self, ins, npass):
        m, op = ins.mnemonic, ins.op_str
        a = ins.address
        if m in ("bl", "blx") and op.startswith("#"):
            t = int(op[1:], 16)
            if npass == 1:
                self.calls1.add(t)
            return m + " T", ("call", t)
        if (m[0] == "b" and op.startswith("#") and m not in ("bkpt",)) or (m in ("cbz", "cbnz")):
            parts = op.split("#")
            t = int(parts[-1], 16)
            return m + " " + parts[0] + "T", ("br", t)
        if m.startswith("adr"):
            parts = op.split("#")
            try:
                tgt = ((a + 4) & ~3) + int(parts[-1], 0)
            except ValueError:
                tgt = None
            return m + " " + parts[0] + "#M", ("adr", tgt)
        if "[pc, #" in op:
            mm = PCREL.search(op)
            disp = int(mm.group(1), 0)
            if "#-" in mm.group(0):
                disp = -abs(disp)
            la = ((a + 4) & ~3) + disp
            size = 8 if (m.startswith("ldrd") or (m.startswith("vldr") and " d" in " " + op.split(",")[0])) else 4
            if m.startswith("ldr") or m.startswith("vldr"):
                if npass == 1 and is_flash(la) and la < self.end:
                    if la not in self.lits or self.lits[la] < size:
                        self.lits[la] = size
            return m + " " + PCREL.sub("[pc, #M]", op), ("pcrel", la)
        if m.startswith("ldr") and op.startswith("pc, [") and "lsl #2" in op:
            return m + " " + op, ("jt", None)
        return m + " " + op, ("i", None)

    def build_funcs(self):
        ent = set()
        for (s, e) in self.segs:
            # vector table entries
            for k in range(1, 166):
                v = self.u32(s + 4 * k)
                if is_flash(v) and (v & 1) and s <= v < e:
                    ent.add(v & ~1)
        for tok, inf in zip(self.toks, self.info):
            if inf[0] == "call" and is_flash(inf[1]):
                ent.add(inf[1])
            elif inf[0] == "lit" and is_flash(inf[1]) and (inf[1] & 1):
                ent.add(inf[1] & ~1)
        # odd flash pointers anywhere in data (vtables), 4-aligned
        for o in range(0, len(self.d) - 3, 4):
            v = struct.unpack_from("<I", self.d, o)[0]
            if (v & 1) and is_flash(v):
                ent.add(v & ~1)
        # keep only entries that start a decoded instruction in a segment
        self.funcs = sorted(x for x in ent if x in self.aidx and self.info[self.aidx[x]][0] not in ("lit", "vec"))
        self.fset = set(self.funcs)
        self.find_text_end()

    def find_text_end(self):
        """Text end per segment. Reachability from the vector tables over calls, tail
        branches and code-pointer literals; a reached entry counts for the text end only if it
        starts with a push and has >= 2 distinct callers (junk BLs decoded from rodata hit
        random addresses). The text ends at the function after the highest such entry."""
        callers = defaultdict(set)
        for a, (kind, v) in zip(self.addrs, self.info):
            if kind == "call" and v in self.fset:
                callers[v].add(a)
        seen, work = set(), []
        for (s, e) in self.segs:
            for k in range(1, 166):
                v = self.u32(s + 4 * k)
                if is_flash(v) and (v & 1) and (v & ~1) in self.fset:
                    work.append(v & ~1)
        while work:
            f = work.pop()
            if f in seen:
                continue
            seen.add(f)
            k = bisect.bisect_right(self.funcs, f)
            nxt = self.funcs[k] if k < len(self.funcs) else self.end
            i = self.aidx[f]
            while i < len(self.addrs) and self.addrs[i] < nxt:
                kind, v = self.info[i]
                if v is not None and kind in ("call", "br") and v in self.fset:
                    work.append(v)
                elif v is not None and kind == "lit" and (v & 1) and (v & ~1) in self.fset:
                    work.append(v & ~1)
                i += 1
        strong = [f for f in seen if len(callers[f]) >= 2 and self.toks[self.aidx[f]].startswith("push")]
        self.reach = seen
        self.text_end = {}
        for (s, e) in self.segs:
            r = [f for f in strong if s <= f < e]
            if not r:
                self.text_end[s] = e
                continue
            k = bisect.bisect_right(self.funcs, max(r))
            self.text_end[s] = min(self.funcs[k] if k < len(self.funcs) else e, e)
    def in_text(self, a):
        for s, e in self.segs:
            if s <= a < e:
                return a < self.text_end[s]
        return False

    def func_of(self, a):
        i = bisect.bisect_right(self.funcs, a) - 1
        if i < 0:
            return None, None
        f = self.funcs[i]
        seg_end = next((e for s, e in self.segs if s <= f < e), self.end)
        nxt = self.funcs[i + 1] if i + 1 < len(self.funcs) else seg_end
        return f, min(nxt, seg_end)

    def tok_index_at(self, a):
        """index of the token covering address a, and the offset into it"""
        i = bisect.bisect_right(self.addrs, a) - 1
        if i < 0:
            return None, 0
        if a < self.addrs[i] + self.sizes[i]:
            return i, a - self.addrs[i]
        return None, 0


def kgram_hashes(ids, k):
    h = []
    n = len(ids)
    for i in range(n - k + 1):
        h.append(hash(tuple(ids[i:i + k])))
    return h


class Matcher:
    def __init__(self, old, new, verbose=True):
        self.A = Image(old, "old")
        self.B = Image(new, "new")
        if verbose:
            print(f"old: {len(self.A.toks)} tokens, {len(self.A.funcs)} funcs, segs {[(hex(s), hex(e)) for s, e in self.A.segs]}")
            print(f"new: {len(self.B.toks)} tokens, {len(self.B.funcs)} funcs, segs {[(hex(s), hex(e)) for s, e in self.B.segs]}")
        vocab = {}
        self.ia = [vocab.setdefault(t, len(vocab)) for t in self.A.toks]
        self.ib = [vocab.setdefault(t, len(vocab)) for t in self.B.toks]
        self.align(verbose)
        self.build_data_anchors()
        self.build_xrefs()
        self.fcache = {}

    # ---------- token alignment ----------
    def align(self, verbose):
        ia, ib = self.ia, self.ib
        ha, hb = kgram_hashes(ia, K), kgram_hashes(ib, K)
        ca, cb = Counter(ha), Counter(hb)
        posb = {}
        for j, h in enumerate(hb):
            if cb[h] == 1:
                posb[h] = j
        runs = set()
        seen_i = set()
        for i, h in enumerate(ha):
            if ca[h] != 1 or h not in posb or i in seen_i:
                continue
            j = posb[h]
            if ia[i:i + K] != ib[j:j + K]:
                continue
            s, t = i, j
            while s > 0 and t > 0 and ia[s - 1] == ib[t - 1]:
                s -= 1; t -= 1
            e, f = i + K, j + K
            while e < len(ia) and f < len(ib) and ia[e] == ib[f]:
                e += 1; f += 1
            runs.add((s, t, e - s))
            for q in range(i, e):
                seen_i.add(q)
        m = [-1] * len(ia)
        how = [None] * len(ia)
        usedb = [False] * len(ib)
        for s, t, n in sorted(runs, key=lambda r: -r[2]):
            for q in range(n):
                if m[s + q] < 0 and not usedb[t + q]:
                    m[s + q] = t + q; usedb[t + q] = True; how[s + q] = "anchor"
        nanch = sum(1 for x in m if x >= 0)
        # gap filling between consecutive matched tokens that are consecutive in new
        i = 0
        n = len(ia)
        filled = 0
        while i < n:
            if m[i] >= 0:
                i += 1
                continue
            g0 = i
            while i < n and m[i] < 0:
                i += 1
            g1 = i  # old gap [g0, g1)
            if g0 == 0 or g1 >= n:
                continue
            p, q = m[g0 - 1], m[g1]
            if q <= p + 1 or (q - p - 1) > 20000 or (g1 - g0) > 20000:
                continue
            h0, h1 = p + 1, q
            if any(usedb[h0:h1]):
                continue
            sm = difflib.SequenceMatcher(None, ia[g0:g1], ib[h0:h1], autojunk=False)
            for tag, a0, a1, b0, b1 in sm.get_opcodes():
                if tag == "equal":
                    for k in range(a1 - a0):
                        m[g0 + a0 + k] = h0 + b0 + k; how[g0 + a0 + k] = "gap-equal"; filled += 1
                elif tag == "replace" and (a1 - a0) == (b1 - b0):
                    for k in range(a1 - a0):
                        m[g0 + a0 + k] = h0 + b0 + k; how[g0 + a0 + k] = "gap-replace"; filled += 1
        self.m, self.how = m, how
        # anchored neighbours for replace/insert positions: keep gap bounds for interpolation
        if verbose:
            print(f"aligned: {nanch} anchor tokens ({100*nanch/len(ia):.1f}%), +{filled} gap tokens; "
                  f"unmatched {sum(1 for x in m if x < 0)}")

    def tmap(self, a):
        """code-token map of address a -> (new, how) or (None, why)"""
        A, B = self.A, self.B
        i, off = A.tok_index_at(a)
        if i is None:
            return None, "no-token"
        j = self.m[i]
        if j < 0:
            return None, "unaligned"
        if off and B.sizes[j] != A.sizes[i]:
            return B.addrs[j] + off, self.how[i] + "+off-size-differs"
        return B.addrs[j] + off, self.how[i]

    # ---------- data anchors ----------
    def build_data_anchors(self, L=24):
        a, b = self.A.d, self.B.d
        cb = {}
        for o in range(0, len(b) - L):
            k = b[o:o + L]
            if k in cb:
                cb[k] = -1
            else:
                cb[k] = o
        self.danch = []  # (old_off, new_off) unique-content anchors, every 8 bytes
        ca = Counter(a[o:o + L] for o in range(0, len(a) - L, 4))
        for o in range(0, len(a) - L, 8):
            k = a[o:o + L]
            if len(set(k)) < 6:
                continue  # low-entropy runs are not anchors
            if ca[k] == 1 and cb.get(k, -1) >= 0:
                self.danch.append((o, cb[k]))
        self.dkeys = [x[0] for x in self.danch]

    def _pattern(self, o, L):
        """regex for old bytes [o, o+L): code pointers translated through the token map when
        anchored, other flash pointers wildcarded; returns (regex, fixed_bytes, wildcards)"""
        A = self.A
        parts, wild, k = [], 0, 0
        while k < L:
            if (o + k) % 4 == 0 and k + 4 <= L:
                v = struct.unpack_from("<I", A.d, o + k)[0]
                if is_flash(v) and v < A.end:
                    nv, how = self.tmap(v & ~1) if (v & 1) else (None, "")
                    if nv is not None and how in ("anchor", "gap-equal"):
                        parts.append(re.escape(struct.pack("<I", nv | (v & 1))))
                    else:
                        parts.append(b"(?s:....)"); wild += 1
                    k += 4
                    continue
            parts.append(re.escape(A.d[o + k:o + k + 1]))
            k += 1
        return re.compile(b"".join(parts), re.S), L - 4 * wild, wild

    def _find(self, rx, limit=2):
        hits = []
        for mm in rx.finditer(self.B.d):
            hits.append(mm.start())
            if len(hits) >= limit:
                break
        return hits

    def dmap(self, addr):
        """content search for data address; returns (new, conf, method)"""
        A = self.A
        o = addr - BASE
        for L in (16, 32, 64, 128, 256):
            if o + L > len(A.d):
                break
            rx, fixed, wild = self._pattern(o, L)
            hits = self._find(rx)
            if not hits:
                break
            if len(hits) == 1 and fixed >= 12:
                conf = "high" if fixed >= 16 else "medium"
                return BASE + hits[0], conf, f"content{L}" + (f"/wild{wild}" if wild else "")
        # bytes at the address changed (or ambiguous): use the context just before it
        for L in (32, 64, 128):
            if o - L < 0:
                break
            rx, fixed, wild = self._pattern(o - L, L)
            hits = self._find(rx)
            if not hits:
                break
            if len(hits) == 1 and fixed >= 16:
                return BASE + hits[0] + L, "medium", f"content-before{L}" + (f"/wild{wild}" if wild else "")
        r = self._dmap_simple(addr, 0x4000)
        if r is not None:
            return r
        return None, None, "no unique content; data anchors disagree"

    def _dmap_simple(self, v, maxspan=0x400):
        o = v - BASE
        i = bisect.bisect_right(self.dkeys, o) - 1
        if 0 <= i < len(self.danch) - 1:
            (o0, n0), (o1, n1) = self.danch[i], self.danch[i + 1]
            if n0 - o0 == n1 - o1 and o1 - o0 <= maxspan:
                if maxspan == 0x400:
                    return v + (n0 - o0)
                return v + (n0 - o0), ("medium" if o1 - o0 <= 0x400 else "low"), f"data-anchor-delta(span {o1-o0:#x})"
        return None

    # ---------- functions ----------
    def func_status(self, fo):
        if fo in self.fcache:
            return self.fcache[fo]
        A, B = self.A, self.B
        f, fe = A.func_of(fo)
        r = dict(fold=f, fold_end=fe, fnew=None, fstatus="unmatched", fnote="")
        if f is None:
            self.fcache[fo] = r
            return r
        nf, how = self.tmap(f)
        if nf is None:
            # majority vote of interior instruction mappings
            i0, i1 = A.aidx.get(f), A.tok_index_at(fe - 1)[0]
            votes = Counter()
            if i0 is not None and i1 is not None:
                for i in range(i0, i1 + 1):
                    if self.m[i] >= 0:
                        g, _ = B.func_of(B.addrs[self.m[i]])
                        votes[g] += 1
            if votes:
                g, c = votes.most_common(1)[0]
                tot = (i1 - i0 + 1)
                if c >= max(3, tot * 0.3):
                    r.update(fnew=g, fstatus="changed", fnote=f"entry unaligned; {c}/{tot} insns vote")
            self.fcache[fo] = r
            return r
        g, ge = B.func_of(nf)
        if g != nf:
            r.update(fnew=nf, fstatus="changed", fnote=f"mapped entry is not a function start in new (inside {g:#x})" if g else "")
            self.fcache[fo] = r
            return r
        r["fnew"] = g
        ob, nb = A.d[f - BASE:fe - BASE], B.d[g - BASE:ge - BASE]
        if ob == nb:
            r["fstatus"] = "exact"
        else:
            i0, i1 = A.aidx[f], A.tok_index_at(fe - 1)[0]
            j0, j1 = B.aidx[g], B.tok_index_at(ge - 1)[0]
            ta, tb = self.ia[i0:i1 + 1], self.ib[j0:j1 + 1]
            if ta == tb:
                bad = self.check_targets(i0, j0, i1 - i0 + 1)
                r["fstatus"] = "same-code-moved"
                r["fnote"] = "targets consistent" if not bad else f"{len(bad)} target/literal inconsistencies: {bad[:3]}"
                if bad:
                    r["fstatus"] = "same-code-moved?"
            else:
                sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
                r["fstatus"] = "changed"
                r["fnote"] = f"token similarity {sm.ratio():.3f}; old {len(ta)} / new {len(tb)} tokens"
                r["ratio"] = sm.ratio()
        self.fcache[fo] = r
        return r

    def check_targets(self, i0, j0, n):
        A, B = self.A, self.B
        bad = []
        for k in range(n):
            ia, ib = A.info[i0 + k], B.info[j0 + k]
            if ia[0] in ("call", "br") or (ia[0] == "lit" and is_flash(ia[1])):
                vo, vn = ia[1], ib[1]
                if vo is None or not is_flash(vo):
                    continue
                if vo >= A.end:
                    if vn != vo:
                        bad.append((hex(A.addrs[i0 + k]), hex(vo), hex(vn)))
                    continue
                pm, _ = self.tmap(vo & ~1)
                if pm is None:
                    pm = self._dmap_simple(vo)
                    if pm is None:
                        continue
                if (pm | (vo & 1)) != vn:
                    bad.append((hex(A.addrs[i0 + k]), hex(vo), hex(vn)))
        return bad

    # ---------- public ----------
    def map(self, addr):
        A, B = self.A, self.B
        thumb = addr & 1
        a = addr & ~1 if thumb else addr
        res = dict(old=addr, new=None, status="unmatched", conf="", method="", kind="", func=None)
        if not (BASE <= a < A.end):
            res.update(status="outside-stock", kind="outside")
            return res
        if not any(s <= a < e for s, e in A.segs):
            res.update(status="outside-stock", kind="fill/gap")
            return res
        i, off = A.tok_index_at(a)
        kind = A.info[i][0] if i is not None else "?"
        f, fe = A.func_of(a)
        is_code = kind in ("call", "br", "adr", "pcrel", "i", "jt") and f is not None and A.in_text(a)
        nt, how = self.tmap(a)
        if is_code and nt is not None:
            fs = self.func_status(a)
            res.update(new=nt | thumb, kind="code", method="token-" + how, func=fs)
            if fs["fstatus"] == "exact":            # identical bytes over the whole function (it may have moved)
                res["status"], res["conf"] = "exact", "high"
            elif fs["fstatus"] == "same-code-moved":
                res["status"], res["conf"] = "same-code-moved", "high"
            elif fs["fstatus"] in ("same-code-moved?",):
                res["status"], res["conf"] = "same-code-moved", "medium"
            else:
                res["status"] = "changed"
                res["conf"] = "high" if how == "anchor" else ("medium" if how == "gap-equal" else "low")
            if fs["fnew"] is None:
                res["status"] = "changed"
            return res
        # data (literal, vector, table, string) or code that the token alignment did not cover
        cands = []
        nd, conf, method = self.dmap(a)
        if nd is not None:
            cands.append((nd, conf, method))
        xr = self.xmap(a)
        if xr[0] is not None:
            cands.append(xr)
        if nt is not None and not is_code:
            cands.append((nt, "low", "token-" + how))
        if is_code and i is not None:
            for d in range(1, 9):
                if i + d < len(self.m) and self.m[i + d] >= 0 and self.how[i + d] != "gap-replace":
                    cands.append((B.addrs[self.m[i + d]] - (A.addrs[i + d] - a), "low", f"next aligned insn -{A.addrs[i + d] - a:#x}"))
                    break
            for d in range(1, 9):
                if i - d >= 0 and self.m[i - d] >= 0 and self.how[i - d] != "gap-replace":
                    cands.append((B.addrs[self.m[i - d]] + (a - A.addrs[i - d]), "low", f"prev aligned insn +{a - A.addrs[i - d]:#x}"))
                    break
        if is_code:
            def prio(c):
                m_ = c[2]
                return (0 if m_.startswith("xref ") and "base" not in m_ else 1 if m_.startswith("next") else
                        2 if m_.startswith("prev") else 3 if m_.startswith("content") else 4)
            cands.sort(key=prio)
        best = None
        for c in cands:
            sc, n = self.verify(a, c[0])
            c2 = (c[0], c[1], c[2] + f"; verify {sc}/{n}")
            if best is None or sc / max(n, 1) > best[0] + 1e-9:
                best = (sc / max(n, 1), c2, sc, n)
        agree = len({c[0] for c in cands}) == 1 and len(cands) > 1
        if is_code:
            fs = self.func_status(a)
            res.update(kind="code", func=fs)
            xr_exact = xr[0] is not None and best is not None and best[1][0] == xr[0] and "base" not in xr[2] and self.xv.get(a, (0, 0, 0))[1] >= 2
            if best is not None and best[0] < 0.5 and not xr_exact:
                res.update(status="unmatched", method="code-unaligned; best candidate rejected: " + best[1][2])
                return res
            if best is not None:
                nn, cf, mt = best[1]
                cf = "medium" if (agree or best[0] >= 0.9) else "low"
                if xr[0] is not None and nn == xr[0] and xr[1] == "high":
                    cf = "high" if self.xv.get(a, (0, 0, 0))[1] >= 3 else "medium"
                res.update(new=nn | thumb, status="changed", conf=cf, method="code-unaligned; " + mt)
            else:
                res.update(status="unmatched", method="code-unaligned; " + (method or ""))
            return res
        res["kind"] = "data" if kind not in ("lit", "vec") else kind
        if best is not None:
            nn, cf, mt = best[1]
            if agree:
                cf = "high"
                mt += f"; {len(cands)} methods agree"
            elif len(cands) > 1:
                mt += "; disagree: " + ",".join(f"{c[2].split(';')[0]}={c[0]:#x}" for c in cands)
                if best[0] < 0.9:
                    cf = "low"
            same = A.d[a - BASE:a - BASE + 16] == B.d[nn - BASE:nn - BASE + 16]
            res.update(new=nn | thumb, status="exact" if same else ("same-code-moved" if best[0] >= 0.9 else "changed"),
                       conf=cf, method=mt)
        else:
            res.update(method=method)
        return res

    # ---------- reference-derived map ----------
    def build_xrefs(self):
        """every aligned token pair that carries a flash value (call/branch target, literal,
        jump-table word, adr/PC-relative target) votes old value -> new value"""
        A, B = self.A, self.B
        votes = defaultdict(Counter)
        for i, j in enumerate(self.m):
            if j < 0 or self.how[i] == "gap-replace":
                continue
            ia, ib = A.info[i], B.info[j]
            if ia[0] in ("call", "br", "lit", "pcrel", "adr", "vec") and ia[1] is not None and ib[1] is not None:
                if is_flash(ia[1]) and is_flash(ib[1]) and ia[1] < A.end:
                    votes[ia[1] & ~1][ib[1] & ~1] += 1
        rv = defaultdict(Counter)
        for i, j in enumerate(self.m):
            if j >= 0 and self.how[i] != "gap-replace" and A.info[i][0] == "lit" and B.info[j][0] == "lit":
                vo, vn = A.info[i][1], B.info[j][1]
                if vo is not None and vn is not None and not is_flash(vo) and (is_ram(vo) or is_ram(vn)):
                    rv[vo][vn] += 1
        self.ram_votes = rv
        self.xv = {}
        for o, c in votes.items():
            (n, k), tot = c.most_common(1)[0], sum(c.values())
            if k * 2 > tot:
                self.xv[o] = (n, k, tot)
        self.xkeys = sorted(self.xv)

    def xmap(self, a, maxoff=0x100):
        if a in self.xv:
            n, k, tot = self.xv[a]
            return n, ("high" if k >= 2 and k == tot else "medium"), f"xref {k}/{tot} refs"
        i = bisect.bisect_right(self.xkeys, a) - 1
        if i >= 0 and a - self.xkeys[i] <= maxoff:
            t = self.xkeys[i]
            n, k, tot = self.xv[t]
            return n + (a - t), "medium", f"xref base {t:#x}+{a-t:#x} ({k}/{tot} refs)"
        return None, None, ""

    def verify(self, a, n, L=32):
        """structural check of old [a,a+L) vs new [n,n+L): bytes equal, or both words are
        flash pointers whose targets correspond (code via map, data via 8 bytes of content)"""
        A, B = self.A, self.B
        ok = tot = 0
        k = 0
        while k < L:
            oa, ob = a - BASE + k, n - BASE + k
            if ob + 4 > len(B.d) or oa + 4 > len(A.d):
                break
            if (a + k) % 4 == 0 and (n + k) % 4 == 0:
                vo, vn = struct.unpack_from("<I", A.d, oa)[0], struct.unpack_from("<I", B.d, ob)[0]
                if is_flash(vo) and vo < A.end and is_flash(vn):
                    tot += 1
                    if (vo & 1) == (vn & 1):
                        if vo & 1:
                            pm, _ = self.tmap(vo & ~1)
                            ok += pm == (vn & ~1) or (pm is None and A.d[vo - 1 - BASE:vo + 7 - BASE] == B.d[vn - 1 - BASE:vn + 7 - BASE])
                        else:
                            ok += A.d[vo - BASE:vo + 8 - BASE] == B.d[vn - BASE:vn + 8 - BASE]
                    k += 4
                    continue
            tot += 1
            ok += A.d[oa:oa + 2] == B.d[ob:ob + 2]
            k += 2
        return ok, tot

    def local_same(self, a, w=6):
        """tokens identical in a window of +-w tokens around old address a and its new image"""
        A = self.A
        i, _ = A.tok_index_at(a)
        if i is None or self.m[i] < 0:
            return 0, 0
        j = self.m[i]
        same = tot = 0
        for d in range(-w, w + 1):
            if 0 <= i + d < len(self.ia) and 0 <= j + d < len(self.ib):
                tot += 1
                same += self.ia[i + d] == self.ib[j + d]
        return same, tot


if __name__ == "__main__":
    old, new = open(sys.argv[1], "rb").read(), open(sys.argv[2], "rb").read()
    mt = Matcher(old, new)
    for s in sys.argv[3:]:
        r = mt.map(int(s, 16))
        f = r["func"] or {}
        print(f"{r['old']:#010x} -> {r['new'] and hex(r['new'])} {r['status']} {r['conf']} {r['method']} "
              f"func {f.get('fold') and hex(f['fold'])}->{f.get('fnew') and hex(f['fnew'])} {f.get('fstatus')} {f.get('fnote','')}")
