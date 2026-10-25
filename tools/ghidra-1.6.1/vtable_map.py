"""Map the M4 UI's C++ classes from their vtables (no RTTI in this image).

For each pointer run (>= MIN_SLOTS Thumb pointers into M4 code) in M4 flash:
  - slot 0 is GetType(): if it is `movs r0,#N; bx lr` (or `mov.w`), N is the
    class's type tag -- the same tag model_find_by_type compares against
    (tempo engine = 0x15 in 1.5.32).
  - slot 5 (+0x14) FindChildById, slot 12 (+0x30) HandleEvent (ui-architecture.md).
  - Methods shared by many vtables at the same slot are inherited base methods;
    the count of vtables sharing a slot value shows the hierarchy's breadth.
Known names (lab_* labels, our anchors) are shown when a slot hits one.
Read-only. Output: vtable_map.txt
"""
import os
from collections import Counter, defaultdict

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject

HERE = os.path.dirname(os.path.abspath(__file__))
LO, HI = 0x08100000, 0x081C0000
MIN_SLOTS = 3  # model-object vtables can be short; widget vtables have >= 14
SKIP_BELOW = 0x08100400  # M4 vector table

p = GhidraProject.openProject(HERE, "bento161", False)
prog = p.openProgram("/", "bento161", True)
mem, L, fm = prog.getMemory(), prog.getListing(), prog.getFunctionManager()
A = prog.getAddressFactory().getDefaultAddressSpace().getAddress
blk_end = mem.getBlock(A(LO)).getEnd().getOffset()


def word(a):
    return mem.getInt(A(a)) & 0xFFFFFFFF


def is_ptr(v):
    return v & 1 and LO <= (v & ~1) < HI


def type_tag(fn):
    i = L.getInstructionAt(A(fn))
    if i is None:
        return None
    j = i.getNext()
    m = i.getMnemonicString().lower()
    if m.startswith("mov") and str(i).lower().startswith(("movs r0,#", "mov r0,#", "mov.w r0,#", "movw r0,#")) \
            and j is not None and str(j).lower() in ("bx lr",):
        return int(str(i).split("#")[1], 0)
    return None


def is_switch(start):
    for back in (4, 6):
        h1 = mem.getShort(A(start - back)) & 0xFFFF; h2 = mem.getShort(A(start - back + 2)) & 0xFFFF
        if (h1 & 0xFFF0) == 0xF850 and (h2 & 0xFFF0) == 0xF020:
            return True
    return False


def name(fn):
    f = fm.getFunctionAt(A(fn))
    return f.getName() if f and not f.getName().startswith("FUN_") else ""


vts = []
a = LO
while a + 4 <= blk_end:
    if is_ptr(word(a)) and not L.getInstructionContaining(A(a)):
        s = a; slots = []
        while a + 4 <= blk_end and is_ptr(word(a)):
            slots.append(word(a) & ~1); a += 4
        if len(slots) >= MIN_SLOTS and s >= SKIP_BELOW and not is_switch(s):
            vts.append((s, slots))
    else:
        a += 4

share = defaultdict(Counter)
for _, slots in vts:
    for i, t in enumerate(slots):
        share[i][t] += 1

tags = Counter()
rows = []
for s, slots in vts:
    t = type_tag(slots[0])
    if t is not None:
        tags[t] += 1
    he = slots[12] if len(slots) > 12 else None
    known = sorted({f"[{i}]{name(x)}" for i, x in enumerate(slots) if name(x)})
    own = sum(1 for i, x in enumerate(slots) if share[i][x] == 1)
    rows.append((s, len(slots), t, he, own, known))

with open(os.path.join(HERE, "vtable_map.txt"), "w") as out:
    out.write(f"{len(vts)} vtables (>= {MIN_SLOTS} slots) in M4 flash; "
              f"{sum(1 for r in rows if r[2] is not None)} with a constant GetType tag\n")
    out.write("Most-shared slot values (inherited base methods):\n")
    for i in range(14):
        for t, c in share[i].most_common(2):
            if c > 3:
                out.write(f"  slot {i:2d} -> {t:08x} {name(t):30s} shared by {c}\n")
    dup = {t: c for t, c in tags.items() if c > 1}
    out.write(f"Type tags used by >1 vtable (subclass or duplicate): {dup}\n\n")
    out.write("vtable    slots type  HandleEvent own known\n")
    for s, n, t, he, own, known in rows:
        out.write(f"{s:08x} {n:5d} {('%#5x' % t) if t is not None else '    -'}  "
                  f"{('%08x' % he) if he else '-':8s} {own:3d} {' '.join(known)}\n")
print(open(os.path.join(HERE, "vtable_map.txt")).read()[:6000])
p.close()
