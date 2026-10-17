// SPDX-License-Identifier: MIT
// Native SPU cache/reset port audit. Authored data only, no BIOS or discs.
// Case 5 is a one-clock API stress case below the generated boot gate's
// three-clock minimum; it is reported separately from the required cases.
`timescale 1ns/1ps
module tb;
reg clk1x=1,clk3x=1;
always #14.76255 clk1x=~clk1x;
always #4.92085 clk3x=~clk3x;
reg init=1,cpu_reset=0,req=0,rnw=1;
reg [18:0] addr=0;reg [31:0] data=0;reg [3:0] be=3;
wire done;wire [31:0] q;
wire [15:0] dq;wire [12:0] chip_a;wire [1:0] chip_ba,chip_dqm;
wire chip_we,chip_ras,chip_cas,chip_cke,chip_clock;
psx_native_memory dut(
 .clk1x(clk1x),.clk3x(clk3x),.init(init),.cpu_reset(cpu_reset),
 .ram_refresh(1'b0),.ram_ena(1'b0),.ram_rnw(1'b1),.ram_dma(1'b0),.ram_cache(1'b0),
 .ram_adr(25'b0),.ram_cntDMA(2'b0),.ram_dataWrite(32'b0),.ram_be(4'b0),.ram_done(),.ram_dataRead32(),
 .dma_wr(),.dma_data(),.dma_reqprocessed(),.cache_wr(),.cache_data(),.cache_addr(),
 .ram_dmafifo_adr(23'b0),.ram_dmafifo_data(32'b0),.ram_dmafifo_empty(1'b1),.ram_dmafifo_read(),
 .spuram_ena(req),.spuram_rnw(rnw),.spuram_adr(addr),.spuram_dataWrite(data),.spuram_be(be),.spuram_done(done),.spuram_dataRead(q),
 .load_valid(1'b0),.load_addr(25'b0),.load_halfwords(4'b0),.load_data(128'b0),.load_be(16'b0),.load_ready(),.load_rsp(),.load_rsp_ready(1'b1),
 .dram_dq(dq),.dram_a(chip_a),.dram_dqm(chip_dqm),.dram_ba(chip_ba),.dram_we_n(chip_we),.dram_ras_n(chip_ras),
 .dram_cas_n(chip_cas),.dram_cke(chip_cke),.dram_clk(chip_clock));
wire chip_clk_d;assign #2 chip_clk_d=chip_clock;
sdram_chip_model #(.CAS(3),.T_AC(5.5),.T_OH(2.5),.T_RET(2.0)) chip(
 .clk(chip_clk_d),.cke(chip_cke),.ncs(1'b0),.nras(chip_ras),.ncas(chip_cas),.nwe(chip_we),
 .a(chip_a),.ba(chip_ba),.dqm(chip_dqm),.dq(dq));
// Use an explicit authored all-zero power-on image for the ordinary
// SDRAM model; no model timing or command logic is changed.
initial begin #1;for(integer i=0;i<$size(chip.mem);i++)chip.mem[i]=0;end
reg [7:0] golden[0:524287];
integer checks=0,requests=0,caseno=0,cycles=0,maxwait=0;
always @(posedge clk1x)cycles++;
task access(input bit readop,input integer a,input [15:0] d,input [1:0] mask=3);
 integer n;
 @(negedge clk1x);addr=19'(a);data={16'b0,d};rnw=readop;be={2'b0,mask};req=1;requests++;
 @(negedge clk1x);req=0;n=0;
 while(!done && n<500)begin @(negedge clk1x);n++;end
 if(!done)$fatal(1,"SPU request did not finish case=%0d addr=%h cache_state=%0d word=%0d cancel=%0d",
    caseno,a,dut.spu_cache.state,dut.spu_cache.word,dut.spu_cache.cancel);
 if(n>maxwait)maxwait=n;
 if(readop)begin
  if(q[15:0]!=={golden[a+1],golden[a]})
   $fatal(1,"SPU read data mismatch case=%0d addr=%h got=%h expected=%h",caseno,a,q[15:0],{golden[a+1],golden[a]});
  checks++;
 end else begin
  if(mask[0])golden[a]=d[7:0];if(mask[1])golden[a+1]=d[15:8];
 end
 @(negedge clk1x);
endtask
task reset_busy(input bit readop,input bit common,input integer hold_cycles);
 @(negedge clk1x);addr=19'h1000;data=32'h5678;be=3;rnw=readop;req=1;
 @(negedge clk1x);req=0;
 if(readop) wait(dut.controller.kind==3 && dut.controller.op==dut.controller.OP_READ1);
 else wait(dut.controller.op==dut.controller.OP_WRITE && dut.controller.ch3_write);
 @(negedge clk1x);
 $display("RESET_BEGIN case=%0d state=%0d word=%0d ctrl_op=%0d kind=%0d cycle=%0d",
   caseno,dut.spu_cache.state,dut.spu_cache.word,dut.controller.op,dut.controller.kind,cycles);
 if(common)init=1;else cpu_reset=1;
 repeat(hold_cycles)@(negedge clk1x);
 if(common)begin init=0;wait(dut.controller.ready);repeat(16)@(negedge clk1x);end
 else cpu_reset=0;
 $display("RESET_END case=%0d state=%0d word=%0d cancel=%0d cycle=%0d",caseno,
   dut.spu_cache.state,dut.spu_cache.word,dut.spu_cache.cancel,cycles);
 repeat(2)@(negedge clk1x);
 access(1,19'h2000,0);
 if(!readop)begin
  // OP_WRITE was already accepted before reset. The physical write must
  // survive the handoff as well as allowing a new request to complete.
  golden[19'h1000]=8'h78;golden[19'h1001]=8'h56;
  access(1,19'h1000,0);
 end
endtask
initial begin
 for(integer i=0;i<524288;i++)golden[i]=0;
 if($value$plusargs("case=%d",caseno))begin end
 repeat(10)@(negedge clk3x);init=0;wait(dut.controller.ready);repeat(16)@(negedge clk1x);
 access(0,19'h1000,16'habcd);access(0,19'h2000,16'h1234);
 case(caseno)
 0:begin
   for(integer i=0;i<256;i++)access(0,i*2,16'(i*1783+951));
   for(integer pass=0;pass<3;pass++)for(integer i=0;i<256;i++)access(1,i*2,0);
   for(integer i=0;i<4;i++)access(0,i*19'h210,16'(i*539+3987));
   for(integer pass=0;pass<4;pass++)for(integer i=0;i<4;i++)access(1,i*19'h210,0);
   for(integer i=0;i<4;i++)begin access(0,19'h214,16'(i*373+17),2'(i));
      for(integer lane=0;lane<8;lane++)access(1,19'h210+lane*2,0);end
  end
 1:begin access(1,19'h1000,0);cpu_reset=1;repeat(50)@(negedge clk1x);cpu_reset=0;access(1,19'h2000,0);end
 2:reset_busy(1,0,50);
 3:reset_busy(0,0,50);
 4:reset_busy(1,1,50);
 5:reset_busy(1,0,1);
 endcase
 if(chip.errors)$fatal(1,"Chip protocol errors=%0d",chip.errors);
 $display("PASS NATIVE_CACHE_PORTS case=%0d requests=%0d reads_checked=%0d maxwait=%0d chiperrors=%0d",caseno,requests,checks,maxwait,chip.errors);
 $finish;
end
initial begin #10000000;$fatal(1,"Port bench timeout");end
endmodule
