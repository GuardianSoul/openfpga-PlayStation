#!/usr/bin/env python3
"""Lockstep-compare gte-shadow-m10k against the gte-control-ram GTERegs.

Both sides get gte-control-ram's shadows; the candidate also gets the
falling-edge block RAM reads (scripts/prepare_gte_shadow_m10k.py). Inputs
change after rising edges, as in the core; o_dataOut and the register
struct must match before every rising edge. A planted rising-edge read
(one clk late) and a planted wrong-word read must both fail.
"""
from pathlib import Path
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM
from prepare_gte_control_ram import control_ram_text, data_ram_text
from prepare_gte_shadow_m10k import shadow_m10k_text
from tool_paths import ROOT, find_tool

FAULTS = (('rising-edge-read', lambda t: t.replace('always @(negedge i_clk) dataShadow_q', 'always @(posedge i_clk) dataShadow_q')),
          ('wrong-word-read', lambda t: t.replace("ctrlShadow_q <= ctrlShadow[i_readRegID[4:0]];", "ctrlShadow_q <= ctrlShadow[i_readRegID[4:0] ^ 5'd1];")))


def run(directory, original, rtl, fault=None):
    directory.mkdir(parents=True, exist_ok=True)
    base = data_ram_text(control_ram_text(original.read_text()))
    reference = directory / 'GTERegsControlRam.sv'
    reference.write_text(base.replace('module PocketGTERegs (', 'module PocketGTERegsControlRam ('))
    text = shadow_m10k_text(base)
    if fault:
        changed = fault(text)
        if changed == text: raise SystemExit('fault anchor not found')
        text = changed
    candidate = directory / 'GTERegsShadowM10K.sv'
    candidate.write_text(text.replace('module PocketGTERegs (', 'module PocketGTERegsShadowM10K ('))
    with (directory / 'build.log').open('w') as log:
        subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O2', '-j', '8', '-Wno-fatal', f'-I{rtl}', '--Mdir', str(directory / 'obj'),
                        '--top-module', 'tb_gte_shadow_m10k', str(reference), str(candidate), str(rtl / 'LeadCountS32.v'),
                        str(rtl / 'M16TO5.sv'), str(ROOT / 'sim/reference/tb_gte_shadow_m10k.sv')],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    result = subprocess.run([str(directory / 'obj/Vtb_gte_shadow_m10k')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    return result.returncode == 0 and 'PASS GTE shadow M10K' in output, output


def main():
    if not find_tool('verilator'): raise SystemExit('Verilator is required')
    directory = ROOT / 'build/sim/gte-shadow-m10k'; directory.mkdir(parents=True, exist_ok=True)
    sources = {Path(s['path']).name: Path(s['path']) for s in adapter_sources(directory / 'adapter')}
    rtl = UPSTREAM / 'rtl'
    ok, output = run(directory / 'candidate', sources['GTERegs.sv'], rtl)
    passes = [l for l in output.splitlines() if 'PASS GTE shadow M10K' in l]
    if not ok: raise SystemExit(output[-2000:])
    print(passes[0])
    for name, fault in FAULTS:
        ok, output = run(directory / name, sources['GTERegs.sv'], rtl, fault)
        if ok: raise SystemExit(f'Planted fault {name} not detected')
        print(f'Planted {name} rejected:', next((l for l in output.splitlines() if 'mismatch' in l), output.strip().splitlines()[-1])[:120])
    print('GTE readback shadows in falling-edge block RAM: lockstep equal, faults rejected')


if __name__ == '__main__': main()
