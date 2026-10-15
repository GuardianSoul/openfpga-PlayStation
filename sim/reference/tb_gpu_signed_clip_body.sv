// SPDX-License-Identifier: GPL-2.0-only
// Integer right triangles have an independent top/left-inclusive pixel oracle.
task triangle(input integer x,y,input [23:0] color);
 write_gpu(0,32'h20000000 | color);
 write_gpu(0,((y & 65535)<<16) | (x & 65535));
 write_gpu(0,((y & 65535)<<16) | ((x+80) & 65535));
 write_gpu(0,(((y+80) & 65535)<<16) | (x & 65535));
 idle();
endtask
initial begin
 repeat(100) @(negedge clk1x);reset=0;ss_reset=0;
 repeat(100) @(negedge clk1x);
 write_gpu(0,32'he1000000);write_gpu(0,32'he3000000);
 write_gpu(0,32'he4000000 | (239<<10) | 319);
 write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
 triangle(160,40,24'h0000ff);
 for(integer y=39;y<=120;y++) for(integer x=159;x<=240;x++)
  pixel(x,y,(x>=160 && y>=40 && x-160+y-40<80) ? 16'h001f:0);
 $display("SIGNED_CLIP positive-control checks=%0d",checks);
 // Starts at -40; GHDL's former unsigned truncation skipped these spans.
 triangle(-40,40,24'h0000ff);
 for(integer y=39;y<=120;y++) begin
  for(integer x=0;x<=40;x++)
   pixel(x,y,(y>=40 && x+40+y-40<80) ? 16'h001f:0);
  pixel(1023,y,0); // Negative coordinates must not wrap into VRAM.
 end
 // Same clipping through the signed GP0 drawing offset, not negative vertices.
 write_gpu(0,32'he5000000 | (2048-80));
 triangle(40,140,24'h00ff00);
 for(integer y=139;y<=220;y++) begin
  for(integer x=0;x<=40;x++)
   pixel(x,y,(y>=140 && x+40+y-140<80) ? 16'h03e0:0);
  pixel(1023,y,0);
 end
 write_gpu(0,32'he5000000);
 // A genuinely right-of-area span must still be rejected after the fix.
 triangle(360,40,24'hffffff);
 for(integer y=39;y<=120;y++) for(integer x=319;x<=441;x++) pixel(x,y,0);
 $display("PASS GPU signed clip checks=%0d",checks);$finish;
end
initial begin repeat(1000000) @(negedge clk1x);$fatal(1,"Overall GPU timeout");end
endmodule
