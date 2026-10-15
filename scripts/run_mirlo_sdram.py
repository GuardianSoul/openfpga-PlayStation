#!/usr/bin/env python3
"""Compare the hardware-selected MIRLO PHY to the experimental pin-model paths.

The nominal byte oracle is a required pass. The maximum-access-time model is
reported independently; no failing corner is relabelled as timing closure.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from memory_vectors import write_vectors
from prepare_mirlo_sdram_phy import prepare
from prepare_pocket_read_pipeline import prepare as prepare_retimed
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/mirlo-sdram';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    mirlo=prepare(work/'mirlo');retimed=prepare_retimed(work/'retimed')
    vector_file=work/'vectors.txt';count=write_vectors(vector_file)
    bench=ROOT/'sim/memory/tb_psx_mem_bridge.sv'
    common=[ROOT/'rtl/memory'/n for n in ('psx_mem_bridge.sv','psx_sdram.sv','psx_sdram_pins.sv')]
    common += [ROOT/'rtl/debug/psx_mem_profile.sv',bench,
        *[ROOT/'sim/memory'/n for n in ('sdr_command_model.sv','sdr_pin_model.sv','altddio_clock_model.sv')],
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv']
    choices={'mirlo':Path(mirlo['path']),'falling':ROOT/'rtl/memory/psx_sdr_phy.sv','retimed':Path(retimed['path'])}
    protected={str(p):digest(p) for p in [*common,*choices.values(),vector_file,Path(__file__),
        ROOT/'scripts/prepare_mirlo_sdram_phy.py',ROOT/'scripts/prepare_pocket_read_pipeline.py',
        ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv']}
    runs=[]
    for name,phy in choices.items():
        for access in (2.5,5.5):
            label=f'{name}-{access}';binary=work/(label+'.vvp')
            command=[find_tool('iverilog'),'-g2012','-s','tb_psx_mem_bridge','-Ptb_psx_mem_bridge.CONTROLLER=2',
                f'-Ptb_psx_mem_bridge.PIN_T_AC={access}',f'-Ptb_psx_mem_bridge.PIN_T_AC_SECOND={access}',
                f'-Ptb_psx_mem_bridge.PIN_T_HZ={2.5 if access==2.5 else 6.0}',
                '-o',str(binary),*map(str,common),str(phy)]
            with (work/(label+'-build.log')).open('w') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
            result=subprocess.run([find_tool('vvp'),str(binary),'+vectors='+str(vector_file)],
                cwd=work,capture_output=True,text=True,timeout=120)
            output=result.stdout+result.stderr;log=work/(label+'.log');log.write_text(output)
            passed=result.returncode==0 and f'transactions={count}' in output and 'FATAL' not in output
            runs.append(dict(variant=name,access_ns=access,passed=passed,exit_code=result.returncode,output=output,
                binary_sha256=digest(binary),log_sha256=digest(log)))
            print(label,passed,output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    original=(ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv').read_text()
    renamed_exact=Path(mirlo['path']).read_text().split('\n',1)[1]==original.replace('module sdr_phy (','module psx_sdr_phy (')
    nominal=all(r['passed'] for r in runs if r['access_ns']==2.5)
    report=dict(passed=identity and renamed_exact and nominal,source_identity_verified=identity,
        upstream_rename_exact=renamed_exact,source_sha256=protected,mirlo=mirlo,retimed=retimed,runs=runs,
        transactions_per_fixture=count,nominal_model_passed=nominal,
        all_model_corners_passed=all(r['passed'] for r in runs),
        hardware_selection='Original MIRLO +62.5/125 MHz/4ns: user-reported full BIOS CRC pass; slow retimed pair fails',
        new_project_hardware_verified=False,timing_verified=False,
        limitation='The existing model accepts experiments that fail on the board; model and physical calibration remain unresolved')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
