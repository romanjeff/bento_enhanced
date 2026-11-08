set -euo pipefail
O=$(ls -d ~/bento/out/m1-* | tail -1)
V=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$O/manifest.json")
# the build's base (manifest base_label, e.g. stock-1.6.8; kits before S1 have none: 1.6.1), and its
# stock image must be the one the build was made from: never pair a bento1.bin with another base's files
L=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('base_label', 'stock-1.6.1'))" "$O/manifest.json")
B=~/bento/$L; SHA=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['base_sha256'])" "$O/manifest.json")
[ -f "$B/bento1.bin" ] && [ -f "$B/bento2.bin" ] || { echo "no stock images for $L in $B" >&2; exit 1; }
[ "$(sha256sum "$B/bento1.bin" | cut -d' ' -f1)" = "$SHA" ] || { echo "$B/bento1.bin is not the build's base" >&2; exit 1; }
K=/mnt/c/Users/rjrom/bento_mods/flash/bento_enhanced-v$V; rm -rf "$K"; mkdir -p "$K"/{01_TEST_FIRMWARE,03_RESTORE_STOCK}
cp "$O/bento1.bin" "$K/01_TEST_FIRMWARE/"; cp "$B/bento2.bin" "$K/01_TEST_FIRMWARE/"
cp "$B/bento1.bin" "$B/bento2.bin" "$K/03_RESTORE_STOCK/"
echo "base: $L"
cp "$O/manifest.json" "$K/"
echo "kit: $K"
(cd "$K" && find . -type f ! -name SHA256SUMS.txt | sort | xargs sha256sum > SHA256SUMS.txt && cat SHA256SUMS.txt)
