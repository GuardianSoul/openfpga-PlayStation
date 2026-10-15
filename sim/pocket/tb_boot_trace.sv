module tbt;
  reg clk=0; always #3 clk=~clk; reg rclk=0; always #7 rclk=~rclk;
  reg reset=1, req=0, wr=0, rdone=0; reg [24:0] a=0; reg [5:0] f=0; reg [12:0] ri=0; reg [31:0] wd=0, rdin=0; wire [31:0] rd;
  reg [255:0] hdr; integer i, k, errs=0, nruns=0, nreq=0;
  reg [31:0] exp_first [0:2047]; reg [63:0] exp_data [0:1023]; integer nd=0;
  psx_boot_trace dut(.clk(clk),.reset(reset),.request(req),.write(wr),.address(a),.frame(f),.write_data(wd),.read_data_in(rdin),.cache_clk(clk),.cache_wr(4'b0),.cache_data(32'b0),.read_done(rdone),
    .header(hdr),.read_clk(rclk),.read_index(ri),.read_data(rd));
  task automatic rd_check(input [12:0] idx, input [31:0] exp);
    begin ri=idx; repeat(3) @(posedge rclk); #1; if (rd!==exp) begin errs=errs+1; if (errs<6) $display("idx %0d got %h exp %h",idx,rd,exp); end end
  endtask
  // One run: n requests from base with stride s.
  task automatic run(input w, input [24:0] base, input integer n, input integer s);
    integer j;
    begin
      for (j=0;j<n;j=j+1) begin
        @(posedge clk); req<=1; wr<=w; a<=base+j*s; wd<=w ? base+j*s+1 : 0;
        @(posedge clk); req<=0;
        if (w && base+j*s+1!=0 && base+j*s<25'h200000 && nd<1024) begin exp_data[nd]={1'b1,6'b0,25'(base+j*s),32'(base+j*s+1)}; nd=nd+1; end
        if (!w) begin @(posedge clk); rdone<=1; rdin<=32'hd0000000+j; @(posedge clk); rdone<=0;
          if (base+j*s<25'h200000 && nd<1024) begin exp_data[nd]={1'b0,6'b0,25'(base+j*s),32'hd0000000+j}; nd=nd+1; end end
        nreq=nreq+1;
      end
      if (nruns<2032) exp_first[nruns]={6'(n>63?63:n), w, base};
      nruns=nruns+1;
    end
  endtask
  initial begin
    for (i=0;i<8;i=i+1) hdr[i*32+:32]=32'hc0de0000+i;
    repeat(3) @(posedge clk); reset=0;
    run(0,25'h800000,100,4); run(1,25'h0,10,4); run(0,25'h1000,5,16); run(1,25'h1000,3,8); run(0,25'h40,1,4); run(0,25'h2000,70,16); run(0,25'h810000,3,4);
    repeat(10) @(posedge rclk);
    rd_check(9, nreq); rd_check(10, nruns);
    for (i=0;i<nruns;i=i+1) rd_check(16+i, exp_first[i]);
    for (i=0;i<nd;i=i+1) begin rd_check(4096+2*i, exp_data[i][63:32]); rd_check(4097+2*i, exp_data[i][31:0]); end
    rd_check(6144, {7'b0,25'h810000}); rd_check(6145, 32'hd0000000); rd_check(6146, {7'b0,25'h810004}); rd_check(6147, 32'hd0000001);
    $display("runs=%0d data=%0d errs=%0d",nruns,nd,errs); $finish;
  end
endmodule
