#!/usr/bin/env python3
"""Cycle-compare gpu_poly (the full candidate chain: poly share, vertex RAM,
narrow multiply, multiply retime, half pipe, one-hot) with and without
gpu-area-reg on the GPU oracle programs; every sampled GPU output must match
every cycle."""
import re
import subprocess

import prepare_gpu_borrowed
import run_gpu_line_share as share
from prepare_gpu_poly_share import poly_share_text
from prepare_gpu_vertex_ram import vertex_ram_text
from prepare_gpu_mul_narrow import mul_narrow_text
from prepare_gpu_mul_retime import mul_retime_text
from prepare_gpu_half_pipe import half_pipe_text
from prepare_gpu_poly_onehot import onehot_text
from prepare_gpu_area_reg import area_reg_text
from tool_paths import ROOT, find_tool


# Flat and Gouraud triangles that cross every edge of narrow drawing areas,
# so lines start left of, inside and right of the area and step across its
# right edge (the oracle programs draw with the full-VRAM area).
CLIP_CASES = ''' for(integer trial=0;trial<40;trial++) begin : area_clip_case
  integer l,r,t,b,x0,y0,x1,y1,x2,y2;
  l=(trial*37)%300;r=l+(trial%5==0 ? 0:6+(trial*53)%180);t=(trial*11)%200;b=t+4+(trial*7)%60;
  x0=l-20+(trial*13)%40;y0=t-6;x1=r+25-(trial*17)%50;y1=t+(b-t)/2;x2=l+(trial*29)%(r-l+30)-10;y2=b+8;
  write_gpu(0,32'he1000000 | ((trial%2)<<9));
  write_gpu(0,32'he3000000 | (t<<10) | l);write_gpu(0,32'he4000000 | (b<<10) | r);
  write_gpu(0,32'he5000000);
  if(trial%2) begin
   write_gpu(0,32'h30000000 | 24'h2040c0);write_gpu(0,((y0&65535)<<16)|(x0&65535));
   write_gpu(0,24'hc08020);write_gpu(0,((y1&65535)<<16)|(x1&65535));
   write_gpu(0,24'h40c080);write_gpu(0,((y2&65535)<<16)|(x2&65535));
  end else begin
   write_gpu(0,32'h20000000 | 24'h30a050);write_gpu(0,((y0&65535)<<16)|(x0&65535));
   write_gpu(0,((y1&65535)<<16)|(x1&65535));write_gpu(0,((y2&65535)<<16)|(x2&65535));
  end
  idle();compare_vram();
 end
 write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);
'''


def main(fault=None):
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    directory = ROOT / 'build/sim' / ('gpu-area-reg' + ('-fault' if fault else '')); directory.mkdir(parents=True, exist_ok=True)
    original_text = prepare_gpu_borrowed.borrowed_text
    def chain(variant):
        def text_for(name, text):
            text = original_text(name, text)
            if name != 'gpu_poly.vhd': return text
            text = onehot_text(half_pipe_text(mul_retime_text(mul_narrow_text(vertex_ram_text(poly_share_text(text))))))
            text = area_reg_text(text) if variant else text
            return fault(text) if (variant and fault) else text
        return text_for
    with (directory / 'checks.log').open('w') as file:
        prepare_gpu_borrowed.borrowed_text = chain(False)
        _, oldverilog, oldname = share.transcode(directory / 'original', True, ghdl, file, borrowed=True)
        oldverilog.write_text(oldverilog.read_text().replace('shared__', 'original__')); oldname = oldname.replace('shared__', 'original__')
        prepare_gpu_borrowed.borrowed_text = chain(True)
        _, newverilog, newname = share.transcode(directory / 'adapted', True, ghdl, file, borrowed=True)
        prepare_gpu_borrowed.borrowed_text = original_text
        top = share.bench(directory, oldname, newname, False, True, True)
        text = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU area reg')
        marker = ' $display("PASS GPU readback checks'
        if text.count(marker) != 1: raise ValueError('Bench readback marker changed')
        top.write_text(text.replace(marker, CLIP_CASES + marker))
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', '8', '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=file, stderr=subprocess.STDOUT, timeout=900, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True, timeout=900)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    print('\n'.join(l for l in output.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'fatal' in l.lower())[-3000:])
    ok = result.returncode == 0 and re.search(r'PASS GPU cycle outputs ticks=\s*[1-9]', output) and 'PASS GPU area reg' in output
    return bool(ok)


if __name__ == '__main__':
    if not main(): raise SystemExit('GPU area register comparison failed')
    # A right-edge flag that ignores the per-pixel step must be caught.
    def stale(t):
        old = "if (xPos + 1 <= to_integer(drawingAreaRight)) then xPosInRight <= '1'; else xPosInRight <= '0'; end if;"
        return t.replace(old, 'null;')
    if main(stale): raise SystemExit('Planted stale-right-edge fault not detected')
    print('Planted stale-right-edge fault rejected')
