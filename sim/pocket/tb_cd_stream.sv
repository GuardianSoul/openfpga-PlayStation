`timescale 1ns/1ps
// Authored disc images behind the real pinned APF command handler. The bench
// plays APF firmware (ID/size table, allcomplete, slot update, target
// dataslot reads) and cd_top's raw-sector port. Expected track tables come
// from scripts/psx_disc_host.py (+meta_a/+meta_b). No SD firmware, bridge
// PHY or Pocket hardware is modelled.
module tb_cd_stream;
 reg hclk=0,cclk=0,host_reset=1,reset=1;
 always #6.734 hclk=~hclk;     // 74.25 MHz APF bridge
 always #14.763 cclk=~cclk;    // 33.8688 MHz clk1x
 reg bridge_wr=0,bridge_rd=0;reg [31:0] bridge_addr=0,bridge_data=0;
 wire [31:0] command_data;
 wire allcomplete,slot_update;wire [15:0] update_id;wire [31:0] update_size;
 wire table_active;wire [9:0] table_addr;wire [31:0] table_q;
 wire target_read,target_done,host_busy,read_error;wire [2:0] target_error;
 wire [15:0] target_id;wire [31:0] target_offset,target_bridge,target_length;
 reg ready_hold=0;
 wire target_ready,table_grant,apf_read,apf_write,apf_done;wire [2:0] apf_error;
 wire [15:0] apf_id;wire [31:0] apf_offset,apf_bridge,apf_length;wire [9:0] apf_table_addr;
 integer card_commands=0,card_errors=0;
`ifdef CD_STREAM_ARB
 // Codex's shared target-port and table arbiters with a memory-card client
 // writing 512-byte chunks and holding table leases alongside the loader.
 // The arbiters see only power reset; the loader's transport reset never
 // revokes a command they have issued.
 reg power_reset=1,card_valid=0,card_table=0;reg [31:0] card_offset=0;
 wire [1:0] p_ready,p_busy,p_done,p_issued,t_grant;wire [1:0][2:0] p_error;wire arb_error;
 psx_apf_target_arb #(.CLIENTS(2)) target_arb(.clk(hclk),.reset(power_reset),
  .p_valid({card_valid,target_read}),.p_write(2'b10),.p_ready(p_ready),.p_busy(p_busy),
  .p_id({16'h10,target_id}),.p_offset({card_offset,target_offset}),.p_bridge({32'h23000000,target_bridge}),
  .p_length({32'd512,target_length}),.p_done(p_done),.p_issued(p_issued),.p_error(p_error),
  .target_read(apf_read),.target_write(apf_write),.target_id(apf_id),.target_offset(apf_offset),
  .target_bridge(apf_bridge),.target_length(apf_length),.target_done(apf_done),.target_error(apf_error),
  .protocol_error(arb_error));
 psx_apf_table_arb #(.CLIENTS(2)) table_arb(.clk(hclk),.reset(power_reset),.request({card_table,table_active}),
  .write_enable(2'b00),.address({10'h3ff,table_addr}),.data(64'b0),.grant(t_grant),
  .table_address(apf_table_addr),.table_write(),.table_data());
 assign target_ready=p_ready[0],target_done=p_done[0],target_error=p_error[0],table_grant=t_grant[0];
 initial begin repeat(3) @(negedge hclk);power_reset=0;end
 // Card client: a 512-byte write-back chunk now and then, and table leases.
 initial begin
  wait(!power_reset);
  forever begin
   repeat(2000+$urandom%6000) @(negedge hclk);
   // Hold valid until accepted (pending shows as busy), then drop it.
   @(negedge hclk);card_valid=1;wait(p_busy[1]);@(negedge hclk);card_valid=0;
   wait(p_done[1]);card_commands++;if(p_error[1]) card_errors++;card_offset+=512;
  end
 end
 // Card initializer stand-in: frequent table leases on another entry.
 initial begin
  wait(!power_reset);
  forever begin
   repeat(20+$urandom%200) @(negedge hclk);card_table=1;
   repeat(100+$urandom%300) @(negedge hclk);card_table=0;
  end
 end
 always @(posedge hclk) if(arb_error) $fatal(1,"Target arbiter protocol error");
`else
 assign target_ready=!ready_hold,table_grant=1'b1;
 assign apf_read=target_read,apf_write=1'b0,apf_id=target_id,apf_offset=target_offset,
  apf_bridge=target_bridge,apf_length=target_length,target_done=apf_done,target_error=apf_error,
  apf_table_addr=table_addr;
`endif
 reg [15:0] disc_slot_id=16'h20;reg disc_select_strobe=0;
 wire disc_change_done,has_disc,disc_pending,stream_idle;wire [7:0] disc_generation;wire [19:0] sector_count;
 reg cd_hps_req=0;reg [31:0] cd_hps_lba=0;
 wire cd_hps_ack,cd_hps_write;wire [15:0] cd_hps_data;
 wire [8:0] trackinfo_addr;wire [31:0] trackinfo_data;wire trackinfo_write;
`ifndef CD_STREAM_NBUF
`define CD_STREAM_NBUF 2
`endif
`ifndef CD_STREAM_TRACKS
`define CD_STREAM_TRACKS 1
`endif
`ifndef CD_STREAM_LC
`define CD_STREAM_LC 16'h0
`endif
 psx_cd_stream #(.DEFAULT_SLOT(16'h20),.NBUF(`CD_STREAM_NBUF),.MAX_TRACKS(`CD_STREAM_TRACKS),.LIBCRYPT_SLOT(`CD_STREAM_LC)) dut(.host_clk(hclk),.host_reset(host_reset),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),
  .bridge_data(bridge_data),.allcomplete(allcomplete),.slot_update(slot_update),.slot_update_id(update_id),
  .slot_update_size(update_size),.table_active(table_active),.table_grant(table_grant),.table_addr(table_addr),.table_q(table_q),
  .target_read(target_read),.target_ready(target_ready),.target_id(target_id),.target_offset(target_offset),.target_bridge(target_bridge),
  .target_length(target_length),.target_done(target_done),.target_error(target_error),.host_busy(host_busy),
  .read_error(read_error),.clk(cclk),.reset(reset),.host_region(2'd2),.disc_slot_id(disc_slot_id),
  .disc_select_strobe(disc_select_strobe),.disc_change_done(disc_change_done),.has_disc(has_disc),
  .disc_pending(disc_pending),.disc_generation(disc_generation),.sector_count(sector_count),.stream_idle(stream_idle),
  .cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba),.cd_hps_ack(cd_hps_ack),.cd_hps_write(cd_hps_write),
  .cd_hps_data(cd_hps_data),.trackinfo_addr(trackinfo_addr),.trackinfo_data(trackinfo_data),
  .trackinfo_write(trackinfo_write));
 core_bridge_cmd commands(.clk(hclk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
  .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
  .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
  .dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
  .dataslot_requestwrite_ack(1'b0),.dataslot_requestwrite_ok(1'b0),
  .dataslot_update(slot_update),.dataslot_update_id(update_id),.dataslot_update_size(update_size),
  .dataslot_allcomplete(allcomplete),
  .savestate_supported(1'b0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
  .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
  .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
  .target_dataslot_read(apf_read),.target_dataslot_write(apf_write),.target_dataslot_getfile(1'b0),
  .target_dataslot_openfile(1'b0),.target_dataslot_id(apf_id),.target_dataslot_slotoffset(apf_offset),
  .target_dataslot_bridgeaddr(apf_bridge),.target_dataslot_length(apf_length),
  .target_dataslot_done(apf_done),.target_dataslot_err(apf_error),
  .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
  .datatable_addr(apf_table_addr),.datatable_wren(1'b0),.datatable_data(0),.datatable_q(table_q));

 // ---------------- authored discs ----------------
 integer sectors_a=40, sectors_b=9;
 // Multi-track disc (case 6): data track 1 at slot 0x30, audio tracks at
 // 0x130 and 0x230, each later file starting with its 150-sector pregap.
 // Disc 0x31: track 2 is not whole sectors.
 integer mt_sectors[0:2]='{20,162,155};
 integer size_a,size_b,size_a2,generation=0;
 // LibCrypt build (case 8): slot 0x63 holds an .sbi with four 14-byte
 // entries, lanes 0 and 2: 03:08:05 (mask bit 15), 03:08:06 (no bit),
 // 03:18:49 (bit 10), 03:35:42 (bit 0) -> mask 0x8401.
 localparam integer SBI_BYTES=4+4*14;
 function automatic [7:0] sbi_byte(input integer offset);
  integer k,b;reg [23:0] msf;
  if(offset<4) return offset==0 ? "S" : offset==1 ? "B" : offset==2 ? "I" : 8'h00;
  k=(offset-4)/14;b=(offset-4)%14;
  msf=k==0 ? 24'h030805 : k==1 ? 24'h030806 : k==2 ? 24'h031849 : 24'h033542;
  if(b<3) return msf[23-8*b-:8];
  return b==3 ? 8'h01 : 8'(k*7+b);
 endfunction
 function automatic [7:0] disc_byte(input integer slot,input integer offset);
  integer s,b;s=offset/2352;b=offset%2352;
  if(slot==16'h63) return sbi_byte(offset);
  return 8'((b*29)^(s*101)^(slot*57)^(b>>3)^(s>>2)^(slot==16'h20 ? generation*13 : 0));
 endfunction
 function automatic integer slot_size(input integer slot);
  if(slot==16'h20) return generation ? size_a2 : size_a;
  if(slot==16'h21) return size_b;
  if(slot==16'h24) return 317787*2352;
  if(slot==16'h30) return mt_sectors[0]*2352;
  if(slot==16'h130) return mt_sectors[1]*2352;
  if(slot==16'h230) return mt_sectors[2]*2352;
  if(slot==16'h31) return 7*2352;
  if(slot==16'h131) return 160*2352+100;
  if(slot==16'h63) return SBI_BYTES;
  return -1;
 endfunction

 // ---------------- APF firmware model (host clock) ----------------
 function automatic [31:0] swap(input [31:0] x);return {x[7:0],x[15:8],x[23:16],x[31:24]};endfunction
 task automatic write_bus(input [31:0] addr,input [31:0] data);
  @(negedge hclk);bridge_addr=addr;bridge_data=data;
  @(negedge hclk);bridge_wr=1;
  @(negedge hclk);bridge_wr=0;
 endtask
 task automatic read_bus(input [31:0] addr,output [31:0] data);
  @(negedge hclk);bridge_addr=addr;bridge_rd=1;
  repeat(4) @(negedge hclk);
  data=swap(command_data);bridge_rd=0;
 endtask
 task automatic host_command(input [15:0] command);
  reg [31:0] result;
  write_bus(32'hf8000000,swap({16'h434d,command}));
  result=0;
  for(integer n=0;n<1000 && result[31:16]!==16'h4f4b;n++) read_bus(32'hf8000000,result);
  if(result[31:16]!=16'h4f4b) $fatal(1,"APF host command %h result %h",command,result);
 endtask
 // Table: unrelated slots first, so position != slot ID.
 task automatic write_table();
  write_bus(32'hf8002000,swap(32'h0));write_bus(32'hf8002004,swap(32'd524288));
  write_bus(32'hf8002008,swap(32'h10));write_bus(32'hf800200c,swap(32'd131072));
  write_bus(32'hf8002010,swap(32'h21));write_bus(32'hf8002014,swap(size_b));
  write_bus(32'hf8002018,swap(32'h20));write_bus(32'hf800201c,swap(size_a));
  write_bus(32'hf8002020,swap(32'h23));write_bus(32'hf8002024,swap(5*2352+100));  // partial sector
  write_bus(32'hf8002028,swap(32'h24));write_bus(32'hf800202c,swap(317787*2352)); // FFVII disc 1 size
  write_bus(32'hf8002030,swap(32'h25));write_bus(32'hf8002034,swap(450000*2352)); // 100 minutes
  write_bus(32'hf8002038,swap(32'h230));write_bus(32'hf800203c,swap(mt_sectors[2]*2352));
  write_bus(32'hf8002040,swap(32'h30));write_bus(32'hf8002044,swap(mt_sectors[0]*2352));
  write_bus(32'hf8002048,swap(32'h130));write_bus(32'hf800204c,swap(mt_sectors[1]*2352));
  write_bus(32'hf8002050,swap(32'h31));write_bus(32'hf8002054,swap(7*2352));
  write_bus(32'hf8002058,swap(32'h131));write_bus(32'hf800205c,swap(160*2352+100));
  if(`CD_STREAM_LC!=0) begin write_bus(32'hf8002060,swap(32'h63));write_bus(32'hf8002064,swap(SBI_BYTES));end
 endtask
 reg op_table=0,op_allcomplete=0,op_update=0;integer update_value=0;
 integer apf_latency=0,apf_spacing=0,fail_offset=-1,reads=0,read_bytes=0;
 integer apf_seed=7,sbi_reads=0;
 reg apf_enabled=1,apf_shuffle=0,apf_serving=0;
 task automatic serve_target();
  reg [31:0] status,id,offset,addr,length,word;integer delay;
  read_bus(32'hf8001000,status);
  if(status[31:16]!==16'h636d) return;
  if(status[15:0]==16'h0184) begin
   // Card write-back chunk: the firmware reads 128 words from the bridge.
   read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
   if(id!=16'h10 || addr!=32'h23000000 || length!=512) $fatal(1,"APF card write shape");
   write_bus(32'hf8001000,swap(32'h62750000));repeat(500) @(negedge hclk);
   write_bus(32'hf8001000,swap(32'h6f6b0000));return;
  end
  if(status[15:0]!=16'h0180) $fatal(1,"APF target opcode %h",status[15:0]);
  read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);
  read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
  if(id==16'h63) begin
   // The .sbi: one read of the whole file into a staging window.
   if(length!=SBI_BYTES || offset!=0 || addr<32'h24000000 || addr[11:0]!=0) $fatal(1,"APF .sbi read shape");
   write_bus(32'hf8001000,swap(32'h62750000));
   for(integer n=0;n<(SBI_BYTES+3)/4;n++)
    write_bus(addr+n*4,{sbi_byte(n*4+3),sbi_byte(n*4+2),sbi_byte(n*4+1),sbi_byte(n*4)});
   sbi_reads++;write_bus(32'hf8001000,swap(32'h6f6b0000));return;
  end
  if(length!=2352 || offset%2352!=0 || addr<32'h24000000 || addr>=32'h24000000+`CD_STREAM_NBUF*32'h1000 || addr[11:0]!=0)
   $fatal(1,"APF sector read shape id=%h offset=%0d addr=%h length=%0d",id,offset,addr,length);
  write_bus(32'hf8001000,swap(32'h62750000));apf_serving=1;
  delay=apf_latency==0 ? 0 : $urandom(apf_seed)%apf_latency;apf_seed++;
  repeat(delay) @(negedge hclk);
  if(slot_size(id)<0 || offset+2352>(slot_size(id)/2352)*2352)
   $fatal(1,"APF read outside the image slot=%h offset=%0d",id,offset);
  if(offset==fail_offset) begin
   write_bus(32'hf8001000,swap(32'h6f6b0003));fail_offset=-1;apf_serving=0;return;
  end
  // Stray writes outside the sector window must be ignored.
  if(apf_shuffle) begin write_bus(addr+2352,32'h5a5a5a5a);write_bus(addr^32'h1000,32'h5a5a5a5a);end
  for(integer n=0;n<588;n++) begin
   integer m;m=apf_shuffle ? (n*97+offset/2352)%588 : n;   // 97 is coprime with 588
   word={disc_byte(id,offset+m*4+3),disc_byte(id,offset+m*4+2),disc_byte(id,offset+m*4+1),disc_byte(id,offset+m*4)};
   write_bus(addr+m*4,word);
   if(apf_spacing) repeat($urandom(apf_seed+n)%apf_spacing) @(negedge hclk);
  end
  reads++;read_bytes+=2352;
  write_bus(32'hf8001000,swap(32'h6f6b0000));apf_serving=0;
 endtask
 initial begin
  wait(!host_reset);
  forever begin
   // The firmware model is the only bridge master: table writes, host
   // commands and target service are serialised here.
   if(op_table) begin write_table();op_table=0;end
   if(op_allcomplete) begin host_command(16'h8f);op_allcomplete=0;end
   if(op_update) begin
    write_bus(32'hf8000020,swap(32'h20));write_bus(32'hf8000024,swap(update_value));
    host_command(16'h8a);op_update=0;
   end
   if(apf_enabled) serve_target(); else @(negedge hclk);
  end
 end

 // ---------------- cd_top side ----------------
 reg [31:0] table_seen[0:399];integer track_writes=0,mounts=0,checks=0,sector_reads=0;
 reg [31:0] meta_a[0:399],meta_b[0:399],meta_a2[0:399],meta_big[0:399],meta_mt[0:399];
 always @(posedge cclk) begin
  if(trackinfo_write) begin table_seen[trackinfo_addr]<=trackinfo_data;track_writes<=track_writes+1;end
  if(cd_hps_write && !cd_hps_ack) $fatal(1,"Sector write outside acknowledge");
  if(disc_change_done && track_writes%400!=0) $fatal(1,"Disc change reported before the track table");
 end
 integer change_count=0;
 always @(posedge cclk) if(disc_change_done) change_count<=change_count+1;
 task automatic check_table(input integer which);
  for(integer i=0;i<400;i++) begin
   reg [31:0] want;
   want=which==0 ? meta_a[i] : which==1 ? meta_b[i] : which==2 ? meta_a2[i] : which==4 ? meta_mt[i] : meta_big[i];
   if(i==3 && `CD_STREAM_LC!=0) want|=32'h8401;   // the .sbi's LibCrypt mask (case 8)
   if(table_seen[i]!==want) $fatal(1,"Track table word %0d=%h expected %h",i,table_seen[i],want);
   checks++;
  end
 endtask
 function automatic [15:0] expected(input integer slot,input integer sectors,input integer lba,input integer h);
  if(lba<150) return 16'h0000;
  if(lba>=150+sectors) return 16'haaaa;
  if(slot==16'h30) begin
   // Multi-track: the track files back to back from LBA 150.
   integer f,k;f=lba-150;k=0;
   while(k<2 && f>=mt_sectors[k]) begin f-=mt_sectors[k];k++;end
   return {disc_byte(16'h30+k*16'h100,f*2352+h*2+1),disc_byte(16'h30+k*16'h100,f*2352+h*2)};
  end
  return {disc_byte(slot,(lba-150)*2352+h*2+1),disc_byte(slot,(lba-150)*2352+h*2)};
 endfunction
 integer max_wait=0,total_wait=0;
 // One HPS fetch as cd_top performs it (SFETCH_START .. SFETCH_HPSDATA).
 task automatic fetch(input integer slot,input integer sectors,input integer lba,input reg fail_expected);
  integer waited,h,gap;reg [15:0] got[0:1175];
  @(negedge cclk);while(cd_hps_ack) @(negedge cclk);
  cd_hps_lba=lba;cd_hps_req=1;waited=0;
  do begin @(posedge cclk);waited++;if(waited>4000000) $fatal(1,"Sector %0d never acknowledged",lba);end while(!cd_hps_ack);
  @(negedge cclk);cd_hps_req=0;
  if(waited>max_wait) max_wait=waited;total_wait+=waited;
  gap=0;
  do begin @(posedge cclk);gap++;if(gap>8) $fatal(1,"Sector data did not follow acknowledge");end while(!cd_hps_write);
  for(h=0;h<1176;h++) begin
   if(!cd_hps_write) $fatal(1,"Sector write burst interrupted at %0d",h);
   got[h]=cd_hps_data;@(posedge cclk);
  end
  // slot -1: the sector raced a disc change; it must be wholly A or wholly B.
  if(slot<0) begin
   slot=16'h20;sectors=sectors_a;
   for(h=0;h<1176;h++) if(got[h]!==expected(16'h20,sectors_a,lba,h)) begin slot=16'h21;sectors=sectors_b;end
  end
  for(h=0;h<1176;h++) begin
   if(got[h]!==(fail_expected ? 16'haaaa : expected(slot,sectors,lba,h)))
    $fatal(1,"Sector data mismatch slot=%h lba=%0d halfword=%0d got=%h expected=%h",slot,lba,h,got[h],
     fail_expected ? 16'haaaa : expected(slot,sectors,lba,h));
   checks++;
  end
  if(cd_hps_write) $fatal(1,"Sector write burst too long");
  gap=0;
  while(cd_hps_ack) begin @(posedge cclk);gap++;if(gap>4) $fatal(1,"Acknowledge held after the sector");end
  sector_reads++;
 endtask
 task automatic select(input [15:0] slot);
  integer prior,n;prior=change_count;
  @(negedge cclk);disc_slot_id=slot;disc_select_strobe=1;@(negedge cclk);disc_select_strobe=0;
  n=0;while(change_count==prior) begin @(posedge cclk);n++;if(n>2000000) $fatal(1,"Disc change never completed");end
 endtask
 task automatic sequential(input integer slot,input integer sectors,input integer first,input integer count,input integer pause);
  for(integer l=first;l<first+count;l++) begin fetch(slot,sectors,l,0);repeat(pause) @(negedge cclk);end
 endtask

 integer test_case=0,base_changes;
 initial begin
  if($value$plusargs("CASE=%d",test_case)) begin end
  base_changes=0;
  $readmemh({"meta-a.hex"},meta_a);if(`CD_STREAM_TRACKS>1) $readmemh({"meta-mt.hex"},meta_mt);$readmemh({"meta-b.hex"},meta_b);$readmemh({"meta-a2.hex"},meta_a2);$readmemh({"meta-big.hex"},meta_big);
  size_a=sectors_a*2352;size_b=sectors_b*2352;size_a2=30*2352;
  if(test_case==1) begin apf_latency=60000;apf_spacing=6;apf_shuffle=1;end  // up to ~0.8 ms per sector
  else if(test_case==5) begin apf_latency=60000;apf_spacing=2;end
  else begin apf_latency=400;apf_spacing=2;end
  repeat(6) @(negedge cclk);host_reset=0;reset=0;
  op_table=1;
  if(test_case==2) begin update_value=size_a;op_update=1;end  // first mount from a slot update
  else op_allcomplete=1;
  wait(has_disc);repeat(20) @(negedge cclk);
  base_changes=change_count;
  check_table(0);
  case(test_case)
   2: begin
    // APF read error: that sector becomes 0xAA fill, later sectors recover.
    fail_offset=(170-150)*2352;
    fetch(16'h20,sectors_a,170,1);
    if(!read_error) $fatal(1,"APF read error not reported");
    fetch(16'h20,sectors_a,171,0);fetch(16'h20,sectors_a,172,0);fetch(16'h20,sectors_a,170,0);
   end
   3: begin
    // The user loads the next disc's file into the mounted slot (APF 0x8A):
    // pending only, the old file keeps serving until the swap strobe.
    sequential(16'h20,sectors_a,150,4,0);
    @(negedge hclk);generation=1;update_value=size_a2;op_update=1;
    wait(disc_pending);repeat(3000) @(negedge cclk);
    if(track_writes!=400) $fatal(1,"Slot reload remounted without a swap");
    fetch(16'h20,sectors_a,154,1);   // old table, new file: unreadable until the swap
    select(16'h20);
    if(disc_pending || !has_disc || change_count!=base_changes+1) $fatal(1,"Reload swap state");
    check_table(2);
    fetch(16'h20,30,179,0);fetch(16'h20,30,180,0);fetch(16'h20,30,150,0);fetch(16'h20,30,151,0);
   end
   4: begin
    // Selections racing with sector reads, and two strobes back to back:
    // the later selection must win and be the one acknowledged.
    fork
     for(integer l=150;l<156;l++) fetch(-1,0,l,0);
     begin repeat(500) @(negedge cclk);
      @(negedge cclk);disc_slot_id=16'h22;disc_select_strobe=1;@(negedge cclk);disc_slot_id=16'h21;
      @(negedge cclk);disc_select_strobe=0;
     end
    join
    wait(change_count==base_changes+1);repeat(5000) @(negedge cclk);
    if(change_count!=base_changes+1 || !has_disc) $fatal(1,"Back-to-back selection change count=%0d",change_count);
    check_table(1);fetch(16'h21,sectors_b,150,0);fetch(16'h21,sectors_b,158,0);fetch(16'h21,sectors_b,159,0);
   end
   5: begin
    // Transport reset while an APF read is still running (slow SD). The
    // modelled arbiter keeps the port leased (ready low) until that command
    // finishes; its done and late staging writes must not complete the new
    // loader's first read. The remount comes from the table (strobe), so
    // the new command is issued while the old one is still active.
    fetch(16'h20,sectors_a,160,0);
    wait(apf_serving);repeat(50) @(negedge hclk);
    if(!host_busy) $fatal(1,"Expected a prefetch in flight");
    @(negedge cclk);host_reset=1;reset=1;ready_hold=1;
    repeat(50) @(negedge cclk);
    if(has_disc || cd_hps_ack || cd_hps_write) $fatal(1,"Transport reset left state");
    host_reset=0;reset=0;
    select(16'h20);if(!has_disc) $fatal(1,"Remount after reset");
    fork
     begin wait(!apf_serving);repeat(30) @(negedge hclk);ready_hold=0;end
     begin fetch(16'h20,sectors_a,170,0);end
    join
    if(ready_hold) $fatal(1,"Read completed while the port was still leased");
    check_table(0);sequential(16'h20,sectors_a,171,3,0);fetch(16'h20,sectors_a,100,0);
   end
   6: begin
    // Multi-track: table, every track boundary, seeks back and forth, end.
    integer total;total=mt_sectors[0]+mt_sectors[1]+mt_sectors[2];
    select(16'h30);if(!has_disc || sector_count!=total) $fatal(1,"Multi-track disc sectors=%0d",sector_count);
    check_table(4);
    fetch(16'h30,total,149,0);sequential(16'h30,total,150,3,0);
    sequential(16'h30,total,150+mt_sectors[0]-2,4,0);           // into track 2's pregap
    fetch(16'h30,total,150+mt_sectors[0]+150,0);                // track 2 INDEX 01
    sequential(16'h30,total,150+mt_sectors[0]+mt_sectors[1]-1,3,0); // into track 3
    fetch(16'h30,total,150+total-1,0);fetch(16'h30,total,150+total,0);
    fetch(16'h30,total,160,0);fetch(16'h30,total,150+mt_sectors[0]+200,0);fetch(16'h30,total,151,0);
    // A track file that is not whole sectors makes the disc unreadable.
    select(16'h31);if(has_disc) $fatal(1,"Partial-sector track accepted");
    select(16'h20);if(!has_disc || sector_count!=sectors_a) $fatal(1,"Single-track disc after multi-track");check_table(0);
    sequential(16'h20,sectors_a,150,3,0);
   end
   7: begin
    // APF 0x8A for the mounted disc slot right after the first mount, before
    // the guest has read anything (seen at start-up?): remount at once, no
    // pending file, sectors keep coming. After a read, a 0x8A is a disc
    // change again and waits for the swap (as in case 3).
    @(negedge hclk);update_value=size_a;op_update=1;
    for(integer n=0;track_writes!=800;n++) begin
     @(posedge cclk);
     if(n>2000000) $fatal(1,"Early slot update left the disc pending=%b has=%b (no remount)",disc_pending,has_disc);
    end
    repeat(3000) @(negedge cclk);
    if(disc_pending || !has_disc) $fatal(1,"Early slot update left the disc pending=%b has=%b",disc_pending,has_disc);
    check_table(0);
    sequential(16'h20,sectors_a,150,4,0);fetch(16'h20,sectors_a,170,0);
    @(negedge hclk);generation=1;update_value=size_a2;op_update=1;
    wait(disc_pending);repeat(3000) @(negedge cclk);
    if(track_writes!=800) $fatal(1,"Slot reload after a read remounted without a swap");
    fetch(16'h20,sectors_a,171,1);
    select(16'h20);
    if(disc_pending || !has_disc) $fatal(1,"Reload swap state after early remount");
    check_table(2);fetch(16'h20,30,150,0);fetch(16'h20,30,179,0);
   end
   8: begin
    // LibCrypt build: every mount reads the .sbi and puts its mask in
    // track word 3; buffer 0 serves sectors normally afterwards.
    if(sbi_reads!=1) $fatal(1,"Expected one .sbi read, got %0d",sbi_reads);
    sequential(16'h20,sectors_a,150,6,0);fetch(16'h20,sectors_a,170,0);
    select(16'h21);if(!has_disc || sbi_reads!=2) $fatal(1,"Second mount .sbi reads=%0d",sbi_reads);
    check_table(1);fetch(16'h21,sectors_b,150,0);sequential(16'h21,sectors_b,151,4,0);
   end
   default: begin
    // Pregap, sequential prefetch, repeat, seeks, image end, past end.
    fetch(16'h20,sectors_a,0,0);fetch(16'h20,sectors_a,149,0);
    sequential(16'h20,sectors_a,150,12,test_case==1 ? 3000 : 14000);
    fetch(16'h20,sectors_a,161,0);fetch(16'h20,sectors_a,155,0);fetch(16'h20,sectors_a,156,0);
    fetch(16'h20,sectors_a,150+sectors_a-1,0);fetch(16'h20,sectors_a,150+sectors_a,0);
    fetch(16'h20,sectors_a,300000,0);fetch(16'h20,sectors_a,32'h00ffffff,0);
    // Disc A sector 170 leaves 171 prefetched; disc B must not see it.
    fetch(16'h20,sectors_a,170,0);repeat(20000) @(negedge cclk);
    select(16'h21);if(!has_disc) $fatal(1,"Disc B not present");check_table(1);
    fetch(16'h21,sectors_b,171,0);fetch(16'h21,sectors_b,158,0);fetch(16'h21,sectors_b,159,0);
    select(16'h22);if(has_disc) $fatal(1,"Absent slot reported a disc");
    fetch(16'h22,0,150,1);
    select(16'h23);if(has_disc || sector_count!=0) $fatal(1,"Partial-sector image accepted");
    select(16'h24);if(!has_disc || sector_count!=317787) $fatal(1,"Full-size disc not mounted");check_table(3);
    fetch(16'h24,317787,150+317786,0);
    select(16'h25);if(has_disc) $fatal(1,"Disc past 99 minutes accepted");
    select(16'h20);if(!has_disc) $fatal(1,"Disc A not restored");check_table(0);
    sequential(16'h20,sectors_a,180,4,0);
   end
  endcase
  if(test_case!=2 && read_error) $fatal(1,"Unexpected APF read error");
`ifdef CD_STREAM_ARB
  if(card_commands==0 || card_errors!=0) $fatal(1,"Card client did not share the port: %0d/%0d",card_commands,card_errors);
`endif
  $display("PASS CD STREAM case=%0d card_commands=%0d checks=%0d sectors=%0d apf_reads=%0d bytes=%0d max_wait=%0d mean_wait=%0d",
   test_case,card_commands,checks,sector_reads,reads,read_bytes,max_wait,total_wait/(sector_reads==0 ? 1:sector_reads));
  $finish;
 end
 initial begin #2000000000;$fatal(1,"CD stream timeout");end
endmodule
