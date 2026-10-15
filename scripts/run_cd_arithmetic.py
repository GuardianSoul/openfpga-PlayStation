#!/usr/bin/env python3
"""Exhaustively compare actual generated CD arithmetic with its original forms."""
import json
import subprocess

from prepare_cd_arithmetic import arithmetic_functions, cd_arithmetic_source
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    ghdl=find_tool('ghdl')
    if not ghdl: raise SystemExit('GHDL is required for CD arithmetic checks')
    directory=ROOT/'build/sim/cd-arithmetic'; directory.mkdir(parents=True,exist_ok=True)
    modified=cd_arithmetic_source(directory/'adapted')
    old,new,wrap=arithmetic_functions()
    contents=(directory/'adapted/cd_top.vhd').read_text()
    if new not in contents or wrap not in contents: raise SystemExit('Bench functions differ from generated RTL')
    package=directory/'cd_arithmetic.vhd'
    package.write_text('''-- GPL-2.0 extracted functions; preserve source license in COPYING.GPL2.
package cd_arithmetic is
    function originalSPT(lba : integer) return integer;
    function adaptedSPT(lba : integer) return integer;
    function wrapSectorOffset(old_offset : integer; sectors_per_track : integer) return integer;
end package;
package body cd_arithmetic is
    constant FRAMES_PER_MINUTE : integer := 4500;
'''+old.replace('getSectorsPerTrack','originalSPT')+'\n'+new.replace('getSectorsPerTrack','adaptedSPT')+'\n'+wrap+'\nend package body;\n')
    bench=ROOT/'sim/reference/tb_cd_arithmetic.vhd'; log=directory/'checks.log'
    output=[]
    with log.open('w') as file:
        for command in ([ghdl,'-a','--std=08',str(package),str(bench)],
                        [ghdl,'-e','--std=08','tb_cd_arithmetic'],
                        [ghdl,'-r','--std=08','tb_cd_arithmetic','--assert-level=error']):
            result=subprocess.run(command,cwd=directory,capture_output=True,text=True,timeout=60)
            file.write(result.stdout+result.stderr); output.append(result.stdout+result.stderr)
            if result.returncode: raise SystemExit(result.stdout+result.stderr)
    stdout=''.join(output); print(stdout,end='')
    passed='PASS CD arithmetic nonnegative_LBAs=524288 negative_LBAs=9001 offsets=480' in stdout
    (directory/'probe-report.json').write_text(json.dumps({'source':modified,
        'functions_sha256':digest(package),'bench_sha256':digest(bench),'stdout':stdout,'passed':passed,
        'track_sectors_exhaustive_call_domain':[0,524287],
        'ring_offset_exhaustive_domain':{'offset':[0,31],'sectors_per_track':[8,22]},
        'full_controller_execution_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed: raise SystemExit('CD arithmetic check counts incomplete')


if __name__=='__main__': main()
