#!/usr/bin/env python3
"""rtl/pocket/psx_snac.sv against its version at 61b5fdb (CARDS 0, 16-bit ackTimer).

sim/pocket/tb_snac_lockstep.sv drives both with random SNAC traffic for 20M
clocks and compares every output on every clock; it must see actions and
received bytes. A planted 10-bit ackTimer must fail.
"""
import re
import subprocess
from tool_paths import ROOT, find_tool

REF = '61b5fdb'


def run(folder, new_text):
    folder.mkdir(parents=True, exist_ok=True)
    ref = subprocess.run(['git', 'show', f'{REF}:rtl/pocket/psx_snac.sv'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    (folder/'ref.sv').write_text(ref.replace('module psx_snac ', 'module psx_snac_ref '))
    (folder/'new.sv').write_text(new_text)
    subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O2', '-Wno-fatal', '-Wno-lint', '--top-module', 'tb',
                    'ref.sv', 'new.sv', str(ROOT/'sim/pocket/tb_snac_lockstep.sv'), '--Mdir', 'obj'],
                   cwd=folder, check=True, capture_output=True, timeout=600)
    out = subprocess.run([str(folder/'obj/Vtb')], cwd=folder, capture_output=True, text=True, timeout=900).stdout
    return re.search(r'SNAC lockstep clocks=(\d+) actions=(\d+) receives=(\d+) mismatches=(\d+)', out)


def main():
    text = (ROOT/'rtl/pocket/psx_snac.sv').read_text()
    m = run(ROOT/'build/sim/snac-lockstep', text)
    if not m or int(m[4]) or int(m[2]) < 1000 or int(m[3]) < 100:
        raise SystemExit(f'psx_snac lockstep failed: {m and m.groups()}')
    print(f'lockstep: {m[1]} clocks, {m[2]} actions, {m[3]} received bytes, 0 mismatches')
    fault = text.replace('localparam ATW = CARDS ? 16 : 11;', 'localparam ATW = CARDS ? 16 : 10;')
    if fault == text: raise SystemExit('fault anchor changed')
    f = run(ROOT/'build/sim/snac-lockstep-fault', fault)
    if not f or not int(f[4]): raise SystemExit('planted 10-bit ackTimer not caught')
    print(f'planted 10-bit ackTimer caught: {f[4]} mismatching clocks')
    print('PASS psx_snac lockstep')


if __name__ == '__main__':
    main()
