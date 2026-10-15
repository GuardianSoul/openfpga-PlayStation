#!/usr/bin/env python3
"""Check the Native SDRAM's observed CAS/rate, independently of its RAM model.

Default is a strict gate: current CL2 at 101.6 MHz exits nonzero. --self-test
also checks detector controls and reports the actual result without calling a
known spec failure a passing memory interface. Mode-only patched copies are
monitor controls; they do not validate the controller's read pipeline.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_native_sdram_io import controller_contract,project_contract
from prepare_resource_targets import digest
from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool


def observe(work, source, half_period=4.9205, cas_override=None):
    work.mkdir()
    (work/'controller.sv').write_text(source)
    (work/'altddio_out.sv').write_text(ALTDDIO)
    bench = work/'tb_native_sdram_spec.sv'
    bench.write_bytes((ROOT/'sim/memory/tb_native_sdram_spec.sv').read_bytes())
    sources = [work/'controller.sv',work/'altddio_out.sv',bench]
    parameters=[]
    if cas_override is not None:
        parameters=['-DNATIVE_SPEC_PARAMETERIZED_CAS',
                    f'-Ptb_native_sdram_spec.ACTIVE_CAS={cas_override}']
    identities = {str(p):digest(p) for p in sources}
    with (work/'compile.log').open('w') as log:
        subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_native_sdram_spec',
                        '-o',str(work/'tb.vvp'),*parameters,*map(str,sources)],stdout=log,stderr=subprocess.STDOUT,check=True)
    run = subprocess.run([find_tool('vvp'),'-n',str(work/'tb.vvp'),
                          f'+half_period={half_period}'],capture_output=True,text=True,timeout=20)
    (work/'sim.log').write_text(run.stdout+run.stderr)
    if any(digest(Path(p))!=sha for p,sha in identities.items()):
        raise ValueError('Frozen monitor sources changed during simulation')
    match = re.search(r'NATIVE_SPEC MRS=([0-9a-f]+) CAS=(\d+) PERIOD_NS=([\d.]+) MIN_NS=([\d.]+) CONFORMANT=([01])',run.stdout)
    complete = run.returncode==0 and 'NATIVE_SPEC_COMPLETE' in run.stdout
    row = dict(work=str(work),exit_code=run.returncode,monitor_complete=complete,
               sources=identities,log_sha256=digest(work/'sim.log'))
    if match:
        row.update(mode_register=int(match[1],16),cas=int(match[2]),period_ns=float(match[3]),
                   minimum_ns=float(match[4]),rate_conformant=bool(int(match[5])))
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--self-test',action='store_true')
    p.add_argument('--project',type=Path,help='Observe the assigned controller and active CAS override from this Native project')
    p.add_argument('--expected-controller-sha256',help='Required with --project; rejects a changed or ambiguous source')
    a=p.parse_args()
    parent=ROOT/'build/sim/native-sdram-spec';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='run-',dir=parent))
    if a.project:
        if not a.expected_controller_sha256:p.error('--project requires --expected-controller-sha256')
        path,wrapper,cas,override=project_contract(a.project,a.expected_controller_sha256)
        wrapper_sha=digest(wrapper)
    else:
        if a.expected_controller_sha256:p.error('--expected-controller-sha256 requires --project')
        path=ROOT/'rtl/memory/psx_sdram_ctrl.sv';wrapper=None;override=None
        cas=controller_contract(path.read_text())
    sha=digest(path);source=path.read_text()
    # Include an explicit parameter only when the source declares it. The
    # literal controller remains compilable; its unsupported overrides reject.
    parameterized=bool(re.search(r'parameter\s+integer\s+CAS_LATENCY\s*=',source))
    actual=observe(work/'actual',source,cas_override=cas if parameterized else None)
    if not actual['monitor_complete'] or actual['cas']!=cas:
        raise ValueError(f'Actual controller MRS/clock observation failed: {actual}')
    controls=[]
    if a.self_test:
        for name,mode,half,expected in [('cl2-slow',2,6.1,True),('cl2-fast',2,4.9205,False),
                                        ('cl3-native',3,4.9205,True),('cl3-fast',3,2.9,False),
                                        ('cl2-limit',2,6.0,True),('cl2-below',2,5.9995,False),
                                        ('cl3-limit',3,3.0,True),('cl3-below',3,2.9995,False)]:
            # Explicitly NOT an implemented CAS3/read-pipeline variant.
            if parameterized:
                changed=source
            else:
                changed,count=re.subn(r"cmd\s*<=\s*C_MRS;\s*a\s*<=\s*13'h0?2[23]1;",
                                     f"cmd <= C_MRS; a <= 13'h02{mode}1;",source)
                if count!=1:raise ValueError('Unknown mode-register control insertion')
            row=observe(work/name,changed,half,mode if parameterized else None)
            if not row['monitor_complete'] or row['rate_conformant']!=expected:
                raise ValueError(f'Spec monitor control failed: {row}')
            controls.append(row)
        fault=source.replace(".datain_h(1'b0), .datain_l(1'b1)",
                             ".datain_h(1'b1), .datain_l(1'b0)")
        if fault==source:raise ValueError('Missing clock-polarity control insertion')
        row=observe(work/'fault-polarity',fault,cas_override=cas if parameterized else None)
        if row['monitor_complete'] or 'SPEC_POLARITY' not in (work/'fault-polarity/sim.log').read_text():
            raise ValueError('Inverted-polarity monitor fault escaped')
        controls.append(row)
    if digest(path)!=sha:raise ValueError('Shared controller changed during spec check')
    if wrapper and digest(wrapper)!=wrapper_sha:raise ValueError('Active CAS wrapper changed during spec check')
    report=dict(actual=actual,monitor_controls=controls,self_test=a.self_test,
                controller_sha256=sha,active_cas=cas,wrapper_override=override,
                wrapper=dict(path=str(wrapper),sha256=wrapper_sha) if wrapper else None,
                data_beats_verified=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    print('Actual SDRAM rate:', 'CONFORMANT' if actual['rate_conformant'] else 'NONCONFORMANT')
    if not a.self_test and not actual['rate_conformant']:
        raise SystemExit(1)
    if a.self_test:print('Spec monitor controls pass; data-beat and board timing are separate gates.')


if __name__=='__main__':main()
