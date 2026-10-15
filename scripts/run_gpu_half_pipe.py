#!/usr/bin/env python3
"""Cycle-compare the build's gpu_poly (poly share + vertex RAM) with and
without gpu-half-pipe on the GPU oracle programs; every sampled GPU output
must match every cycle."""
import re
import subprocess

import prepare_gpu_borrowed
import run_gpu_line_share as share
from prepare_gpu_poly_share import poly_share_text
from prepare_gpu_vertex_ram import vertex_ram_text
from prepare_gpu_half_pipe import half_pipe_text
from prepare_gpu_mul_narrow import mul_narrow_text
from tool_paths import ROOT, find_tool


def main(fault=None):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim' / ('gpu-half-pipe' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    original_text = prepare_gpu_borrowed.borrowed_text
    def chain(narrow):
        def text_for(name, text):
            text = original_text(name, text)
            if name != 'gpu_poly.vhd': return text
            text = mul_narrow_text(vertex_ram_text(poly_share_text(text)))
            text = half_pipe_text(text) if narrow else text
            return fault(text) if (narrow and fault) else text
        return text_for
    with (directory / 'checks.log').open('w') as file:
        prepare_gpu_borrowed.borrowed_text = chain(False)
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        oldverilog.write_text(oldverilog.read_text().replace('shared__', 'original__')); oldname = oldname.replace('shared__', 'original__')
        prepare_gpu_borrowed.borrowed_text = chain(True)
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        prepare_gpu_borrowed.borrowed_text = original_text
        top = share.bench(directory, oldname, newname, False, False, True)
        top.write_text(top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU half pipe'))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    ok = result.returncode == 0 and re.search(r'PASS GPU readback checks=\s*24', output) and 'PASS GPU half pipe' in output
    return bool(ok)


if __name__ == '__main__':
    if not main(): raise SystemExit('GPU half pipe comparison failed')
    # Removing the flat-triangle wait is not observable here: CALCBOUNDARY2
    # holds until div1.done and the quotient is already stable by then, so the
    # wait is a safety margin. Show the image compare sees a halfCoord error.
    if main(lambda t: t.replace('halfCoord    <= baseCoord + halfDiffBase;', 'halfCoord    <= baseCoord;')):
        raise SystemExit('Planted half-edge fault not detected')
    print('Planted half-edge fault rejected')
