#!/usr/bin/env python3
"""Run synthetic R3000 programs against architectural register/memory traces."""
import subprocess
import sys
from tool_paths import ROOT, find_tool
from cpu_vectors import encode, write_programs, write_smoke_program
from run_bios import compile_diagnostic, execute_diagnostic


def check_diagnostic_stops(compiler, runtime, output_dir):
    output = output_dir / 'diagnostic.vvp'
    compile_diagnostic(compiler, output)
    # These ROMs are generated instructions, never user BIOS data. Each one
    # verifies that the diagnostic stops before inventing missing hardware.
    cases = [
        ('mmio', [('lui',(1,0x1F80)), ('ori',(2,0,0x4321)), ('sw',(2,1,0x1070))], 100, 10000, 2),
        ('isolated_cache', [('lui',(1,1)), ('mtc0',(1,12)), ('sw',(0,0,0))], 100, 10000, 2),
        ('cop2', [('lui',(1,0x4000)), ('mtc0',(1,12)), ('cop2',(1,))], 100, 10000, 2),
        ('exception', [('invalid',())], 100, 10000, 0),
        ('instruction_limit', [('nop',())], 3, 10000, 3),
        ('cycle_limit', [('nop',())], 100, 1, 0),
    ]
    for reason, instructions, max_instructions, max_cycles, retired in cases:
        directory = output_dir / f'diagnostic-{reason}'
        directory.mkdir(parents=True, exist_ok=True)
        image = directory / 'fixture.hex'
        words = [encode(name, args) for name, args in instructions]
        image.write_text(''.join(f'{word:08x}\n' for word in words) + '00000000\n'*(131072-len(words)))
        report = execute_diagnostic(runtime, output, image, directory, max_instructions, max_cycles)
        assert report['stop_reason'] == reason and report['retired'] == retired, report
        assert report['pc'] == f'{0xBFC00000+retired*4:08x}', report
        if reason == 'mmio':
            assert report['io_address'] == '1f801070' and report['io_raw_data'] == '00004321', report
            assert report['io_write'] == 1 and report['io_mask'] == 15 and report['io_size'] == 2, report
        if reason == 'exception':
            assert report['exception_code'] == 10, report
    print('PASS reset diagnostic: six stop conditions; generated ROMs only.', flush=True)


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required for CPU tests.')
    output_dir = ROOT / 'build/sim/cpu'
    output_dir.mkdir(parents=True, exist_ok=True)
    programs, steps = write_programs(output_dir)
    for seed in (0x50535808, 0x13579BDF, 0x2468ACE0):
        output = output_dir / f'cpu-{seed:x}.vvp'
        subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_cpu',
                        f'-Ptb_psx_cpu.SEED={seed}', '-o', str(output),
                        str(ROOT / 'rtl/cpu/psx_cpu.sv'), str(ROOT / 'sim/cpu/tb_psx_cpu.sv')],
                       check=True, timeout=60)
        subprocess.run([runtime, str(output), f'+programs={output_dir}'], check=True, timeout=30)
    print(f'CPU oracle checked {steps} retire/exception states from {programs} generated programs per bus timing seed.', flush=True)
    output = output_dir / 'guest-bus.vvp'
    subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_cpu_bus', '-o', str(output),
                    str(ROOT / 'rtl/cpu/psx_cpu_bus.sv'), str(ROOT / 'sim/cpu/tb_psx_cpu_bus.sv')], check=True, timeout=60)
    subprocess.run([runtime, str(output)], check=True, timeout=30)
    check_diagnostic_stops(compiler, runtime, output_dir)
    write_smoke_program(output_dir)
    sources = [
        'rtl/cpu/psx_cpu.sv', 'rtl/cpu/psx_cpu_bus.sv', 'rtl/cpu/psx_cpu_pocket.sv',
        'rtl/memory/psx_mem_cdc.sv', 'rtl/memory/psx_mem_arb.sv', 'rtl/memory/psx_mem_bridge.sv',
        'rtl/memory/psx_sdram.sv', 'rtl/memory/psx_sdram_pins.sv', 'rtl/memory/psx_sdr_phy.sv',
        'rtl/memory/psx_mem_clocks.sv', 'rtl/memory/psx_mem_pocket.sv',
        'references/MIRLO64/rtl/soc/sdr_ctrl.sv', 'sim/memory/altddio_clock_model.sv',
        'sim/memory/sdr_pin_model.sv', 'sim/memory/altera_pll_clock_model.sv', 'sim/cpu/tb_psx_cpu_pocket.sv',
    ]
    for half_period in (10, 7):
        output = output_dir / f'cpu-pins-{half_period}.vvp'
        subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_cpu_pocket',
                        f'-Ptb_psx_cpu_pocket.CORE_HALF={half_period}', '-o', str(output),
                        *(str(ROOT / source) for source in sources)], check=True, timeout=60)
        subprocess.run([runtime, str(output), f'+programs={output_dir}'], check=True, timeout=30)


if __name__ == '__main__':
    main()
