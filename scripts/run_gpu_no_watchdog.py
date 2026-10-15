#!/usr/bin/env python3
"""Cycle-compare the GPU with and without gpu-no-watchdog (gpu_rect) on the GPU oracle programs; every sampled output must match every
cycle, since no oracle primitive comes near the 2^20-clock limit."""
import re
import subprocess

import prepare_gpu_borrowed
import run_gpu_line_share as share
from prepare_gpu_poly_share import poly_share_text
from prepare_gpu_vertex_ram import vertex_ram_text
from prepare_gpu_mul_narrow import mul_narrow_text
from prepare_gpu_mul_retime import mul_retime_text
from prepare_gpu_no_watchdog import no_watchdog_text
from tool_paths import ROOT, find_tool


def main(fault=None):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim' / ('gpu-no-watchdog' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    original_text = prepare_gpu_borrowed.borrowed_text
    def chain(narrow):
        def text_for(name, text):
            text = original_text(name, text)
            if name == 'gpu_rect.vhd':
                if narrow and fault: return text.replace('elsif (timeout < 1048575) then', 'elsif (timeout < 8) then')
                return no_watchdog_text(text) if narrow else text
            if name != 'gpu_poly.vhd': return text
            return mul_retime_text(mul_narrow_text(vertex_ram_text(poly_share_text(text))))
        return text_for
    with (directory / 'checks.log').open('w') as file:
        prepare_gpu_borrowed.borrowed_text = chain(False)
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        oldverilog.write_text(oldverilog.read_text().replace('shared__', 'original__')); oldname = oldname.replace('shared__', 'original__')
        prepare_gpu_borrowed.borrowed_text = chain(True)
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        prepare_gpu_borrowed.borrowed_text = original_text
        top = share.bench(directory, oldname, newname, False, True, True)
        top.write_text(top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU no watchdog'))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    ok = result.returncode == 0 and re.search(r'PASS GPU cycle outputs ticks=\s*[1-9]', output) and 'PASS GPU no watchdog' in output
    return bool(ok)


if __name__ == '__main__':
    if not main(): raise SystemExit('GPU watchdog removal comparison failed')
    # A watchdog that fires after 8 clocks must show up as a difference.
    if main(True): raise SystemExit('Planted early-watchdog fault not detected')
    print('Planted early-watchdog fault rejected')
