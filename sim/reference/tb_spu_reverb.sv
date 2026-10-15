// SPDX-License-Identifier: GPL-2.0-only
// Random-register reverb fixture for paired SPU comparisons: 24 voices with
// reverb on, random reverb configuration rewritten and read back while running.
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
logic REVERBOFF=0;
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
integer ticks=0,reads=0,nonzero_reads=0,rewrites=0,nonzero_out=0;
always @(posedge spu_tick) ticks++;
always @(posedge clk1x) if(!reset && !SS_reset && !spu_core.ss_voice_loading && sound_timeout)
 $fatal(1,"SPU voice schedule exceeded its sample period");
task write_reg(input [9:0] address,input [15:0] data);
 @(negedge clk1x);bus_addr=address;bus_dataWrite=data;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
task read_reg(input [9:0] address,output [15:0] data);
 @(negedge clk1x);bus_addr=address;bus_read=1;
 repeat(3) @(negedge clk1x);data=bus_dataRead;bus_read=0;
endtask
task cold_reset;
 @(negedge clk1x);reset=1;SS_reset=1;bus_read=0;bus_write=0;
 repeat(100) @(negedge clk1x);reset=0;SS_reset=0;
 wait(!spu_core.ss_voice_loading);repeat(8) @(negedge clk1x);
endtask
integer seed=1;
reg [15:0] value;
task random_reverb;
 // mBASE high in RAM, offsets small so buffers stay inside the work area.
 write_reg(10'h1a2,16'(16'hc000+($unsigned($random(seed))%16'h2000)));
 write_reg(10'h184,16'($unsigned($random(seed))));write_reg(10'h186,16'($unsigned($random(seed))));
 for(integer r=0;r<32;r++) begin
  if(r<10 || r>=30) write_reg(10'h1c0+r*2,16'($unsigned($random(seed))));
  else write_reg(10'h1c0+r*2,16'($unsigned($random(seed))%16'h0800));
 end
 rewrites++;
endtask
task read_reverb;
 for(integer r=0;r<35;r++) begin
  read_reg(r<32 ? 10'h1c0+r*2 : r==32 ? 10'h184 : r==33 ? 10'h186 : 10'h1a2,value);reads++;
  if(value!=0) nonzero_reads++;
  // Odd addresses in the block keep the original all-ones read.
  if(r%8==0) begin read_reg(10'h1c1+r*2,value);reads++;end
 end
endtask
always @(posedge spu_tick) if(sound_out_left!=0 || sound_out_right!=0) nonzero_out++;
initial begin
 repeat(4) @(negedge clk1x);
 for(integer v=0;v<24;v++) begin
  for(integer b=0;b<64;b++) begin
   memory.data[(v+64)*4096+b*16]=8'(($unsigned($random(seed))%5)<<4 | ($unsigned($random(seed))%13));
   memory.data[(v+64)*4096+b*16+1]=b==63 ? 8'h03 : 8'h02;
   for(integer i=2;i<16;i++) memory.data[(v+64)*4096+b*16+i]=8'($unsigned($random(seed)));
  end
 end
 for(integer b=0;b<524288;b++) memory_candidate.data[b]=memory.data[b];
 for(integer round=0;round<3;round++) begin
  cold_reset();
  // Reset must clear the whole block: read it back before any write.
  read_reverb();
  write_reg(10'h180,16'h3000);write_reg(10'h182,16'h3000);
  for(integer v=0;v<24;v++) begin
   write_reg(v*16+0,16'($unsigned($random(seed))%16'h4000));write_reg(v*16+2,16'($unsigned($random(seed))%16'h4000));
   write_reg(v*16+4,16'(16'h0400+($unsigned($random(seed))%16'h1000)));
   write_reg(v*16+6,16'((v+64)*512));write_reg(v*16+14,16'((v+64)*512));
   write_reg(v*16+8,16'($unsigned($random(seed))));write_reg(v*16+10,16'($unsigned($random(seed))));
  end
  random_reverb();
  write_reg(10'h198,16'hffff);write_reg(10'h19a,16'h00ff);
  write_reg(10'h1aa,16'hc080);write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
  for(integer n=0;n<700;n++) begin
   @(posedge spu_tick);@(negedge clk1x);
   if(n%97==0) random_reverb();
   if(n%61==0) read_reverb();
   if(n%13==0) begin ce=0;repeat(5) @(negedge clk1x);ce=1;end
   if(n%150==75) begin write_reg(10'h18c,16'h5555);write_reg(10'h18e,16'h0055);end
   if(n%150==120) begin write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);end
   if(n==350) write_reg(10'h1aa,16'hc000);
   if(n==400) write_reg(10'h1aa,16'hc080);
  end
 end
 if(nonzero_out<1000 || nonzero_reads<500) $fatal(1,"Reverb fixture too quiet out=%0d reads=%0d",nonzero_out,nonzero_reads);
 $display("PASS SPU reverb fixture samples=%0d nonzero_samples=%0d register_reads=%0d nonzero_reads=%0d rewrites=%0d",ticks,nonzero_out,reads,nonzero_reads,rewrites);
 $finish;
end
endmodule
