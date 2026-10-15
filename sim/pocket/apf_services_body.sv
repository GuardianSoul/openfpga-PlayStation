// SPDX-License-Identifier: MIT
// Actual CD client, no stand-in card descriptors or table leases.
reg cd_clk=0,disc_select_strobe=0,cd_hps_req=0;
always #14.763 cd_clk=~cd_clk;
reg [31:0] cd_hps_lba=0;
wire cd_hps_ack,cd_hps_write,has_disc,disc_pending,disc_change_done;
wire cd_host_busy,stream_idle,cd_read_error,target_idle;
wire [15:0] cd_hps_data;
wire [7:0] disc_generation;
wire [19:0] sector_count;
wire [8:0] trackinfo_addr;
wire [31:0] trackinfo_data;
wire trackinfo_write,slot_update;
wire [15:0] slot_update_id;
wire [31:0] slot_update_size;
integer cd_sectors=0,cd_halfwords=0,cd_reads=0,track_words=0,overlap=0;
integer cd_table_waits=0,table_contention=0;
reg cd_done=0,cd_error_injected=0;
reg [31:0] meta_a[0:399],meta_b[0:399];
string meta_a_path,meta_b_path;
initial begin
 if(!$value$plusargs("META_A=%s",meta_a_path) || !$value$plusargs("META_B=%s",meta_b_path))
  $fatal(1,"Authored CD metadata required");
 $readmemh(meta_a_path,meta_a);$readmemh(meta_b_path,meta_b);
end
function automatic [31:0] disc_word(input integer word_index);
 return 32'h89abcdef ^ (word_index*32'h01297463);
endfunction
always @(posedge cd_clk) begin
 if(trackinfo_write) begin
  if(trackinfo_addr!==9'(track_words%400) ||
     trackinfo_data!==(track_words<400 ? meta_a[track_words%400]:meta_b[track_words%400]))
   $fatal(1,"Shared CD metadata/order mismatch word=%0d",track_words);
  track_words++;
 end
 if(cd_hps_write && !cd_hps_ack) $fatal(1,"Shared CD write outside ACK");
end
always @(posedge clk) begin
 if(dut.client_busy==2'b11) overlap++;
 if(dut.table_request==2'b11) table_contention++;
 if(dut.client_done[0] && !dut.disc.done_low_seen && dut.disc.accepted && dut.disc.target_read)
  $fatal(1,"Shared CD completion precedes acceptance");
end
// A lookup has two registered-read wait clocks. They must restart/stall while
// another client owns the table, even if a lucky final byte result matches.
always @(posedge clk) if(!reset && dut.table_request[0] && !dut.table_grant[0]) begin
 cd_table_waits++;
 #0.001;
 if(dut.disc.twait!==0) $fatal(1,"CD table advanced without ownership");
end
// Called only by the ONE firmware bus master, also servicing the card chunks.
task automatic serve_cd_target;
 reg [31:0] id,offset,addr,length;
 read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);
 read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
 if(id!=0 || offset%2352 || offset+2352>40*2352 || length!=2352 ||
    addr<32'h24000000 || addr>=32'h24002000 || addr[11:0]!=0)
  $fatal(1,"Shared CD target shape/id mismatch");
 write_bus(32'hf8001000,swap(32'h62750000));
 // Sticky result from a preceding card command must not finish this read.
 repeat(137) @(negedge clk);
 if(test_case==6 && offset==6*2352 && !cd_error_injected) begin
  cd_error_injected=1;write_bus(32'hf8001000,swap(32'h6f6b0003));
 end else begin
  for(integer n=0;n<588;n++) begin
   // Shuffled sector writes coexist with the card's readback staging window.
   integer index;index=(n*97)%588;
   write_bus(addr+4*index,disc_word(offset/4+index));
  end
  write_bus(32'hf8001000,swap(32'h6f6b0000));
 end
 cd_reads++;
endtask
task automatic wait_card_target(output reg [31:0] status);
 while(1) begin
  status=0;
  while(status[31:16]!==16'h636d) read_bus(32'hf8001000,status);
  if(status[15:0]==16'h184) return;
  if(status[15:0]!=16'h180) $fatal(1,"Unknown shared target command %h",status);
  serve_cd_target();
 end
endtask
task automatic fetch_cd(input integer lba,input reg expect_error);
 reg [31:0] word;reg [15:0] expected;
 integer waited;
 @(negedge cd_clk);while(cd_hps_ack) @(negedge cd_clk);
 cd_hps_lba=lba;cd_hps_req=1;waited=0;
 do begin @(posedge cd_clk);waited++;if(waited>4000000) $fatal(1,"Shared CD demand starved");end while(!cd_hps_ack);
 @(negedge cd_clk);cd_hps_req=0;waited=0;
 do begin @(posedge cd_clk);waited++;if(waited>8) $fatal(1,"Shared CD replay missing");end while(!cd_hps_write);
 for(integer h=0;h<1176;h++) begin
  word=disc_word((lba-150)*588+h/2);
  expected=expect_error || lba>=190 ? 16'haaaa:lba<150 ? 16'h0000:h%2 ? word[31:16]:word[15:0];
  if(!cd_hps_write || cd_hps_data!==expected)
   $fatal(1,"Shared sector bytes/order mismatch lba=%0d halfword=%0d got=%h expected=%h",lba,h,cd_hps_data,expected);
  cd_halfwords++;@(posedge cd_clk);
 end
 if(cd_hps_write) $fatal(1,"Shared CD replay too long");
 while(cd_hps_ack) @(negedge cd_clk);
 cd_sectors++;
endtask
initial begin
 wait(mounted==3 && has_disc);
 fetch_cd(150,0);fetch_cd(151,0);fetch_cd(156,test_case==6);fetch_cd(157,0);
 fetch_cd(189,0);fetch_cd(190,0);fetch_cd(149,0);fetch_cd(152,0);
 fetch_cd(153,0);fetch_cd(154,0);fetch_cd(155,0);fetch_cd(158,0);
 cd_done=1;
end
task automatic finish_cd;
 reg [31:0] status;
 // No card commands remain, but CD prefetch/demand may still be pending.
 while(!cd_done || !target_idle || cd_host_busy) begin
  read_bus(32'hf8001000,status);
  if(status[31:16]==16'h636d) begin
   if(status[15:0]!=16'h180) $fatal(1,"Unexpected late card command");
   serve_cd_target();
  end
 end
 if(cd_sectors!=12 || cd_halfwords!=12*1176 || cd_reads<8 || track_words!=400 || overlap==0 ||
    cd_table_waits==0 || (test_case==0 && table_contention==0) ||
    cd_read_error!=(test_case==6) || cd_error_injected!=(test_case==6))
  $fatal(1,"Shared CD/card coverage/error isolation");
 // A runtime disc reload must not reinitialize either resident save.
 write_bus(32'hf8000020,swap(0));write_bus(32'hf8000024,swap(41*2352));host_command(16'h8a,0);
 wait(disc_pending);
 if(mounted!=3 || !ready || busy || freeze || dirty) $fatal(1,"Reload changed resident card service");
 @(negedge cd_clk);disc_select_strobe=1;@(negedge cd_clk);disc_select_strobe=0;
 wait(disc_generation==2);repeat(8) @(negedge cd_clk);
 if(track_words!=800 || sector_count!=41 || disc_pending || mounted!=3 || !ready || dirty)
  $fatal(1,"Shared disc reload metadata/card isolation");
endtask
