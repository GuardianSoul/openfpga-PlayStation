#!/usr/bin/env python3
"""Lockstep-compare the GTE control-readback LUT-RAM variant with the adapter's GTERegs."""
from pathlib import Path
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM
from prepare_gte_control_ram import control_ram_text, data_ram_text
from tool_paths import ROOT, find_tool


def main():
    verilator = find_tool('verilator')
    if not verilator: raise SystemExit('Verilator is required')
    directory = ROOT / 'build/sim/gte-control-ram'; directory.mkdir(parents=True, exist_ok=True)
    sources = {Path(s['path']).name: Path(s['path']) for s in adapter_sources(directory / 'adapter')}
    original = sources['GTERegs.sv']
    candidate = directory / 'GTERegsControlRam.sv'
    candidate.write_text(data_ram_text(control_ram_text(original.read_text())).replace('module PocketGTERegs (', 'module PocketGTERegsControlRam ('))
    rtl = UPSTREAM / 'rtl'
    with (directory / 'build.log').open('w') as log:
        subprocess.run([verilator, '--binary', '-O2', '-j', '8', '-Wno-fatal', f'-I{rtl}', '--Mdir', str(directory / 'obj'),
                        '--top-module', 'tb_gte_control_ram', str(original), str(candidate), str(rtl / 'LeadCountS32.v'),
                        str(rtl / 'M16TO5.sv'), str(ROOT / 'sim/reference/tb_gte_control_ram.sv')],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    result = subprocess.run([str(directory / 'obj/Vtb_gte_control_ram')], cwd=directory, capture_output=True, text=True, timeout=600)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    passes = [line for line in output.splitlines() if 'PASS GTE control RAM' in line]
    for line in passes: print(line)
    if result.returncode or not passes: raise SystemExit(output[-2000:])


if __name__ == '__main__': main()
