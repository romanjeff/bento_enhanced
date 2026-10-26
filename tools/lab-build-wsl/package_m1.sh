set -euo pipefail
O=$(ls -d ~/bento/out/m1-* | tail -1)
K=/mnt/c/Users/rjrom/bento_mods/flash/m1-polyat-v2.1; rm -rf "$K"; mkdir -p "$K"/{01_TEST_FIRMWARE,03_RESTORE_CH10}
cp "$O/bento1.bin" "$K/01_TEST_FIRMWARE/"; cp ~/bento/stock-1.6.1/bento2.bin "$K/01_TEST_FIRMWARE/"
cp /mnt/c/Users/rjrom/bento_mods/flash/ch10-1.6HA/01_TEST_FIRMWARE/bento1.bin /mnt/c/Users/rjrom/bento_mods/flash/ch10-1.6HA/01_TEST_FIRMWARE/bento2.bin "$K/03_RESTORE_CH10/"
cp "$O/manifest.json" "$K/"
(cd "$K" && find . -type f ! -name SHA256SUMS.txt | sort | xargs sha256sum > SHA256SUMS.txt && cat SHA256SUMS.txt)
