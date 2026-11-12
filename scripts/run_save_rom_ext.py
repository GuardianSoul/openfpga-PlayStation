#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_rom_ext.py on a project's save device.

PROJECT is a frozen project before the step (its pinned psx_save_exp1.sv is the "old" device)
or after it (the step is undone exactly to get the old device). Runs, all under Icarus:
  existing   sim/pocket/tb_save_exp1.sv, unchanged, on the old and on the new device (the
             extension is not named: unused). Both must pass with the same summary line.
  ext        sim/pocket/tb_save_rom_ext.sv: old and new side by side on the same inputs
             (lockstep of every output; every extension address read; see the bench's header)
  faults     prepare_save_rom_ext.FAULTS in the new device, each must be rejected by `ext`
Also checked: undoing the step gives the old text byte for byte.
Usage: run_save_rom_ext.py PROJECT [--output DIR] [--only existing,ext,faults]
"""
import argparse
import json
import random
import re
import subprocess
from pathlib import Path

import prepare_save_rom_ext as step
from tool_paths import ROOT, find_tool

TB_OLD = ROOT / 'sim/pocket/tb_save_exp1.sv'
TB = ROOT / 'sim/pocket/tb_save_rom_ext.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'


def sim(out, name, top, bench, sources):
    vvp = out / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', top, '-o', str(vvp), str(bench), str(MODELS), *map(str, sources)], check=True)
    r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name], cwd=out, capture_output=True, text=True, timeout=3600)
    (out / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-rom-ext')
    ap.add_argument('--only', default='existing,ext,faults')
    args = ap.parse_args()
    only = args.only.split(',')
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    project = args.project.resolve()
    rows = [Path(r['path']) for r in json.loads((project / 'fit-inputs.json').read_text())['quartus_sources']
            if Path(r['path']).name == 'psx_save_exp1.sv']
    assert len(rows) == 1
    text = rows[0].read_text()
    if 'psx_save_rom_ext' in text:
        new, old = text, step.restore(text)
        assert step.device_text(old) == new
    else:
        old, new = text, step.device_text(text)
        assert step.restore(new) == old
    print(f'device {rows[0]}: old sha256 {step.sha(old)[:16]}, new {step.sha(new)[:16]}; undoing the step gives the old text', flush=True)
    (out / 'old.sv').write_text(old); (out / 'new.sv').write_text(new)
    renamed = old.replace('module psx_save_exp1 #(', 'module psx_save_exp1_old #(')
    assert renamed != old and renamed.count('psx_save_exp1_old') == 1
    (out / 'old-renamed.sv').write_text(renamed)

    rng = random.Random(0x5A7E)
    rom1 = bytes(rng.randrange(256) for _ in range(4096)); rom2 = bytes(rng.randrange(256) for _ in range(1024))
    ext = bytes(rng.randrange(256) for _ in range(1024))
    (out / 'tb_save_exp1_rom.hex').write_text(''.join(f'{b:02x}\n' for b in rom1))
    (out / 'tb_save_exp1_rom2.hex').write_text(''.join(f'{b:02x}\n' for b in rom2))
    (out / 'tb_save_rom_ext.hex').write_text(step.ext_hex(ext))
    from build_staged_save_fw import rom40_hex
    (out / 'tb_save_exp1_rom40.hex').write_text(rom40_hex(rom1, rom2))
    print(f'extension image: {sum(a != b for a, b in zip(ext, rom2))} of 1024 bytes differ from the second ROM byte at the same address', flush=True)

    failures, report = [], {}
    if 'existing' in only:
        lines = {}
        for label in ('old', 'new'):
            log = sim(out, f'existing-{label}', 'tb_save_exp1', TB_OLD, [out / f'{label}.sv'])
            lines[label] = (re.search(r'PASS save exp1.*', log) or ['FAIL'])[0] + ' | ' + (re.search(r'aux accesses.*', log) or [''])[0]
            print(f'existing bench, {label} device: {lines[label]}', flush=True)
            if 'PASS save exp1' not in log: failures.append(f'existing {label}')
        if lines['old'] != lines['new']: failures.append('existing bench: old and new differ')
        report['existing'] = lines
    if 'ext' in only:
        log = sim(out, 'ext', 'tb_save_rom_ext', TB, [out / 'old-renamed.sv', out / 'new.sv'])
        line = (re.search(r'ROMEXT .*', log) or ['no summary'])[0]
        ok = 'PASS save rom ext' in log and 'ERROR' not in log
        print('ext bench:', 'PASS' if ok else 'FAIL', '|', line, flush=True)
        if not ok:
            print(log[-1500:]); failures.append('ext')
        report['ext'] = line
    if 'faults' in only:
        report['faults'] = {}
        for name in step.FAULTS:
            (out / f'fault-{name}.sv').write_text(step.fault_text(new, name))
            log = sim(out, f'fault-{name}', 'tb_save_rom_ext', TB, [out / 'old-renamed.sv', out / f'fault-{name}.sv'])
            first = (re.search(r'ERROR[^\n]*', log) or ['no error'])[0]
            caught = 'PASS save rom ext' not in log and 'ERROR' in log
            print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', '|', first[:150], flush=True)
            report['faults'][name] = dict(caught=caught, first_error=first)
            if not caught: failures.append('fault ' + name)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if failures:
        raise SystemExit('save rom ext check failed: ' + ', '.join(failures))
    print(f'PASS save rom ext: {", ".join(only)}' + (f' ({len(step.FAULTS)} planted faults rejected)' if 'faults' in only else ''))


if __name__ == '__main__':
    main()
