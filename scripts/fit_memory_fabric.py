#!/usr/bin/env python3
"""Fit four CDC clients, Pocket SDRAM fabric and an independent 32-bit bridge.

The normalized client/bridge buses use virtual pins; SDRAM pads and reference
clocks use actual Pocket pins. This is a component fit, not an APF bitstream.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from build_pocket import source
from prepare_platform_target import PLATFORM
from prepare_resource_targets import apply_compile_profile, digest, write_project
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


def prepare_fabric(directory, read_pipeline=False):
    top = 'psx_memory_fabric_resource'
    specs = [('clk_74a','in',1),('clk_74b','in',1),('reset_async','in',1),
        ('initialized','out',1),('protocol_error','out',1),
        ('dram_a','out',13),('dram_ba','out',2),('dram_dqm','out',2),
        ('dram_ras_n','out',1),('dram_cas_n','out',1),('dram_we_n','out',1),
        ('dram_cke','out',1),('dram_dq','inout',16),('dram_clk','out',1)]
    req = [('valid','in',1),('write','in',1),('ready','out',1),('addr','in',25),
        ('halfwords','in',4),('wdata','in',128),('byte_en','in',16),
        ('rsp_valid','out',1),('rsp_ready','in',1),('rsp_rdata','out',128),('rsp_error','out',1)]
    specs += [('p_'+n,d,w*4) for n,d,w in req]
    specs += [('b_'+n,d,w) for n,d,w in req]
    specs += [('mem_valid','out',1),('mem_ready','in',1),('mem_write','out',1),
        ('mem_addr','out',24),('mem_wdata','out',32),('mem_byte_en','out',4),
        ('mem_rvalid','in',1),('mem_rdata','in',32)]
    declarations = [f'    {dict(in_="input",out="output",inout="inout")["in_" if d=="in" else d]} wire '+
        (f'[{w-1}:0] ' if w>1 else '')+n for n,d,w in specs]
    body = ['    wire client_clk, client_reset;']
    for n,_,w in req:
        body.append(f'    wire [{4*w-1}:0] d_{n};')
    for i in range(4):
        body += [f'    psx_mem_cdc cdc{i} (.src_clk(clk_74b), .dst_clk(client_clk),',
            '        .reset_async(reset_async || client_reset), .src_reset(), .dst_reset(),']
        connections=[]
        for n,_,w in req:
            connections += [f'.s_{n}(p_{n}[{i*w} +: {w}])',f'.d_{n}(d_{n}[{i*w} +: {w}])']
        body += ['        '+',\n        '.join(connections)+');']
    body += [
        '    psx_mem_pocket #(.PORTS(4)) memory (',
        '        .clk_74a(clk_74a), .reset_async(reset_async), .client_clk(client_clk),',
        '        .client_reset(client_reset), .initialized(initialized), .protocol_error(protocol_error),']
    connections=[f'.p_{n}(d_{n})' for n,_,_ in req]
    connections += [f'.{n}({n})' for n,_,_ in specs if n.startswith('dram_')]
    body += ['        '+',\n        '.join(connections)+');',
        '    // Alternative memory endpoint measured separately, not in series with SDRAM.',
        '    psx_mem_bridge bridge (.clk(client_clk), .rst(client_reset),']
    connections=[f'.{"req_"+n if n in ("valid","write","ready","addr","halfwords","wdata","byte_en") else n}(b_{n})' for n,_,_ in req]
    connections += [f'.{n}({n})' for n,_,_ in specs if n.startswith('mem_')]
    body += ['        '+',\n        '.join(connections)+');']
    wrapper=directory/(top+'.sv')
    wrapper.write_text('module '+top+'(\n'+',\n'.join(declarations)+'\n);\n'+'\n'.join(body)+'\nendmodule\n')
    sources=[source(ROOT/'rtl/memory'/n) for n in ('psx_mem_clocks.sv','psx_mem_pocket.sv',
        'psx_mem_arb.sv','psx_mem_bridge.sv','psx_sdram.sv','psx_sdram_pins.sv','psx_sdr_phy.sv','psx_mem_cdc.sv')]
    sources += [source(ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv'),source(wrapper)]
    retiming=None
    if read_pipeline:
        from prepare_pocket_read_pipeline import prepare
        retiming=prepare(directory/'read-pipeline')
        sources=[retiming if s['path']==retiming['original'] else s for s in sources]
    external=[(n,d,'std_logic' if w==1 else f'std_logic_vector({w-1} downto 0)') for n,d,w in specs]
    sdc=['create_clock -name ref_a -period 13.468 [get_ports clk_74a]',
        'create_clock -name ref_b -period 13.468 [get_ports clk_74b]',
        'derive_pll_clocks','derive_clock_uncertainty',
        'set_false_path -to [get_registers {*|request_sync[0] *|response_sync[0]}]',
        'set_max_delay 10 -from [get_registers {*|request_payload[*]}] -to [get_registers {*|d_addr[*] *|d_write_q *|d_halfwords[*] *|d_wdata[*] *|d_byte_en[*]}]',
        'set_max_delay 10 -from [get_registers {*|response_payload[*]}] -to [get_registers {*|s_rsp_rdata[*] *|s_rsp_error}]',
        '# Component area measurement: virtual client and external-memory I/O timing incomplete.']
    clocks=('clk_74a','clk_74b')
    write_project(directory,top,external,clocks,sources,sdc)
    qsf=directory/(top+'.qsf')
    lines=[line for line in qsf.read_text().splitlines() if not ('VIRTUAL_PIN' in line and '-to "dram_' in line)]
    for line in (PLATFORM/'pocket.tcl').read_text().splitlines():
        if line.startswith('set_') and re.search(r'-to (?:"?)(?:dram_|clk_74)',line): lines.append(line)
    qsf.write_text('\n'.join(lines)+'\n')
    target=dict(top=top,directory=str(directory),quartus_sources=sources,
        project_sha256={suffix:digest(directory/(top+suffix)) for suffix in ('.qsf','.qpf','.sdc')},
        fixed_ports={},physical_clock_ports=clocks,
        included_components=['4 CDC channels','psx_mem_pocket','4-port arbiter',
            'psx_sdram / sdr_ctrl','psx_sdram_pins / psx_sdr_phy','memory PLL','independent psx_mem_bridge'],
        components_connected=True,
        limitations=['Virtual client interfaces; no APF shell or PSX core',
            'Bridge measured as an alternative endpoint, not serialized with SDRAM',
            '125 MHz SDRAM / 62.5 MHz client; board and virtual-I/O timing incomplete'])
    manifest=apply_compile_profile(dict(device='5CEBA4F23C8',targets={'memory_fabric':target}),'area-min-state')
    manifest['read_pipeline_retiming']=retiming
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/resources/memory-fabric')
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--read-pipeline',action='store_true',help='Experimental full-cycle IO-to-fabric read transfer; client latency retained')
    args=parser.parse_args()
    runs=args.output.resolve()/'runs'; runs.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=runs))
    manifest=prepare_fabric(directory,args.read_pipeline); print(directory,flush=True)
    if args.prepare_only:return
    binary=Path(find_tool('quartus_sh')).parent
    result=fit_target(binary,manifest['targets']['memory_fabric'],manifest['device'],1800)
    report=dict(tool_version=subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True),
        **manifest,result=result)
    (directory/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('fit_verified','counts','tool_error_messages','reported_timing')}),flush=True)
    if not result['fit_verified']:raise SystemExit(1)


if __name__=='__main__':main()
