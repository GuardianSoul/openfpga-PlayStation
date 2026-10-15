// Lockstep check of PocketGTERegs (original) against the control-readback
// LUT-RAM variant: identical random stimulus on every input, including the
// GTE write-back structs; o_dataOut and the exported register struct are
// compared every cycle.
`include "GTEDefine.hv"
module tb_gte_control_ram;
logic clk=0, ce=1, nRst=0, loadInstr=0, writReg=0;
gteWriteBack wb; gteCtrl wr; SgteREG regA, regB;
E_REG regID, readRegID;
logic [31:0] dataIn, outA, outB;
PocketGTERegs original(.i_clk(clk),.i_ce(ce),.i_nRst(nRst),.i_loadInstr(loadInstr),.i_wb(wb),.gteWR(wr),.gteREG(regA),
  .i_regID(regID),.i_readRegID(readRegID),.i_WritReg(writReg),.i_dataIn(dataIn),.o_dataOut(outA));
PocketGTERegsControlRam candidate(.i_clk(clk),.i_ce(ce),.i_nRst(nRst),.i_loadInstr(loadInstr),.i_wb(wb),.gteWR(wr),.gteREG(regB),
  .i_regID(regID),.i_readRegID(readRegID),.i_WritReg(writReg),.i_dataIn(dataIn),.o_dataOut(outB));
always #5 clk=~clk;
integer cycles=0, ctrl_reads=0, nonzero_ctrl_reads=0, ctrl_writes=0;
initial begin
 wb='0; wr='0; regID=E_REG'(0); readRegID=E_REG'(0); dataIn=0;
 repeat(3) @(negedge clk); nRst=1;
 for(cycles=0; cycles<2000000; cycles++) begin
  @(negedge clk);
  if(outA !== outB) $fatal(1,"GTE readback mismatch reg=%02x at cycle %0d: %08x vs %08x",readRegID,cycles,outA,outB);
  if(regA !== regB) $fatal(1,"GTE register struct mismatch at cycle %0d",cycles);
  if(readRegID[5]) begin ctrl_reads++; if(outA!=0 && readRegID!=6'h3F) nonzero_ctrl_reads++; end
  ce=($urandom%16)!=0; nRst=($urandom%50000)!=0; loadInstr=$urandom%2;
  wb=gteWriteBack'({$urandom,$urandom,$urandom,$urandom}); wr=gteCtrl'({$urandom,$urandom,$urandom,$urandom,$urandom,$urandom});
  if(($urandom%4)!=0) wb='0;
  writReg=($urandom%3)==0; regID=E_REG'($urandom%64); readRegID=E_REG'($urandom%64);
  dataIn=$urandom; if(writReg && regID[5] && ce && nRst) ctrl_writes++;
 end
 $display("PASS GTE control RAM cycles=%0d control_writes=%0d control_reads=%0d nonzero_control_reads=%0d",cycles,ctrl_writes,ctrl_reads,nonzero_ctrl_reads);
 $finish;
end
endmodule
