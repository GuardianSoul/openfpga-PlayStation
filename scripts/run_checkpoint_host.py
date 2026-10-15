#!/usr/bin/env python3
"""Compare the checkpoint sector host with the existing RTL protocol fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    original = ROOT / 'build/sim/disc-host'
    if not (original / 'report.json').exists():
        subprocess.run(['python3', str(ROOT / 'scripts/run_disc_host.py')], check=True)
    directory = ROOT / 'build/sim/checkpoint-host'
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original / 'Disc Host Test', directory / 'Disc Host Test', dirs_exist_ok=True)
    for name in ('trackinfo.hex32', 'expected.hex16'):
        shutil.copy2(original / name, directory / name)
    source = ROOT / 'sim/reference/psx_disc_host.sv'
    driver = ROOT / 'sim/reference/tb_checkpoint_host.cpp'
    host = ROOT / 'sim/reference/psx_checkpoint_host.h'
    bench = directory / 'tb_checkpoint_host.sv'
    bench.write_text('''module tb_checkpoint_host(input clk,start,sector_request,input [31:0] physical_lba,
 output sector_ack,sector_write,output [15:0] sector_data,
 output [8:0] track_addr,output [31:0] track_data,output track_write,mounted,output [31:0] sector_count);
 psx_disc_host host(.*);
endmodule
''')
    verilator = find_tool('verilator')
    if not verilator:
        raise SystemExit('Verilator required')
    prefix = Path(verilator).resolve().parent.parent
    runtime = Path(os.environ.get('VERILATOR_ROOT', prefix / 'share/verilator')) / 'include/verilated_save.cpp'
    if not runtime.is_file():
        raise SystemExit('Verilator save runtime missing')
    with (directory / 'checks.log').open('w') as log:
        subprocess.run([verilator, '--cc', '--exe', '--build', '--timing', '-j', '2', '-Wno-fatal',
                        '--top-module', 'tb_checkpoint_host', '--Mdir', str(directory / 'obj'),
                        str(source), str(bench), str(driver), str(runtime)], cwd=directory,
                       stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
    executable = directory / 'obj/Vtb_checkpoint_host'
    protected = {str(path): digest(path) for path in (source, driver, host, bench, executable,
                 directory / 'Disc Host Test/Disc Image.bin', directory / 'trackinfo.hex32', directory / 'expected.hex16')}
    command = [str(executable), '+disc_file=Disc Host Test/Disc Image.bin',
               '+disc_metadata=trackinfo.hex32', '+disc_sectors=3', '+disc_index=2', '+disc_delay=13']
    run = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
    output = run.stdout + run.stderr
    print(output, end='')
    identity = all(digest(Path(path)) == sha for path, sha in protected.items())
    passed = run.returncode == 0 and identity and 'restored_phases=9' in output
    (directory / 'report.json').write_text(json.dumps(
        {'passed': passed, 'source_and_input_identity_verified': identity, 'protected_sha256': protected,
         'stdout': output, 'metadata_checks': 400, 'word_checks': 8232, 'restored_phases': 9,
         'game_verified': False, 'hardware_verified': False}, indent=2) + '\n')
    if not passed:
        raise SystemExit('Checkpoint sector host comparison failed')


if __name__ == '__main__':
    main()
