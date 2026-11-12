#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "ready-remount" (audit-059 #4; docs/save-states-finish/drive-state.md section 10):
a load whose early-load readiness check (cd_ready) fails after the state's disc was mounted puts the
menu's disc back before it gives up, as every other refusal after the mount does (path 70). Before,
`bnez $v0, bad` returned to the running game with the state's disc in the drive (and, after the early
answer, no error on the Pocket). On the same disc the remount does nothing (disc_mount returns at once).
Same size (one branch target).
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
FAULTS = {'not-remounted': [(NEW, OLD)]}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# ready-remount:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    return once(text, OLD, NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
