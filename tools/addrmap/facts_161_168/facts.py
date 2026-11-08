# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified facts for 1.6.1 -> 1.6.8 that the matcher can't produce or got wrong (workflow
map-161-to-168, scratch rc14: layout/, verify/hand_checks.csv; docs/plan-168-port.md).

FACTS: name -> (value, status, why). The value is used as is ("fact"); gen_table.py still reads the
new image at patch sites and flags changed bytes. FACT_NEEDS_REVIEW: facts that are right but
leave work for a sprint. REVIEW_OURS: our own placement and ids that have to move or be confirmed.
HINTS: evidence for an entry that is still REVIEW (appended to its REVIEW text, for the sprint
that settles it).

REVIEWED: entries a person has settled in a later sprint (S2..S5). Move each review here, not
only into the generated stock_168.py: rerunning gen_table.py rewrites that file from these facts.
    "NAME": dict(value=0x..., evidence="how it was checked", orig="hex of the bytes at a patch site")
The generator uses the value with status "reviewed" and no REVIEW, but checks it against the new
image again: a patch site must hold `orig` (required for entries with expected bytes), and keys /
offsets / events must agree with mods/m1/expected_values.py. Any disagreement becomes REVIEW.
"""

FACTS = {
    # the matcher reported it unmatched; by hand: the same bump (left -= n; cur += n) with register
    # renames, its literals at 0x080A0838 / 0x080A083C (hand_checks.csv)
    "ALLOC_BUMP": (0x080A0740, "fact", "hand check: same bump, register renames; literals 0x080A0838/3C"),
    # M4 globals (layout/ram_xlate: hundreds of consistent votes; plan "What changed")
    "MODEL": (0x30025300, "fact", "M4 model 0x30024B00 -> 0x30025300 (235 literal votes)"),
    "REGISTRY": (0x30032DE4, "fact", "registry pointer 0x30031484 -> 0x30032DE4"),
    "UI_TICK_MS": (0x300378F8, "fact", "M4 UI tick 0x30035F40 -> 0x300378F8 (40 votes)"),
    "REG_KEYS": (498, "fact", "the registry has 498 entries (499 would write past the end)"),
    "MODEL_DIRTY": (0x3FF4, "fact", "model's modified flag +0x36C4 -> +0x3FF4"),
    # pad router fields (hand check of LIVE_SITE 0x08085B6C -> 0x080865EA)
    "ROUTER_SEL_TRACK": (0x308, "fact", "hand check: router +0x2C8 -> +0x308"),
    "ROUTER_PAD_KEYS": (0x30C, "fact", "hand check: router +0x2CC -> +0x30C"),
    # the version string moved (plan)
    "VERSION_STR": (0x0818DD94, "fact", "version string moved"),
    # our M4 blocks: appended after stock's new end (0x081B6E34); plan: from 0x081B7000
    "APPEND_FROM": (0x081B7000, "fact", "plan: append M4 code from 0x081B7000 (32,308 B under the proven size)"),
    # ext pool: start values and allocator semantics unchanged, the Region still fits (plan, measured);
    # build.py's check_region_assumptions verifies the pool on every build
    "REGION": (0xC5FFC000, "fact", "ext pool unchanged; build re-checks it"),
    "PROBE_SCRATCH": (0xC5FF0000, "fact", "below the Region in the unchanged ext pool"),
    "EXT_POOL_END": (0xC6000000, "fact", "POOL_LEFT_INIT + POOL_CUR_INIT, unchanged"),
    # header bar templates: the matcher's content match (0x0819BB58) was on a window that starts with
    # zeros and is wrong. The template loop at 0x081435E4 loads 0x0819BB40 (literals 0x08143904,
    # 0x08143D40; 1.6.1: 0x0814196C loads 0x08199008) and steps 0x1DC (1.6.1: 0x1C4), 0x26 templates.
    "HEADER_TEMPLATES": (0x0819BB40, "fact", "template loop 0x081435E4: literals 0x08143904/0x08143D40, stride 0x1DC"),
    "HEADER_DESC_SIZE": (0x1DC, "fact", "template stride and HEADER_GEN's copy: 0x1C4 -> 0x1DC"),
}

# S3 evidence shared by several entries (scratch rc18/t, rc18/impl/fcmp.py)
_SEQ = ("sequencer block 1.6.1 0x0805F8E0..0x08060160 = 1.6.8 0x0805FC80..0x08060500 (+0x3A0), all five functions "
        "identical (offcmp; implementer's own masked compare rc18/impl/fcmp.py: 0 diffs)")
_PROC = ("timed-note processor 0x08060C40..0x080611C2 (1.6.1 0x08060820, +0x420) identical (offcmp, 0x582 B: 14 moved "
         "branches, one flash literal 0x080BDBEC -> 0x080BE3AC whose first 15 floats are equal)")

REVIEWED = {
    # ======== S2 (2026-11-07): what the black-box-only 1.6.8 build uses (groups trace, stall, irq30,
    # bbox and the build's own checks). Addresses are 1.6.8 unless marked 1.6.1. Checked in scratch rc17
    # (investigators' d.py, cmp.py, blk.py, mpu.py, xref.py, words.py, movt.py; v1scan.py, v2dis.py,
    # v3words.py) and by the build: orig bytes, replayed prologues, check_region_assumptions,
    # check_slice_assumptions; test_diag.py emulates 1.6.8's own code around every hook.
    # ---- trace (M7 entry trampolines) ----
    "H_INPUT_SITE": dict(
        value=0x08059960, orig="2de9f043",
        evidence="engine input handler entry (1.6.1 0x08059980): push.w {r4-r9,lr} (the replayed prologue), "
                 "ldr r3,[r1,#8]; sub sp,#0x24; mov r4,r1; mov r7,r0; ldrb r2,[r1] identical, r0=engine r1=event; "
                 "only diffs key immediates 0x18E->0x18C (0x08059990) and bls->blo (0x08059986); pad events "
                 "pass through to the bus (b.w 0x0809FC40 at 0x08059A92), produced by PAD_RECEIVER 0x0808DB60 "
                 "(byte-identical to 1.6.1 0x0808D080 apart from bl offsets: movs #0xad 0x0808DCFE, #0xac "
                 "0x0808DEBA), so ev[0] type / ev[3] pad / ev[4] raw are as on 1.6.1"),
    # ---- stall (M7 main audio loop 0x0808E9E0, called from 0x080A5CB8 with r0=0x2401D320) ----
    "STALL_PROGRESS_SITE": dict(
        value=0x0808EA34, orig="12f024fb",
        evidence="bl WD_REFRESH 0x080A1080 after ldr r0,=0x2401DD04 (0x0808EA32), in the progress branch "
                 "(bics sl at 0x0808EA2C, beq 0x0808EA92); WD_REFRESH = 1.6.1's (movs r0,#0; push {r3,lr}: "
                 "no arguments); our stub keeps r0 and takes no stack arguments"),
    "STALL_CHECK_SITE": dict(
        value=0x0808EA94, orig="f3f784fa",
        evidence="bl MSG_PUMP 0x08081FA0 after ldr r0,=0x2401DCEC (0x0808EA92), then ldr r3,[r4,#0x18]; "
                 "adds; str; b 0x0808EA20 as 1.6.1 0x0808DFB4; MSG_PUMP prologue identical to 1.6.1 "
                 "(push {r4-r6,lr}; sub sp,#0x20; mov r4,r0): r0 only"),
    # ---- irq30 (M4 watchdog profile) ----
    "M4_WD_SITE": dict(
        value=0x08107DAC, orig="00f03afc",
        evidence="IRQ30 vector (0x081000B8 = 0x08107D9D) -> push {r3,lr}; ldr r0,=0x3001FD28; bl; bl; bl; "
                 "bl M4_WD_SUPERVISOR 0x08108624 at 0x08107DAC; pop {r3,pc}: same shape as 1.6.1 0x08107ACC, "
                 "so main_prof's msp[1] is still EXC_RETURN; supervisor loads its own r0 (=0x3000F630, "
                 "M4_WD_COUNTERS) and its check 0x081085C8 is byte-identical to 1.6.1 0x081082F8 "
                 "(+0xC enable, +8 tick, +4 Main, +0 Pcm; limits 0xD04 / 0x1869)"),
    # ---- region / allocator checks (check_region_assumptions) ----
    "MPU_SETUP": dict(
        value=0x0804FA00,
        evidence="unicorn run of 0x0804FA00 with HAL_MPU_ConfigRegion (0x080447C0) stubbed: R0..R13 "
                 "identical to 1.6.1 (R1 AXI 0x24000000+512K WB noWA, R8 0xC4000000+32M WB noWA, R2/R3 "
                 "0x24050000.. Device), one added R14 0x3003C000+16K Device shareable (0x0804FB78..0x0804FB8C); "
                 "15 calls, nothing else writes the MPU: R15 is free"),
    "MPU_SETUP_END": dict(
        value=0x0804FBD0,
        evidence="end of the literal pool: last literal 0x0300000D at 0x0804FBCC (1.6.1: 0x0804FBB0)"),
    "MPU_SETUP_SHA": dict(
        value="69e23b6a05d4dec3e59a147813eb5204bb3fc76611296e6b5269054ecbbb3029",
        evidence="sha256 of 0x0804FA00..0x0804FBD0; the same emulation over 1.6.1 reproduces its sha 33834f52..."),
    "ALLOC_BUMP_SHA": dict(
        value="47ab21ca7e946522a97a537bc9d3fdccbdb6106b5075451942aeaf786b7d8fdb",
        evidence="0x080A0740..0x080A0758: ldr r0,=POOL_LEFT; cmp n(r2),left; bhi fail; left-=n; str; ldr r1,"
                 "=POOL_CUR; ldr sb,[r1] (the block); cur+=n; str; b 0x080A065A: 1.6.1's bump with registers "
                 "renamed (n r3->r2, block r8->sb, as the other two pools' paths at 0x080A0712/0x080A0730)"),
    # ---- d0 / link figures (h_consume -> load_block / d0_publish) ----
    "LOAD_RING": dict(
        value=0x100451BC,
        evidence="*PRODUCER_GLOBAL + 0x51BC: the render scope 0x0808D2A0 loads r8=0x2401BD3C, r0=[r8], bl "
                 "0x08082CE0 (movw r0,#0x51bc; adds r0,r3,r0; bl 0x08056EC0); the ring object comes from "
                 "0x08082F60 (ldr r0,=0x10040000, unchanged) stored into 0x2401BD3C after 0x080A5B44; the load "
                 "ring code 0x08056EC0.. is 1.6.1's 0x08056F00 (fields +4, +0x408, +0x414, +8 + 4*idx)"),
    "AUDIO_DMA_EVENTS": dict(
        value=0x2401D33C,
        evidence="main audio loop object 0x2401D320 (ldr r0 at 0x080A5CB6, bl 0x0808E9E0) + 0x1C: "
                 "ldr.w fp,[r4,#0x1c] at 0x0808EA20 / ldr r5,[r0,#0x1c] at 0x0808EA08, as 1.6.1"),
    "PANEL_BOOTED": dict(
        value=0x2401D0AA,
        evidence="frame = pad receiver object 0x2401BD40 (ldr r0 at 0x0808EB20, bl PAD_RECEIVER 0x0808DB60) "
                 "+ 0x1328 (movw r5,#0x1328 at 0x0808DB64), +0x42 read by 0x080A3300 (ldrh [r0,#0x42]; "
                 "= 1.6.1 0x080A2B60); receiver code byte-identical apart from bl offsets"),
    "PANEL_CRC": dict(value=0x2401D0B4, evidence="frame 0x2401D068 + 0x4C, as PANEL_BOOTED"),
    "PANEL_TYPE": dict(value=0x2401D070,
                       evidence="frame + 8: PACKET_VALID 0x080A35A0 ldrb r0,[r0,#8] (= 1.6.1 0x080A2E00)"),
    "PANEL_BUTTONS": dict(value=0x2401D078, evidence="frame + 0x10, as PANEL_BOOTED"),
    # ---- our shared slice: AXI above the M7 stack, below M7 MPU R2 (docs/plan-168-port.md, S2) ----
    "D2_SLICE": dict(
        value=0x2404D000,
        note="our reset-surviving shared RAM (1.6.8: AXI 0x2404D000..0x24050000, 12 KB; the name is 1.6.1's, "
             "whose slice was in D2). M7: MPU R1, write-back no-write-allocate (same clean / invalidate rules "
             "as 1.6.1's default map); M4: MPU R0 Device, so M4 accesses must be aligned (-mstrict-align)",
        evidence="M7 MSP top 0x2404CDA0 (vector, 0x08040510..1C), .bss 0x240001D0..0x2401EDC0, heap "
                 "..0x24048DA0, stack 0x24048DA0..0x2404CDA0; nothing in the M7 startup writes above it "
                 "(test_diag SliceLocation emulates it); stock's own check 0x080A03F0 treats [stack top, "
                 "0x24050000) as slack; the two top-down AXI allocators (M7 0x0804AFC0, M4 0x08104EB0) start "
                 "at 0x24080000 and cap at 0x30000 (floor 0x24050000); disk-mode image 0x080E0000 uses "
                 "0x24000000..0x24029898 and 0x24058000..; no literal or movw/movt into [0x2404CDA0,0x24050000) "
                 "except 0x2404CDA0 and the R2 base 0x24050000 (8 aligned words there are instruction "
                 "encodings, OUR_RAM_WORDS). 4 KB aligned: leaves RAMCHK_AXI as a guard band below it, 4 KB "
                 "to grow, and an MPU fallback (R15 16 KB at 0x2404C000, SRD 0x03) possible. 1.6.1 device "
                 "logs: RAMCHK axi over 0x2404CDC0..0x24050000 changed=0 in all 78 readings, across "
                 "bootloader flashes and warm resets"),
    "D2_SLICE_END": dict(value=0x24050000, note="end of our slice (1.6.8: M7 MPU R2, Device, starts here)",
                         evidence="M7 MPU R2 (Device, 0x24050000..) and stock's boundary check 0x080A03F0; "
                                  "the slice uses +0..+0x2000 (diag_shared.h static asserts)"),
    "RAMCHK_AXI": dict(value=0x2404CDC0,
                       note="RAMCHK block: AXI between the M7 stack top and our slice (a guard band)",
                       evidence="M7 stack top 0x2404CDA0 + 0x20 (as 1.6.1); a stray write from below "
                                "shows here before it reaches the slice"),
    "RAMCHK_AXI_END": dict(value=0x2404D000, evidence="D2_SLICE"),
    "RAMCHK_D2A": dict(
        absent=True, value=None,
        note="1.6.1's RAMCHK block above the M4 stack in D2: none on 1.6.8",
        evidence="M4 stack 0x300396A8..0x3003AAA8 (0x08100510..1C, vector SP 0x3003AAA8) and the M4 reset "
                 "code's temporary stack from 0x3003C000 (0x0810041C) cover 0x30039100..0x3003C000"),
    "RAMCHK_D2B": dict(
        value=0x2404F000,
        note="RAMCHK block above the used part of our slice (slice + 0x2000 .. D2_SLICE_END, as on 1.6.1; "
             "AXI on 1.6.8, printed as 'top')",
        evidence="D2_SLICE + 0x2000; 1.6.1's D2 block 0x3003E000.. is stock's SAI DMA buffer under M7 R14"),
    "OUR_RAM_WORDS": dict(
        value=(0x0809F6C8, 0x080A4D60, 0x080A4D80, 0x080A4DA0, 0x080E84B4, 0x0810B42C, 0x0811AEF4, 0x0815EAF0),
        evidence="the only aligned words of 1.6.8 in our ranges: f8c0 2404 = str.w r2,[r0,#0x404] at "
                 "0x0809F6C8, f8d0 2404 (ldr.w), f8d2, f8c5, f8c3, ea42 2404 = orr.w (0x0811AEF4): instruction "
                 "encodings, no PC-relative load reads any of them (v3words.py); the same 8 encodings sit in 1.6.1's AXI block"),
    # ---- event ids the black box reads (m7_diag.c); also in expected_values.py ----
    "EV_PAD_PRESS": dict(value=0xAC, evidence="pad receiver movs r3,#0xac at 0x0808DEBA; 0x08097220 cmp #0xac"),
    "EV_PAD_RELEASE": dict(value=0xAD, evidence="pad receiver movs r3,#0xad at 0x0808DCFE; 0x08097224 cmp #0xad"),
    "EV_PAD_PRESSURE": dict(value=0xAE, evidence="pad router cmp r3,#0xae at 0x08085FFA (1.6.1 0x0808557A)"),
    "EV_VOICE_PRESSURE": dict(value=0x0E, evidence="External handler: 0x0E reaches POLY_SITE's bne.w (cmp #2 0x08062980, "
                                                   "cmp #3 0x08062984) as on 1.6.1; trace filter only in S2"),
    "EV_TRACK_NOTE_ON": dict(value=0x02, evidence="External handler cmp r3,#2 at 0x08062980"),
    "EV_TRACK_NOTE_OFF": dict(value=0x03, evidence="External handler cmp r3,#3 at 0x08062984"),
    "EV_OBJ_NOTE_ON": dict(value=0xB9, evidence="cmp r3,#0xb9 at 0x0805D620 (1.6.1 0x0805D500)"),
    "EV_OBJ_NOTE_OFF": dict(value=0xBA, evidence="cmp r3,#0xba at 0x0805D624 (1.6.1 0x0805D504)"),
    "EV_MIDI_NOTE_ON": dict(value=0x5B, evidence="M4 MIDI task 0x081053D8 cmp r5,#0x5b; subs #0x5b; cmp #8; "
                                                 "MIDI_JT 0x081053EC[0] = 0x08105243 (Note On case)"),
    "EV_MIDI_NOTE_OFF": dict(value=0x5C, evidence="0x081053DC cmp r5,#0x5c; MIDI_JT[1]"),
    "EV_MIDI_BEND": dict(value=0x5D, evidence="MIDI_JT[2] = 0x08105289"),
    "EV_MIDI_CC": dict(value=0x5E, evidence="MIDI_JT[3] = 0x081052C7"),
    "EV_MIDI_POLY_AT": dict(value=0x5F, evidence="MIDI_JT[4] = 0x08105417 (M4_LOOP: unused in stock)"),
    "EV_MIDI_CHAN_AT": dict(value=0x60, evidence="MIDI_JT[5] = 0x08105417 (unused in stock)"),
    "EV_MIDI_RT_FIRST": dict(value=0x61, evidence="MIDI_JT[6] = 0x08105365"),
    "EV_MIDI_RT_LAST": dict(value=0x63, evidence="MIDI_JT[8] = 0x08105339; cmp r5,#8 bound at 0x081053E2"),
    # ======== S2 rework (2026-11-07, review findings): every other entry the black-box-only build reads,
    # until now only machine-mapped (exact / moved). Checked in scratch rc17/s3 (ev.py: first 48 instructions
    # masked for branch / PC-relative operands, side-by-side context at each site; offs*.py: stock's accesses
    # through each object; RAM literal counts), and by the reviewers' own disassembly (rc17/rev, rv17).
    # ---- trace: M7 entry trampolines (each replays the instruction it replaces) ----
    "H_SEND_SITE": dict(
        value=0x08065B28, orig="8c4630b5",
        evidence="msg_send 0x08065B20 (1.6.1 0x08065600): ldr.w r3,[r0,#0x600]; cmp r3,#0x3f; bhi; then the site "
                 "mov ip,r1; push {r4,r5,lr} (replayed), returns to 0x08065B2C; 48 instructions identical (masked)"),
    "H_RING_SITE": dict(
        value=0x08082F20, orig="00f5005c",
        evidence="ring post entry (1.6.1 0x08082640): add.w ip,r0,#0x2000 (replayed); ldr.w r3,[ip,#0x360]; "
                 "and #0xf: write index +0x2360, 16 slots; 48 instructions identical (masked); returns to 0x08082F24"),
    "H_CONSUME_SITE": dict(
        value=0x08065AC0, orig="d0f80036",
        evidence="queue read entry (1.6.1 0x080655A0): ldr.w r3,[r0,#0x600] (replayed); cmp r3,r1; push {r4,lr}; "
                 "bhi; 48 instructions identical (masked); returns to 0x08065AC4"),
    # ---- bbox: M4 hooks ----
    "M4_BL_SITE": dict(
        value=0x08114E7C, orig="ecf7fcfe",
        evidence="defaultTask loop: mov r1,r5; mov r0,r6; bl 0x08101C78 (the site), ldr r0,[r4]; ldr r3,[r0]; "
                 "ldr r3,[r3,#8]; blx r3: the same context as 1.6.1 0x08114B10 (orig differs only by the bl offset)"),
    "M4_BL_TARGET": dict(value=0x08101C78, evidence="same address in both; 48 instructions identical (masked); "
                                                   "called with r0/r1 as on 1.6.1"),
    "SAVE_WRITE_SITE": dict(
        value=0x08131340,
        evidence="project save (1.6.1 0x0813044C): bl FILE_WRITER, 48 instructions identical (masked) from the "
                 "site; the build checks the bl target (save_write wrapper keeps r0-r3, returns FILE_WRITER's r0)"),
    "FILE_WRITER": dict(value=0x0813184C, evidence="1.6.1 0x08130958: 48 instructions identical (masked), "
                                                  "prologue and caller context as 1.6.1"),
    "FS_OPEN": dict(value=0x081204B4, evidence="f_open, 1.6.1 0x08120148: 48 instructions identical (masked)"),
    "FS_READ": dict(value=0x081207D0, evidence="f_read, 1.6.1 0x08120464: 48 instructions identical (masked)"),
    "FS_WRITE": dict(value=0x08120970, evidence="f_write, 1.6.1 0x08120604: 48 instructions identical (masked)"),
    "FS_CLOSE": dict(value=0x08120CE0, evidence="f_close, 1.6.1 0x08120974: 48 instructions identical (masked)"),
    "UI_BUMP_ALLOC": dict(
        value=0x08104DB4,
        evidence="1.6.1 0x08104C08: round up to 32, cur += n (UI_BUMP_CUR 0x3000004C), compare with UI_BUMP_END "
                 "0x30000048, fail path: the code through 0x08104DE2 identical (masked), only the literal pools differ"),
    # ---- irq30 / stall callees ----
    "M4_WD_SUPERVISOR": dict(
        value=0x08108624,
        evidence="push {r3,lr}; ldr r0,=0x3000F630 (M4_WD_COUNTERS, 1.6.1 0x3000F624); bl 0x081085C8 (1.6.1 "
                 "0x081082F8, the check M4_WD_SITE's review found byte-identical); pop {r3,pc}"),
    "WD_REFRESH": dict(value=0x080A1080, evidence="1.6.1 0x080A0460: 48 instructions identical (masked); "
                                                 "STALL_PROGRESS_SITE's bl target"),
    "MSG_PUMP": dict(value=0x08081FA0, evidence="1.6.1 0x080816C0: 48 instructions identical (masked); "
                                               "STALL_CHECK_SITE's bl target"),
    "SYSTICK_HANDLER": dict(value=0x0804BD40, evidence="same address; 48 instructions identical (masked)"),
    "HAL_INCTICK": dict(value=0x08044280, evidence="same address; 48 instructions identical (masked)"),
    # ---- RAM the black box reads (literal counts 1.6.1 / 1.6.8, same address unless noted) ----
    "UWTICK": dict(value=0x2401EDB8, evidence="literal count 2 / 2"),
    "M7_HEAP_START": dict(value=0x2401EDC0, evidence="literal count 8 / 8 (end of .bss, test_slice runs the reset handler)"),
    "M7_HEAP_FREELIST": dict(value=0x24000178, evidence="literal count 2 / 2"),
    "M7_HEAP_END": dict(value=0x24048DA0, evidence="literal count 6 / 6; M7_HEAP_END_LITS hold it"),
    "M7_STACK_TOP": dict(value=0x2404CDA0, evidence="literal count 6 / 6; the M7 vector table's SP (check_slice_assumptions)"),
    "DTCM_USED": dict(value=0x2401B0B8, evidence="literal count 2 / 2"),
    "AXI_USED": dict(value=0x24000448, evidence="literal count 2 / 2"),
    "SHARED_POOL_LEFT": dict(value=0x2400016C, evidence="literal count 2 / 2"),
    "LINK_REBOOTS": dict(value=0x24000450, evidence="literal count 1 / 1"),
    "CTRL_LINK": dict(value=0x2401D830, evidence="literal count 7 / 7; its users access +0x140..+0x1A6 identically (offs3.py)"),
    "SPI1_HANDLE": dict(value=0x2401E1B8, evidence="literal count 7 / 7; its 7 users identical for 500+ instructions (masked)"),
    "PASS_TIMER": dict(value=0x2401D678, evidence="literal count 1 / 1; its one user 0x08057CA0 identical (masked)"),
    "AUDIO_BLOCK_STATS": dict(value=0x2401D9D8, evidence="literal count 1 / 1; the main audio loop accesses the same "
                                                         "fields in the same order (offs5.py)"),
    "AUDIO_GAP_STATS": dict(value=0x2401D9F8, evidence="literal count 2 / 2; same loop, same fields and order"),
    "POOL_LEFT_RAM": dict(value=0x24000160, evidence="literal count 3 / 3 (POOL_LEFT_LITS; check_region_assumptions)"),
    "POOL_CUR_RAM": dict(value=0x24000164, evidence="literal count 1 / 1 (POOL_CUR_LITS; check_region_assumptions)"),
    "M4_POOL_CUR": dict(value=0x30000050, evidence="literal count 1 / 1"),
    "M4_POOL_USED": dict(value=0x30001118, evidence="literal count 1 / 1"),
    "M4_TICK_MS": dict(value=0x3001FE2C, evidence="literal count 2 / 2"),
    "M4_WD_COUNTERS": dict(value=0x3000F630, evidence="moved +0xC from 1.6.1 0x3000F624, literal count 23 / 23; "
                                                     "M4_WD_SUPERVISOR loads it"),
    "UI_BUMP_CUR": dict(value=0x3000004C, evidence="literal count 2 / 2; UI_BUMP_ALLOC's cursor"),
    "UI_BUMP_END": dict(value=0x30000048, evidence="literal count 2 / 2; UI_BUMP_ALLOC's limit"),
    "XHEAP_CUR": dict(value=0x30000044, evidence="literal count 2 / 2"),
    "XHEAP_FLOOR": dict(value=0x30000040, evidence="literal count 1 / 1"),
    # ---- the ext pool checks (check_region_assumptions re-verifies all of them on every build) ----
    "POOL_INIT_FLASH": dict(value=0x080CFBEC, evidence=".data image word pair 0x05000000, 0xC1000000 (POOL_*_INIT)"),
    "POOL_LEFT_INIT": dict(value=0x05000000, evidence="read at POOL_INIT_FLASH"),
    "POOL_CUR_INIT": dict(value=0xC1000000, evidence="read at POOL_INIT_FLASH + 4"),
    "POOL_LEFT_LITS": dict(value=(0x080A0510, 0x080A0588, 0x080A0838),
                           evidence="every literal of POOL_LEFT_RAM in the image (0x080A0510, 0x080A0588, 0x080A0838)"),
    "POOL_CUR_LITS": dict(value=(0x080A083C,), evidence="every literal of POOL_CUR_RAM in the image"),
    "M7_HEAP_END_LITS": dict(value=(0x08040588, 0x080A437C, 0x080A4634),
                             evidence="each holds M7_HEAP_END 0x24048DA0 (the reset handler's and the two heap checks')"),
    "ALLOC_BUMP_LEN": dict(value=24, evidence="ALLOC_BUMP_SHA covers 0x080A0740..0x080A0758 (its review)"),
    # ---- our placement in the M7 zero gap ----
    "M7_TRAMP": dict(value=0x080DA000, evidence="0x080D9000..0x080E0000 is all zero in both images; the kit's writes there are ours"),
    "M7_CODE": dict(value=0x080DA400, evidence="as M7_TRAMP"),
    "M7_LIMIT": dict(value=0x080DF000, evidence="as M7_TRAMP"),
    "CTRL_INPUT": dict(value=0x080D9000, evidence="as M7_TRAMP (hold shortcuts only)"),
    "CTRL_STARTUP": dict(value=0x080D9800, evidence="as M7_TRAMP (hold shortcuts only)"),
    # ---- stock fields the black box reads (also in expected_values.py STOCK_168, with the instructions) ----
    "MIDI_RING_RD": dict(value=0x2340, evidence="M4 reader add.w r0,sl,#0x2340 at 0x081053D0; M7 ctor add.w r7,r4,#0x2340 "
                                               "at 0x08082FF6; 7 M4 / 1 M7 add.w #0x2340, as 1.6.1"),
    "MIDI_RING_WR": dict(value=0x2360, evidence="H_RING_SITE: add.w ip,r0,#0x2000; ldr.w r3,[ip,#0x360]; movw r0,#0x2360 at 0x08082F4E"),
    "LOAD_RING_VALUES": dict(value=0x8, evidence="load ring code 0x08056EC0 = 1.6.1 0x08056F00 for 120 instructions (masked)"),
    "LOAD_RING_IDX": dict(value=0x408, evidence="as LOAD_RING_VALUES"),
    "LOAD_RING_START": dict(value=0x414, evidence="as LOAD_RING_VALUES; ldr.w r1,[r0,#0x414] at 0x08056EC2"),
    "LINK_TX": dict(value=0x190, evidence="CTRL_LINK users: ldr/str [rX,#0x190] once each, as 1.6.1 (offs3.py)"),
    "LINK_RX": dict(value=0x194, evidence="CTRL_LINK users: str [rX,#0x194], as 1.6.1"),
    "LINK_BAD": dict(value=0x19C, evidence="CTRL_LINK users: str [rX,#0x19c], as 1.6.1"),
    "LINK_VERSION": dict(value=0x1A2, evidence="CTRL_LINK users: ldrh [rX,#0x1a2], as 1.6.1"),
    "SPI_STATE": dict(value=0x81, evidence="HAL SPI State byte: M7 ldrb.w #0x81 x11 / strb.w x14 in both; SPI1_HANDLE users identical"),
    "PASS_TIMER_LAST": dict(value=0x4, evidence="PASS_TIMER's user 0x08057CA0 = 1.6.1 0x08057CC0 for 77 instructions (masked)"),
    "AUDIO_BLOCK_WRONG_HALF": dict(value=0x4, evidence="main audio loop 0x0808E9F6 (block stats in sb): ldr/str [sb,#4] 0x0808ED8A/90, "
                                                       "the same accesses in the same order as 1.6.1 0x0808DF12 (r8)"),
    "AUDIO_BLOCK_HEAD": dict(value=0xC, evidence="same loop: ldr [sb,#0xc] 0x0808EBFA, str 0x0808ED44"),
    "AUDIO_BLOCK_HEAD_WRONG": dict(value=0x18, evidence="same loop: ldr/str [sb,#0x18] 0x0808ED98/0x0808EDA2"),
    "AUDIO_GAP_MAX": dict(value=0x8, evidence="same loop, gap stats: ldr/str [r1,#8] 0x0808EBC4/CA"),
    "AUDIO_GAP_UNDER": dict(value=0xC, evidence="same loop: [r2,#0xc] 0x0808EBD6/DA"),
    "AUDIO_GAP_OVER": dict(value=0x10, evidence="same loop: [r2,#0x10] 0x0808EDB2/B6"),
    "AUDIO_GAP_TIMEOUTS": dict(value=0x14, evidence="same loop: [r2,#0x14] 0x0808EDE6/EA"),
    "AUDIO_GAP_POLLS": dict(value=0x18, evidence="same loop: [r3,#0x18] 0x0808ED60/68"),
    "AUDIO_GAP_WAITS": dict(value=0x1C, evidence="same loop: [r3,#0x1c] 0x0808ED5A/5E"),
    "M4_STACK_BOTTOM": dict(value=0x300396A8, evidence="the M4 linker table at 0x08100510: 0x3003AAA8 (the vector SP), "
                                                    "0x300396A8 (= SP - 0x1400, as 1.6.1 0x30037CF0), 0x3003AAA8, 0x3003AAA8"),
    # ---- the M4 MPU (test_slice: the M4 reaches our RAM, Device on 1.6.8) ----
    "M4_MPU_SETUP": dict(value=0x081150E0, evidence="1.6.1 0x08114D74: 120 instructions identical (masked); R0 base "
                                                   "literal 0x24040000 size 0x11 (256 KB) TEX0 C0 B1 AP3 (Device); "
                                                   "test_slice M4Mpu runs it"),

    # ======== S3 (2026-11-07): M7 sequencer hooks used by poly cap, retrig (+ curve players), conditions engine,
    # Note Mod engine, and the stock code the S3 suites run. ========
    # ---- poly cap: the five bl msg_send sites (h_note stubs) ----
    "POLYCAP_SEQ_ON_SITE": dict(value=0x0805FCCC, orig="05f028ff",
        evidence="bl MSG_SEND 0x08065B20 in note_route 0x0805FC80 (+0x4C as 1.6.1); track r4 (mov r4,r0 0x0805FC8C), "
                 "queue r0 = [sp+0x5c]+0x7c8, event sp+0x20 {0x5b,timing,port [r4+0x31],ch [r4+0x32],note,vel}; " + _SEQ),
    "POLYCAP_PAD_ON_SITE": dict(value=0x080602F0, orig="05f016fc",
        evidence="bl MSG_SEND in pad Note On 0x080602A0 (1.6.1 0x0805FF00, +0x50 both): track r4 (mov r4,r0 0x080602AA), "
                 "note r5 = ev[+0xc]; function identical (offcmp 0x140 B; the matcher's 'changed' was wrong). NOTE: 1.6.8's "
                 "pad router clamps pad notes to 0..163 (0x080A25E0 cmp #0xa3; 1.6.1 0x080A1E40 usat #7), so this site can "
                 "post notes > 127, which the M4 now drops; S3: on 1.6.8 h_note passes them through untracked "
                 "(BE_NOTES_ABOVE_127, build.py BASE_SETTINGS)"),
    "POLYCAP_SEQ_OFF_SITE": dict(value=0x080601E4, orig="05f09cfc",
        evidence="bl MSG_SEND in sequencer Note Off 0x080601A0 (+0x44 as 1.6.1): track r4 (mov r4,r0 0x080601AC), "
                 "event {0x5c,timing r7,...,note r8}; " + _SEQ),
    "POLYCAP_PAD_OFF_SITE": dict(value=0x08060428, orig="05f07afb",
        evidence="bl MSG_SEND in pad Note Off 0x080603E0 (1.6.1 0x08060040, +0x48 both): track r4 (mov r4,r0 0x080603E8); "
                 "identical (offcmp 0x120 B)"),
    "POLYCAP_ALL_OFF_SITE": dict(value=0x08061D86, orig="03f0cbfe",
        evidence="bl MSG_SEND in stop / all-off 0x08061D00 (+0x86 as 1.6.1 0x080618E0): track r6 (mov r6,r0 0x08061D0A), "
                 "queue [sp+4] = router+0x7c8; function identical (offcmp 0x184 B)"),
    "MSG_SEND": dict(value=0x08065B20,
        evidence="msg_send (H_SEND_SITE's function, S2): 0x08065AC0..0x08065BC0 identical to 1.6.1 0x080655A0 (offcmp); "
                 "queue count +0x600, 24-byte entries; 146 callers (1.6.1 137): the movs #0x5b (MIDI Note On) posts are 4 "
                 "in both images and map 1:1 (0x0805CD9C, 0x0805D6D4, 0x0805FCCC, 0x080602F0)"),
    # ---- retrig accept / emit (also curve start / emit, nm_on, nm_block, mod_block) ----
    "NOTE_ROUTE": dict(value=0x0805FC80,
        evidence="note_route(track r0, note r1, vel r2, timing r3, [sp] pad, [sp+4] router): identical to 1.6.1 0x0805F8E0 "
                 "(offcmp 0x160 B); single caller RETRIG_ACCEPT_SITE in both images; reads track+0x31/+0x32 (port/ch), "
                 "+0x10 (inst key), +0x86D1/+0x86D2 ([r5,#0x6d1/2], r5=track+0x8000), +0x86D4 (0x0805FCFC), pad keys "
                 "+0x86DC+4*pad (movw #0x21b6 0x0805FD40)"),
    "RETRIG_ACCEPT_SITE": dict(value=0x08060FBA, orig="fef761fe",
        evidence="bl NOTE_ROUTE 0x0805FC80 in the processor (+0x37A as 1.6.1 0x08060B9A); args r0=r8 track, r1=r4 note, "
                 "r2=fp vel, r3=sl offset (TICK_TO_OFFSET clamped >=0, 0x08060FA0/A6), [sp]=pad ldrh [sp,#0x2a], [sp+4]="
                 "router [sp,#0x80]; clock in r6 (mov r6,r1 0x08060C54, mov r0,r6 0x08060F9E): the stub's caller-r6 read; "
                 "caller frame: note record at sp+0x28 (type +0, prob +0x29, pad +0x2a, length +0x30 read 0x08060FC2, "
                 "note +0x38, vel +0x3a, word +0x3c), tick64 at sp+0x20 (0x08060D66/6C); " + _PROC),
    "RETRIG_EMIT_SITE": dict(value=0x08060C40, orig="2de9f04f",
        evidence="processor entry push.w {r4-r11,lr} (the replayed prologue), sub sp,#0x5c; args r0 track (r8), r1 clock "
                 "(r6), r2 start (r5), r3 len (r7), 5th = router at entry sp+0 (read [sp,#0x80] 0x08060DF4/E60/FA4: the "
                 "trampoline's ldr r12,[sp,#32]); one caller 0x08060588 (1.6.1 0x080601E8); " + _PROC),
    "TICK_TO_OFFSET": dict(value=0x080A29A0,
        evidence="(clock, &tick64) -> sample offset; tick math block 0x080A2500..0x080A2A20 (1.6.1 0x080A1D60, +0x7A0): "
                 "TICKS_PER_BLOCK, TICK64_ADD/_TO_OFFSET/_COPY/_MAKE identical (offcmp; the block's only diffs are at "
                 "+0xE2 (a note clamp, not ours) and +0x3A0 (key immediates 0x163 -> 0x161)); 37 callers in both"),
    "BUS_POST": dict(value=0x0809F8A0,
        evidence="bus_post(bus, ev): 0x0809F800..0x0809FE00 identical to 1.6.1 0x0809EC00 (offcmp 0x600 B; one moved "
                 "literal); ldr.w r2,[r0,#0x508] key count, keys at +8+8i, mailboxes +0x604 apart (as test_notemod_route's "
                 "rig); internal curve playback posts {0x0E,0,key,note,value} through it; voice engine 0x08094B00.. "
                 "(incl. its 0x0E case, 1.6.1 0x080945E0) identical"),
    # ---- Note Mod engine ----
    "NM_NOTE_OFF": dict(value=0x080601A0,
        evidence="sequencer Note Off(track r0, note r1, timing r2, pad r3, [sp] router (entry sp+0, read [sp,#0x38] "
                 "0x080601C6)): identical to 1.6.1 0x0805FE00 (offcmp 0x100 B); exactly two callers in both images, the "
                 "two NM_OFF sites (no new unhooked Note Off caller in 1.6.8)"),
    "NM_OFF_SITE_1": dict(value=0x08060976, orig="fff713fc", note="bl sequencer Note Off in the pending note-off pass of FUN_08060500",
        evidence="bl NM_NOTE_OFF in the pending pass (1.6.1 0x080605D2): r0=r5 track, r1=[r4-4] note, r3=[r4-6] pad, "
                 "[sp]=[sp+0x14] router, as 1.6.1. Changed (release note 'note-offs land at the right time'): timing r2 = "
                 "off - clock_lo clamped >= 0 (subs r2,r2,r0 0x08060960; bic r2,r2,r2,asr#31 0x0806096A); 1.6.1 passed "
                 "(clock + ticks per block) - off. nm_off rewrites only a[1] note and a[3] pad, so a shifted note's off "
                 "keeps stock's corrected timing and goes to the mapped pad / pitch"),
    "NM_OFF_SITE_2": dict(value=0x08060E6C, orig="fff798f9",
        evidence="bl NM_NOTE_OFF in the processor (1.6.1 0x08060A4C): ends a pending note with the same note [sp+0x38] and "
                 "pad [sp+0x2a] before a new one; r0=r8, r1=r4 note, r2=r5 start, r3=ip pad, [sp]=router; " + _PROC),
    "NM_ALL_OFF": dict(value=0x08061D00, orig="2de9f04f",
        evidence="stop / all-notes-off(track r0, timing r1 ([sp]), router r2 (r8)); push.w {r4-r11,lr} replayed; identical "
                 "to 1.6.1 0x080618E0 (offcmp 0x184 B); 6 callers in both (0x080607F6, 0x08060ACA, 0x0806279E, 0x080627D0, "
                 "0x08062B86, 0x08062B94); the new 1.6.8 caller of FUN_08061E80 (0x08060AEC, play count wrap) posts only "
                 "event 0x5A and ends no note"),
    # ---- conditions engine (M7) ----
    "COND_GATE_SITE": dict(value=0x08060F84, orig="9df82930",
        evidence="ldrb.w r3,[sp,#0x29] (the replayed load) in the processor; then subs #1; cmp #0x62; bls roll (as 1.6.1); "
                 "seq = r8 (track), note word at sp+0x3c (record +0x14), as the trampoline reads them; " + _PROC),
    "COND_SKIP": dict(value=0x08060E02,
        evidence="processor's next-note path: ldr.w r0,[sb,#0x6c0]; b 0x08060D82 (index +1); stock's own branches to it "
                 "at 0x080610A4, 0x080610C2, 0x080610E0, 0x08061160 (as 1.6.1, +0x420); " + _PROC),
    "COND_LOOP_SITE": dict(value=0x08061B60, orig="2de9f043",
        evidence="loop boundary function (r0 = track, r1 clock, r2, r3 router): push.w {r4-r9,lr} replayed; identical to "
                 "1.6.1 0x08061740 (offcmp 0x1A0 B); 2 callers (0x0806072C, 0x0806093A) as 1.6.1. Caller logic in "
                 "FUN_08060500 equals 1.6.1's (restart: count+1 == 0; wrap at the clip's loop count; +0x30 flag keeps "
                 "counting) except one new case: with the +0x30 flag, a count past 0xFF now calls FUN_08061E80 and this "
                 "function (a boundary). cond_loop reads track+0x14 (the counter, str [r5,#0x14]) and +0x0C / +0x86C0"),
    # ---- poly AT (M7 side; the group needs the M4 handlers, S5) ----
    "POLY_SITE": dict(value=0x08062986, orig="7ff40bae",
        evidence="External handler: cmp r3,#2 (0x08062980); cmp r3,#3 (0x08062984); bne.w 0x080625A0 (the replayed "
                 "branch); type 3 -> pad Note Off(r6 track, sp+0x20 event, r8) 0x08062990; 0x080622E0..0x080629E0 identical "
                 "to 1.6.1 0x08061DC0 (offcmp 0x700 B: 25 moved branches, 53 jump-table words shifted with the code)"),
    "POLY_RESUME": dict(value=0x080625A0,
        evidence="track message loop head: add r2,sp,#0x20; mov r1,sl; ldr r0,[sp,#4]; bl queue read 0x08065AC0 (as 1.6.1 "
                 "0x08062080); POLY_SITE's target"),
    "POLY_DISPATCH": dict(value=0x08062984, evidence="cmp r3,#3 right before POLY_SITE (test entry)"),
    # ---- stock code the S3 suites run (test_notemod_route, test_polyat, test_retrig) ----
    "TRACK_CTOR": dict(value=0x08062FA0, evidence="identical to 1.6.1 0x08062A80 (offcmp 0x200 B)"),
    "TRACK_MSG_LOOP": dict(value=0x080625A0, evidence="= POLY_RESUME"),
    "TRACK_MSG_LOOP_END": dict(value=0x08062700,
        evidence="queue-empty exit of the loop (beq.w at 0x080625AC): ldrb.w r3,[r6,#0x20], as 1.6.1 0x080621E0"),
    "SEQ_PLAY_PASS": dict(value=0x08060834,
        evidence="pending note-off pass in FUN_08060500 (1.6.1 0x08060494): ldr.w r2,[r5,#0x238] (count), entries at "
                 "track+0x38 stride 0x10 (active +0, pad +2, note +4, off tick +8), clock [sp+0x10] (ldrd [sl]), ticks per "
                 "block r8, router [sp+0x14]; the same registers as 1.6.1; only the timing (NM_OFF_SITE_1) changed; the "
                 "function grew 37 instructions (play count) outside this pass"),
    "SEQ_PLAY_PASS_END": dict(value=0x080608A6,
        evidence="add sp,#0x64 (the pass's exit; the compaction path at 0x080609E0 returns here), as 1.6.1 0x0806050A"),
    "SAMPLER_PROCESS": dict(value=0x0808F4A0,
        evidence="sampler message loop (engine r0, router r1): the matcher's 'changed / 11 tokens' was its 0xA1-entry "
                 "jump table decoded as code; offcmp of 0x1200 B vs 1.6.1 0x0808E9A0: 0 instruction diffs, the table "
                 "identical relative to the entry, one moved literal; note-on case 0x12 at 0x08090591 calls VOICE_START "
                 "with note r3=[sp+0x34]"),
    "SAMPLER_NOTE_START": dict(value=0x0808F7A0,
        evidence="the loop's queue-empty exit (beq.w at 0x0808F4EC): ldrb.w r3,[r4,#0x63d], as 1.6.1 0x0808ECA0"),
    "VOICE_START": dict(value=0x08095260,
        evidence="voice start: 0x08095260..0x08095F60 identical to 1.6.1 0x08094760..0x08095460 (fdiff 1169 / 1169 "
                 "masked instructions; the matcher's token count was off); call site 0x080905B8 as 1.6.1 0x0808FAB8"),
    "TICKS_PER_BLOCK": dict(value=0x080A2500, evidence="tick math block identical (see TICK_TO_OFFSET)"),
    "TICK64_ADD": dict(value=0x080A2960, evidence="tick math block identical (see TICK_TO_OFFSET)"),
    "TICK64_COPY": dict(value=0x080A29C0, evidence="tick math block identical (see TICK_TO_OFFSET)"),
    "TICK64_MAKE": dict(value=0x080A29E0, evidence="tick math block identical (see TICK_TO_OFFSET)"),
    # ---- RAM ----
    "PRODUCER_GLOBAL": dict(value=0x2401BD3C,
        evidence="same address; literal count 22 / 23 (one more reader in 1.6.8); the object it points to is the M7 -> M4 "
                 "ring whose +0x2340 / +0x2360 indexes (MIDI_RING_RD / _WR) and +0x51BC (LOAD_RING) S2 reviewed; m7_mod.c "
                 "reads those two indexes (External mod output, deferred)"),
    # ---- S3 implementer: stock's note clamp, the reason for BE_NOTES_ABOVE_127 (test_s3_m7 runs it on each base) ----
    "PAD_NOTE_CLAMP": dict(value=0x080A25E0,
        note="(&note, delta) -> note + delta clamped: 1.6.8 0..163 (1.6.1 0..127); S3: test_s3_m7 runs it",
        evidence="ldrh r3,[r0]; adds r0,r1,r3; sxth; cmp r0,#0xa3; it ge; movge r0,#0xa3; bic.w r0,r0,r0,asr #31; bx lr "
                 "(1.6.1 0x080A1E40: ldrh; add; sxth; usat r0,#7,r0; bx lr), at the same offset +0xE0 of the tick-math block "
                 "(rc18/impl/fcmp.py: the block's only code diff besides key immediates); callers 0x08085624, 0x08097020, "
                 "0x08097056 (1.6.1: 7 callers, 0x08083A5C .. 0x08096556)"),
    "SEQ_PLAYER": dict(value=0x08060500,
        evidence="sequence player: prologue push.w {r4-r11,lr}; mov r5,r0; mov r0,r3; mov r4,r1; vpush {d8}; sub sp,#0x64 "
                 "identical to 1.6.1 0x08060160; one caller each (0x08062724, 1.6.1 0x08062204); contains SEQ_PLAY_PASS "
                 "(+0x334 both) and calls the loop boundary (0x0806072C, 0x0806093A), the processor (0x08060588), "
                 "SAMPLE_TO_TICK (3 calls); changed: the note-off timing (NM_OFF_SITE_1) and the play-count wrap "
                 "(+37 instructions). Only test_s3_m7 uses it (runs it whole on both bases)"),
    "SAMPLE_TO_TICK": dict(value=0x080A2D80,
        evidence="vmov.f32 s15,#0.5; vldr s12,[r1,#0x14c]; push.w {r4-r9,lr} ... same code as 1.6.1 0x080A25E0 for 20 "
                 "instructions; 3 callers each, all in SEQ_PLAYER (0x080606DE, 0x080607BE, 0x080608F0); test_s3_m7 stubs it"),
    # ---- S3 review fix: the pad Note On / Off functions around two poly cap sites (test_s3_m7 runs them whole) ----
    "PAD_NOTE_ON": dict(value=0x080602A0,
        evidence="push.w {r4-r10,lr}; ldrd r5,r3,[r1,#0xc]; sub sp,#0x50; mov r4,r0 ... bl MSG_SEND at +0x50 "
                 "(POLYCAP_PAD_ON_SITE 0x080602F0), same code as 1.6.1 0x0805FF00 (only the bl targets differ: msg init "
                 "0x080A10C0 / 0x080A04A0, MSG_SEND); note 0 maps the pad (<=7: +0x2C, <=15: +0x1C, else 0x34) as 1.6.1"),
    "PAD_NOTE_OFF": dict(value=0x080603E0,
        evidence="ldrb.w r3,[r0,#0x30]; push.w {r4-r9,lr}; mov r4,r0; sub sp,#0x54 ... bl MSG_SEND at +0x48 "
                 "(POLYCAP_PAD_OFF_SITE 0x08060428), same code as 1.6.1 0x08060040 (bl targets only)"),
}

# Registry keys missing from registry_keys.txt (entries without a title to match by): the measured
# rule (plan: "0x002..0x13A unchanged, 0x13C..0x1D8 move down 2, 0x1D9..0x1F2 down 1"). (lo, hi, delta)
KEY_RANGES = ((0x002, 0x13A, 0), (0x13C, 0x1D8, -2), (0x1D9, 0x1F2, -1))

FACT_NEEDS_REVIEW = {}

REVIEW_OURS = {
    "ACT_STRIKE": "stock's header action ids grew in 1.6.8 (0x1041 -> 0x1044, 0x102B -> 0x102D, ...): "
                  "confirm 0x10F0..0x10F3 are still unused by stock",
    "ACT_ROWS": "with ACT_STRIKE", "ACT_MOD": "with ACT_STRIKE", "ACT_NM": "with ACT_STRIKE",
    "UI_REQUEST": "confirm 0xFE is still no stock UI consumer code (the consumer's table bound is 0xB3 in both)",
    # D2_SLICE, D2_SLICE_END and the RAMCHK blocks: settled in S2 (REVIEWED)
}

# Evidence for entries that stay REVIEW (S1 reviews of the 1.6.8 draft, not yet re-checked by the sprint
# that settles them).
_HDR = ("header root layout grew by 0x18 after the description: HEADER_ROOT_INIT (0x081823D4) uses "
        "#0x564 where 1.6.1 used #0x54c, 0x194..0x1d8 for 0x17c..0x1c0, 0xff4..0xffc for 0xfdc..0xfe4; "
        "HEADER_BUILD's UI object +0x6B -> +0x83 (S1 review): measure in S5")
_DESC = ("description grew 0x1C4 -> 0x1DC: HEADER_GEN copies 8 B -> 0x20 B somewhere, +0x196/+0x1B2 -> "
         "+0x1AE/+0x1CA, template page byte +0x1C2 -> +0x1DA; where the action records moved is not known (S5)")
HINTS = {
    "HEADER_APPLIED": _HDR, "HEADER_BUTTONS": _HDR + "; candidate 0x564", "HEADER_ARMED": _HDR + "; candidate 0x1194",
    "HEADER_BUTTON_STRIDE": _HDR, "HEADER_BUTTON_HIDDEN": _HDR, "HEADER_BUTTON_LABEL": _HDR, "HEADER_BUTTON_COUNT": _HDR,
    "HEADER_DESC_ACTIONS": _DESC, "HEADER_ACTIONS": _DESC, "HEADER_ACTION_SIZE": _DESC, "HEADER_ACTION_EMPTY": _DESC,
    "HEADER_ACTION_LIT": _DESC, "HEADER_ACTION_GROUP": _DESC, "HEADER_ACTION_ID": _DESC,
    "EV_MIXER_PAD_MODE": "MIXER_EVENT's range went 0x108-0x10B -> 0x109-0x10D (S1 review)",
    # (S2 settled H_INPUT_SITE, the stall / M4 watchdog sites, ALLOC_BUMP_SHA, MPU_SETUP, LOAD_RING,
    # AUDIO_DMA_EVENTS, PANEL_* and D2_SLICE: REVIEWED)
    "MODEL_MIXER_MODE": "S1 review: measured 0x45CC (+0x950) from MIXER_PAD_MODE and NAVIGATE",
    "MODEL_WORK": "S1 review: probably 0x45D0", "MODEL_PAD_LEDS": "S1 review: deltas are not uniform; measure",
    "MODEL_PAGE": "S1 review: NAVIGATE shows no change at 0x12C..0x148", "MODEL_PREV_PAGE": "as MODEL_PAGE",
    "MODEL_CTX": "as MODEL_PAGE", "MODEL_SEQ": "as MODEL_PAGE", "MODEL_SCENE": "as MODEL_PAGE",
    "UI_WALK_CTX": "S1 review: the inference is weak (the neighbouring literals are odd values)",
}
