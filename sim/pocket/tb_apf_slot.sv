// Lines of 256/320/368/512/640 CE pixels: the first non-DE clock after each
// line carries the APF end-of-line word {slot, 10'b0, 3'b000} (slot 0, 640,
// is all-zero); every other non-DE clock is zero.
module tb_apf_slot;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=0; reg [23:0] rgb_in=24'h123456;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(1'b0),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer w, l, x, errs=0, checked=0, exp_slot=-1, widths[0:4], slots[0:4];
  reg de_d=0;
  always @(posedge clk) begin
    de_d<=de;
    if (!de && rgb!==0) begin
      if (!de_d || exp_slot<0 || rgb!=={8'b0,exp_slot[2:0],13'b0}) begin errs=errs+1; $display("rgb=%0h de_d=%0d exp_slot=%0d",rgb,de_d,exp_slot); end
      else checked=checked+1;
    end
  end
  initial begin
    widths[0]=256; widths[1]=320; widths[2]=368; widths[3]=512; widths[4]=640;
    slots[0]=2; slots[1]=1; slots[2]=3; slots[3]=4; slots[4]=0;
    for (w=0;w<5;w=w+1) for (l=0;l<3;l=l+1) begin
      for (x=0;x<widths[w]*2+40;x=x+1) begin
        @(negedge clk); ce=x%2; hblank=(x>=widths[w]*2); hsync=(x>=widths[w]*2+10 && x<widths[w]*2+20);
        if (x==widths[w]*2-4) exp_slot=slots[w];
      end
    end
    @(negedge clk);
    if (checked != 4*3) begin errs=errs+1; $display("checked=%0d",checked); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
