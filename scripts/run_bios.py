#!/usr/bin/env python3
"""Trace a local 512 KiB BIOS until the first missing device or explicit bound."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

from tool_paths import ROOT, find_tool


def compile_diagnostic(compiler, output):
    subprocess.run([compiler, '-g2012', '-Wall', '-s', 'tb_psx_bios', '-o', str(output),
                    str(ROOT / 'rtl/cpu/psx_cpu.sv'), str(ROOT / 'rtl/cpu/psx_cpu_bus.sv'),
                    str(ROOT / 'sim/cpu/tb_psx_bios.sv')], check=True, timeout=60)


def execute_diagnostic(runtime, output, image, output_dir, max_instructions, max_cycles):
    result = subprocess.run([runtime, str(output), f'+bios={image}',
                             f'+trace={output_dir / "trace.csv"}',
                             f'+max_instructions={max_instructions}',
                             f'+max_cycles={max_cycles}'],
                            capture_output=True, text=True, timeout=60)
    (output_dir / 'run.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    reports = [line.removeprefix('BIOS_DIAGNOSTIC ') for line in result.stdout.splitlines()
               if line.startswith('BIOS_DIAGNOSTIC ')]
    if len(reports) != 1:
        raise RuntimeError('Simulator did not emit exactly one diagnostic stop record.')
    return json.loads(reports[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bios', type=Path, default=ROOT / 'local-assets/bios/scph5501.bin')
    parser.add_argument('--max-instructions', type=int, default=100000)
    parser.add_argument('--max-cycles', type=int, default=5000000)
    args = parser.parse_args()
    if args.max_instructions <= 0 or args.max_cycles <= 0:
        parser.error('Diagnostic bounds must be positive.')
    try:
        data = args.bios.read_bytes()
    except OSError as error:
        parser.error(str(error))
    if len(data) != 512 * 1024:
        parser.error('BIOS must be exactly 512 KiB.')
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        sys.exit('Icarus Verilog is required for the BIOS diagnostic.')
    output_dir = ROOT / 'build/sim/bios'
    output_dir.mkdir(parents=True, exist_ok=True)
    image = output_dir / 'bios.hex'
    image.write_text(''.join(f'{word[0]:08x}\n' for word in struct.iter_unpack('<I', data)))
    output = output_dir / 'bios.vvp'
    compile_diagnostic(compiler, output)
    report = execute_diagnostic(runtime, output, image, output_dir, args.max_instructions, args.max_cycles)
    report.update(bios=str(args.bios.resolve()), bytes=len(data),
                  md5=hashlib.md5(data).hexdigest(), sha256=hashlib.sha256(data).hexdigest(),
                  memory_model='functional bus fixture; no SDRAM pins',
                  bios_boot_completed=False)
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    print(f'Local trace and report: {output_dir}')


if __name__ == '__main__':
    main()
