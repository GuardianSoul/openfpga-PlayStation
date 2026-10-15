// SPDX-License-Identifier: GPL-2.0-only
// Independent bounded pixel expectations; no BIOS, game or hardware success inferred.
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
integer checks=0;
task write_gpu(input [3:0] address,input [31:0] value);
 @(negedge clk1x);while(bus_stall) @(negedge clk1x);
 bus_addr=address;bus_datawrite=value;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
task idle;
 integer n;
 repeat(20) @(negedge clk1x);
 n=0;
 while(!ss_idle) begin @(negedge clk1x);n++;if(n>50000) $fatal(1,"GPU timeout");end
 repeat(20) @(negedge clk1x);
endtask
task pixel(input integer x,y,input [15:0] expected);
 reg [31:0] word_value;reg [15:0] value;
 word_value=vram.data[(y*1024+x)>>1];
 value=x%2 ? word_value[31:16]:word_value[15:0];
 if(value!==expected) $fatal(1,"GPU pixel (%d,%d) got=%04x expected=%04x",x,y,value,expected);
 checks++;
endtask
initial begin
 repeat(100) @(negedge clk1x);reset=0;ss_reset=0;repeat(100) @(negedge clk1x);
 // Unclipped drawing area, normal 15-bit pixels, no dithering or mask.
 write_gpu(0,32'he1000000);write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
 // Quick fill rounds X down and width up to multiples of sixteen.
 write_gpu(0,32'h020000ff);write_gpu(0,32'h00100013);write_gpu(0,32'h00020011);idle();
 for(integer y=16;y<18;y++) for(integer x=16;x<48;x++) pixel(x,y,16'h001f);
 pixel(15,16,0);pixel(48,16,0);pixel(16,15,0);pixel(16,18,0);
 $display("FILL checks=%d",checks);
 // Upload four packed words: 8 pixels, preserving the upper mask bit.
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00140040);write_gpu(0,32'h00020004);
 write_gpu(0,32'hffff001f);write_gpu(0,32'h03e07c00);write_gpu(0,32'h80000000);write_gpu(0,32'h1234abcd);idle();
 pixel(64,20,16'h001f);pixel(65,20,16'hffff);pixel(66,20,16'h7c00);pixel(67,20,16'h03e0);
 pixel(64,21,16'h0000);pixel(65,21,16'h8000);pixel(66,21,16'habcd);pixel(67,21,16'h1234);
 $display("UPLOAD checks=%d",checks);
 // Non-overlapping VRAM copy.
 write_gpu(0,32'h80000000);write_gpu(0,32'h00140040);write_gpu(0,32'h00180050);write_gpu(0,32'h00020004);idle();
 pixel(80,24,16'h001f);pixel(81,24,16'hffff);pixel(82,24,16'h7c00);pixel(83,24,16'h03e0);
 pixel(80,25,16'h0000);pixel(81,25,16'h8000);pixel(82,25,16'habcd);pixel(83,25,16'h1234);
 $display("COPY checks=%d",checks);
 // Untextured rectangle.
 write_gpu(0,32'h6000ff00);write_gpu(0,32'h00200080);write_gpu(0,32'h00040008);idle();
 for(integer y=32;y<36;y++) for(integer x=128;x<136;x++) pixel(x,y,16'h03e0);
 pixel(127,32,0);pixel(136,32,0);pixel(128,31,0);pixel(128,36,0);
 // Flat right triangle: integer raster coordinates, top/left included.
 write_gpu(0,32'h20ff0000);write_gpu(0,32'h004000a0);write_gpu(0,32'h004000a8);write_gpu(0,32'h004800a0);idle();
 for(integer y=63;y<73;y++) for(integer x=159;x<169;x++)
  pixel(x,y,(x>=160 && y>=64 && x-160+y-64<8) ? 16'h7c00:0);
 $display("TRIANGLE checks=%d",checks);
 // Clip a rectangle to a 5 by 3 drawing area and apply a signed offset.
 write_gpu(0,32'he3000000 | (80<<10) | 192);write_gpu(0,32'he4000000 | (82<<10) | 196);
 write_gpu(0,32'he5000000 | ((2048-3)<<11) | 5);
 write_gpu(0,32'h60ffffff);write_gpu(0,32'h005000b8);write_gpu(0,32'h00080010);idle();
 for(integer y=77;y<85;y++) for(integer x=188;x<201;x++)
  pixel(x,y,(x>=192 && x<=196 && y>=80 && y<=82) ? 16'h7fff:0);
 write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);
 $display("CLIP/OFFSET checks=%d",checks);
 // All four PSX semi-transparency modes on an opaque untextured tile.
 for(integer mode=0;mode<4;mode++) begin
  write_gpu(0,32'ha0000000);write_gpu(0,(100<<16)|(224+mode*4));write_gpu(0,32'h00010002);
  write_gpu(0,32'h18e718e7);idle();
  write_gpu(0,32'he1000000 | (mode<<5));
  write_gpu(0,32'h620850a0);write_gpu(0,(100<<16)|(224+mode*4));write_gpu(0,32'h00010002);idle();
  case(mode)
   0: begin pixel(224+mode*4,100,16'h0d0d);pixel(225+mode*4,100,16'h0d0d);end
   1: begin pixel(224+mode*4,100,16'h1e3b);pixel(225+mode*4,100,16'h1e3b);end
   2: begin pixel(224+mode*4,100,16'h1400);pixel(225+mode*4,100,16'h1400);end
   3: begin pixel(224+mode*4,100,16'h192c);pixel(225+mode*4,100,16'h192c);end
  endcase
 end
 $display("BLEND checks=%d",checks);
 // Destination mask protection and forced mask writes.
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00680100);write_gpu(0,32'h00010002);write_gpu(0,32'h03e0801f);idle();
 write_gpu(0,32'he6000002);write_gpu(0,32'h60ff0000);write_gpu(0,32'h00680100);write_gpu(0,32'h00010004);idle();
 pixel(256,104,16'h801f);pixel(257,104,16'h7c00);pixel(258,104,16'h7c00);pixel(259,104,16'h7c00);
 write_gpu(0,32'he6000001);write_gpu(0,32'h600000ff);write_gpu(0,32'h00680100);write_gpu(0,32'h00010004);idle();
 for(integer x=256;x<260;x++) pixel(x,104,16'h801f);
 write_gpu(0,32'he6000002);write_gpu(0,32'h60ffffff);write_gpu(0,32'h00680100);write_gpu(0,32'h00010004);idle();
 for(integer x=256;x<260;x++) pixel(x,104,16'h801f);
 write_gpu(0,32'he6000000);
 $display("MASK checks=%d",checks);
 // Raw 16-bit sprite: transparent zero, opaque black, STP bit preserved.
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00800140);write_gpu(0,32'h00020004);
 write_gpu(0,32'h03e0001f);write_gpu(0,32'hffff7c00);write_gpu(0,32'h801f0000);write_gpu(0,32'h80001234);idle();
 write_gpu(0,32'he1000105);write_gpu(0,32'h6000ff00);write_gpu(0,32'h00780120);write_gpu(0,32'h00020004);idle();
 write_gpu(0,32'h65000000);write_gpu(0,32'h00780120);write_gpu(0,32'h00008000);write_gpu(0,32'h00020004);idle();
 pixel(288,120,16'h001f);pixel(289,120,16'h03e0);pixel(290,120,16'h7c00);pixel(291,120,16'hffff);
 pixel(288,121,16'h03e0);pixel(289,121,16'h801f);pixel(290,121,16'h1234);pixel(291,121,16'h8000);
 $display("16BIT TEXTURE checks=%d",checks);
 // A shared CLUT and four indexed texels, first in 4-bit and then 8-bit mode.
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00a00000);write_gpu(0,32'h00010010);
 write_gpu(0,32'h001f0000);write_gpu(0,32'h7c0003e0);write_gpu(0,32'h0000ffff);
 for(integer i=0;i<5;i++) write_gpu(0,0);idle();
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00800180);write_gpu(0,32'h00010002);write_gpu(0,32'h00003210);idle();
 write_gpu(0,32'he1000006);write_gpu(0,32'h01000000);
 write_gpu(0,32'h6000ff00);write_gpu(0,32'h007c0130);write_gpu(0,32'h00010004);idle();
 write_gpu(0,32'h65000000);write_gpu(0,32'h007c0130);write_gpu(0,32'h28008000);write_gpu(0,32'h00010004);idle();
 pixel(304,124,16'h03e0);pixel(305,124,16'h001f);pixel(306,124,16'h03e0);pixel(307,124,16'h7c00);
 write_gpu(0,32'ha0000000);write_gpu(0,32'h00800180);write_gpu(0,32'h00010002);write_gpu(0,32'h04030201);idle();
 write_gpu(0,32'he1000086);write_gpu(0,32'h01000000);
 write_gpu(0,32'h65000000);write_gpu(0,32'h007d0130);write_gpu(0,32'h28008000);write_gpu(0,32'h00010004);idle();
 pixel(304,125,16'h001f);pixel(305,125,16'h03e0);pixel(306,125,16'h7c00);pixel(307,125,16'hffff);
 $display("INDEXED TEXTURE checks=%d",checks);
 $display("PASS GPU primitive checks=%d",checks);$finish;
end
initial begin repeat(1000000) @(negedge clk1x);$fatal(1,"Overall GPU timeout");end
endmodule
