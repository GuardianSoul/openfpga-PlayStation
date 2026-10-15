#!/usr/bin/env python3
"""Whole-card automatic exit reads via pinned APF handler and bridge pins."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def main():
    parent=ROOT/'build/sim/memcard-os-read';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    sources=[ROOT/'rtl/pocket/psx_memcard_os_read.sv',ROOT/'sim/pocket/tb_memcard_os_read.sv',
        ROOT/'rtl/pocket/psx_memcard_fabric.sv',ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_arb.sv',
        ROOT/'references/MIRLO64/target/pocket/core_bridge_cmd.v',
        ROOT/'references/MIRLO64/platform/pocket/common.v',ROOT/'sim/pocket/mf_datatable_model.sv']
    bridge=ROOT/'references/MIRLO64/platform/pocket/io_bridge_peripheral.v'
    protected={str(p):digest(p) for p in [*sources,bridge,Path(__file__)]}
    def build(name,selected):
        directory=work/name;directory.mkdir()
        with (directory/'build.log').open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--top-module','tb_memcard_os_read','--Mdir',str(directory/'obj'),*map(str,selected)],
                cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        return directory/'obj/Vtb_memcard_os_read'
    def run(binary,case):
        return subprocess.run([str(binary),f'+CASE={case}'],cwd=work,capture_output=True,text=True,timeout=180)
    binary=build('original',sources);runs=[];faults=[]
    for case in range(6):
        result=run(binary,case);output=result.stdout+result.stderr;log=work/f'case-{case}.log';log.write_text(output)
        passed=result.returncode==0 and output.count('PASS MEMCARD OS READ')==1 and not re.search(r'%Fatal|%Error|Assertion failed',output)
        runs.append(dict(case=case,passed=passed,exit_code=result.returncode,output=output,binary_sha256=digest(binary),log_sha256=digest(log)))
        print(case,passed,output,flush=True)
        if not passed:break
    spi={}
    if len(runs)==6 and all(r['passed'] for r in runs):
        # Only spell out unused positional synch_3 outputs for Icarus. Preserve
        # clocks, pipelines, pin drivers and the APF license text verbatim.
        cmd=work/'core_bridge_cmd.v';text=sources[5].read_text()
        anchor='synch_3 s01(bridge_endian_little, endian_little_s, clk);'
        if text.count(anchor)!=1:raise ValueError('APF command compatibility anchor changed')
        cmd.write_text(text.replace(anchor,'synch_3 s01(bridge_endian_little, endian_little_s, clk, , );'))
        io=work/'io_bridge_peripheral.v';text=bridge.read_text()
        for before,after in (
            ('synch_3 s00(reset_n, reset_n_s, clk);','synch_3 s00(reset_n, reset_n_s, clk, , );'),
            ('synch_3 s01(endian_little, endian_little_s, clk);','synch_3 s01(endian_little, endian_little_s, clk, , );'),
            ('synch_3 s03(rx_byte_done, rx_byte_done_s, clk, rx_byte_done_r);','synch_3 s03(rx_byte_done, rx_byte_done_s, clk, rx_byte_done_r, );')):
            if text.count(before)!=1:raise ValueError('APF bridge compatibility anchor changed')
            text=text.replace(before,after)
        # Quartus accepts procedural inout regs; Icarus requires resolved nets.
        # Keep input sampling on the real pins and add the equivalent drivers.
        for pin in ('phy_spiclk','phy_spimosi','phy_spimiso'):
            declaration=f'inout   reg             {pin},'
            if text.count(declaration)!=1:raise ValueError('APF pin declaration changed')
            text=text.replace(declaration,f'inout   wire            {pin},')
            text=text.replace(f'{pin} <=',f'{pin}_driver <=')
        marker='    wire reset_n_s;'
        if text.count(marker)!=1:raise ValueError('APF driver insertion anchor changed')
        drivers=''.join(f'    reg {pin}_driver;\n    assign {pin}={pin}_driver;\n' for pin in ('phy_spiclk','phy_spimosi','phy_spimiso'))
        text=text.replace(marker,drivers+marker)
        io.write_text(text);selected=list(sources);selected[5]=cmd;selected.append(io)
        spi_binary=work/'spi.vvp'
        with (work/'spi-build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-DAPF_SPI','-s','tb_memcard_os_read','-o',str(spi_binary),*map(str,selected)],
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        result=subprocess.run([find_tool('vvp'),str(spi_binary),'+CASE=0'],cwd=work,capture_output=True,text=True,timeout=600)
        output=result.stdout+result.stderr;log=work/'spi.log';log.write_text(output)
        spi=dict(passed=result.returncode==0 and output.count('PASS MEMCARD OS READ')==1 and 'FATAL' not in output,
            exit_code=result.returncode,output=output,binary_sha256=digest(spi_binary),log_sha256=digest(log),
            compatibility_sources_sha256={str(p):digest(p) for p in (cmd,io)})
        print('SPI',spi['passed'],output,flush=True)
        for name,before,after,case,marker in (
            ('early-ack','filled>=DEPTH && memory_state==M_IDLE && memory_idle','filled>=4',0,'OS read acknowledged before prefetch'),
            ('service-drain-bypass','pending && services_idle && !freeze_ack','pending && !freeze_ack',1,'Old service lease reused'),
            ('wrong-window',"25'h0080000","25'h0080008",0,'Automatic card bytes/order mismatch'),
            ('short-completion','if(consumed!=WORDS)',"if(1'b0)",3,'Short automatic read accepted')):
            text=sources[0].read_text()
            if text.count(before)!=1:raise ValueError('OS read fault anchor changed')
            variant=work/(name+'.sv');variant.write_text(text.replace(before,after));selected=list(sources);selected[0]=variant
            faulty=build(name,selected);result=run(faulty,case);output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
            rejected=result.returncode!=0 and marker in output
            faults.append(dict(name=name,rejected=rejected,exit_code=result.returncode,output=output,source_sha256=digest(variant),log_sha256=digest(log)))
            print(name,rejected,output,flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and len(runs)==6 and all(r['passed'] for r in runs) and spi.get('passed',False) and len(faults)==4 and all(f['rejected'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,runs=runs,spi=spi,faults=faults,
        card_bytes=131072,buffer_bytes=1024,minimum_APF_word_spacing_host_clocks=88,
        physical_bridge_model_verified=spi.get('passed',False),physical_memory_verified=False,
        actual_OS_firmware_verified=False,full_top_connected=False,hardware_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
