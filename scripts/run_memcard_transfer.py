#!/usr/bin/env python3
"""Prove direct card load / staged APF target write-back with authored images."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/memcard-transfer';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    fifo_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    fifo=work/'fifo.sv';fifo.write_text(fifo_source.read_text().split('module tb_bios_probe;',1)[0])
    sources=[ROOT/'rtl/pocket/psx_memcard_transfer.sv',ROOT/'sim/pocket/tb_memcard_transfer.sv',ROOT/'rtl/pocket/psx_memcard_fabric.sv',
             ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_arb.sv',
             ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',
             ROOT/'references/MIRLO64/platform/pocket/common.v',fifo]
    protected={str(p):digest(p) for p in [*sources,fifo_source,Path(__file__),ROOT/'support/pocket_memcard_slots.json']}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_memcard_transfer','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_memcard_transfer'
    binary=build('original',sources);runs=[];faults=[]
    for case in range(7):
        run=subprocess.run([str(binary),f'+CASE={case}'],cwd=work,capture_output=True,text=True,timeout=120)
        log=work/f'case-{case}.log';log.write_text(run.stdout+run.stderr)
        passed=run.returncode==0 and log.read_text().count('PASS MEMCARD TRANSFER')==1 and not re.search(r'%Fatal|%Error|Assertion failed',log.read_text())
        runs.append(dict(case=case,passed=passed,exit_code=run.returncode,output=log.read_text(),log_sha256=digest(log),binary_sha256=digest(binary)))
        print(case,passed,log.read_text(),flush=True)
        if not passed:break
    if len(runs)==7 and all(r['passed'] for r in runs):
        for name,index,before,after,marker in [
            ('wrong-window',0,"25'h0080000","25'h0080002",'Card packet escaped logical window'),
            ('no-drain',0,'freeze_ack && memory_ready','memory_ready','Card freeze/drain bypass'),
            ('stale-done',0,'if(done_low_seen && target_done)','if(target_done)','Card save readback mismatch'),
            ('held-return-bypass',2,'freeze_memory && stopped_memory && route_idle','freeze_memory && route_idle','Held guest return bypassed drain')]:
            text=sources[index].read_text()
            if before not in text:raise ValueError('Fault anchor changed')
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after))
            selected=list(sources);selected[index]=variant
            faulty=build(name,selected)
            run=subprocess.run([str(faulty),'+CASE=0'],cwd=work,capture_output=True,text=True,timeout=120)
            log=work/(name+'.log');log.write_text(run.stdout+run.stderr)
            rejected=run.returncode!=0 and marker in log.read_text()
            faults.append(dict(name=name,rejected=rejected,exit_code=run.returncode,output=log.read_text(),source_sha256=digest(variant),log_sha256=digest(log)))
            print(name,rejected,log.read_text(),flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==7 and all(r['passed'] for r in runs) and len(faults)==4 and all(r['rejected'] for r in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        logical_byte_windows=[0x100000,0x200000],card_bytes=131072,staging_bytes=512,
        bridge_clock_MHz=74.25,host_word_spacing_clocks=88,physical_memory_verified=False,
        guest_card_protocol_verified=False,automatic_OS_readback_verified=False,
        full_top_connected=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
