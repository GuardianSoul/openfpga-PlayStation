#!/usr/bin/env python3
"""Execute a bounded GP0 pixel oracle against the converted pinned MiSTer GPU."""
import json
from pathlib import Path
import re
import subprocess

from prepare_resource_targets import digest
from prepare_system_verilator import prepare
from tool_paths import ROOT,find_tool


def main():
    ghdl=find_tool('ghdl');verilator=find_tool('verilator') or str(ROOT/'build/tools/oss-cad-suite/bin/verilator')
    if not ghdl or not Path(verilator).is_file():raise SystemExit('GHDL synthesis and Verilator are required')
    directory=ROOT/'build/sim/gpu-primitives';directory.mkdir(parents=True,exist_ok=True)
    manifest=prepare(directory/'sources')
    verilog=directory/'system.v';models=ROOT/'sim/reference/mister_memory_models.sv'
    template=ROOT/'sim/reference/tb_gpu_primitives.sv';bench=directory/'tb_gpu_primitives.sv'
    with (directory/'checks.log').open('w') as log:
        for source in manifest['sources']:
            subprocess.run([ghdl,'-a','--std=08','-frelaxed',f'--work={source["library"]}',source['path']],
                cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=60,check=True)
        with verilog.open('w') as output:
            subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog',
                'psx_mister_psx_top_resource'],cwd=directory,stdout=output,stderr=log,timeout=120,check=True)
        from ghdl_verilog import normalize_generated_verilog
        manifest['verilog_normalization']=normalize_generated_verilog(verilog)
        # GHDL version/backend can decorate the entity with its architecture.
        matches=re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n',verilog.read_text(),re.M)
        if len(matches)!=1:raise SystemExit('Expected exactly one GPU entity in the converted source')
        header=verilog.read_text()[re.search(r'^module '+re.escape(matches[0])+r'\s*\n',verilog.read_text(),re.M).start():].split(');',1)[0]
        ports={name.lower():name for name in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)',header)}
        text=template.read_text()
        instance=re.search(r'GPU_ENTITY gpu\(.*?\);',text,re.S).group()
        bound=re.sub(r'\.(\w+)\(',lambda match:'.'+ports[match[1].lower()]+'(',instance)
        bench.write_text(text.replace(instance,bound).replace('GPU_ENTITY',matches[0]))
        subprocess.run([verilator,'--binary','--timing','-O3','-j','4','-Wno-fatal','--top-module','tb_gpu_probe',
            '--Mdir',str(directory/'obj'),str(verilog),str(models),str(bench)],cwd=directory,
            stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True)
    result=subprocess.run([str(directory/'obj/Vtb_gpu_probe')],cwd=directory,capture_output=True,text=True,timeout=60)
    output=result.stdout+result.stderr
    with (directory/'checks.log').open('a') as log:log.write(output)
    print(output,end='',flush=True)
    identity=all(digest(Path(s['path']))==s['sha256'] and digest(Path(s['original']))==s['original_sha256']
        for s in manifest['sources'])
    passed=result.returncode==0 and identity and bool(re.search(r'PASS GPU primitive checks=\s*360\b',output))
    (directory/'report.json').write_text(json.dumps({'preparation':manifest,'source_identity_verified':identity,
        'ghdl_version':subprocess.check_output([ghdl,'--version'],text=True),
        'verilator_version':subprocess.check_output([verilator,'--version'],text=True).strip(),
        'verilog_sha256':digest(verilog),'models_sha256':digest(models),
        'template_sha256':digest(template),'bench_sha256':digest(bench),
        'stdout':output,'exit_code':result.returncode,'passed':passed,'pixel_checks':360,
        'coverage':['Quick-fill alignment and neighboring pixels','CPU upload and non-overlapping VRAM copy',
            'Untextured rectangle and one flat right triangle','Drawing-area clipping and signed drawing offset',
            'Four semi-transparency modes','Destination mask protection and forced mask writes',
            'Raw 16-bit texture including transparent zero and opaque black','4-bit/8-bit CLUT textures and cache clear'],
        'original_vhdl_gpu_waveform_equivalence_verified':False,'display_scanout_verified':False,
        'gpu_latency_verified':False,'bios_boot_verified':False,'game_verified':False,'hardware_verified':False},indent=2)+'\n')
    if not passed:raise SystemExit(f'GPU primitive probe failed; see {directory}/checks.log')


if __name__=='__main__':main()
