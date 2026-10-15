`timescale 1ns/1ps
module tb_dpram_sdp;
 parameter AW=8,DW=32,CLOCKS=0,REVERSE=0;
 reg ca=0,cb_async=0;always #5 ca=~ca;
 initial begin #0.37;forever #7 cb_async=~cb_async;end
 wire cb=CLOCKS==1 ? ca:CLOCKS==2 ? !ca:cb_async;
 reg ea=1,eb=1,we=0;reg [AW-1:0] aa=0,ab=0;reg [DW-1:0] data=0;reg [31:0] rng=32'h3c6ef372;
 wire [DW-1:0] a,b,qa0,qb0,qa1,qb1;
 ram_old #(.AW(AW),.DW(DW)) old (.clock_a(REVERSE?cb:ca),.clock_b(REVERSE?ca:cb),
 .clken_a(REVERSE?eb:ea),.clken_b(REVERSE?ea:eb),.wren_a(REVERSE?1'b0:we),.wren_b(REVERSE?we:1'b0),
 .address_a(REVERSE?ab:aa),.address_b(REVERSE?aa:ab),.data_a(REVERSE?{DW{1'b0}}:data),.data_b(REVERSE?data:{DW{1'b0}}),.q_a(qa0),.q_b(qb0));
 ram_new #(.AW(AW),.DW(DW)) newram (.clock_a(REVERSE?cb:ca),.clock_b(REVERSE?ca:cb),
 .clken_a(REVERSE?eb:ea),.clken_b(REVERSE?ea:eb),.wren_a(REVERSE?1'b0:we),.wren_b(REVERSE?we:1'b0),
 .address_a(REVERSE?ab:aa),.address_b(REVERSE?aa:ab),.data_a(REVERSE?{DW{1'b0}}:data),.data_b(REVERSE?data:{DW{1'b0}}),.q_a(qa1),.q_b(qb1));
 assign a=REVERSE?qa0:qb0,b=REVERSE?qa1:qb1;
 integer n=0,checks=0,known=0,unknown=0,collisions=0,oracle_checks=0,nonzero=0,topbit=0;
 reg [DW-1:0] score [0:(1<<AW)-1];
 initial for(integer i=0;i<(1<<AW);i++)score[i]=0;
 always @(posedge ca) if(ea&&we)score[aa]<=data;
 always @(posedge cb) begin
  reg [DW-1:0] expected;reg collision;
  expected=score[ab];collision=CLOCKS==1&&ea&&we&&aa==ab;
  #0.02;if(n>300 && eb && !collision)begin
   if(a!==expected || b!==expected)$fatal(1,"RAM independent oracle mismatch n=%0d old=%h new=%h expected=%h",n,a,b,expected);
   oracle_checks++;if(expected!=0)nonzero++;if(expected[DW-1])topbit++;
  end
 end
 always @(negedge ca)begin
  rng=(rng>>1)^((rng&1)?32'hd0000001:0);ea=(rng&7)!=0;we=(rng&3)!=0;
  aa=n<(1<<AW) ? AW'(n):AW'(rng);
  for(integer bit_nr=0;bit_nr<DW;bit_nr++) data[bit_nr]=rng[(bit_nr+n)%32] ^ 1'((bit_nr+n)>>3);
  n++;
 end
 always @(negedge cb)begin #0.01;eb=(n%17)!=0;ab=(n%3)==0?aa:AW'(rng>>8);end
 always @(posedge ca or posedge cb or negedge ca or negedge cb)begin
  #0.02;if(n>300)begin
   if(a!==b)$fatal(1,"RAM mode difference n=%0d old=%h new=%h",n,a,b);
   checks++;if((^a)===1'bx)unknown++;else known++;
   if(we&&aa==ab)collisions++;
  end
 end
 initial begin repeat(30000)@(negedge ca);#1;
  if(known<1000||checks<10000||collisions<100||oracle_checks<1000||nonzero<1000||topbit<500)$fatal(1,"Missing coverage known=%0d checks=%0d collisions=%0d oracle=%0d nonzero=%0d topbit=%0d",known,checks,collisions,oracle_checks,nonzero,topbit);
  $display("PASS ACTUAL_HELPER aw=%0d dw=%0d reverse=%0d clocks=%0d checks=%0d oracle=%0d topbit=%0d unknown=%0d",AW,DW,REVERSE,CLOCKS,checks,oracle_checks,topbit,unknown);$finish;
 end
endmodule
