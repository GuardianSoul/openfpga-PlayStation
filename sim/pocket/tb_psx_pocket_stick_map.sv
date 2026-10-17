// SPDX-License-Identifier: MIT
// rtl/pocket/psx_pocket_stick_map.sv: every mode of the three menu lists.
`timescale 1ns/1ps
module tb_psx_pocket_stick_map;
    reg ds=1, l3r3=0; reg [1:0] dm=0, sm=1; reg [2:0] fm=3;
    reg [3:0] dpad=0, dcombo=0, fcombo=0; reg [31:0] sin=32'h11223344;
    wire [31:0] sout; wire [3:0] dout;
    integer errors=0;
    psx_pocket_stick_map dut(.dualshock(ds),.l3r3(l3r3),.dpad_mode(dm),.sel_dpad_mode(sm),.face_mode(fm),.dpad(dpad),
        .dpad_combo(dcombo),.face_combo(fcombo),.sticks_in(sin),.sticks_out(sout),.dpad_out(dout));
    task check(input [31:0] want_s, input [3:0] want_d, input [8*40-1:0] what);
        begin #1 if(sout!==want_s || dout!==want_d) begin errors=errors+1;
            $display("FAIL %0s: sticks %h want %h, dpad %b want %b",what,sout,want_s,dout,want_d); end end
    endtask
    initial begin
        // up=0 down=1 left=2 right=3
        dpad=4'b0001; check(32'h11223344,4'b0001,"default: D-pad is D-pad");
        dm=1; check(32'h11220044,4'b0000,"D-pad = left stick, up");
        dpad=4'b1010; check(32'h1122FFFF,4'b0000,"left stick down+right");
        dm=2; dpad=4'b0100; check(32'h11003344,4'b0000,"right stick left");
        dpad=0; dm=0;
        dcombo=4'b0010; check(32'h1122FF44,4'b0000,"Select+D-pad default left stick, down");
        sm=2; check(32'hFF223344,4'b0000,"Select+D-pad right stick, down");
        sm=3; check(32'h11223344,4'b0010,"Select+D-pad as D-pad");
        sm=0; check(32'h11223344,4'b0000,"Select+D-pad none");
        dcombo=0; sm=1;
        fcombo=4'b1001; check(32'h11223344,4'b0000,"face rows 0-3: no stick");
        fm=4; check(32'h112200FF,4'b0000,"Select+X/A on left stick: up+right");
        fm=5; check(32'h00FF3344,4'b0000,"Select+X/A on right stick");
        // L3+R3 held: D-pad directions go to the D-pad (analog-mode switch)
        fcombo=0; fm=3; dcombo=4'b0001; l3r3=1; check(32'h11223344,4'b0001,"L3+R3+Select+Up: D-pad up");
        dcombo=0; dm=1; dpad=4'b0010; check(32'h11223344,4'b0010,"L3+R3, D-pad as stick: D-pad down");
        l3r3=0; dm=0; dpad=0; fcombo=4'b1001; fm=5;
        // DualShock off: stick directions fall back to the D-pad
        ds=0; check(32'h11223344,4'b1001,"digital pad: face stick -> D-pad");
        fcombo=0; fm=3; dm=1; dpad=4'b0100; check(32'h11223344,4'b0100,"digital pad: D-pad mode stick -> D-pad");
        if(errors==0) $display("PASS psx_pocket_stick_map");
        $finish;
    end
endmodule
