/* SPDX-License-Identifier: MIT */
/* psx_cue_mount RAM (16 KiB): code, the APF windows (path struct at 0x2000,
   read window at 0x2200), file and track tables, the cue and playlist
   readers and the cheat list reader with constants and variables, stack
   (0x3E50-0x3FFF: the deepest call chain uses under 400 bytes, -fstack-usage;
   the cue bench measures 360). */
OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY {
    CODE (rwx) : ORIGIN = 0x0000, LENGTH = 0x2000
    BIG  (rw)  : ORIGIN = 0x2400, LENGTH = 0x07F4
    HIGH (rwx) : ORIGIN = 0x2BF4, LENGTH = 0x125C
}
SECTIONS {
    /* First: an input section goes to the first output section naming it. */
    .high : {
        *(.text.parse) *(.text.msf) *(.text.sbi_mask) *(.text.playlist) *(.text.open_entry) *(.text.load_cheats) *(.text.mount_entry) *(.text.cmd_failed) *(.text.cnt) *(.text.fail_at)
        *(.rodata*) *(.srodata*) *(.data*) *(.sdata*) *(.sbss*) *(.bss*) *(COMMON)
    } > HIGH
    .text : { *(.init) *(.text*) } > CODE
    .big (NOLOAD) : { *(.big) } > BIG
    /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.note*) }
}
