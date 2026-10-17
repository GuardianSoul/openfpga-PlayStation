// SPDX-License-Identifier: MIT
// Drop-in for psx_pocket_pad (same module name; extra ports) that adds Select
// combos for the handheld, which only has L and R. While Select is held, each
// key enabled in combo_enable (L, R, Y, A, X, B) is held back from the game and
// reported on combos[] instead. The default layout (buttons[]) routes
//   Select+L = L2, Select+R = R2, Select+Y (Square) = L3, Select+A (Circle) = R3;
// prepare --shoulder-setting routes combos[] through menu settings instead.
// Select itself is held back so a combo never shows the game a Select press:
// a short tap reaches the game as a pulse when Select is released (a press
// shorter than the brush window is dropped: the Pocket's Select sits under
// the D-pad thumb, and a brush stretched to a full pulse opened Resident
// Evil's option screen while walking, 2026-10-17), and Select
// held alone longer than the hold window passes through (combos then end
// until Select is released). With every combo disabled, Select passes
// straight through. A docked controller's own L2/R2/L3/R3 bits pass through
// (docked[] carries them alone). Button levels are independent bits,
// synchronized into the free-running CPU clock as in psx_pocket_pad;
// combo_enable must already be in clk1x. dpad_combo_enable does the same for
// the four D-pad directions together (dpad_combos: up, down, left, right;
// prepare --stick-settings), tie it low otherwise.
module psx_pocket_pad #(
    parameter HOLD_BITS=25, // 2^25 clk1x cycles: 0.99 s at 33.8688 MHz, time to reach L/R/Y/A
    parameter TAP_BITS=22,  // tap pulse: 124 ms, several frames of pad polls
    parameter BRUSH_BITS=20 // shorter Select presses are no tap: 31 ms, under two frames
) (
    input wire clk1x,reset_n,input wire [15:0] apf_keys,
    input wire [5:0] combo_enable,   // L, R, Y, A, X, B
    input wire dpad_combo_enable,
    output wire [15:0] buttons,
    output wire [5:0] combos,
    output wire [3:0] dpad_combos,   // up, down, left, right
    output wire [3:0] docked         // L2, R2, L3, R3 of a docked controller
);
    localparam A=4,B=5,X=6,Y=7,L1=8,R1=9,L2=10,R2=11,L3=12,R3=13,SELECT=14;
    (* async_reg="true", preserve, dont_merge *) reg [15:0] keys_meta=0,keys_cpu=0;
    reg held=0,combo=0,passed=0,tap=0;
    reg [HOLD_BITS-1:0] timer=0;
    wire select=keys_cpu[SELECT];
    wire brush=timer<(64'd1<<BRUSH_BITS); // Select held shorter than the brush window
    wire enabled=|combo_enable|dpad_combo_enable;
    wire modifier=select&!passed&enabled;
    wire [5:0] keys={keys_cpu[B],keys_cpu[X],keys_cpu[A],keys_cpu[Y],keys_cpu[R1],keys_cpu[L1]};
    wire combo_key=|(keys&combo_enable)|(|keys_cpu[3:0]&dpad_combo_enable);
    wire [5:0] held_back=combo_enable&{6{modifier}};
    always @(posedge clk1x or negedge reset_n) begin
        if(!reset_n) begin
            keys_meta<=0;keys_cpu<=0;held<=0;combo<=0;passed<=0;tap<=0;timer<=0;
        end else begin
            keys_meta<=apf_keys;keys_cpu<=keys_meta;held<=select;
            if(select&!held) begin
                combo<=0;passed<=0;tap<=0;timer<=0;
            end else if(select) begin
                if(combo_key&!passed) combo<=1;
                if(!combo&!combo_key&!passed) begin
                    timer<=timer+1'b1;
                    if(&timer) passed<=1;
                end
            end else if(held) begin
                if(!combo&!passed&!brush) tap<=1;
                timer<=0;
                passed<=0;
            end else if(tap) begin
                timer<=timer+1'b1;
                if(&timer[TAP_BITS-1:0]) tap<=0;
            end
        end
    end
    assign combos=keys&held_back;
    wire dpad_held=dpad_combo_enable&modifier;
    assign dpad_combos=keys_cpu[3:0]&{4{dpad_held}};
    assign docked=keys_cpu[R3:L2];
    assign buttons[3:0]=keys_cpu[3:0]&{4{!dpad_held}};
    assign buttons[A]=keys_cpu[A]&!held_back[3];
    assign buttons[B]=keys_cpu[B]&!held_back[5];
    assign buttons[X]=keys_cpu[X]&!held_back[4];
    assign buttons[Y]=keys_cpu[Y]&!held_back[2];
    assign buttons[L1]=keys_cpu[L1]&!held_back[0];
    assign buttons[R1]=keys_cpu[R1]&!held_back[1];
    assign buttons[L2]=keys_cpu[L2]|combos[0];
    assign buttons[R2]=keys_cpu[R2]|combos[1];
    assign buttons[L3]=keys_cpu[L3]|combos[2];
    assign buttons[R3]=keys_cpu[R3]|combos[3];
    assign buttons[SELECT]=enabled ? select&passed|tap : select;
    assign buttons[15]=keys_cpu[15];
endmodule
