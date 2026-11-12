#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine R1: a routine-only update of the PXS6 save routine (no RTL change).

Design note with the RTL evidence: docs/save-states-finish/r1-design.md.
R1 adds to the routine, as text transforms on the PXS6 text
(prepare_spu_exact_voices.pxs6_firmware()):
  1. saved-disc remount on a load (EXP1 DISC / DISC_SET of prepare_save_disc_set.py),
     which is also the wait for the boot-time mount on a wake; the disc is no
     longer part of the magic compare;
  2. memory control 0x1F801000-0x1F801020, RAM size 0x1F801060, cache control
     0xFFFE0130;
  3. MDEC: a quiet-point condition on a save; reset, output-format bits and the
     DMA request enables on a load;
  4. JOY_MODE, JOY_CTRL, JOY_BAUD;
Not in R1 (r1-design.md, known limits): GP1(09h) "allow texture disable" (a load
still clears it; no guest read exists and a save must not draw), DualShock mode.
New state words: page 0 words 48-61. No existing word moves. The magic word's first letter becomes lower case
(MAGIC + 0x20, "pXS6" + disc): a state of the routine before R1 is refused by
R1, and an R1 state by the routine before it.
Only the stage-2 text and comments change; the stub's instructions (entry,
unpacker, exit, both SAVE_MODE writes) stay as they are.
Usage: prepare_save_r1.py --output OUT   (writes OUT/staged-r1.S, OUT/psx_save_rom40.hex)
"""
import argparse
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# sha256 of the PXS6 routine text build() starts from (firmware_text() itself only needs its anchors)
PXS6_SHA = '775a58c7d872873eb6e0d040138cefbcc4ef9632584ef04787dbae779e2cb224'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'R1 anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def after(text, anchor, added):
    return once(text, anchor, anchor + added)


def before(text, anchor, added):
    return once(text, anchor, added + anchor)


EQUATES = '''\
        # R1 (scripts/prepare_save_r1.py, docs/save-states-finish/r1-design.md)
        .equ    DISC_SET, 0x1823        # write [2:0] disc - 1 into the menu setting, [3] silent swap;
                                        # read [0] settled, [1] wrong disc (valid once settled)
        .equ    MAGIC_R1, MAGIC + 0x20  # first letter lower case; + the disc - 1 (bits 2:0)
        .equ    R1_MEM, PAGE+4*48       # page 0 words 48-56: 0x1F801000-0x1F801020 (word 50 = the
                                        # routine's own EXP1 setting; the game's is word 37)
        .equ    R1_RAMSIZE, PAGE+4*57   # 0x1F801060
        .equ    R1_CACHE, PAGE+4*58     # 0xFFFE0130
        .equ    R1_MDEC, PAGE+4*59      # MDEC status 0x1F801824 at the save
        .equ    R1_JOY, PAGE+4*60       # JOY_MODE (15:0), JOY_CTRL (31:16)
        .equ    R1_BAUD, PAGE+4*61      # JOY_BAUD
        .equ    DISC_POLLS, 0x00800000  # remount wait: at least 18 clk1x a look, 4.4 s or more
        .equ    MDEC_LOOKS, 1024        # MDEC idle for at least 8 clk1x a look, 0.24 ms or more
'''

# (iii) comments only: the instruction after each SAVE_MODE write
SAVE_MODE_ON_OLD = '''\
        sb      $t0, SAVE_MODE($k0)     # access is this read of 0x80000000 (bits 22:21 0), so they do not
        lbu     $t0, STATUS($k0)        # (the write is done before SDRAM is read)
'''
SAVE_MODE_ON_NEW = SAVE_MODE_ON_OLD + '''\
        # Late SAVE_MODE (R1 keeps this): the instruction after the write is this EXP1 read, fetched
        # from the EXP1 ROM, so neither its fetch nor its data access is a RAM access; no RAM access
        # starts within one clk1x of the write. Nothing may be put between the two.
'''
SAVE_MODE_OFF_OLD = '''\
        sb      $zero, SAVE_MODE($k0)   # SAVE_MODE off: the address does not move
        sb      $zero, CD_FREEZE($k0)
'''
SAVE_MODE_OFF_NEW = SAVE_MODE_OFF_OLD + '''\
        # Late SAVE_MODE (R1 keeps this): the CD_FREEZE write above is the instruction after the
        # SAVE_MODE write, an EXP1 access fetched from the EXP1 ROM; the stub runs uncached from
        # that ROM up to the jr that leaves it. Nothing may be put between the two.
'''

# 3. MDEC as a quiet-point condition of a save, before the CD's
QUIET_OLD = '''\
        bnez    $t0, retry
        nop
        jal     cd_quiet
        nop
'''
QUIET_NEW = '''\
        bnez    $t0, retry
        nop
        jal     mdec_quiet              # R1: no MDEC command, no MDEC data on its way (t2 = first half)
        nop
        bnez    $v0, retry
        nop
        jal     cd_quiet
        nop
'''

# 1. the magic word; 2-4. the R1 words of page 0 (the staging page holds page 0 here)
SAVE_OLD = '''\
save:
        lbu     $t2, DISC($k0)          # the disc in the drive: a load needs the same
        lui     $t0, %hi(MAGIC)
        ori     $t0, $t0, %lo(MAGIC)
        addu    $t0, $t0, $t2
        sw      $t0, PAGE($k0)
'''
SAVE_NEW = '''\
save:
        lbu     $t2, DISC($k0)          # the disc in the drive: a load mounts it again (R1)
        lui     $t0, %hi(MAGIC_R1)
        ori     $t0, $t0, %lo(MAGIC_R1)
        addu    $t0, $t0, $t2
        sw      $t0, PAGE($k0)
        # R1: registers a cold boot sets differently, into page 0 words 48-61. Plain reads:
        # memctrl_lean, the CPU's cache control word, the MDEC status and joypad_card1's
        # 0x1F801048 / 0x1F80104E reads have no side effect (JOY_STAT at 0x1F801044 has, not read).
        lui     $t9, 0x1F80
        addiu   $t8, $k0, R1_MEM
        addiu   $t7, $t9, 0x1000        # memory control 0x1F801000 .. 0x1F801020
        addiu   $t6, $t9, 0x1024
71:     lw      $t0, 0($t7)
        addiu   $t7, $t7, 4
        sw      $t0, 0($t8)
        bne     $t7, $t6, 71b
        addiu   $t8, $t8, 4
        lw      $t0, 0x1060($t9)        # RAM size
        lui     $t1, 0xFFFE
        lw      $t1, 0x130($t1)         # cache control
        lw      $t2, 0x1824($t9)        # MDEC status (idle: mdec_quiet)
        lw      $t3, 0x1048($t9)        # JOY_MODE, JOY_CTRL << 16
        lhu     $t4, 0x104E($t9)        # JOY_BAUD
        sw      $t0, R1_RAMSIZE($k0)
        sw      $t1, R1_CACHE($k0)
        sw      $t2, R1_MDEC($k0)
        sw      $t3, R1_JOY($k0)
        sw      $t4, R1_BAUD($k0)
'''

# 1. remount, then 2-4 while the state's page 0 is in the staging page
LOAD_OLD = '''\
        lw      $t0, PAGE($k0)
        lbu     $t2, DISC($k0)          # a state of another disc does not load
        lui     $t1, %hi(MAGIC)
        ori     $t1, $t1, %lo(MAGIC)
        addu    $t1, $t1, $t2
        bne     $t0, $t1, bad
        nop
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
'''
LOAD_NEW = '''\
        lw      $t0, PAGE($k0)
        lui     $t1, %hi(MAGIC_R1)
        ori     $t1, $t1, %lo(MAGIC_R1)
        xor     $s6, $t0, $t1           # R1: the format digits must match; bits 2:0 are the
        sltiu   $t0, $s6, 8             # state's disc - 1 (a state from before R1 does not load)
        beqz    $t0, bad
        lbu     $s5, DISC($k0)          # the menu's disc now: put back if the state's does not mount
        # R1 remount, before anything of the running game is changed (CD_FREEZE is off: a
        # load never sets it). On a wake this is also the wait for the boot-time mount.
        jal     disc_mount
        move    $a0, $s6
        beqz    $v0, 72f
        nop
        jal     disc_mount              # not mounted (wrong disc or no answer in time): the menu's
        move    $a0, $s5                # disc again, so DISC names the disc in the drive, and no load
        b       bad
        nop
72:
        # R1: memory control first (the CD and SPU accesses below then run with the bus
        # timing of the state; EXP1 keeps the routine's own setting, word 50, until the exit)
        lui     $t9, 0x1F80
        addiu   $t8, $k0, R1_MEM
        addiu   $t7, $t9, 0x1000
        addiu   $t6, $t9, 0x1024
73:     lw      $t0, 0($t8)
        addiu   $t8, $t8, 4
        sw      $t0, 0($t7)             # (memctrl_lean takes whole words at aligned addresses only)
        addiu   $t7, $t7, 4
        bne     $t7, $t6, 73b
        nop
        lw      $t0, R1_RAMSIZE($k0)
        lw      $t1, R1_CACHE($k0)
        sw      $t0, 0x1060($t9)
        lui     $t0, 0xFFFE
        sw      $t1, 0x130($t0)
        # R1 joypad port: transfer off (CTRL 0: baud counter, transmitting and the ACK wait
        # cleared), reset (CTRL bit 6: both byte flags, the IRQ request and ACK cleared),
        # then MODE, BAUD and CTRL last (bits 1:0 = 3 would start a filled transmit byte,
        # and the reset emptied it). Halfword writes: a word write reaches JOY_MODE only.
        sh      $zero, 0x104A($t9)
        li      $t0, 0x40
        sh      $t0, 0x104A($t9)
        lhu     $t0, R1_JOY($k0)
        lhu     $t1, R1_JOY+2($k0)
        lhu     $t2, R1_BAUD($k0)
        sh      $t0, 0x1048($t9)
        sh      $t2, 0x104E($t9)
        sh      $t1, 0x104A($t9)
        # R1 MDEC: reset (control bit 31: command state, both FIFOs and status 23-26
        # cleared; the tables stay), then status 23-26 back by a decode command of one
        # parameter word that holds two end-of-block codes (no block, nothing put out; the
        # command ends with 15:0 = 0xFFFF as after any decode), then the request enables:
        # data-in (control 30) as status 28 showed it with the input FIFO empty; data-out
        # (control 29) cannot be read with the output FIFO empty and is set like data-in.
        lw      $t3, R1_MDEC($k0)
        lui     $t0, 0x8000
        sw      $t0, 0x1824($t9)
        srl     $t0, $t3, 23            # status 26:23 -> command 28:25
        andi    $t0, $t0, 15
        sll     $t0, $t0, 25
        lui     $t1, 0x2000             # command 1 (decode), 1 word
        or      $t0, $t0, $t1
        ori     $t0, $t0, 1
        sw      $t0, 0x1820($t9)
        lui     $t0, 0xFE00
        ori     $t0, $t0, 0xFE00
        sw      $t0, 0x1820($t9)
        srl     $t0, $t3, 28
        andi    $t0, $t0, 1
        sll     $t1, $t0, 30
        sll     $t0, $t0, 29
        or      $t0, $t0, $t1
        sw      $t0, 0x1824($t9)
        jal     load_start              # page bitmap into the scratchpad, drive paused and set up
'''

SUBS_ANCHOR = "# I/O registers -> staging page. A MODE read clears the timer's reached\n"
SUBS = '''\
# R1 load: a0 = disc - 1 -> v0 = 0 with that disc mounted and settled; 2 = the cue
# reader could not open it and kept the old disc, or nothing settled in time.
# The disc is set through the menu's setting (DISC_SET, silent: no lid sequence)
# unless it is the menu's disc already and settled. DISC is read before the settled
# bit in each look: DISC changes only when the swap for the new setting starts
# (psx_disc_menu selected), and settled is 0 from the setting's change to the
# swap's end, so "DISC = a0, then settled" cannot be the state before the swap.
disc_mount:
        lbu     $t0, DISC($k0)
        lbu     $t1, DISC_SET($k0)
        bne     $t0, $a0, 76f
        andi    $t1, $t1, 1
        bnez    $t1, 78f
        nop
76:     ori     $t0, $a0, 8             # silent
        sb      $t0, DISC_SET($k0)
        lui     $t2, DISC_POLLS >> 16
77:     lbu     $t0, DISC($k0)
        lbu     $t1, DISC_SET($k0)
        xor     $t0, $t0, $a0
        andi    $t1, $t1, 1
        sltiu   $t0, $t0, 1             # this disc ...
        and     $t0, $t0, $t1           # ... and settled
        bnez    $t0, 78f
        addiu   $t2, $t2, -1
        bnez    $t2, 77b
        nop
        b       79f                     # no answer in time
        li      $v0, 2
78:     lbu     $v0, DISC_SET($k0)      # settled: the wrong-disc bit is valid
        nop
        andi    $v0, $v0, 2
79:     jr      $ra
        sb      $a0, DISC_SET($k0)      # silent off (the same disc: the menu setting stays)

# R1 save: v0 = 0 with the MDEC idle for MDEC_LOOKS looks: no command running
# (status 29 = 0), data-in FIFO not full (30 = 0), data-out FIFO empty (31 = 1).
# A macroblock still in the IDCT / colour stages shows none of these; it reaches
# the output FIFO within about 3,400 clk1x (r1-design.md), less than the looks
# take, and 31 = 0 then. Busy: v0 = t2 (1 in the first half of the retries, as
# the pad session: a game that leaves a command open must still be able to save).
mdec_quiet:
        lui     $t9, 0x1F80
        li      $t3, MDEC_LOOKS
        lui     $t5, 0x8000
80:     lw      $t0, 0x1824($t9)
        lui     $t4, 0xE000
        and     $t0, $t0, $t4
        bne     $t0, $t5, 81f
        addiu   $t3, $t3, -1
        bnez    $t3, 80b
        nop
        jr      $ra
        move    $v0, $zero
81:     jr      $ra
        move    $v0, $t2

'''

def firmware_text(text):
    """text: the PXS6 routine (prepare_spu_exact_voices.pxs6_firmware()) -> the R1 routine.

    Every change is an exact-anchor replacement; a missing or repeated anchor raises."""
    if 'MAGIC_R1' in text:
        raise ValueError('R1 is applied already')
    text = after(text, '        .equ    SS_CMD, 0x1822\n', EQUATES)
    text = once(text, SAVE_MODE_ON_OLD, SAVE_MODE_ON_NEW)
    text = once(text, SAVE_MODE_OFF_OLD, SAVE_MODE_OFF_NEW)
    text = once(text, QUIET_OLD, QUIET_NEW)
    text = once(text, SAVE_OLD, SAVE_NEW)
    text = once(text, LOAD_OLD, LOAD_NEW)
    text = before(text, SUBS_ANCHOR, SUBS)
    return text


def stub_bytes(source):
    """The stub (.text) of an image, for the check that R1 leaves it alone."""
    import subprocess, tempfile
    from build_staged_save_fw import LINK
    from tool_paths import find_tool
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'h.ld').write_text(LINK)
        subprocess.run([find_tool('llvm-mc'), '-triple=mipsel', '-mcpu=mips1', '-filetype=obj', str(source), '-o', str(tmp / 'h.o')], check=True)
        subprocess.run([find_tool('ld.lld'), '-m', 'elf32ltsmip', '-T', str(tmp / 'h.ld'), '-e', 'entry', '--no-dynamic-linker',
                        '-o', str(tmp / 'h.elf'), str(tmp / 'h.o')], check=True)
        subprocess.run([find_tool('llvm-objcopy'), '-O', 'binary', '-j', '.text', str(tmp / 'h.elf'), str(tmp / 't.bin')], check=True)
        return (tmp / 't.bin').read_bytes()


def packed_bytes(image, stub_len):
    """Bytes of the packed stage 2 ({stamp, length, stream}) in an image: the ROM room is
    0xFF4 - stub + 0x400, less build_staged_save_fw.free_bytes()."""
    from build_staged_save_fw import free_bytes
    return 0xFF4 - stub_len + 0x400 - free_bytes(image)


def build(output_dir):
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_spu_exact_voices import pxs6_firmware
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pxs6 = pxs6_firmware()
    if PXS6_SHA and hashlib.sha256(pxs6.encode()).hexdigest() != PXS6_SHA:
        raise ValueError('the PXS6 routine text changed: review R1 against it')
    base = output_dir / 'staged-pxs6.S'
    base.write_text(pxs6)
    source = output_dir / 'staged-r1.S'
    source.write_text(firmware_text(pxs6))
    old_image, old_stage = image_parts(source=base)
    image, stage = image_parts(source=source)          # stops if the packed stage 2 does not fit
    old_stub, stub = stub_bytes(base), stub_bytes(source)
    if old_stub != stub:
        raise ValueError('R1 changed the stub (entry, unpacker, exit): only stage 2 may change')
    rom = output_dir / 'psx_save_rom40.hex'
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    report = dict(source=str(source), rom=str(rom),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  pxs6_sha256=hashlib.sha256(pxs6.encode()).hexdigest(),
                  stub_bytes=len(stub), stub_unchanged=True,
                  stage2_bytes_before=len(old_stage), stage2_bytes=len(stage),
                  packed_bytes_before=packed_bytes(old_image, len(old_stub)), packed_bytes=packed_bytes(image, len(stub)),
                  free_bytes_before=free_bytes(old_image), free_bytes=free_bytes(image))
    print(f"R1 routine: {source}")
    print(f"unpacked stage 2: {report['stage2_bytes']} bytes (PXS6: {report['stage2_bytes_before']})")
    print(f"packed stage 2:   {report['packed_bytes']} bytes (PXS6: {report['packed_bytes_before']})")
    print(f"free ROM bytes:   {report['free_bytes_before']} before, {report['free_bytes']} after")
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--output', type=Path, required=True)
    build(ap.parse_args().output)
