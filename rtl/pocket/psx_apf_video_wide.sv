// SPDX-License-Identifier: MIT
// PSX video to the APF scaler with every line stretched to 640 pixels, so
// the scaler slots only differ by height: 0 = 640x240, 1 = 640x480 (480i
// woven), 2 = 640x288 (PAL frames: more than 242 lines in the previous
// frame). APF allows eight slots, too few for every PSX width at every
// height (2026-10-16).
//
// Each line's CE pixels are written to one bank of a two-line buffer; after
// the line ends, the next HS end starts a 640-clock read of that bank with
// nearest-neighbour stretching (output pixel i shows source pixel
// floor(i*w/640), w = that line's width), DE high and skip low. A line is
// shown during the following line period. HS is the PSX hsync, VS the PSX
// vsync (VS at vblank shifted the picture, Video test, 2026-10-16). The
// first clock after each output line carries the APF end-of-line word
// "set scaler slot" ({8'b0, slot, 13'b0}). With INTERLACE_FEATURE, each VS
// carries the 480i frame feature bits as in psx_apf_video. blank sends black.
//
// HYBRID: common widths keep native slots so the Pocket's screen filters see
// real pixels; only the rest are stretched. Slots: 0 = 640x240, 1 = 320x240,
// 2 = 256x240, 3 = 640x480, 4 = 320x288, 5 = 256x288, 6 = 640x288,
// 7 = 512x240.
// LEAD_LINE: the next HS end after VS sends one black line (the previous
// line's slot and width) before the picture, since the APF scaler drops
// the first line of a frame (stray bottom row, as psx_apf_video LEAD_LINE).
// LEAD_LINE 2 and EVEN_LINES (from bhendo's fix for issue #5, 2026-10-21):
// the lead line goes out only in 480i. Progressive frames keep their first
// line now that VS and HS are apart (VS_ON_HS), so a lead line put the picture
// a row low and cost its last line; 480i fields still lose their first line.
// EVEN_LINES: every frame or field sends an even number of DE lines: after
// vblank starts, a black pad line (the previous line's slot and width) follows
// when the lines since VS are odd. With an odd count (the lead line and 240)
// the APF scaler leaves the last 128 pixels of a top row unwritten from
// power-on (the red line). A pad still pending at VS is dropped.
// WEAVE576 (with ASPECT, DBL320, SLOT368, FILL_DETECT, PAL288, CROP_LINES;
// 2026-10-22): PAL 480i is woven like NTSC, 288 lines a field, in a 640x576
// 4:3 slot (5) in Fit and lines 10..277 a field in a 640x536 10:9 slot (7)
// in Fill, so it neither jitters (bob) nor loses lines. Progressive Fill
// frames send every line twice (the copy half a line later, with its own HS)
// and share those slots: NTSC 224 -> 448 in slot 1 with 480i Fill, PAL 268 ->
// 536 in slot 7. Fill is a stretch to the whole screen, so a doubled line
// lands on the same panel rows; Fit and the native slots are unchanged.
// It goes out on every clock like the picture lines. CE-paced (0.5.5-test4
// and before), a lead line at 640 with CE slower than every 4 clocks (320,
// 368, 512 or 256 lines in Stretch, PAL, 368/512 in Fit) took up to 6,400
// clocks and stayed in DE across the next HS (a line is 3,413).
// A line within 1/16 below a native width (320/256, and 512 at 240 lines)
// is sent at that width, centred with black; a line of 321-360 pixels is
// centre-cropped to 320 (FFVII's title is a little over 320 and was
// stretched, 2026-10-16; only 320 to keep the area small). Every other
// line, and every 480i line, is stretched to 640.
// ASPECT (with HYBRID): the Fit / Stretch screen setting (stretch input,
// 2026-10-17). Slots 0-4 are 4:3 (Fit: black bars above and below on
// the Pocket's 10:9 screen) and 5-7 are 10:9 (Stretch: the whole screen):
// 0 = 640x240, 1 = 320x240, 2 = 256x240, 3 = 640x480, 4 = 640x288,
// 5 = 640x240, 6 = 640x480, 7 = 640x288. Fit keeps native 320/256 at 240
// lines; 512 and PAL lines are stretched to 640 (PAL 320 exactly doubled).
// Stretch sends every line at 640. A line sent at 640 that Fit would send
// at 320 (Stretch, or PAL) has each of its 320 pixels doubled, centred or
// cropped as at 320, so every PSX pixel is two whole output pixels (a
// 310- or 330-pixel line otherwise came out with uneven pixels).
// PAL_TIMING: PAL also needs a PAL frame (more than 288 HS per VS, PAL
// has 312/313 and NTSC 262/263), so an NTSC game showing a few more than
// 242 lines (FFVII's title) stays in the 240-line slots, cropped to 240,
// instead of the 288-line ones (uneven pixels in Fit, bars in Stretch,
// 0.5.5-test3).
// CROP_LINES: a field taller than its slot (240 lines per field, 288 for
// PAL) loses half the excess at the top and half at the bottom, as in
// psx_apf_video, from the previous field's line count (Wipeout's PAL 480i
// EU notice has 288-line fields).
// SLOT368 (with ASPECT): slot 6 is 368x240 at 4:3 instead of the Stretch
// 640x480 one. A Fit line whose pixel clock is the 368 one (CE every 7
// clocks; FFVII's menus, 0.5.5 probe 2026-10-18) is sent there natively,
// centred with black or centre-cropped to 368 (368 pixels at that clock
// are 4:3 wide, as 320 at CE 8), instead of stretched to 640 with uneven
// pixels. Such lines are never taken as 320/256 by width. Stretch 480i
// goes to the 4:3 640x480 slot.
// DBL320 (with SLOT368): slot 1 is the Stretch 640x480 one at 10:9
// instead of 320x240. Fit sends 320 lines in the 640 slots with each
// pixel exactly doubled (as Stretch does), so Stretch 480i stretches again.
// VIDEO_PROBE (diagnostic builds only): output lines 0-3 of a frame start
// with 10 blocks of 16 pixels (MSB first, white 1, blue 0) showing the
// previous frame's line count (on green), this line's CE pixel count
// (yellow), its CE period in clocks (cyan: 10 = 256, 8 = 320, 7 = 368,
// 5 = 512, 4 = 640) and the slot it is sent in (orange); the frame's last
// line (sent after VS) is red and native-slot padding magenta.
// FILL_DETECT (with ASPECT): Stretch (Fill Screen) crops a fixed overscan
// window as DuckStation's default does and lets the Pocket's scaler fill
// the screen with it: lines 8-231 of a 240-line frame or 480i field and
// 10-277 of a PAL 288-line one are sent, and package --fill-detect makes
// the Stretch slots 640x224, 640x448 (480i, slot 1 with DBL320, else 6)
// and 640x268, still 10:9 (2026-10-19). It used to measure each
// frame's black bands and zoom past them by sending some lines twice
// (FFVII's field: 224 lines in a 240-line frame; Tekken 3 and Ape Escape
// EU, 0.5.5-test18), but moving black bars at Bloody Roar II's screen
// changes rescaled the picture, each repeated line showed as a fixed seam
// 12 Pocket rows tall in FFVII's scrolling field, and the band logic cost
// area. Fit / Stretch is taken at VS, so each frame goes whole to one slot
// set; Fit output is unchanged. A frame with fewer lines than the window
// ends early (the release's fixed vblank gives every frame 240 or 288).
// FILL_CENTER (with FILL_DETECT, 2026-10-21): the window keeps its height (224 /
// 268, so slots and scaling are unchanged and no line is repeated) but is moved
// to centre the picture: the source lines holding a channel at 0x10 or more,
// merged over every frame since the last change of PAL / 480i or since eight
// black frames in a row, so a dark scene or a fade does not move it. It moves only
// by 2 lines or more, within the frame (top 0..16 of 240, 0..20 of 288). The
// fixed 8 / 10 window centres only pictures centred on the standard range; a PAL
// game drawn lower kept a black band on top (Ape Escape EU, 0.5.5).
// FILL_DISP (with FILL_CENTER, 2026-10-22): Fill centres its window on the
// game's display area instead: the lines the GPU shows from VRAM (its vertical
// display range, GP1(07h)), flagged per line by dispblank (low inside the range;
// the GPU's own vblank before MiSTer's fixed TV window replaces it). The range
// is what a TV positions, it follows only the game's GPU settings, and no
// picture content (a dark scene, a fade, a sprite flying through the border:
// Crash's Naughty Dog screen, its title then centred on lines the splash had
// lit) moves it. Same limits and 2-line hysteresis; Fit (PAL_SHAPE) keeps the
// content-based lines. 480i keeps the standard window: the BIOS's range put the
// window two or more lines up, showing overscan lines on the Sony and PlayStation
// screens (Crash USA, rc1f).
// PAL_SHAPE (with WEAVE576, FILL_CENTER; 2026-10-22): PAL pictures shown the
// Pocket way rather than as a PAL TV's letterbox. Fill's PAL window is 256
// lines (a 640x512 10:9 slot, 256 a field woven), so a 256-line PAL picture
// fills the screen. In Fit, once the picture lines seen since the last PAL/480i
// change span at least 192 lines: a picture of at most 240 lines is cropped to
// 240 around it and sent in the NTSC 640x240 / 640x480 slots, so the PAL BIOS
// looks like the USA one; a taller picture keeps the 288-line slots but is
// centred, moved up by at most 4 lines (3 in 480i: black lines after the last
// line must fit before VS) or down by at most 16 (black lines first). Both
// move only by 2 lines or more. FILL_CENTER then also needs the merged picture
// to span the window less 32 lines before it moves, so a small menu does not.
// BLEND (with WEAVE576, 2026-10-22): slot 2 is 800x240 4:3 instead of the native
// 256x240. 256-wide lines, and 368-wide ones when wide_even is set (Core Settings
// "Wide Pixels: Even"; "Sharp" keeps the native 368 slot), go out 800 wide: 800 is
// exactly 2 panel pixels a pixel at 4:3 on the Pocket, where 256 and 368 are 6.25
// and 4.35 (columns of 6/7 and 4/5 panel pixels: some letters a pixel narrower).
// An output pixel covers 0.32 / 0.46 source pixel; where it straddles a source
// pixel edge the two are mixed in eighths by the share of each (sharp bilinear),
// so every source pixel shows the same width. A line narrower than 256 / 368 is
// centred with black, a wider one centre-cropped, as in the native slots.
// FILL_BLEND (with WEAVE576, 2026-10-22): Fill sends every line 800 wide with
// the same sharp-bilinear blend, whatever its width (320, 368, 384, 512, 640),
// in 800x448 / 800x512 (PAL_SHAPE) 10:9 slots: 800 is exactly 2 panel pixels a
// pixel on the Pocket's 1600-wide screen, where Fill's 640 slots made 368-wide
// menus 4 or 5 panel pixels a pixel (FFVII, 2026-10-22). Line copies
// (WEAVE576) are blended the same way. Fit keeps its native slots.
// FILL_BLEND 2 (2026-10-22): the 800-wide doubled lines (1,600 pixels a line
// period) were more than the Pocket's scaler takes: some lines lost their right
// part to old frames (probe9/10, FFVII field). Fill stays 640 wide (as probes
// 5-8) and the blend works in panel columns: the Pocket draws 640 -> 1,600 with
// output pixels alternately 2 and 3 panel pixels wide, so each output pixel's
// mix is the share of ITS panel columns that each source pixel covers. Every
// source pixel then covers the same 1600/width panel columns. fill_phase picks
// whether output pixel 0 is 2 (A) or 3 (B) panel pixels wide (a Core Setting
// until the Pocket's phase is known).
// PACE (2026-10-22): every third clock of a line read is a held pixel sent with
// skip, so lines go out at 2/3 of the 53.7 MHz pixel clock (a 640 line in 960
// clocks, an 800 one in 1,200). Docked, the full rate gave a shaking stripe at
// the right of the picture and Fill flickering between two sizes; the paced
// pixels of the first dock test build fixed both (issue #15 tester, both
// aspect settings), and the handheld shows no difference.
// DOCK_TEST (test builds, 2026-10-22): dock_mode from a Core Settings list,
// taken at VS, sets when the end-of-line slot word goes out (issues #13, #15:
// docked, a stripe of dashes at the right edge of the picture and the picture
// flickering between two sizes; handheld is clean). The first dock test (paced
// pixels, a black tail) changed nothing; both symptoms fit a dock that sees DE
// fall a clock late, so it takes the slot word as a 641st pixel and reads the
// slot a clock after we cleared it. 0: the word on the first clock after DE (as
// before); 1: on the first and second clock; 2: black on the first, the word on
// the second; 3: the word held from the first clock until the next line.
// EOL_TWICE (no setting): the slot word goes out on the first and second clock
// after DE, as DOCK_TEST mode 1, in every build: the handheld reads the first,
// a dock that sees DE a clock late the second (#15 photos: the dock flips
// between two slot sizes and shows the word as a dashed column).
// OVERSCAN_BLANK: with hide_overscan (the "Hide Overscan" setting, taken
// at VS) the lines outside that window (0-7 and 232-239 of a 240-line
// frame, so woven rows 0-15 and 464-479 in 480i; 0-9 and 278-287 at PAL
// 288) are written black, so Fit (Original) shows the same picture as
// Fill but keeps 240/288 lines (DuckStation hides NTSC 16-23 and
// 248-255 of 16-256; stray lines at the top in Tekken 3 and Ape Escape
// EU). Lines are counted from VS as written, not after CROP_LINES' crop.
// LOG (diagnostic builds only, with --perf-log --video-log): four words per
// frame go to psx_perf_log's ring (rtl/pocket/psx_perf_log.sv), saved by the
// Pocket to SD when the core quits. They are latched at vs_edge on
// video_rgb_clock and log_seq toggles with them; psx_perf_log takes them only
// after the toggle has crossed into clk1x, so a word is never read mid-change.
// W4={lines[9:0],hash[9:0],slot[2:0],pal,mode480i,bob,i480,fit,fd_on,os_on,
// field_last,pal_frame}, W5={lines_out[9:0],first_kept[9:0],last_kept[9:0],2'b0},
// W6={hs_cnt[9:0],wlen[9:0],pic_x0[9:0],2'b0}, W7={pic_y0[9:0],pic_y1[9:0],
// pic_x1[9:0],2'b0}. lines_out against the slot height is the line count the
// scaler gets; first_kept/last_kept the source lines it gets. pic_y0..pic_y1 and
// pic_x0..pic_x1 are the source lines and pixels holding picture (a channel at
// 0x10 or more; 1023/0 when the frame is black), so the black band above the
// picture in the output is pic_y0-first_kept lines and the picture height is
// pic_y1-pic_y0+1 of the lines_out the slot shows (squash, bars, centring).
// hash is a 10-bit CRC of every pixel written: a static screen
// that alternates between two hashes is two different pictures alternating
// (fields shown in turn), the in-place jitter. hs_cnt counts HS edges without
// PAL_TIMING's 289 clamp (311 PAL, 261 NTSC).
// PAL_BOB (with ASPECT, PAL288, CROP_LINES): a PAL 480i field is shown as a whole
// 288-line frame of its own (bob) instead of woven into a 480i frame (2026-10-20).
// The 0.5.5 slot table has no 576-line slot, so a woven PAL frame is cropped to 480
// and loses the top and bottom of the white PAL BIOS screen; tagged with a shorter
// slot the scaler halves it (0.5.5-pal480). A PAL field is already a whole picture, so the core counts its
// 288 lines, keeps them all (crop 288) and sends no 480i frame feature word at VS:
// Fit puts them in the 4:3 640x288 slot, Fill in the 10:9 640x268 one, and the line
// count always equals the slot height, which is what makes the Fill picture reach
// all four edges. A 320-class line sent at 640 is doubled like a progressive one.
// NTSC 480i keeps weaving (pal is 0 there). Fields alternate at 50 Hz, so a moving
// PAL picture flickers a little; BIOS screens and notices are static.
// HEIGHT_SLOTS (with ASPECT, FILL_DETECT): the 8 slots differ by height
// only, so no resolution is cut (2026-10-19): every line goes out at 640
// (320-class lines with each pixel doubled, other widths by the DDA, so
// 384-wide Ape Escape is no longer cut to 368; a line at the 368 pixel
// clock is never doubled as a 320 one) and the slot is
// {Fill, PAL, 480i}: 0 = 640x240, 1 = 640x480, 2 = 640x288, 3 = 640x576
// at 4:3; 4 = 640x224, 5 = 640x448, 6 = 640x268, 7 = 640x536 at 10:9 (Fill,
// the overscan crop). PAL 480i fields keep 288 lines (576 woven) instead
// of being cut to 240. Heights are the PSX's own, so the Pocket's Integer
// scaling and CRT filters see whole rows.
`default_nettype none
module psx_apf_video_wide #(parameter integer INTERLACE_FEATURE=0, parameter integer FIELD_INVERT=0,
    parameter integer PAL288=0, parameter integer HYBRID=0, parameter integer LEAD_LINE=0,
    parameter integer VS_ON_HS=0, parameter integer ASPECT=0, parameter integer CROP_LINES=0,
    parameter integer PAL_TIMING=0, parameter integer VIDEO_PROBE=0, parameter integer SLOT368=0,
    parameter integer DBL320=0, parameter integer FILL_DETECT=0,
    parameter integer OVERSCAN_BLANK=0, parameter integer HEIGHT_SLOTS=0,
    parameter integer PAL_BOB=0, parameter integer LOG=0, parameter integer FILL_CENTER=0,
    parameter integer EVEN_LINES=0, parameter integer WEAVE576=0, parameter integer DOCK_TEST=0,
    parameter integer PAL_SHAPE=0, parameter integer BLEND=0, parameter integer EOL_TWICE=0,
    parameter integer FILL_BLEND=0, parameter integer PACE=0, parameter integer FILL_DISP=0,
    parameter integer FILL_LEAD=0) (
    input wire clk, ce,
    input wire hsync, vsync, hblank, vblank, interlace, blank, weave,
    input wire dispblank,             // FILL_DISP: high outside the GPU's display range
    input wire field_swap,
    input wire stretch,               // ASPECT: Stretch (10:9 slots) instead of Fit
    input wire hide_overscan,         // OVERSCAN_BLANK: "Hide Overscan" setting
    input wire [1:0] dock_mode,       // DOCK_TEST: bit 0 paced pixels, bit 1 black tail
    input wire wide_even,             // BLEND: 368-wide lines blended too ("Wide Pixels: Even")
    input wire fill_phase,            // FILL_BLEND 2: output pixel 0 is 3 panel pixels wide
    input wire [23:0] rgb_in,
    output reg hs=0, vs=0, de=0, skip=0,
    output reg [23:0] rgb=0,
    output reg [31:0] log_w4=0, log_w5=0, log_w6=0, log_w7=0,   // LOG: one set per frame
    output reg log_seq=0                                          // LOG: toggles with each set
);
    localparam [9:0] OUT_W=10'd640;
    reg hs_d=0, vs_d=0, active_d=0;
    reg vs_wait=0;   // VS_ON_HS (as psx_apf_video): VS waits for the next HS start
    reg [3:0] hs_pipe=0;   // VS_ON_HS: HS 4 clocks after VS (APF wants at least 3)
    wire vs_edge=VS_ON_HS!=0 ? hsync && !hs_d && (vsync && !vs_d || vs_wait) : vsync && !vs_d;
    wire active=!hblank && !vblank;
    wire hs_end=!hsync && hs_d;

    // Two-line buffer
    reg [23:0] mem [0:2047];
    reg wb=0, rb=0, pend=0;
    // the buffer this line is read from, taken when its first read starts: the
    // GPU's next line flips rb when it ends, which can fall inside a line's copy
    // (WEAVE576 Fill doubling; FFVII's field: the right part of every copy came
    // from the next line, probe9). That buffer is written again only a line later.
    reg rb_l=0;
    reg [9:0] wx=0, wlen=0;

    // 480i and PAL detection, as in psx_apf_video
    reg seen_now=0, seen_last=0, field_last=0;
    wire mode480i=INTERLACE_FEATURE!=0 && weave && (seen_now || seen_last);
    reg [9:0] lines=0, lines_last=0;
    reg [8:0] hs_n=0;   // PAL_TIMING: HS this frame, held at 289
    reg pal_frame=0;    // PAL_TIMING: the previous frame had more than 288 HS
    wire pal=PAL288!=0 && lines_last>242 && (PAL_TIMING==0 || pal_frame);
    // Output width and slot of the buffered line (chosen when its read starts)
    function automatic native_ok(input [9:0] w, input [9:0] n);
        native_ok=w<=n && w>=n-(n>>4);
    endfunction
    // PAL_BOB: a PAL 480i field is shown as its own whole frame (bob) instead of
    // being woven, so i480 is 0 for it everywhere a progressive picture is meant.
    wire bob=PAL_BOB!=0 && pal && mode480i;
    wire i480=mode480i && !bob;
    wire [2:0] h_slot=mode480i ? 3'd1 : pal ? 3'd2 : 3'd0;
    reg [2:0] slot=0;
    reg [9:0] out_w=10'd640, lead=0;
    wire over_320=wlen>10'd320 && wlen<=10'd360;   // cropped to 320
    reg fit=1;   // ASPECT: Fit (not stretch), taken at VS so a frame is all one
    wire nat=HYBRID!=0 && HEIGHT_SLOTS==0 && !mode480i && fit && (ASPECT==0 || !pal);
    // SLOT368: CE period of the line being written and of the buffered line
    reg [3:0] cec=0, ce_p=0, ce_line=0;
    wire c368=(SLOT368!=0 || HEIGHT_SLOTS!=0) && ASPECT!=0 && HYBRID!=0 && ce_line==4'd7;
    wire use_368=nat && c368;
    wire use_320=DBL320==0 && nat && !c368 && (native_ok(wlen,10'd320) || over_320);
    wire use_256=nat && !c368 && native_ok(wlen,10'd256);
    wire use_512=nat && ASPECT==0 && !pal && native_ok(wlen,10'd512);
    reg f240=0;   // PAL_SHAPE: this PAL Fit frame is cropped to 240 lines
    localparam integer BLX=BLEND!=0 || FILL_BLEND!=0;   // either blend
    reg we_on=0;   // BLEND: wide_even, taken at VS
    wire bl_fill=FILL_BLEND!=0 && !fit;
    wire bl_next=BLEND!=0 && (use_256 || use_368 && we_on) || bl_fill;
    // the width the 800 pixels span: Fill a whole line (321-360 centre-cropped to
    // 320, as before), Fit 256 / 368 with short lines centred
    wire [9:0] bl_w=bl_fill ? (over_320 ? 10'd320 : wlen) : use_368 ? 10'd368 : 10'd256;
    wire [2:0] slot_w576=!fit ? (pal ? 3'd7 : 3'd1)
                         : i480 ? (pal && !f240 ? 3'd5 : 3'd3) : pal ? (f240 ? 3'd0 : 3'd4) : use_256 || bl_next ? 3'd2 : use_368 ? 3'd6 : 3'd0;
    wire [2:0] slot_aspect=WEAVE576!=0 ? slot_w576 : !fit ? (i480 ? (DBL320!=0 ? 3'd1 : SLOT368!=0 ? 3'd3 : 3'd6) : pal ? 3'd7 : 3'd5)
                         : i480 ? 3'd3 : pal ? 3'd4 : use_320 ? 3'd1 : use_256 ? 3'd2 : use_368 ? 3'd6 : 3'd0;
    wire [2:0] slot_next=HYBRID==0 ? h_slot : HEIGHT_SLOTS!=0 ? {!fit, pal, mode480i} : ASPECT!=0 ? slot_aspect : mode480i ? 3'd3 : use_320 ? (pal ? 3'd4 : 3'd1) : use_256 ? (pal ? 3'd5 : 3'd2)
                       : use_512 ? 3'd7 : pal ? 3'd6 : 3'd0;
    // CROP_LINES window for this field, from the previous field's count
    wire [9:0] crop_h=pal && (HEIGHT_SLOTS!=0 || WEAVE576!=0 || i480==0) ? 10'd288 : 10'd240;
    wire [9:0] crop_top=lines_last>crop_h ? (lines_last-crop_h)>>1 : 10'd0;
    wire keep_line=CROP_LINES==0 || (lines>=crop_top && lines<crop_top+crop_h);
    // FILL_DETECT: the fixed window, lines fd_t to fd_e-1
    wire pal_h=crop_h[8];   // 288
    wire [9:0] fd_h=pal_h ? (PAL_SHAPE!=0 ? 10'd256 : 10'd268) : 10'd224;
    reg [9:0] fc_top=10'd8;   // FILL_CENTER: the window's first line
    wire [9:0] fd_t=FILL_CENTER!=0 ? fc_top : pal_h ? (PAL_SHAPE!=0 ? 10'd16 : 10'd10) : 10'd8;
    wire [9:0] fd_e=fd_t+fd_h;
    // FILL_LEAD: in 480i Fill the lead line is the source line above the window
    // instead of black (the scaler showed part of the black lead line on the
    // USA BIOS screens, rc1h; 0.5.5 was clean)
    wire fd_lead=FILL_LEAD!=0 && FILL_DETECT!=0 && !fit && mode480i && fd_t!=10'd0;
    wire fd_in=lines>=(fd_lead ? fd_t-10'd1 : fd_t) && lines<fd_e;
    wire fd_on=FILL_DETECT!=0 && !fit;
    reg os_on=0;   // hide_overscan, taken at VS
    wire os_line=OVERSCAN_BLANK!=0 && os_on && !fd_in;
    wire [9:0] w_next=bl_next ? (bl_fill && FILL_BLEND==2 ? 10'd640 : 10'd800) : use_368 ? 10'd368 : use_320 ? 10'd320 : use_256 ? 10'd256 : use_512 ? 10'd512 : OUT_W;
    wire dbl_next=HYBRID!=0 && ASPECT!=0 && i480==0 && !use_320 && !use_368 && !(HEIGHT_SLOTS!=0 && c368) && (native_ok(wlen,10'd320) || over_320);
    wire native_next=use_368 || use_320 || use_256 || use_512 || dbl_next;
    wire over_368=wlen>10'd368;   // SLOT368: centre-cropped to 368

    // Read side: DDA over the source width
    reg rd_on=0, rd_d=0, out_d=0, native=0, pix_d=0, lead_pend=0, black=0;
    reg vblank_d=0, pad_pend=0, de_odd=0;   // EVEN_LINES: de_odd = odd lines since VS
    // WEAVE576: progressive Fill lines go out twice; the copy starts half a line
    // after the source HS, with an HS of its own
    reg [11:0] lp_cnt=0, lp_half=12'd1706, hs_w=12'd250;
    reg rep_pend=0, rep_on=0, rep_blk=0;   // rep_blk: the copy is a black line (PAL_SHAPE post lines)
    reg [9:0] rep_s=0;
    // BLEND read state: pa / pb the source pixels j / j+1, q pixel j+2 (qv: inside
    // the line), bs the buffer address of j+3; bacc the output pixel's left edge in
    // 1/800 source pixel
    reg bl=0, qv=0;
    reg [1:0] bl_pre=0;
    reg [11:0] bacc=0;
    reg [9:0] bw=0;
    reg bpm=0, fph=0;            // this line blends in panel columns (FILL_BLEND 2); its phase
    reg [11:0] bw2=0, bw3=0;     // 2 and 3 panel pixels of an output pixel, in 1/w units
    reg signed [10:0] bs=0;
    reg [23:0] pa=0, pb=0, qb=0;
    wire bs_ok=!bs[10] && bs[9:0]<wlen;
    // a source pixel spans bunit: 800 output pixels' worth (800 wide) or 1600 panel
    // columns (panel mode); an output pixel moves bacc by its width in those units
    wire wide_px=bpm && (oi[0]^fph);
    wire [11:0] bunit=bpm ? 12'd1600 : 12'd800;
    wire [11:0] bstep=bpm ? (wide_px ? bw3 : bw2) : {2'd0,bw};
    wire [11:0] bright=bacc+bstep;
    wire [11:0] bpart=bright-bunit;
    // weight in eighths, round(8*bpart/bw): 256 by a shift, 368 by its eight
    // rounding points (23, 69, ..., 345) instead of a multiply (video clock timing)
    // weight in eighths, round(8*bpart/bw) halves up: bpart >= (2k-1)*bw/16 for
    // k = 1..8, the eight points worked out at the line's start
    // round(8*part/span) with span = c*w (c 1 in 800 mode, 2 or 3 panel columns):
    // part*(6/c) against the eight points (2k-1)*6w/16, one set for every span,
    // worked out one a clock at the line's start (bth_n)
    wire [11:0] bp=bpart;
    wire [11:0] bpn=!bpm ? {bp[9:0],2'b0}+{bp[10:0],1'b0} : wide_px ? {bp[10:0],1'b0} : bp+{bp[10:0],1'b0};
    reg [11:0] bth [0:7];
    reg [3:0] bth_n=0;      // thresholds still to work out
    reg [15:0] bth_acc=0;   // (2k-1)*6w for the next one
    reg [12:0] bs6x2=0;     // 2*6w
    wire [3:0] bt=bright<=bunit ? 4'd0 : {3'd0,bpn>=bth[0]}+{3'd0,bpn>=bth[1]}+{3'd0,bpn>=bth[2]}+{3'd0,bpn>=bth[3]}
                                        +{3'd0,bpn>=bth[4]}+{3'd0,bpn>=bth[5]}+{3'd0,bpn>=bth[6]}+{3'd0,bpn>=bth[7]};
    reg signed [10:0] bs0=0;   // the line's first buffer address, for its copy
    // the mix is a clock of its own: blended lines go out one clock later
    reg [23:0] pa_r=0, pb_r=0;
    reg [3:0] bt_r=0;
    reg rd_dd=0, pix_dd=0;
    // One read of the line buffer a clock, at one address, so it stays a block RAM
    // (two read sites made Quartus build its 2048x24 bits in logic: probe7, 331%).
    wire bl_now=BLX && bl && rd_on;
    wire bl_adv=bright>=bunit;
    wire [9:0] raddr=BLX && (bl_pre!=0 || bl_now) ? bs[9:0] : s;
    wire rd_en=BLX && bl_pre!=0 || rd_on && (!bl_now || adv && bl_adv);
    function automatic [7:0] mix(input [7:0] a, input [7:0] b, input [3:0] w);
        reg [11:0] m;
        begin m=a*(4'd8-w)+b*w; mix=m[10:3]; end
    endfunction
    // DOCK_TEST
    reg [1:0] dk=0;          // dock_mode, taken at VS
    reg [1:0] pc=0;          // paced: clock in a group of three
    reg tail=0, st_d1=0, st_d2=0, st_d3=0;
    reg [3:0] tail_n=0;
    wire adv=PACE==0 || pc!=2'd2;   // PACE: the third clock holds
    reg eol1=0, eol_hold=0;   // DOCK_TEST: the clock after the slot word; holding it
    reg dbl=0, ph=0;   // dbl: each source pixel twice (ph: the second copy)
    reg [10:0] pw=0;   // picture width in output pixels
    reg [9:0] oi=0, s=0, acc=0;
    wire [10:0] acc_next={1'b0,acc}+wlen;
    wire in_pic=!native || ({1'b0,oi}>={1'b0,lead} && {1'b0,oi}<{1'b0,lead}+pw);
    reg [23:0] q=0;

    // LOG state (per frame, latched at vs_edge)
    reg [9:0] hs_cnt=0, out_n=0, log_first=0, log_last=0, log_src=0;
    reg log_have=0;
    // LOG and FILL_CENTER: picture extent (and, LOG, content hash) of the lines
    // written this frame
    localparam integer PIC=LOG!=0 || FILL_CENTER!=0;
    reg [9:0] pic_y0=0, pic_y1=0, pic_x0=10'h3FF, pic_x1=0, hash=0;
    reg pic_any=0;
    // FILL_CENTER: picture lines merged since the mode changed, with this frame's
    reg [9:0] u0=10'h3FF, u1=0;
    reg [1:0] fc_key=0;
    reg [3:0] fc_dark=0;
    wire fc_reset=fc_key!={pal, mode480i} || fc_dark==4'd8;
    wire [9:0] nu0=fc_reset || pic_any && pic_y0<u0 ? (pic_any ? pic_y0 : 10'h3FF) : u0;
    wire [9:0] nu1=fc_reset || pic_any && pic_y1>u1 ? (pic_any ? pic_y1 : 10'd0) : u1;
    // top = round((nu0+nu1+1-fd_h)/2), within 0..frame-fd_h (16 or 20)
    wire signed [11:0] fc_t2=$signed({2'b0,nu0})+$signed({2'b0,nu1})+12'sd1-$signed({2'b0,fd_h});
    wire [9:0] fc_max=pal_h ? (PAL_SHAPE!=0 ? 10'd32 : 10'd20) : 10'd16;
    wire [9:0] fc_def=pal_h ? (PAL_SHAPE!=0 ? 10'd16 : 10'd10) : 10'd8;
    wire [9:0] u_span=nu1-nu0;   // merged picture lines less one
    wire fc_small=PAL_SHAPE!=0 && u_span+10'd33<fd_h;   // spans less than the window less 32
    // FILL_DISP: this frame's display-range lines
    reg [9:0] dd0=0, dd1=0;
    reg dd_any=0;
    wire signed [11:0] fd_t2=$signed({2'b0,dd0})+$signed({2'b0,dd1})+12'sd1-$signed({2'b0,fd_h});
    wire [9:0] fd_want=fd_t2<0 ? 10'd0 : fd_t2[10:1]>fc_max ? fc_max : fd_t2[10:1];
    wire [9:0] fc_want=nu0>nu1 || fc_small ? fc_def : fc_t2<0 ? 10'd0
                       : fc_t2[10:1]>fc_max ? fc_max : fc_t2[10:1];
    // PAL_SHAPE Fit: crop to 240 (f240) or centre the 288 lines (fsh: + up, - down)
    wire u_big=nu0<=nu1 && u_span>=10'd191;
    wire f240_want=PAL_SHAPE!=0 && pal && u_big && u_span<=10'd239;
    wire signed [11:0] ft2=$signed({2'b0,nu0})+$signed({2'b0,nu1})+12'sd1-12'sd240;
    wire [9:0] t240_want=ft2<0 ? 10'd0 : ft2[10:1]>10'd48 ? 10'd48 : ft2[10:1];
    reg [9:0] t240=10'd24;
    wire signed [11:0] fs2=$signed({2'b0,nu0})+$signed({2'b0,nu1})+12'sd1-12'sd288;
    wire signed [11:0] fs1=fs2>>>1;
    wire signed [5:0] fsh_lim=mode480i ? 6'sd3 : 6'sd4;
    wire signed [5:0] fsh_want=!u_big ? 6'sd0 : fs1>12'sd15 || fs1>$signed({{6{fsh_lim[5]}},fsh_lim}) ? fsh_lim
                               : fs1<-12'sd16 ? -6'sd16 : fs1[5:0];
    reg signed [5:0] fsh=0;
    wire signed [6:0] fsh_d=$signed({fsh_want[5],fsh_want})-$signed({fsh[5],fsh});
    wire fit_pal=PAL_SHAPE!=0 && fit && pal;
    wire ft_in=f240 ? lines>=t240 && lines<t240+10'd240
              : fsh[5] ? lines<10'd288-{4'd0,-fsh} : lines>={4'd0,fsh};
    reg [4:0] pre_n=0, post_n=0;   // black lines before / after the picture (fsh)
    // the values the coming frame uses (f240 and fsh change at VS)
    wire fsh_mv=fsh_d>7'sd1 || fsh_d<-7'sd1 || !u_big;
    wire signed [5:0] fsh_next=PAL_SHAPE!=0 && FILL_CENTER!=0 ? (fsh_mv ? fsh_want : fsh) : 6'sd0;
    // FILL_DISP: 480i keeps the standard window (the BIOS's display range moved it up
    // over overscan lines on the Sony and PlayStation screens, rc1f)
    wire [9:0] fc_next=FILL_DISP==0 ? fc_want : mode480i ? fc_def : fd_want;
    wire fc_move=(FILL_DISP==0 || dd_any || mode480i) && (fc_next>fc_top+10'd1 || fc_top>fc_next+10'd1);
    wire pic_px=!blank && (|rgb_in[23:20] || |rgb_in[15:12] || |rgb_in[7:4]);
    // LOG: the lines actually sent this frame and the first/last source line
    // index among them. The index is taken where the line is WRITTEN (the same
    // pre-increment `lines` that keep_line compares): the last lines of a frame
    // are read out after vs_edge, which resets lines, so taking it at the read
    // start gave 0/1 for the whole tail of the frame.

    // VIDEO_PROBE
    reg [9:0] p_line=0, p_cur=0, p_w=0, p_ce=0, ce_gap=0, ce_cnt=0, p_oi=0;
    reg p_fresh=0, p_last=0;   // the pending line was written after the last VS / is the frame's last line
    reg [9:0] p_val;
    always @(*) case (p_cur[1:0])
        2'd0: p_val=lines_last;
        2'd1: p_val=p_w;
        2'd2: p_val=p_ce;
        default: p_val={7'd0,slot};
    endcase
    wire p_bit=p_val[4'd9-p_oi[7:4]];
    wire [23:0] p_bg=p_cur==0 ? 24'h00FF00 : p_cur==1 ? 24'hFFFF00 : p_cur==2 ? 24'h00FFFF : 24'hFF8000;

    always @(posedge clk) begin
        hs_d<=hsync; vs_d<=vsync; active_d<=active;
        if (VS_ON_HS!=0) vs_wait<=!vs_edge && (vs_wait || vsync && !vs_d);
        hs_pipe<={hs_pipe[2:0], hsync && !hs_d};
        if (PAL_TIMING!=0 && hsync && !hs_d && hs_n!=9'd289) hs_n<=hs_n+1'b1;
        if (LOG!=0) begin
            if (hsync && !hs_d && hs_cnt!=10'h3FF) hs_cnt<=hs_cnt+1'b1;
        end
        hs<=(VS_ON_HS!=0 ? hs_pipe[3] : hsync && !hs_d) || WEAVE576!=0 && rep_on;
        vs<=vs_edge;

        // write
        if (active && ce) begin
            mem[{wb,wx}]<=blank || os_line ? 24'h0 : rgb_in;
            if (wx!=10'h3FF) wx<=wx+1'b1;
            if (PIC) begin
                // CRC step, x^10+x^3+1 (period 1023: no line length lines its terms up)
                hash<={hash[8:0],1'b0}^(hash[9] ? 10'h009 : 10'h0)^rgb_in[23:14]^rgb_in[13:4]^{rgb_in[3:0],6'b0};
                if (pic_px) begin
                    if (!pic_any) pic_y0<=lines;
                    pic_y1<=lines; pic_any<=1'b1;
                    if (wx<pic_x0) pic_x0<=wx;
                    if (wx>pic_x1) pic_x1<=wx;
                end
            end
        end
        if (FILL_DISP!=0 && active && ce && !dispblank) begin
            if (!dd_any) dd0<=lines;
            dd1<=lines; dd_any<=1'b1;
        end
        if ((SLOT368!=0 || HEIGHT_SLOTS!=0) && active) begin
            if (ce) begin ce_p<=cec+1'b1; cec<=0; end else if (cec!=4'hF) cec<=cec+1'b1;
        end
        if (!active && active_d) begin
            ce_line<=ce_p;
            wlen<=wx; wx<=0; rb<=wb; wb<=~wb; pend<=keep_line;
            if (lines!=10'h3FF) lines<=lines+1'b1;
            if (fd_on) pend<=fd_in;
            if (fit_pal) pend<=ft_in;
            if (LOG!=0) log_src<=lines;
        end

        // read
        if (LEAD_LINE!=0 && vs_edge) lead_pend<=(LEAD_LINE==1 || mode480i) && !fd_lead;
        if (PAL_SHAPE!=0) begin
            if (vs_edge) pre_n<=fit_pal && !f240_want && fsh_next[5] ? -fsh_next[4:0] : 5'd0;
            if (vblank && !vblank_d) post_n<=fit_pal && !f240 && !fsh[5] ? fsh[4:0] : 5'd0;
            if (vs_edge) post_n<=0;
            // black lines, as the lead line; the lead line goes first
            if (hs_end && !pend && !(LEAD_LINE!=0 && lead_pend) && (pre_n!=0 || post_n!=0)) begin
                rd_on<=1; oi<=0; black<=1;
                if (pre_n!=0) pre_n<=pre_n-1'b1;
                else if (WEAVE576!=0 && post_n>=5'd2) begin
                    // two black lines a line period (the second at half a line, as a
                    // Fill copy), so up to 4 fit in the few lines before VS
                    post_n<=post_n-5'd2; rep_pend<=1; rep_blk<=1;
                end else post_n<=post_n-1'b1;
            end
        end
        if (LEAD_LINE!=0 && lead_pend && hs_end && !pend) begin
            lead_pend<=0; rd_on<=1; oi<=0; black<=1;   // black lead line, previous slot and width
        end
        vblank_d<=vblank;
        if (EVEN_LINES!=0 && vblank && !vblank_d) pad_pend<=1;
        if (EVEN_LINES!=0 && pad_pend && hs_end && !pend && post_n==0) begin
            pad_pend<=0;
            if (de_odd) begin rd_on<=1; oi<=0; black<=1; end   // black pad line, as the lead line
        end
        if (vs_edge) pad_pend<=0;
        if (EVEN_LINES!=0) de_odd<=(vs_edge ? 1'b0 : de_odd)^(rd_on && !rd_d);
        if (WEAVE576!=0) begin
            if (hsync && !hs_d) begin lp_cnt<=0; lp_half<={1'b0,lp_cnt[11:1]}; end else lp_cnt<=lp_cnt+1'b1;
            if (hs_end) hs_w<=lp_cnt;
            rep_on<=rep_pend && lp_cnt==lp_half;   // the copy's HS
            if (rep_pend && lp_cnt==lp_half+hs_w) begin
                rep_pend<=0; rep_blk<=0; rd_on<=1; oi<=0; acc<=0; black<=rep_blk; ph<=0; s<=rep_s;
                // a blended line's copy is blended again from its start
                if (BLX && bl && !rep_blk) begin rd_on<=0; bl_pre<=2'd3; bacc<=0; bs<=bs0; end
                if (LOG!=0) out_n<=out_n+1'b1;
            end
        end
        if (pend && hs_end) begin
            pend<=0; rd_on<=1; oi<=0; acc<=0; black<=0; rb_l<=rb;
            if (WEAVE576!=0) rep_pend<=!fit && !i480;
            if (LOG!=0) begin
                out_n<=out_n+1'b1;
                if (!log_have) begin log_first<=log_src; log_have<=1'b1; end
                log_last<=log_src;
            end
            slot<=slot_next; out_w<=w_next; native<=native_next;
            lead<=use_368 ? (over_368 ? 10'd0 : (10'd368-wlen)>>1) : over_320 ? 10'd0 : dbl_next ? 10'd320-wlen : (w_next-wlen)>>1;
            s<=use_368 ? (over_368 ? (wlen-10'd368)>>1 : 10'd0) : (use_320 || dbl_next) && over_320 ? {5'd0,wlen[5:1]} : 10'd0;
            rep_s<=(use_320 || dbl_next) && over_320 ? {5'd0,wlen[5:1]} : 10'd0;
            dbl<=dbl_next; ph<=0; pw<=dbl_next ? {wlen,1'b0} : {1'b0,wlen};   // centre crop: 320 = 5*64, so (wlen-320)/2 = wlen[5:1]
            bl<=bl_next;
            if (bl_next) begin
                rd_on<=0; bl_pre<=2'd0; bth_n<=4'd8; bacc<=0; bw<=bl_w;   // thresholds, then the prefetch
                bth_acc<={4'd0,bl_w,2'b0}+{5'd0,bl_w,1'b0};                // 6w
                bs6x2<={1'b0,bl_w,2'b0}+{2'd0,bl_w,1'b0}+{1'b0,bl_w,2'b0}+{2'd0,bl_w,1'b0};   // 12w
                // nominal pixel 0 at buffer address -(nominal - wlen)/2
                bs<=$signed({1'b0,wlen}-{1'b0,bl_w})>>>1;
                bs0<=$signed({1'b0,wlen}-{1'b0,bl_w})>>>1;
                bpm<=bl_fill && FILL_BLEND==2; fph<=fill_phase;
                bw2<={1'b0,bl_w,1'b0}; bw3<={2'd0,bl_w}+{1'b0,bl_w,1'b0};

            end
        end
        if (BLX && bth_n!=0) begin
            bth[4'd8-bth_n]<=(bth_acc+16'd15)>>4;
            bth_acc<=bth_acc+{3'd0,bs6x2};
            bth_n<=bth_n-1'b1;
            if (bth_n==4'd1) bl_pre<=2'd3;
        end
        if (BLX && bl_pre!=0) begin
            qv<=bs_ok; bs<=bs+1'b1;
            if (bl_pre==2'd2) pa<=qv ? q : 24'h0;
            if (bl_pre==2'd1) begin pb<=qv ? q : 24'h0; rd_on<=1; end
            bl_pre<=bl_pre-1'b1;
        end
        if (rd_en) q<=mem[{rb_l,raddr}];
        if (rd_on && BLX && bl) begin
            pix_d<=!black;
            pa_r<=pa; pb_r<=pb; bt_r<=bt;   // a held clock repeats the same pixel
            if (PACE!=0) pc<=pc==2'd2 ? 2'd0 : pc+1'b1;
            if (adv) begin
                if (bl_adv) begin
                    bacc<=bpart; pa<=pb; pb<=qv ? q : 24'h0;
                    qv<=bs_ok; bs<=bs+1'b1;
                end else bacc<=bright;
                oi<=oi+1'b1;
                if (oi==out_w-1'b1) rd_on<=0;
            end
        end else if (rd_on) begin
            pix_d<=in_pic && !black && !tail;
            if (DOCK_TEST!=0 || PACE!=0) pc<=pc==2'd2 ? 2'd0 : pc+1'b1;
            if (adv && !tail) begin
                if (native) begin if (in_pic) begin ph<=!ph; if (!dbl || ph) s<=s+1'b1; end end
                else if (acc_next>={1'b0,out_w}) begin s<=s+1'b1; acc<=acc_next-out_w; end else acc<=acc_next[9:0];
                oi<=oi+1'b1;
                if (oi==out_w-1'b1) begin
                    rd_on<=0;
                end
            end
            if (adv && tail) begin
                tail_n<=tail_n-1'b1;
                if (tail_n==0) begin tail<=0; rd_on<=0; end
            end
        end else pc<=0;
        st_d1<=rd_on && !adv; st_d2<=st_d1; st_d3<=st_d2;
        rd_d<=rd_on;
        if (BLX) begin
            qb<=bt_r==0 ? pa_r : {mix(pa_r[23:16],pb_r[23:16],bt_r), mix(pa_r[15:8],pb_r[15:8],bt_r), mix(pa_r[7:0],pb_r[7:0],bt_r)};
            rd_dd<=rd_d; pix_dd<=pix_d;
        end
        out_d<=BLX && bl ? rd_dd : rd_d;
        de<=BLX && bl ? rd_dd : rd_d;
        skip<=PACE!=0 && (BLX && bl ? st_d3 : st_d2);
        rgb<=BLX && bl ? (rd_dd && pix_dd ? qb : 24'h0) : rd_d && pix_d ? q : 24'h0;
        if (VIDEO_PROBE!=0) begin
            if (active) begin ce_cnt<=ce ? 10'd0 : ce_cnt+1'b1; if (ce) ce_gap<=ce_cnt+1'b1; end
            p_oi<=oi;
            if (!active && active_d) p_fresh<=1;
            if (pend && hs_end) begin
                p_last<=!p_fresh; p_cur<=p_line; p_w<=wlen; p_ce<=ce_gap;
                if (p_fresh) p_line<=p_line+1'b1;
            end
            // the last line of a frame is read after VS (a line goes out
            // during the following line period)
            if (vs_edge) begin p_line<=0; p_fresh<=0; end
            if (rd_d && !black)
                rgb<=!pix_d ? 24'hFF00FF : p_last ? 24'hFF0000
                    : p_cur<10'd4 ? (p_oi<10'd160 ? (p_bit ? 24'hFFFFFF : 24'h0000FF) : p_bg) : q;
        end
        if (!(BLX && bl ? rd_dd : rd_d) && out_d)   // end-of-line slot word
            rgb<=DOCK_TEST!=0 && dk==2'd2 ? 24'h0 : {8'b0,slot,13'b0};
        if (DOCK_TEST!=0 || EOL_TWICE!=0) begin
            eol1<=!(BLX && bl ? rd_dd : rd_d) && out_d;
            if (eol1 && (EOL_TWICE!=0 || dk!=2'd0)) rgb<={8'b0,slot,13'b0};
            if (eol1 && dk==2'd3) eol_hold<=1'b1;
            if (rd_on || vs_edge) eol_hold<=1'b0;
            if (eol_hold && !(BLX && bl ? rd_dd : rd_d)) rgb<={8'b0,slot,13'b0};
        end

        // frame
        if (INTERLACE_FEATURE!=0 && active) begin seen_now<=seen_now || interlace; field_last<=interlace; end
        if (vs_edge) begin
            lines_last<=lines; lines<=0;
            if (LOG!=0) begin
                log_w4<={lines, hash, slot[2:0], pal, mode480i, bob, i480, fit,
                         fd_on, os_on, field_last, pal_frame};
                log_w5<={out_n, log_first, log_last, DOCK_TEST!=0 ? dk : 2'b0};
                log_w6<={hs_cnt, wlen, pic_x0, 2'b0};
                log_w7<={pic_any ? pic_y0 : 10'h3FF, pic_y1, pic_x1, 2'b0};
                log_seq<=!log_seq;
                hs_cnt<=0; out_n<=0; log_first<=0; log_last<=0; log_have<=0;
            end
            if (PIC) begin hash<=0; pic_any<=0; pic_y1<=0; pic_x0<=10'h3FF; pic_x1<=0; end
            if (FILL_CENTER!=0) begin
                u0<=nu0; u1<=nu1; fc_key<={pal, mode480i};
                fc_dark<=pic_any || fc_reset ? 4'd0 : fc_dark+1'b1;
                if (fc_move) fc_top<=fc_next;
                if (FILL_DISP!=0) dd_any<=1'b0;
                if (PAL_SHAPE!=0) begin
                    f240<=f240_want;
                    if (t240_want>t240+10'd1 || t240>t240_want+10'd1) t240<=t240_want;
                    if (fsh_mv) fsh<=fsh_want;
                end
            end
            if (ASPECT!=0) fit<=!stretch;
            if (OVERSCAN_BLANK!=0) os_on<=hide_overscan;
            if (DOCK_TEST!=0) dk<=dock_mode;
            if (BLX) we_on<=wide_even;
            if (PAL_TIMING!=0) begin pal_frame<=hs_n==9'd289; hs_n<=0; end
            if (INTERLACE_FEATURE!=0) begin
                seen_last<=seen_now; seen_now<=0;
                if (i480) rgb<={21'b0,field_last^(FIELD_INVERT==0)^field_swap,2'b10};
            end
        end
    end
endmodule
`default_nettype wire
