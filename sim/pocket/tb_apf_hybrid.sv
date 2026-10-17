// psx_apf_video_wide HYBRID: 240-line modes. 256/320/512-pixel lines keep
// native slots 2/1/7 at their own width; a 310-pixel line goes to the 320
// slot with 5 black pixels each side, a 336-pixel line goes to it
// centre-cropped (8 pixels off each side); 368 and 640 are stretched to 640
// (slot 0). Pixel values are tagged {line, x} and checked per output pixel.
module tb_apf_hybrid;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.PAL288(1),.HYBRID(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer m, l, x, errs=0, px=0, w, div, ow, lead, crop, slot, checked=0, src_line, sx;
  integer widths[0:6], divs[0:6], outs[0:6], slots[0:6];
  reg de_d=0; reg [23:0] want;
  always @(posedge clk) begin
    de_d<=de;
    if (de) begin
      if (ow!=640) begin   // native
        if (px<lead || px>=lead+w) want=24'h0; else begin want=0; want[19:10]=src_line; want[9:0]=px-lead+crop; end
      end else begin want=0; want[19:10]=src_line; want[9:0]=(px*w)/640; end
      if (rgb!==want) begin errs=errs+1; if (errs<10) $display("w %0d px %0d got %h want %h", w, px, rgb, want); end
      px=px+1;
    end
    if (!de && de_d) begin
      if (px!=ow) begin errs=errs+1; $display("w %0d line width %0d want %0d", w, px, ow); end
      if (rgb!=={8'b0,slot[2:0],13'b0}) begin errs=errs+1; $display("w %0d slot word %h want %0d", w, rgb, slot); end
      px=0; checked=checked+1; src_line=src_line+1;
    end
  end
  initial begin
    widths[0]=256; divs[0]=10; outs[0]=256; slots[0]=2;
    widths[1]=320; divs[1]=8;  outs[1]=320; slots[1]=1;
    widths[2]=368; divs[2]=7;  outs[2]=640; slots[2]=0;
    widths[3]=512; divs[3]=5;  outs[3]=512; slots[3]=7;
    widths[4]=640; divs[4]=4;  outs[4]=640; slots[4]=0;
    widths[5]=310; divs[5]=8;  outs[5]=320; slots[5]=1;
    widths[6]=336; divs[6]=8;  outs[6]=320; slots[6]=1;
    for (m=0;m<7;m=m+1) begin
      w=widths[m]; div=divs[m]; ow=outs[m]; slot=slots[m]; lead=ow>w ? (ow-w)/2 : 0; crop=w>ow ? (w-ow)/2 : 0; src_line=0;
      for (l=0;l<6;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%div)==0; vblank=0;
        hblank=!(x>=300 && x<300+w*div);
        hsync=(x>=3100 && x<3350);
        rgb_in=0; rgb_in[19:10]=l[9:0]; rgb_in[9:0]=(x-300)/div;
      end
      for (x=0;x<3413;x=x+1) begin @(negedge clk); hblank=1; vblank=1; hsync=(x>=3100 && x<3350); end
    end
    @(negedge clk);
    if (checked!=42) begin errs=errs+1; $display("lines out %0d", checked); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
