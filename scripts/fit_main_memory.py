#!/usr/bin/env python3
"""Measure the native packet/ownership fabric + paired PSRAM on real pins.

Client ports are virtual and already in the memory clock domain. This is a
component cost, not a connected MiSTer/APF/native-topology measurement.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import tempfile

from build_pocket import source
from fit_psram_burst import prepare as prepare_pins
from prepare_resource_targets import apply_compile_profile,digest,write_project
from record_pocket_measurements import verify_sources
from run_resource_fit import fit_target
from tool_paths import ROOT,find_tool


def validate_contract(path):
    path=Path(path).resolve();report=json.loads(path.read_text())
    expected=['nominal','delayed','fast-chip','backend-error','early-response','odd-mask','die-alias','early-owner','early-startup','lost-backend-error']
    if not report.get('passed') or not report.get('source_identity_verified') or [r['name'] for r in report['runs']]!=expected:
        raise ValueError('Complete native-main pin contract required')
    if not all(r['passed'] for r in report['runs']):raise ValueError('Main pin contract failed')
    if any(digest(Path(p))!=sha for p,sha in report['protected_sha256'].items()):
        raise ValueError('Main pin contract input changed')
    for file in ['rtl/pocket/psx_main_memory.sv','rtl/pocket/psx_psram_packet.sv','rtl/memory/psx_mem_arb.sv',
                 'scripts/run_main_memory.py','scripts/prepare_psram_burst.py']:
        if report['protected_sha256'].get(str(ROOT/file))!=digest(ROOT/file):
            raise ValueError('Main contract missing selected source')
    return path,report


def prepare(directory,contract_path):
    contract_path,contract=validate_contract(contract_path)
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    base=prepare_pins(directory/'pin-base');old=base['targets']['psram_burst']
    top='psx_main_memory_resource'
    specs=[('clk_74a','in',1),('reset_async','in',1),('boot_request','in',1),('guest_quiesced','in',1),
        ('boot_owned','out',1),('fabric_idle','out',1),('protocol_error','out',1),
        ('p_valid','in',4),('p_write','in',4),('p_ready','out',4),('p_addr','in',100),
        ('p_halfwords','in',16),('p_wdata','in',512),('p_byte_en','in',64),
        ('p_rsp_valid','out',4),('p_rsp_ready','in',4),('p_rsp_rdata','out',512),('p_rsp_error','out',4),
        ('initialized','out',1),('wait_error','out',1)]
    pads=[]
    for chip in range(2):
        pads += [(f'cram{chip}_a','out',6),(f'cram{chip}_dq','inout',16),(f'cram{chip}_wait','in',1)]
        pads += [(f'cram{chip}_{name}','out',1) for name in
                 ['clk','adv_n','cre','ce0_n','ce1_n','oe_n','we_n','ub_n','lb_n']]
    declarations=[]
    for name,direction,width in specs+pads:
        bounds='[21:16] ' if name.endswith('_a') else f'[{width-1}:0] ' if width>1 else ''
        declarations.append(f'    {dict(in_="input",out="output",inout="inout")["in_" if direction=="in" else direction]} wire {bounds}{name}')
    previous=Path(old['directory'])/(old['top']+'.sv')
    body=previous.read_text().split('    wire clk, clk2x,',1)[1].rsplit('endmodule',1)[0]
    body='    wire clk, clk2x,'+body
    extra='''    wire valid,write,die,last,ready,rvalid,idle;
    wire [21:0] addr;wire [31:0] wdata,rdata;wire [3:0] be;
    psx_main_memory native (
        .clk(clk),.reset(reset_local[2]),.initialized(initialized),
        .boot_request(boot_request),.guest_quiesced(guest_quiesced),.boot_owned(boot_owned),
        .idle(fabric_idle),.protocol_error(protocol_error),.p_valid(p_valid),.p_write(p_write),
        .p_ready(p_ready),.p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),
        .p_byte_en(p_byte_en),.p_rsp_valid(p_rsp_valid),.p_rsp_ready(p_rsp_ready),
        .p_rsp_rdata(p_rsp_rdata),.p_rsp_error(p_rsp_error),
        .z_valid(valid),.z_we(write),.z_die(die),.z_last(last),.z_ready(ready),.z_rvalid(rvalid),
        .z_idle(idle),.wait_error(wait_error),.z_addr(addr),.z_wdata(wdata),.z_wmask(be),.z_rdata(rdata));
'''
    anchor='    psx_psram_burst controller ('
    if body.count(anchor)!=1:raise ValueError('PSRAM physical wrapper changed')
    body=body.replace(anchor,extra+anchor)
    wrapper=directory/(top+'.sv');wrapper.write_text('module '+top+'(\n'+',\n'.join(declarations)+'\n);\n'+body+'endmodule\n')
    sources=[source(wrapper),*old['quartus_sources'][1:]]
    for name in ['psx_main_memory.sv','psx_psram_packet.sv']:
        copy=directory/name;copy.write_bytes((ROOT/'rtl/pocket'/name).read_bytes());sources.append(source(copy))
    sources.append(source(ROOT/'rtl/memory/psx_mem_arb.sv'))
    if sources[1]['sha256']!=contract['controller']['sha256']:
        raise ValueError('Prepared PSRAM differs from the pin-tested controller')
    external=[(name,direction,'std_logic' if width==1 else
        'std_logic_vector(21 downto 16)' if name.endswith('_a') else f'std_logic_vector({width-1} downto 0)')
        for name,direction,width in specs+pads]
    sdc=(Path(old['directory'])/(old['top']+'.sdc')).read_text().splitlines()
    write_project(directory,top,external,('clk_74a',),sources,sdc)
    qsf=directory/(top+'.qsf');lines=qsf.read_text().splitlines()
    lines=[line for line in lines if not ('VIRTUAL_PIN' in line and '-to "cram' in line)]
    base_lines=(Path(old['directory'])/(old['top']+'.qsf')).read_text().splitlines()
    lines += [line for line in base_lines if (' -to cram' in line or ' -to clk_74a' in line)]
    qsf.write_text('\n'.join(lines)+'\n')
    target=dict(top=top,directory=str(directory),quartus_sources=sources,fixed_ports={},
        physical_clock_ports=['clk_74a'],components_connected=True,
        project_sha256={suffix:digest(directory/(top+suffix)) for suffix in ['.qsf','.qpf','.sdc']},
        included_components=['normalized packet endpoint','four-client arbiter','BIOS ownership fence',
            'tested both-die burst PSRAM/controller/retimed PHY','one shared memory PLL'],
        limitations=['Virtual single-memory-clock client/boot ports; CPU/SPU/CD/APF source adapters/CDC absent',
            'Component allocation is not LAB saving or a connected full-top fit',
            'Board delays provisional, CE gating/reset/PLL unlock and virtual I/O not closed'])
    manifest=apply_compile_profile(dict(device='5CEBA4F23C8',targets={'main_memory':target}),'area-min-state')
    manifest.update(contract_report=str(contract_path),contract_sha256=digest(contract_path),
        preparer_sha256=digest(Path(__file__)),controller=base['controller'],clock_mhz=62.5,
        memory_pll_shared=True,CPU_integration_verified=False,hardware_verified=False)
    verify_sources(directory,sources)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'build/resources/main-memory')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();parent=args.output.resolve()/'runs';parent.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    manifest=prepare(directory,args.contract);print(directory,flush=True)
    if args.prepare_only:return
    binary=Path(find_tool('quartus_sh')).parent
    result=fit_target(binary,manifest['targets']['main_memory'],manifest['device'],1800)
    report=dict(tool_version=subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True),
        **manifest,result=result)
    (directory/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ['fit_verified','counts','reported_timing']}),flush=True)
    if not result['fit_verified']:raise SystemExit(1)


if __name__=='__main__':main()
