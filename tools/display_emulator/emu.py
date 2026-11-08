# Ported from hunting-ghosts/bento-playground @9fc7787, research/display_emulator/emu.py
# GPL-3.0-or-later. Changes for bento_enhanced are marked "BE:".
import hashlib,json,os,struct,sys
sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..','mods','m1'))
import bases   # BE: stock addresses come from the base table (mods/m1/bases)
from unicorn import *
from unicorn.arm_const import *
BASE=0x08040000
RET=0x0FFF0000
def table_for(img,data,allow_draft=False):
    """BE: the base table for a stock image (by sha256), or for a build: the manifest.json next to
    the image (build output) or one folder up (a kit: manifest.json in the kit root, the image in
    01_TEST_FIRMWARE/). A draft table refuses its REVIEW entries unless allow_draft."""
    sha=hashlib.sha256(data).hexdigest()
    b=bases.find_sha(sha,allow_draft)
    if b: return b
    if isinstance(img,str):
        d=os.path.dirname(os.path.abspath(img))
        for m in (d,os.path.dirname(d)):
            if os.path.exists(os.path.join(m,'manifest.json')):
                return bases.from_build(m,allow_draft=allow_draft)
    raise SystemExit('emu: not a stock image and no manifest.json beside it or one folder up: pass base=bases.load(...)')
class Emu:
    def __init__(s,img,init_ctors=True,base=None,allow_draft=False):
        u=s.u=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        s.img=open(img,'rb').read() if isinstance(img,str) else img
        s.S=base or table_for(img,s.img,allow_draft)   # BE: per-base addresses (s.S.NAME)
        u.mem_map(0x08000000,0x400000); u.mem_write(BASE,s.img)
        for a,sz in [(0x00000000,0x10000),(0x0FFF0000,0x10000),(0x10000000,0x100000),(0x20000000,0x20000),(0x24000000,0x80000),(0x30000000,0x48000),(0x38000000,0x10000),(0x38800000,0x1000),
                     (0x40000000,0x10000000),(0x50000000,0x2001000),(0x52002000,0x5FFE000),(0x58000000,0x8000000),(0xC0000000,0x4000000),(0xE0000000,0x100000),(0x90000000,0x1000000)]:
            u.mem_map(a,sz)
        u.mem_write(RET,b'\x00\xbf'*16)
        import dma2d; s.dma=dma2d.DMA2D(u)
        # BE (S4/S5): the emulated stack is the top of M7 DTCM (0x20000000..0x20020000, mapped above), which no M4 code
        # can address: 128 KB that cannot overlap a stock global of either base. Was 0x3003F000: in 1.6.1's D2 slice,
        # and 1.6.8's SAI DMA buffers (0x3003C000..0x30040000). Not the M4's own main stack (vector SP, 0x1400 B):
        # a deeper emulated call chain would run into stock .bss there without any fault.
        s.sp=0x20020000  # addr-ok: harness
        # enable FPU: emulate by setting CPACR not needed in unicorn? set anyway
        s.fpu()
        s.data_init()
        if init_ctors: s.ctors()
    def fpu(s):
        try:
            s.u.reg_write(UC_ARM_REG_C1_C0_2, s.u.reg_read(UC_ARM_REG_C1_C0_2)|(0xf<<20))
        except Exception: pass
        try: s.u.reg_write(UC_ARM_REG_FPEXC,0x40000000)
        except Exception: pass
    def r(s,a): return struct.unpack('<I',s.u.mem_read(a,4))[0]
    def w(s,a,v): s.u.mem_write(a,struct.pack('<I',v&0xffffffff))
    def h(s,a): return struct.unpack('<H',s.u.mem_read(a,2))[0]
    def text(s,a,n=64): return bytes(s.u.mem_read(a,n)).split(b'\0')[0].decode('latin1')
    def call(s,fn,*args,count=20_000_000,sp=None):
        u=s.u
        regs=[UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3]
        st=sp or s.sp
        extra=list(args[4:])
        st-=4*len(extra); st&=~7
        for i,v in enumerate(extra): s.w(st+4*i,v)
        for i,v in enumerate(args[:4]): u.reg_write(regs[i],v&0xffffffff)
        u.reg_write(UC_ARM_REG_SP,st); u.reg_write(UC_ARM_REG_LR,RET|1)
        u.emu_start(fn|1,RET,count=count)
        return u.reg_read(UC_ARM_REG_R0)
    def data_init(s):
        # run crt copy/zero section (1.6.1: 0810044C..081004B6)
        s.u.reg_write(UC_ARM_REG_SP,s.sp)
        s.u.emu_start(s.S.M4_CRT_INIT|1,s.S.M4_CRT_CTORS,count=50_000_000)
    def ctors(s):
        s.u.reg_write(UC_ARM_REG_SP,s.sp)
        s.u.reg_write(UC_ARM_REG_LR,RET|1)
        # run init_array loop until it falls through to whatever follows
        s.u.emu_start(s.S.M4_CRT_CTORS|1,s.S.M4_CRT_CTORS_END,count=50_000_000)
from unicorn.arm_const import UC_ARM_REG_PC,UC_ARM_REG_LR
def where(e):
    return hex(e.u.reg_read(UC_ARM_REG_PC)), hex(e.u.reg_read(UC_ARM_REG_LR))
