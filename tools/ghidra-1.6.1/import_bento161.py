"""Import Bento 1.6.1 bento1.bin into a fresh Ghidra project and auto-analyse it.

bento1.bin holds three images (verified from their vector tables):
  0x08040000..0x080E0000  M7 application (audio, sequencer playback, pad input)
  0x080E0000..0x08100000  disk-mode image
  0x08100000..end         M4 application (UI)
They share one address space, so they stay in one Ghidra program (Lab's
scripts expect that), but each gets its own named block and its own vector
table is used to seed entry points.
"""
import hashlib
import os

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

GHIDRA = r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC"
HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.join(os.path.dirname(HERE), "bento1.bin")
SHA = "14475e9d5068f5659d0fe0b00fc1278c1479c43ed9fda3d40654d5f3a7a6bbb3"
NAME = "bento161"
BASE = 0x08040000

IMAGES = [  # name, start, end (exclusive; None = end of file)
    ("M7_APP", 0x08040000, 0x080E0000),
    ("DISKMODE", 0x080E0000, 0x08100000),
    ("M4_UI", 0x08100000, None),
]
VECTORS = 166  # 16 core exceptions + 150 STM32H747 IRQs

RAM = [  # name, start, size, executable
    ("ITCM", 0x00000000, 0x10000, True),
    ("SRAM123_M4ALIAS", 0x10000000, 0x48000, True),  # D2 SRAM as the M4 addresses it
    ("DTCM", 0x20000000, 0x20000, True),
    ("AXI_SRAM", 0x24000000, 0x80000, True),
    ("SRAM123", 0x30000000, 0x48000, True),
    ("SRAM4", 0x38000000, 0x10000, False),
    ("PERIPH", 0x40000000, 0x20000000, False),
    ("SDRAM", 0xC0000000, 0x8000000, False),        # M4 bump pool lives at C6000000..C8000000
    ("CORE", 0xE0000000, 0x100000, False),
]

data = open(FW, "rb").read()
if hashlib.sha256(data).hexdigest() != SHA:
    raise SystemExit("bento1.bin is not stock 1.6.1; refusing to import")
if os.path.exists(os.path.join(HERE, NAME + ".gpr")):
    raise SystemExit(NAME + ".gpr already exists; delete it deliberately to re-import")

pyghidra.start(install_dir=GHIDRA)

from java.io import File
from ghidra.base.project import GhidraProject
from ghidra.program.flatapi import FlatProgramAPI
from ghidra.program.model.data import PointerDataType
from ghidra.program.model.lang import CompilerSpecID, LanguageID
from ghidra.program.model.symbol import SourceType
from ghidra.program.util import DefaultLanguageService
from ghidra.util.task import TaskMonitor

project = GhidraProject.createProject(HERE, NAME, False)
lang = DefaultLanguageService.getLanguageService().getLanguage(LanguageID("ARM:LE:32:Cortex"))
program = project.importProgram(File(FW), lang, lang.getCompilerSpecByID(CompilerSpecID("default")))
space = program.getAddressFactory().getDefaultAddressSpace()
A = space.getAddress
mem = program.getMemory()
api = FlatProgramAPI(program)
end = BASE + len(data)

tx = program.startTransaction("bento161 setup")
blk = mem.getBlocks()[0]
if blk.getStart().getOffset() != BASE:
    mem.moveBlock(blk, A(BASE), TaskMonitor.DUMMY)
    blk = mem.getBlock(A(BASE))
# Split the flat file into the three images (split leaves the lower part in place).
mem.split(mem.getBlock(A(BASE)), A(0x08100000))
mem.split(mem.getBlock(A(BASE)), A(0x080E0000))
for name, start, _ in IMAGES:
    b = mem.getBlock(A(start))
    b.setName(name); b.setRead(True); b.setWrite(False); b.setExecute(True)

for name, start, size, x in RAM:
    b = mem.createUninitializedBlock(name, A(start), size, False)
    b.setRead(True); b.setWrite(True); b.setExecute(x)
    if name in ("PERIPH", "CORE"):
        b.setVolatile(True)

seeded = 0
for name, start, stop in IMAGES:
    stop = stop or end
    for i in range(VECTORS):
        a = A(start + 4 * i)
        api.createData(a, PointerDataType())
        v = mem.getInt(a) & 0xFFFFFFFF
        # Only seed Thumb pointers that land inside this same image.
        if i > 0 and v & 1 and start <= (v & ~1) < stop:
            t = A(v & ~1)
            if api.getFunctionAt(t) is None:
                label = f"{name}_Reset_Handler" if i == 1 else f"{name}_vec_{i}"
                api.createFunction(t, label)
                seeded += 1
    api.createLabel(A(start), f"{name}_vector_table", True, SourceType.USER_DEFINED)
program.endTransaction(tx, True)
print("vector entry points seeded:", seeded)

project.analyze(program)
project.saveAs(program, "/", NAME, True)
fm = program.getFunctionManager()
counts = {n: 0 for n, _, _ in IMAGES}
for f in fm.getFunctions(True):
    o = f.getEntryPoint().getOffset()
    for n, s, e in IMAGES:
        if s <= o < (e or end):
            counts[n] += 1
print("functions:", fm.getFunctionCount(), counts)
project.close()
