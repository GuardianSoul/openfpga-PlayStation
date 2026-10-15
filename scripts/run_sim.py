#!/usr/bin/env python3
"""Compile and run memory profiler and transaction bridge contract tests."""
import subprocess
import sys

from tool_paths import ROOT, find_tool
from memory_vectors import write_vectors


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required. Install iverilog, or run scripts/setup_iverilog.sh.')
    output_dir = ROOT / 'build/sim/memory'
    output_dir.mkdir(parents=True, exist_ok=True)
    for ports, width in ((4, 32), (4, 4), (1, 32), (1, 4)):
        output = output_dir / f'profile-{ports}-{width}.vvp'
        subprocess.run([
            compiler, '-g2012', '-Wall', '-s', 'tb_psx_mem_profile',
            f'-Ptb_psx_mem_profile.PORTS={ports}',
            f'-Ptb_psx_mem_profile.COUNTER_WIDTH={width}',
            '-o', str(output), str(ROOT / 'rtl/debug/psx_mem_profile.sv'),
            str(ROOT / 'sim/memory/tb_psx_mem_profile.sv'),
        ], check=True, timeout=60)
        subprocess.run([runtime, str(output)], check=True, timeout=30)

    vector_file = output_dir / 'bridge-vectors.txt'
    count = write_vectors(vector_file)
    for controller, pin_access, second_access, pin_hz in (
        (0, 2.5, 2.5, 2.5), (1, 2.5, 2.5, 2.5), (2, 2.5, 2.5, 2.5),
        (2, 5.5, 5.5, 6.0), (2, 5.5, 2.5, 6.0),
    ):
        output = output_dir / f'bridge-{controller}-{pin_access}-{second_access}-{pin_hz}.vvp'
        subprocess.run([
            compiler, '-g2012', '-Wall', '-s', 'tb_psx_mem_bridge',
            f'-Ptb_psx_mem_bridge.CONTROLLER={controller}', '-o', str(output),
            f'-Ptb_psx_mem_bridge.PIN_T_AC={pin_access}',
            f'-Ptb_psx_mem_bridge.PIN_T_AC_SECOND={second_access}',
            f'-Ptb_psx_mem_bridge.PIN_T_HZ={pin_hz}',
            str(ROOT / 'rtl/memory/psx_mem_bridge.sv'),
            str(ROOT / 'rtl/memory/psx_sdram.sv'),
            str(ROOT / 'rtl/memory/psx_sdram_pins.sv'),
            str(ROOT / 'rtl/memory/psx_sdr_phy.sv'),
            str(ROOT / 'rtl/debug/psx_mem_profile.sv'),
            str(ROOT / 'sim/memory/tb_psx_mem_bridge.sv'),
            str(ROOT / 'sim/memory/sdr_command_model.sv'),
            str(ROOT / 'sim/memory/sdr_pin_model.sv'),
            str(ROOT / 'sim/memory/altddio_clock_model.sv'),
            str(ROOT / 'references/MIRLO64/rtl/soc/sdr_ctrl.sv'),
        ], check=True, timeout=60)
        subprocess.run([runtime, str(output), f'+vectors={vector_file}'], check=True, timeout=30)
    print(f'Byte-addressed oracle checked {count} transactions against each backend.', flush=True)


if __name__ == '__main__':
    main()
