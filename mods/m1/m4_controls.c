/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Hold shortcuts: M4 UI side.
 *
 * Ported from bento-community-lab's K16 hold controls (k16_work/bento_controls/
 * controls.c, GPL-3.0-or-later) plus CH8's "an encoder turn counts as use"
 * (eu4_work/integration/fx_ui.c). Behaviour as in Lab's CH10:
 *   - hold TRACKS + pad: top row = that track's instrument page, bottom row =
 *     its sequencer (the playing clip if any);
 *   - hold LAUNCH + pad: bottom row starts the track's selected clip, top row
 *     stops it;
 *   - hold MIXER: solo/mute shown; top row = solo, bottom row = mute;
 *   - LAUNCH / MIXER return to the previous screen on release if the hold was
 *     used (pad or encoder); a plain tap stays, as on stock. TRACKS stays on the
 *     destination;
 *   - on the drum sequencer, hold INST, pick a sound, release: back to the
 *     sequencer with the new sound selected (no pick: stay in INST).
 * Removed from Lab's version: PROJ as Shift (PROJ+PLAY/REC, lock targets, PROJ +
 * pad drum mute) and the parameter-lock calls. State is reset once per M4 boot
 * generation instead of by Lab's UI startup hook (which would also clear our
 * diagnostic RAM).
 *
 * Requests arrive from the M7 pad hook (m7_controls_input.s) as UI event 0xFE.
 * Native button ids: 2 TRACKS, 3 LAUNCH, 5 MIXER, 10 INST, 11 SEQ. Model fields
 * (stock 1.6.1): +0x12c page, +0x13a previous page, +0x13c..0x148 contexts.
 */
#include "diag_shared.h"

typedef void *Ptr;
#define FN(a, t) ((t)((a) | 1u))
#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define B(p, o) (*(volatile u8 *)((u8 *)(p) + (o)))

#define NATIVE_GET_PROPERTY 0x0812dc74u
#define NATIVE_NAVIGATE 0x081420ecu
#define NATIVE_BUTTON_EVENT 0x08142afcu
#define NATIVE_FIND_TRACK 0x08133960u
#define NATIVE_SELECT_TRACK 0x0813bc3cu
#define NATIVE_PLAYING_STATUS 0x081322acu
#define NATIVE_SELECT_SEQUENCE 0x08136bd4u
#define NATIVE_ENGINE_PROPERTY 0x0810b5c8u
#define NATIVE_MIXER_EVENT 0x0814baeau
#define NATIVE_MIXER_RENDER 0x0814b630u
#define NATIVE_RESOLVE_OBJECT 0x0812d80eu
#define NATIVE_TRACK_PROPERTY_EVENT 0x08131ae0u
#define NATIVE_MIXER_PAD_MODE 0x0813bc24u
#define NATIVE_FLUSH_PAD_LIGHTS 0x081257f0u
#define NATIVE_CLEAR_PAD_HIGHLIGHTS 0x081257ccu
#define ENGINE (*(Ptr *)0x3000f650u)

typedef struct { u32 code, context, key, value, extra; } Event;
typedef struct { u32 magic, button, used, await_press, page, track, previous, ctx[4], generation; } State;
typedef struct { u32 active, track, sequence, scene, previous, picked; } InstHold;
typedef struct { u32 magic, active, generation, old_mode; } MixerUI;
typedef struct { u32 active, old_mode; } MixerLights;
typedef struct { u32 magic, gen; } Boot;
#define S ((volatile State *)0x24048cc0u)          /* +4 button, +8 used: read by name in Lab's CH8 code */
#define INST ((volatile InstHold *)0x24048cf0u)    /* +0x14 picked: written by the selection observer */
#define BOOT ((volatile Boot *)0x24048d10u)
#define UI ((volatile MixerUI *)0x24048d20u)
#define LIGHTS ((volatile MixerLights *)0x24048d40u)
#define STATE_MAGIC 0x354d5442u
#define UI_MAGIC 0x35554942u
#define BOOT_MAGIC 0x31544342u /* "BCT1" */
_Static_assert(0x24048d40u + sizeof(MixerLights) <= 0x24048d80u, "controls state reaches the inspect-a-step state");

/* Reset all controls state once per M4 boot (this RAM survives a reset). */
static void init(void) {
    u32 gen = CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u;
    if (BOOT->magic == BOOT_MAGIC && BOOT->gen == gen) return;
    for (volatile u32 *p = (volatile u32 *)0x24048cc0u; p < (volatile u32 *)0x24048d48u; p++) *p = 0;
    S->magic = STATE_MAGIC;
    BOOT->gen = gen;
    BOOT->magic = BOOT_MAGIC;
}

/* Strike velocity on/off: the drum step view's hamburger menu has a 5th item
 * (build.py) whose label points at STRIKE_SET->label and whose command id is
 * STRIKE_TOGGLE_EVENT; selecting it bubbles up to this consumer. The setting
 * survives warm reboots (not power-off) and defaults to on. */
static void strike_label(void) {
    const char *t = STRIKE_SET->on ? "Strike Vel: On" : "Strike Vel: Off";
    u32 i = 0;
    for (; t[i] && i < sizeof(STRIKE_SET->label) - 1; i++) STRIKE_SET->label[i] = t[i];
    for (; i < sizeof(STRIKE_SET->label); i++) STRIKE_SET->label[i] = 0;
}
static void strike_init(void) {
    if (STRIKE_SET->magic == STRIKE_SET_MAGIC) return;
    STRIKE_SET->on = 1;
    strike_label();
    STRIKE_SET->magic = STRIKE_SET_MAGIC;
}

static void mixer_pad_mode(Ptr m, u32 enabled) {
    Event e = {0x10c, 0, enabled, 0, 0};
    FN(NATIVE_MIXER_PAD_MODE, void (*)(Ptr, Event *))(m, &e);
}
static void flush_pad_lights(Ptr m) { FN(NATIVE_FLUSH_PAD_LIGHTS, void (*)(Ptr))((u8 *)m + 0x19c4); }
static u32 get(Ptr obj, u32 key) { return FN(NATIVE_GET_PROPERTY, u32 (*)(Ptr, u32))(obj, key); }
static void nav(Ptr m, u32 page) { FN(NATIVE_NAVIGATE, void (*)(Ptr, u32, u32, u32, u32))(m, page, 0, 0, 0); }
static void button(Ptr m, u32 id) {
    Event e = {0x73, 0, id, 0, 0};
    FN(NATIVE_BUTTON_EVENT, void (*)(Ptr, Event *))(m, &e);
}
static Ptr instrument(Ptr m, u32 track, u32 *h) {
    if (!FN(NATIVE_FIND_TRACK, u32 (*)(Ptr, u32, u32 *))(m, track, h)) return 0;
    Ptr obj = 0;
    if (!FN(NATIVE_RESOLVE_OBJECT, u32 (*)(Ptr, u32, Ptr *))((Ptr)W(m, 0), *h, &obj)) return 0;
    return obj;
}
static u32 type(Ptr obj) { return ((u32 (*)(Ptr))W((Ptr)W(obj, 0), 0))(obj); }

/* Snapshot the screen, then press the button natively. 0 if a dialog blocked it. */
static int save(Ptr m, u32 id, u32 late) {
    if (LIGHTS->active) { mixer_pad_mode(m, LIGHTS->old_mode); LIGHTS->active = 0; }
    if (id == 5) LIGHTS->old_mode = B(m, 0x3c7c);
    S->generation++; S->button = id; S->used = 0; S->await_press = late; S->page = B(m, 0x12c);
    S->track = get((u8 *)m + 4, 0x131); S->previous = B(m, 0x13a);
    for (u32 i = 0; i < 4; i++) S->ctx[i] = W(m, 0x13c + i * 4);
    button(m, id);
    if (B(m, 0x12c) != (id == 2 ? 2 : id == 3 ? 0x1e : 0x1f)) { S->button = 0; S->used = 0; S->await_press = 0; return 0; }
    if (id == 5) { LIGHTS->active = 1; mixer_pad_mode(m, 1); flush_pad_lights(m); }
    return 1;
}
static void restore(Ptr m) {
    if (get((u8 *)m + 4, 0x131) != S->track)   /* do not reselect an unchanged track */
        FN(NATIVE_SELECT_TRACK, void (*)(Ptr, u32))(m, S->track);
    for (u32 i = 0; i < 4; i++) W(m, 0x13c + i * 4) = S->ctx[i];
    nav(m, S->page);
    B(m, 0x13a) = (u8)S->previous;
}
static int playing(Ptr m, u32 track, int *row, int *queued) {
    return FN(NATIVE_PLAYING_STATUS, int (*)(Ptr, u32, int *, int *))(m, track, row, queued);
}
static void choose_seq(Ptr m, u32 h, u32 row) { FN(NATIVE_SELECT_SEQUENCE, void (*)(Ptr, u32 *, u32, u32))(m, &h, row, 0); }

static void tracks(Ptr m, u32 track, u32 seq) {
    FN(NATIVE_SELECT_TRACK, void (*)(Ptr, u32))(m, track);
    u32 h = 0;
    Ptr obj = instrument(m, track, &h);
    if (!obj) return;
    if (seq && type(obj) != 0x11) {          /* 0x11 = loop track: no sequencer, open INST */
        int row = -1, q = -1;
        if (playing(m, track, &row, &q) != -1 && row >= 0 && row < 16) choose_seq(m, h, (u32)row);
        button(m, 11);
    } else {
        button(m, 10);
    }
}
static void mixer(Ptr m, u32 track, u32 mute) {
    u32 h = 0;
    Ptr obj = instrument(m, track, &h);
    if (!obj) return;
    u32 key = mute ? 0x13d : 0x13e;
    Event e = {0xd3, h, key, !get(obj, key), 0};
    FN(NATIVE_TRACK_PROPERTY_EVENT, void (*)(Ptr, Event *))(m, &e);
    mixer_pad_mode(m, 1);
    flush_pad_lights(m);
}
static Ptr clip(Ptr obj, u32 row) {
    /* Typed native enumeration; does not create or select any clip. */
    u32 kind = type(obj) == 0x11 ? 0x2e : 0x18;
    for (u32 i = 0; i < 256; i++) {
        Ptr child = 0;
        u32 ok = ((u32 (*)(Ptr, u32, u32, Ptr *))W((Ptr)W(obj, 0), 0x1c))(obj, kind, i, &child);
        if (!ok) return 0;
        if (child && get(child, kind == 0x2e ? 0x6f : 0x73) == row) return child;
    }
    return 0;
}
static void launch(Ptr m, u32 track, u32 start) {
    u32 h = 0;
    Ptr obj = instrument(m, track, &h);
    if (!obj) return;
    int row = -1, q = -1;
    int pos = playing(m, track, &row, &q);
    if (!start) {
        if (pos == -1 && q < 0) return;
        FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, 0x64, 0);
        return;
    }
    u32 selected = get(obj, 0x133);
    if (selected >= 16 || !clip(obj, selected)) return;
    if ((pos != -1 && row == (int)selected) || q == (int)selected) return;
    FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, 0x64, 1);
    FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, 0x63, selected);
}

/* Lab's seq_lights_clean: after returning to the drum sequencer, clear the
 * stale instrument-playback LED layer (+12) the native updater stops touching. */
static void seq_lights_clean(Ptr m) {
    if (B(m, 0x12c) != 0x27) return;
    u8 *led = (u8 *)m + 0x19c4;
    u32 changed = 0;
    for (u32 i = 0; i < 16; i++)
        if (led[i * 16 + 12]) { led[i * 16 + 12] = 0; changed = 1; }
    if (changed) flush_pad_lights(m);
}

static int inst_hold(Ptr m, u32 code, u32 index) {
    if (code == 0xaf && index != 10) INST->active = 0;
    if (index != 10 || (code != 0xaf && code != 0xb0)) return 0;
    if (code == 0xaf) {
        if (INST->active) return 1;
        if (B(m, 0x12c) != 0x27) return 0;         /* drum sequencer only */
        u32 track = get((u8 *)m + 4, 0x131), h = 0;
        Ptr obj = instrument(m, track, &h);
        if (!obj || type(obj) != 0x0e) return 0;
        INST->track = track; INST->sequence = W(m, 0x144); INST->scene = W(m, 0x148);
        INST->previous = B(m, 0x13a); INST->picked = 0;
        button(m, 10);
        if (B(m, 0x12c) == 7) INST->active = 1;
        return 1;
    }
    if (!INST->active) return 0;
    INST->active = 0;
    if (!INST->picked) return 1;
    if (get((u8 *)m + 4, 0x131) != INST->track) return 1;
    u32 page = B(m, 0x12c);
    if (page != 7 && page != 9 && page != 0x14) return 1;
    /* Keep the newly selected cell (13C/140); restore sequence and scene. */
    W(m, 0x144) = INST->sequence; W(m, 0x148) = INST->scene;
    FN(NATIVE_CLEAR_PAD_HIGHLIGHTS, void (*)(Ptr))((u8 *)m + 0x19c4);
    nav(m, 0x27);
    B(m, 0x13a) = (u8)INST->previous;
    if (B(m, 0x12c) == 0x27) { seq_lights_clean(m); flush_pad_lights(m); }
    return 1;
}

/* UI event consumer (0x0813F98C). Returns 1 when the event was handled. */
__attribute__((used, retain)) int controls_dispatch(Ptr m, u32 code, u32 context, u32 index, const u32 *record) {
    init();
    strike_init();
    if (code == STRIKE_TOGGLE_EVENT) {
        STRIKE_SET->on = !STRIKE_SET->on;
        strike_label();
        return 1;
    }
    /* CH8: an encoder turn (0xAB) while holding LAUNCH or MIXER counts as use. */
    if (code == 0xab && (S->button == 3 || S->button == 5)) S->used = 1;
    if (inst_hold(m, code, index)) return 1;
    if ((code == 0xaf || code == 0xb0) && (index == 2 || index == 3 || index == 5)) {
        if (code == 0xaf) {
            if (S->button == index && S->await_press) { S->await_press = 0; return 1; }
            save(m, index, 0);
            return 1;
        }
        if (S->button == index) {
            u32 returning = S->used && index != 2;
            if (index == 5 && LIGHTS->active) {
                u32 mode = LIGHTS->old_mode;
                /* A plain tap stays on MIXER with the native screen's previous mode. */
                if (!returning && UI->magic == UI_MAGIC && UI->active && UI->generation == S->generation) mode = UI->old_mode;
                mixer_pad_mode(m, mode);
                LIGHTS->active = 0;
            }
            if (returning) restore(m);
            if (index == 5) flush_pad_lights(m);
            S->button = 0; S->used = 0; S->await_press = 0;
        }
        return 1;
    }
    if (code != 0xfe || context != 0xfffffffeu || index > 7 || record[1] != 0x314d5442u || record[5] != 0x31525442u) return 0;
    u32 action = record[4];
    if (action < 1 || action > 6) return 0;
    INST->active = 0;
    u32 id = action <= 2 ? 2 : action <= 4 ? 3 : 5;
    if (S->button != id && !save(m, id, 1)) return 1;
    S->used = 1;
    if (action <= 2) tracks(m, index, action == 2);
    else if (action <= 4) launch(m, index, action == 4);
    else mixer(m, index, action == 6);
    return 1;
}

/* Native mixer render (vtable 0x0819DD20): show Solo/Mute while MIXER is held,
 * using the same toolbar action as a touchscreen press. */
static void mixer_mode(Ptr pane, u32 wanted) {
    if (B(pane, 0x9a08) != (u8)wanted) {
        Event e = {0x10c, 0, 0, 0, 0};
        FN(NATIVE_MIXER_EVENT, u32 (*)(Ptr, Event *))(pane, &e);
    }
}
__attribute__((used, retain)) u32 mixer_render(Ptr pane, Ptr canvas) {
    init();
    if (UI->magic != UI_MAGIC) { UI->active = 0; UI->magic = UI_MAGIC; }
    u32 held = S->button == 5;
    if (UI->active && (!held || UI->generation != S->generation)) {
        mixer_mode(pane, UI->old_mode);
        UI->active = 0;
    }
    if (held && !UI->active) {
        UI->old_mode = B(pane, 0x9a08); UI->generation = S->generation; UI->active = 1;
        mixer_mode(pane, 1);
    }
    return FN(NATIVE_MIXER_RENDER, u32 (*)(Ptr, Ptr))(pane, canvas);
}
