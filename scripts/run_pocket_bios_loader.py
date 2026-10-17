#!/usr/bin/env python3
"""Exercise complete authored BIOS writes, reload freeze and failure contracts."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    verilator=find_tool('verilator')
    if not verilator:raise ValueError('Verilator required')
    parent=ROOT/'build/sim/pocket-bios-loader';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    model_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    model=work/'fifo.sv';model.write_text(model_source.read_text().split('module tb_bios_probe;',1)[0])
    sources=[ROOT/'rtl/pocket/psx_bios_loader.sv',ROOT/'rtl/pocket/psx_boot_gate.sv',
             ROOT/'sim/pocket/tb_bios_loader.sv',model]
    protected={str(p):digest(p) for p in (*sources,model_source,Path(__file__))}
    def build(directory, selected, params=()):
        directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_bios_loader','--Mdir',str(directory/'obj'),*params,
                *map(str,selected)],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_bios_loader'
    binary=build(work/'original',sources)
    # Per-region BIOS slots (0x20-0x2f, Chip32 loadf): a full load from slot
    # 0x25 and a rejected foreign slot, plus the foreign-slot case of the
    # single-slot build.
    REGIONS=('-GLOADER_ID=32','-GLOADER_MASK=65520')
    regional=build(work/'regions',sources,REGIONS)
    plan=[(binary,case,0) for case in range(15)]+[(regional,0,0x25),(regional,14,0x22)]
    runs=[]
    for index,(selected,case,slot) in enumerate(plan):
        directory=work/f'run{index}-case{case}';directory.mkdir()
        run=subprocess.run([str(selected),f'+CASE={case}',f'+SLOT={slot}'],cwd=work,capture_output=True,text=True,timeout=120)
        log=directory/'run.log';log.write_text(run.stdout+run.stderr)
        passed=run.returncode==0 and log.read_text().count('PASS BIOS loader')==1 and not re.search(
            r'%Fatal|%Error|Assertion failed',log.read_text())
        runs.append({'case':case,'slot':slot,'regional':selected==regional,'passed':passed,'exit_code':run.returncode,
                     'stdout':log.read_text(),'binary_sha256':digest(selected),'log_sha256':digest(log)})
        print(case,log.read_text(),flush=True)
        if not passed:break
    faults=[]
    if len(runs)==len(plan) and all(r['passed'] for r in runs):
        original=sources[0].read_text()
        mutations=(
            ('early-ready', 'if(allcomplete && (session==ACK_SLOT || session==ACTIVE)) complete_seen<=1;',
                'if(allcomplete && (session==ACK_SLOT || session==ACTIVE)) begin complete_seen<=1;ready<=1;end',0,
                'BIOS ready before final acknowledged write'),
            ('no-freeze', 'session==FREEZE && freeze_ack==freeze_toggle && memory_ready',
                'session==FREEZE && memory_ready',10,'Stopped CPU clock bypassed freeze'),
            ('byte-swap', 'data<=fifo_q;', 'data<={fifo_q[7:0],fifo_q[15:8],fifo_q[23:16],fifo_q[31:24]};',0,
                'BIOS little-endian data mismatch'),
            ('wrong-address', 'address<=committed_words[16:0];','address<=committed_words[16:0]+1;',0,
                'BIOS write address/mask/order'),
            ('mask-ignored','(slot_id&BIOS_SLOT_MASK)==(BIOS_SLOT_ID&BIOS_SLOT_MASK)','slot_id==BIOS_SLOT_ID',0,
                'BIOS loader timeout',REGIONS,0x25),
            ('any-slot','(slot_id&BIOS_SLOT_MASK)==(BIOS_SLOT_ID&BIOS_SLOT_MASK)','1',14,
                'Foreign slot acknowledged',REGIONS,0x22))
        for name,before,after,case,marker,*extra in mutations:
            params,slot=extra if extra else ((),0)
            if original.count(before)!=1:raise ValueError('loader fault anchor changed: '+name)
            directory=work/name;directory.mkdir()
            variant=directory/'psx_bios_loader.sv';variant.write_text(original.replace(before,after))
            faulty=build(directory/'build',[variant,*sources[1:]],params)
            run=subprocess.run([str(faulty),f'+CASE={case}',f'+SLOT={slot}'],cwd=work,capture_output=True,text=True,timeout=120)
            log=directory/'run.log';log.write_text(run.stdout+run.stderr)
            rejected=run.returncode!=0 and marker in log.read_text()
            faults.append({'name':name,'rejected':rejected,'exit_code':run.returncode,
                'marker':marker,'source_sha256':digest(variant),'binary_sha256':digest(faulty),
                'log_sha256':digest(log),'stdout':log.read_text()})
            print(name,rejected,log.read_text(),flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report={'passed':len(runs)==len(plan) and all(r['passed'] for r in runs) and len(faults)==6
                    and all(f['rejected'] for f in faults) and identity,
            'source_identity_verified':identity,'source_sha256':protected,'runs':runs,'faults':faults,
            'fixture':'authored image only, no BIOS/game assets','CPU_execution_verified':False,
            'vendor_FIFO_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
