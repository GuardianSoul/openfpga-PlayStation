#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "diag-refusal" (diagnostic ROM only, never a release): records why the last load was
refused, so a refused wake (the BIOS boots the game fresh) can be read from the next Memory.

Seven words in SDRAM above main RAM, 0x8021F000-0x8021F018 (SAVE_MODE is on from the routine's entry to
restore_kept; CZ_TAB ends at 0x80213FFF, CZ_SHADOW starts at 0x80220000; the guest cannot reach it,
so it survives the BIOS boot and the game):
  D0  $ra at `bad`: the return address of the last call before the refusal (decoder: source line)
  D1  $k1 at `bad`: bit 0 the early answer was given, bit 2 a load, bit 3 no boot name in RAM
  D2  $v0 on entry to path 70: 2 the state's disc did not mount (D3 = 0: no answer in time, 1: settled
      but the wrong-disc bit), 0 the disc identity differed (D3 = the mounted DISC_ID),
      1 the drive never got ready (cd_ready)
  D3  $t0 on entry to path 70 (with D2 = 2: 0 no answer in time, 1 the wrong-disc bit)
  D4  $t0 at `bad` (a refusal in cz_load's checksum compare: the CSUM read; bit 31 flipped = a dropped
      upload word, the card slot's overflow)
  D5  $k0 when a load reached the format check (a load was seen at all)
  D6  $v0 at `bad` (cz_load's compare: the expected sum; D4 ^ D6 = 0x80000000: only the overflow)
D2-D3 mean something only when D0 is the return address of path 70's disc_mount call.
The save copies D0-D6 into page 0 words 200-206 before page 0 is summed and put (they are in the
checksum like the rest of page 0); a load ignores those words. SDRAM keeps them until the next
refusal or load; after a power cycle they are whatever SDRAM held (the decoder checks D5 and D0).
Usage (routine source to routine source): see firmware_text; build with prepare_save_rom_ext.builder().
"""
TAG = 'diag-refusal'
BASE = 0x8021F000

STAMP_OLD = '''\
        subu    $v0, $t3, $v0           # checksum: the stream's sum must be 2T - page 1 - page 0 (v0 kept to cz_end)
        lw      $t0, PAGE($k0)
'''
STAMP_NEW = '''\
        subu    $v0, $t3, $v0           # checksum: the stream's sum must be 2T - page 1 - page 0 (v0 kept to cz_end)
        lui     $t0, 0x8022             # diag-refusal: D5 = k0, a load reached the format check
        sw      $k0, -0xFEC($t0)
        lw      $t0, PAGE($k0)
'''
P70_OLD = '''\
70:     jal     disc_mount              # not mounted (wrong disc or no answer in time) or not the state's
'''
P70_NEW = '''\
70:     move    $t3, $v0                # diag-refusal: why path 70 (D2, D3; disc_mount uses t0-t2 only)
        move    $t4, $t0
        jal     disc_mount              # not mounted (wrong disc or no answer in time) or not the state's
diag_after_70:
'''
BAD_OLD = '''\
bad:
        li      $a0, SCRATCH
'''
BAD_NEW = '''\
bad:
        lui     $t9, 0x8022             # diag-refusal: D0-D4, D6 (t9: bad's next use sets it)
        sw      $ra, -0x1000($t9)
        sw      $k1, -0xFFC($t9)
        sw      $t3, -0xFF8($t9)
        sw      $t4, -0xFF4($t9)
        sw      $t0, -0xFF0($t9)
        sw      $v0, -0xFE8($t9)
        li      $a0, SCRATCH
'''
SAVE_OLD = '''\
        sw      $s1, CZ_LEN($k0)        # compress
'''
SAVE_NEW = '''\
        sw      $s1, CZ_LEN($k0)        # compress
        lui     $t0, 0x8022             # diag-refusal: D0-D6 into page 0 words 200-206
        addiu   $t0, $t0, -0x1000
        addiu   $t2, $k0, PAGE+4*200
        li      $t3, 28
1:      lw      $t1, 0($t0)
        addiu   $t0, $t0, 4
        sw      $t1, 0($t2)
        addiu   $t3, $t3, -4
        bnez    $t3, 1b
        addiu   $t2, $t2, 4
'''
STEPS = [(STAMP_OLD, STAMP_NEW), (P70_OLD, P70_NEW), (BAD_OLD, BAD_NEW), (SAVE_OLD, SAVE_NEW)]
# deliberate faults the bench must catch
FAULTS = {'no-record': [(BAD_NEW, BAD_OLD)], 'no-copy': [(SAVE_NEW, SAVE_OLD)]}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# diag-refusal:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    for old, new in STEPS:
        text = once(text, old, new)
    return text


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
