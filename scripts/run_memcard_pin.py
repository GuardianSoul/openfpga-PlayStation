#!/usr/bin/env python3
"""Run the authored card/APF/fabric contract through real paired PSRAM pins."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_psram_burst import prepare,UPSTREAM
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def chip_text():
    text=(ROOT/'sim/memory/cellularram_burst_model.sv').read_text()
    before='else if(word>=23\'h7ff000) slot=12288+int\'(word-23\'h7ff000);'
    after=before+'''\n        else if(word>=23'h040000 && word<23'h048000) slot=16384+int'(word-23'h040000);
        else if(word>=23'h080000 && word<23'h088000) slot=49152+int'(word-23'h080000);'''
    if text.count(before)!=1:raise ValueError('Sparse chip window anchor changed')
    text=text.replace(before,after).replace('memory[0:32767]','memory[0:163839]')
    return text


def bench_text():
    text=(ROOT/'sim/pocket/tb_memcard_transfer.sv').read_text()
    text=text.replace('always #8.081 memory_clk=~memory_clk;','always #8 memory_clk=~memory_clk;')
    text=text.replace('wire backend_idle=!mem_busy && !rsp_valid && pin_delay==0;', 'wire backend_idle=packet_idle && initialized;')
    text=text.replace('reg rsp_valid=0,rsp_error=0;reg [127:0] rsp_data=0;',
        'wire rsp_valid,rsp_error;wire [127:0] rsp_data;')
    text=text.replace(' assign m_ready=backend_idle && ticks%11>2 && !force_stall;', '')
    start=text.index(' always @(posedge memory_clk) begin');end=text.index(' integer test_case=0;',start)
    text=text[:start]+''' always @(posedge memory_clk) begin
  if(m_valid && m_ready) begin
   packets++;
   if(m_addr==25'h1234) begin mem_index=-1;guest_word=m_data[31:0];end
   else if(m_addr>=25'h80000 && m_addr<25'h90000) begin mem_card=0;mem_index=(m_addr-25'h80000)/2;end
   else if(m_addr>=25'h100000 && m_addr<25'h110000) begin mem_card=1;mem_index=(m_addr-25'h100000)/2;end
   else $fatal(1,"Card packet escaped logical window");
   mem_write=m_write;
   if(m_write && mem_index>=0) begin memory[mem_card][mem_index]=m_data[31:0];words_written++;end
  end
  if(packet_fault || wait_error) $fatal(1,"Card pin transport fault");
 end
''' +text[end:]
    # The guest's authored sector modifications must enter the actual memory
    # path; drive them as host-independent native packet writes in this probe.
    # Reuse the complete APF fixture without authored guest edits: file bytes
    # alone supply the independent loaded/persisted oracle. Dirty events still
    # select both cards and test retention on command errors.
    text=text.replace('  for(integer n=320;n<352;n++) memory[card][n]=file_word(card,n)^32\'hbade1943;', '')
    text=text.replace('repeat(10) @(negedge clk);reset=0;',
        'repeat(10) @(negedge clk);reset=0;wait(initialized);repeat(12) @(negedge clk);')
    # Memory_ready is a test control in the packet profiles. Here all transfers
    # start only after the real controller startup; no negative profile runs.
    prefix=(ROOT/'sim/memory/tb_psx_psram_burst.sv').read_text().split('    function automatic integer slot',1)[0]
    prefix=prefix.split('module tb_psx_psram_burst;\n',1)[1]
    prefix=prefix.replace('    reg clk=0, clk2x=1, rst=1;\n    always #8 clk=~clk;\n', '    reg clk2x=1;\n')
    replacements={'clk':'memory_clk','rst':'reset','dut':'pin_controller','valid':'z_valid','write':'z_write',
        'last':'z_last','die':'z_die','address':'z_addr','data':'z_data','mask':'z_mask','ready':'z_ready',
        'rvalid':'z_rvalid','idle':'z_idle','read_data':'z_read'}
    for before,after in replacements.items():prefix=re.sub(r'\b'+before+r'\b',after,prefix)
    prefix=prefix.replace('.memory_clk(','.clk(').replace('.reset(','.rst(').replace('.z_ready(initialized)','.ready(initialized)')
    for before,after in [
        ('reg z_valid=0,z_write=0,z_last=0,z_die=0;','wire z_valid,z_write,z_last,z_die;'),
        ('reg [21:0] z_addr=0;','wire [21:0] z_addr;'),
        ('reg [31:0] z_data=0;','wire [31:0] z_data;'),
        ('reg [3:0] z_mask=0;','wire [3:0] z_mask;')]:
        if prefix.count(before)!=1:raise ValueError('Pin prefix changed: '+before)
        prefix=prefix.replace(before,after)
    # Hold only the last response; the actual write and pin drain still occur.
    # The independent chip model retains bytes and checks all original timing.
    packet='''
 wire packet_idle,packet_fault,native_rsp,native_rsp_ready;
 wire hold_response=hold_final && mem_write && mem_index==32767;
 assign rsp_valid=native_rsp && !hold_response;
 assign native_rsp_ready=m_rsp_ready && !hold_response;
 psx_psram_packet packet(.clk(memory_clk),.reset(reset),.initialized(initialized),
  .req_valid(m_valid),.req_ready(m_ready),.req_write(m_write),.req_addr(m_addr),
  .req_halfwords(m_halfwords),.req_wdata(m_data),.req_byte_en(m_be),
  .rsp_valid(native_rsp),.rsp_ready(native_rsp_ready),.rsp_rdata(rsp_data),.rsp_error(rsp_error),
  .idle(packet_idle),.fault(packet_fault),.z_valid(z_valid),.z_ready(z_ready),.z_we(z_write),
  .z_die(z_die),.z_last(z_last),.z_addr(z_addr),.z_wdata(z_data),.z_wmask(z_mask),
  .z_rvalid(z_rvalid),.z_rdata(z_read),.z_idle(z_idle),.wait_error(wait_error));
'''
    text=text.replace('$fatal(1,"APF save opcode/freeze")', '$fatal(1,"APF save opcode/freeze result=%h freeze=%b ack=%b engine=%d",result,freeze,freeze_ack,dut.state)')
    text=text.replace('module tb_memcard_transfer;','module tb_memcard_transfer;\n'+prefix+packet)
    text=text.replace(' integer test_case=0;', ' initial begin commands.hstate=0;commands.tstate=0;commands.host_cmd_start=0;end\n integer test_case=0;')
    return text


def main():
    parent=ROOT/'build/sim/memcard-pin';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    selected=prepare(work/'rtl')
    chip=work/'chip.sv';chip.write_text(chip_text())
    bench=work/'tb.sv';bench.write_text(bench_text())
    fifo_source=ROOT/'sim/pocket/tb_bios_probe.sv'
    fifo=work/'fifo.sv';fifo.write_text(fifo_source.read_text().split('module tb_bios_probe;',1)[0])
    command_source=ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v'
    commands=work/'core_bridge_cmd.v'
    cmd_text=command_source.read_text();anchor='synch_3 s01(bridge_endian_little, endian_little_s, clk);'
    if cmd_text.count(anchor)!=1:raise ValueError('APF positional sync anchor changed')
    commands.write_text(cmd_text.replace(anchor,'synch_3 s01(bridge_endian_little, endian_little_s, clk, , );'))
    sources=[Path(selected['path']),ROOT/'references/MIRLO64/rtl/soc/psram.sv',
        ROOT/'rtl/pocket/psx_psram_packet.sv',ROOT/'rtl/pocket/psx_memcard_transfer.sv',
        ROOT/'rtl/pocket/psx_memcard_fabric.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_arb.sv',
        commands,ROOT/'references/MIRLO64/platform/pocket/common.v',
        ROOT/'sim/memory/altddio_registered_model.sv',chip,bench,fifo]
    protected={str(p):digest(p) for p in [*sources,fifo_source,UPSTREAM,command_source,
        ROOT/'sim/memory/cellularram_burst_model.sv',ROOT/'sim/memory/tb_psx_psram_burst.sv',
        ROOT/'sim/pocket/tb_memcard_transfer.sv',ROOT/'scripts/prepare_psram_burst.py',Path(__file__)]}
    binary=work/'sim.vvp'
    with (work/'build.log').open('w') as log:
        subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_memcard_transfer','-o',str(binary),*map(str,sources)],
            cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    run=subprocess.run([find_tool('vvp'),str(binary),'+CASE=0'],cwd=work,capture_output=True,text=True,timeout=600)
    output=run.stdout+run.stderr;(work/'run.log').write_text(output);print(output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    passed=identity and run.returncode==0 and output.count('PASS MEMCARD TRANSFER')==1 and 'FATAL' not in output
    report=dict(passed=passed,source_identity_verified=identity,source_sha256=protected,exit_code=run.returncode,
        output=output,controller=selected,full_top_connected=False,guest_card_protocol_verified=False,
        automatic_OS_readback_verified=False,hardware_verified=False,
        scope='Two authored images through APF + asynchronous fabric + real paired controller/PHY/pin model; no guest sector modification in this pin fixture')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)

if __name__=='__main__':main()
