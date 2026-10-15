// 4 progressive frames (interlace low): VS carries RGB 0. Then 6 interlaced
// fields with the GPU field bit alternating 1,0,1,...: each following VS
// carries {odd = !previous field, interlaced = 1, 0}. A field-0 frame looks
// progressive, so the feature words stop two frames after the last field 1.
// W=0 (weave off): no feature words and no 480-line slot words.
module tb_apf_interlace #(parameter integer W=1);
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, interlace=0;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1)) dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(interlace),.blank(1'b0),.weave(W!=0),.field_swap(1'b0),.rgb_in(24'h123456),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, errs=0, nvs=0, want[0:12], slot_lines=0;
  reg field_bit;
  always @(posedge clk) begin
    if (vs) begin
      if (rgb!==want[nvs]) begin errs=errs+1; $display("vs %0d rgb=%0h want=%0h",nvs,rgb,want[nvs]); end
      nvs=nvs+1;
    end
    if (!de && !vs && rgb!==0 && rgb!=={8'b0,3'd5,13'b0}) begin errs=errs+1; $display("rgb=%0h",rgb); end
    if (!de && !vs && rgb==={8'b0,3'd5,13'b0}) slot_lines=slot_lines+1;
  end
  initial begin
    for (f=0;f<13;f=f+1) want[f]=0;
    // VS of frame f is sent before frame f's lines; fields are frames 4..9.
    if (W!=0) begin want[5]=24'h2; want[6]=24'h6; want[7]=24'h2; want[8]=24'h6; want[9]=24'h2; want[10]=24'h6; end
    for (f=0;f<13;f=f+1) begin
      field_bit = (f>=4 && f<10) ? (f%2==0) : 0;
      for (l=0;l<12;l=l+1) begin
        for (x=0;x<1400;x=x+1) begin
          @(negedge clk); ce=x%2; vsync=(l==0 && x<10); vblank=(l<2);
          hblank=(x>=1280); hsync=(x>=1300 && x<1320); interlace=field_bit;
        end
      end
    end
    @(negedge clk);
    if (nvs!=13) begin errs=errs+1; $display("nvs=%0d",nvs); end
    if ((slot_lines==0)!=(W==0)) begin errs=errs+1; $display("no 640x480 slot words"); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
