#!/usr/bin/env python3
"""Run the measured core candidate with finite, shared Pocket memory models.

Reuse the existing checked host/clock/checkpoint protocol in this Python process
only. Its files and the live reference executable remain untouched. A distinct
build identity prohibits restoring reference checkpoints into this candidate.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

import run_system_checkpoint as protocol
from ghdl_verilog import normalize_generated_verilog
from prepare_pocket_memory_models import prepare as prepare_memory
from prepare_pocket_system_sim import prepare, CONFIGURATION
from prepare_resource_targets import digest
from system_checkpoint_bench import write_bench as original_bench
from tool_paths import ROOT, find_tool


def write_bench(directory):
    path = original_bench(directory)
    text = path.read_text()
    text = text.replace('mister_sdram_model #(',
        'pocket_psram_model #(.HALFWORDS_PER_TICK(2),.SHARED_BUS(0),')
    marker = 'mister_ddr_model video_memory'
    if text.count(marker) != 1: raise ValueError('VRAM model binding changed')
    text = text.replace(marker, 'pocket_vram_model video_memory')
    marker = 'endmodule\n'
    if text.count(marker) != 1: raise ValueError('Checkpoint fixture tail changed')
    text = text.replace(marker, '''
// Observe the same rising-edge GP1 acceptance used by MiSTer's GPU. These
// stdout records introduce no live file handles into serialized state.
reg observed_executable_entry=0;
always @(posedge clk1x) begin
 if (!core.component_core.igpu.reset && core.component_core.igpu.ce &&
     core.component_core.igpu.bus_write &&
     core.component_core.igpu.bus_addr==4)
  $display("EVENT GP1 cycles=%0d data=%08x",cycles,core.component_core.igpu.bus_datawrite);
 if (!reset_busy && cpu_pc==32'h800110c0 && !observed_executable_entry) begin
  observed_executable_entry<=1;
  $display("EVENT EXECUTABLE_PC_OBSERVED cycles=%0d pc=%08x",cycles,cpu_pc);
 end
end
endmodule
''')
    path.write_text(text)
    return path


def build(directory, fast_reset, fast_boot, reuse, no_disc_lid_closed=False):
    manifest_path = directory/'build-manifest.json'
    executable = directory/'obj/Vpsx_checkpoint'
    if reuse:
        result = json.loads(manifest_path.read_text())
        if (result.get('configuration') != CONFIGURATION or result['fast_reset'] != fast_reset
                or result['fast_boot'] != fast_boot):
            raise ValueError('Candidate configuration/reset/boot selection differs')
        if result.get('no_disc_lid_closed',False)!=no_disc_lid_closed:
            raise ValueError('Candidate no-disc lid selection differs')
        if result['preparation'].get('reset_header_constant_alias')!=1:
            raise ValueError('Legacy candidate reset-header alias; preserve it and use a fresh build and boot')
        if not all(digest(Path(p)) == sha for p,sha in result['protected_sha256'].items()):
            raise ValueError('Candidate build changed; preserve it and rebuild separately')
        return result, executable
    ghdl,verilator = find_tool('ghdl'),find_tool('verilator')
    if not ghdl or not verilator: raise ValueError('GHDL and Verilator required')
    directory.mkdir(parents=True,exist_ok=True)
    prepared = prepare(directory/'sources',fast_reset=fast_reset,fast_boot=fast_boot)
    memory = prepare_memory(directory/'memory-models')
    vram = directory/'memory-models/pocket_vram_model.sv'
    shutil.copy2(ROOT/'sim/reference/pocket_vram_model.sv',vram)
    bench = write_bench(directory)
    if no_disc_lid_closed:
        text=bench.read_text();marker='.LIDopen(LIDopen)'
        if text.count(marker)!=1:
            raise ValueError('Candidate CD lid connection changed')
        bench.write_text(text.replace(marker,".LIDopen(1'b0)"))
    verilog = directory/'system.v'
    driver = ROOT/'sim/reference/psx_checkpoint_main.cpp'
    host = ROOT/'sim/reference/psx_checkpoint_host.h'
    sv = [Path(s['path']) for s in prepared['sv_sources']
        if s.get('assignment') == 'SYSTEMVERILOG_FILE']
    with (directory/'build.log').open('w') as log:
        for source in prepared['sources']:
            subprocess.run([ghdl,'-a','--std=08','-frelaxed','--work='+source['library'],source['path']],
                cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        with verilog.open('w') as out:
            subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip',
                '--vendor-library=gteip','--out=verilog','psx_mister_psx_top_resource'],
                cwd=directory,stdout=out,stderr=log,check=True,timeout=180)
        prepared['verilog_normalization'] = normalize_generated_verilog(verilog)
        if len(re.findall(r'\bpsx_laxer_mister_adapter engine \(',verilog.read_text())) != 1:
            raise ValueError('Selected mixed-language GTE instance is missing or duplicated')
        subprocess.run([verilator,'--cc','--exe','--build','--savable','--no-timing','-O3','-j','4',
            '-Wno-fatal','--output-split','20000','--top-module','psx_checkpoint',
            '--Mdir',str(directory/'obj'),'-I'+str(directory/'sources/hardware/gte-sv'),
            str(verilog),memory['path'],str(vram),str(bench),*map(str,sv),str(driver)],
            cwd=directory,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    inputs = [driver,host,ROOT/'sim/reference/tb_psx_system_fast.sv',
        ROOT/'scripts/system_checkpoint_bench.py',Path(__file__).resolve(),
        ROOT/'scripts/prepare_pocket_system_sim.py',ROOT/'scripts/prepare_pocket_memory_models.py',
        ROOT/'scripts/prepare_system_verilator.py',ROOT/'scripts/ghdl_verilog.py',
        ROOT/'scripts/run_system_checkpoint.py',
        bench,verilog,Path(memory['path']),vram,executable]
    protected = {str(p):digest(p) for p in inputs}
    for source in prepared['sources']+prepared['sv_sources']:
        for key in ('path','original','selected'):
            if key in source: protected[source[key]] = digest(Path(source[key]))
    result = {'configuration':CONFIGURATION,'preparation':prepared,'memory_profile':memory,
        'PSRAM_layout':'One shared paired x32 bus; separate logical main/SPU allocations',
        'VRAM_profile':'125 MHz x16 SDRAM service model',
        'controller_fixture':'Released digital pad 1; both guest memory-card RTL paths present',
        'fast_reset':fast_reset,'fast_boot':fast_boot,'no_disc_lid_closed':no_disc_lid_closed,
        'protected_sha256':protected,
        'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True),
        'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'hardware_verified':False,'game_verified':False}
    manifest_path.write_text(json.dumps(result,indent=2)+'\n')
    return result,executable


def main():
    # This reuses CLI validation, asset privacy, host transport and checkpoint
    # gates without modifying their module on disk or any other process.
    protocol.build = build
    protocol.main()


if __name__ == '__main__':main()
