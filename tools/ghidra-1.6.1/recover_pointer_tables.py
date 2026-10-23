"""Recover functions reachable only through pointer tables (vtables, handler tables).

Ghidra's raw-binary analysis follows direct calls from known entry points. In
this stripped C++ image, virtual methods and message handlers are reached only
through tables of Thumb pointers, so Ghidra never visits them -- or anything
they call (the gap cascades).

Heuristic: a run of >= MIN_RUN consecutive aligned flash words, each odd (Thumb
bit) and pointing into the SAME image's code range, is treated as a pointer
table. Each target not already inside a function becomes a function, the table
words become pointers, then analysis is re-run so call-following fans out.
Single isolated odd words are ignored (too many false positives from data).

Report: recovered_tables.txt (table start, length, targets). Our own project
only; re-import with import_bento161.py to undo.
"""
import os

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject
from ghidra.program.flatapi import FlatProgramAPI
from ghidra.program.model.data import PointerDataType

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "bento161"
MIN_RUN = 3
IMAGES = [("M7_APP", 0x08040000, 0x080E0000), ("DISKMODE", 0x080E0000, 0x08100000),
          ("M4_UI", 0x08100000, None)]

p = GhidraProject.openProject(HERE, NAME, False)
prog = p.openProgram("/", NAME, False)
api = FlatProgramAPI(prog)
mem, fm, listing = prog.getMemory(), prog.getFunctionManager(), prog.getListing()
A = prog.getAddressFactory().getDefaultAddressSpace().getAddress
end = max(b.getEnd().getOffset() for b in mem.getBlocks() if b.getName() in [i[0] for i in IMAGES]) + 1


def image_of(x):
    for n, s, e in IMAGES:
        if s <= x < (e or end):
            return n, s, e or end
    return None


def is_switch_table(start):
    """GCC emits `adr rX,table; ldr.w pc,[rX,rY,lsl #2]` immediately before a switch
    jump table of absolute Thumb addresses. Those targets are case labels inside one
    function, not functions -- seeding them fragments the real function.
    LDR.W PC,[Rn,Rm,LSL #2] = halfwords 0xF85n, 0xF02m (allow 0/2 bytes of padding)."""
    for back in (4, 6):
        try:
            h1 = mem.getShort(A(start - back)) & 0xFFFF
            h2 = mem.getShort(A(start - back + 2)) & 0xFFFF
        except Exception:
            continue
        if (h1 & 0xFFF0) == 0xF850 and (h2 & 0xFFF0) == 0xF020:
            return True
    return False


skipped_switch = 0
before = fm.getFunctionCount()
tables, new_funcs = [], 0
tx = prog.startTransaction("recover pointer tables")
for n, s, e in IMAGES:
    e = e or end
    a = s
    while a + 4 <= e:
        run = []
        while a + 4 <= e:
            v = mem.getInt(A(a)) & 0xFFFFFFFF
            if v & 1 and s <= (v & ~1) < e:
                run.append((a, v & ~1)); a += 4
            else:
                break
        if len(run) >= MIN_RUN and is_switch_table(run[0][0]):
            skipped_switch += 1
        elif len(run) >= MIN_RUN and not listing.getInstructionContaining(A(run[0][0])):
            tables.append((n, run[0][0], len(run), sorted({t for _, t in run})))
            for wa, t in run:
                try:
                    api.createData(A(wa), PointerDataType())
                except Exception:
                    pass  # overlaps existing data; leave it
                if fm.getFunctionContaining(A(t)) is None and api.getFunctionAt(A(t)) is None:
                    api.disassemble(A(t))
                    if api.createFunction(A(t), None) is not None:
                        new_funcs += 1
        a += 4 if not run else 0
prog.endTransaction(tx, True)
print(f"tables={len(tables)} switch tables skipped={skipped_switch} new functions seeded={new_funcs}; re-analysing...")
p.analyze(prog)
p.save(prog)
after = fm.getFunctionCount()
counts = {n: 0 for n, _, _ in IMAGES}
for f in fm.getFunctions(True):
    im = image_of(f.getEntryPoint().getOffset())
    if im:
        counts[im[0]] += 1
with open(os.path.join(HERE, "recovered_tables.txt"), "w") as out:
    for n, start, length, targets in tables:
        out.write(f"{n} {start:08x} len={length} targets={' '.join('%08x' % t for t in targets[:12])}"
                  f"{' ...' if len(targets) > 12 else ''}\n")
print(f"functions before={before} after={after} {counts}")
p.close()
