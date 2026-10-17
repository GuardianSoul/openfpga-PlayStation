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
// Its pixels are CE-paced (skip on other clocks) like test8's Video test D;
// sent on every clock the lead line left stray pixels (0.5.3-test1).
// A line within 1/16 below a native width (320/256, and 512 at 240 lines)
// is sent at that width, centred with black; a line of 321-360 pixels is
// centre-cropped to 320 (FFVII's title is a little over 320 and was
// stretched, 2026-10-16; only 320 to keep the area small). Every other
// line, and every 480i line, is stretched to 640.
`default_nettype none
module psx_apf_video_wide #(parameter integer INTERLACE_FEATURE=0, parameter integer FIELD_INVERT=0,
    parameter integer PAL288=0, parameter integer HYBRID=0, parameter integer LEAD_LINE=0,
    parameter integer VS_ON_HS=0) (
    input wire clk, ce,
    input wire hsync, vsync, hblank, vblank, interlace, blank, weave,
    input wire field_swap,
    input wire [23:0] rgb_in,
    output reg hs=0, vs=0, de=0, skip=0,
    output reg [23:0] rgb=0
);
    localparam [9:0] OUT_W=10'd640;
    reg hs_d=0, vs_d=0, active_d=0;
    reg vs_wait=0;   // VS_ON_HS (as psx_apf_video): VS waits for the next HS start
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
    wire pal=PAL288!=0 && lines_last>242;
    // Output width and slot of the buffered line (chosen when its read starts)
    function automatic native_ok(input [9:0] w, input [9:0] n);
        native_ok=w<=n && w>=n-(n>>4);
    endfunction
    wire [2:0] h_slot=mode480i ? 3'd1 : pal ? 3'd2 : 3'd0;
    reg [2:0] slot=0;
    reg [9:0] out_w=10'd640, lead=0;
    wire over_320=wlen>10'd320 && wlen<=10'd360;   // cropped to 320
    wire use_320=HYBRID!=0 && !mode480i && (native_ok(wlen,10'd320) || over_320);
    wire use_256=HYBRID!=0 && !mode480i && native_ok(wlen,10'd256);
    wire use_512=HYBRID!=0 && !mode480i && !pal && native_ok(wlen,10'd512);
    wire [2:0] slot_next=HYBRID==0 ? h_slot : mode480i ? 3'd3 : use_320 ? (pal ? 3'd4 : 3'd1) : use_256 ? (pal ? 3'd5 : 3'd2)
                       : use_512 ? 3'd7 : pal ? 3'd6 : 3'd0;
    wire [9:0] w_next=use_320 ? 10'd320 : use_256 ? 10'd256 : use_512 ? 10'd512 : OUT_W;
    wire native_next=use_320 || use_256 || use_512;

    // Read side: DDA over the source width
    reg rd_on=0, rd_d=0, out_d=0, native=0, pix_d=0, lead_pend=0, black=0, adv_q=1;
    wire adv=!black || ce;   // the lead line advances on CE pixels only
    reg [9:0] oi=0, s=0, acc=0;
    wire [10:0] acc_next={1'b0,acc}+wlen;
    wire in_pic=!native || (oi>=lead && oi<lead+wlen);
    reg [23:0] q=0;

    always @(posedge clk) begin
        hs_d<=hsync; vs_d<=vsync; active_d<=active;
        if (VS_ON_HS!=0) vs_wait<=!vs_edge && (vs_wait || vsync && !vs_d);
        hs<=hsync && !hs_d;
        vs<=vs_edge;

        // write
        if (active && ce) begin
            mem[{wb,wx}]<=blank ? 24'h0 : rgb_in;
            if (wx!=10'h3FF) wx<=wx+1'b1;
        end
        if (!active && active_d) begin
            wlen<=wx; wx<=0; rb<=wb; wb<=~wb; pend<=1;
            if (lines!=10'h3FF) lines<=lines+1'b1;
        end

        // read
        if (LEAD_LINE!=0 && vs_edge) lead_pend<=1;
        if (LEAD_LINE!=0 && lead_pend && hs_end && !pend) begin
            lead_pend<=0; rd_on<=1; oi<=0; black<=1;   // black lead line, previous slot and width
        end
        if (pend && hs_end) begin
            pend<=0; rd_on<=1; oi<=0; acc<=0; black<=0;
            slot<=slot_next; out_w<=w_next; native<=native_next;
            lead<=over_320 ? 10'd0 : (w_next-wlen)>>1;
            s<=use_320 && over_320 ? {5'd0,wlen[5:1]} : 10'd0;   // centre crop: 320 = 5*64, so (wlen-320)/2 = wlen[5:1]
        end
        adv_q<=adv;
        if (rd_on && adv) begin
            q<=mem[{rb,s}];
            pix_d<=in_pic && !black;
            if (native) begin if (in_pic) s<=s+1'b1; end
            else if (acc_next>={1'b0,out_w}) begin s<=s+1'b1; acc<=acc_next-out_w; end else acc<=acc_next[9:0];
            oi<=oi+1'b1;
            if (oi==out_w-1'b1) rd_on<=0;
        end
        rd_d<=rd_on;
        out_d<=rd_d;
        de<=rd_d;
        skip<=rd_d && !adv_q;
        rgb<=rd_d && pix_d ? q : 24'h0;
        if (!rd_d && out_d) rgb<={8'b0,slot,13'b0};   // end-of-line slot word

        // frame
        if (INTERLACE_FEATURE!=0 && active) begin seen_now<=seen_now || interlace; field_last<=interlace; end
        if (vs_edge) begin
            lines_last<=lines; lines<=0;
            if (INTERLACE_FEATURE!=0) begin
                seen_last<=seen_now; seen_now<=0;
                if (mode480i) rgb<={21'b0,field_last^(FIELD_INVERT==0)^field_swap,2'b10};
            end
        end
    end
endmodule
`default_nettype wire
