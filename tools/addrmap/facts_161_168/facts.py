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

REVIEWED = {}

# Registry keys missing from registry_keys.txt (entries without a title to match by): the measured
# rule (plan: "0x002..0x13A unchanged, 0x13C..0x1D8 move down 2, 0x1D9..0x1F2 down 1"). (lo, hi, delta)
KEY_RANGES = ((0x002, 0x13A, 0), (0x13C, 0x1D8, -2), (0x1D9, 0x1F2, -1))

FACT_NEEDS_REVIEW = {}

REVIEW_OURS = {
    "ACT_STRIKE": "stock's header action ids grew in 1.6.8 (0x1041 -> 0x1044, 0x102B -> 0x102D, ...): "
                  "confirm 0x10F0..0x10F3 are still unused by stock",
    "ACT_ROWS": "with ACT_STRIKE", "ACT_MOD": "with ACT_STRIKE", "ACT_NM": "with ACT_STRIKE",
    "UI_REQUEST": "confirm 0xFE is still no stock UI consumer code (the consumer's table bound is 0xB3 in both)",
    "D2_SLICE": "S2: 1.6.8 owns D2 0x3003C000..0x30040000 (two 8 KB SAI DMA buffers zeroed at boot, M7 MPU R14); "
                "the slice moves (plan: AXI 0x2404CDC0..0x24050000, M4 access aligned only)",
    "D2_SLICE_END": "S2: moves with D2_SLICE",
    "RAMCHK_AXI": "S2: the AXI block becomes our slice; redraw the RAMCHK blocks",
    "RAMCHK_AXI_END": "S2: with RAMCHK_AXI",
    "RAMCHK_D2A": "S2: the M4 stack moved up (vector SP 0x3003AAA8): this block would overwrite it; drop it",
    "RAMCHK_D2B": "S2: stock's D2 now; drop it",
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
    "H_INPUT_SITE": "S1 review: same prologue; only key immediates 0x18E->0x18C, 0x18C->0x18A, field 0xEA5->0xEA4 and "
                    "bls->blo differ; h_input reads only ev[0] types: can be cleared",
    "STALL_PROGRESS_SITE": "S1 review: bl target verified; the stub takes no stack arguments: can be cleared",
    "STALL_CHECK_SITE": "S1 review: bl target verified; MSG_PUMP takes only r0: can be cleared",
    "M4_WD_SITE": "S1 review: IRQ30 handler same shape (ldr r0, =0x3001fd28; three bl; then the supervisor): can be cleared",
    "ALLOC_BUMP_SHA": "S1 review: same left/cursor semantics with registers renamed: accept 47ab21ca...",
    "MPU_SETUP": "S1 review: R8 (0xC4000000, 32 MB, 0x01010001) unchanged apart from a register swap; read the added "
                 "R14 and record the new sha",
    "LOAD_RING": "S1 review: no literal in either image; base literal 0x10040000 unchanged next to H_RING (0x08082F62), "
                 "constructor identical: find the +0x51BC field from the code that writes it",
    "AUDIO_DMA_EVENTS": "S1 review: its object 0x2401D320 has 7 literals in each image; the field is +0x1C",
    "PANEL_BOOTED": "S1 review: the M7 .bss globals checked all have delta 0; confirm through PACKET_VALID (unchanged)",
    "PANEL_CRC": "as PANEL_BOOTED", "PANEL_TYPE": "as PANEL_BOOTED", "PANEL_BUTTONS": "as PANEL_BOOTED",
    "MODEL_MIXER_MODE": "S1 review: measured 0x45CC (+0x950) from MIXER_PAD_MODE and NAVIGATE",
    "MODEL_WORK": "S1 review: probably 0x45D0", "MODEL_PAD_LEDS": "S1 review: deltas are not uniform; measure",
    "MODEL_PAGE": "S1 review: NAVIGATE shows no change at 0x12C..0x148", "MODEL_PREV_PAGE": "as MODEL_PAGE",
    "MODEL_CTX": "as MODEL_PAGE", "MODEL_SEQ": "as MODEL_PAGE", "MODEL_SCENE": "as MODEL_PAGE",
    "UI_WALK_CTX": "S1 review: the inference is weak (the neighbouring literals are odd values)",
    "D2_SLICE": "S1 review: the new AXI slice 0x2404CDC0..0x24050000 is 0x3240 B; check our highest used offset "
                "(the old RAMCHK_D2B sat at slice + 0x2000)",
}
