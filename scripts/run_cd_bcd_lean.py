#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check prepare_cd_bcd_lean.py: the frame-number table against / 10 and mod 10.

1. Exhaustive (GHDL): FRAME_BCD, taken from the patched cd_top text, against
   std_logic_vector(resize(x / 10, 4)) & std_logic_vector(resize(x mod 10, 4))
   for x = 0..127. A table with one entry changed (x = 73) must fail.
2. Clock level: run_cd_lockstep.py with the original cd_top as reference and
   the patched copy as candidate, plus its planted command-timing fault and a
   planted fault in the new lookup (ones digit of nextSubdata(5) flipped).
Usage: run_cd_bcd_lean.py --project PROJECT [--seeds 3 2 4] [--cycles N]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from prepare_cd_bcd_lean import patch, target
from tool_paths import ROOT, find_tool

TB = '''library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity tb_cd_bcd_lean is
end entity;

architecture sim of tb_cd_bcd_lean is
{table}
begin
   process
      variable frameLeft : unsigned(6 downto 0);
      variable expect    : std_logic_vector(7 downto 0);
      variable bad       : integer := 0;
   begin
      for x in 0 to 127 loop
         frameLeft := to_unsigned(x, 7);
         expect := std_logic_vector(resize(frameLeft / 10, 4)) & std_logic_vector(resize(frameLeft mod 10, 4));
         if FRAME_BCD(to_integer(frameLeft)) /= expect then
            report "BCD MISMATCH x=" & integer'image(x);
            bad := bad + 1;
         end if;
      end loop;
      if bad = 0 then report "BCD PASS 128 values"; else report "BCD FAIL " & integer'image(bad); end if;
      wait;
   end process;
end architecture;
'''


def exhaustive(table, work, ghdl):
    work.mkdir(parents=True, exist_ok=True)
    (work / 'tb_cd_bcd_lean.vhd').write_text(TB.format(table=table))
    opts = ['--std=08', f'--workdir={work}']
    for cmd in ([ghdl, '-a', *opts, work / 'tb_cd_bcd_lean.vhd'], [ghdl, '-e', *opts, 'tb_cd_bcd_lean']):
        subprocess.run(cmd, cwd=work, check=True, capture_output=True)
    out = subprocess.run([ghdl, '-r', *opts, 'tb_cd_bcd_lean'], cwd=work, capture_output=True, text=True).stdout
    m = re.search(r'BCD (PASS[^\n]*|FAIL \d+)', out)
    return m[1] if m else 'NO RESULT'


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--project', type=Path, required=True)
    ap.add_argument('--seeds', type=int, nargs='+', default=[3, 2, 4])
    ap.add_argument('--cycles', type=int, default=6000000)
    args = ap.parse_args()
    ghdl = find_tool('ghdl')
    work = ROOT / 'build/sim/cd-bcd-lean'
    work.mkdir(parents=True, exist_ok=True)
    reference = target(args.project.resolve())
    text = patch(reference.read_text())
    (work / 'cd_top_bcd.vhd').write_text(text)

    table = text[text.index('   type t_frame_bcd'):text.index(');\n', text.index('constant FRAME_BCD')) + 3]
    row = [l for l in table.splitlines() if l.startswith('      x"72"')]
    if len(row) != 1: raise SystemExit('table row 72..79 not found')
    mutated = table.replace(row[0], row[0].replace('x"73"', 'x"74"', 1))
    failures = []
    for name, tab, want in (('table', table, 'PASS'), ('mutation x=73', mutated, 'FAIL')):
        r = exhaustive(tab, work / name.split()[0], ghdl)
        ok = r.startswith(want)
        print(f'exhaustive {name}: {r} -> {"ok" if ok else "FAIL"}', flush=True)
        if not ok: failures.append(name)

    site = 'nextSubdata(5)             <= FRAME_BCD(to_integer(frameLeft));'
    cmd = [sys.executable, str(ROOT / 'scripts/run_cd_lockstep.py'), '--reference', str(reference),
           '--candidate', str(work / 'cd_top_bcd.vhd'), '--project', str(args.project.resolve()), '--work', str(work / 'lockstep'),
           '--seeds', *map(str, args.seeds), '--cycles', str(args.cycles),
           '--extra-fault', site, site.replace('FRAME_BCD(to_integer(frameLeft))', 'FRAME_BCD(to_integer(frameLeft)) xor x"01"')]
    r = subprocess.run(cmd, cwd=ROOT / 'scripts')
    if r.returncode: failures.append('lockstep')
    if failures: raise SystemExit('cd-bcd-lean FAIL: ' + ', '.join(failures))
    print('cd-bcd-lean PASS')


if __name__ == '__main__':
    main()
