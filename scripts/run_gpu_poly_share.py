#!/usr/bin/env python3
"""Cycle-compare the candidate GPU (borrowed inputs) with gpu-poly-share added.

Reuses run_gpu_line_share's transcoder and bench: both GPUs run the same
pixel oracle programs and every sampled GPU output is compared each cycle.
"""
from pathlib import Path
import re
import subprocess

import prepare_gpu_borrowed
import run_gpu_line_share as share
from prepare_gpu_poly_share import poly_share_text
from tool_paths import ROOT, find_tool


def main():
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim/gpu-poly-share'; directory.mkdir(parents=True, exist_ok=True)
    log = directory / 'checks.log'
    original_text = prepare_gpu_borrowed.borrowed_text
    with log.open('w') as file:
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        text = oldverilog.read_text().replace('shared__', 'original__'); oldverilog.write_text(text)
        oldname = oldname.replace('shared__', 'original__')
        def with_share(name, text):
            text = original_text(name, text)
            return poly_share_text(text) if name == 'gpu_poly.vhd' else text
        prepare_gpu_borrowed.borrowed_text = with_share
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        prepare_gpu_borrowed.borrowed_text = original_text
        top = share.bench(directory, oldname, newname, False, True, True)
        bench = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU poly share'); top.write_text(bench)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    ok = result.returncode == 0 and re.search(r'PASS GPU cycle outputs ticks=\s*[1-9]', output) and 'PASS GPU poly share' in output
    if not ok: raise SystemExit('GPU poly share comparison failed')


if __name__ == '__main__': main()
