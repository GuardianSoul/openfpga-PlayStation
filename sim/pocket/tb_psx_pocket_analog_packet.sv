// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb;
 parameter integer SRC_HALF=6734, DST_HALF=14763, CONFIG_W=13;
 localparam [CONFIG_W-1:0] CONFIG_RESET=13'h134;
 reg src=0,dst=0,run_dst=1,reset_n=0,setting=1;
 reg [3:0] kind=3;
 reg [CONFIG_W-1:0] cfg=CONFIG_RESET;
 reg [31:0] joy=0;
 always #(SRC_HALF/1000.0) src=~src;
 always #(DST_HALF/1000.0) if(run_dst) dst=~dst;
 wire ds,old_ds,plain_ds;
 wire [31:0] sticks,old_sticks,plain_sticks;
 wire [CONFIG_W-1:0] cfg_out,plain_cfg;
 psx_pocket_analog #(.PASS_CONFIG(1),.CONFIG_W(CONFIG_W),.CONFIG_RESET(CONFIG_RESET)) dut(.clk1x(dst),.reset_n(reset_n),.clk_74a(src),.dualshock_setting(setting),.apf_type(kind),.apf_joy(joy),.config_in(cfg),.config_out(cfg_out),.dualshock(ds),.sticks(sticks));
 psx_pocket_analog #(.CONFIG_W(CONFIG_W),.CONFIG_RESET(CONFIG_RESET)) plain(.clk1x(dst),.reset_n(reset_n),.clk_74a(src),.dualshock_setting(setting),.apf_type(kind),.apf_joy(joy),.config_in(~cfg),.config_out(plain_cfg),.dualshock(plain_ds),.sticks(plain_sticks));
 analog_original original(.clk1x(dst),.reset_n(reset_n),.clk_74a(src),.dualshock_setting(setting),.apf_type(kind),.apf_joy(joy),.dualshock(old_ds),.sticks(old_sticks));
 reg [CONFIG_W+32:0] published[0:16383];
 integer count=0,seq=0,checks=0,deliveries=0,resets=0,mode_seen=0;
 reg [CONFIG_W+32:0] previous={CONFIG_RESET,33'h080808080};
 reg [CONFIG_W+32:0] actual;
 integer i;reg found;
 // Observe independently calculated source words only when publication is
 // possible. The unchanged old sampler independently checks every lower bit.
 always @(posedge src) if(!dut.busy) begin
  if(count==16384)$fatal(1,"publication overflow");
  published[count]={cfg,setting,((kind==3)||(kind==2&&joy!=0&&joy!=32'hffffffff))?joy:32'h80808080};
  count=count+1;
 end
 always @(negedge src) begin
  seq=seq+1;
  joy={16'h4e71,16'(seq)};
  cfg={2'(seq%3),2'((seq/3)%3),2'((seq/5)%4),3'((seq/7)%6),2'((seq/11)%4),2'((seq/13)%3)};
  // The current face-button selector occupies bits 14:13. Use all four
  // transport values, including the reserved one, to expose truncation.
  if(CONFIG_W==15) cfg=cfg | (CONFIG_W'((seq/17)%4)<<13);
  // 16 bits: the face selector moves to 15:14 above a third SELECT + D-Pad bit.
  if(CONFIG_W==16) cfg=cfg | (CONFIG_W'((seq/19)%2)<<13) | (CONFIG_W'((seq/17)%4)<<14);
  setting=(seq/13)%2;
  case(seq%37)
   0:begin kind=1;joy=0;end
   1:begin kind=2;joy=32'hffffffff;end
   2:begin kind=2;joy=0;end
   3:kind=2;
   default:kind=3;
  endcase
 end
 always @(posedge dst) begin
  #0.001;
  checks=checks+1;
  if({ds,sticks}!=={old_ds,old_sticks}||{plain_ds,plain_sticks}!=={old_ds,old_sticks})$fatal(1,"lower33 sampler mismatch");
  if(plain_cfg!==CONFIG_RESET)$fatal(1,"default config changed");
  actual={cfg_out,ds,sticks};
  found=actual==={CONFIG_RESET,33'h080808080};
  for(i=0;i<count;i=i+1)if(actual===published[i])found=1;
  if(!found)$fatal(1,"torn config/analog packet %h",actual);
  if(CONFIG_W>=15 && reset_n) mode_seen=mode_seen | (1 << (cfg_out >> (CONFIG_W-2)));
  if(actual!==previous)deliveries=deliveries+1;
  previous=actual;
 end
 initial begin
  repeat(4)@(negedge dst);reset_n=1;
  repeat(137)@(negedge src);reset_n=0;resets=resets+1;
  repeat(7)@(negedge src);reset_n=1;
  repeat(271)@(negedge src);reset_n=0;resets=resets+1;
  repeat(3)@(negedge src);reset_n=1;
  @(negedge dst);run_dst=0;
  repeat(37)@(negedge src);run_dst=1;
  repeat(2500)@(negedge dst);
  if(deliveries<200||checks<2500||resets!=2)$fatal(1,"coverage missing");
  if(CONFIG_W>=15 && mode_seen!=15)$fatal(1,"face selector coverage missing");
  $display("PASS atomic pad width=%0d face_modes=%0d checks=%0d deliveries=%0d published=%0d resets=%0d",CONFIG_W,mode_seen,checks,deliveries,count,resets);$finish;
 end
 initial begin #1000000;$fatal(1,"timeout");end
endmodule
