# SPDX-License-Identifier: MIT
# psx_cue_mount firmware entry: stack at the top of the 16 KiB RAM.
    .section .init, "ax"
    .globl _start
_start:
    li sp, 0x4000
    call main
1:  j 1b
