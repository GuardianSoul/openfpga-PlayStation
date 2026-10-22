// psx_apf_video_wide FILL_LEAD with the RC's video parameters (FILL_DISP, FILL_BLEND,
// PACE, WEAVE576, EVEN_LINES, LEAD_LINE 2): in 480i Fill the lead line is the source
// line above the window, not black; 480i Fit keeps its black lead line and
// progressive Fill its doubled lines. Per case, one field (VS to VS): DE lines, the
// first DE line's source line and black pixels, the last picture line.
module tb_apf_filllead;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=1, dispblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.FILL_DISP(1),.FILL_LEAD(1),.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.PAL_TIMING(1),.CROP_LINES(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.FILL_CENTER(1),.EVEN_LINES(1),.WEAVE576(1),.PAL_SHAPE(1),.FILL_BLEND(1),.PACE(1)) dut(
    .clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.dispblank(dispblank),.interlace(ilace),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),
    .stretch(stretch),.hide_overscan(1'b1),.dock_mode(2'b0),.wide_even(1'b0),.fill_phase(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer l, x, m, errs=0, rec=0, nl=0, w=0, nblk=0, g0=-1, blk0=-1, glast=-1;
  integer want_n, want_g0, want_blk0, want_glast;
  reg de_d=0, vs_d=0;
  // G carries the source line + 1 (0: black)
  always @(posedge clk) begin
    de_d<=de; vs_d<=vs;
    if (vs && !vs_d) begin
      if (rec==2) begin
        if (nl!==want_n || g0!==want_g0 || blk0!==want_blk0 || glast!==want_glast) begin errs=errs+1;
          $display("case %0d: %0d DE lines, first line source %0d (%0d black px), last picture %0d; want %0d, %0d (%0d), %0d", m, nl, g0-1, blk0, glast-1, want_n, want_g0-1, want_blk0, want_glast-1); end
        else $display("case %0d: %0d DE lines, first line source %0d (%0d black px), last picture %0d", m, nl, g0-1, blk0, glast-1);
        rec=0;
      end
      if (rec==1) rec=2;
      nl=0; g0=-1; glast=-1;
    end
    if (de && !skip) begin w=w+1; if (rgb[15:8]==0) nblk=nblk+1; end
    if (!de && de_d) begin
      if (nl==0) begin g0=rgb_l; blk0=nblk; end
      if (rgb_l!=0) glast=rgb_l;
      nl=nl+1; w=0; nblk=0;
    end
  end
  integer rgb_l=0; always @(posedge clk) if (de && !skip) rgb_l=rgb[15:8];
  task frame(input integer nlines, input integer vb, input integer ht, input integer il);
    begin
      for (l=0;l<nlines;l=l+1) for (x=0;x<ht;x=x+1) begin
        @(negedge clk);
        ce=(x%8)==0; vblank=0; vsync=0; ilace=il; dispblank=0;
        hblank=!(x>=300 && x<300+320*8); hsync=(x>=3100 && x<3350);
        rgb_in={8'hC0, l[7:0]+8'd1, 8'hC0};
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<ht;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; dispblank=1; hsync=(x>=3100 && x<3350); vsync=(l==2);
      end
    end
  endtask
  // settle, then check the field after the next VS
  task check(input integer nlines, input integer vb, input integer ht, input integer il);
    begin
      repeat (4) frame(nlines,vb,ht,il);
      rec=1; frame(nlines,vb,ht,il); frame(nlines,vb,ht,il); rec=0;
    end
  endtask
  initial begin
    // 0: NTSC 480i Fill (the BIOS): line 7 leads, then 8..231 and a pad: 226, no black on top
    m=0; stretch=1; want_n=226; want_g0=8; want_blk0=0; want_glast=232; check(240,22,3413,1);
    // 1: NTSC 480i Fit: the black lead line, 240 lines (Hide Overscan: 8..231 lit) and a pad
    m=1; stretch=0; want_n=242; want_g0=0; want_blk0=640; want_glast=232; check(240,22,3413,1);
    // 2: NTSC progressive Fill: 8..231 twice, no lead line
    m=2; stretch=1; want_n=448; want_g0=9; want_blk0=0; want_glast=232; check(240,22,3413,0);
    // 3: PAL 480i Fill: line 15 leads, then 16..271 (the 256 window): 257 and a pad
    m=3; stretch=1; want_n=258; want_g0=16; want_blk0=0; want_glast=16; check(288,24,3406,1);
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
