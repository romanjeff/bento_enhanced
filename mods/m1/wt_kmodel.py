# SPDX-License-Identifier: GPL-3.0-or-later
"""Stock 1.6.8's Wavetable kernel (0x080A2000) as a bit-exact Python model: the stock oracle for BE4.44's handback
(docs/plan-wt-fm.md 7.2). Written for the WT FM plan (scratch wtfm/map/kmodel.py, M: bit-exact on 1,728 emulator blocks of
stock's kernel: samples and +0xC0..+0xD8, rates 0.3 .. 178.6, frames fixed / moving / every block, n 37 / 128 / 256),
kept here unchanged in substance as a test fixture; test_analog WtFmW2 re-checks it against stock's kernel in the emulator.

kernel(st, tab, req, rate, n) runs one call: st (St) is the kernel state at kernel + 0xC0, mutated; tab the table (float32
array, levels at MIPS's offsets); req the frame asked for (Position); rate stock's rate; n the samples. Returns the samples
(a list of floats, float32 values) or None where stock writes nothing (rate not > 0). The mip ladder runs on the pending
flag (a note start); a new frame crossfades over 16 samples straddling the next wrap, in the guard.
"""
import math, struct
import numpy as np

MIPS = ((1, 0), (2, 0x80000), (4, 0xC0000), (8, 0xE0000), (16, 0xF0000), (32, 0xF8000), (64, 0xFC000))
f32 = np.float32


def F(x): return float(f32(x))


def fma(a, b, c):
    """a b + c rounded once to float32 (a b exact in double; one double rounding, then float32: a tie risk only)."""
    return F(float(a) * float(b) + float(c))


def q28(x):
    """vcvt.s32.f32 #28: toward zero, saturating."""
    v = math.trunc(float(x) * 268435456.0)
    return max(-2 ** 31, min(2 ** 31 - 1, v))


class St:
    """The kernel's state, kernel + 0xC0 .. 0xD8: frac (f32), idx, frame (-1 none yet), off (floats), D, len, the pending flag."""
    FMT = "<fiiIIIB"

    def __init__(s, b):
        s.frac, s.idx, s.frame, s.off, s.D, s.len, s.flag = struct.unpack(St.FMT, bytes(b[:25]))

    def pack(s): return struct.pack(St.FMT, s.frac, s.idx, s.frame, s.off, s.D, s.len, s.flag)


def resamp(x, start, frac, step, cnt, out):
    """0x08050D00 on buffer x from index start: Q4.28 accumulator, linear interpolation with VFMA. Returns (idx, frac)."""
    st, acc, p = q28(step), q28(frac), start
    for _ in range(cnt):
        p += acc >> 28; f = acc & 0x0FFFFFFF
        fr = F(F(f) * F(2.0 ** -28))
        x0, x1 = x[p], x[p + 1]
        out.append(fma(F(x1 - x0), fr, x0))
        acc = f + st
    p += acc >> 28; acc &= 0x0FFFFFFF
    return p, F(F(acc) * F(2.0 ** -28))


def kernel(s, tab, req, rate, n):
    if s.flag:
        s.D, s.len, s.off = 1, 2048, 0
        x = F(rate)
        if x >= 1.25:
            for D, off in MIPS[1:]:
                x = F(x * 0.5)
                s.D, s.len, s.off = D, 2048 // D, off
                if x < 1.25: break
        s.idx = int(math.fmod(s.idx, s.len)); s.flag = 0
    step = F(F(rate) / F(s.D))
    if not step > 0: return None
    cur = req if s.frame == -1 else s.frame
    out, left, idx = [], n, s.idx
    def cnt(d): return min(math.trunc(F(F(F(d) / step) + 1.0)), left)
    while left > 0:
        L = s.len; r3 = L - idx
        if r3 > 9 and idx > 9:                       # body: straight from the frame
            c = cnt(r3 - 10)
            if c <= 0: break
            base = s.off + cur * L
            p, s.frac = resamp(tab[base:base + L], idx, s.frac, step, c, out)
            idx = p; left -= c; continue
        if r3 <= 9: c, g0 = cnt(r3 + 10), 12 - r3   # the wrap's guard: [last 12 | first 12]
        else: c, g0 = cnt(10 - idx), idx + 12
        if c <= 0: break
        b0 = s.off + cur * L
        g = list(map(float, tab[b0 + L - 12:b0 + L])) + list(map(float, tab[b0:b0 + 12]))
        if req != cur:                                # a new frame: 16-step crossfade straddling the wrap (0x080A2200)
            b1 = s.off + req * L
            nw = list(map(float, tab[b1 + L - 12:b1 + L])) + list(map(float, tab[b1:b1 + 12]))
            for i in range(4, 20):
                w = (i - 4) / 16.0
                g[i] = fma(g[i], 1.0 - w, F(nw[i] * w))
            g[20:24] = nw[20:24]
        p, s.frac = resamp(g, g0, s.frac, step, c, out)
        idx = p - 12 if p > 11 else p + L - 12
        if req != cur and idx > 9: cur = req
        left -= c
    s.idx = idx; s.frame = cur
    return out
