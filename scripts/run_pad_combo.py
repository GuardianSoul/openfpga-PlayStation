#!/usr/bin/env python3
"""Simulate rtl/pocket/psx_pocket_pad_combo.sv (prepare --pad-combos) with iverilog."""
import subprocess
import tempfile
from pathlib import Path

from tool_paths import ROOT, find_tool


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'build' if (ROOT/'build').is_dir() else None) as work:
        binary = Path(work)/'tb_pad_combo'
        subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_psx_pocket_pad_combo', '-o', str(binary),
                        str(ROOT/'sim/pocket/tb_psx_pocket_pad_combo.sv'), str(ROOT/'rtl/pocket/psx_pocket_pad_combo.sv')], check=True)
        out = subprocess.run([find_tool('vvp'), str(binary)], check=True, capture_output=True, text=True).stdout
    print(out.strip())
    if 'PASS psx_pocket_pad_combo' not in out: raise SystemExit(1)


if __name__ == '__main__':
    main()
