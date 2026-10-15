`timescale 1ns/1ps
// Authored two-card images; real pinned APF command handler, packet model.
// Does not model SD firmware, serial bridge PHY or PSRAM pins.
module mf_datatable(input [9:0] address_a,address_b,input clock_a,clock_b,
 input [31:0] data_a,data_b,input wren_a,wren_b,output [31:0] q_a,q_b);
 assign q_a=0,q_b=0;
endmodule
module tb_memcard_transfer;
 reg clk=0,reset=1,memory_ready=1,bridge_wr=0,bridge_rd=0;
 always #6.734 clk=~clk;
 reg [31:0] bridge_addr=0,bridge_data=0;
 wire [31:0] command_data,staging_data;
 wire staging_selected,slot_request,slot_ack,slot_ok,allcomplete;
 wire [15:0] slot_id;wire [31:0] slot_size;
 reg flush_request=0,flush_card=0;reg [1:0] guest_dirty=0;
 wire [1:0] image_valid,dirty;wire busy,freeze,failed,flush_done;
 wire freeze_ack;integer ticks=0,finished=0;
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
 reg mem_card=0,mem_write=0;reg [31:0] mem_data=0;
 wire req_ready=source_ready[1];
 assign m_ready=backend_idle && ticks%11>2 && !force_stall;
 reg [31:0] memory[0:1][0:32767];
 reg [31:0] saved[0:1][0:32767];
 wire target_write,target_done;wire [2:0] target_error;
 wire [15:0] target_id;wire [31:0] target_offset,target_bridge,target_length;
 psx_memcard_transfer dut(.*,.staging_data(staging_data),.bridge_data(bridge_data),
  .initialization_commit(1'b0),.initialized_images(2'b00),.target_ready(1'b1),
  .guest_dirty(guest_dirty),.target_error(target_error),.req_ready(req_ready),
  .rsp_valid(source_return[1]),.rsp_error(source_error[1]),.rsp_data(source_data[1]));
 core_bridge_cmd commands(.clk(clk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
  .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
  .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
  .dataslot_requestwrite(slot_request),.dataslot_requestwrite_id(slot_id),
  .dataslot_requestwrite_size(slot_size),.dataslot_requestwrite_ack(slot_ack),.dataslot_requestwrite_ok(slot_ok),
  .dataslot_allcomplete(allcomplete),.dataslot_requestread_ack(1'b0),.dataslot_requestread_ok(1'b0),
  .savestate_supported(1'b0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
  .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
  .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
  .target_dataslot_read(0),.target_dataslot_write(target_write),.target_dataslot_getfile(0),.target_dataslot_openfile(0),
  .target_dataslot_id(target_id),.target_dataslot_slotoffset(target_offset),
  .target_dataslot_bridgeaddr(target_bridge),.target_dataslot_length(target_length),
  .target_dataslot_done(target_done),.target_dataslot_err(target_error),
  .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
  .datatable_addr(0),.datatable_wren(0),.datatable_data(0));
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
   result=0;
   while(result[31:16]!==16'h636d) read_bus(32'hf8001000,result);
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
  if(fabric_error) $fatal(1,"Card fabric protocol error");
 end
 always @(posedge memory_clk) begin
  if(pin_delay>0) pin_delay<=pin_delay-1;
  if(m_valid && m_ready) begin
   if(m_halfwords!=2 || m_be!=15 || m_addr[0]!=0) $fatal(1,"Card packet mask/alignment");
   if(m_addr>=25'h80000 && m_addr<25'h90000) begin mem_card<=0;mem_index<=(m_addr-25'h80000)/2;end
   else if(m_addr>=25'h100000 && m_addr<25'h110000) begin mem_card<=1;mem_index<=(m_addr-25'h100000)/2;end
   else if(m_addr==25'h1234) mem_index<=-1;
   else $fatal(1,"Card packet escaped logical window");
   mem_data<=m_data[31:0];mem_write<=m_write;mem_busy<=1;latency<=packets%23+4;packets++;
  end
  if(mem_busy && !rsp_valid && !(hold_final && mem_write && mem_index==32767)) begin
   if(latency>0) latency<=latency-1;
   else begin
    rsp_error<=force_error;rsp_valid<=1;
    if(mem_write && !force_error) begin
     if(mem_index==-1) guest_word<=mem_data;
     else begin memory[mem_card][mem_index]<=mem_data;words_written++;end
    end
    else rsp_data<={96'b0,(mem_index==-1 ? guest_word:memory[mem_card][mem_index])};
   end
  end
  if(rsp_valid && m_rsp_ready) begin rsp_valid<=0;mem_busy<=0;pin_delay<=13;end
  if(ticks>22000000) $fatal(1,"Memcard transfer timeout");
 end
 integer test_case=0;
 initial begin
  if($value$plusargs("CASE=%d",test_case)) begin end
  for(integer card=0;card<2;card++) for(integer n=0;n<32768;n++) begin memory[card][n]=32'hdeaddead;saved[card][n]=0;end
  repeat(10) @(negedge clk);reset=0;
  if(test_case==1) begin
   open_slot(0,131071,2);if(image_valid || !failed) $fatal(1,"Wrong size accepted");
  end else if(test_case==2) begin
   open_slot(0,131072,0);write_bus(32'h21000000,file_word(0,0));host_command(16'h8f,0);
   repeat(100) @(negedge clk);if(image_valid || !failed || !freeze) $fatal(1,"Short load accepted");
  end else if(test_case==3) begin
   open_slot(0,131072,0);write_bus(32'h21000004,file_word(0,1));
   repeat(20) @(negedge clk);if(image_valid || !failed || !freeze) $fatal(1,"Unordered load accepted");
  end else if(test_case==4) begin
   open_slot(0,131072,0);force_error=1;write_bus(32'h21000000,file_word(0,0));
   repeat(100) @(negedge clk);if(image_valid || !failed || !freeze) $fatal(1,"Backing error accepted");
  end else if(test_case==5) begin
   open_slot(0,131072,0);force_stall=1;
   for(integer n=0;n<300;n++) begin
    write_bus(32'h21000000+n*4,file_word(0,n));repeat(85) @(negedge clk);
   end
   if(image_valid || !failed || !freeze) $fatal(1,"FIFO overflow accepted");
  end else if(test_case==6) begin
   open_slot(0,131072,0);memory_ready=0;repeat(20) @(negedge clk);
   if(image_valid || !failed || !freeze) $fatal(1,"Lost initialization accepted");
  end else begin
   // Leave an upstream response held while the first host pause starts.
   // The fabric must wait for both CDC return consumption and pin drain.
   fork
    begin
     @(negedge guest_clk);guest_valid=1;
     do @(posedge guest_clk);while(!source_ready[0]);
     @(negedge guest_clk);guest_valid=0;
     repeat(170) @(negedge guest_clk);
     if(freeze_ack || image_valid) $fatal(1,"Held guest return bypassed drain");
     guest_return_ready=1;
     wait(source_return[0]);@(negedge guest_clk);
    end
    begin load_card(0);end
   join
   if(guest_word!==32'hcafebade) $fatal(1,"Pre-pause guest write lost");
   load_card(1);
   mark_guest(0);mark_guest(1);
   save_card(0,0);if(!dirty[1]) $fatal(1,"Other card dirty lost");save_card(1,0);
   mark_guest(0);save_card(0,1);
  end
  $display("PASS MEMCARD TRANSFER case=%0d packets=%0d writes=%0d saved_words=%0d chunks=%0d",test_case,packets,words_written,words_saved,commands_saved);$finish;
 end
endmodule
