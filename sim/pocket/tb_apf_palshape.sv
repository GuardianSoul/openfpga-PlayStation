// psx_apf_video_wide PAL_SHAPE (with the 0.5.5 recipe's video parameters,
// WEAVE576, EVEN_LINES, FILL_CENTER): the picture is lines py0..py1 on black;
// nine black frames before each case reset the merged picture lines. Per case,
// one steady output frame (VS to VS): DE lines, slot word, the first and last
// source line sent (G channel, 0 for a black line) and the black lines before
// and after them.
module tb_apf_palshape;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.FILL_CENTER(1),.EVEN_LINES(1),.WEAVE576(1),.PAL_SHAPE(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(1'b0),.dock_mode(2'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer l, x, m, errs=0, counting=0, arm=0, armed=0, nde=0, lastslot=-1, oix=0, py0=0, py1=0, lit=1;
  integer seq[0:1023];
  reg de_d=0, line_lit=0;
  always @(negedge clk) if (vs) begin
    if (arm && counting==0 && armed==0) begin counting=1; armed=1; end
    else if (counting==1) counting=0;
  end
  always @(negedge clk) if (!arm) armed=0;
  // a line's source index is its G byte (+1, so a black line reads 0)
  always @(negedge clk) begin
    de_d<=de;
    if (counting==1) begin
      if (de && !de_d) begin oix=0; line_lit=0; end
      if (de) begin oix=oix+1; if (oix==320) begin seq[nde]=rgb[23:16]==8'hC0 ? rgb[15:8]+1 : 0; nde=nde+1; end end
      if (!de && de_d && rgb[23:16]==0 && rgb[1:0]==2'b00) lastslot=rgb[15:13];
    end
  end
  task frame(input integer il, input integer odd);
    begin
      for (l=0;l<288;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%4)==0; vblank=0; vsync=0; ilace=il && odd;
        hblank=!(x>=300 && x<300+640*4); hsync=(x>=3100 && x<3350);
        rgb_in=lit && l>=py0 && l<=py1 ? {8'hC0, l[7:0], 8'h40} : 24'h0;
      end
      for (l=0;l<24;l=l+1) for (x=0;x<3406;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; hsync=(x>=3100 && x<3350); vsync=(l==5);   // PAL: vsync about 5 lines after the 19..306 window
      end
    end
  endtask
  integer k, first, last, pre, post, s;
  integer want_last;
  task check(input integer il, input integer want_de, input integer want_slot, input integer want_first, input integer want_last_in, input integer want_pre, input integer want_post);
    begin
      want_last=want_last_in;
      lit=0; repeat (9) frame(il, 0);   // reset the merged picture lines
      lit=1; frame(il,0); frame(il,1); frame(il,0); frame(il,1);
      nde=0; arm=1; frame(il,0); frame(il,1); arm=0;
      first=-1; last=-1; pre=0; post=0; want_last=want_last%256;   // G holds the line number mod 256
      for (k=0;k<nde;k=k+1) if (seq[k]!=0) begin if (first<0) first=seq[k]-1; last=seq[k]-1; end
      for (k=0;k<nde && seq[k]==0;k=k+1) pre=pre+1;
      for (k=nde-1;k>=0 && seq[k]==0;k=k-1) post=post+1;
      $display("case %0d: %0d DE lines, slot %0d, source %0d..%0d, %0d black before, %0d after", m, nde, lastslot, first, last, pre, post);
      if (nde!==want_de || lastslot!==want_slot || first!==want_first || last!==want_last || pre!==want_pre || post!==want_post) begin
        errs=errs+1; $display("case %0d wants %0d DE lines, slot %0d, source %0d..%0d, %0d / %0d black", m, want_de, want_slot, want_first, want_last, want_pre, want_post); end
    end
  endtask
  initial begin
    // A: PAL Fit, Ape Escape's picture 20..275 (4 lines low): lines 4..287 then 4 black lines, slot 4
    m=0; stretch=0; py0=20; py1=275; check(0, 288, 4, 20, 275, 16, 16);
    // B: PAL Fit, a 240-line picture 40..279: cropped to 40..279, NTSC slot 0
    m=1; py0=40; py1=279; check(0, 240, 0, 40, 279, 0, 0);
    // C: PAL 480i Fit, the BIOS picture 24..262: 23..262 a field, lead + 240 + pad, NTSC 480i slot 3
    m=2; py0=24; py1=262; check(1, 242, 3, 24, 262, 2, 1);
    // D: PAL Fill, 20..275: the 256-line window 20..275, twice: 512 in slot 7
    m=3; stretch=1; py0=20; py1=275; check(0, 512, 7, 20, 275, 0, 0);
    // E: PAL Fit, a picture drawn high, 4..259: 12 black lines, then lines 0..275, slot 4
    m=4; stretch=0; py0=4; py1=259; check(0, 288, 4, 4, 259, 16, 16);
    // F: PAL Fill, a small menu 80..192 alone: the window stays at 16..271
    m=5; stretch=1; py0=80; py1=192; check(0, 512, 7, 80, 192, 2*64, 2*79);
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
