`timescale 1ns/1ps
// Three frames of 4000 clk1x cycles with known activity: per frame 100
// main-RAM requests of 3 cycles, 50 SPU requests of 5 cycles, 7 samples and
// VRAM busy 2 clk2x cycles in every 10 (1,600 a frame); then the header and records are read
// back through the bridge port. STAGES=1: 8-word records; stage[1] is held
// high (12,000 clk3x cycles a frame) and stage[0] only while ram_ena..done.
module tb_perf_log;
  parameter integer STAGES=0;
  localparam integer R=STAGES ? 12 : 8;   // record 1
  reg clk1x=0, clk2x=0, rclk=0;
  always #2 clk1x=~clk1x;
  always #1 clk2x=~clk2x;
  always #3 rclk=~rclk;
  reg clk3x=0; always #(2.0/3) clk3x=~clk3x;
  reg ram_busy=0;
  reg vsync=0, ram_ena=0, ram_done=0, spu_ena=0, spu_done=0, sample=0;
  reg vram_rd=0, vram_we=0, vram_busy=0, vram_ready=0;
  reg [12:0] idx=0; wire [31:0] q;
  psx_perf_log #(.FRAMES(8), .STAGES(STAGES)) dut(.clk1x(clk1x),.clk2x(clk2x),.clk3x(clk3x),.stage({1'b0,1'b1,ram_busy}),.vsync(vsync),.ram_ena(ram_ena),.ram_done(ram_done),
    .spu_ena(spu_ena),.spu_done(spu_done),.sample(sample),.vram_rd(vram_rd),.vram_we(vram_we),.vram_busy(vram_busy),
    .vram_ready(vram_ready),.status(32'h8000002a),.read_clk(rclk),.read_index(idx),.read_data(q));
  integer f, c, errs=0, k; reg [31:0] w[0:31];
  always @(posedge clk2x) begin
    k=$time%20; vram_we<=(k<4); vram_busy<=(k<4);
  end
  initial begin
    for (f=0; f<4; f=f+1) begin
      for (c=0; c<4000; c=c+1) begin
        @(negedge clk1x);
        vsync=(c<3);
        ram_ena=(c>=100 && c<1300 && c%12==0); ram_done=(c>=100 && c<1300 && c%12==2);
        if (ram_ena) ram_busy=1; if (ram_done) ram_busy=0;
        spu_ena=(c>=2000 && c<3000 && c%20==0); spu_done=(c>=2000 && c<3000 && c%20==4);
        sample=(c>=3500 && c<3507);
      end
    end
    vsync=0;
    for (c=0;c<32;c=c+1) begin @(negedge rclk); idx=c; @(posedge rclk); @(negedge rclk); w[c]=q; end
    if (w[0]!==32'h50455246 || w[1]!=={STAGES!=0,15'b0,16'd4} || w[2]!==32'h8000002a) begin errs=errs+1; $display("header %h %0d %0d",w[0],w[1],w[2]); end
    // record 1 = frame 1 (record 0 is the partial first frame)
    if (w[R][19:0]<3990 || w[R][19:0]>4000 || w[R][31:22]!==7) begin errs=errs+1; $display("w0 %h",w[R]); end
    // upper 12 bits: main-RAM request count (100 ram_ena pulses a frame)
    if (w[R+1]!=={12'd100,20'd300}) begin errs=errs+1; $display("ram %0d",w[R+1]); end
    if (w[R+2]!=={12'd0,20'd250}) begin errs=errs+1; $display("spu %0d",w[R+2]); end
    if (w[R+3][31:16] < 1590/16 || w[R+3][31:16] > 1610/16) begin errs=errs+1; $display("vram %h",w[R+3]); end
    // ram_busy is high 2 of every 12 clk1x cycles over 100 requests: 200 clk1x = 600 clk3x
    if (STAGES && (w[R+4]<590 || w[R+4]>610 || w[R+5]<11950 || w[R+5]>12100 || w[R+6]!==0 || w[R+7]!==0)) begin
      errs=errs+1; $display("stages %0d %0d %0d %0d",w[R+4],w[R+5],w[R+6],w[R+7]); end
    // record 2 repeats it: per-frame deltas, not running totals
    if (STAGES && (w[R+12]!==w[R+4] || w[R+13]<11950 || w[R+13]>12100)) begin
      errs=errs+1; $display("stages2 %0d %0d",w[R+12],w[R+13]); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
