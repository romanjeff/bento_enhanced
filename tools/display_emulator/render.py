# Ported from hunting-ghosts/bento-playground @9fc7787, research/display_emulator/render.py
# GPL-3.0-or-later. Changes for bento_enhanced are marked "BE:".
import emu,struct
from unicorn.arm_const import *
W,H=1280,800
FB=0xC1000000   # addr-ok: harness frame buffer
SURF=0x24078000; CTX=0x24078100   # addr-ok: harness
def surface(e,rot=1,w=W,h=H):
    e.u.mem_write(FB,bytes(w*h))
    e.u.mem_write(SURF,struct.pack('<IHHIBBH',FB,w,h,0,5,rot,1))
    e.u.mem_write(CTX,struct.pack('<II',1,SURF))
def paint(e,widget):
    vt=e.r(widget)
    e.call(e.r(vt+4),widget,CTX)
    e.call(e.S.WIDGET_PAINT_CHILDREN,widget,CTX,0)
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
HDMA2D=None   # BE: per base: e.S.HDMA2D
def setup(e):
    e.w(e.S.HDMA2D,0x52001000)
    e.call(e.S.SURFACE_INIT,SURF,FB,480,1280,5,4)
    e.u.mem_write(CTX,struct.pack('<II',1,SURF))
def clear(e,c=0):
    e.u.mem_write(FB,bytes([c])*(480*1280))
def image(e):
    from PIL import Image
    d=bytes(e.u.mem_read(FB,480*1280))
    return Image.frombytes('L',(480,1280),d), d
def ldr_literal(e,a):
    """BE: the literal address an `ldr rX, [pc, #imm]` at a loads (16-bit or ldr.w encoding)."""
    h1,h2=struct.unpack_from('<HH',e.img,a-0x08040000)
    if h1&0xF800==0x4800: return ((a+4)&~3)+(h1&0xFF)*4
    if h1==0xF8DF: return ((a+4)&~3)+(h2&0xFFF)
    raise SystemExit(f'{a:#x}: not an ldr rX, [pc, #imm]')
def boot_gfx(e):
    e.w(e.S.UI_BUMP_CUR,0xC2000000); e.w(e.S.UI_BUMP_END,0xC3F00000)   # addr-ok: harness heap
    cache=struct.unpack_from('<I',e.img,ldr_literal(e,e.S.GFX_CACHE_LDR)-0x08040000)[0]
    e.call(e.S.GFX_CACHE_INIT,cache)
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
# BE: the header root is per base (e.S.HEADER_ROOT, the default for root= below), as is its layout
# (e.S.HEADER_BUTTONS / _STRIDE / _COUNT / _HIDDEN, e.S.HEADER_ARMED, e.S.HEADER_DESC_SIZE).
def page_reset(e,root=None):
    root=root or e.S.HEADER_ROOT
    # Same effect as native 0818012C minus the parent pointer at +FDC (not built in this harness)
    for k in range(e.S.HEADER_BUTTON_COUNT):
        b=root+e.S.HEADER_BUTTONS+k*e.S.HEADER_BUTTON_STRIDE
        e.call(e.S.WIDGET_SET_HIDDEN,b,1); e.call(e.S.WIDGET_SET_ACTIVE,b,0); e.u.mem_write(b+e.S.HEADER_BUTTON_HIDDEN,b'\x01')
    e.u.mem_write(root+e.S.HEADER_ARMED,b'\x01')
def header(e,tpl_bytes,root=None):
    root=root or e.S.HEADER_ROOT
    buf=0x24070000; e.u.mem_write(buf,tpl_bytes); e.call(e.S.HEADER_BUILD,root,buf)   # addr-ok: harness buffer
def tpl(e,k): return bytes(e.u.mem_read(e.S.HEADER_TEMPLATES+k*e.S.HEADER_DESC_SIZE,e.S.HEADER_DESC_SIZE))
