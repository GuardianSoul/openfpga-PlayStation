"""Frozen, simulation-only MiSTer video conversion with opaque vendor ports.

The candidate uses the actual SV packet wrapper and unchanged pinned FIFO.
Both engines use the existing low-bit signed-offset conversion and RAM model.
No BIOS/disc or production source is edited.
"""
from pathlib import Path
import subprocess
import re
import json
import shutil
from tool_paths import ROOT,find_tool
from prepare_system_sim import compatible_async_video_source
from prepare_video_config_cdc import adapt_videoout
from ghdl_verilog import normalize_generated_verilog
from prepare_resource_targets import hardware_text,digest


def prepare(directory,edit=None):
    root=ROOT
    dest=Path(directory).resolve();dest.mkdir(parents=True,exist_ok=False)
    ghdl=find_tool('ghdl')
    raws=[root/'references/PSX_MiSTer/rtl/pGPU.vhd',root/'references/PSX_MiSTer/rtl/gpu_dither.vhd',
        root/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd',root/'references/PSX_MiSTer/sim/system/src/mem/dpram.vhd',
        root/'rtl/pocket/psx_video_config_cdc.sv',root/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        root/'sim/reference/mister_memory_models.sv',Path(__file__).resolve(),
        root/'scripts/prepare_video_config_cdc.py',root/'scripts/prepare_system_sim.py',root/'scripts/ghdl_verilog.py']
    captured={};protected={}
    for i,p in enumerate(raws):
        sha=digest(p);copy=dest/'input-archive'/str(i)/p.name;copy.parent.mkdir(parents=True)
        shutil.copyfile(p,copy)
        if digest(copy)!=sha:raise ValueError('Video conversion input changed during capture')
        captured[str(p)]=dict(path=str(copy),sha256=sha);protected[str(p)]=sha;protected[str(copy)]=sha
    compat=compatible_async_video_source(dest)
    if compat['original_sha256']!=protected[str(root/'references/PSX_MiSTer/rtl/gpu_videoout_async.vhd')]:
        raise ValueError('Video reference changed before compatible conversion')
    original=Path(compat['path']).read_text()
    if edit:original=edit(original)  # a prepare option's source edit, e.g. prepare_pal_line_rate.patch
    with (dest/'build.log').open('w') as log:
        dummy=dest/'dummy.vhd';dummy.write_text('package dummy is end package;')
        subprocess.run([ghdl,'-a','--std=08','--work=mem',str(dummy)],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        pkg=root/'references/PSX_MiSTer/rtl/pGPU.vhd'
        subprocess.run([ghdl,'-a','--std=08',str(Path(captured[str(pkg)]['path']))],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        ram=root/'references/PSX_MiSTer/sim/system/src/mem/dpram.vhd'
        subprocess.run([ghdl,'-a','--std=08','-frelaxed','--work=ramip',str(Path(captured[str(ram)]['path']))],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        dither=dest/'gpu_dither.vhd';dither.write_text('library ramip;\n'+Path(captured[str(root/'references/PSX_MiSTer/rtl/gpu_dither.vhd')]['path']).read_text().replace('entity work.dpram','entity ramip.dpram'))
        subprocess.run([ghdl,'-a','--std=08',str(dither)],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        stub=dest/'entity.vhd';stub.write_text('''library ieee; use ieee.std_logic_1164.all;
    entity psx_video_config_cdc is port (
     src_clk,dst_clk,reset_async,src_valid : in std_logic;
     src_data : in std_logic_vector(83 downto 0);src_ready : out std_logic;
     dst_data : out std_logic_vector(83 downto 0);dst_update,dst_initialized,src_fault : out std_logic);
    end entity;
    architecture opaque of psx_video_config_cdc is begin end architecture;
    ''')
        subprocess.run([ghdl,'-a','--std=08','--work=videoip',str(stub)],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
        for name,text in [('original',original),('candidate',adapt_videoout(original))]:
            entity='video_'+name
            text=text.replace('gpu_videoout_async',entity)
            if name=='candidate':
                text='library videoip;\n'+text
                marker='   signal video_config_input,video_config_output'
                text=text.replace(marker,'   for video_config : psx_video_config_cdc use entity videoip.psx_video_config_cdc;\n'+marker)
            vhd=dest/(name+'.vhd');vhd.write_text(hardware_text(text))
            subprocess.run([ghdl,'-a','--std=08','-frelaxed',str(vhd)],cwd=dest,stdout=log,stderr=subprocess.STDOUT,check=True)
            verilog=dest/(name+'.v')
            with verilog.open('w') as f:subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=videoip','--vendor-library=ramip','--out=verilog',entity],cwd=dest,stdout=f,stderr=log,check=True)
            normalize_generated_verilog(verilog)
            generated=verilog.read_text()
            for module in re.findall(r'^module (\w+)',generated,re.M):
                generated=re.sub(r'\b'+re.escape(module)+r'\b',name+'__'+module,generated)
            verilog.write_text(generated)
            # Record fields flatten to packed vectors. Verify native source ports.
            print(name,verilog,flush=True)

    for p in [dummy,stub,dither,dest/'original.vhd',dest/'candidate.vhd',dest/'original.v',dest/'candidate.v']:
        protected[str(p)]=digest(p)
    if not all(digest(Path(p))==h for p,h in protected.items()):raise ValueError('Video conversion input identity changed')
    result=dict(source_sha256=protected,input_archives=captured,original=str(dest/'original.v'),candidate=str(dest/'candidate.v'),
        wrapper=captured[str(root/'rtl/pocket/psx_video_config_cdc.sv')]['path'],
        fifo=captured[str(root/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv')]['path'],
        memory_models=captured[str(root/'sim/reference/mister_memory_models.sv')]['path'],
        ghdl_signed_line_offset_compatibility=compat)
    (dest/'sources.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
