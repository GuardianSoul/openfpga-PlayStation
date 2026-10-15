// SPDX-License-Identifier: GPL-2.0-only
// The CPU here is mechanically transcoded from the pinned original VHDL.
module cpu_gte_adapter_top(
    input wire clk1x, clk2x, clk3x, ce, reset, ss_reset,
    output wire request, rnw, isdata, iscache,
    output wire [31:0] addr_instr, addr_data, write_data,
    output wire [3:0] mask,
    input wire [31:0] read_data, input wire done, ram_done,
    output wire gte_busy, output wire gte_command_enable,
    output wire [31:0] gte_command
);
    reg clock_toggle=0, sampled_toggle=0, index2x=0;
    always @(posedge clk1x) clock_toggle <= !clock_toggle;
    always @(posedge clk2x) begin
        sampled_toggle <= clock_toggle;
        index2x <= sampled_toggle==clock_toggle;
    end
    wire read_enable,write_enable;
    wire [5:0] read_addr,write_addr;
    wire [31:0] gte_read_data,gte_write_data;
    cpu cpu_core(
        .clk1x(clk1x), .clk2x(clk2x), .clk3x(clk3x), .ce(ce), .reset(reset),
        .TURBO(1'b0), .TURBO_CACHE(1'b0), .TURBO_CACHE50(1'b0),
        .irqRequest(1'b0), .dmaStallCPU(1'b0), .cpuPaused(1'b0),
        .error(), .error2(), .mem_request(request), .mem_rnw(rnw), .mem_isData(isdata), .mem_isCache(iscache),
        .mem_oldtagvalids(), .mem_addressInstr(addr_instr), .mem_addressData(addr_data), .mem_reqsize(),
        .mem_writeMask(mask), .mem_dataWrite(write_data), .mem_dataRead(read_data), .mem_done(done),
        .mem_fifofull(1'b0), .mem_tagvalids(4'b0), .cache_wr(4'b0), .cache_data(32'b0), .cache_addr(8'b0),
        .stallNext(), .dma_cache_Adr(21'b0), .dma_cache_data(32'b0), .dma_cache_write(1'b0),
        .ram_done(ram_done), .ram_rnw(1'b1), .ram_dataRead(read_data),
        .gte_busy(gte_busy), .gte_readEna(read_enable), .gte_readAddr(read_addr), .gte_readData(gte_read_data),
        .gte_writeAddr(write_addr), .gte_writeData(gte_write_data), .gte_writeEna(write_enable),
        .gte_cmdData(gte_command), .gte_cmdEna(gte_command_enable),
        .SS_reset(ss_reset), .SS_DataWrite(32'b0), .SS_Adr(8'b0), .SS_wren_CPU(1'b0), .SS_wren_SCP(1'b0),
        .SS_rden_CPU(1'b0), .SS_rden_SCP(1'b0), .SS_DataRead_CPU(), .SS_DataRead_SCP(), .SS_idle(),
        .debug_firstGTE(1'b0)
    );
    psx_laxer_mister_adapter geometry(
        .clk2x(clk2x), .clk2xIndex(index2x), .ce(ce), .reset(reset),
        .gte_busy(gte_busy), .gte_readAddr(read_addr), .gte_readData(gte_read_data), .gte_readEna(read_enable),
        .gte_writeAddr_in(write_addr), .gte_writeData_in(gte_write_data), .gte_writeEna_in(write_enable),
        .gte_cmdData(gte_command), .gte_cmdEna(gte_command_enable)
    );
endmodule
