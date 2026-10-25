set -euo pipefail
B=$(ls -d ~/bento/out/ch10-* | tail -1)
cd /mnt/c/Users/rjrom/bento_mods/bento_enhanced/mods/m1
OUT=~/bento/out/m1-$(date +%Y%m%d-%H%M%S)
~/bento/venv/bin/python build.py --base "$B/bento1.bin" --out "$OUT"
echo "out=$OUT"
