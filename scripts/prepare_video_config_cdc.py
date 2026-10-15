#!/usr/bin/env python3
"""Opt-in ordered CPU-to-video packet bridge for a captured MiSTer scanout.

The packet holds all80settings bits and CE/softReset/save-pause/system-pause.
Preserves snapshot/effect order, with added delivery/startup latency. No
production preset is selected. Requires the pinned Laxer dual_clock_fifo.
"""
import argparse
import json
import shutil
from prepare_resource_targets import digest, UPSTREAM
from tool_paths import ROOT
from pathlib import Path
import re

FIELDS=[('GPUSTAT_VerRes',1,'s'),('GPUSTAT_PalVideoMode',1,'s'),('GPUSTAT_VertInterlace',1,'s'),
 ('GPUSTAT_HorRes2',1,'s'),('GPUSTAT_HorRes1',2,'v'),('GPUSTAT_ColorDepth24',1,'s'),
 ('GPUSTAT_DisplayDisable',1,'s'),('vramRange',19,'u'),('hDisplayRange',24,'u'),
 ('vDisplayRange',20,'u'),('pal60',1,'s'),('syncInterlace',1,'s'),('rotate180',1,'s'),
 ('fixedVBlank',1,'s'),('vCrop',2,'v'),('hCrop',1,'s'),('dither24',1,'s'),('render24',1,'s')]
assert sum(f[1] for f in FIELDS)==80


def adapt_videoout(text):
    def replace(before,after):
        nonlocal text
        if text.count(before)!=1:raise ValueError('Video config anchor changed: '+before[:90])
        text=text.replace(before,after)
    replace('   signal videoout_settings_s2 : tvideoout_settings;','')
    replace('   signal videoout_settings_s1 : tvideoout_settings;','')
    for line in ['         videoout_settings_s2 <= videoout_settings_1x;',
                 '         videoout_settings_s1 <= videoout_settings_s2;',
                 '         videoout_settings    <= videoout_settings_s1;']:
        replace(line,'')
    for signal in ('ce','softReset','savestate_pause','system_paused'):
        for suffix,source in [('_s2',signal+'_1x'),('_s1',signal+'_s2'),('',signal+'_s1')]:
            text,n=re.subn(r'^\s*'+signal+suffix+r'\s*<=\s*'+source+r';[^\S\n]*$', '', text, flags=re.M)
            if n!=1:raise ValueError('Missing coherent control stage '+signal+suffix)
        for suffix in ('_s2','_s1'):
            text,n=re.subn(r'^\s*signal '+signal+suffix+r'\s*:\s*std_logic;[^\n]*\n','\n',text,flags=re.M)
            if n!=1:raise ValueError('Missing coherent control declaration '+signal+suffix)
    replace('         reset                <= reset_s1;', '         reset_sampled        <= reset_s1;')
    controls=['reset_s2','reset_s1','reset_sampled',
        'video_config_warm0','video_config_warm1',
        'video_config_reset','video_config_fault_meta','video_config_fault_sync']
    declarations='''
   component psx_video_config_cdc is
      port (
         src_clk,dst_clk,reset_async,src_valid : in std_logic;
         src_data : in std_logic_vector(83 downto 0);
         src_ready : out std_logic;
         dst_data : out std_logic_vector(83 downto 0);
         dst_update,dst_initialized,src_fault : out std_logic
      );
   end component;
   signal video_config_input,video_config_output : std_logic_vector(83 downto 0);
   signal video_config_reset : std_logic := '1';
   signal video_config_initialized,video_config_fault : std_logic;
   signal video_config_warm0,video_config_warm1 : std_logic := '0';
   signal video_config_fault_meta,video_config_fault_sync : std_logic := '0';
   signal reset_sampled : std_logic;
   attribute preserve : boolean;
   attribute dont_merge : boolean;
   attribute async_reg : string;
   attribute altera_attribute : string;
'''
    for name in controls:
        declarations+=f'   attribute preserve of {name} : signal is true;\n'
        declarations+=f'   attribute dont_merge of {name} : signal is true;\n'
        declarations+=f'   attribute altera_attribute of {name} : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";\n'
    for name in controls:
        if name.endswith(('_s2','_s1','_meta','_sync')):
            declarations+=f'   attribute async_reg of {name} : signal is "true";\n'
    statements='''
   -- Continuous ordered CPU-state snapshots; no settings/control coalescing.
   process(clk1x) begin
      if rising_edge(clk1x) then video_config_reset <= reset_1x; end if;
   end process;
   video_config : psx_video_config_cdc port map (
      src_clk=>clk1x,dst_clk=>clkvid,reset_async=>video_config_reset,src_valid=>'1',
      src_data=>video_config_input,src_ready=>open,dst_data=>video_config_output,
      dst_update=>open,dst_initialized=>video_config_initialized,src_fault=>video_config_fault);
   process(clkvid) begin
      if rising_edge(clkvid) then
         video_config_fault_meta <= video_config_fault;
         video_config_fault_sync <= video_config_fault_meta;
         if video_config_initialized='0' then
            video_config_warm0<='0';video_config_warm1<='0';
         else
            video_config_warm0<='1';video_config_warm1<=video_config_warm0;
         end if;
      end if;
   end process;
   -- Existing derived vertical sizes need two clocks after first data.
   reset <= reset_sampled or not video_config_initialized or not video_config_warm1 or video_config_fault_sync;
   ce <= video_config_output(83);
   softReset <= video_config_output(82);
   savestate_pause <= video_config_output(81);
   system_paused <= video_config_output(80);
'''
    terms=[];bit=79
    for name,width,kind in FIELDS:
        term='videoout_settings_1x.'+name
        terms.append('std_logic_vector('+term+')' if kind=='u' else term)
        value=f'video_config_output({bit})' if width==1 else f'video_config_output({bit} downto {bit-width+1})'
        statements+=f'   videoout_settings.{name} <= '+('unsigned('+value+')' if kind=='u' else value)+';\n'
        bit-=width
    assert bit==-1
    statements+='   video_config_input <= ce_1x & softReset_1x & savestate_pause_1x & system_paused_1x &\n      '+' &\n      '.join(terms)+';\n'
    replace('begin \n\n   -- clk1x -> clkvid',declarations+'begin \n'+statements+'\n   -- clk1x -> clkvid')
    return text


def prepare(selected, directory, expected_sha256):
    selected=Path(selected).resolve()
    if digest(selected)!=expected_sha256:
        raise ValueError('Video source does not match expected identity')
    text=adapt_videoout(selected.read_text())
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=False)
    vhdl=directory/'gpu_videoout_async.vhd';vhdl.write_text(text)
    added=[]
    for original in [ROOT/'rtl/pocket/psx_video_config_cdc.sv',ROOT/'scripts/pocket_video_config_scope.tcl']:
        sha=digest(original);copy=directory/original.name;shutil.copyfile(original,copy)
        if digest(copy)!=sha:raise ValueError('Video packet source changed while copying')
        added.append(dict(path=str(copy),sha256=sha,original=str(original)))
    shutil.copyfile(UPSTREAM/'LICENSE',directory/'COPYING.GPL2')
    if digest(selected)!=expected_sha256:raise ValueError('Video input changed during preparation')
    fifo=ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv'
    result=dict(path=str(vhdl),sha256=digest(vhdl),original=str(selected),original_sha256=expected_sha256,
        added=added,dependencies=[dict(path=str(fifo),sha256=digest(fifo),license='BSD-2-Clause notices retained in source')],
        packet_bits=84,settings_bits=80,control_bits=['ce_1x','softReset_1x','savestate_pause_1x','system_paused_1x'],
        packet_snapshots_coalesce=False,nominal_source_valid_every_clock=True,
        FIFO_words=16,usable_FIFO_words=15,video_warmup_clocks_after_first_packet=2,
        production_selected=False,cycle_equivalent=False,game_verified=False,hardware_verified=False,
        limitations=['Add returned module/SDC and exactly one pinned FIFO compilation unit explicitly; replace original VHDL assignment',
            'Source must provide complete CPU-clock values; destination must continuously run faster in the guarded clock range',
            'FIFO exhaustion is a latched fault; a stopped consumer requires common reset before continuing',
            'Added delivery/startup latency needs game and dynamic transition timing qualification',
            'Reverse video report/request and other clock crossings are outside this helper'])
    (directory/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('selected',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--expected-sha256',required=True);a=p.parse_args()
    print(json.dumps(prepare(a.selected,a.output,a.expected_sha256),indent=2))
