#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Freeze a generated Native project and transcode its selected GPU for simulation.

This preserves the selected GPU bodies. Compatibility changes bind portable
RAM/config components, split divider directions, and qualify integer math.
It does not establish which bytes an earlier hardware compiler consumed.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
from measure_pocket_seeds import inputs
from prepare_resource_targets import digest, hardware_text, GPU_SOURCES
from prepare_system_verilator import prepare as prepare_system, gpu_transcode_source
from ghdl_verilog import normalize_generated_verilog
from tool_paths import ROOT, find_tool

NAMES = ['pGPU.vhd', 'gpu_overlay.vhd', 'gpu_dither.vhd',
         'gpu_videoout_sync.vhd', 'gpu_crosshair.vhd',
         'justifier_sensor.vhd', 'gpu_videoout_async.vhd', 'gpu_videoout.vhd']

def prepare_snapshot(directory, project):
    HERE = Path(directory).resolve()
    PROJECT = Path(project).resolve()
    HERE.mkdir(parents=True, exist_ok=False)
    frozen = HERE / 'frozen'
    frozen.mkdir(exist_ok=False)
    selected = inputs(PROJECT)
    manifest = []
    for i, item in enumerate(selected):
        old = Path(item['path'])
        new = frozen / str(i) / old.name
        new.parent.mkdir()
        shutil.copyfile(old, new)
        assert digest(old) == digest(new) == item['sha256']
        manifest.append(dict(item, captured=str(new)))
    extra = {}
    for rel in ['references/PSX_MiSTer/sim/system/src/mem/dpram.vhd',
                'sim/reference/mister_memory_models.sv',
                'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
                'sim/pocket/psx_cram_model.sv',
                'sim/pocket/psx_vram_psram_windows.svh',
                'sim/pocket/tb_psx_vram_psram.sv']:
        old = ROOT / rel
        new = frozen / 'extra' / old.name
        new.parent.mkdir(exist_ok=True)
        shutil.copyfile(old, new)
        extra[old.name] = dict(original=str(old), captured=str(new), sha256=digest(new))
    by_name = {}
    for n in NAMES + ['core_top.sv', 'psx_vram_psram.sv', 'psx_video_config_cdc.sv', 'gpu.vhd']:
        found = [x for x in manifest if Path(x['path']).name == n]
        assert len(found) == 1, (n, found)
        by_name[n] = found[0]
    rbf = PROJECT / 'output_files/apf_top.rbf'
    report = dict(inputs=manifest, extra=extra, by_name=by_name,
                  project=str(PROJECT),
                  RBF=(dict(path=str(rbf), sha256=digest(rbf)) if rbf.is_file() else None),
                  compiled_leaf_identity_proven=False,
                  limitation='Current QSF-selected bytes do not establish the input bytes of an older compiler run.')
    (HERE / 'sources.json').write_text(json.dumps(report, indent=2)+'\n')

    work = HERE / 'converted'
    work.mkdir()
    with (work/'build.log').open('w') as log:
        def analyze(p, library='work'):
            subprocess.run([find_tool('ghdl'), '-a', '--std=08', '-frelaxed', '--work='+library, str(p)],
                           cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        p = work/'dummy.vhd'
        p.write_text('package dummy is end package;')
        analyze(p, 'mem')
        analyze(Path(extra['dpram.vhd']['captured']), 'ramip')
        p = work/'stub.vhd'
        p.write_text('''library ieee;use ieee.std_logic_1164.all;
entity psx_video_config_cdc is port(src_clk,dst_clk,reset_async,src_valid:in std_logic;
src_data:in std_logic_vector(83 downto 0);src_ready:out std_logic;dst_data:out std_logic_vector(83 downto 0);
dst_update,dst_initialized,src_fault:out std_logic);end entity;
architecture opaque of psx_video_config_cdc is begin end architecture;
''')
        analyze(p, 'videoip')
        changes = {}
        for n in NAMES:
            text = Path(by_name[n]['captured']).read_text()
            transforms = []
            if n == 'gpu_videoout_async.vhd':
                marker = 'architecture arch of gpu_videoout_async is'
                assert text.count(marker) == 1
                text = 'library videoip;\n' + text.replace(marker, marker +
                    '\n   for video_config:psx_video_config_cdc use entity videoip.psx_video_config_cdc;\n')
                transforms.append('Bind existing forward-config component to real SV blackbox')
                expressions = [('(lineMax - 1)',9,10,3), ('(lineMax - 2)',9,10,1),
                               ('((vdispNew - vDisplayStart) * 2) + 1',9,12,3),
                               ('(vdispNew - vDisplayStart) * 2',9,12,3),
                               ('vdispNew - vDisplayStart',9,12,3)]
                for expr, dst, width, count in expressions:
                    old = f'to_unsigned({expr}, {dst})'
                    assert text.count(old) == count, (old, text.count(old))
                    text = text.replace(old, f'resize(unsigned(to_signed({expr}, {width})), {dst})')
                transforms.append('Retain low bits of signed off-screen integer calculations as hardware does')
            if n == 'gpu_videoout.vhd':
                for expr, count in {'to_integer(videoout_request_clkvid.lineDisp)':9,
                                    'to_integer(Gun1Y_screen)':2, 'to_integer(Gun2Y_screen)':2}.items():
                    assert text.count(expr) == count
                    text = text.replace(expr, "integer'("+expr+")")
                text, count = re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                                      r"\1integer'(videoout_request_clkvid.xpos),", text)
                assert count == 5
                transforms.append('Qualify 18 integer scalar associations without changing values')
            text, count = re.subn(r'entity (?:work|mem)\.(dpram(?:_dif)?)\b', r'entity ramip.\1', text, flags=re.I)
            if count:
                text = 'library ramip;\n' + text
                transforms.append('Bind RAM to pinned portable behavioral model')
            text = hardware_text(text)
            p = work/n
            p.write_text(text)
            analyze(p)
            changes[n] = dict(path=str(p), sha256=digest(p), transforms=transforms)
        verilog = work/'scanout.v'
        with verilog.open('w') as out:
            subprocess.run([find_tool('ghdl'), '--synth', '--std=08', '-frelaxed', '--vendor-library=videoip',
                            '--vendor-library=ramip', '--out=verilog', 'gpu_videoout'], cwd=work,
                           stdout=out, stderr=log, check=True, timeout=120)
    normalize_generated_verilog(verilog)
    report['converted'] = dict(path=str(verilog), sha256=digest(verilog), sources=changes)
    (HERE/'sources.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def split_selected(text):
    for i in range(1,7):
        text,n=re.subn(rf'      div{i}\s*: inout div_type;',
            f'      div{i}_in : in div_type;\n      div{i}_out : out div_type;',text)
        assert n==1
        for field in ('start','dividend','divisor'):text=text.replace(f'div{i}.{field}',f'div{i}_out.{field}')
        for field in ('done','quotient','remainder'):text=text.replace(f'div{i}.{field}',f'div{i}_in.{field}')
    assignments='\n'.join(f"   div{i}_out.done <= '0'; div{i}_out.quotient <= (others=>'0'); div{i}_out.remainder <= (others=>'0');" for i in range(1,7))
    text,n=re.subn(r'^begin[ \t]*$',lambda m:'begin\n'+assignments,text,count=1,flags=re.M)
    assert n==1
    return text


def prepare_gpu(directory):
    HERE = Path(directory).resolve()
    selected=json.loads((HERE/'sources.json').read_text())
    work=HERE/'gpu';work.mkdir(exist_ok=False)
    base=prepare_system(work/'base')
    by_name={}
    for name in GPU_SOURCES+['psx_gpu_read_math.vhd']:
        matches=[x for x in selected['inputs'] if Path(x['path']).name==name]
        assert len(matches)==1,(name,matches)
        by_name[name]=matches[0]

    rows=[]
    with (work/'build.log').open('w') as log:
        def analyze(path,lib='work'):
            subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+lib,str(path)],
                           cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        analyze(HERE/'converted/stub.vhd','videoip')
        for row in base['sources']:
            name=Path(row['path']).name
            source=Path(row['path'])
            if name in by_name:
                actual=Path(by_name[name]['captured'])
                text=hardware_text(actual.read_text())
                transforms=[]
                if name in ['gpu_videoout.vhd','gpu_videoout_async.vhd']:
                    text=(HERE/'converted'/name).read_text()
                    transforms=['Previously recorded scanout compatibility bindings']
                elif name=='gpu_poly.vhd':
                    # Selected timing-pruned polygon has four cull abs() and
                    # two remaining wide constants, not the pinned 9/3 shape.
                    assert text.count('abs(')==4 and text.count('x"100000000"')==2
                    text=text.replace('abs(','magnitude(')
                    fn=('   function magnitude(value : integer) return integer is\n'
                        '   begin\n'
                        '      if value < 0 then return -value; else return value; end if;\n'
                        '   end function;\n')
                    text,n=re.subn(r'^begin[ \t]*$',lambda m:fn+'begin',text,count=1,flags=re.M)
                    assert n==1
                    text=text.replace('x"100000000"','signed\'(\"000000000\" & x\"100000000\")')
                    marker='to_integer(xStart(43 downto 32)) > drawingAreaRight'
                    assert text.count(marker)==1
                    text=text.replace(marker,'xStart(43 downto 32) > signed(resize(drawingAreaRight, 12))')
                    text=split_selected(text)
                    transforms=['Same 4 integer magnitudes, 2 explicit wide constants, signed span comparison, divider binding']
                elif name=='gpu_line.vhd':
                    text=split_selected(text)
                    transforms=['Same divider request/result directions, explicit architecture binding']
                elif name=='gpu.vhd':
                    text=gpu_transcode_source(Path(name).stem,text)
                    transforms=['Standard divider direction / signed magnitude compatibility']
                text,n=re.subn(r'entity (?:work|mem)\.(RamMLAB|dpram(?:_dif)?)\b',r'entity ramip.\1',text,flags=re.I)
                if n:
                    text='library ramip;\n'+text
                    transforms.append('Opaque pinned RAM semantics')
                source=work/name;source.write_text(text)
                rows.append(dict(original=by_name[name],adapted=str(source),sha256=digest(source),transforms=transforms))
                if name=='gpu.vhd':
                    math=Path(by_name['psx_gpu_read_math.vhd']['captured'])
                    analyze(math)
                    rows.append(dict(original=by_name['psx_gpu_read_math.vhd'],adapted=str(math),sha256=digest(math)))
            analyze(source,row['library'])
        with (work/'gpu.v').open('w') as out:
            subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=ramip',
                            '--vendor-library=videoip','--out=verilog','gpu'],cwd=work,
                           stdout=out,stderr=log,check=True,timeout=180)
    normalize_generated_verilog(work/'gpu.v')
    report=dict(selected=rows,portable_support=base,verilog=dict(path=str(work/'gpu.v'),sha256=digest(work/'gpu.v')),
                selected_current_QSF_bodies=True,compiled_RBF_leaf_identity_proven=False)
    (work/'sources.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

