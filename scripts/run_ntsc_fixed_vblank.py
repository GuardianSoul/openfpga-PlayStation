#!/usr/bin/env python3
"""prepare --ntsc-fixed-vblank: active lines per frame of the pinned scanout.

The pinned gpu_videoout_async (converted as for run_video_config_pipeline)
runs NTSC 320x240 frames with a full display range (lines 16-256) and the
236-line range of CTR's start-up logos (16-252), with fixedVBlank as the
release ties it (0) and as the flag sets it for NTSC (1). Counted per frame:
lines with active pixels (not hblank/vblank) and how many of them carry any
nonzero pixel. Expected: 240/236 active lines without the flag (the APF
scaler keeps stale lines below a short picture), 240/240 with it, the four
extra lines all black, and PAL left at its own range by the gpu.vhd edit.
"""
import re
import subprocess
import tempfile
from pathlib import Path

from prepare_video_config_sim import prepare
from tool_paths import ROOT, find_tool


def main():
    parent = ROOT/'build/sim/ntsc-fixed-vblank'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    converted = work/'converted'; prepared = prepare(converted)
    source = Path(prepared['original']).read_text()
    header = source[source.index('module original__video_original'):].split(');', 1)[0]
    ports = re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\\[^\s,]+|[A-Za-z_]\w*)', header)
    clean = lambda n: re.sub(r'\W', '_', n.lstrip('\\'))
    inp = lambda name: 'i_'+clean('videoout_settings_1x['+name+']')
    out = lambda name: 'o_'+clean('videoout_out['+name+']')
    text = '`timescale 1ns/1ps\nmodule tb_fixed;\n'
    for direction, width, name in ports:
        short = name.lstrip('\\')
        if direction == 'input' and short not in ('clk1x', 'clk2x', 'clkvid', 'videoout_pixelRead', 'videoout_pixelRead2'):
            text += f'reg {width}i_{clean(name)}={1 if short in ("ce_1x", "reset_1x") else 0};\n'
        elif direction == 'output':
            text += f'wire {width}o_{clean(name)};\n'
    text += ('reg clk1x=0,clk2x=0,clkvid=0;\nalways #14.76255 clk1x=~clk1x;\nalways #7.381275 clk2x=~clk2x;\n'
             'always #9.31219 clkvid=~clkvid;\n')
    # Bright test picture: every VRAM read returns white, so black lines can
    # only come from the scanout itself.
    conns = []
    for direction, width, name in ports:
        short = name.lstrip('\\')
        value = ('o_'+clean(name) if direction == 'output' else short if short in ('clk1x', 'clk2x', 'clkvid')
                 else "16'h7fff" if short in ('videoout_pixelRead', 'videoout_pixelRead2') else 'i_'+clean(name))
        conns.append('.'+name+(' ' if name.startswith('\\') else '')+'('+value+')')
    text += 'original__video_original dut ('+',\n'.join(conns)+');\n'
    active, vblank, hblank, ce, red = out('vblank'), out('vblank'), out('hblank'), out('ce'), out('r')
    text += f'''
 integer lines=0,lit=0,frames=0;reg line_active=0,line_lit=0,vb_d=1,hb_d=1;
 integer got_lines[0:3],got_lit[0:3];
 always @(posedge clkvid) begin
  hb_d<={hblank};vb_d<={vblank};
  if(!{hblank} && !{vblank} && {ce}) begin line_active<=1;if({red}!=0) line_lit<=1;end
  if({hblank} && !hb_d) begin if(line_active) lines<=lines+1;if(line_lit) lit<=lit+1;line_active<=0;line_lit<=0;end
  if({vblank} && !vb_d) begin if(frames<4) begin got_lines[frames]=lines;got_lit[frames]=lit;end frames<=frames+1;lines<=0;lit<=0;end
 end
 task automatic run(input bit pal,input bit fixed,input [9:0] first,input [9:0] last);
  @(negedge clk1x);i_reset_1x=1;
'''
    for name, value in [('GPUSTAT_VerRes', '0'), ('GPUSTAT_VertInterlace', '0'), ('GPUSTAT_PalVideoMode', 'pal'),
                        ('GPUSTAT_HorRes2', '0'), ('GPUSTAT_HorRes1', "2'd1"), ('GPUSTAT_ColorDepth24', '0'),
                        ('GPUSTAT_DisplayDisable', '0'), ('vramRange', "19'h0"), ('hDisplayRange', "{12'd3168,12'd608}"),
                        ('vDisplayRange', '{last,first}'), ('vCrop', '0'), ('hCrop', '0'), ('dither24', '0'),
                        ('render24', '0'), ('pal60', '0'), ('syncInterlace', '0'), ('rotate180', '0'),
                        ('fixedVBlank', 'fixed && !pal')]:   # the gpu.vhd edit: fixedVBlank and not PAL
        text += f'  {inp(name)}={value};\n'
    text += '''  repeat(40) @(negedge clk1x);i_reset_1x=0;
  wait(frames==0 || 1);frames=0;
  wait(frames==4);
  $display("RUN pal=%0d fixed=%0d range=%0d-%0d lines=%0d lit=%0d",pal,fixed,first,last,got_lines[3],got_lit[3]);
 endtask
 initial begin
  run(0,0,16,256);run(0,0,16,252);run(0,1,16,256);run(0,1,16,252);run(1,1,48,288);
  $finish;
 end
endmodule
'''
    bench = work/'tb_fixed.sv'; bench.write_text(text)
    sources = [str(bench), prepared['original'], prepared['memory_models']]
    subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O2', '-j', '4', '-Wno-fatal', '--top-module', 'tb_fixed',
                    '--Mdir', str(work/'obj'), *sources], cwd=work, check=True, capture_output=True, timeout=900)
    run = subprocess.run([str(work/'obj/Vtb_fixed')], cwd=work, capture_output=True, text=True, timeout=1800).stdout
    print(run.strip())
    got = {tuple(map(int, m[:4])): (int(m[4]), int(m[5])) for m in
           re.findall(r'RUN pal=(\d) fixed=(\d) range=(\d+)-(\d+) lines=(\d+) lit=(\d+)', run)}
    expect = {(0, 0, 16, 256): (240, 240), (0, 0, 16, 252): (236, 236), (0, 1, 16, 256): (240, 240),
              (0, 1, 16, 252): (240, 236)}
    for key, value in expect.items():
        if got.get(key) != value: raise SystemExit(f'{key}: got {got.get(key)}, expected {value}')
    if got.get((1, 1, 48, 288), (0, 0))[0] != 240: raise SystemExit(f'PAL range changed: {got.get((1, 1, 48, 288))}')
    print('PASS ntsc fixed vblank')


if __name__ == '__main__':
    main()
