#!/usr/bin/env python3
"""Compile and run the PSRAM frontend against timing-checked chip models."""
import subprocess
import sys

from tool_paths import ROOT, find_tool
from psram_vectors import write_vectors


def simulate(compiler, runtime, output_dir, vector_file, clock_mhz=62.5, chip_t_aw=70.0):
    output = output_dir / f'psram-{clock_mhz}-{chip_t_aw}.vvp'
    subprocess.run([
        compiler, '-g2012', '-Wall', '-Wno-timescale', '-s', 'tb_psx_psram',
        f'-Ptb_psx_psram.CLOCK_MHZ={clock_mhz}', f'-Ptb_psx_psram.CHIP_T_AW={chip_t_aw}',
        '-o', str(output),
        str(ROOT / 'references/analogue-pocket-utils/ip/mem/psram.sv'),
        str(ROOT / 'rtl/memory/psx_mem_bridge.sv'),
        str(ROOT / 'rtl/memory/psx_psram.sv'),
        str(ROOT / 'sim/memory/cellularram_async_model.sv'),
        str(ROOT / 'sim/memory/tb_psx_psram.sv'),
    ], check=True, timeout=60)
    # The controller announces its cycle counts with $info; keep the rest.
    result = subprocess.run([runtime, str(output), f'+vectors={vector_file}'],
                            capture_output=True, text=True, timeout=300)
    lines = [line for line in (result.stdout + result.stderr).splitlines()
             if line and not line.startswith(('INFO:', '      Time:', '       Time:'))]
    return result.returncode == 0 and 'PSRAM:' in result.stdout, lines


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required. Install iverilog, or run scripts/setup_iverilog.sh.')
    if not (ROOT / 'references/analogue-pocket-utils/ip/mem/psram.sv').exists():
        sys.exit('Missing references/analogue-pocket-utils; run git submodule update --init.')
    output_dir = ROOT / 'build/sim/psram'
    output_dir.mkdir(parents=True, exist_ok=True)
    vector_file = output_dir / 'psram-vectors.txt'
    count = write_vectors(vector_file)
    passed, lines = simulate(compiler, runtime, output_dir, vector_file)
    print('\n'.join(lines), flush=True)
    if not passed:
        sys.exit('PSRAM simulation failed.')
    # Mutations the checks must reject: controllers told the clock is half
    # as fast (reads sampled before the access time), and chips that need a
    # longer write cycle than the controllers give them.
    for name, options in (('half-clock controller', {'clock_mhz': 31.25}),
                          ('100 ns write-cycle chip', {'chip_t_aw': 100.0})):
        passed, lines = simulate(compiler, runtime, output_dir, vector_file, **options)
        fatal = next((line for line in lines if line.startswith('FATAL')), None)
        if passed or not fatal:
            sys.exit(f'The {name} mutation was not rejected.')
        print(f'Rejected {name}: {fatal.split(": ", 2)[-1]}', flush=True)
    print(f'PSRAM byte oracle checked {count} transactions.', flush=True)

if __name__ == '__main__':
    main()
