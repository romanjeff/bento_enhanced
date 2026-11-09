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

# S6a: the evidence most of the Analog M7 helpers share
_AF = "masked compare vs 1.6.1 (rc20/analog afcmp.py, rc20/s6a ent.py): 0 differences"

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
    # ======== S5 (2026-11-07, investigator rc19/s5parts): poly_at's M4 side, inta_live, cond_ui, Play Count
    # ---- poly_at: the M4 MIDI-out task (1.6.8 recompiled it: new register map, a note > 127 guard) ----
    "M4_NOTE_ON_CASE": dict(value=0x08105242,
        note="MIDI task: the Note On case (MIDI_JT[0]); our 0x5F / 0x60 handlers copy it (build.py note_on_case)",
        evidence="MIDI_JT[0] = 0x08105243 and the 0x5B guard's ble.w 0x08105242 (0x08105412). The case: add r5,sp,#4; "
                 "BEGIN 0x08105F1E; movs r1,#0x90; STATUS 0x08105E0C; uxtb.w r1,sb CHANNEL 0x08105E26; uxtb r1,r6 DATA "
                 "0x08105EC6; uxtb r1,r4 DATA; ldr r4,[pc,#0x210] (literal 0x08105480 = 0x30001120 = M4MIDI_SINK); uxtb r2,r7; "
                 "SEND 0x08105ACC(r4, r5, port); RESULT 0x08105E14; DONE 0x08105D3C(r4, result); b 0x08105416 at 0x08105286. "
                 "Registers per event word (loop head 0x08105380..0x081053CC): type r5 = byte +0, port r7 = +8, channel sb = "
                 "+0xC, note r6 = +0x10, value r4 = ldrh +0x14 (1.6.1: r4 / sb / r8 / r7 / fp, sink r6 from the prologue, "
                 "msg r5 set once). The matcher's 0x08105244 skips the case's own add r5,sp,#4"),
    "M4_NOTE_ON_END": dict(value=0x08105286,
        evidence="the Note On case's b 0x08105416 (M4_LOOP) after DONE; 1.6.1's case falls into its loop head (0x081050DE)"),
    "M4_LOOP": dict(value=0x08105416,
        evidence="loop head ldr.w sl,[r8]; add r3,sl,#0x2000; ldr [r3,#0x360] / [r3,#0x340]; cmp; bne 0x08105380 (read the "
                 "next event) else FLUSH 0x08105C40 and return; the prologue's b 0x08105416 (0x08105240), every case's b back, "
                 "MIDI_JT[4] = MIDI_JT[5] = 0x08105417 (0x5F / 0x60 unused by stock); bhi 0x08105416 bound at 0x081053E4"),
    "MIDI_JT": dict(value=0x081053EC,
        evidence="cmp r5,#0x5b; beq 0x08105410 (note <= 127 -> case) / cmp r5,#0x5c; beq 0x08105436 (note <= 127 -> Note "
                 "Off) at 0x081053D8..DE (new in 1.6.8: notes above 127 dropped); subs r5,#0x5b; cmp r5,#8; bhi loop; adr r3,#4 "
                 "(0x081053E6); ldr.w pc,[r3,r5,lsl #2] (0x081053E8). Words: 0x08105243 0x0810543B 0x08105289 0x081052C7 "
                 "0x08105417 0x08105417 0x08105365 0x0810530D 0x08105339 (on, off, bend, CC, -, -, clock, start, stop)"),
    "MIDI_JT_FIRST": dict(value=0x5B, evidence="subs r5,#0x5b at 0x081053E0; cmp r5,#8 (0x081053E2) bounds the 9 words"),
    "M4MIDI_SINK": dict(value=0x30001120,
        evidence="every 1.6.8 case loads it from literal 0x08105480 (ldr at 0x0810526C, 0x081052AA, 0x081052F0, 0x0810531C, "
                 "0x08105348, 0x08105378, 0x08105464) and FLUSH gets it (0x0810542A); 1.6.1: ldr r6,[pc,#0x220] in the "
                 "prologue (0x08105096, literal 0x081052B8)"),
    # ---- poly_at test rig (test_polyat M4MidiTask runs stock's task on each base) ----
    "M4_MIDI_TASK": dict(value=0x08105214,
        evidence="ldrb r3,[r0,#1]; cbnz; bx lr; push.w {r4-sl,lr}; sub sp,#0x30 ... (1.6.1 0x08105068: push.w {r4-fp,lr}; "
                 "sub sp,#0x34); one caller, 0x08105488 (push {r3,lr}; ldr r0,[pc,#8] = 0x3000111C; bl)"),
    "M4_MIDI_TASK_ARG": dict(value=0x3000111C, evidence="literal 0x08105494 of the task's only caller 0x08105488 (1.6.1: literal 0x081052CC of 0x081052C0, the same value)"),
    "M4MIDI_RING_PTR": dict(value=0x3000F65C,
        evidence="the task's ldr.w r8,[pc,#0x244] at 0x0810523C (literal 0x08105484); the loop reads [r8] + 0x2340 / 0x2360 "
                 "(MIDI_RING_RD / _WR) and entries +0x21C0 + 24 x (rd & 0xF); 1.6.1: ldr.w sl,[pc,#0x228] (0x08105090) = 0x3000F650"),
    "M4MIDI_FLUSH": dict(value=0x08105C40,
        evidence="push.w {r4-r8,lr}; sub sp,#0x18; add.w r3,r0,#0xd000; ldr.w r2,[r3,#0x420] ... same code as 1.6.1 "
                 "0x08105A78 (only the bl target 0x08105848 / 0x08105680 differs); called at 0x0810542C with M4MIDI_SINK"),
    # ---- inta_live: the pad router's pressure post ----
    "LIVE_SITE": dict(value=0x080865EA, orig="19f059f9",
        evidence="bl BUS_POST 0x0809F8A0 in the pad router 0x08085FC0 (1.6.1 0x08085540; push.w {r4-sl,lr}; mov r6,r1 bus; "
                 "mov r5,r0 router, kept), case 0xAE (tbh 0x08086000 -> 0x0808658E; 1.6.1 0x08085B0E): pad sb = [sp,#0x14] "
                 "<= 15, value r7 = min(raw, 0xD000) x 127 / 0xD000; ev at sp+0x38 = {0x0E, 0, key, note, value}; idx = "
                 "[r5,#0x308], key = [r5 + 4 idx + 0x30C]; drum (key 0xFFFFFF00) -> 0x08087398: key = [r5 + 4 (pad + 16 idx + "
                 "0x42)], b.w 0x080865E4 (the only branch to the post block); melodic: note = [r5 + 4 pad + 0x34]; both: add "
                 "r1,sp,#0x38; mov r0,r6; str r2,[sp,#0x40]; bl BUS_POST (the site); b loop. Changed: the melodic note is the "
                 "pad's Note On note (new router table +0x34[16], written at 0x08085352 by the press path, 0..163 through "
                 "PAD_NOTE_CLAMP) where 1.6.1 re-derived it (key map + usat #7, 0x08086290..0x080862BC); the table is why "
                 "every later router field moved +0x40. The only 0x0E post in the router (movs r2,#0xe at 0x080865BC)"),
    # ---- cond_ui: probability editor clamps ----
    "PROB_SHOW_PIANO_SITE": dict(value=0x0815B8F2, orig="013b3360",
        evidence="piano roll value display 0x0815B730 (1.6.1 0x081599BC): ldrb.w r3,[sp,#0x49]; cbnz r3 -> site; movs r3,#0x63; "
                 "str r3,[r6]; b; site: subs r3,#1; str r3,[r6]; b 0x0815B88A, the same bytes as 1.6.1 0x08159B74..0x08159B86. "
                 "The function differs only in registry key immediates (-2: 0x17F/0x181/0x182/0x186 -> 0x17D/0x17F/0x180/0x184; "
                 "0x1DF -> 0x1DE, now a 4-byte cmp.w, hence site +0x1C4 -> +0x1C2) and the MODEL literal 0x30025300; its tbb "
                 "table bytes are equal (mcmp.py)"),
    "PROB_PIANO_EDIT_SITE": dict(value=0x0815A7C0, orig="9df81930",
        evidence="piano roll edit 0x0815A63C (1.6.1 0x081588F8), site at +0x184 in both; masked compare of 0x5F8 B: 550 / 550 "
                 "instructions, one code diff, ldr r1,[sp,#0x1c] -> [sp,#0x34] at 0x0815A92A (1.6.1 0x08158BE6), in the "
                 "960-tick grid path after BACK, not touching sp+0x19 / 0x84 / 0x31; the replaced range is byte-identical "
                 "(PROB_PIANO_EDIT_SHA exact): ldrb.w r3,[sp,#0x19]; ... ldr r2,[sp,#0x84] ... strb.w [sp,#0x31]"),
    "PROB_PIANO_EDIT_BACK": dict(value=0x0815A7F0,
        evidence="cmp.w sl,#0 after the strbhi / strbls pair; the cbz r3 at 0x0815A7BE skips to it (1.6.1 0x08158A7A -> "
                 "0x08158AAC); +0x1B4 in both"),
    # ---- tests only (test_conditions: our build leaves these stock) ----
    "T2_LIST_SITE_1": dict(value=0x08167E0C,
        evidence="literal 0x081A21D4 = the probability label list ('1%%'..'99%%', 'Always', then 'Default'), loaded by ldr "
                 "r3,[pc,#0x270] at 0x08167B98 for the registration of key 0x182 (bl 0x0816576C, r1 = #0x182); 1.6.1: "
                 "0x08165F8C = 0x0819F30C, loaded at 0x08165D44 for key 0x184"),
    "T2_LIST_SITE_2": dict(value=0x08167B94,
        evidence="str.w r8,[sp] with r8 = #0x64 (100 entries) before that registration (mov.w r8,#0x64 at 0x08167B90); 1.6.1 "
                 "0x08165D40, the same instructions with key 0x184"),
    # ---- cond_engine: Play Count (Jeff's default: 1.6.8's 256-step wrap is not a pass) ----
    "COND_LOOP_CALL_1": dict(value=0x0806072C, orig="01f018fa",
        evidence="the player's first bl COND_LOOP_SITE (0x08061B60; 1.6.1 0x08060384 'bl 0x08061740'). Reached from "
                 "0x08060724 at a restart (count+1 == 0), at a wrap with track byte +0x30 clear, and (new) from 0x08060AE6 "
                 "after bl 0x08061E80 when +0x30 is set and count+1 > 0xFF (0x080609C0 / 0x08060AE0 cmp r3,#0xff). With +0x30 "
                 "set, its only other call is the restart. The second caller 0x0806093A has no +0x30 test, as 1.6.1 0x0806059A"),
    # ======== S4+S5 (2026-11-07): step, hold (and the shared helpers they call). Reviews from the investigators
    # rc19/stepui (header, views, keys, pages), rc19/stepdata (be_step_v1 save / load, Note Mod plumbing) and
    # rc19/hold (hold shortcuts, CTRL consumer); entries more than one of them re-derived independently carry
    # each one's evidence (they agree on every value). Implementer's own spot checks: rc19/impl/bdis.py.
    'HEADER_GEN': dict(value=0x8135d74, orig='2de9f043', evidence="header description generator (model r0, out r1, &flag r2, r3): push.w {r4-r9,lr}; sub sp,#0xc (1.6.1 push.w {r4-r10,lr}; sub sp,#8: same 0x28-byte frame, sl no longer used, args r2/r3 -> sb/r8); body = 1.6.1's with model fields +0xB19C -> +0xC2F0 etc. and the description grown 0x1C4 -> 0x1DC (HEADER_GEN 0x08135D74 (1.6.1 0x08134800): builds the description at model+0x4338 (1.6.1 +0x3A00): copies 0x20 from +0x4514/+0x4534/+0x4554 and now 0x20 (1.6.1: 8) from +0x4574 into desc +0/+0x20/+0x40/+0x60, bytes +0x80..+0x82 (1.6.1 +0x68..+0x6A), text fields +0x1AE/+0x1CA (1.6.1 +0x196/+0x1B2): every field after +0x60 moved +0x18; memcpy(out, desc, 0x1DC) at 0x08135E50); returns r6 as 1.6.1; 3 callers as 1.6.1 (0x081701C4, 0x081820CC, 0x081822E4); the per-tick caller 0x081820AC = 1.6.1 0x0817FE70 (memcpy 0x1DC template, HEADER_GEN, compare with root+0x34, HEADER_BUILD). WRAPPER: replay must be 'push.w {r4, r5, r6, r7, r8, r9, lr}' on 1.6.8"),  # stepui
    'HEADER_DESC_ACTIONS': dict(value=0x8c, evidence="header templates (HEADER_TEMPLATES 0x0819BB40, 0x26 x 0x1DC; tpl.py): every template's action records sit at +0x8C (1.6.1 +0x74), 6 x 0x30, label +0, empty +0x20, lit +0x21, tab group +0x22, id u16 +0x2C; page byte +0x1DA (1.6.1 +0x1C2); same pages, labels and groups as 1.6.1, only ids >= 0x1013 grew (Undo 0x1032 -> 0x1034); HEADER_BUILD 0x08181C7C (1.6.1 0x0817FAF8): action loop r7 = desc+0x8C (add.w r7,r6,#0x8c 0x0818204A), adds r7,#0x30 per record, 6 records (widgets root+0xA44..+0xF24 step 0xD0); per record ldrb [r7,#0x22] group (0x08181E36), vt+0x2C(button, [rec+0x20]) hidden (0x08181E5C), WIDGET_SET_ACTIVE(button, [rec+0x21]) (0x08181E64), vt+0x24(button, ldrh [rec+0x2C]) id (0x08181E1E); applied only while root+0x1000+0x194 is set (ldrb [sl,#0x194] 0x08181E4E, sl = root+0x1000); end: memcpy(root+0x34, desc, 0x1DC) 0x08181F48..52, then if root+0x1194: post 0xCD and clear it (0x08181F56, 0x08182014) -- the same code as 1.6.1 with +0x74 -> +0x8C, 0x117C -> 0x1194"),  # stepui
    'HEADER_ACTIONS': dict(value=0x6, evidence='6 records per template (tpl.py) and per HEADER_BUILD loop (HEADER_BUILD 0x08181C7C (1.6.1 0x0817FAF8): action loop r7 = desc+0x8C (add.w r7,r6,#0x8c 0x0818204A), adds r7,#0x30 per record, 6 records (widgets root+0xA44..+0xF24 step 0xD0); per record ldrb [r7,#0x22] group (0x08181E36), vt+0x2C(button, [rec+0x20]) hidden (0x08181E5C), WIDGET_SET_ACTIVE(button, [rec+0x21]) (0x08181E64), vt+0x24(button, ldrh [rec+0x2C]) id (0x08181E1E); applied only while root+0x1000+0x194 is set (ldrb [sl,#0x194] 0x08181E4E, sl = root+0x1000); end: memcpy(root+0x34, desc, 0x1DC) 0x08181F48..52, then if root+0x1194: post 0xCD and clear it (0x08181F56, 0x08182014) -- the same code as 1.6.1 with +0x74 -> +0x8C, 0x117C -> 0x1194)'),  # stepui
    'HEADER_ACTION_SIZE': dict(value=0x30, evidence="HEADER_BUILD adds r7,#0x30 per record (0x08181E2C); header templates (HEADER_TEMPLATES 0x0819BB40, 0x26 x 0x1DC; tpl.py): every template's action records sit at +0x8C (1.6.1 +0x74), 6 x 0x30, label +0, empty +0x20, lit +0x21, tab group +0x22, id u16 +0x2C; page byte +0x1DA (1.6.1 +0x1C2); same pages, labels and groups as 1.6.1, only ids >= 0x1013 grew (Undo 0x1032 -> 0x1034)"),  # stepui
    'HEADER_ACTION_EMPTY': dict(value=0x20, evidence='ldrb r1,[r6,#0x20] -> button vt+0x2C at 0x08181E5C; templates: 1 on every empty record, 0 on labelled ones (tpl.py)'),  # stepui
    'HEADER_ACTION_LIT': dict(value=0x21, evidence='ldrb r1,[r6,#0x21] -> WIDGET_SET_ACTIVE 0x0816C62E at 0x08181E64; templates: Sequence lit on page 0x17, Step lit on page 0x27'),  # stepui
    'HEADER_ACTION_GROUP': dict(value=0x22, evidence='ldrb r3,[r7,#0x22] at 0x08181E36 selects the tab widget set; templates: 1 on Event/Sequence/Automation/Step, 0 on Clear/Undo'),  # stepui
    'HEADER_ACTION_ID': dict(value=0x2c, evidence='ldrh r1,[r6,#0x2c] -> button vt+0x24 at 0x08181E1E; templates: u16 ids 0x49/0x4A/0x4B tabs, 0x1034 Undo'),  # stepui
    'HEADER_APPLIED': dict(value=0x34, evidence='HEADER_BUILD memcpy(root+0x34, desc, 0x1DC) 0x08181F48..0x08181F52; the per-tick caller compares the new description with root+0x34 (add.w r1,r6,#0x34 0x081820D2)'),  # stepui
    'HEADER_ARMED': dict(value=0x1194, evidence='root+0x1000 (add.w r5,r4,#0x1000 0x08181EF6; sl in the record loop) +0x194: ldrb [sl,#0x194] gates hidden/lit per record (0x08181E4E); after the copy ldrb [r5,#0x194] 0x08181F56 -> post 0xCD, strb 0 0x08182014 (1.6.1 the same at +0x17C)'),  # stepui
    'HEADER_BUTTONS': dict(value=0x564, evidence='HEADER_ROOT_INIT 0x081823D4: 6 x button ctor 0x0816C520 at root+0x564 step 0xD0 to +0xA44 (0x08182576..88), 6 x tab ctor 0x08182950 at +0xA44..+0xF24; 1.6.1 +0x54C/+0xA2C/+0xF0C (+0x18 everywhere after the description copy)'),  # stepui
    'HEADER_BUTTON_STRIDE': dict(value=0xd0, evidence='adds r7,#0xd0 0x08182584 / 0x08182594; HEADER_BUILD adds r5,#0xd0'),  # stepui
    'HEADER_BUTTON_COUNT': dict(value=0xc, evidence='6 buttons + 6 tab buttons (HEADER_ROOT_INIT loops above); the id lookups 0x08182128/6A/AC walk root+0x564..+0xA44 checking [+0x14] and [+0x4F4]'),  # stepui
    'HEADER_BUTTON_HIDDEN': dict(value=0x30, evidence="button widget ctor 0x0816C520 = 1.6.1 0x0816A4D8 (c2.py: 65 insns, 0 hunks), WIDGET_SET_HIDDEN / _ACTIVE / label setter 0x0816C5F8 same code: the widget layout is 1.6.1's (display emulator field; confirm with render_seq_headers on 1.6.8)"),  # stepui
    'HEADER_BUTTON_LABEL': dict(value=0x6d, evidence='as HEADER_BUTTON_HIDDEN (same widget code)'),  # stepui
    'HEADER_BUILD': dict(value=0x8181c7c, evidence='HEADER_BUILD 0x08181C7C (1.6.1 0x0817FAF8): action loop r7 = desc+0x8C (add.w r7,r6,#0x8c 0x0818204A), adds r7,#0x30 per record, 6 records (widgets root+0xA44..+0xF24 step 0xD0); per record ldrb [r7,#0x22] group (0x08181E36), vt+0x2C(button, [rec+0x20]) hidden (0x08181E5C), WIDGET_SET_ACTIVE(button, [rec+0x21]) (0x08181E64), vt+0x24(button, ldrh [rec+0x2C]) id (0x08181E1E); applied only while root+0x1000+0x194 is set (ldrb [sl,#0x194] 0x08181E4E, sl = root+0x1000); end: memcpy(root+0x34, desc, 0x1DC) 0x08181F48..52, then if root+0x1194: post 0xCD and clear it (0x08181F56, 0x08182014) -- the same code as 1.6.1 with +0x74 -> +0x8C, 0x117C -> 0x1194; callers 0x081820E8 / 0x08182300 (1.6.1 0x0817FEAC / 0x081800C4), each right after its bl HEADER_GEN'),  # stepui
    'HEADER_ROOT_INIT': dict(value=0x81823d4, evidence="push.w {r4-r11,lr}; sub sp,#0x5c; stores HEADER_ROOT_VTABLE 0x081B6560 at [r0] (str r3,[r0],#0x34 0x081823E4; the vtable's only literal 0x08182710 is in this function), builds the 12 buttons (HEADER_BUTTONS); same prologue as 1.6.1 0x08180198"),  # stepui
    'HEADER_TPL_SEQ': dict(value=0x8, evidence='template 8: page byte 0x17, StepSeq, Event/Sequence/Automation/Clear/Undo (tpl.py)'),  # stepui
    'HEADER_TPL_SEQ_ALT': dict(value=0x14, evidence='template 20: page byte 0x1A (tpl.py), as 1.6.1'),  # stepui
    'HEADER_TPL_SEQ_DRUM': dict(value=0x24, evidence='template 36: page byte 0x27, StepSeq, Step/Sequence/-/-/Undo (tpl.py)'),  # stepui
    'ACT_STRIKE': dict(value=0x10f0, evidence="our id; stock action ids on the M4 end at 0x104A: every 'sub #0x1000' dispatcher is bounded (0x081420FC: 0x1001..0x1045; 0x08146EBC: 0x1010..0x1049; 0x08151A44: 0x100A..0x101A; 0x08135870: 0x100C..0x100E), the drum / piano event handlers' bound is movw #0x1045 (0x0815F3F4, 0x0815C3AC); M4 immediates 0x10xx: highest action-range value 0x104A, 0x1060/0x1080/0x10A0/0x10E0 are offsets/sizes (0x10E0: add.w only); no u16 0x10F0..0x10F3 in flash data (hits are code / pointer bytes)"),  # stepui
    'ACT_ROWS': dict(value=0x10f1, evidence='as ACT_STRIKE'),  # stepui
    'ACT_MOD': dict(value=0x10f2, evidence='as ACT_STRIKE'),  # stepui
    'ACT_NM': dict(value=0x10f3, evidence='as ACT_STRIKE'),  # stepui
    'MODEL_PAGE': dict(value=0x12c, evidence='NAVIGATE 0x08143D44: ldrb r3,[r4,#0x12c]; strb r3,[r4,#0x13a]; strb r5,[r4,#0x12c] at 0x08143F14..1C (1.6.1 0x081422BC..C4); HEADER_GEN ldrb [r0,#0x12c] cmp #1 (0x08135D8C); 3 strb / 50 ldrb #0x12c on the M4 (1.6.1 3 / 47) || also checked by the stepdata investigator: SEQ button handler reads the current page ldrb [r6,#0x12c] (0x08144992, 0x081449BA, 0x081449E8) before NAVIGATE(r6, page) (0x08143D44); Double 0x0813CEFA ldrb [r4,#0x12c]; cmp #0x27; model dispatcher 0x0814056C ldrb [r4,#0x12c]; cmp #1 || also checked by the hold investigator: NAVIGATE ldrb/strb [r4,#0x12c] (0x08143F14/1C), BUTTON_EVENT cases ldrb [r0,#0x12c] (0x081447B8)'),  # stepui+stepdata+hold
    'MODEL_WORK': dict(value=0x45d0, evidence="the working note container: SEQ_COPY's dest in 7 callers is model+0x4580+0x50 (add.w rX,rY,#0x4580; adds rX,#0x50 at 0x08137F26, 0x081383F6, 0x0813857A, 0x0813C60C, 0x0813CABA, 0x0813CEA8, 0x0813ED50); 1.6.1 add.w #0x3c80 at its 6 callers; MODEL_MIXER_MODE 0x45CC sits right below as in 1.6.1 (0x3C7C) || also checked by the stepdata investigator: add.w rX,model,#0x4580; adds rX,#0x50 where 1.6.1 has add.w #0x3c80, as the SEQ_COPY destination, in all six paired functions (0x081383F6, 0x08138B74, 0x08139178, 0x081396CC, 0x0813CABA, 0x0813CEA8); the same +0x950 shift as the recording flag 0x9CA0 -> 0xA5F0"),  # stepui+stepdata
    'PAGE_SEQ': dict(value=0x17, evidence='template 8 page byte; HEADER_TPL_SEQ || also checked by the stepdata investigator: SEQ button handler: cmp r3,#0x17 / movs r1,#0x17 (0x081449BE, 0x081449CC) for non-one-shot tracks'),  # stepui+stepdata
    'PAGE_SEQ_DRUM': dict(value=0x27, evidence='template 36 page byte; HEADER_TPL_SEQ_DRUM || also checked by the stepdata investigator: SEQ button handler: cmp r3,#0x27 / movs r1,#0x27 (0x081449F0, 0x08144A02) for type 0x0E; Double 0x0813CEFE cmp #0x27 || also checked by the hold investigator: SEQ button, one-shot track: movs r1,#0x27 at 0x08144A02; NAVIGATE cmp r5,#0x27 at 0x08143D6C'),  # stepui+stepdata+hold
    'PAGE_SEQ_ALT': dict(value=0x1a, evidence='template 20 page byte'),  # stepui
    'EV_TAB_EVENT': dict(value=0x49, evidence='template ids (8: Event, 36: Step); DRUM_EVENT tbb over 0x47..0x50 (subs r3,#0x47 0x0815F3CA): 0x49 -> DRUM_SET_TAB(view, 0) 0x0815F448; PIANO_EVENT subs #0x49 0x0815C330'),  # stepui
    'EV_TAB_SEQUENCE': dict(value=0x4a, evidence='templates; DRUM_EVENT 0x4A -> DRUM_SET_TAB(view, 1) 0x0815F454'),  # stepui
    'EV_TAB_AUTOMATION': dict(value=0x4b, evidence='template 8 Automation id 0x4B; PIANO_EVENT tbh 0x49..0x4D'),  # stepui
    'EV_ENCODER': dict(value=0xcf, evidence="DRUM_EVENT cmp r3,#0xcf 0x0815F42E -> DRUM_ENCODER(view, ldrh [ev+8], [ev+0xc]) 0x0815F442; PIANO_EVENT tbh from 0xCD (subs r3,#0xcd 0x0815C34C); EXT_EVENT first insns ldrh r3,[r1]; cmp r3,#0xcf (orig 0b88cf2b); UI event ids below 0x108 unchanged (UI_POST immediates g15.py; 0x108.. grew by 1) || also checked by the stepdata investigator: EXT_EVENT's stock entry ldrh r3,[r1]; cmp r3,#0xcf (0x081487EC/EE, 1.6.1 0x08146B80/82); 25 cmp #0xcf sites in the M4 code of both images"),  # stepui+stepdata
    'TYPE_ONESHOT': dict(value=0xe, evidence="5 'blx vtable[0]; cmp r0,#0xe' on the M4 in both (1.6.8 first 0x08139C4C); 0x11 (TYPE_LOOP) 19 (1.6.1 18) || also checked by the stepdata investigator: SEQ button handler 0x08144750 (1.6.1 0x08142AFC, aligned diff: keys -2 and the recording flag only): track type = object vtable[0] (getter 0x08135490, blx at 0x081354FA); cmp r0,#0x11 -> page 0x25; cmp r0,#0xe (0x081449B4) -> page 0x27 (movs r1,#0x27 0x08144A02); else page 0x17 (0x081449CC) || also checked by the hold investigator: SEQ case: type() cmp r0,#0xe at 0x081449B4"),  # stepui+stepdata+hold
    'CONTEXT_GET': dict(value=0x8134a70, orig='f0b583b0', evidence='c2.py vs 1.6.1 0x08133614: 125 insns, only RAM literals (0x30030160 -> 0x30031AC0 ...) and the key bound sub.w #0x1d4 -> #0x1d2 differ; signature (model, &ctx, key, &value) unchanged || also checked by the stepdata investigator: same code; diffs only the cached-key range sub.w #0x1d2 (1.6.1 #0x1d4: the -2 key renumbering, keys 0x1D2..0x1D6 served from a cache) and its RAM literals; 0x8D/0x8E take the generic path (0x08134B12) as on 1.6.1; 340 -> 343 callers'),  # stepui+stepdata
    'DRUM_ENCODER': dict(value=0x815e820, orig='00f58833', evidence='c2.py vs 1.6.1 0x0815CAA0: 108 insns, 1 hunk: subw r3,r1,#0x183 -> #0x181 (stock step keys -2); first insn add.w r3,r0,#0x11000 (replayed); DRUM_EVENT 0xCF calls it (view, key, value)'),  # stepui
    'DRUM_EVENT': dict(value=0x815f3ac, orig='00b59bb0', evidence='push {lr}; sub sp,#0x6c (replayed); c2.py vs 1.6.1 0x0815D600: only action ids (0x1042 -> 0x1045, 0x102B -> 0x102D, 0x1041 -> 0x1044, 0x102C -> 0x102E, 0x1032 -> 0x1034), event 0x110 -> 0x111 and the tbb data; tabs 0x49/0x4A -> DRUM_SET_TAB 0x0815EF80 (0/1), 0xCF -> DRUM_ENCODER'),  # stepui
    'PIANO_EVENT': dict(value=0x815c304, orig='f0b5ddb0', evidence='push {r4-r7,lr}; sub sp,#0x174 (replayed); c2.py vs 1.6.1 0x0815A598: ids 0x110..0x118 -> 0x111..0x119, action ids grown, keys 0x1D9 -> 0x1D8, and one reordered case (0x0815C6CE: the view+0x125DAC == 2 test before the call, same fields 0x125DAC/0x122D24/0x125D40/0x11CD00/0x11CD08); dispatch: 0x49..0x4D tbh, 0xCD.. tbh (0xCF), as 1.6.1'),  # stepui
    'PIANO_HEADER': dict(value=0x815b730, orig='2de9f04f', evidence='push.w {r4-r11,lr} (replayed); c2.py vs 1.6.1 0x081599BC: only MODEL literals and stock keys -2 (0x17F -> 0x17D ... 0x1DF -> 0x1DE, 0x1DB -> 0x1DA); no view offset changed (slot keys +0x11BCC0, values +0x11BCD0, ctx +0x11BCA0, bar +0x55F0)'),  # stepui
    'SEQ_PAGE_INSERT': dict(value=0x813cc74, evidence="return address of 'bl NOTE_INSERT 0x0812EF64' at 0x0813CC70 (drum Sequence page, record sp+0x10, r0 = [r5], r1 = [r7+4]; 1.6.1 0x0813B4B6 [r6+4]); NOTE_INSERT callers 5 in both || also checked by the stepdata investigator: return address of bl NOTE_INSERT at 0x0813CC70 in 0x0813CAB0 (1.6.1 0x0813B2F8), the model's handler for UI event 0xE9 (tbh at 0x08141ED4: case 0xE9 -> 0x08142702; 1.6.1 case 0xE9 -> 0x08140C00), which the drum Step view's STEP_SET (UI_POST 0xE9 at 0x0815ED64..8C) and the Sequence page post; aligned diff: r6/r7 swap, MODEL_WORK, MODEL_DIRTY (strb [r3,#0xff4] 0x0813CCD0); fresh record +0x14 zeroed (str r4,[sp,#0x24], r4=0, 0x0813CAF0)"),  # stepui+stepdata
    'MEL_NOTE_ON': dict(value=0x8138ef6, evidence="return of bl NOTE_INSERT at 0x08138EF2 (record sp+0x48, r0 = [fp], r1 = [[sp+0x18]+0x10]) = 1.6.1 0x081377CA || also checked by the stepdata investigator: return address of bl NOTE_INSERT at 0x08138EF2 (handler +0x3EE, as 1.6.1 0x081377CE); record sp+0x48 = copy of the message's first 6 words with kind forced to 1 (0x08138D10..26). melodic recording handler 0x08138B08 (1.6.1 0x081373E0; entry guard model+0xA5F0, 1.6.1 +0x9CA0): aligned diff only MODEL_WORK (add.w #0x4580; adds #0x50, 1.6.1 add.w #0x3c80) and RAM literal 0x3000F650 -> 0x3000F65C, same instruction count (586), so every return address keeps its function offset"),  # stepui+stepdata
    'MEL_NOTE_OFF': dict(value=0x8139020, evidence='return of bl NOTE_UPDATE 0x0812EFC2 at 0x0813901C (length = n*0x3C0, record sp+0x60) = 1.6.1 0x081378F4. NOTE_UPDATE gained a 4th caller 0x0813863E (curve_update ignores it: lr filter) || also checked by the stepdata investigator: return address of bl NOTE_UPDATE at 0x0813901C (handler +0x518, as 1.6.1 0x081378F8); record sp+0x60 with its new length (str [sp,#0x68] 0x0813900E). melodic recording handler 0x08138B08 (1.6.1 0x081373E0; entry guard model+0xA5F0, 1.6.1 +0x9CA0): aligned diff only MODEL_WORK (add.w #0x4580; adds #0x50, 1.6.1 add.w #0x3c80) and RAM literal 0x3000F650 -> 0x3000F65C, same instruction count (586), so every return address keeps its function offset'),  # stepui+stepdata
    'UI_PAGES_CTOR': dict(value=0x8164f98, evidence='bl at 0x081153BC right after UI_BUMP_ALLOC(0x68B6D8) (1.6.1 0x08115050, size 0x689A98), same surroundings; stores the page-set vtable 0x081A1AD0'),  # stepui
    'UI_PAGES_INIT': dict(value=0x816495c, evidence='slot 0 of the page-set vtable 0x081A1AD0 (= 0x0816495D), called as [[r5]][0] at 0x081153C2..C8 (1.6.1 0x0819EC08[0] = 0x08162ADD)'),  # stepui
    'UI_WALK_CTX': dict(value=0x30018dbc, evidence="opaque: UI_TICK_WALK 0x08171C1C (same code as 1.6.1) and the drum view's tick (vt[0] 0x0815EED8) only forward r1 to the children's vt[0]; the value is 1.6.1's device crash-record value, kept as a harness value (no stock literal in either image)"),  # stepui
    'GFX_CACHE_LDR': dict(value=0x811538a, evidence='ldr r0,[pc,#0xf0]; bl GFX_CACHE_INIT 0x08180524 (literal 0x0811547C = 0x300378FC; 1.6.1 0x0811501E -> 0x30035F44), same form as 1.6.1'),  # stepui
    'DRUM_MENU_SITE': dict(value=0x8143140, evidence='movs r3,#4; addw r2,r6,#0xd48; movs r1,#0xfa; add.w r0,r5,#0xfc0; bl 0x08142D70, the same 10 instructions around it as 1.6.1 0x081415F8'),  # stepui
    'DRUM_MENU_TABLE': dict(value=0x819b938, evidence="the drum menu records {0, 1, id, label, 0} (20 B): 1.6.8 ids 0x1034/0x1044/0x1045/0x102E at 0x0819B8E8+20i (1.6.1 0x1032/0x1041/0x1042/0x102C at 0x08198DB0), the entry is the id word of the first empty record after them (1.6.1 0x08198E00 = 0x08198DF8+8). The matcher's 0x0819ADA8 is another table (ids 0x102A/0x102B)"),  # stepui
    'KEY_LFO_SHAPE': dict(value=0x4a, evidence="registry init 0x08165848: key 0x4A registered as 'LFO Shape' (0x08165F02) || also checked by the stepdata investigator: stock's registry builder run in emulation (rc19/stepdata/reg.py: 0x08165848, 1.6.1 0x081639A8): key 0x4A type 7 'LFO Shape' 9 choices in both"),  # stepui+stepdata
    'KEY_LFO_RATE': dict(value=0x4b, evidence="key 0x4B 'LFO Rate' (0x08165EB2) || also checked by the stepdata investigator: reg.py: 0x4B type 10 'LFO Rate' in both"),  # stepui+stepdata
    'KEY_LFO_DEPTH': dict(value=0x4c, evidence="key 0x4C 'LFO Depth' (0x08165EDA) || also checked by the stepdata investigator: reg.py: 0x4C type 10 'LFO Depth' in both"),  # stepui+stepdata
    'KEY_LFO_SRATE': dict(value=0x4f, evidence="key 0x4F 'LFO Rate' synced list (0x08165EC6) || also checked by the stepdata investigator: reg.py: 0x4F type 7 'LFO Rate' 15 choices (synced) in both"),  # stepui+stepdata
    'KEY_ENV_A': dict(value=0x1e, evidence="key 0x1E 'Attack' (0x08165A56, 0x081664A2) || also checked by the stepdata investigator: reg.py: 0x1E type 10 'Attack' in both"),  # stepui+stepdata
    'KEY_ENV_D': dict(value=0x1f, evidence="key 0x1F 'Decay' || also checked by the stepdata investigator: reg.py: 0x1F type 10 'Decay' in both"),  # stepui+stepdata
    'KEY_ENV_S': dict(value=0x20, evidence="key 0x20 'Sustain' || also checked by the stepdata investigator: reg.py: 0x20 type 10 'Sustain' in both"),  # stepui+stepdata
    'KEY_ENV_R': dict(value=0x21, evidence="key 0x21 'Release' || also checked by the stepdata investigator: reg.py: 0x21 type 10 'Release' in both"),  # stepui+stepdata
    'STEP_SAVE_SITE': dict(value=0x81303e4, orig='9df80830', evidence="ldrb.w r3,[sp,#8] (the replayed kind-byte load) right after the 'step' and 'chan' attributes, then subs/cmp #3/jump table over kinds 1..4 (0x081303E8..F0); record = NOTE_GET(container sl, r7, sp+8) at 0x08130372..78; node r4 = the new 'seqevent' element (0x0813038E bl add-child -> mov r4,r0 0x08130392), the node every attribute of this note goes to (XML_ADD 'step' 0x081303B6). Wrapper: push.w {r0-r12,lr} + push {r0,r1} = 64 B, so add r1,sp,#72 = record, r4 untouched. note writer 0x081301E0 (1.6.1 0x0812F2EC) identical over its code (229 instructions, masked) except one RAM literal 0x30021AE4 -> 0x300222E4 (the container it writes, +0x800 like MODEL); one caller in both"),  # stepdata
    'STEP_LOAD_SITE': dict(value=0x8130aa8, orig='09a90f98', evidence="add r1,sp,#0x24; ldr r0,[sp,#0x3c] (replayed) before bl 0x0812F558 (sequence add = container append at child+0xC; 1.6.1 0x0812E664 identical). Record at sp+0x24: kind strb #1 0x08130B36 (notes), +0x14 = sp+0x38 zeroed for every item at 0x08130B3C and set only for kinds 2/3/4 (value2), 'cond' parsed into +1 (strb [sp,#0x25] 0x08130AA4) before the site; node r4 = current seqevent (0x08130A3C first, 0x08130AB8 next). Wrapper add r1,sp,#100 = 64 + 0x24. note reader 0x08130A10 (1.6.1 0x0812FB1C) identical over its code (238 instructions, 0 masked diffs); two callers in both"),  # stepdata
    'XML_FIND': dict(value=0x8109f34, evidence="identical whole function (18 insns, masked); 27 callers in both; the note reader's lookups ('step' 0x08130AC6, 'cond' 0x08130A96)"),  # stepdata
    'XML_GET_U32': dict(value=0x8109836, evidence='identical whole function (10 insns); 27 callers in both; used right after XML_FIND in the reader (0x08130AD0)'),  # stepdata
    'XML_ADD': dict(value=0x8109fac, evidence="identical over its code (110 insns, 0 masked diffs; the one 'diff' the first pass showed was its literal pool); 39 callers in both; the writer's attribute add ('step' 0x081303B6, 'chan' 0x081303DA)"),  # stepdata
    'XML_SET_U32': dict(value=0x810985e, evidence='identical whole function (14 insns); 26 callers in both; follows every XML_ADD in the writer (0x081303C2)'),  # stepdata
    'NOTE_GET': dict(value=0x810d590, evidence='(container, i, rec): count at container+0x3000, 24-byte records (0x0810D590..BC), identical; 41 -> 42 callers'),  # stepdata
    'NOTE_PUT': dict(value=0x810d47a, evidence='(container, rec): replace by id (+0xC), identical (30 insns); 6 callers in both, each paired with its 1.6.1 caller (aligned diff: literals / MODEL only)'),  # stepdata
    'DOC_READ': dict(value=0x813190c, orig='2de9f04f', evidence='push.w {r4-r11,lr} replayed; identical over its code (93 insns) except RAM literal 0x30024AE8 -> 0x300252E8 (+0x800); 4 callers in both (incl. project load 0x081313EC at 0x08131416, which Pack 0x0812A590 now also calls); path in r0 (bl strlen-like 0x081894B8 first)'),  # stepdata
    'NOTE_INSERT': dict(value=0x812ef64, orig='70b588b0', evidence="(model store, sequence, record): push {r4,r5,r6,lr}; sub sp,#0x20 replayed; identical (39 insns); 5 callers in both (0x081384E4, 0x08138EF2, 0x0813CC70, 0x0813D0DA, 0x0813D19A), each paired with its 1.6.1 caller; wrapper's [sp,#16]=r2 record, [sp,#60]=lr, [sp,#8]=r0, [sp,#12]=r1"),  # stepdata
    'NOTE_UPDATE': dict(value=0x812efc2, orig='10b582b0', evidence='push {r4,lr}; sub sp,#8 replayed; identical (15 insns); callers 3 -> 4: new 0x0813863E in 0x08138548 (record-take undo: restores whole records from the snapshot container model+0xA5F8, so the word travels with the note); curve_update acts only for lr == MEL_NOTE_OFF'),  # stepdata
    'SEQ_COPY': dict(value=0x812f5cc, evidence="identical (16 insns); callers 7 -> 9 (new: record-take snapshot 0x081394F0, record-take undo 0x08138548); stock's pattern SEQ_COPY([model], seq, model+MODEL_WORK) at 0x0813CAC6"),  # stepdata
    'PUBLISH': dict(value=0x8137adc, evidence='identical (57 insns); 5 callers in both, paired (drum edits, piano commit)'),  # stepdata
    'SCALE_CONTEXT': dict(value=0x8135368, evidence="identical (15 insns); 25 callers both; stock's Scale + Root summary calls it then CONTEXT_GET(.., 0x8E) and (.., 0x8D) at 0x081436A8..C0 (1.6.1 0x081419DE)"),  # stepdata
    'GET_PROPERTY': dict(value=0x812eb68, evidence='identical (21 insns); stock reads the selected track as GET_PROPERTY(model+4, 0x131) at 0x08143470 (1.6.1 0x081416F8), as sel_track() does || also checked by the hold investigator: = 1.6.1 0x0812DC74 (21/21, 0 diffs)'),  # stepdata+hold
    'FIND_TRACK': dict(value=0x8134dbc, evidence='identical (28 insns); 31 -> 32 callers || also checked by the hold investigator: = 1.6.1 0x08133960 (28/28, 0 diffs)'),  # stepdata+hold
    'RESOLVE_OBJECT': dict(value=0x812e6fe, evidence='identical (10 insns); 44 -> 46 callers; first call of the recording handler (0x08138B2A) || also checked by the hold investigator: = 1.6.1 0x0812D80E (20/20, 0 diffs)'),  # stepdata+hold
    'PROJECT_TOUCH': dict(value=0x81322cc, evidence='identical (8 insns); 58 -> 59 callers (e.g. after the 0xE9 insert, 0x0813CCD6)'),  # stepdata
    'CTRL_INPUT_SITE': dict(value=0x808dcde, orig='9c42f6d0', evidence='PAD_RECEIVER 0x0808DB60 = 1.6.1 0x0808D080: 754/754 instructions, 0 diffs (masked compare incl. immediates and offsets); site +0x17E in both: ldrh r4,[r6,#2]!; ldrh r3,[r6,#0x30]; cmp r4,r3 (site); beq PAD_NEXT; r4 new / r3 old pressure, r7 pad (adds r7,#1 at 0x0808DCD2), fp receiver (ldr.w r0,[fp,#0x8c8] at 0x0808DCBE); the panel packet is copied to receiver+0x1328 (movw r5,#0x1328 at 0x0808DB64), so the button mask our hook reads at fp+0x1338 is packet+0x10 as on 1.6.1'),  # hold
    'PAD_NEXT': dict(value=0x808dcd0, evidence="receiver +0x170 (as 1.6.1): cmp r8,r6; add.w r7,r7,#1; beq: the per-pad loop step, CTRL_INPUT_SITE's beq target"),  # hold
    'PAD_CHANGED': dict(value=0x808dce2, evidence='receiver +0x182 (as 1.6.1): cmp r3,#0 right after the site (pressure changed path)'),  # hold
    'PAD_RECEIVER': dict(value=0x808db60, evidence='see CTRL_INPUT_SITE; caller 0x0808EB20 passes r0 = 0x2401BD40 (S2 PANEL_BOOTED)'),  # hold
    'ENQUEUE': dict(value=0x8082b00, evidence='UI ring enqueue = 1.6.1 0x08082220 (19/19 instructions, 0 diffs); 11 callers in both images, none posts 0xFE; ring fields +0x2000+0x180 / +0x1A0 and entries +0x980 (24 B) are what the M4 consumer reads (0x08140DF2..0x08140DFA, 0x081413CC..0x081413F2)'),  # hold
    'ENGINE_ENQUEUE': dict(value=0x80595c0, evidence="= 1.6.1 0x080595E0 (0 diffs); test_controls' receiver fixture only"),  # hold
    'M7_MEMCPY': dict(value=0x80a46ae, evidence='= 1.6.1 0x080A3F0E (29/29, 0 diffs); fixture only'),  # hold
    'M7_MEMCMP': dict(value=0x80a4640, evidence='= 1.6.1 0x080A3EA0 (48/48, 0 diffs); fixture only'),  # hold
    'M7_RECORD_INIT': dict(value=0x80a10c0, evidence='= 1.6.1 0x080A04A0 (16/16, 0 diffs); called by the receiver at 0x0808DCAA'),  # hold
    'PACKET_VALID': dict(value=0x80a35a0, evidence='= 1.6.1 0x080A2E00 (28/28, 0 diffs); fixture only'),  # hold
    'TRANSPORT_BUTTONS': dict(value=0x80585e0, evidence='= 1.6.1 0x08058600 (0 diffs over its 0x20 B to the next function); fixture only'),  # hold
    'CTRL_STARTUP_SITE': dict(value=0x80404b6, orig='24482449', evidence='M7 reset handler, the C++ init-array loop: ldr r0,=start; ldr r1,=end (site, replayed by our stub); cmp; beq; ldr r2,[r0]; adds #4; push {r0,r1}; blx r2: 0x080404A0..0x080404CE identical to 1.6.1'),  # hold
    'CTRL_STARTUP_LITS': dict(value=(134899548, 134899624), evidence='the words the two loads read (pool 0x08040548 / 0x0804054C): the init array, 19 entries as on 1.6.1 (0x080A5F9C..0x080A5FE8); build.py re-reads and compares them'),  # hold
    'CTRL_CONSUMER_SITE': dict(value=0x8141408, orig='3378113b', evidence='UI consumer 0x081404C8 (1.6.1 0x0813EC08; one caller each, 0x08164C2A / 0x08162DB2, with r0 = MODEL 0x30025300 / 0x30024B00); r4 = model for the whole function (mov r4,r0 at 0x081404D0, rewritten only in the epilogue 0x0814079C). CHANGED event registers: the fetch block 0x081413CC copies the 24-byte record into r6 = sp+0x218 (add r6,sp,#0x218 at 0x08140DEA, loop-invariant) and keeps no field in a register (1.6.1 0x0813F956..68: r5 code, r6 +8, r7 +0xC, [sp+8] +0x10, record copy at sp+0x220). Site: ldrb r3,[r6]; subs r3,#0x11 (4 B, replayed); cmp r3,#0xb3; bhi.w CTRL_DISPATCH_NEXT; ldr.w pc table at 0x08141418 (codes 0x11..0xC4). Cases read context [sp+0x220] = record+8 and index [sp+0x224] = record+0xC (0xAF at 0x08141024, 0xB0 at 0x08141042). The wrapper passes (r4, ldrb [r6], [r6,#8], [r6,#0xc], record r6); r5 is the event counter (adds r5,#1 at 0x081413FE), r7 = &ENGINE_PTR, r8 = sp+0x214'),  # hold
    'CTRL_DISPATCH_NEXT': dict(value=0x8140dee, evidence="dispatch loop head ldr.w lr,[r7] (r7 = 0x3000F65C, ldr at 0x08140DE8); ring empty test +0x2000 +0x1A0 / +0x180, bne.w to the fetch 0x081413CC; the jump table's default (bhi.w at 0x0814140E) and every case's return (b at 0x08141022, 0x08141040, 0x0814105E ...); as 1.6.1 0x0813F2FC. The set of non-default codes is the same in both tables (jt.py: 33 codes, 0x11 .. 0xC4)"),  # hold
    'CTRL_SELECTION_SITE': dict(value=0x813d504, orig='f0b589b0', evidence='drum cell selection = 1.6.1 0x0813BD10: 45/45 instructions, 0 diffs; push {r4-r7,lr}; sub sp,#0x24 (replayed); r2 = cell; 10 callers in both images'),  # hold
    'MIXER_RENDER_VT_SLOT': dict(value=0x81a0be8, orig='39d21408', evidence='holds MIXER_RENDER|1; same vtable shape as 1.6.1 0x0819DD20 (MIXER_EVENT 12 slots later in both; the vtable is referenced by the literal 0x081A0BA8, 1.6.1 0x0819DCE0, 0x38 below the slot)'),  # hold
    'MIXER_RENDER': dict(value=0x814d238, evidence='= 1.6.1 0x0814B630: 91/91 instructions, diffs only literal values (MODEL, 0x30031AC0/C4 for 0x30030160/64)'),  # hold
    'MIXER_EVENT': dict(value=0x814d6f2, evidence='CHANGED ids: (pane, event); range cmp.w #0x10e / subw #0x109 / tbb (1.6.1: #0x10c / #0x108): every case moved up 1; case 0x10D (0x0814D838) toggles pane+0x9A08 and posts 0x10D upward (1.6.1 case 0x10C at 0x0814BC30); 0x109..0x10C set column keys 0x134/0x13D/0x13E/0x13F (1.6.1 0x134/0x13F/0x140/0x141). pane+0x9A08 (our mixer_render) unchanged'),  # hold
    'MIXER_PAD_MODE': dict(value=0x813d418, evidence='(model, event): ldr r2,[r1,#8]; movne r2,#1; strb.w r2,[model+0x45CC]; bl 0x0813A880 (pad LED refresh, uses model+0x19D0); ignores the event code; 1.6.1 stored at +0x3C7C'),  # hold
    'NAVIGATE': dict(value=0x8143d44, evidence='= 1.6.1 0x081420EC: 285/285 instructions, the 29 diffs are all model offsets >= 0x1000; (model, page r1, r2, r3, byte [sp+0xc0]); previous page = page at 0x08143F14..1C; context pointers r4+0x13C..0x148 (0x08143FBA.., 0x0814417A); no page id changed (cmp r5,#0x27 at 0x08143D6C as 1.6.1)'),  # hold
    'SELECT_TRACK': dict(value=0x813d430, evidence='= 1.6.1 0x0813BC3C: 80/80 instructions, diffs model +0x9CA0 -> +0xA5F0 (x2) and the ENGINE_PTR literal; (model, track): set(model+4, 0x131, track) at 0x0813D44C'),  # hold
    'TRACK_PROPERTY_EVENT': dict(value=0x8132d40, evidence='(model, event): same entry as 1.6.1 0x08131AE0 (handle +4, key +8, value +0xC; RESOLVE_OBJECT([model]); GET_PROPERTY(obj,0x73); set 0x0812ECF4; notify 0x0810B920); the key switch was rebuilt for the -2 renumbering (0x150 -> 0x14E; 0x139..0x14E jump table at 0x08132E24); Mute 0x13B / Solo 0x13C take the default path (set, notify, post 0xC3) as 0x13D / 0x13E did; the event code is not read'),  # hold
    'BUTTON_EVENT': dict(value=0x8144750, evidence='= 1.6.1 0x08142AFC prologue (blocked by [model+0x19BE]; ldrh [r1] 0x73 press / 0x74 release); the press switch over ids 0..14 (ldr.w pc table at 0x0814477C) has the same case per id, each identical over 0x40 B except model +0x9CA0 -> +0xA5F0 (jtb.py)'),  # hold
    'PLAYING_STATUS': dict(value=0x81336b0, evidence='= 1.6.1 0x081322AC (19/19; the ENGINE_PTR literal only)'),  # hold
    'SELECT_SEQUENCE': dict(value=0x8138168, evidence='= 1.6.1 0x08136BD4 (154 instructions, 0 diffs to its end)'),  # hold
    'ENGINE_PROPERTY': dict(value=0x810b898, evidence='= 1.6.1 0x0810B5C8 (46/46, 0 diffs)'),  # hold
    'FLUSH_PAD_LIGHTS': dict(value=0x8125b5c, evidence='= 1.6.1 0x081257F0 (86/86; the ENGINE_PTR literal only); called with model+0x19D0 (0x08140ECA)'),  # hold
    'CLEAR_PAD_HIGHLIGHTS': dict(value=0x8125b38, evidence='= 1.6.1 0x081257CC (8/8, 0 diffs)'),  # hold
    'ENGINE_PTR': dict(value=0x3000f65c, evidence="the consumer's ring object (ldr r7,=0x3000F65C at 0x08140DE8) and ENGINE_PROPERTY's first argument (ldr r0,=0x3000F65C; ldr r0,[r0] at 0x08132DC8 / 0x0813804E); literal in SELECT_TRACK, PLAYING_STATUS, FLUSH_PAD_LIGHTS where 1.6.1 had 0x3000F650"),  # hold
    'UI_REQUEST': dict(value=0xfe, evidence="the consumer's bound subs #0x11; cmp #0xb3; bhi (0x0814140A..0E): codes above 0xC4 go to CTRL_DISPATCH_NEXT; no UI enqueue caller posts 0xFE (11 callers, both images)"),  # hold
    'UI_CODE_ENCODER': dict(value=0xab, evidence='consumer table: 0xAB -> 0x08140FCE (posts 0xAD to the view, +0x160); new guards [model+0x45C8] and 0x081079DE before the post'),  # hold
    'UI_CODE_PRESS': dict(value=0xaf, evidence='consumer table: 0xAF -> 0x08141024: BUTTON_EVENT(model, {0x73, context, index})'),  # hold
    'UI_CODE_RELEASE': dict(value=0xb0, evidence='consumer table: 0xB0 -> 0x08141042: BUTTON_EVENT(model, {0x74, ...})'),  # hold
    'EV_BUTTON': dict(value=0x73, evidence='movs r1,#0x73 at 0x08141028; BUTTON_EVENT cmp r3,#0x73 at 0x08144762'),  # hold
    'EV_TRACK_PROPERTY': dict(value=0xd3, evidence='MIXER_EVENT posts {0xD3, handle, key, value} (movs r0,#0xd3 at 0x0814D792)'),  # hold
    'EV_MIXER_PAD_MODE': dict(value=0x10d, evidence='MIXER_EVENT case 0x10D (movw r2,#0x10d at 0x0814D848; toggle at 0x0814D838); 1.6.1 0x10C'),  # hold
    'BTN_TRACKS': dict(value=0x2, evidence='button press id 2 -> 0x081447B8: navigate(model, 2) unless page 2'),  # hold
    'BTN_LAUNCH': dict(value=0x3, evidence='id 3 -> 0x08144AE6: navigate(0x1E) unless page 0x1E'),  # hold
    'BTN_MIXER': dict(value=0x5, evidence='id 5 -> 0x08144AAC: navigate(0x1F)'),  # hold
    'BTN_INST': dict(value=0xa, evidence='id 10 -> 0x081447D0: page 9 stays, page 7 -> 0x14, else INST'),  # hold
    'BTN_SEQ': dict(value=0xb, evidence='id 11 -> 0x08144978: type 0x11 -> page 0x25, 0x0E -> 0x27 (0x08144A02), else 0x17'),  # hold
    'PAGE_TRACKS': dict(value=0x2, evidence='movs r1,#2 at 0x081447C8 (TRACKS button)'),  # hold
    'PAGE_INST': dict(value=0x7, evidence='INST case cmp r3,#7 at 0x081447D8'),  # hold
    'PAGE_INST_ALT1': dict(value=0x9, evidence='INST case cmp r3,#9 at 0x081447D4'),  # hold
    'PAGE_INST_ALT2': dict(value=0x14, evidence='INST case navigate(0x14) at 0x08144830'),  # hold
    'PAGE_LAUNCH': dict(value=0x1e, evidence='movs r1,#0x1e at 0x08144AF8'),  # hold
    'PAGE_MIXER': dict(value=0x1f, evidence='movs r1,#0x1f at 0x08144AB4'),  # hold
    'TYPE_LOOP': dict(value=0x11, evidence='SEQ case cmp r0,#0x11 at 0x0814498C; 0x0813352E cmp r0,#0x11 before the 0x2E enumeration'),  # hold
    'CHILD_LOOP_CLIPS': dict(value=0x2e, evidence='vtable +0x1C (obj, 0x2e, 0, &child): ldr r4,[r3,#0x1c]; movs r1,#0x2e; blx r4 at 0x08133538..40'),  # hold
    'CHILD_SEQUENCES': dict(value=0x18, evidence='vtable +0x1C (obj, 0x18, i, &child) at 0x08136A68..70 (6 users with 0x18 in both images)'),  # hold
    'ENGINE_PROP_RUN': dict(value=0x64, evidence="movs r2,#0x64; bl ENGINE_PROPERTY at 0x08138030/36 (value = GET_PROPERTY(obj,0x64), registry 'PlayMode'); the same 5 call sites as 1.6.1"),  # hold
    'ENGINE_PROP_CLIP': dict(value=0x63, evidence='movs r2,#0x63; bl ENGINE_PROPERTY at 0x0813804E/56 (and 0x0813967C, 0x0813E892)'),  # hold
    'MODEL_PREV_PAGE': dict(value=0x13a, evidence='NAVIGATE strb.w r3,[r4,#0x13a] at 0x08143F18'),  # hold
    'MODEL_CTX': dict(value=0x13c, evidence='NAVIGATE passes r4+0x13C / +0x140 (0x08143FBA, 0x08143FCE); INST case ldr [r0,#0x13c] at 0x081447DE'),  # hold
    'MODEL_SEQ': dict(value=0x144, evidence='NAVIGATE ldr.w r3,[r4,#0x144] (0x081442B2); SEQ case ldr.w r1,[r6,#0x144] (0x081449DA)'),  # hold
    'MODEL_SCENE': dict(value=0x148, evidence='NAVIGATE add.w r2,r4,#0x148 at 0x0814417A (1.6.1 0x08142522, same place)'),  # hold
    'MODEL_PAD_LEDS': dict(value=0x19d0, evidence='FLUSH_PAD_LIGHTS(model+0x19C0+0x10) at 0x08140ECA (1.6.1 +0x19C0+4 at 0x0813F3CA); all 14 add.w #0x19c0 sites are followed by +0x10 (1.6.1 +4); model+0x19C4..0x19C7 are new bytes (consumer 0x081404EA..)'),  # hold
    'MODEL_MIXER_MODE': dict(value=0x45cc, evidence='MIXER_PAD_MODE strb.w r2,[r3,#0x5cc] with r3 = model+0x4000 (0x0813D426); NAVIGATE +0x45CD/+0x4598 where 1.6.1 had +0x3C7D/+0x3C48'),  # hold
    'KEY_SEL_CLIP': dict(value=0x133, evidence="registry 'Selected Cell Pos' 0x133 in both (rc14 layout/registry.json)"),  # hold
    'KEY_LOOP_ROW': dict(value=0x6f, evidence="registry 'Group Index' 0x6F in both; the 0x2E children's row"),  # hold
    'KEY_SEQ_ROW': dict(value=0x73, evidence="registry 'Disp Pos' 0x73 in both; GET_PROPERTY(child,0x73) at 0x08144846 / 0x08132D6E"),  # hold
    'KEY_MUTE': dict(value=0x13b, evidence="registry 'Mute' 0x13B (1.6.1 0x13D); TRACK_PROPERTY_EVENT default path"),  # hold
    'KEY_SOLO': dict(value=0x13c, evidence="registry 'Solo' 0x13C (1.6.1 0x13E)"),  # hold
    # ---- S4+S5 implementer: the UI page-set layout (test_notemod_real) and the selected-track key ----
    "UI_PAGES_SIZE": dict(value=0x68B6D8,
        evidence="ldr r0,[pc,#0xd8] at 0x081153B0 (literal 0x0811548C, the only word 0x68B6D8 in the image), UI_BUMP_ALLOC, "
                 "str r0,[r6]; bl UI_PAGES_CTOR 0x08164F98 at 0x081153BC (1.6.1: 0x08115044, literal 0x08115120 = 0x689A98)"),
    "UI_PAGE_PIANO": dict(value=0x2EF4D0,
        evidence="the page-set member UI_PAGES_CTOR gives PIANO_VIEW_VT (member 14; stepui g10.py; 1.6.1 +0x2ED980); "
                 "test_notemod_real runs the real constructor and asserts PIANO_VIEW_VT at block + this offset"),
    "UI_PAGE_DRUM": dict(value=0x677738,
        evidence="the page-set member that gets DRUM_VIEW_VT (member 33; 1.6.1 +0x675AF8); test_notemod_real asserts it the same way"),
    "KEY_SEL_TRACK": dict(value=0x131,
        evidence="SELECT_TRACK 0x0813D430 (track r1 <= 7): movw r1,#0x131; adds r0,r5,#4; bl 0x0812ECC2 (set property on "
                 "model+4) at 0x0813D44C; GET_PROPERTY(model+4, 0x131) at 0x0814346A..0x08143470 (stepdata: stock reads the "
                 "selected track this way, as our sel_track() does); keys below 0x13C are not renumbered"),
    # ---- S4+S5 implementer: stock code test_step_xml runs (real note writer, XML library and reader) ----
    "NOTE_SEQ_WRITER": dict(value=0x081301E0,
        evidence="the function around STEP_SAVE_SITE 0x081303E4 (+0x204 as 1.6.1 0x0812F2EC / 0x0812F4F0); stepdata: identical "
                 "over its 229 instructions (masked) except the container literal 0x300222E4 (NOTE_WRITE_SRC); one caller in both. "
                 "test_step_xml runs it unstubbed on 1.6.8: every record comes back from the reader"),
    "NOTE_WRITE_SRC": dict(value=0x300222E4,
        evidence="the writer's only RAM literal that differs from 1.6.1's (0x30021AE4, +0x800 like MODEL); NOTE_GET(this, i, sp+8) "
                 "at 0x08130372..78 (stepdata)"),
    "NOTE_SEQ_READER": dict(value=0x08130A10,
        evidence="the function around STEP_LOAD_SITE 0x08130AA8 (+0x98 as 1.6.1 0x0812FB1C / 0x0812FBB4); stepdata: 238 instructions, "
                 "0 masked diffs, two callers in both; test_step_xml: zeroes +0x14 of every note (0x08130B3C), keeps kind 2's value2"),
    "XML_DOC_INIT": dict(value=0x0810A390,
        evidence="push {r4-r6,lr}; mov r4,r0; mov r6,r1; mov r5,r2; movs r1,#0 = 1.6.1 0x0810A0C0; the XML library moved +0x2D0 "
                 "as a block (XML_FIND 0x08109C64 -> 0x08109F34, reviewed); test_step_xml builds the document with it"),
    "XML_NEW_ELEMENT": dict(value=0x0810A5E6,
        evidence="push.w {r4-r8,lr}; mov r4,r0; mov r6,r1; ldr r3,[r0,#0x74]; cbz = 1.6.1 0x0810A316 (+0x2D0)"),
    "XML_APPEND_CHILD": dict(value=0x081096E2,
        evidence="push {r3-r5,lr}; ldr r2,[r1,#4]; ldr r3,[r0,#4]; cmp; bne = 1.6.1 0x08109412 (+0x2D0)"),
    "M4_DATA_INIT": dict(value=0x081B6D30,
        evidence="the M4 reset handler's copy source for 0x30000000.. (stepdata); same first words as 1.6.1's 0x081B3E38 "
                 "(ff000000 ff000000 ffffffff ff333333); +0x48 / +0x4C = 0xC8000000 / 0xC6000000, the UI bump arena (stepdata)"),
    "M4_DATA_INIT_LEN": dict(value=0x104, evidence="the first block of the copy table, 0x104 B in both (stepdata)"),
    "REG_BUILD": dict(value=0x08165848,
        evidence="push.w {r4-fp,lr}; sub sp,#0x14; mov sl,r0; mov.w r0,#0x3e40 (498 x 32 = REG_KEYS); bl M4_MALLOC 0x08104DB4; "
                 "ldr r3,=REGISTRY; str r0,[r3] at 0x08165848..0x0816585A (1.6.1 0x081639A8: #0x3e60 = 499 x 32); stepdata ran it "
                 "(reg.py); test_keys runs it and finds every key we name with its 1.6.1 title"),
    "M4_MALLOC": dict(value=0x08104DB4,
        evidence="push {r3,lr}; mov r3,r0; bic r2,r0,#0x1f; adds r2,#0x20 = 1.6.1 0x08104C08; REG_BUILD's first call"),
    # ---- S4+S5 implementer: the header path test_notemod_real HeaderThroughStock runs on stock code ----
    "HEADER_TPL_LOAD": dict(value=0x081435BC,
        evidence="push.w {r4-r8,lr}; sub.w sp,sp,#0x470; mov r6,r0 (model); mov r5,r1 (page); add.w r3,r0,#0x4000 (1.6.1 "
                 "0x08141944: the same with #0x3000, the model grew); stepui: the template loader (model, page); with page 0x27 "
                 "the rig's applied header then has the drum Step / Sequence tabs and Undo, as on the device"),
    "HEADER_TICK": dict(value=0x081820AC,
        evidence="push {r4-r6,lr}; sub sp,#0x1f8; mov r6,r0 (root); mov r4,r1 (ev); memcpy(sp+0x1c, [pc], 0x1dc = HEADER_DESC_SIZE); "
                 "bl HEADER_GEN at 0x081820CC and bl HEADER_BUILD at 0x081820E8 (1.6.1 0x0817FE70: #0x1c4; 0x0817FE90 / 0x0817FEAC); "
                 "one of HEADER_GEN's 3 callers in both"),
    "TEMPO_GET": dict(value=0x0810BEF8,
        evidence="push {r3,lr}; add.w r0,r0,#0x5180; adds r0,#0x3c (= 1.6.1 0x0810BC28); 2 callers, one in HEADER_GEN (0x08135EF8)"),
    "M4_LOCK_PAIR": dict(value=0x08104AC0,
        evidence="push {r4,lr}; mov r4,r0; ldr r0,[r0]; cbz -> calls itself; ldr r3,[r4,#4]; cbz; bl 0x08117518 x2, bl 0x08117464 "
                 "x2 (RTOS take / give on the two handles); 22 callers, wrappers 0x08104B48..0x08104CE4 (1.6.1 0x08104914, same "
                 "code). Without the RTOS objects (the rig) it recurses until the stack runs out: stepui's fault"),
    "MODEL_HEADER_ON": dict(value=0x14C, evidence="HEADER_GEN 0x08135D7C ldrb.w r6,[r0,#0x14c]; cmp r6,#0; beq.w 0x08135F46 (1.6.1 0x08134808)"),
    # ---- S4+S5 implementer: the moved / exact entries the default 1.6.8 build uses (now reviewed) ----
    'ANY_SELECTED': dict(value=0x815d830, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; in the drum view block 1.6.1 0x0815B9C0.. = 1.6.8 0x0815D740.. (+0x1D80), which the step UI investigator compared whole (only literals, keys and action ids differ)"),
    'BAR_TEXT': dict(value=0x81727b6, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs (+0x2160); stepui: masked compare of the widget setters"),
    'DRUM_HEADER': dict(value=0x815dffc, orig='2de9f043', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; push.w {r4-r9,lr} replayed by our wrapper (build.py replays); DRUM_KEYS 0x300000BC is still its only literal (stepui); test_notemod_real runs it under our hook on the real drum view"),
    'DRUM_SET_TAB': dict(value=0x815ef80, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; stepui: 0x49 / 0x4A call DRUM_SET_TAB(0/1) at 0x0815F448 / 0x0815F454 in DRUM_EVENT"),
    'DRUM_VIEW_VT': dict(value=0x81a171c, evidence='the vtable page-set member 33 gets in UI_PAGES_CTOR (stepui); first 8 words: same layout as 1.6.1 0x0819E854 (flash pointers); test_notemod_real asserts it at UI_PAGE_DRUM on the real page set'),
    'GRID': dict(value=0x815e238, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (the one diff 0xC before it is the previous function's literal)"),
    'HEADER_ROOT': dict(value=0x300366e8, evidence='3 literal words in both images; HEADER_ROOT_INIT is constructed at it (stepui); test_notemod_real HeaderThroughStock runs HEADER_TICK on it and reads the buttons HEADER_BUILD made there'),
    'HEADER_ROOT_VTABLE': dict(value=0x81b6560, evidence="the only literal of it is in HEADER_ROOT_INIT (0x08182710, stepui); first 8 words flash pointers as 1.6.1 0x081B3680; our header_sync compares the root's first word with it, test_notemod_real sees it there"),
    'HOLD_TAP_PAD': dict(value=0x815f030, orig='b3f57a7f', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 1 diff, the UI_TICK_MS literal (0x30035F40 -> 0x300378F8, a fact); hold investigator: pad release +0x48, cmp.w r3,#1000 (b3f57a7f) in both; test_holdstep runs stock's release"),
    'HOLD_TAP_TOUCH': dict(value=0x815f230, orig='b3f57a7f', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 1 diff, the UI_TICK_MS literal; touch release +0x40, cmp.w r3,#1000 (hold investigator); test_holdstep"),
    'M4MIDI_BEGIN': dict(value=0x8105f1e, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs (+0x1C8, the MIDI builders moved as a block); s5parts: whole-function masked compare 0 diffs; test_polyat M4MidiTask runs it"),
    'M4MIDI_CHANNEL': dict(value=0x8105e26, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask"),
    'M4MIDI_DATA': dict(value=0x8105ec6, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask"),
    'M4MIDI_DONE': dict(value=0x8105d3c, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask stubs it after stock's case called it"),
    'M4MIDI_RESULT': dict(value=0x8105e14, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask"),
    'M4MIDI_SEND': dict(value=0x8105acc, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask records (sink, port, bytes) here"),
    'M4MIDI_STATUS': dict(value=0x8105e0c, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: 0 diffs; M4MidiTask"),
    'PIANO_COMMIT': dict(value=0x81595a4, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (+0x1D44, the piano block; stepui compared it)"),
    'PIANO_SELECTED': dict(value=0x815a590, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (stepui: piano block)"),
    'PIANO_VIEW_VT': dict(value=0x81a1678, evidence='the vtable page-set member 14 gets (stepui); first 8 words as 1.6.1 0x0819E7B0; test_notemod_real asserts it at UI_PAGE_PIANO'),
    'PROB_PIANO_REF_BACK': dict(value=0x815ac6a, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; s5parts: the ref function differs from 1.6.1 only in the MODEL literal; PROB_PIANO_REF_SHA exact over SITE..BACK (build.py replaced())"),
    'PROB_PIANO_REF_SITE': dict(value=0x815ac60, orig='9df82530', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; ldrb.w r3,[sp,#0x25] (replayed, build.py); s5parts as BACK"),
    'PROB_SHOW_DRUM_SITE': dict(value=0x815e152, orig='013b0b60', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; subs r3,#1; str r3,[r1] (replayed); s5parts: context 0 diffs"),
    'SLOT_SET': dict(value=0x8173d7c, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (+0x2160)"),
    'SLOT_TITLE': dict(value=0x81809de, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs (+0x2180)"),
    'SNAPSHOT': dict(value=0x815d784, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs (drum view block)"),
    'STEP_DEFAULTS_READ': dict(value=0x815ee1c, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs; add.w r2,r4,#0x11000 checked by build.py at the site; the view offsets 0x114C0..C8 unchanged (stepui)"),
    'STEP_EDIT_OFFSET': dict(value=0x815e710, orig='2de9f04f', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (push.w {r4-fp,lr} replayed); hold: the three edit hooks match; test_holdstep runs stock's edit"),
    'STEP_EDIT_PROB': dict(value=0x815e62c, orig='2de9f04f', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry; as STEP_EDIT_OFFSET"),
    'STEP_EDIT_VEL': dict(value=0x815e544, orig='2de9f04f', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry; as STEP_EDIT_OFFSET"),
    'STEP_HAS_NOTE': dict(value=0x815da34, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry (drum view block)"),
    'STEP_MATCH': dict(value=0x815d740, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs"),
    'STEP_PRESS': dict(value=0x815ef98, orig='0f2921d8', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 1 diff, the UI_TICK_MS literal at +0x4C; build.py checks cmp r1,#15; bhi STEP_PRESS_SKIP at the site"),
    'STEP_PRESS_SKIP': dict(value=0x815efe0, evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 1 diff, the UI_TICK_MS literal; the bhi target of STEP_PRESS (build.py)"),
    'STEP_TOGGLE': dict(value=0x815eeb8, orig='38b51f29', evidence="implementer's masked compare (rc19/impl/vmoved0.py: 0x60 B from the entry, branches inside relative, outside masked, literals by value): 0 diffs from the entry; push {r3-r5,lr}; cmp r1,#0x1f replayed (build.py)"),
    'DRUM_KEYS': dict(value=0x300000bc, evidence="RAM literal 0x300000BC, 1 word in both images: DRUM_HEADER's only literal (stepui); the step view's 8 slot keys"),
    'KEY_ROOT': dict(value=0x8e, evidence="test_keys: stock's registry builder on 1.6.8 has 'Root Note' (type 7, 12 choices) at 0x8E, as 1.6.1"),
    'KEY_SCALE': dict(value=0x8d, evidence="test_keys: 'Scale' (type 7, 23 choices) at 0x8D, as 1.6.1"),
    'PROB_PIANO_REF_SHA': dict(value='b4a5ebca1082bd7ad8133a0b69a75f6ce7dff3ab254489f9c06c85eb517c2de6', evidence='build.py replaced(): sha256 of PROB_PIANO_REF_SITE..BACK in the 1.6.8 image equals this (the default build checks it)'),
    'PROB_PIANO_EDIT_SHA': dict(value='82d2ea2c828e8f1ddbed98d19f29414b1c2bfe37f8b4a48c8b269b1b7c3c111a', evidence='build.py replaced(): sha256 of PROB_PIANO_EDIT_SITE..BACK in the 1.6.8 image equals this (checked by every build); s5parts: byte-identical'),
    # S4+S5 review fix: the producer of recorded notes' +0x14 (rc19/impl2). The M4 consumer copies the M7 -> M4 ring entry
    # (ENGINE_PTR+0x2FC0) and dispatches on its byte +0x18 (0x08140930: 1 -> the recording handler 0x08138B08, which copies
    # words 0..5 into the note, so +0x14 = word 5; 6 -> 0x08138A04, no insert). The ring's enqueue 0x08082BA0 has two callers:
    # the engine-outbox forwarder 0x0808D540 (type 6) and the bus forwarder 0x08057F60, which reads the bus with BUS_TAKE.
    'REC_MSG_5': dict(value=0x80602a0, evidence="1.6.1 0x0805FF00 with the same message build (0x08060320..0x0806038C = 1.6.1 0x0805FF80..EC instruction for instruction): r7 = 0; str r3=#5,[sp,#8]; strd r7,r7,[sp,#0xc]; str r7,[sp,#0x14]; strh r5/sb at +0x18/+0x1a; str r7,[sp,#0x1c] (word 5 = 0); copied to sp+0x20 before bl 0x080A2940 (writes only [sp..sp+7]); strb #1 at +0x18; bl BUS_APPEND. One caller 0x080629E6"),
    'REC_MSG_6': dict(value=0x80603e0, evidence="1.6.1 0x08060040, same build: r7 = 0; word 0 = 6; strd r7,r7,[sp,#0x18] then only strh r5,[sp,#0x18] (word 5 at sp+0x1c stays 0); +0x18 = 1; bl BUS_APPEND at 0x080604CA. One caller 0x08062990"),
    'BUS_APPEND': dict(value=0x809fce0, evidence="byte-identical to 1.6.1 0x0809F0E0 (0x5C B): count [bus+0x1A48] < 64, copies the 48 B to bus+0xE48 + 48 x count. Callers (bl/b scan of M7, both phases): 0x0806038C (REC_MSG_5) and 0x080604CA (REC_MSG_6) only, as 1.6.1; no pointer to it in the image; no other store to bus+0xE48.. (the other #0xe48 / #0xa48 stores are parameter-table constructors)"),
    # ======== S6a (2026-11-08): Analog oscillators (rc20/analog investigation; rc20/s6a: ent.py, q1/q2/q5.py, keytab.py,
    # typ2.py, regdump.py; build.py replays / orig checks; test_analog.py and test_inst_xml.py run these sites' own code).
    'M7_DSP': dict(value=0x80d0000, evidence="stock's zero gap 0x080CFC5C..0x080E0000 (M7 .data end literal at 0x08040568 = "
                   "0x080CFC5C); check_dsp_assumptions: zero, no literal or movt into it"),
    'M7_DSP_LIMIT': dict(value=0x80d9000, evidence="36 KB, up to CTRL_INPUT (BE4.10 layout)"),
    'DSP_WINDOW_WORDS': dict(value=(0x80f6a94, 0x80f6ba4, 0x80f6f08, 0x81ab750, 0x81ab860, 0x81abbc4, 0x81adaa0),
                             evidence="every aligned word in [M7_DSP, M7_DSP_LIMIT): 3 in the SD-reader image, 4 in M4 rodata, "
                                      "the same words at the same places in 1.6.1 (rc20/s6a lit.py); no PC-relative ldr reads "
                                      "any of them, decoding every halfword (pclit.py); check_dsp_assumptions repeats both"),
    'ACT_ANALOG': dict(value=0x10f4, evidence="after ACT_NM 0x10F3; stock's menu / header action ids end at 0x104A (menu table: "
                       "0x1025..0x102C; REVIEW_OURS rule)"),
    'KEY_AN_SYNC': dict(value=0x1c0, evidence="plan allow-list: empty after stock's builder (test_keys), no immediate in either core, "
                        "below claim()'s 0x1C1. Stock's key-range tests that cover it both send it to their default case: M4 "
                        "0x08132E80 (sub.w r3,r5,#0x1b8; cmp r3,#0xf; table 0x08132E98, entry 8 = the default 0x08132DF6) and M7 "
                        "0x0805D4F4 (keys 0x1B8..0x1D1, table 0x0805D504, entry 8 = the default 0x0805D360); test_keys decodes both"),
    'KEY_AN_GLIDE': dict(value=0x1b7, evidence="plan allow-list: as KEY_AN_SYNC; no key-range test covers it (M7 0x08059990 "
                         "takes 0x18C..0x1B6, one below it; test_keys)"),
    'ACT_WAVETABLE': dict(value=0x102c, evidence="instrument menu table 0x0819AD30 cell 5 {0x102C, 'Wavetable', 0, 0, 1}; the "
                          "menu handler 0x0815095C posts {0x42, 0, 6, track} for it (0x08150BD6)"),
    'TYPE_WAVETABLE': dict(value=0x12, evidence="'blx; cmp r0,#0x12' 10 sites in both images (and 0x13 13 / 0x0E 5 in both: the type "
                           "numbers did not move, typ2.py); Playground's 1.6.1 type 18"),
    'ROOT_HANDLE': dict(value=0xc, evidence="root vtable slot 10 = 0x08054540 'str r1,[r0,#0xc]'; the engine input handler gives "
                        "a free root (key 0xFFFFFF00) the key from the M4's message (0x08059C66..76: ldr r1,[r4,#0x10]; "
                        "vt[0x28]) and finds roots by [root,#0xc] == that key (0x08059C0C); the black box confirms it"),
    'VOICE_WT1': dict(value=0x7a4, evidence="WT root ctor 0x0809CB20: addw r7,r4,#0x7a4; bl osc ctor; strd r7,r6,[r4,#0x178]"),
    'VOICE_WT2': dict(value=0x994, evidence="WT root ctor: addw r6,r4,#0x994"),
    'VOICE_SUB': dict(value=0x4c, evidence="WT root ctor: add.w r3,r4,#0x4c; str [r4,#0x180] (the third oscillator)"),
    'OSC_KERNEL': dict(value=0x10c, evidence="WT osc render: add.w r0,r4,#0x10c; bl kernel read 0x080A2000 (0x08081D52); note "
                       "start: add.w r0,r0,#0x10c; bl kernel reset 0x080A1FC0"),
    'WT_VOICE_VT_SLOT': dict(value=0x80acfb0, orig='81ca0908', evidence="root vtable 0x080ACF68 (WT root ctor literal 0x0809CB80) "
                             "+ 18 x 4; = WT_VOICE_RENDER | 1"),
    'WT_VOICE_RENDER': dict(value=0x809ca80, evidence=f"{_AF}; loops voice +0x178..+0x180 calling osc vt[0](osc, r3, [sp]), then "
                            "the post 0x0807FCC0"),
    'WT_OSC_VT_SLOT': dict(value=0x80acbb4, orig='411c0808', evidence="osc ctor 0x08081E00 stores 0x080ACBB4 at [osc]; slot 0 = "
                           "WT_OSC_RENDER | 1"),
    'WT_OSC_RENDER': dict(value=0x8081c40, evidence=_AF),
    'BUF_FRAMES': dict(value=0x80a2500, evidence=_AF + "; ldr r3,[r0,#4]; cbnz; ldr r3,[r0]"),
    'PARAM_VALUE': dict(value=0x80a09a0, evidence=_AF),
    'NOTE_OCT': dict(value=0x804f680, evidence=_AF),
    'OCT_RATIO': dict(value=0x804f5e0, evidence=_AF + "; x*100 -> linear interpolation in the 2001-entry table 0x080C05D4, "
                      "times 32.703 (0x4202CFDF, C1)"),
    'EXP2': dict(value=0x80518c0, evidence="the same table and interpolation as OCT_RATIO without the C1 factor (2^x); out of "
                 "range: the table's ends (0x08051920 / 0x08051940); 17 callers"),
    'LEVEL_CURVE': dict(value=0x804f360, evidence=_AF),
    'SMOOTH_SET': dict(value=0x8071a20, evidence=_AF),
    'SMOOTH_APPLY': dict(value=0x8071820, evidence=_AF),
    'SCRATCH_PTR': dict(value=0x80523e0, evidence=_AF),
    'SCRATCH_SIZE': dict(value=0x8052fa0, evidence=_AF),
    'MIX_INTO': dict(value=0x8052760, evidence=_AF),
    'WT_ROOT_CTOR': dict(value=0x809cb20, evidence="the one bl caller 0x08058BB4 (engine init); 0x0809CB80 literal = the root vtable"),
    'WT_OSC_CTOR': dict(value=0x8081e00, evidence=_AF),
    'WT_NOTE_START': dict(value=0x8081dc0, evidence=_AF),
    'VOICE_POST': dict(value=0x807fcc0, evidence=_AF + "; the one caller 0x0809CAB0"),
    'VOICE_DRIVER': dict(value=0x807b7c0, evidence="the one caller 0x0807AFCE: only for voices whose byte 0 is set (0x0807AFB4 "
                         "ldrb; beq); calls slot 18 (0x0807BA94 / 0x0807BA16)"),
    'NAMED_ALLOC': dict(value=0x80a05c0, evidence="the WT root ctor's voice allocation: ldr r0,='wtVoice'; movs r1,#2; movw r2,#0xb88"),
    'MENU_CELL': dict(value=0x819add0, orig='00000000f0c51808000000000000000001000000', evidence="menu table 0x0819AD30 + 8 x 20 "
                      "(menu3.py: cells 0..7 the eight instruments, 8 and 9 empty and enabled)"),
    'MENU_COUNT': dict(value=0x8142eb4, orig='0823', evidence="the same context as 1.6.1 (movs r3,#8; add.w r2,r6,#0x190; movs r1,#0xf6)"),
    'MENU_VT_SLOT': dict(value=0x81a13f4, orig='5d091508', evidence="= MENU_HANDLER | 1 (sites.py)"),
    'MENU_HANDLER': dict(value=0x815095c, evidence=_AF + " over the first 8 B; dispatch on ldrh [r5] with 0x102C as the top bound"),
    'PAGE_KEYS_COPY': dict(value=0x8161446, orig='28f00ef8', evidence="bl memcpy(view+0x11CAC, 0x081A17E0, 0xB0); the key table "
                           "rows 0 / 1 are {0xC4, 0x3, 0xC9, 0, ...} as on 1.6.1 (keytab.py)"),
    'M4_MEMCPY': dict(value=0x8189466, evidence=_AF),
    'PANE_TEXT_1': dict(value=0x815f714, orig='21f063f9', evidence="bl SLOT_TITLE, same context as 1.6.1 (sites.py)"),
    'PANE_TEXT_2': dict(value=0x815f8ce, orig='21f086f8', evidence="as PANE_TEXT_1"),
    'PANE_TEXT_3': dict(value=0x815f9ba, orig='21f010f8', evidence="as PANE_TEXT_1"),
    'PANE_TEXT_4': dict(value=0x815ff5e, orig='20f03efd', evidence="as PANE_TEXT_1 (no literal load right before it)"),
    'PANE_TEXT_5': dict(value=0x815ff68, orig='20f039fd', evidence="as PANE_TEXT_1"),
    'PANE_TEXT_6': dict(value=0x815ff92, orig='20f024fd', evidence="as PANE_TEXT_1"),
    'STR_NO_WAV': dict(value=0x818f098, evidence="'No WAV' (pane2.py, strs.py)"),
    'SYNTH_VIEW_EV_SLOT': dict(value=0x81a178c, orig='010f1608', evidence="synth view class 0x081A175C + 0x30 = SYNTH_VIEW_EVENT | 1"),
    'SYNTH_VIEW_EVENT': dict(value=0x8160f00, evidence="1.6.1's prologue and dispatch; 0xCF (tbh index 2) re-posts {0xD0, +4, key "
                             "(0xD3 -> 0xD2), value, +0x10} with UI_POST (0x08161158); unknown codes return 1"),
    # S6a review (2026-11-08, rc20/s6a2: mcmp2.py, sdis.py, words.py): the CPU indicator (A10), the instrument actions that
    # clear the Analog flag, and the M7 .data end the DSP window must stay above.
    'SYNTH_VIEW_TICK_SLOT': dict(value=0x81a175c, orig='81f51508', note="synth view class 0x081A175C slot 0: tick (view, flags)", evidence="synth view class 0x081A175C slot 0 (the class "
                                 "whose slot 12 is SYNTH_VIEW_EV_SLOT) = SYNTH_VIEW_TICK | 1; 1.6.1 0x0819E894 = 0x0815D7D1"),
    'SYNTH_VIEW_TICK': dict(value=0x815f580, evidence="(view r0, flags r1): the model read 0x08135388 (1.6.1 0x08133E74), the two "
                            "Wavetable panes' positions 0x08184948 on view+0x3550 / +0x3830 (1.6.1 0x08182720 on +0x3550 / +0x36C8), "
                            "then the container tick 0x08171C1C(view, flags) (1.6.1 0x0816FABC); 61 / 62 instructions, the 17 masked "
                            "differences are offsets of the grown view (mcmp2.py)"),
    'VIEW_NOWAV_LABEL': dict(value=0x3114, evidence="page builder 0x0815FED4: add.w r6,r0,#0x3100; adds r6,#0x14, then "
                             "'No WAV' (0x0818F098) with bl SLOT_TITLE on r6 at PANE_TEXT_4 0x0815FF5E (stride 0x360 for WT2); "
                             "1.6.1 0x0815E124 the same 0x3114 (PANE_TEXT_4 0x0815E1AE)"),
    'ACT_INST_FIRST': dict(value=0x1025, evidence="instrument menu table 0x0819AD30 cells 0..7: 0x1025, 0x1026, 0x1027, 0x1029, "
                           "0x1028, 0x102C (Wavetable), 0x102A, 0x102B: the eight instruments are 0x1025..0x102C (1.6.1 0x1023..0x102A, "
                           "table 0x081981F8); words.py"),
    'M7_DATA_END': dict(value=0x8040568, orig='5cfc0c08', evidence="M7 startup copy table (0x08040540..0x0804056C): the word at "
                        "0x08040568 = 0x080CFC5C, the end of the M7 image's flash data (stock's zero gap starts there); 1.6.1 the "
                        "same slot = 0x080CF49C"),
    'INST_SAVE_SITE': dict(value=0x81304de, orig='80460024', evidence="instrument writer 0x08130484 (doc r0, parent r1, object "
                           "r2 -> r6): params = XML_APPEND_CHILD(parent, XML_NEW_ELEMENT(doc, 'params')) in r0, then mov r8,r0; movs r4,#0"),
    'INST_LOAD': dict(value=0x81309a0, orig='f0b585b0', evidence="(node r0, object r1): XML_CHILD(node, 'params'), attributes "
                      "matched to the object's descriptors by token (0x0812F11A); unknown ones skipped"),
    'XML_CHILD': dict(value=0x8109538, evidence="1.6.1 0x08109268's prologue and loop (ldr r4,[r0,#0x18]; child list +0x24)"),
    'BUS_TAKE': dict(value=0x809fb40, evidence="byte-identical to 1.6.1 0x0809EF40 (0x60 B): i < [bus+0x1A48] -> copies entry i, returns 1; the bus forwarder's loop 0x08057F60..78 (BUS_TAKE, then the ring enqueue 0x08082BA0)"),
    # BE4.22 note0 (rc23 implN: dis.py, preds.py; docs/plan-ext-low-notes.md, sites re-derived). Each sentinel test sits after the
    # track's MIDI-out check (+0x86D1) and branches, on note 0 only, to an out-of-line block that writes stock's drum note into
    # the note register (pad <= 7: pad + 0x2C, <= 15: pad + 0x1C, else 0x34) and goes back to BACK, where the MIDI event is
    # built. Each block has exactly one predecessor, the sentinel branch (preds.py: a branch decode at every halfword of
    # 0x08040000..0x080D0000), so a b.w at the block's entry runs only for note 0 on a MIDI-out track and leaves every other
    # note on stock's own path. 'orig' = the block's first 4 bytes (cmp pad, #7 [; bls]).
    'NOTE0_SEQ_ON_MAP': dict(value=0x805fd60, orig='072e0dd9', evidence="note_route 0x0805FC80 (track r4, r5 = track+0x8000, note r1 = "
                             "sb, pad r6 = ldrh [sp,#0x58], router [sp,#0x5c]): ldrb r3,[r5,#0x6d1]; cbz r3 0x0805FCD0; cmp r1,#0; beq "
                             "0x0805FD60 (the sentinel 0x0805FC9C) / fall-through mov sl,r1. Block: cmp r6,#7; bls 0x0805FD80 (add.w "
                             "sl,r6,#0x2c; b BACK); cmp r6,#0xf; ite ls; addls.w sl,r6,#0x1c; movhi.w sl,#0x34; b BACK. Note register sl"),
    'NOTE0_SEQ_ON_BACK': dict(value=0x805fca2, evidence="add r0,sp,#0x20; bl msg init 0x080A10C0; ... str.w sl,[sp,#0x30] (the note); "
                              "bl MSG_SEND at POLYCAP_SEQ_ON_SITE 0x0805FCCC. Branches here: 0x0805FD70, 0x0805FD84 (the block) and the "
                              "fall-through; r0-r3, r12 and the flags are written before they are read"),
    'NOTE0_SEQ_OFF_MAP': dict(value=0x8060260, orig='072b0dd9', evidence="sequencer Note Off 0x080601A0 (track r4, note r1, pad r3 "
                              "(16-bit: both callers ldrh, NM_OFF_SITE_1/2), router [sp,#0x38]): ldrb r2,[r5,#0x6d1]; cbz r2; cmp r1,#0; "
                              "beq 0x08060260 (the sentinel 0x080601B6) / mov r8,r1. Block: cmp r3,#7; bls 0x08060280 (add.w r8,r3,#0x2c; "
                              "b BACK); cmp r3,#0xf; ite ls; addls.w r8,r3,#0x1c; movhi.w r8,#0x34; b BACK. Note register r8; r3 (pad) is "
                              "stored at BACK + 2 (str r3,[sp]) and used again after MSG_SEND: a hook must keep it"),
    'NOTE0_SEQ_OFF_BACK': dict(value=0x80601bc, evidence="add r0,sp,#8; str r3,[sp]; bl 0x080A10C0; ... str.w r8,[sp,#0x18]; bl MSG_SEND "
                               "at POLYCAP_SEQ_OFF_SITE 0x080601E4. Branches here: 0x08060270, 0x08060284 and the fall-through"),
    'NOTE0_PAD_ON_MAP': dict(value=0x8060300, orig='b8f1070f', evidence="PAD_NOTE_ON 0x080602A0 (track r4, r6 = track+0x8000, note r5 = "
                             "ev+0xC, pad r8 = ldrh ev+0x14, router sl): ldrb r3,[r6,#0x6d1]; cbz r3; cbz r5,0x08060300 (the sentinel "
                             "0x080602C6). Block: cmp.w r8,#7; bls 0x080603A0 (add r5,r8,#0x2c; b BACK); cmp.w r8,#0xf; ite ls; addls.w "
                             "r5,r8,#0x1c; movhi r5,#0x34; b BACK. Note register r5"),
    'NOTE0_PAD_ON_BACK': dict(value=0x80602c8, evidence="add r0,sp,#0x20; bl 0x080A10C0; ... str r5,[sp,#0x30]; bl MSG_SEND at "
                              "POLYCAP_PAD_ON_SITE 0x080602F0. Branches here: 0x08060312, 0x080603A4 and the fall-through"),
    'NOTE0_PAD_OFF_MAP': dict(value=0x8060440, orig='b8f1070f', evidence="PAD_NOTE_OFF 0x080603E0 (track r4, note r5, pad r8, router "
                              "sb): cbz r5,0x08060440 (the sentinel 0x08060402). Block: cmp.w r8,#7; bls 0x080604E0 (add r5,r8,#0x2c; b "
                              "BACK); cmp.w r8,#0xf; ite ls; addls.w r5,r8,#0x1c; movhi r5,#0x34; b BACK. Note register r5"),
    'NOTE0_PAD_OFF_BACK': dict(value=0x8060404, evidence="add r0,sp,#0x20; bl 0x080A10C0; ... str r5,[sp,#0x30]; bl MSG_SEND at "
                               "POLYCAP_PAD_OFF_SITE 0x08060428. Branches here: 0x08060452, 0x080604E4 and the fall-through"),
    'NOTE0_ALL_OFF_MAP': dict(value=0x8061d40, orig='072f7dd9', evidence="NM_ALL_OFF 0x08061D00 (track r6, fp = track+0x8000, router "
                              "r8, per held note: pad r7 = ldrh [r4,#0x3a], note sb = [r4,#0x3c]): ldrb r1,[fp,#0x6d1]; ...; cmp.w sb,#0; "
                              "beq 0x08061D40 (the sentinel 0x08061DEC) / mov sl,sb; b BACK. Block: cmp r7,#7; bls 0x08061E40 (add.w "
                              "sl,r7,#0x2c; b BACK); cmp r7,#0xf; bhi.w 0x08061E60 (mov.w sl,#0x34; b BACK); add.w sl,r7,#0x1c, then nops "
                              "into BACK. Note register sl"),
    'NOTE0_ALL_OFF_BACK': dict(value=0x8061d60, evidence="add r0,sp,#8; bl 0x080A10C0; ... str.w sl,[sp,#0x18]; bl MSG_SEND at "
                               "POLYCAP_ALL_OFF_SITE 0x08061D86. Branches here: 0x08061DF4 (the non-zero path), 0x08061E44, 0x08061E64, "
                               "and the block's fall-through"),
    # BE4.22 analog: instrument handles (rc23 handles investigation: ctr.py, scan10.py, uses.py, lits.py, probe1/2.py; re-run by
    # rc23 implH: dis8.py, probe2.py; docs/plan-synths-168.md "Instrument handles"). Every M4 object gets its handle from
    # OBJ_CTOR (7 bl sites: 0x081260B6, 0x081260D0, 0x0812E800 (STORE_CREATE), 0x0812F2E0 (child clone), 0x0812F348 (NODE_CLONE),
    # 0x0812F8A8, 0x08131A92); the counter only goes up, so a handle is never reused within a boot (probe2: REMOVE + CREATE
    # hands the same address back with a new handle). Every top-level object leaves through NODE_DESTROY (slot 2) and every
    # copy is made by NODE_CLONE (slot 3); neither has a bl caller or another word pointing at it.
    'OBJ_CTOR': dict(value=0x812e79c, orig='074a0260', evidence="13 instructions, no frame: ldr r2,=OBJ_BASE_VT (pc+0x1c, the "
                     "literal at 0x0812E7BC); str r2,[r0]; movs r2,#0; strb r2,[r0,#4]; str r2,[r0,#8]; str r2,[r0,#0xc]; str r2,"
                     "[r0,#0x14]; ldr r1,=OBJ_HANDLE_CTR; ldr r2,[r1]; add.w ip,r2,#1; str.w ip,[r1]; str r2,[r0,#0x10]; bx lr. "
                     "Writes r1, r2, ip and the flags only (callers may keep r3 across it: a wrapper must keep every other "
                     "register); 'orig' = the vtable load and store, which a wrapper replays"),
    'OBJ_BASE_VT': dict(value=0x819917c, evidence="the word at 0x0812E7BC, OBJ_CTOR's first literal (ldr r2,[pc,#0x1c])"),
    'OBJ_HANDLE_CTR': dict(value=0x30000088, evidence="OBJ_CTOR's ldr r1 literal; its only literal in the image (lits.py) and "
                           "0x0812E7B2 its only store (uses.py); M4 .data (M4_DATA_INIT + 0x88) = 1, the M4 static constructors "
                           "take 1..3 (probe2 reads 4 after them)"),
    'NODE_VT_DESTROY': dict(value=0x8199190, orig='cbe81208', evidence="generic node vtable 0x08199188 (the 0x92C-byte track "
                            "instrument node, type byte +4, slot 0 0x08125FBC ldrb r0,[r0,#4]) slot 2 = NODE_DESTROY | 1; called "
                            "by the store clear 0x0812E542, STORE_REMOVE 0x0812E4C0 and a failed STORE_CREATE 0x0812E82A"),
    'NODE_DESTROY': dict(value=0x812e8ca, evidence="(obj r0, pool r1): push.w {r4-r8,lr}; frees its properties (0x0812E868) "
                         "and its children directly (0x0812E868 + pool free 0x08101E9C), never through slot 2"),
    'NODE_VT_CLONE': dict(value=0x8199194, orig='11f31208', evidence="the same vtable, slot 3 = NODE_CLONE | 1; called by "
                          "STORE_COPY_ALL 0x0812E5EA and STORE_CLONE_H 0x0812E650 (blx through [vt+0xC])"),
    'NODE_CLONE': dict(value=0x812f310, evidence="(src r0, pool r1, &out r2) -> ok: alloc 0x92C (0x08101FB4), memset, OBJ_CTOR "
                       "(new handle), node vtable, properties 0x0812F164 (never writes +0x10), type byte, children by the direct "
                       "child clone 0x0812F2B4; *out only on success (0x0812F372)"),
    'STORE_INIT': dict(value=0x812e3a6, evidence="PresetMgr init 0x081261D4 makes its two stores with it (probe2 runs it)"),
    'STORE_POOL_INIT': dict(value=0x812e55c, evidence="(store, bytes): the DyMemPool at store+0x58 (probe2 runs it)"),
    'STORE_CREATE': dict(value=0x812e7c4, evidence="(store r0, type r1, &obj r2) -> ok: alloc 0x92C from store+0x58, OBJ_CTOR "
                         "(0x0812E800), node vtable, slot 1 init (type, pool), list +0..+0x2C (count +0x30, at most 12) and the "
                         "track list +0x34 (count +0x54, type 0x15 left out); a 13th is destroyed (slot 2)"),
    'STORE_REMOVE': dict(value=0x812e43e, evidence="(store r0, handle r1): finds the top-level object by +0x10 (else slot 8 into "
                         "its children), takes it out of both lists, slot 2 (0x0812E4C0), pool free"),
    'STORE_COPY_ALL': dict(value=0x812e57e, evidence="(dst r0, src r1): the store clear 0x0812E510 (slot 2 per object), then "
                           "slot 3 per src object into dst+0x58 and both lists; load commit 0x08126768 (live <- PresetMgr+0x330), "
                           "save snapshot 0x08126450 (PresetMgr+0x334 <- live)"),
    'STORE_CLONE_H': dict(value=0x812e600, evidence="(dst r0, handle r1, src r2, &out r3): the src object with that handle, "
                          "slot 3 into dst+0x58, both lists; preset apply 0x081267D6, clipboard copy / paste 0x08124F68, 0x08124F8E, "
                          "0x08125130"),
    # BE4.22 test_note0's end-to-end pad sweep (review rc23 B2; the plan's N1 exit test, run as rc21/ui/r2/chain.py did on
    # stock 1.6.8: 128 distinct notes with literal note 0, 127 on stock). Re-checked by rc23 integ2 padr_dis.py / padr_lit.py.
    'PADR_CTOR': dict(value=0x8087620, evidence="push.w {r4-r8,lr}; mov r5,r0; ... str r1(0xFFFFFF00),[r5,#0xc]; str r2(vtable "
                      "literal),[r5]; its only bl caller 0x08058768; no word points at it"),
    'PADR_LOOP': dict(value=0x8085fc0, evidence="(router r0, bus r1): push.w {r4-sl,lr}; ldr r1,[r0,#0xc] (its key); bl BUS_FIND "
                      "0x0809F920; then the queue read 0x08065AC0 loop; no bl caller, the word 0x08085FC1 at 0x080ACC50 is the only "
                      "one pointing at it; LIVE_SITE 0x080865EA is inside it"),
    'PADR_RELEASE': dict(value=0x80844c0, evidence="push.w {r4-fp,lr}; 6 bl callers, all in the pad router "
                         "(0x0808708C .. 0x08087616); chain.py stubs it as the held-pad release on mode / offset changes"),
    'SCALE_INIT': dict(value=0x80a54c0, evidence="push.w {r3-fp,lr}; ldr r4,=0x2401DA18 (the scale table) and fills it; no bl "
                       "caller, the word 0x080A54C1 at 0x080A679C is the only one pointing at it"),
    'BUS_FIND': dict(value=0x809f920, evidence="(router r0, key r1) -> mailbox: ldr.w r2,[r0,#0x508]; subs r2,#1; bmi (none); "
                     "binary search (ite hi; addhi.w ip,r3,#1; addls.w r2,r3,#-1) of {key, mailbox} from +8; 38 bl callers"),
    'BUS_POST_KEY': dict(value=0x809fc40, evidence="(router r0, ev r1, key r2): ldr.w r3,[r0,#0x508]; subs.w ip,r3,#1; bmi "
                         "(dropped); the same binary search, then the mailbox post; 98 callers, among them the internal note "
                         "path after 0x0805FD40 (the pad.s +0x86DC key, build review rc23)"),
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
