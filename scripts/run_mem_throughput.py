#!/usr/bin/env python3
"""Measure PSX-like access patterns on the SDRAM and PSRAM frontends in simulation."""
import subprocess
import sys

from tool_paths import ROOT, find_tool

SOURCES = (
    'rtl/memory/psx_mem_bridge.sv', 'rtl/memory/psx_sdram.sv',
    'references/MIRLO64/rtl/soc/sdr_ctrl.sv', 'sim/memory/sdr_command_model.sv',
    'references/analogue-pocket-utils/ip/mem/psram.sv', 'rtl/memory/psx_psram.sv',
    'sim/memory/cellularram_async_model.sv', 'sim/memory/tb_mem_throughput.sv',
)


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required. Install iverilog, or run scripts/setup_iverilog.sh.')
    output_dir = ROOT / 'build/sim/throughput'
    output_dir.mkdir(parents=True, exist_ok=True)
    for backend in (0, 1):
        output = output_dir / f'throughput-{backend}.vvp'
        subprocess.run([compiler, '-g2012', '-Wno-timescale', '-s', 'tb_mem_throughput',
                        f'-Ptb_mem_throughput.BACKEND={backend}', '-o', str(output),
                        *(str(ROOT / source) for source in SOURCES)],
                       check=True, timeout=60, stderr=subprocess.DEVNULL)
        result = subprocess.run([runtime, str(output)], capture_output=True, text=True, timeout=300)
        lines = [line for line in result.stdout.splitlines()
                 if line.strip() and not line.startswith(('INFO:', '      Time:', '/'))]
        print('\n'.join(lines), flush=True)
        if result.returncode:
            sys.exit(result.stderr or 'Throughput simulation failed.')


if __name__ == '__main__':
    main()
