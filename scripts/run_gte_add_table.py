#!/usr/bin/env python3
"""Lockstep-compare the adapted GTE engine with control RAM + matrix RAM + add table + vector RAM."""
from pathlib import Path
import re
import subprocess

from prepare_gte_adapter import adapter_sources, UPSTREAM
from prepare_gte_control_ram import control_ram_text, data_ram_text
from prepare_gte_matrix_ram import compute_path_text, engine_text
from prepare_gte_add_table import add_table_text
from prepare_gte_vector_ram import vector_ram_text
from tool_paths import ROOT, find_tool

RENAMED = ('PocketGTEEngine', 'PocketGTERegs', 'PocketGTEComputePath')


def main(fault=None):
    verilator = find_tool('verilator')
    if not verilator: raise SystemExit('Verilator is required')
    directory = ROOT / 'build/sim' / ('gte-add-table' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    sources = [Path(s['path']) for s in adapter_sources(directory / 'adapter') if Path(s['path']).suffix in ('.v', '.sv')]
    by_name = {p.name: p for p in sources}
    variants = {'GTERegs.sv': lambda text: data_ram_text(control_ram_text(text)), 'GTEComputePath.sv': lambda text: (fault or (lambda t: t))(vector_ram_text(add_table_text(compute_path_text(text)))), 'GTEEngine.sv': engine_text}
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
    return result.returncode == 0 and bool(passes)


if __name__ == '__main__':
    if not main(): raise SystemExit('GTE add table lockstep failed')
    # DQB stored where OFY lives must be caught.
    if main(lambda t: t.replace("6'h3C: begin addWe = 1'b1; addWa = 4'd14;", "6'h3C: begin addWe = 1'b1; addWa = 4'd13;")):
        raise SystemExit('Planted DQB address fault not detected')
    print('Planted DQB address fault rejected')
    # Swapping the VXY halves between units must be caught.
    if main(lambda t: t.replace("vecRam1[vecWa] <= i_matWrData[15:0]", "vecRam1[vecWa] <= i_matWrData[31:16]")):
        raise SystemExit('Planted vector half fault not detected')
    print('Planted vector half fault rejected')
