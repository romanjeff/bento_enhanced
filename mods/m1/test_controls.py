# SPDX-License-Identifier: GPL-3.0-or-later
"""Emulated checks for the hold shortcuts (port of bento-community-lab K16 hold controls).

    python test_controls.py <stock 1.6.1 bento1.bin> <m1 build dir>

LabInput / LabUI are Lab's test_input.py and test_controls.py (GPL-3.0-or-later),
run against our image: stock's real pad receiver, UI queue, property reads and
track lookups execute; UI/engine side effects are stubbed. Ours covers what the
port changes: PROJ is not a modifier / not Shift, CH8's encoder-counts-as-use,
and the per-boot reset.
"""
import json, os, struct, sys, unittest
from pathlib import Path
os.environ["BENTO_STOCK_IMAGE"] = sys.argv[1]
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE
from unicorn.arm_const import *
from lab_receiver_fixture import Receiver, B, UIQUEUE

BASE = 0x08040000
OUT = Path(sys.argv[2])
IMAGE = (OUT / "bento1.bin").read_bytes()
SYMS = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["m4_symbols"].items()}
MIXER_RENDER = SYMS["controls.mixer_render"]
LAYOUT = {k: int(v, 16) for k, v in json.loads((OUT / "manifest.json").read_text())["layout"].items()}
CONSUMER, CTRL_STARTUP, STATE = LAYOUT["CTRL_WRAP"], 0x080D9800, 0x24048CA0
REGS = [UC_ARM_REG_R0 + i for i in range(13)]

class NativeProperties(dict):
 def __init__(self,f):super().__init__();self.f=f;self.tables={};self.next=0x20035000
 def __setitem__(self,k,v):
  super().__setitem__(k,v);obj,key=k
  if obj not in self.tables:self.tables[obj]=self.next;self.next+=0x100
  table=self.tables[obj];items=[(prop,val) for (o,prop),val in self.items() if o==obj]
  self.f.w(obj+8,table);self.f.w(obj+12,len(items))
  for i,(prop,val) in enumerate(items):self.f.u.mem_write(table+i*8,struct.pack('<IHBB',val&0xffffffff,prop,1,0))
class Fixture:
 def __init__(self):
  self.u=Uc(UC_ARCH_ARM,UC_MODE_THUMB);self.u.mem_map(BASE,0x1c0000);self.u.mem_write(BASE,IMAGE)
  for a,n in [(0x20000000,0x40000),(0x24040000,0x10000),(0x30000000,0x40000)]:self.u.mem_map(a,n)
  self.m=0x20000000;self.globalobj=self.m+4;self.obj=0x20011000;self.vtable=0x20012000;self.child=0x20013000
  self.root=0x20014000;self.w(self.m,self.root);self.w(self.m+4,0x0819abcd)
  self.w(self.root+0x30,8);self.w(self.root+0x54,8)
  for i in range(8):
   o=self.obj+i*0x100;self.w(o,self.vtable);self.w(o+16,77+i);self.w(self.root+i*4,o);self.w(self.root+0x34+i*4,o)
  self.w(self.vtable,0x081ff001);self.w(self.vtable+28,0x081ff011)
  self.props=NativeProperties(self);self.props[(self.globalobj,0x131)]=3;self.props[(self.obj,0x133)]=2;
  for i in range(8):
   self.props[(self.obj+i*0x100,0x73)]=i;self.props[(self.obj+i*0x100,0x133)]=2
  self.kind=14;self.runtime=(-1,-1,-1);self.exists=True
  self.blocked=False;self.log=[];self.page(0x17);self.contexts([11,22,33,44]);self.u.hook_add(UC_HOOK_CODE,self.hook)
 def w(self,a,v):self.u.mem_write(a,struct.pack('<I',v&0xffffffff))
 def r(self,a):return struct.unpack('<I',self.u.mem_read(a,4))[0]
 def page(self,p=None):
  if p is not None:self.u.mem_write(self.m+0x12c,bytes([p]))
  return self.u.mem_read(self.m+0x12c,1)[0]
 def contexts(self,v=None):
  if v is not None:
   for i,n in enumerate(v):self.w(self.m+0x13c+i*4,n)
  return [self.r(self.m+0x13c+i*4) for i in range(4)]
 def ret(self,v=0):self.u.reg_write(UC_ARM_REG_R0,v&0xffffffff);self.u.reg_write(UC_ARM_REG_PC,self.u.reg_read(UC_ARM_REG_LR))
 def hook(self,u,a,size,data):
  r=[u.reg_read(x) for x in REGS[:4]]
  if a in [0x0813f2fc,0x0813f990]:u.emu_stop();return
  if a==0x081ff000:self.ret(self.kind)
  elif a==0x081ff010:
   if r[2]==0 and self.exists:self.w(r[3],self.child);self.props[(self.child,0x73)]=2;self.props[(self.child,0x6f)]=2;self.ret(1)
   else:self.ret(0)
  elif a==0x081420ec:self.log.append(('nav',r[1]));self.page(r[1]);self.ret()
  elif a==0x08142afc:
   key=self.r(r[1]+8);self.log.append(('button',key))
   if not self.blocked:self.page(({13:26,26:25,25:13}.get(self.page(),13)) if key==7 else {2:2,3:30,5:31,10:7,11:23}[key])
   self.ret()
  elif a==0x0813bc3c:
   self.log.append(('select',r[1]));self.props[(self.globalobj,0x131)]=r[1];self.contexts([77,0,99,0]);self.ret()
  elif a==0x081322ac:self.w(r[2],self.runtime[1]);self.w(r[3],self.runtime[2]);self.ret(self.runtime[0])
  elif a==0x08136bd4:self.log.append(('sequence',r[2],r[3]));self.w(self.m+0x144,100+r[2]);self.ret()
  elif a==0x0813bc24:
   mode=self.r(r[1]+8);self.u.mem_write(self.m+0x3c7c,bytes([mode!=0]));self.log.append(('pad_mode',mode));self.ret()
  elif a==0x081257f0:self.log.append(('flush_lights',r[0]));self.ret()
  elif a==0x08131ae0:
   e=[self.r(r[1]+4*i) for i in range(5)];self.log.append(('property',e[1],e[2],e[3]));self.props[(self.obj+(e[1]-77)*0x100,e[2])]=e[3];self.ret()
  elif a==0x0814baea:
   self.log.append(('mixer_mode',self.r(r[1])));self.u.mem_write(r[0]+0x9a08,bytes([not self.u.mem_read(r[0]+0x9a08,1)[0]]));self.ret()
  elif a==0x0814b630:self.log.append(('render',r[0]));self.ret(123)
  elif a==0x0810b5c8:self.log.append(('engine',r[1],r[2],r[3]));self.ret()
 def event(self,code,index,action=0,valid=True):
  sp=0x20030000;vals=[code,0x314d5442 if valid else 0,0xfffffffe,index,action,0x31525442]
  self.u.mem_write(sp+0x220,struct.pack('<6I',*vals))
  before=[0x1010+i for i in range(13)];before[4:8]=[self.m,code,0xfffffffe,index]
  for reg,v in zip(REGS,before):self.u.reg_write(reg,v)
  self.u.reg_write(UC_ARM_REG_SP,sp);self.u.reg_write(UC_ARM_REG_LR,0x20035001)
  self.u.emu_start(CONSUMER|1,0,count=10000)
  assert self.u.reg_read(UC_ARM_REG_SP)==sp
  if self.u.reg_read(UC_ARM_REG_PC)==0x0813f990:before[3]=code-17
  assert [self.u.reg_read(x) for x in REGS]==before
 def render(self):
  self.u.reg_write(UC_ARM_REG_R0,0x20018000);self.u.reg_write(UC_ARM_REG_R1,0)
  self.u.reg_write(UC_ARM_REG_SP,0x20030000);self.u.reg_write(UC_ARM_REG_LR,0x0813f2fd)
  self.u.emu_start(MIXER_RENDER|1,0,count=10000)
  assert self.u.reg_read(UC_ARM_REG_R0)==123
 def choose(self,cell=0):
  sp=0x20030000
  self.u.reg_write(UC_ARM_REG_SP,sp)
  self.u.reg_write(UC_ARM_REG_R0,self.m);self.u.reg_write(UC_ARM_REG_R1,sp-4);self.u.reg_write(UC_ARM_REG_R2,cell)
  self.u.emu_start(0x0813bd11,0x0813bd14,count=100)
  assert self.u.reg_read(UC_ARM_REG_PC)==0x0813bd14
  assert self.u.reg_read(UC_ARM_REG_SP)==sp-56
 def pad(self,a,t=0):self.event(254,t,a)
class LabUI(unittest.TestCase):
 def test_all_three_plain_buttons_snapshot_native_inline_property(self):
  for button,page in [(2,2),(3,30),(5,31)]:
   f=Fixture();f.event(175,button)
   self.assertEqual(f.r(STATE+32+20),3)
   self.assertEqual(f.page(),page)
   f.event(176,button);self.assertEqual(f.page(),page)
 def test_return_reads_inline_property_and_restores_changed_track(self):
  f=Fixture();f.event(175,3);f.pad(4)
  f.props[(f.globalobj,0x131)]=6
  f.event(176,3)
  self.assertIn(('select',3),f.log);self.assertEqual(f.props[(f.globalobj,0x131)],3)
  self.assertEqual(f.page(),23)
 def test_inst_hold_returns_to_step_seq_and_keeps_new_drum(self):
  f=Fixture();f.page(0x27);f.u.mem_write(f.m+0x13a,b'\x1e')
  f.event(175,10);self.assertEqual(f.page(),7)
  f.w(f.m+0x13c,501);f.w(f.m+0x140,502)
  selected=f.obj+3*0x100;f.props[(selected,0x74)]=11
  f.w(f.m+0x144,0);f.w(f.m+0x148,0)
  f.choose(11);f.event(176,10)
  self.assertEqual(f.page(),0x27);self.assertEqual(f.contexts(),[501,502,33,44])
  self.assertEqual(f.props[(selected,0x74)],11)
  self.assertEqual(f.u.mem_read(f.m+0x13a,1),b'\x1e')
 def test_inst_hold_stays_on_inst_without_choosing(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.event(176,10)
  self.assertEqual(f.page(),7);self.assertEqual(f.contexts(),[11,22,33,44])
 def test_inst_hold_from_piano_roll_and_other_pages_passes_stock(self):
  for page in [0x17,2,7,0x1f]:
   f=Fixture();f.page(page);f.event(175,10)
   self.assertEqual(f.log,[]);self.assertEqual(f.page(),page)
 def test_inst_hold_does_not_apply_to_loop_or_other_instruments(self):
  for kind in [0x11,0x10,0x16]:
   f=Fixture();f.kind=kind;f.page(0x27);f.event(175,10);self.assertEqual(f.log,[])
 def test_inst_hold_dialog_block_does_not_arm_return(self):
  f=Fixture();f.page(0x27);f.blocked=True;f.event(175,10);f.event(176,10)
  self.assertEqual(f.page(),0x27);self.assertFalse(any(x[0]=='nav' for x in f.log))
 def test_inst_hold_other_navigation_cancels_return(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.event(175,3);f.event(176,10)
  self.assertEqual(f.page(),0x1e)
 def test_inst_hold_touch_navigation_cancels_return(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.page(0x13);f.event(176,10)
  self.assertEqual(f.page(),0x13)
 def test_inst_hold_track_change_cancels_return(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.props[(f.globalobj,0x131)]=2;f.event(176,10)
  self.assertEqual(f.page(),7)
 def test_inst_hold_repeated_press_does_not_open_editor(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.event(175,10)
  self.assertEqual(f.log.count(('button',10)),1);f.event(176,10);self.assertEqual(f.page(),7)
 def test_inst_hold_returns_after_drum_editor_selection(self):
  for page in [9,0x14]:
   f=Fixture();f.page(0x27);f.event(175,10);f.page(page);f.w(f.m+0x140,808);f.choose(1);f.event(176,10)
   self.assertEqual(f.page(),0x27);self.assertEqual(f.r(f.m+0x140),808)
 def test_inst_reselect_current_sound_counts_as_choice(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.choose(0);f.event(176,10)
  self.assertEqual(f.page(),0x27)
 def test_inst_selection_before_hold_is_not_counted(self):
  f=Fixture();f.choose(0);f.page(0x27);f.event(175,10);f.event(176,10)
  self.assertEqual(f.page(),7)
 def test_inst_invalid_cell_does_not_count_as_choice(self):
  f=Fixture();f.page(0x27);f.event(175,10);f.choose(16);f.event(176,10)
  self.assertEqual(f.page(),7)
 def test_selection_observer_preserves_native_entry_registers_and_flags(self):
  for active in [0,1]:
   f=Fixture();f.w(0x24048cf0,active);sp=0x20030000
   regs=[0x1010+i for i in range(13)];regs[0]=f.m;regs[2]=7
   for r,v in zip(REGS,regs):f.u.reg_write(r,v)
   f.u.reg_write(UC_ARM_REG_SP,sp);f.u.reg_write(UC_ARM_REG_LR,0x08112345)
   f.u.reg_write(UC_ARM_REG_APSR,0xa8000000)
   f.u.emu_start(0x0813bd11,0x0813bd14,count=100)
   self.assertEqual([f.u.reg_read(r) for r in REGS],regs)
   self.assertEqual(f.u.reg_read(UC_ARM_REG_LR),0x08112345)
   self.assertEqual(f.u.reg_read(UC_ARM_REG_APSR)&0xf8000000,0xa8000000)
   self.assertEqual(f.u.reg_read(UC_ARM_REG_SP),sp-56)
   self.assertEqual(f.r(0x24048d04),active)
 def test_tracks_stays_inst(self):
  f=Fixture();f.event(175,2);f.pad(1);f.event(176,2);self.assertEqual(f.page(),7)
 def test_tracks_seq_playing_priority(self):
  f=Fixture();f.runtime=(4,5,-1);f.event(175,2);f.pad(2);f.event(176,2);self.assertIn(('sequence',5,0),f.log);self.assertEqual(f.page(),23)
 def test_tracks_seq_stopped_uses_selection(self):
  f=Fixture();f.pad(2);self.assertFalse(any(x[0]=='sequence' for x in f.log));self.assertEqual(f.page(),23)
 def test_loop_seq_opens_inst(self):
  f=Fixture();f.kind=17;f.pad(2);self.assertEqual(f.page(),7)
 def test_mixer_restore_keeps_toggle(self):
  f=Fixture();f.event(175,5);self.assertEqual(f.page(),31);f.pad(5);f.event(176,5)
  self.assertEqual(f.page(),23);self.assertEqual(f.contexts(),[11,22,33,44]);self.assertEqual(f.props[(f.obj,318)],1);self.assertFalse(any(x[0]=='select' for x in f.log))
 def test_mixer_twice_toggles_off(self):
  f=Fixture();f.pad(6);f.pad(6);self.assertEqual(f.props[(f.obj,317)],0)
 def test_plain_tap_keeps_page(self):
  for id,page in [(2,2),(3,30),(5,31)]:
   f=Fixture();f.event(175,id);f.event(176,id);self.assertEqual(f.page(),page)
 def test_simultaneous_press_does_not_overwrite_snapshot(self):
  f=Fixture();f.pad(5);f.event(175,5);f.event(176,5);self.assertEqual(f.page(),23);self.assertEqual(f.log.count(('button',5)),1)
 def test_simultaneous_tracks_does_not_reopen_tracks(self):
  f=Fixture();f.pad(1);f.event(175,2);self.assertEqual(f.page(),7)
 def test_launch_start_and_restore(self):
  f=Fixture();f.event(175,3);f.pad(4);f.event(176,3);self.assertEqual(f.page(),23)
  self.assertEqual([x for x in f.log if x[0]=='engine'],[('engine',77,100,1),('engine',77,99,2)])
 def test_launch_same_playing_or_queued_no_restart(self):
  for status in [(10,2,-1),(-1,-1,2)]:
   f=Fixture();f.runtime=status;f.pad(4);self.assertFalse(any(x[0]=='engine' for x in f.log))
 def test_launch_empty_noop(self):
  f=Fixture();f.exists=False;f.pad(4);self.assertFalse(any(x[0]=='engine' for x in f.log))
 def test_stop_stopped_noop(self):
  f=Fixture();f.pad(3);self.assertFalse(any(x[0]=='engine' for x in f.log))
 def test_stop_playing(self):
  f=Fixture();f.runtime=(10,4,-1);f.pad(3);self.assertIn(('engine',77,100,0),f.log)
 def test_native_track_lookup_all_tracks_and_actions(self):
  for track in range(8):
   for action in range(1,7):
    f=Fixture();f.runtime=(4,1,-1);f.pad(action,track)
    if action<=2:self.assertIn(('select',track),f.log)
    elif action<=4:self.assertTrue(any(x[0]=='engine' and x[1]==77+track for x in f.log))
    else:self.assertIn(('property',77+track,318 if action==5 else 317,1),f.log)
 def test_missing_track_does_nothing(self):
  f=Fixture();f.w(f.root+0x54,0);f.pad(5);self.assertFalse(any(x[0]=='property' for x in f.log))
 def test_native_dialog_block_prevents_action(self):
  f=Fixture();f.blocked=True;f.pad(5);self.assertEqual(f.page(),23);self.assertFalse(any(x[0]=='property' for x in f.log))
 def test_mixer_render_enables_existing_controls_and_restores(self):
  f=Fixture();f.event(175,5);f.render();self.assertEqual(f.u.mem_read(0x20021a08,1),b'\1')
  f.pad(5);f.event(176,5);f.render();self.assertEqual(f.u.mem_read(0x20021a08,1),b'\0')
  self.assertEqual([x for x in f.log if x[0]=='mixer_mode'],[('mixer_mode',0x10c)]*2)
 def test_mixer_mode_already_on_is_preserved(self):
  f=Fixture();f.u.mem_write(0x20021a08,b'\1');f.event(175,5);f.render();f.event(176,5);f.render()
  self.assertFalse(any(x[0]=='mixer_mode' for x in f.log))
 def test_mixer_inactive_until_next_visit_keeps_original_mode(self):
  f=Fixture();f.event(175,5);f.render();f.pad(5);f.event(176,5)
  f.event(175,5);f.render();f.pad(5);f.event(176,5);f.render()
  self.assertEqual(f.u.mem_read(0x20021a08,1),b'\0')
 def test_bad_custom_record_passes_through(self):
  f=Fixture();f.event(254,0,1,False);self.assertEqual(f.page(),23);self.assertEqual(f.log,[])
 def test_ordinary_event_passes_through(self):
  f=Fixture();f.event(180,3);self.assertEqual(f.log,[])

class Input(Receiver):
 def __init__(self):
  super().__init__(False);self.u.mem_map(0x30000000,0x40000);self.u.mem_write(0x08040000,IMAGE)
  self.word(STATE,0x31534d42);self.u.mem_write(STATE+12,b'\1');self.u.mem_write(STATE+16,bytes(range(16)))
 def callback(self,u,a,size,data):
  if a==0x0808d1fe:return # Actual firmware branch executes.
  super().callback(u,a,size,data)
 def custom(self):return [struct.unpack('<6I',r) for d,r in self.records if d=='ui' and r[0]==254]
class LabInput(unittest.TestCase):
 def test_all_buttons_rows_columns(self):
  for mask,base in [(0x2000,1),(0x1000,3),(0x400,5)]:
   for pad in range(16):
    with self.subTest(mask=hex(mask),pad=pad):
     f=Input();pads=[0]*16;pads[pad]=1000;f.packet(mask,pads)
     self.assertEqual(f.events(),[])
     self.assertEqual(f.custom(),[(254,0x314d5442,0xfffffffe,pad%8,base+(pad//8),0x31525442)])
 def test_pressure_release_orders(self):
  for mask in [0x2000,0x1000,0x400]:
   for pad in [0,7,8,15]:
    for button_first in [False,True]:
     f=Input();pads=[0]*16;pads[pad]=1000;f.packet(mask,pads)
     pads[pad]=1500;f.packet(mask,pads);self.assertEqual(f.custom(),[]);self.assertEqual(f.events(),[])
     if button_first:f.packet(0,pads);self.assertEqual(f.events(),[])
     f.packet(0 if button_first else mask);self.assertEqual(f.events(),[]);self.assertEqual(f.readword(STATE+4)&65535,0)
 def test_preheld_notes_released(self):
  f=Input();f.packet(0,[1000]+[0]*15);self.assertEqual(f.events(),[(172,0,1000),(174,0,1000)])
  f.packet(0x1000,[1200]+[0]*15);self.assertEqual(f.custom(),[]);self.assertEqual(f.events(),[(174,0,1200)])
  f.packet(0x1000);self.assertEqual(f.events(),[(173,0,0),(174,0,0)])
 def test_full_queue_claims_silent_without_overwrite(self):
  f=Input();f.packet(0x400);f.word(UIQUEUE+0x21a0,225);f.word(UIQUEUE+0x2180,1)
  before=bytes(f.u.mem_read(UIQUEUE+0x980,256*24));f.packet(0x400,[1000]+[0]*15)
  self.assertEqual(f.events(),[]);self.assertEqual(f.readword(STATE+8),1);self.assertEqual(bytes(f.u.mem_read(UIQUEUE+0x980,256*24)),before)
 def test_ambiguous_modifiers_do_not_trigger_notes_or_actions(self):
  for mask in [0x3000,0x2400,0x1400,0x3400]:
   f=Input();f.packet(mask,[1000]+[0]*15);self.assertEqual(f.events(),[]);self.assertEqual(f.custom(),[])
   f.packet(0,[1500]+[0]*15);self.assertEqual(f.events(),[])
   f.packet();self.assertEqual(f.events(),[])
 def test_disabled_preserves_note_input(self):
  f=Input();f.u.mem_write(STATE+12,b'\0');f.packet(0x2000,[1000]+[0]*15);self.assertEqual(f.events(),[(172,0,1000),(174,0,1000)]);self.assertEqual(f.custom(),[])
 def test_startup_zeroes_only_m7_cache_line(self):
  f=Input();f.u.mem_write(STATE,bytes([0xa5])*256);f.u.reg_write(UC_ARM_REG_SP,0x2407e000)
  f.u.emu_start(CTRL_STARTUP|1,0x080404ba,count=200000)
  self.assertEqual(f.u.reg_read(UC_ARM_REG_PC),0x080404ba)
  self.assertEqual(bytes(f.u.mem_read(STATE+32,224)),bytes([0xa5])*224)
  self.assertEqual(bytes(f.u.mem_read(STATE+16,16)),bytes(range(16)))

class Ours(unittest.TestCase):
    def test_proj_press_and_release_are_stock(self):
        for code in (175, 176):
            f = Fixture(); f.event(code, 7)
            self.assertEqual(f.log, []); self.assertEqual(f.page(), 0x17)

    def test_proj_plus_pad_plays_notes_as_on_stock(self):
        f = Input(); f.packet(0x100, [1000] + [0] * 15)
        self.assertEqual(f.custom(), []); self.assertEqual(f.events(), [(172, 0, 1000), (174, 0, 1000)])

    def test_proj_with_a_hold_button_still_uses_the_hold(self):
        f = Input(); f.packet(0x1100, [1000] + [0] * 15)
        self.assertEqual(f.events(), []); self.assertEqual(f.custom()[0][4], 3)

    def test_encoder_turn_while_holding_launch_or_mixer_returns_on_release(self):
        for button, page in ((3, 30), (5, 31)):
            f = Fixture(); f.event(175, button); self.assertEqual(f.page(), page)
            f.event(0xab, 0); f.event(176, button)
            self.assertEqual(f.page(), 0x17, button)

    def test_encoder_turn_without_a_hold_does_nothing(self):
        f = Fixture(); f.event(0xab, 0); f.event(175, 3); f.event(176, 3)
        self.assertEqual(f.page(), 30, "plain tap still stays on LAUNCH")

    def test_tracks_hold_with_encoder_stays_on_destination(self):
        f = Fixture(); f.event(175, 2); f.event(0xab, 0); f.event(176, 2)
        self.assertEqual(f.page(), 2)

    def test_state_from_a_previous_boot_is_discarded(self):
        f = Fixture()
        f.w(0x240485E0, 0x31474144); f.w(0x240485E0 + 12, 1)      # boot gen 1
        f.event(175, 3)                                           # LAUNCH held
        f.w(0x240485E0 + 12, 2)                                   # reboot: same RAM, gen 2
        f.event(0xab, 0); f.event(176, 3)
        self.assertEqual(f.page(), 30, "stale hold not acted on after reboot")

    def test_startup_leaves_our_diag_and_condition_ram_alone(self):
        f = Input(); u = f.u
        u.mem_write(0x24048400, bytes([0x5a]) * (STATE - 0x24048400))
        u.mem_write(STATE + 32, bytes([0x5a]) * (0x24048DA0 - STATE - 32))
        u.reg_write(UC_ARM_REG_SP, 0x2407e000)
        u.emu_start(CTRL_STARTUP | 1, 0x080404ba, count=200000)
        self.assertEqual(bytes(u.mem_read(0x24048400, STATE - 0x24048400)), bytes([0x5a]) * (STATE - 0x24048400))
        self.assertEqual(bytes(u.mem_read(STATE + 32, 0x24048DA0 - STATE - 32)), bytes([0x5a]) * (0x24048DA0 - STATE - 32))

class StrikeMenu(unittest.TestCase):
    def label(self, f):
        a = 0x24048D50; t = b""
        while True:
            c = bytes(f.u.mem_read(a, 1))
            if c == b"\0": return t.decode()
            t += c; a += 1

    def test_menu_event_toggles_the_setting_and_label(self):
        f = Fixture(); f.event(180, 3)                 # any event: setting initialised (on)
        self.assertEqual(self.label(f), "Strike Vel: On")
        f.event(0x10F0, 0)
        self.assertEqual(self.label(f), "Strike Vel: Off")
        self.assertEqual(bytes(f.u.mem_read(0x24048D4C, 1))[0], 0)
        f.event(0x10F0, 0)
        self.assertEqual(self.label(f), "Strike Vel: On")
        self.assertEqual(f.log, [], "nothing else happens")

if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"])
