#!/usr/bin/env python3
"""prepare --pal-line-rate: line and frame length of the pinned scanout.

The pinned gpu_videoout_async (converted as for run_video_config_pipeline),
unchanged and with prepare_pal_line_rate.patch, runs 320x240 NTSC and 320x288
PAL progressive frames. Counted in video clocks: hsync rising edge to rising
edge, and HS per VS. Expected: NTSC 3413 x 263 either way; PAL 3406 x 314
unchanged and 3437 x 314 patched, i.e. 49.75 Hz at the Pocket's 53.693166 MHz
(PAL console: 3406 x 314 at 53.203425 MHz = 49.75 Hz). The pixels per line
must not change.
"""
import re
import subprocess
import tempfile
from pathlib import Path

from prepare_pal_line_rate import patch
from prepare_video_config_sim import prepare
from tool_paths import ROOT, find_tool


def bench(prepared):
    source = Path(prepared['original']).read_text()
    header = source[source.index('module original__video_original'):].split(');', 1)[0]
    ports = re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\\[^\s,]+|[A-Za-z_]\w*)', header)
    clean = lambda n: re.sub(r'\W', '_', n.lstrip('\\'))
    inp = lambda name: 'i_'+clean('videoout_settings_1x['+name+']')
    out = lambda name: 'o_'+clean('videoout_out['+name+']')
    text = '`timescale 1ns/1ps\nmodule tb_pal_line;\n'
    for direction, width, name in ports:
        short = name.lstrip('\\')
        if direction == 'input' and short not in ('clk1x', 'clk2x', 'clkvid', 'videoout_pixelRead', 'videoout_pixelRead2'):
            text += f'reg {width}i_{clean(name)}={1 if short in ("ce_1x", "reset_1x") else 0};\n'
        elif direction == 'output':
            text += f'wire {width}o_{clean(name)};\n'
    text += ('reg clk1x=0,clk2x=0,clkvid=0;\nalways #14.76255 clk1x=~clk1x;\nalways #7.381275 clk2x=~clk2x;\n'
             'always #9.31219 clkvid=~clkvid;\n')
    conns = []
    for direction, width, name in ports:
        short = name.lstrip('\\')
        value = ('o_'+clean(name) if direction == 'output' else short if short in ('clk1x', 'clk2x', 'clkvid')
                 else "16'h7fff" if short in ('videoout_pixelRead', 'videoout_pixelRead2') else 'i_'+clean(name))
        conns.append('.'+name+(' ' if name.startswith('\\') else '')+'('+value+')')
    text += 'original__video_original dut ('+',\n'.join(conns)+');\n'
    hs, vs, hb, vb, ce = out('hsync'), out('vsync'), out('hblank'), out('vblank'), out('ce')
    text += f'''
 integer clocks=0,period=0,hs_n=0,frames=0,px=0,line_px=0;reg hs_d=0,vs_d=0,hb_d=1;
 integer got_period[0:3],got_lines[0:3],got_px[0:3];
 always @(posedge clkvid) begin
  hs_d<={hs};vs_d<={vs};hb_d<={hb};clocks<=clocks+1;
  if(!{hb} && !{vb} && {ce}) px<=px+1;
  if({hb} && !hb_d) begin if(px!=0) line_px<=px;px<=0;end
  if({hs} && !hs_d) begin period<=clocks;clocks<=1;hs_n<=hs_n+1;end
  if({vs} && !vs_d) begin if(frames<4) begin got_period[frames]=period;got_lines[frames]=hs_n;got_px[frames]=line_px;end
   frames<=frames+1;hs_n<=({hs} && !hs_d) ? 1 : 0;end   // vsync rises with an hsync
 end
 task automatic run(input bit pal);
  @(negedge clk1x);i_reset_1x=1;
'''
    for name, value in [('GPUSTAT_VerRes', '0'), ('GPUSTAT_VertInterlace', '0'), ('GPUSTAT_PalVideoMode', 'pal'),
                        ('GPUSTAT_HorRes2', '0'), ('GPUSTAT_HorRes1', "2'd1"), ('GPUSTAT_ColorDepth24', '0'),
                        ('GPUSTAT_DisplayDisable', '0'), ('vramRange', "19'h0"), ('hDisplayRange', "{12'd3168,12'd608}"),
                        ('vDisplayRange', "pal ? {10'd307,10'd19} : {10'd256,10'd16}"), ('vCrop', '0'), ('hCrop', '0'),
                        ('dither24', '0'), ('render24', '0'), ('pal60', '0'), ('syncInterlace', '0'), ('rotate180', '0'),
                        ('fixedVBlank', '0')]:
        text += f'  {inp(name)}={value};\n'
    text += '''  repeat(40) @(negedge clk1x);i_reset_1x=0;
  frames=0;
  wait(frames==4);
  $display("RUN pal=%0d clocks=%0d lines=%0d pixels=%0d",pal,got_period[3],got_lines[3],got_px[3]);
 endtask
 initial begin
  run(0);run(1);
  $finish;
 end
endmodule
'''
    return text


def simulate(work, edit):
    prepared = prepare(work/'converted', edit)
    tb = work/'tb_pal_line.sv'; tb.write_text(bench(prepared))
    subprocess.run([find_tool('verilator'), '--binary', '--timing', '-O2', '-j', '4', '-Wno-fatal', '--top-module', 'tb_pal_line',
                    '--Mdir', str(work/'obj'), str(tb), prepared['original'], prepared['memory_models']],
                   cwd=work, check=True, capture_output=True, timeout=900)
    run = subprocess.run([str(work/'obj/Vtb_pal_line')], cwd=work, capture_output=True, text=True, timeout=1800).stdout
    print(run.strip())
    return {int(m[0]): tuple(map(int, m[1:])) for m in re.findall(r'RUN pal=(\d) clocks=(\d+) lines=(\d+) pixels=(\d+)', run)}


def main():
    parent = ROOT/'build/sim/pal-line-rate'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    (work/'base').mkdir(); (work/'patched').mkdir()
    base = simulate(work/'base', None); patched = simulate(work/'patched', patch)
    expect_base = {0: (3413, 263, 320), 1: (3406, 314, 320)}
    expect_patched = {0: (3413, 263, 320), 1: (3437, 314, 320)}
    if base != expect_base: raise SystemExit(f'unpatched: got {base}, expected {expect_base}')
    if patched != expect_patched: raise SystemExit(f'patched: got {patched}, expected {expect_patched}')
    hz = 53.693166e6/(3437*314)
    print(f'PASS pal line rate: PAL {hz:.3f} Hz at 53.693166 MHz (console {53.203425e6/(3406*314):.3f} Hz)')


if __name__ == '__main__':
    main()
