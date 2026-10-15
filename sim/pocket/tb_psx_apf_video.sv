// Fields alternate interlace=0/1 for 4 fields, then 4 progressive fields,
// then 4 fields with the flag stuck high (at most every other one is held).
// Every field keeps VS and DE; held fields record no pixels (all skipped).
module tb_psx_apf_video;
  reg clk=0; always #1 clk=~clk;
  reg ce=0, hsync=0, vsync=0, hblank=1, vblank=1, interlace=0; reg [23:0] rgb_in=24'h123456;
  wire hs, vs, de, skip; wire [23:0] rgb;
  psx_apf_video dut(.clk(clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),.interlace(interlace),.blank(1'b0),.weave(1'b1),.field_swap(1'b0),.rgb_in(rgb_in),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
  integer f, l, x, vs_count[0:11], de_count[0:11], kept[0:11], errs=0, field=0, hs_count=0;
  always @(posedge clk) begin
    if (vs) vs_count[field]=vs_count[field]+1;
    if (de) de_count[field]=de_count[field]+1;
    if (de && !skip) kept[field]=kept[field]+1;
    if (hs) hs_count=hs_count+1;
    if (!de && rgb!==0) errs=errs+1;
  end
  function automatic held(input integer f);
    held=(f<4 && f%2==1) || (f>=8 && f%2==0);
  endfunction
  initial begin
    for (f=0;f<12;f=f+1) begin vs_count[f]=0; de_count[f]=0; kept[f]=0; end
    for (f=0;f<12;f=f+1) begin
      field=f; interlace=((f<4) && f%2==1) || f>=8;
      for (l=0;l<20;l=l+1) begin
        vsync=(l<2); vblank=(l<4);
        for (x=0;x<40;x=x+1) begin
          @(negedge clk); ce=x%2; hsync=(x>=34 && x<38); hblank=(x>=32);
        end
      end
    end
    @(negedge clk); @(negedge clk);
    for (f=0;f<12;f=f+1) begin
      if (vs_count[f] != 1) begin errs=errs+1; $display("field %0d vs=%0d",f,vs_count[f]); end
      if (de_count[f] != 16*32) begin errs=errs+1; $display("field %0d de=%0d",f,de_count[f]); end
      if (kept[f] != (held(f) ? 0 : 16*16)) begin errs=errs+1; $display("field %0d kept=%0d",f,kept[f]); end
    end
    if (hs_count != 12*20) begin errs=errs+1; $display("hs=%0d",hs_count); end
    $display("errs=%0d", errs); $finish;
  end
endmodule
