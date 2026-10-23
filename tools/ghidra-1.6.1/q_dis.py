import os,sys
os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra
pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject
HERE=os.path.dirname(os.path.abspath(__file__))
p=GhidraProject.openProject(HERE,"bento161",False); prog=p.openProgram("/","bento161",True)
A=prog.getAddressFactory().getDefaultAddressSpace().getAddress; L=prog.getListing(); fm=prog.getFunctionManager()
for arg in sys.argv[1:]:
    a,n=arg.split(':'); a=int(a,16); n=int(n)
    print(f"--- from {a:08x}")
    i=L.getInstructionAt(A(a)) or L.getInstructionAfter(A(a))
    for _ in range(n):
        if i is None: break
        f=fm.getFunctionContaining(i.getAddress())
        print(f"  {i.getAddress()} {str(i):40s} [{f.getName() if f else '-'}]")
        i=i.getNext()
p.close()
