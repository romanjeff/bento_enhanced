# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent record of every stock struct offset, registry key and event id (and the registry's
size) bento_enhanced relies on, per base. Kept apart from the base tables on purpose.

The tables (bases/stock_*.py) feed the build, the C header and the tests alike, so a wrong value
there would pass every suite (a test that reads S.KEY_MUTE agrees with C that reads S_KEY_MUTE).
This file is the second record: build.py refuses a base whose table differs from it in any of
these (bases.verify_expected), and tools/addrmap/gen_table.py marks such a draft entry REVIEW.
Addresses are not repeated here: code / data / RAM entries are checked against the image itself
(fingerprints and literal counts, CHECKS in each table).

Write a value here only from your own check in that firmware (say how, in the comment). None
means "not independently measured yet"; a table entry with a value but None here is REVIEW.

stock_161: the literals of the pre-S1 sources (v3.72, whose rebuild is byte-identical) and of the
tests / display emulator of that snapshot.
"""

STOCK_161 = dict(
    REG_KEYS=499,
    # pad router, model fields
    ROUTER_SEL_TRACK=0x2C8, ROUTER_PAD_KEYS=0x2CC,
    MODEL_PAGE=0x12C, MODEL_PREV_PAGE=0x13A, MODEL_CTX=0x13C, MODEL_SEQ=0x144, MODEL_SCENE=0x148,
    MODEL_PAD_LEDS=0x19C4, MODEL_DIRTY=0x36C4, MODEL_MIXER_MODE=0x3C7C, MODEL_WORK=0x3C80,
    # header bar: root, button widgets, description and its action records
    HEADER_APPLIED=0x34, HEADER_BUTTONS=0x54C, HEADER_BUTTON_STRIDE=0xD0, HEADER_BUTTON_COUNT=12,
    HEADER_BUTTON_HIDDEN=0x30, HEADER_BUTTON_LABEL=0x6D, HEADER_ARMED=0x117C,
    HEADER_DESC_SIZE=0x1C4, HEADER_DESC_ACTIONS=0x74, HEADER_ACTIONS=6, HEADER_ACTION_SIZE=0x30,
    HEADER_ACTION_EMPTY=0x20, HEADER_ACTION_LIT=0x21, HEADER_ACTION_GROUP=0x22, HEADER_ACTION_ID=0x2C,
    # registry / parameter keys
    KEY_SEL_TRACK=0x131, KEY_SEL_CLIP=0x133, KEY_MUTE=0x13D, KEY_SOLO=0x13E,
    KEY_MX_FIRST=0x171, KEY_MX_LAST=0x176, KEY_DRUM_STEP_PARAM=0x184,
    KEY_LFO_SHAPE=0x4A, KEY_LFO_RATE=0x4B, KEY_LFO_DEPTH=0x4C, KEY_LFO_SRATE=0x4F,
    KEY_ENV_A=0x1E, KEY_ENV_D=0x1F, KEY_ENV_S=0x20, KEY_ENV_R=0x21,
    KEY_LOOP_ROW=0x6F, KEY_SEQ_ROW=0x73, KEY_SCALE=0x8D, KEY_ROOT=0x8E,
    # M4 UI events, pages, buttons, object types
    EV_ENCODER=0xCF, EV_MX_TAKE_TRACK=0xA8, EV_PAGE_CHANGE=0x0A,
    EV_TAB_EVENT=0x49, EV_TAB_SEQUENCE=0x4A, EV_TAB_AUTOMATION=0x4B,
    EV_MX_SELECT=0x02, EV_DASHBOARD=0x03, EV_DASHBOARD_ALT=0x16, EV_MX_REBUILD=0xC3,
    EV_BUTTON=0x73, EV_TRACK_PROPERTY=0xD3, EV_MIXER_PAD_MODE=0x10C,
    UI_CODE_ENCODER=0xAB, UI_CODE_PRESS=0xAF, UI_CODE_RELEASE=0xB0,
    BTN_TRACKS=2, BTN_LAUNCH=3, BTN_MIXER=5, BTN_INST=10, BTN_SEQ=11,
    PAGE_TRACKS=0x02, PAGE_INST=0x07, PAGE_INST_ALT1=0x09, PAGE_INST_ALT2=0x14, PAGE_SEQ=0x17,
    PAGE_SEQ_ALT=0x1A, PAGE_LAUNCH=0x1E, PAGE_MIXER=0x1F, PAGE_SEQ_DRUM=0x27,
    HEADER_TPL_SEQ_DRUM=36, HEADER_TPL_SEQ=8, HEADER_TPL_SEQ_ALT=20,
    TYPE_ONESHOT=0x0E, TYPE_LOOP=0x11, CHILD_LOOP_CLIPS=0x2E, CHILD_SEQUENCES=0x18,
    ENGINE_PROP_CLIP=0x63, ENGINE_PROP_RUN=0x64,
    MXSRC_NONE=0x00, MXSRC_LFO1=0x04, MXSRC_LFO2=0x05, MXSRC_ENV1=0x01, MXSRC_ENV2=0x02,
    MXSRC_VEL=0x0D, MXSRC_PRESS=0x20, MXSRC_KEY=0x0E,
    MIDI_JT_FIRST=0x5B,
    # M7 engine / track events
    EV_PAD_PRESS=0xAC, EV_PAD_RELEASE=0xAD, EV_PAD_PRESSURE=0xAE, EV_VOICE_PRESSURE=0x0E,
    EV_TRACK_NOTE_ON=0x02, EV_TRACK_NOTE_OFF=0x03, EV_OBJ_NOTE_ON=0xB9, EV_OBJ_NOTE_OFF=0xBA,
    EV_MIDI_NOTE_ON=0x5B, EV_MIDI_NOTE_OFF=0x5C, EV_MIDI_BEND=0x5D, EV_MIDI_CC=0x5E,
    EV_MIDI_POLY_AT=0x5F, EV_MIDI_CHAN_AT=0x60, EV_MIDI_RT_FIRST=0x61, EV_MIDI_RT_LAST=0x63,
    # fields of stock objects the black box reads (m7_diag.c literals of v3.72)
    MIDI_RING_RD=0x2340, MIDI_RING_WR=0x2360, LOAD_RING_VALUES=0x8, LOAD_RING_IDX=0x408, LOAD_RING_START=0x414,
    LINK_TX=0x190, LINK_RX=0x194, LINK_BAD=0x19C, LINK_VERSION=0x1A2, SPI_STATE=0x81, PASS_TIMER_LAST=0x4,
    AUDIO_BLOCK_WRONG_HALF=0x4, AUDIO_BLOCK_HEAD=0xC, AUDIO_BLOCK_HEAD_WRONG=0x18,
    AUDIO_GAP_MAX=0x8, AUDIO_GAP_UNDER=0xC, AUDIO_GAP_OVER=0x10, AUDIO_GAP_TIMEOUTS=0x14, AUDIO_GAP_POLLS=0x18,
    AUDIO_GAP_WAITS=0x1C,
    # UI page set (test_notemod_real; v3.65's literals)
    UI_PAGE_PIANO=0x2ED980, UI_PAGE_DRUM=0x675AF8,
    MODEL_HEADER_ON=0x14C,         # HEADER_GEN 0x08134808 ldrb.w r6,[r0,#0x14c]; beq.w out
)

# 1.6.8: only what was measured in the 1.6.8 image by someone other than the generator's mapping.
STOCK_168 = {**dict.fromkeys(STOCK_161, None), **dict(
    REG_KEYS=498,                 # registry walk: 498 entries (rc14 layout; plan "What changed")
    ROUTER_SEL_TRACK=0x308,       # LIVE_SITE hand check: router +0x2C8 -> +0x308
    ROUTER_PAD_KEYS=0x30C,        # and +0x2CC -> +0x30C
    MODEL_DIRTY=0x3FF4,           # #0x6c4 uses 20 -> 6, #0xff4 uses 1 -> 24 (S1 draft review)
    HEADER_DESC_SIZE=0x1DC,       # template loop at 0x081435E4: add.w r2, r2, #0x1dc; HEADER_GEN copies 0x1DC
    KEY_SEL_TRACK=0x131,          # immediate counts (S1 draft review); DRUM_ENCODER / MX_EDIT hand checks
    KEY_MUTE=0x13B, KEY_SOLO=0x13C,         # immediate counts; TRACK_PROPERTY_EVENT keys move down 2
    KEY_MX_FIRST=0x16F, KEY_MX_LAST=0x174,  # MX_EDIT hand check: key range 0x171 -> 0x16F
    KEY_DRUM_STEP_PARAM=0x182,    # DRUM_ENCODER hand check: key base 0x183 -> 0x181 (same -2 shift)
    KEY_SCALE=0x8D, KEY_ROOT=0x8E,  # hand check 0x081419DE -> 0x081436A8: keys 0x8E / 0x8D unchanged
    # S2: the event ids the black box reads, each from the 1.6.8 code that posts or compares it
    EV_PAD_PRESS=0xAC,            # pad receiver movs r3,#0xac at 0x0808DEBA
    EV_PAD_RELEASE=0xAD,          # pad receiver movs r3,#0xad at 0x0808DCFE
    EV_PAD_PRESSURE=0xAE,         # pad router cmp r3,#0xae at 0x08085FFA
    EV_VOICE_PRESSURE=0x0E,       # External handler: not 2/3 -> bne.w at POLY_SITE 0x08062986 (0x0E path as 1.6.1)
    EV_TRACK_NOTE_ON=0x02,        # External handler cmp r3,#2 at 0x08062980
    EV_TRACK_NOTE_OFF=0x03,       # cmp r3,#3 at 0x08062984
    EV_OBJ_NOTE_ON=0xB9,          # cmp r3,#0xb9 at 0x0805D620
    EV_OBJ_NOTE_OFF=0xBA,         # cmp r3,#0xba at 0x0805D624
    EV_MIDI_NOTE_ON=0x5B,         # M4 MIDI task cmp r5,#0x5b at 0x081053D8; subs #0x5b, cmp #8 (MIDI_JT 0x081053EC)
    EV_MIDI_NOTE_OFF=0x5C,        # cmp r5,#0x5c at 0x081053DC; MIDI_JT[1]
    EV_MIDI_BEND=0x5D,            # MIDI_JT[2]
    EV_MIDI_CC=0x5E,              # MIDI_JT[3]
    EV_MIDI_POLY_AT=0x5F,         # MIDI_JT[4] = M4_LOOP (unused by stock)
    EV_MIDI_CHAN_AT=0x60,         # MIDI_JT[5] = M4_LOOP (unused by stock)
    EV_MIDI_RT_FIRST=0x61,        # MIDI_JT[6]
    EV_MIDI_RT_LAST=0x63,         # MIDI_JT[8], bound cmp r5,#8 at 0x081053E2
    MIDI_JT_FIRST=0x5B,           # subs r5,#0x5b at 0x081053E0; cmp r5,#8; ldr.w pc,[r3,r5,lsl #2] at 0x081053E8
    # S2 review: fields of stock objects the black box reads, from the 1.6.8 code that uses them (scratch rc17/s3)
    MIDI_RING_RD=0x2340,          # M4 reader add.w r0,sl,#0x2340 at 0x081053D0 (7 add.w #0x2340 on the M4, as 1.6.1); M7 ctor 0x08082FF6
    MIDI_RING_WR=0x2360,          # M7 writer: add.w ip,r0,#0x2000; ldr [ip,#0x360] at 0x08082F20/24; movw #0x2360 at 0x08082F4E
    LOAD_RING_VALUES=0x8,         # load ring code 0x08056EC0 = 1.6.1 0x08056F00 (masked): +8 + 4*idx
    LOAD_RING_IDX=0x408,          # same code: +0x408
    LOAD_RING_START=0x414,        # same code: +0x414
    LINK_TX=0x190,                # CTRL_LINK users: ldr/str [r,#0x190] 1/1 as 1.6.1 (literal-tracked scan offs3.py)
    LINK_RX=0x194,                # str [r,#0x194] 1/1
    LINK_BAD=0x19C,               # str [r,#0x19c] 1/1
    LINK_VERSION=0x1A2,           # ldrh [r,#0x1a2] 1/1
    SPI_STATE=0x81,               # HAL SPI State: M7 ldrb.w #0x81 11, strb.w 14 (both); SPI1_HANDLE users identical (masked)
    PASS_TIMER_LAST=0x4,          # pass timer 0x08057CA0 (1.6.1 0x08057CC0): 77 instructions identical (masked), str [r,#4]
    AUDIO_BLOCK_WRONG_HALF=0x4,   # main audio loop 0x0808E9F6 (1.6.1 0x0808DF12), block stats in sb: ldr/str [sb,#4] at 0x0808ED8A/90
    AUDIO_BLOCK_HEAD=0xC,         # ldr [sb,#0xc] 0x0808EBFA/0x0808EC04, str 0x0808ED44
    AUDIO_BLOCK_HEAD_WRONG=0x18,  # ldr/str [sb,#0x18] 0x0808ED98/0x0808EDA2
    AUDIO_GAP_MAX=0x8,            # gap stats, same loop: ldr/str [r1,#8] 0x0808EBC4/CA
    AUDIO_GAP_UNDER=0xC,          # [r2,#0xc] 0x0808EBD6/DA
    AUDIO_GAP_OVER=0x10,          # [r2,#0x10] 0x0808EDB2/B6
    AUDIO_GAP_TIMEOUTS=0x14,      # [r2,#0x14] 0x0808EDE6/EA
    AUDIO_GAP_POLLS=0x18,         # [r3,#0x18] 0x0808ED60/68
    AUDIO_GAP_WAITS=0x1C,         # [r3,#0x1c] 0x0808ED5A/5E
    # S4+S5 (step, hold): each typed from the 1.6.8 code the investigators cite (rc19/stepui, rc19/stepdata, rc19/hold)
    UI_PAGE_PIANO=0x2EF4D0,       # UI_PAGES_CTOR 0x08164F98: page-set member 14 (PIANO_VIEW_VT); test_notemod_real reads the vtable there (stepui g10.py)
    MODEL_HEADER_ON=0x14C,        # HEADER_GEN 0x08135D7C ldrb.w r6,[r0,#0x14c]; cmp; beq.w 0x08135F46 (implementer)
    UI_PAGE_DRUM=0x677738,        # page-set member 33 (DRUM_VIEW_VT), same check
    BTN_INST=0xa,                 # button press table 0x0814477C, cases jtb.py (hold)
    BTN_LAUNCH=0x3,               # button press table 0x0814477C, cases jtb.py (hold)
    BTN_MIXER=0x5,                # button press table 0x0814477C, cases jtb.py (hold)
    BTN_SEQ=0xb,                  # button press table 0x0814477C, cases jtb.py (hold)
    BTN_TRACKS=0x2,               # button press table 0x0814477C, cases jtb.py (hold)
    CHILD_LOOP_CLIPS=0x2e,        # vtable +0x1c (obj, 0x2e, 0, &child) 0x08133538..40 (hold)
    CHILD_SEQUENCES=0x18,         # vtable +0x1c (obj, 0x18, i, &child) 0x08136A68..70 (hold)
    ENGINE_PROP_CLIP=0x63,        # movs r2,#0x63; bl ENGINE_PROPERTY 0x0813804E / 56 (hold)
    ENGINE_PROP_RUN=0x64,         # movs r2,#0x64; bl ENGINE_PROPERTY 0x08138030 / 36 (hold)
    EV_BUTTON=0x73,               # consumer 0xAF case movs r1,#0x73 0x08141028; BUTTON_EVENT cmp #0x73 0x08144762 (hold)
    EV_ENCODER=0xcf,              # DRUM_EVENT cmp r3,#0xcf 0x0815F42E (stepui)
    EV_MIXER_PAD_MODE=0x10d,      # MIXER_EVENT case 0x10D: toggle pane+0x9A08, movw r2,#0x10d 0x0814D848 (1.6.1 0x10C) (hold)
    EV_TAB_AUTOMATION=0x4b,       # DRUM_EVENT tbb 0x47.., templates (stepui)
    EV_TAB_EVENT=0x49,            # DRUM_EVENT tbb 0x47.., templates (stepui)
    EV_TAB_SEQUENCE=0x4a,         # DRUM_EVENT tbb 0x47.., templates (stepui)
    EV_TRACK_PROPERTY=0xd3,       # MIXER_EVENT movs r0,#0xd3 0x0814D792 (hold)
    HEADER_ACTIONS=0x6,           # HEADER_BUILD record loop root+0xA44..+0xF24 step 0xD0 (stepui)
    HEADER_ACTION_EMPTY=0x20,     # ldrb [r6,#0x20] 0x08181E5C (stepui)
    HEADER_ACTION_GROUP=0x22,     # ldrb [r7,#0x22] 0x08181E36 (stepui)
    HEADER_ACTION_ID=0x2c,        # ldrh [r6,#0x2c] 0x08181E1E (stepui)
    HEADER_ACTION_LIT=0x21,       # ldrb [r6,#0x21] 0x08181E64 (stepui)
    HEADER_ACTION_SIZE=0x30,      # adds r7,#0x30 0x08181E2C (stepui)
    HEADER_APPLIED=0x34,          # memcpy(root+0x34, desc, 0x1DC) 0x08181F48 (stepui)
    HEADER_ARMED=0x1194,          # ldrb [sl,#0x194], sl = root+0x1000, 0x08181E4E (stepui)
    HEADER_BUTTONS=0x564,         # HEADER_ROOT_INIT addw r7,r4,#0x564 0x08182576 (stepui)
    HEADER_BUTTON_COUNT=0xc,      # 6 buttons (+0x564) + 6 tabs (+0xA44) (stepui)
    HEADER_BUTTON_HIDDEN=0x30,    # button ctor 0x0816C520 = 1.6.1's (stepui)
    HEADER_BUTTON_LABEL=0x6d,     # same widget code (stepui)
    HEADER_BUTTON_STRIDE=0xd0,    # adds r7,#0xd0 0x08182584 (stepui)
    HEADER_DESC_ACTIONS=0x8c,     # HEADER_BUILD add.w r7,r6,#0x8c 0x0818204A; templates +0x8C (stepui)
    HEADER_TPL_SEQ=0x8,           # template page bytes +0x1DA (stepui)
    HEADER_TPL_SEQ_ALT=0x14,      # template page bytes +0x1DA (stepui)
    HEADER_TPL_SEQ_DRUM=0x24,     # template page bytes +0x1DA (stepui)
    KEY_ENV_A=0x1e,               #  (stepui)
    KEY_ENV_D=0x1f,               #  (stepui)
    KEY_ENV_R=0x21,               #  (stepui)
    KEY_ENV_S=0x20,               #  (stepui)
    KEY_LFO_DEPTH=0x4c,           # registry init titles (stepui)
    KEY_LFO_RATE=0x4b,            # registry init titles (stepui)
    KEY_LFO_SHAPE=0x4a,           # registry init titles (stepui)
    KEY_LFO_SRATE=0x4f,           # registry init titles (stepui)
    KEY_LOOP_ROW=0x6f,            # registry 'Group Index' (both) (hold)
    KEY_SEL_CLIP=0x133,           # registry 'Selected Cell Pos' (both) (hold)
    KEY_SEQ_ROW=0x73,             # registry 'Disp Pos' (both); GET_PROPERTY(child,0x73) 0x08144846 (hold)
    MODEL_CTX=0x13c,              # NAVIGATE add.w r2,r4,#0x13c 0x08143FBA; button INST case ldr [r0,#0x13c] 0x081447DE (hold)
    MODEL_MIXER_MODE=0x45cc,      # MIXER_PAD_MODE strb.w [model+0x4000+0x5cc] 0x0813D426 (1.6.1 +0x3C7C) (hold)
    MODEL_PAD_LEDS=0x19d0,        # FLUSH_PAD_LIGHTS(r4 + 0x19c0 + 0x10) 0x08140ECA; 14/14 add.w #0x19c0 sites then +0x10 (1.6.1 +4) (hold)
    MODEL_PAGE=0x12c,             # NAVIGATE strb r5,[r4,#0x12c] 0x08143F1C (stepui)
    MODEL_PREV_PAGE=0x13a,        # NAVIGATE strb.w r3,[r4,#0x13a] 0x08143F18 (hold)
    MODEL_SCENE=0x148,            # NAVIGATE add.w r2,r4,#0x148 0x0814417A (1.6.1 0x08142522) (hold)
    MODEL_SEQ=0x144,              # NAVIGATE ldr.w r3,[r4,#0x144] 0x081442B2; SEQ case [r6,#0x144] 0x081449DA (hold)
    MODEL_WORK=0x45d0,            # SEQ_COPY dest add.w #0x4580 + #0x50 (7 callers) (stepui)
    PAGE_INST=0x7,                # INST case 0x081447D4 / D8, navigate(0x14) 0x08144830 (hold)
    PAGE_INST_ALT1=0x9,           # INST case 0x081447D4 / D8, navigate(0x14) 0x08144830 (hold)
    PAGE_INST_ALT2=0x14,          # INST case 0x081447D4 / D8, navigate(0x14) 0x08144830 (hold)
    PAGE_LAUNCH=0x1e,             # LAUNCH case movs r1,#0x1e 0x08144AF8 (hold)
    PAGE_MIXER=0x1f,              # MIXER case movs r1,#0x1f 0x08144AB4 (hold)
    PAGE_SEQ=0x17,                # template page bytes (stepui)
    PAGE_SEQ_ALT=0x1a,            # template page bytes (stepui)
    PAGE_SEQ_DRUM=0x27,           # template page bytes (stepui)
    PAGE_TRACKS=0x2,              # TRACKS case movs r1,#2 0x081447C8 (hold)
    TYPE_LOOP=0x11,               # SEQ case cmp r0,#0x11 0x0814498C (hold)
    TYPE_ONESHOT=0xe,             # cmp r0,#0xe after vtable[0] 0x08139C4C (5x) (stepui)
    UI_CODE_ENCODER=0xab,         # consumer table entry 0xAB -> 0x08140FCE (posts 0xAD) (hold)
    UI_CODE_PRESS=0xaf,           # 0xAF -> 0x08141024 (BUTTON_EVENT 0x73) (hold)
    UI_CODE_RELEASE=0xb0,         # 0xB0 -> 0x08141042 (BUTTON_EVENT 0x74) (hold)
)}

EXPECTED = {"stock_161": STOCK_161, "stock_168": STOCK_168}

# S3: stock behaviour that differs between the bases and that test_s3_m7.py checks by running each base's
# own code. Written from the disassembly, not from the tables or from a run (scratch rc18/impl).
BEHAVIOUR = {
    "stock_161": dict(
        # pending note-off pass: timing = (block start + samples per block) - off (mirrored in the block):
        # 0x080605C0 subs r2, r2, r1 with r2 = clock + ticks per block, r1 = the off time
        NOTE_OFF_TIMING="mirrored",
        PAD_NOTE_MAX=127,                 # note clamp 0x080A1E40: usat r0, #7, r0
        # loop-boundary calls in the player's count-on mode (track byte +0x30 set), 320 steps of 60 ticks
        # from a restart: only the restart's (no wrap of the step count)
        LOOP_CALLS_COUNT_MODE_320=1,
    ),
    "stock_168": dict(
        # 0x08060960 subs r2, r2, r0 (off - block start); 0x0806096A bic.w r2, r2, r2, asr #31 (>= 0)
        NOTE_OFF_TIMING="offset",
        PAD_NOTE_MAX=163,                 # 0x080A25E6 cmp r0, #0xa3; movge r0, #0xa3; bic.w r0, r0, r0, asr #31
        # the step count wraps after 256 steps (play count), with a loop-boundary call: restart + 1
        LOOP_CALLS_COUNT_MODE_320=2,
    ),
}
