// SPDX-License-Identifier: GPL-2.0-only
// Whole disc swap through the Pocket path: pinned cd_top, the SD loader
// (psx_cd_stream) behind the pinned APF command handler, psx_disc_swap and
// psx_disc_select. Disc A is read, the APF firmware model reloads slot 0
// with disc B while cd_top is reading (0x8A update), the sequencer opens the
// lid, the loader drains and remounts, the lid closes, and disc B is read.
// InstantSeek enabled; authored images, no game assets.
// With DISC_MENU (prepare --disc-menu), both discs are in the APF table
// (disc A slot 0, disc B slot 0x100) and the swap starts from the core
// menu's Disc setting through psx_disc_menu instead of a 0x8A reload.
`timescale 1ns/1ps
module tb_cd_swap_apf;
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
reg hclk=0;always #6.734 hclk=~hclk;
// ---------------- Pocket path ----------------
reg bridge_wr=0,bridge_rd=0;reg [31:0] bridge_addr=0,bridge_data=0;
wire [31:0] command_data,table_q,update_size,target_offset,target_bridge,target_length;
wire allcomplete,slot_update,table_active,target_read,target_done,host_busy,read_error;
wire [15:0] update_id,target_id,disc_slot_id;wire [9:0] table_addr;wire [2:0] target_error;
wire disc_change_done,has_disc,disc_pending,stream_idle,disc_select_strobe;
wire [7:0] disc_generation;wire [19:0] sector_count;
wire change_req,change_done,lid_open,swap_busy,guest_idle;
reg transport_reset=1;
wire [3:0] discs_present;
`ifdef DISC_MENU
psx_cd_stream #(.DISC_STEP(16'h100)) loader(
`else
psx_cd_stream loader(
`endif.host_clk(hclk),.host_reset(transport_reset),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),
 .bridge_data(bridge_data),.allcomplete(allcomplete),.slot_update(slot_update),.slot_update_id(update_id),
 .slot_update_size(update_size),.table_active(table_active),.table_grant(1'b1),.table_addr(table_addr),.table_q(table_q),
 .target_read(target_read),.target_ready(1'b1),.target_id(target_id),.target_offset(target_offset),
 .target_bridge(target_bridge),.target_length(target_length),.target_done(target_done),
 .target_error(target_error),.host_busy(host_busy),.read_error(read_error),
 .clk(clk1x),.reset(transport_reset),.host_region(2'd2),.disc_slot_id(disc_slot_id),
 .disc_select_strobe(disc_select_strobe),.disc_change_done(disc_change_done),.has_disc(has_disc),.discs_present(discs_present),
 .disc_pending(disc_pending),.guest_idle(guest_idle),.cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba),.cd_hps_ack(cd_hps_ack),
 .cd_hps_write(cd_hps_write),.cd_hps_data(cd_hps_data),.disc_generation(disc_generation),
 .sector_count(sector_count),.stream_idle(stream_idle),
 .trackinfo_addr(trackinfo_addr),.trackinfo_data(trackinfo_data),.trackinfo_write(trackinfo_write));
wire menu_request;reg [1:0] disc_setting=0;
psx_disc_swap #(.OPEN_CYCLES(300000),.CLOSE_CYCLES(200000)) swap(.clk(clk1x),.reset(transport_reset),
 .manual_lid_open(1'b0),.swap_request(menu_request),.disc_pending(disc_pending),.quiet(guest_idle),.change_req(change_req),
 .change_done(change_done),.lid_open(lid_open),.busy(swap_busy));
`ifdef DISC_MENU
psx_disc_menu #(.FIRST_SLOT(16'h0),.STEP(16'h100),.DISCS(2)) select(.clk(clk1x),.reset(transport_reset),
 .disc_setting(disc_setting),.change_req(change_req),.change_done(change_done),.disc_slot_id(disc_slot_id),
 .disc_select_strobe(disc_select_strobe),.disc_change_done(disc_change_done),.discs_present(discs_present),.swap_request(menu_request));
`else
assign menu_request=1'b0;
psx_disc_select select(.clk(clk1x),.reset(transport_reset),.change_req(change_req),.change_done(change_done),
 .disc_slot_id(disc_slot_id),.disc_select_strobe(disc_select_strobe),.disc_change_done(disc_change_done));
`endif
always @* begin hascd=has_disc;lidopen=lid_open;end
core_bridge_cmd apf_commands(.clk(hclk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
 .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
 .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
 .dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
 .dataslot_requestwrite_ack(1'b0),.dataslot_requestwrite_ok(1'b0),
 .dataslot_update(slot_update),.dataslot_update_id(update_id),.dataslot_update_size(update_size),
 .dataslot_allcomplete(allcomplete),
 .savestate_supported(1'b0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
 .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
 .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
 .target_dataslot_read(target_read),.target_dataslot_write(1'b0),.target_dataslot_getfile(1'b0),
 .target_dataslot_openfile(1'b0),.target_dataslot_id(target_id),.target_dataslot_slotoffset(target_offset),
 .target_dataslot_bridgeaddr(target_bridge),.target_dataslot_length(target_length),
 .target_dataslot_done(target_done),.target_dataslot_err(target_error),
 .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
 .datatable_addr(table_addr),.datatable_wren(1'b0),.datatable_data(0),.datatable_q(table_q));
// ---------------- APF firmware model ----------------
string path_a,path_b,meta_a_path,meta_b_path;
integer file_a,file_b,sectors_a=0,sectors_b=0,current=0,apf_reads=0,reload_now=0,reloaded=0;
reg [31:0] meta_a[0:399],meta_b[0:399];
function automatic [31:0] swap32(input [31:0] x);return {x[7:0],x[15:8],x[23:16],x[31:24]};endfunction
task automatic write_bus(input [31:0] addr,input [31:0] data);
 @(negedge hclk);bridge_addr=addr;bridge_data=data;@(negedge hclk);bridge_wr=1;@(negedge hclk);bridge_wr=0;
endtask
task automatic read_bus(input [31:0] addr,output [31:0] data);
 @(negedge hclk);bridge_addr=addr;bridge_rd=1;repeat(4) @(negedge hclk);data=swap32(command_data);bridge_rd=0;
endtask
task automatic host_command(input [15:0] c);
 reg [31:0] result;result=0;write_bus(32'hf8000000,swap32({16'h434d,c}));
 for(integer n=0;n<1000 && result[31:16]!==16'h4f4b;n++) read_bus(32'hf8000000,result);
 if(result[31:16]!=16'h4f4b) $fatal(1,"APF host command %h",c);
endtask
initial begin
 reg [31:0] status,id,offset,addr,length;integer b0,b1,b2,b3,f;
 if(!$value$plusargs("disc_a=%s",path_a) || !$value$plusargs("disc_b=%s",path_b) ||
    !$value$plusargs("meta_a=%s",meta_a_path) || !$value$plusargs("meta_b=%s",meta_b_path) ||
    !$value$plusargs("sectors_a=%d",sectors_a) || !$value$plusargs("sectors_b=%d",sectors_b))
  $fatal(1,"Swap fixture configuration missing");
 file_a=$fopen(path_a,"rb");file_b=$fopen(path_b,"rb");if(!file_a || !file_b) $fatal(1,"Cannot open images");
 $readmemh(meta_a_path,meta_a);$readmemh(meta_b_path,meta_b);
 wait(!transport_reset);
 write_bus(32'hf8002000,swap32(32'h20));write_bus(32'hf8002004,swap32(32'd524288));
 write_bus(32'hf8002008,swap32(32'h0));write_bus(32'hf800200c,swap32(sectors_a*2352));
`ifdef DISC_MENU
`ifndef DISC_MENU_ABSENT
 write_bus(32'hf8002010,swap32(32'h100));write_bus(32'hf8002014,swap32(sectors_b*2352));
`else
 // Declared in data.json but not in the game file: APF lists it, size 0.
 write_bus(32'hf8002010,swap32(32'h100));write_bus(32'hf8002014,swap32(32'h0));
`endif
`endif
 host_command(16'h8f);
 forever begin
`ifdef DISC_MENU
  if(reload_now && !reloaded) begin
   // The user picks Disc 2 in the core menu (a clk_74a register). With
   // DISC_MENU_ABSENT the game file has no disc 2: the core returns to disc 1.
`ifndef DISC_MENU_ABSENT
   current=1;
`endif
   reloaded=1;@(negedge hclk);disc_setting=1;
  end
`else
  if(reload_now && !reloaded) begin
   // The user loads disc B into slot 0 from the Pocket menu.
   current=1;reloaded=1;
   write_bus(32'hf8000020,swap32(32'h0));write_bus(32'hf8000024,swap32(sectors_b*2352));host_command(16'h8a);
  end
`endif
  read_bus(32'hf8001000,status);
  if(status[31:16]==16'h636d) begin
   if(status[15:0]!=16'h0180) $fatal(1,"APF target opcode %h",status[15:0]);
   read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
`ifdef DISC_MENU
   if((id!=0 && id!=16'h100) || length!=2352 || offset+2352>(id ? sectors_b:sectors_a)*2352) $fatal(1,"APF read shape");
   f=id ? file_b:file_a;
`else
   if(id!=0 || length!=2352 || offset+2352>(current ? sectors_b:sectors_a)*2352) $fatal(1,"APF read shape");
   f=current ? file_b:file_a;
`endif
   write_bus(32'hf8001000,swap32(32'h62750000));
   if($fseek(f,offset,0)!=0) $fatal(1,"Disc seek failed");
   for(integer n=0;n<588;n++) begin
    b0=$fgetc(f);b1=$fgetc(f);b2=$fgetc(f);b3=$fgetc(f);if(b3<0) $fatal(1,"Image truncated");
    write_bus(addr+n*4,{b3[7:0],b2[7:0],b1[7:0],b0[7:0]});
   end
   write_bus(32'hf8001000,swap32(32'h6f6b0000));apf_reads++;
  end
 end
end
// Every track table must be the one of the file then in the slot.
integer tables=0;
always @(posedge clk1x) if(trackinfo_write) begin
 if(trackinfo_data!==(current ? meta_b[trackinfo_addr] : meta_a[trackinfo_addr]))
  $fatal(1,"Track word %0d=%h for disc %0d",trackinfo_addr,trackinfo_data,current);
 if(trackinfo_addr==399) tables++;
end
integer drained=0;
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
  if(n>2500000) $fatal(1,"CD IRQ timeout wanted=%d got=%02x cycles=%d command=%0d",wanted,flag,cycles,commands);
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
// Reading may still raise data IRQs around the lid change; ack them and
// empty the response FIFO before a GetStat.
task drain_irqs;
 reg [7:0] flag,status,dropped;
 for(integer n=0;n<16;n++) begin
  write_reg(0,1);read_reg(3,flag);
  if(flag&7) begin
   drained++;
   write_reg(0,0);read_reg(0,status);while(status&32) begin read_reg(1,dropped);read_reg(0,status);end
   ack_irq();repeat(5000) @(negedge clk1x);
  end
 end
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
integer lid_opens=0;
always @(posedge lid_open) lid_opens++;
initial begin
 repeat(12) @(negedge clk1x);reset=0;ss_reset=0;transport_reset=0;
 wait(has_disc);repeat(100) @(negedge clk1x);
`ifdef DISC_MENU_BOOT
`ifdef DISC_MENU_ABSENT
 // A remembered disc the game file lacks, right after start: the drive must
 // stay on disc 1 with no remount at all (nothing for the BIOS to catch).
 reload_now=1;
 repeat(3000000) @(negedge clk1x);
 if(lid_opens!=0 || tables!=1 || !has_disc || swap_busy) $fatal(1,"Remembered absent disc changed the drive: lid=%0d tables=%0d",lid_opens,tables);
 write_reg(0,1);write_reg(2,31);ack_irq();
 init_and_id(8'h41);read_sector0(0);
 $display("PASS CD swap via APF checks=%0d apf_reads=%0d tables=%0d getstat_polls=0 drained_irqs=0 cycles=%0d boot_absent=1",checks,apf_reads,tables,cycles);$finish;
`endif
 // The remembered Disc setting arrives right after start, before anything
 // read the disc: the swap must happen without opening the lid.
 reload_now=1;
 begin : quiet_swap
  integer n;n=0;
  while(!(tables==2 && has_disc && !swap_busy)) begin @(negedge clk1x);n++;if(n>40000000) $fatal(1,"Boot disc choice never mounted (tables=%0d)",tables);end
 end
 if(lid_opens!=0) $fatal(1,"Lid opened for a boot-time disc choice");
 write_reg(0,1);write_reg(2,31);ack_irq();
 init_and_id(8'h41);read_sector0(1);
 $display("PASS CD swap via APF checks=%0d apf_reads=%0d tables=%0d getstat_polls=0 drained_irqs=0 cycles=%0d quiet=1",checks,apf_reads,tables,cycles);$finish;
`endif
 write_reg(0,1);write_reg(2,31);ack_irq();
 init_and_id(8'h41);
 read_sector0(0);
 // Keep disc A streaming (ReadN) while the user loads disc B.
 write_reg(0,0);write_reg(2,0);write_reg(2,2);write_reg(2,0);command(8'h02);wait_irq(3);response(value);ack_irq();
 command(8'h06);wait_irq(3);response(value);ack_irq();
 wait_irq(1);response(value);ack_irq();
 reload_now=1;
`ifdef DISC_MENU_ABSENT
 // Disc 2 is not in the game file: the choice is ignored, disc A stays in
 // and the lid never opens.
 repeat(3000000) @(negedge clk1x);
 if(lid_opens!=0 || tables!=1 || !has_disc || swap_busy || discs_present!=4'b0001)
  $fatal(1,"Absent disc choice changed the drive: lid=%0d tables=%0d has=%b present=%b",lid_opens,tables,has_disc,discs_present);
 $display("PASS CD swap via APF checks=%0d apf_reads=%0d tables=%0d getstat_polls=0 drained_irqs=%0d cycles=%0d kept=1",checks,apf_reads,tables,drained,cycles);$finish;
`endif
`ifndef DISC_MENU
 wait(disc_pending);
`endif
 wait(lid_open);repeat(2000) @(negedge clk1x);
 drain_irqs();
 getstat(value);if(!(value&8'h10)) $fatal(1,"Shell-open bit not set during the swap: %02x",value);opened_seen=1;checks++;
 wait(!swap_busy);
 if(!has_disc || disc_pending || tables!=2 || disc_generation!=2 || sector_count!=sectors_b)
  $fatal(1,"Swap end state has=%b pending=%b tables=%0d gen=%0d count=%0d",has_disc,disc_pending,tables,disc_generation,sector_count);
 value=8'h10;
 while(value&8'h10) begin
  drain_irqs();getstat(value);polls++;
  if(polls>2000) $fatal(1,"Shell-open bit never cleared after closing the lid");
  repeat(20000) @(negedge clk1x);
 end
 checks++;
 init_and_id(8'h41);
read_sector0(1);
 if(checks!=2048*2+4 || read_error) $fatal(1,"Swap checks incomplete %0d",checks);
 $display("PASS CD swap via APF checks=%0d apf_reads=%0d tables=%0d getstat_polls=%0d drained_irqs=%0d cycles=%0d",checks,apf_reads,tables,polls,drained,cycles);$finish;
end
initial begin repeat(400000000) @(negedge clk1x);$fatal(1,"CD swap overall timeout");end
endmodule
