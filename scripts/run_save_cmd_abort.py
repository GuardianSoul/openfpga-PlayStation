#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""core_bridge_cmd before/after the save-cmd-abort step, side by side (sim/pocket/tb_bridge_cmd_abort.sv,
Icarus), plus its planted faults (prepare_save_cmd_abort.FAULTS 'bc-*' must fail). The psx_cue_mount half
is run_cue_mount.py stream-drop with CUE_MOUNT_SV (faults 'cm-*').
Usage: run_save_cmd_abort.py --project PROJECT [--work DIR]
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

import prepare_save_cmd_abort as step
from tool_paths import ROOT, find_tool


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path, required=True)
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/save-cmd-abort')
    args = ap.parse_args()
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    w = args.work.resolve(); w.mkdir(parents=True, exist_ok=True)
    m = json.loads((args.project / 'fit-inputs.json').read_text())
    path = lambda name: next(Path(r['path']) for r in m['quartus_sources'] if Path(r['path']).name == name)
    old = path('core_bridge_cmd.v').read_text()
    new = step.bc_text(old)
    assert step.restore(new, step.BC) == old
    (w / 'old.v').write_text(old.replace('module core_bridge_cmd (', 'module core_bridge_cmd_old (', 1))
    runs = {'step': new, **{f: step.fault_text(new, f) for f, (kind, _, _) in step.FAULTS.items() if kind == 'bc'}}
    report, ok = {}, True
    for name, text in runs.items():
        (w / f'{name}.v').write_text(text)
        exe = w / f'{name}.vvp'
        subprocess.run([iverilog, '-g2012', '-o', str(exe), str(w / 'old.v'), str(w / f'{name}.v'),
                        str(ROOT / 'sim/pocket/tb_bridge_cmd_abort.sv')], check=True)
        out = subprocess.run([vvp, str(exe)], capture_output=True, text=True, timeout=600).stdout
        res = re.findall(r'BRIDGE (PASS.*|FAIL.*)', out)
        passed = bool(res) and res[-1].startswith('PASS') and not any(r.startswith('FAIL') for r in res)
        good = passed if name == 'step' else not passed
        ok &= good
        report[name] = dict(result=res, expected='PASS' if name == 'step' else 'FAIL', ok=good)
        print(f'{name}: {res} -> {"ok" if good else "WRONG"}', flush=True)
    (w / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save-cmd-abort bridge' if ok else 'FAIL save-cmd-abort bridge')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
