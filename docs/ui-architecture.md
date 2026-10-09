# Bento's UI widget, event, and property architecture

A reference map of Bento's UI framework, confirmed across several
research rounds while chasing why a test patch's new encoder showed a
label but not a live value. The goal became broader than that one bug:
understanding this framework precisely enough to add future features the
way the firmware actually expects. This document is the organized result
— see `PROJECT_NOTES.md` (not public) for the chronological trace that
built it.

## The scene graph

Every widget/page is a node in an intrusive tree:

| Offset | Field |
|---|---|
| `+0x00` | vtable |
| `+0x18` | first child |
| `+0x1c` | last child |
| `+0x20` | next sibling |
| `+0x24` | prev sibling |
| `+0x2c` | parent |

Confirmed three independent ways: `AddChild` (`FUN_0816adc8`) appends to
the child list and sets `child+0x2c = parent`; `RemoveChild`
(`FUN_0816ade6`) unlinks and zeroes it; `PostEvent`/bubble
(`FUN_0816ae98`) reads `obj+0x2c` and walks upward.

**vtable slot `+0x30` is `HandleEvent(this, event)`.** `FindChildById` is
vtable slot `+0x14` (confirmed by `FUN_0812ba18`'s own code, exact
signature `obj->vtable[0x14](obj, objId, &out)`) — there is **no shared
base-class implementation**; every class examined implements its own.

### The event struct (20 bytes)

```
+0x00 u16 opcode
+0x02 u16 pad
+0x04 u32 objId
+0x08 u32 propId
+0x0c u32 value
+0x10 u32 reserved
```

### Bubbling semantics

`FUN_0816ae98(obj, event)` calls `(*obj->vtable[0x30])(obj, event)` and,
if that call returns **nonzero**, recurses with `obj->parent` as the new
target. **Correction to an earlier assumption**: the recursion continues
while the handler returns nonzero, not zero — verified directly in
disassembly (`cbnz r0, recurse`). An unhandled event keeps climbing the
tree; a handled one stops there.

### Known opcodes

| Opcode | Meaning | Notes |
|---|---|---|
| `0xcb` | Encoder turned | Carries the bound `objId`/`propId` and the new raw value |
| `0xd0` | Property commit | `{objId, propId, value}` — the generic write-intent event |
| `0x65` | "Re-fetch and redraw" | Value-less invalidate broadcast, parameter-ID-tagged |
| `0x62` | Dirty-flag | Marks a widget for redraw, no value |
| `0x10d`/`0x10e` | Page-specific forwards | Seen on Met-Gain's page; routes to a child or triggers its own refresh |
| `0x10f` | Tap Tempo button press | Page-specific |

## SessionMgr — the model root

A real, fixed singleton at SRAM **`0x30025a20`** — not an inferred
"root," confirmed three independent ways: a literal address load before
a call in widget glue; two other functions' first-argument usage; and
the firmware's own debug string `"SessionMgr::FinalizeBankLoading"`
naming it directly.

- Top-level registered-object array: count at `modelRoot+0x30`, array at
  `modelRoot-4` (pre-increment idiom).
- Command dispatcher `FUN_0813e1ac`: a ~140-case switch on event opcode.
  - `case 0xd0` → `FUN_08138ef8` → general case → `FUN_0812ba18`
  - `case 0xcb` → `FUN_08138bcc` → also → `FUN_0812ba18` (a second,
    independent path — an event a page's own `HandleEvent` rejects can
    still reach the model layer this way, via pure bubbling)
- `FUN_0812ba18(modelRoot, objId, propId, value)`: direct `obj[4]==objId`
  match over the top-level array, or `obj->FindChildById(objId)` on a
  miss. No match anywhere → the event is silently dropped.
- `FUN_0812b9e6(obj, propId, value)`: the generic property **setter** —
  see "Two parallel property systems" below.
- `FUN_0812b88c(obj, propId)`: the generic property **getter**, same
  array format.

## Two parallel property systems — don't confuse them

This codebase has **two separate property mechanisms**, easy to conflate
since both use 16-bit property IDs:

### 1. The global 472-entry metadata table (propId-indexed, flat)

Base SRAM `0x30038600`, built by `FUN_08065700`/`FUN_08160284` (a
byte-for-byte duplicate, ~0xFAB84 bytes later — a second firmware
bank/slot). 32 bytes/entry:

```
+0x00 u16 param_id   +0x02 u8 type_code   +0x04 label_ptr
+0x08 enum_table_ptr (or On/Off table if type==6)
+0x0c enum_count     +0x10 min   +0x14 max   +0x18 storage_key_ptr   +0x1c flags
```

Accessed via the universal accessor `FUN_08132f14(ctx, outBuf, paramId,
&outVal)`, confirmed with 121+ callers across the UI. **Confirmed `type`
code does not predict writability** — propIds already known to be
genuinely writable (SEQ's own Quant Size/Snap Mode/Step Len/Step Count)
span types 1/3/6/7 inconsistently.

### 2. Per-object dispatch arrays (per-object, not global)

Each model object (found via `FUN_0812b4ce`-style type-tag/id resolution
through SessionMgr) owns its own small property array: pointer at
`obj+8`, count at `obj+0xc`, **8-byte entries**:

```
+0x00 u32 value   +0x04 u16 propId   +0x06 u8 writableFlag   +0x07 u8 pad
```

`FUN_0812b9e6` (setter) scans this array for a matching `propId` and
**silently refuses to write unless `writableFlag == 1`** — a real,
confirmed gate, not hypothesized. `FUN_0812b88c` (getter) reads the same
array.

**BPM and Swing (propIds `0x8b`/`0x8e`) live in system 2, as a special
case, not system 1** — they're registered in the global metadata table
too (confirmed: type=2, min=40/max=250 for BPM, min=1/max=99 for Swing),
but that registration is metadata only. The actual live value comes from
a dedicated tempo/clock-engine object's own fast-path field, not either
property array:

- `FUN_08133a34` (the real BPM getter used system-wide) does **not** use
  the per-object array at all — it resolves the tempo-engine object via
  `FUN_0812b4ce` (a type-tag scan, `vtable[0]()==0x15`, no id argument)
  and reads `*(handle+0x10)` directly. The per-object array's `value`
  field for `0x8b` is only ever written as a side effect of a successful
  generic-setter call — it's a mirror, not authoritative storage.
- **BPM's writable flag is very likely `1`**: the main tick/event loop
  (`FUN_0813cc24`) contains a hardcoded, unconditional call —
  `FUN_0812ba18(*SessionMgr, 0, 0x8b, computed_tap_tempo_value)` — through
  the exact gated chain that `FUN_0812b9e6` would silently drop if the
  flag weren't set. Tap Tempo is a known-working shipped feature, so this
  must pass. (Behavioral inference from firmware code, not a direct byte
  read — the per-object array is runtime-constructed, not static ROM
  data, so there's no flash byte to read directly; confirmed after five
  separate static approaches to locate it all failed.)
- Swing's flag has no equivalent hardcoded call to find (ordinary UI
  edits carry `propId` as event data, not a compiled-in immediate, so an
  immediate-operand scan can't see them) — unconfirmed, no specific
  reason to expect it differs from BPM's.

## Working pages vs. SEQ's divergent pattern

**SCENE** (table `0x0818f8ec`, refresh loop `FUN_081546c8`, HandleEvent
`FUN_081544cc`) and **Met-Gain/Count-In** (table `0x081a3e24`, refresh
loop `FUN_08174874`, HandleEvent `FUN_08174a5c`, vtable `0x81a3dec`) —
both genuinely working on real hardware — handle `0xcb` the same way:
for any property ID other than their own page-specific one, they copy
`objId`/`propId`/`value` **unchanged** from the incoming event and bubble
it as `0xd0`. Neither computes or injects a "real" objId.

Their **draw-side** `if (id==0x8b || id==0x8e)` special case (found
earlier, initially assumed to be value formatting) is actually **object
resolution** — which object a property ID belongs to (the tempo-engine
singleton vs. the page's own default selected object) — before calling
the same generic getter either way. Confirmed shared framework code,
reused verbatim by at least two unrelated page classes.

**SEQ** (constructor `FUN_08159130`, vtable `0x0818fa34`, HandleEvent
`FUN_0815a720`) is structurally different, not just missing a case:

```c
else if (uVar1 == 0xcb) {
    uVar2 = FUN_08159c14(param_1, param_2[4], *(undefined4 *)(param_2 + 6));
}
```

`FUN_08159c14` routes through a **strict propId allowlist**, gated by a
mode flag at `param_1+0x11138`:

```c
if (mode_flag == 1) {
    switch (propId) {
    case 0x60: case 0x61: case 0x68: case 0x62: /* bubble 0xd2, handled */
    default: return 1;  // not handled -> keeps bubbling
    }
} else {
    switch (propId) {
    case 0x182: case 0x183: case 0x185: case 0x18b: /* handled */
    default: return 1;
    }
}
```

`0x8b`/`0x8e` are on **neither** list — SEQ's own page logic always
rejects them. But "not handled" still bubbles (per the confirmed
semantics above), continuing up through the root container
(`0x081694b0`, unlabeled in Ghidra, identified by code shape) into
`FUN_0813e1ac`'s **separate `0xcb` case** (`FUN_08138bcc`) — which still
reaches the same generic `FUN_0812ba18` resolver. So SEQ's rejection does
not necessarily dead-end the event.

SEQ's 8-slot encoder table (`0x081a4a4c`, `[Quant Size, Snap Mode, Step
Len, Step Count, 0, 0, 0, 0]`) only feeds its **refresh/draw** loop
(`FUN_08157518`) — a separate concern from the allowlist above. That
refresh loop is confirmed missing the `if(id==0x8b||0x8e)` object-
resolution special case SCENE/Met-Gain have, which is the direct,
confirmed cause of the test patch's value staying at 0 on screen.

## The 8-slot encoder-table family (a third, separate mechanism)

A page-level convention distinct from both property systems above: an
8×u16 array of parameter IDs per top-level page, `0` marking an unused
slot, stride `0x10`. Confirmed instances: SEQ (`0x081a4a4c`, in a tightly
packed run of 4 near-identical 16-byte blocks at `...3c/4c/5c/6c`),
SCENE (`0x0818f8ec`), Met-Gain (`0x081a3e24`), and a fourth, unconfirmed-
by-name "list page" variant (`0x0818e634`/`0x0818e684`). **Zero static
cross-references exist to any of these tables anywhere in the image** —
reached only via a runtime-computed pointer, a dead end hit independently
three separate times. This is a known, accepted limit of static analysis
here, not a gap worth re-chasing the same way again.

## Open questions

- **Swing's writable flag** — unconfirmed, no hardcoded call site exists
  to infer it from the way BPM's was inferred.
- **The tempo-engine object's actual class/constructor** — not located
  after five separate approaches (GetType-stub pattern scan, bulk vtable
  dump, Ghidra RTTI/class symbols, immediate-operand scan, byte-pattern
  scan for the 8-byte entry shape). The object and its property array
  are runtime-constructed (heap/SRAM), not present in the static image.
- **Two unresolved debug-string leads**: `"rootTempo: %.3f"` and the
  `globtempo`/`Swing`/`swing` key strings, referenced from
  `FUN_08065700` — whose decompile has twice timed out (60s, 240s).
  Worth a dedicated pass if this thread is picked up again.
- **Where an 8-wide encoder row's raw `0xcb` event actually gets built**
  — Met-Gain's own constructor has no 8-wide per-slot child array, so
  whatever builds these rows is shared framework code not yet located.
- **Whether `objId` genuinely doesn't matter** for transport-owned
  propIds, or just happens not to for the specific cases checked — the
  evidence (three working controls all passing a plain data value, not a
  stable id, through the objId slot) points one way, but `FindChildById`'s
  actual implementation for the tempo object was never found to prove it.

## Tooling note

`scanptr.py` (used in early sessions) is **silently broken** — a Ghidra
API misuse (`mem.getBytes` with a Python `bytearray`) swallowed by a bare
`except: continue`, so it always reports "no hits" regardless of truth.
Verified by testing it against a known-present literal. Use `scanptr2.py`
instead (uses `Memory.findBytes()` correctly) for any future pointer-
literal scanning in this codebase.
