`timescale 1ns/1ps
// Simulation stand-in for the Quartus APF ID/size table RAM used by the
// pinned core_bridge_cmd. Two-clock registered read, conservative.
module mf_datatable(input [9:0] address_a,address_b,input clock_a,clock_b,
 input [31:0] data_a,data_b,input wren_a,wren_b,output reg [31:0] q_a=0,q_b=0);
 reg [31:0] m[0:1023];reg [9:0] ra=0,rb=0;
 initial for(integer i=0;i<1024;i++) m[i]=0;
 // Two-clock registered read, conservative for the Quartus table RAM.
 always @(posedge clock_a) begin if(wren_a) m[address_a]<=data_a;ra<=address_a;q_a<=m[ra];end
 always @(posedge clock_b) begin if(wren_b) m[address_b]<=data_b;rb<=address_b;q_b<=m[rb];end
endmodule
