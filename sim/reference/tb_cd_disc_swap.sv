// SPDX-License-Identifier: GPL-2.0-only
// Disc swap: disc A read, lid open, disc B track table, lid close, disc B read.
// InstantSeek enabled; authored images, no game assets required.
`timescale 1ns/1ps
module tb_cd_disc_swap;
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
reg [7:0] value;
integer opened_seen=0,polls=0;
task getstat(output [7:0] status);
 command(8'h01);wait_irq(3);response(status);ack_irq();
endtask
task read_sector0(input integer pattern);
 write_reg(0,0);write_reg(2,0);command(8'h0e);wait_irq(3);response(value);ack_irq();       // SetMode 0
 write_reg(0,0);write_reg(2,0);write_reg(2,2);write_reg(2,0);command(8'h02);wait_irq(3);response(value);ack_irq(); // SetLoc 00:02:00
 command(8'h06);wait_irq(3);response(value);ack_irq();                                     // ReadN
 wait_irq(1);response(value);ack_irq();write_reg(0,0);write_reg(3,8'h80);
 begin : wait_data
  integer n;n=0;value=0;
  while(!(value&64)) begin read_reg(0,value);n++;if(n>10000) $fatal(1,"CD data FIFO not ready");end
 end
 for(integer i=0;i<2048;i++) begin
  read_reg(2,value);
  if(value!==((pattern==0 ? i*29 : i*31+7)&255)) $fatal(1,"disc %0d data mismatch byte=%0d got=%02x",pattern,i,value);
  checks++;
 end
 command(8'h09);wait_irq(3);response(value);ack_irq();wait_irq(2);response(value);ack_irq(); // Pause
endtask
task init_and_id(input [7:0] region_letter);
 command(8'h0a);wait_irq(3);response(value);ack_irq();wait_irq(2);response(value);ack_irq();
 command(8'h1a);wait_irq(3);response(value);ack_irq();wait_irq(2);
 for(integer i=0;i<8;i++) begin response(value);if(i==7 && value!=region_letter) $fatal(1,"GetID region wrong %02x",value);end
 checks++;ack_irq();
endtask
initial begin
 hascd=0;lidopen=0;
 repeat(12) @(negedge clk1x);reset=0;ss_reset=0;
 disc_b=0;@(negedge clk1x);load=1;@(negedge clk1x);load=0;wait(loaded);hascd=1;
 repeat(100) @(negedge clk1x);
 write_reg(0,1);write_reg(2,31);ack_irq();
 init_and_id(8'h41);
 read_sector0(0);
 // Open the lid: GetStat must report the shell open.
 lidopen=1;repeat(1000) @(negedge clk1x);
 getstat(value);if(!(value&8'h10)) $fatal(1,"Shell-open bit not set with the lid open: %02x",value);opened_seen=1;checks++;
 // Insert disc B while open, then close.
 disc_b=1;@(negedge clk1x);load=1;@(negedge clk1x);load=0;wait(loaded);
 repeat(1000) @(negedge clk1x);lidopen=0;
 value=8'h10;
 while(value&8'h10) begin
  getstat(value);polls++;
  if(polls>2000) $fatal(1,"Shell-open bit never cleared after closing the lid");
  repeat(20000) @(negedge clk1x);
 end
 checks++;
 init_and_id(8'h41);
 read_sector0(1);
 if(checks!=2048*2+4) $fatal(1,"Disc swap checks incomplete %0d",checks);
 $display("PASS CD disc swap checks=%0d getstat_polls=%0d sectors=%0d cycles=%0d",checks,polls,sectors_delivered,cycles);$finish;
end
initial begin repeat(400000000) @(negedge clk1x);$fatal(1,"CD disc swap overall timeout");end
endmodule
