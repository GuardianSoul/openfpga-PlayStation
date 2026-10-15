// SPDX-License-Identifier: GPL-2.0-only
// XA streaming at real drive timing: FFVII FMV sectors through the pinned
// cd_top at double speed (InstantSeek only shortens the initial seek).
// Logs every sector handed to cd_xa (and whether cd_xa accepted it), every
// 44.1 kHz pop or underrun, and cd_left/right after the volume stage.
`timescale 1ns/1ps
module tb_cd_xa_stream;
logic clk1x=0;
logic ce=1;
logic reset=1;
logic instantseek=1;
logic [2:0] forcecdspeed=0;
logic limitreadspeed=0;
logic hascd=0;
logic lidopen=0;
logic fastcd=0;
logic testseek=0;
logic pauseoncdslow=0;
logic [1:0] region=0;
logic [1:0] region_out=0;
logic pausecd=0;
logic pause_idle_cd=0;
logic fullyidle=0;
logic cdslow=0;
logic error=0;
logic [19:0] lbadisplay=0;
logic irqout=0;
logic spu_tick=0;
logic [15:0] cd_left=0;
logic [15:0] cd_right=0;
logic mdec_idle=1;
logic [3:0] bus_addr=0;
logic [7:0] bus_datawrite=0;
logic bus_read=0;
logic bus_write=0;
logic [7:0] bus_dataread=0;
logic dma_read=0;
logic [7:0] dma_readdata=0;
logic cd_hps_req=0;
logic [31:0] cd_hps_lba=0;
logic [31:0] cd_hps_lba_sim=0;
logic cd_hps_ack=0;
logic cd_hps_write=0;
logic [15:0] cd_hps_data=0;
logic [31:0] trackinfo_data=0;
logic [8:0] trackinfo_addr=0;
logic trackinfo_write=0;
logic resetfromcd=0;
logic ss_reset=1;
logic [31:0] ss_datawrite=0;
logic [13:0] ss_adr=0;
logic ss_wren=0;
logic ss_rden=0;
logic [31:0] ss_dataread=0;
logic ss_idle=0;
CD_ENTITY cd(.clk1x(clk1x),.ce(ce),.reset(reset),.instantseek(instantseek),.forcecdspeed(forcecdspeed),.limitreadspeed(limitreadspeed),.hascd(hascd),.lidopen(lidopen),.fastcd(fastcd),.testseek(testseek),.pauseoncdslow(pauseoncdslow),.region(region),.region_out(region_out),.pausecd(pausecd),.pause_idle_cd(pause_idle_cd),.fullyidle(fullyidle),.cdslow(cdslow),.error(error),.lbadisplay(lbadisplay),.irqout(irqout),.spu_tick(spu_tick),.cd_left(cd_left),.cd_right(cd_right),.mdec_idle(mdec_idle),.bus_addr(bus_addr),.bus_datawrite(bus_datawrite),.bus_read(bus_read),.bus_write(bus_write),.bus_dataread(bus_dataread),.dma_read(dma_read),.dma_readdata(dma_readdata),.cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba),.cd_hps_lba_sim(cd_hps_lba_sim),.cd_hps_ack(cd_hps_ack),.cd_hps_write(cd_hps_write),.cd_hps_data(cd_hps_data),.trackinfo_data(trackinfo_data),.trackinfo_addr(trackinfo_addr),.trackinfo_write(trackinfo_write),.resetfromcd(resetfromcd),.ss_reset(ss_reset),.ss_datawrite(ss_datawrite),.ss_adr(ss_adr),.ss_wren(ss_wren),.ss_rden(ss_rden),.ss_dataread(ss_dataread),.ss_idle(ss_idle));
always #15 clk1x=~clk1x;
reg load=0,disc_b=0;
wire loaded;
integer sectors_delivered;
psx_disc_host_swap host(.clk(clk1x),.load(load),.disc_b(disc_b),.sector_request(cd_hps_req),.physical_lba(cd_hps_lba),
 .sector_ack(cd_hps_ack),.sector_write(cd_hps_write),.sector_data(cd_hps_data),
 .track_addr(trackinfo_addr),.track_data(trackinfo_data),.track_write(trackinfo_write),
 .loaded(loaded),.sector_count(sectors_delivered));
integer sample_counter=0,cycles=0,commands=0,checks=0;
always @(posedge clk1x) begin
 cycles<=cycles+1;sample_counter<=sample_counter==767 ? 0:sample_counter+1;spu_tick<=sample_counter==767;
end
// Capture what cd_xa receives and what it produces.
integer log_file,xa_sectors=0,xa_rejected=0,pops=0,underruns=0,int1=0,tick_age=-1;
reg [31:0] words[0:587];
always @(posedge clk1x) if(log_file) begin
 if(cd.icd_xa.xa_write) words[cd.icd_xa.xa_addr]=cd.icd_xa.xa_data;
 if(cd.icd_xa.xa_start) begin
  if(cd.icd_xa.xa_write) words[cd.icd_xa.xa_addr]=cd.icd_xa.xa_data;
  $fwrite(log_file,"S %0d %0d\n",cycles,!cd.icd_xa.fifoadpcm_nearfull);
  for(integer i=0;i<588;i++) $fwrite(log_file,"%08x\n",words[i]);
  xa_sectors++; if(cd.icd_xa.fifoadpcm_nearfull) xa_rejected++;
 end
 if(spu_tick) begin
  if(cd.icd_xa.fifoout_empty) begin underruns++; $fwrite(log_file,"U %0d\n",cycles); end
  else pops++;
  tick_age<=0;
 end else if(tick_age>=0) tick_age<=tick_age+1;
 if(tick_age==10) begin
  $fwrite(log_file,"P %0d %0d %0d %0d\n",$signed(cd.icd_xa.cdaudio_left),$signed(cd.icd_xa.cdaudio_right),$signed(cd_left),$signed(cd_right));
  tick_age<=-1;
 end
end
task write_reg(input [3:0] address,input [7:0] value);
 @(negedge clk1x);bus_addr=address;bus_datawrite=value;bus_write=1;
 @(negedge clk1x);bus_write=0;
endtask
task read_reg(input [3:0] address,output [7:0] value);
 @(negedge clk1x);bus_addr=address;bus_read=1;
 @(negedge clk1x);value=bus_dataread;bus_read=0;
endtask
task wait_irq(input [2:0] wanted);
 reg [7:0] flag;
 integer n;
 write_reg(0,1);n=0;flag=0;
 while((flag&7)!=wanted) begin
  read_reg(3,flag);n++;
  if((flag&7)==5) $fatal(1,"CD error IRQ flag=%02x command=%d",flag,commands);
  if(n>2500000) $fatal(1,"CD IRQ timeout wanted=%d got=%02x cycles=%d",wanted,flag,cycles);
 end
endtask
task response(output [7:0] value);
 reg [7:0] status;
 integer n;n=0;status=0;
 while(!(status&32)) begin read_reg(0,status);n++;if(n>1000) $fatal(1,"Response FIFO empty command=%d cycles=%d",commands,cycles);end
 read_reg(1,value);
endtask
task ack_irq;
 write_reg(0,1);write_reg(3,31);write_reg(0,0);
endtask
task command(input [7:0] value);
 write_reg(0,0);write_reg(1,value);commands++;
endtask
reg [7:0] value,flag;
integer want_xa;
string log_name;
initial begin
 if(!$value$plusargs("xa_sectors=%d",want_xa)) want_xa=40;
 if(!$value$plusargs("log=%s",log_name)) log_name="xa_stream.log";
 log_file=0;
 repeat(12) @(negedge clk1x);reset=0;ss_reset=0;
 disc_b=0;@(negedge clk1x);load=1;@(negedge clk1x);load=0;wait(loaded);hascd=1;
 repeat(100) @(negedge clk1x);
 write_reg(0,1);write_reg(2,31);ack_irq();
 command(8'h0a);wait_irq(3);response(value);ack_irq();wait_irq(2);response(value);ack_irq();   // Init
 command(8'h0c);wait_irq(3);response(value);ack_irq();                                          // Demute
 write_reg(0,2);write_reg(2,8'h80);write_reg(3,8'h00);                                          // L->L, L->R
 write_reg(0,3);write_reg(1,8'h80);write_reg(2,8'h00);write_reg(3,8'h20);                       // R->R, R->L, apply
 write_reg(0,0);write_reg(2,8'h01);write_reg(2,8'h00);command(8'h0d);wait_irq(3);response(value);ack_irq(); // SetFilter 1/0
 write_reg(0,0);write_reg(2,8'hc8);command(8'h0e);wait_irq(3);response(value);ack_irq();         // SetMode 2x, XA, filter
 write_reg(0,0);write_reg(2,8'h`MM);write_reg(2,8'h`SS);write_reg(2,8'h`FF);command(8'h02);wait_irq(3);response(value);ack_irq(); // SetLoc
 log_file=$fopen(log_name,"w");
 command(8'h06);wait_irq(3);response(value);ack_irq();                                          // ReadN
 // Acknowledge data sectors (INT1) as an FMV player would; their data is not read.
 while(xa_sectors<want_xa) begin
  write_reg(0,1);read_reg(3,flag);
  if((flag&7)==5) $fatal(1,"CD error IRQ during XA stream");
  if((flag&7)==1) begin int1++;response(value);ack_irq(); end
  repeat(200) @(negedge clk1x);
 end
 // Pause the drive (acknowledging any data sectors still arriving). Pause
 // resets the XA decoder, so the sector still playing is cut off.
 command(8'h09);
 begin : pause_wait
  integer n;n=0;
  forever begin
   write_reg(0,1);read_reg(3,flag);
   if((flag&7)==2) begin response(value);ack_irq();disable pause_wait;end
   if((flag&7)==5) $fatal(1,"CD error IRQ during Pause");
   if((flag&7)!=0) begin response(value);ack_irq();end
   n++;if(n>100000) $fatal(1,"Pause never completed");
   repeat(200) @(negedge clk1x);
  end
 end
 repeat(4000*768) @(negedge clk1x);
 $fclose(log_file);log_file=0;
 $display("PASS CD XA stream xa_sectors=%0d rejected=%0d pops=%0d underruns=%0d int1=%0d cycles=%0d",
          xa_sectors,xa_rejected,pops,underruns,int1,cycles);
 $finish;
end
initial begin repeat(2000000000) @(negedge clk1x);$fatal(1,"CD XA stream overall timeout");end
endmodule
