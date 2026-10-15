#!/usr/bin/env python3
"""Compare polygon sharing pixels with the original GPU under its idle-pause contract."""
import json
import argparse
from pathlib import Path
import re
import subprocess

from prepare_system_verilator import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool
from ghdl_verilog import normalize_generated_verilog


def transcode(directory,adapted,ghdl,log,divider_pool=False,timing_prune=False,combined=False,coordinates=False,borrowed=False):
    selected=adapted or coordinates or borrowed
    combined=combined or coordinates or borrowed
    manifest=prepare(directory,gpu_line_share=selected and (combined or (not divider_pool and not timing_prune)),
        gpu_divider_pool=selected and (divider_pool or combined),
        gpu_timing_prune=selected and (timing_prune or combined));output=directory/'system.v'
    if (adapted and coordinates) or borrowed:
        from prepare_gpu_coordinates import registered_coordinate_source
        source=next(s for s in manifest['sources'] if Path(s['path']).name=='gpu_poly.vhd')
        copy=registered_coordinate_source(directory/'gpu-coordinates',source['path'])
        copy['transforms']=['Register negated coordinates during existing boundary setup']
        manifest['sources'][manifest['sources'].index(source)]=copy
        manifest['gpu_coordinates']=copy
    if adapted and borrowed:
        from prepare_gpu_borrowed import borrowed_text
        # gpu_rect passes through unchanged; variant tests hook it via borrowed_text.
        for name in ('gpu_poly.vhd','gpu_line.vhd','gpu.vhd','psx_gpu_divider_pool.vhd','gpu_rect.vhd'):
            source=next(s for s in manifest['sources'] if Path(s['path']).name==name)
            folder=directory/'gpu-borrowed';folder.mkdir(exist_ok=True);path=folder/name
            path.write_text(borrowed_text(name,Path(source['path']).read_text()))
            copy={**source,'path':str(path),'sha256':digest(path),
                  'selected':source['path'],'selected_sha256':source['sha256'],
                  'transforms':source.get('transforms',[])+['Producer-held divider operands']}
            manifest['sources'][manifest['sources'].index(source)]=copy
    for source in manifest['sources']:
        subprocess.run([ghdl,'-a','--std=08','-frelaxed',f'--work={source["library"]}',source['path']],
            cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=60,check=True)
    with output.open('w') as file:
        subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog',
            'psx_mister_psx_top_resource'],cwd=directory,stdout=file,stderr=log,timeout=120,check=True)
    manifest['verilog_normalization']=normalize_generated_verilog(output)
    text=output.read_text();names=re.findall(r'^module (\w+)',text,re.M)
    prefix='shared__' if adapted else 'original__'
    expression=re.compile(r'\b('+ '|'.join(sorted(names,key=len,reverse=True))+r')\b')
    copied=directory/'renamed.v';copied.write_text(expression.sub(lambda m:prefix+m.group(),text))
    gpu=re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n',text,re.M)
    if len(gpu)!=1:raise ValueError('Expected one GPU module')
    return manifest,copied,prefix+gpu[0]


def bench(directory,oldname,newname,divider_pool=False,timing_prune=False,combined=False):
    template=(ROOT/'sim/reference/tb_gpu_primitives.sv').read_text()
    declarations=re.findall(r'^logic (\[\d+:\d+\]\s+)?(\w+)=([01]);',template,re.M)
    # The template input list is the pinned entity declaration. Distinguish
    # outputs using the generated module header, then give each RAM handshake
    # its own model while sharing host commands and clock-enable pauses.
    verilog=(directory/'original/system.v').read_text();base=oldname.removeprefix('original__')
    header_start=re.search(r'^module '+re.escape(base)+r'\s*\n',verilog,re.M)
    if not header_start:raise ValueError('Expected the exact GPU module header')
    header=verilog[header_start.start():].split(');',1)[0]
    ports={name.lower():name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)',header)}
    outputs={name.lower() for name in re.findall(r'\boutput\s+(?:\[\d+:\d+\]\s+)?(\w+)',header)}
    memory_inputs={'vram_busy','vram_dout','vram_dout_ready'}
    shared_inputs={name for _,name,_ in declarations}-outputs-memory_inputs
    text='`timescale 1fs/1fs\nmodule tb_gpu_share;\n'
    for width,name,value in declarations:
        if name in shared_inputs:text+=f'logic {width}{name}={value};\n'
        else:text+=f'logic {width}old_{name}={value},new_{name}={value};\n'
    for prefix,module in (('old_',oldname),('new_',newname)):
        text+=module+' '+prefix+'gpu('+','.join('.'+ports[name]+'('+('' if name in shared_inputs else prefix)+name+')' for _,name,_ in declarations)+');\n'
    if timing_prune:
        # This specialization removes unused bookkeeping, so unlike arithmetic
        # sharing it must preserve every public output on each sampled cycle.
        text+='integer cycle_io_ticks=0;\nalways @(negedge clk2x) if(!reset && !ss_reset) begin\n'
        text+=' cycle_io_ticks++;\n'
        for name in sorted(outputs):
            text+=f' if(old_{name}!==new_{name}) $fatal(1,"GPU cycle output mismatch: {name}");\n'
        text+='end\n'
    text+='''always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #9312170 clkvid=~clkvid;
reg toggle=0,sampled=0;
reg pauses=0;
reg [31:0] random_state=32'h1b873593;
integer pause_ticks=0,total_ticks=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xindex<=sampled==toggle;end
always @(negedge clk1x) begin
 random_state=(random_state>>1)^((random_state&1) ? 32'hd0000001:0);
 if(pauses) begin ce=(random_state&7)!=0;total_ticks++;if(!ce) pause_ticks++;end
 else ce=1;
end
'''
    for prefix in ('old_','new_'):
        text+=f'''mister_ddr_model {prefix}vram(.clk(clk2x),.busy({prefix}vram_busy),.burstcnt({prefix}vram_burstcnt),
 .addr({{4'b0011,{prefix}vram_addr[27:3]}}),.dout({prefix}vram_dout),.ready({prefix}vram_dout_ready),.rd({prefix}vram_rd),
 .din({prefix}vram_din),.be({prefix}vram_be),.we({prefix}vram_we));
'''
    body=template[template.index('integer checks=0;'):]
    body=body.replace('while(bus_stall)','while(old_bus_stall || new_bus_stall || !ce)').replace('while(!ss_idle)','while(!old_ss_idle || !new_ss_idle)')
    body=body.replace('$fatal(1,"GPU timeout")', '$fatal(1,"GPU timeout program=%d old_idle=%d new_idle=%d old_poly=%d new_poly=%d new_step=%d old_fifo=%d new_fifo=%d",programs,old_ss_idle,new_ss_idle,old_gpu.igpu_poly.state,new_gpu.igpu_poly.state,new_gpu.igpu_poly.linemulstep,old_gpu.fifoin_empty,new_gpu.fifoin_empty)')
    body=body.replace('word_value=vram.data[(y*1024+x)>>1];','''word_value=old_vram.data[(y*1024+x)>>1];
 if(word_value!==new_vram.data[(y*1024+x)>>1]) $fatal(1,"Shared GPU oracle word mismatch");''')
    insert='''integer memory_checks=0,programs=0;
task compare_vram;
 for(integer word_index=0;word_index<262144;word_index++) begin
  if(old_vram.data[word_index]!==new_vram.data[word_index])
   $fatal(1,"Shared GPU VRAM mismatch program=%d word=%d old=%08x new=%08x",programs,word_index,old_vram.data[word_index],new_vram.data[word_index]);
  memory_checks++;
 end
 programs++;
endtask
'''
    body=body.replace('initial begin\n repeat(100)',insert+'initial begin\n repeat(100)',1)
    final=' $display("PASS GPU primitive checks=%d",checks);$finish;'
    if body.count(final)!=1:raise ValueError('GPU oracle final marker changed')
    body=body.replace(final,''' compare_vram();
 // Bounded random Gouraud triangles/quads, including offscreen vertices,
 // both windings, diagonal slopes, dithering and semi-transparency.
 for(integer trial=0;trial<96;trial++) begin : polygon_case
  integer x,y,w,h,command;
  reg [31:0] colors[0:3];
  reg [31:0] vertices[0:3];
  pauses=0;ce=1;
  x=(trial*37)%1040-8;y=(trial*19)%528-8;w=3+(trial%29);h=3+((trial*7)%29);
  for(integer i=0;i<4;i++) colors[i]=((trial*17813+i*77569+12345)&24'hffffff);
  vertices[0]=((y&16'hffff)<<16)|(x&16'hffff);
  vertices[1]=((y&16'hffff)<<16)|((x+w)&16'hffff);
  vertices[2]=(((y+h)&16'hffff)<<16)|((x+trial%w)&16'hffff);
  vertices[3]=(((y+h)&16'hffff)<<16)|((x+w)&16'hffff);
  command=32'h30000000 | ((trial%3==0) ? 32'h08000000:0) | ((trial%5==0) ? 32'h02000000:0);
  write_gpu(0,32'he1000000 | ((trial%4)<<5) | ((trial%2)<<9));
  write_gpu(0,command | colors[0]);write_gpu(0,vertices[0]);
  write_gpu(0,colors[1]);write_gpu(0,vertices[trial%2==0 ? 1:2]);
  write_gpu(0,colors[2]);write_gpu(0,vertices[trial%2==0 ? 2:1]);
  if(trial%3==0) begin write_gpu(0,colors[3]);write_gpu(0,vertices[3]);end
  idle();compare_vram();
  // psx_top gates global CE only after SS_Idle; active-render CE pauses are
  // outside that contract and can lose downstream FIFO writes upstream.
  pauses=1;repeat(24) @(negedge clk1x);pauses=0;ce=1;
 end
 // General textured/shaded polygons use the same uploaded texture and CLUT.
 for(integer trial=0;trial<48;trial++) begin : textured_case
  integer x,y,w,h,command,page,clut;
  x=400+(trial*11)%200;y=192+(trial*13)%160;w=3+trial%17;h=3+(trial*5)%17;
  command=32'h34000000 | ((trial%3==0) ? 32'h08000000:0) | ((trial%2==0) ? 32'h01000000:0) | ((trial%5==0) ? 32'h02000000:0);
  page=trial%3==0 ? 6:trial%3==1 ? 16'h0086:16'h0105;
  page=page|((trial%4)<<5);clut=16'h2800;
  write_gpu(0,32'h01000000);
  write_gpu(0,command|24'h808080);write_gpu(0,(y<<16)|x);write_gpu(0,(clut<<16)|16'h8000);
  write_gpu(0,24'h4080c0);write_gpu(0,(y<<16)|(x+w));write_gpu(0,(page<<16)|16'h8003);
  write_gpu(0,24'hc08040);write_gpu(0,((y+h)<<16)|x);write_gpu(0,16'h8100);
  if(trial%3==0) begin write_gpu(0,24'h80a060);write_gpu(0,((y+h)<<16)|(x+w));write_gpu(0,16'h8103);end
  idle();compare_vram();
  // psx_top gates global CE only after SS_Idle; active-render CE pauses are
  // outside that contract and can lose downstream FIFO writes upstream.
  pauses=1;repeat(24) @(negedge clk1x);pauses=0;ce=1;
 end
 $display("PASS GPU line share oracle=%d programs=%d memory_checks=%d pause_ticks=%d total_ticks=%d",checks,programs,memory_checks,pause_ticks,total_ticks);$finish;''')
    body=body.replace('repeat(1000000)','repeat(10000000)')
    text+="""integer old_trace,new_trace;
initial begin old_trace=$fopen("old-pixels.csv","w");new_trace=$fopen("new-pixels.csv","w");end
always @(negedge clk2x) begin
 if(old_gpu.igpu_poly.pipeline_new) $fwrite(old_trace,"%d,%d,%d,%d,%d,%08x,%08x,%08x\\n",programs,
  old_gpu.igpu_poly.pipeline_x,old_gpu.igpu_poly.pipeline_y,old_gpu.igpu_poly.pipeline_cr,old_gpu.igpu_poly.pipeline_cg,
  old_gpu.igpu_poly.work_r,old_gpu.igpu_poly.dxr,old_gpu.igpu_poly.dyr);
 if(new_gpu.igpu_poly.pipeline_new) $fwrite(new_trace,"%d,%d,%d,%d,%d,%08x,%08x,%08x\\n",programs,
  new_gpu.igpu_poly.pipeline_x,new_gpu.igpu_poly.pipeline_y,new_gpu.igpu_poly.pipeline_cr,new_gpu.igpu_poly.pipeline_cg,
  new_gpu.igpu_poly.work_r,new_gpu.igpu_poly.dxr,new_gpu.igpu_poly.dyr);
end
"""
    if divider_pool or combined:
        body=body.replace('new_gpu.igpu_poly.linemulstep','new_gpu.igpu_poly.mulstep')
        marker=' $display("PASS GPU line share'
        lines=''' // Abort while both polygon engines have just issued geometry
 // divisions, before either renderer has emitted the primitive's pixels.
 write_gpu(0,32'h300080c0);write_gpu(0,32'h00640064);
 write_gpu(0,24'hc04080);write_gpu(0,32'h006400a4);
 write_gpu(0,24'h80c040);write_gpu(0,32'h00a40064);
 wait(new_gpu.pool_starts!=0);@(negedge clk2x);reset=1;
 repeat(4) @(negedge clk1x);reset=0;idle();compare_vram();
 write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);
 // Every line starts six divider lanes, including flat lines.
 for(integer trial=0;trial<48;trial++) begin : line_case
  integer x,y,xx,yy,cmd;
  x=(trial*43)%1060-12;y=(trial*23)%536-8;
  xx=x+(trial%4==0 ? 0:3+trial%41);yy=y+(trial%4==1 ? 0:2+trial%29);
  cmd=(trial%2==0 ? 32'h40000000:32'h50000000) | (trial%3==0 ? 32'h02000000:0);
  write_gpu(0,32'he1000000 | ((trial%4)<<5) | ((trial%2)<<9));
  write_gpu(0,cmd|24'h4080c0);write_gpu(0,((y&65535)<<16)|(x&65535));
  if(trial%2) write_gpu(0,24'hc08040);
  write_gpu(0,((yy&65535)<<16)|(xx&65535));
  idle();compare_vram();
 end
'''
        body=body.replace(marker,lines+marker)
    if timing_prune or combined:
        body=body.replace('new_gpu.igpu_poly.linemulstep','new_gpu.igpu_poly.mulstep')
        marker=' $display("PASS GPU line share'
        extra=''' // Line timing counters are also disabled; exercise both line engines.
 for(integer trial=0;trial<48;trial++) begin : timing_line_case
  integer x,y,xx,yy,cmd;
  x=(trial*43)%1060-12;y=(trial*23)%536-8;
  xx=x+(trial%4==0 ? 0:3+trial%41);yy=y+(trial%4==1 ? 0:2+trial%29);
  cmd=(trial%2==0 ? 32'h40000000:32'h50000000) | (trial%3==0 ? 32'h02000000:0);
  write_gpu(0,32'he1000000 | ((trial%4)<<5) | ((trial%2)<<9));
  write_gpu(0,cmd|24'h4080c0);write_gpu(0,((y&65535)<<16)|(x&65535));
  if(trial%2) write_gpu(0,24'hc08040);
  write_gpu(0,((yy&65535)<<16)|(xx&65535));
  idle();compare_vram();
 end
 // Rectangle size modes, texture, masks, clipping and normal/interlaced draw.
 for(integer trial=0;trial<64;trial++) begin : timing_rectangle_case
  integer command,x,y,w,h;
  command=32'h60000000 | ((trial%4)<<27) | (trial%3==0 ? 32'h04000000:0) | (trial%5==0 ? 32'h02000000:0);
  x=(trial*29)%1040-8;y=(trial*17)%528-8;
  w=trial%16==0 ? 0:1+trial%11;h=trial%16==8 ? 0:1+(trial*3)%13;
  write_gpu(4,trial%2 ? 32'h08000024:32'h08000000);
  write_gpu(4,32'h05000000 | ((trial%4>=2)<<10));
  write_gpu(0,32'he1000105 | ((trial%4)<<5));write_gpu(0,32'he6000000 | (trial%4));
  write_gpu(0,32'he3000000 | (trial%7==0 ? 16'h2020:0));
  write_gpu(0,32'he407ffff);write_gpu(0,command|24'h808080);
  write_gpu(0,((y&65535)<<16)|(x&65535));
  if(trial%3==0) write_gpu(0,32'h28008000);
  if(trial%4==0) write_gpu(0,(h<<16)|w);
  idle();compare_vram();
 end
 write_gpu(4,32'h08000000);write_gpu(4,32'h05000000);
 write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he6000000);
 // CPU upload/readback through the actual GP0 FIFO, including odd widths.
 for(integer trial=0;trial<6;trial++) begin : readback_case
  integer x,y,w,h,pixels,total_words;
  reg [31:0] payload,status,got,expected,mask;
  x=700+trial*17;y=300+trial*5;
  case(trial)
   0: begin w=2;h=1;end
   1: begin w=3;h=2;end
   2: begin w=4;h=3;end
   3: begin w=5;h=3;end
   4: begin w=1;h=3;end
   5: begin w=7;h=1;end
  endcase
  pixels=w*h;total_words=(pixels+1)/2;
  write_gpu(0,32'ha0000000);write_gpu(0,(y<<16)|x);write_gpu(0,(h<<16)|w);
  for(integer word_index=0;word_index<total_words;word_index++) begin
   payload=((word_index*2+1)*313+trial*977)&32767;
   payload|=((((word_index*2+2)*313+trial*977)&32767)<<16);
   write_gpu(0,payload);
  end
  idle();
  write_gpu(0,32'hc0000000);write_gpu(0,(y<<16)|x);write_gpu(0,(h<<16)|w);
  for(integer word_index=0;word_index<total_words;word_index++) begin : word_read
   integer polls;
   polls=0;read_gpu(4,status);
   while(!status[27]) begin
    read_gpu(4,status);polls++;
    if(polls>50000) $fatal(1,"GPU readback data timeout");
   end
   read_gpu(0,got);
   expected=((word_index*2+1)*313+trial*977)&32767;
   expected|=((((word_index*2+2)*313+trial*977)&32767)<<16);
   // Check the valid last pixel; the extra upper half is outside this oracle.
   mask=word_index*2+1>=pixels ? 32'h0000ffff:32'hffffffff;
   if((got&mask)!==(expected&mask)) $fatal(1,"GPU readback mismatch trial=%d word=%d got=%x expected=%x",trial,word_index,got,expected);
   readback_checks++;
  end
  idle();compare_vram();
 end
 $display("PASS GPU readback checks=%d",readback_checks);
'''
        if combined:
            # The divider-pool extension already supplies the 48 line cases.
            extra=extra[extra.index(' // Rectangle size modes'):]
        body=body.replace(marker,extra+marker)
        body=body.replace('integer memory_checks=0,programs=0;', '''integer memory_checks=0,programs=0,readback_checks=0;
task read_gpu(input [3:0] address,output [31:0] value);
 @(negedge clk1x);while(!ce) @(negedge clk1x);
 bus_addr=address;bus_read=1;
 @(negedge clk1x);value=old_bus_dataread;bus_read=0;
 if(value!==new_bus_dataread) $fatal(1,"GPU readback output differs between engines");
endtask''')
        if timing_prune:
            body=body.replace(' $display("PASS GPU line share',
                ' $display("PASS GPU cycle outputs ticks=%d",cycle_io_ticks);\n $display("PASS GPU line share')
    label='combined adaptations' if combined else 'divider pool' if divider_pool else 'timing prune' if timing_prune else 'line share'
    body=body.replace('PASS GPU line share','PASS GPU '+label)
    text+=body;path=directory/'tb_gpu_share.sv';path.write_text(text);return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    variant=parser.add_mutually_exclusive_group()
    variant.add_argument('--divider-pool',action='store_true')
    variant.add_argument('--timing-prune',action='store_true')
    variant.add_argument('--combined',action='store_true',help='Compare line sharing, divider pooling and timing pruning together')
    variant.add_argument('--coordinates',action='store_true',help='Compare coordinate registers against the current combined GPU on every sampled cycle')
    variant.add_argument('--borrowed-inputs',action='store_true',help='Compare producer-held divider operands against the coordinate-register GPU')
    parser.add_argument('--output',type=Path,help='Independent output directory for a toolchain comparison')
    args=parser.parse_args()
    ghdl=find_tool('ghdl');verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():raise SystemExit('GHDL synthesis and Verilator required')
    label='combined adaptations' if (args.combined or args.coordinates or args.borrowed_inputs) else 'divider pool' if args.divider_pool else 'timing prune' if args.timing_prune else 'line share'
    directory=args.output.resolve() if args.output else ROOT/('build/sim/gpu-borrowed' if args.borrowed_inputs else 'build/sim/gpu-coordinates' if args.coordinates else 'build/sim/gpu-combined' if args.combined else 'build/sim/gpu-divider-pool' if args.divider_pool else 'build/sim/gpu-timing-prune' if args.timing_prune else 'build/sim/gpu-line-share');directory.mkdir(parents=True,exist_ok=True)
    log=directory/'checks.log';models=ROOT/'sim/reference/mister_memory_models.sv'
    with log.open('w') as file:
        original,oldverilog,oldname=transcode(directory/'original',False,ghdl,file,coordinates=args.coordinates,borrowed=args.borrowed_inputs)
        adapted,newverilog,newname=transcode(directory/'adapted',True,ghdl,file,args.divider_pool,args.timing_prune,args.combined,args.coordinates,args.borrowed_inputs)
        top=bench(directory,oldname,newname,args.divider_pool,args.timing_prune or args.coordinates or args.borrowed_inputs,args.combined or args.coordinates or args.borrowed_inputs)
        subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal','--output-split','20000',
            '--top-module','tb_gpu_share','--Mdir',str(directory/'obj'),str(oldverilog),str(newverilog),str(models),str(top)],
            cwd=directory,stdout=file,stderr=subprocess.STDOUT,timeout=300,check=True)
    executable=directory/'obj/Vtb_gpu_share'
    checked_files=[oldverilog,newverilog,top,models,executable]
    hashes={str(path):digest(path) for path in checked_files}
    result=subprocess.run([str(directory/'obj/Vtb_gpu_share')],cwd=directory,capture_output=True,text=True,timeout=180)
    output=result.stdout+result.stderr;print(output,end='',flush=True)
    with log.open('a') as file:file.write(output)
    identity=all(digest(Path(path))==sha for path,sha in hashes.items())
    identity &= all(digest(Path(s['path']))==s['sha256'] and digest(Path(s['original']))==s['original_sha256']
        and ('selected' not in s or digest(Path(s['selected']))==s['selected_sha256'])
        for manifest in (original,adapted) for s in manifest['sources'])
    programs=264 if (args.combined or args.coordinates or args.borrowed_inputs) else 194 if args.divider_pool else 263 if args.timing_prune else 145
    comparisons=programs*262144
    passed=result.returncode==0 and identity and bool(re.search(r'PASS GPU '+label+rf' oracle=\s*360 programs=\s*{programs} memory_checks=\s*{comparisons} pause_ticks=\s*[1-9]',output))
    cycle_match=re.search(r'PASS GPU cycle outputs ticks=\s*([1-9][0-9]*)',output)
    if args.timing_prune or args.coordinates or args.borrowed_inputs:passed &= bool(cycle_match)
    readback_match=re.search(r'PASS GPU readback checks=\s*24',output)
    if args.timing_prune or args.combined or args.coordinates or args.borrowed_inputs:passed &= bool(readback_match)
    (directory/'report.json').write_text(json.dumps({'original':original,'adapted':adapted,
        'old_verilog_sha256':digest(oldverilog),'new_verilog_sha256':digest(newverilog),'bench_sha256':digest(top),
        'models_sha256':digest(models),'source_identity_verified':identity,'stdout':output,'exit_code':result.returncode,'passed':passed,
        'executable_sha256':hashes[str(executable)],'latency_changed':not (args.timing_prune or args.coordinates or args.borrowed_inputs),
        'comparison_baseline':'Combined GPU with registered coordinates' if args.borrowed_inputs else 'Current combined GPU sharing/timing variant' if args.coordinates else 'Original MiSTer GPU',
        'cycle_io_equivalence_ticks':int(cycle_match[1]) if cycle_match else None,
        'bounded_cycle_io_equivalence_verified':passed and (args.timing_prune or args.coordinates or args.borrowed_inputs),
        'gpu_readback_checks':24 if (args.timing_prune or args.combined or args.coordinates or args.borrowed_inputs) and readback_match else None,
        'clock_enable_pause_scope':'Only after both GPU SS_Idle signals, matching psx_top',
        'active_render_pauses_verified':False,'exact_gpu_timing_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed:raise SystemExit(f'GPU sharing comparison failed; see {log}')


if __name__=='__main__':main()
