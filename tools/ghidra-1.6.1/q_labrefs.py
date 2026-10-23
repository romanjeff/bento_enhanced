"""For each lab_ function: count direct call refs vs data (pointer) refs in the 1.6.1 project."""
import os
os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra
pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject
HERE = os.path.dirname(os.path.abspath(__file__))
p = GhidraProject.openProject(HERE, "bento161", False); prog = p.openProgram("/", "bento161", True)
rm = prog.getReferenceManager()
for f in prog.getFunctionManager().getFunctions(True):
    if not f.getName().startswith("lab_"): continue
    calls = data = 0
    for r in rm.getReferencesTo(f.getEntryPoint()):
        if r.getReferenceType().isCall() or r.getReferenceType().isJump(): calls += 1
        else: data += 1
    flag = "  <-- no direct calls" if calls == 0 else ""
    print(f"{f.getEntryPoint()} {f.getName():32s} calls={calls:3d} data={data}{flag}")
p.close()
