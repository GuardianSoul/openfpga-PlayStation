#!/usr/bin/env python3
"""Cycle-lockstep check of gpu-dy-ram (prepare_gpu_dy_ram.py) on the release GPU.

Both sides are the release GPU sources of the F-nopool base (gpu-sources/,
scripts/derive_gpu_sources.py output) with the MiSTer GPU helpers the release
project compiles. gpu_poly.vhd gets area_reg_text on both sides (as in the
release build) and dy_ram_text on the candidate only. Only entity gpu is
synthesized (GHDL --synth with the GHDL-compatibility transforms that
prepare_system_verilator applies to every oracle build; RAMs are opaque
ramip entities modelled in sim/reference/mister_memory_models.sv). The two
GPUs run side by side in the run_gpu_line_share oracle bench, each with its
own MiSTer DDR model: every public GPU output is compared on every clk2x
edge, GPUREAD on every CPU read, and both VRAMs after every program.

Traffic: the oracle programs (fills, lines, rectangles, Gouraud and textured
polygons, CPU readbacks), then Gouraud triangles and quads (GP0 0x30/0x38),
and textured flat, raw and shaded triangles and quads (0x24/0x25/0x2C/0x2D/
0x34/0x3C) with varied UVs, CLUTs and texture depths over a pseudo-random
VRAM: axis-aligned 1:1 quads (dxU=dyV=4096, dyU=dxV=0), sheared 1:1
triangles (dxU=4096, dyU!=0, dxV=0), one-axis 1:1, scaled and rotated cases,
each under textureFilter 0..3 and textureFilter2DOff 0/1. Counters report how
many polygons took CALCCOLOR4/CALCTEXTURE4 and how often each side of the two
flag decisions was hit. Planted faults (G written to the wrong entry, a U
flag that is never updated) must fail.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import time
from pathlib import Path

import run_gpu_line_share as share
from ghdl_verilog import normalize_generated_verilog
from prepare_gpu_area_reg import area_reg_text
from prepare_gpu_dy_ram import dy_ram_text
from prepare_resource_targets import UPSTREAM, digest, hardware_text
from prepare_system_sim import compatible_async_video_source
from prepare_system_verilator import gpu_transcode_source, split_divider_ports
from tool_paths import ROOT, find_tool

# The release base project (PSX_FIT_BASE, or build/claude-fit/F-nopool of this
# checkout or of the main checkout when this is a worktree under build/).
BASE = Path(os.environ['PSX_FIT_BASE']) if os.environ.get('PSX_FIT_BASE') else next(
    (p for p in (ROOT / 'build/claude-fit/F-nopool', ROOT.parent / 'claude-fit/F-nopool') if p.exists()),
    ROOT / 'build/claude-fit/F-nopool')
RELEASE = ('gpu.vhd', 'gpu_poly.vhd', 'gpu_line.vhd', 'gpu_pixelpipeline.vhd', 'gpu_rect.vhd', 'gpu_fillVram.vhd',
           'gpu_vram2cpu.vhd', 'gpu_vram2vram.vhd', 'psx_gpu_read_math.vhd')
# (library, file) in the release apf_top.qsf order; release names come from BASE.
SOURCES = [('ramip', 'sim:RamMLAB.vhd'), ('ramip', 'sim:dpram.vhd'),
           ('mem', 'SyncFifo.vhd'), ('mem', 'SyncFifoFallThrough.vhd'), ('mem', 'SyncFifoFallThroughMLAB.vhd'),
           ('work', 'export.vhd'), ('work', 'divider.vhd'), ('work', 'pGPU.vhd'), ('work', 'mul32u.vhd'),
           ('work', 'mul9s.vhd'), ('work', 'gpu_fillVram.vhd'), ('work', 'gpu_cpu2vram.vhd'),
           ('work', 'gpu_vram2vram.vhd'), ('work', 'gpu_vram2cpu.vhd'), ('work', 'gpu_line.vhd'),
           ('work', 'gpu_rect.vhd'), ('work', 'gpu_poly.vhd'), ('work', 'gpu_pixelpipeline.vhd'),
           ('work', 'gpu_overlay.vhd'), ('work', 'gpu_dither.vhd'), ('work', 'gpu_videoout_async.vhd'),
           ('work', 'gpu_videoout_sync.vhd'), ('work', 'gpu_crosshair.vhd'), ('work', 'justifier_sensor.vhd'),
           ('work', 'gpu_videoout.vhd'), ('work', 'psx_gpu_read_math.vhd'), ('work', 'gpu.vhd')]


def wrong_entry(t):
    old = "dyWe <= '1'; dyWaddr <= 1; dyWdata"
    if t.count(old) != 1: raise ValueError('wrong_entry marker missing')
    return t.replace(old, "dyWe <= '1'; dyWaddr <= 2; dyWdata")


def stale_u(t):
    old = "if (div2.quotient(31 downto 0) = 0) then dyU_zero <= '1'; else dyU_zero <= '0'; end if;"
    if t.count(old) != 1: raise ValueError('stale_u marker missing')
    return t.replace(old, 'null;')


FAULTS = {'wrong_entry': wrong_entry, 'stale_u': stale_u}
WORKAROUNDS = set()


def source_text(name, candidate, fault):
    """One GPU-only source, with the oracle's GHDL-compatibility transforms."""
    if name.startswith('sim:'):
        return (UPSTREAM / 'sim/system/src/mem' / name[4:]).read_text(), str(UPSTREAM / 'sim/system/src/mem' / name[4:])
    if name in RELEASE:
        path = BASE / 'gpu-sources' / name
    elif name == 'gpu_videoout_async.vhd':
        path = None
    else:
        path = UPSTREAM / 'rtl' / name
    if path is None:
        text = None
    else:
        text = path.read_text()
    if name == 'gpu_poly.vhd':
        text = area_reg_text(text)
        if candidate:
            text = dy_ram_text(text)
            # The release poly (gpu-vertex-ram) already declares the ramstyle
            # attribute; dy_ram_text declares it again, which VHDL rejects.
            # Drop the repeat (no logic change) until the transform does.
            declaration = '   attribute ramstyle : string;\n'
            if text.count(declaration) == 2:
                first = text.index(declaration) + len(declaration)
                text = text[:first] + text[first:].replace(declaration, '', 1)
                WORKAROUNDS.add('duplicate ramstyle attribute declaration removed from dy_ram_text output')
        if fault: text = FAULTS[fault](text)
    return text, str(path) if path else None


def hardware(name, text):
    text = hardware_text(text)
    if name == 'export.vhd':
        text = text.split('end package;')[0]
        marker = '   signal regs                   : tExportRegs;'
        if text.count(marker) != 1: raise ValueError('Pinned debug export package changed')
        text = text.replace(marker, '') + 'end package;\n'
    if name == 'gpu_videoout.vhd':
        for expression, count in {'to_integer(videoout_request_clkvid.lineDisp)': 9,
                                  'to_integer(Gun1Y_screen)': 2, 'to_integer(Gun2Y_screen)': 2}.items():
            if text.count(expression) != count: raise ValueError('Pinned GPU scalar associations changed')
            text = text.replace(expression, "integer'(" + expression + ")")
        text, count = re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                              r"\1integer'(videoout_request_clkvid.xpos),", text)
        if count != 5: raise ValueError('Pinned GPU overlay association changed')
    if name == 'gpu_poly.vhd':
        # gpu_transcode_source's polygon edits; the release poly (poly share)
        # has two 0x100000000 constants instead of the pinned three.
        if text.count('abs(') != 4 or text.count('x"100000000"') != 2: raise ValueError('Release polygon arithmetic changed')
        text = text.replace('abs(', 'magnitude(')
        function = '''   function magnitude(value : integer) return integer is
   begin
      if value < 0 then return -value; else return value; end if;
   end function;

'''
        # The release poly's architecture begin has no trailing space.
        if text.count('\nbegin\n') != 1 or text.count('begin \n'): raise ValueError('Release polygon architecture marker changed')
        text = text.replace('\nbegin\n', '\nbegin \n')
        text = text.replace('begin \n', function + 'begin \n', 1)
        text = text.replace('x"100000000"', 'signed\'("000000000" & x"100000000")')
        marker = 'to_integer(xStart(43 downto 32)) > drawingAreaRight'
        if text.count(marker) != 1: raise ValueError('Release polygon span-start comparison changed')
        text = text.replace(marker, 'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))')
        text = split_divider_ports(text)
    elif name in ('gpu_line.vhd', 'gpu.vhd'):
        text = gpu_transcode_source(name[:-4], text, True)
    text, count = re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram(?:_dif)?)\b', r'entity ramip.\1', text, flags=re.I)
    if count: text = 'library ramip;\n' + text
    return text


def transcode(directory, candidate, fault, ghdl, log):
    directory.mkdir(parents=True, exist_ok=True)
    sources = []
    for library, name in SOURCES:
        if name == 'gpu_videoout_async.vhd':
            video = compatible_async_video_source(directory)
            text, origin = Path(video['path']).read_text(), video['original']
        else:
            text, origin = source_text(name, candidate, fault)
        if library != 'ramip': text = hardware(name, text)
        path = directory / 'hardware' / library / name.removeprefix('sim:'); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        sources.append({'library': library, 'path': str(path), 'sha256': digest(path), 'origin': origin,
                        'origin_sha256': digest(Path(origin)) if origin else None})
    for source in sources:
        subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', f'--work={source["library"]}', source['path']],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
    output = directory / 'system.v'
    with output.open('w') as file:
        subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog', 'gpu'],
                       cwd=directory, stdout=file, stderr=log, timeout=600, check=True)
    normalize_generated_verilog(output)
    text = output.read_text(); names = re.findall(r'^module (\w+)', text, re.M)
    prefix = 'shared__' if candidate else 'original__'
    expression = re.compile(r'\b(' + '|'.join(sorted(names, key=len, reverse=True)) + r')\b')
    renamed = directory / 'renamed.v'; renamed.write_text(expression.sub(lambda m: prefix + m.group(), text))
    gpu = re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n', text, re.M)
    if len(gpu) != 1: raise ValueError('Expected one GPU module')
    return sources, renamed, prefix + gpu[0]


# Module-level coverage counters. The candidate's dyWe/dyWaddr pulse once
# per CALCCOLOR4 (entry 0) and CALCTEXTURE4 (entry 4); at the V write the
# baseline's dyU/dxV/dxU/dyV registers already hold the final gradients.
COVERAGE = '''integer cov_color4=0,cov_texture4=0,cov_u0v0=0,cov_u0v0_4096=0,cov_u0v0_not4096=0,cov_shear4096=0;
integer cov_dxu4096=0,cov_dyv4096=0,cov_rotated=0,cov_decide=0,cov_decide_skip=0,cov_decide_shear=0;
reg cov_prev_c=0,cov_prev_t=0;
always @(posedge clk2x) begin
 cov_prev_c<=new_gpu.igpu_poly.dywe && new_gpu.igpu_poly.dywaddr==0;
 cov_prev_t<=new_gpu.igpu_poly.dywe && new_gpu.igpu_poly.dywaddr==4;
 if(new_gpu.igpu_poly.dywe && new_gpu.igpu_poly.dywaddr==0 && !cov_prev_c) cov_color4++;
 if(new_gpu.igpu_poly.dywe && new_gpu.igpu_poly.dywaddr==4 && !cov_prev_t) begin : texture4
  reg u0,v0,x4096,y4096;
  cov_texture4++;
  u0=old_gpu.igpu_poly.dyu==0;v0=old_gpu.igpu_poly.dxv==0;
  x4096=old_gpu.igpu_poly.dxu==4096;y4096=new_gpu.igpu_poly.dywdata==4096;
  if(x4096) cov_dxu4096++;
  if(y4096) cov_dyv4096++;
  if(!v0) cov_rotated++;
  if(u0 && v0) begin cov_u0v0++;if(x4096 || y4096) cov_u0v0_4096++;else cov_u0v0_not4096++;end
  if(!u0 && v0 && (x4096 || y4096)) cov_shear4096++;
  if(texturefilter2doff && texturefilter!=0) begin
   cov_decide++;
   if(u0 && v0 && (x4096 || y4096)) cov_decide_skip++;
   if(!u0 && v0 && (x4096 || y4096)) cov_decide_shear++;
  end
 end
end
task automatic tpoly(input integer shaded,quad,raw,semi,input integer x0,y0,u0,v0,x1,y1,u1,v1,x2,y2,u2,v2,x3,y3,u3,v3,
  input integer clut,page,input [23:0] c0,c1,c2,c3);
 write_gpu(0,((32'h24|(shaded<<4)|(quad<<3)|(semi<<1)|raw)<<24)|c0);
 write_gpu(0,((y0&65535)<<16)|(x0&65535));write_gpu(0,((clut&65535)<<16)|((v0&255)<<8)|(u0&255));
 if(shaded) write_gpu(0,c1);
 write_gpu(0,((y1&65535)<<16)|(x1&65535));write_gpu(0,((page&65535)<<16)|((v1&255)<<8)|(u1&255));
 if(shaded) write_gpu(0,c2);
 write_gpu(0,((y2&65535)<<16)|(x2&65535));write_gpu(0,((v2&255)<<8)|(u2&255));
 if(quad) begin
  if(shaded) write_gpu(0,c3);
  write_gpu(0,((y3&65535)<<16)|(x3&65535));write_gpu(0,((v3&255)<<8)|(u3&255));
 end
endtask
'''

TRAFFIC = ''' // ---- gpu-dy-ram traffic ----
 pauses=0;ce=1;
 write_gpu(4,32'h08000000);write_gpu(0,32'he1000000);write_gpu(0,32'he2000000);
 write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
 idle();
 // Identical pseudo-random VRAM (texels, CLUTs, mask bits) in both memories.
 for(integer i=0;i<262144;i++) begin : dy_fill
  reg [31:0] value;
  value=i*32'h9e3779b1;value=value^(value>>15)^32'h5bd1e995;value=value*32'h85ebca6b;value=value^(value>>13);
  old_vram.data[i]=value;new_vram.data[i]=value;
 end
 write_gpu(0,32'h01000000);idle();compare_vram();
 for(integer fcfg=0;fcfg<8;fcfg++) begin : dy_config
  texturefilter=fcfg%4;texturefilter2doff=fcfg/4;texturefilterstrength=fcfg%3;
  for(integer trial=0;trial<10;trial++) begin : dy_trial
   integer x,y,w,h,u,v,s,depth,page,clut,k,semi;
   reg [23:0] c0,c1,c2,c3;
   k=fcfg*10+trial;
   x=16+(k*67)%900;y=8+(k*41)%440;w=4+(k*13)%44;h=4+(k*7)%44;
   depth=k%3;page=((k*5)%16)|(((k/3)%2)<<4)|((k%4)<<5)|(depth<<7);
   clut=(((k*11)%64))|((256+(k*37)%250)<<6);
   u=(k*29)%(256-w-1);v=(k*53)%(256-h-1);s=1+(k%5);semi=(k%7==3);
   c0=(k*17813+12345)&24'hffffff;c1=(k*77569+999)&24'hffffff;c2=(k*3571+4242)&24'hffffff;c3=(k*9973+77)&24'hffffff;
   write_gpu(0,32'he1000000|page|((k%2)<<9));
   // Axis-aligned 1:1 quads, raw and modulated, then shaded.
   tpoly(0,1,k%2,semi,x,y,u,v,x+w,y,u+w,v,x,y+h,u,v+h,x+w,y+h,u+w,v+h,clut,page,24'h808080,0,0,0);
   tpoly(1,1,0,0,x+w+2,y,u,v,x+2*w+2,y,u+w,v,x+w+2,y+h,u,v+h,x+2*w+2,y+h,u+w,v+h,clut,page,c0,c1,c2,c3);
   // 1:1 triangle (0x24/0x34) and sheared 1:1 (dxU=4096, dyU!=0, dxV=0).
   tpoly(k%2,0,0,0,x,y+h+2,u,v,x+w,y+h+2,u+w,v,x,y+2*h+2,u,v+h,0,0,0,0,clut,page,c0,c1,c2,c3);
   tpoly(k%2,0,0,0,x+w+2,y+h+2,u,v,x+2*w+2,y+h+2,u+w,v,x+w+2,y+2*h+2,u+s,v+h,0,0,0,0,clut,page,c1,c2,c3,c0);
   tpoly(0,1,1,0,x+2*w+4,y,u,v,x+3*w+4,y,u+w,v,x+2*w+4,y+h,u+s,v+h,x+3*w+4,y+h,u+w+s,v+h,clut,page,24'h808080,0,0,0);
   // One axis 1:1 (U only, V only), both scaled, rotated.
   tpoly(0,1,0,0,x,y+2*h+4,u,v,x+w,y+2*h+4,u+w,v,x,y+2*h+4+h,u,v+h/2,x+w,y+3*h+4,u+w,v+h/2,clut,page,c2,0,0,0);
   tpoly(1,1,0,0,x+w+2,y+2*h+4,u,v,x+2*w+2,y+2*h+4,u+w/2,v,x+w+2,y+3*h+4,u,v+h,x+2*w+2,y+3*h+4,u+w/2,v+h,clut,page,c0,c2,c1,c3);
   tpoly(k%2,0,0,0,x+2*w+4,y+2*h+4,u,v,x+3*w+4,y+2*h+4,u+w/2,v,x+2*w+4,y+3*h+4,u,v+h/2,0,0,0,0,clut,page,c3,c1,c0,c2);
   tpoly(1,0,0,semi,x,y+3*h+6,u+3,v,x+w,y+3*h+8+(k%9),u+w,v+5,x+(k%5),y+4*h+6,u,v+h,0,0,0,0,clut,page,c0,c1,c2,c3);
   tpoly(1,1,0,0,x+w+2,y+3*h+6,u,v+2,x+2*w+2,y+3*h+6,u+w,v,x+w+2,y+4*h+6,u+2,v+h,x+2*w+2+(k%3),y+4*h+6,u+w,v+h+1,clut,page,c3,c2,c1,c0);
   // Gouraud triangle and quad (0x30/0x38), dithered on odd k.
   write_gpu(0,32'h30000000|c0);write_gpu(0,((y+4*h+8)<<16)|x);write_gpu(0,c1);write_gpu(0,((y+4*h+8+(k%11))<<16)|(x+w));
   write_gpu(0,c2);write_gpu(0,((y+5*h+8)<<16)|(x+(k%7)));
   write_gpu(0,32'h38000000|c3);write_gpu(0,((y+4*h+8)<<16)|(x+w+2));write_gpu(0,c2);write_gpu(0,((y+4*h+8)<<16)|(x+2*w+2));
   write_gpu(0,c1);write_gpu(0,((y+5*h+8)<<16)|(x+w+2+(k%4)));write_gpu(0,c0);write_gpu(0,((y+5*h+8)<<16)|(x+2*w+2));
   idle();compare_vram();
  end
 end
 texturefilter=0;texturefilter2doff=0;texturefilterstrength=0;
 $display("COVERAGE calccolor4=%0d calctexture4=%0d dyU0_dxV0=%0d dyU0_dxV0_4096=%0d dyU0_dxV0_not4096=%0d sheared_4096=%0d dxU4096=%0d dyV4096=%0d dxV_nonzero=%0d decide2DOff=%0d decide_skip=%0d decide_sheared=%0d",
  cov_color4,cov_texture4,cov_u0v0,cov_u0v0_4096,cov_u0v0_not4096,cov_shear4096,cov_dxu4096,cov_dyv4096,cov_rotated,cov_decide,cov_decide_skip,cov_decide_shear);
'''


def make_bench(directory, oldname, newname):
    top = share.bench(directory, oldname, newname, False, True, True)
    text = top.read_text().replace('PASS GPU combined adaptations', 'PASS GPU dy ram')
    # The release GPU has no divider pool: abort a few cycles into setup instead.
    old = 'wait(new_gpu.pool_starts!=0);'
    if text.count(old) != 1: raise ValueError('Bench pool marker changed')
    text = text.replace(old, 'repeat(12) @(negedge clk2x);')
    # The pixel trace reads gpu_poly's dyR, which the candidate no longer has.
    start, end = text.index('integer old_trace,new_trace;'), text.index('integer checks=0;')
    text = text[:start] + text[end:]
    text, count = re.subn(r'\$fatal\(1,"GPU timeout program=[^;]*\);', '$fatal(1,"GPU timeout program=%d",programs);', text)
    if count != 1: raise ValueError('Bench timeout marker changed')
    text = text.replace('repeat(10000000) @(negedge clk1x)', 'repeat(400000000) @(negedge clk1x)')
    marker = 'initial begin\n repeat(100)'
    if text.count(marker) != 1: raise ValueError('Bench initial marker changed')
    text = text.replace(marker, COVERAGE + marker)
    marker = ' $display("PASS GPU cycle outputs ticks='
    if text.count(marker) != 1: raise ValueError('Bench cycle marker changed')
    top.write_text(text.replace(marker, TRAFFIC + marker))
    return top


def run(directory, baseline, fault, ghdl, verilator, jobs):
    """Build the candidate (or a planted fault) and run it against the baseline."""
    started = time.time()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'checks.log').open('w') as log:
        sources, newverilog, newname = transcode(directory / 'adapted', True, fault, ghdl, log)
        oldsources, oldverilog, oldname = baseline
        original = directory / 'original'
        if original.resolve() != Path(oldverilog).parent.resolve():
            if original.is_symlink() or original.exists(): original.unlink()
            original.symlink_to(Path(oldverilog).parent.resolve())
        top = make_bench(directory, oldname, newname)
        subprocess.run([verilator, '--binary', '--timing', '-O3', '-j', str(jobs), '-Wno-fatal', '--output-split', '20000',
                        '--top-module', 'tb_gpu_share', '--Mdir', str(directory / 'obj'), str(oldverilog), str(newverilog),
                        str(ROOT / 'sim/reference/mister_memory_models.sv'), str(top)],
                       cwd=directory, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=True)
    result = subprocess.run([str(directory / 'obj/Vtb_gpu_share')], cwd=directory, capture_output=True, text=True,
                            timeout=7200)
    output = result.stdout + result.stderr; (directory / 'run.log').write_text(output)
    cycle = re.search(r'PASS GPU cycle outputs ticks=\s*([0-9]+)', output)
    final = re.search(r'PASS GPU dy ram oracle=\s*(\d+) programs=\s*(\d+) memory_checks=\s*(\d+)', output)
    coverage = re.search(r'^COVERAGE (.*)$', output, re.M)
    readback = re.search(r'PASS GPU readback checks=\s*(\d+)', output)
    passed = bool(result.returncode == 0 and cycle and int(cycle[1]) > 0 and final and readback)
    mismatch = [l for l in output.splitlines() if 'mismatch' in l.lower() or 'fatal' in l.lower() or 'timeout' in l.lower()]
    return {'fault': fault, 'passed': passed, 'exit_code': result.returncode, 'seconds': round(time.time() - started, 1),
            'cycle_ticks': int(cycle[1]) if cycle else None,
            'oracle_checks': int(final[1]) if final else None, 'programs': int(final[2]) if final else None,
            'memory_checks': int(final[3]) if final else None, 'readback_checks': int(readback[1]) if readback else None,
            'coverage': dict(kv.split('=') for kv in coverage[1].split()) if coverage else None,
            'first_failure': mismatch[:3], 'candidate_sources': sources,
            'candidate_verilog_sha256': digest(newverilog), 'bench_sha256': digest(top)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true', help='Skip the planted-fault runs')
    args = parser.parse_args()
    if not (BASE / 'gpu-sources/gpu_poly.vhd').exists(): raise SystemExit(f'Release base GPU sources not found in {BASE}')
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL and Verilator required')
    started = time.time()
    directory = ROOT / 'build/sim/gpu-dy-ram'; directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: run(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    main_result, faults = results[0], results[1:]
    for result in results:
        label = result['fault'] or 'candidate'
        print(f"{label}: {'PASS' if result['passed'] else 'FAIL'} exit={result['exit_code']} ticks={result['cycle_ticks']} "
              f"programs={result['programs']} seconds={result['seconds']}")
        if result['coverage']: print('  coverage', ' '.join(f'{k}={v}' for k, v in result['coverage'].items()))
        if result['first_failure']: print('  ' + '\n  '.join(result['first_failure']))
    cov = main_result['coverage'] or {}
    covered = all(int(cov.get(k, 0)) > 0 for k in ('calccolor4', 'calctexture4', 'dyU0_dxV0_4096', 'dyU0_dxV0_not4096',
                                                    'sheared_4096', 'decide_skip', 'decide_sheared', 'dxV_nonzero'))
    rejected = {r['fault']: not r['passed'] for r in faults}
    passed = main_result['passed'] and covered and all(rejected.values()) and (args.no_faults or len(rejected) == len(FAULTS))
    report = {'passed': passed, 'release_base': str(BASE), 'seconds': round(time.time() - started, 1),
              'baseline': 'area_reg_text(release gpu_poly), all other release GPU sources unchanged',
              'candidate_description': 'dy_ram_text(area_reg_text(release gpu_poly))',
              'baseline_sources': baseline[0], 'baseline_verilog_sha256': digest(baseline[1]),
              'coverage_complete': covered, 'transform_workarounds': sorted(WORKAROUNDS), 'candidate': main_result, 'planted_faults_rejected': rejected,
              'fault_runs': faults, 'compared': 'every public GPU output on every clk2x edge, GPUREAD words, '
              'both VRAMs after every program', 'clock_enable_pause_scope': 'Only after both GPU SS_Idle signals',
              'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for workaround in sorted(WORKAROUNDS): print('WORKAROUND (transform defect):', workaround)
    for fault, ok in rejected.items(): print(f'Planted fault {"rejected" if ok else "NOT DETECTED"}: {fault}')
    print(f"{'PASS' if passed else 'FAIL'} GPU dy RAM lockstep ({report['seconds']} s); report {directory / 'report.json'}")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
