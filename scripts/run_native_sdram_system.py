#!/usr/bin/env python3
"""Run the unchanged MiSTer CPU diagnostic through the hardware-selected SDRAM.

This proof uses nominal pin delays, an authored BIOS preload and the verified
baseline netlist. It does not claim a release candidate, APF boot or timing.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_mirlo_sdram_phy import prepare
from prepare_resource_targets import digest
from reference_system_memory_program import write_program
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-build',type=Path,default=ROOT/'build/sim/system-verilator')
    args=parser.parse_args();source_dir=args.source_build.resolve()
    build_path=source_dir/'build-manifest.json';build=json.loads(build_path.read_text())
    if not build['preparation']['fast_reset']:parser.error('Requires the explicit scratch-reset baseline')
    protected={str(build_path):digest(build_path)}
    for source in build['preparation']['sources']:
        for key,sha in [('path','sha256'),('original','original_sha256'),('selected','selected_sha256')]:
            if key in source:
                if digest(Path(source[key]))!=source[sha]:parser.error('Baseline source changed: '+source[key])
                protected[source[key]]=source[sha]
    netlist=source_dir/'system.v'
    if digest(netlist)!=build['verilog_sha256']:parser.error('Baseline netlist changed')
    protected[str(netlist)]=digest(netlist)
    parent=ROOT/'build/sim/native-sdram-system';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    cloned=work/'system.v';shutil.copyfile(netlist,cloned)
    program=write_program(work/'program')
    shutil.copyfile(program['program'],work/'boot.hex');shutil.copyfile(program['expected'],work/'expected.hex')
    phy=prepare(work/'phy')
    original_chip=ROOT/'sim/memory/sdr_pin_model.sv';text=original_chip.read_text()
    text=text.replace('reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];','reg [15:0] memory [0:1310719];')
    start=text.index('    function automatic integer slot(');end=text.index('    function automatic [24:0] fixture_address',start)
    text=text[:start]+'''    function automatic integer slot(input reg [24:0] a);
        if(a<25'h100000) slot=int'(a);
        else if(a>=25'h840000 && a<25'h880000) slot=1048576+int'(a-25'h840000);
        else begin slot=0;$fatal(1,"Native SDRAM address outside RAM/BIOS: %h",a);end
    endfunction
'''+text[end:]
    before='''        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576; i = i + 1)
            memory[i] = {initial_byte(integer'(fixture_address(i))*2+1), initial_byte(integer'(fixture_address(i))*2)};'''
    if text.count(before)!=1:raise ValueError('Pin-model storage initialization changed')
    text=text.replace(before,'''        for(integer i=0;i<1310720;i++) memory[i]=0;
        for(integer i=0;i<131072;i++) boot[i]=0;
        $readmemh("boot.hex",boot);
        for(integer i=0;i<131072;i++) begin
            memory[1048576+2*i]=boot[i][15:0];memory[1048576+2*i+1]=boot[i][31:16];
        end''')
    text=text.replace('    reg [3:0] opened = 0;','    reg [31:0] boot[0:131071];\n    reg [3:0] opened = 0;')
    chip=work/'chip.sv';chip.write_text(text)
    original_bench=ROOT/'sim/reference/tb_psx_system_fast.sv';text=original_bench.read_text()
    start=text.index('mister_sdram_model #(.BOOT_FILE(');end=text.index('mister_sdram_model #(.MEMORY_BYTES(',start)
    text=text[:start]+'''native_sdram_memory main_memory (
 .clk1x(clk1x),.clk3x(clk3x),.core_ce(core.component_core.ce),.reset_async(native_reset),.guest_reset(reset),
 .ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),.ram_adr(ram_Adr),
 .ram_cntDMA(ram_cntDMA),.ram_dataWrite(ram_dataWrite),.ram_be(ram_be),.ram_done(ram_done),
 .ram_dataRead32(ram_dataRead32),.dma_wr(dma_wr),.dma_data(dma_data),.dma_reqprocessed(dma_reqprocessed),
 .cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
 .ram_dmafifo_adr(ram_dmafifo_adr),.ram_dmafifo_data(ram_dmafifo_data),
 .ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read));
'''+text[end:]
    before='initial begin repeat(100) @(negedge clk1x);reset=0;end'
    if text.count(before)!=1:raise ValueError('CPU reset anchor changed')
    text=text.replace(before,'''reg native_reset=1;
initial begin #100000000;native_reset=0;end // 100 ns, parent precision fs
initial begin wait(main_memory.initialized);repeat(8) @(negedge clk1x);reset=0;end''')
    before='$display("PASS FAST SYSTEM cycles=%d checks=%d",cycles,checks);$finish;'
    if text.count(before)!=1:raise ValueError('Diagnostic completion anchor changed')
    text=text.replace(before,'''diagnostic=0;
   fork begin
    @(negedge clk1x);reset=1;
    wait(main_memory.source_idle && main_memory.service_idle && main_memory.fabric_idle);
    for(integer i=0;i<2648;i++)
     if({main_memory.chip.memory[2048+2*i+1],main_memory.chip.memory[2048+2*i]}!==expected[i])
      $fatal(1,"Native SDRAM committed result mismatch index=%0d",i);
    if({main_memory.chip.memory[32641],main_memory.chip.memory[32640]}!==32'h600df00d)
     $fatal(1,"Native SDRAM sentinel did not physically commit");
    $display("PASS NATIVE SDRAM SYSTEM cycles=%d checks=%d packets=%d cache_words=%d dma_words=%d committed_bytes=10596",
     cycles,checks,main_memory.packets,main_memory.cache_words,main_memory.dma_words);$finish;
   end join_none''')
    bench=work/'tb.sv';bench.write_text(text)
    sources=[cloned,ROOT/'sim/reference/mister_memory_models.sv',Path(phy['path']),
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
        *[ROOT/'rtl/memory'/n for n in ('psx_mem_bridge.sv','psx_sdram.sv','psx_sdram_pins.sv','psx_mem_cdc.sv')],
        ROOT/'rtl/pocket/psx_mister_main_adapter.sv',ROOT/'sim/memory/altddio_clock_model.sv',
        ROOT/'sim/pocket/native_sdram_memory.sv',chip,bench]
    for path in [*sources,original_chip,original_bench,ROOT/'scripts/prepare_mirlo_sdram_phy.py',
        ROOT/'references/MIRLO64/rtl/soc/sdr_phy.sv',ROOT/'scripts/reference_system_memory_program.py',
        Path(__file__),work/'boot.hex',work/'expected.hex']:
        protected[str(path)]=digest(path)
    command=[find_tool('verilator'),'--binary','--timing','-UVERILATOR','-O3','-j','4','-Wno-fatal',
        '--output-split','20000','--top-module','tb_psx_fast','--Mdir',str(work/'obj'),*map(str,sources)]
    with (work/'build.log').open('w') as log:
        subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    binary=work/'obj/Vtb_psx_fast'
    result=subprocess.run([str(binary),'+max_cycles=2000000'],cwd=work,capture_output=True,text=True,timeout=180)
    output=result.stdout+result.stderr;(work/'run.log').write_text(output)
    faulty=subprocess.run([str(binary),'+max_cycles=2000000','+drop_final_write'],
        cwd=work,capture_output=True,text=True,timeout=180)
    fault_output=faulty.stdout+faulty.stderr;(work/'dropped-final-write.log').write_text(fault_output)
    fault_rejected=faulty.returncode!=0 and 'Native SDRAM sentinel did not physically commit' in fault_output
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    passed=identity and result.returncode==0 and output.count('PASS NATIVE SDRAM SYSTEM')==1 and bool(re.search(r'checks=\s*2648\b',output)) and fault_rejected
    report=dict(passed=passed,source_identity_verified=identity,source_sha256=protected,
        binary_sha256=digest(binary),compiler_command=command,exit_code=result.returncode,output=output,
        baseline_build=str(build_path),baseline_build_sha256=digest(build_path),phy=phy,
        run_log_sha256=digest(work/'run.log'),
        faults=[dict(name='dropped-final-write',rejected=fault_rejected,exit_code=faulty.returncode,
            output=fault_output,log_sha256=digest(work/'dropped-final-write.log'))],
        CPU_variant='unchanged verified scratch-reset baseline; not selected resource candidate',
        physical_model_commit_verified=passed,nominal_pin_profile={'tAC_ns':2.5,'tHZ_ns':2.5},
        limitations=['Authored 2648-result CPU diagnostic; no BIOS/game files',
            'Direct authored BIOS chip-model preload, not APF loader',
            'SPU/GPU backing remains independent behavioral models',
            'No source contention or production boot/reload ownership proof',
            'Known maximum tAC model failure and negative fitted input timing remain unresolved'],
        full_top_connected=False,release_variant_verified=False,hardware_verified=False,game_verified=False,timing_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(output[-2000:],flush=True)
    print('Dropped final write rejected:',fault_rejected,flush=True);print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
