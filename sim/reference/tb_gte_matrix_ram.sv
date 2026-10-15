// Lockstep check of the adapted PocketGTEEngine against the variant with the
// control-readback shadow and matrix-column LUT RAM. Random CTC2/MTC2 writes,
// random commands with random sf/mx/v/cv/lm fields, CFC2/MFC2 reads, CE pauses
// and rare resets; o_dataOut and o_executing are compared every cycle.
`include "GTEDefine.hv"
module tb_gte_matrix_ram;
logic clk=0, ce=1, nRst=0, writReg=0, run=0;
E_REG regID, readRegID;
logic [31:0] dataIn, outA, outB;
logic [24:0] instr;
logic execA, execB;
PocketGTEEngine original(.i_clk(clk),.i_ce(ce),.i_nRst(nRst),.i_regID(regID),.i_readRegID(readRegID),.i_WritReg(writReg),
  .i_DIP_USEFASTGTE(1'b0),.i_DIP_FIXWIDE(1'b0),.i_dataIn(dataIn),.o_dataOut(outA),.i_Instruction(instr),.i_run(run),.o_executing(execA));
PocketGTEEngineV candidate(.i_clk(clk),.i_ce(ce),.i_nRst(nRst),.i_regID(regID),.i_readRegID(readRegID),.i_WritReg(writReg),
  .i_DIP_USEFASTGTE(1'b0),.i_DIP_FIXWIDE(1'b0),.i_dataIn(dataIn),.o_dataOut(outB),.i_Instruction(instr),.i_run(run),.o_executing(execB));
always #5 clk=~clk;
byte unsigned ops[21] = '{8'h01,8'h06,8'h0C,8'h10,8'h11,8'h12,8'h13,8'h14,8'h16,8'h1B,8'h1C,8'h1E,8'h20,8'h28,8'h29,8'h2A,8'h2D,8'h2E,8'h30,8'h3D,8'h3E};
integer cycles, commands=0, mvmva=0, writes=0, nonzero_reads=0, seed_count;
initial begin
 regID=E_REG'(0); readRegID=E_REG'(0); dataIn=0; instr=0;
 repeat(3) @(negedge clk); nRst=1;
 for(cycles=0; cycles<3000000; cycles++) begin
  @(negedge clk);
  if(outA !== outB) $fatal(1,"GTE output mismatch reg=%02x cycle %0d: %08x vs %08x",readRegID,cycles,outA,outB);
  if(execA !== execB) $fatal(1,"GTE executing mismatch cycle %0d",cycles);
  if(outA != 0) nonzero_reads++;
  ce=($urandom%8)!=0; nRst=($urandom%200000)!=0;
  run=0; writReg=0;
  readRegID=E_REG'($urandom%64);
  if(!execA) begin
   case($urandom%8)
    0,1,2,3: begin writReg=1; regID=E_REG'($urandom%64); dataIn=$urandom;
                   if(($urandom%4)==0) dataIn=dataIn>>($urandom%20); if(ce && nRst) writes++; end
    4: begin run=1; instr={$urandom}[24:0]; instr[5:0]=ops[$urandom%21][5:0]; if(($urandom%3)==0) instr[5:0]=6'h12;
             if(ce && nRst) begin commands++; if(instr[5:0]==6'h12) mvmva++; end end
    default: ;
   endcase
  end
 end
 $display("PASS GTE matrix RAM cycles=%0d commands=%0d mvmva=%0d writes=%0d nonzero_reads=%0d",cycles,commands,mvmva,writes,nonzero_reads);
 $finish;
end
endmodule
