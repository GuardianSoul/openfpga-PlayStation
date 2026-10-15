`timescale 1ns/1ps
// Actual CD loader + card service, real APF handler/table and async card fabric.
module tb_apf_services;
 reg clk=0,reset=1,memory_ready=1,bridge_wr=0,bridge_rd=0;
 always #6.734 clk=~clk;
 reg [31:0] bridge_addr=0,bridge_data=0;
 wire [31:0] command_data,staging_data;
 wire staging_selected,slot_request,slot_ack,slot_ok,allcomplete;
 wire [15:0] slot_id;wire [31:0] slot_size;
 reg flush_request=0,flush_card=0;reg [1:0] guest_dirty=0;
 wire [1:0] image_valid,dirty;wire busy,freeze,failed,flush_done;
 wire freeze_ack;integer ticks=0,finished=0,adoption_wait=0;
 reg guest_clk=0,memory_clk=0;
 always #7.381 guest_clk=~guest_clk;
 always #8.081 memory_clk=~memory_clk;
 wire guest_pause;reg guest_valid=0,guest_return_ready=0;
 wire [1:0] source_ready,source_return,source_error;
 wire [1:0][127:0] source_data;
 wire m_valid,m_write,m_ready,m_rsp_ready;
 wire [24:0] m_addr;wire [3:0] m_halfwords;
 wire [127:0] m_data;wire [15:0] m_be;
 wire fabric_error;integer pin_delay=0;
 wire backend_idle=!mem_busy && !rsp_valid && pin_delay==0;
 reg [31:0] guest_word=0;
 psx_memcard_fabric fabric(.guest_clk(guest_clk),.host_clk(clk),.memory_clk(memory_clk),.reset(reset),
  .host_freeze(freeze),.guest_stopped(guest_pause),.backend_idle(backend_idle),
  .guest_pause(guest_pause),.host_owned(freeze_ack),
  .s_valid({req_valid,guest_valid}),.s_write({req_write,1'b1}),.s_ready(source_ready),
  .s_addr({req_addr,25'h1234}),.s_halfwords({req_halfwords,4'd2}),
  .s_wdata({req_data,128'hcafebade}),.s_byte_en({req_be,16'hf}),
  .s_rsp_valid(source_return),.s_rsp_ready({rsp_ready,guest_return_ready}),
  .s_rsp_rdata(source_data),.s_rsp_error(source_error),
  .req_valid(m_valid),.req_write(m_write),.req_ready(m_ready),.req_addr(m_addr),
  .req_halfwords(m_halfwords),.req_wdata(m_data),.req_byte_en(m_be),
  .rsp_valid(rsp_valid),.rsp_ready(m_rsp_ready),.rsp_rdata(rsp_data),.rsp_error(rsp_error),
  .protocol_error(fabric_error));
 wire req_valid,req_write,rsp_ready;wire [24:0] req_addr;
 wire [3:0] req_halfwords;wire [127:0] req_data;wire [15:0] req_be;
 reg rsp_valid=0,rsp_error=0;reg [127:0] rsp_data=0;
 reg mem_busy=0,hold_final=0,force_error=0,force_stall=0;
 integer latency=0,mem_index=0,packets=0,words_written=0,words_saved=0,commands_saved=0;
 reg mem_card=0,mem_write=0;reg [127:0] mem_data=0;reg [3:0] mem_width=0;
 wire req_ready=source_ready[1];
 assign m_ready=backend_idle && ticks%11>2 && !force_stall;
 reg [31:0] memory[0:1][0:32767];
 reg [31:0] saved[0:1][0:32767];
 wire target_write=dut.client_valid[1];
 
 psx_apf_services dut(.clk(clk),.reset(reset),.cd_clk(cd_clk),.cd_reset(reset),
  .memory_ready(memory_ready),.memory_idle(memory_idle),.initialize_request(initialize_request),
  .slot_request(slot_request),.slot_id(slot_id),.slot_size(slot_size),.slot_ack(slot_ack),.slot_ok(slot_ok),
  .allcomplete(allcomplete),.bridge_wr(bridge_wr),.bridge_addr(bridge_addr),.bridge_data(bridge_data),
  .staging_data(staging_data),.staging_selected(staging_selected),
  .flush_request(flush_request),.flush_card(flush_card),.guest_dirty(guest_dirty),
  .image_valid(image_valid),.dirty(dirty),.mounted(mounted),.freeze(freeze),.freeze_ack(freeze_ack),
  .busy(busy),.ready(ready),.failed(failed),.flush_done(flush_done),
  .req_valid(req_valid),.req_write(req_write),.req_ready(req_ready),.req_addr(req_addr),.req_halfwords(req_halfwords),
  .req_data(req_data),.req_be(req_be),.rsp_valid(source_return[1]),.rsp_error(source_error[1]),.rsp_data(source_data[1]),.rsp_ready(rsp_ready),
  .target_read(apf_target_read),.target_write(apf_target_write),.target_id(apf_target_id),
  .target_offset(apf_target_offset),.target_bridge(apf_target_bridge),.target_length(apf_target_length),
  .target_done(apf_target_done),.target_error(apf_target_error),
  .table_active(table_active),.datatable_addr(datatable_addr),.datatable_wren(datatable_wren),.datatable_data(datatable_data),.datatable_q(datatable_q),
  .slot_update(slot_update),.slot_update_id(slot_update_id),.slot_update_size(slot_update_size),
  .host_region(2'd2),.disc_slot_id(16'd0),.disc_select_strobe(disc_select_strobe),
  .disc_change_done(disc_change_done),.has_disc(has_disc),.disc_pending(disc_pending),.disc_generation(disc_generation),.sector_count(sector_count),
  .cd_host_busy(cd_host_busy),.stream_idle(stream_idle),.cd_read_error(cd_read_error),
  .cd_hps_req(cd_hps_req),.cd_hps_lba(cd_hps_lba),.cd_hps_ack(cd_hps_ack),.cd_hps_write(cd_hps_write),.cd_hps_data(cd_hps_data),
  .trackinfo_addr(trackinfo_addr),.trackinfo_data(trackinfo_data),.trackinfo_write(trackinfo_write),
  .target_idle(target_idle),.protocol_error(arb_error));
 core_bridge_cmd commands(.clk(clk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
  .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
  .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
  .dataslot_requestwrite(slot_request),.dataslot_requestwrite_id(slot_id),
  .dataslot_requestwrite_size(slot_size),.dataslot_requestwrite_ack(slot_ack),.dataslot_requestwrite_ok(slot_ok),
  .dataslot_allcomplete(allcomplete),.dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
  .dataslot_update(slot_update),.dataslot_update_id(slot_update_id),.dataslot_update_size(slot_update_size),
  .savestate_supported(1'b0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
  .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
  .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
  .target_dataslot_read(apf_target_read),.target_dataslot_write(apf_target_write),.target_dataslot_getfile(0),.target_dataslot_openfile(0),
  .target_dataslot_id(apf_target_id),.target_dataslot_slotoffset(apf_target_offset),
  .target_dataslot_bridgeaddr(apf_target_bridge),.target_dataslot_length(apf_target_length),
  .target_dataslot_done(apf_target_done),.target_dataslot_err(apf_target_error),
  .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
  .datatable_addr(datatable_addr),.datatable_wren(datatable_wren),.datatable_data(datatable_data),.datatable_q(datatable_q));
 function automatic [31:0] swap(input [31:0] x);
  return {x[7:0],x[15:8],x[23:16],x[31:24]};
 endfunction
 function automatic [31:0] file_word(input integer card,input integer index);
  return 32'hb17a0329 ^ (index*32'h0001937b) ^ (card*32'hace59240);
 endfunction
 task automatic write_bus(input [31:0] addr,input [31:0] data);
  @(negedge clk);bridge_addr=addr;bridge_data=data;
  @(negedge clk);bridge_wr=1;
  @(negedge clk);bridge_wr=0;
 endtask
 task automatic read_bus(input [31:0] addr,output [31:0] data);
  @(negedge clk);bridge_addr=addr;bridge_rd=1;
  repeat(4) @(negedge clk);
  data=addr[31:24]==8'hf8 ? swap(command_data) : staging_data;bridge_rd=0;
 endtask
 task automatic host_command(input [15:0] command,input [15:0] expected);
  reg [31:0] result;
  write_bus(32'hf8000000,swap({16'h434d,command}));
  result=0;
  for(integer n=0;n<1000 && result[31:16]!==16'h4f4b;n++) read_bus(32'hf8000000,result);
  if(result!={16'h4f4b,expected}) $fatal(1,"APF card command result %h",result);
 endtask
 task automatic open_slot(input integer card,input integer bytes,input integer expected);
  write_bus(32'hf8000020,swap(16'h10+card));write_bus(32'hf8000024,swap(bytes));
  host_command(16'h82,expected);repeat(8) @(negedge clk);
 endtask
 task automatic load_card(input integer card);
  open_slot(card,131072,0);
  hold_final=1;
  for(integer n=0;n<32768;n++) begin
   write_bus(32'h21000000+card*32'h01000000+n*4,file_word(card,n));repeat(85) @(negedge clk);
  end
  host_command(16'h8f,0);repeat(20) @(negedge clk);
  if(image_valid[card] || !freeze || failed) $fatal(1,"Card mounted before physical final write");
  hold_final=0;wait(image_valid[card]);repeat(8) @(negedge clk);
  if(busy || failed) $fatal(1,"Card load did not drain");
  for(integer n=0;n<32768;n++) if(memory[card][n]!==file_word(card,n)) $fatal(1,"Card load bytes/window mismatch");
 endtask
 task automatic mark_guest(input integer card);
  // Protocol-side bench supplied independently by Claude; here an authored
  // committed sector write tests persistence and dirty handling only.
  for(integer n=320;n<352;n++) memory[card][n]=file_word(card,n)^32'hbade1943;
  @(negedge clk);guest_dirty=1<<card;@(negedge clk);guest_dirty=0;
 endtask
 task automatic save_card(input integer card,input integer fail_command);
  reg [31:0] result,id,offset,addr,length,value;
  integer before_finished;
  before_finished=finished;
  @(negedge clk);flush_card=card;flush_request=1;@(negedge clk);flush_request=0;
  for(integer c=0;c<(fail_command ? 1:256);c++) begin
   wait_card_target(result);
   if(result[15:0]!=16'h184 || !freeze_ack || !freeze) $fatal(1,"APF save opcode/freeze");
   read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);
   read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
   if(id!=16'h10+card || offset!=c*512 || addr!=32'h23000000 || length!=512)
    $fatal(1,"APF save chunk metadata/order");
   write_bus(32'hf8001000,swap(32'h62750000));
   for(integer n=0;n<128;n++) begin
    read_bus(addr+n*4,value);
    if(value!==memory[card][c*128+n]) $fatal(1,"Card save readback mismatch card=%0d word=%0d",card,c*128+n);
    saved[card][c*128+n]=value;words_saved++;repeat(83) @(negedge clk);
    if(!freeze || !freeze_ack) $fatal(1,"Snapshot unpaused before APF completes");
   end
   commands_saved++;
   write_bus(32'hf8001000,swap(fail_command ? 32'h6f6b0003:32'h6f6b0000));
   // Clear the target result poll variable. The real handler retains done;
   // the next command must clear it before the engine advances its chunk.
   repeat(12) @(negedge clk);
  end
  wait(finished>before_finished);repeat(8) @(negedge clk);
  if(busy || failed!=fail_command || dirty[card]!=fail_command)
   $fatal(1,"Save completion/error/dirty mismatch");
  if(!fail_command) for(integer n=0;n<32768;n++)
   if(saved[card][n]!==memory[card][n]) $fatal(1,"Persisted full-image mismatch");
 endtask
 always @(posedge clk) begin
  ticks<=ticks+1;
  if(flush_done) finished<=finished+1;
  if(slot_ack && !freeze_ack || req_valid && !freeze_ack) $fatal(1,"Card freeze/drain bypass");
  if(fabric_error || arb_error) $fatal(1,"Card fabric/target protocol error");
  if(dut.cards.init_ready && !ready) adoption_wait<=adoption_wait+1;
  else adoption_wait<=0;
  if(adoption_wait>200) $fatal(1,"Initialized images not adopted");
  if(dut.cards.init_ready && !mounted && !freeze) $fatal(1,"Ownership released before adoption");
  if(target_write && !target_ready) $fatal(1,"Card publish bypassed target readiness");
  if(mounted && (!ready || mounted!=3 || image_valid!=3 ||
       commands.idt.m[7]!=131072 || commands.idt.m[63]!=131072))
   $fatal(1,"Service mounted before image adoption and table commits");
 end
 always @(posedge memory_clk) begin
  if(pin_delay>0) pin_delay<=pin_delay-1;
  if(m_valid && m_ready) begin
   if(m_halfwords!=2 && m_halfwords!=4 || m_be!=(m_halfwords==2 ? 15:255) || m_addr[0]!=0 || !m_write && m_halfwords!=2) $fatal(1,"Card packet mask/alignment");
   if(m_addr>=25'h80000 && m_addr<25'h90000) begin mem_card<=0;mem_index<=(m_addr-25'h80000)/2;end
   else if(m_addr>=25'h100000 && m_addr<25'h110000) begin mem_card<=1;mem_index<=(m_addr-25'h100000)/2;end
   else if(m_addr==25'h1234) mem_index<=-1;
   else $fatal(1,"Card packet escaped logical window");
   mem_data<=m_data;mem_width<=m_halfwords;mem_write<=m_write;mem_busy<=1;latency<=packets%23+4;packets++;
  end
  if(mem_busy && !rsp_valid && !(hold_final && mem_write && mem_index>=32766)) begin
   if(latency>0) latency<=latency-1;
   else begin
    rsp_error<=force_error;rsp_valid<=1;
    if(mem_write && !force_error) begin
     if(mem_index==-1) guest_word<=mem_data[31:0];
     else begin
      memory[mem_card][mem_index]<=mem_data[31:0];words_written++;
      if(mem_width==4) begin memory[mem_card][mem_index+1]<=mem_data[63:32];words_written++;end
     end
    end
    else rsp_data<={96'b0,(mem_index==-1 ? guest_word:memory[mem_card][mem_index])};
   end
  end
  if(rsp_valid && m_rsp_ready) begin rsp_valid<=0;mem_busy<=0;pin_delay<=13;end
  if(ticks>40000000) $fatal(1,"Memcard transfer timeout");
 end
 // Additional real CD consumer and serialized firmware tasks.
`include "apf_services_body.sv"
 integer test_case=0;
 string blank_path;reg [31:0] blank_words[0:32767];
 reg initialize_request=0;wire ready;wire [1:0] mounted;
 wire table_active,datatable_wren;wire [9:0] datatable_addr;wire [31:0] datatable_data,datatable_q;
 wire arb_error;
 wire apf_target_read,apf_target_write,apf_target_done;wire [2:0] apf_target_error;
 wire [15:0] apf_target_id;wire [31:0] apf_target_offset,apf_target_bridge,apf_target_length;
 wire target_ready=dut.client_ready[1];
 reg backend_idle_meta=0,backend_idle_sync=0;
 always @(posedge clk) begin backend_idle_meta<=backend_idle;backend_idle_sync<=backend_idle_meta;end
 wire memory_idle=backend_idle_sync && source_ready[1] && !source_return[1] && !req_valid;
 initial begin
  if($value$plusargs("CASE=%d",test_case)) begin end
  if(!$value$plusargs("BLANK=%s",blank_path)) $fatal(1,"Blank reference required");
  $readmemh(blank_path,blank_words);
  for(integer card=0;card<2;card++) for(integer n=0;n<32768;n++) begin memory[card][n]=32'hdeaddead;saved[card][n]=0;end
  repeat(10) @(negedge clk);reset=0;
  for(integer n=0;n<32;n++) begin
   write_bus(32'hf8002000+8*n,swap(32'hffff));write_bus(32'hf8002004+8*n,swap(0));
  end
  write_bus(32'hf80020e8,swap(0));write_bus(32'hf80020ec,swap(40*2352));
  // ID16/17 intentionally live at table positions3/31, populated through
  // the real APF B-port rather than direct writes to the helper's table.
  write_bus(32'hf8002018,swap(16'h10));write_bus(32'hf800201c,swap(test_case==1 || test_case==2 ? 131072:test_case==3 ? 131071:0));
  write_bus(32'hf80020f8,swap(16'h11));write_bus(32'hf80020fc,swap(test_case==2 ? 131072:0));
  if(test_case==1 || test_case==2) load_card(0);
  if(test_case==2) load_card(1);
  host_command(16'h8f,0);
  hold_final=1;
  @(negedge clk);initialize_request=1;@(negedge clk);initialize_request=0;
  if(test_case==3) begin
   wait(failed);repeat(20) @(negedge clk);
   if(mounted || ready || image_valid || !freeze || words_written) $fatal(1,"Invalid present save reformatted");
  end else if(test_case==5) begin
   // An unsolicited card upload at the validity handoff must not override
   // the transfer engine's protocol error and mount the image anyway.
   hold_final=0;wait(dut.cards.adopting && freeze_ack);
   @(negedge clk);bridge_addr=32'h21000000;bridge_data=32'hdeaddead;bridge_wr=1;
   @(negedge clk);bridge_wr=0;
   wait(failed);repeat(20) @(negedge clk);
   if(mounted || ready || image_valid || !freeze) $fatal(1,"Late upload bypassed adoption error");
  end else begin
   if(test_case!=2) begin
    wait(mem_busy && mem_write && mem_index>=32766);
    repeat(80) @(negedge clk);
    if(mounted || ready || image_valid==(test_case==1 ? 3:1) || failed)
     $fatal(1,"Blank image adopted before final backing write");
   end
   hold_final=0;wait(mounted==3);repeat(30) @(negedge clk);
   if(!ready || busy || failed || freeze || image_valid!=3 ||
      dirty!=(test_case==0 || test_case==4 || test_case==6 ? 3:test_case==1 ? 2:0))
    $fatal(1,"First-time image adoption/dirty mismatch");
   for(integer card=0;card<2;card++) for(integer n=0;n<32768;n++) begin
    if(memory[card][n]!==((test_case==2 || test_case==1 && card==0) ? file_word(card,n):blank_words[n]))
     $fatal(1,"Service initialized/loaded full-image mismatch");
   end
   // Both actual clients publish while firmware serializes their commands.
   if(!dirty[0]) mark_guest(0);
   save_card(0,test_case==4);
   if(test_case==4) begin
    if(mounted!=3 || !ready || !dirty[0]) $fatal(1,"Failed export invalidated resident image");
    save_card(0,0);
   end
   if(!dirty[1]) mark_guest(1);
   save_card(1,0);
   // A committed guest sector modification must survive a later export too.
   mark_guest(0);save_card(0,0);
   if(dirty || mounted!=3 || !ready) $fatal(1,"Service save/retry completion mismatch");
  end
  finish_cd();
  $display("PASS APF SERVICES case=%0d packets=%0d writes=%0d saved_words=%0d chunks=%0d sectors=%0d halfwords=%0d reads=%0d table_words=%0d overlap=%0d",test_case,packets,words_written,words_saved,commands_saved,cd_sectors,cd_halfwords,cd_reads,track_words,overlap);$finish;
 end
endmodule
