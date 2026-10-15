#!/usr/bin/env python3
"""Full cold/warm MiSTer resets and CPU execution through shared SDRAM pins.

Uses the physical variant's memory wiring, a fresh corrected conversion and
authored BIOS bytes. No old checkpoint, physical project or private image is
modified. This is not the optimized release core or an APF/game boot proof.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ghdl_verilog import normalize_generated_verilog
from prepare_config_cdc_system import config_bench
from prepare_main_reset_fence import fenced_top
from prepare_pocket_sdram_only import MEMORY
from prepare_resource_targets import digest, hardware_text
from prepare_reset_only import reset_only_source
from prepare_release_init import release_init_source
from prepare_shared_sdram import prepare as prepare_phy
from prepare_system_verilator import prepare
from reference_system_memory_program import write_program
from tool_paths import ROOT, find_tool


def replace(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Shared system fixture anchor changed: ' + before[:70])
    return text.replace(before, after)


def chip_text(cached_spu=False):
    text = (ROOT / 'sim/memory/sdr_pin_model.sv').read_text()
    text = replace(text, 'reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];',
                   'reg [15:0] memory [0:2228239];\n    reg [31:0] boot[0:131071];')
    begin = text.index('    function automatic integer slot(')
    end = text.index('    function automatic [24:0] fixture_address', begin)
    text = text[:begin] + '''    function automatic integer slot(input reg [24:0] a);
        if(a<25'h100000) slot=int'(a);
        else if(a>=25'h400000 && a<25'h480000) slot=1048576+int'(a-25'h400000);
        else if(a>=25'h480000 && a<25'h490000) slot=1572864+int'(a-25'h480000);
        else if(a>=25'h500000 && a<25'h510000) slot=1638400+int'(a-25'h500000);
        else if(a>=25'h800000 && a<25'h840000) slot=1703936+int'(a-25'h800000);
        else if(a>=25'h840000 && a<25'h880000) slot=1966080+int'(a-25'h840000);
        else if(a>=25'h1400000 && a<25'h1400004) slot=2228224+int'(a-25'h1400000);
        else if(a>=25'h1600000 && a<25'h1600004) slot=2228228+int'(a-25'h1600000);
        else if(a>=25'h1800000 && a<25'h1800004) slot=2228232+int'(a-25'h1800000);
        else if(a>=25'h1a00000 && a<25'h1a00004) slot=2228236+int'(a-25'h1a00000);
        else begin slot=0;$fatal(1,"Shared system SDRAM escaped physical map: %h",a);end
    endfunction
''' + text[end:]
    text = replace(text, '''        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576; i = i + 1)
            memory[i] = {initial_byte(integer'(fixture_address(i))*2+1), initial_byte(integer'(fixture_address(i))*2)};''',
        '''        for(integer i=0;i<2228240;i++) memory[i]=0;
        for(integer i=0;i<1572864;i++) memory[i]=16'ha5c3;
        for(integer i=1572864;i<1703936;i++) memory[i]=16'h6db7;
        for(integer i=1703936;i<1966080;i++) memory[i]=16'h3ca5;
        for(integer i=0;i<131072;i++) boot[i]=0;
        $readmemh("boot.hex",boot);
        for(integer i=0;i<131072;i++) begin
            memory[1966080+2*i]=boot[i][15:0];memory[1966080+2*i+1]=boot[i][31:16];
        end''')
    if cached_spu:
        text = replace(text, 'reg [15:0] memory [0:2228239];', 'reg [15:0] memory [0:2490383];')
        text = replace(text,
            "else if(a>=25'h800000 && a<25'h840000) slot=1703936+int'(a-25'h800000);",
            "else if(a>=25'h580000 && a<25'h5c0000) slot=1703936+int'(a-25'h580000);\n" +
            "        else if(a>=25'h800000 && a<25'h840000) slot=2228240+int'(a-25'h800000);")
        text = replace(text, 'for(integer i=0;i<2228240;i++) memory[i]=0;',
            "for(integer i=0;i<2228240;i++) memory[i]=0;\n        for(integer i=2228240;i<2490384;i++) memory[i]=16'h91e7;")
    return text


def bench_text(cached_spu=False, config_cdc=False):
    body = MEMORY
    body = replace(body, 'wire memory_locked,mem_fast_clk,mem_forwarded_clk;',
        '''wire memory_locked=1;reg mem_clk=0,mem_fast_clk=1;
    wire mem_forwarded_clk=~mem_fast_clk;
    always #8000000 mem_clk=~mem_clk;
    always #4000000 mem_fast_clk=~mem_fast_clk;''')
    body = replace(body, '''    psx_mem_clocks memory_clocks (.clk_74a(clk_74b),.reset_async(!power_reset_n),
        .clk_sys(mem_clk),.clk_mem(mem_fast_clk),.clk_dram(mem_forwarded_clk),.locked(memory_locked));
''', '')
    body = replace(body, '    assign clkvid=video_rgb_clock;\n', '')
    body = replace(body, '    assign reset=cpu_boot_reset;\n', '')
    if cached_spu:
        body = replace(body, "assign word_addr[3]=vram_word_addr+24'h200000;",
            "assign word_addr[3]=vram_word_addr+((cached_map_fault && vram_word_addr>=24'h0c0000 && "
            "vram_word_addr<24'h0e0000) ? 24'h340000:24'h200000);")
    # Gate both sides of client 0's acceptance, leaving the independent
    # reset-header/VRAM client live. Never gate only mem_ready after a backend
    # acceptance: that would replay a transaction instead of delaying it.
    body = replace(body,
        'assign packet_ready[client]=front_ready && shared_initialized;',
        'assign packet_ready[client]=front_ready && shared_initialized && (client!=0 || permit_main);')
    body = replace(body,
        '.req_valid(packet_valid[client] && shared_initialized)',
        '.req_valid(packet_valid[client] && shared_initialized && (client!=0 || permit_main))')
    aliases = '''reg transport_reset=1;
initial begin #100000000;transport_reset=0;end
wire power_reset_n=!transport_reset,cpu_locked=1,clk_74a=clk1x;
wire mem_initialized,mem_reset;
wire c_valid=0,c_write=0,c_rsp_ready=1,c_ready,c_rsp,c_error;
wire [24:0] c_addr=0;wire [3:0] c_count=1;
wire [127:0] c_data=0,c_read;wire [15:0] c_be=0;
wire [12:0] dram_a;wire [1:0] dram_ba,dram_dqm;
wire dram_ras_n,dram_cas_n,dram_we_n,dram_cke,dram_clk;
wire [15:0] dram_dq;
'''
    chip = '''sdr_pin_model chip (.clk(dram_clk),.cke(dram_cke),.ras_n(dram_ras_n),
 .cas_n(dram_cas_n),.we_n(dram_we_n),.address(dram_a),.bank(dram_ba),.dm(dram_dqm),
 .host_oe(memory.phy.oe_reg),.dq(dram_dq));
'''
    text = (ROOT / 'sim/reference/tb_psx_system_fast.sv').read_text()
    text = replace(text, '.isPaused(isPaused)',
                   '.isPaused(isPaused),.main_reset_request(main_reset_request),.main_reset_hold(main_reset_hold)')
    text = replace(text, '.ddr3_DOUT(ddr3_DOUT),', '.ddr3_DOUT(header_input),')
    begin = text.index('mister_sdram_model #(.BOOT_FILE(')
    end = text.index('integer cycles=0,checks=0,max_cycles=', begin)
    text = text[:begin] + aliases + body + chip + text[end:]
    text = replace(text, 'initial begin repeat(100) @(negedge clk1x);reset=0;end\n', '')
    text = replace(text, 'logic pause=0;', 'logic pause=1;')
    text = replace(text, '$display("PASS FAST SYSTEM cycles=%d checks=%d",cycles,checks);$finish;',
                   'diagnostic=0;pause=1;epoch_done=1;pause_start_cycle=cycles;')
    text = replace(text, 'endmodule\n',
        (ROOT / 'sim/pocket/shared_sdram_system_body.sv').read_text() + '\nendmodule\n')
    return config_bench(text) if config_cdc else text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-reset-repro', action='store_true',
        help='Fast localization only: skip full external clears; never a full-reset proof')
    parser.add_argument('--unpaused-cold-only', action='store_true',
        help='One cold entry with pause=0, like the Pocket top; checks committed diagnostics, not pre-execution clear bytes')
    parser.add_argument('--delay-main-reset', action='store_true',
        help='Delay an old warm-entry command beyond the first reset-header reply; check retained data before restart')
    parser.add_argument('--header-fault', choices=('early-ready','raw-data'),
        help='Private fault injection; requires the fast delayed-command fixture')
    parser.add_argument('--cached-spu', action='store_true',
        help='Evaluate pinned MiSTer cached SPU (SPUSDRAM=0), actual DDR clear mapping and legacy-window guards')
    parser.add_argument('--reset-only', action='store_true',
        help='Use the baseline save/load/EXE-disabled reset specialization')
    parser.add_argument('--release-init', action='store_true',
        help='Also use the lean initialization controller selected by the captured Pocket build')
    parser.add_argument('--config-cdc', action='store_true',
        help='Test coherent region/BIOS-bank delivery across an asynchronous host clock at cold/warm reset')
    args = parser.parse_args()
    if args.delay_main_reset and args.unpaused_cold_only:
        parser.error('The delayed-command proof needs warm entry')
    if args.header_fault and not (args.delay_main_reset and args.scratch_reset_repro):
        parser.error('Header faults require --delay-main-reset --scratch-reset-repro')
    if args.cached_spu and args.scratch_reset_repro:
        parser.error('Cached-SPU qualification requires the complete external reset clear')
    if args.release_init and not args.reset_only:
        parser.error('Lean initialization requires --reset-only')
    ghdl, verilator = find_tool('ghdl'), find_tool('verilator')
    if not ghdl or not verilator:
        raise SystemExit('GHDL synthesis and Verilator required')
    parent = ROOT / ('build/sim/shared-sdram-cached-spu' if args.cached_spu else 'build/sim/shared-sdram-system')
    if args.config_cdc: parent=parent.with_name(parent.name+'-config-cdc')
    if args.reset_only: parent=parent.with_name(parent.name+('-release-init' if args.release_init else '-reset-only'))
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    prepared = prepare(work / 'sources', fast_reset=args.scratch_reset_repro)
    reset_variant=(release_init_source(work/'release-init') if args.release_init else
                   reset_only_source(work/'reset-only') if args.reset_only else None)
    for source in prepared['sources']:
        path=Path(source['path'])
        if path.name=='savestates.vhd' and reset_variant:
            path.write_text(hardware_text(Path(reset_variant['path']).read_text()))
            source['selected']=reset_variant['path'];source['sha256']=digest(path)
            source['selected_sha256']=digest(Path(reset_variant['path']))
            source['transforms'].append('Lean Pocket reset initialization with save/load/EXE off' if args.release_init else
                                        'Baseline reset-only specialization with save/load/EXE off')
        if path.name in ('psx_top.vhd','psx_mister_psx_top_resource.vhd','memorymux.vhd'):
            path.write_text(fenced_top(path.read_text(),path.name))
            if path.name=='psx_mister_psx_top_resource.vhd' and args.cached_spu:
                path.write_text(replace(path.read_text(), "SPUSDRAM => '1',", "SPUSDRAM => '0',"))
                source['transforms'].append('Test-only pinned cached SPU selection: SPUSDRAM=0')
            if path.name=='psx_top.vhd' and args.header_fault:
                text=path.read_text()
                before,after=((' and not main_reset_guard;', ';') if args.header_fault=='early-ready' else
                    ("main_reset_data when main_reset_pending = '1' else ddr3_DOUT;",'ddr3_DOUT;'))
                path.write_text(replace(text,before,after))
            source['sha256']=digest(path)
            source['transforms'].append('Native CPU/memorymux reset fence and buffered reset-header reply')
    prepared['main_reset_fence']=1
    prepared['cached_spu']=args.cached_spu
    prepared['reset_only']=reset_variant
    selected_poly = next(s for s in prepared['sources'] if Path(s['path']).name == 'gpu_poly.vhd')
    if 'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))' not in Path(selected_poly['path']).read_text():
        raise ValueError('Corrected signed GPU conversion required')
    program = write_program(work / 'program')
    shutil.copyfile(program['program'], work / 'boot.hex')
    shutil.copyfile(program['expected'], work / 'expected.hex')
    phy = prepare_phy(work / 'phy')
    chip = work / 'chip.sv'; chip.write_text(chip_text(args.cached_spu))
    bench = work / 'tb.sv'; bench.write_text(bench_text(args.cached_spu, args.config_cdc))
    netlist = work / 'system.v'
    sources = [netlist, ROOT / 'sim/reference/mister_memory_models.sv', Path(phy['path']),
        ROOT / 'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
        *[ROOT / 'rtl/pocket' / n for n in ('psx_sdram_shared.sv', 'psx_spu_sdram_adapter.sv',
            'psx_mister_main_adapter.sv', 'psx_main_reset_fence.sv', 'psx_vram_stream.sv')],
        *[ROOT / 'rtl/memory' / n for n in ('psx_mem_bridge.sv', 'psx_mem_cdc.sv')],
        ROOT / 'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        ROOT / 'sim/memory/altddio_clock_model.sv', chip, bench]
    if args.config_cdc: sources.append(ROOT/'rtl/pocket/psx_config_cdc.sv')
    with (work / 'build.log').open('w') as log:
        for source in prepared['sources']:
            subprocess.run([ghdl, '-a', '--std=08', '-frelaxed', '--work=' + source['library'], source['path']],
                           cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=60, check=True)
        with netlist.open('w') as out:
            subprocess.run([ghdl, '--synth', '--std=08', '-frelaxed', '--vendor-library=ramip', '--out=verilog',
                'psx_mister_psx_top_resource'], cwd=work, stdout=out, stderr=log, check=True, timeout=180)
        prepared['verilog_normalization'] = normalize_generated_verilog(netlist)
        command = [verilator, '--binary', '--timing', '-UVERILATOR', '-O3', '-j', '4',
            *(['-DCACHED_SPU_SYSTEM'] if args.cached_spu else []),
            '-Wno-fatal', '--output-split', '20000', '--top-module', 'tb_psx_fast',
            '--Mdir', str(work / 'obj'), *map(str, sources)]
        subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    inputs = [*sources, ROOT / 'sim/memory/sdr_pin_model.sv', ROOT / 'sim/reference/tb_psx_system_fast.sv',
        ROOT / 'scripts/prepare_pocket_sdram_only.py', ROOT / 'scripts/prepare_shared_sdram.py',
        ROOT / 'scripts/prepare_main_reset_fence.py',
        ROOT / 'scripts/prepare_reset_only.py',
        ROOT / 'scripts/prepare_release_init.py',
        ROOT / 'scripts/prepare_system_verilator.py', ROOT / 'scripts/reference_system_memory_program.py',
        ROOT / 'sim/pocket/shared_sdram_system_body.sv', ROOT/'scripts/prepare_config_cdc_system.py',
        Path(__file__), work / 'boot.hex', work / 'expected.hex']
    for s in prepared['sources']:
        inputs.extend(Path(s[k]) for k in ('path', 'original', 'selected') if k in s)
    protected = {str(p): digest(p) for p in inputs}
    archives = {}
    for index, (name, sha) in enumerate(protected.items()):
        copy = work / 'input-archive' / str(index) / Path(name).name
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(name, copy)
        if digest(copy) != sha:
            raise ValueError('Shared system input changed during archive: ' + name)
        archives[name] = {'path': str(copy), 'sha256': sha}
    binary = work / 'obj/Vtb_psx_fast'
    runs = []
    epochs = 1 if args.unpaused_cold_only else 2
    profiles = [('normal', False)] if args.unpaused_cold_only else [('maintenance-fault', True), ('normal', False)]
    if args.cached_spu and not args.unpaused_cold_only:
        profiles.insert(0, ('cached-map-fault', True))
    for name, fault in profiles:
        run_args = [str(binary), '+max_cycles=40000000']
        if name=='maintenance-fault': run_args += ['+disable_maintenance']
        if name=='cached-map-fault': run_args += ['+cached_map_fault']
        if args.scratch_reset_repro:
            if fault:
                continue # No external SPU clear request exists in FASTSIM.
            run_args += ['+scratch_reset_repro']
        if args.unpaused_cold_only:
            run_args += ['+unpaused_cold_only']
        if args.delay_main_reset and not fault:
            run_args += ['+delay_main_reset']
        # Make progress and state diagnostics observable during long clears.
        if shutil.which('stdbuf'):
            run_args = [shutil.which('stdbuf'), '-oL', *run_args]
        path = work / (name + '.log' if fault else 'run.log')
        print('Running', name if fault else
              'unpaused cold entry' if args.unpaused_cold_only else 'cold and warm reset', flush=True)
        with path.open('w') as log:
            result = subprocess.run(run_args, cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        output = path.read_text()
        if args.header_fault:
            expected=('Reset header advanced before old main work drained' if args.header_fault=='early-ready' else
                'Buffered reset header lost ownership or retained data')
            accepted=result.returncode!=0 and expected in output
        else:
            marker = ('Cached SPU clear reached legacy scalar window' if name=='cached-map-fault' else
                      'Shared reset main request stalled while guest paused' if args.cached_spu else
                      'Shared reset SPU request stalled while guest paused')
            accepted = (result.returncode != 0 and marker in output
                if fault else result.returncode == 0 and output.count('PASS SHARED SDRAM SYSTEM') == 1 and
                output.count('SHARED_RESET epoch=') == epochs and output.count('SHARED_DIAGNOSTIC epoch=') == epochs)
            accepted = bool(accepted and (fault or not re.search(r'%Fatal|%Error|Assertion failed', output)))
        if args.delay_main_reset and not fault and not args.header_fault:
            accepted = accepted and output.count('RESET_HEADER_BUFFER_PASS') == 1
        if args.config_cdc and not fault:
            accepted = accepted and output.count('CONFIG_NATIVE epoch=') == epochs
        runs.append(dict(name=name,fault=fault,accepted=accepted,exit_code=result.returncode,output=output,log_sha256=digest(path)))
        print(output[-3000:], flush=True)
        if not accepted:
            break
    identity = all(digest(Path(p)) == h for p, h in protected.items())
    passed = identity and len(runs) == (1 if args.scratch_reset_repro else len(profiles)) and all(r['accepted'] for r in runs)
    report = dict(passed=passed, source_identity_verified=identity, source_sha256=protected,
        preparation=prepared, phy=phy, compiler_command=command, binary_sha256=digest(binary), runs=runs,
        input_archives=archives, unpaused_cold_only=args.unpaused_cold_only,
        cached_spu=args.cached_spu,
        reset_only=args.reset_only,
        release_init=args.release_init,
        configuration_snapshot_verified=passed and args.config_cdc,
        physical_SPU_byte_base=0xb00000 if args.cached_spu else 0x1000000,
        cached_map_fault_rejected=passed and args.cached_spu and not args.unpaused_cold_only,
        header_fault=args.header_fault, header_fault_rejected=passed and bool(args.header_fault),
        delayed_main_reset_verified=passed and args.delay_main_reset and not args.header_fault,
        full_reset_clear_verified=passed and not args.scratch_reset_repro and not args.unpaused_cold_only,
        maintenance_fault_exercised=not args.scratch_reset_repro and not args.unpaused_cold_only,
        physical_memory_model_verified=passed and not args.scratch_reset_repro,
        scratch_reset_reproducer=args.scratch_reset_repro, optimized_release_core_verified=False,
        pin_profile={'tAC_ns':2.5,'tHZ_ns':2.5},
        limitations=['Original connected MiSTer CPU/GTE/GPU/SPU with tested simulation transformations',
            'Authored diagnostic BIOS preloaded into model; no APF loader or private BIOS/game',
            'No active graphics/audio/CD workload or production bandwidth assertion',
            'Full reset/diagnostic interaction, not every asynchronous reload/freeze boundary',
            'Known maximum pin-delay and fitted SDRAM input failures remain open'],
        hardware_verified=False, game_verified=False, timing_verified=False, full_APF_top_connected=False)
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(work / 'report.json', flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
