#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "ready-remount" (audit-059 #4; docs/save-states-finish/drive-state.md section 10):
a load whose early-load readiness check (cd_ready) fails after the state's disc was mounted puts the
menu's disc back before it gives up, as every other refusal after the mount does (path 70). Before,
`bnez $v0, bad` returned to the running game with the state's disc in the drive (and, after the early
answer, no error on the Pocket). On the same disc the remount does nothing (disc_mount returns at once).
cd_ready keeps its return address in s7, not s5: path 70 takes the menu's disc from s5 (set at the
mount), and with s5 = cd_ready's caller the remount asked for a disc numbered by a code address and
polled DISC_POLLS (about 2.5 s) for it (CPU scenario 'other disc not ready' ran past 8 M cycles).
s7 is free there: on a load the next use sets it (`li $s7, RAM_PAGE`). Same size.
"""
TAG = 'ready-remount'
OLD = '''\
        jal     cd_ready                # early-load ready: before anything of the game is changed
        nop
        bnez    $v0, bad
        nop
'''
NEW = '''\
        jal     cd_ready                # early-load ready: before anything of the game is changed
        nop
        bnez    $v0, 70b                # ready-remount: not ready: the menu's disc back, no load
        nop
'''
RET = [('''\
cd_ready:
        move    $s5, $ra
''', '''\
cd_ready:
        move    $s7, $ra                # ready-remount: s5 keeps the menu's disc for path 70
'''), ('''\
        sb      $s1, 0x1800($t9)        # index
        jr      $s5
        li      $v0, 1
''', '''\
        sb      $s1, 0x1800($t9)        # index
        jr      $s7
        li      $v0, 1
'''), ('''\
        li      $a0, 0x0C               # Demute
        jr      $s5
        move    $v0, $zero
''', '''\
        li      $a0, 0x0C               # Demute
        jr      $s7
        move    $v0, $zero
''')]
FAULTS = {'not-remounted': [(NEW, OLD)], 'ready-s5': [(new, old) for old, new in RET]}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# ready-remount:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    text = once(text, OLD, NEW)
    for old, new in RET:
        text = once(text, old, new)
    return text


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
