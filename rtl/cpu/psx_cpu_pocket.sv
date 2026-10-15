`timescale 1ns/1ps
`default_nettype none

// CPU/memory integration component. APF, peripherals and GTE are external.
// Host requests use host_clk/host_reset. CPU, I/O and COP2 use clk_core/core_reset.
module psx_cpu_pocket #(parameter integer T_INIT=12600, T_REFI=480) (
    input wire clk_74a, clk_core, reset_async, run,
    input wire [5:0] irq,
    output wire core_reset, host_clk, host_reset, initialized, protocol_error, unsupported_cache_mode,
    input wire host_valid, host_write,
    output wire host_ready,
    input wire [24:0] host_addr,
    input wire [3:0] host_halfwords,
    input wire [127:0] host_wdata,
    input wire [15:0] host_byte_en,
    output wire host_rsp_valid, host_rsp_error,
    input wire host_rsp_ready,
    output wire [127:0] host_rsp_rdata,
    output wire io_valid, io_write,
    input wire io_ready,
    output wire [31:0] io_addr, io_wdata, io_store_raw,
    output wire [3:0] io_byte_en,
    output wire [1:0] io_size,
    input wire io_rsp_valid, io_rsp_error,
    output wire io_rsp_ready,
    input wire [31:0] io_rsp_rdata,
    output wire cop2_valid,
    input wire cop2_ready,
    output wire [2:0] cop2_kind,
    output wire [4:0] cop2_index,
    output wire [24:0] cop2_command,
    output wire [31:0] cop2_wdata,
    input wire cop2_rsp_valid, cop2_rsp_error,
    output wire cop2_rsp_ready,
    input wire [31:0] cop2_rsp_rdata,
    output wire trace_valid, trace_exception,
    output wire [31:0] trace_pc, trace_instruction,
    output wire [4:0] trace_exception_code,
    input wire [4:0] debug_reg,
    output wire [31:0] debug_value, debug_pc, debug_hi, debug_lo, debug_status, debug_cause, debug_epc, debug_badaddr,
    output wire [12:0] dram_a,
    output wire [1:0] dram_ba, dram_dqm,
    output wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke,
    inout wire [15:0] dram_dq,
    output wire dram_clk
);
    wire bus_valid,bus_ready,bus_write,bus_fetch,bus_rsp_valid,bus_rsp_ready,bus_rsp_error,cache_isolated;
    wire [31:0] bus_addr,bus_wdata,bus_store_raw,bus_rsp_rdata;
    wire [3:0] bus_byte_en;
    wire [1:0] bus_size;
    wire mem_valid,mem_ready,mem_write,mem_rsp_valid,mem_rsp_ready,mem_rsp_error;
    wire [24:0] mem_addr;
    wire [3:0] mem_halfwords;
    wire [127:0] mem_wdata,mem_rsp_rdata;
    wire [15:0] mem_byte_en;
    wire [1:0] p_valid,p_write,p_ready,p_rsp_valid,p_rsp_ready,p_rsp_error;
    wire [1:0][24:0] p_addr;
    wire [1:0][3:0] p_halfwords;
    wire [1:0][127:0] p_wdata,p_rsp_rdata;
    wire [1:0][15:0] p_byte_en;
    wire mem_initialized,arb_error;
    (* async_reg="true" *) reg [1:0] init_sync,error_sync;
    assign initialized=!core_reset && init_sync[1];
    assign protocol_error=error_sync[1];
    always @(posedge clk_core or posedge reset_async)
        if (reset_async) begin init_sync<=0; error_sync<=0; end
        else if (core_reset) begin init_sync<=0; error_sync<=0; end
        else begin init_sync<={init_sync[0],mem_initialized}; error_sync<={error_sync[0],arb_error}; end
    psx_cpu cpu (.clk(clk_core),.rst(core_reset),.*);
    psx_cpu_bus guest (.clk(clk_core),.rst(core_reset),.*);
    psx_mem_cdc crossing (
        .src_clk(clk_core),.dst_clk(host_clk),.reset_async(reset_async || host_reset),.src_reset(core_reset),.dst_reset(),
        .s_valid(mem_valid),.s_ready(mem_ready),.s_write(mem_write),.s_addr(mem_addr),.s_halfwords(mem_halfwords),
        .s_wdata(mem_wdata),.s_byte_en(mem_byte_en),.s_rsp_valid(mem_rsp_valid),.s_rsp_ready(mem_rsp_ready),
        .s_rsp_rdata(mem_rsp_rdata),.s_rsp_error(mem_rsp_error),
        .d_valid(p_valid[0]),.d_ready(p_ready[0]),.d_write(p_write[0]),.d_addr(p_addr[0]),.d_halfwords(p_halfwords[0]),
        .d_wdata(p_wdata[0]),.d_byte_en(p_byte_en[0]),.d_rsp_valid(p_rsp_valid[0]),.d_rsp_ready(p_rsp_ready[0]),
        .d_rsp_rdata(p_rsp_rdata[0]),.d_rsp_error(p_rsp_error[0])
    );
    assign p_valid[1]=host_valid; assign p_write[1]=host_write; assign p_addr[1]=host_addr;
    assign p_halfwords[1]=host_halfwords; assign p_wdata[1]=host_wdata; assign p_byte_en[1]=host_byte_en;
    assign host_ready=p_ready[1]; assign host_rsp_valid=p_rsp_valid[1]; assign host_rsp_error=p_rsp_error[1];
    assign host_rsp_rdata=p_rsp_rdata[1]; assign p_rsp_ready[1]=host_rsp_ready;
    psx_mem_pocket #(.PORTS(2),.T_INIT(T_INIT),.T_REFI(T_REFI)) memory (
        .clk_74a(clk_74a),.reset_async(reset_async),.client_clk(host_clk),.client_reset(host_reset),
        .initialized(mem_initialized),.protocol_error(arb_error),.p_valid(p_valid),.p_write(p_write),.p_ready(p_ready),
        .p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),.p_byte_en(p_byte_en),
        .p_rsp_valid(p_rsp_valid),.p_rsp_ready(p_rsp_ready),.p_rsp_rdata(p_rsp_rdata),.p_rsp_error(p_rsp_error),.*
    );
endmodule
`default_nettype wire
