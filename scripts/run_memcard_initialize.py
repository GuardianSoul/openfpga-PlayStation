#!/usr/bin/env python3
"""Absent-card fill + loaded-card preservation + APF size registration proof."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from run_memcard_blank import reference
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/memcard-initialize';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    blank=work/'blank.hex';blank.write_text(''.join(f'{b:02x}\n' for b in reference()))
    sources=[ROOT/'rtl/pocket/psx_memcard_initialize.sv',ROOT/'rtl/pocket/psx_dataslot_size.sv',
             ROOT/'rtl/pocket/psx_memcard_blank.sv',ROOT/'sim/pocket/tb_memcard_initialize.sv',
             ROOT/'rtl/pocket/psx_apf_table_arb.sv']
    protected={str(p):digest(p) for p in [*sources,ROOT/'scripts/run_memcard_blank.py',blank,Path(__file__)]}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_memcard_initialize','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_memcard_initialize'
    binary=build('original',sources);runs=[];faults=[]
    def run(binary,case):
        return subprocess.run([str(binary),f'+CASE={case}',f'+BLANK={blank}'],cwd=work,capture_output=True,text=True,timeout=120)
    for case in range(11):
        result=run(binary,case);output=result.stdout+result.stderr
        log=work/f'case-{case}.log';log.write_text(output)
        passed=result.returncode==0 and output.count('PASS MEMCARD INITIALIZE')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
        runs.append(dict(case=case,passed=passed,exit_code=result.returncode,output=output,binary_sha256=digest(binary),log_sha256=digest(log)))
        print(case,passed,output,flush=True)
        if not passed:break
    if len(runs)==11 and all(r['passed'] for r in runs):
        for name,index,before,after,case,marker in [
            ('early-mount',0,'assign mounted=ready && !failed ? initialized : 2\'b00;',"assign mounted=freeze ? 2'b11:2'b00;",0,'Initialization bypassed guest drain'),
            ('short-fill',0,'if(index==16383)','if(index==16382)',0,'Size registration before complete physical image'),
            ('bad-stride',0,"+{9'b0,index,2'b0}","+{10'b0,index,1'b0}",0,'Blank cursor/order mismatch'),
            ('wrong-table-index',1,"datatable_addr={4'b0,index,size_address}",
                "datatable_addr={4'b0,(state==WRITE_SIZE || state==COMMIT ? wanted_id[4:0]:index),size_address}",2,'Wrong APF table position')]:
            text=sources[index].read_text()
            if text.count(before)!=1:raise ValueError('Initialization fault anchor changed: '+name)
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after));selected=list(sources);selected[index]=variant
            faulty=build(name,selected);result=run(faulty,case);output=result.stdout+result.stderr
            log=work/(name+'.log');log.write_text(output)
            rejected=result.returncode!=0 and marker in output
            faults.append(dict(name=name,rejected=rejected,exit_code=result.returncode,output=output,source_sha256=digest(variant),log_sha256=digest(log)))
            print(name,rejected,output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==11 and all(r['passed'] for r in runs) and len(faults)==4 and all(r['rejected'] for r in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        blank_words_per_card=16384,word_bits=64,card_bytes=131072,
        logical_byte_windows=[0x100000,0x200000],table_lookup_by_ID=True,
        physical_memory_verified=False,full_top_connected=False,automatic_OS_readback_verified=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
