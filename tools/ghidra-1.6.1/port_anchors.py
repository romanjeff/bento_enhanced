"""Find our 1.5.32 anchor functions in 1.6.1 by instruction-shape fingerprint.

Why not byte matching: between builds almost every branch offset, literal-pool
address and PC-relative load changes, so raw bytes rarely survive. The
*sequence of mnemonics* usually does. Each function's fingerprint is its
ordered mnemonic list (operands dropped). A match is reported only when a
fingerprint is unique in BOTH programs -- ambiguous shapes (tiny getters,
thunks) are reported as such rather than guessed.

Second pass for anchors that miss: neighbourhood matching. If a function's
callers or callees matched uniquely, the anchor is likely the function at the
corresponding call position -- reported as a CANDIDATE, never as confirmed.

Usage:  python -I port_anchors.py            -> anchors_1532_to_161.txt
"""
import hashlib
import os
from collections import defaultdict

os.environ.setdefault("JAVA_HOME", r"C:\Program Files\Eclipse Adoptium\jdk-25.0.4.101-hotspot")
import pyghidra

pyghidra.start(install_dir=r"C:\Users\rjrom\tools\Ghidra\ghidra_12.1.4_PUBLIC")
from ghidra.base.project import GhidraProject
from ghidra.util.task import TaskMonitor

HERE = os.path.dirname(os.path.abspath(__file__))
OLD = (os.path.join(HERE, "..", "..", "bento-1.5.32", "ghidra"), "bento1b")
NEW = (HERE, "bento161")
MIN_INSNS = 12  # shorter functions are too generic to fingerprint usefully

# Our 1.5.32 anchors (from bento_enhanced docs/ui-architecture.md, mod-matrix.md, PROJECT_NOTES).
ANCHORS = {
    0x0816ADC8: "AddChild",
    0x0816ADE6: "RemoveChild",
    0x0816AE98: "PostEvent_bubble",
    0x0812BA18: "model_resolve_objid",
    0x0812B9E6: "prop_set_generic",
    0x0812B88C: "prop_get_generic",
    0x0812B4CE: "model_find_by_type",
    0x08133A34: "bpm_get",
    0x0813E1AC: "command_dispatch",
    0x08138EF8: "dispatch_0xd0",
    0x08138BCC: "dispatch_0xcb",
    0x0813CC24: "main_tick_loop",
    0x08132F14: "param_accessor",
    0x08160284: "param_table_build_m4",
    0x08065700: "param_table_build_m7",
    0x08162FFC: "mod_source_table_init",
    0x08159130: "seq_construct",
    0x0815A720: "seq_handle_event",
    0x08159C14: "seq_cb_allowlist",
    0x08157518: "seq_refresh",
    0x081546C8: "scene_refresh",
    0x081544CC: "scene_handle_event",
    0x08174874: "metgain_refresh",
    0x08174A5C: "metgain_handle_event",
    0x0806F920: "midi_status_parse",
    0x0809AC60: "msgbus_dispatch",
    0x08062E00: "msg_send",
}


def load(path, name):
    proj = GhidraProject.openProject(os.path.abspath(path), name, False)
    prog = proj.openProgram("/", name, True)  # read-only: never saved
    return proj, prog


def fingerprints(prog):
    listing = prog.getListing()
    fp, by_fp, size = {}, defaultdict(list), {}
    for f in prog.getFunctionManager().getFunctions(True):
        mn = [i.getMnemonicString() for i in listing.getInstructions(f.getBody(), True)]
        e = f.getEntryPoint().getOffset()
        size[e] = len(mn)
        if len(mn) < MIN_INSNS:
            continue
        h = hashlib.sha1(" ".join(mn).encode()).hexdigest()
        fp[e] = h
        by_fp[h].append(e)
    return fp, by_fp, size


def calls(prog):
    out = {}
    for f in prog.getFunctionManager().getFunctions(True):
        out[f.getEntryPoint().getOffset()] = (
            sorted(c.getEntryPoint().getOffset() for c in f.getCallingFunctions(TaskMonitor.DUMMY)),
            sorted(c.getEntryPoint().getOffset() for c in f.getCalledFunctions(TaskMonitor.DUMMY)),
        )
    return out


po, old = load(*OLD)
pn, new = load(*NEW)
ofp, oby, osz = fingerprints(old)
nfp, nby, nsz = fingerprints(new)
unique = {e: nby[h][0] for e, h in ofp.items() if len(oby[h]) == 1 and len(nby.get(h, [])) == 1}
ocalls, ncalls = calls(old), calls(new)
ofm = old.getFunctionManager()

lines = [f"1.5.32 functions fingerprinted: {len(ofp)}; 1.6.1: {len(nfp)}; unique 1:1 matches: {len(unique)}", ""]
for a, name in ANCHORS.items():
    f = ofm.getFunctionContaining(old.getAddressFactory().getDefaultAddressSpace().getAddress(a))
    if f is None:
        lines.append(f"{name:24s} {a:08x} -> (no function in 1.5.32 project)"); continue
    e = f.getEntryPoint().getOffset()
    note = "" if e == a else f" [anchor is inside fn {e:08x}]"
    if e in unique:
        lines.append(f"{name:24s} {a:08x} -> {unique[e]:08x}  MATCH ({osz[e]} insns){note}")
        continue
    h = ofp.get(e)
    if h is None:
        why = "too short to fingerprint"
    elif len(nby.get(h, [])) == 0:
        why = "shape changed in 1.6.1"
    else:
        why = f"shape ambiguous ({len(oby[h])} old / {len(nby[h])} new)"
    # Neighbourhood: new-side functions called by matched callers / calling matched callees.
    votes = defaultdict(int)
    callers, callees = ocalls.get(e, ([], []))
    for c in callers:
        if c in unique:
            for cand in ncalls.get(unique[c], ([], []))[1]:
                votes[cand] += 1
    for c in callees:
        if c in unique:
            for cand in ncalls.get(unique[c], ([], []))[0]:
                votes[cand] += 1
    best = sorted(votes.items(), key=lambda kv: -kv[1])[:3]
    cand = ", ".join(f"{c:08x}(x{v}, {nsz.get(c, 0)} insns)" for c, v in best) or "none"
    lines.append(f"{name:24s} {a:08x} -> ?  {why}; old size {osz[e]} insns; candidates: {cand}{note}")

report = os.path.join(HERE, "anchors_1532_to_161.txt")
open(report, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
po.close(); pn.close()
