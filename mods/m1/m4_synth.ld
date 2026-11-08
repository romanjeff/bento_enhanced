/* S6a synth UI m4 code (m4_synth.c): text + read-only data only, at SYNTH_UI + 0x100 (SYNTH_CODE; the entry table, label and wrappers are below it). */
MEMORY { CODE (rx) : ORIGIN = 0x0, LENGTH = 0x0 }   /* set per build by build.py (compile_elf) */
SECTIONS {
  .text : { KEEP(*(.text.an_*)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > CODE
  .bss  : { *(.bss*) *(COMMON) } > CODE
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "RAM data not allowed in patch code")
