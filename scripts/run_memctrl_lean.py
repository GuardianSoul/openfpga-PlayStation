#!/usr/bin/env python3
"""Cycle-lockstep rtl/pocket/memctrl_lean.vhd against PSX_MiSTer memctrl.

Random bus/bus2 reads and writes (aligned and unaligned, with clock-enable
gaps and resets), inputs changing after rising clk1x edges, every output
compared before each rising edge, three seeds. Planted faults (a missing
valid bit, a wrong kept bit) must fail.
"""
from pathlib import Path
import subprocess

from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer/rtl/memctrl.vhd'
LEAN = ROOT / 'rtl/pocket/memctrl_lean.vhd'
FAULTS = {'valid-bit': ("               valid(word_index)    <= '1';\n", ''),
          'kept-bit': ('& r(30) & bus_dataWrite(29)', '& bus_dataWrite(30) & bus_dataWrite(29)')}


def run(directory, lean_text, seeds, cycles):
    ghdl = find_tool('ghdl'); directory.mkdir(parents=True, exist_ok=True)
    (directory / 'memctrl_ref.vhd').write_text(UPSTREAM.read_text().replace('entity memctrl is', 'entity memctrl_ref is').replace('architecture arch of memctrl is', 'architecture arch of memctrl_ref is'))
    (directory / 'memctrl_lean.vhd').write_text(lean_text)
    log = (directory / 'build.log').open('w')
    for f in ('memctrl_ref.vhd', 'memctrl_lean.vhd', str(ROOT / 'sim/reference/tb_memctrl_lean.vhd')):
        subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run([ghdl, '-e', '--std=08', '-frelaxed', 'tb_memctrl_lean'], cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True)
    out = []
    for seed in seeds:
        r = subprocess.run([ghdl, '-r', '--std=08', '-frelaxed', 'tb_memctrl_lean', f'-gSEED={seed}', f'-gCYCLES={cycles}', '--assert-level=failure', '--ieee-asserts=disable'],
                           cwd=directory, capture_output=True, text=True, timeout=1800)
        text = r.stdout + r.stderr
        out.append((r.returncode == 0 and 'PASS memctrl lean' in text, next((l for l in text.splitlines() if 'PASS' in l or 'mismatch' in l), text[-200:])))
    return out


def main():
    directory = ROOT / 'build/sim/memctrl-lean'
    lean = LEAN.read_text(); failures = []
    for ok, line in run(directory / 'lean', lean, (1, 2, 3), 400000):
        print('PASS' if ok else 'FAIL', line.strip()[-160:])
        if not ok: failures.append('lockstep')
    for name, (old, new) in FAULTS.items():
        if lean.count(old) != 1: raise SystemExit('fault anchor changed: ' + name)
        caught = not run(directory / ('fault-' + name), lean.replace(old, new), (1,), 100000)[0][0]
        print(f'fault {name}: {"rejected" if caught else "NOT DETECTED"}')
        if not caught: failures.append(name)
    if failures: raise SystemExit('memctrl lean check failed: ' + ', '.join(failures))
    print('memctrl lean (readback words in falling-edge block RAM) is cycle-identical; planted faults rejected')


if __name__ == '__main__': main()
