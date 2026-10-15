#!/usr/bin/env python3
"""Exhaust the VRAM wrap arithmetic's bounded X/rounded-length domain."""
import json
from pathlib import Path
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    ghdl=find_tool('ghdl')
    if not ghdl:raise SystemExit('GHDL required')
    folder=ROOT/'build/sim/gpu-read-math';folder.mkdir(parents=True,exist_ok=True)
    package=ROOT/'rtl/gpu/psx_gpu_read_math.vhd';bench=ROOT/'sim/reference/tb_gpu_read_math.vhd'
    protected={str(p):digest(p) for p in (package,bench)}
    output=[]
    for cmd in ([ghdl,'-a','--std=08',str(package),str(bench)],
                [ghdl,'-r','--std=08','tb_gpu_read_math','--assert-level=error']):
        run=subprocess.run(cmd,cwd=folder,capture_output=True,text=True,timeout=60)
        output.append(run.stdout+run.stderr)
        if run.returncode:raise SystemExit(''.join(output))
    stdout=''.join(output);print(stdout,end='')
    identity=all(digest(Path(p))==s for p,s in protected.items())
    passed=identity and 'PASS GPU read-wrap comparisons=1049600' in stdout and 'PASS GPU read rounding comparisons=8192' in stdout
    (folder/'report.json').write_text(json.dumps({'passed':passed,'source_identity_verified':identity,
        'protected_sha256':protected,'stdout':stdout,'comparisons':1049600,'rounding_comparisons':8192,
        'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed:raise SystemExit('GPU wrap check incomplete')


if __name__=='__main__':main()
