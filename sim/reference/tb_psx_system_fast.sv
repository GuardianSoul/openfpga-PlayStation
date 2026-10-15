// SPDX-License-Identifier: GPL-2.0-only
// Simulation-only connected-system bench; no hardware or game success is inferred.
`timescale 1fs/1fs
module tb_psx_fast;
logic clk1x=1;
logic clk2x=1;
logic clk3x=1;
logic clkvid=1;
logic reset=1;
logic isPaused=0;
logic pause=0;
logic hps_busy=0;
logic [1:0] biosregion=0;
logic ram_refresh=0;
logic [31:0] ram_dataWrite=0;
logic [31:0] ram_dataRead32=0;
logic [24:0] ram_Adr=0;
logic [1:0] ram_cntDMA=0;
logic [3:0] ram_be=0;
logic ram_rnw=0;
logic ram_ena=0;
logic ram_dma=0;
logic ram_cache=0;
logic ram_done=0;
logic [22:0] ram_dmafifo_adr=0;
logic [31:0] ram_dmafifo_data=0;
logic ram_dmafifo_empty=0;
logic ram_dmafifo_read=0;
logic [3:0] cache_wr=0;
logic [31:0] cache_data=0;
logic [7:0] cache_addr=0;
logic dma_wr=0;
logic dma_reqprocessed=0;
logic [31:0] dma_data=0;
logic ddr3_BUSY=0;
logic [63:0] ddr3_DOUT=0;
logic ddr3_DOUT_READY=0;
logic [7:0] ddr3_BURSTCNT=0;
logic [27:0] ddr3_ADDR=0;
logic [63:0] ddr3_DIN=0;
logic [7:0] ddr3_BE=0;
logic ddr3_WE=0;
logic ddr3_RD=0;
logic [1:0] region=0;
logic [1:0] region_out=0;
logic hasCD=0;
logic LIDopen=1;
logic [31:0] trackinfo_data=0;
logic [8:0] trackinfo_addr=0;
logic trackinfo_write=0;
logic resetFromCD=0;
logic cd_hps_req=0;
logic [31:0] cd_hps_lba=0;
logic [31:0] cd_hps_lba_sim=0;
logic cd_hps_ack=0;
logic cd_hps_write=0;
logic [15:0] cd_hps_data=0;
logic [31:0] spuram_dataWrite=0;
logic [18:0] spuram_Adr=0;
logic [3:0] spuram_be=0;
logic spuram_rnw=0;
logic spuram_ena=0;
logic [31:0] spuram_dataRead=0;
logic spuram_done=0;
logic memcard_changed=0;
logic saving_memcard=0;
logic memcard1_load=0;
logic memcard2_load=0;
logic memcard_save=0;
logic memcard1_mounted=0;
logic memcard1_available=0;
logic memcard1_rd=0;
logic memcard1_wr=0;
logic [6:0] memcard1_lba=0;
logic memcard1_ack=0;
logic memcard1_write=0;
logic [8:0] memcard1_addr=0;
logic [15:0] memcard1_dataIn=0;
logic [15:0] memcard1_dataOut=0;
logic memcard2_mounted=0;
logic memcard2_available=0;
logic memcard2_rd=0;
logic memcard2_wr=0;
logic [6:0] memcard2_lba=0;
logic memcard2_ack=0;
logic memcard2_write=0;
logic [8:0] memcard2_addr=0;
logic [15:0] memcard2_dataIn=0;
logic [15:0] memcard2_dataOut=0;
logic isPal=0;
logic hsync=0;
logic vsync=0;
logic hblank=0;
logic vblank=0;
logic [10:0] DisplayWidth=0;
logic [9:0] DisplayHeight=0;
logic [9:0] DisplayOffsetX=0;
logic [8:0] DisplayOffsetY=0;
logic video_ce=0;
logic video_interlace=0;
logic [7:0] video_r=0;
logic [7:0] video_g=0;
logic [7:0] video_b=0;
logic video_isPal=0;
logic video_fbmode=0;
logic video_fb24=0;
logic [2:0] video_hResMode=0;
logic [3:0] video_frameindex=0;
logic joypad1_KeyTriangle=0;
logic joypad1_KeyCircle=0;
logic joypad1_KeyCross=0;
logic joypad1_KeySquare=0;
logic joypad1_KeySelect=0;
logic joypad1_KeyStart=0;
logic joypad1_KeyRight=0;
logic joypad1_KeyLeft=0;
logic joypad1_KeyUp=0;
logic joypad1_KeyDown=0;
logic joypad1_KeyR1=0;
logic joypad1_KeyR2=0;
logic joypad1_KeyR3=0;
logic joypad1_KeyL1=0;
logic joypad1_KeyL2=0;
logic joypad1_KeyL3=0;
logic [15:0] joypad1_rumble=0;
logic [15:0] joypad2_rumble=0;
logic [15:0] joypad3_rumble=0;
logic [15:0] joypad4_rumble=0;
logic [1:0] padMode=0;
logic [7:0] transmitValueSnac=0;
logic selectedPort1Snac=0;
logic selectedPort2Snac=0;
logic clk9Snac=1;
logic beginTransferSnac=0;
logic [15:0] sound_out_left=0;
logic [15:0] sound_out_right=0;
psx_mister_psx_top_resource core(.clk1x(clk1x),
 .clk2x(clk2x),
 .clk3x(clk3x),
 .clkvid(clkvid),
 .reset(reset),
 .isPaused(isPaused),
 .pause(pause),
 .hps_busy(hps_busy),
 .biosregion(biosregion),
 .ram_refresh(ram_refresh),
 .ram_dataWrite(ram_dataWrite),
 .ram_dataRead32(ram_dataRead32),
 .ram_Adr(ram_Adr),
 .ram_cntDMA(ram_cntDMA),
 .ram_be(ram_be),
 .ram_rnw(ram_rnw),
 .ram_ena(ram_ena),
 .ram_dma(ram_dma),
 .ram_cache(ram_cache),
 .ram_done(ram_done),
 .ram_dmafifo_adr(ram_dmafifo_adr),
 .ram_dmafifo_data(ram_dmafifo_data),
 .ram_dmafifo_empty(ram_dmafifo_empty),
 .ram_dmafifo_read(ram_dmafifo_read),
 .cache_wr(cache_wr),
 .cache_data(cache_data),
 .cache_addr(cache_addr),
 .dma_wr(dma_wr),
 .dma_reqprocessed(dma_reqprocessed),
 .dma_data(dma_data),
 .ddr3_BUSY(ddr3_BUSY),
 .ddr3_DOUT(ddr3_DOUT),
 .ddr3_DOUT_READY(ddr3_DOUT_READY),
 .ddr3_BURSTCNT(ddr3_BURSTCNT),
 .ddr3_ADDR(ddr3_ADDR),
 .ddr3_DIN(ddr3_DIN),
 .ddr3_BE(ddr3_BE),
 .ddr3_WE(ddr3_WE),
 .ddr3_RD(ddr3_RD),
 .region(region),
 .region_out(region_out),
 .hasCD(hasCD),
 .LIDopen(LIDopen),
 .trackinfo_data(trackinfo_data),
 .trackinfo_addr(trackinfo_addr),
 .trackinfo_write(trackinfo_write),
 .resetFromCD(resetFromCD),
 .cd_hps_req(cd_hps_req),
 .cd_hps_lba(cd_hps_lba),
 .cd_hps_lba_sim(cd_hps_lba_sim),
 .cd_hps_ack(cd_hps_ack),
 .cd_hps_write(cd_hps_write),
 .cd_hps_data(cd_hps_data),
 .spuram_dataWrite(spuram_dataWrite),
 .spuram_Adr(spuram_Adr),
 .spuram_be(spuram_be),
 .spuram_rnw(spuram_rnw),
 .spuram_ena(spuram_ena),
 .spuram_dataRead(spuram_dataRead),
 .spuram_done(spuram_done),
 .memcard_changed(memcard_changed),
 .saving_memcard(saving_memcard),
 .memcard1_load(memcard1_load),
 .memcard2_load(memcard2_load),
 .memcard_save(memcard_save),
 .memcard1_mounted(memcard1_mounted),
 .memcard1_available(memcard1_available),
 .memcard1_rd(memcard1_rd),
 .memcard1_wr(memcard1_wr),
 .memcard1_lba(memcard1_lba),
 .memcard1_ack(memcard1_ack),
 .memcard1_write(memcard1_write),
 .memcard1_addr(memcard1_addr),
 .memcard1_dataIn(memcard1_dataIn),
 .memcard1_dataOut(memcard1_dataOut),
 .memcard2_mounted(memcard2_mounted),
 .memcard2_available(memcard2_available),
 .memcard2_rd(memcard2_rd),
 .memcard2_wr(memcard2_wr),
 .memcard2_lba(memcard2_lba),
 .memcard2_ack(memcard2_ack),
 .memcard2_write(memcard2_write),
 .memcard2_addr(memcard2_addr),
 .memcard2_dataIn(memcard2_dataIn),
 .memcard2_dataOut(memcard2_dataOut),
 .isPal(isPal),
 .hsync(hsync),
 .vsync(vsync),
 .hblank(hblank),
 .vblank(vblank),
 .DisplayWidth(DisplayWidth),
 .DisplayHeight(DisplayHeight),
 .DisplayOffsetX(DisplayOffsetX),
 .DisplayOffsetY(DisplayOffsetY),
 .video_ce(video_ce),
 .video_interlace(video_interlace),
 .video_r(video_r),
 .video_g(video_g),
 .video_b(video_b),
 .video_isPal(video_isPal),
 .video_fbmode(video_fbmode),
 .video_fb24(video_fb24),
 .video_hResMode(video_hResMode),
 .video_frameindex(video_frameindex),
 .joypad1_KeyTriangle(joypad1_KeyTriangle),
 .joypad1_KeyCircle(joypad1_KeyCircle),
 .joypad1_KeyCross(joypad1_KeyCross),
 .joypad1_KeySquare(joypad1_KeySquare),
 .joypad1_KeySelect(joypad1_KeySelect),
 .joypad1_KeyStart(joypad1_KeyStart),
 .joypad1_KeyRight(joypad1_KeyRight),
 .joypad1_KeyLeft(joypad1_KeyLeft),
 .joypad1_KeyUp(joypad1_KeyUp),
 .joypad1_KeyDown(joypad1_KeyDown),
 .joypad1_KeyR1(joypad1_KeyR1),
 .joypad1_KeyR2(joypad1_KeyR2),
 .joypad1_KeyR3(joypad1_KeyR3),
 .joypad1_KeyL1(joypad1_KeyL1),
 .joypad1_KeyL2(joypad1_KeyL2),
 .joypad1_KeyL3(joypad1_KeyL3),
 .joypad1_rumble(joypad1_rumble),
 .joypad2_rumble(joypad2_rumble),
 .joypad3_rumble(joypad3_rumble),
 .joypad4_rumble(joypad4_rumble),
 .padMode(padMode),
 .transmitValueSnac(transmitValueSnac),
 .selectedPort1Snac(selectedPort1Snac),
 .selectedPort2Snac(selectedPort2Snac),
 .clk9Snac(clk9Snac),
 .beginTransferSnac(beginTransferSnac),
 .sound_out_left(sound_out_left),
 .sound_out_right(sound_out_right));
always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #4920850 clk3x=~clk3x;
always #9312170 clkvid=~clkvid;
initial begin repeat(100) @(negedge clk1x);reset=0;end
mister_sdram_model #(.BOOT_FILE("boot.hex8")) main_memory(
 .clk(clk1x),.clk3x(clk3x),.refresh(ram_refresh),.addr({2'b0,ram_Adr}),.req(ram_ena),
 .ram_dma(ram_dma),.ram_dmacnt(ram_cntDMA),.ram_iscache(ram_cache),.rnw(ram_rnw),.be(ram_be),.di(ram_dataWrite),
 .do32(ram_dataRead32),.done(ram_done),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
 .dma_wr(dma_wr),.dma_data(dma_data),.reqprocessed(dma_reqprocessed),.ram_dmafifo_adr(ram_dmafifo_adr),
 .ram_dmafifo_data(ram_dmafifo_data),.ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read));
mister_sdram_model #(.MEMORY_BYTES(524288),.DOREFRESH(0)) spu_memory(
 .clk(clk1x),.clk3x(clk3x),.refresh(1'b0),.addr({8'b0,spuram_Adr}),.req(spuram_ena),
 .ram_dma(1'b0),.ram_dmacnt(2'b0),.ram_iscache(1'b0),.rnw(spuram_rnw),.be(spuram_be),.di(spuram_dataWrite),
 .do32(spuram_dataRead),.done(spuram_done),.cache_wr(),.cache_data(),.cache_addr(),
 .dma_wr(),.dma_data(),.reqprocessed(),.ram_dmafifo_adr(23'b0),.ram_dmafifo_data(32'b0),
 .ram_dmafifo_empty(1'b1),.ram_dmafifo_read());
mister_ddr_model video_memory(.clk(clk2x),.busy(ddr3_BUSY),.burstcnt(ddr3_BURSTCNT),
 .addr({4'b0011,ddr3_ADDR[27:3]}),.dout(ddr3_DOUT),.ready(ddr3_DOUT_READY),.rd(ddr3_RD),
 .din(ddr3_DIN),.be(ddr3_BE),.we(ddr3_WE));
integer cycles=0,checks=0,max_cycles=2000000;
reg diagnostic=1;
reg [31:0] expected[0:2647];
integer trace;
initial begin
 void'($value$plusargs("max_cycles=%d",max_cycles));
 if($test$plusargs("bios")) diagnostic=0;
 else $readmemh("expected.hex",expected);
 trace=$fopen("fast-trace.csv","w");
end
always @(posedge clk1x) begin
 cycles<=cycles+1;
 if(cycles<10000) $fwrite(trace,"%d,%08x,%08x,%d,%08x,%08x\n",cycles,core.component_core.icpu.pc,
   core.component_core.icpu.opcode0,ram_ena,ram_Adr,ram_dataWrite);
 if(cycles%500000==0) $display("PROGRESS cycles=%d pc=%08x checks=%d",cycles,core.component_core.icpu.pc,checks);
 // The reset controller writes RAM after its one-cycle reset strobes. Wait
 // for its complete operation to finish before checking CPU result stores.
 if(diagnostic && ram_ena && !ram_rnw && !core.component_core.isavestates.savestate_busy) begin
  if(ram_Adr>=4096 && ram_Adr<4096+2648*4) begin
   if(ram_Adr!=4096+checks*4 || ram_dataWrite!=expected[checks])
    $fatal(1,"DIAGNOSTIC mismatch index=%d addr=%08x got=%08x expected=%08x",checks,ram_Adr,ram_dataWrite,expected[checks]);
   checks++;
  end else if(ram_Adr==25'hff00) begin
   if(ram_dataWrite!=32'h600df00d || checks!=2648) $fatal(1,"DIAGNOSTIC incomplete");
   $display("PASS FAST SYSTEM cycles=%d checks=%d",cycles,checks);$finish;
  end
 end
 if(cycles>=max_cycles) begin
  if(diagnostic) $fatal(1,"System diagnostic timed out");
  $display("BIOS BOUNDED STOP pc=%08x",core.component_core.icpu.pc);$finish;
 end
end
endmodule
