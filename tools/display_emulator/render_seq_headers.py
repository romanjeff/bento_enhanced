"""Render the native SEQ header-bar templates of a Bento main image to PNG.

Runs the firmware's own widget, text and fill code under Unicorn; only the DMA2D
peripheral is emulated (dma2d.py). No device access, no flashing.

    python render_seq_headers.py --image bento1.bin --out shots/

Writes template_<page>.png per SEQ template and prints each header button's
label and visibility. Based on bento-playground's render_headers.py (GPL-3.0-or-later).
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emu
import render

# (template index, page id) pairs used by the SEQ views (from the Playground harness); per base
def templates(S):
    return [(S.HEADER_TPL_SEQ_DRUM, S.PAGE_SEQ_DRUM), (S.HEADER_TPL_SEQ, S.PAGE_SEQ), (S.HEADER_TPL_SEQ_ALT, S.PAGE_SEQ_ALT)]


def buttons(e, root=None):
    root = root or e.S.HEADER_ROOT
    out = []
    for k in range(e.S.HEADER_ACTIONS):
        b = root + e.S.HEADER_BUTTONS + k * e.S.HEADER_BUTTON_STRIDE
        label = e.text(b + e.S.HEADER_BUTTON_LABEL) or "-"
        out.append((label, "hidden" if e.u.mem_read(b + e.S.HEADER_BUTTON_HIDDEN, 1)[0] else "shown"))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    e = emu.Emu(a.image)
    render.boot_gfx(e)
    e.call(e.S.HEADER_ROOT_INIT, e.S.HEADER_ROOT)
    for index, page in templates(e.S):
        render.page_reset(e)
        render.header(e, render.tpl(e, index))
        render.clear(e)
        render.paint(e, e.S.HEADER_ROOT)
        render.save(e, str(out / f"template_{page:02x}.png"), crop=(0, 424, 1280, 480))
        print(f"page {page:#04x}:", buttons(e))
    print("wrote", out)


if __name__ == "__main__":
    main()
