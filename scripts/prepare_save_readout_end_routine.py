#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save routine step "readout-end" (docs/save-states-finish/drive-state.md section 9). NEEDS the RTL step
prepare_save_readout_end.py (0x180C bits 2 and 3) and the routine step readout-hold before it.

1. The hold after a save's FINISH ends when the Pocket has read the Memory's last page (0x180C bit 2,
   rd_last), at the end of the window it is seen in (the window is the poll period, 1.5 s). Once a read
   was seen only the limit in any case (60 s) applies; with no read ever the no-read limit (45 s) does.
   The quiet-window rule is gone: the Pocket pauses for seconds within a read-out (test41).
2. A save's FINISH waits until the cue reader has no APF command in flight (0x180C bit 3), at most
   0x100000 looks: the drive is frozen from the save's quiet point, so the cue reader sends no new
   command (firmware: no command while the save holds the drive); a command already sent ends first, so
   that none is in flight while the Pocket reads the Memory out.

Usage: prepare_save_readout_end_routine.py --routine SRC.S --output DIR (SRC.S: a chain with readout-hold)
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

TAG = 'readout-end'

LOOP_OLD = '''\
        lbu     $t0, SPU_HOLD($k0)
        addiu   $t1, $t1, -1
        andi    $t0, $t0, 2
        beqz    $t0, rh_quiet
        sltu    $t4, $t2, $t1           # (no read: go on while t2 < t1)
        addiu   $t2, $t1, -1            # a read
        sltu    $t4, $t2, $t1           # (go on unless the limit in any case is reached)
rh_quiet:
        bnez    $t4, rh_window
        li      $t0, 1
'''
LOOP_NEW = '''\
        lbu     $t0, SPU_HOLD($k0)
        addiu   $t1, $t1, -1
        andi    $t5, $t0, 4             # readout-end: the Memory's last page was read: the read-out is over
        bnez    $t5, rh_out
        andi    $t0, $t0, 2
        beqz    $t0, rh_quiet
        sltu    $t4, $t2, $t1           # (no read: go on while t2 < t1)
        move    $t2, $zero              # readout-end: a read: from now on only the limit in any case
        sltu    $t4, $t2, $t1           # (go on unless the limit in any case is reached)
rh_quiet:
        bnez    $t4, rh_window
rh_out:
        li      $t0, 1
'''
FIN_OLD = '''\
        li      $t0, 1
        sb      $t0, FINISH($k0)
        # readout-hold: the Pocket now reads the Memory out of the buffer and serves no disc data.
'''
FIN_NEW = '''\
        lui     $t1, 0x0010             # readout-end: the cue reader's APF command in flight ends first
re_cue: lbu     $t0, SPU_HOLD($k0)      # (0x180C bit 3; the frozen drive lets it send no new one)
        addiu   $t1, $t1, -1
        andi    $t0, $t0, 8
        beqz    $t0, re_fin
        nop
        bnez    $t1, re_cue
        nop
re_fin: li      $t0, 1
        sb      $t0, FINISH($k0)
        # readout-hold: the Pocket now reads the Memory out of the buffer and serves no disc data.
'''

FAULTS = {
    # the old rule: a quiet window after reads ends the hold (test41: ends in a pause of the read-out)
    'quiet-exit': [('        move    $t2, $zero              # readout-end: a read: from now on only the limit in any case\n',
                    '        addiu   $t2, $t1, -1            # FAULT quiet-exit\n')],
    # the last-page bit ignored: the hold runs to the limit in any case
    'no-last': [('        bnez    $t5, rh_out\n', '        nop                             # FAULT no-last\n')],
    # FINISH while a cue command is in flight
    'no-cue-wait': [('        beqz    $t0, re_fin\n', '        b       re_fin                  # FAULT no-cue-wait\n')],
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


def firmware_text(text):
    if '# readout-end:' in text:
        raise ValueError(f'{TAG}: the routine already has the step')
    if 'rh_window' not in text:
        raise ValueError(f'{TAG}: applies after readout-hold')
    return once(once(text, LOOP_OLD, LOOP_NEW), FIN_OLD, FIN_NEW)


def fault_text(text, name):
    for old, new in FAULTS[name]:
        text = once(text, old, new)
    return text


if __name__ == '__main__':
    import prepare_save_drive_state_routine as ds
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--routine', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    src = args.routine.read_text(); new = firmware_text(src)
    (out / 'staged-before.S').write_text(src); (out / f'staged-{TAG}.S').write_text(new)
    for n in FAULTS: (out / f'staged-fault-{n}.S').write_text(fault_text(new, n)); ds.stage2_bytes(out / f'staged-fault-{n}.S')
    a, b = ds.stage2_bytes(out / 'staged-before.S'), ds.stage2_bytes(out / f'staged-{TAG}.S')
    (out / 'report.json').write_text(json.dumps(dict(stage2_bytes_added=b - a, source_sha256=hashlib.sha256(new.encode()).hexdigest()), indent=2) + '\n')
    print(f'{TAG}: stage2_bytes_added = {b - a}')
