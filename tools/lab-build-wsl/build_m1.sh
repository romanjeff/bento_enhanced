set -euo pipefail
B=~/bento/stock-${BENTO_BASE:-1.6.1}     # stock image dir; build.py picks the table by its sha256
cd /mnt/c/Users/rjrom/bento_mods/bento_enhanced/mods/m1
OUT=~/bento/out/m1-$(date +%Y%m%d-%H%M%S)
~/bento/venv/bin/python build.py --base "$B/bento1.bin" --out "$OUT"
echo "out=$OUT"
