#!/usr/bin/env python3
"""Exercise real CPU reset gating, asynchronous blank delivery and APF words."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    base=ROOT/'build/sim/apf-blank-cdc';base.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=base));archive=work/'input-archive';archive.mkdir()
    source=[ROOT/p for p in ['rtl/pocket/psx_video_blank_cdc.sv','rtl/pocket/psx_boot_gate.sv',
                            'rtl/pocket/psx_apf_video.sv','sim/pocket/tb_apf_blank_cdc.sv']]
    originals=source+[Path(__file__).resolve(),ROOT/'scripts/tool_paths.py',ROOT/'scripts/prepare_resource_targets.py']
    hashes={str(p):digest(p) for p in originals};copies=[]
    for p in originals:
        c=archive/p.name;shutil.copyfile(p,c);assert digest(c)==hashes[str(p)];copies.append(c)
    runs=[]
    variants=[(f'profile-{p}',p,None) for p in range(4)]+[
        ('fault-first-bypass',1,('blank_sync<=blank_meta;','blank_sync<=blank_async;')),
        ('fault-stuck-blank',1,('blank_video<=blank_sync;',"blank_video<=1'b1;"))]
    for label,profile,fault in variants:
        files=copies[:4]
        if fault:
            text=copies[0].read_text();assert text.count(fault[0])==1
            altered=work/(label+'.sv');altered.write_text(text.replace(*fault));files[0]=altered
        binary=work/label
        build=subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_apf_blank_cdc',
            f'-Ptb_apf_blank_cdc.PROFILE={profile}','-o',str(binary),*map(str,files)],capture_output=True,text=True,timeout=60)
        if build.returncode:raise RuntimeError(build.stdout+build.stderr)
        result=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=60)
        output=result.stdout+result.stderr;log=work/(label+'.log');log.write_text(output)
        passed=(result.returncode==0 and 'PASS APF_BLANK' in output) if not fault else (
            result.returncode!=0 and ('outside CDC latency bound' in output if label=='fault-first-bypass' else 'Insufficient APF coverage' in output))
        runs.append(dict(label=label,passed=passed,fault=fault is not None,exit_code=result.returncode,
            source_sha256=digest(files[0]),log=str(log),log_sha256=digest(log),output=output))
        print(label,'PASS' if passed else 'FAIL',flush=True)
    unchanged=all(digest(Path(p))==h for p,h in hashes.items())
    report=dict(passed=unchanged and all(r['passed'] for r in runs),source_identity_verified=unchanged,
        source_sha256=hashes,input_archives=[dict(path=str(p),sha256=digest(p)) for p in copies],runs=runs,
        hardware_verified=False,analog_metastability_modeled=False,
        scope='Real boot-gate pulses, video latency bounds, stall-independent delivery, APF timing/skip/scaler metadata and blanked/unblanked pixels')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
