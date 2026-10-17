// MID_W=384: 240-line pictures 384 wide (Ape Escape PAL) select slot 3 and
// fill it; 368-wide ones select slot 3 too and are padded to 384. With the
// old MID_W=368 (M=368) a 384-wide line selects slot 4 and is padded to 512.
module tb_apf_mid384;
  parameter integer W=384, M=384;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1),.FIELD_INVERT(1),.PAD_LINES(1),.PAL288(1),.CENTER_LINES(1),.LEAD_LINE(1),.SLOT6_368(1),.VS_ON_HS(1),.MID_W(M)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b0),.field_swap(1'b0),.video_test(2'b00),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, px=0, want_px, want_slot;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) begin lines=0; frames=frames+1; end
    if (de && !skip) px=px+1;
    if (!de && de_d) begin
      if (frames>3 && lines>0) begin
        if (px!=want_px) begin errs=errs+1; if (errs<5) $display("W %0d M %0d line %0d px %0d want %0d", W, M, lines, px, want_px); end
        if (rgb[15:13]!=want_slot) begin errs=errs+1; if (errs<5) $display("W %0d M %0d slot %0d want %0d", W, M, rgb[15:13], want_slot); end
      end
      lines=lines+1; px=0;
    end
  end
  initial begin
    want_px = W<=M ? M : 512; want_slot = W<=M ? 3 : 4;
    for (f=0;f<6;f=f+1)
      for (l=0;l<250;l=l+1) for (x=0;x<1600;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5) || (l>=245); hblank=vblank || x<300 || x>=300+2*W; hsync=(x<20);
        vsync=(l==2 && x>=300 && x<340);
      end
    @(negedge clk); $display("errs=%0d", errs); $finish;
  end
endmodule
