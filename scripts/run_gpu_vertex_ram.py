#!/usr/bin/env python3
"""Pixel-compare the candidate GPU with gpu-poly-share + gpu-vertex-ram.

Triangles start later, so outputs are not cycle-aligned; the bench compares
the complete VRAM image of every oracle program and the GPU readbacks.
"""
import re
import subprocess

import prepare_gpu_borrowed
import run_gpu_line_share as share
from prepare_gpu_poly_share import poly_share_text
from prepare_gpu_vertex_ram import vertex_ram_text
from tool_paths import ROOT, find_tool


def main():
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim/gpu-vertex-ram'; directory.mkdir(parents=True, exist_ok=True)
    original_text = prepare_gpu_borrowed.borrowed_text
    with (directory / 'checks.log').open('w') as file:
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        oldverilog.write_text(oldverilog.read_text().replace('shared__', 'original__')); oldname = oldname.replace('shared__', 'original__')
        def variant(name, text):
            text = original_text(name, text)
            return vertex_ram_text(poly_share_text(text)) if name == 'gpu_poly.vhd' else text
        prepare_gpu_borrowed.borrowed_text = variant
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        prepare_gpu_borrowed.borrowed_text = original_text
        top = share.bench(directory, oldname, newname, False, False, True)
        top.write_text(top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU vertex RAM'))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    if result.returncode or 'PASS GPU vertex RAM' not in output or not re.search(r'PASS GPU readback checks=\s*24', output):
        raise SystemExit('GPU vertex RAM comparison failed')


if __name__ == '__main__': main()
