#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_trap_timeout.py on a project's save device: sim/pocket/tb_save_trap_timeout.sv (the
device before the step and after it in lockstep; directed time-out cases on a device with a 2^12 time-out).
Planted faults (prepare_save_trap_timeout.FAULTS) must fail.
Usage: run_save_trap_timeout.py PROJECT [--output DIR]   (PROJECT: a frozen project with save-readout-end)
"""
import argparse
import json
import random
import re
import subprocess
from pathlib import Path

import prepare_save_trap_timeout as step
from prepare_save_rom_ext import ext_hex
from tool_paths import ROOT, find_tool

TB = ROOT / 'sim/pocket/tb_save_trap_timeout.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'


def sim(out, name, sources):
    vvp = out / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_trap_timeout', '-o', str(vvp), str(TB), str(MODELS), *map(str, sources)], check=True)
    r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name], cwd=out, capture_output=True, text=True, timeout=7200)
    (out / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-trap-timeout')
    ap.add_argument('--faults', nargs='*', help='only these faults (default: all)')
    args = ap.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    rows = [Path(r['path']) for r in json.loads((args.project / 'fit-inputs.json').read_text())['quartus_sources']
            if Path(r['path']).name == 'psx_save_exp1.sv']
    text = rows[0].read_text()
    old, new = (step.restore(text, step.EX), text) if step.TAG in text else (text, step.ex_text(text))
    assert step.ex_text(old) == new
    renamed = old.replace('module psx_save_exp1 #(', 'module psx_save_exp1_old #(', 1)
    cut = renamed.index("// save-rom-ext: the routine ROM's extension, one M10K") if 'module psx_save_rom_ext' in renamed else None
    (out / 'old-renamed.sv').write_text(renamed[:cut] + '`default_nettype wire\n' if cut else renamed)
    (out / 'new.sv').write_text(new)
    rng = random.Random(0x7A9)
    rom1 = bytes(rng.randrange(256) for _ in range(4096)); rom2 = bytes(rng.randrange(256) for _ in range(1024))
    (out / 'tb_save_rom_ext.hex').write_text(ext_hex(bytes(rng.randrange(256) for _ in range(1024))))
    from build_staged_save_fw import rom40_hex
    (out / 'tb_save_exp1_rom40.hex').write_text(rom40_hex(rom1, rom2))
    report, ok = {}, True
    log = sim(out, 'step', [out / 'old-renamed.sv', out / 'new.sv'])
    good = 'PASS save trap timeout' in log and 'ERROR' not in log
    report['step'] = dict(lines=re.findall(r'TRAP [^\n]*', log), ok=good); ok &= good
    print('step:', 'PASS' if good else 'FAIL', re.findall(r'TRAP [^\n]*', log), flush=True)
    if not good: print(log[-2500:])
    for name in (args.faults if args.faults is not None else step.FAULTS):
        (out / f'fault-{name}.sv').write_text(step.fault_text(new, name))
        log = sim(out, f'fault-{name}', [out / 'old-renamed.sv', out / f'fault-{name}.sv'])
        caught = 'PASS save trap timeout' not in log
        first = (re.search(r'ERROR[^\n]*', log) or ['no error'])[0]
        report[name] = dict(caught=caught, first=first); ok &= caught
        print(f'fault {name}:', f'caught ({first})' if caught else 'NOT CAUGHT', flush=True)
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save-trap-timeout device' if ok else 'FAIL save-trap-timeout device')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
