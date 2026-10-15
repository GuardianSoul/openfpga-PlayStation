#!/usr/bin/env python3
"""Qualify the synchronous main port with a transaction-level PSRAM backend.

Reuses MiSTer's port oracle, not CPU execution or the PSRAM pin model. The
clients/pair clock is clk3x. No independent memory-clock crossing is implied.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import UPSTREAM, digest
from prepare_system_sim import prepare_memory_models
from run_main_latency import bench_text as latency_bench, measurements, summarize
from run_main_psram import bench_text as psram_bench, replace_once
from run_mister_main_adapter import oracle_text, vectors
from tool_paths import ROOT, find_tool

CORE_HALF = 14.76255
MEM_HALF = CORE_HALF / 3


def same_clock(text):
    """Drive the backend from the *actual* clk3x net, not another oscillator."""
    text = replace_once(text, '    reg clk=0,rst=1;', '    wire clk=clk3x; reg rst=1;')
    return replace_once(text,
        '    initial begin #(MEM_PHASE);forever #(MEM_HALF) clk=~clk;end', '')


def composition_bench():
    text = same_clock(psram_bench())
    text = replace_once(text, '    integer count=0,front=0,', '''    integer idle_wait=0;
    always @(posedge clk3x) begin
        if(!rst && source_idle && fabric_idle && !service_idle) idle_wait<=idle_wait+1;
        else idle_wait<=0;
        if(idle_wait==64) $fatal(1,"Adapter drain stuck despite idle source/backend");
    end
    integer count=0,front=0,''')
    text = replace_once(text, '$fatal(1,"Native main adapter timeout");',
        '''$display("TIMEOUT front=%0d count=%0d completed=%0d reset=%0d source_idle=%0d service_idle=%0d fabric_idle=%0d adapter_state=%0d pair_state=%0d client_busy=%0d fifo_empty=%0d core_run=%0d",front,count,completed,rst,source_idle,service_idle,fabric_idle,adapter.state,native.pair.state,native.busy,fifo_empty,core_clock_run);
        $fatal(1,"Native main adapter timeout");''')
    return replace_once(text, 'psx_mister_main_adapter adapter (',
                        'psx_mister_main_sync adapter (')


def stream_bench():
    """composition_bench as --psram-sync builds it: STREAM_CACHE=1 (cache
    lines from the clients' main fill stream) and POST_WRITES=1."""
    text = composition_bench()
    text = replace_once(text, 'psx_mister_main_sync adapter (',
        'wire main_fill; wire [31:0] main_fill_data;\n    psx_mister_main_sync #(.STREAM_CACHE(1),.POST_WRITES(1)) adapter (\n'
        '        .p_fill_valid(main_fill),.p_fill_data(main_fill_data),')
    return replace_once(text, '.fill_valid(),.fill_word(),.fill_data(),',
                        '.fill_valid(),.main_fill_valid(main_fill),.fill_word(),.fill_data(main_fill_data),')


def chip_model(text):
    # Preserve the existing random controller latency/WAIT gaps, but assign
    # unique, zero-initialized storage to the oracle's main and BIOS windows.
    text = replace_once(text, 'parameter real CLOCK_SPEED=104.0',
                        'parameter real CLOCK_SPEED=104.0, parameter integer PAIRED_MODE=1')
    old = '    function automatic [15:0] slot(input [22:0] x); slot={x[22],x[18],x[17],x[12:0]}; endfunction'
    new = '''    function automatic integer slot(input [22:0] x);
        if(!x[22] && x[21:0]<22'h004000) slot=int'(x[21:0]);
        else if(x[22] && PAIRED_MODE!=0 && x[21:0]>=22'h020000 && x[21:0]<22'h024000)
            slot=16384+int'(x[21:0]-22'h020000);
        else if(x[22] && PAIRED_MODE==0 && x[21:0]>=22'h040000 && x[21:0]<22'h044000)
            slot=16384+int'(x[21:0]-22'h040000);
        else begin slot=0;$fatal(1,"Sync model outside authored windows %h",x);end
    endfunction'''
    text = replace_once(text, old, new)
    return replace_once(text, "{x[15:0]^16'h5a5a}", "16'h0000")


def floor_bench(sync):
    text = latency_bench()
    # The asynchronous baseline uses a separate oscillator at exactly the
    # same nominal rate. Candidate backend/monitor both use clk3x itself.
    if sync:
        text = replace_once(text, 'psx_mister_main_adapter adapter (',
                            'psx_mister_main_sync adapter (')
        text = replace_once(text, '    reg clk=0,rst=1;', '    wire clk=clk3x; reg rst=1;')
        text = replace_once(text,
            '    initial begin #(MEM_PHASE); forever #(MEM_HALF) clk=~clk; end', '')
    return text


def reset_bench():
    text = (ROOT/'sim/pocket/tb_main_reset_transport.sv').read_text()
    text = replace_once(text, '    reg clk1x=0,clk3x=0,mem_clk=0,reset_async=1;',
                        '    reg clk1x=0,clk3x=0,reset_async=1; wire mem_clk=clk3x;')
    text = replace_once(text, '    always #(HALFMEM) mem_clk=!mem_clk;', '')
    text = replace_once(text, '    assign p_ready=!busy && permit_request;',
                        '    assign p_ready=p_valid && !busy && permit_request;')
    return replace_once(text, '    psx_mister_main_adapter adapter(',
                        '    psx_mister_main_sync adapter(')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path)
    parser.add_argument('--expected-adapter-sha256')
    args = parser.parse_args()
    if bool(args.adapter) != bool(args.expected_adapter_sha256):
        parser.error('Private adapter and expected SHA256 must be supplied together')
    adapter = args.adapter.resolve() if args.adapter else ROOT/'rtl/pocket/psx_mister_main_sync.sv'
    if args.expected_adapter_sha256 and digest(adapter) != args.expected_adapter_sha256:
        parser.error('Private adapter identity mismatch')
    parent = ROOT/'build/sim/main-sync'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent)); print(work, flush=True)
    rows, gold = vectors(work)
    model = prepare_memory_models(work/'oracle-model')[0]
    oracle = work/'oracle.vhd'; oracle.write_text(oracle_text())
    with (work/'oracle.log').open('w') as log:
        for command in (
            [find_tool('ghdl'), '-a', '--std=08', '--work=tb',
             str(UPSTREAM/'sim/system/src/tb/globals.vhd'), model['path']],
            [find_tool('ghdl'), '-a', '--std=08', str(oracle)],
            [find_tool('ghdl'), '-r', '--std=08', 'tb_mister_oracle',
             '--max-stack-alloc=0', '--assert-level=error'],
        ):
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT,
                           timeout=120, check=True)
    actual = []
    for line in (work/'oracle.txt').read_text().splitlines():
        fields = line.split(); actual.append((int(fields[0]), fields[1], *[int(x,16) for x in fields[2:]]))
    if sorted(actual, key=lambda e:(e[0],e[1])) != gold:
        raise ValueError('Pinned MiSTer oracle differs from independent byte oracle')
    (work/'expected.txt').write_text(''.join(
        f'{i:x} {kind} {mask:x} {address:x} {data:x}\n' for i,kind,mask,address,data in actual))
    composed = work/'composed.sv'; composed.write_text(composition_bench())
    streamed = work/'streamed.sv'; streamed.write_text(stream_bench())
    chip = work/'chip.sv'; chip.write_text(chip_model((ROOT/'sim/pocket/psram_behavior.sv').read_text()))
    reset = work/'reset.sv'; reset.write_text(reset_bench())
    floors = {}
    for sync in (False, True):
        floors[sync] = work/('floor-sync.sv' if sync else 'floor-baseline.sv')
        floors[sync].write_text(floor_bench(sync))
    inputs = [adapter, ROOT/'rtl/pocket/psx_mister_main_adapter.sv', ROOT/'rtl/memory/psx_mem_cdc.sv',
        ROOT/'rtl/pocket/psx_psram_clients.sv', ROOT/'rtl/pocket/psx_psram_pair.sv',
        ROOT/'sim/pocket/tb_main_latency.sv', chip, composed, streamed, reset, *floors.values(), oracle,
        ROOT/'rtl/pocket/psx_main_reset_fence.sv', ROOT/'sim/pocket/tb_main_reset_transport.sv',
        Path(model['path']), UPSTREAM/'sim/system/src/tb/globals.vhd', work/'vectors.txt', work/'expected.txt',
        Path(__file__), ROOT/'scripts/run_main_latency.py', ROOT/'scripts/run_main_psram.py',
        ROOT/'scripts/run_mister_main_adapter.py', ROOT/'sim/pocket/main_psram_backend_body.sv',
        ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv', ROOT/'sim/pocket/psram_behavior.sv',
        ROOT/'scripts/prepare_system_sim.py', ROOT/'scripts/tool_paths.py']
    archives = {}
    for index, path in enumerate(dict.fromkeys(inputs)):
        sha = digest(path); target = work/'inputs'/str(index)/path.name
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, target)
        if digest(target) != sha: raise ValueError('Input changed while archiving: '+str(path))
        archives[str(path)] = dict(path=str(target), sha256=sha)
    def saved(path): return archives[str(path)]['path']
    def execute(name, sources, parameters, plusargs=(), expected_failure=None,
                top='tb_psx_mister_main_adapter', marker='PASS MISTER MAIN ADAPTER vectors=2166'):
        directory = work/name; directory.mkdir()
        for filename in ('vectors.txt','expected.txt'): shutil.copyfile(work/filename, directory/filename)
        binary = directory/'sim.vvp'
        defaults = dict(CORE_HALF=CORE_HALF,MEM_HALF=MEM_HALF) if top=='tb_psx_mister_main_adapter' else {}
        command = [find_tool('iverilog'), '-g2012', '-s', top,
            *[f'-P{top}.{k}={v}' for k,v in {**defaults,**parameters}.items()],
            '-o', str(binary), *map(str,sources)]
        with (directory/'compile.log').open('w') as log:
            subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        run = subprocess.run([find_tool('vvp'), str(binary), *plusargs], cwd=directory,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        log = directory/'run.log'; log.write_text(run.stdout)
        passed = (run.returncode!=0 and expected_failure in run.stdout) if expected_failure else (
            run.returncode==0 and marker in run.stdout)
        if not expected_failure and top=='tb_main_reset_transport':
            passed = passed and run.stdout.count('RESET_TRANSPORT_CASE')==7
        result = dict(name=name,passed=passed,exit_code=run.returncode,log=str(log),log_sha256=digest(log),
            executable_sha256=digest(binary),compiled_source_sha256={str(p):digest(Path(p)) for p in sources},
            parameters=parameters,expected_failure=expected_failure)
        print(name, passed, run.stdout[-350:], flush=True)
        if not passed: raise ValueError('Sync gate failed: '+name)
        return result, directory
    runs = []
    common = [saved(adapter), saved(ROOT/'rtl/pocket/psx_psram_clients.sv'),
              saved(ROOT/'rtl/pocket/psx_psram_pair.sv'), saved(chip), saved(composed)]
    for paired in (1,0):
        for background in (0,1):
            run, _ = execute(f'paired{paired}-background{background}', common,
                             dict(PAIRED=paired,BACKGROUND=background))
            runs.append(run)
    for paired in (1,0):
        for background in (0,1):
            run, _ = execute(f'stream-paired{paired}-background{background}',
                             [*common[:4], saved(streamed)], dict(PAIRED=paired,BACKGROUND=background))
            runs.append(run)
    # Streaming must actually feed the cache: corrupt only the fill stream.
    stream_fault = work/'fault-stream.sv'
    stream_fault.write_text(replace_once(Path(saved(streamed)).read_text(),
        '.p_fill_data(main_fill_data)', ".p_fill_data(main_fill_data^32'h1)"))
    fault, _ = execute('fault-stream-data', [*common[:4], str(stream_fault)], dict(PAIRED=1,BACKGROUND=0),
                       expected_failure='Native cache event mismatch')
    runs.append(fault)
    fault, _ = execute('fault-upper-dma', common, dict(PAIRED=1,BACKGROUND=0),
                       plusargs=['+FAULT_UPPER_DMA'], expected_failure='Native return event mismatch')
    runs.append(fault)
    clients = work/'fault-clients.sv'
    clients.write_text(replace_once(Path(common[1]).read_text(),
        'assign idle=!reset && !busy && !held && pair_idle;', 'assign idle=pair_idle;'))
    broken = [*common]; broken[1] = str(clients)
    fault, _ = execute('fault-early-idle', broken, dict(PAIRED=1,BACKGROUND=0),
                       expected_failure='Idle before accepted packet completed')
    runs.append(fault)
    # Reintroduce the synchronous adapter's original bug in actual RTL. A
    # ready-on-valid backend must still allow the empty adapter to drain.
    adapter_fault = work/'fault-adapter-idle.sv'
    adapter_fault.write_text(replace_once(Path(common[0]).read_text(),
        'response_toggle==response_ack_sync[1] && ram_dmafifo_empty && !ram_dmafifo_read;',
        'response_toggle==response_ack_sync[1] && ram_dmafifo_empty && !ram_dmafifo_read && s_ready;'))
    broken = [*common]; broken[0] = str(adapter_fault)
    fault, _ = execute('fault-adapter-idle', broken, dict(PAIRED=1,BACKGROUND=0),
                       expected_failure='Adapter drain stuck despite idle source/backend')
    runs.append(fault)
    run, _ = execute('reset-transport', [saved(adapter),saved(ROOT/'rtl/pocket/psx_main_reset_fence.sv'),
        saved(reset)], dict(HALF1=CORE_HALF,HALF3=MEM_HALF), top='tb_main_reset_transport',
        marker='PASS MAIN RESET TRANSPORT')
    runs.append(run)
    # Early-acknowledged writes must still be committed by the reset fence.
    posted_reset = work/'reset-posted.sv'
    posted_reset.write_text(replace_once(Path(saved(reset)).read_text(),
        '    psx_mister_main_sync adapter(', '    psx_mister_main_sync #(.POST_WRITES(1)) adapter('))
    run, _ = execute('reset-transport-posted', [saved(adapter),saved(ROOT/'rtl/pocket/psx_main_reset_fence.sv'),
        str(posted_reset)], dict(HALF1=CORE_HALF,HALF3=MEM_HALF), top='tb_main_reset_transport',
        marker='PASS MAIN RESET TRANSPORT')
    runs.append(run)
    latency = {}
    for extra in (0,12):
        for sync in (False, True):
            name = f'floor-{"sync" if sync else "baseline"}-delay{extra}'
            sources = [saved(adapter if sync else ROOT/'rtl/pocket/psx_mister_main_adapter.sv'),
                saved(ROOT/'rtl/memory/psx_mem_cdc.sv'), saved(ROOT/'sim/pocket/tb_main_latency.sv'), saved(floors[sync])]
            run, directory = execute(name, sources, dict(EXTRA=extra)); runs.append(run)
            events = measurements(directory/'latency.csv', rows, MEM_HALF, extra)
            latency[name] = dict(summary=summarize(events), events_sha256=digest(directory/'latency.csv'))
    identity = all(digest(Path(item['path']))==item['sha256'] for item in archives.values())
    report = dict(passed=identity and all(r['passed'] for r in runs), source_identity_verified=identity,
        original_sources_unchanged=all(digest(Path(p))==v['sha256'] for p,v in archives.items()),
        input_archives=archives,runs=runs,latency=latency,vector_count=len(rows),upstream_events=len(gold),
        adapter_sha256=archives[str(adapter)]['sha256'], actual_CPU_execution=False,
        pin_model_verified=False,hardware_verified=False,game_verified=False,
        limitations=['One related 3:1 clock family; backend driven by actual clk3x net',
            'Behavioral controller-port model with random initial/within-burst gaps; no pin/capture/PLL simulation',
            'Background clients model packet contention, not integrated SPU/host reset crossings',
            'Common transport reset, stopped/paused consumer and seven adapter/fence drain cases tested; full CPU warm reset not exercised',
            'Latency uses same-rate synthetic single-client backend; does not predict FFVII frame rate'])
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n'); print(work/'report.json',flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
