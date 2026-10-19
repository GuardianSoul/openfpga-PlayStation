// psx_apf_video_wide VIDEO_PROBE: 310-pixel lines at the 320 pixel clock
// (CE every 8 clocks), 12 lines per frame. In the second frame, output line
// 0 (after the previous frame's last line) shows that frame's line count (12), line 1 the width (310), line
// 2 the CE period (8), line 3 the slot (1), each as 10 bit blocks of 16
// pixels on its colour; the 5-pixel native padding is magenta and other
// lines show the picture. (A last line whose read starts after VS is red.)
module tb_apf_wide_probe;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.PAL288(1),.HYBRID(1),.VIDEO_PROBE(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  localparam W=310, DIV=8, LINES=12;
  integer f, l, x, errs=0, px=0, oline=0, frame=0, b;
  reg de_d=0; reg [23:0] line_px [0:639];
  reg [9:0] val; reg [23:0] bg;
  task check_line; begin
    if (frame==2 && oline==0) begin
      // the first frame's last line: its read began before VS, so it is a picture line
      if (line_px[100]!==24'h123456) begin errs=errs+1; $display("last line %h", line_px[100]); end
    end else if (frame==2) begin
      oline=oline-1;
      if (oline<4) begin
        val=0;
        for (b=0;b<10;b=b+1) begin
          if (line_px[5+16*b+8]==24'hFFFFFF) val[9-b]=1;
          else if (line_px[5+16*b+8]!==24'h0000FF) begin errs=errs+1; $display("line %0d block %0d colour %h", oline, b, line_px[5+16*b+8]); end
        end
        bg=oline==0 ? 24'h00FF00 : oline==1 ? 24'hFFFF00 : oline==2 ? 24'h00FFFF : 24'hFF8000;
        if (line_px[5+200]!==bg) begin errs=errs+1; $display("line %0d background %h", oline, line_px[205]); end
        if (val!==(oline==0 ? LINES : oline==1 ? W : oline==2 ? DIV : 1)) begin errs=errs+1; $display("line %0d value %0d", oline, val); end
      end else if (line_px[100]!==24'h123456) begin errs=errs+1; $display("line %0d picture %h", oline, line_px[100]); end
      if (line_px[0]!==24'hFF00FF || line_px[319]!==24'hFF00FF) begin errs=errs+1; $display("line %0d padding %h %h", oline, line_px[0], line_px[319]); end
      oline=oline+1;
    end
  end endtask
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin frame=frame+1; oline=0; end
    if (de) begin if (px<640) line_px[px]=rgb; px=px+1; end
    if (!de && de_d) begin check_line; px=0; oline=oline+1; end
  end
  initial begin
    for (f=0;f<3;f=f+1) begin
      for (x=0;x<3413*3;x=x+1) begin @(negedge clk); hblank=1; vblank=1; vsync=x<3413; hsync=(x%3413)>=3100 && (x%3413)<3350; ce=0; end
      for (l=0;l<LINES;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); vsync=0; vblank=0;
        ce=(x%DIV)==0; hblank=!(x>=300 && x<300+W*DIV); hsync=(x>=3100 && x<3350);
        rgb_in=24'h123456;
      end
    end
    for (x=0;x<3413*2;x=x+1) begin @(negedge clk); hblank=1; vblank=1; hsync=(x%3413)>=3100 && (x%3413)<3350; end
    if (frame<2) begin errs=errs+1; $display("frames %0d", frame); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
