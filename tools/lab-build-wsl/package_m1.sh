set -euo pipefail
O=$(ls -d ~/bento/out/m1-* | tail -1)
K=/mnt/c/Users/rjrom/bento_mods/flash/m1-stock-v2.7; rm -rf "$K"; mkdir -p "$K"/{01_TEST_FIRMWARE,03_RESTORE_STOCK}
cp "$O/bento1.bin" "$K/01_TEST_FIRMWARE/"; cp ~/bento/stock-1.6.1/bento2.bin "$K/01_TEST_FIRMWARE/"
cp ~/bento/stock-1.6.1/bento1.bin ~/bento/stock-1.6.1/bento2.bin "$K/03_RESTORE_STOCK/"
cp "$O/manifest.json" "$K/"
(cd "$K" && find . -type f ! -name SHA256SUMS.txt | sort | xargs sha256sum > SHA256SUMS.txt && cat SHA256SUMS.txt)
