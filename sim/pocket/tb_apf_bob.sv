// psx_apf_video_wide PAL_BOB on the 0.5.5 recipe's parameters (HYBRID, ASPECT,
// CROP_LINES, PAL_TIMING, SLOT368, DBL320, FILL_DETECT, OVERSCAN_BLANK,
// LEAD_LINE, VS_ON_HS, FIELD_INVERT): every width (256, 320, 368, 384, 512, 640
// at their CE periods) in PAL 480i (288-line fields, 312 HS) and NTSC 480i
// (240-line fields, 262 HS), in Original (fit) and in Fill (stretch). A PAL
// field is shown as a whole frame of its own (bob), so the lines out equal the
// height of the slot it is tagged with: 288 in the 4:3 640x288 slot (4), lines
// 10..277 (268) in the 10:9 640x268 slot (7), and the APF 480i frame feature
// word is NOT sent. NTSC keeps weaving (pal is 0, so i480 is mode480i): 240
// lines per field in the 4:3 640x480 slot (3) and 8..231 (224) in the 640x448
// slot (1, DBL320), with the feature word at every VS.
module tb_apf_bob;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(1),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.PAL_BOB(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer m, f, l, x, errs=0, px=0, got=0, w, div, nl, il, fill, pal, slot, keep, t, checking=0, cases=0, feat=0, want_feat;
  integer ws[0:5], ds[0:5];
  reg de_d=0; reg [23:0] want;
  always @(posedge clk) begin
    de_d<=de;
    if (vs && rgb[23:8]==8'b0 && rgb[1:0]==2'b10) feat=feat+1;   // APF 480i frame feature word at VS
    if (vs) begin
      if (checking==2) begin
        if (got!=keep) begin errs=errs+1; $display("case %0d w%0d nl%0d fill%0d: lines %0d want %0d", m, w, nl, fill, got, keep); end
        if (feat!=want_feat) begin errs=errs+1; $display("case %0d w%0d fill%0d: 480i feature words %0d want %0d", m, w, fill, feat, want_feat); end
        checking=0;
      end
      if (checking==1) begin checking=2; got=-1; feat=0; end   // -1: the LEAD_LINE black line comes first
    end
    if (de && checking==2 && got>=0) begin
      want=0; want[19:10]=t+got; want[9:0]=(px*w)/640;
      if (rgb!==want) begin errs=errs+1; if (errs<12) $display("case %0d line %0d px %0d got %h want %h", m, got, px, rgb, want); end
      px=px+1;
    end
    if (!de && de_d && checking==2) begin
      if (got>=0) begin
        if (px!=640) begin errs=errs+1; if (errs<12) $display("case %0d line %0d width %0d", m, got, px); end
        if (rgb!=={8'b0,slot[2:0],13'b0}) begin errs=errs+1; if (errs<12) $display("case %0d slot word %h want %0d", m, rgb, slot); end
      end
      got=got+1; px=0;
    end
  end
  task frame(input integer check);
    integer vb;
    begin
      vb=(pal ? 312 : 262)-nl;
      for (l=0;l<nl;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%div)==0; vblank=0; vsync=0; ilace=il;
        hblank=!(x>=300 && x<300+w*div);
        hsync=(x>=3100 && x<3350);
        rgb_in=0; rgb_in[19:10]=l[9:0]; rgb_in[9:0]=(x-300)/div;
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; hsync=(x>=3100 && x<3350);
        vsync=(l==2 && x>=3100) || l==3 || (l==4 && x<3100);
        if (l==1 && x==0 && check) checking=1;
        if (l==5) ilace=il && !ilace;   // 480i: the field flag toggles
      end
    end
  endtask
  initial begin
    ws[0]=256; ds[0]=10; ws[1]=320; ds[1]=8; ws[2]=368; ds[2]=7; ws[3]=384; ds[3]=7; ws[4]=512; ds[4]=5; ws[5]=640; ds[5]=4;
    // m<12: PAL 480i (288-line fields, 312 HS) - bob. m>=12: NTSC 480i (240, 262) - woven.
    for (m=0;m<24;m=m+1) begin
      w=ws[m%6]; div=ds[m%6]; pal=m/12==0; fill=m%2; nl=pal ? 288 : 240;
      // bob: Fit 640x288 slot 4 keeps all 288, Fill 640x268 slot 7 keeps 10..277.
      // woven: Fit 640x480 slot 3 keeps all 240, Fill 640x448 slot 1 keeps 8..231.
      slot=fill ? (pal ? 7 : 1) : (pal ? 4 : 3);
      t=fill ? (pal ? 10 : 8) : 0; keep=fill ? (pal ? 268 : 224) : nl;
      want_feat=pal ? 0 : 1; il=1; stretch=fill;
      for (f=0;f<3;f=f+1) frame(f==1);
      cases=cases+1;
    end
    $display("cases=%0d errs=%0d", cases, errs); $finish;
  end
endmodule
