// PAL288 with SLOT480: 288-line frames use slot 7 (320x288) for 300-pixel
// lines and the 240 slot 4 (512) for 400-pixel lines, from the frame after the
// first PAL frame; 240-line frames keep the 240 slots (1 and 4). Lines are
// padded to the slot width.
module tb_apf_pal288;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.SLOT480(1),.INTERLACE_FEATURE(1),.PAD_LINES(1),.VS_AT_VBLANK(1),.PAL288(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, frame=0, px=0, exp_slot, exp_w, lines, width, checked=0;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (vs) frame=frame+1;
    if (de && !skip) px=px+1;
    if (!de && de_d) begin
      if (frame%3!=0) begin   // the first frame of each mode still uses the previous mode's line count
        if (rgb[15:13]!==exp_slot[2:0] || px!=exp_w) begin errs=errs+1; if (errs<10) $display("frame %0d slot %0d px %0d want %0d/%0d", frame, rgb[15:13], px, exp_slot, exp_w); end
        else checked=checked+1;
      end
      px=0;
    end
  end
  task run_frames(input integer n_lines, input integer w, input integer s, input integer sw);
    integer k;
    begin
      for (k=0;k<3;k=k+1) begin
        exp_slot=s; exp_w=sw;
        for (l=0;l<n_lines+5;l=l+1) for (x=0;x<1400;x=x+1) begin
          @(negedge clk); ce=x%2; vblank=(l>=n_lines); hblank=(l>=n_lines) || x>=2*w; hsync=(x>=1380 && x<1390);
        end
      end
    end
  endtask
  initial begin
    run_frames(240, 300, 1, 320);
    run_frames(288, 300, 7, 320);
    run_frames(288, 400, 4, 512);
    run_frames(240, 400, 4, 512);
    @(negedge clk); @(negedge clk);
    if (checked<1000) begin errs=errs+1; $display("checked=%0d", checked); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
