# Ported from hunting-ghosts/bento-playground @9fc7787, research/display_emulator/render.py
# GPL-3.0-or-later. Changes for bento_enhanced are marked "BE:".
import emu,struct
from unicorn.arm_const import *
W,H=1280,800
FB=0xC1000000
SURF=0x24078000; CTX=0x24078100
def surface(e,rot=1,w=W,h=H):
    e.u.mem_write(FB,bytes(w*h))
    e.u.mem_write(SURF,struct.pack('<IHHIBBH',FB,w,h,0,5,rot,1))
    e.u.mem_write(CTX,struct.pack('<II',1,SURF))
def paint(e,widget):
    vt=e.r(widget)
    e.call(e.r(vt+4),widget,CTX)
    e.call(0x0816F9E8,widget,CTX,0)
def png(e,path,w=W,h=H,crop=None,pal=None):
    from PIL import Image
    data=bytes(e.u.mem_read(FB,w*h))
    im=Image.frombytes('P',(w,h),data)
    if pal is None:
        import random
        p=[]
        for i in range(256):
            if i==0: p+= [0,0,0]
            else:
                random.seed(i); p+=[random.randint(40,255) for _ in range(3)]
        pal=p
    im.putpalette(pal)
    if crop: im=im.crop(crop)
    im.convert('RGB').save(path)
    return data
HDMA2D=0x30000E68
def setup(e):
    e.w(HDMA2D,0x52001000)
    e.call(0x08167A1E,SURF,FB,480,1280,5,4)
    e.u.mem_write(CTX,struct.pack('<II',1,SURF))
def clear(e,c=0):
    e.u.mem_write(FB,bytes([c])*(480*1280))
def image(e):
    from PIL import Image
    d=bytes(e.u.mem_read(FB,480*1280))
    return Image.frombytes('L',(480,1280),d), d
def boot_gfx(e):
    e.w(0x3000004C,0xC2000000); e.w(0x30000048,0xC3F00000)
    cache=struct.unpack_from('<I',e.img,(((0x0811501E+4)&~3)+0xf0)-0x08040000)[0]
    e.call(0x0817E3A4,cache)
    setup(e)
    return cache
def ui(e):
    import numpy as np
    from PIL import Image
    d=np.frombuffer(bytes(e.u.mem_read(FB,480*1280)),dtype=np.uint8).reshape(1280,480)
    return d[::-1, :].T.copy()   # glass[Y][X] = fb[1279-X][Y]
PAL=None
def save(e,path,crop=None,scale=1):
    from PIL import Image
    a=ui(e); im=Image.fromarray(a,'P')
    p=[0]*768
    # grayscale default, highlight known palette idx
    for i in range(256): p[3*i:3*i+3]=[i,i,i]
    for idx,col in {0xf1:(20,20,20),0xf2:(235,235,235),0xf3:(120,120,120),0xf4:(70,70,70),0xf5:(180,180,180),0xf6:(255,140,0)}.items(): p[3*idx:3*idx+3]=col
    im.putpalette(p)
    if crop: im=im.crop(crop)
    if scale!=1: im=im.resize((im.width*scale,im.height*scale),Image.NEAREST)
    im.convert('RGB').save(path)
ROOT=0x30034d48
def page_reset(e,root=ROOT):
    # Same effect as native 0818012C minus the parent pointer at +FDC (not built in this harness)
    for k in range(12):
        b=root+0x54c+k*0xd0
        e.call(0x0816A714,b,1); e.call(0x0816A5E6,b,0); e.u.mem_write(b+0x30,b'\x01')
    e.u.mem_write(root+0x117c,b'\x01')
def header(e,tpl_bytes,root=ROOT):
    buf=0x24070000; e.u.mem_write(buf,tpl_bytes); e.call(0x0817FAF8,root,buf)
def tpl(e,k): return bytes(e.u.mem_read(0x8199008+k*0x1c4,0x1c4))
