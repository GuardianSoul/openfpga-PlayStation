#!/usr/bin/env python3
"""Check the actually assigned timer's strobe interface, including IRQ/gating."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from prepare_video_reports_cdc import STROBE,EDGE
from tool_paths import ROOT,find_tool


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-timer-sha256',required=True);args=p.parse_args()
    project=args.project.resolve();choices=[s for kind,s in assignments(project,(project/'apf_top.qsf').read_text()) if kind=='VHDL_FILE' and s.name=='timer.vhd']
    if len(choices)!=1 or digest(choices[0])!=args.expected_timer_sha256:raise ValueError('Expected one identity-checked assigned timer')
    timer=choices[0];text=timer.read_text()
    if text.count(STROBE)!=1 or EDGE in text:raise ValueError('Strobe timer required')
    parent=ROOT/'build/sim/video-reports-timer';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    bench=ROOT/'sim/pocket/tb_psx_video_reports_timer.vhd';protected={str(p):digest(p) for p in (timer,bench,Path(__file__))};runs=[]
    for name,code,fault in [('candidate',text,None),('fault-edge-detector',text.replace(STROBE,EDGE),'Adjacent tick strobes were lost')]:
        case=work/name;case.mkdir();source=case/'timer.vhd';source.write_text(code);copied=case/bench.name;shutil.copyfile(bench,copied)
        with (case/'build.log').open('w') as log:
            for command in ([find_tool('ghdl'),'-a','--std=08',str(source),str(copied)],
                            [find_tool('ghdl'),'-e','--std=08','tb_video_reports_timer']):
                subprocess.run(command,cwd=case,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        command=[find_tool('ghdl'),'-r','--std=08','tb_video_reports_timer','--assert-level=error','--stop-time=1ms']
        run=subprocess.run(command,cwd=case,capture_output=True,text=True,timeout=30)
        output=run.stdout+run.stderr;(case/'run.log').write_text(output)
        accepted=run.returncode!=0 and fault in output if fault else run.returncode==0 and 'PASS REPORTS_TIMER' in output
        runs.append(dict(name=name,accepted=accepted,exit_code=run.returncode,fault=fault,output=output,source_sha256=digest(source)))
        print(name,accepted,flush=True)
        if not accepted:raise RuntimeError(output)
    assert all(digest(Path(p))==sha for p,sha in protected.items())
    (work/'report.json').write_text(json.dumps(dict(passed=True,protected_sha256=protected,runs=runs,hardware_verified=False,
        scope='Actual assigned timer, adjacent strobes, CE stalls, HBlank gate/reset modes, wrap, target IRQ/block-next and system-clock mode; no full timer/game equivalence claim'),indent=2)+'\n')
    print(work/'report.json',flush=True)


if __name__=='__main__':main()
