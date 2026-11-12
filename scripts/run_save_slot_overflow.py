#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_checksum.py's slot part (audit-059 #8: overflow only for a dropped upload word).

SLOT.sv is psx_memcard_slot.sv before or after the step (after: undone exactly to get the old one).
Runs under Icarus sim/pocket/tb_slot_overflow.sv: old and new slot side by side (every other output equal,
overflow against a reference from the old slot's signals), then each of prepare_save_checksum.SLOT_FAULTS
in the new slot, which the bench must reject.
Usage: run_save_slot_overflow.py SLOT.sv [--output DIR] [--n STROBES]
"""
import argparse
import json
import subprocess
from pathlib import Path

import prepare_save_checksum as step
from tool_paths import ROOT, find_tool

TB = ROOT / 'sim/pocket/tb_slot_overflow.sv'


def sim(out, name, new_text, old_text, n):
    (out / f'{name}.sv').write_text(new_text)
    vvp = out / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_slot_overflow', '-o', str(vvp), str(TB),
                    str(out / 'old-renamed.sv'), str(out / f'{name}.sv')], check=True)
    r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name, f'+N={n}'], cwd=out,
                       capture_output=True, text=True, timeout=7200)
    (out / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('slot', type=Path)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-slot-overflow')
    ap.add_argument('--n', type=int, default=60000)
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    text = args.slot.read_text()
    if step.SLOT_NEW in text:
        new, old = text, step.slot_restore(text)
    else:
        old, new = text, step.slot_text(text)
    assert step.slot_restore(new) == old and step.slot_text(old) == new
    print(f'slot {args.slot}: old sha256 {step.sha(old)[:16]}, new {step.sha(new)[:16]}; undoing the step gives the old text', flush=True)
    renamed = old.replace('module psx_memcard_slot #(', 'module psx_memcard_slot_old #(')
    assert renamed.count('psx_memcard_slot_old') == 1
    (out / 'old-renamed.sv').write_text(renamed)
    failures, report = [], dict(old_sha256=step.sha(old), new_sha256=step.sha(new))
    log = sim(out, 'new', new, old, args.n)
    line = next((l for l in log.splitlines() if l.startswith('SLOTOVF')), '')
    ok = 'PASS slot overflow' in log
    print(f"bench: {'PASS' if ok else 'FAIL'} | {line}", flush=True)
    if not ok:
        failures.append('bench'); print(log[-1500:])
    report['bench'] = line
    for name, pairs in step.SLOT_FAULTS.items():
        faulty = new
        for a, b in pairs:
            assert faulty.count(a) == 1, (name, a)
            faulty = faulty.replace(a, b)
        log = sim(out, f'fault-{name}', faulty, old, args.n)
        caught = 'PASS slot overflow' not in log
        first = next((l for l in log.splitlines() if l.startswith(('ERROR', 'FAIL'))), '')
        print(f"fault {name}: {'caught' if caught else 'NOT CAUGHT'} | {first}", flush=True)
        report[f'fault_{name}'] = 'caught' if caught else 'not caught'
        if not caught:
            failures.append(name)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(('PASS' if not failures else 'FAIL') + f' slot overflow: bench, {len(step.SLOT_FAULTS)} planted faults'
          + (f' ({", ".join(failures)})' if failures else ' rejected'))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
