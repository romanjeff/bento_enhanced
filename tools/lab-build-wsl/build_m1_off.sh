#!/usr/bin/env bash
# Bisect build: every feature group off, logging on. Run after build_m1.sh, test and package;
# it becomes the newest output, so package_m1.sh then makes its own kit folder.
set -euo pipefail
export BE_DISABLE="${1:-step,hold,cond,retrig,poly,inta,nmod}" BE_LABEL="${2:-BEoff}" BE_TAG="${3:-off}"
B=~/bento/stock-${BENTO_BASE:-1.6.1}     # stock image dir (as build_m1.sh); build.py picks the table by its sha256
cd /mnt/c/Users/rjrom/bento_mods/bento_enhanced/mods/m1
OUT=~/bento/out/m1-$(date +%Y%m%d-%H%M%S)
~/bento/venv/bin/python build.py --base "$B/bento1.bin" --out "$OUT"
echo "out=$OUT"
