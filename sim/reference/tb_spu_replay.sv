// SPDX-License-Identifier: GPL-2.0-only
// Replays a software-emulator SPU trace (register writes, DMA RAM writes, CD
// input) into the RTL SPU and records its output once per sample tick.
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
integer ticks=0,checks=0,envelope_checks=0,end_checks=0,attack_steps=0,attack_progress=0;
always @(posedge spu_tick) ticks++;
integer overruns=0;
always @(posedge clk1x) if(!reset && !SS_reset && !spu_core.ss_voice_loading && sound_timeout) overruns++;
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
integer events,outs,n,sample_index=0,applied=0,dma_writes=0;
integer ev_sample,ev_type,ev_addr,ev_value,have_event,limit;
integer evshift=0; // +evshift=N applies events N samples earlier
initial if(!$value$plusargs("evshift=%d",evshift)) evshift=0;
`ifdef VOICE_PROBE
// Per-voice internals of the original SPU (GHDL-generated names), for one voice.
integer probe_voice=-1,probe_file;
initial begin if(!$value$plusargs("voice=%d",probe_voice)) probe_voice=-1; probe_file=$fopen("voice_out.txt","w"); end
always @(posedge clk1x) if(probe_voice>=0 && spu_core.index==probe_voice) begin
 if(spu_core.state==7) $fwrite(probe_file,"%0d A %0d %0d\n",sample_index,$signed(spu_core.sample),$signed(spu_core.adsrvolume));
 if(spu_core.state==9) $fwrite(probe_file,"%0d V %0d %0d %0d %0d\n",sample_index,$signed(spu_core.volleft),$signed(spu_core.volright),$signed(spu_core.chanleft),$signed(spu_core.chanright));
end
`endif
`ifdef RS_PROBE
// Exact reverb resampler: per tick the reverb input pair and the decimated
// value (I), and the fresh output plus both interpolated outputs (O).
integer rs_file; logic rs_we_d=0; logic signed [15:0] rs_upl_d,rs_upr_d,rs_ivbl_d,rs_ivbr_d;
initial rs_file=$fopen("resample_probe.txt","w");
always @(posedge clk1x) begin
 rs_we_d<=spu_core.rsinwe; rs_upl_d<=spu_core.rsupl; rs_upr_d<=spu_core.rsupr;
 rs_ivbl_d<=spu_core.reverblastleft; rs_ivbr_d<=spu_core.reverblastright;
 if(rs_we_d) $fwrite(rs_file,"I %0d %0d %0d %0d\n",spu_core.reverbright,$signed(spu_core.rsinl),$signed(spu_core.rsinr),$signed(spu_core.rsdown));
 if(spu_core.rsstart) $fwrite(rs_file,"O %0d %0d %0d %0d %0d\n",!spu_core.reverbright,rs_ivbl_d,rs_ivbr_d,rs_upl_d,rs_upr_d);
 if(spu_core.rsstart) $fwrite(rs_file,"M %0d %0d %0d %0d\n",$signed(spu_core.mda),$signed(spu_core.mdb),$signed(spu_core.iir_a),$signed(spu_core.iir_b));
end
`endif
initial begin
 if(!$value$plusargs("samples=%d",limit)) limit=1000;
 events=$fopen("events.txt","r"); outs=$fopen("rtl_out.txt","w");
 have_event=($fscanf(events,"%d %d %h %h\n",ev_sample,ev_type,ev_addr,ev_value)==4);
 cold_reset();
 // Optional fast-forward: apply every event before +skip back to back.
 if($value$plusargs("skip=%d",n)) begin
  while(have_event && ev_sample<n) begin
   if(ev_type==1) write_reg(10'(ev_addr),16'(ev_value));
   else if(ev_type==2) begin memory.data[ev_addr*2]=8'(ev_value);memory.data[ev_addr*2+1]=8'(ev_value>>8);dma_writes++;end
   else if(ev_type==3) begin cd_left=16'(ev_value>>16);cd_right=16'(ev_value);end
   applied++;
   have_event=($fscanf(events,"%d %d %h %h\n",ev_sample,ev_type,ev_addr,ev_value)==4);
  end
  sample_index=n;
 end
 while(sample_index<limit) begin
  @(posedge spu_tick);
  $fwrite(outs,"%0d %0d %0d\n",sample_index,$signed(sound_out_left),$signed(sound_out_right));
  @(negedge clk1x);
  while(have_event && ev_sample<=sample_index+evshift) begin
   if(ev_type==1) write_reg(10'(ev_addr),16'(ev_value));
   else if(ev_type==2) begin memory.data[ev_addr*2]=8'(ev_value);memory.data[ev_addr*2+1]=8'(ev_value>>8);dma_writes++;end
   else if(ev_type==3) begin cd_left=16'(ev_value>>16);cd_right=16'(ev_value);end
   applied++;
   have_event=($fscanf(events,"%d %d %h %h\n",ev_sample,ev_type,ev_addr,ev_value)==4);
  end
  sample_index++;
 end
 $fclose(outs);
 $display("REPLAY samples=%0d events=%0d dma_writes=%0d overrun_cycles=%0d",sample_index,applied,dma_writes,overruns);
 $finish;
end
endmodule
