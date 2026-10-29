// SPDX-License-Identifier: MIT
// rtl/pocket/psx_pocket_analog.sv: which APF controller types supply sticks,
// the held-payload crossing (only whole source words ever reach the game, even
// when the word changes on every clk_74a edge) and the DualShock checkbox level.
`timescale 1ns/1ps
module tb_psx_pocket_analog;
    reg clk=0,clk74=0,reset_n=0,setting=1;
    reg [3:0] type_=4'h1;
    reg [31:0] joy=32'h80808080;
    wire dualshock; wire [31:0] sticks;
    integer errors=0;
    always #14.76 clk=!clk;
    always #6.734 clk74=!clk74;
    psx_pocket_analog dut(.clk1x(clk),.reset_n(reset_n),.clk_74a(clk74),.dualshock_setting(setting),
        .apf_type(type_),.apf_joy(joy),.dualshock(dualshock),.sticks(sticks));
    task settle; repeat(20) @(posedge clk); endtask
    task check(input [31:0] want_sticks, input want_ds, input [8*40-1:0] what);
        begin
            settle;
            if(sticks!==want_sticks || dualshock!==want_ds) begin
                errors=errors+1;
                $display("FAIL %0s: sticks %h want %h, dualshock %b want %b",what,sticks,want_sticks,dualshock,want_ds);
            end
        end
    endtask
    initial begin
        repeat(3) @(posedge clk); reset_n=1;
        type_=4'h1; joy=32'h00FF1234; check(32'h80808080,1,"handheld: centred");
        type_=4'h3; check(32'h00FF1234,1,"type 3: sticks");
        joy=32'h0; check(32'h80808080,1,"type 3, word 0: centred");
        joy=32'hFFFFFFFF; check(32'h80808080,1,"type 3, all ones: centred");
        joy=32'h00FF1234;
        type_=4'h2; joy=32'h0; check(32'h80808080,1,"type 2, word 0: centred");
        joy=32'hFFFFFFFF; check(32'h80808080,1,"type 2, all ones: centred");
        joy=32'h7F8081C0; check(32'h7F8081C0,1,"type 2, sticks: believed");
        type_=4'h0; check(32'h80808080,1,"nothing: centred");
        type_=4'h3; joy=32'h11223344; check(32'h11223344,1,"type 3 again");
        // a word changing on every clk_74a edge: every value the game sees
        // is one of the words driven (never a mix of two)
        fork
            begin repeat(400) begin @(posedge clk74); #1 joy=(joy==32'h11223344) ? 32'hEEDDCCBB : 32'h11223344; end end
            begin repeat(400) begin @(posedge clk); #1 if(sticks!==32'h11223344 && sticks!==32'hEEDDCCBB) begin errors=errors+1; $display("FAIL torn word reached the game: %h",sticks); end end end
        join
        joy=32'h11223344; check(32'h11223344,1,"settles to the last word");
        setting=0; check(32'h11223344,0,"checkbox off");
        setting=1; check(32'h11223344,1,"checkbox on");
        reset_n=0; #1 if(sticks!==32'h80808080 || dualshock!==0) begin errors=errors+1; $display("FAIL reset values"); end
        if(errors==0) $display("PASS psx_pocket_analog");
        $finish;
    end
endmodule
