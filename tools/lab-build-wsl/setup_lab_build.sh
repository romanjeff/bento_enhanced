#!/usr/bin/env bash
# One-time WSL (Ubuntu 24.04) setup for building bento-community-lab, then an
# independent EU10 rebuild compared against Lab's published hash.
set -euo pipefail
W=~/bento; mkdir -p "$W"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential python3.12-venv cmake >/dev/null
[ -d "$W/lab" ] || git clone -q https://github.com/t4kbsv99sn-source/bento-community-lab.git "$W/lab"
git -C "$W/lab" pull -q --ff-only
mkdir -p "$W/stock-1.6.1"
cp /mnt/c/Users/rjrom/bento_mods/bento-1.6.1/bento1.bin /mnt/c/Users/rjrom/bento_mods/bento-1.6.1/bento2.bin "$W/stock-1.6.1/"
(cd "$W/stock-1.6.1" && sha256sum bento1.bin bento2.bin)
[ -d "$W/venv" ] || python3.12 -m venv "$W/venv"
"$W/venv/bin/pip" install -q -r "$W/lab/requirements.txt"
"$W/venv/bin/python" -c "import ziglang,unicorn,capstone,keystone,numpy;print('deps ok, unicorn',unicorn.__version__)"
echo SETUP_DONE
