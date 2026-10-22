// psx_apf_video_wide LOG: every frame's geometry words (W4/W5/W6) must carry
// the numbers the screen bug reports hang on, with the 0.5.5 recipe's video
// parameters and PAL_BOB. Cases: PAL 480i Fit (288-line fields, 312 HS: whole
// field out, slot 4), PAL 480i Fill (lines 10..277, slot 7), NTSC 480i Fit
// (240-line fields woven, slot 3) and NTSC 480i Fill (lines 8..231, slot 1).
// W4={lines[9:0],hash[9:0],slot[2:0],pal,m480i,bob,i480,fit,fd_on,os_on,
// field_last,pal_frame}; W5={out[9:0],first[9:0],last[9:0],2'b0};
// W6={hs[9:0],wlen[9:0],pic_x0[9:0],2'b0}; W7={pic_y0,pic_y1,pic_x1,2'b0}.
// The picture is a window of lines py0..py1 and pixels px0..px1 on black; the
// hash must repeat for a repeated frame and change when the picture moves a line.
module tb_apf_vlog;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  wire [31:0] w4, w5, w6, w7; wire seq;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.PAL_BOB(1),.LOG(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb),.log_w4(w4),.log_w5(w5),.log_w6(w6),.log_w7(w7),.log_seq(seq));
  integer py0=16, py1=271, px0=8, px1=311, h0, h1, hm0;
  reg seq_d=0;
  // the hash of every frame, at its log_seq toggle
  always @(negedge clk) begin seq_d<=seq; if (seq!==seq_d) begin h0=h1; h1=w4[21:12]; end end
  integer m, l, x, errs=0, check_me=0, nl, vb, il, st, cslot, cout, cfirst, clast, cpal, cbob, ci480, c480, ck_frame;
  // one checked frame's words, sampled at its second (last) vs_edge
  always @(negedge clk) if (vs && check_me) begin
    if (w4[31:22]!==nl) begin errs=errs+1; $display("case %0d lines %0d want %0d", m, w4[31:22], nl); end
    if (w4[8]!==cpal || w4[7]!==c480 || w4[6]!==cbob || w4[5]!==ci480) begin errs=errs+1;
      $display("case %0d flags pal %0d m480i %0d bob %0d i480 %0d want %0d %0d %0d %0d", m, w4[8], w4[7], w4[6], w4[5], cpal, c480, cbob, ci480); end
    if (w4[4]!==!st) begin errs=errs+1; $display("case %0d fit %0d want %0d", m, w4[4], !st); end
    if (w5[31:22]!==cout) begin errs=errs+1; $display("case %0d lines out %0d want %0d", m, w5[31:22], cout); end
    if (w5[21:12]!==cfirst) begin errs=errs+1; $display("case %0d first kept %0d want %0d", m, w5[21:12], cfirst); end
    if (w5[11:2]!==clast) begin errs=errs+1; $display("case %0d last kept %0d want %0d", m, w5[11:2], clast); end
    if (w7[31:22]!==py0 || w7[21:12]!==(py1<nl ? py1 : nl-1) || w7[11:2]!==px1 || w6[11:2]!==px0 || w6[21:12]!==320) begin errs=errs+1;
      $display("case %0d picture y %0d..%0d x %0d..%0d wlen %0d want %0d..%0d %0d..%0d 320", m, w7[31:22], w7[21:12], w6[11:2], w7[11:2], w6[21:12], py0, py1<nl ? py1 : nl-1, px0, px1); end
    $display("note: hs %0d for a %0d-line frame", nl+vb-1, nl); if (w6[31:22]!==nl+vb-1) begin errs=errs+1; $display("case %0d hs %0d want %0d", m, w6[31:22], nl+vb-1); end
    $display("case %0d slot %0d out %0d first %0d last %0d hs %0d wlen %0d picture %0d..%0d x %0d..%0d hash %0d", m, w4[11:9], w5[31:22], w5[21:12], w5[11:2], w6[31:22], w6[21:12], w7[31:22], w7[21:12], w6[11:2], w7[11:2], w4[21:12]);
    if (w4[11:9]!==cslot) begin errs=errs+1; $display("case %0d slot %0d want %0d", m, w4[11:9], cslot); end
  end
  task frame(input integer cnl, input integer cvb, input integer cil, input integer cst, input integer ck);
    begin
      nl=cnl; vb=cvb; il=cil; st=cst; stretch=st; check_me=0; ck_frame=ck;
      for (l=0;l<nl;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%8)==0; vblank=0; vsync=0; ilace=il;
        hblank=!(x>=300 && x<300+320*8); hsync=(x>=3100 && x<3350);
        rgb_in=0; if (l>=py0 && l<=py1 && (x-300)/8>=px0 && (x-300)/8<=px1) begin rgb_in={8'hC0,l[7:0],8'h40}; rgb_in[5:0]=(x-300)/8; end
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; hsync=(x>=3100 && x<3350); vsync=(l==2);
        if (vsync && ck_frame==1) check_me=1;   // the words latch with this vs
      end
      @(negedge clk); check_me=0;
    end
  endtask
  initial begin
    // 0,1: PAL 480i Fit - bob sends the whole 288-line field, slot 4
    m=0; cslot=4; cout=288; cfirst=0; clast=287; cpal=1; cbob=1; ci480=0; c480=1; frame(288,24,1,0,0); frame(288,24,1,0,1);
    // 2,3: PAL 480i Fill - lines 10..277 in the 10:9 slot 7
    m=1; cslot=7; cout=268; cfirst=10; clast=277; frame(288,24,1,1,0); frame(288,24,1,1,1);
    // 4,5: NTSC 480i Fit - woven, slot 3, pal 0 so bob stays 0
    m=2; cslot=3; cout=240; cfirst=0; clast=239; cpal=0; cbob=0; ci480=1; frame(240,22,1,0,0); frame(240,22,1,0,1);
    // 6,7: NTSC 480i Fill - lines 8..231, slot 1
    m=3; cslot=1; cout=224; cfirst=8; clast=231; frame(240,22,1,1,0); frame(240,22,1,1,1);
    // 4: the PAL Fit picture again, then one line lower: same hash, then a new one
    m=4; cslot=4; cout=288; cfirst=0; clast=287; cpal=1; cbob=1; ci480=0; c480=1; frame(288,24,1,0,0); frame(288,24,1,0,1);
    #10 hm0=h1; if (h0!==h1) begin errs=errs+1; $display("hash of a repeated frame %0d then %0d", h0, h1); end
    py0=17; py1=272; frame(288,24,1,0,0); frame(288,24,1,0,1);
    #10 if (h1===hm0) begin errs=errs+1; $display("hash %0d unchanged after a one-line move", h1); end
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
