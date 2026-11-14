#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check prepare_save_wake_hold.py on a pinned psx_save_exp1.sv with save-trap-timeout:
sim/pocket/tb_save_wake_hold.sv with the device before save-trap-timeout (lockstep reference, rebuilt with
prepare_save_trap_timeout.restore from the commit that has it), the device with wake-hold, and each
planted fault and each of save-trap-timeout's faults (must fail). Also runs the device WITHOUT wake-hold, which must fail case W (the 0.5.9 bug).
Usage: run_save_wake_hold.py PINNED_psx_save_exp1.sv --trap-step PATH/prepare_save_trap_timeout.py [--output DIR]
"""
import argparse
import importlib.util
import json
import random
import re
import subprocess
from pathlib import Path

import prepare_save_wake_hold as step
from build_staged_save_fw import rom40_hex
from prepare_save_rom_ext import ext_hex
from tool_paths import ROOT, find_tool

TB = ROOT / 'sim/pocket/tb_save_wake_hold.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'


def sim(out, name, sources):
    vvp = out / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_wake_hold', '-o', str(vvp), str(TB), str(MODELS), *map(str, sources)], check=True)
    try:
        r = subprocess.run(['nice', '-n', '10', find_tool('vvp'), '-n', vvp.name], cwd=out, capture_output=True, text=True, timeout=1800)
        log = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:   # a fault that leaves the trap up forever hangs an unbounded wait: caught
        log = (e.stdout or b'').decode(errors='replace') + '\nERROR the bench did not finish in 1,800 s\n'
    (out / f'{name}.log').write_text(log)
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pinned', type=Path)
    ap.add_argument('--trap-step', type=Path, required=True)
    ap.add_argument('--output', type=Path, default=ROOT / 'build/sim/save-wake-hold')
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('prepare_save_trap_timeout', args.trap_step)
    tt = importlib.util.module_from_spec(spec); spec.loader.exec_module(tt)
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    base = args.pinned.read_text()
    if step.TAG in base:
        raise SystemExit('pass the device without wake-hold')
    old = tt.restore(base, tt.EX)
    assert tt.ex_text(old) == base
    renamed = old.replace('module psx_save_exp1 #(', 'module psx_save_exp1_old #(', 1)
    cut = renamed.index("// save-rom-ext: the routine ROM's extension, one M10K") if 'module psx_save_rom_ext' in renamed else None
    (out / 'old-renamed.sv').write_text(renamed[:cut] + '`default_nettype wire\n' if cut else renamed)
    new = step.device_text(base)
    rng = random.Random(0x7A9)
    rom1 = bytes(rng.randrange(256) for _ in range(4096)); rom2 = bytes(rng.randrange(256) for _ in range(1024))
    (out / 'tb_save_rom_ext.hex').write_text(ext_hex(bytes(rng.randrange(256) for _ in range(1024))))
    (out / 'tb_save_exp1_rom40.hex').write_text(rom40_hex(rom1, rom2))
    report, ok = {}, True
    runs = [('step', new, True), ('without-step', base, False)] + [(f'fault-{n}', step.fault_text(new, n), False) for n in step.FAULTS]
    # save-trap-timeout's own faults, with wake-hold on top (where the fault leaves wake-hold's anchor)
    for n in tt.FAULTS:
        faulty = tt.fault_text(base, n)
        runs.append((f'tt-fault-{n}', step.device_text(faulty) if faulty.count(step.OLD) == 1 else faulty, False))
    for name, text, want_pass in runs:
        (out / f'{name}.sv').write_text(text)
        log = sim(out, name, [out / 'old-renamed.sv', out / f'{name}.sv'])
        passed = 'PASS save wake hold' in log and 'ERROR' not in log
        first = (re.search(r'ERROR[^\n]*', log) or ['no error'])[0]
        good = passed == want_pass
        report[name] = dict(passed=passed, first=first, lines=re.findall(r'TRAP [^\n]*', log)); ok &= good
        print(f'{name}:', ('PASS' if passed else f'FAIL ({first})') + ('' if good else '  <- unexpected'), flush=True)
    (out / 'report.json').write_text(json.dumps(report, indent=1) + '\n')
    print('PASS save wake-hold device' if ok else 'FAIL save wake-hold device')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
