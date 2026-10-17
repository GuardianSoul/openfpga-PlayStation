// psx_apf_video_wide HYBRID + LEAD_LINE: every frame after the first is one
// black lead line at the previous line's width (320, native slot 1), then
// the 236 picture lines.
module tb_apf_wide_lead;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, px=0, black=0;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin
      if (frames>1 && lines!=237) begin errs=errs+1; $display("frame %0d lines %0d want 237", frames, lines); end
      lines=0; frames=frames+1;
    end
    if (de && !skip) begin px=px+1; if (rgb==0) black=black+1; end
    if (!de && de_d) begin
      if (frames>1 && px!=320) begin errs=errs+1; $display("frame %0d line %0d px %0d", frames, lines, px); end
      if (frames>1 && lines==0 && black!=320) begin errs=errs+1; $display("frame %0d lead line black %0d", frames, black); end
      if (frames>1 && lines==1 && black==320) begin errs=errs+1; $display("frame %0d picture line is black", frames); end
      lines=lines+1; px=0; black=0;
    end
  end
  initial begin
    for (f=0;f<5;f=f+1)
      for (l=0;l<241;l=l+1) for (x=0;x<1600;x=x+1) begin
        @(negedge clk); ce=(x%4)==3; vblank=(l<5); hblank=(l<5) || x<120 || x>=120+4*320; hsync=(x<20);
        vsync=(l==2 && x>=300 && x<340);
      end
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
