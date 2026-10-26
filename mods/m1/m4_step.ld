/* Per-step UI m4 code: text + read-only data only. */
MEMORY { CODE (rx) : ORIGIN = 0x081B7200, LENGTH = 0xDC0 }
SECTIONS {
  .text : { KEEP(*(.text.h_* .text.m4_tick)) *(.text*) *(.rodata*) } > CODE
  .data : { *(.data*) } > CODE
  .bss  : { *(.bss*) *(COMMON) } > CODE
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "RAM data not allowed in patch code")
