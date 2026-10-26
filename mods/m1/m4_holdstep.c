/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Inspect a step: edit a held step without the release toggling it off (M4 UI).
 *
 * Stock already selects held steps: a pad press or a screen touch stamps the
 * step into the view's held list (view+0x11420, 32 tick stamps), the encoders
 * then show and edit the held step(s), and on release the step is toggled if
 * it was held for less than 1000 ms. So a quick hold-and-edit also removed the
 * step. We hook the three step-editor encoder handlers (velocity, condition /
 * probability, timing): after any edit, the held stamps are aged past the
 * long-hold threshold, so the release takes stock's long-hold path (no toggle).
 *
 * Derived from bento-community-lab's T2 holdstep.c (GPL-3.0-or-later), keeping
 * only its step_edit. Lab also kept the last held step selected after release;
 * we deliberately do not: after release the encoders show the defaults for new
 * steps again, as on stock.
 */
#include "diag_shared.h"

#define W(p, o) (*(volatile u32 *)((u8 *)(p) + (o)))
#define UI_TICK_MS (*(volatile u32 *)0x30035f40u)
#define LONG_HOLD_MS 1000u

/* Keep nonzero held entries so native multi-step editing still works, but make
 * the release take its native long-hold path (no toggle) after any edit. */
__attribute__((used, retain)) void step_edit(void *v) {
    u32 past = UI_TICK_MS - (LONG_HOLD_MS + 1u);
    if (!past) past = 0xffffffffu;   /* 0 means "not held" */
    for (u32 i = 0; i < 32; i++)
        if (W(v, 0x11420 + i * 4)) W(v, 0x11420 + i * 4) = past;
}
