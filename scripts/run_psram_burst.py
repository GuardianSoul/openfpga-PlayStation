#!/usr/bin/env python3
"""Both-die burst pin/byte/reset checks; no BIOS/disc or FPGA hardware needed."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_psram_burst import prepare, UPSTREAM
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    compiler,runtime=find_tool('iverilog'),find_tool('vvp')
    if not compiler or not runtime: raise SystemExit('Icarus Verilog required')
    parent=ROOT/'build/sim/psram-burst';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));candidate=prepare(work/'rtl')
    original=work/'original-phy-simulation.sv';text=UPSTREAM.read_text()
    marker='logic tg, tg_d;'
    if text.count(marker)!=1: raise ValueError('Original phase registers changed')
    # Match the FPGA's default-low power-up values. No timing/data-path rewrite.
    original.write_text(text.replace(marker,"logic tg=1'b0, tg_d=1'b0;"))
    sources=[Path(candidate['path']),original,ROOT/'sim/memory/altddio_registered_model.sv',
        ROOT/'sim/memory/cellularram_burst_model.sv',ROOT/'sim/memory/tb_psx_psram_burst.sv']
    protected={str(p):digest(p) for p in [*sources,UPSTREAM,Path(__file__),ROOT/'scripts/prepare_psram_burst.py']}
    archive=[]
    for i,(path,sha) in enumerate(protected.items()):
        target=work/'compiled-inputs'/str(i)/Path(path).name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);archive.append(dict(original=path,path=str(target),sha256=sha))

    def run(name,params,files=None,trace=False):
        binary=work/(name+'.vvp')
        params={**params,'TRACE':int(trace)}
        with (work/(name+'-build.log')).open('w')as log:
            subprocess.run([compiler,'-g2012','-s','tb_psx_psram_burst',
                *[f'-Ptb_psx_psram_burst.{p}={v}' for p,v in params.items()],
                '-o',str(binary),*map(str,files or sources)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        args=[runtime,str(binary)]
        if trace: args.append('+trace='+str(work/(name+'-trace.txt')))
        result=subprocess.run(args,capture_output=True,text=True,timeout=120)
        output=result.stdout+result.stderr;(work/(name+'.log')).write_text(output)
        return result.returncode,output

    profiles=[(0,0,0,9),(0,0,0,2.5),(2,1,.5,9),(3.8,2.5,.5,9),
              (3.8,2.5,.5,2.5),(4,3,.5,9),(4,3,.5,2.5)]
    results=[]
    for i,values in enumerate(profiles):
        params=dict(zip(('CLOCK_DELAY','CONTROL_DELAY','RETURN_DELAY','DATA_ACCESS'),values))
        code,out=run(f'profile-{i}',params,trace=i==3);print(out,flush=True)
        if code or 'PSRAM_BURST PASS' not in out: raise SystemExit('Pin oracle failed; preserve '+str(work))
        results.append(dict(parameters=params,log_sha256=digest(work/f'profile-{i}.log'),passed=True))
    calibrated=dict(zip(('CLOCK_DELAY','CONTROL_DELAY','RETURN_DELAY','DATA_ACCESS'),profiles[3]))
    code,out=run('original-phy-calibrated',{**calibrated,'ORIGINAL_PHY':1},trace=True);print(out,flush=True)
    traces_equal=(digest(work/'profile-3-trace.txt')==digest(work/'original-phy-calibrated-trace.txt'))
    if code or 'PSRAM_BURST PASS' not in out or not traces_equal:
        raise SystemExit('Original vs retimed client schedule failed; preserve '+str(work))
    code,out=run('supply',{'MEASURE_ONLY':1});print(out,flush=True)
    if code or 'PSRAM_SUPPLY PASS' not in out: raise SystemExit('Supply failed; preserve '+str(work))
    rates=[dict(words=int(w),write=bool(int(wr)),bytes=int(b),elapsed_ns=float(t),MBps=float(m))
        for w,wr,b,t,m in re.findall(r'PSRAM_SUPPLY words=(\d+) write=(\d) bytes=(\d+) elapsed_ns=([\d.]+) MBps=([\d.]+)',out)]
    if len(rates)!=8: raise ValueError('Incomplete supply patterns')
    mutations=[('die_config','cfg_n[1] ? RCR_ALL : BCR_BURST',
        'cfg_n[1] ? RCR_ALL : cfg_n[0] ? BCR_ASYNC : BCR_BURST','Paired die config mismatch'),
        ('row_stop',"rst || z_last || nxt == 7'd127",'rst || z_last','Burst crossed row without restart'),
        ('reset_drain','rst && (st == S_IDLE || st == S_INIT)','rst','Burst terminated before first data'),
        ('address_guard','z_die == burst_die && z_addr == next_address','z_die == burst_die','Burst read byte mismatch')]
    rejected=[]
    for name,before,after,marker in mutations:
        text=Path(candidate['path']).read_text()
        if text.count(before)!=1: raise ValueError('Mutation contract changed: '+name)
        changed=work/('mutated-'+name+'.sv');changed.write_text(text.replace(before,after))
        code,out=run('mutated-'+name,{},[changed,*sources[1:]])
        if not code or marker not in out: raise ValueError('Oracle did not reject '+name+'; preserve '+str(work))
        rejected.append(name);print('Rejected mutation:',name,flush=True)
    code,out=run('original-phy-zero-delay',{'ORIGINAL_PHY':1})
    if not code or 'Burst read byte mismatch' not in out:
        raise ValueError('Original sampling defect not detected; preserve '+str(work))
    rejected.append('original_phy_zero_delay');print('Rejected original PHY zero-delay sampling',flush=True)
    for p,sha in protected.items():
        if digest(Path(p))!=sha: raise ValueError('Compiled oracle input changed: '+p)
    report=dict(candidate=candidate,profiles=results,protected_sha256=protected,compiled_input_archive=archive,
        mutations_rejected=rejected,original_retimed_client_cycle_trace_equal=traces_equal,
        client_cycle_trace_sha256=digest(work/'profile-3-trace.txt'),
        client_cycle_trace_lines=len((work/'profile-3-trace.txt').read_text().splitlines()),
        nominal_clock_mhz=62.5,initial_wait_cycles=12000,chip_power_up_min_ns=150000,supply=rates,
        limitations=['Pin model: CRE writes/fixed code 4 within rows, no electrical/retention/vendor certification',
            'Clock/control/return delays are independent fixtures, not measured Pocket I/O delays',
            'Native CPU/SPU/BIOS adapters, shared arbitration and full top not integrated',
            'Asynchronous ordinary memory port/variable latency/DPD/config read not covered'],
        board_timing_verified=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work)


if __name__=='__main__':main()
