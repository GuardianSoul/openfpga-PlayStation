#!/usr/bin/env python3
"""Build and test quiescent GTE image restoration and subsequent commands."""
import json
from pathlib import Path
import subprocess
from prepare_gte_save_adapter import save_adapter_sources, UPSTREAM
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

def main():
    directory=ROOT/'build/sim/gte-save-checks'; directory.mkdir(parents=True,exist_ok=True)
    sources=save_adapter_sources(directory/'sources')
    top=ROOT/'sim/reference/gte_save_top.sv'; harness=ROOT/'sim/reference/gte_save_probe.cpp'
    verilator=find_tool('verilator'); obj=directory/'obj'
    command=[verilator,'--cc','--exe','--build','-Wno-fatal','-j','4','--top-module','gte_save_top',
        '-I'+str(UPSTREAM/'rtl'),'--Mdir',str(obj),
        *[s['path'] for s in sources if s['assignment']=='SYSTEMVERILOG_FILE'],str(top),str(harness)]
    with (directory/'build.log').open('w') as log:
        subprocess.run(command,cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    result=subprocess.run([str(obj/'Vgte_save_top')],capture_output=True,text=True,timeout=60)
    output=result.stdout+result.stderr;(directory/'checks.log').write_text(output);print(output,end='')
    report={'passed':result.returncode==0 and 'roundtrips=256 commands=512 comparisons=49152' in output,
        'source_manifest':sources,'top_sha256':digest(top),'harness_sha256':digest(harness),
        'game_verified':False,'hardware_verified':False,'stdout':output}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
