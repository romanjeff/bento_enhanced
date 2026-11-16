# Crash #410 (BE4.22, M7 cause 1, PC 0x08056140): final verdict

Labels: **M** = measured or re-derived in this pass. **S** = taken from an investigation and consistent with what I checked, but not re-derived. **I** = inference.

## Bottom line

#410 is a stock M7 precise BusFault in stock 1.6.8 master-FX DSP code. It happened at the **end of the HUB session**, about 6k ticks after the last HUB save at device RTC 01:27. It did not happen 44 s into the DROME boot, and DROME is unrelated.

The likely cause is the known stock "session-end teardown" class: the M4 resets the FMC through HAL_DeInit while the M7 is still rendering audio. No BE code touches the faulting code or its data, and no BE code touches FMC, RCC or the MPU. BE4.40 and BE4.41 are exactly as exposed as stock, no more and no less.

## What I re-checked against the binaries

| Claim | Result |
|---|---|
| 0x08056140 is a leaf biquad tick whose first instruction is `vldr s14,[r1]` | **M** (stock 1.6.8 disassembly) |
| The caller 0x08054620 makes 8 tick calls per sample (4 L + 4 R). `lr 0x0805480D` is the return from the 3rd call (`bl` at 0x08054808, `ldrd r0,r1,[sp,#0x20]`), which is L stage 3 | **M**. Investigation 1 says "3rd of 8" and investigation 2 says "3rd of 4". Both are right: 4 per channel, 8 in total. |
| bkp4 = BFAR and bkp5 = CFSR | **M**. Handler 0x0804B3C0: SCB base 0xE000ED00 and RTC base 0x58004000 (BKP0R at +0x50). It stores MMFAR when CFSR bit 7 is set, otherwise BFAR (SCB+0x38) when bit 15 is set. It writes bkp5 = CFSR and bkp6..13 = the frame. So CFSR 0x8200 = BFARVALID + PRECISERR, a precise data BusFault at 0xC5AE09C0. bkp4 equals r1. Investigation 2 had labelled this I; it is now M. |
| All registers point into one object at 0xC5AE0904 (r1 = +0xBC, r0 = +0xAC) | **M** (arithmetic). The pointer is valid, 32-byte aligned and inside the object. |
| SDRAM is configured as 128 MB, so the address is in range | **S** (both investigations agree; init at 0x0805B880) |
| HAL_DeInit 0x0811551C writes RCC_AHB3RSTR = 0x7FFFFFFF and then 0 | **M** (literal 0x58024400 at 0x08115578, `str` to +0x7C). It does the same for AHB1..APB4 (+0x80..+0x94). AHB3RSTR bit 12 = FMCRST is from the H7 reference manual (**S**). |
| An access to the FMC region after FMCRST returns an AXI error, so a precise BusFault | **I**. The FMC enable is cleared on reset, and on the H7 an access to a disabled FMC bank is a bus error. |
| Byte-identical in stock, 4.22, 4.40 and 4.41: Phaser 0x08054620–0x08054D00, biquad, vtable 0x080AC60C.., M7 HardFault handler, M4 Shutdown and disk entry 0x08106E84–0x08107100, HAL_DeInit, SDRAM init 0x0805B880, vector table, disk-mode image 0x080E0000–0x08100000 | **M** (scratchpad\crash410\verdict\cmpr.py) |
| **One BE hook *is* on the path** | **M**. process() calls 0x08065AC0 at 0x08054686 (event loop, before the sample loop). That site is patched to the `h_consume` trampoline (`74f0d6ba`) in 4.22, 4.40 and 4.41. Investigation 1 said "no BE hook on the path"; that is wrong, and investigation 2 was right. It does not change the verdict (see H3). |
| The record predates the DROME session | **M**. The gen-1 black box (rtc 261105-051253) shows `count=410 … pc=08056140` "as found at boot". The CRASHES line `ms=43951` equals the DROME save's "dma seen running … at ms 43951", so `ms` is the logging boot's tick, not the crash time. |
| Last-alive tick of the HUB run | **M**. At the gen-1 boot, bkp28 = 0x02ED031F = 49087263. The last HUB save was at ms 49081221, so the run ended about 6.0k ticks after that save. Calibration (**M**): at the gen-3 boot, bkp28 = 6640015 against "last UI pass 6641039", so bkp28 lags the true last-alive time by about 1 s. |
| DROME is the empty `<session version="2"/>`. HUB has a master FX with phaseamount=486, phaselforate=280, phasefeedback=500 | **M** (read-only from D:) |
| D:\ logs are byte-identical to the be422dev copies | **M**. The device has written nothing since the DROME save. The SD-mode entry Jeff just made ended the DROME session, so it is a fresh trial whose result shows up on the next boot. |

## Site

This is M7 thread-mode audio render. The master-FX Phaser process() is at 0x08054620 (identified as the Phaser via property keys; **S**). On sample 0 of a 128-frame block, its 3rd left-channel allpass stage at 0x08056140 read its coefficient line at 0xC5AE09C0. The read got a precise bus error (**M**). Lines 0x..960–0x..99F of the same object had just been read by stages 1 and 2. The data was therefore readable a few cycles earlier, or still cached, which points to the bus or controller and not to the pointer (**I**). The stack was normal at MSP 0x2404C968 (**M**).

## Ranked hypotheses

**H1. Stock teardown race, about 75% (I).** The M4 runs HAL_DeInit, which resets the FMC (and DMA, SAI and the other buses) while the M7 is still running render. The first D-cache miss into SDRAM then takes a precise BusFault, so the Phaser is a bystander. Evidence:
- The CFSR/BFAR signature and the coherent pointer chain (**M**).
- HAL_DeInit resets the FMC, and both stock teardowns call it: PowerMgr::Shutdown 0x08106F08 with a 1000 ms busy-wait afterwards, and disk-mode entry 0x08106E84. The writes are **M**; the call graph and the Unicorn traces are **S**.
- The record falls about 6 s after a save at the very end of a session (**M**).
- The same signature as the 1.6.1-era SD-transition records: #150 with CFSR 8200 / BFAR C1000004 (PROJECT_NOTES:1734), #96 and #161/#163, and the session-end WWDG records #164–169 (PROJECT_NOTES:1818) (**S**).

**Which teardown is unresolved.** Investigation 1 favours power-off, and investigation 2 leaves it open. Weak evidence leans towards power-off (**I**):
- (a) gen=1, slice canary changed=264 and garbage counters mean RAM was lost before the 05:12 boot (**M**). In the earlier gen2→gen3 transition, the session most likely ended in SD mode, because SPACED_COPY was copied off the card at its 09:32:30 save time (**I**). RAM survived that transition for 9.4 h with no new record (count stayed 409) (**M**). So SD entry alone does not explain the RAM loss.
- (b) Nothing in the repo was copied off the card from the 01:27 session (**M**), which weakly suggests no SD-mode visit.

"SD entry, then unplug while in SD mode" also fits both (a) and (b). Only Jeff can settle this.

Not proven: the AXI-error behaviour of a reset FMC (**I**), and whether 1.6.8 power-off actually removes the rails.

**H2. Abrupt power loss (cable pulled or brownout) during play, or a random FMC/SDRAM fault, about 15% (I).** This would also explain gen=1. Against it: a dying SDRAM usually returns garbage rather than an AXI error, so a clean precise BusFault exactly at r1 fits a controller-level refusal better (**I**). A random mid-play glitch would not be expected to land about 6 s after a save at the end of a session.

**H3. A BE4.22 hook caused it, about 5% (I).** `h_consume` (m7_diag.c:403) does run inside this process() once per block before the faulting sample (**M**). It only touches diag RAM: the heartbeat and stack sample, `load_block`, `m1_region_poll` and periodic clean-by-address (**M**, source at c333216's tree). None of the 78 BE4.22 stock-site writes touches FMC, RCC or the MPU (**S**, both investigations). Corrupt data cannot turn a valid, aligned, in-object address into a bus error (**I**, strong). A clean-by-address cannot raise a BusFault on its own. It could only fault the same way if the FMC was already dead, and it would then fault inside BE code, not at 0x08056140 (**I**).

**H4. The DROME project triggered it, ruled out (M).** The record existed before DROME was created.

## Ours or stock

**Stock (I, high confidence).** The faulting code, the object and the reset machinery are all stock and byte-identical in every kit (**M**). The one BE hook on the call path cannot produce this fault type (**I**). A stock-only device trial would settle it at M level.

## BE4.40 / BE4.41 exposure

They are exposed to the same degree as stock 1.6.8 and BE4.22. Every relevant range is byte-identical, and the `h_consume` site is patched the same way in all three (**M**). The 4.40 and 4.41 manifests add only Analog wavetable vtable slots and M4 UI writes, none of them on this path (**S**). No kit makes this better or worse.

## Reproduction

An offline reproduction of the cause is not feasible: Unicorn has no FMC or AXI-error model. A Unicorn harness that unmaps line 0xC5AE09C0 can confirm the layout decode, which is already confirmed by arithmetic, so its value is low.

The device A/B is the real test (**no code change needed**). Run each trial as: boot → wait 25 s or more → save → act → boot → wait 25 s → save → read CRASHES and SAVES:

1. Load HUB (Phaser active), play 1 min, save, then **power off with the button** ×5.
2. Same, but **enter SD mode** ×5.
3. Same as 1 with HUB's Phaser amount = 0 ×3. Under H1 the PC should move to some other SDRAM user.
4. Repeat 1 on **stock 1.6.8** (03_RESTORE_STOCK) and read the record on the "Last crash info" page or after reinstalling BE.

Under H1 you get new cause-1 records (BFAR 0xC…, CFSR 8200) or cause-5 WWDG records at the transition, at a similar rate on stock. Check whether gen resets to 1 after a power-off; that answers whether power-off removes the rails.

## What Jeff should do now

1. **Answer one question:** how did the HUB session end around device time 01:27–01:28? Was it the power button, SD mode, or pulling the cable? Was the unit unplugged overnight? That alone narrows the teardown.
2. **The current SD-mode visit is a free trial.** Leave the card untouched, exit SD mode, let it boot, wait at least 25 s, save any project, then read D:\BENTO_CRASHES.log and BENTO_SAVES.log (read only). What to look for:
   - **count=411?** If yes, decode bkp4/bkp5. DROME had no Phaser, so the PC will be elsewhere or it will be a WWDG.
   - **boot gen 2 or 1?** gen 2 means SD mode kept RAM, which would point #410's RAM loss at a power-off or unplug.
3. If you want the full A/B, run trials 1–2 above, then trial 4 on stock.
4. Notes to update. These are repo edits, so they need your OK:
   - Annotate #410 as the teardown class, next to #150.
   - The CRASHES `ms=` field is the logging boot's tick, not the crash time.
   - bkp28 is a last-alive stamp that lags by about 1 s.
   - bkp4/bkp5 are BFAR/CFSR when CFSR bit 15 is set (or MMFAR/CFSR when bit 7 is set). Teach the decoder to print "precise BusFault @addr".
5. A mitigation is optional and is your call: an M4→M7 "park before HAL_DeInit" handshake in Shutdown and disk entry. It changes the stock power path, so it is only worth doing if these records keep masking real crashes.
