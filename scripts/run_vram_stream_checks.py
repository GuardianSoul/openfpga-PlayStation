#!/usr/bin/env python3
"""Byte/order/credit/reset oracle and pin-model supply of native VRAM bursts."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_pocket_read_pipeline import prepare as retime_phy
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        raise SystemExit('Icarus Verilog required')
    parent = ROOT/'build/sim/vram-stream'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    models=[]
    for name, substitutions in (
        ('sdr_command_model.sv', (("12'h080", "12'h800"), ("24'h080000", "24'h800000"))),
        ('sdr_pin_model.sv', (("12'h080", "12'h800"), ("25'h100000", "25'h1000000")))):
        original=ROOT/'sim/memory'/name
        text=original.read_text()
        for before, after in substitutions:
            if text.count(before)!=1:
                raise ValueError('Sparse oracle changed: '+name)
            text=text.replace(before, after)
        model=work/name; model.write_text(text); models.append((original, model))
    bridge=ROOT/'rtl/pocket/psx_vram_stream.sv'
    phy=ROOT/'rtl/memory/psx_sdr_phy.sv'
    candidate=retime_phy(work/'retimed-phy')
    sources=[bridge, ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv', *[p for _, p in models], phy,
        ROOT/'sim/memory/altddio_clock_model.sv', ROOT/'sim/memory/tb_psx_vram_stream.sv']
    protected={str(p):digest(p) for p in [*[p for p, _ in models], *sources, Path(candidate['path']), Path(__file__)]}
    archived=[]
    for i, (p, sha) in enumerate(protected.items()):
        out=work/'compiled-inputs'/str(i)/Path(p).name; out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, out)
        archived.append({'original':p, 'path':str(out), 'sha256':sha})

    def run(name, profile, changed_sources=None, measure=False, trace=False, extra_parameters=None):
        backend, src, mem, fifo=profile
        binary=work/(name+'.vvp')
        parameters=dict(BACKEND=backend, SRC_HALF_NS=src, MEM_HALF_NS=mem,
                        FIFO_AW=fifo, MEASURE_ONLY=int(measure), TRACE=int(trace))
        parameters.update(extra_parameters or {})
        with (work/(name+'-compile.log')).open('w') as log:
            subprocess.run([compiler, '-g2012', '-s', 'tb_psx_vram_stream',
                *[f'-Ptb_psx_vram_stream.{key}={value}' for key, value in parameters.items()],
                '-o', str(binary), *map(str, changed_sources or sources)],
                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        arguments=[runtime, str(binary)]
        if trace: arguments.append('+trace='+str(work/(name+'-trace.txt')))
        result=subprocess.run(arguments, capture_output=True, text=True, timeout=180)
        output=result.stdout+result.stderr
        (work/(name+'.log')).write_text(output)
        return result.returncode, output

    results=[]
    profiles=[(0,3,8,4), (0,31,4,2), (1,7.381275,8,4), (1,29,8,2), (2,7.381275,8,4)]
    for i, profile in enumerate(profiles):
        code, output=run(f'profile-{i}', profile, trace=i==4)
        print(output, flush=True)
        if code or 'VRAM_STREAM PASS' not in output:
            raise SystemExit('Stream oracle failed; preserve '+str(work))
        results.append(dict(zip(('backend','src_half_ns','mem_half_ns','fifo_aw'), profile),
                            log_sha256=digest(work/f'profile-{i}.log'), passed=True))
    retimed_sources=[Path(candidate['path']) if p==phy else p for p in sources]
    code, output=run('retimed-pins', profiles[-1], retimed_sources, trace=True)
    print(output, flush=True)
    traces_equal=digest(work/'retimed-pins-trace.txt')==digest(work/'profile-4-trace.txt')
    if code or 'VRAM_STREAM PASS' not in output or not traces_equal:
        raise SystemExit('Retimed bridge client-cycle oracle failed; preserve '+str(work))
    code, output=run('asymmetric-pins', profiles[-1], retimed_sources,
                     extra_parameters={'PIN_T_AC_SECOND':2.5})
    print(output, flush=True)
    if code or 'VRAM_STREAM PASS' not in output:
        raise SystemExit('Asymmetric pin access failed; preserve '+str(work))
    code, output=run('supply', profiles[-1], retimed_sources, measure=True)
    print(output, flush=True)
    if code or 'STREAM_SUPPLY PASS' not in output:
        raise SystemExit('Pin-model supply failed; preserve '+str(work))
    supply=[{'pattern':pattern, 'bytes':int(size), 'elapsed_ns':float(elapsed), 'MBps':float(rate)}
        for pattern, size, elapsed, rate in re.findall(
            r'STREAM_SUPPLY pattern=(\w+) bytes=(\d+) elapsed_ns=([\d.]+) MBps=([\d.]+)', output)]
    if len(supply)!=6: raise ValueError('Missing supply pattern')
    mutations=[('address', "command_address+{{15{1'b0}},issued}",
        "command_address+{{15{1'b0}},issued}+24'd2", 'Physical address/order mismatch'),
        ('credits', '(command_write || reserved_words<CREDIT_WORDS)', "1'b1", 'Read credits exceeded storage'),
        ('acceptance', 'wire accept=offered && !busy;', 'wire accept=offered;',
         'VRAM command FIFO overflow'),
        ('reset_gate', '.rd_rst_i(mem_reset || !source_online_sync[1])',
         '.rd_rst_i(mem_reset)', 'Unexpected physical request')]
    rejected=[]
    for name, before, after, marker in mutations:
        text=bridge.read_text()
        if text.count(before)!=1: raise ValueError('Mutation contract changed: '+name)
        changed=work/('mutated-'+name+'.sv'); changed.write_text(text.replace(before, after))
        code, output=run('mutated-'+name, profiles[1], [changed, *sources[1:]])
        if not code or marker not in output:
            raise ValueError('Oracle did not reject '+name+'; preserve '+str(work))
        rejected.append(name); print('Rejected mutation: '+name, flush=True)
    if not all(digest(Path(p))==sha for p, sha in protected.items()):
        raise ValueError('Oracle inputs changed')
    report=dict(profiles=results, protected_sha256=protected, compiled_input_archive=archived,
        mutations_rejected=rejected, retimed_phy=candidate, retimed_client_cycle_trace_equal=traces_equal,
        client_cycle_trace_sha256=digest(work/'profile-4-trace.txt'),
        client_cycle_trace_lines=len((work/'profile-4-trace.txt').read_text().splitlines()),
        supply=supply, supply_log_sha256=digest(work/'supply.log'),
        supply_clocks_requested_mhz=dict(native=67.7376, controller=62.5, pins=125),
        supply_native_half_ns_quantized=7.381, refresh_interval_ns=7680,
        physical_pin_model_timing_checked=True, pin_access_first_ns=5.5, pin_access_second_ns=[5.5,2.5],
        pin_release_max_ns=6.0, asymmetric_pin_model_passed=True, write_supply_includes_read_fence=True,
        board_STA_or_hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(work)


if __name__=='__main__': main()
