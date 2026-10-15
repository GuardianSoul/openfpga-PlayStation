#!/usr/bin/env python3
"""Explicit, unqualified APF output-pin budgets in an identity-checked copy.

No exceptions or clock changes. Receiver setup/hold and PCB flight are caller
assumptions, never board specifications. Optional falling-edge audio pads add
half an MCLK to both I2S wires; the original encoder body is preserved.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments,scoped_hold_text
from prepare_resource_targets import digest
from tool_paths import ROOT

DDIO_SHA='73674edb40a6314f9b00829942bedcad9e183515bcdbb327095f96401799c536'
I2S_SHA='2fba56c3973c412593e9a5c701ae01a14c3098be07601d4aaa26d249467b1540'
AUDIO_WRAPPER='''
// APF_FALLING_AUDIO_PADS: keep the raw I2S stream, move both external wires
// together by half an MCLK. Every rising-MCLK receiver then has an aperture.
module psx_audio_i2s (
    input wire audio_clk, reset,
    input wire [31:0] frame,
    output reg audio_lrck=0, audio_dac=0
);
    wire raw_lrck,raw_dac;
    psx_audio_i2s_raw raw (.audio_clk(audio_clk),.reset(reset),.frame(frame),
        .audio_lrck(raw_lrck),.audio_dac(raw_dac));
    always @(negedge audio_clk) begin
        if(reset) begin audio_lrck<=0;audio_dac<=0;end
        else begin audio_lrck<=raw_lrck;audio_dac<=raw_dac;end
    end
endmodule
'''


def falling_audio(text):
    if text.count('module psx_audio_i2s (')!=1 or 'APF_FALLING_AUDIO_PADS' in text:
        raise ValueError('Expected one original audio serializer')
    return text.replace('module psx_audio_i2s (','module psx_audio_i2s_raw (',1)+AUDIO_WRAPPER


def compact(text):return re.sub(r'\s+','',text)


def framework_contract(text):
    blocks=re.findall(r'mf_ddio_bidir_12\s+(\w+)\s*\((.*?)\);',text,re.S)
    expected={
        'isco':".oe(1'b1),.datain_h(video_rgb[23:12]),.datain_l(video_rgb[11:0]),.outclock(video_rgb_clock),.padio(scal_ddio_12)",
        'iscc':".oe(1'b1),.datain_h({video_vs,video_hs,video_de,video_skip}),.datain_l({video_vs,video_hs,video_de,video_skip}),.outclock(video_rgb_clock),.padio(scal_ddio_ctrl)",
        'isclk':".oe(1'b1),.datain_h(1'b1),.datain_l(1'b0),.outclock(video_rgb_clock_90),.padio(scal_clk)"}
    if len(blocks)!=3 or {n:compact(b) for n,b in blocks}!=expected:
        raise ValueError('Unknown APF DDIO data/clock wiring')
    tokens=['assignscal_vid=scal_ddio_12;']+[
        f'assignscal_{name}=scal_ddio_ctrl[{bit}];' for bit,name in enumerate(('skip','de','hs','vs'))]
    tokens += [f'.audio_{name}(scal_aud{name})' for name in ('mclk','lrck','dac')]
    body=compact(text)
    if any(body.count(t)!=1 for t in tokens):raise ValueError('Unknown APF video/audio pad mapping')


def core_contract(text):
    plls=re.findall(r'altera_pll\s*#\s*\((.*?)\)\s+(\w+)\s*\((.*?)\);',text,re.S)
    video=[(params,ports) for params,name,ports in plls if name=='video_pll']
    if len(video)!=1:raise ValueError('Expected one video PLL')
    params,ports=map(compact,video[0])
    tokens=['.output_clock_frequency0("53.693166MHz")','.phase_shift0("0ps")',
        '.output_clock_frequency1("53.693166MHz")','.phase_shift1("4656ps")',
        '.number_of_clocks(2)','.duty_cycle0(50)','.duty_cycle1(50)']
    if any(params.count(t)!=1 for t in tokens):raise ValueError('Unknown APF video PLL parameters')
    if any(ports.count(t)!=1 for t in ('.outclk({video_rgb_clock_90,video_rgb_clock})','.refclk(clk_74a)')):
        raise ValueError('Unknown APF video PLL wiring')
    body=compact(text)
    if any(body.count(t)!=1 for t in ('.outclk_0(audio_mclk)','.audio_clk(audio_mclk)')):
        raise ValueError('Unknown APF source serializer wiring')


def prepare_project(directory,expected_core_sha256,expected_apf_sha256,*,video_setup_ns,
                    video_hold_ns,audio_setup_ns,audio_hold_ns,board_min_ns=0.0,
                    board_max_ns=0.5,falling_audio_pads=False):
    values=(video_setup_ns,video_hold_ns,audio_setup_ns,audio_hold_ns,board_min_ns,board_max_ns)
    if not all(math.isfinite(v) and 0<=v<=100 for v in values) or not board_min_ns<=board_max_ns<=2:
        raise ValueError('Finite nonnegative receiver budgets and 0<=board_min<=board_max<=2 required')
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';q=qsf.read_text()
    assigned=assignments(directory,q)
    def one(name):
        candidates=[p for kind,p in assigned if kind in ('VERILOG_FILE','SYSTEMVERILOG_FILE') and p.name==name]
        if len(candidates)!=1:raise ValueError('Expected one assigned '+name)
        return candidates[0]
    core=one('core_top.sv');apf=one('apf_top.v');ddio=one('mf_ddio_bidir_12.v');audio=one('psx_audio_i2s.sv')
    if (digest(core)!=expected_core_sha256 or digest(apf)!=expected_apf_sha256 or
            digest(ddio)!=DDIO_SHA or digest(audio)!=I2S_SHA):
        raise ValueError('APF/core/serializer/DDIO identity mismatch')
    framework_contract(apf.read_text());core_contract(core.read_text())
    _,removed=scoped_hold_text((directory/'pocket.sdc').read_text())
    if removed:raise ValueError('APF pin preparation requires no whole-clock cuts')
    pins=['scal_clk','scal_vs','scal_hs','scal_de','scal_skip','scal_audmclk','scal_auddac','scal_audlrck']+[f'scal_vid[{i}]' for i in range(12)]
    for pin in pins:
        if len(re.findall(r'^set_instance_assignment -name IO_STANDARD "1.8 V" -to '+re.escape(pin)+r'\s*$',q,re.M))!=1:
            raise ValueError('Expected one 1.8V APF pin assignment: '+pin)
    work=directory/'apf-pin-timing'
    if work.exists() or 'pocket_apf_video_pin' in q:raise ValueError('APF pin timing already prepared')
    token='"'+str(audio)+'"'
    if falling_audio_pads and q.count(token)!=1:raise ValueError('Expected one quoted serializer assignment')
    protected={str(p):digest(p) for p in (core,apf,ddio,audio,qsf,directory/'pocket.sdc')}
    new_audio=falling_audio(audio.read_text()) if falling_audio_pads else None
    work.mkdir();scope=work/'scope.tcl';shutil.copyfile(ROOT/'scripts/pocket_apf_pin_scope.tcl',scope)
    config=work/'pocket_apf_pin_timing.sdc'
    options=dict(video_setup=video_setup_ns,video_hold=video_hold_ns,audio_setup=audio_setup_ns,
        audio_hold=audio_hold_ns,board_min=board_min_ns,board_max=board_max_ns)
    config.write_text('# DIAGNOSTIC ASSUMPTIONS ONLY; external receiver/PCB/load qualification remains open.\n'+
        ''.join(f'set apf_pin_{key} {value:.6f}\n' for key,value in options.items())+
        f'set apf_pin_falling_audio {int(falling_audio_pads)}\nsource "{scope}"\n')
    files=[scope,config]
    if new_audio is not None:
        output=work/audio.name;output.write_text(new_audio);files.append(output)
        q=q.replace(token,'"'+str(output)+'"')
    assert all(digest(Path(p))==h for p,h in protected.items())
    q+=f'\nset_global_assignment -name SDC_FILE "{config}"\n';qsf.write_text(q)
    report=dict(original=protected,preparer_sha256=digest(Path(__file__)),assumed_requirements_ns=options,analysis_only=True,
        external_electrical_requirements_qualified=False,board_loads_qualified=False,
        video_receiver='Both DDR clock edges, propagated quarter-phase pad clock',
        audio_receiver='Conservative every rising forwarded MCLK; no favorable SCLK phase or multicycle',
        falling_audio_pads=falling_audio_pads,raw_encoder_body_unchanged=True,
        half_MCLK_output_latency_added=falling_audio_pads,additional_exceptions=False,
        added=[dict(path=str(p),sha256=digest(p)) for p in files],
        fit_verified=False,timing_verified=False,hardware_verified=False,production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-core-sha256',required=True);p.add_argument('--expected-apf-sha256',required=True)
    for name in ('video-setup','video-hold','audio-setup','audio-hold'):p.add_argument('--'+name+'-ns',required=True,type=float)
    p.add_argument('--board-min-ns',type=float,default=0.0);p.add_argument('--board-max-ns',type=float,default=0.5)
    p.add_argument('--falling-audio-pads',action='store_true');a=p.parse_args()
    print(json.dumps(prepare_project(a.project,a.expected_core_sha256,a.expected_apf_sha256,
        video_setup_ns=a.video_setup_ns,video_hold_ns=a.video_hold_ns,audio_setup_ns=a.audio_setup_ns,
        audio_hold_ns=a.audio_hold_ns,board_min_ns=a.board_min_ns,board_max_ns=a.board_max_ns,
        falling_audio_pads=a.falling_audio_pads),indent=2))
