#!/usr/bin/env python3
"""GPU prepared-payload proof with real acceptance stalls and scanout priority.

Uses the existing whole-GPU bounded oracle, compares all public outputs and
accepted read payloads, proves the arbitration guard, checks actual GHDL
cones, and rejects two altered RTL implementations. No BIOS/disc needed.
This is not complete optimized-release, guest timing or hardware qualification.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import run_gpu_line_share as run
from prepare_gpu_payload_timing import payload_text as adapt,CHILDREN
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool

def prove_arbitration_and_cones(base):
    root=ROOT;work=base/'proof';work.mkdir()
    yosys=find_tool('yosys') or str(ROOT/'build/tools/oss-cad-suite/bin/yosys')
    lemma='''module gpu_payload_lemma(input ready,pause,vpending,input [5:0] eligible,input [179:0] payloads,input [29:0] video_payload,output bad);
    wire old_video=vpending && ready && !pause;
    wire new_want=vpending;
    wire old_allow=ready && !old_video && !pause;

    wire [29:0] prim_payload;
    assign prim_payload=(payloads[29:0]&{30{eligible[0]}})|(payloads[59:30]&{30{eligible[1]}})|(payloads[89:60]&{30{eligible[2]}})|(payloads[119:90]&{30{eligible[3]}})|(payloads[149:120]&{30{eligible[4]}})|(payloads[179:150]&{30{eligible[5]}});
    wire [29:0] old_payload=(prim_payload&{30{old_allow}})|(video_payload&{30{old_video}});
    wire [29:0] new_payload=new_want ? video_payload:prim_payload;
    assign bad=ready && !pause && (old_payload!=new_payload);
    endmodule
    '''
    p=work/'lemma.sv';p.write_text(lemma)
    runs=[]
    for name,text,negative in [('arbitration',lemma,False),('priority-fault',lemma.replace('new_want ? video_payload:prim_payload','prim_payload | (video_payload&{30{new_want}})'),True)]:
     p=work/(name+'.sv');p.write_text(text);log=work/(name+'.log')
     command=[yosys,'-Q','-T','-p',f'read_verilog "{p}"; prep -top gpu_payload_lemma; flatten; sat -prove bad 0 -verify -show-inputs']
     with log.open('w') as f:r=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=60)
     result=log.read_text();passed=(r.returncode!=0 and 'FAIL!' in result) if negative else (r.returncode==0 and 'SUCCESS!' in result)
     assert passed,result[-1000:]
     runs.append(dict(name=name,passed=passed,negative=negative,source_sha256=digest(p),log_sha256=digest(log),exit_code=r.returncode))
    models=(root/'sim/reference/mister_memory_models.sv').read_text();stubs=work/'opaque_ram_stubs.v'
    stubs.write_text('\n'.join(re.search(r'^module '+n+r'\b[\s\S]*?;',models,re.M)[0]+'\nendmodule\n' for n in ['RamMLAB','dpram','dpram_dif']))
    cones=[]
    for side in ['original','adapted']:
     source=base/'gpu-oracle'/side/'renamed.v';text=source.read_text();top=re.findall(r'^module ((?:original|shared)__gpu(?:_Barch\w*)?)\s*\n',text,re.M)
     assert len(top)==1,top
     netlist=work/(side+'-cone.json');log=work/(side+'-cone.log')
     command=[yosys,'-Q','-T','-p',f'read_verilog -lib "{stubs}"; read_verilog "{source}"; hierarchy -top {top[0]}; proc;  flatten; opt_clean; write_json "{netlist}"']
     with log.open('w') as f:r=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,timeout=180)
     if r.returncode:raise ValueError(log.read_text()[-2000:])
     m=json.loads(netlist.read_text())['modules'][top[0]];drivers={}
     for cell in m['cells'].values():
      for port,direction in cell.get('port_directions',{}).items():
       if direction=='output':
        for b in cell['connections'][port]:
         if isinstance(b,int):drivers[b]=cell
     result={}
     for name in ['reqvramxpos','reqvramypos','reqvramsize']:
      starts=m['netnames'][name]['bits'];pending=list(starts);seen=set();frontier=set()
      while pending:
       b=pending.pop()
       if not isinstance(b,int) or b in seen:continue
       seen.add(b);c=drivers.get(b)
       if c is None:continue
       kind=c['type']
       if 'dff' in kind or 'latch' in kind or kind in ['dpram','dpram_dif']:
        frontier.add(kind);continue
       for port,direction in c.get('port_directions',{}).items():
        if direction=='input' and (kind!='RamMLAB' or port=='rdaddress'):pending.extend(c['connections'][port])
      has_busy=bool(set(m['ports']['vram_busy']['bits'])&seen)
      result[name]=dict(has_busy=has_busy,frontier=sorted(frontier),seen_bits=len(seen))
     cones.append(dict(side=side,top=top[0],source_sha256=digest(source),netlist_sha256=digest(netlist),log_sha256=digest(log),results=result))
    assert all(v['has_busy'] for v in cones[0]['results'].values())
    assert not any(v['has_busy'] for v in cones[1]['results'].values())
    report=dict(passed=True,arbitration_lemma=runs,cones=cones,scope='Combinational payload equivalence when VRAMIdle is true and pause is false; all payload/eligible bits arbitrary, no one-hot assumption. Cone stops at FF/latch and registered RAM outputs; async MLAB follows read address. Not whole-RTL/state/STA proof.',hardware_verified=False,production_selected=False)
    (work/'proof.json').write_text(json.dumps(report,indent=2)+'\n');return report

def reject_rtl_faults(base):
    root=ROOT;w=base/'faults';w.mkdir();positive=base/'gpu-oracle'
    assert json.loads((positive/'report.json').read_text())['passed']
    sources=[positive/'original/renamed.v',positive/'adapted/renamed.v',root/'sim/reference/mister_memory_models.sv',positive/'tb_gpu_share.sv']
    initial={str(p):digest(p) for p in sources};text=sources[1].read_text();cases=[]
    for name,pattern,replacement,count in [('priority',r'(assign \w+ = )(~videoout_reqvramwant);',"1'b1",3),('address',r'(assign reqvramxpos = )([^;]+);',None,1)]:
     p=w/('fault-'+name);p.mkdir(exist_ok=False)
     assert len(re.findall(pattern,text))==count
     changed=re.sub(pattern,lambda m:m[1]+(replacement or '('+m[2]+") ^ 10'b1")+';',text)
     bad=p/'candidate.v';bad.write_text(changed)
     with (p/'build.log').open('w') as f:subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal','--output-split','20000','--top-module','tb_gpu_share','--Mdir',str(p/'obj'),str(sources[0]),str(bad),*map(str,sources[2:])],cwd=p,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=300)
     with (p/'run.log').open('w') as f:r=subprocess.run([str(p/'obj/Vtb_gpu_share')],cwd=p,stdout=f,stderr=subprocess.STDOUT,timeout=180)
     out=(p/'run.log').read_text();accepted=r.returncode!=0 and any(s in out for s in ['Accepted GPU payload mismatch','GPU cycle output mismatch','Shared GPU oracle word mismatch'])
     cases.append(dict(name=name,rejected=accepted,exit_code=r.returncode,mutated_source_sha256=digest(bad),log_sha256=digest(p/'run.log'),output=out))
     print(name,accepted,out[-800:],flush=True)
     if not accepted:break
    identity=all(digest(Path(p))==h for p,h in initial.items());passed=identity and len(cases)==2 and all(x['rejected'] for x in cases)
    (w/'faults.json').write_text(json.dumps(dict(passed=passed,source_identity_verified=identity,source_sha256=initial,cases=cases),indent=2)+'\n')
    if not passed:raise ValueError('GPU actual RTL fault control not rejected')
    return dict(passed=passed,cases=cases)

def main():
    root=ROOT
    parent=ROOT/'build/sim/gpu-payload-timing';parent.mkdir(parents=True,exist_ok=True)
    base=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(base,flush=True)
    protected=[Path(__file__).resolve(),ROOT/'scripts/prepare_gpu_payload_timing.py',
        ROOT/'scripts/run_gpu_line_share.py',ROOT/'scripts/prepare_system_verilator.py',
        ROOT/'scripts/prepare_gpu_borrowed.py',ROOT/'scripts/prepare_gpu_coordinates.py',
        ROOT/'sim/reference/mister_memory_models.sv',ROOT/'sim/reference/tb_gpu_primitives.sv']
    source_hashes={str(p):digest(p) for p in protected}
    archive=base/'driver-inputs';archive.mkdir()
    for i,p in enumerate(protected):
        copy=archive/(str(i)+'-'+p.name);shutil.copyfile(p,copy)
        if digest(copy)!=source_hashes[str(p)]:raise ValueError('GPU driver changed during capture')
    compiled_inputs={}
    # Stack with the existing coordinate/borrowed transformations.
    original_transcode=run.transcode

    def transcode(directory,adapted,*args,**kwargs):
     original_prepare=run.prepare
     # Source substitution before GHDL, with full source/selected identity records.
     # borrowed_text later sees only existing anchors and passes our ports through.
     def prepare(directory,**options):
      m=original_prepare(directory,**options)
      if adapted:
       for s in list(m['sources']):
        name=Path(s['path']).name
        if name not in (*CHILDREN,'gpu.vhd'):continue
        p=directory/'payload-ready'/name;p.parent.mkdir(exist_ok=True)
        p.write_text(adapt(name,Path(s['path']).read_text()))
        m['sources'][m['sources'].index(s)]={**s,'path':str(p),'sha256':digest(p),'selected':s['path'],'selected_sha256':s['sha256']}
      return m
     run.prepare=prepare
     try:return original_transcode(directory,adapted,*args,**kwargs)
     finally:run.prepare=original_prepare
    run.transcode=transcode
    original_bench=run.bench

    def bench(directory,*args,**kwargs):
     p=original_bench(directory,*args,**kwargs);s=p.read_text()
     # Deterministic long and short stalls. Held commands reach the RAM model
     # ONLY on accepting edges; merely ORing BUSY without gating model inputs
     # would duplicate commands and cannot exercise the real handshake.
     declaration='''reg forced_busy=0;reg [31:0] stall_rng=32'h3c6ef372;integer stall_phase=0;
    integer stalled_writes=0,stalled_reads=0,video_requests=0,video_render_overlap=0,paused_clocks=0,prepared_while_busy=0,payload_accept_checks=0;
    always @(negedge clk2x) begin
     stall_rng=(stall_rng>>1)^((stall_rng&1)?32'hd0000001:0);stall_phase++;
     forced_busy=!reset && ((stall_phase%191)<47 || (stall_rng&7)<3);
     if(!reset && forced_busy && old_vram_we)stalled_writes++;
     if(!reset && forced_busy && old_vram_rd)stalled_reads++;
     if(!reset && old_gpu.videoout_reqvramenable)video_requests++;
     if(!reset && new_gpu.videoout_reqvramwant && !old_gpu.proc_idle)video_render_overlap++;
     vram_pause=!reset && (stall_phase%997)>900 && (stall_phase%997)<941;
     if(vram_pause)paused_clocks++;
     if(!reset && forced_busy && new_gpu.reqvramxpos!=old_gpu.reqvramxpos)prepared_while_busy++;
     if(!reset && old_gpu.reqvramenable && old_gpu.vramidle && !vram_pause) begin
      if(old_gpu.reqvramxpos!==new_gpu.reqvramxpos || old_gpu.reqvramypos!==new_gpu.reqvramypos || old_gpu.reqvramsize!==new_gpu.reqvramsize)
       $fatal(1,"Accepted GPU payload mismatch");
      payload_accept_checks++;
     end
    end
    '''
     s=s.replace('reg toggle=0,sampled=0;',declaration+'\nreg toggle=0,sampled=0;')
     s=s.replace('logic old_vram_busy=0,new_vram_busy=0;','wire old_vram_busy,new_vram_busy;')
     for prefix in ['old_','new_']:
      anchor=f'mister_ddr_model {prefix}vram(.clk(clk2x),.busy({prefix}vram_busy),'
      assert s.count(anchor)==1
      s=s.replace(anchor,f'wire {prefix}backend_busy;\nassign {prefix}vram_busy=forced_busy || {prefix}backend_busy;\nmister_ddr_model {prefix}vram(.clk(clk2x),.busy({prefix}backend_busy),')
      for signal in ['rd','we']:
       needle=f'.{signal}({prefix}vram_{signal})';assert s.count(needle)==1
       s=s.replace(needle,f'.{signal}({prefix}vram_{signal} && !forced_busy)')
     # Display enabled before drawing, plus one whole active scanout frame
     # after the oracle, so the BUSY-independent video-priority path is live.
     anchor='// Unclipped drawing area';assert s.count(anchor)==1
     s=s.replace(anchor,"write_gpu(4,32'h03000000);\n "+anchor)
     anchor=' $display("PASS GPU cycle outputs ticks=%d",cycle_io_ticks);'
     assert s.count(anchor)==1
     extension=''' write_gpu(4,32'h03000000);
     repeat(2500000) @(negedge clk2x);
     if(stalled_writes<100 || stalled_reads<10 || video_requests<100 || prepared_while_busy<100 || payload_accept_checks<100 || video_render_overlap<1 || paused_clocks<100)
      $fatal(1,"GPU payload coverage missing %d %d %d %d %d",stalled_writes,stalled_reads,video_requests,prepared_while_busy,payload_accept_checks);
     $display("PASS GPU payload coverage stalled_writes=%0d stalled_reads=%0d video_requests=%0d prepared_while_busy=%0d accept_checks=%0d video_render_overlap=%0d paused_clocks=%0d",stalled_writes,stalled_reads,video_requests,prepared_while_busy,payload_accept_checks,video_render_overlap,paused_clocks);
    '''
     s=s.replace(anchor,extension+anchor)
     p.write_text(s)
     for i,source in enumerate([directory/'original/renamed.v',directory/'adapted/renamed.v',root/'sim/reference/mister_memory_models.sv',p]):
      sha=digest(source);copy=base/'compiler-inputs'/str(i)/source.name;copy.parent.mkdir(parents=True)
      shutil.copyfile(source,copy)
      if digest(copy)!=sha:raise ValueError('GPU compiled input changed while capturing')
      compiled_inputs[str(source)]=dict(path=str(copy),sha256=sha)
     return p
    run.bench=bench
    saved_args=sys.argv[:]
    sys.argv=[sys.argv[0],'--borrowed-inputs','--output',str(base/'gpu-oracle')]
    try:run.main()
    finally:
     run.transcode=original_transcode;run.bench=original_bench;sys.argv=saved_args
    proof=prove_arbitration_and_cones(base)
    faults=reject_rtl_faults(base)
    identity=all(digest(Path(p))==h for p,h in source_hashes.items())
    identity &= all(digest(Path(p))==v['sha256'] and digest(Path(v['path']))==v['sha256'] for p,v in compiled_inputs.items())
    positive=json.loads((base/'gpu-oracle/report.json').read_text())
    passed=identity and positive['passed'] and proof['passed'] and faults['passed']
    report=dict(passed=passed,source_identity_verified=identity,source_sha256=source_hashes,
        compiled_input_archives=compiled_inputs,positive_report=dict(path=str(base/'gpu-oracle/report.json'),sha256=digest(base/'gpu-oracle/report.json')),
        cycle_io_ticks=positive['cycle_io_equivalence_ticks'],programs=264,readback_checks=24,
        proof_report=dict(path=str(base/'proof/proof.json'),sha256=digest(base/'proof/proof.json')),
        faults_report=dict(path=str(base/'faults/faults.json'),sha256=digest(base/'faults/faults.json')),
        production_selected=False,game_verified=False,hardware_verified=False,
        limitations=['Existing combined/coordinate/borrowed GPU fixture, not all release area variants',
            'Public-cycle and accepted-payload equality for bounded authored programs, not complete guest/analog timing proof',
            'Combinational arbitration lemma and state-bounded cones, not whole-RTL induction or routed timing'])
    (base/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(base/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
