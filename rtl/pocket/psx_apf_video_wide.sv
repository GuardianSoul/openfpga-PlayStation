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
// OVERSCAN_BLANK: with hide_overscan (the "Hide Overscan" setting, taken
// at VS) the lines outside that window (0-7 and 232-239 of a 240-line
// frame, so woven rows 0-15 and 464-479 in 480i; 0-9 and 278-287 at PAL
// 288) are written black, so Fit (Original) shows the same picture as
// Fill but keeps 240/288 lines (DuckStation hides NTSC 16-23 and
// 248-255 of 16-256; stray lines at the top in Tekken 3 and Ape Escape
// EU). Lines are counted from VS as written, not after CROP_LINES' crop.
`default_nettype none
module psx_apf_video_wide #(parameter integer INTERLACE_FEATURE=0, parameter integer FIELD_INVERT=0,
    parameter integer PAL288=0, parameter integer HYBRID=0, parameter integer LEAD_LINE=0,
    parameter integer VS_ON_HS=0, parameter integer ASPECT=0, parameter integer CROP_LINES=0,
    parameter integer PAL_TIMING=0, parameter integer VIDEO_PROBE=0, parameter integer SLOT368=0,
    parameter integer DBL320=0, parameter integer FILL_DETECT=0,
    parameter integer OVERSCAN_BLANK=0) (
    input wire clk, ce,
    input wire hsync, vsync, hblank, vblank, interlace, blank, weave,
    input wire field_swap,
    input wire stretch,               // ASPECT: Stretch (10:9 slots) instead of Fit
    input wire hide_overscan,         // OVERSCAN_BLANK: "Hide Overscan" setting
    input wire [23:0] rgb_in,
    output reg hs=0, vs=0, de=0, skip=0,
    output reg [23:0] rgb=0
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
    wire [2:0] h_slot=mode480i ? 3'd1 : pal ? 3'd2 : 3'd0;
    reg [2:0] slot=0;
    reg [9:0] out_w=10'd640, lead=0;
    wire over_320=wlen>10'd320 && wlen<=10'd360;   // cropped to 320
    reg fit=1;   // ASPECT: Fit (not stretch), taken at VS so a frame is all one
    wire nat=HYBRID!=0 && !mode480i && fit && (ASPECT==0 || !pal);
    // SLOT368: CE period of the line being written and of the buffered line
    reg [3:0] cec=0, ce_p=0, ce_line=0;
    wire c368=SLOT368!=0 && ASPECT!=0 && HYBRID!=0 && ce_line==4'd7;
    wire use_368=nat && c368;
    wire use_320=DBL320==0 && nat && !c368 && (native_ok(wlen,10'd320) || over_320);
    wire use_256=nat && !c368 && native_ok(wlen,10'd256);
    wire use_512=nat && ASPECT==0 && !pal && native_ok(wlen,10'd512);
    wire [2:0] slot_aspect=!fit ? (mode480i ? (DBL320!=0 ? 3'd1 : SLOT368!=0 ? 3'd3 : 3'd6) : pal ? 3'd7 : 3'd5)
                         : mode480i ? 3'd3 : pal ? 3'd4 : use_320 ? 3'd1 : use_256 ? 3'd2 : use_368 ? 3'd6 : 3'd0;
    wire [2:0] slot_next=HYBRID==0 ? h_slot : ASPECT!=0 ? slot_aspect : mode480i ? 3'd3 : use_320 ? (pal ? 3'd4 : 3'd1) : use_256 ? (pal ? 3'd5 : 3'd2)
                       : use_512 ? 3'd7 : pal ? 3'd6 : 3'd0;
    // CROP_LINES window for this field, from the previous field's count
    wire [9:0] crop_h=pal && !mode480i ? 10'd288 : 10'd240;
    wire [9:0] crop_top=lines_last>crop_h ? (lines_last-crop_h)>>1 : 10'd0;
    wire keep_line=CROP_LINES==0 || (lines>=crop_top && lines<crop_top+crop_h);
    // FILL_DETECT: the fixed window, lines fd_t to fd_e-1
    wire pal_h=crop_h[8];   // 288
    wire [9:0] fd_t=pal_h ? 10'd10 : 10'd8;
    wire [9:0] fd_e=pal_h ? 10'd278 : 10'd232;
    wire fd_in=lines>=fd_t && lines<fd_e;
    wire fd_on=FILL_DETECT!=0 && !fit;
    reg os_on=0;   // hide_overscan, taken at VS
    wire os_line=OVERSCAN_BLANK!=0 && os_on && !fd_in;
    wire [9:0] w_next=use_368 ? 10'd368 : use_320 ? 10'd320 : use_256 ? 10'd256 : use_512 ? 10'd512 : OUT_W;
    wire dbl_next=HYBRID!=0 && ASPECT!=0 && !mode480i && !use_320 && !use_368 && (native_ok(wlen,10'd320) || over_320);
    wire native_next=use_368 || use_320 || use_256 || use_512 || dbl_next;
    wire over_368=wlen>10'd368;   // SLOT368: centre-cropped to 368

    // Read side: DDA over the source width
    reg rd_on=0, rd_d=0, out_d=0, native=0, pix_d=0, lead_pend=0, black=0;
    reg dbl=0, ph=0;   // dbl: each source pixel twice (ph: the second copy)
    reg [10:0] pw=0;   // picture width in output pixels
    reg [9:0] oi=0, s=0, acc=0;
    wire [10:0] acc_next={1'b0,acc}+wlen;
    wire in_pic=!native || ({1'b0,oi}>={1'b0,lead} && {1'b0,oi}<{1'b0,lead}+pw);
    reg [23:0] q=0;

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
        hs<=VS_ON_HS!=0 ? hs_pipe[3] : hsync && !hs_d;
        vs<=vs_edge;

        // write
        if (active && ce) begin
            mem[{wb,wx}]<=blank || os_line ? 24'h0 : rgb_in;
            if (wx!=10'h3FF) wx<=wx+1'b1;
        end
        if (SLOT368!=0 && active) begin
            if (ce) begin ce_p<=cec+1'b1; cec<=0; end else if (cec!=4'hF) cec<=cec+1'b1;
        end
        if (!active && active_d) begin
            ce_line<=ce_p;
            wlen<=wx; wx<=0; rb<=wb; wb<=~wb; pend<=keep_line;
            if (lines!=10'h3FF) lines<=lines+1'b1;
            if (fd_on) pend<=fd_in;
        end

        // read
        if (LEAD_LINE!=0 && vs_edge) lead_pend<=1;
        if (LEAD_LINE!=0 && lead_pend && hs_end && !pend) begin
            lead_pend<=0; rd_on<=1; oi<=0; black<=1;   // black lead line, previous slot and width
        end
        if (pend && hs_end) begin
            pend<=0; rd_on<=1; oi<=0; acc<=0; black<=0;
            slot<=slot_next; out_w<=w_next; native<=native_next;
            lead<=use_368 ? (over_368 ? 10'd0 : (10'd368-wlen)>>1) : over_320 ? 10'd0 : dbl_next ? 10'd320-wlen : (w_next-wlen)>>1;
            s<=use_368 ? (over_368 ? (wlen-10'd368)>>1 : 10'd0) : (use_320 || dbl_next) && over_320 ? {5'd0,wlen[5:1]} : 10'd0;
            dbl<=dbl_next; ph<=0; pw<=dbl_next ? {wlen,1'b0} : {1'b0,wlen};   // centre crop: 320 = 5*64, so (wlen-320)/2 = wlen[5:1]
        end
        if (rd_on) begin
            q<=mem[{rb,s}];
            pix_d<=in_pic && !black;
            if (native) begin if (in_pic) begin ph<=!ph; if (!dbl || ph) s<=s+1'b1; end end
            else if (acc_next>={1'b0,out_w}) begin s<=s+1'b1; acc<=acc_next-out_w; end else acc<=acc_next[9:0];
            oi<=oi+1'b1;
            if (oi==out_w-1'b1) rd_on<=0;
        end
        rd_d<=rd_on;
        out_d<=rd_d;
        de<=rd_d;
        skip<=0;
        rgb<=rd_d && pix_d ? q : 24'h0;
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
        if (!rd_d && out_d) rgb<={8'b0,slot,13'b0};   // end-of-line slot word

        // frame
        if (INTERLACE_FEATURE!=0 && active) begin seen_now<=seen_now || interlace; field_last<=interlace; end
        if (vs_edge) begin
            lines_last<=lines; lines<=0;
            if (ASPECT!=0) fit<=!stretch;
            if (OVERSCAN_BLANK!=0) os_on<=hide_overscan;
            if (PAL_TIMING!=0) begin pal_frame<=hs_n==9'd289; hs_n<=0; end
            if (INTERLACE_FEATURE!=0) begin
                seen_last<=seen_now; seen_now<=0;
                if (mode480i) rgb<={21'b0,field_last^(FIELD_INVERT==0)^field_swap,2'b10};
            end
        end
    end
endmodule
`default_nettype wire
