#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""cd_top step "save-drive-state" (RTL: goes into the next fit; docs/save-states-finish/drive-state.md).

A save is taken only at a CD quiet point: the routine's cd_quiet refuses while the tap's busy byte
(0x181F, save_cd_state[63:56]) is not 0. A Setloc the game has sent and no ReadN / ReadS / Play /
SeekL / SeekP has used yet (cd_top's setLocActive) was not part of it, and its target is in no tap:
a state saved then loads into a drive without the Setloc, and the game's next ReadN reads from the
wrong place (drive-state bench case 4: WRONGLBA). This step adds setLocActive to the busy term, so
the save waits for the command that uses the Setloc (usually the next few microseconds; the save's
retries cover 0.5 s, then "State failed" as for any busy drive).

  save_cd_state[56] = working | cmd_busy | cmdPending | copy busy | pending drive IRQ | setLocActive

Nothing else changes: no register, no new port, the other 63 tap bits and every cd_top output are
as before. setLocActive is a cd_top register already (Setloc sets it; the read, play and seek
commands clear it; reset clears it), so the cost is one more input on the busy OR.

Check: scripts/run_save_drive_state.py (tap lockstep against the cd_top before the step, the
drive-level save/load bench, planted faults).
Usage: prepare_save_drive_state.py CD_TOP.vhd --text-only OUT          (one cd_top.vhd)
       prepare_save_drive_state.py SOURCE --output OUTPUT [--seed N]   (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-drive-state'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


BUSY_OLD = ("   save_cd_state <= \"0000000\" & (working or cmd_busy or cmdPending or save_copy_busy or (pendingDriveIRQ(0) or pendingDriveIRQ(1) or\n"
            "                    pendingDriveIRQ(2) or pendingDriveIRQ(3) or pendingDriveIRQ(4))) &\n")
BUSY_NEW = ("   -- save-drive-state: a Setloc no command has used yet holds a save off (its target is in no tap)\n"
            "   save_cd_state <= \"0000000\" & (working or cmd_busy or cmdPending or save_copy_busy or (pendingDriveIRQ(0) or pendingDriveIRQ(1) or\n"
            "                    pendingDriveIRQ(2) or pendingDriveIRQ(3) or pendingDriveIRQ(4)) or setLocActive) &\n")
PAIRS = ((BUSY_OLD, BUSY_NEW),)

# Planted faults: (old, new) on the transformed cd_top text.
FAULTS = {
    # the step undone in effect: the Setloc term never reaches the busy bit
    'no_setloc': [('pendingDriveIRQ(4)) or setLocActive) &', "pendingDriveIRQ(4)) or '0') &")],
    # the term inverted: busy whenever no Setloc is pending (every save refused)
    'inverted': [('pendingDriveIRQ(4)) or setLocActive) &', 'pendingDriveIRQ(4)) or not setLocActive) &')],
    # the term in a spare tap bit instead of the busy bit (the routine reads bit 56 only in the busy byte's low bit)
    'wrong_bit': [('   save_cd_state <= "0000000" & (working', '   save_cd_state <= "000000" & setLocActive & (working'),
                  ('pendingDriveIRQ(4)) or setLocActive) &', 'pendingDriveIRQ(4))) &')],
    # a term that holds too long: the read's seek target (readAfterSeek) in place of the pending Setloc
    'read_after_seek': [('pendingDriveIRQ(4)) or setLocActive) &', 'pendingDriveIRQ(4)) or readAfterSeek) &')],
}


def cd_text(text):
    """cd_top.vhd (a project's copy with the save taps) with the step."""
    if TAG in text:
        raise ValueError(f'{TAG}: already applied')
    for old, new in PAIRS:
        text = _once(text, old, new)
    return text


def restore(text):
    """The cd_top text before the step (the inverse of cd_text, exact)."""
    for old, new in PAIRS:
        text = _once(text, new, old)
    return text


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None):
    """Snapshot SOURCE into OUTPUT, patch cd_top.vhd there."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'cd_top.vhd']
    if len(rows) != 1:
        raise SystemExit(f'{TAG}: {source} does not compile one cd_top.vhd')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    cd = next(Path(r['path']) for r in before if Path(r['path']).name == 'cd_top.vhd')
    if digest(cd) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: snapshot changed cd_top.vhd')
    changed = {str(cd): digest(cd)}
    cd.write_text(cd_text(cd.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected cd_top.vhd to change, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(Path(rows[0]['path'])) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: the source project changed')
    return seed, {p: (old, digest(Path(p))) for p, old in changed.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--text-only', type=Path, metavar='OUT')
    args = parser.parse_args()
    try:
        if args.text_only:
            if args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(cd_text(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output:
                parser.error('SOURCE --output OUTPUT required')
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
            print(f'{TAG}: setLocActive added to the CD tap busy bit; no register, no port')
    except ValueError as error:
        raise SystemExit(str(error))
