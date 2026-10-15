#!/usr/bin/env python3
"""Trace a verified checkpoint through its exact existing compiled model.

Link a read-only observer into a private copy of the original clock/host driver.
For every bounded run, compare against the original executable from the SAME
checkpoint: complete final serialized state, PCM, sampled PC, host sector log,
RAM and VRAM. This never rebuilds HDL or makes checkpoints portable.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

from prepare_resource_targets import digest
from run_system_checkpoint import MAX_CYCLES
from tool_paths import ROOT


CORE = 'psx_checkpoint__DOT__core__DOT__component_core__DOT__'


def verify_hashes(protected):
    for path, sha in protected.items():
        if digest(Path(path)) != sha:
            raise ValueError('Observer identity changed: '+path)


def module_text(verilog, entity):
    found = re.findall(r'^module '+entity+r'_Barch(?:_\w+)?\b.*?^endmodule', verilog,
                       flags=re.M | re.S)
    if len(found) != 1:
        raise ValueError('Expected exact generated entity: '+entity)
    return found[0]


def bindings(build, cpu_stalls=False, tag_window=False):
    verilog = (build/'system.v').read_text()
    header = (build/'obj/Vpsx_checkpoint___024root.h').read_text()
    result = {}

    def bind(key, name):
        if not re.search(r'\b'+re.escape(name)+r'\b', header):
            raise ValueError('Compiled observer member missing: '+key+' = '+name)
        result['OBS_'+key] = name

    def alias(key, entity, instance, signal):
        text = module_text(verilog, entity)
        found = re.findall(r'^  assign '+signal+r' = (\w+);', text, re.M)
        if len(found) != 1:
            raise ValueError('Generated alias changed: '+entity+'.'+signal)
        bind(key, CORE+instance+'__DOT__'+found[0])

    # Device ports use the mux's latched data address and value. The GP1 rows
    # are additionally checked against the live HDL's own acceptance stdout.
    alias('BUS_ADDRESS', 'memorymux', 'imemorymux', 'addressdata_buf')
    alias('BUS_DATA', 'memorymux', 'imemorymux', 'datawrite_buf')
    alias('DMA_GPU_WRITE', 'dma', 'idma', 'dma_gpu_writeena')
    alias('DMA_GPU_DATA', 'dma', 'idma', 'dma_gpu_write')
    alias('VIDEO_REPORTS', 'gpu_videoout_async',
          'igpu__DOT__igpu_videoout__DOT__igpu_videoout_async', 'videoout_reports')
    for key, signal in {
        'CE':'ce', 'RESET':'reset_intern', 'CPU_PAUSED':'cpupaused',
        'MEM_REQUEST':'mem_request', 'MEM_DONE':'mem_done', 'DMA_WAIT':'dma_gpu_waiting',
        'GPU_WRITE':'bus_gpu_write', 'GPU_READ':'bus_gpu_read',
        'IRQ_WRITE':'bus_irq_write', 'DMA_BUS_WRITE':'bus_dma_write',
        'INDEX2X':'clk2xindex', 'IRQ_REQUEST':'irqrequest',
        'IRQ_INPUT':'iirq__DOT__irqin', 'GPU_STATUS':'igpu__DOT__gpustat',
    }.items():
        if key == 'INDEX2X' and not re.search(r'\b'+CORE+signal+r'\b', header):
            # Native GHDL output retains the isignal's backing register,
            # whereas the mixed candidate retains the named port alias.
            text = module_text(verilog, 'psx_top')
            found = re.findall(r'^    clk2xindex = (n\d+); // \(isignal\)',text,re.M)
            if len(found) != 1:
                raise ValueError('Native index register alias changed')
            bind(key, CORE+found[0])
        else:
            bind(key, CORE+signal)
    for key, signal in {
        'RAM_ENABLE':'ram_ena', 'RAM_READ':'ram_rnw',
        'RAM_ADDRESS':'ram_Adr', 'RAM_DATA':'ram_dataWrite',
    }.items():
        bind(key, 'psx_checkpoint__DOT__'+signal)
    if cpu_stalls or tag_window:
        # Resolve from the exact generated alias, never a copied register ID.
        text = module_text(verilog, 'cpu')
        for key, signal in {'CPU_STALL':'stall', 'CPU_MEM_BUSY':'memorymuxbusy',
                            'CPU_MEM_DATA':'memorymuxstage4',
                            'CPU_CACHE_HIT':'cachehit'}.items():
            named = CORE+'icpu__DOT__'+signal
            if re.search(r'\b'+re.escape(named)+r'\b', header):
                bind(key, named)
            else:
                found = re.findall(r'^    '+signal+r' = (n\d+); // \(isignal\)', text, re.M)
                if len(found) != 1:
                    raise ValueError('Generated CPU alias changed: '+signal)
                bind(key, CORE+'icpu__DOT__'+found[0])
    if tag_window:
        for key, signal in {'CPU_FETCH_ADDR':'fetchaddr', 'CPU_TAG_DATA':'tag_q_b',
                            'CPU_TAG_ADDR':'tag_address_b'}.items():
            bind(key, CORE+'icpu__DOT__'+signal)
    return result


def build_observer(build, work, protected, cpu_stalls=False, tag_window=False):
    obj = build/'obj'
    driver = ROOT/'sim/reference/psx_checkpoint_main.cpp'
    extension_name = 'psx_checkpoint_tag_observer.h' if tag_window else (
        'psx_checkpoint_cpu_observer.h' if cpu_stalls else 'psx_checkpoint_observer.h')
    extension = ROOT/'sim/reference'/extension_name
    host = ROOT/'sim/reference/psx_checkpoint_host.h'
    runtime_match = re.search(r'^VERILATOR_ROOT\s*=\s*(.+)$',
                             (obj/'Vpsx_checkpoint.mk').read_text(), re.M)
    if not runtime_match:
        raise ValueError('Verilator runtime missing')
    runtime = Path(runtime_match[1].strip())
    library = obj/'Vpsx_checkpoint__ALL.a'
    objects = [obj/(name+'.o') for name in ('verilated', 'verilated_save', 'verilated_threads')]
    mapping = bindings(build, cpu_stalls, tag_window)
    bound = work/'observer-bindings.h'
    bound.write_text(''.join('#define '+key+' '+value+'\n' for key,value in mapping.items()))
    text = driver.read_text()
    markers = {
        '#include "psx_checkpoint_host.h"': '#include "psx_checkpoint_host.h"\n'
            '#include "observer-bindings.h"\n#include "'+extension.name+'"',
        '        const uint64_t start=model->cycles;':
            '        PsxCheckpointObserver observer(*model,driver.time,\n'
            '            std::stoul(argument(argc,argv,"watch_address","ffffffff"),nullptr,16));\n'
            '        const uint64_t start=model->cycles;',
        '            for(unsigned i=0;i<4;i++) if(driver.next[i]==driver.time) {':
            '            observer.before(*model,driver.time,rising,\n'
            '                driver.next[1]==driver.time && !model->clk2x);\n'
            '            for(unsigned i=0;i<4;i++) if(driver.next[i]==driver.time) {',
        '            model->eval();': '            model->eval();\n'
            '            observer.after(*model,driver.time);',
    }
    for marker, replacement in markers.items():
        if text.count(marker) != 1:
            raise ValueError('Original observer driver marker changed: '+marker)
        text = text.replace(marker, replacement)
    source = work/'observer.cpp'
    source.write_text(text)
    inputs = [driver, extension, host, source, bound, Path(__file__), library,
              *objects, *obj.glob('*.h'), *runtime.glob('include/*.h')]
    if cpu_stalls or tag_window:
        inputs.append(ROOT/'sim/reference/psx_checkpoint_observer.h')
    protected.update({str(path):digest(path) for path in inputs})
    compiler = shutil.which('g++')
    if not compiler:
        raise ValueError('C++ compiler required')
    executable = work/'observer'
    with (work/'build.log').open('w') as log:
        subprocess.run([compiler, '-std=c++17', '-O2', '-I'+str(obj),
            '-I'+str(runtime/'include'), '-I'+str(host.parent), '-I'+str(work),
            str(source), str(library), *map(str,objects), '-pthread', '-latomic',
            '-o', str(executable)], stdout=log, stderr=subprocess.STDOUT,
            check=True, timeout=120)
    protected[str(executable)] = digest(executable)
    return executable, mapping


def run_case(directory, executable, state, fixed, maximum, capture, timeout, watch, protected):
    directory.mkdir()
    for name in ('boot.hex8', 'expected.hex', 'trackinfo.hex32', 'disc-host.json'):
        original = state.parent/name
        if original.exists():
            shutil.copy2(original, directory/name)
            protected[str(directory/name)] = digest(original)
    verify_hashes(protected)
    command = [str(executable), '+restore='+str(state), '+max_cycles='+str(maximum),
               '+capture_interval='+str(capture), '+watch_address='+format(watch,'x')]
    if 'disc_image_sha256' in fixed:
        disc = json.loads((state.parent/'disc-host.json').read_text())
        command += ['+diagnostic=0', '+disc_file='+disc['image'],
                    '+disc_metadata='+str(directory/'trackinfo.hex32'),
                    '+disc_sectors='+str(disc['sectors']), '+disc_index='+str(disc['index_sector'])]
    else:
        command += ['+diagnostic=1']
    started = time.monotonic()
    with (directory/'run.log').open('w') as log:
        result = subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout)
    if result.returncode:
        raise ValueError('Bounded observer/reference run failed: '+str(directory))
    return {'segment':json.loads((directory/'segment.json').read_text()),
            'elapsed_seconds':time.monotonic()-started}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-cycles', type=int, required=True)
    parser.add_argument('--capture-interval', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--watch-address', type=lambda x:int(x,0), default=0xffffffff)
    observe = parser.add_mutually_exclusive_group()
    observe.add_argument('--cpu-stalls', action='store_true',
                        help='Read pipeline-stall/memory-busy conditions each CPU edge; no model writes')
    observe.add_argument('--tag-read-window', action='store_true',
                         help='Compare a shadow falling-edge tag sample to the existing asynchronous tag at CPU edges')
    args = parser.parse_args()
    build, state = args.build_dir.resolve(), args.state.resolve()
    manifest_path = build/'build-manifest.json'
    sidecar_path = state.with_suffix('.json')
    manifest = json.loads(manifest_path.read_text())
    sidecar = json.loads(sidecar_path.read_text())
    fixed = sidecar['fixed_inputs']
    original = build/'obj/Vpsx_checkpoint'
    if (fixed['executable_sha256'] != digest(original) or
        fixed['build_manifest_sha256'] != digest(manifest_path) or
        sidecar['checkpoint_sha256'] != digest(state)):
        raise ValueError('Original checkpoint/build identity differs')
    if not sidecar['segment']['end_cycle'] < args.max_cycles <= MAX_CYCLES:
        raise ValueError('Observer must advance a positive bounded interval')
    if (args.timeout <= 0 or not 0 <= args.capture_interval <= MAX_CYCLES
            or not 0 <= args.watch_address <= 0xffffffff):
        raise ValueError('Invalid observer capture/timeout/address')
    protected = dict(manifest['protected_sha256'])
    protected.update({str(path):digest(path) for path in (manifest_path, state, sidecar_path)})
    for name, key in (('boot.hex8','boot_sha256'), ('expected.hex','expected_sha256'),
                      ('trackinfo.hex32','disc_metadata_sha256')):
        if key in fixed:
            path=state.parent/name
            if digest(path) != fixed[key]:
                raise ValueError('Checkpoint fixed input differs: '+name)
            protected[str(path)]=fixed[key]
    if 'disc_image_sha256' in fixed:
        disc_path = state.parent/'disc-host.json'
        disc = json.loads(disc_path.read_text())
        image, cue = Path(disc['image']), Path(disc['cue'])
        if digest(image) != fixed['disc_image_sha256'] or digest(cue) != fixed['cue_sha256']:
            raise ValueError('Local disc identity changed')
        protected.update({str(disc_path):digest(disc_path),
                          str(image):fixed['disc_image_sha256'], str(cue):fixed['cue_sha256']})
    verify_hashes(protected)
    work = args.output.resolve() if args.output else ROOT/'build/sim/checkpoint-observer'/\
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if work.exists() or not work.is_relative_to(ROOT) or subprocess.run(
        ['git','check-ignore','--quiet',str(work/'boot.hex8')],cwd=ROOT).returncode:
        raise ValueError('Use a fresh Git-ignored workspace directory')
    work.mkdir(parents=True)
    executable, mapping = build_observer(build,work,protected,args.cpu_stalls,args.tag_read_window)
    results = {}
    for name, program in (('original',original), ('observed',executable)):
        print('Bounded '+name+' driver run: '+str(work), flush=True)
        results[name] = run_case(work/name,program,state,fixed,args.max_cycles,
                                args.capture_interval,args.timeout,args.watch_address,protected)
    old, new = work/'original', work/'observed'
    names = ['final-state.bin','audio.pcm','execution.csv','pad-events.csv',
             *[name for name in ('main-ram.hex32','spu-ram.hex32') if (old/name).exists()],
             *sorted(p.name for p in old.glob('vram-*.hex32'))]
    equality = {name:digest(old/name)==digest(new/name) for name in names}
    # Host input/output timing remains in complete state, and host sector
    # records are compared separately whenever generated by this fixture.
    for path in old.glob('*.csv'):
        if path.name not in equality:
            equality[path.name]=digest(path)==digest(new/path.name)
    gp1 = re.findall(r'EVENT GP1 cycles=(\d+) data=([0-9a-fA-F]+)',(new/'run.log').read_text())
    observed_gp1 = []
    import csv
    with (new/'hardware-events.csv').open() as file:
        for row in csv.DictReader(file):
            if row['event']=='GP1':
                observed_gp1.append((row['cycles'],format(int(row['value_hex'],16),'08x')))
    acceptance_exact = gp1==observed_gp1 if gp1 else None
    verify_hashes(protected)
    segment_exact = results['original']['segment']==results['observed']['segment']
    passed = segment_exact and all(equality.values()) and acceptance_exact is not False
    result = {'passed':passed, 'original_checkpoint_identity_verified':True,
        'same_compiled_model':True, 'extra_model_evaluations':0, 'model_member_writes':0,
        'original_executable_sha256':digest(original), 'observer_executable_sha256':digest(executable),
        'complete_state_and_captures_equal':equality, 'segment_exact':segment_exact,
        'gp1_stdout_present':bool(gp1),'gp1_acceptance_exact':acceptance_exact,
        'runs':results,'bindings':mapping,'protected_sha256':protected,
        'cpu_pipeline_observer':args.cpu_stalls,
        'tag_read_window_observer':args.tag_read_window,
        'observer_trace_sha256':{name:digest(new/name) for name in
            ('hardware-events.csv','cpu-conditions.csv','tag-read-window.csv') if (new/name).exists()},
        'limitations':['Exact only for this bounded same-model driver comparison',
            'Raw video reports precede CPU clock-domain report buffering',
            'GP0 rows record input latches, not decoded renderer commands or FIFO acceptance',
            'CPU counters are sampled conditions, not physical bus utilization or retired instructions',
            'GPU_CPU_READ value is GPUSTAT only at address4; GP0 readback value is not traced',
            'Observer artifacts do not permit cross-configuration checkpoint restores'],
        'game_correctness_verified':False,'hardware_verified':False}
    (work/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({key:result[key] for key in ('passed','complete_state_and_captures_equal',
        'segment_exact','gp1_acceptance_exact')},indent=2)); print(work)
    if not passed:
        raise SystemExit('Observer changed behavior or GP1 binding differs; preserve '+str(work))


if __name__=='__main__':
    main()
