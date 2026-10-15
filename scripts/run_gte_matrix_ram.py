#!/usr/bin/env python3
"""Lockstep-compare the adapted GTE engine with the control-RAM + matrix-RAM variant."""
from pathlib import Path
import re
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM
from prepare_gte_control_ram import control_ram_text, data_ram_text
from prepare_gte_matrix_ram import compute_path_text, engine_text
from tool_paths import ROOT, find_tool

RENAMED = ('PocketGTEEngine', 'PocketGTERegs', 'PocketGTEComputePath')


def main():
    verilator = find_tool('verilator')
    if not verilator: raise SystemExit('Verilator is required')
    directory = ROOT / 'build/sim/gte-matrix-ram'; directory.mkdir(parents=True, exist_ok=True)
    sources = [Path(s['path']) for s in adapter_sources(directory / 'adapter') if Path(s['path']).suffix in ('.v', '.sv')]
    by_name = {p.name: p for p in sources}
    variants = {'GTERegs.sv': lambda text: data_ram_text(control_ram_text(text)), 'GTEComputePath.sv': compute_path_text, 'GTEEngine.sv': engine_text}
    renamed = []
    for name, transform in variants.items():
        text = transform(by_name[name].read_text())
        for module in RENAMED: text = re.sub(r'\b' + module + r'\b', module + 'V', text)
        path = directory / ('V_' + name); path.write_text(text); renamed.append(path)
    files = [p for p in sources if p.name != 'laxer_mister_adapter.sv'] + renamed
    with (directory / 'build.log').open('w') as log:
        subprocess.run([verilator, '--binary', '-O2', '-j', '8', '-Wno-fatal', f'-I{UPSTREAM / "rtl"}', '--Mdir', str(directory / 'obj'),
                        '--top-module', 'tb_gte_matrix_ram', *map(str, files), str(ROOT / 'sim/reference/tb_gte_matrix_ram.sv')],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=900)
    result = subprocess.run([str(directory / 'obj/Vtb_gte_matrix_ram')], cwd=directory, capture_output=True, text=True, timeout=1800)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    passes = [line for line in output.splitlines() if 'PASS GTE matrix RAM' in line]
    for line in passes: print(line)
    if result.returncode or not passes: raise SystemExit(output[-3000:])


if __name__ == '__main__': main()
