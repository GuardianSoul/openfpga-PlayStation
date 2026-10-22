// psx_apf_video_wide WEAVE576 + EVEN_LINES + LEAD_LINE 2 with the 0.5.5
// recipe's video parameters. Per case, over one steady output frame (VS to VS):
// DE lines (even, the slot's lines plus the lead and pad lines of 480i), the
// end-of-line slot word, the APF 480i frame feature word at VS (480i only), and
// the source line each DE line carries (G channel): progressive Fill sends every
// kept line twice in order, everything else once.
module tb_apf_w576;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.EVEN_LINES(1),.WEAVE576(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer l, x, m, errs=0, cases=0, counting=0, nde=0, nfeat=0, nhs=0, lastslot=-1, oix=0, bad=0, k;
  integer want_de, want_slot, want_feat, want_first, want_last, want_rep;
  integer arm=0, armed_vs=0;
  always @(negedge clk) if (vs) begin
    if (arm && counting==0 && armed_vs==0) begin counting=1; armed_vs=1; if (1) begin end end
    else if (counting==1) begin counting=0; end
  end
  always @(negedge clk) if (!arm) armed_vs=0;
  reg de_d=0, rd_line=0; integer seq[0:1023];
  // count DE lines, HS pulses and feature words between two output VS; record each line's source index
  always @(negedge clk) begin
    de_d<=de;
    if (counting==1) begin
      if (hs) nhs=nhs+1;
      if (de && !de_d) begin oix=0; end
      if (de) begin oix=oix+1; if (oix==320) begin seq[nde]=rgb[15:8]; nde=nde+1; end end
      if (!de && de_d) begin if (oix<320) begin seq[nde]=-1; nde=nde+1; end end
      if (!de && de_d && rgb[23:16]==0 && rgb[1:0]==2'b00) lastslot=rgb[15:13];
      if (vs) begin end
    end
  end
  // the feature word sits on rgb the clock after vs, with de low
  always @(negedge clk) if (counting==1 && !de && rgb[1:0]==2'b10 && rgb[23:3]==0) nfeat=nfeat+1;
  task frame(input integer nl, input integer vb, input integer il, input integer odd);
    begin
      for (l=0;l<nl;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%4)==0; vblank=0; vsync=0; ilace=il && odd;
        hblank=!(x>=300 && x<300+640*4); hsync=(x>=3100 && x<3350);
        rgb_in={8'h40, l[7:0], 8'h40};
      end
      for (l=0;l<vb;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; hsync=(x>=3100 && x<3350); vsync=(l==2);
      end
    end
  endtask
  // run frames until steady, then count one VS-to-VS period
  task check(input integer nl, input integer vb, input integer il);
    begin
      frame(nl,vb,il,0); frame(nl,vb,il,1);
      // count from the VS in the next frame's vblank to the one after it
      nde=0; nfeat=0; nhs=0; arm=1; frame(nl,vb,il,0); frame(nl,vb,il,1); arm=0; cases=cases+1;
      bad=0;
      for (k=0;k<nde;k=k+1) if (seq[k]>=0) begin
        if (want_rep && seq[k]!==(want_first+k/2)%256) bad=bad+1;   // G holds the line number mod 256
      end
      if (nde!==want_de || nde%2!=0) begin errs=errs+1; $display("case %0d DE lines %0d want %0d", m, nde, want_de); end
      if (lastslot!==want_slot) begin errs=errs+1; $display("case %0d slot %0d want %0d", m, lastslot, want_slot); end
      if ((nfeat>0)!==(want_feat!=0)) begin errs=errs+1; $display("case %0d feature words %0d want %0d", m, nfeat, want_feat); end
      if (bad) begin errs=errs+1; $display("case %0d %0d lines out of order (first %0d %0d %0d %0d)", m, bad, seq[0], seq[1], seq[2], seq[3]); end
      $display("case %0d: %0d DE lines, %0d HS, slot %0d, feature %0d, lines %0d %0d %0d %0d .. %0d", m, nde, nhs, lastslot, nfeat, seq[0], seq[1], seq[2], seq[3], seq[nde-1]);
    end
  endtask
  initial begin
    // 0: NTSC progressive Fit: 240 lines, no lead line, slot 0
    m=0; stretch=0; want_de=240; want_slot=0; want_feat=0; want_rep=0; check(240,22,0);
    // 1: NTSC progressive Fill: lines 8..231 twice: 448 in slot 1
    m=1; stretch=1; want_de=448; want_slot=1; want_feat=0; want_rep=1; want_first=8; check(240,22,0);
    // 2: PAL progressive Fit: 288 in slot 4
    m=2; stretch=0; want_de=288; want_slot=4; want_feat=0; want_rep=0; check(288,26,0);
    // 3: PAL progressive Fill: lines 10..277 twice: 536 in slot 7
    m=3; stretch=1; want_de=536; want_slot=7; want_feat=0; want_rep=1; want_first=10; check(288,26,0);
    // 4: PAL 480i Fit, woven: lead + 288 + pad = 290 a field, slot 5, feature word
    m=4; stretch=0; want_de=290; want_slot=5; want_feat=1; want_rep=0; check(288,24,1);
    // 5: PAL 480i Fill, woven: lead + 268 + pad = 270, slot 7
    m=5; stretch=1; want_de=270; want_slot=7; want_feat=1; want_rep=0; check(288,24,1);
    // 6: NTSC 480i Fit: lead + 240 + pad = 242, slot 3
    m=6; stretch=0; want_de=242; want_slot=3; want_feat=1; want_rep=0; check(240,22,1);
    // 7: NTSC 480i Fill: lead + 224 + pad = 226, slot 1
    m=7; stretch=1; want_de=226; want_slot=1; want_feat=1; want_rep=0; check(240,22,1);
    $display("cases=%0d errs=%0d", cases, errs);
    $finish;
  end
endmodule
