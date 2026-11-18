# SPDX-License-Identifier: GPL-3.0-or-later
"""BE4.44 D4 (docs/plan-wt-fm.md 3.5, 12 D4): a wavetable's per-frame DC, the offset FM 1>2 from a Wavetable OSC1 puts into
OSC2's pitch.

    python tools/wt_dc.py [--frame 2048] table.wav [more.wav ...]
    python tools/wt_dc.py --synthetic          (the families BE4.44's D4 row was measured on)

B is OSC1's block before Level times its increment (BE4.42's an_feed form, unchanged by W4), so a frame whose mean is m adds
J inc1 m to OSC2's increment on average: at OSC1 = OSC2's pitch OSC2 plays at (1 + J m) of its own, J = 16 (v / 1000)^1.5
(FM 1>2 250: 2, 500: 5.66, 1000: 16). The mean is taken on level 0 (stock's mip builder scales every level's DC by its
decimator's gain, 0.951 a level, so the coarser levels carry a little less). Prints, per file: the frames, the largest
|mean| and the frame it is in, the mean of |mean| over the frames, and the pitch shift that largest DC gives at FM 1>2 250,
500 and 1000 (cents; "through 0" where 1 + J m <= 0: OSC2's mean increment reverses).

Reads 16-bit, 24-bit and 32-bit PCM and 32-bit float WAVs (mono, or the first channel). Read-only: it never writes. Run it on
a copy of the card's Wavetables folder (or anywhere): it does not need the Bento.
"""
import math, struct, sys

INDEX = {250: 2.0, 500: 16 * 0.5 ** 1.5, 1000: 16.0}


def read_wav(path):
    b = open(path, "rb").read()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a RIFF WAVE file")
    i, fmt, data = 12, None, None
    while i + 8 <= len(b):
        cid, n = b[i:i + 4], struct.unpack_from("<I", b, i + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", b, i + 8)
        elif cid == b"data":
            data = b[i + 8:i + 8 + n]
        i += 8 + n + (n & 1)
    if not fmt or data is None:
        raise ValueError(f"{path}: no fmt / data chunk")
    tag, ch, _, _, _, bits = fmt
    w = bits // 8
    out = []
    for j in range(0, len(data) - w * ch + 1, w * ch):
        s = data[j:j + w]
        if tag == 3 and bits == 32:
            out.append(struct.unpack("<f", s)[0])
        elif bits == 16:
            out.append(struct.unpack("<h", s)[0] / 32768.0)
        elif bits == 24:
            out.append(int.from_bytes(s, "little", signed=True) / 8388608.0)
        elif bits == 32:
            out.append(struct.unpack("<i", s)[0] / 2147483648.0)
        else:
            raise ValueError(f"{path}: {bits}-bit format {tag} not read")
    return out


def cents(m, J):
    r = 1 + J * m
    if r <= 0:
        return "through 0"
    c = 1200 * math.log2(r)
    return f"{c:+.0f}" if abs(c) >= 0.5 else "0"


def report(name, x, frame):
    frames = [x[k:k + frame] for k in range(0, len(x) - frame + 1, frame)] or [x]
    means = [sum(f) / len(f) for f in frames]
    k = max(range(len(means)), key=lambda i: abs(means[i]))
    print(f"{name}: {len(frames)} frames of {len(frames[0])}; largest |DC| {abs(means[k]):.4f} (frame {k}), "
          f"mean |DC| {sum(abs(m) for m in means) / len(means):.4f}; OSC2's shift at that DC, FM 1>2 250 / 500 / 1000: "
          + " / ".join(cents(means[k], J) for J in INDEX.values()) + " cents")
    return max(abs(m) for m in means)


def synthetic(frame=2048):
    """Families a factory set is likely to hold (D4's row): band-limited saw and square, a basic-shapes morph, a PWM sweep,
    a sync sweep, half-wave sines (a vocal-like asymmetric family), and an additive random-phase family."""
    import random
    two = 2 * math.pi
    def bl_pulse(w, h=64):                                   # band-limited pulse of width w (high 1, low -1): DC 2w - 1
        return [2 * w - 1 + sum(4 / (math.pi * k) * math.sin(math.pi * k * w) * math.cos(two * k * (i / frame - w / 2))
                                for k in range(1, h)) for i in range(frame)]
    fams = {}
    fams["saw (band-limited)"] = [sum(math.sin(two * k * i / frame) / k for k in range(1, 64)) * 2 / math.pi * 0.5
                                  for i in range(frame)]
    fams["square 50 %"] = bl_pulse(0.5)
    fams["PWM sweep, 50 % -> 5 %"] = sum((bl_pulse(0.5 - 0.45 * f / 7) for f in range(8)), [])
    fams["half-wave sines"] = sum(([max(0.0, math.sin(two * h * i / frame)) for i in range(frame)] for h in (1, 2, 3)), [])
    fams["hard-sync sweep"] = sum(([((r * i / frame) % 1.0) * 2 - 1 for i in range(frame)] for r in (1.0, 1.5, 2.3, 3.7)), [])
    rnd = random.Random(44)
    def additive():
        hs = [(k, rnd.uniform(0, 1) / k, rnd.uniform(0, two)) for k in range(1, 16)]
        return [sum(a * math.sin(two * k * i / frame + p) for k, a, p in hs) for i in range(frame)]
    fams["additive, random phases"] = sum((additive() for _ in range(4)), [])
    for name, x in fams.items():
        report(name, x, frame)


if __name__ == "__main__":
    args = sys.argv[1:]
    fr = 2048
    if "--frame" in args:
        j = args.index("--frame"); fr = int(args[j + 1]); del args[j:j + 2]
    if args == ["--synthetic"]:
        synthetic(fr)
    elif not args:
        print(__doc__)
    else:
        for p in args:
            report(p, read_wav(p), fr)
