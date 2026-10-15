#!/usr/bin/env python3
"""Pinned APF command handler + release loader/gate at the bridge boundary."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/apf-bios-boot';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    model_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    fifo=work/'fifo.sv';fifo.write_text(model_source.read_text().split('module tb_bios_probe;',1)[0])
    sources=[ROOT/'rtl/pocket/psx_bios_loader.sv',ROOT/'rtl/pocket/psx_boot_gate.sv',
        ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        ROOT/'references/MIRLO64/platform/pocket/common.v',
        ROOT/'sim/pocket/tb_apf_bios_boot.sv',fifo]
    protected={str(p):digest(p) for p in (*sources,model_source,Path(__file__))}
    runs=[]
    for name,parameters,marker in (
        ('original',[],'PASS APF BIOS boot'),
        ('early-setup',['-GEARLY_SETUP=1'],'Premature APF Ready to Run'),
        ('wrong-endian',['-GLITTLE=0'],'APF command result')):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_apf_bios_boot','--Mdir',str(directory/'obj'),
                *parameters,*map(str,sources)],cwd=work,stdout=log,stderr=subprocess.STDOUT,
                check=True,timeout=300)
        binary=directory/'obj/Vtb_apf_bios_boot'
        run=subprocess.run([str(binary)],cwd=work,capture_output=True,text=True,timeout=120)
        log=directory/'run.log';log.write_text(run.stdout+run.stderr)
        passed=(run.returncode==0 and log.read_text().count(marker)==1 and
                not re.search(r'%Fatal|%Error|Assertion failed',log.read_text())) if name=='original' else (
                run.returncode!=0 and marker in log.read_text())
        runs.append({'name':name,'passed':passed,'parameters':parameters,'marker':marker,
            'exit_code':run.returncode,'binary_sha256':digest(binary),
            'log_sha256':digest(log),'stdout':log.read_text()})
        print(name,passed,log.read_text(),flush=True)
        if not passed:break
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report={'passed':identity and len(runs)==3 and all(r['passed'] for r in runs),
        'source_identity_verified':identity,'source_sha256':protected,'runs':runs,
        'bridge_boundary_verified':True,'serial_bridge_PHY_verified':False,
        'CPU_execution_verified':False,'vendor_FIFO_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
