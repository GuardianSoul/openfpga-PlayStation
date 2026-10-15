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
`default_nettype none
module psx_apf_video #(parameter integer STEADY_INTERLACE=1, parameter integer SLOT_SELECT=0,
    parameter integer INTERLACE_FEATURE=0, parameter integer SLOT480=0, parameter integer FIELD_INVERT=0) (
    input wire clk, ce,
    input wire hsync, vsync, hblank, vblank, interlace, blank, weave,
    input wire field_swap,            // run-time flip of the APF field bit (menu setting)
    input wire [23:0] rgb_in,
    output reg hs=0, vs=0, de=0, skip=0,
    output reg [23:0] rgb=0
);
    reg hs_d=0, vs_d=0, hold=0;
    wire vs_edge=vsync && !vs_d;
    wire hold_now=vs_edge ? (STEADY_INTERLACE!=0 && interlace && !hold) : hold;
    wire active=!hblank && !vblank;
    reg active_d=0;
    reg [9:0] count=0;
    reg seen_now=0, seen_last=0, field_last=0;
    wire mode480i=INTERLACE_FEATURE!=0 && weave && (seen_now || seen_last);
    wire [2:0] slot240=count<=256 ? 3'd2 : count<=320 ? 3'd1 : count<=368 ? 3'd3 : count<=512 ? 3'd4 : 3'd0;
    wire [2:0] slot_now=SLOT480!=0 && mode480i ? (count<=368 ? 3'd7 : count<=512 ? 3'd6 : 3'd5) : slot240;
    always @(posedge clk) begin
        hs_d<=hsync; vs_d<=vsync;
        if (vs_edge) hold<=hold_now;
        hs<=hsync && !hs_d;
        vs<=vs_edge;
        de<=active;
        skip<=active && (!ce || hold_now);
        rgb<=active && !blank ? rgb_in : 24'h0;
        if (SLOT_SELECT!=0) begin
            active_d<=active;
            if (active && ce) count<=count+1'b1;
            if (!active && active_d) begin
                count<=0;
                rgb<={8'b0,slot_now,13'b0};
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
