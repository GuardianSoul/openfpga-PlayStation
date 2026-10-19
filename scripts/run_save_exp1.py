#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Test rtl/pocket/psx_save_exp1.sv (EXP1 device + APF handshake for save states).

Runs sim/pocket/tb_save_exp1.sv under Icarus with a seeded random ROM image,
then a planted fault (copies start at page word 1) that must be rejected.
"""
import random
import re
import subprocess
import sys

from tool_paths import ROOT, find_tool

OUT = ROOT / 'build/sim/save-exp1'
RTL = ROOT / 'rtl/pocket/psx_save_exp1.sv'
TB = ROOT / 'sim/pocket/tb_save_exp1.sv'
MODELS = ROOT / 'sim/reference/mister_memory_models.sv'   # dpram (the staging page)
# fault: a copy starts at page word 1
GOOD = "cp_dir <= bus_dataWrite[1]; cp_word <= 9'd0;"
FAULT = "cp_dir <= bus_dataWrite[1]; cp_word <= 9'd1;"


def run(name, rtl):
    vvp = OUT / f'{name}.vvp'
    subprocess.run([find_tool('iverilog'), '-g2012', '-s', 'tb_save_exp1', '-o', str(vvp), str(TB), str(MODELS), str(rtl)], check=True)
    r = subprocess.run([find_tool('vvp'), '-n', vvp.name], cwd=OUT, capture_output=True, text=True, timeout=1800)
    (OUT / f'{name}.log').write_text(r.stdout + r.stderr)
    return r.stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0x5A7E)
    (OUT / 'tb_save_exp1_rom.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(4096)))
    src = RTL.read_text()
    if src.count(GOOD) != 1:
        sys.exit('copy start line not found for the fault variant')
    (OUT / 'fault_gather.sv').write_text(src.replace(GOOD, FAULT))
    ok = run('good', RTL)
    if 'PASS save exp1' not in ok:
        print(ok); sys.exit('save exp1 bench failed')
    print(re.search(r'aux accesses.*', ok).group(0))
    bad = run('fault_gather', OUT / 'fault_gather.sv')
    if 'PASS save exp1' in bad or 'page to buffer' not in bad:
        print(bad); sys.exit('planted copy-start fault was not caught')
    print('PASS save exp1 (good + planted copy-start fault rejected)')


if __name__ == '__main__':
    main()
