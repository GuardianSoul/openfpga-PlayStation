`timescale 1ns/1ps
// Real pinned APF handler, authored CD/card command descriptors. No SD data.
module mf_datatable(input [9:0] address_a,address_b,input clock_a,clock_b,
 input [31:0] data_a,data_b,input wren_a,wren_b,output [31:0] q_a,q_b);
 assign q_a=0,q_b=0;
endmodule
module tb_apf_target_arb;
 reg clk=0,reset=1;
 always #6.734 clk=~clk;
 reg [1:0] p_valid=0,p_write=2'b10;
 wire [1:0] p_ready,p_busy,p_done,p_issued;
 reg [1:0][15:0] p_id=0;
 reg [1:0][31:0] p_offset=0,p_bridge=0,p_length=0;
 wire [1:0][2:0] p_error;
 wire target_read,target_write,target_done,protocol_error;
 wire [15:0] target_id;
 wire [31:0] target_offset,target_bridge,target_length;
 wire [2:0] target_error;
 reg bridge_wr=0,bridge_rd=0;
 reg [31:0] bridge_addr=0,bridge_data=0;
 wire [31:0] command_data;
 psx_apf_target_arb arb(.*);
 core_bridge_cmd commands(.clk(clk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
  .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_data),
  .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
  .dataslot_requestread_ack(0),.dataslot_requestread_ok(0),.dataslot_requestwrite_ack(0),.dataslot_requestwrite_ok(0),
  .savestate_supported(0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
  .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
  .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
  .target_dataslot_read(target_read),.target_dataslot_write(target_write),
  .target_dataslot_getfile(0),.target_dataslot_openfile(0),
  .target_dataslot_id(target_id),.target_dataslot_slotoffset(target_offset),
  .target_dataslot_bridgeaddr(target_bridge),.target_dataslot_length(target_length),
  .target_dataslot_done(target_done),.target_dataslot_err(target_error),
  .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
  .datatable_addr(0),.datatable_wren(0),.datatable_data(0));
 integer accepted[0:1],host_completed[0:1],issued[0:1];
 integer host_ops=0,ticks=0,test_case=0;
 reg server_stop=0;
 function automatic [31:0] swap(input [31:0] x);return {x[7:0],x[15:8],x[23:16],x[31:24]};endfunction
 task automatic write_bus(input [31:0] a,input [31:0] d);
  @(negedge clk);bridge_addr=a;bridge_data=d;@(negedge clk);bridge_wr=1;@(negedge clk);bridge_wr=0;
 endtask
 task automatic read_bus(input [31:0] a,output [31:0] d);
  @(negedge clk);bridge_addr=a;bridge_rd=1;repeat(4) @(negedge clk);d=swap(command_data);bridge_rd=0;
 endtask
 task automatic serve;
  reg [31:0] status,id,offset,addr,length;
  integer client,index,result;
  read_bus(32'hf8001000,status);
  if(status[31:16]!==16'h636d) return;
  read_bus(32'hf8001020,id);read_bus(32'hf8001024,offset);
  read_bus(32'hf8001028,addr);read_bus(32'hf800102c,length);
  if(id!=0 && id!=16'h10) $fatal(1,"Queued slot descriptor mismatch");
  client=id==16'h10;index=host_completed[client];
  if(status[15:0]!=(client ? 16'h184:16'h180)) $fatal(1,"Queued opcode mismatch");
  if(offset!=(client ? 512*index:2352*index) ||
     addr!=(client ? 32'h23000000:32'h24000000+(index%2)*4096) ||
     length!=(client ? 512:2352)) $fatal(1,"Queued payload mismatch");
  if(index>=accepted[client]) $fatal(1,"Unrequested APF command");
  write_bus(32'hf8001000,swap(32'h62750000));
  repeat(30+host_ops*3) @(negedge clk);
  result=client==0 && index==2 ? 3:0;
  write_bus(32'hf8001000,swap(32'h6f6b0000|result));
  host_completed[client]++;host_ops++;
 endtask
 task automatic descriptor(input integer client,input integer index);
  p_id[client]=client ? 16'h10:0;
  p_write[client]=client!=0;
  p_offset[client]=client ? index*512:index*2352;
  p_bridge[client]=client ? 32'h23000000:32'h24000000+(index%2)*4096;
  p_length[client]=client ? 512:2352;
 endtask
 always @(posedge clk) begin
  ticks++;
  if(!reset) begin
   for(integer i=0;i<2;i++) begin
    if(p_valid[i] && p_ready[i]) accepted[i]++;
    if(p_issued[i]) issued[i]++;
   end
   #1;
   for(integer i=0;i<2;i++) begin
    if(p_done[i] && accepted[i]!=host_completed[i]) $fatal(1,"Client completion before host result");
    if(issued[i]>accepted[i]) $fatal(1,"Repeated held request issued");
   end
  end
  if(ticks>20000) $fatal(1,"Target arbitration timeout");
 end
 initial begin
  if($value$plusargs("CASE=%d",test_case)) begin end
  for(integer i=0;i<2;i++) begin accepted[i]=0;host_completed[i]=0;issued[i]=0;end
  repeat(10) @(negedge clk);reset=0;
  fork
   begin while(!server_stop) serve();end
   begin
    // Concurrent read/write requests. Valid is held several clocks; changing
    // descriptors after acceptance must not alter a peer's queued command.
    for(integer n=0;n<6;n++) begin
     wait(p_ready==3);@(negedge clk);descriptor(0,n);descriptor(1,n);p_valid=3;
     repeat(6) @(negedge clk);p_valid=0;
     p_id=32'hffefffed;p_write=~p_write;p_offset=64'hfe123456abcdef12;
     p_bridge=64'hdeaddeaddeaddead;p_length=64'h9999999999999999;
     wait(p_done==3);repeat(4) @(negedge clk);
     if(p_error[0]!=(n==2 ? 3:0) || p_error[1]!=0) $fatal(1,"Result routed to wrong client");
    end
    // A client restarting while its old host operation runs cannot revoke
    // its lease. A fresh held descriptor waits until ready; old completion
    // clears only the old operation, not the newly accepted one.
    wait(p_ready[0]);@(negedge clk);descriptor(0,6);p_valid=1;
    repeat(6) @(negedge clk);p_valid=0;
    @(negedge clk);descriptor(0,7);p_valid=1;
    if(test_case==1) begin
     // Illegal one-cycle pulse while not ready must be reported as dropped.
     @(negedge clk);p_valid=0;wait(p_done[0]);repeat(6) @(negedge clk);
     if(!protocol_error) $fatal(1,"Dropped busy request not reported");
    end else begin
     do @(posedge clk);while(!p_ready[0]);
     @(negedge clk);p_valid=0;wait(p_done[0]);repeat(6) @(negedge clk);
     if(protocol_error) $fatal(1,"Legal held request rejected");
    end
    if(host_ops!=(test_case==1 ? 13:14) || issued[0]!=host_completed[0] || issued[1]!=6)
      $fatal(1,"APF command counts/order mismatch");
    server_stop=1;
   end
  join
  $display("PASS APF TARGET ARB case=%0d commands=%0d cd=%0d card=%0d",test_case,host_ops,host_completed[0],host_completed[1]);$finish;
 end
endmodule
