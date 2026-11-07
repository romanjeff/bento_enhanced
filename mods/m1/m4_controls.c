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
 * Requests arrive from the M7 pad hook (m7_controls_input.s) as UI event S_UI_REQUEST (0xFE).
 * Native button ids (S_BTN_*; 1.6.1: 2 TRACKS, 3 LAUNCH, 5 MIXER, 10 INST, 11 SEQ), pages
 * (S_PAGE_*), event codes (S_EV_*, S_UI_CODE_*) and model fields (S_MODEL_*; 1.6.1: +0x12c page,
 * +0x13a previous page, +0x13c..0x148 contexts) come from the base table.
 */
#include "diag_shared.h"

typedef void *Ptr;
#define FN(a, t) ((t)((a) | 1u))
#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define B(p, o) (*(volatile u8 *)((u8 *)(p) + (o)))

#define NATIVE_GET_PROPERTY S_GET_PROPERTY
#define NATIVE_NAVIGATE S_NAVIGATE
#define NATIVE_BUTTON_EVENT S_BUTTON_EVENT
#define NATIVE_FIND_TRACK S_FIND_TRACK
#define NATIVE_SELECT_TRACK S_SELECT_TRACK
#define NATIVE_PLAYING_STATUS S_PLAYING_STATUS
#define NATIVE_SELECT_SEQUENCE S_SELECT_SEQUENCE
#define NATIVE_ENGINE_PROPERTY S_ENGINE_PROPERTY
#define NATIVE_MIXER_EVENT S_MIXER_EVENT
#define NATIVE_MIXER_RENDER S_MIXER_RENDER
#define NATIVE_RESOLVE_OBJECT S_RESOLVE_OBJECT
#define NATIVE_TRACK_PROPERTY_EVENT S_TRACK_PROPERTY_EVENT
#define NATIVE_MIXER_PAD_MODE S_MIXER_PAD_MODE
#define NATIVE_FLUSH_PAD_LIGHTS S_FLUSH_PAD_LIGHTS
#define NATIVE_CLEAR_PAD_HIGHLIGHTS S_CLEAR_PAD_HIGHLIGHTS
#define ENGINE (*(Ptr *)S_ENGINE_PTR)
#define BTN_TRACKS S_BTN_TRACKS
#define BTN_LAUNCH S_BTN_LAUNCH
#define BTN_MIXER S_BTN_MIXER
#define BTN_INST S_BTN_INST
#define BTN_SEQ S_BTN_SEQ
#define PAGE_SEQ_DRUM S_PAGE_SEQ_DRUM
#define CODE_ENCODER S_UI_CODE_ENCODER
#define CODE_PRESS S_UI_CODE_PRESS
#define CODE_RELEASE S_UI_CODE_RELEASE

typedef struct { u32 code, context, key, value, extra; } Event;
typedef struct { u32 magic, button, used, await_press, page, track, previous, ctx[4], generation; } State;
typedef struct { u32 active, track, sequence, scene, previous, picked; } InstHold;
typedef struct { u32 magic, active, generation, old_mode; } MixerUI;
typedef struct { u32 active, old_mode; } MixerLights;
typedef struct { u32 magic, gen; } Boot;
#define S ((volatile State *)SLICE(0x8C0u))          /* +4 button, +8 used: read by name in Lab's CH8 code */
#define INST ((volatile InstHold *)SLICE(0x8F0u))    /* +0x14 picked: written by the selection observer */
#define BOOT ((volatile Boot *)SLICE(0x910u))
#define UI ((volatile MixerUI *)SLICE(0x920u))
#define LIGHTS ((volatile MixerLights *)SLICE(0x940u))
#define STATE_MAGIC 0x354d5442u
#define UI_MAGIC 0x35554942u
#define BOOT_MAGIC 0x31544342u /* "BCT1" */
_Static_assert(SLICE(0x940u) + sizeof(MixerLights) <= SLICE(0x980u), "controls state reaches the inspect-a-step state");

/* Reset all controls state once per M4 boot (this RAM survives a reset). */
static void init(void) {
    u32 gen = CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u;
    if (BOOT->magic == BOOT_MAGIC && BOOT->gen == gen) return;
    for (volatile u32 *p = (volatile u32 *)SLICE(0x8C0u); p < (volatile u32 *)SLICE(0x948u); p++) *p = 0;
    S->magic = STATE_MAGIC;
    BOOT->gen = gen;
    BOOT->magic = BOOT_MAGIC;
}

static void mixer_pad_mode(Ptr m, u32 enabled) {
    Event e = {S_EV_MIXER_PAD_MODE, 0, enabled, 0, 0};
    FN(NATIVE_MIXER_PAD_MODE, void (*)(Ptr, Event *))(m, &e);
}
static void flush_pad_lights(Ptr m) { FN(NATIVE_FLUSH_PAD_LIGHTS, void (*)(Ptr))((u8 *)m + S_MODEL_PAD_LEDS); }
static u32 get(Ptr obj, u32 key) { return FN(NATIVE_GET_PROPERTY, u32 (*)(Ptr, u32))(obj, key); }
static void nav(Ptr m, u32 page) { FN(NATIVE_NAVIGATE, void (*)(Ptr, u32, u32, u32, u32))(m, page, 0, 0, 0); }
static void button(Ptr m, u32 id) {
    Event e = {S_EV_BUTTON, 0, id, 0, 0};
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
    if (id == BTN_MIXER) LIGHTS->old_mode = B(m, S_MODEL_MIXER_MODE);
    S->generation++; S->button = id; S->used = 0; S->await_press = late; S->page = B(m, S_MODEL_PAGE);
    S->track = get((u8 *)m + 4, S_KEY_SEL_TRACK); S->previous = B(m, S_MODEL_PREV_PAGE);
    for (u32 i = 0; i < 4; i++) S->ctx[i] = W(m, S_MODEL_CTX + i * 4);
    button(m, id);
    if (B(m, S_MODEL_PAGE) != (id == BTN_TRACKS ? S_PAGE_TRACKS : id == BTN_LAUNCH ? S_PAGE_LAUNCH : S_PAGE_MIXER)) { S->button = 0; S->used = 0; S->await_press = 0; return 0; }
    if (id == BTN_MIXER) { LIGHTS->active = 1; mixer_pad_mode(m, 1); flush_pad_lights(m); }
    return 1;
}
static void restore(Ptr m) {
    if (get((u8 *)m + 4, S_KEY_SEL_TRACK) != S->track)   /* do not reselect an unchanged track */
        FN(NATIVE_SELECT_TRACK, void (*)(Ptr, u32))(m, S->track);
    for (u32 i = 0; i < 4; i++) W(m, S_MODEL_CTX + i * 4) = S->ctx[i];
    nav(m, S->page);
    B(m, S_MODEL_PREV_PAGE) = (u8)S->previous;
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
    if (seq && type(obj) != S_TYPE_LOOP) {   /* loop track: no sequencer, open INST */
        int row = -1, q = -1;
        if (playing(m, track, &row, &q) != -1 && row >= 0 && row < 16) choose_seq(m, h, (u32)row);
        button(m, BTN_SEQ);
    } else {
        button(m, BTN_INST);
    }
}
static void mixer(Ptr m, u32 track, u32 mute) {
    u32 h = 0;
    Ptr obj = instrument(m, track, &h);
    if (!obj) return;
    u32 key = mute ? S_KEY_MUTE : S_KEY_SOLO;
    Event e = {S_EV_TRACK_PROPERTY, h, key, !get(obj, key), 0};
    FN(NATIVE_TRACK_PROPERTY_EVENT, void (*)(Ptr, Event *))(m, &e);
    mixer_pad_mode(m, 1);
    flush_pad_lights(m);
}
static Ptr clip(Ptr obj, u32 row) {
    /* Typed native enumeration; does not create or select any clip. */
    u32 kind = type(obj) == S_TYPE_LOOP ? S_CHILD_LOOP_CLIPS : S_CHILD_SEQUENCES;
    for (u32 i = 0; i < 256; i++) {
        Ptr child = 0;
        u32 ok = ((u32 (*)(Ptr, u32, u32, Ptr *))W((Ptr)W(obj, 0), 0x1c))(obj, kind, i, &child);
        if (!ok) return 0;
        if (child && get(child, kind == S_CHILD_LOOP_CLIPS ? S_KEY_LOOP_ROW : S_KEY_SEQ_ROW) == row) return child;
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
        FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, S_ENGINE_PROP_RUN, 0);
        return;
    }
    u32 selected = get(obj, S_KEY_SEL_CLIP);
    if (selected >= 16 || !clip(obj, selected)) return;
    if ((pos != -1 && row == (int)selected) || q == (int)selected) return;
    FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, S_ENGINE_PROP_RUN, 1);
    FN(NATIVE_ENGINE_PROPERTY, void (*)(Ptr, u32, u32, u32))(ENGINE, h, S_ENGINE_PROP_CLIP, selected);
}

/* Lab's seq_lights_clean: after returning to the drum sequencer, clear the
 * stale instrument-playback LED layer (+12) the native updater stops touching. */
static void seq_lights_clean(Ptr m) {
    if (B(m, S_MODEL_PAGE) != PAGE_SEQ_DRUM) return;
    u8 *led = (u8 *)m + S_MODEL_PAD_LEDS;
    u32 changed = 0;
    for (u32 i = 0; i < 16; i++)
        if (led[i * 16 + 12]) { led[i * 16 + 12] = 0; changed = 1; }
    if (changed) flush_pad_lights(m);
}

static int inst_hold(Ptr m, u32 code, u32 index) {
    if (code == CODE_PRESS && index != BTN_INST) INST->active = 0;
    if (index != BTN_INST || (code != CODE_PRESS && code != CODE_RELEASE)) return 0;
    if (code == CODE_PRESS) {
        if (INST->active) return 1;
        if (B(m, S_MODEL_PAGE) != PAGE_SEQ_DRUM) return 0;   /* drum sequencer only */
        u32 track = get((u8 *)m + 4, S_KEY_SEL_TRACK), h = 0;
        Ptr obj = instrument(m, track, &h);
        if (!obj || type(obj) != S_TYPE_ONESHOT) return 0;
        INST->track = track; INST->sequence = W(m, S_MODEL_SEQ); INST->scene = W(m, S_MODEL_SCENE);
        INST->previous = B(m, S_MODEL_PREV_PAGE); INST->picked = 0;
        button(m, BTN_INST);
        if (B(m, S_MODEL_PAGE) == S_PAGE_INST) INST->active = 1;
        return 1;
    }
    if (!INST->active) return 0;
    INST->active = 0;
    if (!INST->picked) return 1;
    if (get((u8 *)m + 4, S_KEY_SEL_TRACK) != INST->track) return 1;
    u32 page = B(m, S_MODEL_PAGE);
    if (page != S_PAGE_INST && page != S_PAGE_INST_ALT1 && page != S_PAGE_INST_ALT2) return 1;
    /* Keep the newly selected cell (13C/140); restore sequence and scene. */
    W(m, S_MODEL_SEQ) = INST->sequence; W(m, S_MODEL_SCENE) = INST->scene;
    FN(NATIVE_CLEAR_PAD_HIGHLIGHTS, void (*)(Ptr))((u8 *)m + S_MODEL_PAD_LEDS);
    nav(m, PAGE_SEQ_DRUM);
    B(m, S_MODEL_PREV_PAGE) = (u8)INST->previous;
    if (B(m, S_MODEL_PAGE) == PAGE_SEQ_DRUM) { seq_lights_clean(m); flush_pad_lights(m); }
    return 1;
}

/* UI event consumer (0x0813F98C). Returns 1 when the event was handled. */
__attribute__((used, retain)) int controls_dispatch(Ptr m, u32 code, u32 context, u32 index, const u32 *record) {
    init();
    /* CH8: an encoder turn while holding LAUNCH or MIXER counts as use. */
    if (code == CODE_ENCODER && (S->button == BTN_LAUNCH || S->button == BTN_MIXER)) S->used = 1;
    if (inst_hold(m, code, index)) return 1;
    if ((code == CODE_PRESS || code == CODE_RELEASE) && (index == BTN_TRACKS || index == BTN_LAUNCH || index == BTN_MIXER)) {
        if (code == CODE_PRESS) {
            if (S->button == index && S->await_press) { S->await_press = 0; return 1; }
            save(m, index, 0);
            return 1;
        }
        if (S->button == index) {
            u32 returning = S->used && index != BTN_TRACKS;
            if (index == BTN_MIXER && LIGHTS->active) {
                u32 mode = LIGHTS->old_mode;
                /* A plain tap stays on MIXER with the native screen's previous mode. */
                if (!returning && UI->magic == UI_MAGIC && UI->active && UI->generation == S->generation) mode = UI->old_mode;
                mixer_pad_mode(m, mode);
                LIGHTS->active = 0;
            }
            if (returning) restore(m);
            if (index == BTN_MIXER) flush_pad_lights(m);
            S->button = 0; S->used = 0; S->await_press = 0;
        }
        return 1;
    }
    if (code != S_UI_REQUEST || context != 0xfffffffeu || index > 7 || record[1] != 0x314d5442u || record[5] != 0x31525442u) return 0;
    u32 action = record[4];
    if (action < 1 || action > 6) return 0;
    INST->active = 0;
    u32 id = action <= 2 ? BTN_TRACKS : action <= 4 ? BTN_LAUNCH : BTN_MIXER;
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
        Event e = {S_EV_MIXER_PAD_MODE, 0, 0, 0, 0};
        FN(NATIVE_MIXER_EVENT, u32 (*)(Ptr, Event *))(pane, &e);
    }
}
__attribute__((used, retain)) u32 mixer_render(Ptr pane, Ptr canvas) {
    init();
    if (UI->magic != UI_MAGIC) { UI->active = 0; UI->magic = UI_MAGIC; }
    u32 held = S->button == BTN_MIXER;
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
