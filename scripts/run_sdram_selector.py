#!/usr/bin/env python3
"""Prove actual SDRAM selector equivalence, then run native shared-memory tests.

Reuses the unchanged shared-SDRAM runner in a captured copy with exactly one
source substitution. No production RTL, source clock or PHY is changed.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from prepare_sdram_selector import prepare,selector_text
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/sdram-selector';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    original=ROOT/'rtl/pocket/psx_sdram_shared.sv';runner=ROOT/'scripts/run_sdram_shared.py'
    protected={};archives={}
    for p in [original,runner,Path(__file__).resolve(),ROOT/'scripts/prepare_sdram_selector.py',ROOT/'scripts/tool_paths.py',ROOT/'scripts/prepare_resource_targets.py']:
        sha=digest(p);copy=work/'input-archive'/p.name;copy.parent.mkdir(exist_ok=True);shutil.copyfile(p,copy)
        if digest(copy)!=sha:raise ValueError('Selector input changed during capture')
        protected[str(p)]=sha;protected[str(copy)]=sha;archives[str(p)]=dict(path=str(copy),sha256=sha)
    changed=prepare(original,work/'candidate',protected[str(original)])
    candidate=Path(changed['path']);_,old,new=selector_text(original.read_text())
    if candidate.read_text().count(new)!=1:raise ValueError('Actual selector body mismatch')
    ports='input [3:0] valid,input [1:0] round_robin,locked_owner,input locked,output reg [1:0] selected,output reg selected_valid'
    text='module golden('+ports+');\n'+old+'\nendmodule\nmodule actual('+ports+');\n'+new+'''\nendmodule
module miter(input [3:0] valid,input [1:0] rr,owner,input locked,output bad);
 wire [1:0] a,b;wire av,bv;
 golden x(valid,rr,owner,locked,a,av);actual y(valid,rr,owner,locked,b,bv);
 assign bad=(a!=b)||(av!=bv);
endmodule
'''
    source=work/'miter.sv';source.write_text(text)
    match=re.search(r"PICK_LO=64'h([0-9a-f]+)",text)
    if not match:raise ValueError('Lookup fault anchor missing')
    fault=work/'fault.sv';fault.write_text(text.replace(match[0],f"PICK_LO=64'h{int(match[1],16)^4:016x}"))
    yosys=find_tool('yosys') or str(ROOT/'build/tools/oss-cad-suite/bin/yosys');runs=[]
    for negative,p in [(False,source),(True,fault)]:
        log=p.with_suffix('.log');cmd=[yosys,'-Q','-T','-p',f'read_verilog -sv "{p}"; prep -top miter; flatten; sat -prove bad 0 -verify -show-inputs']
        with log.open('w') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,timeout=60)
        out=log.read_text();passed=(r.returncode!=0 and 'FAIL!' in out) if negative else (r.returncode==0 and 'SUCCESS!' in out)
        runs.append(dict(passed=passed,negative=negative,exit_code=r.returncode,source_sha256=digest(p),log_sha256=digest(log),command=cmd))
    if not all(r['passed'] for r in runs):raise ValueError('Selector SAT/fault failed')
    # Execute the existing native protocol/PHY fixture, including its own
    # wrong-tag and premature-idle controls; retain its actual source guards.
    text=runner.read_text();anchor="shared=ROOT/'rtl/pocket/psx_sdram_shared.sv'"
    if text.count(anchor)!=1:raise ValueError('Native SDRAM runner substitution changed')
    derived=work/'native_runner.py'
    text=text.replace(anchor,'shared=Path('+repr(str(candidate))+')')
    derived.write_text('import sys\nsys.path.insert(0,'+repr(str(ROOT/'scripts'))+')\n'+text)
    with (work/'native.log').open('w') as f:
        r=subprocess.run([sys.executable,str(derived)],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,timeout=600)
    output=(work/'native.log').read_text();matches=re.findall(r'^(/.+/report\.json)$',output,re.M)
    if r.returncode or not matches:raise ValueError('Native SDRAM selector test failed: '+str(work/'native.log'))
    report_path=Path(matches[-1]);native=json.loads(report_path.read_text())
    identity=all(digest(Path(p))==sha for p,sha in protected.items()) and digest(candidate)==changed['sha256']
    passed=identity and native['passed'] and native['source_identity_verified']
    (work/'report.json').write_text(json.dumps(dict(passed=passed,source_identity_verified=identity,source_sha256=protected,input_archives=archives,
        variant=changed,proof_runs=runs,binary_combinations=512,native_report=dict(path=str(report_path),sha256=digest(report_path)),native_runner_sha256=digest(derived),native_log_sha256=digest(work/'native.log'),
        hardware_verified=False,game_verified=False,fit_verified=False,
        scope='Exact512 selector states plus original tagged SDRAM pin fixture (2.5ns model), not actual CPU/SPU execution or board timing'),indent=2)+'\n')
    print('Selector SAT, fault and native protocol cases:',passed,flush=True);print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
