// VS_ON_HS: 480i fields whose vsync starts with HS (even field) or half a
// line later (odd field, as gpu_videoout_async without syncInterlace).
// With V=1 every VS leaves 4 clocks before an HS (APF wants HS at least 3
// clocks after VS) and none within 3 clocks after it, and the lead line
// starts the same number of clocks after VS in both fields; each field is
// the lead line plus its 236 picture lines. With V=0 (the old timing) the
// odd field's VS is mid-line, which the bench must see.
module tb_apf_vsonhs;
  parameter integer V=1;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, il=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1),.FIELD_INVERT(1),.PAD_LINES(1),.PAL288(1),.CENTER_LINES(1),.LEAD_LINE(1),.SLOT6_368(1),.VS_ON_HS(V)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(il),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.video_test(2'b00),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, lines=0, frames=0, offhs=0, since=0, lead_at[0:9];
  reg de_d=0, timing=0, hs_wait=0;
  always @(posedge clk) begin
    de_d<=de; since=since+1;
    if (hs && hs_wait) begin if (since!=4) offhs=offhs+1; hs_wait=0; end
    else if (hs_wait && since>4) begin offhs=offhs+1; hs_wait=0; end
    if (vs) begin
      hs_wait=1;
      if (frames>1 && lines!=237) begin errs=errs+1; $display("field %0d lines %0d want 237", frames, lines); end
      lines=0; frames=frames+1; since=0; timing=1;
    end
    if (de && !de_d && timing) begin if (frames<10) lead_at[frames]=since; timing=0; end
    if (!de && de_d) lines=lines+1;
  end
  initial begin
    for (f=0;f<8;f=f+1) begin
      il=f%2;
      for (l=0;l<241;l=l+1) for (x=0;x<800;x=x+1) begin
        @(negedge clk); ce=x%2; vblank=(l<5); hblank=(l<5) || x<120 || x>=760; hsync=(x<20);
        vsync=f%2 ? (l==2 && x>=400) || (l==3 && x<400) : (l==2 && x<40);
      end
    end
    @(negedge clk); @(negedge clk);
    if (V!=0) begin
      if (offhs!=0) begin errs=errs+1; $display("%0d VS not 4 clocks before HS", offhs); end
      for (f=3;f<8;f=f+1) if (lead_at[f]!=lead_at[2]) begin errs=errs+1; $display("field %0d lead at %0d, field 2 at %0d", f, lead_at[f], lead_at[2]); end
    end else if (offhs==0) begin errs=errs+1; $display("V=0: odd-field VS should be mid-line"); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
