// SPDX-License-Identifier: MIT
// PSX video to the APF scaler: one-clock HS/VS pulses, RGB zero outside DE
// (nonzero RGB on an HS with DE low selects scaler slots), skip on CE-low
// pixels. With STEADY_INTERLACE, fields that the GPU marks as the second
// field of a 480-line interlaced mode (videoout interlace = VerRes and the
// odd display field) keep their HS/VS/DE timing but every pixel is sent
// with skip set, so the scaler keeps the previous field's image instead of
// alternating fields offset by a line. Dropping VS/DE for such fields made
// the Pocket show a black screen (PSXEarly, 2026-10-14). Never two held
// fields in a row, so a stuck flag cannot freeze the picture.
// blank forces black pixels (keeping sync), e.g. while the CPU is held in
// reset: SDRAM keeps the previous run's VRAM until the reset clear.
// With SLOT_SELECT, the first clock after each DE falling edge carries the
// APF end-of-line word "set scaler slot" (RGB[23:13] slot, [12:3] zero,
// [2:0] function 0) for that line's width in CE pixels, as in video.json:
// 0 = 640, 1 = 320, 2 = 256, 3 = 368, 4 = 512. A 320-pixel line in the
// 640 slot showed squashed into the left half (FFVII credits, 2026-10-14).
// With INTERLACE_FEATURE, each VS pulse carries the APF frame feature bits
// for 480i (RGB[1] interlaced, RGB[2] odd field) so the scaler weaves the
// two 240-line fields. The GPU's interlace output is VerRes AND the display
// field, so it toggles every field in 480i and stays 0 otherwise: a frame is
// 480i when it was high during the active lines of either of the last two
// frames, and the coming field is the opposite of the last one seen. With
// SLOT480, 480i lines use slots 5/6/7 (640/512/368 x 480) instead.
// FIELD_INVERT flips the odd/even field bit (field_swap at run time). weave low (a menu setting)
// sends 480i as plain 240-line fields instead: no feature bits, 240 slots.
// PAD_LINES (with SLOT_SELECT): a line shorter than its slot is continued
// with black pixels (CE-paced, DE high) up to the slot width, stopping at
// HS. Without it the scaler kept whatever an earlier mode left in the rest
// of the slot (stray pixels beside the picture, 2026-10-16).
// CENTER_LINES (with PAD_LINES): the padding is split, half before the
// line and half after, using the previous line's width, slot and start
// (CE pixels after HS ends), so a picture narrower than its slot is centred
// (GT2's title, 2026-10-16). Padding pixels go out on every clock, not on
// CE, so even a 368-wide line fits half of a 640 slot's padding into the
// back porch; the leading part is still limited to the back porch.
// VS_AT_VBLANK: the APF frame starts (VS) when vblank rises after the last
// active line instead of on the PSX vsync. In some modes the GPU's vsync
// came after the first active line, so the scaler wrote that line as one
// extra row below the previous frame and the picture lost its top line
// (probe1 photos, 2026-10-16: the stray row under FFVII's disc prompt).
// PAL288 (with SLOT480): slot 6 stays 512x480, slot 7 is 320x288 for PAL
// frames (more than 242 lines in the previous frame) instead of 368x480;
// 368-wide 480i lines use slot 5 (640x480, padded), PAL lines wider than 320
// the 240-line slots. A 240-line slot cut PAL's bottom rows (Wipeout,
// 2026-10-16); the two spare slots hold 512x480 and 320x288.
// SLOT6_368 (with PAL288): slot 6 is 368x480 instead of 512x480, so
// 368-wide 480i pictures (Tekken 3) fill their slot; 512-wide 480i lines
// use slot 5 (640x480, padded). In the 512 slot Tekken 3 showed at 72%
// width with bars (probe2 photo, 2026-10-16).
// VIDEO_TEST (diagnostic builds): video_test[1] swaps the VS source (PSX
// vsync <-> vblank rise), video_test[0] adds one black lead line (cyan with
// VIDEO_PROBE) after each VS, at the previous line's start and slot width,
// to test whether the APF scaler drops the first line of a frame.
// LEAD_LINE: the lead line of VIDEO_TEST value 1 (VS on the PSX vsync,
// one black line first), fixed and without the setting. The APF scaler
// drops the first DE line after VS, so without it a frame lost its top
// line and the bottom row of the slot kept an older frame's pixels (the
// stray row; probe2 photos on the BIOS screen, 2026-10-16). The lead line
// is CE-paced like test8's Video test D: sent on every clock (0.5.3-test1)
// it left black pixels top left and a flickering green run top right.
// 480i fields need it too: without it (0.5.3-test3) the BIOS screens'
// bottom row kept old pixels again. Wipeout's EU notice sits as low in
// 0.5.2 (photos, 2026-10-16), so its position is not the lead line's.
// VS_ON_HS: VS goes out with the next HS start. The GPU's vsync starts
// with HS in progressive modes and in one 480i field, but half a line later
// in the other (gpu_videoout_async, syncInterlace off), so the scaler saw
// two VS timings. HS goes out 4 clocks later than VS: APF wants HS at least
// 3 clocks after VS, and VS and HS on the same clock (rc1 to 0.5.3) left a
// leftover row above the picture at random (SotN PAL blue, white after a
// hard reboot; probe4 close-ups, 2026-10-17). Only 480i showed artefacts: black pixels top left of the
// white Sony screen, part of the lead line kept on Wipeout's PAL 480i EU
// notice (probe3 photo), and 0.5.2's stray bottom row (2026-10-16).
// CROP_LINES: a field taller than its slot (240 lines per field, 288 in
// the PAL 320x288 slot) is cut evenly at top and bottom: half the excess
// lines get no DE at the top, the rest at the bottom. Before, the slot kept the top rows and cut
// all the excess at the bottom, so PAL pictures sat low: Wipeout's PAL
// 480i EU notice (288-line fields in 640x480) and SotN's PAL title (rc1
// photos, 2026-10-17).
// BLANK_TOP: the first BLANK_TOP (1..3) active lines of each field go out
// black. The first is the top row the scaler shows, and on some frames part of it held the wrong
// pixels (white specks on the Sony screen, a flickering blue run in SotN
// US/EU; probe4 showed that row is the core's own first line, 2026-10-17).
// MID_W: width of the "368" slots (3, and 6/7 where they are 368 wide).
// The PSX 368 mode is wider with a wide display range: Ape Escape (PAL)
// sends 384x240 lines (probe photo, 2026-10-17), which went padded into the
// 512 slot, and the trailing padding was cut at HS, leaving stale pixels on
// the right. 384 holds both 368 and 384 lines.
// VIDEO_PROBE (diagnostic builds only) marks what the core writes: the first
// active line of a frame shows the previous frame's active line count as 10
// blocks of 16 pixels (MSB first, white 1, blue 0) on green, the second line
// the previous line's CE pixel count the same way on yellow, the last active
// line is red, and line padding is magenta instead of black. Any scaler area
// that keeps other colours was never written by this frame.
`default_nettype none
module psx_apf_video #(parameter integer STEADY_INTERLACE=1, parameter integer SLOT_SELECT=0,
    parameter integer INTERLACE_FEATURE=0, parameter integer SLOT480=0, parameter integer FIELD_INVERT=0,
    parameter integer PAD_LINES=0, parameter integer VIDEO_PROBE=0, parameter integer VS_AT_VBLANK=0,
    parameter integer PAL288=0,
    parameter integer CENTER_LINES=0, parameter integer VIDEO_TEST=0, parameter integer LEAD_LINE=0, parameter integer SLOT6_368=0,
    parameter integer VS_ON_HS=0, parameter integer CROP_LINES=0, parameter integer BLANK_TOP=0,
    parameter integer MID_W=368) (
    input wire clk, ce,
    input wire hsync, vsync, hblank, vblank, interlace, blank, weave,
    input wire field_swap,            // run-time flip of the APF field bit (menu setting)
    input wire [1:0] video_test,      // VIDEO_TEST: [1] swap VS source, [0] lead line
    input wire [23:0] rgb_in,
    output reg hs=0, vs=0, de=0, skip=0,
    output reg [23:0] rgb=0
);
    localparam integer LEAD_LOGIC=VIDEO_TEST!=0 || LEAD_LINE!=0;
    wire [1:0] vtest=LEAD_LINE!=0 ? 2'b01 : video_test;
    reg hs_d=0, vs_d=0, vb_d=1'b1, hold=0;
    wire vs_main=VS_AT_VBLANK!=0 ? vblank && !vb_d : vsync && !vs_d;
    wire vs_alt=VS_AT_VBLANK!=0 ? vsync && !vs_d : vblank && !vb_d;
    wire vs_raw=LEAD_LOGIC && vtest[1] ? vs_alt : vs_main;
    reg vs_wait=0;   // VS_ON_HS: a vsync seen mid-line, waiting for the next HS
    reg [3:0] hs_pipe=0;   // VS_ON_HS: HS goes out 4 clocks after its edge, so after VS
    wire vs_edge=VS_ON_HS!=0 ? hsync && !hs_d && (vs_raw || vs_wait) : vs_raw;
    wire hold_now=vs_edge ? (STEADY_INTERLACE!=0 && interlace && !hold) : hold;
    // CROP_LINES: a field with more active lines than its slot is high
    // (240 per field, 288 in the PAL 320x288 slot) loses half the excess at
    // the top and half at the bottom, not all at the bottom; crop holds them.
    reg crop=0, raw_d=0;
    reg [9:0] crop_top=0, crop_end=0;   // per field, from the last field's count
    wire raw_active=!hblank && !vblank;
    wire active=raw_active && !crop;
    reg active_d=0;
    reg [1:0] top_cnt=0;   // BLANK_TOP: active lines of this field still to blank
    wire top_line=top_cnt!=0;
    reg [9:0] count=0;
    reg seen_now=0, seen_last=0, field_last=0;
    wire mode480i=INTERLACE_FEATURE!=0 && weave && (seen_now || seen_last);
    wire [2:0] slot240=count<=256 ? 3'd2 : count<=320 ? 3'd1 : count<=MID_W ? 3'd3 : count<=512 ? 3'd4 : 3'd0;
    reg [9:0] pal_lines=0, pal_last=0;
    wire pal=PAL288!=0 && pal_last>242;   // NTSC is at most 240 lines; PAL games often use 256
    wire [2:0] slot_now=SLOT480!=0 && mode480i ? (count<=MID_W && PAL288==0 ? 3'd7 : count<=(SLOT6_368!=0 ? MID_W : 512) ? 3'd6 : 3'd5)
                      : pal && count<=320 ? 3'd7 : slot240;
    function automatic [9:0] slot_width(input [2:0] s);
        case (s) 3'd1: slot_width=320; 3'd2: slot_width=256; 3'd3: slot_width=MID_W; 3'd4: slot_width=512;
                 3'd6: slot_width=SLOT6_368!=0 ? MID_W : 512; 3'd7: slot_width=PAL288!=0 ? 320 : MID_W;
                 default: slot_width=640; endcase
    endfunction
    reg padding=0, line_d=0;
    reg [2:0] slot_q=0;
    // Leading padding (CENTER_LINES): pre_cnt pixels already sent this line.
    reg [11:0] hs_cnt=0, start_prev=0;   // clocks after HS ends (CENTER_LINES)
    reg [9:0] width_prev=0, pre_cnt=0;
    reg [2:0] slot_prev=0;
    function automatic [9:0] crop_h(input [9:0] n);   // CROP_LINES: slot height
        crop_h=PAL288!=0 && n>242 && !mode480i && width_prev<=320 ? 10'd288 : 10'd240;
    endfunction
    function automatic [9:0] top_skip(input [9:0] n);
        top_skip=n>crop_h(n) ? (n-crop_h(n))>>1 : 10'd0;
    endfunction
    reg prepad=0;
    wire [9:0] gap_prev=slot_width(slot_prev)>width_prev ? (slot_width(slot_prev)-width_prev)>>1 : 10'd0;
    wire [11:0] pre_n=gap_prev<start_prev ? {2'b0,gap_prev} : start_prev;
    wire pre_start=CENTER_LINES!=0 && PAD_LINES!=0 && SLOT_SELECT!=0 && !vblank && !crop && !active && !hsync
                   && pre_n!=0 && hs_cnt==start_prev-pre_n;
    wire pad_clk=CENTER_LINES!=0 || ce;   // padding pixel this clock
    wire pre_now=(pre_start || prepad) && !active;
    wire [9:0] sent=count+pre_cnt;
    wire pad_start=PAD_LINES!=0 && SLOT_SELECT!=0 && !active && active_d && !hsync && sent<slot_width(slot_now);
    wire pad_cont=padding && !hsync && sent<slot_width(slot_q);
    wire pad_now=pad_start || pad_cont;
    reg lead_pend=0, lead_arm=0, lead_on=0, lead_line=0;
    reg [9:0] lead_cnt=0;
    wire lead_start=LEAD_LOGIC && lead_arm && !active && !hsync && ce && hs_cnt>=start_prev;
    wire lead_now=(lead_start || lead_on) && !active;
    wire in_line=active || pad_now || pre_now || lead_now;
    reg [9:0] vline=0, vtotal=0, lastw=0, xpix=0;
    wire [3:0] bitno=4'd9-xpix[7:4];
    wire bitval=xpix[7:4]<10 && (vline==0 ? vtotal[bitno] : lastw[bitno]);
    wire [23:0] probe_rgb=vline==0 ? (xpix<160 ? (bitval ? 24'hFFFFFF : 24'h0000FF) : 24'h00FF00)
                        : vline==1 ? (xpix<160 ? (bitval ? 24'hFFFFFF : 24'h0000FF) : 24'hFFFF00)
                        : vline+1'b1==vtotal ? 24'hFF0000 : rgb_in;
    wire [2:0] slot_end=lead_line ? slot_prev : padding ? slot_q : slot_now;
    always @(posedge clk) begin
        hs_d<=hsync; vs_d<=vsync; vb_d<=vblank;
        if (VS_ON_HS!=0) vs_wait<=!vs_edge && (vs_wait || vs_raw);
        if (vs_edge) hold<=hold_now;
        hs_pipe<={hs_pipe[2:0], hsync && !hs_d};
        hs<=VS_ON_HS!=0 ? hs_pipe[3] : hsync && !hs_d;
        vs<=vs_edge;
        de<=in_line;
        skip<=in_line && ((active || lead_now ? !ce : !pad_clk) || hold_now);
        rgb<=active && !blank && !(BLANK_TOP!=0 && top_line) ? rgb_in : 24'h0;
        if (BLANK_TOP!=0) begin if (vs_edge) top_cnt<=BLANK_TOP; else if (!active && active_d && top_line) top_cnt<=top_cnt-1'b1; end
        if (VIDEO_PROBE!=0) rgb<=active && !blank ? probe_rgb : lead_now ? 24'h00FFFF : pad_now || pre_now ? 24'hFF00FF : 24'h0;
        raw_d<=raw_active;
        if (PAL288!=0 || CROP_LINES!=0) begin
            if (vs_edge) begin pal_last<=pal_lines; pal_lines<=0; end
            else if (!raw_active && raw_d && pal_lines!=10'h3FF) pal_lines<=pal_lines+1'b1;
        end
        if (CROP_LINES!=0) begin
            // pal_lines restarts at vs_edge and counts the same line ends,
            // so it is this field's line number; the bounds come once per
            // field from the count just finished.
            if (vs_edge) begin
                crop<=top_skip(pal_lines)!=0; crop_top<=top_skip(pal_lines);
                crop_end<=pal_lines>crop_h(pal_lines) ? top_skip(pal_lines)+crop_h(pal_lines) : 10'h3FF;
            end else if (!raw_active && raw_d) crop<=pal_lines+1'b1<crop_top || pal_lines+1'b1>=crop_end;
        end
        if (VIDEO_PROBE!=0) begin
            if (in_line && ce) xpix<=xpix+1'b1;
            if (!active && active_d) lastw<=count;
            if (!in_line && line_d) xpix<=0;
            if (!active && active_d) vline<=vline+1'b1;
            if (vs_edge) begin vtotal<=vline; vline<=0; end
        end
        if (SLOT_SELECT!=0) begin
            active_d<=active;
            line_d<=in_line;
            if (active ? ce : pad_now && pad_clk) count<=count+1'b1;
            if (LEAD_LOGIC) begin
                if (vs_edge && vtest[0]) lead_pend<=1;
                if (lead_pend && !hsync && hs_d && !active) begin lead_arm<=1; lead_pend<=0; end
                if (lead_start) begin lead_arm<=0; lead_on<=1; lead_line<=1; end
                if (lead_now && ce) begin   // CE-paced, as test8's Video test D
                    lead_cnt<=lead_cnt+1'b1;
                    if (lead_cnt+1'b1>=slot_width(slot_prev)) lead_on<=0;
                end
                if (active) begin lead_on<=0; lead_arm<=0; end
                if (!in_line && line_d) begin lead_line<=0; lead_cnt<=0; end
            end
            if (CENTER_LINES!=0 || LEAD_LOGIC) begin
                if (!hsync && hs_d) hs_cnt<=0; else if (hs_cnt!=12'hFFF) hs_cnt<=hs_cnt+1'b1;   // clocks from the end of HS
                if (active && !active_d) start_prev<=hs_cnt;
                if (!active && active_d) width_prev<=count;
                prepad<=pre_now && !(active || hsync);
                if (pre_now && pad_clk) pre_cnt<=pre_cnt+1'b1;
            end
            if (pad_start) slot_q<=slot_now;
            padding<=pad_now;
            if (!in_line && line_d) begin
                count<=0; pre_cnt<=0; slot_prev<=slot_end;
                rgb<={8'b0,slot_end,13'b0};
            end
        end
        if (INTERLACE_FEATURE!=0) begin
            if (active) begin seen_now<=seen_now || interlace; field_last<=interlace; end
            if (vs_edge) begin
                seen_last<=seen_now; seen_now<=0;
                if (mode480i) rgb<={21'b0,field_last^(FIELD_INVERT==0)^field_swap,2'b10};
            end
        end
    end
endmodule
`default_nettype wire
