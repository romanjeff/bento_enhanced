"""Which 1.6.1 vtable corresponds to a given 1.5.32 vtable?

Reads the old vtable's slots (consecutive Thumb pointers into code), maps each
target through unique instruction-shape fingerprints (same method as
port_anchors.py), then finds every 1.6.1 flash word that points at a mapped
target and groups those words by their containing pointer run (= vtable).
The new vtable with the most mapped slots, at the same slot indices, is the
counterpart. Usage: python -I vtable_compare.py 0818fa34 [more old vtables]
"""
import hashlib
import os
import sys
from collections import defaultdict

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject

HERE = os.path.dirname(os.path.abspath(__file__))
OLD = (os.path.abspath(os.path.join(HERE, "..", "..", "bento-1.5.32", "ghidra")), "bento1b")
NEW = (HERE, "bento161")


def load(d, n):
    p = GhidraProject.openProject(d, n, False)
    return p, p.openProgram("/", n, True)


def fps(prog):
    L = prog.getListing(); out = {}; by = defaultdict(list)
    for f in prog.getFunctionManager().getFunctions(True):
        mn = [i.getMnemonicString() for i in L.getInstructions(f.getBody(), True)]
        if len(mn) < 6:
            continue
        h = hashlib.sha1(" ".join(mn).encode()).hexdigest()
        e = f.getEntryPoint().getOffset(); out[e] = h; by[h].append(e)
    return out, by


def word(prog, a):
    return prog.getMemory().getInt(prog.getAddressFactory().getDefaultAddressSpace().getAddress(a)) & 0xFFFFFFFF


def vtable(prog, start, lo=0x08100000, hi=0x081C0000):
    slots = []; a = start
    while True:
        v = word(prog, a)
        if not (v & 1 and lo <= v < hi):
            break
        slots.append(v & ~1); a += 4
    return slots


po, old = load(*OLD); pn, new = load(*NEW)
ofp, oby = fps(old); nfp, nby = fps(new)
fmo = old.getFunctionManager()
A_old = old.getAddressFactory().getDefaultAddressSpace().getAddress


def mapped(t):
    f = fmo.getFunctionContaining(A_old(t))
    if f is None or f.getEntryPoint().getOffset() != t:
        return None
    h = ofp.get(t)
    if h and len(oby[h]) == 1 and len(nby.get(h, [])) == 1:
        return nby[h][0]
    return None


# Index every pointer word in the new M4 image.
mem = new.getMemory(); A = new.getAddressFactory().getDefaultAddressSpace().getAddress
ptr_at = defaultdict(list)
a = 0x08100000
end = mem.getBlock(A(0x08100000)).getEnd().getOffset()
while a + 4 <= end:
    v = mem.getInt(A(a)) & 0xFFFFFFFF
    if v & 1:
        ptr_at[v & ~1].append(a)
    a += 4


def run_start(a):
    while True:
        v = word(new, a - 4)
        if v & 1 and 0x08100000 <= v < 0x081C0000:
            a -= 4
        else:
            return a


for arg in sys.argv[1:]:
    vt = int(arg, 16)
    slots = vtable(old, vt)
    print(f"\n1.5.32 vtable {vt:08x}: {len(slots)} slots")
    votes = defaultdict(list)
    for i, t in enumerate(slots):
        m = mapped(t)
        print(f"  [{i:2d}] {t:08x} -> {('%08x' % m) if m else '?'}")
        if m:
            for site in ptr_at.get(m, []):
                s = run_start(site)
                if (site - s) // 4 == i:  # same slot index
                    votes[s].append(i)
    for s, idx in sorted(votes.items(), key=lambda kv: -len(kv[1]))[:5]:
        print(f"  candidate 1.6.1 vtable {s:08x}: {len(idx)} slot matches {idx}")
po.close(); pn.close()
