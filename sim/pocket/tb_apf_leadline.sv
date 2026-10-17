// LEAD_LINE with the 0.5.2 video parameters (VS on the PSX vsync, centring):
// every frame after the first is one black lead line of the slot width
// (320 CE pixels, as Video test D) and then its 236 picture lines.
module tb_apf_leadline;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1),.FIELD_INVERT(1),.PAD_LINES(1),.PAL288(1),.CENTER_LINES(1),.LEAD_LINE(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.video_test(2'b00),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, px=0, black=0, first_black=-1;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin
      if (frames>1 && lines!=237) begin errs=errs+1; $display("frame %0d lines %0d want 237", frames, lines); end
      if (frames>1 && first_black!=320) begin errs=errs+1; $display("frame %0d lead line black px %0d", frames, first_black); end
      lines=0; frames=frames+1; first_black=-1;
    end
    if (de && !skip) begin px=px+1; if (rgb==0) black=black+1; end
    if (!de && de_d) begin
      if (lines==0) first_black=(black==px) ? px : -2;
      else if (frames>1 && px!=320) begin errs=errs+1; $display("frame %0d line %0d px %0d", frames, lines, px); end
      lines=lines+1; px=0; black=0;
    end
  end
  initial begin
    for (f=0;f<5;f=f+1)
      for (l=0;l<241;l=l+1) for (x=0;x<800;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5); hblank=(l<5) || x<120 || x>=760; hsync=(x<20);
        vsync=(l==2 && x>=300 && x<340);
      end
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
