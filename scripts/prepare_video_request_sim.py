"""Frozen pinned scanout + VRAM fetcher, original and coherent requests.

Both use the existing ordered forward configuration bridge and the same
portable RAM model. Only the candidate's reverse request transport differs.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
from ghdl_verilog import normalize_generated_verilog
from prepare_resource_targets import UPSTREAM,hardware_text,digest
from prepare_system_sim import compatible_async_video_source
from prepare_video_config_cdc import adapt_videoout as forward
from prepare_video_request_cdc import adapt_videoout as reverse
from tool_paths import ROOT,find_tool


def prepare(directory):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    names=['pGPU.vhd','gpu_overlay.vhd','gpu_dither.vhd','gpu_videoout_sync.vhd',
           'gpu_crosshair.vhd','justifier_sensor.vhd','gpu_videoout.vhd','gpu_videoout_async.vhd']
    files=[UPSTREAM/'rtl'/n for n in names]+[UPSTREAM/'sim/system/src/mem/dpram.vhd',
           ROOT/'rtl/pocket/psx_video_request_cdc.sv',ROOT/'rtl/pocket/psx_video_config_cdc.sv',
           ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',ROOT/'sim/reference/mister_memory_models.sv',
           Path(__file__).resolve(),ROOT/'scripts/prepare_video_request_cdc.py',ROOT/'scripts/prepare_video_config_cdc.py',
           ROOT/'scripts/prepare_system_sim.py',ROOT/'scripts/ghdl_verilog.py']
    captured={};protected={}
    for i,p in enumerate(files):
        q=directory/'inputs'/str(i)/p.name;q.parent.mkdir(parents=True);shutil.copyfile(p,q)
        protected[str(p)]=digest(p);assert digest(q)==protected[str(p)]
        captured[str(p)]=dict(path=str(q),sha256=digest(q))
    (directory/'compatible').mkdir()
    compatible=compatible_async_video_source(directory/'compatible')
    assert compatible['original_sha256']==protected[str(UPSTREAM/'rtl/gpu_videoout_async.vhd')]
    async_text=forward(Path(compatible['path']).read_text())
    converted={}
    for label in ('original','candidate'):
        work=directory/label;work.mkdir();log=work/'build.log'
        def analyze(path,library='work'):
            subprocess.run([find_tool('ghdl'),'-a','--std=08','-frelaxed','--work='+library,str(path)],
                           cwd=work,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=60)
        with log.open('w') as output:
            dummy=work/'dummy.vhd';dummy.write_text('package dummy is end package;');analyze(dummy,'mem')
            analyze(Path(captured[str(UPSTREAM/'sim/system/src/mem/dpram.vhd')]['path']),'ramip')
            stub=work/'stub.vhd';stub.write_text('''library ieee;use ieee.std_logic_1164.all;
entity psx_video_config_cdc is port(src_clk,dst_clk,reset_async,src_valid:in std_logic;
src_data:in std_logic_vector(83 downto 0);src_ready:out std_logic;dst_data:out std_logic_vector(83 downto 0);
dst_update,dst_initialized,src_fault:out std_logic);end entity;
architecture opaque of psx_video_config_cdc is begin end architecture;
library ieee;use ieee.std_logic_1164.all;
entity psx_video_request_cdc is port(src_clk,dst_clk,reset_async:in std_logic;
src_data:in std_logic_vector(38 downto 0);src_ready,src_fault:out std_logic;dst_data:out std_logic_vector(38 downto 0);
dst_initialized,dst_fault:out std_logic);end entity;
architecture opaque of psx_video_request_cdc is begin end architecture;
''');analyze(stub,'videoip')
            for name in names:
                text=Path(captured[str(UPSTREAM/'rtl'/name)]['path']).read_text()
                if name=='gpu_videoout_async.vhd':
                    text=reverse(async_text) if label=='candidate' else async_text
                    marker='architecture arch of gpu_videoout_async is'
                    binding='\n   for video_config:psx_video_config_cdc use entity videoip.psx_video_config_cdc;\n'
                    if label=='candidate':binding+='   for video_request:psx_video_request_cdc use entity videoip.psx_video_request_cdc;\n'
                    text='library videoip;\n'+text.replace(marker,marker+binding)
                if name=='gpu_videoout.vhd':
                    for expression,count in {'to_integer(videoout_request_clkvid.lineDisp)':9,
                                             'to_integer(Gun1Y_screen)':2,'to_integer(Gun2Y_screen)':2}.items():
                        assert text.count(expression)==count
                        text=text.replace(expression,"integer'("+expression+")")
                    text,n=re.subn(r'(i_pixel_out_x\s*=> )videoout_request_clkvid.xpos,',
                                   r"\1integer'(videoout_request_clkvid.xpos),",text);assert n==5
                text,n=re.subn(r'entity (?:work|mem)\.(dpram(?:_dif)?)\b',r'entity ramip.\1',text,flags=re.I)
                if n:text='library ramip;\n'+text
                q=work/name;q.write_text(hardware_text(text))
                if name!='gpu_videoout.vhd':analyze(q)
            # The parent directly instantiates asynchronous video, so analyze
            # it only after the source-order list's final async entry.
            analyze(work/'gpu_videoout.vhd')
            verilog=work/'scanout.v'
            with verilog.open('w') as target:
                subprocess.run([find_tool('ghdl'),'--synth','--std=08','-frelaxed','--vendor-library=videoip',
                    '--vendor-library=ramip','--out=verilog','gpu_videoout'],cwd=work,stdout=target,
                    stderr=output,check=True,timeout=120)
        normalize_generated_verilog(verilog)
        text=verilog.read_text()
        for module in re.findall(r'^module (\w+)',text,re.M):text=re.sub(r'\b'+re.escape(module)+r'\b',label+'__'+module,text)
        verilog.write_text(text);converted[label]=str(verilog)
    assert all(digest(Path(p))==h for p,h in protected.items())
    result=dict(converted=converted,inputs=captured,protected_sha256=protected,compatibility=compatible)
    (directory/'sources.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
