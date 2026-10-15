#!/usr/bin/env python3
"""Execute the full-top APF scaler adapter against pulse/blanking requirements."""
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_full_pocket import APF_VIDEO_ADAPTER
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    parent=ROOT/'build/sim/apf-video';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    bench=ROOT/'sim/pocket/tb_apf_video.sv'
    protected={str(p):digest(p) for p in (bench,Path(__file__),ROOT/'scripts/prepare_full_pocket.py')}
    header='''module apf_video_fixture(input video_rgb_clock,hsync,vsync,hblank,psx_vblank,video_ce,
        input [7:0] video_r,video_g,video_b,
        output video_skip,video_de,video_hs,video_vs,output [23:0] video_rgb);
'''
    def run(name,text):
        source=work/(name+'.sv');source.write_text(header+text+'endmodule\n')
        binary=work/(name+'.vvp')
        subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_apf_video','-o',str(binary),str(source),str(bench)],check=True,timeout=60)
        result=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=60)
        output=result.stdout+result.stderr;log=work/(name+'.log');log.write_text(output)
        print(name,output,flush=True)
        return dict(exit_code=result.returncode,output=output,source_sha256=digest(source),binary_sha256=digest(binary),log_sha256=digest(log))
    positive=run('original',APF_VIDEO_ADAPTER);faults=[]
    for name,before,after,marker in (
        ('level-sync','apf_hs<=hsync && !apf_hs_d','apf_hs<=hsync','APF sync must pulse once'),
        ('blank-rgb',"apf_rgb<=(!hblank && !psx_vblank) ? {video_r,video_g,video_b} : 24'h0",
            'apf_rgb<={video_r,video_g,video_b}','APF RGB must be black'),
        ('global-skip','apf_skip<=!hblank && !psx_vblank && !video_ce','apf_skip<=!video_ce','APF skip must stay inside')):
        if APF_VIDEO_ADAPTER.count(before)!=1:raise ValueError('APF fault anchor changed')
        result=run(name,APF_VIDEO_ADAPTER.replace(before,after));result.update(name=name,rejected=result['exit_code']!=0 and marker in result['output']);faults.append(result)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    report=dict(passed=identity and positive['exit_code']==0 and 'PASS APF VIDEO' in positive['output'] and all(f['rejected'] for f in faults),
        source_identity_verified=identity,source_sha256=protected,positive=positive,faults=faults,
        hardware_verified=False,timing_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
