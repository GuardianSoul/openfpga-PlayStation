#!/usr/bin/env python3
"""Actual concurrent CD/card clients through pinned APF handler and table."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from psx_disc_host import metadata_words
from run_memcard_blank import reference
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/apf-services';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    blank=work/'blank.hex';data=reference()
    blank.write_text(''.join(f'{int.from_bytes(data[i:i+4],"little"):08x}\n' for i in range(0,len(data),4)))
    metadata=[]
    for name,sectors in (('meta-a.hex',40),('meta-b.hex',41)):
        path=work/name;path.write_text(''.join(f'{word:08x}\n' for word in metadata_words(sectors,region=2)))
        metadata.append(path)
    fifo_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    fifo=work/'fifo.sv';fifo.write_text(fifo_source.read_text().split('module tb_bios_probe;',1)[0])
    sources=[ROOT/'rtl/pocket'/name for name in (
        'psx_apf_services.sv','psx_cd_stream.sv','psx_memcard_service.sv','psx_memcard_transfer.sv',
        'psx_memcard_initialize.sv','psx_memcard_blank.sv','psx_dataslot_size.sv',
        'psx_apf_target_arb.sv','psx_apf_table_arb.sv','psx_memcard_fabric.sv')]
    sources += [ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_arb.sv',
        ROOT/'sim/pocket/tb_apf_services.sv',ROOT/'sim/pocket/mf_datatable_model.sv',
        ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        ROOT/'references/MIRLO64/platform/pocket/common.v',fifo]
    protected={str(p):digest(p) for p in (*sources,*metadata,blank,fifo_source,Path(__file__),
        ROOT/'scripts/run_memcard_blank.py',ROOT/'scripts/psx_disc_host.py',ROOT/'sim/pocket/apf_services_body.sv')}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '-I'+str(ROOT/'sim/pocket'),'--top-module','tb_apf_services','--Mdir',str(directory/'obj'),
                *map(str,selected)],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_apf_services'
    def run(binary,case):
        result=subprocess.run([str(binary),f'+CASE={case}',f'+BLANK={blank}',
            f'+META_A={metadata[0]}',f'+META_B={metadata[1]}'],cwd=work,capture_output=True,text=True,timeout=180)
        return result,result.stdout+result.stderr
    binary=build('original',sources);runs=[];faults=[]
    for case in (0,1,2,4,6):
        result,output=run(binary,case);log=work/f'case-{case}.log';log.write_text(output)
        passed=result.returncode==0 and output.count('PASS APF SERVICES')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
        runs.append(dict(case=case,passed=passed,exit_code=result.returncode,output=output,
            binary_sha256=digest(binary),log_sha256=digest(log)))
        print(case,passed,output[-2000:],flush=True)
        if not passed:break
    if len(runs)==5 and all(r['passed'] for r in runs):
        for name,before,after,case,marker in (
            ('crossed-cd-error','.target_error(client_error[0])','.target_error(client_error[1])',6,'Shared sector bytes/order mismatch'),
            ('no-cd-table-lease','.table_grant(table_grant[0])',".table_grant(1'b1)",0,'CD table advanced without ownership'),
            ('reload-reinitializes-cards','.initialize_request(initialize_request)',
             '.initialize_request(initialize_request || slot_update)',0,'Reload changed resident card service')):
            text=sources[0].read_text()
            if text.count(before)!=1:raise ValueError('APF composition fault anchor changed: '+name)
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after));selected=[variant,*sources[1:]]
            faulty=build(name,selected);result,output=run(faulty,case);log=work/(name+'.log');log.write_text(output)
            rejected=result.returncode!=0 and marker in output
            faults.append(dict(name=name,case=case,rejected=rejected,exit_code=result.returncode,output=output[-2000:],
                source_sha256=digest(variant),binary_sha256=digest(faulty),log_sha256=digest(log)))
            print(name,rejected,output[-1000:],flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==5 and all(r['passed'] for r in runs) and len(faults)==3 and all(f['rejected'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,faults=faults,
        actual_cd_and_card_clients_connected=True,pinned_apf_handler_connected=True,
        table_model='two-clock registered APF RAM',card_transport='actual asynchronous CDC/fabric with delayed packet backend and pin-drain indicator',
        authored_cd_sectors_per_case=12,card_bytes=131072,
        automatic_OS_readback_connected=False,actual_guest_CD_controller_connected=False,
        native_memory_pin_model_connected=False,full_top_connected=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
