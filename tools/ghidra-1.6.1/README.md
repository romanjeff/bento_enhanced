# Ghidra project for Bento 1.6.1

Scripts that build and query a Ghidra project of stock **1.6.1** `bento1.bin`.
Requires Ghidra 12.1.x, a JDK, and `pip install pyghidra`. The paths at the top
of each script (`GHIDRA`, `JAVA_HOME`) are set for the original author's machine,
so edit them for yours.

**Layout:** copy these into `<dir with your stock 1.6.1 bento1.bin>/ghidra/`.
`import_bento161.py` reads `../bento1.bin` and refuses anything that isn't stock
1.6.1 (it checks the SHA-256). `port_anchors.py` also expects a 1.5.32 project at
`../../bento-1.5.32/ghidra/bento1b`.

| Script | What it does |
|---|---|
| `rebuild.sh` | Full rebuild from scratch: import, then Lab labels, then pointer-table recovery |
| `import_bento161.py` | Imports at `0x08040000`, splits flash into `M7_APP` / `DISKMODE` / `M4_UI` and seeds all three vector tables (see [`docs/bento-os.md`](../../docs/bento-os.md)) |
| `label_lab_161.py` | Applies bento-community-lab's documented addresses as `lab_*` symbols |
| `recover_pointer_tables.py` | Seeds functions reached only through vtables and handler tables, then re-analyzes. Skips GCC switch jump tables (`ldr.w pc,[Rn,Rm,lsl #2]`) so case labels don't become fake functions |
| `port_anchors.py` | Matches our 1.5.32 anchor functions to 1.6.1 by mnemonic-sequence fingerprint, with call-graph candidates when the shape changed. Output: `anchors_1532_to_161.txt` |
| `run_lab_scripts.sh` | Runs Lab's four read-only report scripts headlessly with `-readOnly` |
| `gq.py`, `q_dis.py`, `q_labrefs.py` | Query helpers: xrefs/decompile, disassemble at an address, ref counts for `lab_*` functions |

Why pointer-table recovery matters: this is stripped C++. Virtual methods and
message handlers are reached only through pointer tables, which Ghidra's
raw-binary analysis doesn't follow. Everything those methods call is missed in
turn. Recovery raised the function count from 4,967 to 5,654 (M7 image +34%).
