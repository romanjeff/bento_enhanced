# CPI / fast-memory / crash #412: verified report

Labels: M = measured here (disassembly, emulation, byte scan, log arithmetic), S = read from source, I = inferred.
Verification scripts and copies: `C:\Users\rjrom\AppData\Local\Temp\claude\9acdfe94ba00fd09\71a55988-76ea-4e33-b531-3ea1a26f5f35\scratchpad\cpi\V\` (d.py, x.py, y.py, dsp.txt, s.bin = stock 1.6.8, be.bin = BE4.41 image, dsp.elf).

## 0. What I re-derived, and corrections to the three investigations

Confirmed (M unless marked):
- **Boot.** The reset handler at 0x080403CC copies only .data to AXI (0x080CFA8C -> 0x24000000..0x240001D0) and zero-fills .bss/.tbss. Nothing targets 0x0000xxxx. The init-array loop at 0x080404B6 (CTRL_STARTUP_SITE) runs before main.
- **Clock and flash.** SystemClock_Config 0x08050260 sets PLL1 M=3, N=468, P=2, Q=5, R=2 from HSE 6.144 MHz (literal 0x005DC000 at 0x0804BF00). That gives SYSCLK 479.232 MHz and HCLK/AXI 239.6 MHz. 479.232 MHz x 128 / 48 kHz = 1,277,952 cycles per block, which matches the 1.28 M block. HAL_RCC_ClockConfig is called with `movs r1,#4` at 0x080502DE, so **FLASH ACR = 4 wait states**. That is the standard setting for 480 MHz parts, so there is no flash-latency lever.
- **No ITCMCR/DTCMCR literal.** There is no 0xE000EF90/94 literal anywhere in the image.
- **MPU.** I emulated 0x0804FA00 in Unicorn (mpu.py, cross-checked against the HAL_MPU_ConfigRegion field packing at 0x080447C0):
  - 15 regions, PRIVDEFENA. No region covers 0x0..0xFFFF.
  - R1 AXI 512 KB is XN (WB, no write-allocate). R4 flash bank 1 is write-through and executable. R8 SDRAM 0xC4000000/32 MB is WB, no write-allocate, XN.
  - R15 is free.
- **Privilege.** The core runs **privileged on MSP**. The PSP/CONTROL=2 path at 0x0804043E is skipped because both literals (0x08040518 and 0x0804051C) are 0x2404CDA0. Investigation 2's "CONTROL 0 then 2" is wrong. Its conclusion (privileged, so the default map applies, so ITCM is executable) still holds.
- **DSP ELF size.** It is **23,772 B** (.text at 0x080D0100). Investigation 1's "~36 KB" is the flash window (36,608 B), not the code.
- **DSP trampolines.** They are at **0x080D0000..0x080D006F**: 4 entries in BE4.41, at 0x00, 0x10, 0x30 and 0x50. 0x080DA000 holds the M7-C hook trampolines (h_consume and others), which are not DSP. BE4.41 has 4 DSP trampolines, not 5, because D1 is out with drums omitted. All 4 are `push {r0-r4,lr}; bl dsp; ...; b.w stock`. Nothing else in the image BLs into the DSP window; the only other hits are data that happens to decode as BL, in the 0x080CBxxx..0x080CExxx rodata.
- **New finding: the DSP image is already position-independent.**
  - No direct branch leaves the window. There are no absolute literals or movw/movt pairs pointing into 0x080Dxxxx.
  - Rodata is reached PC-relative (`add rX, pc`, `adr`, `vldr [pc]`).
  - Stock is reached only through `blx reg` from movw/movt constants: 0x080A09A1, 0x0804F361, 0x08071821, 0x080518C1, 0x0804F5E1. All of these calls sit in 0x080D0732..0x080D15F4, the per-block glue, not in the kernels.
  - So the exact same bytes can run from flash or from ITCM without relinking. This is a heuristic scan, though. Confirm it from the ELF relocations before relying on it.
- **Measurement scope.** The device cycle counts are wall-clock DWT counts with interrupts enabled (S: analog.cpp:326-355, `r->cyc += CYCCNT - c0`). peak_cyc therefore includes ISR preemption.

Not independently re-checked: SRAM4/SRAM3/D2 usage details (investigation 1), the instruction mixes (investigation 2), and the byte-identity sweep for crash #412 (investigation 3). I spot-checked the crash site and registers myself, below.

## 1. Moving hot DSP code to fast memory

**Feasible: yes.** ITCM (0x0000_0000..0x0000_FFFF, 64 KB, zero wait) is:
- unused by stock's static image (M, both scans);
- executable for privileged code under PRIVDEFENA (M);
- uncached, so no cache maintenance is needed.

DTCM is full (32 B free) and AXI is XN, so ITCM is the only sensible target. SRAM4 is a slower fallback.

**Space.**
- The whole DSP (23.7 KB) fits with large margins.
- Suggested run address: **0x8000**. That leaves 0x0..0x7FFF as a NULL+offset buffer, and the code ends at about 0xDCDC, leaving about 9 KB of headroom below 0x10000 as a canary zone.
- The binding limit stays the **flash window**: 36,608 B, with 12,836 B free. ITCM adds no code space.
- If the DSP ever grows past 32 KB, drop the run address to 0x4000.

**Expected gain: about 3-15% of Analog render cycles. Best guess 5-8%. Unmeasured (I).**
- ITCM removes only the I-fetch part of the CPI.
- A 32 B line fill from flash at 4 WS on the 240 MHz AXI costs roughly 15-30 core cycles. Investigation 2 assumed 2 WS; correcting that raises its per-line cost a little but does not change the order of magnitude.
- Low end (about 3-4%): the 17.7 KB hot set misses once per block.
- High end (about 15%): it misses once per voice-block. That happens if the hot set plus stock's graph code between voices exceeds the 16 KB 2-way I-cache, which is plausible at 17.7 KB.
- What the 2.6 figure actually measures:
  - It comes from one worst block (peak 360,425) and includes ISRs.
  - The histogram (15-30%: 14,692 blocks, 30%+: 0) puts busy 4-voice blocks at roughly 1.4-2.6 CPI.
  - Most of that is FPU latency, VCMP->VMRS->branch serialisation and mispredicts, VDIV, data misses and ISR time. None of these move with ITCM.

**Effort: 2 sprints.** I recommend a different measurement plan from investigation 2's.
- **S1, one dev kit: mechanism plus a paired A/B.**
  - At CTRL_STARTUP, copy the DSP image to ITCM with word or doubleword stores, padded to 8 B for ECC. Store a checksum, and fill the rest of ITCM with a canary.
  - Change each DSP trampoline from `bl dsp` to an indirect call through a RAM selector word: flash base or ITCM base, plus the entry offset. ip is free here: the trampoline's bl already clobbers it under AAPCS.
  - The trampolines have to be re-spaced to 0x40 each within DSP_TRAMP_BYTES 0x100, because the indirect form adds about 10 B per entry plus a literal.
  - Flip the selector every N blocks. Keep two ANALOG accumulators (flash and ITCM: avg, peak, histogram). This works because the bytes are position-independent. It gives a same-session, same-patch A/B that cancels out ISR and patch variance.
  - Have the black box report "ITCM ok/bad" and canary changes.
  - Offline work: a Unicorn test of the real startup stub (ITCM bytes equal the image, 0x0..0x7FFF untouched, checksum matches), plus a selector test showing audio is bit-identical either way. Extend check_branches to accept the indirect form.
- **S2.** If the ITCM side is at least about 5% better and the canary stays intact across a full session (load, SD mode, browse), make ITCM permanent. Optionally harden it with MPU R15 (ITCM read-only and executable). That needs a hook after 0x0804FA00 and uses up the last free region, so it is low priority.
- **Close call.** Investigation 2's cold/warm re-render probe also bounds data-side misses, which the selector A/B does not. But it is more intrusive (a copy of voice state, an IRQ-off re-render) and it does not exercise the real mechanism. The selector A/B measures exactly what we would ship, and most of its code is the ship code. If data-side misses turn out to matter, add the cold/warm probe later.

**Risks.**
- ITCM-free is a static result. A computed pointer into ITCM, or bootloader residue, can't be excluded; we re-copy on every boot anyway. The canary and the checksum are the guard.
- NULL is ITCM on the M7: a stray small-offset write lands silently instead of faulting. Stock's own clang null-trap stores (up to 0x86D4) only run immediately before `udf`, so they don't matter. Unknown stock null bugs do. The 32 KB low buffer and the checksum mitigate this.
- ECC: avoid partial 64-bit words (I).
- The position-independence claim needs confirmation from relocations. A future absolute self-reference, such as a function-pointer table, would break the dual-address selector. Add a build-time check.
- Crash decoding and Ghidra need the DSP mapped at the ITCM address.
- The gain may sit near the noise floor at peak. Judge it on the paired avg and histogram, not on peak_cyc.

## 2. Other CPI levers, ranked (value for effort; all I unless noted)

1. **Measure before optimising (cheap, needed for everything below).** Split ISR time out of the DWT window, or add an IRQ-off sample every 1024th block. Until then we don't know whether 2.6 is the core's CPI or mostly preemption. This changes how every lever below should be judged.
2. **Remove VDIV from the per-sample FM path.** The doc's worst case is 2n VDIVs per render (M, plan-p3). At about 14 cycles each that is about 7 k cycles per 2N voice-block. Replace them with a reciprocal computed once per block, or with a multiply. This is a small, local change that can be tested offline for accuracy.
3. **Branchless compares.** Replace VCMP/VMRS/branch with VSEL/VMAXNM/VMINNM. an_render alone has 128 such pairs (M, investigation 2). Each pair stalls on VMRS and risks a mispredict on data-dependent edges and sync. The gain could be large, but it is uncertain, and the bit-identity tests make it moderate work.
4. **ITCM code move** (section 1): 3-15%, 2 sprints, and the cleanest to measure.
5. **Hot data placement.** BE state lives in SDRAM (0xC5FFxxxx, R8 WB, no write-allocate). Per-voice scratch could go into the ~40 KB of ITCM left after the code, or into the free part of the AXI slice. That is zero-wait, avoids D-cache misses, and avoids no-write-allocate SDRAM stores. The size of the effect is unknown until we know which buffers the kernels stream.
6. **Interleave two oscillators or unroll independent chains to hide FPU latency.** Potentially the largest lever, but it is the most work and costs flash bytes in the window that is the binding limit.
7. **Compiler flags** (-O2 on more files, -falign-loops): about 1-2%, and it spends flash.
8. **Not levers:** the clock (479 MHz, already at the 480 MHz maximum), flash wait states (4 is required), and the I/D caches (already on).

## 3. Crash #412

**Classification: stock teardown class, the same as #410.** I re-checked the core facts (M):
- **Fault site.** PC 0x08053668 is `ldrb r3,[r5,#0x18]` in the 64-slot scan loop of stock function 0x08053560. The loop is `adds r6,#1; adds r5,#0x20; cmp r6,#0x40` at 0x08053660..0x0805366C.
- **Registers.** bkp14..21 hold r4..r11:
  - r5 = 0xC5A297E0 = r10 (0xC5A29720) + 6 x 0x20.
  - BFAR (bkp4) = 0xC5A297F8 = r5 + 0x18.
  - CFSR (bkp5) = 0x8200, which is PRECISERR + BFARVALID.
  - xPSR 0x81000000: thread mode with N=1, consistent with `cmp r6,#0x40` at r6 = 6, so the loop had been incremented.
  - LR 0x080535CB is the return address from the `bl 0x08065AC0` at 0x080535C6, so neither of the later BLs (0x0805364A, 0x0805367A) ran.
  - The object base is 32 B aligned, so each slot is its own cache line. Six neighbouring lines were read moments before line 7 bus-faulted.
- **The BE hook on the path is the same one as in #410.** The 0x08065AC0 entry is patched to the h_consume trampoline at 0x080DA070, and I confirmed that disassembly. It returned before the fault and does not touch 0xC5A2xxxx (S/I, as argued for #410 H3).

**Provenance correction, now on firmer ground than investigation 3 had (M).** #412 sits in the BE4.41 gen-3 "as found at boot" block, and its AUDIO counter continues the BE4.22 gen-2 run: 135,215 at gen 2's save, then 137,510 as found. So **the crash ended the BE4.22 session** about 2,295 blocks (about 6.1 s at 375 blocks/s) after its last save, most likely at the SD-mode entry used to install BE4.41. That is the same "about 6 s after the last save, at session end" signature as #410. It is not a BE4.41 crash, and it is not evidence about BE4.41 runtime. BE4.41 exposure is unchanged because the path is byte-identical.

**Does it change the #410 verdict? No.** It strengthens H1 (teardown race) modestly:
- Two independent session-end records, both cause 1, both CFSR 0x8200, both with BFAR at a valid aligned address in the cached ext pool, in two different stock SDRAM consumers. That is the "PC moves to some other SDRAM user" outcome the #410 doc predicted.
- This was not the controlled Phaser=0 experiment, so H1 stays I. Only the device A/B in docs/crash-410-be422.md (SD-entry, power-off, stock) moves it to M.

**Aside, not verified: #411.** It is cause 5, pc 0x080A4D80, and it ended gen 1. Its r0-xpsr frame is byte-for-byte #410's frame, so the frame looks stale: a cause-5 path that only updates bkp31. That fits the #410 doc's prediction of "cause-5 WWDG records at the transition", but I did not decode it.
