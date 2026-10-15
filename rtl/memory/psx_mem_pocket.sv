`timescale 1ns/1ps
`default_nettype none

// Pocket memory component, not an openFPGA core top. Client signals use
// client_clk; clients reset with client_reset. Other clock domains need a CDC.
module psx_mem_pocket #(parameter integer PORTS = 4, T_INIT = 12600, T_REFI = 480) (
    input wire clk_74a, reset_async,
    output wire client_clk, client_reset, initialized, protocol_error,
    input wire [PORTS-1:0] p_valid, p_write,
    output wire [PORTS-1:0] p_ready,
    input wire [PORTS-1:0][24:0] p_addr,
    input wire [PORTS-1:0][3:0] p_halfwords,
    input wire [PORTS-1:0][127:0] p_wdata,
    input wire [PORTS-1:0][15:0] p_byte_en,
    output wire [PORTS-1:0] p_rsp_valid, p_rsp_error,
    input wire [PORTS-1:0] p_rsp_ready,
    output wire [PORTS-1:0][127:0] p_rsp_rdata,
    output wire [12:0] dram_a,
    output wire [1:0] dram_ba, dram_dqm,
    output wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke,
    inout wire [15:0] dram_dq,
    output wire dram_clk
);
    wire clk_mem, clk_dram, locked;
    psx_mem_clocks memory_clocks (
        .clk_74a(clk_74a), .reset_async(reset_async),
        .clk_sys(client_clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .locked(locked)
    );
    wire req_valid, req_ready, req_write, rsp_valid, rsp_ready, rsp_error;
    wire [24:0] req_addr;
    wire [3:0] req_halfwords;
    wire [127:0] req_wdata, rsp_rdata;
    wire [15:0] req_byte_en;
    psx_mem_arb #(.PORTS(PORTS)) arb (
        .clk(client_clk), .rst(client_reset),
        .p_valid(p_valid), .p_ready(p_ready), .p_write(p_write), .p_addr(p_addr),
        .p_halfwords(p_halfwords), .p_wdata(p_wdata), .p_byte_en(p_byte_en),
        .p_rsp_valid(p_rsp_valid), .p_rsp_ready(p_rsp_ready), .p_rsp_rdata(p_rsp_rdata), .p_rsp_error(p_rsp_error),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write), .req_addr(req_addr),
        .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .protocol_error(protocol_error)
    );
    psx_sdram_pins #(.T_INIT(T_INIT), .T_REFI(T_REFI)) pads (
        .clk_sys(client_clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .reset_async(reset_async || !locked),
        .client_reset(client_reset), .initialized(initialized),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
        .req_addr(req_addr), .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm),
        .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
        .dram_cke(dram_cke), .dram_dq(dram_dq), .dram_clk(dram_clk)
    );
endmodule
`default_nettype wire
