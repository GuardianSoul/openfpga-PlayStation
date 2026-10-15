#!/usr/bin/env python3
"""Matched standalone scalar SPU adapter/CDC/packet-bridge area comparison.

This does not measure a connected full-top LAB delta or board timing.
"""
from datetime import datetime,timezone
import argparse
import json
from pathlib import Path
import shutil
import tempfile

from prepare_resource_targets import apply_compile_profile,digest,write_project
from prepare_spu_scalar_buffer import prepare
from run_resource_fit import fit_target
from tool_paths import ROOT,find_tool


WRAPPER='''module psx_spu_scalar_resource (
 input clk1x,mem_clk,reset_async,core_ce,discard_returns,request,rnw,
 input [18:0] addr,input [31:0] data,input [3:0] byte_en,
 output done,output [31:0] read_data,output source_idle,fault,
 output mem_valid,mem_write,output [23:0] mem_addr,output [31:0] mem_wdata,
 output [3:0] mem_byte_en,input mem_ready,mem_rvalid,input [31:0] mem_rdata);
 wire pv,pw,pr,rv,rr,re;wire [24:0] pa;wire [3:0] pn;
 wire [127:0] pd,rd;wire [15:0] pb;
 reg [1:0] reset_mem=3;
 always @(posedge mem_clk or posedge reset_async)
  if(reset_async) reset_mem<=3;else reset_mem<={reset_mem[0],1'b0};
 psx_spu_sdram_adapter adapter(.clk1x(clk1x),.mem_clk(mem_clk),.reset_async(reset_async),
  .core_ce(core_ce),.discard_returns(discard_returns),.request(request),.rnw(rnw),.addr(addr),
  .data(data),.byte_en(byte_en),.done(done),.read_data(read_data),.source_idle(source_idle),.fault(fault),
  .p_valid(pv),.p_write(pw),.p_ready(pr),.p_addr(pa),.p_halfwords(pn),.p_wdata(pd),.p_byte_en(pb),
  .p_rsp_valid(rv),.p_rsp_ready(rr),.p_rsp_error(re),.p_rsp_rdata(rd));
 psx_mem_bridge #(.PIPELINE_READS(1)) bridge(.clk(mem_clk),.rst(reset_mem[1]),
  .req_valid(pv),.req_ready(pr),.req_write(pw),.req_addr(pa),.req_halfwords(pn),.req_wdata(pd),.req_byte_en(pb),
  .rsp_valid(rv),.rsp_ready(rr),.rsp_error(re),.rsp_rdata(rd),
  .mem_valid(mem_valid),.mem_ready(mem_ready),.mem_write(mem_write),.mem_addr(mem_addr),
  .mem_wdata(mem_wdata),.mem_byte_en(mem_byte_en),.mem_rvalid(mem_rvalid),.mem_rdata(mem_rdata));
endmodule
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--storage',choices=('MLAB','M10K','M10K-dual','M10K-stream'),default='MLAB')
    args=parser.parse_args()
    parent=ROOT/'build/resources/spu-scalar-buffer/runs';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent));print(work,flush=True)
    protected={str(p):digest(p) for p in [Path(__file__),ROOT/'scripts/prepare_spu_scalar_buffer.py',
        ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_bridge.sv']}
    if args.storage!='MLAB':protected[str(ROOT/'scripts/prepare_spu_scalar_bram_buffer.py')]=digest(ROOT/'scripts/prepare_spu_scalar_bram_buffer.py')
    if args.storage in ('M10K-dual','M10K-stream'):protected[str(ROOT/'scripts/prepare_spu_scalar_dual_buffer.py')]=digest(ROOT/'scripts/prepare_spu_scalar_dual_buffer.py')
    if args.storage=='M10K-stream':protected[str(ROOT/'scripts/prepare_spu_scalar_stream_buffer.py')]=digest(ROOT/'scripts/prepare_spu_scalar_stream_buffer.py')
    archives={}
    def archive(paths):
        for p in paths:
            p=Path(p).resolve();name=str(p);h=digest(p)
            if name in archives:
                if h!=archives[name]['sha256']:raise ValueError('Area input changed between targets')
                continue
            copy=work/'input-archive'/str(len(archives))/p.name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,copy)
            if digest(copy)!=h:raise ValueError('Area input changed during archive')
            archives[name]=dict(path=str(copy),sha256=h)
    archive(protected)
    results={};top='psx_spu_scalar_resource'
    binary=Path(find_tool('quartus_sh')).parent
    for name in ('baseline','buffer-two-way'):
        directory=work/name;directory.mkdir();wrapper=directory/(top+'.sv');wrapper.write_text(WRAPPER)
        selection=None
        if name!='baseline':
            if args.storage=='M10K-stream':
                from prepare_spu_scalar_stream_buffer import prepare as prepare_stream,connect
                selection=prepare_stream(directory/'candidate')
                wrapper.write_text(connect(WRAPPER,'adapter','bridge'))
            elif args.storage=='M10K-dual':
                from prepare_spu_scalar_dual_buffer import prepare as prepare_dual
                selection=prepare_dual(directory/'candidate')
            elif args.storage=='M10K':
                from prepare_spu_scalar_bram_buffer import prepare as prepare_bram
                selection=prepare_bram(directory/'candidate')
            else:selection=prepare(directory/'candidate',2)
        adapter=Path(selection['path']) if selection else ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv'
        sources=[dict(path=str(p.resolve()),sha256=digest(p),assignment='SYSTEMVERILOG_FILE',library='work')
                 for p in (adapter,ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_bridge.sv',wrapper)]
        if selection and args.storage=='M10K-stream':
            p=Path(selection['bridge_path']);sources += [dict(path=str(p),sha256=digest(p),assignment='SYSTEMVERILOG_FILE',library='work')]
        ports=[]
        for names,width in [('clk1x mem_clk reset_async core_ce discard_returns request rnw done source_idle fault mem_valid mem_write mem_ready mem_rvalid',1),
                            ('addr',19),('data read_data mem_wdata mem_rdata',32),('byte_en mem_byte_en',4),('mem_addr',24)]:
            for port in names.split():ports.append((port,'inout','std_logic' if width==1 else f'std_logic_vector({width-1} downto 0)'))
        project=write_project(directory,top,ports,('clk1x','mem_clk'),sources,
            ['create_clock -name clk1x -period 29.5251 [get_ports clk1x]',
             'create_clock -name mem_clk -period 16.0 [get_ports mem_clk]','derive_clock_uncertainty'])
        target=dict(top=top,directory=str(directory),quartus_sources=sources,project_sha256=project,
                    physical_clock_ports=['clk1x','mem_clk'],fixed_ports={})
        manifest=apply_compile_profile(dict(targets={name:target}),'area-min-state')
        (directory/'manifest.json').write_text(json.dumps(dict(manifest,selection=selection),indent=2)+'\n')
        archive([s['path'] for s in sources]+[directory/(top+s) for s in ('.qsf','.qpf','.sdc')])
        result=fit_target(binary,target,'5CEBA4F23C8',900);results[name]=result
        print(name,'fit_verified=',result['fit_verified'],'counts=',result['counts'],flush=True)
        if not result['fit_verified']:break
    identity=all(digest(Path(p))==h for p,h in protected.items())
    report=dict(passed=identity and len(results)==2 and all(r['fit_verified'] for r in results.values()),
        source_identity_verified=identity,source_sha256=protected,results=results,
        input_archives=archives,storage=args.storage,
        scope='Standalone identical virtual-pin wrappers including CDC and packet bridge; LAB packing is not additive to a physical top',
        full_top_delta_verified=False,board_timing_verified=False,production_selected=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
