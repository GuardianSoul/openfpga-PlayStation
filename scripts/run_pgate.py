#!/usr/bin/env python3
"""Check clock mailboxes and pgate memory protocols through SDRAM pins."""
import subprocess
import sys
from tool_paths import ROOT, find_tool
from pgate_vectors import write_vectors


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required for pgate memory tests.')
    output_dir = ROOT / 'build/sim/pgate'
    output_dir.mkdir(parents=True, exist_ok=True)
    for source, destination, phase in ((10, 8, 3), (7, 11, 1), (17, 5, 2)):
        output = output_dir / f'cdc-{source}-{destination}.vvp'
        subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_mem_cdc',
                        f'-Ptb_psx_mem_cdc.SRC_HALF={source}', f'-Ptb_psx_mem_cdc.DST_HALF={destination}',
                        f'-Ptb_psx_mem_cdc.DST_PHASE={phase}', '-o', str(output),
                        str(ROOT / 'rtl/memory/psx_mem_cdc.sv'), str(ROOT / 'sim/memory/tb_psx_mem_cdc.sv')],
                       check=True, timeout=60)
        subprocess.run([runtime, str(output)], check=True, timeout=30)
    system_count, gpu_count = write_vectors(output_dir)
    sources = [
        'rtl/memory/psx_pgate_mem.sv', 'rtl/memory/psx_mem_cdc.sv', 'rtl/memory/psx_pgate_memory.sv',
        'rtl/memory/psx_mem_arb.sv', 'rtl/memory/psx_mem_bridge.sv', 'rtl/memory/psx_sdram.sv',
        'rtl/memory/psx_sdram_pins.sv', 'rtl/memory/psx_sdr_phy.sv', 'rtl/memory/psx_mem_clocks.sv',
        'rtl/memory/psx_mem_pocket.sv', 'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
        'sim/memory/altddio_clock_model.sv', 'sim/memory/sdr_pin_model.sv',
        'sim/memory/altera_pll_clock_model.sv', 'sim/memory/tb_psx_pgate_memory.sv',
    ]
    for half_period in (10, 7):
        output = output_dir / f'pgate-{half_period}.vvp'
        subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_pgate_memory',
                        f'-Ptb_psx_pgate_memory.CORE_HALF={half_period}', '-o', str(output),
                        *(str(ROOT / source) for source in sources)], check=True, timeout=60)
        subprocess.run([runtime, str(output), f'+programs={output_dir}'], check=True, timeout=30)
    print(f'Pgate byte oracle checked {system_count} system and {gpu_count} GPU operations per clock configuration.', flush=True)


if __name__ == '__main__':
    main()
