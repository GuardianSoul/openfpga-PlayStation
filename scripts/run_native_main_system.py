#!/usr/bin/env python3
"""Execute the MiSTer CPU/memorymux through native main PSRAM and pin models.

A verified baseline netlist is reused without changing its CPU/GTE/GPU variants.
This is an authored RAM diagnostic, not the release candidate or a game run.
"""
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_psram_burst import prepare
from prepare_resource_targets import digest
from reference_system_memory_program import write_program
from run_main_memory import bench_text
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-build',type=Path,default=ROOT/'build/sim/system-verilator')
    args=parser.parse_args();source_dir=args.source_build.resolve()
    build_path=source_dir/'build-manifest.json';build=json.loads(build_path.read_text())
    if not build['preparation']['fast_reset']:parser.error('This diagnostic requires the explicit scratch-only reset baseline')
    protected={str(build_path):digest(build_path)}
    for source in build['preparation']['sources']:
        for key,sha in [('path','sha256'),('original','original_sha256'),('selected','selected_sha256')]:
            if key in source:
                if digest(Path(source[key]))!=source[sha]:parser.error('Baseline source changed: '+source[key])
                protected[source[key]]=source[sha]
    netlist=source_dir/'system.v'
    if digest(netlist)!=build['verilog_sha256']:parser.error('Baseline netlist changed')
    protected[str(netlist)]=digest(netlist)
    directory=ROOT/'build/sim/native-main-system';directory.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=directory))
    cloned=work/'system.v';shutil.copyfile(netlist,cloned)
    program=write_program(work/'program')
    data=b''.join(int(row,16).to_bytes(4,'little') for row in program['program'].read_text().splitlines())
    (work/'boot.hex8').write_text(''.join(f'{x:02x}\n' for x in data))
    shutil.copyfile(program['expected'],work/'expected.hex')
    controller=prepare(work/'controller')
    # Use the real controller and PHY. Only storage geometry/preload changes;
    # the independent chip command and delay checks remain intact.
    original_chip=ROOT/'sim/memory/cellularram_burst_model.sv';text=original_chip.read_text()
    start=text.index('    function automatic integer slot(');end=text.index('    reg [15:0] bcr',start)
    text=text[:start]+'''    function automatic integer slot(input [22:0] word);
        if(word<23'h080000) slot=int'(word);
        else if(word>=23'h420000 && word<23'h440000) slot=524288+int'(word-23'h420000);
        else begin slot=0;$fatal(1,"Native system physical address outside main/BIOS allocation: %h",word);end
    endfunction
    reg [7:0] memory[0:1310719];
    reg [7:0] bios[0:524287];
    initial begin
        for(integer i=0;i<1310720;i++) memory[i]=0;
        for(integer i=0;i<524288;i++) bios[i]=0;
        $readmemh("boot.hex8",bios);
        for(integer i=0;i<131072;i++) begin
            memory[2*(524288+i)]=bios[4*i+HIGH_HALF*2];
            memory[2*(524288+i)+1]=bios[4*i+HIGH_HALF*2+1];
        end
    end
'''+text[end:]
    chip=work/'chip.sv';chip.write_text(text)
    ports=[('clk1x',1,'input'),('clk3x',1,'input'),('core_ce',1,'input'),('reset_async',1,'input'),('guest_reset',1,'input'),
        ('ram_ena',1,'input'),('ram_rnw',1,'input'),('ram_dma',1,'input'),('ram_cache',1,'input'),
        ('ram_adr',25,'input'),('ram_cntDMA',2,'input'),('ram_dataWrite',32,'input'),('ram_be',4,'input'),
        ('ram_done',1,'output'),('ram_dataRead32',32,'output'),('dma_wr',1,'output'),('dma_data',32,'output'),
        ('dma_reqprocessed',1,'output'),('cache_wr',4,'output'),('cache_data',32,'output'),('cache_addr',8,'output'),
        ('ram_dmafifo_adr',23,'input'),('ram_dmafifo_data',32,'input'),('ram_dmafifo_empty',1,'input'),('ram_dmafifo_read',1,'output')]
    header='`timescale 1ns/1ps\nmodule native_main_pin_memory(\n'+',\n'.join(f'    {direction} wire '+(f'[{width-1}:0] ' if width>1 else '')+name for name,width,direction in ports)+'\n);\n'
    prefix=bench_text().split('    reg [3:0] p_valid',1)[0].split('module tb_psx_main_memory;',1)[1]
    prefix=prefix.replace('reg clk=0, clk2x=1, rst=1;','reg clk=0, clk2x=1;wire rst=reset_async;')
    body=(ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv').read_text()
    begin=body.index('    wire av,aw,ar,arv,are,arr;');end=body.index('    integer count=',begin)
    body=body[begin:end].replace('.discard_returns(discard_returns)','.discard_returns(guest_reset)')
    body=body.replace('.ram_dmafifo_adr(fifo_address),.ram_dmafifo_data(fifo_data)',
        '.ram_dmafifo_adr(ram_dmafifo_adr),.ram_dmafifo_data(ram_dmafifo_data)')
    body=body.replace('.ram_dmafifo_empty(fifo_empty),.ram_dmafifo_read(fifo_read)',
        '.ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read)')
    extra='''    wire source_idle,service_idle,adapter_fault;
    integer packets=0,cache_words=0,dma_words=0;
    always @(posedge clk) if(!rst) begin
        if(av && ar) packets++;
        if(adapter_fault || protocol_error || wait_error) $fatal(1,"Native CPU memory fault");
    end
    always @(posedge clk3x) if(cache_wr!=0) cache_words++;
    always @(posedge clk1x) if(dma_wr) dma_words++;
'''
    wrapper=work/'native.sv';wrapper.write_text(header+prefix+extra+body+'endmodule\n')
    original_bench=ROOT/'sim/reference/tb_psx_system_fast.sv';text=original_bench.read_text()
    begin=text.index('mister_sdram_model #(.BOOT_FILE(');end=text.index('mister_sdram_model #(.MEMORY_BYTES(',begin)
    connections={name:name for name,_,_ in ports}
    connections.update(ram_adr='ram_Adr',core_ce='core.component_core.ce',reset_async='native_reset',guest_reset='reset')
    replacement='native_main_pin_memory main_memory('+','.join(f'.{k}({v})' for k,v in connections.items())+');\n'
    text=text[:begin]+replacement+text[end:]
    anchor='initial begin repeat(100) @(negedge clk1x);reset=0;end'
    if text.count(anchor)!=1:raise ValueError('Baseline reset fixture changed')
    text=text.replace(anchor,'''reg native_reset=1;
initial begin #100000000;native_reset=0;end // 100 ns, bench precision is fs
initial begin wait(main_memory.initialized);repeat(8) @(negedge clk1x);reset=0;end''')
    before='$display("PASS FAST SYSTEM cycles=%d checks=%d",cycles,checks);$finish;'
    after='''diagnostic=0;
   fork
    begin
     // The adapter samples the last offered store on this edge. Stop the
     // actual core on the following edge, keeping memory clocks/reset alive.
     @(negedge clk1x);reset=1;
     wait(main_memory.source_idle && main_memory.service_idle && main_memory.fabric_idle);
     repeat(4) @(negedge clk1x);
     for(integer i=0;i<2648;i++) begin
      if({main_memory.chip0.memory[2*(1024+i)+1],main_memory.chip0.memory[2*(1024+i)],
          main_memory.chip1.memory[2*(1024+i)+1],main_memory.chip1.memory[2*(1024+i)]}!==expected[i])
       $fatal(1,"Native committed result bytes mismatch index=%0d",i);
     end
     if({main_memory.chip0.memory[2*16320+1],main_memory.chip0.memory[2*16320],
         main_memory.chip1.memory[2*16320+1],main_memory.chip1.memory[2*16320]}!==32'h600df00d)
      $fatal(1,"Native final sentinel did not physically commit");
     $display("PASS NATIVE MAIN SYSTEM cycles=%d checks=%d packets=%d cache_words=%d dma_words=%d committed_bytes=%d",
      cycles,checks,main_memory.packets,main_memory.cache_words,main_memory.dma_words,2648*4+4);$finish;
    end
   join_none'''
    if text.count(before)!=1:raise ValueError('Baseline completion fixture changed')
    text=text.replace(before,after)
    bench=work/'tb.sv';bench.write_text(text)
    sources=[cloned,ROOT/'sim/reference/mister_memory_models.sv',Path(controller['path']),
        ROOT/'references/MIRLO64/rtl/soc/psram.sv',ROOT/'rtl/memory/psx_mem_arb.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',
        ROOT/'rtl/pocket/psx_psram_packet.sv',ROOT/'rtl/pocket/psx_main_memory.sv',ROOT/'rtl/pocket/psx_mister_main_adapter.sv',
        ROOT/'sim/memory/altddio_registered_model.sv',chip,wrapper,bench]
    for path in [*sources,original_chip,original_bench,ROOT/'sim/memory/tb_psx_mister_main_adapter_body.sv',
        ROOT/'sim/memory/tb_psx_psram_burst.sv',ROOT/'scripts/run_main_memory.py',ROOT/'scripts/prepare_psram_burst.py',
        Path(__file__),work/'boot.hex8',work/'expected.hex']:
        protected[str(path)]=digest(path)
    verilator=find_tool('verilator');print('Native CPU pin-model build: '+str(work),flush=True)
    command=[verilator,'--binary','--timing','-UVERILATOR','-O3','-j','4','-Wno-fatal','--output-split','20000',
        '--top-module','tb_psx_fast','--Mdir',str(work/'obj'),*map(str,sources)]
    with (work/'build.log').open('w') as log:
        result=subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    if result.returncode:raise SystemExit('Native CPU compilation failed: '+str(work/'build.log'))
    binary=work/'obj/Vtb_psx_fast'
    with (work/'run.log').open('w') as log:
        result=subprocess.run([str(binary),'+max_cycles=2000000'],cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=180)
    output=(work/'run.log').read_text();identity=all(digest(Path(p))==sha for p,sha in protected.items())
    passed=identity and result.returncode==0 and output.count('PASS NATIVE MAIN SYSTEM')==1 and bool(re.search(r'checks=\s*2648\b',output))
    report=dict(passed=passed,source_identity_verified=identity,protected_sha256=protected,
        binary_sha256=digest(binary),compiler_command=command,baseline_build=str(build_path),baseline_build_sha256=digest(build_path),
        authored_program={k:str(v) if isinstance(v,Path) else v for k,v in program.items()},
        exit_code=result.returncode,output=output,hardware_verified=False,game_verified=False,
        CPU_execution_verified=passed,CPU_variant='unchanged verified source-build baseline; not the selected resource candidate',
        limitations=['Authored 2648-result RAM diagnostic only; no BIOS/game assets',
            'BIOS direct chip-model preload, not APF loader/ownership integration',
            'SPU RAM and GPU VRAM still use original independent behavioral backing',
            'Baseline scratch-only FASTSIM reset; full reset clear/header services absent',
            'Core release waits for actual chip initialization plus reset-settling clocks; reload ownership not exercised',
            'Hardware DDIO branch selected with -UVERILATOR and the registered simulation DDIO primitive',
            'Fixed-latency pin/chip models; electrical and Quartus timing closure unproved'])
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(output[-2000:],flush=True);print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
