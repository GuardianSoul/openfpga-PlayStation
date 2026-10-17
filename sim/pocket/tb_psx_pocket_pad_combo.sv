// iverilog -g2012 -o build/tb_pad_combo sim/pocket/tb_psx_pocket_pad_combo.sv rtl/pocket/psx_pocket_pad_combo.sv && vvp build/tb_pad_combo
`timescale 1ns/1ps
module tb_psx_pocket_pad_combo;
    localparam A=4,X=6,Y=7,L1=8,R1=9,L2=10,R2=11,L3=12,R3=13,SELECT=14,START=15;
    localparam HOLD=1<<6,TAP=1<<4;
    localparam B=5;
    reg clk=0,reset_n=0;reg [15:0] keys=0;reg [5:0] enable=6'b001111;wire [15:0] buttons;wire [5:0] combos;wire [3:0] docked;reg dpad_enable=0;wire [3:0] dpad_combos;
    integer errors=0;
    psx_pocket_pad #(.HOLD_BITS(6),.TAP_BITS(4),.BRUSH_BITS(3)) dut(.clk1x(clk),.reset_n(reset_n),.apf_keys(keys),.combo_enable(enable),.dpad_combo_enable(dpad_enable),.dpad_combos(dpad_combos),.buttons(buttons),.combos(combos),.docked(docked));
    always #5 clk=!clk;
    task step(input integer n); repeat(n) @(posedge clk); #1; endtask
    task expect_buttons(input [15:0] want,input [8*40-1:0] what);
        if(buttons!==want) begin errors=errors+1;$display("FAIL %0s: buttons %h, want %h",what,buttons,want);end
    endtask
    task press(input [15:0] k); begin keys=k;step(3);end endtask
    integer i,seen;
    initial begin
        step(2);reset_n=1;step(2);
        // Plain buttons, including docked L2/R2/L3/R3, pass straight through.
        for(i=0;i<16;i=i+1) if(i!=SELECT) begin press(16'b1<<i);expect_buttons(16'b1<<i,"plain button");end
        press(16'hbfff);expect_buttons(16'hbfff,"all but select");
        press(0);
        // Select + L/R/Y/A give L2/R2/L3/R3 with no Select and no originals.
        press(1<<SELECT);expect_buttons(0,"select held back");
        press(1<<SELECT|1<<L1);expect_buttons(1<<L2,"select+L");
        press(1<<SELECT|1<<R1|1<<X);expect_buttons(1<<R2|1<<X,"select+R, X untouched");
        press(1<<SELECT|1<<Y);expect_buttons(1<<L3,"select+Y");
        press(1<<SELECT|1<<A|1<<START);expect_buttons(1<<R3|1<<START,"select+A");
        // After a combo, Select stays hidden however long it is held and on release.
        press(1<<SELECT);step(HOLD*2);expect_buttons(0,"select after combo");
        press(0);seen=0;for(i=0;i<TAP*2;i=i+1) begin step(1);if(buttons[SELECT]) seen=1;end
        if(seen) begin errors=errors+1;$display("FAIL select leaked after a combo");end
        // A short tap reaches the game as a pulse after release.
        press(1<<SELECT);step(10);press(0);expect_buttons(1<<SELECT,"tap pulse");
        seen=0;for(i=0;i<TAP*2;i=i+1) begin step(1);if(buttons[SELECT]) seen=seen+1;end
        if(seen<TAP-4||seen>TAP+2) begin errors=errors+1;$display("FAIL tap pulse length %0d",seen);end
        expect_buttons(0,"tap pulse ended");
        // A brush shorter than the brush window never reaches the game.
        keys=1<<SELECT;step(4);press(0);seen=0;for(i=0;i<TAP*2;i=i+1) begin step(1);if(buttons[SELECT]) seen=1;end
        if(seen) begin errors=errors+1;$display("FAIL brush became a tap");end
        // Held alone past the window, Select passes and L stays L1.
        press(1<<SELECT);step(HOLD+4);expect_buttons(1<<SELECT,"long select");
        press(1<<SELECT|1<<L1);expect_buttons(1<<SELECT|1<<L1,"select passed, L is L1");
        press(0);expect_buttons(0,"no tap after long hold");
        step(TAP*2);expect_buttons(0,"still released");
        // Tap pulse restarts cleanly into a new combo.
        press(1<<SELECT);step(10);press(0);press(1<<SELECT|1<<L1);expect_buttons(1<<L2,"combo during tap");
        press(0);
        // X/B combos are off by default; enabled, they appear on combos[] only.
        press(1<<SELECT|1<<X);expect_buttons(1<<X,"select+X while off");
        press(0);step(TAP*2);
        enable=6'b111111;
        press(1<<SELECT|1<<B);expect_buttons(0,"select+B hidden");
        if(combos!==6'b100000) begin errors=errors+1;$display("FAIL combos %b",combos);end
        press(0);step(TAP*2);
        // Docked L2 shows on docked[] and buttons, not on combos[].
        press(1<<L2);expect_buttons(1<<L2,"docked L2");
        if(docked!==4'b0001||combos!==0) begin errors=errors+1;$display("FAIL docked %b combos %b",docked,combos);end
        press(0);step(TAP*2);
        // D-pad combos (--stick-settings): held back and reported on dpad_combos.
        dpad_enable=1;press(1<<SELECT|1<<0|1<<3);expect_buttons(0,"select+up+right hidden");
        if(dpad_combos!==4'b1001) begin errors=errors+1;$display("FAIL dpad_combos %b",dpad_combos);end
        press(0);step(TAP*2);
        press(1<<2);expect_buttons(1<<2,"left alone");
        if(dpad_combos!==0) begin errors=errors+1;$display("FAIL dpad_combos without select %b",dpad_combos);end
        press(0);step(TAP*2);
        enable=0;press(1<<SELECT|1<<1);expect_buttons(0,"dpad combo with face combos off");
        if(dpad_combos!==4'b0010) begin errors=errors+1;$display("FAIL dpad-only combos %b",dpad_combos);end
        press(0);step(TAP*2);dpad_enable=0;enable=6'b111111;
        // Every combo off: Select passes straight through.
        enable=0;press(1<<SELECT);expect_buttons(1<<SELECT,"select direct");
        press(1<<SELECT|1<<L1);expect_buttons(1<<SELECT|1<<L1,"no combos");
        press(0);expect_buttons(0,"released");
        if(errors) $display("FAILED: %0d errors",errors); else $display("PASS psx_pocket_pad_combo");
        $finish;
    end
endmodule
