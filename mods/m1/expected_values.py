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
)}

EXPECTED = {"stock_161": STOCK_161, "stock_168": STOCK_168}
