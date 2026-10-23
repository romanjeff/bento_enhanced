#!/usr/bin/env bash
# Rebuild the 1.6.1 Ghidra project from scratch: import -> Lab labels -> pointer-table recovery.
set -euo pipefail
cd "$(dirname "$0")"
PY=/c/Users/rjrom/anaconda3/python.exe
rm -rf bento161.gpr bento161.rep bento161.lock bento161.lock~
for s in import_bento161.py label_lab_161.py recover_pointer_tables.py; do
  echo "== $s"; $PY -I $s 2>&1 | grep -v "^INFO" | tail -3
done
