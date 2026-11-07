/* Inspect-a-step m4 code (Lab T2 port): text + read-only data only. */
MEMORY { CODE (rx) : ORIGIN = 0x0, LENGTH = 0x0 }   /* set per build by build.py (compile_elf) */
SECTIONS {
  .text : { KEEP(*(.text.h_* .text.m4_tick)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > CODE
  .bss  : { *(.bss*) *(COMMON) } > CODE
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "RAM data not allowed in patch code")
