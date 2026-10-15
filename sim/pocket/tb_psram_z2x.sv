`timescale 1ns/1ps
module tb; integer i;
  reg clk=0; always #8.08 clk=~clk;   // 61.875 MHz
  reg clk2=0; initial begin #0; forever #4.04 clk2=~clk2; end
  reg reset=1;
  reg req_valid=0, req_write=0; wire req_ready; reg [24:0] req_addr=0; reg [127:0] req_data=0; reg [15:0] req_be=0;
  wire rsp_valid; wire [127:0] rsp_data; wire rsp_error;
  wire z_valid,z_we,z_last,z_ready,z_rvalid,z_idle; wire [21:0] z_addr; wire [31:0] z_data,z_read; wire [3:0] z_mask;
  wire x_valid,x_we,x_ack,ready; wire [21:0] x_addr; wire [31:0] x_wdata,x_rdata; wire [3:0] x_be;
  psx_psram_endpoint ep(.clk(clk),.reset(reset),.req_valid(req_valid),.req_write(req_write),.req_ready(req_ready),.req_addr(req_addr),.req_data(req_data),.req_be(req_be),
    .rsp_valid(rsp_valid),.rsp_ready(1'b1),.rsp_data(rsp_data),.rsp_error(rsp_error),.z_valid(z_valid),.z_we(z_we),.z_last(z_last),.z_ready(z_ready),.z_rvalid(z_rvalid),.z_idle(z_idle),
    .z_addr(z_addr),.z_data(z_data),.z_read(z_read),.z_mask(z_mask));
  psx_psram_z2x shim(.clk(clk),.reset(reset),.ready(ready),.z_valid(z_valid),.z_we(z_we),.z_last(z_last),.z_ready(z_ready),.z_addr(z_addr),.z_data(z_data),.z_mask(z_mask),
    .z_rvalid(z_rvalid),.z_read(z_read),.z_idle(z_idle),.x_valid(x_valid),.x_we(x_we),.x_addr(x_addr),.x_wdata(x_wdata),.x_be(x_be),.x_ack(x_ack),.x_rdata(x_rdata));
  // Behavioural X port (MIRLO64 psram's contract): samples x_valid when idle,
  // x_ack one cycle, read data with it. The real X port passed on hardware.
  reg [31:0] xmem [0:4095]; reg ack_r=0; reg [31:0] rd_r=0; reg ready_r=0; integer lat=0, gap=0;
  assign x_ack=ack_r; assign x_rdata=rd_r; assign ready=ready_r;
  initial for (i=0;i<4096;i=i+1) xmem[i]=32'hdead0000+i;
  always @(posedge clk) begin
    ack_r<=0;
    if (!reset) ready_r<=1;
    if (gap>0) gap<=gap-1;
    else if (lat>0) begin lat<=lat-1; if (lat==1) begin ack_r<=1; gap<=3;
        if (x_we) begin if (x_be[0]) xmem[x_addr][7:0]<=x_wdata[7:0]; if (x_be[1]) xmem[x_addr][15:8]<=x_wdata[15:8];
          if (x_be[2]) xmem[x_addr][23:16]<=x_wdata[23:16]; if (x_be[3]) xmem[x_addr][31:24]<=x_wdata[31:24]; end
        else rd_r<=xmem[x_addr]; end end
    else if (x_valid && ready_r) lat<=5+($random&3);
  end
  reg [63:0] ref_mem [0:63]; reg [63:0] w; integer errs=0, n=0;
  task automatic op(input wr, input integer slot, input [63:0] d, input [7:0] be);
    begin
      @(posedge clk); req_valid<=1; req_write<=wr; req_addr<={slot[20:0],2'b0}; req_data<={64'b0,d}; req_be<={8'b0,be};
      @(posedge clk); while(!req_ready || req_valid!==1) @(posedge clk);
      req_valid<=0; while(!rsp_valid) @(posedge clk);
      if (!wr) begin n=n+1; if (rsp_data[63:0]!==ref_mem[slot]) begin errs=errs+1; $display("slot %0d got %h exp %h",slot,rsp_data[63:0],ref_mem[slot]); end end
    end
  endtask
  integer j; reg [7:0] be;
  initial begin
    repeat(4) @(posedge clk); reset=0; wait(ready);
    for (i=0;i<64;i=i+1) begin ref_mem[i][63:32]=32'hdead0000+2*i+1; ref_mem[i][31:0]=32'hdead0000+2*i; end
    for (i=0;i<32;i=i+1) op(0,i,0,0);
    for (i=0;i<64;i=i+1) begin w={$random,$random}; op(1,i,w,8'hff); ref_mem[i]=w; end
    for (i=0;i<64;i=i+1) begin w={$random,$random}; be=$random;
      for (j=0;j<8;j=j+1) if (be[j]) ref_mem[i][j*8+:8]=w[j*8+:8];
      op(1,i,w,be); end
    for (i=0;i<64;i=i+1) op(0,i,0,0);
    $display("reads=%0d errs=%0d", n, errs); $finish;
  end
  initial begin #20000000; $display("TIMEOUT"); $finish; end
endmodule
