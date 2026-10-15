#!/usr/bin/env python3
"""Fit FIFO/rate/FIR/I2S on Pocket's device; audit exact CDC endpoints.

Native source/audio clocks use real pins. Stream/control/I2S virtual ports
have no board delay model. Excludes PLLs, SPU, APF and other core hardware.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from build_pocket import source
from generate_pocket_audio_coeffs import generate
from prepare_pocket_audio_stream import audio_sdc
from prepare_resource_targets import apply_compile_profile, digest, write_project
from run_resource_fit import fit_target
from tool_paths import ROOT,find_tool
from verify_pocket_audio_rom import verify as verify_rom


def main():
    parent=ROOT/'build/resources/pocket-audio-bridge/runs';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent))
    project=work/'project';project.mkdir();coefficients=generate(work/'coefficients')
    sources=[]
    for name in ('psx_audio_sinc.sv','psx_audio_rate.sv','psx_audio_bridge.sv','psx_audio_i2s.sv'):
        copied=project/name;copied.write_bytes((ROOT/'rtl/audio'/name).read_bytes());sources.append(source(copied))
    top='psx_audio_bridge_resource';wrapper=project/(top+'.sv')
    wrapper.write_text('''module psx_audio_bridge_resource (
    input wire src_clk, audio_clk, reset_async, src_running, src_valid,
    input wire [31:0] src_frame,
    output wire src_full, src_overflow, frame_tick, fault,
    output wire [31:0] output_frame,
    output wire [6:0] buffered_frames,
    output wire [7:0] status,
    output wire signed [15:0] rate_correction,
    output wire audio_lrck, audio_dac
);
    psx_audio_bridge #(.COEFFICIENT_FILE("'''+coefficients['compact_path']+'''")) audio_stream (.*);
endmodule
''');sources.append(source(wrapper))
    specs=[('src_clk','in',1),('audio_clk','in',1),('reset_async','in',1),('src_running','in',1),('src_valid','in',1),
           ('src_frame','in',32),('src_full','out',1),('src_overflow','out',1),('frame_tick','out',1),('fault','out',1),
           ('output_frame','out',32),('buffered_frames','out',7),('status','out',8),('rate_correction','out',16),('audio_lrck','out',1),('audio_dac','out',1)]
    ports=[(n,d,'std_logic' if w==1 else f'std_logic_vector({w-1} downto 0)') for n,d,w in specs]
    sdc=['create_clock -name spu_clk -period 29.525100 [get_ports src_clk]',
         'create_clock -name audio_mclk -period 81.380208 [get_ports audio_clk]',
         'derive_clock_uncertainty',*audio_sdc()]
    hashes=write_project(project,top,ports,('src_clk','audio_clk'),sources,sdc)
    target=dict(top=top,directory=str(project),quartus_sources=sources,project_sha256=hashes,
                physical_clock_ports=['src_clk','audio_clk'],fixed_ports={},
                included_components=['Stereo async FIFO','147/160 phase + bounded drift servo','128-tap stereo FIR','MCLK I2S'],
                limitations=['Virtual stream/control/I2S interfaces; no board delays, PLLs, SPU or APF',
                             'Reset input must be registered/glitch-free; full-system reset topology remains open'])
    manifest=apply_compile_profile(dict(device='5CEBA4F23C8',targets={'audio_bridge':target}),'area-min-state')
    manifest.update(coefficients=coefficients,sdc_preparer_sha256=digest(ROOT/'scripts/prepare_pocket_audio_stream.py'))
    (work/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(work,flush=True)
    binary=Path(find_tool('quartus_sh')).parent
    result=fit_target(binary,target,manifest['device'],1800)
    result['coefficient_identity_verified']=digest(Path(coefficients['compact_path']))==coefficients['compact_sha256']
    if result['fit_verified']:result['mapped_rom']=verify_rom(project,coefficients['compact_path'])
    if not result['coefficient_identity_verified']:result['fit_verified']=False
    if result['fit_verified']:
        audit_script=ROOT/'scripts/report_pocket_audio.tcl';before=digest(project/(top+'.qsf'))
        audit=subprocess.run([str(binary/'quartus_sta'),'-t',str(audit_script),str(work/'audit')],
                             cwd=project,capture_output=True,text=True,timeout=120)
        output=audit.stdout+audit.stderr;(work/'audit.log').write_text(output)
        scopes=re.findall(r'AUDIO_GRAY_SCOPE (\w+) (\d+) (\d+)',output)
        slacks=re.findall(r'Worst case slack is (-?\d+(?:\.\d+)?)',output)
        files=list((work/'audit').glob('*.txt'))
        net_rows=re.findall(r'^;\s*--\s*;\s*(-?\d+(?:\.\d+)?)\s*;\s*(\d+(?:\.\d+)?)\s*;\s*(\d+(?:\.\d+)?)',
                            (work/'audit/net-delay.txt').read_text(),re.M) if (work/'audit/net-delay.txt').exists() else []
        scope_ok=[s[0] for s in scopes]==['wr_gray','rd_gray'] and all(int(s[1])>=7 and s[2]=='7' for s in scopes)
        result['cdc_audit']={'passed':audit.returncode==0 and scope_ok
                            and len(files)==8 and bool(slacks) and all(float(s)>=0 for s in slacks)
                            and len(net_rows)==14 and all(float(row[0])>=0 for row in net_rows)
                            and digest(project/(top+'.qsf'))==before,
                            'exit_code':audit.returncode,'scopes':scopes,'slacks_ns':slacks,
                            'net_delay_rows':net_rows,
                            'files_sha256':{str(p):digest(p) for p in files},
                            'log_sha256':digest(work/'audit.log'),'script_sha256':digest(audit_script)}
    report=dict(**manifest,result=result,tool_version=subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True))
    (work/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('fit_verified','counts','tool_error_messages','reported_timing')}),flush=True)
    if not result['fit_verified'] or not result.get('cdc_audit',{}).get('passed'):raise SystemExit(1)


if __name__=='__main__':main()
