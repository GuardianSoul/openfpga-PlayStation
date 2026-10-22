// psx_apf_video_wide FILL_CENTER with the 0.5.5 recipe's video parameters and
// PAL_BOB: Fill keeps its 268 / 224-line window but centres it on the picture
// seen since the last mode change. The picture is lines py0..py1 on black; the
// log words give the kept window (first/last kept) of each checked frame.
module tb_apf_fillcenter;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=1; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  wire [31:0] w4, w5, w6, w7; wire seq;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.PAL_BOB(1),.LOG(1),.FILL_CENTER(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb),.log_w4(w4),.log_w5(w5),.log_w6(w6),.log_w7(w7),.log_seq(seq));
  integer l, x, errs=0, py0, py1, lit, check_me=0, cfirst, clast, m=0;
  // the words latch at the vs that ends the checked frame
  always @(negedge clk) if (vs && check_me) begin
    if (w5[21:12]!==cfirst || w5[11:2]!==clast) begin errs=errs+1;
      $display("case %0d kept %0d..%0d want %0d..%0d", m, w5[21:12], w5[11:2], cfirst, clast); end
    else $display("case %0d kept %0d..%0d", m, w5[21:12], w5[11:2]);
  end
  // nl lines, vb blank lines; il: 480i; ck: check the frame
  task frame(input integer nl, input integer vb, input integer il, input integer ck);
    begin
      check_me=0;
      for (l=0;l<nl;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%8)==0; vblank=0; vsync=0; ilace=il;
        hblank=!(x>=300 && x<300+320*8); hsync=(x>=3100 && x<3350);
        rgb_in=lit && l>=py0 && l<=py1 ? 24'hC08040 : 24'h0;
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; hsync=(x>=3100 && x<3350); vsync=(l==2);
        if (vsync && ck) check_me=1;
      end
      @(negedge clk); check_me=0;
    end
  endtask
  initial begin
    lit=1;
    // 0: PAL 480i (bob), picture 16..271, centred: the standard 10..277
    m=0; py0=16; py1=271; cfirst=10; clast=277;
    frame(288,24,1,0); frame(288,24,1,0); frame(288,24,1,0); frame(288,24,1,1);
    // 1: a dark frame (picture 120..130 only) does not move it: the merged lines stay 16..271
    m=1; py0=120; py1=130; frame(288,24,1,0); frame(288,24,1,1);
    // 2: eight black frames reset it; the picture comes back drawn lower, 30..285: top 20 (the limit)
    m=2; lit=0; repeat (8) frame(288,24,1,0);
    lit=1; py0=30; py1=285; cfirst=20; clast=287; frame(288,24,1,0); frame(288,24,1,0); frame(288,24,1,1);
    // 3: PAL progressive (a mode change) with picture 20..275: top 14
    m=3; py0=20; py1=275; cfirst=14; clast=281; frame(288,26,0,0); frame(288,26,0,0); frame(288,26,0,0); frame(288,26,0,1);
    // 4: NTSC progressive, picture 20..239: top 16 (the 240-line limit)
    m=4; py0=20; py1=239; cfirst=16; clast=239; frame(240,22,0,0); frame(240,22,0,0); frame(240,22,0,0); frame(240,22,0,1);
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
