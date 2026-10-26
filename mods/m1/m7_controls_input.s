// SPDX-License-Identifier: GPL-3.0-or-later
// Hold shortcuts, M7 pad input (port of bento-community-lab K16 input.s).
// While exactly one of TRACKS / LAUNCH / MIXER is held, a pad press is
// consumed (no note) and posted to the UI queue as request 0xFE
// {magic, 0xFFFFFFFE, column 0-7, action, tail}: action = 1/2 TRACKS top/bottom,
// 3/4 LAUNCH top/bottom, 5/6 MIXER top/bottom. The pad stays silent until it is
// released. Changes from Lab: PROJ is not a modifier (no PROJ+pad drum mute),
// so PROJ+pad behaves as on stock.
// Entry (stock 0x0808D1FE): r4 = new pressure, r3 = old, r7 = logical pad,
// r11 = receiver. State line @STATE@ (M7-only): magic, u16 suppress mask,
// dropped count, enable byte at +12, pad map at +16.
push.w {r0-r12, lr}
sub sp, #32
ldr r8, =@STATE@
ldr r0, [r8]
ldr r1, =@STATE_MAGIC@
cmp r0, r1
bne passthrough
ldrh r9, [r8, #4]
movs r0, #1
lsl.w r10, r0, r7
tst r9, r10
beq fresh
cmp r4, #0
bne consumed
bic.w r9, r9, r10
strh r9, [r8, #4]
b consumed
fresh:
cmp r3, #0
bne passthrough
cmp r4, #0
beq passthrough
ldrb r0, [r8, #12]
cmp r0, #1
bne passthrough
add.w r0, r11, #@LAUNCH_MASK@
ldrh r0, [r0, #0x338]
and.w r0, r0, #@MODIFIERS_MASK@
cmp r0, #0
beq passthrough
cmp.w r0, #@TRACKS_MASK@
beq tracks
cmp.w r0, #@LAUNCH_MASK@
beq launch
cmp.w r0, #@MIXER_MASK@
beq mixer
// Ambiguous multi-button chord: silence the pad without changing anything.
orr.w r9, r9, r10
strh r9, [r8, #4]
b consumed
mixer:
movs r5, #5
b mapped
launch:
movs r5, #3
b mapped
tracks:
movs r5, #1
mapped:
cmp r7, #8
it hs
addhs r5, #1
add.w r0, r8, #16
ldrb r6, [r0, r7]
cmp r6, #15
bhi passthrough
orr.w r9, r9, r10
strh r9, [r8, #4]
ldr r0, =@PRODUCER_GLOBAL@
ldr r0, [r0]
add.w r1, r0, #@TRACKS_MASK@
ldr r2, [r1, #0x1a0]
ldr r3, [r1, #0x180]
subs r2, r2, r3
cmp r2, #224
bhs dropped
movs r1, #0
str r1, [sp]
movs r1, #254
strb r1, [sp]
ldr r1, =@EVENT_MAGIC@
str r1, [sp, #4]
mvn r1, #1
str r1, [sp, #8]
and.w r6, r6, #7
str r6, [sp, #12]
str r5, [sp, #16]
ldr r1, =@EVENT_TAIL@
str r1, [sp, #20]
mov r1, sp
ldr r12, =@ENQUEUE@
blx r12
b consumed
dropped:
ldr r0, [r8, #8]
adds r0, #1
str r0, [r8, #8]
consumed:
add sp, #32
pop.w {r0-r12, lr}
push {r0, r1}
ldr r0, =@PAD_NEXT@
str r0, [sp, #4]
pop {r0, pc}
passthrough:
add sp, #32
pop.w {r0-r12, lr}
cmp r4, r3
beq equal
push {r0, r1}
ldr r0, =@PAD_CHANGED@
str r0, [sp, #4]
pop {r0, pc}
equal:
push {r0, r1}
ldr r0, =@PAD_NEXT@
str r0, [sp, #4]
pop {r0, pc}
