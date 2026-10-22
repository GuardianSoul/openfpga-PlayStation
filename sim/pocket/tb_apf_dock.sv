// psx_apf_video_wide DOCK_TEST slot-word timing, with the probe recipe's video
// parameters: per mode, one steady NTSC Fit frame (320 lines doubled, slot 0)
// and one Fill frame (lines twice, slot 1). At every DE falling edge, RGB on the
// 1st, 2nd and 6th clock after it must be: mode 0 word/0/0, 1 word/word/0,
// 2 0/word/0, 3 word/word/word (the slot word is {8'b0, slot, 13'b0}); the
// picture pixels (a hash) and the DE lines per frame must equal mode 0's.
module tb_apf_dock;
  parameter integer TWICE=0;   // EOL_TWICE alone, DOCK_TEST off: every mode must behave as mode 1
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, stretch=0; reg [1:0] mode=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.EVEN_LINES(1),.WEAVE576(1),.DOCK_TEST(TWICE ? 0 : 1),.EOL_TWICE(TWICE)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.dock_mode(mode),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer l, x, errs=0, counting=0, arm=0, armed=0, nl, k, after=99, bad_eol=0, cs;
  integer lines [0:3]; reg [31:0] sum [0:3];
  reg de_d=0; reg [31:0] s; reg [23:0] e1, e2, e6, word;
  always @(negedge clk) if (vs) begin
    if (arm && counting==0 && armed==0) begin counting=1; armed=1; end
    else if (counting==1) counting=0;
  end
  always @(negedge clk) if (!arm) armed=0;
  always @(negedge clk) begin
    de_d<=de;
    if (counting) begin
      if (de) s=(s^rgb)*32'd16777619;
      if (!de && de_d) begin nl=nl+1; after=1; end else if (after<99) after=after+1;
      if (de) after=99;
      if (after==1) e1=rgb; if (after==2) e2=rgb;
      if (after==6) begin
        e6=rgb; word={8'b0, stretch ? 3'd1 : 3'd0, 13'b0};
        if (TWICE ? (e1!==word || e2!==word || e6!==24'h0) : (e1!==(mode==2 ? 24'h0 : word) || e2!==(mode==0 ? 24'h0 : word) || e6!==(mode==3 ? word : 24'h0))) begin
          bad_eol=bad_eol+1; if (bad_eol<4) $display("mode %0d line %0d: after DE %h %h .. %h", mode, nl, e1, e2, e6); end
      end
    end
  end
  task frame;
    begin
      for (l=0;l<240;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%8)==0; vblank=0; vsync=0;
        hblank=!(x>=300 && x<300+320*8); hsync=(x>=3100 && x<3350);
        rgb_in={8'h80|l[3:0], l[7:0], 8'h40|x[5:0]};
      end
      for (l=0;l<22;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; hsync=(x>=3100 && x<3350); vsync=(l==2);
      end
    end
  endtask
  initial begin
    for (cs=0; cs<2; cs=cs+1) begin
      stretch=cs;
      for (k=0;k<4;k=k+1) begin
        mode=k; frame; frame;
        nl=0; s=0; bad_eol=0; arm=1; frame; frame; arm=0;
        lines[k]=nl; sum[k]=s;
        $display("%s mode %0d: %0d DE lines, pixel hash %h, %0d bad line ends", cs ? "Fill" : "Fit ", k, nl, s, bad_eol);
        if (bad_eol) errs=errs+1;
        if (k>0 && (lines[k]!==lines[0] || sum[k]!==sum[0])) begin errs=errs+1; $display("  picture differs from mode 0"); end
      end
    end
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
