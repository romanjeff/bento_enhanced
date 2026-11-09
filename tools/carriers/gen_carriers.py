#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Carrier WAVs for the drum synths (S6c; docs/plan-synths-168.md section 8).

    python3 gen_carriers.py OUT_DIR [--engines kick,snare,...]

A carrier is an ordinary one-shot sample that the M7 recognises (mods/m1/dsp/drums.cpp) and replaces by a drum
synth: mono, 48 kHz, 16-bit PCM,
  - 32 samples of the signature 0xB1E70A5C, bit i (lowest first) as +1 LSB (1) or -1 LSB (0);
  - 32 samples of the word 0x4245VVII ("BE", format version VV, engine id II) the same way;
  - then silence, up to 4800 + id frames (about 9.6 KB a file): the synth owns the voice's cursor, so the carrier
    need not be as long as the sound.
The constants are mods/m1/synth_shared.h's (CARRIER_*), typed here again; test_drums.py builds its carriers the same
way. Deterministic: the same files, byte for byte, on every run (package_m1.sh records their SHA-256 in the kit).
Our own code; nothing here comes from bento-playground (whose carriers are 144,018 + id frames, signature only).
The files are never committed: package_m1.sh writes them into the kit's "Drum Synths BE" folder.
"""
import argparse, struct, sys
from pathlib import Path

SIGNATURE = 0xB1E70A5C
FRAMES = 4800
VERSION = 1
RATE = 48000
ENGINES = {"kick": (1, "BE Kick.wav"), "snare": (3, "BE Snare.wav"), "hihat": (5, "BE HiHat.wav"),
           "clap": (7, "BE Clap.wav"), "tom": (9, "BE Tom.wav")}
BUILT = ("kick",)            # S6c builds the Kick only; a carrier of an engine not in the image plays as near silence


def word(eid):
    return 0x42450000 | (VERSION << 8) | eid


def samples(eid):
    bits = [(SIGNATURE >> i) & 1 for i in range(32)] + [(word(eid) >> i) & 1 for i in range(32)]
    return [1 if b else -1 for b in bits] + [0] * (FRAMES + eid - 64)


def wav(eid):
    data = struct.pack(f"<{FRAMES + eid}h", *samples(eid))
    fmt = struct.pack("<HHIIHH", 1, 1, RATE, RATE * 2, 2, 16)            # PCM, mono, 48 kHz, 16-bit
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--engines", default=",".join(BUILT), help="comma-separated: " + ", ".join(ENGINES))
    a = ap.parse_args()
    names = [n.strip() for n in a.engines.split(",") if n.strip()]
    bad = [n for n in names if n not in ENGINES]
    if bad:
        sys.exit(f"unknown engine(s): {', '.join(bad)}")
    a.out.mkdir(parents=True, exist_ok=True)
    for n in names:
        eid, fname = ENGINES[n]
        (a.out / fname).write_bytes(wav(eid))
        print(f"{a.out / fname}: engine {eid}, {FRAMES + eid} frames")


if __name__ == "__main__":
    main()
