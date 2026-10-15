#!/usr/bin/env python3
"""Check the explicit bit encodings in the connected simulation copies."""
import json
import subprocess

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    ghdl=find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for system encoding checks')
    directory=ROOT/'build/sim/system-encodings'; directory.mkdir(parents=True,exist_ok=True)
    bench=ROOT/'sim/reference/tb_system_encodings.vhd'; output=[]
    for command in ([ghdl,'-a','--std=08',str(bench)],
                    [ghdl,'-e','--std=08','tb_system_encodings'],
                    [ghdl,'-r','--std=08','tb_system_encodings','--assert-level=error']):
        result=subprocess.run(command,cwd=directory,capture_output=True,text=True,timeout=60)
        output.append(result.stdout+result.stderr)
        if result.returncode: raise SystemExit(''.join(output))
    stdout=''.join(output); (directory/'checks.log').write_text(stdout); print(stdout,end='')
    passed='PASS system encodings analog=256 lines=484866 positions=1048576' in stdout
    (directory/'report.json').write_text(json.dumps({'bench_sha256':digest(bench),'stdout':stdout,
        'passed':passed,'system_execution_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed: raise SystemExit('System encoding check counts incomplete')


if __name__=='__main__': main()
