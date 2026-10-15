#!/usr/bin/env python3
"""Exercise the actual ordered report channel/FIFO and planted faults locally."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/video-reports-cdc';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    paths=[ROOT/'rtl/pocket/psx_video_reports_cdc.sv',
        ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        ROOT/'sim/pocket/tb_psx_video_reports_cdc.sv']
    protected={str(p):digest(p) for p in [*paths,Path(__file__)]};copies=[]
    for i,p in enumerate(paths):
        q=work/'inputs'/str(i)/p.name;q.parent.mkdir(parents=True);shutil.copyfile(p,q);copies.append(q)
        assert digest(q)==protected[str(p)]
    runs=[]
    def run(name,source,sh=9.31219,dh=14.76255,phase=0,fault=None,fifo=None):
        case=work/name;case.mkdir();binary=case/'tb.vvp'
        command=[find_tool('iverilog'),'-g2012','-s','tb',f'-Ptb.SH={sh}',f'-Ptb.DH={dh}',f'-Ptb.PHASE={phase}',
            '-o',str(binary),str(source),str(fifo or copies[1]),str(copies[2])]
        with (case/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        p=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=60)
        output=p.stdout+p.stderr;(case/'run.log').write_text(output)
        markers=fault if isinstance(fault,tuple) else (fault,)
        passed=p.returncode!=0 and any(marker in output for marker in markers) if fault else p.returncode==0 and 'PASS REPORTS_CDC' in output
        runs.append(dict(name=name,passed=passed,fault=fault,exit_code=p.returncode,output=output,source_sha256=digest(source),command=command))
        print(name,passed,output.strip(),flush=True)
        if not passed:raise RuntimeError(output)
    profiles=[('ntsc',9.31219,14.76255,0),('shifted',9.31219,14.76255,5.3),
            ('pal',9.39789,14.76255,3.8),('slow-destination',9.25,15,11.0),('fast-destination',9.5,14.5,1.1)]
    for name,sh,dh,phase in profiles:
        run(name,copies[0],sh,dh,phase)
    # Quartus may infer a registered read address with unregistered RAM
    # output. The just-exposed row must not be overwritten before the next
    # read. The FIFO's reserved slot and conservative read-pointer CDC protect
    # it; exercise that physical inference instead of assuming an output FF.
    fifo_text=copies[1].read_text()
    before='always @(posedge rd_clk_i)\n\tif (rd_en_i)\n\t\trd_data_o <= mem[rd_addr];'
    assert fifo_text.count(before)==1
    mapped=work/'mapped-address-fifo.sv'
    mapped.write_text(fifo_text.replace(before,'''reg [ADDR_WIDTH-1:0] exposed_addr;
always @(posedge rd_clk_i)
    if(rd_en_i)exposed_addr<=rd_addr;
always @* rd_data_o=mem[exposed_addr];'''))
    for name,sh,dh,phase in profiles:
        run('mapped-'+name,copies[0],sh,dh,phase,fifo=mapped)
    exposed_fault=work/'fault-exposed-row.sv'
    before='mem[wr_addr] <= wr_data_i;'
    assert mapped.read_text().count(before)==1
    exposed_fault.write_text(mapped.read_text().replace(before,'begin '+before+' mem[exposed_addr] <= wr_data_i; end'))
    run('fault-exposed-row',copies[0],fault=('Report levels changed without a committed packet','Report event order/data mismatch'),fifo=exposed_fault)
    raw=copies[0].read_text()
    for name,before,after,want in [
        ('packet-bit','.wr_data_i({src_dot,src_levels})',".wr_data_i({src_dot,src_levels} ^ 10'b1)",'Report event order/data mismatch'),
        ('live-level','read_data[8:0] : 9\'b0','src_levels : 9\'b0','Report levels changed without a committed packet'),
        ('hidden-fault','assign dst_fault=fault_sync;','assign dst_fault=0;','Missing synchronized/sticky report fault')]:
        assert raw.count(before)==1
        q=work/(name+'.sv');q.write_text(raw.replace(before,after));run('fault-'+name,q,fault=want)
    identity=all(digest(Path(p))==h for p,h in protected.items())
    report=dict(passed=identity and all(r['passed'] for r in runs),source_identity_verified=identity,
        protected_sha256=protected,runs=runs,hardware_verified=False,timing_verified=False,
        scope='Ordered report/tick packets, paired blank/level state, finite bursts, asynchronous clocks, resets, clock stops and explicit overflow')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
