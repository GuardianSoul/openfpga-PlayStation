// SPDX-License-Identifier: GPL-2.0-only
`include "GTEDefine.hv"
module gte_adapter_top(
    input wire clk2x, input wire clk2xIndex, input wire ce, input wire reset,
    input wire [5:0] read_addr, input wire read_enable,
    input wire [5:0] write_addr, input wire write_enable, input wire [31:0] write_data,
    input wire [31:0] command, input wire command_enable,
    output wire busy, output wire [31:0] read_data,
    input wire reference_clock, input wire [5:0] reference_addr,
    output wire reference_busy, output wire [31:0] reference_data
);
    psx_laxer_mister_adapter candidate(
        .clk2x(clk2x), .clk2xIndex(clk2xIndex), .ce(ce), .reset(reset),
        .gte_busy(busy), .gte_readAddr(read_addr), .gte_readData(read_data), .gte_readEna(read_enable),
        .gte_writeAddr_in(write_addr), .gte_writeData_in(write_data), .gte_writeEna_in(write_enable),
        .gte_cmdData(command), .gte_cmdEna(command_enable)
    );
    GTEEngine reference_engine(
        .i_clk(reference_clock), .i_nRst(!reset), .i_regID(E_REG'(reference_addr)),
        .i_WritReg(write_enable), .i_DIP_USEFASTGTE(1'b0), .i_DIP_FIXWIDE(1'b0),
        .i_dataIn(write_data), .o_dataOut(reference_data),
        .i_Instruction(command[24:0]), .i_run(command_enable), .o_executing(reference_busy)
    );
endmodule
