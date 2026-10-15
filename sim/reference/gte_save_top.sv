// SPDX-License-Identifier: GPL-2.0-only
module gte_save_top(
    input wire clk2x, clk2xIndex, ce, reset, reference_reset,
    input wire [5:0] read_addr, write_addr,
    input wire read_enable, write_enable,
    input wire [31:0] write_data, command,
    input wire command_enable, loading,
    input wire [5:0] ss_addr, input wire ss_write,
    input wire [31:0] ss_data,
    output wire [31:0] ss_read, read_data, reference_data,
    output wire busy, reference_busy
);
    psx_laxer_mister_adapter candidate(
        .clk2x(clk2x), .clk2xIndex(clk2xIndex), .ce(ce), .reset(reset),
        .gte_busy(busy), .gte_readAddr(read_addr), .gte_readData(read_data), .gte_readEna(read_enable),
        .gte_writeAddr_in(write_addr), .gte_writeData_in(write_data), .gte_writeEna_in(write_enable),
        .gte_cmdData(command), .gte_cmdEna(command_enable), .loading_savestate(loading),
        .SS_Adr(ss_addr), .SS_wren(ss_write), .SS_DataWrite(ss_data), .SS_raw_data(ss_read));
    psx_laxer_mister_adapter reference_engine(
        .clk2x(clk2x), .clk2xIndex(clk2xIndex), .ce(ce && !loading), .reset(reference_reset),
        .gte_busy(reference_busy), .gte_readAddr(read_addr), .gte_readData(reference_data), .gte_readEna(read_enable),
        .gte_writeAddr_in(write_addr), .gte_writeData_in(write_data), .gte_writeEna_in(write_enable),
        .gte_cmdData(command), .gte_cmdEna(command_enable), .loading_savestate(1'b0),
        .SS_Adr(6'b0), .SS_wren(1'b0), .SS_DataWrite(32'b0), .SS_raw_data());
endmodule
