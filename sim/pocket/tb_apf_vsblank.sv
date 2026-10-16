// VS_AT_VBLANK: frames whose PSX vsync rises after the first active line.
// Every DE line must fall between two VS pulses, 236 lines per frame, and
// the first line after VS must be the frame's first line (its pixels are
// tagged with the line number). Without the parameter the first line of
// each frame lands before VS (the stray row on the Pocket).
module tb_apf_vsblank;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.PAD_LINES(1),.VS_AT_VBLANK(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, first=1;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin
      if (frames>0 && lines!=236) begin errs=errs+1; $display("frame %0d lines %0d", frames, lines); end
      lines=0; frames=frames+1; first=1;
    end
    if (de && !skip && first && frames>0) begin
      if (rgb[7:0]!==8'd5) begin errs=errs+1; $display("frame %0d starts with line %0d", frames, rgb[7:0]); end
      first=0;
    end
    if (!de && de_d) lines=lines+1;
  end
  initial begin
    for (f=0;f<5;f=f+1)
      for (l=0;l<241;l=l+1) for (x=0;x<700;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5); hblank=(l<5) || x>=600; hsync=(x>=680 && x<690);
        vsync=(l==5 && x>=620 && x<650);   // PSX vsync after the first active line
        rgb_in={16'h0101, l[7:0]};
      end
    @(negedge clk); @(negedge clk);
    if (frames<4) begin errs=errs+1; $display("frames=%0d", frames); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
