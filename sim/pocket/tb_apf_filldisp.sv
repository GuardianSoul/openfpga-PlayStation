// psx_apf_video_wide FILL_DISP with the RC recipe's Fill parameters (FILL_CENTER,
// EVEN_LINES, WEAVE576, PAL_SHAPE): Fill centres its window on the GPU's display
// range (dispblank low on lines dy0..dy1), whatever the picture content
// (lines py0..py1 lit) does. The log words give the kept window of each checked
// frame.
module tb_apf_filldisp;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=1, dispblank=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  wire [31:0] w4, w5, w6, w7; wire seq;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.LOG(1),.FILL_CENTER(1),.EVEN_LINES(1),.WEAVE576(1),.PAL_SHAPE(1),.FILL_DISP(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .dispblank(dispblank),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.dock_mode(2'b0),.wide_even(1'b0),.fill_phase(1'b0),
    .rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb),.log_w4(w4),.log_w5(w5),.log_w6(w6),.log_w7(w7),.log_seq(seq));
  integer l, x, errs=0, py0, py1, dy0, dy1, lit, check_me=0, cfirst, clast, m=0;
  always @(negedge clk) if (vs && check_me) begin
    if (w5[21:12]!==cfirst || w5[11:2]!==clast) begin errs=errs+1;
      $display("case %0d kept %0d..%0d want %0d..%0d", m, w5[21:12], w5[11:2], cfirst, clast); end
    else $display("case %0d kept %0d..%0d", m, w5[21:12], w5[11:2]);
  end
  // nl lines, vb blank lines (ht clocks a line); ck: check the frame
  integer il=0;
  task frame(input integer nl, input integer vb, input integer ht, input integer ck);
    begin
      check_me=0;
      for (l=0;l<nl;l=l+1) for (x=0;x<ht;x=x+1) begin
        @(negedge clk);
        ce=(x%8)==0; vblank=0; vsync=0; ilace=il; dispblank=!(l>=dy0 && l<=dy1);
        hblank=!(x>=300 && x<300+320*8); hsync=(x>=3100 && x<3350);
        rgb_in=lit && l>=py0 && l<=py1 && !dispblank ? 24'hC08040 : 24'h0;
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<ht;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; dispblank=1; hsync=(x>=3100 && x<3350); vsync=(l==2);
        if (vsync && ck) check_me=1;
      end
      @(negedge clk); check_me=0;
    end
  endtask
  task ntsc(input integer ck); frame(240,22,3413,ck); endtask
  task pal(input integer ck); frame(288,24,3406,ck); endtask
  initial begin
    lit=1;
    // 0: NTSC, display 0..239, only a dark spot 100..110 lit: the standard window 8..231
    m=0; dy0=0; dy1=239; py0=100; py1=110; cfirst=8; clast=231; ntsc(0); ntsc(0); ntsc(0); ntsc(1);
    // 1: the display range moves down to 16..239: 16..239
    m=1; dy0=16; dy1=239; cfirst=16; clast=239; ntsc(0); ntsc(1);
    // 2: a sprite lights lines above it and the whole range: the window stays
    m=2; py0=0; py1=239; ntsc(0); ntsc(1);
    // 3: twenty black frames, then a dark partial picture: it stays
    m=3; lit=0; repeat (20) ntsc(0); lit=1; py0=120; py1=130; ntsc(0); ntsc(1);
    // 4: display 0..223: 0..223
    m=4; dy0=0; dy1=223; cfirst=0; clast=223; ntsc(0); ntsc(1);
    // 5: PAL, Ape Escape's display 20..275 with a dark picture: the 256 window 20..275
    m=5; dy0=20; dy1=275; py0=150; py1=160; cfirst=20; clast=275; pal(0); pal(0); pal(0); pal(1);
    // 6: PAL display 0..287: centred, 16..271
    m=6; dy0=0; dy1=287; cfirst=16; clast=271; pal(0); pal(1);
    // 7: NTSC 480i (the BIOS), display 0..231: the standard window 8..231, not 4..227
    m=7; il=1; dy0=0; dy1=231; py0=0; py1=231; cfirst=8; clast=231; ntsc(0); ntsc(0); ntsc(0); ntsc(1);
    // 8: back to progressive with the same range: centred, 4..227
    m=8; il=0; cfirst=4; clast=227; ntsc(0); ntsc(0); ntsc(1);
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
