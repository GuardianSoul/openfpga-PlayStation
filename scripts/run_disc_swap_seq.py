#!/usr/bin/env python3
"""Check the Pocket disc-swap sequencer (rtl/pocket/psx_disc_swap.sv)."""
from pathlib import Path
import subprocess

from tool_paths import ROOT, find_tool


def main():
    iverilog = find_tool('iverilog'); vvp = str(Path(iverilog).with_name('vvp'))
    directory = ROOT / 'build/sim/disc-swap-seq'; directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([iverilog, '-g2012', '-o', str(directory / 'seq.vvp'), str(ROOT / 'rtl/pocket/psx_disc_swap.sv'),
                    str(ROOT / 'sim/reference/tb_disc_swap_seq.sv')], check=True)
    r = subprocess.run([vvp, str(directory / 'seq.vvp')], capture_output=True, text=True, timeout=120)
    print(r.stdout.strip().splitlines()[0] if r.stdout else r.stderr)
    if r.returncode or 'PASS disc swap sequencer' not in r.stdout: raise SystemExit('Disc swap sequencer check failed')
    subprocess.run([iverilog, '-g2012', '-o', str(directory / 'sel.vvp'), str(ROOT / 'rtl/pocket/psx_disc_swap.sv'),
                    str(ROOT / 'rtl/pocket/psx_disc_select.sv'), str(ROOT / 'sim/reference/tb_disc_select.sv')], check=True)
    r = subprocess.run([vvp, str(directory / 'sel.vvp')], capture_output=True, text=True, timeout=120)
    print(r.stdout.strip().splitlines()[0] if r.stdout else r.stderr)
    if r.returncode or 'PASS disc select' not in r.stdout: raise SystemExit('Disc select check failed')


if __name__ == '__main__': main()
