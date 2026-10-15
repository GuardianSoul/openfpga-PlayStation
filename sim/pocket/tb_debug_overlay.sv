module tb;
  reg clk=0; always #1 clk=~clk;
  integer x=0,y=0, errs=0, checks=0; reg ce=0;
  wire de = x>=100 && x<100+2*640 && y>=20 && y<260;
  wire vs = y==5;
  reg [255:0] words; integer r,b,px,ln;
  wire [23:0] out;
  psx_debug_overlay dut(.clk(clk),.ce(ce),.de(de),.vs(vs),.rgb_in(24'h123456),.words(words),.rgb_out(out));
  initial for (r=0;r<8;r=r+1) words[r*32+:32]=32'h8000_0001^(32'h01010101*r)^(r<<13);
  always @(posedge clk) begin
    ce<=!ce;
    if (x==1499) begin x<=0; y<=(y==269)?0:y+1; end else x<=x+1;
  end
  initial begin
    repeat(3*1500*270) @(posedge clk);
    while(!(x==0 && y==0)) @(posedge clk);
    while(y<255) begin @(negedge clk);
      if (de && ce) begin
        px=dut.px; ln=y-20;
        if (px<192 && ln<40 && px%6!=5 && ln%5!=4) begin
          r=ln/5; b=31-px/6; checks=checks+1;
          if (out !== (words[r*32+b] ? 24'hffffff : 24'h1830a0)) begin errs=errs+1; if (errs<5) $display("y=%0d px=%0d out=%h",y,px,out); end
        end
        if (ln>=40 && out!==24'h123456) begin errs=errs+1; if (errs<5) $display("pass-through y=%0d",y); end
      end
    end
    $display("checks=%0d errs=%0d",checks,errs); $finish;
  end
endmodule
