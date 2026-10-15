#!/usr/bin/env python3
"""Pair ordered report/tick transport with a strobe-consuming MiSTer timer.

Opt-in copied project only. Pinned sources, rendering and request/config FIFOs
are untouched. The report FIFO adds latency and needs integrated source-rate,
timer, full-fit and all-corner checks before hardware selection.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
from prepare_pocket_config_cdc import assignments,scoped_hold_text
from prepare_resource_targets import digest,UPSTREAM
from tool_paths import ROOT


DECL='''
   -- POCKET_REPORT_CDC_DECL_BEGIN
   component psx_video_reports_cdc is
      port (
         src_clk,dst_clk,reset_async : in std_logic;
         src_levels : in std_logic_vector(8 downto 0);
         src_dot : in std_logic;
         src_ready,src_fault : out std_logic;
         dst_levels : out std_logic_vector(8 downto 0);
         dst_dot,dst_update,dst_initialized,dst_fault : out std_logic
      );
   end component;
   signal reports_source,reports_dest : std_logic_vector(8 downto 0);
   signal reports_dot : std_logic;
   -- POCKET_REPORT_CDC_DECL_END
'''
BODY='''   -- POCKET_REPORT_CDC_BODY_BEGIN
   reports_source <= allowunpause & videoout_reports.vsync & videoout_reports.irq_VBLANK &
      videoout_reports.hblank_tmr & videoout_reports.GPUSTAT_InterlaceField &
      videoout_reports.GPUSTAT_DrawingOddline & videoout_reports.inVsync &
      videoout_reports.interlacedDisplayField & videoout_reports.activeLineLSB;
   video_reports : psx_video_reports_cdc port map (
      src_clk=>clkvid,dst_clk=>clk1x,reset_async=>request_reset,
      src_levels=>reports_source,src_dot=>videoout_reports.dotclock,
      src_ready=>open,src_fault=>open,dst_levels=>reports_dest,dst_dot=>reports_dot,
      dst_update=>open,dst_initialized=>open,dst_fault=>open);
   allowunpause1x <= reports_dest(8);
   videoout_reports_1x.vsync <= reports_dest(7);
   videoout_reports_1x.irq_VBLANK <= reports_dest(6);
   videoout_reports_1x.hblank_tmr <= reports_dest(5);
   videoout_reports_1x.GPUSTAT_InterlaceField <= reports_dest(4);
   videoout_reports_1x.GPUSTAT_DrawingOddline <= reports_dest(3);
   videoout_reports_1x.inVsync <= reports_dest(2);
   videoout_reports_1x.interlacedDisplayField <= reports_dest(1);
   videoout_reports_1x.activeLineLSB <= reports_dest(0);
   videoout_reports_1x.dotclock <= reports_dot;
   -- POCKET_REPORT_CDC_BODY_END'''
EDGE='newTick(0) := dotclock and (not dotclock_1);'
STROBE='newTick(0) := dotclock; -- POCKET_REPORT_TICK_STROBE'


def restore(text,patches):
    for before,after in reversed(patches):
        if text.count(after)!=1:raise ValueError('Report transform restoration anchor changed')
        text=text.replace(after,before,1)
    return text


def adapt_videoout(original):
    if 'POCKET_REPORT_CDC_' in original or original.count('component psx_video_request_cdc is')!=1:
        raise ValueError('Require one existing ordered request transport and no report adaptation')
    if original.count("if rising_edge(clk1x) then request_reset <= reset_1x; end if;")!=1:
        raise ValueError('Unknown shared request reset')
    text=original;patches=[]
    def patch(before,after):
        nonlocal text
        if text.count(before)!=1:raise ValueError('Changed report adaptation anchor: '+before[:100])
        text=text.replace(before,after,1);patches.append((before,after))
    for name in ('videoout_reports_s1','videoout_reports_s2','allowunpause_s1','allowunpause_s2'):
        matches=re.findall(r'^   signal '+name+r'\s+:[^\n]+\n',text,re.M)
        if len(matches)!=1:raise ValueError('Missing original report stage '+name)
        patch(matches[0],'   -- POCKET_REPORT_RETIRED_'+name+'\n')
        attrs=re.findall(r'^   attribute altera_attribute of '+name+r' : signal is [^\n]+\n',text,re.M)
        if len(attrs)>1:raise ValueError('Duplicate original report attribute')
        if attrs:patch(attrs[0],'   -- POCKET_REPORT_ATTRIBUTE_RETIRED_'+name+'\n')
    pattern=r'   process \(clk1x\)\n   begin\n      if rising_edge\(clk1x\) then\s*videoout_reports_s1\s*<= videoout_reports;\s*videoout_reports_s2\s*<= videoout_reports_s1;\s*videoout_reports_1x\s*<= videoout_reports_s2;\s*allowunpause_s1\s*<= allowunpause;\s*allowunpause_s2\s*<= allowunpause_s1;\s*allowunpause1x\s*<= allowunpause_s2;\s*end if;\s*end process;'
    matches=re.findall(pattern,text)
    if len(matches)!=1:raise ValueError('Unsupported report sampling process')
    patch(matches[0],BODY)
    starts=list(re.finditer(r'^begin[ \t]*\n',text,re.M))
    if len(starts)!=1:raise ValueError('Unknown video architecture begin')
    # Replace one exact architecture boundary rather than any process begin.
    at=starts[0].start();before=text[max(0,at-1):starts[0].end()]
    patch(before,before[:1]+DECL+before[1:])
    if restore(text,patches)!=original:raise ValueError('Unrestorable report source adaptation')
    return text,patches


def adapt_timer(text):
    if text.count(EDGE)!=1 or STROBE in text:raise ValueError('Expected original MiSTer timer dotclock edge detector')
    return text.replace(EDGE,STROBE)


def prepare_project(directory,expected_video_sha256,expected_timer_sha256):
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';q=qsf.read_text()
    assigned=assignments(directory,q)
    _,removed=scoped_hold_text((directory/'pocket.sdc').read_text())
    if removed:raise ValueError('Report preparation requires a parent without whole-clock cuts')
    def source(name,kind):
        found=[p for k,p in assigned if k==kind and p.name==name]
        if len(found)!=1:raise ValueError('Expected one assigned '+name)
        return found[0]
    video=source('gpu_videoout_async.vhd','VHDL_FILE');timer=source('timer.vhd','VHDL_FILE')
    if digest(video)!=expected_video_sha256 or digest(timer)!=expected_timer_sha256:
        raise ValueError('Copied report/timer sources do not match expected identities')
    fifo=source('dual_clock_fifo.sv','SYSTEMVERILOG_FILE')
    if digest(fifo)!=digest(ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv'):
        raise ValueError('Unknown report FIFO implementation')
    if any(p.name=='psx_video_reports_cdc.sv' for _,p in assigned):raise ValueError('Report channel already assigned')
    new_video,patches=adapt_videoout(video.read_text());new_timer=adapt_timer(timer.read_text())
    for p in (video,timer):
        if q.count('"'+str(p)+'"')!=1:raise ValueError('Expected quoted assigned source: '+str(p))
    work=directory/'video-reports-cdc'
    if work.exists():raise ValueError('Fresh report adaptation output required')
    before={str(p):digest(p) for p in (video,timer,qsf,directory/'pocket.sdc',fifo)}
    work.mkdir();out_video=work/video.name;out_timer=work/timer.name
    out_video.write_text(new_video);out_timer.write_text(new_timer)
    q=q.replace('"'+str(video)+'"','"'+str(out_video)+'"').replace('"'+str(timer)+'"','"'+str(out_timer)+'"')
    added=[]
    for p,kind in [(ROOT/'rtl/pocket/psx_video_reports_cdc.sv','SYSTEMVERILOG_FILE'),(ROOT/'scripts/pocket_video_reports_scope.tcl','SDC_FILE')]:
        out=work/p.name;shutil.copyfile(p,out);q+=f'\nset_global_assignment -name {kind} "{out}"'+(' -library work' if kind!='SDC_FILE' else '')+'\n'
        added.append(dict(path=str(out),sha256=digest(out),original=str(p)))
    shutil.copyfile(UPSTREAM/'LICENSE',work/'COPYING.GPL2')
    if not all(digest(Path(p))==sha for p,sha in before.items()):raise ValueError('Parent changed during report preparation')
    qsf.write_text(q)
    report=dict(video=dict(original=str(video),original_sha256=expected_video_sha256,path=str(out_video),sha256=digest(out_video)),
        timer=dict(original=str(timer),original_sha256=expected_timer_sha256,path=str(out_timer),sha256=digest(out_timer)),
        added=added,source_restoration_patches=patches,only_timer_change=[EDGE,STROBE],
        source_snapshot_latency_added=True,source_levels_can_coalesce=False,ticks_are_strobes=True,
        source_rate_requirement='Dot spacing>=4 and non-dot level spacing>=3 video edges, plus separately tested finite bursts',
        fit_verified=False,timing_verified=False,hardware_verified=False,production_selected=False,
        limitations=['Real source event rate/bursts and tick/blank order require integration tests',
            'FIFO adds delivery latency; no original-cycle-equivalence claim',
            'Common reset discards queued events; startup events before both sides are online are not accepted',
            'Source overflow is a sticky failure, not backpressure or silent dropping',
            'Strobe consumer is mandatory; do not combine the bridge with the original edge-detecting timer'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-video-sha256',required=True);p.add_argument('--expected-timer-sha256',required=True)
    a=p.parse_args();print(json.dumps(prepare_project(a.project,a.expected_video_sha256,a.expected_timer_sha256),indent=2))
