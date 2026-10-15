// SPDX-License-Identifier: GPL-2.0-only
// Authored constant ADPCM, envelope and 24-voice accumulation oracle.
// CD inputs are zero, reverb is disabled, samples are preloaded into model RAM.
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
integer ticks=0,checks=0,envelope_checks=0,end_checks=0,attack_steps=0,attack_progress=0;
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
function integer expected(input integer nibble,input integer shift,input integer channel_gain);
 integer pcm,interpolated,enveloped;
 pcm=(nibble*4096) >>> (shift>12 ? 9:shift);
 interpolated=(pcm*32640)>>>15;
 enveloped=(interpolated*32767)>>>15;
 expected=((((enveloped*channel_gain)>>>15)*32766)>>>15);
endfunction
reg [15:0] env,ended;
integer nibble,shift,gain_r;
initial begin
 // Direct preload bypasses the SPU's RAM-write/cache path. Keep fixture blocks
 // immutable at distinct addresses so cases never reuse rewritten sample RAM.
 repeat(4) @(negedge clk1x);
 for(integer trial=0;trial<51;trial++) begin
  nibble=trial<48 ? (trial%16)-8:7;
  shift=trial<16 ? 0:trial<32 ? 5:trial<48 ? 12:trial-35;
  memory.data[(trial+1)*4096]=8'(shift);memory.data[(trial+1)*4096+1]=7;
  for(integer i=2;i<16;i++) memory.data[(trial+1)*4096+i]=8'((nibble&15)*17);
 end
 for(integer v=0;v<24;v++) begin
  memory.data[(v+64)*4096]=0;memory.data[(v+64)*4096+1]=7;
  for(integer i=2;i<16;i++) memory.data[(v+64)*4096+i]=8'h77;
 end
 for(integer trial=0;trial<51;trial++) begin
  attack_progress=0;
  cold_reset();
  nibble=trial<48 ? (trial%16)-8:7;
  shift=trial<16 ? 0:trial<32 ? 5:trial<48 ? 12:trial-35;
  gain_r=trial%2==0 ? 32766: -16384;
  write_reg(10'h180,16'h3fff);write_reg(10'h182,16'h3fff);
  write_reg(10'h000,16'h3fff);write_reg(10'h002,trial%2==0 ? 16'h3fff:16'h6000);
  write_reg(10'h004,16'h1000);write_reg(10'h006,16'((trial+1)*512));
  write_reg(10'h008,16'h00ff);write_reg(10'h00a,16'h1fc0);write_reg(10'h00e,16'((trial+1)*512));
  write_reg(10'h1aa,16'hc000);write_reg(10'h188,1);
  for(integer sample=0;sample<40;sample++) begin
   @(posedge spu_tick);@(negedge clk1x);read_reg(12,env);
   if(sample<8) begin
    if(attack_progress==0 && env==16'h3800) begin attack_progress=1;attack_steps++;end
    else if(attack_progress==1) begin
     if(env!=16'h7000) $fatal(1,"Fast attack second step mismatch");attack_progress=2;attack_steps++;
    end else if(attack_progress==2) begin
     if(env!=16'h7fff) $fatal(1,"Fast attack saturation mismatch");attack_progress=3;attack_steps++;
    end
   end
   if(sample>=8) begin
    if(attack_progress!=3) $fatal(1,"Fast attack steps incomplete");
    if(env!=16'h7fff) $fatal(1,"Voice attack/sustain did not settle trial=%d env=%x",trial,env);
    if($signed(sound_out_left)!==expected(nibble,shift,32766) || $signed(sound_out_right)!==expected(nibble,shift,gain_r))
     $fatal(1,"Voice PCM mismatch trial=%d sample=%d got=%d/%d expected=%d/%d",trial,sample,$signed(sound_out_left),$signed(sound_out_right),expected(nibble,shift,32766),expected(nibble,shift,gain_r));
    checks+=2;envelope_checks++;
   end
  end
  read_reg(10'h19c,ended);if(!(ended&1)) $fatal(1,"Voice loop-end flag missing trial=%d",trial);end_checks++;
  write_reg(10'h18c,1);
  for(integer sample=0;sample<12;sample++) begin
   @(posedge spu_tick);@(negedge clk1x);read_reg(12,env);
   if(sample>=8) begin
    if(env!=0 || sound_out_left!=0 || sound_out_right!=0) $fatal(1,"Key-off release did not mute trial=%d",trial);
    checks+=2;envelope_checks++;
   end
  end
 end
 cold_reset();
 write_reg(10'h180,16'h1000);write_reg(10'h182,16'h1000);
 for(integer v=0;v<24;v++) begin
  write_reg(v*16+0,16'h0800);write_reg(v*16+2,v%2 ? 16'h7800:16'h0800);
  write_reg(v*16+4,16'h1000);write_reg(v*16+6,16'((v+64)*512));
  write_reg(v*16+8,16'h00ff);write_reg(v*16+10,16'h1fc0);write_reg(v*16+14,16'((v+64)*512));
 end
 write_reg(10'h1aa,16'hc000);write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
 for(integer n=0;n<160;n++) begin
  @(posedge spu_tick);@(negedge clk1x);
  if(n>=32) begin
   if($signed(sound_out_left)!=21414 || $signed(sound_out_right)!= -3)
    $fatal(1,"24-voice accumulation mismatch n=%d left=%d right=%d",n,$signed(sound_out_left),$signed(sound_out_right));
   checks+=2;
  end
 end
 $display("PASS SPU voice cases=%d pcm_checks=%d envelope_checks=%d loop_checks=%d max_voices=24 accumulation_checks=256 attack_steps=%d",51,checks,envelope_checks,end_checks,attack_steps);$finish;
end
initial begin repeat(2500000) @(negedge clk1x);$fatal(1,"Voice overall timeout");end
endmodule
