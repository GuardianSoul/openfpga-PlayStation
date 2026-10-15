#!/usr/bin/env python3
"""Measure actual SPU cadence through the native MIRLO SDRAM pin path.

This is a measurement, not a guarantee that every profile meets real time.
The report distinguishes a valid measurement from audio_deadline_met.
Authored inputs only; no BIOS, disc, fitter, or controller changes.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ghdl_verilog import normalize_generated_verilog
from prepare_pocket_audio_stream import capture_spu_text
from prepare_resource_targets import digest
from prepare_shared_sdram import prepare as prepare_phy
from prepare_spu_context_ram import context_ram_source
from prepare_spu_guest_m10k import guest_m10k_text
from prepare_spu_reverb_table import reverb_table_text
from prepare_system_verilator import prepare
from tool_paths import ROOT, find_tool


def chip_text():
    text = (ROOT / 'sim/memory/sdr_pin_model.sv').read_text()
    text = text.replace('reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];',
                        'reg [15:0] memory [0:270335];')
    start = text.index('    function automatic integer slot(')
    end = text.index('    function automatic [24:0] fixture_address', start)
    text = text[:start] + '''    function automatic integer slot(input reg [24:0] a);
        if(a>=25'h800000 && a<25'h840000) slot=int'(a-25'h800000);
        else if(a<25'h2000) slot=262144+int'(a);
        else begin slot=0;$fatal(1,"SPU SDRAM escaped fixture map %h",a);end
    endfunction
''' + text[end:]
    before = '''        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576; i = i + 1)
            memory[i] = {initial_byte(integer'(fixture_address(i))*2+1), initial_byte(integer'(fixture_address(i))*2)};'''
    if text.count(before) != 1:
        raise ValueError('SDRAM initialization anchor changed')
    return text.replace(before, '''        for(integer i=0;i<262144;i++) memory[i]=0;
        for(integer i=262144;i<270336;i++) memory[i]=16'h3ca5;
        for(integer v=0;v<24;v++) begin
            memory[(v+64)*2048]=16'h0700;
            for(integer i=1;i<8;i++) memory[(v+64)*2048+i]=16'h7777;
        end''')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached', action='store_true', help='Measure pinned MiSTer cached DDR port through the native burst frontend')
    parser.add_argument('--scalar-trace', action='store_true', help='Record scalar requests for buffer/reuse analysis; never changes the memory service')
    parser.add_argument('--scalar-buffer', action='store_true', help='Evaluate private 32-line scalar read buffer; no production selection')
    parser.add_argument('--scalar-buffer-ways',type=int,choices=(1,2),default=1)
    parser.add_argument('--scalar-buffer-storage',choices=('MLAB','M10K','M10K-dual','M10K-stream'),default='MLAB')
    parser.add_argument('--scalar-stream',action='store_true',help='64 ADPCM blocks per voice; constant-payload streaming with real cold fills')
    parser.add_argument('--scalar-extended',action='store_true',help='Live guest DMA rewrite and variable-payload waveform comparison for scalar buffer')
    parser.add_argument('--pitch',type=int,default=4096)
    args = parser.parse_args()
    if args.cached and args.scalar_trace:parser.error('Scalar traces require the scalar SPU port')
    if args.cached and args.scalar_buffer:parser.error('Scalar buffer requires the scalar SPU port')
    if args.cached and args.scalar_stream:parser.error('Cached mode already has its own streaming phase')
    if args.scalar_extended and (not args.scalar_buffer or args.scalar_stream):
        parser.error('Scalar extended mode requires --scalar-buffer and supplies its own streaming phase')
    if args.scalar_buffer_storage!='MLAB' and not (args.scalar_buffer and args.scalar_buffer_ways==2):
        parser.error('M10K evaluation requires the two-way scalar buffer')
    parent = ROOT / ('build/sim/spu-cached-memory' if args.cached else
                     'build/sim/spu-scalar-buffer' if args.scalar_buffer else 'build/sim/spu-memory-cadence')
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    ghdl, verilator = find_tool('ghdl'), find_tool('verilator')
    if not ghdl or not verilator:
        raise ValueError('GHDL and Verilator required')
    prepared = prepare(work / 'sources')
    original = next(s for s in prepared['sources'] if Path(s['path']).name == 'spu.vhd')
    context = context_ram_source(work / 'context', original['path'])
    text = reverb_table_text(Path(context['path']).read_text())
    text = guest_m10k_text(text.replace('entity ramip.RamMLAB', 'entity mem.RamMLAB'))
    text = text.replace('entity mem.dpram', 'entity ramip.dpram').replace('entity mem.RamMLAB', 'entity ramip.RamMLAB')
    spu = work / 'spu.vhd'; spu.write_text(capture_spu_text(text))
    netlist = work / 'spu.v'
    with (work / 'build.log').open('w') as log:
        for source in prepared['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + source['library'], source['path']],
                           cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', str(spu)], cwd=work,
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        with netlist.open('w') as output:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip',
                            '--out=verilog', 'spu'], cwd=work, stdout=output, stderr=log,
                           check=True, timeout=180)
    normalize_generated_verilog(netlist)
    template = ROOT / 'sim/reference/tb_spu_cd_mix.sv'
    body = ROOT / 'sim/pocket/spu_memory_cadence_body.sv'
    text = template.read_text()
    text = text.replace('logic [31:0] sdram_dataRead=0;', 'wire [31:0] sdram_dataRead;')
    text = text.replace('logic sdram_done=0;', 'wire sdram_done;')
    if args.cached:
        for declaration in ('logic mem_ack=0;', 'logic [63:0] mem_DOUT=0;', 'logic mem_DOUT_READY=0;'):
            text = text.replace(declaration, declaration.replace('logic', 'wire').replace('=0', ''))
    module = re.search(r'^module (spu(?:_Barch[^\s]*)?)\s*\n(.*?\);)', netlist.read_text(), re.M | re.S)
    names = {p.lower(): p for p in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)', module[2])}
    instance = re.search(r'SPU_ENTITY spu_core\([^\n]+\);', text)[0]
    mapped = re.sub(r'\.(\w+)\(', lambda m: '.' + names[m[1].lower()] + '(', instance)
    mapped = mapped.replace('SPU_ENTITY', module[1])[:-2] + ',.' + names['sound_out_valid'] + '(pair_valid));'
    text = text.replace(instance, 'wire pair_valid;\n' + mapped)
    memory = re.search(r'mister_sdram_model #\(.*?\) memory\(.*?\);', text, re.S)[0]
    memory = memory.replace('.req(sdram_ena)', '.req(sdram_ena && !native_memory)')
    memory = memory.replace('.do32(sdram_dataRead),.done(sdram_done)', '.do32(functional_data),.done(functional_done)')
    start = text.index('integer checks=0,ticks=0;')
    # Keep original ports, clocks and the tested MiSTer phase-index generator.
    text = text[:start].replace(re.search(r'mister_sdram_model #\(.*?\) memory\(.*?\);', text, re.S)[0], memory)
    cached_body = ROOT / 'sim/pocket/spu_cached_memory_body.sv'
    text += body.read_text().replace('// CACHED_MEMORY_PORT', cached_body.read_text() if args.cached else '')
    if args.scalar_buffer_storage=='M10K-stream':
        from prepare_spu_scalar_stream_buffer import connect
        text=connect(text)
    extended_body=ROOT/'sim/pocket/spu_scalar_extended_body.sv'
    if args.scalar_extended:
        anchor='initial begin\n    native_memory=$test$plusargs("native");'
        if text.count(anchor)!=1:raise ValueError('Scalar extended setup anchor changed')
        text=text.replace(anchor,extended_body.read_text()+'\n'+anchor)
        text=text.replace('    measure(2);','    measure(2);\n    if(!negative_control) scalar_extra();')
        if args.scalar_buffer_storage=='M10K-stream':
            text=text.replace('.p_rsp_rdata(rd),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data)',
                ".p_rsp_rdata(rd),.fill_valid(scalar_fill_valid),.fill_word(scalar_fill_word),.fill_data(scalar_fill_data ^ (scalar_corrupt_read ? 32'b1:32'b0))")
        else:
            text=text.replace('.p_rsp_error(re),.p_rsp_rdata(rd),',
                              ".p_rsp_error(re),.p_rsp_rdata(rd ^ (scalar_corrupt_read ? 128'h10000:128'b0)),")
    bench = work / 'bench.sv'; bench.write_text(text)
    chip = work / 'chip.sv'; chip.write_text(chip_text())
    phy = prepare_phy(work / 'phy')
    coefficients = None
    sources = [netlist, ROOT / 'sim/reference/mister_memory_models.sv', Path(phy['path']),
        ROOT / 'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
        *[ROOT / 'rtl/pocket' / n for n in ('psx_sdram_shared.sv', 'psx_spu_sdram_adapter.sv')],
        *[ROOT / 'rtl/memory' / n for n in ('psx_mem_bridge.sv', 'psx_mem_cdc.sv')],
        ROOT / 'sim/memory/altddio_clock_model.sv', chip, bench]
    scalar_buffer=None
    if args.scalar_buffer:
        if args.scalar_buffer_storage=='M10K-stream':
            from prepare_spu_scalar_stream_buffer import prepare as prepare_buffer
            scalar_buffer=prepare_buffer(work/'scalar-buffer')
            sources += [Path(scalar_buffer['bridge_path'])]
        elif args.scalar_buffer_storage=='M10K-dual':
            from prepare_spu_scalar_dual_buffer import prepare as prepare_buffer
            scalar_buffer=prepare_buffer(work/'scalar-buffer')
        elif args.scalar_buffer_storage=='M10K':
            from prepare_spu_scalar_bram_buffer import prepare as prepare_buffer
            scalar_buffer=prepare_buffer(work/'scalar-buffer')
        else:
            from prepare_spu_scalar_buffer import prepare as prepare_buffer
            scalar_buffer=prepare_buffer(work/'scalar-buffer',args.scalar_buffer_ways)
        sources=[Path(scalar_buffer['path']) if p==ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv' else p for p in sources]
    if args.cached:
        sources += [ROOT / 'rtl/pocket/psx_vram_stream.sv',
                    ROOT / 'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv']
    with_audio=args.cached or args.scalar_buffer
    if with_audio:
        from generate_pocket_audio_coeffs import generate
        coefficients = generate(work / 'coefficients')
        sources += [ROOT / 'rtl/audio' / n for n in ('psx_audio_bridge.sv', 'psx_audio_sinc.sv',
                                                    'psx_audio_rate.sv', 'psx_audio_i2s.sv')]
        bridge_body = ROOT / 'sim/pocket/spu_audio_bridge_body.sv'
        bridge_text = bridge_body.read_text().replace('audio_pair_valid', 'pair_valid').replace('_candidate', '')
        if args.scalar_buffer:
            bridge_text=bridge_text.replace("if(bridge_fault!==1'b0)","if(bridge_fault!==1'b0 && !negative_control)")
        text = text.replace('// AUDIO_BRIDGE', bridge_text)
        bench.write_text(text)
    inputs = [*sources, spu, template, body, ROOT / 'sim/memory/sdr_pin_model.sv', Path(__file__),
        *[ROOT / 'scripts' / n for n in ('prepare_shared_sdram.py', 'prepare_pocket_audio_stream.py',
          'prepare_spu_context_ram.py', 'prepare_spu_guest_m10k.py', 'prepare_spu_reverb_table.py',
          'prepare_system_verilator.py')]]
    if args.cached:
        inputs += [cached_body]
    if with_audio:
        inputs += [bridge_body, ROOT / 'scripts/generate_pocket_audio_coeffs.py',Path(coefficients['compact_path'])]
    if args.scalar_buffer:inputs += [ROOT/'scripts/prepare_spu_scalar_buffer.py',ROOT/'rtl/pocket/psx_spu_sdram_adapter.sv']
    if args.scalar_buffer_storage!='MLAB':inputs += [ROOT/'scripts/prepare_spu_scalar_bram_buffer.py']
    if args.scalar_buffer_storage in ('M10K-dual','M10K-stream'):inputs += [ROOT/'scripts/prepare_spu_scalar_dual_buffer.py']
    if args.scalar_buffer_storage=='M10K-stream':inputs += [ROOT/'scripts/prepare_spu_scalar_stream_buffer.py']
    if args.scalar_extended:inputs += [extended_body]
    for s in prepared['sources']:
        inputs.extend(Path(s[k]) for k in ('path', 'original', 'selected') if k in s)
    hashes = {str(p): digest(p) for p in inputs}
    archives = {}
    for index, (name, sha) in enumerate(hashes.items()):
        copy = work / 'input-archive' / str(index) / Path(name).name
        copy.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(name, copy)
        if digest(copy) != sha: raise ValueError('Source changed during archive: ' + name)
        archives[name] = {'path': str(copy), 'sha256': sha}
    command = [verilator, '--binary', '--timing', '-O3', '-j', '4', '-Wno-fatal',
               *(['-DCACHED_SPU'] if args.cached else []),
               *(['-DSCALAR_BUFFER_SPU_AUDIO'] if args.scalar_buffer else []),
               *(['-DCOEFFICIENT_FILE="' + coefficients['compact_path'] + '"'] if with_audio else []),
               '--top-module', 'tb_spu_mix', '--Mdir', str(work / 'obj'), *map(str, sources)]
    with (work / 'compile.log').open('w') as log:
        subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    binary = work / 'obj/Vtb_spu_mix'
    runs = []
    profiles = [('functional', []), ('native-alone', ['+native']),
                       ('native-contended', ['+native', '+contended']),
                       ('delayed-negative-control', ['+native', '+negative_control', '+delay=1200'])]
    if args.cached:
        profiles = profiles[:3] + [('cached-data-fault', ['+native', '+corrupt_read']),
                                  ('cached-write-fault', ['+native', '+drop_writes']),
                                  ('cached-drain-fault', ['+native', '+contended', '+withdraw_background']),
                                  ('cached-pause-fault', ['+unsafe_pause'])]
    if args.scalar_extended:profiles += [('scalar-data-fault',['+native','+scalar_corrupt_read'])]
    reference_pcm = None
    for name, run_args in profiles:
        run_args=[*run_args,'+pitch='+str(args.pitch)]
        if args.scalar_stream or args.scalar_buffer and name=='delayed-negative-control':run_args += ['+scalar_stream']
        pcm = work / (name + '-stream.csv')
        scalar_trace=work/(name+'-scalar.csv')
        if args.scalar_trace:run_args=[*run_args,'+scalar_trace='+str(scalar_trace)]
        result = subprocess.run([str(binary), *run_args, '+pcm_path=' + str(pcm)], cwd=work, capture_output=True, text=True, timeout=240)
        output = result.stdout + result.stderr
        path = work / (name + '.log'); path.write_text(output)
        phases = [json.loads(line.split('CADENCE ', 1)[1]) for line in output.splitlines() if line.startswith('CADENCE ')]
        negative = name == 'delayed-negative-control' or name.endswith('-fault')
        expected_phases=5 if args.cached or args.scalar_extended and not negative else 3
        valid = result.returncode == 0 and 'PASS SPU memory measurement' in output and len(phases) == expected_phases
        if name.endswith('-fault'):
            marker = {'scalar-data-fault':'Scalar SPU byte mismatch',
                      'cached-drain-fault':'SPU native memory protocol fault shared=1',
                      'cached-pause-fault':'Cached SPU unsafe pause lost completion'}.get(name, 'Cached SPU burst byte mismatch')
            valid = result.returncode != 0 and marker in output
        elif negative:
            valid &= any(p['timeouts'] > 0 for p in phases)
        else:
            valid &= all(p['pcm_checks'] >= 256 for p in phases[1:4 if args.cached or args.scalar_extended else 3])
            if args.scalar_buffer:valid &= 'PASS actual SPU audio bridge:' in output
        pcm_record = None
        if (args.cached or args.scalar_extended) and not negative and valid:
            samples = pcm.read_text().splitlines()
            if reference_pcm is None: reference_pcm = samples
            same = samples == reference_pcm
            valid &= same and len(samples) == 1024
            values=[tuple(map(int,line.split(',')[1:])) for line in samples]
            distinct=len(set(values));nonzero=sum(pair!=(0,0) for pair in values)
            valid &= distinct>=10 and nonzero>=1000
            valid &= 'PASS actual SPU audio bridge:' in output
            pcm_record = dict(path=str(pcm), sha256=digest(pcm), pairs=len(samples), reference_equal=same,
                              distinct_pairs=distinct,nonzero_pairs=nonzero)
        record = dict(name=name, exit_code=result.returncode, measurement_valid=bool(valid), phases=phases,
                      negative_control_detected=negative and bool(valid), log=str(path), log_sha256=digest(path),
                      streaming_pcm=pcm_record)
        if args.scalar_trace:
            record['scalar_trace']=dict(path=str(scalar_trace),sha256=digest(scalar_trace),
                                       requests=sum(1 for _ in scalar_trace.open()))
        runs.append(record); print(name + ': ' + output[-2400:], flush=True)
        if not valid: break
    identity = all(digest(Path(p)) == h for p, h in hashes.items())
    report = dict(passed=identity and len(runs) == len(profiles) and all(r['measurement_valid'] for r in runs),
        source_identity_verified=identity, source_sha256=hashes, input_archives=archives,
        preparation=prepared, phy=phy, compiler_command=command, binary_sha256=digest(binary), runs=runs,
        source_clock_period_ns=29.5251, memory_clocks_mhz=[62.5, 125], forwarded_phase_ns=4,
        cached_mode=args.cached, coefficients=coefficients,scalar_trace_enabled=args.scalar_trace,
        scalar_buffer=scalar_buffer,
        actual_audio_bridge_connected=with_audio,
        scalar_stream=args.scalar_stream,pitch=args.pitch,
        scalar_extended=args.scalar_extended,
        hardware_verified=False, full_game_verified=False, topology_selected=False,
        limitations=[('Authored loops,192halfword live DMA rewrite,24x64block stream at4xpitch,zero-coefficient reverb; no game trace'
                      if args.cached or args.scalar_extended else 'Authored constant looping ADPCM, fixed pitch, zero-coefficient reverb; no game trace'),
            'Background clients saturate authored read ports; not actual concurrent GPU/CPU/CD traffic',
            'Native pin model at nominal clocks, not board timing or new PSRAM controller',
            'passed means measurement/byte oracles valid; inspect per-phase audio_deadline_met'])
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(work / 'report.json', flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
