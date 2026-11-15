/* S6a DSP ELF (hard float, mods/m1/dsp): text + read-only data only, at M7_DSP + 0x100 (the trampolines that
 * build.py assembles sit below it). build.py refuses .data / .bss / .init_array / .ARM.extab and any soft-float or
 * double helper (check_dsp_elf); the asserts here are the linker's half of that. clang emits a .ARM.exidx
 * "cannot unwind" entry per function on ARM even with -fno-unwind-tables: nothing unwinds here, so it is dropped,
 * as in m7.ld; real unwind data (.ARM.extab) is refused. */
MEMORY { CODE (rx) : ORIGIN = 0x0, LENGTH = 0x0 }   /* set per build by build.py (compile_dsp) */
SECTIONS {
  .text : { KEEP(*(.text.an_enter .text.an_osc .text.an_prop .text.dr_render)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > CODE
  .bss  : { *(.bss*) *(COMMON) } > CODE
  .init_array : { KEEP(*(.init_array*)) KEEP(*(.ctors*)) } > CODE   /* kept, so a constructor is refused, not dropped */
  .ARM.extab : { *(.ARM.extab*) } > CODE
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "RAM data not allowed in the DSP ELF")
ASSERT(SIZEOF(.init_array) == 0, "static constructors not allowed in the DSP ELF")
ASSERT(SIZEOF(.ARM.extab) == 0, "unwind tables not allowed in the DSP ELF (-fno-unwind-tables)")
