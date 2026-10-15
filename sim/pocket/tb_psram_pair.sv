// Random packets (main-RAM word reads/writes, SPU single halfwords and
// aligned 8-halfword reads, BIOS-region traffic, bad addresses) through
// psx_psram_pair on two behavioral chips; every response and fill word is
// checked against a halfword reference memory.
module tb_psram_pair #(parameter integer PAIRED=1);
  reg clk=0; always #5 clk=~clk;
  reg reset=1, req_valid=0, req_write=0, rsp_ready=1;
  reg [24:0] req_addr=0; reg [3:0] req_halfwords=0; reg [127:0] req_wdata=0; reg [15:0] req_byte_en=0;
  wire req_ready, rsp_valid, rsp_error, fill_valid, initialized, idle; wire [127:0] rsp_rdata; wire [1:0] fill_word; wire [31:0] fill_data;
  wire [15:0] dq0, dq1;
  psx_psram_pair #(.T_INIT(50), .PAIRED(PAIRED)) dut(.clk(clk),.reset(reset),.initialized(initialized),.req_valid(req_valid),.req_write(req_write),
    .req_ready(req_ready),.req_addr(req_addr),.req_halfwords(req_halfwords),.req_wdata(req_wdata),.req_byte_en(req_byte_en),
    .rsp_valid(rsp_valid),.rsp_ready(rsp_ready),.rsp_rdata(rsp_rdata),.rsp_error(rsp_error),
    .fill_valid(fill_valid),.fill_word(fill_word),.fill_data(fill_data),.idle(idle),
    .cram0_a(),.cram1_a(),.cram0_dq(dq0),.cram1_dq(dq1),.cram0_wait(1'b0),.cram1_wait(1'b0),
    .cram0_adv_n(),.cram1_adv_n(),.cram0_cre(),.cram1_cre(),.cram0_ce0_n(),.cram0_ce1_n(),.cram1_ce0_n(),.cram1_ce1_n(),
    .cram0_oe_n(),.cram1_oe_n(),.cram0_we_n(),.cram1_we_n(),.cram0_ub_n(),.cram0_lb_n(),.cram1_ub_n(),.cram1_lb_n());
  reg [15:0] ref_mem [0:8191]; reg ref_ok [0:8191];
  integer q; initial for (q=0;q<8192;q=q+1) ref_ok[q]=0;
  function automatic [12:0] rs(input [24:0] h); rs={h[23],h[18],h[10:0]}; endfunction
  function automatic [15:0] expect_hw(input [24:0] h);
    // chip c holds halfwords with h[0]==c at {h[23], h[22:1]}; seed = chip address ^ 5a5a
    reg [22:0] ca; ca=PAIRED ? {h[23],h[22:1]} : {h[23],h[21:0]};
    expect_hw=ref_ok[rs(h)] ? ref_mem[rs(h)] : (ca[15:0]^16'h5a5a);
  endfunction
  integer n, k, errs=0, reads=0, writes=0, fills=0, bads=0, exp_fill=0;
  reg [31:0] fill_seen [0:7];
  always @(posedge clk) if (fill_valid) begin fill_seen[fill_word]=fill_data; fills=fills+1; end
  task automatic packet(input w, input [24:0] a, input [3:0] h, input [15:0] be);
    reg [127:0] d; integer i;
    d={$urandom,$urandom,$urandom,$urandom};
    @(negedge clk); req_valid=1; req_write=w; req_addr=a; req_halfwords=h; req_wdata=d; req_byte_en=be;
    @(posedge clk); while (!req_ready) @(posedge clk);
    @(negedge clk); req_valid=0;
    while (!rsp_valid) @(posedge clk);
    if (a[24] || a[22]) begin if (!rsp_error) begin errs=errs+1; $display("bad address not flagged %h",a); end bads=bads+1; end
    else if (rsp_error) begin errs=errs+1; $display("unexpected error %h",a); end
    else if (w) begin
      for (i=0;i<h;i=i+1) begin
        reg [15:0] v; v=expect_hw(a+i);
        if (be[2*i]) v[7:0]=d[16*i+:8];
        if (be[2*i+1]) v[15:8]=d[16*i+8+:8];
        ref_mem[rs(a+i)]=v; ref_ok[rs(a+i)]=1;
      end
      writes=writes+1;
    end else begin
      for (i=0;i<h;i=i+1) if (rsp_rdata[16*i+:16]!==expect_hw(a+i)) begin errs=errs+1;
        if (errs<10) $display("read %h+%0d got %h want %h",a,i,rsp_rdata[16*i+:16],expect_hw(a+i)); end
      if (!a[0] && h==8) for (i=0;i<4;i=i+1)
        if (fill_seen[i]!=={expect_hw(a+2*i+1),expect_hw(a+2*i)}) begin errs=errs+1; $display("fill %0d %h",i,fill_seen[i]); end
      reads=reads+1;
    end
    @(negedge clk);
  endtask
  initial begin
    repeat(4) @(posedge clk); reset=0;
    while (!initialized) @(posedge clk);
    for (n=0;n<3000;n=n+1) begin
      reg [24:0] base; integer kind; kind=$urandom%6;
      base = kind<2 ? 25'(($urandom%64)<<1) : kind<4 ? 25'h800000+($urandom%64) : 25'h840000+(($urandom%32)<<1);
      case ($urandom%5)
        0: packet(1, base & ~25'h1, 2*(1+$urandom%4), $urandom);          // word writes
        1: packet(1, base, 1, 16'h0003);                                  // SPU halfword write
        2: packet(0, base & ~25'h7, 8, 16'hffff);                          // aligned line read
        3: packet(0, base, 1+$urandom%8, 16'hffff);                        // any read
        default: if ($urandom%20==0) packet($urandom%2, base|25'h400000, 2, 16'hffff); else packet(0, base & ~25'h1, 2, 16'hffff);
      endcase
    end
    $display("PAIRED=%0d reads=%0d writes=%0d fills=%0d bad=%0d errs=%0d", PAIRED, reads, writes, fills, bads, errs);
    $finish;
  end
endmodule
