// psx_apf_video_wide HYBRID+ASPECT+CROP_LINES: the Fit / Stretch setting.
// Each case sends three frames of `nl` lines at width w (interlace high in
// 480i cases) and checks the last frame: its slot word, output width,
// nearest-neighbour or native pixels ({line, x} tags) and, with more lines
// than the slot holds, the even top/bottom crop (first line out and count).
// S368: SLOT368, CE-7 Fit lines native in slot 6 (368x240), Stretch 480i in 3.
// D320 (with S368): DBL320, Fit 320 lines doubled in slot 0, Stretch 480i in 1.
// FD: FILL_DETECT, the fixed Fill crop: every Stretch case sends lines
// t..t+h-1 and no others (8..231 at 240 lines and per 480i field, 10..277
// at PAL 288), each once, in order (a frame shorter than the window ends
// early: PAL 256 lines, cases 7 and 15, and NTSC 224, case 28). Cases
// 20-30 add black bands (cb lines at the top and the bottom, none inside
// the window) that make no difference: bands of 8 (20), 10 at
// PAL (21), 2 (23), none (24, 26), after all-black frames (25), for one
// frame only (27), a 480i field (29) and at the top only (30); Fit (22)
// is unchanged with the band lines black.
// OB: OVERSCAN_BLANK (with FD), hide_overscan on in even cases and off in
// odd ones: with it on, Fit lines outside that window are black too;
// Stretch, and every case with it off, is as without OB.
// Every case also checks each line's source line in order.
module tb_apf_aspect #(parameter integer PT=1, parameter integer S368=0, parameter integer D320=0, parameter integer FD=0, parameter integer OB=0);
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, ilace=0, stretch=0; reg [23:0] rgb_in=0; reg hide=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.PAL288(1),.HYBRID(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(PT),.SLOT368(S368),.DBL320(D320),.FILL_DETECT(FD),.OVERSCAN_BLANK(OB)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(ilace),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(stretch),.hide_overscan(hide),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer m, f, l, x, errs=0, px=0, w, div, ow, lead, slot, nl, il, st, first, keep, got_lines, got_first, checking=0, line_tag;
  integer cw[0:30], cd[0:30], cnl[0:30], cil[0:30], cst[0:30], cow[0:30], cslot[0:30], cfirst[0:30], ckeep[0:30], cdbl[0:30], cvb[0:30], dbl, crop, vb, ncrop, n;
  integer ct, ch, cl, ccb[0:30], ccbb[0:30], cwarm[0:30], cblk[0:30], cb=0, cbb=0, et, allblack=0;
  reg de_d=0, bl=0; reg [23:0] want;
  always @(posedge clk) begin
    de_d<=de;
    if (de && checking && !skip) begin
      if (px==0) begin et=cfirst[m]+got_lines; bl=((cb>0 || cbb>0) && (et<cb || et>=nl-cbb) || OB!=0 && hide && (et<ct || et>=ct+ch)); end
      if (px==lead) begin line_tag=rgb[19:10]; if (got_lines==0) got_first=bl ? et : line_tag; end
      if (bl) want=24'h0;
      else if (ow!=640) begin
        if (px<lead || px>=lead+w-2*ncrop) want=24'h0; else begin want=0; want[19:10]=line_tag; want[9:0]=ncrop+px-lead; end
      end else if (dbl) begin
        // 320-class line at 640: each pixel twice, centred (lead) or cropped
        if (px<lead || px>=lead+2*w) want=24'h0; else begin want=0; want[19:10]=line_tag; want[9:0]=crop+(px-lead)/2; end
      end else begin want=0; want[19:10]=line_tag; want[9:0]=(px*w)/640; end
      if (rgb!==want) begin errs=errs+1; if (errs<10) $display("case %0d px %0d got %h want %h", m, px, rgb, want); end
      px=px+1;
    end
    if (!de && de_d && checking) begin
      if (px!=0) begin
        if (px!=ow) begin errs=errs+1; $display("case %0d line width %0d want %0d", m, px, ow); end
        if (rgb!=={8'b0,slot[2:0],13'b0}) begin errs=errs+1; $display("case %0d slot word %h want %0d", m, rgb, slot); end
        if (!bl) begin
          if (line_tag!=et) begin
            errs=errs+1; if (errs<10) $display("case %0d line %0d source %0d want %0d", m, got_lines, line_tag, et);
          end
        end
        got_lines=got_lines+1;
      end
      px=0;
    end
  end
  task frame(input integer lines, input integer check);
    begin
      for (l=0;l<lines;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=(x%div)==0; vblank=0; vsync=0; ilace=il;
        hblank=!(x>=300 && x<300+w*div);
        hsync=(x>=3100 && x<3350);
        rgb_in=0; rgb_in[19:10]=l[9:0]; rgb_in[9:0]=(x-300)/div;
        if (allblack || l<cb || l>=lines-cbb) rgb_in=0;
      end
      // blank lines: the frame totals 262 HS (NTSC) or 312 (PAL)
      for (l=0;l<vb;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; ilace=0; hsync=(x>=3100 && x<3350); vsync=(l==2);
        if (l==1 && x==3412) checking=check;
      end
    end
  endtask
  initial begin
    // w, div, lines, 480i, stretch -> out width, slot, first line, lines kept
    cw[0]=320; cd[0]=8;  cnl[0]=240; cil[0]=0; cst[0]=0; cow[0]=320; cslot[0]=1; cfirst[0]=0; ckeep[0]=240;
    cw[1]=320; cd[1]=8;  cnl[1]=240; cil[1]=0; cst[1]=1; cow[1]=640; cslot[1]=5; cfirst[1]=0; ckeep[1]=240;
    cw[2]=256; cd[2]=10; cnl[2]=240; cil[2]=0; cst[2]=0; cow[2]=256; cslot[2]=2; cfirst[2]=0; ckeep[2]=240;
    cw[3]=256; cd[3]=10; cnl[3]=240; cil[3]=0; cst[3]=1; cow[3]=640; cslot[3]=5; cfirst[3]=0; ckeep[3]=240;
    cw[4]=512; cd[4]=5;  cnl[4]=240; cil[4]=0; cst[4]=0; cow[4]=640; cslot[4]=0; cfirst[4]=0; ckeep[4]=240;
    cw[5]=368; cd[5]=7;  cnl[5]=240; cil[5]=0; cst[5]=0; cow[5]=640; cslot[5]=0; cfirst[5]=0; ckeep[5]=240;
    cw[6]=320; cd[6]=8;  cnl[6]=256; cil[6]=0; cst[6]=0; cow[6]=640; cslot[6]=4; cfirst[6]=0; ckeep[6]=256;
    cw[7]=320; cd[7]=8;  cnl[7]=256; cil[7]=0; cst[7]=1; cow[7]=640; cslot[7]=7; cfirst[7]=0; ckeep[7]=256;
    cw[8]=384; cd[8]=7;  cnl[8]=296; cil[8]=0; cst[8]=0; cow[8]=640; cslot[8]=4; cfirst[8]=4; ckeep[8]=288;
    cw[9]=640; cd[9]=4;  cnl[9]=240; cil[9]=1; cst[9]=0; cow[9]=640; cslot[9]=3; cfirst[9]=0; ckeep[9]=240;
    cw[10]=640; cd[10]=4; cnl[10]=288; cil[10]=1; cst[10]=1; cow[10]=640; cslot[10]=6; cfirst[10]=24; ckeep[10]=240;
    cw[11]=320; cd[11]=8; cnl[11]=242; cil[11]=0; cst[11]=0; cow[11]=320; cslot[11]=1; cfirst[11]=1; ckeep[11]=240;
    // Stretch / PAL lines near 320: doubled, centred or cropped
    cw[12]=330; cd[12]=7; cnl[12]=240; cil[12]=0; cst[12]=1; cow[12]=640; cslot[12]=5; cfirst[12]=0; ckeep[12]=240;
    cw[13]=310; cd[13]=8; cnl[13]=240; cil[13]=0; cst[13]=1; cow[13]=640; cslot[13]=5; cfirst[13]=0; ckeep[13]=240;
    cw[14]=310; cd[14]=8; cnl[14]=256; cil[14]=0; cst[14]=0; cow[14]=640; cslot[14]=4; cfirst[14]=0; ckeep[14]=256;
    cw[15]=344; cd[15]=7; cnl[15]=256; cil[15]=0; cst[15]=1; cow[15]=640; cslot[15]=7; cfirst[15]=0; ckeep[15]=256;
    // NTSC frames with a few more than 242 lines stay at 240 lines, cropped
    cw[16]=320; cd[16]=8; cnl[16]=250; cil[16]=0; cst[16]=0; cow[16]=320; cslot[16]=1; cfirst[16]=5; ckeep[16]=240;
    cw[17]=330; cd[17]=7; cnl[17]=250; cil[17]=0; cst[17]=1; cow[17]=640; cslot[17]=5; cfirst[17]=5; ckeep[17]=240;
    // SLOT368: 368-clock lines in Fit go native to slot 6, padded or cropped
    if (S368!=0) begin cow[5]=368; cslot[5]=6; cslot[10]=3; end
    cw[18]=384; cd[18]=7; cnl[18]=240; cil[18]=0; cst[18]=0; cow[18]=368; cslot[18]=6; cfirst[18]=0; ckeep[18]=240;
    cw[19]=340; cd[19]=7; cnl[19]=240; cil[19]=0; cst[19]=0; cow[19]=368; cslot[19]=6; cfirst[19]=0; ckeep[19]=240;
    // FILL_DETECT: band, frames before the checked one, all-black frames
    for (m=0;m<31;m=m+1) begin ccb[m]=0; ccbb[m]=-1; cwarm[m]=1; cblk[m]=0; end
    for (m=20;m<31;m=m+1) begin cw[m]=320; cd[m]=8; cnl[m]=240; cil[m]=0; cst[m]=1; cow[m]=640; cslot[m]=5; cfirst[m]=0; ckeep[m]=240; ccb[m]=8; end
    cnl[21]=288; ccb[21]=10; cslot[21]=7; ckeep[21]=288;                     // PAL band of 10
    cst[22]=0; cow[22]=320; cslot[22]=1;                                      // Fit: unchanged
    ccb[23]=2;                                                                // band of 2
    ccb[24]=0;                                                                // no band
    cblk[25]=2;                                                               // after all-black frames
    ccb[26]=0;                                                                // no band
    cwarm[27]=0;                                                              // band seen for one frame only
    cnl[28]=224; ccb[28]=0;                                                   // 224 lines: ends early
    cw[29]=640; cd[29]=4; cil[29]=1; cslot[29]=6;                             // 480i field band
    ccbb[30]=0;                                                               // black at the top only (Tekken 3, Ape Escape EU)
    for (m=0;m<31;m=m+1) if (ccbb[m]<0) ccbb[m]=ccb[m];
    n=FD!=0 ? 31 : S368!=0 ? 20 : 18;
    for (m=0;m<n;m=m+1) begin
      cdbl[m]=(m==1 || m==6 || m==7 || (m>=12 && m!=16 && m<18) || (m>=20 && m!=22 && m!=29));
      cvb[m]=(m==6 || m==7 || m==8 || m==10 || m==14 || m==15 || m==21) ? 312-cnl[m] : 262-cnl[m];
      // the Fill window: t, h and the slot height (PAL frames, not 480i)
      cl=(cvb[m]+cnl[m]==312 && cnl[m]>242 && !cil[m]) ? 288 : 240;
      ct=cl==288 ? 10 : 8; ch=cl==288 ? 268 : 224;
      if (FD!=0 && cst[m]) begin cfirst[m]=ct; ckeep[m]=(cnl[m]<ct+ch ? cnl[m] : ct+ch)-ct; end
    end
    if (D320!=0) begin
      cslot[10]=1;
      for (m=0;m<n;m=m+1) if (m==0 || m==11 || m==16) begin cow[m]=640; cslot[m]=0; cdbl[m]=1; end
    end
    for (m=0;m<n;m=m+1) if (S368!=0 || m<18 || m>=20) begin
      w=cw[m]; div=cd[m]; nl=cnl[m]; il=cil[m]; st=cst[m]; ow=cow[m]; slot=cslot[m];
      vb=cvb[m]; dbl=cdbl[m]; crop=dbl && w>320 ? (w-320)/2 : 0;
      lead=dbl ? (w>320 ? 0 : 320-w) : ow!=640 && ow>w ? (ow-w)/2 : 0; ncrop=ow!=640 && w>ow ? (w-ow)/2 : 0; stretch=st; got_lines=0; got_first=-1;
      cb=ccb[m]; cbb=ccbb[m]; hide=m%2==0;
      cl=(vb+nl==312 && nl>242 && !il) ? 288 : 240; ct=cl==288 ? 10 : 8; ch=cl==288 ? 268 : 224;
      for (f=0;f<cwarm[m];f=f+1) frame(nl,0);
      allblack=1; for (f=0;f<cblk[m];f=f+1) frame(nl,0); allblack=0;
      frame(nl,1); frame(nl,0);
      checking=0;
      // the checked frame's last line is read out in the vblank after it
      if (got_lines!=ckeep[m]) begin errs=errs+1; $display("case %0d lines out %0d want %0d", m, got_lines, ckeep[m]); end
      if (got_first!=cfirst[m]) begin errs=errs+1; $display("case %0d first line %0d want %0d", m, got_first, cfirst[m]); end
    end
    $display("errs=%0d", errs); $finish;
  end
endmodule
