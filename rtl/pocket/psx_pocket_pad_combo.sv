// SPDX-License-Identifier: MIT
// Drop-in for psx_pocket_pad (same module name and ports) that adds L2/R2/L3/R3
// for the handheld, which only has L and R. While Select is held:
//   L = L2, R = R2, Y (Square position) = L3, A (Circle position) = R3.
// Select itself is held back so a combo never shows the game a Select press:
// a short tap reaches the game as a pulse when Select is released, and Select
// held alone longer than the hold window passes through (combos then end
// until Select is released). A docked controller's own L2/R2/L3/R3 bits pass
// through unchanged. Button levels are independent bits, synchronized into
// the free-running CPU clock as in psx_pocket_pad.
module psx_pocket_pad #(
    parameter HOLD_BITS=25, // 2^25 clk1x cycles: 0.99 s at 33.8688 MHz, time to reach L/R/Y/A
    parameter TAP_BITS=22   // tap pulse: 124 ms, several frames of pad polls
) (
    input wire clk1x,reset_n,input wire [15:0] apf_keys,
    output wire [15:0] buttons
);
    localparam A=4,Y=7,L1=8,R1=9,L2=10,R2=11,L3=12,R3=13,SELECT=14;
    (* async_reg="true", preserve, dont_merge *) reg [15:0] keys_meta=0,keys_cpu=0;
    reg held=0,combo=0,passed=0,tap=0;
    reg [HOLD_BITS-1:0] timer=0;
    wire select=keys_cpu[SELECT];
    wire modifier=select&!passed;
    wire combo_key=keys_cpu[L1]|keys_cpu[R1]|keys_cpu[Y]|keys_cpu[A];
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
                if(!combo&!passed) begin tap<=1;timer<=0;end
                passed<=0;
            end else if(tap) begin
                timer<=timer+1'b1;
                if(&timer[TAP_BITS-1:0]) tap<=0;
            end
        end
    end
    assign buttons[3:0]=keys_cpu[3:0];
    assign buttons[A]=keys_cpu[A]&!modifier;
    assign buttons[6:5]=keys_cpu[6:5];
    assign buttons[Y]=keys_cpu[Y]&!modifier;
    assign buttons[L1]=keys_cpu[L1]&!modifier;
    assign buttons[R1]=keys_cpu[R1]&!modifier;
    assign buttons[L2]=keys_cpu[L2]|keys_cpu[L1]&modifier;
    assign buttons[R2]=keys_cpu[R2]|keys_cpu[R1]&modifier;
    assign buttons[L3]=keys_cpu[L3]|keys_cpu[Y]&modifier;
    assign buttons[R3]=keys_cpu[R3]|keys_cpu[A]&modifier;
    assign buttons[SELECT]=select&passed|tap;
    assign buttons[15]=keys_cpu[15];
endmodule
