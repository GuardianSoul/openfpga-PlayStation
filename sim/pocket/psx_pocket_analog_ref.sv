// Regression reference: lower33-bit sampler at revision465a22a.
// SPDX-License-Identifier: MIT
// DualShock inputs for prepare --dualshock: the Pocket menu's "DualShock"
// checkbox and a docked controller's sticks, brought into the CPU clock.
//
// Sticks are believed for a docked pad of APF type 2 or 3 (analog) whose
// stick word is neither 0 nor all ones (what a pad without sticks sends;
// Parasite Eve II's menu, which reads the left stick, froze on a pinned
// one, #19); otherwise every axis is centred (0x80). The handheld (type 1)
// therefore always reports centred sticks. Byte order is APF's: [7:0] left X,
// [15:8] left Y, [23:16] right X, [31:24] right Y, 0x00 = left/up as on a
// real DualShock, so the bytes pass straight through.
//
// The word crosses as a held payload: in clk_74a the word is copied to hold
// and announced by toggling req, again as soon as the last copy was taken; clk1x takes hold after req's two-flop
// synchronizer, while hold cannot change until clk1x's ack returns. So no
// sample can be torn by bit skew, with two 33-bit stages instead of the
// earlier four (bit-synchronized meta, sync, last and output, 2026-10-17).
module analog_original (
    input wire clk1x,reset_n,              // reset_n: clk1x (cpu_locked)
    input wire clk_74a,
    input wire dualshock_setting,          // clk_74a level (bridge register)
    input wire [3:0] apf_type,             // cont1_key[31:28], clk_74a
    input wire [31:0] apf_joy,             // cont1_joy, clk_74a
    output reg dualshock=1'b0,
    output reg [31:0] sticks=32'h80808080
);
    wire believe=(apf_type==4'h3 || apf_type==4'h2) && apf_joy!=32'h0 && apf_joy!=32'hFFFFFFFF;
    wire [32:0] source={dualshock_setting, believe ? apf_joy : 32'h80808080};
    // clk_74a side
    reg [32:0] hold=33'h080808080;
    reg req=1'b0, busy=1'b0;
    reg ack=1'b0;                          // clk1x
    (* async_reg="true", preserve, dont_merge *) reg [1:0] ack_s=2'b00;
    always @(posedge clk_74a) begin
        ack_s<={ack_s[0],ack};
        if (busy) begin
            if (ack_s[1]==req) busy<=1'b0;
        end else begin   // resend continuously: no 33-bit compare
            hold<=source; req<=~req; busy<=1'b1;
        end
    end
    // clk1x side
    (* async_reg="true", preserve, dont_merge *) reg [1:0] req_s=2'b00;
    always @(posedge clk1x or negedge reset_n) begin
        if(!reset_n) begin
            req_s<=2'b00; ack<=1'b0; dualshock<=1'b0; sticks<=32'h80808080;
        end else begin
            req_s<={req_s[0],req};
            if (req_s[1]!=ack) begin dualshock<=hold[32]; sticks<=hold[31:0]; ack<=req_s[1]; end
        end
    end
endmodule
