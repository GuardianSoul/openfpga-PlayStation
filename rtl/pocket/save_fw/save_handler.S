# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
#
# Pocket save-state handler (notes/save-states-design.md, design B).
# Runs from the EXP1 ROM of rtl/pocket/psx_save_exp1.sv at 0xBF000000: the
# CPU's trapRequest (prepare_save_states.py) vectors here like an interrupt,
# so SR has pushed IEc/KUc and EPC/CAUSE describe the interrupted game.
#
# EXP1 device (k0 = 0xBF000000):
#   0x0000-0x0FFF ROM (this code; word 0xFFC = main RAM pages to copy)
#   0x1C00-0x1FFF second ROM (the rest of this code, from .org 0x1C00)
#   0x1000-0x17FF staging page (512 words)
#   0x1800 STATUS read {want_load, want_save, busy} / COMMAND write bit0 start, bit1 buffer->page
#   0x1801 PAGE_LO, 0x1802 PAGE_HI, 0x1803 FINISH (1 ok, 2 error), 0x1804 ACK (drops the trap)
#   0x1805/6 SET_IRQ, 0x1807 SET_DMA, 0x1808-A timer flags {irqDone, MODE[12:10]}, 0x180B SET_GO
#   0x1810-0x1817 GPU display ranges {vDisplayRange, hDisplayRange, vramRange}
#   FINISH 4 = retry: not at a quiet point, the device traps again later.
#   Staging page word 511 counts the retries of the current request (every
#   other end of a request clears it); retry RETRY_MAX + 1 ends the request
#   with an error instead (about 1.5 s, before the Pocket gives up itself)
#   0x180C SPU_HOLD write / quiet read, 0x180D/E SS_ADR, 0x180F SS_CMD (1 read
#   the SPU's savestate port word SS_ADR into staging page word SS_ADR, 2 write
#   it from there; read busy);
#   COMMAND 7 / 5 copies SPU RAM page PAGE_LO to / from the staging page
#   0x1818-0x181F read the CD controller: stat, mode, file, channel, last
#   sector read (LBA, 3 bytes), busy; write 0x1818/9 SIZE (pages the Pocket copies)
#
# Packed state: page 0 and 1 are always stored; from page 2 on, a main RAM
# page or VRAM row that is all zero is left out and its bit set in the page
# bitmap (page 0 words 64-127, bit p = logical page p), the other pages follow
# in order. The pages below are logical pages; the Pocket copies only the
# stored ones (page 0 word 38 = stored pages, the size reported). While the
# routine runs the bitmap lives in the scratchpad (BITMAP); a save puts the
# game's scratchpad back from page 1 at the end, a load from the state.
#
# Save buffer pages (2 KiB each, 2048 pages = 4 MiB):
#   0     registers (layout below)
#   1     scratchpad (0x000-0x3FF), GTE data regs (0x400), GTE control regs (0x480),
#         I/O (0x500): I_STAT, I_MASK, DPCR, DICR, DMA 0-6 {MADR, BCR, CHCR},
#         timers 0-2 {CURRENT, MODE, TARGET} (0x570), timer flags 0-2 (0x5A0),
#         GPU (0x5B0): GPUSTAT, GP1(10h) infos 2-5 (E2-E5), display ranges (2 words)
#   2..   main RAM, 2 KiB per page from 0x80000000 (N pages, ROM word 0xFFC)
#   then  VRAM, one 1024-pixel row per page (R rows, ROM word 0xFF8)
#   then  SPU registers: savestate port word i at 4*i (the words SPU_SS lists),
#         SPU registers 0x1F801C00-0x1F801DFF at 0x100
#   then  SPU RAM, 2 KiB per page (S pages, ROM word 0xFF4)
#   2047  scratch: the interrupted game's registers during a load
#
# Page 0 words: 0 magic, 1..31 GPRs (26/27 = k0/k1 are not kept: any
# interrupt may clobber them while IEc=1), 32 HI, 33 LO, 34 SR (as seen in
# the handler), 35 CAUSE, 36 EPC, 37 EXP1 delay/size (0x1F801008),
# 38 stored pages, 40/41 CD controller state at the save (0x1818-0x181F),
# 42 CD IRQENA | the game's CD index << 8, 64-127 page bitmap.
#
# CD: a save waits for a CD quiet point (no command, second response or drive
# interrupt on its way, no flag the game has not acked, FIFOs empty), so the
# game holds every sector up to the tapped LBA. A load restarts the drive
# the way a game does: it waits for the motor, pauses it, sets mode, filter,
# demute, and at the end (after I_STAT is back) Setloc and ReadN from the
# next sector, or Play from the position, then restores IRQENA. The game
# sees one longer seek; the XA decoder starts empty.

        .set    noreorder
        .set    noat
        .text

        .equ    MAGIC, 0x32535850       # "PXS2" (packed)
        .equ    PAGE, 0x1000
        .equ    STATUS, 0x1800
        .equ    PAGE_LO, 0x1801
        .equ    PAGE_HI, 0x1802
        .equ    FINISH, 0x1803
        .equ    ACK, 0x1804
        .equ    SCRATCH, 2047
        .equ    CPU_PAGE, 1             # scratchpad + GTE
        .equ    RAM_PAGE, 2             # first main RAM page
        .equ    GTE_DATA, PAGE+0x400
        .equ    GTE_CTRL, PAGE+0x480
        .equ    IO, PAGE+0x500
        .equ    IO_DMA, IO+0x10
        .equ    IO_TMR, IO+0x70
        .equ    IO_TFL, IO+0xA0
        .equ    IO_GPU, IO+0xB0
        .equ    GPU_RANGES, 0x1810
        .equ    SET_IRQ_LO, 0x1805
        .equ    SET_IRQ_HI, 0x1806
        .equ    SET_DMA, 0x1807
        .equ    TFLAGS, 0x1808
        .equ    SET_GO, 0x180B
        .equ    DISC, 0x180B            # read: the disc in the drive - 1 (0-3)
        .equ    SPU_HOLD, 0x180C
        .equ    SS_ADR, 0x180D
        .equ    SS_CMD, 0x180F
        .equ    SPU_REGS, PAGE+0x100
        .equ    RETRIES, PAGE+0x7FC     # staging page word 511
        .equ    RETRY_MAX, 500          # retries about 1 ms apart (SPU waits about 3 ms)
        .equ    CD_STATE, 0x1818
        .equ    SIZE_LO, 0x1818
        .equ    SIZE_HI, 0x1819
        .equ    BITMAP, 0x300           # scratchpad offset of the page bitmap (256 bytes)
        # SPU registers a load does not write back (ENDX, FIFO data, SPUSTAT,
        # current main volume): bits of 0x1F801D80 + 2*bit
        .equ    SPU_NO_BUS, 0x3090C000

        .macro  GPRS op
        \op     $1, PAGE+4*1($k0)
        \op     $2, PAGE+4*2($k0)
        \op     $3, PAGE+4*3($k0)
        \op     $4, PAGE+4*4($k0)
        \op     $5, PAGE+4*5($k0)
        \op     $6, PAGE+4*6($k0)
        \op     $7, PAGE+4*7($k0)
        \op     $8, PAGE+4*8($k0)
        \op     $9, PAGE+4*9($k0)
        \op     $10, PAGE+4*10($k0)
        \op     $11, PAGE+4*11($k0)
        \op     $12, PAGE+4*12($k0)
        \op     $13, PAGE+4*13($k0)
        \op     $14, PAGE+4*14($k0)
        \op     $15, PAGE+4*15($k0)
        \op     $16, PAGE+4*16($k0)
        \op     $17, PAGE+4*17($k0)
        \op     $18, PAGE+4*18($k0)
        \op     $19, PAGE+4*19($k0)
        \op     $20, PAGE+4*20($k0)
        \op     $21, PAGE+4*21($k0)
        \op     $22, PAGE+4*22($k0)
        \op     $23, PAGE+4*23($k0)
        \op     $24, PAGE+4*24($k0)
        \op     $25, PAGE+4*25($k0)
        \op     $28, PAGE+4*28($k0)
        \op     $29, PAGE+4*29($k0)
        \op     $30, PAGE+4*30($k0)
        \op     $31, PAGE+4*31($k0)
        .endm

        # lwc2 GTE data registers from the staging page (as mtc2: the same
        # write side effects), one instruction each
        .macro  GTE_W r:vararg
        .irp    n, \r
        .word   0xC8000000 | (26 << 21) | (\n << 16) | (GTE_DATA+4*\n)
        .endr
        .endm

        # DIAG builds (build_save_fw.py --diag): MARK n paints a 16x16 block
        # at (16*n, 16) of the displayed picture after save step n (white;
        # DIAG builds refuse loads); a wait that times out paints the next
        # block red and stops there, so a photo shows the step that hangs.
        # A retry (no quiet point) paints block 0: blue when a DMA channel
        # runs, yellow when the SPU did not get quiet.
        .macro  MARK n
        .ifdef  DIAG
        jal     diag_block
        li      $v1, \n
        .endif
        .endm
        .macro  TIMEOUT_START
        .ifdef  DIAG
        lui     $v0, 0x0010
        .endif
        .endm
        .macro  TIMEOUT_STEP
        .ifdef  DIAG
        addiu   $v0, $v0, -1
        beqz    $v0, diag_timeout
        nop
        .endif
        .endm

        .globl  entry
entry:
        lui     $k0, 0xBF00
        lbu     $k1, STATUS($k0)
        sb      $zero, ACK($k0)
        GPRS    sw
        .ifdef  DIAG
        move    $a2, $zero              # after GPRS: a2 is the game's
        .endif
        mfhi    $t0
        mflo    $t1
        sw      $t0, PAGE+4*32($k0)
        sw      $t1, PAGE+4*33($k0)
        mfc0    $t0, $12
        mfc0    $t1, $13
        mfc0    $t2, $14
        sw      $t0, PAGE+4*34($k0)
        sw      $t1, PAGE+4*35($k0)
        sw      $t2, PAGE+4*36($k0)
        # a GTE command at EPC (not in a branch delay slot) already ran:
        # return past it, as the BIOS interrupt handler does
        bltz    $t1, 2f
        lui     $t4, 0xFE00
        lw      $t3, 0($t2)
        lui     $t0, 0x4A00
        and     $t3, $t3, $t4
        bne     $t3, $t0, 2f
        addiu   $t2, $t2, 4
        sw      $t2, PAGE+4*36($k0)
2:
        # EXP1 at full speed (8-bit, no delays, address increment) for the copies
        lui     $t1, 0x1F80
        lw      $t0, 0x1008($t1)
        andi    $k1, $k1, 4
        sw      $t0, PAGE+4*37($k0)
        .ifdef  NOOP                    # NOOP builds (--noop): a save reports done
        beqz    $k1, 1f                 # at once without copying anything, a load
        li      $t0, 1                  # is refused; tells a Pocket-side failure
        li      $t0, 2                  # from one in the copy
1:      j       restore
        sb      $t0, FINISH($k0)
        .endif
        lui     $t2, 0xFFFF
        and     $t0, $t0, $t2
        ori     $t0, $t0, 0x2000
        sw      $t0, 0x1008($t1)
        # quiet point: no DMA running, the GPU idle between commands and not
        # sending VRAM (GPUSTAT 26 = 1, 27 = 0); otherwise retry later
        lw      $t0, 0x1814($t1)
        lui     $t2, 0x0C00
        and     $t0, $t0, $t2
        lui     $t2, 0x0400
        bne     $t0, $t2, retry
        lui     $t3, 0x0100
        .irp    n, 0,1,2,3,4,5,6
        lw      $t0, 0x1088+0x10*\n($t1)
        nop
        and     $t0, $t0, $t3
        bnez    $t0, retry
        .ifdef  DIAG
        lui     $a2, 0x00FF             # DIAG retry block: blue = a DMA channel runs
        .else
        nop
        .endif
        .endr
        # a save: CD quiet point, its state into page 0
        bnez    $k1, 1f
        nop
        jal     cd_quiet
        nop
        bnez    $v0, retry
        nop
1:
        # the SPU held between samples (no voice processing, DMA FIFO empty)
        li      $t0, 1
        sb      $t0, SPU_HOLD($k0)
        li      $t1, 0x2000
spu_wait:
        lbu     $t0, SPU_HOLD($k0)
        addiu   $t1, $t1, -1
        andi    $t0, $t0, 1
        bnez    $t0, spu_quiet
        nop
        bnez    $t1, spu_wait
        nop
        b       retry
        .ifdef  DIAG
        ori     $a2, $zero, 0xFFFF      # DIAG retry block: yellow = the SPU never got quiet
        .else
        nop
        .endif
spu_quiet:
        lw      $s4, 0xFF4($k0)         # SPU RAM pages
        lw      $s3, 0xFF8($k0)         # VRAM rows
        lw      $s2, 0xFFC($k0)         # main RAM pages
        .ifdef  DIAG
        li      $a2, 0x00FFFFFF
        .endif
        MARK    1
        bnez    $k1, load
        addiu   $s2, $s2, RAM_PAGE      # last RAM page + 1

save:
        lbu     $t2, DISC($k0)          # the disc in the drive: a load needs the same
        lui     $t0, %hi(MAGIC)
        ori     $t0, $t0, %lo(MAGIC)
        addu    $t0, $t0, $t2
        sw      $t0, PAGE($k0)
        jal     put_page
        move    $a0, $zero
        MARK    2
        jal     io_save
        nop
        MARK    3
        # scratchpad and GTE
        jal     cu2_on
        nop
        lui     $s0, 0x1F80
        jal     copy_out
        li      $a1, 0x400
        .irp    n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
        .word   0x48400000 | (9 << 16) | (\n << 11)    # cfc2 t1, $n
        .word   0xE8000000 | (26 << 21) | (\n << 16) | (GTE_DATA+4*\n)    # swc2 $n, GTE_DATA+4n(k0)
        sw      $t1, GTE_CTRL+4*\n($k0)
        .endr
        jal     put_page
        li      $a0, CPU_PAGE
        MARK    4
        # the scratchpad is in page 1 now: the page bitmap goes there
        lui     $t1, 0x1F80
        li      $t0, 0x100
2:      addiu   $t0, $t0, -4
        addu    $t2, $t1, $t0
        bnez    $t0, 2b
        sw      $zero, BITMAP($t2)
        li      $s7, RAM_PAGE           # next stored page
        # main RAM
        lui     $s0, 0x8000
        li      $s1, RAM_PAGE
1:      jal     copy_out
        li      $a1, 0x800
        jal     put_data
        move    $a0, $s1
        addiu   $s1, $s1, 1
        bne     $s1, $s2, 1b
        nop
        MARK    5
        jal     vram_cmd                # GP0 C0: VRAM -> CPU, rows 0..R-1
        lui     $a0, 0xC000
        addu    $s3, $s3, $s2
6:      jal     gpu_out
        nop
        jal     put_data
        move    $a0, $s1
        addiu   $s1, $s1, 1
        bne     $s1, $s3, 6b
        nop
        MARK    6
        jal     spu_save
        nop
        MARK    7
        lw      $t0, SPU_REGS+0x1B8($k0) # current main volume: port word 43 is
        lw      $t1, SPU_REGS+0x19C($k0) # write-only, the registers read it back;
        lhu     $t2, SPU_REGS+0x1AE($k0) # ENDX (37) and STAT (40 high half, IRQ9)
        sw      $t0, PAGE+4*43($k0)      # come from the guest reads too
        sw      $t1, PAGE+4*37($k0)
        sh      $t2, PAGE+4*40+2($k0)
        jal     put_data
        li      $a3, 1                  # (always stored)
        move    $s5, $zero
spu_out:
        jal     spu_ram_get
        move    $a0, $s5
        jal     put_data
        li      $a3, 1
        addiu   $s5, $s5, 1
        bne     $s5, $s4, spu_out
        nop
        MARK    8
        jal     pack_end                # page 0 with the bitmap and size, scratchpad back
        nop
        li      $t0, 1
        sb      $t0, FINISH($k0)
        jal     get_page                # the registers back into the staging page
        move    $a0, $zero
        j       restore
        nop

load:
        .ifdef  DIAG
        li      $t0, 2                  # DIAG builds only save
        sb      $t0, FINISH($k0)
        j       restore
        nop
        .else
        jal     put_page                # keep the interrupted game's registers
        li      $a0, SCRATCH
        jal     get_page
        move    $a0, $zero
        lw      $t0, PAGE($k0)
        lbu     $t2, DISC($k0)          # a state of another disc does not load
        lui     $t1, %hi(MAGIC)
        ori     $t1, $t1, %lo(MAGIC)
        addu    $t1, $t1, $t2
        bne     $t0, $t1, bad
        nop
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
        li      $s7, RAM_PAGE           # next stored page
        # main RAM
        lui     $s0, 0x8000
        li      $s1, RAM_PAGE
2:      jal     get_data
        move    $a0, $s1
        beqz    $v0, 8f
        nop
        jal     copy_in
        li      $a1, 0x800
        b       9f
        nop
8:      jal     zero_ram
        nop
9:      addiu   $s1, $s1, 1
        bne     $s1, $s2, 2b
        nop
        # instruction cache: main RAM changed under it, so invalidate all
        # 256 lines (SR.IsC on: a cached store clears that line's tag)
        mfc0    $t0, $12
        lui     $t1, 0x0001
        or      $t1, $t0, $t1
        mtc0    $t1, $12
        lui     $t2, 0x8000
        li      $t3, 0x1000
3:      addiu   $t3, $t3, -16
        addu    $t4, $t2, $t3
        bnez    $t3, 3b
        sw      $zero, 0($t4)
        mtc0    $t0, $12
        nop
        lui     $t9, 0x1F80
        sw      $zero, 0x1814($t9)      # GP1 00: reset (FIFO, settings)
        lui     $t0, 0xE600             # GP0 E6: no mask test while uploading
        sw      $t0, 0x1810($t9)
        jal     vram_cmd                # GP0 A0: CPU -> VRAM, rows 0..R-1
        lui     $a0, 0xA000
        addu    $s3, $s3, $s2
7:      jal     get_data
        move    $a0, $s1
        beqz    $v0, 8f
        nop
        jal     gpu_in
        nop
        b       9f
        nop
8:      jal     gpu_zero
        nop
9:      addiu   $s1, $s1, 1
        bne     $s1, $s3, 7b
        nop
        # SPU RAM, then the registers (before io_load: I_STAT is set after
        # the SPU's IRQ line is back); from here every page is stored
        addiu   $s6, $s7, 1
        move    $s5, $zero
spu_in:
        jal     get_page
        move    $a0, $s6
        jal     spu_ram_put
        move    $a0, $s5
        addiu   $s6, $s6, 1
        addiu   $s5, $s5, 1
        bne     $s5, $s4, spu_in
        nop
        jal     get_page
        move    $a0, $s7
        jal     spu_load
        nop
        # scratchpad and GTE: control first; IRGB (28) before IR1-3 (it sets
        # them); SXYP (15), ORGB (29) and LZCR (31) follow from the others
        jal     get_page
        li      $a0, CPU_PAGE
        jal     cu2_on
        nop
        lui     $s0, 0x1F80
        jal     copy_in
        li      $a1, 0x400
        .irp    n, 0,2,4,6,8,10,12,14,16,18,20,22,24,26,28,30
        lw      $t0, GTE_CTRL+4*\n($k0)
        lw      $t1, GTE_CTRL+4*(\n+1)($k0)
        .word   0x48C00000 | (8 << 16) | (\n << 11)    # ctc2 t0, $n
        .word   0x48C00000 | (9 << 16) | ((\n+1) << 11)    # ctc2 t1, $n+1
        .endr
        GTE_W   28,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,16,17,18,19,20,21,22,23,24,25,26,27,30
        jal     io_load
        nop
        jal     get_page
        move    $a0, $zero
        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back
        nop
        li      $t0, 1
        j       restore
        sb      $t0, FINISH($k0)
        .endif
retry:
        .ifdef  DIAG                    # block 0 shows why (none: the GPU was busy)
        beqz    $a2, 1f
        nop
        jal     diag_block
        move    $v1, $zero
1:
        .endif
        lw      $t0, RETRIES($k0)
        li      $t1, RETRY_MAX+1
        addiu   $t0, $t0, 1
        beq     $t0, $t1, give_up       # no quiet point found in time: an error
        sw      $t0, RETRIES($k0)       # (restore clears it again then)
        li      $t0, 4
        j       restore_kept
        sb      $t0, FINISH($k0)
bad:
        jal     get_page
        li      $a0, SCRATCH
give_up:
        li      $t0, 2
        sb      $t0, FINISH($k0)

restore:                                # staging page holds the registers to return with
        sw      $zero, RETRIES($k0)     # the request ended: the next one starts at 0
restore_kept:
        sb      $zero, SPU_HOLD($k0)
        lw      $t0, PAGE+4*32($k0)
        lw      $t1, PAGE+4*33($k0)
        mthi    $t0
        mtlo    $t1
        lw      $t0, PAGE+4*34($k0)
        lw      $t1, PAGE+4*35($k0)
        mtc0    $t0, $12
        mtc0    $t1, $13
        GPRS    lw
        lw      $k1, PAGE+4*37($k0)     # EXP1 delay/size last: fetches are slow again after it
        lui     $k0, 0x1F80
        sw      $k1, 0x1008($k0)
        lui     $k0, 0xBF00
        lw      $k1, PAGE+4*36($k0)
        nop
        jr      $k1
        .word   0x42000010              # rfe (in the delay slot)

# I/O registers -> staging page. A MODE read clears the timer's reached
# bits (11, 12), so the hidden flags are read first and set back after.
io_save:
        lui     $t9, 0x1F80
        lw      $t0, 0x1070($t9)
        lw      $t1, 0x1074($t9)
        lw      $t2, 0x10F0($t9)
        lw      $t3, 0x10F4($t9)
        sw      $t0, IO+0x0($k0)
        sw      $t1, IO+0x4($k0)
        sw      $t2, IO+0x8($k0)
        sw      $t3, IO+0xC($k0)
        .irp    n, 0,1,2,3,4,5,6
        lw      $t0, 0x1080+0x10*\n($t9)
        lw      $t1, 0x1084+0x10*\n($t9)
        lw      $t2, 0x1088+0x10*\n($t9)
        sw      $t0, IO_DMA+12*\n($k0)
        sw      $t1, IO_DMA+12*\n+4($k0)
        sw      $t2, IO_DMA+12*\n+8($k0)
        .endr
        .irp    n, 0,1,2
        lbu     $t3, TFLAGS+\n($k0)
        lw      $t0, 0x1100+0x10*\n($t9)
        lw      $t1, 0x1104+0x10*\n($t9)
        lw      $t2, 0x1108+0x10*\n($t9)
        sw      $t3, IO_TFL+4*\n($k0)
        sb      $t3, TFLAGS+\n($k0)
        sw      $t0, IO_TMR+12*\n($k0)
        sw      $t1, IO_TMR+12*\n+4($k0)
        sw      $t2, IO_TMR+12*\n+8($k0)
        .endr
        lw      $t0, 0x1814($t9)        # GPUSTAT
        lui     $t1, 0x1000
        sw      $t0, IO_GPU($k0)
        .irp    n, 2,3,4,5
        ori     $t0, $t1, \n             # GP1 10h+n: E2-E5 into GPUREAD
        sw      $t0, 0x1814($t9)
        lw      $t0, 0x1810($t9)
        nop
        sw      $t0, IO_GPU+4*(\n-1)($k0)
        .endr
        lw      $t0, GPU_RANGES($k0)
        lw      $t1, GPU_RANGES+4($k0)
        sw      $t0, IO_GPU+20($k0)
        sw      $t1, IO_GPU+24($k0)
        sb      $zero, SET_IRQ_LO($k0)
        sb      $zero, SET_IRQ_HI($k0)
        b       set_pulse
        sb      $zero, SET_DMA($k0)

# a0 = GP0 command (C0 or A0): rectangle 0,0 1024 x s3 rows
vram_cmd:
        lui     $t9, 0x1F80
        sw      $a0, 0x1810($t9)
        sw      $zero, 0x1810($t9)
        sll     $t0, $s3, 16
        ori     $t0, $t0, 1024
        jr      $ra
        sw      $t0, 0x1810($t9)

# 512 GPUREAD words (one VRAM row) -> staging page
gpu_out:                                # (a3 = OR of the words)
        lui     $t9, 0x1F80
        addiu   $t8, $k0, PAGE
        addiu   $a1, $t8, 0x800
        move    $a3, $zero
8:      lw      $t0, 0x1810($t9)
        lw      $t1, 0x1810($t9)
        lw      $t2, 0x1810($t9)
        lw      $t3, 0x1810($t9)
        sw      $t0, 0($t8)
        sw      $t1, 4($t8)
        sw      $t2, 8($t8)
        sw      $t3, 12($t8)
        or      $t0, $t0, $t1
        or      $t2, $t2, $t3
        or      $a3, $a3, $t0
        or      $a3, $a3, $t2
        addiu   $t8, $t8, 16
        bne     $t8, $a1, 8b
        nop
        jr      $ra
        nop

# staging page -> 512 GP0 words (one VRAM row)
gpu_in:
        lui     $t9, 0x1F80
        addiu   $t8, $k0, PAGE
        addiu   $a1, $t8, 0x800
9:      lw      $t0, 0($t8)
        lw      $t1, 4($t8)
        lw      $t2, 8($t8)
        lw      $t3, 12($t8)
        sw      $t0, 0x1810($t9)
        sw      $t1, 0x1810($t9)
        sw      $t2, 0x1810($t9)
        sw      $t3, 0x1810($t9)
        addiu   $t8, $t8, 16
        bne     $t8, $a1, 9b
        nop
        jr      $ra
        nop

# SPU registers -> staging page: voice registers, globals and reverb
# registers by guest reads, the rest through the savestate port
spu_save:
        lui     $t9, 0x1F80
        move    $t7, $zero
10:     addu    $t6, $t9, $t7
        lhu     $t0, 0x1C00($t6)
        addu    $t5, $k0, $t7
        sh      $t0, SPU_REGS($t5)
        addiu   $t7, $t7, 2
        slti    $t0, $t7, 0x200
        bnez    $t0, 10b
        nop
        b       spu_ss
        li      $a0, 1

# staging page -> SPU registers: guest writes first (their side effects on
# the reverb and transfer addresses and the IRQ line are what the savestate
# port writes then set)
spu_load:
        lui     $t9, 0x1F80
        lui     $t4, SPU_NO_BUS >> 16
        ori     $t4, $t4, SPU_NO_BUS & 0xFFFF
        move    $t7, $zero              # halfword 0x1F801C00 + 2*t7
11:     addiu   $t0, $t7, -0xC0
        sltiu   $t1, $t0, 32
        beqz    $t1, 18f
        srlv    $t0, $t4, $t0
        andi    $t0, $t0, 1
        bnez    $t0, 12f
18:     sll     $t1, $t7, 1
        addu    $t2, $k0, $t1
        lhu     $t0, SPU_REGS($t2)
        addu    $t2, $t9, $t1
        sh      $t0, 0x1C00($t2)
12:     addiu   $t7, $t7, 1
        slti    $t0, $t7, 256
        bnez    $t0, 11b
        nop
        li      $a0, 2

# a0 = 1: savestate port words -> staging page; 2: staging page -> port
spu_ss:
        lui     $t3, %hi(SPU_SS)
        addiu   $t3, $t3, %lo(SPU_SS)
13:     lhu     $t4, 0($t3)             # range [t4, t5)
        lhu     $t5, 2($t3)
        addiu   $t3, $t3, 4
        beqz    $t5, 16f
        nop
14:     sb      $t4, SS_ADR($k0)
        srl     $t0, $t4, 8
        sb      $t0, SS_ADR+1($k0)
        sb      $a0, SS_CMD($k0)
        TIMEOUT_START
15:     TIMEOUT_STEP
        lbu     $t0, SS_CMD($k0)
        nop
        andi    $t0, $t0, 1
        bnez    $t0, 15b
        nop
        addiu   $t4, $t4, 1
        bne     $t4, $t5, 14b
        nop
        b       13b
        nop
16:     jr      $ra
        nop

# a0 -> SPU RAM page; SPU RAM -> staging page
spu_ram_get:
        li      $t9, 7
        b       page_cmd
        nop
# a0 -> SPU RAM page; staging page -> SPU RAM
spu_ram_put:
        li      $t9, 5
        b       page_cmd
        nop

# a0 -> page number; staging page -> save buffer
put_page:
        li      $t9, 1
        b       page_cmd
        nop
# a0 -> page number; save buffer -> staging page
get_page:
        li      $t9, 3
page_cmd:
        sb      $a0, PAGE_LO($k0)
        srl     $t8, $a0, 8
        sb      $t8, PAGE_HI($k0)
        sb      $t9, STATUS($k0)
        TIMEOUT_START
3:      TIMEOUT_STEP
        lbu     $t9, STATUS($k0)
        nop
        andi    $t9, $t9, 1
        bnez    $t9, 3b
        nop
        jr      $ra
        nop

# SR.CU2 on (the saved SR keeps the game's own setting)
cu2_on:                                 # (callers run a copy before the first GTE access)
        mfc0    $t0, $12
        lui     $t1, 0x4000
        or      $t0, $t0, $t1
        jr      $ra
        mtc0    $t0, $12

# a1 bytes (a multiple of 32): s0 (advanced) -> staging page
copy_out:                               # (a3 = OR of the words copied)
        addiu   $t8, $k0, PAGE
        addu    $t9, $t8, $a1
        move    $a3, $zero
4:      lw      $t0, 0($s0)
        lw      $t1, 4($s0)
        lw      $t2, 8($s0)
        lw      $t3, 12($s0)
        lw      $t4, 16($s0)
        lw      $t5, 20($s0)
        lw      $t6, 24($s0)
        lw      $t7, 28($s0)
        addiu   $s0, $s0, 32
        sw      $t0, 0($t8)
        sw      $t1, 4($t8)
        sw      $t2, 8($t8)
        sw      $t3, 12($t8)
        sw      $t4, 16($t8)
        sw      $t5, 20($t8)
        sw      $t6, 24($t8)
        sw      $t7, 28($t8)
        or      $t0, $t0, $t1
        or      $t2, $t2, $t3
        or      $t4, $t4, $t5
        or      $t6, $t6, $t7
        or      $t0, $t0, $t2
        or      $t4, $t4, $t6
        or      $a3, $a3, $t0
        or      $a3, $a3, $t4
        addiu   $t8, $t8, 32
        bne     $t8, $t9, 4b
        nop
        jr      $ra
        nop

# a1 bytes (a multiple of 32): staging page -> s0 (advanced)
copy_in:
        addiu   $t8, $k0, PAGE
        addu    $t9, $t8, $a1
5:      lw      $t0, 0($t8)
        lw      $t1, 4($t8)
        lw      $t2, 8($t8)
        lw      $t3, 12($t8)
        lw      $t4, 16($t8)
        lw      $t5, 20($t8)
        lw      $t6, 24($t8)
        lw      $t7, 28($t8)
        addiu   $t8, $t8, 32
        sw      $t0, 0($s0)
        sw      $t1, 4($s0)
        sw      $t2, 8($s0)
        sw      $t3, 12($s0)
        sw      $t4, 16($s0)
        sw      $t5, 20($s0)
        sw      $t6, 24($s0)
        sw      $t7, 28($s0)
        addiu   $s0, $s0, 32
        bne     $t8, $t9, 5b
        nop
        jr      $ra
        nop

        .ifdef  DIAG
# DIAG: GP0 02 fill, 16x16 of colour a2 at (16*v1, 16) from the display origin
diag_block:
        lw      $t0, GPU_RANGES($k0)    # vramRange: x [9:0], y [18:10]
        lui     $t2, 0x1F80
        andi    $t1, $t0, 0x3F0
        srl     $t0, $t0, 10
        andi    $t0, $t0, 0x1FF
        addiu   $t0, $t0, 16
        sll     $t0, $t0, 16
        or      $t0, $t0, $t1
        sll     $t1, $v1, 4
        addu    $t0, $t0, $t1
        lui     $t1, 0x0200
        or      $t1, $t1, $a2
        sw      $t1, 0x1810($t2)
        sw      $t0, 0x1810($t2)
        lui     $t1, 16
        ori     $t1, $t1, 16
        jr      $ra
        sw      $t1, 0x1810($t2)
diag_timeout:
        li      $a2, 0xFF               # red
        jal     diag_block
        addiu   $v1, $v1, 1
1:      b       1b
        nop
        .endif

# staging page -> I/O registers: DMA channels (without the start trigger),
# timers (TARGET, MODE, which zeroes CURRENT, then CURRENT), the hidden
# timer flags, DICR and I_STAT flags (acked, then set), I_MASK last.
io_load:
        .ifdef  NOOP                    # (NOOP builds never load: room in the first ROM)
        jr      $ra
        nop
        .else
        lui     $t9, 0x1F80
        lw      $t0, IO+0x8($k0)
        lui     $t4, 0xEFFF                     # CHCR without bit 28 (manual trigger)
        sw      $t0, 0x10F0($t9)
        ori     $t4, $t4, 0xFFFF
        .irp    n, 0,1,2,3,4,5,6
        lw      $t0, IO_DMA+12*\n($k0)
        lw      $t1, IO_DMA+12*\n+4($k0)
        lw      $t2, IO_DMA+12*\n+8($k0)
        sw      $t0, 0x1080+0x10*\n($t9)
        sw      $t1, 0x1084+0x10*\n($t9)
        and     $t2, $t2, $t4
        sw      $t2, 0x1088+0x10*\n($t9)
        .endr
        .irp    n, 0,1,2
        lw      $t0, IO_TMR+12*\n+8($k0)
        lw      $t1, IO_TMR+12*\n+4($k0)
        lw      $t2, IO_TMR+12*\n($k0)
        lw      $t3, IO_TFL+4*\n($k0)
        sw      $t0, 0x1108+0x10*\n($t9)
        sw      $t1, 0x1104+0x10*\n($t9)
        sw      $t2, 0x1100+0x10*\n($t9)
        sb      $t3, TFLAGS+\n($k0)
        .endr
        lw      $t0, IO+0xC($k0)                # DICR: flags 24-30 written as 1 ack them all
        lui     $t1, 0x7F00
        or      $t1, $t0, $t1
        sw      $t1, 0x10F4($t9)
        srl     $t1, $t0, 24
        sb      $t1, SET_DMA($k0)
        sw      $zero, 0x1070($t9)              # I_STAT: ack all
        lw      $t0, IO+0x0($k0)
        nop
        sb      $t0, SET_IRQ_LO($k0)
        srl     $t0, $t0, 8
        sb      $t0, SET_IRQ_HI($k0)
        lw      $t1, IO+0x4($k0)
        nop
        sw      $t1, 0x1074($t9)
        # GPU settings (VRAM is back already): GP0 01, E1-E6, GP1 05-08, 04, 03, IRQ
        lw      $t5, IO_GPU($k0)        # GPUSTAT
        lui     $t0, 0x0100
        sw      $t0, 0x1810($t9)        # GP0 01: clear the texture cache
        andi    $t0, $t5, 0x7FF         # E1: texpage, dither, draw to display (0-10),
        srl     $t1, $t5, 15            #     texture disable (GPUSTAT 15 -> 11)
        andi    $t1, $t1, 1
        sll     $t1, $t1, 11
        or      $t0, $t0, $t1
        lui     $t1, 0xE100
        or      $t0, $t0, $t1
        sw      $t0, 0x1810($t9)
        .irp    n, 2,3,4,5
        lw      $t0, IO_GPU+4*(\n-1)($k0)
        lui     $t1, 0xE000+0x100*\n
        or      $t0, $t0, $t1
        sw      $t0, 0x1810($t9)
        .endr
        srl     $t0, $t5, 11            # E6: mask bits (GPUSTAT 11-12)
        andi    $t0, $t0, 3
        lui     $t1, 0xE600
        or      $t0, $t0, $t1
        sw      $t0, 0x1810($t9)
        lw      $t6, IO_GPU+20($k0)     # ranges: vram[18:0], h[23:0] at 19, v[19:0] at 43
        lw      $t7, IO_GPU+24($k0)
        lui     $t1, 0x0007
        ori     $t1, $t1, 0xFFFF
        and     $t0, $t6, $t1
        lui     $t1, 0x0500
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)        # GP1 05
        srl     $t0, $t6, 19
        andi    $t1, $t7, 0x7FF
        sll     $t1, $t1, 13
        or      $t0, $t0, $t1
        lui     $t1, 0x0600
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)        # GP1 06
        srl     $t0, $t7, 11
        lui     $t1, 0x0700
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)        # GP1 07
        srl     $t0, $t5, 17            # GP1 08: HorRes1, VerRes, PAL, 24-bit, interlace (17-22)
        andi    $t0, $t0, 0x3F
        srl     $t1, $t5, 16            #   HorRes2 (16 -> 6)
        andi    $t1, $t1, 1
        sll     $t1, $t1, 6
        or      $t0, $t0, $t1
        srl     $t1, $t5, 14            #   reverse (14 -> 7)
        andi    $t1, $t1, 1
        sll     $t1, $t1, 7
        or      $t0, $t0, $t1
        lui     $t1, 0x0800
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)
        srl     $t0, $t5, 29            # GP1 04: DMA direction (29-30)
        andi    $t0, $t0, 3
        lui     $t1, 0x0400
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)
        srl     $t0, $t5, 23            # GP1 03: display disable (23)
        andi    $t0, $t0, 1
        lui     $t1, 0x0300
        or      $t0, $t0, $t1
        sw      $t0, 0x1814($t9)
        srl     $t0, $t5, 24            # GPU IRQ (24): GP0 1F sets it again
        andi    $t0, $t0, 1
        beqz    $t0, set_pulse
        lui     $t0, 0x1F00
        sw      $t0, 0x1810($t9)
        .endif

set_pulse:
        li      $t0, 1
        sb      $t0, SET_GO($k0)
        nop
        nop
        jr      $ra
        sb      $zero, SET_GO($k0)

# a0 = logical page, a3 = OR of its words: an all-zero main RAM page or VRAM
# row only sets its bitmap bit; anything else goes to the next stored page
put_data:
        bnez    $a3, 1f
        srl     $t0, $a0, 3
        lui     $t1, 0x1F80
        addu    $t1, $t1, $t0
        lbu     $t2, BITMAP($t1)
        andi    $t0, $a0, 7
        li      $t3, 1
        sllv    $t3, $t3, $t0
        or      $t2, $t2, $t3
        jr      $ra
        sb      $t2, BITMAP($t1)
1:      move    $a0, $s7
        j       put_page
        addiu   $s7, $s7, 1

# savestate port words saved: current voice volumes, envelope counters,
# voice records; the reverb pointer, transfer address, IRQ address and
# control, ENDX, the IRQ line and current volume ([first, last + 1) pairs)
        .p2align 2
SPU_SS:
        .hword  2, 7, 37, 38, 40, 41, 43, 44, 256, 304, 320, 370, 384, 480, 0, 0

        .org    0xFF4
spu_pages:
        .word   256
vram_rows:
        .word   512
ram_pages:
        .word   1024

# ----------------------------------------------------------------------------
# Second ROM (0x1C00-0x1FFF)
# ----------------------------------------------------------------------------
        .org    0x1C00

# a0 = logical page -> v0 = 1 with its stored page in the staging page,
# 0 when it was all zero
get_data:
        srl     $t0, $a0, 3
        lui     $t1, 0x1F80
        addu    $t1, $t1, $t0
        lbu     $t2, BITMAP($t1)
        andi    $t0, $a0, 7
        srlv    $t2, $t2, $t0
        andi    $t2, $t2, 1
        bnez    $t2, 1f
        move    $v0, $zero
        li      $v0, 1
        move    $a0, $s7
        j       get_page
        addiu   $s7, $s7, 1
1:      jr      $ra
        nop

# 2 KiB of zeros at s0 (advanced)
zero_ram:
        addiu   $t8, $s0, 0x800
1:      sw      $zero, 0($s0)
        sw      $zero, 4($s0)
        sw      $zero, 8($s0)
        sw      $zero, 12($s0)
        sw      $zero, 16($s0)
        sw      $zero, 20($s0)
        sw      $zero, 24($s0)
        sw      $zero, 28($s0)
        addiu   $s0, $s0, 32
        bne     $s0, $t8, 1b
        nop
        jr      $ra
        nop

# 512 zero GP0 words (one VRAM row)
gpu_zero:
        lui     $t9, 0x1F80
        li      $t8, 128
1:      sw      $zero, 0x1810($t9)
        sw      $zero, 0x1810($t9)
        sw      $zero, 0x1810($t9)
        sw      $zero, 0x1810($t9)
        addiu   $t8, $t8, -1
        bnez    $t8, 1b
        nop
        jr      $ra
        nop

# end of a save: page 0 again with the bitmap (words 64-127) and the stored
# page count (word 38), which is also the size the Pocket copies; then the
# game's scratchpad back from page 1
pack_end:
        move    $s5, $ra
        jal     get_page
        move    $a0, $zero
        lui     $t1, 0x1F80
        addiu   $t2, $k0, PAGE+0x100
        li      $t3, 0x100
1:      lw      $t0, BITMAP($t1)
        addiu   $t1, $t1, 4
        sw      $t0, 0($t2)
        addiu   $t3, $t3, -4
        bnez    $t3, 1b
        addiu   $t2, $t2, 4
        sw      $s7, PAGE+4*38($k0)
        jal     put_page
        move    $a0, $zero
        sb      $s7, SIZE_LO($k0)
        srl     $t0, $s7, 8
        sb      $t0, SIZE_HI($k0)
        jal     get_page
        li      $a0, CPU_PAGE
        lui     $s0, 0x1F80
        jal     copy_in
        li      $a1, 0x400
        jr      $s5
        nop

# save: v0 = 0 at a CD quiet point (its state then in page 0 words 40-42)
cd_quiet:
        lui     $t9, 0x1F80
        lbu     $t0, CD_STATE+7($k0)    # busy
        lbu     $t1, CD_STATE($k0)      # stat
        bnez    $t0, 9f
        andi    $t1, $t1, 0x40          # seeking: its target is not kept
        bnez    $t1, 9f
        nop
        lbu     $t0, 0x1800($t9)
        nop
        andi    $t2, $t0, 0xE8          # BUSYSTS, DRQSTS, RSLRRDY 0 and PRMEMPT 1
        li      $t3, 8
        bne     $t2, $t3, 9f
        andi    $t4, $t0, 3             # the game's index
        sb      $zero, 0x1800($t9)
        lbu     $t5, 0x1803($t9)        # IRQENA
        li      $t2, 1
        sb      $t2, 0x1800($t9)
        lbu     $t2, 0x1803($t9)        # IRQFLAG
        sb      $t4, 0x1800($t9)        # the game's index back
        andi    $t2, $t2, 7
        bnez    $t2, 9f
        andi    $t5, $t5, 0x1F
        sll     $t4, $t4, 8
        or      $t5, $t5, $t4
        lw      $t0, CD_STATE($k0)
        lw      $t1, CD_STATE+4($k0)    # busy (bits 31:24) read last: still 0?
        sw      $t5, PAGE+4*42($k0)
        srl     $t2, $t1, 24
        bnez    $t2, 9f
        sw      $t0, PAGE+4*40($k0)
        sw      $t1, PAGE+4*41($k0)
        jr      $ra
        move    $v0, $zero
9:      jr      $ra
        li      $v0, 1

# a0 = command | parameter count << 8, a1 = parameters (the first in bits 7:0)
# -> v0 = the first response byte (stat), or -1 without INT3/INT2 in time or
# with an error; the response is read and the flags acked
cd_cmd:
        lui     $t9, 0x1F80
        sb      $zero, 0x1800($t9)
        srl     $t0, $a0, 8
        beqz    $t0, 2f
        move    $t1, $a1
1:      sb      $t1, 0x1802($t9)
        addiu   $t0, $t0, -1
        bnez    $t0, 1b
        srl     $t1, $t1, 8
2:      sb      $a0, 0x1801($t9)
        lui     $a2, 0x0004
# (t9 = 0x1F80, a2 = loops to wait) the next response, as cd_cmd
cd_wait:
        li      $t0, 1
        sb      $t0, 0x1800($t9)
3:      lbu     $t0, 0x1803($t9)
        addiu   $a2, $a2, -1
        andi    $t0, $t0, 7
        bnez    $t0, 4f
        nop
        bnez    $a2, 3b
        nop
        jr      $ra
        li      $v0, -1
4:      lbu     $v0, 0x1801($t9)
5:      lbu     $t1, 0x1800($t9)
        nop
        andi    $t1, $t1, 0x20
        beqz    $t1, 6f
        nop
        b       5b
        lbu     $t1, 0x1801($t9)        # the rest of the response
6:      li      $t1, 0x1F
        sb      $t1, 0x1803($t9)        # ack
        li      $t1, 3
        beq     $t0, $t1, 7f
        li      $t1, 2
        beq     $t0, $t1, 7f
        nop
        li      $v0, -1
7:      jr      $ra
        nop

# load: the page bitmap from page 0 (in the staging page) into the
# scratchpad, then the drive: CD interrupts off, flags acked, wait for the
# motor (a wake from Sleep starts it fresh), pause it, mode, filter, demute
load_start:
        move    $s5, $ra
        lui     $t1, 0x1F80
        addiu   $t2, $k0, PAGE+0x100
        li      $t3, 0x100
1:      lw      $t0, 0($t2)
        addiu   $t2, $t2, 4
        sw      $t0, BITMAP($t1)
        addiu   $t3, $t3, -4
        bnez    $t3, 1b
        addiu   $t1, $t1, 4
        lui     $t9, 0x1F80
        li      $t0, 1
        sb      $t0, 0x1800($t9)
        sb      $zero, 0x1802($t9)      # IRQENA 0 until cd_resume
        li      $t0, 0x5F
        sb      $t0, 0x1803($t9)        # ack all, empty the parameter FIFO
        li      $s6, 2000               # Getstat until the motor runs, the shell closed (a few s)
2:      jal     cd_cmd
        li      $a0, 0x01
        andi    $t0, $v0, 0x12
        li      $t1, 0x02
        beq     $t0, $t1, 4f
        addiu   $s6, $s6, -1
        li      $t0, 0x1000             # short waits: the load goes on soon after the motor runs
3:      addiu   $t0, $t0, -1
        bnez    $t0, 3b
        nop
        bnez    $s6, 2b
        nop
4:      andi    $t0, $v0, 0xE0          # reading, seeking or playing: pause it
        beqz    $t0, 5f
        nop
        jal     cd_cmd
        li      $a0, 0x09
        lui     $a2, 0x0010
        jal     cd_wait                 # Pause's second response
        nop
5:      lbu     $a1, PAGE+4*40+1($k0)   # mode
        jal     cd_cmd
        li      $a0, 0x10E
        lhu     $a1, PAGE+4*40+2($k0)   # XA filter file, channel
        jal     cd_cmd
        li      $a0, 0x20D
        jal     cd_cmd
        li      $a0, 0x0C               # Demute
        jr      $s5
        nop

# end of a load (page 0 in the staging page, I_STAT restored): the drive
# reads on from the sector after the last one the game got, or plays on from
# the position; then the game's IRQENA and index
cd_resume:
        move    $s5, $ra
        lbu     $t0, PAGE+4*40($k0)     # stat at the save
        lw      $s6, PAGE+4*41($k0)     # LBA in bits 23:0
        andi    $t1, $t0, 0x20
        bnez    $t1, 1f
        andi    $t1, $t0, 0x80
        beqz    $t1, 3f
        li      $s3, 0x03               # Play
        b       2f
        nop
1:      addiu   $s6, $s6, 1
        li      $s3, 0x06               # ReadN
2:      jal     msf
        nop
        jal     cd_cmd
        li      $a0, 0x302              # Setloc
        jal     cd_cmd
        move    $a0, $s3
3:      lui     $t9, 0x1F80
        lbu     $t0, PAGE+4*42($k0)     # IRQENA
        lbu     $t1, PAGE+4*42+1($k0)   # index
        li      $t2, 1
        sb      $t2, 0x1800($t9)
        sb      $t0, 0x1802($t9)
        jr      $s5
        sb      $t1, 0x1800($t9)

# s6 = LBA (bits 23:0) -> a1 = Setloc parameters: BCD minute, second, sector
msf:
        sll     $t0, $s6, 8
        srl     $t0, $t0, 8
        addiu   $t0, $t0, 150
        li      $t1, 75
        divu    $zero, $t0, $t1
        li      $t5, 10
        mflo    $t0
        mfhi    $t2                     # sector
        li      $t1, 60
        nop
        divu    $zero, $t0, $t1
        nop
        mflo    $t3                     # minute
        mfhi    $t4                     # second
        nop
        nop
        divu    $zero, $t3, $t5
        nop
        mflo    $t6
        mfhi    $t7
        sll     $t6, $t6, 4
        or      $a1, $t6, $t7
        divu    $zero, $t4, $t5
        nop
        mflo    $t6
        mfhi    $t7
        sll     $t6, $t6, 4
        or      $t6, $t6, $t7
        sll     $t6, $t6, 8
        or      $a1, $a1, $t6
        divu    $zero, $t2, $t5
        nop
        mflo    $t6
        mfhi    $t7
        sll     $t6, $t6, 4
        or      $t6, $t6, $t7
        sll     $t6, $t6, 16
        jr      $ra
        or      $a1, $a1, $t6

