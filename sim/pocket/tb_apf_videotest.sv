// VIDEO_TEST: with video_test=1 (vblank VS + lead line) every frame after the
// first has one black lead line of the slot width (320) before its 236
// picture lines, the lead line in the vblank lines after VS; video_test=0
// leaves 236 lines; video_test=2 takes VS from the PSX vsync instead. The
// frame where the setting changes is not checked.
module tb_apf_videotest;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [1:0] vt=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.PAD_LINES(1),.VS_AT_VBLANK(1),.VIDEO_TEST(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.video_test(vt),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, px=0, black=0, first_black=-1, exp_lines;
  reg de_d=0, vsync_seen=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin
      if (frames>1 && frames%4!=3 && lines!=exp_lines) begin errs=errs+1; $display("frame %0d vt %0d lines %0d want %0d", frames, vt, lines, exp_lines); end
      if (frames>1 && frames%4!=3 && vt==1 && first_black!=320) begin errs=errs+1; $display("frame %0d lead line black px %0d", frames, first_black); end
      lines=0; frames=frames+1; first_black=-1;
    end
    if (de && !skip) begin px=px+1; if (rgb==0) black=black+1; end
    if (!de && de_d) begin if (lines==0) first_black=(black==px) ? px : -2; lines=lines+1; px=0; black=0; end
  end
  initial begin
    for (f=0;f<12;f=f+1) begin
      vt = f<4 ? 2'd0 : f<8 ? 2'd1 : 2'd2; exp_lines = vt==1 ? 237 : 236;
      for (l=0;l<241;l=l+1) for (x=0;x<800;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5); hblank=(l<5) || x<120 || x>=720; hsync=(x<20);
        vsync=(l==2 && x>=300 && x<340);
      end
    end
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
