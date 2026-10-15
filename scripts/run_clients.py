#!/usr/bin/env python3
"""Validate concurrent client ownership through the memory pin interface."""
import subprocess
import sys
from tool_paths import ROOT, find_tool
from memory_vectors import vectors


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required for client tests.')
    output_dir = ROOT / 'build/sim/clients'
    output_dir.mkdir(parents=True, exist_ok=True)
    programs = [[] for _ in range(4)]
    for case in vectors():
        address = case[0]
        port = 3 if address >= 0x1000000 else 2 if address >= 0x800000 else 1 if address >= 0x100000 else 0
        programs[port].append(case)
    for port, cases in enumerate(programs):
        (output_dir / f'client-{port}.txt').write_text(
            ''.join(' '.join(f'{field:x}' for field in case)+'\n' for case in cases))
    sources = [
        'rtl/memory/psx_mem_arb.sv', 'rtl/memory/psx_mem_bridge.sv',
        'rtl/memory/psx_sdram.sv', 'rtl/memory/psx_sdram_pins.sv', 'rtl/memory/psx_sdr_phy.sv',
        'rtl/memory/psx_mem_clocks.sv', 'rtl/memory/psx_mem_pocket.sv',
        'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
        'sim/memory/altddio_clock_model.sv', 'sim/memory/sdr_pin_model.sv',
        'sim/memory/altera_pll_clock_model.sv',
        'sim/memory/tb_psx_mem_clients.sv',
    ]
    for component in (0, 1):
        output = output_dir / f'clients-{component}.vvp'
        subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_mem_clients',
                        f'-Ptb_psx_mem_clients.USE_COMPONENT={component}', '-o', str(output),
                        *(str(ROOT / source) for source in sources)], check=True, timeout=60)
        subprocess.run([runtime, str(output), f'+programs={output_dir}'], check=True, timeout=30)


if __name__ == '__main__':
    main()
