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
# APF-side faults: (name, line, replacement, the error the bench must report)
APF_FAULTS = [
    # a load in reset waits for the routine, as before the wake fix (the Pocket may wait for the answer first)
    ('fault_held_waits', 'savestate_load_busy <= !core_held; held_load_74 <= core_held;',
     'savestate_load_busy <= 1\'b1; held_load_74 <= 1\'b0;', 'a load in reset is not answered OK at once'),
    # a request while another runs is not acked (the Pocket waits for every ack)
    ('fault_busy_no_ack', "savestate_load_ack <= 1'b1;   // (a second load", "savestate_load_ack <= !busy_74;   // (a second load", 'no load_ack'),
    # SAVE_MODE takes every register write (the CPU would leave stage 2's SDRAM in the middle)
    ('fault_save_mode_any', "bus_addr[3:0] == 4'd14) save_mode <= bus_dataWrite[0];", "1'b1) save_mode <= bus_dataWrite[0];",
     'save_mode follows another register'),
    # the held load never ends: later requests are refused for good
    ('fault_held_stays', "            held_load_74 <= 1'b0;\n", '', 'no save after the held load'),
]


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
    (OUT / 'tb_save_exp1_rom2.hex').write_text(''.join(f'{rng.randrange(256):02x}\n' for _ in range(1024)))
    from build_save_fw import rom40_from_byte_hex
    (OUT / 'tb_save_exp1_rom40.hex').write_text(rom40_from_byte_hex(OUT / 'tb_save_exp1_rom.hex', OUT / 'tb_save_exp1_rom2.hex'))
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
    for name, good, fault, expect in APF_FAULTS:
        if src.count(good) != 1:
            sys.exit(f'{name}: line not found')
        (OUT / f'{name}.sv').write_text(src.replace(good, fault))
        out = run(name, OUT / f'{name}.sv')
        if 'PASS save exp1' in out or expect not in out:
            print(out); sys.exit(f'planted fault {name} was not caught')
        print(f'{name}: caught ({expect})')
    print(f'PASS save exp1 (good + {1 + len(APF_FAULTS)} planted faults rejected)')


if __name__ == '__main__':
    main()
