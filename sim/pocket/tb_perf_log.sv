`timescale 1ns/1ps
// Three frames of 4000 clk1x cycles with known activity: per frame 100
// main-RAM requests of 3 cycles, 50 SPU requests of 5 cycles, 7 samples and
// VRAM busy 2 clk2x cycles in every 10 (1,600 a frame); then the header and records are read
// back through the bridge port. STAGES=1: 8-word records; stage[1] is held
// high (12,000 clk3x cycles a frame) and stage[0] only while ram_ena..done.
module tb_perf_log;
  parameter integer STAGES=0;
  // VIDEO=1: the four geometry words must reach the ring (the probe2 hardware log
  // found duplicate case items that made them stay zero), and only changed frames
  // are stored: frames show pictures A, A, B, C, B, C, so the records are frames
  // 0 (first), 1 (A), 3 (B) and 4 (C, still alternating); 2 and 5 repeat.
  parameter integer VIDEO=0;
  localparam integer R=STAGES ? 12 : (VIDEO ? 13 : 8);   // record 1
  localparam integer NF=VIDEO ? 8 : 4;   // VIDEO: frames 6, 7 are C with only the hash changed: not stored
  reg clk1x=0, clk2x=0, rclk=0;
  always #2 clk1x=~clk1x;
  always #1 clk2x=~clk2x;
  always #3 rclk=~rclk;
  reg clk3x=0; always #(2.0/3) clk3x=~clk3x;
  reg ram_busy=0;
  reg vsync=0, ram_ena=0, ram_done=0, spu_ena=0, spu_done=0, sample=0;
  reg vram_rd=0, vram_we=0, vram_busy=0, vram_ready=0;
  reg [12:0] idx=0; wire [31:0] q;
  psx_perf_log #(.FRAMES(8), .STAGES(STAGES), .VIDEO(VIDEO)) dut(.clk1x(clk1x),.clk2x(clk2x),.clk3x(clk3x),.stage({1'b0,1'b1,ram_busy}),.vsync(vsync),.ram_ena(ram_ena),.ram_done(ram_done),
    .spu_ena(spu_ena),.spu_done(spu_done),.sample(sample),.vram_rd(vram_rd),.vram_we(vram_we),.vram_busy(vram_busy),
    .vram_ready(vram_ready),.status(32'h8000002a),.video_seq(vseq),.video_w4(vw4),.video_w5(32'hBEEF0055),.video_w6(32'hCAFE00EE),.video_w7(vw7),
    .read_clk(rclk),.read_index(idx),.read_data(q));
  integer f, c, errs=0, k; reg [31:0] w[0:63];
  reg vseq=0; reg [31:0] vw4=0, vw7=0;
  function [31:0] pic4(input integer f); pic4=f==0||f==1 ? 32'hDEAD0004 : f==2||f==4 ? 32'hDEAD1004 : 32'hDEAD2004; endfunction
  always @(posedge clk2x) begin
    k=$time%20; vram_we<=(k<4); vram_busy<=(k<4);
  end
  initial begin
    for (f=0; f<NF; f=f+1) begin
      for (c=0; c<4000; c=c+1) begin
        @(negedge clk1x);
        vsync=(c<3);
        if (c==1990) begin vw4=pic4(f); vw7=32'hF00D0070|(vw4[15:12]^7); if (f>=6) vw4=vw4^(32'h00100000<<(f-6)); end   // W7 differs per picture too
        if (c==2000) vseq=!vseq;
        ram_ena=(c>=100 && c<1300 && c%12==0); ram_done=(c>=100 && c<1300 && c%12==2);
        if (ram_ena) ram_busy=1; if (ram_done) ram_busy=0;
        spu_ena=(c>=2000 && c<3000 && c%20==0); spu_done=(c>=2000 && c<3000 && c%20==4);
        sample=(c>=3500 && c<3507);
      end
    end
    vsync=0;
    for (c=0;c<64;c=c+1) begin @(negedge rclk); idx=c; @(posedge rclk); @(negedge rclk); w[c]=q; end
    if (w[0]!==32'h50455246 || w[1]!=={STAGES!=0,VIDEO!=0,VIDEO!=0,13'b0,16'd4} || w[2]!==32'h8000002a) begin errs=errs+1; $display("header %h %0d %0d",w[0],w[1],w[2]); end
    // record 1 = frame 1 (record 0 is the partial first frame)
    if (w[R][19:0]<3990 || w[R][19:0]>4000 || w[R][31:22]!==7) begin errs=errs+1; $display("w0 %h",w[R]); end
    // upper 12 bits: main-RAM request count (100 ram_ena pulses a frame)
    if (w[R+1]!=={12'd100,20'd300}) begin errs=errs+1; $display("ram %0d",w[R+1]); end
    if (w[R+2]!=={12'd0,20'd250}) begin errs=errs+1; $display("spu %0d",w[R+2]); end
    if (w[R+3][31:16] < 1590/16 || w[R+3][31:16] > 1610/16) begin errs=errs+1; $display("vram %h",w[R+3]); end
    // ram_busy is high 2 of every 12 clk1x cycles over 100 requests: 200 clk1x = 600 clk3x
    if (STAGES && (w[R+4]<590 || w[R+4]>610 || w[R+5]<11950 || w[R+5]>12100 || w[R+6]!==0 || w[R+7]!==0)) begin
      errs=errs+1; $display("stages %0d %0d %0d %0d",w[R+4],w[R+5],w[R+6],w[R+7]); end
    if (VIDEO && (w[R+4]!==32'hDEAD0004 || w[R+5]!==32'hBEEF0055 || w[R+6]!==32'hCAFE00EE || w[R+7]!==32'hF00D0077
                  || w[R+8]!=={20'd1,12'd2})) begin
      errs=errs+1; $display("video %h %h %h %h %h",w[R+4],w[R+5],w[R+6],w[R+7],w[R+8]); end
    // "alternating" is set on each new picture (it differs from the frame before);
    // record 0 is frame 0, nothing taken yet
    if (VIDEO && (w[4+8]!==32'd0 || w[R+9+4]!==32'hDEAD1004 || w[R+9+8]!=={20'd3,12'd2}
                  || w[R+18+4]!==32'hDEAD2004 || w[R+18+7]!==32'hF00D0075 || w[R+18+8]!=={20'd4,12'd2})) begin
      errs=errs+1; $display("video records %h / %h %h / %h %h %h",w[4+8],w[R+13],w[R+17],w[R+22],w[R+25],w[R+26]); end
    // the frame after a skipped one holds only its own counters
    if (VIDEO && w[R+9+1]!=={12'd100,20'd300}) begin errs=errs+1; $display("ram after skip %0d",w[R+10]); end
    // record 2 repeats it: per-frame deltas, not running totals
    if (STAGES && (w[R+12]!==w[R+4] || w[R+13]<11950 || w[R+13]>12100)) begin
      errs=errs+1; $display("stages2 %0d %0d",w[R+12],w[R+13]); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
