#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run an authored guest through the actual full PGXP core, cold reset included.

Requires a source-bound project from prepare_pgxp.py, GHDL and Verilator.
Uses digital SDRAM and common portable RAM/VRAM models. This does not model
board electrical behavior or establish game performance/compatibility.
"""
import argparse
from pathlib import Path
import json
import re
import subprocess
from pgxp_sim_sources import g, ROOT, transcode_gpu, transcode_core
from tool_paths import find_tool
from run_sdram_ctrl import ALTDDIO
from pgxp_guest_program import write


def preparation(project):
    baseline = project / 'pgxp-preparation.json'
    path = project / 'pgxp-structure.json'
    if not path.exists():
        path = baseline
    report = json.loads(path.read_text())
    if path != baseline and report['baseline_preparation_sha256'] != g.digest(baseline):
        raise ValueError('Baseline preparation record changed')
    return path, report


def intel_gaussian_model(output):
    vendor = Path(find_tool('quartus_sh')).parent.parent / 'eda/sim_lib/altera_mf.v'
    raw = vendor.read_bytes()
    modules = []
    for name in (b'ALTERA_DEVICE_FAMILIES', b'altmult_add'):
        match = re.search(rb'(?m)^module ' + name + rb'\b', raw)
        if not match:
            raise ValueError('Missing Intel simulation module: ' + name.decode())
        end = re.search(rb'(?m)^endmodule\b[^\n]*', raw[match.start():])
        if not end:
            raise ValueError('Incomplete Intel simulation module')
        modules.append(raw[match.start():match.start() + end.end()])
    path = output / 'actual_intel_altmult_add.v'
    path.write_bytes(b'`timescale 1ps/1ps\n' + b'\n'.join(modules) + b'\n')
    return vendor, path


def qualify(project, output):
    D=Path(output).resolve();C=Path(project).resolve();program=write(D);v=D/'system.v';tr=json.loads((D/'transcode-report.json').read_text());assert tr['verilog_sha256']==g.digest(v)
    t=v.read_text();a=t.index('module psx_mister_psx_top_resource\n');header=t[a:].split(');',1)[0];ports=re.findall(r'\b(input|output)\s+(\[[^]]+\]\s+)?(\w+)',header);assert len(ports)>130,len(ports)
    model_driven=set('ram_dataread32 ram_done ram_dmafifo_read cache_wr cache_data cache_addr dma_wr dma_reqprocessed dma_data spuram_dataread spuram_done ddr3_busy ddr3_dout ddr3_dout_ready pgxp_cpu_valid pgxp_return_tag pgxp_cpu_record pgxp_gpu_valid pgxp_gpu_record pgxp_memory_idle pgxp_initializing'.split())
    b='`timescale 1fs/1fs\nmodule tb;\n'
    for io,width,name in ports:
     n=name.lower();width=width or'';b+=('wire 'if io=='output'or n in model_driven else'reg ')+width+n+(';'if io=='output'or n in model_driven else'=1;'if n in ['reset','lidopen','clk1x','clk2x','clk3x','clkvid']else'=0;')+'\n'
    b+='psx_mister_psx_top_resource core('+','.join('.'+name+'('+name.lower()+')'for _,_,name in ports)+');\n'
    b+='''always #14763000 clk1x=~clk1x;
    always #7381500 clk2x=~clk2x;
    always #4921000 clk3x=~clk3x;
    always #6734000 clkvid=~clkvid;
    reg init=1;reg load_valid=0;reg[24:0]load_addr=0;reg[127:0]load_data=0;wire load_ready,load_rsp;
    wire[15:0]dq;wire[12:0]a;wire[1:0]dqm,ba;wire nwe,nras,ncas,cke,dclk;wire cheat_ack;wire[15:0]cheat_q;
    psx_native_memory memory_service(
     .pgxp_enabled(pgxp_enabled),.pgxp_clear(pgxp_clear),.pgxp_cpu_read(pgxp_cpu_read),.pgxp_gpu_read(pgxp_gpu_read),.pgxp_cpu_tag(pgxp_cpu_tag),.pgxp_cpu_store_meta(pgxp_store_meta),
     .pgxp_cpu_valid(pgxp_cpu_valid),.pgxp_cpu_ready(pgxp_cpu_ready),.pgxp_cpu_return_tag(pgxp_return_tag),.pgxp_cpu_record(pgxp_cpu_record),.pgxp_gpu_valid(pgxp_gpu_valid),.pgxp_gpu_ready(pgxp_gpu_ready),.pgxp_gpu_record(pgxp_gpu_record),.pgxp_idle(pgxp_memory_idle),.pgxp_initializing(pgxp_initializing),
     .clk1x(clk1x),.clk3x(clk3x),.init(init),.cpu_reset(reset),.ram_refresh(ram_refresh),.ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),.ram_adr(ram_adr),.ram_cntDMA(ram_cntdma),.ram_dataWrite(ram_datawrite),.ram_be(ram_be),.ram_done(ram_done),.ram_dataRead32(ram_dataread32),.dma_wr(dma_wr),.dma_data(dma_data),.dma_reqprocessed(dma_reqprocessed),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),.ram_dmafifo_adr(ram_dmafifo_adr),.ram_dmafifo_data(ram_dmafifo_data),.ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read),
     .spuram_ena(spuram_ena),.spuram_rnw(spuram_rnw),.spuram_adr(spuram_adr),.spuram_dataWrite(spuram_datawrite),.spuram_be(spuram_be),.spuram_done(spuram_done),.spuram_dataRead(spuram_dataread),
     .load_valid(load_valid),.load_addr(load_addr),.load_halfwords(4'd8),.load_data(load_data),.load_be(16'hffff),.load_ready(load_ready),.load_rsp(load_rsp),.load_rsp_ready(1'b1),
     .dram_dq(dq),.dram_a(a),.dram_dqm(dqm),.dram_ba(ba),.dram_we_n(nwe),.dram_ras_n(nras),.dram_cas_n(ncas),.dram_cke(cke),.dram_clk(dclk),
     .cheat_req_tgl(1'b0),.cheat_rnw(1'b0),.cheat_byte(1'b0),.cheat_addr(21'd0),.cheat_data(16'd0),.cheat_ack_tgl(cheat_ack),.cheat_q(cheat_q));
    wire chip_clk;assign #2000000 chip_clk=dclk;
    sdram_chip_model #(.STRICT_RFC(1),.NAME("full-pgxp"),.CAS(3),.T_AC(5.5),.T_OH(2.5),.T_RET(2.0)) chip(.clk(chip_clk),.cke(cke),.ncs(1'b0),.nras(nras),.ncas(ncas),.nwe(nwe),.a(a),.ba(ba),.dqm(dqm),.dq(dq));
    mister_ddr_model video_memory(.clk(clk2x),.busy(ddr3_busy),.burstcnt(ddr3_burstcnt),.addr({4'b0011,ddr3_addr[27:3]}),.dout(ddr3_dout),.ready(ddr3_dout_ready),.rd(ddr3_rd),.din(ddr3_din),.be(ddr3_be),.we(ddr3_we));
    integer cycles=0,integer_checks=0,cpu_snapshots=0,gpu_snapshots=0,gpu_words=0,gte_commands=0,events=0;reg finished=0;reg[31:0]expected[0:2];
    always@(posedge clk1x)begin
     cycles++;
     if(pgxp_cpu_valid && pgxp_cpu_ready)cpu_snapshots++;
     if(pgxp_gpu_valid && pgxp_gpu_ready)gpu_snapshots++;
     if(core.component_core.igpu.dma_gpu_writeena)gpu_words++;
     if(core.component_core.gte_cmdena)begin gte_commands++;
      if(pgxp_requested && !pgxp_enabled)$fatal(1,"Authored warmup did not allow mode activation");
     end
     if(!reset && core.component_core.ce && ram_ena && !ram_rnw)begin
      if(ram_adr>=25'h1000 && ram_adr<25'h100c)begin
       if(ram_datawrite!==expected[integer_checks])$fatal(1,"Full guest integer coordinate %0d got %h expected %h",integer_checks,ram_datawrite,expected[integer_checks]);integer_checks++;
      end
      if(ram_adr==25'hff00)begin
       if(ram_datawrite!=32'h600df00d || integer_checks!=3)$fatal(1,"Full guest completion contract");finished<=1;
      end
     end
     if(cycles%500000==0)begin $display("GUEST cycles=%0d reset=%b resetI=%b CE=%b SSstate=%0d type=%0d cursor=%0d pc=%h integer=%0d enabled=%b cpu_records=%0d gpu_records=%0d events=%0d",cycles,reset,core.component_core.reset_intern,core.component_core.ce,core.component_core.isavestates.state,core.component_core.isavestates.savetype_counter,core.component_core.isavestates.ramaddrnext,core.component_core.icpu.pc,integer_checks,pgxp_enabled,cpu_snapshots,gpu_snapshots,events);$fflush();end
    end
    always@(posedge clk3x)begin
     if(memory_service.pgxp_event_valid)events++;
     if(chip.drive && memory_service.controller.dq_oe)$fatal(1,"Full SDRAM contention");
    end
    function automatic bit inside_triangle(input integer x,y);
     longint signed ex0,ex1,ex2,px,py,x0,y0,x1,y1,x2,y2;
     begin
      px=16*x;py=16*y;x0=16*38;y0=16*40;x1=16*82;y1=16*40;x2=16*38;y2=16*86;
      if(pgxp_requested)begin x0+=8;y0+=8;x1+=8;y1+=8;x2+=8;y2+=8;end
      ex0=(x1-x0)*(py-y0)-(y1-y0)*(px-x0);
      ex1=(x2-x1)*(py-y1)-(y2-y1)*(px-x1);
      ex2=(x0-x2)*(py-y2)-(y0-y2)*(px-x2);
      // Top and left included; the sloping right/bottom edge excluded.
      inside_triangle=ex0>=0 && ex1>0 && ex2>=0;
     end
    endfunction
    '''
    b+=f"reg[31:0]rom[0:{program['words']-1}];\n"
    b+='''initial begin
     expected[0]=32'h00280026;expected[1]=32'h00280052;expected[2]=32'h00560026;
     if($test$plusargs("pgxp"))pgxp_requested=1;
     $readmemh("boot.hex32",rom);repeat(10)@(negedge clk1x);init=0;
    '''
    b+=f" for(integer k=0;k<{program['words']};k+=4)begin\n"
    b+='''  @(negedge clk1x);load_addr=k*2;load_data={rom[k+3],rom[k+2],rom[k+1],rom[k]};load_valid=1;
      @(posedge clk1x);while(!load_ready)@(posedge clk1x);
      @(negedge clk1x);load_valid=0;while(!load_rsp)@(negedge clk1x);
     end
     repeat(10)@(negedge clk1x);reset=0;
     wait(finished);while(!core.component_core.igpu.ss_idle || core.component_core.idma.dmaon)@(negedge clk1x);
     repeat(100)@(negedge clk1x);
     begin integer pixels=0;reg[31:0]word_value;reg[15:0]actual,want;
      for(integer y=0;y<128;y++)for(integer x=0;x<128;x++)begin
       want=inside_triangle(x,y)?16'h0cd4:0;word_value=video_memory.data[(y*1024+x)>>1];actual=x%2?word_value[31:16]:word_value[15:0];
       if(actual!==want)$fatal(1,"Full guest raster mode=%b x/y=%0d/%0d got=%h expected=%h",pgxp_requested,x,y,actual,want);pixels++;
      end
      if(chip.errors!=0 || gpu_words!=4 || gte_commands!=1 || integer_checks!=3)$fatal(1,"Full guest coverage/chip errors");
      if(pgxp_requested && (gpu_snapshots!=4 || cpu_snapshots<2 || !pgxp_enabled))$fatal(1,"Full guest precision path vacuous");
      if(!pgxp_requested && (gpu_snapshots!=0 || cpu_snapshots!=0 || events!=0))$fatal(1,"Native-Off emitted precision traffic");
      $display("PASS FULL PGXP GUEST mode=%b integer=%0d pixels=%0d GTE=%0d DMAwords=%0d CPUrecords=%0d GPUrecords=%0d events=%0d cycles=%0d",pgxp_requested,integer_checks,pixels,gte_commands,gpu_words,cpu_snapshots,gpu_snapshots,events,cycles);$finish;
     end
    end
    initial begin #360000000000000;$fatal(1,"Full guest timeout pc=%h pending=%b",core.component_core.icpu.pc,core.component_core.precision_return.pending);end
    endmodule
    '''
    (D/'tb.sv').write_text(b);(D/'ddio.sv').write_text(ALTDDIO)
    (D/'m10k.sv').write_text('''module psx_m10k_sdp #(parameter WIDTH=10,WIDTHAD=8,DUAL_CLOCK=0)(input wrclock,rdclock,wren,input[WIDTHAD-1:0]wraddress,rdaddress,input[WIDTH-1:0]data,output reg[WIDTH-1:0]q=0);
    reg[WIDTH-1:0]m[0:(1<<WIDTHAD)-1];initial foreach(m[i])m[i]=0;
    always@(posedge wrclock)if(wren)m[wraddress]<=data;
    generate if(DUAL_CLOCK)begin always@(posedge rdclock)q<=m[rdaddress];end else begin always@(posedge wrclock)q<=m[rdaddress];end endgenerate
    endmodule
    ''')
    rows=g.inputs(C);gte=[Path(r['path'])for r in rows if r['assignment']=='SYSTEMVERILOG_FILE' and any(z in r['path']for z in ['/gte/','/gte-mac-ram/','/gte-adapter/','/gte-multicycle/'])];assert len(gte)==18,len(gte)
    names=['psx_native_memory.sv','psx_sdram_ctrl.sv','psx_spu_ch3_cache.sv','psx_pgxp_shadow.sv','psx_pgxp_memory.sv','psx_pgxp_event_backend.sv','psx_pgxp_event_fifo.sv','psx_pgxp_sdram64.sv','psx_pgxp_cpu_return.sv','psx_pgxp_mode.sv','psx_pgxp_gte_fifo.sv','psx_video_config_cdc.sv','dual_clock_fifo.sv']
    vendor_files=[]
    if any(Path(row['path']).name=='psx_spu_gaussian_mac.sv' for row in rows):
        names.append('psx_spu_gaussian_mac.sv')
        vendor, model=intel_gaussian_model(D)
        vendor_files=[vendor,model]
    sources=[v,*gte,*[g.one(C,n)for n in names],*vendor_files[1:],D/'ddio.sv',D/'m10k.sv',g.P/'sim/reference/mister_memory_models.sv',g.P/'sim/memory/sdram_chip_model.sv',D/'tb.sv'];protected={str(p):g.digest(p)for p in sources};protected.update({r['path']:r['sha256']for r in rows});protected.update({str(p):g.digest(p)for p in [D/'boot.hex32',D/'program.json',D/'transcode-report.json',Path(__file__),ROOT/'scripts/pgxp_sim_sources.py',ROOT/'scripts/pgxp_guest_program.py',*vendor_files]});inc=next(Path(r['path']).parent for r in rows if Path(r['path']).name=='GTEDefine.hv')
    protected.update(tr['protected'])
    prep_path, prep = preparation(C)
    protected.update({row['path']: row['sha256'] for row in prep['target']['quartus_sources']})
    protected[str(prep_path)] = g.digest(prep_path)
    protected[str(C/'pgxp-preparation.json')] = g.digest(C/'pgxp-preparation.json')
    assert all(g.digest(Path(path)) == sha for path, sha in protected.items()), 'Source changed before compilation'
    with(D/'build.log').open('w')as f:r=subprocess.run([find_tool('verilator'),'--binary','--timing','-O2','-j','6','-Wno-fatal','--output-split','20000','--output-split-cfuncs','1000','-I'+str(inc),'--top-module','tb','--Mdir',str(D/'obj'),*map(str,sources)],cwd=D,stdout=f,stderr=subprocess.STDOUT,timeout=600)
    assert r.returncode==0,D/'build.log';assert all(g.digest(Path(p))==h for p,h in protected.items());runs=[]
    for mode in [0,1]:
     with(D/f'run-{mode}.log').open('w')as f:r=subprocess.run([str(D/'obj/Vtb')]+(['+pgxp']if mode else[]),cwd=D,stdout=f,stderr=subprocess.STDOUT,timeout=420)
     out=(D/f'run-{mode}.log').read_text();print(out[-3500:],flush=True);assert r.returncode==0 and'PASS FULL PGXP GUEST'in out;assert all(g.digest(Path(p))==h for p,h in protected.items());runs.append(dict(mode=mode,output=out))
    (D/'report.json').write_text(json.dumps(dict(passed=True,scope='Actual full selected PSX resource, real SV GTE/native-memory/controller/PGXP FIFO/backend/CPUreturn/mode. Authored R3000 RTPT then MFC2/LWC2/SWC2/MTC2, GPRmoves/scratch/main RAM, DMA2 and GPU raster; integer coordinates and independent public VRAM pixels. Digital SDRAM timing model, common portable onchip RAM/VRAM models; not electrical/game/performance signoff.',protected=protected,program=program,runs=runs),indent=2)+'\n')

    return json.loads((D/'report.json').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project, output = args.project.resolve(), args.output.resolve()
    if output.exists() or output.is_relative_to(project):
        parser.error('Use a fresh output directory outside the project')
    _, prep = preparation(project)
    if prep['target']['quartus_sources'] != g.inputs(project):
        parser.error('Prepared source closure changed')
    output.mkdir(parents=True)
    with (output / 'gpu-transcode.log').open('w') as log:
        gpu = transcode_gpu(project, output / 'gpu', log)
    transcode_core(project, output, gpu)
    qualify(project, output)


if __name__ == '__main__':
    main()
