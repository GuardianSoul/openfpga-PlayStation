#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "switch-check" (docs/save-states-finish/drive-state.md, test38 item 3): a load that
switched discs is checked against the state's disc identity also while a game runs.

Routine g decides a load before any disc change by the boot names: another publisher prefix, or another
serial on the same disc number, is refused at once (FINISH 2, nothing changed). The same prefix with
another disc number is taken as "the same game, the other disc": the routine answers the Pocket early,
sets the state's disc (DISC_SET) and, with a boot name running, loads whatever that slot mounts. A Memory
of another game with the same prefix (SCUS_... against SCUS_...) therefore switched to the running game's
other disc and loaded the foreign state onto it, or, with no such slot, switched back and refused after
the early answer (no notice: the Pocket already had its "OK").

This step: after the mount, when the disc number changed (state's disc != the menu's disc before the
load) and the state carries an identity (page 0 word 198, prepare_save_disc_id.py), the mounted disc's
identity (DISC_ID) must be the state's, as it must already when no boot name runs; else the menu's disc
is mounted again and nothing is loaded. A load on the same disc is decided by the name as before; a
state from before disc-id (word 198 = 0) switches discs as before.

Needs the disc-id RTL step (DISC_ID at 0x1828; in E4b and later).
Usage: prepare_save_switch_check_routine.py --routine SRC.S --output DIR
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'switch-check'

OLD = '''\
        bnez    $v0, 70f
        andi    $t0, $k1, 8             # disc-id: without a running name the mounted disc must be
        beqz    $t0, 72f                # the state's (word 198; 0 in a state from before the step)
        lw      $t0, DISC_ID($k0)
        lw      $t1, ID_WORD($k0)
        nop
        beq     $t0, $t1, 72f           # disc-id: the state's disc
        nop
'''
NEW = '''\
        bnez    $v0, 70f
        lw      $t1, ID_WORD($k0)       # (the state's identity; 0 in a state from before disc-id)
        lw      $t0, DISC_ID($k0)       # the mounted disc's
        andi    $t2, $k1, 8             # disc-id: without a running name the mounted disc must be the state's
        bnez    $t2, 71f
        xor     $t3, $s5, $s6           # switch-check: with one, too when the disc number changed
        beqz    $t3, 72f                # (the same disc: the name decided)
        nop
        beqz    $t1, 72f                # (a state from before disc-id: as before)
        nop
71:     beq     $t0, $t1, 72f           # disc-id: the state's disc
        nop
'''

FAULTS = {
    # the step undone in effect: with a running name the switched disc is never checked
    'no-check': [('        xor     $t3, $s5, $s6           # switch-check: with one, too when the disc number changed\n'
                  '        beqz    $t3, 72f                # (the same disc: the name decided)\n',
                  '        nop                             # FAULT no-check\n'
                  '        b       72f\n')],
    # the check on the same disc too: a same-game state with another identity is refused
    'same-disc-too': [('        beqz    $t3, 72f                # (the same disc: the name decided)\n',
                       '        nop                             # FAULT same-disc-too\n')],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# switch-check:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    return once(text, OLD, NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


if __name__ == '__main__':
    import prepare_save_drive_state_routine as ds
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--routine', type=Path, metavar='SRC.S', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    src = args.routine.read_text()
    new = firmware_text(src)
    (out / 'staged-before.S').write_text(src)
    (out / f'staged-{TAG}.S').write_text(new)
    for name in FAULTS:
        (out / f'staged-fault-{name}.S').write_text(fault_text(new, name))
    a, b = ds.stage2_bytes(out / 'staged-before.S'), ds.stage2_bytes(out / f'staged-{TAG}.S')
    for name in FAULTS:
        ds.stage2_bytes(out / f'staged-fault-{name}.S')
    report = dict(chain=str(args.routine), stage2_bytes_added=b - a,
                  source_sha256=hashlib.sha256((out / f'staged-{TAG}.S').read_bytes()).hexdigest())
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{TAG}: stage2_bytes_added = {b - a}')
