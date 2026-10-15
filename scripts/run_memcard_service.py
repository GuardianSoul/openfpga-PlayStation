#!/usr/bin/env python3
"""Full authored card images and first-time blank cards through APF write-back."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from run_memcard_blank import reference
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/memcard-service';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    blank=work/'blank.hex';data=reference()
    blank.write_text(''.join(f'{int.from_bytes(data[i:i+4],"little"):08x}\n' for i in range(0,len(data),4)))
    fifo_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    fifo=work/'fifo.sv';fifo.write_text(fifo_source.read_text().split('module tb_bios_probe;',1)[0])
    sources=[ROOT/'rtl/pocket/psx_memcard_service.sv',ROOT/'rtl/pocket/psx_memcard_transfer.sv',
        ROOT/'rtl/pocket/psx_memcard_initialize.sv',ROOT/'rtl/pocket/psx_memcard_blank.sv',
        ROOT/'rtl/pocket/psx_dataslot_size.sv',ROOT/'rtl/pocket/psx_apf_target_arb.sv',
        ROOT/'rtl/pocket/psx_memcard_fabric.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_arb.sv',
        ROOT/'sim/pocket/tb_memcard_service.sv',ROOT/'sim/pocket/mf_datatable_model.sv',
        ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',ROOT/'references/MIRLO64/platform/pocket/common.v',fifo]
    protected={str(p):digest(p) for p in [*sources,blank,fifo_source,Path(__file__),ROOT/'scripts/run_memcard_blank.py']}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_memcard_service','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_memcard_service'
    def run(binary,case):
        return subprocess.run([str(binary),f'+CASE={case}',f'+BLANK={blank}'],cwd=work,capture_output=True,text=True,timeout=180)
    binary=build('original',sources);runs=[];faults=[]
    for case in range(6):
        result=run(binary,case);output=result.stdout+result.stderr;log=work/f'case-{case}.log';log.write_text(output)
        passed=result.returncode==0 and output.count('PASS MEMCARD SERVICE')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
        runs.append(dict(case=case,passed=passed,exit_code=result.returncode,output=output,binary_sha256=digest(binary),log_sha256=digest(log)))
        print(case,passed,output,flush=True)
        if not passed:break
    if len(runs)==6 and all(r['passed'] for r in runs):
        for name,index,before,after,marker in (
            ('missing-adoption',0,'.initialization_commit(commit)',".initialization_commit(1'b0)",'Initialized images not adopted'),
            ('ownership-gap',0,'transfer_freeze || init_freeze || adopting','transfer_freeze || init_freeze','Ownership released before adoption'),
            ('target-ready-bypass',1,'if(!TARGET_BACKPRESSURE || target_ready)','if(1\'b1)','Card publish bypassed target readiness')):
            text=sources[index].read_text()
            if text.count(before)!=1:raise ValueError('Service fault anchor changed')
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after));selected=list(sources);selected[index]=variant
            faulty=build(name,selected);result=run(faulty,0);output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
            rejected=result.returncode!=0 and marker in output
            faults.append(dict(name=name,rejected=rejected,exit_code=result.returncode,output=output,source_sha256=digest(variant),log_sha256=digest(log)))
            print(name,rejected,output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    positives=identity and len(runs)==6 and all(r['passed'] for r in runs)
    report=dict(passed=positives and len(faults)==3 and all(f['rejected'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        logical_byte_windows=[0x100000,0x200000],card_bytes=131072,staging_bytes=512,
        blank_first_export_verified=positives,loaded_card_preserved=positives,
        failed_export_retry_verified=positives,transport='asynchronous CDC/arbiter + delayed packet backend/pin-drain indicator',
        physical_memory_verified=False,automatic_OS_readback_verified=False,guest_card_protocol_verified=False,
        actual_CD_client_connected=False,full_top_connected=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
