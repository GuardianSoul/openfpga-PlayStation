// psx_apf_video_wide: lines of 256/320/368/512/640 pixels at their PSX dot
// clocks (CE every 10/8/7/5/4 clocks, 3413-clock lines). Every output line
// has exactly 640 DE pixels with skip low, pixel i is source pixel
// floor(i*w/640) of the line before (pixels are tagged {line, x}), the
// end-of-line word names slot 0 (640x240), and output lines never overlap HS.
module tb_apf_wide;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.PAL288(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer m, l, x, errs=0, lines_out=0, px=0, w, div, widths[0:4], divs[0:4], checked=0;
  integer src_line;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (de) begin
      if (skip) begin errs=errs+1; $display("skip during DE"); end
      if (hsync) begin errs=errs+1; $display("DE during HS"); end
      if (rgb[9:0]!==(px*w)/640 || rgb[19:10]!==src_line[9:0]) begin
        errs=errs+1; if (errs<10) $display("w %0d px %0d got line %0d x %0d want line %0d x %0d", w, px, rgb[19:10], rgb[9:0], src_line, (px*w)/640);
      end
      px=px+1;
    end
    if (!de && de_d) begin
      if (px!=640) begin errs=errs+1; $display("line width %0d", px); end
      if (rgb!=={8'b0,3'd0,13'b0}) begin errs=errs+1; $display("slot word %h", rgb); end
      px=0; lines_out=lines_out+1; checked=checked+1; src_line=src_line+1;
    end
  end
  initial begin
    widths[0]=256; divs[0]=10; widths[1]=320; divs[1]=8; widths[2]=368; divs[2]=7; widths[3]=512; divs[3]=5; widths[4]=640; divs[4]=4;
    for (m=0;m<5;m=m+1) begin
      w=widths[m]; div=divs[m]; src_line=0;
      for (l=0;l<6;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%div)==0;
        vblank=0;
        hblank=!(x>=300 && x<300+w*div);
        hsync=(x>=3100 && x<3350);
        rgb_in=0;
        rgb_in[19:10]=l[9:0]; rgb_in[9:0]=(x-300)/div;
      end
      // end of the mode: let the last line drain
      for (x=0;x<3413;x=x+1) begin @(negedge clk); hblank=1; vblank=1; hsync=(x>=3100 && x<3350); end
    end
    @(negedge clk);
    if (checked!=30) begin errs=errs+1; $display("lines out %0d", checked); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
