#!/usr/bin/env python3
"""Check the native main port through PSRAM clients/pair and a transaction model.

Checks bytes/cache/DMA/order/CE/reset against the pinned MiSTer oracle, with
paired, single-chip, clock-phase and concurrent-client cases. This does not
simulate PocketQuake pins, execute the CPU, or measure game performance.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import UPSTREAM, digest
from prepare_system_sim import prepare_memory_models
from run_mister_main_adapter import oracle_text, vectors
from tool_paths import ROOT, find_tool


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Main/PSRAM fixture anchor changed: '+before[:100])
    return text.replace(before, after)


def bench_text():
    body = (ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv').read_text()
    start = body.index('    wire [3:0] p_ready,p_rsp_valid,p_rsp_error;')
    end = body.index('    integer count=0,front=0,', start)
    body = body[:start]+(ROOT/'sim/pocket/main_psram_backend_body.sv').read_text()+body[end:]
    body = replace_once(body, '        $display("PASS MISTER MAIN ADAPTER vectors=',
        '''        if(BACKGROUND!=0 && (background1==0 || background2==0))
            $fatal(1,"Missing concurrent-client coverage");
        $display("PSRAM_COMPOSITION paired=%0d background1=%0d background2=%0d",PAIRED,background1,background2);
        $display("PASS MISTER MAIN ADAPTER vectors=''')
    return '''`timescale 1ns/1ps
module tb_psx_mister_main_adapter;
    parameter real MEM_HALF=4.8,MEM_PHASE=0;
    parameter integer PAIRED=1,BACKGROUND=0;
    reg clk=0,rst=1;
    wire initialized;
    initial begin #(MEM_PHASE);forever #(MEM_HALF) clk=~clk;end
'''+body


def chip_text():
    text = (ROOT/'sim/pocket/psram_behavior.sv').read_text()
    for before, after in (
        ('parameter real CLOCK_SPEED=104.0',
         'parameter real CLOCK_SPEED=104.0, parameter integer PAIRED_MODE=1'),
        ('// Sparse enough for the bench\'s address windows: die, addr[18:17], addr[8:0].',
         '// Unique main/BIOS window addresses for the selected paired/single geometry.'),
        ('reg [15:0] mem [0:4095];', 'reg [15:0] mem [0:32767];'),
        ('reg written [0:4095];', 'reg written [0:32767];'),
        ('q<4096', 'q<32768'),
        ("{x[15:0]^16'h5a5a}", "16'h0000"),
    ):
        text = replace_once(text, before, after)
    return replace_once(text,
        "    function automatic [11:0] slot(input [22:0] x); slot={x[22],x[18],x[17],x[8:0]}; endfunction",
        '''    function automatic integer slot(input [22:0] x);
        if(!x[22] && x[21:0]<22'h004000) slot=int'(x[21:0]);
        else if(x[22] && PAIRED_MODE!=0 && x[21:0]>=22'h020000 && x[21:0]<22'h024000)
            slot=16384+int'(x[21:0]-22'h020000);
        else if(x[22] && PAIRED_MODE==0 && x[21:0]>=22'h040000 && x[21:0]<22'h044000)
            slot=16384+int'(x[21:0]-22'h040000);
        else begin slot=0;$fatal(1,"Behavioral chip outside authored windows %h paired=%0d",x,PAIRED_MODE);end
    endfunction''')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adapter', type=Path, help='Private adapter; requires expected SHA256')
    parser.add_argument('--expected-adapter-sha256')
    args = parser.parse_args()
    if bool(args.adapter) != bool(args.expected_adapter_sha256):
        raise SystemExit('Private adapter and expected SHA256 must be supplied together')
    adapter = args.adapter.resolve() if args.adapter else ROOT/'rtl/pocket/psx_mister_main_adapter.sv'
    if args.expected_adapter_sha256 and digest(adapter) != args.expected_adapter_sha256:
        raise SystemExit('Private adapter identity mismatch')
    base = ROOT/'build/sim/main-psram'
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=base))
    rows, gold = vectors(work)
    model = prepare_memory_models(work/'oracle-model')[0]
    oracle = work/'oracle.vhd'
    oracle.write_text(oracle_text())
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
        fields = line.split()
        actual.append((int(fields[0]), fields[1], *[int(v,16) for v in fields[2:]]))
    if sorted(actual, key=lambda event:(event[0],event[1])) != gold:
        raise ValueError('Pinned MiSTer events differ from the independent byte oracle')
    (work/'expected.txt').write_text(''.join(
        f'{i:x} {event} {mask:x} {address:x} {data:x}\n'
        for i,event,mask,address,data in actual))
    bench = work/'tb.sv'
    bench.write_text(bench_text())
    chip = work/'chip.sv'
    chip.write_text(chip_text())
    sources = [adapter, ROOT/'rtl/memory/psx_mem_cdc.sv',
        ROOT/'rtl/pocket/psx_psram_clients.sv', ROOT/'rtl/pocket/psx_psram_pair.sv', chip, bench]
    protected = {str(path):digest(path) for path in [*sources,
        ROOT/'sim/pocket/main_psram_backend_body.sv', ROOT/'sim/pocket/psram_behavior.sv',
        Path(__file__), ROOT/'scripts/run_mister_main_adapter.py',
        ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv', Path(model['path']), oracle,
        work/'vectors.txt', work/'expected.txt', UPSTREAM/'sim/system/src/tb/globals.vhd']}
    archives = {}
    for index, (path, sha) in enumerate(protected.items()):
        target = work/'inputs'/str(index)/Path(path).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        if digest(target) != sha:
            raise ValueError('Changed input while archiving: '+path)
        archives[path] = dict(path=str(target), sha256=sha)
    # Compile the archived bytes, so another agent's edits cannot affect an
    # in-flight executable. Also record whether original inputs stayed stable.
    compiled_sources = [archives[str(path)]['path'] for path in sources]
    runs = []
    profiles = [('paired104',4.8,0,1,0), ('phase104',4.8,3.1,1,0),
        ('contended104',4.8,3.1,1,1), ('single104',4.8,3.1,0,0),
        ('contended78',6.4,3.1,1,1), ('paired62',8,0,1,0)]
    for name, half, phase, paired, background in profiles:
        directory = work/name
        directory.mkdir()
        for filename in ('vectors.txt','expected.txt'):
            shutil.copyfile(work/filename, directory/filename)
        binary = directory/'sim.vvp'
        command = [find_tool('iverilog'), '-g2012', '-s', 'tb_psx_mister_main_adapter',
            '-Ptb_psx_mister_main_adapter.CORE_HALF=14.76255',
            f'-Ptb_psx_mister_main_adapter.MEM_HALF={half}',
            f'-Ptb_psx_mister_main_adapter.MEM_PHASE={phase}',
            f'-Ptb_psx_mister_main_adapter.PAIRED={paired}',
            f'-Ptb_psx_mister_main_adapter.BACKGROUND={background}',
            '-o', str(binary), *compiled_sources]
        with (directory/'compile.log').open('w') as log:
            subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                           timeout=120, check=True)
        result = subprocess.run([find_tool('vvp'), str(binary)], cwd=directory,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        log = directory/'run.log'
        log.write_text(result.stdout)
        passed = result.returncode==0 and 'PASS MISTER MAIN ADAPTER vectors=2166' in result.stdout
        runs.append(dict(case=name, passed=passed, exit_code=result.returncode,
            log=str(log), log_sha256=digest(log), executable_sha256=digest(binary),
            mem_half_ns=half, mem_phase_ns=phase, paired=paired, background=background))
        print(name, result.returncode, result.stdout[-600:], flush=True)
        if not passed:
            break
    fault = False
    idle_fault = False
    if all(run['passed'] for run in runs):
        directory = work/'fault-upper-dma'
        directory.mkdir()
        for filename in ('vectors.txt','expected.txt'):
            shutil.copyfile(work/filename, directory/filename)
        result = subprocess.run([find_tool('vvp'),str(work/'paired104/sim.vvp'),'+FAULT_UPPER_DMA'],
            cwd=directory, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        (directory/'run.log').write_text(result.stdout)
        fault = result.returncode!=0 and 'Native return event mismatch' in result.stdout
        print('fault', fault, result.stdout[-600:], flush=True)
        # Reproduce the old clients.idle bug inside an actual RTL copy. The
        # accepted-packet scoreboard must reject it before a pending packet
        # reaches the backend, independently of the component's state bits.
        directory = work/'fault-early-idle'
        directory.mkdir()
        for filename in ('vectors.txt','expected.txt'):
            shutil.copyfile(work/filename, directory/filename)
        client_index = sources.index(ROOT/'rtl/pocket/psx_psram_clients.sv')
        broken_clients = directory/'clients.sv'
        broken_clients.write_text(replace_once(Path(compiled_sources[client_index]).read_text(),
            'assign idle=!reset && !busy && !held && pair_idle;', 'assign idle=pair_idle;'))
        broken_sources = list(compiled_sources)
        broken_sources[client_index] = str(broken_clients)
        binary = directory/'sim.vvp'
        command = [find_tool('iverilog'), '-g2012', '-s', 'tb_psx_mister_main_adapter',
            '-Ptb_psx_mister_main_adapter.CORE_HALF=14.76255',
            '-o', str(binary), *broken_sources]
        with (directory/'compile.log').open('w') as log:
            subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                           timeout=120, check=True)
        result = subprocess.run([find_tool('vvp'),str(binary)], cwd=directory,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        (directory/'run.log').write_text(result.stdout)
        idle_fault = result.returncode!=0 and 'Idle before accepted packet completed' in result.stdout
        print('early-idle fault', idle_fault, result.stdout[-600:], flush=True)
    identity = all(digest(Path(info['path']))==info['sha256'] for info in archives.values())
    originals_stable = all(digest(Path(path))==sha for path,sha in protected.items())
    report = dict(passed=len(runs)==len(profiles) and all(run['passed'] for run in runs) and fault and idle_fault and identity,
        source_identity_verified=identity, original_sources_unchanged=originals_stable,
        protected_sha256=protected, input_archives=archives, runs=runs,
        upper_DMA_word_fault_detected=fault, early_idle_fault_detected=idle_fault,
        vector_count=len(rows), upstream_events=len(gold),
        pin_model_verified=False, actual_CPU_execution=False, hardware_verified=False,
        game_verified=False, adapter=str(adapter), production_RTL_changed=False,
        scope='Native adapter/CDC/clients/pair with behavioral controller-port model; authored windows, no pins')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(work/'report.json', flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
