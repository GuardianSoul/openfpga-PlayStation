// SPDX-License-Identifier: GPL-2.0-only
// Replays a recorded GP0/GP1 stream (Beetle PSX trace, converted by
// scripts/run_gpu_replay.py) into the pinned MiSTer GPU. VRAM starts from
// the recorded snapshot; at each later snapshot marker the bench waits for
// the GPU to go idle and writes VRAM out for comparison.
`timescale 1fs/1fs
module tb_gpu_probe;
logic clk1x=1;
logic clk2x=1;
logic clk2xindex=0;
logic clkvid=1;
logic ce=1;
logic reset=1;
logic allowunpause=0;
logic savestate_busy=0;
logic system_paused=0;
logic ditheroff=0;
logic interlaced480phack=0;
logic reproduciblegputiming=0;
logic videoout_on=1;
logic ispal=0;
logic pal60=0;
logic fpscounton=0;
logic notexture=0;
logic [1:0] texturefilter=0;
logic [1:0] texturefilterstrength=0;
logic texturefilter2doff=0;
logic dither24=0;
logic render24=0;
logic drawslow=0;
logic debugmodeon=0;
logic syncvideoout=0;
logic syncinterlace=0;
logic rotate180=0;
logic fixedvblank=0;
logic [1:0] vcrop=0;
logic hcrop=0;
logic oldgpu=0;
logic gun1crosshairon=0;
logic [7:0] gun1x=0;
logic [8:0] gun1y_scanlines=0;
logic gun1offscreen=0;
logic gun1irq10=0;
logic gun2crosshairon=0;
logic [7:0] gun2x=0;
logic [8:0] gun2y_scanlines=0;
logic gun2offscreen=0;
logic gun2irq10=0;
logic cdslow=0;
logic erroron=0;
logic errorena=0;
logic [3:0] errorcode=0;
logic lbaon=0;
logic [19:0] lbadisplay=0;
logic errorline=0;
logic errorrect=0;
logic errorpoly=0;
logic errorgpu=0;
logic errormask=0;
logic errorfifo=0;
logic [3:0] bus_addr=0;
logic [31:0] bus_datawrite=0;
logic bus_read=0;
logic bus_write=0;
logic [31:0] bus_dataread=0;
logic bus_stall=0;
logic dmaon=0;
logic gpu_dmarequest=0;
logic dma_gpu_waiting=0;
logic dma_gpu_writeena=0;
logic dma_gpu_readena=0;
logic [31:0] dma_gpu_write=0;
logic [31:0] dma_gpu_read=0;
logic irq_vblank=0;
logic irq_gpu=0;
logic vram_pause=0;
logic vram_paused=0;
logic vram_busy=0;
logic [63:0] vram_dout=0;
logic vram_dout_ready=0;
logic [7:0] vram_burstcnt=0;
logic [27:0] vram_addr=0;
logic [63:0] vram_din=0;
logic [7:0] vram_be=0;
logic vram_we=0;
logic vram_rd=0;
logic hblank_tmr=0;
logic vblank_tmr=0;
logic dotclock=0;
logic video_hsync=0;
logic video_vsync=0;
logic video_hblank=0;
logic video_vblank=0;
logic [10:0] video_displaywidth=0;
logic [9:0] video_displayheight=0;
logic [9:0] video_displayoffsetx=0;
logic [8:0] video_displayoffsety=0;
logic video_ce=0;
logic video_interlace=0;
logic [7:0] video_r=0;
logic [7:0] video_g=0;
logic [7:0] video_b=0;
logic video_ispal=0;
logic video_fbmode=0;
logic video_fb24=0;
logic [2:0] video_hresmode=0;
logic [3:0] video_frameindex=0;
logic loading_savestate=0;
logic ss_reset=1;
logic [31:0] ss_datawrite=0;
logic [2:0] ss_adr=0;
logic ss_wren_gpu=0;
logic ss_wren_timing=0;
logic ss_rden_gpu=0;
logic ss_rden_timing=0;
logic [31:0] ss_dataread_gpu=0;
logic [31:0] ss_dataread_timing=0;
logic ss_idle=0;
GPU_ENTITY gpu(.clk1x(clk1x),
.clk2x(clk2x),
.clk2xindex(clk2xindex),
.clkvid(clkvid),
.ce(ce),
.reset(reset),
.allowunpause(allowunpause),
.savestate_busy(savestate_busy),
.system_paused(system_paused),
.ditheroff(ditheroff),
.interlaced480phack(interlaced480phack),
.reproduciblegputiming(reproduciblegputiming),
.videoout_on(videoout_on),
.ispal(ispal),
.pal60(pal60),
.fpscounton(fpscounton),
.notexture(notexture),
.texturefilter(texturefilter),
.texturefilterstrength(texturefilterstrength),
.texturefilter2doff(texturefilter2doff),
.dither24(dither24),
.render24(render24),
.drawslow(drawslow),
.debugmodeon(debugmodeon),
.syncvideoout(syncvideoout),
.syncinterlace(syncinterlace),
.rotate180(rotate180),
.fixedvblank(fixedvblank),
.vcrop(vcrop),
.hcrop(hcrop),
.oldgpu(oldgpu),
.gun1crosshairon(gun1crosshairon),
.gun1x(gun1x),
.gun1y_scanlines(gun1y_scanlines),
.gun1offscreen(gun1offscreen),
.gun1irq10(gun1irq10),
.gun2crosshairon(gun2crosshairon),
.gun2x(gun2x),
.gun2y_scanlines(gun2y_scanlines),
.gun2offscreen(gun2offscreen),
.gun2irq10(gun2irq10),
.cdslow(cdslow),
.erroron(erroron),
.errorena(errorena),
.errorcode(errorcode),
.lbaon(lbaon),
.lbadisplay(lbadisplay),
.errorline(errorline),
.errorrect(errorrect),
.errorpoly(errorpoly),
.errorgpu(errorgpu),
.errormask(errormask),
.errorfifo(errorfifo),
.bus_addr(bus_addr),
.bus_datawrite(bus_datawrite),
.bus_read(bus_read),
.bus_write(bus_write),
.bus_dataread(bus_dataread),
.bus_stall(bus_stall),
.dmaon(dmaon),
.gpu_dmarequest(gpu_dmarequest),
.dma_gpu_waiting(dma_gpu_waiting),
.dma_gpu_writeena(dma_gpu_writeena),
.dma_gpu_readena(dma_gpu_readena),
.dma_gpu_write(dma_gpu_write),
.dma_gpu_read(dma_gpu_read),
.irq_vblank(irq_vblank),
.irq_gpu(irq_gpu),
.vram_pause(vram_pause),
.vram_paused(vram_paused),
.vram_busy(vram_busy),
.vram_dout(vram_dout),
.vram_dout_ready(vram_dout_ready),
.vram_burstcnt(vram_burstcnt),
.vram_addr(vram_addr),
.vram_din(vram_din),
.vram_be(vram_be),
.vram_we(vram_we),
.vram_rd(vram_rd),
.hblank_tmr(hblank_tmr),
.vblank_tmr(vblank_tmr),
.dotclock(dotclock),
.video_hsync(video_hsync),
.video_vsync(video_vsync),
.video_hblank(video_hblank),
.video_vblank(video_vblank),
.video_displaywidth(video_displaywidth),
.video_displayheight(video_displayheight),
.video_displayoffsetx(video_displayoffsetx),
.video_displayoffsety(video_displayoffsety),
.video_ce(video_ce),
.video_interlace(video_interlace),
.video_r(video_r),
.video_g(video_g),
.video_b(video_b),
.video_ispal(video_ispal),
.video_fbmode(video_fbmode),
.video_fb24(video_fb24),
.video_hresmode(video_hresmode),
.video_frameindex(video_frameindex),
.loading_savestate(loading_savestate),
.ss_reset(ss_reset),
.ss_datawrite(ss_datawrite),
.ss_adr(ss_adr),
.ss_wren_gpu(ss_wren_gpu),
.ss_wren_timing(ss_wren_timing),
.ss_rden_gpu(ss_rden_gpu),
.ss_rden_timing(ss_rden_timing),
.ss_dataread_gpu(ss_dataread_gpu),
.ss_dataread_timing(ss_dataread_timing),
.ss_idle(ss_idle));
always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #9312170 clkvid=~clkvid;
reg toggle=0,sampled=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xindex<=sampled==toggle;end
mister_ddr_model vram(.clk(clk2x),.busy(vram_busy),.burstcnt(vram_burstcnt),
 .addr({4'b0011,vram_addr[27:3]}),.dout(vram_dout),.ready(vram_dout_ready),.rd(vram_rd),
 .din(vram_din),.be(vram_be),.we(vram_we));
integer stream,rc,count=0,snaps=0,stuck=0;
string stream_name,out_dir;
reg [31:0] value;
reg [7:0] kind;
task write_gpu(input [3:0] address,input [31:0] data_value);
 // Pace GP0 words like the DMA does: never overflow the 256-word input FIFO.
 @(negedge clk1x);while(bus_stall || (address==0 && gpu.isyncfifo_in.fifocnt>=8'd192)) @(negedge clk1x);
 bus_addr=address;bus_datawrite=data_value;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
task read_gpu(output [31:0] data_value);
 @(negedge clk1x);while(bus_stall) @(negedge clk1x);
 bus_addr=0;bus_read=1;
 @(negedge clk1x);data_value=bus_dataread;bus_read=0;
endtask
task idle;
 integer n;
 repeat(20) @(negedge clk1x);
 n=0;
 while(!ss_idle && n<=2000000) begin @(negedge clk1x);n++;end
 if(!ss_idle) begin
  $display("idle timeout: proc_idle=%b fifoin_empty=%b fifoout_empty=%b vramstate=%0d pipeline_busy=%b word=%0d",
           gpu.proc_idle,gpu.fifoin_empty,gpu.fifoout_empty,gpu.vramstate,gpu.pipeline_busy,count);
  stuck++;
 end
 repeat(20) @(negedge clk1x);
endtask
reg [31:0] snapshot[0:262143];
reg [31:0] readback;
integer reads=0,read_mismatches=0;
initial begin
 if(!$value$plusargs("stream=%s",stream_name) || !$value$plusargs("out=%s",out_dir)) $fatal(1,"+stream and +out required");
 $readmemh({out_dir,"/vram_initial.hex"},snapshot);
 for(integer i=0;i<262144;i++) vram.data[i]=snapshot[i];
 repeat(100) @(negedge clk1x);reset=0;ss_reset=0;repeat(100) @(negedge clk1x);
 stream=$fopen(stream_name,"r");
 if(!stream) $fatal(1,"cannot open stream");
 while(!$feof(stream)) begin
  rc=$fscanf(stream,"%c %h\n",kind,value);
  if(rc!=2) break;
  case(kind)
   "W","S": write_gpu(0,value);
   "C": begin
     write_gpu(4,value);
     // GP1(10) answers through GPUREAD; the game reads it (not traced).
     if(value[31:24]==8'h10) begin repeat(8) @(negedge clk1x);read_gpu(readback);end
    end
   "R": begin read_gpu(readback);reads++;if(readback!==value) read_mismatches++;end
   "V": begin
     idle();
     for(integer i=0;i<262144;i++) snapshot[i]=vram.data[i];
     $writememh($sformatf("%s/vram_%0d.hex",out_dir,value),snapshot);
     snaps++;
     $display("SNAPSHOT %0d words=%0d",value,count);
    end
   default: $fatal(1,"bad stream record %c",kind);
  endcase
  count++;
 end
 $display("PASS GPU replay words=%0d snapshots=%0d reads=%0d read_mismatches=%0d not_idle_at_snapshot=%0d",count,snaps,reads,read_mismatches,stuck);
 $finish;
end
endmodule
