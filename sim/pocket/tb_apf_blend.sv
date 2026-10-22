// psx_apf_video_wide BLEND with the probe recipe's video parameters: NTSC Fit
// lines of 368 pixels (CE every 7 clocks) and 256 (every 10). Each source pixel
// has a distinct colour. With wide_even, 368-wide lines go to slot 2 at 800
// pixels; without it they stay native in slot 6 at 368; 256-wide lines always go
// to slot 2 at 800. Every blended output pixel must equal the reference: the
// output pixel's span over the nominal width, the source pixel(s) it covers and
// their mix in eighths (sharp bilinear); pixels outside a short line are black.
module tb_apf_blend;
  parameter integer FB=0;   // FILL_BLEND: Fill lines of any width blended, doubled, slot 1 (1: 800 wide, 2: 640 in panel columns)
  parameter integer PH=0;
  parameter integer PC=0;   // PACE: only DE pixels without skip count, and they must be the same   // FB=2: output pixel 0 is 3 panel pixels wide
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, even=0; reg [23:0] rgb_in=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video_wide #(.INTERLACE_FEATURE(1),.FIELD_INVERT(1),.PAL288(1),.HYBRID(1),.LEAD_LINE(2),.VS_ON_HS(1),.ASPECT(1),.CROP_LINES(1),.PAL_TIMING(1),.SLOT368(1),.DBL320(1),.FILL_DETECT(1),.OVERSCAN_BLANK(1),.FILL_CENTER(1),.EVEN_LINES(1),.WEAVE576(1),.PAL_SHAPE(1),.BLEND(FB ? 0 : 1),.FILL_BLEND(FB),.PACE(PC)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),
    .blank(1'b0),.weave(1'b1),.field_swap(1'b0),.stretch(FB ? 1'b1 : 1'b0),.hide_overscan(1'b0),.dock_mode(2'b0),.wide_even(even),.fill_phase(PH!=0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer l, x, m, errs=0, counting=0, arm=0, armed=0, nl=0, w=0, wl=0, cep=0, slotw=-1, i, px, bad=0, lastslot=-1, lines_bad=0;
  reg de_d=0;
  reg [23:0] line_px [0:1023];
  reg [23:0] prev_px [0:1023];
  integer copy_bad=0, order_bad=0, prev_line=0;
  always @(negedge clk) if (vs) begin
    if (arm && counting==0 && armed==0) begin counting=1; armed=1; end
    else if (counting==1) counting=0;
  end
  always @(negedge clk) if (!arm) armed=0;
  // source pixel k of the line (k counted in buffer order) has colour {k, k^8'h55, 8'h80|k[3:0]}
  // in Fill (FB) the blue channel's top bits carry the source line, to tell a copy that read the wrong line
  integer cur_l=0;
  function [23:0] src(input integer k); src={k[7:0], k[7:0]^8'h55, FB ? {cur_l[3:0], k[3:0]} : 8'h80|k[3:0]}; endfunction
  function [7:0] mixr(input [7:0] a, input [7:0] b, input integer t); mixr=(a*(8-t)+b*t)>>3; endfunction
  function [23:0] nom(input integer j);   // nominal pixel j: centred line, black outside
    integer off, k; begin off=(w-wl)/2; k=j-off; nom=(k>=0 && k<wl) ? src(k) : 24'h0; end endfunction
  // panel column where output pixel o starts (FB=2): 640 -> 1600 is 2.5 a pixel,
  // widths 2,3,2,3 (phase A) or 3,2,3,2 (phase B)
  function integer pcol(input integer o); pcol=PH ? (5*o+1)/2 : (5*o)/2; endfunction
  function [23:0] ref_px(input integer o);   // reference blend for output pixel o
    integer left, right, j, part, t, unit, span; reg [23:0] a, b;
    begin
      if (FB==2) begin unit=1600; left=pcol(o)*w; right=pcol(o+1)*w; end
      else begin unit=800; left=o*w; right=left+w; end
      span=right-left; j=left/unit; a=nom(j); b=nom(j+1);
      if (right<=(j+1)*unit) ref_px=a;
      else begin
        part=right-(j+1)*unit;
        t=(16*part+span)/(2*span);   // round(8*part/span), halves up
        ref_px={mixr(a[23:16],b[23:16],t), mixr(a[15:8],b[15:8],t), mixr(a[7:0],b[7:0],t)};
      end
    end endfunction
  always @(negedge clk) begin
    de_d<=de;
    if (counting) begin
      if (de && !de_d) px=0;
      if (de && !skip) begin line_px[px]=rgb; px=px+1; end
      if (!de && de_d) begin
        nl=nl+1;
        if (px!==slotw) begin lines_bad=lines_bad+1; if (lines_bad<3) $display("case %0d: line %0d has %0d pixels, want %0d", m, nl, px, slotw); end
        else if (slotw==800 || FB==2 && slotw==640) for (i=0;i<slotw;i=i+1) if ((line_px[i]&24'hFFFF0F)!==(ref_px(i)&24'hFFFF0F)) begin
          bad=bad+1; if (bad<4) $display("case %0d line %0d px %0d: %h want %h", m, nl, i, line_px[i], ref_px(i)); end
      end
      if (!de && de_d && rgb[23:16]==0 && rgb[1:0]==2'b00) lastslot=rgb[15:13];
      // FB: each line twice (the copy equals it), and pairs in line order
      if (FB && !de && de_d && px==slotw) begin
        if (nl%2==0) begin
          for (i=0;i<slotw;i=i+1) if (line_px[i]!==prev_px[i]) copy_bad=copy_bad+1;
          if (nl>2 && line_px[400][7:4]!==((prev_line+1)&15)) order_bad=order_bad+1;
          prev_line=line_px[400][7:4];
        end
        for (i=0;i<slotw;i=i+1) prev_px[i]=line_px[i];
      end
    end
  end
  task frame;
    begin
      for (l=0;l<240;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk);
        ce=((x-300)%cep)==0; vblank=0; vsync=0;
        hblank=!(x>=300 && x<300+wl*cep); hsync=FB ? (x>=1100 && x<1350) : (x>=3100 && x<3350);
        cur_l=l; rgb_in=src((x-300)/cep);
      end
      for (l=0;l<22;l=l+1) for (x=0;x<3413;x=x+1) begin
        @(negedge clk); hblank=1; vblank=1; hsync=FB ? (x>=1100 && x<1350) : (x>=3100 && x<3350); vsync=(l==2);
      end
    end
  endtask
  integer want_n;
  task check(input integer cw, input integer cwl, input integer ccep, input integer ceven, input integer want_slot, input integer want_w);
    begin
      w=cw; wl=cwl; cep=ccep; even=ceven; slotw=want_w; bad=0; lines_bad=0; copy_bad=0; order_bad=0;
      frame; frame;
      nl=0; arm=1; frame; frame; arm=0;
      $display("case %0d: %0d lines, slot %0d, %0d bad lines, %0d bad pixels, %0d copy pixels differing, %0d pairs out of order", m, nl, lastslot, lines_bad, bad, copy_bad, order_bad);
      if (copy_bad || order_bad) errs=errs+1;
      if (lastslot!==want_slot || lines_bad || bad || nl!==(FB ? 448 : 240)) errs=errs+1;
    end
  endtask
  initial begin
    if (FB) begin
      // Fill: every width blended at 800 over its own width, each line twice, slot 1
      m=10; check(320, 320, 8, 0, 1, FB==2 ? 640 : 800);
      m=11; check(368, 368, 7, 0, 1, FB==2 ? 640 : 800);
      m=12; check(256, 256, 10, 0, 1, FB==2 ? 640 : 800);
      m=13; check(640, 640, 4, 0, 1, FB==2 ? 640 : 800);
      $display("errs=%0d", errs);
      $finish;
    end
    // 0: 368-wide, Sharp: native slot 6, 368 pixels
    m=0; check(368, 368, 7, 0, 6, 368);
    // 1: 368-wide, Even: slot 2, 800 blended pixels
    m=1; check(368, 368, 7, 1, 2, 800);
    // 2: 256-wide (either setting): slot 2, 800 blended
    m=2; check(256, 256, 10, 0, 2, 800);
    // 3: a 250-wide line (within 1/16 of 256): centred with black, blended
    m=3; check(256, 250, 10, 1, 2, 800);
    $display("errs=%0d", errs);
    $finish;
  end
endmodule
