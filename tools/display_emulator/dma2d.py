# Ported from hunting-ghosts/bento-playground @9fc7787, research/display_emulator/dma2d.py
# GPL-3.0-or-later. Changes for bento_enhanced are marked "BE:".
import struct
from unicorn import *
D=0x52001000
BPP={0:4,1:3,2:2,3:2,4:2,5:1,6:1,7:2,9:1}
class DMA2D:
    def __init__(s,u):
        s.u=u; s.reg=[0]*0x100; s.ops=[]
        u.mmio_map(D,0x1000,s.rd,None,s.wr,None)
    def rd(s,u,off,size,ud):
        if off==0x04: return 0x2  # TCIF always
        if off==0x00: return s.reg[0]&~1
        return s.reg[off>>2]
    def wr(s,u,off,size,val,ud):
        s.reg[off>>2]=val
        if off==0 and val&1: s.run()
    def run(s):
        R=lambda o:s.reg[o>>2]
        cr=R(0); mode=(cr>>16)&7
        ofmt=R(0x34)&7; obpp=BPP[ofmt]
        nlr=R(0x44); w=nlr>>16; h=nlr&0xffff
        omar=R(0x3c); oor=R(0x40)&0x3fff
        u=s.u
        s.ops.append((mode,w,h,hex(omar)))
        if mode==3:
            col=R(0x38)
            px=col.to_bytes(4,'little')[:obpp]
            row=px*w
            for y in range(h): u.mem_write(omar+y*(w+oor)*obpp,row)
        elif mode in (0,1,2):
            fg=R(0x0c); fgo=R(0x10)&0x3fff; ffmt=R(0x1c)&0xf
            fbpp=BPP.get(ffmt,1)
            if mode==0 or (ffmt==ofmt):
                bpp=fbpp
                for y in range(h):
                    u.mem_write(omar+y*(w+oor)*bpp, bytes(u.mem_read(fg+y*(w+fgo)*bpp,w*bpp)))
            else:
                # PFC / blend: implement A8/A4 alpha onto 8-bit using FG color index (approx)
                s.ops[-1]=s.ops[-1]+('UNIMPL',ffmt,ofmt)
