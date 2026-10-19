#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cycle compare the exact reviewed video source and the private-copy proposal."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
from prepare_video_368_budget import video_368_budget_text
from tool_paths import find_tool

BENCH = r'''
module tb;
parameter PROBE=0;
reg clk=0; always #5 clk=~clk;
reg ce=0,hsync=0,vsync=0,hblank=1,vblank=1,interlace=0,blank=0,weave=0,field_swap=0,stretch=0;
reg [23:0] rgb_in=0;
wire hs0,vs0,de0,skip0,hs1,vs1,de1,skip1;
wire [23:0] rgb0,rgb1;
video_ref #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.VIDEO_PROBE(PROBE),.SLOT368(1)) a
(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(interlace),.blank(blank),.weave(weave),.field_swap(field_swap),.stretch(stretch),.rgb_in(rgb_in),.hs(hs0),.vs(vs0),.de(de0),.skip(skip0),.rgb(rgb0));
video_dut #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.VIDEO_PROBE(PROBE),.SLOT368(1)) b
(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(interlace),.blank(blank),.weave(weave),.field_swap(field_swap),.stretch(stretch),.rgb_in(rgb_in),.hs(hs1),.vs(vs1),.de(de1),.skip(skip1),.rgb(rgb1));
integer cycles=0,slot6=0,frame,line,x,w,period,n,seed=32'h368045;
reg [31:0] rnd=32'h368045;
task step;
begin
 @(posedge clk); #1;
 cycles=cycles+1;
 if ({hs0,vs0,de0,skip0,rgb0} !== {hs1,vs1,de1,skip1,rgb1}) $fatal(1,"public mismatch cycle %0d",cycles);
 if ({a.c368,a.w_next,a.slot_next,a.lead,a.s,a.out_w,a.native,a.in_pic} !== {b.c368,b.w_next,b.slot_next,b.lead,b.s,b.out_w,b.native,b.in_pic}) $fatal(1,"state mismatch cycle %0d",cycles);
 if (a.slot==6 && a.rd_on) slot6=slot6+1;
 @(negedge clk);
end endtask
initial begin
 for(frame=0;frame<6;frame=frame+1) begin
  vblank=1;vsync=1;hsync=1;repeat(12) step();vsync=0;hsync=0;repeat(900) step();
  weave=frame==3;interlace=weave;field_swap=frame[0];
  for(line=0;line<300;line=line+1) begin
   stretch=(frame==2 || frame==4) && line[0];
   case(line%18)
    0:w=0;1:w=1;2:w=240;3:w=256;4:w=299;5:w=300;6:w=310;7:w=320;8:w=321;
    9:w=330;10:w=360;11:w=367;12:w=368;13:w=369;14:w=384;15:w=512;16:w=640;default:w=1023;
   endcase
   case(line%7)
    0:period=4;1:period=5;2:period=7;3:period=8;4:period=10;5:period=20;default:period=1;
   endcase
   hblank=1;vblank=0;hsync=1;ce=0;repeat(16)step();hsync=0;repeat(820)step();
   hblank=0;
   for(x=0;x<w*period;x=x+1)begin
    ce=(x%period)==0 && !(line%19==0 && x%97<17);
    rnd=rnd ^ (rnd<<13);rnd=rnd ^ (rnd>>17);rnd=rnd ^ (rnd<<5);rgb_in=rnd;blank=rnd[31:28]==0;
    step();
   end
   // Long/short CE gaps and pauses cross line boundaries; no reset is invented.
   hblank=1;ce=0;repeat(line%23)step();
  end
 end
 vblank=1;hblank=1;ce=0;repeat(2000)step();
 if(slot6==0)$fatal(1,"368 native slot not covered");
 $display("PASS cycles=%0d slot6_read_cycles=%0d",cycles,slot6);$finish;
end
endmodule
'''

def prove_encoding():
    """Exhaustive one-step induction under the explicitly stated state relation."""
    transitions = 0
    for c, p, line, active, prior_active, ce in itertools.product(
            range(16), range(16), range(16), range(2), range(2), range(2)):
        a, b, d = c, p, line
        x, y, z = min(c, 7), p == 7, line == 7
        if active:
            if ce:
                a, b, x, y = 0, (c + 1) & 15, 0, x == 6
            else:
                a, x = min(c + 1, 15), min(x + 1, 7)
        if not active and prior_active:
            d, z = p, p == 7
        assert (x, y, z) == (min(a, 7), b == 7, d == 7)
        transitions += 1
    combinations = 0
    for width, over320, double, use368, selected in itertools.product(
            range(1024), range(2), range(2), range(2), [256, 320, 368, 512, 640]):
        # These implications are literal assignments in the reviewed source.
        if use368 and (double or selected != 368):
            continue
        old = ((0 if width > 368 else ((368 - width) & 1023) >> 1)
               if use368 else 0 if over320 else (320 - width) & 1023
               if double else ((selected - width) & 1023) >> 1)
        new = (0 if (width > 368 if use368 else over320)
               else (320 - width) & 1023 if double
               else ((selected - width) & 1023) >> 1)
        assert old == new
        combinations += 1
    return dict(tracker_transitions=transitions, centering_combinations=combinations,
                initial_relation=True, passed=True)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('reference',type=Path)
    ap.add_argument('output',type=Path)
    ap.add_argument('--faults-only',action='store_true')
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    src=args.reference.read_text();new=video_368_budget_text(src)
    (args.output/'reference.sv').write_text(src.replace('module psx_apf_video_wide','module video_ref'))
    (args.output/'tb.sv').write_text(BENCH)
    cases=[('positive',new,0),('probe',new,1),('fault-period',new.replace("cec==3'd6","cec==3'd5"),0),('fault-center',new.replace('use_368 ? over_368 : over_320','use_368 ? !over_368 : over_320'),0)]
    if args.faults_only:
        cases=cases[2:]
    report={'bench_sha256':hashlib.sha256(BENCH.encode()).hexdigest(),'encoding_proof':prove_encoding(),'reference_sha256':hashlib.sha256(src.encode()).hexdigest(),'candidate_sha256':hashlib.sha256(new.encode()).hexdigest(),'cases':{}}
    for name,text,probe in cases:
        p=args.output/name;p.mkdir()
        bench=BENCH
        if name.startswith('fault-'):
            bench='\n'.join(line for line in BENCH.split('\n') if '$fatal(1,"state mismatch cycle' not in line)
        (p/'tb.sv').write_text(bench)
        (p/'dut.sv').write_text(text.replace('module psx_apf_video_wide','module video_dut'))
        command=[find_tool('iverilog'),'-g2012','-s','tb',f'-Ptb.PROBE={probe}','-o',str(p/'sim'),str(args.output/'reference.sv'),str(p/'dut.sv'),str(p/'tb.sv')]
        subprocess.run(command,check=True,capture_output=True)
        r=subprocess.run([find_tool('vvp'),str(p/'sim')],capture_output=True,text=True,timeout=300)
        (p/'result.log').write_text(r.stdout+r.stderr)
        ok=r.returncode==0 if name in ('positive','probe') else r.returncode!=0 and 'public mismatch cycle' in r.stdout
        report['cases'][name]={'passed':ok,'returncode':r.returncode,'output':r.stdout+r.stderr}
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(name,ok,r.stdout.strip(),flush=True)
        if not ok:raise SystemExit(1)

if __name__=='__main__':main()
