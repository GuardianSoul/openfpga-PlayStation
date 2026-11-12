#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Bench for "Restart Game during a save or load" (sim/pocket/tb_save_core_reset.sv).

Runs the real psx_save_exp1 through a core reset in the middle of a save and of a load,
then a load request, and a wake's load held in reset; with today's device, with the
save-core-reset step (scripts/prepare_save_core_reset.py) and with the step's planted
faults. Icarus Verilog; each run takes seconds.
Usage: run_save_core_reset.py DEVICE.sv --output DIR
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_paths import ROOT, find_tool
import prepare_save_core_reset as step

TB = ROOT / 'sim/pocket/tb_save_core_reset.sv'


def run(device, out, case, have_port):
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    binary = out / f'{device.stem}-{case}.vvp'
    command = [iverilog, '-g2012', '-o', str(binary), *(['-DHAVE_CORE_RESET'] if have_port else []), str(TB), str(device)]
    built = subprocess.run(command, capture_output=True, text=True)
    if built.returncode:
        raise SystemExit(built.stdout + built.stderr)
    text = subprocess.run([vvp, str(binary), f'+CASE={case}'], capture_output=True, text=True, timeout=600).stdout
    if 'DONE' not in text:
        raise SystemExit('bench did not finish: ' + text[-400:])
    states = {}
    for line in text.splitlines():
        if line.startswith('STATE '):
            label, fields = line[6:].split(':', 1)
            states[label.strip()] = dict(re.findall(r'(\w+)=(\w+)', fields))
    return states


IDLE = dict(save_mode='0', cd_freeze='0', spu_hold='0', trap='0', want_load='0', want_save='0', copy_busy='0')


def problems(case, states):
    """What a correct device shows; returns a list of differences."""
    found = []

    def want(label, **fields):
        for key, value in fields.items():
            if states[label].get(key) != value:
                found.append(f'{label}: {key}={states[label].get(key)} (expected {value})')
    if case == 'held':
        want('load in reset', trap='1', want_load='1', load_ok='1', load_err='0')
        want('after reset', trap='1', want_load='1', load_ok='1', load_err='0', save_mode='0', spu_hold='0')
        return found
    want('before reset', save_mode='1', spu_hold='1', copy_busy='1', cd_freeze='1' if case == 'save' else '0')
    want('in reset', **IDLE, aux_cut='0')
    want('after reset', **IDLE, start_busy='0', load_busy='0', aux_cut='0')
    want('after reset', **({'start_err': '1', 'start_ok': '0'} if case == 'save' else {'load_err': '1', 'load_ok': '0'}))
    if int(states['after reset']['aux_after_edge']) > 1:
        found.append(f"after reset: {states['after reset']['aux_after_edge']} buffer words copied after the reset (at most the one in flight)")
    want('after new load', trap='1', want_load='1', want_save='0', load_busy='1', load_err='0', save_mode='0', cd_freeze='0', spu_hold='0')
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('device', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    today = out / 'today.sv'
    today.write_text(a.device.read_text())
    fixed = out / 'fixed.sv'
    fixed.write_text(step.device_text(a.device.read_text()))
    variants = [('today', today, False), ('fixed', fixed, True)]
    for name in step.FAULTS:
        path = out / f'fault_{name.replace("-", "_")}.sv'
        path.write_text(step.fault_text(fixed.read_text(), name))
        variants.append((f'fault {name}', path, True))
    failed = False
    for name, path, port in variants:
        for case in ('save', 'load', 'held'):
            states = run(path, out, case, port)
            found = problems(case, states)
            for label, fields in states.items():
                print(f'  {name} / reset during a {case} / {label}: ' + ' '.join(f'{k}={v}' for k, v in fields.items())
                      if case != 'held' else f'  {name} / load held in reset / {label}: ' + ' '.join(f'{k}={v}' for k, v in fields.items()))
            verdict = 'clean' if not found else 'NOT clean: ' + '; '.join(found)
            print(f'{name} / {case}: {verdict}', flush=True)
            if name == 'fixed' and found:
                failed = True
        if name.startswith('fault'):
            pass
    return failed


if __name__ == '__main__':
    sys.exit(main())
