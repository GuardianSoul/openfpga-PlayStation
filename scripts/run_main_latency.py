#!/usr/bin/env python3
"""Measure native main-port/CDC latency with a synthetic packet backend.

Reuse the independent MiSTer port oracle and its cache/DMA/CE/reset tests.
This isolates transport cost; it predicts neither chip nor game performance.
"""
import argparse
import csv
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import tempfile

from prepare_resource_targets import UPSTREAM, digest
from prepare_system_sim import prepare_memory_models
from run_mister_main_adapter import oracle_text, vectors
from tool_paths import ROOT, find_tool


def bench_text():
    body=(ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv').read_text()
    start=body.index('    wire [3:0] p_ready,p_rsp_valid,p_rsp_error;')
    end=body.index('    integer count=0,front=0,',start)
    body=body[:start]+'''    wire fabric_idle;
    wire boot_owned=0,protocol_error=0,wait_error=0;
    wire valid=av && ar,write=aw;
    reg measure=1;
    main_latency_backend #(.EXTRA(EXTRA)) backend (
        .clk(clk),.reset(rst),.initialized(initialized),.idle(fabric_idle),
        .valid(av),.write(aw),.ready(ar),.address(aa),.halfwords(an),.wdata(ad),.byte_en(ab),
        .rsp_valid(arv),.rsp_ready(arr),.rdata(ard));
    assign are=0;
    main_latency_monitor timing (
        .clk1x(clk1x),.clk3x(clk3x),.mem_clk(clk),.reset(rst),.measure(measure),
        .source_request(core_ce && ram_ena),.scalar_return(ram_done),.dma_return(dma_wr),.dma_ack(dma_reqprocessed),
        .service_take(adapter.state==0 && adapter.response_toggle==adapter.response_ack_sync[1] &&
            !adapter.src_reset && fifo_empty && adapter.request_sync[1]!=adapter.request_ack),
        .packet_take(adapter.normalized_valid && adapter.s_ready),
        .cdc_return(adapter.state==2 && adapter.s_rsp_valid),.response_toggle(adapter.response_toggle),
        .backend_take(av && ar),.backend_return(arv && arr),.cache_wr(cache_wr));
''' +body[end:]
    anchor='        // Pipeline a second DMA read before the first stream has returned.'
    if body.count(anchor)!=1:raise ValueError('Latency primary-traffic anchor changed')
    body=body.replace(anchor,'        measure=0;\n'+anchor)
    return '''`timescale 1ns/1ps
module tb_psx_mister_main_adapter;
    parameter real MEM_HALF=8, MEM_PHASE=0;
    parameter integer EXTRA=0;
    reg clk=0,rst=1;
    wire initialized;
    initial begin #(MEM_PHASE); forever #(MEM_HALF) clk=~clk; end
''' +body


def classify(row):
    address,rnw,cache,dma,count,_,_=row
    if not rnw:return 'scalar-write'
    if dma:return 'DMA-'+str(count+1)
    if cache:return 'cache-critical-'+str(address//4%4)
    return 'BIOS-read' if address>=0x800000 else 'scalar-read'


def measurements(path,rows,mem_half,extra):
    events=[{} for _ in rows]
    with path.open() as source:
        for row in csv.DictReader(source):
            index=int(row['index'])
            if not 0<=index<len(rows):raise ValueError('Unexpected latency request ID')
            events[index].setdefault(row['event'],[]).append(int(row['time_ps']))
    result=[];period_ps=round(2*mem_half*1000)
    for index,(row,event) in enumerate(zip(rows,events)):
        for name in ('SOURCE','SERVICE','PUBLISH'):
            if len(event.get(name,[]))!=1:raise ValueError(f'Missing/duplicate {name}: {index}')
        for name in ('PACKET','BACKEND','BACKEND_RETURN','CDC_RETURN'):
            if len(event.get(name,[]))!=len(event.get('PACKET',[])) or not event.get(name):
                raise ValueError(f'Incomplete packet timeline {index}: {name}')
        returns=event.get('DMA',[]) if row[3] else event.get('SCALAR',[])
        if len(returns)!=(row[4]+1 if row[3] else 1):raise ValueError('Incorrect native completion count')
        if len(event.get('CACHE',[]))!=(4-row[0]//4%4 if row[2] else 0):raise ValueError('Incorrect cache event count')
        for sent,received in zip(event['BACKEND'],event['BACKEND_RETURN']):
            if received-sent!=(extra+1)*period_ps:
                raise ValueError('Backend latency does not match configured delay')
        source=event['SOURCE'][0];service=event['SERVICE'][0];packet=event['PACKET'][0]
        cdc=event['CDC_RETURN'][-1];publish=event['PUBLISH'][0];complete=returns[-1]
        order=[source,service,packet,event['BACKEND'][0],event['BACKEND_RETURN'][-1],cdc,publish,complete]
        if order!=sorted(order):raise ValueError('Reordered latency events')
        if event.get('CACHE') and event['CACHE'][-1]>returns[0]:raise ValueError('Return before cache consumption')
        backend=sum(end-start for start,end in zip(event['BACKEND'],event['BACKEND_RETURN']))
        stages=dict(request_mailbox=service-source,service_issue=packet-service,
                    memory_transport=cdc-packet-backend,backend=backend,
                    service_finalize=publish-cdc,source_completion=complete-publish)
        if min(stages.values())<0 or sum(stages.values())!=complete-source:
            raise ValueError('Latency stage accounting mismatch')
        result.append(dict(index=index,kind=classify(row),startup=index==0,total_ps=complete-source,
                           first_data_ps=returns[0]-source,packets=len(event['PACKET']),stages_ps=stages))
    return result


def summarize(rows):
    result={}
    for kind in sorted({row['kind'] for row in rows}):
        selected=[r for r in rows if r['kind']==kind and not r['startup']]
        totals=[r['total_ps']/1000 for r in selected]
        result[kind]=dict(requests=len(selected),minimum_ns=min(totals),median_ns=statistics.median(totals),maximum_ns=max(totals),
                         median_stages_ns={key:statistics.median(r['stages_ps'][key]/1000 for r in selected) for key in selected[0]['stages_ps']})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter',type=Path,default=ROOT/'rtl/pocket/psx_mister_main_adapter.sv')
    parser.add_argument('--expected-adapter-sha256',help='Required when measuring an explicit private adapter')
    args=parser.parse_args()
    adapter=args.adapter.resolve()
    if adapter!=(ROOT/'rtl/pocket/psx_mister_main_adapter.sv') and not args.expected_adapter_sha256:
        parser.error('Private adapter requires --expected-adapter-sha256')
    if args.expected_adapter_sha256 and digest(adapter)!=args.expected_adapter_sha256:
        parser.error('Adapter does not match expected source')
    parent=ROOT/'build/sim/main-latency';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    rows,gold=vectors(work)
    model=prepare_memory_models(work/'upstream')[0]
    oracle=work/'oracle.vhd';oracle.write_text(oracle_text())
    ghdl=find_tool('ghdl')
    with (work/'oracle.log').open('w') as log:
        for command in ([ghdl,'-a','--std=08','--work=tb',str(UPSTREAM/'sim/system/src/tb/globals.vhd'),model['path']],
                        [ghdl,'-a','--std=08',str(oracle)],[ghdl,'-e','--std=08','tb_mister_oracle'],
                        [ghdl,'-r','--std=08','tb_mister_oracle','--max-stack-alloc=0','--assert-level=error']):
            subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    actual=[]
    for line in (work/'oracle.txt').read_text().splitlines():
        fields=line.split();actual.append((int(fields[0]),fields[1],*[int(x,16) for x in fields[2:]]))
    if sorted(actual,key=lambda e:(e[0],e[1]))!=gold:raise ValueError('Pinned MiSTer memory oracle differs')
    expected=work/'expected.txt';expected.write_text(''.join(f'{i:x} {kind} {mask:x} {address:x} {data:x}\n' for i,kind,mask,address,data in actual))
    bench=work/'tb.sv';bench.write_text(bench_text())
    sources=[adapter,ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'sim/pocket/tb_main_latency.sv',bench]
    inputs=[*sources,Path(__file__),ROOT/'scripts/run_mister_main_adapter.py',ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv',
            ROOT/'scripts/prepare_system_sim.py',ROOT/'scripts/tool_paths.py',Path(model['path']),oracle,
            UPSTREAM/'sim/system/src/tb/globals.vhd',work/'vectors.txt',expected]
    protected={str(p):digest(p) for p in inputs};archives={}
    for i,(path,sha) in enumerate(protected.items()):
        target=work/'inputs'/str(i)/Path(path).name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        if digest(target)!=sha:raise ValueError('Latency input changed during archive')
        archives[path]=dict(path=str(target),sha256=sha)
    profiles=[]
    for name,half,extra,phase in [('floor62',8,0,0),('delay4-62',8,4,0),('delay12-62',8,12,0),
                                  ('floor104',4.8,0,0),('delay4-104',4.8,4,0),('delay12-104',4.8,12,0),('phase104',4.8,4,3.1)]:
        directory=work/name;directory.mkdir()
        for f in ('vectors.txt','expected.txt'):shutil.copyfile(work/f,directory/f)
        binary=directory/'sim.vvp'
        command=[find_tool('iverilog'),'-g2012','-s','tb_psx_mister_main_adapter',
                 '-Ptb_psx_mister_main_adapter.CORE_HALF=14.76255','-Ptb_psx_mister_main_adapter.MEM_HALF='+str(half),
                 '-Ptb_psx_mister_main_adapter.EXTRA='+str(extra),'-Ptb_psx_mister_main_adapter.MEM_PHASE='+str(phase),
                 '-o',str(binary),*map(str,sources)]
        with (directory/'compile.log').open('w') as log:subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        completed=subprocess.run([find_tool('vvp'),str(binary)],cwd=directory,capture_output=True,text=True,timeout=120)
        (directory/'run.log').write_text(completed.stdout+completed.stderr)
        if completed.returncode or 'PASS MISTER MAIN ADAPTER' not in completed.stdout:raise ValueError('Latency functional oracle failed: '+name)
        measured=measurements(directory/'latency.csv',rows,half,extra)
        (directory/'requests.json').write_text(json.dumps(measured,indent=2)+'\n')
        profiles.append(dict(name=name,mem_half_ns=half,extra_backend_cycles=extra,phase_ns=phase,
                             classes=summarize(measured),trace_sha256=digest(directory/'latency.csv'),binary_sha256=digest(binary),command=command))
        print(name,profiles[-1]['classes']['scalar-read'],flush=True)
    # Same compiled oracle: one extra backend cycle must be observed, not
    # silently absorbed by the measurement's configured-duration assumption.
    source=work/'floor104';fault=work/'delay-fault';fault.mkdir()
    for f in ('vectors.txt','expected.txt'):shutil.copyfile(work/f,fault/f)
    completed=subprocess.run([find_tool('vvp'),str(source/'sim.vvp'),'+FAULT_DELAY'],cwd=fault,capture_output=True,text=True,timeout=120)
    (fault/'run.log').write_text(completed.stdout+completed.stderr)
    if completed.returncode or 'PASS MISTER MAIN ADAPTER' not in completed.stdout:raise ValueError('Delay control lost functional correctness')
    try:measurements(fault/'latency.csv',rows,4.8,0)
    except ValueError as error:delay_fault_detected=str(error)=='Backend latency does not match configured delay'
    else:delay_fault_detected=False
    identity=all(digest(Path(path))==sha for path,sha in protected.items())
    report=dict(passed=identity and delay_fault_detected,source_identity_verified=identity,source_sha256=protected,input_archives=archives,
                profiles=profiles,delay_fault_detected=delay_fault_detected,vector_count=len(rows),upstream_events=len(gold),
                adapter=str(adapter),actual_CPU_execution=False,hardware_verified=False,game_verified=False,production_RTL_changed=False,
                limitations=['Synthetic single-client packet backend; no SDRAM/PSRAM pin timing or arbitration model',
                             'Primary measurement is isolated native requests; extra oracle tests cover queued DMA, CE, stopped clock, reset and posted order',
                             'Configured zero extra cycles still takes one memory-clock response handshake',
                             'Mailbox stages retain current latency; no synchronizer removed or clock relationship excepted',
                             'Native request occupancy differs from CPU stalls or retired-instruction throughput'])
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
