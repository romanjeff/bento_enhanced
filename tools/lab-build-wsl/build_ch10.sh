#!/usr/bin/env bash
# Independent EU10 rebuild + full test suite; compares to Lab's pinned release hash.
set -uo pipefail
W=~/bento; OUT="$W/out/ch10-$(date +%Y%m%d-%H%M%S)"
cd "$W/lab"
"$W/venv/bin/python" tools/build_ch10.py --stock "$W/stock-1.6.1/bento1.bin" --out "$OUT" --test
rc=$?
want=$(python3 -c "import json;print(json.load(open('tools/release_targets.json'))['ch10']['sha'])")
got=$(sha256sum "$OUT/bento1.bin" 2>/dev/null | cut -d' ' -f1)
echo "builder exit=$rc"; echo "expected $want"; echo "got      $got"
[ "$want" = "$got" ] && echo HASH_MATCH || echo HASH_MISMATCH
grep -h -E "^(Ran|OK|FAILED)" "$OUT"/*_RESULTS.txt 2>/dev/null | sort | uniq -c
echo "out=$OUT"
