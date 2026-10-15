// Three clients issue random word writes and read-backs to their own
// regions concurrently through psx_psram_clients; each checks its own
// responses, and client 1 also checks its fill stream on 8-halfword reads.
module tb_psram_clients;
  reg clk=0; always #5 clk=~clk;
  reg reset=1;
  reg [2:0] p_valid=0, p_write=0, r_ready=3'b111;
  reg [2:0][24:0] p_addr; reg [2:0][3:0] p_halfwords; reg [2:0][127:0] p_wdata; reg [2:0][15:0] p_byte_en;
  wire [2:0] p_ready, r_valid, r_error; wire [127:0] r_rdata; wire fill_valid, initialized, idle; wire [1:0] fill_word; wire [31:0] fill_data;
  wire [15:0] dq0, dq1;
  psx_psram_clients #(.T_INIT(50)) dut(.clk(clk),.reset(reset),.initialized(initialized),.idle(idle),
    .p_valid(p_valid),.p_write(p_write),.p_ready(p_ready),.p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),.p_byte_en(p_byte_en),
    .r_valid(r_valid),.r_error(r_error),.r_ready(r_ready),.r_rdata(r_rdata),.fill_valid(fill_valid),.fill_word(fill_word),.fill_data(fill_data),
    .cram0_a(),.cram1_a(),.cram0_dq(dq0),.cram1_dq(dq1),.cram0_wait(1'b0),.cram1_wait(1'b0),
    .cram0_adv_n(),.cram1_adv_n(),.cram0_cre(),.cram1_cre(),.cram0_ce0_n(),.cram0_ce1_n(),.cram1_ce0_n(),.cram1_ce1_n(),
    .cram0_oe_n(),.cram1_oe_n(),.cram0_we_n(),.cram1_we_n(),.cram0_ub_n(),.cram0_lb_n(),.cram1_ub_n(),.cram1_lb_n());
  integer errs=0, done_n[0:2], fills=0, bad_fill=0;
  reg [31:0] fillbuf[0:3];
  always @(posedge clk) begin
    if (fill_valid) begin fillbuf[fill_word]=fill_data; fills=fills+1; end
    if (fill_valid && !(dut.owner==1)) bad_fill=bad_fill+1;
  end
  genvar c;
  generate for (c=0;c<3;c=c+1) begin: client
    reg [31:0] shadow[0:15];
    integer i, n;
    initial begin
      done_n[c]=0;
      for (i=0;i<16;i=i+1) shadow[i]=32'hx;
      @(negedge reset); while (!initialized) @(posedge clk);
      for (n=0;n<400;n=n+1) begin
        reg [3:0] w; reg [31:0] d; reg rd; reg [24:0] base;
        base = c==0 ? 25'h000100 : c==1 ? 25'h800200 : 25'h840000;
        w=$urandom%16; d=$urandom; rd=$urandom%2 && shadow[w]!==32'hx;
        if (c==1 && $urandom%4==0) begin
          // aligned 8-halfword read of words 0..3
          @(negedge clk); p_valid[c]=1; p_write[c]=0; p_addr[c]=base; p_halfwords[c]=8; p_byte_en[c]=16'hffff;
          @(posedge clk); while (!p_ready[c]) @(posedge clk); @(negedge clk); p_valid[c]=0;
          while (!r_valid[c]) @(posedge clk);
          for (i=0;i<4;i=i+1) if (shadow[i]!==32'hx && (r_rdata[32*i+:32]!==shadow[i] || fillbuf[i]!==shadow[i])) begin
            errs=errs+1; $display("client1 line word %0d %h/%h want %h",i,r_rdata[32*i+:32],fillbuf[i],shadow[i]); end
        end else begin
          @(negedge clk); p_valid[c]=1; p_write[c]=!rd; p_addr[c]=base+2*w; p_halfwords[c]=2; p_byte_en[c]=16'h000f; p_wdata[c]={96'b0,d};
          @(posedge clk); while (!p_ready[c]) @(posedge clk); @(negedge clk); p_valid[c]=0;
          while (!r_valid[c]) @(posedge clk);
          if (rd) begin if (r_rdata[31:0]!==shadow[w]) begin errs=errs+1; $display("client %0d word %0d got %h want %h",c,w,r_rdata[31:0],shadow[w]); end end
          else shadow[w]=d;
        end
        @(negedge clk);
      end
      done_n[c]=1;
    end
  end endgenerate
  // Codex's review cases: no ready during reset; not idle while a packet is granted.
  integer gate_errs=0;
  always @(posedge clk) begin
    if (reset && p_ready!=0) gate_errs=gate_errs+1;
    if (idle && (dut.busy || dut.held)) gate_errs=gate_errs+1;
  end
  initial begin
    p_valid[0]=1; p_write=0; p_addr[0]=0; p_halfwords[0]=2; p_byte_en[0]=16'hf; p_wdata[0]=0;
    repeat(3) @(posedge clk); @(negedge clk); p_valid[0]=0; reset=0;
    wait(done_n[0]==1 && done_n[1]==1 && done_n[2]==1);
    if (bad_fill) errs=errs+1;
    if (gate_errs) begin errs=errs+1; $display("reset/idle gate violations %0d", gate_errs); end
    $display("fills=%0d bad_fill=%0d errs=%0d", fills, bad_fill, errs); $finish;
  end
endmodule
