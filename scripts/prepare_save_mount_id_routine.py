#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "mount-id" (after mount-wait-wake): the identity compare waits for the mounted disc's
track table instead of refusing on DISC_ID 0.

test8 (2026-11-14, diag record of a failed SOTN EU wake: path 70 from the identity compare, mounted
DISC_ID 0, no early answer): psx_cd_stream settles a mount without a valid disc at once and writes no
track table (psx_cd_stream.sv: "no disc: settled now"), and DISC_ID is 0 from configuration until a
table is written. A wake that saw such a mount first found the state's disc number "settled", gave no
early answer, passed disc_mount at once and refused on DISC_ID 0 against word 198; the BIOS then found
no disc (the shell), the real mount came later (the logo, a fresh game). 1 wake in 4 on test5-test7.
At 71 (reached without a boot name, or after an in-game disc switch with a state identity): while
DISC_ID reads 0, give the early answer if it was not given (a BIOS-screen Memory load must not wait on
data slots the Pocket serves only after an answer), then re-read DISC_ID for up to 0x08800000 looks
(70 s or more, the wake's mount bound); then compare as before (a disc that never comes: refused).
Faults: 'no-wait' (the compare at once, the test8 behaviour), 'no-answer' (waits without the early answer).
"""
TAG = 'mount-id'
OLD = '''\
71:     beq     $t0, $t1, 72f           # disc-id: the state's disc
        nop
'''
NEW = '''\
71:     bnez    $t0, 59f                # mount-id: DISC_ID 0 = no track table yet (a mount without a
        andi    $t2, $k1, 1             # valid disc settles at once): wait for the table
        bnez    $t2, 58f                # (answered already)
        li      $t2, 1
        sb      $t2, FINISH($k0)        # the early answer: the Pocket serves no data slot to an unanswered load
        ori     $k1, $k1, 1
58:     lui     $t2, 0x0880             # 0x08800000 looks (70 s or more), the wake's mount bound
57:     lw      $t0, DISC_ID($k0)
        addiu   $t2, $t2, -1
        bnez    $t0, 59f
        nop
        bnez    $t2, 57b
        nop
59:     beq     $t0, $t1, 72f           # disc-id: the state's disc
        nop
'''
FAULTS = {
    'no-wait': [(NEW, OLD)],
    'no-answer': [(NEW, NEW.replace('        sb      $t2, FINISH($k0)        # the early answer', '        nop                             # the early answer'))],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# mount-id:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    return once(text, OLD, NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text
