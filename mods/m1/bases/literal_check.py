# SPDX-License-Identifier: GPL-3.0-or-later
"""No bare stock addresses outside the base tables (S1 of the 1.6.8 port).

    python -m bases.literal_check [repo root]        (from mods/m1; build.py runs it too)

Scans the code of build.py, the C/asm sources, headers, linker scripts, the tests and the
display emulator, with comments and docstrings removed (they may cite 1.6.1 addresses as
documentation: Ghidra names like FUN_0815C27C are 1.6.1 names). A hexadecimal literal that
lands in stock flash or in RAM is an error unless:
  - it is one of GENUINE below (hardware memory map, our image base, chip constants), or
  - its line carries the marker `addr-ok` in a comment, used for test-harness scratch
    memory (stacks, event buffers) that no firmware version owns.
Peripheral addresses (0x4..., 0x5..., 0xE...) and the bootloader range (test sentinels)
are not addresses of the firmware being patched and are not checked.
"""
import ast, io, os, re, sys, tokenize, warnings

HEX = re.compile(r"(?<![0-9A-Za-z_])0[xX]([0-9A-Fa-f]{7,8})(?![0-9A-Fa-f])")

# Genuine constants: identical for every firmware version by construction.
GENUINE = {
    0x08040000: "main image load address (bootloader contract)",
    0x08200000: "end of internal flash",
    0x081E0000: "persistent-storage region; nothing may reach it (bootloader/MPU contract)",
    0x10000000: "D2 SRAM alias base (memory map)",
    0x20000000: "DTCM base (memory map)", 0x20020000: "DTCM end (memory map)",
    0x24000000: "AXI SRAM base (memory map)", 0x24080000: "AXI SRAM end (memory map)",
    0x30000000: "D2 SRAM base (memory map)", 0x30048000: "D2 SRAM end (memory map)",
    0x38000000: "D3 SRAM base (memory map)", 0x38010000: "D3 SRAM end (memory map)",
    0x38800000: "backup SRAM base (memory map)",
    0xC0000000: "SDRAM base (memory map)", 0xC8000000: "SDRAM end (memory map)",
    0x0FFF0000: "emulator return pad (unmapped on the chip)",
}


def classify(v):
    """'flash' / 'ram' / 'sdram' for a value that could be a firmware address, else None."""
    if 0x08040000 <= v < 0x081E0000:      # main image (0x081E0000.. is never ours or stock code)
        return "flash"
    if (0x10000000 <= v < 0x10048000 or 0x20000000 <= v < 0x20020000 or 0x24000000 <= v < 0x24080000
            or 0x30000000 <= v < 0x30048000 or 0x38000000 <= v < 0x38010000):
        return "ram"
    if 0xC0000000 <= v <= 0xC8000000:
        return "sdram"
    return None


def FILES(repo):
    m1 = os.path.join(repo, "mods", "m1")
    for n in sorted(os.listdir(m1)):
        if n.endswith((".c", ".s", ".h", ".ld")) or n == "build.py" or (n.startswith("test_") and n.endswith(".py")) \
                or n == "lab_receiver_fixture.py":
            yield os.path.join("mods", "m1", n)
    emu = os.path.join(repo, "tools", "display_emulator")
    for n in sorted(os.listdir(emu)):
        if n.endswith(".py"):
            yield os.path.join("tools", "display_emulator", n)


def _c_code(text):
    """C/asm text with comments blanked (line structure kept)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _py_code(text):
    """Python text with comments and docstrings blanked (line structure kept)."""
    lines = text.splitlines(True)
    spans = []      # (row, col, end row, end col) to blank

    def col(row, byte_col):   # ast columns are UTF-8 byte offsets
        return len(lines[row - 1].encode("utf-8")[:byte_col].decode("utf-8", "ignore"))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(text)
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                d = body[0].value
                spans.append((d.lineno, col(d.lineno, d.col_offset), d.end_lineno, col(d.end_lineno, d.end_col_offset)))
    except SyntaxError:
        pass
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((tok.start[0], tok.start[1], tok.end[0], tok.end[1]))
    chars = [list(l) for l in lines]
    for r0, c0, r1, c1 in spans:
        for r in range(r0, r1 + 1):
            row = chars[r - 1]
            a = c0 if r == r0 else 0
            b = c1 if r == r1 else len(row)
            for k in range(a, min(b, len(row))):
                if row[k] not in "\r\n":
                    row[k] = " "
    return "".join("".join(r) for r in chars)


def code_text(path):
    """(line number, code-only text) for each line of a source file."""
    text = open(path, encoding="utf-8", errors="replace", newline="").read()
    code = _py_code(text) if path.endswith(".py") else _c_code(text)
    raw = text.splitlines()
    for ln, line in enumerate(code.splitlines(), 1):
        if "addr-ok" in raw[ln - 1]:
            continue
        yield ln, line


def check(repo):
    bad = []
    for rel in FILES(repo):
        for ln, line in code_text(os.path.join(repo, rel)):
            for m in HEX.finditer(line):
                v = int(m.group(1), 16)
                if classify(v) and v not in GENUINE:
                    bad.append(f"{rel.replace(os.sep, '/')}:{ln}: {m.group(0)}: {line.strip()[:120]}")
    return bad


def main(argv):
    repo = argv[1] if len(argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    bad = check(repo)
    for b in bad:
        print(b)
    files = list(FILES(repo))
    print(f"literal check: {len(files)} files, {len(bad)} bare stock/RAM address literal(s) outside the base tables"
          f" ({len(GENUINE)} genuine constants allowed)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
