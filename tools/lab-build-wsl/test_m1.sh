B=$(ls -d ~/bento/out/ch10-* | tail -1); O=$(ls -d ~/bento/out/m1-* | tail -1)
cd /mnt/c/Users/rjrom/bento_mods/bento_enhanced/mods/m1
for t in test_diag.py test_polyat.py; do echo "== $t"; ~/bento/venv/bin/python $t "$B/bento1.bin" "$O" 2>&1 | tail -15; done
