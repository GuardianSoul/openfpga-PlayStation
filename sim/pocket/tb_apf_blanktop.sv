// BLANK_TOP: the first active line of each 240-line field goes out black
// (B=1; B=2 blanks two), so the first non-black source line is B.
// The line count stays the lead plus 240 lines.
module tb_apf_blanktop;
  parameter integer B=1;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1),.FIELD_INVERT(1),.PAD_LINES(1),.PAL288(1),.CENTER_LINES(1),.LEAD_LINE(1),.SLOT6_368(1),.VS_ON_HS(1),.CROP_LINES(1),.BLANK_TOP(B)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b0),.field_swap(1'b0),.video_test(2'b00),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, first=-1;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin
      if (frames>2 && lines!=241) begin errs=errs+1; $display("frame %0d lines %0d want 241", frames, lines); end
      if (frames>2 && first!=B) begin errs=errs+1; $display("frame %0d first source line %0d want %0d", frames, first, B); end
      lines=0; frames=frames+1; first=-1;
    end
    if (de && !skip && rgb!=0 && first<0) first=rgb[15:0];
    if (!de && de_d) lines=lines+1;
  end
  initial begin
    for (f=0;f<6;f=f+1)
      for (l=0;l<250;l=l+1) for (x=0;x<1600;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5) || (l>=245); hblank=vblank || x<100 || x>=740; hsync=(x<20);
        vsync=(l==2 && x<40); rgb_in={8'h80,l[15:0]-16'd5};
      end
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
