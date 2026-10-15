#!/usr/bin/env python3
"""Opt-in ordered video request snapshots in a copied Pocket physical top.

Every record is a real source-clock tuple. No coalescing or guest register/
rendering change. FIFO delivery/startup adds latency, measured separately.
Reports/dotclock remain unmodified and unreviewed paths stay timed.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments, scoped_hold_text
from prepare_resource_targets import UPSTREAM, digest
from tool_paths import ROOT


DECL = '''
   -- POCKET_REQUEST_CDC_DECL_BEGIN
   component psx_video_request_cdc is
      port (
         src_clk,dst_clk,reset_async : in std_logic;
         src_data : in std_logic_vector(38 downto 0);
         src_ready,src_fault : out std_logic;
         dst_data : out std_logic_vector(38 downto 0);
         dst_initialized,dst_fault : out std_logic
      );
   end component;
   signal request_input,request_output : std_logic_vector(38 downto 0);
   signal request_initialized,request_fault : std_logic;
   signal request_reset : std_logic := '1';
   -- POCKET_REQUEST_CDC_DECL_END
'''
STATEMENTS = '''
   -- POCKET_REQUEST_CDC_BODY_BEGIN
   -- Register the common reset in its original CPU domain; never CE-gate it.
   process(clk1x) begin
      if rising_edge(clk1x) then request_reset <= reset_1x; end if;
   end process;
   request_input <= videoout_request.fetch & std_logic_vector(videoout_request.fetchsize) &
      std_logic_vector(videoout_request.lineInNext) &
      std_logic_vector(to_unsigned(videoout_request.xpos,10)) & std_logic_vector(videoout_request.lineDisp);
   video_request : psx_video_request_cdc port map (
      src_clk=>clkvid,dst_clk=>clk2x,reset_async=>request_reset,
      src_data=>request_input,src_ready=>open,src_fault=>open,
      dst_data=>request_output,dst_initialized=>request_initialized,dst_fault=>request_fault);
   -- Undefined FIFO data before initialization must never launch a fetch.
   videoout_request_2x.fetch <= request_output(38) when request_initialized='1' and request_fault='0' else '0';
   videoout_request_2x.fetchsize <= unsigned(request_output(37 downto 28)) when request_initialized='1' else (others=>'0');
   videoout_request_2x.lineInNext <= unsigned(request_output(27 downto 19)) when request_initialized='1' else (others=>'0');
   videoout_request_2x.xpos <= to_integer(unsigned(request_output(18 downto 9))) when request_initialized='1' else 0;
   videoout_request_2x.lineDisp <= unsigned(request_output(8 downto 0)) when request_initialized='1' else (others=>'0');
   -- POCKET_REQUEST_CDC_BODY_END
'''


def adapt_videoout(text):
    if 'POCKET_REQUEST_CDC_' in text:
        raise ValueError('Request CDC already present')
    # These attributes would otherwise refer to stages removed by this option.
    # Retain FF attributes for the untouched report/unpause sampling chains.
    text = re.sub(r'^\s*attribute altera_attribute of videoout_request_(?:s1|s2|2x) : signal is "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF";\n', '', text, flags=re.M)
    for stage in ('s1', 's2'):
        pattern = r'^   signal videoout_request_'+stage+r'\s+: tvideoout_request :=[^\n]+\n'
        text, count = re.subn(pattern, '', text, flags=re.M)
        if count != 1:
            raise ValueError('Missing original request stage '+stage)
    pattern = r'   process \(clk2x\)\n   begin\n      if rising_edge\(clk2x\) then\s*videoout_request_s1\s*<= videoout_request;\s*videoout_request_s2\s*<= videoout_request_s1;\s*videoout_request_2x\s*<= videoout_request_s2;\s*end if;\s*end process;'
    text, count = re.subn(pattern, STATEMENTS.rstrip(), text)
    if count != 1:
        raise ValueError('Missing exact three-stage request sampling process')
    marker = 'architecture arch of gpu_videoout_async is'
    if text.count(marker) != 1:
        raise ValueError('Unknown video source architecture')
    extra = ''
    for name in ('preserve', 'dont_merge'):
        if not re.search(r'attribute '+name+r'\s*:\s*boolean;', text):
            extra += f'   attribute {name} : boolean;\n'
        extra += f'   attribute {name} of request_reset : signal is true;\n'
    starts = list(re.finditer(r'^begin[ \t]*\n', text, re.M))
    if len(starts)!=1:
        raise ValueError('Unknown video architecture begin')
    at = starts[0].start()
    return text[:at]+DECL+extra+text[at:]


def prepare_project(directory):
    directory = Path(directory).resolve()
    qsf = directory/'apf_top.qsf'; sdc = directory/'pocket.sdc'
    q = qsf.read_text(); scoped, removed = scoped_hold_text(sdc.read_text())
    candidates = [p for kind,p in assignments(directory,q) if kind=='VHDL_FILE' and
                  re.search(r'^\s*entity\s+gpu_videoout_async\s+is\b', p.read_text(), re.M|re.I)]
    if len(candidates) != 1:
        raise ValueError('Expected one assigned asynchronous video engine')
    original = candidates[0]; before = original.read_text(); sha = digest(original)
    after = adapt_videoout(before)
    token = '"'+str(original)+'"'
    if q.count(token) != 1:
        raise ValueError('Expected one quoted absolute video assignment')
    work = directory/'video-request-cdc'
    if work.exists():
        raise ValueError('Request CDC output already exists')
    dependencies = [ROOT/'rtl/pocket/psx_video_request_cdc.sv', ROOT/'rtl/pocket/psx_video_config_cdc.sv',
                    ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv']
    reused = []; missing = []
    for path in dependencies:
        existing = [p for kind,p in assignments(directory,q) if p.name==path.name]
        if existing:
            if len(existing)!=1 or digest(existing[0])!=digest(path):
                raise ValueError('Conflicting existing request dependency '+path.name)
            reused.append(dict(path=str(existing[0]),sha256=digest(existing[0])))
        else:
            missing.append(path)
    work.mkdir(); output = work/'gpu_videoout_async.vhd'; output.write_text(after)
    shutil.copyfile(UPSTREAM/'LICENSE',work/'COPYING.GPL2')
    for path in missing:
        copy = work/path.name; shutil.copyfile(path,copy)
        q += f'\nset_global_assignment -name SYSTEMVERILOG_FILE "{copy}" -library work\n'
    constraint = work/'pocket_video_request_scope.sdc'
    shutil.copyfile(ROOT/'scripts/pocket_video_request_scope.tcl',constraint)
    q += f'\nset_global_assignment -name SDC_FILE "{constraint}"\n'
    qsf.write_text(q.replace(token,'"'+str(output)+'"'))
    sdc.write_text(scoped)
    if digest(original) != sha:
        raise ValueError('Video source changed while preparing')
    report = dict(original=str(original),original_sha256=sha,path=str(output),sha256=digest(output),
                  preparer_sha256=digest(Path(__file__)),reused=reused,packet_bits=39,depth=16,
                  added_latency=True,coalescing=False,reset_independent_of_CE=True,
                  removed_known_clock_cuts=removed,fit_verified=False,timing_verified=False,
                  hardware_verified=False,game_verified=False,
                  limitations=['Destination clock must continuously drain faster than source',
                               'Stopped destination can latch fault; common reset required',
                               'Fetch disabled before initialization and after synchronized source fault',
                               'Does not solve report/dotclock CDC or all board timing'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    print(json.dumps(prepare_project(p.parse_args().project),indent=2))
