/* M1 diagnostic m4 code: text + read-only data only; any RAM data is a build error. */
MEMORY { CODE (rx) : ORIGIN = 0x081BE000, LENGTH = 0x1580 }
SECTIONS {
  .text : { KEEP(*(.text.h_* .text.m4_tick)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > CODE
  .bss  : { *(.bss*) *(COMMON) } > CODE
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "RAM data not allowed in patch code")
