#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "early-load" (docs/save-states-finish/early-load.md): three parts on the
big-build routine with the MDEC table tap (prepare_save_mdec_tap.py --routine), ROM only.

answer   A load whose disc is not the mounted and settled one is answered OK before the
         remount (FINISH 1 right after the state is validated) and not answered again at its
         end. The Pocket serves no data slot while a Memory load is unanswered; the remount
         needs the cue reader's file commands and the track table read, so it could never
         settle and the load ended "failed" after two timeouts. A remount that fails after the
         early answer is a load that silently does not happen (the game carries on).
ready    The drive is made ready before anything of the running game is changed (cd_ready,
         called after the remount): CD interrupts off, flags acked, pending answers drained
         (as load_start did), then Getstat; a drive without motor or with the shell open gets
         GetID (it starts the motor; with no disc it answers with an error) and Getstat again,
         READY_ROUNDS (1,024) times at most: a drive that a mount has just put into cd_top's
         new-disc sequence needs up to 38M clocks. Still not ready: CD IRQENA and index are
         put back and the load is refused (FINISH 2) with nothing else changed. load_start keeps
         the bitmap copy only.
game-id  A save stores the kernel's boot name (0x8000B8B0, 5 words) in page 0 words 193-197;
         a load compares it with the running game's after the page-count check and before
         anything is changed. Accepted: either first word is not "cdro", or either name has no
         '_' at byte 11 (not a serial: old state, wake before SYSTEM.CNF, a rewritten buffer);
         all words equal; the state's disc is not the menu's disc and words 1-2 ("m:\\S",
         "CUS_") are equal. Else refused (`bad`).

Usage: prepare_save_early_load.py --routine OUT    (sizes with each part, ROM image, faults assemble)
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'save-early-load'
PARTS = ('answer', 'ready', 'game-id')
NAME_ADDRESS = 0x8000B8B0
NAME_WORD = 193
# Rounds of Getstat + GetID before a load is refused for a drive that is not ready. After a mount or
# remount cd_top runs its new-disc sequence (cd_top.vhd: DRIVE_OPENING 13M clocks, 25M in 2x mode, shell
# flag forced, then 13.2M of spin-up); a round is at least 64,000 clocks there (two commands at 15,000
# and GetID's second response at 33,867), so 1,024 rounds are at least 65M clocks against 38.2M.
READY_ROUNDS = 1024


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


# ---------------------------------------------------------------- answer
MOUNT_OLD = '''\
        jal     disc_mount
        move    $a0, $s6
        beqz    $v0, 72f
        nop
        jal     disc_mount              # not mounted (wrong disc or no answer in time): the menu's
        move    $a0, $s5                # disc again, so DISC names the disc in the drive, and no load
        b       bad
        nop
72:
'''
ANSWER = '''\
        # early-load answer: the Pocket serves no data slot while the load is unanswered, and a
        # remount needs them (cue reader, track table). Not the mounted and settled disc: OK now
        # (k1 bit 0: answered; `answer` then writes no second FINISH).
        lbu     $t0, DISC($k0)
        lbu     $t1, DISC_SET($k0)
        xor     $t0, $t0, $s6
        andi    $t1, $t1, 1
        xori    $t1, $t1, 1
        or      $t0, $t0, $t1
        beqz    $t0, 66f
        li      $t0, 1
        sb      $t0, FINISH($k0)
        ori     $k1, $k1, 1
66:
'''
END_OLD = '''\
        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back
        nop
        li      $t0, 1
        sb      $t0, FINISH($k0)
        STUB    restore
        nop
        .endif
'''
END_NEW = '''\
        jal     cd_resume               # Setloc + ReadN / Play, IRQENA back
        nop
        b       answer
        li      $t0, 1
        .endif
'''
GIVE_UP_OLD = '''\
give_up:
        li      $t0, 2
        .ifdef  DIAG
        j       diag_give_up
        .endif
        sb      $t0, FINISH($k0)
        STUB    restore
        nop
'''
GIVE_UP_NEW = '''\
give_up:
        li      $t0, 2
        .ifdef  DIAG
        j       diag_give_up
        sb      $t0, FINISH($k0)
        .endif
answer:                                 # early-load answer: t0 = FINISH value, unless answered already
        andi    $t1, $k1, 1
        bnez    $t1, 67f
        nop
        sb      $t0, FINISH($k0)
67:     STUB    restore
        nop
'''

# ---------------------------------------------------------------- ready
READY_CALL = '''\
        jal     cd_ready                # early-load ready: before anything of the game is changed
        nop
        bnez    $v0, bad
        nop
'''
START_OLD = '''\
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
'''
START_NEW = '''\
load_start:
        lui     $t1, 0x1F80
        addiu   $t2, $k0, PAGE+0x100
        li      $t3, 0x100
1:      lw      $t0, 0($t2)
        addiu   $t2, $t2, 4
        sw      $t0, BITMAP($t1)
        addiu   $t3, $t3, -4
        bnez    $t3, 1b
        addiu   $t1, $t1, 4
        jr      $ra
        nop

# early-load ready (page 0 in the staging page; nothing of the game changed yet):
# -> v0 = 0, the drive paused and set up; or v0 = 1: no disc or the shell open, CD IRQENA
# and index as they were (answers the game had not taken are gone)
cd_ready:
        move    $s5, $ra
        lui     $t9, 0x1F80
        lbu     $s1, 0x1800($t9)        # index
        sb      $zero, 0x1800($t9)
        lbu     $s0, 0x1803($t9)        # IRQENA
        li      $t0, 1
        sb      $t0, 0x1800($t9)
        sb      $zero, 0x1802($t9)      # IRQENA 0 until cd_resume
'''
MOTOR_OLD = '''\
9:      li      $s6, 2000               # Getstat until the motor runs, the shell closed (a few s)
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
'''
MOTOR_NEW = '''\
9:      li      $s6, READY_ROUNDS       # Getstat: the motor runs, the shell closed; else GetID
2:      jal     cd_cmd                  # (it starts the motor; no disc: an error) and again
        li      $a0, 0x01
        andi    $t0, $v0, 0x12
        li      $t1, 0x02
        beq     $t0, $t1, 4f
        addiu   $s6, $s6, -1
        jal     cd_cmd
        li      $a0, 0x1A
        bltz    $v0, 3f
        lui     $a2, 0x0010
        jal     cd_wait                 # GetID's second response
        nop
3:      bnez    $s6, 2b
        nop
        li      $t0, 1                  # no disc, or the shell open: no load
        sb      $t0, 0x1800($t9)
        sb      $s0, 0x1802($t9)        # IRQENA
        sb      $s1, 0x1800($t9)        # index
        jr      $s5
        li      $v0, 1
4:      andi    $t0, $v0, 0xE0          # reading, seeking or playing: pause it
'''
TAIL_OLD = '''\
        jal     cd_cmd
        li      $a0, 0x0C               # Demute
        jr      $s5
        nop
'''
TAIL_NEW = '''\
        jal     cd_cmd
        li      $a0, 0x0C               # Demute
        jr      $s5
        move    $v0, $zero
'''

# ---------------------------------------------------------------- game-id
SAVE_OLD = '''\
        addu    $t0, $t0, $t2
        sw      $t0, PAGE($k0)
        # R1: registers a cold boot sets differently, into page 0 words 48-61. Plain reads:
'''
SAVE_ID = f'''\
        # early-load game-id: the kernel's boot name ("cdrom:\\\\SCUS_941.63;1") into words {NAME_WORD}-{NAME_WORD + 4}
        lui     $t7, 0x{(NAME_ADDRESS + 0x8000) >> 16:04X}
        addiu   $t6, $k0, PAGE+4*{NAME_WORD}
        addiu   $t5, $t6, 20
68:     lw      $t0, {((NAME_ADDRESS & 0xFFFF) ^ 0x8000) - 0x8000}($t7)
        addiu   $t7, $t7, 4
        sw      $t0, 0($t6)
        addiu   $t6, $t6, 4
        bne     $t6, $t5, 68b
        nop
'''
CHECK_ANCHOR = '''\
        bne     $t0, $t3, bad           # not a whole state of this layout
        nop
'''
LOAD_ID = f'''\
        # early-load game-id: the state's boot name against the running game's, nothing changed
        # yet. No check unless both start "cdro" and have '_' at byte 11 (a serial's).
        lui     $t7, 0x{(NAME_ADDRESS + 0x8000) >> 16:04X}
        addiu   $t7, $t7, {((NAME_ADDRESS & 0xFFFF) ^ 0x8000) - 0x8000}
        addiu   $t6, $k0, PAGE+4*{NAME_WORD}
        lui     $t4, 0x6F72             # "cdro"
        ori     $t4, $t4, 0x6463
        lw      $t0, 0($t7)
        lw      $t1, 0($t6)
        bne     $t0, $t4, 75f
        lbu     $t2, 11($t7)
        bne     $t1, $t4, 75f
        lbu     $t3, 11($t6)
        li      $t4, 0x5F
        bne     $t2, $t4, 75f
        addiu   $t5, $t7, 16
        bne     $t3, $t4, 75f
        addiu   $t4, $t7, 12
69:     lw      $t0, 4($t7)
        lw      $t1, 4($t6)
        addiu   $t7, $t7, 4
        bne     $t0, $t1, 74f
        addiu   $t6, $t6, 4
        bne     $t7, $t5, 69b
        nop
        b       75f
        nop
74:     sltu    $t0, $t7, $t4           # words 1-2 ("m:\\\\S", "CUS_") differ: another game
        bnez    $t0, bad
        nop
        beq     $s5, $s6, bad           # the same disc number: the serial must be the same
        nop
75:
'''

FAULTS = {
    # answer: the early answer is never given (today's behaviour: the remount waits unanswered)
    'no-early-answer': ('        beqz    $t0, 66f\n        li      $t0, 1\n', '        b       66f                     # FAULT no-early-answer\n        li      $t0, 1\n'),
    # answer: the load answers a second time at its end
    'answer-twice': ('        bnez    $t1, 67f\n        nop\n', '        nop                             # FAULT answer-twice\n        nop\n'),
    # ready: no GetID for a drive that is not ready (Getstat only, eight times)
    'no-getid': ('        jal     cd_cmd\n        li      $a0, 0x1A\n        bltz    $v0, 3f\n',
                 '        b       3f                      # FAULT no-getid\n        li      $a0, 0x1A\n        bltz    $v0, 3f\n'),
    # ready: a drive that is not ready is loaded into all the same
    'ready-ignored': ('        bnez    $v0, bad\n        nop\n        # R1: memory control first', '        nop                             # FAULT ready-ignored\n        nop\n        # R1: memory control first'),
    # game-id: the refusal for the same disc number is gone
    'id-not-refused': ('        beq     $s5, $s6, bad           # the same disc number: the serial must be the same\n',
                       '        nop                             # FAULT id-not-refused\n'),
    # game-id: the save stores the name from one word further on
    'id-wrong-word': (f'68:     lw      $t0, {((NAME_ADDRESS & 0xFFFF) ^ 0x8000) - 0x8000}($t7)\n',
                      f'68:     lw      $t0, {((NAME_ADDRESS & 0xFFFF) ^ 0x8000) - 0x8000 + 4}($t7)      # FAULT id-wrong-word\n'),
}
FAULT_PART = {'no-early-answer': 'answer', 'answer-twice': 'answer', 'no-getid': 'ready', 'ready-ignored': 'ready',
              'id-not-refused': 'game-id', 'id-wrong-word': 'game-id'}


EQU_ANCHOR = '        .equ    CD_STATE, 0x1818\n'


def firmware_text(text, parts=PARTS, ready_rounds=None):
    """The big-build routine with the MDEC tap, with the step's parts. Exact anchors; a miss raises.
    ready_rounds: BENCH-ONLY override of READY_ROUNDS (also the environment variable
    EARLY_LOAD_BENCH_ROUNDS, for the CPU bench, whose CD model has no opening sequence and where
    1,024 rounds without a disc are hours); build() never takes either."""
    if ready_rounds is None:
        ready_rounds = int(os.environ.get('EARLY_LOAD_BENCH_ROUNDS', READY_ROUNDS))
    if 'early-load' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    for part in parts:
        if part not in PARTS:
            raise ValueError(f'{TAG}: unknown part {part}')
    for label in ('66', '67', '68', '69', '74', '75'):
        if re.search(rf'^{label}:|[ ,]{label}[fb]\b', text, flags=re.M):
            raise ValueError(f'{TAG}: numeric label {label} is in use')
    if 'disc_mount:' not in text or 'load-check' not in text:
        raise ValueError(f'{TAG}: the routine needs R1 and the load-check')
    mount = MOUNT_OLD
    if 'answer' in parts:
        mount = ANSWER + mount
        text = once(text, END_OLD, END_NEW)
        text = once(text, GIVE_UP_OLD, GIVE_UP_NEW)
    if 'ready' in parts:
        mount = mount.replace('72:\n', '72:\n' + READY_CALL)
        if ready_rounds != READY_ROUNDS:
            note = '   # BENCH-ONLY OVERRIDE (the routine has %d)' % READY_ROUNDS
        else:
            note = ''
        text = once(text, EQU_ANCHOR, EQU_ANCHOR + f'        .equ    READY_ROUNDS, {ready_rounds}{note}\n')
        text = once(text, START_OLD, START_NEW)
        text = once(text, MOTOR_OLD, MOTOR_NEW)
        text = once(text, TAIL_OLD, TAIL_NEW)
        text = once(text, '        jal     load_start              # page bitmap into the scratchpad, drive paused and set up\n',
                    '        jal     load_start              # page bitmap into the scratchpad\n')
    text = once(text, MOUNT_OLD, mount)
    if 'game-id' in parts:
        text = once(text, SAVE_OLD, SAVE_OLD.replace('        # R1: registers', SAVE_ID + '        # R1: registers'))
        text = once(text, CHECK_ANCHOR, CHECK_ANCHOR + LOAD_ID)
    return text


def fault_text(text, name):
    old, new = FAULTS[name]
    return once(text, old, new)


def base_text():
    from prepare_save_mdec_tap import big_build_text, firmware_text as tap
    return tap(big_build_text())


def build(output_dir):
    """The routine without the step, with each part alone and with all: sizes; the ROM image of all."""
    from build_staged_save_fw import image_parts, rom1, rom2, rom40_hex, free_bytes
    from prepare_save_r1 import stub_bytes, packed_bytes
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = {}
    variants = [('mdec-tap', ())] + [(p, (p,)) for p in PARTS] + [('early-load', PARTS)]
    for name, parts in variants:
        source = output_dir / f'staged-{name}.S'
        source.write_text(firmware_text(base_text(), parts, READY_ROUNDS) if parts else base_text())
        image, stage = image_parts(source=source)           # stops if the packed stage 2 does not fit
        stub = stub_bytes(source)
        rows[name] = dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), stub_bytes=len(stub),
                          stage2_bytes=len(stage), packed_bytes=packed_bytes(image, len(stub)), free_bytes=free_bytes(image))
    for name in FAULTS:                                     # every planted fault applies and assembles
        faulty = output_dir / f'staged-early-load-fault-{name}.S'
        faulty.write_text(fault_text(firmware_text(base_text(), PARTS, READY_ROUNDS), name))
        image_parts(source=faulty)
    rom = output_dir / 'psx_save_rom40.hex'
    image, _ = image_parts(source=output_dir / 'staged-early-load.S')
    rom.write_text(rom40_hex(rom1(image), rom2(image)))
    a, b = rows['mdec-tap'], rows['early-load']
    if a['stub_bytes'] != b['stub_bytes']:
        raise ValueError(f'{TAG}: the stub changed')
    report = dict(rows, rom=str(rom), rom_sha256=hashlib.sha256(rom.read_bytes()).hexdigest(),
                  packed_bytes_added={p: rows[p]['packed_bytes'] - a['packed_bytes'] for p in PARTS + ('early-load',)},
                  free_bytes_before=a['free_bytes'], free_bytes=b['free_bytes'])
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for name, row in rows.items():
        print(f"{name:12} stage 2 {row['stage2_bytes']} bytes, packed {row['packed_bytes']}, free {row['free_bytes']}")
    print(f"packed bytes added: {report['packed_bytes_added']}")
    print(f"source sha256: {b['source_sha256']}\nROM sha256:    {report['rom_sha256']}")
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='OUT', required=True)
    args = parser.parse_args()
    try:
        build(args.routine)
    except ValueError as error:
        raise SystemExit(str(error))
