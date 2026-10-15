`timescale 1ns/1ps
// Authored resident images; pinned APF command handler, async packet fabric,
// independent memory response/drain. APF_SPI additionally runs real bridge pins.
module tb_memcard_os_read;
 reg clk=0,guest_clk=0,memory_clk=0,reset=1;
 always #6.734 clk=~clk;
 always #7.381 guest_clk=~guest_clk;
 always #8.081 memory_clk=~memory_clk;
 reg memory_ready=1,services_idle=1;
 reg [1:0] image_valid=3;
 wire slot_request,slot_ack,slot_ok,allcomplete;wire [15:0] slot_id;
 wire freeze,freeze_ack,host_suspend,busy,failed,done;
 wire req_valid,req_write,req_ready,rsp_ready,bridge_selected;
 wire [24:0] req_addr;wire [3:0] req_halfwords;
 wire [127:0] req_data;wire [15:0] req_be;
 wire [31:0] reader_data,command_data;
 integer ticks=0,done_count=0,packets=0,read_words=0,min_spacing=99999,last_read_tick=-99999;
 integer test_case=0;
`ifdef APF_SPI
 wire bridge_rd,bridge_wr;wire [31:0] bridge_addr,bridge_wr_data;
 reg phy_select=1,host_driving=0,host_clock=0,host_mosi=0,host_miso=0;
 tri phy_clock,phy_mosi,phy_miso;
 assign phy_clock=host_driving ? host_clock:1'bz;
 assign phy_mosi=host_driving ? host_mosi:1'bz;
 assign phy_miso=host_driving ? host_miso:1'bz;
 io_bridge_peripheral io(.clk(clk),.reset_n(!reset),.endian_little(1'b1),
  .pmp_addr(bridge_addr),.pmp_addr_valid(),.pmp_rd(bridge_rd),.pmp_rd_data(bridge_read_data),
  .pmp_wr(bridge_wr),.pmp_wr_data(bridge_wr_data),
  .phy_spimosi(phy_mosi),.phy_spimiso(phy_miso),.phy_spiclk(phy_clock),.phy_spiss(phy_select));
`else
 reg bridge_rd=0,bridge_wr=0;reg [31:0] bridge_addr=0,bridge_wr_data=0;
`endif
 wire [31:0] bridge_read_data=bridge_addr[31:24]==8'hf8 ? command_data:reader_data;
 wire guest_pause;reg guest_valid=0,guest_return_ready=0;
 wire [1:0] source_ready,source_return,source_error;wire [1:0][127:0] source_data;
 wire m_valid,m_write,m_ready,m_rsp_ready;
 wire [24:0] m_addr;wire [3:0] m_halfwords;
 wire [127:0] m_data;wire [15:0] m_be;
 wire fabric_error;
 reg m_busy=0,m_rsp_valid=0,m_rsp_error=0;reg [127:0] m_rsp_data=0;
 integer latency=0,pin_delay=0,mem_index=0,mem_card=0;
 wire backend_idle=!m_busy && !m_rsp_valid && pin_delay==0;
 reg idle_meta=0,idle_sync=0;
 always @(posedge clk) begin idle_meta<=backend_idle;idle_sync<=idle_meta;end
 wire memory_idle=idle_sync && source_ready[1] && !source_return[1] && !req_valid;
 assign req_ready=source_ready[1];
 assign m_ready=backend_idle && ticks%7!=0;
 psx_memcard_os_read dut(.clk(clk),.reset(reset),.memory_ready(memory_ready),.memory_idle(memory_idle),
  .services_idle(services_idle),.slot_request(slot_request),.slot_id(slot_id),.image_valid(image_valid),
  .allcomplete(allcomplete),.slot_ack(slot_ack),.slot_ok(slot_ok),.host_suspend(host_suspend),
  .freeze(freeze),.freeze_ack(freeze_ack),.busy(busy),.failed(failed),.done(done),
  .bridge_rd(bridge_rd),.bridge_addr(bridge_addr),.bridge_selected(bridge_selected),.bridge_data(reader_data),
  .req_valid(req_valid),.req_write(req_write),.req_ready(req_ready),.req_addr(req_addr),
  .req_halfwords(req_halfwords),.req_data(req_data),.req_be(req_be),
  .rsp_valid(source_return[1]),.rsp_error(source_error[1]),.rsp_data(source_data[1]),.rsp_ready(rsp_ready));
 psx_memcard_fabric fabric(.guest_clk(guest_clk),.host_clk(clk),.memory_clk(memory_clk),.reset(reset),
  .host_freeze(freeze),.guest_stopped(guest_pause),.backend_idle(backend_idle),
  .guest_pause(guest_pause),.host_owned(freeze_ack),
  .s_valid({req_valid,guest_valid}),.s_write({req_write,1'b1}),.s_ready(source_ready),
  .s_addr({req_addr,25'h1234}),.s_halfwords({req_halfwords,4'd2}),.s_wdata({req_data,128'hcafebade}),
  .s_byte_en({req_be,16'hf}),.s_rsp_valid(source_return),.s_rsp_ready({rsp_ready,guest_return_ready}),
  .s_rsp_rdata(source_data),.s_rsp_error(source_error),
  .req_valid(m_valid),.req_write(m_write),.req_ready(m_ready),.req_addr(m_addr),.req_halfwords(m_halfwords),
  .req_wdata(m_data),.req_byte_en(m_be),.rsp_valid(m_rsp_valid),.rsp_ready(m_rsp_ready),
  .rsp_rdata(m_rsp_data),.rsp_error(m_rsp_error),.protocol_error(fabric_error));
 core_bridge_cmd commands(.clk(clk),.bridge_endian_little(1'b1),.bridge_addr(bridge_addr),
  .bridge_rd(bridge_rd),.bridge_rd_data(command_data),.bridge_wr(bridge_wr),.bridge_wr_data(bridge_wr_data),
  .status_boot_done(1'b1),.status_setup_done(1'b0),.status_running(1'b0),
  .dataslot_requestread(slot_request),.dataslot_requestread_id(slot_id),
  .dataslot_requestread_ack(slot_ack),.dataslot_requestread_ok(slot_ok),.dataslot_allcomplete(allcomplete),
  .dataslot_requestwrite_ack(0),.dataslot_requestwrite_ok(0),
  .savestate_supported(0),.savestate_addr(0),.savestate_size(0),.savestate_maxloadsize(0),
  .savestate_start_ack(0),.savestate_start_busy(0),.savestate_start_ok(0),.savestate_start_err(0),
  .savestate_load_ack(0),.savestate_load_busy(0),.savestate_load_ok(0),.savestate_load_err(0),
  .target_dataslot_read(0),.target_dataslot_write(0),.target_dataslot_getfile(0),.target_dataslot_openfile(0),
  .target_dataslot_id(0),.target_dataslot_slotoffset(0),.target_dataslot_bridgeaddr(0),.target_dataslot_length(0),
  .target_buffer_param_struct(0),.target_buffer_resp_struct(0),
  .datatable_addr(0),.datatable_wren(0),.datatable_data(0));
 function automatic [31:0] swap(input [31:0] value);
  return {value[7:0],value[15:8],value[23:16],value[31:24]};
 endfunction
 function automatic [31:0] file_word(input integer card,input integer index);
  return 32'hb713e04d ^ (index*32'h001079ab) ^ (card*32'hacf43819) ^
   (index>=320 && index<352 ? 32'hdead1234:0);
 endfunction
`ifdef APF_SPI
 task automatic spi_address(input [31:0] addr);
  phy_select=0;host_driving=1;host_clock=0;
  repeat(6) @(negedge clk);
  for(integer p=15;p>=0;p--) begin
   @(negedge clk);host_clock=0;host_mosi=addr[p*2+1];host_miso=addr[p*2];
   @(negedge clk);host_clock=1;
  end
 endtask
 task automatic spi_release(input integer started);
  @(negedge clk);phy_select=1;host_driving=0;
  repeat(6) @(negedge clk);
  while(ticks-started<88) @(negedge clk);
 endtask
 task automatic write_bus(input [31:0] addr,input [31:0] value);
  integer started;started=ticks;
  spi_address(addr|1);
  for(integer p=15;p>=0;p--) begin
   @(negedge clk);host_clock=0;host_mosi=value[p*2+1];host_miso=value[p*2];
   @(negedge clk);host_clock=1;
  end
  repeat(12) @(negedge clk);spi_release(started);
 endtask
 task automatic read_bus(input [31:0] addr,output [31:0] value);
  reg [31:0] serial;integer started;started=ticks;
  spi_address(addr);
  @(negedge clk);host_clock=0;host_driving=0;
  wait(io.spis==2);serial=0;
  for(integer p=0;p<16;p++) begin
   @(posedge phy_clock);#1;serial={serial[29:0],phy_mosi,phy_miso};
  end
  value=addr[31:24]==8'hf8 ? serial:swap(serial);
  spi_release(started);
 endtask
`else
 task automatic write_bus(input [31:0] addr,input [31:0] value);
  @(negedge clk);bridge_addr=addr;bridge_wr_data=swap(value);
  @(negedge clk);bridge_wr=1;@(negedge clk);bridge_wr=0;
 endtask
 task automatic read_bus(input [31:0] addr,output [31:0] value);
  @(negedge clk);bridge_addr=addr;
  // Match real io_bridge: capture PREVIOUS response, then kick new read.
  repeat(4) @(negedge clk);value=addr[31:24]==8'hf8 ? swap(command_data):reader_data;
  bridge_rd=1;@(negedge clk);bridge_rd=0;
  repeat(test_case==4 ? 0:83) @(negedge clk);
 endtask
`endif
 task automatic host_command(input [15:0] command,input [15:0] expected);
  reg [31:0] result;
  write_bus(32'hf8000000,{16'h434d,command});result=0;
  // Prime this command's status read. Its first physical response can still
  // be the preceding command's OK, exactly like a first data read is stale.
  read_bus(32'hf8000000,result);result=0;
  for(integer n=0;n<2000 && result[31:16]!==16'h4f4b;n++) read_bus(32'hf8000000,result);
  if(result!={16'h4f4b,expected}) $fatal(1,"APF OS read command result %h expected=%h slot=%h state=%0d filled=%0d consumed=%0d packets=%0d freeze=%b/%b memstate=%0d idle=%b pending=%b service=%b",result,expected,slot_id,dut.state,dut.filled,dut.consumed,packets,freeze,freeze_ack,dut.memory_state,memory_idle,dut.pending,services_idle);
 endtask
 task automatic open_card(input integer card,input integer expected);
  write_bus(32'hf8000020,16'h10+card);host_command(16'h80,16'(expected));
 endtask
 task automatic read_card(input integer card,input integer count);
  reg [31:0] value;
  begin: card_reader
  // Prime word0; the final dummy read returns word32767 through the same map.
  read_bus(32'h21000000+card*32'h01000000,value);
  for(integer n=0;n<count;n++) begin
   read_bus(32'h21000000+card*32'h01000000+(n+1)*4,value);
   if(!failed && value!==file_word(card,n)) $fatal(1,"Automatic card bytes/order mismatch card=%0d word=%0d got=%h",card,n,value);
   read_words++;
   if(failed) disable card_reader;
   if(!freeze || !freeze_ack) $fatal(1,"Automatic card ownership released during read");
  end
  end
 endtask
 always @(posedge clk) begin
  ticks<=ticks+1;if(done) done_count++;
  if(req_valid && !freeze_ack || slot_ack && slot_ok && (!freeze_ack || dut.filled<256 || !memory_idle))
   $fatal(1,"OS read acknowledged before prefetch/physical drain");
  if(fabric_error) $fatal(1,"OS read fabric error");
  if(bridge_rd && bridge_selected) begin
   if(ticks-last_read_tick<min_spacing) min_spacing=ticks-last_read_tick;
   last_read_tick=ticks;
  end
  if(ticks>16000000) $fatal(1,"Automatic card read timeout");
 end
 always @(posedge memory_clk) begin
  if(pin_delay>0) pin_delay<=pin_delay-1;
  if(m_valid && m_ready) begin
   if(m_addr==25'h1234) begin mem_card<=-1;mem_index<=0;end
   else begin
    if(m_write || m_halfwords!=8 || m_be!=16'hffff || m_addr[2:0]!=0) $fatal(1,"Automatic card packet shape");
    if(m_addr>=25'h80000 && m_addr<25'h90000) begin mem_card<=0;mem_index<=(m_addr-25'h80000)/2;end
    else if(m_addr>=25'h100000 && m_addr<25'h110000) begin mem_card<=1;mem_index<=(m_addr-25'h100000)/2;end
    else $fatal(1,"Automatic card packet escaped image");
   end
   m_busy<=1;latency<=test_case==4 && packets>64 ? 2000:packets%29+3;packets++;
  end
  if(m_busy && !m_rsp_valid) begin
   if(latency>0) latency<=latency-1;
   else begin
    m_rsp_valid<=1;m_rsp_error<=test_case==2 && packets==8;
    for(integer n=0;n<4;n++) m_rsp_data[n*32+:32]<=mem_card<0 ? 0:file_word(mem_card,mem_index+n);
   end
  end
  if(m_rsp_valid && m_rsp_ready) begin m_rsp_valid<=0;m_busy<=0;pin_delay<=13;end
 end
 initial begin
  if($value$plusargs("CASE=%d",test_case)) begin end
  // The real shell powers up at zero; Icarus otherwise leaves these X.
  commands.hstate=0;commands.tstate=0;commands.host_cmd_start=0;
`ifdef APF_SPI
  // Reset the asynchronous serial receiver through a real select edge.
  // Initial X -> 1 is not guaranteed to trigger it before the first transfer.
  phy_select=0;repeat(2) @(negedge clk);phy_select=1;
`endif
  repeat(12) @(negedge clk);reset=0;
  guest_return_ready=1;
  if(test_case==1) begin
   services_idle=0;
   fork
    begin repeat(150) @(negedge clk);if(freeze || !host_suspend || packets) $fatal(1,"Old service lease reused");services_idle=1;end
    begin open_card(0,0);end
   join
  end else if(test_case==2) begin
   open_card(0,2);if(!failed || !freeze || !busy) $fatal(1,"Backing error acknowledged as valid file");
  end else if(test_case==5) begin
   image_valid=0;open_card(0,2);if(!failed || freeze || packets) $fatal(1,"Invalid image exported");
  end else open_card(0,0);
  if(test_case!=2 && test_case!=5) begin
   if(test_case==3) begin
    read_card(0,100);host_command(16'h8f,0);repeat(30) @(negedge clk);
    if(!failed || !freeze || done_count) $fatal(1,"Short automatic read accepted");
   end else if(test_case==4) begin
    read_card(0,2000);if(!failed || !freeze || done_count) $fatal(1,"Unsupported read deadline not detected");
   end else begin
    read_card(0,32768);
    // One 008F after BOTH nonvolatile files, as an OS batch can do.
    open_card(1,0);read_card(1,32768);host_command(16'h8f,0);
    wait(done_count==1);repeat(30) @(negedge clk);
    if(failed || busy || freeze || read_words!=65536 || packets!=16384 || min_spacing<88)
     $fatal(1,"Whole automatic read/end/drain mismatch");
   end
  end
  $display("PASS MEMCARD OS READ case=%0d words=%0d packets=%0d min_spacing=%0d",test_case,read_words,packets,min_spacing);$finish;
 end
endmodule
