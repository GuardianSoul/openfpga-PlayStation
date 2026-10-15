#!/usr/bin/env python3
"""Actual reverse request wrapper/FIFO: ordering, clock/reset, fault controls."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/video-request-cdc';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    paths=[ROOT/'rtl/pocket/psx_video_request_cdc.sv',ROOT/'rtl/pocket/psx_video_config_cdc.sv',
           ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',ROOT/'sim/pocket/tb_psx_video_request_cdc.sv']
    protected={str(p):digest(p) for p in [*paths,Path(__file__)]};copies=[]
    for i,p in enumerate(paths):
        q=work/'inputs'/str(i)/p.name;q.parent.mkdir(parents=True);shutil.copyfile(p,q);copies.append(q)
        assert digest(q)==protected[str(p)]
    runs=[]
    def run(name,source,sh=9.31219,dh=7.381275,phase=0,fault=None):
        case=work/name;case.mkdir();binary=case/'tb.vvp'
        with (case/'build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb',f'-Ptb.SH={sh}',f'-Ptb.DH={dh}',f'-Ptb.PHASE={phase}',
                '-o',str(binary),str(source),*[str(p) for p in copies[1:]]],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        p=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=60)
        output=p.stdout+p.stderr;(case/'run.log').write_text(output)
        passed=p.returncode!=0 and fault in output if fault else p.returncode==0 and 'PASS REQUEST_CDC' in output
        runs.append(dict(name=name,passed=passed,fault=fault,exit_code=p.returncode,output=output,source_sha256=digest(source)))
        print(name,passed,flush=True)
        if not passed:raise RuntimeError(output)
    for name,sh,dh,phase in [('ntsc',9.31219,7.381275,0),('shifted',9.31219,7.381275,5.37),
            ('pal',9.39789,7.381275,3.8),('margin',9.6,8,4.2),('fast',9.9,6,1.1)]:
        run(name,copies[0],sh,dh,phase)
    raw=copies[0].read_text()
    for name,before,after,want in [
        ('field-bit','.src_data(src_data)','.src_data(src_data ^ (39\'b1<<30))','Request order/data mismatch'),
        ('live-field','.dst_data(dst_data)','.dst_data(held_data)','Request order/data mismatch'),
        ('hidden-fault','assign dst_fault=fault_sync;','assign dst_fault=0;','Missing synchronized/latching request fault')]:
        assert raw.count(before)==1
        changed=raw.replace(before,after)
        if name=='live-field':changed=changed.replace('    psx_video_config_cdc #(',"    wire [38:0] held_data;\n    assign dst_data={src_data[38:28],held_data[27:0]};\n    psx_video_config_cdc #(")
        q=work/(name+'.sv');q.write_text(changed);run('fault-'+name,q,fault=want)
    identity=all(digest(Path(p))==h for p,h in protected.items())
    report=dict(passed=identity and all(r['passed'] for r in runs),source_identity_verified=identity,
                protected_sha256=protected,runs=runs,hardware_verified=False,timing_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
