// psx_apf_video_wide LEAD_LINE at real line timing (3413 clocks, HS 250):
// the lead line of a line sent at 640 must end before the next HS, as the
// picture lines do. Frames of 240 lines (22 blank, VS on blank line 2) at
// width w with CE every div clocks; Stretch (S=1) sends them at 640. Checks
// every frame after the first: no HS while DE is high, 241 DE lines (black
// lead + 240 picture lines), each as wide as the slot, each after an HS.
// B (FILL_DETECT): the first and last B lines are black; Stretch sends
// only lines 8-231 (the fixed crop), so 225 DE lines and from the seventh
// frame no picture line is black.
module tb_apf_wide_lead640 #(parameter integer W=320, parameter integer DIV=8, parameter integer S=1, parameter integer B=0);
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.FILL_DETECT(B!=0)) dut(
    .clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),
    .field_swap(1'b0),.stretch(S[0]),.rgb_in(band ? 24'h0 : 24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  localparam integer NL=B!=0 && S ? 225 : 241;
  localparam integer OW=S ? 640 : (W==320 ? 320 : W==256 ? 256 : 640);
  integer f, l, x, errs=0, lines=0, frames=0, px=0, black=0;
  reg de_d=0, band=0, hs_seen=0;
  always @(posedge clk) begin
    de_d<=de;
    if (hs && de && frames>1) begin errs=errs+1; if (errs<10) $display("frame %0d line %0d: HS while DE high", frames, lines); end
    if (hs) hs_seen=1;
    if (de && !de_d) begin
      if (!hs_seen && frames>1) begin errs=errs+1; if (errs<10) $display("frame %0d line %0d: DE without HS", frames, lines); end
      hs_seen=0;
    end
    if (vs) begin
      if (frames>1 && lines!=NL) begin errs=errs+1; $display("frame %0d lines %0d want %0d", frames, lines, NL); end
      lines=0; frames=frames+1;
    end
    if (de && !skip) begin px=px+1; if (rgb==0) black=black+1; end
    if (!de && de_d) begin
      if (frames>1 && px!=OW) begin errs=errs+1; if (errs<10) $display("frame %0d line %0d px %0d want %0d", frames, lines, px, OW); end
      if (frames>1 && lines==0 && black!=px) begin errs=errs+1; $display("frame %0d lead line not black (%0d of %0d)", frames, black, px); end
      if (B!=0 && frames>6 && lines>0 && black==px) begin errs=errs+1; if (errs<10) $display("frame %0d line %0d black", frames, lines); end
      if (frames>1 && lines==1 && black==px && B==0) begin errs=errs+1; $display("frame %0d first picture line is black", frames); end
      lines=lines+1; px=0; black=0;
    end
  end
  initial begin
    for (f=0;f<(B!=0 ? 8 : 4);f=f+1)
      for (l=0;l<262;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); ce=(x%DIV)==0; vblank=(l>=240); hblank=vblank || !(x>=300 && x<300+W*DIV); band=l<B || l>=240-B;
        hsync=(x>=3100 && x<3350); vsync=(l==242 && x>=3100) || l==243 || (l==244 && x<3100);
      end
    $display("errs=%0d", errs); $finish;
  end
endmodule
