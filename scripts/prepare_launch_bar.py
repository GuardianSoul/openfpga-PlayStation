"""prepare --launch-bar: a loading bar for the whole launch (docs/pocket-launch-timing.md).

The Pocket draws its own bar only while the Chip32 loader's loadf runs (the
512 KiB BIOS, 0.4 s), but a game takes about 7.5 s from its pick to the
BIOS's first picture (launch timing probe, Ape Escape 2026-10-22: bar
0.84-1.25 s, Reset Exit 1.76 s, first picture 7.48 s). Most of that is
after Reset Exit, on a black screen: the cue mount and the BIOS start-up.

This draws a bar like the Pocket's own (chosen 2026-10-22): the
full width of the picture along its bottom lines, white for the part done
and dark grey for the rest, from Reset Exit until the BIOS's first picture
(the PSX video's first nonzero pixel) or its first video mode change
(hResMode/interlace), whichever comes first: the BIOS sets its 640x480i
mode just before the Sony screen, and a bar placed for the old mode would
show for a frame in the wrong place. The frame being sent is measured as it
goes out (the last line's width W, the last frame's line count H): the bar
is the last 4 lines (8 on frames of 400 lines or more). It fills from
empty to full over the BIOS start-up (0-100 %), timed by video frames
from the CPU release, when the PSX video timing starts: 5.26 s to the first
picture (315 NTSC frames, 1.625 units of 512 a frame), or about 3.3 s with
Fast BIOS Boot (198 frames, 2.58 a frame; 10.31 -> 8.36 s on hardware). It
is drawn for 640-pixel lines (1.25 pixels a unit), the width before the BIOS
sets its mode; the dark track runs to the end of whatever line is sent.
Only active pixels change (RGB stays 0 outside DE for the APF scaler).
"""
from pathlib import Path
import re

OVERLAY = """    // --launch-bar: a bar like the Pocket's own along the bottom of the picture,
    // from Reset Exit until the BIOS's first picture or video mode change,
    // filling 0..512 over the BIOS start-up: 315 NTSC frames from the CPU
    // release (1.625 a frame), or 198 with Fast BIOS Boot (2.58 a frame);
    // drawn 1.25 pixels a unit (640 across), the dark track to the line's end.
    (* async_reg="true" *) reg [1:0] lb_run_m=2'b00, lb_fast_m=2'b00;
    reg lb_vs_q=1'b0, lb_de_q=1'b0, lb_done=1'b0, lb_ref_ok=1'b0;
    reg [8:0] lb_frames=0; reg [9:0] lb_x=0, lb_y=0, lb_top=10'h3ff; reg [3:0] lb_mode_ref=0;
    wire [3:0] lb_mode={video_hResMode,video_interlace};
    wire [11:0] lb_f={3'b0,lb_frames};
    wire [11:0] lb_fill=lb_fast_m[1] ? (lb_f<<1)+(lb_f>>1)+(lb_f>>4)+(lb_f>>6) : lb_f+(lb_f>>1)+(lb_f>>3);
    always @(posedge video_rgb_clock) begin
        lb_run_m<={lb_run_m[0],reset_n}; lb_fast_m<={lb_fast_m[0],__FAST__};
        lb_vs_q<=apf_vs; lb_de_q<=apf_de;
        if (apf_vs && !lb_vs_q) begin
            lb_y<=0;
            lb_top<=lb_y-(lb_y>=10'd400 ? 10'd8 : 10'd4);   // the bar: the frame's last 4 (8) lines
            if (!(&lb_frames)) lb_frames<=lb_frames+1'b1;
            if (!lb_ref_ok) begin lb_ref_ok<=1'b1; lb_mode_ref<=lb_mode; end
            else if (lb_mode!=lb_mode_ref) lb_done<=1'b1;   // the BIOS set its video mode
        end else if (lb_de_q && !apf_de) lb_y<=lb_y+1'b1;
        lb_x<=apf_de ? lb_x+1'b1 : 10'd0;
        if (apf_de && |apf_rgb) lb_done<=1'b1;   // the BIOS's first picture
    end
    wire lb_on=lb_run_m[1] && !lb_done && lb_ref_ok && apf_de && lb_y>=lb_top;
    wire [11:0] lb_fpx=lb_fill+(lb_fill>>2);   // x 1.25
    assign video_skip=apf_skip, video_rgb=!lb_on ? apf_rgb : {2'b0,lb_x}<lb_fpx ? 24'hffffff : 24'h404040;
"""
SDC = ('\n# --launch-bar: Reset Exit and Fast BIOS Boot reach the video clock through two registers.\n'
       'set_false_path -to [get_registers {*|lb_run_m[0] *|lb_fast_m[0]}]\n')


def patch_core_top(text):
    old = '    assign video_skip=apf_skip, video_rgb=apf_rgb;\n'
    if text.count(old) != 1: raise ValueError('--launch-bar: core_top video assign not found')
    # the fill rate follows Fast BIOS Boot when --fast-bios is in the project
    fast = 'fast_bios_set' if 'reg fast_bios_set' in text else "1'b0"
    return text.replace(old, OVERLAY.replace('__FAST__', fast))


def prepare_project(out):
    out = Path(out).resolve(); q = (out/'apf_top.qsf').read_text()
    core = re.findall(r'"([^"]*/core_top\.sv)"', q)
    if len(core) != 1: raise ValueError('--launch-bar: expected one core_top in the project')
    Path(core[0]).write_text(patch_core_top(Path(core[0]).read_text()))
    sdc = out/'pocket.sdc'; sdc.write_text(sdc.read_text() + SDC)


if __name__ == '__main__':
    import sys
    prepare_project(sys.argv[1])
