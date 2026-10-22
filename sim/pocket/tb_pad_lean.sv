// SPDX-License-Identifier: MIT
// Lockstep: the APF template's io_pad_controller against its --pad-lean copy
// (scripts/prepare_pad_lean.py). Each drives its own pulled-up pad line; the
// bench answers both identically as the Pocket's controller MCU does (after a
// request, 12 words of 32 bits, 60 clocks a bit: low 20, data 20, high 20),
// sometimes with random glitches and long silences (timeouts), and compares
// both lines and every output each clock. +seed=N, +cycles=N.
`timescale 1ns/1ps
module tb_pad_lean;
    reg clk=0; always #1 clk=~clk;
    reg reset_n=0;
    reg drive=0, val=1;
    wire pad_a, pad_b;
    pullup(pad_a); pullup(pad_b);
    assign pad_a = drive ? val : 1'bz;
    assign pad_b = drive ? val : 1'bz;
    wire [31:0] k1a,k2a,k3a,k4a,j1a,j2a,j3a,j4a, k1b,k2b,k3b,k4b,j1b,j2b,j3b,j4b;
    wire [15:0] t1a,t2a,t3a,t4a, t1b,t2b,t3b,t4b;
    io_pad_controller u_a(.clk(clk),.reset_n(reset_n),.pad_1wire(pad_a),.cont1_key(k1a),.cont2_key(k2a),.cont3_key(k3a),.cont4_key(k4a),
        .cont1_joy(j1a),.cont2_joy(j2a),.cont3_joy(j3a),.cont4_joy(j4a),.cont1_trig(t1a),.cont2_trig(t2a),.cont3_trig(t3a),.cont4_trig(t4a));
    io_pad_controller_lean u_b(.clk(clk),.reset_n(reset_n),.pad_1wire(pad_b),.cont1_key(k1b),.cont2_key(k2b),.cont3_key(k3b),.cont4_key(k4b),
        .cont1_joy(j1b),.cont2_joy(j2b),.cont3_joy(j3b),.cont4_joy(j4b),.cont1_trig(t1b),.cont2_trig(t2b),.cont3_trig(t3b),.cont4_trig(t4b));
    wire [511:0] oa={pad_a,k1a,k2a,k3a,k4a,j1a,j2a,j3a,j4a,t1a,t2a,t3a,t4a,u_a.rx_timed_out};
    wire [511:0] ob={pad_b,k1b,k2b,k3b,k4b,j1b,j2b,j3b,j4b,t1b,t2b,t3b,t4b,u_b.rx_timed_out};
    integer cycles=0, mism=0, words=0, reqs=0,  // reqs: low phases on the reader's line it drove itself
            glitches=0, maxc=4000000, seed=1;
    reg [31:0] rs;
    function automatic [31:0] rnd(input integer d); begin rs=rs^(rs<<13); rs=rs^(rs>>17); rs=rs^(rs<<5); rnd=rs; end endfunction
    always @(posedge clk) begin
        cycles<=cycles+1;
        if(oa!==ob) begin mism=mism+1; if(mism<=5) $display("MISMATCH at %0d pad %b/%b", cycles, pad_a, pad_b); end
    end
    // counts requests the DUT sends (its line goes low while the bench is not driving)
    reg pa_q=1; always @(posedge clk) begin pa_q<=pad_a; if(pa_q && !pad_a && !drive) reqs<=reqs+1; end
    reg [3:0] st_q=0; integer nshow=0; always @(posedge clk) begin st_q<=u_a.state; if(st_q!=u_a.state && nshow<40 && $test$plusargs("trace")) begin nshow=nshow+1; $display("%0d state %0d -> %0d tr %0d",cycles,st_q,u_a.state,u_a.tr_state); end end
    integer txdone=0, polls=0, rxw=0; always @(posedge clk) begin if(u_a.rx_word_done) rxw<=rxw+1; if(u_a.tr_state==4'd4) txdone<=txdone+1; if(u_a.state==4'd2 && u_a.tx_word_done) polls<=polls+1; end
    task send_word(input [31:0] w); integer b; begin
        for(b=31;b>=0;b=b-1) begin
            drive=1; val=0; repeat(20) @(negedge clk);
            val=w[b]; repeat(20) @(negedge clk);
            val=1; repeat(20) @(negedge clk);
        end
        drive=0; words=words+1;
    end endtask
    integer n;
    initial begin
        if(!$value$plusargs("seed=%d",seed)) seed=1; void'($value$plusargs("cycles=%d",maxc));
        rs=32'h9E3779B9^seed; if(rs==0) rs=1;
        repeat(5) @(negedge clk); reset_n=1;
        while(cycles<maxc) begin
            // wait for the DUT to finish sending (its request ends driving high, then releases)
            @(negedge clk);
            if(u_a.tr_state==4'd4 /*TR_TX_DONE*/ && u_a.state==4'd2 /*ST_RX_BUTTON_1: a poll, not a heartbeat*/) begin
                repeat(30+rnd(0)%200) @(negedge clk);
                if(rnd(0)%64==0) repeat(2200000) @(negedge clk);       // silence: the reader times out (2^21 clocks)
                else for(n=0;n<12;n=n+1) begin
                    send_word(rnd(0));
                    repeat(5+rnd(0)%40) @(negedge clk);
                end
            end else if(rnd(0)%4000000==0 && u_a.tr_state==4'd1) begin   // rare: a stray pulse stalls the reader until its timeout
                // a stray pulse on the line
                drive=1; val=0; repeat(1+rnd(0)%70) @(negedge clk); drive=0; glitches=glitches+1;
            end
        end
        $display("cycles %0d, requests %0d (tx done %0d, polls %0d), words received %0d, cnt %0d state %0d, words sent %0d, glitches %0d, mismatches %0d", cycles, reqs, txdone, polls, rxw, u_a.cnt, u_a.state, words, glitches, mism);
        if(mism==0 && words>1000 && reqs>100) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
