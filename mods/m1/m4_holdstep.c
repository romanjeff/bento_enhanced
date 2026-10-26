/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Inspect a step (hold a step to edit it without toggling it): M4 UI side.
 *
 * Ported from bento-community-lab's T2 module (k16_work/bento_controls/
 * holdstep.c, GPL-3.0-or-later). Same behaviour, with two changes for stock:
 *   - no parameter-lock call from step_press (Lab's locks are not ported);
 *   - the inspection state is tagged with our M4 boot generation instead of
 *     being cleared by Lab's startup hook (this RAM survives a reset).
 *
 * Native step views keep a "held pads" array (view+0x11420, 32 timestamps).
 * Stock edits (velocity / condition / timing encoders) apply to held steps,
 * and releasing a step after a short press toggles it. We add:
 *   - remember the last pressed/touched step that holds a note (step_press,
 *     step_touch), so edits keep applying to it after release;
 *   - step_match / step_selected: when no pad is held, the remembered step
 *     counts as selected (stock's own held-pad logic runs first otherwise);
 *   - step_edit: after any encoder edit, age the held entries past the
 *     long-hold threshold so the release does not toggle the step off.
 * Notes are never created or deleted here; stock does all note writes.
 *
 * View fields (stock 1.6.1): +0x113dc note container, +0x113e4 sequence,
 * +0x113f4 first visible step, +0x113f8 step count, +0x11400 drum/row,
 * +0x114a4 touched step index. 0x30035f40 = UI millisecond tick.
 */
#include "diag_shared.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define FN(a, t) ((t)((a) | 1u))

#define STEP_HAS_NOTE 0x0815bcb4u  /* stock: (view, visible index) -> note at that step? */
#define ORIG_STEP_MATCH 0x081B6600u /* build.py: stock step_match prologue + resume */
#define UI_TICK_MS (*(volatile u32 *)0x30035f40u)
#define LONG_HOLD_MS 1000u

typedef struct { u32 magic, view, sequence, drum, step, active, gen, r; } Inspect;
#define S ((volatile Inspect *)0x24048D80u)
#define MAGIC 0x32495042u
_Static_assert(0x24048D80u + sizeof(Inspect) <= 0x24048DA0u, "inspection state past the M7 stack bottom");

static u32 boot_gen(void) { return CFG->magic == DIAG_MAGIC ? CFG->r[0] : 0u; }

static int held(void *v) {
    for (u32 i = 0; i < 32; i++)
        if (W(v, 0x11420 + i * 4)) return 1;
    return 0;
}

/* Visible index of the remembered step in this view, or -1. */
static int local(void *v) {
    if (S->magic != MAGIC || S->gen != boot_gen() || !S->active || S->view != (u32)v || !W(v, 0x113e4) ||
        S->sequence != W(v, 0x113e4) || S->drum != W(v, 0x11400))
        return -1;
    u32 base = W(v, 0x113f4);
    if (S->step < base || S->step - base >= 32 || S->step >= W(v, 0x113f8)) return -1;
    return (int)(S->step - base);
}

static void remember(void *v, u32 i) {
    S->magic = MAGIC;
    S->gen = boot_gen();
    S->active = 0;
    if (i > 31 || !W(v, 0x113e4) || !W(v, 0x113dc)) return;
    if (!FN(STEP_HAS_NOTE, int (*)(void *, u32))(v, i)) return;  /* empty step: forget */
    S->view = (u32)v;
    S->sequence = W(v, 0x113e4);
    S->drum = W(v, 0x11400);
    S->step = W(v, 0x113f4) + i;
    S->active = 1;
}

__attribute__((used, retain)) void step_press(void *v, u32 encoded) { remember(v, encoded - 0x4508u); }

__attribute__((used, retain)) void step_touch(void *v) { remember(v, W(v, 0x114a4)); }

__attribute__((used, retain)) int step_match(void *v, u32 absolute) {
    if (held(v)) return FN(ORIG_STEP_MATCH, int (*)(void *, u32))(v, absolute);
    return local(v) >= 0 && S->step == absolute;
}

__attribute__((used, retain)) int step_selected(void *v) {
    if (held(v)) return 1;
    int i = local(v);
    return i >= 0 && W(v, 0x113dc) && FN(STEP_HAS_NOTE, int (*)(void *, u32))(v, (u32)i);
}

/* Keep nonzero held entries so native multi-step editing still works, but make
 * the release take its native long-hold path (no toggle) after any edit. */
__attribute__((used, retain)) void step_edit(void *v) {
    u32 past = UI_TICK_MS - (LONG_HOLD_MS + 1u);
    if (!past) past = 0xffffffffu;
    for (u32 i = 0; i < 32; i++)
        if (W(v, 0x11420 + i * 4)) W(v, 0x11420 + i * 4) = past;
}
