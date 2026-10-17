// SPDX-License-Identifier: MIT
// prepare --stick-settings: what the handheld's D-pad and Select combos send
// to a DualShock (2026-10-17). Menu lists, all in clk1x:
//   dpad_mode     "D-pad":            0 D-pad, 1 left stick, 2 right stick
//   sel_dpad_mode "SELECT + D-pad":   0 none, 1 left stick, 2 right stick, 3 D-pad
//   face_mode     "SELECT + A/B/X/Y": 0-3 the shoulder targets (routed in
//                 core_top), 4 left stick, 5 right stick (X up, B down,
//                 Y left, A right)
// A direction sent to a stick moves that axis fully (up/left 0x00,
// down/right 0xFF); an axis with no direction keeps the docked stick's value.
// With the DualShock checkbox off the pad is digital, so stick directions go
// to the D-pad instead. While L3 and R3 are both held (the pad's analog-mode
// switch is L3+R3+Up/Down, as in MiSTer's joypad_pad), every D-pad direction
// goes to the D-pad, so on the handheld Select+Y+A+Up/Down still switches
// analog mode on/off with the default lists. Direction words are {right, left, down, up}, as the
// pad's buttons[3:0]. Every input is a clk1x register; the outputs are logic.
module psx_pocket_stick_map (
    input wire dualshock,
    input wire l3r3,                // L3 and R3 both held (game-side keys)
    input wire [1:0] dpad_mode, sel_dpad_mode,
    input wire [2:0] face_mode,
    input wire [3:0] dpad,          // D-pad not held back by a combo
    input wire [3:0] dpad_combo,    // D-pad with Select held (held back)
    input wire [3:0] face_combo,    // Select + X, B, Y, A as up, down, left, right
    input wire [31:0] sticks_in,    // [7:0] LX, [15:8] LY, [23:16] RX, [31:24] RY
    output wire [31:0] sticks_out,
    output wire [3:0] dpad_out
);
    wire [1:0] dm=l3r3 ? 2'd0 : dpad_mode, sm=l3r3 ? 2'd3 : sel_dpad_mode;
    wire [3:0] left=(dm==2'd1 ? dpad : 4'd0) | (sm==2'd1 ? dpad_combo : 4'd0) | (face_mode==3'd4 ? face_combo : 4'd0);
    wire [3:0] right=(dm==2'd2 ? dpad : 4'd0) | (sm==2'd2 ? dpad_combo : 4'd0) | (face_mode==3'd5 ? face_combo : 4'd0);
    wire [3:0] pad=(dm==2'd0 ? dpad : 4'd0) | (sm==2'd3 ? dpad_combo : 4'd0);
    function [15:0] axes(input [3:0] dirs, input [15:0] base);
        axes={dirs[0] ? 8'h00 : dirs[1] ? 8'hFF : base[15:8], dirs[2] ? 8'h00 : dirs[3] ? 8'hFF : base[7:0]};
    endfunction
    assign sticks_out=dualshock ? {axes(right, sticks_in[31:16]), axes(left, sticks_in[15:0])} : sticks_in;
    assign dpad_out=dualshock ? pad : pad | left | right;
endmodule
