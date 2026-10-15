`timescale 1ns/1ps
`default_nettype none

// PSX transaction frontend plus the pinned MIRLO64 SDRAM command controller.
// DFI still needs the Pocket PHY, clocks and timing constraints. One clock
// domain, one outstanding frontend transaction, no speculative row opening.
module psx_sdram #(
    parameter integer T_INIT = 12600,
    parameter integer T_REFI = 480,
    parameter integer RL = 5
) (
    input wire clk, rst,
    output wire initialized,
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
    output wire [1:0] dfi_ras_n, dfi_cas_n, dfi_we_n,
    output wire [25:0] dfi_addr,
    output wire [3:0] dfi_ba,
    output wire dfi_cke,
    output wire dfi_wrdata_en,
    output wire [31:0] dfi_wrdata,
    output wire [3:0] dfi_wrdata_mask,
    output wire dfi_rddata_en,
    input wire [31:0] dfi_rddata,
    input wire dfi_rddata_valid
);
    wire core_ready, bridge_ready;
    wire mem_valid, mem_ready, mem_write, mem_rvalid;
    wire [23:0] mem_addr;
    wire [31:0] mem_wdata, mem_rdata;
    wire [3:0] mem_byte_en;
    assign initialized = core_ready && !rst;
    assign req_ready = bridge_ready && initialized;

    // sdr_ctrl takes a word per cycle and returns reads in order.
    psx_mem_bridge #(.PIPELINE_READS(1)) bridge (
        .clk(clk), .rst(rst), .req_valid(req_valid && initialized), .req_ready(bridge_ready),
        .req_write(req_write), .req_addr(req_addr), .req_halfwords(req_halfwords),
        .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .mem_valid(mem_valid), .mem_ready(mem_ready), .mem_write(mem_write), .mem_addr(mem_addr),
        .mem_wdata(mem_wdata), .mem_byte_en(mem_byte_en), .mem_rvalid(mem_rvalid), .mem_rdata(mem_rdata)
    );
    sdr_ctrl #(.T_INIT(T_INIT), .T_REFI(T_REFI), .RL(RL), .AHEAD(0)) controller (
        .clk(clk), .rst(rst), .ready(core_ready),
        .req_valid(mem_valid), .req_ready(mem_ready), .req_we(mem_write),
        .req_addr(mem_addr), .req_wdata(mem_wdata), .req_be(mem_byte_en), .req_tag(3'b0),
        .la_valid(1'b0), .la_addr(24'b0),
        .rd_valid(mem_rvalid), .rd_data(mem_rdata), .rd_tag(), .rd_next(), .rd_next_tag(),
        .dfi_ras_n(dfi_ras_n), .dfi_cas_n(dfi_cas_n), .dfi_we_n(dfi_we_n),
        .dfi_addr(dfi_addr), .dfi_ba(dfi_ba), .dfi_cke(dfi_cke),
        .dfi_wrdata_en(dfi_wrdata_en), .dfi_wrdata(dfi_wrdata), .dfi_wrdata_mask(dfi_wrdata_mask),
        .dfi_rddata_en(dfi_rddata_en), .dfi_rddata(dfi_rddata), .dfi_rddata_valid(dfi_rddata_valid)
    );
endmodule
`default_nettype wire
