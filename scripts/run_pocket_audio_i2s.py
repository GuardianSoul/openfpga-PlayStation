#!/usr/bin/env python3
"""Cycle-compare native Pocket I2S framing and decode the signed channels."""
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/pocket-audio-i2s';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    rtl=ROOT/'rtl/audio/psx_audio_i2s.sv';bench=ROOT/'sim/pocket/tb_audio_i2s.sv'
    native=ROOT/'references/MIRLO64/target/pocket/sound_i2s.sv'
    protected={str(p):digest(p) for p in (rtl,bench,native,Path(__file__))}
    def run(name,path):
        binary=work/(name+'.vvp')
        subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_audio_i2s','-o',str(binary),str(path),str(native),str(bench)],check=True,timeout=60)
        result=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=60)
        output=result.stdout+result.stderr;(work/(name+'.log')).write_text(output);print(name+': '+output,end='',flush=True)
        return {'exit_code':result.returncode,'stdout':output,'log_sha256':digest(work/(name+'.log')),
                'passed':result.returncode==0 and 'PASS audio I2S' in output}
    original=run('original',rtl)
    faulty=work/'faulty.sv';faulty.write_text(rtl.read_text().replace('shifter<=frame;','shifter<={frame[15:0],frame[31:16]};'))
    fault=run('wrong-channel',faulty);fault['rejected']=fault['exit_code']!=0 and 'I2S upstream cycle mismatch' in fault['stdout']
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report={'passed':original['passed'] and fault['rejected'] and identity,'source_identity_verified':identity,
            'protected_sha256':protected,'rtl':original,'mutation':fault,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work,flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
