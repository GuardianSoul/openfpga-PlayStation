#!/usr/bin/env python3
"""Concurrent CD/card descriptors through shared arbiter + pinned APF handler."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/apf-target-arb';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    sources=[ROOT/'rtl/pocket/psx_apf_target_arb.sv',ROOT/'sim/pocket/tb_apf_target_arb.sv',
             ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',ROOT/'references/MIRLO64/platform/pocket/common.v']
    protected={str(p):digest(p) for p in [*sources,Path(__file__),ROOT/'support/pocket_psx_data.json']}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_apf_target_arb','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_apf_target_arb'
    binary=build('original',sources);runs=[];faults=[]
    for case in range(2):
        result=subprocess.run([str(binary),f'+CASE={case}'],cwd=work,capture_output=True,text=True,timeout=120)
        output=result.stdout+result.stderr;log=work/f'case-{case}.log';log.write_text(output)
        passed=result.returncode==0 and output.count('PASS APF TARGET ARB')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
        runs.append(dict(case=case,passed=passed,exit_code=result.returncode,output=output,binary_sha256=digest(binary),log_sha256=digest(log)))
        print(case,passed,output,flush=True)
        if not passed:break
    if len(runs)==2 and all(r['passed'] for r in runs):
        for name,before,after,marker in [
            ('stale-done','if(done_low_seen && target_done)','if(target_done)','Client completion before host result'),
            ('live-payload','operation_write<=queued_write[selected]','operation_write<=p_write[selected]','Queued opcode mismatch'),
            ('wrong-result-owner','p_error[owner]<=target_error','p_error[owner^1]<=target_error','Result routed to wrong client')]:
            text=sources[0].read_text()
            if text.count(before)!=1:raise ValueError('Arbiter fault anchor changed')
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after))
            faulty=build(name,[variant,*sources[1:]])
            result=subprocess.run([str(faulty),'+CASE=0'],cwd=work,capture_output=True,text=True,timeout=120)
            output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
            rejected=result.returncode!=0 and marker in output
            faults.append(dict(name=name,rejected=rejected,exit_code=result.returncode,output=output,source_sha256=digest(variant),log_sha256=digest(log)))
            print(name,rejected,output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==2 and all(r['passed'] for r in runs) and len(faults)==3 and all(r['rejected'] for r in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        clients=2,reset_scope='FPGA power reset, not guest/client reset',
        actual_CD_memcard_clients_connected=False,full_top_connected=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
