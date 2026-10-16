// VIDEO_PROBE with PAD_LINES: 236-line frames of 300 CE pixels (320 slot).
// From the third frame: line 0 encodes 236 and line 1 encodes 300 as ten
// 16-pixel blocks (white 1, blue 0), green/yellow after pixel 160; the last
// line is red; padding pixels (300..319) are magenta; other lines pass.
module tb_apf_probe;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.PAD_LINES(1),.VIDEO_PROBE(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, line=0, px=0, frame=0, b;
  reg [23:0] want; reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin line=0; frame=frame+1; end
    if (de && !skip) begin
      if (frame>2) begin
        b = px/16;
        if (px>=300) want=24'hFF00FF;
        else if (line==0) want = px<160 ? ((236>>(9-b))&1 ? 24'hFFFFFF : 24'h0000FF) : 24'h00FF00;
        else if (line==1) want = px<160 ? ((300>>(9-b))&1 ? 24'hFFFFFF : 24'h0000FF) : 24'hFFFF00;
        else if (line==235) want=24'hFF0000;
        else want=24'h123456;
        if (rgb!==want) begin errs=errs+1; if (errs<10) $display("f%0d l%0d x%0d rgb %0h want %0h", frame, line, px, rgb, want); end
      end
      px=px+1;
    end
    if (!de && de_d) begin line=line+1; px=0; end
  end
  initial begin
    for (f=0;f<4;f=f+1)
      for (l=0;l<241;l=l+1) for (x=0;x<700;x=x+1) begin
        @(negedge clk); ce=x%2; vsync=(l==0 && x<50); vblank=(l<5); hblank=(l<5) || x>=600; hsync=(x>=680 && x<690);
      end
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
