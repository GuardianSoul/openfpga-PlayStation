#!/usr/bin/env python3
"""gpu-recip arithmetic: the shared-divisor unit and the 11-bit dividers.

sim/pocket/tb_gpu_recip_unit.vhd runs rtl/pocket/psx_gpu_recip_unit.vhd
against ten pinned PSX_MiSTer dividers per case (and an exact quotient);
a unit without its correction step must fail. sim/pocket/
tb_gpu_narrow_divider.vhd runs the release packed divider with the 11-bit
divisor against the MiSTer divider with the upstream 25-bit divisor on the
edge and line operands.
"""
import re
import shutil
import subprocess

from tool_paths import ROOT, find_tool

FLAGS = ['--std=08', '-frelaxed']
UNIT = ROOT / 'rtl/pocket/psx_gpu_recip_unit.vhd'
MISTER = ROOT / 'references/PSX_MiSTer/rtl/divider.vhd'
CORRECTION = "if (s3R >= ('0' & dAbs)) then c := '1'; else c := '0'; end if;"


def ghdl_run(ghdl, folder, files, top, generics):
    subprocess.run([ghdl, '-a', *FLAGS, *map(str, files)], cwd=folder, check=True, capture_output=True)
    subprocess.run([ghdl, '-e', *FLAGS, top], cwd=folder, check=True, capture_output=True)
    run = subprocess.run([ghdl, '-r', *FLAGS, top, *generics], cwd=folder, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    return run.returncode, run.stdout


def main():
    ghdl = find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL required')
    base = ROOT / 'build/sim/gpu-recip-unit'
    if UNIT.read_text().count(CORRECTION) != 1: raise SystemExit('Mutation anchor missing')
    shutil.rmtree(base, ignore_errors=True)
    for name, unit, cases, expect in (('unit', UNIT.read_text(), 20000, True),
                                      ('no-correction', UNIT.read_text().replace(CORRECTION, "c := '0';"), 300, False)):
        folder = base / name; folder.mkdir(parents=True)
        (folder / 'unit.vhd').write_text(unit)
        code, out = ghdl_run(ghdl, folder, [MISTER, folder / 'unit.vhd', ROOT / 'sim/pocket/tb_gpu_recip_unit.vhd'],
                             'tb_gpu_recip_unit', [f'-gCASES={cases}'])
        found = re.search(r'RECIP cases=(\d+) quotients=(\d+) nonzero=(\d+) upstream_vs_exact=(\d+) errs=(\d+)', out)
        passed = bool(code == 0 and found and found[5] == "0" and found[4] == "0")
        if passed != expect: raise SystemExit(f'{name}: unexpected result\n{out[-2000:]}')
        print(f'{name}: ' + (f'{found[2]} quotients match the upstream divider ({found[3]} nonzero)' if expect else 'rejected'))
    folder = base / 'narrow'; folder.mkdir(parents=True)
    (folder / 'divider_mister.vhd').write_text(MISTER.read_text().replace('entity divider is', 'entity divider_mister is')
                                               .replace('architecture arch of divider is', 'architecture arch of divider_mister is'))
    code, out = ghdl_run(ghdl, folder, [folder / 'divider_mister.vhd', ROOT / 'rtl/pocket/psx_divider_bank.vhd',
                                        ROOT / 'sim/pocket/tb_gpu_narrow_divider.vhd'], 'tb_gpu_narrow_divider', ['-gCASES=30000'])
    found = re.search(r'NARROW cases=(\d+) zero_dy=(\d+) errs=(\d+)', out)
    if code or not found or found[3] != '0': raise SystemExit(f'narrow divider failed\n{out[-2000:]}')
    print(f'narrow divider: {found[1]} edge/line divisions match ({found[2]} zero-dy cases checked for timing only)')
    print('PASS gpu-recip arithmetic')


if __name__ == '__main__':
    main()
