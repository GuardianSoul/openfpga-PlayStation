#!/usr/bin/env python3
"""Test dy-RAM against the QSF-selected GPU of a frozen Pocket project.

Reuses the independent GPU oracle, cycle-output/readback/VRAM comparisons and
faults from run_gpu_dy_ram. Captures the current GPU, including read-late and
all three ordered video channels. Vendor RAM ports use the existing portable
models; no production source, project, clock or timing constraint is changed.
"""
import argparse
import ast
import concurrent.futures
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

import run_gpu_dy_ram as oracle
from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest, hardware_text, UPSTREAM
from tool_paths import ROOT, find_tool


RAM_STUB = '''library ieee;use ieee.std_logic_1164.all;
entity RamM10Kn is generic(width:natural;width_byteena:natural:=1;widthad:natural);
port(inclock,wren:in std_logic;data:in std_logic_vector(width-1 downto 0);
wraddress,rdaddress:in std_logic_vector(widthad-1 downto 0);q:out std_logic_vector(width-1 downto 0));end;
architecture opaque of RamM10Kn is begin end;
'''


def video_components():
    # Use exactly the already exercised SV component ABI of the selected-video
    # census. Literal-only extraction avoids executing that runner's CLI.
    tree = ast.parse((ROOT/'scripts/run_video_dotclock_census.py').read_text())
    found = [call.args[0].value for call in ast.walk(tree) if isinstance(call, ast.Call)
             and isinstance(call.func, ast.Attribute) and call.func.attr == 'write_text'
             and isinstance(call.func.value, ast.Name) and call.func.value.id == 'stub'
             and call.args and isinstance(call.args[0], ast.Constant)]
    if len(found) != 1: raise ValueError('Video census component definitions changed')
    return found[0]


def adapt_video(text):
    if text.count('component psx_video_reports_cdc is') != 1:
        raise ValueError('Current GPU test requires the ordered report/timer composition')
    marker = 'architecture arch of gpu_videoout_async is'
    if text.count(marker) != 1: raise ValueError('Video architecture boundary changed')
    text = re.sub(r'^\s*attribute[^\n]*\n', '', hardware_text(text), flags=re.M)
    # Same simulation-only low-nine-bit blank/reset conversions as the census;
    # avoid GHDL bounds errors for negative values outside the displayed area.
    for value in ('lineMax - 1', 'lineMax - 2', '((vdispNew - vDisplayStart) * 2) + 1',
                  '(vdispNew - vDisplayStart) * 2', 'vdispNew - vDisplayStart'):
        text = text.replace('to_unsigned('+value+', 9)', 'resize(unsigned(to_signed('+value+', 12)), 9)')
    binding='\n'+''.join('   for '+name+':'+module+' use entity videoip.'+module+';\n' for name,module in
                        [('video_config','psx_video_config_cdc'),('video_request','psx_video_request_cdc'),
                         ('video_reports','psx_video_reports_cdc')])
    return 'library videoip;\n'+text.replace(marker, marker+binding, 1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=12)
    args=parser.parse_args();project=args.project.resolve();work=args.output.resolve()
    if work.exists() or not work.is_relative_to(ROOT/'build') or subprocess.run(
            ['git','check-ignore','--quiet',str(work/'report.json')],cwd=ROOT).returncode:
        raise ValueError('Fresh ignored output required')
    if not 3<=args.jobs<=24:raise ValueError('Use 3..24 total compiler jobs')
    work.mkdir(parents=True);print(work,flush=True);started=time.monotonic()
    initial=inputs(project)
    target=json.loads((project/'timing-inputs.json').read_text())
    if initial!=target['quartus_sources'] or any(digest(project/('apf_top'+ext))!=sha
            for ext,sha in target['project_sha256'].items()):
        raise ValueError('Frozen project/source identities differ')
    selected=assignments(project,(project/'apf_top.qsf').read_text())
    def one(name):
        values=[p for _,p in selected if p.name==name]
        if len(values)!=1:raise ValueError('Expected one assigned '+name)
        return values[0]
    top=one('core_top.sv');top_text=top.read_text()
    for setting in ('.output_clock_frequency0("33.868800 MHz")',
                    '.output_clock_frequency1("67.737600 MHz")',
                    '.output_clock_frequency0("53.693166 MHz")'):
        if top_text.count(setting)!=1:raise ValueError('Unknown Native GPU/CPU/video clocks')
    protected={};archive={}
    def capture(path):
        path=Path(path).resolve()
        if str(path) not in archive:
            copy=work/'input-archive'/str(len(archive))/path.name;copy.parent.mkdir(parents=True)
            before=digest(path);shutil.copyfile(path,copy)
            if digest(path)!=before or digest(copy)!=before:raise ValueError('Input changed while capturing '+str(path))
            archive[str(path)]=dict(path=str(copy),sha256=before);protected[str(path)]=before
        return Path(archive[str(path)]['path'])
    chosen={name:one(name) for lib,name in oracle.SOURCES if not name.startswith('sim:')}
    for path in (top,project/'apf_top.qsf',project/'apf_top.qpf',project/'timing-inputs.json'):capture(path)
    saved={name:capture(path) for name,path in chosen.items()}
    extras=[one(name) for name in ('psx_video_config_cdc.sv','psx_video_request_cdc.sv',
                                  'psx_video_reports_cdc.sv','dual_clock_fifo.sv')]
    for path in extras:capture(path)
    for name in ('RamMLAB.vhd','dpram.vhd'):capture(UPSTREAM/'sim/system/src/mem'/name)
    capture(one('RamM10Kn.vhd'))
    tools=[Path(__file__),ROOT/'scripts/run_gpu_dy_ram.py',ROOT/'scripts/run_gpu_line_share.py',
           ROOT/'scripts/prepare_gpu_dy_ram.py',ROOT/'scripts/prepare_gpu_area_reg.py',
           ROOT/'scripts/prepare_system_verilator.py',ROOT/'scripts/prepare_system_sim.py',
           ROOT/'scripts/ghdl_verilog.py',ROOT/'scripts/prepare_resource_targets.py',
           ROOT/'scripts/tool_paths.py',ROOT/'scripts/measure_pocket_seeds.py',
           ROOT/'scripts/prepare_pocket_config_cdc.py',
           ROOT/'scripts/run_video_dotclock_census.py',ROOT/'sim/reference/tb_gpu_primitives.sv',
           ROOT/'sim/reference/mister_memory_models.sv']
    for path in tools:capture(path)
    # Ensure the imported helper is the exact current polygon's known parent;
    # do not accidentally apply area_reg_text twice or test another variant.
    release=oracle.BASE/'gpu-sources/gpu_poly.vhd';capture(release)
    if oracle.area_reg_text(Path(archive[str(release.resolve())]['path']).read_text())!=saved['gpu_poly.vhd'].read_text():
        raise ValueError('Selected polygon differs from reviewed dy-RAM parent')
    if 'dyRam' in saved['gpu_poly.vhd'].read_text():raise ValueError('Polygon already adapted')
    model=work/'runtime-root/sim/reference/mister_memory_models.sv';model.parent.mkdir(parents=True)
    model.write_text('\n'.join(Path(archive[str(p.resolve())]['path']).read_text() for p in
                    [ROOT/'sim/reference/mister_memory_models.sv',*extras]))
    components=video_components();(work/'video-components.vhd').write_text(components)
    # These are the only simulation adaptations beyond the oracle's existing
    # conversion. Identical on both GPUs; recorded with selected source hashes.
    substitutions={};original_hardware=oracle.hardware;original_transcode=oracle.transcode
    def source_text(name,candidate,fault):
        if name=='sim:RamM10Kn.vhd':return RAM_STUB,str(one('RamM10Kn.vhd'))
        path=(UPSTREAM/'sim/system/src/mem'/name[4:]) if name.startswith('sim:') else chosen[name]
        text=Path(archive[str(path.resolve())]['path']).read_text()
        if name=='gpu_poly.vhd' and candidate:
            text=oracle.dy_ram_text(text)
            if fault:text=oracle.FAULTS[fault](text)
        return text,str(path)
    def hardware(name,text):
        if name=='gpu_videoout_async.vhd':text=adapt_video(text)
        count=text.count('entity work.pocket_dpram_sdp')
        if count:
            if name not in ('gpu_pixelpipeline.vhd','gpu_dither.vhd'):raise ValueError('Unknown SDP model instance')
            ports=re.findall(r':\s*entity work\.pocket_dpram_sdp\b.*?port map\s*\((.*?)\n\s*\);',text,re.S)
            if len(ports)!=count or any(not re.search(r"\bwren_b\s*=>\s*'0'",body) or
                    re.search(r'\bq_a\s*=>\s*(?!open\b)\w+',body) for body in ports):
                raise ValueError('SDP model requires read-only B and unused A readback')
            # Same API, registered read, write-A/read-B only. Both GPUs use the
            # established portable vendor-RAM model. This is not hardware proof.
            text=text.replace('entity work.pocket_dpram_sdp','entity work.dpram')
            substitutions[name]=dict(SDP_read_only_B_aliases=count)
        if name=='SyncFifoFallThroughMLAB.vhd':
            token='entity work.RamM10Kn'
            if text.count(token)!=1:raise ValueError('Unknown FIFO M10K branch')
            text='library ramip;\n'+text.replace(token,'entity ramip.RamM10Kn')
        return original_hardware(name,text)
    def current_video(directory):
        path=directory/'current-video.vhd';path.write_text(saved['gpu_videoout_async.vhd'].read_text())
        return dict(path=str(path),original=str(chosen['gpu_videoout_async.vhd']))
    def transcode(directory,candidate,fault,ghdl,log):
        directory.mkdir(parents=True,exist_ok=True);stub=directory/'video-components.vhd';stub.write_text(components)
        subprocess.run([ghdl,'-a','--std=08','--work=videoip',str(stub)],cwd=directory,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        result=original_transcode(directory,candidate,fault,ghdl,log)
        # The current GPU FIFO uses M10K=false. Its alternate entity must be
        # resolvable at VHDL analysis, but must not survive elaboration. Never
        # accept a live RAM replaced by the intentionally opaque unused stub.
        if re.search('ramm10kn',Path(result[1]).read_text(),re.I):
            raise ValueError('Live M10K FIFO needs an explicit simulation model')
        return result
    oracle.source_text=source_text;oracle.hardware=hardware;oracle.transcode=transcode
    oracle.compatible_async_video_source=current_video;oracle.ROOT=work/'runtime-root'
    oracle.SOURCES.insert(2,('ramip','sim:RamM10Kn.vhd'))
    ghdl,verilator=find_tool('ghdl'),find_tool('verilator')
    with (work/'baseline.log').open('w') as log:baseline=transcode(work/'baseline',False,None,ghdl,log)
    runs=[(work/'candidate',None),*[(work/('fault-'+f),f) for f in oracle.FAULTS]]
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        results=list(pool.map(lambda row:oracle.run(row[0],baseline,row[1],ghdl,verilator,max(1,args.jobs//3)),runs))
    good=results[0];cov=good['coverage'] or {}
    coverage=all(int(cov.get(k,0))>0 for k in ('calccolor4','calctexture4','dyU0_dxV0_4096',
                 'dyU0_dxV0_not4096','sheared_4096','decide_skip','decide_sheared','dxV_nonzero'))
    faults={r['fault']:not r['passed'] and r['exit_code']!=0 and any('mismatch' in line.lower()
           for line in r['first_failure']) for r in results[1:]}
    identities=all(digest(Path(p))==sha for p,sha in protected.items())
    identities=identities and all(digest(Path(v['path']))==v['sha256'] for v in archive.values())
    identities=identities and inputs(project)==initial
    report=dict(passed=identities and good['passed'] and coverage and len(faults)==2 and all(faults.values()),
        project=str(project),selected_sources={name:dict(path=str(path),sha256=protected[str(path)]) for name,path in chosen.items()},
        original_sources_unchanged=identities,input_archives=archive,baseline_sources=baseline[0],
        baseline_verilog_sha256=digest(baseline[1]),candidate=good,faults=faults,fault_runs=results[1:],
        source_adaptations=dict(SDP_RAM=substitutions,unused_M10K_FIFO_branch_removed=True,
            video='Exact selected config/request/report channels with external SV bindings, attributes stripped, low9bit blank/reset conversions'),
        current_video_channels_included=True,coverage_complete=coverage,
        seconds=round(time.monotonic()-started,1),physical_timing_verified=False,hardware_verified=False,
        scope='Current selected GPU cycle-output/readback/VRAM comparison of dy-RAM only; portable vendor RAM/DDR models; no CPU/game/APF pins or analog model')
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('FINAL',report['passed'],'ticks',good['cycle_ticks'],'programs',good['programs'],'coverage',cov,'faults',faults,flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
