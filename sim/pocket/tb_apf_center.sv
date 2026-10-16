// CENTER_LINES: 300- and 470-pixel lines (320 and 512 slots) with a
// 50-pixel back porch. From the third line on, each line is sent as
// black / picture / black with the padding split evenly (10/300/10 and
// 21/470/21), the slot word is still the line's own slot, and a 250-pixel
// line gets as many leading pixels as its back porch has clocks.
module tb_apf_center;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.PAD_LINES(1),.CENTER_LINES(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer c, l, x, errs=0, line=0, lead=0, pic=0, trail=0, exp_lead, exp_pic, exp_trail, exp_slot, porch, w;
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (de && !skip) begin
      if (rgb==24'h123456) pic=pic+1; else if (pic==0) lead=lead+1; else trail=trail+1;
    end
    if (!de && de_d) begin
      if (line>=2 && (lead!=exp_lead || pic!=exp_pic || trail!=exp_trail || rgb[15:13]!==exp_slot[2:0])) begin
        errs=errs+1; $display("line %0d: %0d/%0d/%0d slot %0d want %0d/%0d/%0d slot %0d", line, lead, pic, trail, rgb[15:13], exp_lead, exp_pic, exp_trail, exp_slot);
      end
      line=line+1; lead=0; pic=0; trail=0;
    end
  end
  task lines(input integer width, input integer bp, input integer n, input integer el, input integer et, input integer es);
    integer k;
    begin
      line=0; exp_lead=el; exp_pic=width; exp_trail=et; exp_slot=es;
      for (k=0;k<n;k=k+1)
        for (x=0;x<20+2*bp+2*width+400;x=x+1) begin
          @(negedge clk); ce=x%2; hsync=(x<20); hblank=!(x>=20+2*bp && x<20+2*bp+2*width);
        end
    end
  endtask
  initial begin
    lines(300, 50, 6, 10, 10, 1);
    lines(470, 50, 6, 21, 21, 4);
    lines(250, 2, 6, 3, 3, 2);   // 4 clocks of back porch hold all 3 leading pixels (sent every clock)
    lines(250, 1, 6, 1, 5, 2);   // 2 clocks (one after HS ends) hold only 1
    @(negedge clk); @(negedge clk);
    $display("errs=%0d", errs); $finish;
  end
endmodule
