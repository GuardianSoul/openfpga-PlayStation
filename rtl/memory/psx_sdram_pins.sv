`timescale 1ns/1ps
`default_nettype none

// Memory frontend through the Pocket SDRAM pads. Clocks must come from one PLL:
// clk_sys=62.5 MHz, clk_mem=125 MHz, clk_dram=125 MHz shifted by 4 ns.
// reset_async includes reset requests and loss of PLL lock.
module psx_sdram_pins #(
    parameter integer T_INIT = 12600,
    parameter integer T_REFI = 480
) (
    input wire clk_sys, clk_mem, clk_dram,
    input wire reset_async,
    output wire initialized,
    output wire client_reset,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [24:0] req_addr,
    input wire [3:0] req_halfwords,
    input wire [127:0] req_wdata,
    input wire [15:0] req_byte_en,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [127:0] rsp_rdata,
    output wire rsp_error,
    output wire [12:0] dram_a,
    output wire [1:0] dram_ba, dram_dqm,
    output wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke,
    inout wire [15:0] dram_dq,
    output wire dram_clk
);
    (* async_reg = "true" *) reg [2:0] sys_reset, mem_reset;
    always @(posedge clk_sys or posedge reset_async)
        if (reset_async) sys_reset <= 3'b111;
        else sys_reset <= {sys_reset[1:0], 1'b0};
    always @(posedge clk_mem or posedge reset_async)
        if (reset_async) mem_reset <= 3'b111;
        else mem_reset <= {mem_reset[1:0], 1'b0};
    wire rst_sys = sys_reset[2], rst_mem = mem_reset[2];
    assign client_reset = rst_sys;
    wire [1:0] ras_n, cas_n, we_n;
    wire [25:0] addr;
    wire [3:0] ba, mask;
    wire cke, wren, rden, rvalid;
    wire [31:0] wdata, rdata;
    psx_sdram #(.T_INIT(T_INIT), .T_REFI(T_REFI)) memory (
        .clk(clk_sys), .rst(rst_sys), .initialized(initialized),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
        .req_addr(req_addr), .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .dfi_ras_n(ras_n), .dfi_cas_n(cas_n), .dfi_we_n(we_n),
        .dfi_addr(addr), .dfi_ba(ba), .dfi_cke(cke),
        .dfi_wrdata_en(wren), .dfi_wrdata(wdata), .dfi_wrdata_mask(mask),
        .dfi_rddata_en(rden), .dfi_rddata(rdata), .dfi_rddata_valid(rvalid)
    );
    psx_sdr_phy phy (
        .clk(clk_sys), .rst(rst_sys), .clk2x(clk_mem), .rst2x(rst_mem), .clk2x_90(clk_dram),
        .ras_n(ras_n), .cas_n(cas_n), .we_n(we_n), .addr(addr), .ba(ba), .cke(cke),
        .wrdata_en(wren), .wrdata(wdata), .wrdata_mask(mask), .rddata_en(rden),
        .rddata(rdata), .rddata_valid(rvalid),
        .sdram_a(dram_a), .sdram_ba(dram_ba), .sdram_dm(dram_dqm), .sdram_dq(dram_dq),
        .sdram_ras_n(dram_ras_n), .sdram_cas_n(dram_cas_n), .sdram_we_n(dram_we_n),
        .sdram_cke(dram_cke), .sdram_clock(dram_clk)
    );
endmodule
`default_nettype wire
