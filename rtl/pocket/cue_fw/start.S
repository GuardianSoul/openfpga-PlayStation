# SPDX-License-Identifier: MIT
# psx_cue_mount firmware entry: stack at the top of the 16 KiB RAM.
    .section .init, "ax"
    .globl _start
_start:
    li sp, 0x4000
    call main
1:  j 1b
# At 0x10: the pause before a launch retry, in cue_mount.c wait loop turns
# of 104 clocks: 0.42 s at clk_74a (scripts/run_cue_mount.py shortens it for
# the bench).
    .globl retry_wait
retry_wait:
    .word 300000
