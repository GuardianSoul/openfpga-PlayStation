#!/usr/bin/env python3
"""Checks for prepare --dualshock.

1. rtl/pocket/psx_pocket_analog.sv in iverilog (controller types, glitch
   filter, checkbox).
2. The pinned MiSTer joypad_pad driven by the PSX wrapper lines that
   prepare_pocket_dualshock_pad.py writes: an analog-only game's config
   sequence must reach ID 0x73 with the APF stick bytes, and with the
   checkbox off the pad must stay digital (0x41). Two stick words.
3. The wrapper edit applies to a release wrapper (F-nopool's when present).
Planted faults (wrong stick byte, DS forced off) must be caught.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tool_paths import ROOT, find_tool

sys.path.insert(0, str(ROOT/'scripts'))
from prepare_pocket_dualshock_pad import WRAPPER_FIELDS, dualshock_wrapper_text  # noqa: E402

BENCH = ROOT/'sim/pocket/tb_joypad_dualshock.vhd'
PINNED = ROOT/'references/PSX_MiSTer/rtl/joypad_pad.vhd'


def analog_module():
    folder = ROOT/'build/sim/pocket-analog'; shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    binary = folder/'tb'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_psx_pocket_analog', '-o', str(binary),
                    str(ROOT/'sim/pocket/tb_psx_pocket_analog.sv'), str(ROOT/'rtl/pocket/psx_pocket_analog.sv')], check=True)
    out = subprocess.run([find_tool('vvp'), str(binary)], check=True, capture_output=True, text=True).stdout
    print(out.strip())
    if 'PASS psx_pocket_analog' not in out: raise SystemExit(1)


def protocol(fields, runs, tag):
    ghdl = find_tool('ghdl')
    folder = ROOT/'build/sim/joypad-dualshock'/tag; shutil.rmtree(folder, ignore_errors=True); folder.mkdir(parents=True)
    bench = BENCH.read_text()
    if bench.count('--@FIELDS@\n') != 1: raise SystemExit('bench anchor missing')
    (folder/'tb.vhd').write_text(bench.replace('--@FIELDS@\n', ''.join(new for _, new in fields)))
    flags = ['--std=08', '-frelaxed']
    files = [ROOT/'references/PSX_MiSTer/rtl/pJoypad.vhd', PINNED, folder/'tb.vhd']
    subprocess.run([ghdl, '-a', *flags, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *flags, 'tb_joypad_dualshock'], cwd=folder, check=True, capture_output=True)
    total = 0
    for ds, sticks in runs:
        out = subprocess.run([ghdl, '-r', *flags, 'tb_joypad_dualshock', f'-gDUALSHOCK={ds}', f'-gSTICKS={sticks:032b}'],
                             cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        found = re.search(r'DUALSHOCK=\d+ errs=(\d+)', out)
        if not found: raise SystemExit(f'No summary for DUALSHOCK={ds}:\n{out[-2000:]}')
        if int(found.group(1)) and tag == 'release': print(out.strip())
        total += int(found.group(1))
    return total


def main():
    analog_module()
    runs = [(1, 0x20E017C4), (1, 0x80FF007F), (0, 0x20E017C4)]
    errs = protocol(WRAPPER_FIELDS, runs, 'release')
    if errs: raise SystemExit(f'DualShock protocol: {errs} wrong bytes')
    print('DualShock protocol: analog mode reached with APF sticks; checkbox off stays digital (3 runs)')
    faults = {
        'swapped left axes': [(o, n.replace('pad_sticks(7)', 'pad_sticks(15)').replace('pad_sticks(6 downto 0)', 'pad_sticks(14 downto 8)'))
                              if 'Analog1X' in o else (o, n) for o, n in WRAPPER_FIELDS],
        'DualShock stuck off': [(o, n.replace('<= pad_dualshock;', "<= '0';").replace('not pad_dualshock', "'1'")) for o, n in WRAPPER_FIELDS],
        'no centre flip': [(o, n.replace('(not pad_sticks(31))', 'pad_sticks(31)')) for o, n in WRAPPER_FIELDS],
    }
    for name, fields in faults.items():
        if fields == list(WRAPPER_FIELDS): raise SystemExit(f'fault not planted: {name}')
        caught = protocol(fields, runs[:2], 'fault')
        if not caught: raise SystemExit(f'fault not caught: {name}')
        print(f'fault caught ({name}): {caught} wrong bytes')
    wrapper = ROOT/'build/claude-fit/F-nopool/sdram-only/psx_mister_psx_top_resource.vhd'
    if wrapper.is_file():
        text = dualshock_wrapper_text(wrapper.read_text())
        if 'pad_sticks : in std_logic_vector(31 downto 0);' not in text: raise SystemExit('wrapper port missing')
        print('release wrapper edit applies:', wrapper)
    print('PASS joypad dualshock')


if __name__ == '__main__':
    main()
