// SPDX-License-Identifier: MIT
// Lockstep for psx_snac (run_snac_lockstep.py): the module against a pinned
// earlier version (psx_snac_ref) under random port selects, SIO bytes and bit
// clocks, ACK pulses, DAT/IRQ and sync; every output compared on every clock.
`timescale 1ns/1ps
module tb;
  reg clk=0; always #5 clk=~clk;
  reg p1=0,p2=0,ack=1,dat=1,irq=1,hs=0,vs=0,sel1=0,sel2=0,c9=1,bt=0; reg [7:0] tx=0;
  wire [6:0] uo_a,uo_b; wire i_a,i_b,rv_a,rv_b,an_a,an_b,ak_a,ak_b; wire [7:0] rb_a,rb_b;
  psx_snac_ref a(.clk_1x(clk),.snacPort1(p1),.snacPort2(p2),.ack_in(ack),.dat_in(dat),.irq_in(irq),.hs(hs),.vs(vs),.user_out(uo_a),
    .selectedPort1Snac(sel1),.selectedPort2Snac(sel2),.transmitValueSnac(tx),.clk9Snac(c9),.beginTransferSnac(bt),
    .irq10Snac(i_a),.receiveBufferSnac(rb_a),.receiveValidSnac(rv_a),.actionNextSnac(an_a),.ackSnac(ak_a));
  psx_snac b(.clk_1x(clk),.snacPort1(p1),.snacPort2(p2),.ack_in(ack),.dat_in(dat),.irq_in(irq),.hs(hs),.vs(vs),.user_out(uo_b),
    .selectedPort1Snac(sel1),.selectedPort2Snac(sel2),.transmitValueSnac(tx),.clk9Snac(c9),.beginTransferSnac(bt),
    .irq10Snac(i_b),.receiveBufferSnac(rb_b),.receiveValidSnac(rv_b),.actionNextSnac(an_b),.ackSnac(ak_b));
  integer rs=12345;   // seeded LCG: $urandom did not vary here
  function automatic integer rnd(input integer d); rs = rs*1103515245 + 12345; rnd = (rs >>> 8) & 32'h7fffff; endfunction
  integer n, errs=0, acts=0, rvs=0, seed=7, ackrun=0, c9cnt=0;
  always @(negedge clk) begin
    if ({uo_a,i_a,rb_a,rv_a,an_a,ak_a} !== {uo_b,i_b,rb_b,rv_b,an_b,ak_b}) begin errs=errs+1; if (errs<5) $display("mismatch at %0d", n); end
    if (an_a) acts=acts+1; if (rv_a) rvs=rvs+1;
  end
  initial begin
    for (n=0; n<20000000; n=n+1) begin
      @(posedge clk); #1;
      if (rnd(0)%400000==0 || n==0) begin p1=1; p2=rnd(0)%2; end
      if (rnd(0)%60000==0) begin sel1=rnd(0)%2; sel2=!sel1 && (rnd(0)%2); end
      c9cnt=c9cnt+1; if (c9cnt>=68) begin c9cnt=0; c9=~c9; end      // SIO bit clock
      bt = (rnd(0)%6000==0);
      if (bt) tx=rnd(0);
      // controller: an ack pulse now and then, held low a few clocks; random data and irq
      if (ackrun>0) begin ackrun=ackrun-1; ack=(ackrun==0); end else if (rnd(0)%900==0) begin ackrun=1+rnd(0)%12; ack=0; end
      if (rnd(0)%40==0) dat=rnd(0)%2;
      if (rnd(0)%5000==0) irq=~irq;
      if (rnd(0)%2000==0) hs=~hs; if (rnd(0)%50000==0) vs=~vs;
    end
    $display("SNAC lockstep clocks=%0d actions=%0d receives=%0d mismatches=%0d", n, acts, rvs, errs);
    if (errs) $fatal(1,"FAIL"); $display("PASS"); $finish;
  end
endmodule
