/* SPDX-License-Identifier: MIT */
/* psx_cue_mount RAM (16 KiB): code, the APF windows (path struct at 0x2000,
   read window at 0x2200), file and track tables, the cue and playlist
   readers with constants and variables, stack (0x3C00-0x3FFF). */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
    CODE (rwx) : ORIGIN = 0x0000, LENGTH = 0x2000
    BIG  (rw)  : ORIGIN = 0x2400, LENGTH = 0x0800
    HIGH (rwx) : ORIGIN = 0x2C00, LENGTH = 0x1000
}
SECTIONS {
    /* First: an input section goes to the first output section naming it. */
    .high : {
        *(.text.parse) *(.text.sbi_mask) *(.text.playlist) *(.text.entry) *(.text.open_entry)
        *(.rodata*) *(.srodata*) *(.data*) *(.sdata*) *(.sbss*) *(.bss*) *(COMMON)
    } > HIGH
    .text : { *(.init) *(.text*) } > CODE
    .big (NOLOAD) : { *(.big) } > BIG
    /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.note*) }
}
