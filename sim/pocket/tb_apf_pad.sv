// PAD_LINES: lines of 250/300/330/400/600 CE pixels reach their slot width
// (256/320/368/512/640) in DE pixels (DE high, skip low), the padding is
// black, and the first non-DE clock carries the slot of the original width.
// A line whose padding would run into HS stops at HS.
module tb_apf_pad;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=0; reg [23:0] rgb_in=24'h123456;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.PAD_LINES(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer w, l, x, errs=0, px=0, black=0, words=0, exp_px, exp_slot, gap, widths[0:5], slotw[0:5], slots[0:5];
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (de && !skip) begin px=px+1; if (rgb==0) black=black+1; end
    if (!de && de_d) begin
      words=words+1;
      if (rgb!=={8'b0,exp_slot[2:0],13'b0}) begin errs=errs+1; $display("slot word %0h want %0d", rgb, exp_slot); end
      if (px!=exp_px) begin errs=errs+1; $display("width %0d want %0d", px, exp_px); end
      px=0; black=0;
    end
  end
  initial begin
    widths[0]=250; widths[1]=300; widths[2]=330; widths[3]=400; widths[4]=600; widths[5]=300;
    slotw[0]=256; slotw[1]=320; slotw[2]=368; slotw[3]=512; slotw[4]=640; slotw[5]=320;
    slots[0]=2; slots[1]=1; slots[2]=3; slots[3]=4; slots[4]=0; slots[5]=1;
    for (w=0;w<6;w=w+1) for (l=0;l<2;l=l+1) begin
      gap = w==5 ? 10 : 2*(slotw[w]-widths[w])+20;   // case 5: HS cuts the padding after 5 pixels
      exp_slot=slots[w]; exp_px = w==5 ? widths[w]+5 : slotw[w];
      for (x=0;x<widths[w]*2+gap+40;x=x+1) begin
        @(negedge clk); ce=x%2; hblank=(x>=widths[w]*2); hsync=(x>=widths[w]*2+gap && x<widths[w]*2+gap+10);
      end
    end
    @(negedge clk); @(negedge clk);
    if (words != 12) begin errs=errs+1; $display("words=%0d", words); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
