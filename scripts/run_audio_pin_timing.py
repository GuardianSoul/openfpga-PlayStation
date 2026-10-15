#!/usr/bin/env python3
"""Original and falling-MCLK I2S stream; four receiver phases and pin skew."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from prepare_apf_pin_timing import falling_audio,I2S_SHA
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/audio-pin-timing';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));original=ROOT/'rtl/audio/psx_audio_i2s.sv';bench=ROOT/'sim/pocket/tb_audio_pin_timing.sv'
    assert digest(original)==I2S_SHA
    protected={str(p):digest(p) for p in (original,bench,Path(__file__),ROOT/'scripts/prepare_apf_pin_timing.py')}
    text=original.read_text();golden=work/'golden.sv';golden.write_text(text.replace('module psx_audio_i2s (','module golden_audio_i2s ('))
    candidate=work/'candidate.sv';candidate.write_text(falling_audio(text));copied=work/'tb.sv';shutil.copyfile(bench,copied)
    baseline=work/'baseline.sv';baseline.write_text(text);runs=[]
    for name,source,clock,lrck,dac,corrupt,fault in [
        ('nominal',candidate,6,5.4,3,0,None),('opposite-skew',candidate,3,7,6,0,None),
        ('long-clock',candidate,9,2,7,0,None),('equal-flight',candidate,5,5,5,0,None),
        ('fault-original-setup',baseline,6,5.4,3,0,'Audio pin setup aperture'),
        ('fault-original-hold',baseline,6,6.4,3,0,'Audio pin hold aperture'),
        ('fault-lrck-late',candidate,6,410,3,0,'Audio decoded pair/channel mismatch'),
        ('fault-dac-bit',candidate,6,5.4,3,1,'Audio decoded pair/channel mismatch')]:
        case=work/name;case.mkdir();command=[find_tool('iverilog'),'-g2012','-s','tb','-o',str(case/'tb.vvp'),
            '-Ptb.CLK_DELAY='+str(clock),'-Ptb.LR_DELAY='+str(lrck),'-Ptb.DAC_DELAY='+str(dac),'-Ptb.CORRUPT='+str(corrupt),str(source),str(golden),str(copied)]
        c=subprocess.run(command,capture_output=True,text=True,timeout=30);(case/'compile.log').write_text(c.stdout+c.stderr);assert c.returncode==0,c.stderr
        p=subprocess.run([find_tool('vvp'),str(case/'tb.vvp')],capture_output=True,text=True,timeout=30)
        output=p.stdout+p.stderr;(case/'run.log').write_text(output)
        passed=p.returncode!=0 and fault in output if fault else p.returncode==0 and 'PASS AUDIO_PINS' in output
        runs.append(dict(name=name,passed=passed,fault=fault,exit_code=p.returncode,output=output,source_sha256=digest(source),command=command))
        print(name,passed,output.strip(),flush=True)
        if not passed:raise RuntimeError(output)
    assert all(digest(Path(p))==h for p,h in protected.items())
    (work/'report.json').write_text(json.dumps(dict(passed=True,protected=protected,runs=runs,
        scope='Digital serializer and delayed pins, four independent /4 receiver phases, setup/hold aperture, two reset epochs; not SPU/FMV/hardware validation',hardware_verified=False),indent=2)+'\n')
    print(work/'report.json',flush=True)


if __name__=='__main__':main()
