// SPDX-License-Identifier: GPL-2.0-only
// Authored fixed-volume CD mixing oracle; voices and reverb contributions zero.
`timescale 1fs/1fs
module tb_spu_mix;
logic clk1x=1;
logic clk2x=1;
logic clk2xIndex=0;
logic ce=1;
logic reset=1;
logic SPUon=1;
logic SPUIRQTrigger=0;
logic useSDRAM=1;
logic REPRODUCIBLESPUIRQ=1;
logic REPRODUCIBLESPUDMA=0;
logic REVERBOFF=1;
logic cpuPaused=0;
logic spu_tick=0;
logic [15:0] cd_left=0;
logic [15:0] cd_right=0;
logic irqOut=0;
logic sound_timeout=0;
logic [15:0] sound_out_left=0;
logic [15:0] sound_out_right=0;
logic [9:0] bus_addr=0;
logic [15:0] bus_dataWrite=0;
logic bus_read=0;
logic bus_write=0;
logic [15:0] bus_dataRead=0;
logic spu_dmaRequest=0;
logic dma_read=0;
logic [15:0] dma_readdata=0;
logic dma_write=0;
logic [15:0] dma_writedata=0;
logic [31:0] sdram_dataWrite=0;
logic [18:0] sdram_Adr=0;
logic [3:0] sdram_be=0;
logic sdram_rnw=0;
logic sdram_ena=0;
logic [31:0] sdram_dataRead=0;
logic sdram_done=0;
logic mem_request=0;
logic [7:0] mem_BURSTCNT=0;
logic [19:0] mem_ADDR=0;
logic [63:0] mem_DIN=0;
logic [7:0] mem_BE=0;
logic mem_WE=0;
logic mem_RD=0;
logic mem_ack=0;
logic [63:0] mem_DOUT=0;
logic mem_DOUT_READY=0;
logic SS_reset=1;
logic loading_savestate=0;
logic [31:0] SS_DataWrite=0;
logic [8:0] SS_Adr=0;
logic SS_wren=0;
logic SS_rden=0;
logic [31:0] SS_DataRead=0;
logic SS_idle=0;
logic [15:0] SS_RAM_dataWrite=0;
logic [18:0] SS_RAM_Adr=0;
logic SS_RAM_request=0;
logic SS_RAM_rnw=0;
logic [15:0] SS_RAM_dataRead=0;
logic SS_RAM_done=0;
SPU_ENTITY spu_core(.clk1x(clk1x),.clk2x(clk2x),.clk2xIndex(clk2xIndex),.ce(ce),.reset(reset),.SPUon(SPUon),.SPUIRQTrigger(SPUIRQTrigger),.useSDRAM(useSDRAM),.REPRODUCIBLESPUIRQ(REPRODUCIBLESPUIRQ),.REPRODUCIBLESPUDMA(REPRODUCIBLESPUDMA),.REVERBOFF(REVERBOFF),.cpuPaused(cpuPaused),.spu_tick(spu_tick),.cd_left(cd_left),.cd_right(cd_right),.irqOut(irqOut),.sound_timeout(sound_timeout),.sound_out_left(sound_out_left),.sound_out_right(sound_out_right),.bus_addr(bus_addr),.bus_dataWrite(bus_dataWrite),.bus_read(bus_read),.bus_write(bus_write),.bus_dataRead(bus_dataRead),.spu_dmaRequest(spu_dmaRequest),.dma_read(dma_read),.dma_readdata(dma_readdata),.dma_write(dma_write),.dma_writedata(dma_writedata),.sdram_dataWrite(sdram_dataWrite),.sdram_Adr(sdram_Adr),.sdram_be(sdram_be),.sdram_rnw(sdram_rnw),.sdram_ena(sdram_ena),.sdram_dataRead(sdram_dataRead),.sdram_done(sdram_done),.mem_request(mem_request),.mem_BURSTCNT(mem_BURSTCNT),.mem_ADDR(mem_ADDR),.mem_DIN(mem_DIN),.mem_BE(mem_BE),.mem_WE(mem_WE),.mem_RD(mem_RD),.mem_ack(mem_ack),.mem_DOUT(mem_DOUT),.mem_DOUT_READY(mem_DOUT_READY),.SS_reset(SS_reset),.loading_savestate(loading_savestate),.SS_DataWrite(SS_DataWrite),.SS_Adr(SS_Adr),.SS_wren(SS_wren),.SS_rden(SS_rden),.SS_DataRead(SS_DataRead),.SS_idle(SS_idle),.SS_RAM_dataWrite(SS_RAM_dataWrite),.SS_RAM_Adr(SS_RAM_Adr),.SS_RAM_request(SS_RAM_request),.SS_RAM_rnw(SS_RAM_rnw),.SS_RAM_dataRead(SS_RAM_dataRead),.SS_RAM_done(SS_RAM_done));
logic clk3x=1;
always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #4920850 clk3x=~clk3x;
reg toggle=0,sampled=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xIndex<=sampled==toggle;end
mister_sdram_model #(.MEMORY_BYTES(524288),.DOREFRESH(0)) memory(
 .clk(clk1x),.clk3x(clk3x),.refresh(1'b0),.addr({8'b0,sdram_Adr}),.req(sdram_ena),
 .ram_dma(1'b0),.ram_dmacnt(2'b0),.ram_iscache(1'b0),.rnw(sdram_rnw),.be(sdram_be),.di(sdram_dataWrite),
 .do32(sdram_dataRead),.done(sdram_done),.cache_wr(),.cache_data(),.cache_addr(),
 .dma_wr(),.dma_data(),.reqprocessed(),.ram_dmafifo_adr(23'b0),.ram_dmafifo_data(32'b0),
 .ram_dmafifo_empty(1'b1),.ram_dmafifo_read());
integer checks=0,ticks=0;
integer main_l,main_r,vol_l,vol_r,control;
integer cycles=0,last_tick=-1,tick_period_checks=0;
always @(posedge clk1x) cycles++;
always @(posedge spu_tick) begin
 if(last_tick>=0) begin
  if(cycles-last_tick!=768) $fatal(1,"SPU sample tick period changed");
  tick_period_checks++;
 end
 last_tick=cycles;ticks++;
end
task write_reg(input [9:0] address,input [15:0] data);
 @(negedge clk1x);bus_addr=address;bus_dataWrite=data;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
task settle;
 repeat(8) begin @(posedge spu_tick);@(negedge clk1x);end
endtask
function integer expected(input integer sample,input integer cd_volume,input integer main_volume);
 integer stage;
 stage=(sample*cd_volume)>>>15;
 expected=(stage*main_volume)>>>15;
 if(expected>32767) expected=32767;
 if(expected< -32768) expected= -32768;
endfunction
initial begin
 repeat(100) @(negedge clk1x);reset=0;SS_reset=0;
 repeat(100) @(negedge clk1x);
 for(integer mode=0;mode<6;mode++) begin
  control=16'hc001;
  case(mode)
   0: begin main_l=16384;main_r=16384;vol_l=16384;vol_r=16384;end
   1: begin main_l=32766;main_r=32766;vol_l=32767;vol_r=32767;end
   2: begin main_l= -16384;main_r=16384;vol_l=16384;vol_r= -16384;end
   3: begin main_l=0;main_r=16384;vol_l=32767;vol_r=32767;end
   4: begin main_l=16384;main_r= -16384;vol_l=0;vol_r= -32768;end
   5: begin main_l=16384;main_r=16384;vol_l=16384;vol_r=16384;control=16'hc000;end
  endcase
  write_reg(10'h180,16'((main_l&65535)>>1));write_reg(10'h182,16'((main_r&65535)>>1));
  write_reg(10'h1b0,16'(vol_l));write_reg(10'h1b2,16'(vol_r));write_reg(10'h1aa,16'(control));
  for(integer i=0;i<32;i++) begin
  cd_left=16'((i*17777)-32768);cd_right=16'((i*7919)+1);
  settle();
  if($signed(sound_out_left)!==expected($signed(cd_left),(control&1) ? vol_l:0,main_l) ||
     $signed(sound_out_right)!==expected($signed(cd_right),(control&1) ? vol_r:0,main_r))
   $fatal(1,"SPU mix mismatch case=%d in=%d/%d out=%d/%d expected=%d/%d state=%d ticks=%d",i,$signed(cd_left),$signed(cd_right),$signed(sound_out_left),$signed(sound_out_right),expected($signed(cd_left),(control&1) ? vol_l:0,main_l),expected($signed(cd_right),(control&1) ? vol_r:0,main_r),spu_core.state,ticks);
  checks+=2;
  end
 end
 $display("PASS SPU CD mixer checks=%d ticks=%d period_checks=%d",checks,ticks,tick_period_checks);$finish;
end
initial begin repeat(2000000) @(negedge clk1x);$fatal(1,"SPU mixer timeout");end
endmodule
